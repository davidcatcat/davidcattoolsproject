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


if __name__ == "__main__":
    unittest.main()
