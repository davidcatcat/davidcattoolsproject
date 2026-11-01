"""Owner-readable, read-only projection of durable Task status summaries."""

import json


_GROUPS = {
    'attention': ('BLOCKED', 'FAILED', 'UNAVAILABLE', 'UNKNOWN'),
    'running': ('RUNNING',),
    'terminal': ('COMPLETED', 'ABANDONED'),
}
_ORDER = {state: group for group, states in _GROUPS.items() for state in states}
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
    counts = {name: 0 for name in (*_GROUPS, 'unreadable')}
    for item in source_tasks:
        item = item if type(item) is dict else {}
        raw_state = item.get('state')
        state = raw_state if type(raw_state) is str and raw_state in _ORDER else 'UNKNOWN'
        counts[_ORDER.get(state, 'attention')] += 1
        task_id = item.get('task_id')
        rows.append({
            'task_id': task_id if type(task_id) is str else 'UNKNOWN',
            'state': state,
            'state_label': 'derived from persisted Task status',
        })
    counts['unreadable'] = unreadable
    rows.sort(key=lambda row: (_PRIORITY[row['state']], row['task_id']))
    partial = len(source_tasks) > limit or len(source_tasks) >= 200
    return {
        'schema_version': 1,
        'provenance': {'source': 'task_status', 'state_values': 'derived'},
        'counts': counts,
        'partial': partial,
        'tasks': rows[:limit],
    }


def format_task_brief(result, *, as_json=False):
    if as_json:
        return json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    if not result['tasks'] and result['counts']['unreadable'] == 0:
        return '沒有可顯示的任務'
    counts = result['counts']
    lines = [
        '任務摘要：注意 {attention}，執行中 {running}，終態 {terminal}，無法讀取 {unreadable}'.format(
            **counts),
    ]
    if result['partial']:
        lines.append('目前為部分檢視。')
    for item in result['tasks']:
        lines.append(f"{item['task_id']} {item['state']}（{item['state_label']}）")
    return '\n'.join(lines)
