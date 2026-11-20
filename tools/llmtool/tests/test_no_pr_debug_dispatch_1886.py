"""Regression tests for #1886: no-PR AI Debug and covering RCA acceptance.

A1: a legal, completed no-PR AI Debug (RCA_CONFIRMED) recorded for a lineage is
accepted by that lineage's next continuation dispatch.
A2: a PR continuation whose family already has a covering RCA_CONFIRMED is
accepted the same way review repair accepts it, without a new Debug.
A3: refusals stay for a wrong Task or lineage, an unfinished or unconfirmed
Debug, a wrong family, HEAD or binding, a stale record, an unauthorized dispatch,
and a Debug recorded before a later counted failure (CWE-863).
A4/A5: regression tests reproduce the real cases and verify repair limits and counts.
Test data rule (Owner #763 A): no credential-shaped strings.
"""

import copy
import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llmtool.errors import LLMToolError
from llmtool.high_debug import _d6_name
from llmtool import task_submission_cli as cli


def _digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _hex32(label):
    return _digest(label)[:32]


def _hex40(label):
    return _digest(label)[:40]


class _Store:
    def __init__(self, *records):
        self._records = {record['task_id']: copy.deepcopy(record) for record in records}

    def get(self, task_id):
        if task_id not in self._records:
            raise LLMToolError('task not found')
        return copy.deepcopy(self._records[task_id])

    def completion_d6_task_ids(self, executor_task_id):
        return sorted(task_id for task_id, record in self._records.items()
                      if type(record.get('completion')) is dict and
                      record['completion'].get('executor_task_id') == executor_task_id)

    def snapshot(self):
        return copy.deepcopy(self._records)


_REPORT_TEMPLATE = """## Identity and source
Identity details.

## Symptom and reproduction
Reproduction steps.

## Root Cause
Confirmed root cause found.

## Exactly what to modify
Modifications.

## Verification
Verification steps.

## Risks and handoff
Risks and handoff details.

## Outcome/cost
RCA_CONFIRMED
cost details
"""


class NoPrDebugAndCoveringRcaDispatchTests(unittest.TestCase):
    def setUp(self):
        self.trust_patcher = patch(
            'llmtool.trust.load_trust',
            return_value=SimpleNamespace(
                repository={'slug': 'davidcatcat/davidcattoolsproject'},
                tasks_root='/tmp'
            )
        )
        self.trust_patcher.start()
        self.addCleanup(self.trust_patcher.stop)

        self.executor_d6 = 'd6-executor-task'
        self.head_1 = _hex40('head-commit-one')
        self.head_2 = _hex40('head-commit-two')

    def _make_debug_entry(self, task_id, head, family, *, status='RCA_CONFIRMED',
                          no_pr=False, attempt=1, model='gpt-6.1-sol',
                          recorded_at='2026-11-20T10:30:00Z', report_text=None):
        report = report_text or _REPORT_TEMPLATE
        if status != 'RCA_CONFIRMED' and report_text is None:
            report = report.replace('RCA_CONFIRMED', status)
        report_sha256 = _digest(report)
        d6 = _d6_name(self.executor_d6, attempt, head)
        entry = {
            'family': family,
            'failed_corrections': 0,
            'early_reason': None,
            'examined_sha': head,
            'd6': d6,
            'model_requested': model,
            'model_observed': model,
            'effort_observed': 'high',
            'status': status,
            'report_sha256': report_sha256,
            'report': report,
            'usage': {'input': 100, 'output': 100},
            'recorded_at': recorded_at,
            'rules_sha256': '0' * 64,
        }
        if no_pr:
            entry['no_pr'] = True

        event_id = hashlib.sha256(
            ('HIGH_DEBUG\0' + task_id + '\0' + head + '\0' + report_sha256).encode()
        ).hexdigest()[:32]
        session_id = 'high-debug-' + d6
        payload_sha256 = hashlib.sha256(report_sha256.encode()).hexdigest()
        event = {
            'event_id': event_id,
            'source': 'HIGH_DEBUG',
            'session_id': session_id,
            'source_sha': head,
            'payload_sha256': payload_sha256,
        }
        return entry, event

    # -- A1: Completed no-PR AI Debug accepted on continuation dispatch ----

    def test_no_pr_ai_debug_accepted_by_lineage_continuation_dispatch(self):
        pred1_task_id = _hex32('task-nopr-pred1')
        pred2_task_id = _hex32('task-nopr-pred2')
        cont_task_id = _hex32('task-nopr-cont')

        entry, event = self._make_debug_entry(
            pred2_task_id, self.head_1, 'no-pr', no_pr=True,
            recorded_at='2026-11-20T10:30:00Z')

        pred1_record = {
            'task_id': pred1_task_id,
            'developer': {
                'executor_task_id': self.executor_d6,
                'created_at': '2026-11-20T10:00:00Z',
                'state': 'DEVELOPER_RESPONDED',
            },
        }

        pred2_record = {
            'task_id': pred2_task_id,
            'continues': pred1_task_id,
            'developer': {
                'executor_task_id': self.executor_d6,
                'created_at': '2026-11-20T10:15:00Z',
                'state': 'DEVELOPER_RESPONDED',
            },
            'high_debug': [entry],
            'completion_events': [event],
        }

        cont_record = {
            'task_id': cont_task_id,
            'continues': pred2_task_id,
            'developer': {
                'executor_task_id': self.executor_d6,
                'created_at': '2026-11-20T11:00:00Z',
                'state': 'DEVELOPER_DISPATCHING',
            },
        }

        store = _Store(pred1_record, pred2_record, cont_record)
        args = SimpleNamespace(
            task_id=cont_task_id,
            executor_task=self.executor_d6,
            high_debug_task=pred2_task_id,
            high_debug_family='no-pr',
            high_debug_ref='https://github.com/davidcatcat/davidcattoolsproject/issues/1886#issuecomment-7017744285',
            repair_packet=None,
        )

        d6_binding = (
            SimpleNamespace(result_sha=self.head_1),
            SimpleNamespace(head=self.head_1)
        )
        policy = {'repair_max': 4, 'test_fail_max': 5}
        counted = [pred1_task_id, pred2_task_id]
        stamps = {
            pred1_task_id: '2026-11-20T10:00:00Z',
            pred2_task_id: '2026-11-20T10:15:00Z',
        }

        # Should complete without raising DELEGATE_HIGH_DEBUG_TASK_INVALID
        cli._check_repair_limits(
            args, store, d6_binding, policy, cont_task_id,
            counted, set(), set(), stamps[pred2_task_id], stamps=stamps)

    # -- A2: PR continuation with covering RCA accepted without new debug --

    def test_pr_continuation_covering_rca_accepted_without_new_debug_or_exact_head(self):
        root_task_id = _hex32('task-pr-root')
        pred2_task_id = _hex32('task-pr-pred2')
        cont_task_id = _hex32('task-pr-cont')

        # The covering RCA was examined at head_1
        entry, event = self._make_debug_entry(
            root_task_id, self.head_1, 'pr-2406-ec2', no_pr=False,
            recorded_at='2026-11-20T10:30:00Z')

        root_record = {
            'task_id': root_task_id,
            'completion': {'executor_task_id': self.executor_d6},
            'developer': {
                'executor_task_id': self.executor_d6,
                'created_at': '2026-11-20T10:00:00Z',
            },
            'high_debug': [entry],
            'completion_events': [event],
        }

        pred2_record = {
            'task_id': pred2_task_id,
            'continues': root_task_id,
            'completion': {'executor_task_id': self.executor_d6},
            'developer': {
                'executor_task_id': self.executor_d6,
                'created_at': '2026-11-20T10:15:00Z',
            },
        }

        cont_record = {
            'task_id': cont_task_id,
            'continues': pred2_task_id,
            'developer': {
                'executor_task_id': self.executor_d6,
                'created_at': '2026-11-20T11:00:00Z',
            },
        }

        store = _Store(root_record, pred2_record, cont_record)
        args = SimpleNamespace(
            task_id=cont_task_id,
            executor_task=self.executor_d6,
            high_debug_task=root_task_id,
            high_debug_family='pr-2406-ec2',
            high_debug_ref='https://github.com/davidcatcat/davidcattoolsproject/issues/2406#issuecomment-7015510771',
            repair_packet=None,
        )

        # Notice current D6 head has moved to head_2 (head_2 != head_1)
        d6_binding = (
            SimpleNamespace(result_sha=self.head_2),
            SimpleNamespace(head=self.head_2)
        )
        policy = {'repair_max': 4, 'test_fail_max': 5}
        counted = [root_task_id, pred2_task_id]
        stamps = {
            root_task_id: '2026-11-20T10:00:00Z',
            pred2_task_id: '2026-11-20T10:15:00Z',
        }

        # Must accept covering RCA without demanding a new Debug on head_2
        cli._check_repair_limits(
            args, store, d6_binding, policy, cont_task_id,
            counted, set(), set(), stamps[pred2_task_id], stamps=stamps)

    # -- A3: Refusals stay for invalid conditions -----------------------

    def test_refusal_for_unrelated_lineage_task(self):
        pred1_task_id = _hex32('task-pred1')
        pred2_task_id = _hex32('task-pred2')
        cont_task_id = _hex32('task-cont')
        unrelated_task_id = _hex32('task-unrelated')

        entry, event = self._make_debug_entry(
            unrelated_task_id, self.head_1, 'no-pr', no_pr=True,
            recorded_at='2026-11-20T10:30:00Z')

        unrelated_record = {
            'task_id': unrelated_task_id,
            'developer': {'executor_task_id': self.executor_d6},
            'high_debug': [entry],
            'completion_events': [event],
        }
        pred1_record = {
            'task_id': pred1_task_id,
            'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T10:00:00Z'},
        }
        pred2_record = {
            'task_id': pred2_task_id,
            'continues': pred1_task_id,
            'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T10:15:00Z'},
        }
        cont_record = {
            'task_id': cont_task_id,
            'continues': pred2_task_id,
            'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T11:00:00Z'},
        }

        store = _Store(unrelated_record, pred1_record, pred2_record, cont_record)
        args = SimpleNamespace(
            task_id=cont_task_id,
            executor_task=self.executor_d6,
            high_debug_task=unrelated_task_id,
            high_debug_family='no-pr',
            high_debug_ref='https://github.com/davidcatcat/davidcattoolsproject/issues/1886#issuecomment-7017744285',
            repair_packet=None,
        )
        d6_binding = (SimpleNamespace(result_sha=self.head_1), SimpleNamespace(head=self.head_1))
        policy = {'repair_max': 4, 'test_fail_max': 5}
        counted = [pred1_task_id, pred2_task_id]
        stamps = {pred1_task_id: '2026-11-20T10:00:00Z', pred2_task_id: '2026-11-20T10:15:00Z'}

        with self.assertRaises(LLMToolError) as ctx:
            cli._check_repair_limits(
                args, store, d6_binding, policy, cont_task_id,
                counted, set(), set(), stamps[pred2_task_id], stamps=stamps)
        self.assertIn('DELEGATE_HIGH_DEBUG_TASK_INVALID', str(ctx.exception))

    def test_refusal_for_unconfirmed_debug_status(self):
        pred1_task_id = _hex32('task-pred1')
        pred2_task_id = _hex32('task-pred2')
        cont_task_id = _hex32('task-cont')

        entry, event = self._make_debug_entry(
            pred2_task_id, self.head_1, 'no-pr', status='RCA_UNCONFIRMED', no_pr=True,
            recorded_at='2026-11-20T10:30:00Z')

        pred1_record = {
            'task_id': pred1_task_id,
            'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T10:00:00Z'},
        }
        pred2_record = {
            'task_id': pred2_task_id,
            'continues': pred1_task_id,
            'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T10:15:00Z'},
            'high_debug': [entry],
            'completion_events': [event],
        }
        cont_record = {
            'task_id': cont_task_id,
            'continues': pred2_task_id,
            'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T11:00:00Z'},
        }

        store = _Store(pred1_record, pred2_record, cont_record)
        args = SimpleNamespace(
            task_id=cont_task_id,
            executor_task=self.executor_d6,
            high_debug_task=pred2_task_id,
            high_debug_family='no-pr',
            high_debug_ref='https://github.com/davidcatcat/davidcattoolsproject/issues/1886#issuecomment-7017744285',
            repair_packet=None,
        )
        d6_binding = (SimpleNamespace(result_sha=self.head_1), SimpleNamespace(head=self.head_1))
        policy = {'repair_max': 4, 'test_fail_max': 5}
        counted = [pred1_task_id, pred2_task_id]
        stamps = {pred1_task_id: '2026-11-20T10:00:00Z', pred2_task_id: '2026-11-20T10:15:00Z'}

        with self.assertRaises(LLMToolError) as ctx:
            cli._check_repair_limits(
                args, store, d6_binding, policy, cont_task_id,
                counted, set(), set(), stamps[pred2_task_id], stamps=stamps)
        self.assertIn('DELEGATE_HIGH_DEBUG_TASK_INVALID', str(ctx.exception))

    def test_refusal_for_wrong_family(self):
        pred1_task_id = _hex32('task-pred1')
        pred2_task_id = _hex32('task-pred2')
        cont_task_id = _hex32('task-cont')

        entry, event = self._make_debug_entry(
            pred2_task_id, self.head_1, 'expected-family', no_pr=True,
            recorded_at='2026-11-20T10:30:00Z')

        pred1_record = {
            'task_id': pred1_task_id,
            'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T10:00:00Z'},
        }
        pred2_record = {
            'task_id': pred2_task_id,
            'continues': pred1_task_id,
            'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T10:15:00Z'},
            'high_debug': [entry],
            'completion_events': [event],
        }
        cont_record = {
            'task_id': cont_task_id,
            'continues': pred2_task_id,
            'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T11:00:00Z'},
        }

        store = _Store(pred1_record, pred2_record, cont_record)
        args = SimpleNamespace(
            task_id=cont_task_id,
            executor_task=self.executor_d6,
            high_debug_task=pred2_task_id,
            high_debug_family='wrong-family',
            high_debug_ref='https://github.com/davidcatcat/davidcattoolsproject/issues/1886#issuecomment-7017744285',
            repair_packet=None,
        )
        d6_binding = (SimpleNamespace(result_sha=self.head_1), SimpleNamespace(head=self.head_1))
        policy = {'repair_max': 4, 'test_fail_max': 5}
        counted = [pred1_task_id, pred2_task_id]
        stamps = {pred1_task_id: '2026-11-20T10:00:00Z', pred2_task_id: '2026-11-20T10:15:00Z'}

        with self.assertRaises(LLMToolError) as ctx:
            cli._check_repair_limits(
                args, store, d6_binding, policy, cont_task_id,
                counted, set(), set(), stamps[pred2_task_id], stamps=stamps)
        self.assertIn('DELEGATE_HIGH_DEBUG_TASK_INVALID', str(ctx.exception))

    def test_refusal_for_head_moved_on_non_continuation(self):
        task_id = _hex32('task-direct-single')

        entry, event = self._make_debug_entry(
            task_id, self.head_1, 'family-test', no_pr=False,
            recorded_at='2026-11-20T10:30:00Z')

        record = {
            'task_id': task_id,
            'completion': {'executor_task_id': self.executor_d6},
            'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T10:00:00Z'},
            'high_debug': [entry],
            'completion_events': [event],
        }

        store = _Store(record)
        args = SimpleNamespace(
            task_id=task_id,
            executor_task=self.executor_d6,
            high_debug_task=task_id,
            high_debug_family='family-test',
            high_debug_ref='https://github.com/davidcatcat/davidcattoolsproject/issues/1886#issuecomment-7017744285',
            repair_packet=None,
        )

        # Current head is head_2, but debug was taken on head_1
        d6_binding = (
            SimpleNamespace(result_sha=self.head_2),
            SimpleNamespace(head=self.head_2)
        )
        policy = {'repair_max': 4, 'test_fail_max': 5}
        # Non-continuation repair #2 (counted len 2)
        counted = [task_id, _hex32('prior-run')]
        stamps = {task_id: '2026-11-20T10:00:00Z', _hex32('prior-run'): '2026-11-20T09:00:00Z'}

        with self.assertRaises(LLMToolError) as ctx:
            cli._check_repair_limits(
                args, store, d6_binding, policy, task_id,
                counted, set(), set(), stamps[task_id], stamps=stamps)
        self.assertIn('DELEGATE_HIGH_DEBUG_HEAD_MOVED', str(ctx.exception))

    def test_refusal_for_missing_high_debug_args(self):
        task_id = _hex32('task-no-args')
        record = {
            'task_id': task_id,
            'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T10:00:00Z'},
        }
        store = _Store(record)
        args = SimpleNamespace(
            task_id=task_id,
            executor_task=self.executor_d6,
            high_debug_task=None,
            high_debug_family=None,
            high_debug_ref=None,
            repair_packet=None,
        )
        d6_binding = (SimpleNamespace(result_sha=self.head_1), SimpleNamespace(head=self.head_1))
        policy = {'repair_max': 4, 'test_fail_max': 5}
        counted = [task_id, _hex32('prior-run')]
        stamps = {task_id: '2026-11-20T10:00:00Z', _hex32('prior-run'): '2026-11-20T09:00:00Z'}

        with self.assertRaises(LLMToolError) as ctx:
            cli._check_repair_limits(
                args, store, d6_binding, policy, task_id,
                counted, set(), set(), stamps[task_id], stamps=stamps)
        self.assertIn('DELEGATE_HIGH_DEBUG_TASK_REQUIRED', str(ctx.exception))

    def test_refusal_for_stale_debug_record(self):
        pred1_task_id = _hex32('task-pred1')
        pred2_task_id = _hex32('task-pred2')
        cont_task_id = _hex32('task-cont')

        # Recorded at 09:30:00, which is older than predecessor created at 10:00:00
        entry, event = self._make_debug_entry(
            pred2_task_id, self.head_1, 'no-pr', no_pr=True,
            recorded_at='2026-11-20T09:30:00Z')

        pred1_record = {
            'task_id': pred1_task_id,
            'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T10:00:00Z'},
        }
        pred2_record = {
            'task_id': pred2_task_id,
            'continues': pred1_task_id,
            'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T10:15:00Z'},
            'high_debug': [entry],
            'completion_events': [event],
        }
        cont_record = {
            'task_id': cont_task_id,
            'continues': pred2_task_id,
            'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T11:00:00Z'},
        }

        store = _Store(pred1_record, pred2_record, cont_record)
        args = SimpleNamespace(
            task_id=cont_task_id,
            executor_task=self.executor_d6,
            high_debug_task=pred2_task_id,
            high_debug_family='no-pr',
            high_debug_ref='https://github.com/davidcatcat/davidcattoolsproject/issues/1886#issuecomment-7017744285',
            repair_packet=None,
        )
        d6_binding = (SimpleNamespace(result_sha=self.head_1), SimpleNamespace(head=self.head_1))
        policy = {'repair_max': 4, 'test_fail_max': 5}
        counted = [pred1_task_id, pred2_task_id]
        stamps = {pred1_task_id: '2026-11-20T10:00:00Z', pred2_task_id: '2026-11-20T10:15:00Z'}

        with self.assertRaises(LLMToolError) as ctx:
            cli._check_repair_limits(
                args, store, d6_binding, policy, cont_task_id,
                counted, set(), set(), stamps[pred2_task_id], stamps=stamps)
        self.assertIn('DELEGATE_HIGH_DEBUG_TASK_INVALID', str(ctx.exception))

    def test_refusal_for_invalid_high_debug_ref(self):
        pred1_task_id = _hex32('task-pred1')
        pred2_task_id = _hex32('task-pred2')
        cont_task_id = _hex32('task-cont')

        entry, event = self._make_debug_entry(
            pred2_task_id, self.head_1, 'no-pr', no_pr=True,
            recorded_at='2026-11-20T10:30:00Z')

        pred1_record = {
            'task_id': pred1_task_id,
            'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T10:00:00Z'},
        }
        pred2_record = {
            'task_id': pred2_task_id,
            'continues': pred1_task_id,
            'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T10:15:00Z'},
            'high_debug': [entry],
            'completion_events': [event],
        }
        cont_record = {
            'task_id': cont_task_id,
            'continues': pred2_task_id,
            'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T11:00:00Z'},
        }

        store = _Store(pred1_record, pred2_record, cont_record)
        args = SimpleNamespace(
            task_id=cont_task_id,
            executor_task=self.executor_d6,
            high_debug_task=pred2_task_id,
            high_debug_family='no-pr',
            high_debug_ref='https://github.com/other-org/other-repo/issues/1886#issuecomment-7017744285',
            repair_packet=None,
        )
        d6_binding = (SimpleNamespace(result_sha=self.head_1), SimpleNamespace(head=self.head_1))
        policy = {'repair_max': 4, 'test_fail_max': 5}
        counted = [pred1_task_id, pred2_task_id]
        stamps = {pred1_task_id: '2026-11-20T10:00:00Z', pred2_task_id: '2026-11-20T10:15:00Z'}

        with self.assertRaises(LLMToolError) as ctx:
            cli._check_repair_limits(
                args, store, d6_binding, policy, cont_task_id,
                counted, set(), set(), stamps[pred2_task_id], stamps=stamps)
        self.assertIn('DELEGATE_HIGH_DEBUG_REF_INVALID', str(ctx.exception))

    def test_refusal_for_wrong_d6_binding(self):
        pred1_task_id = _hex32('task-pred1')
        pred2_task_id = _hex32('task-pred2')
        cont_task_id = _hex32('task-cont')

        entry, event = self._make_debug_entry(
            pred2_task_id, self.head_1, 'no-pr', no_pr=True,
            recorded_at='2026-11-20T10:30:00Z')
        # Entry points to foreign D6 name
        entry['d6'] = _d6_name('other-executor-task', 1, self.head_1)

        pred1_record = {
            'task_id': pred1_task_id,
            'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T10:00:00Z'},
        }
        pred2_record = {
            'task_id': pred2_task_id,
            'continues': pred1_task_id,
            'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T10:15:00Z'},
            'high_debug': [entry],
            'completion_events': [event],
        }
        cont_record = {
            'task_id': cont_task_id,
            'continues': pred2_task_id,
            'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T11:00:00Z'},
        }

        store = _Store(pred1_record, pred2_record, cont_record)
        args = SimpleNamespace(
            task_id=cont_task_id,
            executor_task=self.executor_d6,
            high_debug_task=pred2_task_id,
            high_debug_family='no-pr',
            high_debug_ref='https://github.com/davidcatcat/davidcattoolsproject/issues/1886#issuecomment-7017744285',
            repair_packet=None,
        )
        d6_binding = (SimpleNamespace(result_sha=self.head_1), SimpleNamespace(head=self.head_1))
        policy = {'repair_max': 4, 'test_fail_max': 5}
        counted = [pred1_task_id, pred2_task_id]
        stamps = {pred1_task_id: '2026-11-20T10:00:00Z', pred2_task_id: '2026-11-20T10:15:00Z'}

        with self.assertRaises(LLMToolError) as ctx:
            cli._check_repair_limits(
                args, store, d6_binding, policy, cont_task_id,
                counted, set(), set(), stamps[pred2_task_id], stamps=stamps)
        self.assertIn('DELEGATE_HIGH_DEBUG_TASK_INVALID', str(ctx.exception))

    def test_refusal_for_debug_recorded_before_later_counted_failure(self):
        """CWE-863: a Debug recorded before a later counted failure must not authorize repairing it."""
        pred1_task_id = _hex32('task-order-pred1')
        pred2_task_id = _hex32('task-order-pred2')
        cont_task_id = _hex32('task-order-cont')

        # Debug was recorded at 10:30:00Z (after pred1 at 10:00:00Z, but before pred2 at 11:00:00Z)
        entry, event = self._make_debug_entry(
            pred1_task_id, self.head_1, 'no-pr', no_pr=True,
            recorded_at='2026-11-20T10:30:00Z')

        pred1_record = {
            'task_id': pred1_task_id,
            'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T10:00:00Z'},
            'high_debug': [entry],
            'completion_events': [event],
        }
        pred2_record = {
            'task_id': pred2_task_id,
            'continues': pred1_task_id,
            'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T11:00:00Z'},
        }
        cont_record = {
            'task_id': cont_task_id,
            'continues': pred2_task_id,
            'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T11:30:00Z'},
        }

        store = _Store(pred1_record, pred2_record, cont_record)
        args = SimpleNamespace(
            task_id=cont_task_id,
            executor_task=self.executor_d6,
            high_debug_task=pred1_task_id,
            high_debug_family='no-pr',
            high_debug_ref='https://github.com/davidcatcat/davidcattoolsproject/issues/1886#issuecomment-7017744285',
            repair_packet=None,
        )
        d6_binding = (SimpleNamespace(result_sha=self.head_1), SimpleNamespace(head=self.head_1))
        policy = {'repair_max': 4, 'test_fail_max': 5}
        counted = [pred1_task_id, pred2_task_id]
        stamps = {pred1_task_id: '2026-11-20T10:00:00Z', pred2_task_id: '2026-11-20T11:00:00Z'}

        # Debug at 10:30:00Z is before pred2 at 11:00:00Z; must be refused
        with self.assertRaises(LLMToolError) as ctx:
            cli._check_repair_limits(
                args, store, d6_binding, policy, cont_task_id,
                counted, set(), set(), stamps[pred2_task_id], stamps=stamps)
        self.assertIn('DELEGATE_HIGH_DEBUG_TASK_INVALID', str(ctx.exception))

    def test_count_greater_than_two_covering_rca_ordering(self):
        """Repairs 3+ accept covering RCA newer than times[1], but refuse one recorded earlier."""
        root_task_id = _hex32('task-root')
        run2_task_id = _hex32('task-run2')
        run3_task_id = _hex32('task-run3')
        cont_task_id = _hex32('task-cont-3')

        # Debug recorded at 10:10:00Z (before run2 at 10:15:00Z)
        early_entry, early_event = self._make_debug_entry(
            root_task_id, self.head_1, 'family-cov', no_pr=False,
            recorded_at='2026-11-20T10:10:00Z')

        # Covering Debug recorded at 10:20:00Z (after run2 at 10:15:00Z, before run3 at 10:45:00Z)
        covering_entry, covering_event = self._make_debug_entry(
            root_task_id, self.head_1, 'family-cov', no_pr=False,
            recorded_at='2026-11-20T10:20:00Z')

        def build_store(entry, event):
            root_rec = {
                'task_id': root_task_id,
                'completion': {'executor_task_id': self.executor_d6},
                'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T10:00:00Z'},
                'high_debug': [entry],
                'completion_events': [event],
            }
            run2_rec = {
                'task_id': run2_task_id,
                'continues': root_task_id,
                'completion': {'executor_task_id': self.executor_d6},
                'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T10:15:00Z'},
            }
            run3_rec = {
                'task_id': run3_task_id,
                'continues': run2_task_id,
                'completion': {'executor_task_id': self.executor_d6},
                'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T10:45:00Z'},
            }
            cont_rec = {
                'task_id': cont_task_id,
                'continues': run3_task_id,
                'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T11:00:00Z'},
            }
            return _Store(root_rec, run2_rec, run3_rec, cont_rec)

        d6_binding = (SimpleNamespace(result_sha=self.head_2), SimpleNamespace(head=self.head_2))
        policy = {'repair_max': 4, 'test_fail_max': 5}
        counted = [root_task_id, run2_task_id, run3_task_id]
        stamps = {
            root_task_id: '2026-11-20T10:00:00Z',
            run2_task_id: '2026-11-20T10:15:00Z',
            run3_task_id: '2026-11-20T10:45:00Z',
        }
        args = SimpleNamespace(
            task_id=cont_task_id,
            executor_task=self.executor_d6,
            high_debug_task=root_task_id,
            high_debug_family='family-cov',
            high_debug_ref='https://github.com/davidcatcat/davidcattoolsproject/issues/1886#issuecomment-7017744285',
            repair_packet=None,
        )

        # Early debug (10:10:00Z <= times[1] 10:15:00Z) must refuse
        early_store = build_store(early_entry, early_event)
        with self.assertRaises(LLMToolError) as ctx:
            cli._check_repair_limits(
                args, early_store, d6_binding, policy, cont_task_id,
                counted, set(), set(), stamps[run3_task_id], stamps=stamps)
        self.assertIn('DELEGATE_HIGH_DEBUG_TASK_INVALID', str(ctx.exception))

        # Covering debug (10:20:00Z > times[1] 10:15:00Z) covers repair #3 and passes
        covering_store = build_store(covering_entry, covering_event)
        cli._check_repair_limits(
            args, covering_store, d6_binding, policy, cont_task_id,
            counted, set(), set(), stamps[run3_task_id], stamps=stamps)

    # -- A5: Repair limit reaches repair_max ------------------------------

    def test_repair_limit_reached_refuses_even_with_rca(self):
        task_id = _hex32('task-limit-test')
        record = {
            'task_id': task_id,
            'developer': {'executor_task_id': self.executor_d6, 'created_at': '2026-11-20T10:00:00Z'},
        }
        store = _Store(record)
        args = SimpleNamespace(
            task_id=task_id,
            executor_task=self.executor_d6,
            high_debug_task=task_id,
            high_debug_family='test-family',
            high_debug_ref='https://github.com/davidcatcat/davidcattoolsproject/issues/1886#issuecomment-7017744285',
            repair_packet=None,
        )
        d6_binding = (SimpleNamespace(result_sha=self.head_1), SimpleNamespace(head=self.head_1))
        policy = {'repair_max': 4, 'test_fail_max': 5}
        # 5 counted review runs: 1 + repair_max = 5 -> reaches limit
        counted = [_hex32(f'run-{i}') for i in range(5)]

        with self.assertRaises(LLMToolError) as ctx:
            cli._check_repair_limits(
                args, store, d6_binding, policy, task_id,
                counted, set(), set(), '2026-11-20T10:00:00Z')
        self.assertIn('DELEGATE_REPAIR_LIMIT_REACHED', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()

