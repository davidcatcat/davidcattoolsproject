"""Fail closed when llmtool is running from stale or dirty source."""

import os
from pathlib import Path
import subprocess
import warnings

import llmtool

from .errors import LLMToolError


_TIMEOUT_SECONDS = 5
_ERROR_CODES = {
    'STALE': 'LLMTOOL_SOURCE_STALE',
    'DIRTY': 'LLMTOOL_SOURCE_DIRTY',
    'UNKNOWN': 'LLMTOOL_SOURCE_UNKNOWN',
}


def source_freshness():
    """Return OK, STALE, DIRTY, or UNKNOWN for the installed package checkout."""
    try:
        package_file = Path(llmtool.__file__).resolve()
        # llmtool lives at <repo>/tools/llmtool/llmtool/__init__.py.
        repo_root = package_file.parents[3]
        common = dict(cwd=str(repo_root), capture_output=True, text=True,
                      timeout=_TIMEOUT_SECONDS, check=False)
        top = subprocess.run(['git', 'rev-parse', '--show-toplevel'], **common)
        if top.returncode != 0 or Path(top.stdout.strip()).resolve() != repo_root:
            return 'UNKNOWN'
        try:
            subprocess.run(
                ['git', 'fetch', '--quiet', '--no-tags', 'origin', 'main'],
                cwd=str(repo_root), capture_output=True, text=True,
                timeout=_TIMEOUT_SECONDS, check=False,
                env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
        except (OSError, subprocess.TimeoutExpired):
            pass
        head = subprocess.run(['git', 'rev-parse', '--verify', 'HEAD'], **common)
        origin_main = subprocess.run(
            ['git', 'rev-parse', '--verify', 'refs/remotes/origin/main'], **common)
        if head.returncode != 0 or origin_main.returncode != 0:
            return 'UNKNOWN'
        status = subprocess.run(['git', 'status', '--porcelain'], **common)
        if status.returncode != 0:
            return 'UNKNOWN'
        if status.stdout:
            return 'DIRTY'
        if head.stdout.strip() != origin_main.stdout.strip():
            return 'STALE'
        return 'OK'
    except (OSError, RuntimeError, ValueError, IndexError, subprocess.SubprocessError):
        return 'UNKNOWN'


def require_fresh_source():
    """Refuse stale source unless an explicit emergency/test override is set."""
    status = source_freshness()
    if status == 'OK':
        return status
    code = _ERROR_CODES.get(status, _ERROR_CODES['UNKNOWN'])
    if os.environ.get('LLMTOOL_ALLOW_STALE_SOURCE') == '1':
        warnings.warn(f'{code}: source freshness check overridden', RuntimeWarning,
                      stacklevel=2)
        return status
    error = LLMToolError(code)
    error.completion_code = code
    raise error
