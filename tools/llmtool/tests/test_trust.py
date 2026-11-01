"""Tests for the isolated Owner trust.json loader."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from llmtool.trust import TrustError, load_trust, verify_repository


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
        if os.name != "posix" or not hasattr(os, "getuid") or os.geteuid() == 0:
            self.skipTest("trust loader requires an unprivileged POSIX identity")
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

    def _error(self, code):
        with self.assertRaises(TrustError) as caught:
            load_trust(home=str(self.home))
        self.assertEqual(caught.exception.code, code)

    def test_valid_file_loads_frozen_config(self):
        self._write()
        result = load_trust(home=str(self.home))
        self.assertEqual(result.canonical_root, str(self.canonical))
        self.assertEqual(result.repository["slug"], "example/project")
        self.assertEqual(result.ci_trust["workflows"][0]["runner_names"], ("runner-1",))
        self.assertEqual(len(result.sha256), 64)
        with self.assertRaises(TypeError):
            result.repository["slug"] = "changed/repo"

    def test_malformed_json_and_duplicate_keys_are_refused(self):
        for raw in (b"{", b'{"schema_version":1,"schema_version":1}',
                    b'{"schema_version":NaN}', b"\xef\xbb\xbf{}", b"\xff"):
            with self.subTest(raw=raw):
                self._write(raw=raw)
                self._error("TRUST_MALFORMED")

    def test_schema_fields_types_and_paths_are_strict(self):
        mutations = [
            (lambda d: d.pop("tools")),
            (lambda d: d.update(extra=True)),
            (lambda d: d.update(schema_version=True)),
            (lambda d: d.update(schema_version=2)),
            (lambda d: d.update(canonical_root=3)),
            (lambda d: d.update(canonical_root="relative/path")),
            (lambda d: d.update(canonical_root=str(self.canonical.parent / "canonical" / ".."))),
            (lambda d: d["repository"].update(extra="x")),
            (lambda d: d["repository"].update(slug="bad slug")),
            (lambda d: d["repository"].update(allowed_origins=["https://github.com/else/repo.git"])),
            (lambda d: d["repository"]["approval_refs"].update(worker_dispatch_issue=True)),
            (lambda d: d["ci_trust"].update(github_actions_app_id=True)),
            (lambda d: d["ci_trust"]["workflows"][0].update(runner_names="runner-1")),
            (lambda d: d["ci_trust"]["workflows"][0].update(path="workflow.yml")),
            (lambda d: d["tools"].update(git="relative/git")),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                value = json.loads(json.dumps(self.data))
                mutate(value)
                self._write(value)
                with self.assertRaises(TrustError):
                    load_trust(home=str(self.home))

    def test_permissions_symlink_and_size_are_refused(self):
        path = self._write()
        path.chmod(0o644)
        self._error("TRUST_FILE_NOT_PRIVATE")
        path.unlink()
        path.symlink_to(self.canonical / "absent")
        self._error("TRUST_FILE_NOT_PRIVATE")
        path.unlink()
        self._write(raw=b"x" * (16 * 1024 + 1))
        self._error("TRUST_TOO_LARGE")
        self._write(raw=b"")
        self._error("TRUST_TOO_LARGE")

    def test_private_directory_mode_is_required(self):
        self._write()
        self.trust_dir.chmod(0o750)
        self._error("TRUST_DIRECTORY_NOT_PRIVATE")

    def test_trust_path_ancestors_do_not_follow_symlinks(self):
        self._write()
        self.config.rename(self.home / "config-real")
        self.config.symlink_to(self.home / "config-real", target_is_directory=True)
        self._error("TRUST_ANCESTOR_INVALID")

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
        trust = load_trust(home=str(self.home))
        self.assertTrue(verify_repository(trust))
        subprocess.run(["git", "-C", str(repo), "remote", "set-url", "origin",
                        "https://github.com/else/project.git"], check=True)
        with self.assertRaises(TrustError) as caught:
            verify_repository(trust)
        self.assertEqual(caught.exception.code, "TRUST_REPOSITORY_INVALID")


if __name__ == "__main__":
    unittest.main()
