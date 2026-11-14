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
spec_tier = spec_preflight.spec_tier
main = spec_preflight.main

LADDER_LOG_TIER_3 = "\n".join([
    json.dumps({"task": "task-001", "tier": 1, "attempts": 1, "outcome": "SOLVED"}),
    json.dumps({"task": "task-002", "tier": 1, "attempts": 1, "outcome": "SOLVED"}),
    json.dumps({"task": "task-003", "tier": 1, "attempts": 1, "outcome": "SOLVED"}),
    json.dumps({"task": "task-004", "tier": 2, "attempts": 1, "outcome": "SOLVED"}),
    json.dumps({"task": "task-005", "tier": 2, "attempts": 1, "outcome": "SOLVED"}),
    json.dumps({"task": "task-006", "tier": 2, "attempts": 1, "outcome": "SOLVED"}),
]) + "\n"

VALID_SPEC = {
    "request": "Implement helper function for calculation",
    "summary": "Provide helper calculation utility",
    "goal": "Support arithmetic formatting",
    "acceptance": ["Calculation helper returns expected result"],
    "read": [],
    "write": ["new_file.py"],
}


class SpecTierNewFileTests(unittest.TestCase):
    def test_existing_one_file_write_small_context_stays_tier_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "existing.py").write_text("print('ok')\n", encoding="utf-8")
            spec = {"write": ["existing.py"]}
            self.assertEqual(spec_tier(spec, context_bytes=1000, repo=repo), 1)

    def test_missing_write_path_gives_tier_4(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            spec = {"write": ["missing.py"]}
            self.assertEqual(spec_tier(spec, context_bytes=1000, repo=repo), 4)

    def test_missing_path_among_existing_ones_gives_4(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "existing.py").write_text("print('ok')\n", encoding="utf-8")
            spec = {"write": ["existing.py", "missing.py"]}
            self.assertEqual(spec_tier(spec, context_bytes=1000, repo=repo), 4)

    def test_path_that_is_directory_counts_as_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "some_dir").mkdir()
            spec = {"write": ["some_dir"]}
            self.assertEqual(spec_tier(spec, context_bytes=1000, repo=repo), 4)

    def test_repo_none_keeps_tier_1_for_missing_path(self):
        spec = {"write": ["missing.py"]}
        self.assertEqual(spec_tier(spec, context_bytes=1000, repo=None), 1)

    def test_size_tier_5_keeps_5_with_new_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            spec = {"write": ["a.py", "b.py", "c.py", "d.py"]}
            self.assertEqual(spec_tier(spec, repo=repo), 5)

    def test_size_tier_6_keeps_6_with_new_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            spec = {"write": ["a.py", "b.py", "c.py", "d.py", "e.py"]}
            self.assertEqual(spec_tier(spec, repo=repo), 6)

    def test_non_string_write_entry_does_not_raise_tier(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            spec = {"write": [123]}
            self.assertEqual(spec_tier(spec, context_bytes=1000, repo=repo), 1)

    def test_main_with_repo_on_spec_creating_file_exceeds_tier(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_dir = tmp_path / "repo"
            repo_dir.mkdir()
            spec_file = tmp_path / "spec.json"
            ladder_file = tmp_path / "ladder.jsonl"

            spec_file.write_text(json.dumps(VALID_SPEC), encoding="utf-8")
            ladder_file.write_text(LADDER_LOG_TIER_3, encoding="utf-8")

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                ret = main([str(spec_file), "--repo", str(repo_dir), "--ladder", str(ladder_file)])

            self.assertEqual(ret, 2)
            self.assertIn("SPEC_LADDER_TIER_EXCEEDED:required=4,current=3", buf.getvalue())

    def test_main_without_repo_on_spec_creating_file_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spec_file = tmp_path / "spec.json"
            ladder_file = tmp_path / "ladder.jsonl"

            spec_file.write_text(json.dumps(VALID_SPEC), encoding="utf-8")
            ladder_file.write_text(LADDER_LOG_TIER_3, encoding="utf-8")

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                ret = main([str(spec_file), "--ladder", str(ladder_file)])

            self.assertEqual(ret, 0)
            self.assertIn("SPEC_PREFLIGHT_OK:tier=1,current=3", buf.getvalue())
