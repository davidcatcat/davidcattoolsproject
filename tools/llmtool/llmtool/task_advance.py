"""Advance one durable Task by exactly one review or closure action."""

import os
import re
import contextlib
import io
import json
import shutil
from argparse import Namespace

from . import github_events, review_dispatch, review_repair, review_result
from . import task_completion, task_submission_cli
from .errors import LLMToolError
from .task_store import TaskStore


def _claims(record):
    value = record.get('review_dispatch_claim')
    if type(value) is dict:
        return [value]
    return value if type(value) is list else []


def _events(related):
    if type(related) is dict:
        value = related.get('events', [])
    else:
        value = related
    return value if type(value) is list else []


def derive_repair_state(repair_record):
    """Normalize a repair Task record into the state used by the advance loop."""
    if type(repair_record) is not dict:
        return 'UNKNOWN'
    developer = repair_record.get('developer')
    state = developer.get('state') if type(developer) is dict else None
    if state == 'DEVELOPER_PENDING' or repair_record.get('state') == 'PM_PENDING':
        return 'PENDING'
    if state == 'DEVELOPER_DISPATCHING':
        return 'RUNNING'
    if state == 'DEVELOPER_UNAVAILABLE':
        return 'UNAVAILABLE'
    if state == 'DEVELOPER_OUTPUT_REJECTED':
        return 'OUTPUT_REJECTED'
    if state == 'DEVELOPER_RESPONDED':
        return 'RESPONDED'
    if state == 'DEVELOPER_OUTCOME_UNKNOWN':
        execution = developer.get('execution') if type(developer) is dict else None
        if (developer.get('error_code') == 'CODEX_BLOCKED' or
                (type(execution) is dict and execution.get('status') == 'BLOCKED')):
            return 'BLOCKED'
        return 'UNKNOWN'
    return 'UNKNOWN'


def decide(record, d6, pr, related):
    """Return the next action name using only supplied snapshots."""
    if type(record) is not dict:
        return 'DEVELOPER_ATTENTION'
    developer = record.get('developer')
    if type(developer) is not dict or developer.get('state') in (
            'DEVELOPER_OUTCOME_UNKNOWN', 'DEVELOPER_UNAVAILABLE',
            'DEVELOPER_OUTPUT_REJECTED'):
        return 'DEVELOPER_ATTENTION'
    if developer.get('state') != 'DEVELOPER_RESPONDED':
        return 'DEVELOPER_ATTENTION'
    if type(d6) is not dict or d6.get('dirty') is True or d6.get('state') == 'DIRTY':
        return 'NEEDS_PUBLISH'
    result_head = d6.get('result_head')
    if (type(result_head) is not str or re.fullmatch(r'[0-9a-f]{40}', result_head) is None or
            d6.get('recorded_result_head') != result_head):
        return 'NEEDS_PUBLISH'
    claims = _claims(record)
    chosen_pr = (pr.get('number') if type(pr) is dict else None)
    if chosen_pr is None and claims:
        chosen_pr = claims[-1].get('pr') if type(claims[-1]) is dict else None
    if type(chosen_pr) is not int or chosen_pr < 1:
        return 'NEEDS_PUBLISH'
    pr_head = pr.get('head') if type(pr) is dict else None
    if type(pr_head) is not dict or pr_head.get('sha') != result_head:
        return 'NEEDS_PUBLISH'

    matching = [claim for claim in claims if type(claim) is dict and
                claim.get('pr') == chosen_pr and claim.get('head_sha') == result_head]
    if not matching:
        return 'DISPATCH_REVIEW'
    claim = max(matching, key=lambda item: item.get('version', 0))
    if claim.get('state') in ('CLAIMED', 'UNKNOWN'):
        return 'REVIEW_OUTCOME_UNKNOWN'
    events = [event for event in _events(related) if type(event) is dict and
              event.get('source') == 'REVIEWER' and event.get('source_sha') == result_head and
              event.get('version', claim.get('version')) == claim.get('version')]
    if claim.get('state') in ('DISPATCHED', 'COMPLETED') and not events:
        return 'RECORD_REVIEW_RESULT'
    if events:
        event = events[-1]
        outcome = event.get('outcome') or event.get('state')
        if outcome == 'REVIEW_BLOCKED':
            return 'REVIEWER_ATTENTION'
        if outcome == 'REVIEW_FAIL':
            repairs = (related.get('repair_claims', []) if type(related) is dict else [])
            current_repair = next((item for item in reversed(repairs)
                                   if type(item) is dict and item.get('head_sha') == result_head), None)
            if (current_repair is not None and current_repair.get('state') == 'DISPATCHED' and
                    current_repair.get('repair_state') in (
                        'UNAVAILABLE', 'BLOCKED', 'OUTPUT_REJECTED')):
                return 'REPAIR_RETRY'
            if (current_repair is not None and current_repair.get('state') == 'DISPATCHED' and
                    current_repair.get('repair_state') in ('RUNNING', 'PENDING')):
                return 'WAITING_REPAIR'
            if (current_repair is not None and current_repair.get('state') == 'DISPATCHED' and
                    current_repair.get('repair_state') == 'UNKNOWN'):
                return 'REPAIR_OUTCOME_UNKNOWN'
            if (current_repair is not None and current_repair.get('state') == 'DISPATCHED' and
                    current_repair.get('repair_state') == 'RECORD_UNAVAILABLE'):
                return 'DEVELOPER_ATTENTION'
            if (current_repair is not None and current_repair.get('state') == 'DISPATCHED' and
                    current_repair.get('repair_state') == 'RESPONDED'):
                return 'REPAIR_RESULT_NOT_PUBLISHED'
            return 'REPAIR_REVIEW'
        if outcome == 'REVIEW_PASS':
            completion = record.get('completion')
            evidence = completion.get('evidence') if type(completion) is dict else None
            review_ci = evidence.get('review_pr_ci') if type(evidence) is dict else None
            result = evidence.get('result') if type(evidence) is dict else None
            if (type(completion) is dict and completion.get('state') == 'PM_EVIDENCE_READY' and
                    type(review_ci) is dict and type(result) is dict and
                    result.get('result_head') == result_head):
                return 'READY_FOR_INTEGRATION'
            return 'RUN_TASK_COMPLETE'
    return 'REVIEW_OUTCOME_UNKNOWN'


def _refusal(exc, attribute):
    code = getattr(exc, attribute, None)
    if type(code) is str and re.fullmatch(r'[A-Z0-9_]{1,64}', code):
        return code
    return 'TASK_ADVANCE_REFUSED'


def _pr_number(record, requested):
    if requested is not None:
        return requested
    claims = _claims(record)
    return claims[-1].get('pr') if claims and type(claims[-1]) is dict else None


def _d6_snapshot(record, executor_task):
    completion = record.get('completion') or {}
    evidence = completion.get('evidence') or {}
    common_dir = evidence.get('common_dir')
    if type(common_dir) is not str:
        return {'state': 'D6_BINDING_UNAVAILABLE', 'code': 'D6_BINDING_UNAVAILABLE'}
    try:
        registration = task_completion.read_d6_registration(
            executor_task, common_dir,
            allowed_states=task_completion._D6_EVIDENCE_STATES)
        snapshot = task_completion.collect_git_snapshot(registration.root)
    except Exception as exc:
        return {'state': 'D6_BINDING_UNAVAILABLE',
                'code': _refusal(exc, 'completion_code')}
    clean = bool(getattr(snapshot, 'clean', False))
    recorded = registration.result_sha
    return {'root': registration.root, 'common_dir': registration.common_dir,
            'branch': registration.branch, 'result_head': snapshot.head,
            'recorded_result_head': recorded,
            'registered_result_head': registration.result_sha, 'dirty': not clean,
            'state': 'CLEAN' if clean else 'DIRTY'}


def _pr_snapshot(number, github_get):
    if type(number) is not int or not 1 <= number <= 9999999:
        return None
    value = github_get(f'pulls/{number}')
    return value if type(value) is dict else None


def _review_outcomes(record, claims, github_get):
    """Read verdicts bound to claims; completion events intentionally hold hashes only."""
    from . import review_result as result_module
    outcomes = []
    task = record
    for claim in claims:
        if type(claim) is not dict or claim.get('state') != 'DISPATCHED':
            continue
        number, version, head = claim.get('pr'), claim.get('version'), claim.get('head_sha')
        events = [event for event in record.get('completion_events', [])
                  if type(event) is dict and event.get('source') == 'REVIEWER' and
                  event.get('source_sha') == head]
        if not events:
            continue
        comments = github_get(f'issues/{number}/comments?per_page=100')
        if type(comments) is not list or len(comments) >= 100:
            continue
        latest = None
        for comment in comments:
            try:
                value = result_module._comment_verdict(comment, head=head,
                    version=version, task=task)
            except Exception:
                value = None
            if value is not None and (latest is None or value[:2] > latest[:2]):
                latest = value
        if latest is not None:
            import hashlib
            import json
            identity = {'task': record['task_id'], 'run': record['run_id'],
                        'head': head, 'version': version,
                        'outcome': latest[2], 'comment_id': latest[1]}
            event_id = hashlib.sha256(json.dumps(
                identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:32]
            if any(event.get('event_id') == event_id for event in events):
                outcomes.append({'source': 'REVIEWER', 'source_sha': head,
                                 'version': version, 'outcome': latest[2]})
        else:
            import hashlib
            import json
            identity = {'task': record['task_id'], 'run': record['run_id'],
                        'head': head, 'version': version,
                        'outcome': 'REVIEW_BLOCKED', 'comment_id': None}
            event_id = hashlib.sha256(json.dumps(
                identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:32]
            if any(event.get('event_id') == event_id for event in events):
                outcomes.append({'source': 'REVIEWER', 'source_sha': head,
                                 'version': version, 'outcome': 'REVIEW_BLOCKED'})
    return outcomes


def _merged_review_target(record, claims, events, pr, integration_pr, github_get):
    """Return a verified merged PR tuple for a completed review, if present."""
    completion = record.get('completion')
    evidence = completion.get('evidence') if type(completion) is dict else None
    result = evidence.get('result') if type(evidence) is dict else None
    review_ci = evidence.get('review_pr_ci') if type(evidence) is dict else None
    if (type(completion) is not dict or completion.get('state') != 'PM_EVIDENCE_READY' or
            type(result) is not dict or type(review_ci) is not dict):
        return None
    for claim in reversed(claims):
        if type(claim) is not dict or claim.get('state') not in ('DISPATCHED', 'COMPLETED'):
            continue
        head = claim.get('head_sha')
        number = claim.get('pr')
        version = claim.get('version')
        if (type(head) is not str or re.fullmatch(r'[0-9a-f]{40}', head) is None or
                result.get('result_head') != head or
                type(number) is not int or type(version) is not int):
            continue
        if not any(type(event) is dict and event.get('source') == 'REVIEWER' and
                   event.get('source_sha') == head and event.get('version') == version and
                   (event.get('outcome') or event.get('state')) == 'REVIEW_PASS'
                   for event in events):
            continue

        if type(pr) is dict and pr.get('number', number) == number and pr.get('merged') is True:
            pr_head = pr.get('head')
            merge_sha = pr.get('merge_commit_sha')
            if (type(pr_head) is dict and pr_head.get('sha') == head and
                    type(merge_sha) is str and re.fullmatch(r'[0-9a-f]{40}', merge_sha)):
                return number, merge_sha

        if integration_pr is None:
            continue
        try:
            integration = _pr_snapshot(integration_pr, github_get)
            integration_head = integration.get('head') if type(integration) is dict else None
            merge_sha = integration.get('merge_commit_sha') if type(integration) is dict else None
            if (type(integration) is not dict or integration.get('merged') is not True or
                    type(integration_head) is not dict or
                    type(integration_head.get('sha')) is not str or
                    type(merge_sha) is not str or
                    re.fullmatch(r'[0-9a-f]{40}', merge_sha) is None):
                continue
            integration_sha = integration_head.get('sha')
            if type(integration_sha) is not str or re.fullmatch(r'[0-9a-f]{40}', integration_sha) is None:
                continue
            compare = github_get(f'compare/{head}...{integration_sha}')
            if type(compare) is dict and compare.get('status') in ('ahead', 'identical'):
                return integration_pr, merge_sha
        except Exception:
            continue
    return None


def advance(*, task_id, store=None, pr_number=None, integration_pr=None,
            reviewer_session_done=False, dispatch_review_call=None,
            record_review_result_call=None, repair_review_call=None,
            run_task_complete_call=None, record_github_event_call=None,
            github_get=None, cwd=None):
    """Perform one selected action and return a bounded JSON-ready summary."""
    store = TaskStore() if store is None else store
    try:
        record = store.get(task_id)
    except Exception:
        return {'task_id': task_id, 'step': 'REFUSED', 'result': None,
                'next': 'REFUSED', 'codes': ['TASK_RECORD_UNAVAILABLE']}
    executor_task = (record.get('completion') or {}).get('executor_task_id')
    number = _pr_number(record, pr_number)
    get = task_completion._github_get if github_get is None else github_get
    d6 = _d6_snapshot(record, executor_task) if type(executor_task) is str else None
    try:
        pr = _pr_snapshot(number, get) if number is not None else None
    except Exception:
        pr = None
    related = {'events': record.get('completion_events', []),
               'repair_claims': record.get('review_repair_claims', [])}
    repair_claims = []
    for claim in related['repair_claims']:
        if type(claim) is not dict:
            continue
        repair_record = None
        try:
            repair_record = store.get(claim['repair_task_id'])
            repair_state = (derive_repair_state(repair_record)
                            if type(repair_record) is dict else 'RECORD_UNAVAILABLE')
        except Exception:
            repair_state = 'RECORD_UNAVAILABLE'
        repair_claims.append(dict(claim, repair_state=repair_state))
    related['repair_claims'] = repair_claims
    try:
        review_outcomes = _review_outcomes(record, _claims(record), get)
        related['events'] = list(related['events']) + review_outcomes
    except Exception:
        # Advance may proceed from persisted reviewer events when GitHub is unavailable.
        pass
    merged = _merged_review_target(record, _claims(record), related['events'], pr,
                                    integration_pr, get)
    if merged is not None:
        merge_number, merge_sha = merged
        import hashlib
        event_id = hashlib.sha256(
            f'GITHUB\0merge\0{merge_number}\0{merge_sha}'.encode()).hexdigest()[:32]
        invoke = record_github_event_call or github_events._record
        try:
            invoke(store, record, 'github-task-advance', event_id,
                   merge_number, None, 'merge', 'MERGED', merge_sha)
            return {'task_id': task_id, 'step': 'CLOSED', 'result': None,
                    'next': 'CLOSED', 'codes': []}
        except Exception as exc:
            return {'task_id': task_id, 'step': 'REFUSED', 'result': None,
                    'next': 'REFUSED', 'codes': [_refusal(exc, 'completion_code') if
                        getattr(exc, 'completion_code', None) else
                        _refusal(exc, 'task_store_error_code')]}
    if type(d6) is dict and d6.get('state') == 'D6_BINDING_UNAVAILABLE':
        code = d6.get('code') if type(d6.get('code')) is str else 'D6_BINDING_UNAVAILABLE'
        return {'task_id': task_id, 'step': 'D6_BINDING_UNAVAILABLE', 'result': None,
                'next': 'D6_BINDING_UNAVAILABLE', 'codes': [code]}
    step = decide(record, d6, pr, related)
    result, codes = None, []
    if step == 'DISPATCH_REVIEW':
        claims = _claims(record)
        versions = [c.get('version', 0) for c in claims if type(c) is dict and
                    type(c.get('version')) is int]
        version = max(versions, default=0) + 1
        invoke = dispatch_review_call or review_dispatch.dispatch_review
        try:
            result = invoke(store=store, task_id=task_id, executor_task=executor_task,
                            pr_number=number, version=version)
        except Exception as exc:
            codes.append(_refusal(exc, 'review_dispatch_code'))
    elif step == 'RECORD_REVIEW_RESULT':
        claim = next(c for c in reversed(_claims(record)) if type(c) is dict and
                     c.get('pr') == number and c.get('head_sha') == d6['result_head'])
        invoke = record_review_result_call or review_result.record_review_result
        try:
            result = invoke(store=store, task_id=task_id, executor_task=executor_task,
                            pr_number=number, version=claim['version'],
                            # First check for a posted comment without creating a
                            # REVIEW_BLOCKED event for a missing verdict.
                            reviewer_session_done=False,
                            github_get=get)
            if type(result) is dict and result.get('state') == 'REVIEW_BLOCKED':
                step = 'REVIEWER_ATTENTION'
                codes.append('REVIEWER_ATTENTION')
            elif (type(result) is dict and result.get('state') == 'NOT_READY' and
                  reviewer_session_done):
                step = 'REVIEW_NOT_POSTED'
                codes.append('REVIEW_VERDICT_NOT_POSTED')
                result = {'reviewer_task': claim.get('reviewer_task'),
                          'reviewer_session_id': claim.get('native_session_id'),
                          'pr': claim.get('pr'), 'head_sha': claim.get('head_sha'),
                          'version': claim.get('version')}
        except Exception as exc:
            codes.append(_refusal(exc, 'review_dispatch_code'))
    elif step in ('REPAIR_REVIEW', 'REPAIR_RETRY'):
        # decide() is the pre-check: RECORD_UNAVAILABLE must surface before cwd/runtime checks.
        if type(d6) is not dict or type(cwd or os.getcwd()) != str or os.path.realpath(cwd or os.getcwd()) != os.path.realpath(d6['root']):
            step, codes = 'NEEDS_CWD', ['NEEDS_CWD']
        else:
            profile = (record.get('developer') or {}).get('provider_profile')
            if profile in ('codex', 'claude') and shutil.which(profile) is None:
                step, codes = 'RUNTIME_NOT_ON_PATH', ['RUNTIME_NOT_ON_PATH']
            else:
                if step == 'REPAIR_RETRY':
                    repair_claim = next((item for item in reversed(record.get('review_repair_claims', []))
                                         if type(item) is dict and item.get('head_sha') == d6['result_head'] and
                                         item.get('state') == 'DISPATCHED'), None)
                    if repair_claim is None:
                        step, codes = 'REFUSED', ['REPAIR_CLAIM_UNAVAILABLE']
                if step in ('REPAIR_REVIEW', 'REPAIR_RETRY'):
                    invoke = repair_review_call or review_repair.repair_review
                    try:
                        captured = io.StringIO()
                        with contextlib.redirect_stdout(captured):
                            result_value = invoke(store=store, task_id=task_id,
                                executor_task=executor_task, pr_number=number,
                                auto_review=False, github_get=get,
                                retry_claim_id=(repair_claim.get('repair_task_id')
                                                if step == 'REPAIR_RETRY' and
                                                type(repair_claim) is dict else None))
                        emitted = captured.getvalue().strip()
                        try:
                            result = json.loads(emitted) if emitted else result_value
                        except (TypeError, ValueError):
                            result = result_value
                    except Exception as exc:
                        codes.append(_refusal(exc, 'review_repair_code'))
    elif step == 'RUN_TASK_COMPLETE':
        claim = next(c for c in reversed(_claims(record)) if type(c) is dict and
                     c.get('pr') == number and c.get('head_sha') == d6['result_head'])
        invoke = run_task_complete_call or task_submission_cli.run_task_complete
        try:
            args = Namespace(task_id=task_id, executor_task=executor_task,
                             reviewer_task=claim.get('reviewer_task'), pr=number)
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                return_code = invoke(args, store=store)
            emitted = captured.getvalue().strip()
            try:
                result = json.loads(emitted) if emitted else {'code': return_code}
            except (TypeError, ValueError):
                result = {'code': return_code}
            fresh = store.get(task_id)
            completion = fresh.get('completion') or {}
            missing = completion.get('missing_gates', [])
            if 'CI' in ' '.join(missing) or any(str(item).startswith(('CI_', 'PR_', 'REQUIRED_CI')) for item in missing):
                codes.extend(item for item in missing if str(item).startswith(('CI_', 'PR_', 'REQUIRED_CI')))
                step = 'WAITING_CI'
            elif missing:
                codes.extend(missing)
                step = 'WAITING_EXECUTOR'
        except Exception as exc:
            codes.append(_refusal(exc, 'completion_code'))
    elif step == 'READY_FOR_INTEGRATION':
        try:
            integration = _pr_snapshot(integration_pr, get) if integration_pr is not None else pr
            merged = type(integration) is dict and integration.get('merged') is True
            merge_sha = integration.get('merge_commit_sha') if merged else None
            if merged:
                integration_head = integration.get('head')
                integration_sha = integration_head.get('sha') if type(integration_head) is dict else None
                if integration_pr is None:
                    merged = integration_sha == d6['result_head']
                else:
                    compare = (get(f'compare/{d6["result_head"]}...{integration_sha}')
                               if type(integration_sha) is str and
                               re.fullmatch(r'[0-9a-f]{40}', integration_sha) else None)
                    merged = type(compare) is dict and compare.get('status') in ('ahead', 'identical')
            if merged and type(merge_sha) is str and re.fullmatch(r'[0-9a-f]{40}', merge_sha):
                import hashlib
                event_id = hashlib.sha256(
                    f'GITHUB\0merge\0{integration_pr or number}\0{merge_sha}'.encode()).hexdigest()[:32]
                invoke = record_github_event_call or github_events._record
                invoke(store, record, 'github-task-advance', event_id,
                       integration_pr or number, None, 'merge', 'MERGED', merge_sha)
                step = 'CLOSED'
            else:
                codes.append('WAITING_INTEGRATION')
        except Exception as exc:
            step, codes = 'REFUSED', [_refusal(exc, 'completion_code') if
                getattr(exc, 'completion_code', None) else
                _refusal(exc, 'task_store_error_code')]
    elif step == 'REVIEWER_ATTENTION':
        # A prior result check may already have persisted the synthetic
        # REVIEW_BLOCKED event for a completed session with no posted verdict.
        # Keep that recoverable state actionable on later advance calls.
        claim = next((c for c in reversed(_claims(record)) if type(c) is dict and
                      c.get('pr') == number and c.get('head_sha') == d6['result_head']), None)
        missing_verdict = (reviewer_session_done and claim is not None and any(
            type(event) is dict and event.get('source') == 'REVIEWER' and
            event.get('source_sha') == claim.get('head_sha') and
            event.get('version') == claim.get('version') and
            (event.get('outcome') or event.get('state')) == 'REVIEW_BLOCKED'
            for event in related['events']))
        if missing_verdict:
            step = 'REVIEW_NOT_POSTED'
            codes.append('REVIEW_VERDICT_NOT_POSTED')
            result = {'reviewer_task': claim.get('reviewer_task'),
                      'reviewer_session_id': claim.get('native_session_id'),
                      'pr': claim.get('pr'), 'head_sha': claim.get('head_sha'),
                      'version': claim.get('version')}
        else:
            codes.append('REVIEWER_ATTENTION')
    elif step == 'REVIEW_NOT_POSTED':
        pass
    elif step == 'NEEDS_PUBLISH':
        codes.append('NEEDS_PUBLISH')
    elif step == 'DEVELOPER_ATTENTION':
        active_repair = next((item for item in reversed(related['repair_claims'])
                              if type(item) is dict and item.get('head_sha') ==
                              (d6.get('result_head') if type(d6) is dict else None) and
                              item.get('state') == 'DISPATCHED'), None)
        if type(active_repair) is dict and active_repair.get('repair_state') == 'RECORD_UNAVAILABLE':
            codes.append('REPAIR_TASK_RECORD_UNAVAILABLE')
        else:
            codes.append('DEVELOPER_ATTENTION')
    elif step in ('REPAIR_OUTCOME_UNKNOWN', 'REPAIR_RESULT_NOT_PUBLISHED'):
        codes.append(step)
    elif step in ('REVIEW_OUTCOME_UNKNOWN', 'WAITING_REPAIR',
                  'NEEDS_CWD', 'RUNTIME_NOT_ON_PATH'):
        codes.append(step)
    next_action = ('resume the reviewer to post its verdict' if step == 'REVIEW_NOT_POSTED'
                   else step)
    return {'task_id': task_id, 'step': step, 'result': result,
            'next': next_action, 'codes': codes}
