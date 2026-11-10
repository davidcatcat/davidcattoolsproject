#!/usr/bin/env python3
"""Tests for office_state serialization in llmtool_monitor."""

import importlib.util
import json
import os
import re
import sys
import unittest
import unittest.mock

# Dynamically import llmtool_monitor
MONITOR_PATH = os.path.join(os.path.dirname(__file__), "llmtool_monitor.py")
if "llmtool_monitor" in sys.modules:
    mon = sys.modules["llmtool_monitor"]
else:
    spec = importlib.util.spec_from_file_location("llmtool_monitor", MONITOR_PATH)
    mon = importlib.util.module_from_spec(spec)
    sys.modules["llmtool_monitor"] = mon
    spec.loader.exec_module(mon)


class TestLlmtoolMonitorState(unittest.TestCase):
    """Test suite for office_state dictionary construction and sanitization."""

    def test_exact_dict_shape_and_key_order(self):
        """Check exact dict shape, key order, exclusions, and json serialization."""
        top_input = [
            ("Normal status line", "title"),
            ("Please brew install something", "!"),
            ("$ pip install dependency", "!"),
            ("$ run something", "dim"),
            ("Valid alert", "!"),
        ]
        owner_input = ["Waiting on response"]
        fake_pr = mon.PrRow(
            number=42,
            head="feature-branch",
            review="PASS",
            ec2="PASS",
            linux="PASS",
            mac="PASS",
            docs="NOT_RUN",
            next="ready to merge",
            owner="reviewer-account",
            current="WORKING",
        )
        stuck_input = ["Worker task 123 is idle"]
        generated_at_input = "2026-11-10T12:00:00Z"
        data_age_input = None

        result = mon.office_state(
            top=top_input,
            owner=owner_input,
            prs=[fake_pr],
            stuck=stuck_input,
            generated_at=generated_at_input,
            data_age=data_age_input,
        )

        # Exact top-level key order
        expected_top_keys = [
            "schema_version",
            "generated_at",
            "data_age",
            "top",
            "owner",
            "prs",
            "stuck",
            "office",
        ]
        self.assertEqual(list(result.keys()), expected_top_keys)

        # Schema version and static office field
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["office"], [])

        # data_age None is kept as null / None
        self.assertIsNone(result["data_age"])

        # Generated at timestamp
        self.assertEqual(result["generated_at"], generated_at_input)

        # Dropped install and command lines from top
        self.assertEqual(len(result["top"]), 2)
        self.assertEqual(list(result["top"][0].keys()), ["text", "style"])
        self.assertEqual(result["top"][0], {"text": "Normal status line", "style": "title"})
        self.assertEqual(result["top"][1], {"text": "Valid alert", "style": "!"})

        # Owner lines
        self.assertEqual(result["owner"], ["Waiting on response"])

        # Stuck lines
        self.assertEqual(result["stuck"], ["Worker task 123 is idle"])

        # PR rows: exact keys, key order, and ensure head and docs never appear
        self.assertEqual(len(result["prs"]), 1)
        pr_entry = result["prs"][0]
        expected_pr_keys = [
            "number",
            "review",
            "ec2",
            "linux",
            "mac",
            "next",
            "owner",
            "current",
        ]
        self.assertEqual(list(pr_entry.keys()), expected_pr_keys)
        self.assertEqual(pr_entry["number"], 42)
        self.assertEqual(pr_entry["review"], "PASS")
        self.assertEqual(pr_entry["ec2"], "PASS")
        self.assertEqual(pr_entry["linux"], "PASS")
        self.assertEqual(pr_entry["mac"], "PASS")
        self.assertEqual(pr_entry["next"], "ready to merge")
        self.assertEqual(pr_entry["owner"], "reviewer-account")
        self.assertEqual(pr_entry["current"], "WORKING")

        self.assertNotIn("head", pr_entry)
        self.assertNotIn("docs", pr_entry)

        # Ensure json.dumps works and data_age becomes null
        serialized = json.dumps(result)
        self.assertIsInstance(serialized, str)
        reloaded = json.loads(serialized)
        self.assertIsNone(reloaded["data_age"])
        self.assertNotIn("head", reloaded["prs"][0])
        self.assertNotIn("docs", reloaded["prs"][0])

    def test_data_age_float_preserved(self):
        """Ensure float data_age is preserved properly."""
        result = mon.office_state(
            top=[],
            owner=[],
            prs=[],
            stuck=[],
            generated_at="2026-11-10T12:00:00Z",
            data_age=12.5,
        )
        self.assertEqual(result["data_age"], 12.5)
        serialized = json.dumps(result)
        reloaded = json.loads(serialized)
        self.assertEqual(reloaded["data_age"], 12.5)

    def test_redaction_with_planted_marker(self):
        """Check redaction when patching SECRETISH with PLANTED-MARKER."""
        fake_pr = mon.PrRow(
            number=7,
            head="safe-head",
            review="PASS",
            ec2="PASS",
            linux="PASS",
            mac="PASS",
            docs="PASS",
            next="PLANTED-MARKER-42",
            owner="safe-owner",
            current="WORKING",
        )
        fake_stuck = ["PLANTED-MARKER-42"]

        with unittest.mock.patch.object(mon, "SECRETISH", re.compile(r"PLANTED-MARKER-[0-9]+")):
            result = mon.office_state(
                top=[],
                owner=[],
                prs=[fake_pr],
                stuck=fake_stuck,
                generated_at="2026-11-10T12:00:00Z",
                data_age=None,
            )

        self.assertEqual(result["prs"][0]["next"], "[已遮蔽]")
        self.assertEqual(result["stuck"][0], "[已遮蔽]")

        # Also test with planted marker inside sentence
        fake_pr2 = mon.PrRow(
            number=8,
            head="safe-head",
            review="PASS",
            ec2="PASS",
            linux="PASS",
            mac="PASS",
            docs="PASS",
            next="waiting for PLANTED-MARKER-42 now",
            owner="safe-owner",
            current="WORKING",
        )
        fake_stuck2 = ["alert: issue PLANTED-MARKER-42 reported"]

        with unittest.mock.patch.object(mon, "SECRETISH", re.compile(r"PLANTED-MARKER-[0-9]+")):
            result2 = mon.office_state(
                top=[],
                owner=[],
                prs=[fake_pr2],
                stuck=fake_stuck2,
                generated_at="2026-11-10T12:00:00Z",
                data_age=None,
            )

        self.assertEqual(result2["prs"][0]["next"], "waiting for [已遮蔽] now")
        self.assertEqual(result2["stuck"][0], "alert: issue [已遮蔽] reported")

    def test_office_view_entries_none_and_early_returns(self):
        """Check entries=None output equals call without entries, and early returns."""
        parent = mon.OfficeRow(
            name="agent-parent",
            state=mon.WORKING,
            detail="task work",
            age=10.0,
            order=10,
            role="pm",
        )
        fake_rows = [parent]

        out_default = mon.office_view(fake_rows)
        out_none = mon.office_view(fake_rows, entries=None)
        self.assertEqual(out_none, out_default)

        entries_list = []
        out_with_entries = mon.office_view(fake_rows, entries=entries_list)
        self.assertEqual(out_with_entries, out_default)
        self.assertEqual(len(entries_list), 1)

        # Early return on empty rows appends nothing
        empty_entries = []
        mon.office_view([], entries=empty_entries)
        self.assertEqual(empty_entries, [])

        # Early return on error appends nothing
        error_entries = []
        mon.office_view(fake_rows, error="failed read", entries=error_entries)
        self.assertEqual(error_entries, [])

    def test_office_view_tree_order_depth_and_sections(self):
        """Check drawing order, depth, and section transitions after STATUS_SECTION."""
        parent = mon.OfficeRow(
            name="parent-agent",
            state=mon.WORKING,
            detail="managing",
            age=15.0,
            order=10,
            key="agent-pm-1",
            role="pm",
        )
        child = mon.OfficeRow(
            name="child-subagent",
            state=mon.IDLE,
            detail="waiting for instructions",
            age=5.0,
            order=10,
            key="sub-1",
            group="agent-pm-1",
            role="",
        )
        worker = mon.OfficeRow(
            name="opencode developer",
            state=mon.IDLE,
            detail="dispatch switch ready",
            age=25.0,
            order=50,
            kind="worker",
            authority="Owner",
        )

        entries = []
        mon.office_view([parent, child, worker], entries=entries, expand=True)

        expected_keys = [
            "section",
            "depth",
            "name",
            "state",
            "detail",
            "age",
            "band",
            "room",
        ]
        self.assertEqual(len(entries), 3)

        # First entry: parent in live section at depth 0
        self.assertEqual(list(entries[0].keys()), expected_keys)
        self.assertEqual(entries[0]["name"], "parent-agent")
        self.assertEqual(entries[0]["depth"], 0)
        self.assertEqual(entries[0]["section"], "live")
        self.assertEqual(entries[0]["room"], "pm")

        # Second entry: child under parent at depth 1 in live section
        self.assertEqual(list(entries[1].keys()), expected_keys)
        self.assertEqual(entries[1]["name"], "child-subagent")
        self.assertEqual(entries[1]["depth"], 1)
        self.assertEqual(entries[1]["section"], "live")
        self.assertEqual(entries[1]["room"], "pm")

        # Third entry: worker row in status section at depth 0
        self.assertEqual(list(entries[2].keys()), expected_keys)
        self.assertEqual(entries[2]["name"], "opencode developer")
        self.assertEqual(entries[2]["depth"], 0)
        self.assertEqual(entries[2]["section"], "status")
        self.assertEqual(entries[2]["room"], "developer")

    def test_office_view_band_words(self):
        """Check line style mapping to band words: working, alert, idle, error."""
        r_working = mon.OfficeRow(
            name="row-work",
            state=mon.WORKING,
            detail="busy",
            age=2.0,
            order=10,
            role="pm",
        )
        r_alert = mon.OfficeRow(
            name="row-alert",
            state=mon.ATTENTION,
            detail="needs attention",
            age=2.0,
            order=10,
            role="pm",
        )
        r_idle = mon.OfficeRow(
            name="row-idle",
            state=mon.IDLE,
            detail="idle waiting",
            age=2.0,
            order=10,
            role="pm",
        )
        r_error = mon.OfficeRow(
            name="row-err",
            state=mon.UNCHECKED,
            detail="unknown state",
            age=2.0,
            order=10,
            role="pm",
        )

        entries = []
        orig_line_style = mon._line_style

        def fake_line_style(row, rows):
            if row.name == "row-err":
                return "?"
            return orig_line_style(row, rows)

        with unittest.mock.patch.object(mon, "_line_style", side_effect=fake_line_style):
            mon.office_view([r_working, r_alert, r_idle, r_error], entries=entries, expand=True)
        bands = [e["band"] for e in entries]
        self.assertEqual(bands, ["working", "alert", "idle", "error"])

    def test_office_view_room_resolution(self):
        """Check room mapping for roles, ci runner, ec2, dispatch switch, and child."""
        parent_pm = mon.OfficeRow(
            name="agent-pm",
            state=mon.WORKING,
            detail="pm task",
            age=10.0,
            order=10,
            key="pm-lead",
            role="pm",
        )
        child_pm = mon.OfficeRow(
            name="subagent-child",
            state=mon.WORKING,
            detail="subagent task",
            age=5.0,
            order=10,
            group="pm-lead",
            role="",
        )
        dev_row = mon.OfficeRow(
            name="agent-dev",
            state=mon.WORKING,
            detail="dev task",
            age=8.0,
            order=11,
            role="developer",
        )
        rev_row = mon.OfficeRow(
            name="agent-rev",
            state=mon.WORKING,
            detail="rev task",
            age=4.0,
            order=12,
            role="reviewer",
        )
        runner_row = mon.OfficeRow(
            name="mac ci runner",
            state=mon.WORKING,
            detail="runner job",
            age=3.0,
            order=80,
            kind="worker",
        )
        ec2_row = mon.OfficeRow(
            name="EC2 遠端測試",
            state=mon.WORKING,
            detail="remote tests",
            age=6.0,
            order=20,
        )
        switch_row = mon.OfficeRow(
            name="worker-switch",
            state=mon.IDLE,
            detail="switch info",
            age=12.0,
            order=50,
            kind="worker",
            authority="Owner",
        )
        other_row = mon.OfficeRow(
            name="other-agent",
            state=mon.WORKING,
            detail="misc",
            age=1.0,
            order=15,
            role="ui",
        )

        all_rows = [
            parent_pm,
            child_pm,
            dev_row,
            rev_row,
            runner_row,
            ec2_row,
            switch_row,
            other_row,
        ]
        entries = []
        mon.office_view(all_rows, entries=entries, expand=True)

        rooms_by_name = {e["name"]: e["room"] for e in entries}
        self.assertEqual(rooms_by_name["agent-pm"], "pm")
        self.assertEqual(rooms_by_name["subagent-child"], "pm")
        self.assertEqual(rooms_by_name["agent-dev"], "developer")
        self.assertEqual(rooms_by_name["agent-rev"], "reviewer")
        self.assertEqual(rooms_by_name["mac ci runner"], "machine")
        self.assertEqual(rooms_by_name["EC2 遠端測試"], "machine")
        self.assertEqual(rooms_by_name["worker-switch"], "developer")
        self.assertEqual(rooms_by_name["other-agent"], "other")

    def test_office_state_with_office_list_and_redaction(self):
        """Check office_state redaction, validation, and mapping of unknown rooms."""
        # When office is None, result['office'] must be empty list
        res_none = mon.office_state(
            top=[],
            owner=[],
            prs=[],
            stuck=[],
            generated_at="2026-11-10T12:00:00Z",
            data_age=None,
            office=None,
        )
        self.assertEqual(res_none["office"], [])

        fake_entry_with_marker = {
            "section": "status",
            "depth": 2,
            "name": "agent-PLANTED-MARKER-10",
            "state": "PLANTED-MARKER-20",
            "detail": "working on PLANTED-MARKER-42 now",
            "age": "PLANTED-MARKER-30 ago",
            "band": "working",
            "room": "pm",
        }
        fake_entry_with_unknowns = {
            "section": "unrecognized_section",
            "depth": -3,
            "name": "plain-agent",
            "state": "WORKING",
            "detail": "safe detail",
            "age": "剛剛",
            "band": "unrecognized_band",
            "room": "unrecognized_room",
        }
        fake_entry_invalid_depth = {
            "section": "live",
            "depth": "invalid_depth_string",
            "name": "depth-agent",
            "state": "IDLE",
            "detail": "detail",
            "age": "剛剛",
            "band": "idle",
            "room": "reviewer",
        }

        with unittest.mock.patch.object(mon, "SECRETISH", re.compile(r"PLANTED-MARKER-[0-9]+")):
            res = mon.office_state(
                top=[],
                owner=[],
                prs=[],
                stuck=[],
                generated_at="2026-11-10T12:00:00Z",
                data_age=None,
                office=[fake_entry_with_marker, fake_entry_with_unknowns, fake_entry_invalid_depth],
            )

        expected_keys = [
            "section",
            "depth",
            "name",
            "state",
            "detail",
            "age",
            "band",
            "room",
        ]
        self.assertEqual(len(res["office"]), 3)

        # Check redacted entry
        first = res["office"][0]
        self.assertEqual(list(first.keys()), expected_keys)
        self.assertEqual(first["section"], "status")
        self.assertEqual(first["depth"], 2)
        self.assertEqual(first["name"], "agent-[已遮蔽]")
        self.assertEqual(first["state"], "[已遮蔽]")
        self.assertEqual(first["detail"], "working on [已遮蔽] now")
        self.assertEqual(first["age"], "[已遮蔽] ago")
        self.assertEqual(first["band"], "working")
        self.assertEqual(first["room"], "pm")

        # Check unknowns mapping: section -> live, depth -> 0, band -> error, room -> other
        second = res["office"][1]
        self.assertEqual(list(second.keys()), expected_keys)
        self.assertEqual(second["section"], "live")
        self.assertEqual(second["depth"], 0)
        self.assertEqual(second["name"], "plain-agent")
        self.assertEqual(second["band"], "error")
        self.assertEqual(second["room"], "other")

        # Check invalid depth string -> 0
        third = res["office"][2]
        self.assertEqual(third["depth"], 0)
        self.assertEqual(third["room"], "reviewer")
        self.assertEqual(third["band"], "idle")
        self.assertEqual(third["section"], "live")


if __name__ == "__main__":
    unittest.main()
