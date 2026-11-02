from __future__ import annotations

from pathlib import Path
import sys
import unittest


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import ci_path_classes  # noqa: E402


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
                       "tools/ai-loop/README.md"], (True, False, False, True)),
            ("docs-only", ["AGENTS.md", "docs/ci/README.md"],
             (False, True, False, False)),
            ("skills-only", ["skills/ai-team-delivery/SKILL.md"],
             (False, True, False, False)),
            ("nested llmtool Markdown", ["tools/llmtool/docs/guide.md"],
             (True, False, False, False)),
            ("workflow-only", [".github/workflows/llmtool-linux-ci.yml"],
             (True, False, False, False)),
            ("mixed", ["docs/ci/README.md", "tools/api-agent-gateway/main.py",
                       "tools/ai-loop/helper.py"],
             (True, True, True, True)),
            ("unknown", ["src/unknown.py"], (True, False, False, False)),
        )
        for label, paths, expected in cases:
            with self.subTest(paths=label):
                classes = [ci_path_classes.classify_path(path) for path in paths]
                flags = (
                    any(item in ("runtime", "gateway", "workflow", "other")
                        for item in classes),
                    "docs" in classes,
                    "gateway" in classes,
                    "ai-loop" in classes,
                )
                self.assertEqual(flags, expected)

    def test_runtime_markdown_never_has_docs_class_and_root_readme_is_docs(self):
        self.assertEqual(ci_path_classes.classify_path("tools/llmtool/README.md"), "docs")
        for path in (
            "tools/llmtool/llmtool/guide.md",
            "tools/llmtool/docs/guide.md",
            "tools/llmtool/nested/README.md",
        ):
            with self.subTest(path=path):
                self.assertEqual(ci_path_classes.classify_path(path), "runtime")


if __name__ == "__main__":
    unittest.main()
