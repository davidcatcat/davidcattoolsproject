"""Finite G3 result delivery: durable ResultReceipt -> the SAME active foreground Integrator.

A successful Worker run is only useful if the active Integrator actually receives a
correlated, machine-readable receipt. This module is that bounded delivery step and
nothing more: no scheduler, daemon, queue, database, second ledger, Git action,
subprocess, provider call, next-stage launch or inactive-session wake.

Usage (these calls run in the Integrator's own foreground process; ``store`` is the
same durable receipt store used for admission/reconciliation)::

    from llmtool import opencode_foreground_host as host
    from llmtool import opencode_result_delivery as delivery

    result, final, receipt, delivered = host.run_with_delivery(
        job, policy, admission, one_native_credential,
        expect={'task': task, 'agent': agent},
        receipt_store=store,
        integrator_session=my_logical_integrator_session)
    if delivered.state != delivery.DELIVERED or delivered.redelivery:
        ...   # handle MISSING / NOT_DELIVERED / UNKNOWN explicitly

or, keeping the existing three-value call and delivering separately::

    result, final, receipt = host.run_with_receipt(
        job, policy, admission, one_native_credential,
        expect={'task': task, 'agent': agent}, receipt_store=store)
    delivered = delivery.deliver_result(
        job=job, receipt=receipt, expect={'task': task, 'agent': agent},
        integrator_session=my_logical_integrator_session, store_path=store)

After a crash, a lock loss or any lost in-memory receipt, the Integrator recovers from
the durable store instead of re-running anything::

    delivered = delivery.deliver_persisted(
        job=job, expect={'task': task, 'agent': agent},
        integrator_session=my_logical_integrator_session, store_path=store)

Envelope states (``delivery.state``):

- ``DELIVERED`` — the receipt already persisted in the durable store was correlated
  to ``task_id``/``agent_id``/``job_digest``/``source_sha`` and to this
  ``integrator_session``, and the delivery record was persisted first.
  ``receipt_verdict``/``receipt_reason`` always carry the real verdict, so a delivered
  ``UNKNOWN`` or ``FAILED`` receipt is never presented as a good result; that includes
  a delivered ``UNKNOWN``/``LAUNCH_IN_FLIGHT`` reservation, which is how the Integrator
  learns that a launch was reserved but no result exists.
- ``MISSING`` — no receipt was ever persisted for this job; nothing is invented and
  no Worker/provider is started to produce one.
- ``NOT_DELIVERED`` — refused before any effect (invalid job/receipt/bindings/
  expectation/session, caller receipt that disagrees with the durable record, or a
  delivery already owned by a different Integrator session).
- ``UNKNOWN`` — the durable outcome could not be verified (untrusted/corrupt or
  unsafe store path, failed write, internal error, or a delivery record that no longer
  matches the receipt digest). ``UNKNOWN`` means keep and recover; it never means
  retry a Worker or provider.

HONEST LIMITS. Delivery is a local, non-atomic hand-off: this module proves which
envelope was returned to the calling process and that the stored receipt was not
re-run to produce it. ``ack == 'CALLER_RETURNED'`` only records that the envelope
reached the caller's return path; it is not proof that a human or an Integrator
process acted on it, and ``at_most_once_effect`` stays ``NOT_CLAIMED``. If the
process dies after persisting a delivery record, ``deliver_persisted`` (or another
``deliver_result`` with the same durable receipt) returns that same record with
``redelivery: True`` (``recovery``: ``REDELIVER_PERSISTED_ENVELOPE``) and never
relaunches anything. A delivery record is never silently rebound: a different
``integrator_session`` or a changed receipt digest is refused/``UNKNOWN`` forever
until an operator decides what happened. ``accepted`` is always False: a delivered
receipt is not review, QA, security, acceptance or merge evidence.
``integrator_session`` is caller-supplied correlation data, not an attestation of the
live foreground session, and must not be a bearer secret.

STORE FORWARD COMPATIBILITY. Delivery adds an optional, strictly validated
``deliveries`` map inside the existing store envelope (same schema version). New code
reads old stores unchanged; an older binary that predates this adapter rejects a store
that already contains deliveries as an invalid envelope (fail-closed: no launch, no
relaunch, no receipt loss). There is no mixed-version/rollback support in this slice.
"""
from dataclasses import dataclass, replace
import hashlib
import json

from .action_contract import TaskBinding
from .opencode_result_receipt import (MAX_DELIVERY_SESSION, ResultReceipt, _expect_check,
                                      _mutate_store, _same)
from .opencode_worker import WorkerJob
from .task_lifecycle import AgentBinding

DELIVERY_SCHEMA = 'llmtool.opencode.result_delivery.v1'
DELIVERED, UNKNOWN, NOT_DELIVERED, MISSING = 'DELIVERED', 'UNKNOWN', 'NOT_DELIVERED', 'MISSING'
DELIVERED_REASON = 'RECEIPT_DELIVERED'
ACK_RETURNED, ACK_UNOBSERVED = 'CALLER_RETURNED', 'UNKNOWN'
AT_MOST_ONCE = 'NOT_CLAIMED'
RECOVERY = 'REDELIVER_PERSISTED_ENVELOPE'
_FROM_STORE = object()   # explicit durable-store source; never a caller-supplied receipt


@dataclass(frozen=True)
class DeliveryEnvelope:
    """One immutable, machine-readable result-delivery record. Untrusted transport data."""

    schema: str
    state: str
    reason: str
    task_id: object = None
    agent_id: object = None
    job_digest: object = None
    source_sha: object = None
    integrator_session: object = None
    receipt_verdict: object = None
    receipt_reason: object = None
    receipt_digest: object = None
    delivery_digest: object = None
    redelivery: bool = False
    worker_launches: int = 0
    provider_calls: int = 0
    accepted: bool = False
    ack: str = ACK_UNOBSERVED
    at_most_once_effect: str = AT_MOST_ONCE
    recovery: str = RECOVERY

    def to_dict(self):
        return {
            'schema': self.schema,
            'state': self.state,
            'reason': self.reason,
            'task_id': self.task_id,
            'agent_id': self.agent_id,
            'job_digest': self.job_digest,
            'source_sha': self.source_sha,
            'integrator_session': self.integrator_session,
            'receipt_verdict': self.receipt_verdict,
            'receipt_reason': self.receipt_reason,
            'receipt_digest': self.receipt_digest,
            'delivery_digest': self.delivery_digest,
            'redelivery': self.redelivery,
            'worker_launches': self.worker_launches,
            'provider_calls': self.provider_calls,
            'accepted': False,
            'ack': self.ack,
            'at_most_once_effect': self.at_most_once_effect,
            'recovery': self.recovery,
        }


def _canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('ascii')


def receipt_digest(record):
    """sha256 of one persisted receipt record (sorted canonical JSON), or None."""
    if type(record) is not dict:
        return None
    try:
        return hashlib.sha256(_canonical(record)).hexdigest()
    except Exception:
        return None


def session_ok(value):
    """True for a bounded, printable, non-blank logical Integrator session identifier."""
    return (type(value) is str and 0 < len(value) <= MAX_DELIVERY_SESSION and value == value.strip()
            and not any(ord(c) < 0x20 or ord(c) == 0x7f for c in value))


def _envelope(state, reason, *, job=None, verdict=None, verdict_reason=None,
              receipt_digest_value=None, integrator_session=None, redelivery=False):
    ok_job = type(job) is WorkerJob
    env = DeliveryEnvelope(
        schema=DELIVERY_SCHEMA, state=state, reason=reason,
        task_id=job.task.task_id if ok_job and type(job.task) is TaskBinding else None,
        agent_id=job.agent.agent_id if ok_job and type(job.agent) is AgentBinding else None,
        job_digest=job.digest if ok_job else None,
        source_sha=job.source_sha if ok_job else None,
        integrator_session=integrator_session,
        receipt_verdict=verdict,
        receipt_reason=verdict_reason,
        receipt_digest=receipt_digest_value,
        redelivery=redelivery,
        ack=ACK_RETURNED if state == DELIVERED else ACK_UNOBSERVED)
    body = env.to_dict()
    for transport_only in ('delivery_digest', 'redelivery', 'ack'):
        body.pop(transport_only)
    return replace(env, delivery_digest=hashlib.sha256(_canonical(body)).hexdigest())


def deliver_result(*, job, receipt, expect, integrator_session, store_path):
    """Deliver one already-persisted ResultReceipt to the same foreground Integrator.

    The receipt must already be persisted in the durable store and must equal the
    caller's receipt byte-for-byte, so this step can never fabricate a verdict.
    Returns a ``DeliveryEnvelope``; returns a fail-closed ``UNKNOWN`` envelope instead
    of raising, never runs a Worker/provider and never rewrites an existing receipt.
    """
    return _deliver(job=job, receipt=receipt, expect=expect,
                    integrator_session=integrator_session, store_path=store_path)


def deliver_persisted(*, job, expect, integrator_session, store_path):
    """Deliver whatever receipt the durable store already holds for ``job`` (no relaunch).

    Crash/restart recovery path: no in-memory receipt is needed or trusted. ``MISSING``
    means nothing was ever recorded for this job; ``UNKNOWN``/``FAILED`` receipts are
    delivered with their real verdict; a prior delivery is returned as ``redelivery``.
    """
    return _deliver(job=job, receipt=_FROM_STORE, expect=expect,
                    integrator_session=integrator_session, store_path=store_path)


def _deliver(*, job, receipt, expect, integrator_session, store_path):
    try:
        if type(job) is not WorkerJob or type(getattr(job, 'digest', None)) is not str or not job.digest:
            return _envelope(NOT_DELIVERED, 'JOB_INVALID')
        if receipt is not _FROM_STORE:
            if type(receipt) is not ResultReceipt:
                return _envelope(NOT_DELIVERED, 'RECEIPT_INVALID', job=job)
            if (receipt.job_digest != job.digest
                    or not _same(receipt.task, job.task, TaskBinding)
                    or not _same(receipt.agent, job.agent, AgentBinding)
                    or receipt.source_sha != job.source_sha):
                return _envelope(NOT_DELIVERED, 'RECEIPT_JOB_MISMATCH', job=job,
                                 verdict=receipt.verdict, verdict_reason=receipt.reason)
            if receipt.accepted is not False:
                # A receipt can never carry acceptance; refuse anything that claims it.
                return _envelope(NOT_DELIVERED, 'RECEIPT_ACCEPTED_INVALID', job=job,
                                 verdict=receipt.verdict, verdict_reason=receipt.reason)
        err = _expect_check(job, expect)
        if err is not None:
            return _envelope(NOT_DELIVERED, err.reason, job=job,
                             verdict=None if receipt is _FROM_STORE else getattr(receipt, 'verdict', None),
                             verdict_reason=None if receipt is _FROM_STORE else getattr(receipt, 'reason', None))
        if not session_ok(integrator_session):
            return _envelope(NOT_DELIVERED, 'INTEGRATOR_SESSION_INVALID', job=job,
                             verdict=None if receipt is _FROM_STORE else getattr(receipt, 'verdict', None),
                             verdict_reason=None if receipt is _FROM_STORE else getattr(receipt, 'reason', None))
        caller = None if receipt is _FROM_STORE else receipt.to_dict()

        def _mutate(data, write):
            try:
                return _locked(data, write, job, caller, receipt, integrator_session)
            except Exception:
                return _envelope(UNKNOWN, 'DELIVERY_INTERNAL_UNKNOWN', job=job,
                                 verdict=None if receipt is _FROM_STORE else getattr(receipt, 'verdict', None),
                                 verdict_reason=None if receipt is _FROM_STORE else getattr(receipt, 'reason', None))

        out = _mutate_store(job=job, store_path=store_path, mutate=_mutate, result=None)
        if type(out) is not DeliveryEnvelope:
            reason = getattr(out, 'reason', None)
            return _envelope(UNKNOWN, reason if type(reason) is str and reason
                             else 'RECEIPT_STORE_INVALID', job=job,
                             verdict=None if receipt is _FROM_STORE else getattr(receipt, 'verdict', None),
                             verdict_reason=None if receipt is _FROM_STORE else getattr(receipt, 'reason', None))
        return out
    except Exception:
        return _envelope(UNKNOWN, 'DELIVERY_INTERNAL_UNKNOWN')


def _record_matches_job(record, job):
    return (record.get('job_digest') == job.digest
            and record.get('task') == job.task.to_dict()
            and record.get('agent') == job.agent.to_dict()
            and record.get('source_sha') == job.source_sha)


def _locked(data, write, job, caller, receipt, integrator_session):
    persisted = data['receipts'].get(job.digest)
    if type(persisted) is not dict:
        return _envelope(MISSING, 'RECEIPT_NOT_PERSISTED', job=job)
    verdict, verdict_reason = persisted.get('verdict'), persisted.get('reason')
    if not _record_matches_job(persisted, job):
        return _envelope(NOT_DELIVERED, 'RECEIPT_JOB_MISMATCH', job=job,
                         verdict=verdict, verdict_reason=verdict_reason)
    if caller is not None and _canonical(persisted) != _canonical(caller):
        return _envelope(NOT_DELIVERED, 'RECEIPT_NOT_DURABLE', job=job,
                         verdict=verdict, verdict_reason=verdict_reason)
    digest = receipt_digest(persisted)
    deliveries = data.get('deliveries')
    prior = deliveries.get(job.digest) if type(deliveries) is dict else None
    if type(prior) is dict:
        if prior.get('receipt_digest') != digest:
            return _envelope(UNKNOWN, 'DELIVERY_DRIFT_UNKNOWN', job=job,
                             verdict=verdict, verdict_reason=verdict_reason,
                             receipt_digest_value=digest)
        if prior.get('integrator_session') != integrator_session:
            return _envelope(NOT_DELIVERED, 'INTEGRATOR_SESSION_MISMATCH', job=job,
                             verdict=verdict, verdict_reason=verdict_reason,
                             receipt_digest_value=digest)
        return _envelope(DELIVERED, DELIVERED_REASON, job=job,
                         verdict=verdict, verdict_reason=verdict_reason,
                         receipt_digest_value=digest,
                         integrator_session=integrator_session, redelivery=True)
    new_data = dict(data)
    new_data['deliveries'] = dict(deliveries or {})
    new_data['deliveries'][job.digest] = {
        'integrator_session': integrator_session,
        'state': DELIVERED,
        'reason': DELIVERED_REASON,
        'receipt_digest': digest,
    }
    try:
        write(new_data)
    except Exception:
        return _envelope(UNKNOWN, 'DELIVERY_WRITE_UNKNOWN', job=job,
                         verdict=verdict, verdict_reason=verdict_reason,
                         receipt_digest_value=digest)
    return _envelope(DELIVERED, DELIVERED_REASON, job=job,
                     verdict=verdict, verdict_reason=verdict_reason,
                     receipt_digest_value=digest, integrator_session=integrator_session)
