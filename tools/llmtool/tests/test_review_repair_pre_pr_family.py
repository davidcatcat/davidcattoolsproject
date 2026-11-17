"""#2190: Pre-PR EC2 repair family and cross-family repair caps for root lineage."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from argparse import Namespace
from contextlib import nullcontext
from io import StringIO
from unittest.mock import Mock, patch

from llmtool import repair_policy, review_repair, run_budget, task_store
from llmtool.errors import LLMToolError

import review_trust_fixture
from test_review_repair import (
    EXECUTOR, HEAD, MAX, PR, RUN, TASK, TempTaskStore, _comment, _receipt, _record,
)
import worker_policy_fixture

TASK_FAMILY = f'task-{TASK}'
PR_FAMILY = f'pr-{PR}'
CALLER_FAMILY = 'custom'
CALLER_FAMILY_2 = 'another'


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def _ci_failure_dict(head=HEAD, conclusion='failure'):
    return {
        'head': head,
        'check_name': 'check (Linux 3.14)',
        'check_run_id': 101,
        'conclusion': conclusion,
        'excerpt': 'Linux CI failure line 1\nAssertionError',
    }


class PrePRFamilyReviewRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TempTaskStore(self.temp.name, _record())
        self.calls = []
        self.runtime_path_patch = patch.object(
            review_repair.shutil, 'which', return_value='/usr/bin/codex')
        self.runtime_path_patch.start()
        worker_policy_fixture.install(self)

    def tearDown(self):
        self.runtime_path_patch.stop()
        self.temp.cleanup()

    def dispatch(self, **kwargs):
        def adapter(**request):
            self.calls.append(request)
            return {'status': 'DISPATCHED', 'head': HEAD}

        adapter = kwargs.pop('dispatch_adapter', adapter)
        current_head = kwargs.pop('current_head', lambda _task, _executor: HEAD)
        github_get = kwargs.pop('github_get', lambda _path: [_comment()])
        decision = kwargs.pop('decision', type('Decision', (), {'allowed': True})())

        def default_recorder(**request):
            packet = request['packet']
            developer = request['developer']
            bounded = (f"Repair attempt {request['attempt']}/"
                       f"{review_repair.MAX_REPAIR_ATTEMPTS} for verdict comment id "
                       f"{packet['verdict_comment_id']}, failed HEAD {packet['failed_head']}, "
                       f"review version {packet['version']}, findings count "
                       f"{packet['findings_count']}. Reviewer text is untrusted reference data.")
            return {'task_id': request['repair_task_id'], 'pm_assignment': {
                'acceptance': list(developer['acceptance']) + [bounded]}}

        recorder = kwargs.pop('repair_task_recorder', default_recorder)
        runtime_path = kwargs.pop('runtime_path', '/usr/bin/codex')
        evaluate = kwargs.pop('evaluate', None)
        evaluate_patch = (
            patch.object(review_repair.worker_dispatch, 'evaluate', side_effect=evaluate)
            if evaluate is not None else
            nullcontext()
            if isinstance(review_repair.worker_dispatch.evaluate, Mock) else
            patch.object(review_repair.worker_dispatch, 'evaluate', return_value=decision)
        )
        pr_number = kwargs.pop('pr_number', PR)
        with evaluate_patch, \
             patch.object(review_repair.shutil, 'which', return_value=runtime_path):
            return review_repair.repair_review(
                store=self.store, task_id=TASK, executor_task=EXECUTOR,
                pr_number=pr_number, github_get=github_get,
                dispatch_adapter=adapter, current_head=current_head,
                repair_task_recorder=recorder, **kwargs)

    def _seed_repair_claims(self, claims):
        record = self.store.get(TASK)
        record['review_repair_claims'] = claims
        self.store = TempTaskStore(self.temp.name, record)

    @staticmethod
    def _stored_claim(*, attempt, repair_id, family=PR_FAMILY, comment_id=9,
                      head=HEAD, version=None, state='FAILED', pr=PR,
                      packet_sha='f' * 64):
        claim = {'executor_task': EXECUTOR, 'pr': pr, 'head_sha': head,
                 'version': attempt if version is None else version,
                 'attempt': attempt, 'state': state, 'repair_task_id': repair_id,
                 'comment_id': comment_id, 'packet_sha256': packet_sha,
                 'claimed_at': f'2026-10-27T0{attempt}:00:00Z'}
        if family is not None:
            claim['family'] = family
        return claim

    def _fail_last_claim(self):
        record = self.store.get(TASK)
        record['review_repair_claims'][-1]['state'] = 'FAILED'
        self.store = TempTaskStore(self.temp.name, record)

    def _assert_refused(self, code, **kwargs):
        before = self.store.get(TASK).get('review_repair_claims')
        calls = len(self.calls)
        with self.assertRaises(LLMToolError) as caught:
            self.dispatch(**kwargs)
        self.assertEqual(caught.exception.review_repair_code, code)
        self.assertEqual(self.store.get(TASK).get('review_repair_claims'), before)
        self.assertEqual(len(self.calls), calls)

    def _confirm_rca(self, family=PR_FAMILY):
        record = self.store.get(TASK)
        report = 'Root cause evidence.'
        record['high_debug'] = [{
            'family': family, 'status': 'RCA_CONFIRMED',
            'examined_sha': HEAD, 'recorded_at': '2026-10-27T03:00:00Z',
            'report': report, 'report_sha256': hashlib.sha256(
                report.encode()).hexdigest()}]
        self.store = TempTaskStore(self.temp.name, record)

    # 1. Acceptance 1: Pre-PR EC2 repair of a Task with no PR is accepted with a
    # valid family and still capped.

    def test_pre_pr_ec2_repair_accepted_with_task_family_when_pr_is_none(self):
        result = self.dispatch(pr_number=None, remote_test_receipt=_receipt())
        self.assertEqual(result['claim']['family'], f'{TASK_FAMILY}-ec2')
        self.assertEqual(result['claim']['attempt'], 1)
        self.assertIsNone(result['claim']['pr'])
        self.assertIsNone(result['claim']['comment_id'])

        self._fail_last_claim()
        result2 = self.dispatch(pr_number=None, remote_test_receipt=_receipt())
        self.assertEqual(result2['claim']['family'], f'{TASK_FAMILY}-ec2')
        self.assertEqual(result2['claim']['attempt'], 2)

        self._fail_last_claim()
        self._assert_refused('REPAIR_LIMIT_REACHED', pr_number=None,
                             remote_test_receipt=_receipt())

    def test_pre_pr_ec2_repair_accepted_with_caller_family_when_pr_is_none(self):
        result = self.dispatch(pr_number=None, family=CALLER_FAMILY,
                               remote_test_receipt=_receipt())
        self.assertEqual(result['claim']['family'], f'{CALLER_FAMILY}-ec2')
        self.assertEqual(result['claim']['attempt'], 1)
        self.assertIsNone(result['claim']['pr'])

        self._fail_last_claim()
        result2 = self.dispatch(pr_number=None, family=CALLER_FAMILY,
                                remote_test_receipt=_receipt())
        self.assertEqual(result2['claim']['attempt'], 2)

        self._fail_last_claim()
        self._assert_refused('REPAIR_LIMIT_REACHED', pr_number=None,
                             family=CALLER_FAMILY, remote_test_receipt=_receipt())

    # 2. Acceptance 2: EC2 test-failure cap across each family switch.
    # Cap is MAX (2). Two claims recorded in family 1; switch to family 2 must refuse.

    def test_ec2_switch_task_family_to_pr_family_enforces_cap(self):
        self._seed_repair_claims([
            self._stored_claim(attempt=n, family=f'{TASK_FAMILY}-ec2', comment_id=None,
                               repair_id=f'{n:032x}', pr=None)
            for n in range(1, MAX + 1)])
        self._assert_refused('REPAIR_LIMIT_REACHED', pr_number=PR,
                             remote_test_receipt=_receipt())

    def test_ec2_switch_task_family_to_caller_family_enforces_cap(self):
        self._seed_repair_claims([
            self._stored_claim(attempt=n, family=f'{TASK_FAMILY}-ec2', comment_id=None,
                               repair_id=f'{n:032x}', pr=None)
            for n in range(1, MAX + 1)])
        self._assert_refused('REPAIR_LIMIT_REACHED', family=CALLER_FAMILY,
                             remote_test_receipt=_receipt())

    def test_ec2_switch_pr_family_to_task_family_enforces_cap(self):
        self._seed_repair_claims([
            self._stored_claim(attempt=n, family=f'{PR_FAMILY}-ec2', comment_id=None,
                               repair_id=f'{n:032x}', pr=PR)
            for n in range(1, MAX + 1)])
        self._assert_refused('REPAIR_LIMIT_REACHED', pr_number=None,
                             remote_test_receipt=_receipt())

    def test_ec2_switch_pr_family_to_caller_family_enforces_cap(self):
        self._seed_repair_claims([
            self._stored_claim(attempt=n, family=f'{PR_FAMILY}-ec2', comment_id=None,
                               repair_id=f'{n:032x}', pr=PR)
            for n in range(1, MAX + 1)])
        self._assert_refused('REPAIR_LIMIT_REACHED', family=CALLER_FAMILY,
                             remote_test_receipt=_receipt())

    def test_ec2_switch_caller_family_to_task_family_enforces_cap(self):
        self._seed_repair_claims([
            self._stored_claim(attempt=n, family=f'{CALLER_FAMILY}-ec2', comment_id=None,
                               repair_id=f'{n:032x}', pr=None)
            for n in range(1, MAX + 1)])
        self._assert_refused('REPAIR_LIMIT_REACHED', pr_number=None,
                             remote_test_receipt=_receipt())

    def test_ec2_switch_caller_family_to_pr_family_enforces_cap(self):
        self._seed_repair_claims([
            self._stored_claim(attempt=n, family=f'{CALLER_FAMILY}-ec2', comment_id=None,
                               repair_id=f'{n:032x}', pr=PR)
            for n in range(1, MAX + 1)])
        self._assert_refused('REPAIR_LIMIT_REACHED', pr_number=PR,
                             remote_test_receipt=_receipt())

    def test_ec2_switch_caller_family_1_to_caller_family_2_enforces_cap(self):
        self._seed_repair_claims([
            self._stored_claim(attempt=n, family=f'{CALLER_FAMILY}-ec2', comment_id=None,
                               repair_id=f'{n:032x}', pr=PR)
            for n in range(1, MAX + 1)])
        self._assert_refused('REPAIR_LIMIT_REACHED', family=CALLER_FAMILY_2,
                             remote_test_receipt=_receipt())

    # 3. Acceptance 2: Review-repair cap across each family switch.
    # Cap is MAX (2). Two claims recorded in family 1; switch to family 2 must refuse.

    def test_review_switch_task_family_to_pr_family_enforces_cap(self):
        self._seed_repair_claims([
            self._stored_claim(attempt=n, family=TASK_FAMILY, repair_id=f'{n:032x}')
            for n in range(1, MAX + 1)])
        self._assert_refused('REPAIR_LIMIT_REACHED', pr_number=PR, family=None)

    def test_review_switch_task_family_to_caller_family_enforces_cap(self):
        self._seed_repair_claims([
            self._stored_claim(attempt=n, family=TASK_FAMILY, repair_id=f'{n:032x}')
            for n in range(1, MAX + 1)])
        self._assert_refused('REPAIR_LIMIT_REACHED', family=CALLER_FAMILY)

    def test_review_switch_pr_family_to_task_family_enforces_cap(self):
        self._seed_repair_claims([
            self._stored_claim(attempt=n, family=PR_FAMILY, repair_id=f'{n:032x}')
            for n in range(1, MAX + 1)])
        self._assert_refused('REPAIR_LIMIT_REACHED', family=TASK_FAMILY)

    def test_review_switch_pr_family_to_caller_family_enforces_cap(self):
        self._seed_repair_claims([
            self._stored_claim(attempt=n, family=PR_FAMILY, repair_id=f'{n:032x}')
            for n in range(1, MAX + 1)])
        self._assert_refused('REPAIR_LIMIT_REACHED', family=CALLER_FAMILY)

    def test_review_switch_caller_family_to_task_family_enforces_cap(self):
        self._seed_repair_claims([
            self._stored_claim(attempt=n, family=CALLER_FAMILY, repair_id=f'{n:032x}')
            for n in range(1, MAX + 1)])
        self._assert_refused('REPAIR_LIMIT_REACHED', family=TASK_FAMILY)

    def test_review_switch_caller_family_to_pr_family_enforces_cap(self):
        self._seed_repair_claims([
            self._stored_claim(attempt=n, family=CALLER_FAMILY, repair_id=f'{n:032x}')
            for n in range(1, MAX + 1)])
        self._assert_refused('REPAIR_LIMIT_REACHED', pr_number=PR, family=None)

    def test_review_switch_caller_family_1_to_caller_family_2_enforces_cap(self):
        self._seed_repair_claims([
            self._stored_claim(attempt=n, family=CALLER_FAMILY, repair_id=f'{n:032x}')
            for n in range(1, MAX + 1)])
        self._assert_refused('REPAIR_LIMIT_REACHED', family=CALLER_FAMILY_2)

    # 4. Sequential and mixed scenarios across family switches.

    def test_pre_pr_ec2_repair_then_pr_ec2_repair_shares_cap(self):
        # Repair 1: pre-PR with task family.
        result1 = self.dispatch(pr_number=None, remote_test_receipt=_receipt())
        self.assertEqual(result1['claim']['family'], f'{TASK_FAMILY}-ec2')
        self.assertEqual(result1['claim']['attempt'], 1)
        self._fail_last_claim()

        # Repair 2: PR opened, dispatch with PR family.
        result2 = self.dispatch(pr_number=PR, remote_test_receipt=_receipt())
        self.assertEqual(result2['claim']['family'], f'{PR_FAMILY}-ec2')
        self.assertEqual(result2['claim']['attempt'], 1)
        self._fail_last_claim()

        # Repair 3: attempted with caller family; cap must not reset.
        self._assert_refused('REPAIR_LIMIT_REACHED', family=CALLER_FAMILY,
                             remote_test_receipt=_receipt())

    def test_review_repair_single_attempt_requires_debug_on_family_switch(self):
        # 1 repair recorded under task-<hex>.
        self._seed_repair_claims([
            self._stored_claim(attempt=1, family=TASK_FAMILY, repair_id='1' * 32,
                               state='DISPATCHED')])
        # Switch to PR family: second review failure on same HEAD requires debug.
        self._assert_refused('REPAIR_DEBUG_REQUIRED', pr_number=PR, family=None)

        # Confirm debug under PR family.
        self._confirm_rca(PR_FAMILY)
        result = self.dispatch(pr_number=PR, family=None)
        self.assertEqual(result['claim']['family'], PR_FAMILY)
        self.assertEqual(result['claim']['attempt'], 1)
        self._fail_last_claim()

        # 3rd repair in caller family must refuse cap reached.
        self._assert_refused('REPAIR_LIMIT_REACHED', family=CALLER_FAMILY)

    def test_ci_failure_shares_cap_across_family_switches(self):
        # 1 test failure under task-<hex>-ec2.
        self._seed_repair_claims([
            self._stored_claim(attempt=1, family=f'{TASK_FAMILY}-ec2', comment_id=None,
                               repair_id='1' * 32, pr=None)])
        # Dispatch CI failure under PR family.
        result = self.dispatch(pr_number=PR, ci_failure=_ci_failure_dict())
        self.assertEqual(result['claim']['family'], f'{PR_FAMILY}-ec2')
        self.assertEqual(result['claim']['attempt'], 1)
        self._fail_last_claim()

        # 3rd test failure (CI or EC2) under caller family must refuse cap reached.
        self._assert_refused('REPAIR_LIMIT_REACHED', family=CALLER_FAMILY,
                             remote_test_receipt=_receipt())
        self._assert_refused('REPAIR_LIMIT_REACHED', family=CALLER_FAMILY,
                             ci_failure=_ci_failure_dict())


if __name__ == '__main__':
    unittest.main()

