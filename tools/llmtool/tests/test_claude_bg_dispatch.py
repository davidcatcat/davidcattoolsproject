"""Background Claude Developer dispatch receipt (Issue #365).

Every case drives a fake `claude` executable from a scenario file; no real
session, model or network call is made.
"""
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_developer_assignment_inbox as assignment_fixtures
import test_task_completion as fixtures

_SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'claude_bg_dispatch.py'
_spec = importlib.util.spec_from_file_location('claude_bg_dispatch_under_test', _SCRIPT)
dispatch_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dispatch_tool)
REAL_RESOLVE_ROLE = dispatch_tool.role_config.resolve_role

TARGET = '2b128608-8b94-4c12-96b9-e6cdf9a388c7'
COPY = '95c9758e-1111-4222-8333-944455556666'
OTHER = '3c239719-0000-4000-8000-000000000000'

# The fake reads the scenario, appends every argv to calls.jsonl, and on
# `--bg` stamps the launch time and writes the scenario's transcripts.
FAKE_CLAUDE = r'''
import json, os, sys, time
from pathlib import Path
home = Path(os.environ['FAKE_CLAUDE_DIR'])
account = (Path(os.environ['CLAUDE_CONFIG_DIR']) if os.environ.get('CLAUDE_CONFIG_DIR')
           else Path(os.environ['HOME']) / '.claude')
account_scenario = account / 'fake-scenario.json'
scenario_path = account_scenario if account_scenario.exists() else home / 'scenario.json'
scenario = json.loads(scenario_path.read_text())
state_path = home / 'state.json'
state = json.loads(state_path.read_text()) if state_path.exists() else {'polls': 0}
argv = sys.argv[1:]
with open(home / 'calls.jsonl', 'a') as log:
    log.write(json.dumps({'argv': argv, 'cwd': os.getcwd(),
                          'config_dir': os.environ.get('CLAUDE_CONFIG_DIR'),
                          'home': os.environ.get('HOME'),
                          'effective_account': str(account),
                          'model': os.environ.get('ANTHROPIC_MODEL')}) + '\n')

def fill(value):
    text = json.dumps(value)
    text = text.replace('"LAUNCH"', str(state.get('launch_ms', 0) + 1)).replace('"OLD"', '1')
    return json.loads(text.replace('NEW-SESSION', state.get('new_session', 'NEW-SESSION')))

def save():
    state_path.write_text(json.dumps(state))

if argv[:2] == ['agents', '--json']:
    if '--all' in argv:
        rows = scenario.get('all', [])
    elif 'launch_ms' not in state:
        rows = scenario.get('before', [])
    else:
        after = scenario.get('after', [[]])
        rows = after[min(state['polls'], len(after) - 1)]
        state['polls'] += 1
        save()
    print(json.dumps(fill(rows)))
elif '--bg' in argv:
    # Parse like commander: a variadic `--add-dir` takes every following
    # non-option argument; `--` ends options and leaves the prompt positional.
    add_dirs, positional, index = [], [], 0
    while index < len(argv):
        arg = argv[index]
        if arg == '--':
            positional += argv[index + 1:]
            break
        if arg == '--add-dir':
            index += 1
            while index < len(argv) and not argv[index].startswith('-'):
                add_dirs.append(argv[index])
                index += 1
            continue
        if arg in ('-n', '--resume', '--session-id'):
            index += 2
            continue
        if not arg.startswith('-'):
            positional.append(arg)
        index += 1
    prompt = positional[0] if positional and not scenario.get('drop_prompt') else None
    with open(home / 'parsed.jsonl', 'a') as log:
        log.write(json.dumps({'add_dirs': add_dirs, 'prompt': prompt}) + '\n')
    state['launch_ms'] = int(time.time() * 1000)
    if '--session-id' in argv:
        state['new_session'] = argv[argv.index('--session-id') + 1]
    save()
    def stamp(ms):
        return time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(ms // 1000)) + '.%03dZ' % (ms % 1000)
    now = stamp(int(time.time() * 1000) + 50)
    for item in scenario.get('transcripts', []):
        sid = fill(item['session'])
        # The default account intentionally has no CLAUDE_CONFIG_DIR in the
        # child environment. Keep the fake's transcript store rooted at the
        # same effective account in that case.
        folder = account / 'projects' / item.get('project', 'p')
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / (sid + '.jsonl'), 'a') as out:
            out.write(json.dumps({'type': 'user', 'cwd': '/old/cwd',
                                  'timestamp': '2026-01-01T00:00:00.000Z'}) + '\n')
            for index in range(item.get('old_actions', 0)):
                out.write(json.dumps({'type': 'assistant',
                    'timestamp': '2026-01-01T00:00:%02d.000Z' % (index % 60),
                    'message': {'content': [{'type': 'tool_use', 'name': 'Old'}]}}) + '\n')
            if item.get('pre_launch_action'):
                # Just inside the old 2-second tolerance, but before this launch.
                out.write(json.dumps({'type': 'assistant',
                    'timestamp': stamp(state['launch_ms'] - 1000),
                    'message': {'content': [{'type': 'tool_use', 'name': 'Earlier'}]}}) + '\n')
            if item.get('new_action') and prompt is not None:
                out.write(json.dumps({'type': 'assistant', 'timestamp': now,
                    'message': {'content': [{'type': 'text', 'text': 'x'},
                                            {'type': 'tool_use', 'name': 'Bash'}]}}) + '\n')
    if prompt is None:
        print('backgrounded · 2b128608 · claude-developer (idle — send a prompt to start)')
    else:
        print(scenario.get('launch_stdout', 'backgrounded'))
    sys.exit(scenario.get('launch_exit', 0))
elif argv[:1] == ['stop']:
    print('stopped')
else:
    sys.exit(9)
'''


def row(session, *, name='claude-developer', status='busy', state=None, started='LAUNCH',
        cwd='/repo', kind='background'):
    value = {'sessionId': session, 'id': session[:8], 'name': name, 'status': status,
             'startedAt': started, 'cwd': cwd, 'kind': kind, 'pid': 1}
    if state is not None:
        value['state'] = state
    return value


import review_trust_fixture


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


class ClaudeBgDispatchTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.TaskCompletionTests('test_failed_test_subprocess_is_recorded_with_nonzero_status')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.config = self.home / 'claude-config'
        self.config.mkdir()
        self.default_config = self.home / '.claude'
        self.default_config.mkdir()
        self.claude = self.home / 'claude'
        self.claude.write_text('#!' + sys.executable + ' -B\n' + FAKE_CLAUDE)
        self.claude.chmod(0o700)
        env = patch.dict(os.environ, {'FAKE_CLAUDE_DIR': str(self.home),
                                      'HOME': str(self.home),
                                      'CLAUDE_CONFIG_DIR': str(self.config)})
        env.start()
        self.addCleanup(env.stop)
        gate = patch.object(dispatch_tool.worker_dispatch, 'evaluate',
                            return_value=dispatch_tool.worker_dispatch.PolicyDecision(
                                'claude-developer', True, 'OWNER_POLICY_ALLOWED', 'a' * 64))
        gate.start()
        self.addCleanup(gate.stop)
        role = patch.object(dispatch_tool.role_config, 'resolve_role', return_value={
            'role': 'claude-developer', 'config_dir': None, 'model': None})
        role.start()
        self.addCleanup(role.stop)
        self.root = str(self.f.root)
        self.cwd = str(self.home)  # the session's recorded cwd must exist to launch there

    def scenario(self, *, account_dir=None, **value):
        path = (Path(account_dir) / 'fake-scenario.json' if account_dir is not None
                else self.home / 'scenario.json')
        path.write_text(json.dumps(value))

    def calls(self):
        path = self.home / 'calls.jsonl'
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def reset(self):
        for name in ('calls.jsonl', 'state.json', 'parsed.jsonl'):
            (self.home / name).unlink(missing_ok=True)

    def recent(self):
        """A `--since` value just before now (the dispatch receipt's launched_at)."""
        return dispatch_tool._iso(int(time.time() * 1000) - 60000)

    def parsed(self):
        path = self.home / 'parsed.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()]

    def launches(self):
        return [call for call in self.calls() if '--bg' in call['argv']]

    def stops(self):
        return [call['argv'][1] for call in self.calls() if call['argv'][:1] == ['stop']]

    def run_tool(self, *args, timeout='1'):
        out = StringIO()
        argv = ['--claude', str(self.claude), '--poll-interval', '0.01'] + list(args)
        if args and args[0] == 'dispatch':
            argv += ['--repo-root', self.root, '--timeout', timeout]
        with redirect_stdout(out):
            code = dispatch_tool._main(argv)
        return code, json.loads(out.getvalue())

    def dispatch(self, session=TARGET, *, name='claude-developer', task='executor-task', timeout='1',
                 owner_ref=None):
        args = ['dispatch', '--session', session, '--name', name,
                '--task-id', task, '--prompt', 'Task Contract: do the work']
        if owner_ref is not None:
            args += ['--owner-assignment-ref', owner_ref]
        return self.run_tool('dispatch', '--session', session, '--name', name,
                             '--task-id', task, '--prompt', 'Task Contract: do the work',
                             timeout=timeout) if owner_ref is None else self.run_tool(
                                 *args, timeout=timeout)

    def test_dispatch_gate_allows_pauses_assignment_and_unknown_roles_before_launch(self):
        valid_ref = 'https://github.com/davidcatcat/davidcattoolsproject/issues/289#issuecomment-1'
        cases = [
            ('claude-developer', 'claude-developer', 'executor-task', None, True,
             'OWNER_POLICY_ALLOWED'),
            ('claude-developer-ui', 'claude-developer', 'executor-task', None, True,
             'OWNER_POLICY_ALLOWED'),
            ('claude-developer-ui-tools', 'claude-developer', 'executor-task', None, True,
             'OWNER_POLICY_ALLOWED'),
            ('claude-reviewer', 'claude-reviewer', 'executor-task', None, False, 'OWNER_PAUSED'),
            ('claude-developer', 'claude-developer', 'executor-task', None, False,
             'OWNER_ASSIGNMENT_REQUIRED'),
            ('claude-developer', 'claude-developer', 'executor-task', valid_ref, True,
             'OWNER_ASSIGNMENT_MATCHED'),
            ('unknown-role', 'unknown', 'executor-task', None, False, 'WORKER_UNKNOWN'),
        ]
        for role, worker, task, ref, allowed, reason in cases:
            with self.subTest(role=role, allowed=allowed):
                self.reset()
                self.scenario(all=[row(TARGET, status='done', cwd=self.cwd)], before=[], after=[[]])
                decision = dispatch_tool.worker_dispatch.PolicyDecision(
                    worker, allowed, reason, 'b' * 64)
                with patch.object(dispatch_tool.worker_dispatch, 'evaluate', return_value=decision) as gate:
                    code, receipt = self.dispatch(name=role, task=task, owner_ref=ref)
                if allowed:
                    gate.assert_called_once_with(worker, ref, task)
                    self.assertEqual(len(self.launches()), 1)
                else:
                    self.assertEqual(code, 2)
                    self.assertEqual(receipt['reason'], reason)
                    self.assertEqual(self.launches(), [])

    def test_invalid_developer_name_variants_are_refused_before_gate_or_launch(self):
        for name, reason in (
                ('claude-developerx', 'WORKER_UNKNOWN'),
                ('claude-developer-UI', 'ROLE_NAME_INVALID'),
                ('claude-developer-', 'WORKER_UNKNOWN')):
            with self.subTest(name=name):
                self.reset()
                self.scenario(all=[row(TARGET, status='done', cwd=self.cwd)])
                with patch.object(dispatch_tool.worker_dispatch, 'evaluate') as gate:
                    code, receipt = self.dispatch(name=name)
                self.assertEqual((code, receipt['reason']), (2, reason))
                gate.assert_not_called()
                self.assertEqual(self.launches(), [])

    def test_dispatch_unknown_role_is_refused_without_evaluating_or_launching(self):
        with patch.object(dispatch_tool.worker_dispatch, 'evaluate') as gate:
            code, receipt = self.dispatch(name='other')
        self.assertEqual(code, 2)
        self.assertEqual(receipt['reason'], 'WORKER_UNKNOWN')
        gate.assert_not_called()
        self.assertEqual(self.calls(), [])

    def test_role_config_dir_and_model_are_used_only_when_environment_is_unset(self):
        role_dir = self.home / 'role-account'
        role_dir.mkdir()
        self.scenario(all=[row(TARGET, status='done', cwd=self.cwd)], before=[], after=[[]])
        configured = {'role': 'claude-developer', 'config_dir': str(role_dir),
                      'model': 'role-model'}
        with patch.object(dispatch_tool.role_config, 'resolve_role', return_value=configured), \
                patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': '', 'ANTHROPIC_MODEL': ''}, clear=False):
            os.environ.pop('CLAUDE_CONFIG_DIR', None)
            os.environ.pop('ANTHROPIC_MODEL', None)
            self.dispatch()
        launch = self.launches()[0]
        self.assertEqual(launch['config_dir'], str(role_dir))
        self.assertEqual(launch['model'], 'role-model')

        self.reset()
        self.scenario(all=[row(TARGET, status='done', cwd=self.cwd)], before=[], after=[[]])
        with patch.object(dispatch_tool.role_config, 'resolve_role', return_value=configured), \
                patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(self.config),
                                        'ANTHROPIC_MODEL': 'env-model'}):
            self.dispatch()
        launch = self.launches()[0]
        self.assertEqual(launch['config_dir'], str(self.config))
        self.assertEqual(launch['model'], 'env-model')

    def test_default_account_omits_config_dir_for_launch_and_agent_listing(self):
        self.scenario(all=[row(TARGET, status='done', cwd=self.cwd)],
                      before=[row(TARGET, status='idle', cwd=self.cwd)],
                      after=[[row(TARGET, cwd=self.cwd)]],
                      transcripts=[{'session': TARGET, 'new_action': True}])
        with patch.object(dispatch_tool.pwd, 'getpwuid',
                          return_value=SimpleNamespace(pw_dir=str(self.home))), \
                patch.object(dispatch_tool.role_config, 'resolve_role', return_value={
                    'role': 'claude-developer', 'provider': 'claude',
                    'config_dir': str(self.default_config), 'model': None}), \
                patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': '', 'HOME': '/mismatched/home'}):
            code, _ = self.dispatch()
        self.assertEqual(code, 0)
        calls = self.calls()
        self.assertTrue(any('--bg' in call['argv'] for call in calls))
        self.assertTrue(any(call['argv'][:2] == ['agents', '--json'] for call in calls))
        self.assertTrue(all(call['config_dir'] is None for call in calls))
        self.assertTrue(all(call['home'] == str(self.home) for call in calls))
        self.assertTrue(all(call['effective_account'] == str(self.default_config) for call in calls))

    def test_account_environment_resolves_default_and_preserves_other_override(self):
        with patch.object(dispatch_tool.pwd, 'getpwuid',
                          return_value=SimpleNamespace(pw_dir=str(self.home))), \
                patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(self.default_config)}):
            _, default_env = dispatch_tool._account_environment('claude-developer')
        self.assertNotIn('CLAUDE_CONFIG_DIR', default_env)

        other = self.home / 'claude2'
        other.mkdir()
        with patch.object(dispatch_tool.pwd, 'getpwuid',
                          return_value=SimpleNamespace(pw_dir=str(self.home))), \
                patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(other)}):
            _, other_env = dispatch_tool._account_environment('claude-developer')
        self.assertEqual(other_env['CLAUDE_CONFIG_DIR'], str(other))

    def test_default_account_sets_child_home_for_role_env_and_implicit_selection(self):
        home_cases = {
            'mismatched': {'HOME': '/different/home'},
            'absent': {},
            'empty': {'HOME': ''},
        }
        selections = {
            'role': ({'role': 'claude-developer', 'provider': 'claude',
                      'config_dir': str(self.default_config), 'model': None}, {}),
            'environment': ({'role': 'claude-developer', 'provider': 'claude',
                             'config_dir': None, 'model': None},
                            {'CLAUDE_CONFIG_DIR': str(self.default_config)}),
            'implicit': ({'role': 'claude-developer', 'provider': 'claude',
                          'config_dir': None, 'model': None}, {}),
        }
        for selection_name, (configured, selection_env) in selections.items():
            for home_name, home_env in home_cases.items():
                with self.subTest(selection=selection_name, home=home_name):
                    parent_env = {**selection_env, **home_env}
                    with patch.object(dispatch_tool.pwd, 'getpwuid',
                                      return_value=SimpleNamespace(pw_dir=str(self.home))), \
                            patch.object(dispatch_tool.role_config, 'resolve_role',
                                         return_value=configured), \
                            patch.dict(os.environ, parent_env, clear=True):
                        before = dict(os.environ)
                        _, child_env = dispatch_tool._account_environment('claude-developer')
                        self.assertEqual(dict(os.environ), before)
                    self.assertEqual(child_env['HOME'], str(self.home))
                    self.assertNotIn('CLAUDE_CONFIG_DIR', child_env)

    def test_default_account_cross_account_guard_compares_resolved_paths(self):
        alias = self.home / 'default-alias'
        alias.symlink_to(self.default_config, target_is_directory=True)
        with patch.object(dispatch_tool.pwd, 'getpwuid',
                          return_value=SimpleNamespace(pw_dir=str(self.home))):
            dispatch_tool.refuse_cross_account_launch(str(alias), account_env={})
            with self.assertRaises(dispatch_tool.DispatchError) as raised:
                dispatch_tool.refuse_cross_account_launch(str(self.config), account_env={})
        self.assertEqual(raised.exception.code, 'CROSS_ACCOUNT_LAUNCH_REFUSED')

    def test_invalid_role_config_reports_role_error_without_launching(self):
        error = dispatch_tool.LLMToolError('invalid role config')
        error.role_config_code = 'ROLE_ACCOUNT_DIR_INVALID'
        with patch.object(dispatch_tool.role_config, 'resolve_role', side_effect=error):
            code, receipt = self.dispatch()
        self.assertEqual((code, receipt['reason']), (2, 'ROLE_ACCOUNT_DIR_INVALID'))
        self.assertEqual(self.calls(), [])

    def test_reviewer_role_resolves_real_config_and_uses_it_for_every_cli_call(self):
        from llmtool.config import user_config_path

        reviewer_dir = self.home / 'reviewer-account'
        reviewer_dir.mkdir()
        user_home = self.home / 'user-home'
        user_home.mkdir()
        temp_dir = self.home / 'tmp'
        temp_dir.mkdir()
        config_path = user_config_path(str(user_home))
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            'accounts:\n  reviewer:\n    kind: claude\n    config_dir: %s\n'
            'roles:\n  reviewer:\n    provider: claude\n    account: reviewer\n' % reviewer_dir)
        self.scenario(account_dir=reviewer_dir,
                      all=[row(TARGET, status='done', state='done', cwd=self.cwd)], before=[],
                      after=[[row(TARGET, status='done', state='done', cwd=self.cwd)]])
        with patch.object(dispatch_tool.role_config, 'resolve_role',
                          side_effect=lambda role: REAL_RESOLVE_ROLE(role, home=str(user_home))), \
                patch.dict(os.environ, {'HOME': str(user_home), 'TMPDIR': str(temp_dir),
                                        'CLAUDE_CONFIG_DIR': '', 'ANTHROPIC_MODEL': ''}):
            code, receipt = self.dispatch(name='claude-reviewer')
        self.assertEqual(code, 1, receipt)
        self.assertEqual(receipt['reason'], 'SESSION_DONE')
        self.assertTrue(self.calls())
        self.assertTrue(all(call['config_dir'] == str(reviewer_dir) for call in self.calls()))

    def test_reviewer_without_account_refuses_before_cli(self):
        with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': '', 'HOME': str(self.home)}):
            with patch.object(dispatch_tool.role_config, 'resolve_role',
                              return_value={'provider': 'claude', 'config_dir': None}):
                code, receipt = self.dispatch(name='claude-reviewer')
        self.assertEqual((code, receipt['reason']), (2, 'REVIEWER_ACCOUNT_UNCONFIGURED'))
        self.assertEqual(self.calls(), [])

    def test_non_claude_role_provider_refuses_before_cli(self):
        with patch.object(dispatch_tool.role_config, 'resolve_role', return_value={
                'provider': 'codex', 'config_dir': str(self.config)}):
            code, receipt = self.dispatch()
        self.assertEqual((code, receipt['reason']), (2, 'ROLE_PROVIDER_UNSUPPORTED'))
        self.assertEqual(self.calls(), [])

    def test_codex_reviewer_is_refused_before_cli_with_or_without_account_config(self):
        cases = (
            ({'provider': 'codex', 'config_dir': str(self.config)},
             {'CLAUDE_CONFIG_DIR': str(self.config)}),
            ({'provider': 'claude', 'config_dir': None},
             {'CLAUDE_CONFIG_DIR': ''}),
        )
        for configured, environment in cases:
            with self.subTest(configured=configured, environment=environment):
                self.reset()
                with patch.object(dispatch_tool.role_config, 'resolve_role',
                                  return_value=configured), \
                        patch.dict(os.environ, environment):
                    code, receipt = self.dispatch(name='codex-reviewer')
                self.assertEqual((code, receipt['reason']),
                                 (2, 'ROLE_PROVIDER_UNSUPPORTED'))
                self.assertEqual(self.calls(), [])

    def test_busy_resume_refuses_using_resolved_account(self):
        role_dir = self.home / 'developer-account'
        role_dir.mkdir()
        self.scenario(account_dir=role_dir,
                      all=[row(TARGET, status='busy', cwd=self.cwd)],
                      before=[row(TARGET, status='busy', cwd=self.cwd)])
        with patch.object(dispatch_tool.role_config, 'resolve_role', return_value={
                'role': 'claude-developer', 'provider': 'claude',
                'config_dir': str(role_dir), 'model': None}), \
                patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': ''}):
            code, receipt = self.dispatch()
        self.assertEqual((code, receipt['reason']), (2, 'SESSION_BUSY'))
        self.assertEqual(self.launches(), [])
        self.assertEqual(len(self.calls()), 2)
        self.assertTrue(all(call['config_dir'] == str(role_dir) for call in self.calls()))

    def test_busy_with_new_action_is_delivered_and_old_history_is_ignored(self):
        self.scenario(
            all=[row(TARGET, status='done', started='OLD', cwd=self.cwd)],
            before=[], after=[[], [row(TARGET, status='busy', cwd=self.cwd)]],
            transcripts=[{'session': TARGET, 'old_actions': 58, 'new_action': True}])
        code, receipt = self.dispatch()
        self.assertEqual(code, 0, receipt)
        self.assertEqual(receipt['result'], 'DELIVERED')
        self.assertEqual(receipt['actual_session_id'], TARGET)
        self.assertFalse(receipt['copy_started'])
        self.assertTrue(receipt['name_ok'])
        self.assertEqual(receipt['first_action']['tool'], 'Bash')
        self.assertEqual(receipt['d6_task_id'], 'executor-task')
        self.assertEqual(receipt['d6_root'], self.root)
        # The registry names a label, not this session: the PM must update it.
        self.assertEqual(receipt['session_ref_update_required'],
                         {'task_id': 'executor-task', 'from': 'Executor D6 session',
                          'to': TARGET})
        [launch] = self.launches()
        argv = launch['argv']
        self.assertEqual(argv[argv.index('--resume') + 1], TARGET)
        self.assertEqual(argv[argv.index('-n') + 1], 'claude-developer')
        self.assertEqual(argv[argv.index('--add-dir') + 1], self.root)
        # `--` ends the variadic --add-dir, so the prompt stays the prompt.
        self.assertEqual(argv[-2:], ['--', 'Task Contract: do the work'])
        self.assertEqual(self.parsed(), [{'add_dirs': [self.root],
                                          'prompt': 'Task Contract: do the work'}])
        self.assertEqual(os.path.realpath(launch['cwd']), os.path.realpath(self.cwd))
        self.assertEqual(self.stops(), [])

    def test_working_null_then_busy_with_new_action_is_delivered_without_stop(self):
        self.scenario(
            all=[row(TARGET, status='done', started='OLD', cwd=self.cwd)],
            after=[[row(TARGET, status=None, state='working', cwd=self.cwd)],
                   [row(TARGET, status='busy', state='working', cwd=self.cwd)]],
            transcripts=[{'session': TARGET, 'new_action': True}])
        code, receipt = self.dispatch()
        self.assertEqual(code, 0, receipt)
        self.assertEqual(receipt['result'], 'DELIVERED')
        self.assertEqual(receipt['first_action']['tool'], 'Bash')
        self.assertEqual(receipt['status'], 'busy')
        self.assertEqual(receipt['state'], 'working')
        self.assertEqual(self.stops(), [])

    def test_working_null_until_timeout_is_not_delivered(self):
        self.scenario(
            all=[row(TARGET, status='done', started='OLD', cwd=self.cwd)],
            after=[[row(TARGET, status=None, state='working', cwd=self.cwd)]],
            transcripts=[{'session': TARGET, 'new_action': False}])
        code, receipt = self.dispatch(timeout='1')
        self.assertEqual(code, 1)
        self.assertEqual(receipt['result'], 'NOT_DELIVERED')
        self.assertIn('TIMEOUT', receipt['reason'])
        self.assertEqual(receipt['status'], None)
        self.assertEqual(receipt['state'], 'working')
        self.assertEqual(self.stops(), [])

    def test_working_null_with_action_at_timeout_is_delivered_without_stop(self):
        self.scenario(
            all=[row(TARGET, status='done', started='OLD', cwd=self.cwd)],
            after=[[row(TARGET, status=None, state='working', cwd=self.cwd)]],
            transcripts=[{'session': TARGET, 'new_action': True}])
        code, receipt = self.dispatch(timeout='1')
        self.assertEqual(code, 0, receipt)
        self.assertEqual(receipt['result'], 'DELIVERED')
        self.assertEqual(receipt['first_action']['tool'], 'Bash')
        self.assertEqual(receipt['status'], None)
        self.assertEqual(receipt['state'], 'working')
        self.assertEqual(self.stops(), [])

    def test_only_resumed_history_is_not_a_receipt(self):
        self.scenario(
            all=[row(TARGET, status='done', started='OLD', cwd=self.cwd)],
            after=[[row(TARGET, status='busy', cwd=self.cwd)]],
            transcripts=[{'session': TARGET, 'old_actions': 58, 'new_action': False}])
        code, receipt = self.dispatch()
        self.assertEqual(code, 1)
        self.assertEqual(receipt['result'], 'NOT_DELIVERED')
        self.assertEqual(receipt['reason'], 'NO_NEW_ACTION')
        self.assertIsNone(receipt['first_action'])
        self.assertEqual(self.stops(), [])

    def test_exact_name_resolves_to_the_full_uuid(self):
        self.scenario(
            all=[row(TARGET, name='pr365-dev', status='done', started='OLD', cwd=self.cwd),
                 row(OTHER, name='someone-else', status='done', started='OLD', cwd=self.cwd)],
            after=[[row(TARGET, cwd=self.cwd)]],
            transcripts=[{'session': TARGET, 'new_action': True}])
        code, receipt = self.dispatch('pr365-dev')
        self.assertEqual(code, 0, receipt)
        self.assertEqual(receipt['resolved_session_id'], TARGET)
        argv = self.launches()[0]['argv']
        self.assertEqual(argv[argv.index('--resume') + 1], TARGET)

    def test_short_id_prefix_or_miscased_uuid_is_refused_before_launch(self):
        # Even when the short id uniquely matches a live row and a transcript.
        folder = self.config / 'projects' / 'p'
        folder.mkdir(parents=True)
        (folder / (TARGET + '.jsonl')).write_text(json.dumps(
            {'type': 'user', 'cwd': self.cwd, 'timestamp': '2026-01-01T00:00:00.000Z'}) + '\n')
        self.scenario(all=[row(TARGET, status='done', started='OLD', cwd=self.cwd)],
                      after=[[row(TARGET, cwd=self.cwd)]],
                      transcripts=[{'session': TARGET, 'new_action': True}])
        for target in (TARGET[:8], TARGET[:13], TARGET[:-1], TARGET.upper()):
            with self.subTest(target=target):
                code, result = self.dispatch(target)
                self.assertEqual((code, result['reason']), (2, 'SESSION_ID_NOT_FULL'))
        self.assertEqual(self.launches(), [])
        self.assertEqual(self.stops(), [])

    def test_unknown_or_ambiguous_session_is_rejected_before_launch(self):
        self.scenario(all=[row(TARGET, status='done', started='OLD'),
                           row(OTHER, status='done', started='OLD')])
        for target, reason in (('ffffffff', 'SESSION_ID_NOT_FULL'),
                               ('no-such-name', 'SESSION_NOT_FOUND'),
                               ('aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee', 'SESSION_NOT_FOUND'),
                               ('claude-developer', 'SESSION_AMBIGUOUS')):
            with self.subTest(target=target):
                code, result = self.dispatch(target)
                self.assertEqual(code, 2)
                self.assertEqual(result['reason'], reason)
        self.assertEqual(self.launches(), [])

    def test_blocked_and_waiting_sessions_are_stopped(self):
        for status, state, reason in (('blocked', None, 'SESSION_BLOCKED'),
                                      ('waiting', None, 'SESSION_WAITING'),
                                      ('busy', 'blocked', 'SESSION_BLOCKED'),
                                      ('exited', None, 'SESSION_EXITED'),
                                      ('idle', 'failed', 'SESSION_FAILED'),
                                      ('idle', 'stopped', 'SESSION_STOPPED')):
            with self.subTest(status=status, state=state):
                self.reset()
                self.scenario(
                    all=[row(TARGET, status='done', started='OLD', cwd=self.cwd)],
                    after=[[row(TARGET, status=status, state=state, cwd=self.cwd)]],
                    transcripts=[{'session': TARGET, 'new_action': True}])
                code, receipt = self.dispatch()
                self.assertEqual(code, 1)
                self.assertEqual(receipt['reason'], reason)
                self.assertEqual(receipt['stop']['session'], TARGET)
                self.assertEqual(self.stops(), [TARGET])

    def test_vanished_or_never_started_session_fails(self):
        self.scenario(all=[row(TARGET, status='done', started='OLD', cwd=self.cwd)],
                      after=[[row(TARGET, cwd=self.cwd)], []])
        code, receipt = self.dispatch()
        self.assertEqual(code, 1)
        self.assertEqual(receipt['reason'], 'SESSION_GONE')
        self.assertEqual(self.stops(), [TARGET])
        self.reset()
        self.scenario(all=[row(TARGET, status='done', started='OLD', cwd=self.cwd)],
                      after=[[]])
        code, receipt = self.dispatch()
        self.assertEqual(code, 1)
        self.assertEqual(receipt['reason'], 'SESSION_NOT_STARTED')
        self.assertEqual(self.stops(), [])  # nothing identified, nothing to stop

    def test_copy_with_wrong_name_reports_the_actual_session(self):
        original = row(TARGET, status='idle', started='OLD', cwd=self.cwd)
        copy = row(COPY, name='pr370-fix-d6-worktree-ui', cwd=self.cwd)
        self.scenario(
            all=[original], before=[original], after=[[original, copy]],
            launch_stdout='Session already running; started a copy: ' + COPY[:8],
            transcripts=[{'session': COPY, 'old_actions': 58, 'new_action': True}])
        code, receipt = self.dispatch()
        self.assertEqual(code, 0, receipt)
        self.assertEqual(receipt['result'], 'DELIVERED')
        self.assertEqual(receipt['resolved_session_id'], TARGET)
        self.assertEqual(receipt['actual_session_id'], COPY)
        self.assertTrue(receipt['copy_started'])
        self.assertFalse(receipt['name_ok'])
        self.assertEqual(receipt['actual_name'], 'pr370-fix-d6-worktree-ui')
        self.assertEqual(receipt['session_ref_update_required']['to'], COPY)
        self.assertEqual(set(receipt['warnings']),
                         {'COPY_STARTED', 'NAME_MISMATCH', 'SESSION_REF_UPDATE_REQUIRED'})

    def test_busy_target_is_refused_as_a_second_writer(self):
        self.scenario(all=[row(TARGET, started='OLD', cwd=self.cwd)],
                      before=[row(TARGET, started='OLD', cwd=self.cwd)])
        code, result = self.dispatch()
        self.assertEqual(code, 2)
        self.assertEqual(result['reason'], 'SESSION_BUSY')
        self.assertEqual(self.launches(), [])

    def test_unregistered_task_role_and_prompt_are_rejected_before_launch(self):
        self.scenario(all=[row(TARGET, status='done', started='OLD', cwd=self.cwd)])
        code, result = self.dispatch(task='no-such-task')
        self.assertEqual((code, result['reason']), (2, 'D6_REGISTRATION_INVALID'))
        code, result = self.dispatch(name='Bad Name')
        self.assertEqual((code, result['reason']), (2, 'ROLE_NAME_INVALID'))
        code, result = self.run_tool('dispatch', '--session', TARGET, '--name', 'claude-developer',
                                     '--task-id', 'executor-task', '--prompt=--evil')
        self.assertEqual((code, result['reason']), (2, 'PROMPT_INVALID'))
        self.assertEqual(self.launches(), [])

    def test_failed_launch_is_not_delivered(self):
        self.scenario(all=[row(TARGET, status='done', started='OLD', cwd=self.cwd)],
                      launch_exit=1)
        code, receipt = self.dispatch()
        self.assertEqual((code, receipt['reason']), (1, 'LAUNCH_FAILED'))

    def test_new_session_starts_in_the_d6_root_with_its_own_uuid(self):
        self.scenario(after=[[row('NEW-SESSION', cwd=self.root)]],
                      transcripts=[{'session': 'NEW-SESSION', 'new_action': True}])
        code, receipt = self.run_tool('dispatch', '--new', '--name', 'claude-developer',
                                      '--task-id', 'executor-task', '--prompt', 'work')
        self.assertEqual(code, 0, receipt)
        [launch] = self.launches()
        argv = launch['argv']
        new_id = argv[argv.index('--session-id') + 1]
        self.assertTrue(dispatch_tool.UUID_RE.fullmatch(new_id))
        self.assertEqual(receipt['actual_session_id'], new_id)
        self.assertEqual(os.path.realpath(launch['cwd']), self.root)

    def test_reviewer_launch_has_no_file_editing_tools_and_developer_launch_is_unchanged(self):
        self.assertEqual(dispatch_tool.REVIEWER_DISALLOWED_TOOLS_ARG,
                         '--disallowedTools=Edit,Write,NotebookEdit')
        for name, expected in (('claude-reviewer', [dispatch_tool.REVIEWER_DISALLOWED_TOOLS_ARG]),
                               ('claude-developer', [])):
            with self.subTest(name=name):
                self.reset()
                self.scenario(after=[[row('NEW-SESSION', name=name, cwd=self.root)]],
                              transcripts=[{'session': 'NEW-SESSION', 'new_action': True}])
                code, receipt = self.run_tool('dispatch', '--new', '--name', name,
                                              '--task-id', 'executor-task', '--prompt', 'work')
                self.assertEqual(code, 0, receipt)
                [launch] = self.launches()
                argv = launch['argv']
                self.assertEqual([arg for arg in argv if arg.startswith('--disallowedTools')],
                                 expected)
                if expected:
                    self.assertLess(argv.index(expected[0]), argv.index('-n'))
                self.assertEqual(argv[-2:], ['--', 'work'])
                self.assertEqual(self.parsed(), [{'add_dirs': [self.root], 'prompt': 'work'}])

    def test_watch_returns_on_report_and_stops_a_blocked_session(self):
        report = self.home / 'report.md'
        self.scenario(after=[[row(TARGET)]])
        (self.home / 'state.json').write_text(json.dumps({'polls': 0, 'launch_ms': 1}))
        report.write_text('PASS')
        code, result = self.run_tool('watch', '--name', 'claude-developer',
                                     '--session-id', TARGET, '--report-file',
                                     str(report), '--timeout', '1', '--since', self.recent())
        self.assertEqual((code, result['result']), (0, 'REPORT_PRESENT'))
        report.unlink()
        self.scenario(after=[[row(TARGET, status='waiting')]])
        code, result = self.run_tool('watch', '--name', 'claude-developer',
                                     '--session-id', TARGET, '--report-file',
                                     str(report), '--timeout', '1')
        self.assertEqual((code, result['reason']), (1, 'SESSION_WAITING'))
        self.assertEqual(self.stops(), [TARGET])
        self.scenario(after=[[]])
        code, result = self.run_tool('watch', '--name', 'claude-developer',
                                     '--session-id', TARGET, '--report-file',
                                     str(report), '--timeout', '1')
        self.assertEqual((code, result['reason']), (1, 'SESSION_GONE'))

    def test_watch_stops_a_session_that_vanishes_while_waiting(self):
        report = self.home / 'report.md'
        (self.home / 'state.json').write_text(json.dumps({'polls': 0, 'launch_ms': 1}))
        # Healthy for two polls, then gone before any report is written.
        self.scenario(after=[[row(TARGET)], [row(TARGET)], []])
        code, result = self.run_tool('watch', '--name', 'claude-developer',
                                     '--session-id', TARGET, '--report-file',
                                     str(report), '--timeout', '5')
        self.assertEqual((code, result['result'], result['reason']),
                         (1, 'FAILED', 'SESSION_GONE'))
        self.assertEqual(result['status'], 'busy')  # last healthy state seen
        self.assertEqual(result['stop']['session'], TARGET)
        self.assertEqual(self.stops(), [TARGET])
        self.assertTrue(all(call['config_dir'] == str(self.config) for call in self.calls()))
        polls = [call for call in self.calls() if call['argv'][:2] == ['agents', '--json']]
        self.assertEqual(len(polls), 3)

    def test_watch_requires_explicit_worker_role(self):
        with self.assertRaises(SystemExit) as caught:
            dispatch_tool._main(['--claude', str(self.claude), 'watch',
                                 '--session-id', TARGET, '--report-file',
                                 str(self.home / 'report.md')])
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(self.calls(), [])

    def test_watch_rejects_unknown_worker_role_before_cli_calls(self):
        code, result = self.run_tool('watch', '--name', 'unknown-role',
                                     '--session-id', TARGET, '--report-file',
                                     str(self.home / 'report.md'), '--timeout', '1')
        self.assertEqual((code, result['reason']), (2, 'WORKER_UNKNOWN'))
        self.assertEqual(self.calls(), [])

    def test_watch_uses_worker_account_and_stops_there_instead_of_pm_account(self):
        worker_dir = self.home / 'worker-account'
        worker_dir.mkdir()
        # watch does not launch this session, so the fake CLI serves its
        # current state from `before`; `after` is used only after a launch.
        self.scenario(account_dir=worker_dir, before=[row(TARGET, status='waiting')])
        def configured_role(role):
            return {'role': role, 'provider': 'claude',
                    'config_dir': str(worker_dir if role == 'claude-developer'
                                     else self.config)}

        with patch.object(dispatch_tool.role_config, 'resolve_role',
                          side_effect=configured_role), \
                patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': ''}):
            _, pm_env = dispatch_tool._account_environment('pm')
            self.assertEqual(pm_env['CLAUDE_CONFIG_DIR'], str(self.config))
            code, result = self.run_tool('watch', '--name', 'claude-developer',
                                         '--session-id', TARGET, '--report-file',
                                         str(self.home / 'worker-report.md'), '--timeout', '1')
        self.assertEqual((code, result['reason']), (1, 'SESSION_WAITING'))
        [stop] = [call for call in self.calls() if call['argv'][:1] == ['stop']]
        self.assertEqual(stop['config_dir'], str(worker_dir))
        self.assertTrue(all(call['config_dir'] == str(worker_dir) for call in self.calls()))

    def test_resumed_session_keeping_its_old_start_time_is_still_identified(self):
        # Not active before the launch, active after it: that is this launch.
        self.scenario(all=[row(TARGET, status='done', started='OLD', cwd=self.cwd)],
                      after=[[row(TARGET, started='OLD', cwd=self.cwd)]],
                      transcripts=[{'session': TARGET, 'new_action': True}])
        code, receipt = self.dispatch()
        self.assertEqual(code, 0, receipt)
        self.assertEqual(receipt['actual_session_id'], TARGET)
        self.assertFalse(receipt['copy_started'])

    def test_transcript_is_read_incrementally_and_waits_for_complete_lines(self):
        folder = self.config / 'projects' / 'p'
        folder.mkdir(parents=True)
        path = folder / (TARGET + '.jsonl')
        old = {'type': 'assistant', 'timestamp': '2026-01-01T00:00:00.000Z',
               'message': {'content': [{'type': 'tool_use', 'name': 'Old'}]}}
        new = dict(old, timestamp='2026-10-26T00:00:00.000Z',
                   message={'content': [{'type': 'tool_use', 'name': 'Read'}]})
        since = 1792972800000  # 2026-10-26T00:00:00Z
        line = json.dumps(new)
        path.write_text(json.dumps(old) + '\n' + line[:10])
        offsets = {}
        self.assertIsNone(dispatch_tool.first_action_since(TARGET, since, offsets))
        with open(path, 'a') as stream:
            stream.write(line[10:] + '\n')
        self.assertEqual(dispatch_tool.first_action_since(TARGET, since, offsets),
                         {'tool': 'Read', 'timestamp': '2026-10-26T00:00:00.000Z'})

    def test_non_dict_message_in_transcript_is_skipped(self):
        folder = self.config / 'projects' / 'p'
        folder.mkdir(parents=True)
        path = folder / (TARGET + '.jsonl')
        stamp = '2026-10-26T00:00:00.000Z'
        odd = [{'type': 'assistant', 'timestamp': stamp, 'message': value}
               for value in ('text', ['list'], 7, True)]
        good = {'type': 'assistant', 'timestamp': stamp,
                'message': {'content': [{'type': 'tool_use', 'name': 'Read'}]}}
        path.write_text(''.join(json.dumps(entry) + '\n' for entry in odd + [good]))
        since = 1792972800000  # 2026-10-26T00:00:00Z
        self.assertEqual(dispatch_tool.first_action_since(TARGET, since, {}),
                         {'tool': 'Read', 'timestamp': stamp})

    def test_prompt_the_cli_did_not_receive_is_not_a_receipt(self):
        # The real #364 case: `backgrounded ... (idle — send a prompt to start)`, exit 0.
        self.scenario(all=[row(TARGET, status='done', started='OLD', cwd=self.cwd)],
                      after=[[row(TARGET, status='idle', cwd=self.cwd)]], drop_prompt=True,
                      transcripts=[{'session': TARGET, 'old_actions': 3, 'new_action': True}])
        code, receipt = self.dispatch()
        self.assertEqual(code, 1)
        self.assertEqual(receipt['reason'], 'PROMPT_NOT_DELIVERED')
        self.assertEqual(self.stops(), [TARGET])

    def test_a_new_session_nearby_is_never_adopted(self):
        # The target is idle and the launch names no copy; another agent's
        # new background session in the same cwd must not become ours.
        original = row(TARGET, status='idle', started='OLD', cwd=self.cwd)
        neighbour = row(COPY, name='claude-reviewer', cwd=self.cwd)
        self.scenario(all=[original], before=[original], after=[[original, neighbour]],
                      transcripts=[{'session': COPY, 'new_action': True}])
        code, receipt = self.dispatch()
        self.assertEqual(code, 1)
        self.assertEqual(receipt['reason'], 'SESSION_NOT_STARTED')
        self.assertIsNone(receipt['actual_session_id'])
        self.assertEqual(self.stops(), [])

    def test_tool_use_one_second_before_launch_is_not_a_receipt(self):
        self.scenario(all=[row(TARGET, status='done', started='OLD', cwd=self.cwd)],
                      after=[[row(TARGET, cwd=self.cwd)]],
                      transcripts=[{'session': TARGET, 'pre_launch_action': True}])
        code, receipt = self.dispatch()
        self.assertEqual(code, 1)
        self.assertEqual(receipt['reason'], 'NO_NEW_ACTION')
        self.assertIsNone(receipt['first_action'])

    def test_finished_session_with_null_status_is_delivered_without_a_stop(self):
        # `claude agents --json` reports most finished sessions as status=None, state=done.
        self.scenario(all=[row(TARGET, status='done', started='OLD', cwd=self.cwd)],
                      after=[[row(TARGET, status=None, state='done', cwd=self.cwd)]],
                      transcripts=[{'session': TARGET, 'new_action': True}])
        code, receipt = self.dispatch()
        self.assertEqual((code, receipt['result']), (0, 'DELIVERED'), receipt)
        self.assertEqual(self.stops(), [])

    def test_done_session_is_delivered_only_if_it_acted(self):
        self.scenario(all=[row(TARGET, status='done', started='OLD', cwd=self.cwd)],
                      after=[[row(TARGET, status='idle', state='done', cwd=self.cwd)]],
                      transcripts=[{'session': TARGET, 'new_action': True}])
        code, receipt = self.dispatch()
        self.assertEqual((code, receipt['result']), (0, 'DELIVERED'))
        self.reset()
        for path in (self.config / 'projects').glob('*/*.jsonl'):
            path.unlink()
        self.scenario(all=[row(TARGET, status='done', started='OLD', cwd=self.cwd)],
                      after=[[row(TARGET, status='idle', state='done', cwd=self.cwd)]])
        code, receipt = self.dispatch()
        self.assertEqual((code, receipt['reason']), (1, 'SESSION_DONE'))
        self.assertEqual(self.stops(), [])  # already ended: nothing to stop

    def test_watch_ignores_a_report_written_before_the_dispatch(self):
        report = self.home / 'report.md'
        report.write_text('old round')
        os.utime(report, (1767225600, 1767225600))  # 2026-01-01
        (self.home / 'state.json').write_text(json.dumps({'polls': 0, 'launch_ms': 1}))
        self.scenario(after=[[row(TARGET)]])
        for extra in ([], ['--since', self.recent()]):
            with self.subTest(extra=extra):
                code, result = self.run_tool('watch', '--name', 'claude-developer',
                                             '--session-id', TARGET, '--report-file',
                                             str(report), '--timeout', '1', *extra)
                self.assertEqual((code, result['reason']), (1, 'REPORT_TIMEOUT'))
        since = self.recent()
        stale = dispatch_tool._entry_ms({'timestamp': since}) / 1000 - 1
        os.utime(report, (stale, stale))  # one second before the dispatch
        code, result = self.run_tool('watch', '--name', 'claude-developer',
                                     '--session-id', TARGET, '--report-file',
                                     str(report), '--timeout', '1', '--since', since)
        self.assertEqual((code, result['reason']), (1, 'REPORT_TIMEOUT'))
        report.write_text('new round')  # mtime is now
        code, result = self.run_tool('watch', '--name', 'claude-developer',
                                     '--session-id', TARGET, '--report-file',
                                     str(report), '--timeout', '1', '--since', self.recent())
        self.assertEqual((code, result['result']), (0, 'REPORT_PRESENT'))

    def test_transcripts_are_read_from_claude_config_dir_only(self):
        other = self.home / 'other-account'
        (other / 'projects' / 'p').mkdir(parents=True)
        (other / 'projects' / 'p' / (TARGET + '.jsonl')).write_text('{}\n')
        self.scenario()
        code, result = self.dispatch(TARGET)
        self.assertEqual((code, result['reason']), (2, 'SESSION_NOT_FOUND'))
        with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(other)}):
            self.assertEqual(dispatch_tool.transcript_paths(TARGET),
                             [str(other / 'projects' / 'p' / (TARGET + '.jsonl'))])

    # -- #367 slice 2: no launch under another account ----------------------

    def test_cross_account_launch_is_refused_before_gate_or_cli(self):
        other = self.home / 'other-account'
        other.mkdir()
        self.scenario(all=[row(TARGET, status='done', started='OLD', cwd=self.cwd)],
                      after=[[row(TARGET, cwd=self.cwd)]],
                      transcripts=[{'session': TARGET, 'new_action': True}])
        for value in (str(other), 'relative/dir'):
            with self.subTest(value=value), \
                    patch.object(dispatch_tool.worker_dispatch, 'evaluate') as gate:
                code, result = self.run_tool(
                    'dispatch', '--session', TARGET, '--name', 'claude-developer',
                    '--task-id', 'executor-task', '--prompt', 'work',
                    '--worker-config-dir', value)
                self.assertEqual((code, result['result'], result['reason']),
                                 (2, 'REJECTED', 'CROSS_ACCOUNT_LAUNCH_REFUSED'))
                gate.assert_not_called()
        self.assertEqual(self.calls(), [])  # no agents listing, no launch
        self.assertEqual(list(other.iterdir()), [])

    def test_same_account_worker_config_dir_keeps_dispatch_unchanged(self):
        self.scenario(all=[row(TARGET, status='done', started='OLD', cwd=self.cwd)],
                      after=[[row(TARGET, cwd=self.cwd)]],
                      transcripts=[{'session': TARGET, 'new_action': True}])
        code, receipt = self.run_tool(
            'dispatch', '--session', TARGET, '--name', 'claude-developer',
            '--task-id', 'executor-task', '--prompt', 'work',
            '--worker-config-dir', str(self.config) + '/')
        self.assertEqual((code, receipt['result']), (0, 'DELIVERED'), receipt)
        self.assertEqual(receipt['first_action']['tool'], 'Bash')
        self.assertEqual(len(self.launches()), 1)


SESSION_B = '4d34a82a-0000-4000-8000-0000000000b2'
SESSION_A = '4d34a82a-0000-4000-8000-0000000000a1'


class CrossAccountDeliveryReceiptTests(unittest.TestCase):
    """``receipt``: slice-1 claim evidence read from an explicit worker account dir.

    PM is account A (the caller's CLAUDE_CONFIG_DIR), the Developer is
    account B. A fake ``claude`` on PATH records any invocation; none may happen.
    """

    def setUp(self):
        self.fx = assignment_fixtures.DeveloperAssignmentInboxTests(
            'test_1_assign_then_cross_account_claim_is_visible_to_pm')
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        for account in (self.fx.account_a, self.fx.account_b):
            os.makedirs(account, mode=0o700, exist_ok=True)
        bin_dir = self.fx.base / 'bin'
        bin_dir.mkdir()
        self.claude_log = self.fx.base / 'claude-called'
        self.claude = bin_dir / 'claude'
        self.claude.write_text('#!/bin/sh\necho "$@" >> "%s"\nexit 9\n' % self.claude_log)
        self.claude.chmod(0o700)
        env = patch.dict(os.environ, {'PATH': str(bin_dir) + os.pathsep +
                                      os.environ.get('PATH', '')})
        env.start()
        self.addCleanup(env.stop)
        store = patch.object(dispatch_tool, 'TaskStore', lambda: self.fx.store)
        store.start()
        self.addCleanup(store.stop)

    def receipt(self, assignment_id, worker_dir=None, *extra):
        out = StringIO()
        argv = ['--claude', str(self.claude), '--poll-interval', '0.01', 'receipt',
                '--assignment-id', assignment_id,
                '--worker-config-dir', worker_dir or self.fx.account_b] + list(extra)
        with redirect_stdout(out):
            code = dispatch_tool._main(argv)
        return code, json.loads(out.getvalue())

    def claimed(self):
        aid = self.fx.assign()['assignment_id']
        path = self.fx.transcript(SESSION_B, self.fx.account_b)
        self.fx.claim(aid, session=SESSION_B)
        return aid, path

    def snapshot(self, account):
        return sorted((str(path), path.lstat().st_mtime_ns, path.lstat().st_size)
                      for path in Path(account).rglob('*'))

    def tearDown(self):
        self.assertFalse(self.claude_log.exists(), 'claude must never be invoked')

    def test_claimed_by_worker_account_is_delivered_read_only(self):
        aid, path = self.claimed()
        later = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(time.time() + 3600))
        with open(path, 'a') as stream:
            stream.write(json.dumps({'type': 'assistant', 'timestamp': later, 'message': {
                'content': [{'type': 'tool_use', 'name': 'Read'}]}}) + '\n')
        before = self.snapshot(self.fx.account_b)
        code, result = self.receipt(aid)
        self.assertEqual((code, result['result'], result['reason']), (0, 'DELIVERED', None))
        self.assertEqual(result['claim']['session_id'], SESSION_B)
        self.assertTrue(result['account_match'])
        self.assertTrue(result['transcript_verified'])
        self.assertFalse(result['launched'])
        self.assertEqual(result['first_action'], {'tool': 'Read', 'timestamp': later})
        self.assertEqual(result['d6_task_id'], assignment_fixtures.D6)
        self.assertEqual(self.snapshot(self.fx.account_b), before)
        self.assertEqual(list(Path(self.fx.account_a).iterdir()), [])

    def test_unclaimed_live_assignment_is_not_delivered_even_after_bounded_wait(self):
        aid = self.fx.assign()['assignment_id']
        self.fx.transcript(SESSION_B, self.fx.account_b)  # a transcript alone is no claim
        for extra in ((), ('--wait', '--timeout', '0.2')):
            with self.subTest(extra=extra):
                started = time.monotonic()
                code, result = self.receipt(aid, None, *extra)
                self.assertLess(time.monotonic() - started, 5)
                self.assertEqual((code, result['result'], result['reason']),
                                 (1, 'NOT_DELIVERED', 'ASSIGNMENT_NOT_CLAIMED'))
                self.assertIsNone(result['claim'])

    def test_wait_returns_delivered_once_the_worker_claims(self):
        aid = self.fx.assign()['assignment_id']
        self.fx.transcript(SESSION_B, self.fx.account_b)
        real = dispatch_tool._assignment
        calls = []

        def claim_on_second_poll(store, assignment_id):
            calls.append(assignment_id)
            if len(calls) == 2:
                self.fx.claim(aid, session=SESSION_B)
            return real(store, assignment_id)

        with patch.object(dispatch_tool, '_assignment', side_effect=claim_on_second_poll):
            code, result = self.receipt(aid, None, '--wait', '--timeout', '5')
        self.assertEqual((code, result['result']), (0, 'DELIVERED'), result)
        self.assertEqual(len(calls), 2)

    def test_expired_without_claim_is_unclaimed(self):
        past = datetime.now(timezone.utc) - timedelta(minutes=20)
        aid = self.fx.assign(now=past)['assignment_id']
        code, result = self.receipt(aid)
        self.assertEqual((code, result['result'], result['reason']),
                         (1, 'UNCLAIMED', 'ASSIGNMENT_UNCLAIMED'))

    def test_pm_account_dir_does_not_match_the_worker_claim(self):
        aid, _ = self.claimed()
        self.fx.transcript(SESSION_B, self.fx.account_a)  # same session id under the PM account
        code, result = self.receipt(aid, self.fx.account_a)
        self.assertEqual((code, result['result'], result['reason']),
                         (1, 'NOT_DELIVERED', 'WORKER_ACCOUNT_MISMATCH'))
        self.assertFalse(result['account_match'])
        self.assertFalse(result['transcript_verified'])

    def test_missing_or_symlinked_worker_transcript_is_refused(self):
        aid, path = self.claimed()
        real = self.fx.base / 'elsewhere.jsonl'
        real.write_text('{}\n')
        path.unlink()
        code, result = self.receipt(aid)
        self.assertEqual((code, result['reason']), (1, 'WORKER_TRANSCRIPT_INVALID'))
        os.symlink(real, path)
        code, result = self.receipt(aid)
        self.assertEqual((code, result['reason']), (1, 'WORKER_TRANSCRIPT_INVALID'))
        self.assertFalse(result['transcript_verified'])
        path.unlink()
        project = path.parent
        moved = self.fx.base / 'moved-project'
        os.rename(project, moved)
        os.symlink(moved, project)  # a symlinked project directory is refused too
        (moved / path.name).write_text('{}\n')
        code, result = self.receipt(aid)
        self.assertEqual((code, result['reason']), (1, 'WORKER_TRANSCRIPT_INVALID'))

    def test_invalid_worker_dir_or_assignment_is_rejected(self):
        aid, _ = self.claimed()
        link = self.fx.base / 'account-link'
        os.symlink(self.fx.account_b, link)
        for worker_dir in (str(link), 'relative/account', str(self.fx.base / 'absent')):
            with self.subTest(worker_dir=worker_dir):
                code, result = self.receipt(aid, worker_dir)
                self.assertEqual((code, result['result'], result['reason']),
                                 (2, 'REJECTED', 'WORKER_CONFIG_DIR_INVALID'))
        for bad, reason in (('f' * 32, 'ASSIGNMENT_NOT_FOUND'),
                            (aid.upper(), 'ASSIGNMENT_ID_INVALID'),
                            (aid[:8], 'ASSIGNMENT_ID_INVALID')):
            with self.subTest(assignment_id=bad):
                code, result = self.receipt(bad)
                self.assertEqual((code, result['reason']), (2, reason))
        code, result = self.receipt(aid, None, '--wait', '--timeout', '601')
        self.assertEqual((code, result['reason']), (2, 'ARGUMENT_INVALID'))


if __name__ == '__main__':
    unittest.main()
