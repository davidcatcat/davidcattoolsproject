"""#1019 C2: formal review claims without usage always record a fixed reason."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from llmtool import review_dispatch, review_result, task_advance, task_store, worker_dispatch
from llmtool.developer_routes import load_route_config
from llmtool.errors import LLMToolError
import worker_policy_fixture


TASK = '1' * 32
RUN = '2' * 32
HEAD = 'a' * 40
EXECUTOR = 'executor-d6'
REVIEWER = 'reviewer-' + 'b' * 20
SESSION = '01234567-89ab-cdef-0123-456789abcdef'
PR = 42
CODEX_REVIEW_MODEL = load_route_config()['roles']['codex-reviewer']['allowed_models'][0]
CLAUDE_REVIEW_MODEL = load_route_config()['roles']['claude-reviewer']['allowed_models'][0]
_STUB_RULES_PREFIX = 'RULES stub for claim reason tests\nRULES_END\n\n'
_STUB_RULES_SHA256 = hashlib.sha256(_STUB_RULES_PREFIX.encode('utf-8')).hexdigest()


def _install_worker_policy(testcase):
    worker_policy_fixture.install(testcase)
    repository = patch.object(
        worker_dispatch,
        'repository_config',
        return_value={
            'slug': 'davidcatcat/davidcattoolsproject',
            'dispatch_issue': 289,
            'dispatch_evidence_pr': 283,
        },
    )
    repository.start()
    testcase.addCleanup(repository.stop)


def _make_store(root, *, provider_profile='codex', claims=None):
    contract = {
        'schema_version': 1, 'contract_version': 1, 'task_id': TASK,
        'goal': 'test review claim reason', 'authorized_scope': ['pm-planning'],
        'criteria': [{'criterion_id': 'pm_response', 'description': 'Record result.',
                      'kind': 'ARTIFACT'}], 'forbidden_scope': [], 'previous_digest': None,
    }
    parsed = task_store.parse_task_contract(json.dumps(contract))
    stamp = '2026-11-28T00:00:00Z'
    record = {
        'schema_version': 2, 'task_id': TASK, 'run_id': RUN,
        'pm_session_id': 'pm-test', 'pm_provider_profile': 'test',
        'pm_provider_type': 'test', 'pm_model': None,
        'pm_route_binding': {'type': 'test', 'model': None, 'base_url': None},
        'backend_session_semantics': 'NOT_STARTED', 'native_session_id': None,
        'state': 'PM_PENDING', 'created_at': stamp, 'updated_at': stamp,
        'task_contract': contract, 'task_binding': vars(parsed.binding(RUN)),
        'request': 'test review claim reason',
        'request_sha256': hashlib.sha256(b'test review claim reason').hexdigest(),
        'response': None, 'usage': None, 'error_code': None,
        'developer': {
            'assignment_id': 'e' * 32, 'selection_source': 'PM',
            'state': 'DEVELOPER_RESPONDED',
            'provider_profile': provider_profile,
            'provider_type': 'codex-cli' if provider_profile == 'codex' else 'agy-cli',
            'model': 'gpt-6-luna' if provider_profile == 'codex' else 'gemini-2.5-pro',
            'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
            'session_id': 'dev-session', 'source_sha': HEAD,
            'source_sha_binding': 'D6_REGISTRY_OBSERVED',
            'read_paths': ['tools/llmtool'], 'write_paths': ['tools/llmtool'],
            'acceptance': ['Record claim reason.'], 'context_sha256': 'f' * 64,
            'egress_basis': 'Bounded task context', 'input_bytes': 1,
            'max_tokens': None, 'artifact_format': 'unified_diff',
            'artifact': 'diff',
            'artifact_sha256': hashlib.sha256(b'diff').hexdigest(),
            'usage': None, 'error_code': None, 'created_at': stamp,
            **({'owner_assignment_ref': None} if provider_profile == 'agy' else {}),
        },
        'pm_assignment': None, 'completion': None,
    }
    if claims is not None:
        record['review_dispatch_claim'] = claims
    store = task_store.TaskStore(Path(root).resolve())
    store.create(record)
    return store


def _write_transcript(config_dir, entries, *, session_id=SESSION, reviewer_task=REVIEWER):
    project_dir = os.path.join(config_dir, 'projects', '-tasks-' + reviewer_task)
    os.makedirs(project_dir, exist_ok=True)
    path = os.path.join(project_dir, session_id + '.jsonl')
    with open(path, 'w', encoding='utf-8') as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + '\n')
    return path


def _assistant_entry(msg_id, *, usage=None, stop_reason='end_turn', content=None):
    message = {'id': msg_id, 'stop_reason': stop_reason,
               'content': content or [{'type': 'text', 'text': 'Reviewed.'}]}
    if usage is not None:
        message['usage'] = usage
    return {'type': 'assistant', 'message': message}


class DispatchUnknownClaimReasonTests(unittest.TestCase):
    """R1, R3, R4: formal review dispatches ending UNKNOWN record a closed-list reason."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        _install_worker_policy(self)
        self.executor = SimpleNamespace(root='/repo/executor', common_dir='/repo/.git')
        self.reviewer = SimpleNamespace(root='/repo/reviewer')

    def _codex_role(self):
        return {
            'provider': 'codex', 'config_dir': None,
            'model': CODEX_REVIEW_MODEL, 'effort': 'high', 'source': 'roles',
        }

    def _claude_role(self):
        return {
            'provider': 'claude', 'config_dir': '/private/reviewer-account',
            'model': CLAUDE_REVIEW_MODEL, 'effort': 'medium', 'source': 'roles',
        }

    def _dispatch_codex_expecting_unknown(self, store, *, preflights=None,
                                          which_path='/bin/codex', proc=None,
                                          run_side_effect=None, policy_side_effect=None):
        preflight = (store.get(TASK), self.executor, HEAD, 'd' * 40, 'codex-reviewer')
        if preflights is None:
            preflights = [preflight, preflight]
        run_kwargs = ({'side_effect': run_side_effect} if run_side_effect is not None
                      else {'return_value': proc})
        eval_kwargs = ({'side_effect': policy_side_effect} if policy_side_effect is not None
                       else {'return_value': SimpleNamespace(allowed=True, reason_code='')})
        with patch.object(review_dispatch, '_preflight', side_effect=preflights), \
                patch.object(review_dispatch.worker_dispatch, 'evaluate', **eval_kwargs), \
                patch.object(review_dispatch, '_resolve_github_login',
                             return_value='reviewer-user'), \
                patch.object(review_dispatch, '_review_rules',
                             return_value=(_STUB_RULES_PREFIX, _STUB_RULES_SHA256)), \
                patch.object(review_dispatch.role_config, 'resolve_role',
                             return_value=self._codex_role()), \
                patch.object(review_dispatch.task_completion, 'read_d6_registration',
                             return_value=self.reviewer), \
                patch.object(review_dispatch.shutil, 'which', return_value=which_path), \
                patch.object(review_dispatch.subprocess, 'run', **run_kwargs):
            with self.assertRaises(LLMToolError) as raised:
                review_dispatch.dispatch_review(
                    store=store, task_id=TASK, executor_task=EXECUTOR,
                    pr_number=PR, version=1, create_d6=lambda **_: None,
                    bind_session=lambda **_: None, prompt_renderer=lambda *_: 'prompt')
        return raised.exception, store.get(TASK)['review_dispatch_claim'][0]

    def test_codex_quota_and_not_started_and_unestablished_are_distinguished(self):
        cases = (
            ('quota_stdout', SimpleNamespace(
                returncode=1, stdout=b'{"error":"usage limit reached"}\n', stderr=b''),
             '/bin/codex', None, 'QUOTA_OR_USAGE_LIMIT'),
            ('quota_stderr_429', SimpleNamespace(
                returncode=1, stdout=b'', stderr=b'http status: 429 too many requests\n'),
             '/bin/codex', None, 'QUOTA_OR_USAGE_LIMIT'),
            ('nonzero_other', SimpleNamespace(
                returncode=2, stdout=b'', stderr=b'custom stderr /private/path/secret'),
             '/bin/codex', None, 'CAUSE_NOT_ESTABLISHED'),
            ('missing_binary', None, None, None, 'REVIEWER_NOT_STARTED'),
            ('spawn_oserror', None, '/bin/codex', OSError('exec failed'), 'REVIEWER_NOT_STARTED'),
            ('timeout', None, '/bin/codex',
             review_dispatch.subprocess.TimeoutExpired('codex', 10), 'CAUSE_NOT_ESTABLISHED'),
        )
        for name, proc, which_path, run_exc, expected_reason in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as sub_dir:
                store = _make_store(Path(sub_dir) / 'store', provider_profile='agy')
                exc, claim = self._dispatch_codex_expecting_unknown(
                    store, which_path=which_path, proc=proc, run_side_effect=run_exc)
                self.assertEqual(claim['state'], 'UNKNOWN')
                self.assertEqual(claim['unknown_reason'], expected_reason)
                self.assertIn(claim['unknown_reason'], review_dispatch.UNKNOWN_CLAIM_REASONS)
                self.assertNotIn(claim['unknown_reason'], review_dispatch.REVIEW_FAILURE_REASONS)
                self.assertNotIn('failure_reason', claim)
                self.assertNotIn('usage', claim)
                self.assertNotIn('usage_unknown', claim)
                self.assertNotIn('/private/path/secret', json.dumps(claim))
                self.assertNotIn('custom stderr', json.dumps(claim))
                self.assertFalse(hasattr(exc, 'review_refusal'))

    def test_codex_refused_after_claim_by_head_mismatch_records_not_started_without_receipt(self):
        store = _make_store(Path(self.temp.name) / 'head_moved', provider_profile='agy')
        first = (store.get(TASK), self.executor, HEAD, 'd' * 40, 'codex-reviewer')
        moved = (store.get(TASK), self.executor, 'b' * 40, 'd' * 40, 'codex-reviewer')
        exc, claim = self._dispatch_codex_expecting_unknown(store, preflights=[first, moved])
        self.assertEqual(exc.review_dispatch_code, 'REVIEW_PROMPT_PR_HEAD_MISMATCH')
        self.assertEqual(claim['state'], 'UNKNOWN')
        self.assertEqual(claim['unknown_reason'], 'REVIEWER_NOT_STARTED')
        self.assertNotIn('failure_reason', claim)
        self.assertFalse(hasattr(exc, 'review_refusal'))
        self.assertIsNone(review_dispatch.read_refusal_receipt(store=store, task_id=TASK, claim=claim))

    def test_prior_claimed_reconciled_to_unknown_records_cause_not_established(self):
        stamp = '2026-11-28T00:00:00Z'
        prior = {
            'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': 1,
            'state': 'CLAIMED', 'reviewer_task': REVIEWER, 'native_session_id': None,
            'account_mode': 'ROLES', 'claimed_at': stamp, 'updated_at': stamp,
        }
        store = _make_store(Path(self.temp.name) / 'prior_claimed', provider_profile='agy', claims=[prior])
        preflight = (store.get(TASK), self.executor, HEAD, 'd' * 40, 'codex-reviewer')
        with patch.object(review_dispatch, '_preflight', return_value=preflight), \
                patch.object(review_dispatch.worker_dispatch, 'evaluate',
                             return_value=SimpleNamespace(allowed=True, reason_code='')):
            result = review_dispatch.dispatch_review(
                store=store, task_id=TASK, executor_task=EXECUTOR, pr_number=PR, version=1)
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertEqual(result['unknown_reason'], 'CAUSE_NOT_ESTABLISHED')
        self.assertNotIn('failure_reason', result)
        stored = store.get(TASK)['review_dispatch_claim'][0]
        self.assertEqual(stored['unknown_reason'], 'CAUSE_NOT_ESTABLISHED')
        self.assertNotIn('failure_reason', stored)

    def test_unknown_reason_does_not_change_advance_decide_outcome_unknown(self):
        for reason in sorted(review_dispatch.UNKNOWN_CLAIM_REASONS):
            with self.subTest(reason=reason):
                record = {
                    'task_id': TASK, 'run_id': RUN,
                    'task_binding': {'contract_digest': 'c' * 64},
                    'developer': {'state': 'DEVELOPER_RESPONDED'},
                    'completion': {'executor_task_id': EXECUTOR},
                    'review_dispatch_claim': [{
                        'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD,
                        'version': 1, 'state': 'UNKNOWN',
                        'reviewer_task': REVIEWER, 'native_session_id': None,
                        'account_mode': 'ROLES',
                        'claimed_at': '2026-11-28T00:00:00Z',
                        'updated_at': '2026-11-28T00:00:00Z',
                        'unknown_reason': reason,
                    }],
                }
                d6 = {'result_head': HEAD, 'recorded_result_head': HEAD, 'dirty': False}
                pr = {'number': PR, 'head': {'sha': HEAD}, 'merged': False}
                self.assertEqual(
                    task_advance.decide(record, d6, pr, {}, docs_only=True),
                    'REVIEW_OUTCOME_UNKNOWN',
                )

    def test_claude_rejected_and_unknown_dispatch_record_closed_reasons(self):
        cases = (
            ('owner_paused', {'result': 'REJECTED', 'reason': 'OWNER_PAUSED'},
             1, None, 'REVIEWER_NOT_STARTED'),
            ('bridge_unknown', {'result': 'NOT_DELIVERED', 'reason': 'LAUNCH_FAILED'},
             1, None, 'CAUSE_NOT_ESTABLISHED'),
            ('bridge_oserror', None, 0, OSError('no python'), 'REVIEWER_NOT_STARTED'),
        )
        for name, payload, rc, run_exc, expected_reason in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as sub_dir:
                store = _make_store(Path(sub_dir) / 'store', provider_profile='codex')
                preflight = (store.get(TASK), self.executor, HEAD, 'd' * 40, 'claude-reviewer')
                proc = SimpleNamespace(
                    returncode=rc,
                    stdout=json.dumps(payload).encode('utf-8') if payload is not None else b'',
                    stderr=b'')
                run_kwargs = ({'side_effect': run_exc} if run_exc is not None
                              else {'return_value': proc})
                with patch.object(review_dispatch, '_preflight', side_effect=[preflight, preflight]), \
                        patch.object(review_dispatch.worker_dispatch, 'evaluate',
                                     return_value=SimpleNamespace(allowed=True, reason_code='')), \
                        patch.object(review_dispatch, '_resolve_github_login',
                                     return_value='reviewer-user'), \
                        patch.object(review_dispatch, '_review_rules',
                                     return_value=(_STUB_RULES_PREFIX, _STUB_RULES_SHA256)), \
                        patch.object(review_dispatch.role_config, 'resolve_role',
                                     return_value=self._claude_role()), \
                        patch.object(review_dispatch.subprocess, 'run', **run_kwargs):
                    with self.assertRaises(LLMToolError):
                        review_dispatch.dispatch_review(
                            store=store, task_id=TASK, executor_task=EXECUTOR,
                            pr_number=PR, version=1, create_d6=lambda **_: None,
                            bind_session=lambda **_: None, prompt_renderer=lambda *_: 'prompt')
                claim = store.get(TASK)['review_dispatch_claim'][0]
                self.assertEqual(claim['state'], 'UNKNOWN')
                self.assertEqual(claim['unknown_reason'], expected_reason)
                self.assertNotIn('failure_reason', claim)
                self.assertNotIn('/private/reviewer-account', json.dumps(claim))

    def test_codex_failed_verdict_and_nonzero_exit_retain_readable_usage_on_unknown_claim(self):
        usage_event = {
            'type': 'turn.completed',
            'usage': {
                'input_tokens': 120,
                'cached_input_tokens': 30,
                'output_tokens': 25,
                'reasoning_output_tokens': 5,
            },
        }
        expected_usage = {
            'prompt_tokens': 120,
            'completion_tokens': 25,
            'total_tokens': 145,
            'cached_prompt_tokens': 30,
        }
        invalid_verdict_stream = '\n'.join((
            json.dumps({'type': 'thread.started', 'thread_id': '0123456789abcdef'}),
            json.dumps({'type': 'item.completed', 'item': {
                'type': 'agent_message', 'text': 'not json'}}),
            json.dumps(usage_event),
        )).encode('utf-8')
        nonzero_usage_stream = (json.dumps(usage_event) + '\n').encode('utf-8')
        zero_usage_stream = '\n'.join((
            json.dumps({'type': 'thread.started', 'thread_id': '0123456789abcdef'}),
            json.dumps({'type': 'item.completed', 'item': {
                'type': 'agent_message', 'text': 'not json'}}),
            json.dumps({'type': 'turn.completed', 'usage': {
                'input_tokens': 0, 'cached_input_tokens': 0,
                'output_tokens': 0, 'reasoning_output_tokens': 0}}),
        )).encode('utf-8')
        cases = (
            ('invalid_verdict_keeps_usage', 0, invalid_verdict_stream,
             'VERDICT_NOT_JSON', None, expected_usage),
            ('nonzero_exit_keeps_usage', 1, nonzero_usage_stream,
             None, 'CAUSE_NOT_ESTABLISHED', expected_usage),
            ('invalid_verdict_zero_usage_never_stored', 0, zero_usage_stream,
             'VERDICT_NOT_JSON', None, None),
        )
        for name, rc, stdout_bytes, exp_failure, exp_unknown, exp_usage in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as sub_dir:
                store = _make_store(Path(sub_dir) / 'store', provider_profile='agy')
                proc = SimpleNamespace(returncode=rc, stdout=stdout_bytes, stderr=b'')
                _, claim = self._dispatch_codex_expecting_unknown(store, proc=proc)
                self.assertEqual(claim['state'], 'UNKNOWN')
                self.assertNotIn('usage_unknown', claim)
                if exp_failure is not None:
                    self.assertEqual(claim['failure_reason'], exp_failure)
                    self.assertNotIn('unknown_reason', claim)
                else:
                    self.assertEqual(claim['unknown_reason'], exp_unknown)
                    self.assertNotIn('failure_reason', claim)
                if exp_usage is not None:
                    self.assertEqual(claim['usage'], exp_usage)
                else:
                    self.assertNotIn('usage', claim)


class SessionEndedWithoutVerdictTests(unittest.TestCase):
    """R1, R2, R3, R4: DISPATCHED Claude claims whose session ends without a verdict."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        _install_worker_policy(self)
        self.home = os.path.join(self.temp.name, 'claude-home')
        os.makedirs(self.home)

    def _dispatched_store(self, name):
        stamp = '2026-11-28T00:00:00Z'
        claim = {
            'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': 1,
            'state': 'DISPATCHED', 'reviewer_task': REVIEWER,
            'native_session_id': SESSION, 'account_mode': 'ROLES',
            'claimed_at': stamp, 'updated_at': stamp,
        }
        return _make_store(Path(self.temp.name) / name, provider_profile='codex', claims=[claim])

    def test_session_ended_without_verdict_keeps_readable_usage(self):
        store = self._dispatched_store('with_usage')
        _write_transcript(self.home, [
            _assistant_entry('msg_1', usage={
                'input_tokens': 100, 'cache_creation_input_tokens': 20,
                'cache_read_input_tokens': 30, 'output_tokens': 15}),
        ])
        res = review_result.record_review_result(
            store=store, task_id=TASK, executor_task=EXECUTOR, pr_number=PR,
            reviewer_session_done=True, github_get=lambda _p: [], config_dir=self.home)
        self.assertEqual(res['state'], 'REVIEW_BLOCKED')
        claim = store.get(TASK)['review_dispatch_claim'][0]
        self.assertEqual(claim['state'], 'DISPATCHED')
        self.assertEqual(claim['usage'], {
            'prompt_tokens': 150, 'completion_tokens': 15,
            'total_tokens': 165, 'cached_prompt_tokens': 30,
        })
        self.assertNotIn('usage_unknown', claim)

    def test_session_ended_without_verdict_and_no_usage_records_reason_never_zero(self):
        cases = (
            ('missing_transcript', None, 'TRANSCRIPT_UNAVAILABLE'),
            ('empty_transcript', [], 'USAGE_ABSENT'),
            ('zero_usage', [_assistant_entry('msg_1', usage={
                'input_tokens': 0, 'cache_creation_input_tokens': 0,
                'cache_read_input_tokens': 0, 'output_tokens': 0})], 'USAGE_ABSENT'),
            ('incomplete_transcript', [_assistant_entry(
                'msg_1', stop_reason='tool_use',
                content=[{'type': 'tool_use', 'id': 't1', 'name': 'Read'}],
                usage={'input_tokens': 10, 'output_tokens': 5})], 'CAUSE_NOT_ESTABLISHED'),
        )
        for name, entries, expected_reason in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as sub_dir:
                home = os.path.join(sub_dir, 'home')
                os.makedirs(home)
                if entries is not None:
                    _write_transcript(home, entries)
                store = _make_store(Path(sub_dir) / 'store', provider_profile='codex', claims=[{
                    'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': 1,
                    'state': 'DISPATCHED', 'reviewer_task': REVIEWER,
                    'native_session_id': SESSION, 'account_mode': 'ROLES',
                    'claimed_at': '2026-11-28T00:00:00Z',
                    'updated_at': '2026-11-28T00:00:00Z',
                }])
                res = review_result.record_review_result(
                    store=store, task_id=TASK, executor_task=EXECUTOR, pr_number=PR,
                    reviewer_session_done=True, github_get=lambda _p: [], config_dir=home)
                self.assertEqual(res['state'], 'REVIEW_BLOCKED')
                claim = store.get(TASK)['review_dispatch_claim'][0]
                self.assertEqual(claim['state'], 'DISPATCHED')
                self.assertNotIn('usage', claim)
                self.assertEqual(claim['usage_unknown'], {'reason': expected_reason})

    def test_unexpected_transcript_error_fails_closed_as_blocked_with_reason(self):
        store = self._dispatched_store('unexpected_err')
        with patch('llmtool.review_transcript.claim_usage', side_effect=RuntimeError('boom')):
            res = review_result.record_review_result(
                store=store, task_id=TASK, executor_task=EXECUTOR, pr_number=PR,
                reviewer_session_done=True, github_get=lambda _p: [], config_dir=self.home)
        self.assertEqual(res['state'], 'REVIEW_BLOCKED')
        claim = store.get(TASK)['review_dispatch_claim'][0]
        self.assertNotIn('usage', claim)
        self.assertEqual(claim['usage_unknown'], {'reason': 'CAUSE_NOT_ESTABLISHED'})

    def test_older_claims_still_read_and_invalid_reasons_still_rejected(self):
        stamp = '2026-11-28T00:00:00Z'
        legacy_unknown = {
            'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': 1,
            'state': 'UNKNOWN', 'reviewer_task': REVIEWER,
            'native_session_id': None, 'account_mode': 'CODEX_CLI',
            'claimed_at': stamp, 'updated_at': stamp,
        }
        store = _make_store(Path(self.temp.name) / 'legacy', provider_profile='agy',
                            claims=[legacy_unknown])
        self.assertEqual(store.get(TASK)['review_dispatch_claim'][0], legacy_unknown)
        with self.assertRaises(LLMToolError):
            store.compare_and_update(
                TASK, expected={'review_dispatch_claim': [legacy_unknown]},
                review_dispatch_claim=[dict(legacy_unknown, failure_reason='NOT_IN_CLOSED_LIST')])
        with self.assertRaises(LLMToolError):
            store.compare_and_update(
                TASK, expected={'review_dispatch_claim': [legacy_unknown]},
                review_dispatch_claim=[dict(legacy_unknown, unknown_reason='NOT_IN_CLOSED_LIST')])
        with self.assertRaises(LLMToolError):
            store.compare_and_update(
                TASK, expected={'review_dispatch_claim': [legacy_unknown]},
                review_dispatch_claim=[dict(
                    legacy_unknown,
                    failure_reason='REVIEWER_D6_CREATE_FAILED',
                    unknown_reason='REVIEWER_NOT_STARTED')])
        with self.assertRaises(LLMToolError):
            store.compare_and_update(
                TASK, expected={'review_dispatch_claim': [legacy_unknown]},
                review_dispatch_claim=[dict(
                    legacy_unknown,
                    bind_failure_reason='REVIEWER_SESSION_BIND_TIMEOUT',
                    unknown_reason='CAUSE_NOT_ESTABLISHED')])


if __name__ == '__main__':
    unittest.main()
