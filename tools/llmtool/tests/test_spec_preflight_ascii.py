"""#2099: spec_preflight refuses non-ASCII text in gw-agent specs."""
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "spec_preflight.py"
SPEC = importlib.util.spec_from_file_location("spec_preflight_ascii", SCRIPT)
spec_preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(spec_preflight)

BASE = {"request": "Add a test", "summary": "Add a test", "goal": "Create one file",
        "acceptance": ["The file exists"], "read": [], "write": ["new_file.py"],
        "provider": "gw-agent"}
WIDE = "\u5be6"


class AsciiGuardTests(unittest.TestCase):
    def run_main(self, spec):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spec.json"
            path.write_text(json.dumps(spec), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = spec_preflight.main(["--repo", tmp, str(path)])
        return code, out.getvalue().strip()

    def test_ascii_gw_agent_spec_passes(self):
        self.assertEqual(self.run_main(BASE), (0, "SPEC_PREFLIGHT_OK"))

    def test_non_ascii_gw_agent_fields_refuse_with_labels_only(self):
        cases = (({"goal": "Create " + WIDE}, "goal_non_ascii"),
                 ({"acceptance": ["ok", "line " + WIDE]}, "acceptance[1]_non_ascii"),
                 ({"edits": [{"path": "new_file.py", "old": "a", "new": WIDE}]},
                  "edits[0]_non_ascii"))
        for change, label in cases:
            with self.subTest(label=label):
                code, out = self.run_main(dict(BASE, **change))
                self.assertEqual((code, out), (1, "SPEC_PREFLIGHT_FAIL:" + label))
                self.assertNotIn(WIDE, out)

    def test_other_routes_keep_non_ascii(self):
        self.assertEqual(spec_preflight.check_ascii(dict(BASE, provider="claude",
                                                         goal="Create " + WIDE)), [])


if __name__ == "__main__":
    unittest.main()
