"""#2188 PR 1: the START_RETRY dispatch-gate action (design section 3).

Every decision goes through the real policy reader and gate with the shared
all-ALLOWED test policy; a refusal sets its row explicitly. All text is plain
prose with no credential-shaped strings (Owner #763 A).
"""

import copy
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_MODULE_HOME = tempfile.TemporaryDirectory()
_IMPORT_HOME_PATCH = patch.dict(os.environ, {'HOME': _MODULE_HOME.name})
_IMPORT_HOME_PATCH.start()
from llmtool import start_failure, worker_dispatch
from llmtool import task_submission_cli
from llmtool.task_store import TaskStore, new_id
from llmtool.task_submission_cli import _record
from review_trust_fixture import start_module as _start_trust_fixture
from review_trust_fixture import stop_module as _stop_trust_fixture
import worker_policy_fixture
_IMPORT_HOME_PATCH.stop()

D6 = 'executor-start-retry'
SOURCE = 'a' * 40
CONTRACT = 'f' * 64
NOW = '2026-11-17T00:00:00Z'


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


def claude_developer(state='DEVELOPER_PENDING', **overrides):
    developer = {
        'assignment_id': new_id(), 'selection_source': 'PM', 'state': state,
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
        'timeout_seconds': 600,
    }
    developer.update(overrides)
    return developer


def blocked(developer, code='CLAUDE_CONFIG_INVALID', **overrides):
    value = dict(developer, state='BLOCKED', provider_type='claude-cli',
                 model='claude-test-1', error_code=code)
    value.update(overrides)
    return value


def retry_of(failed, **overrides):
    """A fresh PENDING assignment that keeps D's source, scope and D6."""
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


def waiting_completion():
    return {'state': 'WAITING_EXECUTOR', 'executor_task_id': D6, 'reviewer_task_id': None,
            'evidence': {'source_sha': SOURCE, 'result_head': None,
                         'execution_mode': 'DIRECT_D6'},
            'missing_gates': [], 'pm_dispatch': 'NOT_STARTED', 'pm_response': None,
            'pm_recommendation': None, 'pm_usage': None, 'pm_error_code': None}


class StartRetryGateTests(unittest.TestCase):
    """In-memory gate checks on authorize_task_store_change."""

    def setUp(self):
        self.policy_path = worker_policy_fixture.install(self)
        self.policy = worker_policy_fixture.read(self.policy_path)
        repository = patch.object(worker_dispatch, 'repository_config',
            return_value={'slug': 'davidcatcat/davidcattoolsproject',
                          'dispatch_issue': 289, 'dispatch_evidence_pr': 283})
        repository.start()
        self.addCleanup(repository.stop)

    def _set_state(self, worker, state):
        worker_policy_fixture.set_dispatch(self.policy, worker, state)
        worker_policy_fixture.write(self.policy_path, self.policy)

    def _case(self, *, failed=None, stored=None, audit=None, probe=None):
        failed = blocked(claude_developer()) if failed is None else failed
        current = {'task_id': new_id(), 'record_sha256': 'e' * 64,
                   'task_binding': {'contract_digest': CONTRACT},
                   'developer': failed, 'completion': waiting_completion(),
                   'dispatch_audit': list(audit or [])}
        if stored is not None:
            current['start_failures'] = stored
        retry = retry_of(failed)
        changes = {'developer': retry,
                   'start_failures': list(stored or []) + [archive_entry(current, failed, retry)]}
        if probe is None:
            probe = lambda source_sha, write_paths=None: True
        expected = {'developer': copy.deepcopy(failed),
                    'completion': copy.deepcopy(current['completion']),
                    'd6_unchanged': probe}
        return current, changes, expected

    def _decide(self, current, changes, expected, d6_unchanged=None):
        denial = worker_dispatch.authorize_task_store_change(
            current, changes, expected, d6_unchanged=d6_unchanged)
        return (None if denial is None else denial.dispatch_code), current['dispatch_audit'][-1]

    def test_valid_retry_is_allowed_and_audited_under_the_new_binding(self):
        current, changes, expected = self._case()
        self.assertEqual(worker_dispatch.task_store_attempts(current, changes),
                         [('claude-developer', 'START_RETRY')])
        code, entry = self._decide(current, changes, expected)
        self.assertIsNone(code)
        self.assertEqual(len(current['dispatch_audit']), 1)
        self.assertEqual((entry['worker_key'], entry['action'], entry['decision'],
                          entry['reason_code']),
                         ('claude-developer', 'START_RETRY', 'ALLOWED', 'OWNER_POLICY_ALLOWED'))
        self.assertEqual((entry['assignment_id'], entry['source_sha'], entry['contract_digest']),
                         (changes['developer']['assignment_id'], SOURCE, CONTRACT))
        self.assertNotEqual(entry['assignment_id'], current['developer']['assignment_id'])
        self.assertNotIn('retry_phase', entry)
        self.assertNotIn('authorization_id', entry)

    def test_each_start_failure_code_is_retryable(self):
        for code in sorted(start_failure.START_FAILURE_CODES):
            with self.subTest(code=code):
                current, changes, expected = self._case(
                    failed=blocked(claude_developer(), code=code))
                self.assertIsNone(self._decide(current, changes, expected)[0])

    def _assert_precondition(self, current, changes, expected):
        code, entry = self._decide(current, changes, expected)
        self.assertEqual(code, 'WORKER_START_RETRY_PRECONDITION')
        self.assertEqual((entry['action'], entry['decision'], entry['reason_code']),
                         ('START_RETRY', 'BLOCKED', 'WORKER_START_RETRY_PRECONDITION'))

    def test_unbound_expected_refuses_precondition(self):
        probe = lambda source_sha, write_paths=None: True
        for name, build in (
                ('no expected', lambda failed, completion: None),
                ('no developer', lambda failed, completion: {'completion': completion, 'd6_unchanged': probe}),
                ('no completion', lambda failed, completion: {'developer': failed, 'd6_unchanged': probe}),
                ('other developer', lambda failed, completion: {
                    'developer': dict(failed, error_code='CLAUDE_ENV_INVALID'),
                    'completion': completion, 'd6_unchanged': probe}),
                ('other completion', lambda failed, completion: {
                    'developer': failed, 'completion': dict(completion, state='WAITING_PM'),
                    'd6_unchanged': probe})):
            with self.subTest(case=name):
                current, changes, _expected = self._case()
                self._assert_precondition(current, changes, build(
                    copy.deepcopy(current['developer']),
                    copy.deepcopy(current['completion'])))

    def test_live_d6_unchanged_probe_enforced_by_gate(self):
        # Pass: a probe that returns True allows START_RETRY.
        current, changes, expected = self._case(probe=lambda s, write_paths=None: True)
        self.assertIsNone(self._decide(current, changes, expected)[0])

        # Changed D6: a probe that returns False refuses WORKER_START_RETRY_PRECONDITION.
        current, changes, expected = self._case(probe=lambda s, write_paths=None: False)
        self._assert_precondition(current, changes, expected)

        # Raising probe: refuses WORKER_START_RETRY_PRECONDITION (fail closed).
        def raising_probe(s, write_paths=None):
            raise RuntimeError('probe failed')
        current, changes, expected = self._case(probe=raising_probe)
        self._assert_precondition(current, changes, expected)

        # Unknown / missing probe: refuses WORKER_START_RETRY_PRECONDITION (fail closed).
        current, changes, expected = self._case()
        del expected['d6_unchanged']
        self._assert_precondition(current, changes, expected)

        # None / non-True probe: refuses WORKER_START_RETRY_PRECONDITION.
        for bad_probe in (lambda s, write_paths=None: None,
                          lambda s, write_paths=None: 1,
                          lambda s, write_paths=None: 'true'):
            with self.subTest(bad_probe=bad_probe):
                current, changes, expected = self._case(probe=bad_probe)
                self._assert_precondition(current, changes, expected)

        # Explicit d6_unchanged argument passed to authorize_task_store_change.
        current, changes, expected = self._case()
        del expected['d6_unchanged']
        denial, _ = self._decide(current, changes, expected,
                                 d6_unchanged=lambda s, write_paths=None: True)
        self.assertIsNone(denial)
        denial, entry = self._decide(current, changes, expected,
                                     d6_unchanged=lambda s, write_paths=None: False)
        self.assertEqual(denial, 'WORKER_START_RETRY_PRECONDITION')
        denial, entry = self._decide(current, changes, expected,
                                     d6_unchanged=raising_probe)
        self.assertEqual(denial, 'WORKER_START_RETRY_PRECONDITION')

    def test_non_start_code_and_native_session_refuse_precondition(self):
        for code in sorted(start_failure.NEVER_START_FAILURE_CODES) + ['CLAUDE_TIMEOUT']:
            with self.subTest(code=code):
                self._assert_precondition(*self._case(
                    failed=blocked(claude_developer(), code=code)))
        session = {'status': 'BLOCKED', 'native_session_id': '0123456789abcdef0123',
                   'process_exit_code': None, 'stdout_sha256': None, 'elapsed_ms': 5,
                   'cleanup_status': 'CONFIRMED', 'usage_source': 'UNKNOWN',
                   'source_bytes': None, 'source_file_count': None,
                   'source_manifest_sha256': None}
        self._assert_precondition(*self._case(
            failed=blocked(claude_developer(), execution=session)))
        # The same failure with no process facts is a start failure.
        self.assertIsNone(self._decide(*self._case(failed=blocked(
            claude_developer(), execution=dict(session, native_session_id=None))))[0])

    def test_reused_assignment_or_session_refuses_precondition(self):
        current, changes, expected = self._case()
        failed = current['developer']
        reused = dict(changes['developer'], assignment_id=failed['assignment_id'])
        changes.update(developer=reused, start_failures=[
            archive_entry(current, failed, reused)])
        self._assert_precondition(current, changes, expected)

        current, changes, expected = self._case()
        changes['developer']['session_id'] = current['developer']['session_id']
        self._assert_precondition(current, changes, expected)

        current, changes, expected = self._case()
        changes['developer']['assignment_id'] = 'not-hex'
        changes['start_failures'][-1]['retry_assignment_id'] = 'not-hex'
        self._assert_precondition(current, changes, expected)

        # An assignment archived by an earlier retry is never reused.
        first_failed = blocked(claude_developer())
        failed = blocked(claude_developer())
        earlier = {'failed_developer': first_failed, 'record_sha256': 'd' * 64,
                   'retry_assignment_id': failed['assignment_id'], 'd6_head': SOURCE,
                   'retried_at': NOW, 'pm_session_id': 'pm-start-retry'}
        current, changes, expected = self._case(failed=failed, stored=[earlier])
        changes['developer']['assignment_id'] = first_failed['assignment_id']
        changes['start_failures'][-1]['retry_assignment_id'] = first_failed['assignment_id']
        self._assert_precondition(current, changes, expected)
        current, changes, expected = self._case(failed=failed, stored=[earlier])
        changes['developer']['session_id'] = first_failed['session_id']
        self._assert_precondition(current, changes, expected)

    def test_changed_scope_or_used_assignment_refuses_precondition(self):
        changed = {
            'selection_source': 'USER', 'provider_profile': 'agy',
            'backend_session_semantics': 'NO_NATIVE_SESSION_ID_EXPOSED',
            'source_sha': 'd' * 40, 'source_sha_binding': 'UNVERIFIED_CALLER_INPUT',
            'owner_assignment_ref': ('https://github.com/davidcatcat/davidcattoolsproject/'
                                     'issues/289#issuecomment-1'),
            'executor_task_id': 'other-executor', 'read_paths': ['other.py', 'src.py'],
            'write_paths': ['other.py'], 'acceptance': ['a different outcome'],
            'egress_basis': 'Different plain text.',
            'provider_type': 'claude-cli', 'model': 'claude-test-1', 'artifact': 'diff',
            'execution': {'status': 'BLOCKED'}, 'usage': {'total_tokens': 1},
            'error_code': 'CLAUDE_CONFIG_INVALID',
        }
        for field, value in changed.items():
            with self.subTest(field=field):
                current, changes, expected = self._case()
                changes['developer'][field] = value
                self._assert_precondition(current, changes, expected)
        for field in ('owner_assignment_ref', 'executor_task_id', 'execution'):
            with self.subTest(missing=field):
                current, changes, expected = self._case()
                del changes['developer'][field]
                self._assert_precondition(current, changes, expected)

    def test_completion_or_other_field_in_the_changes_refuses_precondition(self):
        current, changes, expected = self._case()
        changes['completion'] = copy.deepcopy(current['completion'])
        self._assert_precondition(current, changes, expected)

        current, changes, expected = self._case()
        del changes['start_failures']
        self._assert_precondition(current, changes, expected)

    def test_wrong_archive_entry_refuses_precondition(self):
        cases = {
            'other failed developer': lambda current, changes: changes['start_failures'][-1].update(
                failed_developer=dict(current['developer'], session_id='dev-other')),
            'other retry assignment': lambda current, changes: changes['start_failures'][-1].update(
                retry_assignment_id=new_id()),
            'other record digest': lambda current, changes: changes['start_failures'][-1].update(
                record_sha256='0' * 64),
            'two entries': lambda current, changes: changes['start_failures'].append(
                dict(changes['start_failures'][-1])),
            'no entry': lambda current, changes: changes.update(start_failures=[]),
            'not a list': lambda current, changes: changes.update(
                start_failures=changes['start_failures'][-1]),
            'no record digest': lambda current, changes: current.pop('record_sha256'),
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                current, changes, expected = self._case()
                change(current, changes)
                self._assert_precondition(current, changes, expected)

        # A rewritten earlier entry is not an append.
        first_failed = blocked(claude_developer())
        failed = blocked(claude_developer())
        earlier = {'failed_developer': first_failed, 'record_sha256': 'd' * 64,
                   'retry_assignment_id': failed['assignment_id'], 'd6_head': SOURCE,
                   'retried_at': NOW, 'pm_session_id': 'pm-start-retry'}
        current, changes, expected = self._case(failed=failed, stored=[earlier])
        self.assertIsNone(self._decide(current, changes, expected)[0])
        current, changes, expected = self._case(failed=failed, stored=[earlier])
        changes['start_failures'][0] = dict(earlier, d6_head='d' * 40)
        self._assert_precondition(current, changes, expected)

    def test_missing_audit_binding_refuses_precondition(self):
        current, changes, expected = self._case()
        del current['task_binding']
        self._assert_precondition(current, changes, expected)

    def test_third_retry_refuses_limit(self):
        allowed = worker_dispatch.audit_entry(worker_dispatch.PolicyDecision(
            'claude-developer', True, 'OWNER_POLICY_ALLOWED', 'a' * 64), 'START_RETRY')
        refused = worker_dispatch.audit_entry(worker_dispatch.PolicyDecision(
            'claude-developer', False, 'WORKER_START_RETRY_PRECONDITION', 'a' * 64),
            'START_RETRY')
        self.assertEqual(worker_dispatch.MAX_START_RETRIES_PER_TASK, 2)
        first, second, failed = (blocked(claude_developer()) for _ in range(3))
        stored = [
            {'failed_developer': first, 'record_sha256': 'd' * 64,
             'retry_assignment_id': second['assignment_id'], 'd6_head': SOURCE,
             'retried_at': NOW, 'pm_session_id': 'pm-start-retry'},
            {'failed_developer': second, 'record_sha256': 'c' * 64,
             'retry_assignment_id': failed['assignment_id'], 'd6_head': SOURCE,
             'retried_at': NOW, 'pm_session_id': 'pm-start-retry'},
        ]
        current, changes, expected = self._case(
            failed=failed, stored=stored, audit=[dict(allowed), dict(allowed, attempt_id=new_id())])
        code, entry = self._decide(current, changes, expected)
        self.assertEqual(code, 'WORKER_START_RETRY_LIMIT')
        self.assertEqual((entry['action'], entry['decision'], entry['reason_code']),
                         ('START_RETRY', 'BLOCKED', 'WORKER_START_RETRY_LIMIT'))
        # Only ALLOWED entries count toward the cap.
        current, changes, expected = self._case(
            failed=failed, stored=stored[:1],
            audit=[dict(allowed), dict(refused), dict(refused, attempt_id=new_id())])
        self.assertIsNone(self._decide(current, changes, expected)[0])

    def test_paused_policy_refuses_with_its_own_code(self):
        self._set_state('claude-developer', 'PAUSED')
        code, entry = self._decide(*self._case())
        self.assertEqual(code, 'OWNER_PAUSED')
        self.assertEqual((entry['action'], entry['decision'], entry['reason_code']),
                         ('START_RETRY', 'BLOCKED', 'OWNER_PAUSED'))

    def test_non_claude_blocked_to_pending_still_refuses_reassign(self):
        failed = {**blocked(claude_developer()), 'provider_profile': 'agy',
                  'provider_type': 'agy-cli', 'model': 'agy-model',
                  'error_code': 'AGY_DISPATCH_DENIED'}
        current, changes, expected = self._case(failed=failed)
        changes = {'developer': changes['developer']}
        self.assertEqual(worker_dispatch.task_store_attempts(current, changes),
                         [('agy', 'REASSIGN')])
        code, entry = self._decide(current, changes, {'developer': failed})
        self.assertEqual(code, 'WORKER_REASSIGN_PRECONDITION')
        self.assertEqual(entry['action'], 'REASSIGN')

    def test_other_claude_transitions_keep_their_actions(self):
        pending = claude_developer()
        current = {'task_id': new_id(), 'record_sha256': 'e' * 64,
                   'task_binding': {'contract_digest': CONTRACT},
                   'developer': pending, 'completion': waiting_completion(),
                   'dispatch_audit': []}
        replacement = retry_of(pending)
        # PENDING -> new PENDING stays a bound REASSIGN.
        self.assertEqual(worker_dispatch.task_store_attempts(
            current, {'developer': replacement}), [('claude-developer', 'REASSIGN')])
        self.assertIsNone(worker_dispatch.authorize_task_store_change(
            current, {'developer': replacement}, {'developer': pending}))
        # BLOCKED -> DISPATCHING of the same assignment stays a refused RETRY.
        failed = blocked(claude_developer())
        current = dict(current, developer=failed, dispatch_audit=[])
        redispatch = dict(failed, state='DEVELOPER_DISPATCHING', error_code=None)
        self.assertEqual(worker_dispatch.task_store_attempts(
            current, {'developer': redispatch}), [('claude-developer', 'RETRY')])
        denial = worker_dispatch.authorize_task_store_change(
            current, {'developer': redispatch}, {'developer': failed})
        self.assertEqual(denial.dispatch_code, 'WORKER_REPLAY_FORBIDDEN')
        # PENDING -> DISPATCHING stays the initial provider binding, a DISPATCH.
        current = dict(current, developer=pending, dispatch_audit=[])
        dispatching = dict(pending, state='DEVELOPER_DISPATCHING',
                           provider_type='claude-cli', model='claude-test-1')
        self.assertEqual(worker_dispatch.task_store_attempts(
            current, {'developer': dispatching}), [('claude-developer', 'DISPATCH')])

    def test_audit_entry_accepts_start_retry_without_a_phase(self):
        decision = worker_dispatch.PolicyDecision(
            'claude-developer', True, 'OWNER_POLICY_ALLOWED', 'a' * 64)
        self.assertIn('START_RETRY', worker_dispatch.AUDIT_ACTIONS)
        self.assertEqual(worker_dispatch.audit_entry(decision, 'START_RETRY')['action'],
                         'START_RETRY')
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied):
            worker_dispatch.audit_entry(decision, 'START_RETRY', retry_phase='TRANSITION')


class StartRetryStoreFlowTests(unittest.TestCase):
    """The gate through a real TaskStore write, then the following DISPATCH."""

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

    def _failed_start_task(self):
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
        registration = SimpleNamespace(
            task_id=D6, root='/registered/root', common_dir='/repo/.git',
            branch='task-branch', trusted_agent_id='claude-developer',
            session_ref='session-ref', native_session_id=None)
        callback = task_submission_cli._direct_result_callback(
            task, registration, SOURCE, test_policy='LOCAL')
        completion = waiting_completion()
        completion['evidence'] = dict(callback['evidence'])
        pending = claude_developer(direct_result_callback=callback)
        self.store.d6_bind_compare_and_update(
            task_id, executor_task_id=D6,
            expected={'schema_version': 2, 'state': 'PM_RESPONDED', 'developer': None,
                      'completion': None},
            developer=pending, completion=completion)
        dispatching = dict(pending, state='DEVELOPER_DISPATCHING',
                           provider_type='claude-cli', model='claude-test-1')
        self.store.update(task_id, developer=dispatching)
        self.store.update(task_id, developer=blocked(dispatching))
        return task_id

    def test_store_retry_is_audited_then_dispatch_is_found_by_the_adapter_gate(self):
        task_id = self._failed_start_task()
        current = self.store.get(task_id)
        failed = current['developer']
        self.assertTrue(start_failure.record_checks(current).start_failure)
        retry = retry_of(failed)
        saved = self.store.d6_bind_compare_and_update(
            task_id, executor_task_id=D6,
            expected={'schema_version': 2, 'state': 'PM_RESPONDED', 'developer': failed,
                      'completion': current['completion'],
                      'd6_unchanged': lambda s, write_paths=None: True},
            developer=retry, start_failures=[archive_entry(current, failed, retry)])
        entry = saved['dispatch_audit'][-1]
        self.assertEqual((entry['action'], entry['decision'], entry['assignment_id']),
                         ('START_RETRY', 'ALLOWED', retry['assignment_id']))
        self.assertEqual(saved['developer'], retry)
        self.assertEqual(saved['completion'], current['completion'])
        self.assertEqual(saved['start_failures'][0]['failed_developer'], failed)

        dispatching = dict(retry, state='DEVELOPER_DISPATCHING',
                           provider_type='claude-cli', model='claude-test-1')
        saved = self.store.update(task_id, developer=dispatching)
        transition = saved['dispatch_audit'][-1]
        self.assertEqual((transition['action'], transition['decision'],
                          transition['assignment_id']),
                         ('DISPATCH', 'ALLOWED', retry['assignment_id']))
        decision = worker_dispatch.authorize_dispatch(
            self.store, task_id, 'claude-developer', 'DISPATCH')
        self.assertTrue(decision.allowed)
        adapter = self.store.get(task_id)['dispatch_audit'][-1]
        self.assertEqual((adapter['decision'], adapter['authorization_id'],
                          adapter['assignment_id']),
                         ('ALLOWED', transition['attempt_id'], retry['assignment_id']))

    def test_store_refusal_keeps_its_audited_denial(self):
        task_id = self._failed_start_task()
        current = self.store.get(task_id)
        failed = current['developer']
        retry = retry_of(failed, write_paths=['other.py'], read_paths=['other.py', 'src.py'])
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
            self.store.d6_bind_compare_and_update(
                task_id, executor_task_id=D6,
                expected={'developer': failed, 'completion': current['completion'],
                          'd6_unchanged': lambda s, write_paths=None: True},
                developer=retry, start_failures=[archive_entry(current, failed, retry)])
        self.assertEqual(caught.exception.dispatch_code, 'WORKER_START_RETRY_PRECONDITION')
        saved = self.store.get(task_id)
        self.assertEqual(saved['developer'], failed)
        self.assertNotIn('start_failures', saved)
        self.assertEqual((saved['dispatch_audit'][-1]['action'],
                          saved['dispatch_audit'][-1]['decision']),
                         ('START_RETRY', 'BLOCKED'))


if __name__ == '__main__':
    unittest.main()
