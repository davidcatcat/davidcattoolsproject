"""Read GitHub REST core and GraphQL rate-limit quota through gh."""

import json
import os
import selectors
import shutil
import subprocess
import time

from . import github_identity

FLOOR = 300
_GH_PATH = '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin'
_TIMEOUT = 8
_OUTPUT_LIMIT = 64 * 1024
_RESOURCES = ('core', 'graphql')


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _clean_env():
    # Always isolate gh into the AI config dir; ignore the caller's GH_CONFIG_DIR.
    home = os.path.expanduser('~')
    env = {
        'PATH': _GH_PATH,
        'HOME': home,
        'LC_ALL': 'C',
        'GH_PROMPT_DISABLED': '1',
        'GH_NO_UPDATE_NOTIFIER': '1',
        'NO_COLOR': '1',
        'GH_CONFIG_DIR': github_identity.ai_gh_config_dir(home),
    }
    return env


def _run(argv, env):
    process = subprocess.Popen(
        argv, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        shell=False, close_fds=True, start_new_session=True)
    raw = bytearray()
    deadline = time.monotonic() + _TIMEOUT
    selector = selectors.DefaultSelector()
    try:
        os.set_blocking(process.stdout.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return 1, b''
            for key, _ in selector.select(min(remaining, 0.1)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                elif len(raw) + len(chunk) > _OUTPUT_LIMIT:
                    return 1, b''
                else:
                    raw.extend(chunk)
        code = process.wait(timeout=max(0, deadline - time.monotonic()))
    finally:
        selector.close()
        if process.poll() is None:
            try:
                os.killpg(process.pid, 9)
            except OSError:
                process.kill()
            process.wait()
        process.stdout.close()
    return code, bytes(raw)


def read(runner=None):
    """Run gh api rate_limit once and return core and graphql quota ints, or None."""
    gh = shutil.which('gh', path=_GH_PATH)
    if gh is None:
        return None
    argv = [gh, 'api', 'rate_limit']
    env = _clean_env()
    run = _run if runner is None else runner
    try:
        outcome = run(argv, env)
    except Exception:
        return None
    if type(outcome) is tuple and len(outcome) == 2:
        code, raw = outcome
    else:
        code = getattr(outcome, 'returncode', None)
        raw = getattr(outcome, 'stdout', None)
    if type(code) is not int or code != 0:
        return None
    if type(raw) is bytes:
        try:
            text = raw.decode('utf-8', 'strict')
        except UnicodeError:
            return None
    elif type(raw) is str:
        text = raw
    else:
        return None
    try:
        payload = json.loads(
            text, object_pairs_hook=_unique_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except Exception:
        return None
    if type(payload) is not dict:
        return None
    resources = payload.get('resources')
    if type(resources) is not dict:
        return None
    quota = {}
    for name in _RESOURCES:
        entry = resources.get(name)
        if type(entry) is not dict:
            return None
        remaining = entry.get('remaining')
        reset = entry.get('reset')
        if (type(remaining) is not int or remaining < 0 or
                type(reset) is not int or reset < 0):
            return None
        quota[name] = {'remaining': remaining, 'reset': reset}
    return quota


def low(quota, floor=FLOOR):
    """Return the first resource (core, then graphql) below floor, or None."""
    if type(floor) is not int or floor < 0:
        raise ValueError('floor must be a non-negative integer')
    if type(quota) is not dict:
        return None
    for name in _RESOURCES:
        entry = quota.get(name)
        if type(entry) is not dict:
            return None
        remaining = entry.get('remaining')
        reset = entry.get('reset')
        if (type(remaining) is not int or remaining < 0 or
                type(reset) is not int or reset < 0):
            return None
    for name in _RESOURCES:
        entry = quota[name]
        if entry['remaining'] < floor:
            return {
                'resource': name,
                'remaining': entry['remaining'],
                'reset': entry['reset'],
            }
    return None
