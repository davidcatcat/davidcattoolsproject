"""Read-only, conservative status summaries for durable Tasks."""

import json
import re

from .task_store import PM_NATIVE_SESSION_ID, STATES, TaskStore
from .task_index import TaskStatusIndex


# Persisted states have meaning only where explicitly listed here. In particular,
# review claims and test receipts are evidence, not a review or test PASS.
_TASK_STATES = {
    **dict.fromkeys(('PM_PENDING', 'PM_RESPONDED'), 'RUNNING'),
    'PM_UNAVAILABLE': 'UNAVAILABLE',
    **dict.fromkeys(('INPUT_REJECTED', 'PM_RESPONSE_REJECTED'), 'FAILED'),
}
assert set(_TASK_STATES) == set(STATES)
_CALLBACK_STATES = {
    **dict.fromkeys(('WAITING_EXECUTOR', 'VERIFYING_EXECUTOR', 'WAITING_PM',
                     'PM_EVIDENCE_READY', 'PM_DISPATCHING'), 'RUNNING'),
    'PM_RETURNED': 'COMPLETED',
    'PM_RESPONSE_REJECTED': 'FAILED',
    'PM_UNAVAILABLE': 'UNAVAILABLE',
    'PM_OUTCOME_UNKNOWN': 'UNKNOWN',
}
_COMPLETION_STATES = {
    **dict.fromkeys(('WAITING_EXECUTOR', 'WAITING_REVIEW', 'WAITING_CI',
                     'WAITING_PM', 'PM_EVIDENCE_READY', 'PM_DISPATCHING'), 'RUNNING'),
    'PM_RETURNED': 'COMPLETED',
    'PM_RESPONSE_REJECTED': 'FAILED',
    'PM_UNAVAILABLE': 'UNAVAILABLE',
    'PM_OUTCOME_UNKNOWN': 'UNKNOWN',
}
_DEVELOPER_STATES = {
    **dict.fromkeys(('DEVELOPER_PENDING', 'DEVELOPER_DISPATCHING',
                     'DEVELOPER_RESPONDED'), 'RUNNING'),
    'DEVELOPER_UNAVAILABLE': 'UNAVAILABLE',
    'DEVELOPER_OUTCOME_UNKNOWN': 'UNKNOWN',
    'DEVELOPER_OUTPUT_REJECTED': 'FAILED',
}


def derive_state(record):
    """Derive only states supported by recognized persisted values."""
    if type(record) is not dict:
        return 'UNKNOWN'
    if 'abandonment' in record:
        return 'ABANDONED'
    events = record.get('completion_events')
    events = events if type(events) is list else []
    if any(type(event) is dict and event.get('source') == 'GITHUB' and
           event.get('marker_type') == 'merge' and event.get('marker_status') == 'MERGED'
           for event in events):
        return 'COMPLETED'
    developer = record.get('developer')
    if type(developer) is dict:
        callback = developer.get('direct_result_callback')
        if type(callback) is dict:
            state = _CALLBACK_STATES.get(callback.get('state'))
            gates = callback.get('missing_gates')
            if state is None:
                return 'UNKNOWN'
            if state == 'RUNNING' and type(gates) is list and gates:
                return 'BLOCKED'
            return state
        state = developer.get('state')
        mapped = _DEVELOPER_STATES.get(state)
        if mapped is not None:
            if state == 'DEVELOPER_OUTCOME_UNKNOWN' and developer.get('error_code') == 'CODEX_BLOCKED':
                return 'BLOCKED'
            if state != 'DEVELOPER_RESPONDED':
                return mapped
        if state is not None:
            if state != 'DEVELOPER_RESPONDED':
                return 'UNKNOWN'
    completion = record.get('completion')
    if type(completion) is dict:
        mapped = _COMPLETION_STATES.get(completion.get('state'))
        return mapped if mapped is not None else 'UNKNOWN'
    mapped = _TASK_STATES.get(record.get('state'))
    if mapped is None:
        return 'UNKNOWN'
    return mapped


def _summary(record):
    developer = record.get('developer') if type(record.get('developer')) is dict else {}
    callback = developer.get('direct_result_callback')
    callback = callback if type(callback) is dict else {}
    events = record.get('completion_events')
    events = events if type(events) is list else []
    latest = events[-1] if events and type(events[-1]) is dict else None
    claim = record.get('review_dispatch_claim')
    if type(claim) is list:
        claim = claim[-1] if claim and type(claim[-1]) is dict else None
        claim_state = claim.get('state') if claim is not None else 'UNKNOWN'
    elif type(claim) is dict:
        claim_state = claim.get('state', 'UNKNOWN')
    else:
        claim = None
        claim_state = 'NONE'
    completion = record.get('completion')
    completion = completion if type(completion) is dict else {}
    if claim_state == 'DISPATCHED' and type(claim.get('head_sha')) is str:
        claim_head = claim['head_sha']
        reviewer_event = next((event for event in reversed(events)
                               if type(event) is dict and
                               event.get('source') == 'REVIEWER' and
                               event.get('source_sha') == claim_head), None)
        if reviewer_event is not None:
            # Completion evidence is authoritative for a PASS and must bind to
            # the dispatched claim's head. Stored records may keep it directly,
            # under ``evidence``, or under ``review_pr_ci``.
            evidence_candidates = [record.get('review_pr_ci'), completion,
                                   completion.get('evidence'),
                                   completion.get('review_pr_ci')]
            evidence_candidates.extend(
                evidence.get('evidence') for evidence in tuple(evidence_candidates)
                if type(evidence) is dict and type(evidence.get('evidence')) is dict)
            review_evidence = next((evidence for evidence in evidence_candidates
                                    if type(evidence) is dict and
                                    evidence.get('head_sha') == claim_head and
                                    evidence.get('formal_review_state') == 'ACCEPTED'), None)
            if review_evidence is not None:
                claim_state = 'PASS'
            elif (type(claim.get('version')) is int and
                  reviewer_event.get('version') == claim['version']):
                # Preserve legacy event-derived states when the event carries
                # the old version/outcome fields. A PASS still requires the
                # accepted completion evidence checked above.
                outcome = reviewer_event.get('outcome') or reviewer_event.get('state')
                if type(outcome) is str and outcome not in ('PASS', 'REVIEW_PASS'):
                    claim_state = outcome
    return {
        'task_id': record['task_id'],
        'pm_session_id': record.get('pm_session_id'),
        'state': derive_state(record),
        'executor_d6': callback.get('executor_task_id') or completion.get('executor_task_id'),
        'last_event': None if latest is None else {
            'state': latest.get('state') if type(latest.get('state')) is str else 'UNKNOWN',
            'received_at': latest.get('received_at') if type(latest.get('received_at')) is str else None,
        },
        'review_claim_state': claim_state if type(claim_state) is str else 'UNKNOWN',
        'pending_inbox_count': sum(1 for event in events if type(event) is dict and
                                   event.get('acked_at') is None),
    }


def task_status(*, pm_session=None, limit=20, store=None):
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError('limit must be between 1 and 200')
    if pm_session is not None:
        if type(pm_session) is not str:
            raise ValueError('invalid PM session')
        if PM_NATIVE_SESSION_ID.fullmatch(pm_session) is not None:
            pm_session = 'pm-' + pm_session
        elif re.fullmatch(r'pm-[a-z0-9-]{1,60}', pm_session) is None:
            raise ValueError('invalid PM session')
    store = TaskStore() if store is None else store
    index = TaskStatusIndex(store)
    task_ids = store.task_ids()

    def full_scan():
        records = []
        unreadable_count = 0
        refreshed = TaskStatusIndex(store)
        for task_id in task_ids:
            record, readable = refreshed.remember_full_scan(task_id)
            if not readable:
                unreadable_count += 1
                continue
            if pm_session is None or record.get('pm_session_id') == pm_session:
                records.append(record)
        if refreshed.changed:
            refreshed._save()
        records.sort(key=lambda item: item.get('created_at', ''), reverse=True)
        return {'tasks': [_summary(item) for item in records[:limit]],
                'unreadable': unreadable_count}

    candidates, unreadable = index.scan(task_ids)
    candidates = [item for item in candidates
                  if pm_session is None or item[2] == pm_session]
    candidates.sort(key=lambda item: item[1], reverse=True)
    records = []
    for task_id, _, _ in candidates:
        if len(records) >= limit:
            break
        try:
            record, metadata_changed = index.reread(task_id)
        except Exception:
            unreadable += 1
            continue
        if metadata_changed:
            index.discard()
            return full_scan()
        if pm_session is None or record.get('pm_session_id') == pm_session:
            records.append(record)
        elif len(records) < limit:
            # The PM-session filter changed after the index scan; this is a
            # stale-index condition just like a changed ordering timestamp.
            index.discard()
            return full_scan()
    index.flush()
    return {'tasks': [_summary(item) for item in records],
            'unreadable': unreadable}


def format_status(result, *, as_json=False):
    if as_json:
        return json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    lines = [f"Tasks: {len(result['tasks'])} (unreadable: {result['unreadable']})"]
    for item in result['tasks']:
        event = item['last_event']
        event_text = 'none' if event is None else event['state']
        lines.append(f"{item['task_id']} {item['state']} executor={item['executor_d6'] or 'unknown'} "
                     f"event={event_text} review={item['review_claim_state']} "
                     f"pending={item['pending_inbox_count']}")
    return '\n'.join(lines)
