"""#1928: TaskStore.recover_review_repair_claim (UNKNOWN -> DISPATCHED)."""
import tempfile
import unittest
from pathlib import Path

import test_task_store
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore, new_id


class ClaimRecoveryStoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = TaskStore(Path(temp.name).resolve() / 'tasks')
        task = test_task_store.TaskStoreTests._codex_dispatch_task(self)
        self.store.create(task)
        self.task_id, self.repair = task['task_id'], new_id()
        self.claim = {'executor_task': 'e-1', 'pr': 17, 'head_sha': 'a' * 40, 'version': 1,
                      'attempt': 1, 'family': 'pr-17', 'state': 'CLAIMED', 'comment_id': 9,
                      'repair_task_id': self.repair, 'packet_sha256': 'b' * 64,
                      'claimed_at': '2026-11-15T00:00:00Z'}
        other = dict(self.claim, family='pr-18', pr=18, repair_task_id=new_id())
        for claims in ([other], [other, self.claim], [other, dict(self.claim, state='UNKNOWN')]):
            self.cas(review_repair_claims=claims)
        self.unknown = self.store.get(self.task_id)
        self.claims = self.unknown['review_repair_claims']

    def cas(self, **changes):
        sha = self.store.get(self.task_id)['record_sha256']
        self.store.compare_and_update(self.task_id, expected={'record_sha256': sha}, **changes)

    def refused(self, call, *args, **kwargs):
        before = self.store.get(self.task_id)
        with self.assertRaises(LLMToolError) as caught:
            call(*args, **kwargs)
        self.assertEqual(caught.exception.task_store_error_code,
                         'REVIEW_REPAIR_CLAIM_TRANSITION_INVALID')
        self.assertEqual(self.store.get(self.task_id), before)

    def recover(self, repair=None, sha=None):
        return self.store.recover_review_repair_claim(
            self.task_id, repair or self.repair,
            expected_sha256=sha or self.unknown['record_sha256'])

    def test_recovery_sets_only_that_claim_dispatched(self):
        saved = self.recover()
        self.assertEqual(saved['review_repair_claims'],
                         [self.claims[0], dict(self.claims[1], state='DISPATCHED')])
        changed = {key for key in saved if saved[key] != self.unknown.get(key)}
        self.assertEqual(changed - {'updated_at'}, {'review_repair_claims', 'record_sha256'})

    def test_compare_and_update_still_refuses_unknown_to_dispatched(self):
        self.refused(self.cas, review_repair_claims=[
            self.claims[0], dict(self.claims[1], state='DISPATCHED')])

    def test_trusted_flag_allows_nothing_else(self):
        first, claim = self.claims
        good = dict(claim, state='DISPATCHED')
        for changes in ({'review_repair_claims': [first, good], 'error_code': 'X'},
                        {'review_repair_claims': [first, dict(good, extra=1)]},
                        {'review_repair_claims': [dict(first, state='UNKNOWN'), claim]},
                        {'review_repair_claims': [first, claim, dict(self.claim, attempt=2)]}):
            with self.subTest(changes=changes):
                self.refused(self.store._update, self.task_id, expected=None,
                             changes=changes, trusted_claim_recovery=True)


if __name__ == '__main__':
    unittest.main()
