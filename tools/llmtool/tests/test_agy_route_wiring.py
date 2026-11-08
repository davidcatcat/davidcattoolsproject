import contextlib
import io
import json
import dataclasses
import hashlib
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from llmtool import (review_dispatch, skill_ledger, skill_manifest, task_completion,
                     task_submission_cli, worker_dispatch)
from llmtool.developer_routes import RouteError
from llmtool.developer_routes.agy import owner_assignment_gate
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore


import review_trust_fixture


_SKILL_MANIFEST = {'commit': 'd' * 40, 'role': 'developer', 'gate': 'develop',
                   'entries': [], 'manifest_sha256': '7' * 64}
_SKILL_FILES = [('AGENTS.md', 'AGENTS repository rules.\n'),
                ('skills/ai-team-delivery/SKILL.md', 'Delivery skill rules.\n')]


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


class AgyRouteWiringTests(unittest.TestCase):
    def _run_agy_delegate(self, route_error=None, *, diff_bytes=b'diff --git a/src.py b/src.py\n',
                          decisions=None, repair_packet=None, usage=None,
                          binding_changes=False, fail_success_persist=False,
                          read_paths=None, changed_paths=None, request='AGY goal',
                          goal='AGY goal', session_id='conversation_12345678',
                          bind_result='BOUND', initial_native_session_id=None,
                          bound_registration_mutation=False, audit=None,
                          store=None, verified_model='gemini-test', build_manifest=None,
                          read_manifest_files=None, ledger_record=None,
                          owner_ref=('https://github.com/davidcatcat/davidcattoolsproject/'
                                     'issues/289#issuecomment-1')):
        if build_manifest is None:
            build_manifest = Mock(return_value=_SKILL_MANIFEST)
        if read_manifest_files is None:
            read_manifest_files = Mock(return_value=_SKILL_FILES)
        if ledger_record is None:
            ledger_record = Mock(return_value='receipt')
        store_writes = [] if audit is None else audit.setdefault('store_writes', [])
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
                store_writes.append(updates)
                self.task.update(updates)
                return dict(self.task)

            def d6_bind_compare_and_update(self, task_id, *, executor_task_id,
                                           expected, **updates):
                return self.compare_and_update(task_id, expected=expected, **updates)

            def update(self, _task_id, **updates):
                if (fail_success_persist and
                        updates.get('developer', {}).get('state') == 'DEVELOPER_RESPONDED'):
                    raise RuntimeError('simulated persistence rejection')
                store_writes.append(updates)
                self.task.update(updates)
                return dict(self.task)

        if store is None:
            store = Store()
        else:
            record = task_submission_cli._record(
                task_id, task['run_id'], 'pm-' + 'e' * 32, 'pm', request,
                state='PM_RESPONDED',
                provider_type='openai-compatible',
                model='pm-model',
                backend='NO_NATIVE_SESSION_ID_EXPOSED',
                request_sha=hashlib.sha256(request.encode('utf-8')).hexdigest(),
            )
            record.update(
                response=task['response'],
                pm_route_binding=task['pm_route_binding'],
                pm_assignment={'schema_version': 1, **task['pm_assignment']},
            )
            task = store.create(record)
        snapshot = SimpleNamespace(root='/registered/root', common_dir='/repo/.git',
                                   branch='task-branch', head='d' * 40, clean=True)
        registration = task_completion.D6Registration(
            task_id='executor-d6', trusted_agent_id='codex', session_ref='session-ref',
            root=snapshot.root, common_dir=snapshot.common_dir, branch=snapshot.branch,
            source_sha=snapshot.head, result_sha=snapshot.head, state='ACTIVE',
            lock_state='HELD', device=1, inode=2, uid=3,
            native_session_id=initial_native_session_id, github_login=None,
            env2_pass_marker=None, env2_approved_tooling_sha=snapshot.head)
        callback = task_submission_cli._direct_result_callback(
            task, registration, snapshot.head, test_policy='LOCAL')
        usage = ({'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5}
                 if usage is None else usage)
        result = SimpleNamespace(session_id=session_id, model=verified_model, exit_code=0,
            stdout_sha256='e' * 64, elapsed_ms=42, cleanup_status='CONFIRMED', usage=usage)
        allowed = SimpleNamespace(allowed=True, reason_code='OWNER_ASSIGNMENT_MATCHED')
        denied = SimpleNamespace(allowed=False, reason_code='OWNER_ASSIGNMENT_MISMATCH')
        gate_decisions = iter(decisions or (allowed, allowed))
        changed_registration = dataclasses.replace(registration, result_sha='f' * 40)
        binding_results = iter(((registration, snapshot), (registration, snapshot),
                                (changed_registration, snapshot)))
        bind_executor_calls = []
        registration_reads = []
        diff_calls = []
        inbox_events = []
        prompts = []
        if audit is not None:
            audit['prompts'] = prompts
        bind_called = False

        def read_registration(*_args, **_kwargs):
            registration_reads.append(True)
            if (bind_called and initial_native_session_id is None and bind_result == 'BOUND' and
                    type(session_id) is str and session_id not in ('', 'UNKNOWN')):
                native = task_completion.D6Registration(
                    **{**registration.__dict__, 'native_session_id': session_id})
                if bound_registration_mutation:
                    return dataclasses.replace(native, branch='changed-branch')
                return native
            return registration

        def bind_native_session(*_args, **_kwargs):
            nonlocal bind_called
            bind_called = True
            return bind_result

        def route_runner(_route, _request, *, gate, **_kwargs):
            prompts.append(_request['prompt'])
            gate_result = gate()
            if gate_result is False or getattr(gate_result, 'allowed', False) is not True:
                raise RouteError('AGY_DISPATCH_DENIED',
                                 blocked_reason='OWNER_DISPATCH_DENIED')
            if route_error is not None:
                raise route_error
            if audit is not None:
                audit['provider_results'] = audit.get('provider_results', 0) + 1
            return result

        def direct_event(event_task_id, executor_task_id, *, store, reviewer_adapter=None):
            saved_task = store.get(event_task_id)
            saved = saved_task['developer']
            self.assertEqual(saved['state'], 'DEVELOPER_RESPONDED')
            completion_evidence = saved_task['completion']['evidence']
            callback_evidence = saved['direct_result_callback']['evidence']
            for key, value in completion_evidence.items():
                self.assertEqual(callback_evidence[key], value)
            self.assertEqual(callback_evidence['executor_task_id'], 'executor-d6')
            self.assertEqual(callback_evidence['verification_state'], 'NOT_STARTED')
            self.assertEqual(callback_evidence['test_policy'], 'LOCAL')
            self.assertEqual(saved['direct_result_callback']['executor_task_id'], 'executor-d6')
            inbox_events.append({'task_id': event_task_id, 'executor_task_id': executor_task_id})
            return 0

        args = Namespace(task_id=task_id, provider='agy', mode='direct',
            owner_ref=owner_ref,
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
                              bind_executor_calls.append(True) or
                              (next(binding_results) if binding_changes else
                               (registration, snapshot)))), \
             patch.object(task_submission_cli.task_completion, 'read_d6_registration',
                          side_effect=read_registration), \
             patch.object(task_submission_cli, '_bind_codex_native_session',
                          side_effect=bind_native_session), \
             patch.object(task_submission_cli.task_completion, '_worktree_diff',
                          side_effect=lambda *_args, **_kwargs:
                              diff_calls.append(True) or
                              (changed_paths or ['src.py'], diff_bytes)), \
             patch.object(task_submission_cli, '_assignment_paths',
                          return_value=(read_paths or ['src.py'], ['src.py'])), \
             patch.object(task_submission_cli, '_remote_receipt_test_policy', return_value='LOCAL'), \
             patch.object(task_submission_cli, 'load_route_config', return_value={
                 'routes': {'agy': {'executable': 'agy', 'default_model': 'gemini-test',
                                    'default_effort': 'high', 'denied_dirs': ['.gemini'],
                                    'state_dirs': {'policy': 'private-temp-only'}}}}), \
             patch.object(task_submission_cli.shutil, 'which', return_value='/usr/bin/agy'), \
             patch.object(task_submission_cli, 'for_platform', return_value=boundary), \
             patch.object(task_submission_cli, '_task_view', return_value={}), \
             patch.object(task_submission_cli.skill_manifest, 'build_manifest', build_manifest), \
             patch.object(task_submission_cli.skill_manifest, 'read_manifest_files',
                          read_manifest_files), \
             patch.object(task_submission_cli.skill_ledger, 'record', ledger_record), \
             patch.object(task_submission_cli, 'run_task_direct_result_event',
                          side_effect=direct_event):
            status = task_submission_cli.run_task_agy_delegate(
                args, store=store, route_runner=route_runner)
        if audit is not None:
            audit.update(bind_executor_calls=len(bind_executor_calls),
                         registration_reads=len(registration_reads),
                         diff_calls=len(diff_calls), bind_calls=int(bind_called),
                         result_events=len(inbox_events))
        return status, store.get(task_id), inbox_events, prompts

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
            def __getattr__(self, name):
                raise AssertionError('store must not be read before owner-ref validation')

        # #785 M18: a missing ref is optional, but a supplied malformed ref is
        # still refused before any store read.
        for owner_ref in ('https://example.invalid/x', 7):
            with self.subTest(owner_ref=owner_ref):
                args = Namespace(provider='agy', mode='direct', owner_ref=owner_ref,
                                 task_id='a' * 32, background=False,
                                 acceptance=['the result is recorded'])
                with self.assertRaises(LLMToolError) as caught:
                    task_submission_cli.run_task_delegate(args, store=UnreadStore())
                self.assertEqual(
                    str(caught.exception),
                    '--provider agy requires a valid --owner-ref before Task mutation')
                with self.assertRaises(LLMToolError) as caught:
                    task_submission_cli.run_task_agy_delegate(args, store=UnreadStore())
                self.assertEqual(
                    str(caught.exception),
                    '--provider agy requires a valid --owner-ref before Task mutation')

    def test_agy_delegate_without_owner_ref_reaches_the_store(self):
        class SentinelStore:
            def __init__(self):
                self.gets = []

            def task_ids(self):
                return []

            def get(self, task_id):
                self.gets.append(task_id)
                raise LLMToolError('SENTINEL_STORE_GET')

        store = SentinelStore()
        args = Namespace(provider='agy', mode='direct', owner_ref=None, task_id='a' * 32,
                         background=False, context_stdin=False,
                         acceptance=['the result is recorded'])
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli.run_task_delegate(args, store=store)
        self.assertEqual(str(caught.exception), 'SENTINEL_STORE_GET')
        self.assertEqual(store.gets, ['a' * 32])

    def test_claude_delegate_without_owner_ref_is_still_refused_before_store_read(self):
        class UnreadStore:
            def __getattr__(self, name):
                raise AssertionError('store must not be read before owner-ref validation')

        args = Namespace(provider='claude', mode='direct', owner_ref=None, task_id='a' * 32,
                         background=False, acceptance=['the result is recorded'])
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli.run_task_delegate(args, store=UnreadStore())
        self.assertEqual(str(caught.exception), '--provider claude requires --owner-ref')

    def _fixture_policy(self, agy_row):
        """Write a self-contained v1 policy fixture; never the live file."""
        evidence = 'https://github.com/davidcatcat/davidcattoolsproject/issues/289'
        workers = {
            key: {'dispatch': 'PAUSED', 'decided_by': 'Owner', 'decided_at': '2026-11-08',
                  'reason': 'test fixture', 'evidence': evidence}
            for key in worker_dispatch.WORKERS
        }
        workers['company-gw-agent'] = {
            'dispatch': 'PAUSED', 'decided_by': 'PM', 'decided_at': '2026-11-08',
            'reason': 'test fixture', 'evidence': worker_dispatch._GW_AGENT_PAUSED_REF}
        workers['agy'] = dict({'decided_by': 'Owner', 'decided_at': '2026-11-08',
                               'reason': 'test fixture', 'evidence': evidence}, **agy_row)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'worker-dispatch.json'
        path.write_text(json.dumps({'schema_version': 1, 'workers': workers}),
                        encoding='utf-8')
        return path

    def test_agy_without_owner_ref_is_refused_by_restrictive_policy_without_store_write(self):
        task_id = 'a' * 32
        matching_ref = ('https://github.com/davidcatcat/davidcattoolsproject/issues/289'
                        '#issuecomment-1')
        rows = {
            'OWNER_ASSIGNMENT_ONLY': ({'dispatch': 'OWNER_ASSIGNMENT_ONLY', 'owner_assignments': [
                {'task_id': task_id, 'owner_assignment_ref': matching_ref}]},
                'OWNER_ASSIGNMENT_REQUIRED'),
            'PAUSED': ({'dispatch': 'PAUSED'}, 'OWNER_PAUSED'),
        }
        task = {
            'schema_version': 2, 'task_id': task_id, 'run_id': 'b' * 32,
            'state': 'PM_RESPONDED', 'response': 'PM response', 'request': 'AGY goal',
            'task_binding': {'contract_digest': 'c' * 64},
            'pm_assignment': {'route': 'agy', 'risk': 'LOW', 'goal': 'AGY goal',
                              'acceptance': ['the result is recorded']},
            'developer': None, 'completion': None,
        }
        registration = SimpleNamespace(task_id='executor-d6')
        snapshot = SimpleNamespace(head='d' * 40)
        for name, (agy_row, expected_code) in rows.items():
            with self.subTest(policy=name):
                policy_path = self._fixture_policy(agy_row)
                writes = []

                class Store:
                    def get(self, _task_id):
                        return dict(task)

                    def __getattr__(self, attr):
                        def record(*args, **kwargs):
                            writes.append((attr, args, kwargs))
                            raise AssertionError('no store write is allowed')
                        return record

                args = Namespace(task_id=task_id, provider='agy', mode='direct',
                                 owner_ref=None, context_stdin=False, selection_source='PM',
                                 acceptance=['the result is recorded'],
                                 egress_basis='Approved non-sensitive Task context.',
                                 executor_task='executor-d6', read_path=['src.py'],
                                 write_path=['src.py'], repair_packet=None)
                store = Store()
                with patch.object(worker_dispatch, '_POLICY_PATH', policy_path), \
                        patch.object(task_submission_cli.task_completion,
                                     'validate_task_binding'), \
                        patch.object(task_submission_cli, '_delegate_d6_binding',
                                     return_value=(registration, snapshot)), \
                        patch.object(task_submission_cli, '_reject_claude_reserved_d6'):
                    with self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
                        task_submission_cli.run_task_agy_delegate(args, store=store)
                self.assertEqual(caught.exception.dispatch_code, expected_code)
                self.assertEqual(writes, [])

    def test_agy_developer_record_stores_omitted_owner_ref_as_none(self):
        _status, saved, _events, _prompts = self._run_agy_delegate(owner_ref=None)
        self.assertIn('owner_assignment_ref', saved['developer'])
        self.assertIsNone(saved['developer']['owner_assignment_ref'])

    def test_agy_developer_routes_to_claude_reviewer(self):
        task = {
            'task_id': 'a' * 32, 'run_id': 'b' * 32,
            'task_binding': {'contract_digest': 'c' * 64},
            'completion': {'executor_task_id': 'executor-d6',
                           'evidence': {'common_dir': '/repo/.git'}},
            'developer': {'provider_profile': 'agy', 'model': 'gemini-2.5-pro'},
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
        for evidence in (task['completion']['evidence'],
                         task['developer']['direct_result_callback']['evidence']):
            self.assertEqual(evidence['executor_native_session_id'], 'conversation_12345678')
            self.assertEqual(evidence['executor_native_session'], 'VERIFIED')
        self.assertNotIn('native_session_binding', task['developer']['execution'])
        self.assertEqual(inbox_events, [{'task_id': 'a' * 32,
                                         'executor_task_id': 'executor-d6'}])

    def test_agy_unbound_missing_empty_and_unknown_sessions_remain_unverified(self):
        for session_id in (None, '', 'UNKNOWN'):
            with self.subTest(session_id=session_id):
                status, task, _events, _prompts = self._run_agy_delegate(
                    session_id=session_id)
                self.assertEqual(status, 0)
                self.assertIsNone(task['completion']['evidence']['executor_native_session_id'])
                self.assertEqual(task['completion']['evidence']['executor_native_session'],
                                 'UNKNOWN')
                self.assertIsNone(task['developer']['direct_result_callback']['evidence'][
                    'executor_native_session_id'])
                self.assertEqual(task['developer']['direct_result_callback']['evidence'][
                    'executor_native_session'], 'UNKNOWN')

    def test_agy_non_bound_session_result_preserves_registration_evidence(self):
        status, task, _events, _prompts = self._run_agy_delegate(bind_result='D6_BIND_NATIVE_SESSION_ALREADY_SET',
            initial_native_session_id='conversation_old1234')
        self.assertEqual(status, 0)
        self.assertNotIn('native_session_binding', task['developer']['execution'])
        for evidence in (task['completion']['evidence'],
                         task['developer']['direct_result_callback']['evidence']):
            self.assertEqual(evidence['executor_native_session_id'], 'conversation_old1234')
            self.assertEqual(evidence['executor_native_session'], 'VERIFIED')

    def test_agy_repair_on_bound_d6_passes_real_executor_worktree_result_gate(self):
        status, task, _events, _prompts = self._run_agy_delegate(
            bind_result='D6_BIND_NATIVE_SESSION_ALREADY_SET',
            initial_native_session_id='conversation_old1234')
        self.assertEqual(status, 0)
        evidence = task['developer']['direct_result_callback']['evidence']
        registration = task_completion.D6Registration(
            task_id=evidence['executor_task_id'], trusted_agent_id=evidence['executor_identity'],
            session_ref=evidence['executor_session_ref'], root=evidence['root'],
            common_dir=evidence['common_dir'], branch=evidence['branch'],
            source_sha=evidence['source_sha'], result_sha=evidence['source_sha'],
            state='ACTIVE', lock_state='HELD', device=1, inode=2, uid=3,
            native_session_id='conversation_old1234', github_login=None,
            env2_pass_marker=None, env2_approved_tooling_sha=evidence['source_sha'])
        diff = task['developer']['artifact'].encode('utf-8')
        live = {'common_dir': registration.common_dir, 'branch': registration.branch,
                'source_sha': registration.result_sha, 'diff': diff,
                'changed_paths': ['src.py'],
                'diff_sha256': hashlib.sha256(diff).hexdigest(),
                'status_sha256': 'f' * 64, 'file_manifest_sha256': 'e' * 64}
        with patch.object(task_completion, 'read_d6_registration', return_value=registration), \
                patch.object(task_completion, '_reject_legacy_grafts'), \
                patch.object(task_completion, '_git', return_value=b''), \
                patch.object(task_completion, '_worktree_snapshot', return_value=live), \
                patch.object(task_completion, 'validate_env2_pass_marker'):
            collected = task_completion.collect_executor_worktree_result(
                task_id='a' * 32, expected_common_dir=registration.common_dir,
                expected_root=registration.root, expected_branch=registration.branch,
                source_sha=registration.source_sha,
                task_binding={'task_id': 'a' * 32, 'run_id': 'b' * 32,
                              'contract_digest': 'c' * 64},
                expected_agent=registration.trusted_agent_id,
                expected_session_ref=registration.session_ref,
                expected_native_session_id=evidence['executor_native_session_id'],
                write_paths=['src.py'], read_paths=['src.py'], artifact=diff.decode('utf-8'),
                artifact_sha256=hashlib.sha256(diff).hexdigest())
        self.assertEqual(collected['executor_native_session_id'], 'conversation_old1234')
        self.assertEqual(collected['executor_native_session'], 'VERIFIED')

    def test_agy_bound_registration_reread_mismatch_blocks_with_binding_changed(self):
        status, task, inbox_events, _prompts = self._run_agy_delegate(
            bound_registration_mutation=True)
        self.assertEqual(status, 1)
        self.assertEqual(task['developer']['error_code'], 'AGY_D6_BINDING_CHANGED')
        self.assertEqual(inbox_events, [])

    def test_agy_session_shared_with_reviewer_fails_native_independence_gate(self):
        session_id = 'conversation_shared123'
        status, task, _events, _prompts = self._run_agy_delegate(session_id=session_id)
        self.assertEqual(status, 0)
        self.assertEqual(task['completion']['evidence']['executor_native_session_id'],
                         session_id)
        result_sha = 'd' * 40
        reviewer = task_completion.D6Registration(
            task_id='reviewer-d6', trusted_agent_id='codex-reviewer',
            session_ref='review-session', root='/reviewer/root', common_dir='/repo/.git',
            branch='review-branch', source_sha=result_sha, result_sha=result_sha,
            state='ACTIVE', lock_state='HELD', device=1, inode=2, uid=3,
            native_session_id=session_id, github_login='reviewer',
            env2_pass_marker=None, env2_approved_tooling_sha=result_sha)
        pull = {'state': 'open', 'merged': False,
                'head': {'sha': result_sha, 'ref': 'task-branch'},
                'base': {'repo': {'full_name': 'owner/repo'}},
                'user': {'login': 'author'}}
        ci = {'successful_checks': [], 'required_checks': ['check'],
              'docs_only': False, 'check_outcomes': {}, 'workflow_path': None,
              'workflow_name': None, 'workflow_id': None, 'run_id': None,
              'run_attempt': None, 'run_number': None, 'check_ids': []}
        with patch.object(task_completion, '_trusted_slug', return_value='owner/repo'), \
                patch.object(task_completion, '_github_get', side_effect=[pull, [], []]), \
                patch.object(task_completion, 'read_d6_registration', return_value=reviewer), \
                patch.object(task_completion, 'collect_git_snapshot', return_value=SimpleNamespace(
                    common_dir=reviewer.common_dir, branch=reviewer.branch, head=result_sha)), \
                patch.object(task_completion, '_collect_ci_evidence', return_value=ci):
            result = task_completion.collect_github_evidence(
                pr_number=1, result_sha=result_sha, task_id='a' * 32, run_id='b' * 32,
                contract_digest='c' * 64, executor_identity='codex',
                executor_session_ref='executor-session',
                executor_native_session_id=session_id, reviewer_task_id=reviewer.task_id,
                common_dir=reviewer.common_dir)
        self.assertIn('REVIEWER_NATIVE_SESSION_NOT_INDEPENDENT', result['missing_gates'])

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

    def test_credential_shaped_read_path_is_refused(self):
        fixture_path = 'fixtures/' + 'gh' + 'p_' + 'A' * 36
        with self.assertRaisesRegex(LLMToolError, 'recognizable credential material'):
            self._run_agy_delegate(read_paths=[fixture_path])

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
        audit = {}
        status, task, inbox_events, _prompts = self._run_agy_delegate(
            binding_changes=True, audit=audit)
        self.assertEqual(status, 1)
        self.assertEqual(task['developer']['error_code'], 'AGY_D6_BINDING_CHANGED')
        self.assertEqual(audit['bind_executor_calls'], 3)
        self.assertEqual(audit.get('provider_results', 0), 0)
        self.assertEqual(audit['registration_reads'], 0)
        self.assertEqual(audit['diff_calls'], 0)
        self.assertEqual(audit['bind_calls'], 0)
        self.assertEqual(audit['result_events'], 0)
        self.assertEqual(inbox_events, [])

    def test_agy_bound_result_persists_through_real_task_store(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / 'tasks'
            store = TaskStore(root)
            decision = worker_dispatch.PolicyDecision(
                worker_key='agy',
                allowed=True,
                reason_code='OWNER_POLICY_ALLOWED',
                policy_sha256='f' * 64,
            )
            audit = {}
            with patch.object(
                    task_submission_cli, '_agy_developer_profile',
                    return_value=('gemini-test', 'high')):
                status, task, events, _prompts = self._run_agy_delegate(
                    store=store,
                    decisions=(decision,) * 4,
                    audit=audit,
                )

            persisted = TaskStore(root).get('a' * 32)
            self.assertEqual(task, persisted)
            self.assertEqual(status, 0)

            developer = persisted['developer']
            self.assertEqual(developer['state'], 'DEVELOPER_RESPONDED')
            self.assertIsNone(developer['error_code'])
            self.assertEqual(developer['execution']['status'], 'DONE')
            self.assertEqual(
                developer['execution']['native_session_id'],
                'conversation_12345678',
            )
            self.assertNotIn(
                'native_session_binding', developer['execution'])

            artifact = 'diff --git a/src.py b/src.py\n'
            self.assertEqual(developer['artifact'], artifact)
            self.assertEqual(
                developer['artifact_sha256'],
                hashlib.sha256(artifact.encode('utf-8')).hexdigest(),
            )

            completion_evidence = persisted['completion']['evidence']
            callback_evidence = developer['direct_result_callback']['evidence']
            for evidence in (completion_evidence, callback_evidence):
                self.assertEqual(
                    evidence['executor_native_session_id'],
                    'conversation_12345678',
                )
                self.assertEqual(
                    evidence['executor_native_session'], 'VERIFIED')
                self.assertEqual(
                    evidence['contract_digest'],
                    persisted['task_binding']['contract_digest'],
                )
            for key, value in completion_evidence.items():
                self.assertEqual(callback_evidence[key], value)

            self.assertEqual(audit['provider_results'], 1)
            self.assertEqual(audit['registration_reads'], 2)
            self.assertEqual(audit['bind_calls'], 1)
            self.assertEqual(audit['result_events'], 1)
            self.assertEqual(events, [{
                'task_id': 'a' * 32,
                'executor_task_id': 'executor-d6',
            }])

    def test_agy_refused_bind_persists_through_real_task_store(self):
        cases = (
            ('D6_BIND_NATIVE_SESSION_REFUSED', None, 'UNKNOWN'),
            (
                'D6_BIND_NATIVE_SESSION_ALREADY_SET',
                'conversation_old1234',
                'VERIFIED',
            ),
        )
        for bind_result, previous_session, verification in cases:
            with self.subTest(bind_result=bind_result):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary).resolve() / 'tasks'
                    store = TaskStore(root)
                    decision = worker_dispatch.PolicyDecision(
                        worker_key='agy',
                        allowed=True,
                        reason_code='OWNER_POLICY_ALLOWED',
                        policy_sha256='f' * 64,
                    )
                    audit = {}
                    with patch.object(
                            task_submission_cli, '_agy_developer_profile',
                            return_value=('gemini-test', 'high')):
                        status, task, events, _prompts = self._run_agy_delegate(
                            store=store,
                            decisions=(decision,) * 4,
                            bind_result=bind_result,
                            initial_native_session_id=previous_session,
                            audit=audit,
                        )

                    persisted = TaskStore(root).get('a' * 32)
                    self.assertEqual(task, persisted)
                    self.assertEqual(status, 0)

                    developer = persisted['developer']
                    self.assertEqual(
                        developer['state'], 'DEVELOPER_RESPONDED')
                    self.assertIsNone(developer['error_code'])
                    self.assertEqual(
                        developer['execution']['status'], 'DONE')
                    self.assertEqual(
                        developer['execution']['native_session_id'],
                        'conversation_12345678',
                    )
                    self.assertNotIn(
                        'native_session_binding', developer['execution'])

                    artifact = 'diff --git a/src.py b/src.py\n'
                    self.assertEqual(developer['artifact'], artifact)
                    self.assertEqual(
                        developer['artifact_sha256'],
                        hashlib.sha256(artifact.encode('utf-8')).hexdigest(),
                    )

                    completion_evidence = persisted['completion']['evidence']
                    callback_evidence = developer[
                        'direct_result_callback']['evidence']
                    for evidence in (completion_evidence, callback_evidence):
                        self.assertEqual(
                            evidence['executor_native_session_id'],
                            previous_session,
                        )
                        self.assertEqual(
                            evidence['executor_native_session'],
                            verification,
                        )
                    for key, value in completion_evidence.items():
                        self.assertEqual(callback_evidence[key], value)

                    self.assertEqual(audit['provider_results'], 1)
                    self.assertEqual(audit['registration_reads'], 1)
                    self.assertEqual(audit['bind_calls'], 1)
                    self.assertEqual(audit['result_events'], 1)
                    self.assertEqual(events, [{
                        'task_id': 'a' * 32,
                        'executor_task_id': 'executor-d6',
                    }])

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

    def test_credential_shaped_repair_excerpt_is_refused(self):
        fake_pat = 'gh' + 'p_' + 'A' * 36
        packet = {'task': 'task-1', 'pr': 7, 'executor_task': 'executor-d6',
            'failed_head': 'd' * 40, 'version': 1, 'verdict_comment_id': 9,
            'findings_count': 1,
            'reviewer_text_excerpt_untrusted_data': f'Use {fake_pat} here.'}
        with self.assertRaisesRegex(LLMToolError, 'recognizable credential material'):
            self._run_agy_delegate(repair_packet=packet)

    def _assert_skill_refusal(self, message, **kwargs):
        audit = {}
        record = Mock()
        with self.assertRaisesRegex(LLMToolError, message):
            self._run_agy_delegate(audit=audit, ledger_record=record, **kwargs)
        self.assertEqual(audit['store_writes'], [])
        self.assertEqual(audit['prompts'], [])
        record.assert_not_called()

    def test_agy_provider_selects_agents_file_from_the_manifest(self):
        commit = 'd' * 40
        with tempfile.TemporaryDirectory() as root:
            def git(argv, **_kwargs):
                return SimpleNamespace(
                    returncode=0, stdout=root if '--show-toplevel' in argv else commit)

            with patch.object(skill_manifest, '_read_blob', return_value=b'rules\n'):
                manifest = skill_manifest.build_manifest(
                    root, commit, 'developer', 'develop', git_runner=git, provider='agy')
        self.assertEqual([entry['path'] for entry in manifest['entries']],
                         ['AGENTS.md', 'skills/ai-team-delivery/SKILL.md'])

    def test_success_embeds_manifest_skill_text_and_records_delivery(self):
        build = Mock(return_value=_SKILL_MANIFEST)
        read = Mock(return_value=_SKILL_FILES)
        record = Mock(return_value='receipt')
        with patch.object(task_submission_cli, '_agy_developer_profile',
                          return_value=('gemini-requested', 'high')):
            status, task, _events, prompts = self._run_agy_delegate(
                verified_model='gemini-verified', build_manifest=build,
                read_manifest_files=read, ledger_record=record)
        self.assertEqual(status, 0)
        build.assert_called_once_with('/registered/root', 'd' * 40, 'developer', 'develop',
                                      provider='agy')
        read.assert_called_once_with('/registered/root', _SKILL_MANIFEST)
        prompt = prompts[0]
        raw = prompt.encode('utf-8')
        self.assertIn('these limits override any conflicting skill text', prompt)
        for path, text in _SKILL_FILES:
            self.assertIn('REQUIRED_SKILL_BEGIN path=' + path + '\n' + text, prompt)
            self.assertLess(prompt.index(text), prompt.index('\nTASK_JSON='))
        self.assertLess(len(raw), 64 * 1024)
        developer = task['developer']
        self.assertEqual(developer['model'], 'gemini-requested')
        self.assertEqual(developer['input_bytes'], len(raw))
        self.assertEqual(developer['context_sha256'], hashlib.sha256(raw).hexdigest())
        evidence = task['completion']['evidence']
        record.assert_called_once_with({
            'task_id': 'a' * 32, 'run_id': 'b' * 32,
            'session_id': 'conversation_12345678',
            'agent_id': evidence['executor_identity'], 'role': 'developer',
            'provider': 'agy', 'model': 'gemini-verified', 'repo': '/repo/.git',
            'head': 'd' * 40, 'manifest_sha256': '7' * 64,
            'evidence_level': 'CONTROLLER_DELIVERED',
        })

    def test_real_ledger_entry_matches_the_run(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        ledger_path = Path(temp.name) / 'ledger.jsonl'
        with patch.object(skill_ledger, '_path', return_value=ledger_path), \
                patch.object(task_submission_cli, '_agy_developer_profile',
                             return_value=('gemini-requested', 'high')):
            status, task, _events, _prompts = self._run_agy_delegate(
                verified_model='gemini-verified', ledger_record=skill_ledger.record)
            evidence = task['completion']['evidence']

            def lookup(model):
                return skill_ledger.check(
                    task['task_id'], task['run_id'],
                    task['developer']['execution']['native_session_id'],
                    evidence['executor_identity'], 'developer', 'agy', model,
                    evidence['common_dir'], task['developer']['source_sha'],
                    _SKILL_MANIFEST['manifest_sha256'])

            verified = lookup('gemini-verified')
            requested = lookup('gemini-requested')
            chain = skill_ledger.verify_chain()
        self.assertEqual(status, 0)
        self.assertEqual(chain, (True, None))
        self.assertEqual(verified['status'], 'VERIFIED')
        self.assertEqual(verified['evidence_level'], 'CONTROLLER_DELIVERED')
        self.assertEqual(requested, {'status': 'UNVERIFIED', 'reason': 'WRONG_MODEL'})
        self.assertEqual(len(ledger_path.read_text(encoding='utf-8').splitlines()), 1)

    def test_missing_or_unreadable_skill_refuses_before_provider_call(self):
        for target, code in (
                ('build_manifest', skill_manifest.SKILL_MANIFEST_PATH_MISSING),
                ('build_manifest', skill_manifest.SKILL_MANIFEST_NOT_FILE),
                ('build_manifest', skill_manifest.SKILL_MANIFEST_NOT_UTF8),
                ('read_manifest_files', skill_manifest.SKILL_MANIFEST_PATH_MISSING)):
            with self.subTest(target=target, code=code):
                failing = Mock(side_effect=skill_manifest.SkillManifestError(code))
                self._assert_skill_refusal(code, **{target: failing})

    def test_unsafe_skill_text_refuses_before_provider_call(self):
        for text in (' \n', 'rules\x01\n', 'rules​\n'):
            with self.subTest(text=text):
                self._assert_skill_refusal(
                    'AGY_SKILL_TEXT_REFUSED',
                    read_manifest_files=Mock(return_value=[('AGENTS.md', text)]))

    def test_sensitive_skill_text_refuses_before_provider_call(self):
        flagged = 'flagged skill rule\n'
        with patch.object(task_submission_cli, '_emitted_sensitive',
                          side_effect=lambda value: flagged in value):
            self._assert_skill_refusal(
                'AGY_SKILL_TEXT_REFUSED',
                read_manifest_files=Mock(return_value=[('AGENTS.md', flagged)]))

    def test_skill_prompt_limit_refuses_and_never_truncates(self):
        def files(extra):
            path, text = _SKILL_FILES[1]
            padding = ('skill rule ' * (extra // 11 + 1))[:extra]
            return [_SKILL_FILES[0], (path, text + padding)]

        def run(extra):
            status, _task, _events, prompts = self._run_agy_delegate(
                read_manifest_files=Mock(return_value=files(extra)))
            self.assertEqual(status, 0)
            return prompts[0]

        limit = task_submission_cli._AGY_PROMPT_MAX_BYTES
        self.assertEqual(limit, 64 * 1024 - 1)
        room = limit - len(run(0).encode('utf-8'))
        self.assertGreater(room, 0)
        fitted = run(room)
        self.assertEqual(len(fitted.encode('utf-8')), limit)
        self.assertIn(files(room)[1][1], fitted)
        self._assert_skill_refusal(
            'AGY_SKILL_PROMPT_TOO_LARGE',
            read_manifest_files=Mock(return_value=files(room + 1)))

    def test_ledger_failure_keeps_the_finished_run_and_reports_the_code(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            status, task, inbox_events, _prompts = self._run_agy_delegate(
                ledger_record=Mock(side_effect=skill_ledger.SkillLedgerError('CHAIN_BROKEN')))
        self.assertEqual(status, 0)
        self.assertEqual(task['developer']['state'], 'DEVELOPER_RESPONDED')
        self.assertEqual(len(inbox_events), 1)
        self.assertIn('AGY_SKILL_LEDGER_OUTCOME_UNKNOWN: CHAIN_BROKEN', stderr.getvalue())

    def test_route_failure_records_no_delivery(self):
        record = Mock()
        status, task, _events, _prompts = self._run_agy_delegate(
            RouteError('BLOCKED_WRITE_ISOLATION', blocked_reason='BOUNDARY_UNVERIFIED'),
            ledger_record=record)
        self.assertEqual(status, 1)
        self.assertEqual(task['developer']['state'], 'BLOCKED')
        record.assert_not_called()

    def test_dispatch_denial_records_no_delivery(self):
        allowed = SimpleNamespace(allowed=True, reason_code='OWNER_ASSIGNMENT_MATCHED')
        denied = SimpleNamespace(allowed=False, reason_code='OWNER_ASSIGNMENT_MISMATCH')
        record = Mock()
        status, task, _events, _prompts = self._run_agy_delegate(
            decisions=(allowed, denied), ledger_record=record)
        self.assertEqual(status, 1)
        self.assertEqual(task['developer']['error_code'], 'AGY_DISPATCH_DENIED')
        record.assert_not_called()


if __name__ == '__main__':
    unittest.main()
