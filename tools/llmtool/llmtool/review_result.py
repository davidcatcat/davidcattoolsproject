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
_CODEX_BINDING = re.compile(
    r'LLMTOOL-REVIEW-BINDING v1 task=([0-9a-f]{32}) run=([0-9a-f]{32}) '
    r'contract=([0-9a-f]{64}) head=([0-9a-f]{40})\Z')
_PRIORITY_FINDING = re.compile(r'^(?:\*\*)?\[P[0-3]\]')


def _refuse(code):
    error = LLMToolError('Review result is unavailable; details omitted')
    error.review_dispatch_code = code
    raise error from None


def _codex_formal_pass(lines, *, head, version, task_id, run_id, contract_digest):
    """Match Codex's PASS header and its exact accept and binding lines."""
    if len(lines) < 4:
        return False
    return (
        lines[0] == '<!-- activity: type=review_verdict actor=Codex target=PM status=PASS -->' and
        lines[1] == f'Verdict: PASS | Version: v{version} | HEAD: {head}' and
        'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT' in lines[2:] and
        (f'LLMTOOL-REVIEW-BINDING v1 task={task_id} run={run_id} '
         f'contract={contract_digest} head={head}') in lines[2:]
    )


def _comment_verdict(comment, *, head, version, task):
    body = comment.get('body')
    if type(body) is not str or len(body.encode('utf-8', 'replace')) > 65536:
        return None
    lines = [line for line in body.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    marker = task_completion._VERDICT_MARKER.fullmatch(lines[0])
    developer = task.get('developer')
    if (marker is not None and marker.group(1) == 'Claude' and
            task_completion._reviewer_author_family(developer) == 'claude'):
        return None
    codex_formal_pass = False
    codex_bound = False
    if marker is not None and marker.group(1) == 'Codex':
        developer_family = task_completion._reviewer_author_family(developer)
        if developer_family is None or developer_family == 'codex':
            return None
        binding = next((match for line in lines[1:]
                        if (match := _CODEX_BINDING.fullmatch(line)) is not None), None)
        task_binding = task.get('task_binding')
        codex_bound = (
            binding is not None and type(task_binding) is dict and
            binding.group(1) == task.get('task_id') and
            binding.group(2) == task.get('run_id') and
            binding.group(3) == task_binding.get('contract_digest') and
            binding.group(4) == head)
        if not codex_bound:
            return None
    status_line = _VERDICT_VERSION_HEAD.fullmatch(lines[1])
    if status_line is None:
        return None
    status, raw_version, raw_head = status_line.groups()
    if raw_head != head or int(raw_version) != version:
        return None
    marker_matches = marker is not None and marker.group(2) == status
    # A Codex PASS is formal only for a cross-family Task and the exact accept
    # and binding lines required by the Codex Reviewer prompt.
    if marker_matches and marker.group(1) == 'Codex' and status == 'PASS':
        codex_formal_pass = _codex_formal_pass(
            lines, head=head, version=version, task_id=task['task_id'],
            run_id=task['run_id'],
            contract_digest=task['task_binding']['contract_digest'])
        if not codex_formal_pass:
            return None
    comment_id = comment.get('id')
    if type(comment_id) is not int or comment_id < 0:
        _refuse('REVIEW_RESULT_INVALID')

    formal_pass = (codex_formal_pass or
                   (marker_matches and marker.group(1) == 'Claude' and status == 'PASS' and
                    task_completion._review_family_compatible(
                        developer, 'claude-reviewer') and
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
                   re.match(r'(?i)^finding\b', line.strip()) or
                   _PRIORITY_FINDING.match(line.strip()))
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


def _completed_claim_result(task, claim, *, version):
    """Classify a completed claim's stored verdict and return its event identity."""
    task_id = task.get('task_id')
    run_id = task.get('run_id')
    binding = task.get('task_binding')
    result = claim.get('review_result')
    head = claim.get('head_sha')
    if (type(result) is not dict or result.get('task_id') != task_id or
            result.get('run_id') != run_id or type(binding) is not dict or
            result.get('contract_digest') != binding.get('contract_digest') or
            result.get('head') != head or
            result.get('verdict') not in ('PASS', 'FIX', 'FAIL', 'BLOCKED') or
            type(result.get('review_text')) is not str):
        _refuse('REVIEW_RESULT_INVALID')
    claim_stamp = claim.get('updated_at')
    latest = _comment_verdict(
        {'id': 0, 'body': result['review_text'],
         'created_at': claim_stamp, 'updated_at': claim_stamp},
        head=head, version=version, task=task)
    if latest is None:
        _refuse('REVIEW_RESULT_INVALID')
    expected_verdict = 'PASS' if latest[2] == 'REVIEW_PASS' else 'FAIL'
    if (result['verdict'] == 'PASS') != (expected_verdict == 'PASS'):
        _refuse('REVIEW_RESULT_INVALID')
    identity = {'task': task_id, 'run': run_id, 'head': head,
                'version': version, 'outcome': latest[2], 'comment_id': latest[1]}
    event_id = hashlib.sha256(json.dumps(
        identity, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()[:32]
    return latest, event_id


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

    latest = None
    event_id = None
    if claim.get('state') == 'COMPLETED':
        latest, event_id = _completed_claim_result(task, claim, version=version)
    else:
        get = task_completion._github_get if github_get is None else github_get
        comments = get(f'issues/{pr_number}/comments?per_page=100')
        if type(comments) is not list or len(comments) >= 100:
            _refuse('REVIEW_RESULT_COMMENTS_INVALID')
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
    if event_id is None:
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
