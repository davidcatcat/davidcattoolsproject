"""Focused tests for deterministic TEST-1 plan selection."""

import os
import subprocess
import sys
import unittest
from pathlib import Path

from llmtool.test_selection import (
    IMPACT_MAP_VERSION,
    select_test_plan,
)


SOURCE = "tools/llmtool/llmtool/test_selection.py"
L1_TEST = "tools/llmtool/tests/test_test_selection.py"
DEPENDENCY_TEST = "tools/llmtool/tests/test_task_store.py"


class TestSelectionTests(unittest.TestCase):
    def test_l0_is_always_present_and_l1_is_exactly_mapped(self):
        plan = select_test_plan([SOURCE])

        self.assertEqual(plan["schema_version"], 1)
        self.assertEqual(plan["impact_map_version"], IMPACT_MAP_VERSION)
        self.assertEqual([level["level"] for level in plan["levels"]], ["L0", "L1"])
        self.assertEqual(plan["levels"][0]["command"], ["make", "syntax"])
        self.assertEqual(plan["levels"][1]["tests"], [L1_TEST])
        self.assertEqual(plan["levels"][1]["commands"], [{
            "argv": ["python3", "-c", "import sys; sys.path.insert(0, 'tests'); "
                     "import unittest; unittest.main(module='test_test_selection')"],
            "cwd": "tools/llmtool",
        }])
        self.assertEqual(plan["required_gates"], ["exact_head_ci", "security", "mac_native"])
        self.assertFalse(plan["executes_tests"])
        self.assertFalse(plan["pipeline_integrated"])

    def test_pr_712_and_713_agy_paths_use_shared_impact_map(self):
        cases = {
            "tools/llmtool/llmtool/developer_routes/agy.py": [
                "tools/llmtool/tests/test_developer_route_agy.py",
                "tools/llmtool/tests/test_task_submission_cli.py",
            ],
            "tools/llmtool/llmtool/task_submission_cli.py": [
                "tools/llmtool/tests/test_developer_route_agy.py",
                "tools/llmtool/tests/test_task_submission_cli.py",
            ],
            "tools/llmtool/developer-routes.json": [
                "tools/llmtool/tests/test_developer_route_agy.py",
            ],
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                plan = select_test_plan([source])
                self.assertEqual([level["level"] for level in plan["levels"]], ["L0", "L1"])
                self.assertEqual(plan["levels"][1]["tests"], sorted(expected))

    def test_unknown_config_dependency_and_deleted_test_keep_full_fallback(self):
        for path in (
            "tools/llmtool/llmtool/unknown_config.py",
            "tools/llmtool/nested/unknown.py",
            "tools/llmtool/nested/unknown.json",
            "tools/llmtool/requirements-ci.txt",
            "tools/llmtool/tests/test_deleted_agy.py",
            ".github/workflows/llmtool-mac-ci.yml",
        ):
            with self.subTest(path=path):
                plan = select_test_plan([path])
                self.assertEqual([level["level"] for level in plan["levels"]], ["L0", "L3"])
                self.assertIn("unmapped_path", plan["fallback_reasons"])

    def test_emitted_l1_command_runs_only_the_selected_module(self):
        if os.environ.get("LLMTOOL_TEST_SELECTION_COMMAND_CHILD"):
            self.skipTest("avoid recursively launching the command smoke test")
        command = select_test_plan([SOURCE])["levels"][1]["commands"][0]
        self.assertEqual(command["argv"][0], "python3")
        self.assertEqual(command["argv"][1], "-c")
        self.assertIn("unittest.main(module='test_test_selection')", command["argv"][2])
        self.assertNotIn("discover", command["argv"])
        self.assertFalse(any(char in command["argv"][2] for char in "*?[]"))
        environment = os.environ.copy()
        environment["LLMTOOL_TEST_SELECTION_COMMAND_CHILD"] = "1"
        result = subprocess.run(
            [sys.executable, *command["argv"][1:]],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_explicit_dependency_tests_add_l2_deterministically(self):
        plan = select_test_plan(
            [SOURCE],
            dependency_regression_tests=[DEPENDENCY_TEST, L1_TEST],
        )

        self.assertEqual([level["level"] for level in plan["levels"]], ["L0", "L1", "L2"])
        self.assertEqual(plan["levels"][2]["tests"], sorted([L1_TEST, DEPENDENCY_TEST]))
        self.assertEqual(plan, select_test_plan(
            [SOURCE], dependency_regression_tests=[L1_TEST, DEPENDENCY_TEST]))

    def test_unmapped_changed_path_falls_back_to_l3(self):
        plan = select_test_plan(["tools/llmtool/llmtool/new_module.py"])

        self.assertEqual([level["level"] for level in plan["levels"]], ["L0", "L3"])
        self.assertIn("unmapped_path", plan["fallback_reasons"])
        self.assertEqual(plan["levels"][1]["command"], ["make", "check"])

    def test_stale_map_falls_back_to_l3_even_for_mapped_path(self):
        plan = select_test_plan([SOURCE], impact_map_version=IMPACT_MAP_VERSION + 1)

        self.assertEqual([level["level"] for level in plan["levels"]], ["L0", "L3"])
        self.assertIn("stale_impact_map", plan["fallback_reasons"])

    def test_mapped_high_risk_files_still_fall_back_to_l3(self):
        for path in (
            "tools/llmtool/llmtool/providers/openai.py",
            "tools/llmtool/llmtool/authorization_policy.py",
            "tools/llmtool/llmtool/authorization_ledger.py",
            "tools/llmtool/llmtool/git_intel.py",
            "tools/llmtool/llmtool/opencode_worker.py",
            "tools/llmtool/llmtool/opencode_foreground_host.py",
            "tools/llmtool/llmtool/opencode_direct_developer.py",
            "tools/llmtool/llmtool/codex_direct_developer.py",
            "tools/llmtool/llmtool/opencode_native_credential.py",
            "tools/llmtool/llmtool/review_authority.py",
            "tools/llmtool/llmtool/review_authority_cli.py",
            "tools/llmtool/llmtool/test_output_excerpts.py",
        ):
            with self.subTest(path=path):
                plan = select_test_plan(
                    [path],
                    impact_test_map={path: [L1_TEST]},
                    impact_map_version=IMPACT_MAP_VERSION,
                )

                self.assertEqual([level["level"] for level in plan["levels"]], ["L0", "L3"])
                self.assertIn("high_risk_path", plan["fallback_reasons"])

    def test_default_mapping_high_risk_files_force_l3(self):
        for path in (
            "tools/llmtool/llmtool/test_output_excerpts.py",
            "tools/llmtool/llmtool/opencode_direct_developer.py",
            "tools/llmtool/llmtool/codex_direct_developer.py",
        ):
            with self.subTest(path=path):
                plan = select_test_plan([path])

                self.assertEqual([level["level"] for level in plan["levels"]], ["L0", "L3"])
                self.assertIn("high_risk_path", plan["fallback_reasons"])

    def test_empty_changes_fall_back_to_l3(self):
        plan = select_test_plan([])

        self.assertEqual([level["level"] for level in plan["levels"]], ["L0", "L3"])
        self.assertIn("no_changed_paths", plan["fallback_reasons"])

    def test_invalid_changed_paths_are_rejected(self):
        for path in ("/absolute.py", "C:/absolute.py", "../escape.py",
                     "tools//module.py", "tools\\module.py", "tools/module*.py", ""):
            with self.subTest(path=path), self.assertRaises(ValueError):
                select_test_plan([path])

    def test_invalid_dependency_test_path_is_rejected(self):
        for path in ("tools/llmtool/llmtool/test_selection.py",
                     "tools/llmtool/tests/../test_escape.py",
                     "tools/llmtool/tests/not_a_test.py",
                     "tools/llmtool/tests/test_*.py",
                     "tools/llmtool/tests/test_bad-name.py",
                     "tools/llmtool/tests/nested/test_test_selection.py"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                select_test_plan([SOURCE], dependency_regression_tests=[path])

    def test_invalid_map_paths_are_rejected(self):
        with self.assertRaises(ValueError):
            select_test_plan([SOURCE], impact_test_map={SOURCE: ["../outside.py"]})
        with self.assertRaises(ValueError):
            select_test_plan([SOURCE], impact_test_map={SOURCE: []})
        with self.assertRaises(ValueError):
            select_test_plan([SOURCE], impact_test_map={
                SOURCE: ["tools/llmtool/tests/test_*.py"]
            })
        # A nested duplicate basename is rejected rather than broadening the
        # flat file's exact unittest invocation.
        with self.assertRaises(ValueError):
            select_test_plan([SOURCE], impact_test_map={
                SOURCE: [L1_TEST, "tools/llmtool/tests/nested/test_test_selection.py"]
            })


if __name__ == "__main__":
    unittest.main()
