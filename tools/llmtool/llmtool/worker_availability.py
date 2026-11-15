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
