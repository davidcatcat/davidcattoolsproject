"""Tests for ``llmtool board-check`` (#763), using fakes for every evidence read.

Owner tests covered: (1) actionable work with no dispatch is flagged; (2) a Task
done with no Reviewer is flagged; (4) changing only row text or time does not
change a state; (5) a fake or stale Task ID is not RUNNING; (7) every Developer
busy or waiting a legal authorization is not a PM fault. High Debug liveness
defects F1-F9 (PR 1113) have permanent regressions, named in their class docstrings.
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
TASK_CHILD = '5' * 32
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

    def test_bad_or_endless_open_pr_list_is_unknown(self):
        # #763: get_pages refuses a non-list page and a list that never ends.
        result = run([], github=FakeGitHub({OPEN_PRS: {'number': 77}}))
        self.assertRow(result, 'github:open-prs', board_check.UNKNOWN, 'GITHUB_READ_INVALID')
        full = [{'number': number} for number in range(1, 101)]
        pages = {f'pulls?state=open&per_page=100&page={page}': full
                 for page in range(1, board_check.MAX_GITHUB_PAGES + 1)}
        result = run([], github=FakeGitHub(pages))
        self.assertRow(result, 'github:open-prs', board_check.UNKNOWN, 'GITHUB_LIST_TRUNCATED')
        self.assertEqual((result['result'], result['exit_code']), ('UNKNOWN', 1))

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

    def test_unreadable_owner_marker_or_task_record_is_unknown(self):
        # #763: an unreadable Owner-marker issue or Task record stays UNKNOWN with its code.
        ref = 'https://github.com/o/r/issues/7#issuecomment-5'
        github = FakeGitHub({'issues/7': {'state': 'unknown'}})
        result = run([{'id': 'ask', 'owner_ref': ref}], github=github)
        self.assertRow(result, 'ask', board_check.UNKNOWN, 'OWNER_MARKER_UNAVAILABLE')
        store = FakeStore(pending(TASK_PENDING))
        store.get = lambda task_id: ['not', 'a', 'record']
        result = run([{'id': 'pending', 'task_id': TASK_PENDING}], store=store)
        self.assertRow(result, 'pending', board_check.UNKNOWN, 'TASK_RECORD_UNREADABLE')
        self.assertEqual((result['result'], result['exit_code']), ('UNKNOWN', 1))

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


class DispatchAuditWorkerIdTests(unittest.TestCase):
    """#1502 WP-E: a stored audit worker_key reads through stored_worker_id."""

    def test_legacy_and_new_gw_agent_entries_are_launch_evidence(self):
        for stored in ('company-gw-agent', 'gw-agent'):
            with self.subTest(worker_key=stored):
                record = dispatching(TASK_RUNNING, profile='gw-agent')
                record['dispatch_audit'][0]['worker_key'] = stored
                since = board_check._dispatched_at(record)
                self.assertIsNotNone(since)
                self.assertEqual(board_check._stamp(since[0]), '2026-11-07T11:00:00Z')

    def test_latest_entry_of_either_id_wins(self):
        record = dispatching(TASK_RUNNING, profile='gw-agent',
                             audits=('2026-11-07T11:00:00Z', '2026-11-07T11:20:00Z'))
        record['dispatch_audit'][1]['worker_key'] = 'company-gw-agent'
        self.assertEqual(board_check._stamp(board_check._dispatched_at(record)[0]),
                         '2026-11-07T11:20:00Z')

    def test_entries_of_another_worker_are_not_launch_evidence(self):
        for stored in ('company-gw', 'agy', 'opencode-developer', None):
            with self.subTest(worker_key=stored):
                record = dispatching(TASK_RUNNING, profile='gw-agent')
                record['dispatch_audit'][0]['worker_key'] = stored
                self.assertIsNone(board_check._dispatched_at(record))
        # The alias maps only the retired gw Agent id: it never widens another route.
        record = dispatching(TASK_RUNNING, profile='opencode')
        record['dispatch_audit'][0]['worker_key'] = 'company-gw-agent'
        self.assertIsNone(board_check._dispatched_at(record))


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
            with self.assertRaises(board_check._Unknown) as caught:
                board_check.github_reader(runner)('pulls/41')
        self.assertEqual(caught.exception.code, 'GITHUB_READ_FAILED')

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
        with self.assertRaises(board_check._Unknown) as caught:
            board_check.list_processes(fake_run)
        self.assertEqual(caught.exception.code, 'PROCESS_LIST_FAILED')


if __name__ == '__main__':
    unittest.main()
