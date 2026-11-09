import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch


TOOLS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_ROOT))
from scripts import ci_test_shards


class CiTestShardsTests(unittest.TestCase):
    def test_partition_covers_discovered_modules_once_for_one_to_four_shards(self):
        modules = {path.name for path in ci_test_shards.TESTS.glob("test_*.py")}
        for count in range(1, 5):
            with self.subTest(shard_count=count):
                shards = ci_test_shards.partition(count)
                flattened = [module for shard in shards for module in shard]
                self.assertEqual(set(flattened), modules)
                self.assertEqual(len(flattened), len(set(flattened)))
                self.assertEqual(len(shards), count)

    def test_partition_is_deterministic_and_balanced_within_largest_module(self):
        timing_data = json.loads(ci_test_shards.TIMINGS.read_text(encoding="utf-8"))
        weights = timing_data["modules"]
        default = timing_data["default_seconds"]
        for count in range(1, 5):
            first = ci_test_shards.partition(count)
            self.assertEqual(first, ci_test_shards.partition(count))
            totals = [sum(weights.get(module, default) for module in shard) for shard in first]
            largest = max(
                (weights.get(module, default) for shard in first for module in shard),
                default=default,
            )
            self.assertLessEqual(max(totals) - min(totals), largest)

    def test_unknown_module_uses_default_weight(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            test_dir = root / "tests"
            test_dir.mkdir()
            (test_dir / "test_known.py").touch()
            (test_dir / "test_unknown.py").touch()
            timing_path = root / "timings.json"
            timing_path.write_text(
                json.dumps({"default_seconds": 7, "modules": {"test_known.py": 50}}),
                encoding="utf-8",
            )
            with patch.object(ci_test_shards, "TESTS", test_dir), patch.object(
                ci_test_shards, "TIMINGS", timing_path
            ):
                self.assertEqual(
                    ci_test_shards.partition(2),
                    [["test_known.py"], ["test_unknown.py"]],
                )

    def test_workflow_has_aggregate_gate_and_third_runner_mapping(self):
        workflow = (TOOLS_ROOT.parent.parent / ".github/workflows/llmtool-linux-ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("check (Linux ${{ matrix.python }})", workflow)
        self.assertIn("needs: [changes, shards]", workflow)
        self.assertIn("needs.shards.result", workflow)
        self.assertIn("always()", workflow)
        # #1253: GitHub-hosted runners only; the hosted boundary replaces the EC2 one.
        self.assertNotIn("self-hosted", workflow.split("jobs:", 1)[1])
        self.assertEqual(workflow.count("runs-on: ubuntu-24.04"), 6)
        self.assertEqual(workflow.count("- name: Check GitHub-hosted runner boundary"), 3)
        self.assertEqual(workflow.count('test "$RUNNER_ENVIRONMENT" = github-hosted'), 3)
        self.assertIn("name: check (docs)", workflow)
        self.assertIn("needs: changes", workflow)
        self.assertIn("tools/llmtool/scripts/docs_check.py", workflow)
        self.assertIn("needs.changes.outputs.code == 'true'", workflow)
        classify_match = re.search(
            r"(?ms)^      - name: Classify changed paths\n.*?^        run: \|\n"
            r"(?P<script>(?:^          [^\n]*\n|^          \n)*)",
            workflow,
        )
        self.assertIsNotNone(classify_match)
        classify_script = textwrap.dedent(classify_match.group("script"))
        cases = [
            (["tools/llmtool/README.md"], (False, True)),
            (["tools/llmtool/x/y.md"], (True, True)),
            (["tools/llmtool/llmtool/cli.py"], (True, False)),
            (["AGENTS.md"], (False, True)),
            (
                ["tools/llmtool/README.md", "tools/llmtool/llmtool/cli.py"],
                (True, True),
            ),
            (
                ["tools/llmtool/llmtool/cli.py", "tools/llmtool/README.md"],
                (True, True),
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            bin_dir = temp / "bin"
            bin_dir.mkdir()
            git_stub = bin_dir / "git"
            git_stub.write_text("#!/bin/sh\ncat \"$CLASSIFY_PATHS\"\n", encoding="utf-8")
            git_stub.chmod(0o755)
            for paths, expected in cases:
                with self.subTest(paths=paths):
                    paths_file = temp / "input-paths"
                    paths_file.write_bytes(b"".join(path.encode() + b"\0" for path in paths))
                    output_file = temp / "github-output"
                    env = os.environ.copy()
                    env.update(
                        {
                            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
                            "GITHUB_EVENT_NAME": "pull_request",
                            "GITHUB_OUTPUT": str(output_file),
                            "RUNNER_TEMP": directory,
                            "CLASSIFY_PATHS": str(paths_file),
                            "BASE_SHA": "base",
                            "EXPECTED_SHA": "head",
                        }
                    )
                    subprocess.run(
                        ["bash", "-c", classify_script],
                        cwd=TOOLS_ROOT.parent.parent,
                        env=env,
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    result = dict(
                        line.split("=", 1)
                        for line in output_file.read_text(encoding="utf-8").splitlines()
                    )
                    self.assertEqual((result["code"] == "true", result["docs"] == "true"), expected)
        self.assertIn(
            'unittest discover -s tools/llmtool/tests -p "$module" < /dev/null',
            workflow,
        )


import shutil


class SourceRevisionLooseMergeTests(unittest.TestCase):
    """Verifies loose merge and exact-head assertions in CI Source revision steps."""

    @classmethod
    def setUpClass(cls):
        mac_workflow = (
            TOOLS_ROOT.parent.parent / ".github/workflows/llmtool-mac-ci.yml"
        ).read_text(encoding="utf-8")
        linux_workflow = (
            TOOLS_ROOT.parent.parent / ".github/workflows/llmtool-linux-ci.yml"
        ).read_text(encoding="utf-8")
        cls.mac_scripts = cls._extract_scripts(mac_workflow)
        cls.linux_scripts = cls._extract_scripts(linux_workflow)
        cls.all_scripts = cls.mac_scripts + cls.linux_scripts

    @staticmethod
    def _extract_scripts(workflow: str) -> list[str]:
        # Extract each '- name: Source revision' step's run script after 'run: |'
        pattern = re.compile(
            r"(?ms)^ +- name: Source revision\b.*?^ +run: \|\n"
            r"(?P<script>(?:^ {10}[^\n]*\n)+)"
        )
        return [textwrap.dedent(m.group("script")) for m in pattern.finditer(workflow)]

    def test_extracted_script_counts(self):
        # One Source revision step in Mac workflow, three in Linux workflow.
        self.assertEqual(len(self.mac_scripts), 1)
        self.assertEqual(len(self.linux_scripts), 3)

    def test_text_requirements_in_extracted_scripts(self):
        notice_line = (
            'echo "::notice::head does not contain base $BASE_SHA; CI tested the exact PR head '
            '(loose merge rule, Issue #1317); the merge gate checks GitHub mergeability"'
        )
        for i, script in enumerate(self.all_scripts):
            with self.subTest(script_index=i):
                self.assertIn("::notice::", script)
                self.assertIn("does not contain base", script)
                self.assertIn(notice_line, script)
                self.assertIn('test "$head" = "$EXPECTED_SHA"', script)
                self.assertIn('test "$head" != "$GITHUB_SHA"', script)
                self.assertIn('git merge-base --is-ancestor "$BASE_SHA" "$head"', script)
                self.assertIn("cannot compare head with base", script)
                self.assertNotIn("update the branch", script)
                self.assertNotIn('fail "head does not contain base', script)

    def test_bash_loose_merge_and_head_refusals(self):
        # Skip only if bash or git is missing.
        if shutil.which("bash") is None or shutil.which("git") is None:
            self.skipTest("bash or git is missing")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            repo = temp_path / "repo"
            repo.mkdir()

            git_env = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": str(temp_path),
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
            }

            def run_git(*args):
                subprocess.run(
                    ["git", *args],
                    cwd=repo,
                    env=git_env,
                    check=True,
                    capture_output=True,
                    text=True,
                )

            # Local identity and gpgsign disabled without reading global git config
            run_git("init")
            run_git("config", "user.name", "Test User")
            run_git("config", "user.email", "test@example.com")
            run_git("config", "commit.gpgsign", "false")

            # Commit A parent
            (repo / "file.txt").write_text("initial content\n", encoding="utf-8")
            run_git("add", "file.txt")
            run_git("commit", "-m", "initial commit")
            parent_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                env=git_env,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            # Commit A
            (repo / "file.txt").write_text("commit A content\n", encoding="utf-8")
            run_git("commit", "-am", "commit A")
            sha_a = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                env=git_env,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            # Commit B from A's parent on another branch so B does not contain A
            run_git("checkout", "-b", "branch-b", parent_sha)
            (repo / "file_b.txt").write_text("commit B content\n", encoding="utf-8")
            run_git("add", "file_b.txt")
            run_git("commit", "-m", "commit B")
            sha_b = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                env=git_env,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            dummy_github_sha = "f" * 40

            for i, script in enumerate(self.all_scripts):
                with self.subTest(script_index=i):
                    # B does not contain A: exit 0 and stdout contains ::notice::
                    env_loose = {
                        "PATH": os.environ.get("PATH", ""),
                        "HOME": str(temp_path),
                        "GITHUB_EVENT_NAME": "pull_request",
                        "EXPECTED_SHA": sha_b,
                        "BASE_SHA": sha_a,
                        "GITHUB_SHA": dummy_github_sha,
                    }
                    proc = subprocess.run(
                        ["bash", "-c", script],
                        cwd=repo,
                        env=env_loose,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(
                        proc.returncode,
                        0,
                        msg=f"script {i} failed: stdout={proc.stdout}, stderr={proc.stderr}",
                    )
                    self.assertIn("::notice::", proc.stdout)

                    # Contained base (parent of B): exit 0 and stdout contains 'merge coverage'
                    env_contained = dict(env_loose, BASE_SHA=parent_sha)
                    proc = subprocess.run(
                        ["bash", "-c", script],
                        cwd=repo,
                        env=env_contained,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(
                        proc.returncode,
                        0,
                        msg=f"script {i} failed: stdout={proc.stdout}, stderr={proc.stderr}",
                    )
                    self.assertIn("merge coverage", proc.stdout)

                    # EXPECTED_SHA does not match checked-out HEAD: nonzero exit
                    env_wrong_expected = dict(env_loose, EXPECTED_SHA=sha_a)
                    proc = subprocess.run(
                        ["bash", "-c", script],
                        cwd=repo,
                        env=env_wrong_expected,
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotEqual(proc.returncode, 0)

                    # GITHUB_SHA matches HEAD (merge-ref check): nonzero exit
                    env_merge_ref = dict(env_loose, GITHUB_SHA=sha_b)
                    proc = subprocess.run(
                        ["bash", "-c", script],
                        cwd=repo,
                        env=env_merge_ref,
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
