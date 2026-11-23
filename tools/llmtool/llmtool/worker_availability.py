"""#1816 part 2 and #2622: worker quota and Developer route-failure layers.

classify() reads only developer.execution.provider_evidence, the route's exact
quota/rate codes and the worker key (plus, for a legacy record without the
field, its CLI_REPORTED usage). It never reads a state, an outcome or an
error-code family. observe() turns one outcome into one TaskStore observation:
a quota-layer result (#1816) and, for AGY and OpenCode only, a route-failure
streak (#2622). fresh_quota_fail() reads the quota layer back for the delegate
gate; route_pause_code() reads the route layer there and in the repair path.
route_pause_refusal() and quota_refusal() return the pause message (the code
plus the resume command) so a gate that raises the value carries both.

#2622: AGY and OpenCode pause after route_failure_pause_after consecutive
route-level failures. A route-level failure is a failure of the route itself
(CLI exit/startup, runtime, home/config, login/authorization, provider/service
unavailable), never a Task-level outcome, and never a quota/free-limit/rate
code. A Developer result that reached the route (responded or output rejected)
resets the streak before a pause. Once paused, only the explicit PM command
`llmtool worker-route-resume <worker>` clears it.

Observations are evidence only. They never write worker-dispatch.json, never
re-enable a PAUSED worker and never grant dispatch authority: Owner policy is
always decided first and separately. One success proves only that request: a
PASS is fresh for the same bounded window as any other observation (30 min; a
provider reset only shortens it). A policy that cannot be read never creates a
new pause and never clears one: an existing pause stays until the PM resumes
the route, and no route is blocked merely because policy.json is unreadable.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re

from .errors import LLMToolError
from . import repair_policy
from . import worker_dispatch

AVAILABLE = 'AVAILABLE'
QUOTA_EXHAUSTED = 'QUOTA_EXHAUSTED'
RATE_LIMITED = 'RATE_LIMITED'
QUOTA_LAYER = 'quota'
REFUSAL_CODE = 'WORKER_QUOTA_UNAVAILABLE'
FRESH_FOR = timedelta(minutes=30)
MAX_RESET_WINDOW = timedelta(days=30)  # TaskStore WORKER_AVAILABILITY_MAX_AGE_DAYS
# codex-developer is PAUSED and company-gw is retired: never written.
NO_WRITE_WORKERS = frozenset(('codex-developer', 'company-gw'))

_BASES = {'claude': 'CLI_USAGE', 'opencode': 'STEP_USAGE', 'agy': 'AGY_STATUS',
          'gw-agent': 'GATEWAY_USAGE'}
_CONDITIONS = frozenset((QUOTA_EXHAUSTED, RATE_LIMITED))
# Exact codes each route stores; no prefix or suffix matching.
_ROUTE_CODES = {
    'claude': {'CLAUDE_QUOTA_EXHAUSTED': QUOTA_EXHAUSTED},
    'agy': {'AGY_QUOTA_EXHAUSTED': QUOTA_EXHAUSTED},
    'gw-agent': {'DEVELOPER_GW_AGENT_QUOTA_EXHAUSTED': QUOTA_EXHAUSTED,
                 'DEVELOPER_GW_AGENT_QUOTA_EXHAUSTED_HTTP_429': QUOTA_EXHAUSTED},
    'opencode': {'OPENCODE_FREE_LIMIT': QUOTA_EXHAUSTED,
                 'OPENCODE_RATE_LIMITED': RATE_LIMITED},
}
# Legacy proof (records without provider_evidence): CLI_REPORTED usage with
# tokens > 0, for these routes only. Every legacy AGY record gives None.
_LEGACY_USAGE_ROUTES = frozenset(('claude', 'opencode', 'gw-agent'))
_TOKEN_FIELDS = ('total_tokens', 'prompt_tokens', 'completion_tokens',
                 'input_tokens', 'output_tokens')

# #2622: the two Developer routes that pause after consecutive route failures.
# The keys are dispatch worker keys, the same keys the availability record uses.
ROUTE_PAUSE_WORKERS = frozenset(('agy', 'opencode-developer'))
ROUTE_PAUSE_PROFILES = frozenset(('agy', 'opencode'))
ROUTE_FAILURE_LAYER = 'runtime'
ROUTE_PAUSE_REFUSAL_CODE = 'WORKER_ROUTE_PAUSED'
ROUTE_PAUSE_RESUME_COMMAND = 'llmtool worker-route-resume'
ROUTE_FAILURE_COUNT_MAX = 10000
# A route-level failure is a failure of the route itself: CLI exit or startup,
# runtime, home or config, login or authorization, or provider/service
# unavailable. Each route lists only codes that exist on main, with each
# code's source. There is no suffix composition and no fallback word match, so
# a Task-level outcome can never enter the streak by accident.
#   AGY (developer_routes/agy.py): AGY_PROFILE_INVALID (:41), AGY_ENV_INVALID
#        (:56), AGY_CONFIG_INVALID (:72). AGY_EXIT_NONZERO (task_store.py:3402)
#        may also be a work error, so it counts only when the record states its
#        exit diagnostic (blocked_reason 'exit_code=...').
#   OpenCode (opencode_direct_developer.py unless noted): OPENCODE_AUTH_REQUIRED
#        (:377), OPENCODE_PROVIDER_FAILED (:407 and :1192),
#        OPENCODE_RUNTIME_UNAVAILABLE (:867, :897, :966, :1031),
#        OPENCODE_HOME_UNAVAILABLE (:677), OPENCODE_CONFIG_UNAVAILABLE (:997),
#        OPENCODE_VERSION_UNSUPPORTED (:901), and OPENCODE_RUNTIME_INVALID
#        (opencode_foreground_host.py:384).
# OPENCODE_HOST_TERMINATED does not exist in the read route code, so it is not
# listed or counted. OPENCODE_FREE_LIMIT is quota and never counts.
_ROUTE_FAILURE_CODES = {
    'agy': frozenset((
        'AGY_PROFILE_INVALID', 'AGY_ENV_INVALID', 'AGY_CONFIG_INVALID',
        'AGY_EXIT_NONZERO')),
    'opencode': frozenset((
        'OPENCODE_AUTH_REQUIRED', 'OPENCODE_PROVIDER_FAILED',
        'OPENCODE_RUNTIME_INVALID', 'OPENCODE_RUNTIME_UNAVAILABLE',
        'OPENCODE_HOME_UNAVAILABLE', 'OPENCODE_CONFIG_UNAVAILABLE',
        'OPENCODE_VERSION_UNSUPPORTED')),
}
# Task-level outcomes never count, and neither do the quota-family codes.
# This list is explicit as well and names only codes that exist on main;
# membership here always wins over the failure list above.
_ROUTE_NEVER_COUNT_CODES = frozenset((
    'TOOL_DENIED',
    'AGY_REQUEST_INVALID', 'AGY_OUTPUT_INVALID', 'AGY_QUOTA_EXHAUSTED',
    'OPENCODE_OUTPUT_INVALID', 'OPENCODE_OUTPUT_TOO_LARGE',
    'OPENCODE_OUTPUT_INCOMPLETE', 'OPENCODE_OUTPUT_TRUNCATED',
    'OPENCODE_STEPS_EXHAUSTED', 'OPENCODE_TIMEOUT', 'OPENCODE_SCOPE_INVALID',
    'OPENCODE_D6_BINDING_CHANGED', 'OPENCODE_SOURCE_INVALID',
    'OPENCODE_SOURCE_TOO_LARGE', 'OPENCODE_SOURCE_CHANGED',
    'OPENCODE_SOURCE_UNAVAILABLE', 'OPENCODE_PROMPT_INVALID',
    'OPENCODE_PROMPT_SENSITIVE', 'OPENCODE_PROMPT_TOO_LARGE',
    'OPENCODE_PROMPT_DELIVERY_INCOMPLETE', 'OPENCODE_MODEL_INVALID',
    'OPENCODE_FREE_LIMIT', 'OPENCODE_RATE_LIMITED', 'OPENCODE_BUDGET_INVALID',
    'OPENCODE_STEPS_INVALID', 'OPENCODE_DELIVERY_CALLBACK_FAILED',
    'OPENCODE_LAUNCH_RECORD_REJECTED', 'OPENCODE_SPAWN_AUTHORIZATION_REFUSED',
))
# A Developer result reached the route: reset the streak before a pause.
_ROUTE_RESULT_STATES = frozenset(('DEVELOPER_RESPONDED', 'DEVELOPER_OUTPUT_REJECTED'))
# Keep a pause inside the store's 30-day freshness bound; the pause itself is a
# plain boolean observation field, so an expired entry still reads as paused.
ROUTE_PAUSE_FRESH_FOR = timedelta(days=29)
# The count and the pause are ordinary TaskStore worker-observation fields
# (route_failure_count and route_paused). evidence_sha256 stays a real digest.


def _profile(record):
    developer = record.get('developer') if type(record) is dict else None
    if type(developer) is not dict:
        return None, None
    profile = developer.get('provider_profile')
    return (profile if type(profile) is str else None), developer


def worker_key(record):
    """The dispatch worker key of a Task record's Developer, or None."""
    profile, _developer = _profile(record)
    return worker_dispatch.worker_key_for(profile)


def _tokens_reported(usage):
    return type(usage) is dict and any(
        type(usage.get(field)) is int and usage[field] > 0 for field in _TOKEN_FIELDS)


def classify(record):
    """Return AVAILABLE, QUOTA_EXHAUSTED, RATE_LIMITED or None for one Task record."""
    profile, developer = _profile(record)
    if profile not in _BASES:
        return None  # codex, company-gw and unknown routes never classify
    execution = developer.get('execution')
    execution = execution if type(execution) is dict else {}
    evidence = execution.get('provider_evidence')
    if (type(evidence) is dict and set(evidence) == {'condition'} and
            type(evidence['condition']) is str and evidence['condition'] in _CONDITIONS):
        return evidence['condition']
    code = developer.get('error_code')
    if type(code) is str and code in _ROUTE_CODES[profile]:
        return _ROUTE_CODES[profile][code]
    if 'provider_evidence' in execution:
        if (type(evidence) is dict and set(evidence) == {'basis', 'accepted'} and
                evidence['accepted'] is True and evidence['basis'] == _BASES[profile]):
            return AVAILABLE
        return None
    if (profile in _LEGACY_USAGE_ROUTES and
            execution.get('usage_source') == 'CLI_REPORTED' and
            _tokens_reported(developer.get('usage'))):
        return AVAILABLE
    return None


def _stamp(value):
    return value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _reset_time(reset_at, observed):
    """A provider-reported reset time as a UTC datetime inside the bound, else None."""
    if type(reset_at) is str:
        try:
            reset_at = datetime.strptime(reset_at, '%Y-%m-%dT%H:%M:%SZ').replace(
                tzinfo=timezone.utc)
        except ValueError:
            return None
    if type(reset_at) is not datetime or reset_at.tzinfo is None:
        return None
    reset_at = reset_at.astimezone(timezone.utc).replace(microsecond=0)
    if not observed < reset_at <= observed + MAX_RESET_WINDOW:
        return None
    return reset_at


def route_failure(record):
    """True when one Developer record is a route-level failure (#2622).

    Only a literal code in that route's list counts. Task-level outcomes,
    spec/test failures, empty results, output rejected and the quota/free-limit/
    rate-limit codes are never counted. AGY_EXIT_NONZERO is ambiguous, so it
    counts only with the stated exit diagnostic, never for a bare work failure.
    """
    profile, developer = _profile(record)
    if profile not in ROUTE_PAUSE_PROFILES or type(developer) is not dict:
        return False
    code = developer.get('error_code')
    if type(code) is not str:
        return False
    if developer.get('state') in _ROUTE_RESULT_STATES:
        return False
    if code in _ROUTE_NEVER_COUNT_CODES:
        return False
    if code not in _ROUTE_FAILURE_CODES[profile]:
        return False
    if code == 'AGY_EXIT_NONZERO':
        reason = developer.get('blocked_reason')
        return type(reason) is str and reason.startswith('exit_code=')
    return True


def route_pause_after():
    """The configured consecutive-failure limit, or None when policy is unreadable.

    None keeps an existing pause and creates no new one: an unreadable policy
    must neither silently unpause a route nor block every route permanently.
    """
    from . import repair_policy
    try:
        return repair_policy.route_failure_pause_after()
    except LLMToolError:
        return None


def route_pause_resume_step(worker):
    """The explicit PM command that clears this route's pause."""
    normalized = {'opencode': 'opencode-developer'}.get(worker, worker)
    return ROUTE_PAUSE_RESUME_COMMAND + ' ' + normalized


def route_pause_message(worker):
    """The refusal message; it names the resume step (A2)."""
    return ROUTE_PAUSE_REFUSAL_CODE + ': run ' + route_pause_resume_step(worker)


def _route_count_evidence(worker, count, *, paused=False):
    """A real sha256 for the entry; count and pause are separate fields."""
    return hashlib.sha256(
        f'route-failure:{worker}:{int(count)}:{int(bool(paused))}'.encode('utf-8')
    ).hexdigest()


def _route_entry(store, worker):
    reader = getattr(store, 'get_worker_observations', None)
    if not callable(reader):
        return None
    try:
        view = reader()
        entry = view[worker][ROUTE_FAILURE_LAYER]
    except Exception:
        return None
    return entry if type(entry) is dict else None


def _route_failure_count(worker, entry):
    """The stored consecutive failure count; an expired pause still counts."""
    if type(entry) is not dict or entry.get('status') not in ('FAIL', 'UNKNOWN'):
        return 0
    count = entry.get('route_failure_count')
    return count if (type(count) is int and
                     0 <= count <= ROUTE_FAILURE_COUNT_MAX) else 0


def _route_pause_count(worker, entry):
    """The pause count, or 0 when the route is not paused.

    The pause is the plain route_paused observation field. It is independent of
    the status, so the store reading an old entry as UNKNOWN at the freshness
    bound never clears it; only the explicit resume command clears it.
    """
    if type(entry) is not dict or entry.get('status') not in ('FAIL', 'UNKNOWN'):
        return 0
    if entry.get('route_paused') is not True:
        return 0
    return _route_failure_count(worker, entry)


def route_pause_code(store, worker):
    """ROUTE_PAUSE_REFUSAL_CODE while the worker's route is paused, else None."""
    normalized = {'opencode': 'opencode-developer'}.get(worker, worker)
    if normalized not in ROUTE_PAUSE_WORKERS:
        return None
    if _route_pause_count(normalized, _route_entry(store, normalized)):
        return ROUTE_PAUSE_REFUSAL_CODE
    return None


def route_pause_refusal(store, worker):
    """The pause refusal message while paused, else None.

    The message carries the definite pause code and the explicit PM resume
    command, so a dispatch gate that raises the returned value gives the PM
    both without the task submission CLI module changing.
    """
    if route_pause_code(store, worker) is None:
        return None
    return route_pause_message(worker)


def _write_route_observation(store, worker, status, observed, fresh_until, evidence,
                             count, paused, source='LOCAL_OBSERVATION'):
    store.record_worker_observation(
        worker_key=worker, layer=ROUTE_FAILURE_LAYER, status=status,
        observed_at=_stamp(observed), fresh_until=_stamp(fresh_until),
        source=source, evidence_sha256=evidence,
        route_failure_count=count, route_paused=paused)


def _observe_route(store, record, worker, observed):
    """Record one route outcome; never clear a pause (only resume does)."""
    if worker not in ROUTE_PAUSE_WORKERS:
        return None
    entry = _route_entry(store, worker)
    if _route_pause_count(worker, entry):
        return 'PAUSED'
    developer = record.get('developer') if type(record) is dict else None
    if route_failure(record):
        count = _route_failure_count(worker, entry) + 1
        limit = route_pause_after()
        paused = limit is not None and count >= limit
        _write_route_observation(
            store, worker, 'FAIL' if paused else 'UNKNOWN', observed,
            observed + (ROUTE_PAUSE_FRESH_FOR if paused else FRESH_FOR),
            _route_count_evidence(worker, count, paused=paused), count, paused)
        return 'PAUSE' if paused else 'COUNT'
    if (type(developer) is dict and developer.get('state') in _ROUTE_RESULT_STATES):
        if _route_failure_count(worker, entry):
            _write_route_observation(
                store, worker, 'PASS', observed, observed + FRESH_FOR,
                _route_count_evidence(worker, 0), 0, False)
        return 'RESET'
    return None


def resume_route(store, worker, *, now=None):
    """Clear one AGY/OpenCode route pause from the explicit PM resume command.

    The resume is recorded in the existing worker availability record, never in
    worker-dispatch.json. It writes a PASS entry with the plain integer 0, so
    the next route failure starts a new streak at 1.
    """
    normalized = {'opencode': 'opencode-developer'}.get(worker, worker)
    if type(normalized) is not str or normalized not in ROUTE_PAUSE_WORKERS:
        raise LLMToolError('WORKER_ROUTE_RESUME_WORKER_INVALID')
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(
        microsecond=0)
    before = _route_entry(store, normalized)
    was_paused = bool(_route_pause_count(normalized, before))
    _write_route_observation(
        store, normalized, 'PASS', observed, observed + FRESH_FOR,
        _route_count_evidence(normalized, 0), 0, False,
        source='AUTHORIZED_OPERATION')
    return {'worker': normalized, 'was_paused': was_paused, 'paused': False,
            'resumed_at': _stamp(observed),
            'next_action': route_pause_resume_step(normalized)}


def observe(store, record, *, now=None, reset_at=None):
    """Record one outcome as quota and route observations.

    Returns the quota result (AVAILABLE, QUOTA_EXHAUSTED or RATE_LIMITED), else
    the route outcome ('PAUSE', 'COUNT', 'RESET', 'PAUSED') or None.

    FAIL for QUOTA_EXHAUSTED/RATE_LIMITED, PASS for AVAILABLE, nothing for None.
    fresh_until is min(reset, observed + FRESH_FOR) for a FAIL with a valid
    provider reset, else observed + FRESH_FOR (30 min; a provider reset only
    shortens it). PASS stays observed + FRESH_FOR. PAUSED codex-developer and
    retired company-gw are never written.

    #2622: AGY and OpenCode also record their route-failure streak. A result
    that reached the route resets it; route-level failures increment it and
    pause at route_failure_pause_after. A pause is never cleared here.
    """
    worker = worker_key(record)
    result = classify(record)
    if worker is None or worker in NO_WRITE_WORKERS:
        return None
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(
        microsecond=0)
    if result is not None:
        status = 'PASS' if result == AVAILABLE else 'FAIL'
        reset = _reset_time(reset_at, observed) if status == 'FAIL' else None
        fresh_until = (min(reset, observed + FRESH_FOR) if reset is not None
                       else observed + FRESH_FOR)
        developer = record['developer']
        digest = hashlib.sha256(json.dumps({
            'task_id': record.get('task_id'),
            'assignment_id': developer.get('assignment_id'),
            'worker_key': worker, 'result': result,
        }, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
        store.record_worker_observation(
            worker_key=worker, layer=QUOTA_LAYER, status=status,
            observed_at=_stamp(observed), fresh_until=_stamp(fresh_until),
            source='LOCAL_OBSERVATION', evidence_sha256=digest)
    route_result = _observe_route(store, record, worker, observed)
    return result if result is not None else route_result


_SLUG_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$')
_OWNER_RECOVERY_MARKER = '<!-- activity: type=pm_progress actor=PM target=Owner status=ANSWERED -->'


def _relay_url_re(slug):
    return re.compile(
        r'https://github\.com/' + re.escape(slug) + r'/issues/([1-9][0-9]*)#issuecomment-([1-9][0-9]*)'
    )


def _parse_strict_utc(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    if parsed.strftime('%Y-%m-%dT%H:%M:%SZ') != value:
        return None
    return parsed


def record_owner_recovery(store, worker, owner_relay_url, *, now=None, github_get=None):
    """Record an Owner recovery observation clearing a worker quota refusal.

    Clears a fresh quota FAIL by recording an AUTHORIZED_OPERATION PASS in the
    TaskStore quota layer, verified against an Owner relay comment URL.
    Observations are evidence only: this never reads or writes
    worker-dispatch.json and never overrides PAUSED. All agents share the same
    GitHub login (#926/S2), so the marker check is an accident guard, not proof
    of Owner authorship.
    """
    if worker not in worker_dispatch.WORKERS or worker in NO_WRITE_WORKERS:
        raise LLMToolError('WORKER_QUOTA_RECOVERY_WORKER_INVALID')
    if not isinstance(owner_relay_url, str):
        raise LLMToolError('WORKER_QUOTA_RECOVERY_REF_INVALID')
    try:
        from . import task_completion
        slug = task_completion._trusted_slug()
    except Exception:
        raise LLMToolError('WORKER_QUOTA_RECOVERY_REF_INVALID') from None
    if not isinstance(slug, str) or not _SLUG_RE.match(slug):
        raise LLMToolError('WORKER_QUOTA_RECOVERY_REF_INVALID')
    match = _relay_url_re(slug).fullmatch(owner_relay_url)
    if match is None:
        raise LLMToolError('WORKER_QUOTA_RECOVERY_REF_INVALID')
    issue_number = match.group(1)
    comment_id = match.group(2)

    observations = store.get_worker_observations()
    if worker not in observations:
        raise LLMToolError('WORKER_QUOTA_RECOVERY_WORKER_INVALID')
    before = observations[worker][QUOTA_LAYER]
    if before.get('status') != 'FAIL':
        raise LLMToolError('WORKER_QUOTA_RECOVERY_NO_FAIL')

    if github_get is None:
        github_get = task_completion._github_get

    try:
        comment = github_get(f'issues/comments/{comment_id}')
    except Exception:
        raise LLMToolError('WORKER_QUOTA_RECOVERY_UNAVAILABLE') from None

    if not isinstance(comment, dict):
        raise LLMToolError('WORKER_QUOTA_RECOVERY_UNAVAILABLE')

    for field in ('body', 'html_url', 'issue_url', 'created_at', 'updated_at'):
        if not isinstance(comment.get(field), str):
            raise LLMToolError('WORKER_QUOTA_RECOVERY_UNAVAILABLE')

    if comment['html_url'] != owner_relay_url:
        raise LLMToolError('WORKER_QUOTA_RECOVERY_REF_INVALID')

    if comment['issue_url'] != 'https://api.github.com/repos/' + slug + '/issues/' + issue_number:
        raise LLMToolError('WORKER_QUOTA_RECOVERY_REF_INVALID')

    if _OWNER_RECOVERY_MARKER not in comment['body'].splitlines():
        raise LLMToolError('WORKER_QUOTA_RECOVERY_MARKER')

    worker_pattern = r'(?<![A-Za-z0-9_-])' + re.escape(worker) + r'(?![A-Za-z0-9_-])'
    if not re.search(worker_pattern, comment['body']):
        raise LLMToolError('WORKER_QUOTA_RECOVERY_WORKER_MISSING')

    created_at_dt = _parse_strict_utc(comment['created_at'])
    updated_at_dt = _parse_strict_utc(comment['updated_at'])
    if created_at_dt is None or updated_at_dt is None:
        raise LLMToolError('WORKER_QUOTA_RECOVERY_UNAVAILABLE')

    if comment['updated_at'] != comment['created_at']:
        raise LLMToolError('WORKER_QUOTA_RECOVERY_EDITED')

    fail_observed_dt = _parse_strict_utc(before.get('observed_at'))
    if fail_observed_dt is None or created_at_dt <= fail_observed_dt:
        raise LLMToolError('WORKER_QUOTA_RECOVERY_STALE')

    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(
        microsecond=0)
    evidence_sha256 = hashlib.sha256(owner_relay_url.encode('utf-8')).hexdigest()
    try:
        store.record_worker_observation(
            worker_key=worker, layer=QUOTA_LAYER, status='PASS',
            observed_at=_stamp(current_time), fresh_until=_stamp(current_time + FRESH_FOR),
            source='AUTHORIZED_OPERATION', evidence_sha256=evidence_sha256,
            expected=before)
    except LLMToolError as exc:
        if str(exc) == 'WORKER_AVAILABILITY_CHANGED' or getattr(exc, 'task_store_error_code', None) == 'WORKER_AVAILABILITY_CHANGED':
            raise LLMToolError('WORKER_QUOTA_RECOVERY_CHANGED') from None
        raise
    after = store.get_worker_observations()[worker][QUOTA_LAYER]
    return {
        'worker': worker,
        'owner_relay_url': owner_relay_url,
        'evidence_sha256': evidence_sha256,
        'before': before,
        'after': after,
    }


def _fresh_availability(store, worker):
    """One read of the route and quota layers: (paused, quota_fail).

    A missing or unreadable reader is (False, False); it never refuses.
    """
    if type(worker) is not str or worker in NO_WRITE_WORKERS:
        return False, False
    reader = getattr(store, 'get_worker_observations', None)
    if not callable(reader):
        return False, False
    try:
        view = reader()
    except Exception:
        return False, False
    paused = False
    route_worker = {'opencode': 'opencode-developer'}.get(worker, worker)
    if route_worker in ROUTE_PAUSE_WORKERS:
        try:
            entry = view[route_worker][ROUTE_FAILURE_LAYER]
        except Exception:
            entry = None
        paused = bool(_route_pause_count(
            route_worker, entry if type(entry) is dict else None))
    try:
        quota_fail = view[worker][QUOTA_LAYER]['status'] == 'FAIL'
    except Exception:
        quota_fail = False
    return paused, quota_fail


def fresh_quota_fail(store, worker):
    """Whether the worker's quota layer holds a fresh FAIL; refuses a paused route.

    Expired, absent or unreadable observations read UNKNOWN and never refuse;
    a store without the observation reader has none. #2622: a paused AGY or
    OpenCode route is refused here too, because this is the delegate gate's
    read of the worker availability record. The refusal is raised (not returned)
    so the message can name the explicit PM resume step. One read serves both
    layers.
    """
    paused, quota_fail = _fresh_availability(store, worker)
    if paused:
        error = LLMToolError(route_pause_message(worker))
        error.route_pause_code = ROUTE_PAUSE_REFUSAL_CODE
        error.route_pause_resume_step = route_pause_resume_step(worker)
        raise error from None
    return quota_fail


def quota_refusal(store, worker, *, owner_assignment_ref=None, policy_task_id=None):
    """REFUSAL_CODE on a fresh quota FAIL, a pause refusal message on a pause.

    Owner policy comes first: when the Owner policy refuses the worker this
    returns None, so the route's own Owner refusal is the one reported. The
    pause case returns the message (it starts with ROUTE_PAUSE_REFUSAL_CODE and
    names the resume command), so the delegate and reassign gates that raise
    the returned value carry the code and the resume step.
    """
    paused, quota_fail = _fresh_availability(store, worker)
    if not paused and not quota_fail:
        return None
    decision = worker_dispatch.evaluate(worker, owner_assignment_ref, policy_task_id)
    if not decision.allowed:
        return None
    return route_pause_message(worker) if paused else REFUSAL_CODE


# #2367: gw-agent availability. The probe reuses the existing worker
# availability sidecar (no new state system); delegation and probing are both
# refused while a hold is active. Failure classes are kept apart:
#   runtime        infrastructure pause, cleared by a passing probe
#   endpoint       rate limit (HTTP 429), cleared by Retry-After or a probe
#   inference      model capability, stepped down then paused at the
#                  lowest tier (never cleared by a probe, per S1)
#   task_acceptance security/authorization hold (never auto-recovered)
GW_AGENT_WORKER = 'gw-agent'
GW_AGENT_INFRA_LAYER = 'runtime'
GW_AGENT_RATE_LAYER = 'endpoint'
GW_AGENT_MODEL_LAYER = 'inference'
GW_AGENT_SECURITY_LAYER = 'task_acceptance'
GW_AGENT_PROBE_LAYERS = ('runtime', 'endpoint', 'authentication', 'inference')
GW_AGENT_FAILURE_CLASSES = frozenset((
    'infrastructure', 'rate_limit', 'model', 'security', 'unknown'))
GW_AGENT_REFUSALS = {
    'infrastructure': 'WORKER_GW_AGENT_UNAVAILABLE',
    'rate_limit': 'WORKER_GW_AGENT_RATE_LIMITED',
    'model': 'WORKER_GW_AGENT_MODEL_PAUSED',
    'security': 'WORKER_GW_AGENT_SECURITY_HOLD',
    'unrecorded': 'WORKER_GW_AGENT_HOLD_UNRECORDED',
}
GW_AGENT_HOLD_FRESH_FOR = timedelta(days=29)  # under WORKER_AVAILABILITY_MAX_AGE_DAYS
_MODEL_STEP_TWO = hashlib.sha256(b'gw-agent-model-failure:2').hexdigest()
_MODEL_STEP_ONE = hashlib.sha256(b'gw-agent-model-failure:1').hexdigest()
_MODEL_PAUSE = hashlib.sha256(b'gw-agent-model-pause').hexdigest()
_GW_HOLD_WRITE_FAILED = {}


def _model_step_digest(step):
    return hashlib.sha256(f'gw-agent-model-step:{step}'.encode('utf-8')).hexdigest()


def _prior_model_step(prior, pause_after):
    if type(prior) is not dict:
        return 0
    if prior.get('status') == 'FAIL':
        return pause_after
    digest = prior.get('evidence_sha256')
    if digest == _MODEL_STEP_ONE:
        return 1
    for step in range(1, max(pause_after, 32)):
        if digest == _model_step_digest(step):
            return step
    return 0


def _store_key(store):
    root = getattr(store, 'root', None)
    return str(root) if root is not None else ('id', id(store))


def _observation_view(store):
    reader = getattr(store, 'get_worker_observations', None)
    if not callable(reader):
        return None
    try:
        return reader()
    except Exception:
        return 'UNAVAILABLE'


def _observation(view, layer):
    try:
        return view[GW_AGENT_WORKER][layer]
    except Exception:
        return None


def _active_fail(view, layer, now=None):
    entry = _observation(view, layer)
    if type(entry) is not dict or entry.get('status') != 'FAIL':
        return False
    if now is None:
        return True
    # The stored view marks freshness at read time; a caller's bounded time
    # (a probe or a later admission) must also see an expired FAIL as inactive.
    try:
        return (datetime.strptime(entry.get('fresh_until'), '%Y-%m-%dT%H:%M:%SZ')
                .replace(tzinfo=timezone.utc) > now)
    except (TypeError, ValueError):
        return True


def _failure_class(value):
    if type(value) is not str:
        return 'unknown'
    code = value.upper()
    for prefix in ('DEVELOPER_GW_AGENT_', 'GW_AGENT_'):
        if code.startswith(prefix):
            code = code[len(prefix):]
            break
    if ('429' in code or 'RATE' in code or 'QUOTA' in code):
        return 'rate_limit'
    if any(token in code for token in (
            'AUTH', 'CREDENTIAL', 'AUDIT', 'SECURITY', 'FORBIDDEN',
            'UNAUTHORIZED', 'DISPATCH_DENIED', 'HTTP_401', 'HTTP_403')):
        return 'security'
    if any(token in code for token in (
            'HTTP_5', 'TIMEOUT', 'CONNECTION', 'NETWORK', 'TRANSPORT',
            'GATEWAY_STALE', 'UNAVAILABLE')):
        return 'infrastructure'
    if any(token in code for token in (
            'MODEL', 'PROFILE', 'CAPABILITY', 'SERVED', 'UNVERIFIED')):
        return 'model'
    return 'unknown'


def classify_gw_agent_failure(error_code=None, *, gateway_code=None,
                              exception=None, status_code=None):
    """Return infrastructure, rate_limit, model, security or unknown.

    The class comes only from fixed route/gateway codes, an HTTP status or the
    exception type; provider text is never inspected.
    """
    if type(status_code) is int:
        if status_code == 429:
            return 'rate_limit'
        if status_code in (401, 403):
            return 'security'
        if status_code >= 500:
            return 'infrastructure'
        if 400 <= status_code < 500:
            return 'model'
    if exception is not None:
        if isinstance(exception, PermissionError):
            return 'security'
        if isinstance(exception, (TimeoutError, ConnectionError, OSError)):
            return 'infrastructure'
    for value in (gateway_code, error_code):
        code = _failure_class(value)
        if code != 'unknown':
            return code
    return 'unknown'


def _utc_now(now):
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(
        microsecond=0)


def _hold_fresh_until(now):
    return now + GW_AGENT_HOLD_FRESH_FOR


def _retry_seconds(value, now):
    """A bounded Retry-After value (seconds or HTTP date); None when unusable."""
    if type(value) in (int, float) and not isinstance(value, bool):
        seconds = value
    elif type(value) is str:
        try:
            seconds = float(value.strip())
        except ValueError:
            try:
                from email.utils import parsedate_to_datetime
                parsed = parsedate_to_datetime(value)
                if parsed is None or parsed.tzinfo is None:
                    return None
                seconds = (parsed - now).total_seconds()
            except Exception:
                return None
    else:
        return None
    if seconds < 0:
        return None
    return min(seconds, MAX_RESET_WINDOW.total_seconds())


def _write_observation(store, layer, status, observed, fresh_until, evidence):
    store.record_worker_observation(
        worker_key=GW_AGENT_WORKER, layer=layer, status=status,
        observed_at=_stamp(observed), fresh_until=_stamp(fresh_until),
        source='LOCAL_OBSERVATION', evidence_sha256=evidence)


def record_gw_agent_failure(store, failure_class=None, *, error_code=None,
                            gateway_code=None, exception=None, status_code=None,
                            now=None, retry_after=None, retry_after_seconds=None,
                            task_id=None, tier='lowest'):
    """Record one gw-agent failure in the existing worker availability record.

    infrastructure pauses dispatch until a probe passes. rate_limit honours
    Retry-After. security stays held. model steps down until the configured
    consecutive-failure threshold at the lowest tier (S1), then pauses.
    unknown writes nothing.
    A hold that cannot be recorded marks the store so a later admission is
    refused until a hold or a passing probe is durably recorded (S2).
    """
    if failure_class not in GW_AGENT_FAILURE_CLASSES:
        failure_class = classify_gw_agent_failure(
            error_code if failure_class is None else failure_class,
            gateway_code=gateway_code, exception=exception, status_code=status_code)
    now = _utc_now(now)
    key = _store_key(store)
    if failure_class == 'unknown':
        return {'worker': GW_AGENT_WORKER, 'failure_class': 'unknown',
                'action': 'NONE', 'at': _stamp(now)}
    retry = retry_after_seconds if retry_after_seconds is not None else retry_after
    evidence = hashlib.sha256(
        ('gw-agent:' + failure_class + ':' + (task_id or '')).encode('utf-8')).hexdigest()
    if failure_class == 'model':
        pause_after = repair_policy.route_failure_pause_after()
        view = _observation_view(store)
        prior = None if view in (None, 'UNAVAILABLE') else _observation(
            view, GW_AGENT_MODEL_LAYER)
        if type(prior) is dict and prior.get('status') == 'FAIL':
            count = pause_after
        elif tier != 'lowest':
            count = 1
        else:
            count = min(_prior_model_step(prior, pause_after) + 1, pause_after)
        paused = count >= pause_after
        status = 'FAIL' if paused else 'UNKNOWN'
        evidence = _MODEL_PAUSE if paused else _model_step_digest(count)
        layer, fresh = GW_AGENT_MODEL_LAYER, _hold_fresh_until(now)
        action = 'PAUSE' if paused else 'STEP_DOWN'
    elif failure_class == 'security':
        count, layer, fresh = 2, GW_AGENT_SECURITY_LAYER, _hold_fresh_until(now)
        status, action = 'FAIL', 'SECURITY_HOLD'
    elif failure_class == 'infrastructure':
        count, layer, fresh = 2, GW_AGENT_INFRA_LAYER, _hold_fresh_until(now)
        status, action = 'FAIL', 'INFRASTRUCTURE_PAUSE'
    else:
        seconds = _retry_seconds(retry, now)
        count, layer, status, action = 2, GW_AGENT_RATE_LAYER, 'FAIL', 'RATE_LIMITED'
        fresh = now + (timedelta(seconds=seconds) if seconds is not None else FRESH_FOR)
    try:
        _write_observation(store, layer, status, now, fresh, evidence)
        if failure_class == 'security':
            # The auth layer reports the finding; the task_acceptance layer is
            # the hold a probe can never clear (S1).
            _write_observation(store, 'authentication', 'FAIL', now, fresh, evidence)
    except Exception:
        _GW_HOLD_WRITE_FAILED[key] = failure_class
        return {'worker': GW_AGENT_WORKER, 'failure_class': failure_class,
                'action': 'HOLD_WRITE_FAILED', 'at': _stamp(now)}
    _GW_HOLD_WRITE_FAILED.pop(key, None)
    return {'worker': GW_AGENT_WORKER, 'failure_class': failure_class,
            'action': action, 'count': count, 'layer': layer,
            'status': status, 'observed_at': _stamp(now),
            'fresh_until': _stamp(fresh)}


def record_gw_agent_success(store, *, now=None, evidence=None):
    """Record a passing probe: PASS on every probe layer, holds untouched."""
    now = _utc_now(now)
    key = _store_key(store)
    digest = evidence or hashlib.sha256(
        ('gw-agent:probe:pass:' + _stamp(now)).encode('utf-8')).hexdigest()
    fresh = now + FRESH_FOR
    view = _observation_view(store)
    failed = False
    for layer in GW_AGENT_PROBE_LAYERS:
        if layer == GW_AGENT_MODEL_LAYER and _active_fail(view, layer, now):
            continue  # S1: a model pause is never cleared by a probe
        if layer == GW_AGENT_RATE_LAYER and _active_fail(view, layer, now):
            continue  # an unexpired Retry-After is not cleared by a probe
        try:
            _write_observation(store, layer, 'PASS', now, fresh, digest)
        except Exception:
            failed = True
    if failed:
        _GW_HOLD_WRITE_FAILED.setdefault(key, 'infrastructure')
    elif _GW_HOLD_WRITE_FAILED.get(key) not in ('security', 'model'):
        _GW_HOLD_WRITE_FAILED.pop(key, None)
    return not failed


def _task_failure_unresolved(store, now, view, *, classes=None):
    """True when a stored gw-agent failure still needs a hold or passing probe.

    This is the durable half of S2: if recording a hold failed, the failed
    Task's own record is enough to refuse a later admission. A passing probe
    (or a fresh hold) resolves an infrastructure failure. A recorded step-down
    is not a pause; a model pause and every security hold stay unresolved.
    """
    lister = getattr(store, 'task_ids', None)
    if not callable(lister):
        return False
    try:
        task_ids = list(lister())
    except Exception:
        return False
    developers = []
    for task_id in task_ids:
        try:
            record = store.get(task_id)
        except Exception:
            continue
        developer = record.get('developer') if type(record) is dict else None
        if (type(developer) is not dict or
                developer.get('provider_profile') != GW_AGENT_WORKER):
            continue
        try:
            created_at = datetime.strptime(
                developer.get('created_at'), '%Y-%m-%dT%H:%M:%SZ').replace(
                tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        developers.append((developer, created_at))
    # A later successful gw-agent run proves the route worked after an older
    # failure, so that failure no longer needs a hold or probe. Without this,
    # failures recorded before this check existed (already handled at the
    # time) would hold gw-agent for MAX_RESET_WINDOW.
    successes = [created_at for developer, created_at in developers
                 if developer.get('state') == 'DEVELOPER_RESPONDED' and
                 developer.get('error_code') is None]
    latest_success = max(successes) if successes else None
    for developer, created_at in developers:
        failure = classify_gw_agent_failure(developer.get('error_code'))
        if failure == 'unknown' or (classes is not None and failure not in classes):
            continue
        if now - created_at > MAX_RESET_WINDOW:
            continue
        if latest_success is not None and latest_success > created_at:
            continue
        if failure == 'security':
            entry = (None if view in (None, 'UNAVAILABLE')
                     else _observation(view, GW_AGENT_SECURITY_LAYER))
            if type(entry) is not dict or entry.get('status') != 'FAIL':
                return True
            try:
                if (datetime.strptime(entry.get('observed_at'), '%Y-%m-%dT%H:%M:%SZ')
                        .replace(tzinfo=timezone.utc) < created_at):
                    return True
            except (TypeError, ValueError):
                return True
            continue
        if failure == 'model':
            entry = (None if view in (None, 'UNAVAILABLE')
                     else _observation(view, GW_AGENT_MODEL_LAYER))
            if type(entry) is not dict:
                return True
            try:
                if (datetime.strptime(entry.get('observed_at'), '%Y-%m-%dT%H:%M:%SZ')
                        .replace(tzinfo=timezone.utc) < created_at):
                    return True
            except (TypeError, ValueError):
                return True
            if entry.get('status') == 'FAIL':
                continue  # the active-pause check already refused
            if entry.get('evidence_sha256') in (_MODEL_PAUSE, _MODEL_STEP_TWO):
                return True
            continue
        layer = (GW_AGENT_INFRA_LAYER if failure == 'infrastructure'
                 else GW_AGENT_RATE_LAYER)
        entry = None if view in (None, 'UNAVAILABLE') else _observation(view, layer)
        if failure == 'rate_limit':
            reset = entry.get('fresh_until') if type(entry) is dict else None
            if type(reset) is str:
                try:
                    if datetime.strptime(reset, '%Y-%m-%dT%H:%M:%SZ').replace(
                            tzinfo=timezone.utc) <= now:
                        continue
                except ValueError:
                    pass
            if type(entry) is dict and entry.get('status') == 'PASS':
                continue
            return True
        if type(entry) is not dict or entry.get('status') != 'PASS':
            return True
        observed = entry.get('observed_at')
        try:
            if (datetime.strptime(observed, '%Y-%m-%dT%H:%M:%SZ').replace(
                    tzinfo=timezone.utc) < created_at):
                return True
        except (TypeError, ValueError):
            return True
    return False


def gw_agent_refusal(store, *, now=None):
    """The refusal code while gw-agent is held out of dispatch, else None."""
    key = _store_key(store)
    if key in _GW_HOLD_WRITE_FAILED:
        return GW_AGENT_REFUSALS['unrecorded']
    view = _observation_view(store)
    if view is None:
        return None
    if view == 'UNAVAILABLE':
        return 'WORKER_GW_AGENT_AVAILABILITY_UNKNOWN'
    now = _utc_now(now)
    if _active_fail(view, GW_AGENT_SECURITY_LAYER, now):
        return GW_AGENT_REFUSALS['security']
    if _active_fail(view, GW_AGENT_MODEL_LAYER, now):
        return GW_AGENT_REFUSALS['model']
    if _active_fail(view, GW_AGENT_INFRA_LAYER, now):
        return GW_AGENT_REFUSALS['infrastructure']
    if _active_fail(view, GW_AGENT_RATE_LAYER, now):
        return GW_AGENT_REFUSALS['rate_limit']
    if _task_failure_unresolved(store, now, view):
        return GW_AGENT_REFUSALS['unrecorded']
    return None


def _probe_report(status, now, *, provider_call, tcp='UNKNOWN', auth='UNKNOWN',
                  inference='UNKNOWN', reason=None):
    report = {'worker': GW_AGENT_WORKER, 'status': status, 'at': _stamp(now),
              'observed_at': _stamp(now), 'tcp': tcp, 'auth': auth,
              'inference': inference, 'provider_call': provider_call}
    if reason is not None:
        report['reason'] = reason
    return report


def probe_gw_agent(store, *, call=None, now=None):
    """Probe gw-agent through the caller's existing route call.

    One call at most, never a loop. No provider call is made while Owner policy
    refuses gw-agent, while a model/security hold is active (S1), or while an
    unexpired Retry-After holds the rate layer. A passing call records PASS on
    the probe layers and clears an infrastructure pause.
    """
    now = _utc_now(now)
    key = _store_key(store)
    if _GW_HOLD_WRITE_FAILED.get(key) in ('security', 'model'):
        return _probe_report('UNKNOWN', now, provider_call=False,
                             reason=GW_AGENT_REFUSALS['unrecorded'])
    try:
        decision = worker_dispatch.evaluate(GW_AGENT_WORKER)
    except Exception:
        return _probe_report('UNKNOWN', now, provider_call=False,
                             reason='OWNER_POLICY_UNAVAILABLE')
    if not decision.allowed:
        return _probe_report('UNKNOWN', now, provider_call=False,
                             reason=decision.reason_code)
    view = _observation_view(store)
    if view is None:
        return _probe_report('UNKNOWN', now, provider_call=False,
                             reason='AVAILABILITY_STORE_UNAVAILABLE')
    if view == 'UNAVAILABLE':
        return _probe_report('UNKNOWN', now, provider_call=False,
                             reason='AVAILABILITY_UNAVAILABLE')
    if _active_fail(view, GW_AGENT_SECURITY_LAYER, now):
        return _probe_report('FAIL', now, provider_call=False, auth='FAIL',
                             reason=GW_AGENT_REFUSALS['security'])
    if _active_fail(view, GW_AGENT_MODEL_LAYER, now):
        return _probe_report('FAIL', now, provider_call=False, inference='FAIL',
                             reason=GW_AGENT_REFUSALS['model'])
    if _active_fail(view, GW_AGENT_RATE_LAYER, now):
        return _probe_report('UNKNOWN', now, provider_call=False,
                             reason=GW_AGENT_REFUSALS['rate_limit'])
    if _task_failure_unresolved(store, now, view, classes=('security', 'model')):
        return _probe_report('UNKNOWN', now, provider_call=False,
                             reason=GW_AGENT_REFUSALS['unrecorded'])
    if not callable(call):
        return _probe_report('UNKNOWN', now, provider_call=False,
                             reason='PROBE_CALL_UNAVAILABLE')
    try:
        result = call()
    except Exception as exc:
        failure = classify_gw_agent_failure(
            getattr(exc, 'code', None), exception=exc,
            status_code=getattr(exc, 'status_code', None))
        facts = getattr(exc, 'facts', None)
        retry = (getattr(exc, 'retry_after', None) or
                 (facts.get('retry_after') if type(facts) is dict else None) or
                 (facts.get('retry_after_seconds') if type(facts) is dict else None))
        record_gw_agent_failure(
            store, failure, exception=exc, now=now, retry_after=retry)
        inference = ('FAIL' if failure == 'model' else 'UNKNOWN')
        return _probe_report('FAIL', now, provider_call=True,
                             tcp=('FAIL' if failure == 'infrastructure' else 'UNKNOWN'),
                             auth=('FAIL' if failure == 'security' else 'UNKNOWN'),
                             inference=inference, reason=failure)
    if type(result) is dict:
        statuses = {name: (result.get(name) if result.get(name) in
                           ('PASS', 'FAIL', 'UNKNOWN') else 'UNKNOWN')
                    for name in ('tcp', 'auth', 'inference')}
        if result.get('status') in ('PASS', 'FAIL', 'UNKNOWN'):
            statuses = {name: (value if value != 'UNKNOWN' else result['status'])
                        for name, value in statuses.items()}
        if result.get('model_capability') is True and statuses['inference'] == 'FAIL':
            failure = 'model'
        elif statuses['auth'] == 'FAIL':
            failure = 'security'
        elif statuses['tcp'] == 'FAIL':
            failure = 'infrastructure'
        elif statuses['inference'] == 'FAIL':
            failure = ('infrastructure' if result.get('infrastructure') is True
                       else 'model')
        elif 'FAIL' in statuses.values():
            failure = 'infrastructure'
        else:
            failure = None
        retry = result.get('retry_after', result.get('retry_after_seconds'))
    else:
        if result is False:
            statuses = {'tcp': 'FAIL', 'auth': 'UNKNOWN', 'inference': 'UNKNOWN'}
            failure = 'infrastructure'
        else:
            statuses = {'tcp': 'PASS', 'auth': 'PASS', 'inference': 'PASS'}
            failure = None
        retry = None
    if failure is not None:
        record_gw_agent_failure(store, failure, now=now, retry_after=retry)
        return _probe_report('FAIL', now, provider_call=True, reason=failure,
                             **statuses)
    if type(result) is dict and any(value == 'UNKNOWN' for value in statuses.values()):
        return _probe_report('UNKNOWN', now, provider_call=True,
                             reason='PROBE_INCONCLUSIVE', **statuses)
    if not record_gw_agent_success(store, now=now):
        return _probe_report('UNKNOWN', now, provider_call=True,
                             reason='PROBE_RESULT_NOT_RECORDED', **statuses)
    return _probe_report('PASS', now, provider_call=True, **statuses)


def record_gw_agent_hold(store, hold='security', **kwargs):
    """Record one gw-agent hold: a security hold or any failure class."""
    return record_gw_agent_failure(store, hold, **kwargs)


# Compatibility names for callers that describe the same two operations.
gw_agent_probe = probe_gw_agent
gw_agent_availability_refusal = gw_agent_refusal
record_availability_failure = record_gw_agent_failure
record_hold = record_gw_agent_hold
