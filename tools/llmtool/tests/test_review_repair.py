import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llmtool import cli, review_repair

TASK, RUN, HEAD, CONTRACT = 'a' * 32, 'b' * 32, 'c' * 40, 'd' * 64
EXECUTOR, PR, EVENT_ID = 'executor-d6', 42, 'e' * 32


def _comment(status='FAIL', *, comment_id=9, actor='Claude'):
    marker = 'FIX' if status == 'FAIL' else status
    body = (f'<!-- activity: type=review_verdict actor={actor} target=PM status={marker} -->\n'
            f'Verdict: {status} | Version: v1 | HEAD: {HEAD}\n'
            'Finding: address the reported issue\n')
    return {'id': comment_id, 'body': body, 'created_at': '2026-10-27T01:00:00Z',
            'updated_at': '2026-10-27T01:00:00Z'}


class TempTaskStore:
    """Small persistent TaskStore fake preserving compare-and-update semantics."""

    def __init__(self, root, record):
        self.path = Path(root) / 'task.json'
        self.path.write_text(json.dumps(record))

    def get(self, task_id):
        return json.loads(self.path.read_text())

    def compare_and_update(self, task_id, *, expected, **changes):
        record = self.get(task_id)
        if any(record.get(key) != value for key, value in expected.items()):
            raise RuntimeError('conflict')
        record.update(changes)
        record['record_sha256'] = hashlib.sha256(json.dumps(
            {key: value for key, value in record.items() if key != 'record_sha256'},
            sort_keys=True).encode()).hexdigest()
        self.path.write_text(json.dumps(record))
        return record


def _record():
    claim = {'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': 1,
             'state': 'DISPATCHED', 'reviewer_task': 'reviewer-d6',
             'native_session_id': 'review-session', 'account_mode': 'DEFAULT_SECOND_ACCOUNT',
             'claimed_at': '2026-10-27T00:00:00Z', 'updated_at': '2026-10-27T00:00:00Z'}
    event = {'event_id': EVENT_ID, 'source': 'REVIEWER', 'state': 'PENDING',
             'source_sha': HEAD, 'session_id': 'review-' + EVENT_ID,
             'received_at': '2026-10-27T01:01:00Z'}
    record = {
        'task_id': TASK, 'run_id': RUN,
        'developer': {'assignment_id': 'f' * 32, 'provider_profile': 'codex',
                      'model': 'gpt-test', 'owner_assignment_ref': None,
                      'selection_source': 'PM', 'read_paths': ['tools/llmtool/llmtool'],
                      'write_paths': ['tools/llmtool/llmtool/review_repair.py'],
                      'acceptance': ['Repair the reported issue.'],
                      'egress_basis': 'Task authorized source files only.',
                      'state': 'DEVELOPER_RESPONDED'},
        'completion': {'executor_task_id': EXECUTOR, 'evidence': {'common_dir': '/tmp/git'}},
        'review_dispatch_claim': [claim], 'completion_events': [event],
        'record_sha256': '0' * 64,
    }
    record['record_sha256'] = hashlib.sha256(json.dumps(
        {key: value for key, value in record.items() if key != 'record_sha256'},
        sort_keys=True).encode()).hexdigest()
    return record


class ReviewRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TempTaskStore(self.temp.name, _record())
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def dispatch(self, **kwargs):
        def adapter(**request):
            self.calls.append(request)
            return {'status': 'DISPATCHED', 'head': HEAD}
        current_head = kwargs.pop('current_head', lambda _task, _executor: HEAD)
        github_get = kwargs.pop('github_get', lambda _path: [_comment()])
        decision = kwargs.pop('decision', type('Decision', (), {'allowed': True})())
        with patch.object(review_repair.worker_dispatch, 'evaluate',
                          return_value=decision):
            return review_repair.repair_review(
                store=self.store, task_id=TASK, executor_task=EXECUTOR,
                pr_number=PR, github_get=github_get,
                dispatch_adapter=adapter, current_head=current_head, **kwargs)

    def test_happy_path_packet_and_next_action(self):
        result = self.dispatch()
        self.assertEqual(result['next_action'], 'review-dispatch --version 2')
        self.assertEqual(self.calls[0]['packet']['findings_count'], 1)
        self.assertEqual(self.store.get(TASK)['review_repair_claims'][0]['attempt'], 1)

    def test_replay_after_restart_does_not_dispatch_twice(self):
        self.dispatch()
        restarted = TempTaskStore(self.temp.name, self.store.get(TASK))
        self.store = restarted
        result = self.dispatch()
        self.assertTrue(result['replayed'])
        self.assertEqual(len(self.calls), 1)

    def test_limit_reached(self):
        result = self.dispatch()
        record = self.store.get(TASK)
        second = dict(record['review_repair_claims'][0], head_sha='a' * 40,
                      version=2, attempt=2, packet_sha256='1' * 64)
        record['review_repair_claims'].append(second)
        self.store = TempTaskStore(self.temp.name, record)
        with self.assertRaisesRegex(Exception, 'refused') as raised:
            self.dispatch(github_get=lambda _path: [_comment(comment_id=10)])
        self.assertEqual(getattr(raised.exception, 'review_repair_code'), 'REPAIR_LIMIT_REACHED')

    def test_head_moved(self):
        with self.assertRaisesRegex(Exception, 'refused') as raised:
            self.dispatch(current_head=lambda _task, _executor: 'a' * 40)
        self.assertEqual(getattr(raised.exception, 'review_repair_code'), 'REPAIR_HEAD_MOVED')
        self.assertEqual(self.calls, [])

    def test_paused_policy_refused(self):
        decision = type('Decision', (), {'allowed': False, 'reason_code': 'PAUSED'})()
        with patch.object(review_repair.worker_dispatch, 'evaluate', return_value=decision):
            with self.assertRaisesRegex(Exception, 'refused') as raised:
                self.dispatch(decision=decision)
        self.assertEqual(getattr(raised.exception, 'review_repair_code'), 'PAUSED')

    def test_pass_event_does_not_dispatch(self):
        with self.assertRaisesRegex(Exception, 'refused'):
            review_repair.repair_review(
                store=self.store, task_id=TASK, executor_task=EXECUTOR,
                pr_number=PR, github_get=lambda _path: [_comment('PASS', actor='Codex')],
                dispatch_adapter=lambda **_kwargs: self.calls.append('bad'),
                current_head=lambda _task, _executor: HEAD)
        self.assertEqual(self.calls, [])

    def test_auto_review_calls_next_version(self):
        seen = []
        self.dispatch(auto_review=True, review_dispatcher=lambda **kwargs: seen.append(kwargs) or 'review')
        self.assertEqual(seen[0]['version'], 2)

    def test_default_adapter_rebuilds_original_direct_developer_request(self):
        seen = []
        with patch.object(review_repair.worker_dispatch, 'evaluate',
                          return_value=type('Decision', (), {'allowed': True})()):
            result = review_repair.repair_review(
                store=self.store, task_id=TASK, executor_task=EXECUTOR,
                pr_number=PR, github_get=lambda _path: [_comment()],
                current_head=lambda _task, _executor: HEAD,
                delegate_callable=lambda args, store: seen.append((args, store)) or 0)
        args, passed_store = seen[0]
        self.assertIs(passed_store, self.store)
        self.assertEqual((args.provider, args.mode, args.executor_task),
                         ('codex', 'direct', EXECUTOR))
        self.assertEqual(args.read_path, ['tools/llmtool/llmtool'])
        self.assertEqual(args.write_path, ['tools/llmtool/llmtool/review_repair.py'])
        self.assertEqual(args.acceptance[0], 'Repair the reported issue.')
        self.assertIn('comment id 9', args.acceptance[1])
        self.assertIn(HEAD, args.acceptance[1])
        self.assertIn('findings count 1', args.acceptance[1])
        self.assertEqual(args.repair_packet['verdict_comment_id'], 9)
        self.assertEqual(result['result'], 0)

    def test_cli_route_reaches_handler(self):
        with patch.object(cli, 'run_task_repair', return_value=17) as handler:
            with patch('llmtool.source_freshness.require_fresh_source'):
                self.assertEqual(cli.main(['task', 'repair', TASK, '--executor-task', EXECUTOR,
                                           '--pr', str(PR)]), 17)
        handler.assert_called_once()


if __name__ == '__main__':
    unittest.main()
