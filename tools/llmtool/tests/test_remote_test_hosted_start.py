"""Unit tests for _hosted_start in remote_test.py."""

import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
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
REF = 'refs/pull/123/head'
MODULES = ['test_module_one']
EXECUTOR_TASK = 'd6-task'


def _make_task_record(task_id, run_id, pm_session_id):
    request = 'Bounded remote test dispatch task.'
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


def _make_run(run_id, nonce):
    return {
        'id': run_id,
        'display_title': remote_test._HOSTED_TITLE_PREFIX + nonce,
        'head_branch': 'main',
        'path': remote_test._HOSTED_WORKFLOW_PATH,
        'actor': {'login': remote_test._HOSTED_ACTORS[0]},
        'triggering_actor': {'login': remote_test._HOSTED_ACTORS[0]},
    }


class RemoteTestHostedStartTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = TaskStore(Path(self.temp_dir.name).resolve() / 'tasks')
        self.record = _make_task_record(TASK_ID, RUN_ID, PM_SESSION_ID)
        self.task = self.store.create(self.record)

    def test_entry_stored_before_the_post(self):
        post_calls = []
        observed_states_during_post = []

        def fake_post(path, body):
            post_calls.append((path, body))
            stored_task = self.store.get(TASK_ID)
            dispatches = stored_task.get('remote_test_dispatches', [])
            self.assertEqual(len(dispatches), 1)
            observed_states_during_post.append(dispatches[0]['state'])
            self.assertEqual(dispatches[0]['nonce'], body['inputs']['nonce'])
            return 'DISPATCHED'

        run_obj = None

        def fake_get(path):
            nonlocal run_obj
            stored_task = self.store.get(TASK_ID)
            nonce = stored_task['remote_test_dispatches'][0]['nonce']
            run_obj = _make_run(12345, nonce)
            return {'workflow_runs': [run_obj]}

        base_time = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)
        tag, result_run, nonce = remote_test._hosted_start(
            self.store, TASK_ID, self.task,
            executor_task=EXECUTOR_TASK, sha=SHA, ref=REF, modules=MODULES,
            get=fake_get, post=fake_post, now=base_time)

        self.assertEqual(tag, 'RUN')
        self.assertEqual(result_run, run_obj)
        self.assertEqual(len(post_calls), 1)
        self.assertEqual(post_calls[0][0], remote_test._HOSTED_DISPATCH_PATH)
        self.assertEqual(observed_states_during_post, ['DISPATCHED'])

        stored_task = self.store.get(TASK_ID)
        stored_entry = stored_task['remote_test_dispatches'][0]
        self.assertEqual(stored_entry['state'], 'RUN_FOUND')
        self.assertEqual(stored_entry['run_id'], 12345)
        self.assertEqual(stored_entry['nonce'], nonce)

    def test_pending_reuse_with_no_second_post(self):
        base_time = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)
        nonce = 'e' * 32
        entry = {
            'nonce': nonce,
            'sha': SHA,
            'executor_task': EXECUTOR_TASK,
            'modules': MODULES,
            'dispatched_at': '2026-11-09T12:00:00Z',
            'run_id': None,
            'state': 'DISPATCHED',
        }
        self.store.append_remote_test_dispatch(TASK_ID, entry)
        self.task = self.store.get(TASK_ID)

        post_called = False

        def fake_post(path, body):
            nonlocal post_called
            post_called = True
            raise AssertionError('POST should not be called for pending reuse')

        run_obj = _make_run(55555, nonce)

        def fake_get(path):
            return {'workflow_runs': [run_obj]}

        tag, res_run, res_nonce = remote_test._hosted_start(
            self.store, TASK_ID, self.task,
            executor_task=EXECUTOR_TASK, sha=SHA, ref=REF, modules=MODULES,
            get=fake_get, post=fake_post, now=base_time)

        self.assertFalse(post_called)
        self.assertEqual(tag, 'RUN')
        self.assertEqual(res_nonce, nonce)
        self.assertEqual(res_run['id'], 55555)

        stored = self.store.get(TASK_ID)['remote_test_dispatches'][0]
        self.assertEqual(stored['state'], 'RUN_FOUND')
        self.assertEqual(stored['run_id'], 55555)

        # Calling again when already RUN_FOUND reuses with no second POST
        tag2, res_run2, res_nonce2 = remote_test._hosted_start(
            self.store, TASK_ID, self.store.get(TASK_ID),
            executor_task=EXECUTOR_TASK, sha=SHA, ref=REF, modules=MODULES,
            get=fake_get, post=fake_post, now=base_time)
        self.assertFalse(post_called)
        self.assertEqual(tag2, 'RUN')
        self.assertEqual(res_nonce2, nonce)

    def test_unconfirmed_dispatch(self):
        def fake_post(path, body):
            return 'UNCONFIRMED'

        def fake_get(path):
            raise AssertionError('GET should not be called on unconfirmed dispatch')

        tag, result, code, nonce = remote_test._hosted_start(
            self.store, TASK_ID, self.task,
            executor_task=EXECUTOR_TASK, sha=SHA, ref=REF, modules=MODULES,
            get=fake_get, post=fake_post)

        self.assertEqual(tag, 'OUTCOME')
        self.assertEqual(result, 'UNAVAILABLE')
        self.assertEqual(code, 'HOSTED_DISPATCH_UNCONFIRMED')

        stored = self.store.get(TASK_ID)['remote_test_dispatches'][0]
        self.assertEqual(stored['state'], 'DISPATCHED')
        self.assertEqual(stored['nonce'], nonce)

    def test_run_not_found_young_and_old_abandoned(self):
        base_time = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)

        def fake_post(path, body):
            return 'DISPATCHED'

        def fake_get_empty(path):
            return {'workflow_runs': []}

        # Young: 10 minutes elapsed (< 30 minutes)
        young_time = base_time + timedelta(minutes=10)
        tag, result, code, nonce = remote_test._hosted_start(
            self.store, TASK_ID, self.task,
            executor_task=EXECUTOR_TASK, sha=SHA, ref=REF, modules=MODULES,
            get=fake_get_empty, post=fake_post, now=base_time)

        self.assertEqual(tag, 'OUTCOME')
        self.assertEqual(result, 'UNAVAILABLE')
        self.assertEqual(code, 'HOSTED_PENDING')

        stored = self.store.get(TASK_ID)['remote_test_dispatches'][0]
        self.assertEqual(stored['state'], 'DISPATCHED')

        # Check pending reuse when still young
        self.task = self.store.get(TASK_ID)
        tag_young, res_young, code_young, nonce_young = remote_test._hosted_start(
            self.store, TASK_ID, self.task,
            executor_task=EXECUTOR_TASK, sha=SHA, ref=REF, modules=MODULES,
            get=fake_get_empty, post=fake_post, now=young_time)
        self.assertEqual((tag_young, res_young, code_young, nonce_young),
                         ('OUTCOME', 'UNAVAILABLE', 'HOSTED_PENDING', nonce))
        stored = self.store.get(TASK_ID)['remote_test_dispatches'][0]
        self.assertEqual(stored['state'], 'DISPATCHED')

        # Old: 30 minutes elapsed (>= 30 minutes) -> ABANDONED
        old_time = base_time + timedelta(minutes=30)
        tag_old, res_old, code_old, nonce_old = remote_test._hosted_start(
            self.store, TASK_ID, self.task,
            executor_task=EXECUTOR_TASK, sha=SHA, ref=REF, modules=MODULES,
            get=fake_get_empty, post=fake_post, now=old_time)
        self.assertEqual((tag_old, res_old, code_old, nonce_old),
                         ('OUTCOME', 'UNAVAILABLE', 'HOSTED_DISPATCH_UNCONFIRMED', nonce))

        stored = self.store.get(TASK_ID)['remote_test_dispatches'][0]
        self.assertEqual(stored['state'], 'ABANDONED')

    def test_run_ambiguous(self):
        def fake_post(path, body):
            return 'DISPATCHED'

        def fake_get(path):
            stored = self.store.get(TASK_ID)
            nonce = stored['remote_test_dispatches'][0]['nonce']
            return {'workflow_runs': [_make_run(101, nonce), _make_run(102, nonce)]}

        tag, result, code, nonce = remote_test._hosted_start(
            self.store, TASK_ID, self.task,
            executor_task=EXECUTOR_TASK, sha=SHA, ref=REF, modules=MODULES,
            get=fake_get, post=fake_post)

        self.assertEqual((tag, result, code), ('OUTCOME', 'UNKNOWN', 'HOSTED_RUN_AMBIGUOUS'))
        stored = self.store.get(TASK_ID)['remote_test_dispatches'][0]
        self.assertEqual(stored['state'], 'DISPATCHED')

    def test_run_unreadable(self):
        def fake_post(path, body):
            return 'DISPATCHED'

        def fake_get(path):
            raise OSError('Network unreadable')

        tag, result, code, nonce = remote_test._hosted_start(
            self.store, TASK_ID, self.task,
            executor_task=EXECUTOR_TASK, sha=SHA, ref=REF, modules=MODULES,
            get=fake_get, post=fake_post)

        self.assertEqual((tag, result, code), ('OUTCOME', 'UNAVAILABLE', 'HOSTED_PENDING'))
        stored = self.store.get(TASK_ID)['remote_test_dispatches'][0]
        self.assertEqual(stored['state'], 'DISPATCHED')

    def test_run_found_moves_dispatched_to_run_found(self):
        def fake_post(path, body):
            return 'DISPATCHED'

        run_id = 99999
        run_dict = None

        def fake_get(path):
            nonlocal run_dict
            stored = self.store.get(TASK_ID)
            nonce = stored['remote_test_dispatches'][0]['nonce']
            run_dict = _make_run(run_id, nonce)
            return {'workflow_runs': [run_dict]}

        tag, run_res, nonce = remote_test._hosted_start(
            self.store, TASK_ID, self.task,
            executor_task=EXECUTOR_TASK, sha=SHA, ref=REF, modules=MODULES,
            get=fake_get, post=fake_post)

        self.assertEqual(tag, 'RUN')
        self.assertEqual(run_res, run_dict)

        stored = self.store.get(TASK_ID)['remote_test_dispatches'][0]
        self.assertEqual(stored['state'], 'RUN_FOUND')
        self.assertEqual(stored['run_id'], run_id)

    def test_records_nothing_else(self):
        def fake_post(path, body):
            return 'DISPATCHED'

        def fake_get(path):
            stored = self.store.get(TASK_ID)
            nonce = stored['remote_test_dispatches'][0]['nonce']
            return {'workflow_runs': [_make_run(1234, nonce)]}

        remote_test._hosted_start(
            self.store, TASK_ID, self.task,
            executor_task=EXECUTOR_TASK, sha=SHA, ref=REF, modules=MODULES,
            get=fake_get, post=fake_post)

        stored = self.store.get(TASK_ID)
        self.assertNotIn('remote_test_receipts', stored)
        self.assertNotIn('remote_test_outcomes', stored)
        self.assertNotIn('completion_events', stored)


if __name__ == '__main__':
    unittest.main()
