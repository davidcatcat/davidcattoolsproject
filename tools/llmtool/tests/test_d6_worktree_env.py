"""Focused tests for ENV-2 D6 lifecycle integration; no pip/provider calls."""

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import d6_worktree_env as d6env  # noqa: E402


class D6WorktreeEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="d6-env-preflight-")
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "source"
        self.source.mkdir(mode=0o700)
        self.root = Path(self.temp.name) / "tasks" / "task-root"
        self.root.parent.mkdir(mode=0o700)
        self.registry = None
        self.task_id = "issue267-test-task"
        self._git_at(self.source, "init", "-b", "main")
        self._git_at(self.source, "config", "user.name", "D6 Test")
        self._git_at(self.source, "config", "user.email", "d6-test@example.invalid")
        self._git_at(self.source, "remote", "add", "origin",
                     "git@github.com:davidcatcat/davidcattoolsproject.git")
        makefile_dir = self.source / "tools" / "llmtool"
        (makefile_dir / "scripts").mkdir(parents=True)
        (makefile_dir / "Makefile").write_text("env-bootstrap:\n\t@true\n", encoding="utf-8")
        (makefile_dir / "scripts" / "worktree_env.py").write_text(
            "# test fixture source\n", encoding="utf-8")
        (makefile_dir / "scripts" / "d6_worktree_env.py").write_bytes(
            (SCRIPTS / "d6_worktree_env.py").read_bytes())
        (self.source / "README").write_text("test\n", encoding="utf-8")
        self._git_at(self.source, "add", ".")
        self._git_at(self.source, "commit", "-m", "fixture")
        self._git_at(self.source, "worktree", "add", "-b", "feat/test-d6-env",
                     str(self.root), "HEAD")
        self.root = Path(os.path.realpath(self.root))
        os.chmod(self.root, 0o700)
        self._git_at(self.source, "worktree", "lock", str(self.root), "--reason", "test lock")
        self._git("config", "user.name", "D6 Test")
        self._git("config", "user.email", "d6-test@example.invalid")
        self.common = Path(self._git("rev-parse", "--path-format=absolute",
                                     "--git-common-dir"))
        self.registry = self.common / "llmtool-worktrees"
        self.registry.mkdir(mode=0o700)
        os.chmod(self.registry, 0o700)
        self.record_path = self.registry / (self.task_id + ".json")
        self._write_record()

    def _git_at(self, root, *args):
        result = subprocess.run(["/usr/bin/git", "-C", str(root), *args],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                check=False)
        if result.returncode:
            self.fail("git fixture setup failed: " + result.stderr.decode(errors="replace"))
        return result.stdout.decode().strip()

    def _git(self, *args):
        return self._git_at(self.root, *args)

    def _record(self):
        stat_result = self.root.lstat()
        return {
            "schema_version": 1,
            "task_id": self.task_id,
            "trusted_agent_id": "codex-test",
            "session_ref": "test-session",
            "root_realpath": str(self.root),
            "physical_identity": {"device": stat_result.st_dev,
                                  "inode": stat_result.st_ino,
                                  "uid": stat_result.st_uid,
                                  "mode": oct(stat.S_IMODE(stat_result.st_mode))},
            "git_common_dir": str(self.common),
            "branch": self._git("branch", "--show-current"),
            "base_sha": self._git("rev-parse", "HEAD"),
            "head_sha": self._git("rev-parse", "HEAD"),
            "state": "ACTIVE",
            "lock_state": "LOCKED",
        }

    def _write_record(self, record=None):
        self.record_path.write_text(json.dumps(record or self._record()), encoding="utf-8")
        os.chmod(self.record_path, 0o600)

    def _main_at_root(self):
        previous = Path.cwd()
        os.chdir(self.root)
        try:
            script_path = self.root / "tools" / "llmtool" / "scripts" / "d6_worktree_env.py"
            with mock.patch.object(d6env, "__file__", str(script_path)):
                return d6env.main(["--task-id", self.task_id])
        finally:
            os.chdir(previous)

    def test_valid_locked_registration_invokes_existing_make_target(self):
        with mock.patch.object(d6env, "_invoke_bootstrap", return_value=0) as invoke:
            self.assertEqual(self._main_at_root(), 0)
        invoke.assert_called_once_with(self.root, self.task_id)

    def test_bootstrap_invokes_existing_make_target_without_shell(self):
        makefile_dir = self.root / "tools" / "llmtool"
        result = type("Result", (), {"returncode": 0})()
        with mock.patch.object(d6env, "_run_make", return_value=result) as run:
            self.assertEqual(d6env._invoke_bootstrap(self.root, self.task_id), 0)
        argv = run.call_args.args[0]
        self.assertEqual(argv[:4], ["/usr/bin/make", "-C", str(makefile_dir),
                                    "env-bootstrap"])
        self.assertIn("WORKTREE_ENV_ROOT=../..", argv)
        self.assertIn("WORKTREE_ENV_PYTHON=" + d6env.shlex.quote(sys.executable), argv)
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_interpreter_path_with_spaces_is_shell_quoted(self):
        makefile_dir = self.root / "tools" / "llmtool"
        result = type("Result", (), {"returncode": 0})()
        with mock.patch.object(d6env.sys, "executable", "/opt/Python Home/bin/python3"):
            with mock.patch.object(d6env, "_run_make", return_value=result) as run:
                self.assertEqual(d6env._invoke_bootstrap(self.root, self.task_id), 0)
        self.assertIn("WORKTREE_ENV_PYTHON='/opt/Python Home/bin/python3'",
                      run.call_args.args[0])

    def test_record_head_mismatch_fails_before_bootstrap(self):
        record = self._record()
        record["head_sha"] = "0" * 40
        self._write_record(record)
        with mock.patch.object(d6env, "_invoke_bootstrap") as invoke:
            self.assertEqual(self._main_at_root(), 1)
        invoke.assert_not_called()

    def test_unlocked_git_worktree_fails_even_if_record_claims_locked(self):
        self._git("worktree", "unlock", str(self.root))
        with mock.patch.object(d6env, "_invoke_bootstrap") as invoke:
            self.assertEqual(self._main_at_root(), 1)
        invoke.assert_not_called()

    def test_branch_mismatch_fails_before_bootstrap(self):
        record = self._record()
        record["branch"] = "feat/another-task"
        self._write_record(record)
        with mock.patch.object(d6env, "_invoke_bootstrap") as invoke:
            self.assertEqual(self._main_at_root(), 1)
        invoke.assert_not_called()

    def test_repair_state_fails_closed(self):
        record = self._record()
        record["state"] = "REPAIR"
        self._write_record(record)
        with mock.patch.object(d6env, "_invoke_bootstrap") as invoke:
            self.assertEqual(self._main_at_root(), 1)
        invoke.assert_not_called()

    def test_non_private_worktree_mode_fails_closed(self):
        os.chmod(self.root, 0o750)
        with mock.patch.object(d6env, "_invoke_bootstrap") as invoke:
            self.assertEqual(self._main_at_root(), 1)
        invoke.assert_not_called()

    def test_changed_makefile_fails_before_bootstrap(self):
        makefile = self.root / "tools" / "llmtool" / "Makefile"
        makefile.write_text("env-bootstrap:\n\t@echo changed\n", encoding="utf-8")
        with mock.patch.object(d6env, "_invoke_bootstrap") as invoke:
            self.assertEqual(self._main_at_root(), 1)
        invoke.assert_not_called()

    def test_changed_bootstrap_script_fails_before_bootstrap(self):
        script = self.root / "tools" / "llmtool" / "scripts" / "worktree_env.py"
        script.write_text("# changed after registration\n", encoding="utf-8")
        with mock.patch.object(d6env, "_invoke_bootstrap") as invoke:
            self.assertEqual(self._main_at_root(), 1)
        invoke.assert_not_called()

    def test_changed_preflight_helper_fails_before_bootstrap(self):
        script = self.root / "tools" / "llmtool" / "scripts" / "d6_worktree_env.py"
        script.write_text("# changed after registration\n", encoding="utf-8")
        with mock.patch.object(d6env, "_invoke_bootstrap") as invoke:
            self.assertEqual(self._main_at_root(), 1)
        invoke.assert_not_called()

    def test_source_change_between_checks_fails_before_make(self):
        original = d6env.validate_registered_worktree
        calls = []

        def validate_then_change(task_id, root=None):
            result = original(task_id, root)
            calls.append(True)
            if len(calls) == 1:
                makefile = self.root / "tools" / "llmtool" / "Makefile"
                makefile.write_text("env-bootstrap:\n\t@echo raced\n", encoding="utf-8")
            return result

        with mock.patch.object(d6env, "validate_registered_worktree",
                               side_effect=validate_then_change) as validate:
            with mock.patch.object(d6env, "_run_make") as run_make:
                self.assertEqual(self._main_at_root(), 1)
        self.assertEqual(validate.call_count, 2)
        self.assertEqual(len(calls), 1)
        run_make.assert_not_called()

    def test_unregistered_task_fails_before_bootstrap(self):
        self.record_path.unlink()
        with mock.patch.object(d6env, "_invoke_bootstrap") as invoke:
            self.assertEqual(self._main_at_root(), 1)
        invoke.assert_not_called()

    def test_symlinked_record_fails_before_bootstrap(self):
        self.record_path.unlink()
        target = Path(self.temp.name) / "record.json"
        target.write_text(json.dumps(self._record()), encoding="utf-8")
        self.record_path.symlink_to(target)
        with mock.patch.object(d6env, "_invoke_bootstrap") as invoke:
            self.assertEqual(self._main_at_root(), 1)
        invoke.assert_not_called()


if __name__ == "__main__":
    unittest.main()
