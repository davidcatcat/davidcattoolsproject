"""#2781: a manual task repair retries an unknown review repair only when safe."""

import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import review_repair
from llmtool.errors import LLMToolError

import test_review_repair as trr
import worker_policy_fixture

REPAIR_ID = '2' * 32
MOVED_HEAD = 'd' * 40


def setUpModule():
    trr.setUpModule()


def tearDownModule():
    trr.tearDownModule()


def _claim():
    return {
        'executor_task': trr.EXECUTOR, 'pr': trr.PR, 'head_sha': trr.HEAD,
        'version': 1, 'attempt': 1, 'family': f'pr-{trr.PR}',
        'state': 'DISPATCHED', 'repair_task_id': REPAIR_ID,
        'comment_id': 9, 'packet_sha256': 'f' * 64,
        'claimed_at': '2026-10-27T01:00:00Z',
    }


def _root_record():
    record = trr._record()
    record['review_repair_claims'] = [_claim()]
    return record


def _repair_record(*, cleanup='CONFIRMED', state='DEVELOPER_OUTCOME_UNKNOWN'):
    return {
        'task_id': REPAIR_ID,
        'run_id': '3' * 32,
        'pm_assignment': {
            'route': 'agy', 'risk': 'LOW', 'goal': 'Repair the issue.',
            'acceptance': ['Repair the reported issue.'],
        },
        'developer': {
            'provider_profile': 'agy',
            'state': state,
            'dispatch_boundary': 'STOPPED',
            'execution': {'status': 'BLOCKED', 'cleanup_status': cleanup},
        },
    }


def _registration(head=trr.HEAD):
    return SimpleNamespace(task_id=trr.EXECUTOR, root='/registered/d6',
                           common_dir='/tmp/git', branch='feat/task',
                           result_sha=head)


def _snapshot(*, head=trr.HEAD, clean=True):
    return SimpleNamespace(head=head, clean=clean, root='/registered/d6',
                           common_dir='/tmp/git', branch='feat/task')


def _github(pr_head=trr.HEAD):
    def get(path):
        if path.startswith('pulls/'):
            return {'number': trr.PR, 'head': {'sha': pr_head},
                    'state': 'open', 'merged': False}
        return [trr._comment()]
    return get


class ReviewRepairUnknownRetry2781Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        worker_policy_fixture.install(self)

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, *, repair=None, registration=None, snapshot=None,
             pr_head=trr.HEAD, auto=False, retry_claim_id=None):
        calls = []

        def adapter(**request):
            calls.append(request)
            return {'status': 'DISPATCHED', 'head': trr.HEAD}

        registration = registration or _registration()
        snapshot = snapshot or _snapshot()
        repair = _repair_record() if repair is None else repair
        store = trr.TempTaskStore(self.temp.name, [_root_record(), repair])
        self.last_calls = calls
        self.last_store = store
        with patch.object(review_repair.task_completion, 'read_d6_registration',
                          return_value=registration), \
             patch.object(review_repair.task_completion, 'collect_git_snapshot',
                          return_value=snapshot), \
             patch.object(review_repair.worker_dispatch, 'evaluate',
                          return_value=SimpleNamespace(allowed=True)), \
             patch.object(review_repair.shutil, 'which', return_value='/usr/bin/codex'):
            return review_repair.repair_review(
                store=store, task_id=trr.TASK, executor_task=trr.EXECUTOR,
                pr_number=trr.PR, github_get=_github(pr_head),
                current_head=lambda _task, _executor: trr.HEAD,
                dispatch_adapter=adapter, auto=auto,
                retry_claim_id=retry_claim_id)

    def _assert_replayed_without_dispatch(self, result):
        self.assertTrue(result['replayed'])
        self.assertEqual(result['repair_task_id'], REPAIR_ID)
        self.assertEqual(self.last_calls, [])
        claims = self.last_store.get(trr.TASK)['review_repair_claims']
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]['repair_task_id'], REPAIR_ID)
        self.assertEqual(claims[0]['state'], 'DISPATCHED')

    def test_manual_repair_retries_the_same_dispatched_unknown_claim(self):
        result = self._run()
        self.assertEqual(len(self.last_calls), 1)
        self.assertEqual(self.last_calls[0]['task']['task_id'], REPAIR_ID)
        claims = self.last_store.get(trr.TASK)['review_repair_claims']
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]['repair_task_id'], REPAIR_ID)
        self.assertEqual(claims[0]['attempt'], 1)
        self.assertEqual(claims[0]['state'], 'DISPATCHED')
        self.assertEqual(result['repair_task_id'], REPAIR_ID)
        self.assertEqual(result['claim']['attempt'], 1)
        self.assertTrue(result['replayed'])

    def test_dirty_d6_only_replays(self):
        result = self._run(snapshot=_snapshot(clean=False))
        self._assert_replayed_without_dispatch(result)

    def test_unconfirmed_cleanup_only_replays(self):
        result = self._run(repair=_repair_record(cleanup='UNCONFIRMED'))
        self._assert_replayed_without_dispatch(result)

    def test_moved_d6_head_only_replays(self):
        result = self._run(registration=_registration(head=MOVED_HEAD))
        self._assert_replayed_without_dispatch(result)

    def test_moved_pr_head_only_replays(self):
        result = self._run(pr_head=MOVED_HEAD)
        self._assert_replayed_without_dispatch(result)

    def test_auto_route_still_refuses_the_same_retry(self):
        with self.assertRaises(LLMToolError) as caught:
            self._run(auto=True, retry_claim_id=REPAIR_ID)
        self.assertEqual(caught.exception.review_repair_code,
                         'REVIEW_REPAIR_PM_REQUIRED')
        self.assertEqual(self.last_calls, [])
        claims = self.last_store.get(trr.TASK)['review_repair_claims']
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]['state'], 'DISPATCHED')


if __name__ == '__main__':
    unittest.main()
