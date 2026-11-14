"""#1928 PR3: TaskStore.recover_review_repair_claim runs E1-E5 inside its locked update."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_task_store
from llmtool import review_repair_recovery
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore, TaskStoreConflict, new_id


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

    def recover(self, verify=None, sha=None, repair=None):
        calls = []

        def fake(store, root, repair_id, *, executor_task, pr_number):
            calls.append((store, root['record_sha256'], repair_id, executor_task, pr_number))
            if verify is not None:
                raise verify
        with mock.patch.object(review_repair_recovery, 'verify_recovery', fake):
            saved = self.store.recover_review_repair_claim(
                self.task_id, repair or self.repair, executor_task='e-1', pr_number=17,
                expected_sha256=sha or self.unknown['record_sha256'])
        return saved, calls

    def unchanged(self, error, call, *args, **kwargs):
        before = self.store.get(self.task_id)
        with self.assertRaises(error) as caught:
            call(*args, **kwargs)
        self.assertEqual(self.store.get(self.task_id), before)
        return caught.exception

    def test_verified_recovery_sets_only_that_claim_dispatched(self):
        saved, calls = self.recover()
        self.assertEqual(calls, [(self.store, self.unknown['record_sha256'], self.repair,
                                  'e-1', 17)])
        self.assertEqual(saved['review_repair_claims'],
                         [self.claims[0], dict(self.claims[1], state='DISPATCHED')])
        changed = {key for key in saved if saved[key] != self.unknown.get(key)}
        self.assertEqual(changed - {'updated_at'}, {'review_repair_claims', 'record_sha256'})


if __name__ == '__main__':
    unittest.main()
