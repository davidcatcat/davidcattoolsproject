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
import time
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


def _with_child_trust_home(home, code):
    """Test-only wrapper: run the exact child code with pw_dir set to ``home``."""
    return (
        "import pwd as _fixture_pwd\n"
        "_fixture_getpwuid = _fixture_pwd.getpwuid\n"
        "def _fixture_temp_home(uid):\n"
        "    entry = _fixture_getpwuid(uid)\n"
        "    return _fixture_pwd.struct_passwd(\n"
        "        tuple(entry[:5]) + (" + repr(home) + ",) + tuple(entry[6:]))\n"
        "_fixture_pwd.getpwuid = _fixture_temp_home\n"
        "exec(compile(" + repr(code) + ", '<string>', 'exec'))\n")


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
        self.trust_home = self.root / "trust-home"
        trust_dir = self.trust_home / ".config" / "llmtool"
        trust_dir.mkdir(parents=True, mode=0o700)
        for directory in (self.trust_home, self.trust_home / ".config", trust_dir):
            directory.chmod(0o700)
        self.trust_path = trust_path = trust_dir / "trust.json"
        self._write_trust()
        self._original_load_trust = lifecycle._load_trust
        self._trust_patch = patch.object(
            lifecycle, "_load_trust",
            side_effect=lambda: self._original_load_trust(home=str(self.trust_home)))
        self._trust_patch.start()
        self.addCleanup(self._trust_patch.stop)
        self._original_env_load_trust = d6env._load_trust
        self._env_trust_patch = patch.object(
            d6env, "_load_trust",
            side_effect=lambda: self._original_env_load_trust(home=str(self.trust_home)))
        self._env_trust_patch.start()
        self.addCleanup(self._env_trust_patch.stop)
        # Bootstrapped children run the exact approved blob with ``-I -c``; they
        # read trust only from pwd.getpwuid(...).pw_dir.  The fixture points
        # that lookup at the temp trust home inside the child, so the real
        # ~/.config/llmtool/trust.json is never read and product code keeps no
        # env/CLI location override.
        real_run = subprocess.run
        trust_home = str(self.trust_home)

        def run_with_temp_trust_home(argv, *args, **kwargs):
            if isinstance(argv, list) and argv[:4] == [sys.executable, "-B", "-I", "-c"]:
                argv = argv[:4] + [_with_child_trust_home(trust_home, argv[4])] + argv[5:]
            return real_run(argv, *args, **kwargs)

        self._child_trust_patch = patch.object(
            lifecycle.subprocess, "run", side_effect=run_with_temp_trust_home)
        self._child_trust_patch.start()
        self.addCleanup(self._child_trust_patch.stop)
        self._script_path_patch = patch.object(
            lifecycle, "__file__",
            str(self.source / "tools" / "llmtool" / "scripts" /
                "d6_worktree_lifecycle.py"))
        self._script_path_patch.start()
        self.addCleanup(self._script_path_patch.stop)

    def _write_trust(self, *, retain_release_issues=(289, 352), origins=None,
                     ci_trust=None, tasks_root=None):
        """(Re)write the temp trust.json; never the real Owner file."""
        self.trust_path.write_text(json.dumps({
            "schema_version": 1, "canonical_root": str(self.source),
            "workspace_root": str(self.root), "tasks_root": str(tasks_root or self.tasks),
            "repository": {"slug": "davidcatcat/davidcattoolsproject",
                           "allowed_origins": list(origins or [
                               "git@github.com:davidcatcat/davidcattoolsproject.git"]),
                           "approval_refs": {
                               "retain_release_issues": list(retain_release_issues)}},
            "ci_trust": ci_trust or {}, "tools": {},
        }), encoding="utf-8")
        self.trust_path.chmod(0o600)

    def test_main_refuses_changed_trust_digest(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            result = lifecycle._main(["--trust-sha256", "0" * 64])
        self.assertEqual(result, 1)
        self.assertEqual(output.getvalue(),
                         "STOP: D6 lifecycle rejected (TRUST_CHANGED_DURING_RUN)\n")

    def test_create_worktree_requires_trusted_canonical_root_outside_test_mode(self):
        trust = lifecycle._load_trust()
        trust["canonical_root"] = str(self.root / "other-repository")
        with patch.object(lifecycle, "_load_trust", return_value=trust):
            with self.assertRaises(lifecycle.LifecycleError) as raised:
                lifecycle.create_worktree(
                    canonical_root=self.source, task_id="q5-root-check",
                    agent_id="codex-test", session_ref="session", lease_ref="lease",
                    branch="test/q5-root-check", base_sha=self.base,
                    workspace_root=self.tasks)
        self.assertEqual(raised.exception.code, "D6_REPOSITORY_INVALID")
        self.assertFalse((self.tasks / "q5-root-check").exists())

    # --- #369 PR-4: registry trust stamps and rotation -------------------

    HTTPS_ORIGIN = "https://github.com/davidcatcat/davidcattoolsproject.git"
    SSH_ORIGIN = "git@github.com:davidcatcat/davidcattoolsproject.git"

    def _rotate_trust_binding(self):
        # Adding the HTTPS origin changes allowed_origins, so the binding digest.
        self._write_trust(origins=[self.SSH_ORIGIN, self.HTTPS_ORIGIN])

    def _stamp(self, record_path):
        trust = lifecycle._load_trust()
        record = lifecycle._read_registry_record(record_path)
        record.update(trust_sha256=trust["sha256"],
                      trust_binding_sha256=trust["binding_sha256"])
        lifecycle._replace_record(record_path, record)
        return record

    def _record_result(self, task_id, root):
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}):
            return lifecycle.record_result(task_id=task_id, canonical_root=self.source,
                                           root=root, workspace_root=self.tasks)

    def _bind(self, task_id):
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}):
            return lifecycle.bind_native_session(
                task_id=task_id, native_session_id="native-session-pr4",
                canonical_root=self.source, workspace_root=self.tasks)

    def test_binding_digest_matches_trust_module(self):
        from llmtool import trust as trust_module
        for origins in ([self.SSH_ORIGIN], [self.HTTPS_ORIGIN, self.SSH_ORIGIN]):
            self._write_trust(origins=origins)
            value = json.loads(self.trust_path.read_text(encoding="utf-8"))
            loaded = lifecycle._load_trust()
            self.assertEqual(loaded["binding_sha256"], trust_module._binding(value))
            self.assertEqual(loaded["slug"], "davidcatcat/davidcattoolsproject")
            self.assertEqual(loaded["retain_release_issues"], (289, 352))

    def test_trust_loader_rejects_invalid_retain_release_issues(self):
        for issues in ([], [0], [True], [5, 5], list(range(1, 10)), ["289"]):
            self._write_trust(retain_release_issues=issues)
            with self.assertRaises(lifecycle.TrustError) as raised:
                lifecycle._load_trust()
            self.assertEqual(str(raised.exception), "TRUST_SCHEMA_INVALID")

    def test_record_result_allows_full_digest_change_and_refuses_rotation(self):
        root, record_path = self._registered_result_fixture("stamp-result")
        stamped = self._stamp(record_path)
        self._write_trust(ci_trust={"mode": "self-hosted"})
        self.assertNotEqual(lifecycle._load_trust()["sha256"], stamped["trust_sha256"])
        head = self._commit_file(root, "result\n", "developer result")

        def complete_bootstrap(_root, _task_id):
            current = lifecycle._read_registry_record(record_path)
            current["env2_pass_marker"] = dict(current["env2_pass_marker"],
                                               submitted_head_sha=head,
                                               current_head_sha=head)
            lifecycle._replace_record(record_path, current)

        with patch.object(lifecycle, "_bootstrap", side_effect=complete_bootstrap), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self._record_result("stamp-result", root), head)
        after = lifecycle._read_registry_record(record_path)
        # Stamps are never rewritten by a later lifecycle step.
        self.assertEqual((after["trust_sha256"], after["trust_binding_sha256"]),
                         (stamped["trust_sha256"], stamped["trust_binding_sha256"]))

        self._rotate_trust_binding()
        self._commit_file(root, "second\n", "second result")
        before = lifecycle._read_registry_record(record_path)
        with patch.object(lifecycle, "_bootstrap") as bootstrap, \
                self.assertRaises(lifecycle.LifecycleError) as raised:
            self._record_result("stamp-result", root)
        self.assertEqual(raised.exception.code, "D6_TRUST_ROTATED")
        bootstrap.assert_not_called()
        self.assertEqual(lifecycle._read_registry_record(record_path), before)

    def test_bind_native_session_refuses_rotated_binding(self):
        _root, record_path = self._registered_result_fixture("stamp-bind")
        self._stamp(record_path)
        self._rotate_trust_binding()
        before = lifecycle._read_registry_record(record_path)
        with self.assertRaises(lifecycle.LifecycleError) as raised:
            self._bind("stamp-bind")
        self.assertEqual(raised.exception.code, "D6_TRUST_ROTATED")
        self.assertEqual(lifecycle._read_registry_record(record_path), before)

    def test_partial_or_invalid_stamps_are_treated_as_rotated(self):
        _root, record_path = self._registered_result_fixture("stamp-partial")
        trust = lifecycle._load_trust()
        for stamps in ({"trust_sha256": trust["sha256"]},
                       {"trust_binding_sha256": None},
                       {"trust_sha256": trust["sha256"],
                        "trust_binding_sha256": trust["binding_sha256"].upper()}):
            record = lifecycle._read_registry_record(record_path)
            for key in ("trust_sha256", "trust_binding_sha256"):
                record.pop(key, None)
            record.update(stamps)
            lifecycle._replace_record(record_path, record)
            with self.assertRaises(lifecycle.LifecycleError) as raised:
                self._bind("stamp-partial")
            self.assertEqual(raised.exception.code, "D6_TRUST_ROTATED")

    def test_legacy_record_follows_current_trust_path_rules(self):
        # Fixture records carry no stamps: matching paths pass unchanged.
        _root, record_path = self._registered_result_fixture("legacy-bind")
        with contextlib.redirect_stdout(io.StringIO()):
            self._bind("legacy-bind")
        after = lifecycle._read_registry_record(record_path)
        self.assertNotIn("trust_sha256", after)
        self.assertNotIn("trust_binding_sha256", after)

        root, record_path = self._registered_result_fixture("legacy-moved")
        trust = lifecycle._load_trust()
        for changed in (dict(trust, canonical_root=str(self.root / "other-canonical")),
                        dict(trust, tasks_root=str(self.root / "other-tasks"))):
            before = lifecycle._read_registry_record(record_path)
            with patch.object(lifecycle, "_load_trust", return_value=changed):
                with self.assertRaises(lifecycle.LifecycleError) as raised:
                    self._bind("legacy-moved")
                self.assertEqual(raised.exception.code, "D6_TRUST_ROTATED")
                with patch.object(lifecycle, "_bootstrap") as bootstrap, \
                        self.assertRaises(lifecycle.LifecycleError) as raised:
                    self._record_result("legacy-moved", root)
                self.assertEqual(raised.exception.code, "D6_TRUST_ROTATED")
                bootstrap.assert_not_called()
            self.assertEqual(lifecycle._read_registry_record(record_path), before)

    def test_trust_change_after_lock_stops_record_result_and_bind(self):
        root, record_path = self._registered_result_fixture("stamp-toctou")
        self._commit_file(root, "result\n", "developer result")
        before = lifecycle._read_registry_record(record_path)
        trust = lifecycle._load_trust()
        for call in (lambda: self._record_result("stamp-toctou", root),
                     lambda: self._bind("stamp-toctou")):
            changed = dict(trust, sha256="0" * 64)
            with patch.object(lifecycle, "_load_trust", side_effect=[trust, changed]), \
                    patch.object(lifecycle, "_bootstrap") as bootstrap, \
                    self.assertRaises(lifecycle.TrustError) as raised:
                call()
            self.assertEqual(str(raised.exception), "TRUST_CHANGED_DURING_RUN")
            bootstrap.assert_not_called()
            self.assertEqual(lifecycle._read_registry_record(record_path), before)

    def test_device_rebind_refuses_rotated_binding(self):
        _root, record_path = self._registered_result_fixture("stamp-rebind")
        record = self._stamp(record_path)
        record["physical_identity"]["device"] += 1
        lifecycle._replace_record(record_path, record)
        self._rotate_trust_binding()
        for execute in (False, True):
            result, line = self._rebind("stamp-rebind", execute=execute)
            self.assertEqual(result, 1)
            self.assertEqual((line["result"], line["reasons"]),
                             ("BLOCKED", ["D6_TRUST_ROTATED"]))
        self.assertEqual(lifecycle._read_registry_record(record_path), record)

    def test_reconcile_retain_blocks_rotated_absent_record_and_reports_legacy(self):
        rotated_root, rotated_path = self._registered_result_fixture("stamp-reconcile")
        legacy_root, legacy_path = self._registered_result_fixture("legacy-reconcile")
        self._stamp(rotated_path)
        for root in (rotated_root, legacy_root):
            subprocess.run(["/usr/bin/git", "worktree", "unlock", str(root)],
                           cwd=self.source, check=True, capture_output=True)
            shutil.rmtree(root)
        subprocess.run(["/usr/bin/git", "worktree", "prune"], cwd=self.source,
                       check=True, capture_output=True)
        self._rotate_trust_binding()

        def reconcile(execute):
            output = io.StringIO()
            with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                    contextlib.redirect_stdout(output):
                lifecycle.reconcile_registry(canonical_root=self.source, execute=execute,
                                             workspace_root=self.tasks)
            return {item["task_id"]: item for item in json.loads(output.getvalue())["records"]}

        dry = reconcile(False)
        self.assertEqual((dry["stamp-reconcile"]["result"], dry["stamp-reconcile"]["reason"]),
                         ("BLOCKED", "D6_TRUST_ROTATED"))
        self.assertEqual(dry["legacy-reconcile"]["result"], "RECONCILE_READY")
        self.assertIs(dry["legacy-reconcile"]["legacy_unstamped"], True)
        self.assertEqual(lifecycle._read_registry_record(rotated_path)["state"], "ACTIVE")

        executed = reconcile(True)
        self.assertEqual(executed["stamp-reconcile"]["result"], "RETAIN_BLOCKED")
        self.assertEqual(executed["legacy-reconcile"]["result"], "RETIRED_EXTERNALLY")
        rotated = lifecycle._read_registry_record(rotated_path)
        self.assertEqual(rotated["state"], "RETAIN_BLOCKED")
        self.assertIn("trust rotation", rotated["evidence_refs"][-1])
        self.assertNotIn("trust_sha256", lifecycle._read_registry_record(legacy_path))

        # The rotated record is released only with an approval from the new trust.
        self._write_trust(origins=[self.SSH_ORIGIN, self.HTTPS_ORIGIN],
                          retain_release_issues=[900])
        old = "https://github.com/davidcatcat/davidcattoolsproject/issues/352#issuecomment-1"
        new = "https://github.com/davidcatcat/davidcattoolsproject/issues/900#issuecomment-2"
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}):
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                self.assertEqual(lifecycle._main([
                    "--release-retain", "stamp-reconcile=" + old,
                    "--repo-root", str(self.source)]), 1)
            self.assertEqual(error.getvalue(),
                             "STOP: D6 lifecycle rejected (D6_TASK_INPUT_INVALID)\n")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(lifecycle._main([
                    "--release-retain", "stamp-reconcile=" + new,
                    "--repo-root", str(self.source)]), 0)
        (released,) = json.loads(output.getvalue())["records"]
        self.assertEqual(released["result"], "RETIRED_EXTERNALLY")
        self.assertIs(released["trust_rotated"], True)
        self.assertEqual(lifecycle._read_registry_record(rotated_path)["owner_approval_ref"], new)

    def test_rotated_tasks_root_absent_tree_reconciles_and_releases(self):
        root, record_path = self._registered_result_fixture("moved-absent")
        self._stamp(record_path)
        subprocess.run(["/usr/bin/git", "worktree", "unlock", str(root)],
                       cwd=self.source, check=True, capture_output=True)
        shutil.rmtree(root)
        subprocess.run(["/usr/bin/git", "worktree", "prune"], cwd=self.source,
                       check=True, capture_output=True)
        new_tasks = self.root / "tasks-new"
        new_tasks.mkdir(mode=0o700)
        self._write_trust(tasks_root=new_tasks, retain_release_issues=[900])

        def reconcile(execute):
            output = io.StringIO()
            with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                    contextlib.redirect_stdout(output):
                lifecycle.reconcile_registry(canonical_root=self.source, execute=execute,
                                             workspace_root=new_tasks)
            return {item["task_id"]: item for item in json.loads(output.getvalue())["records"]}

        dry = reconcile(False)
        self.assertEqual((dry["moved-absent"]["result"], dry["moved-absent"]["reason"]),
                         ("BLOCKED", "D6_TRUST_ROTATED"))
        self.assertEqual(lifecycle._read_registry_record(record_path)["state"], "ACTIVE")
        self.assertEqual(reconcile(True)["moved-absent"]["result"], "RETAIN_BLOCKED")
        self.assertEqual(lifecycle._read_registry_record(record_path)["state"],
                         "RETAIN_BLOCKED")

        approval = "https://github.com/davidcatcat/davidcattoolsproject/issues/900#issuecomment-3"
        # The old tree reappearing at its recorded root blocks the release.
        root.mkdir(mode=0o700)
        output = io.StringIO()
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                contextlib.redirect_stdout(output):
            self.assertEqual(lifecycle._main([
                "--release-retain", "moved-absent=" + approval,
                "--repo-root", str(self.source)]), 1)
        (blocked,) = json.loads(output.getvalue())["records"]
        self.assertEqual(blocked["reason"], "D6_RELEASE_RETAIN_WORKTREE_PRESENT")
        root.rmdir()

        output = io.StringIO()
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                contextlib.redirect_stdout(output):
            self.assertEqual(lifecycle._main([
                "--release-retain", "moved-absent=" + approval,
                "--repo-root", str(self.source)]), 0)
        (released,) = json.loads(output.getvalue())["records"]
        self.assertEqual(released["result"], "RETIRED_EXTERNALLY")
        self.assertIs(released["trust_rotated"], True)
        record = lifecycle._read_registry_record(record_path)
        self.assertEqual((record["state"], record["owner_approval_ref"]),
                         ("RETIRED_EXTERNALLY", approval))

    def test_release_retain_requires_trust_retain_release_issues(self):
        self._write_trust()
        value = json.loads(self.trust_path.read_text(encoding="utf-8"))
        del value["repository"]["approval_refs"]["retain_release_issues"]
        self.trust_path.write_text(json.dumps(value), encoding="utf-8")
        approval = "https://github.com/davidcatcat/davidcattoolsproject/issues/352#issuecomment-1"
        error = io.StringIO()
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                contextlib.redirect_stderr(error):
            self.assertEqual(lifecycle._main([
                "--release-retain", "any-task=" + approval,
                "--repo-root", str(self.source)]), 1)
        self.assertEqual(error.getvalue(),
                         "STOP: D6 lifecycle rejected (TRUST_SCHEMA_INVALID)\n")
        with self.assertRaises(lifecycle.TrustError):
            lifecycle.release_retained_records(canonical_root=self.source,
                                               approvals={"any-task": approval},
                                               workspace_root=self.tasks)

    def test_release_retain_pattern_and_help_derive_from_trust(self):
        self._write_trust(retain_release_issues=[900, 901])
        pattern = lifecycle._retain_release_re(lifecycle._load_trust())
        self.assertTrue(pattern.fullmatch(
            "https://github.com/davidcatcat/davidcattoolsproject/issues/901#issuecomment-7"))
        for rejected in (
                "https://github.com/davidcatcat/davidcattoolsproject/issues/352#issuecomment-7",
                "https://github.com/davidcatcatXdavidcattoolsproject/issues/900#issuecomment-7",
                "https://github.com/davidcatcat/davidcattoolsproject/issues/9001#issuecomment-7"):
            self.assertIsNone(pattern.fullmatch(rejected))
        self.assertIn("issue 900 or 901 of davidcatcat/davidcattoolsproject",
                      lifecycle._release_retain_help())
        with patch.object(lifecycle, "_load_trust",
                          side_effect=lifecycle.TrustError("TRUST_MISSING")):
            self.assertIn("trust.json approval_refs.retain_release_issues",
                          lifecycle._release_retain_help())
        # The legacy constant stays only for `llmtool trust verify-legacy`.
        self.assertIn("(?:289|352)", lifecycle.OWNER_APPROVAL_RE.pattern)

    def _registered_result_fixture(self, task_id="record-result-test"):
        root = self.tasks / task_id
        branch = "test/" + task_id
        subprocess.run(["/usr/bin/git", "worktree", "add", "--lock", "-b", branch,
                        str(root), self.base], cwd=self.source, check=True,
                       capture_output=True)
        root.chmod(0o700)
        root_stat = root.lstat()
        fingerprint = _actual_cache_fingerprint()
        report = {"schema_version": 1, "result": "PASS", "task_id": task_id,
                  "root_realpath": str(root), "head_sha": self.base,
                  "tooling_sha": self.base, "cache_fingerprint": fingerprint}
        report_bytes = (json.dumps(report, sort_keys=True, separators=(",", ":")) +
                        "\n").encode()
        report_path = root / "tools" / "llmtool" / ".d6-bootstrap-report.json"
        report_path.write_bytes(report_bytes)
        report_path.chmod(0o600)
        record = {"schema_version": 1, "task_id": task_id,
                  "trusted_agent_id": "codex-developer-test",
                  "session_ref": "live test session", "root_realpath": str(root),
                  "physical_identity": {"device": root_stat.st_dev,
                      "inode": root_stat.st_ino, "uid": root_stat.st_uid, "mode": "0o700"},
                  "git_common_dir": str(self.source / ".git"), "branch": branch,
                  "base_sha": self.base, "head_sha": self.base, "state": "ACTIVE",
                  "last_verified_at": "2026-01-01T00:00:00Z", "evidence_refs": [],
                  "lock_state": "LOCKED",
                  "retention_paths": list(lifecycle.RETENTION_PATHS),
                  "env2_pass_marker": {"schema_version": 1, "result": "PASS",
                      "task_id": task_id, "root_realpath": str(root),
                      "submitted_head_sha": self.base, "current_head_sha": self.base,
                      "tooling_sha": self.base, "environment_fingerprint": fingerprint,
                      "report_sha256": __import__("hashlib").sha256(report_bytes).hexdigest()}}
        record_path = self.source / ".git" / "llmtool-worktrees" / (task_id + ".json")
        record_path.write_text(json.dumps(record), encoding="utf-8")
        record_path.chmod(0o600)
        return root, record_path

    def _commit_file(self, root, content, message):
        path = root / "tools" / "llmtool" / "result-fixture.txt"
        path.write_text(content, encoding="utf-8")
        subprocess.run(["/usr/bin/git", "add", str(path)], cwd=root, check=True)
        subprocess.run(["/usr/bin/git", "-c", "user.name=D6 test",
                        "-c", "user.email=d6@example.invalid", "commit", "-m", message],
                       cwd=root, check=True, capture_output=True)
        return subprocess.check_output(["/usr/bin/git", "rev-parse", "HEAD"],
                                       cwd=root, text=True).strip()

    def _git(self, cwd, *args):
        return subprocess.check_output(["/usr/bin/git", *args], cwd=cwd,
                                       text=True).strip()

    def _rebind(self, task_id, *, execute=False):
        output = io.StringIO()
        argv = ["--rebind-device-task-id", task_id, "--repo-root", str(self.source)]
        if execute:
            argv.append("--execute-approved-rebind")
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                contextlib.redirect_stdout(output):
            result = lifecycle._main(argv)
        lines = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(lines), 1)
        return result, lines[0]

    def test_device_rebind_dry_run_and_execute(self):
        root, record_path = self._registered_result_fixture("device-rebind")
        worktree_head = self._commit_file(
            root, "descendant commit\n", "commit after registration")
        record = lifecycle._read_registry_record(record_path)
        recorded_head = record["head_sha"]
        self.assertNotEqual(worktree_head, recorded_head)
        original_device = record["physical_identity"]["device"]
        record["physical_identity"]["device"] = original_device + 1
        lifecycle._replace_record(record_path, record)
        before = lifecycle._read_registry_record(record_path)

        result, line = self._rebind("device-rebind")
        self.assertEqual(result, 0)
        self.assertEqual((line["result"], line["old_device"], line["new_device"]),
                         ("READY", original_device + 1, root.lstat().st_dev))
        self.assertEqual(lifecycle._read_registry_record(record_path), before)

        result, line = self._rebind("device-rebind", execute=True)
        self.assertEqual(result, 0)
        self.assertEqual(line["result"], "REBOUND")
        rebound = lifecycle._read_registry_record(record_path)
        self.assertEqual(rebound["physical_identity"]["device"], root.lstat().st_dev)
        self.assertEqual(rebound["head_sha"], recorded_head)
        self.assertRegex(
            rebound["evidence_refs"][-1],
            r"^device rebind %s->%s at .* \(worktree HEAD %s ahead of record\)$" %
            (original_device + 1, root.lstat().st_dev, worktree_head))
        self.assertEqual(lifecycle._identity_reasons(
            root, rebound, self.source, self.source / ".git", self.tasks), [])
        def complete_bootstrap(bootstrap_root, bootstrap_task_id):
            self.assertEqual(bootstrap_root, root)
            self.assertEqual(bootstrap_task_id, "device-rebind")
            current = lifecycle._read_registry_record(record_path)
            self.assertEqual(current["head_sha"], worktree_head)
            current["env2_pass_marker"] = {
                **before["env2_pass_marker"],
                "submitted_head_sha": worktree_head,
                "current_head_sha": worktree_head,
            }
            lifecycle._replace_record(record_path, current)
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_bootstrap",
                             side_effect=complete_bootstrap) as bootstrap:
            recorded = lifecycle.record_result(
                task_id="device-rebind", canonical_root=self.source, root=root,
                workspace_root=self.tasks)
        bootstrap.assert_called_once_with(root, "device-rebind")
        self.assertEqual(recorded, worktree_head)
        self.assertEqual(lifecycle._read_registry_record(record_path)["head_sha"],
                         worktree_head)

    def _rebind_outside_test_mode(self, task_id, *, execute=False):
        argv = ["--rebind-device-task-id", task_id]
        if execute:
            argv.append("--execute-approved-rebind")
        output, errors = io.StringIO(), io.StringIO()
        environ = {key: value for key, value in os.environ.items()
                   if key != "LLMTOOL_D6_TESTING"}
        with patch.dict(os.environ, environ, clear=True), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.assertNotIn("LLMTOOL_D6_TESTING", os.environ)
            result = lifecycle._main(argv)
        return result, output.getvalue(), errors.getvalue()

    def test_device_rebind_cli_succeeds_outside_test_mode(self):
        # The CLI never passes a workspace root; the trusted tasks_root (a str)
        # must still compare equal to itself as a Path outside test mode.
        root, record_path = self._registered_result_fixture("rebind-real-mode")
        record = lifecycle._read_registry_record(record_path)
        original_device = record["physical_identity"]["device"]
        record["physical_identity"]["device"] = original_device + 1
        lifecycle._replace_record(record_path, record)

        result, stdout, stderr = self._rebind_outside_test_mode("rebind-real-mode")
        self.assertEqual((result, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["result"], "READY")

        result, stdout, stderr = self._rebind_outside_test_mode(
            "rebind-real-mode", execute=True)
        self.assertEqual((result, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["result"], "REBOUND")
        self.assertEqual(lifecycle._read_registry_record(record_path)
                         ["physical_identity"]["device"], root.lstat().st_dev)

    def test_device_rebind_refuses_mismatching_workspace_root_outside_test_mode(self):
        _, record_path = self._registered_result_fixture("rebind-root-mismatch")
        record = lifecycle._read_registry_record(record_path)
        record["physical_identity"]["device"] += 1
        lifecycle._replace_record(record_path, record)
        before = lifecycle._read_registry_record(record_path)
        other = self.root / "other-tasks"
        other.mkdir(mode=0o700)
        environ = {key: value for key, value in os.environ.items()
                   if key != "LLMTOOL_D6_TESTING"}
        with patch.dict(os.environ, environ, clear=True):
            for workspace_root in (other, str(other)):
                with self.subTest(type=type(workspace_root).__name__):
                    with self.assertRaises(lifecycle.LifecycleError) as raised:
                        lifecycle.rebind_device_tasks(
                            canonical_root=self.source,
                            task_ids=["rebind-root-mismatch"], execute=True,
                            workspace_root=workspace_root)
                    self.assertEqual(raised.exception.code, "D6_WORKSPACE_ROOT_INVALID")
        self.assertEqual(lifecycle._read_registry_record(record_path), before)

    def test_device_rebind_refuses_every_non_device_mismatch(self):
        cases = (
            ("inode", "D6_REBIND_INODE_MISMATCH"),
            ("uid", "D6_REBIND_UID_MISMATCH"),
            ("mode", "D6_REBIND_MODE_MISMATCH"),
            ("branch", "D6_REBIND_BRANCH_MISMATCH"),
            ("head", "D6_REBIND_HEAD_MISMATCH"),
            ("path", "D6_REBIND_PATH_MISMATCH"),
            ("retired", "D6_REBIND_STATE_REFUSED"),
        )
        for suffix, expected in cases:
            with self.subTest(mismatch=suffix):
                _root = None
                try:
                    task_id = "rebind-" + suffix
                    _root, record_path = self._registered_result_fixture(task_id)
                    record = lifecycle._read_registry_record(record_path)
                    record["physical_identity"]["device"] += 1
                    identity = record["physical_identity"]
                    if suffix == "inode":
                        identity["inode"] += 1
                    elif suffix == "uid":
                        identity["uid"] += 1
                    elif suffix == "mode":
                        os.chmod(_root, 0o755)
                    elif suffix == "branch":
                        record["branch"] = "task/wrong-branch"
                    elif suffix == "head":
                        record["head_sha"] = "0" * 40
                    elif suffix == "path":
                        record["root_realpath"] = str(self.tasks / (task_id + "-other"))
                    elif suffix == "retired":
                        record["state"] = "REMOVED"
                    lifecycle._replace_record(record_path, record)
                    before = lifecycle._read_registry_record(record_path)
                    result, line = self._rebind(task_id, execute=True)
                    self.assertEqual(result, 1)
                    self.assertEqual(line["result"], "BLOCKED")
                    self.assertEqual(line["reasons"], [expected])
                    self.assertEqual(lifecycle._read_registry_record(record_path), before)
                finally:
                    if suffix == "mode" and _root is not None:
                        os.chmod(_root, 0o700)

    def test_train_squash_publication_requires_all_evidence(self):
        root, _ = self._registered_result_fixture("train-evidence")
        head = self._commit_file(root, "pr head\n", "PR head")
        train_head = self._commit_file(root, "train result\n", "train result")
        tree = self._git(root, "rev-parse", train_head + "^{tree}")
        squash = subprocess.check_output(["/usr/bin/git", "commit-tree", tree,
                                          "-p", self.base], cwd=root, text=True).strip()
        self._git(root, "update-ref", "refs/remotes/origin/main", squash)
        evidence = (head, train_head, squash)
        self.assertEqual(lifecycle._publication(root, "test/train-evidence", head,
                                                None, evidence),
                         "train_squash_evidence")

        self.assertIsNone(lifecycle._publication(
            root, "test/train-evidence", head, None,
            (self.base, train_head, squash)))
        self.assertIsNone(lifecycle._publication(
            root, "test/train-evidence", head, None,
            (head, self.base, squash)))

        mismatch_tree = subprocess.check_output(["/usr/bin/git", "mktree"],
                                                cwd=root, input="", text=True).strip()
        mismatched_squash = subprocess.check_output(
            ["/usr/bin/git", "commit-tree", mismatch_tree, "-p", self.base],
            cwd=root, text=True).strip()
        self._git(root, "update-ref", "refs/remotes/origin/main", mismatched_squash)
        self.assertIsNone(lifecycle._publication(
            root, "test/train-evidence", head, None,
            (head, train_head, mismatched_squash)))

        self._git(root, "update-ref", "refs/remotes/origin/main", self.base)
        self.assertIsNone(lifecycle._publication(
            root, "test/train-evidence", head, None, evidence))

    def test_detached_reviewer_worktree_is_retire_ready_when_head_is_published(self):
        root, record_path = self._registered_result_fixture("reviewer-published")
        head = self._commit_file(root, "reviewed\n", "reviewed head")
        self._git(root, "branch", "review/reviewer-published", head)
        self._git(root, "checkout", "--detach", head)
        self._git(root, "update-ref", "refs/remotes/origin/main", head)
        record = lifecycle._read_registry_record(record_path)
        record.update(branch="review/reviewer-published", head_sha=head)
        lifecycle._replace_record(record_path, record)
        with patch.object(lifecycle, "_live_pids", return_value=[]):
            plan = lifecycle._retire_plan(
                "reviewer-published", record, canonical_root=self.source,
                common_dir=self.source / ".git", workspace_root=self.tasks,
                squash=None)
        self.assertEqual(plan["publication"], "origin_main")
        self.assertEqual(plan["reasons"], [])

    def test_detached_reviewer_head_published_on_another_origin_branch(self):
        root, record_path = self._registered_result_fixture("reviewer-pr-ref")
        head = self._commit_file(root, "reviewed\n", "reviewed head")
        self._git(root, "branch", "review/reviewer-pr-ref", head)
        self._git(root, "checkout", "--detach", head)
        self._git(root, "update-ref", "refs/remotes/origin/feature/529", head)
        record = lifecycle._read_registry_record(record_path)
        record.update(branch="review/reviewer-pr-ref", head_sha=head)
        lifecycle._replace_record(record_path, record)

        with patch.object(lifecycle, "_live_pids", return_value=[]):
            plan = lifecycle._retire_plan(
                "reviewer-pr-ref", record, canonical_root=self.source,
                common_dir=self.source / ".git", workspace_root=self.tasks,
                squash=None)
        self.assertEqual(plan["publication"], "origin_pr_head")
        self.assertEqual(plan["reasons"], [])

        self._git(root, "update-ref", "-d", "refs/remotes/origin/feature/529")
        self._git(root, "update-ref", "refs/remotes/origin/other-pr/head", self.base)
        self.assertIsNone(lifecycle._publication(root, "review/reviewer-pr-ref",
                                                 head, None))
        self._git(root, "update-ref", "refs/remotes/origin/feature/530", head)
        self.assertEqual(lifecycle._publication(root, "review/reviewer-pr-ref",
                                                head, None), "origin_pr_head")

    def test_release_retain_requires_approved_comment_and_absent_unregistered_tree(self):
        root, record_path = self._registered_result_fixture("release-retain-test")
        record = lifecycle._read_registry_record(record_path)
        record["state"] = "RETAIN_BLOCKED"
        lifecycle._replace_record(record_path, record)
        approval = "https://github.com/davidcatcat/davidcattoolsproject/issues/352#issuecomment-12345"

        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}):
            error_output = io.StringIO()
            with contextlib.redirect_stderr(error_output):
                self.assertEqual(lifecycle._main([
                    "--release-retain", "release-retain-test=" +
                    "https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-12345",
                    "--repo-root", str(self.source)]), 1)
            self.assertIn("D6_TASK_INPUT_INVALID", error_output.getvalue())

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(lifecycle._main([
                    "--release-retain", "release-retain-test=" + approval,
                    "--repo-root", str(self.source)]), 1)
            self.assertEqual(lifecycle._read_registry_record(record_path)["state"],
                             "RETAIN_BLOCKED")
            self.assertIn("D6_RELEASE_RETAIN_WORKTREE_PRESENT", output.getvalue())

            subprocess.run(["/usr/bin/git", "worktree", "unlock", str(root)],
                           cwd=self.source, check=True, capture_output=True)
            subprocess.run(["/usr/bin/git", "worktree", "remove", str(root)],
                           cwd=self.source, check=True, capture_output=True)
            self.assertFalse(root.exists())
            self.assertNotIn("worktree " + str(root), self._git(
                self.source, "worktree", "list", "--porcelain"))

            with contextlib.redirect_stdout(output := io.StringIO()):
                self.assertEqual(lifecycle._main([
                    "--release-retain", "release-retain-test=" + approval,
                    "--release-retain", "release-retain-test=" + approval,
                    "--repo-root", str(self.source)]), 1)
            self.assertEqual(lifecycle._read_registry_record(record_path)["state"],
                             "RETAIN_BLOCKED")
            self.assertEqual(lifecycle._parse_release_retain([
                "release-retain-test=" + approval]),
                {"release-retain-test": approval})

            with contextlib.redirect_stdout(output := io.StringIO()):
                self.assertEqual(lifecycle._main([
                    "--release-retain", "release-retain-test=" + approval,
                    "--repo-root", str(self.source)]), 0)
            released = lifecycle._read_registry_record(record_path)
            self.assertEqual(released["state"], "RETIRED_EXTERNALLY")
            self.assertEqual(released["owner_approval_ref"], approval)

    def test_reconcile_registry_dry_run_execute_and_safety_boundaries(self):
        missing_root, missing_path = self._registered_result_fixture("missing-record")
        retained_root, retained_path = self._registered_result_fixture("retained-record")
        existing_root, existing_path = self._registered_result_fixture("existing-record")
        locked_root, locked_path = self._registered_result_fixture("locked-missing-record")
        retained = lifecycle._read_registry_record(retained_path)
        retained["state"] = "RETAIN_BLOCKED"
        lifecycle._replace_record(retained_path, retained)
        existing_before = lifecycle._read_registry_record(existing_path)
        for root in (missing_root, retained_root):
            subprocess.run(["/usr/bin/git", "worktree", "unlock", str(root)],
                           cwd=self.source, check=True, capture_output=True)
        for root in (missing_root, retained_root):
            shutil.rmtree(root)
        shutil.rmtree(locked_root)
        subprocess.run(["/usr/bin/git", "worktree", "prune"], cwd=self.source,
                       check=True, capture_output=True)

        listed_worktrees = subprocess.check_output(
            ["/usr/bin/git", "worktree", "list", "--porcelain"],
            cwd=self.source, text=True)
        self.assertIn("worktree " + str(locked_root), listed_worktrees)

        dry_output = io.StringIO()
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                contextlib.redirect_stdout(dry_output):
            self.assertTrue(lifecycle.reconcile_registry(
                canonical_root=self.source, workspace_root=self.tasks))
        dry = json.loads(dry_output.getvalue())
        self.assertEqual([item["task_id"] for item in dry["records"]], ["missing-record"])
        self.assertNotIn("locked-missing-record",
                         [item["task_id"] for item in dry["records"]])
        self.assertTrue(existing_root.is_dir())
        self.assertEqual(lifecycle._read_registry_record(missing_path)["state"], "ACTIVE")
        self.assertEqual(lifecycle._read_registry_record(locked_path)["state"], "ACTIVE")

        execute_output = io.StringIO()
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                contextlib.redirect_stdout(execute_output):
            self.assertTrue(lifecycle.reconcile_registry(
                canonical_root=self.source, execute=True, workspace_root=self.tasks))
        executed = json.loads(execute_output.getvalue())
        self.assertEqual([item["task_id"] for item in executed["records"]], ["missing-record"])
        self.assertEqual(lifecycle._read_registry_record(missing_path)["state"],
                         "RETIRED_EXTERNALLY")
        self.assertEqual(lifecycle._read_registry_record(retained_path), retained)
        self.assertEqual(lifecycle._read_registry_record(existing_path), existing_before)
        self.assertTrue(existing_root.is_dir())

    def test_record_result_rebootstraps_env2_for_new_head(self):
        root, record_path = self._registered_result_fixture()
        before = lifecycle._read_registry_record(record_path)
        report_path = root / "tools" / "llmtool" / ".d6-bootstrap-report.json"
        report_before = report_path.read_bytes()
        head = self._commit_file(root, "result\n", "developer result")
        output = io.StringIO()
        def complete_bootstrap(bootstrap_root, bootstrap_task_id):
            self.assertEqual(bootstrap_root, root)
            self.assertEqual(bootstrap_task_id, "record-result-test")
            current = lifecycle._read_registry_record(record_path)
            self.assertEqual(current["head_sha"], head)
            current["env2_pass_marker"] = {
                **before["env2_pass_marker"], "submitted_head_sha": head,
                "current_head_sha": head,
            }
            lifecycle._replace_record(record_path, current)
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_bootstrap", side_effect=complete_bootstrap) as bootstrap, \
                contextlib.redirect_stdout(output):
            recorded = lifecycle.record_result(
                task_id="record-result-test", canonical_root=self.source,
                root=root, workspace_root=self.tasks)
        bootstrap.assert_called_once_with(root, "record-result-test")
        after = lifecycle._read_registry_record(record_path)
        self.assertEqual(recorded, head)
        self.assertEqual(after["head_sha"], head)
        self.assertNotEqual(after["last_verified_at"], before["last_verified_at"])
        self.assertEqual(after["env2_pass_marker"]["current_head_sha"], head)
        self.assertEqual(after["env2_pass_marker"]["submitted_head_sha"], head)
        self.assertEqual(report_path.read_bytes(), report_before)
        self.assertEqual({key: value for key, value in after.items()
                          if key not in ("head_sha", "last_verified_at", "env2_pass_marker")},
                         {key: value for key, value in before.items()
                          if key not in ("head_sha", "last_verified_at", "env2_pass_marker")})
        self.assertIn("D6_RESULT_RECORDED", output.getvalue())

    def test_record_result_source_mismatch_rolls_back_registry(self):
        root, record_path = self._registered_result_fixture("record-result-source-mismatch")
        makefile = root / "tools" / "llmtool" / "Makefile"
        makefile.write_text(makefile.read_text() + "# changed bootstrap source\n")
        subprocess.run(["/usr/bin/git", "add", str(makefile)], cwd=root, check=True)
        subprocess.run(["/usr/bin/git", "-c", "user.name=D6 test",
                        "-c", "user.email=d6@example.invalid", "commit", "-m",
                        "change bootstrap source"], cwd=root, check=True, capture_output=True)
        before = lifecycle._read_registry_record(record_path)
        def reject_changed_bootstrap_source(bootstrap_root, bootstrap_task_id):
            current = lifecycle._read_registry_record(record_path)
            self.assertEqual(current["head_sha"], subprocess.check_output(
                ["/usr/bin/git", "rev-parse", "HEAD"], cwd=root, text=True).strip())
            self.assertNotEqual(
                subprocess.check_output(["/usr/bin/git", "show",
                                         before["base_sha"] + ":tools/llmtool/Makefile"],
                                        cwd=root),
                (root / "tools" / "llmtool" / "Makefile").read_bytes())
            raise lifecycle.LifecycleError("ENV_BOOTSTRAP_REJECTED")
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_bootstrap",
                             side_effect=reject_changed_bootstrap_source), \
                self.assertRaises(lifecycle.LifecycleError) as raised:
            lifecycle.record_result(task_id="record-result-source-mismatch",
                                    canonical_root=self.source, root=root,
                                    workspace_root=self.tasks)
        self.assertEqual(raised.exception.code, "ENV_BOOTSTRAP_REJECTED")
        self.assertEqual(lifecycle._read_registry_record(record_path), before)

    def test_record_result_refuses_approved_tooling_without_registry_change(self):
        root, record_path = self._registered_result_fixture("record-result-approved")
        before = lifecycle._read_registry_record(record_path)
        before["env2_approved_tooling_sha"] = self.base
        lifecycle._replace_record(record_path, before)
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                self.assertRaises(lifecycle.LifecycleError) as raised:
            lifecycle.record_result(task_id="record-result-approved",
                                    canonical_root=self.source, root=root,
                                    workspace_root=self.tasks)
        self.assertEqual(raised.exception.code,
                         "D6_RECORD_RESULT_APPROVED_TOOLING_REFUSED")
        self.assertEqual(lifecycle._read_registry_record(record_path), before)

    def test_record_result_refuses_symlinked_report_directories(self):
        root, record_path = self._registered_result_fixture("record-result-symlink")
        tools = root / "tools"
        moved = root / "tools-real"
        tools.rename(moved)
        tools.symlink_to(moved, target_is_directory=True)
        before = lifecycle._read_registry_record(record_path)
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                self.assertRaises(lifecycle.LifecycleError) as raised:
            lifecycle.record_result(task_id="record-result-symlink",
                                    canonical_root=self.source, root=root,
                                    workspace_root=self.tasks)
        self.assertEqual(raised.exception.code,
                         "D6_RECORD_RESULT_ENV2_MARKER_INVALID")
        self.assertEqual(lifecycle._read_registry_record(record_path), before)

    def test_record_result_refuses_unknown_and_blocked_or_removed_tasks(self):
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                self.assertRaises(lifecycle.LifecycleError) as raised:
            lifecycle.record_result(task_id="missing-task", canonical_root=self.source,
                                    root=self.source, workspace_root=self.tasks)
        self.assertEqual(raised.exception.code, "D6_RECORD_RESULT_UNKNOWN_TASK")
        root, record_path = self._registered_result_fixture("record-result-state")
        for state in ("RETAIN_BLOCKED", "REMOVED"):
            record = lifecycle._read_registry_record(record_path)
            record["state"] = state
            lifecycle._replace_record(record_path, record)
            with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                    self.assertRaises(lifecycle.LifecycleError) as raised:
                lifecycle.record_result(task_id="record-result-state",
                                        canonical_root=self.source, root=root,
                                        workspace_root=self.tasks)
            self.assertEqual(raised.exception.code, "D6_RECORD_RESULT_STATE_REFUSED")

    def test_bind_native_session_sets_unset_value_without_changing_other_fields(self):
        root, record_path = self._registered_result_fixture("bind-native-session")
        before = lifecycle._read_registry_record(record_path)
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}):
            bound = lifecycle.bind_native_session(task_id="bind-native-session",
                native_session_id="12345678-1234-4234-8234-123456789abc",
                canonical_root=self.source, workspace_root=self.tasks)
        after = lifecycle._read_registry_record(record_path)
        self.assertEqual(bound, "12345678-1234-4234-8234-123456789abc")
        self.assertEqual(after["native_session_id"], bound)
        self.assertEqual({key: value for key, value in after.items()
                          if key != "native_session_id"}, before)

    def test_bind_native_session_finalizes_pending_session_ref(self):
        root, record_path = self._registered_result_fixture("bind-pending-session")
        record = lifecycle._read_registry_record(record_path)
        record["session_ref"] = "pending-bind-pending-session"
        lifecycle._replace_record(record_path, record)
        native_session_id = "12345678-1234-4234-8234-123456789abc"
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}):
            lifecycle.bind_native_session(task_id="bind-pending-session",
                native_session_id=native_session_id, canonical_root=self.source,
                workspace_root=self.tasks)
        after = lifecycle._read_registry_record(record_path)
        self.assertEqual(after["session_ref"], native_session_id)
        self.assertEqual(after["native_session_id"], native_session_id)

    def test_bind_native_session_refuses_overwrite_and_bad_format(self):
        root, record_path = self._registered_result_fixture("bind-native-refused")
        before = lifecycle._read_registry_record(record_path)
        before["native_session_id"] = "existing-session"
        lifecycle._replace_record(record_path, before)
        cases = [("replacement-session", "D6_BIND_NATIVE_SESSION_ALREADY_SET"),
                 ("bad\nsession", "D6_BIND_NATIVE_SESSION_INPUT_INVALID")]
        for native_session_id, expected in cases:
            with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                    self.assertRaises(lifecycle.LifecycleError) as raised:
                lifecycle.bind_native_session(task_id="bind-native-refused",
                    native_session_id=native_session_id, canonical_root=self.source,
                    workspace_root=self.tasks)
            self.assertEqual(raised.exception.code, expected)
            self.assertEqual(lifecycle._read_registry_record(record_path), before)

    def test_bind_native_session_refuses_unknown_task(self):
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                self.assertRaises(lifecycle.LifecycleError) as raised:
            lifecycle.bind_native_session(task_id="missing-bind-task",
                native_session_id="native-session", canonical_root=self.source,
                workspace_root=self.tasks)
        self.assertEqual(raised.exception.code,
                         "D6_BIND_NATIVE_SESSION_UNKNOWN_TASK")

    def test_record_result_refuses_dirty_branch_root_and_canonical_mismatch(self):
        root, _ = self._registered_result_fixture("record-result-mismatch")
        cases = [
            (root, "D6_RECORD_RESULT_DIRTY", lambda: (root / "dirty").write_text("x")),
            (root, "D6_RECORD_RESULT_BRANCH_MISMATCH",
             lambda: subprocess.run(["/usr/bin/git", "checkout", "-b", "wrong"],
                                    cwd=root, check=True, capture_output=True)),
            (self.source, "D6_RECORD_RESULT_CANONICAL_ROOT", lambda: None),
        ]
        for target, code, prepare in cases:
            if target == root:
                subprocess.run(["/usr/bin/git", "checkout", "test/record-result-mismatch"],
                               cwd=root, check=True, capture_output=True)
                path = root / "dirty"
                if path.exists():
                    path.unlink()
            prepare()
            with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                    self.assertRaises(lifecycle.LifecycleError) as raised:
                lifecycle.record_result(task_id="record-result-mismatch",
                                        canonical_root=self.source, root=target,
                                        workspace_root=self.tasks)
            self.assertEqual(raised.exception.code, code)
            if code == "D6_RECORD_RESULT_DIRTY":
                (root / "dirty").unlink()

        wrong_root = self.tasks / "other-root"
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                self.assertRaises(lifecycle.LifecycleError) as raised:
            lifecycle.record_result(task_id="record-result-mismatch",
                                    canonical_root=self.source, root=wrong_root,
                                    workspace_root=self.tasks)
        self.assertEqual(raised.exception.code, "D6_RECORD_RESULT_ROOT_MISMATCH")

    def test_record_result_refuses_non_descendant_head_without_registry_change(self):
        root, record_path = self._registered_result_fixture("record-result-diverge")
        first = self._commit_file(root, "first\n", "first descendant")
        record = lifecycle._read_registry_record(record_path)
        record["head_sha"] = first
        lifecycle._replace_record(record_path, record)
        subprocess.run(["/usr/bin/git", "reset", "--hard", self.base],
                       cwd=root, check=True, capture_output=True)
        self._commit_file(root, "other\n", "divergent result")
        before = lifecycle._read_registry_record(record_path)
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                self.assertRaises(lifecycle.LifecycleError) as raised:
            lifecycle.record_result(task_id="record-result-diverge",
                                    canonical_root=self.source, root=root,
                                    workspace_root=self.tasks)
        self.assertEqual(raised.exception.code, "D6_RECORD_RESULT_NON_DESCENDANT")
        self.assertEqual(lifecycle._read_registry_record(record_path), before)

    # --- #1019 A11 row 43: D6 writer-lease expiry -------------------------

    EXPIRED_LEASE = "2026-01-01T00:00:00Z"
    FUTURE_LEASE = "2999-01-01T00:00:00Z"

    def _set_lease(self, record_path, value):
        record = lifecycle._read_registry_record(record_path)
        record["lease_expires_at"] = value
        lifecycle._replace_record(record_path, record)
        return record

    def _complete_bootstrap(self, record_path, head):
        def complete(_root, _task_id):
            current = lifecycle._read_registry_record(record_path)
            current["env2_pass_marker"] = dict(current["env2_pass_marker"],
                                               submitted_head_sha=head,
                                               current_head_sha=head)
            lifecycle._replace_record(record_path, current)
        return complete

    def _lease_main(self, argv, cwds=()):
        """Run the CLI with a stubbed process list; return (code, JSON lines, stub)."""
        output = io.StringIO()
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_process_cwds",
                             return_value=None if cwds is None else
                             [("1", "/")] + list(cwds)) as process_cwds, \
                contextlib.redirect_stdout(output):
            code = lifecycle._main(argv + ["--repo-root", str(self.source)])
        lines = [json.loads(line) for line in output.getvalue().splitlines()]
        return code, lines, process_cwds

    def _release(self, task_id, cwds=()):
        return self._lease_main(["--release-expired-lease", task_id,
                                 "--pm-session", "pm-lease-test"], cwds)

    def test_lease_expiry_is_computed_and_refreshed_on_recorded_result(self):
        self.assertEqual(lifecycle.LEASE_SECONDS, 24 * 60 * 60)
        self.assertEqual(lifecycle._lease_expiry(1_000_000),
                         time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                       time.gmtime(1_000_000 + 24 * 60 * 60)))
        self.assertEqual(lifecycle._parse_utc(lifecycle._lease_expiry(1_000_000)),
                         1_000_000 + 24 * 60 * 60)
        record = {"state": "ACTIVE", "lease_expires_at": lifecycle._lease_expiry(1_000_000)}
        self.assertEqual(lifecycle.lease_status(record, now=1_000_000 + 86_399),
                         "LEASE_ACTIVE")
        self.assertEqual(lifecycle.lease_status(record, now=1_000_000 + 86_400),
                         "LEASE_EXPIRED")
        for invalid in ("not-a-time", None, 0, "2026-13-01T00:00:00Z",
                        "2026-01-01 00:00:00Z"):
            self.assertEqual(lifecycle.lease_status(
                {"state": "ACTIVE", "lease_expires_at": invalid}), "LEASE_INVALID")

        root, record_path = self._registered_result_fixture("lease-refresh")
        self._set_lease(record_path, self.EXPIRED_LEASE)
        head = self._commit_file(root, "result\n", "developer result")
        started = int(time.time())
        with patch.object(lifecycle, "_bootstrap",
                          side_effect=self._complete_bootstrap(record_path, head)), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self._record_result("lease-refresh", root), head)
        finished = time.time()
        after = lifecycle._read_registry_record(record_path)
        expires = lifecycle._parse_utc(after["lease_expires_at"])
        self.assertGreaterEqual(expires, started + lifecycle.LEASE_SECONDS)
        self.assertLessEqual(expires, finished + lifecycle.LEASE_SECONDS)
        self.assertEqual(lifecycle.lease_status(after), "LEASE_ACTIVE")

    def test_expired_lease_without_live_process_releases_and_keeps_worktree(self):
        root, record_path = self._registered_result_fixture("lease-release")
        head = self._commit_file(root, "kept\n", "developer work to keep")
        before = self._set_lease(record_path, self.EXPIRED_LEASE)

        code, (status,), _ = self._lease_main(["--lease-status-task-id", "lease-release"])
        self.assertEqual(code, 0)
        self.assertEqual((status["lease_status"], status["lease_expired"]),
                         ("LEASE_EXPIRED", True))
        # Reporting is read-only: an expired lease is never released by itself.
        self.assertEqual(lifecycle._read_registry_record(record_path), before)

        code, (line,), process_cwds = self._release(
            "lease-release", cwds=[("77", str(self.root / "elsewhere"))])
        self.assertEqual(code, 0)
        process_cwds.assert_called_once_with()
        self.assertEqual((line["result"], line["state"], line["worktree_kept"]),
                         ("LEASE_RELEASED", "LEASE_RELEASED", True))
        after = lifecycle._read_registry_record(record_path)
        self.assertEqual(after["state"], "LEASE_RELEASED")
        self.assertEqual(after["lease_released_by"], "pm-lease-test")
        self.assertEqual(after["lease_released_from_state"], "ACTIVE")
        self.assertIn("released by PM", after["evidence_refs"][-1])
        self.assertEqual({key: value for key, value in after.items() if key not in (
            "state", "last_verified_at", "lease_released_at", "lease_released_by",
            "lease_released_from_state", "evidence_refs")},
            {key: value for key, value in before.items() if key not in (
                "state", "last_verified_at", "evidence_refs")})
        self.assertEqual(lifecycle.lease_status(after), "LEASE_RELEASED")

        # The worktree, its lock, its branch and its commits are untouched.
        self.assertTrue((root / "tools" / "llmtool" / "result-fixture.txt").is_file())
        self.assertEqual(self._git(root, "rev-parse", "HEAD"), head)
        self.assertEqual(self._git(self.source, "rev-parse",
                                   "refs/heads/test/lease-release"), head)
        listing = lifecycle._worktree_listing(self.source, root)
        self.assertIsNotNone(listing)
        self.assertIn("locked", listing)

        # The old writer can no longer record a result, and a second release refuses.
        with patch.object(lifecycle, "_bootstrap") as bootstrap, \
                self.assertRaises(lifecycle.LifecycleError) as raised:
            self._record_result("lease-release", root)
        self.assertEqual(raised.exception.code, "D6_RECORD_RESULT_STATE_REFUSED")
        bootstrap.assert_not_called()
        code, (line,), _ = self._release("lease-release")
        self.assertEqual((code, line["reason"]), (1, "D6_LEASE_RELEASE_STATE_REFUSED"))
        self.assertEqual(lifecycle._read_registry_record(record_path), after)
        self.assertIn("LEASE_RELEASED", lifecycle.RETIRE_STATES)

    def test_expired_lease_with_live_or_unknown_process_is_refused(self):
        root, record_path = self._registered_result_fixture("lease-live")
        before = self._set_lease(record_path, self.EXPIRED_LEASE)
        code, (line,), _ = self._release(
            "lease-live", cwds=[("4242", str(root / "tools" / "llmtool"))])
        self.assertEqual(code, 1)
        self.assertEqual((line["result"], line["reason"], line["live_pids"]),
                         ("BLOCKED", "D6_LEASE_RELEASE_LIVE_PROCESS", ["4242"]))
        self.assertEqual(lifecycle._read_registry_record(record_path), before)

        code, (line,), _ = self._release("lease-live", cwds=None)
        self.assertEqual((code, line["reason"]), (1, "D6_LEASE_RELEASE_LIVENESS_UNKNOWN"))
        self.assertEqual(lifecycle._read_registry_record(record_path), before)
        self.assertTrue(root.is_dir())

    def test_unexpired_or_invalid_lease_is_refused_before_liveness(self):
        root, record_path = self._registered_result_fixture("lease-active")
        for value, reason in ((self.FUTURE_LEASE, "D6_LEASE_RELEASE_NOT_EXPIRED"),
                              ("not-a-time", "D6_LEASE_RELEASE_LEASE_INVALID")):
            before = self._set_lease(record_path, value)
            code, (line,), process_cwds = self._release("lease-active")
            self.assertEqual((code, line["result"], line["reason"]),
                             (1, "BLOCKED", reason))
            process_cwds.assert_not_called()
            self.assertEqual(lifecycle._read_registry_record(record_path), before)
        self.assertTrue(root.is_dir())

    def test_legacy_record_without_lease_reads_unexpired_and_stays_unchanged(self):
        root, record_path = self._registered_result_fixture("lease-legacy")
        before = lifecycle._read_registry_record(record_path)
        self.assertNotIn("lease_expires_at", before)
        self.assertEqual(lifecycle.lease_status(before), "LEASE_UNSET")

        code, (status,), _ = self._lease_main(["--lease-status-task-id", "lease-legacy"])
        self.assertEqual(code, 0)
        self.assertEqual((status["lease_status"], status["lease_expired"]),
                         ("LEASE_UNSET", False))
        self.assertNotIn("lease_expires_at", status)

        code, (line,), process_cwds = self._release("lease-legacy")
        self.assertEqual((code, line["reason"]), (1, "D6_LEASE_RELEASE_NOT_EXPIRED"))
        process_cwds.assert_not_called()
        self.assertEqual(lifecycle._read_registry_record(record_path), before)

        # A legacy writer keeps recording results; no lease is added.
        head = self._commit_file(root, "legacy\n", "legacy writer result")
        with patch.object(lifecycle, "_bootstrap",
                          side_effect=self._complete_bootstrap(record_path, head)), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self._record_result("lease-legacy", root), head)
        after = lifecycle._read_registry_record(record_path)
        self.assertEqual(after["head_sha"], head)
        self.assertNotIn("lease_expires_at", after)

    def test_lease_cli_options_are_exclusive_and_release_needs_pm_session(self):
        self._registered_result_fixture("lease-cli")
        cases = (
            ["--release-expired-lease", "lease-cli"],
            ["--pm-session", "pm-lease-test"],
            ["--lease-status-task-id", "lease-cli", "--pm-session", "pm-lease-test"],
            ["--release-expired-lease", "lease-cli", "--pm-session", "pm-lease-test",
             "--lease-status-task-id", "lease-cli"],
            ["--release-expired-lease", "lease-cli", "--pm-session", "pm-lease-test",
             "--retire-task-id", "lease-cli"],
            ["--release-expired-lease", "lease-cli", "--pm-session", "bad\nsession"],
            ["--release-expired-lease", "Bad/Task", "--pm-session", "pm-lease-test"],
        )
        for argv in cases:
            with self.subTest(argv=argv):
                error = io.StringIO()
                with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                        patch.object(lifecycle, "_process_cwds") as process_cwds, \
                        contextlib.redirect_stderr(error), \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(lifecycle._main(argv + ["--repo-root",
                                                             str(self.source)]), 1)
                self.assertEqual(error.getvalue(),
                                 "STOP: D6 lifecycle rejected (D6_TASK_INPUT_INVALID)\n")
                process_cwds.assert_not_called()
        code, (line,), _ = self._release("missing-lease-task")
        self.assertEqual((code, line["reason"]), (1, "D6_LEASE_RELEASE_UNKNOWN_TASK"))

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
        # A new registration carries a writer lease about 24 h from creation.
        expires = lifecycle._parse_utc(record["lease_expires_at"])
        self.assertIsNotNone(expires)
        self.assertLessEqual(expires - time.time(), lifecycle.LEASE_SECONDS)
        self.assertGreater(expires - time.time(), lifecycle.LEASE_SECONDS - 600)
        self.assertEqual(lifecycle.lease_status(record), "LEASE_ACTIVE")
        trust = lifecycle._load_trust()
        self.assertEqual(record["trust_sha256"], trust["sha256"])
        self.assertEqual(record["trust_binding_sha256"], trust["binding_sha256"])
        # ENV-2 (d6_worktree_env) just accepted this stamped record; the
        # task_completion reader used by review dispatch accepts it as well.
        from llmtool import task_completion
        registration = task_completion.read_d6_registration(
            "d6-integration-test", str(self.source / ".git"))
        self.assertEqual(registration.root, str(root))
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

        # After a trust rotation the stamped worktree is never resumed (7.3).
        self._rotate_trust_binding()
        before = lifecycle._read_registry_record(record_path)
        error_output = io.StringIO()
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_bootstrap") as bootstrap, \
                contextlib.redirect_stderr(error_output):
            self.assertEqual(lifecycle._main([
                "--resume-task-id", "d6-integration-test",
                "--resume-root", str(root), "--repo-root", str(self.source),
            ]), 1)
        bootstrap.assert_not_called()
        self.assertEqual(error_output.getvalue(),
                         "STOP: D6 lifecycle rejected (D6_TRUST_ROTATED)\n")
        self.assertEqual(lifecycle._read_registry_record(record_path), before)

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

        with patch.object(lifecycle, "__file__", str(task_lifecycle)):
            with self.assertRaises(lifecycle.LifecycleError) as raised:
                lifecycle._bootstrap(self.source, "test-task")

        self.assertEqual(raised.exception.code, "ENV_BOOTSTRAP_SOURCE_UNTRUSTED")
        self.assertFalse(marker_path.exists())

    def test_bootstrap_rejection_reports_only_one_bounded_stop_code(self):
        valid_stop = b"STOP: D6 lifecycle rejected (D6_UNREGISTERED_LOCAL_STATE)\n"
        cases = (
            ("valid stop", valid_stop, "ENV_BOOTSTRAP_REJECTED:D6_UNREGISTERED_LOCAL_STATE"),
            ("junk", b"helper failed without a code\n", "ENV_BOOTSTRAP_REJECTED"),
            ("extra output", valid_stop + b"private path\n", "ENV_BOOTSTRAP_REJECTED"),
            ("oversized", b"x" * (lifecycle.BOOTSTRAP_STDERR_LIMIT + 1) + valid_stop,
             "ENV_BOOTSTRAP_REJECTED"),
        )
        for label, stderr, expected in cases:
            with self.subTest(stderr=label):
                helper_source = (
                    "import sys\nsys.stderr.buffer.write(" + repr(stderr) + ")\n"
                    "sys.exit(1)\n"
                ).encode("utf-8")
                error_output = io.StringIO()
                with patch.object(lifecycle, "_trusted_dispatcher_git",
                                  return_value=(self.source, self.base, lambda *_: "",
                                                helper_source)), \
                        patch.object(lifecycle, "_approved_task_source", return_value=None), \
                        contextlib.redirect_stderr(error_output):
                    with self.assertRaises(lifecycle.LifecycleError) as raised:
                        lifecycle._bootstrap(self.source, "test-task")
                self.assertEqual(raised.exception.code, expected)
                self.assertEqual(error_output.getvalue(), "")

    def test_integrator_approval_bootstraps_exact_helper_blob_from_shared_store(self):
        task_id = "d6-approved-source-test"
        trusted_lifecycle = (self.source / "tools" / "llmtool" / "scripts" /
                             "d6_worktree_lifecycle.py")
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
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

        with patch.object(lifecycle, "__file__", str(trusted_lifecycle)), \
                patch.object(lifecycle.subprocess, "run", side_effect=capture_candidate):
            lifecycle._bootstrap(root, task_id)

        self.assertEqual(len(captured), 1)
        argv, kwargs = captured[0]
        self.assertEqual(argv[argv.index("-c") + 1], helper_bytes.decode())
        self.assertIn("--worktree-root", argv)
        self.assertEqual(argv[argv.index("--worktree-root") + 1], str(root))
        self.assertIn("--trust-sha256", argv)
        self.assertEqual(argv[argv.index("--trust-sha256") + 1],
                         lifecycle._load_trust()["sha256"])
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
        with patch.object(lifecycle, "__file__", str(trusted_lifecycle)):
            with self.assertRaises(lifecycle.LifecycleError) as raised:
                lifecycle._bootstrap(root, task_id)
        self.assertEqual(raised.exception.code, "ENV_BOOTSTRAP_SOURCE_UNTRUSTED")

    def test_execute_approved_lifecycle_runs_exact_blob_and_rejects_task_path(self):
        task_id = "d6-approved-lifecycle-runner-test"
        trusted_lifecycle = (self.source / "tools" / "llmtool" / "scripts" /
                             "d6_worktree_lifecycle.py")
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
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

        with patch.object(lifecycle, "__file__", str(trusted_lifecycle)):
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
                                             "--repo-root", str(self.source),
                                             "--trust-sha256",
                                             lifecycle._load_trust()["sha256"]])
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
        with patch.object(lifecycle, "__file__", str(lifecycle_path)):
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
                                   "--worktree-root", str(root),
                                   "--trust-sha256", d6env._load_trust()["sha256"]])
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
            with patch.object(lifecycle, "_load_trust", return_value={
                    "canonical_root": str(root), "tasks_root": str(root / "tasks"),
                    "origins": frozenset(("git@github.com:davidcatcat/davidcattoolsproject.git",)),
                    "sha256": "a" * 64}), \
                    patch.object(lifecycle, "__file__", str(trusted)), \
                    patch.object(lifecycle.subprocess, "run", side_effect=self._timeout) as run:
                with self.assertRaises(lifecycle.LifecycleError) as raised:
                    lifecycle._trusted_dispatcher_git()
        self.assertEqual(raised.exception.code, "ENV_BOOTSTRAP_UNAVAILABLE")
        self.assertEqual(run.call_args.kwargs["timeout"], lifecycle.GIT_TIMEOUT_SECONDS)


if __name__ == "__main__":
    unittest.main()
