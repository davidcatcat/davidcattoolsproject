"""Unit tests for D6 ENV-2 PASS marker validation (#1817 PR-2)."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

from llmtool.errors import LLMToolError
from llmtool import task_completion


class Env2MarkerValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.common = str(self.root / ".git")

        # Initialize Git repo
        subprocess.run(["/usr/bin/git", "init", "-b", "main"], cwd=self.root,
                       check=True, capture_output=True)
        subprocess.run(["/usr/bin/git", "config", "user.name", "ENV2 Test"],
                       cwd=self.root, check=True)
        subprocess.run(["/usr/bin/git", "config", "user.email", "env2@example.invalid"],
                       cwd=self.root, check=True)

        tools = self.root / "tools" / "llmtool"
        scripts = tools / "scripts"
        scripts.mkdir(parents=True)
        (tools / "Makefile").write_text("env-bootstrap:\n\t@true\n", encoding="utf-8")
        (tools / ".gitignore").write_text("/.d6-bootstrap-report.json\n", encoding="utf-8")
        (scripts / "worktree_env.py").write_text("# worktree helper\n", encoding="utf-8")
        (tools / "worktree-env.lock").write_text("# lock file\n", encoding="utf-8")
        (tools / "pyproject.toml").write_text("[build-system]\nrequires = []\n", encoding="utf-8")
        (scripts / "d6_worktree_env.py").write_text("# d6 helper\n", encoding="utf-8")

        subprocess.run(["/usr/bin/git", "add", "tools"], cwd=self.root, check=True)
        subprocess.run(["/usr/bin/git", "commit", "-m", "base commit"],
                       cwd=self.root, check=True, capture_output=True)
        self.base_sha = subprocess.check_output(
            ["/usr/bin/git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip()

        # Create worktree
        self.worktree = self.root / "tasks" / "test-task"
        self.worktree.parent.mkdir(mode=0o700)
        subprocess.run(["/usr/bin/git", "worktree", "add", "-b", "test-branch",
                        str(self.worktree), self.base_sha],
                       cwd=self.root, check=True, capture_output=True)
        self.worktree.chmod(0o700)

        # Commit a bootstrap source change in the worktree
        (self.worktree / "tools" / "llmtool" / "pyproject.toml").write_text(
            "[build-system]\n# version bump\nrequires = []\n", encoding="utf-8")
        subprocess.run(["/usr/bin/git", "add", "tools/llmtool/pyproject.toml"],
                       cwd=self.worktree, check=True)
        subprocess.run(["/usr/bin/git", "commit", "-m", "bump pyproject version"],
                       cwd=self.worktree, check=True, capture_output=True)
        self.head_sha = subprocess.check_output(
            ["/usr/bin/git", "rev-parse", "HEAD"], cwd=self.worktree, text=True).strip()

        (Path(self.common) / "llmtool-worktrees").mkdir(mode=0o700)
        self.task_id = "test-task"
        self.fingerprint = "cpython3.13-darwin-arm64-012345abcdef"

    def _write_report(self, report_dict, root=None):
        target_root = root or self.worktree
        report_path = target_root / "tools" / "llmtool" / ".d6-bootstrap-report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        raw = (json.dumps(report_dict, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        report_path.write_bytes(raw)
        report_path.chmod(0o600)
        return hashlib.sha256(raw).hexdigest()

    def _build_valid_venv_identity(self):
        stat_info = self.worktree.stat()
        return {
            "schema_version": 1,
            "path": "tools/llmtool/.venv",
            "device": stat_info.st_dev,
            "inode": stat_info.st_ino,
            "uid": os.getuid(),
            "pyvenv_cfg_sha256": "1" * 64,
            "interpreter_realpath": "/usr/bin/python3",
            "interpreter_stat": {
                "device": stat_info.st_dev,
                "inode": stat_info.st_ino,
                "size": 1024,
                "mtime_ns": 1000,
            },
            "site_packages_sha256": "2" * 64,
        }

    def _create_registration(self, marker, *, head_sha=None, source_sha=None,
                             approved_tooling_sha=None):
        current_head = head_sha or self.head_sha
        source = source_sha or self.base_sha
        stat_info = self.worktree.stat()
        reg_dict = {
            "schema_version": 1,
            "task_id": self.task_id,
            "trusted_agent_id": "test-agent",
            "session_ref": "test-session",
            "root_realpath": str(self.worktree),
            "physical_identity": {
                "device": stat_info.st_dev,
                "inode": stat_info.st_ino,
                "uid": stat_info.st_uid,
                "mode": oct(stat.S_IMODE(stat_info.st_mode)),
            },
            "git_common_dir": self.common,
            "branch": "test-branch",
            "base_sha": source,
            "head_sha": current_head,
            "state": "ACTIVE",
            "lock_state": "LOCKED",
            "env2_pass_marker": marker,
        }
        if approved_tooling_sha is not None:
            reg_dict["env2_approved_tooling_sha"] = approved_tooling_sha
        reg_path = Path(self.common) / "llmtool-worktrees" / f"{self.task_id}.json"
        reg_path.write_text(json.dumps(reg_dict), encoding="utf-8")
        reg_path.chmod(0o600)
        return task_completion.read_d6_registration(self.task_id, self.common)

    def test_schema1_marker_accepted_with_various_write_paths(self):
        report = {
            "schema_version": 1,
            "result": "PASS",
            "status": "env_created",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "cache_fingerprint": self.fingerprint,
        }
        report_sha256 = self._write_report(report)
        marker = {
            "schema_version": 1,
            "result": "PASS",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "submitted_head_sha": self.head_sha,
            "current_head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "environment_fingerprint": self.fingerprint,
            "report_sha256": report_sha256,
        }
        reg = self._create_registration(marker)

        # write_paths omitted (default None)
        validated = task_completion.validate_env2_pass_marker(
            reg, expected_root=str(self.worktree), current_head=self.head_sha)
        self.assertEqual(validated, marker)

        # write_paths is None explicitly
        validated = task_completion.validate_env2_pass_marker(
            reg, expected_root=str(self.worktree), current_head=self.head_sha,
            write_paths=None)
        self.assertEqual(validated, marker)

        # write_paths is a list that excludes every bootstrap source
        validated = task_completion.validate_env2_pass_marker(
            reg, expected_root=str(self.worktree), current_head=self.head_sha,
            write_paths=["src/unrelated.py"])
        self.assertEqual(validated, marker)

    def test_schema1_report_accepted_with_or_without_venv_identity(self):
        # Case 1: without venv_identity
        report = {
            "schema_version": 1,
            "result": "PASS",
            "status": "env_reused",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "cache_fingerprint": self.fingerprint,
        }
        report_sha256 = self._write_report(report)
        marker = {
            "schema_version": 1,
            "result": "PASS",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "submitted_head_sha": self.head_sha,
            "current_head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "environment_fingerprint": self.fingerprint,
            "report_sha256": report_sha256,
        }
        reg = self._create_registration(marker)
        self.assertEqual(
            task_completion.validate_env2_pass_marker(
                reg, expected_root=str(self.worktree), current_head=self.head_sha),
            marker)

        # Case 2: with venv_identity
        report["venv_identity"] = self._build_valid_venv_identity()
        marker["report_sha256"] = self._write_report(report)
        reg = self._create_registration(marker)
        self.assertEqual(
            task_completion.validate_env2_pass_marker(
                reg, expected_root=str(self.worktree), current_head=self.head_sha),
            marker)

    def test_valid_carried_marker_and_report_accepted(self):
        report = {
            "schema_version": 2,
            "result": "PASS",
            "status": "env_carried",
            "operations": "NONE",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "cache_fingerprint": self.fingerprint,
            "carried_from_head_sha": self.base_sha,
            "allowed_bootstrap_paths": ["tools/llmtool/pyproject.toml"],
            "venv_identity": self._build_valid_venv_identity(),
        }
        report_sha256 = self._write_report(report)
        marker = {
            "schema_version": 2,
            "result": "PASS",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "submitted_head_sha": self.head_sha,
            "current_head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "environment_fingerprint": self.fingerprint,
            "report_sha256": report_sha256,
            "environment_mode": "CARRIED",
            "carried_from_head_sha": self.base_sha,
            "allowed_bootstrap_paths": ["tools/llmtool/pyproject.toml"],
        }
        reg = self._create_registration(marker)
        write_paths = ["tools/llmtool/pyproject.toml", "src/foo.py"]
        validated = task_completion.validate_env2_pass_marker(
            reg, expected_root=str(self.worktree), current_head=self.head_sha,
            write_paths=write_paths)
        self.assertEqual(validated, marker)

    def _assert_validation_refusal(self, marker, code, *, write_paths=None,
                                   approved_tooling_sha=None, current_head=None):
        reg = self._create_registration(marker, approved_tooling_sha=approved_tooling_sha)
        head = current_head or self.head_sha
        with self.assertRaises(LLMToolError) as raised:
            task_completion.validate_env2_pass_marker(
                reg, expected_root=str(self.worktree), current_head=head,
                write_paths=write_paths)
        self.assertEqual(raised.exception.completion_code, code)

    def test_refusal_extra_or_missing_keys(self):
        # 10 keys: 9 schema-1 keys + 1 extra key -> INVALID
        marker_extra = {
            "schema_version": 1,
            "result": "PASS",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "submitted_head_sha": self.head_sha,
            "current_head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "environment_fingerprint": self.fingerprint,
            "report_sha256": "3" * 64,
            "extra_field": "bad",
        }
        self._assert_validation_refusal(marker_extra, "D6_ENV2_PASS_MARKER_INVALID")

        # 8 keys: schema-1 missing report_sha256 -> INVALID
        marker_missing = {
            "schema_version": 1,
            "result": "PASS",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "submitted_head_sha": self.head_sha,
            "current_head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "environment_fingerprint": self.fingerprint,
        }
        self._assert_validation_refusal(marker_missing, "D6_ENV2_PASS_MARKER_INVALID")

        # 11 keys: 12 schema-2 keys missing carried_from_head_sha -> INVALID
        marker_missing_v2 = {
            "schema_version": 2,
            "result": "PASS",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "submitted_head_sha": self.head_sha,
            "current_head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "environment_fingerprint": self.fingerprint,
            "report_sha256": "4" * 64,
            "environment_mode": "CARRIED",
            "allowed_bootstrap_paths": ["tools/llmtool/pyproject.toml"],
        }
        self._assert_validation_refusal(marker_missing_v2, "D6_ENV2_PASS_MARKER_INVALID")

    def test_refusal_version_shape_mismatch(self):
        # 9 keys with schema_version == 2 -> MISMATCH
        marker_9_v2 = {
            "schema_version": 2,
            "result": "PASS",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "submitted_head_sha": self.head_sha,
            "current_head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "environment_fingerprint": self.fingerprint,
            "report_sha256": "5" * 64,
        }
        self._assert_validation_refusal(marker_9_v2, "D6_ENV2_PASS_MARKER_MISMATCH")

        # 12 keys with schema_version == 1 -> MISMATCH
        marker_12_v1 = {
            "schema_version": 1,
            "result": "PASS",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "submitted_head_sha": self.head_sha,
            "current_head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "environment_fingerprint": self.fingerprint,
            "report_sha256": "5" * 64,
            "environment_mode": "CARRIED",
            "carried_from_head_sha": self.base_sha,
            "allowed_bootstrap_paths": ["tools/llmtool/pyproject.toml"],
        }
        self._assert_validation_refusal(marker_12_v1, "D6_ENV2_PASS_MARKER_MISMATCH")

    def test_refusal_carried_marker_with_write_paths_omitted_or_none(self):
        report = {
            "schema_version": 2,
            "result": "PASS",
            "status": "env_carried",
            "operations": "NONE",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "cache_fingerprint": self.fingerprint,
            "carried_from_head_sha": self.base_sha,
            "allowed_bootstrap_paths": ["tools/llmtool/pyproject.toml"],
            "venv_identity": self._build_valid_venv_identity(),
        }
        report_sha256 = self._write_report(report)
        marker = {
            "schema_version": 2,
            "result": "PASS",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "submitted_head_sha": self.head_sha,
            "current_head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "environment_fingerprint": self.fingerprint,
            "report_sha256": report_sha256,
            "environment_mode": "CARRIED",
            "carried_from_head_sha": self.base_sha,
            "allowed_bootstrap_paths": ["tools/llmtool/pyproject.toml"],
        }
        # write_paths omitted
        self._assert_validation_refusal(marker, "D6_ENV2_PASS_MARKER_MISMATCH")
        # write_paths is None explicitly
        self._assert_validation_refusal(marker, "D6_ENV2_PASS_MARKER_MISMATCH", write_paths=None)
        # write_paths is not a list (tuple)
        self._assert_validation_refusal(
            marker, "D6_ENV2_PASS_MARKER_MISMATCH",
            write_paths=("tools/llmtool/pyproject.toml",))

    def test_refusal_carried_report_with_schema_version_1(self):
        report = {
            "schema_version": 1,
            "result": "PASS",
            "status": "env_carried",
            "operations": "NONE",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "cache_fingerprint": self.fingerprint,
            "carried_from_head_sha": self.base_sha,
            "allowed_bootstrap_paths": ["tools/llmtool/pyproject.toml"],
            "venv_identity": self._build_valid_venv_identity(),
        }
        report_sha256 = self._write_report(report)
        marker = {
            "schema_version": 2,
            "result": "PASS",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "submitted_head_sha": self.head_sha,
            "current_head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "environment_fingerprint": self.fingerprint,
            "report_sha256": report_sha256,
            "environment_mode": "CARRIED",
            "carried_from_head_sha": self.base_sha,
            "allowed_bootstrap_paths": ["tools/llmtool/pyproject.toml"],
        }
        self._assert_validation_refusal(
            marker, "D6_ENV2_PASS_MARKER_MISMATCH",
            write_paths=["tools/llmtool/pyproject.toml"])

    def test_refusal_wrong_environment_mode(self):
        report = {
            "schema_version": 2,
            "result": "PASS",
            "status": "env_carried",
            "operations": "NONE",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "cache_fingerprint": self.fingerprint,
            "carried_from_head_sha": self.base_sha,
            "allowed_bootstrap_paths": ["tools/llmtool/pyproject.toml"],
            "venv_identity": self._build_valid_venv_identity(),
        }
        report_sha256 = self._write_report(report)
        marker = {
            "schema_version": 2,
            "result": "PASS",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "submitted_head_sha": self.head_sha,
            "current_head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "environment_fingerprint": self.fingerprint,
            "report_sha256": report_sha256,
            "environment_mode": "BUILD",
            "carried_from_head_sha": self.base_sha,
            "allowed_bootstrap_paths": ["tools/llmtool/pyproject.toml"],
        }
        self._assert_validation_refusal(
            marker, "D6_ENV2_PASS_MARKER_MISMATCH",
            write_paths=["tools/llmtool/pyproject.toml"])

    def test_refusal_carried_from_head_sha_equal_or_not_ancestor(self):
        report = {
            "schema_version": 2,
            "result": "PASS",
            "status": "env_carried",
            "operations": "NONE",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "cache_fingerprint": self.fingerprint,
            "carried_from_head_sha": self.head_sha,
            "allowed_bootstrap_paths": ["tools/llmtool/pyproject.toml"],
            "venv_identity": self._build_valid_venv_identity(),
        }
        # Case 1: carried_from_head_sha equal to head
        report_sha256 = self._write_report(report)
        marker = {
            "schema_version": 2,
            "result": "PASS",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "submitted_head_sha": self.head_sha,
            "current_head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "environment_fingerprint": self.fingerprint,
            "report_sha256": report_sha256,
            "environment_mode": "CARRIED",
            "carried_from_head_sha": self.head_sha,
            "allowed_bootstrap_paths": ["tools/llmtool/pyproject.toml"],
        }
        self._assert_validation_refusal(
            marker, "D6_ENV2_PASS_MARKER_MISMATCH",
            write_paths=["tools/llmtool/pyproject.toml"])

        # Case 2: carried_from_head_sha not an ancestor
        fake_sha = "6" * 40
        report["carried_from_head_sha"] = fake_sha
        marker["carried_from_head_sha"] = fake_sha
        marker["report_sha256"] = self._write_report(report)
        self._assert_validation_refusal(
            marker, "D6_ENV2_PASS_MARKER_MISMATCH",
            write_paths=["tools/llmtool/pyproject.toml"])

    def test_refusal_approved_tooling_present(self):
        report = {
            "schema_version": 2,
            "result": "PASS",
            "status": "env_carried",
            "operations": "NONE",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "cache_fingerprint": self.fingerprint,
            "carried_from_head_sha": self.base_sha,
            "allowed_bootstrap_paths": ["tools/llmtool/pyproject.toml"],
            "venv_identity": self._build_valid_venv_identity(),
        }
        report_sha256 = self._write_report(report)
        marker = {
            "schema_version": 2,
            "result": "PASS",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "submitted_head_sha": self.head_sha,
            "current_head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "environment_fingerprint": self.fingerprint,
            "report_sha256": report_sha256,
            "environment_mode": "CARRIED",
            "carried_from_head_sha": self.base_sha,
            "allowed_bootstrap_paths": ["tools/llmtool/pyproject.toml"],
        }
        self._assert_validation_refusal(
            marker, "D6_ENV2_PASS_MARKER_MISMATCH",
            write_paths=["tools/llmtool/pyproject.toml"],
            approved_tooling_sha=self.head_sha)

    def test_refusal_path_outside_bootstrap_sources_or_write_paths(self):
        # Outside BOOTSTRAP_SOURCES
        report = {
            "schema_version": 2,
            "result": "PASS",
            "status": "env_carried",
            "operations": "NONE",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "cache_fingerprint": self.fingerprint,
            "carried_from_head_sha": self.base_sha,
            "allowed_bootstrap_paths": ["outside/path.py"],
            "venv_identity": self._build_valid_venv_identity(),
        }
        report_sha256 = self._write_report(report)
        marker = {
            "schema_version": 2,
            "result": "PASS",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "submitted_head_sha": self.head_sha,
            "current_head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "environment_fingerprint": self.fingerprint,
            "report_sha256": report_sha256,
            "environment_mode": "CARRIED",
            "carried_from_head_sha": self.base_sha,
            "allowed_bootstrap_paths": ["outside/path.py"],
        }
        self._assert_validation_refusal(
            marker, "D6_ENV2_PASS_MARKER_MISMATCH",
            write_paths=["outside/path.py"])

        # In BOOTSTRAP_SOURCES, but outside write_paths
        report["allowed_bootstrap_paths"] = ["tools/llmtool/pyproject.toml"]
        marker["allowed_bootstrap_paths"] = ["tools/llmtool/pyproject.toml"]
        marker["report_sha256"] = self._write_report(report)
        self._assert_validation_refusal(
            marker, "D6_ENV2_PASS_MARKER_MISMATCH",
            write_paths=["src/other.py"])

    def test_refusal_unsorted_or_duplicate_paths(self):
        report = {
            "schema_version": 2,
            "result": "PASS",
            "status": "env_carried",
            "operations": "NONE",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "cache_fingerprint": self.fingerprint,
            "carried_from_head_sha": self.base_sha,
            "allowed_bootstrap_paths": ["tools/llmtool/pyproject.toml", "tools/llmtool/Makefile"],
            "venv_identity": self._build_valid_venv_identity(),
        }
        report_sha256 = self._write_report(report)
        # Unsorted
        marker_unsorted = {
            "schema_version": 2,
            "result": "PASS",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "submitted_head_sha": self.head_sha,
            "current_head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "environment_fingerprint": self.fingerprint,
            "report_sha256": report_sha256,
            "environment_mode": "CARRIED",
            "carried_from_head_sha": self.base_sha,
            "allowed_bootstrap_paths": ["tools/llmtool/pyproject.toml", "tools/llmtool/Makefile"],
        }
        self._assert_validation_refusal(
            marker_unsorted, "D6_ENV2_PASS_MARKER_MISMATCH",
            write_paths=["tools/llmtool/Makefile", "tools/llmtool/pyproject.toml"])

        # Duplicate
        report["allowed_bootstrap_paths"] = [
            "tools/llmtool/pyproject.toml",
            "tools/llmtool/pyproject.toml",
        ]
        marker_dup = dict(marker_unsorted,
                          allowed_bootstrap_paths=["tools/llmtool/pyproject.toml",
                                                   "tools/llmtool/pyproject.toml"],
                          report_sha256=self._write_report(report))
        self._assert_validation_refusal(
            marker_dup, "D6_ENV2_PASS_MARKER_MISMATCH",
            write_paths=["tools/llmtool/pyproject.toml"])

    def test_refusal_paths_differ_from_actual_diff(self):
        # Only pyproject.toml changed between base_sha and head_sha,
        # but marker claims Makefile also changed.
        report = {
            "schema_version": 2,
            "result": "PASS",
            "status": "env_carried",
            "operations": "NONE",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "cache_fingerprint": self.fingerprint,
            "carried_from_head_sha": self.base_sha,
            "allowed_bootstrap_paths": ["tools/llmtool/Makefile", "tools/llmtool/pyproject.toml"],
            "venv_identity": self._build_valid_venv_identity(),
        }
        report_sha256 = self._write_report(report)
        marker = {
            "schema_version": 2,
            "result": "PASS",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "submitted_head_sha": self.head_sha,
            "current_head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "environment_fingerprint": self.fingerprint,
            "report_sha256": report_sha256,
            "environment_mode": "CARRIED",
            "carried_from_head_sha": self.base_sha,
            "allowed_bootstrap_paths": ["tools/llmtool/Makefile", "tools/llmtool/pyproject.toml"],
        }
        self._assert_validation_refusal(
            marker, "D6_ENV2_PASS_MARKER_MISMATCH",
            write_paths=["tools/llmtool/Makefile", "tools/llmtool/pyproject.toml"])

    def test_refusal_missing_required_source(self):
        # Delete Makefile from current head in a new commit
        (self.worktree / "tools" / "llmtool" / "Makefile").unlink()
        subprocess.run(["/usr/bin/git", "add", "tools/llmtool/Makefile"],
                       cwd=self.worktree, check=True)
        subprocess.run(["/usr/bin/git", "commit", "-m", "delete required Makefile"],
                       cwd=self.worktree, check=True, capture_output=True)
        del_head = subprocess.check_output(
            ["/usr/bin/git", "rev-parse", "HEAD"], cwd=self.worktree, text=True).strip()

        # Both Makefile and pyproject.toml differ from tooling
        allowed = ["tools/llmtool/Makefile", "tools/llmtool/pyproject.toml"]
        report = {
            "schema_version": 2,
            "result": "PASS",
            "status": "env_carried",
            "operations": "NONE",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "head_sha": del_head,
            "tooling_sha": self.base_sha,
            "cache_fingerprint": self.fingerprint,
            "carried_from_head_sha": self.base_sha,
            "allowed_bootstrap_paths": allowed,
            "venv_identity": self._build_valid_venv_identity(),
        }
        report_sha256 = self._write_report(report)
        marker = {
            "schema_version": 2,
            "result": "PASS",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "submitted_head_sha": del_head,
            "current_head_sha": del_head,
            "tooling_sha": self.base_sha,
            "environment_fingerprint": self.fingerprint,
            "report_sha256": report_sha256,
            "environment_mode": "CARRIED",
            "carried_from_head_sha": self.base_sha,
            "allowed_bootstrap_paths": allowed,
        }
        self._assert_validation_refusal(
            marker, "D6_ENV2_PASS_MARKER_MISMATCH",
            write_paths=allowed, current_head=del_head)

    def test_refusal_report_wrong_status_or_operations_or_carried_fields(self):
        base_report = {
            "schema_version": 2,
            "result": "PASS",
            "status": "env_carried",
            "operations": "NONE",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "cache_fingerprint": self.fingerprint,
            "carried_from_head_sha": self.base_sha,
            "allowed_bootstrap_paths": ["tools/llmtool/pyproject.toml"],
            "venv_identity": self._build_valid_venv_identity(),
        }
        marker = {
            "schema_version": 2,
            "result": "PASS",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "submitted_head_sha": self.head_sha,
            "current_head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "environment_fingerprint": self.fingerprint,
            "report_sha256": "0" * 64,
            "environment_mode": "CARRIED",
            "carried_from_head_sha": self.base_sha,
            "allowed_bootstrap_paths": ["tools/llmtool/pyproject.toml"],
        }
        # Wrong status
        rep = copy.deepcopy(base_report)
        rep["status"] = "env_created"
        marker["report_sha256"] = self._write_report(rep)
        self._assert_validation_refusal(
            marker, "D6_ENV2_PASS_MARKER_MISMATCH",
            write_paths=["tools/llmtool/pyproject.toml"])

        # Wrong operations
        rep = copy.deepcopy(base_report)
        rep["operations"] = "BUILD"
        marker["report_sha256"] = self._write_report(rep)
        self._assert_validation_refusal(
            marker, "D6_ENV2_PASS_MARKER_MISMATCH",
            write_paths=["tools/llmtool/pyproject.toml"])

        # Mismatched carried_from_head_sha
        rep = copy.deepcopy(base_report)
        rep["carried_from_head_sha"] = "7" * 40
        marker["report_sha256"] = self._write_report(rep)
        self._assert_validation_refusal(
            marker, "D6_ENV2_PASS_MARKER_MISMATCH",
            write_paths=["tools/llmtool/pyproject.toml"])

        # Mismatched allowed_bootstrap_paths
        rep = copy.deepcopy(base_report)
        rep["allowed_bootstrap_paths"] = ["tools/llmtool/Makefile"]
        marker["report_sha256"] = self._write_report(rep)
        self._assert_validation_refusal(
            marker, "D6_ENV2_PASS_MARKER_MISMATCH",
            write_paths=["tools/llmtool/pyproject.toml"])

    def test_refusal_malformed_venv_identity(self):
        base_report = {
            "schema_version": 2,
            "result": "PASS",
            "status": "env_carried",
            "operations": "NONE",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "cache_fingerprint": self.fingerprint,
            "carried_from_head_sha": self.base_sha,
            "allowed_bootstrap_paths": ["tools/llmtool/pyproject.toml"],
            "venv_identity": self._build_valid_venv_identity(),
        }
        marker = {
            "schema_version": 2,
            "result": "PASS",
            "task_id": self.task_id,
            "root_realpath": str(self.worktree),
            "submitted_head_sha": self.head_sha,
            "current_head_sha": self.head_sha,
            "tooling_sha": self.base_sha,
            "environment_fingerprint": self.fingerprint,
            "report_sha256": "0" * 64,
            "environment_mode": "CARRIED",
            "carried_from_head_sha": self.base_sha,
            "allowed_bootstrap_paths": ["tools/llmtool/pyproject.toml"],
        }
        malformed_cases = [
            # Not a dict
            "UNKNOWN",
            None,
            # Missing key
            {k: v for k, v in self._build_valid_venv_identity().items() if k != "pyvenv_cfg_sha256"},
            # Extra key
            dict(self._build_valid_venv_identity(), extra=123),
            # Wrong schema_version
            dict(self._build_valid_venv_identity(), schema_version=2),
            # Wrong path
            dict(self._build_valid_venv_identity(), path="other/.venv"),
            # Wrong device type (bool instead of int)
            dict(self._build_valid_venv_identity(), device=True),
            # Invalid pyvenv_cfg_sha256
            dict(self._build_valid_venv_identity(), pyvenv_cfg_sha256="short"),
            # Empty interpreter_realpath
            dict(self._build_valid_venv_identity(), interpreter_realpath=""),
            # Malformed interpreter_stat (missing mtime_ns)
            dict(self._build_valid_venv_identity(),
                 interpreter_stat={"device": 1, "inode": 2, "size": 3}),
            # Malformed interpreter_stat value (string instead of int)
            dict(self._build_valid_venv_identity(),
                 interpreter_stat={"device": 1, "inode": 2, "size": "bad", "mtime_ns": 4}),
        ]
        for malformed in malformed_cases:
            with self.subTest(malformed=malformed):
                rep = copy.deepcopy(base_report)
                rep["venv_identity"] = malformed
                marker["report_sha256"] = self._write_report(rep)
                self._assert_validation_refusal(
                    marker, "D6_ENV2_PASS_MARKER_MISMATCH",
                    write_paths=["tools/llmtool/pyproject.toml"])


if __name__ == "__main__":
    unittest.main()
