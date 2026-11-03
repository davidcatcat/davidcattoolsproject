"""Focused tests for ENV-2 D6 lifecycle integration; no pip/provider calls."""

import json
import io
import os
import platform
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import d6_worktree_env as d6env  # noqa: E402
import worktree_env as worktreeenv  # noqa: E402


def _actual_cache_fingerprint():
    return worktreeenv.fingerprint_id({
        "implementation": sys.implementation.name,
        "python_version": "%d.%d.%d" % sys.version_info[:3],
        "platform": sys.platform,
        "machine": platform.machine(),
    })


class D6WorktreeEnvironmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._fixture_temp = tempfile.TemporaryDirectory(prefix="d6-env-fixture-src-")
        cls._template_base = Path(cls._fixture_temp.name)
        cls._template_source = cls._template_base / "source"
        cls._template_source.mkdir(mode=0o700)
        cls._template_root = cls._template_base / "tasks" / "task-root"
        cls._template_root.parent.mkdir(mode=0o700)
        cls._git_run(cls._template_source, "init", "-b", "main")
        cls._git_run(cls._template_source, "config", "user.name", "D6 Test")
        cls._git_run(cls._template_source, "config", "user.email", "d6-test@example.invalid")
        cls._git_run(cls._template_source, "remote", "add", "origin",
                     "git@github.com:davidcatcat/davidcattoolsproject.git")
        makefile_dir = cls._template_source / "tools" / "llmtool"
        (makefile_dir / "scripts").mkdir(parents=True)
        (makefile_dir / "Makefile").write_text("env-bootstrap:\n\t@true\n", encoding="utf-8")
        (makefile_dir / "scripts" / "worktree_env.py").write_text(
            "# test fixture source\n", encoding="utf-8")
        (makefile_dir / "scripts" / "d6_worktree_env.py").write_bytes(
            (SCRIPTS / "d6_worktree_env.py").read_bytes())
        (makefile_dir / ".gitignore").write_bytes(
            (Path(__file__).resolve().parents[1] / ".gitignore").read_bytes())
        (makefile_dir / "worktree-env.lock").write_text(
            "[base]\nexample==1.0 --hash=sha256:" + "a" * 64 + "\n",
            encoding="utf-8")
        (makefile_dir / "pyproject.toml").write_text(
            "[build-system]\nrequires = ['setuptools>=68']\n"
            "build-backend = 'setuptools.build_meta'\n",
            encoding="utf-8")
        (cls._template_source / ".gitignore").write_text(
            "/tools/llmtool/.venv/\n/tools/llmtool/build/\n",
            encoding="utf-8")
        (cls._template_source / "README").write_text("test\n", encoding="utf-8")
        cls._git_run(cls._template_source, "add", ".")
        cls._git_run(cls._template_source, "commit", "-m", "fixture")
        cls._git_run(cls._template_source, "worktree", "add", "-b", "feat/test-d6-env",
                     str(cls._template_root), "HEAD")
        cls._template_root = Path(os.path.realpath(cls._template_root))
        os.chmod(cls._template_root, 0o700)
        cls._git_run(cls._template_source, "worktree", "lock", str(cls._template_root),
                     "--reason", "test lock")
        cls._template_head_sha = cls._git_run(cls._template_source, "rev-parse", "HEAD")

    @classmethod
    def tearDownClass(cls):
        cls._fixture_temp.cleanup()
        super().tearDownClass()

    @classmethod
    def _git_run(cls, root, *args):
        result = subprocess.run(["/usr/bin/git", "-C", str(root), *args],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                check=False)
        if result.returncode:
            raise AssertionError("git fixture setup failed: " + result.stderr.decode(errors="replace"))
        return result.stdout.decode().strip()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="d6-env-preflight-")
        self.addCleanup(self.temp.cleanup)
        base = Path(os.path.realpath(self.temp.name))
        self.source = base / "source"
        self.root = base / "tasks" / "task-root"
        shutil.copytree(str(self._template_source), str(self.source))
        shutil.copytree(str(self._template_root), str(self.root))
        os.chmod(self.source, 0o700)
        os.chmod(self.root.parent, 0o700)
        os.chmod(self.root, 0o700)
        self.common = self.source / ".git"
        os.chmod(self.common, 0o700)
        wt_admin = self.common / "worktrees" / self.root.name
        (self.root / ".git").write_text(f"gitdir: {wt_admin}\n", encoding="utf-8")
        (wt_admin / "gitdir").write_text(f"{self.root / '.git'}\n", encoding="utf-8")
        self.registry = None
        self.task_id = "issue267-test-task"
        self._branch = "feat/test-d6-env"
        self._head_sha = self._template_head_sha
        self.registry = self.common / "llmtool-worktrees"
        self.registry.mkdir(mode=0o700)
        os.chmod(self.registry, 0o700)
        lock_path = self.registry / ".lifecycle.lock"
        lock_path.touch(mode=0o600)
        os.chmod(lock_path, 0o600)
        self.record_path = self.registry / (self.task_id + ".json")
        self._write_record()
        self.trust_home = base / "trust-home"
        trust_dir = self.trust_home / ".config" / "llmtool"
        trust_dir.mkdir(parents=True, mode=0o700)
        for directory in (self.trust_home, self.trust_home / ".config", trust_dir):
            os.chmod(directory, 0o700)
        trust_value = {
            "schema_version": 1, "canonical_root": str(self.source),
            "workspace_root": str(base), "tasks_root": str(self.root.parent),
            "repository": {"slug": "davidcatcat/davidcattoolsproject",
                           "allowed_origins": [
                               "git@github.com:davidcatcat/davidcattoolsproject.git"],
                           "approval_refs": {}},
            "ci_trust": {}, "tools": {},
        }
        trust_path = trust_dir / "trust.json"
        trust_path.write_text(json.dumps(trust_value), encoding="utf-8")
        os.chmod(trust_path, 0o600)
        self._original_load_trust = d6env._load_trust
        self._trust_patch = mock.patch.object(
            d6env, "_load_trust",
            side_effect=lambda: self._original_load_trust(home=str(self.trust_home)))
        self._trust_patch.start()
        self.addCleanup(self._trust_patch.stop)

    def _git_at(self, root, *args):
        return self._git_run(root, *args)

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
            "branch": self._branch,
            "base_sha": self._head_sha,
            "head_sha": self._head_sha,
            "state": "ACTIVE",
            "lock_state": "LOCKED",
            "retention_paths": ["tools/llmtool/.venv/", "tools/llmtool/build/",
                                "tools/llmtool/.d6-bootstrap-report.json"],
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
                return d6env.main(["--task-id", self.task_id,
                                   "--worktree-root", str(self.root),
                                   "--trust-sha256", d6env._load_trust()["sha256"]])
        finally:
            os.chdir(previous)

    def _make_result(self, returncode=0, *, report=None, stderr=b""):
        payload = report or {
            "status": "env_created",
            "cache_status": "miss",
            "cache_fingerprint": _actual_cache_fingerprint(),
            "total_s": 2.5,
            "timings": {"cache": 1.0, "deps": 0.5, "project": 0.2},
            "operations": {
                "pip_download_command_count": 1,
                "downloaded_wheels": [{"name": "example", "version": "1.0"}],
                "downloaded_wheel_count": 1,
                "wheel_source_copy_count": 0,
                "dependency_install_command_count": 1,
                "dependency_installed": [{"name": "example", "version": "1.0"}],
                "dependency_install_package_count": 1,
                "project_install_command_count": 1,
                "project_installed": [{"name": "llmtool", "version": "0.1.0"}],
                "project_install_package_count": 1,
            },
            "packages": {"example": "1.0"},
        }
        return subprocess.CompletedProcess(
            ["/usr/bin/make"], returncode,
            stdout=b"make recipe output\n" + json.dumps(payload).encode("utf-8"),
            stderr=stderr,
        )

    def test_valid_locked_registration_invokes_existing_make_target(self):
        with mock.patch.object(d6env, "_invoke_bootstrap", return_value=0) as invoke:
            self.assertEqual(self._main_at_root(), 0)
        invoke.assert_called_once_with(self.root, self.task_id)

    def test_main_rejects_changed_trust_digest_before_bootstrap(self):
        output = io.StringIO()
        with mock.patch.object(d6env, "_invoke_bootstrap") as invoke, \
                mock.patch("sys.stderr", output):
            self.assertEqual(d6env.main([
                "--task-id", self.task_id, "--worktree-root", str(self.root),
                "--trust-sha256", "0" * 64]), 1)
        self.assertIn("TRUST_CHANGED_DURING_RUN", output.getvalue())
        invoke.assert_not_called()

    def test_bootstrap_invokes_existing_make_target_without_shell(self):
        makefile_dir = self.root / "tools" / "llmtool"
        result = self._make_result(stderr=b"secret-token=https://u:p@host/?token=secret\n")
        with mock.patch.object(d6env, "_run_make", return_value=result) as run:
            self.assertEqual(d6env._invoke_bootstrap(self.root, self.task_id), 0)
        argv = run.call_args.args[0]
        self.assertEqual(argv[:6], ["/usr/bin/make", "-f", "Makefile", "-C",
                                    str(makefile_dir), "env-bootstrap"])
        self.assertIn("WORKTREE_ENV_ROOT=../..", argv)
        self.assertIn("WORKTREE_ENV_PYTHON=" + d6env.shlex.quote(sys.executable) +
                      " -I -B", argv)
        self.assertIn("env", run.call_args.kwargs)
        self.assertNotIn("shell", run.call_args.kwargs)
        report_path = self.root / "tools" / "llmtool" / ".d6-bootstrap-report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(stat.S_IMODE(report_path.stat().st_mode), 0o600)
        self.assertEqual(report["result"], "PASS")
        self.assertEqual(report["task_id"], self.task_id)
        self.assertEqual(report["root_realpath"], str(self.root))
        self.assertEqual(report["head_sha"], self._git("rev-parse", "HEAD"))
        self.assertEqual(report["cache_fingerprint"], _actual_cache_fingerprint())
        self.assertEqual(report["operations"]["downloaded_wheel_count"], 1)
        self.assertEqual(report["operations"]["dependency_install_package_count"], 1)
        record = json.loads(self.record_path.read_text(encoding="utf-8"))
        marker = record["env2_pass_marker"]
        self.assertEqual(marker["result"], "PASS")
        self.assertEqual(marker["task_id"], self.task_id)
        self.assertEqual(marker["root_realpath"], str(self.root))
        self.assertEqual(marker["submitted_head_sha"], self._git("rev-parse", "HEAD"))
        self.assertEqual(marker["current_head_sha"], self._git("rev-parse", "HEAD"))
        self.assertEqual(marker["environment_fingerprint"], _actual_cache_fingerprint())
        self.assertEqual(marker["report_sha256"],
                         __import__("hashlib").sha256(report_path.read_bytes()).hexdigest())
        self.assertEqual(report["operations"]["project_install_package_count"], 1)
        self.assertNotIn("secret-token", report_path.read_text(encoding="utf-8"))
        self.assertNotIn("token=secret", report_path.read_text(encoding="utf-8"))
        # The report itself is in retention_paths and must not break revalidation.
        with mock.patch.object(d6env, "_run_make", return_value=result):
            self.assertEqual(self._main_at_root(), 0)

    def test_failed_bootstrap_clears_previous_pass_marker(self):
        record = self._record()
        record["env2_pass_marker"] = {"result": "PASS", "task_id": self.task_id}
        self._write_record(record)
        failed = self._make_result(returncode=1,
                                   report={"status": "error", "code": "PIP_FAILED"})
        with mock.patch.object(d6env, "_run_make", return_value=failed):
            self.assertEqual(d6env._invoke_bootstrap(self.root, self.task_id), 1)
        updated = json.loads(self.record_path.read_text(encoding="utf-8"))
        self.assertNotIn("env2_pass_marker", updated)
        report = json.loads((self.root / "tools" / "llmtool" /
                             ".d6-bootstrap-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["result"], "FAIL")

    def test_interpreter_path_with_spaces_is_shell_quoted(self):
        makefile_dir = self.root / "tools" / "llmtool"
        result = self._make_result()
        with mock.patch.object(d6env.sys, "executable", "/opt/Python Home/bin/python3"):
            with mock.patch.object(d6env, "_run_make", return_value=result) as run:
                self.assertEqual(d6env._invoke_bootstrap(self.root, self.task_id), 0)
        self.assertIn("WORKTREE_ENV_PYTHON='/opt/Python Home/bin/python3' -I -B",
                      run.call_args.args[0])

    def test_invalid_bootstrap_output_records_unknown_without_raw_logs(self):
        result = subprocess.CompletedProcess(
            ["/usr/bin/make"], 0,
            stdout=b"https://user:password@example.invalid/?token=secret",
            stderr=b"restricted raw diagnostic",
        )
        with mock.patch.object(d6env, "_run_make", return_value=result):
            with self.assertRaises(d6env.PreflightError) as raised:
                d6env._invoke_bootstrap(self.root, self.task_id)
        self.assertEqual(raised.exception.code, "ENV_BOOTSTRAP_REPORT_INVALID")
        report_path = self.root / "tools" / "llmtool" / ".d6-bootstrap-report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["result"], "BLOCKED")
        self.assertEqual(report["cache_fingerprint"], "UNKNOWN")
        self.assertEqual(report["operations"], "UNKNOWN")
        evidence = report_path.read_text(encoding="utf-8")
        self.assertNotIn("password", evidence)
        self.assertNotIn("token=secret", evidence)
        self.assertNotIn("restricted raw diagnostic", evidence)

    def test_cache_fingerprint_requires_real_worktree_env_format(self):
        generated = _actual_cache_fingerprint()
        self.assertTrue(d6env._valid_cache_fingerprint(generated))
        for value in ("a" * 64, "../" + generated,
                      generated + "abcdef"):
            with self.subTest(value=value):
                self.assertFalse(d6env._valid_cache_fingerprint(value))

    def test_make_and_python_environment_overrides_are_scrubbed(self):
        result = self._make_result()
        injected = {
            "MAKEFILES": "attacker.mk",
            "MAKEFLAGS": "-f attacker.mk",
            "GNUMAKEFLAGS": "--eval=bad",
            "MFLAGS": "-f attacker.mk",
            "PYTHONPATH": "/tmp/shadow",
            "PYTHONHOME": "/tmp/other-python",
        }
        with mock.patch.dict(os.environ, injected, clear=False):
            with mock.patch.object(d6env, "_run_make", return_value=result) as run:
                self.assertEqual(self._main_at_root(), 0)
        make_env = run.call_args.kwargs["env"]
        for name in injected:
            self.assertNotIn(name, make_env)

    def test_registered_ignored_build_output_is_allowed(self):
        output = self.root / "tools" / "llmtool" / "build" / "generated.txt"
        output.parent.mkdir(parents=True)
        output.write_text("registered retention output\n", encoding="utf-8")
        result = self._make_result()
        with mock.patch.object(d6env, "_run_make", return_value=result) as run:
            self.assertEqual(self._main_at_root(), 0)
        run.assert_called_once()

    def test_bootstrap_report_is_ignored_by_repository_configuration(self):
        report = self.root / "tools" / "llmtool" / ".d6-bootstrap-report.json"
        report.write_text("{}\n", encoding="utf-8")
        result = subprocess.run(
            ["/usr/bin/git", "-C", str(self.root), "check-ignore", "-v",
             "tools/llmtool/.d6-bootstrap-report.json"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("tools/llmtool/.gitignore", result.stdout)
        self.assertIn(".d6-bootstrap-report.json", result.stdout)

    def test_untracked_gnumakefile_fails_before_make(self):
        (self.root / "tools" / "llmtool" / "GNUmakefile").write_text(
            "env-bootstrap:\n\t@echo shadow\n", encoding="utf-8")
        with mock.patch.object(d6env, "_run_make") as run:
            self.assertEqual(self._main_at_root(), 1)
        run.assert_not_called()

    def test_untracked_python_shadow_module_fails_before_make(self):
        shadow = self.root / "tools" / "llmtool" / "scripts" / "json.py"
        shadow.write_text("raise RuntimeError('shadow')\n", encoding="utf-8")
        with mock.patch.object(d6env, "_run_make") as run:
            self.assertEqual(self._main_at_root(), 1)
        run.assert_not_called()

    def test_ignored_python_shadow_module_fails_before_make(self):
        ignore = self.root / ".gitignore"
        ignore.write_text(ignore.read_text(encoding="utf-8") +
                          "/tools/llmtool/scripts/json.py\n", encoding="utf-8")
        shadow = self.root / "tools" / "llmtool" / "scripts" / "json.py"
        shadow.write_text("raise RuntimeError('ignored shadow')\n", encoding="utf-8")
        with mock.patch.object(d6env, "_run_make") as run:
            self.assertEqual(self._main_at_root(), 1)
        run.assert_not_called()

    def test_untracked_inventory_drift_between_validations_fails_before_make(self):
        original = d6env.validate_registered_worktree
        calls = []

        def validate_then_add_untracked(task_id, root=None, trust=None):
            result = original(task_id, root, trust)
            calls.append(True)
            if len(calls) == 1:
                shadow = self.root / "tools" / "llmtool" / "GNUmakefile"
                shadow.write_text("env-bootstrap:\n\t@echo raced\n", encoding="utf-8")
            return result

        with mock.patch.object(d6env, "validate_registered_worktree",
                               side_effect=validate_then_add_untracked) as validate:
            with mock.patch.object(d6env, "_run_make") as run_make:
                self.assertEqual(self._main_at_root(), 1)
        self.assertEqual(validate.call_count, 2)
        self.assertEqual(len(calls), 1)
        run_make.assert_not_called()

    def test_record_head_mismatch_fails_before_bootstrap(self):
        record = self._record()
        record["head_sha"] = "0" * 40
        self._write_record(record)
        with mock.patch.object(d6env, "_invoke_bootstrap") as invoke:
            self.assertEqual(self._main_at_root(), 1)
        invoke.assert_not_called()

    def test_registered_descendant_head_is_accepted_and_marker_binds_current_head(self):
        base = self._git("rev-parse", "HEAD")
        readme = self.root / "README"
        readme.write_text(readme.read_text(encoding="utf-8") + "advanced\n",
                          encoding="utf-8")
        self._git("add", "README")
        self._git("commit", "-m", "advance registered task head")
        head = self._git("rev-parse", "HEAD")
        record = self._record()
        record["base_sha"] = base
        record["head_sha"] = head
        self._write_record(record)
        result = self._make_result()
        with mock.patch.object(d6env, "_run_make", return_value=result):
            self.assertEqual(d6env._invoke_bootstrap(self.root, self.task_id), 0)
        updated = json.loads(self.record_path.read_text(encoding="utf-8"))
        self.assertEqual(updated["base_sha"], base)
        self.assertEqual(updated["head_sha"], head)
        self.assertEqual(updated["env2_pass_marker"]["submitted_head_sha"], head)
        self.assertEqual(updated["env2_pass_marker"]["current_head_sha"], head)
        self.assertEqual(updated["env2_pass_marker"]["tooling_sha"], base)

    def test_tooling_edit_requires_exact_integrator_approved_head(self):
        base = self._git("rev-parse", "HEAD")
        helper = self.root / "tools" / "llmtool" / "scripts" / "d6_worktree_env.py"
        helper.write_text(helper.read_text(encoding="utf-8") + "\n# reviewed tooling edit\n",
                          encoding="utf-8")
        self._git("add", "tools/llmtool/scripts/d6_worktree_env.py")
        self._git("commit", "-m", "edit bootstrap tooling")
        head = self._git("rev-parse", "HEAD")
        record = self._record()
        record["base_sha"] = base
        record["head_sha"] = head
        self._write_record(record)

        with mock.patch.object(d6env, "_invoke_bootstrap") as invoke:
            self.assertEqual(self._main_at_root(), 1)
        invoke.assert_not_called()
        self.assertNotIn("env2_pass_marker", json.loads(
            self.record_path.read_text(encoding="utf-8")))

        record["env2_approved_tooling_sha"] = head
        self._write_record(record)
        with mock.patch.object(d6env, "_run_make", return_value=self._make_result()):
            self.assertEqual(self._main_at_root(), 0)
        marker = json.loads(self.record_path.read_text(encoding="utf-8"))["env2_pass_marker"]
        self.assertEqual(marker["tooling_sha"], head)

    def test_invalid_tooling_approval_fails_before_make_or_marker_write(self):
        record = self._record()
        record["env2_approved_tooling_sha"] = "f" * 40
        self._write_record(record)
        with mock.patch.object(d6env, "_run_make") as run_make:
            self.assertEqual(self._main_at_root(), 1)
        run_make.assert_not_called()
        updated = json.loads(self.record_path.read_text(encoding="utf-8"))
        self.assertNotIn("env2_pass_marker", updated)

    def test_registered_divergent_base_is_rejected(self):
        tree = self._git("rev-parse", "HEAD^{tree}")
        unrelated_base = self._git("commit-tree", tree)
        record = self._record()
        record["base_sha"] = unrelated_base
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

    def test_repair_state_is_valid_for_registered_resume(self):
        record = self._record()
        record["state"] = "REPAIR"
        self._write_record(record)
        with mock.patch.object(d6env, "_invoke_bootstrap", return_value=0) as invoke:
            self.assertEqual(self._main_at_root(), 0)
        invoke.assert_called_once_with(self.root, self.task_id)

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

    def test_changed_dependency_lock_fails_before_bootstrap(self):
        lock = self.root / "tools" / "llmtool" / "worktree-env.lock"
        lock.write_text("[base]\nchanged==9.9 --hash=sha256:" + "b" * 64 + "\n",
                        encoding="utf-8")
        with mock.patch.object(d6env, "_invoke_bootstrap") as invoke:
            self.assertEqual(self._main_at_root(), 1)
        invoke.assert_not_called()

    def test_changed_build_metadata_fails_before_bootstrap(self):
        metadata = self.root / "tools" / "llmtool" / "pyproject.toml"
        metadata.write_text(
            "[build-system]\nrequires = ['setuptools>=1']\n"
            "build-backend = 'setuptools.build_meta'\n",
            encoding="utf-8")
        with mock.patch.object(d6env, "_invoke_bootstrap") as invoke:
            self.assertEqual(self._main_at_root(), 1)
        invoke.assert_not_called()

    def test_untracked_setup_hook_fails_before_bootstrap(self):
        hook = self.root / "tools" / "llmtool" / "setup.py"
        hook.write_text("from setuptools import setup\n", encoding="utf-8")
        with mock.patch.object(d6env, "_invoke_bootstrap") as invoke:
            self.assertEqual(self._main_at_root(), 1)
        invoke.assert_not_called()

    def test_source_change_between_checks_fails_before_make(self):
        original = d6env.validate_registered_worktree
        calls = []

        def validate_then_change(task_id, root=None, trust=None):
            result = original(task_id, root, trust)
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

    def test_lock_change_between_checks_fails_before_make(self):
        original = d6env.validate_registered_worktree
        calls = []

        def validate_then_change(task_id, root=None, trust=None):
            result = original(task_id, root, trust)
            calls.append(True)
            if len(calls) == 1:
                lock = self.root / "tools" / "llmtool" / "worktree-env.lock"
                lock.write_text("[base]\nraced==9.9 --hash=sha256:" + "c" * 64 + "\n",
                                encoding="utf-8")
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
