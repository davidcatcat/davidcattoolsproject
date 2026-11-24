"""#2281 rows 13 and 19: policy callers for ci_excerpt_max_lines and start_retries_max.

C1: task_advance._ci_excerpt keeps at most min(ci_excerpt_max_lines, 40) lines;
    review_repair._ci_failure validator stays at fixed 40; invalid policy fails
    closed.
R1: both START_RETRY_LIMIT_REACHED gates in task_submission_cli refuse at
    min(start_retries_max, 2); worker_dispatch and TaskStore keep fixed 2.

Test data (Owner #763 A): plain prose and SHA digests of fixed prose only.
No credential-shaped strings.
"""

import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from argparse import Namespace
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import repair_policy
from llmtool import review_repair
from llmtool import task_advance
from llmtool import task_store
from llmtool import task_submission_cli as cli
from llmtool import worker_dispatch
from llmtool.developer_routes import RouteError
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStoreConflict


BASE_POLICY = {
    'schema_version': 1,
    'repair_max': 5,
    'parent_debug_max': 1,
    'group_debug_max': 3,
    'child_max': 3,
    'replan_max': 1,
    'test_fail_max': 5,
    'review_fail_max': 6,
    'waiting_notify_after_hours': 24,
}


def _sha1(text):
    return hashlib.sha1(text.encode('utf-8')).hexdigest()


def _sha256(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _write_policy(test_case, data):
    handle, path = tempfile.mkstemp(suffix='.json')
    with os.fdopen(handle, 'w', encoding='utf-8') as stream:
        if isinstance(data, str):
            stream.write(data)
        else:
            stream.write(json.dumps(data))
    test_case.addCleanup(os.unlink, path)
    return Path(path)


class CiExcerptPolicyCallerTests(unittest.TestCase):
    """C1: task_advance._ci_excerpt reads ci_excerpt_max_lines capped at 40."""

    def _run_with_lines(self, count):
        text = '\n'.join(f'line {i}' for i in range(1, count + 1))
        return {'output': {'summary': text}}

    def test_configured_ci_excerpt_max_lines_5_gives_5_lines(self):
        path = _write_policy(self, {**BASE_POLICY, 'ci_excerpt_max_lines': 5})
        with patch.object(repair_policy, 'policy_path', return_value=path):
            excerpt = task_advance._ci_excerpt(self._run_with_lines(50))
        self.assertEqual(excerpt.splitlines(), [f'line {i}' for i in range(1, 6)])

    def test_absent_ci_excerpt_max_lines_gives_40_lines(self):
        path = _write_policy(self, BASE_POLICY)
        with patch.object(repair_policy, 'policy_path', return_value=path):
            excerpt = task_advance._ci_excerpt(self._run_with_lines(50))
        self.assertEqual(excerpt.splitlines(), [f'line {i}' for i in range(1, 41)])

    def test_ci_excerpt_max_lines_100_is_capped_at_40_lines(self):
        path = _write_policy(self, {**BASE_POLICY, 'ci_excerpt_max_lines': 100})
        with patch.object(repair_policy, 'policy_path', return_value=path):
            excerpt = task_advance._ci_excerpt(self._run_with_lines(50))
        self.assertEqual(excerpt.splitlines(), [f'line {i}' for i in range(1, 41)])

    def test_review_repair_validator_keeps_fixed_40_line_bound(self):
        self.assertEqual(review_repair.CI_EXCERPT_MAX_LINES, 40)
        path = _write_policy(self, {**BASE_POLICY, 'ci_excerpt_max_lines': 5})
        task = {'task_id': 'a' * 32}
        head = _sha1('ci-validator-head')
        ci_40 = {
            'head': head,
            'check_name': 'check (Linux 3.14)',
            'check_run_id': 7,
            'conclusion': 'failure',
            'excerpt': '\n'.join(f'line {i}' for i in range(1, 41)),
        }
        ci_41 = dict(ci_40, excerpt='\n'.join(f'line {i}' for i in range(1, 42)))
        with patch.object(repair_policy, 'policy_path', return_value=path):
            packet, identity = review_repair._ci_failure(
                task, executor_task='d6-ci', pr_number=42, ci_failure=ci_40)
            self.assertEqual(packet['failed_head'], head)
            self.assertEqual(len(identity), 64)
            with self.assertRaises(LLMToolError) as raised:
                review_repair._ci_failure(
                    task, executor_task='d6-ci', pr_number=42, ci_failure=ci_41)
            self.assertEqual(raised.exception.review_repair_code, 'CI_FAILURE_INVALID')

    def test_invalid_policy_fails_closed(self):
        bad_value = _write_policy(self, {**BASE_POLICY, 'ci_excerpt_max_lines': 0})
        malformed = _write_policy(self, '{not json')
        for path in (bad_value, malformed):
            with self.subTest(path=str(path)), \
                    patch.object(repair_policy, 'policy_path', return_value=path):
                with self.assertRaises(LLMToolError) as raised:
                    task_advance._ci_excerpt(self._run_with_lines(5))
                self.assertEqual(str(raised.exception), 'POLICY_INVALID')
                with self.assertRaises(LLMToolError) as empty_raised:
                    task_advance._ci_excerpt({'output': {}})
                self.assertEqual(str(empty_raised.exception), 'POLICY_INVALID')


D6 = 'policy-caller-d6'
HEAD = _sha1('policy caller head')
CONTRACT = _sha256('policy caller contract')
PM_SESSION = 'pm-policy-caller-fixture'
ROOT = '/nonexistent/policy-caller-d6'
COMMON = '/nonexistent/policy-caller-common'
BRANCH = 'feat/policy-caller'
WRITE = 'docs/policy_note.md'
READ = ['docs/policy_context.md', WRITE]
ACCEPTANCE = ['The note states the policy outcome.']
EGRESS = 'Synthetic fixture prose only.'
DIFF = ('diff --git a/docs/policy_note.md b/docs/policy_note.md\n'
        '--- a/docs/policy_note.md\n+++ b/docs/policy_note.md\n'
        '@@ -1 +1 @@\n-Old note.\n+New note.\n')
CARD = '\nREQUIRED_SKILL_BEGIN path=card\nFixture rules.\nREQUIRED_SKILL_END path=card'
REGISTRATION = SimpleNamespace(
    task_id=D6, trusted_agent_id='claude-developer', session_ref='Policy fixture session',
    native_session_id=None, root=ROOT, common_dir=COMMON, branch=BRANCH, source_sha=HEAD)
SNAPSHOT = SimpleNamespace(head=HEAD, root=ROOT, common_dir=COMMON, branch=BRANCH)
ALLOWED = worker_dispatch.PolicyDecision(
    'claude-developer', True, 'OWNER_POLICY_ALLOWED', _sha256('policy fixture'))


class _GateStore:
    """In-memory Task store applying the real dispatch gate and #2188 rules."""

    def __init__(self):
        self.records = {}

    def _seal(self, record):
        body = {key: value for key, value in record.items() if key != 'record_sha256'}
        record['record_sha256'] = hashlib.sha256(json.dumps(
            body, sort_keys=True, ensure_ascii=True).encode('ascii')).hexdigest()
        self.records[record['task_id']] = record
        return copy.deepcopy(record)

    def add(self, record):
        return self._seal(copy.deepcopy(record))

    def force(self, task_id, **changes):
        record = copy.deepcopy(self.records[task_id])
        record.update(copy.deepcopy(changes))
        return self._seal(record)

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


class _Route:
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


class _Boundary:
    def prepare(self, root, paths, state_dirs=()):
        return ('fixture-policy', True)

    def verify(self, root, paths, state_dirs=(), *, runner):
        return {'verified': True, 'policy_sha256': _sha256('fixture boundary')}


def _clean_probe(source_sha, write_paths=None):
    return source_sha == HEAD


class StartRetriesPolicyCallerTests(unittest.TestCase):
    """R1: both START_RETRY_LIMIT_REACHED gates read start_retries_max capped at 2."""

    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        for patcher in (
                patch.object(cli, '_delegate_d6_binding',
                             return_value=(REGISTRATION, SNAPSHOT)),
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
                patch.object(cli, 'for_platform', return_value=_Boundary()),
                patch.object(cli, '_developer_card_prefix',
                             return_value=(CARD, _sha256(CARD))),
                patch.object(cli, '_record_skill_delivery', return_value=None),
                patch.object(cli, 'resolve_route_max_diff_bytes', return_value=1 << 20),
                patch.object(cli, 'run_task_direct_result_event', return_value=0),
                patch.object(cli, '_task_view',
                             side_effect=lambda record: {'task_id': record['task_id']}),
                patch.object(cli, '_delegate_chain_gate',
                             side_effect=lambda args, store, d6_binding, claim: claim()),
                patch.object(cli, '_check_delegate_repair_cap', return_value=None),
                patch.object(cli.worker_availability, 'quota_refusal', return_value=None),
                patch.object(worker_dispatch, 'evaluate', return_value=ALLOWED),
                patch('llmtool.pm_caller.check_caller', return_value=None),
                redirect_stdout(io.StringIO())):
            stack.enter_context(patcher)
        self.store = _GateStore()

    def _started_task(self):
        task_id = task_store.new_id()
        task = self.store.add({
            'schema_version': 2,
            'task_id': task_id,
            'run_id': task_store.new_id(),
            'pm_session_id': PM_SESSION,
            'state': 'PM_RESPONDED',
            'request': 'Write one short note about the policy outcome.',
            'response': '{"summary":"Write the policy note."}',
            'task_binding': {'task_id': task_id, 'contract_digest': CONTRACT},
            'pm_assignment': {'route': 'claude', 'risk': 'LOW',
                              'goal': 'Write the policy note.', 'acceptance': list(ACCEPTANCE)},
            'developer': None,
            'completion': None,
            'created_at': '2026-11-17T00:00:00Z',
            'updated_at': '2026-11-17T00:00:00Z',
        })
        args = Namespace(
            task_id=task['task_id'], provider='claude', mode='direct', selection_source='PM',
            executor_task=D6, read_path=list(READ), write_path=[WRITE],
            acceptance=list(ACCEPTANCE), egress_basis=EGRESS, owner_ref=None,
            repair_packet=None, context_stdin=False, timeout=None)
        self.assertEqual(cli.run_task_claude_delegate(
            args, store=self.store, route_runner=_Route(error='CLAUDE_CONFIG_INVALID')), 1)
        return self.store.get(task['task_id'])

    def _retry(self, task_id, *, route=None):
        route = _Route() if route is None else route
        status = cli.run_task_retry_start(
            Namespace(task_id=task_id, executor_task=D6, pm_session=PM_SESSION),
            store=self.store, route_runner=route, d6_unchanged=_clean_probe)
        return status, route

    def _assert_command_refused(self, task_id, code):
        before = self.store.get(task_id)
        route = _Route()
        with self.assertRaises(LLMToolError) as raised:
            self._retry(task_id, route=route)
        self.assertTrue(str(raised.exception).startswith(code), str(raised.exception))
        self.assertEqual(route.requests, [])
        self.assertEqual(self.store.get(task_id), before)

    def _assert_claim_refused(self, task_id, code):
        record = self.store.get(task_id)
        failed = record['developer']
        rebuilt = dict(
            failed,
            assignment_id=task_store.new_id(),
            session_id='dev-' + task_store.new_id(),
            state='DEVELOPER_PENDING',
            provider_type=None,
            model=None,
            artifact=None,
            artifact_sha256=None,
            usage=None,
            error_code=None,
            execution=None,
        )
        claim = cli._start_retry_claim(
            Namespace(task_id=task_id, executor_task=D6),
            self.store, record, failed, rebuilt, record['completion'],
            REGISTRATION, SNAPSHOT, None, _clean_probe)
        with self.assertRaises(LLMToolError) as raised:
            claim()
        self.assertTrue(str(raised.exception).startswith(code), str(raised.exception))

    def test_start_retries_max_1_refuses_second_retry_at_both_gates(self):
        path = _write_policy(self, {**BASE_POLICY, 'start_retries_max': 1})
        record = self._started_task()
        with patch.object(repair_policy, 'policy_path', return_value=path):
            status, route = self._retry(record['task_id'],
                                        route=_Route(error='CLAUDE_ENV_INVALID'))
            self.assertEqual(status, 1)
            self.assertEqual(len(route.requests), 1)
            self.assertEqual(len(self.store.get(record['task_id'])['start_failures']), 1)
            self._assert_command_refused(record['task_id'], 'START_RETRY_LIMIT_REACHED')
            self._assert_claim_refused(record['task_id'], 'START_RETRY_LIMIT_REACHED')

    def test_start_retries_max_absent_allows_2_and_refuses_third_at_both_gates(self):
        path = _write_policy(self, BASE_POLICY)
        record = self._started_task()
        with patch.object(repair_policy, 'policy_path', return_value=path):
            for expected_count in (1, 2):
                status, route = self._retry(record['task_id'],
                                            route=_Route(error='CLAUDE_ENV_INVALID'))
                self.assertEqual(status, 1)
                self.assertEqual(len(route.requests), 1)
                self.assertEqual(len(self.store.get(record['task_id'])['start_failures']),
                                 expected_count)
            self._assert_command_refused(record['task_id'], 'START_RETRY_LIMIT_REACHED')
            self._assert_claim_refused(record['task_id'], 'START_RETRY_LIMIT_REACHED')

    def test_start_retries_max_5_is_capped_at_2_at_both_gates(self):
        path = _write_policy(self, {**BASE_POLICY, 'start_retries_max': 5})
        record = self._started_task()
        with patch.object(repair_policy, 'policy_path', return_value=path):
            for expected_count in (1, 2):
                status, route = self._retry(record['task_id'],
                                            route=_Route(error='CLAUDE_ENV_INVALID'))
                self.assertEqual(status, 1)
                self.assertEqual(len(route.requests), 1)
                self.assertEqual(len(self.store.get(record['task_id'])['start_failures']),
                                 expected_count)
            self._assert_command_refused(record['task_id'], 'START_RETRY_LIMIT_REACHED')
            self._assert_claim_refused(record['task_id'], 'START_RETRY_LIMIT_REACHED')

    def test_unreadable_retry_count_still_refuses_limit(self):
        path = _write_policy(self, {**BASE_POLICY, 'start_retries_max': 5})
        record = self._started_task()
        self.store.force(record['task_id'], start_failures='malformed')
        with patch.object(repair_policy, 'policy_path', return_value=path):
            self._assert_command_refused(record['task_id'], 'START_RETRY_LIMIT_REACHED')
            self._assert_claim_refused(record['task_id'], 'START_RETRY_LIMIT_REACHED')

    def test_invalid_policy_fails_closed_at_both_gates(self):
        path = _write_policy(self, {**BASE_POLICY, 'start_retries_max': 0})
        record = self._started_task()
        with patch.object(repair_policy, 'policy_path', return_value=path):
            self._assert_command_refused(record['task_id'], 'POLICY_INVALID')
            self._assert_claim_refused(record['task_id'], 'POLICY_INVALID')


if __name__ == '__main__':
    unittest.main()
