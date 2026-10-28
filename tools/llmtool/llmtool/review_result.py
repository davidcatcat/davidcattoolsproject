"""Collect a dispatched review verdict and durably notify its original PM."""

import hashlib
import json
import re

from . import task_completion
from .errors import LLMToolError


_HEX40 = re.compile(r'[0-9a-f]{40}\Z')
_HEX32 = re.compile(r'[0-9a-f]{32}\Z')
_VERDICT_VERSION_HEAD = re.compile(
    r'Verdict: (PASS|FIX|FAIL|BLOCKED) \| Version: v([1-9][0-9]{0,3}) \| HEAD: ([0-9a-f]{40})\Z')


def _refuse(code):
    error = LLMToolError('Review result is unavailable; details omitted')
    error.review_dispatch_code = code
    raise error from None


def _comment_verdict(comment, *, head, version, task):
    body = comment.get('body')
    if type(body) is not str or len(body.encode('utf-8', 'replace')) > 65536:
        return None
    lines = [line for line in body.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    status_line = _VERDICT_VERSION_HEAD.fullmatch(lines[1])
    if status_line is None:
        return None
    status, raw_version, raw_head = status_line.groups()
    if raw_head != head or int(raw_version) != version:
        return None

    marker = task_completion._VERDICT_MARKER.fullmatch(lines[0])
    marker_matches = marker is not None and marker.group(2) == status
    codex_formal_pass = False
    # A Codex PASS can be formal only for a Claude-authored Task, and only
    # after the existing strict Claude validator accepts the same bound body.
    if marker_matches and marker.group(1) == 'Codex' and status == 'PASS':
        developer = task.get('developer')
        if type(developer) is dict and developer.get('provider_profile') == 'claude':
            claude_marker = lines[0].replace('actor=Codex', 'actor=Claude', 1)
            claude_body = body.replace(lines[0], claude_marker, 1)
            codex_formal_pass = task_completion.is_formal_claude_pass(
                claude_body, task_id=task['task_id'], run_id=task['run_id'],
                contract_digest=task['task_binding']['contract_digest'],
                result_sha=head)
        if not codex_formal_pass:
            return None
    comment_id = comment.get('id')
    if type(comment_id) is not int or comment_id < 0:
        _refuse('REVIEW_RESULT_INVALID')

    formal_pass = (codex_formal_pass or
                   (marker_matches and marker.group(1) == 'Claude' and status == 'PASS' and
                    task_completion.is_formal_claude_pass(
                        body, task_id=task['task_id'], run_id=task['run_id'],
                        contract_digest=task['task_binding']['contract_digest'],
                        result_sha=head)))
    # Match the strict formal PASS shape used by task_completion. All other
    # bound verdicts are non-passing dispositions, including malformed PASSes.
    outcome = 'REVIEW_PASS' if formal_pass else 'REVIEW_FAIL'
    created_at = comment.get('created_at')
    updated_at = comment.get('updated_at', created_at)
    if (type(created_at) is not str or type(updated_at) is not str or
            not created_at or not updated_at):
        _refuse('REVIEW_RESULT_INVALID')
    if updated_at != created_at:
        outcome = 'REVIEW_FAIL'
    findings = sum(1 for line in lines[2:]
                   if line.lstrip().startswith(('-', '*')) or
                   re.match(r'(?i)^finding\b', line.strip()))
    return (updated_at, comment_id, outcome, findings)


def _claim_for(task, *, executor_task, pr_number, version):
    claims = task.get('review_dispatch_claim')
    if type(claims) is dict:
        claims = [claims]
    if type(claims) is not list:
        _refuse('REVIEW_RESULT_CLAIM_MISSING')
    matching = [claim for claim in claims if type(claim) is dict and
                claim.get('executor_task') == executor_task and
                claim.get('pr') == pr_number and claim.get('version') == version]
    if len(matching) != 1:
        _refuse('REVIEW_RESULT_CLAIM_MISSING')
    claim = matching[0]
    head = claim.get('head_sha')
    if type(head) is not str or _HEX40.fullmatch(head) is None:
        _refuse('REVIEW_RESULT_CLAIM_INVALID')
    return claim, head


def record_review_result(*, store, task_id, executor_task, pr_number, version=1,
                         reviewer_session_done=False, github_get=None):
    """Classify the latest claim-bound verdict and persist one idempotent event."""
    if (type(version) is not int or not 1 <= version <= 9999 or
            type(pr_number) is not int or not 1 <= pr_number <= 9999999 or
            type(reviewer_session_done) is not bool):
        _refuse('REVIEW_RESULT_ARGUMENT_INVALID')
    task = store.get(task_id)
    if type(task) is not dict or task.get('task_id') != task_id:
        _refuse('REVIEW_RESULT_TASK_INVALID')
    claim, head = _claim_for(task, executor_task=executor_task,
                             pr_number=pr_number, version=version)
    if (task.get('schema_version') != 2 or
            type(task.get('run_id')) is not str or _HEX32.fullmatch(task['run_id']) is None or
            type(task.get('pm_session_id')) is not str or
            type(task.get('task_binding')) is not dict or
            type(task['task_binding'].get('contract_digest')) is not str):
        _refuse('REVIEW_RESULT_TASK_INVALID')

    get = task_completion._github_get if github_get is None else github_get
    comments = get(f'issues/{pr_number}/comments?per_page=100')
    if type(comments) is not list or len(comments) >= 100:
        _refuse('REVIEW_RESULT_COMMENTS_INVALID')
    latest = None
    for comment in comments:
        if type(comment) is not dict:
            _refuse('REVIEW_RESULT_COMMENTS_INVALID')
        current = _comment_verdict(comment, head=head, version=version, task=task)
        if current is not None and (latest is None or current[:2] > latest[:2]):
            latest = current

    if latest is None:
        if not reviewer_session_done:
            return {'state': 'NOT_READY', 'event': None}
        outcome, comment_id, findings_count = 'REVIEW_BLOCKED', None, 0
    else:
        _, comment_id, outcome, findings_count = latest

    payload = {'outcome': outcome, 'head': head, 'version': version,
               'comment_id': comment_id, 'findings_count': findings_count}
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    if len(payload_bytes) > 512:
        _refuse('REVIEW_RESULT_INVALID')
    payload_sha256 = hashlib.sha256(payload_bytes).hexdigest()
    identity = {'task': task_id, 'run': task['run_id'], 'head': head,
                'version': version, 'outcome': outcome, 'comment_id': comment_id}
    event_id = hashlib.sha256(json.dumps(
        identity, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()[:32]
    result = store.record_completion_event(
        task_id, event_id=event_id, run_id=task['run_id'],
        session_id='review-' + event_id, source='REVIEWER', source_sha=head,
        sequence=0, payload_sha256=payload_sha256)
    return {'state': outcome, 'event_id': event_id,
            'created': result['created'], 'comment_id': comment_id,
            'head': head, 'version': version}
