"""#1815 T2b: AI Debug run timeouts come from the developer-routes role entry."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool import developer_routes
from llmtool import high_debug
from llmtool.developer_routes import RouteError
from llmtool.errors import LLMToolError


def _launch_codex(directory):
    return high_debug._run_codex(prompt='diagnosis', cwd=directory,
                                 output_path=str(Path(directory) / 'report.md'))


def _launch_claude(directory):
    return high_debug._run_claude(prompt='diagnosis', cwd=directory,
                                  output_path=str(Path(directory) / 'report.md'),
                                  config_dir=directory)


_CASES = (
    ('codex-debug', _launch_codex),
    ('claude-debug', _launch_claude),
)


class DebugRoleTimeoutTests(unittest.TestCase):
    def test_shipped_config_passes_default_timeout(self):
        for role, launch in _CASES:
            with self.subTest(role=role), tempfile.TemporaryDirectory() as directory, \
                 patch.object(high_debug.subprocess, 'run') as run:
                launch(directory)
            run.assert_called_once()
            # #2281 (Owner 2026-11-19): AI Debug runs default to 3 hours.
            self.assertEqual(run.call_args.kwargs['timeout'], 10800)
            self.assertEqual(developer_routes.DEFAULT_ROLE_TIMEOUT_SECONDS, 10800)

    def test_configured_role_timeout_is_passed_to_launch(self):
        for role, launch in _CASES:
            asked = []

            def configured(requested, path=None):
                asked.append(requested)
                return 1234

            with self.subTest(role=role), tempfile.TemporaryDirectory() as directory, \
                 patch('llmtool.developer_routes.role_timeout', side_effect=configured), \
                 patch.object(high_debug.subprocess, 'run') as run:
                launch(directory)
            run.assert_called_once()
            self.assertEqual(run.call_args.kwargs['timeout'], 1234)
            self.assertEqual(asked, [role])

    def test_invalid_route_config_refuses_before_launch(self):
        for role, launch in _CASES:
            with self.subTest(role=role), tempfile.TemporaryDirectory() as directory, \
                 patch('llmtool.developer_routes.role_timeout',
                       side_effect=RouteError('ROUTE_CONFIG_INVALID')), \
                 patch.object(high_debug.subprocess, 'run') as run:
                with self.assertRaises(LLMToolError) as caught:
                    launch(directory)
            self.assertEqual(caught.exception.high_debug_code, 'ROUTE_CONFIG_UNAVAILABLE')
            run.assert_not_called()

    def test_route_config_refusal_never_switches_routes(self):
        self.assertNotIn('ROUTE_CONFIG_UNAVAILABLE', high_debug.DEBUG_FALLBACK_CODES)


if __name__ == '__main__':
    unittest.main()
