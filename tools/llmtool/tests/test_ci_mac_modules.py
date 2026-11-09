from __future__ import annotations

import ast
from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import ci_mac_modules  # noqa: E402
import ci_path_classes  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[3]
MAC_REQUIRED = {
    "test_chat_tui_pty.ChatTUIPTYTests",
    "test_chat_tui_theme_pty.ThemePTYTests",
    "test_cli_lifecycle_state.LifecycleStateTests",
    "test_cli_lifecycle_state.BoundedWorkerLifecycleTests",
    "test_cli_provider_lifecycle.TUISignalOwnershipTests",
    "test_cli_provider_lifecycle.CLIProviderLifecycleTests",
    "test_codex_cli_provider.CodexCLIProviderTests",
    "test_codex_direct_developer.CodexDirectDeveloperTests",
    "test_opencode_direct_developer.DirectOpenCodeDeveloperTests",
    "test_review_authority_native.NativeProviderTests",
}
KNOWN_DARWIN_REGRESSION_CLASSES = {
    "test_cli_lifecycle_state.BoundedWorkerLifecycleTests",
    "test_chat_tui_pty.ChatTUIPTYTests",
    "test_chat_tui_theme_pty.ThemePTYTests",
    "test_review_authority_native.NativeProviderTests",
}
PR_781_PATHS = (
    "tools/llmtool/llmtool/review_repair.py",
    "tools/llmtool/llmtool/task_advance.py",
    "tools/llmtool/tests/test_review_repair.py",
    "tools/llmtool/tests/test_task_advance.py",
)


class MacTestIdSelectionTests(unittest.TestCase):
    def assert_pr_781_selection(self, report):
        """Check the #781 report against its core set and import-derived extras.

        The extras are derived from the test tree with ``ast`` here, so a new
        test module importing task_advance or review_repair needs no edit.
        """
        targets = {"llmtool.review_repair", "llmtool.task_advance"}
        test_dir = REPO_ROOT / "tools" / "llmtool" / "tests"

        def imports_target(test_path):
            tree = ast.parse(test_path.read_text(encoding="utf-8"), filename=str(test_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = {alias.name for alias in node.names}
                elif (isinstance(node, ast.ImportFrom) and node.level == 0
                      and node.module):
                    names = {node.module} | {
                        f"{node.module}.{alias.name}" for alias in node.names
                    }
                else:
                    continue
                if names & targets:
                    return True
            return False

        self.assertEqual(report["level"], "L1")
        self.assertEqual(report["fallback_reasons"], [])
        self.assertEqual(report["fallback_paths"], [])
        self.assertEqual(report["fallback_details"], [])
        selected = set(report["test_ids"])
        core = MAC_REQUIRED | {
            "test_review_repair", "test_task_advance", "test_task_drive",
            "test_claude_route_wiring",
        }
        self.assertEqual(core - selected, set(), "#781 core test IDs are missing")
        for test_id in sorted(selected - core):
            test_path = test_dir / f"{test_id}.py"
            self.assertTrue(
                test_id.startswith("test_") and test_id.isidentifier()
                and test_path.is_file() and imports_target(test_path),
                f"#781 selected an unrelated test ID: {test_id}",
            )
        for test_path in sorted(test_dir.glob("test_*.py")):
            if imports_target(test_path):
                self.assertIn(test_path.stem, selected,
                              f"#781 missed an importing test module: {test_path.stem}")

    def test_pr_781_fixture_check_rejects_lost_unrelated_and_fallback_edits(self):
        report = ci_mac_modules.selection_report(list(PR_781_PATHS), REPO_ROOT)
        self.assert_pr_781_selection(copy.deepcopy(report))
        self.assertIn("test_task_advance", report["test_ids"])
        self.assertNotIn("test_worker_dispatch", report["test_ids"])
        self.assertTrue((REPO_ROOT / "tools/llmtool/tests/test_worker_dispatch.py").is_file())

        lost = copy.deepcopy(report)
        lost["test_ids"] = [test_id for test_id in lost["test_ids"]
                            if test_id != "test_task_advance"]
        unrelated = copy.deepcopy(report)
        unrelated["test_ids"] = sorted(set(unrelated["test_ids"]) | {"test_worker_dispatch"})
        fallback = copy.deepcopy(report)
        fallback["fallback_reasons"] = ["missing_file"]
        for label, edited in (("lost", lost), ("unrelated", unrelated),
                              ("fallback", fallback)):
            with self.subTest(edit=label):
                with self.assertRaises(AssertionError):
                    self.assert_pr_781_selection(edited)

    def test_explicit_manifest_selects_known_darwin_regression_classes(self):
        selected = ci_mac_modules.required_test_ids(REPO_ROOT)
        self.assertEqual(selected, MAC_REQUIRED)
        self.assertTrue(KNOWN_DARWIN_REGRESSION_CLASSES <= selected)
        self.assertIsNone(ci_mac_modules.select_test_ids([], REPO_ROOT))
        report = ci_mac_modules.selection_report([], REPO_ROOT)
        self.assertEqual(report["level"], "L3")
        self.assertEqual(report["test_ids"], ["FULL"])
        self.assertIn("no_changed_paths", report["fallback_reasons"])

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
                        self.assertIn("invalid_manifest", report["fallback_reasons"])

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

    def test_worker_dispatch_policy_selects_reader_test_and_mac_baseline(self):
        report = ci_mac_modules.selection_report(
            ["tools/llmtool/worker-dispatch.json"], REPO_ROOT
        )

        self.assertEqual(report["level"], "L1")
        self.assertEqual(report["test_ids"], sorted(
            MAC_REQUIRED | {"test_worker_dispatch"}
        ))
        self.assertEqual(report["fallback_reasons"], [])
        self.assertEqual(report["fallback_paths"], [])

    def test_guidance_drift_inputs_select_checker_and_docs_tests(self):
        expected = sorted(MAC_REQUIRED | {
            "test_guidance_drift_check", "test_docs_check",
        })
        paths = (
            "tools/llmtool/guidance-drift.json",
            "tools/llmtool/scripts/guidance_drift_check.py",
        )
        for path in paths:
            with self.subTest(path=path):
                report = ci_mac_modules.selection_report([path], REPO_ROOT)
                self.assertEqual(report["level"], "L1")
                self.assertEqual(report["test_ids"], expected)
                self.assertEqual(report["fallback_reasons"], [])

        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(ci_mac_modules, "required_test_ids", return_value=MAC_REQUIRED):
                deleted = ci_mac_modules.selection_report(
                    [paths[0]], Path(temporary)
                )
        self.assertEqual(deleted["level"], "L3")
        self.assertEqual(deleted["test_ids"], ["FULL"])
        self.assertIn("missing_file", deleted["fallback_reasons"])
        self.assertIn(paths[0], deleted["fallback_paths"])

        for gate in (
            "tools/llmtool/scripts/docs_check.py",
            "tools/llmtool/scripts/claude_bg_dispatch.py",
        ):
            for changed in ([gate], [paths[0], gate]):
                with self.subTest(changed=changed):
                    report = ci_mac_modules.selection_report(changed, REPO_ROOT)
                    self.assertEqual(report["level"], "L3")
                    self.assertEqual(report["test_ids"], ["FULL"])
                    self.assertIn("unmapped_source", report["fallback_reasons"])

        combined = ci_mac_modules.selection_report(
            ["CLAUDE.md", "AGENTS.md", paths[0]], REPO_ROOT
        )
        self.assertEqual(combined["level"], "L1")
        self.assertEqual(combined["test_ids"], expected)
        self.assertEqual(combined["fallback_reasons"], [])
        self.assertEqual(ci_path_classes.classify_path(paths[0]), "runtime")
        self.assertEqual(
            ci_path_classes.linux_flags(b"CLAUDE.md\0AGENTS.md\0" + paths[0].encode()),
            (True, True, False, False),
        )

    def test_deleted_worker_dispatch_policy_still_falls_back_to_full(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(ci_mac_modules, "required_test_ids", return_value=MAC_REQUIRED):
                report = ci_mac_modules.selection_report(
                    ["tools/llmtool/worker-dispatch.json"], root
                )

        self.assertEqual(report["level"], "L3")
        self.assertEqual(report["test_ids"], ["FULL"])
        self.assertIn("missing_file", report["fallback_reasons"])
        self.assertEqual(report["fallback_paths"], ["tools/llmtool/worker-dispatch.json"])

    def test_whole_module_id_removes_its_class_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tests = root / "tools/llmtool/tests"
            tests.mkdir(parents=True)
            (tests / "test_sample.py").write_text(
                "import unittest\n"
                "class SampleTests(unittest.TestCase): pass\n",
                encoding="utf-8",
            )
            manifest = root / "tools/llmtool/ci/mac-modules.txt"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("test_sample.SampleTests\n", encoding="utf-8")
            with patch.object(ci_mac_modules, "MODULE_LIST", manifest):
                report = ci_mac_modules.selection_report(
                    ["tools/llmtool/tests/test_sample.py"], root
                )

        self.assertEqual(report["level"], "L1")
        self.assertEqual(report["test_ids"], ["test_sample"])
        self.assertEqual(report["fallback_reasons"], [])

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
                selected_ids = set(report["test_ids"])
                normalized_mac_required = {
                    test_id.partition(".")[0]
                    if test_id.partition(".")[0] in selected_ids else test_id
                    for test_id in MAC_REQUIRED
                }
                self.assertTrue(normalized_mac_required <= selected_ids)
                for importer_id in importer_ids:
                    self.assertIn(importer_id, report["test_ids"])

    def test_chat_tui_source_selects_subprocess_pty_scenario_suite(self):
        report = ci_mac_modules.selection_report(
            ["tools/llmtool/llmtool/chat_tui.py"], REPO_ROOT
        )
        self.assertNotEqual(report["level"], "L3")
        self.assertIn("test_chat_tui_scenarios", report["test_ids"])
        self.assertTrue(MAC_REQUIRED <= set(report["test_ids"]))

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
                self.assertIn("selector_change", report["fallback_reasons"])

    def test_full_fallback_reports_actual_high_risk_and_unmapped_paths(self):
        cases = (
            ("tools/llmtool/llmtool/opencode_direct_developer.py",
             "high_risk_file"),
        )
        for path, reason in cases:
            with self.subTest(path=path):
                report = ci_mac_modules.selection_report([path], REPO_ROOT)
                self.assertEqual(report["level"], "L3")
                self.assertEqual(report["test_ids"], ["FULL"])
                self.assertIn(reason, report["fallback_reasons"])
                self.assertIn(path, report["fallback_paths"])
                self.assertIn({"path": path, "reason": reason}, report["fallback_details"])
                self.assertRegex(report["selector_revision"], r"^[0-9a-f]{64}$")

    def test_historical_pr_path_fixtures_preserve_selection_and_rejection_reasons(self):
        fixtures = (
            # #781 ids are checked by assert_pr_781_selection, not a frozen list.
            ("#781", list(PR_781_PATHS), "L1", None, None),
            (
                "#782",
                [
                    "tools/llmtool/llmtool/opencode_direct_developer.py",
                    "tools/llmtool/llmtool/task_store.py",
                    "tools/llmtool/llmtool/task_submission_cli.py",
                    "tools/llmtool/tests/test_task_store.py",
                    "tools/llmtool/tests/test_task_submission_cli.py",
                ],
                "L3",
                {"FULL"},
                ("tools/llmtool/llmtool/opencode_direct_developer.py", "high_risk_file"),
            ),
            (
                "#787",
                [
                    "tools/llmtool/llmtool/chat_tui.py",
                    "tools/llmtool/llmtool/chat_tui_status.py",
                    "tools/llmtool/tests/test_chat_tui.py",
                ],
                "L1",
                MAC_REQUIRED | {
                    "test_chat_tui", "test_chat_tui_scenarios",
                    "test_chat_tui_theme", "test_chat_tui_layout", "test_chat_tui_pm",
                    "test_chat_tui_source_revision",
                },
                None,
            ),
            (
                "#789",
                [
                    "README.md",
                    "tools/llmtool/llmtool/config.py",
                    "tools/llmtool/llmtool/role_config.py",
                    "tools/llmtool/tests/test_role_config.py",
                ],
                "L3",
                {"FULL"},
                ("README.md", "unsupported_path"),
            ),
            (
                "#790",
                ["skills/ai-team-delivery/pm-continuation.md"],
                "L0",
                set(),
                None,
            ),
            (
                "#791",
                [
                    "tools/llmtool/worker-dispatch.json",
                    "tools/llmtool/tests/test_worker_dispatch.py",
                ],
                "L1",
                MAC_REQUIRED | {"test_worker_dispatch"},
                None,
            ),
            (
                "#792",
                [
                    ".github/workflows/llmtool-linux-ci.yml",
                    "tools/ai-loop/README.md",
                ],
                "L3",
                None,
                (".github/workflows/llmtool-linux-ci.yml", "workflow_change"),
            ),
        )
        for label, paths, level, expected_ids, expected_reason in fixtures:
            with self.subTest(pr=label):
                report = ci_mac_modules.selection_report(paths, REPO_ROOT)
                self.assertEqual(report["level"], level)
                if expected_ids is not None:
                    self.assertEqual(set(report["test_ids"]), expected_ids)
                if expected_reason is not None:
                    path, reason = expected_reason
                    self.assertIn(reason, report["fallback_reasons"])
                    self.assertIn(path, report["fallback_paths"])
                    self.assertIn({"path": path, "reason": reason}, report["fallback_details"])
                if level == "L3":
                    self.assertEqual(report["test_ids"], ["FULL"])
                    self.assertTrue(all(":" not in reason
                                        for reason in report["fallback_reasons"]))
                    self.assertRegex(report["selector_revision"], r"^[0-9a-f]{64}$")
                elif label == "#781":
                    self.assert_pr_781_selection(report)
                elif label == "#791":
                    self.assertEqual(report["fallback_reasons"], [])
                    self.assertEqual(report["fallback_paths"], [])

    def test_readme_and_candidate_only_role_config_are_evaluated_in_candidate_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            llmtool = root / "tools/llmtool/llmtool"
            llmtool.mkdir(parents=True)
            (root / "README.md").write_text("# candidate README\n", encoding="utf-8")
            (llmtool / "config.py").write_text("# candidate config\n", encoding="utf-8")
            (llmtool / "role_config.py").write_text("# candidate role config\n", encoding="utf-8")
            tests = root / "tools/llmtool/tests"
            tests.mkdir(parents=True)
            (tests / "test_role_config.py").write_text(
                "import unittest\n"
                "from llmtool import config, role_config\n"
                "class RoleConfigTests(unittest.TestCase): pass\n",
                encoding="utf-8",
            )
            manifest = root / "tools/llmtool/ci/mac-modules.txt"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("test_role_config.RoleConfigTests\n", encoding="utf-8")
            with patch.object(ci_mac_modules, "MODULE_LIST", manifest):
                report = ci_mac_modules.selection_report(
                    [
                        "tools/llmtool/llmtool/config.py",
                        "tools/llmtool/llmtool/role_config.py",
                        "tools/llmtool/tests/test_role_config.py",
                    ],
                    root,
                )
        self.assertEqual(report["level"], "L1")
        self.assertEqual(report["test_ids"], ["test_role_config"])
        self.assertEqual(report["fallback_reasons"], [])
        self.assertRegex(report["selector_revision"], r"^[0-9a-f]{64}$")

    def test_candidate_only_source_and_test_are_read_from_candidate_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "tools/llmtool/llmtool/candidate_only.py"
            source.parent.mkdir(parents=True)
            source.write_text("# candidate-only source\n", encoding="utf-8")
            tests = root / "tools/llmtool/tests"
            tests.mkdir(parents=True)
            (tests / "test_candidate_only.py").write_text(
                "import unittest\nclass CandidateOnlyTests(unittest.TestCase): pass\n",
                encoding="utf-8",
            )
            manifest = root / "tools/llmtool/ci/mac-modules.txt"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("test_candidate_only.CandidateOnlyTests\n", encoding="utf-8")
            report = ci_mac_modules.selection_report(
                ["tools/llmtool/llmtool/candidate_only.py"], root
            )
        self.assertEqual(report["level"], "L1")
        self.assertIn("test_candidate_only", report["test_ids"])
        self.assertRegex(report["selector_revision"], r"^[0-9a-f]{64}$")

    def test_monitor_and_documentation_changes_select_no_subset(self):
        for path in (
            "scripts/llmtool_monitor.py",
            "scripts/test_llmtool_monitor_github_reads.py",
            "tools/llmtool/docs/guide.md",
            "AGENTS.md",
            "CLAUDE.md",
            "skills/ai-team-delivery/pm-continuation.md",
            "docs/meetings/2026-11-02-llmtool-governance-and-ai-team-decisions.md",
            "tools/llmtool/README.md",
        ):
            with self.subTest(path=path):
                report = ci_mac_modules.selection_report([path], REPO_ROOT)
                self.assertEqual(report["level"], "L0")
                self.assertEqual(report["test_ids"], [])

    def test_ci_documentation_stays_a_conservative_full_run(self):
        report = ci_mac_modules.selection_report(["docs/ci/README.md"], REPO_ROOT)
        self.assertEqual(report["level"], "L3")
        self.assertEqual(report["test_ids"], ["FULL"])
        self.assertIn("ci_or_dependency_change", report["fallback_reasons"])

    def test_repo_readme_stays_unsupported_and_runtime_markdown_is_not_docs(self):
        readme = ci_mac_modules.selection_report(["README.md"], REPO_ROOT)
        self.assertEqual(readme["level"], "L3")
        self.assertIn("unsupported_path", readme["fallback_reasons"])

        for path in (
            "tools/llmtool/llmtool/guide.md",
            "tools/llmtool/nested/README.md",
        ):
            with self.subTest(path=path):
                report = ci_mac_modules.selection_report([path], REPO_ROOT)
                self.assertEqual(report["level"], "L0")
                self.assertEqual(report["test_ids"], [])

    def test_repo_docs_can_mix_with_runtime_without_becoming_fallbacks(self):
        report = ci_mac_modules.selection_report(
            ["docs/meetings/2026-11-02-llmtool-governance-and-ai-team-decisions.md",
             "tools/llmtool/tests/test_worker_dispatch.py"],
            REPO_ROOT,
        )
        self.assertEqual(report["level"], "L1")
        self.assertEqual(report["fallback_reasons"], [])
        self.assertIn("test_worker_dispatch", report["test_ids"])

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
            self.assertIn("missing_mapped_test", report["fallback_reasons"])

    def test_cli_json_and_text_output_are_deterministic(self):
        with tempfile.TemporaryDirectory() as temporary:
            changed = Path(temporary) / "changed-paths"
            changed.write_bytes(b"tools/llmtool/developer-routes.json\0")
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(ci_mac_modules.main([
                    "--changed-paths", str(changed), "--source-head", "candidate-sha",
                    "--json-report"
                ]), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report["level"], "L1")
            self.assertEqual(report["source_head"], "candidate-sha")
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
        self.assertIn('pipe_status=("${PIPESTATUS[@]}")', workflow)
        self.assertIn("canonical exact HEAD:", workflow)
        self.assertIn("canonical test ID elapsed seconds:", workflow)
        self.assertIn("canonical unittest test-case count:", workflow)
        self.assertIn("verify=true", workflow)

    def test_monitor_github_reads_selection_matches_monitor_test(self):
        # Changes holding only the new monitor test are selected like scripts/test_llmtool_monitor.py.
        baseline = ci_mac_modules.selection_report(
            ["scripts/test_llmtool_monitor.py"], REPO_ROOT
        )
        report = ci_mac_modules.selection_report(
            ["scripts/test_llmtool_monitor_github_reads.py"], REPO_ROOT
        )
        self.assertEqual(report["level"], "L0")
        self.assertEqual(report["test_ids"], [])
        self.assertEqual(report["level"], baseline["level"])
        self.assertEqual(report["test_ids"], baseline["test_ids"])
        self.assertEqual(report["selection_reason"], baseline["selection_reason"])
        self.assertEqual(report["fallback_reasons"], baseline["fallback_reasons"])
        self.assertEqual(report["fallback_paths"], baseline["fallback_paths"])
        self.assertEqual(report["fallback_details"], baseline["fallback_details"])
        self.assertEqual(
            ci_mac_modules.select_test_ids(
                ["scripts/test_llmtool_monitor_github_reads.py"], REPO_ROOT
            ),
            ci_mac_modules.select_test_ids(
                ["scripts/test_llmtool_monitor.py"], REPO_ROOT
            ),
        )

    def test_workflow_names_monitor_github_reads_paths_and_pattern(self):
        # Workflow paths list and monitor-only case pattern both name the new file.
        workflow = (REPO_ROOT / ".github" / "workflows" / "llmtool-mac-ci.yml").read_text()
        self.assertIn("- 'scripts/test_llmtool_monitor_github_reads.py'", workflow)
        case_pattern = (
            "scripts/llmtool_monitor.py|scripts/test_llmtool_monitor.py|"
            "scripts/test_llmtool_monitor_github_reads.py)"
        )
        self.assertIn(case_pattern, workflow)

    def test_ec2_runner_readme_alone_selects_no_subset(self):
        report = ci_mac_modules.selection_report(["tools/ec2-runner/README.md"], REPO_ROOT)
        self.assertEqual(report["level"], "L0")
        self.assertEqual(report["test_ids"], [])

    def test_nlf_readme_alone_selects_no_subset(self):
        report = ci_mac_modules.selection_report(["tools/nlf/README.md"], REPO_ROOT)
        self.assertEqual(report["level"], "L0")
        self.assertEqual(report["test_ids"], [])

    def test_mac_runner_readme_alone_selects_no_subset(self):
        report = ci_mac_modules.selection_report(["tools/mac-runner/README.md"], REPO_ROOT)
        self.assertEqual(report["level"], "L0")
        self.assertEqual(report["test_ids"], [])

    def test_other_tool_non_markdown_keeps_full_fallback(self):
        for path in (
            "tools/mac-runner/setup.sh",
            "tools/ec2-runner/README.txt",
            "tools/nlf/main.go",
        ):
            report = ci_mac_modules.selection_report([path], REPO_ROOT)
            self.assertEqual(report["level"], "L3")
            self.assertIn("unsupported_path", report["fallback_reasons"])

    def test_other_tool_markdown_with_rejected_path_keeps_full_fallback(self):
        report = ci_mac_modules.selection_report(
            ["tools/nlf/README.md", "tools/mac-runner/setup.sh"], REPO_ROOT
        )
        self.assertEqual(report["level"], "L3")
        self.assertIn("unsupported_path", report["fallback_reasons"])

    def test_other_tool_markdown_helper_rejects_bad_paths(self):
        for path in (
            "tools/README.md",
            "tools/llmtool/README.md",
            "tools/nlf/../x.md",
            "tools//x.md",
            "tools/nlf/README.txt",
        ):
            self.assertFalse(ci_mac_modules._is_other_tool_markdown(path))
        self.assertTrue(ci_mac_modules._is_other_tool_markdown("tools/nlf/docs/guide.md"))


WORKFLOW = REPO_ROOT / ".github" / "workflows" / "llmtool-mac-ci.yml"
CANONICAL_STEP = "Offline canonical checks"
MODE_EXPRESSION = "${{ steps.coverage.outputs.mode }}"
STEP_LIMIT = "${{ steps.coverage.outputs.mode == 'full' && 110 || 45 }}"
# Synthetic commit id for the fake git; not a real revision.
SYNTHETIC_HEAD = "f" * 40
# Shared body of the fake test commands. FAKE_STARTED makes the command
# slow (it marks that it started, then sleeps); FAKE_RAN lists the counts
# for the "Ran N tests" lines; FAKE_STATUS is the exit status.
FAKE_RUN = r"""if [ -n "$FAKE_STARTED" ]; then
  : > "$FAKE_STARTED"
  sleep 120
fi
for count in $FAKE_RAN; do
  printf 'synthetic_case (test_synthetic.SyntheticTests) ... ok\n\nRan %s tests in 0.001s\n\nOK\n' "$count"
done
exit "${FAKE_STATUS:-0}"
"""
FAKE_TOOLS = {
    "make": "#!/bin/sh\n# Synthetic make: the syntax target passes.\n"
            'if [ "$3" = syntax ]; then exit 0; fi\n' + FAKE_RUN,
    "python3": "#!/bin/sh\n# Synthetic test runner.\n" + FAKE_RUN,
    "git": "#!/bin/sh\n# Synthetic git: answers rev-parse HEAD only.\n"
           '[ "$1 $2" = "rev-parse HEAD" ] || exit 2\n'
           f"echo {SYNTHETIC_HEAD}\n",
}
# Installed only when a test asks for it: records and prints its input,
# then fails with FAKE_TEE_STATUS.
FAKE_TEE = ("#!/bin/sh\n# Synthetic tee that fails after it records its input.\n"
            'cat > "$1"\ncat "$1"\nexit "$FAKE_TEE_STATUS"\n')
SUBSET_IDS = ("test_alpha.AlphaTests", "test_beta")
REPORT_BLOCK = re.compile(
    r"^canonical exact HEAD: (.*)\n"
    r"canonical unittest test-case count: (\d+)\n"
    r"canonical elapsed seconds: (\d+)\n"
    r"canonical result: (\w+)$",
    re.MULTILINE,
)


def workflow_step(name):
    """Return the raw lines of one check-job step, found by its name."""
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = lines.index(f"      - name: {name}")
    end = start + 1
    while end < len(lines) and (not lines[end].strip()
                                or lines[end].startswith(" " * 8)):
        end += 1
    return lines[start:end]


def canonical_script():
    """Return the run script of the canonical step, as GitHub passes it."""
    step = workflow_step(CANONICAL_STEP)
    body = []
    for line in step[step.index("        run: |") + 1:]:
        if line.strip() and not line.startswith(" " * 10):
            break
        body.append(line[10:])
    return "\n".join(body).rstrip("\n") + "\n"


class CanonicalStepStructureTests(unittest.TestCase):
    def test_script_sets_pass_only_on_its_last_line_with_one_exit_trap(self):
        script = canonical_script()
        lines = [line.strip() for line in script.splitlines() if line.strip()]
        commands = [line for line in lines if not line.startswith("#")]
        self.assertEqual(lines[-1], "result=PASS")
        self.assertEqual(sum("result=PASS" in line for line in lines), 1)
        self.assertLess(commands.index("result=INCOMPLETE"),
                        commands.index("trap report_result EXIT"))
        self.assertIn("test_count=0", commands)
        traps = [line for line in commands if re.search(r"\btrap\b", line)]
        self.assertEqual(traps, ["trap report_result EXIT"])
        self.assertIn('echo "::error::canonical checks ran no test cases"', commands)

    def test_job_limit_is_120_and_only_canonical_step_has_a_mode_limit(self):
        lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
        limits = [line for line in lines if line.strip().startswith("timeout-minutes:")]
        self.assertEqual(limits, [
            "    timeout-minutes: 120",
            f"        timeout-minutes: {STEP_LIMIT}",
        ])
        job = lines.index("  check:")
        steps = lines.index("    steps:")
        self.assertTrue(job < lines.index(limits[0]) < steps)
        self.assertIn(limits[1], workflow_step(CANONICAL_STEP))


@unittest.skipUnless(os.name == "posix", "the step script runs under POSIX bash")
class CanonicalStepRunTests(unittest.TestCase):
    """Run the real canonical step script with fake make, python3 and git."""

    def run_step(self, mode, *, ran="2 3", status=0, terminate=False, tee_status=None):
        bash = shutil.which("bash")
        self.assertIsNotNone(bash, "bash is required on POSIX systems")
        script = canonical_script()
        self.assertEqual(script.count(MODE_EXPRESSION), 1)
        script = script.replace(MODE_EXPRESSION, mode)
        self.assertNotIn("${{", script)
        tools = dict(FAKE_TOOLS)
        if tee_status is not None:
            tools["tee"] = FAKE_TEE
        with tempfile.TemporaryDirectory(prefix="mac-ci-step-") as temporary:
            root = Path(temporary)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            for name, body in tools.items():
                tool = bin_dir / name
                tool.write_text(body, encoding="utf-8")
                tool.chmod(0o755)
            (root / "mac-test-ids").write_text(
                "".join(f"{test_id}\n" for test_id in SUBSET_IDS), encoding="utf-8")
            script_path = root / "step.sh"
            script_path.write_text(script, encoding="utf-8")
            started = root / "started"
            env = {
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', os.defpath)}",
                "HOME": str(root),
                "RUNNER_TEMP": str(root),
                "JOB_PYTHON": "python3",
                "FAKE_RAN": ran,
                "FAKE_STATUS": str(status),
            }
            if terminate:
                env["FAKE_STARTED"] = str(started)
            if tee_status is not None:
                env["FAKE_TEE_STATUS"] = str(tee_status)
            output_path = root / "output"
            with output_path.open("wb") as output:
                process = subprocess.Popen(
                    [bash, "--noprofile", "--norc", "-eo", "pipefail", str(script_path)],
                    cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=output,
                    stderr=subprocess.STDOUT, start_new_session=True,
                )
            try:
                if terminate:
                    deadline = time.monotonic() + 30
                    while not started.exists():
                        if process.poll() is not None or time.monotonic() > deadline:
                            self.fail("the slow fake command did not start")
                        time.sleep(0.05)
                    os.kill(process.pid, signal.SIGTERM)
                    try:
                        returncode = process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        self.fail("the step shell did not exit promptly after SIGTERM")
                else:
                    returncode = process.wait(timeout=60)
            finally:
                # The slow fake command outlives the shell; stop the whole group.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    # The group is already gone (macOS may report EPERM).
                    pass
                process.wait(timeout=10)
            text = output_path.read_text(encoding="utf-8", errors="replace")
        return returncode, text

    def report(self, text):
        blocks = REPORT_BLOCK.findall(text)
        self.assertEqual(len(blocks), 1, text)
        self.assertEqual(text.count("canonical result:"), 1, text)
        head, count, _elapsed, result = blocks[0]
        self.assertEqual(head, SYNTHETIC_HEAD)
        return int(count), result

    def test_pass_sums_counts_in_both_modes(self):
        # Each fake run prints "Ran 2" and "Ran 3"; the subset runs two IDs.
        for mode, expected in (("full", 5), ("mac-subset", 10)):
            with self.subTest(mode=mode):
                returncode, text = self.run_step(mode)
                self.assertEqual(returncode, 0, text)
                self.assertEqual(self.report(text), (expected, "PASS"))
                self.assertNotIn("::error::", text)
                if mode == "mac-subset":
                    for test_id in SUBSET_IDS:
                        self.assertRegex(text, re.compile(
                            rf"^canonical test ID: {re.escape(test_id)}\n"
                            r"canonical test ID elapsed seconds: \d+$", re.MULTILINE))

    def test_failing_command_reports_fail_with_its_own_status(self):
        for mode in ("full", "mac-subset"):
            with self.subTest(mode=mode):
                returncode, text = self.run_step(mode, status=3)
                self.assertEqual(returncode, 3, text)
                self.assertEqual(self.report(text), (5, "FAIL"))
                if mode == "mac-subset":
                    self.assertIn(f"canonical test ID: {SUBSET_IDS[0]}\n", text)
                    self.assertNotIn(f"canonical test ID: {SUBSET_IDS[1]}\n", text)

    def test_failing_tee_is_not_a_pass(self):
        # The fake tee records the "Ran N tests" lines and then fails.
        for mode in ("full", "mac-subset"):
            with self.subTest(mode=mode, command_status=0):
                returncode, text = self.run_step(mode, tee_status=4)
                self.assertEqual(returncode, 4, text)
                self.assertEqual(self.report(text), (5, "FAIL"))
                if mode == "mac-subset":
                    self.assertIn(f"canonical test ID: {SUBSET_IDS[0]}\n", text)
                    self.assertNotIn(f"canonical test ID: {SUBSET_IDS[1]}\n", text)
            with self.subTest(mode=mode, command_status=3):
                # When both fail, the step exits with the command's own status.
                returncode, text = self.run_step(mode, status=3, tee_status=4)
                self.assertEqual(returncode, 3, text)
                self.assertEqual(self.report(text), (5, "FAIL"))

    def test_zero_test_cases_is_not_a_pass(self):
        for mode in ("full", "mac-subset"):
            for ran in ("0", ""):
                with self.subTest(mode=mode, ran=ran):
                    returncode, text = self.run_step(mode, ran=ran)
                    self.assertEqual(returncode, 1, text)
                    self.assertEqual(self.report(text), (0, "FAIL"))
                    self.assertIn("::error::canonical checks ran no test cases", text)

    def test_sigterm_during_slow_command_is_not_a_pass(self):
        for mode in ("full", "mac-subset"):
            with self.subTest(mode=mode):
                returncode, text = self.run_step(mode, terminate=True)
                self.assertNotEqual(returncode, 0, text)
                _count, result = self.report(text)
                self.assertIn(result, ("INCOMPLETE", "FAIL"))


if __name__ == "__main__":
    unittest.main()
