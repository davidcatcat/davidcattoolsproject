"""Tests for syntax-only scanning of joined strings in test modules."""

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
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
        return self.findings_source(source, module)

    def findings_source(self, source, module="ordinary_test"):
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

    def test_keyword_value_is_scanned(self):
        self.assertEqual(len(self.findings("call(value='mark' + 'er')")), 1)

    def test_comprehension_clauses_are_scanned(self):
        self.assertEqual(len(self.findings("value = [item for item in ('mark' + 'er',) if item]")), 1)

    def test_with_item_is_scanned(self):
        self.assertEqual(len(self.findings("with ('mark' + 'er') as value:\n    pass")), 1)

    def test_except_handler_body_is_scanned(self):
        self.assertEqual(len(self.findings(
            "try:\n    pass\nexcept Exception:\n    value = 'mark' + 'er'")), 1)

    def test_match_case_body_is_scanned(self):
        self.assertEqual(len(self.findings(
            "match value:\n    case _:\n        result = 'mark' + 'er'")), 1)

    def test_module_class_nested_method_and_module_if_are_scanned(self):
        self.assertEqual(len(self.findings_source(
            "module_value = 'mark' + 'er'\n"
            "class Outer:\n    attribute = 'mark' + 'er'\n"
            "    class Inner:\n        def method(self):\n            value = 'mark' + 'er'\n"
            "if True:\n    def nested():\n        value = 'mark' + 'er'")), 4)

    def test_top_level_method_decorators_and_defaults_are_scanned(self):
        self.assertEqual(len(self.findings_source(
            "@decorate('mark' + 'er')\n"
            "def sample(value='mark' + 'er'): pass\n"
            "class Example:\n    @decorate('mark' + 'er')\n"
            "    def method(self, value='mark' + 'er'): pass")), 4)

    def test_augmented_assignment_uses_statement_line(self):
        findings = self.findings("value = 'mark'\nvalue += 'er'")
        self.assertEqual(len(findings), 1)
        self.assertIn(":3:", findings[0])

    def test_plain_literal_is_ignored(self):
        self.assertEqual(self.findings("value = 'marker'"), [])

    def test_label_is_required(self):
        self.assertEqual(len(self.findings("value = 'mar' + 'ker'", "test_fixture_check")), 1)

    def test_direct_label_allows_screen_tool_fixture(self):
        self.assertEqual(self.findings(
            "# synthetic-fixture: screen-tool test\nvalue = 'mar' + 'ker'",
            "test_fixture_check",
        ), [])

    def test_label_above_multiline_enclosing_statement_allows_fixture(self):
        self.assertEqual(self.findings(
            "# synthetic-fixture: screen-tool test\n"
            "value = (\n    'mark' +\n    'er'\n)",
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

    def test_assignment_targets_clear_tracked_names(self):
        source = "part = 'mark'\nfor part in ():\n    pass\nvalue = part + 'er'"
        self.assertEqual(self.findings(source), [])

    def test_with_as_target_clears_tracked_names(self):
        source = "part = 'mark'\nwith context as part:\n    pass\nvalue = part + 'er'"
        self.assertEqual(self.findings(source), [])

    def test_deep_expression_is_rejected_with_fixed_limit_error(self):
        source = "value = " + " + ".join("'x'" for _ in range(1600))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test_deep.py"
            path.write_text(source, encoding="utf-8")
            output = StringIO()
            with mock.patch.object(fixture_check, "_module_paths", return_value=[path]), \
                    redirect_stdout(output):
                status = fixture_check.main([])
        self.assertEqual(status, 2)
        self.assertEqual(output.getvalue(), "fixture-check: input exceeds fixture-check limits\n")

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



class RepositoryFixtureCheckTests(unittest.TestCase):
    def test_repository_check_fails_above_lowered_temporary_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = Path(directory) / "baseline.json"
            baseline.write_text('{"test_test_output_excerpts.py": 0}\n', encoding="utf-8")
            original_baseline = fixture_check.BASELINE
            try:
                fixture_check.BASELINE = baseline
                status, findings = fixture_check.check()
            finally:
                fixture_check.BASELINE = original_baseline
        self.assertEqual(status, 1)
        self.assertTrue(any("test_test_output_excerpts.py" in item for item in findings))


if __name__ == "__main__":
    unittest.main()
