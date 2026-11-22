"""#2492: recover an UNKNOWN review repair claim whose repair Task never reached a Developer.

Shaped like the real PR #2403 case: review claim attempt 1 on a formal FAIL
(version 3, one finding), repair Task recorded by the runtime and left
PM_RESPONDED with no Developer, claim UNKNOWN.
"""
import contextlib
import types
import unittest
from pathlib import Path
import tempfile
from unittest import mock

from test_recover_ec2_claim_2475 import (EXECUTOR, HEAD, PR, _StoreView, _arm_root,
                                         _clean_d6, _make_root, _process, _record_pm)
from llmtool import (lineage_counts, review_repair, review_repair_recovery as rec,
                     worker_availability, worker_dispatch)
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore, new_id

COMMENT_ID, VERSION = 7012264117, 3


def _review_packet(task_id):
    return {'task': task_id, 'pr': PR, 'executor_task': EXECUTOR, 'failed_head': HEAD,
            'version': VERSION, 'verdict_comment_id': COMMENT_ID, 'findings_count': 1,
            'reviewer_text_excerpt_untrusted_data': 'F1 off by one'}


class RecoverReviewNoLaunchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = TaskStore(Path(temp.name).resolve() / 'tasks')
        self.task_id = _make_root(self.store)
        _arm_root(self.store, self.task_id)
        root = self.store.get(self.task_id)
        self.repair = new_id()
        review_repair._record_repair_task(
            task=root, developer=root['developer'], packet=_review_packet(self.task_id),
            attempt=1, store=self.store, repair_task_id=self.repair, record_pm=_record_pm)
        claim = {'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': VERSION,
                 'attempt': 1, 'family': f'pr-{PR}', 'state': 'CLAIMED',
                 'comment_id': COMMENT_ID, 'repair_task_id': self.repair,
                 'packet_sha256': 'c' * 64, 'claimed_at': '2026-11-19T15:30:44Z'}
        self._cas(review_repair_claims=[claim])
        # The provider launch never happened: the claim was left UNKNOWN.
        self._cas(review_repair_claims=[dict(claim, state='UNKNOWN')])

    def _cas(self, **changes):
        sha = self.store.get(self.task_id)['record_sha256']
        self.store.compare_and_update(self.task_id, expected={'record_sha256': sha}, **changes)

    def _claim(self):
        return next(item for item in self.store.get(self.task_id)['review_repair_claims']
                    if item['repair_task_id'] == self.repair)

    def _recover(self, **kwargs):
        args = dict(store=self.store, task_id=self.task_id, executor_task=EXECUTOR,
                    pr_number=PR, repair_task_id=self.repair,
                    registered_d6=_clean_d6(HEAD), process_status=_process('NONE'))
        return rec.recover_unknown_claim(**{**args, **kwargs})

    def _verify(self, repair_changes=None, *, registered=None, status='NONE'):
        repair = dict(self.store.get(self.repair), **(repair_changes or {}))
        view = _StoreView(self.store, self.repair, repair)
        return rec.verify_review_no_launch_recovery(
            view, self.store.get(self.task_id), self.repair, executor_task=EXECUTOR,
            pr_number=PR, registered_d6=registered or _clean_d6(HEAD),
            process_status=_process(status))

    def _refused(self, code='REPAIR_RECOVERY_EVIDENCE_INVALID', **kwargs):
        with self.assertRaises(LLMToolError) as caught:
            self._verify(**kwargs)
        self.assertEqual(caught.exception.review_repair_code, code)

    def test_never_started_review_claim_recovers_once_without_a_new_count(self):
        before = self.store.get(self.task_id)
        used = lineage_counts.chain_review_repairs(self.store, before)
        result = self._recover()
        self.assertTrue(result['recovered'])
        after = self.store.get(self.task_id)
        self.assertEqual(self._claim()['state'], 'CLAIMED')
        self.assertEqual(after['review_repair_claims'],
                         [dict(item, state='CLAIMED') for item in before['review_repair_claims']])
        self.assertEqual(lineage_counts.chain_review_repairs(self.store, after), used)
        self.assertIsNone(self.store.get(self.repair).get('developer'))
        # A second recovery refuses and changes nothing.
        with self.assertRaises(LLMToolError) as caught:
            self._recover()
        self.assertEqual(caught.exception.review_repair_code, 'REPAIR_RECOVERY_CLAIM_INVALID')
        self.assertEqual(self.store.get(self.task_id)['record_sha256'], after['record_sha256'])

    def test_recorded_continuation_passes_the_check(self):
        self.assertEqual(self._verify()['repair_task_id'], self.repair)

    def test_any_launch_evidence_refuses(self):
        for changes in ({'developer': {'state': 'DEVELOPER_DISPATCHING'}},
                        {'developer': {'state': 'DEVELOPER_RESPONDED'}},
                        {'completion': {'state': 'WAITING_EXECUTOR'}},
                        {'dispatch_audit': [{'action': 'DISPATCH'}]},
                        {'state': 'DEVELOPER_PENDING'},
                        {'abandonment': {'reason': 'x'}}):
            with self.subTest(changes=changes):
                self._refused(repair_changes=changes)

    def test_provider_invoked_or_foreign_provenance_refuses(self):
        provenance = self.store.get(self.repair)['pm_provenance']
        for changes in ({'provider_invoked': True}, {'source': 'OTHER'}):
            with self.subTest(changes=changes):
                self._refused(repair_changes={'pm_provenance': dict(provenance, **changes)})

    def test_text_that_is_not_this_claims_continuation_refuses(self):
        self._refused(repair_changes={'request': 'REPAIR 2/5 of ' + self.task_id})

    def test_dirty_moved_d6_or_live_process_refuses(self):
        self._refused(registered=_clean_d6(HEAD, clean=False))
        self._refused(registered=_clean_d6('0' * 40))
        for status in ('ALIVE', 'UNKNOWN'):
            with self.subTest(status=status):
                self._refused(status=status)

    def test_refused_recovery_leaves_the_claim_unknown(self):
        with self.assertRaises(LLMToolError):
            self._recover(process_status=_process('ALIVE'))
        self.assertEqual(self._claim()['state'], 'UNKNOWN')

    def test_launched_repair_keeps_the_1928_path(self):
        view = _StoreView(self.store, self.repair,
                          dict(self.store.get(self.repair),
                               developer={'state': 'DEVELOPER_RESPONDED'}))
        self.assertFalse(rec._review_never_started(view, self._claim(), self.repair))
        self.assertTrue(rec._review_never_started(self.store, self._claim(), self.repair))

    def test_pm_repair_delegates_the_same_repair_task_once(self):
        self._recover()
        delegated = []
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                review_repair, '_failed_event',
                return_value=(_review_packet(self.task_id), None)))
            stack.enter_context(mock.patch.object(
                worker_dispatch, 'evaluate', return_value=types.SimpleNamespace(
                    allowed=True, reason_code='OWNER_POLICY_ALLOWED')))
            stack.enter_context(mock.patch.object(
                worker_availability, 'fresh_quota_fail', return_value=False))
            stack.enter_context(mock.patch.object(
                review_repair, '_registered_d6', return_value=(types.SimpleNamespace(
                    root='/d6', branch='branch', result_sha=HEAD), None)))
            result = review_repair.repair_review(
                store=self.store, task_id=self.task_id, executor_task=EXECUTOR,
                pr_number=PR, current_head=lambda task, executor: HEAD,
                delegate_callable=lambda args, store=None: delegated.append(
                    args.task_id) or {'delegated': args.task_id})
            self.assertEqual(delegated, [self.repair])
            self.assertEqual(result['repair_task_id'], self.repair)
            self.assertEqual(self._claim()['state'], 'DISPATCHED')
            self.assertEqual(len(self.store.get(self.task_id)['review_repair_claims']), 1)
            # The next call sees DISPATCHED and delegates nothing again.
            again = review_repair.repair_review(
                store=self.store, task_id=self.task_id, executor_task=EXECUTOR,
                pr_number=PR, current_head=lambda task, executor: HEAD,
                delegate_callable=lambda args, store=None: delegated.append(args.task_id))
        self.assertEqual(delegated, [self.repair])
        self.assertTrue(again['replayed'])


if __name__ == '__main__':
    unittest.main()
