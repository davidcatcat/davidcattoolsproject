"""Read-only, conservative status summaries for durable Tasks."""

import json
import re

from .task_store import STATES, TaskStore


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
        claim_state = claim[-1].get('state') if claim and type(claim[-1]) is dict else 'UNKNOWN'
    elif type(claim) is dict:
        claim_state = claim.get('state', 'UNKNOWN')
    else:
        claim_state = 'NONE'
    completion = record.get('completion')
    completion = completion if type(completion) is dict else {}
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
    if pm_session is not None and (type(pm_session) is not str or
                                   re.fullmatch(r'pm-[a-z0-9-]{1,60}', pm_session) is None):
        raise ValueError('invalid PM session')
    store = TaskStore() if store is None else store
    records = []
    unreadable = 0
    # Read all IDs so unreadable newest records cannot hide older readable Tasks.
    for task_id in store.task_ids():
        try:
            record = store.get(task_id)
        except Exception:
            unreadable += 1
            continue
        if pm_session is None or record.get('pm_session_id') == pm_session:
            records.append(record)
    records.sort(key=lambda item: item.get('created_at', ''), reverse=True)
    return {'tasks': [_summary(item) for item in records[:limit]],
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
