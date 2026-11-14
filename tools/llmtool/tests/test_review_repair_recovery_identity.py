"""#1928 PR1: E1 claim checks for UNKNOWN repair claim recovery."""
import json
import unittest

from llmtool import review_repair, review_repair_recovery as rec
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


def identity_root():
    return dict(root(), pm_provenance={'pm_native_session_id': 'pm-1', 'pm_cwd': '/repo'},
                pm_assignment={'goal': 'Fix it.'})


def recorded(**packet):
    """The repair Task exactly as review_repair._record_repair_task records it."""
    seen = {}

    def record_pm(args, *, store, task_id, input_stream, return_record):
        seen.update(json.loads(input_stream.read()), session=args.pm_session_id)
        return {'task_id': task_id}
    packet = {'verdict_comment_id': 9, 'failed_head': HEAD, 'version': 1, 'findings_count': 3,
              'reviewer_text_excerpt_untrusted_data': 'x', **packet}
    review_repair._record_repair_task(
        task=identity_root(), developer={'provider_profile': 'gw-agent', 'acceptance': ['a']},
        packet=packet, attempt=1, store=None, repair_task_id=REPAIR, record_pm=record_pm)
    return {'task_id': REPAIR, 'request': seen['request'], 'response': seen['response'],
            'pm_assignment': json.loads(seen['response'])['developer_assignment'],
            'pm_provenance': {'pm_native_session_id': seen['session']}}


class RecoveryRepairIdentityTests(unittest.TestCase):
    code = RecoveryClaimTests.code

    def test_e2_accepts_the_runtime_recorded_repair_task(self):
        self.assertIsNone(rec.check_identity(identity_root(), recorded(), CLAIM))

    def test_e2_refuses_each_mismatch(self):
        good = recorded()
        cases = [dict(good, task_id='e' * 32), dict(good, request=good['request'][:-1] + '0'),
                 dict(good, response=good['response'].replace(ROOT, 'e' * 32)),
                 recorded(failed_head='e' * 40), recorded(verdict_comment_id=10),
                 recorded(version=2), dict(good, pm_provenance={'pm_native_session_id': 'pm-2'}),
                 dict(good, response=None), dict(good, pm_assignment={'acceptance': []}),
                 dict(good, pm_provenance={'pm_native_session_id': None}), 'not a record',
                 dict(good, pm_assignment={'acceptance': [
                     good['pm_assignment']['acceptance'][-1].replace('count 3.', 'count 03.')]})]
        for repair in cases:
            with self.subTest(repair=repair):
                self.assertEqual(self.code(rec.check_identity, identity_root(), repair, CLAIM),
                                 'REPAIR_RECOVERY_EVIDENCE_INVALID')
        for root, claim in ((None, CLAIM), (identity_root(), {}), (identity_root(), None)):
            with self.subTest(root=root, claim=claim):
                self.assertEqual(self.code(rec.check_identity, root, good, claim),
                                 'REPAIR_RECOVERY_EVIDENCE_INVALID')


if __name__ == '__main__':
    unittest.main()
