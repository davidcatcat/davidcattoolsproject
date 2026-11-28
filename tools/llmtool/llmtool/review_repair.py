"""Bounded, event driven repair dispatch for a failed formal review."""

from datetime import datetime, timezone
import contextlib
import hashlib
import io
import json
import re
import shutil
import threading
from argparse import Namespace

from . import review_result, run_budget, sensitive_paths, task_completion, worker_availability, worker_dispatch
from .errors import LLMToolError
from .task_store import REVIEW_DISPUTE_DEFERRABLE, REVIEW_DISPUTE_SEVERITIES, _json_bytes, new_id

# This same-class repair cap is separate from worker_dispatch's per-dispatch limit of 1.
# #2367: it follows policy.json through run_budget (repair_policy.repair_attempts_max).
MAX_REPAIR_ATTEMPTS = run_budget.MAX_REPAIR_ATTEMPTS
MAX_EXCERPT_CHARS = 2000


def _refuse(code, *, prelaunch=False):
    error = LLMToolError('Review repair refused; details omitted')
    error.review_repair_code = code
    if prelaunch:
        error.prelaunch_refusal = True
    raise error from None


def _is_prelaunch_coded_refusal(exc, store, repair_task_id):
    """Return True only when exc is a coded pre-launch refusal and no Developer was bound."""
    if getattr(exc, 'prelaunch_refusal', False) is not True:
        return False
    code = getattr(exc, 'review_repair_code', None) or getattr(exc, 'code', None)
    if type(code) is not str or re.fullmatch(r'[A-Z0-9_]{1,64}', code) is None:
        return False
    if code == 'DELEGATE_TEXT_SENSITIVE':
        return False
    try:
        repair_task = store.get(repair_task_id)
    except Exception:
        return False
    return type(repair_task) is dict and repair_task.get('developer') is None


def _invoke_claimed_adapter(*, store, task_id, claim, repair_task, developer,
                            packet, executor_task, profile, dispatch_adapter,
                            delegate_callable, high_debug_task=None,
                            high_debug_ref=None, high_debug_family=None):
    adapter = dispatch_adapter
    if adapter is None:
        adapter = lambda **request: _delegate_repair(
            **request, delegate=delegate_callable, store=store,
            high_debug_task=high_debug_task,
            high_debug_ref=high_debug_ref,
            high_debug_family=high_debug_family)
    try:
        result = adapter(task=repair_task, developer=developer, packet=packet,
                         executor_task=executor_task, provider_profile=profile,
                         model=developer.get('model'))
    except Exception as exc:
        if not _is_prelaunch_coded_refusal(exc, store, claim['repair_task_id']):
            _update_claim_state(store, task_id, claim, 'UNKNOWN')
        raise
    _update_claim_state(store, task_id, claim, 'DISPATCHED')
    return result


def _refuse_unclaimed(code, repair_task_id):
    """Refuse after a repair Task may have been recorded but before any claim.

    No claim is committed, so no count is used. A Task recorded under
    repair_task_id is never dispatched or reported as a repair; the caller
    surfaces it for PM reconciliation.
    """
    error = LLMToolError('Review repair refused; details omitted')
    error.review_repair_code = code
    error.review_repair_unclaimed_task_id = repair_task_id
    raise error from None


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


# #1502 WP-D D13 (section 4, E10): review disputes. These helpers only read
# records and choose; they never run or launch a review.

def dispute_refuse(code):
    error = LLMToolError(code)
    error.dispute_code = code
    raise error from None


_FINDING_LINE = re.compile(r'^Finding: (F[1-9][0-9]{0,3}) \[([A-Za-z]+)\](?=[ \t]|$)', re.M)


def verdict_findings(text):
    """Return {finding_id: severity} from the formal verdict Finding lines.

    {} when no Finding line carries an ID; None when an ID repeats or a
    severity word is unknown, so nothing can be disputed by an unclear ID.
    """
    if type(text) is not str:
        return None
    found = {}
    for match in _FINDING_LINE.finditer(text):
        finding_id, severity = match.groups()
        if severity not in REVIEW_DISPUTE_SEVERITIES or finding_id in found:
            return None
        found[finding_id] = severity
    return found


def dispute_open_on(task, pr, head):
    """True while a dispute on this PR head is OPEN; a malformed list counts as open."""
    disputes = task.get('review_disputes', []) if type(task) is dict else None
    if type(disputes) is not list:
        return True
    return any(type(item) is not dict or
               (item.get('pr') == pr and item.get('head_sha') == head and
                item.get('state') == 'OPEN')
               for item in disputes)


def _withdrawal_in_version(item, version):
    """True when a RULED WITHDRAWN entry's ruling covers exactly this review version.

    The entry's own review_version must equal version (a non-bool int). An
    arbitrate ruling also carries review_version, which must match too.
    """
    entry_version = item.get('review_version')
    if type(entry_version) is not int or entry_version != version:
        return False
    ruling = item['ruling']
    if 'review_version' in ruling or ruling.get('path') == 'arbitrate':
        ruling_version = ruling.get('review_version')
        return type(ruling_version) is int and ruling_version == version
    return True


def withdrawn_findings(task, pr, head, version):
    """Finding IDs an arbiter ruling WITHDREW from this PR head and review version.

    A ruling withdraws its finding only from the one formal verdict it ruled
    on. A version that is not a non-bool int withdraws nothing (fail closed).
    """
    disputes = task.get('review_disputes', []) if type(task) is dict else None
    if type(disputes) is not list or type(version) is not int:
        return frozenset()
    return frozenset(
        item['finding_id'] for item in disputes
        if type(item) is dict and type(item.get('finding_id')) is str and
        item.get('pr') == pr and item.get('head_sha') == head and
        item.get('state') == 'RULED' and type(item.get('ruling')) is dict and
        item['ruling'].get('outcome') == 'WITHDRAWN' and item['ruling'].get('head_sha') == head and
        _withdrawal_in_version(item, version))


# Formal Reviewer keys and their model families, in the fixed choice order.
ARBITER_REVIEWERS = (('claude-reviewer', 'claude'), ('codex-reviewer', 'codex'))
# AGY is deliberately absent: since Owner #289 it serves Gemini and Claude ids,
# so developer_family resolves its family from the recorded model.
_PROFILE_FAMILIES = {'claude': 'claude', 'codex': 'codex',
                     'opencode': 'opencode', 'gw-agent': 'gw-agent', 'company-gw': 'gw-agent'}
_REVIEW_ACCOUNT_MODES = {'CODEX_CLI': 'codex-reviewer',
                         'EXPLICIT_CLAUDE_CONFIG_DIR': 'claude-reviewer',
                         'DEFAULT_SECOND_ACCOUNT': 'claude-reviewer'}
_VERDICT_ACTOR = re.compile(r'<!-- activity: type=review_verdict actor=(Claude|Codex|AGY) ')


# Model-name markers, in the fixed order used for a route that can serve more
# than one vendor's model.
_MODEL_FAMILY_MARKERS = (('claude', 'claude'), ('gpt', 'codex'),
                         ('codex', 'codex'), ('gemini', 'gemini'))


def _named_model_family(model):
    """The family a recorded model name shows, or None when it shows none."""
    if type(model) is not str:
        return None
    lowered = model.lower()
    for marker, named in _MODEL_FAMILY_MARKERS:
        if marker in lowered:
            return named
    return None


def developer_family(developer):
    """The actual model family of a Task's Developer, or None when unknown.

    #2302 part 1 (Codex security v91 F2, A1c, CWE-863): a record that names its
    run model takes the family of that model on every route
    (developer_routes.model_family), never the route's. A model of no
    determinable family is UNKNOWN (None) on the claude, codex and agy routes,
    and choose_arbiter then refuses rather than pairing a same-family arbiter
    with it; the multi-vendor opencode, company-gw and gw-agent routes keep
    their former reading of such a name (marker, else the route key). A legacy
    record with no recorded model keeps its former result: claude, codex,
    opencode and gw-agent by route key, and AGY unknown.
    """
    if type(developer) is not dict:
        return None
    profile = developer.get('provider_profile')
    family = _PROFILE_FAMILIES.get(profile)
    model = developer.get('model')
    if type(model) is str and model:
        if family is None and profile != 'agy':
            return None
        from .developer_routes import MODEL_FAMILY_UNKNOWN, model_family
        named = model_family(model)
        if named != MODEL_FAMILY_UNKNOWN:
            return named
        if family in ('opencode', 'gw-agent'):
            return _named_model_family(model) or family
        return None
    if profile == 'agy':
        return None
    return family


def claim_reviewer(claim):
    """The Reviewer key of one COMPLETED review claim, or None when unknown.

    The verdict marker names the actor; a claim without one falls back to its
    account mode. Any other account mode is unknown.
    """
    if type(claim) is not dict:
        return None
    result = claim.get('review_result')
    text = result.get('review_text') if type(result) is dict else None
    if type(text) is str:
        first = next((line.strip() for line in text.splitlines() if line.strip()), '')
        match = _VERDICT_ACTOR.match(first)
        if match is not None:
            return {'Claude': 'claude-reviewer', 'Codex': 'codex-reviewer',
                    'AGY': 'agy-reviewer'}[match.group(1)]
    return _REVIEW_ACCOUNT_MODES.get(claim.get('account_mode'))


def choose_arbiter(*, developer_family, first_reviewer, first_review_session):
    """Pure arbiter choice (#1502 section 4): llmtool picks; the PM cannot.

    The arbiter's family differs from the actual Developer family and, when
    possible, from the first Reviewer's. Otherwise it is the cross-family first
    Reviewer again, in a fresh session. The first review session never rules.
    Refuses DISPUTE_ARBITER_UNAVAILABLE when no such Reviewer exists.
    """
    families = dict(ARBITER_REVIEWERS)
    if (type(developer_family) is not str or not developer_family or
            first_reviewer not in families or
            type(first_review_session) is not str or not first_review_session):
        dispute_refuse('DISPUTE_ARBITER_UNAVAILABLE')
    cross = [(key, family) for key, family in ARBITER_REVIEWERS if family != developer_family]
    other = [(key, family) for key, family in cross if family != families[first_reviewer]]
    if other:
        reviewer, family = other[0]
        fresh = False
    elif any(key == first_reviewer for key, _ in cross):
        reviewer, family = first_reviewer, families[first_reviewer]
        fresh = True
    else:
        dispute_refuse('DISPUTE_ARBITER_UNAVAILABLE')
    return {'reviewer': reviewer, 'family': family, 'fresh_session': fresh,
            'excluded_session': first_review_session}


def _update_claim_state(store, task_id, claim, state):
    record = store.get(task_id)
    claims = record.get('review_repair_claims', [])
    for index, item in enumerate(claims):
        if item.get('repair_task_id') == claim['repair_task_id']:
            updated = dict(item, state=state)
            claims[index] = updated
            store.compare_and_update(
                task_id, expected={'record_sha256': record['record_sha256']},
                review_repair_claims=claims)
            return
    _refuse('REPAIR_CLAIM_UNAVAILABLE')


_LOCAL_DISPATCH_GUARD = threading.Lock()
_LOCAL_DISPATCH_LOCKS = {}


@contextlib.contextmanager
def _local_dispatch_lock(repair_task_id):
    """Process-local stand-in for a store that is not a TaskStore (no other process)."""
    from .task_store import LINEAGE_LOCK_TIMEOUT_SECONDS
    with _LOCAL_DISPATCH_GUARD:
        lock = _LOCAL_DISPATCH_LOCKS.setdefault(repair_task_id, threading.Lock())
    if not lock.acquire(timeout=LINEAGE_LOCK_TIMEOUT_SECONDS):
        raise LLMToolError('REPAIR_DISPATCH_LOCK_BUSY')
    try:
        yield
    finally:
        lock.release()


def _repair_dispatch_guard(store, repair_task_id):
    """#2475 F2: take one repair Task's dispatch lock; return an ExitStack that releases it.

    The lock is held across the delegate call and the claim's final state write,
    so one continuation has one dispatcher at a time. A lock that cannot be
    taken refuses REPAIR_CLAIM_UNAVAILABLE and changes nothing.
    """
    from .task_store import TaskStore
    stack = contextlib.ExitStack()
    try:
        if isinstance(store, TaskStore):
            stack.enter_context(store.repair_dispatch_lock(repair_task_id))
        else:
            stack.enter_context(_local_dispatch_lock(repair_task_id))
    except Exception:
        stack.close()
        _refuse('REPAIR_CLAIM_UNAVAILABLE')
    return stack


def _replay_claimed_test_repair(*, store, task_id, existing, developer, packet,
                                executor_task, profile, dispatch_adapter,
                                delegate_callable, packet_version,
                                high_debug_task=None, high_debug_ref=None,
                                high_debug_family=None):
    """Replay the delegation of one CLAIMED claim whose repair Task has no Developer.

    #2475 F2: the dispatch lock is taken first and the claim and the repair Task
    are read again under it. A competing caller that already took the dispatch is
    seen as a DISPATCHED claim or a Developer assignment, and this call delegates
    nothing; so a loser can never move a claim the winner is finishing.
    """
    repair_task_id = existing['repair_task_id']
    guard = _repair_dispatch_guard(store, repair_task_id)
    with guard:
        try:
            current = store.get(task_id)
            repair_task = store.get(repair_task_id)
        except Exception:
            _refuse('REPAIR_CLAIM_UNAVAILABLE')
        claims = current.get('review_repair_claims') if type(current) is dict else None
        fresh = next((item for item in claims
                      if type(item) is dict and item.get('repair_task_id') == repair_task_id),
                     None) if type(claims) is list else None
        if type(fresh) is not dict or type(repair_task) is not dict:
            _refuse('REPAIR_CLAIM_UNAVAILABLE')
        if fresh.get('state') != 'CLAIMED' or repair_task.get('developer') is not None:
            dispatched = fresh.get('state') == 'DISPATCHED'
            return {'claim': fresh, 'repair_task_id': repair_task_id,
                    'attempt': fresh.get('attempt'), 'replayed': dispatched,
                    'next_action': (f"review-dispatch --version {packet_version + 1}"
                                    if dispatched else f"task delegate {repair_task_id}")}
        result = _invoke_claimed_adapter(
            store=store, task_id=task_id, claim=fresh, repair_task=repair_task,
            developer=developer, packet=packet, executor_task=executor_task,
            profile=profile, dispatch_adapter=dispatch_adapter,
            delegate_callable=delegate_callable,
            high_debug_task=high_debug_task,
            high_debug_ref=high_debug_ref,
            high_debug_family=high_debug_family)
        claim = dict(fresh, state='DISPATCHED')
        return {'result': result, 'attempt': claim['attempt'],
                'repair_task_id': repair_task_id, 'claim': claim, 'replayed': True,
                'next_action': f"review-dispatch --version {packet_version + 1}"}


_MAX_EXTRA_READ_PATHS = 16
_MAX_TOTAL_READ_PATHS = 32
_ADMISSIBLE_TEST_READ_PATH = re.compile(
    r'(?:tools/[A-Za-z0-9._-]+/tests|scripts)/test_[A-Za-z0-9_]{1,120}\.py\Z')


def _admissible_repair_read_path(path):
    """Return True only for a safe, non-sensitive repository test module path."""
    if (type(path) is not str or not path or len(path) > 256 or
            path.startswith('/') or '\\' in path or
            any(part in ('', '.', '..') for part in path.split('/')) or
            _ADMISSIBLE_TEST_READ_PATH.fullmatch(path) is None):
        return False
    try:
        return sensitive_paths.is_sensitive(path) is False
    except Exception:
        return False


def _merge_repair_read_paths(read_paths, extra_read_paths, write_paths):
    """Add admissible failing test modules to read_paths within existing bounds."""
    if type(read_paths) is not list or type(write_paths) is not list:
        return read_paths
    if type(extra_read_paths) not in (list, tuple) or not extra_read_paths:
        return read_paths
    merged = list(read_paths)
    seen = set(merged)
    write_set = set(write_paths)
    cap = max(len(merged), _MAX_TOTAL_READ_PATHS)
    added = 0
    for path in extra_read_paths:
        if added >= _MAX_EXTRA_READ_PATHS or len(merged) >= cap:
            break
        if path in seen or path in write_set or not _admissible_repair_read_path(path):
            continue
        seen.add(path)
        merged.append(path)
        added += 1
    return merged


def _worker_key(profile):
    return {'company-gw': 'company-gw', 'gw-agent': 'gw-agent',
            'opencode': 'opencode-developer', 'claude': 'claude-developer',
            'agy': 'agy'}.get(profile)


def _delegate_repair(*, task, developer, packet, executor_task, delegate=None,
                     store=None, high_debug_task=None, high_debug_ref=None,
                     high_debug_family=None, **_route_metadata):
    """Resume the original direct Developer route with a bounded repair request."""
    from . import task_submission_cli

    invoke = task_submission_cli.run_task_delegate if delegate is None else delegate
    assignment = task.get('pm_assignment')
    acceptance = assignment.get('acceptance') if type(assignment) is dict else None
    read_paths = developer.get('read_paths')
    write_paths = developer.get('write_paths')
    if (type(acceptance) is not list or type(read_paths) is not list or
            type(write_paths) is not list):
        _refuse('REPAIR_DEVELOPER_BINDING_INVALID', prelaunch=True)
    profile = developer.get('provider_profile')
    data_class = developer.get('data_class')
    if profile == 'gw-agent' and data_class not in ('SYNTHETIC', 'PUBLIC_REPO'):
        _refuse('REPAIR_DATA_CLASS_INVALID', prelaunch=True)
    try:
        original_task = store.get(packet['task'])
    except Exception:
        _refuse('REPAIR_HEAD_MOVED', prelaunch=True)
    if type(original_task) is not dict:
        _refuse('REPAIR_HEAD_MOVED', prelaunch=True)
    try:
        registration, _snapshot = _registered_d6(original_task, executor_task)
    except LLMToolError as exc:
        exc.prelaunch_refusal = True
        raise
    args = Namespace(
        task_id=task['task_id'], provider=profile,
        mode='direct' if profile in ('codex', 'agy', 'claude', 'gw-agent') else 'proposal',
        owner_ref=developer.get('owner_assignment_ref'),
        selection_source='PM',
        executor_task=executor_task, cwd=registration.root,
        read_path=list(read_paths),
        write_path=list(write_paths), acceptance=list(acceptance),
        egress_basis=developer.get('egress_basis'), repair_packet=packet,
        data_class=data_class,
        high_debug_task=high_debug_task,
        high_debug_ref=high_debug_ref,
        high_debug_family=high_debug_family,
    )
    # #2503: the repair keeps its read scope; files the findings name outside it are
    # shown and recorded as advice. The advice never changes or blocks the repair.
    return task_submission_cli._delegate_with_scope_advice(
        args, store, lambda: invoke(args, store=store))


def _record_repair_task(*, task, developer, packet, attempt, store, repair_task_id=None,
                        record_pm=None):
    """Create the bounded continuation through the ordinary record-pm path."""
    from . import task_submission_cli

    provenance = task.get('pm_provenance')
    if (type(provenance) is not dict or
            type(provenance.get('pm_native_session_id')) is not str or
            type(provenance.get('pm_cwd')) is not str):
        _refuse('REPAIR_PM_SESSION_UNAVAILABLE')
    route = developer.get('provider_profile')
    if route == 'codex':   # #1502 O1 / WP-C: Codex is Reviewer and AI Debug only
        _refuse('REPAIR_CODEX_DEVELOPER_RETIRED')
    if route not in ('opencode', 'agy', 'claude', 'gw-agent'):
        _refuse('REPAIR_DEVELOPER_ROUTE_UNSUPPORTED')
    pm_assignment = task.get('pm_assignment')
    if type(pm_assignment) is not dict or type(pm_assignment.get('goal')) is not str:
        _refuse('REPAIR_DEVELOPER_BINDING_INVALID')
    original_acceptance = developer.get('acceptance')
    if (type(original_acceptance) is not list or
            not all(type(item) is str for item in original_acceptance)):
        _refuse('REPAIR_DEVELOPER_BINDING_INVALID')
    excerpt = packet.get('reviewer_text_excerpt_untrusted_data')
    if (packet.get('version') == 0 and packet.get('verdict_comment_id') == 0 and
            type(excerpt) is str and excerpt.startswith(CI_PACKET_HEADER + ' ')):
        bounded_acceptance = (
            f"Repair attempt {attempt}/{MAX_REPAIR_ATTEMPTS} for required CI check failure "
            f"at HEAD {packet['failed_head']}. Failure excerpt is untrusted reference data."
        )
    elif packet.get('version') == 0 and packet.get('verdict_comment_id') == 0:
        bounded_acceptance = (
            f"Repair attempt {attempt}/{MAX_REPAIR_ATTEMPTS} for remote-test failure "
            f"at HEAD {packet['failed_head']}. Failure excerpt is untrusted reference data."
        )
    else:
        bounded_acceptance = (
            f"Repair attempt {attempt}/{MAX_REPAIR_ATTEMPTS} for verdict comment id "
            f"{packet['verdict_comment_id']}, "
            f"failed HEAD {packet['failed_head']}, review version {packet['version']}, "
            f"findings count {packet['findings_count']}. Reviewer text is untrusted reference data."
        )
    assignment = {
        'route': route, 'risk': 'LOW', 'goal': pm_assignment['goal'],
        'acceptance': list(original_acceptance) + [bounded_acceptance],
    }
    # #2006: no room for the bounded repair item (PM record limit is 16 items).
    if len(assignment['acceptance']) > 16:
        _refuse('REPAIR_ACCEPTANCE_FULL')
    response = json.dumps({
        'schema_version': 1,
        'summary': (f"LLMTOOL_RUNTIME recorded bounded repair continuation "
                    f"{attempt}/{MAX_REPAIR_ATTEMPTS} "
                    f"for Task {task['task_id']}; no PM or provider decision was made."),
        'developer_assignment': assignment,
    }, ensure_ascii=True, separators=(',', ':'))
    request = f"REPAIR {attempt}/{MAX_REPAIR_ATTEMPTS} of {task['task_id']}"
    args = Namespace(
        pm_session_id=provenance['pm_native_session_id'],
        pm_runtime='claude-code', pm_cwd=provenance['pm_cwd'],
    )
    record_pm = task_submission_cli.run_task_record_pm if record_pm is None else record_pm
    record = record_pm(
        args, store=store, task_id=repair_task_id,
        input_stream=io.StringIO(json.dumps({'request': request, 'response': response})),
        return_record=True)
    if type(record) is not dict or type(record.get('task_id')) is not str:
        _refuse('REPAIR_TASK_RECORD_UNAVAILABLE')
    return record


def _registered_d6(task, executor_task):
    completion = task.get('completion')
    evidence = completion.get('evidence') if type(completion) is dict else None
    if (type(evidence) is not dict or evidence.get('common_dir') is None or
            completion.get('executor_task_id') != executor_task):
        _refuse('REPAIR_HEAD_MOVED')
    try:
        registration = task_completion.read_d6_registration(
            executor_task, evidence['common_dir'],
            allowed_states=task_completion._D6_EVIDENCE_STATES)
        snapshot = task_completion.collect_git_snapshot(registration.root)
    except Exception:
        _refuse('REPAIR_HEAD_MOVED')
    if (snapshot.head != registration.result_sha or snapshot.root != registration.root or
            snapshot.common_dir != registration.common_dir or
            snapshot.branch != registration.branch):
        _refuse('REPAIR_HEAD_MOVED')
    return registration, snapshot


def _current_d6_head(task, executor_task, current_head):
    if current_head is not None:
        return current_head(task, executor_task)
    registration, _snapshot = _registered_d6(task, executor_task)
    return registration.result_sha


def _failed_event(task, *, executor_task, pr_number, github_get, body_holder=None,
                  body_out=None):
    claims = task.get('review_dispatch_claim')
    claims = [claims] if type(claims) is dict else claims
    if type(claims) is not list:
        _refuse('REVIEW_FAIL_EVENT_MISSING')
    events = task.get('completion_events', [])
    candidates = []
    for event in events:
        if (type(event) is not dict or event.get('source') != 'REVIEWER' or
                event.get('state') != 'PENDING'):
            continue
        matching_claims = [item for item in claims if type(item) is dict and
                           'purpose' not in item and
                           item.get('executor_task') == executor_task and
                           item.get('pr') == pr_number and
                           item.get('head_sha') == event.get('source_sha') and
                           type(item.get('version')) is int and
                           1 <= item['version'] <= 9999 and
                           event.get('session_id') == 'review-' + event.get('event_id')]
        if matching_claims:
            claim = max(matching_claims, key=lambda item: item['version'])
            candidates.append((event.get('received_at', ''), event, claim))
    if not candidates:
        _refuse('REVIEW_FAIL_EVENT_MISSING')
    _, event, claim = max(candidates, key=lambda row: row[0])
    get = task_completion._github_get if github_get is None else github_get
    comments = get(f'issues/{pr_number}/comments?per_page=100')
    if type(comments) is not list or len(comments) >= 100:
        _refuse('REVIEW_RESULT_COMMENTS_INVALID')
    verdicts = []
    for comment in comments:
        if type(comment) is not dict:
            _refuse('REVIEW_RESULT_COMMENTS_INVALID')
        if review_result.is_verdict_too_long(
                comment, head=claim['head_sha'], version=claim['version']):
            _refuse('REVIEW_VERDICT_TOO_LONG')
        verdict = review_result._comment_verdict(
            comment, head=claim['head_sha'], version=claim['version'], task=task)
        if verdict is not None:
            verdicts.append((verdict, comment))
    if not verdicts:
        _refuse('REVIEW_FAIL_EVENT_MISSING')
    (updated, comment_id, outcome, findings_count), comment = max(
        verdicts, key=lambda pair: pair[0][:2])
    if (event.get('source_sha') != claim['head_sha'] or outcome != 'REVIEW_FAIL' or
            comment_id is None):
        _refuse('REVIEW_FAIL_EVENT_MISSING')
    body = comment.get('body', '')
    excerpt = body[:MAX_EXCERPT_CHARS]
    packet = {
        'task': task['task_id'], 'pr': pr_number, 'executor_task': executor_task,
        'failed_head': claim['head_sha'], 'version': claim['version'],
        'verdict_comment_id': comment_id, 'findings_count': findings_count,
        'reviewer_text_excerpt_untrusted_data': excerpt,
    }
    encoded = json.dumps(packet, ensure_ascii=True, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
    if len(encoded) > 8192:
        _refuse('REPAIR_PACKET_INVALID')
    target = body_holder if body_holder is not None else body_out
    if target is not None:
        if isinstance(target, list):
            target.append(body)
        elif isinstance(target, dict):
            target['body'] = body
        elif callable(target):
            target(body)
    return packet, event


def _remote_test_failure(task, *, executor_task, pr_number, receipt, details=None):
    """Build a repair packet only from a digest-valid FAIL receipt for this exact Task head."""
    from . import task_submission_cli
    completion = task.get('completion')
    expected_executor = completion.get('executor_task_id') if type(completion) is dict else None
    if (type(receipt) is not dict or receipt.get('task_id') != task.get('task_id') or
            receipt.get('run_id') != task.get('run_id') or
            receipt.get('executor_task') != executor_task or
            expected_executor != executor_task or receipt.get('result') != 'FAIL' or
            type(receipt.get('source_sha')) is not str or
            re.fullmatch(r'[0-9a-f]{40}', receipt['source_sha']) is None or
            type(receipt.get('modules')) is not list or not receipt['modules']):
        _refuse('REMOTE_TEST_RECEIPT_INVALID')
    digest_input = dict(receipt)
    claimed = digest_input.pop('receipt_digest', None)
    digest = hashlib.sha256(_json_bytes(digest_input)).hexdigest()
    excerpt = receipt.get('failure_excerpt')
    try:
        valid_excerpt = (type(excerpt) is str and len(excerpt.encode('utf-8')) <= 4096 and
                         len(excerpt.splitlines()) <= 40)
    except UnicodeError:
        valid_excerpt = False
    if claimed != digest or not valid_excerpt:
        _refuse('REMOTE_TEST_RECEIPT_INVALID')
    if type(details) is dict:
        screened_excerpt = (
            task_submission_cli._screen_text(excerpt, CI_EXCERPT_MAX_BYTES + 1024)
            if excerpt.strip() else '')
        check_name = details.get('check_name')
        if type(check_name) is not str or not check_name:
            check_name = 'EC2 remote-test'
        run_identity = details.get('run_identity')
        if type(run_identity) is not str or not run_identity:
            run_identity = f'receipt={claimed[:8]} executor={executor_task}'
        raw_tests = details.get('failing_tests')
        failing_tests = ([item for item in raw_tests[:20] if type(item) is str and item][:20]
                         if type(raw_tests) is list else [])
        if failing_tests:
            screened_tests = task_submission_cli._screen_text(
                ', '.join(failing_tests), CI_EXCERPT_MAX_BYTES + 1024)
        else:
            screened_tests = 'unavailable'
        raw_modules = details.get('failing_modules')
        failing_modules = ([item for item in raw_modules[:20] if type(item) is str and item]
                           if type(raw_modules) is list else [])
        modules_line = ', '.join(failing_modules) if failing_modules else 'unavailable'
        body = screened_excerpt if screened_excerpt.strip() else 'Failure details unavailable.'
        text = (
            f"REMOTE_TEST_FAILURE check={check_name} {run_identity} head={receipt['source_sha']}\n"
            f"failing_tests={screened_tests}\n"
            f"failing_modules={modules_line}\n"
            f"{body}"
        )[:MAX_EXCERPT_CHARS]
    else:
        text = excerpt[:MAX_EXCERPT_CHARS]
    packet = {
        'task': task['task_id'], 'pr': pr_number, 'executor_task': executor_task,
        'failed_head': receipt['source_sha'], 'version': 0,
        'verdict_comment_id': 0, 'findings_count': 0,
        'reviewer_text_excerpt_untrusted_data': text,
    }
    while len(json.dumps(packet, ensure_ascii=True, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')) > 8192:
        if not packet['reviewer_text_excerpt_untrusted_data']:
            _refuse('REPAIR_PACKET_INVALID')
        packet['reviewer_text_excerpt_untrusted_data'] = (
            packet['reviewer_text_excerpt_untrusted_data'][:-256])
    return packet


# #1502 WP-D D11, #2337: exact-head required CI checks a CI repair may answer.
# #2793 A13.0: resolved lazily via __getattr__ so a missing or invalid default
# profile does not fail on cold import before _resolve_ci_profile can refuse.
# #2793 A13.0: a missing, invalid or ambiguous profile refuses with this code.
CI_REPAIR_PROFILE_INVALID = 'CI_REPAIR_PROFILE_INVALID'
_UNSET = object()
CI_EXCERPT_MAX_BYTES = 4096
CI_EXCERPT_MAX_LINES = 40
CI_PACKET_HEADER = 'CI_CHECK_FAILURE'
_CI_FAILURE_FIELDS = frozenset(('head', 'check_name', 'check_run_id', 'conclusion', 'excerpt'))
_CI_FAILURE_OPTIONAL_FIELDS = frozenset((
    'failing_tests', 'failing_modules', 'workflow_run_id', 'job_id', 'details_unavailable'))


def _resolve_ci_profile(value):
    """Return a validated CiProfile for the CI repair step.

    The resolution and refusal follow mac_full_due.resolve_profile (#2793 slice
    1). A missing default and any invalid, duplicate-check or ambiguous value
    refuse CI_REPAIR_PROFILE_INVALID; the original names are never used as a
    fallback.
    """
    try:
        from . import mac_full_due
        resolved = (mac_full_due.resolve_profile()
                    if value is _UNSET
                    else mac_full_due.resolve_profile(value))
    except Exception:
        _refuse(CI_REPAIR_PROFILE_INVALID)
    checks = (resolved.linux_code_check, resolved.docs_check,
              resolved.gateway_check, resolved.ai_loop_check,
              resolved.macos_check)
    if len(set(checks)) != len(checks):
        _refuse(CI_REPAIR_PROFILE_INVALID)
    return resolved


def _ci_repair_checks(value=_UNSET):
    """Return the CI check names the repair step accepts, from the profile."""
    resolved = _resolve_ci_profile(value)
    return (resolved.linux_code_check, resolved.docs_check,
            resolved.gateway_check, resolved.ai_loop_check,
            resolved.macos_check)


def __getattr__(name):
    if name == 'CI_REPAIR_CHECKS':
        return _ci_repair_checks()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _ci_failure(task, *, executor_task, pr_number, ci_failure, profile=_UNSET):
    """Build a repair packet from one failed exact-head required CI check.

    Returns (packet, identity_sha256). The identity binds the Task, PR, head and
    check run, so a replayed failure of the same run reuses its claim even when
    the untrusted excerpt differs. Any malformed input refuses CI_FAILURE_INVALID.
    The accepted check names come from profile (#2793 A13.0).
    """
    from . import task_submission_cli
    accepted_checks = _ci_repair_checks(profile)
    if (type(ci_failure) is not dict or
            not _CI_FAILURE_FIELDS <= set(ci_failure) or
            not set(ci_failure) <= (_CI_FAILURE_FIELDS | _CI_FAILURE_OPTIONAL_FIELDS) or
            type(ci_failure['head']) is not str or
            re.fullmatch(r'[0-9a-f]{40}', ci_failure['head']) is None or
            type(ci_failure['check_name']) is not str or
            ci_failure['check_name'] not in accepted_checks or
            type(ci_failure['check_run_id']) is not int or
            isinstance(ci_failure['check_run_id'], bool) or ci_failure['check_run_id'] < 1 or
            type(ci_failure['conclusion']) is not str or
            ci_failure['conclusion'] != 'failure' or
            type(ci_failure['excerpt']) is not str):
        _refuse('CI_FAILURE_INVALID')
    failing_tests = ci_failure.get('failing_tests')
    if failing_tests is not None and (
            type(failing_tests) is not list or len(failing_tests) > 20 or
            any(type(item) is not str or len(item) > 256 for item in failing_tests)):
        _refuse('CI_FAILURE_INVALID')
    failing_modules = ci_failure.get('failing_modules')
    if failing_modules is not None and (
            type(failing_modules) is not list or len(failing_modules) > 20 or
            any(type(item) is not str or len(item) > 256 for item in failing_modules)):
        _refuse('CI_FAILURE_INVALID')
    for key in ('workflow_run_id', 'job_id'):
        if key in ci_failure:
            val = ci_failure[key]
            if type(val) is not int or isinstance(val, bool) or val < 1:
                _refuse('CI_FAILURE_INVALID')
    if 'details_unavailable' in ci_failure and type(ci_failure['details_unavailable']) is not bool:
        _refuse('CI_FAILURE_INVALID')
    excerpt = ci_failure['excerpt']
    if excerpt.strip():
        excerpt = task_submission_cli._screen_text(excerpt, CI_EXCERPT_MAX_BYTES + 1024)
    try:
        valid_excerpt = (len(excerpt.encode('utf-8')) <= CI_EXCERPT_MAX_BYTES and
                         len(excerpt.splitlines()) <= CI_EXCERPT_MAX_LINES)
    except UnicodeError:
        valid_excerpt = False
    if not valid_excerpt:
        _refuse('CI_FAILURE_INVALID')
    head, name, run_id = ci_failure['head'], ci_failure['check_name'], ci_failure['check_run_id']
    lines = [f"{CI_PACKET_HEADER} check={name} run={run_id} conclusion=failure head={head}"]
    has_details = any(key in ci_failure for key in _CI_FAILURE_OPTIONAL_FIELDS)
    if has_details:
        if 'workflow_run_id' in ci_failure and 'job_id' in ci_failure:
            lines.append(f"workflow_run={ci_failure['workflow_run_id']} job={ci_failure['job_id']}")
        if failing_tests:
            screened_tests = task_submission_cli._screen_text(
                ', '.join(failing_tests), CI_EXCERPT_MAX_BYTES + 1024)
            lines.append(f"failing_tests={screened_tests}")
        else:
            lines.append('failing_tests=unavailable')
        if failing_modules:
            lines.append(f"failing_modules={', '.join(failing_modules)}")
        else:
            lines.append('failing_modules=unavailable')
        if excerpt.strip():
            lines.append(excerpt)
        else:
            lines.append('Failure details unavailable.')
    else:
        lines.append(excerpt)
    text = '\n'.join(lines)[:MAX_EXCERPT_CHARS]
    packet = {
        'task': task['task_id'], 'pr': pr_number, 'executor_task': executor_task,
        'failed_head': head, 'version': 0,
        'verdict_comment_id': 0, 'findings_count': 0,
        'reviewer_text_excerpt_untrusted_data': text,
    }
    # Escaped non-ASCII text can exceed the packet cap; trim the excerpt, never refuse.
    while len(json.dumps(packet, ensure_ascii=True, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')) > 8192:
        if not packet['reviewer_text_excerpt_untrusted_data']:
            _refuse('REPAIR_PACKET_INVALID')
        packet['reviewer_text_excerpt_untrusted_data'] = (
            packet['reviewer_text_excerpt_untrusted_data'][:-256])
    identity = {'kind': 'CI', 'task': task['task_id'], 'pr': pr_number,
                'executor_task': executor_task, 'head': head, 'check_name': name,
                'check_run_id': run_id, 'conclusion': 'failure'}
    identity_sha = hashlib.sha256(json.dumps(
        identity, ensure_ascii=True, sort_keys=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()
    return packet, identity_sha


def _claim_family(claim):
    return claim.get('family', f"pr-{claim.get('pr')}")


# EC2 remote-test and CI repairs store their claims in '<base>-ec2' so they keep
# one test-failure count separate from review repairs (policy test_fail_max).
EC2_FAMILY_SUFFIX = '-ec2'


def ec2_claim_kind(claim):
    """Return 'NEW' or 'LEGACY' for an EC2 repair claim, else None.

    An EC2 claim has a family field and comment_id None. 'NEW' is stored in a
    '-ec2' family; 'LEGACY' was stored in the base family before this split and
    counts on both counts. Anything else, or anything malformed, is a review claim.
    """
    if (type(claim) is not dict or 'comment_id' not in claim or
            claim['comment_id'] is not None or type(claim.get('family')) is not str):
        return None
    return 'NEW' if claim['family'].endswith(EC2_FAMILY_SUFFIX) else 'LEGACY'


def _policy():
    from . import repair_policy
    try:
        return repair_policy.load_policy()
    except LLMToolError:
        _refuse('REPAIR_POLICY_INVALID')


def _review_gate(task, attempts, packet, family, policy, counts=None, *, body=None, inherited=None, debug_used=0):
    """#1502 WP-D (O2, D1, D3): admit one review repair from the valid FAILs per HEAD.

    Each earlier review repair claim stands for a valid FAIL on its HEAD and the
    current FAIL adds its own HEAD, so a re-review of one HEAD never counts twice.
    A FAIL whose IDs show no blocking finding is invalid. The fourth and fifth
    repairs require an applicable confirmed RCA, which is returned for binding.

    D14 (spec section 7): the policy decides before the repair limit. A Task whose
    repair_max repairs are used escalates by kind at its next valid FAIL (ROOT
    SPLIT, child PM_DECIDE, REPLAN FAILED_FINAL); a re-review of a HEAD already
    counted adds no valid FAIL and is refused REPAIR_LIMIT_REACHED.

    L1 (I1-I3, I6): counts, from lineage_counts, gives the repairs used and the
    failed HEADs of the whole continuation chain. UNKNOWN counts as the limit.
    Without counts the Task's own attempts are used.
    """
    from . import repair_policy
    text = body if body is not None else packet['reviewer_text_excerpt_untrusted_data']
    if review_result.blocking_findings(text) == []:
        _refuse('REPAIR_NO_BLOCKING_FINDING')
    # #1502 WP-D D13: a FAIL whose every blocking finding an arbiter withdrew
    # is not a valid FAIL. Findings without IDs all stay blocking.
    withdrawn = withdrawn_findings(task, packet.get('pr'), packet['failed_head'],
                                   packet.get('version'))
    findings = verdict_findings(text) if withdrawn else None
    if findings:
        blocking = {finding_id for finding_id, severity in findings.items()
                    if severity not in REVIEW_DISPUTE_DEFERRABLE}
        if blocking <= withdrawn:
            _refuse('REPAIR_NO_BLOCKING_FINDING')
    if counts is None:
        repair_count = len(attempts)
        known_heads = {claim.get('head_sha'): 'FAIL' for claim in attempts}
    else:
        repair_count = counts.get('review_repairs', repair_policy.UNKNOWN)
        known_heads = counts.get('review_heads', repair_policy.UNKNOWN)
    used = (repair_count if type(repair_count) is int and repair_count >= 0
            else policy['repair_max'])
    spent = used >= policy['repair_max']
    if type(known_heads) is dict:
        heads = dict(known_heads)
        new_fail = packet['failed_head'] not in heads
        heads[packet['failed_head']] = 'FAIL'
        fail_count = repair_policy.review_fail_count(heads)
    else:
        new_fail, fail_count = True, repair_policy.UNKNOWN
    if spent and not new_fail:
        _refuse('REPAIR_LIMIT_REACHED')
    if spent and new_fail and type(fail_count) is int:
        # No repair is left, so this new valid FAIL is at least the escalation FAIL.
        fail_count = max(fail_count, policy['review_fail_max'])
    entries = [entry for entry in task.get('high_debug') or []
               if type(entry) is dict and entry.get('family') == family and
               entry.get('no_pr') is not True and
               entry.get('status') in ('RCA_CONFIRMED', 'RCA_UNCONFIRMED', 'BLOCKED')]
    executor = packet.get('executor_task')
    pr = packet.get('pr')
    target = review_result.diagnosis_findings(
        task, packet['failed_head'], executor, pr=pr, version=packet.get('version'),
        excerpt=packet.get('reviewer_text_excerpt_untrusted_data'))
    on_head = [item for item in entries if item.get('examined_sha') == packet['failed_head']]
    entry = on_head[-1] if on_head else None
    if entry is not None:
        original = review_result.diagnosis_findings(
            task, entry['examined_sha'], executor, before=entry.get('recorded_at'), pr=pr)
        if target is not None:
            if original != target:
                entry = None
        elif used >= repair_policy.REVIEW_REPAIRS_WITHOUT_DEBUG:
            # Older unstructured verdicts can bind the exact review on the
            # same HEAD. Missing evidence or edited review text cannot do so.
            options = {'pr': pr, 'version': packet.get('version'),
                       'excerpt': packet.get('reviewer_text_excerpt_untrusted_data')}
            current_body = review_result.diagnosis_review_body(
                task, packet['failed_head'], executor, **options)
            before_body = review_result.diagnosis_review_body(
                task, packet['failed_head'], executor, before=entry.get('recorded_at'), **options)
            if current_body is None or before_body != current_body:
                entry = None
    if entry is None and target is not None and used >= repair_policy.REVIEW_REPAIRS_WITHOUT_DEBUG:
        # Reuse an earlier same-family report only for the same formally bound findings.
        claim_heads = {claim.get('head_sha') for claim in attempts if type(claim) is dict}
        for item in reversed(entries):
            if item['status'] != 'RCA_CONFIRMED' or item['examined_sha'] not in claim_heads:
                continue
            original = review_result.diagnosis_findings(
                task, item['examined_sha'], executor, before=item.get('recorded_at'), pr=pr)
            if original == target:
                entry = item
                break
    if (entry is None or entry['status'] != 'RCA_CONFIRMED') and inherited is not None:
        entry = inherited
    lineage = task.get('lineage')
    try:
        action = repair_policy.review_action(
            kind=lineage.get('kind') if type(lineage) is dict else 'root',
            fail_count=fail_count, repair_count=repair_count,
            debug_status=entry['status'] if entry else None, group_debug_used=debug_used,
            policy=policy)
    except LLMToolError:
        _refuse('REPAIR_LINEAGE_INVALID')
    if spent and action in ('REPAIR', 'DEBUG'):
        _refuse('REPAIR_LIMIT_REACHED')
    if action == 'DEBUG':
        _refuse('REPAIR_DEBUG_REQUIRED')
    if action != 'REPAIR':
        _refuse(f'REPAIR_{action}_REQUIRED')
    confirmed = entry is not None and entry['status'] == 'RCA_CONFIRMED'
    code = repair_policy.admit_repair(repair_count=repair_count, rca_confirmed=confirmed,
                                      policy=policy)
    if code is not None:
        _refuse(code)
    return entry if used else None


_LIVE_CLAIM_STATES = ('CLAIMED', 'DISPATCHED', 'UNKNOWN')


def _auto_replay_claim(store, task, task_id, existing):
    """Admit an auto replay of a live review claim only when it is the chain's only one.

    v91 F1: checked under the chain lock on fresh reads. The replayed claim
    must still be live on a fresh read of this Task, and the whole chain (every
    Task, every PR family; '-ec2' claims excluded) must hold exactly one review
    repair claim. Anything else, a count in doubt included, refuses
    REVIEW_REPAIR_PM_REQUIRED with no state change. Returns the fresh claim.
    """
    from . import lineage_counts
    key = lineage_counts.chain_key(store, task)
    try:
        release_chain = lineage_counts.acquire_chain_lock(store, key)
    except Exception:
        _refuse('REPAIR_CHAIN_LOCK_UNAVAILABLE')
    try:
        try:
            current = store.get(task_id)
        except Exception:
            current = None
        if type(current) is not dict or current.get('task_id') != task_id:
            _refuse('REVIEW_REPAIR_PM_REQUIRED')
        # The chain is write-once, so a key that changed is a read in doubt.
        if lineage_counts.chain_key(store, current) != key:
            _refuse('REVIEW_REPAIR_PM_REQUIRED')
        claims = current.get('review_repair_claims', [])
        if type(claims) is not list:
            _refuse('REVIEW_REPAIR_PM_REQUIRED')
        fields = ('repair_task_id', 'executor_task', 'pr', 'head_sha', 'version')
        fresh = [item for item in claims
                 if type(item) is dict and
                 all(item.get(name) == existing.get(name) for name in fields) and
                 _claim_family(item) == _claim_family(existing) and
                 item.get('state') in _LIVE_CLAIM_STATES]
        if (len(fresh) != 1 or type(_claim_family(fresh[0])) is not str or
                _claim_family(fresh[0]).endswith(EC2_FAMILY_SUFFIX)):
            _refuse('REVIEW_REPAIR_PM_REQUIRED')
        used = lineage_counts.chain_review_repairs(store, current)
        if type(used) is not int or used != 1:
            _refuse('REVIEW_REPAIR_PM_REQUIRED')
        return fresh[0]
    finally:
        release_chain()


def repair_review(*, store, task_id, executor_task, pr_number, family=None, github_get=None,
                  dispatch_adapter=None, current_head=None, auto_review=False,
                  review_dispatcher=None, delegate_callable=None,
                  repair_task_recorder=None, retry_claim_id=None,
                  remote_test_receipt=None, ci_failure=None, profile=_UNSET,
                  auto=False,
                  extra_read_paths=None, remote_test_details=None,
                  high_debug_task=None, high_debug_ref=None,
                  high_debug_family=None):
    """Claim and dispatch one repair for a failed review, remote test or CI check.

    remote_test_receipt (EC2) and ci_failure (#1502 WP-D D11) are exclusive; both
    are test failures and share one count, the '<base>-ec2' family. For a
    ci_failure, the accepted check names come from the effective CI profile
    resolver; an explicit profile refuses CI_REPAIR_PROFILE_INVALID because
    later claim-verification and retry callers resolve the same effective
    profile without a per-call profile argument, and an unresolvable or invalid
    resolved profile also refuses CI_REPAIR_PROFILE_INVALID.

    auto is True only from task advance / drive. An auto review repair (a retry
    included) is refused REVIEW_REPAIR_PM_REQUIRED, inside the chain-locked
    admission, once any review repair claim exists anywhere in the chain (every
    chain Task, every PR family; UNKNOWN counts as used). An auto replay of a
    live claim is also decided under the chain lock: it is returned only when
    that claim is the chain's only review repair claim (v91 F1). A manual task
    repair (auto False) keeps D1 and its replay. auto has no effect on a
    test-failure repair.
    """
    if ci_failure is not None and remote_test_receipt is not None:
        _refuse('CI_FAILURE_INVALID')
    if profile is not _UNSET:
        _refuse(CI_REPAIR_PROFILE_INVALID)
    test_failure = remote_test_receipt is not None or ci_failure is not None
    task = store.get(task_id)
    if type(task) is not dict or task.get('task_id') != task_id:
        _refuse('REPAIR_TASK_INVALID')
    developer = task.get('developer')
    completion = task.get('completion')
    if (type(developer) is not dict or type(completion) is not dict or
            completion.get('executor_task_id') != executor_task or
            developer.get('state') not in ('DEVELOPER_RESPONDED', 'DEVELOPER_OUTPUT_REJECTED')):
        _refuse('REPAIR_DEVELOPER_BINDING_INVALID')
    from . import direct_reassignment
    developer = direct_reassignment.authoring_route(store, task, developer, executor_task)
    if extra_read_paths is None and type(ci_failure) is dict:
        extra_read_paths = ci_failure.get('failing_modules')
    if extra_read_paths is not None:
        merged_read = _merge_repair_read_paths(
            developer.get('read_paths'), extra_read_paths, developer.get('write_paths'))
        if merged_read != developer.get('read_paths'):
            developer = dict(developer, read_paths=merged_read)
    provider_profile = developer.get('provider_profile')
    claude_cli = (provider_profile == 'claude' and
                  developer.get('backend_session_semantics') == 'CLI_REPORTED_SESSION_ID')
    if provider_profile == 'claude' and not claude_cli:
        _refuse('REPAIR_DEVELOPER_ROUTE_UNSUPPORTED')
    if provider_profile == 'codex':   # #1502 O1 / WP-C: Codex is Reviewer and AI Debug only
        _refuse('REPAIR_CODEX_DEVELOPER_RETIRED')
    worker = _worker_key(provider_profile)
    if worker is None:
        _refuse('REPAIR_DEVELOPER_ROUTE_UNSUPPORTED')
    if provider_profile == 'company-gw':
        _refuse('REPAIR_DEVELOPER_ROUTE_UNSUPPORTED')
    if provider_profile == 'claude' and shutil.which(provider_profile) is None:
        _refuse('RUNTIME_NOT_ON_PATH')
    ci_identity_sha = None
    body_holder = []
    if ci_failure is not None:
        packet, ci_identity_sha = _ci_failure(task, executor_task=executor_task,
                                              pr_number=pr_number, ci_failure=ci_failure,
                                              profile=profile)
    elif remote_test_receipt is None:
        packet, _event = _failed_event(task, executor_task=executor_task,
                                       pr_number=pr_number, github_get=github_get,
                                       body_holder=body_holder)
    else:
        packet = _remote_test_failure(task, executor_task=executor_task,
                                      pr_number=pr_number, receipt=remote_test_receipt,
                                      details=remote_test_details)
    if _current_d6_head(task, executor_task, current_head) != packet['failed_head']:
        _refuse('REPAIR_HEAD_MOVED')
    # #1502 WP-D D13: no review repair while a dispute on this PR head is OPEN.
    if not test_failure and dispute_open_on(task, pr_number, packet['failed_head']):
        _refuse('REPAIR_DISPUTE_OPEN')
    policy_task = executor_task if claude_cli else task_id
    def require_original_worker():
        decision = worker_dispatch.evaluate(
            worker, developer.get('owner_assignment_ref'), policy_task)
        if not decision.allowed:
            _refuse(decision.reason_code)
        # #1816: after the Owner policy, before any claim, Task or D6 write.
        # #2622: a paused AGY/OpenCode route refuses the repair here too, with
        # the explicit resume step in the refusal.
        pause = worker_availability.route_pause_code(store, worker)
        if pause is not None:
            error = LLMToolError(worker_availability.route_pause_message(worker))
            error.review_repair_code = pause
            error.route_pause_resume_step = worker_availability.route_pause_resume_step(worker)
            raise error from None
        if worker_availability.fresh_quota_fail(store, worker):
            _refuse(worker_availability.REFUSAL_CODE)
    if not auto:
        require_original_worker()
    base_family = f'pr-{pr_number}' if family is None else family
    if (type(base_family) is not str or
            re.fullmatch(r'[a-z0-9._-]{1,64}', base_family) is None or
            base_family.endswith(EC2_FAMILY_SUFFIX)):
        _refuse('REPAIR_FAMILY_INVALID')
    # The stored family: a review repair stays in the base family; an EC2
    # remote-test or CI repair goes to '<base>-ec2' (one test-failure count).
    family = (base_family if not test_failure
              else base_family + EC2_FAMILY_SUFFIX)
    if len(family) > 64:
        _refuse('REPAIR_FAMILY_INVALID')
    # The count lives on the root Task, which task advance drives.
    try:
        candidate_ids = list(store.task_ids())
    except Exception:
        _refuse('REPAIR_COUNT_UNAVAILABLE')
    for other_id in candidate_ids:
        if other_id == task_id:
            continue
        try:
            other_record = store.get(other_id)
        except Exception:
            _refuse('REPAIR_COUNT_UNAVAILABLE')
        if type(other_record) is not dict:
            _refuse('REPAIR_COUNT_UNAVAILABLE')
        if other_record.get('task_id') == task_id:
            continue
        other_claims = other_record.get('review_repair_claims')
        if type(other_claims) is not list:
            continue
        if any(type(item) is not dict for item in other_claims):
            continue
        if any(item.get('repair_task_id') == task_id for item in other_claims):
            _refuse('REPAIR_ROOT_TASK_REQUIRED')
    claims = task.get('review_repair_claims', [])
    if type(claims) is not list:
        _refuse('REPAIR_CLAIM_UNAVAILABLE')
    stored = [claim for claim in claims if _claim_family(claim) == family]
    if not test_failure:
        # Review count: every claim in the base family, legacy EC2 claims included.
        attempts = stored
    else:
        # Test-failure count (EC2 and CI): the '-ec2' family plus legacy EC2
        # claims stored in base.
        attempts = [claim for claim in claims
                    if _claim_family(claim) == family or
                    (_claim_family(claim) == base_family and
                     ec2_claim_kind(claim) == 'LEGACY')]
    packet_version = packet.get('version', 0)
    # A CI claim binds its head and check run, not the untrusted excerpt.
    packet_sha = ci_identity_sha if ci_identity_sha is not None else hashlib.sha256(json.dumps(
        packet, ensure_ascii=True, sort_keys=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()
    if packet_version == 0:
        existing = next((claim for claim in attempts
                         if claim.get('pr') == pr_number and
                         claim.get('head_sha') == packet['failed_head'] and
                         claim.get('packet_sha256') == packet_sha and
                         claim.get('state') in ('CLAIMED', 'DISPATCHED', 'UNKNOWN')), None)
        claim_version = max((claim.get('version', 0) for claim in claims
                             if claim.get('pr') == pr_number and
                             claim.get('head_sha') == packet['failed_head'] and
                             type(claim.get('version')) is int), default=0) + 1
    else:
        existing = next((claim for claim in attempts
                         if claim.get('pr') == pr_number and
                         claim.get('head_sha') == packet['failed_head'] and
                         claim.get('version') == packet_version and
                         claim.get('state') in ('CLAIMED', 'DISPATCHED', 'UNKNOWN')), None)
        claim_version = packet_version
    retry_claim = next((claim for claim in attempts
                        if retry_claim_id is not None and
                        claim.get('repair_task_id') == retry_claim_id and
                        claim.get('pr') == pr_number and
                        claim.get('head_sha') == packet['failed_head'] and
                        claim.get('state') == 'DISPATCHED'), None)
    if retry_claim_id is not None and retry_claim is None:
        _refuse('REPAIR_CLAIM_UNAVAILABLE')
    if existing is not None and existing is not retry_claim:
        # #2438: an already-counted direct repair can continue on a replacement
        # assignment. The delegate serializes the old controller and handoff,
        # then applies its ordinary scope, policy, quota and D6 checks.
        repair_task = None
        if dispatch_adapter is None or existing.get('state') == 'CLAIMED':
            try:
                repair_task = store.get(existing['repair_task_id'])
            except (LLMToolError, KeyError):
                pass
        repair_developer = repair_task.get('developer') if type(repair_task) is dict else None
        history = (repair_developer.get('direct_handoffs') or []
                   if type(repair_developer) is dict else [])
        if history and repair_developer.get('state') == 'DEVELOPER_RESPONDED':
            direct_reassignment.reconcile_claim(
                store, existing['repair_task_id'], history[-1]['old_assignment'])
            saved = store.get(task_id)
            bound = next(claim for claim in saved['review_repair_claims']
                         if claim['repair_task_id'] == existing['repair_task_id'])
            return {'claim': bound, 'repair_task_id': existing['repair_task_id'],
                    'attempt': bound['attempt'], 'replayed': True, 'next_action': 'task advance'}
        if (direct_reassignment.replacement_candidate(repair_developer) and
                (repair_developer.get('provider_profile') == provider_profile or
                 (history and history[-1]['old_profile'] == provider_profile))):
            if (auto and not test_failure and
                    repair_developer.get('dispatch_boundary') != 'PREPARED'):
                _auto_replay_claim(store, task, task_id, existing)
            value = _delegate_repair(task=repair_task, developer=developer, packet=packet,
                                     executor_task=executor_task, delegate=delegate_callable,
                                     store=store,
                                     high_debug_task=high_debug_task,
                                     high_debug_ref=high_debug_ref,
                                     high_debug_family=high_debug_family)
            saved = store.get(task_id)
            bound = next(claim for claim in saved['review_repair_claims']
                         if claim['repair_task_id'] == existing['repair_task_id'])
            return {'result': value, 'claim': bound, 'attempt': existing['attempt'],
                    'repair_task_id': existing['repair_task_id'], 'replayed': True,
                    'next_action': 'task advance'}
        if auto:
            require_original_worker()
        if auto and not test_failure:
            # v91 F1: an auto replay is decided under the chain lock too.
            existing = _auto_replay_claim(store, task, task_id, existing)
        if (existing.get('state') == 'CLAIMED' and
                type(repair_task) is dict and repair_task.get('developer') is None):
            # #2475 F2: serialized per repair Task; the claim is read again under the lock.
            # #2492: a review claim recovered to CLAIMED with no Developer replays the same way.
            return _replay_claimed_test_repair(
                store=store, task_id=task_id, existing=existing, developer=developer,
                packet=packet, executor_task=executor_task, profile=provider_profile,
                dispatch_adapter=dispatch_adapter, delegate_callable=delegate_callable,
                packet_version=packet_version,
                high_debug_task=high_debug_task,
                high_debug_ref=high_debug_ref,
                high_debug_family=high_debug_family)
        task_id_for_repair = existing.get('repair_task_id')
        dispatched = existing.get('state') == 'DISPATCHED'
        return {'claim': existing, 'repair_task_id': task_id_for_repair,
                'replayed': dispatched,
                'next_action': (f"review-dispatch --version {packet_version + 1}"
                                if dispatched else f"task delegate {task_id_for_repair}")}
    if auto:
        require_original_worker()
    # #1502 WP-D: limits from policy.json; review repairs pass the counting gate.
    # L1 (I1, I2, I6): the counts cover the whole continuation chain; a link in
    # doubt makes them UNKNOWN, which counts as the limit. The replay above
    # finds its claim among this Task's own claims; an auto review replay is
    # then admitted only under the chain lock (_auto_replay_claim, v91 F1).
    # L1 phase 2: the counts are read and the claim is written under the chain
    # lock of the chain's oldest Task (a Task without continues is its own
    # key), so two repairs in one chain cannot both spend the last repair. The
    # counts also cover later Tasks of the chain. A lock that cannot be taken
    # refuses REPAIR_CHAIN_LOCK_UNAVAILABLE before any count or write; the lock
    # is released on every path, before the provider call.
    from . import lineage_counts
    key = lineage_counts.chain_key(store, task)
    try:
        release_chain = lineage_counts.acquire_chain_lock(store, key)
    except Exception:
        _refuse('REPAIR_CHAIN_LOCK_UNAVAILABLE')
    try:
        # The chain is write-once, so a key that changed is a read in doubt.
        if lineage_counts.chain_key(store, task) != key:
            _refuse('REPAIR_COUNT_UNAVAILABLE')
        if auto and not test_failure:
            # Checked only here, under the chain lock, on a fresh read of this
            # Task: a claim committed since the first read is seen.
            try:
                current = store.get(task_id)
            except Exception:
                current = None
            used = (lineage_counts.chain_review_repairs(store, current)
                    if type(current) is dict and current.get('task_id') == task_id
                    else None)
            if type(used) is not int or used != 0:
                _refuse('REVIEW_REPAIR_PM_REQUIRED')
        counts = lineage_counts.lineage_counts(store, task, base_family=base_family,
                                               include_successors=True)
        if not test_failure and retry_claim is None:
            heads = counts.get('review_heads')
            replaced_failure = any(item.get('head_sha') == packet['failed_head'] and
                                   item.get('state') == 'FAILED' for item in attempts)
            if (type(heads) is dict and packet['failed_head'] in heads and
                    not replaced_failure and counts['review_repairs'] < _policy()['repair_max']):
                _refuse('REPAIR_HEAD_ALREADY_CLAIMED')
        rca_entry = None
        inherited = None
        if not test_failure:
            from . import high_debug, start_failure
            debug_used, _ = lineage_counts.chain_debug_used(
                store, task, task.get('high_debug', []), high_debug._debug_entry_used)
            inherited = start_failure.inherited_rca(store, task, packet)
            body = body_holder[0] if body_holder else None
            rca_entry = _review_gate(task, attempts, packet, family, _policy(), counts=counts,
                                     body=body, inherited=inherited[1] if inherited else None,
                                     debug_used=debug_used)
            if rca_entry is not None:
                packet = dict(packet,
                              high_debug_report_untrusted_data=rca_entry['report'][:6000],
                              high_debug_report_sha256=rca_entry['report_sha256'])
        else:
            test_policy = _policy()
            used = counts['test_fails']
            if type(used) is not int or used < 0 or used >= test_policy['test_fail_max']:
                _refuse('REPAIR_LIMIT_REACHED')
        # The store numbers attempts within the stored family only.
        attempt = len(stored) + 1
        identity = (pr_number, packet['failed_head'], claim_version, attempt)
        if any(type(item) is dict and
               (item.get('pr'), item.get('head_sha'), item.get('version'),
                item.get('attempt')) == identity for item in claims):
            # A review and an EC2 claim in different families can share this
            # identity, which the store refuses; refuse before any write.
            _refuse('REPAIR_CLAIM_UNAVAILABLE')
        repair_task_id = new_id()
        claim = {
            'executor_task': executor_task, 'pr': pr_number,
            'head_sha': packet['failed_head'], 'version': claim_version,
            'attempt': attempt, 'family': family, 'state': 'CLAIMED',
            'repair_task_id': repair_task_id,
            'comment_id': (None if packet_version == 0 else packet.get('verdict_comment_id')),
            'packet_sha256': packet_sha,
            'claimed_at': _now(),
        }
        if rca_entry is not None:
            claim['high_debug_report_sha256'] = rca_entry['report_sha256']
            if inherited is not None and rca_entry is inherited[1]:
                claim['high_debug_source_task'] = inherited[0]
        next_claims = list(claims)
        if retry_claim is not None:
            retry_index = next((index for index, item in enumerate(next_claims)
                                if item.get('repair_task_id') == retry_claim_id and
                                item.get('state') == 'DISPATCHED'), None)
            if retry_index is None:
                _refuse('REPAIR_CLAIM_UNAVAILABLE')
            next_claims[retry_index] = dict(next_claims[retry_index], state='FAILED')
        next_claims.append(claim)

        # #1502 WP-D D11 repair 1: no counted claim exists until the repair Task is
        # recorded and validated and the final authority recheck passes. Every
        # refusal from here to the CAS leaves the claims, and so the counts,
        # unchanged; a Task it may have left is surfaced for PM reconciliation.
        recorder = repair_task_recorder
        try:
            if recorder is None:
                repair_task = _record_repair_task(
                    task=task, developer=developer, packet=packet,
                    attempt=attempt, store=store, repair_task_id=repair_task_id)
            else:
                repair_task = recorder(task=task, developer=developer, packet=packet,
                                       attempt=attempt, store=store,
                                       repair_task_id=repair_task_id)
        except LLMToolError as error:
            error.review_repair_unclaimed_task_id = repair_task_id
            if not getattr(error, 'review_repair_code', None) and not getattr(error, 'code', None):
                error.review_repair_code = 'REPAIR_TASK_RECORD_UNAVAILABLE'
            raise
        except Exception:
            _refuse_unclaimed('REPAIR_TASK_RECORD_UNAVAILABLE', repair_task_id)
        if (type(repair_task) is not dict or
                repair_task.get('task_id') != repair_task_id):
            _refuse_unclaimed('REPAIR_TASK_RECORD_UNAVAILABLE', repair_task_id)

        # Re-read the Owner switch immediately before the claim and the provider side effect.
        decision = worker_dispatch.evaluate(
            worker, developer.get('owner_assignment_ref'), policy_task)
        if not decision.allowed:
            _refuse_unclaimed(decision.reason_code, repair_task_id)
        # #2475 F2: this dispatch's lock is taken before the claim is visible. The
        # repair Task ID is new, so nobody else holds its lock and this cannot
        # wait; a replay of the claim then waits for this dispatch to finish.
        try:
            dispatch_guard = _repair_dispatch_guard(store, repair_task_id)
        except LLMToolError:
            _refuse_unclaimed('REPAIR_CLAIM_UNAVAILABLE', repair_task_id)
        try:
            store.compare_and_update(
                task_id, expected={'record_sha256': task['record_sha256']},
                review_repair_claims=next_claims)
        except Exception:
            dispatch_guard.close()
            _refuse_unclaimed('REPAIR_CLAIM_UNAVAILABLE', repair_task_id)
    finally:
        release_chain()

    # The provider is called only after the claim CAS succeeded.
    try:
        result = _invoke_claimed_adapter(
            store=store, task_id=task_id, claim=claim, repair_task=repair_task,
            developer=developer, packet=packet, executor_task=executor_task,
            profile=provider_profile, dispatch_adapter=dispatch_adapter,
            delegate_callable=delegate_callable,
            high_debug_task=high_debug_task,
            high_debug_ref=high_debug_ref,
            high_debug_family=high_debug_family)
    finally:
        dispatch_guard.close()
    claim['state'] = 'DISPATCHED'
    response = {'result': result, 'attempt': claim['attempt'],
                'repair_task_id': repair_task['task_id'],
                'claim': claim,
                'next_action': f"review-dispatch --version {packet_version + 1}"}
    if auto_review:
        try:
            published_head = _current_d6_head(task, executor_task, current_head)
        except Exception:
            published_head = packet['failed_head']
        if (type(published_head) is str and
                re.fullmatch(r'[0-9a-f]{40}', published_head) is not None and
                published_head != packet['failed_head']):
            dispatch = review_dispatcher
            if dispatch is None:
                from .review_dispatch import dispatch_review
                dispatch = dispatch_review
            response['review'] = dispatch(
                store=store, task_id=task_id, executor_task=executor_task,
                pr_number=pr_number, version=packet_version + 1)
        else:
            response['review'] = {'state': 'NOT_READY', 'reason': 'REPAIR_NOT_PUBLISHED'}
    return response
