"""Tests for the High Debug evidence required beyond the delegate repair cap."""

import argparse
import hashlib
import io
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import task_submission_cli
from llmtool.errors import LLMToolError
from llmtool.run_budget import MAX_REPAIR_ATTEMPTS


_CURRENT_TASK = 'f' * 32
_HIGH_DEBUG_TASK = 'e' * 32
_EXECUTOR_TASK = 'executor-d6'
_HIGH_DEBUG_REF = 'https://github.com/example/project/issues/12/comments/34'
_HEAD = 'a' * 40


class _Store:
    def __init__(self, *, count, diagnosis=None, unreadable=False):
        self.records = {
            f'{number:032x}': {
                'completion': {'executor_task_id': _EXECUTOR_TASK},
                'developer': {'state': 'DEVELOPER_RESPONDED',
                              'created_at': '2026-11-04T00:00:00Z'},
            }
            for number in range(count)
        }
        if diagnosis is not None:
            self.records[_HIGH_DEBUG_TASK] = diagnosis
        elif unreadable:
            # Keep the unreadable Task addressable without persisting a None
            # record; get() raises for it below.
            self.records[_HIGH_DEBUG_TASK] = {}
        self.unreadable = unreadable
        self.recorded = []

    def task_ids(self):
        return list(self.records)

    def get(self, task_id):
        if task_id == _HIGH_DEBUG_TASK and self.unreadable:
            raise OSError('unreadable')
        return self.records[task_id]

    def record_repair_cap_override(self, task_id, *, ref, rounds):
        self.recorded.append((task_id, ref, rounds))


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


class DelegateRepairCapTests(unittest.TestCase):
    def setUp(self):
        self.trust_patch = patch.object(
            task_submission_cli.trust, 'load_trust',
            return_value=SimpleNamespace(repository={'slug': 'example/project'}))
        self.trust_patch.start()
        self.addCleanup(self.trust_patch.stop)
        self.binding_patch = patch.object(
            task_submission_cli, '_delegate_d6_binding',
            return_value=(None, SimpleNamespace(head=_HEAD)))
        self.binding_patch.start()
        self.addCleanup(self.binding_patch.stop)

    def _at_cap(self, **kwargs):
        return _Store(count=1 + MAX_REPAIR_ATTEMPTS, **kwargs)

    def test_below_cap_ignores_high_debug_task_option(self):
        store = _Store(count=MAX_REPAIR_ATTEMPTS)
        args = _args(high_debug_task='invalid')

        task_submission_cli._check_delegate_repair_cap(args, store)

        self.assertEqual(store.recorded, [])

    def test_below_cap_rejects_unreadable_record(self):
        store = _Store(count=MAX_REPAIR_ATTEMPTS, unreadable=True)
        with self.assertRaisesRegex(
                LLMToolError, 'DELEGATE_REPAIR_COUNT_UNAVAILABLE'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.recorded, [])

    def test_unreadable_diagnosis_that_may_be_counted_fails_closed(self):
        store = _Store(count=MAX_REPAIR_ATTEMPTS, unreadable=True)
        store.records.pop(_HIGH_DEBUG_TASK, None)
        store.records[_HIGH_DEBUG_TASK] = {
            'completion': {'executor_task_id': _EXECUTOR_TASK},
            'developer': {'created_at': '2026-11-04T00:00:00Z'},
        }

        with self.assertRaisesRegex(
                LLMToolError, 'DELEGATE_REPAIR_COUNT_UNAVAILABLE'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.recorded, [])

    def test_missing_high_debug_task_is_refused(self):
        store = self._at_cap(diagnosis=_diagnosis([_entry()]))

        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_TASK_REQUIRED'):
            task_submission_cli._check_delegate_repair_cap(
                _args(high_debug_task=None), store)
        self.assertEqual(store.recorded, [])

    def test_unreadable_high_debug_task_is_refused(self):
        store = self._at_cap(diagnosis=None, unreadable=True)

        with self.assertRaisesRegex(
                LLMToolError, 'DELEGATE_REPAIR_COUNT_UNAVAILABLE'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.recorded, [])

    def test_task_without_high_debug_entry_is_refused(self):
        store = self._at_cap(diagnosis=_diagnosis([]))

        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_TASK_INVALID'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)

    def test_last_high_debug_entry_must_be_confirmed(self):
        store = self._at_cap(diagnosis=_diagnosis([
            _entry(), _entry(status='RUN_FAILED')]))

        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_TASK_INVALID'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)

    def test_high_debug_entry_must_derive_from_repair_executor(self):
        store = self._at_cap(diagnosis=_diagnosis([_entry(executor_task='other-d6')]))

        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_TASK_INVALID'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)

    def test_confirmed_high_debug_entry_allows_recording_existing_override_shape(self):
        store = self._at_cap(diagnosis=_diagnosis([_entry()]))

        task_submission_cli._check_delegate_repair_cap(_args(), store)

        self.assertEqual(store.recorded, [(
            _CURRENT_TASK, _HIGH_DEBUG_REF,
            max(3, (1 + MAX_REPAIR_ATTEMPTS) + 1))])

    def test_refusal_order_checks_reference_before_task(self):
        store = self._at_cap(diagnosis=_diagnosis([_entry()]))
        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_REPAIR_LIMIT_REACHED'):
            task_submission_cli._check_delegate_repair_cap(
                _args(high_debug_ref=None, high_debug_task=None), store)
        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_REF_INVALID'):
            task_submission_cli._check_delegate_repair_cap(
                _args(high_debug_ref='bad', high_debug_task=None), store)
        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_TASK_REQUIRED'):
            task_submission_cli._check_delegate_repair_cap(
                _args(high_debug_task=None), store)

    def test_head_family_timestamp_and_reuse_are_rejected_without_override(self):
        cases = (
            (_entry(examined_sha='b' * 40), 'DELEGATE_HIGH_DEBUG_TASK_INVALID', 'pr-938'),
            (_entry(family='other'), 'DELEGATE_HIGH_DEBUG_TASK_INVALID', 'pr-938'),
            (_entry(recorded_at='2026-11-04T00:00:00Z'), 'DELEGATE_HIGH_DEBUG_TASK_INVALID', 'pr-938'),
            (_entry(recorded_at='bad'), 'DELEGATE_HIGH_DEBUG_TASK_INVALID', 'pr-938'),
        )
        for entry, code, family in cases:
            store = self._at_cap(diagnosis=_diagnosis([entry]))
            with self.subTest(entry=entry), self.assertRaisesRegex(LLMToolError, code):
                task_submission_cli._check_delegate_repair_cap(
                    _args(high_debug_family=family), store)
            self.assertEqual(store.recorded, [])

    def test_family_syntax_and_reuse_after_new_assignment_are_rejected(self):
        invalid_family = _entry(family='Bad Family')
        store = self._at_cap(diagnosis=_diagnosis([invalid_family]))
        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_TASK_INVALID'):
            task_submission_cli._check_delegate_repair_cap(
                _args(high_debug_family='Bad Family'), store)
        self.assertEqual(store.recorded, [])

        store = self._at_cap(diagnosis=_diagnosis([_entry()]))
        store.records['00000000000000000000000000000000']['developer']['created_at'] = (
            '2026-11-04T00:00:02Z')
        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_HIGH_DEBUG_TASK_INVALID'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.recorded, [])

    def test_malformed_assignment_time_is_count_unavailable(self):
        store = self._at_cap(diagnosis=_diagnosis([_entry()]))
        store.records['00000000000000000000000000000000']['developer']['created_at'] = 'bad'
        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_REPAIR_COUNT_UNAVAILABLE'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.recorded, [])

    def test_d6_binding_failure_has_distinct_error(self):
        store = self._at_cap(diagnosis=_diagnosis([_entry()]))
        with patch.object(task_submission_cli, '_delegate_d6_binding',
                          side_effect=OSError('unavailable')):
            with self.assertRaisesRegex(
                    LLMToolError, 'D6_SOURCE_BINDING_MISMATCH'):
                task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.recorded, [])


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
    extra claim naming the Task being delegated.
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
        for target, kwargs in (
                ((task_submission_cli.trust, 'load_trust'),
                 {'return_value': SimpleNamespace(
                     repository={'slug': 'example/project'})}),
                ((task_submission_cli, '_delegate_d6_binding'),
                 {'return_value': (None, SimpleNamespace(head=_HEAD))})):
            started = patch.object(*target, **kwargs)
            started.start()
            self.addCleanup(started.stop)

    def _passes(self, store):
        task_submission_cli._check_delegate_repair_cap(
            _args(high_debug_ref=None, high_debug_task=None), store)
        self.assertEqual(store.recorded, [])

    def _refuses(self, store):
        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_REPAIR_LIMIT_REACHED'):
            task_submission_cli._check_delegate_repair_cap(
                _args(high_debug_ref=None, high_debug_task=None), store)
        self.assertEqual(store.recorded, [])

    def test_ec2_runs_leave_every_review_round_available(self):
        self._passes(_split_store(ec2_runs=MAX_REPAIR_ATTEMPTS))
        self._passes(_split_store(review_runs=MAX_REPAIR_ATTEMPTS - 1,
                                  ec2_runs=MAX_REPAIR_ATTEMPTS))
        self._refuses(_split_store(review_runs=MAX_REPAIR_ATTEMPTS,
                                   ec2_runs=MAX_REPAIR_ATTEMPTS))

    def test_review_runs_leave_every_ec2_round_available(self):
        current = _ec2_claim(_CURRENT_TASK)
        self._refuses(_split_store(review_runs=MAX_REPAIR_ATTEMPTS))
        self._passes(_split_store(review_runs=MAX_REPAIR_ATTEMPTS, current=current))
        self._passes(_split_store(review_runs=MAX_REPAIR_ATTEMPTS,
                                  ec2_runs=MAX_REPAIR_ATTEMPTS - 1, current=current))
        self._refuses(_split_store(review_runs=MAX_REPAIR_ATTEMPTS,
                                   ec2_runs=MAX_REPAIR_ATTEMPTS, current=current))

    def test_legacy_ec2_runs_count_on_both_counts(self):
        # Review: a legacy EC2 run still counts as a review round.
        self._refuses(_split_store(review_runs=MAX_REPAIR_ATTEMPTS - 1,
                                   legacy_runs=1))
        # EC2: a legacy run counts as a prior EC2 run, for a new or a legacy
        # current EC2 claim alike.
        for current in (_ec2_claim(_CURRENT_TASK),
                        _ec2_claim(_CURRENT_TASK, family='pr-938')):
            with self.subTest(current=current):
                self._passes(_split_store(ec2_runs=MAX_REPAIR_ATTEMPTS - 1,
                                          current=current))
                self._refuses(_split_store(ec2_runs=MAX_REPAIR_ATTEMPTS - 1,
                                           legacy_runs=1, current=current))

    def test_claims_with_comment_id_or_without_family_stay_review(self):
        for make in (lambda task_id: _ec2_claim(task_id, comment_id=9),
                     lambda task_id: _ec2_claim(task_id, family=None)):
            store = _split_store(review_runs=MAX_REPAIR_ATTEMPTS,
                                 current=make(_CURRENT_TASK))
            store.records[_record_id(0)]['review_repair_claims'] += [
                make(_record_id(n)) for n in range(1, 1 + MAX_REPAIR_ATTEMPTS)]
            with self.subTest(claim=make('x')):
                self._refuses(store)

    def test_unreadable_claims_take_the_review_limit(self):
        for claims in ('bad', [None, 'bad', {'comment_id': None, 'family': 'pr-938-ec2'},
                               {'comment_id': None, 'family': 'pr-938-ec2',
                                'repair_task_id': ['x']},
                               {'family': 'pr-938-ec2', 'repair_task_id': _CURRENT_TASK},
                               {'comment_id': None, 'family': ['pr-938-ec2'],
                                'repair_task_id': _CURRENT_TASK}]):
            store = _split_store(review_runs=MAX_REPAIR_ATTEMPTS)
            store.records[_record_id(0)]['review_repair_claims'] = claims
            with self.subTest(claims=claims):
                self._refuses(store)

    def test_high_debug_override_records_the_count_that_tripped_the_cap(self):
        diagnosis = _diagnosis([_entry()])
        store = _split_store(review_runs=MAX_REPAIR_ATTEMPTS,
                             ec2_runs=MAX_REPAIR_ATTEMPTS, diagnosis=diagnosis)
        task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.recorded, [(
            _CURRENT_TASK, _HIGH_DEBUG_REF, max(3, (1 + MAX_REPAIR_ATTEMPTS) + 1))])

        store = _split_store(review_runs=MAX_REPAIR_ATTEMPTS,
                             ec2_runs=MAX_REPAIR_ATTEMPTS,
                             current=_ec2_claim(_CURRENT_TASK), diagnosis=diagnosis)
        task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.recorded, [(
            _CURRENT_TASK, _HIGH_DEBUG_REF, max(3, MAX_REPAIR_ATTEMPTS + 1))])


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
        for name, target, kwargs in (
                ('trust_patch', (task_submission_cli.trust, 'load_trust'),
                 {'return_value': SimpleNamespace(
                     repository={'slug': 'example/project'})}),
                ('binding_patch', (task_submission_cli, '_delegate_d6_binding'),
                 {'return_value': (SimpleNamespace(root='/d6'),
                                   SimpleNamespace(head=_HEAD))}),
                ('diff_patch', (task_submission_cli.task_completion,
                                '_worktree_diff'),
                 {'side_effect': lambda root, head: self.diff})):
            started = patch.object(*target, **kwargs)
            started.start()
            self.addCleanup(started.stop)

    def _store(self, developer):
        # One extra record so the cap is still reached after one is skipped.
        store = _Store(count=2 + MAX_REPAIR_ATTEMPTS,
                       diagnosis=_diagnosis([_entry()]))
        store.records[self._NEWEST]['developer'] = developer
        return store

    def _assert_skipped(self, developer):
        store = self._store(developer)
        task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.recorded, [(
            _CURRENT_TASK, _HIGH_DEBUG_REF, 2 + MAX_REPAIR_ATTEMPTS)])

    def _assert_counted(self, developer):
        store = self._store(developer)
        # The newer counted record becomes the newest assignment, so the
        # earlier High Debug diagnosis is stale.
        with self.assertRaisesRegex(
                LLMToolError, 'DELEGATE_HIGH_DEBUG_TASK_INVALID'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.recorded, [])

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
        store = self._store(_no_work(created_at='2026-11-05T00:00:00Z'))
        task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(len(store.recorded), 1)

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

    def test_skip_does_not_hide_a_reached_cap(self):
        store = self._store(_no_work())
        with self.assertRaisesRegex(LLMToolError, 'DELEGATE_REPAIR_LIMIT_REACHED'):
            task_submission_cli._check_delegate_repair_cap(
                _args(high_debug_ref=None, high_debug_task=None), store)
        self.assertEqual(store.recorded, [])

    def test_skip_can_bring_count_below_cap(self):
        store = _Store(count=1 + MAX_REPAIR_ATTEMPTS)
        store.records[self._NEWEST]['developer'] = _no_work()
        task_submission_cli._check_delegate_repair_cap(
            _args(high_debug_ref=None, high_debug_task=None), store)
        self.assertEqual(store.recorded, [])


class HighDebugRunBindingTests(unittest.TestCase):
    """#914: past the cap the entry must be bound to its Task, run and report."""

    def setUp(self):
        for target, kwargs in (
                ((task_submission_cli.trust, 'load_trust'),
                 {'return_value': SimpleNamespace(
                     repository={'slug': 'example/project'})}),
                ((task_submission_cli, '_delegate_d6_binding'),
                 {'return_value': (None, SimpleNamespace(head=_HEAD))})):
            started = patch.object(*target, **kwargs)
            started.start()
            self.addCleanup(started.stop)

    def _refuses(self, diagnosis):
        store = _Store(count=1 + MAX_REPAIR_ATTEMPTS, diagnosis=diagnosis)
        with self.assertRaisesRegex(
                LLMToolError, '^DELEGATE_HIGH_DEBUG_TASK_INVALID$'):
            task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.recorded, [])

    def _passes(self, diagnosis):
        store = _Store(count=1 + MAX_REPAIR_ATTEMPTS, diagnosis=diagnosis)
        task_submission_cli._check_delegate_repair_cap(_args(), store)
        self.assertEqual(store.recorded, [(
            _CURRENT_TASK, _HIGH_DEBUG_REF, max(3, (1 + MAX_REPAIR_ATTEMPTS) + 1))])

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

            def runner(*, prompt, cwd, output_path):
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
    """#914: at the cap, a comment URL never stands in for a bound High Debug Task."""

    def setUp(self):
        for target, kwargs in (
                ((task_submission_cli.trust, 'load_trust'),
                 {'return_value': SimpleNamespace(
                     repository={'slug': 'example/project'})}),
                ((task_submission_cli, '_delegate_d6_binding'),
                 {'return_value': (None, SimpleNamespace(head=_HEAD))})):
            started = patch.object(*target, **kwargs)
            started.start()
            self.addCleanup(started.stop)

    def _refuses(self, diagnosis, **overrides):
        store = _Store(count=1 + MAX_REPAIR_ATTEMPTS, diagnosis=diagnosis)
        with self.assertRaisesRegex(
                LLMToolError, '^DELEGATE_HIGH_DEBUG_TASK_INVALID$'):
            task_submission_cli._check_delegate_repair_cap(_args(**overrides), store)
        self.assertEqual(store.recorded, [])

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
        for target, kwargs in (
                ((task_submission_cli.trust, 'load_trust'),
                 {'return_value': SimpleNamespace(
                     repository={'slug': 'example/project'})}),
                ((task_submission_cli, '_delegate_d6_binding'),
                 {'return_value': (None, SimpleNamespace(head=_HEAD))})):
            started = patch.object(*target, **kwargs)
            started.start()
            self.addCleanup(started.stop)

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
                store = _Store(count=1 + MAX_REPAIR_ATTEMPTS,
                               diagnosis=_diagnosis([_entry()]))
                args = _args(high_debug_ref=ref, high_debug_task=None, **route_args)
                with self.subTest(route=route, ref=ref), \
                        patch.object(task_submission_cli, route) as runner:
                    with self.assertRaisesRegex(
                            LLMToolError, '^DELEGATE_HIGH_DEBUG_TASK_REQUIRED$'):
                        task_submission_cli.run_task_delegate(args, store=store)
                    runner.assert_not_called()
                self.assertEqual(store.recorded, [])


if __name__ == '__main__':
    unittest.main()
