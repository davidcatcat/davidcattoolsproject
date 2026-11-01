import io
import json
import unittest
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import review_dispatch, task_completion, task_submission_cli, worker_dispatch
from llmtool.developer_routes import RouteError
from llmtool.developer_routes.agy import owner_assignment_gate
from llmtool.errors import LLMToolError


class AgyRouteWiringTests(unittest.TestCase):
    def _run_agy_delegate(self, route_error=None, *, diff_bytes=b'diff --git a/src.py b/src.py\n',
                          decisions=None, repair_packet=None, usage=None,
                          binding_changes=False, fail_success_persist=False,
                          read_paths=None, changed_paths=None, request='AGY goal',
                          goal='AGY goal'):
        task_id = 'a' * 32
        task = {
            'schema_version': 2, 'task_id': task_id, 'run_id': 'b' * 32,
            'state': 'PM_RESPONDED', 'response': 'PM response', 'request': request,
            'task_binding': {'contract_digest': 'c' * 64},
            'pm_assignment': {'route': 'agy', 'risk': 'LOW', 'goal': goal,
                              'acceptance': ['the result is recorded']},
            'pm_route_binding': {'type': 'openai-compatible', 'model': 'pm-model',
                                 'base_url': None},
            'developer': None, 'completion': None,
        }

        class Store:
            def __init__(self):
                self.task = dict(task)

            def get(self, _task_id):
                return dict(self.task)

            def compare_and_update(self, _task_id, *, expected, **updates):
                self.task.update(updates)
                return dict(self.task)

            def update(self, _task_id, **updates):
                if (fail_success_persist and
                        updates.get('developer', {}).get('state') == 'DEVELOPER_RESPONDED'):
                    raise RuntimeError('simulated persistence rejection')
                self.task.update(updates)
                return dict(self.task)

        store = Store()
        snapshot = SimpleNamespace(root='/registered/root', common_dir='/repo/.git',
                                   branch='task-branch', head='d' * 40, clean=True)
        registration = SimpleNamespace(task_id='executor-d6', root=snapshot.root,
            common_dir=snapshot.common_dir, branch=snapshot.branch, result_sha=snapshot.head,
            trusted_agent_id='codex', session_ref='session-ref', native_session_id=None)
        callback = {'state': 'WAITING_EXECUTOR', 'executor_task_id': registration.task_id}
        usage = ({'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5}
                 if usage is None else usage)
        result = SimpleNamespace(session_id='conversation_12345678', exit_code=0,
            stdout_sha256='e' * 64, elapsed_ms=42, cleanup_status='CONFIRMED', usage=usage)
        allowed = SimpleNamespace(allowed=True, reason_code='OWNER_ASSIGNMENT_MATCHED')
        denied = SimpleNamespace(allowed=False, reason_code='OWNER_ASSIGNMENT_MISMATCH')
        gate_decisions = iter(decisions or (allowed, allowed))
        binding_results = iter(((registration, snapshot), (registration, snapshot),
                                (SimpleNamespace(**{**registration.__dict__,
                                                    'result_sha': 'f' * 40}), snapshot)))
        inbox_events = []
        prompts = []

        def route_runner(_route, _request, *, gate, **_kwargs):
            prompts.append(_request['prompt'])
            gate_result = gate()
            if gate_result is False or getattr(gate_result, 'allowed', False) is not True:
                raise RouteError('AGY_DISPATCH_DENIED',
                                 blocked_reason='OWNER_DISPATCH_DENIED')
            if route_error is not None:
                raise route_error
            return result

        def direct_event(event_task_id, executor_task_id, *, store, reviewer_adapter=None):
            saved = store.get(event_task_id)['developer']
            self.assertEqual(saved['state'], 'DEVELOPER_RESPONDED')
            self.assertEqual(saved['direct_result_callback'], callback)
            inbox_events.append({'task_id': event_task_id, 'executor_task_id': executor_task_id})
            return 0

        args = Namespace(task_id=task_id, provider='agy', mode='direct',
            owner_ref='https://github.com/davidcatcat/davidcattoolsproject/issues/289#issuecomment-1',
            context_stdin=False, selection_source='PM', acceptance=['the result is recorded'],
            egress_basis='Approved non-sensitive Task context.', executor_task='executor-d6',
            read_path=['src.py'], write_path=['src.py'], repair_packet=repair_packet)
        boundary = SimpleNamespace(prepare=lambda *_args: ((), 'f' * 64),
                                   verify=lambda *_args, **_kwargs: {'verified': True,
                                                                     'policy_sha256': 'f' * 64})
        with patch.object(task_submission_cli.worker_dispatch, 'evaluate',
                          side_effect=lambda *_args: next(gate_decisions, denied)), \
             patch.object(task_submission_cli.task_completion, 'validate_task_binding'), \
             patch.object(task_submission_cli.task_completion, 'collect_git_snapshot', return_value=snapshot), \
             patch.object(task_submission_cli.task_completion, 'bind_executor_task',
                          side_effect=(lambda *_args, **_kwargs:
                              next(binding_results) if binding_changes else
                              (registration, snapshot))), \
             patch.object(task_submission_cli.task_completion, 'read_d6_registration',
                          return_value=registration), \
             patch.object(task_submission_cli.task_completion, '_worktree_diff',
                          return_value=(changed_paths or ['src.py'], diff_bytes)), \
             patch.object(task_submission_cli, '_assignment_paths',
                          return_value=(read_paths or ['src.py'], ['src.py'])), \
             patch.object(task_submission_cli, '_direct_result_callback', return_value=callback), \
             patch.object(task_submission_cli, '_remote_receipt_test_policy', return_value='LOCAL'), \
             patch.object(task_submission_cli, 'load_route_config', return_value={
                 'routes': {'agy': {'executable': 'agy', 'default_model': 'gemini-test',
                                    'default_effort': 'high', 'denied_dirs': ['.gemini'],
                                    'state_dirs': {'policy': 'private-temp-only'}}}}), \
             patch.object(task_submission_cli.shutil, 'which', return_value='/usr/bin/agy'), \
             patch.object(task_submission_cli, 'for_platform', return_value=boundary), \
             patch.object(task_submission_cli, '_task_view', return_value={}), \
             patch.object(task_submission_cli, 'run_task_direct_result_event',
                          side_effect=direct_event):
            status = task_submission_cli.run_task_agy_delegate(
                args, store=store, route_runner=route_runner)
        return status, store.task, inbox_events, prompts

    def test_record_pm_accepts_agy_recommendation(self):
        response = json.dumps({
            'schema_version': 1, 'summary': 'Assign the bounded implementation.',
            'developer_assignment': {'route': 'agy', 'risk': 'LOW',
                                    'goal': 'Wire AGY direct dispatch.',
                                    'acceptance': ['Preserve the owner gate.']},
        })
        envelope = json.dumps({'request': 'Wire AGY.', 'response': response})
        _request, _response, assignment = task_submission_cli._read_pm_record_input(
            io.StringIO(envelope))
        self.assertEqual(assignment['route'], 'agy')

    def test_dispatch_key_and_missing_owner_ref_are_fail_closed(self):
        self.assertEqual(worker_dispatch.worker_key_for('agy'), 'agy')

        class UnreadStore:
            def get(self, _task_id):
                raise AssertionError('store must not be read before owner-ref validation')

        args = Namespace(provider='agy', mode='direct', owner_ref=None)
        with self.assertRaises(LLMToolError):
            task_submission_cli.run_task_delegate(args, store=UnreadStore())

    def test_agy_developer_routes_to_claude_reviewer(self):
        task = {
            'task_id': 'a' * 32, 'run_id': 'b' * 32,
            'task_binding': {'contract_digest': 'c' * 64},
            'completion': {'executor_task_id': 'executor-d6',
                           'evidence': {'common_dir': '/repo/.git'}},
            'developer': {'provider_profile': 'agy'},
        }
        executor = SimpleNamespace(root='/repo/worktree', common_dir='/repo/.git',
                                   branch='task-branch', result_sha='d' * 40)
        snapshot = SimpleNamespace(clean=True, head='d' * 40, root='/repo/worktree',
                                   common_dir='/repo/.git', branch='task-branch')
        pull = {'number': 7, 'head': {'sha': 'd' * 40}, 'base': {'sha': 'e' * 40}}
        with patch.object(task_completion, 'validate_task_binding'), \
                patch.object(task_completion, 'read_d6_registration', return_value=executor), \
                patch.object(task_completion, 'collect_git_snapshot', return_value=snapshot), \
                patch.object(task_completion, '_github_get', return_value=pull):
            result = review_dispatch._preflight(
                None, 'a' * 32, 'executor-d6', 7, 1, task=task)
        self.assertEqual(result[-1], 'claude-reviewer')

    def test_agy_success_records_response_and_emits_direct_result_event(self):
        status, task, inbox_events, _prompts = self._run_agy_delegate()
        self.assertEqual(status, 0)
        self.assertEqual(task['developer']['state'], 'DEVELOPER_RESPONDED')
        self.assertEqual(task['developer']['provider_profile'], 'agy')
        self.assertEqual(task['developer']['direct_result_callback']['state'], 'WAITING_EXECUTOR')
        self.assertEqual(inbox_events, [{'task_id': 'a' * 32,
                                         'executor_task_id': 'executor-d6'}])

    def test_agy_prompt_contains_scope_but_no_source_file_payload(self):
        _status, _task, _inbox_events, prompts = self._run_agy_delegate()
        prompt = prompts[0]
        self.assertNotIn('SOURCE_FILES_JSON', prompt)
        self.assertNotIn('source_files', prompt)
        self.assertIn('Read the authorized files from the worktree yourself', prompt)
        packet = json.loads(prompt.split('TASK_JSON=', 1)[1])
        self.assertEqual(packet['task_contract_digest'], 'c' * 64)
        self.assertEqual(packet['source_sha'], 'd' * 40)
        self.assertEqual(packet['goal'], 'AGY goal')
        self.assertEqual(packet['acceptance'], ['the result is recorded'])
        self.assertEqual(packet['read_paths'], ['src.py'])
        self.assertEqual(packet['write_paths'], ['src.py'])

    def test_credential_shaped_read_path_does_not_block_dispatch(self):
        fixture_path = 'fixtures/' + 'gh' + 'p_' + 'A' * 36
        status, task, _inbox_events, prompts = self._run_agy_delegate(
            read_paths=[fixture_path])
        self.assertEqual(status, 0)
        self.assertEqual(task['developer']['state'], 'DEVELOPER_RESPONDED')
        packet = json.loads(prompts[0].split('TASK_JSON=', 1)[1])
        self.assertEqual(packet['read_paths'], [fixture_path])

    def test_credential_material_in_pm_request_is_still_refused(self):
        fake_pat = 'gh' + 'p_' + 'A' * 36
        with self.assertRaisesRegex(LLMToolError, 'recognizable credential material'):
            self._run_agy_delegate(request=f'Use token {fake_pat} for this task')

    def test_out_of_scope_write_is_still_rejected(self):
        status, task, inbox_events, _prompts = self._run_agy_delegate(
            changed_paths=['outside.py'])
        self.assertEqual(status, 1)
        self.assertEqual(task['developer']['state'], 'BLOCKED')
        self.assertEqual(task['developer']['error_code'], 'AGY_WRITE_SCOPE_VIOLATION')
        self.assertEqual(inbox_events, [])

    def test_agy_unknown_usage_is_not_persisted_as_cli_reported(self):
        status, task, _inbox_events, _prompts = self._run_agy_delegate(
            usage={'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5,
                    'vendor_metric': 99})
        self.assertEqual(status, 0)
        self.assertIsNone(task['developer']['usage'])
        self.assertEqual(task['developer']['execution']['usage_source'], 'UNKNOWN')

    def test_agy_success_persistence_rejection_is_recorded_unknown(self):
        status, task, inbox_events, _prompts = self._run_agy_delegate(
            fail_success_persist=True)
        self.assertEqual(status, 1)
        self.assertEqual(task['developer']['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(task['developer']['error_code'],
                         'AGY_RESULT_PERSISTENCE_REJECTED')
        self.assertIsNone(task['developer']['artifact'])
        self.assertEqual(inbox_events, [])

    def test_agy_route_gate_binding_failure_has_binding_error(self):
        status, task, inbox_events, _prompts = self._run_agy_delegate(binding_changes=True)
        self.assertEqual(status, 1)
        self.assertEqual(task['developer']['error_code'], 'AGY_D6_BINDING_CHANGED')
        self.assertEqual(inbox_events, [])

    def test_agy_isolation_failure_records_specific_boundary_reason(self):
        status, task, inbox_events, _prompts = self._run_agy_delegate(
            RouteError('BLOCKED_WRITE_ISOLATION', blocked_reason='BOUNDARY_UNVERIFIED'))
        self.assertEqual(status, 1)
        self.assertEqual(task['developer']['state'], 'BLOCKED')
        self.assertEqual(task['developer']['error_code'], 'BLOCKED_WRITE_ISOLATION')
        self.assertEqual(task['developer']['blocked_reason'], 'BOUNDARY_UNVERIFIED')
        self.assertEqual(inbox_events, [])

    def test_agy_quota_failure_records_progress_without_emitting_result_event(self):
        progress = {'state': 'QUOTA_EXHAUSTED', 'head_sha': 'd' * 40,
                    'changed_files': ['src.py'], 'unfinished_checks': ['UNKNOWN'],
                    'session_id': 'conversation_12345678'}
        status, task, inbox_events, _prompts = self._run_agy_delegate(
            RouteError('AGY_QUOTA_EXHAUSTED', progress=progress))
        self.assertEqual(status, 1)
        self.assertEqual(task['developer']['progress'], progress)
        self.assertEqual(task['developer']['error_code'], 'AGY_QUOTA_EXHAUSTED')
        self.assertEqual(inbox_events, [])

    def test_agy_owner_gate_recheck_failure_is_dispatch_denied(self):
        allowed = SimpleNamespace(allowed=True, reason_code='OWNER_ASSIGNMENT_MATCHED')
        denied = SimpleNamespace(allowed=False, reason_code='OWNER_ASSIGNMENT_MISMATCH')
        status, task, inbox_events, _prompts = self._run_agy_delegate(
            decisions=(allowed, denied))
        self.assertEqual(status, 1)
        self.assertEqual(task['developer']['error_code'], 'AGY_DISPATCH_DENIED')
        self.assertEqual(task['developer']['blocked_reason'], 'OWNER_DISPATCH_DENIED')
        self.assertEqual(inbox_events, [])

    def test_agy_route_gate_denies_unknown_allowed_reason(self):
        initial = SimpleNamespace(allowed=True, reason_code='OWNER_POLICY_ALLOWED')
        unknown = SimpleNamespace(allowed=True, reason_code='UNRECOGNIZED_REASON')
        status, task, inbox_events, _prompts = self._run_agy_delegate(
            decisions=(initial, unknown))
        self.assertEqual(status, 1)
        self.assertEqual(task['developer']['error_code'], 'AGY_DISPATCH_DENIED')
        self.assertEqual(task['developer']['blocked_reason'], 'OWNER_DISPATCH_DENIED')
        self.assertEqual(inbox_events, [])

    def test_agy_route_gate_accepts_policy_allowed_and_assignment_match_only(self):
        for reason in ('OWNER_POLICY_ALLOWED', 'OWNER_ASSIGNMENT_MATCHED'):
            with self.subTest(reason=reason):
                decision = SimpleNamespace(allowed=True, reason_code=reason)
                status, task, inbox_events, _prompts = self._run_agy_delegate(
                    decisions=(decision, decision))
                self.assertEqual(status, 0)
                self.assertEqual(task['developer']['state'], 'DEVELOPER_RESPONDED')
                self.assertEqual(len(inbox_events), 1)

    def test_agy_adapter_gate_denies_paused_assignment_required_and_unknown(self):
        for decision in (
                SimpleNamespace(allowed=False, reason_code='OWNER_PAUSED'),
                SimpleNamespace(allowed=False, reason_code='OWNER_ASSIGNMENT_REQUIRED'),
                SimpleNamespace(allowed=True, reason_code='UNRECOGNIZED_REASON')):
            with self.subTest(reason=decision.reason_code), \
                    patch.object(worker_dispatch, 'evaluate', return_value=decision):
                self.assertFalse(owner_assignment_gate('a' * 32, 'owner-ref')())

    def test_agy_adapter_gate_accepts_only_recognized_allowed_reasons(self):
        for reason in ('OWNER_POLICY_ALLOWED', 'OWNER_ASSIGNMENT_MATCHED'):
            decision = SimpleNamespace(allowed=True, reason_code=reason)
            with self.subTest(reason=reason), \
                    patch.object(worker_dispatch, 'evaluate', return_value=decision):
                self.assertIs(owner_assignment_gate('a' * 32, 'owner-ref')(), decision)

    def test_agy_large_diff_has_distinct_output_error(self):
        status, task, inbox_events, _prompts = self._run_agy_delegate(diff_bytes=b'x' * (64 * 1024 + 1))
        self.assertEqual(status, 1)
        self.assertEqual(task['developer']['error_code'], 'AGY_OUTPUT_TOO_LARGE')
        self.assertEqual(inbox_events, [])

    def test_agy_repair_prompt_contains_untrusted_reference(self):
        packet = {'task': 'task-1', 'pr': 7, 'executor_task': 'executor-d6',
            'failed_head': 'd' * 40, 'version': 1, 'verdict_comment_id': 9,
            'findings_count': 1, 'reviewer_text_excerpt_untrusted_data': 'Correct the import.'}
        _status, _task, _events, prompts = self._run_agy_delegate(repair_packet=packet)
        self.assertIn('untrusted_repair_reference', prompts[0])
        self.assertIn('Correct the import.', prompts[0])


if __name__ == '__main__':
    unittest.main()
