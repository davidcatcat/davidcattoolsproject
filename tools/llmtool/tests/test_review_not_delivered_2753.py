"""Tests for #2753: Claude Reviewer NOT_DELIVERED bridge receipts."""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from llmtool import review_dispatch, task_advance
from llmtool.errors import LLMToolError
import test_review_dispatch as trd


NOT_DELIVERED_REASONS = ('SESSION_NOT_STARTED', 'PROMPT_NOT_DELIVERED')
SYNTHETIC_SESSION_ID = '11111111-2222-3333-4444-555555555555'


def setUpModule():
    trd.setUpModule()


def tearDownModule():
    trd.tearDownModule()


def _bridge_proc(payload, *, returncode=1):
    stdout = payload if type(payload) is bytes else json.dumps(payload).encode('utf-8')
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=b'')


class ClaudeBridgeNotDeliveredTests(unittest.TestCase):
    """N1 & N3: _dispatch handling of NOT_DELIVERED and unchanged outcomes."""

    def _run_dispatch(self, proc):
        with patch.object(review_dispatch, '_admit_review_model',
                          return_value='claude-sonnet-4-6'), \
             patch.object(review_dispatch.subprocess, 'run', return_value=proc):
            return review_dispatch._dispatch(
                task_id='reviewer-1',
                prompt='review prompt',
                repo_root='/repo/worktree',
                env={'PATH': '/usr/bin'},
                model='claude-sonnet-4-6',
            )

    def test_not_delivered_reasons_are_in_review_failure_reasons(self):
        for reason in NOT_DELIVERED_REASONS:
            self.assertIn(reason, review_dispatch.REVIEW_FAILURE_REASONS)

    def test_not_delivered_with_target_reason_raises_dispatch_unknown_with_reason(self):
        for reason in NOT_DELIVERED_REASONS:
            with self.subTest(reason=reason):
                proc = _bridge_proc({
                    'schema_version': 1,
                    'result': 'NOT_DELIVERED',
                    'reason': reason,
                    'actual_session_id': None,
                }, returncode=1)
                with self.assertRaises(RuntimeError) as ctx:
                    self._run_dispatch(proc)
                self.assertEqual(ctx.exception.args, ('REVIEWER_DISPATCH_UNKNOWN',))
                self.assertEqual(getattr(ctx.exception, 'review_failure_reason', None), reason)
                self.assertIsNone(getattr(ctx.exception, 'native_session_id', None))

    def test_not_delivered_preserves_actual_session_id_on_error(self):
        for reason in NOT_DELIVERED_REASONS:
            with self.subTest(reason=reason):
                proc = _bridge_proc({
                    'schema_version': 1,
                    'result': 'NOT_DELIVERED',
                    'reason': reason,
                    'actual_session_id': SYNTHETIC_SESSION_ID,
                }, returncode=1)
                with self.assertRaises(RuntimeError) as ctx:
                    self._run_dispatch(proc)
                self.assertEqual(ctx.exception.args, ('REVIEWER_DISPATCH_UNKNOWN',))
                self.assertEqual(getattr(ctx.exception, 'review_failure_reason', None), reason)
                self.assertEqual(getattr(ctx.exception, 'native_session_id', None),
                                 SYNTHETIC_SESSION_ID)

    def test_unchanged_outcomes(self):
        cases = (
            ('unparseable_json', _bridge_proc(b'not-json', returncode=1)),
            ('invalid_utf8', _bridge_proc(b'\xff\xfe', returncode=1)),
            ('nonzero_empty', _bridge_proc(b'', returncode=1)),
            ('other_not_delivered_reason', _bridge_proc({
                'result': 'NOT_DELIVERED', 'reason': 'LAUNCH_FAILED',
            }, returncode=1)),
            ('missing_not_delivered_reason', _bridge_proc({
                'result': 'NOT_DELIVERED',
            }, returncode=1)),
            ('none_not_delivered_reason', _bridge_proc({
                'result': 'NOT_DELIVERED', 'reason': None,
            }, returncode=1)),
            ('rejected_other', _bridge_proc({
                'result': 'REJECTED', 'reason': 'OTHER',
            }, returncode=1)),
        )
        for name, proc in cases:
            with self.subTest(case=name):
                with self.assertRaises(RuntimeError) as ctx:
                    self._run_dispatch(proc)
                self.assertEqual(ctx.exception.args, ('REVIEWER_DISPATCH_UNKNOWN',))
                self.assertFalse(hasattr(ctx.exception, 'review_failure_reason'))

    def test_rejected_owner_refusal_unchanged(self):
        for reason in ('OWNER_ASSIGNMENT_REQUIRED', 'OWNER_PAUSED'):
            with self.subTest(reason=reason):
                proc = _bridge_proc({'result': 'REJECTED', 'reason': reason}, returncode=1)
                with self.assertRaises(RuntimeError) as ctx:
                    self._run_dispatch(proc)
                self.assertEqual(ctx.exception.args, ('REVIEWER_DISPATCH_REJECTED',))
                self.assertEqual(ctx.exception.launch_refusal,
                                 {'result': 'REJECTED', 'reason': reason})
                self.assertFalse(hasattr(ctx.exception, 'review_failure_reason'))

    def test_delivered_receipt_unchanged(self):
        proc = _bridge_proc({
            'result': 'DELIVERED',
            'reason': None,
            'actual_session_id': SYNTHETIC_SESSION_ID,
        }, returncode=0)
        self.assertEqual(self._run_dispatch(proc), SYNTHETIC_SESSION_ID)


class DispatchReviewNotDeliveredStoreTests(unittest.TestCase):
    """N1, N2 & N5: real TaskStore claim write and no bind/post on NOT_DELIVERED."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def _run_dispatch_review(self, store, task_id, receipt):
        executor = SimpleNamespace(root='/repo/worktree', common_dir='/repo/.git')
        preflight = (store.get(task_id), executor, trd.HEAD, 'd' * 40, 'claude-reviewer')
        bind_mock = Mock()
        poster_mock = Mock()
        proc = _bridge_proc(receipt, returncode=1)
        with patch.object(
                review_dispatch.role_config, 'resolve_role',
                return_value={'config_dir': '/private/test-reviewer-account', 'source': 'roles'}), \
             patch.object(review_dispatch, '_preflight', side_effect=[preflight, preflight]), \
             patch.object(review_dispatch.worker_dispatch, 'evaluate',
                          return_value=SimpleNamespace(allowed=True, reason_code='')), \
             patch.object(review_dispatch, '_resolve_github_login', return_value='reviewer-user'), \
             patch.object(review_dispatch, '_review_model', return_value='claude-sonnet-4-6'), \
             patch.object(review_dispatch, '_admit_review_model', return_value='claude-sonnet-4-6'), \
             patch.object(review_dispatch, '_review_rules', return_value=('RULES\n', 'e' * 64)), \
             patch.object(review_dispatch.subprocess, 'run', return_value=proc):
            with self.assertRaises(LLMToolError) as ctx:
                review_dispatch.dispatch_review(
                    store=store,
                    task_id=task_id,
                    executor_task='executor-d6',
                    pr_number=42,
                    version=1,
                    create_d6=lambda **_kw: None,
                    bind_session=bind_mock,
                    prompt_renderer=lambda *_: 'prompt',
                    comment_poster=poster_mock,
                )
        return ctx.exception, store.get(task_id)['review_dispatch_claim'][-1], bind_mock, poster_mock

    def test_real_store_records_failure_reason_without_session_id(self):
        store, task_id = trd._real_store(Path(self.temp.name) / 'no_session')
        err, claim, bind_mock, poster_mock = self._run_dispatch_review(store, task_id, {
            'schema_version': 1,
            'result': 'NOT_DELIVERED',
            'reason': 'SESSION_NOT_STARTED',
            'actual_session_id': None,
        })
        self.assertEqual(getattr(err, 'review_dispatch_code', None), 'REVIEW_DISPATCH_OUTCOME_UNKNOWN')
        self.assertEqual(getattr(err, 'review_failure_reason', None), 'SESSION_NOT_STARTED')
        self.assertEqual(claim['state'], 'UNKNOWN')
        self.assertEqual(claim['failure_reason'], 'SESSION_NOT_STARTED')
        self.assertIsNone(claim['native_session_id'])
        bind_mock.assert_not_called()
        poster_mock.assert_not_called()
        receipt = review_dispatch.read_refusal_receipt(store=store, task_id=task_id, claim=claim)
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt['failure_reason'], 'SESSION_NOT_STARTED')

    def test_real_store_keeps_session_id_without_bind_or_post(self):
        store, task_id = trd._real_store(Path(self.temp.name) / 'with_session')
        err, claim, bind_mock, poster_mock = self._run_dispatch_review(store, task_id, {
            'schema_version': 1,
            'result': 'NOT_DELIVERED',
            'reason': 'PROMPT_NOT_DELIVERED',
            'actual_session_id': SYNTHETIC_SESSION_ID,
        })
        self.assertEqual(getattr(err, 'review_dispatch_code', None), 'REVIEW_DISPATCH_OUTCOME_UNKNOWN')
        self.assertEqual(getattr(err, 'review_failure_reason', None), 'PROMPT_NOT_DELIVERED')
        self.assertEqual(claim['state'], 'UNKNOWN')
        self.assertEqual(claim['failure_reason'], 'PROMPT_NOT_DELIVERED')
        self.assertEqual(claim['native_session_id'], SYNTHETIC_SESSION_ID)
        bind_mock.assert_not_called()
        poster_mock.assert_not_called()


class TaskAdvanceNotDeliveredRedispatchTests(unittest.TestCase):
    """N4: task_advance re-dispatches once and stops on a second unusable claim."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def _base_record(self, claims):
        return {
            'task_id': trd.TASK,
            'run_id': 'run-1',
            'task_binding': {'contract_digest': 'c' * 64},
            'developer': {'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'agy'},
            'published_pr': {'pr': 42, 'head_sha': trd.HEAD},
            'remote_test_receipts': [],
            'completion_events': [],
            'review_dispatch_claim': claims,
        }

    def _d6_snapshot(self):
        return {
            'root': '/repo/worktree',
            'common_dir': '/repo/.git',
            'task_id': 'executor-d6',
            'branch': 'llmtool/dev',
            'result_head': trd.HEAD,
            'recorded_result_head': trd.HEAD,
            'dirty': False,
            'state': 'CLEAN',
        }

    def _pr_snapshot(self):
        return {
            'number': 42,
            'state': 'open',
            'merged': False,
            'head': {'sha': trd.HEAD},
        }

    def test_decide_redispatches_once_and_stops_on_second_unusable_claim(self):
        d6 = self._d6_snapshot()
        pr = self._pr_snapshot()
        related = {'events': [], 'repair_claims': []}
        for reason in NOT_DELIVERED_REASONS:
            with self.subTest(reason=reason):
                first = {
                    'pr': 42, 'head_sha': trd.HEAD, 'version': 1,
                    'state': 'UNKNOWN', 'failure_reason': reason,
                    'native_session_id': None,
                }
                self.assertEqual(
                    task_advance.decide(self._base_record([first]), d6, pr, related, docs_only=True),
                    'REVIEW_REDISPATCH',
                )
                second = {
                    'pr': 42, 'head_sha': trd.HEAD, 'version': 2,
                    'state': 'UNKNOWN', 'failure_reason': reason,
                    'native_session_id': None,
                }
                self.assertEqual(
                    task_advance.decide(
                        self._base_record([first, second]), d6, pr, related, docs_only=True),
                    'REVIEW_UNUSABLE_STOPPED',
                )

    def test_advance_redispatches_once_with_real_store_and_stops_on_second(self):
        store, task_id = trd._real_store(Path(self.temp.name) / 'advance_store')
        executor = SimpleNamespace(root='/repo/worktree', common_dir='/repo/.git')
        preflight = (store.get(task_id), executor, trd.HEAD, 'd' * 40, 'claude-reviewer')
        proc = _bridge_proc({
            'schema_version': 1,
            'result': 'NOT_DELIVERED',
            'reason': 'SESSION_NOT_STARTED',
            'actual_session_id': None,
        }, returncode=1)
        with patch.object(
                review_dispatch.role_config, 'resolve_role',
                return_value={'config_dir': '/private/test-reviewer-account', 'source': 'roles'}), \
             patch.object(review_dispatch, '_preflight', side_effect=[preflight, preflight]), \
             patch.object(review_dispatch.worker_dispatch, 'evaluate',
                          return_value=SimpleNamespace(allowed=True, reason_code='')), \
             patch.object(review_dispatch, '_resolve_github_login', return_value='reviewer-user'), \
             patch.object(review_dispatch, '_review_model', return_value='claude-sonnet-4-6'), \
             patch.object(review_dispatch, '_admit_review_model', return_value='claude-sonnet-4-6'), \
             patch.object(review_dispatch, '_review_rules', return_value=('RULES\n', 'e' * 64)), \
             patch.object(review_dispatch.subprocess, 'run', return_value=proc):
            with self.assertRaises(LLMToolError):
                review_dispatch.dispatch_review(
                    store=store,
                    task_id=task_id,
                    executor_task='executor-d6',
                    pr_number=42,
                    version=1,
                    create_d6=lambda **_kw: None,
                    bind_session=Mock(),
                    prompt_renderer=lambda *_: 'prompt',
                )

        record = store.get(task_id)
        completion = dict(record.get('completion') or {}, executor_task_id='executor-d6')
        store.compare_and_update(
            task_id,
            expected={'record_sha256': record['record_sha256']},
            published_pr={'pr': 42, 'head_sha': trd.HEAD},
            completion=completion,
        )
        d6 = self._d6_snapshot()
        pr = self._pr_snapshot()
        redispatch_calls = []

        def _fake_dispatch_review(**kwargs):
            redispatch_calls.append(kwargs)
            return {'state': 'DISPATCHED', 'version': kwargs['version'], 'pr': 42}

        with patch.object(task_advance, '_d6_snapshot', return_value=d6), \
             patch.object(task_advance, '_pr_snapshot', return_value=pr), \
             patch.object(task_advance, '_pr_docs_only', return_value=False), \
             patch.object(task_advance, '_remote_test_status', return_value=('PASS', None, None)), \
             patch.object(task_advance, '_review_ci_gate', return_value=(None, None)), \
             patch.object(task_advance, '_pr_changed_test_modules', return_value=((), None)), \
             patch.object(task_advance, '_merge_security_claim', return_value=None):
            step1 = task_advance.advance(
                task_id=task_id,
                store=store,
                pr_number=42,
                github_get=lambda path: [] if 'comments' in path else {'changed_files': 1},
                dispatch_review_call=_fake_dispatch_review,
            )

        self.assertEqual(step1['step'], 'DISPATCH_REVIEW')
        self.assertIn('REVIEW_REDISPATCHED', step1['codes'])
        self.assertIn('SESSION_NOT_STARTED', step1['codes'])
        self.assertEqual(len(redispatch_calls), 1)
        self.assertEqual(redispatch_calls[0]['version'], 2)
        self.assertTrue(redispatch_calls[0]['refusal_rerun'])

        fresh = store.get(task_id)
        second_claim = dict(
            fresh['review_dispatch_claim'][0],
            version=2,
            reviewer_task='reviewer-2',
        )
        store.compare_and_update(
            task_id,
            expected={'record_sha256': fresh['record_sha256']},
            review_dispatch_claim=fresh['review_dispatch_claim'] + [second_claim],
        )
        with patch.object(task_advance, '_d6_snapshot', return_value=d6), \
             patch.object(task_advance, '_pr_snapshot', return_value=pr), \
             patch.object(task_advance, '_pr_docs_only', return_value=False), \
             patch.object(task_advance, '_remote_test_status', return_value=('PASS', None, None)):
            step2 = task_advance.advance(
                task_id=task_id,
                store=store,
                pr_number=42,
                github_get=lambda path: [] if 'comments' in path else {'changed_files': 1},
                dispatch_review_call=_fake_dispatch_review,
            )

        self.assertEqual(step2['step'], 'REVIEW_UNUSABLE_STOPPED')
        self.assertEqual(len(redispatch_calls), 1)


if __name__ == '__main__':
    unittest.main()
