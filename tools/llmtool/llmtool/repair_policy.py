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


KINDS = ('root', 'child', 'replan')
# Where a Task goes when its review route is used up (spec section 3-2).
_ESCALATION = {'root': 'SPLIT', 'child': 'PM_DECIDE', 'replan': 'FAILED_FINAL'}
_FAIL_OUTCOMES = ('PASS', 'FAIL', 'FAIL_NO_BLOCKING')


def _used(value, limit):
    """A known count as is; UNKNOWN or anything malformed counts as the limit."""
    return value if type(value) is int and value >= 0 else limit


def review_fail_count(heads):
    """Count valid review FAILs, at most one per reviewed HEAD (spec section 3-3).

    heads maps each reviewed HEAD to the outcome of its latest formal verdict:
    PASS, FAIL (at least one blocking finding not withdrawn by a dispute) or
    FAIL_NO_BLOCKING. Re-reviews and new versions of one HEAD never add a count.
    Any other value makes the whole count UNKNOWN.
    """
    if type(heads) is not dict:
        return UNKNOWN
    if any(type(head) is not str or outcome not in _FAIL_OUTCOMES
           for head, outcome in heads.items()):
        return UNKNOWN
    return sum(outcome == 'FAIL' for outcome in heads.values())


def review_action(*, kind, fail_count, debug_status=None, group_debug_used=0, policy):
    """The next step after a valid review FAIL: REPAIR, DEBUG or the escalation.

    FAIL #1 is repaired. FAIL #2 needs AI Debug first (O2); RCA_CONFIRMED allows
    the second repair (D1), any other Debug outcome escalates. From FAIL
    review_fail_max on, ROOT splits, a child goes to PM_DECIDE and a REPLAN
    ends FAILED_FINAL. A child whose lineage used its group Debug escalates.
    """
    if kind not in KINDS:
        _refuse('LINEAGE_KIND_INVALID')
    escalate = _ESCALATION[kind]
    fails = _used(fail_count, policy['review_fail_max'])
    if fails < 1:
        _refuse('REVIEW_FAIL_COUNT_INVALID')
    if fails >= policy['review_fail_max']:
        return escalate
    if fails == 1:
        return 'REPAIR'
    if debug_status is None:
        if kind == 'child' and (_used(group_debug_used, policy['group_debug_max']) >=
                                policy['group_debug_max']):
            return escalate
        return 'DEBUG'
    return 'REPAIR' if debug_status == 'RCA_CONFIRMED' else escalate
