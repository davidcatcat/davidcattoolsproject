"""#1502 WP-D D11: review_repair answers one failed exact-head required CI check.

Fixtures come from test_review_repair. A CI repair shares the '<base>-ec2'
test-failure count with EC2 remote-test repairs (policy test_fail_max).
"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llmtool import review_repair
from llmtool.errors import LLMToolError

import review_trust_fixture
from test_review_repair import (EXECUTOR, HEAD, PR, TASK, TempTaskStore, _receipt,
                                _record)

EC2_FAMILY = f'pr-{PR}-ec2'


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def _ci(*, head=HEAD, check_name='check (Linux 3.14)', check_run_id=7,
        conclusion='failure', excerpt='FAIL: test_example\nAssertionError'):
    return {'head': head, 'check_name': check_name, 'check_run_id': check_run_id,
            'conclusion': conclusion, 'excerpt': excerpt}


class CiFailRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TempTaskStore(self.temp.name, _record())
        self.calls = []
        dispatch = review_repair.worker_dispatch
        policy = json.loads(Path(dispatch._POLICY_PATH).read_text(encoding='utf-8'))
        policy_path = Path(self.temp.name).resolve() / 'worker-dispatch.json'
        policy_path.write_text(json.dumps(policy), encoding='utf-8')
        for patcher in (
                patch.object(dispatch, '_POLICY_PATH', policy_path),
                patch.object(dispatch, 'evaluate',
                             return_value=type('Decision', (), {'allowed': True})()),
                patch.object(review_repair.shutil, 'which', return_value='/usr/bin/agy')):
            patcher.start()
            self.addCleanup(patcher.stop)

    def dispatch(self, **kwargs):
        def adapter(**request):
            self.calls.append(request)
            return {'status': 'DISPATCHED', 'head': HEAD}

        def recorder(**request):
            return {'task_id': request['repair_task_id'],
                    'pm_assignment': {'acceptance': ['Repair the failed check.']}}

        def no_github(_path):
            raise AssertionError('a CI repair must not read review comments')

        return review_repair.repair_review(
            store=self.store, task_id=TASK, executor_task=EXECUTOR, pr_number=PR,
            github_get=no_github, dispatch_adapter=adapter,
            current_head=lambda _task, _executor: HEAD,
            repair_task_recorder=recorder, **kwargs)

    def _claims(self):
        return self.store.get(TASK).get('review_repair_claims')

    def _fail_last_claim(self):
        record = self.store.get(TASK)
        record['review_repair_claims'][-1]['state'] = 'FAILED'
        self.store = TempTaskStore(self.temp.name, record)

    def _assert_refused(self, code, **kwargs):
        before = self._claims()
        calls = len(self.calls)
        with self.assertRaises(LLMToolError) as caught:
            self.dispatch(**kwargs)
        self.assertEqual(caught.exception.review_repair_code, code)
        self.assertEqual(self._claims(), before)
        self.assertEqual(len(self.calls), calls)

    def test_valid_ci_failure_dispatches_one_claim_in_ec2_family(self):
        for name in review_repair.CI_REPAIR_CHECKS:
            with self.subTest(check=name):
                self.store = TempTaskStore(self.temp.name, _record())
                self.calls = []
                result = self.dispatch(ci_failure=_ci(check_name=name))
                claims = self._claims()
                self.assertEqual(len(claims), 1)
                claim = claims[0]
                self.assertEqual(result['repair_task_id'], claim['repair_task_id'])
                _, identity = review_repair._ci_failure(
                    self.store.get(TASK), executor_task=EXECUTOR, pr_number=PR,
                    ci_failure=_ci(check_name=name, excerpt='another excerpt'))
                self.assertEqual(claim['packet_sha256'], identity)
                self.assertEqual((claim['family'], claim['attempt'], claim['head_sha'],
                                  claim['state']), (EC2_FAMILY, 1, HEAD, 'DISPATCHED'))
                self.assertIsNone(claim['comment_id'])
                self.assertEqual(len(self.calls), 1)
                packet = self.calls[0]['packet']
                self.assertEqual((packet['failed_head'], packet['version'],
                                  packet['verdict_comment_id']), (HEAD, 0, 0))
                text = packet['reviewer_text_excerpt_untrusted_data']
                self.assertTrue(text.startswith(
                    f'{review_repair.CI_PACKET_HEADER} check={name} run=7 '
                    f'conclusion=failure head={HEAD}\n'))
                self.assertIn('FAIL: test_example', text)

    def test_malformed_ci_failure_refuses_without_store_write(self):
        missing = _ci()
        missing.pop('excerpt')
        extra = dict(_ci(), status='completed')
        cases = {
            'wrong check name': _ci(check_name='check (docs)'),
            'conclusion cancelled': _ci(conclusion='cancelled'),
            'conclusion neutral': _ci(conclusion='neutral'),
            'short head': _ci(head='c' * 39),
            'upper-case head': _ci(head='C' * 40),
            'run id zero': _ci(check_run_id=0),
            'run id bool': _ci(check_run_id=True),
            'run id text': _ci(check_run_id='7'),
            'excerpt not text': _ci(excerpt=None),
            'oversize excerpt bytes': _ci(excerpt='x' * (review_repair.CI_EXCERPT_MAX_BYTES + 1)),
            'oversize excerpt lines': _ci(excerpt='line\n' * (review_repair.CI_EXCERPT_MAX_LINES + 1)),
            'missing field': missing,
            'extra field': extra,
            'not a dict': [_ci()],
        }
        for label, ci_failure in cases.items():
            with self.subTest(label):
                self._assert_refused('CI_FAILURE_INVALID', ci_failure=ci_failure)
                self.assertIsNone(self._claims())
        with self.subTest('both ci_failure and remote_test_receipt'):
            self._assert_refused('CI_FAILURE_INVALID', ci_failure=_ci(),
                                 remote_test_receipt=_receipt())
            self.assertIsNone(self._claims())

    def test_ec2_and_ci_repairs_share_one_count(self):
        first = self.dispatch(remote_test_receipt=_receipt())
        self.assertEqual((first['claim']['family'], first['claim']['attempt']),
                         (EC2_FAMILY, 1))
        self._fail_last_claim()
        second = self.dispatch(ci_failure=_ci(check_run_id=8))
        self.assertEqual((second['claim']['family'], second['claim']['attempt']),
                         (EC2_FAMILY, 2))
        self._fail_last_claim()
        self.assertEqual(review_repair._policy()['test_fail_max'], 2)
        self._assert_refused('REPAIR_LIMIT_REACHED',
                             ci_failure=_ci(check_name='check (macOS 3.13)', check_run_id=9))
        self._assert_refused('REPAIR_LIMIT_REACHED', remote_test_receipt=_receipt())
        self.assertEqual(len(self._claims()), 2)
        self.assertEqual(len(self.calls), 2)

    def test_ci_claims_stay_out_of_the_review_family(self):
        self.dispatch(ci_failure=_ci(check_run_id=8))
        self._fail_last_claim()
        self.dispatch(ci_failure=_ci(check_run_id=9))
        self._fail_last_claim()
        self._assert_refused('REPAIR_LIMIT_REACHED', ci_failure=_ci(check_run_id=10))
        families = [claim['family'] for claim in self._claims()]
        self.assertEqual(families, [EC2_FAMILY, EC2_FAMILY])
        self.assertNotIn(f'pr-{PR}', families)

    def test_final_recheck_refusal_preserves_ci_claims_and_shared_count(self):
        # #1502 WP-D D11 repair 1: a refusal after the repair Task is recorded
        # commits no claim, so the shared EC2/CI test-failure count is unchanged.
        self.assertEqual(review_repair._policy()['test_fail_max'], 2)
        self.dispatch(remote_test_receipt=_receipt())
        self._fail_last_claim()
        before = self._claims()
        self.assertEqual([(claim['family'], claim['attempt']) for claim in before],
                         [(EC2_FAMILY, 1)])
        decisions = iter([type('D', (), {'allowed': True})(),
                          type('D', (), {'allowed': False, 'reason_code': 'PAUSED'})()])
        with patch.object(review_repair.worker_dispatch, 'evaluate',
                          side_effect=lambda *_args, **_kwargs: next(decisions)):
            with self.assertRaises(LLMToolError) as caught:
                self.dispatch(ci_failure=_ci(check_run_id=8))
        self.assertEqual(caught.exception.review_repair_code, 'PAUSED')
        self.assertRegex(caught.exception.review_repair_unclaimed_task_id, r'^[0-9a-f]{32}$')
        self.assertEqual(self._claims(), before)
        self.assertEqual(len(self.calls), 1)
        # A later authorization dispatches the same CI failure exactly once,
        # as the second and last test-failure repair.
        result = self.dispatch(ci_failure=_ci(check_run_id=8))
        # A fresh dispatch carries the provider result; only a reused claim is 'replayed'.
        self.assertNotIn('replayed', result)
        self.assertEqual(result['result'], {'status': 'DISPATCHED', 'head': HEAD})
        self.assertEqual((result['claim']['family'], result['claim']['attempt']),
                         (EC2_FAMILY, 2))
        self.assertNotEqual(result['repair_task_id'],
                            caught.exception.review_repair_unclaimed_task_id)
        self.assertEqual(len(self.calls), 2)
        replay = self.dispatch(ci_failure=_ci(check_run_id=8))
        self.assertTrue(replay['replayed'])
        self.assertEqual(len(self.calls), 2)
        self._fail_last_claim()
        self._assert_refused('REPAIR_LIMIT_REACHED', ci_failure=_ci(check_run_id=9))
        self._assert_refused('REPAIR_LIMIT_REACHED', remote_test_receipt=_receipt())
        self.assertEqual(len(self._claims()), 2)
        self.assertEqual(review_repair._policy()['test_fail_max'], 2)

    def test_replay_of_same_head_and_check_run_reuses_claim(self):
        first = self.dispatch(ci_failure=_ci())
        before = self._claims()
        replay = self.dispatch(ci_failure=_ci(excerpt='a different untrusted excerpt'))
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['repair_task_id'], first['repair_task_id'])
        self.assertEqual(replay['claim']['repair_task_id'], before[0]['repair_task_id'])
        self.assertEqual(self._claims(), before)
        self.assertEqual(len(self.calls), 1)


if __name__ == '__main__':
    unittest.main()
