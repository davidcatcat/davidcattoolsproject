#!/usr/bin/env python3
"""Tests for llmtool drive and advance rows in llmtool_monitor."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest

# Load the monitor module dynamically like scripts/test_llmtool_monitor_gw_live.py
MONITOR_PATH = os.path.join(os.path.dirname(__file__), "llmtool_monitor.py")
spec = importlib.util.spec_from_file_location("llmtool_monitor", MONITOR_PATH)
monitor = importlib.util.module_from_spec(spec)
sys.modules["llmtool_monitor"] = monitor
spec.loader.exec_module(monitor)


class TestLlmtoolMonitorDriveRows(unittest.TestCase):
    def setUp(self):
        monitor._llmtool_records.clear()
        monitor._row_facts_cache.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.now = 1700000000.0

    def tearDown(self):
        monitor._llmtool_records.clear()
        monitor._row_facts_cache.clear()
        self.tmp.cleanup()

    def _write_record(self, filename: str, data: dict, mtime: float | None = None) -> str:
        path = os.path.join(self.root, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def test_classify_task_drive(self):
        """llmtool task drive with a 32-hex ID gets role 'llmtool drive'."""
        cmd1 = "llmtool task drive 74590be9053e4051909da5a4f0ca51ad"
        self.assertEqual(monitor.classify(cmd1), "llmtool drive")

        cmd2 = "python3 -m llmtool task drive 74590be9053e4051909da5a4f0ca51ad"
        self.assertEqual(monitor.classify(cmd2), "llmtool drive")

        cmd3 = "python3 tools/llmtool/bin/llmtool task drive 74590be9053e4051909da5a4f0ca51ad"
        self.assertEqual(monitor.classify(cmd3), "llmtool drive")

    def test_classify_task_advance(self):
        """llmtool task advance with a 32-hex ID gets role 'llmtool drive'."""
        cmd1 = "llmtool task advance 74590be9053e4051909da5a4f0ca51ad"
        self.assertEqual(monitor.classify(cmd1), "llmtool drive")

        cmd2 = "python3 -m llmtool task advance 74590be9053e4051909da5a4f0ca51ad"
        self.assertEqual(monitor.classify(cmd2), "llmtool drive")

    def test_classify_other_llmtool_commands_give_llmtool_cli(self):
        """Other llmtool commands or non-32-hex targets get 'llmtool CLI'."""
        self.assertEqual(monitor.classify("llmtool task list"), "llmtool CLI")
        self.assertEqual(monitor.classify("llmtool task remote-test"), "llmtool CLI")
        self.assertEqual(monitor.classify("llmtool ci"), "llmtool CLI")
        self.assertEqual(monitor.classify("llmtool task drive not-a-task-id"), "llmtool CLI")
        self.assertEqual(monitor.classify("llmtool task drive 12345"), "llmtool CLI")
        self.assertEqual(monitor.classify("python3 -m llmtool status"), "llmtool CLI")

    def test_parse_processes_keeps_only_task_id(self):
        """ps parsing keeps role 'llmtool drive' and only the 32-hex Task ID."""
        ps_out = "  101     1   101  01:23 S    llmtool --option=sample task drive 74590be9053e4051909da5a4f0ca51ad\n"
        snapshot = monitor.parse_processes(ps_out, self_pid=999)
        self.assertEqual(len(snapshot.processes), 1)
        proc = snapshot.processes[0]
        self.assertEqual(proc.role, "llmtool drive")
        self.assertEqual(proc.drive_task, "74590be9053e4051909da5a4f0ca51ad")
        self.assertNotIn("option", repr(proc))
        self.assertNotIn("sample", repr(proc))

    def test_drive_and_advance_processes_give_working_rows_with_task_prefix(self):
        """Active drive and advance processes give WORKING rows with the Task prefix."""
        record_pr = {
            "task_id": "74590be9053e4051909da5a4f0ca51ad",
            "pr": 2201,
        }
        self._write_record("74590be9053e4051909da5a4f0ca51ad.json", record_pr, mtime=self.now - 50.0)

        record_no_pr = {
            "task_id": "8a6142ce683d47f29f0426588dbf28e2",
        }
        self._write_record("8a6142ce683d47f29f0426588dbf28e2.json", record_no_pr, mtime=self.now - 20.0)

        p1 = monitor.Process(101, 1, 101, "01:23", "S", "llmtool drive",
                             drive_task="74590be9053e4051909da5a4f0ca51ad")
        p2 = monitor.Process(102, 1, 102, "00:45", "S", "llmtool drive",
                             drive_task="8a6142ce683d47f29f0426588dbf28e2")

        rows = monitor.drive_rows([p1, p2], self.now, self.root)
        self.assertEqual(len(rows), 2)

        row1 = rows[0]
        self.assertEqual(row1.name, "llmtool 推進")
        self.assertEqual(row1.state, monitor.WORKING)
        self.assertEqual(row1.kind, "worker")
        self.assertEqual(row1.detail, "task 74590be9 · PR #2201 · 跑了 01:23")

        row2 = rows[1]
        self.assertEqual(row2.name, "llmtool 推進")
        self.assertEqual(row2.state, monitor.WORKING)
        self.assertEqual(row2.kind, "worker")
        self.assertEqual(row2.detail, "task 8a6142ce · 跑了 00:45")

    def test_missing_or_unreadable_task_file_gives_row_with_pr_unconfirmed(self):
        """A missing or corrupted Task file still produces a row with 'PR 未確認'."""
        p_missing = monitor.Process(103, 1, 103, "02:10", "S", "llmtool drive",
                                    drive_task="9b7253df794e58f3a01537699ecf39f3")
        rows = monitor.drive_rows([p_missing], self.now, self.root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].name, "llmtool 推進")
        self.assertEqual(rows[0].state, monitor.WORKING)
        self.assertEqual(rows[0].detail, "task 9b7253df · PR 未確認 · 跑了 02:10")

        # Corrupted / invalid JSON file
        corrupt_path = os.path.join(self.root, "a1b2c3d4e5f60718293a4b5c6d7e8f90.json")
        with open(corrupt_path, "w", encoding="utf-8") as f:
            f.write("{invalid json syntax")
        p_corrupt = monitor.Process(104, 1, 104, "03:15", "S", "llmtool drive",
                                    drive_task="a1b2c3d4e5f60718293a4b5c6d7e8f90")
        rows = monitor.drive_rows([p_corrupt], self.now, self.root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].detail, "task a1b2c3d4 · PR 未確認 · 跑了 03:15")

    def test_other_llmtool_commands_give_no_drive_rows(self):
        """Non-drive llmtool commands produce no drive_rows."""
        proc = monitor.Process(105, 1, 105, "00:10", "S", "llmtool CLI")
        rows = monitor.drive_rows([proc], self.now, self.root)
        self.assertEqual(rows, [])

    def test_argv_text_other_than_task_id_never_in_row(self):
        """Argv text other than the Task ID never appears in any row."""
        custom_flag = "--custom-option=sample_flag_value"
        extra_word = "benchmark_stage_arg"
        ps_out = f"  106     1   106  00:50 S    llmtool {custom_flag} task drive 74590be9053e4051909da5a4f0ca51ad {extra_word}\n"
        snapshot = monitor.parse_processes(ps_out, self_pid=999)
        self.assertEqual(len(snapshot.processes), 1)

        req_text = "Please write unit tests for the calculator function"
        resp_text = "Added tests for addition and subtraction"
        record = {
            "task_id": "74590be9053e4051909da5a4f0ca51ad",
            "pr": 2201,
            "request": req_text,
            "response": resp_text,
            "summary": "Calculator test summary",
            "acceptance": "Should pass all tests",
        }
        self._write_record("74590be9053e4051909da5a4f0ca51ad.json", record, mtime=self.now - 10.0)

        rows = monitor.drive_rows(snapshot.processes, self.now, self.root)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        combined = f"{row.name} {row.detail} {row.evidence} {row.state} {row.kind} {row.task}"
        self.assertNotIn("custom-option", combined)
        self.assertNotIn("sample_flag_value", combined)
        self.assertNotIn(extra_word, combined)
        self.assertNotIn(req_text, combined)
        self.assertNotIn(resp_text, combined)
        self.assertNotIn("Calculator test summary", combined)
        self.assertNotIn("Should pass all tests", combined)
        self.assertIn("74590be9", combined)


if __name__ == "__main__":
    unittest.main()
