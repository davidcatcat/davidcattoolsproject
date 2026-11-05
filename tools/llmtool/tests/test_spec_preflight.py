import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "spec_preflight.py"
SPEC = importlib.util.spec_from_file_location("spec_preflight", SCRIPT)
spec_preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(spec_preflight)
check = spec_preflight.check


class SpecPreflightTests(unittest.TestCase):
    def setUp(self):
        self.spec = {
            "request": "Add a bounded helper.",
            "summary": "Screen a dispatch spec locally.",
            "goal": "Print field names only.",
            "acceptance": ["Return a clean result."],
        }

    def run_main(self, spec):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.json"
            path.write_text(json.dumps(spec), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = spec_preflight.main([str(path)])
            return status, output.getvalue()

    def test_clean_spec(self):
        self.assertEqual(check(self.spec), [])
        status, output = self.run_main(self.spec)
        self.assertEqual((status, output), (0, "SPEC_PREFLIGHT_OK\n"))

    def test_sensitive_value_in_each_field_is_not_printed(self):
        sensitive_value = "private-fixture-value"
        sensitive_text = "tok" + "en = " + sensitive_value
        cases = (
            ("request", sensitive_text, "request"),
            ("summary", sensitive_text, "summary"),
            ("goal", sensitive_text, "goal"),
            ("acceptance", [sensitive_text], "acceptance[0]"),
        )
        for key, value, label in cases:
            with self.subTest(field=key):
                spec = dict(self.spec)
                spec[key] = value
                status, output = self.run_main(spec)
                self.assertEqual(status, 1)
                self.assertEqual(output, f"SPEC_PREFLIGHT_FAIL:{label}\n")
                self.assertNotIn(sensitive_value, output)

    def test_goal_and_acceptance_byte_limits(self):
        for size, expected in ((512, []), (513, ["goal"])):
            with self.subTest(goal_bytes=size):
                spec = dict(self.spec, goal="a" * size)
                self.assertEqual(check(spec), expected)
        for size, expected in ((512, []), (513, ["acceptance[0]"])):
            with self.subTest(acceptance_bytes=size):
                spec = dict(self.spec, acceptance=["a" * size])
                self.assertEqual(check(spec), expected)

    def test_multibyte_text_is_counted_as_utf8_bytes(self):
        self.assertEqual(check(dict(self.spec, goal="é" * 256)), [])
        self.assertEqual(check(dict(self.spec, goal="é" * 257)), ["goal"])
        self.assertEqual(check(dict(self.spec, acceptance=["é" * 257])), ["acceptance[0]"])

    def test_acceptance_count_boundaries(self):
        for count, expected in ((0, ["acceptance_count"]), (1, []), (16, []),
                                (17, ["acceptance_count"])):
            with self.subTest(count=count):
                self.assertEqual(check(dict(self.spec, acceptance=["ok"] * count)), expected)

    def test_invalid_json_and_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.json"
            path.write_text("{", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = spec_preflight.main([str(path)])
            self.assertEqual((status, output.getvalue()), (2, "SPEC_PREFLIGHT_INVALID\n"))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = spec_preflight.main([str(path / "missing")])
            self.assertEqual((status, output.getvalue()), (2, "SPEC_PREFLIGHT_INVALID\n"))

    def test_wrong_field_type_and_argument_count(self):
        spec = dict(self.spec, acceptance=[1])
        status, output = self.run_main(spec)
        self.assertEqual((status, output), (2, "SPEC_PREFLIGHT_INVALID\n"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = spec_preflight.main([])
        self.assertEqual((status, output.getvalue()), (2, "SPEC_PREFLIGHT_INVALID\n"))


if __name__ == "__main__":
    unittest.main()
