"""#2188 start-failure retry: pure classification and repair-packet binding.

Design: docs/design/start-failure-retry-2188.md, sections 2 and 4 (PR 1, not
wired). Nothing here reads or writes a Task, a D6 or the network. The live D6
check (section 2 check 6) is the caller's injected ``d6_unchanged`` probe.

Every check fails closed: a value that is unknown, malformed or failing makes
the record "not a start failure", and a packet that cannot be bound does not
match.

#2310 adds the gw-agent pre-model failure (gw_agent_record_checks) and the
route dispatchers restart_record_checks and classify_restart; the Claude
checks (record_checks, classify) are unchanged. #2310 F1 extends the gw-agent
checks to a failure after the DISPATCHING write persisted but before the
route was called, proven by the dispatch audit or the code.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

# Section 2 check 2. Each code below is raised by run_route before the provider
# process starts (confirmed at source 89dbd330, developer_routes/base.py:774-928):
# - CLAUDE_PROFILE_INVALID: ClaudeRoute.validate_profile (claude.py:68), called by
#   DeveloperRoute.validate_request (base.py:213), which run_route calls first
#   (base.py:799).
# - CLAUDE_REQUEST_INVALID: ClaudeRoute.validate_request (claude.py:103) and the
#   base request checks (base.py:201-234); run_route also raises it at
#   base.py:795, :797, :803, :892 and :925. All are before execute (base.py:928).
# - CLAUDE_CONFIG_INVALID: ClaudeRoute.validate_request (claude.py:105, :109),
#   inside base.py:799.
# - CLAUDE_ENV_INVALID: ClaudeRoute.build_env (claude.py:90), called at
#   base.py:916, before execute (base.py:928).
# None of them is raised after base.py:928, so a record with one of them never
# had a provider process. Any code that could follow a spawn is never here.
START_FAILURE_CODES = frozenset((
    'CLAUDE_PROFILE_INVALID', 'CLAUDE_ENV_INVALID',
    'CLAUDE_REQUEST_INVALID', 'CLAUDE_CONFIG_INVALID',
))

# Section 2 "Never a start failure": codes raised after a run (or possibly after
# one), and the Owner denials. Listed for review and tests; the classifier
# admits only START_FAILURE_CODES, so any other code is refused as well.
NEVER_START_FAILURE_CODES = frozenset((
    'CLAUDE_RESULT_UNKNOWN', 'CLAUDE_NO_CHANGES', 'CLAUDE_WRITE_SCOPE_VIOLATION',
    'CLAUDE_OUTPUT_TOO_LARGE', 'CLAUDE_OUTPUT_INVALID', 'CLAUDE_STATUS_FAILED',
    'CLAUDE_QUOTA_EXHAUSTED', 'CLAUDE_D6_BINDING_CHANGED',
    'CLAUDE_DISPATCH_DENIED', 'OWNER_DISPATCH_DENIED',
))

# Section 2 check 3: present and None, and absent.
_EMPTY_RESULT_FIELDS = ('artifact', 'artifact_sha256', 'usage')
_ABSENT_RESULT_FIELDS = ('progress', 'no_changes_diagnosis', 'blocked_reason')
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
    # Check 1: a Claude CLI developer that ended BLOCKED.
    if not (type(developer) is dict and
            developer.get('provider_profile') == 'claude' and
            developer.get('backend_session_semantics') == 'CLI_REPORTED_SESSION_ID' and
            developer.get('state') == 'BLOCKED'):
        return _not(1)
    # Check 2: a code raised before the spawn.
    code = developer.get('error_code')
    if type(code) is not str or code not in START_FAILURE_CODES:
        return _not(2)
    # Check 3: no result, progress or diagnosis.
    changed_files_empty = ('changed_files' not in developer or
                           (type(developer['changed_files']) is list and
                            not developer['changed_files']))
    if not (all(field in developer and developer[field] is None
                for field in _EMPTY_RESULT_FIELDS) and
            not any(field in developer for field in _ABSENT_RESULT_FIELDS) and
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


# #2310: a gw-agent result also carries these; a pre-model record has none.
_GW_AGENT_ABSENT_RESULT_FIELDS = _ABSENT_RESULT_FIELDS + (
    'audit_sha256', 'audit_copy_path_sha256',
)

# #2310 F1: the route identity the gw-agent DISPATCHING write binds
# (worker_dispatch._PROVIDER_TYPES['gw-agent']).
_GW_AGENT_PROVIDER_TYPE = 'gw-agent-cli'

# #2310 F1: the codes run_task_gw_agent_delegate writes after its DISPATCHING
# write persisted and before it calls the route, when building GwAgentRoute or
# its validate_profile raises (the RouteError and Exception handlers right
# after authorize_dispatch), or authorize_dispatch raises something that is
# not a refusal (the same Exception handler). Nothing the route raises after
# the gateway starts has either code: GwAgentRoute raises
# GW_AGENT_CONFIG_INVALID only while it is built (gw_agent.py:181-244) or
# writes its input files (gw_agent.py:359), and its post-launch codes are
# GW_AGENT_<status> (gw_agent.py:553, statuses at :45), GW_AGENT_OUTPUT_INVALID,
# GW_AGENT_MODEL_UNVERIFIED and GW_AGENT_TASK_ID_MISMATCH[_<gateway code>],
# each stored with the DEVELOPER_ prefix. Both codes are also listed as
# pre-launch in task_submission_cli._POST_RUN_NO_WORK_GW_AGENT_REFUSALS.
GW_AGENT_PRE_ROUTE_CODES = frozenset((
    'DEVELOPER_GW_AGENT_CONFIG_INVALID', 'DEVELOPER_DISPATCH_FAILED',
))


def _gw_agent_dispatch_audit(audit, assignment_id):
    """#2310 F1: the DISPATCH audit of one gw-agent assignment.

    Returns ``(transitions, adapter_allowed, refused_codes)`` or None. An
    entry without an assignment counts as this one. ``transitions`` counts
    ALLOWED DISPATCH entries without an ``authorization_id``: the store gate's
    entry for the PENDING to DISPATCHING write. ``adapter_allowed`` counts
    ALLOWED DISPATCH entries with one: worker_dispatch.authorize_dispatch
    saves that entry before it returns, and the delegate calls the route only
    after it returned. ``refused_codes`` holds ``DEVELOPER_<reason_code>`` of
    every BLOCKED DISPATCH entry, as the delegate stores a refusal. None when
    the audit is unreadable, or when an ALLOWED entry of any other action but
    START_RETRY names the assignment (fail closed).
    """
    if type(audit) is not list:
        return None
    transitions = adapter_allowed = 0
    refused_codes = set()
    for entry in audit:
        if type(entry) is not dict:
            return None
        if entry.get('assignment_id') not in (None, assignment_id):
            continue
        action, decision = entry.get('action'), entry.get('decision')
        if action == 'START_RETRY':
            continue
        if decision == 'BLOCKED':
            reason = entry.get('reason_code')
            if action == 'DISPATCH' and type(reason) is str:
                refused_codes.add(('DEVELOPER_' + reason)[:64])
            continue
        if action != 'DISPATCH' or decision != 'ALLOWED':
            return None
        if 'authorization_id' in entry:
            adapter_allowed += 1
        else:
            transitions += 1
    return transitions, adapter_allowed, frozenset(refused_codes)


def gw_agent_record_checks(record):
    """#2310: checks 1-5 for a gw-agent developer refused before any model call.

    The gw-agent delegate (task_submission_cli.run_task_gw_agent_delegate)
    runs the route only after its DISPATCHING write has persisted, and that
    write is the one that records ``provider_type`` and ``model``. The store
    never lets those identity fields go back to None (worker_dispatch REASSIGN
    needs a PENDING developer). Two records are admitted:

    - Unbound: both identity fields still None. The failure was persisted
      from the DISPATCHING write's own refusal (the Owner gate or a rejected
      write), so the route never ran. No ALLOWED DISPATCH entry exists.
    - Bound (#2310 F1): the DISPATCHING write persisted (``gw-agent-cli`` and
      a model), with exactly its one ALLOWED transition entry, and a failure
      proven to come before the route call: either the adapter gate refused
      (its BLOCKED DISPATCH entry gives the stored code, and no adapter
      authorization was saved for the assignment), or the code is in
      GW_AGENT_PRE_ROUTE_CODES, with at most the one adapter authorization
      the delegate makes before it builds the route.

    Any other gw-agent record is refused, so a run that reached the provider
    (any model call) is never a start failure.
    """
    if type(record) is not dict:
        return _not(1)
    developer = record.get('developer')
    # Check 1: a gw-agent CLI developer that ended DEVELOPER_UNAVAILABLE.
    if not (type(developer) is dict and
            developer.get('provider_profile') == 'gw-agent' and
            developer.get('backend_session_semantics') == 'CLI_REPORTED_SESSION_ID' and
            developer.get('state') == 'DEVELOPER_UNAVAILABLE'):
        return _not(1)
    # Check 2: a code the gw-agent delegate writes (DEVELOPER_*), and either no
    # route identity (the DISPATCHING write never persisted), or the bound
    # identity with a failure that is shown to be before the route call.
    code = developer.get('error_code')
    assignment_id = developer.get('assignment_id')
    audit = _gw_agent_dispatch_audit(record.get('dispatch_audit', []), assignment_id)
    unbound = ('provider_type' in developer and developer['provider_type'] is None and
               'model' in developer and developer['model'] is None)
    bound = (developer.get('provider_type') == _GW_AGENT_PROVIDER_TYPE and
             type(developer.get('model')) is str and bool(developer['model']))
    pre_route = type(code) is str and code in GW_AGENT_PRE_ROUTE_CODES
    adapter_refused = (type(code) is str and audit is not None and code in audit[2])
    if not (type(code) is str and code.startswith('DEVELOPER_') and
            (unbound or (bound and (pre_route or adapter_refused)))):
        return _not(2)
    # Check 3: no result, progress, diagnosis or gateway audit.
    if not _no_result(developer, _GW_AGENT_ABSENT_RESULT_FIELDS):
        return _not(3)
    # Check 4: no execution at all, and only the dispatch audit that check 2's
    # case allows for this assignment: refusals and the START_RETRY that
    # created it always; unbound, no ALLOWED DISPATCH entry; bound, the one
    # ALLOWED transition, plus one adapter authorization only for a
    # GW_AGENT_PRE_ROUTE_CODES failure. An entry without an assignment counts
    # as this one.
    if ('execution' not in developer or developer['execution'] is not None or
            type(assignment_id) is not str or audit is None):
        return _not(4)
    transitions, adapter_allowed, _refused = audit
    if unbound:
        if transitions or adapter_allowed:
            return _not(4)
    elif not (transitions == 1 and
              (adapter_allowed == 0 or (adapter_allowed == 1 and pre_route))):
        return _not(4)
    # Check 5: as for Claude.
    if not _still_waiting(record):
        return _not(5)
    return _START_FAILURE


def restart_record_checks(record):
    """#2310: the record checks of the stored developer's own route.

    A gw-agent developer takes gw_agent_record_checks; every other record takes
    the #2188 Claude record_checks, unchanged.
    """
    developer = record.get('developer') if type(record) is dict else None
    if type(developer) is dict and developer.get('provider_profile') == 'gw-agent':
        return gw_agent_record_checks(record)
    return record_checks(record)


def _no_result(developer, absent_fields):
    """Section 2 check 3 with the route's list of fields that must be absent."""
    changed_files_empty = ('changed_files' not in developer or
                           (type(developer['changed_files']) is list and
                            not developer['changed_files']))
    return (all(field in developer and developer[field] is None
                for field in _EMPTY_RESULT_FIELDS) and
            not any(field in developer for field in absent_fields) and
            changed_files_empty)


def _still_waiting(record):
    """Section 2 check 5: the completion waits with no result, nothing later started."""
    completion = record.get('completion')
    evidence = completion.get('evidence') if type(completion) is dict else None
    if not (type(completion) is dict and completion.get('state') == 'WAITING_EXECUTOR' and
            type(evidence) is dict and 'result_head' in evidence and
            evidence['result_head'] is None):
        return False
    for field in _NO_PROGRESS_RECORD_FIELDS:
        value = record.get(field)
        if value is not None and not (type(value) is list and not value):
            return False
    return True


def _probe_d6(record, d6_unchanged):
    """Section 2 check 6 on a record that passed checks 1-5."""
    developer = record['developer']
    try:
        unchanged = d6_unchanged(developer.get('source_sha'),
                                 write_paths=developer.get('write_paths'))
    except Exception:
        return _not(6)
    if unchanged is not True:
        return _not(6)
    return _START_FAILURE


def classify_restart(record, d6_unchanged):
    """#2310: restart_record_checks, then the live D6 check 6; returns a Verdict.

    For a Claude record this is exactly ``classify``.
    """
    verdict = restart_record_checks(record)
    if not verdict.start_failure:
        return verdict
    return _probe_d6(record, d6_unchanged)


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
