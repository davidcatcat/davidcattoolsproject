"""#1502 WP-D: limits for review repair, AI Debug, split, REPLAN and test failures.

The numbers live only in tools/llmtool/policy.json (spec section 5); no caller
overrides them. The functions here are pure: callers pass counts derived from
their own Task records. A count that cannot be derived is UNKNOWN, and UNKNOWN
counts as used up (spec section 7).

#2367 (Owner 2026-11-19): a work item has repair_max effective review repairs
and test_fail_max effective EC2/CI test repairs (5 each). AI Debug is still
required once, before the review repair that follows the 2nd valid FAIL; one
RCA_CONFIRMED of a family then covers that family's later repairs, so FAILs 3
to repair_max are repaired without a new Debug. review_fail_max is the FAIL
after the last repair (repair_max + 1), where the Task escalates by its kind.
Each split child is its own work item with one Debug (parent_debug_max); the
children of a lineage share group_debug_max (one per child up to child_max).
Every other module takes these numbers from here (repair_attempts_max for the
stored claim and run budget bounds); none keeps its own copy.
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
# #1815 T3: optional limits as key -> (min, max); an absent key keeps its caller default.
OPTIONAL_POLICY_LIMITS = {'timeout_continuation_max': (0, 3)}
# #2281 L0a (design rule 3): the bound is a real technical limit, not an
# arbitrary cap. policy.json is untrusted input, so load_policy reads at most
# _MAX_BYTES + 1 bytes in one bounded buffer instead of the whole file. The
# bound must stay above the largest possible valid policy.json (schema_version,
# the POLICY_KEYS as small ints and the planned #2281 keys: a few hundred
# bytes), so it can never refuse a valid policy; 16 KiB keeps the read buffer
# bounded while leaving room for those keys.
_MAX_BYTES = 16 * 1024


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
    allowed = {'schema_version', *POLICY_KEYS, *OPTIONAL_POLICY_LIMITS}
    if (type(data) is not dict or not {'schema_version', *POLICY_KEYS} <= set(data) <= allowed or
            any(key in data and (type(data[key]) is not int or not low <= data[key] <= high)
                for key, (low, high) in OPTIONAL_POLICY_LIMITS.items()) or
            type(data['schema_version']) is not int or data['schema_version'] != 1 or
            any(type(data[key]) is not int or not 0 <= data[key] <= 1000
                for key in POLICY_KEYS)):
        _refuse('POLICY_INVALID')
    return dict(data)


def repair_attempts_max(path=None):
    """The most effective repairs (review or test) one work item may use.

    #2367: the single source for the stored repair-claim attempt range and the
    run budget repair bound, read from policy.json like every other limit. A
    policy that cannot be read gives 0, which admits no repair (fail closed).
    """
    try:
        policy = load_policy(path)
    except LLMToolError:
        return 0
    return max(policy['repair_max'], policy['test_fail_max'])


KINDS =('root', 'child', 'replan')
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
    the second repair (D1), any other Debug outcome escalates. #2367: a later
    FAIL below review_fail_max is decided the same way by the Debug that covers
    it (the caller passes the family's RCA_CONFIRMED), so no new Debug is run.
    From FAIL review_fail_max on, ROOT splits, a child goes to PM_DECIDE and a
    REPLAN ends FAILED_FINAL. A child whose lineage used its group Debug
    escalates.
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


def admit_repair(*, repair_count, rca_confirmed, policy):
    """None admits one more review repair; else the refusal code.

    At most repair_max repairs; every repair after the first needs an
    RCA_CONFIRMED Debug: repair #2 one bound to its failed HEAD (D1), a later
    repair the family's RCA_CONFIRMED that covers it (#2367).
    """
    done = _used(repair_count, policy['repair_max'])
    if done >= policy['repair_max']:
        return 'REPAIR_LIMIT_REACHED'
    if done >= 1 and rca_confirmed is not True:
        return 'REPAIR_DEBUG_REQUIRED'
    return None


def admit_debug(*, kind, parent_debug_used=0, group_debug_used=0, policy):
    """None admits one AI Debug.

    ROOT and REPLAN own parent_debug_max each. #2367: a split child is a work
    item too, so it owns parent_debug_max (parent_debug_used is the child's
    own count), and the children of one lineage share group_debug_max. Both
    child refusals use DEBUG_GROUP_USED.
    """
    if kind not in KINDS:
        _refuse('LINEAGE_KIND_INVALID')
    if kind == 'child':
        if _used(parent_debug_used, policy['parent_debug_max']) >= policy['parent_debug_max']:
            return 'DEBUG_GROUP_USED'
        used, limit, code = group_debug_used, policy['group_debug_max'], 'DEBUG_GROUP_USED'
    else:
        used, limit, code = parent_debug_used, policy['parent_debug_max'], 'DEBUG_PARENT_USED'
    return code if _used(used, limit) >= limit else None


def test_failure_action(*, test_fail_count, policy):
    """After an EC2, Linux CI or Mac CI failure caused by the code: REPAIR or STOPPED."""
    done = _used(test_fail_count, policy['test_fail_max'])
    return 'REPAIR' if done < policy['test_fail_max'] else 'STOPPED'


TERMINAL_STATES = ('CLOSED', 'DROPPED', 'STOPPED', 'FAILED_FINAL')
# D4: every Task that does not end in success notifies the Owner; CLOSED does not.
OWNER_NOTICE_STATES = ('DROPPED', 'STOPPED', 'FAILED_FINAL')
WAITING_REASONS = ('AUTH', 'QUOTA', 'ROUTE_UNAVAILABLE', 'NO_REVIEWER', 'DISPUTE', 'ENV')


def admit_split(*, kind, child_count, new_children, policy):
    """None admits a SPLIT of new_children child Tasks; only a ROOT splits."""
    if kind != 'root':
        return 'SPLIT_NOT_ROOT'
    if type(new_children) is not int or new_children < 1:
        return 'SPLIT_CHILDREN_INVALID'
    if _used(child_count, policy['child_max']) + new_children > policy['child_max']:
        return 'SPLIT_CHILD_LIMIT'
    return None


def admit_replan(*, kind, replan_used, replaces, policy):
    """None admits the lineage's one REPLAN, chosen at PM_DECIDE of a child Task."""
    if kind != 'child':
        return 'REPLAN_NOT_CHILD'
    if (type(replaces) is not list or not replaces or len(set(replaces)) != len(replaces) or
            any(type(item) is not str or not item for item in replaces)):
        return 'REPLAN_REPLACES_INVALID'
    if _used(replan_used, policy['replan_max']) >= policy['replan_max']:
        return 'REPLAN_USED'
    return None


def admit_drop(*, reason):
    """None admits a DROP; a DROP must record why the original function is not affected."""
    if type(reason) is not str or not reason.strip() or len(reason) > 500:
        return 'DROP_REASON_REQUIRED'
    return None


def owner_notice(state):
    """True when a Task in this end state notifies the Owner (D4)."""
    return state in OWNER_NOTICE_STATES


def root_outcome(tasks):
    """OPEN, CLOSED or FAILED for a split ROOT from its child and REPLAN Tasks.

    tasks: dicts with id, kind ('child' or 'replan'), state and, for a REPLAN,
    replaces (the child ids whose obligation it takes over). A child replaced by
    a CLOSED REPLAN has met its obligation. The ROOT result never notifies the
    Owner again: each non-successful Task already did (D4).
    """
    if type(tasks) is not list or not tasks or any(type(task) is not dict for task in tasks):
        return 'OPEN'
    if any(task.get('state') not in TERMINAL_STATES for task in tasks):
        return 'OPEN'
    covered = set()
    for task in tasks:
        if task.get('kind') == 'replan' and task.get('state') == 'CLOSED':
            covered.update(task.get('replaces') or ())
    for task in tasks:
        if task.get('state') == 'CLOSED':
            continue
        if task.get('kind') == 'child' and task.get('id') in covered:
            continue
        return 'FAILED'
    return 'CLOSED'

def waiting_notice_due(*, reason, waiting_since, now, policy):
    """E5: AUTH notifies at once; any other WAITING after waiting_notify_after_hours.

    WAITING has no time limit of its own; only the notice is timed. A malformed
    reason or time notifies at once.
    """
    from datetime import datetime, timedelta
    if reason == 'AUTH' or reason not in WAITING_REASONS:
        return True
    try:
        since = datetime.strptime(waiting_since, '%Y-%m-%dT%H:%M:%SZ')
        current = datetime.strptime(now, '%Y-%m-%dT%H:%M:%SZ')
    except (TypeError, ValueError):
        return True
    return current - since >= timedelta(hours=policy['waiting_notify_after_hours'])
