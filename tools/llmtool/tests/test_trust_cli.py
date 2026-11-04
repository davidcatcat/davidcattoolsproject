"""CLI tests for owner trust configuration commands."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from llmtool import trust_cli
from llmtool import task_completion


def _ci_draft():
    return {"mode": "self-hosted", "github_actions_app_id": 15368,
            "workflows": [
                {"path": task_completion._CI_WORKFLOW_PATH,
                 "name": task_completion._CI_WORKFLOW_NAME,
                 "runner_names": sorted(task_completion._CI_RUNNER_NAMES),
                 "runner_labels": None, "code_check": task_completion._MAC_CHECK,
                 "docs_check": None},
                {"path": task_completion._LINUX_CI_WORKFLOW_PATH,
                 "name": task_completion._LINUX_CI_WORKFLOW_NAME,
                 "runner_names": sorted(task_completion._LINUX_CI_RUNNER_NAMES),
                 "runner_labels": None, "code_check": task_completion._LINUX_CHECK,
                 "docs_check": task_completion._DOCS_CHECK}],
            "not_run_checks": list(task_completion._NOT_RUN_CHECKS)}


class TrustCliTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp()).resolve()
        self.home.chmod(0o700)
        self.addCleanup(shutil.rmtree, self.home)

    def test_init_writes_private_file_atomically_and_refuses_overwrite(self):
        checkout = self.home / "checkout"
        checkout.mkdir(mode=0o700)
        workspace = self.home / "workspaces" / "project"
        values = {
            "canonical_root": str(checkout),
            "workspace_root": str(workspace),
            "tasks_root": str(workspace / "tasks"),
            "repository": {"slug": "davidcatcat/davidcattoolsproject",
                           "allowed_origins": [
                               "git@github.com:davidcatcat/davidcattoolsproject.git",
                               "https://github.com/davidcatcat/davidcattoolsproject.git"]},
        }
        draft = self.home / "draft.json"
        draft.write_text(json.dumps({"ci_trust": _ci_draft()}), encoding="utf-8")
        loaded = mock.Mock(sha256="a" * 64)
        target = self.home / ".config" / "llmtool" / "trust.json"
        with mock.patch.object(trust_cli, "_owner_home", return_value=self.home), \
             mock.patch.object(trust_cli, "_checkout_values", return_value=values), \
             mock.patch.object(trust_cli, "load_trust", return_value=loaded), \
             mock.patch.object(trust_cli, "verify_repository", return_value=True), \
             mock.patch("sys.stdout"):
            self.assertEqual(trust_cli._init(argparse.Namespace(input=str(draft), import_config=False)), 0)
            info = target.stat()
            self.assertEqual(info.st_mode & 0o777, 0o600)
            self.assertEqual(info.st_nlink, 1)
            self.assertEqual(target.parent.stat().st_mode & 0o777, 0o700)
            with self.assertRaises(trust_cli.TrustError) as caught:
                trust_cli._init(argparse.Namespace(input=str(draft), import_config=False))
            self.assertEqual(caught.exception.code, "TRUST_EXISTS")

    def test_init_rejects_missing_non_tty_input_and_conflicting_identity(self):
        with mock.patch.object(trust_cli, "_owner_home", return_value=self.home), \
             mock.patch.object(trust_cli, "_checkout_values", return_value={
                 "canonical_root": "/repo", "workspace_root": "/workspace",
                 "tasks_root": "/workspace/tasks", "repository": {"slug": "owner/repo",
                 "allowed_origins": ["https://github.com/owner/repo.git"]}}):
            with self.assertRaises(trust_cli.TrustError) as caught:
                trust_cli._init(argparse.Namespace(input=None, import_config=False))
            self.assertEqual(caught.exception.code, "TRUST_INIT_INPUT_INVALID")

    def test_import_config_overrides_dispatch_references(self):
        checkout = self.home / "checkout"
        checkout.mkdir(mode=0o700)
        workspace = self.home / "workspaces" / "project"
        values = {
            "canonical_root": str(checkout), "workspace_root": str(workspace),
            "tasks_root": str(workspace / "tasks"),
            "repository": {"slug": "davidcatcat/davidcattoolsproject",
                           "allowed_origins": [
                               "git@github.com:davidcatcat/davidcattoolsproject.git",
                               "https://github.com/davidcatcat/davidcattoolsproject.git"]},
        }
        draft = self.home / "draft.json"
        draft.write_text(json.dumps({"ci_trust": _ci_draft()}), encoding="utf-8")
        output = __import__("io").StringIO()
        with mock.patch.object(trust_cli, "_owner_home", return_value=self.home), \
             mock.patch.object(trust_cli, "_checkout_values", return_value=values), \
             mock.patch.object(trust_cli, "_import_config_refs", return_value=(410, 411)), \
             mock.patch.object(trust_cli, "load_trust", return_value=mock.Mock(sha256="d" * 64)), \
             mock.patch.object(trust_cli, "verify_repository", return_value=True), \
             mock.patch("sys.stdout", output):
            self.assertEqual(trust_cli._init(argparse.Namespace(input=str(draft), import_config=True)), 0)
        document = json.loads((self.home / ".config/llmtool/trust.json").read_text(encoding="utf-8"))
        refs = document["repository"]["approval_refs"]
        self.assertEqual(refs["worker_dispatch_issue"], 410)
        self.assertEqual(refs["worker_dispatch_evidence_pr"], 411)

    def test_replace_requires_matching_digest_and_reports_only_affected_count(self):
        config_dir = self.home / ".config" / "llmtool"
        config_dir.mkdir(mode=0o700, parents=True)
        target = config_dir / "trust.json"
        old_raw = b'{"schema_version":1}\n'
        target.write_bytes(old_raw)
        old_sha256 = hashlib.sha256(old_raw).hexdigest()
        checkout = self.home / "checkout"
        checkout.mkdir(mode=0o700)
        workspace = self.home / "workspaces" / "project"
        values = {
            "canonical_root": str(checkout), "workspace_root": str(workspace),
            "tasks_root": str(workspace / "tasks"),
            "repository": {"slug": "davidcatcat/davidcattoolsproject",
                           "allowed_origins": [
                               "git@github.com:davidcatcat/davidcattoolsproject.git",
                               "https://github.com/davidcatcat/davidcattoolsproject.git"]},
        }
        draft = self.home / "draft.json"
        draft.write_text(json.dumps({"ci_trust": _ci_draft()}), encoding="utf-8")
        old = mock.Mock(sha256=old_sha256)
        args = argparse.Namespace(input=str(draft), import_config=False,
                                  replace=True, confirm_rotation="b" * 64)
        with mock.patch.object(trust_cli, "_owner_home", return_value=self.home), \
             mock.patch.object(trust_cli, "load_trust", return_value=old), \
             mock.patch.object(trust_cli, "_checkout_values", return_value=values):
            with self.assertRaises(trust_cli.TrustError) as caught:
                trust_cli._init(args)
        self.assertEqual(caught.exception.code, "TRUST_ROTATION_UNCONFIRMED")

        args.confirm_rotation = old_sha256
        with mock.patch.object(trust_cli, "_owner_home", return_value=self.home), \
             mock.patch.object(trust_cli, "load_trust", side_effect=[
                 old, trust_cli.TrustError("TRUST_PATH_INVALID")]), \
             mock.patch.object(trust_cli, "_checkout_values", return_value=values), \
             mock.patch.object(trust_cli, "_affected_active_records", return_value=3):
            with self.assertRaises(trust_cli.TrustError):
                trust_cli._init(args)
        self.assertEqual(target.read_bytes(), old_raw)

        with mock.patch.object(trust_cli, "_owner_home", return_value=self.home), \
             mock.patch.object(trust_cli, "load_trust", side_effect=[old, mock.Mock()]), \
             mock.patch.object(trust_cli, "_checkout_values", return_value=values), \
             mock.patch.object(trust_cli, "_affected_active_records", return_value=3), \
             mock.patch.object(trust_cli, "verify_repository",
                               side_effect=trust_cli.TrustError("TRUST_REPOSITORY_INVALID")):
            with self.assertRaises(trust_cli.TrustError):
                trust_cli._init(args)
        self.assertEqual(target.read_bytes(), old_raw)

        output = __import__("io").StringIO()
        with mock.patch.object(trust_cli, "_owner_home", return_value=self.home), \
             mock.patch.object(trust_cli, "load_trust", return_value=old), \
             mock.patch.object(trust_cli, "_checkout_values", return_value=values), \
             mock.patch.object(trust_cli, "_affected_active_records", return_value=3), \
             mock.patch.object(trust_cli, "verify_repository", return_value=True), \
             mock.patch("sys.stdout", output):
            self.assertEqual(trust_cli._init(args), 0)
        self.assertIn("affected_active_records: 3", output.getvalue())
        self.assertNotIn(str(self.home), output.getvalue())

    def test_affected_active_record_count_ignores_non_active_and_matching_records(self):
        common = self.home / "checkout.git"
        registry_dir = common / "llmtool-worktrees"
        registry_dir.mkdir(mode=0o700, parents=True)
        records = {
            "active-old": {"state": "ACTIVE", "trust_sha256": "old",
                           "trust_binding_sha256": "old"},
            "active-match": {"state": "ACTIVE", "trust_sha256": "current",
                             "trust_binding_sha256": "new"},
            "active-no-stamp": {"state": "ACTIVE"},
            "removed": {"state": "REMOVED", "trust_binding_sha256": "old"},
        }
        for task_id, record in records.items():
            registry = registry_dir / (task_id + ".json")
            registry.write_text(json.dumps(record), encoding="utf-8")
            registry.chmod(0o600)
        with mock.patch.object(trust_cli, "_git", return_value=str(common)):
            self.assertEqual(trust_cli._affected_active_records(self.home, "new"), 2)
            (registry_dir / "active-old.json").chmod(0o644)
            with self.assertRaises(trust_cli.TrustError) as caught:
                trust_cli._affected_active_records(self.home, "new")
        self.assertEqual(caught.exception.code, "TRUST_REGISTRY_INVALID")

    def test_show_masks_all_paths_and_reports_sanitized_failure(self):
        config = mock.Mock(schema_version=1, sha256="b" * 64, binding_sha256="c" * 64,
                           canonical_root="/private/owner/repo", workspace_root="/private/workspaces",
                           tasks_root="/private/workspaces/tasks",
                           ci_trust={"mode": "self-hosted", "workflows": ({
                               "path": ".github/workflows/private.yml", "name": "CI",
                               "code_check": "code", "docs_check": "docs",
                               "runner_names": ("runner-secret",), "runner_labels": None},)})
        output = __import__("io").StringIO()
        with mock.patch.object(trust_cli, "load_trust", return_value=config), \
             mock.patch("sys.stdout", output):
            self.assertEqual(trust_cli._show(argparse.Namespace(reveal_paths=False)), 0)
        rendered = output.getvalue()
        self.assertNotIn("/private/owner/repo", rendered)
        self.assertNotIn("runner-secret", rendered)
        self.assertNotIn("private.yml", rendered)
        with mock.patch.object(trust_cli, "load_trust", side_effect=trust_cli.TrustError("TRUST_MISSING")), \
             mock.patch("sys.stdout", output):
            self.assertEqual(trust_cli._show(argparse.Namespace(reveal_paths=False)), 1)
        self.assertIn("STOP: trust rejected (TRUST_MISSING)", output.getvalue())
        self.assertNotIn("/private/owner/repo", output.getvalue())

        output = __import__("io").StringIO()
        with mock.patch.object(trust_cli, "load_trust", return_value=config), \
             mock.patch("sys.stdout", output):
            self.assertEqual(trust_cli._show(argparse.Namespace(reveal_paths=True)), 0)
        self.assertIn("/private/owner/repo", output.getvalue())
        self.assertIn(".github/workflows/private.yml", output.getvalue())

    def test_atomic_init_failure_leaves_no_partial_trust_file(self):
        target_dir = self.home / ".config" / "llmtool"
        target_dir.mkdir(mode=0o700, parents=True)
        target = target_dir / "trust.json"
        fdopen = trust_cli.os.fdopen

        class PartialWriter:
            def __init__(self, descriptor, mode, closefd=True):
                self.stream = fdopen(descriptor, mode, closefd=closefd)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def fileno(self):
                return self.stream.fileno()

            def write(self, data):
                self.stream.write(data[:3])
                self.stream.flush()
                raise OSError("simulated mid-write failure")

            def flush(self):
                self.stream.flush()

        with mock.patch.object(trust_cli.os, "fdopen", side_effect=PartialWriter):
            with self.assertRaises(trust_cli.TrustError) as caught:
                trust_cli._atomic_create(target, b'{"schema_version":1}\n')
        self.assertEqual(caught.exception.code, "TRUST_INIT_WRITE_FAILED")
        self.assertFalse(target.exists())
        self.assertEqual(list(target_dir.iterdir()), [])

    def test_checkout_values_derives_both_origins_from_https_remote(self):
        checkout = self.home / "checkout"
        (checkout / ".git").mkdir(parents=True)
        outputs = [str(checkout), str(checkout / ".git"), str(checkout / ".git"),
                   "https://github.com/example/project.git"]
        with mock.patch.object(trust_cli, "_git", side_effect=outputs):
            values = trust_cli._checkout_values(self.home)
        self.assertEqual(values["repository"]["allowed_origins"], [
            "git@github.com:example/project.git",
            "https://github.com/example/project.git"])

    def test_verify_legacy_uses_legacy_constants_and_effective_dispatch_settings(self):
        from llmtool import config

        trust = mock.Mock()
        trust.tasks_root = "/tasks"
        trust.canonical_root = "/repo"
        trust.workspace_root = "/workspaces"
        trust.repository = {
            "allowed_origins": [
                "git@github.com:davidcatcat/davidcattoolsproject.git",
                "https://github.com/davidcatcat/davidcattoolsproject.git"],
            "slug": "davidcatcat/davidcattoolsproject",
            "approval_refs": {"reviewer_host_policy_issue": 120,
                               "worker_dispatch_issue": 410,
                               "worker_dispatch_evidence_pr": 411,
                               "retain_release_issues": [289, 352]}}
        trust.ci_trust = {"workflows": [
            {"path": task_completion._CI_WORKFLOW_PATH,
             "name": task_completion._CI_WORKFLOW_NAME,
             "runner_names": sorted(task_completion._CI_RUNNER_NAMES),
             "runner_labels": None, "code_check": task_completion._MAC_CHECK,
             "docs_check": None},
            {"path": task_completion._LINUX_CI_WORKFLOW_PATH,
             "name": task_completion._LINUX_CI_WORKFLOW_NAME,
             "runner_names": sorted(task_completion._LINUX_CI_RUNNER_NAMES),
             "runner_labels": None, "code_check": task_completion._LINUX_CHECK,
             "docs_check": task_completion._DOCS_CHECK}],
            "not_run_checks": task_completion._NOT_RUN_CHECKS}
        output = __import__("io").StringIO()
        with mock.patch.object(trust_cli, "load_trust", return_value=trust), \
             mock.patch.object(trust_cli, "_owner_home", return_value=self.home), \
             mock.patch.object(config, "legacy_repository_config", return_value={
                 "slug": trust.repository["slug"], "dispatch_issue": 410,
                 "dispatch_evidence_pr": 411}), \
             mock.patch.object(task_completion, "_DOCS_CHECK", "changed legacy docs check"), \
             mock.patch("sys.stdout", output):
            self.assertEqual(trust_cli._verify_legacy(), 1)
        self.assertIn("worker_dispatch_issue: MATCH", output.getvalue())
        self.assertIn("worker_dispatch_evidence_pr: MATCH", output.getvalue())
        self.assertIn("workflows: MISMATCH", output.getvalue())
        self.assertNotIn("410", output.getvalue())

    def test_verify_legacy_prints_field_names_and_match_or_mismatch_only(self):
        keys = ("tasks_root", "canonical_root", "allowed_origins", "slug",
                "reviewer_host_policy_issue", "worker_dispatch_issue",
                "worker_dispatch_evidence_pr", "retain_release_issues", "workflows",
                "not_run_checks", "workspace_root")
        expected = {key: object() for key in keys}
        fields = dict(expected)
        output = __import__("io").StringIO()
        with mock.patch.object(trust_cli, "load_trust", return_value=object()), \
             mock.patch.object(trust_cli, "_legacy_fields", return_value=(fields, expected)), \
             mock.patch("sys.stdout", output):
            self.assertEqual(trust_cli._verify_legacy(), 0)
        self.assertEqual(output.getvalue().count(": MATCH"), len(keys))
        self.assertNotIn("object at", output.getvalue())
        fields["slug"] = "private/value"
        output = __import__("io").StringIO()
        with mock.patch.object(trust_cli, "load_trust", return_value=object()), \
             mock.patch.object(trust_cli, "_legacy_fields", return_value=(fields, expected)), \
             mock.patch("sys.stdout", output):
            self.assertEqual(trust_cli._verify_legacy(), 1)
        self.assertIn("slug: MISMATCH", output.getvalue())
        self.assertNotIn("private/value", output.getvalue())


if __name__ == "__main__":
    unittest.main()
