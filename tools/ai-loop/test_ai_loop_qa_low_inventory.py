#!/usr/bin/env python3
"""Synthetic, source-free tests for the frozen QA Low inventory validator."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_loop_qa_low_inventory import (
    ALLOWED, DIFF_SHA, PR_HEAD, QA_RUN, EvidenceError, frozen_inventory,
)


def fixture():
    files = sorted(ALLOWED)
    def row(index):
        return {"severity": "Low", "blocking": False, "file": files[index % len(files)],
                "detail": "synthetic detail %d" % index,
                "evidence": "synthetic evidence %d" % index}
    return {
        "task_id": "C1-PR55-PostJWT-Security-Functional-QA-20261009",
        "run_id": QA_RUN, "pr": 55, "head": PR_HEAD, "diff_sha256": DIFF_SHA,
        "qa_result": "PASS", "tests_related": "PASS", "tests_full_local": "PASS",
        "roles": [
            {"role": "security", "status": "PASS", "findings": [row(0), row(1)]},
            {"role": "functional_qa", "status": "PASS",
             "findings": [row(2), row(3), row(4), row(5)]},
        ],
        "merge_authorized": False, "product_provider_egress_authorized": False,
    }


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / QA_RUN / "qa-pr55.json"
        self.path.parent.mkdir()
        self.record = fixture()

    def write(self):
        self.path.write_text(json.dumps(self.record), encoding="utf-8")

    def test_six_findings_are_metadata_only(self):
        self.write()
        result = frozen_inventory(self.path)
        self.assertEqual(len(result["findings"]), 6)
        self.assertFalse(result["merge_authorized"])
        self.assertNotIn("synthetic detail", json.dumps(result))
        self.assertNotIn("synthetic evidence", json.dumps(result))
        self.assertEqual(len(set(r["finding_sha256"] for r in result["findings"])), 6)

    def test_wrong_head_rejected(self):
        self.record["head"] = "0" * 40
        self.write()
        with self.assertRaises(EvidenceError):
            frozen_inventory(self.path)

    def test_blocker_rejected_even_on_pass(self):
        self.record["roles"][0]["findings"][0]["blocking"] = True
        self.write()
        with self.assertRaises(EvidenceError):
            frozen_inventory(self.path)

    def test_medium_rejected_even_on_pass(self):
        self.record["roles"][0]["findings"][0]["severity"] = "Medium"
        self.write()
        with self.assertRaises(EvidenceError):
            frozen_inventory(self.path)

    def test_missing_finding_rejected(self):
        self.record["roles"][1]["findings"].pop()
        self.write()
        with self.assertRaises(EvidenceError):
            frozen_inventory(self.path)

    def test_duplicate_finding_rejected(self):
        self.record["roles"][1]["findings"][1] = self.record["roles"][0]["findings"][0].copy()
        self.write()
        with self.assertRaises(EvidenceError):
            frozen_inventory(self.path)

    def test_symlink_rejected(self):
        alternate = self.path.parent / "original.json"
        alternate.write_text(json.dumps(self.record), encoding="utf-8")
        self.path.symlink_to(alternate)
        with self.assertRaises(EvidenceError):
            frozen_inventory(self.path)

    def test_oversized_rejected(self):
        self.write()
        with self.path.open("ab") as out:
            out.write(b" " * 300000)
        with self.assertRaises(EvidenceError):
            frozen_inventory(self.path)

    def test_mismatched_run_path_rejected(self):
        self.write()
        alternate = Path(self.dir.name) / "wrong" / "qa-pr55.json"
        alternate.parent.mkdir()
        alternate.write_bytes(self.path.read_bytes())
        with self.assertRaises(EvidenceError):
            frozen_inventory(alternate)

    def test_no_authorization_from_qa_report(self):
        self.record["merge_authorized"] = True
        self.write()
        with self.assertRaises(EvidenceError):
            frozen_inventory(self.path)


if __name__ == "__main__":
    unittest.main()
