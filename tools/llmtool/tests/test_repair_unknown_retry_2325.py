"""#2325: a launched repair whose outcome is unknown is retried only when it provably ended.

Through the real task_advance.advance path (store, D6 snapshot and PR snapshot
are test doubles at their boundaries): confirmed cleanup on an unchanged D6
retries the same DISPATCHED claim once (REPAIR_RETRY, the existing retry path);
every doubt stays REPAIR_OUTCOME_UNKNOWN and names its reason and next command.
A repair Task that never reached a Developer is never retried here (#2492).
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from llmtool import task_advance  # noqa: E402
from test_task_advance import HEAD, PR, base_record, d6  # noqa: E402

REPAIR = '3' * 32


def _developer(cleanup='CONFIRMED', state='DEVELOPER_OUTCOME_UNKNOWN'):
    return {'state': state,
            'execution': {'status': 'UNKNOWN', 'cleanup_status': cleanup}}


class _Store:
    def __init__(self, repair_record):
        self.record = base_record(
            review_dispatch_claim=[{'pr': 17, 'head_sha': HEAD, 'version': 1,
                                    'state': 'DISPATCHED'}],
            review_repair_claims=[{'head_sha': HEAD, 'state': 'DISPATCHED',
                                   'repair_task_id': REPAIR, 'pr': 17,
                                   'family': 'pr-17', 'attempt': 1}])
        self.repair_record = repair_record
        self.writes = 0

    def get(self, task_id):
        if task_id == REPAIR:
            return self.repair_record
        return self.record

    def compare_and_update(self, *_args, **_kwargs):
        self.writes += 1


def _advance(store, *, snapshot=None, pr=PR, repair=None):
    calls = []

    def repair_call(**kwargs):
        calls.append(kwargs)
        return {'claim': {'state': 'DISPATCHED'}, 'repair_task_id': REPAIR}

    with patch.object(task_advance, '_d6_snapshot', return_value=snapshot or d6()), \
         patch.object(task_advance, '_pr_snapshot', return_value=pr), \
         patch.object(task_advance.shutil, 'which', return_value='/usr/bin/x'), \
         patch.object(task_advance, '_review_outcomes', return_value=[{
             'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
             'outcome': 'REVIEW_FAIL'}]):
        result = task_advance.advance(task_id=store.record['task_id'], store=store,
                                      pr_number=17, github_get=lambda _path: [],
                                      repair_review_call=repair or repair_call)
    return result, calls


class LaunchedUnknownRepairTests(unittest.TestCase):
    def test_confirmed_cleanup_on_unchanged_d6_retries_the_same_claim_once(self):
        store = _Store({'task_id': REPAIR, 'developer': _developer()})
        result, calls = _advance(store)
        self.assertEqual(result['step'], 'REPAIR_RETRY')
        self.assertEqual(len(calls), 1)
        # The existing retry path: the same DISPATCHED claim is named; the
        # stored claims are not rewritten here (counts unchanged by advance).
        self.assertEqual(calls[0]['retry_claim_id'], REPAIR)
        self.assertEqual(len(store.record['review_repair_claims']), 1)
        self.assertEqual(store.writes, 0)

    def test_unconfirmed_cleanup_stays_unknown_with_reason_and_next_command(self):
        store = _Store({'task_id': REPAIR, 'developer': _developer(cleanup='UNKNOWN')})
        result, calls = _advance(store, repair=lambda **_k: self.fail('no repair may start'))
        self.assertEqual(result['step'], 'REPAIR_OUTCOME_UNKNOWN')
        self.assertEqual(result['codes'],
                         ['REPAIR_OUTCOME_UNKNOWN', 'REPAIR_CLEANUP_NOT_CONFIRMED'])
        self.assertEqual(result['result']['repair_task_id'], REPAIR)
        self.assertEqual(result['result']['next_command'], f'llmtool task brief {REPAIR}')
        self.assertEqual(calls, [])

    def test_dirty_or_moved_d6_never_retries(self):
        for snapshot in (d6(clean=False), d6(recorded='b' * 40)):
            with self.subTest(snapshot=snapshot):
                store = _Store({'task_id': REPAIR, 'developer': _developer()})
                # advance stops earlier on a dirty or moved D6; no repair starts.
                result, _calls = _advance(
                    store, snapshot=snapshot,
                    repair=lambda **_k: self.fail('no repair may start'))
                self.assertNotEqual(result.get('step'), 'REPAIR_RETRY')
                self.assertEqual(
                    task_advance.unknown_repair_reason(
                        store.repair_record, store.record['review_repair_claims'][0],
                        snapshot, PR),
                    'REPAIR_D6_DIRTY_OR_MOVED')

    def test_moved_pr_head_stays_unknown(self):
        store = _Store({'task_id': REPAIR, 'developer': _developer()})
        moved = {'number': 17, 'head': {'sha': 'c' * 40}, 'merged': False}
        self.assertEqual(
            task_advance.unknown_repair_reason(
                store.repair_record, store.record['review_repair_claims'][0], d6(), moved),
            'REPAIR_PR_HEAD_MOVED')

    def test_never_delegated_repair_is_not_retried(self):
        # #2492 item 1 = B: a repair Task without a Developer record stays UNKNOWN.
        store = _Store({'task_id': REPAIR, 'developer': None})
        result, _calls = _advance(store, repair=lambda **_k: self.fail('no repair may start'))
        self.assertEqual(result['step'], 'REPAIR_OUTCOME_UNKNOWN')
        self.assertIn('REPAIR_NOT_LAUNCHED_OR_STATE_CHANGED', result['codes'])

    def test_ec2_path_uses_the_same_rule(self):
        retryable = {'head_sha': HEAD, 'state': 'DISPATCHED', 'repair_state': 'UNKNOWN',
                     'unknown_reason': None}
        self.assertEqual(task_advance._remote_fail_action(
            {'repair_claims': [retryable]}, HEAD), 'REPAIR_RETRY')
        for claim in (dict(retryable, unknown_reason='REPAIR_CLEANUP_NOT_CONFIRMED'),
                      {k: v for k, v in retryable.items() if k != 'unknown_reason'}):
            with self.subTest(claim=claim):
                self.assertEqual(task_advance._remote_fail_action(
                    {'repair_claims': [claim]}, HEAD), 'REPAIR_OUTCOME_UNKNOWN')


if __name__ == '__main__':
    unittest.main()
