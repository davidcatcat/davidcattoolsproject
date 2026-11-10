"""Fail closed when llmtool is running from stale or dirty source.

With an approved install pin, STALE means behind the approved SHA. A missing
pin keeps the origin/main check; deleting the pin is the rollback. A same-user
AI can still set the override, edit the pin, or move the checkout, so this is
an accident guard, not a security boundary.
"""

import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import warnings

import llmtool

from . import github_identity
from .errors import LLMToolError


_TIMEOUT_SECONDS = 5
_ERROR_CODES = {
    'STALE': 'LLMTOOL_SOURCE_STALE',
    'UNAPPROVED': 'LLMTOOL_SOURCE_UNAPPROVED',
    'DIRTY': 'LLMTOOL_SOURCE_DIRTY',
    'UNKNOWN': 'LLMTOOL_SOURCE_UNKNOWN',
}
_SHA = re.compile(r'[0-9a-f]{40}\Z')
_PIN_FIELDS = {'schema_version', 'approved_sha', 'previous_sha', 'approved_at'}


def pin_path():
    """Return the per-user approved-install pin path."""
    return Path.home() / '.local/state/llmtool/approved-install.json'


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate pin field')
        value[key] = item
    return value


def read_pin():
    """Read and validate the optional owner-only approved install pin."""
    path = pin_path()
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError('invalid pin path') from exc

    try:
        parent_info = path.parent.lstat()
        if (not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != os.getuid() or
                stat.S_IMODE(parent_info.st_mode) & 0o022):
            raise ValueError('invalid pin directory')

        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        fd = os.open(path, flags)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                    stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or
                    info.st_size > 4096):
                raise ValueError('invalid pin file')
            with os.fdopen(fd, 'rb') as stream:
                fd = -1
                raw = stream.read(4097)
        finally:
            if fd >= 0:
                os.close(fd)
    except OSError as exc:
        raise ValueError('invalid pin file') from exc
    if len(raw) > 4096:
        raise ValueError('oversize pin file')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('invalid pin content') from exc
    if (type(value) is not dict or set(value) != _PIN_FIELDS or
            type(value.get('schema_version')) is not int or value['schema_version'] != 1 or
            type(value.get('approved_sha')) is not str or
            _SHA.fullmatch(value['approved_sha']) is None or
            (value.get('previous_sha') is not None and
             (type(value['previous_sha']) is not str or
              _SHA.fullmatch(value['previous_sha']) is None)) or
            type(value.get('approved_at')) is not int or value['approved_at'] < 0):
        raise ValueError('invalid pin content')
    return value


def _fetch_origin_main(repo_root):
    """Fetch origin/main, stopping the full process group if it times out."""
    home = os.environ.get('HOME') or os.path.expanduser('~')
    try:
        env = github_identity.ai_github_env(os.environ, home)
    except (ValueError, TypeError):
        return
    env['GIT_TERMINAL_PROMPT'] = '0'
    argv = ['git', 'fetch', '--quiet', '--no-tags', 'origin', 'main']
    try:
        process = subprocess.Popen(
            argv, cwd=str(repo_root), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env,
            start_new_session=True)
    except OSError:
        return
    try:
        process.communicate(timeout=_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()


def source_freshness():
    """Return OK, STALE, UNAPPROVED, DIRTY, or UNKNOWN for the installed package checkout."""
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
            pin = read_pin()
        except (ValueError, OSError):
            return 'UNKNOWN'
        if pin is not None:
            status = subprocess.run(['git', 'status', '--porcelain'], **common)
            if status.returncode != 0:
                return 'UNKNOWN'
            if status.stdout:
                return 'DIRTY'
            head = subprocess.run(['git', 'rev-parse', '--verify', 'HEAD'], **common)
            if head.returncode != 0:
                return 'UNKNOWN'
            head_sha = head.stdout.strip()
            if head_sha == pin['approved_sha']:
                return 'OK'
            ancestry = subprocess.run(
                ['git', 'merge-base', '--is-ancestor', pin['approved_sha'], 'HEAD'],
                **common)
            if ancestry.returncode == 0:
                return 'UNAPPROVED'
            if ancestry.returncode == 1:
                return 'STALE'
            return 'UNKNOWN'
        _fetch_origin_main(repo_root)
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
