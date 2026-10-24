import json
import os
import stat
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from llmtool.codex_app_server_budget import (
    MAX_LAUNCHES, MAX_WALL_CLOCK_SECONDS, Denial, OutputCapExceeded, Reservation,
    RunLedger, default_budget)
from llmtool.errors import LLMToolError
from llmtool.run_budget import BudgetCode, CallPhase


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class RunLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.clock = Clock()

    def ledger(self, run_id="run-1", **kwargs):
        return RunLedger(self.root, run_id, clock=self.clock, **kwargs)

    def test_first_request_is_primary_and_capped_at_128(self):
        reservation = self.ledger().reserve_request()
        self.assertIsInstance(reservation, Reservation)
        self.assertEqual(reservation.phase, CallPhase.PRIMARY)
        self.assertEqual(reservation.max_output_tokens, 128)

    def test_caller_cap_can_only_lower_never_raise(self):
        ledger = self.ledger()
        self.assertEqual(ledger.reserve_request(32).max_output_tokens, 32)
        ledger.complete(Reservation(0, CallPhase.PRIMARY, 32), 5, 7)
        self.assertEqual(ledger.reserve_request(10_000).max_output_tokens, 128)

    def test_at_most_two_requests_and_one_retry(self):
        ledger = self.ledger()
        first = ledger.reserve_request()
        self.assertTrue(ledger.fail(first))
        second = ledger.reserve_request()
        self.assertEqual(second.phase, CallPhase.RETRY)
        self.assertTrue(ledger.complete(second, 10, 4))
        for _ in range(5):
            denial = ledger.reserve_request()
            self.assertIsInstance(denial, Denial)
            self.assertEqual(denial.code, BudgetCode.CALL_LIMIT_REACHED)
        snapshot = ledger.snapshot()
        self.assertEqual((snapshot["requests"], snapshot["retries"]), (2, 1))

    def test_in_flight_request_blocks_a_concurrent_second_one(self):
        ledger = self.ledger()
        self.assertIsInstance(ledger.reserve_request(), Reservation)
        self.assertEqual(ledger.reserve_request().code, BudgetCode.USAGE_UNKNOWN)

    def test_unknown_transport_is_never_replayed(self):
        ledger = self.ledger()
        self.assertTrue(ledger.unknown(ledger.reserve_request()))
        self.assertEqual(ledger.reserve_request().code, BudgetCode.USAGE_UNKNOWN)
        self.assertEqual(ledger.snapshot()["requests"], 1)

    def test_restart_with_unfinished_reservation_does_not_reset_admission(self):
        self.ledger().reserve_request()                  # process "crashes" while in flight
        restarted = self.ledger()                        # same run, brand-new object
        self.assertEqual(restarted.reserve_request().code, BudgetCode.USAGE_UNKNOWN)
        self.assertEqual(restarted.snapshot()["states"], ["RESERVED"])

    def test_restart_and_new_request_ids_keep_the_same_counts(self):
        first = self.ledger()
        self.assertTrue(first.complete(first.reserve_request(), 3, 3))
        second = self.ledger().reserve_request()         # new object, new "request id"
        self.assertEqual(second.phase, CallPhase.RETRY)
        self.assertEqual(self.ledger().snapshot()["requests"], 2)

    def test_runs_are_isolated_from_each_other(self):
        self.ledger("run-a").reserve_request()
        self.assertIsInstance(self.ledger("run-b").reserve_request(), Reservation)

    def test_usage_above_reserved_cap_is_recorded_and_stops_admission(self):
        ledger = self.ledger()
        with self.assertRaisesRegex(OutputCapExceeded, "^app-server output cap exceeded$"):
            ledger.complete(ledger.reserve_request(), 10, 200)
        self.assertEqual(ledger.snapshot()['states'], ['CAP_EXCEEDED'])
        self.assertEqual(ledger.snapshot()['usage'], [{'input_tokens': 10, 'output_tokens': 200}])
        self.assertEqual(self.ledger().reserve_request().code, BudgetCode.TOKEN_BUDGET_EXCEEDED)

    def test_exhausted_output_budget_is_denied(self):
        ledger = self.ledger(budget=replace(default_budget(), max_output_tokens=32))
        self.assertTrue(ledger.complete(ledger.reserve_request(), 10, 32))
        self.assertEqual(ledger.reserve_request().code, BudgetCode.TOKEN_BUDGET_EXCEEDED)

    def test_overrun_of_a_lower_requested_cap_also_blocks(self):
        ledger = self.ledger()
        with self.assertRaises(OutputCapExceeded):
            ledger.complete(ledger.reserve_request(4), 1, 5)
        self.assertEqual(ledger.reserve_request().code, BudgetCode.TOKEN_BUDGET_EXCEEDED)

    def test_malformed_persisted_entry_fields_are_sanitized_and_never_reset(self):
        self.ledger().reserve_request()
        path = self.root / 'run-1.ledger.json'
        original = json.loads(path.read_text())
        cases = [None, {}, {**original['entries'][0], 'phase': 'PRIVATE_PHASE'},
                 {**original['entries'][0], 'state': 'PRIVATE_STATE'},
                 {**original['entries'][0], 'sequence': -1},
                 {**original['entries'][0], 'max_output_tokens': True},
                 {**original['entries'][0], 'input_tokens': 1}]
        for entry in cases:
            state = dict(original, entries=[entry])
            data = json.dumps(state)
            path.write_text(data)
            with self.subTest(entry=entry):
                with self.assertRaisesRegex(LLMToolError, '^app-server run ledger unreadable$'):
                    self.ledger().reserve_request()
                self.assertEqual(path.read_text(), data)
        for key, value in (('schema', True), ('started', float('nan')), ('last', -1),
                           ('denied', -1), ('launches', 9), ('entries', [original['entries'][0]] * 3)):
            path.write_text(json.dumps(dict(original, **{key: value})))
            with self.subTest(key=key), self.assertRaises(LLMToolError):
                self.ledger().snapshot()

    def test_atomic_ledger_replace_fsyncs_file_and_parent_directory(self):
        modes = []
        real_fsync = os.fsync
        def observe(fd):
            modes.append(os.fstat(fd).st_mode)
            return real_fsync(fd)
        with patch('llmtool.codex_app_server_budget.os.fsync', side_effect=observe):
            self.ledger().reserve_request()
        self.assertTrue(any(stat.S_ISREG(mode) for mode in modes))
        self.assertTrue(any(stat.S_ISDIR(mode) for mode in modes))

    def test_finished_entries_are_never_rewritten(self):
        ledger = self.ledger()
        reservation = ledger.reserve_request()
        self.assertTrue(ledger.unknown(reservation))
        self.assertFalse(ledger.complete(reservation, 1, 1))
        self.assertFalse(ledger.fail(reservation))
        self.assertEqual(ledger.snapshot()["states"], ["UNKNOWN"])

    def test_invalid_usage_becomes_unknown_not_zero(self):
        ledger = self.ledger()
        self.assertTrue(ledger.complete(ledger.reserve_request(), None, 5))
        self.assertEqual(ledger.snapshot()["states"], ["UNKNOWN"])
        self.assertEqual(ledger.reserve_request().code, BudgetCode.USAGE_UNKNOWN)

    def test_wall_clock_is_enforced_across_restarts(self):
        ledger = self.ledger()
        self.assertTrue(ledger.reserve_launch())
        self.clock.now += MAX_WALL_CLOCK_SECONDS - 1
        self.assertGreater(self.ledger().remaining_seconds(), 0)
        self.assertIsInstance(self.ledger().reserve_request(), Reservation)
        self.clock.now += 1
        self.assertEqual(self.ledger().remaining_seconds(), 0)
        self.assertFalse(self.ledger().reserve_launch())

    def test_wall_clock_denies_requests_after_the_limit(self):
        ledger = self.ledger()
        self.assertTrue(ledger.complete(ledger.reserve_request(), 1, 1))
        self.clock.now += MAX_WALL_CLOCK_SECONDS
        self.assertEqual(ledger.reserve_request().code, BudgetCode.WALL_CLOCK_EXCEEDED)

    def test_a_backwards_clock_never_grants_time_back(self):
        ledger = self.ledger()
        ledger.reserve_launch()
        self.clock.now += 100
        ledger.reserve_launch()
        self.clock.now -= 5000
        self.assertEqual(self.ledger().remaining_seconds(), MAX_WALL_CLOCK_SECONDS - 100)
        self.assertTrue(self.ledger().reserve_launch())
        self.assertEqual(self.ledger().remaining_seconds(), MAX_WALL_CLOCK_SECONDS - 100)

    def test_at_most_eight_launches_even_across_restarts(self):
        for index in range(MAX_LAUNCHES):
            self.assertTrue(self.ledger().reserve_launch(), index)
        self.assertFalse(self.ledger().reserve_launch())
        self.assertEqual(self.ledger().snapshot()["launches"], MAX_LAUNCHES)

    def test_launch_cap_cannot_be_raised_by_the_caller(self):
        with self.assertRaises(LLMToolError):
            self.ledger(max_launches=MAX_LAUNCHES + 1)

    def test_custom_budget_cannot_raise_any_hard_limit(self):
        limits = default_budget()
        for field in ("max_provider_calls", "max_repair_attempts", "wall_clock_ms",
                      "max_input_tokens", "max_output_tokens"):
            with self.subTest(field=field), self.assertRaisesRegex(
                    LLMToolError, "app-server run budget invalid"):
                self.ledger(budget=replace(limits, **{field: getattr(limits, field) + 1}))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_tighter_custom_budget_limits_requests_launches_and_runtime(self):
        limits = replace(default_budget(), max_provider_calls=1, wall_clock_ms=1000,
                         max_output_tokens=64)
        ledger = self.ledger(budget=limits)
        self.assertTrue(ledger.reserve_launch())
        self.assertEqual(ledger.remaining_seconds(), 1.0)
        reservation = ledger.reserve_request()
        self.assertEqual(reservation.max_output_tokens, 64)
        ledger.complete(reservation, 1, 1)
        self.assertEqual(ledger.reserve_request().code, BudgetCode.CALL_LIMIT_REACHED)
        self.clock.now += 1
        self.assertEqual(ledger.remaining_seconds(), 0)
        self.assertFalse(ledger.reserve_launch())

    def test_corrupt_ledger_denies_instead_of_resetting(self):
        ledger = self.ledger()
        ledger.reserve_request()
        path = self.root / "run-1.ledger.json"
        path.write_bytes(b"{not json")
        with self.assertRaisesRegex(LLMToolError, "unreadable"):
            ledger.reserve_request()
        with self.assertRaises(LLMToolError):
            ledger.reserve_launch()
        self.assertEqual(path.read_bytes(), b"{not json")

    def test_wrong_schema_is_rejected(self):
        ledger = self.ledger()
        ledger.reserve_request()
        path = self.root / "run-1.ledger.json"
        state = json.loads(path.read_text())
        state["schema"] = 2
        path.write_text(json.dumps(state))
        with self.assertRaises(LLMToolError):
            ledger.snapshot()

    def test_run_id_is_restricted(self):
        for bad in ("", "../x", "a/b", "a b", "x" * 65, None, 5):
            with self.subTest(run_id=bad), self.assertRaises(LLMToolError):
                RunLedger(self.root, bad)

    def test_ledger_files_are_owner_only(self):
        self.ledger().reserve_request()
        for name in ("run-1.ledger.json", "run-1.ledger.lock"):
            self.assertEqual((self.root / name).stat().st_mode & 0o777, 0o600, name)

    def test_concurrent_reservations_admit_exactly_one(self):
        results = []
        barrier = threading.Barrier(8)

        def attempt():
            ledger = RunLedger(self.root, "race", clock=self.clock)
            barrier.wait()
            results.append(ledger.reserve_request())

        threads = [threading.Thread(target=attempt) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(len(results), 8)
        self.assertEqual(sum(isinstance(r, Reservation) for r in results), 1)
        self.assertEqual(RunLedger(self.root, "race", clock=self.clock).snapshot()["requests"], 1)

    def test_denials_are_counted(self):
        ledger = self.ledger()
        ledger.reserve_request()
        ledger.reserve_request()
        self.assertEqual(ledger.snapshot()["denied"], 1)


if __name__ == "__main__":
    unittest.main()
