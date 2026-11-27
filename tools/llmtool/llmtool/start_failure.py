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

from .errors import LLMToolError

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
# #2793 A13.0: a verification call that names no profile uses the default one.
_PROFILE_UNSET = object()


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


def _ci_identity_sha256(base, profile=_PROFILE_UNSET):
    """The review_repair._ci_failure identity digest rebuilt from a CI packet, or None.

    A CI packet is bound to the exact check name that was accepted when it was
    built (#2793 A13.0). With a profile, only that profile's names are accepted;
    without one, the name recorded in the packet is validated as a profile name
    and the claim digest proves it was the built name. A name outside the given
    profile, or a malformed name, returns None, so the packet never binds.
    """
    from . import review_repair
    if profile is _PROFILE_UNSET:
        from . import ci_profile
        name_pattern = r'(.{1,100})'
    else:
        try:
            checks = review_repair._ci_repair_checks(profile)
        except Exception:
            return None
        name_pattern = '(' + '|'.join(re.escape(name) for name in checks) + ')'
    header = re.compile(re.escape(review_repair.CI_PACKET_HEADER) +
                        r' check=' + name_pattern + r' run=([1-9][0-9]*)'
                        r' conclusion=failure head=([0-9a-f]{40})')
    first_line = base['reviewer_text_excerpt_untrusted_data'].split('\n', 1)[0]
    match = header.fullmatch(first_line)
    if match is None:
        return None
    name, run_id, head = match.groups()
    if profile is _PROFILE_UNSET:
        try:
            ci_profile._validate_name(name)
        except Exception:
            return None
    if head != base['failed_head']:
        return None
    identity = {'kind': 'CI', 'task': base['task'], 'pr': base['pr'],
                'executor_task': base['executor_task'], 'head': head,
                'check_name': name, 'check_run_id': int(run_id),
                'conclusion': 'failure'}
    return _sha256_text(canonical(identity))


def inherited_rca(store, root, packet, *, source_task_id=None, report_sha256=None):
    """Find a related RCA with matching formal findings and run evidence."""
    from . import high_debug, lineage_counts, review_result, task_submission_cli
    try:
        members = lineage_counts.debug_lineage_members(store, root)
        if not members:
            return None
        target = review_result.diagnosis_findings(
            root, packet['failed_head'], packet['executor_task'], pr=packet['pr'],
            version=packet['version'], excerpt=packet.get('reviewer_text_excerpt_untrusted_data'))
        if target is None:
            return None
        for task_id, task in members.items():
            if (task_id == root['task_id'] or
                    source_task_id is not None and task_id != source_task_id):
                continue
            executor = (task.get('completion') or {}).get('executor_task_id')
            for entry in reversed(task.get('high_debug', [])):
                if entry.get('status') != 'RCA_CONFIRMED' or entry.get('no_pr') is True:
                    continue
                if report_sha256 is not None and entry.get('report_sha256') != report_sha256:
                    continue
                family = re.fullmatch(r'pr-([1-9][0-9]*)', entry.get('family', ''))
                if family is None:
                    continue
                head = entry['examined_sha']
                d6 = entry.get('d6', '')
                attempt = re.search(r'-hd([1-9][0-9]*)-[0-9a-f]{7}\Z', d6)
                if (not isinstance(executor, str) or attempt is None or
                        d6 != high_debug._d6_name(executor, int(attempt[1]), head)):
                    continue
                findings = review_result.diagnosis_findings(
                    task, head, executor, before=entry['recorded_at'], pr=int(family.group(1)))
                if findings != target:
                    continue
                try:
                    task_submission_cli._require_bound_high_debug_run(task, task_id, entry, executor)
                except ValueError:
                    continue
                return task_id, entry
    except (LLMToolError, AttributeError, KeyError, TypeError, ValueError):
        return None
    return None


def _matches(packet, claim, root, store, profile=_PROFILE_UNSET):
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
        digest = _ci_identity_sha256(base, profile)
    else:
        digest = _sha256_text(canonical(base))
    stored_digest = claim.get('packet_sha256')
    if digest is None or type(stored_digest) is not str or digest != stored_digest:
        return False
    # Existing claims retain the RCA binding recorded when they were created.
    if 'high_debug_report_sha256' not in claim:
        return not any(field in packet for field in _PACKET_HIGH_DEBUG_FIELDS)
    report_sha256 = claim['high_debug_report_sha256']
    if (type(report_sha256) is not str or
            packet.get('high_debug_report_sha256') != report_sha256 or
            'high_debug_report_untrusted_data' not in packet):
        return False
    source_task = claim.get('high_debug_source_task')
    if source_task is not None:
        source = inherited_rca(store, root, packet, source_task_id=source_task,
                               report_sha256=report_sha256)
        return (source is not None and packet['high_debug_report_untrusted_data'] ==
                source[1]['report'][:HIGH_DEBUG_EXCERPT_MAX_CHARS])
    return bool(local_rca_entries(packet, claim, root))


def local_rca_entries(packet, claim, root):
    """Local runs that match the claim's family, digest, report and permitted HEAD."""
    family = claim.get('family', f"pr-{claim.get('pr')}")
    bound_heads = {claim.get('head_sha')}
    for earlier in root.get('review_repair_claims', []):
        if earlier.get('family', f"pr-{earlier.get('pr')}") != family:
            continue
        if earlier.get('attempt') >= claim.get('attempt', 0):
            break
        bound_heads.add(earlier.get('head_sha'))
    entries = root.get('high_debug', [])
    if type(entries) is not list:
        return []
    return [entry for entry in entries if type(entry) is dict and entry.get('family') == family and
               entry.get('no_pr') is not True and
               entry.get('status') == 'RCA_CONFIRMED' and
               entry.get('examined_sha') in bound_heads and
               entry.get('report_sha256') == claim.get('high_debug_report_sha256') and
               type(entry.get('report')) is str and
               packet['high_debug_report_untrusted_data'] ==
               entry['report'][:HIGH_DEBUG_EXCERPT_MAX_CHARS]
               ]


def repair_packet_matches(packet, claim, root, store=None, profile=_PROFILE_UNSET):
    """Section 4 "Packet binding": True only when packet P is bound to claim C of root R.

    Pure. Any miss, malformed input or error gives False; the caller refuses
    START_RETRY_REPAIR_PACKET_MISMATCH. For a CI packet the accepted check names
    come from the CI profile (#2793 A13.0): the default profile when profile is
    not given, else that profile.
    """
    try:
        return _matches(packet, claim, root, store, profile) is True
    except Exception:
        return False
