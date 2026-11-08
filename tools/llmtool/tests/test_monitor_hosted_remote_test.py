"""Tests for GitHub-hosted remote test monitor entries and rows."""

import importlib.util
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
MONITOR_PATH = os.path.join(REPO_ROOT, "scripts", "llmtool_monitor.py")

SPEC = importlib.util.spec_from_file_location("llmtool_monitor", MONITOR_PATH)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"Cannot load monitor from {MONITOR_PATH}")
monitor = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = monitor
SPEC.loader.exec_module(monitor)


class TestMonitorHostedRemoteTest(unittest.TestCase):
    """Test _remote_test_entries, remote_test_row, and malformed dispatches."""

    def test_receipt_without_actions_block_gives_unchanged_words(self):
        record = {
            "task_id": "0123456789abcdef0123456789abcdef",
            "remote_test_receipts": [
                {
                    "result": "PASS",
                    "source_sha": "a" * 40,
                    "ran_total": 10,
                    "ended_at": "2026-11-09T01:00:00Z",
                }
            ],
        }
        entries = monitor._remote_test_entries(record)
        self.assertEqual(len(entries), 1)
        when, words = entries[0]
        self.assertGreater(when, 0)
        self.assertEqual(words, "task 01234567 PASS @aaaaaaa，跑了 10 個測試")

    def test_receipt_with_invalid_actions_block_gives_unchanged_words(self):
        invalid_actions = [
            None,
            "not-a-dict",
            [],
            {"run_id": 0},
            {"run_id": -1},
            {"run_id": (1 << 53) + 1},
            {"run_id": True},
            {"run_id": False},
            {"run_id": "12345"},
            {},
        ]
        for actions in invalid_actions:
            with self.subTest(actions=actions):
                record = {
                    "task_id": "0123456789abcdef0123456789abcdef",
                    "remote_test_receipts": [
                        {
                            "result": "FAIL",
                            "source_sha": "b" * 40,
                            "ran_total": 4,
                            "ended_at": "2026-11-09T01:00:00Z",
                            "actions": actions,
                        }
                    ],
                }
                entries = monitor._remote_test_entries(record)
                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0][1], "task 01234567 FAIL @bbbbbbb，跑了 4 個測試")

    def test_receipt_with_valid_actions_run_id_gains_hosted_words(self):
        record = {
            "task_id": "0123456789abcdef0123456789abcdef",
            "remote_test_receipts": [
                {
                    "result": "PASS",
                    "source_sha": "c" * 40,
                    "ran_total": 7,
                    "ended_at": "2026-11-09T01:00:00Z",
                    "actions": {"run_id": 987654321},
                }
            ],
        }
        entries = monitor._remote_test_entries(record)
        self.assertEqual(len(entries), 1)
        when, words = entries[0]
        self.assertGreater(when, 0)
        self.assertEqual(
            words,
            "task 01234567 PASS GitHub-hosted run 987654321 @ccccccc，跑了 7 個測試",
        )

    def test_dispatches_with_dispatched_and_run_found(self):
        record = {
            "task_id": "0123456789abcdef0123456789abcdef",
            "remote_test_dispatches": [
                {
                    "state": "DISPATCHED",
                    "sha": "d" * 40,
                    "dispatched_at": "2026-11-09T02:00:00Z",
                },
                {
                    "state": "RUN_FOUND",
                    "sha": "e" * 40,
                    "dispatched_at": "2026-11-09T02:05:00Z",
                    "run_id": 12345,
                },
            ],
        }
        entries = monitor._remote_test_entries(record)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0][1], "task 01234567 hosted DISPATCHED run 待定 @ddddddd")
        self.assertEqual(entries[1][1], "task 01234567 hosted RUN_FOUND run 12345 @eeeeeee")

    def test_dispatches_with_six_fractional_digits(self):
        record = {
            "task_id": "0123456789abcdef0123456789abcdef",
            "remote_test_dispatches": [
                {
                    "state": "DISPATCHED",
                    "sha": "d" * 40,
                    "dispatched_at": "2026-11-09T02:00:00.123456Z",
                },
            ],
        }
        entries = monitor._remote_test_entries(record)
        self.assertEqual(len(entries), 1)
        self.assertAlmostEqual(entries[0][0] % 1, 0.123456, places=5)
        self.assertEqual(entries[0][1], "task 01234567 hosted DISPATCHED run 待定 @ddddddd")

    def test_malformed_dispatch_entries_give_nothing(self):
        malformed_list = [
            None,
            "not-a-dict",
            12345,
            [],
            {},
            {"state": "UNKNOWN", "sha": "f" * 40, "dispatched_at": "2026-11-09T02:00:00Z"},
            {"state": "PENDING", "sha": "f" * 40, "dispatched_at": "2026-11-09T02:00:00Z"},
            {"state": "DISPATCHED", "sha": "short", "dispatched_at": "2026-11-09T02:00:00Z"},
            {"state": "DISPATCHED", "sha": "G" * 40, "dispatched_at": "2026-11-09T02:00:00Z"},
            {"state": "DISPATCHED", "sha": "f" * 40, "dispatched_at": "not-a-date"},
            {"state": "DISPATCHED", "sha": "f" * 40, "dispatched_at": 12345},
            {"state": "DISPATCHED", "sha": "f" * 40, "dispatched_at": "2026-11-09T02:00:00.1Z"},
            {"state": "DISPATCHED", "sha": "f" * 40, "dispatched_at": "2026-11-09T02:00:00.1234567Z"},
            {"state": "DISPATCHED", "sha": "f" * 40, "dispatched_at": "2026-11-09T02:00:00Z", "run_id": -1},
            {"state": "DISPATCHED", "sha": "f" * 40, "dispatched_at": "2026-11-09T02:00:00Z", "run_id": 0},
            {"state": "DISPATCHED", "sha": "f" * 40, "dispatched_at": "2026-11-09T02:00:00Z", "run_id": (1 << 53) + 1},
            {"state": "DISPATCHED", "sha": "f" * 40, "dispatched_at": "2026-11-09T02:00:00Z", "run_id": "abc"},
            {"state": "DISPATCHED", "sha": "f" * 40, "dispatched_at": "2026-11-09T02:00:00Z", "run_id": True},
        ]
        record = {
            "task_id": "0123456789abcdef0123456789abcdef",
            "remote_test_dispatches": malformed_list,
        }
        entries = monitor._remote_test_entries(record)
        self.assertEqual(entries, ())

    def test_remote_test_row_pending_hosted_dispatch(self):
        now = 1794200000.0
        words = "task 01234567 hosted DISPATCHED run 待定 @aaaaaaa"
        with mock.patch.object(monitor, "_remote_test_runs", return_value=[]), \
             mock.patch.object(monitor, "remote_test_latest", return_value=((now - 30.0, words), "")):
            row = monitor.remote_test_row([], now)
            self.assertEqual(row.name, "EC2 遠端測試")
            self.assertEqual(row.state, monitor.WORKING)
            self.assertEqual(row.detail, f"GitHub-hosted 執行中：{words}")
            self.assertEqual(row.evidence, "llmtool 紀錄")
            self.assertEqual(row.age, 30.0)

    def test_remote_test_row_pending_hosted_run_found(self):
        now = 1794200000.0
        words = "task 01234567 hosted RUN_FOUND run 5555 @aaaaaaa"
        with mock.patch.object(monitor, "_remote_test_runs", return_value=[]), \
             mock.patch.object(monitor, "remote_test_latest", return_value=((now - 45.0, words), "")):
            row = monitor.remote_test_row([], now)
            self.assertEqual(row.name, "EC2 遠端測試")
            self.assertEqual(row.state, monitor.WORKING)
            self.assertEqual(row.detail, f"GitHub-hosted 執行中：{words}")
            self.assertEqual(row.evidence, "llmtool 紀錄")
            self.assertEqual(row.age, 45.0)

    def test_remote_test_row_read_problem_gives_unchecked(self):
        now = 1794200000.0
        words = "task 01234567 hosted DISPATCHED run 待定 @aaaaaaa"
        problem = "task 紀錄超過讀取上限，結果可能不是最新"
        with mock.patch.object(monitor, "_remote_test_runs", return_value=[]), \
             mock.patch.object(monitor, "remote_test_latest", return_value=((now - 10.0, words), problem)):
            row = monitor.remote_test_row([], now)
            self.assertEqual(row.name, "EC2 遠端測試")
            self.assertEqual(row.state, monitor.UNCHECKED)
            self.assertEqual(row.detail, f"已讀到的一次：{words}；{problem}")
            self.assertEqual(row.evidence, "llmtool 紀錄")

    def test_remote_test_row_idle_receipt(self):
        now = 1794200000.0
        words = "task 01234567 PASS GitHub-hosted run 12345 @aaaaaaa，跑了 5 個測試"
        with mock.patch.object(monitor, "_remote_test_runs", return_value=[]), \
             mock.patch.object(monitor, "remote_test_latest", return_value=((now - 60.0, words), "")):
            row = monitor.remote_test_row([], now)
            self.assertEqual(row.name, "EC2 遠端測試")
            self.assertEqual(row.state, monitor.IDLE)
            self.assertEqual(row.detail, f"最近一次：{words}")
            self.assertEqual(row.evidence, "llmtool 紀錄")

    def test_remote_test_row_idle_no_records(self):
        with mock.patch.object(monitor, "_remote_test_runs", return_value=[]), \
             mock.patch.object(monitor, "remote_test_latest", return_value=(None, "")):
            row = monitor.remote_test_row([], 1794200000.0)
            self.assertEqual(row.name, "EC2 遠端測試")
            self.assertEqual(row.state, monitor.IDLE)
            self.assertEqual(row.detail, "尚無遠端測試紀錄")
            self.assertEqual(row.evidence, "llmtool 紀錄")


if __name__ == "__main__":
    unittest.main()
