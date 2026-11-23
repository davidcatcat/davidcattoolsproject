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
    FORMAL_REVIEWER,
    STAMP_1,
    STAMP_2,
    TASK,
    DisputeStoreSchemaTests,
)


class DisputeTuiStatusTests(unittest.TestCase):
    def _new_store(self):
        """A1 shape (formal claim plus ruling claim, with history) via the A1 store
        test module's own fixtures, written through the real TaskStore."""
        store, _task_path = DisputeStoreSchemaTests._new_store(self, with_dispute=True,
                                                               with_history=True)
        DisputeStoreSchemaTests._claim_arbiter(self, store, with_history=True)
        store.compare_and_update(
            TASK, expected={'record_sha256': store.get(TASK)['record_sha256']},
            completion={'state': 'WAITING_REVIEW',
                        'evidence': {'review_pr_ci': None}})
        return store

    def _reviewer_line(self, record):
        record = dict(record, developer=None, review_repair_claims=None)
        return chat_tui_status.reviewer(record)

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

        # Single ruling-claim dict: same result as no claim for this function.
        single = dict(rec, review_dispatch_claim=rec['review_dispatch_claim'][1])
        self.assertEqual(self._reviewer_line(single), self._reviewer_line(rec))

    def test_background_items_take_formal_claim_time(self):
        store = self._new_store()
        rec = store.get(TASK)
        self.assertEqual(rec['review_dispatch_claim'][1].get('purpose'),
                         'dispute_ruling')

        items = chat_tui_status.background_items([rec])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['task'], TASK)
        self.assertEqual(items[0]['state'], 'WAITING_REVIEW')
        # started_at is the formal claim's claimed_at, not the arbiter's.
        self.assertEqual(items[0]['started_at'], STAMP_1)
        self.assertNotEqual(items[0]['started_at'], STAMP_2)

        # Single ruling-claim dict: same result as no claim for this function.
        single = dict(rec, review_dispatch_claim=rec['review_dispatch_claim'][1])
        self.assertEqual(chat_tui_status.background_items([single]),
                         chat_tui_status.background_items([rec]))

    def test_formal_claims_unchanged(self):
        store = self._new_store()
        rec = store.get(TASK)
        formal_only = dict(rec, review_dispatch_claim=rec['review_dispatch_claim'][:1])
        rec = dict(rec, developer=None, review_repair_claims=None)
        formal_only = dict(formal_only, developer=None, review_repair_claims=None)

        self.assertEqual(chat_tui_status.reviewer(rec),
                         chat_tui_status.reviewer(formal_only))
        self.assertEqual(chat_tui_status.summary_text(rec),
                         chat_tui_status.summary_text(formal_only))
        self.assertEqual(chat_tui_status.background_items([rec]),
                         chat_tui_status.background_items([formal_only]))
        self.assertIn(FORMAL_REVIEWER, chat_tui_status.summary_text(rec))

    def test_reviewer_line_skips_any_purposed_claim(self):
        store = self._new_store()
        rec = store.get(TASK)
        # Any claim dict with a purpose key is skipped, whatever its value:
        # a claim with another purpose is not a formal claim either.
        purposed = dict(rec, review_dispatch_claim=[
            dict(rec['review_dispatch_claim'][1], purpose='dispute_appeal')])
        self.assertEqual(purposed['review_dispatch_claim'][0].get('purpose'),
                         'dispute_appeal')

        who, _verdict = self._reviewer_line(purposed)
        self.assertEqual(self._reviewer_line(purposed),
                         self._reviewer_line(rec))
        self.assertIn(FORMAL_REVIEWER, who)
        self.assertNotIn(ARBITER_REVIEWER, who)


if __name__ == '__main__':
    unittest.main()
