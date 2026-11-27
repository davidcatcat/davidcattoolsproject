"""Baseline-shrink notes stay separate from violations (#1230)."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fixture_check.py"
SPEC = importlib.util.spec_from_file_location("fixture_check_notes_1230", SCRIPT)
fixture_check = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fixture_check
SPEC.loader.exec_module(fixture_check)


class FixtureCheckNoteTests(unittest.TestCase):
    def run_with(self, baseline, findings, *, use_main):
        with tempfile.TemporaryDirectory() as directory:
            baseline_path = Path(directory) / "baseline.json"
            baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
            module_path = fixture_check.LLMTOOL / "tests" / "sample.py"
            output = StringIO()
            with mock.patch.object(fixture_check, "BASELINE", baseline_path), \
                    mock.patch.object(fixture_check, "_module_paths", return_value=[module_path]), \
                    mock.patch.object(fixture_check, "analyze_path", return_value=list(findings)), \
                    redirect_stdout(output):
                if use_main:
                    status = fixture_check.main([])
                    violations = None
                else:
                    status, violations = fixture_check.check()
        return status, violations, output.getvalue()

    def test_check_prints_shrink_note_without_a_violation(self):
        status, violations, output = self.run_with({"sample.py": 1}, [], use_main=False)
        self.assertEqual(status, 0)
        self.assertEqual(violations, [])
        self.assertIn("fixture-check note: sample.py: baseline can shrink (1 to 0)", output)
        self.assertNotIn("violation", output)

    def test_main_prints_shrink_note_and_counts_zero_violations(self):
        status, _violations, output = self.run_with({"sample.py": 1}, [], use_main=True)
        self.assertEqual(status, 0)
        self.assertIn("fixture-check note: sample.py: baseline can shrink (1 to 0)", output)
        self.assertIn("fixture-check: 0 violation(s)", output)
        self.assertIn(
            "fixture-check: baseline outcome: within baseline; 0 observed finding(s)", output)

    def test_main_reports_findings_within_baseline_and_succeeds(self):
        status, _violations, output = self.run_with(
            {"sample.py": 1}, ["sample.py:1: demo_rule"], use_main=True)
        self.assertEqual(status, 0)
        self.assertIn("tools/llmtool/tests/sample.py:1: demo_rule", output)
        self.assertIn("fixture-check: 1 violation(s)", output)
        self.assertIn(
            "fixture-check: baseline outcome: within baseline; 1 observed finding(s)", output)

    def test_exceeded_baseline_keeps_status_and_counts_real_violation(self):
        status, _violations, output = self.run_with(
            {"sample.py": 0}, ["sample.py:1: demo_rule"], use_main=True)
        self.assertEqual(status, 1)
        self.assertIn("tools/llmtool/tests/sample.py:1: demo_rule", output)
        self.assertIn("fixture-check: 1 violation(s)", output)
        self.assertIn(
            "fixture-check: baseline outcome: above baseline; 1 observed finding(s)", output)
        self.assertNotIn("within baseline", output)
        self.assertNotIn("fixture-check note:", output)

    def test_check_returns_exceeded_status_with_the_real_violation(self):
        status, violations, output = self.run_with(
            {"sample.py": 0}, ["sample.py:1: demo_rule"], use_main=False)
        self.assertEqual(status, 1)
        self.assertEqual(violations, ["tools/llmtool/tests/sample.py:1: demo_rule"])
        self.assertNotIn("fixture-check note:", output)


if __name__ == "__main__":
    unittest.main()
