"""Tests for llmtool.role_timeout (#1019 A11 row 41 PR 1a).

No provider calls, no I/O. All values are fixed, made-up route names, codes
and short hex ids; no credential-shaped strings (common-core.md section 4).
"""

import types
import unittest

from llmtool import role_timeout
from llmtool.developer_routes import base
from llmtool.errors import LLMToolError

ROUTES = ('claude', 'agy', 'gw-agent', 'opencode')
TIMEOUT_CODES = {
    'claude': 'CLAUDE_TIMEOUT',
    'agy': 'AGY_TIMEOUT',
    'gw-agent': 'GW_AGENT_TIMEOUT',
    'opencode': 'OPENCODE_TIMEOUT',
}
# Adapter names used by base._route_code (see the module docstring).
ADAPTER_NAMES = {'claude': 'claude', 'agy': 'agy', 'gw-agent': 'gw_agent'}

ASSIGNMENT_A = '0a0a0a0a'
ASSIGNMENT_B = '0b0b0b0b'
LINK_1 = '00000001'
LINK_2 = '00000002'
LINK_3 = '00000003'


def entry(action, decision='ALLOWED', worker_key='claude-developer',
          assignment_id=ASSIGNMENT_A, **extra):
    value = {'action': action, 'decision': decision, 'worker_key': worker_key,
             'assignment_id': assignment_id}
    value.update(extra)
    return value


def transition(decision='ALLOWED', **extra):
    return entry('ROLE_TIMEOUT_RETRY', decision, retry_phase='TRANSITION', **extra)


def adapter(link, decision='ALLOWED', **extra):
    value = entry('ROLE_TIMEOUT_RETRY', decision, retry_phase='ADAPTER', **extra)
    if link is not None:
        value['authorization_id'] = link
    return value


def full_retries(count):
    """count full retries: an ALLOWED transition and its ALLOWED adapter each."""
    entries = []
    for index in range(count):
        entries.append(transition())
        entries.append(adapter('%08x' % (index + 1)))
    return entries


class RouteTableTests(unittest.TestCase):

    def test_table_is_exactly_the_four_routes(self):
        self.assertEqual(dict(role_timeout.ROUTE_TIMEOUT_CODES), TIMEOUT_CODES)

    def test_table_is_read_only(self):
        with self.assertRaises(TypeError):
            role_timeout.ROUTE_TIMEOUT_CODES['codex'] = 'CODEX_TIMEOUT'

    def test_codes_follow_base_route_code(self):
        for route, name in ADAPTER_NAMES.items():
            with self.subTest(route=route):
                stub = types.SimpleNamespace(name=name)
                self.assertEqual(base._route_code(stub, 'TIMEOUT'),
                                 role_timeout.ROUTE_TIMEOUT_CODES[route])

    def test_codes_survive_route_error_code_pattern(self):
        for route in ROUTES:
            code = role_timeout.ROUTE_TIMEOUT_CODES[route]
            with self.subTest(route=route):
                self.assertEqual(base.RouteError(code).code, code)

    def test_opencode_code_is_the_adapter_literal(self):
        self.assertEqual(role_timeout.ROUTE_TIMEOUT_CODES['opencode'], 'OPENCODE_TIMEOUT')


class ClassifyTests(unittest.TestCase):

    def assertRefused(self, code, route, error_code):
        with self.assertRaises(role_timeout.RoleTimeoutError) as caught:
            role_timeout.classify(route, error_code)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_each_route_exact_code_is_role_timeout(self):
        for route in ROUTES:
            with self.subTest(route=route):
                self.assertEqual(role_timeout.classify(route, TIMEOUT_CODES[route]),
                                 role_timeout.ROLE_TIMEOUT)

    def test_wrong_route_code_is_not_timeout(self):
        for route in ROUTES:
            for other in ROUTES:
                if other == route:
                    continue
                with self.subTest(route=route, code_of=other):
                    self.assertEqual(role_timeout.classify(route, TIMEOUT_CODES[other]),
                                     role_timeout.NOT_TIMEOUT)

    def test_non_timeout_codes_are_not_timeout(self):
        codes = (
            # quota and free-limit
            'CLAUDE_QUOTA_EXHAUSTED', 'AGY_QUOTA_EXHAUSTED', 'GW_AGENT_QUOTA_EXHAUSTED',
            'OPENCODE_QUOTA_EXHAUSTED', 'OPENCODE_FREE_LIMIT', 'OPENCODE_RATE_LIMITED',
            # auth, unavailable, crash, rejected output, screen refusal
            'CLAUDE_AUTH_FAILED', 'AGY_UNAVAILABLE', 'CLAUDE_RUNNER_FAILED',
            'CLAUDE_INTERRUPTED', 'AGY_EXIT_NONZERO', 'OPENCODE_OUTPUT_INVALID',
            'GW_AGENT_OUTPUT_INVALID', 'SENSITIVE_SCREEN_REFUSED', 'GW_AGENT_TOOL_REFUSED',
            'OPENCODE_CLEANUP_UNKNOWN', 'ROUTE_ERROR',
            # step limit, drive budget, resolver refusals, Codex
            'OPENCODE_STEPS_EXHAUSTED', 'MAX_STEPS', 'WAITING_EXECUTOR',
            'TIMEOUT_INVALID', 'TIMEOUT_OVER_HARD_CAP', 'TIMEOUT_OVER_ROUTE_MAX',
            'CODEX_TIMEOUT_WITH_CHANGES', 'CODEX_TIMEOUT',
        )
        for route in ROUTES:
            for code in codes:
                with self.subTest(route=route, code=code):
                    self.assertEqual(role_timeout.classify(route, code),
                                     role_timeout.NOT_TIMEOUT)

    def test_no_prefix_substring_or_case_match(self):
        variants = ('', 'TIMEOUT', '_TIMEOUT', 'CLAUDE_TIMEOUT ', ' CLAUDE_TIMEOUT',
                    'claude_timeout', 'Claude_Timeout', 'CLAUDE_TIMEOUT_RETRY',
                    'X_CLAUDE_TIMEOUT', 'CLAUDE_TIMEOUTS', 'CLAUDE_DEVELOPER_TIMEOUT',
                    'CLAUDE_TIMEOUT\n', 'CLAUDE-TIMEOUT')
        for code in variants:
            with self.subTest(code=code):
                self.assertEqual(role_timeout.classify('claude', code),
                                 role_timeout.NOT_TIMEOUT)
        self.assertEqual(role_timeout.classify('opencode', 'OPENCODE_DEVELOPER_TIMEOUT'),
                         role_timeout.NOT_TIMEOUT)
        self.assertEqual(role_timeout.classify('gw-agent', 'GW-AGENT_TIMEOUT'),
                         role_timeout.NOT_TIMEOUT)

    def test_unknown_route_is_refused(self):
        for route in ('codex', 'claude-developer', 'opencode-developer', 'gw_agent',
                      'CLAUDE', 'Claude', ' claude', '', 'unknown'):
            with self.subTest(route=route):
                self.assertRefused(role_timeout.ROUTE_UNKNOWN, route, 'CLAUDE_TIMEOUT')

    def test_non_str_input_is_refused(self):
        class Text(str):
            pass

        bad = (None, 1, 0, True, 1.0, b'claude', bytearray(b'claude'), ['claude'],
               ('claude',), {'claude'}, {'route': 'claude'}, object(), Text('claude'))
        for value in bad:
            with self.subTest(route=value):
                self.assertRefused(role_timeout.INPUT_INVALID, value, 'CLAUDE_TIMEOUT')
            with self.subTest(error_code=value):
                self.assertRefused(role_timeout.INPUT_INVALID, 'claude', value)
        self.assertRefused(role_timeout.INPUT_INVALID, 'claude', Text('CLAUDE_TIMEOUT'))
        self.assertRefused(role_timeout.INPUT_INVALID, None, None)
        # Input type is checked before the route lookup.
        self.assertRefused(role_timeout.INPUT_INVALID, 'codex', None)

    def test_refusal_is_llmtool_error_with_fixed_message(self):
        error = self.assertRefused(role_timeout.ROUTE_UNKNOWN, 'route-marker-7', 'X')
        self.assertIsInstance(error, LLMToolError)
        self.assertNotIn('route-marker-7', str(error))
        first = self.assertRefused(role_timeout.INPUT_INVALID, 7, 'X')
        self.assertEqual(str(first), str(error))


class CountTimeoutAttemptsTests(unittest.TestCase):

    def count(self, entries):
        return role_timeout.count_timeout_attempts(entries)

    def test_empty_audit_counts_zero(self):
        self.assertEqual(self.count([]), 0)
        self.assertEqual(self.count(()), 0)

    def test_one_full_retry_counts_one(self):
        self.assertEqual(self.count([transition(), adapter(LINK_1)]), 1)

    def test_blocked_transition_counts_one(self):
        self.assertEqual(self.count([transition('BLOCKED',
                                                code='WORKER_ROLE_TIMEOUT_RETRY_LIMIT')]), 1)

    def test_blocked_adapter_counts_zero(self):
        for code in ('WORKER_DISPATCH_AUTHORIZATION_CONSUMED',
                     'WORKER_DISPATCH_TRANSITION_NOT_FOUND', 'OWNER_PAUSED'):
            with self.subTest(code=code):
                self.assertEqual(self.count([adapter(None, 'BLOCKED', code=code)]), 0)
                self.assertEqual(self.count([adapter(LINK_1, 'BLOCKED', code=code)]), 0)

    def test_adapter_without_transition_counts_zero(self):
        self.assertEqual(self.count([adapter(None)]), 0)

    def test_role_retry_unclassifiable_shape_counts_one(self):
        shapes = (
            entry('ROLE_TIMEOUT_RETRY'),
            entry('ROLE_TIMEOUT_RETRY', retry_phase=None),
            entry('ROLE_TIMEOUT_RETRY', retry_phase='OTHER'),
            entry('ROLE_TIMEOUT_RETRY', retry_phase='adapter'),
            entry('ROLE_TIMEOUT_RETRY', retry_phase='ADAPTER '),
            entry('ROLE_TIMEOUT_RETRY', retry_phase=['ADAPTER']),
            entry('ROLE_TIMEOUT_RETRY', retry_phase=1),
            transition(authorization_id=LINK_1),
            entry('ROLE_TIMEOUT_RETRY', authorization_id=LINK_1),
        )
        for shape in shapes:
            with self.subTest(shape=shape):
                self.assertEqual(self.count([shape]), 1)

    def test_codex_continuation_split_by_authorization_id(self):
        self.assertEqual(self.count([entry('TIMEOUT_CONTINUATION', worker_key='codex')]), 1)
        self.assertEqual(self.count([entry('TIMEOUT_CONTINUATION', worker_key='codex',
                                           authorization_id=LINK_1)]), 0)
        self.assertEqual(self.count([entry('TIMEOUT_CONTINUATION', worker_key='codex'),
                                     entry('TIMEOUT_CONTINUATION', worker_key='codex',
                                           authorization_id=LINK_1)]), 1)

    def test_codex_malformed_authorization_id_counts_one(self):
        for value in (None, '', 1, ['x'], {'id': LINK_1}):
            with self.subTest(value=value):
                self.assertEqual(self.count([entry('TIMEOUT_CONTINUATION',
                                                   authorization_id=value)]), 1)

    def test_other_actions_count_zero(self):
        for action in ('DISPATCH', 'REPAIR', 'RECOVERY', 'RETRY', 'REASSIGN',
                       'role_timeout_retry', 'TIMEOUT_CONTINUATION_X', ''):
            with self.subTest(action=action):
                self.assertEqual(self.count([entry(action)]), 0)
                self.assertEqual(self.count([entry(action, 'BLOCKED')]), 0)

    def test_malformed_entries_count_one(self):
        for bad in (None, 'ROLE_TIMEOUT_RETRY', 1, ['ROLE_TIMEOUT_RETRY'], ('x',), {},
                    {'decision': 'ALLOWED'}, {'action': None}, {'action': 1},
                    {'action': ['ROLE_TIMEOUT_RETRY']}):
            with self.subTest(entry=bad):
                self.assertEqual(self.count([bad]), 1)

    def test_any_decision_worker_key_and_assignment(self):
        entries = [
            transition(worker_key='claude-developer', assignment_id=ASSIGNMENT_A),
            adapter(LINK_1, worker_key='claude-developer', assignment_id=ASSIGNMENT_A),
            transition('BLOCKED', worker_key='agy', assignment_id=ASSIGNMENT_B),
            transition(worker_key='opencode-developer', assignment_id=ASSIGNMENT_B),
            adapter(LINK_2, worker_key='opencode-developer', assignment_id=ASSIGNMENT_B),
            entry('TIMEOUT_CONTINUATION', worker_key='codex', assignment_id=ASSIGNMENT_B),
            entry('TIMEOUT_CONTINUATION', worker_key='codex', assignment_id=ASSIGNMENT_B,
                  authorization_id=LINK_3),
            entry('DISPATCH', worker_key='gw-agent'),
        ]
        self.assertEqual(self.count(entries), 4)

    def test_codex_and_role_retry_share_one_count(self):
        entries = [entry('TIMEOUT_CONTINUATION', worker_key='codex'),
                   entry('TIMEOUT_CONTINUATION', worker_key='codex',
                         authorization_id=LINK_1)] + full_retries(1)
        self.assertEqual(self.count(entries), 2)

    def test_full_retries_count_once_each(self):
        for retries in range(4):
            with self.subTest(retries=retries):
                entries = full_retries(retries)
                self.assertEqual(len(entries), 2 * retries)
                self.assertEqual(self.count(entries), retries)

    def test_limit_two(self):
        limit = 2
        self.assertLess(self.count(full_retries(1)), limit)  # second retry allowed
        self.assertEqual(self.count(full_retries(2)), limit)  # third refused
        # Counting raw entries would reach the limit after one retry.
        self.assertEqual(len(full_retries(1)), limit)

    def test_limit_three(self):
        limit = 3
        self.assertLess(self.count(full_retries(2)), limit)  # third retry allowed
        self.assertEqual(self.count(full_retries(3)), limit)  # fourth refused
        self.assertGreaterEqual(len(full_retries(2)), limit)

    def test_audit_must_be_list_or_tuple(self):
        for bad in (None, {}, {'action': 'ROLE_TIMEOUT_RETRY'}, 'ROLE_TIMEOUT_RETRY',
                    b'', 1, iter([]), (item for item in []), set(), frozenset()):
            with self.subTest(audit=type(bad).__name__):
                with self.assertRaises(role_timeout.RoleTimeoutError) as caught:
                    self.count(bad)
                self.assertEqual(caught.exception.code, role_timeout.AUDIT_INVALID)

    def test_input_not_modified(self):
        entries = full_retries(2) + [entry('TIMEOUT_CONTINUATION')]
        snapshot = [dict(item) for item in entries]
        self.count(entries)
        self.assertEqual(entries, snapshot)


if __name__ == '__main__':
    unittest.main()
