"""#2087 slice R2 (G4 P2, piece 2): _repairing ignores purpose-carrying claims.

Exercises the repair-stage check directly on real TaskStore records: a
higher-version dispute ruling claim must not clear REPAIRING while a repair
claim is in flight; a higher-version formal review claim still does.
Placeholder IDs and digests only; no runtime started.
"""
import unittest

from llmtool import chat_tui_status
from test_dispute_ruling_claim_readers import (
    _arbiter_claim, _base_task_record, _create_store_with_claims, _dispute_entry,
    _formal_claim)

import review_trust_fixture


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
    def _with_claims(self, claims):
        """Store with the repair claim, then formal/ruling claims via the readers helper."""
        base, contract_digest = _base_task_record()
        formal = [c for c in claims if 'purpose' not in c]
        ruling = [c for c in claims if 'purpose' in c]
        base['review_repair_claims'] = [_repair_claim()]
        claims_list = list(formal) + list(ruling)
        if not claims_list:
            with_dispute = False
        else:
            with_dispute = bool(_dispute_entry() is not None and ruling)
        store, rec, _ = _create_store_with_claims(claims_list, with_dispute=with_dispute)
        return store, rec

    def test_higher_version_ruling_claim_keeps_repairing(self):
        base, contract_digest = _base_task_record()
        formal = _formal_claim(contract_digest, version=1)
        ruling = _arbiter_claim(contract_digest, state='COMPLETED', verdict='RULING',
                                version=2)
        store, rec = self._with_claims([formal, ruling])
        claim = chat_tui_status._repairing(store.get(base['task_id']))
        self.assertIsNotNone(claim)
        self.assertEqual(claim['version'], 1)
        self.assertEqual(claim['state'], 'CLAIMED')

    def test_higher_version_formal_claim_clears_repairing(self):
        base, contract_digest = _base_task_record()
        formal = _formal_claim(contract_digest, version=2)
        store, rec = self._with_claims([formal])
        claim = chat_tui_status._repairing(store.get(base['task_id']))
        self.assertIsNone(claim)

    def test_no_review_claims_keeps_repairing(self):
        store, rec = self._with_claims([])
        claim = chat_tui_status._repairing(store.get(rec['task_id']))
        self.assertIsNotNone(claim)
        self.assertEqual(claim['version'], 1)


if __name__ == '__main__':
    unittest.main()
