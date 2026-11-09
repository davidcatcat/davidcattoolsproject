"""Tests for the isolated Owner trust.json loader."""

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from llmtool import trust
from llmtool.trust import TrustError, _check_directory, load_trust, verify_repository


def _document(canonical, workspace, tasks):
    return {
        "schema_version": 1,
        "canonical_root": str(canonical),
        "workspace_root": str(workspace),
        "tasks_root": str(tasks),
        "repository": {
            "slug": "example/project",
            "allowed_origins": ["git@github.com:example/project.git",
                                 "https://github.com/example/project.git"],
            "approval_refs": {
                "reviewer_host_policy_issue": 120,
                "worker_dispatch_issue": 289,
                "worker_dispatch_evidence_pr": 283,
                "retain_release_issues": [289, 352],
            },
        },
        "ci_trust": {
            "mode": "self-hosted",
            "github_actions_app_id": 15368,
            "workflows": [
                {"path": ".github/workflows/code.yml", "name": "Code CI",
                 "runner_names": ["runner-1"], "runner_labels": None,
                 "code_check": "check (code)", "docs_check": None},
                {"path": ".github/workflows/docs.yaml", "name": "Docs CI",
                 "runner_names": ["runner-1"], "runner_labels": None,
                 "code_check": None, "docs_check": "check (docs)"},
            ],
            "not_run_checks": ["Windows CI"],
        },
        "tools": {"git": None, "gh_path": None, "lsof": None},
    }


class TrustTest(unittest.TestCase):
    def setUp(self):
        if os.name != "posix" or not hasattr(os, "getuid"):
            self.skipTest("trust loader requires a POSIX identity")
        self.allow_root = os.geteuid() == 0
        trust._TRUST_CACHE = None
        self.addCleanup(setattr, trust, "_TRUST_CACHE", None)
        base = Path(tempfile.mkdtemp()).resolve()
        base.chmod(0o700)
        self.addCleanup(shutil.rmtree, base)
        self.home = base / "home"
        self.home.mkdir(mode=0o700)
        self.home.chmod(0o700)
        self.config = self.home / ".config"
        self.config.mkdir(mode=0o700)
        self.trust_dir = self.config / "llmtool"
        self.trust_dir.mkdir(mode=0o700)
        self.canonical = base / "canonical"
        self.canonical.mkdir(mode=0o700)
        self.canonical.chmod(0o700)
        self.workspace = base / "workspace"
        self.workspace.mkdir(mode=0o700)
        self.workspace.chmod(0o700)
        self.tasks = self.workspace / "tasks"
        self.tasks.mkdir(mode=0o700)
        self.tasks.chmod(0o700)
        self.data = _document(self.canonical, self.workspace, self.tasks)

    def _write(self, value=None, *, raw=None, mode=0o600):
        path = self.trust_dir / "trust.json"
        if raw is None:
            raw = json.dumps(self.data if value is None else value).encode("utf-8")
        path.write_bytes(raw)
        path.chmod(mode)
        return path

    def _error(self, code, *, home=None):
        with self.assertRaises(TrustError) as caught:
            load_trust(home=str(self.home) if home is None else home,
                       _allow_root=self.allow_root)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    def test_valid_file_loads_frozen_config(self):
        self._write()
        result = load_trust(home=str(self.home), _allow_root=self.allow_root)
        self.assertEqual(result.canonical_root, str(self.canonical))
        self.assertEqual(result.repository["slug"], "example/project")
        self.assertEqual(result.ci_trust["workflows"][0]["runner_names"], ("runner-1",))
        self.assertEqual(len(result.sha256), 64)
        self.assertEqual(len(result.binding_sha256), 64)
        with self.assertRaises(TypeError):
            result.repository["slug"] = "changed/repo"

    def test_default_load_is_frozen_once_per_process(self):
        self._write()
        trust._TRUST_CACHE = None
        with mock.patch("llmtool.trust.pwd.getpwuid", return_value=mock.Mock(pw_dir=str(self.home))), \
                mock.patch("llmtool.trust.os.open", wraps=os.open) as open_call:
            first = load_trust(_allow_root=self.allow_root)
            opens_after_first_load = open_call.call_count
            second = load_trust(_allow_root=self.allow_root)
        self.assertIs(first, second)
        self.assertEqual(open_call.call_count, opens_after_first_load)

    def test_binding_digest_is_stable_and_ignores_non_binding_fields(self):
        self._write()
        first = load_trust(home=str(self.home), _allow_root=self.allow_root)
        changed = json.loads(json.dumps(self.data))
        changed["ci_trust"]["not_run_checks"].append("Other CI")
        self._write(changed)
        second = load_trust(home=str(self.home), _allow_root=self.allow_root)
        self.assertNotEqual(first.sha256, second.sha256)
        self.assertEqual(first.binding_sha256, second.binding_sha256)

    def test_malformed_json_and_duplicate_keys_are_refused(self):
        for raw in (b"{", b'{"schema_version":1,"schema_version":1}',
                    b'{"schema_version":NaN}', b'{"schema_version":Infinity}',
                    b'{"outer":{"value":1,"value":2}}', b"\xef\xbb\xbf{}", b"\xff",
                    b"[" + b"[" * 1100 + b"0" + b"]" * 1100 + b"]"):
            with self.subTest(raw=raw):
                self._write(raw=raw)
                self._error("TRUST_MALFORMED")
        self._write(raw=b"[]")
        self._error("TRUST_SCHEMA_INVALID")

    def test_schema_fields_types_and_paths_are_strict(self):
        mutations = [
            (lambda d: d.pop("tools"), "TRUST_SCHEMA_INVALID"),
            (lambda d: d.update(extra=True), "TRUST_SCHEMA_INVALID"),
            (lambda d: d.update(schema_version=True), "TRUST_SCHEMA_VERSION_UNSUPPORTED"),
            (lambda d: d.update(schema_version=2), "TRUST_SCHEMA_VERSION_UNSUPPORTED"),
            (lambda d: d.update(canonical_root=3), "TRUST_SCHEMA_INVALID"),
            (lambda d: d.update(canonical_root="relative/path"), "TRUST_SCHEMA_INVALID"),
            (lambda d: d.update(canonical_root=str(self.canonical.parent / "canonical" / "..")), "TRUST_SCHEMA_INVALID"),
            (lambda d: d["repository"].update(extra="x"), "TRUST_SCHEMA_INVALID"),
            (lambda d: d["repository"].update(slug="bad slug"), "TRUST_SCHEMA_INVALID"),
            (lambda d: d["repository"].update(allowed_origins=["https://github.com/else/repo.git"]), "TRUST_SCHEMA_INVALID"),
            (lambda d: d["repository"].update(allowed_origins=["https://github.com/example/project.git"] * 2), "TRUST_SCHEMA_INVALID"),
            (lambda d: d["repository"].update(allowed_origins=["https://github.com/example/project.git", "git@github.com:example/project.git", "https://github.com/example/project.git"]), "TRUST_SCHEMA_INVALID"),
            (lambda d: d["repository"]["approval_refs"].update(worker_dispatch_issue=True), "TRUST_SCHEMA_INVALID"),
            (lambda d: d["repository"]["approval_refs"].update(reviewer_host_policy_issue=0), "TRUST_SCHEMA_INVALID"),
            (lambda d: d["repository"]["approval_refs"].update(worker_dispatch_issue=10**9 + 1), "TRUST_SCHEMA_INVALID"),
            (lambda d: d["repository"]["approval_refs"].update(retain_release_issues=[]), "TRUST_SCHEMA_INVALID"),
            (lambda d: d["repository"]["approval_refs"].update(retain_release_issues=[289, 289]), "TRUST_SCHEMA_INVALID"),
            (lambda d: d["ci_trust"].update(github_actions_app_id=True), "TRUST_SCHEMA_INVALID"),
            (lambda d: d["ci_trust"].update(mode="unknown"), "TRUST_CI_INVALID"),
            (lambda d: d["ci_trust"]["workflows"][0].update(runner_names="runner-1"), "TRUST_CI_INVALID"),
            (lambda d: d["ci_trust"]["workflows"][0].update(runner_names=None), "TRUST_CI_INVALID"),
            (lambda d: d["ci_trust"]["workflows"][0].update(runner_names=["runner?"]), "TRUST_CI_INVALID"),
            (lambda d: d["ci_trust"]["workflows"][0].update(path="workflow.yml"), "TRUST_SCHEMA_INVALID"),
            (lambda d: d["ci_trust"]["workflows"][0].update(path=".github/workflows/../x.yml"), "TRUST_SCHEMA_INVALID"),
            (lambda d: d["ci_trust"]["workflows"][1].update(code_check="check (code)"), "TRUST_CI_INVALID"),
            (lambda d: d["tools"].update(git="relative/git"), "TRUST_TOOL_INVALID"),
        ]
        for mutate, code in mutations:
            with self.subTest(mutate=mutate):
                value = json.loads(json.dumps(self.data))
                mutate(value)
                self._write(value)
                self._error(code)

    def test_permissions_symlink_and_size_are_refused(self):
        path = self._write()
        path.chmod(0o644)
        self._error("TRUST_FILE_NOT_PRIVATE")
        path.unlink()
        path.symlink_to(self.canonical / "absent")
        self._error("TRUST_FILE_NOT_PRIVATE")
        path.unlink()
        path.mkdir()
        self._error("TRUST_FILE_NOT_PRIVATE")
        path.rmdir()
        self._write(raw=b"x" * (16 * 1024 + 1))
        self._error("TRUST_TOO_LARGE")
        self._write(raw=b"")
        self._error("TRUST_TOO_LARGE")

    def test_private_directory_mode_is_required(self):
        self._write()
        for mode in (0o750, 0o755):
            with self.subTest(mode=oct(mode)):
                self.trust_dir.chmod(mode)
                self._error("TRUST_DIRECTORY_NOT_PRIVATE")

    def test_trust_path_ancestors_do_not_follow_symlinks(self):
        self._write()
        self.config.rename(self.home / "config-real")
        self.config.symlink_to(self.home / "config-real", target_is_directory=True)
        self._error("TRUST_ANCESTOR_INVALID")

    def test_root_owned_sticky_world_writable_ancestor_is_allowed(self):
        info = os.stat_result((stat.S_IFDIR | 0o1777, 0, 0, 1, 0, 0, 0, 0, 0, 0))
        with mock.patch("llmtool.trust.os.fstat", return_value=info):
            result = _check_directory(123, os.geteuid(), "TRUST_ANCESTOR_INVALID")
        self.assertEqual(result.st_uid, 0)

    def test_non_sticky_world_writable_ancestor_is_rejected(self):
        info = os.stat_result((stat.S_IFDIR | 0o777, 0, 0, 1, 0, 0, 0, 0, 0, 0))
        with mock.patch("llmtool.trust.os.fstat", return_value=info):
            with self.assertRaises(TrustError) as caught:
                _check_directory(123, os.geteuid(), "TRUST_ANCESTOR_INVALID")
        self.assertEqual(caught.exception.code, "TRUST_ANCESTOR_INVALID")

    def test_trust_directory_owner_must_match(self):
        info = os.stat_result((stat.S_IFDIR | 0o700, 0, 0, 1, os.geteuid() + 1,
                               os.getegid() if hasattr(os, "getegid") else 0,
                               0, 0, 0, 0))
        with mock.patch("llmtool.trust.os.fstat", return_value=info):
            with self.assertRaises(TrustError) as caught:
                _check_directory(123, os.geteuid(), "TRUST_ANCESTOR_INVALID", private=True)
        self.assertEqual(caught.exception.code, "TRUST_DIRECTORY_NOT_PRIVATE")

    def test_fifo_is_not_opened_as_a_trust_file(self):
        path = self.trust_dir / "trust.json"
        os.mkfifo(path, 0o600)
        self._error("TRUST_FILE_NOT_PRIVATE")

    def test_repository_verification_accepts_and_rejects_expected_repo(self):
        repo = Path(self.canonical)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                        "https://github.com/example/project.git"], check=True)
        self._write()
        trusted = load_trust(home=str(self.home), _allow_root=self.allow_root)
        self.assertTrue(verify_repository(trusted))
        subprocess.run(["git", "-C", str(repo), "remote", "set-url", "origin",
                        "https://github.com/else/project.git"], check=True)
        with self.assertRaises(TrustError) as caught:
            verify_repository(trusted)
        self.assertEqual(caught.exception.code, "TRUST_REPOSITORY_INVALID")

    def test_missing_file_and_trust_directory_have_exact_code(self):
        self._error("TRUST_MISSING")
        self.trust_dir.rmdir()
        self._error("TRUST_MISSING")

    def test_owner_file_modes_and_hard_links_are_rejected(self):
        path = self._write()
        for mode in (0o400, 0o700, 0o644):
            with self.subTest(mode=oct(mode)):
                path.chmod(mode)
                self._error("TRUST_FILE_NOT_PRIVATE")
        path.chmod(0o600)
        os.link(path, self.trust_dir / "second-link")
        self._error("TRUST_FILE_NOT_PRIVATE")

    def test_exact_maximum_size_is_accepted_and_oversize_rejected(self):
        raw = json.dumps(self.data, separators=(",", ":")).encode("utf-8")
        raw += b" " * (16 * 1024 - len(raw))
        self.assertEqual(len(raw), 16 * 1024)
        self._write(raw=raw)
        self.assertEqual(load_trust(home=str(self.home), _allow_root=self.allow_root).canonical_root, str(self.canonical))
        self._write(raw=raw + b" ")
        self._error("TRUST_TOO_LARGE")

    def test_trust_ancestors_with_git_directory_or_file_are_rejected(self):
        self._write()
        git_dir = self.home / ".git"
        git_dir.mkdir()
        self._error("TRUST_INSIDE_WORKTREE")
        git_dir.rmdir()
        git_file = self.home / ".git"
        git_file.write_text("gitdir: /tmp/linked-worktree", encoding="utf-8")
        self._error("TRUST_INSIDE_WORKTREE")

    def test_read_race_has_exact_code(self):
        path = self._write()
        original_read = os.read
        changed = False

        def read_then_replace(fd, size):
            nonlocal changed
            result = original_read(fd, size)
            if not changed:
                changed = True
                path.write_bytes(result + b" ")
            return result

        with mock.patch("llmtool.trust.os.read", side_effect=read_then_replace):
            self._error("TRUST_CHANGED_DURING_READ")

    def test_home_identity_and_platform_refusals_have_exact_codes(self):
        symlink_home = self.home.parent / "home-link"
        symlink_home.symlink_to(self.home, target_is_directory=True)
        self._error("TRUST_HOME_INVALID", home=str(symlink_home))
        with mock.patch("llmtool.trust.pwd.getpwuid", side_effect=KeyError):
            with self.assertRaises(TrustError) as caught:
                load_trust(_allow_root=self.allow_root)
        self.assertEqual(caught.exception.code, "TRUST_HOME_INVALID")
        with mock.patch("llmtool.trust.os.getuid", return_value=os.geteuid() + 1):
            self._error("TRUST_IDENTITY_INVALID")
        with mock.patch("llmtool.trust.os.getuid", return_value=0), \
                mock.patch("llmtool.trust.os.geteuid", return_value=0):
            with self.assertRaises(TrustError) as caught:
                load_trust(home=str(self.home))
            self.assertEqual(caught.exception.code, "TRUST_IDENTITY_INVALID")
        with mock.patch("llmtool.trust.sys.platform", "win32"):
            self._error("TRUST_UNSUPPORTED_PLATFORM")
        with mock.patch("llmtool.trust.os.name", "nt"):
            self._error("TRUST_UNSUPPORTED_PLATFORM")

    def test_schema_matrix_exact_codes(self):
        cases = [
            (lambda d: d["repository"].update(slug="a/.."), "TRUST_SCHEMA_INVALID"),
            (lambda d: d["repository"].update(allowed_origins=["ssh://git@github.com/example/project.git"]), "TRUST_SCHEMA_INVALID"),
            (lambda d: d["ci_trust"].update(github_actions_app_id=15367), "TRUST_SCHEMA_INVALID"),
            (lambda d: d["ci_trust"]["workflows"][0].update(runner_names=["*"]), "TRUST_CI_INVALID"),
            (lambda d: (d["ci_trust"].update(mode="github-hosted"),
                        [workflow.update(runner_names=None) for workflow in d["ci_trust"]["workflows"]],
                        d["ci_trust"]["workflows"][0].update(runner_labels=["self-hosted"])), "TRUST_CI_INVALID"),
            (lambda d: d["ci_trust"]["workflows"][1].update(docs_check=None), "TRUST_CI_INVALID"),
        ]
        for mutate, code in cases:
            with self.subTest(code=code, mutate=mutate):
                value = json.loads(json.dumps(self.data))
                mutate(value)
                self._write(value)
                self._error(code)

    def test_github_hosted_configuration_loads(self):
        value = json.loads(json.dumps(self.data))
        value["ci_trust"]["mode"] = "github-hosted"
        for workflow in value["ci_trust"]["workflows"]:
            workflow["runner_names"] = None
            workflow["runner_labels"] = ["ubuntu-latest"]
        self._write(value)
        self.assertEqual(load_trust(home=str(self.home), _allow_root=self.allow_root).ci_trust["mode"], "github-hosted")

    def test_github_hosted_runner_names_must_be_null(self):
        value = json.loads(json.dumps(self.data))
        value["ci_trust"]["mode"] = "github-hosted"
        for workflow in value["ci_trust"]["workflows"]:
            workflow["runner_labels"] = ["ubuntu-latest"]
        value["ci_trust"]["workflows"][0]["runner_names"] = ["runner-1"]
        self._write(value)
        self._error("TRUST_CI_INVALID")

    def test_github_hosted_runner_label_alternatives_load_and_freeze(self):
        value = json.loads(json.dumps(self.data))
        value["ci_trust"]["mode"] = "github-hosted"
        for workflow in value["ci_trust"]["workflows"]:
            workflow["runner_names"] = None
            workflow["runner_labels"] = [["macos-15"], ["macos-15-intel"]]
        self._write(value)
        trusted = load_trust(home=str(self.home), _allow_root=self.allow_root)
        self.assertEqual(
            trusted.ci_trust["workflows"][0]["runner_labels"],
            (("macos-15",), ("macos-15-intel",)),
        )

    def test_github_hosted_repeated_runner_label_alternatives_load(self):
        value = json.loads(json.dumps(self.data))
        value["ci_trust"]["mode"] = "github-hosted"
        for workflow in value["ci_trust"]["workflows"]:
            workflow["runner_names"] = None
            workflow["runner_labels"] = [["macos-15"], ["macos-15"]]
        self._write(value)
        trusted = load_trust(home=str(self.home), _allow_root=self.allow_root)
        self.assertEqual(
            trusted.ci_trust["workflows"][0]["runner_labels"],
            (("macos-15",), ("macos-15",)),
        )

    def test_github_hosted_invalid_runner_label_forms_refused(self):
        invalid_labels = [
            None,
            [],
            [[]],
            [["macos-15"], []],
            ["macos-15", ["macos-15-intel"]],
            [["macos-15"], "macos-15-intel"],
            [[["macos-15"]]],
            [["macos-15"], [["macos-15-intel"]]],
            [["macos-15"], ["macos-14"], ["macos-13"], ["macos-12"], ["macos-11"]],
            [["macos-15", "macos-14", "macos-13", "macos-12", "macos-11", "macos-10", "macos-9", "macos-8", "macos-7"]],
            [["macos-15", 123]],
            [123],
            [["self-hosted"]],
            [["macos-15"], ["self-hosted"]],
            [["macos-15", "macos-15"]],
        ]
        for labels in invalid_labels:
            with self.subTest(labels=labels):
                value = json.loads(json.dumps(self.data))
                value["ci_trust"]["mode"] = "github-hosted"
                for workflow in value["ci_trust"]["workflows"]:
                    workflow["runner_names"] = None
                    workflow["runner_labels"] = [["macos-15"]]
                value["ci_trust"]["workflows"][0]["runner_labels"] = labels
                self._write(value)
                self._error("TRUST_CI_INVALID")

    def test_self_hosted_nested_runner_labels_refused(self):
        value = json.loads(json.dumps(self.data))
        value["ci_trust"]["workflows"][0]["runner_labels"] = [["macos-15"]]
        self._write(value)
        self._error("TRUST_CI_INVALID")

    def test_path_invalid_and_overlap_have_exact_codes(self):
        value = json.loads(json.dumps(self.data))
        value["canonical_root"] = str(self.canonical / "missing")
        self._write(value)
        self._error("TRUST_PATH_INVALID")
        value["canonical_root"] = str(self.workspace)
        self._write(value)
        self._error("TRUST_PATHS_OVERLAP")
        value["canonical_root"] = str(self.workspace / "tasks")
        self._write(value)
        self._error("TRUST_PATHS_OVERLAP")
        value["canonical_root"] = str(self.canonical)
        value["workspace_root"] = str(self.canonical.parent)
        self._write(value)
        self._error("TRUST_PATHS_OVERLAP")
        value["workspace_root"] = str(self.workspace)
        value["tasks_root"] = str(self.canonical)
        self._write(value)
        self._error("TRUST_PATHS_OVERLAP")
        value["tasks_root"] = str(self.home)
        self._write(value)
        self._error("TRUST_PATHS_OVERLAP")

    def test_tool_paths_must_be_private_physical_executables(self):
        tool = self.home / "tool"
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        tool.chmod(0o755)
        value = json.loads(json.dumps(self.data))
        value["tools"]["git"] = str(tool)
        tool.chmod(0o775)
        self._write(value)
        self._error("TRUST_TOOL_INVALID")
        tool.chmod(0o755)
        link = self.home / "tool-link"
        link.symlink_to(tool)
        value["tools"]["git"] = str(link)
        self._write(value)
        self._error("TRUST_TOOL_INVALID")

    def test_repository_verification_rejects_linked_worktree_and_toplevel_mismatch(self):
        repo = Path(self.canonical)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c",
                        "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "initial"], check=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                        "https://github.com/example/project.git"], check=True)
        linked = self.canonical.parent / "linked"
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", "-q",
                        str(linked)], check=True)
        self._write()
        trusted = load_trust(home=str(self.home), _allow_root=self.allow_root)
        linked_trust = trusted.__class__(trusted.schema_version, str(linked), trusted.workspace_root,
                                         trusted.tasks_root, trusted.repository, trusted.ci_trust,
                                         trusted.tools, trusted.sha256, trusted.binding_sha256)
        with self.assertRaises(TrustError) as caught:
            verify_repository(linked_trust)
        self.assertEqual(caught.exception.code, "TRUST_REPOSITORY_INVALID")
        nested = self.canonical / "nested"
        nested.mkdir()
        nested_trust = trusted.__class__(trusted.schema_version, str(nested), trusted.workspace_root,
                                         trusted.tasks_root, trusted.repository, trusted.ci_trust,
                                         trusted.tools, trusted.sha256, trusted.binding_sha256)
        with self.assertRaises(TrustError) as caught:
            verify_repository(nested_trust)
        self.assertEqual(caught.exception.code, "TRUST_REPOSITORY_INVALID")


if __name__ == "__main__":
    unittest.main()
