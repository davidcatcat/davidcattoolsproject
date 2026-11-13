"""Trusted-host one-shot non-interactive Claude Reviewer handoff adapter.

Dispatches a single bound review request through the existing reviewed
`ProductionReviewIssuer` + `ReceiptLedger(kind='PROVIDER_REVIEW')` +
`run_foreground_review` pipeline (`claude --print --output-format json
--no-session-persistence --safe-mode --tools WebSearch,WebFetch`), closing `stdin` on EOF so no
interactive terminal, AppleScript paste, or manual Enter keypress is ever used.

No new controller, daemon, scheduler, database, or second authority ledger is
created. Public `review_authority_factory()` remains `UnavailableReviewAuthority`.
"""

from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
import threading
from typing import Optional
import weakref

from .action_contract import ContractValue, _hex, _integer
from .authorization_ledger import ReceiptLedger
from .errors import LLMToolError
from . import worker_dispatch
from .review_authority import (
    HostPin,
    ProductionReviewIssuer,
    ReviewContext,
    run_foreground_review,
)
from .run_budget import RunBudget
from .task_review import (
    PACKET_LIMIT,
    REVIEWER_ROLES,
    canonical,
    digest,
    load_json,
    reject,
    safe_id,
    screen,
)

HANDOFF_VERDICTS = ('PASS', 'FAIL', 'BLOCKED')
EXECUTION_STATUSES = ('SUCCEEDED', 'FAILED', 'BLOCKED', 'UNKNOWN')
USAGE_STATUSES = ('KNOWN', 'UNKNOWN')
LEDGER_STATUSES = ('CONSUMED', 'EFFECT_DONE', 'EFFECT_UNKNOWN', 'DENIED', 'EXPIRED', 'NOT_CONSUMED')

_ledger_recovery_guard = threading.Lock()
_ledger_handoff_locks = weakref.WeakKeyDictionary()
_recovered_ledgers = weakref.WeakKeyDictionary()


@dataclass(frozen=True)
class ReviewHandoffReceipt(ContractValue):
    """Sanitized, host-verified one-shot Reviewer handoff receipt (DATA only)."""
    schema_version: int
    code: str
    verdict: str
    security: str
    functional: str
    execution_status: str
    authorized: bool
    execution_disposition: str
    review_authority: str
    transport_mode: str
    manual_enter_required: bool
    task_id: str
    run_id: str
    source_sha: str
    packet_digest: Optional[str]
    launch_nonce: str
    redeem_run_id: str
    grant_nonce: Optional[str]
    ledger_status: str
    recovered_receipts: int
    reviewer_role: str
    runtime_session_id: Optional[str]
    identity_source: Optional[str]
    provider_calls: int
    wrapper_launches: int
    cleanup_confirmed: bool
    usage_status: str
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    findings_count: int

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            reject('REVIEW_HANDOFF_RECEIPT_INVALID')
        safe_id(self.code)
        if (self.verdict not in HANDOFF_VERDICTS or
                self.security not in ('APPROVE', 'CHANGES_REQUIRED', 'BLOCKED') or
                self.functional not in ('APPROVE', 'CHANGES_REQUIRED', 'BLOCKED') or
                self.execution_status not in EXECUTION_STATUSES or
                self.authorized is not False or
                self.execution_disposition != 'STOP_NO_EXECUTION_AUTHORITY' or
                self.review_authority != 'WORKER_DATA_ONLY' or
                self.transport_mode != 'CLAUDE_PRINT_JSON_STDIN_EOF' or
                self.manual_enter_required is not False or
                self.ledger_status not in LEDGER_STATUSES or
                self.reviewer_role != REVIEWER_ROLES[1] or
                type(self.cleanup_confirmed) is not bool or
                self.usage_status not in USAGE_STATUSES):
            reject('REVIEW_HANDOFF_RECEIPT_INVALID')
        safe_id(self.task_id)
        _hex(self.run_id, (32,))
        _hex(self.source_sha, (40, 64))
        _hex(self.launch_nonce, (32,))
        _hex(self.redeem_run_id, (32,))
        if self.packet_digest is not None:
            _hex(self.packet_digest)
        if self.grant_nonce is not None:
            _hex(self.grant_nonce)
        if self.runtime_session_id is not None:
            safe_id(self.runtime_session_id)
        if self.identity_source is not None and self.identity_source != 'CLI_REPORTED_RUNTIME_ID':
            reject('REVIEW_HANDOFF_RECEIPT_INVALID')
        _integer(self.provider_calls, minimum=0, maximum=1)
        _integer(self.wrapper_launches, minimum=0, maximum=1)
        _integer(self.recovered_receipts, minimum=0)
        _integer(self.findings_count, minimum=0, maximum=32)
        if self.usage_status == 'KNOWN':
            _integer(self.input_tokens, minimum=0, maximum=10**12)
            _integer(self.output_tokens, minimum=0, maximum=10**12)
        elif self.input_tokens is not None or self.output_tokens is not None:
            reject('REVIEW_HANDOFF_RECEIPT_INVALID')
        if self.provider_calls > 0 and self.execution_status == 'BLOCKED':
            reject('REVIEW_HANDOFF_RECEIPT_INVALID')
        if self.verdict == 'PASS' and (
                self.security != 'APPROVE' or
                self.functional != 'APPROVE' or
                self.execution_status != 'SUCCEEDED'):
            reject('REVIEW_HANDOFF_RECEIPT_INVALID')
        if self.verdict == 'FAIL' and (
                self.execution_status != 'SUCCEEDED' or
                'CHANGES_REQUIRED' not in (self.security, self.functional) or
                'BLOCKED' in (self.security, self.functional)):
            reject('REVIEW_HANDOFF_RECEIPT_INVALID')
        if self.execution_status == 'SUCCEEDED' and (
                self.ledger_status != 'EFFECT_DONE' or
                not self.cleanup_confirmed or
                self.runtime_session_id is None or
                self.identity_source != 'CLI_REPORTED_RUNTIME_ID' or
                self.packet_digest is None or
                self.grant_nonce is None):
            reject('REVIEW_HANDOFF_RECEIPT_INVALID')
        if self.execution_status != 'SUCCEEDED' and (
                self.verdict != 'BLOCKED' or
                self.security != 'BLOCKED' or
                self.functional != 'BLOCKED'):
            reject('REVIEW_HANDOFF_RECEIPT_INVALID')
        screen(canonical(self.to_dict()).decode('utf-8'))

    @property
    def digest(self):
        return digest(self.to_dict(), 'llmtool.review-handoff-receipt.v1')


def _extract_grant_and_ledger_status(issuer, ledger, provider_calls=0):
    grant_nonce = None
    services = getattr(issuer, 'foreground_services', None)
    if services is not None:
        receipts = getattr(services.authority, 'receipts', None) or {}
        if len(receipts) == 1:
            grant_nonce = next(iter(receipts))
        elif len(receipts) > 1:
            return None, 'EFFECT_UNKNOWN'
    if grant_nonce is None:
        return None, ('EFFECT_UNKNOWN' if provider_calls > 0 else 'NOT_CONSUMED')
    try:
        status = ledger.status(grant_nonce)
    except Exception:
        return grant_nonce, 'EFFECT_UNKNOWN'
    if status not in LEDGER_STATUSES:
        return grant_nonce, 'EFFECT_UNKNOWN'
    return grant_nonce, status


def _classify_execution_status(code, provider_calls, ledger_status, cleanup_confirmed, pending_job_present, verdict_valid):
    if ledger_status in ('CONSUMED', 'EFFECT_UNKNOWN') or pending_job_present:
        return 'UNKNOWN'
    if verdict_valid and ledger_status == 'EFFECT_DONE' and cleanup_confirmed:
        return 'SUCCEEDED'
    if provider_calls == 0 and ledger_status in ('NOT_CONSUMED', 'DENIED', 'EXPIRED'):
        return 'BLOCKED'
    if code == 'REVIEW_VERDICT_INVALID' and ledger_status == 'EFFECT_DONE' and not pending_job_present:
        return 'FAILED'
    return 'UNKNOWN'


def _dispatch_review_handoff(issuer, ledger, *, budget, dispatch_store=None,
                             recovered_receipts=0):
    """Execute one non-interactive Claude Reviewer handoff without manual Enter.

    Wraps the existing `run_foreground_review(issuer, ledger, budget=budget)`
    entry point and returns `(ReviewHandoffReceipt, raw_envelope)`.
    - Consumes the one-use grant in `ReceiptLedger(kind='PROVIDER_REVIEW')` before
      spawning `claude -p`.
    - Retains `UNKNOWN` (`EFFECT_UNKNOWN`) on timeout, crash, or unconfirmed cleanup
      and never retries or automatically resends.
    - Maps dual review decisions (`security`, `functional`) to traceable
      `PASS` (`APPROVE` + `APPROVE`), `FAIL` (`CHANGES_REQUIRED`), or `BLOCKED`,
      while keeping `authorized=False` and `STOP_NO_EXECUTION_AUTHORITY`.
    """
    if (type(issuer) is not ProductionReviewIssuer or
            type(ledger) is not ReceiptLedger or
            ledger.kind != 'PROVIDER_REVIEW' or
            type(budget) is not RunBudget):
        reject('REVIEW_HOST_SERVICES_INVALID')
    c = issuer.context
    if type(c) is not ReviewContext or type(issuer.pin) is not HostPin:
        reject('REVIEW_HOST_NOT_PINNED')
    try:
        task_id = c.expect.task.task_id
        run_id = c.expect.task.run_id
        source_sha = c.expect.source_sha
    except AttributeError:
        reject('REVIEW_HOST_SERVICES_INVALID')
    launch_nonce = issuer.reviewer_launch_nonce
    redeem_run_id = issuer.redeem_run_id

    try:
        if dispatch_store is None:
            from .task_store import TaskStore
            dispatch_store = TaskStore()
        worker_dispatch.authorize_dispatch(
            dispatch_store, task_id, 'claude-reviewer', 'REVIEWER_HANDOFF')
        raw = run_foreground_review(issuer, ledger, budget=budget)
    except worker_dispatch.WorkerDispatchDenied as exc:
        raw = {
            'schema_version': 1,
            'code': exc.dispatch_code,
            'status': 'BLOCKED',
            'authorized': False,
            'provider_calls': 0,
            'invocation_status': 'NOT_CALLED',
            'verdict_validation': 'NOT_VERIFIED',
            'review_authority': 'WORKER_DATA_ONLY',
            'execution_disposition': 'STOP_NO_EXECUTION_AUTHORITY',
            'input_tokens': None,
            'output_tokens': None,
        }
    except LLMToolError as exc:
        code = getattr(exc, 'review_code', None) or 'REVIEW_HOST_SERVICES_INVALID'
        raw = {
            'schema_version': 1,
            'code': code,
            'status': 'BLOCKED',
            'authorized': False,
            'provider_calls': 1 if getattr(issuer, 'foreground_services', None) is not None and
                              bool(getattr(issuer.foreground_services.authority, 'receipts', None)) else 0,
            'invocation_status': 'NOT_CALLED',
            'verdict_validation': 'NOT_VERIFIED',
            'review_authority': 'WORKER_DATA_ONLY',
            'execution_disposition': 'STOP_NO_EXECUTION_AUTHORITY',
            'input_tokens': None,
            'output_tokens': None,
        }
    except Exception:
        raw = {
            'schema_version': 1,
            'code': 'REVIEW_PROVIDER_UNAVAILABLE',
            'status': 'BLOCKED',
            'authorized': False,
            'provider_calls': 1 if getattr(issuer, 'foreground_services', None) is not None and
                              bool(getattr(issuer.foreground_services.authority, 'receipts', None)) else 0,
            'invocation_status': 'NOT_CALLED',
            'verdict_validation': 'NOT_VERIFIED',
            'review_authority': 'WORKER_DATA_ONLY',
            'execution_disposition': 'STOP_NO_EXECUTION_AUTHORITY',
            'input_tokens': None,
            'output_tokens': None,
        }

    code = raw.get('code') if type(raw.get('code')) is str else 'REVIEW_BLOCKED'
    raw['recovered_receipts'] = recovered_receipts
    raw_calls = raw.get('provider_calls')
    provider_calls = raw_calls if type(raw_calls) is int and raw_calls in (0, 1) else 0
    grant_nonce, ledger_status = _extract_grant_and_ledger_status(issuer, ledger, provider_calls=provider_calls)
    if grant_nonce is not None and provider_calls == 0 and ledger_status in ('CONSUMED', 'EFFECT_DONE', 'EFFECT_UNKNOWN'):
        provider_calls = 1

    job_evidence = raw.get('job_evidence') if type(raw.get('job_evidence')) is dict else {}
    pending = getattr(issuer, 'pending_job', None)
    cleanup_confirmed = (
        pending is None and (
            provider_calls == 0 or (
                bool(job_evidence) and
                all(job_evidence.get(k) is True for k in ('reaped', 'pipes_drained', 'pipes_closed', 'runtime_closed'))
            )
        )
    )
    runtime_session_id = job_evidence.get('runtime_session_id') if type(job_evidence.get('runtime_session_id')) is str else None
    identity_source = job_evidence.get('identity_source') if job_evidence.get('identity_source') == 'CLI_REPORTED_RUNTIME_ID' else None
    wrapper_launches = 1 if type(job_evidence.get('wrapper_launches')) is int and job_evidence.get('wrapper_launches') == 1 else (
        1 if provider_calls == 1 and runtime_session_id else 0
    )

    packet_digest = raw.get('packet_digest') if type(raw.get('packet_digest')) is str else None
    verdict_obj = raw.get('verdict') if type(raw.get('verdict')) is dict else None
    findings_list = verdict_obj.get('findings') if verdict_obj is not None and type(verdict_obj.get('findings')) is list else None
    sec_raw = raw.get('security')
    func_raw = raw.get('functional')
    verdict_valid = (
        raw.get('verdict_validation') == 'SUCCESS' and
        verdict_obj is not None and
        verdict_obj.get('source_sha') == source_sha and
        sec_raw in ('APPROVE', 'CHANGES_REQUIRED', 'BLOCKED') and
        func_raw in ('APPROVE', 'CHANGES_REQUIRED', 'BLOCKED') and
        (sec_raw != 'APPROVE' or func_raw != 'APPROVE' or code == 'REVIEW_APPROVED') and
        packet_digest is not None and
        grant_nonce is not None and
        findings_list is not None and
        0 <= len(findings_list) <= 32 and
        cleanup_confirmed and
        bool(job_evidence) and
        all(job_evidence.get(k) is True for k in ('reaped', 'pipes_drained', 'pipes_closed', 'runtime_closed')) and
        ledger_status == 'EFFECT_DONE' and
        runtime_session_id is not None and
        identity_source == 'CLI_REPORTED_RUNTIME_ID' and
        job_evidence.get('launch_nonce') == launch_nonce
    )

    if verdict_valid:
        sec = sec_raw
        func = func_raw
        if sec == 'APPROVE' and func == 'APPROVE':
            verdict = 'PASS'
        elif 'BLOCKED' in (sec, func):
            verdict = 'BLOCKED'
        else:
            verdict = 'FAIL'
        findings_count = len(findings_list)
    else:
        sec = 'BLOCKED'
        func = 'BLOCKED'
        verdict = 'BLOCKED'
        findings_count = 0

    in_tok = raw.get('input_tokens')
    out_tok = raw.get('output_tokens')
    if type(in_tok) is int and type(out_tok) is int and 0 <= in_tok <= 10**12 and 0 <= out_tok <= 10**12:
        usage_status = 'KNOWN'
    else:
        usage_status = 'UNKNOWN'
        in_tok = None
        out_tok = None

    execution_status = _classify_execution_status(
        code, provider_calls, ledger_status, cleanup_confirmed, pending is not None, verdict_valid
    )
    if execution_status != 'SUCCEEDED':
        verdict = 'BLOCKED'
        sec = 'BLOCKED'
        func = 'BLOCKED'

    receipt = ReviewHandoffReceipt(
        schema_version=1,
        code=code,
        verdict=verdict,
        security=sec,
        functional=func,
        execution_status=execution_status,
        authorized=False,
        execution_disposition='STOP_NO_EXECUTION_AUTHORITY',
        review_authority='WORKER_DATA_ONLY',
        transport_mode='CLAUDE_PRINT_JSON_STDIN_EOF',
        manual_enter_required=False,
        task_id=task_id,
        run_id=run_id,
        source_sha=source_sha,
        packet_digest=packet_digest,
        launch_nonce=launch_nonce,
        redeem_run_id=redeem_run_id,
        grant_nonce=grant_nonce,
        ledger_status=ledger_status,
        recovered_receipts=recovered_receipts,
        reviewer_role=REVIEWER_ROLES[1],
        runtime_session_id=runtime_session_id,
        identity_source=identity_source,
        provider_calls=provider_calls,
        wrapper_launches=wrapper_launches,
        cleanup_confirmed=cleanup_confirmed,
        usage_status=usage_status,
        input_tokens=in_tok,
        output_tokens=out_tok,
        findings_count=findings_count,
    )
    return receipt, raw


def _open_handoff_lock(ledger):
    """Open the ledger's pinned directory as the cross-process handoff lock."""
    flags = (os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) |
             getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0))
    try:
        fd = os.open(os.path.dirname(ledger._path), flags)
    except OSError:
        reject('REVIEW_HOST_SERVICES_INVALID')
    try:
        st = os.fstat(fd)
        if (st.st_dev, st.st_ino) != ledger._directory:
            reject('REVIEW_HOST_SERVICES_INVALID')
    except BaseException:
        os.close(fd)
        raise
    return fd


def dispatch_review_handoff(issuer, ledger, *, budget, dispatch_store=None):
    """Recover stale redemptions once, then hold ownership through the handoff.

    The advisory flock on the ledger's directory prevents concurrent
    review_handoff hosts from recovering a receipt while another host is
    executing it. A process crash releases the lock, allowing the next open
    ledger object to recover its CONSUMED rows.
    """
    if type(ledger) is not ReceiptLedger:
        return _dispatch_review_handoff(
            issuer, ledger, budget=budget, dispatch_store=dispatch_store)
    with _ledger_recovery_guard:
        thread_lock = _ledger_handoff_locks.get(ledger)
        if thread_lock is None:
            thread_lock = threading.Lock()
            _ledger_handoff_locks[ledger] = thread_lock
    with thread_lock:
        # flock is associated with the open file description, and unlike the
        # SQLite transaction lock it remains held across the provider effect.
        # Lock the ledger's directory, never the database file: on Darwin/BSD
        # flock and SQLite's fcntl byte-range locks share one lock table, so a
        # flock on the database makes every SQLite write (recover/redeem)
        # fail LEDGER_LOCKED. Linux keeps the two lock kinds independent.
        lock_fd = _open_handoff_lock(ledger)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            if ledger not in _recovered_ledgers:
                # Record the attempt before calling out so an error cannot make
                # a later handoff retry recovery on the same open ledger.
                _recovered_ledgers[ledger] = 0
                _recovered_ledgers[ledger] = ledger.recover()
            return _dispatch_review_handoff(
                issuer, ledger, budget=budget, dispatch_store=dispatch_store,
                recovered_receipts=_recovered_ledgers[ledger])
        finally:
            os.close(lock_fd)  # releases the flock, as a crash would


def task_completion_adapter(issuer, ledger, *, budget, dispatch_store=None):
    """Bind the existing one-shot Host handoff to a task-completion callback.

    This factory is deliberately opt-in: callers must already possess the
    independently constructed Host issuer, PROVIDER_REVIEW ledger and budget.
    It does not install a CLI route or make the public ReviewAuthority factory
    available. The returned callable accepts one validated task intent and
    produces only the bounded receipt format consumed by task_completion.
    """
    if (type(issuer) is not ProductionReviewIssuer or
            type(ledger) is not ReceiptLedger or ledger.kind != 'PROVIDER_REVIEW' or
            type(budget) is not RunBudget):
        reject('REVIEW_HOST_SERVICES_INVALID')

    def adapt(intent):
        from . import task_completion

        task_completion.validate_review_dispatch_intent(intent)
        try:
            context = issuer.context
            expected = context.expect
            task = expected.task
            agents = expected.agents
            reviewers = [agent for agent in agents
                         if getattr(agent.role, 'value', agent.role) == 'REVIEWER']
            if (len(reviewers) != 1 or task.task_id != intent['task_id'] or
                    task.run_id != intent['run_id'] or
                    task.contract_digest != intent['contract_digest'] or
                    expected.source_sha != intent['source_sha']):
                reject('REVIEW_RECEIPT_BINDING_MISMATCH')
            packet_input = load_json(context.packet_input.decode('utf-8'), PACKET_LIMIT)
            packet_diff = packet_input.get('diff') if type(packet_input) is dict else None
            if (type(packet_diff) is not dict or
                    packet_input.get('source_sha') != intent['source_sha'] or
                    packet_diff.get('sha256') != intent['diff_sha256'] or
                    packet_diff.get('changed_paths') != intent['changed_paths']):
                reject('REVIEW_RECEIPT_BINDING_MISMATCH')
            reviewer = reviewers[0]
            reviewer_task_id = task.task_id
            reviewer_identity = reviewer.agent_id
            reviewer_session_ref = reviewer.session_id
            if (not reviewer_task_id or not reviewer_identity or
                    not reviewer_session_ref):
                reject('REVIEW_IDENTITY_NOT_ATTESTED')
        except LLMToolError:
            raise
        except Exception:
            reject('REVIEW_HOST_SERVICES_INVALID')

        handoff, raw = dispatch_review_handoff(
            issuer, ledger, budget=budget, dispatch_store=dispatch_store)
        severity = {'APPROVE': 'PASS', 'CHANGES_REQUIRED': 'FAIL',
                    'BLOCKED': 'BLOCKED'}
        status = handoff.execution_status
        verdict = handoff.verdict
        findings = None
        raw_verdict = raw.get('verdict') if type(raw) is dict else None
        if type(raw_verdict) is dict and type(raw_verdict.get('findings')) is list:
            findings = raw_verdict['findings']
        if findings is None:
            if handoff.findings_count != 0:
                reject('REVIEW_RECEIPT_INVALID')
            findings = []
        if len(findings) != handoff.findings_count:
            reject('REVIEW_RECEIPT_INVALID')
        receipt = {
            'schema_version': 2,
            'dispatch_id': intent['dispatch_id'],
            'task_id': intent['task_id'],
            'run_id': intent['run_id'],
            'contract_digest': intent['contract_digest'],
            'source_sha': intent['source_sha'],
            'result_head': intent['result_head'],
            'diff_sha256': intent['diff_sha256'],
            'developer_task_id': intent['developer_task_id'],
            'reviewer_task_id': reviewer_task_id,
            'reviewer_identity': reviewer_identity,
            'reviewer_session_ref': reviewer_session_ref,
            'review_packet_digest': handoff.packet_digest,
            'verdict': verdict,
            'security': severity[handoff.security],
            'functional': severity[handoff.functional],
            'execution_status': status,
            'code': handoff.code,
            'findings_sha256': hashlib.sha256(canonical(findings)).hexdigest(),
            'provenance': 'TRUSTED_REVIEW_ADAPTER',
        }
        receipt['receipt_sha256'] = hashlib.sha256(json.dumps(
            receipt, ensure_ascii=True, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()
        return task_completion.validate_review_dispatch_receipt(intent, receipt)

    return adapt
