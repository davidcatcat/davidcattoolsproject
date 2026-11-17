"""Tests for bootstrap source allowance and lifecycle binding (#1817 PR-1)."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
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
from llmtool import task_advance  # noqa: E402


def _actual_cache_fingerprint():
    return worktreeenv.fingerprint_id({
        "implementation": sys.implementation.name,
        "python_version": "%d.%d.%d" % sys.version_info[:3],
        "platform": sys.platform,
        "machine": platform.machine(),
    })


def _with_child_trust_home(home, code):
    return (
        "import pwd as _fixture_pwd\n"
        "_fixture_getpwuid = _fixture_pwd.getpwuid\n"
        "def _fixture_temp_home(uid):\n"
        "    entry = _fixture_getpwuid(uid)\n"
        "    return _fixture_pwd.struct_passwd(\n"
        "        tuple(entry[:5]) + (" + repr(home) + ",) + tuple(entry[6:]))\n"
        "_fixture_pwd.getpwuid = _fixture_temp_home\n"
        "exec(compile(" + repr(code) + ", '<string>', 'exec'))\n")


class BootstrapSourceAllowanceTests(unittest.TestCase):
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
        (tools / "Makefile").write_text("env-bootstrap:\n\t@true\n", encoding="utf-8")
        (scripts / "worktree_env.py").write_text("# test helper\n", encoding="utf-8")
        shutil.copy2(SCRIPTS / "d6_worktree_env.py", scripts / "d6_worktree_env.py")
        shutil.copy2(SCRIPTS / "d6_worktree_lifecycle.py", scripts / "d6_worktree_lifecycle.py")
        (tools / ".gitignore").write_bytes(
            (Path(__file__).resolve().parents[1] / ".gitignore").read_bytes())
        (tools / "worktree-env.lock").write_text("# test lock\n", encoding="utf-8")
        (tools / "pyproject.toml").write_text("[build-system]\nrequires = []\n", encoding="utf-8")
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
        self.trust_path = trust_dir / "trust.json"
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

    def _registered_result_fixture(self, task_id="allowance-test"):
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
                      "report_sha256": hashlib.sha256(report_bytes).hexdigest()}}
        record_path = self.source / ".git" / "llmtool-worktrees" / (task_id + ".json")
        record_path.write_text(json.dumps(record), encoding="utf-8")
        record_path.chmod(0o600)
        return root, record_path

    def _commit_file(self, root, relpath, content, message):
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        subprocess.run(["/usr/bin/git", "add", str(path)], cwd=root, check=True)
        subprocess.run(["/usr/bin/git", "-c", "user.name=D6 test",
                        "-c", "user.email=d6@example.invalid", "commit", "-m", message],
                       cwd=root, check=True, capture_output=True)
        return subprocess.check_output(["/usr/bin/git", "rev-parse", "HEAD"],
                                       cwd=root, text=True).strip()

    def test_bootstrap_sources_contents(self):
        expected = {
            "tools/llmtool/Makefile",
            "tools/llmtool/.gitignore",
            "tools/llmtool/scripts/worktree_env.py",
            "tools/llmtool/worktree-env.lock",
            "tools/llmtool/pyproject.toml",
            "tools/llmtool/setup.py",
            "tools/llmtool/setup.cfg",
            "tools/llmtool/MANIFEST.in",
            "tools/llmtool/scripts/d6_worktree_env.py",
        }
        self.assertEqual(lifecycle.BOOTSTRAP_SOURCES, expected)

    def test_allowance_is_exact_intersection_bound_to_head(self):
        root, record_path = self._registered_result_fixture("intersection-test")
        head = self._commit_file(root, "tools/llmtool/pyproject.toml",
                                 "[build-system]\n# version bump\n", "bump pyproject")
        write_paths = [
            "tools/llmtool/pyproject.toml",
            "tools/llmtool/scripts/d6_worktree_env.py",
            "src/some_other_code.py",
        ]

        def complete_bootstrap(_root, _task_id):
            current = lifecycle._read_registry_record(record_path)
            current["env2_pass_marker"] = dict(
                current["env2_pass_marker"],
                submitted_head_sha=head,
                current_head_sha=head)
            lifecycle._replace_record(record_path, current)

        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_bootstrap", side_effect=complete_bootstrap), \
                contextlib.redirect_stdout(io.StringIO()):
            recorded = lifecycle.record_result(
                task_id="intersection-test", canonical_root=self.source,
                root=root, workspace_root=self.tasks, write_paths=write_paths)

        self.assertEqual(recorded, head)
        record = lifecycle._read_registry_record(record_path)
        self.assertIn("env2_source_allowance", record)
        allowance = record["env2_source_allowance"]
        self.assertEqual(allowance["schema_version"], 1)
        self.assertEqual(allowance["head_sha"], head)
        self.assertEqual(allowance["base_sha"], self.base)
        self.assertEqual(allowance["paths"], [
            "tools/llmtool/pyproject.toml",
            "tools/llmtool/scripts/d6_worktree_env.py",
        ])
        expected_digest = hashlib.sha256(
            json.dumps(write_paths, sort_keys=True, ensure_ascii=True,
                       separators=(",", ":")).encode("ascii")).hexdigest()
        self.assertEqual(allowance["write_paths_sha256"], expected_digest)

    def test_empty_intersection_removes_allowance(self):
        root, record_path = self._registered_result_fixture("empty-intersection-test")
        # Pre-seed an allowance
        record = lifecycle._read_registry_record(record_path)
        record["env2_source_allowance"] = {
            "schema_version": 1,
            "head_sha": self.base,
            "base_sha": self.base,
            "paths": ["tools/llmtool/pyproject.toml"],
            "write_paths_sha256": "0" * 64,
        }
        lifecycle._replace_record(record_path, record)

        head = self._commit_file(root, "tools/llmtool/result-fixture.txt",
                                 "clean content\n", "non-bootstrap change")
        write_paths = ["tools/llmtool/result-fixture.txt"]

        def complete_bootstrap(_root, _task_id):
            current = lifecycle._read_registry_record(record_path)
            current["env2_pass_marker"] = dict(
                current["env2_pass_marker"],
                submitted_head_sha=head,
                current_head_sha=head)
            lifecycle._replace_record(record_path, current)

        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_bootstrap", side_effect=complete_bootstrap), \
                contextlib.redirect_stdout(io.StringIO()):
            lifecycle.record_result(
                task_id="empty-intersection-test", canonical_root=self.source,
                root=root, workspace_root=self.tasks, write_paths=write_paths)

        after = lifecycle._read_registry_record(record_path)
        self.assertNotIn("env2_source_allowance", after)

    def test_write_paths_none_removes_allowance(self):
        root, record_path = self._registered_result_fixture("none-write-paths-test")
        record = lifecycle._read_registry_record(record_path)
        record["env2_source_allowance"] = {
            "schema_version": 1,
            "head_sha": self.base,
            "base_sha": self.base,
            "paths": ["tools/llmtool/pyproject.toml"],
            "write_paths_sha256": "0" * 64,
        }
        lifecycle._replace_record(record_path, record)

        head = self._commit_file(root, "tools/llmtool/result-fixture.txt",
                                 "clean content\n", "non-bootstrap change")

        def complete_bootstrap(_root, _task_id):
            current = lifecycle._read_registry_record(record_path)
            current["env2_pass_marker"] = dict(
                current["env2_pass_marker"],
                submitted_head_sha=head,
                current_head_sha=head)
            lifecycle._replace_record(record_path, current)

        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_bootstrap", side_effect=complete_bootstrap), \
                contextlib.redirect_stdout(io.StringIO()):
            lifecycle.record_result(
                task_id="none-write-paths-test", canonical_root=self.source,
                root=root, workspace_root=self.tasks, write_paths=None)

        after = lifecycle._read_registry_record(record_path)
        self.assertNotIn("env2_source_allowance", after)

    def test_cli_record_result_removes_allowance(self):
        root, record_path = self._registered_result_fixture("cli-remove-test")
        record = lifecycle._read_registry_record(record_path)
        record["env2_source_allowance"] = {
            "schema_version": 1,
            "head_sha": self.base,
            "base_sha": self.base,
            "paths": ["tools/llmtool/pyproject.toml"],
            "write_paths_sha256": "0" * 64,
        }
        lifecycle._replace_record(record_path, record)

        head = self._commit_file(root, "tools/llmtool/result-fixture.txt",
                                 "clean content\n", "non-bootstrap change")

        def complete_bootstrap(_root, _task_id):
            current = lifecycle._read_registry_record(record_path)
            current["env2_pass_marker"] = dict(
                current["env2_pass_marker"],
                submitted_head_sha=head,
                current_head_sha=head)
            lifecycle._replace_record(record_path, current)

        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_bootstrap", side_effect=complete_bootstrap), \
                contextlib.redirect_stdout(io.StringIO()):
            code = lifecycle._main([
                "--record-result-task-id", "cli-remove-test",
                "--repo-root", str(self.source),
            ])

        self.assertEqual(code, 0)
        after = lifecycle._read_registry_record(record_path)
        self.assertNotIn("env2_source_allowance", after)

    def test_rollback_restores_previous_allowance_and_marker(self):
        root, record_path = self._registered_result_fixture("rollback-test")
        initial_allowance = {
            "schema_version": 1,
            "head_sha": self.base,
            "base_sha": self.base,
            "paths": ["tools/llmtool/Makefile"],
            "write_paths_sha256": "1" * 64,
        }
        before = lifecycle._read_registry_record(record_path)
        before["env2_source_allowance"] = initial_allowance
        lifecycle._replace_record(record_path, before)

        self._commit_file(root, "tools/llmtool/pyproject.toml",
                          "[build-system]\n# changed\n", "change bootstrap")

        def fail_bootstrap(_root, _task_id):
            raise lifecycle.LifecycleError("ENV_BOOTSTRAP_REJECTED")

        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_bootstrap", side_effect=fail_bootstrap), \
                self.assertRaises(lifecycle.LifecycleError) as raised:
            lifecycle.record_result(
                task_id="rollback-test", canonical_root=self.source,
                root=root, workspace_root=self.tasks,
                write_paths=["tools/llmtool/pyproject.toml"])

        self.assertEqual(raised.exception.code, "ENV_BOOTSTRAP_REJECTED")
        after = lifecycle._read_registry_record(record_path)
        self.assertEqual(after, before)
        self.assertEqual(after["env2_source_allowance"], initial_allowance)
        self.assertEqual(after["env2_pass_marker"], before["env2_pass_marker"])

    def test_c3_accepts_schema1_marker_as_today(self):
        root, record_path = self._registered_result_fixture("c3-schema1-test")
        head = self._commit_file(root, "tools/llmtool/result-fixture.txt",
                                 "change\n", "change")

        def complete_bootstrap(_root, _task_id):
            current = lifecycle._read_registry_record(record_path)
            # Schema 1 marker: exactly the 9 keys
            current["env2_pass_marker"] = {
                "schema_version": 1,
                "result": "PASS",
                "task_id": "c3-schema1-test",
                "root_realpath": str(root),
                "submitted_head_sha": head,
                "current_head_sha": head,
                "tooling_sha": self.base,
                "environment_fingerprint": _actual_cache_fingerprint(),
                "report_sha256": "f" * 64,
            }
            lifecycle._replace_record(record_path, current)

        # Schema 1 marker is accepted whether allowance is present or absent
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_bootstrap", side_effect=complete_bootstrap), \
                contextlib.redirect_stdout(io.StringIO()):
            recorded = lifecycle.record_result(
                task_id="c3-schema1-test", canonical_root=self.source,
                root=root, workspace_root=self.tasks,
                write_paths=["tools/llmtool/pyproject.toml"])

        self.assertEqual(recorded, head)

    def test_c3_accepts_schema2_marker_inside_allowance(self):
        root, record_path = self._registered_result_fixture("c3-schema2-pass-test")
        head = self._commit_file(root, "tools/llmtool/pyproject.toml",
                                 "[build-system]\n# changed\n", "change pyproject")

        def complete_bootstrap(_root, _task_id):
            current = lifecycle._read_registry_record(record_path)
            # Schema 2 marker: exactly the 12 keys
            current["env2_pass_marker"] = {
                "schema_version": 2,
                "result": "PASS",
                "task_id": "c3-schema2-pass-test",
                "root_realpath": str(root),
                "submitted_head_sha": head,
                "current_head_sha": head,
                "tooling_sha": self.base,
                "environment_fingerprint": _actual_cache_fingerprint(),
                "report_sha256": "f" * 64,
                "environment_mode": "CARRIED",
                "carried_from_head_sha": self.base,
                "allowed_bootstrap_paths": ["tools/llmtool/pyproject.toml"],
            }
            lifecycle._replace_record(record_path, current)

        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_bootstrap", side_effect=complete_bootstrap), \
                contextlib.redirect_stdout(io.StringIO()):
            recorded = lifecycle.record_result(
                task_id="c3-schema2-pass-test", canonical_root=self.source,
                root=root, workspace_root=self.tasks,
                write_paths=["tools/llmtool/pyproject.toml", "tools/llmtool/Makefile"])

        self.assertEqual(recorded, head)

    def test_c3_refuses_schema2_marker_path_outside_allowance(self):
        root, record_path = self._registered_result_fixture("c3-outside-test")
        head = self._commit_file(root, "tools/llmtool/pyproject.toml",
                                 "[build-system]\n# changed\n", "change pyproject")

        def complete_bootstrap(_root, _task_id):
            current = lifecycle._read_registry_record(record_path)
            # Carried marker includes Makefile, which is not in write_paths/allowance
            current["env2_pass_marker"] = {
                "schema_version": 2,
                "result": "PASS",
                "task_id": "c3-outside-test",
                "root_realpath": str(root),
                "submitted_head_sha": head,
                "current_head_sha": head,
                "tooling_sha": self.base,
                "environment_fingerprint": _actual_cache_fingerprint(),
                "report_sha256": "f" * 64,
                "environment_mode": "CARRIED",
                "carried_from_head_sha": self.base,
                "allowed_bootstrap_paths": [
                    "tools/llmtool/Makefile",
                    "tools/llmtool/pyproject.toml",
                ],
            }
            lifecycle._replace_record(record_path, current)

        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_bootstrap", side_effect=complete_bootstrap), \
                self.assertRaises(lifecycle.LifecycleError) as raised:
            lifecycle.record_result(
                task_id="c3-outside-test", canonical_root=self.source,
                root=root, workspace_root=self.tasks,
                write_paths=["tools/llmtool/pyproject.toml"])

        self.assertEqual(raised.exception.code, "D6_RECORD_RESULT_ENV2_MARKER_INVALID")

    def test_c3_refuses_schema2_marker_stale_allowance_head(self):
        root, record_path = self._registered_result_fixture("c3-stale-head-test")
        head = self._commit_file(root, "tools/llmtool/pyproject.toml",
                                 "[build-system]\n# changed\n", "change pyproject")

        def tamper_allowance_head(_root, _task_id):
            current = lifecycle._read_registry_record(record_path)
            # Tamper the allowance head to simulate a stale allowance
            current["env2_source_allowance"]["head_sha"] = "0" * 40
            current["env2_pass_marker"] = {
                "schema_version": 2,
                "result": "PASS",
                "task_id": "c3-stale-head-test",
                "root_realpath": str(root),
                "submitted_head_sha": head,
                "current_head_sha": head,
                "tooling_sha": self.base,
                "environment_fingerprint": _actual_cache_fingerprint(),
                "report_sha256": "f" * 64,
                "environment_mode": "CARRIED",
                "carried_from_head_sha": self.base,
                "allowed_bootstrap_paths": ["tools/llmtool/pyproject.toml"],
            }
            lifecycle._replace_record(record_path, current)

        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_bootstrap", side_effect=tamper_allowance_head), \
                self.assertRaises(lifecycle.LifecycleError) as raised:
            lifecycle.record_result(
                task_id="c3-stale-head-test", canonical_root=self.source,
                root=root, workspace_root=self.tasks,
                write_paths=["tools/llmtool/pyproject.toml"])

        self.assertEqual(raised.exception.code, "D6_RECORD_RESULT_ENV2_MARKER_INVALID")

    def test_c3_refuses_schema2_marker_when_allowance_missing(self):
        root, record_path = self._registered_result_fixture("c3-no-allowance-test")
        head = self._commit_file(root, "tools/llmtool/result-fixture.txt",
                                 "change\n", "change")

        def complete_bootstrap(_root, _task_id):
            current = lifecycle._read_registry_record(record_path)
            current["env2_pass_marker"] = {
                "schema_version": 2,
                "result": "PASS",
                "task_id": "c3-no-allowance-test",
                "root_realpath": str(root),
                "submitted_head_sha": head,
                "current_head_sha": head,
                "tooling_sha": self.base,
                "environment_fingerprint": _actual_cache_fingerprint(),
                "report_sha256": "f" * 64,
                "environment_mode": "CARRIED",
                "carried_from_head_sha": self.base,
                "allowed_bootstrap_paths": ["tools/llmtool/pyproject.toml"],
            }
            lifecycle._replace_record(record_path, current)

        # write_paths has no bootstrap sources, so no allowance is written
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_bootstrap", side_effect=complete_bootstrap), \
                self.assertRaises(lifecycle.LifecycleError) as raised:
            lifecycle.record_result(
                task_id="c3-no-allowance-test", canonical_root=self.source,
                root=root, workspace_root=self.tasks,
                write_paths=["tools/llmtool/result-fixture.txt"])

        self.assertEqual(raised.exception.code, "D6_RECORD_RESULT_ENV2_MARKER_INVALID")

    def test_c3_refuses_schema2_marker_shape_or_version_mismatch(self):
        root, record_path = self._registered_result_fixture("c3-mismatch-test")
        head = self._commit_file(root, "tools/llmtool/pyproject.toml",
                                 "[build-system]\n# changed\n", "change pyproject")

        # Case 1: 9-key marker but with schema_version == 2
        def complete_v2_with_9_keys(_root, _task_id):
            current = lifecycle._read_registry_record(record_path)
            current["env2_pass_marker"] = {
                "schema_version": 2,
                "result": "PASS",
                "task_id": "c3-mismatch-test",
                "root_realpath": str(root),
                "submitted_head_sha": head,
                "current_head_sha": head,
                "tooling_sha": self.base,
                "environment_fingerprint": _actual_cache_fingerprint(),
                "report_sha256": "f" * 64,
            }
            lifecycle._replace_record(record_path, current)

        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_bootstrap", side_effect=complete_v2_with_9_keys), \
                self.assertRaises(lifecycle.LifecycleError) as raised:
            lifecycle.record_result(
                task_id="c3-mismatch-test", canonical_root=self.source,
                root=root, workspace_root=self.tasks,
                write_paths=["tools/llmtool/pyproject.toml"])
        self.assertEqual(raised.exception.code, "D6_RECORD_RESULT_ENV2_MARKER_INVALID")

        # Case 2: 12-key marker but with schema_version == 1
        def complete_v1_with_12_keys(_root, _task_id):
            current = lifecycle._read_registry_record(record_path)
            current["env2_pass_marker"] = {
                "schema_version": 1,
                "result": "PASS",
                "task_id": "c3-mismatch-test",
                "root_realpath": str(root),
                "submitted_head_sha": head,
                "current_head_sha": head,
                "tooling_sha": self.base,
                "environment_fingerprint": _actual_cache_fingerprint(),
                "report_sha256": "f" * 64,
                "environment_mode": "CARRIED",
                "carried_from_head_sha": self.base,
                "allowed_bootstrap_paths": ["tools/llmtool/pyproject.toml"],
            }
            lifecycle._replace_record(record_path, current)

        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_bootstrap", side_effect=complete_v1_with_12_keys), \
                self.assertRaises(lifecycle.LifecycleError) as raised:
            lifecycle.record_result(
                task_id="c3-mismatch-test", canonical_root=self.source,
                root=root, workspace_root=self.tasks,
                write_paths=["tools/llmtool/pyproject.toml"])
        self.assertEqual(raised.exception.code, "D6_RECORD_RESULT_ENV2_MARKER_INVALID")

        # Case 3: 9-key marker but with schema_version == 3
        def complete_v3_with_9_keys(_root, _task_id):
            current = lifecycle._read_registry_record(record_path)
            current["env2_pass_marker"] = {
                "schema_version": 3,
                "result": "PASS",
                "task_id": "c3-mismatch-test",
                "root_realpath": str(root),
                "submitted_head_sha": head,
                "current_head_sha": head,
                "tooling_sha": self.base,
                "environment_fingerprint": _actual_cache_fingerprint(),
                "report_sha256": "f" * 64,
            }
            lifecycle._replace_record(record_path, current)

        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_bootstrap", side_effect=complete_v3_with_9_keys), \
                self.assertRaises(lifecycle.LifecycleError) as raised:
            lifecycle.record_result(
                task_id="c3-mismatch-test", canonical_root=self.source,
                root=root, workspace_root=self.tasks,
                write_paths=["tools/llmtool/pyproject.toml"])
        self.assertEqual(raised.exception.code, "D6_RECORD_RESULT_ENV2_MARKER_INVALID")

        # Case 4: mixed keys (schema-1 keys plus partial schema-2 key, 10 keys)
        def complete_mixed_keys(_root, _task_id):
            current = lifecycle._read_registry_record(record_path)
            current["env2_pass_marker"] = {
                "schema_version": 1,
                "result": "PASS",
                "task_id": "c3-mismatch-test",
                "root_realpath": str(root),
                "submitted_head_sha": head,
                "current_head_sha": head,
                "tooling_sha": self.base,
                "environment_fingerprint": _actual_cache_fingerprint(),
                "report_sha256": "f" * 64,
                "environment_mode": "CARRIED",
            }
            lifecycle._replace_record(record_path, current)

        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_bootstrap", side_effect=complete_mixed_keys), \
                self.assertRaises(lifecycle.LifecycleError) as raised:
            lifecycle.record_result(
                task_id="c3-mismatch-test", canonical_root=self.source,
                root=root, workspace_root=self.tasks,
                write_paths=["tools/llmtool/pyproject.toml"])
        self.assertEqual(raised.exception.code, "D6_RECORD_RESULT_ENV2_MARKER_INVALID")

        # Case 5: missing keys (schema-1 keys missing report_sha256, 8 keys)
        def complete_missing_keys(_root, _task_id):
            current = lifecycle._read_registry_record(record_path)
            current["env2_pass_marker"] = {
                "schema_version": 1,
                "result": "PASS",
                "task_id": "c3-mismatch-test",
                "root_realpath": str(root),
                "submitted_head_sha": head,
                "current_head_sha": head,
                "tooling_sha": self.base,
                "environment_fingerprint": _actual_cache_fingerprint(),
            }
            lifecycle._replace_record(record_path, current)

        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_bootstrap", side_effect=complete_missing_keys), \
                self.assertRaises(lifecycle.LifecycleError) as raised:
            lifecycle.record_result(
                task_id="c3-mismatch-test", canonical_root=self.source,
                root=root, workspace_root=self.tasks,
                write_paths=["tools/llmtool/pyproject.toml"])
        self.assertEqual(raised.exception.code, "D6_RECORD_RESULT_ENV2_MARKER_INVALID")

        # Case 6: missing keys from schema-2 (missing carried_from_head_sha, 11 keys)
        def complete_missing_schema2_keys(_root, _task_id):
            current = lifecycle._read_registry_record(record_path)
            current["env2_pass_marker"] = {
                "schema_version": 2,
                "result": "PASS",
                "task_id": "c3-mismatch-test",
                "root_realpath": str(root),
                "submitted_head_sha": head,
                "current_head_sha": head,
                "tooling_sha": self.base,
                "environment_fingerprint": _actual_cache_fingerprint(),
                "report_sha256": "f" * 64,
                "environment_mode": "CARRIED",
                "allowed_bootstrap_paths": ["tools/llmtool/pyproject.toml"],
            }
            lifecycle._replace_record(record_path, current)

        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_bootstrap", side_effect=complete_missing_schema2_keys), \
                self.assertRaises(lifecycle.LifecycleError) as raised:
            lifecycle.record_result(
                task_id="c3-mismatch-test", canonical_root=self.source,
                root=root, workspace_root=self.tasks,
                write_paths=["tools/llmtool/pyproject.toml"])
        self.assertEqual(raised.exception.code, "D6_RECORD_RESULT_ENV2_MARKER_INVALID")

        # Case 7: mixed keys with unexpected extra field (13 keys)
        def complete_mixed_extra_keys(_root, _task_id):
            current = lifecycle._read_registry_record(record_path)
            current["env2_pass_marker"] = {
                "schema_version": 2,
                "result": "PASS",
                "task_id": "c3-mismatch-test",
                "root_realpath": str(root),
                "submitted_head_sha": head,
                "current_head_sha": head,
                "tooling_sha": self.base,
                "environment_fingerprint": _actual_cache_fingerprint(),
                "report_sha256": "f" * 64,
                "environment_mode": "CARRIED",
                "carried_from_head_sha": self.base,
                "allowed_bootstrap_paths": ["tools/llmtool/pyproject.toml"],
                "unexpected_field": "disallowed",
            }
            lifecycle._replace_record(record_path, current)

        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_bootstrap", side_effect=complete_mixed_extra_keys), \
                self.assertRaises(lifecycle.LifecycleError) as raised:
            lifecycle.record_result(
                task_id="c3-mismatch-test", canonical_root=self.source,
                root=root, workspace_root=self.tasks,
                write_paths=["tools/llmtool/pyproject.toml"])
        self.assertEqual(raised.exception.code, "D6_RECORD_RESULT_ENV2_MARKER_INVALID")

    def test_record_result_refuses_invalid_write_paths(self):
        root, _record_path = self._registered_result_fixture("bad-write-paths-test")
        self._commit_file(root, "tools/llmtool/result-fixture.txt", "c\n", "msg")

        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}):
            for bad_paths in ("not-a-list", [123], ["ok", None]):
                with self.subTest(bad_paths=bad_paths), \
                        self.assertRaises(lifecycle.LifecycleError) as raised:
                    lifecycle.record_result(
                        task_id="bad-write-paths-test", canonical_root=self.source,
                        root=root, workspace_root=self.tasks, write_paths=bad_paths)
                self.assertEqual(raised.exception.code, "D6_TASK_INPUT_INVALID")

    def test_publish_task_passes_task_write_paths(self):
        write_paths = ["tools/llmtool/pyproject.toml", "src/mod.py"]
        record = {
            "task_id": "task-publish-1",
            "developer": {"write_paths": write_paths},
        }
        d6_binding = {
            "root": str(self.source),
            "common_dir": str(self.source / ".git"),
            "branch": "main",
            "task_id": "task-publish-1",
            "recorded_result_head": "0" * 40,
        }
        # Fake runners
        git_outputs = {
            ("rev-parse", "--show-toplevel"): str(self.source) + "\n",
            ("branch", "--show-current"): "feature-branch\n",
            ("ls-files", "--others", "--ignored", "--exclude-standard", "-z"): "",
            ("diff", "--no-renames", "--name-only", "-z", "HEAD"): "",
            ("ls-files", "--others", "--exclude-standard", "-z"): "",
            ("ls-remote", "--heads", "origin", "refs/heads/feature-branch"): "",
            ("rev-parse", "--verify", "HEAD"): self.base + "\n",
            ("fetch", "origin", "main"): "",
        }

        def fake_git(root, args):
            key = tuple(args)
            if key in git_outputs:
                return subprocess.CompletedProcess(args, 0, git_outputs[key], "")
            if args[:2] == ["merge-base", "--is-ancestor"]:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        recorded_calls = []

        def fake_record_result(**kwargs):
            recorded_calls.append(kwargs)
            return self.base

        d6_binding["branch"] = "feature-branch"
        with patch.object(task_advance, "_open_pr_present", return_value=True), \
                patch.object(task_advance, "_publish_pr", return_value=99):
            task_advance._publish_task(
                record, d6_binding, "task-publish-1",
                pre_pr_gate=lambda _h, _b: None,
                git_runner=fake_git,
                gh_runner=lambda *_a: subprocess.CompletedProcess([], 0, b"", b""),
                record_result_call=fake_record_result)

        self.assertEqual(len(recorded_calls), 1)
        self.assertEqual(recorded_calls[0]["write_paths"], write_paths)
        self.assertEqual(recorded_calls[0]["task_id"], "task-publish-1")

    def test_record_d6_result_forwards_write_paths(self):
        called = {}
        executed = []

        def mock_record_result(**kwargs):
            called.update(kwargs)
            return "abc"

        with patch("importlib.util.spec_from_file_location") as mock_spec:
            mock_module = type("M", (), {"record_result": mock_record_result})
            mock_loader = type("L", (), {
                "exec_module": lambda self, module: executed.append(module),
            })
            mock_spec.return_value = type("S", (), {"loader": mock_loader()})
            with patch("importlib.util.module_from_spec", return_value=mock_module):
                result = task_advance._record_d6_result(
                    task_id="t1", canonical_root="/canon", root="/root",
                    write_paths=["tools/llmtool/pyproject.toml"])

        self.assertEqual(result, "abc")
        self.assertEqual(executed, [mock_module])
        self.assertEqual(called, {
            "task_id": "t1",
            "canonical_root": "/canon",
            "root": "/root",
            "write_paths": ["tools/llmtool/pyproject.toml"],
        })
