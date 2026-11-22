"""#2622 PR 2: AGY and OpenCode pause after consecutive route-level failures.

The TaskStore and its worker availability record are real. Provider outcomes are
scrubbed records at the Developer route boundary, the Owner policy is the shared
test fixture, and one synthetic Task record exercises the repair path. No
credential-shaped value appears here (Owner #763 A).
"""

from __future__ import annotations

import contextlib
import copy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, Path(__file__).resolve().parent):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from llmtool import cli  # noqa: E402
from llmtool import repair_policy  # noqa: E402
from llmtool import review_repair  # noqa: E402
from llmtool import worker_availability as wa  # noqa: E402
from llmtool import worker_dispatch  # noqa: E402
from llmtool.errors import LLMToolError  # noqa: E402
from llmtool.task_store import TaskStore  # noqa: E402
import worker_policy_fixture  # noqa: E402

TASK = '1' * 32
ASSIGNMENT = '2' * 32
HEAD = 'a' * 40
AGY = 'agy'
OPENCODE = 'opencode'
OPENCODE_WORKER = 'opencode-developer'
REPO = {'slug': 'davidcatcat/davidcattoolsproject', 'dispatch_issue': 289,
        'dispatch_evidence_pr': 1}


def _developer(profile, code=None, state='DEVELOPER_UNAVAILABLE'):
    return {
        'assignment_id': ASSIGNMENT,
        'provider_profile': profile,
        'state': state,
        'error_code': code,
        'usage': None,
        'artifact': None,
        'execution': None,
    }


def _record(profile, code=None, state='DEVELOPER_UNAVAILABLE'):
    return {'task_id': TASK, 'developer': _developer(profile, code, state)}


class _TaskView:
    """One synthetic Task record over the real worker availability store."""

    def __init__(self, store, task):
        self._store = store
        self._task = task

    def get(self, task_id):
        return copy.deepcopy(self._task)

    def __getattr__(self, name):
        return getattr(self._store, name)


@contextlib.contextmanager
def pause_after(value):
    """The policy read at the route-failure limit, without a real policy.json."""
    with mock.patch.object(repair_policy, 'load_policy',
                           return_value={'route_failure_pause_after': value}):
        yield


@contextlib.contextmanager
def unreadable_policy():
    with mock.patch.object(repair_policy, 'load_policy',
                           side_effect=LLMToolError('POLICY_INVALID')):
        yield


class RouteFailurePauseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TaskStore(Path(self.temp.name).resolve() / 'tasks')
        self.policy_path = worker_policy_fixture.install(self)
        patcher = mock.patch.object(worker_dispatch, 'repository_config', return_value=REPO)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)

    def at(self, index):
        return self.now + timedelta(seconds=index)

    def observe(self, store, profile, code=None, state='DEVELOPER_UNAVAILABLE', index=0):
        return wa.observe(store, _record(profile, code, state), now=self.at(index))

    def new_store(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return TaskStore(Path(temp.name).resolve() / 'tasks')

    def quota(self, store, worker):
        return store.get_worker_observations()[worker][wa.QUOTA_LAYER]

    def test_agy_pauses_after_three_consecutive_route_failures(self):
        with pause_after(3):
            for index in range(2):
                self.observe(self.store, AGY, 'AGY_PROVIDER_FAILED', index=index)
            self.assertIsNone(wa.route_pause_refusal(self.store, AGY))
            self.observe(self.store, AGY, 'AGY_RUNTIME_FAILED', index=2)
            self.assertEqual(wa.route_pause_refusal(self.store, AGY),
                             wa.ROUTE_PAUSE_REFUSAL_CODE)
        with self.assertRaises(LLMToolError) as caught:
            wa.fresh_quota_fail(self.store, AGY)
        self.assertIn(wa.ROUTE_PAUSE_REFUSAL_CODE, str(caught.exception))
        self.assertIn('worker-route-resume agy', str(caught.exception))

    def test_opencode_pauses_after_three_consecutive_route_failures(self):
        with pause_after(3):
            for index in range(3):
                self.observe(self.store, OPENCODE, 'OPENCODE_PROVIDER_FAILED', index=index)
        self.assertEqual(wa.route_pause_refusal(self.store, OPENCODE_WORKER),
                         wa.ROUTE_PAUSE_REFUSAL_CODE)
        self.assertEqual(wa.quota_refusal(self.store, OPENCODE_WORKER),
                         wa.ROUTE_PAUSE_REFUSAL_CODE)

    def test_a_result_before_the_limit_resets_the_streak(self):
        with pause_after(3):
            self.observe(self.store, AGY, 'AGY_EXIT_NONZERO', index=0)
            self.observe(self.store, AGY, 'AGY_CONFIG_INVALID', index=1)
            self.assertEqual(
                self.observe(self.store, AGY, None, state='DEVELOPER_RESPONDED', index=2),
                'RESET')
            self.assertIsNone(wa.route_pause_refusal(self.store, AGY))
            self.observe(self.store, AGY, 'AGY_EXIT_NONZERO', index=3)
            self.observe(self.store, AGY, 'AGY_CONFIG_INVALID', index=4)
            self.assertIsNone(wa.route_pause_refusal(self.store, AGY))
            self.observe(self.store, AGY, 'AGY_PROVIDER_FAILED', index=5)
            self.assertEqual(wa.route_pause_refusal(self.store, AGY),
                             wa.ROUTE_PAUSE_REFUSAL_CODE)

    def test_a_result_after_a_pause_does_not_clear_it(self):
        with pause_after(2):
            self.observe(self.store, OPENCODE, 'OPENCODE_START_FAILED', index=0)
            self.observe(self.store, OPENCODE, 'OPENCODE_LOCAL_CONFIG_INVALID', index=1)
        self.assertEqual(wa.route_pause_refusal(self.store, OPENCODE_WORKER),
                         wa.ROUTE_PAUSE_REFUSAL_CODE)
        self.observe(self.store, OPENCODE, None, state='DEVELOPER_RESPONDED', index=2)
        self.assertEqual(wa.route_pause_refusal(self.store, OPENCODE_WORKER),
                         wa.ROUTE_PAUSE_REFUSAL_CODE)

    def test_task_level_and_quota_outcomes_never_count(self):
        cases = (
            (OPENCODE, 'OPENCODE_NO_CHANGES'),
            (OPENCODE, 'OPENCODE_STEPS_EXHAUSTED'),
            (OPENCODE, 'OPENCODE_SPEC_MISMATCH'),
            (OPENCODE, 'OPENCODE_EMPTY_RESULT'),
            (OPENCODE, 'OPENCODE_OUTPUT_REJECTED'),
            (OPENCODE, 'OPENCODE_ENVELOPE_INVALID'),
            (OPENCODE, 'OPENCODE_TIMEOUT'),
            (OPENCODE, 'OPENCODE_D6_SESSION_REFUSED'),
            (OPENCODE, 'OPENCODE_WRITE_SCOPE_VIOLATION'),
            (OPENCODE, 'OPENCODE_FREE_LIMIT'),
            (OPENCODE, 'OPENCODE_RATE_LIMITED'),
            (AGY, 'AGY_QUOTA_EXHAUSTED'),
            (AGY, 'AGY_TEST_FAILED'),
        )
        with pause_after(2):
            for index, (profile, code) in enumerate(cases):
                with self.subTest(code=code):
                    self.assertFalse(wa.route_failure(_record(profile, code)))
                    self.observe(self.store, profile, code, state='BLOCKED', index=index)
                    self.assertIsNone(wa.route_pause_refusal(
                        self.store, OPENCODE_WORKER if profile == OPENCODE else AGY))

    def test_a_configured_limit_is_honoured(self):
        store_two = self.new_store()
        with pause_after(2):
            self.observe(store_two, AGY, 'AGY_EXIT_NONZERO', index=0)
            self.assertIsNone(wa.route_pause_refusal(store_two, AGY))
            self.observe(store_two, AGY, 'AGY_EXIT_NONZERO', index=1)
            self.assertEqual(wa.route_pause_refusal(store_two, AGY),
                             wa.ROUTE_PAUSE_REFUSAL_CODE)

        store_four = self.new_store()
        with pause_after(4):
            for index in range(3):
                self.observe(store_four, AGY, 'AGY_EXIT_NONZERO', index=index)
            self.assertIsNone(wa.route_pause_refusal(store_four, AGY))
            self.observe(store_four, AGY, 'AGY_EXIT_NONZERO', index=3)
            self.assertEqual(wa.route_pause_refusal(store_four, AGY),
                             wa.ROUTE_PAUSE_REFUSAL_CODE)

    def test_resume_clears_the_pause_and_restarts_the_streak(self):
        with pause_after(2):
            self.observe(self.store, AGY, 'AGY_EXIT_NONZERO', index=0)
            self.observe(self.store, AGY, 'AGY_EXIT_NONZERO', index=1)
        result = wa.resume_route(self.store, AGY, now=self.at(2))
        self.assertTrue(result['was_paused'])
        self.assertFalse(result['paused'])
        self.assertEqual(result['worker'], AGY)
        self.assertIsNone(wa.route_pause_refusal(self.store, AGY))
        self.assertFalse(wa.fresh_quota_fail(self.store, AGY))
        with pause_after(2):
            self.observe(self.store, AGY, 'AGY_EXIT_NONZERO', index=3)
            self.assertIsNone(wa.route_pause_refusal(self.store, AGY))
            self.observe(self.store, AGY, 'AGY_EXIT_NONZERO', index=4)
            self.assertEqual(wa.route_pause_refusal(self.store, AGY),
                             wa.ROUTE_PAUSE_REFUSAL_CODE)

    def test_the_cli_resume_command_clears_an_opencode_pause(self):
        with pause_after(2):
            self.observe(self.store, OPENCODE, 'OPENCODE_PROVIDER_FAILED', index=0)
            self.observe(self.store, OPENCODE, 'OPENCODE_PROVIDER_FAILED', index=1)
        args = cli.parser_for_cli().parse_args(['worker-route-resume', 'opencode'])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.run_worker_route_resume(args, store=self.store)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['worker'], OPENCODE_WORKER)
        self.assertIsNone(wa.route_pause_refusal(self.store, OPENCODE_WORKER))

    def test_resume_rejects_an_unmanaged_worker(self):
        for worker in ('claude-developer', 'gw-agent', 'unknown', ''):
            with self.subTest(worker=worker):
                with self.assertRaises(LLMToolError) as caught:
                    wa.resume_route(self.store, worker, now=self.at(0))
                self.assertEqual(str(caught.exception),
                                 'WORKER_ROUTE_RESUME_WORKER_INVALID')

    def test_the_repair_path_refuses_a_paused_route_before_any_write(self):
        with pause_after(2):
            self.observe(self.store, OPENCODE, 'OPENCODE_EXIT_NONZERO', index=0)
            self.observe(self.store, OPENCODE, 'OPENCODE_EXIT_NONZERO', index=1)
        task = {
            'task_id': TASK,
            'developer': {'state': 'DEVELOPER_RESPONDED',
                          'provider_profile': OPENCODE,
                          'owner_assignment_ref': None},
            'completion': {'executor_task_id': 'd6-task'},
        }
        with mock.patch.object(review_repair, '_failed_event',
                               return_value=({'failed_head': HEAD}, None)), \
                mock.patch.object(review_repair, '_current_d6_head', return_value=HEAD), \
                mock.patch.object(review_repair, 'dispute_open_on', return_value=False), \
                mock.patch('llmtool.direct_reassignment.authoring_route',
                           side_effect=lambda store, task, developer, executor: developer):
            with self.assertRaises(LLMToolError) as caught:
                review_repair.repair_review(
                    store=_TaskView(self.store, task), task_id=TASK,
                    executor_task='d6-task', pr_number=1)
        self.assertEqual(caught.exception.review_repair_code,
                         wa.ROUTE_PAUSE_REFUSAL_CODE)
        self.assertIn('worker-route-resume opencode-developer',
                      str(caught.exception))

    def test_quota_behaviour_is_unchanged(self):
        with pause_after(2):
            self.observe(self.store, OPENCODE, 'OPENCODE_FREE_LIMIT', state='BLOCKED')
            self.assertEqual(self.quota(self.store, OPENCODE_WORKER)['status'], 'FAIL')
            self.assertIsNone(wa.route_pause_refusal(self.store, OPENCODE_WORKER))
            self.assertTrue(wa.fresh_quota_fail(self.store, OPENCODE_WORKER))
            self.assertEqual(wa.quota_refusal(self.store, OPENCODE_WORKER),
                             wa.REFUSAL_CODE)
            self.observe(self.store, OPENCODE, 'OPENCODE_RATE_LIMITED',
                         state='BLOCKED', index=1)
            self.assertEqual(self.quota(self.store, OPENCODE_WORKER)['status'], 'FAIL')
            self.assertIsNone(wa.route_pause_refusal(self.store, OPENCODE_WORKER))

    def test_an_unreadable_policy_neither_unpauses_nor_blocks_every_route(self):
        with pause_after(2):
            self.observe(self.store, AGY, 'AGY_EXIT_NONZERO', index=0)
            self.observe(self.store, AGY, 'AGY_EXIT_NONZERO', index=1)
        with unreadable_policy():
            # The stored pause survives a policy that cannot be read.
            self.assertEqual(wa.route_pause_refusal(self.store, AGY),
                             wa.ROUTE_PAUSE_REFUSAL_CODE)
            with self.assertRaises(LLMToolError) as caught:
                wa.fresh_quota_fail(self.store, AGY)
            self.assertIn('worker-route-resume agy', str(caught.exception))
            # The explicit resume still works; it does not read the policy.
            wa.resume_route(self.store, AGY, now=self.at(2))
            self.assertIsNone(wa.route_pause_refusal(self.store, AGY))

            # A route with no stored pause is not permanently blocked.
            other = self.new_store()
            for index in range(5):
                self.observe(other, OPENCODE, 'OPENCODE_PROVIDER_FAILED', index=index)
            self.assertIsNone(wa.route_pause_refusal(other, OPENCODE_WORKER))
            self.assertFalse(wa.fresh_quota_fail(other, OPENCODE_WORKER))


if __name__ == '__main__':
    unittest.main()
