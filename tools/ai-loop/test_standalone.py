"""Safety checks for the transitional standalone entry points."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import os

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
        workflow = (ROOT.parents[1] / ".github/workflows/llmtool-linux-ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("tools/ai-loop/*) ai_loop=true ;;", workflow)
        self.assertIn("needs.changes.outputs.ai_loop == 'true'", workflow)
        self.assertIn("python3.14 -B -m unittest discover -s tools/ai-loop", workflow)


if __name__ == "__main__":
    unittest.main()
