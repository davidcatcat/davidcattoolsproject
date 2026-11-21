"""Tests for developer_routes role and remote test timeouts."""

import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from llmtool import developer_routes, remote_test, review_dispatch
from llmtool.developer_routes import (
    DEFAULT_REMOTE_TEST_TIMEOUT_SECONDS,
    DEFAULT_ROLE_TIMEOUT_SECONDS,
    REGISTRY_ROLES,
    RouteError,
    load_route_config,
    remote_test_timeout,
    role_timeout,
)
from llmtool.errors import LLMToolError

try:
    from tests import test_guidance_drift_check
except ImportError:
    import test_guidance_drift_check
guidance_drift_check = test_guidance_drift_check.guidance_drift_check

ROUTES_FILE = Path(__file__).resolve().parents[1] / 'developer-routes.json'


class RoleTimeoutLoaderTests(unittest.TestCase):
    """Loader and getter tests for role and remote test timeouts."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'developer-routes.json'
        self.shipped = json.loads(ROUTES_FILE.read_text(encoding='utf-8'))

    def load(self, data):
        self.path.write_text(json.dumps(data), encoding='utf-8')
        return load_route_config(self.path)

    def test_shipped_config_loads_unchanged_and_defaults_apply(self):
        config = self.load(self.shipped)
        self.assertIn('schema_version', config)
        self.assertIn('routes', config)
        self.assertNotIn('remote_test_timeout', config)
        for role in ('codex-reviewer', 'claude-reviewer', 'codex-debug', 'claude-debug'):
            self.assertNotIn('timeout', config['roles'][role])
            # #2281 (Owner 2026-11-19): Reviewer and AI Debug default to 3 hours.
            self.assertEqual(role_timeout(role, self.path), 10800)
            self.assertEqual(role_timeout(role), 10800)
        self.assertEqual(DEFAULT_ROLE_TIMEOUT_SECONDS, 10800)
        # The EC2 remote test timeout is not an AI run and is unchanged.
        self.assertEqual(DEFAULT_REMOTE_TEST_TIMEOUT_SECONDS, 1800)
        self.assertEqual(remote_test_timeout(self.path), 1800)
        self.assertEqual(remote_test_timeout(), 1800)

    def test_valid_timeouts_accepted_and_returned(self):
        data = copy.deepcopy(self.shipped)
        data['roles']['codex-reviewer']['timeout'] = 1
        self.load(data)
        self.assertEqual(role_timeout('codex-reviewer', self.path), 1)

        for value in (3600, 10800, 18000):
            with self.subTest(role_timeout=value):
                data = copy.deepcopy(self.shipped)
                data['roles']['codex-reviewer']['timeout'] = value
                self.load(data)
                self.assertEqual(role_timeout('codex-reviewer', self.path), value)

        data = copy.deepcopy(self.shipped)
        data['remote_test_timeout'] = 3600
        loaded = self.load(data)
        self.assertEqual(loaded['remote_test_timeout'], 3600)
        self.assertEqual(remote_test_timeout(self.path), 3600)

    def test_invalid_timeouts_and_keys_refused(self):
        def mutate_role_timeout(val):
            def _mutate(data):
                data['roles']['codex-reviewer']['timeout'] = val
            return _mutate

        def mutate_remote_test_timeout(val):
            def _mutate(data):
                data['remote_test_timeout'] = val
            return _mutate

        cases = [
            ('role timeout 0', mutate_role_timeout(0)),
            ('role timeout 18001', mutate_role_timeout(18001)),
            ('role timeout True', mutate_role_timeout(True)),
            ("role timeout '600'", mutate_role_timeout('600')),
            ('role timeout 1.5', mutate_role_timeout(1.5)),
            ('remote_test_timeout 0', mutate_remote_test_timeout(0)),
            # The remote test ceiling stays 3600: it is not an AI run (#2281).
            ('remote_test_timeout 3601', mutate_remote_test_timeout(3601)),
            ('remote_test_timeout 18000', mutate_remote_test_timeout(18000)),
            ('remote_test_timeout False', mutate_remote_test_timeout(False)),
            ('unknown top-level key', lambda data: data.update(unexpected_key=1800)),
        ]
        for name, mutate in cases:
            with self.subTest(case=name):
                data = copy.deepcopy(self.shipped)
                mutate(data)
                with self.assertRaises(RouteError) as caught:
                    self.load(data)
                self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')

        with self.subTest(case="role_timeout('reviewer')"):
            with self.assertRaises(RouteError) as caught:
                role_timeout('reviewer', self.path)
            self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')


class ReviewDispatchTimeoutTests(unittest.TestCase):
    """Review dispatch timeout behavior."""

    def test_codex_dispatch_uses_role_timeout(self):
        task = {
            'task_id': '0' * 32,
            'run_id': '1' * 32,
            'task_binding': {'contract_digest': '2' * 64},
        }
        recorded_timeout = []

        def fake_run(*args, **kwargs):
            recorded_timeout.append(kwargs.get('timeout'))
            raise subprocess.TimeoutExpired(cmd=args[0] if args else [], timeout=kwargs.get('timeout'))

        with patch.object(review_dispatch.shutil, 'which', return_value='/fake/codex'), \
                patch.object(review_dispatch.subprocess, 'run', side_effect=fake_run):
            with self.assertRaises(LLMToolError) as caught:
                review_dispatch._dispatch_codex(
                    reviewer_root='/fake/root',
                    prompt='test prompt',
                    task=task,
                    head='a' * 40,
                )
            self.assertEqual(getattr(caught.exception, 'review_dispatch_code', None), 'REVIEWER_TIMEOUT')
            self.assertEqual(recorded_timeout, [10800])

        with patch.object(developer_routes, 'role_timeout', side_effect=RouteError('ROUTE_CONFIG_INVALID')), \
                patch.object(review_dispatch.subprocess, 'run') as mock_run:
            with self.assertRaises(LLMToolError) as caught:
                review_dispatch._dispatch_codex(
                    reviewer_root='/fake/root',
                    prompt='test prompt',
                    task=task,
                    head='a' * 40,
                )
            self.assertEqual(getattr(caught.exception, 'review_dispatch_code', None), 'ROUTE_CONFIG_UNAVAILABLE')
            mock_run.assert_not_called()


class RemoteTestTimeoutTests(unittest.TestCase):
    """Remote test timeout behavior."""

    def test_remote_test_ssh_timeout(self):
        self.assertEqual(remote_test._ssh_timeout(), 1800)
        self.assertEqual(remote_test._SSH_TIMEOUT, 1800)

        with patch.object(developer_routes, 'remote_test_timeout', side_effect=RouteError('ROUTE_CONFIG_INVALID')):
            with self.assertRaises(LLMToolError) as caught:
                remote_test._ssh_timeout()
            self.assertTrue(str(caught.exception).startswith('ADMISSION_DENIED'))


class DriftSchemaTimeoutTests(unittest.TestCase):
    """Drift check schema tests for developer-routes.json."""

    def setUp(self):
        self.schema = guidance_drift_check.RULE_SCHEMAS['developer-routes.json']
        self.shipped = json.loads(ROUTES_FILE.read_text(encoding='utf-8'))

    def test_schema_validates_timeouts(self):
        self.assertEqual(self.schema.errors(self.shipped, '$'), [])

        valid = copy.deepcopy(self.shipped)
        valid['roles']['codex-reviewer']['timeout'] = 18000
        valid['remote_test_timeout'] = 1800
        self.assertEqual(self.schema.errors(valid, '$'), [])

        invalid_0 = copy.deepcopy(self.shipped)
        invalid_0['roles']['codex-reviewer']['timeout'] = 0
        self.assertTrue(bool(self.schema.errors(invalid_0, '$')))

        invalid_18001 = copy.deepcopy(self.shipped)
        invalid_18001['roles']['codex-reviewer']['timeout'] = 18001
        self.assertTrue(bool(self.schema.errors(invalid_18001, '$')))

        invalid_remote_0 = copy.deepcopy(self.shipped)
        invalid_remote_0['remote_test_timeout'] = 0
        self.assertTrue(bool(self.schema.errors(invalid_remote_0, '$')))

        # The remote test ceiling stays 3600 (not an AI run, #2281).
        invalid_remote_3601 = copy.deepcopy(self.shipped)
        invalid_remote_3601['remote_test_timeout'] = 3601
        self.assertTrue(bool(self.schema.errors(invalid_remote_3601, '$')))


if __name__ == '__main__':
    unittest.main()
