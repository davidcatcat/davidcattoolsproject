from contextlib import redirect_stdout
import io
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
MAC_TIMINGS = TOOLS_ROOT / "ci" / "mac-test-module-seconds.json"
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

    def test_default_output_is_unchanged_without_timings_flag(self):
        expected_shards = ci_test_shards.partition(3)
        for index, expected in enumerate(expected_shards, start=1):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                ci_test_shards.main(["3", str(index)])
            self.assertEqual(buffer.getvalue(), "".join(f"{name}\n" for name in expected))

    def test_mac_timings_partition_is_disjoint_complete_and_each_shard_under_half_total(self):
        linux_schema = set(json.loads(ci_test_shards.TIMINGS.read_text(encoding="utf-8")).keys())
        mac_data = json.loads(MAC_TIMINGS.read_text(encoding="utf-8"))
        self.assertEqual(set(mac_data.keys()), linux_schema)
        default = mac_data["default_seconds"]
        weights = mac_data["modules"]
        self.assertIsInstance(default, int)
        self.assertGreater(default, 0)
        modules = {path.name for path in ci_test_shards.TESTS.glob("test_*.py")}
        self.assertTrue(set(weights.keys()) <= modules)

        shards = ci_test_shards.partition(3, timings=MAC_TIMINGS)
        self.assertEqual(len(shards), 3)
        self.assertEqual(set(shards[0]) & set(shards[1]), set())
        self.assertEqual(set(shards[0]) & set(shards[2]), set())
        self.assertEqual(set(shards[1]) & set(shards[2]), set())
        self.assertEqual(set(shards[0]) | set(shards[1]) | set(shards[2]), modules)

        for index, expected in enumerate(shards, start=1):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                ci_test_shards.main(["--timings", str(MAC_TIMINGS), "3", str(index)])
            self.assertEqual(buffer.getvalue().splitlines(), expected)

        shard_totals = [
            sum(weights.get(module, default) for module in shard) for shard in shards
        ]
        measured_total = sum(shard_totals)
        for shard_total in shard_totals:
            self.assertLess(shard_total * 2, measured_total)

        explicit_totals = [sum(weights.get(module, 0) for module in shard) for shard in shards]
        explicit_sum = sum(explicit_totals)
        for explicit_total in explicit_totals:
            self.assertLess(explicit_total * 2, explicit_sum)

    def test_mac_workflow_has_single_check_job_and_three_hosted_shards(self):
        workflow = (TOOLS_ROOT.parent.parent / ".github/workflows/llmtool-mac-ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(workflow.count("name: check (macOS 3.13)"), 1)
        self.assertNotIn("self-hosted", workflow)
        self.assertEqual(workflow.count("runs-on: macos-15-intel"), 3)
        self.assertIn("needs: [selection, shards]", workflow)
        self.assertIn("needs.selection.result", workflow)
        self.assertIn("needs.shards.result", workflow)
        self.assertIn('if [ "$SHARDS_RESULT" != success ]; then', workflow)
        self.assertIn("always()", workflow)
        self.assertIn("fail-fast: false", workflow)
        self.assertIn("shard: [1, 2, 3]", workflow)
        self.assertIn(
            'tools/llmtool/scripts/ci_test_shards.py --timings '
            'tools/llmtool/ci/mac-test-module-seconds.json 3 "${{ matrix.shard }}"',
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
