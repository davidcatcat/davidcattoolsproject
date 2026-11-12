"""#1502 WP-D: limits for review repair, AI Debug, split, REPLAN and test failures.

The numbers live only in tools/llmtool/policy.json (spec section 5); no caller
overrides them. The functions here are pure: callers pass counts derived from
their own Task records. A count that cannot be derived is UNKNOWN, and UNKNOWN
counts as used up (spec section 7).
"""

import json
import os
import sysconfig
from pathlib import Path

from .errors import LLMToolError

UNKNOWN = 'UNKNOWN'
POLICY_KEYS = ('repair_max', 'parent_debug_max', 'group_debug_max', 'child_max',
               'replan_max', 'test_fail_max', 'review_fail_max',
               'waiting_notify_after_hours')
_MAX_BYTES = 4096


def _refuse(code):
    raise LLMToolError(code) from None


def policy_path():
    """The source policy.json, else a packaged or installed copy, in that order."""
    module = Path(__file__).resolve()
    candidates = (module.parent.parent / 'policy.json', module.parent / 'policy.json',
                  Path(sysconfig.get_path('data')) / 'llmtool' / 'policy.json')
    for candidate in candidates:
        if os.path.lexists(candidate):
            return Path(os.path.abspath(candidate))
    return Path(os.path.abspath(candidates[0]))


def load_policy(path=None):
    """Read and strictly check policy.json; any doubt refuses POLICY_INVALID."""
    try:
        with open(policy_path() if path is None else path, 'rb') as stream:
            raw = stream.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise ValueError
        data = json.loads(raw.decode('utf-8'))
    except (OSError, ValueError, UnicodeError):
        _refuse('POLICY_INVALID')
    if (type(data) is not dict or set(data) != {'schema_version', *POLICY_KEYS} or
            type(data['schema_version']) is not int or data['schema_version'] != 1 or
            any(type(data[key]) is not int or not 0 <= data[key] <= 1000
                for key in POLICY_KEYS)):
        _refuse('POLICY_INVALID')
    return dict(data)
