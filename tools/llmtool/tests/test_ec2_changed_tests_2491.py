"""#2491: the pre-review EC2 gate covers exact-head PR test modules.

The GitHub reads, the EC2 receipt and the review dispatch are injected; no
network, Git or EC2 is touched.
"""

import hashlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from llmtool import remote_test, task_advance
from llmtool.task_store import _json_bytes

HEAD = 'a' * 40
OTHER = 'b' * 40
TASK_ID = '1' * 32
RUN_ID = '2' * 32
PR = 17
DECLARED = 'test_declared_2491'
ADDED = 'test_pr_added_2491'


def record(modules=(DECLARED,), receipts=()):
    return {
        'task_id': TASK_ID, 'run_id': RUN_ID, 'pm_session_id': 'pm-' + '3' * 32,
        'backend_session_semantics': 'ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL',
        'pm_provenance': {'source': 'ACTIVE_PM_RUNTIME', 'provider_invoked': False},
        'developer': {
            'state': 'DEVELOPER_RESPONDED',
            'read_paths': [f'tools/llmtool/tests/{name}.py' for name in modules],
            'write_paths': [],
        },
        'completion': {
            'executor_task_id': 'executor-1', 'state': 'WAITING_REVIEW',
            'evidence': {'common_dir': '/repo/.git',
                         'result': {'result_head': HEAD}},
        },
        'review_dispatch_claim': [], 'review_repair_claims': [],
        'completion_events': [], 'remote_test_outcomes': [],
        'completion_event_tombstones': [],
        'remote_test_receipts': list(receipts), 'published_pr': None,
    }


def receipt(modules):
    value = {
        'task_id': TASK_ID, 'run_id': RUN_ID, 'executor_task': 'executor-1',
        'source_sha': HEAD, 'result': 'PASS', 'modules': list(modules),
        'ran_total': len(modules), 'elapsed_s': 0.01,
        'final': ['OK'] * len(modules), 'failures': [],
        'module_results': [{'module': name, 'ran': 1, 'skipped': 0, 'final': 'OK'}
                           for name in modules],
    }
    value['receipt_digest'] = hashlib.sha256(_json_bytes(value)).hexdigest()
    return value


def pr_snapshot(head=HEAD, changed_files=2):
    return {'number': PR, 'head': {'sha': head}, 'state': 'open', 'merged': False,
            'changed_files': changed_files}


def files(*names):
    return [{'filename': name, 'status': 'modified'} for name in names]


def changed_tests_get(*names, pr=None):
    snapshot = pr_snapshot(changed_files=len(names)) if pr is None else pr
    changed = files(*names)

    def get(path):
        if path == f'pulls/{PR}':
            return snapshot
        if path.startswith(f'pulls/{PR}/files?per_page='):
            return changed
        if path.startswith('commits/') and path.endswith('/check-runs?per_page=100'):
            return {'total_count': 1, 'check_runs': [{
                'id': 1, 'name': 'check (docs)', 'head_sha': HEAD,
                'status': 'completed', 'conclusion': 'success'}]}
        return []
    return get


class FakeStore:
    def __init__(self, value):
        self.record = value
        self.task = value

    def get(self, task_id):
        if task_id != self.record['task_id']:
            raise KeyError(task_id)
        return self.record

    def task_ids(self):
        return [self.record['task_id']]

    def append_remote_test_receipt(self, task_id, saved):
        self.record['remote_test_receipts'].append(saved)
        return saved

    def record_published_pr(self, task_id, pr, head_sha):
        self.record['published_pr'] = {'pr': pr, 'head_sha': head_sha}
        return self.record['published_pr']

    def append_remote_test_outcome(self, task_id, outcome):
        self.record['remote_test_outcomes'].append(outcome)
        return {'outcome': outcome,
                'counter': len(self.record['remote_test_outcomes']) - 1,
                'created': True}

    def record_completion_event(self, task_id, **event):
        saved = dict(event, task_id=task_id, state='PENDING')
        self.record['completion_events'].append(saved)
        return {'event': saved, 'created': True}


def d6():
    return {'root': '/repo/task', 'task_id': 'executor-1', 'branch': 'feat/x',
            'result_head': HEAD, 'recorded_result_head': HEAD, 'dirty': False,
            'state': 'CLEAN'}


class ChangedTestModuleSetTests(unittest.TestCase):
    def test_declared_and_undeclared_changed_module_are_required_and_admitted(self):
        value = record()
        get = changed_tests_get(f'tools/llmtool/tests/{ADDED}.py',
                                'docs/guide.md')

        modules, code = task_advance._pr_changed_test_modules(
            PR, HEAD, get, pr_snapshot())
        self.assertEqual(code, None)
        self.assertEqual(modules, (ADDED,))
        required = remote_test.remote_required_modules(value, modules)
        self.assertEqual(required, (DECLARED, ADDED))

        store = FakeStore(value)
        sent = []

        def transport(argv, _script, _timeout):
            sent.extend(argv[-2:])
            return SimpleNamespace(stdout=_pass_receipt((DECLARED, ADDED)),
                                   stderr=b'', returncode=0)

        with patch.object(remote_test, 'read_d6_registration',
                          return_value=SimpleNamespace(result_sha=HEAD)), \
                patch.object(remote_test, '_github_get', side_effect=get), \
                patch.object(remote_test, '_config', return_value={
                    'host': 'ec2.example.org', 'user': 'runner',
                    'repo_path': '/home/runner/project',
                    'python': '/usr/bin/python3'}), \
                patch.object(remote_test, '_ssh_timeout', return_value=60):
            result = remote_test.run_remote_test(
                TASK_ID, executor_task='d6-task', sha=HEAD,
                ref=f'refs/pull/{PR}/head', modules=(), pr=PR,
                store=store, transport=transport)

        self.assertEqual(result['result'], 'PASS')
        self.assertEqual(sent, [DECLARED, ADDED])


def _pass_receipt(modules):
    payload = {
        'result': 'PASS', 'exit_code': 0, 'source_sha': HEAD, 'tree': 'c' * 40,
        'fetch_head': HEAD, 'modules': list(modules), 'identity': {'uid': 65534},
        'started_at': '2026-11-23T00:00:00Z', 'ended_at': '2026-11-23T00:00:01Z',
        'elapsed_s': 1.0, 'ran_total': len(modules),
        'final': ['OK'] * len(modules), 'failures': [],
        'log_bytes': 0, 'log_sha256': 'd' * 64, 'remote_log_path': None,
        'module_results': [{'module': name, 'ran': 1, 'skipped': 0, 'final': 'OK'}
                           for name in modules],
    }
    return json.dumps(payload, separators=(',', ':')).encode()


class PreReviewGateTests(unittest.TestCase):
    def advance(self, value, get, dispatch):
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
                patch.object(task_advance, '_pr_snapshot',
                             return_value=pr_snapshot()), \
                patch.object(task_advance, '_review_outcomes', return_value=[]), \
                patch.object(task_advance, '_run_remote_test',
                             return_value={'code': None}):
            return task_advance.advance(
                task_id=TASK_ID, store=FakeStore(value), pr_number=PR,
                github_get=get, dispatch_review_call=dispatch,
                publish_gh_runner=lambda *_args: SimpleNamespace(returncode=0))

    def test_unreadable_or_mismatched_pr_files_block_review_with_code(self):
        def unreadable(path):
            if path.startswith(f'pulls/{PR}/files?per_page='):
                raise OSError('unavailable')
            return changed_tests_get()(path)

        dispatch = Mock(return_value={'state': 'DISPATCHED'})
        blocked = self.advance(record(receipts=[receipt((DECLARED,))]),
                               unreadable, dispatch)
        self.assertEqual(blocked['step'], task_advance.PR_CHANGED_TESTS_UNAVAILABLE)
        self.assertEqual(blocked['codes'], [task_advance.PR_CHANGED_TESTS_UNAVAILABLE])
        dispatch.assert_not_called()

        modules, code = task_advance._pr_changed_test_modules(
            PR, OTHER, changed_tests_get(), pr_snapshot())
        self.assertEqual(modules, None)
        self.assertEqual(code, task_advance.PR_CHANGED_TESTS_UNAVAILABLE)

        mismatched = pr_snapshot(changed_files=3)
        modules, code = task_advance._pr_changed_test_modules(
            PR, HEAD, changed_tests_get(f'tools/llmtool/tests/{ADDED}.py'), mismatched)
        self.assertEqual(modules, None)
        self.assertEqual(code, task_advance.PR_CHANGED_TESTS_UNAVAILABLE)

        dispatch = Mock(return_value={'state': 'DISPATCHED'})
        mismatch_advance = self.advance(
            record(receipts=[receipt((DECLARED,))]),
            changed_tests_get(f'tools/llmtool/tests/{ADDED}.py'), dispatch)
        self.assertEqual(mismatch_advance['step'],
                         task_advance.PR_CHANGED_TESTS_UNAVAILABLE)
        dispatch.assert_not_called()

    def test_pass_without_the_changed_module_runs_ec2_instead_of_review(self):
        dispatch = Mock(return_value={'state': 'DISPATCHED'})
        result = self.advance(
            record(receipts=[receipt((DECLARED,))]),
            changed_tests_get(f'tools/llmtool/tests/{ADDED}.py', 'docs/guide.md'),
            dispatch)
        self.assertEqual(result['step'], 'RUN_REMOTE_TEST')
        dispatch.assert_not_called()

    def test_pr_without_test_changes_still_dispatches_review(self):
        dispatch = Mock(return_value={'state': 'DISPATCHED'})
        result = self.advance(record(receipts=[receipt((DECLARED,))]),
                              changed_tests_get('docs/guide.md', 'docs/other.md'),
                              dispatch)
        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        dispatch.assert_called_once()


if __name__ == '__main__':
    unittest.main()
