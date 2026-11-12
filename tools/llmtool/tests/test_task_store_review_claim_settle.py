"""#1636: TaskStore lets an UNKNOWN review claim settle to DISPATCHED only narrowly."""

import tempfile
import unittest

from llmtool.errors import LLMToolError
from test_review_result import HEAD, RUN, TASK, _store


class UnknownClaimSettleTransitionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = _store(temporary.name, claim_state='UNKNOWN')

    def _event(self, head=HEAD, source='REVIEWER'):
        self.store.record_completion_event(
            TASK, event_id='e' * 32, run_id=RUN, session_id='review-' + 'e' * 32,
            source=source, source_sha=head, sequence=0, payload_sha256='f' * 64)

    def _settle(self, **change):
        record = self.store.get(TASK)
        claim = record['review_dispatch_claim'][0]
        after = dict(claim, state='DISPATCHED', updated_at='2026-10-27T05:00:00Z')
        after.update(change)
        return self.store.compare_and_update(
            TASK, expected={'review_dispatch_claim': record['review_dispatch_claim']},
            review_dispatch_claim=[after])

    def _unknown_from_claimed(self, **change):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = _store(temporary.name, claim_state='CLAIMED')
        record = self.store.get(TASK)
        claim = dict(record['review_dispatch_claim'][0], state='UNKNOWN', **change)
        self.store.compare_and_update(
            TASK, expected={'record_sha256': record['record_sha256']},
            review_dispatch_claim=[claim])

    def assertRefused(self, **change):
        with self.assertRaises(LLMToolError):
            self._settle(**change)
        self.assertEqual(self.store.get(TASK)['review_dispatch_claim'][0]['state'], 'UNKNOWN')

    def test_unknown_claim_with_session_and_reviewer_event_settles(self):
        self._event()
        saved = self._settle()
        claim = saved['review_dispatch_claim'][0]
        self.assertEqual(claim['state'], 'DISPATCHED')
        self.assertEqual(claim['native_session_id'], 'review-session')

    def test_no_reviewer_event_for_the_head_is_refused(self):
        self.assertRefused()
        self._event(head='e' * 40)
        self.assertRefused()

    def test_identity_or_session_change_is_refused(self):
        self._event()
        for change in ({'reviewer_task': 'reviewer-other'}, {'head_sha': 'e' * 40},
                       {'version': 2}, {'native_session_id': 'other-session'},
                       {'claimed_at': '2026-10-27T04:00:00Z'}):
            with self.subTest(change=change):
                self.assertRefused(**change)

    def test_added_review_result_or_failure_reason_is_refused(self):
        self._event()
        self.assertRefused(failure_reason='NO_FINAL_MESSAGE')

    def test_unknown_claim_with_failure_reason_or_no_session_is_refused(self):
        for change in ({'failure_reason': 'NO_FINAL_MESSAGE',
                        'native_session_id': 'review-session'}, {}):
            with self.subTest(change=change):
                self._unknown_from_claimed(**change)
                self._event()
                self.assertRefused()


if __name__ == '__main__':
    unittest.main()
