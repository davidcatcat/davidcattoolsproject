"""Owner-readable, read-only projection of durable Task status summaries."""

import json


_STATES = ('RUNNING', 'BLOCKED', 'UNAVAILABLE', 'FAILED', 'COMPLETED',
           'ABANDONED', 'UNKNOWN')
_ATTENTION = ('BLOCKED', 'FAILED', 'UNAVAILABLE', 'UNKNOWN')
_PRIORITY = {state: rank for rank, state in enumerate(
    ('BLOCKED', 'FAILED', 'UNAVAILABLE', 'UNKNOWN', 'RUNNING', 'COMPLETED', 'ABANDONED'))}


def task_brief(status, *, limit=20):
    """Project task_status output without retaining or exposing source records."""
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError('limit must be between 1 and 200')
    source = status if type(status) is dict else {}
    source_tasks = source.get('tasks')
    source_tasks = source_tasks if type(source_tasks) is list else []
    unreadable = source.get('unreadable')
    unreadable = unreadable if type(unreadable) is int and unreadable >= 0 else 0
    rows = []
    counts = {name: 0 for name in (*_STATES, 'unreadable')}
    for item in source_tasks:
        item = item if type(item) is dict else {}
        raw_state = item.get('state')
        state = raw_state if type(raw_state) is str and raw_state in _STATES else 'UNKNOWN'
        counts[state] += 1
        task_id = item.get('task_id')
        event = item.get('last_event')
        event = event if type(event) is dict else {}
        event_state = event.get('state')
        claim_state = item.get('review_claim_state')
        pending = item.get('pending_inbox_count')
        rows.append({
            'task_id': task_id if type(task_id) is str else 'UNKNOWN',
            'state': state,
            'state_label': 'derived from persisted Task status',
            'last_event': {
                'state': event_state if type(event_state) is str else 'UNKNOWN',
            },
            'review_claim_state': claim_state if type(claim_state) is str else 'UNKNOWN',
            'review_claim_label': 'stored claim state; not a formal verdict',
            'pending_inbox_count': (pending if type(pending) is int and pending >= 0
                                    else 'UNKNOWN'),
        })
    counts['unreadable'] = unreadable
    rows.sort(key=lambda row: (_PRIORITY[row['state']], row['task_id']))
    partial = len(source_tasks) > limit or len(source_tasks) >= 200
    selected = rows[:limit]
    return {
        'schema_version': 1,
        'provenance': {'source': 'task_status', 'state_values': 'derived'},
        'counts': counts,
        'partial': partial,
        'view': {
            'scanned': len(source_tasks),
            'selected': len(selected),
            'truncated': max(0, len(source_tasks) - len(selected)),
        },
        'tasks': selected,
    }


def format_task_brief(result, *, as_json=False):
    if as_json:
        return json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    if not result['tasks'] and result['counts']['unreadable'] == 0:
        return '沒有可顯示的任務'
    counts = result['counts']
    view = result['view']
    lines = [
        ('任務摘要：注意 {attention}，執行中 {running}，終態 {terminal}，'
         '無法讀取 {unreadable}').format(
            attention=sum(counts[state] for state in _ATTENTION),
            running=counts['RUNNING'],
            terminal=counts['COMPLETED'] + counts['ABANDONED'],
            **counts),
    ]
    if result['partial']:
        lines.append(f"目前為部分檢視（掃描 {view['scanned']}，選取 {view['selected']}，"
                     f"截斷 {view['truncated']}；最多掃描 200 筆）。")
    for item in result['tasks']:
        lines.append(
            f"{item['task_id']} {item['state']}（{item['state_label']}） "
            f"事件={item['last_event']['state']} 審查聲明={item['review_claim_state']} "
            f"待處理收件匣={item['pending_inbox_count']}；"
            f"{item['review_claim_label']}。")
    return '\n'.join(lines)
