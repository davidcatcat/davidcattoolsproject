import json
import os
import re
import shutil
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

    def test_workflow_paths_include_scripts_and_workflows_preserving_order(self):
        workflow = (TOOLS_ROOT.parent.parent / ".github/workflows/llmtool-linux-ci.yml").read_text(
            encoding="utf-8"
        )
        expected_paths = [
            "tools/llmtool/**",
            "!tools/llmtool/*.md",
            ".github/workflows/llmtool-linux-ci.yml",
            "tools/ec2-runner/**",
            "tools/safe-git/**",
            "tools/api-agent-gateway/**",
            "tools/ai-loop/**",
            "AGENTS.md",
            "CLAUDE.md",
            "skills/**",
            "docs/**",
            "tools/llmtool/*.md",
            "*.md",
            "scripts/**",
            ".github/workflows/**",
        ]
        header = workflow.split("jobs:", 1)[0]
        # Under push there are a branches list and a comment line before paths:,
        # and under pull_request a comment line comes before paths:.
        # Parse the lines between each paths: key and the next key without assuming
        # the trigger and paths: are adjacent.
        paths_matches = list(
            re.finditer(
                r"(?ms)^\s+paths:\n(?P<entries>.*?)(?=^\s+[a-zA-Z0-9_-]+:|\Z)",
                header,
            )
        )
        self.assertEqual(len(paths_matches), 2)
        self.assertIn("push:", header[: paths_matches[0].start()])
        self.assertIn(
            "pull_request:",
            header[paths_matches[0].end() : paths_matches[1].start()],
        )

        push_paths = [
            line.strip().lstrip("- ").strip("'\"")
            for line in paths_matches[0].group("entries").splitlines()
            if line.strip().startswith("-")
        ]
        self.assertEqual(push_paths, expected_paths)
        self.assertEqual(push_paths[-2:], ["scripts/**", ".github/workflows/**"])
        idx_llmtool = push_paths.index("tools/llmtool/**")
        self.assertEqual(push_paths[idx_llmtool + 1], "!tools/llmtool/*.md")

        pr_paths = [
            line.strip().lstrip("- ").strip("'\"")
            for line in paths_matches[1].group("entries").splitlines()
            if line.strip().startswith("-")
        ]
        self.assertEqual(pr_paths, expected_paths)
        self.assertEqual(pr_paths[-2:], ["scripts/**", ".github/workflows/**"])
        idx_llmtool_pr = pr_paths.index("tools/llmtool/**")
        self.assertEqual(pr_paths[idx_llmtool_pr + 1], "!tools/llmtool/*.md")

    def test_repository_script_tests_step_contract(self):
        workflow = (TOOLS_ROOT.parent.parent / ".github/workflows/llmtool-linux-ci.yml").read_text(
            encoding="utf-8"
        )
        shards_section = workflow.split("shards:", 1)[1].split("check:", 1)[0]
        self.assertIn("name: Repository script tests", shards_section)
        step_pos = shards_section.index("name: Repository script tests")
        clean_pos = shards_section.index("name: Working tree is clean")
        self.assertLess(step_pos, clean_pos)
        if "name: Safe-git preflight tests" in shards_section:
            safe_git_pos = shards_section.index("name: Safe-git preflight tests")
            self.assertLess(safe_git_pos, step_pos)

        step_match = re.search(
            r"(?ms)(?P<comment>(?:^[ ]*#[^\n]*\n)+)?[ ]*- name: Repository script tests\n(?P<body>.*?)(?=^[ ]*- name:|\Z)",
            shards_section,
        )
        self.assertIsNotNone(step_match)
        comment = step_match.group("comment") or ""
        body = step_match.group("body")

        self.assertIn("#1174", comment)
        self.assertIn("3.9", comment)
        self.assertIn("matrix.shard == 1 && matrix.python == '3.14'", body)
        self.assertIn("shell: bash", body)
        self.assertIn("PYTHONDONTWRITEBYTECODE: '1'", body)
        self.assertIn(
            '"$JOB_PYTHON" -B -m unittest discover -s scripts -p \'test_*.py\' < /dev/null',
            body,
        )

    def test_repository_script_tests_run_script_execution(self):
        if shutil.which("bash") is None:
            self.skipTest("bash is missing")

        workflow = (TOOLS_ROOT.parent.parent / ".github/workflows/llmtool-linux-ci.yml").read_text(
            encoding="utf-8"
        )
        step_match = re.search(
            r"(?ms)^      - name: Repository script tests\n.*?^        run: \|\n"
            r"(?P<script>(?:^          [^\n]*\n|^          \n)*)",
            workflow,
        )
        self.assertIsNotNone(step_match)
        step_script = textwrap.dedent(step_match.group("script"))

        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            (temp / "scripts").mkdir()
            skills_dir = temp / "skills"
            skills_dir.mkdir()

            fake_python = temp / "fake_python"
            fake_python.write_text(
                "#!/bin/sh\n"
                "if [ -n \"$FAKE_PYTHON_LOG\" ]; then\n"
                "  echo \"$@\" >> \"$FAKE_PYTHON_LOG\"\n"
                "fi\n"
                "if [ \"$FAKE_PYTHON_FAIL\" = \"scripts\" ]; then\n"
                "  for arg in \"$@\"; do\n"
                "    if [ \"$arg\" = \"scripts\" ]; then\n"
                "      exit 1\n"
                "    fi\n"
                "  done\n"
                "fi\n"
                "if [ \"$FAKE_PYTHON_FAIL\" = \"skills\" ]; then\n"
                "  for arg in \"$@\"; do\n"
                "    case \"$arg\" in\n"
                "      skills*) exit 1 ;;\n"
                "    esac\n"
                "  done\n"
                "fi\n"
                "exit 0\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)

            # Case 1: no skills tests gives exit 0 and 'nothing to run'
            log1 = temp / "log1.txt"
            env1 = os.environ.copy()
            env1["JOB_PYTHON"] = str(fake_python)
            env1["FAKE_PYTHON_LOG"] = str(log1)
            proc1 = subprocess.run(
                ["bash", "-e", "-c", step_script],
                cwd=temp,
                env=env1,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc1.returncode, 0)
            self.assertIn("nothing to run", proc1.stdout)
            lines1 = log1.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines1), 1)
            self.assertIn("discover -s scripts", lines1[0])

            # Case 2: a failing scripts run gives nonzero
            log2 = temp / "log2.txt"
            env2 = os.environ.copy()
            env2["JOB_PYTHON"] = str(fake_python)
            env2["FAKE_PYTHON_LOG"] = str(log2)
            env2["FAKE_PYTHON_FAIL"] = "scripts"
            proc2 = subprocess.run(
                ["bash", "-e", "-c", step_script],
                cwd=temp,
                env=env2,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(proc2.returncode, 0)

            # Case 3: a skills folder with a test_*.py file is discovered
            skill_folder = skills_dir / "my_skill"
            skill_folder.mkdir(parents=True)
            (skill_folder / "test_sample.py").touch()

            log3 = temp / "log3.txt"
            env3 = os.environ.copy()
            env3["JOB_PYTHON"] = str(fake_python)
            env3["FAKE_PYTHON_LOG"] = str(log3)
            proc3 = subprocess.run(
                ["bash", "-e", "-c", step_script],
                cwd=temp,
                env=env3,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc3.returncode, 0)
            self.assertNotIn("nothing to run", proc3.stdout)
            lines3 = log3.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines3), 2)
            self.assertIn("discover -s scripts", lines3[0])
            self.assertIn("discover -s skills/my_skill", lines3[1])

            # Case 4: a failing skills run gives nonzero
            log4 = temp / "log4.txt"
            env4 = os.environ.copy()
            env4["JOB_PYTHON"] = str(fake_python)
            env4["FAKE_PYTHON_LOG"] = str(log4)
            env4["FAKE_PYTHON_FAIL"] = "skills"
            proc4 = subprocess.run(
                ["bash", "-e", "-c", step_script],
                cwd=temp,
                env=env4,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(proc4.returncode, 0)
            lines4 = log4.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines4), 2)
            self.assertIn("discover -s scripts", lines4[0])
            self.assertIn("discover -s skills/my_skill", lines4[1])


if __name__ == "__main__":
    unittest.main()
