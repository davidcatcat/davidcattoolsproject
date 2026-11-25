"""#2728: an invalid High Debug value refuses before launch, keeping the claim CLAIMED.

C2: the refusal keeps its existing code and the review-repair claim stays
CLAIMED. C3: a corrected retry on the same Task store dispatches the repair
without any recover-claim step.
"""

import unittest

from llmtool.errors import LLMToolError

import review_trust_fixture
import test_repair_replay_rca_2722 as _replay
from test_repair_replay_rca_2722 import HD_FAMILY, HD_REF, TASK


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


class RepairRcaPrelaunch2728Tests(unittest.TestCase):
    """Reuse the #2722 replay harness, but not its test methods."""

    setUp = _replay.RepairReplayRca2722Tests.setUp
    tearDown = _replay.RepairReplayRca2722Tests.tearDown
    _recorder = staticmethod(_replay.RepairReplayRca2722Tests._recorder)
    _seed_debug_required_replay = _replay.RepairReplayRca2722Tests._seed_debug_required_replay
    _run_through_delegate = _replay.RepairReplayRca2722Tests._run_through_delegate

    def _claim_state(self):
        claims = self.store.get(TASK)['review_repair_claims']
        self.assertEqual(len(claims), 1)
        return claims[0].get('state')

    def _assert_refusal_then_valid_retry(self, expected_prefix, **invalid):
        self._seed_debug_required_replay()
        dispatched = []
        with self.assertRaises(LLMToolError) as caught:
            self._run_through_delegate(dispatched=dispatched, **invalid)
        self.assertTrue(str(caught.exception).startswith(expected_prefix),
                        msg=str(caught.exception))
        self.assertEqual(getattr(caught.exception, 'code', None), expected_prefix)
        self.assertIs(getattr(caught.exception, 'prelaunch_refusal', None), True)
        self.assertEqual(dispatched, [])
        self.assertEqual(self._claim_state(), 'CLAIMED')

        # C3: the same claim is replayed on a corrected retry, with no
        # recover-claim call in between.
        result, retried = self._run_through_delegate(
            high_debug_task=TASK, high_debug_ref=HD_REF,
            high_debug_family=HD_FAMILY)
        self.assertTrue(result['replayed'])
        self.assertEqual(len(retried), 1)
        self.assertEqual(retried[0].high_debug_task, TASK)
        self.assertEqual(retried[0].high_debug_ref, HD_REF)
        self.assertEqual(retried[0].high_debug_family, HD_FAMILY)

    def test_invalid_ref_keeps_claim_claimed_and_valid_retry_dispatches(self):
        self._assert_refusal_then_valid_retry(
            'DELEGATE_HIGH_DEBUG_REF_INVALID',
            high_debug_task=TASK,
            high_debug_ref='https://example.com/bad',
            high_debug_family=HD_FAMILY)

    def test_invalid_task_id_keeps_claim_claimed_and_valid_retry_dispatches(self):
        self._assert_refusal_then_valid_retry(
            'DELEGATE_HIGH_DEBUG_TASK_INVALID',
            high_debug_task='not-a-task-id',
            high_debug_ref=HD_REF,
            high_debug_family=HD_FAMILY)


if __name__ == '__main__':
    unittest.main()
