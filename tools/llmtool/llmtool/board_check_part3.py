"""Gate evaluation and row classification for ``llmtool board-check`` (#763)."""

import re
import sys
from datetime import datetime, timezone

from . import task_advance, task_status, worker_dispatch


def _bc():
    name = f'{__package__}.board_check' if __package__ else 'board_check'
    mod = sys.modules.get(name)
    if mod is None:
        from . import board_check as mod
    return mod


def _capacity(ctx):
    """HOLD when every D6 Developer route is busy or not authorized.

    One proven free route clears capacity. Without one, an uncertain route keeps
    the answer UNKNOWN, because it may be free.
    """
    bc = _bc()
    free, waiting, doubts = [], [], []
    for worker in bc.DEVELOPER_ROUTES:
        decision = ctx.evaluate(worker)
        reason = getattr(decision, 'reason_code', None)
        if getattr(decision, 'allowed', None) is not True:
            if reason in bc._AUTHORIZATION_WAIT_CODES:
                waiting.append(f'{worker}={reason}')
                continue
            raise bc._Unknown(reason if type(reason) is str else 'DISPATCH_POLICY_UNAVAILABLE')
        occupancy, detail = bc._route_occupancy(ctx, worker)
        if occupancy == 'FREE':
            free.append(worker)
        elif occupancy == 'BUSY':
            waiting.append(f'{worker}=busy:{detail}')
        else:
            doubts.append(detail)
    if free:
        return 'CLEARED', 'capacity cleared: free Developer routes ' + ','.join(free)
    if doubts:
        raise bc._Unknown(doubts[0])
    return 'HOLD', 'capacity: every Developer route busy or awaiting authorization (' + \
        ', '.join(waiting) + ')'


def _route_occupancy(ctx, worker):
    """Return ('BUSY', Task ID), ('FREE', None) or ('UNKNOWN', code) for one route.

    A Task occupies the route only under the checks that make it RUNNING, so a
    terminal or unowned Task does not count. Failed evidence is neither busy nor free.
    """
    bc = _bc()
    doubt = None
    for task_id in sorted(ctx.task_ids()):
        try:
            record = ctx.record(task_id)
            developer = record.get('developer')
            if (type(developer) is not dict or
                    developer.get('state') != 'DEVELOPER_DISPATCHING' or
                    worker_dispatch.worker_key_for(developer.get('provider_profile')) != worker or
                    task_status.derive_state(record) in bc._TERMINAL_TASK_STATES):
                continue
            pid, _ = bc._attempt_process(ctx, record, bc._executor_task(record))
        except bc._Unknown as exc:
            doubt = doubt or exc.code
            continue
        if pid is not None:
            return 'BUSY', task_id
    return ('UNKNOWN', doubt) if doubt is not None else ('FREE', None)


def _merged(record):
    """True only for a recorded GitHub merge marker.

    ``task_status.derive_state`` also says COMPLETED once a Developer result is
    returned to the PM (callback or completion PM_RETURNED), before any formal
    review or merge, so that state alone never makes a row DONE.
    """
    events = record.get('completion_events')
    return task_status.derive_state(record) == 'COMPLETED' and any(
        type(event) is dict and event.get('source') == 'GITHUB' and
        event.get('marker_type') == 'merge' and event.get('marker_status') == 'MERGED'
        for event in (events if type(events) is list else []))


def _row_done(ctx, row):
    bc = _bc()
    task_id = row.get('task_id')
    if type(task_id) is str:
        record = ctx.record(task_id)
        return record is not None and bc._merged(record)
    if type(row.get('pr')) is int:
        return ctx.pull(row['pr'])['merged']
    return False


def _gate(ctx, row, gate, rows_by_id):
    """Return (HOLD|CLEARED|ANOMALY, evidence) for one parsed predicate."""
    bc = _bc()
    kind, value = gate
    if kind == 'capacity':
        return bc._capacity(ctx)
    if kind == 'pr_merged':
        pull = ctx.pull(value)
        if pull['merged']:
            return 'CLEARED', f'pr_merged:{value} cleared: PR merged'
        if pull['state'] == 'closed':
            return 'ANOMALY', f'pr_merged:{value} can never clear: PR closed unmerged'
        return 'HOLD', f'pr_merged:{value}: PR #{value} open'
    if kind == 'issue_closed':
        issue = ctx.get(f'issues/{value}')
        state = issue.get('state') if type(issue) is dict else None
        if state == 'closed':
            return 'CLEARED', f'issue_closed:{value} cleared'
        if state == 'open':
            return 'HOLD', f'issue_closed:{value}: issue #{value} open'
        raise bc._Unknown('ISSUE_UNAVAILABLE')
    if kind == 'dispatch_denied':
        task_id = row.get('task_id') if type(row.get('task_id')) is str else None
        owner_ref = row.get('owner_ref') if type(row.get('owner_ref')) is str else None
        decision = ctx.evaluate(value, owner_ref, task_id)
        reason = getattr(decision, 'reason_code', None)
        if getattr(decision, 'allowed', None) is True:
            return 'CLEARED', f'dispatch_denied:{value} cleared: {reason}'
        if reason in bc._AUTHORIZATION_WAIT_CODES:
            return 'HOLD', f'dispatch_denied:{value}: {reason}'
        if reason in bc._UNCHECKABLE_WORKER_CODES:
            return 'ANOMALY', f'dispatch_denied:{value} names no policy Worker'
        raise bc._Unknown(reason if type(reason) is str else 'DISPATCH_POLICY_UNAVAILABLE')
    if kind == 'lease':
        record = ctx.record(value)
        if record is None:
            return 'ANOMALY', f'lease:{value}: no such Task'
        state = task_status.derive_state(record)
        if state in ('RUNNING', 'BLOCKED'):
            return 'HOLD', f'lease:{value}: Task {state}'
        if state == 'UNKNOWN':
            raise bc._Unknown('LEASE_TASK_STATE_UNKNOWN')
        return 'CLEARED', f'lease:{value} cleared: Task {state}'
    if kind == 'time':
        if ctx.now < value:
            return 'HOLD', f'time:{bc._stamp(value)} not reached'
        return 'CLEARED', f'time:{bc._stamp(value)} reached'
    target = rows_by_id[value]
    if bc._row_done(ctx, target):
        return 'CLEARED', f'after row {bc._label(value)} cleared: row DONE'
    return 'HOLD', f'after row {bc._label(value)}: row not DONE'


def _row_gates(row, rows_by_id):
    """Parse blocker and after; return (gates, anomaly evidence or None)."""
    bc = _bc()
    gates = []
    blocker = row.get('blocker')
    if blocker is not None:
        parsed = bc.parse_predicate(blocker)
        if parsed is None:
            return None, 'blocker is free text, not a checkable predicate'
        gates.append(parsed)
    after = row.get('after')
    if after is not None:
        parsed = bc.parse_predicate(after)
        if parsed is None and type(after) is str and after in rows_by_id:
            seen, current = {row.get('id')}, after
            while True:
                if current in seen:
                    return None, 'after forms a cycle and can never clear'
                seen.add(current)
                following = rows_by_id[current].get('after')
                if (type(following) is not str or following not in rows_by_id or
                        bc.parse_predicate(following) is not None):
                    break
                current = following
            parsed = ('row', after)
        if parsed is None:
            return None, 'after is free text, not a predicate or row id'
        gates.append(parsed)
    return gates, None


def _finish(ctx, row, record, gates, rows_by_id, idle, *, needs_developer):
    """Apply Owner wait and predicates to work that has no live evidence."""
    bc = _bc()
    wait = bc._owner_wait(ctx, row, record)
    if wait is not None:
        return bc.WAITING_OWNER, [wait]
    results = [bc._gate(ctx, row, gate, rows_by_id) for gate in gates]
    anomalies = [evidence for status, evidence in results if status == 'ANOMALY']
    if anomalies:
        return bc.ANOMALY, anomalies
    held = [evidence for status, evidence in results if status == 'HOLD']
    if held:
        return bc.BLOCKED, held
    cleared = [evidence for status, evidence in results if status == 'CLEARED']
    if needs_developer and not any(gate[0] == 'capacity' for gate in gates):
        status, evidence = bc._capacity(ctx)
        if status == 'HOLD':
            return bc.BLOCKED, [evidence]
        cleared.append(evidence)
    return bc.ACTIONABLE, idle + cleared


def _classify_pr(ctx, row, number, gates, rows_by_id):
    bc = _bc()
    ctx.covered_prs.add(number)
    pull = ctx.pull(number)
    head = pull['head']['sha']
    if pull['merged']:
        return bc.DONE, [f'PR #{number} merged']
    if pull['state'] == 'closed':
        return bc.ANOMALY, [f'PR #{number} closed unmerged but still on board']
    pending = bc._pending_checks(ctx, head)
    if pending:
        return bc.WAITING_CI, [f'PR #{number} head {head[:12]} pending: ' + ','.join(pending)]
    return bc._finish(ctx, row, None, gates, rows_by_id,
                      [f'open PR #{number} head {head[:12]} has no Task and no pending check'],
                      needs_developer=False)


def _related(ctx, record):
    """Build decide()'s related snapshot the way task advance does, read-only."""
    bc = _bc()
    events = record.get('completion_events')
    events = list(events) if type(events) is list else []
    repairs = []
    claims = record.get('review_repair_claims')
    for claim in claims if type(claims) is list else []:
        if type(claim) is not dict:
            continue
        try:
            repair_record = ctx.record(claim.get('repair_task_id'))
            state = (task_advance.derive_repair_state(repair_record)
                     if type(repair_record) is dict else 'RECORD_UNAVAILABLE')
        except bc._Unknown:
            state = 'RECORD_UNAVAILABLE'
        repairs.append(dict(claim, repair_state=state))
    outcomes = task_advance._review_outcomes(record, bc._claims(record), ctx.get)
    return {'events': events + outcomes, 'repair_claims': repairs}


def _classify_repair(ctx, row, record, head, gates, rows_by_id):
    """Classify WAITING_REPAIR from the repair Task itself, never from the parent."""
    bc = _bc()
    repair = next(item for item in reversed(record['review_repair_claims'])
                  if type(item) is dict and item.get('head_sha') == head)
    task_id = repair.get('repair_task_id')
    child = ctx.record(task_id)
    derived = task_status.derive_state(child)
    if derived in bc._TERMINAL_TASK_STATES:
        return bc.ANOMALY, [f'repair Task {task_id} {derived}: dead repair']
    if task_advance.derive_repair_state(child) == 'PENDING':
        return bc._finish(ctx, row, record, gates, rows_by_id,
                          [f'repair Task {task_id} is claimed but was never dispatched'],
                          needs_developer=True)
    executor = bc._executor_task(child)
    if executor is None:
        raise bc._Unknown('REPAIR_EXECUTOR_UNVERIFIABLE')
    if executor != repair.get('executor_task'):
        return bc.ANOMALY, [f'repair Task {task_id} executor does not match the repair claim']
    pid, since = bc._attempt_process(ctx, child, executor, binding=record)
    if pid is None:
        return bc.ANOMALY, [f'repair Task {task_id}: {bc._no_process(since)}: dead repair']
    return bc.RUNNING, [f'repair Task {task_id} process {pid} proven in the D6 root, started '
                        f'after dispatch {bc._stamp(since[0])}']


def _classify_task(ctx, row, task_id, gates, rows_by_id):
    bc = _bc()
    record = ctx.record(task_id)
    if record is None:
        return bc.ANOMALY, [f'Task {task_id} not found in TaskStore']
    bc._cover_record(ctx, row, record)
    derived = task_status.derive_state(record)
    if bc._merged(record):
        return bc.DONE, ['Task COMPLETED: merge marker recorded']
    if derived in ('ABANDONED', 'FAILED'):
        return bc.ANOMALY, [f'Task {derived}: dead Task still on board']
    number = bc._pr_number(row, record)
    pull = ctx.pull(number) if number is not None else None
    # A terminal PR outranks any attempt, review or repair the Task still records
    # as live, so it is checked before the Developer state or any D6 or process read.
    if type(pull) is dict and pull['merged']:
        return bc.ACTIONABLE, [f'PR #{number} merged but the Task has no merge marker: '
                               'reconcile through task advance']
    if type(pull) is dict and pull['state'] == 'closed':
        return bc.ANOMALY, [f'PR #{number} closed unmerged but the Task is still live']
    developer = record.get('developer')
    developer_state = developer.get('state') if type(developer) is dict else None
    if developer_state == 'DEVELOPER_DISPATCHING':
        pid, since = bc._attempt_process(ctx, record, bc._executor_task(record))
        if pid is not None:
            return bc.RUNNING, [f'process {pid} proven in the Task D6 root, started after '
                                f'dispatch {bc._stamp(since[0])}']
        return bc.ANOMALY, [f'Task DEVELOPER_DISPATCHING but {bc._no_process(since)}: dead Task']
    if developer_state != 'DEVELOPER_RESPONDED':
        step = task_advance.decide(record, None, None, {})
        label = developer_state if type(developer_state) is str else 'none'
        return bc._finish(ctx, row, record, gates, rows_by_id,
                          [f'Developer state {label}; next step {step}'],
                          needs_developer=developer_state in (None, 'DEVELOPER_PENDING'))

    executor = bc._executor_task(record)
    d6 = ctx.d6_snapshot(record, executor) if executor is not None else None
    step = task_advance.decide(record, d6, pull, bc._related(ctx, record))
    head = d6.get('result_head') if type(d6) is dict else None
    idle = f'Task has next step {step} and nothing is running'
    if step == 'WAITING_REPAIR':
        return bc._classify_repair(ctx, row, record, head, gates, rows_by_id)
    if step == 'RECORD_REVIEW_RESULT':
        claim = max((item for item in bc._claims(record)
                     if item.get('pr') == number and item.get('head_sha') == head),
                    key=lambda item: item.get('version', 0))
        if claim.get('state') == 'COMPLETED':
            idle = (f"review claim v{claim.get('version')} COMPLETED: result awaiting "
                    f'ingestion, next step {step}')
        elif claim.get('state') == 'DISPATCHED' and claim.get('executor_task') == executor:
            pid = ctx.live_process(ctx.d6_root(record, claim.get('reviewer_task')),
                                   bc._interval(claim.get('claimed_at')),
                                   {'session': claim.get('native_session_id')})
            if pid is not None:
                return bc.WAITING_REVIEW, [
                    f"review claim v{claim.get('version')} on PR #{number} head {head[:12]}; "
                    f'Reviewer process {pid} proven in its D6']
    if step in bc._CI_WAIT_STEPS and type(pull) is dict and head == pull['head']['sha']:
        pending = bc._pending_checks(ctx, head)
        if pending:
            return bc.WAITING_CI, [f'PR #{number} head {head[:12]} pending: ' + ','.join(pending)]
    return bc._finish(ctx, row, record, gates, rows_by_id, [idle], needs_developer=False)


def _row_problem(row):
    bc = _bc()
    if type(row) is not dict:
        return 'row is not an object'
    if row.get('id') is not None and (type(row['id']) is not str or not 1 <= len(row['id']) <= 128):
        return 'id is not a short string'
    if row.get('task_id') is not None and (type(row['task_id']) is not str or
                                           bc._TASK_ID.fullmatch(row['task_id']) is None):
        return 'task_id is not a Task ID: unknown Task'
    if row.get('pr') is not None and (type(row['pr']) is not int or
                                      not 1 <= row['pr'] <= 9999999):
        return 'pr is not a PR number'
    for field in ('owner_ref', 'after', 'blocker'):
        if row.get(field) is not None and (type(row[field]) is not str or len(row[field]) > 512):
            return f'{field} is not a short string'
    if 'next' in row:
        val = row['next']
        if (type(val) is not list or
                not 1 <= len(val) <= 16 or
                any(type(item) is not str or not 1 <= len(item) <= 64 for item in val)):
            return 'invalid next list'
    return None


def _classify_row(ctx, row, rows_by_id, duplicate_ids):
    bc = _bc()
    problem = bc._row_problem(row)
    if problem is not None:
        return bc.ANOMALY, [problem]
    # The row lists this PR and Task whatever its own state turns out to be, so
    # neither may later be reported as missing from the board.
    if row.get('pr') is not None:
        ctx.covered_prs.add(row['pr'])
    if row.get('task_id') is not None:
        ctx.covered_tasks.add(row['task_id'])
    if row.get('id') in duplicate_ids:
        return bc.ANOMALY, ['duplicate row id']
    gates, problem = bc._row_gates(row, rows_by_id)
    if problem is not None:
        return bc.ANOMALY, [problem]

    if getattr(ctx, 'ledger_unreadable', False) and row.get('task_id') is None:
        return bc.UNKNOWN, ['LEDGER_UNREADABLE']

    ledger_claims = getattr(ctx, 'ledger_claims', None)
    if ledger_claims is not None:
        row_id = row.get('id')
        row_claims = [c for c in ledger_claims
                      if isinstance(c, dict) and c.get('row') == row_id] if isinstance(row_id, str) else []
        live_claims = [c for c in row_claims if bc.claim_live(c, ctx.store, ctx.now)]

        if row.get('task_id') is not None:
            if any(c.get('task_id') is not None and c.get('task_id') != row['task_id'] for c in live_claims):
                return bc.ANOMALY, ['board task differs from ledger claim']
            return bc._classify_task(ctx, row, row['task_id'], gates, rows_by_id)

        bound_claims = [c for c in live_claims if c.get('task_id') is not None]
        if bound_claims:
            bound_task_id = bound_claims[-1]['task_id']
            ctx.covered_tasks.add(bound_task_id)
            return bc._classify_task(ctx, row, bound_task_id, gates, rows_by_id)

        if live_claims:
            return bc.CLAIMED, ['live reservation']

        if row_claims:
            return bc.ANOMALY, ['stale ledger claim']

    elif row.get('task_id') is not None:
        return bc._classify_task(ctx, row, row['task_id'], gates, rows_by_id)

    if row.get('pr') is not None:
        return bc._classify_pr(ctx, row, row['pr'], gates, rows_by_id)
    return bc._finish(ctx, row, None, gates, rows_by_id,
                      ['row has no Task or PR: work not dispatched'], needs_developer=True)


def _unlisted(ctx):
    """Report open PRs and live Tasks that no board row covers.

    A successful list read proves the orphan, so it stays ANOMALY even when
    another row is UNKNOWN; that uncertainty stays on the affected row.
    """
    bc = _bc()
    findings = []
    try:
        pulls = ctx.get_pages('pulls?state=open')
        numbers = []
        for pull in pulls:
            number = pull.get('number') if type(pull) is dict else None
            if type(number) is not int or number < 1:
                raise bc._Unknown('GITHUB_READ_INVALID')
            numbers.append(number)
    except bc._Unknown as exc:
        findings.append({'row': 'github:open-prs', 'state': bc.UNKNOWN, 'evidence': [exc.code]})
    else:
        for number in sorted(set(numbers) - ctx.covered_prs):
            findings.append({'row': f'pr:#{number}', 'state': bc.ANOMALY,
                             'evidence': ['open PR missing from the board']})
    try:
        task_ids = sorted(ctx.task_ids())
    except bc._Unknown as exc:
        return findings + [{'row': 'taskstore', 'state': bc.UNKNOWN, 'evidence': [exc.code]}]
    for task_id in task_ids:
        if task_id in ctx.covered_tasks:
            continue
        try:
            record = ctx.record(task_id)
        except bc._Unknown as exc:
            findings.append({'row': f'task:{task_id}', 'state': bc.UNKNOWN, 'evidence': [exc.code]})
            continue
        developer = record.get('developer')
        if (type(developer) is dict and developer.get('state') == 'DEVELOPER_DISPATCHING' and
                task_status.derive_state(record) not in bc._TERMINAL_TASK_STATES):
            findings.append({'row': f'task:{task_id}', 'state': bc.ANOMALY,
                             'evidence': ['live Task (DEVELOPER_DISPATCHING) missing from the '
                                          'board']})
    return findings


def _label(value):
    return re.sub(r'[^\x21-\x7e]', '?', value)[:64]


def _row_label(row, index):
    bc = _bc()
    if type(row) is dict and type(row.get('id')) is str and row['id']:
        return bc._label(row['id'])
    return f'row[{index}]'


def check_board(rows, *, store, github_get, list_processes, read_d6=None,
                evaluate=None, now=None, ledger_claims=None,
                ledger_unreadable=False):
    """Classify every row and return {'result', 'exit_code', 'rows'}."""
    bc = _bc()
    if ledger_claims == 'LEDGER_UNREADABLE':
        ledger_claims = None
        ledger_unreadable = True
    ctx = bc._Context(store=store, github_get=github_get, list_processes=list_processes,
                      read_d6=read_d6 or task_advance._d6_snapshot,
                      evaluate=evaluate or worker_dispatch.evaluate,
                      now=now or datetime.now(timezone.utc),
                      ledger_claims=ledger_claims,
                      ledger_unreadable=ledger_unreadable)
    rows_by_id, duplicate_ids = {}, set()
    for row in rows:
        if type(row) is dict and type(row.get('id')) is str:
            if row['id'] in rows_by_id:
                duplicate_ids.add(row['id'])
            rows_by_id[row['id']] = row
    findings = []
    for index, row in enumerate(rows):
        try:
            state, evidence = bc._classify_row(ctx, row, rows_by_id, duplicate_ids)
        except bc._Unknown as exc:
            state, evidence = bc.UNKNOWN, [exc.code]
        except Exception:
            state, evidence = bc.UNKNOWN, ['BOARD_CHECK_INTERNAL_ERROR']
        findings.append({'row': bc._row_label(row, index), 'state': state, 'evidence': evidence})
        if state == bc.DONE and type(row) is dict and type(row.get('next')) is list:
            row_id = row['id'] if type(row.get('id')) is str and row['id'] else bc._row_label(row, index)
            for next_id in row['next']:
                if next_id not in rows_by_id:
                    findings.append({
                        'row': f'next:{next_id}',
                        'state': bc.ACTIONABLE,
                        'evidence': [f'pending next item of {row_id}'],
                    })
    findings.extend(bc._unlisted(ctx))
    anomalies = None
    if ledger_claims is not None:
        anomalies = []
        for item, row in zip(findings, rows):
            if item['state'] != bc.ACTIONABLE or type(row) is not dict:
                continue
            if not any(isinstance(c, dict) and c.get('row') == row.get('id') and
                       bc.claim_live(c, ctx.store, ctx.now) for c in ledger_claims):
                anomalies.append({'row': item['row'], 'code': 'IDLE_RUNNABLE'})
    if any(item['state'] in bc.FLAGGED_STATES for item in findings):
        result, code = 'FLAGGED', 2
    elif any(item['state'] == bc.UNKNOWN for item in findings):
        result, code = 'UNKNOWN', 1
    else:
        result, code = 'CLEAN', 0
    out = {'result': result, 'exit_code': code, 'rows': findings}
    if anomalies is not None:
        out['anomalies'] = anomalies
    return out
