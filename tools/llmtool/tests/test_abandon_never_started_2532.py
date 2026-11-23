"""#2532: abandon a never-started DISPATCHING or RUNNING Task, refuse any start evidence."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import test_task_store
import worker_policy_fixture
from llmtool import review_repair, task_status
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore, new_id

HEAD = 'a' * 40
EXECUTOR = 'executor-2532-d6'


def _clean_d6(head=HEAD, *, clean=True, result_sha=None):
    target = head if result_sha is None else result_sha
    return lambda _task, _executor: (
        SimpleNamespace(root='/d6', branch='fix/2532', result_sha=target),
        SimpleNamespace(root='/d6', branch='fix/2532', head=head, clean=clean),
    )


def _process(status):
    return lambda _store, _executor: status


class AbandonNeverStartedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TaskStore(Path(self.temp.name).resolve() / 'tasks')
        self.policy_path = worker_policy_fixture.install(self)

    def _d6_completion(self, executor_task=EXECUTOR):
        return {
            'state': 'WAITING_EXECUTOR',
            'executor_task_id': executor_task,
            'reviewer_task_id': None,
            'evidence': None,
            'missing_gates': [],
            'pm_dispatch': 'NOT_STARTED',
            'pm_response': None,
            'pm_recommendation': None,
            'pm_usage': None,
            'pm_error_code': None,
        }

    def _make_direct_task(self, *, profile='agy', state='DEVELOPER_DISPATCHING',
                          boundary=None, execution=None, executor_task=EXECUTOR):
        task = test_task_store.TaskStoreTests._codex_dispatch_task(self)
        task['pm_assignment'] = dict(task['pm_assignment'], route=profile)
        self.store.create(task)
        pending = test_task_store.TaskStoreTests._developer_assignment(
            provider=profile, max_tokens=4096 if profile == 'opencode' else None)
        pending['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
        pending['source_sha'] = HEAD
        if profile == 'agy':
            pending['owner_assignment_ref'] = None
        if profile == 'opencode':
            pending['execution'] = None
        completion = self._d6_completion(executor_task) if executor_task else None
        self.store.compare_and_update(
            task['task_id'], expected={'developer': None},
            developer=pending, completion=completion)
        if state == 'DEVELOPER_PENDING':
            return self.store.get(task['task_id'])
        dispatching = dict(pending, state='DEVELOPER_DISPATCHING',
                           provider_type=profile + '-cli',
                           model='gemini-test' if profile == 'agy' else 'opencode/test-free')
        if boundary == 'PREPARED':
            self.store.prepare_direct_dispatch(
                task['task_id'], expected_developer=pending, developer=dispatching)
        elif boundary == 'STARTING':
            prepared = self.store.prepare_direct_dispatch(
                task['task_id'], expected_developer=pending, developer=dispatching)['developer']
            self.store.authorize_direct_spawn(task['task_id'], expected_developer=prepared)
        else:
            if execution is not None:
                dispatching['execution'] = execution
            self.store.update(task['task_id'], developer=dispatching)
        if state == 'DEVELOPER_DISPATCHING':
            return self.store.get(task['task_id'])
        raise ValueError(f'unsupported test state: {state}')

    def _assert_abandon_refused(self, task, *, registered_d6=None, process_status=None):
        before = self.store.get(task['task_id'])
        with self.assertRaises(LLMToolError) as caught:
            self.store.abandon(
                task['task_id'], pm_session_id=task['pm_session_id'],
                reason='Attempt to abandon started task',
                registered_d6=registered_d6 or _clean_d6(),
                process_status=process_status or _process('NONE'))
        self.assertEqual(caught.exception.task_store_error_code,
                         'TASK_ABANDONMENT_START_EVIDENCE')
        self.assertIn('llmtool task advance', str(caught.exception))
        self.assertEqual(self.store.get(task['task_id']), before)
        self.assertNotIn('abandonment', self.store.get(task['task_id']))

    def test_never_started_dispatching_task_can_be_abandoned(self):
        for boundary in (None, 'PREPARED'):
            with self.subTest(boundary=boundary):
                task = self._make_direct_task(
                    profile='agy', state='DEVELOPER_DISPATCHING', boundary=boundary,
                    executor_task=f'{EXECUTOR}-{boundary or "none"}')
                saved = self.store.abandon(
                    task['task_id'], pm_session_id=task['pm_session_id'],
                    reason='Never started before dispatch interruption',
                    registered_d6=_clean_d6(), process_status=_process('NONE'))
                self.assertEqual(saved['abandonment']['reason'],
                                 'Never started before dispatch interruption')
                self.assertEqual(task_status.derive_state(saved), 'ABANDONED')

    def test_never_started_running_task_with_no_execution_can_be_abandoned(self):
        task = self._make_direct_task(
            profile='agy', state='DEVELOPER_PENDING', executor_task=EXECUTOR + '-pending')
        self.assertEqual(task_status.derive_state(task), 'RUNNING')
        with mock.patch.object(review_repair, '_registered_d6', side_effect=_clean_d6()):
            saved = self.store.abandon(
                task['task_id'], pm_session_id=task['pm_session_id'],
                reason='Pending task never launched')
        self.assertEqual(saved['abandonment']['reason'], 'Pending task never launched')
        self.assertEqual(task_status.derive_state(saved), 'ABANDONED')

    def test_task_with_execution_record_is_refused(self):
        task = self._make_direct_task(profile='agy', state='DEVELOPER_DISPATCHING')
        self.store.record_agy_launch(
            task['task_id'], assignment_id=task['developer']['assignment_id'],
            pid=999999, start_time='Mon Jan 01 00:00:00 2026', pgid=999999)
        launched = self.store.get(task['task_id'])
        self._assert_abandon_refused(launched)

    def test_task_with_provider_invocation_or_output_is_refused(self):
        starting = self._make_direct_task(
            profile='agy', state='DEVELOPER_DISPATCHING', boundary='STARTING',
            executor_task=EXECUTOR + '-starting')
        self._assert_abandon_refused(starting)

        responded_task = self._make_direct_task(
            profile='agy', state='DEVELOPER_DISPATCHING',
            executor_task=EXECUTOR + '-responded')
        artifact = ('diff --git a/src.py b/src.py\n'
                    '--- a/src.py\n+++ b/src.py\n@@ -1 +1 @@\n-x=1\n+x=2\n')
        responded_dev = dict(
            responded_task['developer'], state='DEVELOPER_RESPONDED',
            artifact=artifact,
            artifact_sha256=hashlib.sha256(artifact.encode()).hexdigest())
        self.store.update(responded_task['task_id'], developer=responded_dev)
        self._assert_abandon_refused(self.store.get(responded_task['task_id']))

    def test_task_with_bound_process_or_dirty_d6_is_refused(self):
        task = self._make_direct_task(profile='agy', state='DEVELOPER_DISPATCHING')
        for status in ('ALIVE', 'UNKNOWN'):
            with self.subTest(process_status=status):
                self._assert_abandon_refused(task, process_status=_process(status))
        self._assert_abandon_refused(task, registered_d6=_clean_d6(clean=False))
        self._assert_abandon_refused(task, registered_d6=_clean_d6(head='b' * 40))
        self._assert_abandon_refused(task, registered_d6=_clean_d6(result_sha='b' * 40))

    def test_real_d6_process_check_allows_unavailable_neighbour_and_refuses_active_one(self):
        task = self._make_direct_task(profile='agy', state='DEVELOPER_DISPATCHING')
        neighbour_id = new_id()
        real_ids, real_get = self.store.task_ids, self.store.get

        def with_neighbour(record):
            def get(tid, *args, **kwargs):
                if tid == neighbour_id:
                    return dict(record)
                return real_get(tid, *args, **kwargs)
            return (mock.patch.object(self.store, 'task_ids',
                                      lambda: [*real_ids(), neighbour_id]),
                    mock.patch.object(self.store, 'get', get))

        unavailable = {'task_id': neighbour_id, 'developer': {
            'state': 'DEVELOPER_UNAVAILABLE', 'executor_task_id': EXECUTOR,
            'execution': None, 'error_code': 'DEVELOPER_GW_AGENT_POLICY_REFUSED_PROFILE_INVALID'}}
        p1, p2 = with_neighbour(unavailable)
        with p1, p2:
            saved = self.store.abandon(
                task['task_id'], pm_session_id=task['pm_session_id'],
                reason='Unavailable neighbour does not block',
                registered_d6=_clean_d6())
        self.assertEqual(saved['abandonment']['reason'],
                         'Unavailable neighbour does not block')

        task2 = self._make_direct_task(
            profile='agy', state='DEVELOPER_DISPATCHING', executor_task=EXECUTOR + '-2')
        active = {'task_id': neighbour_id, 'developer': {
            'state': 'DEVELOPER_DISPATCHING', 'executor_task_id': EXECUTOR + '-2',
            'execution': None}}
        p1, p2 = with_neighbour(active)
        with p1, p2:
            self._assert_abandon_refused(task2, registered_d6=_clean_d6(), process_status=None)


if __name__ == '__main__':
    unittest.main()
