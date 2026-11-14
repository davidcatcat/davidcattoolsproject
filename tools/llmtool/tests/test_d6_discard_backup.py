"""Tests for D6 discard backup verification and approval parsing."""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import d6_worktree_lifecycle as lifecycle  # noqa: E402


def _git(cwd, *args):
    return subprocess.run(
        ["/usr/bin/git", "-c", "user.name=D6 test",
         "-c", "user.email=d6@example.invalid", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


class D6DiscardBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.top = Path(self.temp.name).resolve()
        self.repo = self.top / "repo"
        self.repo.mkdir(mode=0o700)
        _git(self.repo, "init", "-b", "main")
        (self.repo / "base.txt").write_text("base\n", encoding="utf-8")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-m", "base")
        self.main_head = _git(self.repo, "rev-parse", "HEAD")

        _git(self.repo, "checkout", "-b", "task/x")
        (self.repo / "task.txt").write_text("task\n", encoding="utf-8")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-m", "task commit")
        self.head = _git(self.repo, "rev-parse", "HEAD")

        self.backups = self.top / "backups"
        self.backups.mkdir(mode=0o700)
        self.bundle = self.backups / "x.bundle"
        _git(self.repo, "bundle", "create", str(self.bundle), "task/x")
        self.bundle.chmod(0o600)
        self.digest = hashlib.sha256(self.bundle.read_bytes()).hexdigest()

    def test_verify_discard_backup_success(self):
        self.assertIsNone(
            lifecycle._verify_discard_backup(
                self.repo, self.head, str(self.bundle), self.digest,
                backup_root=self.backups,
            )
        )

    def test_verify_discard_backup_digest_head_and_unverified(self):
        wrong_digest = "0" * 64 if self.digest != "0" * 64 else "1" * 64
        self.assertEqual(
            lifecycle._verify_discard_backup(
                self.repo, self.head, str(self.bundle), wrong_digest,
                backup_root=self.backups,
            ),
            "D6_DISCARD_BACKUP_DIGEST_MISMATCH",
        )
        self.assertEqual(
            lifecycle._verify_discard_backup(
                self.repo, self.main_head, str(self.bundle), self.digest,
                backup_root=self.backups,
            ),
            "D6_DISCARD_BACKUP_HEAD_MISSING",
        )
        not_bundle = self.backups / "not_bundle.bundle"
        not_bundle.write_bytes(b"not a bundle\n")
        not_bundle.chmod(0o600)
        not_bundle_digest = hashlib.sha256(b"not a bundle\n").hexdigest()
        self.assertEqual(
            lifecycle._verify_discard_backup(
                self.repo, self.head, str(not_bundle), not_bundle_digest,
                backup_root=self.backups,
            ),
            "D6_DISCARD_BACKUP_UNVERIFIED",
        )

    def test_verify_discard_backup_mode_missing_and_invalid_paths(self):
        self.bundle.chmod(0o622)
        try:
            self.assertEqual(
                lifecycle._verify_discard_backup(
                    self.repo, self.head, str(self.bundle), self.digest,
                    backup_root=self.backups,
                ),
                "D6_DISCARD_BACKUP_UNSAFE",
            )
        finally:
            self.bundle.chmod(0o600)

        missing_path = self.backups / "missing.bundle"
        self.assertEqual(
            lifecycle._verify_discard_backup(
                self.repo, self.head, str(missing_path), self.digest,
                backup_root=self.backups,
            ),
            "D6_DISCARD_BACKUP_MISSING",
        )

        symlink_path = self.backups / "symlink.bundle"
        symlink_path.symlink_to(self.bundle)
        self.assertEqual(
            lifecycle._verify_discard_backup(
                self.repo, self.head, str(symlink_path), self.digest,
                backup_root=self.backups,
            ),
            "D6_DISCARD_BACKUP_PATH_INVALID",
        )

        outside_copy = self.top / "outside.bundle"
        shutil.copyfile(self.bundle, outside_copy)
        outside_copy.chmod(0o600)
        self.assertEqual(
            lifecycle._verify_discard_backup(
                self.repo, self.head, str(outside_copy), self.digest,
                backup_root=self.backups,
            ),
            "D6_DISCARD_BACKUP_PATH_INVALID",
        )

        self.assertEqual(
            lifecycle._verify_discard_backup(
                self.repo, self.head, "relative.bundle", self.digest,
                backup_root=self.backups,
            ),
            "D6_DISCARD_BACKUP_PATH_INVALID",
        )

        self.assertTrue(self.bundle.exists())

    def test_parse_owner_approved_discard(self):
        url = (
            "https://github.com/davidcatcat/davidcattoolsproject/issues/1256"
            "#issuecomment-6733583539"
        )
        bundle_path = str(self.bundle)
        valid_value = f"x-task={url},{bundle_path},{self.digest}"
        self.assertEqual(
            lifecycle._parse_owner_approved_discard([valid_value]),
            {"x-task": (url, bundle_path, self.digest)},
        )

        invalid_cases = [
            f"x-task={url},relative.bundle,{self.digest}",
            f"x-task=https://github.com/other/davidcattoolsproject/issues/1256#issuecomment-6733583539,{bundle_path},{self.digest}",
            f"x-task={url},{bundle_path},{self.digest[:-1]}",
            f"X={url},{bundle_path},{self.digest}",
            f"x-task={url},{bundle_path}",
        ]
        for invalid_value in invalid_cases:
            with self.assertRaises(lifecycle.LifecycleError) as caught:
                lifecycle._parse_owner_approved_discard([invalid_value])
            self.assertEqual(caught.exception.code, "D6_TASK_INPUT_INVALID")

        with self.assertRaises(lifecycle.LifecycleError) as caught:
            lifecycle._parse_owner_approved_discard([valid_value, valid_value])
        self.assertEqual(caught.exception.code, "D6_TASK_INPUT_INVALID")


if __name__ == "__main__":
    unittest.main()
