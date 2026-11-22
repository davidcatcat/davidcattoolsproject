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

MODULE_DIR = "tools/llmtool/tests/"


def modules(count):
    return [f"{MODULE_DIR}test_case_{index}.py" for index in range(count)]


class DeclaredTestModuleCountTests(unittest.TestCase):
    def test_preflight_accepts_legal_test_declarations_without_a_count_gate(self):
        for count in (1, 8, 9, 11, 17, 25, 100):
            spec = {
                "request": "request text",
                "summary": "summary text",
                "goal": "goal text",
                "acceptance": ["acceptance text"],
                "read": modules(count),
            }
            with self.subTest(count=count), tempfile.TemporaryDirectory() as directory:
                spec_path = Path(directory) / "spec.json"
                spec_path.write_text(json.dumps(spec), encoding="utf-8")
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = spec_preflight.main([str(spec_path)])
                self.assertEqual(code, 0, output.getvalue())


if __name__ == "__main__":
    unittest.main()
