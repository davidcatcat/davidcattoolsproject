"""CLI tests for owner trust configuration commands."""

import argparse
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from llmtool import trust_cli


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
        draft.write_text(json.dumps({"ci_trust": {
            "mode": "self-hosted", "github_actions_app_id": 15368,
            "workflows": list(trust_cli._WORKFLOWS),
            "not_run_checks": trust_cli._NOT_RUN_CHECKS}}), encoding="utf-8")
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
                 "allowed_origins": ["https://github.com/owner/repo.git"]}}), \
             mock.patch("sys.stdin.isatty", return_value=False):
            with self.assertRaises(trust_cli.TrustError) as caught:
                trust_cli._init(argparse.Namespace(input=None, import_config=False))
            self.assertEqual(caught.exception.code, "TRUST_INIT_INPUT_INVALID")

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
