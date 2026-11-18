"""#2310: a gw-agent delegate refused before any model call restarts on the same Task.

Every case runs ``task_submission_cli.run_task_delegate`` (the delegate path
``task delegate`` and review repair use) against a real temporary TaskStore,
a real registered D6 Git worktree and the fake gateway model server of
test_gw_agent_route_wiring. Only the Owner policy decision, the OS sandbox and
the adapter-boundary audit are injected, exactly as that module does. No
screen function is changed and no test string is credential-shaped.
"""

import io
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from llmtool import start_failure, task_store, task_submission_cli, worker_dispatch
from llmtool.developer_routes import load_route_config
from llmtool.errors import LLMToolError

import review_trust_fixture
# Imported as a module so its own test classes are not collected here again.
import test_gw_agent_route_wiring as wiring


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


_ALLOWED = worker_dispatch.PolicyDecision('gw-agent', True, 'OWNER_POLICY_ALLOWED', 'f' * 64)
_PAUSED = worker_dispatch.PolicyDecision('gw-agent', False, 'OWNER_PAUSED', 'f' * 64)
_GATED_ACTIONS = ('DISPATCH', 'START_RETRY', 'RETRY', 'RECOVERY', 'REPAIR', 'REASSIGN')


class GwAgentPreModelRestartTests(wiring.GwAgentRealStoreTests):
    """Reuses the real-store fixture (setUp, D6, PM Task, fake model server)."""

    def _run(self, task_id, target, *, gate=_ALLOWED, reach_model=True,
             served_model='qwen3.8-27b', wrap_store=None):
        """One ``run_task_delegate`` call for ``task_id``; returns (code, reloaded Task)."""
        self.task_id = task_id
        if reach_model:
            self.server.script.extend((
                self.reply(self.tool_call('apply_edit', path=target, new='hello synthetic\n',
                                          create=True), model=served_model, usage=(1026, 49)),
                self.reply(self.tool_call('finish', summary='done'), model=served_model,
                           usage=(1026, 49))))
        args = Namespace(
            task_id=task_id, provider='gw-agent', mode='direct', background=False,
            timeout=None, context_stdin=False, data_class='SYNTHETIC',
            egress_basis='Synthetic test content only.',
            acceptance=[f'Only {target} is created.'], selection_source='PM',
            executor_task='executor-d6', read_path=[target], write_path=[target],
            cwd=str(self.root))
        real_config = load_route_config()
        real_config['routes']['gw-agent']['package_path'] = str(
            Path(__file__).resolve().parents[2] / 'api-agent-gateway')
        store = self.store if wrap_store is None else wrap_store(self.store)
        with patch.object(task_submission_cli, 'load_route_config', return_value=real_config), \
             patch.object(task_submission_cli, 'for_platform',
                          return_value=wiring._NoSandboxBoundary()), \
             patch.object(worker_dispatch, 'authorize_dispatch', Mock()), \
             patch.object(worker_dispatch, 'evaluate', return_value=gate), \
             patch.object(task_submission_cli, '_bind_direct_native_session',
                          return_value=False), \
             redirect_stdout(io.StringIO()):
            code = task_submission_cli.run_task_delegate(args, store=store)
        return code, self._reloaded()

    def _refused_dispatching_write(self):
        """The store refuses the DISPATCHING write: a failure before the route starts."""
        return self._store_faulting(state='DEVELOPER_DISPATCHING',
                                    error=task_store.TaskStoreConflict)

    @staticmethod
    def _gated(task):
        return [(entry['worker_key'], entry['action'], entry['decision'], entry['reason_code'])
                for entry in task.get('dispatch_audit', [])
                if entry.get('action') in _GATED_ACTIONS]

    def test_owner_pause_refusal_restarts_on_the_same_task_and_d6(self):
        target = 'gw-agent-restart.md'
        task_id = self._active_pm_task(target)
        code, failed = self._run(task_id, target, gate=_PAUSED, reach_model=False)
        self.assertEqual(code, 1)
        self._assert_failed(failed, 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_OWNER_PAUSED',
                            provider_type=None, model=None)
        self.assertTrue(start_failure.gw_agent_record_checks(failed).start_failure)
        failed_developer = failed['developer']

        code, task = self._run(task_id, target)

        self.assertEqual(code, 0)
        self.assertEqual(task['task_id'], task_id)
        self._assert_success_persisted(task, target)
        self.assertEqual(task['completion']['executor_task_id'], 'executor-d6')
        new_developer = task['developer']
        self.assertEqual(new_developer['direct_result_callback']['executor_task_id'],
                         'executor-d6')
        self.assertNotEqual(new_developer['assignment_id'], failed_developer['assignment_id'])
        self.assertNotEqual(new_developer['session_id'], failed_developer['session_id'])
        # The restart is recorded: one archive entry and one ALLOWED START_RETRY.
        self.assertEqual(len(task['start_failures']), 1)
        entry = task['start_failures'][0]
        self.assertEqual(entry['failed_developer'], failed_developer)
        self.assertEqual(entry['retry_assignment_id'], new_developer['assignment_id'])
        self.assertEqual(entry['d6_head'], self.head)
        self.assertEqual(entry['pm_session_id'], task['pm_session_id'])
        self.assertEqual(self._gated(task), [
            ('gw-agent', 'DISPATCH', 'BLOCKED', 'OWNER_PAUSED'),
            ('gw-agent', 'START_RETRY', 'ALLOWED', 'OWNER_POLICY_ALLOWED'),
            ('gw-agent', 'DISPATCH', 'ALLOWED', 'OWNER_POLICY_ALLOWED')])
        restart_audit = [item for item in task['dispatch_audit']
                         if item['action'] == 'START_RETRY']
        self.assertEqual(restart_audit[0]['assignment_id'], new_developer['assignment_id'])
        # Not a model result and not a repair: no RETRY, RECOVERY, REPAIR or
        # REASSIGN, no repair claim, and the failed record is no longer the
        # Task's developer, the only field the run counts read.
        self.assertFalse({'RETRY', 'RECOVERY', 'REPAIR', 'REASSIGN'} &
                         {item['action'] for item in task['dispatch_audit']})
        self.assertFalse(task.get('review_repair_claims'))

    def test_restart_still_paused_is_refused_and_audited_then_allowed(self):
        target = 'gw-agent-restart-paused.md'
        task_id = self._active_pm_task(target)
        _code, failed = self._run(task_id, target, gate=_PAUSED, reach_model=False)

        with self.assertRaises(LLMToolError) as caught:
            self._run(task_id, target, gate=_PAUSED, reach_model=False)

        self.assertEqual(getattr(caught.exception, 'dispatch_code', None), 'OWNER_PAUSED')
        refused = self._reloaded()
        self.assertEqual(refused['developer'], failed['developer'])
        self.assertNotIn('start_failures', refused)
        self.assertEqual(self._gated(refused), [
            ('gw-agent', 'DISPATCH', 'BLOCKED', 'OWNER_PAUSED'),
            ('gw-agent', 'START_RETRY', 'BLOCKED', 'OWNER_PAUSED')])

        code, task = self._run(task_id, target)

        self.assertEqual(code, 0)
        self._assert_success_persisted(task, target)
        self.assertEqual(len(task['start_failures']), 1)

    def test_store_refusal_before_the_route_restarts_twice_then_goes_to_the_pm(self):
        target = 'gw-agent-restart-limit.md'
        task_id = self._active_pm_task(target)
        archived = []
        for attempt in range(3):
            code, task = self._run(task_id, target, reach_model=False,
                                   wrap_store=self._refused_dispatching_write())
            self.assertEqual(code, 1)
            self._assert_failed(task, 'DEVELOPER_UNAVAILABLE',
                                'DEVELOPER_DISPATCH_PERSISTENCE_REJECTED',
                                provider_type=None, model=None)
            self.assertEqual([item['failed_developer'] for item in
                              task.get('start_failures', [])], archived)
            archived.append(task['developer'])
        before = self._reloaded()

        with self.assertRaisesRegex(LLMToolError, 'START_RETRY_LIMIT_REACHED'):
            self._run(task_id, target, reach_model=False)

        after = self._reloaded()
        for field in ('developer', 'completion', 'start_failures', 'dispatch_audit'):
            self.assertEqual(after.get(field), before.get(field))
        self.assertEqual(
            [item[1:3] for item in self._gated(after)],
            [('START_RETRY', 'ALLOWED'), ('START_RETRY', 'ALLOWED')])

    def test_run_that_reached_the_model_is_still_a_duplicate(self):
        target = 'gw-agent-reached-model.md'
        task_id = self._active_pm_task(target)
        # The routing alias as the served model: the fake model answered, then
        # the result was refused after the model calls.
        code, failed = self._run(task_id, target, served_model='gw')
        self.assertEqual(code, 1)
        self._assert_failed(failed, 'DEVELOPER_UNAVAILABLE',
                            'DEVELOPER_GW_AGENT_MODEL_UNVERIFIED')
        verdict = start_failure.gw_agent_record_checks(failed)
        self.assertEqual((verdict.start_failure, verdict.failed_check), (False, 2))
        # It still counts as a run toward the D6 and chain repair caps.
        self.assertIsNotNone(task_submission_cli._repair_run_stamp(
            task_id, failed['developer'], lambda *_args, **_kwargs: False))
        self.server.script.clear()

        with self.assertRaisesRegex(LLMToolError,
                                    'developer assignment already exists; '
                                    'duplicate dispatch refused'):
            self._run(task_id, target, reach_model=False)

        after = self._reloaded()
        for field in ('developer', 'completion', 'dispatch_audit'):
            self.assertEqual(after.get(field), failed.get(field))
        self.assertNotIn('start_failures', after)

    def test_store_gate_refuses_a_start_retry_write_for_a_run_that_reached_the_model(self):
        target = 'gw-agent-reached-model-store.md'
        task_id = self._active_pm_task(target)
        _code, failed = self._run(task_id, target, served_model='gw')
        old = failed['developer']
        new = dict(old, assignment_id=task_store.new_id(),
                   session_id='dev-' + task_store.new_id(), state='DEVELOPER_PENDING',
                   provider_type=None, model=None, error_code=None, execution=None,
                   artifact=None, artifact_sha256=None, usage=None)
        entry = {'failed_developer': old, 'record_sha256': failed['record_sha256'],
                 'retry_assignment_id': new['assignment_id'], 'd6_head': self.head,
                 'retried_at': '2026-11-18T00:00:00Z', 'pm_session_id': failed['pm_session_id']}

        with patch.object(worker_dispatch, 'evaluate', return_value=_ALLOWED), \
             self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
            self.store.d6_bind_compare_and_update(
                task_id, executor_task_id='executor-d6',
                expected={'schema_version': 2, 'state': 'PM_RESPONDED', 'developer': old,
                          'completion': failed['completion']},
                d6_unchanged=lambda *_args, **_kwargs: True,
                developer=new, start_failures=[entry])

        self.assertEqual(caught.exception.dispatch_code, 'WORKER_START_RETRY_PRECONDITION')
        after = self._reloaded()
        self.assertEqual(after['developer'], old)
        self.assertNotIn('start_failures', after)
        self.assertEqual(self._gated(after)[-1],
                         ('gw-agent', 'START_RETRY', 'BLOCKED',
                          'WORKER_START_RETRY_PRECONDITION'))


# Run only this class's own tests here: the inherited real-store tests
# already run in test_gw_agent_route_wiring. unittest and pytest both skip a
# test name whose attribute is not callable.
for _name in dir(wiring.GwAgentRealStoreTests):
    if _name.startswith('test') and _name not in vars(GwAgentPreModelRestartTests):
        setattr(GwAgentPreModelRestartTests, _name, None)
del _name


if __name__ == '__main__':
    unittest.main()
