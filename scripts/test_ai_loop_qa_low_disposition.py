#!/usr/bin/env python3
"""Offline, no-provider tests of six-Low final disposition schema and privacy."""
import unittest
from ai_loop_qa_low_disposition import (
    ACTIONS, PR_HEAD, QA_RUN, Stop, validate_dispositions,
)


def inventory():
    return {"findings": [
        {"finding_sha256": ("%064x" % (n + 1)),
         "role": "security" if n < 2 else "functional_qa",
         "file": "tools/llmtool/llmtool/test_output_excerpts.py"}
        for n in range(6)
    ]}


def result():
    return {"reviewed_head": PR_HEAD, "reviewed_qa_run": QA_RUN,
            "dispositions": [
                {"finding_sha256": item["finding_sha256"], "role": item["role"],
                 "action": "ACCEPT_WITH_RATIONALE", "confidence": "HIGH",
                 "private_reason": "Verified limited functional impact on source",
                 "followup_condition": ""}
                for item in inventory()["findings"]
            ]}


class DispositionTests(unittest.TestCase):
    def test_valid_exact_six(self):
        self.assertEqual(len(validate_dispositions(result(), inventory())), 6)

    def test_missing_identity(self):
        value = result()
        value["dispositions"][0]["finding_sha256"] = "f" * 64
        with self.assertRaises(Stop):
            validate_dispositions(value, inventory())

    def test_duplicate_identity(self):
        value = result()
        value["dispositions"][1] = value["dispositions"][0].copy()
        with self.assertRaises(Stop):
            validate_dispositions(value, inventory())

    def test_stale_head(self):
        value = result()
        value["reviewed_head"] = "f" * 40
        with self.assertRaises(Stop):
            validate_dispositions(value, inventory())

    def test_missing_reason(self):
        value = result()
        value["dispositions"][1]["private_reason"] = ""
        with self.assertRaises(Stop):
            validate_dispositions(value, inventory())

    def test_defer_requires_condition(self):
        value = result()
        value["dispositions"][1]["action"] = "DEFER_FOLLOW_UP"
        with self.assertRaises(Stop):
            validate_dispositions(value, inventory())
        value["dispositions"][1]["followup_condition"] = "Owner: QA, before company Phase 0"
        self.assertEqual(len(validate_dispositions(value, inventory())), 6)

    def test_fix_before_merge_retained(self):
        value = result()
        value["dispositions"][1]["action"] = "FIX_BEFORE_MERGE"
        self.assertEqual(validate_dispositions(value, inventory())[1]["action"],
                         "FIX_BEFORE_MERGE")

    def test_unknown_action_rejected(self):
        value = result()
        value["dispositions"][1]["action"] = "MERGE_NOW"
        with self.assertRaises(Stop):
            validate_dispositions(value, inventory())

    def test_invalid_role_rejected(self):
        value = result()
        value["dispositions"][1]["role"] = "coordinator"
        with self.assertRaises(Stop):
            validate_dispositions(value, inventory())

    def test_extra_source_field_rejected(self):
        value = result()
        value["dispositions"][1]["provider_output"] = "SECRET"
        with self.assertRaises(Stop):
            validate_dispositions(value, inventory())


if __name__ == "__main__":
    unittest.main()
