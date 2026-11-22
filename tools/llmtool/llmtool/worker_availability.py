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
    """Whether the worker's quota layer holds a fresh FAIL.

    Expired, absent or unreadable observations read UNKNOWN and never refuse;
    a store without the observation reader has none.
    """
    if type(worker) is not str or worker in NO_WRITE_WORKERS:
        return False
    reader = getattr(store, 'get_worker_observations', None)
    if not callable(reader):
        return False
    try:
        view = reader()
        return view[worker][QUOTA_LAYER]['status'] == 'FAIL'
    except Exception:
        return False


def quota_refusal(store, worker, *, owner_assignment_ref=None, policy_task_id=None):
    """REFUSAL_CODE on a fresh quota FAIL, else None.

    Owner policy comes first: when the Owner policy refuses the worker this
    returns None, so the route's own Owner refusal is the one reported.
    """
    if not fresh_quota_fail(store, worker):
        return None
    decision = worker_dispatch.evaluate(worker, owner_assignment_ref, policy_task_id)
    return REFUSAL_CODE if decision.allowed else None


# #2367: gw-agent availability. The probe reuses the existing worker
# availability sidecar (no new state system); delegation and probing are both
# refused while a hold is active. Failure classes are kept apart:
#   runtime        infrastructure pause, cleared by a passing probe
#   endpoint       rate limit (HTTP 429), cleared by Retry-After or a probe
#   inference      model capability, stepped down once then paused at the
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
_GW_HOLD_WRITE_FAILED = {}


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


def _active_fail(view, layer):
    entry = _observation(view, layer)
    return type(entry) is dict and entry.get('status') == 'FAIL'


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
    Retry-After. security stays held. model steps down once and pauses on the
    second consecutive failure at the lowest tier (S1). unknown writes nothing.
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
        view = _observation_view(store)
        prior = None if view in (None, 'UNAVAILABLE') else _observation(
            view, GW_AGENT_MODEL_LAYER)
        count = 2
        if tier != 'lowest' or type(prior) is not dict or (
                prior.get('status') != 'FAIL' and
                prior.get('evidence_sha256') != _MODEL_STEP_ONE):
            count = 1
        status = 'FAIL' if count >= 2 else 'UNKNOWN'
        evidence = _MODEL_STEP_TWO if count >= 2 else _MODEL_STEP_ONE
        layer, fresh = GW_AGENT_MODEL_LAYER, _hold_fresh_until(now)
        action = 'PAUSE' if count >= 2 else 'STEP_DOWN'
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
        if layer == GW_AGENT_MODEL_LAYER and _active_fail(view, layer):
            continue  # S1: a model pause is never cleared by a probe
        if layer == GW_AGENT_RATE_LAYER and _active_fail(view, layer):
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
    for task_id in task_ids:
        try:
            record = store.get(task_id)
        except Exception:
            continue
        developer = record.get('developer') if type(record) is dict else None
        if (type(developer) is not dict or
                developer.get('provider_profile') != GW_AGENT_WORKER):
            continue
        failure = classify_gw_agent_failure(developer.get('error_code'))
        if failure == 'unknown' or (classes is not None and failure not in classes):
            continue
        created = developer.get('created_at')
        if type(created) is not str:
            continue
        try:
            created_at = datetime.strptime(created, '%Y-%m-%dT%H:%M:%SZ').replace(
                tzinfo=timezone.utc)
        except ValueError:
            continue
        if now - created_at > MAX_RESET_WINDOW:
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
            if entry.get('evidence_sha256') == _MODEL_STEP_TWO:
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
    if _active_fail(view, GW_AGENT_SECURITY_LAYER):
        return GW_AGENT_REFUSALS['security']
    if _active_fail(view, GW_AGENT_MODEL_LAYER):
        return GW_AGENT_REFUSALS['model']
    if _active_fail(view, GW_AGENT_INFRA_LAYER):
        return GW_AGENT_REFUSALS['infrastructure']
    if _active_fail(view, GW_AGENT_RATE_LAYER):
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
    if _active_fail(view, GW_AGENT_SECURITY_LAYER):
        return _probe_report('FAIL', now, provider_call=False, auth='FAIL',
                             reason=GW_AGENT_REFUSALS['security'])
    if _active_fail(view, GW_AGENT_MODEL_LAYER):
        return _probe_report('FAIL', now, provider_call=False, inference='FAIL',
                             reason=GW_AGENT_REFUSALS['model'])
    if _active_fail(view, GW_AGENT_RATE_LAYER):
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
