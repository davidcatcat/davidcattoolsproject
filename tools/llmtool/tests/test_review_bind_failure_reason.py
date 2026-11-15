from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from llmtool import review_dispatch
from llmtool.errors import LLMToolError
import test_review_dispatch as trd


def setUpModule():
    trd.setUpModule()


def tearDownModule():
    trd.tearDownModule()


class ReviewBindFailureReasonDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def _dispatch_with_bind_error(self, exc):
        store = trd.DurableFakeStore(Path(self.temp.name) / f'task_{id(exc)}.json')
        executor = SimpleNamespace(root='/repo/worktree', common_dir='/repo/.git')
        preflight = (trd.TASK, executor, trd.HEAD, 'd' * 40, 'claude-reviewer')
        with patch.object(
                review_dispatch.role_config, 'resolve_role',
                return_value={'config_dir': '/private/test-reviewer-account', 'source': 'roles'}), \
             patch.object(review_dispatch, '_preflight', side_effect=[preflight, preflight]), \
             patch.object(review_dispatch.worker_dispatch, 'evaluate',
                          return_value=SimpleNamespace(allowed=True, reason_code='')), \
             patch.object(review_dispatch, '_resolve_github_login', return_value='reviewer-user'):
            def _bind(**_kw):
                raise exc

            with self.assertRaises(LLMToolError) as ctx:
                review_dispatch.dispatch_review(
                    store=store,
                    task_id=trd.TASK,
                    executor_task='executor-d6',
                    pr_number=42,
                    version=1,
                    create_d6=lambda **_kw: None,
                    dispatch_session=lambda **_kw: 'native-1',
                    bind_session=_bind,
                    prompt_renderer=lambda *_: 'prompt',
                )
        return ctx.exception, store.get(trd.TASK)['review_dispatch_claim'][-1]

    def test_bind_timeout_recorded_on_unknown_claim(self):
        err, last_claim = self._dispatch_with_bind_error(RuntimeError('REVIEWER_SESSION_BIND_TIMEOUT'))
        self.assertEqual(getattr(err, 'review_dispatch_code', None), 'REVIEW_DISPATCH_OUTCOME_UNKNOWN')
        self.assertEqual(last_claim['state'], 'UNKNOWN')
        self.assertEqual(last_claim['native_session_id'], 'native-1')
        self.assertEqual(last_claim.get('bind_failure_reason'), 'REVIEWER_SESSION_BIND_TIMEOUT')
        self.assertNotIn('failure_reason', last_claim)

    def test_bind_failed_recorded_on_unknown_claim(self):
        err, last_claim = self._dispatch_with_bind_error(RuntimeError('REVIEWER_SESSION_BIND_FAILED'))
        self.assertEqual(getattr(err, 'review_dispatch_code', None), 'REVIEW_DISPATCH_OUTCOME_UNKNOWN')
        self.assertEqual(last_claim['state'], 'UNKNOWN')
        self.assertEqual(last_claim['native_session_id'], 'native-1')
        self.assertEqual(last_claim.get('bind_failure_reason'), 'REVIEWER_SESSION_BIND_FAILED')
        self.assertNotIn('failure_reason', last_claim)

    def test_unrecognized_runtime_error_omits_bind_failure_reason(self):
        _err, last_claim = self._dispatch_with_bind_error(RuntimeError('other text'))
        self.assertEqual(last_claim['state'], 'UNKNOWN')
        self.assertEqual(last_claim['native_session_id'], 'native-1')
        self.assertNotIn('bind_failure_reason', last_claim)

    def test_other_exception_type_omits_bind_failure_reason(self):
        _err, last_claim = self._dispatch_with_bind_error(ValueError('REVIEWER_SESSION_BIND_TIMEOUT'))
        self.assertEqual(last_claim['state'], 'UNKNOWN')
        self.assertEqual(last_claim['native_session_id'], 'native-1')
        self.assertNotIn('bind_failure_reason', last_claim)


class ReviewBindFailureReasonStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_claimed_transitions_to_unknown_with_bind_failure_reason(self):
        store, task_id = trd._real_store(self.temp.name)
        initial = store.get(task_id)
        claimed = trd._claim()
        store.compare_and_update(
            task_id,
            expected={'record_sha256': initial['record_sha256']},
            review_dispatch_claim=[claimed],
        )
        current = store.get(task_id)
        unknown = dict(
            claimed,
            state='UNKNOWN',
            native_session_id='native-1',
            bind_failure_reason='REVIEWER_SESSION_BIND_TIMEOUT',
            updated_at='2026-10-27T00:02:00Z',
        )
        store.compare_and_update(
            task_id,
            expected={'record_sha256': current['record_sha256']},
            review_dispatch_claim=[unknown],
        )
        self.assertEqual(store.get(task_id)['review_dispatch_claim'], [unknown])

    def test_rejects_invalid_bind_failure_reason(self):
        store, task_id = trd._real_store(Path(self.temp.name) / 'invalid')
        initial = store.get(task_id)
        claimed = trd._claim()
        store.compare_and_update(
            task_id,
            expected={'record_sha256': initial['record_sha256']},
            review_dispatch_claim=[claimed],
        )
        current = store.get(task_id)
        for bad_reason in ('OTHER', 7):
            with self.subTest(bad_reason=bad_reason):
                bad_unknown = dict(
                    claimed,
                    state='UNKNOWN',
                    native_session_id='native-1',
                    bind_failure_reason=bad_reason,
                    updated_at='2026-10-27T00:03:00Z',
                )
                with self.assertRaises(LLMToolError):
                    store.compare_and_update(
                        task_id,
                        expected={'record_sha256': current['record_sha256']},
                        review_dispatch_claim=[bad_unknown],
                    )
                self.assertEqual(store.get(task_id)['review_dispatch_claim'], [claimed])

    def test_rejects_dispatched_claim_with_bind_failure_reason(self):
        store, task_id = trd._real_store(Path(self.temp.name) / 'dispatched')
        initial = store.get(task_id)
        claimed = trd._claim()
        store.compare_and_update(
            task_id,
            expected={'record_sha256': initial['record_sha256']},
            review_dispatch_claim=[claimed],
        )
        current = store.get(task_id)
        dispatched = dict(
            claimed,
            state='DISPATCHED',
            native_session_id='native-1',
            bind_failure_reason='REVIEWER_SESSION_BIND_TIMEOUT',
        )
        with self.assertRaises(LLMToolError):
            store.compare_and_update(
                task_id,
                expected={'record_sha256': current['record_sha256']},
                review_dispatch_claim=[dispatched],
            )
        self.assertEqual(store.get(task_id)['review_dispatch_claim'], [claimed])

    def test_rejects_unknown_claim_with_both_bind_failure_reason_and_failure_reason(self):
        store, task_id = trd._real_store(Path(self.temp.name) / 'unknown_both')
        initial = store.get(task_id)
        claimed = trd._claim()
        store.compare_and_update(
            task_id,
            expected={'record_sha256': initial['record_sha256']},
            review_dispatch_claim=[claimed],
        )
        current = store.get(task_id)
        bad_unknown = dict(
            claimed,
            state='UNKNOWN',
            native_session_id='native-1',
            bind_failure_reason='REVIEWER_SESSION_BIND_FAILED',
            failure_reason='OUTPUT_UNREADABLE',
            updated_at='2026-10-27T00:03:00Z',
        )
        with self.assertRaises(LLMToolError):
            store.compare_and_update(
                task_id,
                expected={'record_sha256': current['record_sha256']},
                review_dispatch_claim=[bad_unknown],
            )
        self.assertEqual(store.get(task_id)['review_dispatch_claim'], [claimed])

    def test_rejects_claimed_claim_with_bind_failure_reason(self):
        store, task_id = trd._real_store(Path(self.temp.name) / 'claimed_bind_reason')
        initial = store.get(task_id)
        claimed = dict(
            trd._claim(),
            bind_failure_reason='REVIEWER_SESSION_BIND_TIMEOUT',
        )
        with self.assertRaises(LLMToolError):
            store.compare_and_update(
                task_id,
                expected={'record_sha256': initial['record_sha256']},
                review_dispatch_claim=[claimed],
            )
        self.assertEqual(
            store.get(task_id).get('review_dispatch_claim'),
            initial.get('review_dispatch_claim'),
        )


if __name__ == '__main__':
    unittest.main()

