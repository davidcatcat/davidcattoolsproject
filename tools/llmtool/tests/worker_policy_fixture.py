"""Shared schema-valid test worker policy (#1692).

Ordinary dispatch tests must not depend on the live state of
``tools/llmtool/worker-dispatch.json``. They load this fixture instead,
through the product's own policy-path hook (``worker_dispatch._POLICY_PATH``),
so every decision still goes through the real reader, parser and gate.

Every worker key is ``ALLOWED``. A test that checks a refusal sets
``PAUSED`` or ``OWNER_ASSIGNMENT_ONLY`` explicitly, either through ``states``
or by editing the written file. Only tests that validate the real policy
file, or are integration tests of it, read ``worker-dispatch.json``.
"""

import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from llmtool import worker_dispatch

FILE_NAME = 'worker-dispatch.json'
_EVIDENCE = 'https://github.com/davidcatcat/davidcattoolsproject/issues/289'
_DECIDED_AT = '2026-11-13'
_REASON = 'test fixture: shared worker policy (#1692)'
_GW_AGENT = 'company-gw-agent'


def _row(worker, state):
    if worker == _GW_AGENT:
        # The parser accepts only the exact Owner-allowed or PM-paused row.
        if state == 'ALLOWED':
            return {'dispatch': 'ALLOWED', 'decided_by': 'Owner', 'decided_at': _DECIDED_AT,
                    'reason': _REASON, 'evidence': worker_dispatch._GW_AGENT_OWNER_REF}
        if state == 'PAUSED':
            return {'dispatch': 'PAUSED', 'decided_by': 'PM', 'decided_at': _DECIDED_AT,
                    'reason': _REASON, 'evidence': worker_dispatch._GW_AGENT_PAUSED_REF}
        raise ValueError('company-gw-agent fixture state must be ALLOWED or PAUSED')
    return {'dispatch': state, 'decided_by': 'Owner', 'decided_at': _DECIDED_AT,
            'reason': _REASON, 'evidence': _EVIDENCE}


def policy_document(states=None):
    """Return the v1 policy dict: every worker ALLOWED unless ``states`` says otherwise."""
    states = dict(states or {})
    keys = worker_dispatch.WORKERS | worker_dispatch.DEBUG_WORKERS
    unknown = set(states) - keys
    if unknown:
        raise ValueError('unknown fixture worker key: %s' % ', '.join(sorted(unknown)))
    for state in states.values():
        if state not in worker_dispatch.DISPATCH_STATES:
            raise ValueError('unknown fixture dispatch state')
    return {
        'schema_version': worker_dispatch.POLICY_SCHEMA_VERSION,
        'workers': {key: _row(key, states.get(key, 'ALLOWED')) for key in sorted(keys)},
    }


def write_policy(path, states=None):
    """Write the fixture policy to ``path`` and return it as a resolved Path."""
    path = Path(path).resolve()
    path.write_text(json.dumps(policy_document(states)), encoding='utf-8')
    return path


def install(test_case, directory=None, states=None):
    """Write the fixture and point the gate at it for one test.

    ``directory`` defaults to a new temporary directory owned by the test.
    Returns the policy path so a refusal test can edit one worker row.
    """
    if directory is None:
        temporary = tempfile.TemporaryDirectory()
        test_case.addCleanup(temporary.cleanup)
        directory = temporary.name
    path = write_policy(Path(directory) / FILE_NAME, states)
    patcher = patch.object(worker_dispatch, '_POLICY_PATH', path)
    patcher.start()
    test_case.addCleanup(patcher.stop)
    return path


_MODULE_STACK = []


def start_module(states=None):
    """Install the fixture for a whole test module (call from setUpModule)."""
    temporary = tempfile.TemporaryDirectory()
    path = write_policy(Path(temporary.name) / FILE_NAME, states)
    patcher = patch.object(worker_dispatch, '_POLICY_PATH', path)
    patcher.start()
    _MODULE_STACK.append((patcher, temporary))
    return path


def stop_module():
    """Undo the latest ``start_module`` (call from tearDownModule)."""
    if _MODULE_STACK:
        patcher, temporary = _MODULE_STACK.pop()
        patcher.stop()
        temporary.cleanup()
