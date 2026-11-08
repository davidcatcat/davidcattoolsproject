from __future__ import annotations

import ast
from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import yaml


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
        self.assertIn("status=${PIPESTATUS[0]}", workflow)
        self.assertIn("canonical exact HEAD:", workflow)
        self.assertIn("canonical test ID elapsed seconds:", workflow)
        self.assertIn("canonical unittest test-case count:", workflow)
        self.assertIn("verify=true", workflow)


WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "llmtool-mac-ci.yml"
CANONICAL_STEP = "Offline canonical checks"
MODE_EXPRESSION = "${{ steps.coverage.outputs.mode }}"
STEP_LIMIT = "${{ steps.coverage.outputs.mode == 'full' && 90 || 45 }}"
# Synthetic stand-in for a unittest run. FAKE_TESTS picks the behaviour.
FAKE_TEST_BODY = r"""
case "$FAKE_TESTS" in
  pass)
    printf 'Ran 3 tests in 0.010s\n\nOK\nRan 1 test in 0.001s\n\nOK\n'
    exit 0 ;;
  fail)
    printf 'Ran 2 tests in 0.010s\n\nFAILED (failures=1)\n'
    exit 7 ;;
  none)
    printf 'OK\n'
    exit 0 ;;
  slow)
    : > "$FAKE_STARTED"
    exec sleep 120 ;;
esac
exit 99
"""
FAKE_MAKE = '#!/bin/sh\ncase " $* " in *" syntax "*) exit 0 ;; esac\n' + FAKE_TEST_BODY
FAKE_PYTHON = "#!/bin/sh\n" + FAKE_TEST_BODY
FAKE_GIT = "#!/bin/sh\nprintf 'fake-head\\n'\n"
RESULT_LINE = re.compile(r"^canonical result: (\S+)$", re.MULTILINE)


def load_workflow():
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def canonical_step():
    steps = load_workflow()["jobs"]["check"]["steps"]
    return next(step for step in steps if step.get("name") == CANONICAL_STEP)


@unittest.skipUnless(os.name == "posix", "the step runs under bash on POSIX runners")
class OfflineCanonicalChecksStepTests(unittest.TestCase):
    """Run the real step script with a fake make, Python and git."""

    def step_script(self, mode):
        script = canonical_step()["run"]
        self.assertEqual(script.count(MODE_EXPRESSION), 1)
        script = script.replace(MODE_EXPRESSION, mode)
        self.assertNotIn("${{", script)
        return script

    @staticmethod
    def kill_group(process):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass

    def start_step(self, mode, behaviour):
        temporary = tempfile.TemporaryDirectory(prefix="mac-canonical-step-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        bin_dir, runner_temp, work = root / "bin", root / "runner", root / "work"
        for directory in (bin_dir, runner_temp, work):
            directory.mkdir()
        for name, body in (("make", FAKE_MAKE), ("python3", FAKE_PYTHON), ("git", FAKE_GIT)):
            path = bin_dir / name
            path.write_text(body, encoding="utf-8")
            path.chmod(0o700)
        (runner_temp / "mac-test-ids").write_text("test_alpha\ntest_beta\n", encoding="utf-8")
        script = root / "step.sh"
        script.write_text(self.step_script(mode), encoding="utf-8")
        started = root / "fake-started"
        log = root / "step.log"
        # BASH_ENV would run a startup file even with --norc; keep it out.
        env = {
            **{key: value for key, value in os.environ.items()
               if key not in ("BASH_ENV", "ENV")},
            "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
            "RUNNER_TEMP": str(runner_temp),
            "JOB_PYTHON": str(bin_dir / "python3"),
            "FAKE_TESTS": behaviour,
            "FAKE_STARTED": str(started),
        }
        with log.open("wb") as handle:
            # Same shell flags as GitHub's default bash step shell.
            process = subprocess.Popen(
                ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)],
                cwd=work, env=env, stdin=subprocess.DEVNULL, stdout=handle,
                stderr=subprocess.STDOUT, start_new_session=True,
            )
        # Cleanups run last-in first-out, so the group dies before the files go.
        self.addCleanup(self.kill_group, process)
        return process, log, started

    def run_step(self, mode, behaviour):
        process, log, _ = self.start_step(mode, behaviour)
        returncode = process.wait(timeout=60)
        return returncode, log.read_text(encoding="utf-8", errors="replace")

    def assert_one_result(self, output):
        results = RESULT_LINE.findall(output)
        self.assertEqual(len(results), 1, output)
        self.assertIn("canonical exact HEAD: fake-head", output)
        return results[0]

    def test_script_sets_pass_only_on_its_last_line(self):
        script = canonical_step()["run"]
        lines = [line.strip() for line in script.strip().splitlines()]
        self.assertIn("result=INCOMPLETE", lines)
        self.assertEqual(script.count("result=PASS"), 1)
        self.assertEqual(lines[-1], "result=PASS")
        self.assertEqual([line for line in lines if line.startswith("trap ")],
                         ["trap report_result EXIT"])

    def test_passing_run_reports_pass_with_summed_count(self):
        for mode, count in (("full", 4), ("mac-subset", 8)):
            with self.subTest(mode=mode):
                returncode, output = self.run_step(mode, "pass")
                self.assertEqual(returncode, 0, output)
                self.assertEqual(self.assert_one_result(output), "PASS")
                self.assertIn(f"canonical unittest test-case count: {count}\n", output)
                self.assertIn("canonical elapsed seconds: ", output)
                if mode == "mac-subset":
                    self.assertIn("canonical test ID: test_alpha\n", output)
                    self.assertIn("canonical test ID: test_beta\n", output)
                    self.assertIn("canonical test ID elapsed seconds: ", output)

    def test_failing_command_reports_fail_with_its_exit_status(self):
        for mode in ("full", "mac-subset"):
            with self.subTest(mode=mode):
                returncode, output = self.run_step(mode, "fail")
                self.assertEqual(returncode, 7, output)
                self.assertEqual(self.assert_one_result(output), "FAIL")

    def test_zero_test_count_is_not_a_pass(self):
        for mode in ("full", "mac-subset"):
            with self.subTest(mode=mode):
                returncode, output = self.run_step(mode, "none")
                self.assertNotEqual(returncode, 0, output)
                self.assertIn(self.assert_one_result(output), ("INCOMPLETE", "FAIL"))
                self.assertIn("canonical unittest test-case count: 0\n", output)
                self.assertIn("ran no test cases", output)

    def test_sigterm_during_slow_test_command_is_not_a_pass(self):
        for mode in ("full", "mac-subset"):
            with self.subTest(mode=mode):
                process, log, started = self.start_step(mode, "slow")
                deadline = time.monotonic() + 30
                while not started.exists():
                    self.assertIsNone(process.poll(), log.read_text(errors="replace"))
                    self.assertLess(time.monotonic(), deadline, "fake test never started")
                    time.sleep(0.05)
                os.kill(process.pid, signal.SIGTERM)
                # The fake command sleeps 120 s, so a quick exit shows bash
                # did not wait for it before reacting to the signal.
                returncode = process.wait(timeout=20)
                output = log.read_text(encoding="utf-8", errors="replace")
                self.assertNotEqual(returncode, 0, output)
                self.assertIn(self.assert_one_result(output), ("INCOMPLETE", "FAIL"))
                self.assertNotIn("canonical result: PASS", output)


class MacWorkflowTimeLimitTests(unittest.TestCase):
    def test_job_and_canonical_step_limits_follow_mode(self):
        job = load_workflow()["jobs"]["check"]
        self.assertEqual(job["timeout-minutes"], 100)
        limited = {step.get("name"): step["timeout-minutes"]
                   for step in job["steps"] if "timeout-minutes" in step}
        self.assertEqual(limited, {CANONICAL_STEP: STEP_LIMIT})


if __name__ == "__main__":
    unittest.main()
