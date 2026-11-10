"""Tests verifying Reviewer and PM session environments isolate AI credentials."""

import io
import json
import os
import shutil
import subprocess

import pytest

from llmtool import pm_runtime, pm_runtime_codex, review_dispatch
from llmtool.errors import LLMToolError


SYNTHETIC_TOKEN = 'synthetic-not-a-token'


def test_codex_reviewer_ai_identity(monkeypatch, tmp_path):
    """Codex reviewer session environment passes through ai_github_env."""
    monkeypatch.setenv('GH_TOKEN', SYNTHETIC_TOKEN)
    monkeypatch.setenv('GITHUB_TOKEN', SYNTHETIC_TOKEN)
    monkeypatch.setenv('SSH_AUTH_SOCK', SYNTHETIC_TOKEN)
    monkeypatch.setenv('GH_CONFIG_DIR', '/other')

    captured_env = None

    def fake_which(cmd, **kwargs):
        return '/usr/local/bin/codex'

    def fake_run(args, **kwargs):
        nonlocal captured_env
        captured_env = kwargs.get('env')
        return subprocess.CompletedProcess(
            args=args, returncode=1, stdout=b'', stderr=b''
        )

    monkeypatch.setattr(shutil, 'which', fake_which)
    monkeypatch.setattr(subprocess, 'run', fake_run)

    task = {
        'task_id': 'task-1',
        'run_id': 'run-1',
        'task_binding': {'contract_digest': 'digest-1'},
    }
    fake_config_dir = str(tmp_path / 'codex-config')

    try:
        review_dispatch._dispatch_codex(
            reviewer_root=str(tmp_path),
            prompt='Review prompt',
            task=task,
            head='head-1',
            config_dir=fake_config_dir,
        )
    except LLMToolError:
        pass

    assert captured_env is not None
    home = captured_env['HOME']
    assert captured_env['GH_CONFIG_DIR'] == f'{home}/.config/gh-ai'
    assert 'GH_TOKEN' not in captured_env
    assert 'GITHUB_TOKEN' not in captured_env
    assert 'SSH_AUTH_SOCK' not in captured_env
    assert captured_env['CODEX_HOME'] == fake_config_dir
    assert 'PATH' in captured_env
    assert captured_env['LC_ALL'] == 'C'
    assert captured_env['NO_COLOR'] == '1'
    assert captured_env['GIT_CONFIG_COUNT'] == '3'


def test_claude_reviewer_ai_identity(monkeypatch, tmp_path):
    """Claude reviewer session environment passes through ai_github_env."""
    monkeypatch.setenv('GH_TOKEN', SYNTHETIC_TOKEN)
    monkeypatch.setenv('GITHUB_TOKEN', SYNTHETIC_TOKEN)
    monkeypatch.setenv('SSH_AUTH_SOCK', SYNTHETIC_TOKEN)
    monkeypatch.setenv('GH_CONFIG_DIR', '/other')

    captured_env = None
    fake_config_dir = str(tmp_path / 'claude-config')
    fake_executor = type('Executor', (), {'common_dir': str(tmp_path / 'common')})()

    monkeypatch.setattr(
        review_dispatch,
        '_preflight',
        lambda *args, **kwargs: (None, fake_executor, 'head-1', 'base-1', 'claude-reviewer'),
    )
    monkeypatch.setattr(
        review_dispatch,
        '_policy',
        lambda *args, **kwargs: type('Gate', (), {'allowed': True})(),
    )
    monkeypatch.setattr(
        review_dispatch.role_config,
        'resolve_role',
        lambda role, **kwargs: {'config_dir': fake_config_dir, 'source': 'roles', 'account': 'acc-1'},
    )
    monkeypatch.setattr(
        review_dispatch,
        '_resolve_github_login',
        lambda: 'ddavidccatssun',
    )

    fake_store = type('Store', (), {
        'get': lambda self, tid: {'record_sha256': 'sha-1'},
        'compare_and_update': lambda self, tid, **kw: True,
    })()

    def fake_dispatch_session(*, task_id, prompt, repo_root, env):
        nonlocal captured_env
        captured_env = env
        return 'session-123'

    review_dispatch.dispatch_review(
        store=fake_store,
        task_id='task-1',
        executor_task='exec-1',
        pr_number=1,
        version=1,
        create_d6=lambda **kw: None,
        dispatch_session=fake_dispatch_session,
        bind_session=lambda **kw: None,
        prompt_renderer=lambda *args: 'prompt',
        reviewer='claude-reviewer',
    )

    assert captured_env is not None
    home = captured_env.get('HOME') or os.path.expanduser('~')
    assert captured_env['GH_CONFIG_DIR'] == f'{home}/.config/gh-ai'
    assert 'GH_TOKEN' not in captured_env
    assert 'GITHUB_TOKEN' not in captured_env
    assert 'SSH_AUTH_SOCK' not in captured_env
    assert captured_env['CLAUDE_CONFIG_DIR'] == fake_config_dir
    assert captured_env['GIT_CONFIG_COUNT'] == '3'


def test_claude_pm_ai_identity(monkeypatch, tmp_path):
    """Claude PM session environment passes through ai_github_env."""
    monkeypatch.setenv('GH_TOKEN', SYNTHETIC_TOKEN)
    monkeypatch.setenv('GITHUB_TOKEN', SYNTHETIC_TOKEN)
    monkeypatch.setenv('SSH_AUTH_SOCK', SYNTHETIC_TOKEN)
    monkeypatch.setenv('GH_CONFIG_DIR', '/other')

    state_root = tmp_path / 'state'
    state_root.mkdir()
    cwd_dir = tmp_path / 'repo'
    cwd_dir.mkdir()
    fake_config_dir = str(tmp_path / 'claude-pm-config')
    pm_session_id = '11111111-2222-3333-4444-555555555555'

    binding = {
        'runtime': 'claude-code',
        'account': 'pm-acc',
        'native_session_id': pm_session_id,
        'cwd': str(cwd_dir),
        'dedicated': True,
        'created_at': '2026-11-10T00:00:00+00:00',
        'session_started': True,
    }
    binding_file = state_root / f'{pm_session_id}.binding.json'
    binding_file.write_text(json.dumps(binding), encoding='utf-8')
    current_ptr = state_root / 'current-binding.json'
    current_ptr.write_text(json.dumps({'native_session_id': pm_session_id}), encoding='utf-8')

    monkeypatch.setattr(
        pm_runtime.role_config,
        'resolve_role',
        lambda role, **kwargs: {'provider': 'claude', 'account': 'pm-acc', 'config_dir': fake_config_dir},
    )
    monkeypatch.setattr(
        pm_runtime.active_pm_inbox,
        'observe_claude_session',
        lambda session_id, cwd, env: str(cwd_dir),
    )
    monkeypatch.setattr(
        pm_runtime.active_pm_inbox,
        'claude_session_file',
        lambda session_id, cwd, env: tmp_path / 'transcript' / 'sub' / 'file.jsonl',
    )
    monkeypatch.setattr(
        pm_runtime,
        '_session_live_elsewhere',
        lambda *args, **kwargs: False,
    )

    captured_env = None

    class FakeChild:
        def __init__(self):
            self.stdin = io.BytesIO()
            self.stdout = []
            self.stderr = None
            self.pid = 99999
        def poll(self):
            return 0
        def terminate(self):
            pass
        def wait(self, timeout=None):
            return 0

    def fake_runner(args, cwd, env):
        nonlocal captured_env
        captured_env = env
        return FakeChild()

    runtime = pm_runtime.PMRuntime(
        pm_session_id,
        cwd=str(cwd_dir),
        runtime='claude-code',
        runner=fake_runner,
        state_root=state_root,
    )
    try:
        runtime.open()
    finally:
        runtime.close(timeout=0)

    assert captured_env is not None
    home = captured_env.get('HOME') or os.path.expanduser('~')
    assert captured_env['GH_CONFIG_DIR'] == f'{home}/.config/gh-ai'
    assert 'GH_TOKEN' not in captured_env
    assert 'GITHUB_TOKEN' not in captured_env
    assert 'SSH_AUTH_SOCK' not in captured_env
    assert captured_env['CLAUDE_CONFIG_DIR'] == fake_config_dir
    assert captured_env['GIT_CONFIG_COUNT'] == '3'


def test_codex_pm_ai_identity(monkeypatch, tmp_path):
    """Codex PM session environment passes through ai_github_env."""
    monkeypatch.setenv('GH_TOKEN', SYNTHETIC_TOKEN)
    monkeypatch.setenv('GITHUB_TOKEN', SYNTHETIC_TOKEN)
    monkeypatch.setenv('SSH_AUTH_SOCK', SYNTHETIC_TOKEN)
    monkeypatch.setenv('GH_CONFIG_DIR', '/other')

    state_root = tmp_path / 'state'
    state_root.mkdir()
    cwd_dir = tmp_path / 'repo'
    cwd_dir.mkdir()
    fake_config_dir = str(tmp_path / 'codex-pm-config')
    pm_session_id = '11111111-2222-3333-4444-555555555555'

    binding = {
        'runtime': 'codex',
        'account': 'codex-acc',
        'native_session_id': pm_session_id,
        'cwd': str(cwd_dir),
        'dedicated': True,
        'created_at': '2026-11-10T00:00:00+00:00',
        'session_started': True,
    }
    binding_file = state_root / f'{pm_session_id}.binding.json'
    binding_file.write_text(json.dumps(binding), encoding='utf-8')

    monkeypatch.setattr(
        pm_runtime_codex.role_config,
        'resolve_role',
        lambda role, **kwargs: {'provider': 'codex', 'account': 'codex-acc', 'config_dir': fake_config_dir},
    )

    owner = type('FakeOwner', (), {
        'environ': None,
        'state_root': state_root,
        'pm_session_id': pm_session_id,
        'account': 'codex-acc',
        'cwd': str(cwd_dir),
    })()

    codex_runtime = pm_runtime_codex.CodexRuntime(owner)
    env = codex_runtime._environment()

    assert env is not None
    home = env.get('HOME') or os.path.expanduser('~')
    assert env['GH_CONFIG_DIR'] == f'{home}/.config/gh-ai'
    assert 'GH_TOKEN' not in env
    assert 'GITHUB_TOKEN' not in env
    assert 'SSH_AUTH_SOCK' not in env
    assert env['CODEX_HOME'] == fake_config_dir
    assert 'PATH' in env
    assert env['GIT_CONFIG_COUNT'] == '3'
