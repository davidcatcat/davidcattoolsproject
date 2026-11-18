"""#2310: a gw-agent delegate refused before any model call restarts on the same Task.

Every case runs ``task_submission_cli.run_task_delegate`` (the delegate path
``task delegate`` and review repair use) against a real temporary TaskStore,
a real registered D6 Git worktree and the fake gateway model server of
test_gw_agent_route_wiring. Only the Owner policy decision, the OS sandbox and
the adapter-boundary audit are injected, exactly as that module does. No
screen function is changed and no test string is credential-shaped.

#2310 F1: the cases that fail after the DISPATCHING write persisted run the
real adapter-boundary gate (worker_dispatch.authorize_dispatch) on the real
store with only its Owner policy decision injected, or inject a refusal of
building the route, so the record and its audit are what production writes.

#2310 F1 repair 2: the final dispatch refusal runs the real run_route and the
delegate's real route_gate, with only the Owner decision that gate reads
injected, after the real store and adapter gates allowed.
"""

import copy
import io
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from llmtool import start_failure, task_store, task_submission_cli, worker_dispatch
from llmtool.developer_routes import RouteError, load_route_config
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
# Bound at import, before any test patches the module attribute.
_REAL_AUTHORIZE_DISPATCH = worker_dispatch.authorize_dispatch


_REAL_RUN_ROUTE = task_submission_cli.run_route


def _real_adapter_gate(decision):
    """The real adapter-boundary gate on the real store; only its Owner decision is injected."""
    def authorize(store, task_id, worker_key, action='DISPATCH'):
        with patch.object(worker_dispatch, 'evaluate', return_value=decision):
            return _REAL_AUTHORIZE_DISPATCH(store, task_id, worker_key, action)
    return authorize


def _final_gate_decision(decision, launches, process_starts):
    """The real run_route, with only the Owner decision its final gate reads injected.

    The delegate's own route_gate runs unchanged inside the real run_route; it
    reads ``decision`` instead of the store and adapter gates' decision. The
    launch callback and the gateway process runner only record that they ran.
    """
    def run_route(route, request, *, on_launch, **kwargs):
        def launch():
            launches.append(True)
            return on_launch()

        def process_runner(*args, **_kwargs):
            process_starts.append(args)
            raise AssertionError('the gateway process must not start')

        with patch.object(worker_dispatch, 'evaluate', return_value=decision):
            return _REAL_RUN_ROUTE(route, request, runner=process_runner,
                                   on_launch=launch, **kwargs)
    return run_route


class GwAgentPreModelRestartTests(wiring.GwAgentRealStoreTests):
    """Reuses the real-store fixture (setUp, D6, PM Task, fake model server)."""

    def _run(self, task_id, target, *, gate=_ALLOWED, reach_model=True,
             served_model='qwen3.8-27b', wrap_store=None, authorize=None,
             route_factory=None, run_route=None):
        """One ``run_task_delegate`` call for ``task_id``; returns (code, reloaded Task).

        ``gate`` is the Owner decision of every policy read; ``authorize`` and
        ``run_route`` may wrap the real adapter gate and the real run_route to
        give its own read another decision.
        """
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
             patch.object(worker_dispatch, 'authorize_dispatch', authorize or Mock()), \
             patch.object(worker_dispatch, 'evaluate', return_value=gate), \
             patch.object(task_submission_cli, 'GwAgentRoute',
                          route_factory or task_submission_cli.GwAgentRoute), \
             patch.object(task_submission_cli, 'run_route', run_route or _REAL_RUN_ROUTE), \
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

    # #2310 F1: refusals after the DISPATCHING write persisted, before the route.

    def _assert_restarted(self, task_id, target, failed):
        """Delegate ``task_id`` again; it restarts on the same Task and D6 and succeeds."""
        failed_developer = failed['developer']
        self.assertEqual(failed_developer['provider_type'], 'gw-agent-cli')
        code, task = self._run(task_id, target)
        self.assertEqual(code, 0)
        self.assertEqual(task['task_id'], task_id)
        self._assert_success_persisted(task, target)
        self.assertEqual(task['completion']['executor_task_id'], 'executor-d6')
        new_developer = task['developer']
        self.assertNotEqual(new_developer['assignment_id'], failed_developer['assignment_id'])
        # The restart is recorded: the failed developer archived, identity and all.
        self.assertEqual(len(task['start_failures']), 1)
        entry = task['start_failures'][0]
        self.assertEqual(entry['failed_developer'], failed_developer)
        self.assertEqual(entry['retry_assignment_id'], new_developer['assignment_id'])
        self.assertEqual(entry['d6_head'], self.head)
        self.assertEqual(self._gated(task)[len(self._gated(failed)):], [
            ('gw-agent', 'START_RETRY', 'ALLOWED', 'OWNER_POLICY_ALLOWED'),
            ('gw-agent', 'DISPATCH', 'ALLOWED', 'OWNER_POLICY_ALLOWED')])
        # Not a model result and not a repair.
        self.assertFalse({'RETRY', 'RECOVERY', 'REPAIR', 'REASSIGN'} &
                         {item['action'] for item in task['dispatch_audit']})
        self.assertFalse(task.get('review_repair_claims'))
        return task

    def _assert_still_duplicate(self, task_id, target, failed):
        """Delegate ``task_id`` again; it is refused as a duplicate and nothing changes."""
        self.server.script.clear()
        with self.assertRaisesRegex(LLMToolError,
                                    'developer assignment already exists; '
                                    'duplicate dispatch refused'):
            self._run(task_id, target, reach_model=False)
        after = self._reloaded()
        for field in ('developer', 'completion', 'dispatch_audit'):
            self.assertEqual(after.get(field), failed.get(field))
        self.assertNotIn('start_failures', after)

    def test_adapter_gate_refusal_after_dispatching_restarts_on_the_same_task(self):
        target = 'gw-agent-restart-adapter.md'
        task_id = self._active_pm_task(target)
        # The store gate allows DISPATCHING; the real adapter gate then refuses.
        code, failed = self._run(task_id, target, reach_model=False,
                                 authorize=_real_adapter_gate(_PAUSED))
        self.assertEqual(code, 1)
        self._assert_failed(failed, 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_OWNER_PAUSED')
        self.assertIsNone(failed['developer']['execution'])
        self.assertEqual(self._gated(failed), [
            ('gw-agent', 'DISPATCH', 'ALLOWED', 'OWNER_POLICY_ALLOWED'),
            ('gw-agent', 'DISPATCH', 'BLOCKED', 'OWNER_PAUSED')])
        self.assertTrue(start_failure.gw_agent_record_checks(failed).start_failure)
        # Had the adapter gate authorized this assignment, the route could have
        # run: the same record with that entry is not a start failure.
        forged = copy.deepcopy(failed)
        refusal = forged['dispatch_audit'][-1]
        forged['dispatch_audit'].append(dict(
            refusal, attempt_id=task_store.new_id(), decision='ALLOWED',
            reason_code='OWNER_POLICY_ALLOWED'))
        verdict = start_failure.gw_agent_record_checks(forged)
        self.assertEqual((verdict.start_failure, verdict.failed_check), (False, 4))

        self._assert_restarted(task_id, target, failed)

    def test_route_config_refusal_after_adapter_authorization_restarts(self):
        target = 'gw-agent-restart-config.md'
        task_id = self._active_pm_task(target)
        # The real adapter gate authorizes; building the route is then refused.
        code, failed = self._run(
            task_id, target, reach_model=False, authorize=_real_adapter_gate(_ALLOWED),
            route_factory=Mock(side_effect=RouteError('GW_AGENT_CONFIG_INVALID')))
        self.assertEqual(code, 1)
        self._assert_failed(failed, 'DEVELOPER_UNAVAILABLE',
                            'DEVELOPER_GW_AGENT_CONFIG_INVALID')
        self.assertIsNone(failed['developer']['execution'])
        self.assertEqual(self._gated(failed), [
            ('gw-agent', 'DISPATCH', 'ALLOWED', 'OWNER_POLICY_ALLOWED'),
            ('gw-agent', 'DISPATCH', 'ALLOWED', 'OWNER_POLICY_ALLOWED')])
        self.assertEqual(['authorization_id' in item for item in failed['dispatch_audit']
                          if item['action'] == 'DISPATCH'], [False, True])
        self.assertTrue(start_failure.gw_agent_record_checks(failed).start_failure)

        self._assert_restarted(task_id, target, failed)

    def test_unexpected_route_build_failure_after_dispatching_restarts(self):
        target = 'gw-agent-restart-build.md'
        task_id = self._active_pm_task(target)
        code, failed = self._run(task_id, target, reach_model=False,
                                 route_factory=Mock(side_effect=TypeError('synthetic')))
        self.assertEqual(code, 1)
        self._assert_failed(failed, 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_DISPATCH_FAILED')
        self.assertTrue(start_failure.gw_agent_record_checks(failed).start_failure)

        self._assert_restarted(task_id, target, failed)

    def test_adapter_refusal_without_audit_evidence_is_still_a_duplicate(self):
        target = 'gw-agent-adapter-unaudited.md'
        task_id = self._active_pm_task(target)
        # A refusal code with no BLOCKED adapter entry behind it proves nothing.
        code, failed = self._run(task_id, target, reach_model=False, authorize=Mock(
            side_effect=worker_dispatch.WorkerDispatchDenied('OWNER_PAUSED')))
        self.assertEqual(code, 1)
        self._assert_failed(failed, 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_OWNER_PAUSED')
        verdict = start_failure.gw_agent_record_checks(failed)
        self.assertEqual((verdict.start_failure, verdict.failed_check), (False, 2))

        self._assert_still_duplicate(task_id, target, failed)

    def test_run_that_reached_the_model_after_the_real_adapter_gate_is_a_duplicate(self):
        target = 'gw-agent-reached-model-adapter.md'
        task_id = self._active_pm_task(target)
        code, failed = self._run(task_id, target, served_model='gw',
                                 authorize=_real_adapter_gate(_ALLOWED))
        self.assertEqual(code, 1)
        self._assert_failed(failed, 'DEVELOPER_UNAVAILABLE',
                            'DEVELOPER_GW_AGENT_MODEL_UNVERIFIED')
        self.assertEqual(self._gated(failed), [
            ('gw-agent', 'DISPATCH', 'ALLOWED', 'OWNER_POLICY_ALLOWED'),
            ('gw-agent', 'DISPATCH', 'ALLOWED', 'OWNER_POLICY_ALLOWED')])
        verdict = start_failure.gw_agent_record_checks(failed)
        self.assertEqual((verdict.start_failure, verdict.failed_check), (False, 2))
        # It still counts as a run toward the D6 and chain repair caps.
        self.assertIsNotNone(task_submission_cli._repair_run_stamp(
            task_id, failed['developer'], lambda *_args, **_kwargs: False))

        self._assert_still_duplicate(task_id, target, failed)

    # #2310 F1 repair 2: the final dispatch refusal inside run_route, after the
    # adapter authorization and before the provider launch.

    def test_final_route_gate_refusal_before_launch_restarts_on_the_same_task(self):
        target = 'gw-agent-restart-final-gate.md'
        task_id = self._active_pm_task(target)
        launches, process_starts = [], []
        # The store gate and the real adapter gate allow; only the real final
        # route_gate inside the real run_route reads a paused Owner policy.
        code, failed = self._run(
            task_id, target, authorize=_real_adapter_gate(_ALLOWED),
            run_route=_final_gate_decision(_PAUSED, launches, process_starts))
        self.assertEqual(code, 1)
        # No launch, no gateway process and no model call before the refusal:
        # both scripted model replies are still unserved.
        self.assertEqual(launches, [])
        self.assertEqual(process_starts, [])
        self.assertEqual(len(self.server.script), 2)
        self.server.script.clear()
        self._assert_failed(failed, 'DEVELOPER_UNAVAILABLE', 'DEVELOPER_DISPATCH_DENIED')
        self.assertIsNone(failed['developer']['execution'])
        self.assertEqual(self._gated(failed), [
            ('gw-agent', 'DISPATCH', 'ALLOWED', 'OWNER_POLICY_ALLOWED'),
            ('gw-agent', 'DISPATCH', 'ALLOWED', 'OWNER_POLICY_ALLOWED')])
        self.assertEqual(['authorization_id' in item for item in failed['dispatch_audit']
                          if item['action'] == 'DISPATCH'], [False, True])
        self.assertTrue(start_failure.gw_agent_record_checks(failed).start_failure)
        # The same refusal with process facts, or with a second adapter
        # authorization, is not a start failure.
        launched = copy.deepcopy(failed)
        launched['developer']['execution'] = {
            'status': 'UNKNOWN', 'native_session_id': None, 'process_exit_code': 1,
            'stdout_sha256': None, 'cleanup_status': 'CONFIRMED'}
        verdict = start_failure.gw_agent_record_checks(launched)
        self.assertEqual((verdict.start_failure, verdict.failed_check), (False, 4))
        twice = copy.deepcopy(failed)
        adapter_entry = [item for item in twice['dispatch_audit']
                         if item['action'] == 'DISPATCH' and 'authorization_id' in item][-1]
        twice['dispatch_audit'].append(dict(
            adapter_entry, attempt_id=task_store.new_id(),
            authorization_id=task_store.new_id()))
        verdict = start_failure.gw_agent_record_checks(twice)
        self.assertEqual((verdict.start_failure, verdict.failed_check), (False, 4))

        task = self._assert_restarted(task_id, target, failed)

        self.assertEqual(len([item for item in task['dispatch_audit']
                              if item['action'] == 'START_RETRY']), 1)
        self.assertEqual(task['start_failures'][0]['failed_developer']['error_code'],
                         'DEVELOPER_DISPATCH_DENIED')
        self.assertNotEqual(task['developer']['session_id'],
                            failed['developer']['session_id'])

    def test_final_route_gate_refusal_after_launch_is_still_a_duplicate(self):
        target = 'gw-agent-final-gate-after-launch.md'
        task_id = self._active_pm_task(target)

        def launched_then_denied(_route, _request, *, gate, on_launch, **_kwargs):
            # A gate denial after the launch callback ran: the delegate must
            # not store it as the pre-launch DEVELOPER_DISPATCH_DENIED.
            on_launch()
            with patch.object(worker_dispatch, 'evaluate', return_value=_PAUSED):
                self.assertIs(gate(), False)
            raise RouteError('GW_AGENT_DISPATCH_DENIED')

        code, failed = self._run(task_id, target, reach_model=False,
                                 authorize=_real_adapter_gate(_ALLOWED),
                                 run_route=launched_then_denied)
        self.assertEqual(code, 1)
        self._assert_failed(failed, 'DEVELOPER_UNAVAILABLE',
                            'DEVELOPER_GW_AGENT_DISPATCH_DENIED')
        verdict = start_failure.gw_agent_record_checks(failed)
        self.assertEqual((verdict.start_failure, verdict.failed_check), (False, 2))

        self._assert_still_duplicate(task_id, target, failed)

    def test_run_that_reached_the_model_after_the_final_gate_is_a_duplicate(self):
        target = 'gw-agent-final-gate-reached-model.md'
        task_id = self._active_pm_task(target)
        launches, process_starts = [], []

        def allowed_run_route(route, request, *, on_launch, **kwargs):
            def launch():
                launches.append(True)
                return on_launch()
            return _REAL_RUN_ROUTE(route, request, on_launch=launch, **kwargs)

        # The final gate allows, the fake model answers, and the result is then
        # refused after the model calls.
        code, failed = self._run(task_id, target, served_model='gw',
                                 authorize=_real_adapter_gate(_ALLOWED),
                                 run_route=allowed_run_route)
        self.assertEqual(code, 1)
        self.assertEqual(launches, [True])
        self._assert_failed(failed, 'DEVELOPER_UNAVAILABLE',
                            'DEVELOPER_GW_AGENT_MODEL_UNVERIFIED')
        verdict = start_failure.gw_agent_record_checks(failed)
        self.assertEqual((verdict.start_failure, verdict.failed_check), (False, 2))
        self.assertIsNotNone(task_submission_cli._repair_run_stamp(
            task_id, failed['developer'], lambda *_args, **_kwargs: False))

        self._assert_still_duplicate(task_id, target, failed)


# Run only this class's own tests here: the inherited real-store tests
# already run in test_gw_agent_route_wiring. unittest and pytest both skip a
# test name whose attribute is not callable.
for _name in dir(wiring.GwAgentRealStoreTests):
    if _name.startswith('test') and _name not in vars(GwAgentPreModelRestartTests):
        setattr(GwAgentPreModelRestartTests, _name, None)
del _name


if __name__ == '__main__':
    unittest.main()
