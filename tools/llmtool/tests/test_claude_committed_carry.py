"""#1148 / #1279: a finished Claude run with no new edit carries the D6's committed head."""

import contextlib
import unittest
from unittest.mock import patch

from llmtool import task_advance
from llmtool import worker_dispatch
from llmtool.developer_routes import RouteError
from llmtool.errors import LLMToolError

import test_claude_route_wiring as wiring

BASE, HEAD, OTHER = '1' * 40, 'd' * 40, '3' * 40
RAW = b'diff --git a/src.py b/src.py\n--- a/src.py\n+++ b/src.py\n@@ -1 +1 @@\n-a\n+b\n'


def setUpModule():
    wiring.setUpModule()


def tearDownModule():
    wiring.tearDownModule()


class ClaudeCommittedCarryTests(unittest.TestCase):
    def run_case(self, *, committed=('src.py',), result_sha=HEAD, base=BASE, status='DONE',
                 runner_error=None, ancestor=True, others=(), store_error=None):
        cli, store, args, registration, _evals, _prompts, _requests, patches, runner = (
            wiring.ClaudeRouteWiringTests._setup(self, runner_error=runner_error))
        registration.source_sha, registration.result_sha = base, result_sha
        patches = [item for item in patches if item.attribute not in
                   ('_worktree_diff', '_direct_result_callback')]

        def worktree_diff(_root, source):
            return ([], b'') if source == HEAD else (list(committed), RAW)

        def git(_root, command, **_kwargs):
            if not ancestor and command[0] == 'merge-base':
                raise LLMToolError('not an ancestor')
            return b''

        def finished_runner(*call_args, **kwargs):
            result = runner(*call_args, **kwargs)
            result.status = status
            return result

        task_id = store.task['task_id']
        records = {'0' * 32: record for record in others}
        records_get = store.get
        store.task_ids = lambda: [task_id, *records]
        store.get = lambda requested: records.get(requested) or records_get(requested)
        if store_error is not None:
            accept = store.compare_and_update

            def refuse(requested, *, expected, **updates):
                if updates.get('developer', {}).get('state') == 'DEVELOPER_RESPONDED':
                    raise store_error
                return accept(requested, expected=expected, **updates)
            store.compare_and_update = refuse
        patches += [
            patch.object(cli.task_completion, '_worktree_diff', side_effect=worktree_diff),
            patch.object(cli.task_completion, '_git', side_effect=git),
            patch.object(cli.task_completion, '_committed_changed_paths',
                         return_value=sorted(committed)),
        ]
        direct_event = next(item for item in patches
                            if item.attribute == 'run_task_direct_result_event')
        with contextlib.ExitStack() as stack:
            entered = {id(item): stack.enter_context(item) for item in patches}
            status_code = cli.run_task_claude_delegate(args, store=store,
                                                       route_runner=finished_runner)
        return status_code, store.task, entered[id(direct_event)]

    def assert_no_carry(self, outcome, code='CLAUDE_NO_CHANGES'):
        status_code, task, direct_event = outcome
        developer = task['developer']
        self.assertEqual(status_code, 1)
        self.assertEqual((developer['state'], developer['error_code']), ('BLOCKED', code))
        self.assertEqual(developer['source_sha'], HEAD)
        self.assertIsNone(developer['artifact'])
        direct_event.assert_not_called()

    def test_1148_committed_result_is_carried_and_sent_to_the_result_event(self):
        status_code, task, direct_event = self.run_case()
        developer = task['developer']
        self.assertEqual(status_code, 0)
        self.assertEqual((developer['state'], developer['source_sha'], developer['error_code']),
                         ('DEVELOPER_RESPONDED', BASE, None))
        self.assertEqual(developer['artifact'], RAW.decode())
        self.assertEqual((developer['execution']['status'],
                          developer['execution']['process_exit_code']), ('DONE', 0))
        evidence = developer['direct_result_callback']['evidence']
        self.assertEqual((evidence['source_sha'], evidence['result_head']), (BASE, HEAD))
        self.assertEqual(evidence['carried_result'], {
            'mode': 'NO_RUN_CHANGES', 'assignment_id': developer['assignment_id'],
            'd6_base': BASE, 'carried_head': HEAD})
        self.assertEqual((task['completion']['evidence']['source_sha'],
                          task['completion']['evidence']['result_head']), (BASE, HEAD))
        direct_event.assert_called_once_with(task['task_id'], 'executor-d6', store=unittest.mock.ANY)
        self.assertEqual(task_advance.derive_repair_state(task), 'RESPONDED')

    def test_1279_verify_only_run_after_timeouts_carries_the_committed_head(self):
        timed_out = {'developer': {'executor_task_id': 'executor-d6', 'state': 'BLOCKED',
                                   'error_code': 'CLAUDE_TIMEOUT'}}
        status_code, task, _direct_event = self.run_case(others=(timed_out,))
        self.assertEqual((status_code, task['developer']['state']), (0, 'DEVELOPER_RESPONDED'))

    def test_a_d6_head_that_is_not_the_launch_head_carries_nothing(self):
        outcome = self.run_case(result_sha=OTHER)
        self.assert_no_carry(outcome)
        self.assertEqual(task_advance.derive_repair_state(outcome[1]), 'BLOCKED')

    def test_an_out_of_scope_commit_carries_nothing(self):
        self.assert_no_carry(self.run_case(committed=('src.py', 'outside.py')))

    def test_a_commit_that_is_not_on_the_d6_base_carries_nothing(self):
        self.assert_no_carry(self.run_case(ancestor=False))

    def test_a_d6_without_committed_work_carries_nothing(self):
        self.assert_no_carry(self.run_case(base=HEAD))

    def test_an_unverified_timeout_or_refusal_carries_nothing(self):
        for code in ('CLAUDE_TIMEOUT', 'CLAUDE_STATUS_FAILED'):
            with self.subTest(code=code):
                status_code, task, direct_event = self.run_case(runner_error=RouteError(code))
                self.assertEqual((status_code, task['developer']['error_code']), (1, code))
                self.assertEqual(task['developer']['source_sha'], HEAD)
                direct_event.assert_not_called()
        self.assert_no_carry(self.run_case(status='error'))

    def test_a_d6_with_another_developer_result_carries_nothing(self):
        delivered = {'developer': {'executor_task_id': 'executor-d6',
                                   'state': 'DEVELOPER_RESPONDED'}}
        self.assert_no_carry(self.run_case(others=(delivered,)))

    def test_a_refused_store_write_keeps_the_launch_source(self):
        self.assert_no_carry(self.run_case(
            store_error=worker_dispatch.WorkerDispatchDenied('WORKER_REASSIGN_PRECONDITION')))



if __name__ == '__main__':
    unittest.main()
