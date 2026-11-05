"""Tests for syntax-only scanning of joined strings in test modules."""

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fixture_check.py"
SPEC = importlib.util.spec_from_file_location("fixture_check", SCRIPT)
fixture_check = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fixture_check
SPEC.loader.exec_module(fixture_check)


class FixtureCheckTests(unittest.TestCase):
    def setUp(self):
        self.sensitive = mock.patch.object(
            fixture_check, "_emitted_sensitive",
            side_effect=lambda value: "marker" in value or value == "markmark",
        )
        self.hits = mock.patch.object(
            fixture_check, "_screen_hits", side_effect=lambda _value: [("cloud_key", 1)]
        )
        self.sensitive.start()
        self.hits.start()
        self.addCleanup(self.sensitive.stop)
        self.addCleanup(self.hits.stop)

    def findings(self, body, module="ordinary_test"):
        source = "def sample():\n" + "\n".join("    " + line for line in body.splitlines()) + "\n"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / (module + ".py")
            path.write_text(source, encoding="utf-8")
            return fixture_check.analyze_path(path, module)

    def test_addition_is_folded(self):
        self.assertEqual(len(self.findings("value = 'mar' + 'ker'")), 1)

    def test_constant_repeat_is_folded(self):
        self.assertEqual(len(self.findings("value = 'mark' * 2")), 1)

    def test_constant_separator_join_is_folded(self):
        self.assertEqual(len(self.findings("value = ''.join(('mar', 'ker'))")), 1)

    def test_prior_name_resolution_is_folded(self):
        self.assertEqual(len(self.findings("part = 'mar'\nvalue = part + 'ker'")), 1)

    def test_outer_fold_is_counted_once(self):
        self.assertEqual(len(self.findings("value = ('mar' + 'k') + 'er'")), 1)

    def test_plain_literal_is_ignored(self):
        self.assertEqual(self.findings("value = 'marker'"), [])

    def test_label_is_required(self):
        self.assertEqual(len(self.findings("value = 'mar' + 'ker'", "test_fixture_check")), 1)

    def test_direct_label_allows_screen_tool_fixture(self):
        self.assertEqual(self.findings(
            "# synthetic-fixture: screen-tool test\nvalue = 'mar' + 'ker'",
            "test_fixture_check",
        ), [])

    def test_label_one_line_too_high_does_not_allow(self):
        self.assertEqual(len(self.findings(
            "# synthetic-fixture: screen-tool test\nother = 1\nvalue = 'mar' + 'ker'",
            "test_fixture_check",
        )), 1)

    def test_allowlist_does_not_allow_other_modules(self):
        self.assertEqual(len(self.findings(
            "# synthetic-fixture: screen-tool test\nvalue = 'mar' + 'ker'",
            "test_unlisted_module",
        )), 1)

    def test_baseline_over_and_under_are_reported(self):
        over, over_findings = fixture_check.compare_counts({"sample.py": 2}, {"sample.py": 1})
        under, under_findings = fixture_check.compare_counts({"sample.py": 0}, {"sample.py": 1})
        self.assertTrue(over)
        self.assertEqual(over_findings, [])
        self.assertFalse(under)
        self.assertTrue(any("can shrink" in item for item in under_findings))

    def test_parse_error_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.py"
            path.write_text("def broken(:\n", encoding="utf-8")
            with self.assertRaises(SyntaxError):
                fixture_check.analyze_path(path, "broken")

    def test_output_has_locations_and_rules_but_no_folded_text(self):
        findings = self.findings("value = 'mar' + 'ker'")
        output = "\n".join(findings)
        self.assertIn("ordinary_test.py:2: cloud_key", output)
        self.assertNotIn("marker", output)

    def test_repository_check_passes(self):
        # This is run by CI; local intake explicitly prohibits running tests.
        with mock.patch.object(fixture_check, "_emitted_sensitive", wraps=fixture_check._emitted_sensitive), \
                mock.patch.object(fixture_check, "_screen_hits", wraps=fixture_check._screen_hits):
            status, _findings = fixture_check.check()
        self.assertEqual(status, 0)


if __name__ == "__main__":
    unittest.main()
