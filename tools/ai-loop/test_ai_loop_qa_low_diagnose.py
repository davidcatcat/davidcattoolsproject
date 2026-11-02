#!/usr/bin/env python3
"""Offline QA Low reviewer diagnostic tests; no real private evidence or provider."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_loop_qa_low_diagnose import (
    Stop, frozen_review, validate_classification, CATEGORIES, AREAS, ACTIONS,
)


def findings():
    return [{"sha": "a" * 64, "text": "synthetic review issue"},
            {"sha": "b" * 64, "text": "synthetic second issue"}]


def valid():
    return {"source_sha": "c" * 64, "items": [
        {"sha": x["sha"], "category": "SCHEMA_VALIDATION",
         "area": "CODE_REVIEW", "action": "FIX_CODE", "confidence": "HIGH"}
        for x in findings()
    ]}


class ClassificationTests(unittest.TestCase):
    def test_exact_finding_set(self):
        self.assertEqual(len(validate_classification(valid(), "c" * 64, findings())), 2)

    def test_no_finding_loss(self):
        x = valid()
        x["items"].pop()
        with self.assertRaises(Stop):
            validate_classification(x, "c" * 64, findings())

    def test_no_duplicate_finding(self):
        x = valid()
        x["items"][1]["sha"] = "a" * 64
        with self.assertRaises(Stop):
            validate_classification(x, "c" * 64, findings())

    def test_no_unknown_finding(self):
        x = valid()
        x["items"][1]["sha"] = "f" * 64
        with self.assertRaises(Stop):
            validate_classification(x, "c" * 64, findings())

    def test_no_stale_source(self):
        with self.assertRaises(Stop):
            validate_classification(valid(), "d" * 64, findings())

    def test_no_raw_model_text(self):
        x = valid()
        x["items"][0]["raw_detail"] = "PRIVATE"
        with self.assertRaises(Stop):
            validate_classification(x, "c" * 64, findings())

    def test_no_custom_category(self):
        x = valid()
        x["items"][0]["category"] = "MODEL_SAYS_MERGE"
        with self.assertRaises(Stop):
            validate_classification(x, "c" * 64, findings())


class FrozenReviewerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "code-review.raw.json"
        self.digest = "a" * 64

    def write(self, decision="FAIL", rows=None, digest=None):
        envelope = {"subtype": "success", "is_error": False,
                    "structured_output": {
                        "reviewed_source_sha256": digest or self.digest,
                        "status": decision, "findings": rows if rows is not None
                        else ["synthetic finding one"]}}
        self.path.write_text(json.dumps(envelope), encoding="utf-8")

    def test_prior_fail_evidence_is_bounded_and_hashed(self):
        self.write()
        raw, review, rows = frozen_review(self.path, self.digest)
        self.assertEqual(review["status"], "FAIL")
        self.assertEqual(rows[0]["sha"],
                         hashlib.sha256("synthetic finding one".encode()).hexdigest())
        self.assertNotIn("synthetic finding", rows[0]["sha"])

    def test_original_pass_cannot_be_reclassified_as_failure(self):
        self.write(decision="PASS", rows=[])
        with self.assertRaises(Stop):
            frozen_review(self.path, self.digest)

    def test_wrong_review_source_is_not_accepted(self):
        self.write(digest="b" * 64)
        with self.assertRaises(Stop):
            frozen_review(self.path, self.digest)

    def test_duplicate_findings_rejected(self):
        self.write(rows=["one", "one"])
        with self.assertRaises(Stop):
            frozen_review(self.path, self.digest)

    def test_malformed_envelope_rejected(self):
        self.path.write_text('{"subtype":"error"}', encoding="utf-8")
        with self.assertRaises(Stop):
            frozen_review(self.path, self.digest)


if __name__ == "__main__":
    unittest.main()
