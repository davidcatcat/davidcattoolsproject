"""Real local-Git integration tests for the D6 create/bootstrap handoff."""

from __future__ import annotations

import contextlib
import fcntl
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
        shutil.copy2(SCRIPTS / "d6_worktree_lifecycle.py",
                     scripts / "d6_worktree_lifecycle.py")
        (tools / ".gitignore").write_bytes(
            (Path(__file__).resolve().parents[1] / ".gitignore").read_bytes())
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
        trusted_lifecycle = (self.source / "tools" / "llmtool" / "scripts" /
                             "d6_worktree_lifecycle.py")
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "D6_INTEGRATOR_DISPATCHER_ROOT", self.source), \
                patch.object(lifecycle, "__file__", str(trusted_lifecycle)), \
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
        marker = record["env2_pass_marker"]
        self.assertEqual(marker["result"], "PASS")
        self.assertEqual(marker["task_id"], "d6-integration-test")
        self.assertEqual(marker["root_realpath"], str(root))
        self.assertEqual(marker["submitted_head_sha"], self.base)
        self.assertEqual(marker["current_head_sha"], self.base)
        self.assertEqual(marker["environment_fingerprint"], _actual_cache_fingerprint())
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
                patch.object(lifecycle, "D6_INTEGRATOR_DISPATCHER_ROOT", self.source), \
                patch.object(lifecycle, "__file__", str(trusted_lifecycle)), \
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

    def test_committed_task_tree_lifecycle_cannot_bootstrap_from_itself(self):
        task_root = self.tasks / "committed-task-tree"
        subprocess.run(["/usr/bin/git", "clone", "-b", "main", str(self.source),
                        str(task_root)], check=True, capture_output=True)
        task_lifecycle = (task_root / "tools" / "llmtool" / "scripts" /
                          "d6_worktree_lifecycle.py")
        marker_path = self.root / "committed-task-lifecycle-ran"
        task_lifecycle.write_text(
            "from pathlib import Path\nPath(" + repr(str(marker_path)) +
            ").write_text('ran')\n", encoding="utf-8")
        subprocess.run(["/usr/bin/git", "-c", "user.name=Task Fixture",
                        "-c", "user.email=task@example.invalid", "add", "tools"],
                       cwd=task_root, check=True)
        subprocess.run(["/usr/bin/git", "-c", "user.name=Task Fixture",
                        "-c", "user.email=task@example.invalid", "commit", "-m",
                        "committed task lifecycle descendant"],
                       cwd=task_root, check=True, capture_output=True)

        with patch.object(lifecycle, "D6_INTEGRATOR_DISPATCHER_ROOT", self.source), \
                patch.object(lifecycle, "__file__", str(task_lifecycle)):
            with self.assertRaises(lifecycle.LifecycleError) as raised:
                lifecycle._bootstrap(self.source, "test-task")

        self.assertEqual(raised.exception.code, "ENV_BOOTSTRAP_SOURCE_UNTRUSTED")
        self.assertFalse(marker_path.exists())

    def test_integrator_approval_bootstraps_exact_helper_blob_from_shared_store(self):
        task_id = "d6-approved-source-test"
        trusted_lifecycle = (self.source / "tools" / "llmtool" / "scripts" /
                             "d6_worktree_lifecycle.py")
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "D6_INTEGRATOR_DISPATCHER_ROOT", self.source), \
                patch.object(lifecycle, "__file__", str(trusted_lifecycle)):
            root = lifecycle.create_worktree(
                canonical_root=self.source, workspace_root=self.tasks,
                task_id=task_id, agent_id="codex-developer-test",
                session_ref="live test session", lease_ref="live test lease",
                branch="test/d6-approved-source", base_sha=self.base)

        marker_path = self.root / "approved-helper-ran"
        helper_bytes = (
            "from pathlib import Path\nPath(" + repr(str(marker_path)) +
            ").write_text('ran')\n").encode()
        helper_path = root / "tools" / "llmtool" / "scripts" / "d6_worktree_env.py"
        helper_path.write_bytes(helper_bytes)
        subprocess.run(["/usr/bin/git", "add", "tools/llmtool/scripts/d6_worktree_env.py"],
                       cwd=root, check=True)
        subprocess.run(["/usr/bin/git", "-c", "user.name=Task Fixture",
                        "-c", "user.email=task@example.invalid", "commit", "-m",
                        "approved helper candidate"], cwd=root, check=True,
                       capture_output=True)
        approved_sha = subprocess.check_output(
            ["/usr/bin/git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        record_path = (self.source / ".git" / "llmtool-worktrees" /
                       (task_id + ".json"))
        lock_path = record_path.parent / ".lifecycle.lock"
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            record = lifecycle._read_registry_record(record_path)
            record["head_sha"] = approved_sha
            record["env2_approved_tooling_sha"] = approved_sha
            lifecycle._replace_record(record_path, record)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

        captured = []
        real_run = subprocess.run

        def capture_candidate(argv, *args, **kwargs):
            if argv and argv[0] == sys.executable and "-c" in argv:
                captured.append((argv, kwargs))
                return subprocess.CompletedProcess(argv, 0)
            return real_run(argv, *args, **kwargs)

        with patch.object(lifecycle, "D6_INTEGRATOR_DISPATCHER_ROOT", self.source), \
                patch.object(lifecycle, "__file__", str(trusted_lifecycle)), \
                patch.object(lifecycle.subprocess, "run", side_effect=capture_candidate):
            lifecycle._bootstrap(root, task_id)

        self.assertEqual(len(captured), 1)
        argv, kwargs = captured[0]
        self.assertEqual(argv[argv.index("-c") + 1], helper_bytes.decode())
        self.assertIn("--worktree-root", argv)
        self.assertEqual(argv[argv.index("--worktree-root") + 1], str(root))
        self.assertEqual(Path(kwargs["cwd"]), self.source)
        self.assertFalse(marker_path.exists())

        lock_fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            record = lifecycle._read_registry_record(record_path)
            record["env2_approved_tooling_sha"] = 7
            lifecycle._replace_record(record_path, record)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        with patch.object(lifecycle, "D6_INTEGRATOR_DISPATCHER_ROOT", self.source), \
                patch.object(lifecycle, "__file__", str(trusted_lifecycle)):
            with self.assertRaises(lifecycle.LifecycleError) as raised:
                lifecycle._bootstrap(root, task_id)
        self.assertEqual(raised.exception.code, "ENV_BOOTSTRAP_SOURCE_UNTRUSTED")

    def test_execute_approved_lifecycle_runs_exact_blob_and_rejects_task_path(self):
        task_id = "d6-approved-lifecycle-runner-test"
        trusted_lifecycle = (self.source / "tools" / "llmtool" / "scripts" /
                             "d6_worktree_lifecycle.py")
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "D6_INTEGRATOR_DISPATCHER_ROOT", self.source), \
                patch.object(lifecycle, "__file__", str(trusted_lifecycle)):
            root = lifecycle.create_worktree(
                canonical_root=self.source, workspace_root=self.tasks,
                task_id=task_id, agent_id="codex-developer-test",
                session_ref="live test session", lease_ref="live test lease",
                branch="test/d6-approved-lifecycle-runner", base_sha=self.base)

        route_marker = self.root / "approved-lifecycle-source-ran"
        candidate_lifecycle = (
            "from pathlib import Path\nimport sys\nPath(" + repr(str(route_marker)) +
            ").write_text(__file__ + '\\n' + '\\n'.join(sys.argv[1:]))\n").encode()
        lifecycle_path = (root / "tools" / "llmtool" / "scripts" /
                          "d6_worktree_lifecycle.py")
        lifecycle_path.write_bytes(candidate_lifecycle)
        subprocess.run(["/usr/bin/git", "add", "tools/llmtool/scripts/d6_worktree_lifecycle.py"],
                       cwd=root, check=True)
        subprocess.run(["/usr/bin/git", "-c", "user.name=Task Fixture",
                        "-c", "user.email=task@example.invalid", "commit", "-m",
                        "approved lifecycle candidate"], cwd=root, check=True,
                       capture_output=True)
        approved_sha = subprocess.check_output(
            ["/usr/bin/git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        record_path = (self.source / ".git" / "llmtool-worktrees" /
                       (task_id + ".json"))
        lock_path = record_path.parent / ".lifecycle.lock"
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            record = lifecycle._read_registry_record(record_path)
            record["state"] = "REPAIR"
            record["head_sha"] = approved_sha
            record["env2_approved_tooling_sha"] = approved_sha
            lifecycle._replace_record(record_path, record)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

        with patch.object(lifecycle, "D6_INTEGRATOR_DISPATCHER_ROOT", self.source), \
                patch.object(lifecycle, "__file__", str(trusted_lifecycle)):
            self.assertEqual(lifecycle._main([
                "--execute-approved-lifecycle",
                "--resume-task-id", task_id, "--resume-root", str(root),
                "--repo-root", str(self.source),
            ]), 0)
        self.assertTrue(route_marker.is_file())
        route_output = route_marker.read_text(encoding="utf-8").splitlines()
        self.assertEqual(route_output[0], str(trusted_lifecycle))
        self.assertEqual(route_output[1:], ["--resume-task-id", task_id,
                                             "--resume-root", str(root),
                                             "--repo-root", str(self.source)])
        self.assertFalse(str(lifecycle_path) in route_output)

        task_marker = self.root / "untrusted-task-lifecycle-ran"
        lifecycle_path.write_text(
            "from pathlib import Path\nPath(" + repr(str(task_marker)) +
            ").write_text('ran')\n", encoding="utf-8")
        subprocess.run(["/usr/bin/git", "add", "tools/llmtool/scripts/d6_worktree_lifecycle.py"],
                       cwd=root, check=True)
        subprocess.run(["/usr/bin/git", "-c", "user.name=Task Fixture",
                        "-c", "user.email=task@example.invalid", "commit", "-m",
                        "unapproved lifecycle descendant"], cwd=root, check=True,
                       capture_output=True)
        with patch.object(lifecycle, "D6_INTEGRATOR_DISPATCHER_ROOT", self.source), \
                patch.object(lifecycle, "__file__", str(lifecycle_path)):
            with self.assertRaises(lifecycle.LifecycleError) as raised:
                lifecycle._bootstrap(root, task_id)
        self.assertEqual(raised.exception.code, "ENV_BOOTSTRAP_SOURCE_UNTRUSTED")
        self.assertFalse(task_marker.exists())
        final_record = lifecycle._read_registry_record(record_path)
        self.assertEqual(final_record["state"], "REPAIR")
        self.assertEqual(final_record["env2_approved_tooling_sha"], approved_sha)

    def _revalidate_d6(self, root, task_id):
        previous = Path.cwd()
        os.chdir(root)
        try:
            script = root / "tools" / "llmtool" / "scripts" / "d6_worktree_env.py"
            with patch.object(d6env, "__file__", str(script)):
                return d6env.main(["--task-id", task_id,
                                   "--worktree-root", str(root)])
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

    def test_bootstrap_failure_relocks_registry_and_records_repair_without_marker(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_bootstrap",
                             side_effect=lifecycle.LifecycleError("ENV_BOOTSTRAP_FAILED")), \
                contextlib.redirect_stdout(output):
            with self.assertRaises(lifecycle.LifecycleError) as raised:
                lifecycle.create_worktree(
                    canonical_root=self.source,
                    workspace_root=self.tasks,
                    task_id="d6-bootstrap-failure-test",
                    agent_id="codex-developer-test",
                    session_ref="live test session",
                    lease_ref="live test lease",
                    branch="fix/d6-bootstrap-failure-test",
                    base_sha=self.base,
                )
        self.assertEqual(raised.exception.code, "ENV_BOOTSTRAP_FAILED")
        record_path = (self.source / ".git" / "llmtool-worktrees" /
                       "d6-bootstrap-failure-test.json")
        record = json.loads(record_path.read_text())
        self.assertEqual(record["state"], "REPAIR")
        self.assertEqual(record["lock_state"], "LOCKED")
        self.assertNotIn("env2_pass_marker", record)
        self.assertNotIn("D6_WORKTREE_READY", output.getvalue())

    def test_bootstrap_failure_preserves_concurrent_registry_update(self):
        record_path = (self.source / ".git" / "llmtool-worktrees" /
                       "d6-bootstrap-cas-test.json")
        lock_path = record_path.parent / ".lifecycle.lock"

        def update_registry_then_fail(root, task_id):
            lock_fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                current = lifecycle._read_registry_record(record_path)
                current["env2_pass_marker"] = {
                    "result": "PASS", "task_id": task_id,
                    "root_realpath": str(root), "reviewed_update": True,
                }
                lifecycle._replace_record(record_path, current)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
            raise lifecycle.LifecycleError("ENV_BOOTSTRAP_FAILED")

        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_bootstrap", side_effect=update_registry_then_fail):
            with self.assertRaises(lifecycle.LifecycleError) as raised:
                lifecycle.create_worktree(
                    canonical_root=self.source,
                    workspace_root=self.tasks,
                    task_id="d6-bootstrap-cas-test",
                    agent_id="codex-developer-test",
                    session_ref="live test session",
                    lease_ref="live test lease",
                    branch="fix/d6-bootstrap-cas-test",
                    base_sha=self.base,
                )

        self.assertEqual(raised.exception.code, "D6_REGISTRY_CHANGED")
        record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(record["state"], "ACTIVE")
        self.assertTrue(record["env2_pass_marker"]["reviewed_update"])

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


class GitTimeoutTests(unittest.TestCase):
    """A stuck git lookup stops with a stable code instead of hanging."""

    def _timeout(self, *args, **kwargs):
        raise subprocess.TimeoutExpired(args[0] if args else "git", kwargs.get("timeout"))

    def test_read_only_lookup_times_out_as_git_unavailable(self):
        with patch.object(lifecycle.subprocess, "run", side_effect=self._timeout) as run:
            with self.assertRaises(lifecycle.LifecycleError) as raised:
                lifecycle._run_git(Path("/"), "rev-parse", "HEAD")
        self.assertEqual(raised.exception.code, "D6_GIT_UNAVAILABLE")
        self.assertEqual(run.call_args.kwargs["timeout"], lifecycle.GIT_TIMEOUT_SECONDS)

    def test_repository_changes_are_not_timed(self):
        done = subprocess.CompletedProcess([], 0, "", "")
        with patch.object(lifecycle.subprocess, "run", return_value=done) as run:
            lifecycle._run_git(Path("/"), "worktree", "lock", "/x", check=False, timeout=None)
        self.assertIsNone(run.call_args.kwargs["timeout"])

    def test_dispatcher_git_preflight_times_out_as_bootstrap_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(os.path.realpath(tmp))
            trusted = root / "tools" / "llmtool" / "scripts" / "d6_worktree_lifecycle.py"
            with patch.object(lifecycle, "D6_INTEGRATOR_DISPATCHER_ROOT", root), \
                    patch.object(lifecycle, "__file__", str(trusted)), \
                    patch.object(lifecycle.subprocess, "run", side_effect=self._timeout) as run:
                with self.assertRaises(lifecycle.LifecycleError) as raised:
                    lifecycle._trusted_dispatcher_git()
        self.assertEqual(raised.exception.code, "ENV_BOOTSTRAP_UNAVAILABLE")
        self.assertEqual(run.call_args.kwargs["timeout"], lifecycle.GIT_TIMEOUT_SECONDS)


if __name__ == "__main__":
    unittest.main()
