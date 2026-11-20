import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "spec_preflight.py"

SPEC = importlib.util.spec_from_file_location("spec_preflight", SCRIPT)
spec_preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(spec_preflight)

from llmtool import remote_test  # noqa: E402

MODULE_DIR = "tools/llmtool/tests/"


def modules(count):
    return [f"{MODULE_DIR}test_case_{index}.py" for index in range(count)]


class DeclaredTestModuleCountTests(unittest.TestCase):
    def test_eight_distinct_modules_are_not_a_new_failure(self):
        spec = {"read": modules(8)}
        self.assertEqual(spec_preflight.check_test_module_count(spec, maximum=8), [])

    def test_nine_modules_are_refused_naming_count_and_maximum(self):
        failures = spec_preflight.check_test_module_count({"read": modules(9)}, maximum=8)
        self.assertEqual(len(failures), 1)
        self.assertIn("9", failures[0])
        self.assertIn("8", failures[0])

    def test_same_module_in_read_and_write_counts_once(self):
        path = MODULE_DIR + "test_same.py"
        spec = {"read": [path], "write": [path]}
        self.assertEqual(spec_preflight.declared_test_modules(spec), {path})
        self.assertEqual(spec_preflight.check_test_module_count(spec, maximum=1), [])

    def test_non_test_and_outside_paths_are_not_counted(self):
        spec = {
            "read": [
                MODULE_DIR + "helper.py",
                MODULE_DIR + "sub/test_nested.py",
                "scripts/test_outside.py",
                MODULE_DIR + "test_real.py",
            ],
            "write": ["tools/llmtool/scripts/spec_preflight.py"],
        }
        self.assertEqual(spec_preflight.declared_test_modules(spec),
                         {MODULE_DIR + "test_real.py"})
        self.assertEqual(spec_preflight.check_test_module_count(spec, maximum=1), [])

    def test_check_follows_the_remote_test_maximum(self):
        with mock.patch.object(remote_test, "_MAX_REMOTE_MODULES", 2):
            self.assertEqual(spec_preflight.check_test_module_count({"read": modules(2)}), [])
            failures = spec_preflight.check_test_module_count({"read": modules(3)})
        self.assertEqual(len(failures), 1)
        self.assertIn("3", failures[0])
        self.assertIn("2", failures[0])

    def test_preflight_refuses_nine_modules(self):
        spec = {
            "request": "request text",
            "summary": "summary text",
            "goal": "goal text",
            "acceptance": ["acceptance text"],
            "read": modules(9),
        }
        with tempfile.TemporaryDirectory() as directory:
            spec_path = Path(directory) / "spec.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            output = io.StringIO()
            with mock.patch.object(remote_test, "_MAX_REMOTE_MODULES", 8):
                with contextlib.redirect_stdout(output):
                    code = spec_preflight.main([str(spec_path)])
        self.assertEqual(code, 1)
        self.assertIn("test_module_count_exceeds_max:count=9:max=8", output.getvalue())


if __name__ == "__main__":
    unittest.main()
