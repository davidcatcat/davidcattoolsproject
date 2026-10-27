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

import hashlib
import json
import os
import stat
import time
from pathlib import Path

from .errors import LLMToolError
from .task_store import PM_NATIVE_SESSION_ID, SESSION_ID

DEFAULT_WAIT_SECONDS = 540
MAX_WAIT_SECONDS = 3600
POLL_SECONDS = 2.0
_EVENT_ID_DOMAIN = b'llmtool-active-pm-completion-event-v1\0'
_ACK_DOMAIN = b'llmtool-active-pm-ack-v1\0'
_REMOTE_TEST_EVENT_DOMAIN = b'llmtool-active-pm-remote-test-event-v1\0'
_REMOTE_TEST_OUTCOME_EVENT_DOMAIN = b'llmtool-active-pm-remote-test-outcome-event-v1\0'


def pm_session_key(value):
    """Map a Claude Code session UUID (or a stored pm-* ID) to the Task binding."""
    if type(value) is str and PM_NATIVE_SESSION_ID.fullmatch(value):
        return 'pm-' + value
    if type(value) is str and SESSION_ID.fullmatch(value):
        return value
    raise LLMToolError('PM session must be a Claude Code session UUID or a pm- session ID')


def claude_session_file(native_session_id, cwd, environ=None):
    """Return where Claude Code keeps this session's transcript for ``cwd``."""
    env = os.environ if environ is None else environ
    base = env.get('CLAUDE_CONFIG_DIR') or (
        os.path.join(env['HOME'], '.claude') if env.get('HOME') else None)
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
    path = claude_session_file(native_session_id, cwd, environ)
    # Every component from the configuration directory down must be this
    # user's real directory or file; a symlink anywhere is refused.
    for component, regular in ((path.parents[2], False), (path.parents[1], False),
                               (path.parent, False), (path, True)):
        try:
            info = component.lstat()
        except OSError:
            raise LLMToolError(
                'Claude Code PM session was not found for this directory') from None
        kind = stat.S_ISREG(info.st_mode) if regular else stat.S_ISDIR(info.st_mode)
        if not kind or info.st_uid != os.getuid():
            raise LLMToolError('Claude Code PM session path is not a plain user path')
    return cwd


def recovery(record):
    """How to reach the PM session again; None when no session was declared."""
    provenance = record.get('pm_provenance')
    if type(provenance) is not dict or 'pm_native_session_id' not in provenance:
        return None
    return {'runtime': provenance['pm_runtime'], 'cwd': provenance['pm_cwd'],
            'resume_argv': ['claude', '--resume', provenance['pm_native_session_id']]}


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
    stream = [event['sequence'] for event in known
              if (event['source'], event['session_id']) == ('EC2_REMOTE_TEST', session_id)]
    return store.record_completion_event(
        task_id, event_id=event_id, run_id=record['run_id'], session_id=session_id,
        source='EC2_REMOTE_TEST', source_sha=source_sha,
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
    source_sha = outcome.get('sha') or hashlib.sha1(payload).hexdigest()
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
    for task_id in store.task_ids():
        try:
            record = store.get(task_id)
        except LLMToolError:
            unreadable += 1
            continue
        if record.get('pm_session_id') == session_key and _active_pm_runtime(record):
            records.append(record)
    return records, unreadable


def read_inbox(store, session_key):
    """Reconcile READY Tasks, then return every unacknowledged event of this PM."""
    from .task_submission_cli import _active_pm_handoff

    records, unreadable = _session_records(store, session_key)
    events, blocked = [], []
    for record in records:
        if _active_pm_handoff(record) is not None:
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
    return {'pm_session_id': session_key,
            'status': 'ATTENTION' if blocked or unreadable else 'OK',
            'events': events, 'blocked': blocked,
            'unreadable_task_count': unreadable}


def wait_inbox(store, session_key, *, wait=False, timeout=DEFAULT_WAIT_SECONDS,
               poll=POLL_SECONDS, clock=time.monotonic, sleep=time.sleep):
    if type(timeout) is not int or not 0 <= timeout <= MAX_WAIT_SECONDS:
        raise LLMToolError('inbox timeout must be 0 to %d seconds' % MAX_WAIT_SECONDS)
    deadline = clock() + timeout
    while True:
        inbox = read_inbox(store, session_key)
        if inbox['events'] or inbox['blocked'] or not wait or clock() >= deadline:
            inbox['timed_out'] = bool(wait and not inbox['events'] and not inbox['blocked'])
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
