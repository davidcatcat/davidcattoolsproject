import fcntl
import hashlib
import inspect
import io
import json
import os
import errno
import select
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool.action_contract import TaskBinding
from llmtool.errors import LLMToolError
from llmtool import worker_dispatch
from llmtool import task_submission_cli as task_submission_cli_module
from llmtool import codex_direct_developer
from llmtool.task_store import TaskStore, TaskStoreConflict, new_id
from llmtool import task_store as task_store_module
from llmtool.task_submission_cli import _record, _task_view
from llmtool.task_verification import AcceptanceCriterion, CriterionKind, TaskContract


def record(request='Implement a small tested feature'):
    task_id, run_id = new_id(), new_id()
    request_sha = hashlib.sha256(request.encode()).hexdigest()
    contract = TaskContract(
        schema_version=1, task_id=task_id, contract_version=1,
        previous_digest=None, goal='PM intake request sha256:' + request_sha,
        authorized_scope=('pm-planning',), forbidden_scope=(),
        criteria=(AcceptanceCriterion('pm_response', CriterionKind.ARTIFACT,
                                      'Record the PM reply or unavailable status.'),),
    )
    return {
        'schema_version': 1, 'task_id': task_id, 'run_id': run_id,
        'pm_session_id': 'pm-' + new_id(), 'pm_provider_profile': 'pm',
        'pm_provider_type': 'codex-cli', 'pm_model': 'gpt-6-luna',
        'backend_session_semantics': 'EPHEMERAL_SINGLE_CALL', 'native_session_id': None,
        'state': 'PM_PENDING', 'created_at': '2026-10-21T00:00:00Z',
        'updated_at': '2026-10-21T00:00:00Z', 'task_contract': contract.to_dict(),
        'task_binding': contract.binding(run_id).to_dict(), 'request': request,
        'request_sha256': request_sha, 'response': None, 'usage': None,
        'error_code': None, 'record_sha256': None,
    }


def remote_test_receipt(task, pm_session=None, **overrides):
    """One valid PASS remote-test receipt for ``task`` with its receipt_digest.

    ``pm_session`` defaults to the Task's current PM session.
    """
    receipt = {
        'result': 'PASS', 'exit_code': 0, 'source_sha': 'a' * 40,
        'tree': 'b' * 40, 'fetch_head': 'a' * 40,
        'modules': ['test_task_store'], 'identity': {'uid': 65534},
        'started_at': '2026-10-27T00:00:00Z', 'ended_at': '2026-10-27T00:00:01Z',
        'elapsed_s': 1.0, 'ran_total': 1, 'final': ['OK'], 'failures': [],
        'log_bytes': 0, 'log_sha256': 'c' * 64, 'remote_log_path': None,
        'task_id': task['task_id'], 'run_id': task['run_id'],
        'executor_task': 'd6-task',
        'pm_session': task['pm_session_id'] if pm_session is None else pm_session,
        'command_digest': 'd' * 64,
    }
    receipt.update(overrides)
    receipt['receipt_digest'] = hashlib.sha256(json.dumps(
        receipt, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False).encode() + b'\n').hexdigest()
    return receipt


import review_trust_fixture
import worker_policy_fixture


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'tasks'
        self.store = TaskStore(self.root)
        # #1692: the shared all-ALLOWED test policy, never the live file.
        self.policy_path = worker_policy_fixture.install(self)

    def test_merge_attempt_unknown_can_be_reclaimed_for_same_pr_and_head(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        claimed = {'pr': 17, 'head_sha': 'a' * 40, 'state': 'CLAIMED',
                   'claimed_at': '2026-11-06T00:00:00Z'}
        current = self.store.get(task['task_id'])
        self.store.compare_and_update(task['task_id'],
            expected={'record_sha256': current['record_sha256']}, merge_attempt=claimed)
        unknown = dict(claimed, state='UNKNOWN')
        current = self.store.get(task['task_id'])
        self.store.compare_and_update(task['task_id'],
            expected={'record_sha256': current['record_sha256']}, merge_attempt=unknown)
        retry = dict(claimed, claimed_at='2026-11-06T00:00:00.000001Z')
        current = self.store.get(task['task_id'])
        saved = self.store.compare_and_update(task['task_id'],
            expected={'record_sha256': current['record_sha256'], 'merge_attempt': unknown},
            merge_attempt=retry)
        self.assertEqual(saved['merge_attempt'], retry)

    def test_merge_attempt_transition_refusals_remain_strict(self):
        cases = (
            ('MERGED to CLAIMED', 'MERGED', 17, 'a' * 40,
             {'pr': 17, 'head_sha': 'a' * 40, 'state': 'CLAIMED',
              'claimed_at': '2026-11-06T00:00:01Z'}),
            ('CLAIMED to CLAIMED', 'CLAIMED', 17, 'a' * 40,
             {'pr': 17, 'head_sha': 'a' * 40, 'state': 'CLAIMED',
              'claimed_at': '2026-11-06T00:00:01Z'}),
            ('UNKNOWN to another PR', 'UNKNOWN', 17, 'a' * 40,
             {'pr': 18, 'head_sha': 'a' * 40, 'state': 'CLAIMED',
              'claimed_at': '2026-11-06T00:00:01Z'}),
            ('UNKNOWN to another head', 'UNKNOWN', 17, 'a' * 40,
             {'pr': 17, 'head_sha': 'b' * 40, 'state': 'CLAIMED',
              'claimed_at': '2026-11-06T00:00:01Z'}),
            ('UNKNOWN without a new claim time', 'UNKNOWN', 17, 'a' * 40,
             {'pr': 17, 'head_sha': 'a' * 40, 'state': 'CLAIMED',
              'claimed_at': '2026-11-06T00:00:00Z'}),
        )
        for label, old_state, pr, head, new_attempt in cases:
            with self.subTest(label=label):
                task = self._codex_dispatch_task()
                self.store.create(task)
                old = {'pr': pr, 'head_sha': head, 'state': 'CLAIMED',
                       'claimed_at': '2026-11-06T00:00:00Z'}
                current = self.store.get(task['task_id'])
                self.store.compare_and_update(task['task_id'],
                    expected={'record_sha256': current['record_sha256']}, merge_attempt=old)
                if old_state != 'CLAIMED':
                    old = dict(old, state=old_state)
                    current = self.store.get(task['task_id'])
                    self.store.compare_and_update(task['task_id'],
                        expected={'record_sha256': current['record_sha256']}, merge_attempt=old)
                current = self.store.get(task['task_id'])
                with self.assertRaises(LLMToolError) as caught:
                    self.store.compare_and_update(task['task_id'],
                        expected={'record_sha256': current['record_sha256']},
                        merge_attempt=new_attempt)
                self.assertEqual(caught.exception.task_store_error_code,
                                 'TASK_MERGE_ATTEMPT_TRANSITION_INVALID')

    def test_claimed_merge_attempt_still_allows_unknown_and_merged(self):
        for next_state in ('UNKNOWN', 'MERGED'):
            with self.subTest(next_state=next_state):
                task = self._codex_dispatch_task()
                self.store.create(task)
                claim = {'pr': 17, 'head_sha': 'a' * 40, 'state': 'CLAIMED',
                         'claimed_at': '2026-11-06T00:00:00Z'}
                current = self.store.get(task['task_id'])
                self.store.compare_and_update(task['task_id'],
                    expected={'record_sha256': current['record_sha256']}, merge_attempt=claim)
                updated = dict(claim, state=next_state)
                current = self.store.get(task['task_id'])
                saved = self.store.compare_and_update(task['task_id'],
                    expected={'record_sha256': current['record_sha256']},
                    merge_attempt=updated)
                self.assertEqual(saved['merge_attempt'], updated)

    def test_high_debug_entries_are_append_only(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        entry = {
            'family': 'test-family', 'failed_corrections': 2, 'early_reason': None,
            'examined_sha': 'a' * 40, 'd6': 'executor-hd1-aaaaaaa',
            'model_requested': 'gpt-6.1-sol', 'model_observed': 'UNKNOWN',
            'effort_observed': 'UNKNOWN', 'status': 'REPORT_INVALID',
            'report_sha256': hashlib.sha256(b'').hexdigest(), 'report': '', 'usage': 'UNKNOWN',
            'recorded_at': '2026-10-27T00:00:00Z',
        }
        saved = self.store.append_high_debug(task['task_id'], entry)
        changed = dict(entry, status='RCA_CONFIRMED')
        with self.assertRaises(LLMToolError):
            self.store.update(task['task_id'], high_debug=[changed])
        self.assertEqual(self.store.get(task['task_id'])['high_debug'], [entry])

    def test_old_repair_cap_override_stays_readable_without_a_writer(self):
        # #1502 WP-D D7: the High Debug cap override writer is gone; an old
        # record that holds the field keeps its valid shape.
        self.assertFalse(hasattr(type(self.store), 'record_repair_cap_override'))
        task = self._codex_dispatch_task()
        self.store.create(task)
        override = {
            'ref': 'https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-123',
            'rounds': 3, 'recorded_at': '2026-11-04T00:00:00Z'}
        self.store.update(task['task_id'], repair_cap_override=override)
        self.assertEqual(self.store.get(task['task_id'])['repair_cap_override'], override)

    def test_repair_cap_override_rejects_bad_shapes(self):
        cases = (
            {'ref': 'http://github.com/owner/repo/issues/1/comments/2',
             'rounds': 3, 'recorded_at': '2026-11-04T00:00:00Z'},
            {'ref': 'https://github.com/owner/repo/issues/1',
             'rounds': 3, 'recorded_at': '2026-11-04T00:00:00Z'},
            {'ref': 'https://github.com/owner/repo/issues/1/comments/2',
             'rounds': 2, 'recorded_at': '2026-11-04T00:00:00Z'},
            {'ref': 'https://github.com/owner/repo/issues/1/comments/2',
             'rounds': 3, 'recorded_at': 'invalid'},
            {'ref': 'https://github.com/owner/repo/issues/1/comments/2',
             'rounds': True, 'recorded_at': '2026-11-04T00:00:00Z'},
        )
        for override in cases:
            with self.subTest(override=override):
                task = self._codex_dispatch_task()
                self.store.create(task)
                with self.assertRaises(LLMToolError) as caught:
                    self.store.update(task['task_id'], repair_cap_override=override)
                self.assertEqual(caught.exception.task_store_error_code,
                                 'TASK_REPAIR_CAP_OVERRIDE_INVALID')

    def test_repair_cap_override_is_optional_for_existing_records(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        self.assertNotIn('repair_cap_override', self.store.get(task['task_id']))

    def test_run_failed_high_debug_entries_from_high_debug_are_valid(self):
        from llmtool import high_debug
        task = self._codex_dispatch_task()
        self.store.create(task)
        reasons = ({'failed_corrections': 2, 'early_reason': None},
                   {'failed_corrections': None, 'early_reason': 'early'})
        codes = ('HIGH_DEBUG_RUN_FAILED', 'HIGH_DEBUG_WROTE_FILES',
                 'HIGH_DEBUG_REPORT_INVALID')
        expected = []
        for index, (reason, code) in enumerate(
                [(reason, code) for reason in reasons for code in codes], 1):
            entry = high_debug._failed_entry(
                family='test-family', head='a' * 40, d6=f'executor-hd{index}-aaaaaaa',
                code=code, observed='UNKNOWN', effort='UNKNOWN',
                usage={'input': 1, 'cached': 0, 'output': 1, 'reasoning': 0},
                recorded_at='2026-10-27T00:00:00Z', **reason)
            self.store.append_high_debug(task['task_id'], entry)
            expected.append(entry)
        self.assertEqual(self.store.get(task['task_id'])['high_debug'], expected)
        with self.assertRaises(LLMToolError):
            self.store.append_high_debug(task['task_id'], dict(expected[0], status='FAILED'))

    def test_review_repair_claim_can_fail_only_after_dispatch(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        claim = {
            'executor_task': 'executor-1', 'pr': 17, 'head_sha': 'a' * 40,
            'version': 1, 'attempt': 1, 'family': 'pr-17', 'state': 'CLAIMED',
            'repair_task_id': new_id(), 'comment_id': 9, 'packet_sha256': 'b' * 64,
            'claimed_at': '2026-10-27T00:00:00Z',
        }
        saved = self.store.get(task['task_id'])
        self.store.compare_and_update(task['task_id'],
            expected={'record_sha256': saved['record_sha256']},
            review_repair_claims=[claim])
        claimed_record = self.store.get(task['task_id'])
        with self.assertRaises(LLMToolError) as caught:
            self.store.compare_and_update(task['task_id'],
                expected={'record_sha256': claimed_record['record_sha256']},
                review_repair_claims=[dict(claim, state='FAILED')])
        self.assertEqual(caught.exception.task_store_error_code,
                         'REVIEW_REPAIR_CLAIM_TRANSITION_INVALID')
        self.assertEqual(self.store.get(task['task_id']), claimed_record)
        dispatched = dict(claim, state='DISPATCHED')
        self.store.compare_and_update(task['task_id'],
            expected={'record_sha256': claimed_record['record_sha256']},
            review_repair_claims=[dispatched])
        failed = dict(dispatched, state='FAILED')
        dispatched_record = self.store.get(task['task_id'])
        saved = self.store.compare_and_update(task['task_id'],
            expected={'record_sha256': dispatched_record['record_sha256']},
            review_repair_claims=[failed])
        self.assertEqual(saved['review_repair_claims'], [failed])
        with self.assertRaises(LLMToolError) as caught:
            self.store.compare_and_update(task['task_id'],
                expected={'record_sha256': saved['record_sha256']},
                review_repair_claims=[dict(failed, state='DISPATCHED')])
        self.assertEqual(caught.exception.task_store_error_code,
                         'REVIEW_REPAIR_CLAIM_TRANSITION_INVALID')
        self.assertEqual(self.store.get(task['task_id']), saved)

    def test_second_review_repair_claim_binds_a_confirmed_debug_on_its_head(self):
        # #1502 WP-D (D1): repair #2 may carry the RCA_CONFIRMED report digest for its HEAD.
        from llmtool import high_debug
        task = self._codex_dispatch_task()
        self.store.create(task)
        entry = dict(high_debug._failed_entry(
            family='pr-17', head='a' * 40, d6='executor-1-hd1-aaaaaaa',
            code='HIGH_DEBUG_RUN_FAILED', observed='UNKNOWN', effort='UNKNOWN',
            usage={'input': 1, 'cached': 0, 'output': 1, 'reasoning': 0},
            recorded_at='2026-10-27T00:00:30Z', failed_corrections=None,
            early_reason='review FAIL 2'), status='RCA_CONFIRMED')
        self.store.append_high_debug(task['task_id'], entry)
        first = {
            'executor_task': 'executor-1', 'pr': 17, 'head_sha': 'b' * 40,
            'version': 1, 'attempt': 1, 'family': 'pr-17', 'state': 'CLAIMED',
            'repair_task_id': new_id(), 'comment_id': 9, 'packet_sha256': 'b' * 64,
            'claimed_at': '2026-10-27T00:00:00Z',
        }
        for claims in ([first], [dict(first, state='DISPATCHED')]):
            saved = self.store.get(task['task_id'])
            self.store.compare_and_update(task['task_id'],
                expected={'record_sha256': saved['record_sha256']},
                review_repair_claims=claims)
        first = dict(first, state='DISPATCHED')
        second = dict(first, head_sha='a' * 40, version=2, attempt=2, state='CLAIMED',
                      repair_task_id=new_id(), claimed_at='2026-10-27T00:01:00Z',
                      high_debug_report_sha256=entry['report_sha256'])
        saved = self.store.get(task['task_id'])
        for bad in ([first, dict(second, high_debug_report_sha256='0' * 64)],
                    [first, dict(second, head_sha='c' * 40)]):
            with self.assertRaises(LLMToolError) as caught:
                self.store.compare_and_update(task['task_id'],
                    expected={'record_sha256': saved['record_sha256']},
                    review_repair_claims=bad)
            self.assertEqual(caught.exception.task_store_error_code,
                             'REVIEW_REPAIR_CLAIM_INVALID')
        updated = self.store.compare_and_update(task['task_id'],
            expected={'record_sha256': saved['record_sha256']},
            review_repair_claims=[first, second])
        self.assertEqual(updated['review_repair_claims'][1]['high_debug_report_sha256'],
                         entry['report_sha256'])

    def test_lineage_is_optional_and_strictly_validated(self):
        # #1502 WP-D (spec 3-2, 7): an old Task has no lineage and is its own ROOT.
        root = self._codex_dispatch_task()
        self.store.create(root)
        root_id = root['task_id']
        self.assertNotIn('lineage', self.store.get(root_id))
        base = {'lineage_id': root_id, 'kind': 'root', 'parent': None, 'replaces': [],
                'state': 'PARKED', 'reason': None, 'base_sha': None,
                'recorded_at': '2026-11-13T00:00:00Z'}
        self.store.update(root_id, lineage=base)
        self.assertEqual(self.store.get(root_id)['lineage'], base)
        child = self._codex_dispatch_task()
        self.store.create(child)
        child_id = child['task_id']
        line = dict(base, kind='child', parent=root_id, state='OPEN', base_sha='a' * 40)
        for bad in (dict(line, parent=None), dict(line, state='DROPPED'),
                    dict(line, kind='replan'), dict(line, lineage_id=child_id, parent=child_id),
                    dict(line, state='PARKED'), dict(line, extra=1), dict(line, base_sha='x'),
                    dict(line, kind='replan', replaces=['b' * 32] * 4)):
            with self.subTest(bad=bad):
                with self.assertRaises(LLMToolError) as caught:
                    self.store.update(child_id, lineage=bad)
                self.assertEqual(caught.exception.task_store_error_code,
                                 'TASK_LINEAGE_INVALID')
        replan = dict(line, kind='replan', replaces=['b' * 32])
        self.store.update(child_id, lineage=replan)
        dropped = dict(replan, state='DROPPED', reason='original function unaffected')
        self.store.update(child_id, lineage=dropped)
        self.assertEqual(self.store.get(child_id)['lineage'], dropped)
        # #1502 WP-D D9b: a plain update cannot reopen an end state or rewrite base_sha.
        for bad in (dict(replan, state='OPEN'), dict(dropped, base_sha='c' * 40)):
            with self.subTest(bypass=bad):
                with self.assertRaises(LLMToolError) as caught:
                    self.store.update(child_id, lineage=bad)
                self.assertEqual(caught.exception.task_store_error_code,
                                 'TASK_LINEAGE_TRANSITION_INVALID')
        self.assertEqual(self.store.get(child_id)['lineage'], dropped)

    def test_create_refuses_a_record_that_carries_a_lineage(self):
        # #1502 WP-D D10: lineage is written only by record_lineage, after create.
        from llmtool.task_store import record_lineage
        task = self._codex_dispatch_task()
        line = {'lineage_id': task['task_id'], 'kind': 'root', 'parent': None,
                'replaces': [], 'state': 'OPEN', 'reason': None, 'base_sha': None,
                'recorded_at': '2026-11-13T00:00:00Z'}
        for state in ('OPEN', 'PARKED', 'CLOSED'):
            with self.subTest(state=state):
                with self.assertRaises(LLMToolError) as caught:
                    self.store.create(dict(task, lineage=dict(line, state=state)))
                self.assertEqual(caught.exception.task_store_error_code,
                                 'TASK_LINEAGE_TRANSITION_INVALID')
        self.store.create(task)
        record_lineage(self.store, task['task_id'], line)
        self.assertEqual(self.store.get(task['task_id'])['lineage'], line)

    def test_record_lineage_only_moves_forward(self):
        # #1502 WP-D: kind, lineage and parent never change; an end state is final.
        from llmtool.task_store import record_lineage
        root = self._codex_dispatch_task()
        self.store.create(root)
        root_id = root['task_id']
        base = {'lineage_id': root_id, 'kind': 'root', 'parent': None, 'replaces': [],
                'state': 'OPEN', 'reason': None, 'base_sha': None,
                'recorded_at': '2026-11-13T00:00:00Z'}
        record_lineage(self.store, root_id, base)
        record_lineage(self.store, root_id, dict(base, state='PARKED'))
        record_lineage(self.store, root_id, dict(base, state='CLOSED'))
        for bad in (dict(base, state='PARKED'), dict(base, state='CLOSED'),
                    dict(base, kind='child')):
            with self.assertRaises(LLMToolError) as caught:
                record_lineage(self.store, root_id, bad)
            self.assertEqual(caught.exception.task_store_error_code,
                             'TASK_LINEAGE_TRANSITION_INVALID')
        other = self._codex_dispatch_task()
        self.store.create(other)
        record_lineage(self.store, other['task_id'],
                       dict(base, lineage_id=other['task_id'], state='PARKED'))
        with self.assertRaises(LLMToolError) as caught:
            record_lineage(self.store, other['task_id'],
                           dict(base, lineage_id=other['task_id'], state='OPEN'))
        self.assertEqual(caught.exception.task_store_error_code,
                         'TASK_LINEAGE_TRANSITION_INVALID')

    _DISPUTE_REF = 'https://github.com/davidcatcat/davidcattoolsproject/pull/42#issuecomment-1001'
    _RULING_REF = 'https://github.com/davidcatcat/davidcattoolsproject/pull/42#issuecomment-1002'

    @classmethod
    def _dispute(cls, **changes):
        entry = {'finding_id': 'F1', 'pr': 42, 'head_sha': 'a' * 40, 'review_version': 1,
                 'severity': 'High', 'state': 'OPEN', 'evidence_ref': cls._DISPUTE_REF,
                 'opened_at': '2026-11-13T00:00:00Z'}
        entry.update(changes)
        return entry

    @classmethod
    def _ruling(cls, **changes):
        ruling = {'outcome': 'WITHDRAWN', 'head_sha': 'a' * 40, 'reviewer': 'codex-reviewer',
                  'reviewer_task': 'arbiter-d6', 'reviewer_session': 'arbiter-session-2',
                  'evidence_ref': cls._RULING_REF, 'ruled_at': '2026-11-13T01:00:00Z'}
        ruling.update(changes)
        return ruling

    def _assert_store_code(self, code, task_id, **changes):
        with self.assertRaises(LLMToolError) as caught:
            self.store.update(task_id, **changes)
        self.assertEqual(caught.exception.task_store_error_code, code)

    def test_review_disputes_are_optional_and_strictly_validated(self):
        # #1502 WP-D D13: an old Task has no disputes; each entry is strictly shaped.
        task = self._codex_dispatch_task()
        self.store.create(task)
        task_id = task['task_id']
        self.assertNotIn('review_disputes', self.store.get(task_id))
        dot_repo = 'https://github.com/example/../pull/42#issuecomment-1001'
        not_github = 'https://example.com/example/other/pull/42#issuecomment-1001'
        for bad in (dict(self._dispute(), extra=1), self._dispute(finding_id='X1'),
                    self._dispute(finding_id='F0'), self._dispute(pr=0),
                    self._dispute(head_sha='x'), self._dispute(review_version=0),
                    self._dispute(severity='Severe'), self._dispute(evidence_ref=dot_repo),
                    self._dispute(evidence_ref=not_github),
                    self._dispute(evidence_ref=self._DISPUTE_REF.split('#')[0]),
                    self._dispute(evidence_ref=self._DISPUTE_REF.replace('https', 'http')),
                    self._dispute(opened_at='2026-11-13'),
                    self._dispute(state='DEFERRED'), self._dispute(state='DEFERRED',
                                                                   severity='Medium')):
            with self.subTest(bad=bad):
                self._assert_store_code('REVIEW_DISPUTE_INVALID', task_id,
                                        review_disputes=[bad])
        for bad in (self._dispute(state='RULED', ruling=self._ruling()),
                    self._dispute(state='WITHDRAWN'), self._dispute(state='CLOSED'),
                    dict(self._dispute(), ruling=self._ruling())):
            with self.subTest(appended=bad):
                self._assert_store_code('REVIEW_DISPUTE_TRANSITION_INVALID', task_id,
                                        review_disputes=[bad])
        self.assertNotIn('review_disputes', self.store.get(task_id))
        deferred = self._dispute(finding_id='F2', severity='Advisory', state='DEFERRED')
        self.store.update(task_id, review_disputes=[deferred])
        self.assertEqual(self.store.get(task_id)['review_disputes'], [deferred])

    def test_review_disputes_append_and_rule_once(self):
        # #1502 WP-D D13: append-only; OPEN moves once; one ruling per (pr, finding_id).
        task = self._codex_dispatch_task()
        self.store.create(task)
        task_id = task['task_id']
        opened = self._dispute()
        self.store.update(task_id, review_disputes=[opened])
        self._assert_store_code('REVIEW_DISPUTE_INVALID', task_id,
                                review_disputes=[opened, self._dispute()])
        for bad_ruling in (self._ruling(head_sha='b' * 40), self._ruling(outcome='DENIED'),
                           self._ruling(reviewer='pm'), self._ruling(reviewer_session=''),
                           self._ruling(evidence_ref=self._DISPUTE_REF),
                           self._ruling(ruled_at='2026-11-12T00:00:00Z'),
                           dict(self._ruling(), extra=1)):
            with self.subTest(ruling=bad_ruling):
                self._assert_store_code(
                    'REVIEW_DISPUTE_INVALID', task_id,
                    review_disputes=[dict(opened, state='RULED', ruling=bad_ruling)])
        self._assert_store_code(
            'REVIEW_DISPUTE_TRANSITION_INVALID', task_id,
            review_disputes=[dict(opened, state='RULED', ruling=self._ruling(),
                                  severity='Low')])
        ruled = dict(opened, state='RULED', ruling=self._ruling())
        self.store.update(task_id, review_disputes=[ruled])
        self.assertEqual(self.store.get(task_id)['review_disputes'], [ruled])
        for bad in ([], [dict(ruled, ruling=self._ruling(outcome='UPHELD'))],
                    [dict(ruled, state='WITHDRAWN')], [opened]):
            with self.subTest(rewrite=bad):
                self._assert_store_code('REVIEW_DISPUTE_TRANSITION_INVALID', task_id,
                                        review_disputes=bad)
        # A second dispute of a ruled finding, even on a new HEAD, can never be ruled.
        self._assert_store_code('REVIEW_DISPUTE_INVALID', task_id,
                                review_disputes=[ruled, self._dispute(head_sha='b' * 40)])
        other = self._dispute(finding_id='F3', severity='Medium')
        self.store.update(task_id, review_disputes=[ruled, other])
        self.store.update(task_id, review_disputes=[ruled, dict(other, state='WITHDRAWN')])
        self.assertEqual(self.store.get(task_id)['review_disputes'][1]['state'], 'WITHDRAWN')

    def test_review_dispute_evidence_of_one_task_stays_in_one_repository(self):
        # The store holds no trusted repository: the dispute commands check the
        # caller's trusted slug. The store checks the URL shape and that all
        # dispute evidence of a Task names one repository.
        other_ref = 'https://github.com/example/other/pull/42#issuecomment-1001'
        other_ruling = 'https://github.com/example/other/pull/42#issuecomment-1002'
        task = self._codex_dispatch_task()
        self.store.create(task)
        task_id = task['task_id']
        opened = self._dispute()
        self.store.update(task_id, review_disputes=[opened])
        self._assert_store_code(
            'REVIEW_DISPUTE_INVALID', task_id,
            review_disputes=[dict(opened, state='RULED',
                                  ruling=self._ruling(evidence_ref=other_ruling))])
        self._assert_store_code(
            'REVIEW_DISPUTE_INVALID', task_id,
            review_disputes=[opened, self._dispute(finding_id='F3', severity='Medium',
                                                   evidence_ref=other_ref)])
        self.assertEqual(self.store.get(task_id)['review_disputes'], [opened])
        # A Task whose disputes all name another repository is shaped correctly.
        other_task = self._codex_dispatch_task()
        self.store.create(other_task)
        other = self._dispute(evidence_ref=other_ref)
        self.store.update(other_task['task_id'], review_disputes=[other])
        ruled = dict(other, state='RULED', ruling=self._ruling(evidence_ref=other_ruling))
        self.store.update(other_task['task_id'], review_disputes=[ruled])
        self.assertEqual(self.store.get(other_task['task_id'])['review_disputes'], [ruled])

    def test_review_dispute_ref_parts_need_the_callers_repository(self):
        repository = 'davidcatcat/davidcattoolsproject'
        self.assertEqual(task_store_module.review_dispute_ref_parts(self._DISPUTE_REF,
                                                                    repository), (42, 1001))
        self.assertTrue(task_store_module.valid_review_dispute_ref(self._RULING_REF, repository))
        for other in ('example/other', 'davidcatcat/other', None, '', 'no-slash',
                      'a/b/c', 'example/..', repository + '/'):
            with self.subTest(repository=other):
                self.assertIsNone(task_store_module.review_dispute_ref_parts(
                    self._DISPUTE_REF, other))
                self.assertFalse(task_store_module.valid_review_dispute_ref(
                    self._DISPUTE_REF, other))

    def test_create_refuses_a_record_that_carries_review_disputes(self):
        task = self._codex_dispatch_task()
        with self.assertRaises(LLMToolError) as caught:
            self.store.create(dict(task, review_disputes=[self._dispute()]))
        self.assertEqual(caught.exception.task_store_error_code,
                         'REVIEW_DISPUTE_TRANSITION_INVALID')

    def test_review_dispute_text_is_screened(self):
        # The real screen decides; it is injected so no credential-shaped text is needed.
        task = self._codex_dispatch_task()
        self.store.create(task)
        flagged = self._DISPUTE_REF
        real_screen = task_store_module._emitted_sensitive
        with patch.object(task_store_module, '_emitted_sensitive',
                          side_effect=lambda value: value == flagged or real_screen(value)):
            self._assert_store_code('REVIEW_DISPUTE_INVALID', task['task_id'],
                                    review_disputes=[self._dispute()])
        self.assertNotIn('review_disputes', self.store.get(task['task_id']))
    WAITING_RESUME = 'quota resets at 00:00 UTC'

    def _waiting(self, **overrides):
        value = {'waiting_since': '2026-11-13T00:00:00Z', 'reason': 'QUOTA',
                 'resume_condition': self.WAITING_RESUME}
        value.update(overrides)
        return value

    def test_waiting_is_optional_and_strictly_validated(self):
        # #1502 WP-D D12: a Task without the field is not WAITING and stays as it is.
        from llmtool.task_store import clear_waiting, is_waiting
        task = self._codex_dispatch_task()
        self.store.create(task)
        task_id = task['task_id']
        legacy = self.store.get(task_id)
        self.assertNotIn('waiting', legacy)
        self.assertFalse(is_waiting(legacy))
        self.assertEqual(clear_waiting(self.store, task_id), legacy)
        self.assertEqual(self.store.get(task_id), legacy)   # no write at all
        bad_values = (
            'QUOTA', [], {}, self._waiting(extra=1),
            {'waiting_since': '2026-11-13T00:00:00Z', 'reason': 'QUOTA'},
            self._waiting(waiting_since='2026-11-13 00:00:00'),
            self._waiting(waiting_since='2026-11-13T00:00:00.000001Z'),
            self._waiting(waiting_since='2026-13-01T00:00:00Z'),
            self._waiting(waiting_since=None),
            self._waiting(reason='quota'), self._waiting(reason=''),
            self._waiting(reason='Q' * 65), self._waiting(reason='ROUTE-DOWN'),
            self._waiting(reason=7),
            # Waiting for an Owner answer is waiting_owner, never WAITING.
            self._waiting(reason='WAITING_OWNER'), self._waiting(reason='OWNER'),
            self._waiting(resume_condition=''), self._waiting(resume_condition='   '),
            self._waiting(resume_condition='first line\nsecond line'),
            self._waiting(resume_condition='x' * 501),
            self._waiting(resume_condition=None),
        )
        for bad in bad_values:
            with self.subTest(bad=bad):
                with self.assertRaises(LLMToolError) as caught:
                    self.store.update(task_id, waiting=bad)
                self.assertEqual(caught.exception.task_store_error_code,
                                 'TASK_WAITING_INVALID')
        self.assertEqual(self.store.get(task_id), legacy)
        for good in (self._waiting(), self._waiting(reason='AUTH'),
                     self._waiting(reason='Q' * 64, resume_condition=(
                         'wait until the shared runner is back online ' * 12)[:500])):
            with self.subTest(good=good):
                other = self._codex_dispatch_task()
                self.store.create(other)
                self.store.update(other['task_id'], waiting=good)
                stored = self.store.get(other['task_id'])
                self.assertEqual(stored['waiting'], good)
                self.assertTrue(is_waiting(stored))
        for value in (None, {}, {'waiting': None}, {'waiting': 'QUOTA'}):
            self.assertFalse(is_waiting(value))

    def test_waiting_resume_condition_goes_through_the_sensitive_screen(self):
        # The screen is injected (Owner #763 A); the store must still refuse.
        from llmtool.task_store import enter_waiting
        task = self._codex_dispatch_task()
        self.store.create(task)
        before = self.store.get(task['task_id'])
        flagged = 'condition the injected screen flags'
        calls = []

        def screen(value):
            calls.append(value)
            return value == flagged
        with patch.object(task_store_module, '_emitted_sensitive', side_effect=screen):
            with self.assertRaises(LLMToolError) as caught:
                enter_waiting(self.store, task['task_id'], reason='QUOTA',
                              resume_condition=flagged, now='2026-11-13T00:00:00Z')
            self.assertEqual(caught.exception.task_store_error_code, 'TASK_WAITING_INVALID')
            self.assertIn(flagged, calls)
            self.assertEqual(self.store.get(task['task_id']), before)
            enter_waiting(self.store, task['task_id'], reason='QUOTA',
                          resume_condition=self.WAITING_RESUME, now='2026-11-13T00:00:00Z')
        self.assertIn(self.WAITING_RESUME, calls)
        self.assertEqual(self.store.get(task['task_id'])['waiting'], self._waiting())

    def test_valid_waiting_is_the_store_rule_without_raising(self):
        # The monitor shows a Task file's WAITING record only when this says True.
        from llmtool.task_store import valid_waiting
        for good in (self._waiting(), self._waiting(reason='AUTH')):
            with self.subTest(good=good):
                self.assertIs(valid_waiting(good), True)
        for bad in (None, 'QUOTA', [], {}, self._waiting(extra=1),
                    self._waiting(waiting_since='2026-11-13 00:00:00'),
                    self._waiting(reason='quota'), self._waiting(reason='WAITING_OWNER'),
                    self._waiting(resume_condition='   '),
                    self._waiting(resume_condition='first\x1bline'),
                    self._waiting(resume_condition='x' * 501),
                    self._waiting(resume_condition=None)):
            with self.subTest(bad=bad):
                self.assertIs(valid_waiting(bad), False)
        # The sensitive screen is injected (Owner #763 A) and still decides.
        flagged = 'condition the injected screen flags'
        calls = []

        def screen(value):
            calls.append(value)
            return value == flagged
        with patch.object(task_store_module, '_emitted_sensitive', side_effect=screen):
            self.assertIs(valid_waiting(self._waiting(resume_condition=flagged)), False)
            self.assertIs(valid_waiting(self._waiting()), True)
        self.assertIn(flagged, calls)
        self.assertIn(self.WAITING_RESUME, calls)

    def test_enter_and_clear_waiting_restart_the_clock_only_on_progress(self):
        from llmtool.task_store import clear_waiting, enter_waiting, is_waiting
        task = self._codex_dispatch_task()
        self.store.create(task)
        task_id = task['task_id']
        saved = enter_waiting(self.store, task_id, reason='QUOTA',
                              resume_condition=self.WAITING_RESUME,
                              now='2026-11-13T00:00:00Z')
        self.assertEqual(saved['waiting'], self._waiting())
        # Entering again while WAITING never moves the clock (no silent reset).
        again = enter_waiting(self.store, task_id, reason='AUTH',
                              resume_condition='the Owner logs in again',
                              now='2026-11-13T05:00:00Z')
        self.assertEqual(again['waiting'], {'waiting_since': '2026-11-13T00:00:00Z',
                                            'reason': 'AUTH',
                                            'resume_condition': 'the Owner logs in again'})
        # A plain update cannot move the clock either.
        with self.assertRaises(LLMToolError) as caught:
            self.store.update(task_id, waiting=dict(again['waiting'],
                                                    waiting_since='2026-11-13T06:00:00Z'))
        self.assertEqual(caught.exception.task_store_error_code,
                         'TASK_WAITING_TRANSITION_INVALID')
        for kwargs in ({'reason': 'WAITING_OWNER', 'resume_condition': 'an answer'},
                       {'reason': 'QUOTA', 'resume_condition': ''}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(LLMToolError) as caught:
                    enter_waiting(self.store, task_id, now='2026-11-13T07:00:00Z', **kwargs)
                self.assertEqual(caught.exception.task_store_error_code,
                                 'TASK_WAITING_INVALID')
        self.assertEqual(self.store.get(task_id)['waiting'], again['waiting'])
        # Progress clears WAITING; clearing again writes nothing.
        cleared = clear_waiting(self.store, task_id)
        self.assertIsNone(cleared['waiting'])
        self.assertFalse(is_waiting(cleared))
        self.assertEqual(clear_waiting(self.store, task_id), self.store.get(task_id))
        self.assertEqual(self.store.get(task_id)['record_sha256'], cleared['record_sha256'])
        # Entering after a clear restarts the clock.
        restarted = enter_waiting(self.store, task_id, reason='QUOTA',
                                  resume_condition=self.WAITING_RESUME,
                                  now='2026-11-14T00:00:00Z')
        self.assertEqual(restarted['waiting']['waiting_since'], '2026-11-14T00:00:00Z')
        # Without now, the clock starts at the current UTC second.
        fresh = self._codex_dispatch_task()
        self.store.create(fresh)
        since = enter_waiting(self.store, fresh['task_id'], reason='ENV',
                              resume_condition='the runner is back')['waiting']['waiting_since']
        self.assertTrue(task_store_module._valid_utc_seconds(since))
        started = task_store_module._utc_seconds(since).timestamp()
        self.assertLessEqual(abs(time.time() - started), 60)
        unstarted = self._codex_dispatch_task()
        self.store.create(unstarted)
        with self.assertRaises(LLMToolError) as caught:
            enter_waiting(self.store, unstarted['task_id'], reason='ENV',
                          resume_condition='the runner is back', now='yesterday')
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_WAITING_INVALID')
        self.assertNotIn('waiting', self.store.get(unstarted['task_id']))

    def test_enter_waiting_never_undoes_a_concurrent_change(self):
        from llmtool.task_store import clear_waiting, enter_waiting
        task = self._codex_dispatch_task()
        self.store.create(task)
        task_id = task['task_id']
        stale = self.store.get(task_id)
        enter_waiting(self.store, task_id, reason='QUOTA',
                      resume_condition=self.WAITING_RESUME, now='2026-11-13T00:00:00Z')
        current = self.store.get(task_id)
        with patch.object(self.store, 'get', return_value=stale):
            with self.assertRaises(TaskStoreConflict):
                enter_waiting(self.store, task_id, reason='QUOTA',
                              resume_condition=self.WAITING_RESUME,
                              now='2026-11-14T00:00:00Z')
        with patch.object(self.store, 'get',
                          return_value=dict(current, waiting=self._waiting(reason='ENV'))):
            with self.assertRaises(TaskStoreConflict):
                clear_waiting(self.store, task_id)
        self.assertEqual(self.store.get(task_id), current)

    def test_park_and_merge_claim_exclude_each_other_under_the_task_lock(self):
        # #1502 D10c2 (security v91, CWE-362): parking a ROOT and claiming its merge
        # are each checked against the other inside the same locked write.
        from llmtool.task_store import park_lineage_root, record_lineage
        claim = {'pr': 17, 'head_sha': 'a' * 40, 'state': 'CLAIMED',
                 'claimed_at': '2026-11-13T00:00:00.000000Z'}

        def root_line(task_id, state):
            return {'lineage_id': task_id, 'kind': 'root', 'parent': None, 'replaces': [],
                    'state': state, 'reason': 'split', 'base_sha': None,
                    'recorded_at': '2026-11-13T00:00:00Z'}

        def refused(code, call):
            with self.assertRaises(LLMToolError) as caught:
                call()
            self.assertEqual(caught.exception.task_store_error_code, code)

        def new_task():
            task = self._codex_dispatch_task()
            self.store.create(task)
            return task['task_id']

        # Merge claimed first: the park is refused and the lineage is not written.
        claimed = new_task()
        self.store.update(claimed, merge_attempt=claim)
        refused('SPLIT_ROOT_MERGE_STARTED',
                lambda: park_lineage_root(self.store, claimed, root_line(claimed, 'PARKED')))
        refused('SPLIT_ROOT_MERGE_STARTED',
                lambda: record_lineage(self.store, claimed, root_line(claimed, 'PARKED')))
        self.assertNotIn('lineage', self.store.get(claimed))
        # Parked first: a merge claim is refused even with a stale record digest.
        parked = new_task()
        stale = self.store.get(parked)['record_sha256']
        park_lineage_root(self.store, parked, root_line(parked, 'PARKED'))
        park_lineage_root(self.store, parked, root_line(parked, 'PARKED'))  # a later split
        refused('MERGE_ROOT_PARKED', lambda: self.store.compare_and_update(
            parked, expected={'record_sha256': stale}, merge_attempt=claim))
        refused('MERGE_ROOT_PARKED', lambda: self.store.update(parked, merge_attempt=claim))
        self.assertNotIn('merge_attempt', self.store.get(parked))
        # An ended ROOT is neither parked nor merged.
        ended = new_task()
        record_lineage(self.store, ended, root_line(ended, 'FAILED_FINAL'))
        refused('SPLIT_ROOT_NOT_OPEN',
                lambda: park_lineage_root(self.store, ended, root_line(ended, 'PARKED')))
        refused('MERGE_LINEAGE_ENDED', lambda: self.store.update(ended, merge_attempt=claim))
        self.assertEqual(self.store.get(ended)['lineage']['state'], 'FAILED_FINAL')
        # park_lineage_root writes only a PARKED ROOT lineage of that Task.
        plain = new_task()
        for bad in (root_line(plain, 'OPEN'), dict(root_line(plain, 'PARKED'), kind='child'),
                    root_line(ended, 'PARKED'), None):
            refused('TASK_LINEAGE_INVALID', lambda bad=bad: park_lineage_root(
                self.store, plain, bad))
        # An OPEN lineage Task still claims and records its merge.
        record_lineage(self.store, plain, root_line(plain, 'OPEN'))
        self.store.update(plain, merge_attempt=claim)
        self.store.update(plain, merge_attempt=dict(claim, state='MERGED'))
        self.assertEqual(self.store.get(plain)['merge_attempt']['state'], 'MERGED')

    def test_one_write_never_carries_park_and_merge_claim_together(self):
        # PR #1758 security v91 (CWE-841): the guards check the proposed combined
        # state, so a single update or compare_and_update that sets a PARKED lineage
        # and a CLAIMED merge_attempt together is refused whole, in either order.
        from llmtool.task_store import record_lineage
        claim = {'pr': 17, 'head_sha': 'a' * 40, 'state': 'CLAIMED',
                 'claimed_at': '2026-11-13T00:00:00.000000Z'}

        def root_line(task_id, state):
            return {'lineage_id': task_id, 'kind': 'root', 'parent': None, 'replaces': [],
                    'state': state, 'reason': 'split', 'base_sha': None,
                    'recorded_at': '2026-11-13T00:00:00Z'}

        def combined(task_id, state, park_first):
            line = root_line(task_id, state)
            return ({'lineage': line, 'merge_attempt': dict(claim)} if park_first else
                    {'merge_attempt': dict(claim), 'lineage': line})

        def refused(task_id, code, call):
            before = self.store.get(task_id)
            with self.assertRaises(LLMToolError) as caught:
                call()
            self.assertEqual(caught.exception.task_store_error_code, code)
            self.assertEqual(self.store.get(task_id), before)
            self.assertNotIn('merge_attempt', self.store.get(task_id))

        for with_open_lineage in (False, True):
            for park_first in (True, False):
                task = self._codex_dispatch_task()
                self.store.create(task)
                task_id = task['task_id']
                if with_open_lineage:
                    record_lineage(self.store, task_id, root_line(task_id, 'OPEN'))
                with self.subTest(open_lineage=with_open_lineage, park_first=park_first):
                    refused(task_id, 'MERGE_ROOT_PARKED', lambda: self.store.update(
                        task_id, **combined(task_id, 'PARKED', park_first)))
                    digest = self.store.get(task_id)['record_sha256']
                    refused(task_id, 'MERGE_ROOT_PARKED', lambda: self.store.compare_and_update(
                        task_id, expected={'record_sha256': digest},
                        **combined(task_id, 'PARKED', park_first)))
                    # An ended lineage in the same write is refused the same way.
                    refused(task_id, 'MERGE_LINEAGE_ENDED', lambda: self.store.update(
                        task_id, **combined(task_id, 'FAILED_FINAL', park_first)))
                    if with_open_lineage:
                        self.assertEqual(self.store.get(task_id)['lineage']['state'], 'OPEN')
                    else:
                        self.assertNotIn('lineage', self.store.get(task_id))
                    # Each field alone still behaves as before: the claim on an OPEN
                    # Task succeeds, and parking it afterwards is then refused.
                    self.store.update(task_id, merge_attempt=dict(claim))
                    refused_park = root_line(task_id, 'PARKED')
                    with self.assertRaises(LLMToolError) as caught:
                        self.store.update(task_id, lineage=refused_park)
                    self.assertEqual(caught.exception.task_store_error_code,
                                     'SPLIT_ROOT_MERGE_STARTED')
                    self.assertEqual(self.store.get(task_id)['merge_attempt']['state'],
                                     'CLAIMED')
        # A combined OPEN lineage with a merge claim stays allowed.
        task = self._codex_dispatch_task()
        self.store.create(task)
        self.store.update(task['task_id'], lineage=root_line(task['task_id'], 'OPEN'),
                          merge_attempt=dict(claim))
        stored = self.store.get(task['task_id'])
        self.assertEqual((stored['lineage']['state'], stored['merge_attempt']['state']),
                         ('OPEN', 'CLAIMED'))

    def test_waiting_and_split_merge_gates_each_still_apply_after_the_merge(self):
        # PR #1764 merge check: the D12 WAITING transition gate and the D10c2
        # PARKED / merge-claim gates sit side by side under the task lock. Each
        # still applies alone on a Task that carries the other fields.
        from llmtool.task_store import (clear_waiting, enter_waiting, park_lineage_root,
                                        record_lineage)
        claim = {'pr': 17, 'head_sha': 'a' * 40, 'state': 'CLAIMED',
                 'claimed_at': '2026-11-13T00:00:00.000000Z'}

        def root_line(task_id, state):
            return {'lineage_id': task_id, 'kind': 'root', 'parent': None, 'replaces': [],
                    'state': state, 'reason': 'split', 'base_sha': None,
                    'recorded_at': '2026-11-13T00:00:00Z'}

        def refused(task_id, code, call):
            before = self.store.get(task_id)
            with self.assertRaises(LLMToolError) as caught:
                call()
            self.assertEqual(caught.exception.task_store_error_code, code)
            self.assertEqual(self.store.get(task_id), before)

        def waiting_task():
            task = self._codex_dispatch_task()
            self.store.create(task)
            enter_waiting(self.store, task['task_id'], reason='QUOTA',
                          resume_condition=self.WAITING_RESUME, now='2026-11-13T00:00:00Z')
            return task['task_id']

        moved = self._waiting(waiting_since='2026-11-13T06:00:00Z')
        # WAITING gate alone, on a Task with an OPEN lineage and a CLAIMED merge.
        claimed = waiting_task()
        record_lineage(self.store, claimed, root_line(claimed, 'OPEN'))
        self.store.update(claimed, merge_attempt=dict(claim))
        refused(claimed, 'TASK_WAITING_TRANSITION_INVALID',
                lambda: self.store.update(claimed, waiting=moved))
        # D10c2 park gate alone, on a WAITING Task whose merge started.
        refused(claimed, 'SPLIT_ROOT_MERGE_STARTED', lambda: park_lineage_root(
            self.store, claimed, root_line(claimed, 'PARKED')))
        refused(claimed, 'SPLIT_ROOT_MERGE_STARTED', lambda: record_lineage(
            self.store, claimed, root_line(claimed, 'PARKED')))
        self.assertEqual(self.store.get(claimed)['waiting'], self._waiting())
        # D10c2 merge-claim gate alone, on a WAITING Task that was parked.
        parked = waiting_task()
        park_lineage_root(self.store, parked, root_line(parked, 'PARKED'))
        self.assertEqual(self.store.get(parked)['waiting'], self._waiting())
        refused(parked, 'MERGE_ROOT_PARKED',
                lambda: self.store.update(parked, merge_attempt=dict(claim)))
        digest = self.store.get(parked)['record_sha256']   # current, so only the gate refuses
        refused(parked, 'MERGE_ROOT_PARKED', lambda: self.store.compare_and_update(
            parked, expected={'record_sha256': digest}, merge_attempt=dict(claim)))
        # WAITING still enters, keeps its clock and clears on a parked ROOT.
        refused(parked, 'TASK_WAITING_TRANSITION_INVALID',
                lambda: self.store.update(parked, waiting=moved))
        again = enter_waiting(self.store, parked, reason='ENV',
                              resume_condition='the runner is back',
                              now='2026-11-13T09:00:00Z')
        self.assertEqual(again['waiting']['waiting_since'], '2026-11-13T00:00:00Z')
        self.assertIsNone(clear_waiting(self.store, parked)['waiting'])
        stored = self.store.get(parked)
        self.assertEqual(stored['lineage']['state'], 'PARKED')
        self.assertNotIn('merge_attempt', stored)

    def test_one_write_with_waiting_lineage_and_merge_claim_meets_every_gate(self):
        # PR #1764 merge check: one update carrying waiting, lineage and
        # merge_attempt together is refused whole when any gate refuses, and
        # commits all three only when every gate allows it.
        from llmtool.task_store import enter_waiting, record_lineage
        claim = {'pr': 17, 'head_sha': 'a' * 40, 'state': 'CLAIMED',
                 'claimed_at': '2026-11-13T00:00:00.000000Z'}

        def root_line(task_id, state):
            return {'lineage_id': task_id, 'kind': 'root', 'parent': None, 'replaces': [],
                    'state': state, 'reason': 'split', 'base_sha': None,
                    'recorded_at': '2026-11-13T00:00:00Z'}

        def refused(task_id, code, call):
            before = self.store.get(task_id)
            with self.assertRaises(LLMToolError) as caught:
                call()
            self.assertEqual(caught.exception.task_store_error_code, code)
            self.assertEqual(self.store.get(task_id), before)

        def new_task(waiting):
            task = self._codex_dispatch_task()
            self.store.create(task)
            if waiting:
                enter_waiting(self.store, task['task_id'], reason='QUOTA',
                              resume_condition=self.WAITING_RESUME,
                              now='2026-11-13T00:00:00Z')
            return task['task_id']

        kept = self._waiting(reason='ENV')
        moved = self._waiting(waiting_since='2026-11-13T06:00:00Z')
        for already_waiting in (False, True):
            with self.subTest(already_waiting=already_waiting):
                task_id = new_task(already_waiting)
                # PARKED lineage + merge claim + a valid WAITING: the D10c2 gate refuses.
                refused(task_id, 'MERGE_ROOT_PARKED', lambda: self.store.update(
                    task_id, waiting=kept, lineage=root_line(task_id, 'PARKED'),
                    merge_attempt=dict(claim)))
                digest = self.store.get(task_id)['record_sha256']
                refused(task_id, 'MERGE_ROOT_PARKED', lambda: self.store.compare_and_update(
                    task_id, expected={'record_sha256': digest}, waiting=kept,
                    lineage=root_line(task_id, 'PARKED'), merge_attempt=dict(claim)))
                # An ended lineage in the same write is refused the same way.
                refused(task_id, 'MERGE_LINEAGE_ENDED', lambda: self.store.update(
                    task_id, waiting=kept, lineage=root_line(task_id, 'FAILED_FINAL'),
                    merge_attempt=dict(claim)))
                # An invalid WAITING shape refuses an otherwise allowed OPEN claim.
                refused(task_id, 'TASK_WAITING_INVALID', lambda: self.store.update(
                    task_id, waiting=self._waiting(reason='WAITING_OWNER'),
                    lineage=root_line(task_id, 'OPEN'), merge_attempt=dict(claim)))
                if already_waiting:
                    # A moved clock refuses an otherwise allowed OPEN claim.
                    refused(task_id, 'TASK_WAITING_TRANSITION_INVALID',
                            lambda: self.store.update(
                                task_id, waiting=moved, lineage=root_line(task_id, 'OPEN'),
                                merge_attempt=dict(claim)))
                stored = self.store.get(task_id)
                self.assertNotIn('lineage', stored)
                self.assertNotIn('merge_attempt', stored)
                self.assertEqual(stored.get('waiting'),
                                 self._waiting() if already_waiting else None)
                # Every gate allows it: all three are written together.
                self.store.update(task_id, waiting=kept, lineage=root_line(task_id, 'OPEN'),
                                  merge_attempt=dict(claim))
                stored = self.store.get(task_id)
                self.assertEqual(stored['waiting'], kept)
                self.assertEqual((stored['lineage']['state'], stored['merge_attempt']['state']),
                                 ('OPEN', 'CLAIMED'))
                # Afterwards a park with a kept WAITING is refused (merge started),
                # and a park with a moved clock is refused before the WAITING write.
                refused(task_id, 'SPLIT_ROOT_MERGE_STARTED', lambda: self.store.update(
                    task_id, waiting=kept, lineage=root_line(task_id, 'PARKED')))
                refused(task_id, 'SPLIT_ROOT_MERGE_STARTED', lambda: self.store.update(
                    task_id, waiting=moved, lineage=root_line(task_id, 'PARKED')))
        # WAITING + PARKED lineage without a merge claim is allowed in one write;
        # a moved clock in that same write is still refused by the WAITING gate.
        task_id = new_task(True)
        refused(task_id, 'TASK_WAITING_TRANSITION_INVALID', lambda: self.store.update(
            task_id, waiting=moved, lineage=root_line(task_id, 'PARKED')))
        self.store.update(task_id, waiting=kept, lineage=root_line(task_id, 'PARKED'))
        stored = self.store.get(task_id)
        self.assertEqual((stored['waiting'], stored['lineage']['state']), (kept, 'PARKED'))
        self.assertNotIn('merge_attempt', stored)

    def test_retry_claim_transition_is_atomic_with_next_attempt(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        first = {
            'executor_task': 'executor-1', 'pr': 17, 'head_sha': 'a' * 40,
            'version': 1, 'attempt': 1, 'family': 'pr-17', 'state': 'CLAIMED',
            'repair_task_id': new_id(), 'comment_id': 9, 'packet_sha256': 'b' * 64,
            'claimed_at': '2026-10-27T00:00:00Z',
        }
        saved = self.store.get(task['task_id'])
        self.store.compare_and_update(task['task_id'],
            expected={'record_sha256': saved['record_sha256']},
            review_repair_claims=[first])
        saved = self.store.get(task['task_id'])
        first_dispatched = dict(first, state='DISPATCHED')
        self.store.compare_and_update(task['task_id'],
            expected={'record_sha256': saved['record_sha256']},
            review_repair_claims=[first_dispatched])
        second = dict(first, attempt=2, state='CLAIMED', repair_task_id=new_id(),
                      claimed_at='2026-10-27T00:01:00Z')
        saved = self.store.get(task['task_id'])
        updated = self.store.compare_and_update(task['task_id'],
            expected={'record_sha256': saved['record_sha256']},
            review_repair_claims=[dict(first_dispatched, state='FAILED'), second])
        self.assertEqual([claim['state'] for claim in updated['review_repair_claims']],
                         ['FAILED', 'CLAIMED'])

    def test_remote_test_receipt_is_appended_idempotently_to_same_task(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        receipt = {
            'result': 'PASS', 'exit_code': 0, 'source_sha': 'a' * 40,
            'tree': 'b' * 40, 'fetch_head': 'a' * 40,
            'modules': ['test_task_store'], 'identity': {'uid': 65534},
            'started_at': '2026-10-27T00:00:00Z', 'ended_at': '2026-10-27T00:00:01Z',
            'elapsed_s': 1.0, 'ran_total': 1,
            'final': ['OK (skipped=1)'], 'failures': [],
            'log_bytes': 0, 'log_sha256': 'c' * 64,
            'remote_log_path': '/tmp/llmtool-remote-log.Abc123/remote-test.log',
            'task_id': task['task_id'],
            'run_id': task['run_id'], 'executor_task': 'd6-task',
            'pm_session': task['pm_session_id'], 'command_digest': 'd' * 64,
            'receipt_digest': 'e' * 64,
        }
        receipt['receipt_digest'] = hashlib.sha256(json.dumps(
            {key: value for key, value in receipt.items() if key != 'receipt_digest'},
            ensure_ascii=False, sort_keys=True, separators=(',', ':'),
            allow_nan=False).encode() + b'\n').hexdigest()
        self.assertEqual(self.store.append_remote_test_receipt(task['task_id'], receipt), receipt)
        self.assertEqual(self.store.append_remote_test_receipt(task['task_id'], receipt), receipt)
        self.assertEqual(self.store.get(task['task_id'])['remote_test_receipts'], [receipt])

        invalid = {**receipt, 'final': ['OK (expected failures=1)']}
        invalid['receipt_digest'] = hashlib.sha256(json.dumps(
            {key: value for key, value in invalid.items() if key != 'receipt_digest'},
            ensure_ascii=False, sort_keys=True, separators=(',', ':'),
            allow_nan=False).encode() + b'\n').hexdigest()
        with self.assertRaises(LLMToolError):
            self.store.append_remote_test_receipt(task['task_id'], invalid)

    def test_remote_test_receipt_of_an_unrelated_pm_session_is_refused(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        before = self.store.get(task['task_id'])
        unrelated = remote_test_receipt(task, 'pm-' + new_id())
        with self.assertRaises(LLMToolError) as caught:
            self.store.append_remote_test_receipt(task['task_id'], unrelated)
        self.assertEqual(caught.exception.task_store_error_code, 'REMOTE_TEST_RECEIPT_INVALID')
        self.assertEqual(self.store.get(task['task_id']), before)
        with self.assertRaises(LLMToolError) as caught:
            task_store_module._validate_remote_test_receipts(
                dict(before, remote_test_receipts=[unrelated]))
        self.assertEqual(caught.exception.task_store_error_code, 'REMOTE_TEST_RECEIPT_INVALID')
        # With no adoption history only the current session is accepted.
        task_store_module._validate_remote_test_receipts(
            dict(before, remote_test_receipts=[remote_test_receipt(task)]))

    def test_published_pr_is_idempotent_replaceable_and_validated(self):
        task = self._codex_dispatch_task()
        self.store.create(task)

        first = self.store.record_published_pr(task['task_id'], 425, 'a' * 40)
        self.assertEqual(first, self.store.record_published_pr(
            task['task_id'], 425, 'a' * 40))
        self.assertEqual(self.store.get(task['task_id'])['published_pr'], first)
        replacement = self.store.record_published_pr(
            task['task_id'], 426, 'b' * 40)
        self.assertEqual(replacement['pr'], 426)
        self.assertEqual(replacement['head_sha'], 'b' * 40)

        claim = {
            'executor_task': 'executor-d6', 'pr': 426, 'head_sha': 'b' * 40,
            'version': 1, 'state': 'CLAIMED', 'reviewer_task': 'reviewer-d6',
            'native_session_id': None, 'account_mode': 'ROLES',
            'claimed_at': '2026-10-27T00:00:00Z',
            'updated_at': '2026-10-27T00:00:00Z',
        }
        current = self.store.get(task['task_id'])
        saved = self.store.compare_and_update(
            task['task_id'], expected={'record_sha256': current['record_sha256']},
            review_dispatch_claim=[claim], remote_test_receipts=[])
        task_store_module._validate(saved)
        with_published = dict(saved, published_pr=replacement)
        with_published['record_sha256'] = task_store_module._digest(with_published)
        task_store_module._validate(with_published)
        without_published = dict(saved)
        without_published.pop('published_pr')
        without_published['record_sha256'] = task_store_module._digest(without_published)
        task_store_module._validate(without_published)

        for invalid in (
                {'pr': True, 'head_sha': 'a' * 40,
                 'recorded_at': '2026-10-27T00:00:00Z'},
                {'pr': 0, 'head_sha': 'a' * 40,
                 'recorded_at': '2026-10-27T00:00:00Z'},
                {'pr': 425, 'head_sha': 'A' * 40,
                 'recorded_at': '2026-10-27T00:00:00Z'},
                {'pr': 425, 'head_sha': 'a' * 40,
                 'recorded_at': 'bad'},
                {'pr': 425, 'head_sha': 'a' * 40,
                 'recorded_at': '2026-10-27T00:00:00Z', 'extra': True},
        ):
            malformed = dict(saved, published_pr=invalid)
            malformed['record_sha256'] = task_store_module._digest(malformed)
            with self.subTest(invalid=invalid), self.assertRaises(LLMToolError) as caught:
                task_store_module._validate(malformed)
            self.assertEqual(caught.exception.task_store_error_code,
                             'TASK_PUBLISHED_PR_INVALID')

        for pr, head in ((True, 'a' * 40), (0, 'a' * 40), (425, 'bad')):
            with self.subTest(pr=pr, head=head), self.assertRaises(LLMToolError) as caught:
                self.store.record_published_pr(task['task_id'], pr, head)
            self.assertEqual(caught.exception.task_store_error_code,
                             'TASK_PUBLISHED_PR_INVALID')

    def test_publish_operations_are_bounded_capped_and_validated(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        older = self.store.get(task['task_id'])
        self.assertNotIn('publish_operations', older)
        task_store_module._validate(older)

        def operation(number):
            return {'task_id': task['task_id'], 'repository': 'owner/repo',
                    'branch': 'feature/task', 'pr': number,
                    'creation_url': f'https://github.com/owner/repo/pull/{number}',
                    'expected_head': 'a' * 40, 'observed_head': None,
                    'recheck': 'PASS', 'close_attempted': True,
                    'record_comment': 'POSTED', 'outcome': 'CLOSED',
                    'next_action': 'publish again'}

        self.assertEqual(task_store_module.MAX_PUBLISH_OPERATIONS, 8)
        for number in range(1, 11):
            self.assertEqual(self.store.record_publish_operation(
                task['task_id'], operation(number)), operation(number))
        saved = self.store.get(task['task_id'])
        self.assertEqual([item['pr'] for item in saved['publish_operations']],
                         list(range(3, 11)))
        task_store_module._validate(saved)

        long_value = 'x' * (task_store_module._PUBLISH_OPERATION_VALUE_BYTES + 1)
        missing = operation(1)
        missing.pop('outcome')
        invalid_items = (
            ('not a dict', 'record'),
            ('missing field', missing),
            ('extra field', dict(operation(1), stderr='output')),
            ('long string', dict(operation(1), next_action=long_value)),
            ('long multibyte string', dict(operation(1), next_action='é' * 257)),
            ('float value', dict(operation(1), pr=1.5)),
            ('list value', dict(operation(1), recheck=['FAIL:list'])),
            ('dict value', dict(operation(1), observed_head={'sha': 'a' * 40})),
        )
        for label, item in invalid_items:
            with self.subTest(label):
                with self.assertRaises(LLMToolError) as caught:
                    self.store.record_publish_operation(task['task_id'], item)
                self.assertEqual(caught.exception.task_store_error_code,
                                 'TASK_PUBLISH_OPERATION_INVALID')
                malformed = dict(saved, publish_operations=[item])
                malformed['record_sha256'] = task_store_module._digest(malformed)
                with self.assertRaises(LLMToolError) as caught:
                    task_store_module._validate(malformed)
                self.assertEqual(caught.exception.task_store_error_code,
                                 'TASK_PUBLISH_OPERATION_INVALID')
        self.assertEqual(self.store.get(task['task_id'])['publish_operations'],
                         saved['publish_operations'])
        for label, value in (('too many', [operation(1)] * 9), ('not a list', {})):
            with self.subTest(label):
                malformed = dict(saved, publish_operations=value)
                malformed['record_sha256'] = task_store_module._digest(malformed)
                with self.assertRaises(LLMToolError) as caught:
                    task_store_module._validate(malformed)
                self.assertEqual(caught.exception.task_store_error_code,
                                 'TASK_PUBLISH_OPERATION_INVALID')
        exact = dict(operation(1), next_action='y' * task_store_module._PUBLISH_OPERATION_VALUE_BYTES)
        self.assertEqual(self.store.record_publish_operation(task['task_id'], exact), exact)

    def test_remote_test_receipt_accepts_missing_log_and_rejects_other_paths(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        base = {
            'result': 'FAIL', 'exit_code': 1, 'source_sha': 'a' * 40,
            'tree': 'b' * 40, 'fetch_head': 'a' * 40,
            'modules': ['test_task_store'], 'identity': {'uid': 65534},
            'started_at': '2026-10-27T00:00:00Z', 'ended_at': '2026-10-27T00:00:01Z',
            'elapsed_s': 1.0, 'ran_total': 1, 'final': ['FAILED (failures=1)'],
            'failures': ['test_task_store'], 'log_bytes': 1, 'log_sha256': 'c' * 64,
            'remote_log_path': None, 'task_id': task['task_id'],
            'run_id': task['run_id'], 'executor_task': 'd6-task',
            'pm_session': task['pm_session_id'], 'command_digest': 'd' * 64,
            'receipt_digest': 'e' * 64,
        }
        for path in (None, '/tmp/llmtool-remote-log.Abc123/remote-test.log'):
            receipt = {**base, 'remote_log_path': path}
            receipt['receipt_digest'] = hashlib.sha256(json.dumps(
                {key: value for key, value in receipt.items() if key != 'receipt_digest'},
                ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                allow_nan=False).encode() + b'\n').hexdigest()
            self.assertEqual(self.store.append_remote_test_receipt(task['task_id'], receipt),
                             receipt)
        invalid = {**base, 'remote_log_path': '/tmp/other.log'}
        invalid['receipt_digest'] = hashlib.sha256(json.dumps(
            {key: value for key, value in invalid.items() if key != 'receipt_digest'},
            ensure_ascii=False, sort_keys=True, separators=(',', ':'),
            allow_nan=False).encode() + b'\n').hexdigest()
        with self.assertRaises(LLMToolError):
            self.store.append_remote_test_receipt(task['task_id'], invalid)

    def _codex_dispatch_task(self):
        request = 'Bounded non-sensitive Codex proposal task.'
        task_id, run_id = new_id(), new_id()
        request_sha = hashlib.sha256(request.encode()).hexdigest()
        task = _record(
            task_id, run_id, 'pm-' + new_id(), 'pm-profile', request,
            state='PM_RESPONDED', provider_type='openai-compatible', model='pm-model',
            backend='NO_NATIVE_SESSION_ID_EXPOSED', request_sha=request_sha)
        task.update(
            pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                              'base_url': None},
            response='PM returned a bounded task.', error_code=None,
            pm_assignment={'schema_version': 1, 'route': 'codex', 'risk': 'LOW',
                           'goal': 'Produce a proposal diff.',
                           'acceptance': ['the proposal is recorded']},
        )
        return task

    @staticmethod
    def _developer_assignment(*, provider='codex', max_tokens=None):
        return {
            'assignment_id': new_id(), 'selection_source': 'PM',
            'state': 'DEVELOPER_PENDING', 'provider_profile': provider,
            'provider_type': None, 'model': None,
            'backend_session_semantics': 'NO_NATIVE_SESSION_ID_EXPOSED',
            'session_id': 'dev-' + new_id(), 'source_sha': 'a' * 40,
            'source_sha_binding': 'D6_REGISTRY_OBSERVED',
            'read_paths': ['src.py'], 'write_paths': ['src.py'],
            'acceptance': ['the proposal is recorded'],
            'context_sha256': 'b' * 64,
            'egress_basis': 'Approved non-sensitive test context.', 'input_bytes': 10,
            'max_tokens': max_tokens, 'artifact_format': 'unified_diff',
            'artifact': None, 'artifact_sha256': None, 'usage': None,
            'error_code': None, 'created_at': '2026-10-23T00:00:00Z',
        }

    def test_codex_delegate_developer_account_is_stored_as_name(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        developer = self._developer_assignment()
        developer['account'] = 'codex-developer'
        saved = self.store.compare_and_update(
            task['task_id'], expected={'developer': None}, developer=developer)
        self.assertEqual(saved['developer']['account'], 'codex-developer')
        self.assertNotIn('/private', repr(saved['developer']))

    def test_developer_account_rejects_path_like_and_invalid_names(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        for account in ('/private/codex-account', 'codex.account'):
            with self.subTest(account=account):
                developer = self._developer_assignment()
                developer['account'] = account
                with self.assertRaises(LLMToolError):
                    self.store.compare_and_update(
                        task['task_id'], expected={'developer': None},
                        developer=developer)

    def test_real_store_accepts_reviewer_accounts_and_preserves_them_on_transition(self):
        for account in ('claude-reviewer', 'codex-reviewer'):
            with self.subTest(account=account):
                task = self._codex_dispatch_task()
                self.store.create(task)
                claimed = {
                    'executor_task': 'executor-d6', 'pr': 42,
                    'head_sha': 'a' * 40, 'version': 1, 'state': 'CLAIMED',
                    'reviewer_task': 'reviewer-d6', 'native_session_id': None,
                    'account_mode': 'ROLES', 'account': account,
                    'claimed_at': '2026-10-27T00:00:00Z',
                    'updated_at': '2026-10-27T00:00:00Z',
                }
                initial = self.store.get(task['task_id'])
                self.store.compare_and_update(
                    task['task_id'], expected={'record_sha256': initial['record_sha256']},
                    review_dispatch_claim=[claimed])
                current = self.store.get(task['task_id'])
                dispatched = dict(claimed, state='DISPATCHED',
                                  native_session_id='native-' + account)
                changed_account = dict(dispatched, account='other-reviewer')
                with self.assertRaises(LLMToolError):
                    self.store.compare_and_update(
                        task['task_id'],
                        expected={'record_sha256': current['record_sha256']},
                        review_dispatch_claim=[changed_account])
                self.store.compare_and_update(
                    task['task_id'], expected={'record_sha256': current['record_sha256']},
                    review_dispatch_claim=[dispatched])
                self.assertEqual(
                    self.store.get(task['task_id'])['review_dispatch_claim'], [dispatched])

    def test_task_store_rejects_invalid_account_names(self):
        for account in ('/private/codex-account', 'codex.account'):
            with self.subTest(account=account):
                task = self._codex_dispatch_task()
                self.store.create(task)
                initial = self.store.get(task['task_id'])
                claim = {
                    'executor_task': 'executor-d6', 'pr': 42,
                    'head_sha': 'a' * 40, 'version': 1, 'state': 'CLAIMED',
                    'reviewer_task': 'reviewer-d6', 'native_session_id': None,
                    'account_mode': 'ROLES', 'account': account,
                    'claimed_at': '2026-10-27T00:00:00Z',
                    'updated_at': '2026-10-27T00:00:00Z',
                }
                with self.assertRaises(LLMToolError):
                    self.store.compare_and_update(
                        task['task_id'],
                        expected={'record_sha256': initial['record_sha256']},
                        review_dispatch_claim=[claim])

    def _claimed_review_task(self):
        """Create a task holding one CLAIMED review claim; return task and claim."""
        task = self._codex_dispatch_task()
        self.store.create(task)
        claimed = {
            'executor_task': 'executor-d6', 'pr': 42,
            'head_sha': 'a' * 40, 'version': 1, 'state': 'CLAIMED',
            'reviewer_task': 'reviewer-d6', 'native_session_id': None,
            'account_mode': 'CODEX_CLI',
            'claimed_at': '2026-10-27T00:00:00Z',
            'updated_at': '2026-10-27T00:00:00Z',
        }
        initial = self.store.get(task['task_id'])
        self.store.compare_and_update(
            task['task_id'], expected={'record_sha256': initial['record_sha256']},
            review_dispatch_claim=[claimed])
        return task, claimed

    def test_binding_mismatch_keys_are_stored_on_an_unknown_mismatch_claim(self):
        for keys in (['head'], ['task_id'], ['run_id'], ['task_id', 'run_id', 'head'],
                     ['head', 'task_id']):
            with self.subTest(keys=keys):
                task, claimed = self._claimed_review_task()
                current = self.store.get(task['task_id'])
                unknown = dict(claimed, state='UNKNOWN',
                               updated_at='2026-10-27T00:01:00Z',
                               failure_reason='VERDICT_BINDING_MISMATCH',
                               binding_mismatch_keys=keys)
                self.store.compare_and_update(
                    task['task_id'], expected={'record_sha256': current['record_sha256']},
                    review_dispatch_claim=[unknown])
                self.assertEqual(
                    self.store.get(task['task_id'])['review_dispatch_claim'], [unknown])

    def test_binding_mismatch_keys_are_refused_outside_their_one_allowed_shape(self):
        mismatch = {'failure_reason': 'VERDICT_BINDING_MISMATCH'}
        cases = {
            'empty list': dict(mismatch, binding_mismatch_keys=[]),
            'repeated key': dict(mismatch, binding_mismatch_keys=['head', 'head']),
            'unknown key': dict(mismatch, binding_mismatch_keys=['contract_digest']),
            'value not name': dict(mismatch, binding_mismatch_keys=['e' * 40]),
            'tuple': dict(mismatch, binding_mismatch_keys=('head',)),
            'string': dict(mismatch, binding_mismatch_keys='head'),
            'non-string item': dict(mismatch, binding_mismatch_keys=[1]),
            'other reason': {'failure_reason': 'REVIEW_BINDING_LINE_MISSING',
                             'binding_mismatch_keys': ['head']},
            'no reason': {'binding_mismatch_keys': ['head']},
        }
        for name, extra in cases.items():
            with self.subTest(case=name):
                task, claimed = self._claimed_review_task()
                current = self.store.get(task['task_id'])
                unknown = dict(claimed, state='UNKNOWN',
                               updated_at='2026-10-27T00:01:00Z', **extra)
                with self.assertRaises(LLMToolError) as raised:
                    self.store.compare_and_update(
                        task['task_id'],
                        expected={'record_sha256': current['record_sha256']},
                        review_dispatch_claim=[unknown])
                self.assertEqual(getattr(raised.exception, 'task_store_error_code', None),
                                 'REVIEW_DISPATCH_CLAIM_INVALID')
                self.assertEqual(
                    self.store.get(task['task_id'])['review_dispatch_claim'], [claimed])
        # Only an UNKNOWN claim may carry the field, even with the mismatch reason.
        task, claimed = self._claimed_review_task()
        current = self.store.get(task['task_id'])
        dispatched = dict(claimed, state='DISPATCHED', native_session_id='native-1',
                          failure_reason='VERDICT_BINDING_MISMATCH',
                          binding_mismatch_keys=['head'])
        with self.assertRaises(LLMToolError) as raised:
            self.store.compare_and_update(
                task['task_id'], expected={'record_sha256': current['record_sha256']},
                review_dispatch_claim=[dispatched])
        self.assertEqual(getattr(raised.exception, 'task_store_error_code', None),
                         'REVIEW_DISPATCH_CLAIM_INVALID')

    def _claude_result_task(self, pm_session_id=None):
        task = self._codex_dispatch_task()
        if pm_session_id is not None:
            task['pm_session_id'] = pm_session_id
        self.store.create(task)
        developer = self._developer_assignment(provider='claude')
        developer['owner_assignment_ref'] = (
            'https://github.com/davidcatcat/davidcattoolsproject/issues/289'
            '#issuecomment-1')
        developer['executor_task_id'] = 'executor-1'
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            self.store.compare_and_update(
                task['task_id'], expected={'developer': None}, developer=developer)
        return task

    def _claude_cli_task(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        registration = SimpleNamespace(
            task_id='executor-claude', root='/registered/root', common_dir='/repo/.git',
            branch='task-branch', result_sha='a' * 40, trusted_agent_id='claude-developer',
            session_ref='session-ref', native_session_id=None)
        developer = self._developer_assignment(provider='claude')
        developer.update(
            backend_session_semantics='CLI_REPORTED_SESSION_ID',
            owner_assignment_ref=('https://github.com/davidcatcat/davidcattoolsproject/issues/289'
                                  '#issuecomment-1'),
            executor_task_id=registration.task_id,
            execution=None,
            direct_result_callback=task_submission_cli_module._direct_result_callback(
                task, registration, 'a' * 40, test_policy='LOCAL'))
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            self.store.compare_and_update(
                task['task_id'], expected={'developer': None}, developer=developer)
        return task

    def test_claude_cli_record_lifecycle_and_record_only_result_refusal(self):
        task = self._claude_cli_task()
        developer = self.store.get(task['task_id'])['developer']
        self.assertEqual(developer['backend_session_semantics'], 'CLI_REPORTED_SESSION_ID')
        with self.assertRaises(LLMToolError) as caught:
            self.store.record_claude_result(
                task['task_id'], pm_session_id=task['pm_session_id'],
                executor_task_id='executor-claude', head_sha='a' * 40)
        self.assertEqual(caught.exception.task_store_error_code, 'CLAUDE_RESULT_STATE_INVALID')

        dispatching = dict(developer, state='DEVELOPER_DISPATCHING',
                           provider_type='claude-cli', model='claude-test-1',
                           execution=None)
        initial = self.store.get(task['task_id'])
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            self.store.compare_and_update(task['task_id'],
                expected={'record_sha256': initial['record_sha256']}, developer=dispatching)
        responded = dict(dispatching, state='DEVELOPER_RESPONDED',
            artifact='diff --git a/src.py b/src.py\n',
            artifact_sha256=hashlib.sha256(b'diff --git a/src.py b/src.py\n').hexdigest(),
            execution={'status': 'DONE', 'native_session_id': '12345678-1234-4234-8234-123456789abc',
                'process_exit_code': 0, 'stdout_sha256': 'c' * 64, 'elapsed_ms': 1,
                'cleanup_status': 'CONFIRMED', 'usage_source': 'UNKNOWN',
                'source_bytes': None, 'source_file_count': None,
                'source_manifest_sha256': None})
        current = self.store.get(task['task_id'])
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            self.store.compare_and_update(task['task_id'],
                expected={'record_sha256': current['record_sha256']}, developer=responded)
        self.assertEqual(self.store.get(task['task_id'])['developer']['state'],
                         'DEVELOPER_RESPONDED')

    def test_claude_cli_accepts_blocked_isolation_record(self):
        task = self._claude_cli_task()
        developer = self.store.get(task['task_id'])['developer']
        blocked = dict(developer, state='BLOCKED', provider_type='claude-cli',
                       model='claude-test-1', error_code='BLOCKED_WRITE_ISOLATION',
                       blocked_reason='BLOCKED_WRITE_ISOLATION', execution=None)
        current = self.store.get(task['task_id'])
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            saved = self.store.compare_and_update(task['task_id'],
                expected={'record_sha256': current['record_sha256']}, developer=blocked)
        self.assertEqual(saved['developer']['state'], 'BLOCKED')

    def test_claude_cli_rejects_mismatched_callback_executor(self):
        task = self._claude_cli_task()
        current = self.store.get(task['task_id'])
        developer = dict(current['developer'])
        developer['direct_result_callback'] = dict(
            developer['direct_result_callback'], executor_task_id='other-executor')
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            with self.assertRaises(LLMToolError):
                self.store.compare_and_update(task['task_id'],
                    expected={'record_sha256': current['record_sha256']}, developer=developer)

    def test_claude_cli_rejects_record_only_result_field(self):
        task = self._claude_cli_task()
        current = self.store.get(task['task_id'])
        developer = dict(current['developer'], claude_result={})
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            with self.assertRaises(LLMToolError):
                self.store.compare_and_update(task['task_id'],
                    expected={'record_sha256': current['record_sha256']}, developer=developer)

    def test_record_only_claude_rejects_cli_provider_type(self):
        task = self._claude_result_task()
        current = self.store.get(task['task_id'])
        developer = dict(current['developer'], provider_type='claude-cli')
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            with self.assertRaises(LLMToolError):
                self.store.compare_and_update(task['task_id'],
                    expected={'record_sha256': current['record_sha256']}, developer=developer)

    def test_claude_assignment_rejects_invalid_repository_config(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        developer = self._developer_assignment(provider='claude')
        developer['owner_assignment_ref'] = (
            'https://github.com/davidcatcat/davidcattoolsproject/issues/289'
            '#issuecomment-1')
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None), \
                patch.object(worker_dispatch, 'owner_comment_ref',
                             side_effect=LLMToolError('repository config is invalid')):
            with self.assertRaises(LLMToolError) as caught:
                self.store.compare_and_update(
                    task['task_id'], expected={'developer': None}, developer=developer)
        self.assertEqual(caught.exception.task_store_error_code,
                         'TASK_STORE_REPOSITORY_CONFIG_INVALID')

    def test_claude_result_is_pm_bound_single_pending_transition(self):
        task = self._claude_result_task()
        pm_session = task['pm_session_id']
        with self.assertRaises(LLMToolError):
            self.store.record_claude_result(
                task['task_id'], pm_session_id='pm-' + new_id(),
                executor_task_id='executor-1', head_sha='a' * 40)
        with self.assertRaises(LLMToolError):
            self.store.record_claude_result(
                task['task_id'], pm_session_id=pm_session,
                executor_task_id='executor-1', head_sha='bad')
        with self.assertRaises(LLMToolError):
            self.store.record_claude_result(
                task['task_id'], pm_session_id=pm_session,
                executor_task_id='different-executor', head_sha='a' * 40)
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            saved = self.store.record_claude_result(
                task['task_id'], pm_session_id=pm_session,
                executor_task_id='executor-1', head_sha='a' * 40)
        result = saved['developer']['claude_result']
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_RESPONDED')
        self.assertEqual(result['executor_task_id'], 'executor-1')
        self.assertEqual(result['head_sha'], 'a' * 40)
        self.assertRegex(result['recorded_at'], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
        with self.assertRaises(LLMToolError):
            self.store.record_claude_result(
                task['task_id'], pm_session_id=pm_session,
                executor_task_id='executor-1', head_sha='a' * 40)

    def test_claude_result_cli_requires_invoking_pm_session(self):
        task = self._claude_result_task()
        args = SimpleNamespace(task_id=task['task_id'], executor_task='executor-1',
                               head='a' * 40)
        with self.assertRaises(LLMToolError):
            task_submission_cli_module.run_task_claude_result(args, store=self.store)
        args.pm_session = 'pm-not-the-original'
        with self.assertRaises(LLMToolError):
            task_submission_cli_module.run_task_claude_result(args, store=self.store)
        args.pm_session = 'not a session'
        with self.assertRaises(LLMToolError):
            task_submission_cli_module.run_task_claude_result(args, store=self.store)
        self.assertEqual(self.store.get(task['task_id'])['developer']['state'],
                         'DEVELOPER_PENDING')
        args.pm_session = task['pm_session_id']
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            self.assertEqual(task_submission_cli_module.run_task_claude_result(
                args, store=self.store, stdout=io.StringIO()), 0)
        self.assertEqual(self.store.get(task['task_id'])['developer']['state'],
                         'DEVELOPER_RESPONDED')

        native_pm_session_id = str(uuid.uuid4())
        uuid_task = self._claude_result_task(pm_session_id='pm-' + native_pm_session_id)
        args.task_id = uuid_task['task_id']
        args.pm_session = native_pm_session_id
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            self.assertEqual(task_submission_cli_module.run_task_claude_result(
                args, store=self.store, stdout=io.StringIO()), 0)
        self.assertEqual(self.store.get(uuid_task['task_id'])['developer']['state'],
                         'DEVELOPER_RESPONDED')

    def test_claude_result_rejects_wrong_state(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        with self.assertRaises(LLMToolError):
            self.store.record_claude_result(
                task['task_id'], pm_session_id=task['pm_session_id'],
                executor_task_id='executor-1', head_sha='a' * 40)

    def test_developer_progress_is_optional_extension_for_all_direct_routes(self):
        artifact = ('diff --git a/src.py b/src.py\n'
                    '--- a/src.py\n+++ b/src.py\n@@ -1 +1 @@\n-x=1\n+x=2\n')
        execution = {
            'status': 'DONE', 'native_session_id': '12345678-1234-4234-8234-123456789abc',
            'process_exit_code': 0, 'stdout_sha256': 'c' * 64, 'elapsed_ms': 12,
            'cleanup_status': 'CONFIRMED', 'usage_source': 'UNKNOWN',
            'source_bytes': None, 'source_file_count': None,
            'source_manifest_sha256': None,
        }
        cases = (
            ('codex', 'DEVELOPER_PENDING', False, False),
            ('codex', 'DEVELOPER_RESPONDED', True, False),
            ('agy', 'DEVELOPER_RESPONDED', True, False),
            ('agy', 'BLOCKED', False, True),
        )
        for provider, state, with_execution, with_progress in cases:
            with self.subTest(provider=provider, state=state, progress=with_progress):
                task = self._codex_dispatch_task()
                self.store.create(task)
                developer = self._developer_assignment(provider=provider, max_tokens=None)
                developer.update(state=state,
                    provider_type=('agy-cli' if provider == 'agy' and state != 'DEVELOPER_PENDING'
                                   else 'codex-cli' if provider == 'codex' and state != 'DEVELOPER_PENDING'
                                   else None),
                    model=('gemini-test' if provider == 'agy' and state != 'DEVELOPER_PENDING'
                           else 'gpt-6-luna' if provider == 'codex' and state != 'DEVELOPER_PENDING'
                           else None),
                    backend_session_semantics=('CLI_REPORTED_SESSION_ID' if provider == 'agy'
                                               else 'NO_NATIVE_SESSION_ID_EXPOSED'),
                    error_code='AGY_QUOTA_EXHAUSTED' if with_progress else None)
                if provider == 'agy':
                    developer['owner_assignment_ref'] = (
                        'https://github.com/davidcatcat/davidcattoolsproject/issues/289'
                        '#issuecomment-1')
                developer['execution'] = execution if with_execution else None
                if with_progress:
                    developer['progress'] = {
                        'state': 'QUOTA_EXHAUSTED', 'head_sha': 'a' * 40,
                        'changed_files': ['src.py'], 'unfinished_checks': ['UNKNOWN'],
                        'session_id': 'conversation_12345678',
                    }
                if state == 'DEVELOPER_RESPONDED':
                    developer.update(artifact=artifact,
                        artifact_sha256=hashlib.sha256(artifact.encode()).hexdigest())
                saved = self.store.compare_and_update(
                    task['task_id'], expected={'developer': None}, developer=developer)
                self.assertEqual(saved['developer'].get('progress'),
                                 developer.get('progress'))
                self.assertEqual(saved['developer'].get('execution'),
                                 developer.get('execution'))

    def test_agy_owner_assignment_ref_may_be_none_but_must_be_valid(self):
        # #785 M18: AGY dispatch is decided by the live gate, so the store
        # accepts an AGY record whose ref is None. A supplied ref must still
        # be valid and the key stays required for AGY. Claude: next test.
        artifact = ('diff --git a/src.py b/src.py\n'
                    '--- a/src.py\n+++ b/src.py\n@@ -1 +1 @@\n-x=1\n+x=2\n')
        task = self._codex_dispatch_task()
        self.store.create(task)
        developer = self._developer_assignment(provider='agy', max_tokens=None)
        developer.update(
            state='DEVELOPER_RESPONDED', provider_type='agy-cli', model='gemini-test',
            backend_session_semantics='CLI_REPORTED_SESSION_ID', owner_assignment_ref=None,
            execution={
                'status': 'DONE', 'native_session_id': '12345678-1234-4234-8234-123456789abc',
                'process_exit_code': 0, 'stdout_sha256': 'c' * 64, 'elapsed_ms': 12,
                'cleanup_status': 'CONFIRMED', 'usage_source': 'UNKNOWN',
                'source_bytes': None, 'source_file_count': None,
                'source_manifest_sha256': None,
            },
            artifact=artifact, artifact_sha256=hashlib.sha256(artifact.encode()).hexdigest())
        saved = self.store.compare_and_update(
            task['task_id'], expected={'developer': None}, developer=developer)
        self.assertIn('owner_assignment_ref', saved['developer'])
        self.assertIsNone(saved['developer']['owner_assignment_ref'])
        task_store_module._validate(saved)

        missing_key = dict(saved['developer'])
        del missing_key['owner_assignment_ref']
        refused = (
            ('agy malformed ref', dict(saved['developer'],
                                       owner_assignment_ref='https://example.invalid/x')),
            ('agy non-string ref', dict(saved['developer'], owner_assignment_ref=7)),
            ('agy missing key', missing_key),
            ('codex None ref', dict(saved['developer'], provider_profile='codex',
                                    provider_type='codex-cli')),
        )
        for label, candidate in refused:
            with self.subTest(label):
                malformed = dict(saved, developer=candidate)
                malformed['record_sha256'] = task_store_module._digest(malformed)
                with self.assertRaises(LLMToolError):
                    task_store_module._validate(malformed)

    def test_claude_cli_owner_assignment_ref_may_be_none_but_must_be_valid(self):
        # #1502 WP-C: the PM-selected Claude Developer needs no --owner-ref, so
        # the store accepts its record with a None ref; a given ref stays checked.
        record = self.store.get(self._claude_cli_task()['task_id'])
        for ref, accepted in ((None, True), ('https://example.invalid/x', False)):
            with self.subTest(ref=ref):
                candidate = dict(record, developer=dict(record['developer'],
                                                        owner_assignment_ref=ref))
                candidate['record_sha256'] = task_store_module._digest(candidate)
                if accepted:
                    task_store_module._validate(candidate)
                else:
                    with self.assertRaises(LLMToolError):
                        task_store_module._validate(candidate)

    def test_agy_exit_nonzero_keeps_a_screened_exit_diagnostic(self):
        # #1606: an AGY_EXIT_NONZERO block may keep a bounded, screened
        # exit diagnostic; other codes and unscreened text stay refused.
        task = self._codex_dispatch_task()
        self.store.create(task)
        reason = 'exit_code=3 stderr_tail=fatal: model run failed'
        developer = self._developer_assignment(provider='agy', max_tokens=None)
        developer.update(
            state='BLOCKED', provider_type='agy-cli', model='gemini-test',
            backend_session_semantics='CLI_REPORTED_SESSION_ID', owner_assignment_ref=None,
            error_code='AGY_EXIT_NONZERO', blocked_reason=reason, execution=None)
        saved = self.store.compare_and_update(
            task['task_id'], expected={'developer': None}, developer=developer)
        self.assertEqual(saved['developer']['blocked_reason'], reason)
        cred = 'api_' + 'key'  # built here so the spec text holds no credential shape
        refused = (
            ('other code', dict(saved['developer'], error_code='AGY_MODEL_MISMATCH')),
            ('no exit prefix', dict(saved['developer'], blocked_reason='fatal')),
            ('too long', dict(saved['developer'],
                              blocked_reason='exit_code=3 ' + 'x' * 2048)),
            ('credential', dict(saved['developer'],
                                blocked_reason='exit_code=3 %s=secret-value' % cred)),
            ('control', dict(saved['developer'], blocked_reason='exit_code=3 bad\x01')),
        )
        for label, candidate in refused:
            with self.subTest(label):
                malformed = dict(saved, developer=candidate)
                malformed['record_sha256'] = task_store_module._digest(malformed)
                with self.assertRaises(LLMToolError):
                    task_store_module._validate(malformed)

    def test_non_claude_d6_binding_refuses_second_live_task_for_each_route(self):
        store = TaskStore(Path(self.temp.name).resolve())
        routes = ('codex', 'company-gw', 'opencode', 'agy', 'gw-agent')

        def create_task(provider, *, bind=False):
            task = self._codex_dispatch_task()
            store.create(task)
            if not bind:
                return task['task_id']
            developer = self._developer_assignment(provider=provider, max_tokens=None)
            if provider == 'opencode':
                # OpenCode assignments always carry an execution record, even
                # before a run has started.
                developer['execution'] = None
            if provider in ('opencode', 'agy', 'gw-agent'):
                developer['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
            if provider == 'agy':
                ref = ('https://github.com/davidcatcat/davidcattoolsproject/issues/289'
                       '#issuecomment-1')
                developer['owner_assignment_ref'] = ref
            completion = {
                'state': 'WAITING_EXECUTOR',
                'executor_task_id': executor_task_id,
                'reviewer_task_id': None, 'evidence': None, 'missing_gates': [],
                'pm_dispatch': 'NOT_STARTED', 'pm_response': None,
                'pm_recommendation': None, 'pm_usage': None, 'pm_error_code': None,
            }
            developer['executor_task_id'] = executor_task_id
            store.compare_and_update(
                task['task_id'], expected={'developer': None, 'completion': None},
                developer=developer, completion=completion)
            return task['task_id']

        for route in routes:
            with self.subTest(route=route):
                executor_task_id = 'executor-shared-' + route
                live_id = create_task(route, bind=True)
                candidate_id = create_task(route)
                pending = self._developer_assignment(provider=route, max_tokens=None)
                if route == 'opencode':
                    pending['execution'] = None
                if route in ('opencode', 'agy', 'gw-agent'):
                    pending['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
                if route == 'agy':
                    ref = ('https://github.com/davidcatcat/davidcattoolsproject/issues/289'
                           '#issuecomment-1')
                    pending['owner_assignment_ref'] = ref
                completion = {
                    'state': 'WAITING_EXECUTOR', 'executor_task_id': executor_task_id,
                    'reviewer_task_id': None, 'evidence': None, 'missing_gates': [],
                    'pm_dispatch': 'NOT_STARTED', 'pm_response': None,
                    'pm_recommendation': None, 'pm_usage': None, 'pm_error_code': None,
                }
                with self.assertRaises(LLMToolError) as caught:
                    store.d6_bind_compare_and_update(
                        candidate_id, executor_task_id=executor_task_id,
                        expected={'developer': None, 'completion': None},
                        developer=pending, completion=completion)
                self.assertEqual(caught.exception.task_store_error_code,
                                 'TASK_D6_ALREADY_BOUND')
                self.assertEqual(store.get(live_id)['developer']['state'],
                                 'DEVELOPER_PENDING')

    def test_non_claude_d6_binding_allows_finished_and_abandoned_tasks(self):
        store = TaskStore(Path(self.temp.name).resolve())
        executor_task_id = 'executor-repair-1'
        finished = self._codex_dispatch_task()
        store.create(finished)
        developer = self._developer_assignment(provider='codex', max_tokens=None)
        developer['executor_task_id'] = executor_task_id
        completion = {
            'state': 'WAITING_EXECUTOR', 'executor_task_id': executor_task_id,
            'reviewer_task_id': None, 'evidence': None, 'missing_gates': [],
            'pm_dispatch': 'NOT_STARTED', 'pm_response': None,
            'pm_recommendation': None, 'pm_usage': None, 'pm_error_code': None,
        }
        store.compare_and_update(finished['task_id'],
            expected={'developer': None, 'completion': None},
            developer=developer, completion=completion)
        dispatching = dict(developer, state='DEVELOPER_DISPATCHING',
                           provider_type='codex-cli', model='gpt-6-luna')
        store.update(finished['task_id'], developer=dispatching)
        artifact = ('diff --git a/src.py b/src.py\n--- a/src.py\n+++ b/src.py\n'
                    '@@ -1 +1 @@\n-x=1\n+x=2\n')
        responded = dict(dispatching, state='DEVELOPER_RESPONDED',
                         artifact=artifact,
                         artifact_sha256=hashlib.sha256(artifact.encode()).hexdigest())
        store.update(finished['task_id'], developer=responded)

        candidate = self._codex_dispatch_task()
        store.create(candidate)
        candidate_developer = self._developer_assignment(provider='codex', max_tokens=None)
        candidate_completion = dict(completion)
        saved = store.d6_bind_compare_and_update(
            candidate['task_id'], executor_task_id=executor_task_id,
            expected={'developer': None, 'completion': None},
            developer=candidate_developer, completion=candidate_completion)
        self.assertEqual(saved['completion']['executor_task_id'], executor_task_id)

        abandoned = self._codex_dispatch_task()
        store.create(abandoned)
        abandoned_developer = self._developer_assignment(provider='codex', max_tokens=None)
        abandoned_developer['executor_task_id'] = 'executor-abandoned-1'
        abandoned_completion = dict(completion, executor_task_id='executor-abandoned-1')
        store.compare_and_update(abandoned['task_id'],
            expected={'developer': None, 'completion': None},
            developer=abandoned_developer, completion=abandoned_completion)
        store.abandon(abandoned['task_id'], pm_session_id=abandoned['pm_session_id'],
                      reason='Task stopped before D6 work')
        after_abandonment = self._codex_dispatch_task()
        store.create(after_abandonment)
        allowed_developer = self._developer_assignment(provider='codex', max_tokens=None)
        allowed_completion = dict(completion, executor_task_id='executor-abandoned-1')
        saved = store.d6_bind_compare_and_update(
            after_abandonment['task_id'], executor_task_id='executor-abandoned-1',
            expected={'developer': None, 'completion': None},
            developer=allowed_developer, completion=allowed_completion)
        self.assertEqual(saved['completion']['executor_task_id'], 'executor-abandoned-1')

        abandoned_target = self._codex_dispatch_task()
        store.create(abandoned_target)
        store.abandon(abandoned_target['task_id'],
                      pm_session_id=abandoned_target['pm_session_id'],
                      reason='Target was abandoned before dispatch')
        with self.assertRaises(LLMToolError) as caught:
            store.d6_bind_compare_and_update(
                abandoned_target['task_id'], executor_task_id='executor-target-abandoned',
                expected={'developer': None, 'completion': None},
                developer=self._developer_assignment(provider='codex', max_tokens=None),
                completion=dict(completion, executor_task_id='executor-target-abandoned'))
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_D6_ALREADY_BOUND')
        self.assertIsNone(store.get(abandoned_target['task_id'])['developer'])

    def test_d6_binding_unknown_cleanup_and_callback_holders_are_live(self):
        store = TaskStore(Path(self.temp.name).resolve())
        executor_task_id = 'executor-unknown-cleanup'
        holder = self._codex_dispatch_task()
        store.create(holder)
        developer = self._developer_assignment(provider='codex', max_tokens=None)
        developer.update(state='DEVELOPER_OUTCOME_UNKNOWN',
                         provider_type='codex-cli',
                         error_code='CODEX_INTERRUPTED',
                         executor_task_id=executor_task_id,
                         execution={'status': 'UNKNOWN', 'native_session_id': None,
                            'process_exit_code': None, 'stdout_sha256': None,
                            'elapsed_ms': None, 'cleanup_status': 'UNKNOWN',
                            'usage_source': 'UNKNOWN', 'source_bytes': None,
                            'source_file_count': None, 'source_manifest_sha256': None})
        registration = SimpleNamespace(task_id=executor_task_id, root='/registered/root',
            common_dir='/repo/.git', branch='task-branch', result_sha='a' * 40,
            trusted_agent_id='codex-developer', session_ref='session-ref',
            native_session_id=None)
        developer['direct_result_callback'] = task_submission_cli_module._direct_result_callback(
            holder, registration, 'a' * 40, test_policy='LOCAL')
        completion = {'state': 'WAITING_EXECUTOR', 'executor_task_id': executor_task_id,
            'reviewer_task_id': None, 'evidence': None, 'missing_gates': [],
            'pm_dispatch': 'NOT_STARTED', 'pm_response': None,
            'pm_recommendation': None, 'pm_usage': None, 'pm_error_code': None}
        store.compare_and_update(holder['task_id'],
            expected={'developer': None, 'completion': None},
            developer=developer, completion=completion)

        candidate = self._codex_dispatch_task()
        store.create(candidate)
        pending = self._developer_assignment(provider='codex', max_tokens=None)
        with self.assertRaises(LLMToolError) as caught:
            store.d6_bind_compare_and_update(candidate['task_id'],
                executor_task_id=executor_task_id,
                expected={'developer': None, 'completion': None},
                developer=pending, completion=completion)
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_D6_ALREADY_BOUND')

        callback_only = dict(developer, executor_task_id='executor-other-bound')
        store.update(holder['task_id'], developer=callback_only,
                     completion=dict(completion, executor_task_id='executor-other-bound'))
        with self.assertRaises(LLMToolError) as caught:
            store.d6_bind_compare_and_update(candidate['task_id'],
                executor_task_id=executor_task_id,
                expected={'developer': None, 'completion': None},
                developer=pending, completion=completion)
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_D6_ALREADY_BOUND')

        confirmed = dict(developer, execution={
            'status': 'UNKNOWN', 'native_session_id': None,
            'process_exit_code': None, 'stdout_sha256': None, 'elapsed_ms': None,
            'cleanup_status': 'CONFIRMED', 'usage_source': 'UNKNOWN',
            'source_bytes': None, 'source_file_count': None,
            'source_manifest_sha256': None})
        store.update(holder['task_id'], developer=confirmed)
        candidate_completion = dict(completion, executor_task_id=executor_task_id)
        saved = store.d6_bind_compare_and_update(candidate['task_id'],
            executor_task_id=executor_task_id,
            expected={'developer': None, 'completion': None},
            developer=pending, completion=candidate_completion)
        self.assertEqual(saved['completion']['executor_task_id'], executor_task_id)

    def test_d6_binding_scan_failure_refuses_claim(self):
        store = TaskStore(Path(self.temp.name).resolve())
        candidate = self._codex_dispatch_task()
        store.create(candidate)
        with patch.object(store, 'task_ids', side_effect=OSError('scan unavailable')):
            with self.assertRaises(LLMToolError) as caught:
                store.d6_bind_compare_and_update(candidate['task_id'],
                    executor_task_id='executor-scan-failure',
                    expected={'developer': None, 'completion': None},
                    developer=self._developer_assignment(provider='codex', max_tokens=None),
                    completion={'state': 'WAITING_EXECUTOR',
                                'executor_task_id': 'executor-scan-failure',
                                'reviewer_task_id': None, 'evidence': None,
                                'missing_gates': [], 'pm_dispatch': 'NOT_STARTED',
                                'pm_response': None, 'pm_recommendation': None,
                                'pm_usage': None, 'pm_error_code': None})
        self.assertEqual(caught.exception.task_store_error_code,
                         'TASK_D6_BINDING_SCAN_FAILED')
        self.assertIsNone(store.get(candidate['task_id'])['developer'])

    def test_non_claude_concurrent_d6_binds_allow_exactly_one_winner(self):
        store = TaskStore(Path(self.temp.name).resolve())
        executor_task_id = 'executor-concurrent-1'
        candidates = [self._codex_dispatch_task() for _ in range(2)]
        for task in candidates:
            store.create(task)
        barrier = threading.Barrier(2)
        outcomes = []

        def bind(task):
            developer = self._developer_assignment(provider='codex', max_tokens=None)
            completion = {
                'state': 'WAITING_EXECUTOR', 'executor_task_id': executor_task_id,
                'reviewer_task_id': None, 'evidence': None, 'missing_gates': [],
                'pm_dispatch': 'NOT_STARTED', 'pm_response': None,
                'pm_recommendation': None, 'pm_usage': None, 'pm_error_code': None,
            }
            barrier.wait()
            try:
                store.d6_bind_compare_and_update(
                    task['task_id'], executor_task_id=executor_task_id,
                    expected={'developer': None, 'completion': None},
                    developer=developer, completion=completion)
                outcomes.append('BOUND')
            except LLMToolError as exc:
                outcomes.append(exc.task_store_error_code)

        threads = [threading.Thread(target=bind, args=(task,)) for task in candidates]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(outcomes.count('BOUND'), 1)
        self.assertEqual(outcomes.count('TASK_D6_ALREADY_BOUND'), 1)

    def test_d6_bound_refusal_names_the_code_and_the_holder_task(self):
        store = TaskStore(Path(self.temp.name).resolve())
        executor_task_id = 'executor-bound-message'
        holder, candidate = self._codex_dispatch_task(), self._codex_dispatch_task()
        store.create(holder)
        store.create(candidate)

        def bind(task):
            return store.d6_bind_compare_and_update(
                task['task_id'], executor_task_id=executor_task_id,
                expected={'developer': None, 'completion': None},
                developer=self._developer_assignment(provider='codex', max_tokens=None),
                completion=self._d6_completion(executor_task_id))

        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            bind(holder)
            with self.assertRaises(LLMToolError) as caught:
                bind(candidate)
        self.assertEqual(str(caught.exception),
                         'TASK_D6_ALREADY_BOUND: D6 is held by Task ' + holder['task_id'])
        self.assertEqual(caught.exception.task_store_code, 'INVALID')
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_D6_ALREADY_BOUND')

    def test_other_task_store_failures_keep_the_omitted_details_message(self):
        holder_id = new_id()
        cases = (
            ('TASK_D6_BINDING_SCAN_FAILED', holder_id),
            ('TASK_STORE_INVALID', None),
            ('TASK_D6_ALREADY_BOUND', None),
            ('TASK_D6_ALREADY_BOUND', '../private/path'),
        )
        for code, holder_task_id in cases:
            with self.subTest(code=code, holder_task_id=holder_task_id):
                with self.assertRaises(LLMToolError) as caught:
                    task_store_module._fail(code, holder_task_id=holder_task_id)
                self.assertEqual(str(caught.exception),
                                 'task store unavailable; details omitted')
                self.assertEqual(caught.exception.task_store_error_code, code)

    def test_d6_binding_with_many_finished_tasks_does_not_hold_lock_over_full_scan(self):
        store = TaskStore(Path(self.temp.name).resolve())
        finished_ids = set()
        artifact = ('diff --git a/src.py b/src.py\n--- a/src.py\n+++ b/src.py\n'
                    '@@ -1 +1 @@\n-x=1\n+x=2\n')
        artifact_sha = hashlib.sha256(artifact.encode()).hexdigest()
        for index in range(60):
            finished = self._codex_dispatch_task()
            store.create(finished)
            developer = self._developer_assignment(provider='codex', max_tokens=None)
            developer.update(
                state='DEVELOPER_RESPONDED', provider_type='codex-cli',
                model='gpt-6-luna', executor_task_id=f'executor-done-{index}',
                artifact=artifact, artifact_sha256=artifact_sha)
            completion = {
                'state': 'WAITING_EXECUTOR',
                'executor_task_id': f'executor-done-{index}',
                'reviewer_task_id': None, 'evidence': None, 'missing_gates': [],
                'pm_dispatch': 'NOT_STARTED', 'pm_response': None,
                'pm_recommendation': None, 'pm_usage': None, 'pm_error_code': None,
            }
            store.compare_and_update(
                finished['task_id'], expected={'developer': None, 'completion': None},
                developer=developer, completion=completion)
            finished_ids.add(finished['task_id'])

        candidates = [self._codex_dispatch_task() for _ in range(2)]
        for task in candidates:
            store.create(task)

        original_get = store.get
        original_lock = store._lock_sidecar
        original_unlock = store._unlock_sidecar
        lock_owner = [None]
        locked_finished_reads = []
        barrier = threading.Barrier(2)
        outcomes = []

        def tracked_lock(directory, lock_name, *, exclusive, code):
            fd = original_lock(directory, lock_name, exclusive=exclusive, code=code)
            if lock_name == '.claude-d6-binding.lock':
                lock_owner[0] = threading.get_ident()
            return fd

        def tracked_unlock(lock_fd):
            if lock_fd is not None:
                lock_owner[0] = None
            return original_unlock(lock_fd)

        unlocked_finished_reads = []

        def slow_finished_get(task_id):
            if task_id in finished_ids:
                if lock_owner[0] == threading.get_ident():
                    locked_finished_reads.append(task_id)
                else:
                    unlocked_finished_reads.append(task_id)
                time.sleep(0.005)
            return original_get(task_id)

        def bind(index, task):
            executor_task_id = f'executor-many-{index}'
            developer = self._developer_assignment(provider='codex', max_tokens=None)
            developer['executor_task_id'] = executor_task_id
            completion = {
                'state': 'WAITING_EXECUTOR', 'executor_task_id': executor_task_id,
                'reviewer_task_id': None, 'evidence': None, 'missing_gates': [],
                'pm_dispatch': 'NOT_STARTED', 'pm_response': None,
                'pm_recommendation': None, 'pm_usage': None, 'pm_error_code': None,
            }
            barrier.wait()
            try:
                store.d6_bind_compare_and_update(
                    task['task_id'], executor_task_id=executor_task_id,
                    expected={'developer': None, 'completion': None},
                    developer=developer, completion=completion)
                outcomes.append('BOUND')
            except LLMToolError as exc:
                outcomes.append(exc.task_store_error_code)

        # #1631: a 100 ms lock wait raced the locked commit on slow runners.
        # Keep the product lock timeout; the read lists below prove that the
        # scans read the finished Tasks and never under the binding lock.
        with patch.object(store, '_lock_sidecar', side_effect=tracked_lock), \
                patch.object(store, '_unlock_sidecar', side_effect=tracked_unlock), \
                patch.object(store, 'get', side_effect=slow_finished_get):
            threads = [threading.Thread(target=bind, args=(i, task))
                       for i, task in enumerate(candidates)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(locked_finished_reads, [])
        self.assertTrue(finished_ids <= set(unlocked_finished_reads))
        self.assertEqual(outcomes, ['BOUND', 'BOUND'])

        claude_task = self._codex_dispatch_task()
        store.create(claude_task)
        claude_dev = self._developer_assignment(provider='claude', max_tokens=None)
        claude_dev['owner_assignment_ref'] = (
            'https://github.com/davidcatcat/davidcattoolsproject/issues/289'
            '#issuecomment-1')
        claude_dev['executor_task_id'] = 'executor-done-0'
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            with self.assertRaises(LLMToolError) as caught:
                store.claude_bind_compare_and_update(
                    claude_task['task_id'], executor_task_id='executor-done-0',
                    expected={'developer': None}, developer=claude_dev)
            self.assertEqual(caught.exception.task_store_error_code,
                             'TASK_D6_ALREADY_BOUND')
            claude_dev['executor_task_id'] = 'executor-claude-fresh'
            saved = store.claude_bind_compare_and_update(
                claude_task['task_id'], executor_task_id='executor-claude-fresh',
                expected={'developer': None}, developer=claude_dev)
        self.assertEqual(saved['developer']['executor_task_id'], 'executor-claude-fresh')

    @staticmethod
    def _d6_completion(executor_task_id):
        return {
            'state': 'WAITING_EXECUTOR', 'executor_task_id': executor_task_id,
            'reviewer_task_id': None, 'evidence': None, 'missing_gates': [],
            'pm_dispatch': 'NOT_STARTED', 'pm_response': None,
            'pm_recommendation': None, 'pm_usage': None, 'pm_error_code': None,
        }

    def _d6_timed_out_task(self, store, executor_task_id):
        """A Codex Task that timed out with confirmed cleanup: not a live holder."""
        task = self._codex_dispatch_task()
        store.create(task)
        developer = self._developer_assignment(provider='codex', max_tokens=None)
        developer.update(
            state='DEVELOPER_OUTCOME_UNKNOWN', provider_type='codex-cli',
            error_code='CODEX_TIMEOUT', executor_task_id=executor_task_id,
            execution={'status': 'TIMEOUT', 'native_session_id': None,
                       'process_exit_code': None, 'stdout_sha256': None,
                       'elapsed_ms': 25, 'cleanup_status': 'CONFIRMED',
                       'usage_source': 'UNKNOWN', 'source_bytes': None,
                       'source_file_count': None, 'source_manifest_sha256': None})
        saved = store.compare_and_update(
            task['task_id'], expected={'developer': None, 'completion': None},
            developer=developer, completion=self._d6_completion(executor_task_id))
        self.assertFalse(store._d6_bind_holds(saved, executor_task_id))
        return task['task_id']

    def _d6_binder(self, store, kind, executor_task_id):
        """Prepare one way of gaining a D6; return a call that tries it."""
        if kind in ('continuation-bind', 'continuation-update'):
            task_id = self._d6_timed_out_task(store, executor_task_id)
            timed_out = store.get(task_id)
            continued = self._developer_assignment(provider='codex', max_tokens=None)
            continued['executor_task_id'] = executor_task_id
            if kind == 'continuation-bind':
                return lambda: store.d6_bind_compare_and_update(
                    task_id, executor_task_id=executor_task_id,
                    expected={'developer': timed_out['developer']}, developer=continued)
            return lambda: store.compare_and_update(
                task_id, expected={'record_sha256': timed_out['record_sha256']},
                developer=continued)
        if kind == 'create':
            task = self._codex_dispatch_task()
            developer = self._developer_assignment(provider='codex', max_tokens=None)
            developer['executor_task_id'] = executor_task_id
            task.update(developer=developer,
                        completion=self._d6_completion(executor_task_id))
            return lambda: store.create(task)
        task = self._codex_dispatch_task()
        store.create(task)
        if kind == 'claude':
            developer = self._developer_assignment(provider='claude', max_tokens=None)
            developer['owner_assignment_ref'] = (
                'https://github.com/davidcatcat/davidcattoolsproject/issues/289'
                '#issuecomment-1')
            developer['executor_task_id'] = executor_task_id
            return lambda: store.claude_bind_compare_and_update(
                task['task_id'], executor_task_id=executor_task_id,
                expected={'developer': None}, developer=developer)
        developer = self._developer_assignment(provider='codex', max_tokens=None)
        return lambda: store.d6_bind_compare_and_update(
            task['task_id'], executor_task_id=executor_task_id,
            expected={'developer': None, 'completion': None},
            developer=developer, completion=self._d6_completion(executor_task_id))

    @staticmethod
    def _d6_live_holders(store, executor_task_id):
        return [task_id for task_id in store.task_ids()
                if store._d6_bind_holds(store.get(task_id), executor_task_id)]

    def _run_d6_interleaved(self, store, first, second, *, before_lock=None):
        """Run ``second`` to completion after ``first`` scans but before it locks."""
        original_lock = store._lock_sidecar
        pending = [second]
        outcomes = {}

        def attempt(name, call):
            try:
                call()
                outcomes[name] = 'BOUND'
            except LLMToolError as exc:
                outcomes[name] = exc.task_store_error_code

        def racing_lock(directory, lock_name, *, exclusive, code):
            if exclusive and lock_name == task_store_module.D6_BINDING_LOCK_NAME and pending:
                attempt('second', pending.pop())
                if before_lock is not None:
                    before_lock()
            return original_lock(directory, lock_name, exclusive=exclusive, code=code)

        with patch.object(store, '_lock_sidecar', side_effect=racing_lock):
            attempt('first', first)
        self.assertEqual(pending, [])
        return outcomes

    def test_d6_interleaved_binders_never_commit_two_live_holders(self):
        """Each binder finishes its scan, then the other commits before it locks."""
        kinds = ('continuation-bind', 'continuation-update', 'fresh', 'claude', 'create')
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            for index, (first_kind, second_kind) in enumerate(
                    (first, second) for first in kinds for second in kinds):
                with self.subTest(first=first_kind, second=second_kind):
                    store = TaskStore(Path(self.temp.name).resolve() / f'race-{index}')
                    executor_task_id = f'executor-race-{index}'
                    first = self._d6_binder(store, first_kind, executor_task_id)
                    second = self._d6_binder(store, second_kind, executor_task_id)
                    outcomes = self._run_d6_interleaved(store, first, second)
                    self.assertEqual(sorted(outcomes.values()),
                                     ['BOUND', 'TASK_D6_ALREADY_BOUND'])
                    self.assertEqual(len(self._d6_live_holders(store, executor_task_id)), 1)

    def test_d6_timeout_continuation_refused_while_another_task_holds(self):
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            for kind in ('continuation-bind', 'continuation-update'):
                with self.subTest(kind=kind):
                    store = TaskStore(Path(self.temp.name).resolve() / kind)
                    executor_task_id = 'executor-continued'
                    continuation = self._d6_binder(store, kind, executor_task_id)
                    self._d6_binder(store, 'fresh', executor_task_id)()
                    with self.assertRaises(LLMToolError) as caught:
                        continuation()
                    self.assertEqual(caught.exception.task_store_error_code,
                                     'TASK_D6_ALREADY_BOUND')
                    self.assertEqual(len(self._d6_live_holders(store, executor_task_id)), 1)

    def test_d6_claude_binder_treats_live_callback_holder_as_bound(self):
        store = TaskStore(Path(self.temp.name).resolve())
        executor_task_id = 'executor-callback-held'
        holder = self._codex_dispatch_task()
        store.create(holder)
        developer = self._developer_assignment(provider='codex', max_tokens=None)
        developer.update(state='DEVELOPER_OUTCOME_UNKNOWN', provider_type='codex-cli',
                         error_code='CODEX_INTERRUPTED',
                         executor_task_id='executor-other-bound',
                         execution={'status': 'UNKNOWN', 'native_session_id': None,
                            'process_exit_code': None, 'stdout_sha256': None,
                            'elapsed_ms': None, 'cleanup_status': 'UNKNOWN',
                            'usage_source': 'UNKNOWN', 'source_bytes': None,
                            'source_file_count': None, 'source_manifest_sha256': None})
        registration = SimpleNamespace(task_id=executor_task_id, root='/registered/root',
            common_dir='/repo/.git', branch='task-branch', result_sha='a' * 40,
            trusted_agent_id='codex-developer', session_ref='session-ref',
            native_session_id=None)
        developer['direct_result_callback'] = task_submission_cli_module._direct_result_callback(
            holder, registration, 'a' * 40, test_policy='LOCAL')
        store.compare_and_update(holder['task_id'],
            expected={'developer': None, 'completion': None}, developer=developer,
            completion=self._d6_completion('executor-other-bound'))
        self.assertFalse(store._claude_d6_holds(store.get(holder['task_id']),
                                                executor_task_id))
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            with self.assertRaises(LLMToolError) as caught:
                self._d6_binder(store, 'claude', executor_task_id)()
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_D6_ALREADY_BOUND')

    def test_d6_journal_gap_forces_a_new_scan(self):
        store = TaskStore(Path(self.temp.name).resolve())
        executor_task_id = 'executor-gap'
        first = self._d6_binder(store, 'continuation-update', executor_task_id)
        second = self._d6_binder(store, 'fresh', executor_task_id)
        other = self._d6_binder(store, 'fresh', 'executor-gap-other')
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None), \
                patch.object(task_store_module, 'D6_BINDING_JOURNAL_MAX_ENTRIES', 1):
            # The other D6's claim pushes the same-D6 claim out of the journal.
            outcomes = self._run_d6_interleaved(store, first, second, before_lock=other)
        self.assertEqual(outcomes, {'second': 'BOUND', 'first': 'TASK_D6_ALREADY_BOUND'})
        self.assertEqual(len(self._d6_live_holders(store, executor_task_id)), 1)

    def test_d6_binding_read_failures_refuse(self):
        executor_task_id = 'executor-read-failure'
        with self.subTest(read='journal'):
            root = Path(self.temp.name).resolve() / 'journal'
            store = TaskStore(root)
            bind = self._d6_binder(store, 'fresh', executor_task_id)
            journal = root / task_store_module.D6_BINDING_JOURNAL_NAME
            journal.write_text('{"not": "a journal"}', encoding='utf-8')
            journal.chmod(0o600)
            with self.assertRaises(LLMToolError) as caught:
                bind()
            self.assertEqual(caught.exception.task_store_error_code,
                             'TASK_D6_BINDING_SCAN_FAILED')
            self.assertEqual(self._d6_live_holders(store, executor_task_id), [])

        with self.subTest(read='locked holder'):
            store = TaskStore(Path(self.temp.name).resolve() / 'locked')
            self._d6_binder(store, 'fresh', executor_task_id)()
            holder_id, = self._d6_live_holders(store, executor_task_id)
            bind = self._d6_binder(store, 'fresh', executor_task_id)
            original_get = store.get
            holder_reads = []

            def failing_get(task_id):
                if task_id == holder_id:
                    holder_reads.append(task_id)
                    if len(holder_reads) > 1:
                        raise OSError('holder unreadable')
                return original_get(task_id)

            with patch.object(store, 'get', side_effect=failing_get):
                with self.assertRaises(LLMToolError) as caught:
                    bind()
            self.assertEqual(caught.exception.task_store_error_code,
                             'TASK_D6_BINDING_SCAN_FAILED')
            self.assertEqual(len(holder_reads), 2)
            self.assertEqual(self._d6_live_holders(store, executor_task_id), [holder_id])

    def _stop_after_d6_journal_append(self, store, call):
        """Run ``call`` in a child SIGKILLed between its journal append and commit.

        Returns the Task ID the stopped writer left in the journal.
        """
        append = store._append_d6_journal_at

        def append_then_stop(*args):
            append(*args)
            os.kill(os.getpid(), signal.SIGKILL)

        pid = os.fork()
        if pid == 0:
            try:
                store._append_d6_journal_at = append_then_stop
                call()
            finally:
                os._exit(1)
        deadline = time.monotonic() + 10
        while True:
            finished, status = os.waitpid(pid, os.WNOHANG)
            if finished:
                break
            if time.monotonic() >= deadline:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
                self.fail('stopped writer never reached its journal append')
            time.sleep(0.01)
        self.assertTrue(os.WIFSIGNALED(status))
        self.assertEqual(os.WTERMSIG(status), signal.SIGKILL)
        journal = store.root / task_store_module.D6_BINDING_JOURNAL_NAME
        return json.loads(journal.read_text(encoding='utf-8'))['entries'][-1]['task_id']

    def test_d6_writer_stopped_before_its_commit_gains_nothing(self):
        """The D6 stays bindable by exactly one Task after the stop."""
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            for kind in ('create', 'fresh', 'continuation-update'):
                for timing in ('scan-before-stop', 'scan-after-stop'):
                    with self.subTest(stopped=kind, timing=timing):
                        store = TaskStore(Path(self.temp.name).resolve() /
                                          f'stopped-{kind}-{timing}')
                        executor_task_id = 'executor-stopped'
                        stopped = self._d6_binder(store, kind, executor_task_id)
                        binder = self._d6_binder(store, 'fresh', executor_task_id)

                        def stop():
                            stopped_id = self._stop_after_d6_journal_append(store, stopped)
                            self.assertEqual(stopped_id in store.task_ids(), kind != 'create')
                            self.assertEqual(self._d6_live_holders(store, executor_task_id), [])

                        if timing == 'scan-before-stop':
                            # The binder's epoch predates the stopped writer's
                            # entry, so it re-reads that Task under the lock.
                            outcomes = self._run_d6_interleaved(store, binder, stop)
                            self.assertEqual(outcomes['first'], 'BOUND')
                        else:
                            stop()
                            binder()
                        holder_id, = self._d6_live_holders(store, executor_task_id)
                        # The stopped writer's retry cannot become a second holder.
                        with self.assertRaises(LLMToolError) as caught:
                            stopped()
                        self.assertEqual(caught.exception.task_store_error_code,
                                         'TASK_D6_ALREADY_BOUND')
                        self.assertEqual(self._d6_live_holders(store, executor_task_id),
                                         [holder_id])

    def test_d6_retry_of_a_stopped_writer_holds_against_a_racing_binder(self):
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            for kind in ('create', 'fresh', 'continuation-update'):
                with self.subTest(stopped=kind):
                    store = TaskStore(Path(self.temp.name).resolve() / f'retried-{kind}')
                    executor_task_id = 'executor-retried'
                    stopped = self._d6_binder(store, kind, executor_task_id)
                    binder = self._d6_binder(store, 'fresh', executor_task_id)

                    def stop_then_retry():
                        self._stop_after_d6_journal_append(store, stopped)
                        stopped()

                    outcomes = self._run_d6_interleaved(store, binder, stop_then_retry)
                    self.assertEqual(outcomes, {'second': 'BOUND',
                                                'first': 'TASK_D6_ALREADY_BOUND'})
                    self.assertEqual(len(self._d6_live_holders(store, executor_task_id)), 1)

    def test_d6_only_a_journal_claim_with_no_record_is_skipped(self):
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            with self.subTest(read='stopped writer record unreadable'):
                root = Path(self.temp.name).resolve() / 'stopped-unreadable'
                store = TaskStore(root)
                executor_task_id = 'executor-stopped-unreadable'
                stopped = self._d6_binder(store, 'create', executor_task_id)
                binder = self._d6_binder(store, 'fresh', executor_task_id)

                def stop_then_corrupt():
                    stopped_id = self._stop_after_d6_journal_append(store, stopped)
                    corrupt = root / f'{stopped_id}.json'
                    corrupt.write_text('{"not": "a task"}', encoding='utf-8')
                    corrupt.chmod(0o600)

                outcomes = self._run_d6_interleaved(store, binder, stop_then_corrupt)
                self.assertEqual(outcomes['first'], 'TASK_D6_BINDING_SCAN_FAILED')

            with self.subTest(read='scan holder record gone'):
                root = Path(self.temp.name).resolve() / 'holder-gone'
                store = TaskStore(root)
                executor_task_id = 'executor-holder-gone'
                self._d6_binder(store, 'fresh', executor_task_id)()
                holder_id, = self._d6_live_holders(store, executor_task_id)
                binder = self._d6_binder(store, 'fresh', executor_task_id)
                outcomes = self._run_d6_interleaved(
                    store, binder, (root / f'{holder_id}.json').unlink)
                self.assertEqual(outcomes['first'], 'TASK_D6_BINDING_SCAN_FAILED')
                self.assertEqual(self._d6_live_holders(store, executor_task_id), [])

    def test_d6_binding_locked_work_does_not_grow_with_finished_tasks(self):
        def locked_work(name, finished_count):
            store = TaskStore(Path(self.temp.name).resolve() / name)
            executor_task_id = 'executor-hold-time'
            for _ in range(finished_count):
                # Finished on the same D6, so each one is a former holder.
                self._d6_timed_out_task(store, executor_task_id)
            bind = self._d6_binder(store, 'continuation-update', executor_task_id)
            original_lock = store._lock_sidecar
            original_unlock = store._unlock_sidecar
            original_read = store._read_at
            original_ids = store.task_ids
            held = [False]
            reads, listings = [], []

            def tracked_lock(directory, lock_name, *, exclusive, code):
                fd = original_lock(directory, lock_name, exclusive=exclusive, code=code)
                held[0] = exclusive and lock_name == task_store_module.D6_BINDING_LOCK_NAME
                return fd

            def tracked_unlock(lock_fd):
                held[0] = False
                return original_unlock(lock_fd)

            def tracked_read(directory, task_id):
                if held[0]:
                    reads.append(task_id)
                return original_read(directory, task_id)

            def tracked_ids():
                if held[0]:
                    listings.append(True)
                return original_ids()

            with patch.object(worker_dispatch, 'authorize_task_store_change',
                              return_value=None), \
                    patch.object(store, '_lock_sidecar', side_effect=tracked_lock), \
                    patch.object(store, '_unlock_sidecar', side_effect=tracked_unlock), \
                    patch.object(store, '_read_at', side_effect=tracked_read), \
                    patch.object(store, 'task_ids', side_effect=tracked_ids):
                bind()
            self.assertEqual(len(self._d6_live_holders(store, executor_task_id)), 1)
            self.assertEqual(listings, [])
            return len(reads), len(set(reads))

        self.assertEqual(locked_work('none-finished', 0), locked_work('many-finished', 40))

    @staticmethod
    def _track_binding_locks(store):
        """Patch ``store`` to record each D6 binding lock it takes."""
        original_lock = store._lock_sidecar
        taken = []

        def tracked_lock(directory, lock_name, *, exclusive, code):
            if lock_name == task_store_module.D6_BINDING_LOCK_NAME:
                taken.append(exclusive)
            return original_lock(directory, lock_name, exclusive=exclusive, code=code)
        return patch.object(store, '_lock_sidecar', side_effect=tracked_lock), taken

    def test_d6_repair_round_after_a_failed_run_still_binds(self):
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            for kind in ('continuation-bind', 'continuation-update', 'fresh'):
                with self.subTest(kind=kind):
                    store = TaskStore(Path(self.temp.name).resolve() / f'repair-{kind}')
                    executor_task_id = 'executor-repair-round'
                    # An earlier round on the same D6 failed with confirmed cleanup.
                    failed_id = self._d6_timed_out_task(store, executor_task_id)
                    repair = self._d6_binder(store, kind, executor_task_id)
                    tracker, taken = self._track_binding_locks(store)
                    with tracker:
                        repair()
                    holders = self._d6_live_holders(store, executor_task_id)
                    self.assertEqual(len(holders), 1)
                    self.assertNotIn(failed_id, holders)
                    self.assertIn(True, taken)

    def test_d6_owner_denial_is_audited_while_the_d6_is_held(self):
        owner_ref = ('https://github.com/davidcatcat/davidcattoolsproject/issues/289'
                     '#issuecomment-1234567890')
        policy = worker_policy_fixture.read(self.policy_path)
        policy['workers']['claude-developer'] = {
            'dispatch': 'OWNER_ASSIGNMENT_ONLY', 'decided_by': 'Owner',
            'decided_at': '2026-11-05', 'reason': 'test D6-bound assignment',
            'evidence': 'https://github.com/davidcatcat/davidcattoolsproject/issues/289',
            'owner_assignments': [{'task_id': 'e' * 32, 'owner_assignment_ref': owner_ref}],
        }
        policy_path = Path(self.temp.name) / 'worker-dispatch-claude-denied.json'
        policy_path.write_text(json.dumps(policy), encoding='utf-8')
        executor_task_id = 'f' * 32

        def denied_developer():
            developer = self._developer_assignment(provider='claude', max_tokens=None)
            developer.update(backend_session_semantics='CLI_REPORTED_SESSION_ID',
                             owner_assignment_ref=owner_ref,
                             executor_task_id=executor_task_id)
            return developer

        def claude_task():
            task = self._codex_dispatch_task()
            task['pm_assignment'] = dict(task['pm_assignment'], route='claude')
            return task

        for path in ('create', 'update'):
            with self.subTest(path=path):
                root = Path(self.temp.name).resolve() / f'denied-{path}'
                store = TaskStore(root)
                with patch.object(worker_dispatch, 'authorize_task_store_change',
                                  return_value=None):
                    self._d6_binder(store, 'fresh', executor_task_id)()
                holder_id, = self._d6_live_holders(store, executor_task_id)
                journal = root / task_store_module.D6_BINDING_JOURNAL_NAME
                journal_before = journal.read_bytes()
                task = claude_task()
                if path == 'update':
                    store.create(task)
                else:
                    task['developer'] = denied_developer()
                tracker, taken = self._track_binding_locks(store)
                with patch.object(worker_dispatch, '_POLICY_PATH', policy_path), tracker:
                    with self.assertRaises(worker_dispatch.WorkerDispatchDenied):
                        if path == 'create':
                            store.create(task)
                        else:
                            store.compare_and_update(task['task_id'],
                                expected={'developer': None}, developer=denied_developer())
                # Denied, so nothing was gained: the binding lock and journal
                # were never touched and the denial is on record.
                self.assertEqual(taken, [])
                self.assertEqual(journal.read_bytes(), journal_before)
                saved = store.get(task['task_id'])
                self.assertIsNone(saved['developer'])
                self.assertEqual(saved['dispatch_audit'][-1]['worker_key'], 'claude-developer')
                self.assertEqual(saved['dispatch_audit'][-1]['decision'], 'BLOCKED')
                self.assertEqual(self._d6_live_holders(store, executor_task_id), [holder_id])

    def test_d6_bind_target_that_can_never_bind_takes_no_binding_lock(self):
        root = Path(self.temp.name).resolve() / 'never-binds'
        store = TaskStore(root)
        executor_task_id = 'executor-never-binds'
        abandoned = self._codex_dispatch_task()
        store.create(abandoned)
        store.abandon(abandoned['task_id'], pm_session_id=abandoned['pm_session_id'],
                      reason='Target was abandoned before dispatch')
        stale = self._codex_dispatch_task()
        store.create(stale)
        claude_developer = self._developer_assignment(provider='claude', max_tokens=None)
        claude_developer.update(
            owner_assignment_ref=('https://github.com/davidcatcat/davidcattoolsproject/'
                                  'issues/289#issuecomment-1'),
            executor_task_id=executor_task_id)

        def d6_bind(task_id, expected):
            return store.d6_bind_compare_and_update(
                task_id, executor_task_id=executor_task_id,
                expected=dict(expected, completion=None),
                developer=self._developer_assignment(provider='codex', max_tokens=None),
                completion=self._d6_completion(executor_task_id))

        def claude_bind(task_id, expected):
            return store.claude_bind_compare_and_update(
                task_id, executor_task_id=executor_task_id, expected=expected,
                developer=claude_developer)

        stale_expected = {'developer': None, 'response': 'not the stored response'}
        cases = (
            ('abandoned-d6', d6_bind, abandoned, {'developer': None},
             'TASK_D6_ALREADY_BOUND'),
            ('abandoned-claude', claude_bind, abandoned, {'developer': None},
             'CLAUDE_TASK_ABANDONED'),
            ('stale-d6', d6_bind, stale, stale_expected, 'TASK_STORE_CONFLICT'),
            ('stale-claude', claude_bind, stale, stale_expected, 'TASK_STORE_CONFLICT'),
        )
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            for name, bind, target, expected, error_code in cases:
                with self.subTest(case=name):
                    tracker, taken = self._track_binding_locks(store)
                    with tracker, self.assertRaises(LLMToolError) as caught:
                        bind(target['task_id'], expected)
                    self.assertEqual(caught.exception.task_store_error_code, error_code)
                    self.assertEqual(taken, [])
                    self.assertIsNone(store.get(target['task_id'])['developer'])
        self.assertFalse((root / task_store_module.D6_BINDING_JOURNAL_NAME).exists())
        self.assertEqual(self._d6_live_holders(store, executor_task_id), [])

    @staticmethod
    def _d6_store_snapshot(store, task_id):
        journal = store.root / task_store_module.D6_BINDING_JOURNAL_NAME
        return store.get(task_id), journal.read_bytes() if journal.exists() else None

    def test_d6_forged_claim_is_refused_and_changes_nothing(self):
        self.assertNotIn('_d6_claims',
                         inspect.signature(TaskStore.compare_and_update).parameters)
        executor_task_id = 'executor-forged-claim'
        forged_claims = frozenset((executor_task_id,))
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            for held in (False, True):
                for key in ('_d6_claims', 'd6_claims'):
                    with self.subTest(held=held, key=key):
                        store = TaskStore(Path(self.temp.name).resolve() /
                                          f'forged-{key}-{held}')
                        if held:
                            self._d6_binder(store, 'fresh', executor_task_id)()
                        holders = self._d6_live_holders(store, executor_task_id)
                        task = self._codex_dispatch_task()
                        store.create(task)
                        before = self._d6_store_snapshot(store, task['task_id'])

                        def gain(**forged):
                            return store.compare_and_update(
                                task['task_id'],
                                expected={'developer': None, 'completion': None},
                                developer=self._developer_assignment(
                                    provider='codex', max_tokens=None),
                                completion=self._d6_completion(executor_task_id),
                                **forged)

                        tracker, taken = self._track_binding_locks(store)
                        with tracker, self.assertRaises(LLMToolError) as caught:
                            gain(**{key: forged_claims})
                        self.assertEqual(caught.exception.task_store_error_code,
                                         'TASK_STORE_INVALID')
                        self.assertEqual(taken, [])
                        self.assertEqual(self._d6_store_snapshot(store, task['task_id']),
                                         before)
                        self.assertEqual(self._d6_live_holders(store, executor_task_id),
                                         holders)

                        # The same change with no claim still takes the binding path.
                        tracker, taken = self._track_binding_locks(store)
                        if held:
                            with tracker, self.assertRaises(LLMToolError) as caught:
                                gain()
                            self.assertEqual(caught.exception.task_store_error_code,
                                             'TASK_D6_ALREADY_BOUND')
                            self.assertEqual(
                                self._d6_live_holders(store, executor_task_id), holders)
                        else:
                            with tracker:
                                gain()
                            journal = store.root / task_store_module.D6_BINDING_JOURNAL_NAME
                            entries = json.loads(journal.read_text(encoding='utf-8'))['entries']
                            self.assertEqual(entries[-1]['task_id'], task['task_id'])
                            self.assertEqual(self._d6_live_holders(store, executor_task_id),
                                             [task['task_id']])
                        self.assertIn(True, taken)

    def test_d6_forged_claim_leaves_no_dispatch_denial_audit(self):
        policy = worker_policy_fixture.read(self.policy_path)
        policy['workers']['claude-developer'] = {
            'dispatch': 'OWNER_ASSIGNMENT_ONLY', 'decided_by': 'Owner',
            'decided_at': '2026-11-05', 'reason': 'test forged D6 claim',
            'evidence': 'https://github.com/davidcatcat/davidcattoolsproject/issues/289',
            'owner_assignments': [],
        }
        policy_path = Path(self.temp.name) / 'worker-dispatch-forged-claim.json'
        policy_path.write_text(json.dumps(policy), encoding='utf-8')
        executor_task_id = 'executor-forged-denied'
        developer = self._developer_assignment(provider='claude', max_tokens=None)
        developer.update(backend_session_semantics='CLI_REPORTED_SESSION_ID',
                         owner_assignment_ref=(
                             'https://github.com/davidcatcat/davidcattoolsproject/'
                             'issues/289#issuecomment-1234567890'),
                         executor_task_id=executor_task_id)
        store = TaskStore(Path(self.temp.name).resolve() / 'forged-denied')
        task = self._codex_dispatch_task()
        task['pm_assignment'] = dict(task['pm_assignment'], route='claude')
        store.create(task)
        before = self._d6_store_snapshot(store, task['task_id'])
        forged = {'_d6_claims': frozenset((executor_task_id,))}
        calls = {
            'compare_and_update': lambda: store.compare_and_update(
                task['task_id'], expected={'developer': None},
                developer=developer, **forged),
            'update': lambda: store.update(task['task_id'], developer=developer, **forged),
        }
        with patch.object(worker_dispatch, '_POLICY_PATH', policy_path):
            for name, call in calls.items():
                with self.subTest(call=name):
                    with self.assertRaises(LLMToolError) as caught:
                        call()
                    self.assertEqual(caught.exception.task_store_error_code,
                                     'TASK_STORE_INVALID')
                    self.assertEqual(self._d6_store_snapshot(store, task['task_id']), before)

    def test_developer_reported_claims_are_bounded_and_strict(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        pending = self._developer_assignment(provider='codex', max_tokens=None)
        self.store.compare_and_update(task['task_id'], expected={'developer': None},
                                      developer=pending)
        valid_report = {'tests': ['claimed test'], 'summary': 'claimed summary',
                        'self_review': 'claimed review'}
        valid = {**pending, 'reported': valid_report}
        saved = self.store.update(task['task_id'], developer=valid)
        self.assertEqual(saved['developer']['reported'], valid_report)
        self.assertIsNone(saved.get('completion'))

        for reported in (
                {'other': 'field'}, {'summary': 4},
                {'summary': 'x' * (8 * 1024 + 1)},
                {'summary': 'x' * (4 * 1024), 'self_review': 'y' * (4 * 1024)},
                {'tests': ['x' * 1025]},
                {'tests': [''.join(('api', '_', 'key', '=', 'secret'))]},
                {'tests': ['ok'] * 33}, {}):
            with self.subTest(reported=repr(reported)[:32]):
                with self.assertRaises(LLMToolError):
                    self.store.update(task['task_id'],
                                      developer={**valid, 'reported': reported})

    def test_codex_cli_dispatch_and_response_allow_unknown_token_cap(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        pending = self._developer_assignment(provider='codex', max_tokens=None)
        self.store.compare_and_update(
            task['task_id'], expected={'developer': None}, developer=pending)

        dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                       'provider_type': 'codex-cli', 'model': 'gpt-6-luna'}
        self.store.update(task['task_id'], developer=dispatching)
        artifact = ('diff --git a/src.py b/src.py\n'
                    '--- a/src.py\n+++ b/src.py\n@@ -1 +1 @@\n-x=1\n+x=2\n')
        responded = {
            **dispatching, 'state': 'DEVELOPER_RESPONDED', 'artifact': artifact,
            'artifact_sha256': hashlib.sha256(artifact.encode()).hexdigest(),
        }
        saved = self.store.update(task['task_id'], developer=responded)
        self.assertEqual(saved['developer']['provider_profile'], 'codex')
        self.assertEqual(saved['developer']['provider_type'], 'codex-cli')
        self.assertIsNone(saved['developer']['max_tokens'])
        self.assertIsNone(saved['developer']['usage'])
        before_clear = saved
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
            self.store.update(task['task_id'], developer=None)
        self.assertEqual(caught.exception.dispatch_code,
                         'WORKER_ASSIGNMENT_CLEAR_FORBIDDEN')
        after_clear = self.store.get(task['task_id'])
        self.assertEqual(after_clear['developer'], before_clear['developer'])
        self.assertEqual(len(after_clear['dispatch_audit']),
                         len(before_clear['dispatch_audit']) + 1)
        refusal = after_clear['dispatch_audit'][-1]
        self.assertEqual(refusal['worker_key'], 'codex-developer')
        self.assertEqual(refusal['action'], 'REASSIGN')
        self.assertEqual(refusal['decision'], 'BLOCKED')
        self.assertEqual(refusal['reason_code'], 'WORKER_ASSIGNMENT_CLEAR_FORBIDDEN')
        self.assertRegex(refusal['policy_sha256'], r'^[0-9a-f]{64}$')

    def test_codex_child_identity_is_written_only_by_trusted_launch_path(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        pending = self._developer_assignment(provider='codex', max_tokens=None)
        self.store.compare_and_update(
            task['task_id'], expected={'developer': None}, developer=pending)
        dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                       'provider_type': 'codex-cli', 'model': 'gpt-6-luna'}
        self.store.update(task['task_id'], developer=dispatching)
        launch = {**dispatching, 'execution': {
            'status': 'RUNNING', 'native_session_id': None,
            'process_exit_code': None, 'stdout_sha256': None, 'elapsed_ms': None,
            'cleanup_status': 'UNKNOWN', 'usage_source': 'UNKNOWN',
            'source_bytes': None, 'source_file_count': None,
            'source_manifest_sha256': None, 'child_pid': 123,
            'child_start_time': '123456', 'child_pgid': 123}}
        with self.assertRaises(LLMToolError):
            self.store.update(task['task_id'], developer=launch)
        saved = self.store.record_codex_launch(
            task['task_id'], assignment_id=pending['assignment_id'], pid=123,
            start_time='123456', pgid=123)
        self.assertEqual(saved['developer']['execution'], launch['execution'])

    def test_opencode_child_identity_is_profile_checked_and_trusted(self):
        task = self._codex_dispatch_task()
        task['pm_assignment']['route'] = 'opencode'
        store = TaskStore(Path(self.temp.name).resolve() / 'opencode-tasks')
        store.create(task)
        pending = self._developer_assignment(provider='opencode', max_tokens=4096)
        pending['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
        pending['execution'] = None
        store.compare_and_update(
            task['task_id'], expected={'developer': None}, developer=pending)
        dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                       'provider_type': 'opencode-cli', 'model': 'opencode/test-free'}
        store.update(task['task_id'], developer=dispatching)
        launch = {**dispatching, 'execution': {
            'status': 'RUNNING', 'native_session_id': None,
            'process_exit_code': None, 'stdout_sha256': None, 'elapsed_ms': None,
            'cleanup_status': 'UNKNOWN', 'usage_source': 'UNKNOWN',
            'source_bytes': None, 'source_file_count': None,
            'source_manifest_sha256': None, 'child_pid': 123,
            'child_start_time': '123456', 'child_pgid': 123}}
        with self.assertRaises(LLMToolError):
            store.update(task['task_id'], developer=launch)
        with patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None):
            saved = store.record_opencode_launch(
                task['task_id'], assignment_id=pending['assignment_id'], pid=123,
                start_time='123456', pgid=123)
        self.assertEqual(saved['developer']['execution'], launch['execution'])
        with self.assertRaises(LLMToolError):
            store.record_opencode_launch(
                task['task_id'], assignment_id=pending['assignment_id'], pid=124,
                start_time='123457', pgid=124)

    def test_codex_direct_success_persists_native_thread_and_usage(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        pending = self._developer_assignment(provider='codex', max_tokens=None)
        pending['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
        self.store.compare_and_update(
            task['task_id'], expected={'developer': None}, developer=pending)
        dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                       'provider_type': 'codex-cli', 'model': 'gpt-6-luna'}
        self.store.update(task['task_id'], developer=dispatching)
        artifact = ('diff --git a/src.py b/src.py\n'
                    '--- a/src.py\n+++ b/src.py\n@@ -1 +1 @@\n-x=1\n+x=2\n')
        execution = {'status': 'DONE',
            'native_session_id': '12345678-1234-4234-8234-123456789abc',
            'process_exit_code': 0, 'stdout_sha256': 'c' * 64, 'elapsed_ms': 12,
            'cleanup_status': 'CONFIRMED', 'usage_source': 'CLI_REPORTED',
            'source_bytes': None, 'source_file_count': None,
            'source_manifest_sha256': None, 'native_session_binding': 'BOUND'}
        responded = {**dispatching, 'state': 'DEVELOPER_RESPONDED',
            'artifact': artifact, 'artifact_sha256': hashlib.sha256(artifact.encode()).hexdigest(),
            'execution': execution,
            'usage': {'prompt_tokens': 13, 'completion_tokens': 8, 'total_tokens': 21}}
        saved = self.store.update(task['task_id'], developer=responded)
        self.assertEqual(saved['developer']['execution']['status'], 'DONE')
        self.assertEqual(saved['developer']['execution']['native_session_id'],
                         execution['native_session_id'])
        self.assertEqual(saved['developer']['execution']['native_session_binding'], 'BOUND')
        self.assertEqual(saved['developer']['usage']['total_tokens'], 21)

    def test_native_session_binding_rejected_for_opencode_and_invalid_values(self):
        artifact = ('diff --git a/src.py b/src.py\n'
                    '--- a/src.py\n+++ b/src.py\n@@ -1 +1 @@\n-x=1\n+x=2\n')
        opencode_execution = {
            'status': 'SUCCEEDED', 'native_session_id': 'ses_abcdefgh12345678',
            'process_exit_code': 0, 'stdout_sha256': 'c' * 64,
            'elapsed_ms': 12, 'cleanup_status': 'CONFIRMED',
            'usage_source': 'UNKNOWN', 'source_bytes': 10,
            'source_file_count': 1, 'source_manifest_sha256': 'd' * 64,
        }
        task = self._codex_dispatch_task()
        self.store.create(task)
        pending = self._developer_assignment(provider='opencode', max_tokens=128)
        pending['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
        pending['execution'] = None
        self.store.compare_and_update(
            task['task_id'], expected={'developer': None}, developer=pending)
        dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                       'provider_type': 'opencode-cli', 'model': None}
        self.store.update(task['task_id'], developer=dispatching)
        responded = {
            **dispatching, 'state': 'DEVELOPER_RESPONDED', 'artifact': artifact,
            'artifact_sha256': hashlib.sha256(artifact.encode()).hexdigest(),
            'execution': {**opencode_execution, 'native_session_binding': 'BOUND'},
        }
        with self.assertRaises(LLMToolError):
            self.store.update(task['task_id'], developer=responded)

        for invalid_binding in ('bound', 'B' * 65, 7):
            with self.subTest(invalid_binding=repr(invalid_binding)):
                task = self._codex_dispatch_task()
                self.store.create(task)
                pending = self._developer_assignment(provider='codex', max_tokens=None)
                pending['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
                self.store.compare_and_update(
                    task['task_id'], expected={'developer': None}, developer=pending)
                dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                               'provider_type': 'codex-cli', 'model': 'gpt-6-luna'}
                self.store.update(task['task_id'], developer=dispatching)
                invalid = {
                    **dispatching, 'state': 'DEVELOPER_RESPONDED', 'artifact': artifact,
                    'artifact_sha256': hashlib.sha256(artifact.encode()).hexdigest(),
                    'execution': {
                        'status': 'DONE',
                        'native_session_id': '12345678-1234-4234-8234-123456789abc',
                        'process_exit_code': 0, 'stdout_sha256': 'c' * 64,
                        'elapsed_ms': 12, 'cleanup_status': 'CONFIRMED',
                        'usage_source': 'CLI_REPORTED', 'source_bytes': None,
                        'source_file_count': None, 'source_manifest_sha256': None,
                        'native_session_binding': invalid_binding,
                    },
                }
                with self.assertRaises(LLMToolError):
                    self.store.update(task['task_id'], developer=invalid)

        task = self._codex_dispatch_task()
        self.store.create(task)
        pending = self._developer_assignment(provider='codex', max_tokens=None)
        pending['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
        self.store.compare_and_update(
            task['task_id'], expected={'developer': None}, developer=pending)
        dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                       'provider_type': 'codex-cli', 'model': 'gpt-6-luna'}
        self.store.update(task['task_id'], developer=dispatching)
        invalid = {
            **dispatching, 'state': 'DEVELOPER_RESPONDED', 'artifact': artifact,
            'artifact_sha256': hashlib.sha256(artifact.encode()).hexdigest(),
            'execution': {
                'status': 'DONE',
                'native_session_id': '12345678-1234-4234-8234-123456789abc',
                'process_exit_code': 0, 'stdout_sha256': 'c' * 64,
                'elapsed_ms': 12, 'cleanup_status': 'CONFIRMED',
                'usage_source': 'CLI_REPORTED', 'source_bytes': None,
                'source_file_count': None, 'source_manifest_sha256': None,
                'unexpected_execution_key': 'rejected',
            },
        }
        with self.assertRaises(LLMToolError):
            self.store.update(task['task_id'], developer=invalid)

    def test_codex_blocked_and_timeout_outcomes_persist_reported_usage(self):
        usage = {'prompt_tokens': 13, 'completion_tokens': 8, 'total_tokens': 21}
        for status, error_code, process_exit in (
                ('BLOCKED', 'CODEX_BLOCKED', 0), ('TIMEOUT', 'CODEX_TIMEOUT', None)):
            with self.subTest(status=status):
                task = self._codex_dispatch_task()
                self.store.create(task)
                pending = self._developer_assignment(provider='codex', max_tokens=None)
                pending['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
                self.store.compare_and_update(
                    task['task_id'], expected={'developer': None}, developer=pending)
                dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                    'provider_type': 'codex-cli', 'model': 'gpt-6-luna'}
                self.store.update(task['task_id'], developer=dispatching)
                execution = {'status': status,
                    'native_session_id': '12345678-1234-4234-8234-123456789abc',
                    'process_exit_code': process_exit, 'stdout_sha256': 'd' * 64,
                    'elapsed_ms': 25, 'cleanup_status': 'CONFIRMED',
                    'usage_source': 'CLI_REPORTED', 'source_bytes': None,
                    'source_file_count': None, 'source_manifest_sha256': None}
                outcome = {**dispatching, 'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                    'error_code': error_code, 'execution': execution, 'usage': usage}
                if status == 'BLOCKED':
                    outcome['blocked_reason'] = 'The requested change cannot be made safely.'
                saved = self.store.update(task['task_id'], developer=outcome)
                self.assertEqual(saved['developer']['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
                self.assertEqual(saved['developer']['execution']['status'], status)
                self.assertEqual(saved['developer']['usage'], usage)
                if status == 'BLOCKED':
                    self.assertEqual(saved['developer']['blocked_reason'],
                                     'The requested change cannot be made safely.')
                    self.assertEqual(_task_view(saved)['developer']['blocked_reason'],
                                     saved['developer']['blocked_reason'])

    def test_blocked_reason_must_be_bounded_screened_and_bound_to_codex_blocked(self):
        for reason in ('x' * 2049, 'api_key=secret-value', 'bad\x01reason'):
            with self.subTest(reason=reason[:20]):
                task = self._codex_dispatch_task()
                self.store.create(task)
                pending = self._developer_assignment(provider='codex', max_tokens=None)
                pending['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
                self.store.compare_and_update(
                    task['task_id'], expected={'developer': None}, developer=pending)
                dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                    'provider_type': 'codex-cli', 'model': 'gpt-6-luna'}
                self.store.update(task['task_id'], developer=dispatching)
                invalid = {**dispatching, 'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                    'provider_type': 'codex-cli', 'error_code': 'CODEX_BLOCKED',
                    'execution': {'status': 'BLOCKED',
                        'native_session_id': '12345678-1234-4234-8234-123456789abc',
                        'process_exit_code': 0, 'stdout_sha256': 'd' * 64,
                        'elapsed_ms': 25, 'cleanup_status': 'CONFIRMED',
                        'usage_source': 'UNKNOWN', 'source_bytes': None,
                        'source_file_count': None, 'source_manifest_sha256': None},
                    'blocked_reason': reason}
                with self.assertRaises(LLMToolError):
                    self.store.update(task['task_id'], developer=invalid)

        task = self._codex_dispatch_task()
        self.store.create(task)
        pending = self._developer_assignment(provider='codex', max_tokens=None)
        pending['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
        self.store.compare_and_update(
            task['task_id'], expected={'developer': None}, developer=pending)
        dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
            'provider_type': 'codex-cli', 'model': 'gpt-6-luna'}
        self.store.update(task['task_id'], developer=dispatching)
        not_blocked = {**dispatching, 'state': 'DEVELOPER_OUTCOME_UNKNOWN',
            'provider_type': 'codex-cli', 'error_code': 'CODEX_TIMEOUT',
            'execution': {'status': 'TIMEOUT',
                'native_session_id': '12345678-1234-4234-8234-123456789abc',
                'process_exit_code': None, 'stdout_sha256': 'd' * 64,
                'elapsed_ms': 25, 'cleanup_status': 'CONFIRMED',
                'usage_source': 'UNKNOWN', 'source_bytes': None,
                'source_file_count': None, 'source_manifest_sha256': None},
            'blocked_reason': 'must not be attached to an unknown outcome'}
        with self.assertRaises(LLMToolError):
            self.store.update(task['task_id'], developer=not_blocked)

    def test_blocked_reason_is_rejected_for_every_non_blocked_codex_outcome(self):
        artifact = ('diff --git a/src.py b/src.py\n'
                    '--- a/src.py\n+++ b/src.py\n@@ -1 +1 @@\n-x=1\n+x=2\n')
        outcomes = (
            ('DEVELOPER_RESPONDED', None, None, artifact, None),
            ('DEVELOPER_OUTCOME_UNKNOWN', 'CODEX_RESULT_EVIDENCE_UNKNOWN',
             'UNKNOWN', None, None),
            ('DEVELOPER_OUTCOME_UNKNOWN', 'CODEX_RESULT_PERSISTENCE_REJECTED',
             'UNKNOWN', None, None),
            ('DEVELOPER_OUTPUT_REJECTED', 'DEVELOPER_OUTPUT_REJECTED',
             None, None, None),
        )
        for state, error_code, execution_status, result_artifact, usage in outcomes:
            with self.subTest(state=state, error_code=error_code):
                task = self._codex_dispatch_task()
                self.store.create(task)
                pending = self._developer_assignment(provider='codex', max_tokens=None)
                pending['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
                self.store.compare_and_update(
                    task['task_id'], expected={'developer': None}, developer=pending)
                dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                    'provider_type': 'codex-cli', 'model': 'gpt-6-luna'}
                self.store.update(task['task_id'], developer=dispatching)
                outcome = {**dispatching, 'state': state,
                    'error_code': error_code, 'artifact': result_artifact,
                    'artifact_sha256': (hashlib.sha256(result_artifact.encode()).hexdigest()
                                        if result_artifact is not None else None),
                    'usage': usage,
                    'blocked_reason': 'A bounded reason belongs only to CODEX_BLOCKED.'}
                if execution_status is not None:
                    outcome['execution'] = {'status': execution_status,
                        'native_session_id': '12345678-1234-4234-8234-123456789abc',
                        'process_exit_code': None,
                        'stdout_sha256': 'd' * 64, 'elapsed_ms': 25,
                        'cleanup_status': 'CONFIRMED', 'usage_source': 'UNKNOWN',
                        'source_bytes': None, 'source_file_count': None,
                        'source_manifest_sha256': None}
                with self.assertRaises(LLMToolError):
                    self.store.update(task['task_id'], developer=outcome)

    def test_rejection_detail_is_restricted_to_rejected_output_and_known_shape(self):
        def persist(detail):
            task = self._codex_dispatch_task()
            self.store.create(task)
            pending = self._developer_assignment(provider='codex', max_tokens=None)
            pending['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
            self.store.compare_and_update(
                task['task_id'], expected={'developer': None}, developer=pending)
            dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                'provider_type': 'codex-cli', 'model': 'gpt-6-luna'}
            self.store.update(task['task_id'], developer=dispatching)
            outcome = {**dispatching, 'state': 'DEVELOPER_OUTPUT_REJECTED',
                'error_code': 'DEVELOPER_OUTPUT_INVALID_DIFF', 'artifact': None,
                'artifact_sha256': None, 'usage': None,
                'rejection_detail': detail}
            return task, outcome

        task, outcome = persist({'reason': 'HUNK_HEADER_INVALID', 'hunk': 0})
        self.store.update(task['task_id'], developer=outcome)
        self.assertEqual(self.store.get(task['task_id'])['developer']['rejection_detail'],
                         {'reason': 'HUNK_HEADER_INVALID', 'hunk': 0})

        invalid_details = (
            {'reason': 'NOT_A_REASON', 'hunk': 0},
            {'reason': 'HUNK_COUNTS_RECOUNTED', 'hunk': 0},
            {'reason': 'STRICT_PARSE', 'hunk': -1},
            {'reason': 'STRICT_PARSE', 'hunk': True},
            {'reason': 'STRICT_PARSE', 'hunk': None, 'extra': 'value'},
        )
        for detail in invalid_details:
            task, outcome = persist(detail)
            with self.subTest(detail=detail), self.assertRaises(LLMToolError):
                self.store.update(task['task_id'], developer=outcome)

        task, outcome = persist({'reason': 'STRICT_PARSE', 'hunk': None})
        outcome['state'] = 'DEVELOPER_OUTCOME_UNKNOWN'
        outcome['error_code'] = 'CODEX_TIMEOUT'
        outcome['execution'] = {'status': 'UNKNOWN',
            'native_session_id': '12345678-1234-4234-8234-123456789abc',
            'process_exit_code': None, 'stdout_sha256': 'd' * 64,
            'elapsed_ms': 25, 'cleanup_status': 'CONFIRMED',
            'usage_source': 'UNKNOWN', 'source_bytes': None,
            'source_file_count': None, 'source_manifest_sha256': None}
        with self.assertRaises(LLMToolError):
            self.store.update(task['task_id'], developer=outcome)

    def test_codex_delegate_screens_blocked_reason_before_persisting(self):
        root = str(Path.cwd())
        snapshot = SimpleNamespace(root=root, common_dir='/registered/common',
                                   branch='task-branch', head='a' * 40)
        registration = SimpleNamespace(
            task_id='executor-task', trusted_agent_id='agent',
            session_ref='session-ref', native_session_id=None, root=root,
            common_dir=snapshot.common_dir, branch=snapshot.branch,
            env2_pass_marker='pass')
        args = SimpleNamespace(
            task_id=None, selection_source='USER', context_stdin=False,
            executor_task='executor-task', read_path=['src.py'],
            write_path=['src.py'], acceptance=['the outcome is recorded'],
            egress_basis='Approved non-sensitive test context.')

        cases = (
            ('bad\x01reason', 'CODEX_BLOCKED', None, 'BLOCKED', False),
            ('api_key=secret-value', 'CODEX_BLOCKED', None, 'BLOCKED', False),
            ('The requested change cannot be made safely.', 'CODEX_BLOCKED',
             'The requested change cannot be made safely.', 'BLOCKED', False),
            ('The requested change cannot be made safely.',
             'CODEX_RESULT_EVIDENCE_UNKNOWN', None, 'UNKNOWN', True),
        )
        for reason, expected_code, expected_reason, expected_execution, evidence_failure in cases:
            with self.subTest(reason=reason[:20]):
                task = self._codex_dispatch_task()
                self.store.create(task)
                args.task_id = task['task_id']
                result = SimpleNamespace(
                    status='BLOCKED', blocked_reason=reason,
                    thread_id='12345678-1234-4234-8234-123456789abc',
                    exit_code=0, stdout_sha256='d' * 64, elapsed_ms=25,
                    cleanup_status='CONFIRMED', usage=None)
                with patch.object(task_submission_cli_module.task_completion,
                                  'validate_task_binding'), \
                        patch.object(task_submission_cli_module.task_completion,
                                     'collect_git_snapshot', return_value=snapshot), \
                        patch.object(task_submission_cli_module.task_completion,
                                     'bind_executor_task', return_value=(registration, snapshot)), \
                        patch.object(task_submission_cli_module.task_completion,
                                     'validate_env2_pass_marker'), \
                        patch.object(task_submission_cli_module.task_completion,
                                     'read_d6_registration',
                                     side_effect=(RuntimeError('evidence unavailable')
                                                  if evidence_failure else None),
                                     return_value=registration), \
                        patch.object(task_submission_cli_module.task_completion,
                                     '_git', return_value=b''), \
                        patch.object(task_submission_cli_module,
                                     '_bootstrap_registered_d6'), \
                        patch.object(task_submission_cli_module,
                                     '_ignored_file_fingerprints', return_value={}), \
                        patch.object(task_submission_cli_module,
                                     '_codex_developer_profile',
                                     return_value=({'model': 'gpt-6-luna'}, 'high')), \
                        patch.object(task_submission_cli_module,
                                     '_ignored_output_rejection', return_value=None), \
                        patch.object(codex_direct_developer, 'run', return_value=result), \
                        patch.object(task_submission_cli_module.dataclasses, 'replace',
                                     side_effect=lambda value, **changes: value):
                    task_submission_cli_module.run_task_codex_delegate(args, store=self.store)

                saved = self.store.get(task['task_id'])['developer']
                self.assertEqual(saved['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
                self.assertEqual(saved['error_code'], expected_code)
                self.assertEqual(saved['execution']['status'], expected_execution)
                self.assertEqual(saved['execution']['process_exit_code'], 0)
                self.assertEqual(saved['execution']['native_session_id'], result.thread_id)
                self.assertIsNone(saved['usage'])
                self.assertEqual(saved.get('blocked_reason'), expected_reason)

    def test_codex_blocked_result_persistence_rejection_drops_blocked_reason(self):
        root = str(Path.cwd())
        snapshot = SimpleNamespace(root=root, common_dir='/registered/common',
                                   branch='task-branch', head='a' * 40)
        registration = SimpleNamespace(
            task_id='executor-task', trusted_agent_id='agent',
            session_ref='session-ref', native_session_id=None, root=root,
            common_dir=snapshot.common_dir, branch=snapshot.branch,
            env2_pass_marker='pass')
        task = self._codex_dispatch_task()
        self.store.create(task)
        args = SimpleNamespace(
            task_id=task['task_id'], selection_source='USER', context_stdin=False,
            executor_task='executor-task', read_path=['src.py'],
            write_path=['src.py'], acceptance=['the outcome is recorded'],
            egress_basis='Approved non-sensitive test context.')
        result = SimpleNamespace(
            status='BLOCKED', blocked_reason='The requested change cannot be made safely.',
            thread_id='12345678-1234-4234-8234-123456789abc',
            exit_code=0, stdout_sha256='d' * 64, elapsed_ms=25,
            cleanup_status='CONFIRMED', usage=None)
        original_update = self.store.update
        rejected = False

        def reject_first_blocked_result(task_id, **changes):
            nonlocal rejected
            developer = changes.get('developer')
            if (not rejected and type(developer) is dict and
                    developer.get('error_code') == 'CODEX_BLOCKED'):
                rejected = True
                raise TaskStoreConflict('simulated first result persistence rejection')
            return original_update(task_id, **changes)

        with patch.object(task_submission_cli_module.task_completion,
                          'validate_task_binding'), \
                patch.object(task_submission_cli_module.task_completion,
                             'collect_git_snapshot', return_value=snapshot), \
                patch.object(task_submission_cli_module.task_completion,
                             'bind_executor_task', return_value=(registration, snapshot)), \
                patch.object(task_submission_cli_module.task_completion,
                             'validate_env2_pass_marker'), \
                patch.object(task_submission_cli_module.task_completion,
                             'read_d6_registration', return_value=registration), \
                patch.object(task_submission_cli_module.task_completion,
                             '_git', return_value=b''), \
                patch.object(task_submission_cli_module, '_bootstrap_registered_d6'), \
                patch.object(task_submission_cli_module,
                             '_ignored_file_fingerprints', return_value={}), \
                patch.object(task_submission_cli_module, '_codex_developer_profile',
                             return_value=({'model': 'gpt-6-luna'}, 'high')), \
                patch.object(task_submission_cli_module,
                             '_ignored_output_rejection', return_value=None), \
                patch.object(codex_direct_developer, 'run', return_value=result), \
                patch.object(task_submission_cli_module.dataclasses, 'replace',
                             side_effect=lambda value, **changes: value), \
                patch.object(self.store, 'update', side_effect=reject_first_blocked_result):
            task_submission_cli_module.run_task_codex_delegate(args, store=self.store)

        saved = self.store.get(task['task_id'])['developer']
        self.assertTrue(rejected)
        self.assertEqual(saved['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(saved['error_code'], 'CODEX_RESULT_PERSISTENCE_REJECTED')
        self.assertNotIn('blocked_reason', saved)

    def test_unknown_token_cap_is_rejected_for_other_routes_and_codex_cannot_claim_one(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        pending = self._developer_assignment(provider='company-gw', max_tokens=None)
        self.store.compare_and_update(
            task['task_id'], expected={'developer': None}, developer=pending)
        dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                       'provider_type': 'openai-compatible', 'model': 'gw-model'}
        with self.assertRaises(LLMToolError):
            self.store.update(task['task_id'], developer=dispatching)

        other = self._codex_dispatch_task()
        claimed_cap = self._developer_assignment(provider='codex', max_tokens=64)
        other['developer'] = claimed_cap
        with self.assertRaises(LLMToolError):
            self.store.create(other)

    def test_owner_private_creation_and_second_store_read(self):
        value = record()
        created = self.store.create(value)
        loaded = TaskStore(self.root).get(value['task_id'])
        self.assertEqual(created, loaded)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        path = self.root / (value['task_id'] + '.json')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(path.stat().st_nlink, 1)
        self.assertEqual(loaded['task_binding']['task_id'], loaded['task_id'])

    def test_get_keeps_record_descriptor_open_for_path_stat(self):
        value = record()
        self.store.create(value)
        expected = self.store.get(value['task_id'])
        name = value['task_id'] + '.json'
        original_open, original_stat, original_fstat = os.open, os.stat, os.fstat
        record_fds = []
        checked = []

        def remember_open(path, flags, *args, **kwargs):
            fd = original_open(path, flags, *args, **kwargs)
            if path == name:
                record_fds.append(fd)
            return fd

        def check_open_during_stat(path, *args, **kwargs):
            if path == name:
                self.assertTrue(record_fds)
                original_fstat(record_fds[-1])
                checked.append(True)
            return original_stat(path, *args, **kwargs)

        with patch.object(task_store_module.os, 'open', side_effect=remember_open), \
                patch.object(task_store_module.os, 'stat', side_effect=check_open_during_stat):
            self.assertEqual(self.store.get(value['task_id']), expected)

        self.assertEqual(checked, [True])
        with self.assertRaises(OSError):
            original_fstat(record_fds[-1])

    def test_get_survives_inode_reuse_during_path_stat(self):
        value = record()
        self.store.create(value)
        other_store = TaskStore(self.root)
        name = value['task_id'] + '.json'
        original_stat = os.stat
        injected = [False]
        updates = []

        def replace_twice_before_path_stat(path, *args, **kwargs):
            if path == name and not injected[0]:
                injected[0] = True
                updates.append(other_store.update(
                    value['task_id'], state='PM_UNAVAILABLE',
                    error_code='PROVIDER_TIMEOUT'))
                updates.append(other_store.update(
                    value['task_id'], error_code='PROVIDER_TIMEOUT'))
            return original_stat(path, *args, **kwargs)

        with patch.object(task_store_module.os, 'stat',
                          side_effect=replace_twice_before_path_stat):
            loaded = self.store.get(value['task_id'])

        self.assertEqual(len(updates), 2)
        self.assertEqual(loaded, updates[-1])

    def test_get_retries_when_atomic_replace_lands_after_open(self):
        value = record()
        self.store.create(value)
        original_stat = os.stat
        replacing = [False]
        replaced = [False]
        updated = []

        def replace_before_path_stat(path, *args, **kwargs):
            if (path == value['task_id'] + '.json' and not replacing[0] and
                    not replaced[0]):
                replaced[0] = True
                replacing[0] = True
                try:
                    updated.append(self.store.update(
                        value['task_id'], state='PM_UNAVAILABLE',
                        error_code='PROVIDER_TIMEOUT'))
                finally:
                    replacing[0] = False
            return original_stat(path, *args, **kwargs)

        with patch.object(task_store_module.os, 'stat', side_effect=replace_before_path_stat):
            loaded = self.store.get(value['task_id'])

        self.assertTrue(replaced[0])
        self.assertEqual(loaded, updated[0])

    def test_get_reports_busy_when_path_keeps_changing(self):
        value = record()
        self.store.create(value)
        original_stat = os.stat
        replacing = [False]
        replacements = []

        def replace_before_path_stat(path, *args, **kwargs):
            if path == value['task_id'] + '.json' and not replacing[0]:
                replacing[0] = True
                try:
                    replacements.append(self.store.update(
                        value['task_id'], state='PM_UNAVAILABLE',
                        error_code='PROVIDER_TIMEOUT'))
                finally:
                    replacing[0] = False
            return original_stat(path, *args, **kwargs)

        with patch.object(task_store_module.os, 'stat', side_effect=replace_before_path_stat):
            with self.assertRaises(LLMToolError) as caught:
                self.store.get(value['task_id'])

        self.assertEqual(len(replacements), 3)
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_BUSY')

    def test_get_rejects_self_inconsistent_open_descriptor(self):
        value = record()
        self.store.create(value)
        path = self.root / (value['task_id'] + '.json')
        target_inode = path.stat().st_ino
        original_fstat = os.fstat
        task_fd_calls = [0]

        def inconsistent_fstat(fd):
            info = original_fstat(fd)
            if info.st_ino == target_inode:
                task_fd_calls[0] += 1
                if task_fd_calls[0] == 2:
                    return SimpleNamespace(
                        st_dev=info.st_dev, st_ino=info.st_ino, st_mode=info.st_mode,
                        st_uid=info.st_uid, st_gid=info.st_gid, st_size=info.st_size + 1,
                        st_mtime_ns=info.st_mtime_ns, st_nlink=info.st_nlink)
            return info

        with patch.object(task_store_module.os, 'fstat', side_effect=inconsistent_fstat):
            with self.assertRaises(LLMToolError) as caught:
                self.store.get(value['task_id'])

        self.assertEqual(task_fd_calls[0], 2)
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_INVALID')

    def test_atomic_update_preserves_integrity_and_retains_lock_inode(self):
        value = record()
        self.store.create(value)
        changed = self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                    error_code='PROVIDER_TIMEOUT')
        self.assertEqual(changed['state'], 'PM_UNAVAILABLE')
        self.assertEqual(changed['error_code'], 'PROVIDER_TIMEOUT')
        self.assertEqual(self.store.get(value['task_id']), changed)
        lock = self.root / ('.' + value['task_id'] + '.json.lock')
        self.assertTrue(lock.is_file())
        self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o600)
        self.assertEqual(lock.stat().st_nlink, 1)

    def test_failed_atomic_replace_keeps_previous_record_and_releases_lock(self):
        value = record()
        self.store.create(value)
        previous = self.store.get(value['task_id'])
        with patch('llmtool.task_store.os.replace', side_effect=OSError('private detail')):
            with self.assertRaises(LLMToolError) as caught:
                self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                  error_code='PROVIDER_TIMEOUT')
        self.assertEqual(self.store.get(value['task_id']), previous)
        self.assertEqual(caught.exception.task_store_code, 'UNAVAILABLE')
        lock = self.root / ('.' + value['task_id'] + '.json.lock')
        self.assertTrue(lock.is_file())
        self.assertFalse(any(path.name.endswith('.tmp') for path in self.root.iterdir()))

    def test_transient_atomic_replace_contention_retries_update(self):
        value = record()
        self.store.create(value)
        original_replace = os.replace
        attempts = []

        def contend_once(source, destination, **kwargs):
            attempts.append((source, destination))
            if len(attempts) == 1:
                raise OSError(errno.EBUSY, 'transient filesystem contention')
            return original_replace(source, destination, **kwargs)

        with patch.object(task_store_module.os, 'replace', side_effect=contend_once):
            updated = self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                        error_code='PROVIDER_TIMEOUT')

        self.assertEqual(len(attempts), 2)
        self.assertEqual(updated['state'], 'PM_UNAVAILABLE')
        self.assertEqual(self.store.get(value['task_id']), updated)

    def _start_lock_holder(self, lock_path):
        script = (
            'import fcntl, os, sys; '
            'fd=os.open(sys.argv[1], os.O_RDWR|os.O_CREAT, 0o600); '
            'fcntl.flock(fd, fcntl.LOCK_EX); print("LOCKED", flush=True); '
            'sys.stdin.readline(); fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)'
        )
        process = subprocess.Popen(
            [sys.executable, '-B', '-c', script, str(lock_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True)
        self.assertEqual(process.stdout.readline().strip(), 'LOCKED')
        self.addCleanup(self._stop_process, process)
        return process

    @staticmethod
    def _stop_process(process):
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()

    def _writer_env(self):
        env = os.environ.copy()
        package_root = str(Path(__file__).resolve().parents[1])
        env['PYTHONPATH'] = package_root + os.pathsep + env.get('PYTHONPATH', '')
        return env

    @staticmethod
    def _readline_timeout(process, timeout=5):
        ready, _, _ = select.select([process.stdout], [], [], timeout)
        if not ready:
            return None
        return process.stdout.readline().strip()

    def test_sigkill_releases_lock_and_next_update_succeeds(self):
        value = record()
        self.store.create(value)
        lock = self.root / ('.' + value['task_id'] + '.json.lock')
        holder = self._start_lock_holder(lock)
        holder.kill()
        self.assertEqual(holder.wait(timeout=5), -signal.SIGKILL)

        changed = self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                    error_code='PROVIDER_TIMEOUT')
        self.assertEqual(changed['state'], 'PM_UNAVAILABLE')
        self.assertEqual(changed['error_code'], 'PROVIDER_TIMEOUT')
        self.assertTrue(lock.is_file())

    def test_two_process_updates_wait_for_lock_and_both_succeed(self):
        value = record()
        self.store.create(value)
        lock = self.root / ('.' + value['task_id'] + '.json.lock')
        holder = self._start_lock_holder(lock)
        script = (
            'import fcntl, sys\n'
            'from llmtool import task_store\n'
            'from llmtool.task_store import TaskStore\n'
            'real_flock = fcntl.flock\n'
            'def observed_flock(fd, operation):\n'
            '    try:\n'
            '        return real_flock(fd, operation)\n'
            '    except BlockingIOError:\n'
            '        print("WAITING", flush=True)\n'
            '        raise\n'
            'task_store.fcntl.flock = observed_flock\n'
            # A stalled parent must not turn the waiter into BUSY; that path
            # has its own test.
            'task_store.TASK_UPDATE_LOCK_TIMEOUT_SECONDS = 30\n'
            'print("READY", flush=True)\n'
            'TaskStore(sys.argv[1]).update(sys.argv[2], state="PM_UNAVAILABLE", '
            'error_code="PROVIDER_TIMEOUT")\n'
            'print("OK", flush=True)\n'
        )
        writers = [subprocess.Popen(
            [sys.executable, '-B', '-c', script, str(self.root), value['task_id']],
            env=self._writer_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True) for _ in range(2)]
        for writer in writers:
            self.addCleanup(self._stop_process, writer)
        try:
            for writer in writers:
                self.assertEqual(self._readline_timeout(writer), 'READY')
            for writer in writers:
                self.assertEqual(self._readline_timeout(writer), 'WAITING')
            self.assertTrue(all(writer.poll() is None for writer in writers))
        finally:
            holder.stdin.write('release\n')
            holder.stdin.flush()
            self.assertEqual(holder.wait(timeout=5), 0)
        results = [writer.communicate(timeout=5) for writer in writers]
        self.assertTrue(all(writer.returncode == 0 for writer in writers), results)
        self.assertTrue(all(stdout.strip().endswith('OK') for stdout, _ in results), results)
        saved = self.store.get(value['task_id'])
        self.assertEqual(saved['state'], 'PM_UNAVAILABLE')
        self.assertEqual(saved['error_code'], 'PROVIDER_TIMEOUT')

    def test_concurrent_process_cas_keeps_real_conflict(self):
        value = record()
        self.store.create(value)
        lock_name = '.' + value['task_id'] + '.json.lock'
        lock_path = self.root / lock_name
        self.assertFalse(lock_path.exists())
        barrier = Path(self.temp.name) / 'first-lock-barrier'
        barrier.mkdir(mode=0o700)
        start = barrier / 'start'
        open_release = barrier / 'open-release'
        owner_release = barrier / 'owner-release'
        script = (
            'import fcntl, os, sys, time\n'
            'from llmtool import task_store\n'
            'from llmtool.task_store import TaskStore, TaskStoreConflict\n'
            'root, task_id, lock_name, start, open_release, owner_release = sys.argv[1:]\n'
            'print("READY", flush=True)\n'
            'while not os.path.exists(start): time.sleep(0.005)\n'
            'real_open = os.open\n'
            'def synchronized_open(path, flags, *args, **kwargs):\n'
            '    fd = real_open(path, flags, *args, **kwargs)\n'
            '    if path == lock_name:\n'
            '        inode = os.fstat(fd).st_ino\n'
            '        print("OPENED", inode, flush=True)\n'
            '        while not os.path.exists(open_release): time.sleep(0.005)\n'
            '    return fd\n'
            'os.open = synchronized_open\n'
            'real_flock = fcntl.flock\n'
            'first_acquisition = [True]\n'
            'def observed_flock(fd, operation):\n'
            '    try:\n'
            '        result = real_flock(fd, operation)\n'
            '    except BlockingIOError:\n'
            '        print("WAITING", flush=True)\n'
            '        raise\n'
            '    if first_acquisition[0]:\n'
            '        first_acquisition[0] = False\n'
            '        print("ACQUIRED", flush=True)\n'
            '        while not os.path.exists(owner_release): time.sleep(0.005)\n'
            '    return result\n'
            'task_store.fcntl.flock = observed_flock\n'
            # A stalled parent must not turn the waiter into BUSY; that path
            # has its own test.
            'task_store.TASK_UPDATE_LOCK_TIMEOUT_SECONDS = 30\n'
            'try:\n'
            '    TaskStore(root).compare_and_update(task_id, expected={"state": "PM_PENDING"}, '
            'state="PM_UNAVAILABLE", error_code="PROVIDER_TIMEOUT")\n'
            'except TaskStoreConflict as exc:\n'
            '    print("CONFLICT", exc.task_store_code, flush=True)\n'
            '    sys.exit(3)\n'
            'print("UPDATED", flush=True)\n'
        )
        writers = [subprocess.Popen(
            [sys.executable, '-B', '-c', script, str(self.root), value['task_id'],
             lock_name, str(start), str(open_release), str(owner_release)],
            env=self._writer_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True) for _ in range(2)]
        for writer in writers:
            self.addCleanup(self._stop_process, writer)
        try:
            for writer in writers:
                self.assertEqual(self._readline_timeout(writer), 'READY')
            start.touch(mode=0o600)
            inodes = []
            for writer in writers:
                line = self._readline_timeout(writer)
                self.assertIsNotNone(line)
                fields = line.split()
                self.assertEqual(fields[0], 'OPENED', line)
                inodes.append(int(fields[1]))
            self.assertEqual(inodes[0], inodes[1])
            self.assertEqual(lock_path.stat().st_ino, inodes[0])
            open_release.touch(mode=0o600)
            states = [self._readline_timeout(writer) for writer in writers]
            self.assertEqual(sorted(states), ['ACQUIRED', 'WAITING'], states)
        finally:
            open_release.touch(mode=0o600)
            owner_release.touch(mode=0o600)
        results = [writer.communicate(timeout=5) for writer in writers]
        self.assertEqual(sorted(writer.returncode for writer in writers), [0, 3], results)
        self.assertTrue(any('CONFLICT' in stdout for stdout, _ in results), results)
        saved = self.store.get(value['task_id'])
        self.assertEqual(saved['state'], 'PM_UNAVAILABLE')

    def test_lock_wait_timeout_is_bounded_and_classified_busy(self):
        value = record()
        self.store.create(value)
        lock = self.root / ('.' + value['task_id'] + '.json.lock')
        holder = self._start_lock_holder(lock)
        started = time.monotonic()
        with patch.object(task_store_module, 'TASK_UPDATE_LOCK_TIMEOUT_SECONDS', 0.1):
            with self.assertRaises(LLMToolError) as caught:
                self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                  error_code='PROVIDER_TIMEOUT')
        elapsed = time.monotonic() - started
        self.assertEqual(caught.exception.task_store_code, 'BUSY')
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_BUSY')
        self.assertGreaterEqual(elapsed, 0.08)
        self.assertLess(elapsed, 1.0)
        holder.stdin.write('release\n')
        holder.stdin.flush()
        self.assertEqual(holder.wait(timeout=5), 0)

    def test_transient_flock_enolck_retries_update(self):
        value = record()
        self.store.create(value)
        original_flock = fcntl.flock
        attempts = []

        def contend_once(fd, operation):
            if operation == fcntl.LOCK_EX | fcntl.LOCK_NB:
                attempts.append(operation)
                if len(attempts) == 1:
                    raise OSError(errno.ENOLCK, 'transient lock table contention')
            return original_flock(fd, operation)

        with patch.object(task_store_module.fcntl, 'flock', side_effect=contend_once):
            updated = self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                        error_code='PROVIDER_TIMEOUT')

        self.assertEqual(len(attempts), 2)
        self.assertEqual(updated['state'], 'PM_UNAVAILABLE')
        self.assertEqual(updated['error_code'], 'PROVIDER_TIMEOUT')
        self.assertEqual(self.store.get(value['task_id']), updated)

    def test_concurrent_lock_create_enoent_is_retried(self):
        # macOS openat(O_CREAT) may report ENOENT while another process
        # creates the same lock name; the next attempt opens that file.
        value = record()
        self.store.create(value)
        lock_name = '.' + value['task_id'] + '.json.lock'
        original_open = os.open
        attempts = []

        def lose_first_create(path, flags, *args, **kwargs):
            if path == lock_name:
                attempts.append(flags)
                if len(attempts) == 1:
                    raise FileNotFoundError(errno.ENOENT, 'private filesystem detail')
            return original_open(path, flags, *args, **kwargs)

        with patch.object(task_store_module.os, 'open', side_effect=lose_first_create):
            changed = self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                        error_code='PROVIDER_TIMEOUT')
        self.assertEqual(changed['state'], 'PM_UNAVAILABLE')
        self.assertEqual(len(attempts), 2)
        self.assertTrue(all(flags & os.O_CREAT and flags & os.O_NOFOLLOW for flags in attempts))

    def test_persistent_lock_create_enoent_is_bounded_and_unavailable(self):
        value = record()
        self.store.create(value)
        lock_name = '.' + value['task_id'] + '.json.lock'
        original_open = os.open
        attempts = []

        def always_enoent(path, flags, *args, **kwargs):
            if path == lock_name:
                attempts.append(path)
                raise FileNotFoundError(errno.ENOENT, 'private filesystem detail')
            return original_open(path, flags, *args, **kwargs)

        started = time.monotonic()
        with patch.object(task_store_module, 'TASK_UPDATE_LOCK_TIMEOUT_SECONDS', 0.1), \
                patch.object(task_store_module.os, 'open', side_effect=always_enoent):
            with self.assertRaises(LLMToolError) as caught:
                self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                  error_code='PROVIDER_TIMEOUT')
        elapsed = time.monotonic() - started
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_UNAVAILABLE')
        self.assertNotIn('private filesystem detail', str(caught.exception))
        self.assertGreater(len(attempts), 1)
        self.assertGreaterEqual(elapsed, 0.08)
        self.assertLess(elapsed, 1.0)
        self.assertEqual(self.store.get(value['task_id'])['state'], value['state'])

    def test_planted_lock_paths_are_refused_without_touching_them(self):
        value = record()
        self.store.create(value)
        lock = self.root / ('.' + value['task_id'] + '.json.lock')
        outside = Path(self.temp.name) / 'outside-target'

        def symlink():
            outside.write_bytes(b'outside')
            outside.chmod(0o600)
            lock.symlink_to(outside)

        def dangling():
            lock.symlink_to(Path(self.temp.name) / 'missing-target')

        def hardlink():
            outside.write_bytes(b'outside')
            outside.chmod(0o600)
            os.link(outside, lock)

        def loose_mode():
            lock.write_bytes(b'')
            lock.chmod(0o644)

        def fifo():
            os.mkfifo(lock, 0o600)

        def directory():
            lock.mkdir(mode=0o700)

        cases = (('symlink', symlink), ('dangling symlink', dangling), ('hardlink', hardlink),
                 ('mode 0644', loose_mode), ('fifo', fifo), ('directory', directory))
        for label, plant in cases:
            with self.subTest(label):
                plant()
                before = self.store.get(value['task_id'])
                started = time.monotonic()
                with self.assertRaises(LLMToolError) as caught:
                    self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                      error_code='PROVIDER_TIMEOUT')
                self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_INVALID')
                self.assertLess(time.monotonic() - started, 1.0)
                self.assertEqual(self.store.get(value['task_id']), before)
                self.assertFalse((Path(self.temp.name) / 'missing-target').exists())
                if outside.exists():
                    self.assertEqual(outside.read_bytes(), b'outside')
                    self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o600)
                if lock.is_dir() and not lock.is_symlink():
                    lock.rmdir()
                else:
                    lock.unlink()
                if outside.exists():
                    outside.unlink()

    def test_lock_owned_by_another_user_is_refused(self):
        value = record()
        self.store.create(value)
        lock_name = '.' + value['task_id'] + '.json.lock'
        original_open, original_fstat = os.open, os.fstat
        lock_fd = []

        def remember(path, flags, *args, **kwargs):
            fd = original_open(path, flags, *args, **kwargs)
            if path == lock_name:
                lock_fd.append(fd)
            return fd

        def foreign_owner(fd):
            info = original_fstat(fd)
            if lock_fd and fd == lock_fd[0]:
                fields = list(info)
                fields[stat.ST_UID] = os.getuid() + 1
                return os.stat_result(fields)
            return info

        with patch.object(task_store_module.os, 'open', side_effect=remember), \
                patch.object(task_store_module.os, 'fstat', side_effect=foreign_owner):
            with self.assertRaises(LLMToolError) as caught:
                self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                  error_code='PROVIDER_TIMEOUT')
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_INVALID')
        self.assertNotEqual(self.store.get(value['task_id'])['state'], 'PM_UNAVAILABLE')

    def test_lock_open_and_fstat_errors_are_safe_and_close_descriptors(self):
        value = record()
        self.store.create(value)
        lock_name = '.' + value['task_id'] + '.json.lock'
        original_open = os.open
        original_fstat = os.fstat

        def fail_lock_open(path, flags, *args, **kwargs):
            if path == lock_name:
                raise OSError(errno.EIO, 'private filesystem detail')
            return original_open(path, flags, *args, **kwargs)

        with patch.object(task_store_module.os, 'open', side_effect=fail_lock_open):
            with self.assertRaises(LLMToolError) as caught:
                self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                  error_code='PROVIDER_TIMEOUT')
        self.assertEqual(caught.exception.task_store_code, 'UNAVAILABLE')
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_UNAVAILABLE')
        self.assertNotIn('private filesystem detail', str(caught.exception))

        lock_fd = []

        def remember_lock_open(path, flags, *args, **kwargs):
            fd = original_open(path, flags, *args, **kwargs)
            if path == lock_name:
                lock_fd.append(fd)
            return fd

        def fail_lock_fstat(fd):
            if lock_fd and fd == lock_fd[0]:
                raise OSError(errno.EIO, 'private stat detail')
            return original_fstat(fd)

        with patch.object(task_store_module.os, 'open', side_effect=remember_lock_open), \
                patch.object(task_store_module.os, 'fstat', side_effect=fail_lock_fstat):
            with self.assertRaises(LLMToolError) as caught:
                self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                  error_code='PROVIDER_TIMEOUT')
        self.assertEqual(caught.exception.task_store_code, 'UNAVAILABLE')
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_UNAVAILABLE')
        self.assertNotIn('private stat detail', str(caught.exception))
        self.assertEqual(len(lock_fd), 1)
        with self.assertRaises(OSError) as closed:
            original_fstat(lock_fd[0])
        self.assertEqual(closed.exception.errno, errno.EBADF)

    def test_task_store_failures_expose_safe_distinct_categories(self):
        value = record()
        self.store.create(value)
        self.store.compare_and_update(
            value['task_id'], expected={'state': 'PM_PENDING'},
            state='PM_UNAVAILABLE', error_code='PROVIDER_TIMEOUT')
        with self.assertRaises(TaskStoreConflict) as caught:
            self.store.compare_and_update(
                value['task_id'], expected={'state': 'PM_PENDING'},
                state='PM_UNAVAILABLE', error_code='PROVIDER_TIMEOUT')
        self.assertEqual(caught.exception.task_store_code, 'CONFLICT')
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_CONFLICT')

    def test_unknown_malformed_corrupt_symlink_and_wrong_mode_reject(self):
        with self.assertRaises(LLMToolError):
            self.store.get('../outside')
        with self.assertRaises(LLMToolError):
            self.store.get('0' * 32)
        value = record()
        self.store.create(value)
        path = self.root / (value['task_id'] + '.json')
        raw = json.loads(path.read_text())
        raw['state'] = 'PM_RESPONDED'
        path.write_text(json.dumps(raw))
        with self.assertRaises(LLMToolError):
            self.store.get(value['task_id'])
        path.unlink()
        target = self.root / 'target'
        target.write_text('{}')
        path.symlink_to(target)
        with self.assertRaises(LLMToolError):
            self.store.get(value['task_id'])
        path.unlink()
        self.store.create(value)
        path.chmod(0o644)
        with self.assertRaises(LLMToolError):
            self.store.get(value['task_id'])

    def test_secret_like_request_or_response_never_persists(self):
        for key, secret in (('request', 'api_key=sk-abcdefghijklmnopqrstuvwxyz'),
                            ('response', 'Bearer abcdefghijklmnop')):
            value = record()
            value[key] = secret
            if key == 'request':
                value['request_sha256'] = hashlib.sha256(secret.encode()).hexdigest()
                contract = TaskContract(
                    schema_version=1, task_id=value['task_id'], contract_version=1,
                    previous_digest=None,
                    goal='PM intake request sha256:' + value['request_sha256'],
                    authorized_scope=('pm-planning',), forbidden_scope=(),
                    criteria=(AcceptanceCriterion('pm_response', CriterionKind.ARTIFACT,
                                                  'Record PM outcome.'),),
                )
                value['task_contract'] = contract.to_dict()
                value['task_binding'] = contract.binding(value['run_id']).to_dict()
            else:
                value['state'] = 'PM_RESPONDED'
                value['error_code'] = None
            with self.subTest(key=key), self.assertRaises(LLMToolError):
                self.store.create(value)
        self.assertFalse(self.root.exists())

    def test_state_requires_matching_request_provider_and_outcome(self):
        value = record()
        value.update(state='PM_RESPONDED', request=None,
                     response='A recorded PM reply.', error_code=None)
        with self.assertRaises(LLMToolError):
            self.store.create(value)

        value = record()
        value.update(state='INPUT_REJECTED', backend_session_semantics='EPHEMERAL_SINGLE_CALL',
                     error_code='PM_INPUT_REJECTED')
        with self.assertRaises(LLMToolError):
            self.store.create(value)

    def _active_pm_record(self, route='opencode'):
        value = record()
        response = json.dumps({
            'schema_version': 1, 'summary': 'Ready.',
            'developer_assignment': {
                'route': route, 'risk': 'LOW', 'goal': 'Do task.',
                'acceptance': ['A test passes.'],
            },
        }, separators=(',', ':'))
        value.update(
            schema_version=2,
            pm_provider_profile='active-pm-runtime',
            pm_provider_type='active-pm-runtime',
            pm_model=None,
            pm_route_binding={'type': 'active-pm-runtime', 'model': None, 'base_url': None},
            backend_session_semantics='ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL',
            state='PM_RESPONDED',
            response=response,
            error_code=None,
            developer=None,
            pm_assignment={
                'schema_version': 1, 'route': route, 'risk': 'LOW',
                'goal': 'Do task.', 'acceptance': ['A test passes.'],
            },
            completion=None,
            pm_provenance={
                'source': 'ACTIVE_PM_RUNTIME', 'provider_invoked': False,
                'native_session_id_status': 'UNAVAILABLE', 'usage_status': 'UNKNOWN',
                'session_restored': False, 'automatic_callback': False,
            },
        )
        return value

    def test_active_pm_provenance_is_validated_and_round_trips(self):
        value = self._active_pm_record()
        created = self.store.create(value)
        self.assertEqual(self.store.get(value['task_id']), created)
        self.assertEqual(created['pm_provenance']['usage_status'], 'UNKNOWN')
        self.assertEqual(created['backend_session_semantics'],
                         'ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL')

    # #1502 WP-E (Codex v1 F1 on PR #1774): a Task stored before the gw Agent
    # rename names the route by its retired id. It reads as gw-agent through
    # worker_dispatch.stored_worker_id; a new write naming the retired id fails.
    _RETIRED_GW_AGENT = 'company-gw-agent'

    def _path(self, task_id):
        return self.root / (task_id + '.json')

    def _store_as_written(self, value):
        """Put a record on disk as an older llmtool wrote it, digest included."""
        value = dict(value)
        value['record_sha256'] = task_store_module._digest(value)
        self._path(value['task_id']).write_bytes(task_store_module._json_bytes(value))
        return value

    def _legacy_gw_agent_task(self):
        task = self._codex_dispatch_task()
        task['pm_assignment'] = dict(task['pm_assignment'], route='gw-agent')
        self.store.create(task)
        developer = self._developer_assignment(provider='gw-agent')
        developer['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
        current = self.store.compare_and_update(
            task['task_id'], expected={'developer': None}, developer=developer)
        legacy = dict(
            current,
            developer=dict(current['developer'], provider_profile=self._RETIRED_GW_AGENT),
            pm_assignment=dict(current['pm_assignment'], route=self._RETIRED_GW_AGENT))
        return current, self._store_as_written(legacy)

    def test_stored_task_with_the_retired_gw_agent_id_reads_as_gw_agent(self):
        current, legacy = self._legacy_gw_agent_task()
        raw = self._path(legacy['task_id']).read_bytes()
        loaded = self.store.get(legacy['task_id'])
        self.assertEqual(loaded['developer']['provider_profile'], 'gw-agent')
        self.assertEqual(loaded['pm_assignment']['route'], 'gw-agent')
        # Only the retired ids change; the read copy is a valid, self-consistent record.
        self.assertEqual({key: value for key, value in loaded.items() if key != 'record_sha256'},
                         {key: value for key, value in current.items() if key != 'record_sha256'})
        self.assertEqual(loaded['record_sha256'], task_store_module._digest(loaded))
        task_store_module._validate(dict(loaded))   # strict: raises if invalid
        # A read never rewrites the stored record.
        self.assertEqual(self._path(legacy['task_id']).read_bytes(), raw)
        self.assertIn(self._RETIRED_GW_AGENT.encode(), raw)

    def test_update_of_a_legacy_task_writes_the_current_gw_agent_id(self):
        _current, legacy = self._legacy_gw_agent_task()
        loaded = self.store.get(legacy['task_id'])
        claimed = {'pr': 17, 'head_sha': 'a' * 40, 'state': 'CLAIMED',
                   'claimed_at': '2026-11-06T00:00:00Z'}
        saved = self.store.compare_and_update(
            legacy['task_id'], expected={'record_sha256': loaded['record_sha256'],
                                         'developer': loaded['developer']},
            merge_attempt=claimed)
        self.assertEqual(saved['merge_attempt'], claimed)
        self.assertEqual(saved['developer']['provider_profile'], 'gw-agent')
        raw = self._path(legacy['task_id']).read_bytes()
        self.assertNotIn(self._RETIRED_GW_AGENT.encode(), raw)
        self.assertEqual(self.store.get(legacy['task_id']), saved)

    def test_legacy_active_pm_task_reads_its_retired_route_as_gw_agent(self):
        value = self._active_pm_record(route='gw-agent')
        created = self.store.create(value)
        response = json.loads(created['response'])
        response['developer_assignment']['route'] = self._RETIRED_GW_AGENT
        stored_response = json.dumps(response, separators=(',', ':'))
        self._store_as_written(dict(
            created, response=stored_response,
            pm_assignment=dict(created['pm_assignment'], route=self._RETIRED_GW_AGENT)))
        loaded = self.store.get(value['task_id'])
        self.assertEqual(loaded['pm_assignment']['route'], 'gw-agent')
        # The PM response is kept exactly as written.
        self.assertEqual(loaded['response'], stored_response)

    def test_legacy_task_with_a_changed_digest_stays_invalid(self):
        _current, legacy = self._legacy_gw_agent_task()
        tampered = dict(legacy, updated_at='2026-10-22T00:00:00Z')
        self._path(legacy['task_id']).write_bytes(task_store_module._json_bytes(tampered))
        with self.assertRaises(LLMToolError) as caught:
            self.store.get(legacy['task_id'])
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_INVALID')

    def test_new_writes_naming_the_retired_gw_agent_id_are_refused(self):
        task = self._codex_dispatch_task()
        task['pm_assignment'] = dict(task['pm_assignment'], route=self._RETIRED_GW_AGENT)
        with self.assertRaises(LLMToolError):
            self.store.create(task)
        self.assertFalse(self._path(task['task_id']).exists())

        value = self._active_pm_record(route=self._RETIRED_GW_AGENT)
        with self.assertRaises(LLMToolError):
            self.store.create(value)

        task = self._codex_dispatch_task()
        self.store.create(task)
        developer = self._developer_assignment(provider=self._RETIRED_GW_AGENT)
        developer['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
        with self.assertRaises(LLMToolError):
            self.store.compare_and_update(
                task['task_id'], expected={'developer': None}, developer=developer)
        self.assertIsNone(self.store.get(task['task_id'])['developer'])

        # _validate itself, strict or not, never accepts the retired id.
        current, _legacy = self._legacy_gw_agent_task()
        for strict in (True, False):
            for field, key in (('developer', 'provider_profile'), ('pm_assignment', 'route')):
                with self.subTest(strict=strict, field=field):
                    candidate = dict(current, **{
                        field: dict(current[field], **{key: self._RETIRED_GW_AGENT})})
                    candidate['record_sha256'] = task_store_module._digest(candidate)
                    with self.assertRaises(LLMToolError):
                        task_store_module._validate(candidate, strict=strict)

    def test_active_codex_pm_session_binding_is_validated_and_round_trips(self):
        value = self._active_pm_record()
        native = str(uuid.uuid4())
        value['pm_session_id'] = 'pm-' + native
        value['pm_provenance'].update(
            pm_runtime='codex', pm_native_session_id=native,
            pm_cwd='/tmp/codex-pm-task',
            native_session_id_status='LOCAL_SESSION_FILE_OBSERVED')
        created = self.store.create(value)
        self.assertEqual(self.store.get(value['task_id']), created)
        self.assertEqual(created['pm_provenance']['pm_runtime'], 'codex')
        invalid = self._active_pm_record()
        invalid['pm_session_id'] = 'pm-' + native
        invalid['pm_provenance'].update(
            pm_runtime='codex', pm_native_session_id=native,
            pm_cwd='relative/path',
            native_session_id_status='LOCAL_SESSION_FILE_OBSERVED')
        with self.assertRaises(LLMToolError):
            TaskStore(self.root / 'invalid-codex-binding').create(invalid)

    def test_active_pm_company_gateway_assignment_is_validated_and_round_trips(self):
        value = self._active_pm_record(route='company-gw')
        created = self.store.create(value)
        self.assertEqual(self.store.get(value['task_id']), created)
        self.assertEqual(created['pm_assignment']['route'], 'company-gw')

    def test_legacy_codex_pm_runtime_records_remain_readable(self):
        value = self._active_pm_record()
        value['pm_provider_type'] = 'codex-cli'
        value['pm_route_binding'] = {
            'type': 'codex-cli', 'model': None, 'base_url': None}
        created = self.store.create(value)
        self.assertEqual(self.store.get(value['task_id']), created)
        self.assertEqual(created['pm_provider_type'], 'codex-cli')

    def test_active_pm_provenance_rejects_false_or_incomplete_claims(self):
        mutations = (
            lambda record: record['pm_provenance'].__setitem__('provider_invoked', True),
            lambda record: record['pm_provenance'].__setitem__('session_restored', True),
            lambda record: record['pm_provenance'].__setitem__('automatic_callback', True),
            lambda record: record.__setitem__('usage', {
                'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}),
            lambda record: record.__setitem__('pm_assignment', {
                **record['pm_assignment'], 'risk': 'OTHER'}),
            lambda record: record.__setitem__('pm_assignment', {
                **record['pm_assignment'], 'route': 'none'}),
            lambda record: record.pop('pm_provenance'),
        )
        for mutate in mutations:
            value = self._active_pm_record()
            mutate(value)
            with self.subTest(mutate=mutate), self.assertRaises(LLMToolError):
                self.store.create(value)

    def test_legacy_task_shapes_remain_compatible_without_provenance(self):
        legacy = record()
        self.assertEqual(self.store.create(legacy)['schema_version'], 1)
        self.assertEqual(self.store.get(legacy['task_id'])['task_id'], legacy['task_id'])

        previous_v2 = record()
        previous_v2.update(
            schema_version=2,
            pm_route_binding={'type': 'codex-cli', 'model': 'gpt-6-luna',
                              'base_url': None},
            developer=None,
            pm_assignment=None,
            completion=None,
        )
        saved = self.store.create(previous_v2)
        self.assertEqual(self.store.get(previous_v2['task_id']), saved)
        self.assertNotIn('pm_provenance', saved)

    @staticmethod
    def _completion_event(value, *, event_id=None, sequence=0):
        return dict(event_id=event_id or new_id(), run_id=value['run_id'],
                    session_id='ses_Test123', source='DIRECT_D6_DEVELOPER',
                    source_sha='a' * 40, sequence=sequence,
                    payload_sha256='b' * 64)

    def test_completion_event_persists_before_wake_and_replays_idempotently(self):
        value = self._active_pm_record()
        self.store.create(value)
        args = self._completion_event(value)
        first = self.store.record_completion_event(value['task_id'], **args)
        self.assertTrue(first['created'])
        self.assertEqual(first['event']['state'], 'PENDING')
        with self.assertRaises(LLMToolError) as generic_mutation:
            self.store.update(value['task_id'], completion_events=[])
        self.assertEqual(generic_mutation.exception.task_store_error_code,
                         'COMPLETION_EVENT_MUTATION_FORBIDDEN')
        self.assertEqual(TaskStore(self.root).unacked_completion_events(
            value['task_id']), [first['event']])
        replay = TaskStore(self.root).record_completion_event(value['task_id'], **args)
        self.assertFalse(replay['created'])
        self.assertEqual(replay['event'], first['event'])
        self.assertEqual(len(self.store.get(value['task_id'])['completion_events']), 1)
        with self.assertRaises(LLMToolError) as changed_payload:
            self.store.record_completion_event(value['task_id'],
                **{**args, 'payload_sha256': 'c' * 64})
        self.assertEqual(changed_payload.exception.task_store_error_code,
                         'COMPLETION_EVENT_CONFLICT')
        with self.assertRaises(LLMToolError) as changed_id_same_sequence:
            self.store.record_completion_event(value['task_id'],
                **{**args, 'event_id': new_id()})
        self.assertEqual(changed_id_same_sequence.exception.task_store_error_code,
                         'COMPLETION_EVENT_CONFLICT')
        second = self._completion_event(value, sequence=2)
        self.store.record_completion_event(value['task_id'], **second)
        with self.assertRaises(LLMToolError) as out_of_order:
            self.store.record_completion_event(value['task_id'],
                **self._completion_event(value, sequence=1))
        self.assertEqual(out_of_order.exception.task_store_error_code,
                         'COMPLETION_EVENT_SEQUENCE_INVALID')

    def test_completion_ack_requires_persisted_pm_consumption_and_confirmed_intent(self):
        value = self._active_pm_record()
        self.store.create(value)
        args = self._completion_event(value)
        event_id = args['event_id']
        pm = value['pm_session_id']
        self.store.record_completion_event(value['task_id'], **args)
        with self.assertRaises(LLMToolError) as wrong_pm:
            self.store.consume_completion_event(value['task_id'], event_id,
                pm_session_id='pm-not-the-original', pm_decision_sha256='c' * 64)
        self.assertEqual(wrong_pm.exception.task_store_error_code,
                         'COMPLETION_EVENT_BINDING_MISMATCH')
        with self.assertRaises(LLMToolError):
            self.store.ack_completion_event(value['task_id'], event_id,
                                            pm_session_id=pm, pm_decision_sha256='c' * 64)
        consumed = self.store.consume_completion_event(
            value['task_id'], event_id, pm_session_id=pm, pm_decision_sha256='c' * 64)
        self.assertEqual(consumed['event']['state'], 'PM_CONSUMED')
        self.assertFalse(TaskStore(self.root).consume_completion_event(
            value['task_id'], event_id, pm_session_id=pm,
            pm_decision_sha256='c' * 64)['created'])
        intent_id = new_id()
        intent = self.store.prepare_completion_dispatch_intent(
            value['task_id'], event_id, intent_id=intent_id,
            intent_sha256='d' * 64, pm_session_id=pm)
        self.assertTrue(intent['created'])
        self.assertEqual(intent['event']['dispatch_intent']['state'], 'UNKNOWN')
        with self.assertRaises(LLMToolError) as refused_replay:
            self.store.prepare_completion_dispatch_intent(
                value['task_id'], event_id, intent_id=new_id(),
                intent_sha256='e' * 64, pm_session_id=pm)
        self.assertEqual(refused_replay.exception.task_store_error_code,
                         'COMPLETION_EVENT_CONFLICT')
        self.assertFalse(TaskStore(self.root).prepare_completion_dispatch_intent(
            value['task_id'], event_id, intent_id=intent_id,
            intent_sha256='d' * 64, pm_session_id=pm)['created'])
        with self.assertRaises(LLMToolError):
            self.store.ack_completion_event(value['task_id'], event_id,
                                            pm_session_id=pm, pm_decision_sha256='c' * 64)
        self.assertEqual(TaskStore(self.root).unacked_completion_events(
            value['task_id'])[0]['dispatch_intent']['state'], 'UNKNOWN')
        confirmed = self.store.confirm_completion_dispatch(
            value['task_id'], event_id, intent_id=intent_id,
            receipt_sha256='f' * 64, pm_session_id=pm)
        self.assertEqual(confirmed['event']['dispatch_intent']['state'], 'CONFIRMED')
        self.assertFalse(self.store.confirm_completion_dispatch(
            value['task_id'], event_id, intent_id=intent_id,
            receipt_sha256='f' * 64, pm_session_id=pm)['created'])
        acked = self.store.ack_completion_event(value['task_id'], event_id,
                                                pm_session_id=pm,
                                                pm_decision_sha256='c' * 64)
        self.assertEqual(acked['event']['state'], 'ACKED')
        self.assertEqual(TaskStore(self.root).unacked_completion_events(
            value['task_id']), [])
        self.assertFalse(self.store.ack_completion_event(
            value['task_id'], event_id, pm_session_id=pm,
            pm_decision_sha256='c' * 64)['created'])

    def test_completion_persist_failures_preserve_previous_transition(self):
        value = self._active_pm_record()
        self.store.create(value)
        args = self._completion_event(value)
        pm = value['pm_session_id']
        prior = self.store.get(value['task_id'])
        with patch('llmtool.task_store.os.replace', side_effect=OSError('private detail')):
            with self.assertRaises(LLMToolError):
                self.store.record_completion_event(value['task_id'], **args)
        self.assertEqual(self.store.get(value['task_id']), prior)
        self.store.record_completion_event(value['task_id'], **args)
        self.store.consume_completion_event(value['task_id'], args['event_id'],
                                            pm_session_id=pm,
                                            pm_decision_sha256='c' * 64)
        consumed = self.store.get(value['task_id'])
        with patch('llmtool.task_store.os.replace', side_effect=OSError('private detail')):
            with self.assertRaises(LLMToolError):
                self.store.prepare_completion_dispatch_intent(
                    value['task_id'], args['event_id'],
                    intent_id=new_id(), intent_sha256='d' * 64, pm_session_id=pm)
        self.assertEqual(TaskStore(self.root).get(value['task_id']), consumed)
        intent_id = new_id()
        self.store.prepare_completion_dispatch_intent(
            value['task_id'], args['event_id'],
            intent_id=intent_id, intent_sha256='d' * 64, pm_session_id=pm)
        intent_pending = self.store.get(value['task_id'])
        with patch('llmtool.task_store.os.replace', side_effect=OSError('private detail')):
            with self.assertRaises(LLMToolError):
                self.store.confirm_completion_dispatch(
                    value['task_id'], args['event_id'], intent_id=intent_id,
                    receipt_sha256='e' * 64, pm_session_id=pm)
        self.assertEqual(TaskStore(self.root).get(value['task_id']), intent_pending)
        self.store.confirm_completion_dispatch(
            value['task_id'], args['event_id'], intent_id=intent_id,
            receipt_sha256='e' * 64, pm_session_id=pm)
        confirmed = self.store.get(value['task_id'])
        with patch('llmtool.task_store.os.replace', side_effect=OSError('private detail')):
            with self.assertRaises(LLMToolError):
                self.store.ack_completion_event(value['task_id'], args['event_id'],
                    pm_session_id=pm, pm_decision_sha256='c' * 64)
        self.assertEqual(TaskStore(self.root).get(value['task_id']), confirmed)

    def test_completion_concurrent_duplicate_reconciles_without_second_event(self):
        value = self._active_pm_record()
        self.store.create(value)
        args = self._completion_event(value)
        other = TaskStore(self.root)
        original = self.store._update
        first = True
        def concurrent_writer(*call_args, **call_kwargs):
            nonlocal first
            if first:
                first = False
                other.record_completion_event(value['task_id'], **args)
                raise TaskStoreConflict()
            return original(*call_args, **call_kwargs)
        with patch.object(self.store, '_update', side_effect=concurrent_writer):
            result = self.store.record_completion_event(value['task_id'], **args)
        self.assertFalse(result['created'])
        self.assertEqual(len(self.store.get(value['task_id'])['completion_events']), 1)

    def test_completion_identity_and_legacy_schema_fail_closed(self):
        legacy = record()
        self.store.create(legacy)
        with self.assertRaises(LLMToolError):
            self.store.record_completion_event(legacy['task_id'],
                **self._completion_event(legacy))
        value = self._active_pm_record()
        self.store.create(value)
        args = self._completion_event(value)
        with self.assertRaises(LLMToolError) as mismatch:
            self.store.record_completion_event(value['task_id'],
                **{**args, 'run_id': new_id()})
        self.assertEqual(mismatch.exception.task_store_error_code,
                         'COMPLETION_EVENT_BINDING_MISMATCH')
        with self.assertRaises(LLMToolError):
            self.store.record_completion_event(value['task_id'],
                **{**args, 'session_id': '../other'})

    def test_abandonment_is_pm_bound_validated_and_idempotent(self):
        value = self._codex_dispatch_task()
        self.store.create(value)
        pm = value['pm_session_id']
        with self.assertRaises(LLMToolError) as denied:
            self.store.abandon(value['task_id'], pm_session_id='pm-wrong', reason='Stopped')
        self.assertEqual(denied.exception.task_store_error_code, 'TASK_PM_SESSION_DENIED')
        self.assertNotIn('abandonment', self.store.get(value['task_id']))

        for reason in ('', 'x' * 501, 'line\nfeed'):
            with self.subTest(reason=reason[:20]):
                with self.assertRaises(LLMToolError) as invalid:
                    self.store.abandon(value['task_id'], pm_session_id=pm, reason=reason)
                self.assertEqual(invalid.exception.task_store_error_code,
                                 'TASK_ABANDONMENT_INVALID')
        with patch.object(task_store_module, '_emitted_sensitive', return_value=True):
            with self.assertRaises(LLMToolError) as sensitive:
                self.store.abandon(value['task_id'], pm_session_id=pm,
                                   reason='Rejected by sensitive screen')
            self.assertEqual(sensitive.exception.task_store_error_code,
                             'TASK_ABANDONMENT_INVALID')
        saved = self.store.abandon(value['task_id'], pm_session_id=pm,
                                   reason='PM stopped this Task')
        self.assertEqual(saved['abandonment']['reason'], 'PM stopped this Task')
        self.assertTrue(saved['abandonment']['abandoned_at'].endswith('Z'))
        self.assertEqual(self.store.abandon(value['task_id'], pm_session_id=pm,
                                            reason='PM stopped this Task'), saved)
        with self.assertRaises(LLMToolError) as conflict:
            self.store.abandon(value['task_id'], pm_session_id=pm,
                               reason='Different reason')
        self.assertEqual(conflict.exception.task_store_error_code,
                         'TASK_ABANDONMENT_CONFLICT')
        self.assertEqual(self.store.get(value['task_id']), saved)

    def test_create_rejects_abandonment_without_writing_record(self):
        value = record()
        value['abandonment'] = {
            'reason': 'PM stopped this Task',
            'abandoned_at': '2026-10-29T00:00:00Z',
            'pm_session_id': value['pm_session_id'],
        }

        with self.assertRaises(LLMToolError) as refused:
            self.store.create(value)

        self.assertEqual(refused.exception.task_store_error_code,
                         'TASK_ABANDONMENT_MUTATION_FORBIDDEN')
        self.assertFalse((self.root / (value['task_id'] + '.json')).exists())

    def test_abandonment_schema_requires_exact_shape(self):
        value = self._codex_dispatch_task()
        self.store.create(value)
        with self.assertRaises(LLMToolError) as invalid:
            self.store.update(value['task_id'], abandonment={
                'reason': 'Stopped', 'abandoned_at': '2026-10-29T00:00:00Z',
                'pm_session_id': value['pm_session_id'], 'extra': True,
            })
        self.assertEqual(invalid.exception.task_store_error_code,
                         'TASK_ABANDONMENT_MUTATION_FORBIDDEN')

    def test_abandonment_changes_are_restricted_to_pm_bound_path(self):
        value = self._codex_dispatch_task()
        self.store.create(value)
        original = self.store.get(value['task_id'])
        stamped = {
            'reason': 'PM stopped this Task',
            'abandoned_at': '2026-10-29T00:00:00Z',
            'pm_session_id': value['pm_session_id'],
        }
        for mutate in (
                lambda: self.store.update(value['task_id'], abandonment=stamped),
                lambda: self.store.compare_and_update(value['task_id'],
                    expected={'record_sha256': original['record_sha256']},
                    abandonment=stamped)):
            with self.assertRaises(LLMToolError) as refused:
                mutate()
            self.assertEqual(refused.exception.task_store_error_code,
                             'TASK_ABANDONMENT_MUTATION_FORBIDDEN')
            self.assertEqual(self.store.get(value['task_id']), original)

        saved = self.store.abandon(value['task_id'],
                                   pm_session_id=value['pm_session_id'],
                                   reason=stamped['reason'])
        changed = dict(saved['abandonment'], reason='Changed outside abandon')
        for mutate in (
                lambda: self.store.update(value['task_id'], abandonment=changed),
                lambda: self.store.compare_and_update(value['task_id'],
                    expected={'record_sha256': saved['record_sha256']},
                    abandonment=changed),
                lambda: self.store.update(value['task_id'], abandonment=None),
                lambda: self.store.compare_and_update(value['task_id'],
                    expected={'record_sha256': saved['record_sha256']},
                    abandonment=None)):
            with self.assertRaises(LLMToolError) as refused:
                mutate()
            self.assertEqual(refused.exception.task_store_error_code,
                             'TASK_ABANDONMENT_MUTATION_FORBIDDEN')
            self.assertEqual(self.store.get(value['task_id']), saved)

    def test_abandonment_pm_session_must_match_task_session(self):
        value = self._codex_dispatch_task()
        self.store.create(value)
        saved = self.store.abandon(value['task_id'],
                                   pm_session_id=value['pm_session_id'],
                                   reason='PM stopped this Task')
        mismatched = dict(saved)
        mismatched['abandonment'] = dict(saved['abandonment'],
                                         pm_session_id='pm-' + new_id())
        with self.assertRaises(LLMToolError) as invalid:
            task_store_module._validate(mismatched)
        self.assertEqual(invalid.exception.task_store_error_code,
                         'TASK_ABANDONMENT_INVALID')

    def test_abandonment_supports_v1_record_shape(self):
        value = record()
        self.assertEqual(value['schema_version'], 1)
        self.store.create(value)

        saved = self.store.abandon(value['task_id'],
                                   pm_session_id=value['pm_session_id'],
                                   reason='PM stopped this Task')

        self.assertEqual(saved['abandonment']['reason'], 'PM stopped this Task')
        self.assertEqual(saved['schema_version'], 1)

    def test_pm_session_adoption_fields_are_not_writable_through_generic_updates(self):
        value = self._codex_dispatch_task()
        self.store.create(value)
        original = self.store.get(value['task_id'])
        changes = {
            'pm_provenance': {'forged': True},
            'pm_session_adoptions': [{'old_session_key': original['pm_session_id'],
                'new_session_key': 'pm-' + new_id(), 'at': '2026-10-29T00:00:00Z'}],
        }
        for field, field_value in changes.items():
            for mutate in (
                    lambda field=field, field_value=field_value:
                        self.store.update(value['task_id'], **{field: field_value}),
                    lambda field=field, field_value=field_value:
                        self.store.compare_and_update(value['task_id'],
                            expected={'record_sha256': original['record_sha256']},
                            **{field: field_value})):
                with self.subTest(field=field), self.assertRaises(LLMToolError) as caught:
                    mutate()
                self.assertEqual(caught.exception.task_store_error_code,
                                 'PM_SESSION_ADOPTION_INVALID')
                self.assertEqual(self.store.get(value['task_id']), original)

    def test_pm_session_adoption_refuses_abandoned_task_under_update_lock(self):
        value = record()
        self.store.create(value)
        self.store.abandon(value['task_id'], pm_session_id=value['pm_session_id'],
                           reason='Stopped before adoption')
        current = self.store.get(value['task_id'])
        with self.assertRaises(LLMToolError) as caught:
            self.store.adopt_pm_session(
                value['task_id'], old_session_key=current['pm_session_id'],
                new_session_key='pm-' + new_id(), at='2026-10-29T00:00:00Z',
                expected_record_sha256=current['record_sha256'],
                old_runtime='claude-code', new_runtime='claude-code', new_account=None,
                liveness=['PROCESS_CLEAN', 'LOCK_FREE', 'REGISTRY_CLEAN'])
        self.assertEqual(caught.exception.task_store_error_code,
                         'PM_SESSION_ADOPTION_ABANDONED')
        self.assertEqual(self.store.get(value['task_id']), current)


_NO_DEVELOPER_PATHS = object()


class CompletionDocsOnlyAttributedPathsTests(unittest.TestCase):
    """#1169: the completion docs-only rule uses Developer-attributed paths.

    A clean merge of trusted main adds its own files to changed_paths only.
    """

    HEAD = 'c' * 40
    DOCS = ['docs/guide.md', 'tools/llmtool/WORKFLOW.md']
    MERGED = ['docs/guide.md', 'tools/llmtool/WORKFLOW.md',
              'tools/llmtool/llmtool/cli.py', 'tools/llmtool/llmtool/task_store.py']

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TaskStore(Path(self.temp.name).resolve() / 'tasks')

    @staticmethod
    def _task():
        request = 'Bounded non-sensitive documentation task.'
        task_id, run_id = new_id(), new_id()
        task = _record(
            task_id, run_id, 'pm-' + new_id(), 'pm-profile', request,
            state='PM_RESPONDED', provider_type='openai-compatible', model='pm-model',
            backend='NO_NATIVE_SESSION_ID_EXPOSED',
            request_sha=hashlib.sha256(request.encode()).hexdigest())
        task.update(
            pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                              'base_url': None},
            response='PM returned a bounded task.', error_code=None,
            pm_assignment={'schema_version': 1, 'route': 'codex', 'risk': 'LOW',
                           'goal': 'Update the guide.',
                           'acceptance': ['the guide is updated']},
        )
        return task

    def docs_test(self, paths, **overrides):
        test = {'status': 'NOT_APPLICABLE_DOCS_ONLY', 'changed_paths': list(paths),
                'source_sha': self.HEAD, 'docs_only': True}
        test.update(overrides)
        return test

    def _completion_task(self, changed, developer, tests):
        task = self._task()
        result = {'task_id': task['task_id'], 'run_id': task['run_id'],
                  'source_sha': 'a' * 40, 'result_head': self.HEAD,
                  'changed_paths': list(changed), 'diff_sha256': 'd' * 64}
        if developer is not _NO_DEVELOPER_PATHS:
            result['developer_changed_paths'] = (
                list(developer) if type(developer) is list else developer)
        task['completion'] = {
            'state': 'WAITING_PM', 'executor_task_id': 'executor-docs-1',
            'reviewer_task_id': None,
            'evidence': {
                'task_id': task['task_id'], 'run_id': task['run_id'],
                'contract_digest': task['task_binding']['contract_digest'],
                'execution_mode': 'DIRECT_D6', 'result': result, 'tests': tests,
                'review_pr_ci': {'stage': 'EXECUTOR_RESULT',
                                 'formal_review_state': 'NOT_STARTED',
                                 'ci_state': 'NOT_STARTED'},
            },
            'missing_gates': [], 'pm_dispatch': 'NOT_STARTED', 'pm_response': None,
            'pm_recommendation': None, 'pm_usage': None, 'pm_error_code': None,
        }
        return task

    def save(self, changed, developer, tests):
        task = self._completion_task(changed, developer, tests)
        self.store.create(task)
        return self.store.get(task['task_id'])

    def assert_refused(self, changed, developer, tests):
        task = self._completion_task(changed, developer, tests)
        with self.assertRaises(LLMToolError) as caught:
            self.store.create(task)
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_INVALID')
        self.assertNotIn(task['task_id'], self.store.task_ids())

    def test_main_merge_paths_do_not_fail_docs_only_completion(self):
        saved = self.save(self.MERGED, self.DOCS, [self.docs_test(self.DOCS)])
        result = saved['completion']['evidence']['result']
        # changed_paths keeps the full source-to-head diff.
        self.assertEqual(result['changed_paths'], self.MERGED)
        self.assertEqual(result['developer_changed_paths'], self.DOCS)
        self.assertEqual(saved['completion']['evidence']['tests'],
                         [self.docs_test(self.DOCS)])

    def test_developer_code_path_still_fails_docs_only_completion(self):
        for developer in (['docs/guide.md', 'tools/llmtool/llmtool/cli.py'],
                          ['tools/llmtool/llmtool/task_store.py']):
            with self.subTest(developer=developer):
                self.assert_refused(self.MERGED, developer, [self.docs_test(developer)])

    def test_docs_only_test_paths_must_equal_developer_paths(self):
        for test_paths in (self.MERGED, self.DOCS[:1], [], ['docs/other.md']):
            with self.subTest(test_paths=test_paths):
                self.assert_refused(self.MERGED, self.DOCS, [self.docs_test(test_paths)])

    def test_docs_only_flag_and_source_sha_are_still_checked(self):
        for overrides in ({'docs_only': False}, {'docs_only': None},
                          {'docs_only': 'true'}, {'source_sha': 'e' * 40},
                          {'source_sha': None}):
            with self.subTest(overrides=overrides):
                self.assert_refused(self.MERGED, self.DOCS,
                                    [self.docs_test(self.DOCS, **overrides)])
        test = self.docs_test(self.DOCS)
        del test['docs_only']
        self.assert_refused(self.MERGED, self.DOCS, [test])

    # A present developer_changed_paths must be a non-empty list of unique,
    # non-empty strings. Membership in changed_paths is not required.
    MALFORMED_DEVELOPER = ([], None, 'docs/guide.md', ['docs/guide.md', 1],
                           ['docs/guide.md', ''], ['docs/guide.md', 'docs/guide.md'])

    def test_invalid_developer_changed_paths_make_the_record_invalid(self):
        for developer in self.MALFORMED_DEVELOPER:
            with self.subTest(developer=developer):
                tests = [self.docs_test(developer if type(developer) is list
                                        else self.DOCS)]
                self.assert_refused(self.MERGED, developer, tests)

    def test_record_without_developer_paths_keeps_changed_paths_rule(self):
        saved = self.save(self.DOCS, _NO_DEVELOPER_PATHS, [self.docs_test(self.DOCS)])
        self.assertNotIn('developer_changed_paths',
                         saved['completion']['evidence']['result'])
        # Without the key a main-merge .py file in changed_paths still fails.
        self.assert_refused(self.MERGED, _NO_DEVELOPER_PATHS,
                            [self.docs_test(self.MERGED)])
        self.assert_refused(self.MERGED, _NO_DEVELOPER_PATHS,
                            [self.docs_test(self.DOCS)])

    def test_executed_test_rule_is_unchanged_with_developer_paths(self):
        developer = ['docs/guide.md', 'tools/llmtool/llmtool/cli.py']
        executed = {'status': 'PASS', 'source_sha': self.HEAD, 'exit_code': 0}
        saved = self.save(self.MERGED, developer, [executed])
        self.assertEqual(saved['completion']['evidence']['tests'], [executed])
        for overrides in ({'exit_code': 1}, {'source_sha': 'e' * 40}):
            with self.subTest(overrides=overrides):
                self.assert_refused(self.MERGED, developer, [dict(executed, **overrides)])

    def test_invalid_developer_paths_fail_with_executed_tests_only(self):
        # Codex review v1: a present developer_changed_paths is checked in
        # every completion record, not only when a docs-only test exists.
        executed = {'status': 'PASS', 'source_sha': self.HEAD, 'exit_code': 0}
        for developer in self.MALFORMED_DEVELOPER:
            with self.subTest(developer=developer):
                self.assert_refused(self.MERGED, developer, [executed])
        saved = self.save(self.MERGED, _NO_DEVELOPER_PATHS, [executed])
        self.assertEqual(saved['completion']['evidence']['tests'], [executed])
        self.assertNotIn('developer_changed_paths',
                         saved['completion']['evidence']['result'])

    def test_malformed_developer_paths_fail_with_docs_only_test(self):
        for developer in self.MALFORMED_DEVELOPER:
            with self.subTest(developer=developer):
                self.assert_refused(self.MERGED, developer, [self.docs_test(self.DOCS)])

    def test_changed_paths_must_be_a_list_when_developer_paths_present(self):
        executed = {'status': 'PASS', 'source_sha': self.HEAD, 'exit_code': 0}
        for changed in (None, 'docs/guide.md', {'docs/guide.md': True}):
            with self.subTest(changed=changed):
                task = self._completion_task([], self.DOCS, [executed])
                task['completion']['evidence']['result']['changed_paths'] = changed
                with self.assertRaises(LLMToolError) as caught:
                    self.store.create(task)
                self.assertEqual(caught.exception.task_store_error_code,
                                 'TASK_STORE_INVALID')
                self.assertNotIn(task['task_id'], self.store.task_ids())

    def test_attributed_path_outside_changed_paths_is_saved(self):
        # #1177 attribution can name a path that is not in the net diff, for
        # example a rename-conflict source or a file added and later deleted.
        developer = ['docs/guide.md', 'docs/other.md']
        changed = ['docs/guide.md']
        executed = {'status': 'PASS', 'source_sha': self.HEAD, 'exit_code': 0}
        for tests in ([executed], [self.docs_test(developer)]):
            with self.subTest(status=tests[0]['status']):
                saved = self.save(changed, developer, tests)
                result = saved['completion']['evidence']['result']
                self.assertEqual(result['changed_paths'], changed)
                self.assertEqual(result['developer_changed_paths'], developer)
                self.assertEqual(saved['completion']['evidence']['tests'], tests)
        # The docs-only test still compares with the attributed paths only.
        self.assert_refused(changed, developer, [self.docs_test(changed)])

    def test_legacy_docs_only_record_is_readable_but_not_writable(self):
        # A legacy record whose NOT_APPLICABLE_DOCS_ONLY test matched
        # changed_paths rather than developer_changed_paths is accepted on read
        # through get(), but cannot be created or updated.
        task = self._completion_task(self.DOCS, ['docs/attributed.md'],
                                     [self.docs_test(self.DOCS)])
        self.store._ensure_root()
        path = self.store.root / f"{task['task_id']}.json"
        task['record_sha256'] = task_store_module._digest(task)
        path.write_bytes(task_store_module._json_bytes(task))
        os.chmod(path, 0o600)

        # get() reads it through lenient validation
        loaded = self.store.get(task['task_id'])
        self.assertEqual(loaded['task_id'], task['task_id'])

        # Writing that record fails strict validation
        with self.assertRaises(LLMToolError) as caught:
            self.store.update(task['task_id'], response='Updated response')
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_INVALID')

        # Writing a new record with that shape fails
        new_task = self._completion_task(self.DOCS, ['docs/attributed.md'],
                                         [self.docs_test(self.DOCS)])
        with self.assertRaises(LLMToolError) as caught:
            self.store.create(new_task)
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_INVALID')

        # A docs-only test whose paths include a non-.md file still fails on read
        bad_paths = ['docs/guide.md', 'tools/script.py']
        bad_task = self._completion_task(bad_paths, ['docs/attributed.md'],
                                         [self.docs_test(bad_paths)])
        bad_path = self.store.root / f"{bad_task['task_id']}.json"
        bad_task['record_sha256'] = task_store_module._digest(bad_task)
        bad_path.write_bytes(task_store_module._json_bytes(bad_task))
        os.chmod(bad_path, 0o600)

        with self.assertRaises(LLMToolError) as caught:
            self.store.get(bad_task['task_id'])
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_INVALID')


if __name__ == '__main__':
    unittest.main()
