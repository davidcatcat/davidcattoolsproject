"""#1132 item 1: a newer open claim keeps an older PASS from closing a merged Task."""
import unittest

from llmtool import task_advance

HEAD = 'a' * 40
MERGE = 'b' * 40
PASS = {'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1, 'outcome': 'REVIEW_PASS'}


def _target(*newer, events=(PASS,)):
    claims = [{'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'COMPLETED'}]
    claims += [{'pr': 17, 'head_sha': HEAD, 'version': 2, 'state': s} for s in newer]
    record = {'task_id': '1' * 32, 'completion': {'state': 'PM_EVIDENCE_READY', 'evidence': {
        'result': {'result_head': HEAD}, 'review_pr_ci': {'stage': 'READY'}}}}
    pr = {'number': 17, 'merged': True, 'head': {'sha': HEAD}, 'merge_commit_sha': MERGE}
    return task_advance._merged_review_target(record, claims, list(events), pr, None,
                                              lambda _path: None)


class MergedTargetNewerClaimTests(unittest.TestCase):
    def test_only_the_newest_claim_at_the_head_can_close(self):
        self.assertEqual(_target(), (17, MERGE))
        for state in ('CLAIMED', 'DISPATCHED', 'UNKNOWN', 'COMPLETED'):
            with self.subTest(state=state):
                self.assertIsNone(_target(state))
        failed = dict(PASS, version=2, outcome='REVIEW_FAIL')
        self.assertIsNone(_target('COMPLETED', events=(PASS, failed)))
        passed = dict(PASS, version=2)
        self.assertEqual(_target('COMPLETED', events=(PASS, passed)), (17, MERGE))


if __name__ == '__main__':
    unittest.main()
