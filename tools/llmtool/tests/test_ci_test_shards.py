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
        self.assertIn("ec2-self-runner-3) account=ghrunner3", workflow)
        self.assertEqual(workflow.count("ec2-self-runner-4) account=ghrunner4 ;;"), 3)
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
            (["scripts/llmtool_monitor.py"], (True, False)),
            (["skills/x/tool.py"], (True, False)),
            (["skills/x/SKILL.md"], (False, True)),
            ([".github/workflows/llmtool-mac-ci.yml"], (True, False)),
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

    def _script_test_step(self):
        workflow = (TOOLS_ROOT.parent.parent / ".github/workflows/llmtool-linux-ci.yml").read_text(
            encoding="utf-8"
        )
        match = re.search(
            r"(?ms)^      - name: Repository script tests\n(?P<head>.*?)^        run: \|\n"
            r"(?P<script>(?:^          [^\n]*\n|^          \n)*)",
            workflow,
        )
        self.assertIsNotNone(match)
        return workflow, match.group("head"), textwrap.dedent(match.group("script"))

    def test_shard_one_runs_script_tests_on_3_14_before_clean_tree_check(self):
        workflow, head, script = self._script_test_step()
        self.assertIn("if: matrix.shard == 1 && matrix.python == '3.14'", head)
        self.assertIn("PYTHONDONTWRITEBYTECODE: '1'", head)
        self.assertIn("shell: bash", head)
        self.assertIn(
            "\"$JOB_PYTHON\" -B -m unittest discover -s scripts -p 'test_*.py' < /dev/null",
            script,
        )
        self.assertLess(workflow.index("- name: Repository script tests"),
                        workflow.index("- name: Working tree is clean"))
        shards_job = workflow[workflow.index("\n  shards:\n"):workflow.index("\n  check:\n")]
        self.assertIn("- name: Repository script tests", shards_job)
        comment = workflow[:workflow.index("- name: Repository script tests")]
        comment = comment[comment.rindex("- name: Safe-git preflight tests"):]
        self.assertIn("#1174", comment)
        self.assertIn("3.9", comment)

    def test_script_test_step_runs_scripts_and_skills_and_fails_on_failure(self):
        _, _, script = self._script_test_step()
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            stub = temp / "python-stub"
            log = temp / "calls.log"
            # The stub records its arguments, then fails when the start dir is listed
            # in STUB_FAIL_DIR, standing in for a failing unittest run.
            stub.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >> \"$STUB_LOG\"\n"
                "case \" $* \" in *\" -s $STUB_FAIL_DIR \"*) exit 1 ;; esac\n"
                "exit 0\n",
                encoding="utf-8",
            )
            stub.chmod(0o755)
            repo = temp / "repo"
            (repo / "scripts").mkdir(parents=True)
            (repo / "skills" / "x").mkdir(parents=True)
            (repo / "skills" / "x" / "SKILL.md").write_text("skill\n", encoding="utf-8")

            def run(fail_dir="none"):
                log.write_text("", encoding="utf-8")
                env = os.environ.copy()
                env.update({"JOB_PYTHON": str(stub), "STUB_LOG": str(log),
                            "STUB_FAIL_DIR": fail_dir})
                result = subprocess.run(
                    ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
                    cwd=repo, env=env, capture_output=True, text=True,
                    stdin=subprocess.DEVNULL,
                )
                return result, log.read_text(encoding="utf-8").splitlines()

            with self.subTest(case="no skills tests"):
                result, calls = run()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(calls, ["-B -m unittest discover -s scripts -p test_*.py"])
                self.assertIn("nothing to run", result.stdout)

            with self.subTest(case="scripts failure"):
                result, calls = run("scripts")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(calls), 1)

            (repo / "skills" / "x" / "test_tool.py").write_text("", encoding="utf-8")
            (repo / "skills" / "y" / "deep").mkdir(parents=True)
            (repo / "skills" / "y" / "deep" / "test_more.py").write_text("", encoding="utf-8")

            with self.subTest(case="skills tests present"):
                result, calls = run()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(calls, [
                    "-B -m unittest discover -s scripts -p test_*.py",
                    "-B -m unittest discover -s skills/x -p test_*.py",
                    "-B -m unittest discover -s skills/y/deep -p test_*.py",
                ])

            with self.subTest(case="skills failure"):
                result, calls = run("skills/x")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("-B -m unittest discover -s skills/x -p test_*.py", calls)


if __name__ == "__main__":
    unittest.main()
