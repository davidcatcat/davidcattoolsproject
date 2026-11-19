"""#2281 limit table row 2: the spec_preflight large-file limit is configurable.

A1: the threshold is spec_preflight_large_file_lines from the trusted
    checkout's policy.json, validated by the policy loader. At its recorded
    value (2000 lines) preflight results are unchanged.
A2: an absent key keeps the #1570 default of 2000; an invalid policy refuses
    fail closed (SPEC_PREFLIGHT_INVALID) instead of falling back silently.
"""

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "spec_preflight.py"
sys.path.insert(0, str(ROOT))

from llmtool import repair_policy  # noqa: E402
from llmtool.errors import LLMToolError  # noqa: E402

SPEC = importlib.util.spec_from_file_location("spec_preflight_policy_2281", SCRIPT)
spec_preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(spec_preflight)

KEY = "spec_preflight_large_file_lines"
TABLE_LINES = 2000
REFUSED = (2, "SPEC_PREFLIGHT_INVALID:EDIT_LINE_HINT_MISSING:-1\n")
OK = (0, "SPEC_PREFLIGHT_OK\n")


class SpecPreflightPolicyTests(unittest.TestCase):
    def setUp(self):
        self.spec = {
            "request": "Add a bounded helper.",
            "summary": "Screen a dispatch spec locally.",
            "goal": "Print field names only.",
            "acceptance": ["Return a clean result."],
        }
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)

    def run_main(self, files, provider="gw-agent"):
        for name, line_count in files.items():
            (self.dir / name).write_text("line\n" * line_count, encoding="utf-8")
        spec = dict(self.spec, provider=provider, write=list(files))
        spec_path = self.dir / "spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = spec_preflight.main(["--repo", str(self.dir), str(spec_path)])
        return status, output.getvalue()

    # A1
    def test_repository_policy_records_row_two_value(self):
        self.assertIn(KEY, repair_policy.OPTIONAL_POSITIVE_POLICY_KEYS)
        self.assertEqual(repair_policy.load_policy()[KEY], TABLE_LINES)

    def test_current_value_keeps_previous_results(self):
        self.assertEqual(self.run_main({"under.txt": TABLE_LINES - 1}), OK)
        self.assertEqual(self.run_main({"at.txt": TABLE_LINES}), REFUSED)

    def test_threshold_comes_from_the_policy_value(self):
        with mock.patch.object(spec_preflight, "load_policy", return_value={KEY: 3}):
            self.assertEqual(self.run_main({"two.txt": 2}), OK)
            self.assertEqual(self.run_main({"three.txt": 3}), REFUSED)
        with mock.patch.object(spec_preflight, "load_policy", return_value={KEY: 5000}):
            self.assertEqual(self.run_main({"long.txt": TABLE_LINES}), OK)

    # A2
    def test_absent_key_keeps_the_previous_default(self):
        with mock.patch.object(spec_preflight, "load_policy", return_value={}):
            self.assertEqual(self.run_main({"under.txt": TABLE_LINES - 1}), OK)
            self.assertEqual(self.run_main({"at.txt": TABLE_LINES}), REFUSED)

    def test_invalid_policy_refuses_fail_closed(self):
        with mock.patch.object(spec_preflight, "load_policy",
                               side_effect=LLMToolError("POLICY_INVALID")):
            self.assertEqual(self.run_main({"small.txt": 1}),
                             (2, "SPEC_PREFLIGHT_INVALID\n"))

    def test_invalid_policy_refuses_even_a_plain_spec(self):
        with mock.patch.object(spec_preflight, "load_policy",
                               side_effect=LLMToolError("POLICY_INVALID")):
            self.assertEqual(self.run_main({"small.txt": 1}, provider="codex"),
                             (2, "SPEC_PREFLIGHT_INVALID\n"))

    def test_invalid_policy_file_refuses_through_the_real_loader(self):
        bad = self.dir / "policy.json"
        bad.write_text('{"schema_version": 1}', encoding="utf-8")
        with mock.patch.object(spec_preflight, "load_policy",
                               side_effect=lambda: repair_policy.load_policy(bad)):
            self.assertEqual(self.run_main({"small.txt": 1}),
                             (2, "SPEC_PREFLIGHT_INVALID\n"))


if __name__ == "__main__":
    unittest.main()
