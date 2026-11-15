"""Tests for RouteError execution facts, failure execution, and no-work proof."""

import contextlib
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from llmtool.developer_routes.base import (
    RouteError, capture_snapshot, run_route,
)
from llmtool.task_submission_cli import (
    _launched_run_did_no_work, _route_failure_execution,
)

try:
    from test_developer_route_contract import FakeBoundary, FakeRoute
except ImportError:
    from tests.test_developer_route_contract import FakeBoundary, FakeRoute

try:
    import test_claude_route_wiring
except ImportError:
    from tests import test_claude_route_wiring

try:
    import test_agy_route_wiring
except ImportError:
    from tests import test_agy_route_wiring

try:
    import review_trust_fixture
    import worker_policy_fixture
except ImportError:
    from tests import review_trust_fixture
    from tests import worker_policy_fixture


def setUpModule():
    review_trust_fixture.start_module()
    worker_policy_fixture.start_module()


def tearDownModule():
    worker_policy_fixture.stop_module()
    review_trust_fixture.stop_module()


def _real_git_request(root):
    git = shutil.which('git') or '/usr/bin/git'
    subprocess.run([git, 'init', str(root)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run([git, '-C', str(root), 'config', 'user.name', 'Test'], check=True)
    subprocess.run([git, '-C', str(root), 'config', 'user.email', 'test@example.invalid'], check=True)
    (root / 'out.txt').write_text('before\n')
    subprocess.run([git, '-C', str(root), 'add', 'out.txt'], check=True)
    subprocess.run([git, '-C', str(root), 'commit', '-m', 'initial'], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    branch = subprocess.check_output([git, '-C', str(root), 'branch', '--show-current'],
                                     text=True).strip()
    head = subprocess.check_output([git, '-C', str(root), 'rev-parse', 'HEAD'],
                                   text=True).strip()
    return {
        'root': str(root),
        'model': 'fake-model',
        'effort': 'low',
        'expected_branch': branch,
        'expected_head': head,
        'write_paths': ['out.txt'],
        'executable': '/fake/cli',
        'log_path': '/nonexistent/fake-log',
    }


class StatusFailedRoute(FakeRoute):
    def validate_status(self, parsed):
        raise RouteError('FAKE_STATUS_FAILED')


class RouteFailureExecutionTests(unittest.TestCase):
    def test_run_route_execution_facts(self):
        route = StatusFailedRoute({})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            root.mkdir()
            request = _real_git_request(root)

            # Runner with last_cleanup_status='CONFIRMED'
            def runner_confirmed(*args):
                return 1, b'out', b''

            runner_confirmed.last_cleanup_status = 'CONFIRMED'

            with self.assertRaises(RouteError) as caught:
                run_route(route, request, gate=lambda: True, runner=runner_confirmed,
                          boundary=FakeBoundary(), snapshot=capture_snapshot)
            self.assertEqual(caught.exception.code, 'FAKE_STATUS_FAILED')
            facts = caught.exception.execution_facts
            self.assertIsInstance(facts, dict)
            self.assertEqual(facts['process_exit_code'], 1)
            self.assertEqual(facts['stdout_sha256'], hashlib.sha256(b'out').hexdigest())
            self.assertIsInstance(facts['elapsed_ms'], int)
            self.assertGreaterEqual(facts['elapsed_ms'], 0)
            self.assertEqual(facts['cleanup_status'], 'CONFIRMED')

            # Runner without last_cleanup_status attribute -> UNKNOWN
            def runner_no_attr(*args):
                return 1, b'out', b''

            with self.assertRaises(RouteError) as caught:
                run_route(route, request, gate=lambda: True, runner=runner_no_attr,
                          boundary=FakeBoundary(), snapshot=capture_snapshot)
            self.assertEqual(caught.exception.code, 'FAKE_STATUS_FAILED')
            facts = caught.exception.execution_facts
            self.assertIsInstance(facts, dict)
            self.assertEqual(facts['cleanup_status'], 'UNKNOWN')

            # Gate returning False (refused before launch) -> execution_facts is None
            with self.assertRaises(RouteError) as caught:
                run_route(route, request, gate=lambda: False, runner=runner_confirmed,
                          boundary=FakeBoundary(), snapshot=capture_snapshot)
            self.assertIsNone(caught.exception.execution_facts)

    def test_route_failure_execution_extraction(self):
        valid_facts = {
            'process_exit_code': 1,
            'stdout_sha256': 'a' * 64,
            'elapsed_ms': 50,
            'cleanup_status': 'CONFIRMED',
        }
        exc = RouteError('SAMPLE_CODE')
        exc.execution_facts = valid_facts

        # Valid facts with condition None -> full BLOCKED execution with facts
        execution = _route_failure_execution(exc, None)
        expected = {
            'status': 'BLOCKED',
            'native_session_id': None,
            'process_exit_code': 1,
            'stdout_sha256': 'a' * 64,
            'elapsed_ms': 50,
            'cleanup_status': 'CONFIRMED',
            'usage_source': 'UNKNOWN',
            'source_bytes': None,
            'source_file_count': None,
            'source_manifest_sha256': None,
        }
        self.assertEqual(execution, expected)

        # Valid facts with a condition -> provider_evidence kept and facts overlaid
        condition = {
            'status': 'BLOCKED',
            'native_session_id': None,
            'process_exit_code': None,
            'stdout_sha256': None,
            'elapsed_ms': None,
            'cleanup_status': 'UNKNOWN',
            'usage_source': 'UNKNOWN',
            'source_bytes': None,
            'source_file_count': None,
            'source_manifest_sha256': None,
            'provider_evidence': {'condition': 'QUOTA_EXHAUSTED'},
        }
        execution_cond = _route_failure_execution(exc, condition)
        expected_cond = dict(expected, provider_evidence={'condition': 'QUOTA_EXHAUSTED'})
        self.assertEqual(execution_cond, expected_cond)

        # Malformed facts -> returns condition unchanged (None when None)
        malformed_cases = [
            # Extra key
            dict(valid_facts, extra=True),
            # Cleanup 'FAILED'
            dict(valid_facts, cleanup_status='FAILED'),
            # Elapsed -1
            dict(valid_facts, elapsed_ms=-1),
            # Short sha
            dict(valid_facts, stdout_sha256='abc'),
        ]
        for malformed in malformed_cases:
            with self.subTest(malformed=malformed):
                bad_exc = RouteError('SAMPLE_CODE')
                bad_exc.execution_facts = malformed
                self.assertIsNone(_route_failure_execution(bad_exc, None))
                self.assertEqual(_route_failure_execution(bad_exc, condition), condition)

    def test_no_work_proof(self):
        exc = RouteError('CLAUDE_STATUS_FAILED')
        exc.execution_facts = {
            'process_exit_code': 1,
            'stdout_sha256': 'a' * 64,
            'elapsed_ms': 42,
            'cleanup_status': 'CONFIRMED',
        }
        execution = _route_failure_execution(exc, None)
        dev = {
            'state': 'BLOCKED',
            'error_code': 'CLAUDE_STATUS_FAILED',
            'artifact': None,
            'artifact_sha256': None,
            'source_sha': 'c' * 40,
            'execution': execution,
        }
        self.assertTrue(_launched_run_did_no_work(dev, lambda sha: True))

        # With cleanup UNKNOWN -> False
        exc_unknown = RouteError('CLAUDE_STATUS_FAILED')
        exc_unknown.execution_facts = dict(exc.execution_facts, cleanup_status='UNKNOWN')
        dev_unknown = dict(dev, execution=_route_failure_execution(exc_unknown, None))
        self.assertFalse(_launched_run_did_no_work(dev_unknown, lambda sha: True))

        # With d6_unchanged returning False -> False
        self.assertFalse(_launched_run_did_no_work(dev, lambda sha: False))

    def test_claude_route_wiring(self):
        case = test_claude_route_wiring.ClaudeRouteWiringTests(
            methodName='test_route_failure_records_no_delivery')
        e_with_facts = RouteError('CLAUDE_STATUS_FAILED')
        e_with_facts.execution_facts = {
            'process_exit_code': 1,
            'stdout_sha256': 'a' * 64,
            'elapsed_ms': 42,
            'cleanup_status': 'CONFIRMED',
        }
        cli, store, args, _reg, _evals, _prompts, _reqs, patches, runner = case._setup(
            runner_error=e_with_facts)
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            status = cli.run_task_claude_delegate(args, store=store, route_runner=runner)
        self.assertEqual(status, 1)
        stored_dev = store.get(args.task_id)['developer']
        self.assertIsNotNone(stored_dev.get('execution'))
        self.assertEqual(stored_dev['execution']['cleanup_status'], 'CONFIRMED')
        self.assertEqual(stored_dev['execution']['status'], 'BLOCKED')

        # Without facts -> execution stays None
        e_without_facts = RouteError('CLAUDE_STATUS_FAILED')
        cli, store, args, _reg, _evals, _prompts, _reqs, patches, runner = case._setup(
            runner_error=e_without_facts)
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            status = cli.run_task_claude_delegate(args, store=store, route_runner=runner)
        self.assertEqual(status, 1)
        stored_dev = store.get(args.task_id)['developer']
        self.assertIsNone(stored_dev.get('execution'))

    def test_agy_route_wiring(self):
        e_with_facts = RouteError('AGY_STATUS_FAILED')
        e_with_facts.execution_facts = {
            'process_exit_code': 1,
            'stdout_sha256': 'a' * 64,
            'elapsed_ms': 42,
            'cleanup_status': 'CONFIRMED',
        }
        if hasattr(test_agy_route_wiring, '_run_agy_delegate'):
            run_agy = test_agy_route_wiring._run_agy_delegate
        else:
            agy_case = test_agy_route_wiring.AgyRouteWiringTests(
                methodName='test_record_pm_accepts_agy_recommendation')
            run_agy = agy_case._run_agy_delegate

        status, task, _events, _prompts = run_agy(route_error=e_with_facts)
        self.assertEqual(status, 1)
        stored_dev = task['developer']
        self.assertIsNotNone(stored_dev.get('execution'))
        self.assertEqual(stored_dev['execution']['cleanup_status'], 'CONFIRMED')
        self.assertEqual(stored_dev['execution']['status'], 'BLOCKED')

        # Without facts -> execution stays None
        e_without_facts = RouteError('AGY_STATUS_FAILED')
        status, task, _events, _prompts = run_agy(route_error=e_without_facts)
        self.assertEqual(status, 1)
        stored_dev = task['developer']
        self.assertIsNone(stored_dev.get('execution'))
