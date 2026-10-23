"""Real local-Git integration tests for the D6 create/bootstrap handoff."""

from __future__ import annotations

import contextlib
import io
import json
import os
import platform
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import d6_worktree_lifecycle as lifecycle  # noqa: E402
import d6_worktree_env as d6env  # noqa: E402
import worktree_env as worktreeenv  # noqa: E402


REPO = Path(__file__).resolve().parents[3]


def _actual_cache_fingerprint():
    return worktreeenv.fingerprint_id({
        "implementation": sys.implementation.name,
        "python_version": "%d.%d.%d" % sys.version_info[:3],
        "platform": sys.platform,
        "machine": platform.machine(),
    })


class D6WorktreeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "repo"
        self.source.mkdir(mode=0o700)
        self.tasks = self.root / "tasks"
        self.tasks.mkdir(mode=0o700)
        subprocess.run(["/usr/bin/git", "init", "-b", "main"], cwd=self.source,
                       check=True, capture_output=True)
        subprocess.run(["/usr/bin/git", "config", "user.name", "D6 test"],
                       cwd=self.source, check=True)
        subprocess.run(["/usr/bin/git", "config", "user.email", "d6@example.invalid"],
                       cwd=self.source, check=True)
        subprocess.run(["/usr/bin/git", "remote", "add", "origin",
                        "git@github.com:davidcatcat/davidcattoolsproject.git"],
                       cwd=self.source, check=True)

        tools = self.source / "tools" / "llmtool"
        scripts = tools / "scripts"
        scripts.mkdir(parents=True)
        (tools / "Makefile").write_text(
            "env-bootstrap:\n"
            "\t@printf 'bootstrapped\\n' > .worktree-env.lock\n"
            "\t@printf '%s\\n' '{\"status\":\"env_created\",\"cache_status\":\"miss\","
            "\"cache_fingerprint\":\"" + _actual_cache_fingerprint() + "\","
            "\"total_s\":0,\"timings\":{},\"operations\":{"
            "\"pip_download_command_count\":0,\"downloaded_wheels\":[],"
            "\"downloaded_wheel_count\":0,\"wheel_source_copy_count\":0,"
            "\"dependency_install_command_count\":0,\"dependency_installed\":[],"
            "\"dependency_install_package_count\":0,\"project_install_command_count\":0,"
            "\"project_installed\":[],\"project_install_package_count\":0},\"packages\":{}}'\n",
            encoding="utf-8")
        (scripts / "worktree_env.py").write_text("# test bootstrap helper\n", encoding="utf-8")
        shutil.copy2(SCRIPTS / "d6_worktree_env.py",
                     scripts / "d6_worktree_env.py")
        (tools / "worktree-env.lock").write_text("# test lock\n", encoding="utf-8")
        (tools / "pyproject.toml").write_text("[build-system]\n", encoding="utf-8")
        subprocess.run(["/usr/bin/git", "add", "tools"], cwd=self.source, check=True)
        subprocess.run(["/usr/bin/git", "commit", "-m", "D6 fixture base"],
                       cwd=self.source, check=True, capture_output=True)
        self.base = subprocess.check_output(
            ["/usr/bin/git", "rev-parse", "HEAD"], cwd=self.source, text=True).strip()
        registry = self.source / ".git" / "llmtool-worktrees"
        registry.mkdir(mode=0o700)
        registry.chmod(0o700)

    def test_atomic_record_publishes_complete_private_file_without_overwrite(self):
        registry = self.source / ".git" / "llmtool-worktrees"
        record_path = registry / "atomic-record.json"
        expected = {"task_id": "atomic-test", "state": "REPAIR"}

        lifecycle._atomic_record(record_path, expected)

        self.assertEqual(json.loads(record_path.read_text()), expected)
        self.assertEqual(stat.S_IMODE(record_path.stat().st_mode), 0o600)
        self.assertEqual(list(registry.glob(".atomic-record.json.*.tmp")), [])

        with self.assertRaises(lifecycle.LifecycleError) as raised:
            lifecycle._atomic_record(record_path, {"task_id": "replacement"})

        self.assertEqual(raised.exception.code, "D6_REGISTRY_WRITE_FAILED")
        self.assertEqual(json.loads(record_path.read_text()), expected)
        self.assertEqual(list(registry.glob(".atomic-record.json.*.tmp")), [])

    def test_create_register_lock_and_real_bootstrap_happen_before_ready(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                contextlib.redirect_stdout(output):
            root = lifecycle.create_worktree(
                canonical_root=self.source,
                workspace_root=self.tasks,
                task_id="d6-integration-test",
                agent_id="codex-developer-test",
                session_ref="live test session",
                lease_ref="live test lease",
                branch="test/d6-integration-test",
                base_sha=self.base,
            )

        record_path = self.source / ".git" / "llmtool-worktrees" / "d6-integration-test.json"
        self.assertTrue((root / "tools" / "llmtool" / ".worktree-env.lock").is_file())
        self.assertIn("D6_WORKTREE_READY task_id=d6-integration-test", output.getvalue())
        record = __import__("json").loads(record_path.read_text())
        self.assertEqual(record["state"], "ACTIVE")
        self.assertEqual(record["lock_state"], "LOCKED")
        self.assertEqual(record["head_sha"], self.base)
        self.assertEqual(record["branch"], "test/d6-integration-test")
        self.assertEqual(record["root_realpath"], str(root))
        generated_pyc = ("tools/llmtool/llmtool/__pycache__/__init__." +
                         sys.implementation.cache_tag + ".pyc")
        self.assertIn(generated_pyc, record["retention_paths"])
        worktrees = subprocess.check_output(
            ["/usr/bin/git", "worktree", "list", "--porcelain"],
            cwd=self.source, text=True)
        self.assertIn("worktree " + str(root), worktrees)
        self.assertIn("locked", worktrees)

        record["state"] = "REPAIR"
        lifecycle._replace_record(record_path, record)
        resume_output = io.StringIO()
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                contextlib.redirect_stdout(resume_output):
            self.assertEqual(lifecycle._main([
                "--resume-task-id", "d6-integration-test",
                "--resume-root", str(root), "--repo-root", str(self.source),
            ]), 0)
        self.assertIn("D6_WORKTREE_RESUMED task_id=d6-integration-test",
                      resume_output.getvalue())

        package_cache = root / "tools" / "llmtool" / "llmtool" / "__pycache__"
        package_cache.mkdir(parents=True)
        (package_cache / ("__init__." + sys.implementation.cache_tag + ".pyc")) \
            .write_bytes(b"generated editable-install bytecode")
        self.assertEqual(self._revalidate_d6(root, "d6-integration-test"), 0)

        (package_cache / "unregistered.cpython-313.pyc").write_bytes(b"unknown")
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            self.assertEqual(self._revalidate_d6(root, "d6-integration-test"), 1)
        self.assertIn("D6_UNREGISTERED_LOCAL_STATE", output.getvalue())

    def _revalidate_d6(self, root, task_id):
        previous = Path.cwd()
        os.chdir(root)
        try:
            script = root / "tools" / "llmtool" / "scripts" / "d6_worktree_env.py"
            with patch.object(d6env, "__file__", str(script)):
                return d6env.main(["--task-id", task_id])
        finally:
            os.chdir(previous)

    def test_lock_failure_records_repair_and_skips_bootstrap(self):
        original_run_git = lifecycle._run_git

        def fail_lock(cwd, *args, **kwargs):
            if args[:2] == ("worktree", "lock"):
                return subprocess.CompletedProcess(args, 1, "", "")
            return original_run_git(cwd, *args, **kwargs)

        output = io.StringIO()
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_run_git", side_effect=fail_lock), \
                contextlib.redirect_stdout(output):
            with self.assertRaises(lifecycle.LifecycleError) as raised:
                lifecycle.create_worktree(
                    canonical_root=self.source,
                    workspace_root=self.tasks,
                    task_id="d6-lock-failure-test",
                    agent_id="codex-developer-test",
                    session_ref="live test session",
                    lease_ref="live test lease",
                    branch="fix/d6-lock-failure-test",
                    base_sha=self.base,
                )

        self.assertEqual(raised.exception.code, "D6_WORKTREE_LOCK_FAILED")
        root = self.tasks / "d6-lock-failure-test"
        record_path = (self.source / ".git" / "llmtool-worktrees" /
                       "d6-lock-failure-test.json")
        record = __import__("json").loads(record_path.read_text())
        self.assertEqual(record["state"], "REPAIR")
        self.assertEqual(record["lock_state"], "UNLOCKED")
        self.assertFalse((root / "tools" / "llmtool" / ".worktree-env.lock").exists())
        self.assertNotIn("D6_WORKTREE_READY", output.getvalue())
        worktrees = subprocess.check_output(
            ["/usr/bin/git", "worktree", "list", "--porcelain"],
            cwd=self.source, text=True)
        self.assertIn("worktree " + str(root), worktrees)
        self.assertNotIn("\nlocked\n", worktrees)

    def test_option_like_and_reflog_expansion_branches_fail_closed(self):
        for index, branch in enumerate(("--help", "fix/topic@{1}")):
            with self.subTest(branch=branch):
                with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}):
                    with self.assertRaises(lifecycle.LifecycleError) as raised:
                        lifecycle.create_worktree(
                            canonical_root=self.source,
                            workspace_root=self.tasks,
                            task_id="d6-invalid-branch-" + str(index),
                            agent_id="codex-developer-test",
                            session_ref="live test session",
                            lease_ref="live test lease",
                            branch=branch,
                            base_sha=self.base,
                        )
                self.assertEqual(raised.exception.code, "D6_TASK_INPUT_INVALID")
        self.assertEqual(list(self.tasks.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
