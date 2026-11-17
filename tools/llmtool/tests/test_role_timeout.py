"""Tests for llmtool.role_timeout (#1019 A11 row 41 PR 1a and PR 1b).

No provider calls and no I/O beyond reading role_timeout's own source for the
import check. Values are fixed route names, codes and short-pattern hex ids,
or hex ids that a seeded stdlib random.Random makes at run time; no
credential-shaped strings (common-core.md section 4).

The count tests follow docs/design/a11-row41-timeout-retry-1019.md section 6
(attempt count 1-4, property tests P1-P5) for the monotone count of section
3.5.
"""

import ast
from collections.abc import Mapping
import copy
import inspect
import random
import types
import unittest

from llmtool import role_timeout
from llmtool import worker_dispatch
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

ROLE = 'ROLE_TIMEOUT_RETRY'
CODEX = 'TIMEOUT_CONTINUATION'
UNRELATED_NAMES = ('DISPATCH', 'RETRY', 'REASSIGN', 'RECOVERY', 'REPAIR',
                   'REVIEWER_HANDOFF', 'REVIEWER_RETRY', 'COMMITTED_RESULT_CARRY',
                   'START_RETRY')
BINDING_FIELDS = ('assignment_id', 'source_sha', 'contract_digest')
PAIR_FIELDS = ('action', 'worker_key') + BINDING_FIELDS
UNREAD_FIELDS = ('reason_code', 'policy_sha256', 'at')
HEX_SIZES = {'attempt_id': 32, 'authorization_id': 32, 'assignment_id': 32,
             'source_sha': 40, 'contract_digest': 64}

ASSIGNMENT_A = '0a' * 16
ASSIGNMENT_B = '0b' * 16
SOURCE_A = '0c' * 20
SOURCE_B = '0e' * 20
DIGEST_A = '0d' * 32
DIGEST_B = '0f' * 32
AT = '2026-11-16T00:00:00Z'


class Ids:
    """Sequential 32-hex attempt ids (000...01, 000...02, ...)."""

    def __init__(self, start=1):
        self.value = start - 1

    def __call__(self):
        self.value += 1
        return '%032x' % self.value


def timeout_entry(attempt_id, action=ROLE, decision='ALLOWED', *, phase=None,
                  authorization_id=None, worker_key='claude-developer',
                  assignment_id=ASSIGNMENT_A, source_sha=SOURCE_A, contract_digest=DIGEST_A):
    """One audit entry in the shape worker_dispatch.audit_entry writes."""
    entry = {
        'attempt_id': attempt_id, 'worker_key': worker_key, 'action': action,
        'decision': decision,
        'reason_code': 'OWNER_POLICY_ALLOWED' if decision == 'ALLOWED' else 'OWNER_PAUSED',
        'policy_sha256': None, 'at': AT,
        'assignment_id': assignment_id, 'source_sha': source_sha,
        'contract_digest': contract_digest,
    }
    if phase is not None:
        entry['retry_phase'] = phase
    if authorization_id is not None:
        entry['authorization_id'] = authorization_id
    return entry


def transition(attempt_id, action=ROLE, decision='ALLOWED', **fields):
    return timeout_entry(attempt_id, action, decision,
                         phase='TRANSITION' if action == ROLE else None, **fields)


def adapter(attempt_id, authorization_id, action=ROLE, decision='ALLOWED', **fields):
    return timeout_entry(attempt_id, action, decision,
                         phase='ADAPTER' if action == ROLE else None,
                         authorization_id=authorization_id, **fields)


def pair_fields(entry):
    return {field: entry[field] for field in ('worker_key',) + BINDING_FIELDS}


def unrelated(action, attempt_id, **extra):
    entry = {'attempt_id': attempt_id, 'worker_key': 'claude-developer', 'action': action,
             'decision': 'ALLOWED', 'reason_code': 'OWNER_POLICY_ALLOWED',
             'policy_sha256': None, 'at': AT}
    entry.update(extra)
    return entry


def full_retry(ids, action=ROLE, **fields):
    """An ALLOWED transition and its ALLOWED adapter, as one real retry writes them."""
    first = transition(ids(), action, **fields)
    return [first, adapter(ids(), first['attempt_id'], action, **fields)]


def full_retries(retries, ids=None, action=ROLE, **fields):
    ids = Ids() if ids is None else ids
    entries = []
    for _ in range(retries):
        entries.extend(full_retry(ids, action, **fields))
    return entries


def count(entries):
    return role_timeout.count_timeout_attempts(entries)


# Reference rule, written out from design section 3.5 independently of the
# module, so the property tests can classify each mutation (P3) and cross-check
# the module on every generated audit.

def _is_hex(value, size):
    return (type(value) is str and len(value) == size and
            all(char in '0123456789abcdef' for char in value))


def reference_class(entry):
    if not isinstance(entry, Mapping):
        return 'OTHER', None
    action = entry.get('action')
    if type(action) is not str:
        return 'OTHER', None
    if action in UNRELATED_NAMES:
        return 'UNRELATED', None
    if action not in (ROLE, CODEX):
        return 'OTHER', None
    worker_key, decision = entry.get('worker_key'), entry.get('decision')
    if not (_is_hex(entry.get('attempt_id'), 32) and _is_hex(entry.get('assignment_id'), 32) and
            _is_hex(entry.get('source_sha'), 40) and
            _is_hex(entry.get('contract_digest'), 64) and
            type(worker_key) is str and worker_key != '' and
            type(decision) is str and decision in ('ALLOWED', 'BLOCKED')):
        return 'OTHER', None
    has_authorization = 'authorization_id' in entry
    if action == CODEX:
        phase_ok = 'retry_phase' not in entry
    else:
        phase = entry.get('retry_phase')
        phase_ok = type(phase) is str and phase == (
            'ADAPTER' if has_authorization else 'TRANSITION')
    if not phase_ok:
        return 'OTHER', None
    if not has_authorization:
        return 'TRANSITION', entry['attempt_id']
    authorization_id = entry['authorization_id']
    if not _is_hex(authorization_id, 32) or authorization_id == entry['attempt_id']:
        return 'OTHER', None
    return 'ADAPTER', authorization_id


def reference_count(entries):
    classes = [reference_class(entry) for entry in entries]
    total = sum(1 for kind, _ in classes if kind != 'UNRELATED')
    completed = set()
    for later, (kind, identity) in enumerate(classes):
        if kind != 'ADAPTER':
            continue
        for earlier in range(later):
            other_kind, other_identity = classes[earlier]
            if (other_kind == 'TRANSITION' and other_identity == identity and
                    entries[earlier]['decision'] == 'ALLOWED' and
                    all(entries[earlier][field] == entries[later][field]
                        for field in PAIR_FIELDS)):
                completed.add(identity)
    return total - len(completed)


class RouteTableTests(unittest.TestCase):

    def test_table_is_exactly_the_four_routes(self):
        self.assertEqual(dict(role_timeout.ROUTE_TIMEOUT_CODES), TIMEOUT_CODES)
        self.assertEqual(role_timeout.ROLE_TIMEOUT_CODES, frozenset(TIMEOUT_CODES.values()))

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


class ModuleContractTests(unittest.TestCase):

    def test_unrelated_copy_equals_audit_actions_without_timeout_actions(self):
        self.assertEqual(role_timeout.UNRELATED_ACTIONS,
                         worker_dispatch.AUDIT_ACTIONS - {ROLE, CODEX})
        self.assertEqual(role_timeout.TIMEOUT_ACTIONS, frozenset((ROLE, CODEX)))
        self.assertLessEqual(role_timeout.TIMEOUT_ACTIONS, worker_dispatch.AUDIT_ACTIONS)
        self.assertEqual(role_timeout.UNRELATED_ACTIONS, frozenset(UNRELATED_NAMES))

    def test_module_imports_no_dispatch_store_or_advance_module(self):
        tree = ast.parse(inspect.getsource(role_timeout))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add(node.module or '')
                names.update(alias.name for alias in node.names)
        for forbidden in ('worker_dispatch', 'task_store', 'task_advance'):
            self.assertFalse(any(forbidden in name for name in names), forbidden)


class CountTimeoutAttemptsTests(unittest.TestCase):
    """Design section 6, attempt count tests 1-4, and the four findings."""

    def test_empty_audit_counts_zero(self):
        self.assertEqual(count([]), 0)
        self.assertEqual(count(()), 0)

    def test_one_full_retry_counts_one_for_both_actions(self):
        for action in (ROLE, CODEX):
            with self.subTest(action=action):
                self.assertEqual(count(full_retries(1, action=action)), 1)

    def test_k_full_retries_count_k(self):
        for action in (ROLE, CODEX):
            for retries in range(4):
                with self.subTest(action=action, retries=retries):
                    entries = full_retries(retries, action=action)
                    self.assertEqual(len(entries), 2 * retries)
                    self.assertEqual(count(entries), retries)
                    self.assertEqual(count(tuple(entries)), retries)

    def test_limit_two_and_three_count_logical_attempts(self):
        # Limit 2: one retry leaves 1 (< 2, allowed), two leave 2 (refused).
        # Limit 3: two retries leave 2 (< 3, allowed), three leave 3 (refused).
        # Counting raw entries would reach each limit one retry early.
        for retries in (1, 2, 3):
            with self.subTest(retries=retries):
                self.assertEqual(count(full_retries(retries)), retries)
                self.assertEqual(len(full_retries(retries)), 2 * retries)

    def test_blocked_transition_counts_one(self):
        ids = Ids()
        for action in (ROLE, CODEX):
            with self.subTest(action=action):
                self.assertEqual(count([transition(ids(), action, 'BLOCKED')]), 1)

    def test_transition_whose_adapter_never_wrote_counts_one(self):
        ids = Ids()
        for action in (ROLE, CODEX):
            with self.subTest(action=action):
                self.assertEqual(count([transition(ids(), action)]), 1)

    def test_paused_adapter_completes_the_pair(self):
        ids = Ids()
        for action in (ROLE, CODEX):
            with self.subTest(action=action):
                first = transition(ids(), action)
                paused = adapter(ids(), first['attempt_id'], action, 'BLOCKED')
                self.assertEqual(count([first, paused]), 1)

    def test_consumed_adapter_adds_one(self):
        ids = Ids()
        for action in (ROLE, CODEX):
            for decision in ('ALLOWED', 'BLOCKED'):
                with self.subTest(action=action, decision=decision):
                    entries = full_retry(ids, action)
                    again = adapter(ids(), entries[0]['attempt_id'], action, decision)
                    self.assertEqual(count(entries + [again]), 2)
                    self.assertEqual(count(entries + [again, dict(again, attempt_id=ids())]), 3)

    def test_transition_not_found_adapter_adds_one(self):
        ids = Ids()
        not_found = timeout_entry(ids(), ROLE, 'BLOCKED', phase='ADAPTER')
        self.assertEqual(count([not_found]), 1)
        self.assertEqual(count(full_retries(1, ids) + [not_found]), 2)

    def test_role_retry_shape_errors_count_one(self):
        ids = Ids()
        shapes = (
            timeout_entry(ids(), ROLE),  # no retry_phase
            timeout_entry(ids(), ROLE, phase='OTHER'),
            timeout_entry(ids(), ROLE, phase='adapter'),
            timeout_entry(ids(), ROLE, phase='ADAPTER '),
            dict(timeout_entry(ids(), ROLE), retry_phase=None),
            dict(timeout_entry(ids(), ROLE), retry_phase=['ADAPTER']),
            dict(timeout_entry(ids(), ROLE), retry_phase=1),
            transition(ids(), authorization_id='00' * 16),  # TRANSITION with id
            timeout_entry(ids(), ROLE, phase='ADAPTER'),  # ADAPTER without id
            timeout_entry(ids(), ROLE, authorization_id='00' * 16),  # id, no phase
        )
        for shape in shapes:
            with self.subTest(shape=shape):
                self.assertEqual(count([shape]), 1)

    def test_codex_entry_with_any_retry_phase_counts_one(self):
        ids = Ids()
        for phase in ('TRANSITION', 'ADAPTER', 'OTHER', None, 1):
            with self.subTest(phase=phase):
                first = transition(ids(), CODEX)
                entries = [first, adapter(ids(), first['attempt_id'], CODEX)]
                entries[1]['retry_phase'] = phase
                self.assertEqual(count([dict(first, retry_phase=phase)]), 1)
                self.assertEqual(count(entries), 2)

    def test_adapter_naming_its_own_attempt_counts_one(self):
        own = '0123456789abcdef' * 2
        for action in (ROLE, CODEX):
            with self.subTest(action=action):
                self.assertEqual(count([adapter(own, own, action)]), 1)

    def test_pairing_needs_earlier_allowed_transition_of_same_action_key_and_binding(self):
        ids = Ids()
        first = transition(ids())
        good = adapter(ids(), first['attempt_id'])
        self.assertEqual(count([first, good]), 1)
        cases = {
            'adapter before transition': [good, first],
            'after BLOCKED transition': [dict(first, decision='BLOCKED'), good],
            'other action': [first, adapter(ids(), first['attempt_id'], CODEX)],
            'other worker key': [first, dict(good, worker_key='agy')],
            'retired alias': [transition(first['attempt_id'], worker_key='gw-agent'),
                              dict(good, worker_key='company-gw-agent')],
            'other assignment': [first, dict(good, assignment_id=ASSIGNMENT_B)],
            'other source': [first, dict(good, source_sha=SOURCE_B)],
            'other digest': [first, dict(good, contract_digest=DIGEST_B)],
            'names nothing': [first, dict(good, authorization_id='ff' * 16)],
        }
        for label, entries in cases.items():
            with self.subTest(case=label):
                self.assertEqual(count(entries), 2)

    def test_two_transitions_never_share_one_attempt(self):
        ids = Ids()
        first = transition(ids())
        twin = dict(first)
        self.assertEqual(count([first, twin]), 2)
        self.assertEqual(count([first, twin, adapter(ids(), first['attempt_id'])]), 2)

    def test_each_unrelated_action_counts_zero_whatever_its_fields(self):
        ids = Ids()
        for action in UNRELATED_NAMES:
            variants = (
                unrelated(action, ids()),
                unrelated(action, ids(), decision='BLOCKED'),
                {'action': action},
                {'action': action, 'attempt_id': None, 'decision': 'MAYBE', 'worker_key': 7,
                 'authorization_id': 'x', 'retry_phase': 'ADAPTER'},
                dict(transition(ids()), action=action),
            )
            for entry in variants:
                with self.subTest(action=action, entry=entry):
                    self.assertEqual(count([entry]), 0)

    def test_unknown_and_timeout_like_action_names_count_one(self):
        names = ('role_timeout_retry', 'Role_Timeout_Retry', ' ROLE_TIMEOUT_RETRY',
                 'ROLE_TIMEOUT_RETRY ', 'ROLE_TIMEOUT_RETRY\n', 'X_ROLE_TIMEOUT_RETRY',
                 'ROLE_TIMEOUT_RETRY_X', 'timeout_continuation', 'X_TIMEOUT_CONTINUATION',
                 'TIMEOUT_CONTINUATION_X', 'TIMEOUT_RETRY', 'dispatch', 'DISPATCH ',
                 'NEW_AUDIT_ACTION', '')
        ids = Ids()
        for name in names:
            with self.subTest(action=name):
                self.assertEqual(count([dict(transition(ids()), action=name)]), 1)
                self.assertEqual(count([unrelated(name, ids())]), 1)

    def test_str_subclass_action_counts_one(self):
        class Text(str):
            pass

        for name in (ROLE, CODEX, 'DISPATCH'):
            with self.subTest(action=name):
                self.assertEqual(count([dict(transition('00' * 16), action=Text(name))]), 1)

    def test_malformed_entries_count_one(self):
        for bad in (None, ROLE, 1, [ROLE], ('x',), {}, {'decision': 'ALLOWED'},
                    {'action': None}, {'action': 1}, {'action': [ROLE]}, {'action': ROLE}):
            with self.subTest(entry=bad):
                self.assertEqual(count([bad]), 1)

    def test_common_field_errors_count_one(self):
        ids = Ids()
        good = transition(ids())
        for field, size in (('attempt_id', 32), ('assignment_id', 32), ('source_sha', 40),
                            ('contract_digest', 64)):
            for value in (None, 7, '', 'A' * size, '0' * (size - 1), '0' * (size + 1),
                          'g' * size):
                with self.subTest(field=field, value=value):
                    self.assertEqual(count([dict(good, **{field: value})]), 1)
        for field, value in (('worker_key', ''), ('worker_key', None), ('worker_key', 3),
                             ('decision', 'MAYBE'), ('decision', 'allowed'),
                             ('decision', None)):
            with self.subTest(field=field, value=value):
                self.assertEqual(count([dict(good, **{field: value})]), 1)

    def test_any_decision_worker_key_and_assignment_share_one_count(self):
        ids = Ids()
        entries = (full_retries(1, ids, worker_key='claude-developer') +
                   [transition(ids(), decision='BLOCKED', worker_key='agy',
                               assignment_id=ASSIGNMENT_B)] +
                   full_retries(1, ids, worker_key='opencode-developer',
                                assignment_id=ASSIGNMENT_B) +
                   full_retries(1, ids, CODEX, worker_key='codex-developer') +
                   [unrelated('DISPATCH', ids(), worker_key='gw-agent')])
        self.assertEqual(count(entries), 4)

    def test_finding_1_unvalidated_adapters_count(self):
        ids = Ids()
        base_entries = full_retries(1, ids)
        lone = timeout_entry(ids(), ROLE, 'ALLOWED', phase='ADAPTER')
        self.assertEqual(count(base_entries + [lone]), 2)
        altered = copy.deepcopy(base_entries)
        altered[0]['retry_phase'] = 'ADAPTER'
        self.assertEqual(count(altered), 2)

    def test_finding_2_missing_adapter_fields_count(self):
        ids = Ids()
        for action in (ROLE, CODEX):
            base_entries = full_retries(1, ids, action)
            for field in ('authorization_id', 'attempt_id', 'worker_key', 'decision',
                          'assignment_id', 'source_sha', 'contract_digest'):
                with self.subTest(action=action, field=field):
                    altered = copy.deepcopy(base_entries)
                    del altered[1][field]
                    self.assertGreaterEqual(count(altered), 1)
                    self.assertEqual(count(altered), 2)

    def test_finding_3_malformed_action_strings_count(self):
        ids = Ids()
        base_entries = full_retries(1, ids)
        for action in ('role_timeout_retry', 'ROLE_TIMEOUT_RETRY ', 'X_ROLE_TIMEOUT_RETRY',
                       'TIMEOUT_RETRY'):
            with self.subTest(action=action):
                altered = copy.deepcopy(base_entries)
                altered[0]['action'] = action
                self.assertEqual(count(altered), 2)

    def test_finding_4_reused_authorization_id_counts(self):
        ids = Ids()
        base_entries = full_retries(1, ids)
        identity = base_entries[0]['attempt_id']
        self.assertEqual(count(base_entries + [adapter(ids(), identity)]), 2)
        codex_copy = dict(transition(ids(), CODEX), authorization_id=identity)
        self.assertEqual(count(base_entries + [codex_copy]), 2)
        role_copy = dict(transition(ids()), authorization_id=identity)
        self.assertEqual(count(base_entries + [role_copy]), 2)
        self.assertEqual(count([adapter(ids(), 'ee' * 16)]), 1)

    def test_audit_must_be_list_or_tuple(self):
        for bad in (None, {}, {'action': ROLE}, ROLE, b'', 1, iter([]),
                    (item for item in []), set(), frozenset()):
            with self.subTest(audit=type(bad).__name__):
                with self.assertRaises(role_timeout.RoleTimeoutError) as caught:
                    count(bad)
                self.assertEqual(caught.exception.code, role_timeout.AUDIT_INVALID)

    def test_input_not_modified(self):
        entries = full_retries(2) + [transition('ab' * 16, CODEX), None, {'action': 1}]
        snapshot = copy.deepcopy(entries)
        count(entries)
        self.assertEqual(entries, snapshot)


# Property tests (design section 6, P1-P5). Fixed seeds and a fixed number of
# cases per seed: every run checks the same audits. All ids, SHAs and digests
# are hex values the seeded generator makes; the only literal strings are
# action names, phases, decisions and route worker keys.

SEEDS = (1019, 2184, 41, 7, 3)
CASES_PER_SEED = 16
MAX_AUDIT_LENGTH = 20
WORKER_KEYS = ('claude-developer', 'agy', 'gw-agent')
MANGLED_ACTIONS = ('role_timeout_retry', 'Role_Timeout_Retry', ' ROLE_TIMEOUT_RETRY',
                   'ROLE_TIMEOUT_RETRY ', 'ROLE_TIMEOUT_RETRY\n', 'X_ROLE_TIMEOUT_RETRY',
                   'ROLE_TIMEOUT_RETRY_X', 'timeout_continuation', 'TIMEOUT_CONTINUATION ',
                   'X_TIMEOUT_CONTINUATION', 'TIMEOUT_CONTINUATION_X', 'TIMEOUT_RETRY',
                   'UNKNOWN_ACTION', '', None, 3, [ROLE])
NON_MAPPINGS = (None, ROLE, 7, [ROLE], (CODEX,))


class AuditGenerator:
    """Seeded generator of mixed audits (design section 6, property tests)."""

    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.bindings = [self.binding() for _ in range(2)]

    def hex(self, size):
        return '%0*x' % (size, self.rng.getrandbits(4 * size))

    def binding(self):
        return {'assignment_id': self.hex(32), 'source_sha': self.hex(40),
                'contract_digest': self.hex(64)}

    def fields(self):
        return dict(self.rng.choice(self.bindings), worker_key=self.rng.choice(WORKER_KEYS))

    def action(self):
        return self.rng.choice((ROLE, CODEX))

    def decision(self):
        return self.rng.choice(('ALLOWED', 'ALLOWED', 'BLOCKED'))

    def transition(self, attempt_id=None):
        return transition(attempt_id or self.hex(32), self.action(), self.decision(),
                          **self.fields())

    def adapter_for(self, target):
        return adapter(self.hex(32), target['attempt_id'], target['action'], self.decision(),
                       **pair_fields(target))

    def mismatched_adapter(self, target):
        entry_fields = pair_fields(target)
        action = target['action']
        field = self.rng.choice(PAIR_FIELDS)
        if field == 'action':
            action = CODEX if action == ROLE else ROLE
        elif field == 'worker_key':
            entry_fields['worker_key'] = ('company-gw-agent' if target['worker_key'] == 'gw-agent'
                                          else 'opencode-developer')
        else:
            entry_fields[field] = self.hex(HEX_SIZES[field])
        return adapter(self.hex(32), target['attempt_id'], action, self.decision(),
                       **entry_fields)

    def malformed(self):
        entry = self.rng.choice((self.transition(), self.adapter_for(self.transition())))
        label, mutated, _unread = self.rng.choice(mutations(entry))
        return mutated

    def candidate(self, audit, pending):
        transitions = [entry for entry in audit if reference_class(entry)[0] == 'TRANSITION']
        adapters = [entry for entry in audit if reference_class(entry)[0] == 'ADAPTER']
        kind = self.rng.randrange(13)
        if kind == 1 and pending:
            return self.transition(pending.pop())  # named earlier by an adapter
        if kind == 2 and transitions:
            return self.adapter_for(self.rng.choice(transitions))
        if kind == 3:
            later = self.hex(32)  # an adapter naming a transition that comes later
            pending.append(later)
            return adapter(self.hex(32), later, self.action(), self.decision(), **self.fields())
        if kind == 4:
            return adapter(self.hex(32), self.hex(32), self.action(), self.decision(),
                           **self.fields())
        if kind == 5:
            own = self.hex(32)
            return adapter(own, own, self.action(), self.decision(), **self.fields())
        if kind == 6 and adapters:
            repeat = dict(self.rng.choice(adapters), attempt_id=self.hex(32),
                          decision=self.decision())
            return repeat
        if kind == 7 and transitions:
            return self.mismatched_adapter(self.rng.choice(transitions))
        if kind == 8:
            return self.malformed()
        if kind == 9:
            entry = self.transition()
            action = self.rng.choice(MANGLED_ACTIONS)
            if action is None and self.rng.random() < 0.5:
                del entry['action']
            else:
                entry['action'] = action
            return entry
        if kind == 10:
            return copy.deepcopy(self.rng.choice(NON_MAPPINGS))
        if kind == 11:
            return unrelated(self.rng.choice(UNRELATED_NAMES), self.hex(32), **self.fields())
        return self.transition()

    def audit(self):
        entries, pending = [], []
        for _ in range(self.rng.randrange(MAX_AUDIT_LENGTH + 1)):
            entries.append(self.candidate(entries, pending))
        return entries

    def audits(self):
        return [self.audit() for _ in range(CASES_PER_SEED)]


def mutations(entry):
    """(label, mutated copy, unread_only) for one dict entry: the P3 mutation list."""
    result = []

    def put(label, value, unread=False):
        result.append((label, value, unread))

    for key in list(entry):
        changed = dict(entry)
        del changed[key]
        put('delete ' + key, changed, key in UNREAD_FIELDS)
        for bad in (None, 7, '', ['x']):
            put('%s=%r' % (key, bad), dict(entry, **{key: bad}), key in UNREAD_FIELDS)
    for key, size in HEX_SIZES.items():
        value = entry.get(key)
        if type(value) is str:
            put(key + ' upper', dict(entry, **{key: value.upper()}))
            put(key + ' shorter', dict(entry, **{key: value[:-1]}))
            put(key + ' longer', dict(entry, **{key: value + '0'}))
            put(key + ' non-hex', dict(entry, **{key: 'g' + value[1:]}))
    action = entry.get('action')
    if type(action) is str:
        for name in (action.lower(), action + ' ', ' ' + action, action + '\n',
                     'X_' + action, action + '_X', 'TIMEOUT_RETRY', 'ROLE_TIMEOUT'):
            put('action %r' % name, dict(entry, action=name))
    put('unknown phase', dict(entry, retry_phase='UNKNOWN'))
    if 'attempt_id' in entry:
        put('own authorization', dict(entry, authorization_id=entry['attempt_id']))
    put('unknown decision', dict(entry, decision='MAYBE'))
    put('other reason', dict(entry, reason_code='OTHER_REASON'), True)
    put('other policy', dict(entry, policy_sha256='e' * 64), True)
    put('other time', dict(entry, at='2026-11-17T00:00:00Z'), True)
    return result


class MonotoneCountPropertyTests(unittest.TestCase):

    def generated(self):
        for seed in SEEDS:
            generator = AuditGenerator(seed)
            for index, audit in enumerate(generator.audits()):
                yield seed, index, generator, audit

    def test_module_count_equals_reference_rule(self):
        for seed, index, _generator, audit in self.generated():
            with self.subTest(seed=seed, case=index):
                self.assertEqual(count(audit), reference_count(audit))

    def test_p1_adding_an_entry_anywhere_never_lowers_the_count(self):
        for seed, index, generator, audit in self.generated():
            before = count(audit)
            pending = []
            for _ in range(6):
                entry = generator.candidate(audit, pending)
                for position in range(len(audit) + 1):
                    with self.subTest(seed=seed, case=index, position=position):
                        grown = audit[:position] + [entry] + audit[position:]
                        self.assertGreaterEqual(count(grown), before)
                self.assertGreaterEqual(count(audit + [entry]), before)

    def test_p2_duplicating_an_entry_never_lowers_the_count(self):
        for seed, index, _generator, audit in self.generated():
            before = count(audit)
            for source in range(len(audit)):
                for position in range(len(audit) + 1):
                    with self.subTest(seed=seed, case=index, source=source, position=position):
                        grown = (audit[:position] + [copy.deepcopy(audit[source])] +
                                 audit[position:])
                        self.assertGreaterEqual(count(grown), before)

    def test_p3_field_mutation_never_lowers_the_count(self):
        checked = 0
        for seed, index, generator, audit in self.generated():
            before = count(audit)
            for position, entry in enumerate(audit):
                original = reference_class(entry)[0]
                if original == 'UNRELATED':
                    # Altering an exact unrelated entry into any candidate entry.
                    replacements = [('candidate', generator.candidate(audit, []), False)
                                    for _ in range(3)]
                elif type(entry) is dict:
                    replacements = mutations(entry)
                else:
                    continue
                for label, mutated, unread in replacements:
                    result = reference_class(mutated)[0]
                    # Scope (design P3): results with no valid identity, changes
                    # to fields the count does not read, and any change of an
                    # unrelated entry. Other rewrites are refused by the store.
                    if not (original == 'UNRELATED' or result == 'OTHER' or unread):
                        continue
                    altered = audit[:position] + [mutated] + audit[position + 1:]
                    with self.subTest(seed=seed, case=index, position=position, mutation=label):
                        self.assertGreaterEqual(count(altered), before)
                    checked += 1
        self.assertGreater(checked, 1000)

    def test_p4_writer_sequences_count_exactly_the_table(self):
        for seed in SEEDS:
            generator = AuditGenerator(seed)
            rng = generator.rng
            for case in range(CASES_PER_SEED):
                audit, deferred, completed, expected = [], [], [], 0
                for _ in range(rng.randrange(8)):
                    op = rng.choice(('FULL', 'BLOCKED', 'NO_ADAPTER', 'PAUSED', 'CONSUMED',
                                     'NOT_FOUND', 'UNRELATED'))
                    action, fields = generator.action(), generator.fields()
                    if op == 'UNRELATED':
                        audit.append(unrelated(rng.choice(UNRELATED_NAMES), generator.hex(32),
                                               **fields))
                        continue
                    if op == 'CONSUMED':
                        if not completed:
                            continue
                        target = rng.choice(completed)
                        audit.append(adapter(generator.hex(32), target['attempt_id'],
                                             target['action'], rng.choice(('ALLOWED', 'BLOCKED')),
                                             **pair_fields(target)))
                    elif op == 'NOT_FOUND':
                        audit.append(timeout_entry(
                            generator.hex(32), action, 'BLOCKED',
                            phase='ADAPTER' if action == ROLE else None, **fields))
                    elif op in ('BLOCKED', 'NO_ADAPTER'):
                        audit.append(transition(generator.hex(32), action,
                                                'BLOCKED' if op == 'BLOCKED' else 'ALLOWED',
                                                **fields))
                    else:
                        first = transition(generator.hex(32), action, **fields)
                        audit.append(first)
                        second = adapter(generator.hex(32), first['attempt_id'], action,
                                         'ALLOWED' if op == 'FULL' else 'BLOCKED', **fields)
                        # The adapter may be written later, after other entries.
                        if rng.random() < 0.5:
                            audit.append(second)
                        else:
                            deferred.append(second)
                        completed.append(first)
                    expected += 1
                rng.shuffle(deferred)
                audit.extend(deferred)
                with self.subTest(seed=seed, case=case):
                    self.assertEqual(count(audit), expected)

    def test_p5_bounds_and_purity(self):
        for seed, index, generator, audit in self.generated():
            snapshot = copy.deepcopy(audit)
            with self.subTest(seed=seed, case=index):
                value = count(audit)
                self.assertGreaterEqual(value, 0)
                self.assertLessEqual(value, len(audit))
                self.assertEqual(audit, snapshot)
                only_unrelated = [unrelated(generator.rng.choice(UNRELATED_NAMES),
                                            generator.hex(32)) for _ in audit]
                self.assertEqual(count(only_unrelated), 0)


if __name__ == '__main__':
    unittest.main()
