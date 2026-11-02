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
MAC_REQUIRED = {
    "test_chat_tui_pty.ChatTUIPTYTests",
    "test_chat_tui_theme_pty.ThemePTYTests",
    "test_cli_lifecycle_state.LifecycleStateTests",
    "test_cli_provider_lifecycle.CLIProviderLifecycleTests",
    "test_codex_cli_provider.CodexCLIProviderTests",
    "test_codex_direct_developer.CodexDirectDeveloperTests",
    "test_opencode_direct_developer.DirectOpenCodeDeveloperTests",
    "test_review_authority_native.NativeProviderTests",
}


class MacTestIdSelectionTests(unittest.TestCase):
    def test_explicit_manifest_selects_both_darwin_only_classes(self):
        self.assertEqual(ci_mac_modules.required_test_ids(REPO_ROOT), MAC_REQUIRED)
        self.assertIsNone(ci_mac_modules.select_test_ids([], REPO_ROOT))
        report = ci_mac_modules.selection_report([], REPO_ROOT)
        self.assertEqual(report["level"], "L3")
        self.assertEqual(report["test_ids"], ["FULL"])
        self.assertEqual(report["selection_reason"], "no_changed_paths_requires_full")

    def test_manifest_rejects_malformed_duplicate_and_missing_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "mac-modules.txt"
            cases = (
                ("not-a-test-id\n", "manifest_invalid_test_id"),
                ("test_missing_case.NoSuchCase\n", "manifest_invalid_test_id"),
                ("test_chat_tui_pty.ChatTUIPTYTests\ntest_chat_tui_pty.ChatTUIPTYTests\n",
                 "manifest_empty_or_duplicate"),
                ("# empty manifest\n", "manifest_empty_or_duplicate"),
            )
            with patch.object(ci_mac_modules, "MODULE_LIST", manifest):
                for contents, reason in cases:
                    with self.subTest(contents=contents):
                        manifest.write_text(contents, encoding="utf-8")
                        self.assertIsNone(ci_mac_modules.select_test_ids([], REPO_ROOT))
                        report = ci_mac_modules.selection_report([], REPO_ROOT)
                        self.assertEqual(report["level"], "L3")
                        self.assertEqual(report["test_ids"], ["FULL"])
                        self.assertEqual(report["selection_reason"], "invalid_manifest_requires_full")

    def test_shared_impact_map_selects_route_and_submission_test_modules(self):
        agy = ci_mac_modules.selection_report(
            ["tools/llmtool/llmtool/developer_routes/agy.py"], REPO_ROOT
        )
        self.assertEqual(agy["level"], "L2")
        self.assertIn("test_developer_route_agy", agy["test_ids"])
        self.assertIn("test_task_submission_cli", agy["test_ids"])
        route = ci_mac_modules.selection_report(
            ["tools/llmtool/developer-routes.json"], REPO_ROOT
        )
        self.assertEqual(route["level"], "L1")
        self.assertIn("test_developer_route_agy", route["test_ids"])
        self.assertNotIn("test_task_submission_cli", route["test_ids"])

    def test_unmapped_llmtool_modules_select_direct_and_importing_tests(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_root = root / "tools/llmtool/llmtool"
            (source_root / "developer_routes").mkdir(parents=True)
            (source_root / "task_advance.py").write_text("# source\n", encoding="utf-8")
            (source_root / "developer_routes/base.py").write_text("# source\n", encoding="utf-8")
            tests = root / "tools/llmtool/tests"
            tests.mkdir(parents=True)
            test_sources = {
                "test_chat_tui_pty.py": "import unittest\nclass ChatTUIPTYTests(unittest.TestCase): pass\n",
                "test_review_authority_native.py": "import unittest\nclass NativeProviderTests(unittest.TestCase): pass\n",
                "test_task_advance.py": "import unittest\nfrom llmtool.task_advance import advance\nclass AdvanceTests(unittest.TestCase): pass\n",
                "test_task_consumer.py": "import unittest\nfrom llmtool.developer_routes import base\nclass ConsumerTests(unittest.TestCase): pass\n",
            }
            for name, contents in test_sources.items():
                (tests / name).write_text(contents, encoding="utf-8")
            manifest = root / "tools/llmtool/ci/mac-modules.txt"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                "test_chat_tui_pty.ChatTUIPTYTests\n"
                "test_review_authority_native.NativeProviderTests\n",
                encoding="utf-8",
            )
            with patch.object(ci_mac_modules, "MODULE_LIST", manifest):
                advance = ci_mac_modules.selection_report(
                    ["tools/llmtool/llmtool/task_advance.py"], root
                )
                route_base = ci_mac_modules.selection_report(
                    ["tools/llmtool/llmtool/developer_routes/base.py"], root
                )
            self.assertEqual(advance["level"], "L1")
            self.assertEqual(set(advance["test_ids"]), {
                "test_chat_tui_pty.ChatTUIPTYTests",
                "test_review_authority_native.NativeProviderTests",
                "test_task_advance",
            })
            self.assertEqual(route_base["level"], "L1")
            self.assertIn("test_task_consumer", route_base["test_ids"])
            self.assertIn("test_chat_tui_pty.ChatTUIPTYTests", route_base["test_ids"])

    def test_runtime_sources_select_direct_importing_and_mac_tests(self):
        cases = (
            ("tools/llmtool/llmtool/task_advance.py", "test_task_advance", ()),
            ("tools/llmtool/llmtool/review_repair.py", "test_review_repair", ()),
            ("tools/llmtool/llmtool/developer_routes/base.py",
             "test_developer_route_contract", ("test_developer_route_agy",)),
            ("tools/llmtool/llmtool/task_submission_cli.py",
             "test_task_submission_cli", ("test_task_completion",)),
        )
        for path, direct_id, importer_ids in cases:
            with self.subTest(path=path):
                report = ci_mac_modules.selection_report([path], REPO_ROOT)
                self.assertNotEqual(report["level"], "L3")
                self.assertIn(direct_id, report["test_ids"])
                self.assertTrue(MAC_REQUIRED <= set(report["test_ids"]))
                for importer_id in importer_ids:
                    self.assertIn(importer_id, report["test_ids"])

    def test_runtime_import_scan_failure_falls_back_to_full(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "tools/llmtool/llmtool/task_advance.py"
            source.parent.mkdir(parents=True)
            source.write_text("# source\n", encoding="utf-8")
            tests = root / "tools/llmtool/tests"
            tests.mkdir(parents=True)
            (tests / "test_chat_tui_pty.py").write_text(
                "import unittest\nclass ChatTUIPTYTests(unittest.TestCase): pass\n",
                encoding="utf-8",
            )
            (tests / "test_review_authority_native.py").write_text(
                "import unittest\nclass NativeProviderTests(unittest.TestCase): pass\n",
                encoding="utf-8",
            )
            (tests / "test_broken.py").write_text("from llmtool.task_advance import (\n",
                                                  encoding="utf-8")
            manifest = root / "tools/llmtool/ci/mac-modules.txt"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                "test_chat_tui_pty.ChatTUIPTYTests\n"
                "test_review_authority_native.NativeProviderTests\n",
                encoding="utf-8",
            )
            with patch.object(ci_mac_modules, "MODULE_LIST", manifest):
                report = ci_mac_modules.selection_report(
                    ["tools/llmtool/llmtool/task_advance.py"], root
                )
            self.assertEqual(report["level"], "L3")
            self.assertEqual(report["test_ids"], ["FULL"])

    def test_directly_changed_test_selects_its_whole_module(self):
        report = ci_mac_modules.selection_report(
            ["tools/llmtool/tests/test_developer_route_agy.py"], REPO_ROOT
        )
        self.assertEqual(report["level"], "L1")
        self.assertIn("test_developer_route_agy", report["test_ids"])
        self.assertTrue(MAC_REQUIRED <= set(report["test_ids"]))

    def test_deleted_test_unknown_source_dependencies_and_high_risk_stay_full(self):
        for path in (
            "tools/llmtool/tests/test_deleted.py",
            "tools/llmtool/llmtool/unknown_config.py",
            "tools/llmtool/llmtool/nested/unknown.json",
            "tools/llmtool/requirements-ci.txt",
            "tools/llmtool/llmtool/providers/agy.py",
            "tools/llmtool/llmtool/authorization_policy.py",
            ".github/workflows/llmtool-mac-ci.yml",
            "tools/mac-runner/setup.sh",
            "tools/llmtool/ci/mac-modules.txt",
        ):
            with self.subTest(path=path):
                report = ci_mac_modules.selection_report([path], REPO_ROOT)
                self.assertEqual(report["level"], "L3")
                self.assertEqual(report["test_ids"], ["FULL"])

    def test_selector_changes_require_full(self):
        for path in (
            "tools/llmtool/scripts/ci_mac_modules.py",
            "tools/llmtool/llmtool/test_selection.py",
        ):
            with self.subTest(path=path):
                report = ci_mac_modules.selection_report([path], REPO_ROOT)
                self.assertEqual(report["level"], "L3")
                self.assertEqual(report["test_ids"], ["FULL"])
                self.assertEqual(report["selection_reason"], "selector_change_requires_full")

    def test_monitor_and_documentation_changes_select_no_subset(self):
        for path in ("scripts/llmtool_monitor.py", "tools/llmtool/docs/guide.md"):
            with self.subTest(path=path):
                report = ci_mac_modules.selection_report([path], REPO_ROOT)
                self.assertEqual(report["level"], "L0")
                self.assertEqual(report["test_ids"], [])

    def test_missing_mapped_test_is_full(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "tools/llmtool/llmtool/developer_routes/agy.py"
            source.parent.mkdir(parents=True)
            source.write_text("# source fixture\n", encoding="utf-8")
            tests = root / "tools/llmtool/tests"
            tests.mkdir(parents=True)
            (tests / "test_chat_tui_pty.py").write_text(
                "import unittest\nclass ChatTUIPTYTests(unittest.TestCase): pass\n",
                encoding="utf-8",
            )
            (tests / "test_review_authority_native.py").write_text(
                "import unittest\nclass NativeProviderTests(unittest.TestCase): pass\n",
                encoding="utf-8",
            )
            manifest = root / "tools/llmtool/ci/mac-modules.txt"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                "test_chat_tui_pty.ChatTUIPTYTests\n"
                "test_review_authority_native.NativeProviderTests\n",
                encoding="utf-8",
            )
            with patch.object(ci_mac_modules, "MODULE_LIST", manifest):
                report = ci_mac_modules.selection_report(
                    ["tools/llmtool/llmtool/developer_routes/agy.py"], root
                )
            self.assertEqual(report["level"], "L3")
            self.assertEqual(report["test_ids"], ["FULL"])
            self.assertEqual(report["selection_reason"], "deleted_mapped_test_requires_full")

    def test_cli_json_and_text_output_are_deterministic(self):
        with tempfile.TemporaryDirectory() as temporary:
            changed = Path(temporary) / "changed-paths"
            changed.write_bytes(b"tools/llmtool/developer-routes.json\0")
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(ci_mac_modules.main([
                    "--changed-paths", str(changed), "--json-report"
                ]), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report["level"], "L1")
            self.assertIn("test_developer_route_agy", report["test_ids"])
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(ci_mac_modules.main(["--changed-paths", str(changed)]), 0)
            self.assertEqual(output.getvalue().splitlines(), sorted(report["test_ids"]))

    def test_workflow_runs_exact_ids_streams_output_and_keeps_full_gates(self):
        workflow = (REPO_ROOT / ".github" / "workflows" / "llmtool-mac-ci.yml").read_text()
        self.assertIn("name: check (macOS 3.13)", workflow)
        self.assertIn("mode=full", workflow)
        self.assertIn('make -C tools/llmtool check PYTHON="$JOB_PYTHON"', workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertIn("python3 -I -B tools/llmtool/scripts/ci_mac_modules.py", workflow)
        self.assertIn('"test_ids"', workflow)
        self.assertIn('"$test_id" 2>&1 | tee "$output"', workflow)
        self.assertIn("while IFS= read -r test_id <&3; do", workflow)
        self.assertIn('done 3< "$RUNNER_TEMP/mac-test-ids" < /dev/null', workflow)
        self.assertIn("status=${PIPESTATUS[0]}", workflow)
        self.assertIn("canonical exact HEAD:", workflow)
        self.assertIn("canonical test ID elapsed seconds:", workflow)
        self.assertIn("canonical unittest test-case count:", workflow)
        self.assertIn("verify=true", workflow)


if __name__ == "__main__":
    unittest.main()
