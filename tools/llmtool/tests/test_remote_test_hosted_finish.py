"""Unit tests for _hosted_finish and _run_hosted in remote_test.py."""

import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from llmtool import remote_test
from llmtool.task_store import TaskStore
from llmtool.task_submission_cli import _record

import review_trust_fixture


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


SHA = 'a' * 40
TASK_ID = '1' * 32
RUN_ID = '2' * 32
PM_SESSION_ID = 'pm-' + '3' * 32
NONCE = '4' * 32
WORKFLOW_SHA = '5' * 40
TREE_SHA = '6' * 40
REF = 'refs/pull/123/head'
MODULES = ['test_module_one']
EXECUTOR_TASK = 'd6-task'
RUN_ID_INT = 1001
JOB_ID_INT = 2002
CHECK_RUN_ID_INT = 3003


def _make_task_record(task_id, run_id, pm_session_id):
    request = 'Bounded remote test finish task.'
    request_sha = hashlib.sha256(request.encode()).hexdigest()
    task = _record(
        task_id, run_id, pm_session_id, 'pm-profile', request,
        state='PM_RESPONDED', provider_type='openai-compatible', model='pm-model',
        backend='NO_NATIVE_SESSION_ID_EXPOSED', request_sha=request_sha)
    task.update(
        pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                          'base_url': None},
        response='PM returned a bounded task.', error_code=None,
        pm_assignment={'schema_version': 1, 'route': 'codex', 'risk': 'LOW',
                       'goal': 'Produce a proposal diff.',
                       'acceptance': ['the proposal is recorded']},
    )
    return task


def _make_receipt_dict(
    result='PASS',
    exit_code=0,
    sha=SHA,
    modules=None,
    fetch_head=SHA,
    tree=TREE_SHA,
    failures=(),
    final=('test_module_one ... OK',),
    ran_total=1,
    failure_excerpt=None,
):
    if modules is None:
        modules = MODULES
    if result == 'PASS':
        exit_code = 0
        fetch_head = sha
        failures = []
        if ran_total <= 0:
            ran_total = 1
    elif exit_code == 0:
        exit_code = 1
    receipt = {
        'result': result,
        'exit_code': exit_code,
        'source_sha': sha,
        'tree': tree,
        'fetch_head': fetch_head,
        'modules': list(modules),
        'identity': {'uid': 1000},
        'started_at': '2026-11-09T10:00:00Z',
        'ended_at': '2026-11-09T10:01:00Z',
        'elapsed_s': 60.0,
        'ran_total': ran_total,
        'final': list(final),
        'failures': list(failures),
        'log_bytes': 100,
        'log_sha256': 'a' * 64,
        'remote_log_path': None,
    }
    if failure_excerpt is not None:
        receipt['failure_excerpt'] = failure_excerpt
    return receipt


def _make_artifact_zip(receipt_dict):
    raw = json.dumps(receipt_dict, separators=(',', ':'), sort_keys=True).encode('utf-8')
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, 'w') as zf:
        zf.writestr('receipt.json', raw)
    return bio.getvalue()


def _make_run(run_id=RUN_ID_INT, nonce=NONCE, status='completed', conclusion='success'):
    return {
        'id': run_id,
        'status': status,
        'conclusion': conclusion,
        'run_attempt': 1,
        'path': remote_test._HOSTED_WORKFLOW_PATH,
        'head_sha': WORKFLOW_SHA,
        'display_title': remote_test._HOSTED_TITLE_PREFIX + nonce,
        'head_branch': 'main',
        'actor': {'login': remote_test._HOSTED_ACTORS[0]},
        'triggering_actor': {'login': remote_test._HOSTED_ACTORS[0]},
    }


def _make_job(job_id=JOB_ID_INT, check_run_id=CHECK_RUN_ID_INT, status='completed', conclusion='success',
              runner_group_name=remote_test._HOSTED_RUNNER_GROUP):
    return {
        'id': job_id,
        'name': remote_test._HOSTED_JOB_NAME,
        'status': status,
        'conclusion': conclusion,
        'labels': list(remote_test._HOSTED_LABELS),
        'runner_group_name': runner_group_name,
        'check_run_id': check_run_id,
    }


class RemoteTestHostedFinishTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = TaskStore(Path(self.temp_dir.name).resolve() / 'tasks')
        self.record = _make_task_record(TASK_ID, RUN_ID, PM_SESSION_ID)
        self.task = self.store.create(self.record)

    def _append_dispatch(self, nonce=NONCE, state='RUN_FOUND', run_id=RUN_ID_INT):
        entry = {
            'nonce': nonce,
            'sha': SHA,
            'executor_task': EXECUTOR_TASK,
            'modules': list(MODULES),
            'dispatched_at': '2026-11-09T10:00:00Z',
            'run_id': run_id,
            'state': state,
        }
        self.store.append_remote_test_dispatch(TASK_ID, entry)
        self.task = self.store.get(TASK_ID)
        return entry

    def test_pending_run_returns_outcome_and_keeps_dispatch_entry(self):
        self._append_dispatch(NONCE, state='RUN_FOUND', run_id=RUN_ID_INT)
        run = _make_run(RUN_ID_INT, NONCE, status='in_progress', conclusion=None)

        outcome = remote_test._hosted_finish(
            self.store, TASK_ID, self.task, run, NONCE,
            executor_task=EXECUTOR_TASK, sha=SHA, ref=REF, modules=MODULES,
        )

        self.assertEqual(outcome['result'], 'UNAVAILABLE')
        self.assertEqual(outcome['code'], 'HOSTED_PENDING')
        stored_entry = self.store.get(TASK_ID)['remote_test_dispatches'][0]
        self.assertEqual(stored_entry['state'], 'RUN_FOUND')

    def test_pending_job_returns_outcome_and_keeps_dispatch_entry(self):
        self._append_dispatch(NONCE, state='RUN_FOUND', run_id=RUN_ID_INT)
        run = _make_run(RUN_ID_INT, NONCE, status='completed', conclusion=None)
        job = _make_job(JOB_ID_INT, CHECK_RUN_ID_INT, status='in_progress', conclusion=None)

        def fake_get(path):
            if path == f'actions/runs/{RUN_ID_INT}/jobs':
                return {'jobs': [job]}
            raise AssertionError(f'Unexpected GET path: {path}')

        outcome = remote_test._hosted_finish(
            self.store, TASK_ID, self.task, run, NONCE,
            executor_task=EXECUTOR_TASK, sha=SHA, ref=REF, modules=MODULES,
            get=fake_get,
        )

        self.assertEqual(outcome['result'], 'UNAVAILABLE')
        self.assertEqual(outcome['code'], 'HOSTED_PENDING')
        stored_entry = self.store.get(TASK_ID)['remote_test_dispatches'][0]
        self.assertEqual(stored_entry['state'], 'RUN_FOUND')

    def test_pass_receipt_stored_with_actions_block_and_entry_recorded(self):
        self._append_dispatch(NONCE, state='RUN_FOUND', run_id=RUN_ID_INT)
        run = _make_run(RUN_ID_INT, NONCE, status='completed', conclusion='success')
        job = _make_job(JOB_ID_INT, CHECK_RUN_ID_INT, status='completed', conclusion='success')
        receipt_dict = _make_receipt_dict('PASS')
        zip_bytes = _make_artifact_zip(receipt_dict)

        def fake_get(path):
            if path == f'actions/runs/{RUN_ID_INT}/jobs':
                return {'jobs': [job]}
            if path == f'check-runs/{CHECK_RUN_ID_INT}':
                return {'app': {'id': remote_test._HOSTED_APP_ID}, 'head_sha': WORKFLOW_SHA}
            if path.startswith(f'actions/runs/{RUN_ID_INT}/artifacts'):
                return {'total_count': 1, 'artifacts': [
                    {'id': 777, 'name': remote_test._HOSTED_ARTIFACT_PREFIX + NONCE,
                     'expired': False, 'size_in_bytes': len(zip_bytes)}
                ]}
            raise AssertionError(f'Unexpected GET path: {path}')

        def fake_get_bytes(path):
            self.assertEqual(path, 'actions/artifacts/777/zip')
            return zip_bytes

        saved = remote_test._hosted_finish(
            self.store, TASK_ID, self.task, run, NONCE,
            executor_task=EXECUTOR_TASK, sha=SHA, ref=REF, modules=MODULES,
            get=fake_get, get_bytes=fake_get_bytes,
        )

        self.assertEqual(saved['result'], 'PASS')
        self.assertEqual(saved['exit_code'], 0)
        self.assertEqual(saved['task_id'], TASK_ID)
        self.assertEqual(saved['run_id'], RUN_ID)
        self.assertEqual(saved['executor_task'], EXECUTOR_TASK)
        self.assertEqual(saved['pm_session'], PM_SESSION_ID)

        actions = saved['actions']
        self.assertEqual(actions['run_id'], RUN_ID_INT)
        self.assertEqual(actions['run_attempt'], 1)
        self.assertEqual(actions['job_id'], JOB_ID_INT)
        self.assertEqual(actions['check_run_id'], CHECK_RUN_ID_INT)
        self.assertEqual(actions['workflow_path'], remote_test._HOSTED_WORKFLOW_PATH)
        self.assertEqual(actions['workflow_sha'], WORKFLOW_SHA)
        self.assertEqual(actions['labels'], ['ubuntu-24.04'])
        self.assertEqual(actions['runner_group'], 'GitHub Actions')
        self.assertEqual(actions['nonce'], NONCE)

        expected_cmd_digest = remote_test._hosted_command_digest(
            remote_test._HOSTED_WORKFLOW_PATH, WORKFLOW_SHA,
            sha=SHA, ref=REF, modules=MODULES, nonce=NONCE,
        )
        self.assertEqual(saved['command_digest'], expected_cmd_digest)

        stored_task = self.store.get(TASK_ID)
        stored_receipts = stored_task['remote_test_receipts']
        self.assertEqual(len(stored_receipts), 1)
        self.assertEqual(stored_receipts[0]['receipt_digest'], saved['receipt_digest'])

        stored_entry = stored_task['remote_test_dispatches'][0]
        self.assertEqual(stored_entry['state'], 'RECORDED')

        events = stored_task.get('completion_events', [])
        self.assertTrue(any(e.get('source') == 'EC2_REMOTE_TEST' for e in events))

    def test_fail_receipt_stored_with_actions_block_and_entry_recorded(self):
        self._append_dispatch(NONCE, state='RUN_FOUND', run_id=RUN_ID_INT)
        run = _make_run(RUN_ID_INT, NONCE, status='completed', conclusion='failure')
        job = _make_job(JOB_ID_INT, CHECK_RUN_ID_INT, status='completed', conclusion='failure')
        receipt_dict = _make_receipt_dict(
            'FAIL', exit_code=1, failures=['test_module_one'],
            final=['FAIL: test_module_one'],
            failure_excerpt='FAIL: test_module_one\nAssertionError',
        )
        zip_bytes = _make_artifact_zip(receipt_dict)

        def fake_get(path):
            if path == f'actions/runs/{RUN_ID_INT}/jobs':
                return {'jobs': [job]}
            if path == f'check-runs/{CHECK_RUN_ID_INT}':
                return {'app': {'id': remote_test._HOSTED_APP_ID}, 'head_sha': WORKFLOW_SHA}
            if path.startswith(f'actions/runs/{RUN_ID_INT}/artifacts'):
                return {'total_count': 1, 'artifacts': [
                    {'id': 777, 'name': remote_test._HOSTED_ARTIFACT_PREFIX + NONCE,
                     'expired': False, 'size_in_bytes': len(zip_bytes)}
                ]}
            raise AssertionError(f'Unexpected GET path: {path}')

        def fake_get_bytes(path):
            self.assertEqual(path, 'actions/artifacts/777/zip')
            return zip_bytes

        saved = remote_test._hosted_finish(
            self.store, TASK_ID, self.task, run, NONCE,
            executor_task=EXECUTOR_TASK, sha=SHA, ref=REF, modules=MODULES,
            get=fake_get, get_bytes=fake_get_bytes,
        )

        self.assertEqual(saved['result'], 'FAIL')
        self.assertEqual(saved['exit_code'], 1)
        self.assertEqual(saved['failures'], ['test_module_one'])
        self.assertEqual(saved['failure_excerpt'], 'FAIL: test_module_one\nAssertionError')
        self.assertIn('actions', saved)

        stored_task = self.store.get(TASK_ID)
        self.assertEqual(len(stored_task['remote_test_receipts']), 1)
        self.assertEqual(stored_task['remote_test_receipts'][0]['result'], 'FAIL')
        self.assertEqual(stored_task['remote_test_dispatches'][0]['state'], 'RECORDED')

    def test_cancelled_run_outcome_records_and_marks_entry_recorded(self):
        self._append_dispatch(NONCE, state='RUN_FOUND', run_id=RUN_ID_INT)
        run = _make_run(RUN_ID_INT, NONCE, status='completed', conclusion='cancelled')
        job = _make_job(JOB_ID_INT, CHECK_RUN_ID_INT, status='completed', conclusion='cancelled')

        def fake_get(path):
            if path == f'actions/runs/{RUN_ID_INT}/jobs':
                return {'jobs': [job]}
            if path == f'check-runs/{CHECK_RUN_ID_INT}':
                return {'app': {'id': remote_test._HOSTED_APP_ID}, 'head_sha': WORKFLOW_SHA}
            raise AssertionError(f'Unexpected GET path: {path}')

        outcome = remote_test._hosted_finish(
            self.store, TASK_ID, self.task, run, NONCE,
            executor_task=EXECUTOR_TASK, sha=SHA, ref=REF, modules=MODULES,
            get=fake_get,
        )

        self.assertEqual(outcome['result'], 'UNKNOWN')
        self.assertEqual(outcome['code'], 'HOSTED_RUN_CANCELLED')

        stored_task = self.store.get(TASK_ID)
        self.assertEqual(len(stored_task.get('remote_test_receipts', [])), 0)
        self.assertEqual(len(stored_task['remote_test_outcomes']), 1)
        self.assertEqual(stored_task['remote_test_outcomes'][0]['code'], 'HOSTED_RUN_CANCELLED')
        self.assertEqual(stored_task['remote_test_dispatches'][0]['state'], 'RECORDED')

    def test_invalid_artifact_records_outcome_and_marks_entry_recorded(self):
        self._append_dispatch(NONCE, state='RUN_FOUND', run_id=RUN_ID_INT)
        run = _make_run(RUN_ID_INT, NONCE, status='completed', conclusion='success')
        job = _make_job(JOB_ID_INT, CHECK_RUN_ID_INT, status='completed', conclusion='success')

        def fake_get(path):
            if path == f'actions/runs/{RUN_ID_INT}/jobs':
                return {'jobs': [job]}
            if path == f'check-runs/{CHECK_RUN_ID_INT}':
                return {'app': {'id': remote_test._HOSTED_APP_ID}, 'head_sha': WORKFLOW_SHA}
            if path.startswith(f'actions/runs/{RUN_ID_INT}/artifacts'):
                return {'total_count': 1, 'artifacts': [
                    {'id': 777, 'name': remote_test._HOSTED_ARTIFACT_PREFIX + NONCE,
                     'expired': False, 'size_in_bytes': 30}
                ]}
            raise AssertionError(f'Unexpected GET path: {path}')

        def fake_get_bytes(path):
            return b'corrupted not a zip content'

        outcome = remote_test._hosted_finish(
            self.store, TASK_ID, self.task, run, NONCE,
            executor_task=EXECUTOR_TASK, sha=SHA, ref=REF, modules=MODULES,
            get=fake_get, get_bytes=fake_get_bytes,
        )

        self.assertEqual(outcome['result'], 'UNKNOWN')
        self.assertEqual(outcome['code'], 'RECEIPT_INVALID')

        stored_task = self.store.get(TASK_ID)
        self.assertEqual(len(stored_task.get('remote_test_receipts', [])), 0)
        self.assertEqual(len(stored_task['remote_test_outcomes']), 1)
        self.assertEqual(stored_task['remote_test_outcomes'][0]['code'], 'RECEIPT_INVALID')
        self.assertEqual(stored_task['remote_test_dispatches'][0]['state'], 'RECORDED')

    def test_missing_actions_block_records_outcome_and_marks_entry_recorded(self):
        self._append_dispatch(NONCE, state='RUN_FOUND', run_id=RUN_ID_INT)
        run = _make_run(RUN_ID_INT, NONCE, status='completed', conclusion='success')
        # Missing or invalid runner_group causes _hosted_actions_block to return None
        job = _make_job(JOB_ID_INT, CHECK_RUN_ID_INT, status='completed', conclusion='success',
                        runner_group_name='')
        receipt_dict = _make_receipt_dict('PASS')
        zip_bytes = _make_artifact_zip(receipt_dict)

        def fake_get(path):
            if path == f'actions/runs/{RUN_ID_INT}/jobs':
                return {'jobs': [job]}
            if path == f'check-runs/{CHECK_RUN_ID_INT}':
                return {'app': {'id': remote_test._HOSTED_APP_ID}, 'head_sha': WORKFLOW_SHA}
            if path.startswith(f'actions/runs/{RUN_ID_INT}/artifacts'):
                return {'total_count': 1, 'artifacts': [
                    {'id': 777, 'name': remote_test._HOSTED_ARTIFACT_PREFIX + NONCE,
                     'expired': False, 'size_in_bytes': len(zip_bytes)}
                ]}
            raise AssertionError(f'Unexpected GET path: {path}')

        def fake_get_bytes(path):
            return zip_bytes

        outcome = remote_test._hosted_finish(
            self.store, TASK_ID, self.task, run, NONCE,
            executor_task=EXECUTOR_TASK, sha=SHA, ref=REF, modules=MODULES,
            get=fake_get, get_bytes=fake_get_bytes,
        )

        self.assertEqual(outcome['result'], 'UNKNOWN')
        self.assertEqual(outcome['code'], 'RECEIPT_INVALID')

        stored_task = self.store.get(TASK_ID)
        self.assertEqual(len(stored_task.get('remote_test_receipts', [])), 0)
        self.assertEqual(len(stored_task['remote_test_outcomes']), 1)
        self.assertEqual(stored_task['remote_test_outcomes'][0]['code'], 'RECEIPT_INVALID')
        self.assertEqual(stored_task['remote_test_dispatches'][0]['state'], 'RECORDED')

    def test_run_hosted_end_to_end_one_dispatch(self):
        post_calls = []

        def fake_post(path, body):
            post_calls.append((path, body))
            return 'DISPATCHED'

        run_obj = _make_run(RUN_ID_INT, NONCE, status='completed', conclusion='success')
        job = _make_job(JOB_ID_INT, CHECK_RUN_ID_INT, status='completed', conclusion='success')
        receipt_dict = _make_receipt_dict('PASS')
        zip_bytes = _make_artifact_zip(receipt_dict)

        def fake_get(path):
            if path.startswith('actions/workflows/llmtool-remote-test.yml/runs'):
                stored_task = self.store.get(TASK_ID)
                dispatches = stored_task.get('remote_test_dispatches', [])
                if dispatches:
                    nonce = dispatches[0]['nonce']
                    run_obj['display_title'] = remote_test._HOSTED_TITLE_PREFIX + nonce
                return {'workflow_runs': [run_obj]}
            if path == f'actions/runs/{RUN_ID_INT}/jobs':
                return {'jobs': [job]}
            if path == f'check-runs/{CHECK_RUN_ID_INT}':
                return {'app': {'id': remote_test._HOSTED_APP_ID}, 'head_sha': WORKFLOW_SHA}
            if path.startswith(f'actions/runs/{RUN_ID_INT}/artifacts'):
                stored_task = self.store.get(TASK_ID)
                nonce = stored_task['remote_test_dispatches'][0]['nonce']
                return {'total_count': 1, 'artifacts': [
                    {'id': 777, 'name': remote_test._HOSTED_ARTIFACT_PREFIX + nonce,
                     'expired': False, 'size_in_bytes': len(zip_bytes)}
                ]}
            raise AssertionError(f'Unexpected GET path: {path}')

        def fake_get_bytes(path):
            self.assertEqual(path, 'actions/artifacts/777/zip')
            return zip_bytes

        base_time = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)
        saved = remote_test._run_hosted(
            self.store, TASK_ID, self.task,
            executor_task=EXECUTOR_TASK, sha=SHA, ref=REF, modules=MODULES,
            get=fake_get, post=fake_post, get_bytes=fake_get_bytes, now=base_time,
        )

        self.assertEqual(saved['result'], 'PASS')
        self.assertEqual(len(post_calls), 1)
        self.assertEqual(post_calls[0][0], remote_test._HOSTED_DISPATCH_PATH)
        self.assertEqual(post_calls[0][1]['inputs']['sha'], SHA)
        self.assertEqual(post_calls[0][1]['inputs']['ref'], REF)

        stored_task = self.store.get(TASK_ID)
        self.assertEqual(len(stored_task['remote_test_receipts']), 1)
        stored_entry = stored_task['remote_test_dispatches'][0]
        self.assertEqual(stored_entry['state'], 'RECORDED')
        self.assertEqual(stored_entry['run_id'], RUN_ID_INT)

    def test_run_hosted_outcome_recorded_when_start_returns_outcome(self):
        def fake_post(path, body):
            return 'UNCONFIRMED'

        def fake_get(path):
            raise AssertionError('GET should not be called when dispatch is unconfirmed')

        outcome = remote_test._run_hosted(
            self.store, TASK_ID, self.task,
            executor_task=EXECUTOR_TASK, sha=SHA, ref=REF, modules=MODULES,
            get=fake_get, post=fake_post,
        )

        self.assertEqual(outcome['result'], 'UNAVAILABLE')
        self.assertEqual(outcome['code'], 'HOSTED_DISPATCH_UNCONFIRMED')

        stored_task = self.store.get(TASK_ID)
        self.assertEqual(len(stored_task.get('remote_test_receipts', [])), 0)
        self.assertEqual(len(stored_task['remote_test_outcomes']), 1)
        self.assertEqual(stored_task['remote_test_outcomes'][0]['code'], 'HOSTED_DISPATCH_UNCONFIRMED')
        self.assertEqual(stored_task['remote_test_dispatches'][0]['state'], 'DISPATCHED')
