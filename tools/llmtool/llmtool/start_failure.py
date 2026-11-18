"""#2188 start-failure retry: pure classification and repair-packet binding.

Design: docs/design/start-failure-retry-2188.md, sections 2 and 4 (PR 1, not
wired). Nothing here reads or writes a Task, a D6 or the network. The live D6
check (section 2 check 6) is the caller's injected ``d6_unchanged`` probe.

Every check fails closed: a value that is unknown, malformed or failing makes
the record "not a start failure", and a packet that cannot be bound does not
match.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

# Section 2 check 2. Codes raised before any provider process starts
# across direct Developer routes (Claude, AGY, gw-agent, Codex, OpenCode).
# None of them is raised after provider launch or model execution.
START_FAILURE_CODES = frozenset((
    # Claude pre-launch codes
    'CLAUDE_PROFILE_INVALID', 'CLAUDE_ENV_INVALID',
    'CLAUDE_REQUEST_INVALID', 'CLAUDE_CONFIG_INVALID',
    'CLAUDE_D6_BINDING_CHANGED', 'CLAUDE_DISPATCH_DENIED',
    # AGY pre-launch codes
    'AGY_PROFILE_INVALID', 'AGY_ENV_INVALID',
    'AGY_REQUEST_INVALID', 'AGY_CONFIG_INVALID',
    'AGY_D6_BINDING_CHANGED', 'AGY_DISPATCH_DENIED',
    # Shared route isolation & git state codes
    'BLOCKED_WRITE_ISOLATION', 'GIT_STATE_CHANGED_SNAPSHOT',
    # Gw-agent pre-launch codes
    'DEVELOPER_OWNER_PAUSED', 'DEVELOPER_DISPATCH_PERSISTENCE_REJECTED',
    'DEVELOPER_GW_AGENT_CONFIG_INVALID', 'DEVELOPER_GW_AGENT_PROFILE_INVALID',
    'DEVELOPER_GW_AGENT_REQUEST_INVALID', 'DEVELOPER_GW_AGENT_CREDENTIAL_MISSING',
    'DEVELOPER_GW_AGENT_SESSION_HOME_UNSAFE', 'DEVELOPER_GW_AGENT_SESSION_LOCKED',
    'DEVELOPER_GW_AGENT_TASK_INVALID', 'DEVELOPER_GW_AGENT_TASK_UNKNOWN_FIELDS',
    'DEVELOPER_GW_AGENT_TASK_SCHEMA_VERSION_INVALID', 'DEVELOPER_GW_AGENT_TASK_TASK_ID_INVALID',
    'DEVELOPER_GW_AGENT_TASK_SOURCE_SHA_INVALID', 'DEVELOPER_GW_AGENT_TASK_GOAL_INVALID',
    'DEVELOPER_GW_AGENT_TASK_PATHS_INVALID', 'DEVELOPER_GW_AGENT_TASK_EGRESS_BASIS_INVALID',
    'DEVELOPER_GW_AGENT_TASK_DATA_CLASS_INVALID', 'DEVELOPER_GW_AGENT_TASK_BUDGETS_INVALID',
    'DEVELOPER_DISPATCH_FAILED', 'GW_AGENT_D6_BINDING_CHANGED',
    'DEVELOPER_DISPATCH_DENIED', 'DEVELOPER_GW_AGENT_DISPATCH_DENIED',
    # Codex pre-launch codes
    'DEVELOPER_PROFILE_UNAVAILABLE', 'DEVELOPER_CALL_BINDING_INVALID',
    'CODEX_D6_BINDING_CHANGED', 'ENV_BOOTSTRAP_REJECTED',
    'CODEX_ENV2_MARKER_INVALID', 'CODEX_IGNORED_BASELINE_UNAVAILABLE',
))

# Section 2 "Never a start failure": codes raised after a run (or possibly after
# one). Listed for review and tests; the classifier admits only
# START_FAILURE_CODES, so any other code is refused as well.
NEVER_START_FAILURE_CODES = frozenset((
    'CLAUDE_RESULT_UNKNOWN', 'CLAUDE_NO_CHANGES', 'CLAUDE_WRITE_SCOPE_VIOLATION',
    'CLAUDE_OUTPUT_TOO_LARGE', 'CLAUDE_OUTPUT_INVALID', 'CLAUDE_STATUS_FAILED',
    'CLAUDE_QUOTA_EXHAUSTED', 'AGY_RESULT_UNKNOWN', 'AGY_NO_CHANGES',
    'AGY_WRITE_SCOPE_VIOLATION', 'AGY_OUTPUT_TOO_LARGE', 'AGY_OUTPUT_INVALID',
    'AGY_STATUS_FAILED', 'AGY_QUOTA_EXHAUSTED',
))

# Section 2 check 3: present and None, and absent.
_EMPTY_RESULT_FIELDS = ('artifact', 'artifact_sha256', 'usage')
_ABSENT_RESULT_FIELDS = ('progress', 'no_changes_diagnosis')
_PRE_SPAWN_BLOCKED_REASONS = frozenset((
    'OWNER_DISPATCH_DENIED', 'UNSUPPORTED_BOUNDARY',
    'BOUNDARY_PREPARATION_FAILED', 'BOUNDARY_UNVERIFIED',
))
# Section 2 check 4: a pre-spawn execution record has none of these.
_NO_PROCESS_FIELDS = ('native_session_id', 'process_exit_code', 'stdout_sha256')
# Section 2 check 5: none of these may hold anything. A tombstone is a compacted
# completion event, so it counts as an event (fail closed).
_NO_PROGRESS_RECORD_FIELDS = (
    'completion_events', 'completion_event_tombstones', 'review_repair_claims',
    'merge_attempt', 'abandonment', 'published_pr',
)

# Section 4: the repair packet rules of task_submission_cli._validate_repair_packet.
_PACKET_FIELDS = frozenset((
    'task', 'pr', 'executor_task', 'failed_head', 'version', 'verdict_comment_id',
    'findings_count', 'reviewer_text_excerpt_untrusted_data',
))
_PACKET_HIGH_DEBUG_FIELDS = frozenset((
    'high_debug_report_untrusted_data', 'high_debug_report_sha256',
))
REVIEWER_EXCERPT_MAX_CHARS = 2000
HIGH_DEBUG_EXCERPT_MAX_CHARS = 6000
# review_repair._ci_failure writes '<CI_PACKET_HEADER> check=...' (section 4).
_CI_EXCERPT_PREFIX = 'CI_CHECK_FAILURE '


@dataclass(frozen=True)
class Verdict:
    """The result of a start-failure check.

    ``start_failure`` is True only when every check ran and passed.
    ``failed_check`` is the number (1-6) of the first check that did not pass,
    else None.
    """

    start_failure: bool
    failed_check: int | None = None


_START_FAILURE = Verdict(True, None)


def _not(check):
    return Verdict(False, check)


def record_checks(record):
    """Section 2 checks 1-5 on the stored record only; returns a Verdict."""
    if type(record) is not dict:
        return _not(1)
    developer = record.get('developer')
    # Check 1: a direct CLI developer that ended BLOCKED or DEVELOPER_UNAVAILABLE.
    if not (type(developer) is dict and
            developer.get('provider_profile') in ('claude', 'agy', 'gw-agent', 'codex', 'opencode') and
            developer.get('backend_session_semantics') in ('CLI_REPORTED_SESSION_ID', 'NO_NATIVE_SESSION_ID_EXPOSED') and
            developer.get('state') in ('BLOCKED', 'DEVELOPER_UNAVAILABLE')):
        return _not(1)
    # Check 2: a code raised before the spawn.
    code = developer.get('error_code')
    if type(code) is not str or code not in START_FAILURE_CODES:
        return _not(2)
    # Check 3: no result, progress or diagnosis.
    changed_files_empty = ('changed_files' not in developer or
                           (type(developer['changed_files']) is list and
                            not developer['changed_files']))
    blocked_reason_ok = ('blocked_reason' not in developer or
                         developer['blocked_reason'] is None or
                         developer['blocked_reason'] in _PRE_SPAWN_BLOCKED_REASONS)
    if not (all(field in developer and developer[field] is None
                for field in _EMPTY_RESULT_FIELDS) and
            not any(field in developer for field in _ABSENT_RESULT_FIELDS) and
            blocked_reason_ok and
            changed_files_empty):
        return _not(3)
    # Check 4: no process facts, or only a confirmed cleanup with none.
    if 'execution' not in developer:
        return _not(4)
    execution = developer['execution']
    if execution is not None and not (
            type(execution) is dict and
            all(field in execution and execution[field] is None
                for field in _NO_PROCESS_FIELDS) and
            execution.get('cleanup_status') == 'CONFIRMED'):
        return _not(4)
    # Check 5: the completion is still waiting for the executor with no result,
    # and nothing later in the Task's life has started.
    completion = record.get('completion')
    evidence = completion.get('evidence') if type(completion) is dict else None
    if not (type(completion) is dict and completion.get('state') == 'WAITING_EXECUTOR' and
            type(evidence) is dict and 'result_head' in evidence and
            evidence['result_head'] is None):
        return _not(5)
    for field in _NO_PROGRESS_RECORD_FIELDS:
        value = record.get(field)
        if value is not None and not (type(value) is list and not value):
            return _not(5)
    return _START_FAILURE


def classify(record, d6_unchanged):
    """Section 2 checks 1-6; returns a Verdict.

    ``d6_unchanged(source_sha, write_paths=...)`` is the caller's live D6 probe
    (task_submission_cli._d6_unchanged_probe). Only an exact True passes check
    6; False, any other value or a raised exception does not.
    """
    verdict = record_checks(record)
    if not verdict.start_failure:
        return verdict
    developer = record['developer']
    try:
        unchanged = d6_unchanged(developer.get('source_sha'),
                                 write_paths=developer.get('write_paths'))
    except Exception:
        return _not(6)
    if unchanged is not True:
        return _not(6)
    return _START_FAILURE


def canonical(value):
    """Section 4 encoding: the TASK_JSON and packet_sha256 serialization (pure ASCII)."""
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'))


def repair_packet_shape_ok(value):
    """The rules of task_submission_cli._validate_repair_packet, as a predicate."""
    if (type(value) is not dict or not _PACKET_FIELDS.issubset(value) or
            set(value) - _PACKET_FIELDS - _PACKET_HIGH_DEBUG_FIELDS or
            type(value.get('task')) is not str or
            type(value.get('executor_task')) is not str or
            type(value.get('pr')) is not int or
            type(value.get('failed_head')) is not str or
            len(value['failed_head']) != 40 or
            type(value.get('version')) is not int or
            type(value.get('verdict_comment_id')) is not int or
            type(value.get('findings_count')) is not int or
            type(value.get('reviewer_text_excerpt_untrusted_data')) is not str or
            len(value['reviewer_text_excerpt_untrusted_data']) > REVIEWER_EXCERPT_MAX_CHARS):
        return False
    if set(value) != _PACKET_FIELDS:
        if (not _PACKET_HIGH_DEBUG_FIELDS.issubset(value) or
                type(value.get('high_debug_report_untrusted_data')) is not str or
                len(value['high_debug_report_untrusted_data']) > HIGH_DEBUG_EXCERPT_MAX_CHARS or
                type(value.get('high_debug_report_sha256')) is not str or
                re.fullmatch(r'[0-9a-f]{64}', value['high_debug_report_sha256']) is None):
            return False
    return True


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError('invalid JSON constant')


def decode_repair_packet(text):
    """Return the packet dict stored as ``text``, or None.

    ``text`` must be ASCII, decode strictly to a dict that passes the packet
    rules, and be exactly ``canonical`` of that dict, so the stored text is the
    only encoding of the packet. The byte bound is the store's
    (task_store.REPAIR_PACKET_MAX_BYTES).
    """
    if type(text) is not str or not text.isascii():
        return None
    try:
        value = json.loads(text, object_pairs_hook=_unique_pairs,
                           parse_constant=_reject_constant)
    except (ValueError, TypeError, RecursionError):
        return None
    if not repair_packet_shape_ok(value):
        return None
    try:
        if canonical(value) != text:
            return None
    except (ValueError, TypeError, RecursionError):
        return None
    return value


def _sha256_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _ci_identity_sha256(base):
    """The review_repair._ci_failure identity digest rebuilt from a CI packet, or None."""
    from . import review_repair
    checks = '|'.join(re.escape(name) for name in review_repair.CI_REPAIR_CHECKS)
    header = re.compile(re.escape(review_repair.CI_PACKET_HEADER) +
                        r' check=(' + checks + r') run=([1-9][0-9]*)'
                        r' conclusion=failure head=([0-9a-f]{40})')
    first_line = base['reviewer_text_excerpt_untrusted_data'].split('\n', 1)[0]
    match = header.fullmatch(first_line)
    if match is None:
        return None
    name, run_id, head = match.groups()
    if head != base['failed_head']:
        return None
    identity = {'kind': 'CI', 'task': base['task'], 'pr': base['pr'],
                'executor_task': base['executor_task'], 'head': head,
                'check_name': name, 'check_run_id': int(run_id),
                'conclusion': 'failure'}
    return _sha256_text(canonical(identity))


def _matches(packet, claim, root):
    if not (repair_packet_shape_ok(packet) and type(claim) is dict and type(root) is dict):
        return False
    # Identity: P matches the claim C and its root Task R.
    if not (packet['task'] == root.get('task_id') and
            packet['pr'] == claim.get('pr') and
            packet['executor_task'] == claim.get('executor_task') and
            packet['failed_head'] == claim.get('head_sha')):
        return False
    if 'comment_id' not in claim:
        return False
    comment_id = claim['comment_id']
    if comment_id is not None:
        # A review claim (review_repair.py: comment_id is the verdict comment).
        if (type(comment_id) is not int or packet['version'] != claim.get('version') or
                packet['verdict_comment_id'] != comment_id):
            return False
    elif packet['version'] != 0 or packet['verdict_comment_id'] != 0:
        # A test claim (EC2 remote test or CI) has version and comment 0.
        return False
    # Base digest: B is P without its High Debug fields.
    base = {key: value for key, value in packet.items()
            if key not in _PACKET_HIGH_DEBUG_FIELDS}
    if (comment_id is None and
            base['reviewer_text_excerpt_untrusted_data'].startswith(_CI_EXCERPT_PREFIX)):
        digest = _ci_identity_sha256(base)
    else:
        digest = _sha256_text(canonical(base))
    stored_digest = claim.get('packet_sha256')
    if digest is None or type(stored_digest) is not str or digest != stored_digest:
        return False
    # High Debug binding (repair 2 and 3).
    if 'high_debug_report_sha256' not in claim:
        return not any(field in packet for field in _PACKET_HIGH_DEBUG_FIELDS)
    report_sha256 = claim['high_debug_report_sha256']
    if (type(report_sha256) is not str or
            packet.get('high_debug_report_sha256') != report_sha256 or
            'high_debug_report_untrusted_data' not in packet):
        return False
    family = claim.get('family', f"pr-{claim.get('pr')}")
    entries = root.get('high_debug', [])
    if type(entries) is not list:
        return False
    return any(type(entry) is dict and entry.get('family') == family and
               entry.get('no_pr') is not True and
               entry.get('status') == 'RCA_CONFIRMED' and
               entry.get('examined_sha') == claim.get('head_sha') and
               entry.get('report_sha256') == report_sha256 and
               type(entry.get('report')) is str and
               packet['high_debug_report_untrusted_data'] ==
               entry['report'][:HIGH_DEBUG_EXCERPT_MAX_CHARS]
               for entry in entries)


def repair_packet_matches(packet, claim, root):
    """Section 4 "Packet binding": True only when packet P is bound to claim C of root R.

    Pure. Any miss, malformed input or error gives False; the caller refuses
    START_RETRY_REPAIR_PACKET_MISMATCH.
    """
    try:
        return _matches(packet, claim, root) is True
    except Exception:
        return False
