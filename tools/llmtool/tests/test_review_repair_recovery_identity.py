"""#1928 PR1: E1 claim checks for UNKNOWN repair claim recovery."""
import unittest

from llmtool import review_repair_recovery as rec
from llmtool.errors import LLMToolError

ROOT, REPAIR, HEAD = 'a' * 32, 'b' * 32, 'c' * 40
CLAIM = {'executor_task': 'exec-1', 'pr': 17, 'head_sha': HEAD, 'version': 1,
         'attempt': 1, 'family': 'pr-17', 'state': 'UNKNOWN', 'repair_task_id': REPAIR,
         'comment_id': 9, 'packet_sha256': 'd' * 64, 'claimed_at': '2026-11-15T00:00:00Z'}


def root(*claims):
    return {'task_id': ROOT, 'review_repair_claims': list(claims or [CLAIM])}


class RecoveryClaimTests(unittest.TestCase):
    def code(self, call, *args, **kwargs):
        with self.assertRaises(LLMToolError) as caught:
            call(*args, **kwargs)
        self.assertNotIn(REPAIR, str(caught.exception))
        return caught.exception.review_repair_code

    def e1(self, record, repair=REPAIR, others=(), executor='exec-1', pr=17):
        return rec.check_claim(record, repair, executor_task=executor, pr_number=pr,
                               others=[record, *others])

    def test_e1_returns_the_one_unknown_claim(self):
        other = {'task_id': 'e' * 32,
                 'review_repair_claims': [dict(CLAIM, repair_task_id='f' * 32)]}
        self.assertEqual(self.e1(root(), others=[other, {'task_id': 'f' * 32}]), CLAIM)

    def test_e1_refuses_each_bad_claim(self):
        bad = [{'state': 'DISPATCHED'}, {'state': 'CLAIMED'}, {'executor_task': 'exec-2'},
               {'pr': 18}, {'head_sha': 'C' * 40}, {'attempt': True}, {'version': '1'},
               {'comment_id': None}, {'family': 'pr-17-ec2'}, {'family': None},
               {'attempt': 4}, {'version': 0}, {'comment_id': -1}, {'family': ''},
               {'family': 'PR-17'}, {'packet_sha256': 'x'}, {'packet_sha256': None},
               {'claimed_at': '2026-11-15'}]
        for change in bad:
            with self.subTest(change=change):
                code = self.code(self.e1, root(dict(CLAIM, **change)))
                self.assertEqual(code, 'REPAIR_RECOVERY_CLAIM_INVALID')
        for record in (root(CLAIM, dict(CLAIM, version=2)), root(CLAIM, 'not a claim'),
                       root(dict(CLAIM, repair_task_id='e' * 32)), {'task_id': None}):
            with self.subTest(record=record):
                self.assertEqual(self.code(self.e1, record), 'REPAIR_RECOVERY_CLAIM_INVALID')
        # A bool PR number equals 1 but is not an int PR.
        self.assertEqual(self.code(self.e1, root(dict(CLAIM, pr=True)), pr=1),
                         'REPAIR_RECOVERY_CLAIM_INVALID')

    def test_e1_refuses_bad_arguments(self):
        for kwargs in ({'repair': 'B' * 32}, {'pr': True}, {'pr': 0}, {'executor': ''}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.code(self.e1, root(), **kwargs),
                                 'REPAIR_RECOVERY_ARGUMENT_INVALID')

    def test_e1_refuses_repair_named_elsewhere_or_root_that_is_a_repair(self):
        for named, expected in ((REPAIR, 'REPAIR_RECOVERY_CLAIM_INVALID'),
                                (ROOT, 'REPAIR_ROOT_TASK_REQUIRED')):
            other = {'task_id': 'e' * 32,
                     'review_repair_claims': [dict(CLAIM, repair_task_id=named)]}
            with self.subTest(named=named):
                self.assertEqual(self.code(self.e1, root(), others=[other]), expected)
        for other in ({'task_id': 'e' * 32, 'review_repair_claims': {}}, 'not a record'):
            with self.subTest(other=other):
                self.assertEqual(self.code(self.e1, root(), others=[other]),
                                 'REPAIR_RECOVERY_CLAIM_INVALID')


if __name__ == '__main__':
    unittest.main()
