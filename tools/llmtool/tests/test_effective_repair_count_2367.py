"""#2367 P0-B (Owner #2383 B): the delegate repair guard counts effective repairs only.

The PR #2383 chain had six rounds, each its own Task and D6 (newest last here):
an original run, a provider quota stop, a repair, a quota stop, a repair and a
Claude NO_CHANGES run. Only the original run and the two repairs did work, so
the chain has used 2 of its 5 effective repairs and the next round is not
refused DELEGATE_REPAIR_LIMIT_REACHED. Five real repairs still refuse; an
UNKNOWN round, a quota stop that changed files and a NO_CHANGES run whose
evidence cannot exclude a change still count. The count runs through the real
chain gate (_delegate_chain_gate) on an in-memory store and never rewrites a
record. The repair #2+ root-cause check is a separate gate covered by
test_task_delegate_repair_cap; here it is replaced by a recorder so the count
alone is observed. Standard library only; no network, Git or provider call.
"""

import copy
import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from llmtool import repair_policy, task_submission_cli
from llmtool.errors import LLMToolError


def _task_id(prefix):
    return prefix + '0' * (32 - len(prefix))


def _sha(prefix):
    return prefix + '0' * (40 - len(prefix))


CURRENT = _task_id('c0ffee01')
NEXT_EXECUTOR = 'executor-next'
NEXT_SOURCE = _sha('5eed')
ARTIFACT = ('diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n'
            '@@ -1 +1 @@\n-Synthetic source.\n+Synthetic result.\n')

# The six PR #2383 rounds, oldest first: (task prefix, kind, created_at, source).
# A quota round's source is its route-recorded progress head_sha.
PR_2383_ROUNDS = (
    ('983dcdb1', 'responded', '2026-11-19T02:58:00Z', _sha('983dcdb1')),
    ('30f92678', 'quota', '2026-11-19T03:19:00Z', _sha('dc8562f1')),
    ('99fa743f', 'responded', '2026-11-19T03:43:00Z', _sha('99fa743f')),
    ('ba2da1a0', 'quota', '2026-11-19T05:20:00Z', _sha('99f634c2')),
    ('8fed4077', 'responded', '2026-11-19T06:20:00Z', _sha('8fed4077')),
    ('198e4ead', 'no_changes', '2026-11-19T10:21:00Z', _sha('198e4ead')),
)
EFFECTIVE_RUNS = {_task_id('983dcdb1'), _task_id('99fa743f'), _task_id('8fed4077')}


def _developer(kind, created_at, source, profile='claude'):
    developer = {
        'provider_profile': profile, 'created_at': created_at, 'source_sha': source,
        'read_paths': ['README.md'], 'write_paths': ['README.md'],
        'executor_task_id': None,
        'direct_result_callback': {'evidence': {'source_sha': source, 'result_head': None}},
    }
    if kind == 'responded':
        developer.update(
            state='DEVELOPER_RESPONDED', error_code=None, artifact=ARTIFACT,
            artifact_sha256=hashlib.sha256(ARTIFACT.encode('utf-8')).hexdigest(),
            usage=None, execution={
                'status': 'DONE', 'native_session_id': 'synthetic-session',
                'process_exit_code': 0, 'stdout_sha256': None, 'elapsed_ms': 1,
                'cleanup_status': 'CONFIRMED', 'usage_source': 'CLI_REPORTED',
                'provider_evidence': {'accepted': True, 'basis': 'CLI_USAGE'}})
    elif kind == 'quota':
        developer.update(
            state='BLOCKED', error_code='CLAUDE_QUOTA_EXHAUSTED', artifact=None,
            artifact_sha256=None, usage=None, progress={'head_sha': source}, execution={
                'status': 'BLOCKED', 'native_session_id': None, 'process_exit_code': 1,
                'stdout_sha256': None, 'elapsed_ms': 1, 'cleanup_status': 'UNKNOWN',
                'usage_source': 'UNKNOWN',
                'provider_evidence': {'condition': 'QUOTA_EXHAUSTED'}})
    elif kind == 'no_changes':
        developer.update(
            state='BLOCKED', error_code='CLAUDE_NO_CHANGES', artifact=None,
            artifact_sha256=None, usage=None, execution={
                'status': 'BLOCKED', 'native_session_id': 'synthetic-session',
                'process_exit_code': 0, 'stdout_sha256': None, 'elapsed_ms': 1,
                'cleanup_status': 'CONFIRMED', 'usage_source': 'CLI_REPORTED',
                'provider_evidence': {'accepted': True, 'basis': 'CLI_USAGE'}})
    else:
        raise AssertionError(kind)
    return developer


def _chain(rounds, *, profiles=None):
    """{task_id: record} for rounds linked by continues, plus the unclaimed next Task."""
    records, previous = {}, None
    for index, (prefix, kind, created_at, source) in enumerate(rounds):
        task_id = _task_id(prefix)
        executor = 'executor-' + prefix   # every round on its own D6
        developer = _developer(kind, created_at, source,
                               profile=profiles[index] if profiles else 'claude')
        developer['executor_task_id'] = executor
        record = {
            'task_id': task_id, 'developer': developer,
            'completion': {'state': 'WAITING_EXECUTOR', 'executor_task_id': executor,
                           'reviewer_task_id': None,
                           'evidence': {'task_id': task_id, 'execution_mode': 'DIRECT_D6',
                                        'source_sha': source, 'result_head': None,
                                        'pr_number': 2383}},
        }
        if previous is not None:
            record['continues'] = previous
        records[task_id] = record
        previous = task_id
    records[CURRENT] = {'task_id': CURRENT, 'continues': previous,
                        'developer': None, 'completion': None}
    return records


def _real_rounds(count):
    """count runs that each did real work, on new Tasks, D6s and minutes."""
    return tuple((f'ab{number:06x}', 'responded', f'2026-11-19T0{number}:00:00Z',
                  _sha(f'ab{number:06x}')) for number in range(count))


class _ChainStore:
    """A read-only in-memory store; the guard must never write a record."""

    def __init__(self, records):
        self.records = records

    def task_ids(self):
        return list(self.records)

    def get(self, task_id):
        return self.records[task_id]


class EffectiveRepairCountTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(repair_policy.load_policy()['repair_max'], 5)
        self.limits = Mock(wraps=task_submission_cli._check_repair_limits)
        self.rca = Mock(return_value=None)
        for target, kwargs in (
                ((task_submission_cli, '_check_repair_limits'), {'new': self.limits}),
                ((task_submission_cli, '_check_repair_rca'), {'new': self.rca}),
                # Not a start failure (#2188): the skip under test is the new one.
                ((task_submission_cli.start_failure, 'record_checks'),
                 {'return_value': SimpleNamespace(start_failure=False)}),
                # No Git: an unreadable D6 proves nothing, so a run would count.
                ((task_submission_cli.task_completion, '_worktree_diff'),
                 {'side_effect': OSError('no git in tests')})):
            started = patch.object(*target, **kwargs)
            started.start()
            self.addCleanup(started.stop)

    def _gate(self, records):
        """(result or error, counted run IDs) of one guarded claim for the next Task."""
        before = copy.deepcopy(records)
        store = _ChainStore(records)
        claim = Mock(return_value='CLAIMED')
        args = SimpleNamespace(task_id=CURRENT, executor_task=NEXT_EXECUTOR,
                               high_debug_ref=None, high_debug_task=None,
                               high_debug_family=None, repair_packet=None)
        binding = (SimpleNamespace(task_id=NEXT_EXECUTOR, root='/synthetic-d6',
                                   source_sha=NEXT_SOURCE),
                   SimpleNamespace(head=NEXT_SOURCE))
        try:
            outcome = task_submission_cli._delegate_chain_gate(args, store, binding, claim)
        except LLMToolError as error:
            outcome = error
            claim.assert_not_called()
        else:
            claim.assert_called_once_with()
        # A2: recorded history is read as-is, never rewritten.
        self.assertEqual(records, before)
        counted = set(self.limits.call_args.args[5])
        return outcome, counted

    def _effective_repairs(self, counted):
        # The first effective run of the chain is the original run, not a repair.
        return max(0, len(counted) - 1)

    def test_pr_2383_rounds_count_two_effective_repairs_and_do_not_refuse(self):
        outcome, counted = self._gate(_chain(PR_2383_ROUNDS))
        self.assertEqual(outcome, 'CLAIMED')
        self.assertEqual(counted, EFFECTIVE_RUNS)
        self.assertEqual(self._effective_repairs(counted), 2)
        # The next round is repair #3: the covering root cause is checked, not the limit.
        self.rca.assert_called_once()
        self.assertIs(self.rca.call_args.kwargs.get('covered'), True)

    def test_first_run_of_the_chain_is_not_a_repair(self):
        for rounds in (PR_2383_ROUNDS[:1], PR_2383_ROUNDS[:2]):
            self.rca.reset_mock()
            with self.subTest(rounds=len(rounds)):
                outcome, counted = self._gate(_chain(rounds))
                self.assertEqual(outcome, 'CLAIMED')
                self.assertEqual(counted, {_task_id('983dcdb1')})
                self.assertEqual(self._effective_repairs(counted), 0)
                self.rca.assert_not_called()

    def test_a_chain_opened_by_a_quota_stop_starts_at_the_first_real_run(self):
        rounds = PR_2383_ROUNDS[1:3]   # quota stop, then the first real run
        outcome, counted = self._gate(_chain(rounds))
        self.assertEqual(outcome, 'CLAIMED')
        self.assertEqual(counted, {_task_id('99fa743f')})
        self.rca.assert_not_called()

    def test_five_real_repairs_still_refuse(self):
        outcome, counted = self._gate(_chain(_real_rounds(6)))
        self.assertEqual(str(outcome), 'DELEGATE_REPAIR_LIMIT_REACHED')
        self.assertEqual(self._effective_repairs(counted), 5)

    def test_five_real_repairs_across_routes_d6s_and_tasks_still_refuse(self):
        # A2: the count follows the chain across Task, D6, PR and route.
        profiles = ('claude', 'agy', 'codex', 'opencode', 'claude', 'gw-agent')
        outcome, counted = self._gate(_chain(_real_rounds(6), profiles=profiles))
        self.assertEqual(str(outcome), 'DELEGATE_REPAIR_LIMIT_REACHED')
        self.assertEqual(len(counted), 6)

    def test_skipped_rounds_do_not_hide_five_real_repairs(self):
        real = _real_rounds(6)
        rounds = real[:2] + PR_2383_ROUNDS[1:2] + real[2:4] + PR_2383_ROUNDS[3:4] + \
            real[4:] + PR_2383_ROUNDS[5:]
        outcome, counted = self._gate(_chain(rounds))
        self.assertEqual(str(outcome), 'DELEGATE_REPAIR_LIMIT_REACHED')
        self.assertEqual(counted, {_task_id(prefix) for prefix, *_ in real})

    def _with_round(self, index, change):
        records = _chain(PR_2383_ROUNDS)
        task_id = _task_id(PR_2383_ROUNDS[index][0])
        change(records[task_id], records[task_id]['developer'])
        return records, task_id

    def _assert_round_counts(self, index, change):
        records, task_id = self._with_round(index, change)
        outcome, counted = self._gate(records)
        # Counted, but one more prior run is still under the limit.
        self.assertEqual(outcome, 'CLAIMED')
        self.assertEqual(counted, EFFECTIVE_RUNS | {task_id})
        self.assertEqual(self._effective_repairs(counted), 3)

    def test_unknown_round_still_counts(self):
        no_changes = 5
        changes = {
            'outcome unknown': lambda record, dev: dev.update(
                state='DEVELOPER_OUTCOME_UNKNOWN'),
            'result unknown': lambda record, dev: dev.update(
                error_code='CLAUDE_RESULT_UNKNOWN'),
            'execution status unknown': lambda record, dev: dev['execution'].update(
                status='UNKNOWN'),
            'cleanup unknown': lambda record, dev: dev['execution'].update(
                cleanup_status='UNKNOWN'),
            'no execution: error code alone': lambda record, dev: dev.update(
                execution=None),
            'nonzero exit': lambda record, dev: dev['execution'].update(
                process_exit_code=1),
            'exit not reported': lambda record, dev: dev['execution'].update(
                process_exit_code=None),
            'another route': lambda record, dev: dev.update(provider_profile='agy'),
            'no error code': lambda record, dev: dev.update(error_code=None),
        }
        for name, change in changes.items():
            with self.subTest(name=name):
                self._assert_round_counts(no_changes, change)

    def test_unknown_quota_round_still_counts(self):
        quota = 1
        changes = {
            'outcome unknown': lambda record, dev: dev.update(
                state='DEVELOPER_OUTCOME_UNKNOWN'),
            'result unknown': lambda record, dev: dev.update(
                error_code='CLAUDE_RESULT_UNKNOWN'),
            'execution status unknown': lambda record, dev: dev['execution'].update(
                status='UNKNOWN'),
            'no provider condition': lambda record, dev: dev['execution'].pop(
                'provider_evidence'),
            'malformed execution': lambda record, dev: dev.update(execution='BLOCKED'),
        }
        for name, change in changes.items():
            with self.subTest(name=name):
                self._assert_round_counts(quota, change)

    def test_quota_stop_that_changed_files_still_counts(self):
        quota = 1
        other_head = _sha('feed')

        def drop(key):
            return lambda record, dev: dev.pop(key)
        changes = {
            'changed files': lambda record, dev: dev.update(changed_files=['README.md']),
            'malformed changed files': lambda record, dev: dev.update(changed_files='README.md'),
            'progress changed files': lambda record, dev: dev['progress'].update(
                changed_files=['README.md']),
            'progress diff': lambda record, dev: dev['progress'].update(diff_bytes=12),
            'progress head moved': lambda record, dev: dev['progress'].update(
                head_sha=other_head),
            'malformed progress': lambda record, dev: dev.update(progress='moved'),
            'artifact': lambda record, dev: dev.update(artifact=ARTIFACT),
            'artifact digest': lambda record, dev: dev.update(
                artifact_sha256=hashlib.sha256(ARTIFACT.encode('utf-8')).hexdigest()),
            'missing artifact': drop('artifact'),
            'missing artifact digest': drop('artifact_sha256'),
            'missing source': drop('source_sha'),
            'completion result head': lambda record, dev: record['completion'][
                'evidence'].update(result_head=other_head),
            'completion result head equal to source': lambda record, dev: record[
                'completion']['evidence'].update(result_head=dev['source_sha']),
            'callback result head': lambda record, dev: dev['direct_result_callback'][
                'evidence'].update(result_head=other_head),
            'malformed callback': lambda record, dev: dev.update(
                direct_result_callback='done'),
        }
        for name, change in changes.items():
            with self.subTest(name=name):
                self._assert_round_counts(quota, change)

    def test_malformed_completion_of_a_stop_refuses_before_counting(self):
        # The chain view is in doubt: the guard refuses instead of skipping.
        records, _ = self._with_round(1, lambda record, dev: record.update(completion='done'))
        before = copy.deepcopy(records)
        claim = Mock(return_value='CLAIMED')
        args = SimpleNamespace(task_id=CURRENT, executor_task=NEXT_EXECUTOR,
                               high_debug_ref=None, high_debug_task=None,
                               high_debug_family=None, repair_packet=None)
        binding = (SimpleNamespace(task_id=NEXT_EXECUTOR, root='/synthetic-d6',
                                   source_sha=NEXT_SOURCE),
                   SimpleNamespace(head=NEXT_SOURCE))
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_REPAIR_COUNT_UNAVAILABLE'):
            task_submission_cli._delegate_chain_gate(args, _ChainStore(records), binding, claim)
        claim.assert_not_called()
        self.assertEqual(records, before)

    def test_no_changes_run_with_change_evidence_still_counts(self):
        no_changes = 5
        changes = {
            'changed files': lambda record, dev: dev.update(changed_files=['README.md']),
            'progress head moved': lambda record, dev: dev.update(
                progress={'head_sha': _sha('feed')}),
            'progress without head_sha': lambda record, dev: dev.update(
                progress={'head': dev['source_sha']}),
            'empty progress': lambda record, dev: dev.update(progress={}),
            'completion result head': lambda record, dev: record['completion'][
                'evidence'].update(result_head=_sha('feed')),
            'artifact': lambda record, dev: dev.update(artifact=ARTIFACT),
            'quota condition on a NO_CHANGES code': lambda record, dev: dev[
                'execution'].update(provider_evidence={'condition': 'QUOTA_EXHAUSTED'}),
        }
        for name, change in changes.items():
            with self.subTest(name=name):
                self._assert_round_counts(no_changes, change)

    def test_quota_stop_without_affirmative_progress_still_counts(self):
        # Codex security v91 (CWE-863): a quota stop is excluded only on
        # complete, consistent route-recorded no-change evidence.
        quota = 1
        changes = {
            'missing progress':lambda record, dev: dev.pop('progress'),
            'progress None': lambda record, dev: dev.update(progress=None),
            'empty progress': lambda record, dev: dev.update(progress={}),
            'progress head only, not the route head_sha': lambda record, dev: dev.update(
                progress={'head': dev['source_sha']}),
            'progress head_sha None': lambda record, dev: dev.update(
                progress={'head_sha': None}),
            'progress head_sha malformed': lambda record, dev: dev.update(
                progress={'head_sha': ['not', 'a', 'sha']}),
            'progress head_sha uppercase': lambda record, dev: dev.update(
                progress={'head_sha': dev['source_sha'].upper()}),
            'progress non-str key': lambda record, dev: dev['progress'].update(
                {1: None}),
        }
        for name, change in changes.items():
            with self.subTest(name=name):
                self._assert_round_counts(quota, change)

    def test_quota_stop_with_forged_or_contradictory_evidence_still_counts(self):
        quota = 1
        other_head = _sha('feed')
        changes = {
            'progress head contradicts head_sha': lambda record, dev: dev[
                'progress'].update(head=other_head),
            'progress result head': lambda record, dev: dev['progress'].update(
                result_head=other_head),
            'progress head_sha differs from source': lambda record, dev: dev.update(
                progress={'head_sha': other_head}),
            'source rewritten to the progress head': lambda record, dev: dev.update(
                source_sha=other_head, progress={'head_sha': other_head}),
            'quota condition on a non-quota code': lambda record, dev: dev.update(
                error_code='CLAUDE_NO_CHANGES'),
            'quota condition on a DONE execution': lambda record, dev: dev[
                'execution'].update(status='DONE'),
            'completion source differs': lambda record, dev: record['completion'][
                'evidence'].update(source_sha=other_head),
            'callback source differs': lambda record, dev: dev['direct_result_callback'][
                'evidence'].update(source_sha=other_head),
        }
        for name, change in changes.items():
            with self.subTest(name=name):
                self._assert_round_counts(quota, change)

    def test_proved_stops_with_empty_progress_lists_are_still_skipped(self):
        def change(record, dev):
            dev['progress'].update(changed_files=[])
            dev['changed_files'] = []
        records, _ = self._with_round(1, change)
        outcome, counted = self._gate(records)
        self.assertEqual(outcome, 'CLAIMED')
        self.assertEqual(counted, EFFECTIVE_RUNS)

    def test_same_d6_quota_stop_with_missing_or_inconsistent_evidence_still_counts(self):
        # Security finding v92 on PR #2405 (CWE-863): when a quota stop is on
        # the same D6 as the target D6 and cleanup is confirmed, missing,
        # partial, or contradictory execution evidence must still count.
        other_head = _sha('feed')
        with patch.object(task_submission_cli.task_completion, '_worktree_diff',
                          return_value=([], b'')), \
                patch.object(task_submission_cli, '_git_status_paths',
                             return_value=()):
            def change_affirmative(record, dev):
                dev.update(source_sha=NEXT_SOURCE, progress={'head_sha': NEXT_SOURCE},
                           executor_task_id=NEXT_EXECUTOR)
                dev['execution'].update(cleanup_status='CONFIRMED')
                dev['direct_result_callback']['evidence'].update(source_sha=NEXT_SOURCE)
                record['completion'].update(executor_task_id=NEXT_EXECUTOR)
                record['completion']['evidence'].update(source_sha=NEXT_SOURCE)
            records, _ = self._with_round(1, change_affirmative)
            outcome, counted = self._gate(records)
            self.assertEqual(outcome, 'CLAIMED')
            self.assertEqual(counted, EFFECTIVE_RUNS)

            changes = {
                'missing progress': lambda record, dev: dev.pop('progress'),
                'progress None': lambda record, dev: dev.update(progress=None),
                'empty progress': lambda record, dev: dev.update(progress={}),
                'progress head moved': lambda record, dev: dev['progress'].update(
                    head_sha=other_head),
                'progress changed files': lambda record, dev: dev['progress'].update(
                    changed_files=['README.md']),
                'changed files on developer': lambda record, dev: dev.update(
                    changed_files=['README.md']),
                'completion result head': lambda record, dev: record['completion'][
                    'evidence'].update(result_head=other_head),
                'callback result head': lambda record, dev: dev['direct_result_callback'][
                    'evidence'].update(result_head=other_head),
                'callback source differs': lambda record, dev: dev['direct_result_callback'][
                    'evidence'].update(source_sha=other_head),
            }
            for name, change in changes.items():
                with self.subTest(case=name):
                    def apply_change(record, dev):
                        change_affirmative(record, dev)
                        change(record, dev)
                    records, task_id = self._with_round(1, apply_change)
                    outcome, counted = self._gate(records)
                    self.assertEqual(outcome, 'CLAIMED')
                    self.assertEqual(counted, EFFECTIVE_RUNS | {task_id})
                    self.assertEqual(self._effective_repairs(counted), 3)


class EvidencedNoWorkStopUnitTests(unittest.TestCase):
    """The predicate alone: False for any doubt, never a write."""

    def _record(self, kind):
        records = _chain(PR_2383_ROUNDS)
        prefix = {'quota': '30f92678', 'no_changes': '198e4ead',
                  'responded': '983dcdb1'}[kind]
        return records[_task_id(prefix)]

    def test_proved_stops(self):
        for kind in ('quota', 'no_changes'):
            record = self._record(kind)
            with self.subTest(kind=kind):
                self.assertIs(task_submission_cli._evidenced_no_work_stop(
                    record['developer'], record), True)

    def test_quota_stop_needs_route_progress_head_sha(self):
        record = self._record('quota')
        source = record['developer']['source_sha']
        for progress in ('absent', None, {}, {'head': source}, {'head_sha': None},
                         {'head_sha': source, 'head': _sha('feed')}):
            developer = copy.deepcopy(record['developer'])
            if progress == 'absent':
                del developer['progress']
            else:
                developer['progress'] = progress
            with self.subTest(progress=progress):
                self.assertIs(task_submission_cli._evidenced_no_work_stop(
                    developer, record), False)

    def test_progress_predicate_fails_closed(self):
        source = _sha('abc')
        self.assertIs(task_submission_cli._progress_shows_no_change(
            {'head_sha': source, 'changed_files': []}, source), True)
        for progress in (None, 'x', [], {}, {'head': source},
                         {'head_sha': source, 'head': _sha('feed')},
                         {'head_sha': source, 'changed_files': ['README.md']},
                         {'head_sha': source, 1: None}):
            with self.subTest(progress=progress):
                self.assertIs(task_submission_cli._progress_shows_no_change(
                    progress, source), False)
        self.assertIs(task_submission_cli._progress_shows_no_change(
            {'head_sha': None}, None), False)

    def test_real_run_and_missing_record(self):
        record = self._record('responded')
        self.assertIs(task_submission_cli._evidenced_no_work_stop(
            record['developer'], record), False)
        for kind in ('quota', 'no_changes'):
            record = self._record(kind)
            with self.subTest(kind=kind):
                # Without the Task record the result head is unknown: the run counts.
                self.assertIs(task_submission_cli._evidenced_no_work_stop(
                    record['developer'], None), False)
        for developer in (None, 'BLOCKED', [], {}):
            with self.subTest(developer=developer):
                self.assertIs(task_submission_cli._evidenced_no_work_stop(
                    developer, {}), False)


if __name__ == '__main__':
    unittest.main()
