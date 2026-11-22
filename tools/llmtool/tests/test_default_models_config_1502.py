"""#1502 WP-G: Reviewer and Debug defaults come from developer-routes.json.

Changing a role's default in a copy of the route configuration changes the
model review dispatch and AI Debug use, with no code change. A role with no
usable default is refused before any provider call.
"""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool import developer_routes, high_debug, review_dispatch
from llmtool.errors import LLMToolError

CONFIG_PATH = Path(__file__).resolve().parents[1] / 'developer-routes.json'
CODEX_REVIEW_DEFAULT = 'gpt-config-review-default'
CLAUDE_REVIEW_DEFAULT = 'claude-config-review-default'
CODEX_DEBUG_DEFAULT = 'gpt-config-debug-default'
CLAUDE_DEBUG_DEFAULT = 'claude-config-debug-default'


def route_config():
    """A mutable copy of the repository route configuration."""
    return copy.deepcopy(json.loads(CONFIG_PATH.read_text(encoding='utf-8')))


def with_default(config, role, model):
    config['roles'][role]['default_model'] = model
    return config


def without_role(config, role):
    del config['roles'][role]
    return config


class ReviewDefaultModelTests(unittest.TestCase):
    def test_codex_reviewer_default_changes_with_the_route_config(self):
        config = with_default(route_config(), 'codex-reviewer', CODEX_REVIEW_DEFAULT)
        with patch.object(developer_routes, 'load_route_config', return_value=config):
            argv = review_dispatch._codex_argv('/bin/codex', '/repo/reviewer')
        self.assertEqual(argv[argv.index('--model') + 1], CODEX_REVIEW_DEFAULT)

    def test_claude_reviewer_default_changes_with_the_route_config(self):
        config = with_default(route_config(), 'claude-reviewer', CLAUDE_REVIEW_DEFAULT)
        with patch.object(developer_routes, 'load_route_config', return_value=config):
            model = review_dispatch._review_default_model('claude-reviewer')
        self.assertEqual(model, CLAUDE_REVIEW_DEFAULT)

    def test_explicit_review_model_still_wins(self):
        config = with_default(route_config(), 'codex-reviewer', CODEX_REVIEW_DEFAULT)
        explicit = 'gpt-explicit-review-model'
        with patch.object(developer_routes, 'load_route_config', return_value=config):
            argv = review_dispatch._codex_argv(
                '/bin/codex', '/repo/reviewer', model=explicit)
        self.assertEqual(argv[argv.index('--model') + 1], explicit)

    def test_missing_reviewer_default_refuses_before_the_provider(self):
        config = without_role(route_config(), 'codex-reviewer')
        with patch.object(developer_routes, 'load_route_config', return_value=config), \
                patch.object(review_dispatch.shutil, 'which') as which, \
                patch.object(review_dispatch.subprocess, 'run') as run:
            with self.assertRaises(LLMToolError) as caught:
                review_dispatch._dispatch_codex(
                    reviewer_root='/repo/reviewer', prompt='prompt',
                    task={'task_id': 'a' * 32}, head='b' * 40)
        self.assertEqual(caught.exception.review_dispatch_code, 'ROLE_MODEL_NOT_ALLOWED')
        which.assert_not_called()
        run.assert_not_called()

    def test_unavailable_route_config_refuses_before_the_provider(self):
        with patch.object(developer_routes, 'load_route_config',
                          side_effect=developer_routes.RouteError('ROUTE_CONFIG_INVALID')), \
                patch.object(review_dispatch.shutil, 'which') as which, \
                patch.object(review_dispatch.subprocess, 'run') as run:
            with self.assertRaises(LLMToolError) as caught:
                review_dispatch._dispatch_codex(
                    reviewer_root='/repo/reviewer', prompt='prompt',
                    task={'task_id': 'a' * 32}, head='b' * 40)
        self.assertEqual(caught.exception.review_dispatch_code, 'ROUTE_CONFIG_UNAVAILABLE')
        which.assert_not_called()
        run.assert_not_called()

    def test_missing_claude_reviewer_default_refuses_before_the_provider(self):
        config = without_role(route_config(), 'claude-reviewer')
        with patch.object(developer_routes, 'load_route_config', return_value=config), \
                patch.object(review_dispatch.subprocess, 'run') as run:
            with self.assertRaises(LLMToolError) as caught:
                review_dispatch._review_model('claude-reviewer', None)
        self.assertEqual(caught.exception.review_dispatch_code, 'ROLE_MODEL_NOT_ALLOWED')
        run.assert_not_called()


class DebugDefaultModelTests(unittest.TestCase):
    def _run_codex(self, config):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(developer_routes, 'load_route_config', return_value=config), \
                    patch.object(high_debug.subprocess, 'run') as run:
                high_debug._run_codex(
                    prompt='prompt', cwd=directory,
                    output_path=str(Path(directory) / 'report.md'))
        return run

    def test_codex_debug_default_changes_with_the_route_config(self):
        run = self._run_codex(with_default(route_config(), 'codex-debug', CODEX_DEBUG_DEFAULT))
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index('-m') + 1], CODEX_DEBUG_DEFAULT)

    def _run_claude(self, config):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(developer_routes, 'load_route_config', return_value=config), \
                    patch.object(high_debug.subprocess, 'run') as run:
                high_debug._run_claude(
                    prompt='prompt', cwd=directory,
                    output_path=str(Path(directory) / 'report.md'),
                    config_dir=directory)
        return run

    def test_claude_debug_default_changes_with_the_route_config(self):
        run = self._run_claude(with_default(route_config(), 'claude-debug', CLAUDE_DEBUG_DEFAULT))
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index('--model') + 1], CLAUDE_DEBUG_DEFAULT)

    def test_explicit_debug_model_still_wins(self):
        config = with_default(route_config(), 'codex-debug', CODEX_DEBUG_DEFAULT)
        explicit = 'gpt-explicit-debug-model'
        with patch.object(developer_routes, 'load_route_config', return_value=config):
            model, _timeout = high_debug._launch_settings('codex-debug', explicit)
        self.assertEqual(model, explicit)

    def test_missing_debug_default_refuses_before_the_provider(self):
        config = without_role(route_config(), 'codex-debug')
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(developer_routes, 'load_route_config', return_value=config), \
                patch.object(high_debug.subprocess, 'run') as run:
            with self.assertRaises(LLMToolError) as caught:
                high_debug._run_codex(
                    prompt='prompt', cwd=directory,
                    output_path=str(Path(directory) / 'report.md'))
        self.assertEqual(caught.exception.high_debug_code, 'ROLE_MODEL_NOT_ALLOWED')
        run.assert_not_called()

    def test_missing_claude_debug_default_refuses_before_the_provider(self):
        config = without_role(route_config(), 'claude-debug')
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(developer_routes, 'load_route_config', return_value=config), \
                patch.object(high_debug.subprocess, 'run') as run:
            with self.assertRaises(LLMToolError) as caught:
                high_debug._run_claude(
                    prompt='prompt', cwd=directory,
                    output_path=str(Path(directory) / 'report.md'),
                    config_dir=directory)
        self.assertEqual(caught.exception.high_debug_code, 'ROLE_MODEL_NOT_ALLOWED')
        run.assert_not_called()


class NoCopiedDefaultTests(unittest.TestCase):
    def test_consumers_keep_no_default_model_value(self):
        for name in ('_CODEX_REVIEW_MODEL', '_CLAUDE_REVIEW_MODEL'):
            self.assertFalse(hasattr(review_dispatch, name), name)
        for name in ('_CODEX_MODEL', '_CLAUDE_MODEL'):
            self.assertFalse(hasattr(high_debug, name), name)


if __name__ == '__main__':
    unittest.main()
