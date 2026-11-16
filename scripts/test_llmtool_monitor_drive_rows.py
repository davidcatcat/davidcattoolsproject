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

    def test_fully_known_row(self):
        """A fully known Task record produces a row with AI, PR, summary, stage, time, and Task ID prefix."""
        record = {
            "task_id": "74590be9053e4051909da5a4f0ca51ad",
            "developer": {"provider_profile": "claude-developer"},
            "pr": 2201,
            "pm_assignment": {"summary": "Implement user calculator"},
            "stage": "EC2 測試中",
        }
        self._write_record("74590be9053e4051909da5a4f0ca51ad.json", record, mtime=self.now - 50.0)

        p = monitor.Process(101, 1, 101, "01:23", "S", "llmtool drive",
                            drive_task="74590be9053e4051909da5a4f0ca51ad")
        rows = monitor.drive_rows([p], self.now, self.root)
        self.assertEqual(len(rows), 1)

        row = rows[0]
        self.assertEqual(row.name, "llmtool 推進")
        self.assertEqual(row.state, monitor.WORKING)
        self.assertEqual(row.kind, "worker")
        self.assertEqual(row.task, "task 74590be9")
        expected_detail = "Claude · PR #2201 · Implement user calculator · EC2 測試中 · 跑了 01:23 (74590be9)"
        self.assertEqual(row.detail, expected_detail)

    def test_all_developer_ai_families(self):
        """All five Developer AI families map to their exact required names."""
        cases = [
            ({"developer": {"provider_profile": "claude-developer"}}, "Claude"),
            ({"pm_assignment": {"route": "agy"}}, "AGY"),
            ({"developer": {"provider_profile": "gw-agent"}}, "home LLM"),
            ({"developer": {"provider_type": "openai-compatible"}}, "home LLM"),
            ({"developer": {"provider_profile": "opencode-developer"}}, "OpenCode"),
            ({"developer": {"provider_type": "codex-cli"}}, "Codex"),
            ({"developer": {"provider_profile": "unknown-custom"}}, "狀態未知"),
        ]
        for idx, (data, expected_ai) in enumerate(cases):
            task_id = f"1111222233334444555566667777888{idx}"
            full_record = dict(data, task_id=task_id, pr=100 + idx, stage="等審查",
                               pm_assignment=dict(data.get("pm_assignment", {}), summary="Test work"))
            self._write_record(f"{task_id}.json", full_record, mtime=self.now - 10.0)
            p = monitor.Process(200 + idx, 1, 200 + idx, "00:30", "S", "llmtool drive",
                                drive_task=task_id)
            rows = monitor.drive_rows([p], self.now, self.root)
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0].detail.startswith(f"{expected_ai} · PR #{100 + idx} · "))

    def test_all_stages(self):
        """All required stages appear in the expected field."""
        stages = ["EC2 測試中", "等審查", "審查中", "修正中", "狀態未知"]
        for idx, stage in enumerate(stages):
            task_id = f"2222333344445555666677778888999{idx}"
            record = {
                "task_id": task_id,
                "developer": {"provider_profile": "claude-developer"},
                "pr": 300 + idx,
                "pm_assignment": {"summary": "Stage check work"},
                "stage": stage,
            }
            self._write_record(f"{task_id}.json", record, mtime=self.now - 10.0)
            p = monitor.Process(300 + idx, 1, 300 + idx, "00:15", "S", "llmtool drive",
                                drive_task=task_id)
            rows = monitor.drive_rows([p], self.now, self.root)
            self.assertEqual(len(rows), 1)
            expected = f"Claude · PR #{300 + idx} · Stage check work · {stage} · 跑了 00:15 ({task_id[:8]})"
            self.assertEqual(rows[0].detail, expected)

    def test_fallbacks_for_missing_fields_and_long_summary(self):
        """Fallbacks appear when PR, summary, AI, or stage are missing or need cutting."""
        # Case 1: no PR, no summary, no AI, no stage
        task_id1 = "33334444555566667777888899990001"
        self._write_record(f"{task_id1}.json", {"task_id": task_id1}, mtime=self.now - 10.0)
        p1 = monitor.Process(401, 1, 401, "00:20", "S", "llmtool drive", drive_task=task_id1)

        # Case 2: long summary cut to 40 chars
        task_id2 = "44445555666677778888999900001112"
        long_summary = "A quick brown fox jumps over the lazy dog and runs away"
        record2 = {
            "task_id": task_id2,
            "developer": {"provider_profile": "agy"},
            "pr": 555,
            "summary": long_summary,
            "stage": "審查中",
        }
        self._write_record(f"{task_id2}.json", record2, mtime=self.now - 10.0)
        p2 = monitor.Process(402, 1, 402, "00:40", "S", "llmtool drive", drive_task=task_id2)

        rows = monitor.drive_rows([p1, p2], self.now, self.root)
        self.assertEqual(len(rows), 2)

        expected1 = f"狀態未知 · 尚未開 PR · （無摘要） · 狀態未知 · 跑了 00:20 ({task_id1[:8]})"
        self.assertEqual(rows[0].detail, expected1)

        expected_cut = long_summary[:40] + "…"
        expected2 = f"AGY · PR #555 · {expected_cut} · 審查中 · 跑了 00:40 ({task_id2[:8]})"
        self.assertEqual(rows[1].detail, expected2)

    def test_missing_or_unreadable_task_file_gives_row_with_fallbacks(self):
        """A missing or corrupted Task file produces a row with fallbacks and Task ID prefix."""
        p_missing = monitor.Process(103, 1, 103, "02:10", "S", "llmtool drive",
                                    drive_task="9b7253df794e58f3a01537699ecf39f3")
        rows = monitor.drive_rows([p_missing], self.now, self.root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].name, "llmtool 推進")
        self.assertEqual(rows[0].state, monitor.WORKING)
        expected_missing = "狀態未知 · 尚未開 PR · （無摘要） · 狀態未知 · 跑了 02:10 (9b7253df)"
        self.assertEqual(rows[0].detail, expected_missing)

        # Corrupted / invalid JSON file
        corrupt_path = os.path.join(self.root, "a1b2c3d4e5f60718293a4b5c6d7e8f90.json")
        with open(corrupt_path, "w", encoding="utf-8") as f:
            f.write("{invalid json syntax")
        p_corrupt = monitor.Process(104, 1, 104, "03:15", "S", "llmtool drive",
                                    drive_task="a1b2c3d4e5f60718293a4b5c6d7e8f90")
        rows = monitor.drive_rows([p_corrupt], self.now, self.root)
        self.assertEqual(len(rows), 1)
        expected_corrupt = "狀態未知 · 尚未開 PR · （無摘要） · 狀態未知 · 跑了 03:15 (a1b2c3d4)"
        self.assertEqual(rows[0].detail, expected_corrupt)

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
        self.assertIn("Calculator test summary", combined)
        self.assertNotIn("Should pass all tests", combined)
        self.assertIn("74590be9", combined)


if __name__ == "__main__":
    unittest.main()
