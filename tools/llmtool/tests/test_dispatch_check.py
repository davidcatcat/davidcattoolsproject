"""Minimal dispatch-check output for PM policy gates."""

import review_trust_fixture

import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import cli
from llmtool import dispatch_check
from llmtool.worker_dispatch import PolicyDecision


class DispatchCheckTests(unittest.TestCase):
    def test_allowed_prints_only_minimal_decision_and_returns_zero(self):
        decision = PolicyDecision('claude-reviewer', True, 'OWNER_POLICY_ALLOWED', 'a' * 64)
        out = StringIO()
        with patch('llmtool.dispatch_check.worker_dispatch.evaluate', return_value=decision) as gate:
            with redirect_stdout(out):
                code = dispatch_check.run_dispatch_check(SimpleNamespace(
                    worker='claude-reviewer', task_id=None, owner_assignment_ref=None))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue()), {
            'worker': 'claude-reviewer', 'allowed': True,
            'reason': 'OWNER_POLICY_ALLOWED', 'policy_digest': 'a' * 64})
        self.assertEqual(set(json.loads(out.getvalue())),
                         {'worker', 'allowed', 'reason', 'policy_digest'})
        gate.assert_called_once_with('claude-reviewer', None, None)

    def test_refusal_returns_three_and_passes_task_and_owner_ref(self):
        decision = PolicyDecision('codex-reviewer', False, 'OWNER_ASSIGNMENT_REQUIRED', 'b' * 64)
        out = StringIO()
        with patch('llmtool.dispatch_check.worker_dispatch.evaluate', return_value=decision) as gate:
            with redirect_stdout(out):
                code = cli.main(['dispatch-check', 'codex-reviewer', '--task', 'f' * 32,
                                 '--owner-ref',
                                 'https://github.com/davidcatcat/davidcattoolsproject/issues/289#issuecomment-1'])
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(out.getvalue())['allowed'], False)
        gate.assert_called_once_with(
            'codex-reviewer',
            'https://github.com/davidcatcat/davidcattoolsproject/issues/289#issuecomment-1',
            'f' * 32)


if __name__ == '__main__':
    unittest.main()


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()
