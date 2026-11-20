"""#2190 pre-PR EC2 test failure recovery and repair family tests.

Covers all rows (a)-(g) of the Case table:
(a) pre-PR EC2 failure, no PR -> REPAIR_REVIEW routes one counted test-failure repair in the pre-PR family;
(b) that Task later gets a PR -> its pre-PR repair count is kept in the 5-repair total;
(c) PR exists -> pr-<N> family unchanged;
(d) EC2 repair claim on a PR -> stored in the EC2 family as on main;
(e) over-long or malformed family -> refused before any write (existing test_invalid_repair_families_are_refused_before_any_write keeps its expectations);
(f) 5 effective repairs across the chain -> next refused;
(g) the pre-PR repair packet is persisted through the real TaskStore start-failure shape (integer PR not required for pre-PR).

TEST DATA RULE (Owner #763 A): All test fixtures use plain or CJK prose only.
No credential-shaped strings (passwords, tokens, API keys).
"""

import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from llmtool import (lineage_counts, repair_policy, review_repair, run_budget,
                      start_failure, task_advance, task_store, task_submission_cli,
                      worker_dispatch)
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore, new_id

import review_trust_fixture
import worker_policy_fixture

TASK = 'a' * 32
RUN = 'b' * 32
HEAD = 'c' * 40
CONTRACT = 'd' * 64
EXECUTOR = 'executor-d6'
PR = 42
PREPR_BASE_FAMILY = 'prepr'
PREPR_EC2_FAMILY = 'prepr-ec2'
PR_FAMILY = f'pr-{PR}'
PR_EC2_FAMILY = f'pr-{PR}-ec2'


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def _comment(status='FAIL', *, comment_id=9, actor='Claude', head=HEAD, version=1):
    marker = 'FIX' if status == 'FAIL' else status
    body = (f'<!-- activity: type=review_verdict actor={actor} target=PM status={marker} -->\n'
            f'Verdict: {status} | Version: v{version} | HEAD: {head}\n'
            'Finding: address the reported issue\n')
    return {'id': comment_id, 'body': body, 'created_at': '2026-10-27T01:00:00Z',
            'updated_at': '2026-10-27T01:00:00Z'}


def _receipt(head=HEAD, task_id=TASK):
    receipt = {
        'task_id': task_id, 'run_id': RUN, 'executor_task': EXECUTOR,
        'source_sha': head, 'result': 'FAIL', 'modules': ['test_remote'],
        'failure_excerpt': 'FAIL: test_remote\nAssertionError: expected value',
    }
    receipt['receipt_digest'] = hashlib.sha256(
        task_store._json_bytes(receipt)).hexdigest()
    return receipt


def _base_record(task_id=TASK):
    record = {
        'schema_version': 2,
        'task_id': task_id,
        'run_id': RUN,
        'task_binding': {'task_id': task_id, 'contract_digest': CONTRACT},
        'pm_provenance': {'pm_native_session_id': 'pm-session-1',
                          'pm_cwd': '/workspace/task'},
        'pm_assignment': {'goal': 'Fix prepr repair family handling.',
                          'acceptance': ['Pre-PR EC2 failure routes repair in prepr-ec2 family.']},
        'developer': {
            'state': 'DEVELOPER_RESPONDED',
            'provider_profile': 'agy',
            'owner_assignment_ref': 'https://github.com/example/repo/issues/1#issuecomment-2',
            'read_paths': ['tools/llmtool'],
            'write_paths': ['tools/llmtool/llmtool/review_repair.py'],
            'acceptance': ['Pre-PR EC2 failure routes repair in prepr-ec2 family.'],
        },
        'completion': {
            'state': 'COMPLETED',
            'executor_task_id': EXECUTOR,
            'evidence': {'common_dir': '/fake/common', 'result_head': HEAD},
        },
    }
    record['record_sha256'] = hashlib.sha256(
        json.dumps(record, sort_keys=True).encode()).hexdigest()
    return record


class TempTaskStore:
    """Persistent TaskStore fake preserving compare-and-update semantics."""

    def __init__(self, root, record):
        self.root = Path(root)
        self.records = {}
        records = record if isinstance(record, (list, tuple)) else [record]
        for item in records:
            if isinstance(item, dict) and 'task_id' in item:
                self.records[item['task_id']] = dict(item)
                (self.root / f"{item['task_id']}.json").write_text(json.dumps(item))

    def task_ids(self):
        return sorted(self.records.keys())

    def get(self, task_id):
        if task_id not in self.records:
            raise KeyError(task_id)
        return json.loads(json.dumps(self.records[task_id]))

    def compare_and_update(self, task_id, *, expected, **changes):
        record = self.get(task_id)
        if any(record.get(key) != value for key, value in expected.items()):
            raise LLMToolError('CAS conflict')
        record.update(changes)
        record['record_sha256'] = hashlib.sha256(
            json.dumps(record, sort_keys=True).encode()).hexdigest()
        self.records[task_id] = record
        (self.root / f"{task_id}.json").write_text(json.dumps(record))
        return record

    def update(self, task_id, **changes):
        record = self.get(task_id)
        record.update(changes)
        record['record_sha256'] = hashlib.sha256(
            json.dumps(record, sort_keys=True).encode()).hexdigest()
        self.records[task_id] = record
        (self.root / f"{task_id}.json").write_text(json.dumps(record))
        return record


class PreprRepairFamilyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TempTaskStore(self.temp.name, _base_record())
        self.calls = []
        self.runtime_path_patch = patch.object(
            review_repair.shutil, 'which', return_value='/usr/bin/agy')
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
                       f"{review_repair.MAX_REPAIR_ATTEMPTS} for remote-test failure "
                       f"at HEAD {packet['failed_head']}. Failure excerpt is untrusted reference data.")
            return {'task_id': request['repair_task_id'], 'pm_assignment': {
                'acceptance': list(developer['acceptance']) + [bounded]}}
        recorder = kwargs.pop('repair_task_recorder', default_recorder)
        pr_number = kwargs.pop('pr_number', None)
        task_id = kwargs.pop('task_id', TASK)

        evaluate_patch = patch.object(
            review_repair.worker_dispatch, 'evaluate', return_value=decision)

        with evaluate_patch:
            return review_repair.repair_review(
                store=self.store, task_id=task_id, executor_task=EXECUTOR,
                pr_number=pr_number, github_get=github_get,
                dispatch_adapter=adapter, current_head=current_head,
                repair_task_recorder=recorder, **kwargs)

    # (a) pre-PR EC2 failure, no PR -> REPAIR_REVIEW routes one counted test-failure repair in the pre-PR family
    def test_case_a_prepr_ec2_failure_routes_repair_in_prepr_family(self):
        receipt = _receipt()
        result = self.dispatch(pr_number=None, remote_test_receipt=receipt)
        claim = result['claim']
        self.assertEqual(claim['family'], PREPR_EC2_FAMILY)
        self.assertIsNone(claim['pr'])
        self.assertEqual(claim['attempt'], 1)
        self.assertEqual(claim['version'], 1)
        self.assertEqual(claim['state'], 'DISPATCHED')
        self.assertEqual(len(self.calls), 1)

        # Stored in review_repair_claims on the Task
        stored = self.store.get(TASK)['review_repair_claims']
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]['family'], PREPR_EC2_FAMILY)
        self.assertIsNone(stored[0]['pr'])

        # Counted as one test failure in lineage counts
        counts = lineage_counts.lineage_counts(
            self.store, self.store.get(TASK), base_family=PREPR_BASE_FAMILY)
        self.assertEqual(counts['test_fails'], 1)
        self.assertEqual(counts['review_repairs'], 0)

        # Verified through task_advance._test_fail_count with number=None
        self.assertEqual(task_advance._test_fail_count(
            self.store.get(TASK), None, self.store), 1)

    # (b) that Task later gets a PR -> its pre-PR repair count is kept in the 5-repair total
    def test_case_b_prepr_repair_count_kept_when_task_later_gets_pr(self):
        receipt = _receipt()
        self.dispatch(pr_number=None, remote_test_receipt=receipt)

        # Now the Task is evaluated in the context of PR 42
        record = self.store.get(TASK)
        counts_on_pr = lineage_counts.lineage_counts(
            self.store, record, base_family=PR_FAMILY)
        self.assertEqual(counts_on_pr['test_fails'], 1)

        # Checked through task_advance._test_fail_count with number=42
        self.assertEqual(task_advance._test_fail_count(record, PR, self.store), 1)

    # (c) PR exists -> pr-<N> family unchanged
    def test_case_c_pr_exists_pr_family_unchanged(self):
        # Review repair on PR
        result = self.dispatch(pr_number=PR)
        claim = result['claim']
        self.assertEqual(claim['family'], PR_FAMILY)
        self.assertEqual(claim['pr'], PR)

        # Explicit family='prepr' when PR exists is refused
        with self.assertRaises(LLMToolError) as caught:
            self.dispatch(pr_number=PR, family='prepr')
        self.assertEqual(caught.exception.review_repair_code, 'REPAIR_FAMILY_INVALID')

    # (d) EC2 repair claim on a PR -> stored in the EC2 family as on main
    def test_case_d_ec2_repair_claim_on_pr_stored_in_ec2_family(self):
        receipt = _receipt()
        result = self.dispatch(pr_number=PR, remote_test_receipt=receipt)
        claim = result['claim']
        self.assertEqual(claim['family'], PR_EC2_FAMILY)
        self.assertEqual(claim['pr'], PR)

    # (e) over-long or malformed family -> refused before any write
    def test_case_e_overlong_or_malformed_family_refused_before_any_write(self):
        receipt = _receipt()
        # Review repair requires PR (cannot review without PR)
        with self.assertRaises(LLMToolError) as caught:
            self.dispatch(pr_number=None)
        self.assertEqual(caught.exception.review_repair_code, 'REPAIR_FAMILY_INVALID')
        self.assertNotIn('review_repair_claims', self.store.get(TASK))

        # Non-prepr base family when pr_number is None is refused
        with self.assertRaises(LLMToolError) as caught:
            self.dispatch(pr_number=None, family='network', remote_test_receipt=receipt)
        self.assertEqual(caught.exception.review_repair_code, 'REPAIR_FAMILY_INVALID')
        self.assertNotIn('review_repair_claims', self.store.get(TASK))

        # Over-long family (>64 chars) is refused
        with self.assertRaises(LLMToolError) as caught:
            self.dispatch(pr_number=PR, family='x' * 61, remote_test_receipt=receipt)
        self.assertEqual(caught.exception.review_repair_code, 'REPAIR_FAMILY_INVALID')
        self.assertNotIn('review_repair_claims', self.store.get(TASK))

        # Suffix -ec2 in base family is refused
        with self.assertRaises(LLMToolError) as caught:
            self.dispatch(pr_number=PR, family='network-ec2', remote_test_receipt=receipt)
        self.assertEqual(caught.exception.review_repair_code, 'REPAIR_FAMILY_INVALID')
        self.assertNotIn('review_repair_claims', self.store.get(TASK))

    # (f) 5 effective repairs across the chain -> next refused
    def test_case_f_five_effective_repairs_across_chain_next_refused(self):
        # 1 pre-PR repair on the root Task
        self.dispatch(pr_number=None, remote_test_receipt=_receipt())
        # Mark claim FAILED so a next repair attempt is allowed
        self.store.records[TASK]['review_repair_claims'][-1]['state'] = 'FAILED'

        # 4 more repairs on PR 42 (reaching total 5)
        for i in range(2, 6):
            receipt = _receipt(head=HEAD)
            res = self.dispatch(pr_number=PR, remote_test_receipt=receipt)
            self.assertEqual(res['claim']['attempt'], i - 1)  # attempts in pr-42-ec2
            self.store.records[TASK]['review_repair_claims'][-1]['state'] = 'FAILED'

        # Verify lineage_counts sees total 5 test_fails
        counts = lineage_counts.lineage_counts(
            self.store, self.store.get(TASK), base_family=PR_FAMILY)
        self.assertEqual(counts['test_fails'], 5)

        # Next repair is refused REPAIR_LIMIT_REACHED
        self.calls.clear()
        with self.assertRaises(LLMToolError) as caught:
            self.dispatch(pr_number=PR, remote_test_receipt=_receipt())
        self.assertEqual(caught.exception.review_repair_code, 'REPAIR_LIMIT_REACHED')
        self.assertEqual(len(self.calls), 0)

    # (g) the pre-PR repair packet is persisted through real TaskStore start-failure shape
    def test_case_g_prepr_repair_packet_persisted_through_real_task_store(self):
        packet = {
            'task': TASK, 'pr': None, 'executor_task': EXECUTOR,
            'failed_head': HEAD, 'version': 0, 'verdict_comment_id': 0,
            'findings_count': 0,
            'reviewer_text_excerpt_untrusted_data': 'Pre-PR remote test execution failure.',
        }
        # Predicate accepts pr=None
        self.assertTrue(start_failure.repair_packet_shape_ok(packet))

        # Encodes canonical ASCII text
        text = start_failure.canonical(packet)
        self.assertTrue(text.isascii())
        self.assertIn('"pr":null', text)

        # Decodes back to equal dict
        decoded = start_failure.decode_repair_packet(text)
        self.assertEqual(decoded, packet)
        self.assertIsNone(decoded['pr'])

        # Real TaskStore validates repair_packet text on write
        real_temp = tempfile.TemporaryDirectory()
        try:
            store = TaskStore(real_temp.name)
            record = _base_record()
            record['repair_packet'] = text
            store.create(record)
            fetched = store.get(TASK)
            self.assertEqual(fetched['repair_packet'], text)
            self.assertEqual(start_failure.decode_repair_packet(fetched['repair_packet']), packet)

            # Matches pre-PR test claim
            claim = {
                'executor_task': EXECUTOR, 'pr': None, 'head_sha': HEAD,
                'version': 0, 'attempt': 1, 'comment_id': None,
                'family': PREPR_EC2_FAMILY,
                'packet_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
            }
            root = {'task_id': TASK}
            self.assertTrue(start_failure.repair_packet_matches(packet, claim, root))
        finally:
            real_temp.cleanup()


if __name__ == '__main__':
    unittest.main()
