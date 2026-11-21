"""Recover the single writer of a clean direct-Developer worktree."""
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat

from . import review_repair, start_failure, task_completion
from .errors import LLMToolError

AGENTS = {'opencode': 'opencode-developer', 'agy': 'agy-developer'}


def _refuse(code):
    raise LLMToolError(code) from None


@dataclass(frozen=True)
class Reassignment:
    current: dict
    original: object
    successor: object
    snapshot: object
    assignment: str
    repair_packet: dict | None
    resumed: bool = False


def test_policy(plan):
    callback = plan.current['developer'].get('direct_result_callback') or {}
    policy = (callback.get('evidence') or {}).get('test_policy', 'LOCAL')
    if policy not in ('LOCAL', 'REMOTE_RECEIPT'):
        _refuse('DIRECT_REASSIGN_TEST_POLICY_UNVERIFIED')
    return policy


def replacement_candidate(developer):
    """A hint for the continuation. The lease and CAS decide admission."""
    if type(developer) is not dict or developer.get('provider_profile') not in AGENTS:
        return False
    state = developer.get('state')
    phase = developer.get('dispatch_boundary')
    return (state == 'DEVELOPER_PENDING' or
            (state == 'DEVELOPER_DISPATCHING' and phase == 'PREPARED') or
            (state == 'DEVELOPER_OUTCOME_UNKNOWN' and phase == 'STOPPED'))


# #2447 (Owner decision A on #2413): llmtool never infers "not started" from
# missing launch evidence, and it uses no date cutoff. A direct assignment
# recorded before launch-boundary recording (#2440) carries no dispatch_boundary
# and no launch record; that absence proves nothing. It is voided only by an
# explicit command that carries an Owner relay URL for that exact repair Task,
# and only when every other check below also holds.
_LSOF_PATHS = ('/usr/sbin/lsof', '/usr/bin/lsof')
_PS_PATHS = ('/bin/ps', '/usr/bin/ps')
_TASK_ID = re.compile(r'[0-9a-f]{32}\Z')
# The PM relay marker of an Owner decision (common-core rule 7: ``actor=PM``).
# The relayed comment must also name this exact repair Task id and explicitly
# approve this void, so an ordinary PM progress comment or an unanswered Owner
# request never authorises one. The repository's activity marker is
# ``<!-- activity: key=value ... -->``; an Owner answer is an ``owner_*``
# decision whose status or decision is an approval.
_OWNER_RELAY_MARKER = re.compile(
    r'(?<![A-Za-z0-9_=-])actor=PM(?![A-Za-z0-9_=-])')
_ACTIVITY_MARKER = re.compile(r'<!--\s*activity:\s*([^>]*?)\s*-->')
_ACTIVITY_ATTRIBUTE = re.compile(r'([a-z_][a-z0-9_]*)=([^\s>]+)')
_OWNER_APPROVAL_STATUSES = frozenset((
    'APPROVED', 'APPROVAL', 'ACCEPTED', 'ANSWERED', 'VOID', 'VOID_APPROVED',
    'APPROVED_VOID'))
_OWNER_APPROVAL_DECISIONS = frozenset((
    'APPROVE', 'APPROVED', 'ACCEPT', 'ACCEPTED', 'VOID', 'VOID_APPROVED',
    'APPROVED_VOID', 'VOID_HISTORICAL_DISPATCH'))
# The explicit reason token an Owner approval may carry instead of an activity
# marker. It is the stored void reason, not an ordinary PM progress word.
_OWNER_VOID_TOKEN = re.compile(
    r'(?i)(?<![A-Za-z0-9_])OWNER_APPROVED_VOID(?![A-Za-z0-9_])')
_VOID_DIRECTIVE = re.compile(
    r'(?i)(?<![A-Za-z0-9_])void(?:e[ds]|ing)?(?![A-Za-z0-9_])')
_PATH_CHAR_BEFORE = r'(?<![A-Za-z0-9._@+%/-])'
_PATH_CHAR_AFTER = r'(?![A-Za-z0-9._@+%-])'
# Result or recovery fields a never-launched historical assignment cannot carry.
_VOID_FORBIDDEN_FIELDS = frozenset((
    'reported', 'changed_files', 'progress', 'final_answer', 'result_status',
    'normalizations', 'rejection_detail', 'rejected_output_capture',
    'no_changes_diagnosis', 'claude_failure_evidence', 'direct_handoffs',
    'voided_assignments', 'dispatch_boundary'))


def historical_candidate(developer):
    """True for a direct DISPATCHING record that has no boundary and no launch.

    Only a hint: void_historical decides, and a missing launch record alone
    never proves that nothing started.
    """
    return (type(developer) is dict and developer.get('provider_profile') in AGENTS and
            developer.get('state') == 'DEVELOPER_DISPATCHING' and
            'dispatch_boundary' not in developer and developer.get('execution') is None)


def _capture(argv):
    """Run one read-only listing; return (stdout text, own child pid). Fails closed."""
    import subprocess
    try:
        child = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env={'PATH': '/usr/bin:/bin'})
        try:
            output, _ = child.communicate(timeout=30)
        except BaseException:
            child.kill()
            child.wait()
            raise
    except (OSError, subprocess.SubprocessError):
        _refuse('DIRECT_HISTORICAL_VOID_PROCESS_UNVERIFIED')
    if child.returncode != 0 or type(output) is not bytes:
        _refuse('DIRECT_HISTORICAL_VOID_PROCESS_UNVERIFIED')
    return output.decode('utf-8', 'surrogateescape'), child.pid


def d6_processes(root):
    """PIDs of processes bound to the D6 at root by cwd, open file or command line.

    Two listings, both required (fails closed on a missing tool, a failing tool
    or an unreadable answer):

    - lsof lists every open file of this user's processes: the working
      directory, executables, mapped files and every open descriptor. Any name
      inside root counts, so a process with another cwd that holds a D6 file
      open is found.
    - ps lists every process's full command line. Any argument naming root
      (also as ``--dir=ROOT``) counts, so a provider started against the D6
      from elsewhere is found.

    The caller's own process and the listing children are not counted.
    """
    lsof = next((path for path in _LSOF_PATHS if os.access(path, os.X_OK)), None)
    ps = next((path for path in _PS_PATHS if os.access(path, os.X_OK)), None)
    if (lsof is None or ps is None or type(root) is not str or not os.path.isabs(root)):
        _refuse('DIRECT_HISTORICAL_VOID_PROCESS_UNVERIFIED')
    base = os.path.realpath(root)
    names = {base.rstrip('/'), root.rstrip('/')}
    named = re.compile('|'.join(
        _PATH_CHAR_BEFORE + re.escape(name) + _PATH_CHAR_AFTER
        for name in sorted(names) if name))
    files, lsof_child = _capture(
        [lsof, '-w', '-n', '-P', '-a', '-u', str(os.getuid()), '-Fpn'])
    commands, ps_child = _capture([ps, '-ww', '-A', '-o', 'pid=,args='])
    ignored = {os.getpid(), lsof_child, ps_child}
    pids = []
    pid = None
    for line in files.splitlines():
        if line.startswith('p') and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith('n') and pid is not None:
            where = os.path.realpath(line[1:]) if line[1:].startswith('/') else line[1:]
            if (where == base or where.startswith(base.rstrip('/') + '/')) and pid not in ignored:
                pids.append(pid)
    for line in commands.splitlines():
        fields = line.split(None, 1)
        if len(fields) != 2 or not fields[0].isdigit():
            continue
        if int(fields[0]) not in ignored and named.search(fields[1]):
            pids.append(int(fields[0]))
    return sorted(set(pids))


def _check_no_process(root, process_probe):
    try:
        pids = (process_probe or d6_processes)(root)
    except LLMToolError:
        raise
    except Exception:
        _refuse('DIRECT_HISTORICAL_VOID_PROCESS_UNVERIFIED')
    if type(pids) is not list:
        _refuse('DIRECT_HISTORICAL_VOID_PROCESS_UNVERIFIED')
    if pids:
        _refuse('DIRECT_HISTORICAL_VOID_PROCESS_LIVE')


def owner_relay_pattern():
    """The one accepted Owner relay URL shape on this repository."""
    from . import trust

    try:
        slug = trust.load_trust().repository['slug']
    except Exception:
        _refuse('DIRECT_HISTORICAL_VOID_OWNER_REF_INVALID')
    if type(slug) is not str:
        _refuse('DIRECT_HISTORICAL_VOID_OWNER_REF_INVALID')
    return re.compile(r'https://github\.com/' + re.escape(slug) +
                      r'/issues/([1-9][0-9]*)#issuecomment-([1-9][0-9]*)\Z')


def _read_owner_comment(owner_ref):
    """Read the linked Issue comment body; every read failure refuses.

    llmtool reads the comment itself instead of trusting a PM-supplied body.
    gh talks to this repository only. The returned canonical comment URL and
    issue URL must be exactly the ones ``owner_ref`` names, so a URL that
    aliases another issue with the same comment id is refused; the reservation
    is therefore always bound to one canonical comment identity. A missing
    comment (HTTP 404) is reported as such; any other failure is unverified.
    The body is untrusted data: it is only searched for the PM relay marker,
    this Task id and an explicit void approval.
    """
    import subprocess

    from . import trust

    match = (owner_relay_pattern().fullmatch(owner_ref)
             if type(owner_ref) is str else None)
    if match is None:
        _refuse('DIRECT_HISTORICAL_VOID_OWNER_REF_INVALID')
    issue_number, comment_id = match.group(1), match.group(2)
    try:
        slug = trust.load_trust().repository['slug']
    except Exception:
        _refuse('DIRECT_HISTORICAL_VOID_OWNER_READ_FAILED')
    if type(slug) is not str:
        _refuse('DIRECT_HISTORICAL_VOID_OWNER_READ_FAILED')
    try:
        completed = subprocess.run(
            ['gh', 'api', f'repos/{slug}/issues/comments/{comment_id}',
             '--jq', '{body: .body, html_url: .html_url, issue_url: .issue_url}'],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False, timeout=30,
            env={'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin',
                 'HOME': os.path.expanduser('~'), 'LC_ALL': 'C', 'LANG': 'C',
                 'GH_PROMPT_DISABLED': '1', 'GH_NO_UPDATE_NOTIFIER': '1'})
    except (OSError, subprocess.SubprocessError):
        _refuse('DIRECT_HISTORICAL_VOID_OWNER_READ_FAILED')
    if type(completed.stdout) is not bytes:
        _refuse('DIRECT_HISTORICAL_VOID_OWNER_READ_FAILED')
    if completed.returncode != 0:
        error = completed.stderr if type(completed.stderr) is bytes else b''
        if b'Not Found' in error or b'HTTP 404' in error:
            _refuse('DIRECT_HISTORICAL_VOID_OWNER_COMMENT_MISSING')
        _refuse('DIRECT_HISTORICAL_VOID_OWNER_READ_FAILED')
    try:
        value = json.loads(completed.stdout.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        _refuse('DIRECT_HISTORICAL_VOID_OWNER_READ_FAILED')
    if (type(value) is not dict or
            set(value) != {'body', 'html_url', 'issue_url'} or
            type(value['body']) is not str or
            type(value['html_url']) is not str or
            type(value['issue_url']) is not str):
        _refuse('DIRECT_HISTORICAL_VOID_OWNER_READ_FAILED')
    canonical = (f'https://github.com/{slug}/issues/{issue_number}'
                 f'#issuecomment-{comment_id}')
    api_issue = f'https://api.github.com/repos/{slug}/issues/{issue_number}'
    if (value['html_url'].lower() != canonical.lower() or
            value['issue_url'].lower() != api_issue.lower()):
        _refuse('DIRECT_HISTORICAL_VOID_OWNER_REF_INVALID')
    return value['body']


def _owner_void_approved(body):
    """True only for an explicit Owner approval of this historical void.

    A plain PM relay marker is not enough: an ordinary PM progress comment or
    an unanswered Owner request also carries the Task id. The body must carry
    either the explicit ``OWNER_APPROVED_VOID`` reason token or an established
    activity marker naming an ``owner_*`` decision with an approved status,
    and it must direct a void.
    """
    if _OWNER_VOID_TOKEN.search(body) is not None:
        return True
    if _VOID_DIRECTIVE.search(body) is None:
        return False
    for match in _ACTIVITY_MARKER.finditer(body):
        attributes = dict(_ACTIVITY_ATTRIBUTE.findall(match.group(1)))
        if (attributes.get('actor') == 'PM' and
                attributes.get('type', '').startswith('owner_') and
                (attributes.get('status') in _OWNER_APPROVAL_STATUSES or
                 attributes.get('decision') in _OWNER_APPROVAL_DECISIONS)):
            return True
    return False


def _relay_used_elsewhere(store, task_id, owner_ref):
    """True when this relay URL is already recorded on another Task's void."""
    for other_id in store.task_ids():
        if other_id == task_id:
            continue
        developer = store.get(other_id).get('developer')
        for entry in (developer or {}).get('voided_assignments') or []:
            if type(entry) is dict and entry.get('owner_ref') == owner_ref:
                return True
    return False


def check_owner_relay(store, task, owner_ref, owner_task):
    """The Owner decision named by the PM must be for this exact repair Task.

    llmtool reads the linked Issue comment from this repository itself: it must
    exist, carry the PM relay marker of an Owner decision (``actor=PM``), name
    this exact Task id and explicitly approve this void. The URL must be the
    canonical Issue comment link it names, and a URL already recorded for
    another Task's void is never reused. Any read failure, mismatch or prior
    reservation refuses.
    """
    task_id = task.get('task_id')
    if (type(owner_task) is not str or _TASK_ID.fullmatch(owner_task) is None or
            owner_task != task_id):
        _refuse('DIRECT_HISTORICAL_VOID_OWNER_TASK_MISMATCH')
    if type(owner_ref) is not str or owner_relay_pattern().fullmatch(owner_ref) is None:
        _refuse('DIRECT_HISTORICAL_VOID_OWNER_REF_INVALID')
    try:
        body = _read_owner_comment(owner_ref)
    except LLMToolError:
        raise
    except Exception:
        _refuse('DIRECT_HISTORICAL_VOID_OWNER_READ_FAILED')
    if type(body) is not str:
        _refuse('DIRECT_HISTORICAL_VOID_OWNER_READ_FAILED')
    if re.search(r'(?<![0-9a-f])' + re.escape(task_id) + r'(?![0-9a-f])',
                 body) is None:
        _refuse('DIRECT_HISTORICAL_VOID_OWNER_TASK_MISMATCH')
    if _OWNER_RELAY_MARKER.search(body) is None:
        _refuse('DIRECT_HISTORICAL_VOID_OWNER_MARKER_MISSING')
    if not _owner_void_approved(body):
        _refuse('DIRECT_HISTORICAL_VOID_OWNER_APPROVAL_MISSING')
    if _relay_used_elsewhere(store, task_id, owner_ref):
        _refuse('DIRECT_HISTORICAL_VOID_OWNER_TASK_MISMATCH')


def _check_void(store, task, registration, snapshot, executor_task, process_probe,
                owner_ref, owner_task):
    """Every void condition on one stored record; any miss refuses."""
    check_owner_relay(store, task, owner_ref, owner_task)
    developer = task.get('developer')
    if not historical_candidate(developer):
        _refuse('DIRECT_HISTORICAL_VOID_NOT_HISTORICAL')
    profile = developer['provider_profile']
    identity = AGENTS[profile]
    # No execution (checked above), artifact, usage, error, report or recovery trail.
    if (_VOID_FORBIDDEN_FIELDS & set(developer) or
            any(developer.get(field) is not None for field in (
                'artifact', 'artifact_sha256', 'usage', 'error_code'))):
        _refuse('DIRECT_HISTORICAL_VOID_RESULT_PRESENT')
    # No completion result, completion event or result callback.
    completion = task.get('completion') or {}
    evidence = completion.get('evidence') or {}
    callback = developer.get('direct_result_callback')
    callback_evidence = (callback or {}).get('evidence') or {}
    if (task.get('state') != 'PM_RESPONDED' or
            completion.get('state') != 'WAITING_EXECUTOR' or
            completion.get('reviewer_task_id') is not None or
            completion.get('pm_dispatch') != 'NOT_STARTED' or
            completion.get('pm_response') is not None or
            completion.get('missing_gates') != [] or
            evidence.get('result_head') is not None or
            task.get('completion_events') or task.get('completion_event_tombstones') or
            (callback is not None and
             (type(callback) is not dict or callback.get('state') != 'WAITING_EXECUTOR' or
              callback.get('pm_dispatch') != 'NOT_STARTED' or
              callback.get('pm_response') is not None or
              {'review_handoff', 'review_return'} & set(callback) or
              callback_evidence.get('result_head') is not None or
              callback_evidence.get('verification_state') != 'NOT_STARTED'))):
        _refuse('DIRECT_HISTORICAL_VOID_RESULT_PRESENT')
    # No provider session evidence anywhere in the record or the D6 registration.
    if (evidence.get('executor_native_session_id') not in (None, 'UNKNOWN') or
            evidence.get('executor_native_session') == 'VERIFIED' or
            registration.native_session_id not in (None, 'UNKNOWN') or
            callback_evidence.get('executor_native_session_id') not in (None, 'UNKNOWN')):
        _refuse('DIRECT_HISTORICAL_VOID_SESSION_EVIDENCE')
    # The D6 is the one the record names, at its source_sha, with a clean tree.
    if (completion.get('executor_task_id') != executor_task or
            registration.task_id != executor_task or
            registration.trusted_agent_id != identity or
            evidence.get('executor_identity') != identity or
            evidence.get('executor_session_ref') != registration.session_ref or
            evidence.get('root') != registration.root or
            evidence.get('common_dir') != registration.common_dir or
            evidence.get('branch') != registration.branch or
            snapshot.head != developer.get('source_sha') or snapshot.clean is not True):
        _refuse('DIRECT_HISTORICAL_VOID_D6_UNVERIFIED')
    # The same counted repair claim, if any, is still intact.
    _repair_packet(store, task, developer, registration)
    _check_no_process(registration.root, process_probe)


def void_historical(store, task, registration, snapshot, *, executor_task,
                    owner_ref, owner_task, process_probe=None):
    """Void one historical DISPATCHING assignment on an explicit Owner approval.

    This is the Owner's decision, not a finding that the provider never ran:
    the record keeps the Owner relay URL and states no launch proof. The
    assignment stays in the Task record (voided_assignments and the PREPARED
    boundary); the repair claim, family, attempt and counts are not touched. The
    ordinary #2440 reassignment then creates the new assignment. Any unmet
    condition refuses; a second or concurrent void refuses
    DIRECT_HISTORICAL_VOID_CONFLICT.

    A store-wide lock wraps the relay read, the cross-Task reservation check and
    the commit, so one relay URL can reserve at most one Task even when two
    different Tasks are voided at once.
    """
    from .task_store import TaskStoreConflict

    lock = getattr(store, 'direct_historical_void_lock', None)
    if not callable(lock):
        _refuse('DIRECT_HISTORICAL_VOID_LOCK_UNAVAILABLE')
    with lock():
        _check_void(store, task, registration, snapshot, executor_task, process_probe,
                    owner_ref, owner_task)

        def verify():
            # Under the shared reservation lock and the Task lock: relay, D6
            # and processes now.
            check_owner_relay(store, task, owner_ref, owner_task)
            if task_completion.bind_executor_task(
                    registration.task_id, expected_common_dir=registration.common_dir,
                    root=registration.root) != (registration, snapshot):
                _refuse('DIRECT_HISTORICAL_VOID_D6_UNVERIFIED')
            _check_no_process(registration.root, process_probe)
            return True

        try:
            return store.void_historical_direct_dispatch(
                task['task_id'], current=task, owner_ref=owner_ref, verify=verify)
        except TaskStoreConflict:
            _refuse('DIRECT_HISTORICAL_VOID_CONFLICT')


def _checked_scope(args, developer, read_paths, write_paths):
    if (read_paths != developer.get('read_paths') or write_paths != developer.get('write_paths') or
            args.acceptance != developer.get('acceptance') or
            args.egress_basis != developer.get('egress_basis') or
            args.selection_source != developer.get('selection_source') or
            getattr(args, 'data_class', None) != developer.get('data_class')):
        _refuse('DIRECT_REASSIGN_SCOPE_MISMATCH')


def packet_digest(packet):
    """Use the same identity for CI and excerpt-bearing review or EC2 packets."""
    text = packet.get('reviewer_text_excerpt_untrusted_data', '')
    if text.startswith(review_repair.CI_PACKET_HEADER + ' '):
        header, _, excerpt = text.partition('\n')
        match = re.fullmatch(
            r'CI_CHECK_FAILURE check=(.+) run=([1-9][0-9]*) conclusion=failure head=([0-9a-f]{40})',
            header)
        if match is None or match[3] != packet.get('failed_head'):
            _refuse('DIRECT_REASSIGN_REPAIR_PACKET_UNVERIFIED')
        _, digest = review_repair._ci_failure(
            {'task_id': packet['task']}, executor_task=packet['executor_task'],
            pr_number=packet['pr'], ci_failure={'head': match[3], 'check_name': match[1],
                'check_run_id': int(match[2]), 'conclusion': 'failure', 'excerpt': excerpt})
        return digest
    return hashlib.sha256(start_failure.canonical(packet).encode()).hexdigest()


def _repair_packet(store, task, developer, registration, *, require_closed=True):
    # Repair Tasks keep their original ID, so the parent claim still counts.
    matches = []
    prior_session_closed = registration.native_session_id in (None, 'UNKNOWN')
    for task_id in store.task_ids():
        if task_id == task['task_id']:
            continue
        parent = store.get(task_id)
        parent_dev = parent.get('developer') or {}
        parent_completion = parent.get('completion') or {}
        execution = parent_dev.get('execution') or {}
        if (parent_completion.get('executor_task_id') == registration.task_id and
                parent_dev.get('state') == 'DEVELOPER_RESPONDED' and
                execution.get('native_session_id') == registration.native_session_id and
                execution.get('cleanup_status') == 'CONFIRMED'):
            prior_session_closed = True
        for claim in parent.get('review_repair_claims', []):
            if claim.get('repair_task_id') == task['task_id']:
                matches.append((parent, claim))
    if require_closed and not prior_session_closed:
        _refuse('DIRECT_REASSIGN_SESSION_UNVERIFIED')
    if not matches:
        if task.get('repair_packet') is not None or task.get('request', '').startswith('REPAIR '):
            _refuse('DIRECT_REASSIGN_REPAIR_CLAIM_UNVERIFIED')
        return None
    if len(matches) != 1:
        _refuse('DIRECT_REASSIGN_REPAIR_CLAIM_UNVERIFIED')
    parent, claim = matches[0]
    if (claim.get('executor_task') != registration.task_id or
            claim.get('head_sha') != developer.get('source_sha') or
            claim.get('state') not in ('CLAIMED', 'DISPATCHED', 'UNKNOWN')):
        _refuse('DIRECT_REASSIGN_REPAIR_CLAIM_UNVERIFIED')
    packets = []
    if task.get('repair_packet') is not None:
        saved_packet = start_failure.decode_repair_packet(task['repair_packet'])
        if saved_packet is None:
            _refuse('DIRECT_REASSIGN_REPAIR_PACKET_UNVERIFIED')
        packets.append(saved_packet)
    if claim.get('family', '').endswith('-ec2'):
        for receipt in parent.get('remote_test_receipts', []):
            if receipt.get('source_sha') != claim['head_sha'] or receipt.get('result') != 'FAIL':
                continue
            try:
                packet = review_repair._remote_test_failure(
                    parent, executor_task=registration.task_id, pr_number=claim['pr'],
                    receipt=receipt)
            except LLMToolError:
                continue
            packets.append(packet)
    for packet in packets:
        if (packet.get('task') == parent['task_id'] and packet.get('pr') == claim['pr'] and
                packet.get('failed_head') == claim['head_sha'] and
                packet.get('executor_task') == registration.task_id and
                packet_digest(packet) == claim.get('packet_sha256')):
            return packet
    _refuse('DIRECT_REASSIGN_REPAIR_PACKET_UNVERIFIED')


def inspect(args, store, task, registration, snapshot, profile, read_paths, write_paths):
    """Inspect the original assignment against the same D6 and payload."""
    previous = task.get('developer')
    old_assignment = getattr(args, 'reassign_from', None)
    if (type(old_assignment) is not str or not re.fullmatch(r'[0-9a-f]{32}', old_assignment) or
            type(previous) is not dict or profile not in AGENTS):
        _refuse('DIRECT_REASSIGN_ASSIGNMENT_MISMATCH')
    _checked_scope(args, previous, read_paths, write_paths)
    completion = task.get('completion') or {}
    evidence = completion.get('evidence') or {}
    if (completion.get('executor_task_id') != args.executor_task or
            evidence.get('root') != registration.root or
            evidence.get('common_dir') != registration.common_dir or
            evidence.get('branch') != registration.branch or
            snapshot.head != previous.get('source_sha') or not snapshot.clean):
        _refuse('DIRECT_REASSIGN_D6_BINDING_MISMATCH')
    history = previous.get('direct_handoffs', [])
    if history and history[-1]['old_assignment'] == old_assignment:
        last = history[-1]
        assignment = previous['assignment_id']
        if (previous.get('state') != 'DEVELOPER_PENDING' or last['new_profile'] != profile or
                previous.get('provider_profile') != profile):
            _refuse('DIRECT_REASSIGN_ALREADY_CLAIMED')
        successor = replace(registration, trusted_agent_id=AGENTS[profile],
                            session_ref='handoff-' + assignment, native_session_id=None)
        if (registration != successor and
                (registration.trusted_agent_id != AGENTS[last['old_profile']] or
                 registration.session_ref != last['old_session_ref'])):
            _refuse('DIRECT_REASSIGN_D6_BINDING_MISMATCH')
        packet = _repair_packet(store, task, previous, registration)
        return Reassignment(task, registration, successor, snapshot, assignment, packet, True)

    source_profile = previous.get('provider_profile')
    if (old_assignment != previous.get('assignment_id') or source_profile not in AGENTS or
            source_profile == profile):
        _refuse('DIRECT_REASSIGN_ASSIGNMENT_MISMATCH')
    state = previous.get('state')
    if state == 'DEVELOPER_DISPATCHING':
        if previous.get('dispatch_boundary') != 'PREPARED':
            _refuse('DIRECT_REASSIGN_LAUNCH_UNVERIFIED')
    elif state != 'DEVELOPER_PENDING' and not (
            state == 'DEVELOPER_OUTCOME_UNKNOWN' and
            previous.get('dispatch_boundary') == 'STOPPED'):
        _refuse('DIRECT_REASSIGN_STATE_REFUSED')
    if ((previous.get('execution') is not None and
         previous.get('dispatch_boundary') != 'STOPPED') or
            previous.get('artifact') is not None or
            evidence.get('executor_identity') != AGENTS[source_profile] or
            registration.trusted_agent_id != AGENTS[source_profile] or
            evidence.get('executor_session_ref') != registration.session_ref or
            evidence.get('executor_native_session_id') != registration.native_session_id):
        _refuse('DIRECT_REASSIGN_D6_BINDING_MISMATCH')
    assignment = secrets.token_hex(16)
    successor = replace(registration, trusted_agent_id=AGENTS[profile],
                        session_ref='handoff-' + assignment, native_session_id=None)
    packet = _repair_packet(store, task, previous, registration)
    return Reassignment(task, registration, successor, snapshot, assignment, packet)


def _install_successor(store, plan, expected_record_sha):
    """CAS the D6 record while holding the lifecycle lock used by registry writers."""
    old, new = plan.original, plan.successor
    if old == new:
        return
    registry = Path(old.common_dir) / 'llmtool-worktrees'
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(registry, flags)
    tmp = None
    lock = None
    try:
        info = os.fstat(directory)
        if (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700 or
                (info.st_dev, info.st_ino) != (registry.stat().st_dev, registry.stat().st_ino)):
            _refuse('DIRECT_REASSIGN_D6_BINDING_MISMATCH')
        lock = store._lock_sidecar(directory, '.lifecycle.lock', exclusive=True,
                                   code='DIRECT_REASSIGN_D6_LOCK')
        if store.get(plan.current['task_id'])['record_sha256'] != expected_record_sha:
            _refuse('DIRECT_REASSIGN_PENDING_MISMATCH')
        if task_completion.bind_executor_task(old.task_id, expected_common_dir=old.common_dir,
                                              root=old.root) != (old, plan.snapshot):
            _refuse('DIRECT_REASSIGN_D6_BINDING_MISMATCH')
        name = old.task_id + '.json'
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or
                    info.st_uid != os.getuid() or info.st_nlink != 1 or
                    info.st_size > task_completion._REGISTRY_LIMIT):
                _refuse('DIRECT_REASSIGN_D6_BINDING_MISMATCH')
            raw = stream.read(task_completion._REGISTRY_LIMIT + 1)
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=task_completion._unique_pairs)
        if any(value.get(key) != expected for key, expected in (
                ('task_id', old.task_id), ('trusted_agent_id', old.trusted_agent_id),
                ('session_ref', old.session_ref), ('native_session_id', old.native_session_id),
                ('root_realpath', old.root), ('branch', old.branch), ('head_sha', plan.snapshot.head))):
            _refuse('DIRECT_REASSIGN_D6_BINDING_MISMATCH')
        value.update(trusted_agent_id=new.trusted_agent_id, session_ref=new.session_ref)
        value.pop('native_session_id', None)
        data = (json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False) + '\n').encode()
        if len(data) > task_completion._REGISTRY_LIMIT:
            _refuse('DIRECT_REASSIGN_D6_BINDING_MISMATCH')
        tmp = '.reassign-' + secrets.token_hex(16)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                     dir_fd=directory)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        path_info = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if (path_info.st_dev, path_info.st_ino, path_info.st_mtime_ns) != (
                info.st_dev, info.st_ino, info.st_mtime_ns):
            _refuse('DIRECT_REASSIGN_D6_BINDING_MISMATCH')
        if store.get(plan.current['task_id'])['record_sha256'] != expected_record_sha:
            _refuse('DIRECT_REASSIGN_PENDING_MISMATCH')
        os.replace(tmp, name, src_dir_fd=directory, dst_dir_fd=directory)
        tmp = None
        os.fsync(directory)
    finally:
        if tmp is not None:
            os.unlink(tmp, dir_fd=directory)
        if lock is not None:
            store._unlock_sidecar(lock)
        os.close(directory)


def claim(store, plan, developer, completion):
    task_id = plan.current['task_id']

    def verify():
        return (task_completion.bind_executor_task(
            plan.original.task_id, expected_common_dir=plan.original.common_dir,
            root=plan.original.root) == (plan.original, plan.snapshot) and
            _repair_packet(store, plan.current, plan.current['developer'],
                           plan.original) == plan.repair_packet)

    if plan.resumed:
        # The original invocation had completed the TaskStore write. Require
        # the same bounded payload when resuming its pending activation.
        saved = store.get(task_id)
        if (saved['record_sha256'] != plan.current['record_sha256'] or
                any(saved['developer'].get(key) != developer.get(key) for key in (
                    'provider_profile', 'context_sha256', 'source_sha', 'timeout_seconds',
                    'owner_assignment_ref')) or not verify()):
            _refuse('DIRECT_REASSIGN_PENDING_MISMATCH')
    else:
        saved = store.reassign_direct_developer(
            task_id, current=plan.current, developer=developer, completion=completion,
            verify_binding=verify)
    _install_successor(store, plan, saved['record_sha256'])
    if task_completion.bind_executor_task(
            plan.successor.task_id, expected_common_dir=plan.successor.common_dir,
            root=plan.successor.root) != (plan.successor, plan.snapshot):
        _refuse('DIRECT_REASSIGN_D6_BINDING_MISMATCH')
    return saved


def verified_repair_result(root, claim, task):
    """Check the current writer, prior claim and result before settling the claim."""
    from . import worker_dispatch

    developer = task.get('developer') or {}
    completion = task.get('completion') or {}
    evidence = completion.get('evidence') or {}
    execution = developer.get('execution') or {}
    registration = task_completion.read_d6_registration(
        completion.get('executor_task_id'), evidence.get('common_dir'),
        allowed_states=task_completion._D6_EVIDENCE_STATES)
    binding = worker_dispatch._assignment_audit_binding(task, developer)
    worker = worker_dispatch.worker_key_for(developer.get('provider_profile'))
    actions = {entry.get('action') for entry in task.get('dispatch_audit', [])
               if entry.get('worker_key') == worker and entry.get('decision') == 'ALLOWED' and
               worker_dispatch._matches_assignment_binding(entry, binding)}
    history = developer.get('direct_handoffs') or []
    root_completion = root.get('completion') or {}
    root_evidence = root_completion.get('evidence') or {}
    if (root_completion.get('executor_task_id') != registration.task_id or
            root_evidence.get('root') != registration.root or
            root_evidence.get('common_dir') != registration.common_dir or
            root_evidence.get('branch') != registration.branch or
            not history or history[-1]['new_assignment'] != developer.get('assignment_id') or
            not {'REASSIGN', 'DISPATCH'}.issubset(actions) or
            developer.get('state') != 'DEVELOPER_RESPONDED' or
            developer.get('dispatch_boundary') != 'STARTING' or
            registration.trusted_agent_id != AGENTS.get(developer.get('provider_profile')) or
            registration.root != evidence.get('root') or registration.branch != evidence.get('branch') or
            claim.get('head_sha') != developer.get('source_sha') or
            claim.get('executor_task') != registration.task_id or
            execution.get('status') not in ('DONE', 'SUCCEEDED') or
            execution.get('process_exit_code') != 0 or execution.get('cleanup_status') != 'CONFIRMED' or
            execution.get('native_session_id') in (None, 'UNKNOWN') or
            registration.native_session_id != execution.get('native_session_id') or
            evidence.get('executor_native_session_id') != registration.native_session_id):
        _refuse('DIRECT_REASSIGN_RESULT_UNVERIFIED')


def reconcile_claim(store, task_id, old_assignment):
    """Resume the original counted claim after a verified replacement result."""
    from . import lineage_counts

    task = store.get(task_id)
    developer = task.get('developer') or {}
    history = developer.get('direct_handoffs', [])
    if (not history or history[-1]['old_assignment'] != old_assignment or
            developer.get('state') != 'DEVELOPER_RESPONDED'):
        return
    completion = task.get('completion') or {}
    evidence = completion.get('evidence') or {}
    registration = task_completion.read_d6_registration(
        completion.get('executor_task_id'), evidence.get('common_dir'),
        allowed_states=task_completion._D6_EVIDENCE_STATES)
    packet = _repair_packet(store, task, developer, registration, require_closed=False)
    if packet is None:
        return
    root = store.get(packet['task'])
    release = lineage_counts.acquire_chain_lock(store, lineage_counts.chain_key(store, root))
    try:
        root = store.get(packet['task'])
        matches = [claim for claim in root.get('review_repair_claims', [])
                   if claim.get('repair_task_id') == task_id]
        if len(matches) != 1:
            _refuse('DIRECT_REASSIGN_REPAIR_CLAIM_UNVERIFIED')
        claim = matches[0]
        if claim.get('state') != 'DISPATCHED':
            store.reconcile_direct_repair_claim(root, claim, task, packet)
    finally:
        release()


def effective_writer_admission(store, root, initial, executor_task):
    """Project verified handoff identity onto the original artifact chain."""
    claims = root.get('review_repair_claims') or []
    if type(claims) is not list or any(type(claim) is not dict for claim in claims):
        return initial
    for claim in reversed(claims):
        if claim.get('executor_task') != executor_task or claim.get('state') != 'DISPATCHED':
            continue
        try:
            repair = store.get(claim['repair_task_id'])
        except (LLMToolError, KeyError):
            continue
        developer = repair.get('developer') or {}
        if (not developer.get('direct_handoffs') or
                developer.get('state') != 'DEVELOPER_RESPONDED'):
            continue
        verified_repair_result(root, claim, repair)
        evidence = repair['completion']['evidence']
        return dict(initial, **{name: evidence[name] for name in (
            'executor_identity', 'executor_session_ref', 'executor_native_session_id',
            'executor_native_session')})
    return initial


def authoring_route(store, root, developer, executor_task):
    """Keep later repairs on the writer admitted by an earlier handoff."""
    completion = root.get('completion') or {}
    initial = completion.get('evidence') or {}
    if not root.get('review_repair_claims') or not initial.get('common_dir'):
        return developer
    current = effective_writer_admission(store, root, initial, executor_task)
    role = current.get('executor_identity')
    if role == initial.get('executor_identity') and not str(
            current.get('executor_session_ref', '')).startswith('handoff-'):
        return developer
    profile = next((profile for profile, agent in AGENTS.items() if agent == role), None)
    if profile is None:
        _refuse('DIRECT_REASSIGN_D6_BINDING_MISMATCH')
    if profile == developer.get('provider_profile'):
        return developer
    return dict(developer, provider_profile=profile, owner_assignment_ref=None)
