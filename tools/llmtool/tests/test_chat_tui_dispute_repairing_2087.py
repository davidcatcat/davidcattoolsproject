"""#2087 slice R2 (G4 P2, piece 2): _repairing ignores purpose-carrying claims.

Exercises the repair-stage check directly on real TaskStore records: a
higher-version dispute ruling claim must not clear REPAIRING while a repair
claim is in flight; a higher-version formal review claim still does; with no
ruling claim the result is unchanged.
Placeholder IDs and digests only; no runtime started.
"""

import tempfile
import unittest
from pathlib import Path

from llmtool import chat_tui_status, task_store

import review_trust_fixture
from test_dispute_ruling_claim_readers import (
    EXECUTOR, HEAD, PR, TASK,
    _arbiter_claim, _base_task_record, _dispute_entry, _formal_claim)


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def _repair_claim(version=1):
    return {
        'executor_task': 'executor-d6', 'pr': 42, 'head_sha': 'c' * 40,
        'version': version, 'attempt': 1, 'state': 'CLAIMED', 'comment_id': 1001,
        'packet_sha256': '5' * 64, 'claimed_at': '2026-11-23T00:00:00Z',
        'family': 'review_repair', 'repair_task_id': '6' * 32,
    }


class RepairingClaimChecksTests(unittest.TestCase):
    def _new_store(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return task_store.TaskStore(Path(temp.name).resolve() / 'store')

    def _with_claims(self, claims):
        """Store a record carrying the repair claim plus review claims.

        Mirrors the readers test module's write order (a method there, so
        rebuilt here from module-level helpers): the base record carries the
        repair claim and any formal review claim at create time; the dispute
        entry and the ruling claim are then written with
        TaskStore.compare_and_update against the current record_sha256, the
        ruling claim first CLAIMED, then in its final state.
        """
        store = self._new_store()
        base, contract_digest = _base_task_record()
        formal = [c for c in claims if 'purpose' not in c]
        ruling = [c for c in claims if 'purpose' in c]
        base['review_repair_claims'] = [_repair_claim()]
        if formal:
            base['review_dispatch_claim'] = list(formal)
        rec = store.create(base)
        if ruling:
            rec = store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_disputes=[_dispute_entry()])
            for arbiter in ruling:
                claimed = {key: value for key, value in arbiter.items()
                           if key != 'review_result'}
                claimed['state'] = 'CLAIMED'
                claimed['native_session_id'] = None
                current_claims = list(rec.get('review_dispatch_claim') or [])
                rec = store.compare_and_update(
                    TASK, expected={'record_sha256': rec['record_sha256']},
                    review_dispatch_claim=current_claims + [claimed])
                if arbiter['state'] != 'CLAIMED':
                    rec = store.compare_and_update(
                        TASK, expected={'record_sha256': rec['record_sha256']},
                        review_dispatch_claim=current_claims + [arbiter])
        return store, rec, contract_digest

    def test_higher_version_ruling_claim_keeps_repairing(self):
        """A higher-version ruling claim (purpose-carrying) keeps the repair claim."""
        _, contract_digest = _base_task_record()
        formal = _formal_claim(contract_digest, version=1)
        ruling = _arbiter_claim(contract_digest, state='COMPLETED', verdict='RULING',
                                version=2)
        store, rec, _ = self._with_claims([formal, ruling])
        claim = chat_tui_status._repairing(store.get(TASK))
        self.assertIsNotNone(claim)
        self.assertEqual(claim['version'], 1)
        self.assertEqual(claim['state'], 'CLAIMED')
        self.assertEqual(claim['executor_task'], EXECUTOR)
        self.assertEqual(claim['pr'], PR)
        self.assertEqual(claim['head_sha'], HEAD)

    def test_higher_version_formal_claim_clears_repairing(self):
        """A higher-version formal review claim still clears the repair claim."""
        _, contract_digest = _base_task_record()
        formal = _formal_claim(contract_digest, version=2)
        store, rec, _ = self._with_claims([formal])
        claim = chat_tui_status._repairing(store.get(TASK))
        self.assertIsNone(claim)

    def test_no_ruling_claim_is_unchanged(self):
        """With no ruling claim present, the formal review claim result is unchanged."""
        _, contract_digest = _base_task_record()
        formal = _formal_claim(contract_digest, version=1)
        store, rec, _ = self._with_claims([formal])
        claim = chat_tui_status._repairing(store.get(TASK))
        self.assertIsNotNone(claim)
        self.assertEqual(claim['version'], 1)
        self.assertEqual(claim['state'], 'CLAIMED')


if __name__ == '__main__':
    unittest.main()
