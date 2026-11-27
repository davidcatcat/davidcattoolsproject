"""#1502 WP-B: Codex-to-Claude AI Debug fallback, WAITING and CLI wiring."""

import unittest
import unittest.mock
from contextlib import redirect_stderr
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import cli
from llmtool import high_debug
from llmtool import task_submission_cli
from llmtool.errors import LLMToolError

def refusal(code):
    error = LLMToolError('refused')
    error.high_debug_code = code
    return error


class _Store:
    def __init__(self):
        self.entries, self.events, self.served = [], [], set()

    def task_ids(self):
        return sorted(self.served)

    def finalize_high_debug(self, _task_id, index, entry):
        self.entries[index] = entry

    def get(self, task_id):
        self.served.add(task_id)
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


def codex_attempt_reported(kwargs, code):
    """Stand in for run_high_debug's Codex runner reporting ``code`` itself.

    PR #2222 v92: only that runner result fills in the attempt record; a
    fake that raises a fallback code without it proves no Codex attempt.
    """
    attempt = kwargs.get('codex_attempt')
    if code in high_debug.DEBUG_FALLBACK_CODES and attempt is not None:
        attempt.code = code


class FallbackTests(unittest.TestCase):
    def setUp(self):
        # These tests cover the Codex-first route; the Owner pause of
        # codex-debug (#289 c7153323324) is covered by PausedCodexDebugTests.
        paused = patch.object(high_debug, '_codex_debug_owner_paused', return_value=False)
        paused.start()
        self.addCleanup(paused.stop)

    def _run(self, outcomes, route='codex', attempt_ran=True):
        calls = []
        def fake(*, route, **kwargs):
            calls.append(route)
            outcome = outcomes[route]
            if outcome is None:
                return 'ran-' + route
            if attempt_ran:
                codex_attempt_reported(kwargs, outcome)
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

    def test_fallback_code_without_a_codex_attempt_is_refused_without_switch(self):
        # PR #2222 v92 (CWE-862): the code alone, raised before or outside the
        # Codex attempt, issues no grant and starts no Claude Debug.
        for code in sorted(high_debug.DEBUG_FALLBACK_CODES):
            with self.subTest(code=code):
                result, error, calls = self._run({'codex': code, 'claude': None},
                                                 attempt_ran=False)
                self.assertIsNone(result)
                self.assertEqual(error.high_debug_code, 'HIGH_DEBUG_CLAUDE_ROUTE_REFUSED')
                self.assertTrue(error.high_debug_detail.endswith(' codex_attempt=UNCONFIRMED'))
                self.assertEqual(calls, ['codex'])
                self.assertEqual(high_debug._CLAUDE_GRANTS, set())

    def test_attempt_record_with_another_code_is_refused_without_switch(self):
        # The Codex attempt reported login failure, but the raised code is
        # quota: the code was not that attempt's result.
        calls = []
        def fake(*, route, **kwargs):
            calls.append(route)
            codex_attempt_reported(kwargs, 'HIGH_DEBUG_AUTH_FAILED')
            raise refusal('HIGH_DEBUG_QUOTA_EXHAUSTED')
        with patch.object(high_debug, 'run_high_debug', side_effect=fake):
            with self.assertRaises(LLMToolError) as caught:
                high_debug.run_high_debug_with_fallback(task_id='a' * 32)
        self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_CLAUDE_ROUTE_REFUSED')
        self.assertEqual(calls, ['codex'])

    def test_each_call_gets_a_fresh_empty_attempt_record(self):
        seen = []
        def fake(*, route, **kwargs):
            seen.append(kwargs.get('codex_attempt'))
            return 'ran-' + route
        with patch.object(high_debug, 'run_high_debug', side_effect=fake):
            high_debug.run_high_debug_with_fallback(task_id='a' * 32)
            high_debug.run_high_debug_with_fallback(task_id='a' * 32)
        self.assertEqual(len(seen), 2)
        self.assertIsNot(seen[0], seen[1])
        for attempt in seen:
            self.assertIs(type(attempt), high_debug._CodexAttempt)
            self.assertEqual((attempt.task_id, attempt.code), ('a' * 32, None))

    def test_other_codex_refusals_stop_without_switch(self):
        for code in ('HIGH_DEBUG_RUN_FAILED', 'HIGH_DEBUG_REPORT_INVALID',
                     'HIGH_DEBUG_EVIDENCE_SENSITIVE', 'HIGH_DEBUG_DISPATCH_REFUSED',
                     'HIGH_DEBUG_WROTE_FILES'):
            with self.subTest(code=code):
                _result, error, calls = self._run({'codex': code, 'claude': None})
                self.assertEqual(error.high_debug_code, code)
                self.assertEqual(calls, ['codex'])

    def test_codex_success_never_switches(self):
        self.assertEqual(self._run({'codex': None, 'claude': None})[2], ['codex'])

    def test_explicit_claude_route_is_refused_and_runs_nothing(self):
        # Codex v995 audit of #1630 (CWE-862): without a Codex quota or login
        # failure in this run, a direct Claude request starts no Debug at all.
        for claude in (None, 'HIGH_DEBUG_QUOTA_EXHAUSTED'):
            with self.subTest(claude=claude):
                result, error, calls = self._run({'codex': None, 'claude': claude},
                                                 route='claude')
                self.assertIsNone(result)
                self.assertEqual(error.high_debug_code, high_debug.CLAUDE_ROUTE_REFUSED)
                self.assertEqual(error.high_debug_code, 'HIGH_DEBUG_CLAUDE_ROUTE_REFUSED')
                self.assertEqual(calls, [])

    def test_allowed_fallback_passes_the_same_request_to_claude(self):
        seen = []
        attempts = []
        def fake(*, route, **kwargs):
            attempts.append(kwargs.get('codex_attempt'))
            if route == 'codex':
                codex_attempt_reported(kwargs, 'HIGH_DEBUG_AUTH_FAILED')
            attempt = kwargs.pop('codex_attempt', None)
            grant = kwargs.pop('claude_fallback', None)
            seen.append((route, kwargs, grant))
            if route == 'codex':
                self.assertEqual(attempt.code, 'HIGH_DEBUG_AUTH_FAILED')
                raise refusal('HIGH_DEBUG_AUTH_FAILED')
            # The grant is live while the Claude run uses it.
            self.assertIn(grant, high_debug._CLAUDE_GRANTS)
            return 'ran-claude'
        with patch.object(high_debug, 'run_high_debug', side_effect=fake):
            self.assertEqual(high_debug.run_high_debug_with_fallback(
                task_id='a' * 32, family='family'), 'ran-claude')
        self.assertEqual([route for route, _kwargs, _grant in seen], ['codex', 'claude'])
        self.assertEqual(seen[0][1], seen[1][1])
        # Codex gets an attempt record and no grant; Claude gets no attempt
        # record and a grant bound to this Task and the Codex fallback code,
        # revoked once the Claude run returns.
        self.assertIs(type(attempts[0]), high_debug._CodexAttempt)
        self.assertIsNone(attempts[1])
        self.assertIsNone(seen[0][2])
        grant = seen[1][2]
        self.assertIs(type(grant), high_debug._ClaudeFallbackGrant)
        self.assertEqual((grant.task_id, grant.codex_code), ('a' * 32, 'HIGH_DEBUG_AUTH_FAILED'))
        self.assertNotIn(grant, high_debug._CLAUDE_GRANTS)
        self.assertFalse(high_debug._consume_claude_grant(grant, 'a' * 32))

    def test_grant_is_revoked_when_the_claude_run_fails(self):
        seen = []
        def fake(*, route, **kwargs):
            if route == 'codex':
                codex_attempt_reported(kwargs, 'HIGH_DEBUG_QUOTA_EXHAUSTED')
                raise refusal('HIGH_DEBUG_QUOTA_EXHAUSTED')
            seen.append(kwargs['claude_fallback'])
            raise refusal('HIGH_DEBUG_RUN_FAILED')
        with patch.object(high_debug, 'run_high_debug', side_effect=fake):
            with self.assertRaises(LLMToolError) as caught:
                high_debug.run_high_debug_with_fallback(task_id='a' * 32)
        self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_RUN_FAILED')
        self.assertEqual(len(seen), 1)
        self.assertNotIn(seen[0], high_debug._CLAUDE_GRANTS)

    def test_caller_supplied_grant_is_refused_and_runs_nothing(self):
        # Only the fallback issues a grant; a caller can never pass one in.
        for grant in (None, object(), high_debug._ClaudeFallbackGrant(
                'a' * 32, 'HIGH_DEBUG_QUOTA_EXHAUSTED')):
            with self.subTest(grant=grant), \
                 patch.object(high_debug, 'run_high_debug') as run:
                with self.assertRaises(LLMToolError) as caught:
                    high_debug.run_high_debug_with_fallback(task_id='a' * 32,
                                                            claude_fallback=grant)
                self.assertEqual(caught.exception.high_debug_code,
                                 'HIGH_DEBUG_ARGUMENT_INVALID')
                run.assert_not_called()

    def test_caller_supplied_attempt_record_is_refused_and_runs_nothing(self):
        # PR #2222 v92: only the fallback makes an attempt record.
        filled = high_debug._CodexAttempt('a' * 32)
        filled.code = 'HIGH_DEBUG_QUOTA_EXHAUSTED'
        for attempt in (None, filled, high_debug._CodexAttempt('a' * 32)):
            with self.subTest(attempt=attempt), \
                 patch.object(high_debug, 'run_high_debug') as run:
                with self.assertRaises(LLMToolError) as caught:
                    high_debug.run_high_debug_with_fallback(task_id='a' * 32,
                                                            codex_attempt=attempt)
                self.assertEqual(caught.exception.high_debug_code,
                                 'HIGH_DEBUG_ARGUMENT_INVALID')
                run.assert_not_called()

    def test_direct_module_call_of_the_claude_route_is_refused(self):
        # PR #2222 v91 (CWE-862): the rule holds in run_high_debug itself, not
        # only in this wrapper. A direct Claude call starts nothing.
        # No runner is injected, so the real Claude launcher is the one refused.
        store = unittest.mock.Mock()
        create = unittest.mock.Mock()
        with patch.object(high_debug.worker_dispatch, 'evaluate') as evaluate, \
             patch.object(high_debug.role_config, 'resolve_role') as resolve, \
             patch.object(high_debug, '_run_claude') as launch:
            with self.assertRaises(LLMToolError) as caught:
                high_debug.run_high_debug(
                    store=store, task_id='a' * 32, executor_task='executor',
                    pr_number=17, family='family', failed_corrections=2,
                    create_d6=create, route='claude')
        self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_CLAUDE_ROUTE_REFUSED')
        for called in (evaluate, resolve, launch, create):
            called.assert_not_called()
        self.assertEqual(store.mock_calls, [])

    def test_refused_claude_route_starts_no_provider_or_store_access(self):
        # The real run_high_debug stays reachable; it must never be entered.
        store = unittest.mock.Mock()
        runner, create = unittest.mock.Mock(), unittest.mock.Mock()
        with patch.object(high_debug, 'run_high_debug',
                          wraps=high_debug.run_high_debug) as wrapped, \
             patch.object(high_debug.worker_dispatch, 'evaluate') as evaluate, \
             patch.object(high_debug.role_config, 'resolve_role') as resolve:
            with self.assertRaises(LLMToolError) as caught:
                high_debug.run_high_debug_with_fallback(
                    store=store, task_id='a' * 32, executor_task='executor',
                    pr_number=17, family='family', failed_corrections=2,
                    runner=runner, create_d6=create, route='claude')
        self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_CLAUDE_ROUTE_REFUSED')
        wrapped.assert_not_called()
        evaluate.assert_not_called()
        resolve.assert_not_called()
        runner.assert_not_called()
        create.assert_not_called()
        self.assertEqual(store.mock_calls, [])

    def test_unknown_route_is_still_refused_by_run_high_debug_without_switch(self):
        _result, error, calls = self._run({'other': 'HIGH_DEBUG_ARGUMENT_INVALID',
                                           'claude': None}, route='other')
        self.assertEqual((error.high_debug_code, calls), ('HIGH_DEBUG_ARGUMENT_INVALID', ['other']))

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


    def _cli_refusal_line(self, action, detail):
        error = LLMToolError('safe refusal')
        error.high_debug_code = 'HIGH_DEBUG_REPORT_INVALID'
        error.review_repair_code = 'REVIEW_REPAIR_REFUSED'
        if detail is not None:
            error.high_debug_detail = detail
        args = SimpleNamespace(command='task', action=action)
        output = StringIO()
        with patch.object(cli, 'parser_for_cli',
                          return_value=SimpleNamespace(parse_args=lambda _argv: args)), \
             patch('llmtool.source_freshness.require_fresh_source'), \
             patch.object(cli.task_submission_cli, 'run_task_high_debug', side_effect=error), \
             patch.object(cli.task_submission_cli, 'run_task_repair', side_effect=error), \
             redirect_stderr(output):
            self.assertEqual(cli.main([]), 1)
        return output.getvalue()

    def test_cli_prints_allowlisted_high_debug_detail_with_hyphen_period_or_colon(self):
        for detail in ('expected=pr-17', 'v1.2', '2026-11-12T00:00:00Z'):
            with self.subTest(detail=detail):
                self.assertEqual(
                    self._cli_refusal_line('high-debug', detail),
                    'llmtool: high-debug refused: HIGH_DEBUG_REPORT_INVALID '
                    + '(' + detail + ')\n')

    def test_cli_omits_high_debug_detail_with_newline_slash_quote_or_129_chars(self):
        for detail in ('pr-17\nv1.2', 'pr-17/v1.2', 'pr-17"v1.2', 'a' * 129):
            with self.subTest(detail=detail[:8] + ('...' if len(detail) > 11 else '')):
                self.assertEqual(self._cli_refusal_line('high-debug', detail),
                                 'llmtool: high-debug refused: HIGH_DEBUG_REPORT_INVALID\n')

    def test_cli_prints_compact_high_debug_waiting_pair_for_long_fallback_detail(self):
        detail = ('reason=QUOTA codex=HIGH_DEBUG_QUOTA_EXHAUSTED '
                  'claude=HIGH_DEBUG_QUOTA_EXHAUSTED '
                  'waiting_since=2026-11-17T06:00:00Z '
                  'resume_condition=a_debug_route_can_run')
        self.assertGreater(len(detail), 128)
        error = LLMToolError('safe refusal')
        error.high_debug_code = 'HIGH_DEBUG_WAITING'
        error.review_repair_code = 'REVIEW_REPAIR_REFUSED'
        error.high_debug_detail = detail
        args = SimpleNamespace(command='task', action='high-debug')
        output = StringIO()
        with patch.object(cli, 'parser_for_cli',
                          return_value=SimpleNamespace(parse_args=lambda _argv: args)), \
             patch('llmtool.source_freshness.require_fresh_source'), \
             patch.object(cli.task_submission_cli, 'run_task_high_debug', side_effect=error), \
             patch.object(cli.task_submission_cli, 'run_task_repair', side_effect=error), \
             redirect_stderr(output):
            self.assertEqual(cli.main([]), 1)
        self.assertEqual(output.getvalue(),
                         'llmtool: high-debug refused: HIGH_DEBUG_WAITING '
                         '(reason=QUOTA waiting_since=2026-11-17T06:00:00Z)\n')

    def test_cli_omits_high_debug_waiting_detail_when_since_fails_allowlist_or_reason_missing(self):
        details = ('reason=QUOTA codex=HIGH_DEBUG_QUOTA_EXHAUSTED claude=HIGH_DEBUG_QUOTA_EXHAUSTED waiting_since=2026/11/17T06:00:00Z resume_condition=a_debug_route_can_run',
                   'codex=HIGH_DEBUG_QUOTA_EXHAUSTED claude=HIGH_DEBUG_QUOTA_EXHAUSTED waiting_since=2026-11-17T06:00:00Z resume_condition=a_debug_route_can_run_now_or_later')
        for detail in details:
            self.assertGreater(len(detail), 128)
            error = LLMToolError('safe refusal')
            error.high_debug_code = 'HIGH_DEBUG_WAITING'
            error.review_repair_code = 'REVIEW_REPAIR_REFUSED'
            error.high_debug_detail = detail
            args = SimpleNamespace(command='task', action='high-debug')
            output = StringIO()
            with patch.object(cli, 'parser_for_cli',
                              return_value=SimpleNamespace(parse_args=lambda _argv: args)), \
                 patch('llmtool.source_freshness.require_fresh_source'), \
                 patch.object(cli.task_submission_cli, 'run_task_high_debug', side_effect=error), \
                 patch.object(cli.task_submission_cli, 'run_task_repair', side_effect=error), \
                 redirect_stderr(output):
                self.assertEqual(cli.main([]), 1)
            self.assertEqual(output.getvalue(),
                             'llmtool: high-debug refused: HIGH_DEBUG_WAITING\n')

    def test_cli_omits_high_debug_report_invalid_detail_over_128_chars(self):
        detail = 'reason=QUOTA codex=HIGH_DEBUG_QUOTA_EXHAUSTED claude=HIGH_DEBUG_QUOTA_EXHAUSTED waiting_since=2026-11-17T06:00:00Z resume_condition=a_debug_route_can_run'
        self.assertGreater(len(detail), 128)
        error = LLMToolError('safe refusal')
        error.high_debug_code = 'HIGH_DEBUG_REPORT_INVALID'
        error.review_repair_code = 'REVIEW_REPAIR_REFUSED'
        error.high_debug_detail = detail
        args = SimpleNamespace(command='task', action='high-debug')
        output = StringIO()
        with patch.object(cli, 'parser_for_cli',
                          return_value=SimpleNamespace(parse_args=lambda _argv: args)), \
             patch('llmtool.source_freshness.require_fresh_source'), \
             patch.object(cli.task_submission_cli, 'run_task_high_debug', side_effect=error), \
             patch.object(cli.task_submission_cli, 'run_task_repair', side_effect=error), \
             redirect_stderr(output):
            self.assertEqual(cli.main([]), 1)
        self.assertEqual(output.getvalue(),
                         'llmtool: high-debug refused: HIGH_DEBUG_REPORT_INVALID\n')


class CliWiringTests(unittest.TestCase):
    def setUp(self):
        # These tests cover the Codex-first route; the Owner pause of
        # codex-debug (#289 c7153323324) is covered by PausedCodexDebugTests.
        paused = patch.object(high_debug, '_codex_debug_owner_paused', return_value=False)
        paused.start()
        self.addCleanup(paused.stop)

    def test_task_high_debug_goes_through_the_fallback(self):
        args = SimpleNamespace(task_id='a' * 32, executor_task='executor', pr=17, family='family',
                               failed_corrections=2, early=None, evidence_file=None, route='codex')
        with patch.object(high_debug, 'run_high_debug_with_fallback', return_value='done') as run:
            self.assertEqual(task_submission_cli.run_task_high_debug(args, store=_Store()), 'done')
        self.assertEqual(run.call_args.kwargs['route'], 'codex')
        self.assertEqual(run.call_args.kwargs['task_id'], 'a' * 32)

    def test_task_high_debug_route_claude_is_refused_before_any_debug(self):
        args = SimpleNamespace(task_id='a' * 32, executor_task='executor', pr=17, family='family',
                               failed_corrections=2, early=None, evidence_file=None, route='claude')
        runner, create = unittest.mock.Mock(), unittest.mock.Mock()
        with patch.object(high_debug, 'run_high_debug') as run:
            with self.assertRaises(LLMToolError) as caught:
                task_submission_cli.run_task_high_debug(args, store=_Store(), runner=runner,
                                                        create_d6=create)
        self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_CLAUDE_ROUTE_REFUSED')
        run.assert_not_called()
        runner.assert_not_called()
        create.assert_not_called()


if __name__ == '__main__':
    unittest.main()


class PausedCodexDebugTests(unittest.TestCase):
    """Owner 2026-11-25 (#289 c7153323324): codex-debug PAUSED runs claude-debug."""

    def _policy(self, allowed, reason):
        return patch.object(high_debug.worker_dispatch, 'evaluate',
                            return_value=SimpleNamespace(allowed=allowed, reason_code=reason))

    def _run(self):
        calls = []
        def fake(*, route, claude_fallback=None, **kwargs):
            calls.append((route, 'codex_attempt' in kwargs))
            if route == 'claude':
                # The real run_high_debug consumes the one-use grant first.
                self.assertTrue(high_debug._consume_claude_grant(claude_fallback, 'a' * 32))
            return 'ran-' + route
        with patch.object(high_debug, 'run_high_debug', side_effect=fake):
            return high_debug.run_high_debug_with_fallback(task_id='a' * 32), calls

    def test_owner_paused_codex_debug_runs_claude_without_a_codex_attempt(self):
        with self._policy(False, 'OWNER_PAUSED'):
            result, calls = self._run()
        self.assertEqual((result, calls), ('ran-claude', [('claude', False)]))
        self.assertEqual(high_debug._CLAUDE_GRANTS, set())

    def test_allowed_codex_debug_keeps_codex_first(self):
        with self._policy(True, 'OWNER_POLICY_ALLOWED'):
            result, calls = self._run()
        self.assertEqual((result, calls), ('ran-codex', [('codex', True)]))

    def test_other_refusals_or_unreadable_policy_keep_codex_first(self):
        for decision in (self._policy(False, 'OWNER_ASSIGNMENT_REQUIRED'),
                         patch.object(high_debug.worker_dispatch, 'evaluate',
                                      side_effect=LLMToolError('unreadable'))):
            with self.subTest(decision=decision), decision:
                result, calls = self._run()
                self.assertEqual((result, calls), ('ran-codex', [('codex', True)]))

    def test_explicit_claude_route_is_still_refused(self):
        with self._policy(False, 'OWNER_PAUSED'), \
                self.assertRaises(LLMToolError) as caught:
            high_debug.run_high_debug_with_fallback(route='claude', task_id='a' * 32)
        self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_CLAUDE_ROUTE_REFUSED')

    def test_paused_grant_is_one_use_and_task_bound(self):
        grant = high_debug._issue_claude_grant('a' * 32, high_debug.CODEX_DEBUG_PAUSED)
        self.assertFalse(high_debug._consume_claude_grant(grant, 'b' * 32))
        grant = high_debug._issue_claude_grant('a' * 32, high_debug.CODEX_DEBUG_PAUSED)
        self.assertTrue(high_debug._consume_claude_grant(grant, 'a' * 32))
        self.assertFalse(high_debug._consume_claude_grant(grant, 'a' * 32))

    def test_real_policy_allows_codex_debug(self):
        # The repository policy file through the real reader; only the trusted
        # repository config is pinned, as in the other real-policy tests.
        # Owner 2026-11-27 (#289 c7218927310): codex-debug is ALLOWED again.
        from pathlib import Path
        dispatch = high_debug.worker_dispatch
        with patch.object(dispatch, '_POLICY_PATH', Path(dispatch._DEFAULT_POLICY_PATH)), \
                patch.object(dispatch, 'repository_config',
                             return_value={'slug': 'davidcatcat/davidcattoolsproject',
                                           'dispatch_issue': 289, 'dispatch_evidence_pr': 283}):
            self.assertFalse(high_debug._codex_debug_owner_paused('a' * 32))
            self.assertTrue(dispatch.evaluate('codex-debug', None, 'a' * 32).allowed)
            self.assertTrue(dispatch.evaluate('claude-debug', None, 'a' * 32).allowed)
