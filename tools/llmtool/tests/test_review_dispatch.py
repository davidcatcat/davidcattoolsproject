import hashlib
import json
import io
from contextlib import redirect_stderr
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import cli, review_dispatch, task_store, task_submission_cli
from llmtool.errors import LLMToolError


TASK = 'a' * 32
HEAD = 'b' * 40


class DurableFakeStore:
    """Small file-backed TaskStore seam for restart/idempotence tests."""
    def __init__(self, path):
        self.path = Path(path)
        self.data = (json.loads(self.path.read_text()) if self.path.exists() else
                     {'record_sha256': 'c' * 64})

    def get(self, task_id):
        assert task_id == TASK
        return dict(self.data)

    def compare_and_update(self, task_id, *, expected, **changes):
        assert task_id == TASK
        if any(self.data.get(key) != value for key, value in expected.items()):
            raise RuntimeError('conflict')
        self.data.update(changes)
        self.path.write_text(json.dumps(self.data))
        return dict(self.data)


class ReviewDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = DurableFakeStore(Path(self.temp.name) / 'task.json')
        self.executor = SimpleNamespace(root='/repo/worktree', common_dir='/repo/.git')
        self.preflight = (TASK, self.executor, HEAD, 'd' * 40, 'claude-reviewer')
        self.created, self.sent, self.bound = [], [], []

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, *, create=None, preflight=None, policy=True, codex_runner=None):
        create = create or (lambda **kw: self.created.append(kw))
        send = lambda **kw: (self.sent.append(kw) or 'native-session')
        bind = lambda **kw: self.bound.append(kw)
        gate = SimpleNamespace(allowed=policy, reason_code='PAUSED')
        with patch.object(review_dispatch, '_preflight', side_effect=preflight or [self.preflight, self.preflight]), \
                patch.object(review_dispatch.worker_dispatch, 'evaluate', return_value=gate), \
                patch.object(review_dispatch, '_resolve_github_login', return_value='reviewer-user'):
            return review_dispatch.dispatch_review(
                store=self.store, task_id=TASK, executor_task='executor-d6',
                pr_number=42, version=1, create_d6=create, dispatch_session=send,
                bind_session=bind, prompt_renderer=lambda *_: 'bound prompt',
                codex_runner=codex_runner)

    def test_happy_path_claims_creates_dispatches_and_binds(self):
        result = self.invoke()
        self.assertEqual(result['state'], 'DISPATCHED')
        self.assertEqual(result['reviewer_task'][:9], 'reviewer-')
        self.assertEqual(result['native_session_id'], 'native-session')
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.created[0]['github_login'], 'reviewer-user')
        self.assertTrue(self.created[0]['task_id'].startswith('reviewer-'))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.bound), 1)
        self.assertEqual(self.bound[0]['session_id'], 'native-session')

    def test_replay_after_restart_returns_claim_without_redispatch(self):
        first = self.invoke()
        restarted = DurableFakeStore(self.store.path)
        second = self.invoke_store(restarted)
        self.assertEqual(second, first)
        self.assertEqual(len(self.sent), 1)

    def test_new_head_and_version_get_a_separate_one_shot_claim(self):
        def dispatch(head, version):
            executor = self.executor
            preflight = (TASK, executor, head, 'd' * 40, 'claude-reviewer')
            with patch.object(review_dispatch, '_preflight', side_effect=[preflight, preflight]), \
                    patch.object(review_dispatch.worker_dispatch, 'evaluate',
                                 return_value=SimpleNamespace(allowed=True, reason_code='')), \
                    patch.object(review_dispatch, '_resolve_github_login',
                                 return_value='reviewer-user'):
                return review_dispatch.dispatch_review(
                    store=self.store, task_id=TASK, executor_task='executor-d6',
                    pr_number=42, version=version,
                    create_d6=lambda **_: None,
                    dispatch_session=lambda **_: 'native-' + str(version),
                    bind_session=lambda **_: None,
                    prompt_renderer=lambda *_: 'prompt')

        first = dispatch(HEAD, 1)
        replay = dispatch(HEAD, 1)
        later = dispatch('e' * 40, 2)
        self.assertEqual(replay, first)
        self.assertEqual(later['state'], 'DISPATCHED')
        self.assertEqual(len(self.store.get(TASK)['review_dispatch_claim']), 2)
        self.assertEqual(len(self.sent), 0)

    def test_github_login_resolution_uses_authenticated_account_and_validates_output(self):
        result = SimpleNamespace(returncode=0, stdout=b'authenticated-account\n')
        with patch.object(review_dispatch.shutil, 'which', return_value='/bin/gh') as which, \
                patch.object(review_dispatch.subprocess, 'run', return_value=result) as run:
            self.assertEqual(review_dispatch._resolve_github_login(), 'authenticated-account')
        which.assert_called_once_with('gh', path=review_dispatch.task_completion._GH_PATH)
        self.assertEqual(run.call_args.args[0], [
            '/bin/gh', 'api', 'user', '--jq', '.login'])
        for invalid in (SimpleNamespace(returncode=1, stdout=b''),
                        SimpleNamespace(returncode=0, stdout=b'not a login')):
            with patch.object(review_dispatch.shutil, 'which', return_value='/bin/gh'), \
                    patch.object(review_dispatch.subprocess, 'run', return_value=invalid), \
                    self.assertRaises(LLMToolError) as raised:
                review_dispatch._resolve_github_login()
            self.assertEqual(raised.exception.review_dispatch_code,
                             'REVIEWER_GITHUB_LOGIN_UNAVAILABLE')

    def test_github_login_unavailable_prevents_reviewer_d6_creation(self):
        error = LLMToolError('Reviewer dispatch refused; details omitted')
        error.review_dispatch_code = 'REVIEWER_GITHUB_LOGIN_UNAVAILABLE'
        with patch.object(review_dispatch, '_preflight',
                          side_effect=[self.preflight, self.preflight]), \
                patch.object(review_dispatch.worker_dispatch, 'evaluate',
                             return_value=SimpleNamespace(allowed=True, reason_code='')), \
                patch.object(review_dispatch, '_resolve_github_login', side_effect=error):
            with self.assertRaises(LLMToolError):
                review_dispatch.dispatch_review(
                    store=self.store, task_id=TASK, executor_task='executor-d6',
                    pr_number=42, create_d6=lambda **kw: self.created.append(kw),
                    dispatch_session=lambda **kw: self.sent.append(kw),
                    prompt_renderer=lambda *_: 'bound prompt')
        self.assertEqual(self.created, [])
        self.assertEqual(self.sent, [])

    def invoke_store(self, store):
        original = self.store
        self.store = store
        try:
            return self.invoke()
        finally:
            self.store = original

    def test_crash_after_claim_becomes_unknown_and_is_not_retried(self):
        with self.assertRaises(LLMToolError):
            self.invoke(create=lambda **_: (_ for _ in ()).throw(RuntimeError('crash')))
        self.assertEqual(self.store.get(TASK)['review_dispatch_claim'][0]['state'], 'UNKNOWN')
        before = len(self.sent)
        result = self.invoke_store(DurableFakeStore(self.store.path))
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertEqual(len(self.sent), before)

    def test_head_moved_before_session_dispatch_is_refused(self):
        moved = (TASK, self.executor, 'e' * 40, 'd' * 40, 'claude-reviewer')
        with self.assertRaises(LLMToolError):
            self.invoke(preflight=[self.preflight, moved])
        self.assertEqual(self.sent, [])
        self.assertEqual(self.store.get(TASK)['review_dispatch_claim'][0]['state'], 'UNKNOWN')

    def test_claude_authored_task_dispatches_codex_in_reviewer_d6(self):
        self.store.data.update({
            'developer': {'provider_profile': 'claude'},
            'run_id': 'r' * 32,
            'task_binding': {'contract_digest': 'd' * 64},
        })
        reviewer = SimpleNamespace(root='/repo/reviewer-d6')
        create_args, runner_args = [], []
        def create(**kwargs):
            create_args.append(kwargs)
        def run_codex(**kwargs):
            runner_args.append(kwargs)
            return {'thread_id': '0123456789abcdef',
                    'review_text': '{"verdict":"PASS"}', 'verdict': 'PASS'}
        with patch.object(review_dispatch.task_completion, 'read_d6_registration',
                          return_value=reviewer):
            result = self.invoke(
                preflight=[(TASK, self.executor, HEAD, 'd' * 40, 'codex-reviewer'),
                           (TASK, self.executor, HEAD, 'd' * 40, 'codex-reviewer')],
                create=create, codex_runner=run_codex)
        self.assertEqual(result['state'], 'DISPATCHED')
        self.assertEqual(create_args[0]['reviewer_key'], 'codex-reviewer')
        self.assertEqual(runner_args[0]['reviewer_root'], '/repo/reviewer-d6')
        self.assertEqual(runner_args[0]['head'], HEAD)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.bound[0]['session_id'], '0123456789abcdef')
        self.assertEqual(result['review_text'], '{"verdict":"PASS"}')
        self.assertEqual(result['verdict'], 'PASS')

    def test_codex_argv_is_read_only_and_uses_required_profile_and_d6(self):
        verdict = {'task_id': TASK, 'run_id': 'r' * 32,
                   'contract_digest': 'd' * 64, 'head': HEAD, 'verdict': 'PASS'}
        final_review = json.dumps(verdict)
        events = [
            {'type': 'thread.started', 'thread_id': '0123456789abcdef'},
            {'type': 'item.completed', 'item': {
                'type': 'agent_message', 'text': 'intermediate message'}},
            {'type': 'item.completed', 'item': {
                'type': 'agent_message', 'text': final_review}},
        ]
        result = SimpleNamespace(returncode=0, stdout='\n'.join(
            json.dumps(event) for event in events).encode('utf-8'))
        with patch.object(review_dispatch.shutil, 'which', return_value='/bin/codex'), \
                patch.object(review_dispatch.subprocess, 'run', return_value=result) as run:
            review_result = review_dispatch._dispatch_codex(
                reviewer_root='/repo/reviewer-d6', prompt='review prompt',
                task={'task_id': TASK, 'run_id': 'r' * 32,
                      'task_binding': {'contract_digest': 'd' * 64}}, head=HEAD)
        argv = run.call_args.args[0]
        self.assertIn('-s', argv)
        self.assertEqual(argv[argv.index('-s') + 1], 'read-only')
        self.assertEqual(argv[argv.index('--model') + 1], 'gpt-6-luna')
        self.assertIn('model_reasoning_effort=high', argv)
        self.assertEqual(argv[argv.index('--cd') + 1], '/repo/reviewer-d6')
        self.assertEqual(run.call_args.kwargs['cwd'], '/repo/reviewer-d6')
        self.assertEqual(run.call_args.kwargs['timeout'], 600)
        self.assertEqual(review_result, {'thread_id': '0123456789abcdef',
                                         'review_text': final_review,
                                         'verdict': 'PASS'})
        self.assertEqual(run.call_args.kwargs['env']['PATH'], '/bin:/usr/bin:/bin')
        for flag in ('--no-daemon', '--ignore-user-config', '--ignore-rules',
                     '--ephemeral', '--strict-config'):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index('--ask-for-approval') + 1], 'never')

    def test_malformed_or_misbinding_codex_verdict_is_refused(self):
        raw = (b'{"type":"item.completed","item":{"type":"agent_message",'
               b'"text":"{\\"task_id\\":\\"wrong\\",\\"run_id\\":\\"run\\",'
               b'\\"contract_digest\\":\\"digest\\",\\"head\\":\\"head\\",'
               b'\\"verdict\\":\\"PASS\\"}"}}')
        with self.assertRaises(LLMToolError) as raised:
            review_dispatch._parse_codex_review(
                raw, task_id=TASK, run_id='run', contract_digest='digest', head=HEAD)
        self.assertEqual(raised.exception.review_dispatch_code, 'REVIEW_NOT_POSTED')

    def test_reviewer_selection_is_cross_family_and_unknown_is_refused(self):
        executor = SimpleNamespace(
            result_sha=HEAD, root='/repo/worktree', common_dir='/repo/.git', branch='task')
        snapshot = SimpleNamespace(
            head=HEAD, root='/repo/worktree', common_dir='/repo/.git', branch='task',
            clean=True)
        task = {
            'task_id': TASK, 'run_id': 'r' * 32,
            'task_binding': {'contract_digest': 'd' * 64},
            'developer': {'provider_profile': 'claude'},
            'completion': {'executor_task_id': 'executor-d6',
                           'evidence': {'common_dir': '/repo/.git'}},
        }
        pull = {'number': 42, 'head': {'sha': HEAD}, 'base': {'sha': 'c' * 40}}
        with patch.object(review_dispatch.task_completion, 'validate_task_binding'), \
                patch.object(review_dispatch.task_completion, 'read_d6_registration',
                             return_value=executor), \
                patch.object(review_dispatch.task_completion, 'collect_git_snapshot',
                             return_value=snapshot), \
                patch.object(review_dispatch.task_completion, '_github_get',
                             return_value=pull):
            self.assertEqual(review_dispatch._preflight(
                self.store, TASK, 'executor-d6', 42, 1, task=task)[4], 'codex-reviewer')
            task['developer']['provider_profile'] = 'codex'
            self.assertEqual(review_dispatch._preflight(
                self.store, TASK, 'executor-d6', 42, 1, task=task)[4], 'claude-reviewer')
            for family in ('opencode', 'company-gw', 'gw', 'agy'):
                task['developer']['provider_profile'] = family
                self.assertEqual(review_dispatch._preflight(
                    self.store, TASK, 'executor-d6', 42, 1, task=task)[4],
                    'claude-reviewer')
            task['developer']['provider_profile'] = 'unknown'
            with self.assertRaises(LLMToolError) as raised:
                review_dispatch._preflight(
                    self.store, TASK, 'executor-d6', 42, 1, task=task)
        self.assertEqual(raised.exception.review_dispatch_code, 'REVIEWER_FAMILY_UNSUPPORTED')

    def test_paused_worker_policy_refuses_before_claim_or_side_effect(self):
        with self.assertRaises(LLMToolError):
            self.invoke(policy=False)
        self.assertNotIn('review_dispatch_claim', self.store.get(TASK))
        self.assertEqual(self.created, [])

    def test_wrong_family_is_never_selected(self):
        with patch.object(review_dispatch, '_preflight', side_effect=LLMToolError('unsupported')):
            with self.assertRaises(LLMToolError):
                review_dispatch.dispatch_review(
                    store=self.store, task_id=TASK, executor_task='executor-d6',
                    pr_number=42, create_d6=lambda **kw: self.created.append(kw))
        self.assertEqual(self.created, [])
        self.assertNotIn('review_dispatch_claim', self.store.get(TASK))

    def test_dirty_executor_refuses_before_claim_or_adapter_calls(self):
        task = {
            'task_id': TASK, 'run_id': 'run',
            'task_binding': {'contract_digest': 'd' * 64},
            'completion': {'executor_task_id': 'executor-d6',
                           'evidence': {'common_dir': '/repo/.git'}},
        }
        self.store.data = dict(task, record_sha256='c' * 64)
        before = self.store.get(TASK)
        executor = SimpleNamespace(
            result_sha=HEAD, root='/repo/worktree', common_dir='/repo/.git', branch='task')
        snapshot = SimpleNamespace(
            head=HEAD, root='/repo/worktree', common_dir='/repo/.git', branch='task',
            clean=False)
        adapters = []
        with patch.object(review_dispatch.task_completion, 'validate_task_binding'), \
                patch.object(review_dispatch.task_completion, 'read_d6_registration',
                             return_value=executor), \
                patch.object(review_dispatch.task_completion, 'collect_git_snapshot',
                             return_value=snapshot):
            with self.assertRaises(LLMToolError) as raised:
                review_dispatch.dispatch_review(
                    store=self.store, task_id=TASK, executor_task='executor-d6',
                    pr_number=42,
                    create_d6=lambda **kwargs: adapters.append(('create', kwargs)),
                    dispatch_session=lambda **kwargs: adapters.append(('dispatch', kwargs)),
                    bind_session=lambda **kwargs: adapters.append(('bind', kwargs)))
        self.assertEqual(raised.exception.review_dispatch_code,
                         'REVIEW_PROMPT_EXECUTOR_DIRTY')
        self.assertEqual(adapters, [])
        self.assertEqual(self.store.get(TASK), before)


    def test_cli_routes_review_dispatch_to_dispatcher(self):
        with patch('llmtool.source_freshness.require_fresh_source', return_value='OK'), \
                patch.object(task_submission_cli, 'run_task_review_dispatch', return_value=17) as dispatch, \
                patch.object(cli, 'run_task_inspect', side_effect=AssertionError('inspector called')):
            self.assertEqual(cli.main(['task', 'review-dispatch', TASK,
                                       '--executor-task', 'executor-d6', '--pr', '42']), 17)
        dispatch.assert_called_once()

    def test_cli_prints_review_dispatch_refusal_code(self):
        codes = ('PAUSED', 'REVIEWER_ROUTE_UNIMPLEMENTED',
                 'REVIEW_PROMPT_PR_HEAD_MISMATCH',
                 'REVIEW_DISPATCH_CLAIM_MISMATCH', 'REVIEW_DISPATCH_OUTCOME_UNKNOWN')
        for code in codes:
            error = LLMToolError('Reviewer dispatch refused; details omitted')
            error.review_dispatch_code = code
            output = io.StringIO()
            with self.subTest(code=code), \
                    patch('llmtool.source_freshness.require_fresh_source', return_value='OK'), \
                    patch.object(
                    task_submission_cli, 'run_task_review_dispatch', side_effect=error), \
                    redirect_stderr(output):
                self.assertEqual(cli.main(['task', 'review-dispatch', TASK,
                                           '--executor-task', 'executor-d6', '--pr', '42']), 1)
            self.assertIn('llmtool: review-dispatch refused: ' + code, output.getvalue())


def _real_store(tmp_path):
    task_id, run_id = 'a' * 32, 'b' * 32
    contract = {
        'schema_version': 1, 'contract_version': 1, 'task_id': task_id,
        'goal': 'test formal review dispatch claims',
        'authorized_scope': ['pm-planning'],
        'criteria': [{'criterion_id': 'pm_response',
                      'description': 'Record the selected PM response or a truthful unavailable outcome.',
                      'kind': 'ARTIFACT'}],
        'forbidden_scope': [],
        'previous_digest': None,
    }
    parsed = task_store.parse_task_contract(json.dumps(contract))
    record = {
        'schema_version': 2, 'task_id': task_id, 'run_id': run_id,
        'pm_session_id': 'pm-test', 'pm_provider_profile': 'test',
        'pm_provider_type': 'test', 'pm_model': None,
        'pm_route_binding': {'type': 'test', 'model': None, 'base_url': None},
        'backend_session_semantics': 'NOT_STARTED', 'native_session_id': None,
        'state': 'PM_PENDING', 'created_at': '2026-10-27T00:00:00Z',
        'updated_at': '2026-10-27T00:00:00Z', 'task_contract': contract,
        'task_binding': vars(parsed.binding(run_id)),
        'request': 'test formal review dispatch claims',
        'request_sha256': hashlib.sha256(
            b'test formal review dispatch claims').hexdigest(),
        'response': None, 'usage': None,
        'error_code': None, 'developer': None, 'pm_assignment': None,
        'completion': None,
    }
    store = task_store.TaskStore(Path(tmp_path).resolve())
    store.create(record)
    return store, task_id


def _claim(*, head=HEAD, version=1, reviewer='reviewer-a'):
    stamp = '2026-10-27T00:00:00Z'
    return {'executor_task': 'executor-d6', 'pr': 42, 'head_sha': head,
            'version': version, 'state': 'CLAIMED', 'reviewer_task': reviewer,
            'native_session_id': None, 'account_mode': 'DEFAULT_SECOND_ACCOUNT',
            'claimed_at': stamp, 'updated_at': stamp}


class RealTaskStoreReviewClaimTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store, self.task_id = _real_store(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def update_claims(self, expected, claims):
        self.store.compare_and_update(
            self.task_id, expected={'record_sha256': expected['record_sha256']},
            review_dispatch_claim=claims)

    def test_claimed_transitions_to_dispatched(self):
        initial = self.store.get(self.task_id)
        claimed = _claim()
        self.update_claims(initial, [claimed])
        current = self.store.get(self.task_id)
        dispatched = dict(claimed, state='DISPATCHED', native_session_id='native-1')
        self.update_claims(current, [dispatched])
        self.assertEqual(self.store.get(self.task_id)['review_dispatch_claim'], [dispatched])

    def test_claimed_transitions_to_unknown(self):
        initial = self.store.get(self.task_id)
        claimed = _claim()
        self.update_claims(initial, [claimed])
        current = self.store.get(self.task_id)
        unknown = dict(claimed, state='UNKNOWN', updated_at='2026-10-27T00:01:00Z')
        self.update_claims(current, [unknown])
        self.assertEqual(self.store.get(self.task_id)['review_dispatch_claim'], [unknown])

    def test_rejects_illegal_transition_and_record_corruption(self):
        initial = self.store.get(self.task_id)
        claimed = _claim()
        self.update_claims(initial, [claimed])
        current = self.store.get(self.task_id)
        with self.assertRaises(LLMToolError):
            self.update_claims(current, [dict(claimed, reviewer_task='reviewer-b')])
        with self.assertRaises(LLMToolError) as raised:
            self.update_claims(current, [claimed, dict(_claim(version=2), pr=0)])
        self.assertEqual(getattr(raised.exception, 'task_store_error_code', None),
                         'REVIEW_DISPATCH_CLAIM_INVALID')

    def test_new_head_and_version_get_new_claim_but_same_key_is_one_shot(self):
        claim1 = _claim()
        self.update_claims(self.store.get(self.task_id), [claim1])
        current = self.store.get(self.task_id)
        claim2 = _claim(head='e' * 40, version=2, reviewer='reviewer-b')
        self.update_claims(current, [claim1, claim2])
        saved = self.store.get(self.task_id)
        self.assertEqual(saved['review_dispatch_claim'], [claim1, claim2])
        with self.assertRaises(LLMToolError) as raised:
            self.update_claims(saved, [claim1, claim2, dict(claim1)])
        self.assertEqual(getattr(raised.exception, 'task_store_error_code', None),
                         'REVIEW_DISPATCH_CLAIM_INVALID')


if __name__ == '__main__':
    unittest.main()
