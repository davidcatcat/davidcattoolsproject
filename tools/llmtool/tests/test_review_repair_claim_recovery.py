"""#1928 PR4: recover_unknown_claim under the chain lock: once, idempotent, race-safe."""
import threading
import unittest
from unittest import mock

import test_task_store_claim_recovery as base
from llmtool import lineage_counts, review_repair_recovery as rec
from llmtool.errors import LLMToolError


class ClaimRecoveryOrchestratorTests(unittest.TestCase):
    # A fresh store holding one UNKNOWN claim (and one CLAIMED claim of another PR).
    setUp, cas = base.ClaimRecoveryStoreTests.setUp, base.ClaimRecoveryStoreTests.cas

    def run_recover(self, **kwargs):
        args = dict(store=self.store, task_id=self.task_id, executor_task='e-1', pr_number=17,
                    repair_task_id=self.repair)
        return rec.recover_unknown_claim(**{**args, **kwargs})

    @mock.patch.object(rec, 'verify_recovery', return_value=None)
    def test_recovers_once_then_repeats_without_a_write(self, verify):
        first = self.run_recover()
        claim = dict(self.claims[1], state='DISPATCHED')
        self.assertEqual(first, {'recovered': True, 'claim': claim})
        saved = self.store.get(self.task_id)
        self.assertEqual(self.run_recover(), {'recovered': False, 'claim': claim})
        self.assertEqual(self.store.get(self.task_id), saved)
        self.assertEqual(verify.call_count, 1)

    @mock.patch.object(rec, 'verify_recovery', side_effect=LLMToolError('evidence'))
    def test_refusals_leave_the_claim_unknown(self, verify):
        with self.assertRaises(LLMToolError):
            self.run_recover()
        for patch, kwargs, code in (
                (None, {'task_id': 'f' * 32}, 'REPAIR_RECOVERY_ARGUMENT_INVALID'),
                (mock.patch.object(lineage_counts, 'acquire_chain_lock', side_effect=OSError()),
                 {}, 'REPAIR_CHAIN_LOCK_UNAVAILABLE')):
            with self.subTest(code=code), (patch or mock.MagicMock()), \
                    self.assertRaises(LLMToolError) as caught:
                self.run_recover(**kwargs)
            self.assertEqual(caught.exception.review_repair_code, code)
        self.assertEqual(self.store.get(self.task_id), self.unknown)

    @mock.patch.object(rec, 'verify_recovery', return_value=None)
    def test_parallel_recoveries_write_once(self, verify):
        for _ in range(20):
            self.setUp()
            barrier, results = threading.Barrier(2), []

            def worker():
                barrier.wait()
                results.append(self.run_recover()['recovered'])
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(sorted(results), [False, True])
            claims = self.store.get(self.task_id)['review_repair_claims']
            self.assertEqual(claims[1]['state'], 'DISPATCHED')
        self.assertEqual(verify.call_count, 20)


if __name__ == '__main__':
    unittest.main()
