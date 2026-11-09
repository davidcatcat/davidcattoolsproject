from __future__ import annotations

from pathlib import Path
import sys
import unittest

import yaml


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import ci_path_classes  # noqa: E402
import ci_mac_modules  # noqa: E402


class CIPathClassTests(unittest.TestCase):
    def test_nul_separated_classification_and_surrogate_paths(self):
        self.assertEqual(
            ci_path_classes.classify_paths(
                b"docs/guide.md\0tools/llmtool/docs/guide.md\0"
                b"tools/api-agent-gateway/a.py\0tools/ai-loop/a.py\0"
                b".github/workflows/ci.yml\0src/unknown.py\0"
            ),
            ["docs", "runtime", "gateway", "ai-loop", "workflow", "other"],
        )
        self.assertEqual(ci_path_classes.classify_paths(b"bad-\xff.py\0"), ["other"])
        self.assertEqual(ci_path_classes.classify_paths(b""), [])

    def test_workflow_linux_flags_preserve_class_mapping_and_markdown_docs(self):
        cases = (
            ("docs", b"docs/guide.md\0", (False, True, False, False)),
            ("runtime", b"tools/llmtool/llmtool/app.py\0", (True, False, False, False)),
            ("gateway", b"tools/api-agent-gateway/app.py\0", (True, False, True, False)),
            ("ai-loop", b"tools/ai-loop/helper.py\0", (False, False, False, True)),
            ("workflow", b".github/workflows/ci.yml\0", (True, False, False, False)),
            ("other", b"src/unknown.py\0", (True, False, False, False)),
            ("runtime Markdown", b"tools/llmtool/docs/guide.md\0",
             (True, True, False, False)),
        )
        for label, paths, expected in cases:
            with self.subTest(paths=label):
                self.assertEqual(ci_path_classes.linux_flags(paths), expected)

        workflow = (Path(__file__).resolve().parents[3] / ".github" / "workflows"
                    / "llmtool-linux-ci.yml").read_text(encoding="utf-8")
        self.assertIn(
            "flags=$(python3 -I -B tools/llmtool/scripts/ci_path_classes.py --linux-flags \"$paths\")",
            workflow,
        )
        self.assertIn("read -r code docs gateway ai_loop <<< \"$flags\"", workflow)

    def test_historical_and_boundary_path_sets_keep_linux_job_flags(self):
        cases = (
            ("#781", [
                "tools/llmtool/llmtool/review_repair.py",
                "tools/llmtool/llmtool/task_advance.py",
                "tools/llmtool/tests/test_review_repair.py",
                "tools/llmtool/tests/test_task_advance.py",
            ], (True, False, False, False)),
            ("#782", [
                "tools/llmtool/llmtool/opencode_direct_developer.py",
                "tools/llmtool/llmtool/task_store.py",
                "tools/llmtool/llmtool/task_submission_cli.py",
                "tools/llmtool/tests/test_task_store.py",
                "tools/llmtool/tests/test_task_submission_cli.py",
            ], (True, False, False, False)),
            ("#787", [
                "tools/llmtool/llmtool/chat_tui.py",
                "tools/llmtool/llmtool/chat_tui_status.py",
                "tools/llmtool/tests/test_chat_tui.py",
            ], (True, False, False, False)),
            ("#789", [
                "README.md", "tools/llmtool/llmtool/config.py",
                "tools/llmtool/llmtool/role_config.py",
                "tools/llmtool/tests/test_role_config.py",
            ], (True, True, False, False)),
            ("#790", ["skills/ai-team-delivery/pm-continuation.md"],
             (False, True, False, False)),
            ("#791", ["tools/llmtool/worker-dispatch.json",
                       "tools/llmtool/tests/test_worker_dispatch.py"],
             (True, False, False, False)),
            ("#792", [".github/workflows/llmtool-linux-ci.yml",
                       "tools/ai-loop/README.md"], (True, True, False, True)),
            ("docs-only", ["AGENTS.md", "docs/meetings/2026-11-02-llmtool-governance-and-ai-team-decisions.md"],
             (False, True, False, False)),
            ("skills-only", ["skills/ai-team-delivery/SKILL.md"],
             (False, True, False, False)),
            ("nested llmtool Markdown", ["tools/llmtool/docs/guide.md"],
             (True, True, False, False)),
            ("workflow-only", [".github/workflows/llmtool-linux-ci.yml"],
             (True, False, False, False)),
            ("mixed", ["docs/meetings/2026-11-02-llmtool-governance-and-ai-team-decisions.md", "tools/api-agent-gateway/main.py",
                       "tools/ai-loop/helper.py"],
             (True, True, True, True)),
            ("unknown", ["src/unknown.py"], (True, False, False, False)),
        )
        for label, paths, expected in cases:
            with self.subTest(paths=label):
                path_bytes = b"\0".join(path.encode() for path in paths) + b"\0"
                self.assertEqual(ci_path_classes.linux_flags(path_bytes), expected)

    def test_historical_and_boundary_path_sets_keep_mac_levels(self):
        cases = (
            ("#781", ["tools/llmtool/llmtool/review_repair.py",
                       "tools/llmtool/llmtool/task_advance.py",
                       "tools/llmtool/tests/test_review_repair.py",
                       "tools/llmtool/tests/test_task_advance.py"], "L1"),
            ("#782", ["tools/llmtool/llmtool/opencode_direct_developer.py",
                       "tools/llmtool/llmtool/task_store.py",
                       "tools/llmtool/llmtool/task_submission_cli.py",
                       "tools/llmtool/tests/test_task_store.py",
                       "tools/llmtool/tests/test_task_submission_cli.py"], "L3"),
            ("#787", ["tools/llmtool/llmtool/chat_tui.py",
                       "tools/llmtool/llmtool/chat_tui_status.py",
                       "tools/llmtool/tests/test_chat_tui.py"], "L1"),
            ("#789", ["README.md", "tools/llmtool/llmtool/config.py",
                       "tools/llmtool/llmtool/role_config.py",
                       "tools/llmtool/tests/test_role_config.py"], "L3"),
            ("#790", ["skills/ai-team-delivery/pm-continuation.md"], "L0"),
            ("#791", ["tools/llmtool/worker-dispatch.json",
                       "tools/llmtool/tests/test_worker_dispatch.py"], "L1"),
            ("#792", [".github/workflows/llmtool-linux-ci.yml",
                       "tools/ai-loop/README.md"], "L3"),
            ("docs-only", ["AGENTS.md",
                            "docs/meetings/2026-11-02-llmtool-governance-and-ai-team-decisions.md"], "L0"),
            ("skills-only", ["skills/ai-team-delivery/SKILL.md"], "L0"),
            ("nested llmtool Markdown", ["tools/llmtool/docs/guide.md"], "L0"),
            ("workflow-only", [".github/workflows/llmtool-linux-ci.yml"], "L3"),
            ("mixed", ["docs/meetings/2026-11-02-llmtool-governance-and-ai-team-decisions.md",
                       "tools/api-agent-gateway/main.py", "tools/ai-loop/helper.py"], "L3"),
        )
        for label, paths, expected in cases:
            with self.subTest(paths=label):
                self.assertEqual(ci_mac_modules.selection_report(paths)["level"], expected)

    def test_ci_documentation_remains_a_conservative_mac_full_run(self):
        report = ci_mac_modules.selection_report(["docs/ci/README.md"])
        self.assertEqual(report["level"], "L3")
        self.assertEqual(report["test_ids"], ["FULL"])
        self.assertIn("ci_or_dependency_change", report["fallback_reasons"])

    def test_runtime_markdown_never_has_docs_class_and_root_readme_is_docs(self):
        self.assertEqual(ci_path_classes.classify_path("tools/llmtool/README.md"), "docs")
        for path in (
            "tools/llmtool/llmtool/guide.md",
            "tools/llmtool/docs/guide.md",
            "tools/llmtool/nested/README.md",
        ):
            with self.subTest(path=path):
                self.assertEqual(ci_path_classes.classify_path(path), "runtime")

    def test_classify_path_cases(self):
        cases = (
            ("scripts/llmtool_monitor.py", "other"),
            ("scripts/test_llmtool_monitor.py", "other"),
            ("skills/x/tool.py", "other"),
            ("skills/x/test_tool.py", "other"),
            ("skills/x/SKILL.md", "docs"),
            ("skills/x/notes.txt", "docs"),
            (".github/workflows/llmtool-mac-ci.yml", "workflow"),
        )
        for path, expected in cases:
            with self.subTest(path=path):
                self.assertEqual(ci_path_classes.classify_path(path), expected)

    def test_linux_flags_cases(self):
        cases = (
            (["scripts/llmtool_monitor.py"], (True, False, False, False)),
            (["skills/x/tool.py"], (True, False, False, False)),
            (["skills/x/SKILL.md"], (False, True, False, False)),
            ([".github/workflows/llmtool-mac-ci.yml"], (True, False, False, False)),
        )
        for paths, expected in cases:
            with self.subTest(paths=paths):
                path_bytes = b"\0".join(p.encode() for p in paths) + b"\0"
                self.assertEqual(ci_path_classes.linux_flags(path_bytes), expected)

    def test_workflow_paths_include_markdown_and_reports(self):
        repo_root = Path(__file__).resolve().parents[3]
        workflow_text = (
            repo_root / ".github" / "workflows" / "llmtool-linux-ci.yml"
        ).read_text(encoding="utf-8")
        parsed = yaml.safe_load(workflow_text)
        on_block = parsed.get("on") if "on" in parsed else parsed.get(True)
        push_paths = on_block["push"]["paths"]
        pr_paths = on_block["pull_request"]["paths"]

        required_entries = (
            "!tools/llmtool/*.md",
            "*.md",
            "scripts/**",
            ".github/workflows/**",
            "**/*.md",
            "reports/**",
        )
        for paths in (push_paths, pr_paths):
            self.assertEqual(paths[-2:], ["**/*.md", "reports/**"])
            for entry in required_entries:
                self.assertIn(entry, paths)

    def test_reports_markdown_classification(self):
        code, docs, gateway, ai_loop = ci_path_classes.linux_flags(b"reports/x.md\0")
        self.assertTrue(docs)
        self.assertFalse(code)
        self.assertEqual(
            ci_path_classes.linux_flags(b"reports/x.md\0"),
            (False, True, False, False),
        )


if __name__ == "__main__":
    unittest.main()
