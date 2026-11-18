"""#1502 WP-G subtraction (c6957703194): a spec with an 'edits' field is refused.

The old edits support (anchors, edit caps, line hints, check_edits) is removed on
purpose; these tests prove every 'edits' field gets the fixed refusal label.
"""
import contextlib
import io
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "spec_preflight.py"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SPEC = importlib.util.spec_from_file_location("spec_preflight_edits", SCRIPT)
spec_preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(spec_preflight)

REFUSED = "SPEC_PREFLIGHT_INVALID:EDITS_FORBIDDEN\n"


class SpecPreflightEditsRefusedTests(unittest.TestCase):
    def setUp(self):
        self.base_spec = {
            "request": "Add a bounded helper.",
            "summary": "Check the spec locally.",
            "goal": "Verify the spec before dispatch.",
            "acceptance": ["The spec passes preflight."],
            "write": ["sample.txt"],
        }

    def run_main(self, spec, repo=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.json"
            path.write_text(json.dumps(spec), encoding="utf-8")
            cmd_args = [str(path)]
            if repo is not None:
                cmd_args.extend(["--repo", str(repo)])
            output, errors = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                status = spec_preflight.main(cmd_args)
            return status, output.getvalue(), errors.getvalue()

    def test_edits_that_once_passed_are_refused(self):
        """Edits the removed support accepted (a unique anchor, six at the cap) are refused."""
        with tempfile.TemporaryDirectory() as repo_dir:
            (Path(repo_dir) / "sample.txt").write_text(
                "hello universe\nv0 v1 v2 v3 v4 v5\n", encoding="utf-8")
            one = [{"path": "sample.txt", "old": "universe", "new": "world"}]
            six = [{"path": "sample.txt", "old": f"v{i}", "new": f"w{i}"} for i in range(6)]
            for name, edits in (("one edit", one), ("six edits", six)):
                with self.subTest(edits=name):
                    status, output, errors = self.run_main(
                        dict(self.base_spec, edits=edits), repo=repo_dir)
                    self.assertEqual((status, output), (2, REFUSED))
                    self.assertEqual(errors, spec_preflight.EDITS_FORBIDDEN_MESSAGE + "\n")
                    self.assertNotIn("world", output + errors)

    def test_any_edits_value_is_refused_with_fixed_label(self):
        values = (("empty list", []), ("null", None), ("empty object", {}),
                  ("string", "replace universe"), ("number", 0),
                  ("malformed item", [{"path": "other.txt"}]))
        for name, value in values:
            with self.subTest(edits=name):
                status, output, _ = self.run_main(dict(self.base_spec, edits=value))
                self.assertEqual((status, output), (2, REFUSED))

    def test_refusal_does_not_echo_edit_text(self):
        edits = [{"path": "sample.txt", "old": "anchor-text-a", "new": "answer-text-b"}]
        status, output, errors = self.run_main(dict(self.base_spec, edits=edits))
        self.assertEqual((status, output), (2, REFUSED))
        for text in ("anchor-text-a", "answer-text-b", "sample.txt"):
            self.assertNotIn(text, output + errors)

    def test_specs_without_edits_pass_unchanged(self):
        self.assertEqual(self.run_main(self.base_spec), (0, "SPEC_PREFLIGHT_OK\n", ""))

    def test_edits_support_is_removed(self):
        self.assertFalse(hasattr(spec_preflight, "check_edits"))

    def big_repo(self, directory):
        lines = [f"line {i}" for i in range(1, 2101)]
        (Path(directory) / "sample.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return directory

    def test_large_file_edit_with_line_hint_is_refused(self):
        """The removed line hints no longer let an edit through, even on a long file."""
        with tempfile.TemporaryDirectory() as repo_dir:
            edit = {"path": "sample.txt", "old": "line 1500\n", "new": "line x\n", "line": 1490}
            status, output, _ = self.run_main(
                dict(self.base_spec, provider="gw-agent", edits=[edit]),
                repo=self.big_repo(repo_dir))
            self.assertEqual((status, output), (2, REFUSED))

    def test_gw_agent_spec_writing_large_file_is_refused(self):
        """#1570: a gw-agent spec that writes a long file is still refused."""
        with tempfile.TemporaryDirectory() as repo_dir:
            spec = dict(self.base_spec, provider="gw-agent")
            status, output, _ = self.run_main(spec, repo=self.big_repo(repo_dir))
            self.assertEqual((status, output),
                             (2, "SPEC_PREFLIGHT_INVALID:EDIT_LINE_HINT_MISSING:-1\n"))
            (Path(repo_dir) / "sample.txt").write_text("short\n", encoding="utf-8")
            self.assertEqual(self.run_main(spec, repo=repo_dir), (0, "SPEC_PREFLIGHT_OK\n", ""))


if __name__ == "__main__":
    unittest.main()
