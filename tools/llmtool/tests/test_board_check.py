"""Tests for ``llmtool board-check`` (#763), using fakes for every evidence read.

Owner tests covered: (1) actionable work with no dispatch is flagged; (2) a Task
done with no Reviewer is flagged; (4) changing only row text or time does not
change a state; (5) a fake or stale Task ID is not RUNNING; (7) every Developer
busy or waiting a legal authorization is not a PM fault.
"""

import contextlib
import copy
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import board_check, task_status, worker_dispatch  # noqa: E402
from llmtool.task_store import _json_bytes  # noqa: E402

NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)
HEAD = 'a' * 40
OTHER_HEAD = 'b' * 40
RUN = 'c' * 32
ASSIGNMENT = 'd' * 32
TASK_RUNNING = '1' * 32
TASK_PENDING = '2' * 32
TASK_REVIEW = '3' * 32
TASK_UNKNOWN = '4' * 32
ROOT = '/nonexistent-board-check'
OPEN_PRS = 'pulls?state=open&per_page=100&page=1'
CHECKS = f'commits/{HEAD}/check-runs?per_page=100'
ALLOWED_CODES = ('OWNER_POLICY_ALLOWED', 'OWNER_ASSIGNMENT_MATCHED')


def at(hour, minute=0, second=0):
    return datetime(2026, 11, 7, hour, minute, second, tzinfo=timezone.utc)


class FakeStore:
    """Read-only TaskStore stand-in; any write fails the test."""

    def __init__(self, *records):
        self.records = {item['task_id']: copy.deepcopy(item) for item in records}
        self.writes = []

    def task_ids(self):
        return sorted(self.records)

    def get(self, task_id):
        return copy.deepcopy(self.records[task_id])

    def _write(self, *args, **kwargs):
        self.writes.append((args, kwargs))
        raise AssertionError('board-check must never write a Task')

    update = compare_and_update = create = _write


class FakeGitHub:
    """GET adapter over a fixed response table; unknown paths fail the read."""

    def __init__(self, responses=None):
        self.responses = {OPEN_PRS: []}
        self.responses.update(responses or {})
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        if path not in self.responses:
            raise RuntimeError(f'unexpected GitHub read {path}')
        value = self.responses[path]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)


def policy(**codes):
    """Fake worker_dispatch.evaluate; workers default to OWNER_POLICY_ALLOWED."""
    calls = []

    def evaluate(worker, owner_ref=None, task_id=None):
        calls.append((worker, owner_ref, task_id))
        code = codes.get(worker.replace('-', '_'), 'OWNER_POLICY_ALLOWED')
        return worker_dispatch.PolicyDecision(worker, code in ALLOWED_CODES, code, 'f' * 64)
    evaluate.calls = calls
    return evaluate


def process(pid, d6_task, started_at):
    return {'pid': pid, 'cwd': f'{ROOT}/{d6_task}', 'started_at': started_at}


def d6_reader(**snapshots):
    def read_d6(record, d6_task):
        key = d6_task.replace('-', '_') if type(d6_task) is str else None
        if key not in snapshots:
            raise RuntimeError('D6 not registered')
        return copy.deepcopy(snapshots[key])
    return read_d6


def d6_root(d6_task):
    return {'root': f'{ROOT}/{d6_task}'}


def d6_result(d6_task, head=HEAD):
    return {'root': f'{ROOT}/{d6_task}', 'result_head': head, 'recorded_result_head': head,
            'dirty': False, 'state': 'CLEAN', 'branch': 'feat/x', 'task_id': d6_task}


def audit(when, *, worker='claude-developer', decision='ALLOWED', action='DISPATCH'):
    return {'attempt_id': 'e' * 32, 'worker_key': worker, 'action': action,
            'decision': decision, 'reason_code': 'OWNER_POLICY_ALLOWED',
            'policy_sha256': 'f' * 64, 'at': when, 'assignment_id': ASSIGNMENT,
            'source_sha': 'f' * 40, 'contract_digest': 'f' * 64}


def dispatching(task_id, d6_task='d6-run', *, profile='claude',
                audits=('2026-11-07T11:00:00Z',)):
    worker = worker_dispatch.worker_key_for(profile)
    return {'task_id': task_id, 'run_id': RUN, 'state': 'PM_RESPONDED',
            'developer': {'state': 'DEVELOPER_DISPATCHING', 'provider_profile': profile,
                          'assignment_id': ASSIGNMENT, 'executor_task_id': d6_task},
            'dispatch_audit': [audit(item, worker=worker) for item in audits]}


def pending(task_id):
    return {'task_id': task_id, 'run_id': RUN, 'state': 'PM_RESPONDED',
            'developer': {'state': 'DEVELOPER_PENDING', 'provider_profile': 'claude',
                          'assignment_id': ASSIGNMENT}}


def receipt(task_id, d6_task, head=HEAD):
    value = {'task_id': task_id, 'run_id': RUN, 'executor_task': d6_task,
             'source_sha': head, 'result': 'PASS', 'modules': ['tests.test_x']}
    value['receipt_digest'] = hashlib.sha256(_json_bytes(value)).hexdigest()
    return value


def responded(task_id, d6_task='d6-dev', *, pr=41, claims=None, returned=False):
    """A Developer-finished Task with an exact-head EC2 PASS and an open PR."""
    record = {
        'task_id': task_id, 'run_id': RUN, 'state': 'PM_RESPONDED',
        'developer': {'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'claude',
                      'assignment_id': ASSIGNMENT, 'executor_task_id': d6_task},
        'completion': {'state': 'WAITING_REVIEW', 'executor_task_id': d6_task,
                       'reviewer_task_id': None, 'evidence': {}, 'missing_gates': [],
                       'pm_dispatch': 'NOT_STARTED', 'pm_response': None,
                       'pm_recommendation': None, 'pm_usage': None, 'pm_error_code': None},
        'published_pr': {'pr': pr, 'head_sha': HEAD, 'recorded_at': '2026-11-07T11:00:00Z'},
        'remote_test_receipts': [receipt(task_id, d6_task)],
    }
    if claims is not None:
        record['review_dispatch_claim'] = claims
    if returned:
        # The Developer result was returned to the PM; no review has started.
        record['developer']['direct_result_callback'] = {
            'state': 'PM_RETURNED', 'executor_task_id': d6_task, 'missing_gates': [],
            'evidence': {'review_pr_ci': {'stage': 'EXECUTOR_RESULT',
                                          'formal_review_state': 'NOT_STARTED',
                                          'ci_state': 'NOT_STARTED'}},
            'pm_dispatch': 'RETURNED', 'pm_response': None, 'pm_recommendation': None,
            'pm_usage': None, 'pm_error_code': None}
    return record


def claim(*, head=HEAD, state='DISPATCHED', claimed_at='2026-11-07T11:30:00.000000Z'):
    return {'pr': 41, 'head_sha': head, 'version': 1, 'state': state,
            'reviewer_task': 'd6-rev', 'claimed_at': claimed_at}


def pull(number=41, *, state='open', merged=False, head=HEAD):
    return {'number': number, 'state': state, 'merged': merged, 'head': {'sha': head}}


def checks(*statuses, head=HEAD):
    runs = [{'name': f'check {index}', 'status': status, 'head_sha': head}
            for index, status in enumerate(statuses)]
    return {'total_count': len(runs), 'check_runs': runs}


def run(rows, *, store=None, github=None, processes=(), d6=None, evaluate=None, now=NOW):
    return board_check.check_board(
        rows, store=store or FakeStore(), github_get=github or FakeGitHub(),
        list_processes=lambda: [dict(item) for item in processes],
        read_d6=d6 or d6_reader(), evaluate=evaluate or policy(), now=now)


def states(result):
    return {item['row']: item['state'] for item in result['rows']}


class BoardCheckCase(unittest.TestCase):
    def assertRow(self, result, row, state, evidence=None):
        found = [item for item in result['rows'] if item['row'] == row]
        self.assertEqual(len(found), 1, result)
        self.assertEqual(found[0]['state'], state, result)
        if evidence is not None:
            self.assertIn(evidence, ' '.join(found[0]['evidence']), result)


class ActionableWithoutDispatchTests(BoardCheckCase):
    """Owner test 1: actionable work with no dispatch is flagged."""

    def test_undispatched_row_with_a_free_developer_is_actionable(self):
        result = run([{'id': 'work'}])
        self.assertRow(result, 'work', board_check.ACTIONABLE, 'work not dispatched')
        self.assertEqual((result['result'], result['exit_code']), ('FLAGGED', 2))

    def test_pending_task_never_dispatched_is_actionable(self):
        store = FakeStore(pending(TASK_PENDING))
        result = run([{'id': 'pending', 'task_id': TASK_PENDING}], store=store)
        self.assertRow(result, 'pending', board_check.ACTIONABLE, 'DEVELOPER_ATTENTION')
        self.assertEqual(result['exit_code'], 2)
        self.assertEqual(store.writes, [])

    def test_cleared_predicate_is_actionable_and_held_predicate_is_not(self):
        github = FakeGitHub({'pulls/12': pull(12, state='closed', merged=True)})
        result = run([{'id': 'next', 'blocker': 'pr_merged:12'}], github=github)
        self.assertRow(result, 'next', board_check.ACTIONABLE, 'pr_merged:12 cleared')
        self.assertEqual(result['exit_code'], 2)

        github = FakeGitHub({'pulls/12': pull(12), OPEN_PRS: [{'number': 12}],
                             f'commits/{HEAD}/check-runs?per_page=100': checks('completed')})
        result = run([{'id': 'next', 'blocker': 'pr_merged:12'}, {'id': 'pr', 'pr': 12}],
                     github=github)
        self.assertRow(result, 'next', board_check.BLOCKED, 'PR #12 open')

    def test_expired_time_predicate_is_actionable(self):
        rows = [{'id': 'later', 'blocker': 'time:2026-11-07T11:00:00Z'}]
        self.assertRow(run(rows), 'later', board_check.ACTIONABLE, 'reached')
        self.assertRow(run(rows, now=at(10)), 'later', board_check.BLOCKED, 'not reached')

    def test_open_pr_missing_from_board_is_anomaly(self):
        result = run([], github=FakeGitHub({OPEN_PRS: [{'number': 77}]}))
        self.assertRow(result, 'pr:#77', board_check.ANOMALY, 'missing from the board')
        self.assertEqual(result['exit_code'], 2)

    def test_board_check_only_reads(self):
        github = FakeGitHub()
        store = FakeStore(pending(TASK_PENDING))
        run([{'id': 'pending', 'task_id': TASK_PENDING}], store=store, github=github)
        self.assertEqual(store.writes, [])
        self.assertEqual(github.calls, [OPEN_PRS])


class DoneWithoutReviewerTests(BoardCheckCase):
    """Owner test 2: a Task done with no Reviewer is flagged."""

    def setUp(self):
        self.github = FakeGitHub({'pulls/41': pull(), OPEN_PRS: [{'number': 41}],
                                  CHECKS: checks('completed')})
        self.d6 = d6_reader(d6_dev=d6_result('d6-dev'), d6_rev=d6_root('d6-rev'))

    def check(self, record, processes=()):
        return run([{'id': 'task', 'task_id': record['task_id']}], store=FakeStore(record),
                   github=self.github, d6=self.d6, processes=processes)

    def test_developer_done_and_no_review_dispatched_is_actionable(self):
        result = self.check(responded(TASK_REVIEW))
        self.assertRow(result, 'task', board_check.ACTIONABLE, 'DISPATCH_REVIEW')
        self.assertEqual(result['exit_code'], 2)

    def test_result_returned_to_pm_is_not_done_without_review(self):
        record = responded(TASK_REVIEW, returned=True)
        # Premise: derive_state already calls this Task COMPLETED, unreviewed.
        self.assertEqual(task_status.derive_state(record), 'COMPLETED')
        result = self.check(record)
        self.assertRow(result, 'task', board_check.ACTIONABLE, 'DISPATCH_REVIEW')
        self.assertEqual(result['exit_code'], 2)

    def test_claim_with_dead_reviewer_is_not_waiting_review(self):
        result = self.check(responded(TASK_REVIEW, claims=[claim()]))
        self.assertRow(result, 'task', board_check.ACTIONABLE, 'RECORD_REVIEW_RESULT')
        # A Reviewer process older than the claim is not this review.
        result = self.check(responded(TASK_REVIEW, claims=[claim()]),
                            processes=[process(9, 'd6-rev', at(11, 29, 59))])
        self.assertRow(result, 'task', board_check.ACTIONABLE, 'RECORD_REVIEW_RESULT')

    def test_head_bound_claim_with_live_reviewer_is_waiting_review(self):
        result = self.check(responded(TASK_REVIEW, claims=[claim()]),
                            processes=[process(9, 'd6-rev', at(11, 31))])
        self.assertRow(result, 'task', board_check.WAITING_REVIEW, 'Reviewer process 9')
        self.assertEqual((result['result'], result['exit_code']), ('CLEAN', 0))

    def test_claim_for_another_head_is_not_waiting_review(self):
        result = self.check(responded(TASK_REVIEW, claims=[claim(head=OTHER_HEAD)]),
                            processes=[process(9, 'd6-rev', at(11, 31))])
        self.assertRow(result, 'task', board_check.ACTIONABLE, 'DISPATCH_REVIEW')

    def test_exact_head_pending_check_is_waiting_ci(self):
        self.github.responses[CHECKS] = checks('completed', 'in_progress')
        result = self.check(responded(TASK_REVIEW))
        self.assertRow(result, 'task', board_check.WAITING_CI, 'pending: check 1')
        self.assertEqual(result['exit_code'], 0)

    def test_pending_check_for_another_head_is_not_waiting_ci(self):
        self.github.responses[CHECKS] = checks('in_progress', head=OTHER_HEAD)
        result = self.check(responded(TASK_REVIEW))
        self.assertRow(result, 'task', board_check.ACTIONABLE, 'DISPATCH_REVIEW')

    def test_only_a_merge_marker_makes_a_task_done(self):
        record = responded(TASK_REVIEW)
        record['completion_events'] = [{'source': 'GITHUB', 'marker_type': 'merge',
                                        'marker_status': 'MERGED', 'issue_number': 41}]
        self.github.responses[OPEN_PRS] = []
        result = self.check(record)
        self.assertRow(result, 'task', board_check.DONE, 'merge marker')
        self.assertEqual(result['exit_code'], 0)

    def test_github_read_failure_is_unknown_never_clean(self):
        self.github.responses['pulls/41'] = RuntimeError('network down')
        result = self.check(responded(TASK_REVIEW))
        self.assertRow(result, 'task', board_check.UNKNOWN)
        self.assertEqual((result['result'], result['exit_code']), ('UNKNOWN', 1))


class UnknownRowDoesNotHideOrphansTests(BoardCheckCase):
    """A failed row read stays on that row; a proven orphan stays ANOMALY."""

    def setUp(self):
        self.d6 = d6_reader(d6_dev=d6_result('d6-dev'))

    def test_open_pr_missing_from_board_stays_anomaly_beside_an_unknown_row(self):
        github = FakeGitHub({'pulls/41': RuntimeError('network down'),
                             OPEN_PRS: [{'number': 41}, {'number': 77}]})
        result = run([{'id': 'task', 'task_id': TASK_REVIEW}],
                     store=FakeStore(responded(TASK_REVIEW)), github=github, d6=self.d6)
        self.assertEqual(states(result), {'task': board_check.UNKNOWN,
                                          'pr:#77': board_check.ANOMALY}, result)
        self.assertRow(result, 'pr:#77', board_check.ANOMALY, 'missing from the board')
        self.assertEqual((result['result'], result['exit_code']), ('FLAGGED', 2))

    def test_live_task_missing_from_board_stays_anomaly_beside_an_unknown_row(self):
        github = FakeGitHub({'pulls/41': RuntimeError('network down'),
                             OPEN_PRS: [{'number': 41}]})
        store = FakeStore(responded(TASK_REVIEW), dispatching(TASK_RUNNING))
        result = run([{'id': 'task', 'task_id': TASK_REVIEW}],
                     store=store, github=github, d6=self.d6)
        self.assertEqual(states(result), {'task': board_check.UNKNOWN,
                                          f'task:{TASK_RUNNING}': board_check.ANOMALY}, result)
        self.assertRow(result, f'task:{TASK_RUNNING}', board_check.ANOMALY,
                       'missing from the board')
        self.assertEqual((result['result'], result['exit_code']), ('FLAGGED', 2))

    def test_failed_d6_read_does_not_orphan_the_pr_of_its_own_task(self):
        github = FakeGitHub({OPEN_PRS: [{'number': 41}]})
        result = run([{'id': 'task', 'task_id': TASK_REVIEW}],
                     store=FakeStore(responded(TASK_REVIEW)), github=github, d6=d6_reader())
        self.assertEqual(states(result), {'task': board_check.UNKNOWN}, result)
        self.assertEqual((result['result'], result['exit_code']), ('UNKNOWN', 1))

    def test_failed_process_read_does_not_orphan_the_pr_of_a_dispatching_task(self):
        def broken():
            raise OSError('lsof failed')
        record = dispatching(TASK_RUNNING)
        record['published_pr'] = {'pr': 41, 'head_sha': HEAD,
                                  'recorded_at': '2026-11-07T10:00:00Z'}
        result = board_check.check_board(
            [{'id': 'task', 'task_id': TASK_RUNNING}], store=FakeStore(record),
            github_get=FakeGitHub({OPEN_PRS: [{'number': 41}]}), list_processes=broken,
            read_d6=d6_reader(d6_run=d6_root('d6-run')), evaluate=policy(), now=NOW)
        self.assertEqual(states(result), {'task': board_check.UNKNOWN}, result)
        self.assertEqual((result['result'], result['exit_code']), ('UNKNOWN', 1))


class TextAndTimeTests(BoardCheckCase):
    """Owner test 4: changing only row text or time does not change a state."""

    TEXT_A = {'title': 'Implement board check', 'status': 'RUNNING',
              'note': 'Developer working on it', 'updated_at': '2026-11-07T11:59:00Z'}
    TEXT_B = {'title': 'Board check (done)', 'status': 'DONE', 'state': 'WAITING_REVIEW',
              'note': 'all good, nothing to do', 'updated_at': '2026-11-07T12:00:00Z'}

    def assertSameResult(self, row, **kwargs):
        first = run([dict(row, **self.TEXT_A)], **kwargs)
        second = run([dict(row, **self.TEXT_B)], **kwargs)
        self.assertEqual(first, second)
        return first

    def test_text_does_not_clear_undispatched_work(self):
        result = self.assertSameResult({'id': 'work'})
        self.assertRow(result, 'work', board_check.ACTIONABLE)
        self.assertEqual(result['exit_code'], 2)

    def test_text_and_fresh_time_do_not_revive_a_dead_task(self):
        store = FakeStore(dispatching(TASK_RUNNING))
        result = self.assertSameResult({'id': 'dead', 'task_id': TASK_RUNNING},
                                       store=store, d6=d6_reader(d6_run=d6_root('d6-run')))
        self.assertRow(result, 'dead', board_check.ANOMALY, 'dead Task')
        self.assertEqual(result['exit_code'], 2)

    def test_free_text_blocker_is_anomaly_not_blocked(self):
        result = run([{'id': 'held', 'blocker': 'waiting for the Owner, see chat'}])
        self.assertRow(result, 'held', board_check.ANOMALY, 'free text')
        result = run([{'id': 'held', 'after': 'once review is done'}])
        self.assertRow(result, 'held', board_check.ANOMALY, 'free text')

    def test_owner_ref_without_open_marker_is_not_waiting_owner(self):
        ref = 'https://github.com/o/r/issues/7#issuecomment-5'
        github = FakeGitHub({'issues/7': {'state': 'open'},
                             'issues/7/comments?per_page=100&page=1':
                                 [{'body': 'WAITING_OWNER: please decide'}]})
        result = run([{'id': 'ask', 'owner_ref': ref}], github=github)
        self.assertRow(result, 'ask', board_check.ACTIONABLE)

        github.responses['issues/7/comments?per_page=100&page=1'] = [
            {'body': '<!-- activity: type=waiting_owner status=OPEN -->'}]
        result = run([{'id': 'ask', 'owner_ref': ref}], github=github)
        self.assertRow(result, 'ask', board_check.WAITING_OWNER, 'marker OPEN on open #7')
        self.assertEqual(result['exit_code'], 0)

        github.responses['issues/7/comments?per_page=100&page=1'].append(
            {'body': '<!-- activity: type=waiting_owner status=ANSWERED -->'})
        result = run([{'id': 'ask', 'owner_ref': ref}], github=github)
        self.assertRow(result, 'ask', board_check.ACTIONABLE)


class FakeOrStaleTaskTests(BoardCheckCase):
    """Owner test 5: a fake or stale Task ID is not RUNNING."""

    def check(self, record, processes, task_id=TASK_RUNNING):
        store = FakeStore(record) if record is not None else FakeStore()
        return run([{'id': 'task', 'task_id': task_id}], store=store, processes=processes,
                   d6=d6_reader(d6_run=d6_root('d6-run')))

    def test_live_process_in_d6_root_after_dispatch_is_running(self):
        result = self.check(dispatching(TASK_RUNNING),
                            [process(101, 'd6-run', at(11, 0, 5))])
        self.assertRow(result, 'task', board_check.RUNNING, 'process 101')
        self.assertEqual((result['result'], result['exit_code']), ('CLEAN', 0))

    def test_unknown_task_id_is_anomaly(self):
        result = self.check(None, [process(101, 'd6-run', at(11, 0, 5))], task_id=TASK_UNKNOWN)
        self.assertRow(result, 'task', board_check.ANOMALY, 'not found')
        self.assertEqual(result['exit_code'], 2)

    def test_malformed_task_id_is_anomaly(self):
        result = self.check(None, [], task_id='TASK-763')
        self.assertRow(result, 'task', board_check.ANOMALY, 'unknown Task')

    def test_no_process_is_dead_task(self):
        result = self.check(dispatching(TASK_RUNNING), [])
        self.assertRow(result, 'task', board_check.ANOMALY, 'dead Task')

    def test_process_started_before_dispatch_is_stale(self):
        result = self.check(dispatching(TASK_RUNNING),
                            [process(101, 'd6-run', at(10, 59, 59))])
        self.assertRow(result, 'task', board_check.ANOMALY, 'dead Task')

    def test_process_from_an_earlier_dispatch_is_stale(self):
        record = dispatching(TASK_RUNNING,
                             audits=('2026-11-07T10:00:00Z', '2026-11-07T11:00:00Z'))
        result = self.check(record, [process(101, 'd6-run', at(10, 30))])
        self.assertRow(result, 'task', board_check.ANOMALY, 'dead Task')

    def test_process_in_another_directory_is_not_this_task(self):
        result = self.check(dispatching(TASK_RUNNING),
                            [process(101, 'd6-other', at(11, 30))])
        self.assertRow(result, 'task', board_check.ANOMALY, 'dead Task')

    def test_denied_dispatch_audit_is_not_a_dispatch(self):
        record = dispatching(TASK_RUNNING)
        record['dispatch_audit'][0]['decision'] = 'BLOCKED'
        result = self.check(record, [process(101, 'd6-run', at(11, 30))])
        self.assertRow(result, 'task', board_check.ANOMALY, 'no ALLOWED dispatch audit')

    def test_abandoned_task_is_anomaly(self):
        record = dispatching(TASK_RUNNING)
        record['abandonment'] = {'reason': 'superseded'}
        result = self.check(record, [process(101, 'd6-run', at(11, 30))])
        self.assertRow(result, 'task', board_check.ANOMALY, 'ABANDONED')

    def test_live_task_missing_from_board_is_anomaly(self):
        result = run([], store=FakeStore(dispatching(TASK_RUNNING)),
                     processes=[process(101, 'd6-run', at(11, 30))],
                     d6=d6_reader(d6_run=d6_root('d6-run')))
        self.assertRow(result, f'task:{TASK_RUNNING}', board_check.ANOMALY,
                       'missing from the board')

    def test_row_pr_of_a_running_task_is_on_the_board(self):
        github = FakeGitHub({OPEN_PRS: [{'number': 41}]})
        result = run([{'id': 'task', 'task_id': TASK_RUNNING, 'pr': 41}],
                     store=FakeStore(dispatching(TASK_RUNNING)), github=github,
                     processes=[process(101, 'd6-run', at(11, 30))],
                     d6=d6_reader(d6_run=d6_root('d6-run')))
        self.assertEqual(states(result), {'task': board_check.RUNNING}, result)
        self.assertEqual(result['exit_code'], 0)

    def test_unknown_lease_task_is_anomaly(self):
        result = run([{'id': 'held', 'blocker': f'lease:{TASK_UNKNOWN}'}])
        self.assertRow(result, 'held', board_check.ANOMALY, 'no such Task')

    def test_process_list_failure_is_unknown(self):
        def broken():
            raise OSError('lsof failed')
        result = board_check.check_board(
            [{'id': 'task', 'task_id': TASK_RUNNING}],
            store=FakeStore(dispatching(TASK_RUNNING)), github_get=FakeGitHub(),
            list_processes=broken, read_d6=d6_reader(d6_run=d6_root('d6-run')),
            evaluate=policy(), now=NOW)
        self.assertRow(result, 'task', board_check.UNKNOWN, 'PROCESS_LIST_FAILED')
        self.assertEqual(result['exit_code'], 1)


class CapacityTests(BoardCheckCase):
    """Owner test 7: busy or authorization-waiting Developers are not a PM fault."""

    def test_every_developer_busy_or_waiting_authorization_is_blocked(self):
        store = FakeStore(dispatching(TASK_RUNNING), pending(TASK_PENDING))
        evaluate = policy(opencode_developer='OWNER_PAUSED', agy='OWNER_ASSIGNMENT_REQUIRED')
        rows = [{'id': 'running', 'task_id': TASK_RUNNING},
                {'id': 'pending', 'task_id': TASK_PENDING},
                {'id': 'work'}]
        result = run(rows, store=store, evaluate=evaluate,
                     processes=[process(101, 'd6-run', at(11, 30))],
                     d6=d6_reader(d6_run=d6_root('d6-run')))
        self.assertEqual(states(result), {'running': board_check.RUNNING,
                                          'pending': board_check.BLOCKED,
                                          'work': board_check.BLOCKED}, result)
        self.assertRow(result, 'work', board_check.BLOCKED,
                       f'claude-developer=busy:{TASK_RUNNING}')
        self.assertEqual((result['result'], result['exit_code']), ('CLEAN', 0))

    def test_every_developer_waiting_authorization_is_blocked(self):
        evaluate = policy(claude_developer='OWNER_ASSIGNMENT_REQUIRED',
                          opencode_developer='OWNER_PAUSED', agy='OWNER_PAUSED')
        for row in ({'id': 'work'}, {'id': 'work', 'blocker': 'capacity'}):
            result = run([row], evaluate=evaluate)
            self.assertRow(result, 'work', board_check.BLOCKED, 'awaiting authorization')
            self.assertEqual(result['exit_code'], 0)

    def test_one_free_developer_makes_work_actionable(self):
        evaluate = policy(opencode_developer='OWNER_PAUSED', agy='OWNER_PAUSED')
        result = run([{'id': 'work'}], evaluate=evaluate)
        self.assertRow(result, 'work', board_check.ACTIONABLE, 'claude-developer')
        self.assertEqual(result['exit_code'], 2)

    def test_dispatch_denied_predicate_follows_policy(self):
        row = {'id': 'agy-work', 'blocker': 'dispatch_denied:agy',
               'owner_ref': 'https://github.com/o/r/issues/289#issuecomment-1'}
        github = FakeGitHub({'issues/289': {'state': 'open'},
                             'issues/289/comments?per_page=100&page=1': []})
        evaluate = policy(agy='OWNER_ASSIGNMENT_REQUIRED')
        result = run([row], github=github, evaluate=evaluate)
        self.assertRow(result, 'agy-work', board_check.BLOCKED, 'OWNER_ASSIGNMENT_REQUIRED')
        self.assertEqual(result['exit_code'], 0)
        self.assertIn(('agy', row['owner_ref'], None), evaluate.calls)

        result = run([row], github=github, evaluate=policy(agy='OWNER_ASSIGNMENT_MATCHED'))
        self.assertRow(result, 'agy-work', board_check.ACTIONABLE, 'dispatch_denied:agy cleared')

    def test_unreadable_policy_is_unknown_not_a_pm_fault(self):
        result = run([{'id': 'work'}], evaluate=policy(claude_developer='WORKER_POLICY_INVALID'))
        self.assertRow(result, 'work', board_check.UNKNOWN, 'WORKER_POLICY_INVALID')
        self.assertEqual((result['result'], result['exit_code']), ('UNKNOWN', 1))


class BoardFileAndCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, 'pm-board.json')

    def write(self, value):
        with open(self.path, 'w', encoding='utf-8') as handle:
            handle.write(value if type(value) is str else json.dumps(value))

    def invoke(self, *, as_json=True):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = board_check.run_board_check(
                SimpleNamespace(board=self.path, json=as_json), store=FakeStore(),
                github_get=FakeGitHub(), list_processes_call=lambda: [],
                read_d6=d6_reader(), evaluate=policy(), now=NOW)
        return code, output.getvalue()

    def test_unreadable_board_exits_1(self):
        for content in (None, '{bad json', {'schema_version': 2, 'rows': []},
                        {'schema_version': True, 'rows': []}, {'schema_version': 1},
                        {'schema_version': 1, 'rows': {}}):
            if content is None:
                if os.path.exists(self.path):
                    os.unlink(self.path)
            else:
                self.write(content)
            code, output = self.invoke()
            self.assertEqual(code, 1, content)
            self.assertEqual(json.loads(output)['result'], 'UNREADABLE')

    def test_symlinked_board_is_unreadable(self):
        target = os.path.join(self.tmp.name, 'real.json')
        with open(target, 'w', encoding='utf-8') as handle:
            json.dump({'schema_version': 1, 'rows': []}, handle)
        os.symlink(target, self.path)
        self.assertEqual(self.invoke()[0], 1)

    def test_clean_board_exits_0(self):
        self.write({'schema_version': 1, 'rows': []})
        code, output = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)['result'], 'CLEAN')

    def test_text_output_names_row_state_and_evidence(self):
        self.write({'schema_version': 1, 'rows': [{'id': 'work'}]})
        code, output = self.invoke(as_json=False)
        self.assertEqual(code, 2)
        self.assertIn('board-check: FLAGGED', output)
        self.assertIn('work ACTIONABLE row has no Task or PR', output)

    def test_cli_verb_is_wired(self):
        from llmtool import cli
        args = cli.parser_for_cli().parse_args(['board-check', '--board', self.path, '--json'])
        self.assertEqual((args.command, args.board, args.json),
                         ('board-check', self.path, True))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.main(['board-check', '--board', os.path.join(self.tmp.name, 'none')])
        self.assertEqual(code, 1)
        self.assertIn('UNREADABLE', output.getvalue())


class AdapterTests(unittest.TestCase):
    def test_github_reader_issues_one_read_only_get(self):
        calls = []

        def runner(cwd, args):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout=b'{"number": 41}')
        with mock.patch('llmtool.config.repository_config', return_value={'slug': 'o/r'}):
            get = board_check.github_reader(runner)
            self.assertEqual(get('pulls/41'), {'number': 41})
        self.assertEqual(calls, [['api', '--method', 'GET', 'repos/o/r/pulls/41']])

    def test_github_reader_failure_raises(self):
        def runner(cwd, args):
            return SimpleNamespace(returncode=1, stdout=b'')
        with mock.patch('llmtool.config.repository_config', return_value={'slug': 'o/r'}):
            with self.assertRaises(Exception):
                board_check.github_reader(runner)('pulls/41')

    def test_list_processes_parses_cwd_and_start(self):
        outputs = {'lsof': b'p123\nfcwd\nn/work/d6-run\n',
                   'ps': b'  123 Sat Nov  7 11:00:05 2026\n'}

        def fake_run(argv, **kwargs):
            return SimpleNamespace(returncode=0, stdout=outputs[argv[0]])
        items = board_check.list_processes(fake_run)
        self.assertEqual([(item['pid'], item['cwd']) for item in items],
                         [(123, '/work/d6-run')])
        self.assertIsNotNone(items[0]['started_at'].tzinfo)

    def test_list_processes_failure_raises(self):
        def fake_run(argv, **kwargs):
            return SimpleNamespace(returncode=1, stdout=b'')
        with self.assertRaises(Exception):
            board_check.list_processes(fake_run)


if __name__ == '__main__':
    unittest.main()
