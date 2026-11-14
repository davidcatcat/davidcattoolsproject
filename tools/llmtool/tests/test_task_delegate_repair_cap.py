"""Tests for the delegate repair cap and the root cause that repair #2 needs.

#1502 WP-D D7: the limits come from policy.json, the delegate always refuses
at or over a limit, and review repair #2 on a D6 needs a confirmed root cause
bound by the High Debug arguments or by the repair packet.
"""

import argparse
import hashlib
import io
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import repair_policy
from llmtool import task_submission_cli
from llmtool.errors import LLMToolError


_POLICY = repair_policy.load_policy()
# Prior review runs on a D6 when the next run is repair #2, the last one the
# review limit allows (1 + repair_max runs in total).
_REPAIR_TWO = _POLICY['repair_max']
_REVIEW_LIMIT = 1 + _POLICY['repair_max']
_TEST_FAIL_MAX = _POLICY['test_fail_max']

_CURRENT_TASK = 'f' * 32
_HIGH_DEBUG_TASK = 'e' * 32
_EXECUTOR_TASK = 'executor-d6'
_HIGH_DEBUG_REF = 'https://github.com/example/project/issues/12/comments/34'
_HEAD = 'a' * 40


class _Store:
    def __init__(self, *, count, diagnosis=None, unreadable=False, scan_fails=False):
        self.records = {
            f'{number:032x}': {
                'completion': {'executor_task_id': _EXECUTOR_TASK},
                'developer': {'state': 'DEVELOPER_RESPONDED',
                              'created_at': '2026-11-04T00:00:00Z'},
            }
            for number in range(count)
        }
        # #1502 WP-D phase 2: the cap reads the delegated Task's chain, so the
        # store holds the delegated Task, as a real store always does.
        self.records[_CURRENT_TASK] = {'task_id': _CURRENT_TASK, 'developer': None,
                                       'completion': None}
        if diagnosis is not None:
            self.records[_HIGH_DEBUG_TASK] = diagnosis
        elif unreadable:
            # Keep the unreadable Task addressable without persisting a None
            # record; get() raises for it below.
            self.records[_HIGH_DEBUG_TASK] = {}
        self.unreadable = unreadable
        self.scan_fails = scan_fails
        self.scans = 0
        # Every Task write attempt; the cap check must never write.
        self.writes = []

    def task_ids(self):
        return list(self.records)

    def completion_d6_task_ids(self, executor_task_id):
        self.scans += 1
        if self.scan_fails:
            raise OSError('scan failed')
        matching = []
        for task_id, record in self.records.items():
            completion = record.get('completion')
            if (isinstance(completion, dict) and
                    completion.get('executor_task_id') == executor_task_id):
                matching.append(task_id)
        return sorted(matching)

    def get(self, task_id):
        if task_id == _HIGH_DEBUG_TASK and self.unreadable:
            raise OSError('unreadable')
        return self.records[task_id]

    def update(self, task_id, **changes):
        self.writes.append(('update', task_id))

    def compare_and_update(self, task_id, **changes):
        self.writes.append(('compare_and_update', task_id))

    def create(self, record):
        self.writes.append(('create', record.get('task_id')))


# A report with every required section whose Outcome/cost status line says
# RCA_CONFIRMED, as run_high_debug requires of a confirmed diagnosis.
_REPORT = '\n'.join((
    '## Identity and source', 'Synthetic fixture.',
    '## Symptom and reproduction', 'Synthetic fixture.',
    '## Root Cause', 'Confirmed root cause.',
    '## Exactly what to modify', 'Synthetic fixture.',
    '## Verification', 'Synthetic fixture.',
    '## Risks and handoff', 'Synthetic fixture.',
    '## Outcome/cost', 'RCA_CONFIRMED', 'Cost: UNKNOWN',
)) + '\n'
_REPORT_SHA256 = hashlib.sha256(_REPORT.encode('utf-8')).hexdigest()
_DIAGNOSIS_RUN = 'd' * 32


def _entry(*, executor_task=_EXECUTOR_TASK, status='RCA_CONFIRMED',
           examined_sha=_HEAD, family='pr-938', recorded_at='2026-11-04T00:00:01Z',
           report=_REPORT, report_sha256=None, model_observed='gpt-6.1-sol',
           model_requested='gpt-6.1-sol'):
    from llmtool.high_debug import _d6_name

    return {
        'status': status,
        'family': family,
        'failed_corrections': 2,
        'early_reason': None,
        'd6': _d6_name(executor_task, 1, examined_sha),
        'examined_sha': examined_sha,
        'model_requested': model_requested,
        'model_observed': model_observed,
        'effort_observed': 'high',
        'report': report,
        'report_sha256': (hashlib.sha256(report.encode('utf-8')).hexdigest()
                          if report_sha256 is None else report_sha256),
        'usage': 'UNKNOWN',
        'recorded_at': recorded_at,
    }


def _event(entry, *, sequence=0, **overrides):
    """The HIGH_DEBUG completion event run_high_debug records for ``entry``."""
    report_sha256 = entry['report_sha256']
    event = {
        'event_id': hashlib.sha256(
            ('HIGH_DEBUG\0' + _HIGH_DEBUG_TASK + '\0' + entry['examined_sha'] +
             '\0' + report_sha256).encode()).hexdigest()[:32],
        'task_id': _HIGH_DEBUG_TASK, 'run_id': _DIAGNOSIS_RUN,
        'session_id': 'high-debug-' + entry['d6'], 'source': 'HIGH_DEBUG',
        'source_sha': entry['examined_sha'], 'sequence': sequence,
        'payload_sha256': hashlib.sha256(report_sha256.encode()).hexdigest(),
        'state': 'PENDING',
    }
    event.update(overrides)
    return event


def _tombstone(entry, **overrides):
    event = _event(entry)
    tombstone = {'event_id': event['event_id'], 'source': event['source'],
                 'session_id': event['session_id'], 'sequence': event['sequence'],
                 'acked_at': '2026-11-04T00:00:02Z'}
    tombstone.update(overrides)
    return tombstone


def _diagnosis(entries, *, executor_task=_EXECUTOR_TASK, events=None,
               tombstones=None):
    """The original Task holding ``entries`` and, by default, one event each."""
    record = {
        'task_id': _HIGH_DEBUG_TASK, 'run_id': _DIAGNOSIS_RUN,
        'completion': (None if executor_task is None else
                       {'executor_task_id': executor_task}),
        'high_debug': entries,
        'completion_events': (
            [_event(entry, sequence=index) for index, entry in enumerate(entries)]
            if events is None else events),
    }
    if tombstones is not None:
        record['completion_event_tombstones'] = tombstones
    return record


def _args(**overrides):
    values = {
        'task_id': _CURRENT_TASK,
        'executor_task': _EXECUTOR_TASK,
        'high_debug_ref': _HIGH_DEBUG_REF,
        'high_debug_task': _HIGH_DEBUG_TASK,
        'high_debug_family': 'pr-938',
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _no_high_debug(**overrides):
    return _args(high_debug_ref=None, high_debug_task=None,
                 high_debug_family=None, **overrides)


def _patch_trust_and_binding(test, binding=(None, SimpleNamespace(head=_HEAD))):
    for target, kwargs in (
            ((task_submission_cli.trust, 'load_trust'),
             {'return_value': SimpleNamespace(
                 repository={'slug': 'example/project'})}),
            ((task_submission_cli, '_delegate_d6_binding'),
             {'return_value': binding})):
        started = patch.object(*target, **kwargs)
        started.start()
        test.addCleanup(started.stop)


class PolicyLimitTests(unittest.TestCase):
    """The limits come only from policy.json; a read failure refuses first."""

    def setUp(self):
        _patch_trust_and_binding(self)

    def test_policy_read_failure_refuses_before_any_store_access(self):
        for failure in (LLMToolError('POLICY_INVALID'), OSError('unreadable')):
            store = _Store(count=0, diagnosis=_diagnosis([_entry()]))
            with self.subTest(failure=failure), \
                    patch.object(task_submission_cli.repair_policy, 'load_policy',
                                 side_effect=failure):
                with self.assertRaisesRegex(
                        LLMToolError,
                        '^DELEGATE_REPAIR_COUNT_UNAVAILABLE: Task ' + _CURRENT_TASK + '$'):
                    task_submission_cli._check_delegate_repair_cap(_args(), store)
            self.assertEqual(store.scans, 0)
            self.assertEqual(store.writes, [])

    def test_review_limit_follows_repair_max(self):
        policy = dict(_POLICY, repair_max=3)
        with patch.object(task_submission_cli.repair_policy, 'load_policy',
                          return_value=policy):
            # Prior runs 3: repair #3 of at most 3, with a confirmed root cause.
            store = _Store(count=3, diagnosis=_diagnosis([_entry()]))
            task_submission_cli._check_delegate_repair_cap(_args(), store)
            self.assertEqual(store.writes, [])
            # Prior runs 4: the limit, even with a valid High Debug.
            store = _Store(count=4, diagnosis=_diagnosis([_entry()]))
            with self.assertRaisesRegex(LLMToolError, '^DELEGATE_REPAIR_LIMIT_REACHED$'):
                task_submission_cli._check_delegate_repair_cap(_args(), store)
            self.assertEqual(store.writes, [])

    def test_ec2_limit_follows_test_fail_max(self):
        policy = dict(_POLICY, test_fail_max=1)
        current = _ec2_claim(_CURRENT_TASK)
        with patch.object(task_submission_cli.repair_policy, 'load_policy',
                          return_value=policy):
            task_submission_cli._check_delegate_repair_cap(
                _no_high_debug(), _split_store(current=current))
            with self.assertRaisesRegex(LLMToolError, '^DELEGATE_REPAIR_LIMIT_REACHED$'):
                task_submission_cli._check_delegate_repair_cap(
                    _no_high_debug(), _split_store(ec2_runs=1, current=current))


class DelegateRepairCapTests(unittest.TestCase):
    def setUp(self):
        _patch_trust_and_binding(self)

    def _at_repair_two(self, **kwargs):
        return _Store(count=_REPAIR_TWO, **kwargs)

    def test_original_run_and_repair_one_ignore_high_debug_options(self):
        for count in (0, 1):
            store = _Store(count=count)
            with self.subTest(count=count):
                task_submission_cli._check_delegate_repair_cap(
                    _args(high_debug_task='invalid', high_debug_ref='bad'), store)
            self.assertEqual(store.writes, [])

    def test_unrelated_unreadable_record_does_not_refuse(self):
        # Before repair #2 the High Debug Task named by the arguments is never
        # read: one the store does not hold does not refuse.
        store = _Store(count=1, unreadable=True)
        store.records.pop(_HIGH_DEBUG_TASK)
        task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.writes, [])

    def test_a_stored_record_that_cannot_be_read_is_a_count_in_doubt(self):
        # #1502 WP-D phase 2: any stored Task may continue this one, so the
        # chain scan reads every stored Task; a read that fails is a real
        # doubt, UNKNOWN, and refuses as at the cap before any write.
        store = _Store(count=1, unreadable=True)
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_REPAIR_LIMIT_REACHED$'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.writes, [])

    def test_failing_scan_refuses(self):
        store = _Store(count=0, scan_fails=True)
        with self.assertRaisesRegex(
                LLMToolError, 'DELEGATE_REPAIR_COUNT_UNAVAILABLE'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.writes, [])

    def test_unreadable_diagnosis_that_may_be_counted_fails_closed(self):
        store = _Store(count=1, unreadable=True)
        store.records.pop(_HIGH_DEBUG_TASK, None)
        store.records[_HIGH_DEBUG_TASK] = {
            'completion': {'executor_task_id': _EXECUTOR_TASK},
            'developer': {'created_at': '2026-11-04T00:00:00Z'},
        }

        with self.assertRaisesRegex(
                LLMToolError, 'DELEGATE_REPAIR_COUNT_UNAVAILABLE'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.writes, [])

    def test_d6_record_unreadable_refuses_with_count_unavailable(self):
        store = _Store(count=0)
        unreadable_id = '1' * 32
        store.records[unreadable_id] = {
            'completion': {'executor_task_id': _EXECUTOR_TASK},
        }
        original_get = store.get

        def failing_get(task_id):
            if task_id == unreadable_id:
                raise OSError('unreadable')
            return original_get(task_id)

        store.get = failing_get
        with self.assertRaisesRegex(
                LLMToolError, 'DELEGATE_REPAIR_COUNT_UNAVAILABLE'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.writes, [])

    def test_repair_two_without_any_high_debug_is_task_required(self):
        store = self._at_repair_two(diagnosis=_diagnosis([_entry()]))
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_HIGH_DEBUG_TASK_REQUIRED$'):
            task_submission_cli._check_delegate_repair_cap(_no_high_debug(), store)
        self.assertEqual(store.writes, [])

    def test_missing_high_debug_task_is_refused(self):
        store = self._at_repair_two(diagnosis=_diagnosis([_entry()]))

        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_TASK_REQUIRED'):
            task_submission_cli._check_delegate_repair_cap(
                _args(high_debug_task=None), store)
        self.assertEqual(store.writes, [])

    def test_unreadable_high_debug_task_is_refused(self):
        # A High Debug Task that cannot be read and that the store does not
        # list refuses as invalid.
        store = self._at_repair_two(diagnosis=None, unreadable=True)
        store.records.pop(_HIGH_DEBUG_TASK)

        with self.assertRaisesRegex(
                LLMToolError, 'DELEGATE_HIGH_DEBUG_TASK_INVALID'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.writes, [])
        # #1502 WP-D phase 2: a listed one is read by the chain scan first; its
        # failed read is a count in doubt and refuses as at the cap.
        store = self._at_repair_two(diagnosis=None, unreadable=True)
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_REPAIR_LIMIT_REACHED$'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.writes, [])

    def test_task_without_high_debug_entry_is_refused(self):
        store = self._at_repair_two(diagnosis=_diagnosis([]))

        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_TASK_INVALID'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)

    def test_last_high_debug_entry_must_be_confirmed(self):
        store = self._at_repair_two(diagnosis=_diagnosis([
            _entry(), _entry(status='RUN_FAILED')]))

        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_TASK_INVALID'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)

    def test_high_debug_entry_must_derive_from_repair_executor(self):
        store = self._at_repair_two(diagnosis=_diagnosis([_entry(executor_task='other-d6')]))

        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_TASK_INVALID'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)

    def test_confirmed_high_debug_allows_repair_two_and_records_nothing(self):
        store = self._at_repair_two(diagnosis=_diagnosis([_entry()]))

        self.assertIsNone(task_submission_cli._check_delegate_repair_cap(_args(), store))

        self.assertEqual(store.writes, [])
        self.assertNotIn('repair_cap_override', store.records[_HIGH_DEBUG_TASK])

    def test_over_the_limit_refuses_even_with_valid_high_debug(self):
        for count in (_REVIEW_LIMIT, _REVIEW_LIMIT + 1):
            store = _Store(count=count, diagnosis=_diagnosis([_entry()]))
            with self.subTest(count=count), self.assertRaisesRegex(
                    LLMToolError, '^DELEGATE_REPAIR_LIMIT_REACHED$'):
                task_submission_cli._check_delegate_repair_cap(_args(), store)
            self.assertEqual(store.writes, [])

    def test_existing_override_field_is_ignored(self):
        # An old record's repair_cap_override no longer raises the limit.
        store = _Store(count=_REVIEW_LIMIT, diagnosis=_diagnosis([_entry()]))
        store.records[_CURRENT_TASK] = {
            'repair_cap_override': {'ref': _HIGH_DEBUG_REF, 'rounds': 9,
                                    'recorded_at': '2026-11-04T00:00:00Z'}}
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_REPAIR_LIMIT_REACHED$'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.writes, [])

    def test_refusal_order_checks_reference_before_task(self):
        store = self._at_repair_two(diagnosis=_diagnosis([_entry()]))
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_HIGH_DEBUG_TASK_REQUIRED$'):
            task_submission_cli._check_delegate_repair_cap(
                _args(high_debug_ref=None, high_debug_task=None), store)
        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_REF_INVALID'):
            task_submission_cli._check_delegate_repair_cap(
                _args(high_debug_ref='bad', high_debug_task=None), store)
        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_REF_INVALID'):
            task_submission_cli._check_delegate_repair_cap(
                _args(high_debug_ref=None), store)
        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_TASK_REQUIRED'):
            task_submission_cli._check_delegate_repair_cap(
                _args(high_debug_task=None), store)
        self.assertEqual(store.writes, [])

    def test_head_family_timestamp_and_reuse_are_rejected(self):
        cases = (
            (_entry(examined_sha='b' * 40), 'DELEGATE_HIGH_DEBUG_HEAD_MOVED', 'pr-938'),
            (_entry(family='other'), 'DELEGATE_HIGH_DEBUG_TASK_INVALID', 'pr-938'),
            (_entry(recorded_at='2026-11-04T00:00:00Z'), 'DELEGATE_HIGH_DEBUG_TASK_INVALID', 'pr-938'),
            (_entry(recorded_at='bad'), 'DELEGATE_HIGH_DEBUG_TASK_INVALID', 'pr-938'),
        )
        for entry, code, family in cases:
            store = self._at_repair_two(diagnosis=_diagnosis([entry]))
            with self.subTest(entry=entry), self.assertRaisesRegex(LLMToolError, code):
                task_submission_cli._check_delegate_repair_cap(
                    _args(high_debug_family=family), store)
            self.assertEqual(store.writes, [])

    def test_family_syntax_and_reuse_after_new_assignment_are_rejected(self):
        invalid_family = _entry(family='Bad Family')
        store = self._at_repair_two(diagnosis=_diagnosis([invalid_family]))
        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_TASK_INVALID'):
            task_submission_cli._check_delegate_repair_cap(
                _args(high_debug_family='Bad Family'), store)
        self.assertEqual(store.writes, [])

        store = self._at_repair_two(diagnosis=_diagnosis([_entry()]))
        store.records['00000000000000000000000000000000']['developer']['created_at'] = (
            '2026-11-04T00:00:02Z')
        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_TASK_INVALID'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.writes, [])

    def test_malformed_assignment_time_is_count_unavailable(self):
        store = self._at_repair_two(diagnosis=_diagnosis([_entry()]))
        store.records['00000000000000000000000000000000']['developer']['created_at'] = 'bad'
        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_REPAIR_COUNT_UNAVAILABLE'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.writes, [])

    def test_d6_binding_failure_has_distinct_error(self):
        store = self._at_repair_two(diagnosis=_diagnosis([_entry()]))
        with patch.object(task_submission_cli, '_delegate_d6_binding',
                          side_effect=OSError('unavailable')):
            with self.assertRaisesRegex(
                    LLMToolError, 'D6_SOURCE_BINDING_MISMATCH'):
                task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.writes, [])

    def test_head_moved_refuses_with_prefix_and_records_nothing(self):
        examined = 'b' * 40
        entry = _entry(examined_sha=examined)
        store = self._at_repair_two(diagnosis=_diagnosis([entry]))
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli._check_delegate_repair_cap(_args(), store)
        message = str(caught.exception)
        self.assertTrue(message.startswith('DELEGATE_HIGH_DEBUG_HEAD_MOVED'))
        self.assertIn('a new High Debug must be taken on the current D6 head', message)
        self.assertIn(examined[:12], message)
        self.assertIn(_HEAD[:12], message)
        self.assertNotIn(examined, message)
        self.assertNotIn(_HEAD, message)
        self.assertEqual(store.writes, [])

    def test_invalid_ref_names_accepted_forms_without_echoing_link(self):
        store = self._at_repair_two(diagnosis=_diagnosis([_entry()]))
        bad_ref = 'https://example.com/not-a-valid-link'
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli._check_delegate_repair_cap(
                _args(high_debug_ref=bad_ref), store)
        message = str(caught.exception)
        self.assertTrue(message.startswith('DELEGATE_HIGH_DEBUG_REF_INVALID'))
        self.assertIn('/issues/N#issuecomment-M', message)
        self.assertIn('/issues/N/comments/M', message)
        self.assertNotIn(bad_ref, message)
        self.assertNotIn('github.com', message)
        self.assertEqual(store.writes, [])

    def test_pull_request_comment_link_is_refused(self):
        store = self._at_repair_two(diagnosis=_diagnosis([_entry()]))
        pr_ref = 'https://github.com/example/project/pull/12#issuecomment-34'
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli._check_delegate_repair_cap(
                _args(high_debug_ref=pr_ref), store)
        message = str(caught.exception)
        self.assertTrue(message.startswith('DELEGATE_HIGH_DEBUG_REF_INVALID'))
        self.assertNotIn(pr_ref, message)
        self.assertEqual(store.writes, [])


_ROOT_TASK = '0' * 32


def _packet(**overrides):
    """A review repair #2 packet as review_repair builds it after piece D6a."""
    packet = {
        'task': _ROOT_TASK, 'pr': 938, 'executor_task': _EXECUTOR_TASK,
        'failed_head': _HEAD, 'version': 2, 'verdict_comment_id': 5,
        'findings_count': 1,
        'reviewer_text_excerpt_untrusted_data': 'Synthetic reviewer finding.',
        'high_debug_report_untrusted_data': _REPORT,
        'high_debug_report_sha256': _REPORT_SHA256,
    }
    packet.update(overrides)
    return packet


class RepairPacketHighDebugTests(unittest.TestCase):
    """Repair #2 accepts the root cause the review repair packet binds."""

    def setUp(self):
        _patch_trust_and_binding(self)

    def _store(self, entries, *, count=_REPAIR_TWO):
        # The packet's Task is the original Task on the D6, the first counted run.
        store = _Store(count=count)
        store.records[_ROOT_TASK].update(task_id=_ROOT_TASK, high_debug=entries)
        return store

    def _check(self, store, packet):
        return task_submission_cli._check_delegate_repair_cap(
            _no_high_debug(repair_packet=packet), store)

    def _refuses(self, store, packet, code='^DELEGATE_HIGH_DEBUG_TASK_INVALID$'):
        with self.assertRaisesRegex(LLMToolError, code):
            self._check(store, packet)
        self.assertEqual(store.writes, [])

    def test_matching_rca_entry_passes_and_records_nothing(self):
        store = self._store([_entry(status='RUN_FAILED', report_sha256='0' * 64),
                             _entry()])
        self.assertIsNone(self._check(store, _packet()))
        self.assertEqual(store.writes, [])

    def test_wrong_report_sha_refuses(self):
        for sha in ('0' * 64, 'A' * 64, 'bad', None):
            with self.subTest(sha=sha):
                self._refuses(self._store([_entry()]),
                              _packet(high_debug_report_sha256=sha))

    def test_wrong_head_refuses(self):
        self._refuses(self._store([_entry(examined_sha='b' * 40)]), _packet())
        self._refuses(self._store([_entry()]), _packet(failed_head='b' * 40))

    def test_entry_must_be_confirmed(self):
        for status in ('RCA_UNCONFIRMED', 'BLOCKED', 'REPORT_INVALID', 'RUN_FAILED'):
            with self.subTest(status=status):
                self._refuses(self._store([_entry(status=status)]), _packet())

    def test_packet_bound_to_another_task_or_d6_refuses(self):
        self._refuses(self._store([_entry()]), _packet(executor_task='other-d6'))
        self._refuses(self._store([_entry()]), _packet(task='1' * 32))
        store = self._store([_entry()])
        store.records[_ROOT_TASK]['completion'] = {'executor_task_id': 'other-d6'}
        store.records[_ROOT_TASK].pop('developer')
        store.records['1' * 32] = {
            'completion': {'executor_task_id': _EXECUTOR_TASK},
            'developer': {'state': 'DEVELOPER_RESPONDED',
                          'created_at': '2026-11-04T00:00:00Z'}}
        self._refuses(store, _packet())

    def test_record_without_entries_refuses(self):
        store = self._store([])
        self._refuses(store, _packet())
        store.records[_ROOT_TASK].pop('high_debug')
        self._refuses(store, _packet())

    def test_packet_without_report_sha_needs_high_debug_arguments(self):
        packet = _packet()
        del packet['high_debug_report_sha256']
        del packet['high_debug_report_untrusted_data']
        self._refuses(self._store([_entry()]), packet,
                      '^DELEGATE_HIGH_DEBUG_TASK_REQUIRED$')

    def test_packet_over_the_limit_refuses(self):
        self._refuses(self._store([_entry()], count=_REVIEW_LIMIT), _packet(),
                      '^DELEGATE_REPAIR_LIMIT_REACHED$')

    def test_repair_one_packet_passes_without_root_cause(self):
        packet = _packet()
        del packet['high_debug_report_sha256']
        del packet['high_debug_report_untrusted_data']
        store = self._store([], count=1)
        self.assertIsNone(self._check(store, packet))
        self.assertEqual(store.writes, [])


def _record_id(number):
    return f'{number:032x}'


def _ec2_claim(repair_task_id, *, family='pr-938-ec2', comment_id=None):
    claim = {'repair_task_id': repair_task_id, 'comment_id': comment_id}
    if family is not None:
        claim['family'] = family
    return claim


def _split_store(*, review_runs=0, ec2_runs=0, legacy_runs=0, current=None,
                 **kwargs):
    """A parent Task plus review, new EC2 and legacy EC2 repair runs on one D6.

    The parent Task stores the EC2 claims naming those runs; ``current`` is an
    extra claim naming the Task being delegated. The parent's own run is the
    first review run, so the review count is 1 + review_runs + legacy_runs.
    """
    store = _Store(count=1 + review_runs + ec2_runs + legacy_runs, **kwargs)
    first_ec2 = 1 + review_runs
    first_legacy = first_ec2 + ec2_runs
    claims = [_ec2_claim(_record_id(n)) for n in range(first_ec2, first_legacy)]
    claims += [_ec2_claim(_record_id(n), family='pr-938')
               for n in range(first_legacy, first_legacy + legacy_runs)]
    if current is not None:
        claims.append(current)
    store.records[_record_id(0)]['review_repair_claims'] = claims
    return store


class SplitRepairCountTests(unittest.TestCase):
    """Row 35: review rounds and EC2 repair rounds have separate D6 caps."""

    def setUp(self):
        _patch_trust_and_binding(self)

    def _passes(self, store, args=None):
        task_submission_cli._check_delegate_repair_cap(
            _no_high_debug() if args is None else args, store)
        self.assertEqual(store.writes, [])

    def _refuses(self, store, args=None, code='^DELEGATE_REPAIR_LIMIT_REACHED$'):
        with self.assertRaisesRegex(LLMToolError, code):
            task_submission_cli._check_delegate_repair_cap(
                _no_high_debug() if args is None else args, store)
        self.assertEqual(store.writes, [])

    def _needs_root_cause(self, store):
        self._refuses(store, code='^DELEGATE_HIGH_DEBUG_TASK_REQUIRED$')

    def test_ec2_runs_leave_every_review_round_available(self):
        self._passes(_split_store(ec2_runs=_TEST_FAIL_MAX))
        self._needs_root_cause(_split_store(review_runs=_REPAIR_TWO - 1,
                                            ec2_runs=_TEST_FAIL_MAX))
        self._refuses(_split_store(review_runs=_REVIEW_LIMIT - 1,
                                   ec2_runs=_TEST_FAIL_MAX))

    def test_review_runs_leave_every_ec2_round_available(self):
        current = _ec2_claim(_CURRENT_TASK)
        self._refuses(_split_store(review_runs=_REVIEW_LIMIT - 1))
        self._passes(_split_store(review_runs=_REVIEW_LIMIT - 1, current=current))
        self._passes(_split_store(review_runs=_REVIEW_LIMIT - 1,
                                  ec2_runs=_TEST_FAIL_MAX - 1, current=current))
        self._refuses(_split_store(review_runs=_REVIEW_LIMIT - 1,
                                   ec2_runs=_TEST_FAIL_MAX, current=current))

    def test_legacy_ec2_runs_count_on_both_counts(self):
        # Review: a legacy EC2 run still counts as a review round.
        self._refuses(_split_store(review_runs=_REVIEW_LIMIT - 2,
                                   legacy_runs=1))
        # EC2: a legacy run counts as a prior EC2 run, for a new or a legacy
        # current EC2 claim alike.
        for current in (_ec2_claim(_CURRENT_TASK),
                        _ec2_claim(_CURRENT_TASK, family='pr-938')):
            with self.subTest(current=current):
                self._passes(_split_store(ec2_runs=_TEST_FAIL_MAX - 1,
                                          current=current))
                self._refuses(_split_store(ec2_runs=_TEST_FAIL_MAX - 1,
                                           legacy_runs=1, current=current))

    def test_claims_with_comment_id_or_without_family_stay_review(self):
        for make in (lambda task_id: _ec2_claim(task_id, comment_id=9),
                     lambda task_id: _ec2_claim(task_id, family=None)):
            store = _split_store(review_runs=_REVIEW_LIMIT - 1,
                                 current=make(_CURRENT_TASK))
            store.records[_record_id(0)]['review_repair_claims'] += [
                make(_record_id(n)) for n in range(1, _REVIEW_LIMIT)]
            with self.subTest(claim=make('x')):
                self._refuses(store)

    def test_unreadable_claims_take_the_review_limit(self):
        for claims in ('bad', [None, 'bad', {'comment_id': None, 'family': 'pr-938-ec2'},
                               {'comment_id': None, 'family': 'pr-938-ec2',
                                'repair_task_id': ['x']},
                               {'family': 'pr-938-ec2', 'repair_task_id': _CURRENT_TASK},
                               {'comment_id': None, 'family': ['pr-938-ec2'],
                                'repair_task_id': _CURRENT_TASK}]):
            store = _split_store(review_runs=_REVIEW_LIMIT - 1)
            store.records[_record_id(0)]['review_repair_claims'] = claims
            with self.subTest(claims=claims):
                self._refuses(store)

    def test_ec2_at_its_limit_refuses_even_with_valid_high_debug(self):
        diagnosis = _diagnosis([_entry()])
        current = _ec2_claim(_CURRENT_TASK)
        self._refuses(_split_store(ec2_runs=_TEST_FAIL_MAX, current=current,
                                   diagnosis=diagnosis), args=_args())
        packet = _packet(task=_record_id(0))
        store = _split_store(ec2_runs=_TEST_FAIL_MAX, current=current)
        store.records[_record_id(0)].update(task_id=_record_id(0), high_debug=[_entry()])
        self._refuses(store, args=_no_high_debug(repair_packet=packet))

    def test_ec2_repair_never_needs_a_root_cause(self):
        # The review count is at repair #2, but an EC2 repair is not a review repair.
        self._passes(_split_store(review_runs=_REPAIR_TWO - 1,
                                  current=_ec2_claim(_CURRENT_TASK)))

    def test_review_repair_two_beside_ec2_runs_takes_high_debug(self):
        store = _split_store(review_runs=_REPAIR_TWO - 1, ec2_runs=_TEST_FAIL_MAX,
                             diagnosis=_diagnosis([_entry()]))
        self._passes(store, args=_args())


def _legacy_overrides():
    """Old per-Task cap and override keys; the delegate cap reads none of them."""
    return {
        'repair_cap_override': {'ref': _HIGH_DEBUG_REF, 'rounds': 9,
                                'recorded_at': '2026-11-04T00:00:00Z'},
        'repair_max': 9, 'max_repair_attempts': 9, 'test_fail_max': 9,
        'review_fail_max': 9,
    }


def _add_legacy_overrides(store, *task_ids):
    for task_id in task_ids:
        store.records.setdefault(task_id, {}).update(_legacy_overrides())


class LegacyDataTests(unittest.TestCase):
    """#1502 WP-D D14 (spec section 7): legacy Task data under the new limits."""

    def setUp(self):
        _patch_trust_and_binding(self)

    def _refuses(self, store, args, code):
        with self.assertRaisesRegex(LLMToolError, code):
            task_submission_cli._check_delegate_repair_cap(args, store)
        self.assertEqual(store.writes, [])

    def test_unreadable_claims_leave_test_runs_on_the_review_count(self):
        # UNKNOWN: runs the parent's claims cannot name as EC2 runs count as
        # review runs, so one review run plus two such runs uses the review limit.
        policy = dict(_POLICY, repair_max=2, test_fail_max=2)
        with patch.object(task_submission_cli.repair_policy, 'load_policy',
                          return_value=policy):
            store = _split_store(ec2_runs=2)
            task_submission_cli._check_delegate_repair_cap(_no_high_debug(), store)
            self.assertEqual(store.writes, [])
            for claims in ('UNKNOWN', None, ['UNKNOWN']):
                store = _split_store(ec2_runs=2)
                store.records[_record_id(0)]['review_repair_claims'] = claims
                with self.subTest(claims=claims):
                    self._refuses(store, _no_high_debug(), '^DELEGATE_REPAIR_LIMIT_REACHED$')

    def test_legacy_override_fields_do_not_raise_the_review_limit(self):
        for count in (_REVIEW_LIMIT, _REVIEW_LIMIT + 2):
            store = _Store(count=count, diagnosis=_diagnosis([_entry()]))
            _add_legacy_overrides(store, _CURRENT_TASK, *map(_record_id, range(count)))
            with self.subTest(count=count, root_cause='arguments'):
                self._refuses(store, _args(), '^DELEGATE_REPAIR_LIMIT_REACHED$')
            store = _Store(count=count)
            store.records[_ROOT_TASK].update(task_id=_ROOT_TASK, high_debug=[_entry()])
            _add_legacy_overrides(store, *map(_record_id, range(count)))
            with self.subTest(count=count, root_cause='packet'):
                self._refuses(store, _no_high_debug(repair_packet=_packet()),
                              '^DELEGATE_REPAIR_LIMIT_REACHED$')

    def test_legacy_override_fields_do_not_skip_the_root_cause(self):
        store = _Store(count=_REPAIR_TWO, diagnosis=_diagnosis([_entry()]))
        _add_legacy_overrides(store, _CURRENT_TASK, *map(_record_id, range(_REPAIR_TWO)))
        self._refuses(store, _no_high_debug(), '^DELEGATE_HIGH_DEBUG_TASK_REQUIRED$')

    def test_legacy_override_fields_do_not_raise_the_test_fail_limit(self):
        current = _ec2_claim(_CURRENT_TASK)
        for ec2_runs in (_TEST_FAIL_MAX, _TEST_FAIL_MAX + 1):
            store = _split_store(ec2_runs=ec2_runs, current=current)
            _add_legacy_overrides(store, *list(store.records))
            with self.subTest(ec2_runs=ec2_runs):
                self._refuses(store, _no_high_debug(), '^DELEGATE_REPAIR_LIMIT_REACHED$')


class MixedFamilyRepairChainTests(unittest.TestCase):
    """Completion walks review and EC2 repairs in stored claim order."""

    SOURCE, HEAD1, HEAD2, FINAL = '1' * 40, '2' * 40, '3' * 40, '4' * 40
    ARTIFACT = 'diff --git a/allowed.py b/allowed.py\n'

    def setUp(self):
        digest = hashlib.sha256(self.ARTIFACT.encode()).hexdigest()
        self.original = {'source_sha': self.SOURCE, 'artifact': self.ARTIFACT,
                         'artifact_sha256': digest, 'write_paths': ['allowed.py']}
        self.repairs = {
            'a' * 32: {'developer': {'state': 'DEVELOPER_RESPONDED',
                                     'source_sha': self.HEAD1,
                                     'artifact': self.ARTIFACT,
                                     'artifact_sha256': digest}},
            'b' * 32: {'developer': {'state': 'DEVELOPER_RESPONDED',
                                     'source_sha': self.HEAD2,
                                     'artifact': self.ARTIFACT,
                                     'artifact_sha256': digest}},
        }
        reader = patch.object(
            task_submission_cli.task_completion, 'read_d6_registration',
            return_value=SimpleNamespace(root='/repo', branch='task',
                                         source_sha=self.SOURCE,
                                         result_sha=self.FINAL))
        reader.start()
        self.addCleanup(reader.stop)

    def _segments(self, claims):
        return task_submission_cli._completion_repair_artifact_segments(
            {'developer': self.original, 'review_repair_claims': claims},
            SimpleNamespace(get=lambda task_id: self.repairs[task_id]),
            executor_task='executor', common_dir='/repo/.git', root='/repo',
            branch='task', source_sha=self.SOURCE)

    @staticmethod
    def _claim(family, attempt, head, repair_id, state='DISPATCHED'):
        claim = {'pr': 7, 'attempt': attempt, 'state': state, 'head_sha': head,
                 'repair_task_id': repair_id}
        if family is not None:
            claim['family'] = family
        return claim

    def _assert_invalid(self, claims):
        with self.assertRaises(LLMToolError) as caught:
            self._segments(claims)
        self.assertEqual(caught.exception.completion_code,
                         'DEVELOPER_ARTIFACT_CHAIN_INVALID')

    def test_ec2_repair_then_review_repair_passes(self):
        result = self._segments([
            self._claim('pr-7-ec2', 1, self.HEAD1, 'a' * 32),
            self._claim('pr-7', 1, self.HEAD2, 'b' * 32)])
        self.assertEqual([segment[2:] for segment in result], [
            (self.SOURCE, self.HEAD1), (self.HEAD1, self.HEAD2),
            (self.HEAD2, self.FINAL)])

    def test_legacy_family_claims_share_the_default_family(self):
        result = self._segments([
            self._claim(None, 1, self.HEAD1, 'a' * 32, state='FAILED'),
            self._claim('pr-7', 2, self.HEAD1, 'a' * 32),
            self._claim('pr-7-ec2', 1, self.HEAD2, 'b' * 32)])
        self.assertEqual(len(result), 3)

    def test_gap_in_one_family_fails(self):
        for claims in (
                [self._claim('pr-7-ec2', 1, self.HEAD1, 'a' * 32),
                 self._claim('pr-7', 2, self.HEAD2, 'b' * 32)],
                [self._claim('pr-7', 1, self.HEAD1, 'a' * 32),
                 self._claim('pr-7-ec2', 2, self.HEAD2, 'b' * 32)],
                [self._claim('pr-7', 2, self.HEAD2, 'b' * 32),
                 self._claim('pr-7', 1, self.HEAD1, 'a' * 32)],
                [self._claim(None, 1, self.HEAD1, 'a' * 32),
                 self._claim('pr-7', 1, self.HEAD2, 'b' * 32)]):
            with self.subTest(claims=claims):
                self._assert_invalid(claims)


def _no_work(**overrides):
    # The real shape persisted by the Codex route: no changed_files field.
    developer = {
        'state': 'DEVELOPER_OUTCOME_UNKNOWN',
        'provider_profile': 'codex',
        'error_code': 'CODEX_BLOCKED',
        'created_at': '2026-11-04T00:00:02Z',
        'source_sha': _HEAD,
        'artifact': None,
        'artifact_sha256': None,
        'execution': {'status': 'BLOCKED', 'cleanup_status': 'CONFIRMED'},
        'usage': None,
    }
    developer.update(overrides)
    return developer


class LaunchedNoWorkRepairCountTests(unittest.TestCase):
    _NEWEST = '00000000000000000000000000000000'

    def setUp(self):
        self.diff = ([], b'')
        _patch_trust_and_binding(
            self, binding=(SimpleNamespace(root='/d6'), SimpleNamespace(head=_HEAD)))
        started = patch.object(task_submission_cli.task_completion, '_worktree_diff',
                               side_effect=lambda root, head: self.diff)
        started.start()
        self.addCleanup(started.stop)

    def _store(self, developer, *, count=_REVIEW_LIMIT):
        # One record more than repair #2: skipping it leaves repair #2, which a
        # valid High Debug passes; counting it reaches the limit.
        store = _Store(count=count, diagnosis=_diagnosis([_entry()]))
        store.records[self._NEWEST]['developer'] = developer
        return store

    def _assert_skipped(self, developer):
        store = self._store(developer)
        task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.writes, [])

    def _assert_counted(self, developer):
        store = self._store(developer)
        with self.assertRaisesRegex(
                LLMToolError, '^DELEGATE_REPAIR_LIMIT_REACHED$'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.writes, [])

    def test_blocked_run_with_no_work_is_skipped(self):
        self._assert_skipped(_no_work())

    def test_blocked_state_with_no_work_is_skipped(self):
        self._assert_skipped(_no_work(
            state='BLOCKED', error_code='AGY_BLOCKED'))

    def test_interrupted_run_with_no_work_is_skipped(self):
        self._assert_skipped(_no_work(
            error_code='CODEX_INTERRUPTED',
            execution={'status': 'UNKNOWN', 'cleanup_status': 'CONFIRMED'}))

    def test_skipped_record_is_not_the_newest_assignment(self):
        # Were it the newest assignment, the earlier High Debug would be stale.
        self._assert_skipped(_no_work(created_at='2026-11-05T00:00:00Z'))

    def test_skipped_record_needs_no_valid_timestamp(self):
        self._assert_skipped(_no_work(created_at='bad'))

    def test_artifact_counts(self):
        self._assert_counted(_no_work(artifact='diff', artifact_sha256='0' * 64))

    def test_artifact_sha_alone_counts(self):
        self._assert_counted(_no_work(artifact_sha256='0' * 64))

    def test_run_that_left_uncommitted_files_counts(self):
        # Real record: empty artifact, but the D6 holds uncommitted edits.
        self.diff = (['a.py'], b'diff --git a/a.py b/a.py\n')
        self._assert_counted(_no_work())

    def test_untracked_or_raw_diff_counts(self):
        for diff in (([], b'diff'), (['a.py'], b'')):
            with self.subTest(diff=diff):
                self.diff = diff
                self._assert_counted(_no_work())

    def test_moved_d6_head_counts(self):
        self._assert_counted(_no_work(source_sha='b' * 40))

    def test_missing_or_malformed_source_sha_counts(self):
        for value in (None, 'abc', 5, 'A' * 40):
            with self.subTest(value=value):
                self._assert_counted(_no_work(source_sha=value))
        developer = _no_work()
        del developer['source_sha']
        self._assert_counted(developer)

    def test_unverifiable_d6_state_counts(self):
        with patch.object(task_submission_cli, '_delegate_d6_binding',
                          side_effect=OSError('dirty or unavailable')):
            self._assert_counted(_no_work())
        with patch.object(task_submission_cli.task_completion,
                          '_worktree_diff', side_effect=OSError('git')):
            self._assert_counted(_no_work())

    def test_present_changed_files_must_be_empty_list(self):
        for value in (['a.py'], None, 'a.py', {}):
            with self.subTest(value=value):
                self._assert_counted(_no_work(changed_files=value))

    def test_unknown_or_missing_cleanup_counts(self):
        for execution in (
                {'status': 'BLOCKED', 'cleanup_status': 'UNKNOWN'},
                {'status': 'BLOCKED'},
                {'status': 'BLOCKED', 'cleanup_status': None},
                None, 'CONFIRMED', []):
            with self.subTest(execution=execution):
                self._assert_counted(_no_work(execution=execution))
        developer = _no_work()
        del developer['execution']
        self._assert_counted(developer)

    def test_missing_artifact_fields_count(self):
        for key in ('artifact', 'artifact_sha256'):
            developer = _no_work()
            del developer[key]
            with self.subTest(key=key):
                self._assert_counted(developer)

    def test_other_outcomes_count(self):
        for overrides in (
                {'state': 'DEVELOPER_RESPONDED', 'error_code': None},
                {'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                 'error_code': 'CODEX_TIMEOUT'},
                {'state': 'DEVELOPER_OUTCOME_UNKNOWN', 'error_code': None},
                {'state': 'DEVELOPER_OUTPUT_REJECTED',
                 'error_code': 'CODEX_BLOCKED'},
                {'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                 'error_code': 'CODEX_NOT_BLOCKED_X'}):
            with self.subTest(overrides=overrides):
                self._assert_counted(_no_work(**overrides))

    def test_unreadable_record_still_fails_closed(self):
        store = self._store(_no_work())
        store.records[self._NEWEST]['developer'] = 'unreadable'
        with self.assertRaisesRegex(
                LLMToolError, 'DELEGATE_REPAIR_COUNT_UNAVAILABLE'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)

    def test_counted_record_without_valid_timestamp_is_unavailable(self):
        store = self._store(_no_work(changed_files=['a.py'], created_at='bad'))
        with self.assertRaisesRegex(
                LLMToolError, 'DELEGATE_REPAIR_COUNT_UNAVAILABLE'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)

    def test_skip_does_not_hide_a_reached_limit(self):
        store = self._store(_no_work(), count=_REVIEW_LIMIT + 1)
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_REPAIR_LIMIT_REACHED$'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.writes, [])

    def test_skip_can_bring_count_below_repair_two(self):
        store = _Store(count=_REPAIR_TWO)
        store.records[self._NEWEST]['developer'] = _no_work()
        task_submission_cli._check_delegate_repair_cap(_no_high_debug(), store)
        self.assertEqual(store.writes, [])


class HighDebugRunBindingTests(unittest.TestCase):
    """#914: at repair #2 the entry must be bound to its Task, run and report."""

    def setUp(self):
        _patch_trust_and_binding(self)

    def _refuses(self, diagnosis):
        store = _Store(count=_REPAIR_TWO, diagnosis=diagnosis)
        with self.assertRaisesRegex(
                LLMToolError, '^DELEGATE_HIGH_DEBUG_TASK_INVALID$'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.writes, [])

    def _passes(self, diagnosis):
        store = _Store(count=_REPAIR_TWO, diagnosis=diagnosis)
        self.assertIsNone(task_submission_cli._check_delegate_repair_cap(_args(), store))
        self.assertEqual(store.writes, [])

    def test_no_completion_refuses(self):
        self._refuses(_diagnosis([_entry()], executor_task=None))
        diagnosis = _diagnosis([_entry()])
        del diagnosis['completion']
        self._refuses(diagnosis)

    def test_completion_naming_another_d6_refuses(self):
        self._refuses(_diagnosis([_entry()], executor_task='other-d6'))

    def test_missing_report_section_refuses(self):
        from llmtool.high_debug import REPORT_SECTIONS

        for heading in REPORT_SECTIONS:
            report = _REPORT.replace('## ' + heading + '\n', '')
            self.assertNotEqual(report, _REPORT)
            with self.subTest(heading=heading):
                self._refuses(_diagnosis([_entry(report=report)]))

    def test_report_status_line_must_say_confirmed(self):
        # The stored status says RCA_CONFIRMED; the report itself does not.
        for status_lines in ('RCA_UNCONFIRMED', 'BLOCKED',
                             'RCA_CONFIRMED\nRCA_CONFIRMED',
                             'RCA_CONFIRMED - not RCA_UNCONFIRMED', 'Cost only'):
            report = _REPORT.replace('\nRCA_CONFIRMED\n', '\n' + status_lines + '\n')
            self.assertNotEqual(report, _REPORT)
            entry = _entry(report=report)
            self.assertEqual(entry['status'], 'RCA_CONFIRMED')
            with self.subTest(status_lines=status_lines):
                self._refuses(_diagnosis([entry]))

    def test_report_digest_mismatch_refuses(self):
        self._refuses(_diagnosis([_entry(report_sha256='0' * 64)]))
        entry = _entry()
        entry['report'] = _REPORT + 'Edited after the run.\n'
        self._refuses(_diagnosis([entry]))
        for report_sha256 in (None, 5, 'A' * 64):
            entry = _entry()
            entry['report_sha256'] = report_sha256
            with self.subTest(report_sha256=report_sha256):
                self._refuses(_diagnosis([entry], events=[]))

    def test_model_unknown_refuses(self):
        self._refuses(_diagnosis([_entry(model_observed='UNKNOWN')]))
        entry = _entry()
        del entry['model_observed']
        self._refuses(_diagnosis([entry]))
        self._refuses(_diagnosis([_entry(model_observed=None)]))

    def test_other_model_refuses(self):
        for model in ('gpt-6-luna', 'gpt-6.1-sol-mini', 'GPT-6.1-SOL', ' gpt-6.1-sol'):
            with self.subTest(model=model):
                self._refuses(_diagnosis([_entry(model_observed=model)]))

    def test_claude_model_with_matching_request_passes(self):
        self._passes(_diagnosis([_entry(model_requested='claude-opus-5-5',
                                        model_observed='claude-opus-5-5')]))

    def test_mixed_or_unknown_models_refuse(self):
        for requested, observed in (('gpt-6.1-sol', 'claude-opus-5-5'),
                                    ('claude-opus-5-5', 'gpt-6.1-sol')):
            with self.subTest(requested=requested, observed=observed):
                self._refuses(_diagnosis([_entry(
                    model_requested=requested, model_observed=observed)]))

    def test_no_event_refuses(self):
        self._refuses(_diagnosis([_entry()], events=[]))
        diagnosis = _diagnosis([_entry()])
        del diagnosis['completion_events']
        self._refuses(diagnosis)
        # An event of another entry does not bind this one.
        other = _entry(report=_REPORT + 'Another run.\n')
        self._refuses(_diagnosis([_entry()], events=[_event(other)]))

    def test_event_with_wrong_binding_refuses(self):
        entry = _entry()
        for overrides in ({'session_id': 'high-debug-other-d6'},
                          {'source': 'GITHUB'},
                          {'source_sha': 'b' * 40},
                          {'payload_sha256': '0' * 64},
                          {'payload_sha256': hashlib.sha256(_REPORT.encode()).hexdigest()}):
            with self.subTest(overrides=overrides):
                self._refuses(_diagnosis(
                    [entry], events=[_event(entry, **overrides)]))

    def test_tombstone_with_wrong_binding_refuses(self):
        entry = _entry()
        for overrides in ({'session_id': 'high-debug-other-d6'},
                          {'source': 'GITHUB'}):
            with self.subTest(overrides=overrides):
                self._refuses(_diagnosis(
                    [entry], events=[], tombstones=[_tombstone(entry, **overrides)]))

    def test_live_event_passes(self):
        self._passes(_diagnosis([_entry()]))

    def test_tombstone_passes(self):
        entry = _entry()
        self._passes(_diagnosis([entry], events=[], tombstones=[_tombstone(entry)]))

    def test_entry_and_event_from_run_high_debug_pass(self):
        import io
        import json
        import subprocess
        import tempfile
        from contextlib import redirect_stdout
        from pathlib import Path

        from llmtool import high_debug
        from llmtool.task_store import TaskStore, new_id

        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            store_root = base / 'tasks'
            diagnosis_root = base / 'diagnosis-d6'
            diagnosis_root.mkdir()
            sessions = base / 'sessions'
            sessions.mkdir()
            thread_id = '0199a000-0000-7000-8000-000000000001'
            (sessions / f'rollout-2026-11-07T00-00-00-{thread_id}.jsonl').write_text(
                json.dumps({'type': 'turn_context',
                            'payload': {'model': 'gpt-6.1-sol', 'effort': 'high'}}) + '\n',
                encoding='utf-8')

            store = TaskStore(store_root)
            request = 'Bounded High Debug binding fixture.'
            record = task_submission_cli._record(
                _HIGH_DEBUG_TASK, new_id(), 'pm-' + new_id(), 'pm-profile', request,
                state='PM_RESPONDED', provider_type='openai-compatible',
                model='pm-model', backend='NO_NATIVE_SESSION_ID_EXPOSED',
                request_sha=hashlib.sha256(request.encode()).hexdigest())
            assignment = {'schema_version': 1, 'route': 'codex', 'risk': 'LOW',
                          'goal': 'Test the High Debug binding.',
                          'acceptance': ['dispatch is bounded']}
            record.update(
                pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                                  'base_url': None},
                response=json.dumps({'schema_version': 1, 'summary': 'Fixture response.',
                                     'developer_assignment': assignment}),
                pm_assignment=assignment,
                completion={
                    'state': 'WAITING_EXECUTOR', 'executor_task_id': _EXECUTOR_TASK,
                    'reviewer_task_id': None,
                    'evidence': {'common_dir': str(base / 'repo' / '.git')},
                    'missing_gates': [], 'pm_dispatch': 'NOT_STARTED',
                    'pm_response': None, 'pm_recommendation': None,
                    'pm_usage': None, 'pm_error_code': None,
                })
            store.create(record)

            def read_registration(executor_task, common_dir, *, allowed_states):
                self.assertEqual(executor_task, _EXECUTOR_TASK)
                return SimpleNamespace(result_sha=_HEAD, root=str(base / 'executor-d6'),
                                       common_dir=common_dir)

            created = []

            def create_d6(*, task_id, base_sha, repo_root, executor_task):
                created.append((task_id, base_sha, executor_task))
                return str(diagnosis_root)

            # #1502 WP-F: this test does not read main; the rule step is
            # stubbed with a fixed prefix (test_high_debug_cards covers it).
            rules_prefix = 'RULES stub\nRULES_END\n\n'
            rules_sha256 = hashlib.sha256(rules_prefix.encode('utf-8')).hexdigest()
            prompts = []

            def runner(*, prompt, cwd, output_path):
                prompts.append(prompt)
                self.assertEqual(cwd, str(diagnosis_root))
                Path(output_path).write_text(_REPORT, encoding='utf-8')
                events = json.dumps({'type': 'thread.started', 'thread_id': thread_id})
                return SimpleNamespace(returncode=0, stdout=(events + '\n').encode(),
                                       stderr=b'')

            def git_status(argv, **_kwargs):
                self.assertEqual(argv, ['git', 'status', '--porcelain'])
                return SimpleNamespace(returncode=0, stdout=b'')

            fake_subprocess = SimpleNamespace(run=git_status, DEVNULL=subprocess.DEVNULL,
                                              PIPE=subprocess.PIPE)
            with patch.object(high_debug.task_store, 'default_root',
                              return_value=store_root), \
                    patch.object(high_debug.worker_dispatch, 'evaluate',
                                 return_value=SimpleNamespace(allowed=True)), \
                    patch.object(high_debug.task_completion, 'collect_git_snapshot',
                                 return_value=SimpleNamespace(head=_HEAD)), \
                    patch.object(high_debug, '_debug_rules',
                                 return_value=(rules_prefix, rules_sha256)), \
                    patch.object(high_debug, '_d6_taken', return_value=False), \
                    patch.object(high_debug, '_post_record', return_value=False), \
                    patch.object(high_debug, 'subprocess', fake_subprocess), \
                    redirect_stdout(io.StringIO()) as output:
                high_debug.run_high_debug(
                    store=store, task_id=_HIGH_DEBUG_TASK, executor_task=_EXECUTOR_TASK,
                    pr_number=938, family='pr-938',
                    early_reason='Synthetic early reason.', runner=runner,
                    create_d6=create_d6, repo_root=str(base / 'repo'),
                    read_registration=read_registration, sessions_dir=str(sessions))
            result = json.loads(output.getvalue())
            diagnosis = store.get(_HIGH_DEBUG_TASK)

        self.assertEqual(result['state'], 'RECORDED')
        self.assertEqual(result['status'], 'RCA_CONFIRMED')
        self.assertEqual(len(created), 1)
        entry = diagnosis['high_debug'][-1]
        self.assertEqual(entry['model_observed'], 'gpt-6.1-sol')
        self.assertEqual(entry['d6'], created[0][0])
        self.assertEqual(len(prompts), 1)
        self.assertTrue(prompts[0].startswith(rules_prefix))
        self.assertEqual(entry['rules_sha256'], rules_sha256)
        self.assertEqual([event['event_id'] for event in diagnosis['completion_events']],
                         [result['event_id']])
        self._passes(diagnosis)


class HighDebugRouteTests(unittest.TestCase):
    """The high-debug action routes to Codex by default and forwards the route."""

    def _parser(self):
        parser = argparse.ArgumentParser()
        actions = parser.add_subparsers(dest='action')
        task_submission_cli.add_task_submission_parsers(actions)
        return parser

    def test_route_defaults_to_codex_and_accepts_claude(self):
        parser = self._parser()
        base = ['high-debug', 'a' * 32, '--executor-task', 'executor',
                '--pr', '17', '--family', 'family', '--early', 'reason']

        self.assertEqual(parser.parse_args(base).route, 'codex')
        self.assertEqual(parser.parse_args(base + ['--route', 'claude']).route, 'claude')

    def test_unknown_route_is_refused(self):
        parser = self._parser()
        base = ['high-debug', 'a' * 32, '--executor-task', 'executor',
                '--pr', '17', '--family', 'family', '--early', 'reason']
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(base + ['--route', 'other'])

    def _run(self, route=None):
        values = {'task_id': 'a' * 32, 'executor_task': 'executor', 'pr': 17,
                  'family': 'family', 'early': 'reason'}
        if route is not None:
            values['route'] = route
        args = SimpleNamespace(**values)
        with patch('llmtool.high_debug.run_high_debug') as runner:
            task_submission_cli.run_task_high_debug(args, store=object())
        return runner.call_args.kwargs

    def test_route_is_forwarded(self):
        self.assertEqual(self._run(route='claude')['route'], 'claude')

    def test_missing_route_forwards_codex(self):
        self.assertEqual(self._run()['route'], 'codex')


# Invented comment URLs of valid shape for the trusted slug; neither names a
# real comment.
_COMMENT_REFS = (_HIGH_DEBUG_REF,
                 'https://github.com/example/project/issues/12#issuecomment-56')


class CommentCannotPassRepairCapTests(unittest.TestCase):
    """#914: at repair #2, a comment URL never stands in for a bound High Debug Task."""

    def setUp(self):
        _patch_trust_and_binding(self)

    def _refuses(self, diagnosis, **overrides):
        store = _Store(count=_REPAIR_TWO, diagnosis=diagnosis)
        with self.assertRaisesRegex(
                LLMToolError, '^DELEGATE_HIGH_DEBUG_TASK_INVALID$'):
            task_submission_cli._check_delegate_repair_cap(_args(**overrides), store)
        self.assertEqual(store.writes, [])

    def test_comment_url_as_high_debug_task_refuses(self):
        for ref in _COMMENT_REFS:
            with self.subTest(ref=ref):
                self._refuses(_diagnosis([_entry()]), high_debug_task=ref)

    def test_unknown_high_debug_task_refuses(self):
        self._refuses(_diagnosis([_entry()]), high_debug_task='1' * 32)

    def test_task_without_high_debug_key_refuses(self):
        diagnosis = _diagnosis([_entry()])
        del diagnosis['high_debug']
        self._refuses(diagnosis)

    def test_record_naming_another_task_refuses(self):
        diagnosis = _diagnosis([_entry()])
        diagnosis['task_id'] = 'c' * 32
        self._refuses(diagnosis)

    def test_last_entry_not_a_dict_refuses(self):
        confirmed = _entry()
        for last in ('RCA_CONFIRMED', None, ['RCA_CONFIRMED']):
            with self.subTest(last=last):
                self._refuses(_diagnosis([confirmed, last],
                                         events=[_event(confirmed)]))

    def test_missing_high_debug_family_refuses(self):
        self._refuses(_diagnosis([_entry()]), high_debug_family=None)

    def test_unconfirmed_sole_entry_refuses(self):
        for status in ('RCA_UNCONFIRMED', 'BLOCKED', 'REPORT_INVALID', 'RUN_FAILED'):
            with self.subTest(status=status):
                self._refuses(_diagnosis([_entry(status=status)]))

    def test_unconfirmed_entry_after_confirmed_one_refuses(self):
        for status in ('RCA_UNCONFIRMED', 'BLOCKED', 'REPORT_INVALID', 'RUN_FAILED'):
            with self.subTest(status=status):
                self._refuses(_diagnosis([
                    _entry(), _entry(status=status,
                                     recorded_at='2026-11-04T00:00:02Z')]))

    def test_d6_derived_from_another_head_refuses(self):
        from llmtool.high_debug import _d6_name

        entry = _entry()
        entry['d6'] = _d6_name(_EXECUTOR_TASK, 1, 'b' * 40)
        self.assertNotEqual(entry['d6'], _entry()['d6'])
        self._refuses(_diagnosis([entry]))

    def test_d6_without_attempt_suffix_refuses(self):
        for d6 in (_EXECUTOR_TASK, _EXECUTOR_TASK + '-' + _HEAD[:7],
                   _EXECUTOR_TASK + '-hd-' + _HEAD[:7],
                   _EXECUTOR_TASK + '-hd0-' + _HEAD[:7]):
            entry = _entry()
            entry['d6'] = d6
            with self.subTest(d6=d6):
                self._refuses(_diagnosis([entry]))


class DelegateRequiresHighDebugTaskTests(unittest.TestCase):
    """#914: every delegate route refuses a comment URL with no High Debug Task."""

    def setUp(self):
        _patch_trust_and_binding(self)

    def test_each_route_refuses_before_its_runner(self):
        owner_ref = 'https://github.com/example/project/issues/12#issuecomment-78'
        routes = (
            ('run_task_agy_delegate',
             {'provider': 'agy', 'mode': 'direct', 'owner_ref': owner_ref,
              'acceptance': ['Synthetic acceptance.']}),
            ('run_task_claude_delegate',
             {'provider': 'claude', 'mode': 'direct', 'owner_ref': owner_ref,
              'acceptance': ['Synthetic acceptance.']}),
            ('run_task_gw_agent_delegate',
             {'provider': 'gw-agent', 'mode': 'direct'}),
            ('run_task_codex_delegate',
             {'provider': 'codex', 'mode': 'direct',
              'acceptance': ['Synthetic acceptance.']}),
            ('run_task_opencode_delegate',
             {'provider': 'opencode', 'acceptance': ['Synthetic acceptance.']}),
        )
        for route, route_args in routes:
            for ref in _COMMENT_REFS:
                store = _Store(count=_REPAIR_TWO,
                               diagnosis=_diagnosis([_entry()]))
                args = _args(high_debug_ref=ref, high_debug_task=None, **route_args)
                with self.subTest(route=route, ref=ref), \
                        patch.object(task_submission_cli, route) as runner:
                    with self.assertRaisesRegex(
                            LLMToolError, '^DELEGATE_HIGH_DEBUG_TASK_REQUIRED$'):
                        task_submission_cli.run_task_delegate(args, store=store)
                    runner.assert_not_called()
                self.assertEqual(store.writes, [])


if __name__ == '__main__':
    unittest.main()
