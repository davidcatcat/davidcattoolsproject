"""Durable completion inbox for a live (record-pm) PM runtime (#352 G6).

When a Developer result reaches PM_EVIDENCE_READY on a Task recorded by an
active PM runtime, one completion event is persisted in that Task's existing
TaskStore event list. The PM reads unacknowledged events for its own session
with ``task inbox`` (optionally blocking) and acknowledges them with ``task ack``.
Nothing here dispatches a Worker, calls a provider or wakes a process: the PM
runtime polls, so events survive its absence or restart.

Delivery rules:
- An event ID is derived from the Task, Run, source and evidence digest, so
  recording the same evidence twice is inert and never creates a second event.
- Unacknowledged events (PENDING or PM_CONSUMED) are returned on every read;
  ACKED events are never returned again.
- A READY Task whose event is missing (for example after a crash between the
  state write and the event write) is reconciled on the next inbox read.
- Retention: a Task keeps at most 32 events. When it is full, the oldest ACKED
  event is compacted into a tombstone (ID and stream position kept, at most
  256), so it is still never recreated. If all 32 are unacknowledged, the new
  result is reported under ``blocked`` with COMPLETION_EVENT_LIMIT; acking the
  delivered events and reading the inbox again records it. Tombstones are
  never dropped: when 256 are held, the Task records no more events and the
  result is reported with COMPLETION_EVENT_HISTORY_LIMIT (start a new Task or
  Run).
"""

import contextlib
import hashlib
import json
import os
import stat
import time
from pathlib import Path

from .errors import LLMToolError
from .task_index import TaskStatusIndex
from .task_store import PM_NATIVE_SESSION_ID, SESSION_ID, TaskStoreConflict

DEFAULT_WAIT_SECONDS = 540
MAX_WAIT_SECONDS = 3600
POLL_SECONDS = 2.0
_EVENT_ID_DOMAIN = b'llmtool-active-pm-completion-event-v1\0'
_ACK_DOMAIN = b'llmtool-active-pm-ack-v1\0'
_REMOTE_TEST_EVENT_DOMAIN = b'llmtool-active-pm-remote-test-event-v1\0'
_REMOTE_TEST_OUTCOME_EVENT_DOMAIN = b'llmtool-active-pm-remote-test-outcome-event-v1\0'


def pm_session_key(value):
    """Map a supported PM native session UUID (or stored pm-* ID) to its Task binding."""
    if type(value) is str and PM_NATIVE_SESSION_ID.fullmatch(value):
        return 'pm-' + value
    if type(value) is str and SESSION_ID.fullmatch(value):
        return value
    raise LLMToolError('PM session must be a native session UUID or a pm- session ID')


def claude_session_file(native_session_id, cwd, environ=None):
    """Return where Claude Code keeps this session's transcript for ``cwd``."""
    env = os.environ if environ is None else environ
    base = env.get('CLAUDE_CONFIG_DIR')
    if not base and env.get('HOME'):
        from .role_config import resolve_role
        base = resolve_role('pm', environ=env, home=env['HOME'])['config_dir']
    if not base:
        base = os.path.join(env['HOME'], '.claude') if env.get('HOME') else None
    if base is None or not os.path.isabs(base):
        raise LLMToolError('Claude Code configuration directory is unavailable')
    encoded = ''.join(char if char.isascii() and (char.isalnum() or char == '-') else '-'
                      for char in cwd)
    return Path(base) / 'projects' / encoded / (native_session_id + '.jsonl')


def observe_claude_session(native_session_id, cwd, environ=None):
    """Fail closed unless this user's Claude Code transcript for the session exists.

    This proves the session exists locally and can be resumed with
    ``claude --resume`` in ``cwd``; it does not prove the caller is that session.
    """
    if type(native_session_id) is not str or not PM_NATIVE_SESSION_ID.fullmatch(
            native_session_id):
        raise LLMToolError('PM session ID must be a lowercase Claude Code session UUID')
    if type(cwd) is not str or not cwd:
        raise LLMToolError('PM working directory is required')
    cwd = os.path.normpath(os.path.abspath(cwd))
    if (len(cwd) > 1024 or any(ord(char) < 0x20 or ord(char) == 0x7f for char in cwd) or
            not os.path.isdir(cwd)):
        raise LLMToolError('PM working directory is invalid')
    _check_session_path(claude_session_file(native_session_id, cwd, environ),
                        allow_missing=False)
    return cwd


def claude_session_present(native_session_id, cwd, environ=None):
    """Whether this user's transcript for ``cwd`` exists; False only when it is genuinely missing.

    The components that do exist are checked exactly like
    ``observe_claude_session`` (this user's plain directories and file, no
    symlinks, without following any), so a first open may start the session
    with ``--session-id`` only when the transcript is absent from a real,
    unlinked location rather than hidden behind a symlink.
    """
    return _check_session_path(claude_session_file(native_session_id, cwd, environ),
                               allow_missing=True)


def observe_codex_session(native_session_id, cwd, environ=None):
    """Find a real Codex transcript whose session metadata binds ID to cwd."""
    if type(native_session_id) is not str or not PM_NATIVE_SESSION_ID.fullmatch(
            native_session_id):
        raise LLMToolError('PM session ID must be a lowercase Codex session UUID')
    if type(cwd) is not str or not cwd:
        raise LLMToolError('PM working directory is required')
    cwd = os.path.normpath(os.path.abspath(cwd))
    if (len(cwd) > 1024 or any(ord(char) < 0x20 or ord(char) == 0x7f for char in cwd) or
            not os.path.isdir(cwd)):
        raise LLMToolError('PM working directory is invalid')
    env = os.environ if environ is None else environ
    base = env.get('CODEX_HOME') or (os.path.join(env['HOME'], '.codex')
                                     if env.get('HOME') else None)
    if base is None or not os.path.isabs(base):
        raise LLMToolError('Codex configuration directory is unavailable')
    root = Path(base) / 'sessions'
    _check_directory_path(root)
    matches = []
    visited = 0
    for directory, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current = Path(directory)
        _check_plain_user_directory(current)
        for name in dirs:
            _check_plain_user_directory(current / name)
        for name in files:
            visited += 1
            if visited > 100000:
                raise LLMToolError('Codex session store exceeds the supported size')
            candidate = current / name
            if not name.endswith('-' + native_session_id + '.jsonl'):
                continue
            info = candidate.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise LLMToolError('Codex PM session path is not a plain user file')
            if info.st_size > 16 * 1024 * 1024:
                raise LLMToolError('Codex PM session file exceeds the supported size')
            try:
                with candidate.open('r', encoding='utf-8') as stream:
                    first_line = stream.readline(65537)
                if len(first_line.encode('utf-8')) > 65536:
                    raise ValueError('metadata line too large')
                metadata = json.loads(first_line)
                payload = metadata.get('payload')
                if (type(metadata) is not dict or metadata.get('type') != 'session_meta' or
                        type(payload) is not dict or payload.get('id') != native_session_id or
                        payload.get('cwd') != cwd):
                    raise ValueError('session metadata does not match')
            except Exception:
                raise LLMToolError(
                    'Codex PM session is malformed or bound to another directory') from None
            matches.append(candidate)
    if not matches:
        raise LLMToolError('Codex PM session was not found for this directory')
    if len(matches) > 1:
        raise LLMToolError('Codex PM session was found multiple times for this directory')
    return cwd


def _check_plain_user_directory(path):
    try:
        info = path.lstat()
    except OSError:
        raise LLMToolError('Codex PM session store is unavailable') from None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise LLMToolError('Codex PM session path is not a plain user directory')


def _check_directory_path(path):
    _check_plain_user_directory(path.parent)
    _check_plain_user_directory(path)


def _check_session_path(path, *, allow_missing):
    # Every component from the configuration directory down must be this
    # user's real directory or file; a symlink anywhere is refused.  Each
    # component is inspected without following symlinks, parents first, so a
    # symlinked or dangling parent is refused before its children are judged
    # missing.  With ``allow_missing`` a genuinely absent component ends the
    # walk as "transcript not present"; a dangling symlink is still a symlink.
    for component, regular in ((path.parents[2], False), (path.parents[1], False),
                               (path.parent, False), (path, True)):
        try:
            info = component.lstat()
        except FileNotFoundError:
            if allow_missing:
                return False
            raise LLMToolError(
                'Claude Code PM session was not found for this directory') from None
        except OSError:
            raise LLMToolError(
                'Claude Code PM session was not found for this directory') from None
        kind = stat.S_ISREG(info.st_mode) if regular else stat.S_ISDIR(info.st_mode)
        if not kind or info.st_uid != os.getuid():
            raise LLMToolError('Claude Code PM session path is not a plain user path')
    return True


def recovery(record):
    """How to reach the PM session again; None when no session was declared."""
    provenance = record.get('pm_provenance')
    if type(provenance) is not dict or 'pm_native_session_id' not in provenance:
        return None
    if provenance['pm_runtime'] == 'claude-code':
        argv = ['claude', '--resume', provenance['pm_native_session_id']]
    elif provenance['pm_runtime'] == 'codex':
        argv = ['codex', 'resume', provenance['pm_native_session_id']]
    else:
        return None
    return {'runtime': provenance['pm_runtime'], 'cwd': provenance['pm_cwd'],
            'resume_argv': argv}


def _handoff_event(record, handoff):
    developer = record['developer']
    callback = developer.get('direct_result_callback')
    if type(callback) is dict and callback.get('state') == 'PM_EVIDENCE_READY':
        source = 'DIRECT_D6_DEVELOPER'
    else:
        evidence = record['completion']['evidence']
        source = ('REVIEWER' if evidence['review_pr_ci'].get('stage') != 'EXECUTOR_RESULT'
                  else 'HOST_WORKER' if evidence.get('execution_mode') == 'HOST_WORKER'
                  else 'DIRECT_D6_DEVELOPER')
    content = json.dumps(handoff['packet'], ensure_ascii=True, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
    payload_sha256 = hashlib.sha256(content).hexdigest()
    event_id = hashlib.sha256(_EVENT_ID_DOMAIN + '\0'.join((
        record['task_id'], record['run_id'], source, payload_sha256)).encode()).hexdigest()[:32]
    return {'event_id': event_id, 'run_id': record['run_id'],
            'session_id': developer['session_id'], 'source': source,
            'source_sha': developer['source_sha'], 'payload_sha256': payload_sha256}


def record_ready_event(store, task_id):
    """Persist (idempotently) the completion event for a PM_EVIDENCE_READY Task."""
    from .task_submission_cli import _active_pm_handoff

    for _ in range(3):
        record = store.get(task_id)
        handoff = _active_pm_handoff(record)
        if handoff is None:
            return None
        binding = _handoff_event(record, handoff)
        known = (record.get('completion_events', []) +
                 record.get('completion_event_tombstones', []))
        existing = next((event for event in known
                         if event['event_id'] == binding['event_id']), None)
        if existing is not None:
            return existing
        stream = [event['sequence'] for event in known
                  if (event['source'], event['session_id']) ==
                  (binding['source'], binding['session_id'])]
        try:
            return store.record_completion_event(
                task_id, sequence=max(stream) + 1 if stream else 0, **binding)['event']
        except LLMToolError as exc:
            # A concurrent writer took this sequence; reread and retry.
            if getattr(exc, 'task_store_error_code', None) not in (
                    'COMPLETION_EVENT_CONFLICT', 'COMPLETION_EVENT_SEQUENCE_INVALID'):
                raise
    error = LLMToolError('completion event could not be recorded; retry later')
    error.task_store_error_code = 'COMPLETION_EVENT_BUSY'
    raise error


def record_remote_test_event(store, task_id, receipt):
    """Deliver an already validated EC2 remote test receipt to its PM inbox."""
    record = store.get(task_id)
    from .task_submission_cli import _active_pm_runtime

    if (record.get('pm_session_id') != receipt.get('pm_session') or
            receipt.get('task_id') != task_id or receipt.get('run_id') != record.get('run_id') or
            not _active_pm_runtime(record)):
        raise LLMToolError('remote test receipt does not match this Task')
    payload = json.dumps(receipt, ensure_ascii=True, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
    payload_sha256 = hashlib.sha256(payload).hexdigest()
    source_sha = receipt.get('source_sha')
    if type(source_sha) is not str or len(source_sha) != 40:
        raise LLMToolError('remote test receipt has no bound source SHA')
    session_id = 'remote-test-' + receipt['executor_task']
    event_id = hashlib.sha256(_REMOTE_TEST_EVENT_DOMAIN + '\0'.join((
        task_id, record['run_id'], source_sha,
        ','.join(receipt['modules']), receipt['receipt_digest'])).encode()).hexdigest()[:32]
    known = record.get('completion_events', []) + record.get('completion_event_tombstones', [])
    existing = next((event for event in known if event['event_id'] == event_id), None)
    if existing is not None:
        return existing
    source = 'GITHUB_REMOTE_TEST' if isinstance(receipt.get('actions'), dict) else 'EC2_REMOTE_TEST'
    stream = [event['sequence'] for event in known
              if (event['source'], event['session_id']) == (source, session_id)]
    return store.record_completion_event(
        task_id, event_id=event_id, run_id=record['run_id'], session_id=session_id,
        source=source, source_sha=source_sha,
        sequence=max(stream) + 1 if stream else 0,
        payload_sha256=payload_sha256)['event']


def record_remote_test_outcome_event(store, task_id, outcome, counter):
    """Deliver a persisted no-receipt outcome, with a stable per-write identity."""
    record = store.get(task_id)
    from .task_submission_cli import _active_pm_runtime

    if (outcome.get('task_id') not in (None, task_id) or
            outcome.get('run_id') not in (None, record.get('run_id')) or
            not _active_pm_runtime(record) or type(counter) is not int or counter < 0):
        raise LLMToolError('remote test outcome does not match this Task')
    payload = json.dumps(outcome, ensure_ascii=True, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
    payload_sha256 = hashlib.sha256(payload).hexdigest()
    # #1230 A3 B4: SHA-1 only names the event here; it is not a security use.
    source_sha = outcome.get('sha') or hashlib.sha1(payload, usedforsecurity=False).hexdigest()
    modules = outcome.get('modules', [])
    session_id = 'remote-test-' + (outcome.get('executor_task') or 'unknown')
    event_id = hashlib.sha256(_REMOTE_TEST_OUTCOME_EVENT_DOMAIN + b'\0' + b'\0'.join((
        task_id.encode(), record['run_id'].encode(), source_sha.encode(),
        ','.join(modules).encode(), outcome['result'].encode(), outcome['code'].encode(),
        str(counter).encode()))).hexdigest()[:32]
    known = record.get('completion_events', []) + record.get('completion_event_tombstones', [])
    existing = next((event for event in known if event['event_id'] == event_id), None)
    if existing is not None:
        return existing
    stream = [event['sequence'] for event in known
              if (event['source'], event['session_id']) == ('EC2_REMOTE_TEST', session_id)]
    return store.record_completion_event(
        task_id, event_id=event_id, run_id=record['run_id'], session_id=session_id,
        source='EC2_REMOTE_TEST', source_sha=source_sha,
        sequence=max(stream) + 1 if stream else 0,
        payload_sha256=payload_sha256)['event']


# What the PM should do when a READY result could not be turned into an event.
_BLOCKED_ACTIONS = {
    'COMPLETION_EVENT_LIMIT': 'ACK_DELIVERED_EVENTS_THEN_READ_INBOX',
    'COMPLETION_EVENT_HISTORY_LIMIT': 'START_NEW_TASK_OR_RUN',
}


def _session_records(store, session_key):
    from .task_submission_cli import _active_pm_runtime

    unreadable = 0
    records = []
    task_ids = store.task_ids()
    index = TaskStatusIndex(store)
    if not index.enabled:
        for task_id in task_ids:
            try:
                record = store.get(task_id)
            except LLMToolError:
                unreadable += 1
                continue
            if record.get('pm_session_id') == session_key and _active_pm_runtime(record):
                records.append(record)
        return records, unreadable

    # scan() refreshes uncached or changed files and retains unreadable records
    # in its count. Once warm, only Tasks indexed to this PM need a validated
    # read for the inbox's active-runtime check.
    candidates, unreadable = index.scan(task_ids)
    for task_id, _, indexed_session in candidates:
        if indexed_session != session_key:
            continue
        try:
            record, _ = index.reread(task_id)
        except LLMToolError:
            unreadable += 1
            continue
        if record.get('pm_session_id') == session_key and _active_pm_runtime(record):
            records.append(record)
    index.flush()
    return records, unreadable


def _inbox(store, session_key, *, reconcile):
    """Build an inbox view, optionally reconciling READY Tasks into events."""
    from .task_submission_cli import _active_pm_handoff

    records, unreadable = _session_records(store, session_key)
    events, blocked, pending = [], [], []
    for record in records:
        handoff = _active_pm_handoff(record)
        if handoff is not None:
            binding = _handoff_event(record, handoff)
            known = (record.get('completion_events', []) +
                     record.get('completion_event_tombstones', []))
            if not reconcile and not any(
                    event['event_id'] == binding['event_id'] for event in known):
                pending.append({
                    'task_id': record['task_id'], 'event_id': binding['event_id'],
                    'source': binding['source'], 'session_id': binding['session_id'],
                    'state': 'NOT_YET_RECORDED',
                })
        if reconcile and handoff is not None:
            try:
                record_ready_event(store, record['task_id'])
                record = store.get(record['task_id'])
            except LLMToolError as exc:
                # Deliver what is already durable and report why the newest
                # result has no event yet; the next read retries.
                code = getattr(exc, 'task_store_error_code', None) or \
                    'COMPLETION_EVENT_UNAVAILABLE'
                blocked.append({
                    'task_id': record['task_id'], 'code': code,
                    'unacknowledged_event_count': sum(
                        event['state'] != 'ACKED'
                        for event in record.get('completion_events', [])),
                    'action': _BLOCKED_ACTIONS.get(code, 'RETRY_INBOX'),
                })
        handoff = _active_pm_handoff(record)
        current = None if handoff is None else _handoff_event(record, handoff)['event_id']
        for event in record.get('completion_events', []):
            if event['state'] == 'ACKED':
                continue
            events.append({
                **event,
                'current': event['event_id'] == current,
                'active_pm_handoff': handoff if event['event_id'] == current else None,
                'pm_recovery': recovery(record),
            })
    events.sort(key=lambda event: (event['received_at'], event['task_id'], event['sequence']))
    unclaimed, claimed, assignments_code = _developer_assignments(store, session_key)
    return {'pm_session_id': session_key,
            'status': ('ATTENTION' if blocked or unreadable or unclaimed or assignments_code
                       else 'OK'),
            'events': events, 'blocked': blocked,
            'unreadable_task_count': unreadable,
            'unclaimed_assignments': unclaimed,
            'claimed_assignments': claimed,
            **({'assignments_error_code': assignments_code} if assignments_code else {}),
            **({'pending': pending} if not reconcile else {})}


def _developer_assignments(store, session_key):
    """#367: this PM's UNCLAIMED and CLAIMED Developer assignments (read-only view)."""
    from .developer_assignment_inbox import pm_assignments, refusal_code

    try:
        unclaimed, claimed = pm_assignments(store, session_key)
        return unclaimed, claimed, None
    except LLMToolError as exc:
        # A damaged assignment sidecar must not hide completion events.
        return [], [], refusal_code(exc) or 'ASSIGNMENTS_UNAVAILABLE'


def read_inbox(store, session_key):
    """Reconcile READY Tasks, then return every unacknowledged event of this PM."""
    return _inbox(store, session_key, reconcile=True)


def _refuse_adoption(code):
    error = LLMToolError('PM session adoption refused')
    error.task_store_error_code = code
    raise error


def _observe_bound_session(native_id, binding, root, environ, home):
    """Observe a bound session in its own account's directory; raise when it is not there."""
    from . import pm_session_liveness, role_config

    runtime, account, cwd = binding['runtime'], binding.get('account'), binding['cwd']
    if account is None and runtime == 'claude-code':
        observe_claude_session(native_id, cwd, environ)
        return
    if type(account) is not str:
        raise LLMToolError('PM binding has no account')
    resolved = role_config.resolve_role(
        'pm-codex-binding' if runtime == 'codex' else 'pm', environ=environ, home=home,
        cli={'account': account})
    config_dir = resolved.get('config_dir')
    if (resolved.get('account') != account or not config_dir or
            resolved.get('provider') not in (('codex',) if runtime == 'codex'
                                             else (None, 'claude'))):
        raise LLMToolError('PM binding account is not usable')
    if runtime == 'claude-code':
        observe_claude_session(native_id, cwd, {'CLAUDE_CONFIG_DIR': config_dir})
        return
    # A dedicated Codex session's transcript is named after its thread, not its binding.
    thread_id = pm_session_liveness._codex_thread_id(root, native_id, binding)
    if thread_id is None:
        raise LLMToolError('Codex PM binding has no thread')
    observe_codex_session(thread_id, cwd, {'CODEX_HOME': config_dir})


def _resolve_new_session(native_id, task_runtime, cwd, root, environ, home):
    """(runtime, account) of the replacement session, proven to exist on its own side."""
    from . import pm_runtime, pm_session_liveness

    invalid = 'PM_SESSION_ADOPTION_NEW_SESSION_INVALID'
    try:
        bound = pm_session_liveness._exists(pm_runtime._binding_path(root, native_id))
        binding = (pm_runtime.current_binding(native_id, state_root=root,
                                              resolve_legacy_account=False)
                   if bound else None)
    except (LLMToolError, OSError):
        _refuse_adoption(invalid)
    if bound and binding is None:
        _refuse_adoption(invalid)
    # Provenance keeps pm_cwd across a move, so another directory would break recovery.
    if bound and binding['cwd'] != cwd:
        _refuse_adoption('PM_SESSION_ADOPTION_CWD_MISMATCH')
    try:
        if bound:
            _observe_bound_session(native_id, binding, root, environ, home)
        elif task_runtime == 'codex':
            observe_codex_session(native_id, cwd, environ)
        else:
            observe_claude_session(native_id, cwd, environ)
    except (LLMToolError, OSError, ValueError):
        _refuse_adoption(invalid)
    return (binding['runtime'], binding.get('account')) if bound else (task_runtime, None)


def _adoption_candidates(store, old_session_key, old_native_id, cwd):
    """Every open Task of the old session and its runtime; refuse if any cannot move."""
    candidates, runtimes = [], set()
    for task_id in store.task_ids():
        record = store.get(task_id)
        if (record.get('pm_session_id') != old_session_key or
                record.get('abandonment') is not None):
            continue
        provenance = record.get('pm_provenance')
        if (type(provenance) is not dict or
                not {'pm_runtime', 'pm_native_session_id', 'pm_cwd',
                     'native_session_id_status'}.issubset(provenance) or
                provenance.get('native_session_id_status') !=
                'LOCAL_SESSION_FILE_OBSERVED' or
                provenance.get('pm_native_session_id') != old_native_id or
                provenance['pm_runtime'] not in ('claude-code', 'codex')):
            _refuse_adoption('PM_SESSION_ADOPTION_PROVENANCE_REQUIRED')
        # The caller-supplied directory is only a consistency check, and must be
        # each Task's exact canonical recorded value (normalizing here would
        # accept a trailing-slash alias).
        if type(cwd) is not str or cwd != provenance['pm_cwd']:
            _refuse_adoption('PM_SESSION_ADOPTION_CWD_MISMATCH')
        # Refused here, before the fence: a Task at its limit could never leave a
        # fenced session.
        if len(record.get('pm_session_adoptions', [])) >= 32:
            _refuse_adoption('PM_SESSION_ADOPTION_LIMIT')
        runtimes.add(provenance['pm_runtime'])
        candidates.append((task_id, record))
    if len(runtimes) > 1:
        _refuse_adoption('PM_SESSION_ADOPTION_PROVENANCE_REQUIRED')
    return candidates, next(iter(runtimes), None)


def _adopt_under_lock(store, old_session_key, new_session_key, old_native_id, cwd, *, root,
                      environ, home, clock, process_lister):
    from . import pm_runtime, pm_session_liveness

    new_native_id = new_session_key[3:]
    fence = pm_runtime.read_session_fence(old_native_id, root)
    # An unreadable fence has no target, so it never matches.
    if fence is not None and fence.get('new_session_key') != new_session_key:
        _refuse_adoption('PM_SESSION_ADOPTION_FENCED_OTHER_TARGET')
    if pm_runtime.read_session_fence(new_native_id, root) is not None:
        _refuse_adoption('PM_SESSION_ADOPTION_TARGET_FENCED')
    candidates, old_runtime = _adoption_candidates(store, old_session_key, old_native_id, cwd)
    if not candidates:
        return {'moved': [], 'skipped': []}
    new_runtime, new_account = _resolve_new_session(new_native_id, old_runtime, cwd, root,
                                                    environ, home)
    # The old side's binding, if any, must match this runtime and directory: a
    # mismatch reads as BINDING_INVALID, which is never STOPPED.
    liveness = pm_session_liveness.check_session(
        old_native_id, old_runtime, cwd, state_root=root, environ=environ, home=home,
        clock=clock, process_lister=process_lister, lock_held=True)
    if liveness['state'] == pm_session_liveness.ACTIVE:
        _refuse_adoption('PM_SESSION_ADOPTION_OLD_PM_ACTIVE')
    if liveness['state'] != pm_session_liveness.STOPPED:
        _refuse_adoption('PM_SESSION_ADOPTION_LIVENESS_UNKNOWN')
    at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    if fence is None:
        pm_runtime.write_session_fence(old_native_id, new_session_key, at, root)
    moved, skipped = [], []
    for task_id, record in candidates:
        try:
            store.adopt_pm_session(
                task_id, old_session_key=old_session_key,
                new_session_key=new_session_key, at=at,
                expected_record_sha256=record['record_sha256'],
                old_runtime=old_runtime, new_runtime=new_runtime,
                new_account=new_account, liveness=liveness['evidence'])
        except TaskStoreConflict:
            skipped.append(task_id)
        else:
            moved.append(task_id)
    return {'moved': moved, 'skipped': skipped}


def adopt_pm_session(store, old_session_key, new_session_key, *, old_native_id,
                     cwd, environ=None, state_root=None, clock=None, process_lister=None):
    """Move open Tasks to a replacement PM session once the old one is verified STOPPED.

    Under the old session's lock: validate every Task and both sides, check
    liveness, write the fence, then compare-and-swap each Task. The old transcript
    is never touched. A rerun with the same new session resumes an interrupted
    move. Per-Task conflicts are skipped and returned in ``skipped``; remaining
    eligible Tasks are still adopted. With no eligible Task nothing is written.
    """
    from . import pm_runtime

    if (type(old_native_id) is not str or
            PM_NATIVE_SESSION_ID.fullmatch(old_native_id) is None or
            type(old_session_key) is not str or
            SESSION_ID.fullmatch(old_session_key) is None or
            old_session_key != 'pm-' + old_native_id or
            type(new_session_key) is not str or
            SESSION_ID.fullmatch(new_session_key) is None or
            PM_NATIVE_SESSION_ID.fullmatch(new_session_key[3:]) is None):
        _refuse_adoption('PM_SESSION_ADOPTION_KEY_INVALID')
    if old_session_key == new_session_key:
        _refuse_adoption('PM_SESSION_ADOPTION_KEYS_EQUAL')
    env = os.environ if environ is None else environ
    home = env.get('HOME') or str(Path.home())
    with contextlib.ExitStack() as held:
        try:
            root = pm_runtime._session_state_root(
                state_root or Path(home) / '.local/state/llmtool/pm-runtime')
            held.enter_context(pm_runtime.transfer_lock(old_native_id, root))
        except LLMToolError as exc:
            _refuse_adoption('PM_SESSION_ADOPTION_OLD_PM_ACTIVE'
                             if getattr(exc, 'pm_runtime_code', None) == 'PM_SESSION_BUSY'
                             else 'PM_SESSION_ADOPTION_LIVENESS_UNKNOWN')
        return _adopt_under_lock(store, old_session_key, new_session_key, old_native_id, cwd,
                                 root=root, environ=environ, home=home, clock=clock,
                                 process_lister=process_lister)


def peek_inbox(store, session_key):
    """Return the PM inbox without writing; unrecorded READY results are pending."""
    return _inbox(store, session_key, reconcile=False)


def wait_inbox(store, session_key, *, wait=False, timeout=DEFAULT_WAIT_SECONDS,
               poll=POLL_SECONDS, clock=time.monotonic, sleep=time.sleep):
    if type(timeout) is not int or not 0 <= timeout <= MAX_WAIT_SECONDS:
        raise LLMToolError('inbox timeout must be 0 to %d seconds' % MAX_WAIT_SECONDS)
    deadline = clock() + timeout
    already_unclaimed = None
    while True:
        inbox = read_inbox(store, session_key)
        unclaimed = {item['assignment_id'] for item in inbox['unclaimed_assignments']}
        # Wake for assignments that become UNCLAIMED during this wait only, so a
        # long-unclaimed assignment does not turn --wait into a tight loop.
        newly_unclaimed = already_unclaimed is not None and bool(unclaimed - already_unclaimed)
        already_unclaimed = unclaimed if already_unclaimed is None else already_unclaimed
        if (inbox['events'] or inbox['blocked'] or newly_unclaimed or not wait or
                clock() >= deadline):
            inbox['timed_out'] = bool(wait and not inbox['events'] and not inbox['blocked']
                                      and not newly_unclaimed)
            return inbox
        sleep(max(0.0, min(poll, deadline - clock())))


def ack_decision_sha256(event_id):
    return hashlib.sha256(_ACK_DOMAIN + event_id.encode('ascii')).hexdigest()


def ack(store, event_id, session_key):
    """Mark one of this PM session's events received; repeating it is inert."""
    if type(event_id) is not str or len(event_id) != 32 or any(
            char not in '0123456789abcdef' for char in event_id):
        raise LLMToolError('event ID must be 32 lowercase hex characters')
    records, _ = _session_records(store, session_key)
    for record in records:
        tombstone = next((item for item in record.get('completion_event_tombstones', [])
                          if item['event_id'] == event_id), None)
        if tombstone is not None:
            # Compaction keeps only ACKED events: this ack already happened.
            return {'event': dict(tombstone, task_id=record['task_id'], state='ACKED',
                                  compacted=True),
                    'task_id': record['task_id'], 'acknowledged_now': False}
    owner = next((record for record in records
                  if any(event['event_id'] == event_id
                         for event in record.get('completion_events', []))), None)
    if owner is None:
        # Also covers an event that belongs to a different PM session.
        raise LLMToolError('completion event not found for this PM session')
    digest = ack_decision_sha256(event_id)
    event = next(event for event in owner['completion_events']
                 if event['event_id'] == event_id)
    if event['state'] == 'PENDING':
        store.consume_completion_event(owner['task_id'], event_id,
                                       pm_session_id=session_key,
                                       pm_decision_sha256=digest)
    result = store.ack_completion_event(owner['task_id'], event_id,
                                        pm_session_id=session_key,
                                        pm_decision_sha256=digest)
    return {'event': result['event'], 'task_id': owner['task_id'],
            'acknowledged_now': result['created']}
