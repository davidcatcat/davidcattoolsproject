"""#2087 slice R1: the task status summary skips ruling claims.

A dispute ruling claim (A1, merged as ba7fdf3e) is stored in the Task's
``review_dispatch_claim`` list next to formal review claims and is
identified by its ``purpose`` key ('dispute_ruling'). The status summary in
``llmtool/task_status.py`` must not show a ruling claim as the Task's formal
review claim state; the formal-claim behaviour and the summary's JSON keys
are unchanged.

The A1-shaped records (a formal claim plus a ruling claim) are built with
the record and claim helpers of the A1 store test module
(``test_dispute_store_schema.py``) and written through the real TaskStore;
those helpers are reused, not copied or changed. All IDs, sessions and
digests are fixed placeholder values (Owner #763 A).
"""

import unittest

from llmtool import task_status

try:  # Same-directory import (pytest layout).
    import test_dispute_store_schema as _a1
except ModuleNotFoundError:  # Executed as ``tests.test_task_status_ruling_2087``.
    from tests import test_dispute_store_schema as _a1

# Reused from the A1 store test module (read-only), never redefined here.
_BASE_RECORD = _a1._base_record
_ARBITER_CLAIM = _a1._arbiter_claim
_RULING_RESULT = _a1._ruling_result
_NEW_STORE = _a1.DisputeStoreSchemaTests._new_store
_CLAIM_ARBITER = _a1.DisputeStoreSchemaTests._claim_arbiter
_COMPLETE_RULING = _a1.DisputeStoreSchemaTests._complete_codex_arbiter

SUMMARY_KEYS = (
    'task_id', 'pm_session_id', 'state', 'executor_d6', 'last_event',
    'review_claim_state', 'pending_inbox_count',
)


def _summary_of(record):
    return task_status._summary(record)


def _sans_state(summary):
    """The summary minus the derived task state (a whole-record function)."""
    return {key: value for key, value in sorted(summary.items()) if key != 'state'}


def _ruling_shape_record(verdict):
    """A1-shaped record: the base formal claim plus a COMPLETED ruling claim."""
    rec = _BASE_RECORD(with_dispute=False)
    rec['review_dispatch_claim'] = rec['review_dispatch_claim'] + [
        _ARBITER_CLAIM(
            state='COMPLETED', version=2,
            review_result=_RULING_RESULT(
                rec['task_binding']['contract_digest'],
                verdict=verdict, outcome='UPHELD'))
    ]
    return rec


class TaskStatusRulingTests(unittest.TestCase):
    # --- A1: a ruling claim is not shown as the formal review claim ---

    def test_a1_ruling_claim_behind_formal_claim_shows_formal_state(self):
        for verdict in ('RULING', 'BLOCKED'):
            with self.subTest(verdict=verdict):
                store, _ = _NEW_STORE(self, with_history=True)
                _CLAIM_ARBITER(self, store, with_history=True)
                saved = _COMPLETE_RULING(
                    self, store, verdict=verdict, with_history=True)
                read_back = store.get(_a1.TASK)
                self.assertEqual(read_back, saved)
                # The real store really holds [formal claim, ruling claim].
                self.assertEqual(len(read_back['review_dispatch_claim']), 2)
                self.assertNotIn('purpose', read_back['review_dispatch_claim'][0])
                ruling = read_back['review_dispatch_claim'][1]
                self.assertEqual(ruling['purpose'], 'dispute_ruling')
                self.assertEqual(ruling['state'], 'COMPLETED')
                self.assertEqual(ruling['review_result']['verdict'], verdict)
                # The summary shows the formal claim's review claim state,
                # not the ruling claim's, and matches the formal-only
                # summary exactly.
                summary = _summary_of(read_back)
                self.assertEqual(summary['review_claim_state'], 'COMPLETED')
                self.assertEqual(
                    _sans_state(summary),
                    _sans_state(_summary_of(_BASE_RECORD(with_dispute=False))))
                self.assertEqual(set(summary), set(SUMMARY_KEYS))

    # --- A2: single ruling-claim dict, no claim, malformed last item ---

    def test_a2_single_ruling_claim_dict_is_unknown_not_none(self):
        rec = _ruling_shape_record('RULING')
        rec['review_dispatch_claim'] = rec['review_dispatch_claim'][1]
        summary = _summary_of(rec)
        self.assertEqual(summary['review_claim_state'], 'UNKNOWN')
        self.assertNotEqual(summary['review_claim_state'], 'NONE')
        self.assertEqual(set(summary), set(SUMMARY_KEYS))

    def test_a2_single_formal_claim_dict_is_unchanged(self):
        rec = _BASE_RECORD(with_dispute=False)
        rec['review_dispatch_claim'] = rec['review_dispatch_claim'][0]
        self.assertEqual(_summary_of(rec)['review_claim_state'], 'COMPLETED')

    def test_a2_no_claim_recorded_is_none(self):
        rec = _BASE_RECORD(with_dispute=False)
        del rec['review_dispatch_claim']
        self.assertEqual(_summary_of(rec)['review_claim_state'], 'NONE')

    def test_a2_empty_claim_list_is_unknown(self):
        rec = _BASE_RECORD(with_dispute=False)
        rec['review_dispatch_claim'] = []
        self.assertEqual(_summary_of(rec)['review_claim_state'], 'UNKNOWN')

    def test_a2_malformed_last_item_is_unknown(self):
        # Non-dict items stay where they are: the ruling claim is dropped,
        # but a malformed last item still gives UNKNOWN.
        store, _ = _NEW_STORE(self, with_history=True)
        _CLAIM_ARBITER(self, store, with_history=True)
        _COMPLETE_RULING(self, store, verdict='RULING', with_history=True)
        rec = store.get(_a1.TASK)
        rec['review_dispatch_claim'] = rec['review_dispatch_claim'] + [
            'not-a-claim']
        self.assertEqual(_summary_of(rec)['review_claim_state'], 'UNKNOWN')

    # --- A3: formal-claim-only summaries and JSON keys are unchanged ---

    def test_a3_formal_claims_only_summary_and_keys_unchanged(self):
        store, _ = _NEW_STORE(self, with_dispute=False)
        rec = store.get(_a1.TASK)
        summary = _summary_of(rec)
        # The real-store round-trip summary matches the summary of an
        # identically-shaped helper record (modulo the derived task state):
        # formal-claim behaviour and the summary's JSON keys are unchanged.
        self.assertEqual(
            _sans_state(summary),
            _sans_state(_summary_of(_BASE_RECORD(with_dispute=False))))
        self.assertEqual(summary['review_claim_state'], 'COMPLETED')
        self.assertEqual(
            set(summary),
            {'task_id', 'pm_session_id', 'state', 'executor_d6',
             'last_event', 'review_claim_state', 'pending_inbox_count'})


if __name__ == '__main__':
    unittest.main()