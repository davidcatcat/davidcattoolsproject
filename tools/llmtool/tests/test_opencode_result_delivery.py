"""Offline synthetic tests for finite G3 result delivery. No provider/credential access.

Delivery reuses the existing durable ResultReceipt store; these tests prove it never
launches a worker, never invents a receipt, distinguishes redelivery from execution,
and fails closed on unsafe/tampered/concurrent/restart states.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from llmtool import opencode_result_delivery as d
from llmtool import opencode_result_receipt as r
from llmtool import opencode_worker as w
from llmtool.run_budget import CallPhase, CallUsage, UsageLedger

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import test_opencode_worker as fixtures


def facts():
    return (dict(kind='server', pid=11, host_pid=1, reaped=True, exit=0, group_absent=True),
            dict(kind='client', pid=12, host_pid=1, reaped=True, exit=0, group_absent=True))


def returned(job, session='backend-session-delivery-1'):
    return w.WorkerResult(
        'OPENCODE_OUTPUT_RETURNED', 'RETURNED', 'synthetic model text', session,
        UsageLedger(job.usage.calls + (CallUsage(CallPhase.PRIMARY, 10, 5),),
                    job.usage.repair_attempts, 100),
        True, facts(), 'a' * 64, 'HOST_OBSERVED')


def provider_failed(job):
    return w.WorkerResult(
        'OPENCODE_PROVIDER_FAILED', 'FAILED', None, None,
        UsageLedger(job.usage.calls + (CallUsage(CallPhase.PRIMARY, 10, 5),),
                    job.usage.repair_attempts, 100),
        True, facts(), None, 'HOST_OBSERVED')


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        case = fixtures.OpenCodeWorkerTests()
        self.addCleanup(case.doCleanups)
        self.job = case.job()
        self.expect = {'task': self.job.task, 'agent': self.job.agent}
        self.tmp = tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(self.tmp.cleanup)
        self.store = str(Path(self.tmp.name) / 'receipts.json')
        self.session = 'integrator-logical-session-1'

    # ---- fixtures -------------------------------------------------------
    def persist(self, result=None, *, job=None, store=None):
        job = self.job if job is None else job
        result = returned(job) if result is None else result
        final = {'code': result.code, 'launched': True, 'one_use_consumed': True,
                 'job_digest': job.digest}
        return r.record_host_receipt(job=job, result=result, host_final=final,
                                     observed_head=job.source_sha, expect=self.expect,
                                     store_path=str(self.store if store is None else store))

    def deliver(self, receipt, **changes):
        args = dict(job=self.job, receipt=receipt, expect=self.expect,
                    integrator_session=self.session, store_path=self.store)
        args.update(changes)
        return d.deliver_result(**args)

    def stored(self):
        return json.loads(Path(self.store).read_text())

    # ---- happy path -----------------------------------------------------
    def test_delivers_durable_receipt_with_correlation_and_zero_effect(self):
        rec = self.persist()
        self.assertEqual((rec.verdict, rec.reason), ('SUCCESS', 'WORKER_RUN_OBSERVED'))
        with patch.object(w, 'run_worker', side_effect=AssertionError('relaunch')) as worker, \
                patch('subprocess.Popen', side_effect=AssertionError('spawn')) as spawn:
            out = self.deliver(rec)
        worker.assert_not_called()
        spawn.assert_not_called()
        self.assertEqual((out.state, out.reason), ('DELIVERED', 'RECEIPT_DELIVERED'))
        self.assertFalse(out.redelivery)
        self.assertEqual(out.task_id, self.job.task.task_id)
        self.assertEqual(out.agent_id, self.job.agent.agent_id)
        self.assertEqual(out.job_digest, self.job.digest)
        self.assertEqual(out.source_sha, self.job.source_sha)
        self.assertEqual(out.integrator_session, self.session)
        self.assertEqual((out.receipt_verdict, out.receipt_reason), ('SUCCESS', 'WORKER_RUN_OBSERVED'))
        self.assertFalse(out.accepted)                      # delivery is not acceptance/QA/merge
        self.assertEqual((out.worker_launches, out.provider_calls), (0, 0))
        self.assertEqual(out.at_most_once_effect, 'NOT_CLAIMED')
        self.assertEqual(len(out.delivery_digest), 64)
        self.assertEqual(out.receipt_digest, d.receipt_digest(self.stored()['receipts'][self.job.digest]))
        persisted = self.stored()
        self.assertEqual(persisted['deliveries'][self.job.digest]['integrator_session'], self.session)
        self.assertEqual(persisted['receipts'][self.job.digest]['verdict'], 'SUCCESS')
        self.assertNotIn('synthetic model text', Path(self.store).read_text())

    def test_unknown_and_failed_receipts_are_delivered_truthfully(self):
        unknown = self.persist(replace(returned(self.job), cleanup_confirmed=False))
        self.assertEqual((unknown.verdict, unknown.reason), ('UNKNOWN', 'CLEANUP_NOT_CONFIRMED'))
        out = self.deliver(unknown)
        self.assertEqual(out.state, 'DELIVERED')
        self.assertEqual((out.receipt_verdict, out.receipt_reason), ('UNKNOWN', 'CLEANUP_NOT_CONFIRMED'))
        self.assertFalse(out.accepted)
        job2 = replace(self.job, prompt='second bounded prompt')
        self.assertNotEqual(job2.digest, self.job.digest)
        failed = self.persist(provider_failed(job2), job=job2)
        self.assertEqual((failed.verdict, failed.reason), ('FAILED', 'OPENCODE_PROVIDER_FAILED'))
        out2 = d.deliver_result(job=job2, receipt=failed,
                                expect={'task': job2.task, 'agent': job2.agent},
                                integrator_session=self.session, store_path=self.store)
        self.assertEqual((out2.state, out2.receipt_verdict), ('DELIVERED', 'FAILED'))
        self.assertEqual((out2.receipt_reason, out2.accepted),
                         ('OPENCODE_PROVIDER_FAILED', False))

    # ---- refusals before any effect ------------------------------------
    def test_missing_receipt_is_never_invented(self):
        receipt = r.reconcile(job=self.job, result=returned(self.job), host_final=None,
                              observed_head=self.job.source_sha, seen=set(), expect=None)
        self.assertEqual((receipt.verdict, receipt.reason), ('UNKNOWN', 'HOST_FACTS_MISSING'))
        out = self.deliver(receipt)
        self.assertEqual((out.state, out.reason), ('MISSING', 'RECEIPT_NOT_PERSISTED'))
        self.assertFalse(Path(self.store).exists())
        self.assertEqual((out.worker_launches, out.provider_calls), (0, 0))

    def test_caller_receipt_must_match_durable_record_and_bindings(self):
        rec = self.persist()
        tampered = replace(rec, reason='OTHER')
        out = self.deliver(tampered)
        self.assertEqual((out.state, out.reason), ('NOT_DELIVERED', 'RECEIPT_NOT_DURABLE'))
        other = replace(self.job, prompt='another prompt')
        self.assertEqual((self.deliver(replace(rec, job_digest=other.digest)).reason),
                         'RECEIPT_JOB_MISMATCH')
        self.assertEqual((self.deliver(replace(rec, accepted=True)).reason),
                         'RECEIPT_ACCEPTED_INVALID')
        self.assertEqual((self.deliver(replace(rec, task=replace(self.job.task, run_id='d' * 32))).reason),
                         'RECEIPT_JOB_MISMATCH')
        for bad_expect in (None, {}, {'task': self.job.task},
                           {'task': replace(self.job.task, run_id='c' * 32), 'agent': self.job.agent},
                           {'task': self.job.task, 'agent': replace(self.job.agent, session_id='other')}):
            with self.subTest(bad_expect=bad_expect):
                out = self.deliver(rec, expect=bad_expect)
                self.assertEqual(out.state, 'NOT_DELIVERED')
                self.assertIn(out.reason, ('EXPECTATION_INVALID', 'TASK_IDENTITY_MISMATCH',
                                           'AGENT_IDENTITY_MISMATCH'))
        for bad_session in ('', '   ', 7, 'bad\nsession', 'x' * 257):
            with self.subTest(bad_session=bad_session):
                out = self.deliver(rec, integrator_session=bad_session)
                self.assertEqual((out.state, out.reason),
                                 ('NOT_DELIVERED', 'INTEGRATOR_SESSION_INVALID'))
        self.assertNotIn('deliveries', self.stored())

    def test_receipt_type_and_job_type_are_validated(self):
        rec = self.persist()
        for bad in (None, 'receipt', {'verdict': 'SUCCESS'}, object()):
            with self.subTest(bad=bad):
                out = self.deliver(bad)
                self.assertEqual((out.state, out.reason), ('NOT_DELIVERED', 'RECEIPT_INVALID'))
        out = d.deliver_result(job=None, receipt=rec, expect=self.expect,
                               integrator_session=self.session, store_path=self.store)
        self.assertEqual((out.state, out.reason), ('NOT_DELIVERED', 'JOB_INVALID'))
        self.assertNotIn('deliveries', self.stored())

    # ---- replay / restart / concurrency --------------------------------
    def test_persisted_delivery_recovers_after_restart_without_receipt_or_relaunch(self):
        self.persist()                       # durable receipt; in-memory receipt then "lost"
        first = d.deliver_persisted(job=self.job, expect=self.expect,
                                    integrator_session=self.session, store_path=self.store)
        self.assertEqual((first.state, first.reason), ('DELIVERED', 'RECEIPT_DELIVERED'))
        self.assertFalse(first.redelivery)
        self.assertEqual((first.receipt_verdict, first.receipt_reason),
                         ('SUCCESS', 'WORKER_RUN_OBSERVED'))
        with patch.object(w, 'run_worker', side_effect=AssertionError('relaunch')) as worker:
            second = d.deliver_persisted(job=self.job, expect=self.expect,
                                         integrator_session=self.session, store_path=self.store)
        worker.assert_not_called()
        self.assertTrue(second.redelivery)
        self.assertEqual(second.reason, first.reason)
        self.assertEqual(first.delivery_digest, second.delivery_digest)
        self.assertEqual(first.receipt_digest, second.receipt_digest)
        self.assertEqual(len(self.stored()['deliveries']), 1)
        self.assertEqual(self.stored()['receipts'][self.job.digest]['verdict'], 'SUCCESS')

    def test_persisted_delivery_of_inflight_reservation_is_truthful_not_success(self):
        job = self.job
        self.assertIsNone(r.reserve_host_launch(job=job, expect=self.expect,
                                                store_path=self.store))
        out = d.deliver_persisted(job=job, expect=self.expect,
                                  integrator_session=self.session, store_path=self.store)
        self.assertEqual(out.state, 'DELIVERED')      # the Integrator does get the record
        self.assertEqual((out.receipt_verdict, out.receipt_reason),
                         ('UNKNOWN', 'LAUNCH_IN_FLIGHT'))
        self.assertFalse(out.accepted)

    def test_persisted_delivery_without_any_receipt_is_missing(self):
        out = d.deliver_persisted(job=self.job, expect=self.expect,
                                  integrator_session=self.session, store_path=self.store)
        self.assertEqual((out.state, out.reason), ('MISSING', 'RECEIPT_NOT_PERSISTED'))
        self.assertFalse(Path(self.store).exists())

    def test_redelivery_of_same_receipt_is_idempotent(self):
        rec = self.persist()
        first = self.deliver(rec)
        second = self.deliver(rec)
        self.assertFalse(first.redelivery)
        self.assertTrue(second.redelivery)
        self.assertEqual(first.delivery_digest, second.delivery_digest)
        self.assertEqual(len(self.stored()['deliveries']), 1)

    def test_other_integrator_session_cannot_take_over_delivery(self):
        rec = self.persist()
        self.deliver(rec)
        before = self.stored()['deliveries'][self.job.digest]
        out = self.deliver(rec, integrator_session='another-integrator-session')
        self.assertEqual((out.state, out.reason), ('NOT_DELIVERED', 'INTEGRATOR_SESSION_MISMATCH'))
        self.assertEqual(self.stored()['deliveries'][self.job.digest], before)

    def test_concurrent_delivery_writes_exactly_one_record(self):
        rec = self.persist()
        results, errors = [], []

        def run():
            try:
                results.append(self.deliver(rec))
            except BaseException as exc:   # pragma: no cover - failure detail
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 8)
        self.assertEqual(sum(1 for x in results if not x.redelivery), 1)
        self.assertEqual(sum(1 for x in results if x.redelivery), 7)
        self.assertEqual(len({x.delivery_digest for x in results}), 1)
        self.assertEqual(len(self.stored()['deliveries']), 1)

    # ---- unsafe / failing / partial stores -----------------------------
    def test_unsafe_or_corrupt_store_is_unknown_and_writes_nothing(self):
        rec = self.persist()
        base = Path(self.tmp.name)
        target = base / 'target.json'
        target.write_text('{}')
        os.chmod(target, 0o600)
        link = base / 'link.json'
        link.symlink_to(target)
        bad_mode = base / 'bad_mode.json'
        bad_mode.write_text(self.store_payload())
        os.chmod(bad_mode, 0o644)
        corrupt = base / 'corrupt.json'
        corrupt.write_text('{not json')
        os.chmod(corrupt, 0o600)
        hard = base / 'hard.json'
        hard.write_text(self.store_payload())
        os.chmod(hard, 0o600)
        os.link(hard, base / 'hard_link.json')
        for path in (link, bad_mode, corrupt, hard):
            with self.subTest(path=path.name):
                out = self.deliver(rec, store_path=str(path))
                self.assertEqual((out.state, out.reason), ('UNKNOWN', 'RECEIPT_STORE_INVALID'))
                self.assertEqual((out.worker_launches, out.provider_calls), (0, 0))

    def store_payload(self):
        return json.dumps({'schema': r.STORE_SCHEMA, 'schema_version': 1, 'seen': [],
                           'receipts': {}})

    def test_partial_write_failure_is_unknown_then_recoverable(self):
        rec = self.persist()
        before = Path(self.store).read_text()
        with patch.object(r, '_write_store', side_effect=OSError('synthetic failure')):
            out = self.deliver(rec)
        self.assertEqual((out.state, out.reason), ('UNKNOWN', 'DELIVERY_WRITE_UNKNOWN'))
        self.assertEqual(Path(self.store).read_text(), before)
        self.assertNotIn('deliveries', self.stored())
        recovered = self.deliver(rec)
        self.assertEqual((recovered.state, recovered.reason), ('DELIVERED', 'RECEIPT_DELIVERED'))
        self.assertFalse(recovered.redelivery)

    def test_crash_after_write_recovers_as_redelivery_without_execution(self):
        rec = self.persist()
        self.deliver(rec)                     # effect persisted; caller "crashed" before ack
        with patch.object(w, 'run_worker', side_effect=AssertionError('relaunch')) as worker:
            out = self.deliver(rec)
        worker.assert_not_called()
        self.assertTrue(out.redelivery)
        self.assertEqual(out.state, 'DELIVERED')
        self.assertEqual(out.at_most_once_effect, 'NOT_CLAIMED')
        self.assertEqual(self.stored()['receipts'][self.job.digest]['verdict'], 'SUCCESS')

    def test_envelope_digest_detects_store_tampering_after_delivery(self):
        rec = self.persist()
        out = self.deliver(rec)
        data = self.stored()
        data['deliveries'][self.job.digest]['receipt_digest'] = 'f' * 64
        Path(self.store).write_text(json.dumps(data))
        os.chmod(self.store, 0o600)
        drifted = self.deliver(rec)
        self.assertEqual((drifted.state, drifted.reason), ('UNKNOWN', 'DELIVERY_DRIFT_UNKNOWN'))
        self.assertEqual(out.state, 'DELIVERED')


class EnvelopeShapeTests(unittest.TestCase):
    def test_envelope_is_machine_readable_and_bounded(self):
        case = fixtures.OpenCodeWorkerTests()
        self.addCleanup(case.doCleanups)
        job = case.job()
        with tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve())) as tmp:
            store = str(Path(tmp) / 'receipts.json')
            final = {'code': 'OPENCODE_OUTPUT_RETURNED', 'launched': True,
                     'one_use_consumed': True, 'job_digest': job.digest}
            expect = {'task': job.task, 'agent': job.agent}
            rec = r.record_host_receipt(job=job, result=returned(job), host_final=final,
                                        observed_head=job.source_sha, expect=expect,
                                        store_path=store)
            out = d.deliver_result(job=job, receipt=rec, expect=expect,
                                   integrator_session='integrator-1', store_path=store)
            data = out.to_dict()
            self.assertEqual(data['schema'], d.DELIVERY_SCHEMA)
            self.assertEqual(set(data), {'schema', 'state', 'reason', 'task_id', 'agent_id',
                                         'job_digest', 'source_sha', 'integrator_session',
                                         'receipt_verdict', 'receipt_reason', 'receipt_digest',
                                         'delivery_digest', 'redelivery', 'worker_launches',
                                         'provider_calls', 'accepted', 'ack',
                                         'at_most_once_effect', 'recovery'})
            self.assertIsInstance(json.dumps(data), str)
            self.assertLess(len(json.dumps(data)), 2048)
            self.assertEqual(out.ack, 'CALLER_RETURNED')
            self.assertEqual(out.recovery, 'REDELIVER_PERSISTED_ENVELOPE')


if __name__ == '__main__':
    unittest.main()
