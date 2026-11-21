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


# #1817 refused a spec that declares more modules than one EC2 run takes. #2457
# accepts it: EC2 runs the modules in batches, so preflight now reports the count.
class DeclaredTestModuleCountTests(unittest.TestCase):
    def test_eight_distinct_modules_need_no_batch_note(self):
        spec = {"read": modules(8)}
        self.assertIsNone(spec_preflight.declared_module_batch_note(spec, maximum=8))

    def test_nine_modules_are_accepted_and_report_count_maximum_and_batches(self):
        note = spec_preflight.declared_module_batch_note({"read": modules(9)}, maximum=8)
        self.assertEqual(note, "test_module_batches:count=9:max=8:batches=2")

    def test_same_module_in_read_and_write_counts_once(self):
        path = MODULE_DIR + "test_same.py"
        spec = {"read": [path], "write": [path]}
        self.assertEqual(spec_preflight.declared_test_modules(spec), {path})
        self.assertIsNone(spec_preflight.declared_module_batch_note(spec, maximum=1))

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
        self.assertIsNone(spec_preflight.declared_module_batch_note(spec, maximum=1))

    def test_note_follows_the_remote_test_maximum(self):
        with mock.patch.object(remote_test, "_MAX_REMOTE_MODULES", 2):
            self.assertIsNone(spec_preflight.declared_module_batch_note({"read": modules(2)}))
            note = spec_preflight.declared_module_batch_note({"read": modules(3)})
        self.assertEqual(note, "test_module_batches:count=3:max=2:batches=2")

    def test_preflight_accepts_nine_modules_and_reports_two_batches(self):
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
            output, errors = io.StringIO(), io.StringIO()
            with mock.patch.object(remote_test, "_MAX_REMOTE_MODULES", 8), \
                    mock.patch.object(spec_preflight, "large_file_lines", return_value=2000):
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    code = spec_preflight.main([str(spec_path)])
        self.assertEqual(code, 0)
        self.assertNotIn("test_module_count_exceeds_max", output.getvalue())
        self.assertIn("test_module_batches:count=9:max=8:batches=2", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
