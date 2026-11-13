"""Shared, schema-valid worker dispatch policy for ordinary tests (#1692).

Ordinary tests must not depend on the live ``tools/llmtool/worker-dispatch.json``.
``install`` writes a v1 policy file in which every worker key the product knows
(``WORKERS | DEBUG_WORKERS``) is ALLOWED, and points the product's own
policy-path hook (``worker_dispatch._POLICY_PATH``) at it. Every decision still
goes through the real reader, parser and gate (``worker_dispatch.evaluate``);
nothing is bypassed or mocked here.

A test that checks a refusal sets PAUSED or OWNER_ASSIGNMENT_ONLY explicitly
with ``set_dispatch`` or ``update``. Only tests that validate the real policy
file read ``worker_dispatch._DEFAULT_POLICY_PATH``.
"""

import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from llmtool import worker_dispatch

EVIDENCE = 'https://github.com/davidcatcat/davidcattoolsproject/issues/289'
# company-gw-agent accepts only these two exact rows (worker_dispatch._parse_policy).
GW_AGENT_OWNER_EVIDENCE = ('https://github.com/davidcatcat/davidcattoolsproject/'
                           'issues/120#issuecomment-6760670167')
GW_AGENT_PM_PAUSED_EVIDENCE = 'https://github.com/davidcatcat/davidcattoolsproject/issues/120'
DECIDED_AT = '2026-11-13'
REASON = 'shared test worker policy (#1692)'


def set_dispatch(value, worker, dispatch, *, owner_assignments=None):
    """Set one worker row in a policy dict to a schema-valid state; return the dict."""
    if dispatch not in worker_dispatch.DISPATCH_STATES:
        raise ValueError('unknown dispatch state')
    row = {'dispatch': dispatch, 'decided_by': 'Owner', 'decided_at': DECIDED_AT,
           'reason': REASON, 'evidence': EVIDENCE}
    if worker == 'company-gw-agent':
        if dispatch == 'ALLOWED':
            row['evidence'] = GW_AGENT_OWNER_EVIDENCE
        elif dispatch == 'PAUSED':
            row.update(decided_by='PM', evidence=GW_AGENT_PM_PAUSED_EVIDENCE)
        else:
            raise ValueError('company-gw-agent has no schema-valid OWNER_ASSIGNMENT_ONLY row')
    if owner_assignments is not None:
        row['owner_assignments'] = [dict(item) for item in owner_assignments]
    value['workers'][worker] = row
    return value


def policy(states=None, *, keys=None):
    """Return a v1 policy dict: every key ALLOWED, then ``states`` applied.

    ``keys`` defaults to every worker key the product knows. ``states`` maps a
    worker key to a dispatch state.
    """
    if keys is None:
        keys = worker_dispatch.WORKERS | worker_dispatch.DEBUG_WORKERS
    value = {'schema_version': worker_dispatch.POLICY_SCHEMA_VERSION, 'workers': {}}
    for key in sorted(keys):
        set_dispatch(value, key, 'ALLOWED')
    for key, dispatch in (states or {}).items():
        set_dispatch(value, key, dispatch)
    return value


def write(path, value):
    path = Path(path)
    path.write_text(json.dumps(value), encoding='utf-8')
    return path


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def update(path, worker, dispatch, *, owner_assignments=None):
    """Rewrite one worker row of the policy file at ``path``; return the policy."""
    value = set_dispatch(read(path), worker, dispatch, owner_assignments=owner_assignments)
    write(path, value)
    return value


def _start(states):
    temporary = tempfile.TemporaryDirectory()
    path = write(Path(temporary.name).resolve() / 'worker-dispatch.json', policy(states))
    patcher = patch.object(worker_dispatch, '_POLICY_PATH', path)
    patcher.start()
    return temporary, patcher, path


def install(testcase, states=None):
    """Write the shared policy to a private temporary file and patch the policy hook.

    The patch and the file are removed by ``testcase`` cleanups. Returns the path.
    """
    temporary, patcher, path = _start(states)
    testcase.addCleanup(temporary.cleanup)
    testcase.addCleanup(patcher.stop)
    return path


_MODULE_STACK = []


def start_module(states=None):
    """Module-level form of ``install`` for a ``setUpModule`` hook; returns the path."""
    temporary, patcher, path = _start(states)
    _MODULE_STACK.append((temporary, patcher))
    return path


def stop_module():
    """Undo the most recent ``start_module``; call it from ``tearDownModule``."""
    temporary, patcher = _MODULE_STACK.pop()
    patcher.stop()
    temporary.cleanup()
