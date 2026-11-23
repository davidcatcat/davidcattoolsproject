"""#2087 (slice R2): the TUI timeline rows a dispute-ruling claim as a
DISPUTE event with the dispute entry state, never as a formal REVIEW claim.

Pure task_events tests over record dicts; the base record, claims and dispute
entries come from the shared module-level helpers of
test_dispute_ruling_claim_readers (loaded by plain module name).
"""

import unittest

import test_dispute_ruling_claim_readers as readers

from llmtool.chat_tui_events import task_events


def _entry(claim, state):
    entry = readers._dispute_entry()
    entry["state"] = state
    entry["arbiter_claim"] = {"reviewer_task": claim["reviewer_task"],
                              "version": claim["version"]}
    return entry


def _ruling_record(state, entry_state, reviewer="codex-reviewer"):
    record, digest = readers._base_task_record()
    claim = readers._arbiter_claim(digest, state=state, reviewer=reviewer)
    record["review_dispatch_claim"] = [claim]
    record["review_disputes"] = [_entry(claim, entry_state)]
    return record, claim


class TestDisputeClaimTimeline(unittest.TestCase):

    def test_ruling_claims_are_dispute_rows(self):
        cases = (("DISPATCHED", "claude-reviewer", "SUPERSEDED"),
                 ("COMPLETED", "codex-reviewer", "RULED"),
                 ("CLAIMED", "codex-reviewer", "OPEN"))
        for state, reviewer, entry_state in cases:
            with self.subTest(state=state, reviewer=reviewer, entry=entry_state):
                record, claim = _ruling_record(state, entry_state, reviewer=reviewer)
                base = ("dispute arbiter claim PR #" + str(readers.PR) + " finding "
                        + claim["dispute"]["finding_id"] + " v" + str(claim["version"])
                        + " " + state + ", reviewer task " + claim["reviewer_task"]
                        + " (dispute " + entry_state + "; not a formal review)")
                pairs = {event["text"].rsplit(" ", 1)[-1]: (event["kind"], event["time"])
                         for event in task_events(record)
                         if event["text"].startswith(base + " ")}
                self.assertEqual(pairs, {
                    "(claimed)": ("DISPUTE", claim["claimed_at"]),
                    "(updated)": ("DISPUTE", claim["updated_at"])})
                for event in task_events(record):
                    if event["kind"] == "REVIEW":
                        self.assertNotIn(readers.ARBITER_REVIEWER, event["text"])

    def test_dispatched_superseded_tail(self):
        record, _ = _ruling_record("DISPATCHED", "SUPERSEDED",
                                   reviewer="claude-reviewer")
        tail = ("DISPATCHED, reviewer task " + readers.ARBITER_REVIEWER
                + " (dispute SUPERSEDED; not a formal review)")
        dispute_texts = [event["text"] for event in task_events(record)
                         if event["kind"] == "DISPUTE"]
        self.assertEqual(len(dispute_texts), 2)
        self.assertTrue(all(tail in text for text in dispute_texts))

    def test_ruling_outcome_never_shown(self):
        record, claim = _ruling_record("COMPLETED", "RULED")
        result = claim["review_result"]
        for event in task_events(record):
            self.assertNotIn(result["review_text"], event["text"])
            self.assertNotIn(result["outcome"], event["text"])

    def test_entry_state_fail_closed(self):
        record, digest = readers._base_task_record()
        claim = readers._arbiter_claim(digest)
        cases = (("no matching entry", [readers._dispute_entry()]),
                 ("two matching entries", [_entry(claim, "OPEN"),
                                           _entry(claim, "RULED")]),
                 ("review_disputes not a list", readers._dispute_entry()))
        for label, disputes in cases:
            with self.subTest(label=label):
                record["review_dispatch_claim"] = [claim]
                record["review_disputes"] = disputes
                dispute_events = [event for event in task_events(record)
                                  if event["kind"] == "DISPUTE"]
                self.assertTrue(dispute_events)
                for event in dispute_events:
                    self.assertIn("(dispute UNKNOWN; not a formal review)",
                                  event["text"])

    def test_formal_claim_unchanged(self):
        for with_legacy_entry in (False, True):
            with self.subTest(legacy_entry=with_legacy_entry):
                record, digest = readers._base_task_record()
                claim = readers._formal_claim(digest)
                claim["updated_at"] = readers.STAMP_2
                record["review_dispatch_claim"] = [claim]
                if with_legacy_entry:
                    record["review_disputes"] = [readers._dispute_entry()]
                base = ("review claim PR #" + str(readers.PR) + " v1 "
                        + claim["state"] + ", reviewer task "
                        + readers.FORMAL_REVIEWER)
                events = task_events(record)
                review = [event for event in events if event["kind"] == "REVIEW"]
                self.assertEqual(
                    sorted((event["text"], event["time"]) for event in review),
                    sorted([(base + " (claimed)", claim["claimed_at"]),
                            (base + " (updated)", claim["updated_at"])]))
                self.assertNotIn("DISPUTE", [event["kind"] for event in events])


if __name__ == "__main__":
    unittest.main()
