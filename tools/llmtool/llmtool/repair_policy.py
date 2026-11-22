"""#1502 WP-D: limits for review repair, AI Debug, split, REPLAN and test failures.

#2281 row 18 limits (basis #819): Developer assignment wait and expiry.

The numbers live only in tools/llmtool/policy.json (spec section 5); no caller
overrides them. The functions here are pure: callers pass counts derived from
their own Task records. A count that cannot be derived is UNKNOWN, and UNKNOWN
counts as used up (spec section 7).

#2367 (Owner 2026-11-19): a work item has repair_max effective review repairs
and test_fail_max effective EC2/CI test repairs (5 each). The fourth review
repair is the first to require a confirmed RCA. review_fail_max is the FAIL
after the last repair (repair_max + 1), where the Task escalates by its kind.
ROOT, CHILD, REPLAN and continued Tasks share one Debug budget.
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
# #2281 L0b: optional AI-set limits from the #2281 limit table, each a positive
# integer (>= 1) with no upper validation bound. An absent key keeps its caller
# default; a present key of the wrong type, zero or negative refuses
# POLICY_INVALID. Callers are wired in later pieces.
#   row 2:  spec_preflight_large_file_lines (gw-agent edit large-file threshold)
#   row 15: merge_train_members_max, merge_train_files_per_pr_max
#   row 16: revert_scope_prs_max
#   row 17: d6_writer_lease_hours (#2281 L7: D6 writer lease length in hours,
#           read by scripts/d6_worktree_lifecycle.py, which has no default)
#   row 12: review_packet_limit_bytes (#2281 row 12, first part, basis #131
#           and #165: review packet size limit in bytes; caller default
#           llmtool.task_review.PACKET_LIMIT)
#   row 12: review_source_limit_bytes (#2281 row 12, basis #131 and
#           #165: review source size limit in bytes; caller default
#           llmtool.task_review.SOURCE_LIMIT)
#   row 12: claude_review_envelope_limit_bytes (#2281 row 12, basis #131 and
#           #165: Claude review envelope size limit in bytes; caller default
#           llmtool.providers.claude_cli.REVIEW_ENVELOPE_LIMIT)
#   row 13: start_failures_max (#2281 row 13, first part, basis #2188: the
#           per-Task start-failure limit; caller default
#           llmtool.task_store.MAX_START_FAILURES)
#   row 13: start_retries_max (#2281 row 13, second part, basis #2188: the
#           per-Task start retry limit; caller default
#           llmtool.worker_dispatch.MAX_START_RETRIES_PER_TASK)
#   row 14: committed_result_carries_max (#2281 row 14, basis #1148: the
#           committed-result carries per Developer assignment; caller default
#           llmtool.worker_dispatch.MAX_COMMITTED_CARRIES)
#   row 18: developer_assignment_wait_seconds (#819: Developer assignment wait;
#           caller default llmtool.developer_assignment_inbox.DEFAULT_WAIT_SECONDS)
#           and developer_assignment_expiry_seconds (#819: Developer
#           assignment expiry, how long an assignment stays valid; caller
#           default llmtool.developer_assignment_inbox.DEFAULT_EXPIRY_SECONDS)
#   row 19: ai_debug_excerpt_max_chars (#2281 row 19, second part, basis #2188
#           and #1502 D11: the AI Debug excerpt size limit in characters;
#           caller default llmtool.start_failure.HIGH_DEBUG_EXCERPT_MAX_CHARS)
#   #2537:  gw_agent_max_turns (the per-Task gw-agent turn cap; caller default
#           GW_AGENT_MAX_TURNS below)
OPTIONAL_POSITIVE_POLICY_KEYS = ('spec_preflight_large_file_lines', 'merge_train_members_max',
                                 'merge_train_files_per_pr_max', 'revert_scope_prs_max',
                                 'd6_writer_lease_hours', 'developer_assignment_wait_seconds',
                                 'developer_assignment_expiry_seconds',
                                 'review_packet_limit_bytes',
                                 'claude_review_envelope_limit_bytes',
                                 'review_source_limit_bytes', 'start_failures_max',
                                 'start_retries_max', 'committed_result_carries_max',
                                 'ai_debug_excerpt_max_chars', 'gw_agent_max_turns')
# #2281 row 18 (basis #819): Developer assignment wait. The key above is
# optional; a policy that omits it keeps the caller default, the hard-coded
# wait at its point of use (llmtool.developer_assignment_inbox).
DEVELOPER_ASSIGNMENT_WAIT_SECONDS = 540
# #2281 row 18 (basis #819): Developer assignment expiry. The key above is
# optional; a policy that omits it keeps the caller default, the hard-coded
# expiry at its point of use (llmtool.developer_assignment_inbox).
DEVELOPER_ASSIGNMENT_EXPIRY_SECONDS = 600
# #2281 row 12 (first part, basis #131, #165): review packet size limit in
# bytes. The key above is optional; a policy that omits it keeps the caller
# default, the hard-coded limit at its point of use (llmtool.task_review,
# PACKET_LIMIT).
REVIEW_PACKET_LIMIT_BYTES = 256 * 1024
# #2281 row 12 (basis #131, #165): Claude review envelope size limit in bytes.
# The key above is optional; a policy that omits it keeps the caller default,
# the hard-coded limit at its point of use
# (llmtool.providers.claude_cli.REVIEW_ENVELOPE_LIMIT).
CLAUDE_REVIEW_ENVELOPE_LIMIT_BYTES = 128 * 1024
# #2281 row 12 (basis #131, #165): review source size limit in bytes.
# The key above is optional; a policy that omits it keeps the caller
# default, the hard-coded limit at its point of use (llmtool.task_review,
# SOURCE_LIMIT).
REVIEW_SOURCE_LIMIT_BYTES = 128 * 1024
# #2281 row 13 (first part, basis #2188): the per-Task start-failure limit.
# The key above is optional; a policy that omits it keeps the caller
# default, the hard-coded limit at its point of use (llmtool.task_store,
# MAX_START_FAILURES).
START_FAILURES_MAX = 2
# #2281 row 13 (second part, basis #2188): the per-Task start retry limit.
# The key above is optional; a policy that omits it keeps the caller
# default, the hard-coded limit at its point of use
# (llmtool.worker_dispatch, MAX_START_RETRIES_PER_TASK).
START_RETRIES_MAX = 2
# #2281 row 14 (basis #1148): the committed-result carries per Developer
# assignment. The key above is optional; a policy that omits it keeps the
# caller default, the hard-coded limit at its point of use
# (llmtool.worker_dispatch, MAX_COMMITTED_CARRIES).
COMMITTED_RESULT_CARRIES_MAX = 1
# #2281 row 19 (second part, basis #2188 and #1502 D11): the AI Debug excerpt
# size limit, in characters. The key above is optional; a policy that omits it
# keeps the caller default, the hard-coded limit at its point of use
# (llmtool.start_failure, HIGH_DEBUG_EXCERPT_MAX_CHARS).
AI_DEBUG_EXCERPT_MAX_CHARS = 6000
# #2537: the per-Task gw-agent turn cap. llmtool passes the gateway
# min(route max_turns, this cap), so a looping run ends with the definite
# BUDGET_EXCEEDED / TURN_BUDGET_EXCEEDED result instead of running to the
# wall timeout. Basis (299 local gw-agent session records, 2026-11-22):
# successful single runs used at most 37 turns (recent successes above 16
# turns used 17-37); looping runs reached 137 (#2537) and 274 turns.
# 64 leaves about 1.7x headroom over the largest success and stops a loop at
# under half the smallest one. The key above is optional; a policy that omits
# it keeps this value.
GW_AGENT_MAX_TURNS = 64
# #2281 L0a (design rule 3): the bound is a real technical limit, not an
# arbitrary cap. policy.json is untrusted input, so load_policy reads at most
# _MAX_BYTES + 1 bytes in one bounded buffer instead of the whole file. The
# bound must stay above the largest possible valid policy.json (schema_version,
# the POLICY_KEYS and the optional keys as ints: a few hundred bytes), so it
# can never refuse a valid policy; 16 KiB keeps the read buffer bounded while
# leaving room for those keys.
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
    allowed = {'schema_version', *POLICY_KEYS, *OPTIONAL_POLICY_LIMITS,
               *OPTIONAL_POSITIVE_POLICY_KEYS}
    if (type(data) is not dict or not {'schema_version', *POLICY_KEYS} <= set(data) <= allowed or
            any(key in data and (type(data[key]) is not int or not low <= data[key] <= high)
                for key, (low, high) in OPTIONAL_POLICY_LIMITS.items()) or
            any(key in data and (type(data[key]) is not int or data[key] < 1)
                for key in OPTIONAL_POSITIVE_POLICY_KEYS) or
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


def assignment_wait_seconds(path=None):
    """#2281 row 18 (basis #819): the Developer assignment wait, in seconds.

    The key is optional; a policy that omits it keeps the caller default, the
    hard-coded wait at its point of use (llmtool.developer_assignment_inbox).
    """
    policy = load_policy(path)
    return policy.get('developer_assignment_wait_seconds',
                      DEVELOPER_ASSIGNMENT_WAIT_SECONDS)


def assignment_expiry_seconds(path=None):
    """#2281 row 18 (basis #819): the Developer assignment expiry, in seconds.

    The key is optional; a policy that omits it keeps the caller default, the
    hard-coded expiry at its point of use (llmtool.developer_assignment_inbox).
    """
    policy = load_policy(path)
    return policy.get('developer_assignment_expiry_seconds',
                      DEVELOPER_ASSIGNMENT_EXPIRY_SECONDS)


def review_packet_limit_bytes(path=None):
    """#2281 row 12 (first part, basis #131, #165): the review packet size
    limit, in bytes.

    The key is optional; a policy that omits it keeps the caller default, the
    hard-coded limit at its point of use (llmtool.task_review, PACKET_LIMIT).
    """
    policy = load_policy(path)
    return policy.get('review_packet_limit_bytes',
                      REVIEW_PACKET_LIMIT_BYTES)


def claude_review_envelope_limit_bytes(path=None):
    """#2281 row 12 (basis #131, #165): the Claude review envelope size limit,
    in bytes.

    The key is optional; a policy that omits it keeps the caller default, the
    hard-coded limit at its point of use (llmtool.providers.claude_cli,
    REVIEW_ENVELOPE_LIMIT).
    """
    policy = load_policy(path)
    return policy.get('claude_review_envelope_limit_bytes',
                      CLAUDE_REVIEW_ENVELOPE_LIMIT_BYTES)


def review_source_limit_bytes(path=None):
    """#2281 row 12 (basis #131, #165): the review source size limit, in
    bytes.

    The key is optional; a policy that omits it keeps the caller default,
    the hard-coded limit at its point of use (llmtool.task_review,
    SOURCE_LIMIT).
    """
    policy = load_policy(path)
    return policy.get('review_source_limit_bytes',
                      REVIEW_SOURCE_LIMIT_BYTES)


def start_failures_max(path=None):
    """#2281 row 13 (first part, basis #2188): the per-Task start-failure
    limit.

    The key is optional; a policy that omits it keeps the caller default,
    the hard-coded limit at its point of use (llmtool.task_store,
    MAX_START_FAILURES).
    """
    policy = load_policy(path)
    return policy.get('start_failures_max',
                      START_FAILURES_MAX)


def start_retries_max(path=None):
    """#2281 row 13 (second part, basis #2188): the per-Task start retry
    limit.

    The key is optional; a policy that omits it keeps the caller default,
    the hard-coded limit at its point of use (llmtool.worker_dispatch,
    MAX_START_RETRIES_PER_TASK).
    """
    policy = load_policy(path)
    return policy.get('start_retries_max',
                      START_RETRIES_MAX)


def committed_result_carries_max(path=None):
    """#2281 row 14 (basis #1148): the committed-result carries per
    Developer assignment.

    The key is optional; a policy that omits it keeps the caller default,
    the hard-coded limit at its point of use (llmtool.worker_dispatch,
    MAX_COMMITTED_CARRIES).
    """
    policy = load_policy(path)
    return policy.get('committed_result_carries_max',
                      COMMITTED_RESULT_CARRIES_MAX)


def ai_debug_excerpt_max_chars(path=None):
    """#2281 row 19 (second part, basis #2188 and #1502 D11): the AI Debug
    excerpt size limit, in characters.

    The key is optional; a policy that omits it keeps the caller default, the
    hard-coded limit at its point of use (llmtool.start_failure,
    HIGH_DEBUG_EXCERPT_MAX_CHARS).
    """
    policy = load_policy(path)
    return policy.get('ai_debug_excerpt_max_chars',
                      AI_DEBUG_EXCERPT_MAX_CHARS)


def gw_agent_max_turns(path=None):
    """#2537: the per-Task gw-agent turn cap (optional key, default
    GW_AGENT_MAX_TURNS)."""
    policy = load_policy(path)
    return policy.get('gw_agent_max_turns', GW_AGENT_MAX_TURNS)


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


REVIEW_REPAIRS_WITHOUT_DEBUG = 3


def review_action(*, kind, fail_count, debug_status=None, group_debug_used=0,
                  repair_count=None, policy):
    """Select the action after a formal review FAIL."""
    if kind not in KINDS:
        _refuse('LINEAGE_KIND_INVALID')
    escalate = _ESCALATION[kind]
    fails = _used(fail_count, policy['review_fail_max'])
    if fails < 1:
        _refuse('REVIEW_FAIL_COUNT_INVALID')
    if fails >= policy['review_fail_max']:
        return escalate
    done = fails - 1 if repair_count is None else _used(repair_count, policy['repair_max'])
    if done < REVIEW_REPAIRS_WITHOUT_DEBUG or debug_status == 'RCA_CONFIRMED':
        return 'REPAIR'
    if admit_debug(kind=kind, group_debug_used=group_debug_used, policy=policy) is not None:
        return escalate
    return 'DEBUG'


def admit_repair(*, repair_count, rca_confirmed, policy):
    """Admit an effective review repair within the cap and RCA threshold."""
    done = _used(repair_count, policy['repair_max'])
    if done >= policy['repair_max']:
        return 'REPAIR_LIMIT_REACHED'
    if done >= REVIEW_REPAIRS_WITHOUT_DEBUG and rca_confirmed is not True:
        return 'REPAIR_DEBUG_REQUIRED'
    return None


def admit_debug(*, kind, parent_debug_used=0, group_debug_used=0, policy):
    """Apply one shared Debug budget to the entire original work."""
    if kind not in KINDS:
        _refuse('LINEAGE_KIND_INVALID')
    limit = min(policy['parent_debug_max'], policy['group_debug_max'])
    used = max(_used(parent_debug_used, limit), _used(group_debug_used, limit))
    if used < limit:
        return None
    return 'DEBUG_GROUP_USED' if kind == 'child' else 'DEBUG_PARENT_USED'


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

    tasks: dicts with id, kind ('child' or 'replan'), state and, for a REPLAN or
    split child, replaces (the child ids whose obligation it takes over). A child
    replaced by a CLOSED REPLAN or split child has met its obligation. The ROOT
    result never notifies the Owner again: each non-successful Task already did (D4).
    """
    if type(tasks) is not list or not tasks or any(type(task) is not dict for task in tasks):
        return 'OPEN'
    if any(task.get('state') not in TERMINAL_STATES for task in tasks):
        return 'OPEN'
    covered = set()
    for task in tasks:
        if task.get('state') == 'CLOSED':
            covered.update(task.get('replaces') or ())
    for task in tasks:
        if task.get('state') == 'CLOSED':
            continue
        if task.get('id') in covered:
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
