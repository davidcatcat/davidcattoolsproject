import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from llmtool import review_dispatch, review_result
from llmtool.errors import LLMToolError
import test_review_dispatch as trd
import test_review_result as trr


BIND_CODES = ('REVIEWER_SESSION_BIND_FAILED', 'REVIEWER_SESSION_BIND_TIMEOUT')


def _bound_pass(task):
    """One formal Claude PASS bound to the stored task contract digest (synthetic)."""
    comment = trr._comment()
    comment['body'] = comment['body'].replace(
        trr.CONTRACT, task['task_binding']['contract_digest'])
    return comment


def _unknown_store(root, **change):
    """A real store whose one review claim moved CLAIMED -> UNKNOWN with a started session."""
    Path(root).mkdir(mode=0o700)
    store = trr._store(root, claim_state='CLAIMED')
    record = store.get(trr.TASK)
    store.compare_and_update(
        trr.TASK, expected={'record_sha256': record['record_sha256']},
        review_dispatch_claim=[dict(record['review_dispatch_claim'][0], state='UNKNOWN',
                                    native_session_id='review-session', **change)])
    return store


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


class ReviewBindFailureReasonSettleTests(unittest.TestCase):
    """#1909 item 1 (security v92): an UNKNOWN claim with a failed Reviewer bind never settles."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # The claim's Reviewer D6 binds 'review-session', so only the field blocks settlement.
        self.common = trr._bind_reviewer(self.temp.name)

    def _root(self, name):
        return Path(self.temp.name, name)

    def _record(self, store):
        task = store.get(trr.TASK)
        return review_result.record_review_result(
            store=store, task_id=trr.TASK, executor_task=trr.EXECUTOR,
            pr_number=trr.PR, github_get=lambda *_args, **_kwargs: [_bound_pass(task)],
            common_dir=self.common)

    @staticmethod
    def _claims(store):
        return store.get(trr.TASK)['review_dispatch_claim']

    @staticmethod
    def _reviewer_event(store):
        event_id = hashlib.sha256(b'synthetic reviewer event').hexdigest()[:32]
        store.record_completion_event(
            trr.TASK, event_id=event_id, run_id=trr.RUN, session_id='review-' + event_id,
            source='REVIEWER', source_sha=trr.HEAD, sequence=0,
            payload_sha256=hashlib.sha256(b'synthetic reviewer payload').hexdigest())

    def test_settle_candidate_refuses_bind_failure_reason(self):
        for code in BIND_CODES:
            with self.subTest(code=code):
                root = self._root('candidate-' + code.lower())
                root.mkdir(mode=0o700)
                task = trr.FamilyStore(trr._store(root, claim_state='UNKNOWN'),
                                       'gw-agent').get(trr.TASK)
                claim = task['review_dispatch_claim'][0]
                comments = [_bound_pass(task)]
                self.assertTrue(review_result._settle_candidate(
                    task, claim, comments, head=trr.HEAD, version=1))
                self.assertFalse(review_result._settle_candidate(
                    task, dict(claim, bind_failure_reason=code), comments,
                    head=trr.HEAD, version=1))

    def test_record_review_result_keeps_bind_failed_claim_unknown(self):
        for code in BIND_CODES:
            with self.subTest(code=code):
                # Healthy control: same claim shape without failure fields settles once.
                control = trr.ReconcileStore(
                    _unknown_store(self._root('control-' + code.lower())), 'gw-agent')
                first = self._record(control)
                self.assertEqual(first['state'], 'REVIEW_PASS')
                self.assertTrue(first['created'])
                self.assertEqual(self._claims(control)[0]['state'], 'DISPATCHED')
                settled = self._claims(control)
                replay = self._record(control)
                self.assertEqual(replay['state'], 'REVIEW_PASS')
                self.assertFalse(replay['created'])
                self.assertEqual(self._claims(control), settled)

                failed = trr.ReconcileStore(
                    _unknown_store(self._root('failed-' + code.lower()),
                                   bind_failure_reason=code), 'gw-agent')
                before = self._claims(failed)
                result = self._record(failed)
                self.assertEqual(result['state'], 'REVIEW_PASS')
                after = self._claims(failed)
                self.assertEqual(after, before)
                self.assertEqual(after[0]['state'], 'UNKNOWN')
                self.assertEqual(after[0]['bind_failure_reason'], code)

    def test_store_refuses_dispatch_of_bind_failed_claim(self):
        for code in BIND_CODES:
            with self.subTest(code=code):
                # Control: the same transition without the field is accepted.
                control = _unknown_store(self._root('store-control-' + code.lower()))
                self._reviewer_event(control)
                record = control.get(trr.TASK)
                claim = record['review_dispatch_claim'][0]
                settled = dict(claim, state='DISPATCHED', updated_at='2026-10-27T02:00:00Z')
                control.compare_and_update(
                    trr.TASK, expected={'record_sha256': record['record_sha256']},
                    review_dispatch_claim=[settled])
                self.assertEqual(self._claims(control), [settled])

                store = _unknown_store(self._root('store-' + code.lower()),
                                       bind_failure_reason=code)
                self._reviewer_event(store)
                record = store.get(trr.TASK)
                claim = record['review_dispatch_claim'][0]
                kept = dict(claim, state='DISPATCHED', updated_at='2026-10-27T02:00:00Z')
                dropped = {key: value for key, value in kept.items()
                           if key != 'bind_failure_reason'}
                for name, target in (('kept', kept), ('dropped', dropped)):
                    with self.subTest(transition=name):
                        with self.assertRaises(LLMToolError) as caught:
                            store.compare_and_update(
                                trr.TASK, expected={'record_sha256': record['record_sha256']},
                                review_dispatch_claim=[target])
                        self.assertEqual(
                            getattr(caught.exception, 'task_store_error_code', None),
                            'REVIEW_DISPATCH_CLAIM_TRANSITION_INVALID')
                        self.assertEqual(self._claims(store), [claim])


if __name__ == '__main__':
    unittest.main()

