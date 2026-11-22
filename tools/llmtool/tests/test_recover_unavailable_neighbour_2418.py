"""#2418: an older Task on the same D6 that ended DEVELOPER_UNAVAILABLE before any
provider was started must not make the D6 process status UNKNOWN.

Shaped like the real case: D6 h1assigntime-gwagent-2281-20261120 also carried a
read-only investigation Task refused before the model call
(DEVELOPER_GW_AGENT_POLICY_REFUSED_PROFILE_INVALID, no execution record), which
made every --recover-claim on that D6 refuse REPAIR_RECOVERY_EVIDENCE_INVALID.
The recovery runs here with the real D6 process reader (process_status=None).
"""
import contextlib
import unittest
from unittest import mock

import test_recover_review_no_launch_2492 as base
from test_recover_ec2_claim_2475 import EXECUTOR
from llmtool import review_repair_recovery as rec
from llmtool.errors import LLMToolError
from llmtool.task_store import new_id


@contextlib.contextmanager
def _neighbour_on_d6(store, record):
    """One more Task record bound to the same D6, visible to every reader of the store
    (including the verification that runs inside the store's locked update)."""
    neighbour_id = new_id()
    real_ids, real_get = store.task_ids, store.get

    def get(task_id, *args, **kwargs):
        if task_id == neighbour_id:
            return dict(record)
        return real_get(task_id, *args, **kwargs)

    with mock.patch.object(store, 'task_ids', lambda: [*real_ids(), neighbour_id]), \
            mock.patch.object(store, 'get', get):
        yield


def _neighbour(state, execution=None):
    return {'task_id': 'x', 'developer': {
        'state': state, 'executor_task_id': EXECUTOR, 'execution': execution,
        'error_code': 'DEVELOPER_GW_AGENT_POLICY_REFUSED_PROFILE_INVALID'}}


class RecoverWithUnavailableNeighbourTests(unittest.TestCase):
    # The #2492 fixture (setUp and helpers) without re-running its tests.
    setUp = base.RecoverReviewNoLaunchTests.setUp
    _cas = base.RecoverReviewNoLaunchTests._cas
    _claim = base.RecoverReviewNoLaunchTests._claim
    _recover = base.RecoverReviewNoLaunchTests._recover

    def test_unavailable_neighbour_without_execution_is_not_a_process(self):
        with _neighbour_on_d6(self.store, _neighbour('DEVELOPER_UNAVAILABLE')):
            self.assertEqual(rec._d6_process_status(self.store, EXECUTOR), 'NONE')
            result = self._recover(process_status=None)
        self.assertTrue(result['recovered'])
        self.assertEqual(self._claim()['state'], 'CLAIMED')

    def test_launching_or_unknown_neighbour_still_refuses(self):
        for state in ('DEVELOPER_DISPATCHING', 'DEVELOPER_OUTCOME_UNKNOWN'):
            with self.subTest(state=state):
                with _neighbour_on_d6(self.store, _neighbour(state)):
                    self.assertEqual(rec._d6_process_status(self.store, EXECUTOR), 'UNKNOWN')
                    with self.assertRaises(LLMToolError) as caught:
                        self._recover(process_status=None)
                self.assertEqual(caught.exception.review_repair_code,
                                 'REPAIR_RECOVERY_EVIDENCE_INVALID')
                self.assertEqual(self._claim()['state'], 'UNKNOWN')

    def test_unavailable_neighbour_with_unverifiable_execution_still_refuses(self):
        with _neighbour_on_d6(self.store, _neighbour('DEVELOPER_UNAVAILABLE', {'status': 'UNKNOWN'})):
            self.assertEqual(rec._d6_process_status(self.store, EXECUTOR), 'UNKNOWN')



if __name__ == '__main__':
    unittest.main()
