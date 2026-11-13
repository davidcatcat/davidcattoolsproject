import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import llmtool_monitor as mon

LEGACY_WORKER_ID = "company-gw-agent"   # #1502 WP-E: the retired gw Agent id, fixture data only


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

    def test_finished_record_with_the_retired_gw_agent_id_with_usage(self):
        # #1502 WP-E: a record written with the retired id still reads.
        task_id = "agent00123456789"
        dev = {
            "provider_profile": LEGACY_WORKER_ID,
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

    def test_current_gw_agent_record_keeps_its_own_rows(self):
        # A current gw-agent run is shown by gw_rows; this view adds no row,
        # as before the rename (#1502 WP-E keeps every existing behaviour).
        dev = {"provider_profile": "gw-agent", "state": "DEVELOPER_COMPLETED",
               "model": "gw-test-model"}
        _write_record(self.root, "current123456789", dev)
        self.assertEqual(mon.gw_task_rows(self.now, self.root), [])

    def test_monitor_reads_through_the_gate_alias_and_keeps_no_table(self):
        # Codex v1 F1 on PR #1780: the monitor uses the gate's one shared alias
        # (worker_dispatch in this checkout), never a second table of its own.
        gate = mon._checkout_llmtool_module("worker_dispatch", "stored_worker_id")
        self.assertIsNotNone(gate)
        root = os.path.join(os.path.dirname(SCRIPTS), "tools", "llmtool")
        self.assertTrue(os.path.realpath(gate.__file__).startswith(os.path.realpath(root) + os.sep))
        self.assertIs(mon._retired_worker_aliases(), gate.RETIRED_WORKER_ALIASES)
        self.assertFalse(hasattr(mon, "RETIRED_WORKER_IDS"))
        self.assertFalse(hasattr(mon, "RETIRED_WORKER_ALIASES"))
        # A change to the gate's table is what the monitor sees.
        with patch.dict(gate.RETIRED_WORKER_ALIASES, {"old-route": "agy"}):
            self.assertEqual(mon.stored_worker_id("old-route"), "agy")
            self.assertIn("old-route", mon._retired_worker_aliases())
        self.assertEqual(mon.stored_worker_id("old-route"), "old-route")

    def test_without_the_gate_module_ids_show_as_stored_and_no_legacy_row(self):
        _write_record(self.root, "agent00123456789",
                      {"provider_profile": LEGACY_WORKER_ID, "state": "DEVELOPER_COMPLETED"})
        with patch.object(mon, "_checkout_llmtool_module", return_value=None):
            self.assertEqual(mon._retired_worker_aliases(), {})
            self.assertEqual(mon.stored_worker_id(LEGACY_WORKER_ID), LEGACY_WORKER_ID)
            self.assertEqual(mon.gw_task_rows(self.now, self.root), [])

    def test_non_string_profile_is_skipped(self):
        for index, profile in enumerate(([LEGACY_WORKER_ID], {"a": 1}, 7, None)):
            _write_record(self.root, f"badprofile{index:06d}",
                          {"provider_profile": profile, "state": "DEVELOPER_COMPLETED"})
        self.assertEqual(mon.gw_task_rows(self.now, self.root), [])

    def test_retired_gw_agent_id_reads_as_gw_agent_in_route_views(self):
        # #1502 WP-E: the retired id reads as gw-agent through the gate's alias.
        self.assertEqual(mon.stored_worker_id(LEGACY_WORKER_ID), "gw-agent")
        for value in ("gw-agent", "company-gw", "agy", None, 7):
            self.assertEqual(mon.stored_worker_id(value), value)
        _write_record(self.root, "a1" * 16,
                      {"provider_profile": LEGACY_WORKER_ID, "state": "DEVELOPER_RESPONDED"})
        _write_record(self.root, "b2" * 16, None,
                      pm_assignment={"route": LEGACY_WORKER_ID}, state="PM_RESPONDED")
        outcomes = mon.llmtool_route_outcomes(self.now, self.root)
        self.assertEqual(set(outcomes), {"gw-agent"})
        self.assertEqual(outcomes["gw-agent"][1:3], (1, 1))
        lines = [line for _, line in mon.llmtool_task_records(self.now, self.root)]
        self.assertEqual(len(lines), 2)
        self.assertTrue(any("派給 gw-agent" in line for line in lines), lines)
        self.assertTrue(any("PM 建議 gw-agent" in line for line in lines), lines)
        self.assertFalse(any(LEGACY_WORKER_ID in line for line in lines), lines)

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
