"""#1519: task advance --merge --owner-exception reconciles a CLAIMED exception merge."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool import owner_exception, task_advance
from llmtool.task_store import TaskStore
from test_owner_exception import (BASE_SHA, FILE_PATH, HEAD_HASH, HEAD_SHA, REPLACEMENT_MAP,
                                  SLUG, URL, make_fake_github_get)
from test_task_advance_review_exception_store import _record

OTHER_HEAD = 'c' * 40


class OwnerExceptionReconcileTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = TaskStore(Path(temp.name).resolve() / 'tasks')
        record = _record()
        self.store.create(record)
        self.task_id = record['task_id']
        for name, value in (('_trusted_slug', SLUG), ('default_replacement_map', REPLACEMENT_MAP)):
            patcher = patch.object(owner_exception, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.gh_calls = []

    def claim(self, state='CLAIMED', pr=42, head=HEAD_SHA):
        """Write a fresh merge_attempt; TaskStore refuses to move a CLAIMED one."""
        claim = {'pr': pr, 'head_sha': head, 'state': state, 'claimed_at': '2026-11-11T16:05:51Z'}
        record = self.store.get(self.task_id)
        self.store.compare_and_update(self.task_id, expected={
            'record_sha256': record['record_sha256']}, merge_attempt=claim)
        return claim

    def gh(self, root, args):
        self.gh_calls.append(args)
        raise AssertionError('no gh call is expected')

    def merge(self, **fake):
        github_get = make_fake_github_get(**dict({'pr_state': 'closed', 'pr_merged': True}, **fake))
        return task_advance._merge_once(
            store=self.store, record=self.store.get(self.task_id), number=42, head=HEAD_SHA,
            pr=dict(github_get('pulls/42'), mergeable=True), events=[], github_get=github_get,
            gh_runner=self.gh, root='/nonexistent-root', owner_exception=URL,
            sleep_fn=lambda _s: None)

    def assert_refused(self, code, **fake):
        before = self.store.get(self.task_id)
        outcome = self.merge(**fake)
        self.assertEqual((outcome['step'], outcome['codes']), ('MERGE_REFUSED', [code]))
        self.assertEqual(self.gh_calls, [])
        self.assertEqual(self.store.get(self.task_id), before)

    def test_reconciles_a_claimed_merge_without_any_gh_call(self):
        claim = self.claim()
        outcome = self.merge()
        evidence = {'type': owner_exception.EXCEPTION_TYPE, 'slot': 'S2a', 'url': URL, 'pr': 42,
                    'base': BASE_SHA, 'head': HEAD_SHA, 'files': [[FILE_PATH, HEAD_HASH, 1]]}
        review_exception = {'type': owner_exception.EXCEPTION_TYPE, 'url': URL,
                            'evidence': evidence}
        self.assertEqual(outcome, {'step': 'MERGED', 'codes': ['MERGE_RECONCILED'], 'result': {
            'pr': 42, 'head': HEAD_SHA, 'review_exception': review_exception, 'reconciled': True}})
        stored = self.store.get(self.task_id)
        self.assertEqual(stored['merge_attempt'], dict(claim, state='MERGED'))
        self.assertEqual(stored['review_exception'], review_exception)
        self.assertEqual(self.gh_calls, [])
        # A second reconcile finds the claim MERGED and changes nothing.
        self.assert_refused('MERGE_ALREADY_CLAIMED')


if __name__ == '__main__':
    unittest.main()