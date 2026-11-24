"""#2087 slice R2 (piece 3b): pin the task index's stored TUI events to the TUI timeline.

For a record whose newest claim is a dispute ruling claim, the stored TUI summary
must show the same ruling-claim DISPUTE events as task_events: same kind and
cut text at each position, count bounded by TUI_EVENT_LIMIT.
"""

import unittest

from llmtool import chat_tui_status, task_index
from llmtool.chat_tui_events import task_events

from test_dispute_ruling_claim_readers import (_arbiter_claim, _base_task_record,
                                               _dispute_entry, _formal_claim)


def _record(claim_kwargs, entry_state):
    record, contract_digest = _base_task_record()
    claim = _arbiter_claim(contract_digest, **claim_kwargs)
    record['review_dispatch_claim'] = [_formal_claim(contract_digest), claim]
    entry = _dispute_entry()
    entry['state'] = entry_state
    entry['arbiter_claim'] = {'reviewer_task': claim['reviewer_task'],
                              'version': claim['version']}
    record['review_disputes'] = [entry]
    return record


def _cut(text):
    limit = task_index.TUI_TEXT_LIMIT
    return text if len(text) <= limit else text[:limit - 1] + '\u2026'


class StoredDisputeEventsTest(unittest.TestCase):
    def test_stored_summary_equals_timeline(self):
        cases = (
            ({}, 'RULED', 'COMPLETED/RULED'),
            ({'state': 'DISPATCHED', 'reviewer': 'claude-reviewer'},
             'OPEN', 'DISPATCHED/OPEN'),
        )
        for claim_kwargs, entry_state, label in cases:
            with self.subTest(label):
                record = _record(claim_kwargs, entry_state)
                summary = task_index._tui_summary(record)
                self.assertIsNotNone(summary)
                self.assertEqual(summary, chat_tui_status.index_summary(record))
                events = task_events(record)
                limit = task_index.TUI_EVENT_LIMIT
                self.assertEqual(len(summary['events']), min(limit, len(events)))
                self.assertEqual(summary['event_count'], len(events))
                self.assertEqual(summary['events'][0][1], 'DISPUTE')
                self.assertLessEqual(len(summary['events'][0][2]),
                                     task_index.TUI_TEXT_LIMIT)
                for stored, event in zip(summary['events'], events[:limit]):
                    self.assertEqual((stored[1], stored[2]),
                                     (event['kind'], _cut(event['text'])))


if __name__ == '__main__':
    unittest.main()
