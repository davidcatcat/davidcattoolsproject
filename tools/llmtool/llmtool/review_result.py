"""Collect a dispatched review verdict and durably notify its original PM."""

import hashlib
import json
import os
import re
import shutil

from . import review_inline, task_completion
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
    if marker is not None and marker.group(1) == 'Claude':
        # #1898: a Claude verdict bound to another Task or run is not this Task's
        # verdict, so it is ignored as the Codex branch does, never a REVIEW_FAIL.
        binding = next((match for line in lines[1:]
                        if (match := _CODEX_BINDING.fullmatch(line.strip())) is not None), None)
        if binding is not None and (binding.group(1) != task.get('task_id') or
                                    binding.group(2) != task.get('run_id')):
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


# #1502 WP-D (spec section 4.12): stable finding IDs with a severity.
_FINDING_ID = re.compile(
    r'Finding: (F[1-9][0-9]{0,2}) \[(Blocker|High|Medium|Low|Advisory)\] \S')
NON_BLOCKING_SEVERITIES = ('Low', 'Advisory')


def finding_ids(body):
    """Map each stable finding ID to its severity, or None for a legacy verdict.

    A finding line reads "Finding: F<n> [<severity>] <file:line> <text>". A body
    with no such line, a "Finding" line without an ID, or a repeated ID is
    legacy: None, and every finding in it counts as blocking.
    """
    if type(body) is not str:
        return None
    found = {}
    for line in body.splitlines():
        text = line.strip()
        if not text.startswith('Finding'):
            continue
        match = _FINDING_ID.match(text)
        if match is None or match.group(1) in found:
            return None
        found[match.group(1)] = match.group(2)
    return found or None


def blocking_findings(body):
    """Sorted blocking finding IDs (every severity but Low and Advisory), or None if legacy."""
    found = finding_ids(body)
    if found is None:
        return None
    return sorted((fid for fid, severity in found.items()
                   if severity not in NON_BLOCKING_SEVERITIES),
                  key=lambda fid: int(fid[1:]))


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


_UTC_SECONDS = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z')


def _settle_candidate(task, claim, comments, *, head, version):
    """#1636: whether an UNKNOWN Claude review claim may settle from its one bound PASS.

    Only a claim with no failure reason and a started Reviewer session may
    settle. The PR must hold exactly one verdict bound to this claim's head
    and version, and it must be a formal Claude PASS posted after the claim.
    Anything else leaves the claim UNKNOWN. The dispatch is never replayed.
    """
    claimed_at = claim.get('claimed_at')
    if (claim.get('state') != 'UNKNOWN' or 'failure_reason' in claim or
            'binding_mismatch_keys' in claim or
            type(claim.get('reviewer_task')) is not str or
            type(claim.get('native_session_id')) is not str or
            type(claimed_at) is not str or _UTC_SECONDS.fullmatch(claimed_at) is None):
        return False
    bound = []
    for comment in comments:
        verdict = _comment_verdict(comment, head=head, version=version, task=task)
        if verdict is not None:
            bound.append((verdict, comment))
    if len(bound) != 1:
        return False
    verdict, comment = bound[0]
    posted_at = verdict[0]
    if (verdict[2] != 'REVIEW_PASS' or type(posted_at) is not str or
            _UTC_SECONDS.fullmatch(posted_at) is None or posted_at <= claimed_at or
            not task_completion.is_formal_claude_pass(
                comment.get('body'), task_id=task['task_id'], run_id=task['run_id'],
                contract_digest=task['task_binding']['contract_digest'], result_sha=head)):
        return False
    return True


def _default_common_dir():
    """#1909: the trusted canonical root's Git common dir, resolved by Git.

    The same ``rev-parse --git-common-dir`` the D6 registry uses; never the
    caller's cwd and never a joined path.
    """
    root = os.path.realpath(task_completion._trust_config().canonical_root)
    common = task_completion._git(
        root, ('rev-parse', '--path-format=absolute', '--git-common-dir'),
        limit=4096).decode('utf-8', 'strict').strip()
    if not os.path.isabs(common) or os.path.realpath(common) != common:
        _refuse('REVIEW_REVIEWER_SESSION_UNBOUND')
    return common


def _reviewer_session_bound(claim, common_dir):
    """#1909 item 2: the claim's Reviewer D6 record binds the claim's own native session.

    A missing, invalid or unreadable record, or an unbound or different
    session, is not bound. Without ``common_dir`` _default_common_dir()
    is used.
    """
    try:
        if common_dir is None:
            common_dir = _default_common_dir()
        registration = task_completion.read_d6_registration(
            claim['reviewer_task'], common_dir,
            allowed_states=task_completion._D6_EVIDENCE_STATES)
    except Exception:
        return False
    return (type(registration.native_session_id) is str and
            registration.native_session_id == claim.get('native_session_id'))


def _settle_unknown_claim(store, task, claim, comments, *, head, version):
    """#1636: settle a claim that _settle_candidate accepts; never replay the dispatch."""
    if not _settle_candidate(task, claim, comments, head=head, version=version):
        return False
    from .review_dispatch import _now
    stored = task.get('review_dispatch_claim')
    claims = [stored] if type(stored) is dict else stored
    settled = dict(claim, state='DISPATCHED', updated_at=_now())
    updated = [settled if item is claim else item for item in claims]
    try:
        store.compare_and_update(task['task_id'], expected={'review_dispatch_claim': stored},
                                 review_dispatch_claim=updated)
    except Exception:
        _refuse('REVIEW_RESULT_CLAIM_RECONCILE_FAILED')
    return True


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


_INLINE_IO_KEYS = ('file_lister', 'comment_lister', 'poster')


def _production_inline_io():
    """Build the gh api I/O for advisory inline copies; raise on any setup failure."""
    from . import review_dispatch, review_inline_io
    gh = shutil.which('gh', path=task_completion._GH_PATH)
    root = task_completion._trust_config().canonical_root
    return dict(zip(_INLINE_IO_KEYS,
                    review_inline_io.github_io(root, gh, review_dispatch._gh_env())))


def _inline_copies(pr_number, head, body, *, inline_io, github_get):
    """#1235 C6a 2b: post advisory inline copies of a new verdict's findings.

    Returns fixed counts, or None when nothing was tried. It never raises and
    never changes the verdict, the event or the claim. Without injected I/O,
    gh is called only with the production GitHub reader and a finding line.
    """
    if type(body) is not str:
        return None
    if inline_io is None:
        if github_get is not None and github_get is not task_completion._github_get:
            return None
        if not any(review_inline._LINE.fullmatch(line.strip()) for line in body.splitlines()):
            return None
    try:
        io = _production_inline_io() if inline_io is None else inline_io
        return review_inline.post_inline_copies(pr_number, head, body, **io)
    except Exception:
        return {'posted': 0, 'skipped': 0, 'failed': 1}


def record_review_result(*, store, task_id, executor_task, pr_number, version=1,
                         reviewer_session_done=False, github_get=None, inline_io=None,
                         common_dir=None):
    """Classify the latest claim-bound verdict and persist one idempotent event.

    #1909 item 2: an UNKNOWN claim that would settle needs its Reviewer D6
    to bind the claim's native session (registry under ``common_dir``).
    """
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
    latest_body = None
    event_id = None
    if claim.get('state') == 'COMPLETED':
        latest, event_id = _completed_claim_result(task, claim, version=version)
        latest_body = claim['review_result']['review_text']
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
                latest_body = comment.get('body')

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
    settle = (claim.get('state') == 'UNKNOWN' and
              _settle_candidate(task, claim, comments, head=head, version=version))
    if settle and not _reviewer_session_bound(claim, common_dir):
        # #1909 item 2: completion could never verify an unbound Reviewer
        # session, so the claim stays UNKNOWN and no event is recorded.
        _refuse('REVIEW_REVIEWER_SESSION_UNBOUND')
    result = store.record_completion_event(
        task_id, event_id=event_id, run_id=task['run_id'],
        session_id='review-' + event_id, source='REVIEWER', source_sha=head,
        sequence=0, payload_sha256=payload_sha256)
    if settle:
        _settle_unknown_claim(store, task, claim, comments, head=head, version=version)
    output = {'state': outcome, 'event_id': event_id,
              'created': result['created'], 'comment_id': comment_id,
              'head': head, 'version': version}
    if result['created'] is True and latest is not None:
        counts = _inline_copies(pr_number, head, latest_body,
                                inline_io=inline_io, github_get=github_get)
        if counts is not None:
            output['inline_copies'] = counts
    return output
