"""#1182: a refusal while rendering the Reviewer prompt names a fixed reason."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from llmtool import review_dispatch, task_advance
from llmtool.errors import LLMToolError


TASK = 'a' * 32
HEAD = 'b' * 40
REASON = 'REVIEW_PROMPT_REFUSED'


class MemoryStore:
    """Minimal TaskStore seam: get plus compare-and-update."""

    def __init__(self):
        self.data = {'record_sha256': 'c' * 64}

    def get(self, task_id):
        assert task_id == TASK
        return dict(self.data)

    def compare_and_update(self, task_id, *, expected, **changes):
        assert task_id == TASK
        if any(self.data.get(key) != value for key, value in expected.items()):
            raise RuntimeError('conflict')
        self.data.update(changes)
        return dict(self.data)


def _prompt_error(completion_code=None, review_dispatch_code=None):
    error = LLMToolError('Reviewer prompt is unavailable; details omitted')
    if completion_code is not None:
        error.completion_code = completion_code
    if review_dispatch_code is not None:
        error.review_dispatch_code = review_dispatch_code
    return error


def _raise(error):
    def call(*_args, **_kwargs):
        raise error
    return call


class PromptRefusalReasonTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.store.data.update({'developer': {'provider_profile': 'claude'},
                                'run_id': 'c' * 32,
                                'task_binding': {'contract_digest': 'd' * 64}})
        self.executor = SimpleNamespace(root='/repo/worktree', common_dir='/repo/.git')
        self.created, self.sent, self.bound, self.codex = [], [], [], []

    def _role(self, role):
        if role == 'reviewer':
            return {'config_dir': '/private/test-reviewer-account', 'source': 'roles'}
        return {'provider': 'codex', 'config_dir': None, 'source': 'roles'}

    def dispatch(self, *, reviewer_key='claude-reviewer', renderer=None, preflight=None,
                 send=None, bind=None, codex_runner=None):
        first = (TASK, self.executor, HEAD, 'd' * 40, reviewer_key)
        renderer = renderer or (lambda *_: 'bound prompt')
        send = send or (lambda **kw: (self.sent.append(kw) or 'native-session'))
        bind = bind or (lambda **kw: self.bound.append(kw))
        codex_runner = codex_runner or (lambda **kw: self.codex.append(kw) or {
            'thread_id': '0123456789abcdef', 'review_text': '', 'verdict': 'BLOCKED'})
        gate = SimpleNamespace(allowed=True, reason_code='')
        with patch.object(review_dispatch, '_preflight',
                          side_effect=preflight or [first, first]), \
                patch.object(review_dispatch.worker_dispatch, 'evaluate', return_value=gate), \
                patch.object(review_dispatch, '_resolve_github_login',
                             return_value='reviewer-user'), \
                patch.object(review_dispatch.role_config, 'resolve_role',
                             side_effect=self._role), \
                patch.object(review_dispatch.task_completion, 'read_d6_registration',
                             return_value=SimpleNamespace(root='/repo/reviewer-d6')):
            return review_dispatch.dispatch_review(
                store=self.store, task_id=TASK, executor_task='executor-d6',
                pr_number=42, version=1,
                create_d6=lambda **kw: self.created.append(kw),
                dispatch_session=send, bind_session=bind,
                prompt_renderer=renderer, codex_runner=codex_runner)

    def claim(self):
        claims = self.store.get(TASK)['review_dispatch_claim']
        self.assertEqual(len(claims), 1)
        return claims[0]

    def test_reason_is_a_known_review_failure_reason(self):
        self.assertIn(REASON, review_dispatch.REVIEW_FAILURE_REASONS)
        self.assertEqual(task_advance._failure_reason(REASON), REASON)

    def test_prompt_refusal_records_reason_and_starts_no_reviewer(self):
        for reviewer_key in ('claude-reviewer', 'codex-reviewer'):
            with self.subTest(reviewer=reviewer_key):
                self.setUp()
                error = _prompt_error('REVIEW_PROMPT_EXECUTOR_HEAD_MISMATCH')
                with self.assertRaises(LLMToolError) as raised:
                    self.dispatch(reviewer_key=reviewer_key, renderer=_raise(error))
                self.assertEqual(raised.exception.review_dispatch_code,
                                 'REVIEW_PROMPT_EXECUTOR_HEAD_MISMATCH')
                self.assertEqual(raised.exception.review_failure_reason, REASON)
                claim = self.claim()
                self.assertEqual(claim['state'], 'UNKNOWN')
                self.assertEqual(claim['failure_reason'], REASON)
                self.assertIsNone(claim['native_session_id'])
                self.assertEqual(len(self.created), 1)
                self.assertEqual(self.sent, [])
                self.assertEqual(self.codex, [])
                self.assertEqual(self.bound, [])

    def test_refusal_code_falls_back_to_dispatch_code_then_fixed_name(self):
        cases = (
            (_prompt_error(review_dispatch_code='REVIEW_PROMPT_PR_UNAVAILABLE'),
             'REVIEW_PROMPT_PR_UNAVAILABLE'),
            (_prompt_error('not an identifier', 'REVIEW_PROMPT_PR_HEAD_MISMATCH'),
             'REVIEW_PROMPT_PR_HEAD_MISMATCH'),
            (_prompt_error(), REASON),
            (_prompt_error('lower_case'), REASON),
            (_prompt_error('/private/path secret'), REASON),
        )
        for error, expected in cases:
            with self.subTest(expected=expected):
                self.setUp()
                with self.assertRaises(LLMToolError) as raised:
                    self.dispatch(renderer=_raise(error))
                self.assertEqual(raised.exception.review_dispatch_code, expected)
                self.assertEqual(raised.exception.review_failure_reason, REASON)
                self.assertEqual(self.claim()['failure_reason'], REASON)
                self.assertEqual(self.sent, [])

    def test_non_llmtool_error_from_renderer_stores_no_reason(self):
        with self.assertRaises(LLMToolError) as raised:
            self.dispatch(renderer=_raise(RuntimeError('render crashed')))
        self.assertEqual(raised.exception.review_dispatch_code,
                         'REVIEW_DISPATCH_OUTCOME_UNKNOWN')
        self.assertIsNone(getattr(raised.exception, 'review_failure_reason', None))
        claim = self.claim()
        self.assertEqual(claim['state'], 'UNKNOWN')
        self.assertNotIn('failure_reason', claim)
        self.assertEqual(self.sent, [])

    def test_refusal_after_rendering_before_session_stores_no_reason(self):
        first = (TASK, self.executor, HEAD, 'd' * 40, 'claude-reviewer')
        moved = _prompt_error(review_dispatch_code='REVIEW_PROMPT_EXECUTOR_HEAD_MISMATCH')
        with self.assertRaises(LLMToolError) as raised:
            self.dispatch(preflight=[first, moved])
        self.assertEqual(raised.exception.review_dispatch_code,
                         'REVIEW_PROMPT_EXECUTOR_HEAD_MISMATCH')
        claim = self.claim()
        self.assertEqual(claim['state'], 'UNKNOWN')
        self.assertNotIn('failure_reason', claim)
        self.assertEqual(self.sent, [])

    def test_refusal_after_session_id_exists_stores_no_new_reason(self):
        for reviewer_key in ('claude-reviewer', 'codex-reviewer'):
            with self.subTest(reviewer=reviewer_key):
                self.setUp()
                error = _prompt_error('REVIEW_PROMPT_EXECUTOR_HEAD_MISMATCH',
                                      'REVIEW_PROMPT_EXECUTOR_HEAD_MISMATCH')
                with self.assertRaises(LLMToolError) as raised:
                    self.dispatch(reviewer_key=reviewer_key, bind=_raise(error))
                self.assertIsNone(getattr(raised.exception, 'review_failure_reason', None))
                claim = self.claim()
                self.assertEqual(claim['state'], 'UNKNOWN')
                self.assertIsNotNone(claim['native_session_id'])
                self.assertNotIn('failure_reason', claim)


class PromptRefusalAdvanceTests(unittest.TestCase):
    def _claim(self, version, reviewer_task):
        return {'executor_task': 'executor-d6', 'pr': 42, 'head_sha': HEAD,
                'version': version, 'state': 'UNKNOWN', 'reviewer_task': reviewer_task,
                'native_session_id': None, 'account_mode': 'ROLES',
                'claimed_at': '2026-11-08T00:00:00Z',
                'updated_at': '2026-11-08T00:00:01Z', 'failure_reason': REASON}

    def decide(self, claims):
        record = {'task_id': TASK, 'run_id': 'c' * 32,
                  'developer': {'state': 'DEVELOPER_RESPONDED'},
                  'completion': {'executor_task_id': 'executor-d6'},
                  'review_dispatch_claim': claims}
        d6 = {'result_head': HEAD, 'recorded_result_head': HEAD, 'dirty': False}
        pr = {'number': 42, 'head': {'sha': HEAD}, 'merged': False}
        with patch.object(task_advance, '_remote_test_status',
                          return_value=('PASS', None, None)):
            return task_advance.decide(record, d6, pr, {'events': []})

    def test_first_prompt_refusal_on_a_head_allows_one_redispatch(self):
        self.assertEqual(self.decide([self._claim(1, 'reviewer-a')]), 'REVIEW_REDISPATCH')

    def test_second_claim_on_the_same_head_stops(self):
        claims = [self._claim(1, 'reviewer-a'), self._claim(2, 'reviewer-b')]
        self.assertEqual(self.decide(claims), 'REVIEW_UNUSABLE_STOPPED')

    def test_claim_without_reason_stays_outcome_unknown(self):
        claim = self._claim(1, 'reviewer-a')
        del claim['failure_reason']
        self.assertEqual(self.decide([claim]), 'REVIEW_OUTCOME_UNKNOWN')


if __name__ == '__main__':
    unittest.main()
