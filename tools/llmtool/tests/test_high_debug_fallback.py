"""#1502 WP-B: Codex-to-Claude AI Debug fallback, WAITING and CLI wiring."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import high_debug
from llmtool import task_submission_cli
from llmtool.errors import LLMToolError

def refusal(code):
    error = LLMToolError('refused')
    error.high_debug_code = code
    return error


class _Store:
    def __init__(self):
        self.entries, self.events = [], []

    def get(self, task_id):
        return {'task_id': task_id, 'run_id': 'b' * 32,
                'review_repair_claims': [{'family': 'family', 'pr': 17}] * 2,
                'completion': {'executor_task_id': 'executor',
                               'evidence': {'common_dir': '/tmp/ignored'}},
                'high_debug': list(self.entries)}

    @staticmethod
    def _completion_now():
        return '2026-11-12T00:00:00Z'

    def append_high_debug(self, _task_id, entry):
        self.entries.append(entry)

    def record_completion_event(self, _task_id, **event):
        self.events.append(event)
        return {'created': True}


class FallbackTests(unittest.TestCase):
    def _run(self, outcomes, route='codex'):
        calls = []
        def fake(*, route, **kwargs):
            calls.append(route)
            outcome = outcomes[route]
            if outcome is None:
                return 'ran-' + route
            raise refusal(outcome)
        with patch.object(high_debug, 'run_high_debug', side_effect=fake):
            try:
                return high_debug.run_high_debug_with_fallback(route=route, task_id='a' * 32), None, calls
            except LLMToolError as error:
                return None, error, calls

    def test_whitelisted_codex_failures_switch_to_claude(self):
        for code in sorted(high_debug.DEBUG_FALLBACK_CODES):
            with self.subTest(code=code):
                result, error, calls = self._run({'codex': code, 'claude': None})
                self.assertEqual((result, error, calls), ('ran-claude', None, ['codex', 'claude']))

    def test_other_codex_refusals_stop_without_switch(self):
        for code in ('HIGH_DEBUG_RUN_FAILED', 'HIGH_DEBUG_REPORT_INVALID',
                     'HIGH_DEBUG_EVIDENCE_SENSITIVE', 'HIGH_DEBUG_DISPATCH_REFUSED',
                     'HIGH_DEBUG_WROTE_FILES'):
            with self.subTest(code=code):
                _result, error, calls = self._run({'codex': code, 'claude': None})
                self.assertEqual(error.high_debug_code, code)
                self.assertEqual(calls, ['codex'])

    def test_codex_success_and_explicit_claude_route_never_switch(self):
        self.assertEqual(self._run({'codex': None, 'claude': None})[2], ['codex'])
        _result, error, calls = self._run({'codex': None, 'claude': 'HIGH_DEBUG_QUOTA_EXHAUSTED'},
                                          route='claude')
        self.assertEqual((error.high_debug_code, calls), ('HIGH_DEBUG_QUOTA_EXHAUSTED', ['claude']))

    def test_claude_failure_that_is_not_unavailability_is_raised(self):
        _result, error, calls = self._run({'codex': 'HIGH_DEBUG_QUOTA_EXHAUSTED',
                                           'claude': 'HIGH_DEBUG_RUN_FAILED'})
        self.assertEqual((error.high_debug_code, calls), ('HIGH_DEBUG_RUN_FAILED', ['codex', 'claude']))

    def test_both_routes_unavailable_is_waiting_with_a_reason(self):
        cases = ((('HIGH_DEBUG_QUOTA_EXHAUSTED', 'HIGH_DEBUG_QUOTA_EXHAUSTED'), 'QUOTA'),
                 (('HIGH_DEBUG_AUTH_FAILED', 'HIGH_DEBUG_QUOTA_EXHAUSTED'), 'AUTH'),
                 (('HIGH_DEBUG_QUOTA_EXHAUSTED', 'HIGH_DEBUG_AUTH_FAILED'), 'AUTH'),
                 (('HIGH_DEBUG_QUOTA_EXHAUSTED', 'HIGH_DEBUG_DISPATCH_REFUSED'), 'ROUTE_UNAVAILABLE'),
                 (('HIGH_DEBUG_QUOTA_EXHAUSTED', 'HIGH_DEBUG_ACCOUNT_UNCONFIGURED'), 'ROUTE_UNAVAILABLE'))
        for (first, second), reason in cases:
            with self.subTest(first=first, second=second):
                _result, error, calls = self._run({'codex': first, 'claude': second})
                self.assertEqual(error.high_debug_code, 'HIGH_DEBUG_WAITING')
                detail = error.high_debug_detail
                self.assertIn(f'reason={reason} codex={first} claude={second} ', detail)
                self.assertRegex(detail, r'waiting_since=\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ ')
                self.assertTrue(detail.endswith('resume_condition=a_debug_route_can_run'))
                self.assertEqual(calls, ['codex', 'claude'])


class CliWiringTests(unittest.TestCase):
    def test_task_high_debug_goes_through_the_fallback(self):
        args = SimpleNamespace(task_id='a' * 32, executor_task='executor', pr=17, family='family',
                               failed_corrections=2, early=None, evidence_file=None, route='codex')
        with patch.object(high_debug, 'run_high_debug_with_fallback', return_value='done') as run:
            self.assertEqual(task_submission_cli.run_task_high_debug(args, store=_Store()), 'done')
        self.assertEqual(run.call_args.kwargs['route'], 'codex')
        self.assertEqual(run.call_args.kwargs['task_id'], 'a' * 32)


if __name__ == '__main__':
    unittest.main()
