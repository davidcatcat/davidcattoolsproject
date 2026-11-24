"""Recover a proved pre-launch refusal without discarding its arbitration claim."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

from .review_repair import dispute_refuse
from .task_store import _canonical_claim_sha256, review_dispute_ref_parts


# Reviewed vulnerable implementations. These exact bytes reject the bridge's
# reviewer ID under OWNER_ASSIGNMENT_ONLY before any Claude CLI invocation.
_LEGACY_FILES = {
    'tools/llmtool/llmtool/review_dispatch.py':
        '24b96abcac1e5d90f105bd819df67a4e8e47e244d8697dcf21178467e7817046',
    'tools/llmtool/scripts/claude_bg_dispatch.py':
        '3767d55407014255a383af588ca6ed461db78a33863d5c91b53688c2aaa5b89e',
}


# Receipt-less migration approved by the independent investigation in #2087
# c7143486423 and the PM handoff. Match the entire immutable failed claim,
# not just its missing session ID. Other UNKNOWN claims need a saved receipt.
_LEGACY_REFUSALS = {
    'dbe066b40dc3a1718d156a935a7eff244d631dc19c253f8c5679aac72816a912':
        '2a55a82f99e04033421fc56e0d60b7083b63fee7',
}


def no_start_proof(claim, *, source_sha, repo_root):
    receipt = claim.get('launch_refusal')
    if receipt is not None:
        return hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
    if (_LEGACY_REFUSALS.get(_canonical_claim_sha256(claim)) != source_sha or
            claim.get('reviewer') != 'claude-reviewer' or
            claim.get('failure_reason') is not None or
            claim.get('bind_failure_reason') is not None or
            type(source_sha) is not str or re.fullmatch(r'[0-9a-f]{40}', source_sha) is None):
        dispute_refuse('ARBITER_RECOVERY_EVIDENCE_REQUIRED')

    def read(path):
        try:
            return subprocess.run(
                ['git', '-C', repo_root, 'show', f'{source_sha}:{path}'],
                stdin=subprocess.DEVNULL, capture_output=True, check=True,
                timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            dispute_refuse('ARBITER_RECOVERY_SOURCE_INVALID')

    for path, expected in _LEGACY_FILES.items():
        if hashlib.sha256(read(path)).hexdigest() != expected:
            dispute_refuse('ARBITER_RECOVERY_SOURCE_INVALID')
    try:
        policy = json.loads(read('tools/llmtool/worker-dispatch.json'))
        worker = policy['workers']['claude-reviewer']
        if policy['schema_version'] != 1 or worker['dispatch'] != 'OWNER_ASSIGNMENT_ONLY':
            dispute_refuse('ARBITER_RECOVERY_SOURCE_INVALID')
    except (ValueError, KeyError, TypeError):
        dispute_refuse('ARBITER_RECOVERY_SOURCE_INVALID')
    # The verified old bridge supplied no Owner ref, so even a matching ID
    # could not pass. The source SHA is retained in the evidence digest.
    return hashlib.sha256(source_sha.encode() + read('tools/llmtool/worker-dispatch.json')).hexdigest()


def recover(store, task, disputes, index, *, reviewer_task, evidence_ref,
            source_sha, repository, now):
    entry = disputes[index]
    claims = task.get('review_dispatch_claim', [])
    claims = [claims] if type(claims) is dict else claims
    pointer = entry.get('arbiter_claim') or {}
    found = [c for c in claims if c.get('purpose') == 'dispute_ruling' and
             c.get('reviewer_task') == reviewer_task == pointer.get('reviewer_task') and
             c.get('version') == pointer.get('version')]
    if (len(found) != 1 or found[0].get('state') != 'UNKNOWN' or
            found[0].get('native_session_id') is not None):
        dispute_refuse('ARBITER_RECOVERY_CLAIM_INVALID')
    parts = review_dispute_ref_parts(evidence_ref, repository)
    if parts is None:
        dispute_refuse('ARBITER_RECOVERY_EVIDENCE_REQUIRED')
    from .task_submission_cli import _dispute_arbiter_d6_binding
    reg = _dispute_arbiter_d6_binding(
        task, reviewer=found[0]['reviewer'], reviewer_task=reviewer_task,
        head=entry['head_sha'], require_clean=True)
    if reg.native_session_id is not None or reg.session_ref != 'pending-' + reviewer_task:
        dispute_refuse('ARBITER_RECOVERY_SESSION_EXISTS')
    proof = no_start_proof(found[0], source_sha=source_sha,
                           repo_root=str(Path(reg.common_dir).parent))
    updated = dict(entry)
    del updated['arbiter_claim']
    updated['history'] = list(entry.get('history', [])) + [{
        'event': 'ARBITER_RECOVERED', 'at': now,
        'actor': 'pm:' + task['pm_session_id'], 'head_sha': entry['head_sha'],
        'refs': {'reviewer_task': reviewer_task, 'version': pointer['version'],
                 'comment_id': parts[1], 'output_sha256': proof,
                 'claim_sha256': _canonical_claim_sha256(found[0])},
    }]
    if found[0].get('launch_refusal') is None:
        updated['history'][-1]['refs']['source_sha'] = source_sha
    updated_disputes = list(disputes)
    updated_disputes[index] = updated
    store._update(task['task_id'], trusted_arbiter_recovery=True,
                  expected={'record_sha256': task['record_sha256']},
                  changes={"review_disputes": updated_disputes})
    return {'task_id': task['task_id'], 'code': 'ARBITER_RECOVERED',
            'reviewer_task': reviewer_task, 'pr': entry['pr'],
            'finding_id': entry['finding_id'], 'head_sha': entry['head_sha']}
