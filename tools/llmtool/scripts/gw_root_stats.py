#!/usr/bin/env python3
"""Read-only #1517 ROOT statistics from the ledger, TaskStore, D6 and cached GitHub.

It writes roots.csv and summary.json, with UNKNOWN for anything missing. It
changes no dispatch, review, CI or merge gate (#1517 lock, section 14).
"""
import argparse
import csv
import json
from pathlib import Path

T0 = '2026-11-11T16:03:28Z'
UNKNOWN = 'UNKNOWN'
GW_WORKER = 'company-gw-agent'


def load_jsonl(path):
    try:
        lines = Path(path).read_text(encoding='utf-8').splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def load_tasks(task_dir):
    tasks = []
    for path in sorted(Path(task_dir).glob('*.json')):
        try:
            task = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(task, dict) and isinstance(task.get('task_id'), str):
            tasks.append(task)
    return tasks


def first_gw_dispatch(task):
    times = [entry.get('at') for entry in task.get('dispatch_audit') or []
             if isinstance(entry, dict) and entry.get('action') == 'DISPATCH'
             and entry.get('decision') == 'ALLOWED' and entry.get('worker_key') == GW_WORKER
             and isinstance(entry.get('at'), str)]
    return min(times) if times else None


def root_tag(task):
    request = task.get('request')
    if not isinstance(request, str) or not request.startswith('ROOT='):
        return None
    words = request[len('ROOT='):].split()
    tag = words[0].rstrip('.,;:') if words else ''
    return tag or None


def group_roots(ledger, tasks, t0=T0):
    """Map root_id to (ledger row or None, Tasks); an untagged post-T0 gw-agent Task is its own ROOT."""
    roots = {row['root_id']: (row, []) for row in ledger if isinstance(row.get('root_id'), str)}
    for task in tasks:
        tag = root_tag(task)
        if tag is not None:
            roots.setdefault(tag, (None, []))[1].append(task)
            continue
        first = first_gw_dispatch(task)
        if first is not None and first >= t0:
            roots[task['task_id']] = (None, [task])
    return roots
