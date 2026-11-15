"""#1815 T3: policy.json timeout_continuation_max bounds Codex timeout continuations."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import test_worker_dispatch as twd
from llmtool import repair_policy, worker_dispatch
from llmtool.errors import LLMToolError

setUpModule, tearDownModule = twd.setUpModule, twd.tearDownModule
GOOD = {'schema_version': 1, 'repair_max': 2, 'parent_debug_max': 1, 'group_debug_max': 1,
        'child_max': 3, 'replan_max': 1, 'test_fail_max': 2, 'review_fail_max': 3,
        'waiting_notify_after_hours': 24}


class TimeoutContinuationPolicyTests(unittest.TestCase):
    setUp, _task = twd.WorkerDispatchTests.setUp, twd.WorkerDispatchTests._task
    developer = staticmethod(twd.WorkerDispatchTests.developer)

    def continue_after(self, prior, policy):
        previous = self.developer(state='DEVELOPER_OUTCOME_UNKNOWN', provider='codex')
        previous.update(provider_type='codex-cli', model='gpt-6-luna',
                        executor_task_id='executor-task-1',
                        error_code='CODEX_TIMEOUT_WITH_CHANGES',
                        execution={'status': 'TIMEOUT', 'diff_sha256': 'c' * 64,
                                   'diff_base_sha': previous['source_sha']})
        continued = {**previous, 'assignment_id': twd.new_id(), 'state': 'DEVELOPER_DISPATCHING',
                     'session_id': 'dev-' + twd.new_id(), 'execution': None, 'error_code': None}
        entry = worker_dispatch.audit_entry(worker_dispatch.PolicyDecision(
            'codex-developer', True, 'OWNER_POLICY_ALLOWED', 'a' * 64), 'TIMEOUT_CONTINUATION')
        current = {'task_id': self.task['task_id'], 'developer': previous, 'completion': None,
                   'dispatch_audit': [dict(entry) for _ in range(prior)]}
        changes = {'developer': continued, 'completion': {
            'executor_task_id': 'executor-task-1', 'evidence': {'result_head': 'd' * 40}}}
        load = (patch.object(repair_policy, 'load_policy', return_value=policy)
                if type(policy) is dict else
                patch.object(repair_policy, 'load_policy', side_effect=policy))
        with load:
            denied = worker_dispatch.authorize_task_store_change(
                current, changes, expected={'developer': previous})
        code = None if denied is None else denied.dispatch_code
        self.assertEqual(current['dispatch_audit'][-1]['reason_code'],
                         code or 'OWNER_POLICY_ALLOWED')
        return code


if __name__ == '__main__':
    unittest.main()
