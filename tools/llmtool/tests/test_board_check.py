"""Tests for ``llmtool board-check`` (#763), using fakes for every evidence read.

Owner tests covered: (1) actionable work with no dispatch is flagged; (2) a Task
done with no Reviewer is flagged; (4) changing only row text or time does not
change a state; (5) a fake or stale Task ID is not RUNNING; (7) every Developer
busy or waiting a legal authorization is not a PM fault. High Debug liveness
defects F1-F9 (PR 1113) have permanent regressions, named in their class docstrings.

Dispatch ledger (#763 T2 part 1) tests use a real TaskStore and ledger under a
temporary HOME: (6) two schedule runs give one claim and one bound Task; (8) a
fresh session reads the persisted ledger, refuses a duplicate claim and reports
the bound Task's next step. Every test runs under a temporary HOME, so none reads
the real ~/.local/state ledger or TaskStore.

Part 2 adds (3) a merged PR row with a next item lists it as pending, not DONE,
and IDLE_RUNNABLE for every ACTIONABLE row without a live ledger claim.
"""

import contextlib
import copy
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

LLMTOOL_ROOT = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, LLMTOOL_ROOT)

from llmtool import board_check, task_advance, task_status, worker_dispatch  # noqa: E402
from llmtool.task_store import TaskStore, _json_bytes  # noqa: E402

_MODULE_HOME = []


def setUpModule():
    """Point HOME at a private temporary directory for every test in this module."""
    home = tempfile.TemporaryDirectory()
    patcher = mock.patch.dict(os.environ, {'HOME': os.path.realpath(home.name)})
    patcher.start()
    _MODULE_HOME.extend((patcher, home))


def tearDownModule():
    patcher, home = _MODULE_HOME
    patcher.stop()
    home.cleanup()
    del _MODULE_HOME[:]

NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)
HEAD = 'a' * 40
OTHER_HEAD = 'b' * 40
RUN = 'c' * 32
ASSIGNMENT = 'd' * 32
TASK_RUNNING = '1' * 32
TASK_PENDING = '2' * 32
TASK_REVIEW = '3' * 32
TASK_UNKNOWN = '4' * 32
TASK_CHILD = '5' * 32
TASK_OTHER = '6' * 32
SESSION = '11111111-2222-3333-4444-555555555555'
OTHER_SESSION = '66666666-7777-8888-9999-000000000000'
ROOT = '/nonexistent-board-check'
OPEN_PRS = 'pulls?state=open&per_page=100&page=1'
CHECKS = f'commits/{HEAD}/check-runs?per_page=100'
REVIEW_COMMENTS = 'issues/41/comments?per_page=100'
ALLOWED_CODES = ('OWNER_POLICY_ALLOWED', 'OWNER_ASSIGNMENT_MATCHED')


def at(hour, minute=0, second=0):
    return datetime(2026, 11, 7, hour, minute, second, tzinfo=timezone.utc)


class FakeStore:
    """Read-only TaskStore stand-in; any write fails the test."""

    def __init__(self, *records, assignments=()):
        self.records = {item['task_id']: copy.deepcopy(item) for item in records}
        self.assignments = list(assignments)
        self.writes = []

    def task_ids(self):
        return sorted(self.records)

    def get(self, task_id):
        return copy.deepcopy(self.records[task_id])

    def get_developer_assignments(self):
        return copy.deepcopy(self.assignments)

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


def process(pid, d6_task, started_at, *, sessions=()):
    start_time = f'{started_at:%a %b} {started_at.day} {started_at:%H:%M:%S %Y}'
    return {'pid': pid, 'cwd': f'{ROOT}/{d6_task}', 'started_at': started_at,
            'start_time': start_time, 'native_session_ids': list(sessions)}


def reads(*snapshots):
    """A process adapter returning one snapshot per read, in order."""
    remaining = iter(snapshots)
    return lambda: [dict(item) for item in next(remaining)]


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


def launch(pid=101, started_at=at(11, 30), *, start_time=None):
    """The execution record TaskStore writes through its trusted launch path."""
    # Raw ``ps -o lstart`` text pads the day, which the adapter normalizes.
    raw = f'{started_at:%a %b} {started_at.day:>2} {started_at:%H:%M:%S %Y}'
    return {'status': 'RUNNING', 'native_session_id': None, 'process_exit_code': None,
            'stdout_sha256': None, 'elapsed_ms': None, 'cleanup_status': 'UNKNOWN',
            'usage_source': 'UNKNOWN', 'source_bytes': None, 'source_file_count': None,
            'source_manifest_sha256': None, 'child_pid': pid,
            'child_start_time': raw if start_time is None else start_time, 'child_pgid': pid}


def dispatching(task_id, d6_task='d6-run', *, profile='opencode',
                audits=('2026-11-07T11:00:00Z',), execution=launch()):
    worker = worker_dispatch.worker_key_for(profile)
    return {'task_id': task_id, 'run_id': RUN, 'state': 'PM_RESPONDED',
            'developer': {'state': 'DEVELOPER_DISPATCHING', 'provider_profile': profile,
                          'assignment_id': ASSIGNMENT, 'executor_task_id': d6_task,
                          'execution': execution},
            'dispatch_audit': [audit(item, worker=worker) for item in audits]}


def claude_dispatching(task_id, d6_task='d6-run', **kwargs):
    """Claude has no launch record; its attempt is proven by a claimed native session."""
    return dispatching(task_id, d6_task, profile='claude', execution=None, **kwargs)


def assignment(task_id, d6_task='d6-run', *, worker='claude-developer', session=SESSION):
    return {'assignment_id': 'e' * 32, 'worker': worker, 'd6_task_id': d6_task,
            'task_id': task_id, 'state': 'CLAIMED',
            'claim': {'session_id': session, 'claimed_at': '2026-11-07T11:00:30Z',
                      'account_dir_label': '0' * 16}}


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


def claim(*, head=HEAD, state='DISPATCHED', claimed_at='2026-11-07T11:30:00.000000Z',
          executor='d6-dev', session=SESSION):
    return {'executor_task': executor, 'pr': 41, 'head_sha': head, 'version': 1,
            'state': state, 'reviewer_task': 'd6-rev', 'native_session_id': session,
            'claimed_at': claimed_at}


def repairing(task_id, child_id=TASK_CHILD, *, executor='d6-dev'):
    """A Task whose review failed, with one DISPATCHED repair claim on the head."""
    record = responded(task_id, claims=[claim()])
    record['completion_events'] = [{'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                                    'outcome': 'REVIEW_FAIL'}]
    record['review_repair_claims'] = [
        {'executor_task': executor, 'pr': 41, 'head_sha': HEAD, 'version': 1, 'attempt': 1,
         'state': 'DISPATCHED', 'comment_id': None, 'packet_sha256': 'f' * 64,
         'claimed_at': '2026-11-07T11:40:00Z', 'repair_task_id': child_id}]
    return record


def repair_child(task_id=TASK_CHILD, d6_task='d6-dev'):
    return dispatching(task_id, d6_task, audits=('2026-11-07T11:45:00Z',),
                       execution=launch(301, at(11, 50)))


def pull(number=41, *, state='open', merged=False, head=HEAD):
    return {'number': number, 'state': state, 'merged': merged, 'head': {'sha': head}}


def checks(*statuses, head=HEAD):
    runs = [{'name': f'check {index}', 'status': status, 'head_sha': head}
            for index, status in enumerate(statuses)]
    return {'total_count': len(runs), 'check_runs': runs}


def run(rows, *, store=None, github=None, processes=(), listing=None, d6=None,
        evaluate=None, now=NOW):
    return board_check.check_board(
        rows, store=store or FakeStore(), github_get=github or FakeGitHub(),
        list_processes=listing or (lambda: [dict(item) for item in processes]),
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
                            processes=[process(9, 'd6-rev', at(11, 31), sessions=[SESSION])])
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
        github = FakeGitHub({'pulls/41': pull(), OPEN_PRS: [{'number': 41}]})
        result = run([{'id': 'task', 'task_id': TASK_REVIEW}],
                     store=FakeStore(responded(TASK_REVIEW)), github=github, d6=d6_reader())
        self.assertEqual(states(result), {'task': board_check.UNKNOWN}, result)
        self.assertRow(result, 'task', board_check.UNKNOWN, 'D6_BINDING_UNAVAILABLE')
        self.assertEqual((result['result'], result['exit_code']), ('UNKNOWN', 1))

    def test_failed_process_read_does_not_orphan_the_pr_of_a_dispatching_task(self):
        def broken():
            raise OSError('lsof failed')
        record = dispatching(TASK_RUNNING)
        record['published_pr'] = {'pr': 41, 'head_sha': HEAD,
                                  'recorded_at': '2026-11-07T10:00:00Z'}
        result = board_check.check_board(
            [{'id': 'task', 'task_id': TASK_RUNNING}], store=FakeStore(record),
            github_get=FakeGitHub({'pulls/41': pull(), OPEN_PRS: [{'number': 41}]}),
            list_processes=broken, read_d6=d6_reader(d6_run=d6_root('d6-run')),
            evaluate=policy(), now=NOW)
        self.assertEqual(states(result), {'task': board_check.UNKNOWN}, result)
        self.assertRow(result, 'task', board_check.UNKNOWN, 'PROCESS_LIST_FAILED')
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

    def test_launched_process_in_d6_root_after_dispatch_is_running(self):
        result = self.check(dispatching(TASK_RUNNING),
                            [process(101, 'd6-run', at(11, 30))])
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
        record = dispatching(TASK_RUNNING, execution=launch(101, at(10, 59, 59)))
        result = self.check(record, [process(101, 'd6-run', at(10, 59, 59))])
        self.assertRow(result, 'task', board_check.ANOMALY, 'dead Task')

    def test_process_from_an_earlier_dispatch_is_stale(self):
        record = dispatching(TASK_RUNNING, execution=launch(101, at(10, 30)),
                             audits=('2026-11-07T10:00:00Z', '2026-11-07T11:00:00Z'))
        result = self.check(record, [process(101, 'd6-run', at(10, 30))])
        self.assertRow(result, 'task', board_check.ANOMALY, 'dead Task')

    def test_process_in_another_directory_is_not_this_task(self):
        result = self.check(dispatching(TASK_RUNNING),
                            [process(202, 'd6-other', at(11, 30))])
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
        github = FakeGitHub({'pulls/41': pull(), OPEN_PRS: [{'number': 41}]})
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


class ProcessIdentityTests(BoardCheckCase):
    """F1-F3: only attempt-bound identity is live; doubt is UNKNOWN, not RUNNING."""

    def check(self, record, processes=(), *, assignments=(), listing=None):
        return run([{'id': 'task', 'task_id': record['task_id']}],
                   store=FakeStore(record, assignments=assignments), processes=processes,
                   listing=listing, d6=d6_reader(d6_run=d6_root('d6-run')))

    def assertUnknown(self, result, code):
        self.assertRow(result, 'task', board_check.UNKNOWN, code)
        self.assertEqual((result['result'], result['exit_code']), ('UNKNOWN', 1))

    def test_cwd_and_time_alone_are_not_running(self):
        result = self.check(claude_dispatching(TASK_RUNNING),
                            [process(202, 'd6-run', at(11, 30))])
        self.assertUnknown(result, 'PROCESS_IDENTITY_UNPROVEN')

    def test_other_process_in_the_root_is_not_the_launched_attempt(self):
        result = self.check(dispatching(TASK_RUNNING), [process(202, 'd6-run', at(11, 31))])
        self.assertRow(result, 'task', board_check.ANOMALY, 'dead Task')

    def test_reused_pid_is_not_the_launched_attempt(self):
        result = self.check(dispatching(TASK_RUNNING), [process(101, 'd6-run', at(11, 45))])
        self.assertRow(result, 'task', board_check.ANOMALY, 'dead Task')

    def test_recorded_start_that_cannot_be_compared_is_unknown(self):
        record = dispatching(TASK_RUNNING, execution=launch(start_time='boot-1234'))
        self.assertUnknown(self.check(record, [process(101, 'd6-run', at(11, 30))]),
                           'PROCESS_START_UNCOMPARABLE')

    def test_launched_process_outside_the_root_is_unknown(self):
        result = self.check(dispatching(TASK_RUNNING), [process(101, 'd6-other', at(11, 30))])
        self.assertUnknown(result, 'PROCESS_CWD_UNPROVEN')

    def test_claimed_native_session_on_the_process_is_running(self):
        result = self.check(claude_dispatching(TASK_RUNNING),
                            [process(202, 'd6-run', at(11, 30), sessions=[SESSION])],
                            assignments=[assignment(TASK_RUNNING)])
        self.assertRow(result, 'task', board_check.RUNNING, 'process 202')

    def test_process_with_another_session_is_not_running(self):
        result = self.check(claude_dispatching(TASK_RUNNING),
                            [process(202, 'd6-run', at(11, 30), sessions=[OTHER_SESSION])],
                            assignments=[assignment(TASK_RUNNING)])
        self.assertUnknown(result, 'PROCESS_IDENTITY_UNPROVEN')

    def test_session_claimed_for_another_task_or_d6_is_not_evidence(self):
        for item in (assignment(TASK_PENDING), assignment(TASK_RUNNING, 'd6-other'),
                     assignment(TASK_RUNNING, worker='agy'),
                     dict(assignment(TASK_RUNNING), state='EXPIRED')):
            result = self.check(claude_dispatching(TASK_RUNNING),
                                [process(202, 'd6-run', at(11, 30), sessions=[SESSION])],
                                assignments=[item])
            self.assertUnknown(result, 'PROCESS_IDENTITY_UNPROVEN')

    def test_unreadable_assignments_are_unknown(self):
        store = FakeStore(claude_dispatching(TASK_RUNNING))
        store.get_developer_assignments = mock.Mock(side_effect=OSError('locked'))
        result = run([{'id': 'task', 'task_id': TASK_RUNNING}], store=store,
                     d6=d6_reader(d6_run=d6_root('d6-run')))
        self.assertUnknown(result, 'ASSIGNMENTS_UNAVAILABLE')

    def test_start_in_the_dispatch_second_is_unknown(self):
        record = dispatching(TASK_RUNNING, execution=launch(101, at(11)))
        self.assertUnknown(self.check(record, [process(101, 'd6-run', at(11))]),
                           'PROCESS_START_AMBIGUOUS')

    def test_start_in_the_next_second_is_ordered(self):
        record = dispatching(TASK_RUNNING, execution=launch(101, at(11, 0, 1)))
        result = self.check(record, [process(101, 'd6-run', at(11, 0, 1))])
        self.assertRow(result, 'task', board_check.RUNNING, 'process 101')

    def test_finer_stamp_inside_the_process_second_is_unknown(self):
        github = FakeGitHub({'pulls/41': pull(), OPEN_PRS: [{'number': 41}],
                             CHECKS: checks('completed')})
        record = responded(TASK_REVIEW, claims=[claim(
            claimed_at='2026-11-07T11:31:00.500000Z')])
        result = run([{'id': 'task', 'task_id': TASK_REVIEW}], store=FakeStore(record),
                     github=github, d6=d6_reader(d6_dev=d6_result('d6-dev'),
                                                 d6_rev=d6_root('d6-rev')),
                     processes=[process(9, 'd6-rev', at(11, 31), sessions=[SESSION])])
        self.assertUnknown(result, 'PROCESS_START_AMBIGUOUS')

    def test_identity_that_appears_between_reads_is_unknown(self):
        ours = process(101, 'd6-run', at(11, 30))
        for first in ([], [process(101, 'd6-run', at(11, 29))]):
            result = self.check(dispatching(TASK_RUNNING), listing=reads(first, [ours]))
            self.assertUnknown(result, 'PROCESS_IDENTITY_UNSTABLE')

    def test_cwd_change_between_reads_is_unknown(self):
        ours = process(101, 'd6-run', at(11, 30))
        moved = dict(ours, cwd=f'{ROOT}/d6-other')
        result = self.check(dispatching(TASK_RUNNING), listing=reads(moved, ) if False else
                            reads([moved], [ours]))
        self.assertUnknown(result, 'PROCESS_IDENTITY_UNSTABLE')

    def test_process_gone_by_the_second_read_is_dead(self):
        ours = process(101, 'd6-run', at(11, 30))
        result = self.check(dispatching(TASK_RUNNING), listing=reads([ours], []))
        self.assertRow(result, 'task', board_check.ANOMALY, 'dead Task')

    def test_process_without_identity_fields_is_an_invalid_list(self):
        item = process(101, 'd6-run', at(11, 30))
        del item['start_time']
        self.assertUnknown(self.check(dispatching(TASK_RUNNING), [item]),
                           'PROCESS_LIST_INVALID')


class DispatchAuditBindingTests(BoardCheckCase):
    """F4: only an audit entry for the current assignment is launch evidence."""

    def check(self, record):
        return run([{'id': 'task', 'task_id': TASK_RUNNING}], store=FakeStore(record),
                   processes=[process(101, 'd6-run', at(11, 30))],
                   d6=d6_reader(d6_run=d6_root('d6-run')))

    def assertNoLaunchEvidence(self, record):
        self.assertRow(self.check(record), 'task', board_check.ANOMALY,
                       'no ALLOWED dispatch audit')

    def test_legacy_entry_without_assignment_is_not_launch_evidence(self):
        record = dispatching(TASK_RUNNING)
        for field in ('assignment_id', 'source_sha', 'contract_digest'):
            del record['dispatch_audit'][0][field]
        self.assertNoLaunchEvidence(record)

    def test_entry_for_another_assignment_is_not_launch_evidence(self):
        record = dispatching(TASK_RUNNING)
        record['dispatch_audit'][0]['assignment_id'] = 'e' * 32
        self.assertNoLaunchEvidence(record)

    def test_empty_current_assignment_matches_nothing(self):
        for value in ('', None):
            record = dispatching(TASK_RUNNING)
            record['developer']['assignment_id'] = value
            record['dispatch_audit'][0]['assignment_id'] = value
            self.assertNoLaunchEvidence(record)

    def test_bound_entry_is_used_beside_a_legacy_entry(self):
        record = dispatching(TASK_RUNNING)
        legacy = dict(record['dispatch_audit'][0], attempt_id='9' * 32,
                      at='2026-11-07T11:59:00Z')
        for field in ('assignment_id', 'source_sha', 'contract_digest'):
            del legacy[field]
        record['dispatch_audit'].append(legacy)
        self.assertRow(self.check(record), 'task', board_check.RUNNING, '11:00:00Z')


class RepairLivenessTests(BoardCheckCase):
    """F5: a repair is live only by its own child Task, never by the parent's D6."""

    def check(self, child, processes=(), *, parent=None, d6=None):
        github = FakeGitHub({'pulls/41': pull(), OPEN_PRS: [{'number': 41}],
                             REVIEW_COMMENTS: [], CHECKS: checks('completed')})
        return run([{'id': 'task', 'task_id': TASK_REVIEW}],
                   store=FakeStore(parent or repairing(TASK_REVIEW), child), github=github,
                   processes=processes, d6=d6 or d6_reader(d6_dev=d6_result('d6-dev')))

    def test_child_attempt_proven_in_the_claimed_d6_is_running(self):
        result = self.check(repair_child(), [process(301, 'd6-dev', at(11, 50))])
        self.assertRow(result, 'task', board_check.RUNNING, f'repair Task {TASK_CHILD}')
        self.assertEqual(result['exit_code'], 0)

    def test_pending_child_is_not_running(self):
        result = self.check(pending(TASK_CHILD), [process(301, 'd6-dev', at(11, 50))])
        self.assertRow(result, 'task', board_check.ACTIONABLE, 'never dispatched')
        self.assertEqual(result['exit_code'], 2)

    def test_abandoned_child_is_not_running(self):
        for child in (repair_child(), pending(TASK_CHILD)):
            child['abandonment'] = {'reason': 'superseded'}
            result = self.check(child, [process(301, 'd6-dev', at(11, 50))])
            self.assertRow(result, 'task', board_check.ANOMALY, 'ABANDONED')

    def test_child_of_another_executor_is_not_running(self):
        result = self.check(repair_child(d6_task='d6-other'),
                            [process(301, 'd6-dev', at(11, 50))])
        self.assertRow(result, 'task', board_check.ANOMALY, 'does not match the repair claim')

    def test_child_without_executor_is_unverifiable(self):
        child = repair_child()
        del child['developer']['executor_task_id']
        result = self.check(child, [process(301, 'd6-dev', at(11, 50))])
        self.assertRow(result, 'task', board_check.UNKNOWN, 'REPAIR_EXECUTOR_UNVERIFIABLE')

    def test_child_without_dispatch_evidence_is_not_running(self):
        child = repair_child()
        child['dispatch_audit'] = []
        result = self.check(child, [process(301, 'd6-dev', at(11, 50))])
        self.assertRow(result, 'task', board_check.ANOMALY, 'no ALLOWED dispatch audit')

    def test_child_process_gone_is_a_dead_repair(self):
        result = self.check(repair_child(), [process(202, 'd6-dev', at(11, 50))])
        self.assertRow(result, 'task', board_check.ANOMALY, 'dead repair')

    def test_child_process_before_its_dispatch_is_a_dead_repair(self):
        child = dispatching(TASK_CHILD, 'd6-dev', audits=('2026-11-07T11:45:00Z',),
                            execution=launch(301, at(11, 44)))
        result = self.check(child, [process(301, 'd6-dev', at(11, 44))])
        self.assertRow(result, 'task', board_check.ANOMALY, 'dead repair')

    def test_failed_d6_read_is_unknown(self):
        result = self.check(repair_child(), [process(301, 'd6-dev', at(11, 50))],
                            d6=lambda record, d6_task: (
                                d6_result('d6-dev') if record['task_id'] == TASK_CHILD else
                                {'state': 'D6_BINDING_UNAVAILABLE'}))
        self.assertRow(result, 'task', board_check.UNKNOWN)


class ReviewLivenessTests(BoardCheckCase):
    """F6: only an active dispatched review with matching identity is WAITING_REVIEW."""

    LIVE = (process(9, 'd6-rev', at(11, 31), sessions=[SESSION]),)

    def check(self, record, processes=LIVE):
        github = FakeGitHub({'pulls/41': pull(), OPEN_PRS: [{'number': 41}],
                             CHECKS: checks('completed')})
        return run([{'id': 'task', 'task_id': TASK_REVIEW}], store=FakeStore(record),
                   github=github, processes=processes,
                   d6=d6_reader(d6_dev=d6_result('d6-dev'), d6_rev=d6_root('d6-rev')))

    def test_completed_claim_is_a_result_awaiting_ingestion(self):
        result = self.check(responded(TASK_REVIEW, claims=[claim(state='COMPLETED')]))
        self.assertRow(result, 'task', board_check.ACTIONABLE, 'awaiting ingestion')
        self.assertEqual(result['exit_code'], 2)

    def test_reviewer_in_another_session_is_not_waiting_review(self):
        result = self.check(responded(TASK_REVIEW, claims=[claim(session=OTHER_SESSION)]))
        self.assertRow(result, 'task', board_check.UNKNOWN, 'PROCESS_IDENTITY_UNPROVEN')

    def test_claim_for_another_executor_is_not_waiting_review(self):
        result = self.check(responded(TASK_REVIEW, claims=[claim(executor='d6-other')]))
        self.assertRow(result, 'task', board_check.ACTIONABLE, 'RECORD_REVIEW_RESULT')


class TerminalPrTests(BoardCheckCase):
    """F7: a terminal PR outranks any attempt, review or repair still recorded as live.

    It is read first, so no D6 or process evidence can change or hide the result.
    """

    def setUp(self):
        self.d6 = d6_reader(d6_dev=d6_result('d6-dev'), d6_rev=d6_root('d6-rev'))
        self.reviewer = process(9, 'd6-rev', at(11, 31), sessions=[SESSION])
        self.repairer = process(301, 'd6-dev', at(11, 50))

    def check(self, terminal_pull, *records, processes):
        return run([{'id': 'task', 'task_id': TASK_REVIEW}], store=FakeStore(*records),
                   github=FakeGitHub({'pulls/41': terminal_pull}), d6=self.d6,
                   processes=processes)

    def check_unread(self, terminal_pull, record, row=None):
        """Classify with D6 and process reads that fail the test if they are made."""
        read_d6 = mock.Mock(side_effect=AssertionError('D6 read before the terminal PR'))
        listing = mock.Mock(side_effect=AssertionError('process read before the terminal PR'))
        result = run([dict(row or {}, id='task', task_id=record['task_id'])],
                     store=FakeStore(record), github=FakeGitHub({'pulls/41': terminal_pull}),
                     d6=read_d6, listing=listing)
        read_d6.assert_not_called()
        listing.assert_not_called()
        return result

    def live_task(self, build=dispatching):
        record = build(TASK_RUNNING)
        record['published_pr'] = {'pr': 41, 'head_sha': HEAD,
                                  'recorded_at': '2026-11-07T10:00:00Z'}
        return record

    def test_closed_unmerged_pr_is_anomaly_for_a_dispatching_task(self):
        result = self.check_unread(pull(state='closed'), self.live_task())
        self.assertRow(result, 'task', board_check.ANOMALY, 'closed unmerged')
        self.assertEqual(result['exit_code'], 2)

    def test_merged_pr_without_a_marker_is_actionable_for_a_dispatching_task(self):
        result = self.check_unread(pull(state='closed', merged=True), self.live_task())
        self.assertRow(result, 'task', board_check.ACTIONABLE, 'no merge marker')
        self.assertEqual(result['exit_code'], 2)

    def test_terminal_pr_outranks_a_proven_live_attempt(self):
        for terminal, state, evidence in (
                (pull(state='closed'), board_check.ANOMALY, 'closed unmerged'),
                (pull(state='closed', merged=True), board_check.ACTIONABLE, 'no merge marker')):
            result = run([{'id': 'task', 'task_id': TASK_RUNNING}],
                         store=FakeStore(self.live_task()),
                         github=FakeGitHub({'pulls/41': terminal}),
                         processes=[process(101, 'd6-run', at(11, 30))],
                         d6=d6_reader(d6_run=d6_root('d6-run')))
            self.assertRow(result, 'task', state, evidence)

    def test_row_pr_is_enough_to_end_a_dispatching_task(self):
        result = self.check_unread(pull(state='closed'), dispatching(TASK_RUNNING), {'pr': 41})
        self.assertRow(result, 'task', board_check.ANOMALY, 'closed unmerged')

    def test_terminal_pr_ends_a_task_not_yet_dispatched(self):
        result = self.check_unread(pull(state='closed'), self.live_task(pending))
        self.assertRow(result, 'task', board_check.ANOMALY, 'closed unmerged')
        result = self.check_unread(pull(state='closed', merged=True), self.live_task(pending))
        self.assertRow(result, 'task', board_check.ACTIONABLE, 'no merge marker')

    def test_terminal_pr_is_read_before_the_d6_of_a_responded_task(self):
        result = self.check_unread(pull(state='closed'), responded(TASK_REVIEW))
        self.assertRow(result, 'task', board_check.ANOMALY, 'closed unmerged')
        result = self.check_unread(pull(state='closed', merged=True), responded(TASK_REVIEW))
        self.assertRow(result, 'task', board_check.ACTIONABLE, 'no merge marker')

    def test_failed_d6_or_process_read_does_not_hide_a_terminal_pr(self):
        def broken():
            raise OSError('lsof failed')
        result = run([{'id': 'task', 'task_id': TASK_RUNNING}],
                     store=FakeStore(self.live_task()), listing=broken, d6=d6_reader(),
                     github=FakeGitHub({'pulls/41': pull(state='closed')}))
        self.assertRow(result, 'task', board_check.ANOMALY, 'closed unmerged')

    def test_open_pr_leaves_a_live_attempt_running(self):
        result = run([{'id': 'task', 'task_id': TASK_RUNNING}],
                     store=FakeStore(self.live_task()),
                     github=FakeGitHub({'pulls/41': pull(), OPEN_PRS: [{'number': 41}]}),
                     processes=[process(101, 'd6-run', at(11, 30))],
                     d6=d6_reader(d6_run=d6_root('d6-run')))
        self.assertRow(result, 'task', board_check.RUNNING, 'process 101')
        self.assertEqual((result['result'], result['exit_code']), ('CLEAN', 0))

    def test_unreadable_pr_of_a_live_attempt_is_unknown_not_running(self):
        github = FakeGitHub({'pulls/41': RuntimeError('network down'),
                             OPEN_PRS: [{'number': 41}]})
        result = run([{'id': 'task', 'task_id': TASK_RUNNING}],
                     store=FakeStore(self.live_task()), github=github,
                     processes=[process(101, 'd6-run', at(11, 30))],
                     d6=d6_reader(d6_run=d6_root('d6-run')))
        self.assertRow(result, 'task', board_check.UNKNOWN, 'GITHUB_READ_FAILED')
        self.assertEqual((result['result'], result['exit_code']), ('UNKNOWN', 1))

    def test_merge_marker_stays_done_without_a_pr_read(self):
        record = responded(TASK_REVIEW)
        record['completion_events'] = [{'source': 'GITHUB', 'marker_type': 'merge',
                                        'marker_status': 'MERGED', 'issue_number': 41}]
        github = FakeGitHub()
        result = run([{'id': 'task', 'task_id': TASK_REVIEW}], store=FakeStore(record),
                     github=github, d6=self.d6)
        self.assertRow(result, 'task', board_check.DONE, 'merge marker')
        self.assertNotIn('pulls/41', github.calls)

    def test_closed_unmerged_pr_is_anomaly_under_a_live_reviewer(self):
        result = self.check(pull(state='closed'), responded(TASK_REVIEW, claims=[claim()]),
                            processes=[self.reviewer])
        self.assertRow(result, 'task', board_check.ANOMALY, 'closed unmerged')

    def test_closed_unmerged_pr_is_anomaly_under_a_live_repair(self):
        result = self.check(pull(state='closed'), repairing(TASK_REVIEW), repair_child(),
                            processes=[self.repairer])
        self.assertRow(result, 'task', board_check.ANOMALY, 'closed unmerged')

    def test_merged_pr_without_a_marker_needs_reconciliation(self):
        merged = pull(state='closed', merged=True)
        for records, live in (((responded(TASK_REVIEW, claims=[claim()]),), self.reviewer),
                              ((repairing(TASK_REVIEW), repair_child()), self.repairer)):
            result = self.check(merged, *records, processes=[live])
            self.assertRow(result, 'task', board_check.ACTIONABLE, 'no merge marker')
            self.assertEqual(result['exit_code'], 2)

    def test_a_held_predicate_does_not_hide_a_merged_pr(self):
        result = run([{'id': 'task', 'task_id': TASK_REVIEW,
                       'blocker': 'time:2026-11-08T00:00:00Z'}],
                     store=FakeStore(responded(TASK_REVIEW)), d6=self.d6,
                     github=FakeGitHub({'pulls/41': pull(state='closed', merged=True)}))
        self.assertRow(result, 'task', board_check.ACTIONABLE, 'no merge marker')


class CapacityTests(BoardCheckCase):
    """Owner test 7: busy or authorization-waiting Developers are not a PM fault."""

    def test_every_developer_busy_or_waiting_authorization_is_blocked(self):
        store = FakeStore(claude_dispatching(TASK_RUNNING), pending(TASK_PENDING),
                          assignments=[assignment(TASK_RUNNING)])
        evaluate = policy(opencode_developer='OWNER_PAUSED', agy='OWNER_ASSIGNMENT_REQUIRED')
        rows = [{'id': 'running', 'task_id': TASK_RUNNING},
                {'id': 'pending', 'task_id': TASK_PENDING},
                {'id': 'work'}]
        result = run(rows, store=store, evaluate=evaluate,
                     processes=[process(101, 'd6-run', at(11, 30), sessions=[SESSION])],
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


MERGED = [{'source': 'GITHUB', 'marker_type': 'merge', 'marker_status': 'MERGED',
           'issue_number': 41}]
REFUSED = board_check.CLAIM_REFUSED_EXIT


def ledger_claim(row, holder='sched-a', task=None, *, reserved_at='2026-11-07T11:00:00Z'):
    """One ledger claim as board-check writes it; ``task`` makes it a binding."""
    return {'claim_id': uuid.uuid4().hex, 'row': row, 'holder': holder,
            'reserved_at': reserved_at, 'task_id': task,
            'bound_at': reserved_at if task is not None else None}


def _record_validate(value):
    """Stand-in for the TaskStore record schema check (see LedgerCase)."""
    if type(value) is not dict or type(value.get('task_id')) is not str:
        raise AssertionError('not a Task record')
    return value


class LedgerCase(BoardCheckCase):
    """A real TaskStore, board file and ledger under a private temporary HOME.

    TaskStore keeps its real files, permissions, task listing and reads. Only its
    record schema check is replaced, because these minimal fixture records carry
    no Task contract. Board-check sees exactly what TaskStore returns. Every claim
    and check builds new TaskStore, board and ledger readers from the files.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # TaskStore refuses a symlinked path component, such as macOS /var.
        self.home = os.path.realpath(tmp.name)
        for patcher in (mock.patch.dict(os.environ, {'HOME': self.home}),
                        mock.patch('llmtool.task_store._validate', _record_validate)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.state = self.home
        for part in ('.local', 'state', 'llmtool'):
            self.state = os.path.join(self.state, part)
            os.mkdir(self.state, 0o700)
            os.chmod(self.state, 0o700)
        self.ledger_path = os.path.join(self.state, 'board-ledger.json')
        self.board_path = os.path.join(self.state, 'pm-board.json')
        self.write_board([{'id': 'work'}])

    def write_board(self, rows):
        with open(self.board_path, 'w', encoding='utf-8') as handle:
            json.dump({'schema_version': 1, 'rows': rows}, handle)

    def put(self, *records):
        for record in records:
            TaskStore()._write(copy.deepcopy(record), create=True)

    def replace(self, record):
        TaskStore()._write(copy.deepcopy(record), create=False)

    def write_ledger(self, claims=(), *, raw=None, mode=0o600):
        if raw is None:
            raw = json.dumps({'schema_version': 1, 'claims': list(claims)}).encode('ascii')
        fd = os.open(self.ledger_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw)
        os.chmod(self.ledger_path, mode)

    def ledger(self):
        return board_check.read_ledger(self.ledger_path)

    def ledger_bytes(self):
        try:
            with open(self.ledger_path, 'rb') as handle:
                return handle.read()
        except FileNotFoundError:
            return None

    def claim(self, row, holder, task=None, *, now=NOW, as_json=True):
        """One ``board-check --claim`` with default paths under the temporary HOME."""
        args = SimpleNamespace(claim=row, holder=holder, task=task, board=None, ledger=None,
                               json=as_json)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = board_check.run_board_claim(args, now=now)
        return code, json.loads(output.getvalue()) if as_json else output.getvalue()

    def assertClaim(self, outcome, result, code=None):
        exit_code, output = outcome
        self.assertEqual((exit_code, output['result'], output['code']),
                         (REFUSED if code else 0, result, code), output)
        return output

    def bind(self, row, holder, task_id, *, now=NOW):
        self.assertClaim(self.claim(row, holder, now=now), 'RESERVED')
        return self.assertClaim(self.claim(row, holder, task_id, now=now), 'BOUND')

    def check(self, *, now=NOW, processes=(), d6=None, evaluate=None, github=None):
        """One plain ``board-check --json`` reading the persisted board and ledger."""
        args = SimpleNamespace(board=None, ledger=None, json=True)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = board_check.run_board_check(
                args, github_get=github or FakeGitHub(),
                list_processes_call=lambda: [dict(item) for item in processes],
                read_d6=d6 or d6_reader(), evaluate=evaluate or policy(), now=now)
        result = json.loads(output.getvalue())
        self.assertEqual(code, result['exit_code'], result)
        return result


class ScheduleRunTests(LedgerCase):
    """Owner test 6: two schedule runs, each board-check then --claim, give one claim
    and one bound Task."""

    def test_two_schedule_runs_give_one_claim_and_one_bound_task(self):
        # Both runs read the board before either claims: the race the ledger closes.
        for _ in ('sched-a', 'sched-b'):
            self.assertRow(self.check(), 'work', board_check.ACTIONABLE, 'work not dispatched')
        self.assertClaim(self.claim('work', 'sched-a'), 'RESERVED')
        self.assertClaim(self.claim('work', 'sched-b'), 'REFUSED', 'ROW_ALREADY_CLAIMED')
        # Only the run that won the claim creates a Task and binds it.
        self.put(pending(TASK_PENDING))
        bound = self.assertClaim(self.claim('work', 'sched-a', TASK_PENDING, now=at(12, 1)),
                                 'BOUND')
        self.assertEqual(bound['claim']['task_id'], TASK_PENDING)

        # The next run of either schedule sees the bound Task and cannot claim again.
        result = self.check(now=at(12, 5))
        self.assertRow(result, 'work', board_check.ACTIONABLE,
                       f'ledger claim by sched-a binds Task {TASK_PENDING}')
        for holder in ('sched-b', 'sched-a'):
            self.assertClaim(self.claim('work', holder, now=at(12, 5)),
                             'REFUSED', 'ROW_ALREADY_CLAIMED')
        self.assertClaim(self.claim('work', 'sched-b', TASK_PENDING, now=at(12, 5)),
                         'REFUSED', 'ROW_ALREADY_CLAIMED')

        claims = self.ledger()
        self.assertEqual([(item['row'], item['holder'], item['task_id']) for item in claims],
                         [('work', 'sched-a', TASK_PENDING)])
        self.assertEqual(TaskStore().task_ids(), [TASK_PENDING])


class FreshSessionTests(LedgerCase):
    """Owner test 8: a fresh session reads the persisted ledger, refuses a duplicate
    claim and reports the bound Task's next step."""

    def test_fresh_session_refuses_a_duplicate_and_reports_the_next_step(self):
        self.put(pending(TASK_PENDING))
        self.bind('work', 'sched-a', TASK_PENDING)
        persisted = self.ledger_bytes()

        # A new session, past the reservation TTL: the bound Task keeps the claim live.
        from llmtool import cli
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.main(['board-check', '--claim', 'work', '--holder', 'sched-b', '--json'])
        refused = json.loads(output.getvalue())
        self.assertEqual((code, refused['result'], refused['code']),
                         (REFUSED, 'REFUSED', 'ROW_ALREADY_CLAIMED'))
        self.assertEqual(self.ledger_bytes(), persisted)

        result = self.check(now=at(14))
        self.assertRow(result, 'work', board_check.ACTIONABLE,
                       f'ledger claim by sched-a binds Task {TASK_PENDING}')
        self.assertRow(result, 'work', board_check.ACTIONABLE,
                       'Developer state DEVELOPER_PENDING; next step DEVELOPER_ATTENTION')
        self.assertEqual(result['exit_code'], 2)


class LedgerFileTests(LedgerCase):
    """The ledger is one private, bounded file replaced atomically under a lock."""

    def test_missing_ledger_is_empty_and_a_claim_creates_a_private_file(self):
        self.assertEqual(board_check.read_ledger(self.ledger_path), [])
        self.assertRow(self.check(), 'work', board_check.ACTIONABLE)
        output = self.assertClaim(self.claim('work', 'sched-a'), 'RESERVED')
        info = os.lstat(self.ledger_path)
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual((stat.S_IMODE(info.st_mode), info.st_nlink), (0o600, 1))
        with open(self.ledger_path, encoding='utf-8') as handle:
            self.assertEqual(json.load(handle)['schema_version'], 1)
        self.assertEqual(self.ledger(), [output['claim']])
        self.assertEqual((output['claim']['reserved_at'], output['claim']['task_id']),
                         ('2026-11-07T12:00:00Z', None))
        self.assertEqual([name for name in os.listdir(self.state) if name.endswith('.tmp')], [])

    def test_claim_waits_for_the_exclusive_lock(self):
        with board_check._ledger_lock(self.ledger_path), \
                mock.patch.object(board_check, 'LEDGER_LOCK_TIMEOUT_SECONDS', 0.2):
            self.assertClaim(self.claim('work', 'sched-a'), 'REFUSED', 'LEDGER_BUSY')
        self.assertIsNone(self.ledger_bytes())
        self.assertClaim(self.claim('work', 'sched-a'), 'RESERVED')

    def test_full_ledger_refuses_a_new_claim(self):
        self.write_ledger([ledger_claim('old', reserved_at='2026-11-07T10:00:00Z')
                           for _ in range(board_check.MAX_LEDGER_CLAIMS)])
        before = self.ledger_bytes()
        self.assertClaim(self.claim('work', 'sched-a'), 'REFUSED', 'LEDGER_FULL')
        self.assertEqual(self.ledger_bytes(), before)

    def test_cli_claim_arguments_are_wired(self):
        from llmtool import cli
        args = cli.parser_for_cli().parse_args(
            ['board-check', '--claim', 'work', '--holder', 'sched-a', '--task', TASK_PENDING,
             '--ledger', self.ledger_path])
        self.assertEqual((args.claim, args.holder, args.task, args.ledger),
                         ('work', 'sched-a', TASK_PENDING, self.ledger_path))
        self.assertTrue(board_check.claim_requested(args))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.main(['board-check', '--claim', 'work', '--holder', 'sched-a'])
        self.assertEqual(code, 0)
        self.assertIn('board-check claim: RESERVED row work holder sched-a', output.getvalue())
        self.assertEqual([item['holder'] for item in self.ledger()], ['sched-a'])


class ConcurrentClaimTests(LedgerCase):
    """Two concurrent claim processes give one winner."""

    SCRIPT = ('import sys; sys.path.insert(0, sys.argv[1]); from llmtool import cli; '
              'sys.exit(cli.main(sys.argv[2:]))')

    def test_two_claim_processes_have_one_winner(self):
        env = dict(os.environ, HOME=self.home)
        processes = []
        # Hold the lock while both start, so both wait on it and then race for it.
        with board_check._ledger_lock(self.ledger_path):
            for holder in ('sched-a', 'sched-b'):
                child = subprocess.Popen(
                    [sys.executable, '-c', self.SCRIPT, LLMTOOL_ROOT, 'board-check',
                     '--claim', 'work', '--holder', holder, '--json'],
                    env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE)
                self.addCleanup(lambda item=child: item.poll() is None and item.kill())
                processes.append(child)
            time.sleep(1.0)
        outcomes = []
        for child in processes:
            stdout, stderr = child.communicate(timeout=120)
            self.assertIn(child.returncode, (0, REFUSED), stderr)
            outcomes.append((child.returncode, json.loads(stdout)))
        self.assertEqual(sorted(code for code, _ in outcomes), [0, REFUSED], outcomes)
        winner = next(output for code, output in outcomes if code == 0)
        loser = next(output for code, output in outcomes if code == REFUSED)
        self.assertEqual((winner['result'], loser['code']), ('RESERVED', 'ROW_ALREADY_CLAIMED'))
        self.assertEqual([item['holder'] for item in self.ledger()], [winner['holder']])


class ClaimRuleTests(LedgerCase):
    """--claim and --task refusals; a claim never dispatches, creates a Task or writes GitHub."""

    def test_live_claim_is_refused_whoever_asks(self):
        self.assertClaim(self.claim('work', 'sched-a'), 'RESERVED')
        before = self.ledger_bytes()
        for holder in ('sched-a', 'sched-b'):
            self.assertClaim(self.claim('work', holder, now=at(12, 14, 59)),
                             'REFUSED', 'ROW_ALREADY_CLAIMED')
        self.assertEqual(self.ledger_bytes(), before)

    def test_text_refusal_names_its_code_and_exits_nonzero(self):
        self.claim('work', 'sched-a')
        code, text = self.claim('work', 'sched-b', as_json=False)
        self.assertEqual(code, REFUSED)
        self.assertIn('board-check claim: REFUSED ROW_ALREADY_CLAIMED row work holder sched-b',
                      text)

    def test_claim_never_dispatches_creates_a_task_or_writes_github(self):
        self.put(pending(TASK_PENDING))
        before = TaskStore().get(TASK_PENDING)
        with mock.patch('subprocess.Popen', side_effect=AssertionError('claim ran a process')), \
                mock.patch.object(task_advance, '_publish_run_gh',
                                  side_effect=AssertionError('claim called GitHub')):
            self.bind('work', 'sched-a', TASK_PENDING)
        self.assertEqual(TaskStore().task_ids(), [TASK_PENDING])
        self.assertEqual(TaskStore().get(TASK_PENDING), before)

    def test_row_not_on_the_board_is_refused(self):
        self.put(pending(TASK_PENDING))
        for task in (None, TASK_PENDING):
            self.assertClaim(self.claim('ghost', 'sched-a', task), 'REFUSED', 'ROW_NOT_ON_BOARD')
        self.assertIsNone(self.ledger_bytes())

    def test_task_not_in_taskstore_is_refused(self):
        self.assertClaim(self.claim('work', 'sched-a'), 'RESERVED')
        for task in (TASK_UNKNOWN, 'TASK-763'):
            self.assertClaim(self.claim('work', 'sched-a', task), 'REFUSED', 'TASK_NOT_FOUND')
        self.assertEqual([item['task_id'] for item in self.ledger()], [None])

    def test_holder_other_than_the_reserver_is_refused(self):
        self.put(pending(TASK_PENDING))
        self.assertClaim(self.claim('work', 'sched-a'), 'RESERVED')
        self.assertClaim(self.claim('work', 'sched-b', TASK_PENDING), 'REFUSED', 'HOLDER_MISMATCH')
        self.assertEqual([item['task_id'] for item in self.ledger()], [None])

    def test_task_bound_to_another_live_claim_is_refused(self):
        self.write_board([{'id': 'work'}, {'id': 'other'}])
        self.put(pending(TASK_PENDING))
        self.bind('work', 'sched-a', TASK_PENDING)
        self.assertClaim(self.claim('other', 'sched-a'), 'RESERVED')
        self.assertClaim(self.claim('other', 'sched-a', TASK_PENDING),
                         'REFUSED', 'TASK_ALREADY_BOUND')

    def test_binding_needs_a_live_reservation(self):
        self.put(pending(TASK_PENDING))
        self.assertClaim(self.claim('work', 'sched-a', TASK_PENDING),
                         'REFUSED', 'RESERVATION_REQUIRED')
        self.assertClaim(self.claim('work', 'sched-a'), 'RESERVED')
        self.assertClaim(self.claim('work', 'sched-a', TASK_PENDING, now=at(12, 15)),
                         'REFUSED', 'RESERVATION_EXPIRED')

    def test_terminal_task_cannot_be_bound(self):
        merged = responded(TASK_REVIEW)
        merged['completion_events'] = MERGED
        abandoned = dict(pending(TASK_PENDING), abandonment={'reason': 'superseded'})
        self.put(merged, abandoned)
        self.assertClaim(self.claim('work', 'sched-a'), 'RESERVED')
        for task in (TASK_REVIEW, TASK_PENDING):
            self.assertClaim(self.claim('work', 'sched-a', task), 'REFUSED', 'TASK_NOT_LIVE')

    def test_invalid_claim_arguments_are_refused(self):
        self.assertClaim(self.claim('work', None), 'REFUSED', 'CLAIM_ARGUMENTS_INVALID')
        self.assertClaim(self.claim('work', 'bad holder'), 'REFUSED', 'HOLDER_INVALID')
        self.assertIsNone(self.ledger_bytes())


class ClaimLivenessTests(LedgerCase):
    """Only a fresh reservation or a live bound Task makes a claim live."""

    def test_reservation_is_claimed_until_it_expires(self):
        self.assertClaim(self.claim('work', 'sched-a'), 'RESERVED')
        result = self.check(now=at(12, 14, 59))
        self.assertRow(result, 'work', board_check.CLAIMED,
                       'ledger reservation by sched-a at 2026-11-07T12:00:00Z; no Task bound')
        self.assertEqual((result['result'], result['exit_code']), ('CLEAN', 0))

        result = self.check(now=at(12, 15))
        self.assertRow(result, 'work', board_check.ANOMALY, 'expired with no Task bound')
        self.assertEqual(result['exit_code'], 2)
        # The row can be claimed again; the expired claim stays as history.
        self.assertClaim(self.claim('work', 'sched-b', now=at(12, 15)), 'RESERVED')
        self.assertEqual([(item['holder'], item['task_id']) for item in self.ledger()],
                         [('sched-a', None), ('sched-b', None)])
        self.assertRow(self.check(now=at(12, 16)), 'work', board_check.CLAIMED, 'sched-b')

    def test_abandoned_bound_task_is_a_dead_claim(self):
        record = pending(TASK_PENDING)
        self.put(record)
        self.bind('work', 'sched-a', TASK_PENDING)
        self.replace(dict(record, abandonment={'reason': 'superseded'}))
        result = self.check()
        self.assertRow(result, 'work', board_check.ANOMALY,
                       f'binds Task {TASK_PENDING}, which is ABANDONED: dead claim')
        self.assertClaim(self.claim('work', 'sched-b'), 'RESERVED')
        self.assertEqual([item['task_id'] for item in self.ledger()], [TASK_PENDING, None])

    def test_fake_bound_task_is_never_live(self):
        self.write_ledger([ledger_claim('work', task=TASK_UNKNOWN)])
        result = self.check()
        self.assertRow(result, 'work', board_check.ANOMALY,
                       f'binds Task {TASK_UNKNOWN}, which is not found in TaskStore: dead claim')
        self.assertClaim(self.claim('work', 'sched-b'), 'RESERVED')
        self.assertEqual([item['task_id'] for item in self.ledger()], [TASK_UNKNOWN, None])

    def test_merged_bound_task_makes_the_row_done_and_frees_it(self):
        record = responded(TASK_REVIEW)
        record['completion_events'] = MERGED
        self.put(record)
        self.write_ledger([ledger_claim('work', task=TASK_REVIEW)])
        result = self.check()
        self.assertRow(result, 'work', board_check.DONE, 'merge marker')
        self.assertEqual(result['exit_code'], 0)
        self.assertClaim(self.claim('work', 'sched-b'), 'RESERVED')

    def test_board_task_id_that_differs_from_the_live_ledger_task_is_anomaly(self):
        self.write_board([{'id': 'work', 'task_id': TASK_OTHER}])
        self.put(pending(TASK_PENDING), pending(TASK_OTHER))
        self.bind('work', 'sched-a', TASK_PENDING)
        result = self.check()
        self.assertRow(result, 'work', board_check.ANOMALY,
                       f'board task_id {TASK_OTHER} differs from live ledger Task {TASK_PENDING}')

        self.write_board([{'id': 'work', 'task_id': TASK_PENDING}])
        self.assertRow(self.check(), 'work', board_check.ACTIONABLE, 'DEVELOPER_ATTENTION')

    def test_dead_ledger_task_leaves_the_board_task_id_in_charge(self):
        self.write_board([{'id': 'work', 'task_id': TASK_PENDING}])
        self.put(pending(TASK_PENDING))
        self.write_ledger([ledger_claim('work', task=TASK_UNKNOWN)])
        self.assertRow(self.check(), 'work', board_check.ACTIONABLE, 'DEVELOPER_ATTENTION')


class LedgerAuthorizationTests(LedgerCase):
    """A Task is RUNNING only when a live ledger claim binds it or its Developer
    worker is a worker-dispatch.json Worker."""

    def setUp(self):
        super().setUp()
        self.put(dispatching(TASK_RUNNING))

    def check_live(self, code):
        return self.check(processes=[process(101, 'd6-run', at(11, 30))],
                          d6=d6_reader(d6_run=d6_root('d6-run')),
                          evaluate=policy(opencode_developer=code))

    def test_worker_outside_the_policy_is_anomaly_not_running(self):
        self.write_board([{'id': 'task', 'task_id': TASK_RUNNING}])
        for code in ('WORKER_UNKNOWN', 'WORKER_KEY_INVALID'):
            result = self.check_live(code)
            self.assertRow(result, 'task', board_check.ANOMALY,
                           f'Task {TASK_RUNNING} Developer worker opencode-developer is not a '
                           'worker-dispatch.json Worker and no live ledger claim binds it')
            self.assertEqual(result['exit_code'], 2)
        # A policy Worker, even one paused for new work, keeps its live attempt RUNNING.
        for code in ('OWNER_POLICY_ALLOWED', 'OWNER_PAUSED'):
            self.assertRow(self.check_live(code), 'task', board_check.RUNNING, 'process 101')

    def test_live_ledger_claim_authorizes_a_running_task(self):
        self.write_board([{'id': 'task', 'task_id': TASK_RUNNING}])
        self.bind('task', 'sched-a', TASK_RUNNING)
        result = self.check_live('WORKER_UNKNOWN')
        self.assertRow(result, 'task', board_check.RUNNING, 'process 101')
        self.assertEqual((result['result'], result['exit_code']), ('CLEAN', 0))

    def test_row_without_task_id_runs_by_its_ledger_task(self):
        self.bind('work', 'sched-a', TASK_RUNNING)
        result = self.check_live('WORKER_UNKNOWN')
        self.assertRow(result, 'work', board_check.RUNNING,
                       f'ledger claim by sched-a binds Task {TASK_RUNNING}')
        self.assertRow(result, 'work', board_check.RUNNING, 'process 101')
        self.assertEqual((result['result'], result['exit_code']), ('CLEAN', 0))
        # The T1 Task rules still apply: without its process the bound Task is dead.
        result = self.check(d6=d6_reader(d6_run=d6_root('d6-run')))
        self.assertRow(result, 'work', board_check.ANOMALY, 'dead Task')

    def test_fake_or_stale_ledger_claim_does_not_authorize(self):
        self.write_board([{'id': 'task', 'task_id': TASK_RUNNING}])
        for claims in ([ledger_claim('task', task=TASK_UNKNOWN)],
                       [ledger_claim('task', reserved_at='2026-11-07T11:00:00Z')]):
            self.write_ledger(claims)
            result = self.check_live('WORKER_UNKNOWN')
            self.assertRow(result, 'task', board_check.ANOMALY,
                           'is not a worker-dispatch.json Worker')
            self.assertNotEqual(states(result)['task'], board_check.RUNNING)

    def test_abandoned_bound_task_is_never_running(self):
        self.bind('work', 'sched-a', TASK_RUNNING)
        record = TaskStore().get(TASK_RUNNING)
        del record['record_sha256']
        self.replace(dict(record, abandonment={'reason': 'superseded'}))
        result = self.check_live('OWNER_POLICY_ALLOWED')
        self.assertRow(result, 'work', board_check.ANOMALY, 'which is ABANDONED: dead claim')


class UnreadableLedgerTests(LedgerCase):
    """An unreadable or malformed ledger refuses every claim; affected rows are UNKNOWN."""

    def setUp(self):
        super().setUp()
        self.put(pending(TASK_PENDING))

    def assertUnreadable(self):
        before = self.ledger_bytes()
        self.assertClaim(self.claim('work', 'sched-a'), 'REFUSED', 'LEDGER_UNREADABLE')
        self.assertClaim(self.claim('work', 'sched-a', TASK_PENDING),
                         'REFUSED', 'LEDGER_UNREADABLE')
        self.assertEqual(self.ledger_bytes(), before)
        result = self.check()
        self.assertRow(result, 'work', board_check.UNKNOWN, 'LEDGER_UNREADABLE')
        self.assertEqual((result['result'], result['exit_code']), ('UNKNOWN', 1))

    def test_malformed_ledger(self):
        bad_holder = dict(ledger_claim('work'), holder='bad holder')
        for raw in (b'{bad json', b'[]', b'{"schema_version": 2, "claims": []}',
                    b'{"schema_version": true, "claims": []}',
                    b'{"schema_version": 1, "claims": [], "extra": 1}',
                    b'{"schema_version": 1, "claims": [], "claims": []}',
                    json.dumps({'schema_version': 1, 'claims': [bad_holder]}).encode('ascii')):
            with self.subTest(raw=raw):
                self.write_ledger(raw=raw)
                self.assertUnreadable()

    def test_ledger_that_is_not_private_or_bounded(self):
        self.write_ledger(mode=0o644)
        self.assertUnreadable()
        self.write_ledger(raw=b'{"schema_version": 1, "claims": []}' +
                          b' ' * board_check.MAX_LEDGER_BYTES)
        self.assertUnreadable()

    def test_hard_linked_ledger(self):
        self.write_ledger()
        os.link(self.ledger_path, os.path.join(self.home, 'ledger-link'))
        self.assertUnreadable()

    def test_symlinked_ledger_is_unreadable_and_never_replaced(self):
        target = os.path.join(self.home, 'real-ledger.json')
        fd = os.open(target, os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(b'{"schema_version": 1, "claims": []}')
        os.symlink(target, self.ledger_path)
        self.assertUnreadable()
        self.assertTrue(os.path.islink(self.ledger_path))

    def test_unreadable_ledger_does_not_hide_a_proven_anomaly(self):
        self.write_board([{'id': 'work'}, {'id': 'held', 'blocker': 'see chat'}])
        self.write_ledger(raw=b'{bad json')
        result = self.check()
        self.assertEqual(states(result), {'work': board_check.UNKNOWN,
                                          'held': board_check.ANOMALY}, result)
        self.assertEqual(result['exit_code'], 2)


class NextFieldTests(BoardCheckCase):
    """Board rows stay schema_version 1 with an optional ``next`` list of row ids."""

    def test_next_list_is_accepted_and_anything_else_is_anomaly(self):
        result = run([{'id': 'a', 'next': ['b']}, {'id': 'b', 'next': []}])
        self.assertEqual(states(result), {'a': board_check.ACTIONABLE,
                                          'b': board_check.ACTIONABLE}, result)
        for value in ('b', [1], [''], ['x'] * 33):
            result = run([{'id': 'a', 'next': value}])
            self.assertRow(result, 'a', board_check.ANOMALY, 'next is not a list of row ids')

    def test_next_items_come_only_from_done_rows_once_each(self):
        github = FakeGitHub({'pulls/12': pull(12, state='closed', merged=True),
                             'pulls/14': pull(14, state='closed', merged=True)})
        rows = [{'id': 'a', 'pr': 12, 'next': ['x']},
                {'id': 'b', 'pr': 14, 'next': ['x', 'a']},
                {'id': 'c', 'next': ['y']}]
        result = run(rows, github=github)
        # 'a' is on the board, so b's next does not add it; c is not DONE, so 'y' is not pending.
        self.assertEqual(states(result), {'a': board_check.DONE, 'b': board_check.DONE,
                                          'c': board_check.ACTIONABLE,
                                          'next:x': board_check.ACTIONABLE}, result)
        self.assertRow(result, 'next:x', board_check.ACTIONABLE,
                       'next item of DONE row a has no board row: pending authorized work')


def idle_rows(result):
    return [item['row'] for item in result['anomalies']
            if item['code'] == board_check.IDLE_RUNNABLE]


class NextItemTests(LedgerCase):
    """Owner test 3: a merged PR row with a next item lists it as pending, not DONE."""

    def test_merged_pr_row_lists_its_next_item_as_pending_not_done(self):
        self.write_board([{'id': 'impl', 'pr': 12, 'next': ['docs', 'qa']},
                          {'id': 'qa', 'after': 'impl'}])
        github = FakeGitHub({'pulls/12': pull(12, state='closed', merged=True)})
        result = self.check(github=github)
        self.assertEqual(states(result), {'impl': board_check.DONE,
                                          'qa': board_check.ACTIONABLE,
                                          'next:docs': board_check.ACTIONABLE}, result)
        self.assertRow(result, 'impl', board_check.DONE, 'PR #12 merged')
        self.assertRow(result, 'next:docs', board_check.ACTIONABLE,
                       'next item of DONE row impl has no board row: pending authorized work')
        # The listed row is classified on its own evidence: undispatched work.
        self.assertRow(result, 'qa', board_check.ACTIONABLE, 'work not dispatched')
        self.assertEqual(idle_rows(result), ['qa', 'next:docs'])
        self.assertEqual((result['result'], result['exit_code']), ('FLAGGED', 2))

    def test_listed_next_row_keeps_its_own_state(self):
        merged = responded(TASK_REVIEW)
        merged['completion_events'] = MERGED
        self.put(merged)
        self.write_board([{'id': 'impl', 'next': ['qa', 'docs']}, {'id': 'qa'},
                          {'id': 'docs', 'pr': 13}])
        self.write_ledger([ledger_claim('impl', task=TASK_REVIEW)])
        self.assertClaim(self.claim('qa', 'sched-a'), 'RESERVED')
        github = FakeGitHub({'pulls/13': pull(13), OPEN_PRS: [{'number': 13}],
                             CHECKS: checks('in_progress')})
        result = self.check(github=github)
        self.assertEqual(states(result), {'impl': board_check.DONE,
                                          'qa': board_check.CLAIMED,
                                          'docs': board_check.WAITING_CI}, result)
        self.assertRow(result, 'impl', board_check.DONE, 'merge marker')
        self.assertEqual(result['anomalies'], [])
        self.assertEqual((result['result'], result['exit_code']), ('CLEAN', 0))

    def test_malformed_next_on_a_merged_row_is_anomaly(self):
        github = FakeGitHub({'pulls/12': pull(12, state='closed', merged=True)})
        for value in ('docs', [7], ['x' * 129], {'docs': 1}):
            with self.subTest(value=value):
                self.write_board([{'id': 'impl', 'pr': 12, 'next': value}])
                result = self.check(github=github)
                self.assertEqual(states(result), {'impl': board_check.ANOMALY}, result)
                self.assertRow(result, 'impl', board_check.ANOMALY,
                               'next is not a list of row ids')
                self.assertEqual(result['exit_code'], 2)

    def test_unreadable_ledger_leaves_a_next_item_unknown(self):
        self.write_board([{'id': 'work'}, {'pr': 12, 'next': ['docs']}])
        self.write_ledger(raw=b'{bad json')
        github = FakeGitHub({'pulls/12': pull(12, state='closed', merged=True)})
        result = self.check(github=github)
        self.assertEqual(states(result), {'work': board_check.UNKNOWN,
                                          'row[1]': board_check.DONE,
                                          'next:docs': board_check.UNKNOWN}, result)
        self.assertRow(result, 'next:docs', board_check.UNKNOWN, 'LEDGER_UNREADABLE')
        self.assertEqual(result['anomalies'], [])
        self.assertEqual((result['result'], result['exit_code']), ('UNKNOWN', 1))


class IdleRunnableTests(LedgerCase):
    """Every ACTIONABLE row without a live ledger claim is IDLE_RUNNABLE; exit stays 2."""

    def test_actionable_rows_without_a_claim_are_idle_runnable(self):
        self.write_board([{'id': 'work'}, {'id': 'task', 'task_id': TASK_PENDING}])
        self.put(pending(TASK_PENDING))
        result = self.check()
        self.assertEqual(states(result), {'work': board_check.ACTIONABLE,
                                          'task': board_check.ACTIONABLE}, result)
        self.assertEqual(result['anomalies'], [
            {'code': 'IDLE_RUNNABLE', 'row': 'work',
             'evidence': ['ACTIONABLE with no live ledger claim']},
            {'code': 'IDLE_RUNNABLE', 'row': 'task',
             'evidence': ['ACTIONABLE with no live ledger claim']}])
        self.assertEqual((result['result'], result['exit_code']), ('FLAGGED', 2))

    def test_row_with_a_live_claim_is_never_idle_runnable(self):
        self.write_board([{'id': 'work'}, {'id': 'task', 'task_id': TASK_PENDING}])
        self.put(pending(TASK_PENDING), pending(TASK_OTHER))
        self.bind('work', 'sched-a', TASK_OTHER)
        self.bind('task', 'sched-b', TASK_PENDING)
        result = self.check()
        self.assertEqual(states(result), {'work': board_check.ACTIONABLE,
                                          'task': board_check.ACTIONABLE}, result)
        self.assertEqual(result['anomalies'], [])
        self.assertEqual(result['exit_code'], 2)

    def test_reservation_is_not_idle_and_its_expiry_is_an_anomaly_row(self):
        self.assertClaim(self.claim('work', 'sched-a'), 'RESERVED')
        result = self.check(now=at(12, 5))
        self.assertRow(result, 'work', board_check.CLAIMED)
        self.assertEqual((result['anomalies'], result['exit_code']), ([], 0))
        result = self.check(now=at(12, 15))
        self.assertRow(result, 'work', board_check.ANOMALY, 'expired')
        self.assertEqual((result['anomalies'], result['exit_code']), ([], 2))

    def test_fake_or_stale_claim_leaves_an_actionable_row_idle(self):
        self.write_board([{'id': 'work', 'task_id': TASK_PENDING}])
        self.put(pending(TASK_PENDING))
        for claims in ([ledger_claim('work', task=TASK_UNKNOWN)],
                       [ledger_claim('work', reserved_at='2026-11-07T11:00:00Z')]):
            with self.subTest(claims=claims):
                self.write_ledger(claims)
                result = self.check()
                self.assertRow(result, 'work', board_check.ACTIONABLE, 'DEVELOPER_ATTENTION')
                self.assertEqual(idle_rows(result), ['work'])
                self.assertEqual(result['exit_code'], 2)

    def test_text_output_names_idle_runnable(self):
        text = board_check.format_result(run([{'id': 'work'}]))
        self.assertIn('anomaly IDLE_RUNNABLE work ACTIONABLE with no live ledger claim', text)


if __name__ == '__main__':
    unittest.main()
