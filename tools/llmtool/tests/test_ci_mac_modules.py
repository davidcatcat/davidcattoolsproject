from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import ci_mac_modules  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[3]
MONITOR_ONLY = ["scripts/llmtool_monitor.py", "scripts/test_llmtool_monitor.py"]


class MacModuleSelectionTests(unittest.TestCase):
    def test_runtime_scan_finds_synthetic_macos_module(self):
        with tempfile.TemporaryDirectory() as temporary:
            tests = Path(temporary) / "tools" / "llmtool" / "tests"
            tests.mkdir(parents=True)
            (tests / "test_synthetic_mac.py").write_text("import sys\nassert sys.platform == 'darwin'\n")
            (tests / "test_unrelated.py").write_text("def test_plain(): pass\n")
            self.assertEqual(ci_mac_modules.scanned_test_modules(Path(temporary)), {"test_synthetic_mac"})

    def test_default_root_main_finds_real_baseline(self):
        self.assertEqual(ci_mac_modules.DEFAULT_REPO_ROOT, REPO_ROOT)
        self.assertIn("test_review_authority", ci_mac_modules.scanned_test_modules())
        with tempfile.TemporaryDirectory() as temporary:
            changed_paths = Path(temporary) / "changed-paths"
            changed_paths.write_text("scripts/llmtool_monitor.py\n")
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(ci_mac_modules.main(["--changed-paths", str(changed_paths)]), 0)
            self.assertEqual(output.getvalue(), "NONE\n")

    def test_extra_modules_are_included(self):
        with tempfile.TemporaryDirectory() as temporary:
            extras = Path(temporary) / "mac-modules.txt"
            extras.write_text("# comment\ntest_extra\n")
            with patch.object(ci_mac_modules, "MODULE_LIST", extras):
                self.assertEqual(ci_mac_modules.extra_modules(), {"test_extra"})
                selected = ci_mac_modules.select_modules(
                    ["tools/llmtool/llmtool/task_store.py"], REPO_ROOT
                )
                self.assertIn("test_extra", selected)

    def test_monitor_only_paths_keep_baseline(self):
        self.assertEqual(
            ci_mac_modules.select_modules(MONITOR_ONLY, REPO_ROOT),
            set(),
        )

    def test_single_source_change_adds_matching_test_modules(self):
        selected = ci_mac_modules.select_modules(["tools/llmtool/scripts/ci_mac_modules.py"], REPO_ROOT)
        self.assertEqual(selected, ci_mac_modules.scanned_test_modules(REPO_ROOT) | ci_mac_modules.extra_modules())

    def test_pr_712_agy_paths_select_l2_baseline_and_real_tests(self):
        report = ci_mac_modules.selection_report(
            [
                "tools/llmtool/llmtool/developer_routes/agy.py",
                "tools/llmtool/llmtool/task_submission_cli.py",
            ], REPO_ROOT
        )
        self.assertEqual(report["level"], "L2")
        self.assertIn("test_developer_route_agy", report["modules"])
        self.assertIn("test_task_submission_cli", report["modules"])
        self.assertTrue(
            ci_mac_modules.scanned_test_modules(REPO_ROOT) | ci_mac_modules.extra_modules()
            <= set(report["modules"])
        )

    def test_pr_713_route_json_selects_l1_parser_default_test(self):
        report = ci_mac_modules.selection_report(
            ["tools/llmtool/developer-routes.json"], REPO_ROOT
        )
        self.assertEqual(report["level"], "L1")
        self.assertIn("test_developer_route_agy", report["modules"])
        self.assertIn("test_task_submission_cli", report["modules"])

    def test_selection_report_records_level_paths_modules_and_fallback(self):
        report = ci_mac_modules.selection_report(
            ["tools/llmtool/developer-routes.json"], REPO_ROOT
        )
        self.assertEqual(report["changed_paths"], ["tools/llmtool/developer-routes.json"])
        self.assertEqual(report["level"], "L1")
        self.assertIn("test_developer_route_agy", report["modules"])
        self.assertEqual(report["module_count"], len(report["modules"]))
        self.assertFalse(report["fallback_reasons"])

    def test_unknown_config_dependency_and_high_risk_paths_stay_full(self):
        for path in (
            "tools/llmtool/llmtool/unknown_config.py",
            "tools/llmtool/unknown_config.json",
            "tools/llmtool/llmtool/nested/unknown.py",
            "tools/llmtool/llmtool/nested/unknown.json",
            "tools/llmtool/requirements-ci.txt",
            "tools/llmtool/llmtool/providers/agy.py",
        ):
            with self.subTest(path=path):
                self.assertIsNone(ci_mac_modules.select_modules([path], REPO_ROOT))
                self.assertEqual(
                    ci_mac_modules.selection_report([path], REPO_ROOT)["level"], "L3"
                )

    def test_missing_mapped_test_keeps_selection_full(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "tools/llmtool/llmtool/developer_routes/agy.py"
            source.parent.mkdir(parents=True)
            source.write_text("# source fixture\n")
            self.assertIsNone(ci_mac_modules.select_modules(
                ["tools/llmtool/llmtool/developer_routes/agy.py"], root
            ))
            report = ci_mac_modules.selection_report(
                ["tools/llmtool/llmtool/developer_routes/agy.py"], root
            )
            self.assertEqual(report["level"], "L3")
            self.assertEqual(report["modules"], ["FULL"])
            self.assertEqual(
                report["selection_reason"], "deleted_mapped_test_requires_full"
            )

    def test_existing_unmapped_flat_python_and_json_sources_keep_selection_full(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_dir = root / "tools/llmtool/llmtool"
            source_dir.mkdir(parents=True)
            paths = (
                "tools/llmtool/llmtool/unmapped_fixture.py",
                "tools/llmtool/llmtool/unmapped_fixture.json",
            )
            (source_dir / "unmapped_fixture.py").write_text("VALUE = 1\n")
            (source_dir / "unmapped_fixture.json").write_text("{}\n")

            for path in paths:
                with self.subTest(path=path):
                    self.assertTrue((root / path).is_file())
                    self.assertIsNone(ci_mac_modules.select_modules([path], root))
                    report = ci_mac_modules.selection_report([path], root)
                    self.assertEqual(report["level"], "L3")
                    self.assertEqual(report["modules"], ["FULL"])
                    self.assertEqual(
                        report["selection_reason"],
                        "unmapped_or_nested_source_requires_full",
                    )

    def test_test_only_change_adds_changed_module(self):
        selected = ci_mac_modules.select_modules(["tools/llmtool/tests/test_ci_mac_modules.py"], REPO_ROOT)
        self.assertEqual(selected, ci_mac_modules.scanned_test_modules(REPO_ROOT) | ci_mac_modules.extra_modules() | {"test_ci_mac_modules"})

    def test_deleted_test_change_requires_full(self):
        self.assertIsNone(ci_mac_modules.select_modules(["tools/llmtool/tests/test_deleted.py"], REPO_ROOT))

    def test_init_and_workflow_changes_require_full(self):
        self.assertIsNone(ci_mac_modules.select_modules(["tools/llmtool/llmtool/__init__.py"], REPO_ROOT))
        self.assertIsNone(ci_mac_modules.select_modules([".github/workflows/llmtool-mac-ci.yml"], REPO_ROOT))

    def test_docs_only_change_selects_nothing(self):
        self.assertEqual(
            ci_mac_modules.select_modules(["tools/llmtool/docs/guide.md"], REPO_ROOT),
            set(),
        )

    def test_empty_mac_baseline_after_source_change_requires_full(self):
        with patch.object(ci_mac_modules, "scanned_test_modules", return_value=set()), \
                patch.object(ci_mac_modules, "extra_modules", return_value=set()):
            self.assertIsNone(
                ci_mac_modules.select_modules(["tools/llmtool/llmtool/source.py"], REPO_ROOT)
            )

    def test_mac_relevant_test_change_is_selected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tests = root / "tools" / "llmtool" / "tests"
            tests.mkdir(parents=True)
            (tests / "test_changed_mac.py").write_text("import sys\nassert sys.platform == 'darwin'\n")
            with patch.object(ci_mac_modules, "scanned_test_modules", return_value={"test_baseline"}), \
                    patch.object(ci_mac_modules, "extra_modules", return_value={"test_extra"}):
                self.assertEqual(
                    ci_mac_modules.select_modules(
                        ["tools/llmtool/tests/test_changed_mac.py"], root
                    ),
                    {"test_baseline", "test_extra", "test_changed_mac"},
                )

    def test_non_mac_module_change_selects_only_mac_relevant_set(self):
        with patch.object(ci_mac_modules, "scanned_test_modules", return_value={"test_mac"}), \
                patch.object(ci_mac_modules, "extra_modules", return_value={"test_extra"}):
            self.assertEqual(
                ci_mac_modules.select_modules(["tools/llmtool/llmtool/task_store.py"], REPO_ROOT),
                {"test_mac", "test_extra", "test_task_store"},
            )

    def test_empty_selection_prints_full(self):
        with tempfile.TemporaryDirectory() as temporary:
            changed_paths = Path(temporary) / "changed-paths"
            changed_paths.write_text("tools/llmtool/tests/test_missing.py\n")
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(ci_mac_modules.main(["--changed-paths", str(changed_paths)]), 0)
            self.assertEqual(output.getvalue(), "FULL\n")

    def test_json_report_cli_emits_selection_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            changed_paths = Path(temporary) / "changed-paths"
            changed_paths.write_text("tools/llmtool/llmtool/developer_routes/agy.py\n")
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    ci_mac_modules.main(
                        ["--changed-paths", str(changed_paths), "--json-report"]
                    ),
                    0,
                )
            self.assertIn('"level": "L2"', output.getvalue())
            self.assertIn('"module_count"', output.getvalue())

    def test_full_fallback_report_is_conservative_and_observable(self):
        report = ci_mac_modules.full_fallback_report(
            ["tools/llmtool/developer-routes.json"], "selector_execution_failed"
        )
        self.assertEqual(report["level"], "L3")
        self.assertEqual(report["modules"], ["FULL"])
        self.assertEqual(report["fallback_reasons"], ["selector_execution_failed"])

    def test_selector_internal_failure_emits_full_json_telemetry(self):
        with tempfile.TemporaryDirectory() as temporary:
            changed_paths = Path(temporary) / "changed-paths"
            changed_paths.write_text("tools/llmtool/developer-routes.json\n")
            output = io.StringIO()
            with patch.object(ci_mac_modules, "selection_report", side_effect=RuntimeError):
                with redirect_stdout(output):
                    self.assertEqual(ci_mac_modules.main([
                        "--changed-paths", str(changed_paths), "--json-report"
                    ]), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report["level"], "L3")
            self.assertEqual(report["modules"], ["FULL"])
            self.assertEqual(report["fallback_reasons"], ["selector_internal_error"])

    def test_workflow_and_makefile_require_full(self):
        for path in (".github/workflows/other.yml", "tools/llmtool/Makefile"):
            with self.subTest(path=path):
                self.assertIsNone(ci_mac_modules.select_modules([path], REPO_ROOT))

    def test_unknown_non_markdown_path_requires_full(self):
        self.assertIsNone(ci_mac_modules.select_modules(["tools/llmtool/docs/unknown.txt"], REPO_ROOT))

    def test_output_is_deterministic(self):
        with tempfile.TemporaryDirectory() as temporary:
            changed_paths = Path(temporary) / "changed-paths"
            changed_paths.write_bytes(b"tools/llmtool/tests/test_z.py\0tools/llmtool/tests/test_a.py\0")
            output = []
            for _ in range(2):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    self.assertEqual(ci_mac_modules.main(["--changed-paths", str(changed_paths)]), 0)
                output.append(buffer.getvalue())
            self.assertEqual(output[0], output[1])
            self.assertEqual(output[0].splitlines(), sorted(output[0].splitlines()))

    def test_workflow_keeps_job_name_and_full_suite(self):
        workflow = (REPO_ROOT / ".github" / "workflows" / "llmtool-mac-ci.yml").read_text()
        self.assertIn("name: check (macOS 3.13)", workflow)
        self.assertIn("mode=full", workflow)
        self.assertIn("make -C tools/llmtool check PYTHON=\"$JOB_PYTHON\"", workflow)
        trigger = workflow.split("on:\n", 1)[1].split("permissions:\n", 1)[0]
        self.assertIn("pull_request:", trigger)
        self.assertIn("workflow_dispatch:", trigger)
        self.assertNotIn("schedule:", trigger)
        self.assertNotIn("cron:", trigger)
        self.assertNotIn("push:", trigger)
        self.assertIn("python3 -I -B tools/llmtool/scripts/ci_mac_modules.py", workflow)
        self.assertIn("--json-report", workflow)
        self.assertIn("selector_execution_failed", workflow)
        self.assertIn("selector_report_invalid", workflow)
        self.assertIn("selection level: L3", workflow)
        self.assertIn("mode=docs-only", workflow)
        self.assertIn("verify=true", workflow)
        self.assertIn("steps.coverage.outputs.verify == 'true'", workflow)

    def test_subset_unittest_invocations_do_not_inherit_module_list_stdin(self):
        workflow = (REPO_ROOT / ".github" / "workflows" / "llmtool-mac-ci.yml").read_text()
        loop = workflow.split('while IFS= read -r module; do\n', 1)[1].split(
            'done < "$RUNNER_TEMP/mac-modules"', 1
        )[0]
        invocation = next(line for line in loop.splitlines() if "-m unittest discover" in line)
        self.assertIn("< /dev/null", invocation)


if __name__ == "__main__":
    unittest.main()
