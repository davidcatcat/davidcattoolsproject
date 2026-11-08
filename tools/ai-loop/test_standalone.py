"""Safety checks for the transitional standalone entry points."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import os
import importlib.util

import ai_loop


ROOT = Path(__file__).resolve().parent
LAUNCHER = ROOT / "ai-loop.sh"


class StandaloneSafetyTests(unittest.TestCase):
    def checkout(self, directory, *, branch="main", origin="https://github.com/davidcatcat/davidcattoolsproject.git"):
        root = Path(directory)
        subprocess.run(["git", "init", "-b", branch, str(root)], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(root), "remote", "add", "origin", origin],
                       check=True, capture_output=True)
        environment = dict(os.environ, GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.invalid",
                           GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.invalid")
        subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c",
                        "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "init"],
                       check=True, capture_output=True, env=environment)
        task = root / "task"
        task.mkdir()
        return root, task

    def test_launcher_has_no_network_fetch_or_bootstrap_cache(self):
        source = LAUNCHER.read_text(encoding="utf-8")
        self.assertNotIn("git fetch", source)
        self.assertNotIn("git show", source)
        self.assertNotIn("bootstrap", source)
        self.assertIn("--repo", source)
        self.assertIn("--task-dir", source)

    def test_launcher_executes_helper_from_checked_repository(self):
        source = LAUNCHER.read_text(encoding="utf-8")
        self.assertIn("ai_loop.py must be under the checked repository", source)
        self.assertIn('python3 -B "$script_dir/ai_loop.py"', source)

    def test_frozen_bootstrap_routes_are_disabled_before_execution(self):
        for arguments in (("--qa-low", "fixture"),
                          ("--qa-low-review-diagnose", "fixture"),
                          ("--maintenance-issue52",),
                          ("--install-launcher",)):
            with self.subTest(arguments=arguments), \
                 patch.object(ai_loop.sys, "argv", ["ai_loop.py", *arguments]), \
                 patch.object(ai_loop, "git") as git:
                with self.assertRaises(SystemExit) as stopped:
                    ai_loop.main()
                self.assertEqual(stopped.exception.code, 2)
                git.assert_not_called()

    def test_readme_describes_historical_feature_branch_publication(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("push a feature branch", readme)
        self.assertIn("gh pr create", readme)

    def test_launcher_refuses_non_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory) / "task"
            task.mkdir()
            result = subprocess.run(
                ["bash", str(LAUNCHER), "--repo", directory, "--task-dir", str(task)],
                capture_output=True, text=True, check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a Git checkout", result.stderr)

    def test_launcher_refuses_wrong_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            root, task = self.checkout(directory, origin="https://example.invalid/wrong.git")
            result = subprocess.run(
                ["bash", str(LAUNCHER), "--repo", str(root), "--task-dir", str(task)],
                capture_output=True, text=True, check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("origin is not", result.stderr)

    def test_launcher_refuses_non_main_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            root, task = self.checkout(directory, branch="feature/test")
            result = subprocess.run(
                ["bash", str(LAUNCHER), "--repo", str(root), "--task-dir", str(task)],
                capture_output=True, text=True, check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires the main branch", result.stderr)

    def test_launcher_refuses_dirty_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root, task = self.checkout(directory)
            (root / "uncommitted.txt").write_text("dirty", encoding="utf-8")
            result = subprocess.run(
                ["bash", str(LAUNCHER), "--repo", str(root), "--task-dir", str(task)],
                capture_output=True, text=True, check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("working tree is not clean", result.stderr)

    def test_publishers_fail_before_any_gh_call(self):
        with patch.object(ai_loop, "run") as run:
            with self.assertRaisesRegex(ai_loop.Stop, "disabled"):
                ai_loop.publish_decision_checkpoint(
                    Path("."), Path("."), {}, {}, {}, "a" * 40, "b" * 40, 0,
                )
            with self.assertRaisesRegex(ai_loop.Stop, "disabled"):
                ai_loop.publish_diagnostics(Path("."), Path("."))
            run.assert_not_called()

    def test_ci_classifies_ai_loop_separately_from_llmtool_shards(self):
        repository = ROOT.parents[1]
        classifier_path = repository / "tools/llmtool/scripts/ci_path_classes.py"
        spec = importlib.util.spec_from_file_location("ci_path_classes", classifier_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        classifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(classifier)

        changed_path = b"tools/ai-loop/test_standalone.py\0"
        self.assertEqual(classifier.classify_path("tools/ai-loop/test_standalone.py"), "ai-loop")
        self.assertEqual(classifier.linux_flags(changed_path), (False, False, False, True))

        workflow = (repository / ".github/workflows/llmtool-linux-ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("ai_loop: ${{ steps.classify.outputs.ai_loop }}", workflow)
        self.assertIn("needs.changes.outputs.ai_loop == 'true'", workflow)
        self.assertIn('"$SETUP_PYTHON" -B -m unittest discover -s tools/ai-loop', workflow)
        self.assertIn("python-version: '3.14'", workflow)


if __name__ == "__main__":
    unittest.main()
