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
GOOD = {'schema_version': 1, 'repair_max': 5, 'parent_debug_max': 1, 'group_debug_max': 3,
        'child_max': 3, 'replan_max': 1, 'test_fail_max': 5, 'review_fail_max': 6,
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

    def test_default_limit_is_one_continuation(self):
        self.assertEqual(worker_dispatch.MAX_TIMEOUT_CONTINUATIONS_PER_TASK, 1)
        self.assertIsNone(self.continue_after(0, GOOD))
        self.assertEqual(self.continue_after(1, GOOD), 'WORKER_TIMEOUT_CONTINUATION_LIMIT')

    def test_policy_value_sets_the_limit(self):
        for limit, prior, code in ((0, 0, 'WORKER_TIMEOUT_CONTINUATION_LIMIT'), (2, 1, None),
                                   (2, 2, 'WORKER_TIMEOUT_CONTINUATION_LIMIT'), (3, 2, None)):
            with self.subTest(limit=limit, prior=prior):
                self.assertEqual(self.continue_after(
                    prior, dict(GOOD, timeout_continuation_max=limit)), code)

    def test_unreadable_policy_allows_none_with_its_own_code(self):
        self.assertEqual(self.continue_after(0, LLMToolError('POLICY_INVALID')),
                         'WORKER_TIMEOUT_CONTINUATION_POLICY_INVALID')

    def test_policy_file_bounds_the_value(self):
        handle, path = tempfile.mkstemp(suffix='.json')
        os.close(handle)
        self.addCleanup(os.unlink, path)
        for value, valid in ((0, True), (3, True), (4, False), (-1, False), (True, False),
                             ('1', False)):
            with self.subTest(value=value):
                with open(path, 'w') as stream:
                    json.dump(dict(GOOD, timeout_continuation_max=value), stream)
                if valid:
                    self.assertEqual(repair_policy.load_policy(path)['timeout_continuation_max'],
                                     value)
                else:
                    with self.assertRaises(LLMToolError):
                        repair_policy.load_policy(path)


if __name__ == '__main__':
    unittest.main()
