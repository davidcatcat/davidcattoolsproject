"""Focused tests for the original Task's production Reviewer adapter boundary."""

import review_trust_fixture

import importlib
import unittest
from unittest.mock import patch

from llmtool import worker_dispatch


class ReviewerGateIntegrationTests(unittest.TestCase):
    def test_completion_consumes_reviewer_dispatch_authorization_once(self):
        task_completion_tests = importlib.import_module('test_task_completion')
        fixture = task_completion_tests.TaskCompletionTests(
            'test_host_injected_reviewer_adapter_is_used_by_completion_event')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        task, store, result, tests = fixture._direct_opencode_task(
            'reviewer-single-gate')

        def adapter(intent):
            worker_dispatch.authorize_dispatch(
                store, intent['task_id'], 'claude-reviewer', 'REVIEWER_HANDOFF')
            return fixture._trusted_review_receipt(intent, verdict='PASS')

        with patch.object(worker_dispatch, 'authorize_dispatch',
                          wraps=worker_dispatch.authorize_dispatch) as authorize:
            code, client, _, _ = fixture._run_direct_pm_send_to_review(
                task, store, result, tests, adapter=adapter, inject_adapter=True)

        self.assertEqual(code, 0)
        self.assertEqual(authorize.call_count, 1)
        self.assertEqual(authorize.call_args.args[2:],
                         ('claude-reviewer', 'REVIEWER_HANDOFF'))
        self.assertEqual(store.get(task['task_id'])['dispatch_audit'][-1]['decision'],
                         'ALLOWED')
        callback = store.get(task['task_id'])['developer']['direct_result_callback']
        self.assertEqual(callback['review_handoff']['dispatch_state'], 'RECEIVED')
        self.assertEqual(callback['review_handoff']['receipt']['verdict'], 'PASS')
        self.assertEqual(callback['review_return']['state'], 'RETURNED')
        self.assertEqual(len(client.calls), 2)


if __name__ == '__main__':
    unittest.main()


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()
