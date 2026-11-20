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
