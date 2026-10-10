"""A11b.5 run budgets: unknown is never zero or within; admission grants nothing."""

import os
import socket
import subprocess
import time
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import patch

from llmtool import run_budget as b
from llmtool.errors import LLMToolError

P = b.CallPhase
S = b.BudgetStatus
C = b.BudgetCode
D = b.BudgetDimension
MESSAGE = 'run budget invalid; details omitted'


def budget(**overrides):
    values = dict(schema_version=1, max_provider_calls=4, max_repair_attempts=2,
                  wall_clock_ms=1000, max_input_tokens=100, max_output_tokens=50)
    values.update(overrides)
    return b.RunBudget(**values)


def call(inp=None, out=None, phase=P.PRIMARY):
    return b.CallUsage(phase, inp, out)


def ledger(calls=(), repairs=0, elapsed=0):
    return b.UsageLedger(tuple(calls), repairs, elapsed)


def fp(digest='a', code='TEST_FAILED'):
    return b.AttemptFingerprint(digest * 64, code)


class RunBudgetTest(unittest.TestCase):
    def assertInvalid(self, func, *args, **kwargs):
        with self.assertRaises(LLMToolError) as ctx:
            func(*args, **kwargs)
        self.assertEqual(str(ctx.exception), MESSAGE)

    def statuses(self, assessment):
        return [(d.dimension, d.status) for d in assessment.dimensions]

    def test_all_known_within(self):
        a = b.assess_budget(budget(), ledger([call(10, 5), call(20, 5, P.REVIEW)], elapsed=10))
        self.assertIs(a.status, S.WITHIN)
        self.assertTrue(a.within_budget)
        self.assertEqual(self.statuses(a), [(d, S.WITHIN) for d in D])
        self.assertEqual(a.dimension(D.INPUT_TOKENS).used, 30)
        self.assertEqual(a.dimension(D.OUTPUT_TOKENS).used, 10)
        self.assertEqual(b.admit_next(budget(), ledger([call(1, 1)]), P.PRIMARY),
                         C.WITHIN_BUDGET)

    def test_unknown_tokens_are_unknown_not_within(self):
        a = b.assess_budget(budget(), ledger([call(10, 5), call()]))
        self.assertIs(a.status, S.UNKNOWN)
        self.assertFalse(a.within_budget)
        self.assertIs(a.dimension(D.INPUT_TOKENS).status, S.UNKNOWN)
        self.assertIs(a.dimension(D.OUTPUT_TOKENS).status, S.UNKNOWN)

    def test_known_partial_over_limit_is_exceeded_despite_unknown(self):
        a = b.assess_budget(budget(), ledger([call(), call(101, 1)]))
        inp = a.dimension(D.INPUT_TOKENS)
        self.assertEqual((inp.status, inp.used, inp.unknown_calls), (S.EXCEEDED, 101, 1))
        self.assertIs(a.dimension(D.OUTPUT_TOKENS).status, S.UNKNOWN)
        self.assertIs(a.status, S.EXCEEDED)

    def test_unknown_never_zero_filled(self):
        all_unknown = b.assess_budget(budget(), ledger([call(), call(phase=P.RETRY)]))
        inp = all_unknown.dimension(D.INPUT_TOKENS)
        self.assertEqual((inp.status, inp.used, inp.unknown_calls), (S.UNKNOWN, None, 2))
        partial = b.assess_budget(budget(), ledger([call(), call(7, 3)]))
        out = partial.dimension(D.OUTPUT_TOKENS)
        self.assertEqual((out.status, out.used, out.unknown_calls), (S.UNKNOWN, 3, 1))
        empty = b.assess_budget(budget(), ledger()).dimension(D.INPUT_TOKENS)
        self.assertEqual((empty.status, empty.used, empty.unknown_calls), (S.WITHIN, 0, 0))
        self.assertFalse(call().known)
        self.assertTrue(call(0, 0).known)

    def test_partial_usage_pair_rejected(self):
        self.assertInvalid(b.CallUsage, P.PRIMARY, 1, None)
        self.assertInvalid(b.CallUsage, P.PRIMARY, None, 0)
        self.assertInvalid(b.CallUsage, P.PRIMARY, -1, 0)
        self.assertInvalid(b.CallUsage, P.PRIMARY, True, 0)
        self.assertInvalid(b.CallUsage, 'PRIMARY', 0, 0)
        self.assertInvalid(b.CallUsage, P.PRIMARY, b.MAX_TOKENS + 1, 0)

    def test_usage_from_provider(self):
        good = {'prompt_tokens': 12, 'completion_tokens': 3, 'total_tokens': 15}
        u = b.usage_from_provider(P.ESCALATION, good)
        self.assertEqual((u.phase, u.input_tokens, u.output_tokens), (P.ESCALATION, 12, 3))
        bad = [None, {}, 'x', {'prompt_tokens': 1, 'completion_tokens': 2},
               {'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 4},
               {'prompt_tokens': -1, 'completion_tokens': 2, 'total_tokens': 1},
               {'prompt_tokens': True, 'completion_tokens': 0, 'total_tokens': 1},
               {'input_tokens': 1, 'output_tokens': 2, 'total_tokens': 3},
               dict(good, model='m')]
        for metadata in bad:
            u = b.usage_from_provider(P.PRIMARY, metadata)
            self.assertEqual((u.input_tokens, u.output_tokens, u.known), (None, None, False))
        self.assertInvalid(b.usage_from_provider, 'PRIMARY', good)

    def test_elapsed_unknown_and_over(self):
        a = b.assess_budget(budget(), ledger(elapsed=None))
        wall = a.dimension(D.WALL_CLOCK)
        self.assertEqual((wall.status, wall.used, a.status), (S.UNKNOWN, None, S.UNKNOWN))
        self.assertEqual(b.admit_next(budget(), ledger(elapsed=None), P.PRIMARY),
                         C.ELAPSED_UNKNOWN)
        self.assertIs(b.assess_budget(budget(), ledger(elapsed=1000)).status, S.WITHIN)
        over = b.assess_budget(budget(), ledger(elapsed=1001))
        self.assertIs(over.dimension(D.WALL_CLOCK).status, S.EXCEEDED)
        self.assertEqual(b.admit_next(budget(), ledger(elapsed=1001), P.PRIMARY),
                         C.WALL_CLOCK_EXCEEDED)

    def test_call_limit(self):
        full = ledger([call(1, 1)] * 4)
        self.assertIs(b.assess_budget(budget(), full).dimension(D.PROVIDER_CALLS).status,
                      S.WITHIN)
        self.assertEqual(b.admit_next(budget(), full, P.PRIMARY), C.CALL_LIMIT_REACHED)
        self.assertEqual(b.admit_next(budget(), ledger([call(1, 1)] * 3), P.PRIMARY),
                         C.WITHIN_BUDGET)
        over = b.assess_budget(budget(), ledger([call(1, 1)] * 5))
        self.assertIs(over.dimension(D.PROVIDER_CALLS).status, S.EXCEEDED)
        self.assertIs(over.status, S.EXCEEDED)

    def test_repair_cap_is_two(self):
        self.assertEqual(b.MAX_REPAIR_ATTEMPTS, 2)
        self.assertInvalid(budget, max_repair_attempts=3)
        two = ledger([call(1, 1, P.REPAIR)] * 2, repairs=2)
        self.assertEqual(b.admit_next(budget(), two, P.REPAIR), C.REPAIR_LIMIT_REACHED)
        self.assertEqual(b.admit_next(budget(), two, P.REVIEW), C.WITHIN_BUDGET)
        self.assertEqual(b.admit_next(budget(), ledger(repairs=1), P.REPAIR), C.WITHIN_BUDGET)
        over = ledger(repairs=3)
        self.assertIs(b.assess_budget(budget(), over).dimension(D.REPAIR_ATTEMPTS).status,
                      S.EXCEEDED)
        self.assertEqual(b.admit_next(budget(), over, P.PRIMARY), C.REPAIR_LIMIT_REACHED)

    def test_zero_repair_budget_blocks_first_repair(self):
        zero = budget(max_repair_attempts=0)
        self.assertEqual(b.admit_next(zero, ledger(), P.REPAIR), C.REPAIR_LIMIT_REACHED)
        self.assertEqual(b.admit_next(zero, ledger(), P.PRIMARY), C.WITHIN_BUDGET)

    def test_admit_precedence(self):
        everything = ledger([call(), call(500, 500)] * 2, repairs=2, elapsed=5000)
        self.assertEqual(b.admit_next(budget(max_provider_calls=4), everything, P.REPAIR),
                         C.CALL_LIMIT_REACHED)
        roomy = budget(max_provider_calls=64)
        self.assertEqual(b.admit_next(roomy, everything, P.REPAIR), C.REPAIR_LIMIT_REACHED)
        self.assertEqual(b.admit_next(roomy, everything, P.RETRY), C.WALL_CLOCK_EXCEEDED)
        no_wall = ledger([call(), call(500, 500)], elapsed=None)
        self.assertEqual(b.admit_next(roomy, no_wall, P.RETRY), C.TOKEN_BUDGET_EXCEEDED)
        both_unknown = ledger([call()], elapsed=None)
        self.assertEqual(b.admit_next(roomy, both_unknown, P.RETRY), C.ELAPSED_UNKNOWN)
        self.assertEqual(b.admit_next(roomy, ledger([call(1, 1)], elapsed=1001), P.RETRY),
                         C.WALL_CLOCK_EXCEEDED)
        out_only = ledger([call(1, 51)])
        self.assertEqual(b.admit_next(roomy, out_only, P.RETRY), C.TOKEN_BUDGET_EXCEEDED)

    def test_unknown_usage_denied_admission(self):
        # Open question: a provider that never reports usage blocks a second call.
        self.assertEqual(b.admit_next(budget(), ledger(), P.PRIMARY), C.WITHIN_BUDGET)
        self.assertEqual(b.admit_next(budget(), ledger([call()]), P.PRIMARY), C.USAGE_UNKNOWN)

    def test_all_phases_count(self):
        calls = [call(10, 5, phase) for phase in P]
        a = b.assess_budget(budget(max_provider_calls=6), ledger(calls))
        self.assertEqual(a.dimension(D.PROVIDER_CALLS).used, 6)
        self.assertEqual(a.dimension(D.INPUT_TOKENS).used, 60)
        self.assertEqual(a.dimension(D.OUTPUT_TOKENS).used, 30)
        self.assertIs(a.status, S.WITHIN)
        self.assertEqual(b.admit_next(budget(max_provider_calls=6), ledger(calls), P.REVIEW),
                         C.CALL_LIMIT_REACHED)

    def test_repeated_failure_exact_pair(self):
        self.assertIsNone(b.detect_repeated_failure([]))
        self.assertIsNone(b.detect_repeated_failure([fp('a', 'X'), fp('b', 'X'), fp('a', 'Y')]))
        self.assertEqual(b.detect_repeated_failure(
            [fp('a', 'X'), fp('b', 'X'), fp('a', 'X'), fp('b', 'X')]), 2)
        self.assertIsNone(b.detect_repeated_failure([fp('a', 'X'), fp('a', 'x')]))
        self.assertInvalid(b.AttemptFingerprint, 'A' * 64, 'X')
        self.assertInvalid(b.AttemptFingerprint, 'a' * 63, 'X')
        self.assertInvalid(b.AttemptFingerprint, 'a' * 64, 'bad code')
        self.assertInvalid(b.detect_repeated_failure, [('a' * 64, 'X')])
        self.assertInvalid(b.detect_repeated_failure, None)

    def test_budget_requires_every_field(self):
        with self.assertRaises(TypeError):
            b.RunBudget(1, 4, 2, 1000, 100)
        bad = dict(schema_version=[0, 2, True, None], max_provider_calls=[0, 65, None, True, -1],
                   max_repair_attempts=[-1, 3, None, False], wall_clock_ms=[0, b.MAX_WALL_CLOCK_MS + 1, None, True],
                   max_input_tokens=[-1, b.MAX_TOKENS + 1, None, False, 1.0],
                   max_output_tokens=[-1, b.MAX_TOKENS + 1, None, True, '1'])
        for name, values in bad.items():
            for value in values:
                with self.subTest(name=name, value=value):
                    self.assertInvalid(budget, **{name: value})
        budget(max_provider_calls=64, wall_clock_ms=b.MAX_WALL_CLOCK_MS, max_input_tokens=0)
        self.assertInvalid(b.UsageLedger, (), -1, 0)
        self.assertInvalid(b.UsageLedger, (), 0, -1)
        self.assertInvalid(b.UsageLedger, ({},), 0, 0)
        self.assertInvalid(b.UsageLedger, [call()] * 1025, 0, 0)
        self.assertInvalid(b.assess_budget, None, ledger())
        self.assertInvalid(b.admit_next, budget(), ledger(), 'PRIMARY')
        with self.assertRaises(FrozenInstanceError):
            budget().max_provider_calls = 5

    def test_dimension_order_and_stable_json(self):
        a = b.assess_budget(budget(), ledger([call(), call(3, 4, P.REPAIR)], repairs=1,
                                             elapsed=7))
        self.assertEqual([d.dimension for d in a.dimensions], list(D))
        self.assertEqual(a.to_json(), (
            '{"dimensions":['
            '{"dimension":"PROVIDER_CALLS","limit":4,"status":"WITHIN","unknown_calls":0,"used":2},'
            '{"dimension":"REPAIR_ATTEMPTS","limit":2,"status":"WITHIN","unknown_calls":0,"used":1},'
            '{"dimension":"WALL_CLOCK","limit":1000,"status":"WITHIN","unknown_calls":0,"used":7},'
            '{"dimension":"INPUT_TOKENS","limit":100,"status":"UNKNOWN","unknown_calls":1,"used":3},'
            '{"dimension":"OUTPUT_TOKENS","limit":50,"status":"UNKNOWN","unknown_calls":1,"used":4}'
            '],"status":"UNKNOWN"}'))
        self.assertEqual(budget().to_json(),
            '{"max_input_tokens":100,"max_output_tokens":50,"max_provider_calls":4,'
            '"max_repair_attempts":2,"schema_version":1,"wall_clock_ms":1000}')
        self.assertInvalid(b.BudgetAssessment, S.WITHIN, tuple(reversed(a.dimensions)))
        self.assertInvalid(b.BudgetAssessment, S.WITHIN, a.dimensions)

    def test_pure_no_io(self):
        def boom(*args, **kwargs):
            raise AssertionError('I/O attempted')
        with patch('builtins.open', boom), patch.object(os, 'open', boom), \
                patch.object(subprocess, 'Popen', boom), patch.object(socket, 'socket', boom), \
                patch.object(time, 'time', boom):
            usage = b.usage_from_provider(P.PRIMARY, {'prompt_tokens': 1,
                                                      'completion_tokens': 1, 'total_tokens': 2})
            led = ledger([usage, b.usage_from_provider(P.REVIEW, None)])
            self.assertIs(b.assess_budget(budget(), led).status, S.UNKNOWN)
            self.assertEqual(b.admit_next(budget(), led, P.REPAIR), C.USAGE_UNKNOWN)
            self.assertEqual(b.detect_repeated_failure([fp(), fp()]), 1)
            b.assess_budget(budget(), led).to_json()


if __name__ == '__main__':
    unittest.main()
