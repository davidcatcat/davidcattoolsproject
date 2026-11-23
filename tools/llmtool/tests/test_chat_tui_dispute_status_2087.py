"""#2087 (Advisor slice R2, piece 1): TUI status readers with a dispute ruling claim.

A ruling claim (purpose dispute_ruling) sits in the Task's review_dispatch_claim
list after the formal claim (A1). The reviewer line and the background work list
must use the latest formal claim: they never name the arbiter as the Task's
Reviewer and never use the arbiter's claim time. Records are built from the A1
store test module's fixtures (formal claim plus ruling claim, with history) and
written through the real TaskStore on a temporary store directory with no store
mock. All IDs, sessions and digests are fixed placeholder values (Owner #763 A).
"""

import unittest

from llmtool import chat_tui_status
from llmtool.chat_tui_task import NOT_AVAILABLE

from test_dispute_store_schema import (
    ARBITER_REVIEWER,
    EXECUTOR,
    FORMAL_REVIEWER,
    PR,
    STAMP_1,
    STAMP_2,
    TASK,
    DisputeStoreSchemaTests,
)


class DisputeTuiStatusTests(unittest.TestCase):
    def _new_store(self):
        """A1 shape (formal claim plus ruling claim, with history) via the A1 store
        test module's own fixtures, written through the real TaskStore. The
        completion record carries the exact keys the real TaskStore requires
        (missing ones are TASK_STORE_INVALID); the stored developer stays None."""
        store, _task_path = DisputeStoreSchemaTests._new_store(self, with_dispute=True,
                                                               with_history=True)
        DisputeStoreSchemaTests._claim_arbiter(self, store, with_history=True)
        rec = store.get(TASK)
        store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            completion={'state': 'WAITING_REVIEW',
                        'executor_task_id': EXECUTOR,
                        'reviewer_task_id': FORMAL_REVIEWER,
                        'evidence': None,
                        'missing_gates': [],
                        'pm_dispatch': 'NOT_STARTED',
                        'pm_response': None,
                        'pm_recommendation': None,
                        'pm_usage': None,
                        'pm_error_code': None})
        return store

    def _reviewer_line(self, record):
        record = dict(record, developer=None, review_repair_claims=None)
        return chat_tui_status.reviewer(record)

    def _background_items(self, record):
        """The status stage reads completion only for a live developer; the stored
        record's developer is None, so pass an in-memory copy with developer set
        to an empty dict (not stored), as the existing reviewer-line tests do."""
        return chat_tui_status.background_items(
            [dict(record, developer={}, review_repair_claims=None)])

    def test_reviewer_line_uses_formal_claim_not_arbiter(self):
        store = self._new_store()
        rec = store.get(TASK)
        self.assertEqual(len(rec['review_dispatch_claim']), 2)
        self.assertEqual(rec['review_dispatch_claim'][1].get('purpose'),
                         'dispute_ruling')

        who, verdict = self._reviewer_line(rec)
        self.assertIn(FORMAL_REVIEWER, who)
        self.assertNotIn(ARBITER_REVIEWER, who)
        self.assertEqual(verdict,
                         NOT_AVAILABLE + " (no review receipt recorded)")

        # Public status text: no arbiter anywhere; verdict line unchanged.
        text = chat_tui_status.summary_text(rec)
        self.assertNotIn(ARBITER_REVIEWER, text)
        self.assertIn(FORMAL_REVIEWER, text)
        self.assertIn("Latest review verdict: " + verdict, text)

        # Single ruling-claim dict: same result as a record with no claim for
        # this function.
        single = dict(rec, review_dispatch_claim=rec['review_dispatch_claim'][1])
        no_claim = dict(rec, review_dispatch_claim=None)
        self.assertEqual(self._reviewer_line(single), self._reviewer_line(no_claim))

    def test_background_items_take_formal_claim_time(self):
        store = self._new_store()
        rec = store.get(TASK)
        self.assertEqual(rec['review_dispatch_claim'][1].get('purpose'),
                         'dispute_ruling')

        items = self._background_items(rec)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['task'], TASK)
        self.assertEqual(items[0]['state'], 'WAITING_REVIEW')
        # started_at is the formal claim's claimed_at, not the arbiter's.
        self.assertEqual(items[0]['started_at'], STAMP_1)
        self.assertNotEqual(items[0]['started_at'], STAMP_2)
        self.assertEqual(items[0]['pr'], PR)

        # Single ruling-claim dict: the purposed claim is skipped, so the
        # item is still there (state from the completion) with no started_at,
        # while pr is still derived as #42.
        single = dict(rec, review_dispatch_claim=rec['review_dispatch_claim'][1])
        single_items = self._background_items(single)
        self.assertEqual(len(single_items), 1)
        self.assertEqual(single_items[0]['state'], 'WAITING_REVIEW')
        self.assertIsNone(single_items[0]['started_at'])
        self.assertEqual(single_items[0]['pr'], PR)

    def test_formal_claims_unchanged(self):
        store = self._new_store()
        rec = store.get(TASK)
        rec = dict(rec, developer={}, review_repair_claims=None)
        formal_only = dict(rec, review_dispatch_claim=rec['review_dispatch_claim'][:1])

        self.assertEqual(chat_tui_status.reviewer(rec),
                         chat_tui_status.reviewer(formal_only))
        self.assertEqual(chat_tui_status.summary_text(rec),
                         chat_tui_status.summary_text(formal_only))
        self.assertEqual(self._background_items(rec),
                         self._background_items(formal_only))
        self.assertIn(FORMAL_REVIEWER, chat_tui_status.summary_text(rec))

    def test_reviewer_line_skips_any_purposed_claim(self):
        store = self._new_store()
        rec = store.get(TASK)
        rec = dict(rec, developer=None, review_repair_claims=None)
        # A claim list with a single purposed claim equals no claim at all:
        # the reviewer line reports nothing available, not the formal line.
        purposed_only = dict(rec, review_dispatch_claim=[
            dict(rec['review_dispatch_claim'][1], purpose='dispute_appeal')])
        self.assertEqual(purposed_only['review_dispatch_claim'][0].get('purpose'),
                         'dispute_appeal')
        who, verdict = self._reviewer_line(purposed_only)
        self.assertEqual(who, NOT_AVAILABLE)
        self.assertEqual(verdict, NOT_AVAILABLE)

        # The formal claim plus a claim with another purpose: the purposed
        # claim is skipped, so the line is the formal one, arbiter-free.
        formal_plus = dict(rec, review_dispatch_claim=[
            rec['review_dispatch_claim'][0],
            dict(rec['review_dispatch_claim'][1], purpose='dispute_appeal')])
        formal_only = dict(rec, review_dispatch_claim=rec['review_dispatch_claim'][:1])
        self.assertEqual(self._reviewer_line(formal_plus),
                         self._reviewer_line(formal_only))
        who, _verdict = self._reviewer_line(formal_plus)
        self.assertIn(FORMAL_REVIEWER, who)
        self.assertNotIn(ARBITER_REVIEWER, who)


if __name__ == '__main__':
    unittest.main()
