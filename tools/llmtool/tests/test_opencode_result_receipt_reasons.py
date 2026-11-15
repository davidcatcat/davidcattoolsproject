"""#1230 refusal-code pin: reason codes of the OpenCode result receipt. Offline, synthetic, no provider."""
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
from test_opencode_result_receipt import facts


class ReceiptReasonTests(unittest.TestCase):
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

    def outcome(self, **kw):
        args = dict(job=self.job, result=self.good, host_final=self.final,
                    observed_head=self.job.source_sha, seen=set())
        args.update(kw)
        receipt = r.reconcile(**args)
        return receipt.verdict, receipt.reason

    def test_success_reason(self):
        self.assertEqual(self.outcome(), ('SUCCESS', 'WORKER_RUN_OBSERVED'))

    def test_seen_set_and_session_reuse_reasons(self):
        self.assertEqual(self.outcome(seen=[]), ('UNKNOWN', 'SEEN_SET_INVALID'))
        reused = replace(self.good, session_id=self.job.agent.session_id)
        self.assertEqual(self.outcome(result=reused), ('FAILED', 'SESSION_IDENTITY_REUSE'))

    def test_blocked_code_with_launched_host_is_inconsistent(self):
        blocked = replace(self.good, code='OPENCODE_NOT_AUTHORIZED', status='BLOCKED',
                          content=None, processes=())
        final = {'code': blocked.code, 'launched': True, 'one_use_consumed': False}
        self.assertEqual(self.outcome(result=blocked, host_final=final),
                         ('UNKNOWN', 'BLOCKED_FACTS_INCONSISTENT'))

    def test_evidence_class_and_usage_reasons(self):
        offline = replace(self.good, evidence_class='OFFLINE_FIXTURE')
        self.assertEqual(self.outcome(result=offline), ('UNKNOWN', 'EVIDENCE_NOT_HOST_OBSERVED'))
        unchanged = replace(self.good, usage=self.job.usage)
        self.assertEqual(self.outcome(result=unchanged), ('UNKNOWN', 'USAGE_NOT_RECORDED'))


if __name__ == '__main__':
    unittest.main()
