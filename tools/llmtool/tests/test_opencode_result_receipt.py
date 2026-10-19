"""Offline synthetic tests for the G3 minimal result receipt. No provider/credential access."""
import sys
import unittest
from dataclasses import replace
from pathlib import Path

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


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        case = fixtures.OpenCodeWorkerTests()
        self.addCleanup(case.doCleanups)
        self.job = case.job()
        self.good = w.WorkerResult(
            'OPENCODE_OUTPUT_RETURNED', 'RETURNED', 'synthetic model text', 'backend-session-1',
            UsageLedger(self.job.usage.calls + (CallUsage(CallPhase.PRIMARY, 10, 5),),
                        self.job.usage.repair_attempts, 100),
            True, facts(), 'a' * 64, 'HOST_OBSERVED')
        self.final = {'code': 'OPENCODE_OUTPUT_RETURNED', 'launched': True, 'one_use_consumed': True}

    def receipt(self, **kw):
        args = dict(job=self.job, result=self.good, host_final=self.final,
                    observed_head=self.job.source_sha, seen=set())
        args.update(kw)
        return r.reconcile(**args)

    def test_success_is_receipt_not_acceptance(self):
        x = self.receipt()
        self.assertEqual(x.verdict, 'SUCCESS')
        self.assertFalse(x.accepted)  # independent QA/review still required
        self.assertEqual(x.job_digest, self.job.digest)
        self.assertEqual(x.task, self.job.task)
        self.assertEqual(x.agent, self.job.agent)
        self.assertEqual(x.source_sha, self.job.source_sha)

    def test_missing_or_wrong_type_is_unknown(self):
        for kw in (dict(result=None), dict(host_final=None), dict(host_final={}),
                   dict(result='text'), dict(job=None), dict(observed_head=None)):
            with self.subTest(kw=kw):
                self.assertEqual(self.receipt(**kw).verdict, 'UNKNOWN')

    def test_exit_zero_or_model_text_alone_is_not_success(self):
        for bad in (replace(self.good, content=None), replace(self.good, content=''),
                    replace(self.good, status='BLOCKED'), replace(self.good, cleanup_confirmed=False),
                    replace(self.good, processes=()), replace(self.good, output_sha256=None),
                    replace(self.good, session_id=None),
                    replace(self.good, processes=(facts()[0],)),
                    replace(self.good, processes=(facts()[0], {**facts()[1], 'group_absent': False})),
                    replace(self.good, processes=(facts()[0], {**facts()[1], 'exit': 1}))):
            with self.subTest(bad=bad):
                self.assertNotEqual(self.receipt(result=bad).verdict, 'SUCCESS')

    def test_offline_fixture_evidence_never_success(self):
        x = self.receipt(result=replace(self.good, evidence_class='OFFLINE_FIXTURE'))
        self.assertEqual(x.verdict, 'UNKNOWN')

    def test_identity_mismatch_fails(self):
        for expected in (dict(task=replace(self.job.task, run_id='b' * 32)),
                         dict(agent=replace(self.job.agent, session_id='other-session'))):
            with self.subTest(expected=expected):
                self.assertEqual(self.receipt(expect=expected).verdict, 'FAILED')
        self.assertEqual(self.receipt(expect=dict(task=self.job.task, agent=self.job.agent)).verdict, 'SUCCESS')

    def test_backend_session_reuse_of_assignment_fails(self):
        bad = replace(self.good, session_id=self.job.agent.session_id)
        self.assertEqual(self.receipt(result=bad).verdict, 'FAILED')

    def test_duplicate_result_fails_and_is_recorded_once(self):
        seen = set()
        self.assertEqual(self.receipt(seen=seen).verdict, 'SUCCESS')
        self.assertEqual(self.receipt(seen=seen).verdict, 'FAILED')

    def test_host_final_must_agree_with_result(self):
        for final in ({**self.final, 'code': 'OPENCODE_SOURCE_UNKNOWN'},
                      {**self.final, 'launched': False},
                      {**self.final, 'one_use_consumed': False}):
            with self.subTest(final=final):
                self.assertNotEqual(self.receipt(host_final=final).verdict, 'SUCCESS')

    def test_source_head_drift_fails(self):
        other = 'd' * 40 if self.job.source_sha != 'd' * 40 else 'e' * 40
        self.assertEqual(self.receipt(observed_head=other).verdict, 'FAILED')

    def test_usage_unknown_or_unchanged_is_not_success(self):
        unknown = UsageLedger(self.job.usage.calls + (CallUsage(CallPhase.PRIMARY, None, None),),
                              self.job.usage.repair_attempts, 100)
        self.assertEqual(self.receipt(result=replace(self.good, usage=unknown)).verdict, 'UNKNOWN')
        self.assertNotEqual(self.receipt(result=replace(self.good, usage=self.job.usage)).verdict, 'SUCCESS')

    def test_failure_code_classification(self):
        def blocked(code, launched, status='BLOCKED'):
            res = replace(self.good, code=code, status=status, content=None, processes=facts() if launched else ())
            fin = {'code': code, 'launched': launched, 'one_use_consumed': launched}
            return self.receipt(result=res, host_final=fin).verdict
        for code in ('OPENCODE_NOT_AUTHORIZED', 'OPENCODE_BUDGET_BLOCKED', 'OPENCODE_JOB_INVALID',
                     'OPENCODE_ATTEMPT_ALREADY_USED'):
            with self.subTest(code=code):
                self.assertEqual(blocked(code, False), 'BLOCKED')
        self.assertEqual(blocked('OPENCODE_PROVIDER_FAILED', True), 'FAILED')
        for code in ('OPENCODE_CLEANUP_UNKNOWN', 'OPENCODE_SOURCE_UNKNOWN', 'OPENCODE_USAGE_UNKNOWN',
                     'OPENCODE_RUNTIME_UNKNOWN', 'OPENCODE_HOST_UNKNOWN', 'OPENCODE_FINALIZE_UNKNOWN',
                     'SOMETHING_NEW'):
            with self.subTest(code=code):
                self.assertEqual(blocked(code, True), 'UNKNOWN')

    def test_unknown_is_retryable_but_terminal_is_recorded(self):
        seen = set()
        self.assertEqual(self.receipt(seen=seen, observed_head='').verdict, 'UNKNOWN')
        self.assertEqual(self.receipt(seen=seen, host_final=None).verdict, 'UNKNOWN')
        self.assertEqual(self.receipt(seen=seen).verdict, 'SUCCESS')
        self.assertEqual(self.receipt(seen=seen).verdict, 'FAILED')

    def test_same_backend_session_under_another_job_is_duplicate(self):
        seen = set()
        self.assertEqual(self.receipt(seen=seen).verdict, 'SUCCESS')
        other = replace(self.job, prompt='different prompt')
        res = self.receipt(job=other, seen=seen)
        self.assertEqual((res.verdict, res.reason), ('FAILED', 'DUPLICATE_RESULT'))

    def test_usage_must_extend_job_usage_with_matching_phase(self):
        wrong_phase = UsageLedger(self.job.usage.calls + (CallUsage(CallPhase.REVIEW, 1, 1),),
                                  self.job.usage.repair_attempts, 100)
        self.assertEqual(self.receipt(result=replace(self.good, usage=wrong_phase)).verdict, 'UNKNOWN')

    def test_process_exit_must_be_int_zero(self):
        for exit_value in (False, None, 1):
            with self.subTest(exit_value=exit_value):
                bad = replace(self.good, processes=(facts()[0], {**facts()[1], 'exit': exit_value}))
                self.assertNotEqual(self.receipt(result=bad).verdict, 'SUCCESS')
        self.assertNotEqual(self.receipt(result=replace(self.good, processes=facts()[::-1])).verdict, 'SUCCESS')

    def test_admission_unknown_is_unknown_and_blocked_requires_not_launched(self):
        res = replace(self.good, code='OPENCODE_HOST_ADMISSION_UNKNOWN', status='BLOCKED',
                      content=None, processes=())
        fin = {'code': res.code, 'launched': False, 'one_use_consumed': False}
        self.assertEqual(self.receipt(result=res, host_final=fin).verdict, 'UNKNOWN')
        res = replace(res, code='OPENCODE_NOT_AUTHORIZED')
        self.assertEqual(self.receipt(result=res, host_final={**fin, 'code': res.code}).verdict, 'BLOCKED')
        self.assertEqual(self.receipt(result=res, host_final={**fin, 'code': res.code, 'launched': True}).verdict,
                         'UNKNOWN')

    def test_failed_code_requires_confirmed_cleanup(self):
        res = replace(self.good, code='OPENCODE_PROVIDER_FAILED', status='BLOCKED', content=None,
                      cleanup_confirmed=False)
        fin = {'code': res.code, 'launched': True, 'one_use_consumed': True}
        self.assertEqual(self.receipt(result=res, host_final=fin).verdict, 'UNKNOWN')

    def test_malformed_inputs_never_raise(self):
        bad_job = replace(self.job, prompt=None)
        self.assertEqual(self.receipt(job=bad_job).verdict, 'UNKNOWN')
        self.assertEqual(self.receipt(expect='x').verdict, 'UNKNOWN')
        self.assertEqual(self.receipt(expect=dict(task=object())).verdict, 'FAILED')
        self.assertEqual(self.receipt(result=replace(self.good, usage=object())).verdict, 'UNKNOWN')
        self.assertEqual(self.receipt(result=replace(self.good, processes=[1])).verdict, 'UNKNOWN')
        self.assertEqual(self.receipt(seen=[]).verdict, 'UNKNOWN')

    def test_ledger_continuity_repair_elapsed_and_single_call(self):
        calls = self.good.usage.calls
        prior = self.job.usage
        ok = self.good.usage
        self.assertEqual(self.receipt().verdict, 'SUCCESS')
        cases = {
            'repair_reset': UsageLedger(calls, prior.repair_attempts + 1, ok.elapsed_ms),
            'elapsed_none': UsageLedger(calls, prior.repair_attempts, None),
            'extra_review_call': UsageLedger(prior.calls + (CallUsage(CallPhase.REVIEW, 1, 1), CallUsage(CallPhase.PRIMARY, 10, 5)),
                                             prior.repair_attempts, ok.elapsed_ms),
            'extra_review_after_primary': UsageLedger(prior.calls + (CallUsage(CallPhase.PRIMARY, 10, 5), CallUsage(CallPhase.REVIEW, 1, 1)),
                                                      prior.repair_attempts, ok.elapsed_ms),
            'no_new_call': UsageLedger(prior.calls, prior.repair_attempts, ok.elapsed_ms),
        }
        for name, ledger in cases.items():
            with self.subTest(name=name):
                self.assertEqual(self.receipt(result=replace(self.good, usage=ledger)).verdict, 'UNKNOWN')

    def test_prior_calls_prefix_must_be_unchanged(self):
        job = replace(self.job, usage=UsageLedger((CallUsage(CallPhase.PRIMARY, 1, 1),), 0, 0))
        new = CallUsage(CallPhase.PRIMARY, 10, 5)
        good = UsageLedger(job.usage.calls + (new,), 0, 100)
        self.assertEqual(self.receipt(job=job, result=replace(self.good, usage=good)).verdict, 'SUCCESS')
        rewritten = UsageLedger((CallUsage(CallPhase.PRIMARY, 2, 2), new), 0, 100)
        self.assertEqual(self.receipt(job=job, result=replace(self.good, usage=rewritten)).verdict, 'UNKNOWN')

    def test_elapsed_must_not_go_backwards(self):
        job = replace(self.job, usage=UsageLedger(self.job.usage.calls, 0, 50))
        back = UsageLedger(job.usage.calls + (CallUsage(CallPhase.PRIMARY, 10, 5),), 0, 10)
        self.assertEqual(self.receipt(job=job, result=replace(self.good, usage=back)).verdict, 'UNKNOWN')
        same = UsageLedger(back.calls, 0, 50)
        self.assertEqual(self.receipt(job=job, result=replace(self.good, usage=same)).verdict, 'SUCCESS')
        none_prior = replace(self.job, usage=UsageLedger(self.job.usage.calls, 0, None))
        self.assertEqual(self.receipt(job=none_prior).verdict, 'UNKNOWN')

    def test_already_over_budget_job_with_reset_counter_is_not_success(self):
        over = replace(self.job, usage=UsageLedger(self.job.usage.calls, self.job.budget.max_repair_attempts + 1,
                                                  self.job.usage.elapsed_ms))
        reset = UsageLedger(over.usage.calls + (CallUsage(CallPhase.PRIMARY, 10, 5),), 0, 100)
        x = self.receipt(job=over, result=replace(self.good, usage=reset))
        self.assertEqual((x.verdict, x.reason), ('UNKNOWN', 'USAGE_NOT_CONTINUOUS'))

    def test_legitimate_multistep_run_is_success_within_max_steps(self):
        prior = self.job.usage
        many = lambda n: UsageLedger(prior.calls + (CallUsage(CallPhase.PRIMARY, 10, 5),) * n,
                                     prior.repair_attempts, 100)
        self.assertEqual(self.receipt(result=replace(self.good, usage=many(2))).verdict, 'SUCCESS')
        too_many = many(self.job.max_steps + 1)
        self.assertNotEqual(self.receipt(result=replace(self.good, usage=too_many)).verdict, 'SUCCESS')

    def test_result_ledger_exceeding_budget_fails(self):
        big = UsageLedger(self.job.usage.calls + (CallUsage(CallPhase.PRIMARY, 10**6, 5),),
                          self.job.usage.repair_attempts, 100)
        self.assertEqual(self.receipt(result=replace(self.good, usage=big)).verdict, 'FAILED')

    def test_receipt_carries_no_content_or_secrets(self):
        x = self.receipt()
        self.assertNotIn('synthetic model text', repr(x))
        self.assertIsNone(getattr(x, 'content', None))

    def test_host_final_job_digest_mismatch_is_unknown(self):
        with_digest = {**self.final, 'job_digest': self.job.digest}
        self.assertEqual(self.receipt(host_final=with_digest).verdict, 'SUCCESS')
        wrong_digest = {**self.final, 'job_digest': 'f' * 64}
        x = self.receipt(host_final=wrong_digest)
        self.assertEqual((x.verdict, x.reason), ('UNKNOWN', 'HOST_FACTS_DISAGREE'))
        blocked_res = replace(self.good, code='OPENCODE_NOT_AUTHORIZED', status='BLOCKED',
                              content=None, processes=())
        blocked_wrong = {'code': 'OPENCODE_NOT_AUTHORIZED', 'launched': False,
                         'one_use_consumed': False, 'job_digest': 'f' * 64}
        self.assertEqual(self.receipt(result=blocked_res, host_final=blocked_wrong).verdict, 'UNKNOWN')

    def test_record_host_receipt_requires_both_expect_bindings_and_invocation_digest(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(tmp.cleanup)
        store = Path(tmp.name) / 'receipts.json'
        fin = {**self.final, 'job_digest': self.job.digest}
        exp = {'task': self.job.task, 'agent': self.job.agent}
        for bad_exp in (None, {}, {'task': self.job.task}, {'agent': self.job.agent},
                        {'task': self.job.task, 'agent': self.job.agent, 'extra': 1}):
            with self.subTest(bad_exp=bad_exp):
                x = r.record_host_receipt(job=self.job, result=self.good, host_final=fin,
                                          observed_head=self.job.source_sha, expect=bad_exp,
                                          store_path=str(store))
                self.assertEqual((x.verdict, x.reason), ('UNKNOWN', 'EXPECTATION_INVALID'))
                self.assertFalse(store.exists())
        no_digest = dict(self.final)
        x = r.record_host_receipt(job=self.job, result=self.good, host_final=no_digest,
                                  observed_head=self.job.source_sha, expect=exp,
                                  store_path=str(store))
        self.assertEqual((x.verdict, x.reason), ('UNKNOWN', 'HOST_FACTS_DISAGREE'))

    def test_durable_store_prevents_replay_across_restart_and_preserves_prior_success(self):
        import json, os, stat, tempfile
        tmp = tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(tmp.cleanup)
        store = Path(tmp.name) / 'receipts.json'
        fin = {**self.final, 'job_digest': self.job.digest}
        exp = {'task': self.job.task, 'agent': self.job.agent}
        first = r.record_host_receipt(job=self.job, result=self.good, host_final=fin,
                                      observed_head=self.job.source_sha, expect=exp,
                                      store_path=str(store))
        self.assertEqual((first.verdict, first.reason), ('SUCCESS', 'WORKER_RUN_OBSERVED'))
        self.assertFalse(first.accepted)
        st = os.lstat(store)
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)
        persisted_before = json.loads(store.read_text())
        self.assertEqual(persisted_before['receipts'][self.job.digest]['verdict'], 'SUCCESS')
        self.assertNotIn('synthetic model text', store.read_text())
        # Simulated restart / replay of the same job or same backend session:
        # returns FAILED DUPLICATE_RESULT to caller, but NEVER overwrites prior SUCCESS in store.
        dup = r.record_host_receipt(job=self.job, result=self.good, host_final=fin,
                                    observed_head=self.job.source_sha, expect=exp,
                                    store_path=str(store))
        self.assertEqual((dup.verdict, dup.reason), ('FAILED', 'DUPLICATE_RESULT'))
        persisted_after = json.loads(store.read_text())
        self.assertEqual(persisted_after['receipts'][self.job.digest]['verdict'], 'SUCCESS')
        self.assertEqual(persisted_after['receipts'][self.job.digest]['reason'], 'WORKER_RUN_OBSERVED')
        # Replaying the same backend session under a different job is also caught across restart.
        other_job = replace(self.job, prompt='second prompt')
        other_fin = {**self.final, 'job_digest': other_job.digest}
        dup_ses = r.record_host_receipt(job=other_job, result=self.good, host_final=other_fin,
                                        observed_head=other_job.source_sha, expect=exp,
                                        store_path=str(store))
        self.assertEqual((dup_ses.verdict, dup_ses.reason), ('FAILED', 'DUPLICATE_RESULT'))

    def test_durable_store_rejects_symlink_bad_mode_or_corrupt_file_fail_closed(self):
        import os, tempfile
        tmp = tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(tmp.cleanup)
        fin = {**self.final, 'job_digest': self.job.digest}
        exp = {'task': self.job.task, 'agent': self.job.agent}
        target = Path(tmp.name) / 'target.json'
        target.write_text('{}')
        os.chmod(target, 0o600)
        link = Path(tmp.name) / 'link.json'
        link.symlink_to(target)
        x = r.record_host_receipt(job=self.job, result=self.good, host_final=fin,
                                  observed_head=self.job.source_sha, expect=exp,
                                  store_path=str(link))
        self.assertEqual((x.verdict, x.reason), ('UNKNOWN', 'RECEIPT_STORE_INVALID'))
        bad_mode = Path(tmp.name) / 'bad_mode.json'
        bad_mode.write_text('{}')
        os.chmod(bad_mode, 0o644)
        x2 = r.record_host_receipt(job=self.job, result=self.good, host_final=fin,
                                   observed_head=self.job.source_sha, expect=exp,
                                   store_path=str(bad_mode))
        self.assertEqual((x2.verdict, x2.reason), ('UNKNOWN', 'RECEIPT_STORE_INVALID'))
        corrupt = Path(tmp.name) / 'corrupt.json'
        corrupt.write_text('{not-json')
        os.chmod(corrupt, 0o600)
        x3 = r.record_host_receipt(job=self.job, result=self.good, host_final=fin,
                                   observed_head=self.job.source_sha, expect=exp,
                                   store_path=str(corrupt))
        self.assertEqual((x3.verdict, x3.reason), ('UNKNOWN', 'RECEIPT_STORE_INVALID'))
        # Store inside agent.root or agent.common_dir must be rejected fail-closed.
        in_root = str(Path(self.job.agent.root) / 'receipts.json')
        x4 = r.record_host_receipt(job=self.job, result=self.good, host_final=fin,
                                   observed_head=self.job.source_sha, expect=exp,
                                   store_path=in_root)
        self.assertEqual((x4.verdict, x4.reason), ('UNKNOWN', 'RECEIPT_STORE_INVALID'))
        in_common = str(Path(self.job.agent.common_dir) / 'receipts.json')
        x5 = r.record_host_receipt(job=self.job, result=self.good, host_final=fin,
                                   observed_head=self.job.source_sha, expect=exp,
                                   store_path=in_common)
        self.assertEqual((x5.verdict, x5.reason), ('UNKNOWN', 'RECEIPT_STORE_INVALID'))

    def test_durable_store_unknown_re_reconciles_to_success_and_preserves_terminal_verdicts(self):
        import json, tempfile
        tmp = tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(tmp.cleanup)
        store = Path(tmp.name) / 'receipts.json'
        fin = {**self.final, 'job_digest': self.job.digest}
        exp = {'task': self.job.task, 'agent': self.job.agent}
        # First reconciliation has missing observed_head -> UNKNOWN persisted, not in seen.
        u = r.record_host_receipt(job=self.job, result=self.good, host_final=fin,
                                  observed_head=None, expect=exp, store_path=str(store))
        self.assertEqual((u.verdict, u.reason), ('UNKNOWN', 'SOURCE_NOT_OBSERVED'))
        self.assertEqual(json.loads(store.read_text())['receipts'][self.job.digest]['verdict'], 'UNKNOWN')
        # Supplying the missing host-side evidence without re-running the worker updates UNKNOWN -> SUCCESS.
        s = r.record_host_receipt(job=self.job, result=self.good, host_final=fin,
                                  observed_head=self.job.source_sha, expect=exp, store_path=str(store))
        self.assertEqual((s.verdict, s.reason), ('SUCCESS', 'WORKER_RUN_OBSERVED'))
        self.assertEqual(json.loads(store.read_text())['receipts'][self.job.digest]['verdict'], 'SUCCESS')
        # A terminal BLOCKED or FAILED receipt for another job is also preserved across replay.
        blocked_job = replace(self.job, prompt='blocked job prompt')
        blocked_res = replace(self.good, code='OPENCODE_NOT_AUTHORIZED', status='BLOCKED',
                              content=None, session_id=None, processes=())
        b1 = r.record_host_receipt(job=blocked_job, result=blocked_res, host_final=None,
                                   observed_head=blocked_job.source_sha, expect=exp, store_path=str(store))
        self.assertEqual((b1.verdict, b1.reason), ('BLOCKED', 'OPENCODE_NOT_AUTHORIZED'))
        b2 = r.record_host_receipt(job=blocked_job, result=blocked_res, host_final=None,
                                   observed_head=blocked_job.source_sha, expect=exp, store_path=str(store))
        self.assertEqual((b2.verdict, b2.reason), ('FAILED', 'DUPLICATE_RESULT'))
        self.assertEqual(json.loads(store.read_text())['receipts'][blocked_job.digest]['verdict'], 'BLOCKED')

    def test_reserve_host_launch_persists_in_flight_blocks_known_session_and_fsyncs_parent(self):
        import json, os, tempfile
        from unittest.mock import patch
        tmp = tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(tmp.cleanup)
        store = Path(tmp.name) / 'reserve.json'
        fin = {**self.final, 'job_digest': self.job.digest}
        exp = {'task': self.job.task, 'agent': self.job.agent}
        fsynced_fds = []
        orig_fsync = os.fsync

        def track_fsync(fd):
            fsynced_fds.append(os.fstat(fd).st_ino)
            return orig_fsync(fd)

        with patch.object(os, 'fsync', side_effect=track_fsync):
            self.assertIsNone(r.reserve_host_launch(job=self.job, expect=exp, store_path=str(store)))
        self.assertIn(store.parent.stat().st_ino, fsynced_fds)
        persisted = json.loads(store.read_text())
        self.assertEqual(persisted['receipts'][self.job.digest]['verdict'], 'UNKNOWN')
        self.assertEqual(persisted['receipts'][self.job.digest]['reason'], 'LAUNCH_IN_FLIGHT')
        # Second reservation while in-flight / UNKNOWN is refused without resending.
        in_flight = r.reserve_host_launch(job=replace(self.job), expect=exp, store_path=str(store))
        self.assertEqual((in_flight.verdict, in_flight.reason), ('UNKNOWN', 'LAUNCH_IN_FLIGHT'))
        # Post-run reconciliation with host-side evidence promotes LAUNCH_IN_FLIGHT -> SUCCESS.
        s = r.record_host_receipt(job=self.job, result=self.good, host_final=fin,
                                  observed_head=self.job.source_sha, expect=exp, store_path=str(store))
        self.assertEqual((s.verdict, s.reason), ('SUCCESS', 'WORKER_RUN_OBSERVED'))
        # Subsequent reservation after terminal verdict returns FAILED DUPLICATE_RESULT.
        dup = r.reserve_host_launch(job=replace(self.job), expect=exp, store_path=str(store))
        self.assertEqual((dup.verdict, dup.reason), ('FAILED', 'DUPLICATE_RESULT'))
        # Known backend session reuse as agent.session_id in a new job is refused pre-launch.
        reused_agent = replace(self.job.agent, session_id=self.good.session_id)
        reused_job = replace(self.job, agent=reused_agent, prompt='new prompt with reused session')
        reused_exp = {'task': reused_job.task, 'agent': reused_agent}
        dup_sess = r.reserve_host_launch(job=reused_job, expect=reused_exp, store_path=str(store))
        self.assertEqual((dup_sess.verdict, dup_sess.reason), ('FAILED', 'DUPLICATE_RESULT'))


if __name__ == '__main__':
    unittest.main()
