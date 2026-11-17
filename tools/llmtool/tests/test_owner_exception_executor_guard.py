"""#1817: an owner-exception merge always checks the executor identity.

Post-merge audit (Codex v995 on PR #1514, CWE-863): `task advance --merge
--owner-exception` checked the D6 executor identity only when the caller also
passed `--executor-task`. These tests drive the real `task_advance.advance`
gate; only the D6 snapshot, the GitHub reads and the merge itself are injected.
"""

import unittest
from unittest.mock import patch

from llmtool import task_advance


HEAD = 'a' * 40
TASK = '1' * 32
EXECUTOR = 'executor-1'
OWNER_EXCEPTION = 'https://github.com/example/repo/issues/1253#issuecomment-1'
PR = {'number': 17, 'head': {'sha': HEAD}, 'merged': False, 'state': 'open'}
REACHED = 'TEST_MERGE_ONCE_REACHED'


def record(executor=EXECUTOR):
    completion = {'state': 'WAITING_REVIEW',
                  'evidence': {'result': {'result_head': HEAD}}}
    if executor is not None:
        completion['executor_task_id'] = executor
    return {'task_id': TASK, 'run_id': '2' * 32,
            'developer': {'state': 'DEVELOPER_RESPONDED'},
            'completion': completion, 'review_dispatch_claim': [],
            'review_repair_claims': [], 'completion_events': []}


def snapshot(*, task_id=EXECUTOR, agent='pm-deterministic', **changes):
    value = {'root': '/repo/d6', 'common_dir': '/repo/.git', 'task_id': task_id,
             'branch': 'task/branch', 'result_head': HEAD,
             'recorded_result_head': HEAD, 'registered_result_head': HEAD,
             'dirty': False, 'state': 'CLEAN', 'registration_state': 'ACTIVE',
             'trusted_agent_id': agent}
    value.update(changes)
    return value


class Store:
    def __init__(self, value):
        self.record = value

    def get(self, _task_id):
        return self.record


class OwnerExceptionExecutorGuardTests(unittest.TestCase):

    def _advance(self, value, d6, **kwargs):
        store = Store(value)
        snapshots, merges = [], []

        def take_snapshot(*args, **options):
            snapshots.append((args, options))
            return d6

        def merge_once(**options):
            merges.append(options)
            return {'step': 'MERGE_REFUSED', 'codes': [REACHED], 'result': None}

        with patch.object(task_advance, '_d6_snapshot', side_effect=take_snapshot), \
             patch.object(task_advance, '_pr_snapshot', return_value=dict(PR)), \
             patch.object(task_advance, '_review_outcomes', return_value=[]), \
             patch.object(task_advance, '_merged_review_target', return_value=None), \
             patch.object(task_advance, '_merge_once', side_effect=merge_once):
            result = task_advance.advance(task_id=TASK, store=store, merge=True,
                                          pr_number=17, github_get=lambda _path: None,
                                          **kwargs)
        return result, snapshots, merges

    # Owner-exception merge without --executor-task: the check now runs.

    def test_exception_merge_without_flag_refuses_non_deterministic_executor(self):
        result, snapshots, merges = self._advance(
            record(), snapshot(agent='claude-developer'),
            owner_exception=OWNER_EXCEPTION)
        self.assertEqual(result['step'], 'MERGE_REFUSED')
        self.assertEqual(result['codes'], ['OWNER_EXCEPTION_EXECUTOR_INVALID'])
        self.assertEqual(merges, [])
        # The completion executor's own D6 is read, without the cwd fallback.
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0][0][1], EXECUTOR)
        self.assertNotIn('allow_cwd_fallback', snapshots[0][1])

    def test_exception_merge_without_flag_refuses_d6_bound_to_other_task(self):
        result, _, merges = self._advance(
            record(), snapshot(task_id='executor-2'), owner_exception=OWNER_EXCEPTION)
        self.assertEqual(result['codes'], ['OWNER_EXCEPTION_EXECUTOR_INVALID'])
        self.assertEqual(merges, [])

    def test_exception_merge_without_flag_refuses_unbound_d6(self):
        for d6 in ({'state': 'D6_BINDING_UNAVAILABLE', 'code': 'D6_BINDING_UNAVAILABLE'},
                   snapshot(registration_state='RETIRED'),
                   snapshot(dirty=True, state='DIRTY'),
                   snapshot(recorded_result_head='b' * 40)):
            with self.subTest(d6=d6):
                result, _, merges = self._advance(record(), d6,
                                                  owner_exception=OWNER_EXCEPTION)
                self.assertEqual(result['codes'], ['OWNER_EXCEPTION_EXECUTOR_INVALID'])
                self.assertEqual(merges, [])

    def test_exception_merge_without_any_executor_is_refused(self):
        result, snapshots, merges = self._advance(
            record(executor=None), snapshot(), owner_exception=OWNER_EXCEPTION)
        self.assertEqual(result['step'], 'MERGE_REFUSED')
        self.assertEqual(result['codes'], ['OWNER_EXCEPTION_EXECUTOR_UNKNOWN'])
        self.assertEqual(snapshots, [])
        self.assertEqual(merges, [])

    def test_exception_merge_without_flag_and_matching_executor_proceeds(self):
        result, _, merges = self._advance(
            record(), snapshot(), owner_exception=OWNER_EXCEPTION)
        self.assertEqual(result['codes'], [REACHED])
        self.assertEqual(len(merges), 1)
        self.assertEqual(merges[0]['owner_exception'], OWNER_EXCEPTION)
        self.assertEqual(merges[0]['head'], HEAD)

    # Owner-exception merge with --executor-task: unchanged.

    def test_exception_merge_with_matching_flag_proceeds(self):
        result, snapshots, merges = self._advance(
            record(), snapshot(), owner_exception=OWNER_EXCEPTION,
            executor_task=EXECUTOR)
        self.assertEqual(result['codes'], [REACHED])
        self.assertEqual(len(merges), 1)
        self.assertIs(snapshots[0][1].get('allow_cwd_fallback'), True)

    def test_exception_merge_with_flag_and_no_completion_executor_proceeds(self):
        result, _, merges = self._advance(
            record(executor=None), snapshot(), owner_exception=OWNER_EXCEPTION,
            executor_task=EXECUTOR)
        self.assertEqual(result['codes'], [REACHED])
        self.assertEqual(len(merges), 1)

    def test_exception_merge_with_flag_naming_other_executor_is_refused(self):
        result, snapshots, merges = self._advance(
            record(), snapshot(), owner_exception=OWNER_EXCEPTION,
            executor_task='executor-2')
        self.assertEqual(result['codes'], ['OWNER_EXCEPTION_EXECUTOR_MISMATCH'])
        self.assertEqual(snapshots, [])
        self.assertEqual(merges, [])

    def test_exception_merge_with_flag_refuses_non_deterministic_executor(self):
        result, _, merges = self._advance(
            record(), snapshot(agent='claude-developer'),
            owner_exception=OWNER_EXCEPTION, executor_task=EXECUTOR)
        self.assertEqual(result['codes'], ['OWNER_EXCEPTION_EXECUTOR_INVALID'])
        self.assertEqual(merges, [])

    # Non-exception merge: unchanged, no executor identity requirement.

    def test_plain_merge_does_not_require_deterministic_executor(self):
        result, snapshots, merges = self._advance(
            record(), snapshot(agent='claude-developer'))
        self.assertEqual(result['codes'], [REACHED])
        self.assertEqual(len(merges), 1)
        self.assertIsNone(merges[0]['owner_exception'])
        self.assertNotIn('allow_cwd_fallback', snapshots[0][1])

    def test_plain_merge_without_executor_keeps_d6_unavailable_code(self):
        result, snapshots, merges = self._advance(record(executor=None), snapshot())
        self.assertEqual(result['codes'], ['MERGE_D6_UNAVAILABLE'])
        self.assertEqual(snapshots, [])
        self.assertEqual(merges, [])

    def test_executor_flag_still_requires_owner_exception_merge(self):
        result, _, merges = self._advance(record(), snapshot(), executor_task=EXECUTOR)
        self.assertEqual(result['codes'], ['OWNER_EXCEPTION_EXECUTOR_REQUIRES_EXCEPTION'])
        self.assertEqual(merges, [])


if __name__ == '__main__':
    unittest.main()
