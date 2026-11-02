#!/usr/bin/env python3
"""Offline, no-provider tests of six-Low final disposition schema and privacy."""
import unittest
import json
import hashlib
from pathlib import Path
import tempfile
from unittest.mock import patch
import ai_loop_qa_low_disposition as review_module
from pathlib import Path
import tempfile
from unittest.mock import patch
from ai_loop_qa_low_disposition import (
    ACTIONS, PR_HEAD, QA_RUN, Stop, validate_dispositions, verify_bootstrap_blob,
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


class BootstrapBlobAuthenticationTests(unittest.TestCase):
    """No repo checkout or model/provider calls: mock only read-only Git metadata."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "ai_loop_qa_low_disposition.py"
        self.source.write_bytes(b"#!/usr/bin/env python3\nprint('safe')\n")
        self.revision = "a" * 40
        self.blob = "b" * 40

    def test_matching_exact_blob_is_accepted(self):
        with patch("ai_loop_qa_low_disposition.git", side_effect=[self.blob, self.blob]) as git_mock:
            verify_bootstrap_blob(self.source, self.revision)
        self.assertEqual(git_mock.call_count, 2)
        self.assertEqual(git_mock.call_args_list[0].args,
                         ("rev-parse", self.revision + ":scripts/ai_loop_qa_low_disposition.py"))
        self.assertEqual(git_mock.call_args_list[1].args,
                         ("hash-object", "--", str(self.source)))
        self.assertNotIn("-w", git_mock.call_args_list[1].args)

    def test_mismatched_blob_stops_before_model(self):
        with patch("ai_loop_qa_low_disposition.git",
                   side_effect=[self.blob, "c" * 40]):
            with self.assertRaisesRegex(Stop, "Git blob does not match"):
                verify_bootstrap_blob(self.source, self.revision)

    def test_invalid_git_blob_stops(self):
        with patch("ai_loop_qa_low_disposition.git",
                   side_effect=["not-a-sha", self.blob]):
            with self.assertRaises(Stop):
                verify_bootstrap_blob(self.source, self.revision)

    def test_invalid_revision_stops_without_git_call(self):
        with patch("ai_loop_qa_low_disposition.git") as git_mock:
            with self.assertRaisesRegex(Stop, "SHA-addressed"):
                verify_bootstrap_blob(self.source, "main")
            git_mock.assert_not_called()

    def test_symlink_stops_without_git_call(self):
        link = Path(self.temp.name) / "symlink.py"
        link.symlink_to(self.source)
        with patch("ai_loop_qa_low_disposition.git") as git_mock:
            with self.assertRaisesRegex(Stop, "unsafe local bootstrap"):
                verify_bootstrap_blob(link, self.revision)
            git_mock.assert_not_called()

    def test_missing_source_stops(self):
        self.source.unlink()
        with patch("ai_loop_qa_low_disposition.git") as git_mock:
            with self.assertRaises(Stop):
                verify_bootstrap_blob(self.source, self.revision)
            git_mock.assert_not_called()


class FrozenReviewCacheTests(unittest.TestCase):
    def test_old_failed_cache_does_not_poison_new_controller_sha(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            prior = state / "code-review.raw.json"
            prior.write_text('{"status":"FAIL","findings":["PRIVATE"]}')
            revision = "a" * 40
            filename = state / revision / "ai_loop_qa_low_disposition.py"
            filenames = (
                "scripts/ai_loop_qa_low_inventory.py",
                "scripts/ai_loop_qa_low_disposition.py",
                "scripts/test_ai_loop_qa_low_inventory.py",
                "scripts/test_ai_loop_qa_low_disposition.py",
            )
            body = "\n\n".join(x + "\n" + "synthetic source" for x in filenames)
            digest = hashlib.sha256(body.encode()).hexdigest()
            envelope = json.dumps({
                "subtype": "success", "is_error": False,
                "structured_output": {
                    "reviewed_source_sha256": digest,
                    "status": "PASS", "findings": [],
                },
            })
            with (patch.object(review_module, "__file__", str(filename)),
                  patch.object(review_module, "verify_bootstrap_blob"),
                  patch.object(review_module, "git", return_value="synthetic source"),
                  patch.object(review_module, "command", return_value=envelope) as model):
                review_module.code_review(state)
            model.assert_called_once()
            self.assertEqual(prior.read_text(),
                             '{"status":"FAIL","findings":["PRIVATE"]}')
            self.assertTrue((state / ("code-review-" + revision + ".json")).is_file())
            self.assertTrue((state / ("code-review-" + revision + ".raw.json")).is_file())


if __name__ == "__main__":
    unittest.main()
