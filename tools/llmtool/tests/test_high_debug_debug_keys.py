"""#1502 WP-B: AI Debug dispatch keys and the Claude Debug account in run_high_debug."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import high_debug
from llmtool.errors import LLMToolError

from test_high_debug import _review_fail_claim

REPORT = ''.join('## ' + heading + '\nDetails.\n' for heading in high_debug.REPORT_SECTIONS) + 'RCA_CONFIRMED'


def _codex_quota_first(claude_runner):
    """A runner whose first launch is a Codex attempt reporting a real quota failure.

    PR #2222: Claude Debug runs only as the fallback of this run's Codex
    Debug, so the Claude runner is reached through that fallback.
    """
    launches = []

    def runner(**kwargs):
        launches.append(True)
        if len(launches) == 1:
            return subprocess.CompletedProcess([], 1, stdout=b'',
                                               stderr=b"You've hit your usage limit.\n")
        return claude_runner(**kwargs)
    return runner


class _Store:
    def __init__(self):
        self.entries, self.events, self.served = [], [], set()

    def task_ids(self):
        return sorted(self.served)

    def finalize_high_debug(self, _task_id, index, entry):
        self.entries[index] = entry

    def get(self, task_id):
        self.served.add(task_id)
        return {'task_id': task_id, 'run_id': 'b' * 32,
                'review_repair_claims': [{'family': 'family', 'pr': 17}] * 2,
                'review_dispatch_claim': [_review_fail_claim(task_id)],
                'completion': {'executor_task_id': 'executor',
                               'evidence': {'common_dir': '/tmp/ignored'}},
                'high_debug': list(self.entries)}

    @staticmethod
    def _completion_now():
        return '2026-11-12T00:00:00Z'

    def append_high_debug(self, _task_id, entry):
        self.entries.append(entry)

    def record_completion_event(self, _task_id, **event):
        self.events.append(event)
        return {'created': True}


class RunHighDebugTests(unittest.TestCase):
    def setUp(self):
        # Codex-first route; the Owner pause of codex-debug is covered in
        # test_high_debug_fallback.PausedCodexDebugTests.
        _codex_paused = patch.object(high_debug, '_codex_debug_owner_paused', return_value=False)
        _codex_paused.start()
        self.addCleanup(_codex_paused.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'repo'
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        for name, value in (('_post_github_record', True),):
            patcher = patch.object(high_debug, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(high_debug.task_store, 'default_root',
                               return_value=Path(temporary.name) / 'tasks')
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(high_debug.ai_github_exec, 'verified_ai_env',
                               return_value={'GH_CONFIG_DIR': '/safe/gh-config', 'PATH': '/usr/bin'})
        patcher.start()
        self.addCleanup(patcher.stop)
        # #1502 WP-F: the rule loader would read main; test_high_debug_cards covers it.
        patcher = patch.object(high_debug, '_debug_rules',
                               return_value=('RULES stub\n', 'c' * 64))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, route, runner, *, resolve=None, allowed=True):
        """Run one Debug. Route 'claude' is the authorized fallback (PR #2222):
        the Codex attempt of the same run reports a real quota failure first."""
        registration = SimpleNamespace(result_sha='a' * 40, root=str(self.root),
                                       common_dir=str(self.root / '.git'))
        store = _Store()
        if route == 'claude':
            runner = _codex_quota_first(runner)
            debug = high_debug.run_high_debug_with_fallback
            route = 'codex'
        else:
            debug = high_debug.run_high_debug
        resolve = resolve or (lambda role: {'role': role, 'source': 'roles', 'account': 'acct',
                                            'config_dir': '/safe/claude', 'provider': 'claude'})
        with patch.object(high_debug.worker_dispatch, 'evaluate',
                          return_value=SimpleNamespace(allowed=allowed)) as evaluate, \
             patch.object(high_debug.role_config, 'resolve_role', side_effect=resolve) as roles, \
             patch.object(high_debug.task_completion, 'collect_git_snapshot',
                          return_value=SimpleNamespace(head='a' * 40)):
            try:
                debug(
                    store=store, task_id='a' * 32, executor_task='executor', pr_number=17,
                    family='family', failed_corrections=2, runner=runner,
                    create_d6=lambda **_kwargs: str(self.root), repo_root=str(self.root),
                    read_registration=lambda *_args, **_kwargs: registration, route=route)
                code = None
            except LLMToolError as error:
                code = error.high_debug_code
        return code, store, evaluate, roles

    def test_codex_uses_the_codex_debug_key(self):
        def runner(*, prompt, cwd, output_path):
            Path(output_path).write_text(REPORT, encoding='utf-8')
            return subprocess.CompletedProcess([], 0, stdout=b'{"model":"gpt-6.1-sol"}\n', stderr=b'')
        code, store, evaluate, _roles = self._run('codex', runner)
        self.assertIsNone(code)
        evaluate.assert_called_with('codex-debug', None, 'a' * 32)
        self.assertEqual(store.entries[0]['status'], 'RCA_CONFIRMED')

    def test_codex_quota_failure_is_recorded_with_its_code(self):
        def runner(*, prompt, cwd, output_path):
            return subprocess.CompletedProcess([], 1, stdout=b'', stderr=b"You've hit your usage limit.")
        code, store, _evaluate, _roles = self._run('codex', runner)
        self.assertEqual(code, 'HIGH_DEBUG_QUOTA_EXHAUSTED')
        self.assertEqual(store.entries[0]['report'], 'HIGH_DEBUG_QUOTA_EXHAUSTED')

    def test_claude_debug_key_and_reviewer_account_default(self):
        calls = []
        def resolve(role):
            calls.append(role)
            if role == 'claude-debug':
                return {'role': role, 'source': 'builtin', 'account': None, 'config_dir': None}
            return {'role': role, 'source': 'roles', 'account': 'reviewer-account',
                    'config_dir': '/safe/claude', 'provider': 'claude'}
        refused = []
        def runner(**_kwargs):
            refused.append(True)
            raise RuntimeError('stop after the account choice')
        code, store, evaluate, _roles = self._run('claude', runner, resolve=resolve)
        evaluate.assert_called_with('claude-debug', None, 'a' * 32)
        # Claude Debug ran only as the fallback of this run's Codex quota failure.
        self.assertEqual([call.args for call in evaluate.call_args_list],
                         [('codex-debug', None, 'a' * 32), ('claude-debug', None, 'a' * 32)])
        self.assertEqual(store.entries[0]['report'], 'HIGH_DEBUG_QUOTA_EXHAUSTED')
        self.assertEqual(calls, ['claude-debug', 'reviewer'])
        self.assertEqual(code, 'HIGH_DEBUG_RUN_FAILED')
        self.assertEqual(refused, [True])
        self.assertEqual(high_debug._CLAUDE_GRANTS, set())

    def test_configured_claude_debug_role_is_used_alone(self):
        launched = []
        def runner(**_kwargs):
            launched.append(True)
            raise RuntimeError('stop')
        _code, store, _evaluate, roles = self._run('claude', runner)
        self.assertEqual([call.args[0] for call in roles.call_args_list], ['claude-debug'])
        self.assertEqual(launched, [True])
        self.assertEqual([entry['model_requested'] for entry in store.entries],
                         [high_debug._debug_default_model('codex-debug'),
                          high_debug._debug_default_model('claude-debug')])


if __name__ == '__main__':
    unittest.main()
