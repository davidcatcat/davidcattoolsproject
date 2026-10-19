"""G3 result receipt and durable Host-wiring reconciliation for OpenCode WorkerResult.

Pure reconciliation (`reconcile`) plus bounded host-side receipt persistence
(`record_host_receipt`). Reuses WorkerJob/TaskBinding/AgentBinding/UsageLedger and
the Host's sanitized final facts; creates no controller, daemon, database or second
authority ledger. A SUCCESS receipt only means the worker run was observed complete
and consistent; it is NOT acceptance, review, QA or merge evidence (``accepted`` is
always False) and never waives independent review. The model text and exit status
alone never yield SUCCESS, and the receipt never carries model content or secrets.

Verdicts: SUCCESS, FAILED (definite wrong/failed result), BLOCKED (refused before
any launch), UNKNOWN (missing/inconsistent/unverifiable evidence; keep, never
resend to the provider — re-reconciling is permitted only when missing host-side
evidence is supplied without re-running the worker).
"""
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import secrets
import stat

from .action_contract import TaskBinding
from .opencode_worker import WorkerJob, WorkerResult
from .run_budget import BudgetStatus, CallPhase, UsageLedger, assess_budget
from .task_lifecycle import AgentBinding
from .task_review_collector import physical_directory

SUCCESS, FAILED, BLOCKED, UNKNOWN = 'SUCCESS', 'FAILED', 'BLOCKED', 'UNKNOWN'
RETURNED_CODE = 'OPENCODE_OUTPUT_RETURNED'
STORE_SCHEMA = 'llmtool.opencode.result_receipt_store.v1'
STORE_LIMIT = 256 * 1024
MAX_STORE_RECORDS = 256
MAX_DELIVERY_SESSION = 128
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

    def to_dict(self):
        return {
            'verdict': self.verdict,
            'reason': self.reason,
            'job_digest': self.job_digest,
            'task': self.task.to_dict() if type(self.task) is TaskBinding else None,
            'agent': self.agent.to_dict() if type(self.agent) is AgentBinding else None,
            'source_sha': self.source_sha,
            'worker_code': self.worker_code,
            'backend_session_id': self.backend_session_id,
            'output_sha256': self.output_sha256,
            'accepted': False,
        }


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

    Trust limit: every input is caller-held data, not an attestation. When
    ``host_final`` includes ``job_digest``, it must equal ``job.digest``; for
    mandatory same-invocation binding and durable ``seen`` persistence across
    process restarts, use ``record_host_receipt``. SUCCESS does not bind content
    to output_sha256 (the worker hashes raw stdout). A terminal (non-UNKNOWN)
    verdict records the job digest and backend session in ``seen``; UNKNOWN
    means keep and never resend to the provider (re-reconciling is permitted
    only when missing host-side evidence is supplied without re-running the
    worker). Not thread-safe on an in-memory ``seen`` set.

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
        if type(out.backend_session_id) is str and out.backend_session_id:
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
    if (job.digest in seen or (type(result.session_id) is str and result.session_id
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
    if type(host_final) is dict and 'job_digest' in host_final and host_final.get('job_digest') != job.digest:
        return _receipt(UNKNOWN, 'HOST_FACTS_DISAGREE', job, result)

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


def _validate_store_payload(data):
    if (type(data) is not dict
            or set(data) not in ({'schema', 'schema_version', 'seen', 'receipts'},
                                 {'schema', 'schema_version', 'seen', 'receipts', 'deliveries'})
            or data['schema'] != STORE_SCHEMA or type(data['schema_version']) is not int
            or data['schema_version'] != 1 or type(data['seen']) is not list
            or type(data['receipts']) is not dict or len(data['seen']) > 2 * MAX_STORE_RECORDS
            or len(data['receipts']) > MAX_STORE_RECORDS):
        raise ValueError('invalid store envelope')
    if any(type(x) is not str or not x for x in data['seen']):
        raise ValueError('invalid seen entry')
    seen_set = set(data['seen'])
    if len(seen_set) != len(data['seen']):
        raise ValueError('duplicate seen entry')
    for k, rec in data['receipts'].items():
        if (type(k) is not str or not k or type(rec) is not dict
                or rec.get('job_digest') != k
                or rec.get('verdict') not in (SUCCESS, FAILED, BLOCKED, UNKNOWN)
                or rec.get('accepted') is not False or 'content' in rec):
            raise ValueError('invalid receipt record')
        if rec.get('verdict') != UNKNOWN:
            if k not in seen_set:
                raise ValueError('terminal receipt missing from seen')
            sid = rec.get('backend_session_id')
            if type(sid) is str and sid and ('session:' + sid) not in seen_set:
                raise ValueError('terminal session missing from seen')
    if 'deliveries' in data:
        _validate_deliveries(data['deliveries'], set(data['receipts']))


def _validate_deliveries(deliveries, receipt_digests):
    if type(deliveries) is not dict or len(deliveries) > MAX_STORE_RECORDS:
        raise ValueError('invalid delivery envelope')
    for k, rec in deliveries.items():
        if (type(k) is not str or not k or k not in receipt_digests or type(rec) is not dict
                or set(rec) != {'integrator_session', 'state', 'reason', 'receipt_digest'}
                or rec.get('state') != 'DELIVERED'
                or type(rec.get('integrator_session')) is not str
                or not 0 < len(rec['integrator_session']) <= MAX_DELIVERY_SESSION
                or rec['integrator_session'] != rec['integrator_session'].strip()
                or any(ord(c) < 0x20 or ord(c) == 0x7f for c in rec['integrator_session'])
                or rec.get('reason') != 'RECEIPT_DELIVERED'
                or type(rec.get('receipt_digest')) is not str or len(rec['receipt_digest']) != 64
                or any(c not in '0123456789abcdef' for c in rec['receipt_digest'])):
            raise ValueError('invalid delivery record')


def _read_store(path):
    if not path.exists() and not path.is_symlink():
        return {'schema': STORE_SCHEMA, 'schema_version': 1, 'seen': [], 'receipts': {}}
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
            or info.st_size > STORE_LIMIT):
        raise ValueError('unsafe store file')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    try:
        fstat = os.fstat(fd)
        if (fstat.st_dev, fstat.st_ino) != (info.st_dev, info.st_ino) or fstat.st_size > STORE_LIMIT:
            raise ValueError('store inode drift')
        raw = os.read(fd, STORE_LIMIT + 1)
    finally:
        os.close(fd)
    if len(raw) > STORE_LIMIT:
        raise ValueError('store too large')
    data = json.loads(raw.decode('utf-8'))
    _validate_store_payload(data)
    return data


def _write_store(path, data):
    _validate_store_payload(data)
    encoded = (json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')
    if len(encoded) > STORE_LIMIT:
        raise ValueError('encoded store exceeds limit')
    tmp = path.parent / ('.' + path.name + '.tmp-' + secrets.token_hex(8))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(encoded)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    except Exception:
        os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.close(fd)
    os.replace(tmp, path)
    dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _expect_check(job, expect, result=None):
    if (type(expect) is not dict or set(expect) != {'task', 'agent'}
            or type(expect['task']) is not TaskBinding or type(expect['agent']) is not AgentBinding):
        return _receipt(UNKNOWN, 'EXPECTATION_INVALID', job, result)
    if not _same(expect['task'], job.task, TaskBinding):
        return _receipt(FAILED, 'TASK_IDENTITY_MISMATCH', job, result)
    if not _same(expect['agent'], job.agent, AgentBinding):
        return _receipt(FAILED, 'AGENT_IDENTITY_MISMATCH', job, result)
    return None


def _with_store_lock(job, store_path, fn, result=None):
    if type(store_path) is not str or not store_path:
        return _receipt(UNKNOWN, 'RECEIPT_STORE_INVALID', job, result)
    path = Path(store_path)
    if not path.is_absolute() or str(path) != os.path.abspath(path):
        return _receipt(UNKNOWN, 'RECEIPT_STORE_INVALID', job, result)
    physical_directory(path.parent)
    for excluded in (job.agent.root, job.agent.common_dir):
        if type(excluded) is str and excluded:
            for ex in (Path(excluded), Path(excluded).resolve()):
                if path == ex or ex in path.parents:
                    return _receipt(UNKNOWN, 'RECEIPT_STORE_INVALID', job, result)
    lock_path = path.parent / ('.' + path.name + '.lock')
    if lock_path.is_symlink():
        return _receipt(UNKNOWN, 'RECEIPT_STORE_INVALID', job, result)
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    locked = False
    try:
        lst = os.fstat(lock_fd)
        if not stat.S_ISREG(lst.st_mode) or lst.st_uid != os.getuid() or lst.st_nlink != 1:
            return _receipt(UNKNOWN, 'RECEIPT_STORE_INVALID', job, result)
        os.fchmod(lock_fd, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        locked = True
        data = _read_store(path)
        return fn(path, data)
    finally:
        try:
            if locked:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def _mutate_store(*, job, store_path, mutate, result=None):
    """Run ``mutate(data, write)`` under the existing receipt-store lock and validation.

    Adds no second store, ledger, database or daemon: ``data`` is the same already
    validated durable envelope read by ``_read_store``, and ``write(new_data)`` is the
    same validated atomic ``_write_store`` (file and parent-directory fsync) used by
    ``reserve_host_launch``/``record_host_receipt``. The callback decides whether to
    persist; nothing is written unless it calls ``write``. Private by design: the only
    caller is the G3 result-delivery adapter, so no generic public writer exists that
    could bypass the terminal-receipt immutability rule. The callback must not call
    another store function (a second ``flock`` on a new descriptor self-deadlocks).
    Returns the callback's return value, or a fail-closed ``UNKNOWN`` ResultReceipt
    when the store path, file, lock or envelope cannot be trusted. Never raises.
    """
    def _run(path, data):
        def write(new_data):
            _write_store(path, new_data)
        return mutate(data, write)

    try:
        return _with_store_lock(job, store_path, _run, result)
    except Exception:
        return _receipt(UNKNOWN, 'RECEIPT_STORE_INVALID', job, result)


def reserve_host_launch(*, job, expect, store_path):
    """Fail-closed durable pre-launch admission/reservation in the existing receipt store.

    Atomically under the store ``flock``, before releasing the lock or invoking any
    Worker/provider:
    1. Validates ``job``, mandatory ``expect`` (both ``task`` and ``agent`` bindings
       matching ``job``), and safe off-repo ``store_path``.
    2. Rejects if ``job.digest`` or ``'session:' + job.agent.session_id`` is already
       in ``seen`` (or has a persisted terminal receipt) with ``FAILED`` /
       ``DUPLICATE_RESULT``.
    3. Rejects if ``job.digest`` already has an in-flight / prior ``UNKNOWN`` record
       in ``receipts`` (returning ``UNKNOWN`` with the persisted reason; never resend
       on crash, uncertainty, or fresh Python objects).
    4. Otherwise persists an ``UNKNOWN`` / ``LAUNCH_IN_FLIGHT`` reservation record
       (syncing both the file and parent directory) and returns ``None`` to permit
       exactly one launch. Never raises.
    """
    try:
        if type(job) is not WorkerJob or _digest(job) is None:
            return _receipt(UNKNOWN, 'JOB_MISSING')
        err = _expect_check(job, expect)
        if err is not None:
            return err

        def _reserve(path, data):
            seen = set(data['seen'])
            existing = data['receipts'].get(job.digest)
            sid = getattr(job.agent, 'session_id', None)
            if (job.digest in seen
                    or (type(sid) is str and sid and ('session:' + sid) in seen)
                    or (existing is not None and existing.get('verdict') != UNKNOWN)):
                return _receipt(FAILED, 'DUPLICATE_RESULT', job)
            if existing is not None:
                reason = existing.get('reason')
                return _receipt(UNKNOWN, reason if type(reason) is str and reason else 'LAUNCH_IN_FLIGHT', job)
            pending = _receipt(UNKNOWN, 'LAUNCH_IN_FLIGHT', job)
            data['receipts'][job.digest] = pending.to_dict()
            _write_store(path, data)
            return None

        return _with_store_lock(job, store_path, _reserve)
    except Exception:
        return _receipt(UNKNOWN, 'RECEIPT_STORE_INVALID', job)


def record_host_receipt(*, job, result, host_final, observed_head, expect, store_path):
    """Host-wired reconciliation with mandatory expect, same-invocation digest, and durable seen store.

    Enforces L1-L4:
    - L3: ``expect`` is mandatory and must contain BOTH ``'task'`` (TaskBinding) and
      ``'agent'`` (AgentBinding). ``host_final`` must be a dict carrying
      ``job_digest == job.digest`` (except when ``host_final is None`` for a
      pre-admit Worker/Host rejection with no launched processes or content).
    - L4: ``store_path`` is a mode-0600 regular file inside a ``physical_directory``
      parent (outside ``job.agent.root`` and ``job.agent.common_dir``), holding durable
      ``seen`` digests/sessions and sanitized bounded receipt records across restarts.
    - L1: Duplicate replays return ``FAILED`` / ``DUPLICATE_RESULT`` to the caller,
      and NEVER overwrite an earlier ``SUCCESS`` receipt (or any terminal receipt)
      already persisted for that ``job_digest``.
    Never raises; returns ``UNKNOWN`` fail-closed on any store or input error.
    """
    try:
        if type(job) is not WorkerJob or _digest(job) is None:
            return _receipt(UNKNOWN, 'JOB_MISSING')
        if type(result) is not WorkerResult:
            return _receipt(UNKNOWN, 'RESULT_MISSING', job)
        err = _expect_check(job, expect, result)
        if err is not None:
            return err
        pre_admit = (host_final is None and result.code != RETURNED_CODE
                     and result.content is None and not result.processes)
        if not pre_admit:
            if type(host_final) is not dict or host_final.get('job_digest') != job.digest:
                return _receipt(UNKNOWN, 'HOST_FACTS_DISAGREE', job, result)

        def _record(path, data):
            seen = set(data['seen'])
            out = reconcile(job=job, result=result, host_final=host_final,
                            observed_head=observed_head, seen=seen, expect=expect)
            # L1: Never overwrite an existing terminal receipt (SUCCESS, FAILED, BLOCKED);
            # only persist when no receipt exists yet or a prior UNKNOWN is updated.
            existing = data['receipts'].get(job.digest)
            if out.reason == 'DUPLICATE_RESULT' or (existing is not None and existing.get('verdict') != UNKNOWN):
                return out
            data['seen'] = sorted(seen)
            data['receipts'][job.digest] = out.to_dict()
            _write_store(path, data)
            return out

        return _with_store_lock(job, store_path, _record, result)
    except Exception:
        return _receipt(UNKNOWN, 'RECEIPT_STORE_INVALID', job, result)
