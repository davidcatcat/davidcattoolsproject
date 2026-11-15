"""#1502 WP-D R1+R2: chain counts, the continuation link and AI Debug on a real TaskStore.

Tasks A, B --continues A and C --continues B each run on their own D6 and PR.
The delegate repair count never resets across the chain, an unlinked Task on
A's result head refuses CONTINUATION_LINK_REQUIRED, and B cannot reserve an AI
Debug once A used the chain's one. Standard library only; no network, Git or
provider call.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import (config, high_debug, lineage_counts, repair_policy, task_submission_cli,
                     worker_dispatch)
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore, new_id

REPOSITORY = {'slug': 'example/project', 'dispatch_issue': 289, 'dispatch_evidence_pr': 1}
SOURCE = 'c' * 40   # the base every chain starts from
HEAD_A, HEAD_B = '1' * 40, '2' * 40
AT = '2026-11-15T00:00:00Z'
REASON = 'Synthetic early reason.'
ASSIGNMENT = {'schema_version': 1, 'route': 'codex', 'risk': 'LOW',
              'goal': 'Synthetic integration goal.', 'acceptance': ['dispatch is bounded']}


def debug_entry(status='RESERVED'):
    code = 'HIGH_DEBUG_RESERVED' if status == 'RESERVED' else 'HIGH_DEBUG_RUN_FAILED'
    return dict(high_debug._failed_entry(
        family='pr-17', failed_corrections=None, early_reason=REASON, head=SOURCE,
        d6='executor-hd1-ccccccc', code=code, observed='UNKNOWN', effort='UNKNOWN',
        usage='UNKNOWN', recorded_at=AT, model_requested=high_debug._CODEX_MODEL,
        rules_sha256='c' * 64), status=status)


class LineageDelegateIntegrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        for patcher in (
                patch.dict(os.environ, {'HOME': str(self.base)}),
                # The temp HOME has no owner trust file; the store's dispatch gate
                # reads the repository identity through worker_dispatch.
                patch.object(config, 'repository_config', return_value=dict(REPOSITORY)),
                patch.object(worker_dispatch, 'repository_config',
                             return_value=dict(REPOSITORY)),
                patch.object(high_debug.task_store, 'default_root',
                             return_value=self.base / 'tasks'),
                # Two review runs per chain, so a third Developer run is over it.
                patch.object(task_submission_cli.repair_policy, 'load_policy',
                             return_value=dict(repair_policy.load_policy(), repair_max=1))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.store = TaskStore(self.base / 'tasks')

    def record_pm(self, continues=None):
        """Create a Task the way task record-pm does, under the continues lock."""
        task_id = new_id()
        response = json.dumps({'schema_version': 1, 'summary': 'Fixture response.',
                               'developer_assignment': ASSIGNMENT})

        def create():
            task_submission_cli._record_pm_create(
                self.store, task_id, new_id(), 'Synthetic integration request.', response,
                dict(ASSIGNMENT), None, continues, True)
        if continues is None:
            create()
        else:
            with lineage_counts.continues_lock(self.store):
                lineage_counts.check_continues(self.store, task_id, continues)
                create()
        return task_id

    def delegate(self, task_id, executor, source, pr):
        """Run the chain gate with a claim that writes the Developer assignment."""
        args = SimpleNamespace(task_id=task_id, executor_task=executor, high_debug_ref=None,
                               high_debug_task=None, high_debug_family=None,
                               repair_packet=None)
        binding = (SimpleNamespace(task_id=executor, root=str(self.base / executor),
                                   source_sha=source, trusted_agent_id='codex-developer'),
                   SimpleNamespace(head=source))

        def claim():
            developer = {
                'assignment_id': new_id(), 'selection_source': 'PM',
                'state': 'DEVELOPER_PENDING', 'provider_profile': 'codex',
                'provider_type': None, 'model': None,
                'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
                'session_id': 'dev-' + new_id(), 'source_sha': source,
                'source_sha_binding': 'D6_REGISTRY_OBSERVED',
                'read_paths': ['README.md'], 'write_paths': ['README.md'],
                'acceptance': list(ASSIGNMENT['acceptance']),
                'context_sha256': '0' * 64, 'egress_basis': 'Synthetic basis.',
                'input_bytes': 1, 'max_tokens': None, 'artifact_format': 'unified_diff',
                'artifact': None, 'artifact_sha256': None, 'usage': None,
                'error_code': None, 'execution': None, 'created_at': AT}
            completion = {
                'state': 'WAITING_EXECUTOR', 'executor_task_id': executor,
                'reviewer_task_id': None,
                'evidence': {'task_id': task_id, 'execution_mode': 'DIRECT_D6',
                             'source_sha': source, 'result_head': None, 'pr_number': pr},
                'missing_gates': [], 'pm_dispatch': 'NOT_STARTED', 'pm_response': None,
                'pm_recommendation': None, 'pm_usage': None, 'pm_error_code': None}
            # The store's dispatch gate audits this claim as a Codex DISPATCH.
            # The live worker-dispatch.json names the real repository, not
            # REPOSITORY, so for this synthetic claim only the policy decision
            # is the Owner ALLOWED one; the audit and every other check still run.
            allowed = worker_dispatch.PolicyDecision(
                'codex-developer', True, 'OWNER_POLICY_ALLOWED', None)
            with patch.object(worker_dispatch, 'evaluate', return_value=allowed):
                self.store.update(task_id, developer=developer, completion=completion)
            return 'CLAIMED'
        return task_submission_cli._delegate_chain_gate(args, self.store, binding, claim)

    def finish(self, task_id, head):
        """Record the Developer result head, as a returned run leaves it."""
        record = self.store.get(task_id)
        completion = dict(record['completion'])
        completion['evidence'] = dict(completion['evidence'], result_head=head)
        self.store.update(task_id, developer=dict(record['developer'],
                                                  state='DEVELOPER_RESPONDED'),
                          completion=completion)

    def refused(self, call):
        with self.assertRaises(LLMToolError) as caught:
            call()
        return caught.exception

    def assert_unclaimed(self, task_id):
        record = self.store.get(task_id)
        self.assertIsNone(record['developer'])
        self.assertIsNone(record['completion'])

    def assert_released(self, *keys):
        release = lineage_counts.acquire_chain_locks(self.store, list(keys))
        release()

    def test_chain_count_link_and_debug(self):
        a = self.record_pm()
        self.assertEqual(self.delegate(a, 'executor-a', SOURCE, 101), 'CLAIMED')
        self.finish(a, HEAD_A)
        b = self.record_pm(continues=a)
        self.assertEqual(self.delegate(b, 'executor-b', HEAD_A, 102), 'CLAIMED')
        self.finish(b, HEAD_B)
        c = self.record_pm(continues=b)
        self.assertEqual(self.store.get(c)['continues'], b)

        # The repair count never resets: C's own D6 and PR are new, the chain is full.
        args = SimpleNamespace(task_id=c, executor_task='executor-c', high_debug_ref=None,
                               high_debug_task=None, high_debug_family=None)
        task_submission_cli._check_delegate_repair_cap(
            args, self.store, (SimpleNamespace(root=str(self.base)),
                               SimpleNamespace(head=HEAD_B)))
        error = self.refused(lambda: self.delegate(c, 'executor-c', HEAD_B, 103))
        self.assertEqual(str(error), 'DELEGATE_REPAIR_LIMIT_REACHED')
        self.assert_unclaimed(c)
        self.assert_released(a)

        # An unlinked Task on A's result head must continue the chain.
        unlinked = self.record_pm()
        error = self.refused(lambda: self.delegate(unlinked, 'executor-u', HEAD_A, 104))
        self.assertEqual(str(error), 'CONTINUATION_LINK_REQUIRED')
        self.assertEqual(error.continuation_link_reason, 'RESULT_HEAD_OUTSIDE_CHAIN')
        self.assert_unclaimed(unlinked)
        self.assert_released(a, unlinked)

        # A singleton on a base no Task produced is claimed as before.
        fresh = self.record_pm()
        self.assertEqual(self.delegate(fresh, 'executor-f', SOURCE, 105), 'CLAIMED')

        # A used the chain's one AI Debug, so B cannot reserve another.
        self.store.append_high_debug(a, debug_entry('RCA_CONFIRMED'))
        entries = self.store.get(b).get('high_debug', [])
        error = self.refused(lambda: high_debug._reserve_debug(
            self.store, b, entries, debug_entry()))
        self.assertEqual(getattr(error, 'high_debug_code', None),
                         'HIGH_DEBUG_DEBUG_PARENT_USED')
        self.assertEqual(self.store.get(b).get('high_debug', []), [])


if __name__ == '__main__':
    unittest.main()
