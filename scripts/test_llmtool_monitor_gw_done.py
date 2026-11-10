import json
import os
import sys
import tempfile
import time
import unittest

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import llmtool_monitor as mon


def _write_record(root, task_id, developer, **extra):
    record = {"task_id": task_id, "developer": developer, **extra}
    path = os.path.join(root, f"{task_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f)
    return path


class TestLlmtoolMonitorGwDone(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.now = time.time()
        mon._llmtool_records.clear()

    def tearDown(self):
        mon._llmtool_records.clear()
        self.tmp.cleanup()

    def test_finished_company_gw_with_usage(self):
        task_id = "c0ffee0123456789"
        dev = {
            "provider_profile": "company-gw",
            "state": "DEVELOPER_COMPLETED",
            "model": "gw-test-model",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        _write_record(self.root, task_id, dev)
        rows = mon.gw_task_rows(self.now, self.root)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.name, f"home LLM {task_id[:8]}")
        self.assertEqual(row.state, mon.DONE)
        self.assertIn("已結束", row.detail)
        self.assertIn("輸入 10 · 輸出 5 · 合計 15 tokens", row.detail)

    def test_finished_company_gw_agent_with_usage(self):
        task_id = "agent00123456789"
        dev = {
            "provider_profile": "company-gw-agent",
            "state": "DEVELOPER_COMPLETED",
            "model": "gw-test-model",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        _write_record(self.root, task_id, dev)
        rows = mon.gw_task_rows(self.now, self.root)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.name, f"home LLM {task_id[:8]}")
        self.assertEqual(row.state, mon.DONE)
        self.assertIn("已結束", row.detail)
        self.assertIn("輸入 10 · 輸出 5 · 合計 15 tokens", row.detail)

    def test_missing_usage(self):
        task_id = "nousage123456789"
        dev = {
            "provider_profile": "company-gw",
            "state": "DEVELOPER_COMPLETED",
            "model": "gw-test-model",
        }
        _write_record(self.root, task_id, dev)
        rows = mon.gw_task_rows(self.now, self.root)
        self.assertEqual(len(rows), 1)
        self.assertIn("用量未記錄", rows[0].detail)

    def test_failed_run_shows_error_code(self):
        task_id = "failed0123456789"
        dev = {
            "provider_profile": "company-gw",
            "state": "DEVELOPER_FAILED",
            "error_code": "GW_TIMEOUT",
            "model": "gw-test-model",
        }
        _write_record(self.root, task_id, dev)
        rows = mon.gw_task_rows(self.now, self.root)
        self.assertEqual(len(rows), 1)
        self.assertIn("GW_TIMEOUT", rows[0].detail)

    def test_non_integer_counts_show_unknown(self):
        task_id = "invalid012345678"
        dev = {
            "provider_profile": "company-gw",
            "state": "DEVELOPER_COMPLETED",
            "model": "gw-test-model",
            "usage": {"prompt_tokens": "10", "completion_tokens": -1, "total_tokens": 15},
        }
        _write_record(self.root, task_id, dev)
        rows = mon.gw_task_rows(self.now, self.root)
        self.assertEqual(len(rows), 1)
        self.assertIn("不明", rows[0].detail)

    def test_request_response_never_in_row(self):
        task_id = "secret0123456789"
        dev = {
            "provider_profile": "company-gw",
            "state": "DEVELOPER_COMPLETED",
            "model": "gw-test-model",
        }
        _write_record(self.root, task_id, dev, request="SECRET-REQUEST", response="SECRET-RESPONSE")
        rows = mon.gw_task_rows(self.now, self.root)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        for field in (row.name, row.state, row.detail, row.kind, row.evidence, row.key, row.group):
            self.assertNotIn("SECRET-REQUEST", str(field))
            self.assertNotIn("SECRET-RESPONSE", str(field))


if __name__ == "__main__":
    unittest.main()
