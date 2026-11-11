import contextlib
import io
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "spec_preflight.py"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SPEC = importlib.util.spec_from_file_location("spec_preflight", SCRIPT)
spec_preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(spec_preflight)
check_edits = spec_preflight.check_edits


class SpecPreflightEditsTests(unittest.TestCase):
    def setUp(self):
        self.base_spec = {
            "request": "Add a bounded edit helper.",
            "summary": "Check spec edits field locally.",
            "goal": "Verify edits before dispatch.",
            "acceptance": ["Edits pass preflight."],
            "write": ["sample.txt"],
        }

    def run_main(self, spec, repo=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.json"
            path.write_text(json.dumps(spec), encoding="utf-8")
            cmd_args = [str(path)]
            if repo is not None:
                cmd_args.extend(["--repo", str(repo)])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = spec_preflight.main(cmd_args)
            return status, output.getvalue()

    def test_valid_edits_pass(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            file_path = Path(repo_dir) / "sample.txt"
            file_path.write_text("hello universe\n", encoding="utf-8")
            spec = dict(
                self.base_spec,
                edits=[{"path": "sample.txt", "old": "universe", "new": "world"}],
            )
            status, output = self.run_main(spec, repo=repo_dir)
            self.assertEqual(status, 0)
            self.assertEqual(output, "SPEC_PREFLIGHT_OK\n")

    def test_anchor_twice_fails(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            file_path = Path(repo_dir) / "sample.txt"
            file_path.write_text("repeat word repeat word\n", encoding="utf-8")
            spec = dict(
                self.base_spec,
                edits=[{"path": "sample.txt", "old": "repeat", "new": "unique"}],
            )
            status, output = self.run_main(spec, repo=repo_dir)
            self.assertEqual(status, 2)
            self.assertEqual(output, "SPEC_PREFLIGHT_INVALID:EDIT_ANCHOR_NOT_UNIQUE:0\n")
            self.assertNotIn("repeat", output)
            self.assertNotIn("unique", output)

    def test_anchor_missing_fails(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            file_path = Path(repo_dir) / "sample.txt"
            file_path.write_text("greeting text\n", encoding="utf-8")
            spec = dict(
                self.base_spec,
                edits=[{"path": "sample.txt", "old": "nonexistent target", "new": "replacement"}],
            )
            status, output = self.run_main(spec, repo=repo_dir)
            self.assertEqual(status, 2)
            self.assertEqual(output, "SPEC_PREFLIGHT_INVALID:EDIT_ANCHOR_MISSING:0\n")
            self.assertNotIn("nonexistent target", output)
            self.assertNotIn("replacement", output)

    def test_anchor_missing_when_file_not_found(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            spec = dict(
                self.base_spec,
                edits=[{"path": "sample.txt", "old": "target", "new": "replacement"}],
            )
            status, output = self.run_main(spec, repo=repo_dir)
            self.assertEqual(status, 2)
            self.assertEqual(output, "SPEC_PREFLIGHT_INVALID:EDIT_ANCHOR_MISSING:0\n")

    def test_path_not_in_write_list_fails(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            file_path = Path(repo_dir) / "other.txt"
            file_path.write_text("some content\n", encoding="utf-8")
            spec = dict(
                self.base_spec,
                write=["sample.txt"],
                edits=[{"path": "other.txt", "old": "some", "new": "replacement"}],
            )
            status, output = self.run_main(spec, repo=repo_dir)
            self.assertEqual(status, 2)
            self.assertEqual(output, "SPEC_PREFLIGHT_INVALID:EDIT_PATH_OUT_OF_SCOPE:0\n")
            self.assertNotIn("some", output)
            self.assertNotIn("replacement", output)

    def test_empty_new_text_fails(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            file_path = Path(repo_dir) / "sample.txt"
            file_path.write_text("initial text\n", encoding="utf-8")
            spec = dict(
                self.base_spec,
                edits=[{"path": "sample.txt", "old": "initial", "new": ""}],
            )
            status, output = self.run_main(spec, repo=repo_dir)
            self.assertEqual(status, 2)
            self.assertEqual(output, "SPEC_PREFLIGHT_INVALID:EDIT_NEW_TEXT_INVALID:0\n")

    def test_identical_new_text_fails(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            file_path = Path(repo_dir) / "sample.txt"
            file_path.write_text("initial text\n", encoding="utf-8")
            spec = dict(
                self.base_spec,
                edits=[{"path": "sample.txt", "old": "initial", "new": "initial"}],
            )
            status, output = self.run_main(spec, repo=repo_dir)
            self.assertEqual(status, 2)
            self.assertEqual(output, "SPEC_PREFLIGHT_INVALID:EDIT_NEW_TEXT_INVALID:0\n")

    def test_seven_edits_over_cap_fails(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            file_path = Path(repo_dir) / "sample.txt"
            file_path.write_text("item0 item1 item2 item3 item4 item5 item6\n", encoding="utf-8")
            edits = [
                {"path": "sample.txt", "old": f"item{i}", "new": f"mod{i}"}
                for i in range(7)
            ]
            spec = dict(self.base_spec, edits=edits)
            status, output = self.run_main(spec, repo=repo_dir)
            self.assertEqual(status, 2)
            self.assertEqual(output, "SPEC_PREFLIGHT_INVALID:EDIT_COUNT_OVER_CAP:6\n")
            for i in range(7):
                self.assertNotIn(f"item{i}", output)
                self.assertNotIn(f"mod{i}", output)

    def test_error_names_correct_edit_index(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            file_path = Path(repo_dir) / "sample.txt"
            file_path.write_text("line one\nline two\n", encoding="utf-8")
            edits = [
                {"path": "sample.txt", "old": "line one", "new": "row one"},
                {"path": "sample.txt", "old": "missing anchor", "new": "row two"},
            ]
            spec = dict(self.base_spec, edits=edits)
            status, output = self.run_main(spec, repo=repo_dir)
            self.assertEqual(status, 2)
            self.assertEqual(output, "SPEC_PREFLIGHT_INVALID:EDIT_ANCHOR_MISSING:1\n")
            self.assertNotIn("missing anchor", output)
            self.assertNotIn("row two", output)

    def test_specs_without_edits_pass_unchanged(self):
        status, output = self.run_main(self.base_spec)
        self.assertEqual(status, 0)
        self.assertEqual(output, "SPEC_PREFLIGHT_OK\n")

    def test_six_edits_at_cap_pass(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            file_path = Path(repo_dir) / "sample.txt"
            file_path.write_text("v0 v1 v2 v3 v4 v5\n", encoding="utf-8")
            edits = [
                {"path": "sample.txt", "old": f"v{i}", "new": f"w{i}"}
                for i in range(6)
            ]
            spec = dict(self.base_spec, edits=edits)
            status, output = self.run_main(spec, repo=repo_dir)
            self.assertEqual(status, 0)
            self.assertEqual(output, "SPEC_PREFLIGHT_OK\n")

    def test_check_edits_direct_function(self):
        with tempfile.TemporaryDirectory() as repo_dir:
            file_path = Path(repo_dir) / "sample.txt"
            file_path.write_text("content line\n", encoding="utf-8")
            valid_spec = dict(
                self.base_spec,
                edits=[{"path": "sample.txt", "old": "content", "new": "updated"}],
            )
            self.assertIsNone(check_edits(valid_spec, repo=repo_dir))

            missing_anchor_spec = dict(
                self.base_spec,
                edits=[{"path": "sample.txt", "old": "absent", "new": "updated"}],
            )
            self.assertEqual(
                check_edits(missing_anchor_spec, repo=repo_dir),
                ("EDIT_ANCHOR_MISSING", 0),
            )


if __name__ == "__main__":
    unittest.main()
