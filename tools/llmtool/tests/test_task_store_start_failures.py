"""#2188 PR 1: TaskStore start_failures and repair_packet rules (design sections 3-4).

Writes go through the real TaskStore, dispatch gate and D6 binding with the
shared all-ALLOWED test policy. A test that checks the store's own backstop
injects an allowing gate and says so. All text is plain or CJK prose with no
credential-shaped strings (Owner #763 A).
"""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_MODULE_HOME = tempfile.TemporaryDirectory()
_IMPORT_HOME_PATCH = patch.dict(os.environ, {'HOME': _MODULE_HOME.name})
_IMPORT_HOME_PATCH.start()
from llmtool import start_failure, task_store, worker_dispatch
from llmtool import task_submission_cli
from llmtool.developer_routes import claude as claude_route
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore, new_id
from llmtool.task_submission_cli import _record
from review_trust_fixture import start_module as _start_trust_fixture
from review_trust_fixture import stop_module as _stop_trust_fixture
import worker_policy_fixture
_IMPORT_HOME_PATCH.stop()

D6 = 'executor-start-retry'
SOURCE = 'a' * 40
HEAD = 'c' * 40
NOW = '2026-11-17T00:00:00Z'
ROOT_TASK = '1' * 32
REVIEW_PROSE = '审查意见：请为配置读取顺序补充边界测试，并说明修改原因。'
DEBUG_PROSE = '根因分析：默认值在显式设置之后写入，因此覆盖了用户的选择。'


def setUpModule():
    _start_trust_fixture()
    global _MODULE_HOME_PATCH
    _MODULE_HOME_PATCH = patch.dict(os.environ, {'HOME': _MODULE_HOME.name})
    _MODULE_HOME_PATCH.start()
    user_config = Path(_MODULE_HOME.name) / '.config' / 'llmtool' / 'config.yaml'
    user_config.parent.mkdir(parents=True, exist_ok=True)
    user_config.write_text('generation: {}\n', encoding='utf-8')


def tearDownModule():
    _MODULE_HOME_PATCH.stop()
    _stop_trust_fixture()
    _MODULE_HOME.cleanup()


def claude_pending(callback):
    return {
        'assignment_id': new_id(), 'selection_source': 'PM', 'state': 'DEVELOPER_PENDING',
        'provider_profile': 'claude', 'provider_type': None, 'model': None,
        'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
        'session_id': 'dev-' + new_id(), 'source_sha': SOURCE,
        'source_sha_binding': 'D6_REGISTRY_OBSERVED',
        'read_paths': ['src.py'], 'write_paths': ['src.py'],
        'acceptance': ['the focused test passes'], 'context_sha256': 'b' * 64,
        'egress_basis': 'Plain project text for a bounded change.', 'input_bytes': 10,
        'max_tokens': None, 'artifact_format': 'unified_diff', 'artifact': None,
        'artifact_sha256': None, 'usage': None, 'error_code': None, 'execution': None,
        'created_at': NOW, 'owner_assignment_ref': None, 'executor_task_id': D6,
        'timeout_seconds': 600, 'direct_result_callback': callback,
    }


def dispatching_of(developer):
    return dict(developer, state='DEVELOPER_DISPATCHING', provider_type='claude-cli',
                model='claude-test-1')


def retry_of(failed, **overrides):
    value = dict(failed, assignment_id=new_id(), session_id='dev-' + new_id(),
                 state='DEVELOPER_PENDING', provider_type=None, model=None,
                 artifact=None, artifact_sha256=None, usage=None, error_code=None,
                 execution=None)
    value.update(overrides)
    return value


def archive_entry(current, failed, retry, **overrides):
    entry = {'failed_developer': failed, 'record_sha256': current['record_sha256'],
             'retry_assignment_id': retry['assignment_id'], 'd6_head': SOURCE,
             'retried_at': NOW, 'pm_session_id': 'pm-start-retry'}
    entry.update(overrides)
    return entry


def cjk(text, count):
    """``count`` characters of repeated CJK prose."""
    return (text * (count // len(text) + 1))[:count]


def repair_packet(reviewer=REVIEW_PROSE, debug=None):
    packet = {'task': ROOT_TASK, 'pr': 41, 'executor_task': D6, 'failed_head': HEAD,
              'version': 2, 'verdict_comment_id': 777, 'findings_count': 1,
              'reviewer_text_excerpt_untrusted_data': reviewer}
    if debug is not None:
        packet.update(high_debug_report_untrusted_data=debug,
                      high_debug_report_sha256=hashlib.sha256(
                          debug.encode('utf-8', 'surrogatepass')).hexdigest())
    return packet


def sized_packet_text(size):
    """A canonical repair-2 packet text of exactly ``size`` bytes (non-BMP CJK prose)."""
    packet = repair_packet(reviewer='', debug='')
    remaining = size - len(start_failure.canonical(packet))
    wide = min(remaining // 12, start_failure.HIGH_DEBUG_EXCERPT_MAX_CHARS)
    packet['high_debug_report_untrusted_data'] = '\U00020000' * wide
    packet['reviewer_text_excerpt_untrusted_data'] = 'z' * (remaining - 12 * wide)
    text = start_failure.canonical(packet)
    assert len(text) == size, (len(text), size)
    return text


def plain_diff(size):
    """A plain unified diff of exactly ``size`` bytes."""
    header = 'diff --git a/src.py b/src.py\n--- a/src.py\n+++ b/src.py\n@@ -0,0 +1 @@\n'
    count, extra = divmod(size - len(header), 6)
    if extra == 1:
        count, extra = count - 1, extra + 6
    tail = '+' + 'p' * (extra - 2) + '\n' if extra else ''
    return header + '+pass\n' * count + tail


class StartFailureStoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        worker_policy_fixture.install(self)
        repository = patch.object(worker_dispatch, 'repository_config',
            return_value={'slug': 'davidcatcat/davidcattoolsproject',
                          'dispatch_issue': 289, 'dispatch_evidence_pr': 283})
        repository.start()
        self.addCleanup(repository.stop)
        self.store = TaskStore(Path(temp.name).resolve() / 'tasks')

    # -- helpers ---------------------------------------------------------

    def _task(self):
        request = 'Bounded plain Developer task.'
        task_id, run_id = new_id(), new_id()
        task = _record(task_id, run_id, 'pm-' + new_id(), 'pm-profile', request,
                       state='PM_RESPONDED', provider_type='openai-compatible',
                       model='pm-model', backend='NO_NATIVE_SESSION_ID_EXPOSED',
                       request_sha=hashlib.sha256(request.encode()).hexdigest())
        task.update(pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                                      'base_url': None},
                    response='PM accepted a bounded request.', error_code=None,
                    pm_assignment={'schema_version': 1, 'route': 'claude', 'risk': 'LOW',
                                   'goal': 'Add a focused test.',
                                   'acceptance': ['the focused test passes']})
        self.store.create(task)
        return task

    def _bind(self, task, **extra):
        """The delegate's first CAS: developer and completion from None."""
        registration = SimpleNamespace(
            task_id=D6, root='/registered/root', common_dir='/repo/.git',
            branch='task-branch', trusted_agent_id='claude-developer',
            session_ref='session-ref', native_session_id=None)
        callback = task_submission_cli._direct_result_callback(
            task, registration, SOURCE, test_policy='LOCAL')
        completion = {'state': 'WAITING_EXECUTOR', 'executor_task_id': D6,
                      'reviewer_task_id': None, 'evidence': dict(callback['evidence']),
                      'missing_gates': [], 'pm_dispatch': 'NOT_STARTED',
                      'pm_response': None, 'pm_recommendation': None, 'pm_usage': None,
                      'pm_error_code': None}
        return self.store.d6_bind_compare_and_update(
            task['task_id'], executor_task_id=D6,
            expected={'schema_version': 2, 'state': 'PM_RESPONDED', 'developer': None,
                      'completion': None},
            developer=claude_pending(callback), completion=completion, **extra)

    def _fail_start(self, task_id, code='CLAUDE_CONFIG_INVALID'):
        dispatching = dispatching_of(self.store.get(task_id)['developer'])
        self.store.update(task_id, developer=dispatching)
        return self.store.update(task_id, developer=dict(
            dispatching, state='BLOCKED', error_code=code))

    def _failed_task(self, code='CLAUDE_CONFIG_INVALID', **bind_extra):
        task = self._task()
        self._bind(task, **bind_extra)
        self._fail_start(task['task_id'], code)
        return task['task_id']

    def _retry(self, task_id, *, entry_overrides=None, d6_unchanged=None, **changes):
        current = self.store.get(task_id)
        failed = current['developer']
        retry = retry_of(failed)
        entry = archive_entry(current, failed, retry, **(entry_overrides or {}))
        values = {'developer': retry,
                  'start_failures': current.get('start_failures', []) + [entry]}
        values.update(changes)
        if d6_unchanged is None:
            d6_unchanged = lambda source, write_paths=None: True
        return self.store.d6_bind_compare_and_update(
            task_id, executor_task_id=D6,
            expected={'schema_version': 2, 'state': 'PM_RESPONDED', 'developer': failed,
                      'completion': current['completion'],
                      'd6_unchanged': d6_unchanged},
            **values)

    def assertStoreCode(self, caught, code):
        self.assertEqual(getattr(caught.exception, 'task_store_error_code', None), code)

    # -- start_failures ----------------------------------------------------

    def test_retry_appends_one_entry_and_keeps_the_completion(self):
        task_id = self._failed_task()
        before = self.store.get(task_id)
        saved = self._retry(task_id)
        self.assertEqual(len(saved['start_failures']), 1)
        entry = saved['start_failures'][0]
        self.assertEqual(entry['failed_developer'], before['developer'])
        self.assertEqual(entry['record_sha256'], before['record_sha256'])
        self.assertEqual(entry['retry_assignment_id'], saved['developer']['assignment_id'])
        self.assertEqual(saved['completion'], before['completion'])
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_PENDING')

    def test_at_most_two_retries_and_the_third_is_audited_limit(self):
        task_id = self._failed_task()
        self._retry(task_id)
        self._fail_start(task_id, 'CLAUDE_ENV_INVALID')
        self._retry(task_id)
        self._fail_start(task_id, 'CLAUDE_REQUEST_INVALID')
        before = self.store.get(task_id)
        self.assertEqual(len(before['start_failures']), task_store.MAX_START_FAILURES)
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
            self._retry(task_id)
        self.assertEqual(caught.exception.dispatch_code, 'WORKER_START_RETRY_LIMIT')
        saved = self.store.get(task_id)
        self.assertEqual(saved['developer'], before['developer'])
        self.assertEqual(saved['start_failures'], before['start_failures'])
        self.assertEqual((saved['dispatch_audit'][-1]['action'],
                          saved['dispatch_audit'][-1]['decision']),
                         ('START_RETRY', 'BLOCKED'))
        # The store's own cap holds even when a gate would allow a third entry.
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            with self.assertRaises(LLMToolError) as caught:
                self._retry(task_id)
        self.assertStoreCode(caught, 'START_RETRY_INVALID')
        self.assertEqual(self.store.get(task_id)['start_failures'], before['start_failures'])
        third = dict(before['start_failures'][-1], retry_assignment_id=new_id())
        with self.assertRaises(LLMToolError) as caught:
            TaskStore._encoded(dict(before, start_failures=before['start_failures'] + [third]))
        self.assertStoreCode(caught, 'START_RETRY_INVALID')

    def test_start_failures_change_only_by_one_append(self):
        task_id = self._failed_task()
        self._retry(task_id)
        self._fail_start(task_id)
        stored = self.store.get(task_id)['start_failures']
        for name, value in (('removed', []), ('cleared', None),
                            ('rewritten', [dict(stored[0], d6_head='d' * 40)])):
            with self.subTest(case=name):
                with self.assertRaises(LLMToolError) as caught:
                    self.store.update(task_id, start_failures=value)
                self.assertStoreCode(caught, 'START_RETRY_INVALID')
        # A retry whose earlier entry was rewritten is refused by the gate first.
        current = self.store.get(task_id)
        failed = current['developer']
        retry = retry_of(failed)
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
            self.store.d6_bind_compare_and_update(
                task_id, executor_task_id=D6,
                expected={'developer': failed, 'completion': current['completion'],
                          'd6_unchanged': lambda s, write_paths=None: True},
                developer=retry, start_failures=[dict(stored[0], d6_head='d' * 40),
                                                 archive_entry(current, failed, retry)])
        self.assertEqual(caught.exception.dispatch_code, 'WORKER_START_RETRY_PRECONDITION')
        saved = self.store.get(task_id)
        self.assertEqual(saved['start_failures'], stored)
        self.assertEqual(saved['developer'], failed)

    def test_store_retry_refuses_when_d6_probe_fails_or_missing(self):
        task_id = self._failed_task()
        # Changed D6 probe refuses
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
            self._retry(task_id, d6_unchanged=lambda s, write_paths=None: False)
        self.assertEqual(caught.exception.dispatch_code, 'WORKER_START_RETRY_PRECONDITION')
        self.assertEqual(self.store.get(task_id)['dispatch_audit'][-1]['decision'], 'BLOCKED')

        # Raising probe refuses
        def raising(s, write_paths=None):
            raise RuntimeError('probe failed')
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
            self._retry(task_id, d6_unchanged=raising)
        self.assertEqual(caught.exception.dispatch_code, 'WORKER_START_RETRY_PRECONDITION')
        self.assertEqual(self.store.get(task_id)['dispatch_audit'][-1]['decision'], 'BLOCKED')

        # Missing probe in raw call refuses
        current = self.store.get(task_id)
        failed = current['developer']
        retry = retry_of(failed)
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
            self.store.d6_bind_compare_and_update(
                task_id, executor_task_id=D6,
                expected={'developer': failed, 'completion': current['completion']},
                developer=retry, start_failures=[archive_entry(current, failed, retry)])
        self.assertEqual(caught.exception.dispatch_code, 'WORKER_START_RETRY_PRECONDITION')
        self.assertEqual(self.store.get(task_id)['dispatch_audit'][-1]['decision'], 'BLOCKED')

    def test_entry_that_does_not_archive_the_stored_developer_is_refused(self):
        task_id = self._failed_task()
        failed = self.store.get(task_id)['developer']
        other = dict(failed, session_id='dev-other')
        # Through the real gate: refused and audited, nothing else written.
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
            self._retry(task_id, entry_overrides={'failed_developer': other})
        self.assertEqual(caught.exception.dispatch_code, 'WORKER_START_RETRY_PRECONDITION')
        saved = self.store.get(task_id)
        self.assertEqual(saved['developer'], failed)
        self.assertNotIn('start_failures', saved)
        self.assertEqual((saved['dispatch_audit'][-1]['action'],
                          saved['dispatch_audit'][-1]['decision'],
                          saved['dispatch_audit'][-1]['reason_code']),
                         ('START_RETRY', 'BLOCKED', 'WORKER_START_RETRY_PRECONDITION'))
        # The store's own rule, with an injected allowing gate.
        for name, overrides in (('failed developer', {'failed_developer': other}),
                                ('retry assignment', {'retry_assignment_id': new_id()})):
            with self.subTest(case=name):
                with patch.object(worker_dispatch, 'authorize_task_store_change',
                                  return_value=None):
                    with self.assertRaises(LLMToolError) as caught:
                        self._retry(task_id, entry_overrides=overrides)
                self.assertStoreCode(caught, 'START_RETRY_INVALID')
                self.assertNotIn('start_failures', self.store.get(task_id))

    def test_start_failures_never_ride_on_another_write(self):
        task_id = self._failed_task()
        # Alone, with no developer change.
        current = self.store.get(task_id)
        retry = retry_of(current['developer'])
        with self.assertRaises(LLMToolError) as caught:
            self.store.update(task_id, start_failures=[
                archive_entry(current, current['developer'], retry)])
        self.assertStoreCode(caught, 'START_RETRY_INVALID')
        # On the following PENDING -> DISPATCHING write (a DISPATCH).
        self._retry(task_id)
        current = self.store.get(task_id)
        pending = current['developer']
        dispatching = dispatching_of(pending)
        with self.assertRaises(LLMToolError) as caught:
            self.store.update(task_id, developer=dispatching, start_failures=(
                current['start_failures'] + [archive_entry(current, pending, dispatching)]))
        self.assertStoreCode(caught, 'START_RETRY_INVALID')
        self.assertEqual(self.store.get(task_id)['developer'], pending)
        # On create.
        record = self._task()
        record = dict(self.store.get(record['task_id']), task_id=new_id(),
                      start_failures=current['start_failures'])
        with self.assertRaises(LLMToolError) as caught:
            self.store.create(record)
        self.assertStoreCode(caught, 'START_RETRY_INVALID')

    def test_refused_retry_of_a_non_start_failure_keeps_its_blocked_audit(self):
        task_id = self._failed_task(code='CLAUDE_NO_CHANGES')
        before = self.store.get(task_id)
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
            self._retry(task_id)
        self.assertEqual(caught.exception.dispatch_code, 'WORKER_START_RETRY_PRECONDITION')
        saved = self.store.get(task_id)
        self.assertEqual(saved['developer'], before['developer'])
        self.assertEqual(saved['dispatch_audit'][:-1], before['dispatch_audit'])
        self.assertEqual((saved['dispatch_audit'][-1]['action'],
                          saved['dispatch_audit'][-1]['decision']),
                         ('START_RETRY', 'BLOCKED'))

    def test_clearing_the_developer_stays_forbidden(self):
        task_id = self._failed_task()
        self._retry(task_id)
        self._fail_start(task_id)
        before = self.store.get(task_id)
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
            self.store.update(task_id, developer=None)
        self.assertEqual(caught.exception.dispatch_code, 'WORKER_ASSIGNMENT_CLEAR_FORBIDDEN')
        saved = self.store.get(task_id)
        self.assertEqual(saved['developer'], before['developer'])
        self.assertEqual(saved['start_failures'], before['start_failures'])
        self.assertEqual(saved['dispatch_audit'][-1]['reason_code'],
                         'WORKER_ASSIGNMENT_CLEAR_FORBIDDEN')

    # -- repair_packet -----------------------------------------------------

    def test_repair_packet_is_written_once_with_the_first_developer(self):
        text = start_failure.canonical(repair_packet())
        task_id = self._failed_task(repair_packet=text)
        self.assertEqual(self.store.get(task_id)['repair_packet'], text)
        other = start_failure.canonical(repair_packet(reviewer='另一段审查意见。'))
        for value in (text, other, None):
            with self.subTest(value=value):
                with self.assertRaises(LLMToolError) as caught:
                    self.store.update(task_id, repair_packet=value)
                self.assertStoreCode(caught, 'REPAIR_PACKET_IMMUTABLE')
        with self.assertRaises(LLMToolError) as caught:
            self._retry(task_id, repair_packet=text)
        self.assertStoreCode(caught, 'REPAIR_PACKET_IMMUTABLE')
        # A retry leaves the packet unchanged.
        saved = self._retry(task_id)
        self.assertEqual(saved['repair_packet'], text)

    def test_repair_packet_needs_the_write_that_sets_developer_from_none(self):
        text = start_failure.canonical(repair_packet())
        task = self._task()
        with self.assertRaises(LLMToolError) as caught:
            self.store.update(task['task_id'], repair_packet=text)
        self.assertStoreCode(caught, 'REPAIR_PACKET_IMMUTABLE')
        record = dict(self.store.get(task['task_id']), task_id=new_id(), repair_packet=text)
        with self.assertRaises(LLMToolError) as caught:
            self.store.create(record)
        self.assertStoreCode(caught, 'REPAIR_PACKET_IMMUTABLE')

    def test_boundary_packet_is_accepted_next_to_a_maximal_artifact(self):
        packet = repair_packet(reviewer=cjk(REVIEW_PROSE, 2000), debug=cjk(DEBUG_PROSE, 6000))
        self.assertTrue(start_failure.repair_packet_shape_ok(packet))
        text = start_failure.canonical(packet)
        self.assertTrue(text.isascii())
        self.assertGreater(len(text), 16384)
        self.assertLessEqual(len(text), task_store.REPAIR_PACKET_MAX_BYTES)
        task = self._task()
        self._bind(task, repair_packet=text)
        dispatching = dispatching_of(self.store.get(task['task_id'])['developer'])
        self.store.update(task['task_id'], developer=dispatching)
        artifact = plain_diff(task_store.MAX_ARTIFACT_BYTES)
        self.assertEqual(len(artifact.encode('utf-8')), task_store.MAX_ARTIFACT_BYTES)
        responded = dict(
            dispatching, state='DEVELOPER_RESPONDED', artifact=artifact,
            artifact_sha256=hashlib.sha256(artifact.encode('utf-8')).hexdigest(),
            execution={'status': 'DONE', 'native_session_id': '0123456789abcdef-0123',
                       'process_exit_code': 0, 'stdout_sha256': 'c' * 64,
                       'elapsed_ms': 10, 'cleanup_status': 'CONFIRMED',
                       'usage_source': 'UNKNOWN', 'source_bytes': None,
                       'source_file_count': None, 'source_manifest_sha256': None})
        self.store.update(task['task_id'], developer=responded)
        saved = self.store.get(task['task_id'])
        self.assertEqual(saved['repair_packet'], text)
        self.assertEqual(saved['developer']['artifact'], artifact)
        self.assertEqual(start_failure.decode_repair_packet(saved['repair_packet']), packet)

    def test_lone_surrogate_packet_is_accepted_and_round_trips(self):
        packet = repair_packet(reviewer='审查意见\ud800结尾')
        text = start_failure.canonical(packet)
        self.assertIn('\\ud800', text)
        task_id = self._failed_task(repair_packet=text)
        saved = self.store.get(task_id)
        self.assertEqual(saved['repair_packet'], text)
        self.assertEqual(start_failure.decode_repair_packet(saved['repair_packet']), packet)

    def test_packet_byte_bound(self):
        self.assertEqual(task_store.REPAIR_PACKET_MAX_BYTES, 5 * 1024 * 1024)
        self.assertGreaterEqual(task_store.REPAIR_PACKET_MAX_BYTES,
                                claude_route.MAX_PROMPT_BYTES)
        record = self.store.get(self._task()['task_id'])
        # A canonical packet's excerpts are bounded (2000/6000 chars), so no
        # valid packet reaches 5 MiB; the old 64 KiB ceiling no longer applies.
        TaskStore._encoded(dict(record, repair_packet=sized_packet_text(65537)))
        with self.assertRaises(LLMToolError) as caught:
            TaskStore._encoded(dict(record,
                                    repair_packet='z' * (5 * 1024 * 1024 + 1)))
        self.assertStoreCode(caught, 'REPAIR_PACKET_INVALID')

    def test_invalid_packet_text_is_refused(self):
        packet = repair_packet(reviewer=REVIEW_PROSE, debug=DEBUG_PROSE)
        text = start_failure.canonical(packet)
        missing = {key: value for key, value in packet.items() if key != 'findings_count'}
        cases = {
            'non ascii': json.dumps(packet, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')),
            'spacing': json.dumps(packet, ensure_ascii=True, sort_keys=True),
            'key order': json.dumps(dict(reversed(sorted(packet.items()))),
                                    ensure_ascii=True, separators=(',', ':')),
            'missing field': start_failure.canonical(missing),
            'long excerpt': start_failure.canonical(dict(
                packet, reviewer_text_excerpt_untrusted_data='z' * 2001)),
            'one debug field': start_failure.canonical({
                key: value for key, value in packet.items()
                if key != 'high_debug_report_sha256'}),
            'not an object': start_failure.canonical([packet]),
            'empty': '',
            'object': packet,
            'null': None,
        }
        record = self.store.get(self._task()['task_id'])
        TaskStore._encoded(dict(record, repair_packet=text))
        for name, value in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(LLMToolError) as caught:
                    TaskStore._encoded(dict(record, repair_packet=value))
                self.assertStoreCode(caught, 'REPAIR_PACKET_INVALID')
        # Through the delegate's first CAS: refused, nothing written.
        task = self._task()
        with self.assertRaises(LLMToolError) as caught:
            self._bind(task, repair_packet=cases['spacing'])
        self.assertStoreCode(caught, 'REPAIR_PACKET_INVALID')
        saved = self.store.get(task['task_id'])
        self.assertIsNone(saved['developer'])
        self.assertNotIn('repair_packet', saved)


if __name__ == '__main__':
    unittest.main()
