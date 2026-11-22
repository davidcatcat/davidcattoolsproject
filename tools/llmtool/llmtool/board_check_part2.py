"""Evidence context and attempt/GitHub helpers for ``llmtool board-check`` (#763)."""

import os
import re
import sys
from datetime import datetime

from . import worker_dispatch


def _bc():
    name = f'{__package__}.board_check' if __package__ else 'board_check'
    mod = sys.modules.get(name)
    if mod is None:
        from . import board_check as mod
    return mod


class _Context:
    """Cached, read-only evidence for one board check."""

    def __init__(self, *, store, github_get, list_processes, read_d6, evaluate, now,
                 ledger_claims=None, ledger_unreadable=False):
        self.store = store
        self._github_get = github_get
        self._list_processes = list_processes
        self._read_d6 = read_d6
        self._evaluate = evaluate
        self.now = now
        self.ledger_claims = ledger_claims
        self.ledger_unreadable = ledger_unreadable
        self._ids = None
        self._records = {}
        self._processes = None
        self._assignments = None
        self._issue_markers = {}
        self.covered_prs = set()
        self.covered_tasks = set()

    def get(self, path):
        bc = _bc()
        try:
            return self._github_get(path)
        except Exception:
            raise bc._Unknown('GITHUB_READ_FAILED') from None

    def get_pages(self, path):
        """Read a bounded paginated GitHub list; truncation is UNKNOWN."""
        bc = _bc()
        items = []
        separator = '&' if '?' in path else '?'
        for page in range(1, bc.MAX_GITHUB_PAGES + 1):
            value = self.get(f'{path}{separator}per_page=100&page={page}')
            if type(value) is not list:
                raise bc._Unknown('GITHUB_READ_INVALID')
            items.extend(value)
            if len(value) < 100:
                return items
        raise bc._Unknown('GITHUB_LIST_TRUNCATED')

    def pull(self, number):
        bc = _bc()
        value = self.get(f'pulls/{number}')
        head = value.get('head') if type(value) is dict else None
        if (type(value) is not dict or type(value.get('merged')) is not bool or
                value.get('state') not in ('open', 'closed') or type(head) is not dict or
                type(head.get('sha')) is not str or bc._SHA.fullmatch(head['sha']) is None):
            raise bc._Unknown('PR_UNAVAILABLE')
        return value

    def evaluate(self, worker, owner_ref=None, task_id=None):
        bc = _bc()
        try:
            return self._evaluate(worker, owner_ref, task_id)
        except Exception:
            raise bc._Unknown('DISPATCH_POLICY_UNAVAILABLE') from None

    def task_ids(self):
        bc = _bc()
        if self._ids is None:
            try:
                ids = self.store.task_ids()
            except Exception:
                raise bc._Unknown('TASK_STORE_UNAVAILABLE') from None
            self._ids = frozenset(item for item in ids if type(item) is str)
        return self._ids

    def record(self, task_id):
        """Return the Task record, None when no such Task exists; failures are UNKNOWN."""
        bc = _bc()
        if task_id not in self._records:
            if task_id not in self.task_ids():
                self._records[task_id] = None
            else:
                try:
                    value = self.store.get(task_id)
                except Exception:
                    raise bc._Unknown('TASK_RECORD_UNREADABLE') from None
                if type(value) is not dict:
                    raise bc._Unknown('TASK_RECORD_UNREADABLE')
                self._records[task_id] = value
        return self._records[task_id]

    def d6_root(self, record, d6_task):
        bc = _bc()
        if type(d6_task) is not str or not d6_task:
            raise bc._Unknown('D6_BINDING_UNAVAILABLE')
        try:
            snapshot = self._read_d6(record, d6_task)
        except Exception:
            raise bc._Unknown('D6_BINDING_UNAVAILABLE') from None
        root = snapshot.get('root') if type(snapshot) is dict else None
        if type(root) is not str or not root:
            raise bc._Unknown('D6_BINDING_UNAVAILABLE')
        return root

    def d6_snapshot(self, record, d6_task):
        bc = _bc()
        try:
            snapshot = self._read_d6(record, d6_task)
        except Exception:
            raise bc._Unknown('D6_BINDING_UNAVAILABLE') from None
        if type(snapshot) is not dict:
            raise bc._Unknown('D6_BINDING_UNAVAILABLE')
        if snapshot.get('state') == 'D6_BINDING_UNAVAILABLE':
            raise bc._Unknown(snapshot.get('code') or 'D6_BINDING_UNAVAILABLE')
        return snapshot

    def _read_processes(self):
        bc = _bc()
        try:
            value = list(self._list_processes())
        except Exception:
            raise bc._Unknown('PROCESS_LIST_FAILED') from None
        for item in value:
            started = item.get('started_at') if type(item) is dict else None
            if (type(item) is not dict or type(item.get('pid')) is not int or
                    item['pid'] < 1 or
                    (item.get('cwd') is not None and type(item['cwd']) is not str) or
                    type(item.get('start_time')) is not str or not item['start_time'] or
                    not isinstance(started, datetime) or started.tzinfo is None):
                raise bc._Unknown('PROCESS_LIST_INVALID')
        return value

    def processes(self):
        """Read twice; only a process unchanged across both reads is stable."""
        if self._processes is None:
            before = {item['pid']: (item['start_time'], item.get('cwd'))
                      for item in self._read_processes()}
            self._processes = [
                dict(item, stable=before.get(item['pid']) ==
                     (item['start_time'], item.get('cwd')))
                for item in self._read_processes()]
        return self._processes

    def live_process(self, root, since, identity):
        """Return the pid proven to be this attempt, or None when it is proven gone.

        ``identity`` holds a trusted launch (``pid``, ``start``), a verified
        ``session``, or nothing. A process that may be the attempt but cannot be
        proven raises _Unknown; it is neither live nor dead.
        """
        bc = _bc()
        if since is None:
            return None
        target = os.path.realpath(root)
        doubt = None
        for item in self.processes():
            in_root = item.get('cwd') is not None and os.path.realpath(item['cwd']) == target
            matched = bc._identity_match(item, identity)
            # Once a launch PID is known, no other process can be the attempt.
            if matched is False and ('pid' in identity or not in_root):
                continue
            order = bc._start_order(item['started_at'], since)
            if order == 'before':
                continue
            if matched is None:
                doubt = 'PROCESS_START_UNCOMPARABLE'
            elif matched is False:
                doubt = 'PROCESS_IDENTITY_UNPROVEN'
            elif not item['stable']:
                doubt = 'PROCESS_IDENTITY_UNSTABLE'
            elif order == 'overlap':
                doubt = 'PROCESS_START_AMBIGUOUS'
            elif not in_root:
                doubt = 'PROCESS_CWD_UNPROVEN'
            else:
                return item['pid']
        if doubt is not None:
            raise bc._Unknown(doubt)
        return None

    def claimed_session(self, task_id, worker, d6_task):
        """Return the native session of the latest CLAIMED assignment for this attempt."""
        bc = _bc()
        if self._assignments is None:
            try:
                value = self.store.get_developer_assignments()
            except Exception:
                raise bc._Unknown('ASSIGNMENTS_UNAVAILABLE') from None
            if type(value) is not list:
                raise bc._Unknown('ASSIGNMENTS_UNAVAILABLE')
            self._assignments = value
        claims = [item['claim'] for item in self._assignments
                  if type(item) is dict and item.get('state') == 'CLAIMED' and
                  item.get('task_id') == task_id and item.get('worker') == worker and
                  item.get('d6_task_id') == d6_task and type(item.get('claim')) is dict]
        latest = max(claims, key=lambda claim: str(claim.get('claimed_at')), default=None)
        return latest.get('session_id') if latest is not None else None


def _executor_task(record):
    completion = record.get('completion')
    developer = record.get('developer')
    value = completion.get('executor_task_id') if type(completion) is dict else None
    if type(value) is not str and type(developer) is dict:
        value = developer.get('executor_task_id')
    return value if type(value) is str and value else None


def _dispatched_at(record):
    """Return the interval of the latest ALLOWED dispatch of the current assignment.

    Legacy entries carry no assignment_id. They stay readable as audit history,
    but nothing binds them to this attempt, so they are not launch evidence.
    A stored worker_key reads through worker_dispatch.stored_worker_id, so an
    entry written under a retired worker id alias still matches (#1502 WP-E).
    """
    bc = _bc()
    developer = record.get('developer')
    if type(developer) is not dict:
        return None
    worker = worker_dispatch.worker_key_for(developer.get('provider_profile'))
    assignment = developer.get('assignment_id')
    if type(assignment) is not str or not assignment:
        return None
    latest = None
    audit = record.get('dispatch_audit')
    for entry in audit if type(audit) is list else []:
        if (type(entry) is not dict or entry.get('decision') != 'ALLOWED' or
                worker_dispatch.stored_worker_id(entry.get('worker_key')) != worker or
                entry.get('action') not in bc._DISPATCH_ACTIONS or
                entry.get('assignment_id') != assignment):
            continue
        at = bc._interval(entry.get('at'))
        if at is not None and (latest is None or at[0] > latest[0]):
            latest = at
    return latest


def _attempt_identity(ctx, record, d6_task):
    """Return the trusted launch or verified native session of the current attempt."""
    developer = record.get('developer')
    developer = developer if type(developer) is dict else {}
    execution = developer.get('execution')
    if type(execution) is dict and execution.get('status') == 'RUNNING':
        return {'pid': execution.get('child_pid'), 'start': execution.get('child_start_time')}
    worker = worker_dispatch.worker_key_for(developer.get('provider_profile'))
    session = ctx.claimed_session(record.get('task_id'), worker, d6_task)
    return {'session': session} if session is not None else {}


def _attempt_process(ctx, record, d6_task, binding=None):
    """Return (pid or None, dispatch interval) for a Task's current attempt.

    ``binding`` is the record that holds the D6 binding when it is not ``record``.
    """
    bc = _bc()
    root = ctx.d6_root(binding or record, d6_task)
    since = bc._dispatched_at(record)
    return ctx.live_process(root, since, bc._attempt_identity(ctx, record, d6_task)), since


def _no_process(since):
    bc = _bc()
    if since is None:
        return 'no ALLOWED dispatch audit for the current assignment'
    return f'no live attempt process started after {bc._stamp(since[0])}'


def _claims(record):
    value = record.get('review_dispatch_claim')
    if type(value) is dict:
        return [value]
    return [item for item in value if type(item) is dict] if type(value) is list else []


def _cover_record(ctx, row, record):
    """Mark what this Task owns before any read that can fail for the row."""
    bc = _bc()
    ctx.covered_tasks.add(record.get('task_id'))
    number = bc._pr_number(row, record)
    if number is not None:
        ctx.covered_prs.add(number)
    repairs = record.get('review_repair_claims')
    for claim in repairs if type(repairs) is list else []:
        if type(claim) is dict and type(claim.get('repair_task_id')) is str:
            ctx.covered_tasks.add(claim['repair_task_id'])


def _pr_number(row, record):
    bc = _bc()
    if type(row.get('pr')) is int:
        return row['pr']
    published = record.get('published_pr')
    if type(published) is dict and type(published.get('pr')) is int:
        return published['pr']
    claims = bc._claims(record)
    number = claims[-1].get('pr') if claims else None
    return number if type(number) is int and number > 0 else None


def _pending_checks(ctx, head):
    """Return names of exact-head checks not yet completed."""
    bc = _bc()
    value = ctx.get(f'commits/{head}/check-runs?per_page=100')
    runs = value.get('check_runs') if type(value) is dict else None
    if (type(runs) is not list or type(value.get('total_count')) is not int or
            value['total_count'] != len(runs) or len(runs) > 100):
        raise bc._Unknown('CHECKS_UNAVAILABLE')
    pending = []
    for item in runs:
        if (type(item) is not dict or type(item.get('name')) is not str or
                type(item.get('status')) is not str):
            raise bc._Unknown('CHECKS_UNAVAILABLE')
        if item.get('head_sha') == head and item['status'] != 'completed':
            pending.append(re.sub(r'[^\x20-\x7e]', '?', item['name'])[:64])
    return sorted(set(pending))


def _issue_marker_status(ctx, number):
    """Return the latest waiting_owner marker status on an open issue, or None."""
    bc = _bc()
    if number not in ctx._issue_markers:
        issue = ctx.get(f'issues/{number}')
        if type(issue) is not dict or issue.get('state') not in ('open', 'closed'):
            raise bc._Unknown('OWNER_MARKER_UNAVAILABLE')
        status = None
        if issue['state'] == 'open':
            for comment in ctx.get_pages(f'issues/{number}/comments'):
                body = comment.get('body') if type(comment) is dict else None
                if type(body) is not str:
                    raise bc._Unknown('OWNER_MARKER_UNAVAILABLE')
                for match in bc._ACTIVITY.finditer(body):
                    fields = dict(token.split('=', 1) for token in match.group(1).split()
                                  if '=' in token)
                    if fields.get('type') == 'waiting_owner':
                        status = fields.get('status')
        ctx._issue_markers[number] = status
    return ctx._issue_markers[number]


def _owner_wait(ctx, row, record):
    """Return evidence for an open waiting_owner marker, or None."""
    bc = _bc()
    if record is not None:
        latest = None
        events = record.get('completion_events')
        for event in events if type(events) is list else []:
            if (type(event) is dict and event.get('source') == 'GITHUB' and
                    event.get('marker_type') == 'waiting_owner'):
                latest = event
        if latest is not None and latest.get('marker_status') == 'OPEN':
            return f"Task waiting_owner marker OPEN on #{latest.get('issue_number')}"
    ref = row.get('owner_ref')
    match = bc._OWNER_REF.fullmatch(ref) if type(ref) is str else None
    if match is None:
        return None
    number = int(match.group(1))
    if bc._issue_marker_status(ctx, number) == 'OPEN':
        return f'waiting_owner marker OPEN on open #{number}'
    return None
