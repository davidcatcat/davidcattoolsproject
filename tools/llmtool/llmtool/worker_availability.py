"""#1816 part 2: the worker quota layer from Developer outcomes.

classify() reads only developer.execution.provider_evidence, the route's exact
quota/rate codes and the worker key (plus, for a legacy record without the
field, its CLI_REPORTED usage). It never reads a state, an outcome or an
error-code family. observe() turns one outcome into one TaskStore quota-layer
observation; fresh_quota_fail() reads it back for the delegate gate.

Observations are evidence only. They never write worker-dispatch.json, never
re-enable a PAUSED worker and never grant dispatch authority: Owner policy is
always decided first and separately. One success proves only that request: a
PASS is fresh for the same bounded window as any other observation (30 min; a
provider reset only shortens it).

#2367 (Owner 2026-11-19): gw-agent failure classes are kept apart in the same
availability record, one layer each:
- infrastructure (network, gateway, service): an endpoint-layer FAIL that holds
  admission until a gw-agent availability probe passes;
- security or authorization findings: an authentication-layer FAIL that no
  probe or success clears (no automatic recovery);
- model capability (the model ran but the work failed its checks): E9 step-down,
  then a runtime-layer pause after two consecutive failures at the lowest tier,
  which a probe never clears;
- UNKNOWN outcomes: nothing is written and nothing is replayed.
A hold only withholds admission; it never allows what the Owner policy refuses.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re

from .errors import LLMToolError
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


def observe(store, record, *, now=None, reset_at=None):
    """Record one outcome as a quota-layer observation; return the result or None.

    FAIL for QUOTA_EXHAUSTED/RATE_LIMITED, PASS for AVAILABLE, nothing for None.
    fresh_until is min(reset, observed + FRESH_FOR) for a FAIL with a valid
    provider reset, else observed + FRESH_FOR (30 min; a provider reset only
    shortens it). PASS stays observed + FRESH_FOR. PAUSED codex-developer and
    retired company-gw are never written.
    """
    worker = worker_key(record)
    result = classify(record)
    if worker is None or worker in NO_WRITE_WORKERS or result is None:
        return None
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(
        microsecond=0)
    status = 'PASS' if result == AVAILABLE else 'FAIL'
    reset = _reset_time(reset_at, observed) if status == 'FAIL' else None
    fresh_until = min(reset, observed + FRESH_FOR) if reset is not None else observed + FRESH_FOR
    developer = record['developer']
    digest = hashlib.sha256(json.dumps({
        'task_id': record.get('task_id'), 'assignment_id': developer.get('assignment_id'),
        'worker_key': worker, 'result': result,
    }, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
    store.record_worker_observation(
        worker_key=worker, layer=QUOTA_LAYER, status=status,
        observed_at=_stamp(observed), fresh_until=_stamp(fresh_until),
        source='LOCAL_OBSERVATION', evidence_sha256=digest)
    return result


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


def fresh_quota_fail(store, worker):
    """Whether the worker's quota layer holds a fresh FAIL, or gw-agent is held.

    For gw-agent a fresh infrastructure pause, security stop or model-capability
    pause (hold_reason) also counts, so every existing admission point that
    reads this refuses it. Expired, absent or unreadable observations read
    UNKNOWN and never refuse; a store without the observation reader has none.
    """
    if type(worker) is not str or worker in NO_WRITE_WORKERS:
        return False
    reader = getattr(store, 'get_worker_observations', None)
    if not callable(reader):
        return False
    try:
        view = reader()
    except Exception:
        return False
    try:
        if view[worker][QUOTA_LAYER]['status'] == 'FAIL':
            return True
    except Exception:
        pass
    return worker == GW_AGENT and _hold_from_view(view) is not None


def quota_refusal(store, worker, *, owner_assignment_ref=None, policy_task_id=None):
    """REFUSAL_CODE on a fresh quota FAIL or a gw-agent hold, else None.

    Owner policy comes first: when the Owner policy refuses the worker this
    returns None, so the route's own Owner refusal is the one reported. The
    hold class itself is read with hold_reason.
    """
    if not fresh_quota_fail(store, worker):
        return None
    decision = worker_dispatch.evaluate(worker, owner_assignment_ref, policy_task_id)
    return REFUSAL_CODE if decision.allowed else None


# --- #2367: gw-agent failure classes, holds and the availability probe -----

GW_AGENT = 'gw-agent'
ENDPOINT_LAYER = 'endpoint'
AUTHENTICATION_LAYER = 'authentication'
RUNTIME_LAYER = 'runtime'
MODEL_CAPABILITY = 'MODEL_CAPABILITY'
INFRASTRUCTURE = 'INFRASTRUCTURE'
SECURITY = 'SECURITY'
OUTCOME_UNKNOWN = 'UNKNOWN'
HOLD_SECURITY = 'SECURITY_STOP'
HOLD_INFRASTRUCTURE = 'INFRASTRUCTURE_PAUSE'
HOLD_MODEL_CAPABILITY = 'MODEL_CAPABILITY_PAUSE'
# E9: pause after this many consecutive model-capability failures at the
# lowest tier (the Owner's E9 rule, not a new limit).
E9_LOWEST_TIER_PAUSE_AFTER = 2
# A hold is kept for the longest window the store accepts, less one ordinary
# freshness window so it always stays inside the store bound.
HOLD_FOR = MAX_RESET_WINDOW - FRESH_FOR

_GW_UNKNOWN_CODES = frozenset(('DEVELOPER_RESULT_UNKNOWN',))
# HTTP 429 is a quota/rate condition: the quota layer (classify/observe) owns it.
_GW_RATE_CODES = frozenset(_ROUTE_CODES['gw-agent']) | frozenset((
    'DEVELOPER_GW_AGENT_MODEL_ERROR_HTTP_429',))
_GW_SECURITY_CODES = frozenset((
    'DEVELOPER_GW_AGENT_CREDENTIAL_MISSING', 'DEVELOPER_GW_AGENT_AUDIT_INVALID',
    'DEVELOPER_GW_AGENT_MODEL_UNVERIFIED', 'DEVELOPER_GW_AGENT_MODEL_ERROR_HTTP_401',
    'DEVELOPER_GW_AGENT_MODEL_ERROR_HTTP_403',
))
_GW_SECURITY_FAMILY = re.compile(
    r'DEVELOPER_GW_AGENT_(?:POLICY_REFUSED|TASK_ID_MISMATCH)(?:_[A-Z0-9_]+)?\Z')
_GW_INFRA_CODES = frozenset(('DEVELOPER_GW_AGENT_TIMEOUT', 'DEVELOPER_GW_AGENT_GATEWAY_TIMEOUT'))
_GW_INFRA_FAMILY = re.compile(r'DEVELOPER_GW_AGENT_MODEL_ERROR(?:_[A-Z0-9_]+)?\Z')
_GW_CAPABILITY_FAMILY = re.compile(
    r'DEVELOPER_GW_AGENT_(?:BLOCKED|BUDGET_EXCEEDED)(?:_[A-Z0-9_]+)?\Z')


def classify_failure(record):
    """Return the failure class of one gw-agent Task record, or None.

    MODEL_CAPABILITY, INFRASTRUCTURE, SECURITY or UNKNOWN. None for another
    route, for no failure and for a quota/rate condition (the quota layer).
    """
    profile, developer = _profile(record)
    if profile != GW_AGENT:
        return None
    code, state = developer.get('error_code'), developer.get('state')
    if state == 'DEVELOPER_OUTCOME_UNKNOWN' or code in _GW_UNKNOWN_CODES:
        return OUTCOME_UNKNOWN
    if type(code) is not str:
        return MODEL_CAPABILITY if state == 'DEVELOPER_OUTPUT_REJECTED' else None
    if code in _GW_RATE_CODES:
        return None
    if code in _GW_SECURITY_CODES or _GW_SECURITY_FAMILY.fullmatch(code):
        return SECURITY
    if code in _GW_INFRA_CODES or _GW_INFRA_FAMILY.fullmatch(code):
        return INFRASTRUCTURE
    if _GW_CAPABILITY_FAMILY.fullmatch(code) or state == 'DEVELOPER_OUTPUT_REJECTED':
        return MODEL_CAPABILITY
    if code.startswith('DEVELOPER_GW_AGENT_'):
        return OUTCOME_UNKNOWN  # an unlisted gateway code is never guessed
    return None


def _now(now):
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)


def _digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def _record_evidence(record, failure):
    developer = record.get('developer') if type(record) is dict else None
    return _digest({
        'task_id': record.get('task_id') if type(record) is dict else None,
        'assignment_id': developer.get('assignment_id') if type(developer) is dict else None,
        'worker_key': GW_AGENT, 'failure_class': failure,
    })


def _streak_marker(streak):
    """The evidence digest that names a lowest-tier capability streak."""
    return _digest({'worker_key': GW_AGENT, 'failure_class': MODEL_CAPABILITY,
                    'lowest_tier_streak': streak})


def _write(store, layer, status, observed, fresh_until, evidence, expected=None):
    kwargs = {}
    if expected is not None:
        kwargs['expected'] = expected
    store.record_worker_observation(
        worker_key=GW_AGENT, layer=layer, status=status,
        observed_at=_stamp(observed), fresh_until=_stamp(fresh_until),
        source='LOCAL_OBSERVATION', evidence_sha256=evidence, **kwargs)


def observe_failure_class(store, record, *, now=None):
    """Record one gw-agent outcome's infrastructure or security class; return the class.

    INFRASTRUCTURE writes an endpoint FAIL and SECURITY an authentication FAIL,
    each held for HOLD_FOR unless a probe PASS clears the endpoint one first.
    MODEL_CAPABILITY goes through record_capability_outcome; UNKNOWN and None
    write nothing.
    """
    failure = classify_failure(record)
    layer = {INFRASTRUCTURE: ENDPOINT_LAYER, SECURITY: AUTHENTICATION_LAYER}.get(failure)
    if layer is None:
        return failure
    observed = _now(now)
    _write(store, layer, 'FAIL', observed, observed + HOLD_FOR,
           _record_evidence(record, failure))
    return failure


def _runtime_streak(entry):
    """The current lowest-tier capability streak named by a runtime FAIL, else 0."""
    if type(entry) is not dict or entry.get('status') != 'FAIL':
        return 0
    for streak in range(1, E9_LOWEST_TIER_PAUSE_AFTER + 1):
        if entry.get('evidence_sha256') == _streak_marker(streak):
            return streak
    return 0


def record_capability_outcome(store, record, *, passed, at_lowest_tier, now=None):
    """E9 for one judged gw-agent result; return CLEAR, STEP_DOWN, LOWEST_TIER_FAILURE or PAUSED.

    A failure above the lowest tier is a step-down and leaves the streak alone.
    Consecutive failures at the lowest tier count up to the E9 pause; a pass
    resets a running streak. Once paused, neither a pass nor a probe clears it.
    Only a model-capability failure may be counted here.
    """
    if (type(passed) is not bool or type(at_lowest_tier) is not bool or
            worker_key(record) != GW_AGENT):
        raise LLMToolError('WORKER_CAPABILITY_OUTCOME_INVALID')
    if not passed and classify_failure(record) not in (MODEL_CAPABILITY, None):
        raise LLMToolError('WORKER_FAILURE_CLASS_MISMATCH')
    before = store.get_worker_observations()[GW_AGENT][RUNTIME_LAYER]
    streak = _runtime_streak(before)
    if streak >= E9_LOWEST_TIER_PAUSE_AFTER:
        return 'PAUSED'
    expected = before if before.get('status') in ('PASS', 'FAIL') else None
    observed = _now(now)
    if passed:
        if streak:
            _write(store, RUNTIME_LAYER, 'PASS', observed, observed + FRESH_FOR,
                   _record_evidence(record, None), expected)
        return 'CLEAR'
    if not at_lowest_tier:
        return 'STEP_DOWN'
    streak += 1
    _write(store, RUNTIME_LAYER, 'FAIL', observed, observed + HOLD_FOR,
           _streak_marker(streak), expected)
    return 'PAUSED' if streak >= E9_LOWEST_TIER_PAUSE_AFTER else 'LOWEST_TIER_FAILURE'


def _hold_from_view(view):
    entry = view.get(GW_AGENT) if type(view) is dict else None
    if type(entry) is not dict:
        return None

    def status(layer):
        item = entry.get(layer)
        return item.get('status') if type(item) is dict else None

    if status(AUTHENTICATION_LAYER) == 'FAIL':
        return HOLD_SECURITY
    if _runtime_streak(entry.get(RUNTIME_LAYER)) >= E9_LOWEST_TIER_PAUSE_AFTER:
        return HOLD_MODEL_CAPABILITY
    if status(ENDPOINT_LAYER) == 'FAIL':
        return HOLD_INFRASTRUCTURE
    return None


def hold_reason(store, worker):
    """SECURITY_STOP, MODEL_CAPABILITY_PAUSE or INFRASTRUCTURE_PAUSE for gw-agent, else None."""
    if worker != GW_AGENT:
        return None
    reader = getattr(store, 'get_worker_observations', None)
    if not callable(reader):
        return None
    try:
        return _hold_from_view(reader())
    except Exception:
        return None


def run_gw_agent_probe(store, route, budgets, *, probe=None, now=None):
    """Run at most one gw-agent availability probe and record its result.

    Refused before any call while a security stop holds (no automatic
    recovery), while a quota FAIL holds (Retry-After or the quota window) and
    within FRESH_FOR of the last endpoint observation (the existing backoff), so
    repeated failures cannot loop. PASS clears only the infrastructure pause.
    The probe never reads or writes a Task record and never replays a Task.
    """
    try:
        entry = store.get_worker_observations()[GW_AGENT]
        authentication = dict(entry[AUTHENTICATION_LAYER])
        quota = dict(entry[QUOTA_LAYER])
        endpoint = dict(entry[ENDPOINT_LAYER])
    except Exception:
        raise LLMToolError('GW_AGENT_PROBE_STATE_UNAVAILABLE') from None
    if authentication.get('status') == 'FAIL':
        raise LLMToolError('GW_AGENT_PROBE_SECURITY_STOP')
    if quota.get('status') == 'FAIL':
        raise LLMToolError('GW_AGENT_PROBE_RATE_LIMITED')
    if endpoint.get('status') in ('PASS', 'FAIL'):
        last = _parse_strict_utc(endpoint.get('observed_at'))
        if last is None or _now(now) < last + FRESH_FOR:
            raise LLMToolError('GW_AGENT_PROBE_NOT_DUE')
    if probe is None:
        from .developer_routes.gw_agent import probe_availability as probe
    report = probe(route, budgets)
    if type(report) is not dict or report.get('outcome') not in (
            'PASS', 'INFRASTRUCTURE', 'AUTHORIZATION', 'RATE_LIMITED', 'UNKNOWN'):
        report = {'outcome': 'UNKNOWN', 'tcp': 'UNKNOWN', 'auth': 'UNKNOWN',
                  'inference': 'UNKNOWN', 'retry_after_seconds': None}
    outcome = report['outcome']
    observed = _now(now)
    evidence = _digest({'worker_key': GW_AGENT, 'probe': {
        key: report.get(key) for key in ('outcome', 'tcp', 'auth', 'inference', 'checked_at')}})
    if outcome == 'PASS':
        layer, status, fresh_until = ENDPOINT_LAYER, 'PASS', observed + FRESH_FOR
    elif outcome == 'AUTHORIZATION':
        layer, status, fresh_until = AUTHENTICATION_LAYER, 'FAIL', observed + HOLD_FOR
    elif outcome == 'RATE_LIMITED':
        # HTTP 429: honour Retry-After when the gateway reports it, else the
        # ordinary quota window; the probe is refused until it ends.
        retry = report.get('retry_after_seconds')
        window = (min(timedelta(seconds=retry), HOLD_FOR)
                  if type(retry) is int and retry > 0 else FRESH_FOR)
        layer, status, fresh_until = QUOTA_LAYER, 'FAIL', observed + window
    else:
        # INFRASTRUCTURE, or UNKNOWN: a probe that cannot show the gateway
        # available never clears the pause, and the backoff still applies.
        layer, status, fresh_until = ENDPOINT_LAYER, 'FAIL', observed + HOLD_FOR
    _write(store, layer, status, observed, fresh_until, evidence)
    result = {key: report.get(key) for key in (
        'outcome', 'tcp', 'auth', 'inference', 'retry_after_seconds', 'checked_at',
        'elapsed_ms')}
    result.update(worker=GW_AGENT, recorded_layer=layer, recorded_status=status,
                  hold=hold_reason(store, GW_AGENT))
    return result
