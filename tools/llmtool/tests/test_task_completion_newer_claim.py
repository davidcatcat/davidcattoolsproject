"""#1132 item 1: a newer open review claim at the same PR and head blocks an older PASS."""
import unittest

from llmtool import task_completion

HEAD = 'a' * 40
BODY = 'marker\nVerdict: PASS | Version: v1 | HEAD: ' + HEAD + '\nFORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n'


def _record(*claims):
    return {'review_dispatch_claim': [dict(pr=17, head_sha=HEAD, **c) for c in claims]}


class NewerClaimPendingTests(unittest.TestCase):
    def test_only_a_newer_open_claim_at_the_same_pr_and_head_blocks(self):
        pending = task_completion._newer_claim_pending
        done = {'version': 1, 'state': 'COMPLETED'}
        for state in ('CLAIMED', 'DISPATCHED', 'UNKNOWN'):
            with self.subTest(state=state):
                self.assertTrue(pending(_record(done, {'version': 2, 'state': state}),
                                        17, HEAD, BODY))
        self.assertFalse(pending(_record(done, {'version': 2, 'state': 'COMPLETED'}), 17, HEAD, BODY))
        self.assertFalse(pending(_record({'version': 1, 'state': 'DISPATCHED'}), 17, HEAD, BODY))
        self.assertFalse(pending(_record(done, {'version': 2, 'state': 'DISPATCHED'}), 18, HEAD, BODY))
        self.assertFalse(pending(_record(done, {'version': 2, 'state': 'DISPATCHED'}), 17, 'b' * 40, BODY))
        self.assertFalse(pending(None, 17, HEAD, BODY))
        self.assertFalse(pending(_record(done), 17, HEAD, 'no verdict line'))


if __name__ == '__main__':
    unittest.main()
