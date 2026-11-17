"""#2188 PR 2: `task retry-start` wiring (docs/design/start-failure-retry-2188.md).

Covers design section 7 item 2: the first delegate, a REPLAN Task, a dirty
D6, a native session, the limit, a concurrent write, a repair-1 retry, the
root-claim refusals, a pre-PR 2 repair Task, and the successful repair-2
retry with its packet-mismatch refusal. The first CAS persisting
``repair_packet`` is covered too.

What runs for real: run_task_retry_start, run_task_claude_delegate, the
start_failure predicate, the dispatch gate
(worker_dispatch.authorize_task_store_change, including START_RETRY) and the
#2188 TaskStore field rules, which GateStore below calls on every write.
What is injected: the route runner, the live D6 probe (section 2 check 6),
the D6 registry and Git reads, the Owner policy file read, the PM caller
check, the quota read and the chain lock (passed through). Every refusal is
the production code's own; no check is disabled.

Test data (Owner #763 A): plain English or CJK prose and SHA digests of fixed
prose only. No credential-shaped strings.
"""

import copy
import hashlib
import io
import json
import unittest
from argparse import Namespace
from contextlib import ExitStack, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import lineage_counts
from llmtool import start_failure
from llmtool import task_store
from llmtool import task_submission_cli as cli
from llmtool import worker_dispatch
from llmtool.developer_routes import RouteError
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStoreConflict


def _sha1(text):
    return hashlib.sha1(text.encode('utf-8')).hexdigest()


def _sha256(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


D6 = 'retry-start-d6'
HEAD = _sha1('retry start fixture head')
EARLIER_HEAD = _sha1('retry start fixture earlier head')
CONTRACT = _sha256('retry start fixture contract')
PM_SESSION = 'pm-retry-start-fixture'
ROOT = '/nonexistent/retry-start-d6'
COMMON = '/nonexistent/retry-start-common'
BRANCH = 'feat/retry-start'
WRITE = 'docs/retry_note.md'
READ = ['docs/retry_context.md', WRITE]
ACCEPTANCE = ['The note states the retry outcome.']
EGRESS = 'Synthetic fixture prose only.'
DIFF = ('diff --git a/docs/retry_note.md b/docs/retry_note.md\n'
        '--- a/docs/retry_note.md\n+++ b/docs/retry_note.md\n'
        '@@ -1 +1 @@\n-Old note.\n+New note.\n')
CARD = '\nREQUIRED_SKILL_BEGIN path=card\nFixture rules.\nREQUIRED_SKILL_END path=card'
PR = 2188
FAMILY = 'pr-2188'
REVIEW_CJK = ('审查意见：重试说明需要补充启动失败的原因与处理步骤。' * 200)[:2000]
REPORT_CJK = ('根因分析：启动失败发生在进程创建之前，配置文件缺少必需字段。' * 300)[:6400]
OTHER_REPORT_CJK = ('另一份报告：这是一份不同的诊断说明。' * 400)[:6400]

REGISTRATION = SimpleNamespace(
    task_id=D6, trusted_agent_id='claude-developer', session_ref='Retry fixture session',
    native_session_id=None, root=ROOT, common_dir=COMMON, branch=BRANCH, source_sha=HEAD)
SNAPSHOT = SimpleNamespace(head=HEAD, root=ROOT, common_dir=COMMON, branch=BRANCH)
ALLOWED = worker_dispatch.PolicyDecision(
    'claude-developer', True, 'OWNER_POLICY_ALLOWED', _sha256('policy fixture'))
PAUSED = worker_dispatch.PolicyDecision(
    'claude-developer', False, 'OWNER_PAUSED', _sha256('policy fixture'))


class GateStore:
    """In-memory Task store that applies the real gate and #2188 rules to each write.

    Order as in TaskStore._update_record: the expected fields, the
    repair_packet write-once rule, the dispatch gate (a denial keeps its
    audit entry), the start_failures append rule, then the field validators.
    """

    def __init__(self):
        self.records = {}
        self.before_bind = None
        self.binds = 0

    def _seal(self, record):
        body = {key: value for key, value in record.items() if key != 'record_sha256'}
        record['record_sha256'] = hashlib.sha256(json.dumps(
            body, sort_keys=True, ensure_ascii=True).encode('ascii')).hexdigest()
        self.records[record['task_id']] = record
        return copy.deepcopy(record)

    def add(self, record):
        return self._seal(copy.deepcopy(record))

    def force(self, task_id, **changes):
        """Test setup only: change a record without the gate."""
        record = copy.deepcopy(self.records[task_id])
        record.update(copy.deepcopy(changes))
        return self._seal(record)

    def task_ids(self):
        return sorted(self.records)

    def get(self, task_id):
        if task_id not in self.records:
            raise LLMToolError('task not found')
        return copy.deepcopy(self.records[task_id])

    def update(self, task_id, **changes):
        return self._commit(task_id, None, changes, None)

    def compare_and_update(self, task_id, *, expected, **changes):
        return self._commit(task_id, expected, changes, None)

    def d6_bind_compare_and_update(self, task_id, *, executor_task_id, expected,
                                   d6_unchanged=None, **changes):
        self.binds += 1
        if self.before_bind is not None:
            self.before_bind(self, task_id)
        return self._commit(task_id, expected, changes, d6_unchanged)

    def _commit(self, task_id, expected, changes, d6_unchanged):
        current = self.get(task_id)
        if expected is not None and any(current.get(key) != value
                                        for key, value in expected.items()):
            raise TaskStoreConflict()
        changes = copy.deepcopy(changes)
        task_store._check_repair_packet_write(current, changes)
        denial = worker_dispatch.authorize_task_store_change(
            current, changes, expected, d6_unchanged=d6_unchanged)
        if denial is not None:
            self._seal(current)
            raise denial
        task_store._check_start_failures_write(current, changes)
        current.update(changes)
        if 'start_failures' in current:
            task_store._validate_start_failures(current)
        if 'repair_packet' in current:
            task_store._validate_repair_packet_text(current['repair_packet'])
        return self._seal(current)


class Route:
    """Injected route runner: records each request; raises a route code or succeeds."""

    def __init__(self, error=None):
        self.error = error
        self.requests = []

    def __call__(self, route, request, *, gate, boundary, snapshot):
        self.requests.append(dict(request))
        if self.error is not None:
            raise RouteError(self.error)
        return SimpleNamespace(
            session_id='native-retry-run', exit_code=0,
            stdout_sha256=_sha256('retry run output'), elapsed_ms=1200,
            cleanup_status='CONFIRMED', usage=None, provider_evidence=None)


class Boundary:
    def prepare(self, root, paths, state_dirs=()):
        return ('fixture-policy', True)

    def verify(self, root, paths, state_dirs=(), *, runner):
        return {'verified': True, 'policy_sha256': _sha256('fixture boundary')}


def clean_probe(source_sha, write_paths=None):
    return source_sha == HEAD


def task_record(**extra):
    task_id = task_store.new_id()
    record = {
        'schema_version': 2, 'task_id': task_id, 'run_id': task_store.new_id(),
        'pm_session_id': PM_SESSION, 'state': 'PM_RESPONDED',
        'request': 'Write one short note about the retry outcome.',
        'response': '{"summary":"Write the retry note."}',
        'task_binding': {'task_id': task_id, 'contract_digest': CONTRACT},
        'pm_assignment': {'route': 'claude', 'risk': 'LOW',
                          'goal': 'Write the retry note.', 'acceptance': list(ACCEPTANCE)},
        'developer': None, 'completion': None,
        'created_at': '2026-11-17T00:00:00Z', 'updated_at': '2026-11-17T00:00:00Z',
    }
    record.update(extra)
    return record


def review_packet(root_id, *, version, comment_id, report=None, report_sha256=None):
    """(P, packet_sha256 of its base B) for one review repair, as review_repair builds them."""
    packet = {
        'task': root_id, 'pr': PR, 'executor_task': D6, 'failed_head': HEAD,
        'version': version, 'verdict_comment_id': comment_id, 'findings_count': 1,
        'reviewer_text_excerpt_untrusted_data': REVIEW_CJK,
    }
    base_sha256 = _sha256(start_failure.canonical(packet))
    if report is not None:
        packet['high_debug_report_untrusted_data'] = report[:6000]
        packet['high_debug_report_sha256'] = (report_sha256 if report_sha256 is not None
                                              else _sha256(report))
    return packet, base_sha256


def repair_claim(repair_task_id, *, attempt, version, comment_id, packet_sha256,
                 head=HEAD, state='DISPATCHED', report_sha256=None):
    claim = {
        'executor_task': D6, 'pr': PR, 'head_sha': head, 'version': version,
        'attempt': attempt, 'family': FAMILY, 'state': state,
        'repair_task_id': repair_task_id, 'comment_id': comment_id,
        'packet_sha256': packet_sha256, 'claimed_at': f'2026-11-17T0{attempt}:00:00Z',
    }
    if report_sha256 is not None:
        claim['high_debug_report_sha256'] = report_sha256
    return claim


def root_record(claims, high_debug=()):
    return task_record(
        developer={'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'claude',
                   'executor_task_id': D6, 'source_sha': EARLIER_HEAD},
        completion={'state': 'WAITING_EXECUTOR', 'executor_task_id': D6,
                    'evidence': {'source_sha': EARLIER_HEAD, 'result_head': None}},
        review_repair_claims=list(claims), high_debug=list(high_debug))


def rca_entry(report):
    return {'family': FAMILY, 'status': 'RCA_CONFIRMED', 'examined_sha': HEAD,
            'report': report, 'report_sha256': _sha256(report),
            'recorded_at': '2026-11-17T01:30:00Z'}


class TaskRetryStartTests(unittest.TestCase):
    def setUp(self):
        self.d6_binding_args = []

        def d6_binding(args):
            self.d6_binding_args.append(args)
            return REGISTRATION, SNAPSHOT

        stack = ExitStack()
        self.addCleanup(stack.close)
        for patcher in (
                patch.object(cli, '_delegate_d6_binding', side_effect=d6_binding),
                patch.object(cli.task_completion, 'validate_task_binding', return_value=None),
                patch.object(cli.task_completion, 'bind_executor_task',
                             return_value=(REGISTRATION, SNAPSHOT)),
                patch.object(cli.task_completion, 'read_d6_registration',
                             return_value=REGISTRATION),
                patch.object(cli.task_completion, 'collect_git_snapshot', return_value=SNAPSHOT),
                patch.object(cli.task_completion, '_worktree_diff',
                             return_value=([WRITE], DIFF.encode('utf-8'))),
                patch.object(cli, '_delegate_timeout', return_value=600),
                patch.object(cli, 'load_route_config',
                             return_value={'routes': {'claude': {'denied_dirs': []}}}),
                patch.object(cli, '_claude_developer_profile',
                             return_value=('fixture-model', 'medium', '/nonexistent/config')),
                patch.object(cli.shutil, 'which', return_value='/nonexistent/bin/claude'),
                patch.object(cli, '_DiagnosedClaudeRoute',
                             side_effect=lambda config: SimpleNamespace()),
                patch.object(cli, 'for_platform', return_value=Boundary()),
                patch.object(cli, '_developer_card_prefix',
                             return_value=(CARD, _sha256(CARD))),
                patch.object(cli, '_record_skill_delivery', return_value=None),
                patch.object(cli, 'resolve_route_max_diff_bytes', return_value=1 << 20),
                patch.object(cli, 'run_task_direct_result_event', return_value=0),
                patch.object(cli, '_task_view',
                             side_effect=lambda record: {'task_id': record['task_id']}),
                # The chain locks and the chain-wide count are covered by
                # test_task_delegate_repair_cap; the claim runs through here.
                patch.object(cli, '_delegate_chain_gate',
                             side_effect=lambda args, store, d6_binding, claim: claim()),
                patch.object(cli, '_check_delegate_repair_cap', return_value=None),
                patch.object(cli.worker_availability, 'quota_refusal', return_value=None),
                patch.object(worker_dispatch, 'evaluate', return_value=ALLOWED),
                patch('llmtool.pm_caller.check_caller', return_value=None),
                redirect_stdout(io.StringIO())):
            stack.enter_context(patcher)
        self.store = GateStore()

    # -- helpers ---------------------------------------------------------

    def first_delegate(self, task_id, *, packet=None, code='CLAUDE_CONFIG_INVALID'):
        """Run the real Claude delegate once with a route that fails before any process."""
        route = Route(error=code)
        args = Namespace(
            task_id=task_id, provider='claude', mode='direct', selection_source='PM',
            executor_task=D6, read_path=list(READ), write_path=[WRITE],
            acceptance=list(ACCEPTANCE), egress_basis=EGRESS, owner_ref=None,
            repair_packet=packet, context_stdin=False, timeout=None)
        self.assertEqual(cli.run_task_claude_delegate(args, store=self.store,
                                                      route_runner=route), 1)
        record = self.store.get(task_id)
        self.assertEqual(record['developer']['state'], 'BLOCKED')
        self.assertEqual(record['developer']['error_code'], code)
        return record, route

    def started_task(self, **extra):
        task = self.store.add(task_record(**extra))
        record, _route = self.first_delegate(task['task_id'])
        return record

    def retry(self, task_id, *, route=None, probe=clean_probe, executor_task=D6):
        route = Route() if route is None else route
        status = cli.run_task_retry_start(
            Namespace(task_id=task_id, executor_task=executor_task, pm_session=PM_SESSION),
            store=self.store, route_runner=route, d6_unchanged=probe)
        return status, route

    def assert_refused(self, task_id, code, *, probe=clean_probe, executor_task=D6):
        before = self.store.get(task_id)
        route = Route()
        with self.assertRaises(LLMToolError) as raised:
            self.retry(task_id, route=route, probe=probe, executor_task=executor_task)
        self.assertTrue(str(raised.exception).startswith(code), str(raised.exception))
        self.assertEqual(route.requests, [])
        self.assertEqual(self.store.get(task_id), before)
        return raised.exception

    def task_json(self, request):
        return request['prompt'].split('\nTASK_JSON=', 1)[1]

    def assert_retried(self, record, failed, completion):
        developer = record['developer']
        self.assertEqual(developer['state'], 'DEVELOPER_RESPONDED')
        self.assertNotEqual(developer['assignment_id'], failed['assignment_id'])
        self.assertNotEqual(developer['session_id'], failed['session_id'])
        for field in ('source_sha', 'executor_task_id', 'read_paths', 'write_paths',
                      'acceptance', 'egress_basis', 'owner_assignment_ref'):
            self.assertEqual(developer[field], failed[field], field)
        self.assertEqual(record['completion'], completion)
        entry = record['start_failures'][-1]
        self.assertEqual(entry['failed_developer'], failed)
        self.assertEqual(entry['retry_assignment_id'], developer['assignment_id'])
        self.assertEqual(entry['d6_head'], HEAD)
        self.assertEqual(entry['pm_session_id'], PM_SESSION)
        audit = record['dispatch_audit']
        start = [item for item in audit if item['action'] == 'START_RETRY']
        self.assertEqual(start[-1]['decision'], 'ALLOWED')
        self.assertEqual(start[-1]['assignment_id'], developer['assignment_id'])
        dispatch = [item for item in audit if item['action'] == 'DISPATCH' and
                    item.get('assignment_id') == developer['assignment_id']]
        self.assertEqual([item['decision'] for item in dispatch], ['ALLOWED'])
        # The retry rebinds the D6 recorded at the failed start.
        self.assertEqual(self.d6_binding_args[-1].cwd, ROOT)

    # -- parser ----------------------------------------------------------

    def test_parser_takes_no_scope_arguments(self):
        from llmtool.cli import parser_for_cli
        parser = parser_for_cli()
        args = parser.parse_args(['task', 'retry-start', 'a' * 32,
                                  '--executor-task', D6, '--pm-session', PM_SESSION])
        self.assertEqual((args.action, args.task_id, args.executor_task, args.pm_session),
                         ('retry-start', 'a' * 32, D6, PM_SESSION))
        for option in ('--read-path', '--write-path', '--acceptance', '--provider'):
            with self.subTest(option=option), \
                    patch('sys.stderr', new_callable=io.StringIO), \
                    self.assertRaises(SystemExit):
                parser.parse_args(['task', 'retry-start', 'a' * 32,
                                   '--executor-task', D6, option, WRITE])

    def test_cli_requires_fresh_source_then_dispatches_to_handler(self):
        from llmtool.cli import main
        calls = []
        with patch('llmtool.source_freshness.require_fresh_source',
                   side_effect=lambda: calls.append('fresh') or 'OK'), \
                patch.object(cli, 'run_task_retry_start',
                             side_effect=lambda args: calls.append(args) or 0):
            rc = main(['task', 'retry-start', 'a' * 32,
                       '--executor-task', D6, '--pm-session', PM_SESSION])
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], 'fresh')
        self.assertEqual((calls[1].action, calls[1].task_id, calls[1].executor_task),
                         ('retry-start', 'a' * 32, D6))

    def test_cli_stale_source_refusal_runs_no_handler(self):
        from llmtool.cli import main
        called = []

        def stale():
            raise LLMToolError('SOURCE_STALE: synthetic stale-source refusal for this test')

        with patch('llmtool.source_freshness.require_fresh_source', side_effect=stale), \
                patch.object(cli, 'run_task_retry_start',
                             side_effect=lambda args: called.append(args) or 0), \
                patch('sys.stderr', new_callable=io.StringIO), \
                redirect_stdout(io.StringIO()):
            rc = main(['task', 'retry-start', 'a' * 32,
                       '--executor-task', D6, '--pm-session', PM_SESSION])
        self.assertNotEqual(rc, 0)
        self.assertEqual(called, [])

    # -- first CAS persists the repair packet -----------------------------

    def test_first_cas_persists_canonical_repair_packet_inside_task_json(self):
        root = self.store.add(root_record([]))
        packet, _base = review_packet(root['task_id'], version=1, comment_id=7001)
        task = self.store.add(task_record())
        record, route = self.first_delegate(task['task_id'], packet=packet)
        self.assertEqual(record['repair_packet'], start_failure.canonical(packet))
        self.assertTrue(record['repair_packet'].isascii())
        self.assertIn(record['repair_packet'], self.task_json(route.requests[0]))
        self.assertEqual(start_failure.decode_repair_packet(record['repair_packet']), packet)

    def test_first_cas_round_trips_lone_surrogate_packet(self):
        packet, _base = review_packet('b' * 32, version=1, comment_id=7001)
        packet['reviewer_text_excerpt_untrusted_data'] = 'Reviewer note \ud800 ends here.'
        task = self.store.add(task_record())
        record, route = self.first_delegate(task['task_id'], packet=packet)
        self.assertEqual(start_failure.decode_repair_packet(record['repair_packet']), packet)
        self.assertIn(record['repair_packet'], self.task_json(route.requests[0]))

    def test_delegate_without_packet_writes_no_repair_packet(self):
        record = self.started_task()
        self.assertNotIn('repair_packet', record)

    # -- unchanged behaviour without retry-start --------------------------

    def test_plain_delegate_still_refuses_a_blocked_task(self):
        record = self.started_task()
        args = Namespace(
            task_id=record['task_id'], provider='claude', mode='direct', selection_source='PM',
            executor_task=D6, read_path=list(READ), write_path=[WRITE],
            acceptance=list(ACCEPTANCE), egress_basis=EGRESS, owner_ref=None,
            repair_packet=None, context_stdin=False, timeout=None)
        route = Route()
        with self.assertRaises(LLMToolError) as raised:
            cli.run_task_claude_delegate(args, store=self.store, route_runner=route)
        self.assertIn('duplicate dispatch refused', str(raised.exception))
        self.assertEqual(route.requests, [])
        self.assertEqual(self.store.get(record['task_id']), record)

    # -- first delegate and REPLAN ---------------------------------------

    def test_first_delegate_start_failure_is_retried(self):
        record = self.started_task()
        failed, completion = record['developer'], record['completion']
        status, route = self.retry(record['task_id'])
        self.assertEqual(status, 0)
        self.assertEqual(len(route.requests), 1)
        self.assertNotIn('untrusted_repair_reference', self.task_json(route.requests[0]))
        after = self.store.get(record['task_id'])
        self.assert_retried(after, failed, completion)
        self.assertEqual(len(after['start_failures']), 1)
        self.assertEqual(after['start_failures'][0]['record_sha256'], record['record_sha256'])
        self.assertNotIn('repair_packet', after)

    def test_replan_task_keeps_its_lineage_and_creates_no_task(self):
        lineage = {'role': 'replan', 'replaces': ['c' * 32], 'state': 'OPEN'}
        record = self.started_task(lineage=lineage)
        ids_before = self.store.task_ids()
        status, _route = self.retry(record['task_id'])
        self.assertEqual(status, 0)
        after = self.store.get(record['task_id'])
        self.assert_retried(after, record['developer'], record['completion'])
        self.assertEqual(after['lineage'], lineage)
        self.assertNotIn('continues', after)
        self.assertEqual(self.store.task_ids(), ids_before)

    # -- refusals with no write ------------------------------------------

    def test_dirty_d6_refuses_check_6(self):
        record = self.started_task()
        error = self.assert_refused(record['task_id'], 'START_RETRY_NOT_START_FAILURE',
                                    probe=lambda source_sha, write_paths=None: False)
        self.assertIn('check 6', str(error))

    def test_raising_probe_refuses_check_6(self):
        def probe(source_sha, write_paths=None):
            raise OSError('fixture probe failure')
        record = self.started_task()
        error = self.assert_refused(record['task_id'], 'START_RETRY_NOT_START_FAILURE',
                                    probe=probe)
        self.assertIn('check 6', str(error))

    def test_native_session_refuses_check_4(self):
        record = self.started_task()
        failed = dict(record['developer'], execution={
            'status': 'BLOCKED', 'native_session_id': 'native-earlier-run',
            'process_exit_code': None, 'stdout_sha256': None, 'elapsed_ms': None,
            'cleanup_status': 'CONFIRMED', 'usage_source': 'UNKNOWN', 'source_bytes': None,
            'source_file_count': None, 'source_manifest_sha256': None})
        self.store.force(record['task_id'], developer=failed)
        error = self.assert_refused(record['task_id'], 'START_RETRY_NOT_START_FAILURE')
        self.assertIn('check 4', str(error))

    def test_code_after_a_run_refuses_check_2(self):
        task = self.store.add(task_record())
        self.first_delegate(task['task_id'], code='CLAUDE_QUOTA_EXHAUSTED')
        error = self.assert_refused(task['task_id'], 'START_RETRY_NOT_START_FAILURE')
        self.assertIn('check 2', str(error))

    def test_task_without_developer_refuses_check_1(self):
        task = self.store.add(task_record())
        error = self.assert_refused(task['task_id'], 'START_RETRY_NOT_START_FAILURE')
        self.assertIn('check 1', str(error))

    def test_other_executor_task_refuses(self):
        record = self.started_task()
        self.assert_refused(record['task_id'], 'START_RETRY_D6_CHANGED',
                            executor_task='another-d6')

    def test_changed_pm_acceptance_refuses(self):
        record = self.started_task()
        assignment = dict(record['pm_assignment'], acceptance=['A different outcome.'])
        self.store.force(record['task_id'], pm_assignment=assignment)
        self.assert_refused(record['task_id'], 'START_RETRY_CONFLICT')

    def test_paused_policy_refuses_before_any_write(self):
        record = self.started_task()
        before = self.store.get(record['task_id'])
        with patch.object(worker_dispatch, 'evaluate', return_value=PAUSED), \
                self.assertRaises(worker_dispatch.WorkerDispatchDenied) as raised:
            self.retry(record['task_id'])
        self.assertEqual(raised.exception.dispatch_code, 'OWNER_PAUSED')
        self.assertEqual(self.store.get(record['task_id']), before)

    def test_third_start_failure_goes_to_the_pm(self):
        record = self.started_task()
        for number in (1, 2):
            status, route = self.retry(record['task_id'],
                                       route=Route(error='CLAUDE_ENV_INVALID'))
            self.assertEqual(status, 1)
            self.assertEqual(len(route.requests), 1)
            after = self.store.get(record['task_id'])
            self.assertEqual(len(after['start_failures']), number)
            self.assertEqual(after['developer']['error_code'], 'CLAUDE_ENV_INVALID')
        self.assert_refused(record['task_id'], 'START_RETRY_LIMIT_REACHED')
        audit = self.store.get(record['task_id'])['dispatch_audit']
        self.assertEqual(sum(1 for item in audit if item['action'] == 'START_RETRY' and
                             item['decision'] == 'ALLOWED'), 2)

    def test_concurrent_write_before_the_cas_refuses_conflict(self):
        record = self.started_task()

        def concurrent(store, task_id):
            store.force(task_id, updated_at='2026-11-17T09:00:00Z')

        self.store.before_bind = concurrent
        route = Route()
        with self.assertRaises(LLMToolError) as raised:
            self.retry(record['task_id'], route=route)
        self.assertTrue(str(raised.exception).startswith('START_RETRY_CONFLICT'))
        self.assertEqual(route.requests, [])
        after = self.store.get(record['task_id'])
        self.assertEqual(after['developer'], record['developer'])
        self.assertNotIn('start_failures', after)
        self.assertFalse(any(item['action'] == 'START_RETRY'
                             for item in after['dispatch_audit']))

    def test_developer_changed_under_the_chain_lock_refuses_conflict(self):
        record = self.started_task()
        replaced = dict(record['developer'], error_code='CLAUDE_ENV_INVALID')

        def gate(args, store, d6_binding, claim):
            store.force(args.task_id, developer=replaced)
            return claim()

        route = Route()
        with patch.object(cli, '_delegate_chain_gate', side_effect=gate), \
                self.assertRaises(LLMToolError) as raised:
            self.retry(record['task_id'], route=route)
        self.assertTrue(str(raised.exception).startswith('START_RETRY_CONFLICT'))
        self.assertEqual(route.requests, [])
        self.assertEqual(self.store.get(record['task_id'])['developer'], replaced)

    # -- repair Tasks -----------------------------------------------------

    def repair_task(self, packet):
        task = self.store.add(task_record())
        record, route = self.first_delegate(task['task_id'], packet=packet)
        return record, route

    def test_repair_1_retry_resends_the_bound_packet(self):
        root_id = task_store.new_id()
        packet, base_sha256 = review_packet(root_id, version=1, comment_id=7001)
        record, _first = self.repair_task(packet)
        root = root_record([repair_claim(record['task_id'], attempt=1, version=1,
                                         comment_id=7001, packet_sha256=base_sha256)])
        root['task_id'] = root_id
        root['task_binding'] = dict(root['task_binding'], task_id=root_id)
        self.store.add(root)
        root_before = self.store.get(root_id)
        status, route = self.retry(record['task_id'])
        self.assertEqual(status, 0)
        self.assertIn(record['repair_packet'], self.task_json(route.requests[0]))
        self.assert_retried(self.store.get(record['task_id']), record['developer'],
                            record['completion'])
        self.assertEqual(self.store.get(root_id), root_before)

    def add_root(self, root_id, claims, high_debug=()):
        root = root_record(claims, high_debug)
        root['task_id'] = root_id
        root['task_binding'] = dict(root['task_binding'], task_id=root_id)
        return self.store.add(root)

    def repair_2_fixture(self, *, report=REPORT_CJK, packet_report_sha256=None):
        """Root R with attempt 2 DISPATCHED for T, bound to High Debug digest H."""
        root_id = task_store.new_id()
        report_sha256 = _sha256(report)
        packet, base_sha256 = review_packet(
            root_id, version=2, comment_id=7002, report=report,
            report_sha256=packet_report_sha256)
        record, first = self.repair_task(packet)
        earlier_packet, earlier_sha256 = review_packet(root_id, version=1, comment_id=7001)
        claims = [
            repair_claim(task_store.new_id(), attempt=1, version=1, comment_id=7001,
                         packet_sha256=earlier_sha256, head=EARLIER_HEAD),
            repair_claim(record['task_id'], attempt=2, version=2, comment_id=7002,
                         packet_sha256=base_sha256, report_sha256=report_sha256),
        ]
        self.add_root(root_id, claims, [rca_entry(report)])
        return record, first, root_id, report_sha256

    def test_successful_repair_2_retry_with_maximal_cjk_packet(self):
        record, first, root_id, report_sha256 = self.repair_2_fixture()
        stored = record['repair_packet']
        # The boundary packet: 2000 CJK reviewer and 6000 CJK High Debug characters.
        self.assertGreater(len(stored), 16384)
        self.assertLessEqual(len(stored), task_store.REPAIR_PACKET_MAX_BYTES)
        self.assertIn(stored, self.task_json(first.requests[0]))
        root_before = self.store.get(root_id)
        claims_before = json.dumps(root_before['review_repair_claims'], sort_keys=True)
        counts_before = lineage_counts.lineage_counts(
            self.store, root_before, base_family=FAMILY, include_successors=True)
        repairs_before = lineage_counts.chain_review_repairs(self.store, root_before)
        ids_before = self.store.task_ids()

        status, route = self.retry(record['task_id'])

        self.assertEqual(status, 0)
        self.assertEqual(len(route.requests), 1)
        task_json = self.task_json(route.requests[0])
        self.assertIn(stored, task_json)
        sent = json.loads(task_json)['untrusted_repair_reference']
        self.assertEqual(sent['high_debug_report_sha256'], report_sha256)
        self.assertEqual(sent['high_debug_report_untrusted_data'], REPORT_CJK[:6000])
        after = self.store.get(record['task_id'])
        self.assert_retried(after, record['developer'], record['completion'])
        self.assertEqual(after['repair_packet'], stored)
        root_after = self.store.get(root_id)
        self.assertEqual(root_after, root_before)
        self.assertEqual(json.dumps(root_after['review_repair_claims'], sort_keys=True),
                         claims_before)
        self.assertEqual(lineage_counts.lineage_counts(
            self.store, root_after, base_family=FAMILY, include_successors=True),
            counts_before)
        self.assertEqual(lineage_counts.chain_review_repairs(self.store, root_after),
                         repairs_before)
        self.assertEqual(self.store.task_ids(), ids_before)

    def test_repair_2_packet_with_wrong_high_debug_digest_refuses(self):
        record, _first, root_id, _h = self.repair_2_fixture(
            packet_report_sha256=_sha256(OTHER_REPORT_CJK))
        root_before = self.store.get(root_id)
        self.assert_refused(record['task_id'], 'START_RETRY_REPAIR_PACKET_MISMATCH')
        self.assertEqual(self.store.get(root_id), root_before)

    def test_repair_packet_but_no_claim_names_the_task(self):
        packet, _base = review_packet(task_store.new_id(), version=1, comment_id=7001)
        record, _first = self.repair_task(packet)
        self.assert_refused(record['task_id'], 'START_RETRY_REPAIR_CLAIM_INVALID')

    def test_two_roots_name_the_task(self):
        root_id = task_store.new_id()
        packet, base_sha256 = review_packet(root_id, version=1, comment_id=7001)
        record, _first = self.repair_task(packet)
        claim = repair_claim(record['task_id'], attempt=1, version=1, comment_id=7001,
                             packet_sha256=base_sha256)
        self.add_root(root_id, [claim])
        self.add_root(task_store.new_id(), [claim])
        self.assert_refused(record['task_id'], 'START_RETRY_REPAIR_ROOT_UNKNOWN')

    def test_unknown_or_failed_claim_refuses(self):
        for state in ('UNKNOWN', 'FAILED', 'CLAIMED'):
            with self.subTest(state=state):
                root_id = task_store.new_id()
                packet, base_sha256 = review_packet(root_id, version=1, comment_id=7001)
                record, _first = self.repair_task(packet)
                self.add_root(root_id, [repair_claim(
                    record['task_id'], attempt=1, version=1, comment_id=7001,
                    packet_sha256=base_sha256, state=state)])
                self.assert_refused(record['task_id'], 'START_RETRY_REPAIR_CLAIM_INVALID')

    def test_claim_that_is_not_the_last_of_its_family_refuses(self):
        root_id = task_store.new_id()
        packet, base_sha256 = review_packet(root_id, version=1, comment_id=7001)
        record, _first = self.repair_task(packet)
        self.add_root(root_id, [
            repair_claim(record['task_id'], attempt=1, version=1, comment_id=7001,
                         packet_sha256=base_sha256),
            repair_claim(task_store.new_id(), attempt=2, version=2, comment_id=7002,
                         packet_sha256=base_sha256, head=EARLIER_HEAD)])
        self.assert_refused(record['task_id'], 'START_RETRY_REPAIR_CLAIM_INVALID')

    def test_pre_pr2_repair_task_has_no_packet(self):
        record = self.started_task()
        self.assertNotIn('repair_packet', record)
        root_id = task_store.new_id()
        _packet, base_sha256 = review_packet(root_id, version=1, comment_id=7001)
        self.add_root(root_id, [repair_claim(record['task_id'], attempt=1, version=1,
                                             comment_id=7001, packet_sha256=base_sha256)])
        self.assert_refused(record['task_id'], 'START_RETRY_REPAIR_PACKET_UNAVAILABLE')

    def test_unreadable_store_scan_refuses_root_unknown(self):
        record = self.started_task()
        with patch.object(self.store, 'task_ids', side_effect=OSError('fixture scan failure')):
            self.assert_refused(record['task_id'], 'START_RETRY_REPAIR_ROOT_UNKNOWN')


if __name__ == '__main__':
    unittest.main()
