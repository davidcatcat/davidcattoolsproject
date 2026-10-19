"""Minimal G3 result receipt: host-side reconciliation of ONE OpenCode WorkerResult.

Pure function over caller-held data; no I/O, process, Git, provider, file, queue,
ledger or daemon. It reuses WorkerJob/TaskBinding/AgentBinding/UsageLedger and the
Host's sanitized final facts. A SUCCESS receipt only means the worker run was
observed complete and consistent; it is NOT acceptance, review, QA or merge
evidence (``accepted`` is always False) and never waives independent review.
The model text and exit status alone never yield SUCCESS, and the receipt does
not carry model content.

Verdicts: SUCCESS, FAILED (definite wrong/failed result), BLOCKED (refused before
any launch), UNKNOWN (missing/inconsistent/unverifiable evidence; keep, never retry).
"""
from dataclasses import dataclass

from .action_contract import TaskBinding
from .opencode_worker import WorkerJob, WorkerResult
from .run_budget import BudgetStatus, CallPhase, UsageLedger, assess_budget
from .task_lifecycle import AgentBinding

SUCCESS, FAILED, BLOCKED, UNKNOWN = 'SUCCESS', 'FAILED', 'BLOCKED', 'UNKNOWN'
RETURNED_CODE = 'OPENCODE_OUTPUT_RETURNED'
# Refused before any launch (Host/Worker admission). Anything else unlisted is UNKNOWN.
BLOCKED_CODES = frozenset({'OPENCODE_NOT_AUTHORIZED', 'OPENCODE_BUDGET_BLOCKED',
                           'OPENCODE_JOB_INVALID', 'OPENCODE_ATTEMPT_ALREADY_USED'})
# Launched and definitively failed with cleanup/source not in doubt.
FAILED_CODES = frozenset({'OPENCODE_PROVIDER_FAILED', 'OPENCODE_IDENTITY_REUSE',
                          'OPENCODE_RUNTIME_INVALID'})


@dataclass(frozen=True)
class ResultReceipt:
    verdict: str
    reason: str
    job_digest: object = None
    task: object = None
    agent: object = None
    source_sha: object = None
    worker_code: object = None
    backend_session_id: object = None
    output_sha256: object = None
    accepted: bool = False


def _digest(job):
    try:
        return job.digest if type(job) is WorkerJob else None
    except Exception:
        return None


def _receipt(verdict, reason, job=None, result=None):
    ok = type(job) is WorkerJob and _digest(job) is not None
    res = result if type(result) is WorkerResult else None
    return ResultReceipt(verdict, reason, job.digest if ok else None, job.task if ok else None,
                         job.agent if ok else None, job.source_sha if ok else None,
                         res.code if res else None, res.session_id if res else None,
                         res.output_sha256 if res else None)


def _same(a, b, kind):
    return type(a) is kind and type(b) is kind and a.to_dict() == b.to_dict()


def _process_facts_ok(processes):
    if type(processes) is not tuple or len(processes) != 2:
        return False
    for fact, kind in zip(processes, ('server', 'client')):
        if (type(fact) is not dict or fact.get('kind') != kind or type(fact.get('exit')) is not int or fact.get('exit') != 0
                or fact.get('group_absent') is not True):
            return False
    return True


def reconcile(*, job, result, host_final, observed_head, seen, expect=None):
    """Return a ResultReceipt for one job/result pair; never raises.

    Trust limit: every input is caller-held data, not an attestation. host_final
    carries no job digest, so the caller must pass the facts of THIS invocation.
    SUCCESS does not bind content to output_sha256 (the worker hashes raw stdout).
    A terminal (non-UNKNOWN) verdict records the job digest and backend session
    in ``seen``; UNKNOWN is retryable with corrected evidence. Not thread-safe.

    host_final: sanitized Host facts from opencode_foreground_host.run().
    observed_head: HEAD the host independently read after the run (None => UNKNOWN).
    seen: caller-owned set of already-reconciled job digests (duplicate guard; it
    is the caller's in-memory set, not a ledger). expect: optional dict with
    independently held 'task' (TaskBinding) and/or 'agent' (AgentBinding).
    """
    try:
        out = _reconcile(job, result, host_final, observed_head, seen, expect)
    except Exception:
        return _receipt(UNKNOWN, 'RECONCILE_ERROR', job, result)
    if out.verdict != UNKNOWN and out.reason != 'DUPLICATE_RESULT':
        seen.add(out.job_digest)
        if type(out.backend_session_id) is str:
            seen.add('session:' + out.backend_session_id)
    return out


def _reconcile(job, result, host_final, observed_head, seen, expect):
    if type(job) is not WorkerJob or _digest(job) is None:
        return _receipt(UNKNOWN, 'JOB_MISSING')
    if type(result) is not WorkerResult:
        return _receipt(UNKNOWN, 'RESULT_MISSING', job)
    if type(seen) is not set:
        return _receipt(UNKNOWN, 'SEEN_SET_INVALID', job, result)
    if type(observed_head) is not str or not observed_head:
        return _receipt(UNKNOWN, 'SOURCE_NOT_OBSERVED', job, result)
    if (job.digest in seen or (type(result.session_id) is str
                               and 'session:' + result.session_id in seen)):
        return _receipt(FAILED, 'DUPLICATE_RESULT', job, result)
    expect = {} if expect is None else expect
    if type(expect) is not dict or set(expect) - {'task', 'agent'}:
        return _receipt(UNKNOWN, 'EXPECTATION_INVALID', job, result)
    if 'task' in expect and not _same(expect['task'], job.task, TaskBinding):
        return _receipt(FAILED, 'TASK_IDENTITY_MISMATCH', job, result)
    if 'agent' in expect and not _same(expect['agent'], job.agent, AgentBinding):
        return _receipt(FAILED, 'AGENT_IDENTITY_MISMATCH', job, result)
    if observed_head != job.source_sha:
        return _receipt(FAILED, 'SOURCE_HEAD_MISMATCH', job, result)
    if result.session_id == job.agent.session_id:
        return _receipt(FAILED, 'SESSION_IDENTITY_REUSE', job, result)

    if result.code != RETURNED_CODE:
        launched = type(host_final) is dict and host_final.get('launched')
        if result.code in BLOCKED_CODES and result.content is None and not result.processes:
            if host_final is None or (type(host_final) is dict and host_final.get('launched') is False
                                      and host_final.get('code') in (None, result.code)):
                return _receipt(BLOCKED, result.code, job, result)
            return _receipt(UNKNOWN, 'BLOCKED_FACTS_INCONSISTENT', job, result)
        if (result.code in FAILED_CODES and launched is True and result.cleanup_confirmed is True
                and type(host_final) is dict and host_final.get('code') == result.code):
            return _receipt(FAILED, result.code, job, result)
        return _receipt(UNKNOWN, result.code if type(result.code) is str else 'CODE_INVALID', job, result)

    # Claimed success: every independent fact must agree.
    if type(host_final) is not dict:
        return _receipt(UNKNOWN, 'HOST_FACTS_MISSING', job, result)
    if (host_final.get('code') != RETURNED_CODE or host_final.get('launched') is not True
            or host_final.get('one_use_consumed') is not True):
        return _receipt(UNKNOWN, 'HOST_FACTS_DISAGREE', job, result)
    if result.evidence_class != 'HOST_OBSERVED':
        return _receipt(UNKNOWN, 'EVIDENCE_NOT_HOST_OBSERVED', job, result)
    if (result.status != 'RETURNED' or type(result.content) is not str or not result.content
            or type(result.session_id) is not str or not result.session_id
            or type(result.output_sha256) is not str or not result.output_sha256):
        return _receipt(UNKNOWN, 'RESULT_INCOMPLETE', job, result)
    if result.cleanup_confirmed is not True or not _process_facts_ok(result.processes):
        return _receipt(UNKNOWN, 'CLEANUP_NOT_CONFIRMED', job, result)
    if type(result.usage) is not UsageLedger or len(result.usage.calls) <= len(job.usage.calls):
        return _receipt(UNKNOWN, 'USAGE_NOT_RECORDED', job, result)
    prior = len(job.usage.calls)
    # One Worker invocation appends one CallUsage per native assistant step (1..max_steps,
    # worker._persisted), does no repairs and cannot rewind time.
    if (not 1 <= len(result.usage.calls) - prior <= job.max_steps
            or result.usage.repair_attempts != job.usage.repair_attempts
            or type(job.usage.elapsed_ms) is not int or type(result.usage.elapsed_ms) is not int
            or result.usage.elapsed_ms < job.usage.elapsed_ms
            or assess_budget(job.budget, job.usage).status is not BudgetStatus.WITHIN):
        return _receipt(UNKNOWN, 'USAGE_NOT_CONTINUOUS', job, result)
    phase = CallPhase.PRIMARY if job.phase == 'DEVELOP' else CallPhase.REVIEW
    if (result.usage.calls[:prior] != job.usage.calls
            or any(c.phase is not phase for c in result.usage.calls[prior:])):
        return _receipt(UNKNOWN, 'USAGE_NOT_CONTINUOUS', job, result)
    status = assess_budget(job.budget, result.usage).status
    if status is BudgetStatus.UNKNOWN:
        return _receipt(UNKNOWN, 'USAGE_UNKNOWN', job, result)
    if status is not BudgetStatus.WITHIN:
        return _receipt(FAILED, 'BUDGET_EXCEEDED', job, result)
    return _receipt(SUCCESS, 'WORKER_RUN_OBSERVED', job, result)
