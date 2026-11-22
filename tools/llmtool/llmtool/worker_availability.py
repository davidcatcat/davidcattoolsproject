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

#2367 adds the same record's gw-agent availability holds and one bounded probe.
A security stop and a model-capability pause are never cleared by a probe; an
infrastructure pause clears only when a probe durably passes.
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

# #2367: gw-agent availability holds reuse the worker observation sidecar.
# Infrastructure and model-capability holds are separate: a probe may clear
# infrastructure, but never a security stop or a model-capability pause.
GW_AGENT = 'gw-agent'
GW_AGENT_PROBE_SOURCE = 'AUTHORIZED_OPERATION'
GW_AGENT_INFRASTRUCTURE_PAUSED = 'GW_AGENT_INFRASTRUCTURE_PAUSED'
GW_AGENT_MODEL_CAPABILITY_PAUSED = 'GW_AGENT_MODEL_CAPABILITY_PAUSED'
GW_AGENT_SECURITY_STOP = 'GW_AGENT_SECURITY_STOP'
GW_AGENT_HOLD_UNRECORDED = 'GW_AGENT_HOLD_UNRECORDED'
_MODEL_CAPABILITY = 'MODEL_CAPABILITY'
_INFRASTRUCTURE = 'INFRASTRUCTURE'
_SECURITY = 'SECURITY'
_QUOTA = 'QUOTA'
_HOLD_LAYERS = {
    _SECURITY: 'authentication',
    _MODEL_CAPABILITY: 'inference',
    _INFRASTRUCTURE: 'runtime',
}
# A failed hold write is never made available again by the same process.
_GW_AGENT_HOLD_NOT_RECORDED = False

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


def _gw_agent_view(store):
    """The gw-agent worker/layer view, None without a reader (legacy double)."""
    reader = getattr(store, 'get_worker_observations', None)
    if not callable(reader):
        return None
    try:
        view = reader()
        worker = view[GW_AGENT] if type(view) is dict else None
        return worker if type(worker) is dict else {}
    except Exception:
        return {}


def _record_gw_agent(store, layer, status, observed, *, source=GW_AGENT_PROBE_SOURCE,
                     fresh_until=None, label=None):
    """Write one gw-agent observation; True only when it is durable."""
    try:
        store.record_worker_observation(
            worker_key=GW_AGENT, layer=layer, status=status,
            observed_at=_stamp(observed),
            fresh_until=_stamp(fresh_until or observed + FRESH_FOR),
            source=source,
            evidence_sha256=hashlib.sha256(
                ('gw-agent-observation:' + (label or layer + ':' + status)
                 ).encode('utf-8')).hexdigest())
        return True
    except Exception:
        return False


def record_gw_agent_hold(store, kind, *, now=None):
    """Durably record a gw-agent hold; False when no durable hold exists.

    A failed detailed write falls back to the runtime layer, which every
    admission and probe checks first. If even that fails, the process-local
    guard refuses gw-agent until a probe pass is durably recorded. No hold ever
    changes worker-dispatch.json or grants dispatch authority.
    """
    global _GW_AGENT_HOLD_NOT_RECORDED
    if kind not in _HOLD_LAYERS:
        return False
    if not callable(getattr(store, 'record_worker_observation', None)):
        return False
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(
        microsecond=0)
    # A hold is not a freshness sample: it stays until a passing probe or the
    # Owner changes the state. The store's 30-day maximum is the only bound.
    hold_until = observed + MAX_RESET_WINDOW
    if _record_gw_agent(store, _HOLD_LAYERS[kind], 'FAIL', observed,
                        source='LOCAL_OBSERVATION', fresh_until=hold_until,
                        label='hold:' + kind):
        return True
    if kind != _INFRASTRUCTURE and _record_gw_agent(
            store, 'runtime', 'FAIL', observed, source='LOCAL_OBSERVATION',
            fresh_until=hold_until, label='hold:INFRASTRUCTURE_FALLBACK'):
        return True
    _GW_AGENT_HOLD_NOT_RECORDED = True
    return False


def gw_agent_failure_class(record):
    """Classify one gw-agent Developer outcome: model, infrastructure, security or quota.

    Only a fixed error code decides; no provider text, status or state is read.
    Dispatch-denied and pre-provider refusals get None: Owner policy is the
    route's own refusal and is never rewritten into a technical hold.
    """
    if worker_key(record) != GW_AGENT:
        return None
    developer = record.get('developer') if type(record) is dict else None
    if type(developer) is not dict:
        return None
    code = developer.get('error_code')
    if type(code) is not str:
        return None
    if code in _ROUTE_CODES[GW_AGENT] or code.endswith('_HTTP_429'):
        return _QUOTA
    if any(token in code for token in (
            'AUTH', 'CREDENTIAL', 'SECURITY', 'AUDIT_INVALID',
            'HTTP_401', 'HTTP_403', 'POLICY_REFUSED', 'SENSITIVE')):
        return _SECURITY
    if 'DISPATCH_DENIED' in code or 'OWNER_' in code:
        return None
    if any(token in code for token in (
            'HTTP_5', 'HTTP_400', 'HTTP_404', 'HTTP_422', 'CONFIG_INVALID',
            'DISPATCH_FAILED', 'RESULT_UNKNOWN', 'TRANSPORT')):
        return _INFRASTRUCTURE
    if any(token in code for token in (
            'MODEL', 'OUTPUT_INVALID', 'OUTPUT_REJECTED', 'GATEWAY_USAGE')):
        return _MODEL_CAPABILITY
    return None


def _note_gw_agent_outcome(store, record, observed):
    """Persist the hold a gw-agent failure implies; success clears infrastructure."""
    developer = record.get('developer') if type(record) is dict else None
    if type(developer) is not dict:
        return
    classification = gw_agent_failure_class(record)
    if classification is None:
        if developer.get('state') == 'DEVELOPER_RESPONDED':
            _record_gw_agent(store, 'runtime', 'PASS', observed,
                             source='LOCAL_OBSERVATION', label='runtime:PASS')
            _record_gw_agent(store, 'task_acceptance', 'PASS', observed,
                             source='LOCAL_OBSERVATION', label='model-step-down:PASS')
        return
    if classification == _QUOTA:
        return
    if classification in (_SECURITY, _INFRASTRUCTURE):
        record_gw_agent_hold(store, classification, now=observed)
        return
    if classification == _MODEL_CAPABILITY:
        view = _gw_agent_view(store) or {}
        marker = view.get('task_acceptance') if type(view) is dict else None
        paused = view.get('inference') if type(view) is dict else None
        if ((type(marker) is dict and marker.get('status') == 'FAIL') or
                (type(paused) is dict and paused.get('status') == 'FAIL')):
            record_gw_agent_hold(store, _MODEL_CAPABILITY, now=observed)
        else:
            # E9: the first model failure steps down; a second consecutive
            # failure at the lowest tier records the pause above.
            _record_gw_agent(store, 'task_acceptance', 'FAIL', observed,
                             source='LOCAL_OBSERVATION', label='model-step-down')


def gw_agent_hold_refusal(store, *, owner_assignment_ref=None, policy_task_id=None):
    """The gw-agent hold code that refuses admission, else None.

    Owner policy comes first, exactly as quota_refusal: a denied worker is left
    to the route's own Owner refusal. An unrecorded hold always refuses.
    """
    view = _gw_agent_view(store) or {}
    hold = (GW_AGENT_HOLD_UNRECORDED if _GW_AGENT_HOLD_NOT_RECORDED else
            GW_AGENT_SECURITY_STOP
            if view.get('authentication', {}).get('status') == 'FAIL' else
            GW_AGENT_MODEL_CAPABILITY_PAUSED
            if view.get('inference', {}).get('status') == 'FAIL' else
            GW_AGENT_INFRASTRUCTURE_PAUSED
            if view.get('runtime', {}).get('status') == 'FAIL' else None)
    if hold is None:
        return None
    try:
        decision = worker_dispatch.evaluate(GW_AGENT, owner_assignment_ref, policy_task_id)
    except Exception:
        return hold  # an unreadable policy never makes a held worker available
    if not decision.allowed:
        return None
    return hold


# Both names are used by call sites and tests: a hold-only check and the full
# quota-or-hold admission refusal.
gw_agent_admission_refusal = gw_agent_hold_refusal


def gw_agent_refusal(store, *, owner_assignment_ref=None, policy_task_id=None):
    """Quota or hold refusal for gw-agent; owner policy still wins."""
    code = quota_refusal(store, GW_AGENT, owner_assignment_ref=owner_assignment_ref,
                         policy_task_id=policy_task_id)
    return code or gw_agent_hold_refusal(
        store, owner_assignment_ref=owner_assignment_ref, policy_task_id=policy_task_id)


def _probe_status(value):
    return value if value in ('PASS', 'FAIL', 'UNKNOWN') else 'UNKNOWN'


def _probe_report(status, *, tcp='UNKNOWN', auth='UNKNOWN', inference='UNKNOWN',
                  code=None, retry_after=None, observed=None):
    return {'worker': GW_AGENT, 'status': status, 'tcp': tcp, 'auth': auth,
            'inference': inference, 'code': code, 'retry_after': retry_after,
            'observed_at': _stamp(observed) if observed is not None else None}


def _normalize_probe_result(raw):
    if type(raw) is not dict:
        raw = {}
    tcp = _probe_status(raw.get('tcp'))
    auth = _probe_status(raw.get('auth'))
    inference = _probe_status(raw.get('inference'))
    statuses = (tcp, auth, inference)
    status = ('PASS' if all(item == 'PASS' for item in statuses) else
              'FAIL' if 'FAIL' in statuses else 'UNKNOWN')
    retry_after = raw.get('retry_after')
    if type(retry_after) is str and retry_after.isdigit():
        retry_after = int(retry_after)
    if not (type(retry_after) is int and not isinstance(retry_after, bool) and
            1 <= retry_after <= int(MAX_RESET_WINDOW.total_seconds())):
        retry_after = None
    code = raw.get('code')
    if not (type(code) is str and re.fullmatch(r'[A-Z0-9_]{1,64}', code)):
        code = None
    return {'status': status, 'tcp': tcp, 'auth': auth, 'inference': inference,
            'security': raw.get('security') is True, 'retry_after': retry_after,
            'code': code}


def probe_gw_agent(store, *, probe_call=None, call=None, now=None):
    """One bounded gw-agent availability probe.

    A hold that blocks dispatch also blocks the probe before any provider call:
    a security stop and a model-capability pause are never probed, and the
    existing 30-minute freshness window rate-limits another probe after a
    failed one. The probe reports TCP/auth/inference PASS, FAIL or UNKNOWN with
    its time. A pass clears infrastructure and quota evidence; it never clears
    a security stop or a model-capability pause. A failed probe write keeps
    gw-agent unavailable until a pass is durably recorded.
    """
    global _GW_AGENT_HOLD_NOT_RECORDED
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(
        microsecond=0)
    # Every stop that blocks delegation also blocks probing: Owner PAUSED or
    # OWNER_ASSIGNMENT_ONLY never makes a provider call from a probe.
    try:
        decision = worker_dispatch.evaluate(GW_AGENT)
        reason_code = decision.reason_code
        allowed = decision.allowed is True
    except Exception:
        allowed, reason_code = False, 'GW_AGENT_AVAILABILITY_UNKNOWN'
    if not allowed:
        return _probe_report('BLOCKED', code=reason_code, observed=observed)
    view = _gw_agent_view(store)
    # A recorded security or model stop always blocks before any call, even
    # while an unrecorded hold is waiting for a probe to clear it (S1, A3).
    if view:
        if view.get('authentication', {}).get('status') == 'FAIL':
            return _probe_report('BLOCKED', code=GW_AGENT_SECURITY_STOP,
                                 observed=observed)
        if view.get('inference', {}).get('status') == 'FAIL':
            return _probe_report('BLOCKED', code=GW_AGENT_MODEL_CAPABILITY_PAUSED,
                                 observed=observed)
    # An unrecorded hold has no trusted class, so only a passing probe may end
    # it (S2): backoff and probe-sourced blocks do not wedge that recovery.
    if not _GW_AGENT_HOLD_NOT_RECORDED and view:
        runtime = view.get('runtime')
        if (type(runtime) is dict and runtime.get('status') == 'FAIL' and
                runtime.get('source') == GW_AGENT_PROBE_SOURCE):
            return _probe_report('BLOCKED', code=GW_AGENT_INFRASTRUCTURE_PAUSED,
                                 observed=observed)
        if view.get('quota', {}).get('status') == 'FAIL':
            return _probe_report('BLOCKED', code=REFUSAL_CODE, observed=observed)

    if probe_call is None and call is not None:
        probe_call = call
    if probe_call is None:
        from . import task_submission_cli
        probe_call = task_submission_cli._gw_agent_probe_call
    try:
        result = _normalize_probe_result(probe_call())
    except Exception:
        result = _normalize_probe_result({})

    if result['status'] == 'PASS':
        writes = [
            ('endpoint', 'PASS', 'probe:tcp'),
            ('authentication', 'PASS', 'probe:auth'),
            ('inference', 'PASS', 'probe:inference'),
            ('task_acceptance', 'PASS', 'probe:model-step-down'),
            ('quota', 'PASS', 'probe:quota'),
            ('runtime', 'PASS', 'probe:runtime'),
        ]
        ok = all(_record_gw_agent(store, layer, value, observed, label=label)
                 for layer, value, label in writes)
        if not ok:
            if callable(getattr(store, 'record_worker_observation', None)):
                _GW_AGENT_HOLD_NOT_RECORDED = True
            return _probe_report('UNKNOWN', code='GW_AGENT_PROBE_NOT_RECORDED',
                                 observed=observed)
        _GW_AGENT_HOLD_NOT_RECORDED = False
        return _probe_report('PASS', tcp='PASS', auth='PASS', inference='PASS',
                             code=result['code'], observed=observed)

    if result['security'] or result['auth'] == 'FAIL':
        record_gw_agent_hold(store, _SECURITY, now=observed)
        return _probe_report('FAIL', tcp=result['tcp'], auth='FAIL',
                             inference=result['inference'], code=result['code'],
                             observed=observed)
    if result['retry_after'] is not None:
        retry_until = observed + timedelta(seconds=result['retry_after'])
        ok = _record_gw_agent(store, 'quota', 'FAIL', observed,
                              fresh_until=retry_until, label='probe:retry-after')
        if not ok and callable(getattr(store, 'record_worker_observation', None)):
            _GW_AGENT_HOLD_NOT_RECORDED = True
        return _probe_report('FAIL', tcp=result['tcp'], auth=result['auth'],
                             inference=result['inference'], code=result['code'],
                             retry_after=result['retry_after'], observed=observed)
    if result['tcp'] == 'FAIL':
        _record_gw_agent(store, 'endpoint', 'FAIL', observed, label='probe:tcp')
    # An inference-only failure and an UNKNOWN outcome stay infrastructure
    # holds: the next probe is rate-limited by this probe-sourced FAIL, and no
    # result is replayed here.
    if (not _record_gw_agent(store, 'runtime', 'FAIL', observed, label='probe:runtime')
            and callable(getattr(store, 'record_worker_observation', None))):
        _GW_AGENT_HOLD_NOT_RECORDED = True
    return _probe_report(result['status'], tcp=result['tcp'], auth=result['auth'],
                         inference=result['inference'], code=result['code'],
                         observed=observed)


# Short public spellings for CLI and test callers.
gw_agent_probe = probe_gw_agent
gw_agent_hold = record_gw_agent_hold


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
    if worker is None or worker in NO_WRITE_WORKERS:
        return None
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(
        microsecond=0)
    if result is None:
        if worker == GW_AGENT:
            _note_gw_agent_outcome(store, record, observed)
        return None
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
    if worker == GW_AGENT:
        _note_gw_agent_outcome(store, record, observed)
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
