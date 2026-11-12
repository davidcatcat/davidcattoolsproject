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


FIELDS = ('root_id', 'kind', 'source_url', 'declared_stratum', 'tasks', 'd6s', 'd6_records',
          'first_dispatch', 'prs', 'last_merged_at', 'total_tokens', 'error_codes')


def _ids(values):
    return sorted({value for value in values if isinstance(value, (str, int))}, key=str)


def root_row(root_id, ledger_row, tasks, github, d6_dir=None):
    row = ledger_row or {}
    d6s = _ids((task.get('completion') or {}).get('executor_task_id') for task in tasks)
    firsts = [time for time in map(first_gw_dispatch, tasks) if time]
    prs = _ids((task.get('published_pr') or {}).get('pr') for task in tasks)
    merged = [(github.get(str(pr)) or {}).get('merged_at') for pr in prs]
    tokens = [((task.get('developer') or {}).get('usage') or {}).get('total_tokens') for task in tasks]
    errors = _ids((task.get('developer') or {}).get('error_code') for task in tasks)
    return {
        'root_id': root_id,
        'kind': (row.get('kind', 'ROOT') if ledger_row else
                 'UNTAGGED' if [task.get('task_id') for task in tasks] == [root_id] else 'UNDECLARED'),
        'source_url': row.get('source_url', UNKNOWN),
        'declared_stratum': row.get('declared_stratum', UNKNOWN),
        'tasks': len(tasks),
        'd6s': len(d6s),
        'd6_records': (sum((Path(d6_dir) / (str(d6) + '.json')).is_file() for d6 in d6s)
                       if d6_dir else UNKNOWN),
        'first_dispatch': min(firsts) if firsts else UNKNOWN,
        'prs': ' '.join(str(pr) for pr in prs) or UNKNOWN,
        'last_merged_at': (max(merged) if merged and all(isinstance(m, str) for m in merged)
                           else UNKNOWN),
        'total_tokens': (sum(tokens) if tokens and all(type(n) is int for n in tokens)
                         else UNKNOWN),
        'error_codes': ' '.join(str(code) for code in errors),
    }


def summary(rows, t0=T0):
    kinds = {}
    for row in rows:
        kinds[row['kind']] = kinds.get(row['kind'], 0) + 1
    return {'t0': t0, 'roots': len(rows), 'by_kind': kinds,
            'tokens_known': sum(row['total_tokens'] != UNKNOWN for row in rows),
            'merge_known': sum(row['last_merged_at'] != UNKNOWN for row in rows)}


def main(argv=None):
    state = Path.home() / '.local' / 'state' / 'llmtool'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', default=str(state / 'gw-validation' / 'roots.jsonl'))
    parser.add_argument('--tasks', default=str(state / 'tasks'))
    parser.add_argument('--d6-dir', help='.git/llmtool-worktrees of the canonical repo')
    parser.add_argument('--github-cache', help='JSON {"<pr>": {"merged_at": ...}}, fetched elsewhere')
    parser.add_argument('--t0', default=T0)
    parser.add_argument('--out-dir', required=True)
    args = parser.parse_args(argv)
    github = {}
    if args.github_cache:
        try:
            github = json.loads(Path(args.github_cache).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            github = {}
    github = github if isinstance(github, dict) else {}
    roots = group_roots(load_jsonl(args.ledger), load_tasks(args.tasks), args.t0)
    rows = [root_row(root_id, row, tasks, github, args.d6_dir)
            for root_id, (row, tasks) in sorted(roots.items())]
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / 'roots.csv', 'w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    (out / 'summary.json').write_text(
        json.dumps(summary(rows, args.t0), indent=1, sort_keys=True) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
