"""#2087 slice R1 piece 2: board check skips dispute-ruling claims (#2087).

A ``purpose: dispute_ruling`` claim is never the formal review claim shown at
``RECORD_REVIEW_RESULT``. A1-shaped formal and ruling claims are written through
the real TaskStore; only the board evidence reads are faked, and
``task_advance.decide`` is injected to return ``RECORD_REVIEW_RESULT``.
"""

import contextlib
import copy
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

from llmtool import board_check, task_advance, task_store  # noqa: E402
from test_dispute_store_schema import (  # noqa: E402
    ARBITER_REVIEWER,
    CLAUDE_UUID,
    CODEX_THREAD,
    EXECUTOR,
    FORMAL_REVIEWER,
    HEAD,
    OUTPUT_DIGEST,
    PM_SESSION,
    PR,
    REF,
    STAMP_1,
    STAMP_2,
    STAMP_3,
    TASK,
    _arbiter_claim,
    _base_record,
    _canonical_claim_sha256,
    _history_event,
    _ruling_result,
    _text_sha256,
)

OTHER_HEAD = 'e' * 40
ARBITER_SESSION = '00000000-0000-4000-8000-000000000002'
ROOT = '/nonexistent-board-check'
NOW = datetime(2026, 11, 23, 12, 0, 0, tzinfo=timezone.utc)


def _github(path):
    if path == f'pulls/{PR}':
        return {'number': PR, 'state': 'open', 'merged': False, 'head': {'sha': HEAD}}
    if path.startswith('pulls?state=open'):
        return [{'number': PR}]
    if path.startswith('commits/'):
        return {'total_count': 0, 'check_runs': []}
    raise AssertionError(f'unexpected GitHub read {path}')


def _d6(record, d6_task):
    return {'root': f'{ROOT}/{d6_task}', 'result_head': HEAD,
            'recorded_result_head': HEAD, 'dirty': False, 'state': 'CLEAN',
            'branch': 'feat/x', 'task_id': d6_task}


def _board_record(record):
    """Copy the stored record and add only the Developer evidence board check needs."""
    value = copy.deepcopy(record)
    value['state'] = 'PM_RESPONDED'
    value['developer'] = {
        'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'claude',
        'assignment_id': 'e' * 32, 'executor_task_id': EXECUTOR,
    }
    value['completion'] = {
        'state': 'WAITING_REVIEW', 'executor_task_id': EXECUTOR,
        'reviewer_task_id': None, 'evidence': {}, 'missing_gates': [],
        'pm_dispatch': 'NOT_STARTED', 'pm_response': None,
        'pm_recommendation': None, 'pm_usage': None, 'pm_error_code': None,
    }
    value['published_pr'] = {'pr': PR, 'head_sha': HEAD, 'recorded_at': STAMP_2}
    value['remote_test_receipts'] = []
    value['completion_events'] = []
    return value


class _BoardStore:
    """Read-only adapter over one real TaskStore record."""

    def __init__(self, record):
        self.record = record

    def task_ids(self):
        return [self.record['task_id']]

    def get(self, task_id):
        if task_id != self.record['task_id']:
            raise KeyError(task_id)
        return copy.deepcopy(self.record)


class BoardCheckSkipRulingTests(unittest.TestCase):
    def _new_store(self, mutate=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = task_store.TaskStore(Path(temp.name).resolve())
        record = _base_record(with_dispute=False)
        if mutate is not None:
            mutate(record)
        rec = store.create(record)
        entry = {
            'finding_id': 'F1', 'pr': PR, 'head_sha': HEAD, 'review_version': 1,
            'severity': 'High', 'state': 'OPEN', 'evidence_ref': REF,
            'opened_at': STAMP_1,
            'history': [_history_event('OPENED', at=STAMP_1,
                                       actor=f'pm:{PM_SESSION}')],
        }
        store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_disputes=[entry])
        return store

    def _claim_arbiter(self, store, *, reviewer='codex-reviewer', version=2):
        rec = store.get(TASK)
        claim = _arbiter_claim(reviewer=reviewer, state='CLAIMED', version=version)
        entry = dict(rec['review_disputes'][0])
        entry['arbiter_claim'] = {'reviewer_task': ARBITER_REVIEWER, 'version': version}
        entry['history'] = list(entry.get('history', [])) + [
            _history_event('ARBITER_CLAIMED', refs={
                'reviewer_task': ARBITER_REVIEWER, 'version': version}),
        ]
        return store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_dispatch_claim=rec['review_dispatch_claim'] + [claim],
            review_disputes=[entry])

    def _dispatch_formal(self, store, version):
        rec = store.get(TASK)
        claimed = {
            'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': version,
            'state': 'CLAIMED', 'reviewer_task': FORMAL_REVIEWER,
            'native_session_id': None, 'account_mode': 'ROLES',
            'claimed_at': STAMP_2, 'updated_at': STAMP_2,
        }
        rec = store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_dispatch_claim=rec['review_dispatch_claim'] + [claimed])
        dispatched = dict(claimed, state='DISPATCHED',
                          native_session_id=CLAUDE_UUID, updated_at=STAMP_2)
        return store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_dispatch_claim=rec['review_dispatch_claim'][:-1] + [dispatched])

    def _complete_arbiter(self, store):
        rec = store.get(TASK)
        result = _ruling_result(rec['task_binding']['contract_digest'],
                                source_kind='CODEX_JSONL')
        completed = dict(rec['review_dispatch_claim'][-1], state='COMPLETED',
                         native_session_id=CODEX_THREAD, updated_at=STAMP_3,
                         review_result=result)
        entry = dict(rec['review_disputes'][0])
        entry['history'] = list(entry['history']) + [
            _history_event('ARBITER_COMPLETED', at=STAMP_3, refs={
                'output_sha256': OUTPUT_DIGEST,
                'claim_sha256': _canonical_claim_sha256(completed),
                'review_text_sha256': _text_sha256(result['review_text']),
            }),
        ]
        return store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_dispatch_claim=rec['review_dispatch_claim'][:-1] + [completed],
            review_disputes=[entry])

    def _dispatch_arbiter(self, store):
        rec = store.get(TASK)
        version = rec['review_dispatch_claim'][-1]['version']
        dispatched = dict(rec['review_dispatch_claim'][-1], state='DISPATCHED',
                          native_session_id=ARBITER_SESSION, updated_at=STAMP_3)
        entry = dict(rec['review_disputes'][0])
        entry['history'] = list(entry['history']) + [
            _history_event('ARBITER_DISPATCHED', at=STAMP_3, refs={
                'reviewer_task': ARBITER_REVIEWER, 'version': version}),
        ]
        return store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_dispatch_claim=rec['review_dispatch_claim'][:-1] + [dispatched],
            review_disputes=[entry])

    def _run(self, store, *, live_pid=None, outcomes=None):
        record = _board_record(store.get(TASK))
        calls = []

        def live_process(_ctx, root, since, identity):
            calls.append({'root': root, 'since': since, 'identity': identity})
            return live_pid

        with contextlib.ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(board_check._Context, 'live_process', live_process))
            stack.enter_context(
                mock.patch.object(task_advance, 'decide',
                                  return_value='RECORD_REVIEW_RESULT'))
            stack.enter_context(
                mock.patch.object(task_advance, '_review_outcomes',
                                  outcomes or (lambda record, claims, get: [])))
            result = board_check.check_board(
                [{'id': 'task', 'task_id': TASK, 'pr': PR}],
                store=_BoardStore(record),
                github_get=_github,
                list_processes=lambda: [],
                read_d6=_d6,
                evaluate=lambda *args, **kwargs: SimpleNamespace(
                    allowed=True, reason_code='ALLOWED'),
                now=NOW,
            )
        return result, calls

    def _row(self, result):
        found = [item for item in result['rows'] if item['row'] == 'task']
        self.assertEqual(len(found), 1, result)
        return found[0]

    def test_completed_arbiter_never_names_the_newer_version(self):
        store = self._new_store()
        self._claim_arbiter(store)
        self._complete_arbiter(store)

        result, calls = self._run(store)

        row = self._row(result)
        self.assertEqual(row['state'], board_check.ACTIONABLE)
        self.assertEqual(row['evidence'], [
            'review claim v1 COMPLETED: result awaiting ingestion, next step '
            'RECORD_REVIEW_RESULT'])
        self.assertNotIn('v2', ' '.join(row['evidence']))
        self.assertEqual(calls, [])

    def test_dispatched_arbiter_is_not_the_live_review_process(self):
        store = self._new_store()
        self._dispatch_formal(store, 2)
        self._claim_arbiter(store, reviewer='claude-reviewer', version=3)
        self._dispatch_arbiter(store)

        result, calls = self._run(store)

        self.assertEqual(self._row(result)['state'], board_check.ACTIONABLE)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['root'], f'{ROOT}/{FORMAL_REVIEWER}')
        self.assertEqual(calls[0]['identity'], {'session': CLAUDE_UUID})

    def test_only_arbiter_claim_keeps_idle_text_and_reads_no_process(self):
        def formal_elsewhere(record):
            formal = record['review_dispatch_claim'][0]
            formal['head_sha'] = OTHER_HEAD
            formal['review_result'] = dict(formal['review_result'], head=OTHER_HEAD)

        store = self._new_store(formal_elsewhere)
        self._claim_arbiter(store, reviewer='claude-reviewer')
        self._dispatch_arbiter(store)

        result, calls = self._run(store, live_pid=9)

        row = self._row(result)
        self.assertEqual(row['state'], board_check.ACTIONABLE)
        self.assertEqual(row['evidence'], [
            'Task has next step RECORD_REVIEW_RESULT and nothing is running'])
        self.assertEqual(calls, [])

    def test_formal_only_outputs_are_unchanged(self):
        store = self._new_store()
        result, calls = self._run(store, live_pid=9)
        row = self._row(result)
        self.assertEqual(row['state'], board_check.ACTIONABLE)
        self.assertEqual(row['evidence'], [
            'review claim v1 COMPLETED: result awaiting ingestion, next step '
            'RECORD_REVIEW_RESULT'])
        self.assertEqual(calls, [])

        store2 = self._new_store()
        self._dispatch_formal(store2, 2)
        result2, calls2 = self._run(store2, live_pid=9)
        row2 = self._row(result2)
        self.assertEqual(row2['state'], board_check.WAITING_REVIEW)
        self.assertEqual(row2['evidence'], [
            f'review claim v2 on PR #{PR} head {HEAD[:12]}; '
            'Reviewer process 9 proven in its D6'])
        self.assertEqual(len(calls2), 1)
        self.assertEqual(calls2[0]['root'], f'{ROOT}/{FORMAL_REVIEWER}')
        self.assertEqual(calls2[0]['identity'], {'session': CLAUDE_UUID})

    def test_related_passes_the_full_claim_list(self):
        store = self._new_store()
        self._claim_arbiter(store)
        self._complete_arbiter(store)
        seen = []

        def outcomes(record, claims, get):
            seen.append(list(claims))
            return []

        self._run(store, outcomes=outcomes)

        full = store.get(TASK)['review_dispatch_claim']
        self.assertEqual(seen, [full])
        self.assertEqual([item['version'] for item in seen[0]], [1, 2])
        self.assertEqual(seen[0][1]['purpose'], 'dispute_ruling')
        self.assertEqual(len(seen[0]), len(full))


if __name__ == '__main__':
    unittest.main()
