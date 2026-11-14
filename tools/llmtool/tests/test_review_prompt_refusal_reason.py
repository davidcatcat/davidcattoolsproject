"""#1182: a review prompt refused before any session starts leaves a named claim."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import review_dispatch, task_advance, task_store
from llmtool.errors import LLMToolError

import review_trust_fixture


HEAD = 'b' * 40
PR = 42
EXECUTOR = 'executor-d6'
REASON = 'REVIEW_PROMPT_REFUSED'
RENDER_DETAIL = 'renderer-detail-must-not-be-kept'
THREAD = '01234567-89ab-cdef-0123-456789abcdef'
# #1502 WP-F: a fixed rule prefix stands in for the main-pinned Reviewer cards,
# so no test here reads GitHub or Git for rules.
RULES_PREFIX = 'RULES stub for review prompt refusal tests\nRULES_END\n\n'
RULES_SHA256 = hashlib.sha256(RULES_PREFIX.encode('utf-8')).hexdigest()


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def _real_store(root):
    task_id, run_id = 'a' * 32, 'c' * 32
    contract = {
        'schema_version': 1, 'contract_version': 1, 'task_id': task_id,
        'goal': 'test review prompt refusal claims',
        'authorized_scope': ['pm-planning'],
        'criteria': [{'criterion_id': 'pm_response',
                      'description': 'Record the selected PM response or a truthful unavailable outcome.',
                      'kind': 'ARTIFACT'}],
        'forbidden_scope': [],
        'previous_digest': None,
    }
    parsed = task_store.parse_task_contract(json.dumps(contract))
    request = 'test review prompt refusal claims'
    record = {
        'schema_version': 2, 'task_id': task_id, 'run_id': run_id,
        'pm_session_id': 'pm-test', 'pm_provider_profile': 'test',
        'pm_provider_type': 'test', 'pm_model': None,
        'pm_route_binding': {'type': 'test', 'model': None, 'base_url': None},
        'backend_session_semantics': 'NOT_STARTED', 'native_session_id': None,
        'state': 'PM_PENDING', 'created_at': '2026-11-08T00:00:00Z',
        'updated_at': '2026-11-08T00:00:00Z', 'task_contract': contract,
        'task_binding': vars(parsed.binding(run_id)),
        'request': request,
        'request_sha256': hashlib.sha256(request.encode('utf-8')).hexdigest(),
        'response': None, 'usage': None,
        'error_code': None, 'developer': None, 'pm_assignment': None,
        'completion': None,
    }
    store = task_store.TaskStore(Path(root).resolve())
    store.create(record)
    return store, task_id


def _render_error(*, completion_code=None, review_dispatch_code=None):
    error = LLMToolError(RENDER_DETAIL)
    if completion_code is not None:
        error.completion_code = completion_code
    if review_dispatch_code is not None:
        error.review_dispatch_code = review_dispatch_code
    return error


class ReviewPromptRefusalReasonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        home = Path(self.temp.name) / 'home'
        home.mkdir()
        env_patch = patch.dict(os.environ, {'HOME': str(home)})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        rules_patch = patch.object(review_dispatch, '_review_rules',
                                   return_value=(RULES_PREFIX, RULES_SHA256))
        rules_patch.start()
        self.addCleanup(rules_patch.stop)
        self.root = Path(self.temp.name) / 'store'
        self.store, self.task_id = _real_store(self.root)
        self.create_calls, self.codex_calls = [], []
        self.send_calls, self.bind_calls = [], []

    def create(self, **kwargs):
        self.create_calls.append(kwargs)

    def send(self, **kwargs):
        self.send_calls.append(kwargs)
        return 'session-must-not-start'

    def dispatch(self, *, version=1, renderer=None, codex_runner=None, bind=None,
                 extra_patches=()):
        executor = SimpleNamespace(root='/repo/executor', common_dir='/repo/.git')
        preflight = (self.task_id, executor, HEAD, 'e' * 40, 'codex-reviewer')
        role = {'source': 'roles', 'config_dir': None, 'provider': 'codex',
                'model': None, 'effort': None}

        def runner(**kwargs):
            self.codex_calls.append(kwargs)
            if codex_runner is None:
                self.fail('Codex runner called')
            return codex_runner(**kwargs)

        def binder(**kwargs):
            self.bind_calls.append(kwargs)
            if bind is not None:
                bind(**kwargs)

        patches = [
            patch.object(review_dispatch, '_preflight', return_value=preflight),
            patch.object(review_dispatch.worker_dispatch, 'evaluate',
                         return_value=SimpleNamespace(allowed=True, reason_code='')),
            patch.object(review_dispatch, '_resolve_github_login',
                         return_value='reviewer-user'),
            patch.object(review_dispatch.role_config, 'resolve_role', return_value=role),
            patch.object(review_dispatch.task_completion, 'read_d6_registration',
                         return_value=SimpleNamespace(root='/repo/reviewer')),
            *extra_patches,
        ]
        for item in patches:
            item.start()
        try:
            with self.assertRaises(LLMToolError) as raised:
                review_dispatch.dispatch_review(
                    store=self.store, task_id=self.task_id, executor_task=EXECUTOR,
                    pr_number=PR, version=version, create_d6=self.create,
                    dispatch_session=self.send, bind_session=binder,
                    prompt_renderer=renderer, codex_runner=runner)
        finally:
            for item in reversed(patches):
                item.stop()
        return raised.exception

    def claims(self):
        return self.store.get(self.task_id)['review_dispatch_claim']

    def decide(self):
        record = dict(self.store.get(self.task_id),
                      developer={'state': 'DEVELOPER_RESPONDED'},
                      completion={'executor_task_id': EXECUTOR})
        d6 = {'result_head': HEAD, 'recorded_result_head': HEAD, 'dirty': False}
        pr = {'number': PR, 'head': {'sha': HEAD}, 'merged': False}
        with patch.object(task_advance, '_remote_test_status',
                          return_value=('PASS', None, None)):
            return task_advance.decide(record, d6, pr, {})

    def assert_no_session_started(self):
        self.assertEqual(len(self.create_calls), 1)
        self.assertEqual(self.codex_calls, [])
        self.assertEqual(self.send_calls, [])
        self.assertEqual(self.bind_calls, [])

    def assert_named_unknown(self, exc, code):
        self.assertEqual(exc.review_dispatch_code, code)
        self.assertEqual(exc.review_failure_reason, REASON)
        self.assertIsNone(exc.__cause__)
        self.assertTrue(exc.__suppress_context__)
        [claim] = self.claims()
        self.assertEqual(claim['state'], 'UNKNOWN')
        self.assertEqual(claim['failure_reason'], REASON)
        self.assertEqual((claim['pr'], claim['head_sha'], claim['version']), (PR, HEAD, 1))
        self.assertEqual(claim['rules_sha256'], RULES_SHA256)
        self.assertIsNone(claim['native_session_id'])
        stored = json.dumps(self.store.get(self.task_id))
        self.assertNotIn(RENDER_DETAIL, stored)
        self.assertNotIn(RENDER_DETAIL, str(exc))
        # A reopened store re-validates the saved claim from disk.
        reopened = task_store.TaskStore(self.root.resolve())
        self.assertEqual(reopened.get(self.task_id)['review_dispatch_claim'], [claim])
        self.assert_no_session_started()

    def test_reason_joins_the_fixed_set_and_create_reasons_are_unchanged(self):
        self.assertIn(REASON, review_dispatch.REVIEW_FAILURE_REASONS)
        self.assertNotIn(REASON, review_dispatch._D6_CREATE_FAILURE_REASONS)
        self.assertEqual(review_dispatch._D6_CREATE_FAILURE_REASONS, frozenset((
            'REVIEWER_D6_CREATE_FAILED', 'REVIEWER_D6_CREATE_TIMEOUT')))

    def test_renderer_completion_code_refusal_names_the_claim(self):
        def renderer(*_):
            raise _render_error(completion_code='REVIEW_PROMPT_EXECUTOR_HEAD_MISMATCH')
        exc = self.dispatch(renderer=renderer)
        self.assert_named_unknown(exc, 'REVIEW_PROMPT_EXECUTOR_HEAD_MISMATCH')

    def test_review_dispatch_code_is_kept_when_there_is_no_completion_code(self):
        def renderer(*_):
            raise _render_error(review_dispatch_code='REVIEW_PROMPT_PR_HEAD_MISMATCH')
        exc = self.dispatch(renderer=renderer)
        self.assert_named_unknown(exc, 'REVIEW_PROMPT_PR_HEAD_MISMATCH')

    def test_completion_code_wins_over_review_dispatch_code(self):
        def renderer(*_):
            raise _render_error(completion_code='REVIEW_PROMPT_D6_UNAVAILABLE',
                                review_dispatch_code='REVIEW_PROMPT_PR_UNAVAILABLE')
        exc = self.dispatch(renderer=renderer)
        self.assert_named_unknown(exc, 'REVIEW_PROMPT_D6_UNAVAILABLE')

    def test_missing_or_free_text_code_falls_back_to_the_reason(self):
        cases = (
            _render_error(),
            _render_error(completion_code='not an identifier'),
            _render_error(completion_code=7, review_dispatch_code='lower_case'),
            _render_error(review_dispatch_code='REVIEW PROMPT'),
        )
        for index, error in enumerate(cases):
            with self.subTest(case=index):
                self.root = Path(self.temp.name) / f'case-{index}'
                self.store, self.task_id = _real_store(self.root)
                self.create_calls = []

                def renderer(*_, error=error):
                    raise error
                exc = self.dispatch(renderer=renderer)
                self.assert_named_unknown(exc, REASON)

    def test_default_renderer_refusal_names_the_claim(self):
        from llmtool import task_submission_cli
        error = _render_error(completion_code='REVIEW_PROMPT_EXECUTOR_HEAD_MISMATCH')
        render = patch.object(task_submission_cli, 'run_task_review_prompt',
                              side_effect=error)
        exc = self.dispatch(renderer=None, extra_patches=(render,))
        self.assert_named_unknown(exc, 'REVIEW_PROMPT_EXECUTOR_HEAD_MISMATCH')

    def test_non_llmtool_renderer_error_keeps_outcome_unknown(self):
        def renderer(*_):
            raise RuntimeError(RENDER_DETAIL)
        exc = self.dispatch(renderer=renderer)
        self.assertEqual(exc.review_dispatch_code, 'REVIEW_DISPATCH_OUTCOME_UNKNOWN')
        self.assertIsNone(getattr(exc, 'review_failure_reason', None))
        [claim] = self.claims()
        self.assertEqual(claim['state'], 'UNKNOWN')
        self.assertNotIn('failure_reason', claim)
        self.assertEqual(self.decide(), 'REVIEW_OUTCOME_UNKNOWN')
        self.assert_no_session_started()

    def test_refusal_after_session_starts_records_no_new_reason(self):
        def runner(**_):
            return {'thread_id': THREAD, 'review_text': 'text', 'verdict': 'PASS'}

        def bind(**_):
            raise _render_error(completion_code='REVIEW_PROMPT_EXECUTOR_HEAD_MISMATCH',
                                review_dispatch_code='REVIEW_PROMPT_EXECUTOR_HEAD_MISMATCH')
        exc = self.dispatch(renderer=lambda *_: 'prompt', codex_runner=runner, bind=bind)
        self.assertEqual(exc.review_dispatch_code, 'REVIEW_PROMPT_EXECUTOR_HEAD_MISMATCH')
        self.assertIsNone(getattr(exc, 'review_failure_reason', None))
        [claim] = self.claims()
        self.assertEqual(claim['state'], 'UNKNOWN')
        self.assertEqual(claim['native_session_id'], THREAD)
        self.assertNotIn('failure_reason', claim)
        self.assertEqual(len(self.codex_calls), 1)
        self.assertEqual(self.codex_calls[0]['prompt'], RULES_PREFIX + 'prompt')
        self.assertEqual(len(self.bind_calls), 1)
        self.assertEqual(self.decide(), 'REVIEW_OUTCOME_UNKNOWN')

    def test_first_refusal_redispatches_and_second_on_head_stops(self):
        def renderer(*_):
            raise _render_error(completion_code='REVIEW_PROMPT_EXECUTOR_HEAD_MISMATCH')
        exc = self.dispatch(renderer=renderer)
        self.assertEqual(exc.review_failure_reason, REASON)
        [first] = self.claims()
        self.assertEqual(first['failure_reason'], REASON)
        self.assertEqual(self.decide(), 'REVIEW_REDISPATCH')

        exc = self.dispatch(version=2, renderer=renderer)
        self.assertEqual(exc.review_failure_reason, REASON)
        claims = self.claims()
        self.assertEqual(len(claims), 2)
        self.assertEqual(claims[0], first)
        self.assertEqual(claims[1]['state'], 'UNKNOWN')
        self.assertEqual(claims[1]['failure_reason'], REASON)
        self.assertEqual(claims[1]['version'], 2)
        self.assertEqual(self.decide(), 'REVIEW_UNUSABLE_STOPPED')
        self.assertEqual(self.codex_calls, [])
        self.assertEqual(self.send_calls, [])
        self.assertEqual(self.bind_calls, [])


if __name__ == '__main__':
    unittest.main()
