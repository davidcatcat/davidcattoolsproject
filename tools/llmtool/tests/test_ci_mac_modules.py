from __future__ import annotations

from contextlib import redirect_stdout
import io
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
    def test_runtime_scan_finds_synthetic_killpg_module(self):
        with tempfile.TemporaryDirectory() as temporary:
            tests = Path(temporary) / "tools" / "llmtool" / "tests"
            tests.mkdir(parents=True)
            (tests / "test_synthetic_mac.py").write_text("import os\nos.killpg(1, 9)\n")
            (tests / "test_unrelated.py").write_text("def test_plain(): pass\n")
            self.assertEqual(ci_mac_modules.scanned_test_modules(Path(temporary)), {"test_synthetic_mac"})

    def test_extra_modules_are_included(self):
        with tempfile.TemporaryDirectory() as temporary:
            extras = Path(temporary) / "mac-modules.txt"
            extras.write_text("# comment\ntest_extra\n")
            with patch.object(ci_mac_modules, "MODULE_LIST", extras):
                self.assertEqual(ci_mac_modules.extra_modules(), {"test_extra"})
                selected = ci_mac_modules.select_modules(["scripts/llmtool_monitor.py"], REPO_ROOT)
                self.assertIn("test_extra", selected)

    def test_monitor_only_paths_keep_baseline(self):
        self.assertEqual(
            ci_mac_modules.select_modules(MONITOR_ONLY, REPO_ROOT),
            ci_mac_modules.scanned_test_modules(REPO_ROOT) | ci_mac_modules.extra_modules(),
        )

    def test_single_source_change_adds_matching_test_modules(self):
        selected = ci_mac_modules.select_modules(["tools/llmtool/llmtool/task_store.py"], REPO_ROOT)
        matching = {path.stem for path in (REPO_ROOT / "tools/llmtool/tests").glob("test_task_store*.py")}
        self.assertEqual(selected, ci_mac_modules.scanned_test_modules(REPO_ROOT) | ci_mac_modules.extra_modules() | matching)

    def test_test_only_change_adds_changed_module(self):
        selected = ci_mac_modules.select_modules(["tools/llmtool/tests/test_new_feature.py"], REPO_ROOT)
        self.assertEqual(selected, ci_mac_modules.scanned_test_modules(REPO_ROOT) | ci_mac_modules.extra_modules() | {"test_new_feature"})

    def test_workflow_and_makefile_require_full(self):
        for path in (".github/workflows/other.yml", "tools/llmtool/Makefile"):
            with self.subTest(path=path):
                self.assertIsNone(ci_mac_modules.select_modules([path], REPO_ROOT))

    def test_unknown_path_requires_full(self):
        self.assertIsNone(ci_mac_modules.select_modules(["tools/llmtool/docs/unknown.md"], REPO_ROOT))

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

    def test_workflow_keeps_job_name_and_full_main_push(self):
        workflow = (REPO_ROOT / ".github" / "workflows" / "llmtool-mac-ci.yml").read_text()
        self.assertIn("name: check (macOS 3.13)", workflow)
        self.assertIn("mode=full", workflow)
        self.assertIn("make -C tools/llmtool check PYTHON=\"$JOB_PYTHON\"", workflow)
        self.assertIn("cancel-in-progress: true", workflow)


if __name__ == "__main__":
    unittest.main()
