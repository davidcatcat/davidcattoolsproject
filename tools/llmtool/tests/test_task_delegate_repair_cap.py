"""Tests for the High Debug evidence required beyond the delegate repair cap."""

import hashlib
import unittest
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


def _entry(*, executor_task=_EXECUTOR_TASK, status='RCA_CONFIRMED',
           examined_sha=_HEAD, family='pr-938', recorded_at='2026-11-04T00:00:01Z'):
    from llmtool.high_debug import _d6_name

    return {
        'status': status,
        'family': family,
        'failed_corrections': 2,
        'early_reason': None,
        'd6': _d6_name(executor_task, 1, examined_sha),
        'examined_sha': examined_sha,
        'model_requested': 'gpt-6.1-sol',
        'model_observed': 'gpt-6.1-sol',
        'effort_observed': 'high',
        'report': 'Confirmed root cause.',
        'report_sha256': hashlib.sha256(
            b'Confirmed root cause.').hexdigest(),
        'usage': 'UNKNOWN',
        'recorded_at': recorded_at,
    }


def _diagnosis(entries):
    return {'task_id': _HIGH_DEBUG_TASK, 'high_debug': entries}


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


if __name__ == '__main__':
    unittest.main()
