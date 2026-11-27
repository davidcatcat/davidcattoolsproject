"""Tests for ci_changed_paths.py and related CI selection wiring."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
LLMTOOL_ROOT = REPO_ROOT / "tools" / "llmtool"
SCRIPT_PATH = LLMTOOL_ROOT / "scripts" / "ci_changed_paths.py"
CI_MAC_MODULES_PATH = LLMTOOL_ROOT / "scripts" / "ci_mac_modules.py"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "llmtool-mac-ci.yml"
SENSITIVE_PATHS = LLMTOOL_ROOT / "sensitive-paths.json"

sys.path.insert(0, str(LLMTOOL_ROOT))
from llmtool.test_selection import HIGH_RISK_FILES  # noqa: E402


def _git_run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *cmd],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )


def _git_commit(cwd: Path, message: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "commit",
            "-m",
            message,
        ],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )


def _get_sha(cwd: Path, ref: str = "HEAD") -> str:
    res = subprocess.run(
        ["git", "rev-parse", ref],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    return res.stdout.strip()


def _run_script(
    base: str,
    head: str,
    format_opt: str,
    repo_dir: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable,
        "-I",
        "-B",
        str(SCRIPT_PATH),
        "--base",
        base,
        "--head",
        head,
        "--format",
        format_opt,
    ]
    if repo_dir is not None:
        cmd.extend(["--repo", str(repo_dir)])
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _run_script_bytes(
    base: str,
    head: str,
    format_opt: str,
    repo_dir: Path | None = None,
) -> subprocess.CompletedProcess[bytes]:
    cmd = [
        sys.executable,
        "-I",
        "-B",
        str(SCRIPT_PATH),
        "--base",
        base,
        "--head",
        head,
        "--format",
        format_opt,
    ]
    if repo_dir is not None:
        cmd.extend(["--repo", str(repo_dir)])
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


class CIChangedPathsTests(unittest.TestCase):
    def test_owner_case_1_pr_behind_main(self) -> None:
        """Owner case 1 (#2062): branch from main, main adds a file, PR edits another.

        The diff between merge base and PR HEAD must include only the PR-edited file,
        not the newly added file on main.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo = Path(tmp_dir)
            _git_run(["init"], repo)
            _git_run(["checkout", "-B", "main"], repo)

            test_dir = repo / "tests"
            test_dir.mkdir(parents=True)
            existing_file = test_dir / "test_existing.py"
            existing_file.write_text("def test_existing(): pass\n", encoding="utf-8")
            _git_run(["add", "."], repo)
            _git_commit(repo, "commit 1: initial existing test")

            # Branch PR from main
            _git_run(["checkout", "-b", "pr"], repo)
            existing_file.write_text(
                "def test_existing(): pass\n# edited in PR\n",
                encoding="utf-8",
            )
            _git_run(["add", "."], repo)
            _git_commit(repo, "commit 3: edit existing test in PR")
            pr_head = _get_sha(repo, "HEAD")

            # On main, add a new test module
            _git_run(["checkout", "main"], repo)
            new_mod = test_dir / "test_new_mod.py"
            new_mod.write_text("def test_new(): pass\n", encoding="utf-8")
            _git_run(["add", "."], repo)
            _git_commit(repo, "commit 2: add new mod on main")
            main_head = _get_sha(repo, "HEAD")

            # lines format
            res_lines = _run_script(
                base=main_head,
                head=pr_head,
                format_opt="lines",
                repo_dir=repo,
            )
            self.assertEqual(res_lines.returncode, 0)
            self.assertEqual(
                res_lines.stdout.splitlines(),
                ["tests/test_existing.py"],
            )

            # nul format
            res_nul = _run_script(
                base=main_head,
                head=pr_head,
                format_opt="nul",
                repo_dir=repo,
            )
            self.assertEqual(res_nul.returncode, 0)
            paths_nul = [p for p in res_nul.stdout.split("\0") if p]
            self.assertEqual(paths_nul, ["tests/test_existing.py"])

    def test_owner_case_6_pr_head_contains_base(self) -> None:
        """Owner case 6: when PR head already contains the latest base.

        Output equals the plain two-dot 'git diff --name-only BASE HEAD'.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo = Path(tmp_dir)
            _git_run(["init"], repo)
            _git_run(["checkout", "-B", "main"], repo)

            file1 = repo / "file1.txt"
            file1.write_text("base content\n", encoding="utf-8")
            _git_run(["add", "."], repo)
            _git_commit(repo, "commit 1: base commit")
            base_sha = _get_sha(repo, "HEAD")

            _git_run(["checkout", "-b", "pr"], repo)
            file1.write_text("updated content in PR\n", encoding="utf-8")
            file2 = repo / "file2.txt"
            file2.write_text("new file in PR\n", encoding="utf-8")
            _git_run(["add", "."], repo)
            _git_commit(repo, "commit 2: pr commit")
            pr_sha = _get_sha(repo, "HEAD")

            res_lines = _run_script(
                base=base_sha,
                head=pr_sha,
                format_opt="lines",
                repo_dir=repo,
            )
            self.assertEqual(res_lines.returncode, 0)
            script_paths = sorted(res_lines.stdout.splitlines())

            git_diff_res = subprocess.run(
                ["git", "diff", "--name-only", base_sha, pr_sha],
                cwd=repo,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
            )
            git_paths = sorted(git_diff_res.stdout.splitlines())

            self.assertEqual(script_paths, git_paths)
            self.assertEqual(script_paths, ["file1.txt", "file2.txt"])

    def test_owner_case_5_invalid_sha_unknown_commit_unrelated_histories(self) -> None:
        """Case 5: invalid SHA, unknown commit, unrelated histories: exit 2 and empty stdout."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo = Path(tmp_dir)
            _git_run(["init"], repo)
            _git_run(["checkout", "-B", "main"], repo)

            test_file = repo / "init.txt"
            test_file.write_text("init\n", encoding="utf-8")
            _git_run(["add", "."], repo)
            _git_commit(repo, "init commit")
            valid_sha = _get_sha(repo, "HEAD")

            invalid_shas = [
                "not-a-sha",
                "1234",
                "0" * 39,
                "0" * 41,
                "A" * 40,
                "g" * 40,
                "1234567890abcdef1234567890abcdef1234567G",
            ]
            for bad_sha in invalid_shas:
                for fmt in ("lines", "nul"):
                    res = _run_script(
                        base=bad_sha,
                        head=valid_sha,
                        format_opt=fmt,
                        repo_dir=repo,
                    )
                    self.assertEqual(res.returncode, 2)
                    self.assertEqual(res.stdout, "")
                    self.assertIn("ci_changed_paths:", res.stderr)

                    res2 = _run_script(
                        base=valid_sha,
                        head=bad_sha,
                        format_opt=fmt,
                        repo_dir=repo,
                    )
                    self.assertEqual(res2.returncode, 2)
                    self.assertEqual(res2.stdout, "")
                    self.assertIn("ci_changed_paths:", res2.stderr)

            # Unknown commit (valid 40 hex characters but non-existent in git repo)
            unknown_sha = "0" * 40
            for fmt in ("lines", "nul"):
                res = _run_script(
                    base=unknown_sha,
                    head=valid_sha,
                    format_opt=fmt,
                    repo_dir=repo,
                )
                self.assertEqual(res.returncode, 2)
                self.assertEqual(res.stdout, "")
                self.assertIn("ci_changed_paths:", res.stderr)

                res2 = _run_script(
                    base=valid_sha,
                    head=unknown_sha,
                    format_opt=fmt,
                    repo_dir=repo,
                )
                self.assertEqual(res2.returncode, 2)
                self.assertEqual(res2.stdout, "")
                self.assertIn("ci_changed_paths:", res2.stderr)

            # Unrelated histories (no merge base)
            _git_run(["checkout", "--orphan", "orphan_branch"], repo)
            _git_run(["rm", "-rf", "."], repo)
            orphan_file = repo / "orphan.txt"
            orphan_file.write_text("orphan\n", encoding="utf-8")
            _git_run(["add", "."], repo)
            _git_commit(repo, "orphan commit")
            orphan_sha = _get_sha(repo, "HEAD")

            for fmt in ("lines", "nul"):
                res = _run_script(
                    base=valid_sha,
                    head=orphan_sha,
                    format_opt=fmt,
                    repo_dir=repo,
                )
                self.assertEqual(res.returncode, 2)
                self.assertEqual(res.stdout, "")
                self.assertIn("ci_changed_paths:", res.stderr)

    def test_deleted_and_modified_files_listed(self) -> None:
        """Deleted and modified files are both listed in the changed paths output."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo = Path(tmp_dir)
            _git_run(["init"], repo)
            _git_run(["checkout", "-B", "main"], repo)

            mod_file = repo / "file_mod.txt"
            mod_file.write_text("initial mod\n", encoding="utf-8")
            del_file = repo / "file_del.txt"
            del_file.write_text("initial del\n", encoding="utf-8")
            _git_run(["add", "."], repo)
            _git_commit(repo, "initial files")
            base_sha = _get_sha(repo, "HEAD")

            _git_run(["checkout", "-b", "pr"], repo)
            mod_file.write_text("updated mod\n", encoding="utf-8")
            _git_run(["rm", "file_del.txt"], repo)
            _git_run(["add", "."], repo)
            _git_commit(repo, "modify one file, remove another")
            head_sha = _get_sha(repo, "HEAD")

            res = _run_script(
                base=base_sha,
                head=head_sha,
                format_opt="lines",
                repo_dir=repo,
            )
            self.assertEqual(res.returncode, 0)
            paths = sorted(res.stdout.splitlines())
            self.assertEqual(paths, ["file_del.txt", "file_mod.txt"])

    def test_diagnostic_lines_escape_breaks_and_keep_nul_bytes(self) -> None:
        """Lines mode keeps one entry per path; NUL mode bytes stay unchanged."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo = Path(tmp_dir)
            _git_run(["init"], repo)
            _git_run(["checkout", "-B", "main"], repo)

            ordinary = repo / "ordinary.txt"
            ordinary.write_text("ordinary\n", encoding="utf-8")
            newline_path = repo / "diag\nbreak.txt"
            newline_path.write_text("newline name\n", encoding="utf-8")
            vertical_tab_path = repo / "diag\vbreak.txt"
            vertical_tab_path.write_text("vertical tab name\n", encoding="utf-8")
            space_path = repo / "diag space.txt"
            space_path.write_text("space name\n", encoding="utf-8")
            escape_path = repo / "diag\\nliteral.txt"
            escape_path.write_text("escape-looking name\n", encoding="utf-8")
            _git_run(["add", "."], repo)
            _git_commit(repo, "add diagnostic path fixtures")
            base_sha = _get_sha(repo, "HEAD")

            _git_run(["checkout", "-b", "pr"], repo)
            fixtures = (
                ordinary,
                newline_path,
                vertical_tab_path,
                space_path,
                escape_path,
            )
            for path in fixtures:
                path.write_text(
                    path.read_text(encoding="utf-8") + "edited\n",
                    encoding="utf-8",
                )
            _git_run(["add", "."], repo)
            _git_commit(repo, "edit diagnostic path fixtures")
            head_sha = _get_sha(repo, "HEAD")

            res_lines = _run_script(
                base=base_sha,
                head=head_sha,
                format_opt="lines",
                repo_dir=repo,
            )
            self.assertEqual(res_lines.returncode, 0)
            line_paths = res_lines.stdout.splitlines()
            self.assertEqual(len(line_paths), 5)
            self.assertEqual(
                set(line_paths),
                {
                    "ordinary.txt",
                    "diag\\nbreak.txt",
                    "diag\\vbreak.txt",
                    "diag space.txt",
                    "diag\\\\nliteral.txt",
                },
            )

            res_nul = _run_script_bytes(
                base=base_sha,
                head=head_sha,
                format_opt="nul",
                repo_dir=repo,
            )
            self.assertEqual(res_nul.returncode, 0)
            self.assertEqual(
                res_nul.stdout,
                b"diag\nbreak.txt\0"
                b"diag\vbreak.txt\0"
                b"diag space.txt\0"
                b"diag\\nliteral.txt\0"
                b"ordinary.txt\0",
            )


class SelectorIntegrationTests(unittest.TestCase):
    def _run_ci_mac_modules(self, paths: list[str]) -> dict:
        with tempfile.NamedTemporaryFile("wb", delete=False) as tf:
            for p in paths:
                tf.write(p.encode("utf-8") + b"\0")
            tf_name = tf.name
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(CI_MAC_MODULES_PATH),
                    "--changed-paths",
                    tf_name,
                    "--json-report",
                ],
                cwd=str(REPO_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
            )
            return json.loads(proc.stdout)
        finally:
            Path(tf_name).unlink(missing_ok=True)

    def test_owner_case_2_known_test_module(self) -> None:
        """One known test module -> level L1/L2 with that module, not FULL."""
        report = self._run_ci_mac_modules(["tools/llmtool/tests/test_ci_mac_modules.py"])
        self.assertIn(report["level"], ("L1", "L2"))
        self.assertNotIn("FULL", report["test_ids"])
        self.assertTrue(
            any("test_ci_mac_modules" in tid for tid in report["test_ids"]),
            f"Expected test_ci_mac_modules in {report['test_ids']}",
        )

    def test_owner_case_3_high_risk_selector_change(self) -> None:
        """A high-risk path (ci_mac_modules.py) -> L3 FULL with non-empty fallback_reasons."""
        report = self._run_ci_mac_modules(["tools/llmtool/scripts/ci_mac_modules.py"])
        self.assertEqual(report["level"], "L3")
        self.assertEqual(report["test_ids"], ["FULL"])
        self.assertTrue(len(report["fallback_reasons"]) > 0)

    def test_owner_case_4_deleted_unmappable_test(self) -> None:
        """A deleted unmappable test -> L3 FULL."""
        report = self._run_ci_mac_modules(["tools/llmtool/tests/test_does_not_exist_2086.py"])
        self.assertEqual(report["level"], "L3")
        self.assertEqual(report["test_ids"], ["FULL"])

    def test_ci_changed_paths_in_high_risk_files(self) -> None:
        """ci_changed_paths.py in HIGH_RISK_FILES -> L3 FULL with non-empty fallback_reasons."""
        self.assertIn("tools/llmtool/scripts/ci_changed_paths.py", HIGH_RISK_FILES)
        report = self._run_ci_mac_modules(["tools/llmtool/scripts/ci_changed_paths.py"])
        self.assertEqual(report["level"], "L3")
        self.assertEqual(report["test_ids"], ["FULL"])
        self.assertTrue(len(report["fallback_reasons"]) > 0)


class WorkflowCoverageModeTests(unittest.TestCase):
    def test_workflow_coverage_mode_step(self) -> None:
        """Verify select job's 'Coverage mode' step in llmtool-mac-ci.yml."""
        data = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        select_job = data["jobs"]["select"]

        # Checkout ref is unchanged across checkout steps
        for step in select_job["steps"]:
            if str(step.get("uses", "")).startswith("actions/checkout@"):
                self.assertEqual(
                    step.get("with", {}).get("ref"),
                    "${{ github.event.pull_request.head.sha || github.sha }}",
                )

        # Find Coverage mode step
        coverage_step = next(
            s for s in select_job["steps"] if s.get("id") == "coverage"
        )
        run_text = coverage_step["run"]

        # No remaining 'git diff' using "$BASE_SHA" "$EXPECTED_SHA"
        pattern = re.compile(r'git\s+diff.*"\$BASE_SHA".*"\$EXPECTED_SHA"')
        self.assertIsNone(
            pattern.search(run_text),
            "Found remaining git diff using $BASE_SHA and $EXPECTED_SHA",
        )

        # Every changed-path read uses ci_changed_paths.py
        expected_if = (
            'if python3 -I -B tools/llmtool/scripts/ci_changed_paths.py '
            '--base "$BASE_SHA" --head "$EXPECTED_SHA" --format nul > "$paths"; then'
        )
        self.assertIn(expected_if, run_text)

        # Diagnostic log lines use ci_changed_paths.py with format lines
        expected_diagnostic = (
            'python3 -I -B tools/llmtool/scripts/ci_changed_paths.py '
            '--base "$BASE_SHA" --head "$EXPECTED_SHA" --format lines '
            '|| echo "(changed paths unavailable)"'
        )
        self.assertIn(expected_diagnostic, run_text)

        # Selection call is inside 'if ... > "$paths"; then' with mode=full set before it
        mode_full_idx = run_text.index("mode=full")
        if_idx = run_text.index(expected_if)
        self.assertLess(mode_full_idx, if_idx)


class SensitivePathsConfigurationTests(unittest.TestCase):
    def test_sensitive_paths_includes_ci_changed_paths(self) -> None:
        data = json.loads(SENSITIVE_PATHS.read_text(encoding="utf-8"))
        exact = data["exact"]
        self.assertIn("tools/llmtool/scripts/ci_changed_paths.py", exact)
        mac_idx = exact.index("tools/llmtool/scripts/ci_mac_modules.py")
        cp_idx = exact.index("tools/llmtool/scripts/ci_changed_paths.py")
        self.assertEqual(cp_idx, mac_idx + 1)


if __name__ == "__main__":
    unittest.main()
