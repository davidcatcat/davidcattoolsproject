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
check_read_scope = spec_preflight.check_read_scope
main = spec_preflight.main

BASE_SPEC = {
    "request": "Implement formatting helper for report generation",
    "summary": "Provide string formatting utility",
    "goal": "Support clean report generation formatting",
    "acceptance": ["Format helper returns valid string"],
    "read": [],
    "write": [],
}


class SpecPreflightReadScopeTests(unittest.TestCase):
    def _run_main(self, spec_dict: dict, repo_dir: Path) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            spec_file = Path(tmp) / "spec.json"
            spec_file.write_text(json.dumps(spec_dict), encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = main([str(spec_file), "--repo", str(repo_dir)])
            return code, buf.getvalue()

    def test_failure_read_no_test_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "tools" / "llmtool" / "scripts").mkdir(parents=True)
            helper = repo / "tools" / "llmtool" / "scripts" / "helper.py"
            helper.write_text("# helper\n", encoding="utf-8")

            spec = dict(BASE_SPEC,
                        write=["tools/llmtool/scripts/helper.py"],
                        read=["tools/llmtool/scripts/helper.py"],
                        acceptance=["Update tools/llmtool/scripts/helper.py."])

            failures = check_read_scope(spec, repo)
            self.assertEqual(failures, ["read_no_test_module"])

            code, out = self._run_main(spec, repo)
            self.assertEqual(code, 1)
            self.assertEqual(out, "SPEC_PREFLIGHT_FAIL:read_no_test_module\n")
            self.assertNotIn("helper.py", out)

    def test_failure_acceptance_path_not_in_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "tools" / "llmtool" / "scripts").mkdir(parents=True)
            (repo / "tools" / "llmtool" / "tests").mkdir(parents=True)
            (repo / "tools" / "llmtool" / "scripts" / "helper.py").write_text("# h\n", encoding="utf-8")
            (repo / "tools" / "llmtool" / "tests" / "test_helper.py").write_text("# t\n", encoding="utf-8")

            spec = dict(BASE_SPEC,
                        write=["tools/llmtool/scripts/helper.py"],
                        read=["tools/llmtool/scripts/helper.py", "tools/llmtool/tests/test_helper.py"],
                        acceptance=["Refactor tools/llmtool/scripts/other.py to use helper."])

            failures = check_read_scope(spec, repo)
            self.assertEqual(failures, ["acceptance_path_not_in_read"])

            code, out = self._run_main(spec, repo)
            self.assertEqual(code, 1)
            self.assertEqual(out, "SPEC_PREFLIGHT_FAIL:acceptance_path_not_in_read\n")
            self.assertNotIn("other.py", out)

    def test_failure_read_path_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "tools" / "llmtool" / "scripts").mkdir(parents=True)
            (repo / "tools" / "llmtool" / "tests").mkdir(parents=True)
            (repo / "tools" / "llmtool" / "scripts" / "helper.py").write_text("# h\n", encoding="utf-8")
            (repo / "tools" / "llmtool" / "tests" / "test_helper.py").write_text("# t\n", encoding="utf-8")

            spec = dict(BASE_SPEC,
                        write=["tools/llmtool/scripts/helper.py"],
                        read=["tools/llmtool/scripts/helper.py",
                              "tools/llmtool/tests/test_helper.py",
                              "tools/llmtool/scripts/missing.py"],
                        acceptance=["Update tools/llmtool/scripts/helper.py."])

            failures = check_read_scope(spec, repo)
            self.assertEqual(failures, ["read_path_missing"])

            code, out = self._run_main(spec, repo)
            self.assertEqual(code, 1)
            self.assertEqual(out, "SPEC_PREFLIGHT_FAIL:read_path_missing\n")
            self.assertNotIn("missing.py", out)

    def test_passing_code_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "tools" / "llmtool" / "scripts").mkdir(parents=True)
            (repo / "tools" / "llmtool" / "tests").mkdir(parents=True)
            (repo / "tools" / "llmtool" / "scripts" / "helper.py").write_text("# h\n", encoding="utf-8")
            (repo / "tools" / "llmtool" / "tests" / "test_helper.py").write_text("# t\n", encoding="utf-8")

            spec = dict(BASE_SPEC,
                        write=["tools/llmtool/scripts/helper.py"],
                        read=["tools/llmtool/scripts/helper.py", "tools/llmtool/tests/test_helper.py"],
                        acceptance=["New function in tools/llmtool/scripts/helper.py passes tests."])

            failures = check_read_scope(spec, repo)
            self.assertEqual(failures, [])

            code, out = self._run_main(spec, repo)
            self.assertEqual(code, 0)
            self.assertEqual(out, "SPEC_PREFLIGHT_OK\n")

    def test_passing_docs_only_spec_needs_no_test_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "docs").mkdir(parents=True)
            (repo / "docs" / "existing.md").write_text("# Existing\n", encoding="utf-8")

            spec = dict(BASE_SPEC,
                        write=["docs/guide.md"],
                        read=["docs/existing.md"],
                        acceptance=["Update docs/guide.md using docs/existing.md."])

            failures = check_read_scope(spec, repo)
            self.assertEqual(failures, [])

            code, out = self._run_main(spec, repo)
            self.assertEqual(code, 0)
            self.assertEqual(out, "SPEC_PREFLIGHT_OK\n")

    def test_read_path_in_write_paths_does_not_need_to_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "tools" / "llmtool" / "scripts").mkdir(parents=True)
            (repo / "tools" / "llmtool" / "tests").mkdir(parents=True)
            (repo / "tools" / "llmtool" / "scripts" / "helper.py").write_text("# h\n", encoding="utf-8")
            (repo / "tools" / "llmtool" / "tests" / "test_existing.py").write_text("# t\n", encoding="utf-8")

            # test_new.py is in read and write, does not exist in repo yet
            spec = dict(BASE_SPEC,
                        write=["tools/llmtool/scripts/helper.py", "tools/llmtool/tests/test_new.py"],
                        read=["tools/llmtool/scripts/helper.py",
                              "tools/llmtool/tests/test_existing.py",
                              "tools/llmtool/tests/test_new.py"],
                        acceptance=["Add tools/llmtool/scripts/helper.py and tools/llmtool/tests/test_new.py."])

            failures = check_read_scope(spec, repo)
            self.assertEqual(failures, [])

            code, out = self._run_main(spec, repo)
            self.assertEqual(code, 0)
            self.assertEqual(out, "SPEC_PREFLIGHT_OK\n")

    def test_tests_only_write_needs_no_separate_test_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "tools" / "llmtool" / "tests").mkdir(parents=True)
            (repo / "tools" / "llmtool" / "tests" / "test_helper.py").write_text("# t\n", encoding="utf-8")

            spec = dict(BASE_SPEC,
                        write=["tools/llmtool/tests/test_helper.py"],
                        read=["tools/llmtool/tests/test_helper.py"],
                        acceptance=["Update tools/llmtool/tests/test_helper.py."])

            failures = check_read_scope(spec, repo)
            self.assertEqual(failures, [])

            code, out = self._run_main(spec, repo)
            self.assertEqual(code, 0)
            self.assertEqual(out, "SPEC_PREFLIGHT_OK\n")

    def test_acceptance_path_in_write_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "tools" / "llmtool" / "scripts").mkdir(parents=True)
            (repo / "tools" / "llmtool" / "tests").mkdir(parents=True)
            (repo / "tools" / "llmtool" / "tests" / "test_helper.py").write_text("# t\n", encoding="utf-8")

            # helper.py is in write and in acceptance, not in read
            spec = dict(BASE_SPEC,
                        write=["tools/llmtool/scripts/helper.py"],
                        read=["tools/llmtool/tests/test_helper.py"],
                        acceptance=["Implement tools/llmtool/scripts/helper.py."])

            failures = check_read_scope(spec, repo)
            self.assertEqual(failures, [])

            code, out = self._run_main(spec, repo)
            self.assertEqual(code, 0)
            self.assertEqual(out, "SPEC_PREFLIGHT_OK\n")

    def test_acceptance_token_patterns(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "docs").mkdir(parents=True)
            (repo / "scripts").mkdir(parents=True)
            (repo / ".claude").mkdir(parents=True)
            (repo / "tools" / "llmtool").mkdir(parents=True)
            (repo / "docs" / "doc.md").write_text("# d\n", encoding="utf-8")
            (repo / "scripts" / "run.py").write_text("# r\n", encoding="utf-8")
            (repo / ".claude" / "cfg.json").write_text("{}\n", encoding="utf-8")
            (repo / "tools" / "llmtool" / "conf.json").write_text("{}\n", encoding="utf-8")

            spec = dict(BASE_SPEC,
                        write=["docs/doc.md"],
                        read=["docs/doc.md", "scripts/run.py", ".claude/cfg.json", "tools/llmtool/conf.json"],
                        acceptance=[
                            "Check docs/doc.md, scripts/run.py, .claude/cfg.json, and tools/llmtool/conf.json.",
                            "Do not match tools/llmtool/tests/test_*.py wildcard or other/path.py."
                        ])

            failures = check_read_scope(spec, repo)
            self.assertEqual(failures, [])

    def test_multiple_failures_joined_by_comma(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "tools" / "llmtool" / "scripts").mkdir(parents=True)
            (repo / "tools" / "llmtool" / "scripts" / "helper.py").write_text("# h\n", encoding="utf-8")

            # 1: no test module in read
            # 2: acceptance mentions other.py not in read or write
            # 3: read mentions missing.py not in repo
            spec = dict(BASE_SPEC,
                        write=["tools/llmtool/scripts/helper.py"],
                        read=["tools/llmtool/scripts/helper.py", "tools/llmtool/scripts/missing.py"],
                        acceptance=["Refactor tools/llmtool/scripts/other.py for helper."])

            failures = check_read_scope(spec, repo)
            self.assertEqual(failures, [
                "read_no_test_module",
                "acceptance_path_not_in_read",
                "read_path_missing",
            ])

            code, out = self._run_main(spec, repo)
            self.assertEqual(code, 1)
            self.assertEqual(out, "SPEC_PREFLIGHT_FAIL:read_no_test_module,acceptance_path_not_in_read,read_path_missing\n")

    def test_main_without_repo_does_not_check_read_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec_file = Path(tmp) / "spec.json"
            spec = dict(BASE_SPEC,
                        write=["tools/llmtool/scripts/helper.py"],
                        read=[],
                        acceptance=["Update helper."])
            spec_file.write_text(json.dumps(spec), encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = main([str(spec_file)])
            self.assertEqual(code, 0)
            self.assertEqual(buf.getvalue(), "SPEC_PREFLIGHT_OK\n")

    def test_main_with_repo_and_ladder_refuses_no_test_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "tools" / "llmtool" / "scripts").mkdir(parents=True)
            (repo / "tools" / "llmtool" / "scripts" / "helper.py").write_text("# h\n", encoding="utf-8")

            spec_file = repo / "spec.json"
            ladder_file = repo / "ladder.jsonl"
            ladder_file.write_text(
                json.dumps({"task": "task-001", "tier": 1, "attempts": 1, "outcome": "SOLVED"}) + "\n",
                encoding="utf-8"
            )

            spec = dict(BASE_SPEC,
                        write=["tools/llmtool/scripts/helper.py"],
                        read=["tools/llmtool/scripts/helper.py"],
                        acceptance=["Update tools/llmtool/scripts/helper.py."])
            spec_file.write_text(json.dumps(spec), encoding="utf-8")

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = main([str(spec_file), "--repo", str(repo), "--ladder", str(ladder_file)])
            self.assertEqual(code, 1)
            self.assertEqual(buf.getvalue(), "SPEC_PREFLIGHT_FAIL:read_no_test_module\n")

    def test_main_with_repo_and_ladder_passes_valid_code_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "tools" / "llmtool" / "scripts").mkdir(parents=True)
            (repo / "tools" / "llmtool" / "tests").mkdir(parents=True)
            (repo / "tools" / "llmtool" / "scripts" / "helper.py").write_text("# h\n", encoding="utf-8")
            (repo / "tools" / "llmtool" / "tests" / "test_helper.py").write_text("# t\n", encoding="utf-8")

            spec_file = repo / "spec.json"
            ladder_file = repo / "ladder.jsonl"
            ladder_file.write_text(
                json.dumps({"task": "task-001", "tier": 1, "attempts": 1, "outcome": "SOLVED"}) + "\n",
                encoding="utf-8"
            )

            spec = dict(BASE_SPEC,
                        write=["tools/llmtool/scripts/helper.py"],
                        read=["tools/llmtool/scripts/helper.py", "tools/llmtool/tests/test_helper.py"],
                        acceptance=["Update tools/llmtool/scripts/helper.py."])
            spec_file.write_text(json.dumps(spec), encoding="utf-8")

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = main([str(spec_file), "--repo", str(repo), "--ladder", str(ladder_file)])
            self.assertEqual(code, 0)
            self.assertIn("SPEC_PREFLIGHT_OK:tier=", buf.getvalue())

    def test_read_paths_and_write_paths_fallback_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "tools" / "llmtool" / "scripts").mkdir(parents=True)
            (repo / "tools" / "llmtool" / "tests").mkdir(parents=True)
            (repo / "tools" / "llmtool" / "scripts" / "helper.py").write_text("# h\n", encoding="utf-8")
            (repo / "tools" / "llmtool" / "tests" / "test_helper.py").write_text("# t\n", encoding="utf-8")

            spec = {
                "request": "Implement formatting helper for report generation",
                "summary": "Provide string formatting utility",
                "goal": "Support clean report generation formatting",
                "acceptance": ["Update tools/llmtool/scripts/helper.py."],
                "write_paths": ["tools/llmtool/scripts/helper.py"],
                "read_paths": ["tools/llmtool/scripts/helper.py", "tools/llmtool/tests/test_helper.py"],
            }
            failures = check_read_scope(spec, repo)
            self.assertEqual(failures, [])

            code, out = self._run_main(spec, repo)
            self.assertEqual(code, 0)
            self.assertEqual(out, "SPEC_PREFLIGHT_OK\n")


if __name__ == "__main__":
    unittest.main()
