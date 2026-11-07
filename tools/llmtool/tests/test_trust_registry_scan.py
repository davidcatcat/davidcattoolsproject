"""Registry scan tests for trust init --replace (#1093).

An oversized D6 registry record in a non-active state must not block a trust
rotation; active-state records and every unsafe-file check stay fail closed.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from llmtool import trust_cli


_SMALL_LIMIT = 64 * 1024
_MAX_LIMIT = 1024 * 1024
_MISSING = object()


def _record(state=_MISSING, size=None, **fields):
    """Return JSON bytes for one record, padded to exactly ``size`` bytes."""
    value = dict(fields)
    if state is not _MISSING:
        value["state"] = state
    if size is None:
        return json.dumps(value).encode("utf-8")
    value["pad"] = ""
    base = len(json.dumps(value).encode("utf-8"))
    if size < base:
        raise AssertionError("record size too small for its fields")
    value["pad"] = "x" * (size - base)
    raw = json.dumps(value).encode("utf-8")
    if len(raw) != size:
        raise AssertionError("record padding mismatch")
    return raw


def _stale(state, size=None):
    return _record(state, size, trust_sha256="old", trust_binding_sha256="old")


class TrustRegistryScanTest(unittest.TestCase):
    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git is unavailable")
        self.base = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.base)
        self.base.chmod(0o700)
        self.home = self.base / "home"
        self.home.mkdir(mode=0o700)
        self.repo = self.base / "repo"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.registry_dir = self.repo / ".git" / "llmtool-worktrees"
        self.registry_dir.mkdir(mode=0o700)
        self.registry_dir.chmod(0o700)

    def _write(self, name, raw, mode=0o600):
        path = self.registry_dir / name
        path.write_bytes(raw)
        path.chmod(mode)
        return path

    def _scan(self, new_binding="new"):
        # The replace path passes the trusted canonical_root from trust.json (#369).
        return trust_cli._affected_active_records(str(self.repo), new_binding)

    def _assert_invalid(self):
        with self.assertRaises(trust_cli.TrustError) as caught:
            self._scan()
        self.assertEqual(caught.exception.code, "TRUST_REGISTRY_INVALID")

    def _snapshot(self):
        result = {}
        for path in sorted(self.registry_dir.iterdir()):
            info = path.lstat()
            raw = None if path.is_symlink() else path.read_bytes()
            result[path.name] = (raw, info.st_ino, info.st_mode, info.st_nlink,
                                 info.st_mtime_ns)
        return result

    # Oversized non-active records are ignored.

    def test_oversized_retain_blocked_record_does_not_block_and_counts_zero(self):
        self._write("legacy.json", _stale("RETAIN_BLOCKED", 437 * 1024))
        before = self._snapshot()
        self.assertEqual(self._scan(), 0)
        self.assertEqual(self._snapshot(), before)

    def test_record_of_exactly_one_mebibyte_is_accepted(self):
        self._write("legacy.json", _stale("REMOVED", _MAX_LIMIT))
        self.assertEqual(self._scan(), 0)

    # Oversized active-state records are counted by the existing rule.

    def test_oversized_active_record_with_stale_binding_counts_one(self):
        self._write("active.json", _stale("ACTIVE", 200 * 1024))
        self.assertEqual(self._scan(), 1)

    def test_oversized_active_record_with_matching_binding_counts_zero(self):
        self._write("active.json", _record("ACTIVE", 200 * 1024, trust_sha256="current",
                                           trust_binding_sha256="new"))
        self.assertEqual(self._scan(), 0)

    def test_oversized_review_pending_and_repair_records_are_counted(self):
        self._write("review.json", _stale("REVIEW_PENDING", 300 * 1024))
        self._write("repair.json", _stale("REPAIR", _MAX_LIMIT))
        self._write("legacy.json", _stale("RETAIN_BLOCKED", 437 * 1024))
        before = self._snapshot()
        self.assertEqual(self._scan(), 2)
        self.assertEqual(self._snapshot(), before)

    def test_oversized_review_pending_record_alone_is_counted(self):
        self._write("review.json", _stale("REVIEW_PENDING", _SMALL_LIMIT + 1))
        self.assertEqual(self._scan(), 1)

    def test_oversized_repair_record_alone_is_counted(self):
        self._write("repair.json", _stale("REPAIR", _SMALL_LIMIT + 1))
        self.assertEqual(self._scan(), 1)

    # Oversized records that cannot be trusted still fail closed.

    def test_oversized_record_that_is_not_json_is_invalid(self):
        self._write("broken.json", b"x" * (100 * 1024))
        self._assert_invalid()

    def test_oversized_record_that_is_not_utf8_is_invalid(self):
        self._write("broken.json", b"\xff" * (100 * 1024))
        self._assert_invalid()

    def test_oversized_record_without_state_is_invalid(self):
        self._write("stateless.json", _record(size=100 * 1024, trust_binding_sha256="old"))
        self._assert_invalid()

    def test_oversized_record_with_non_string_state_is_invalid(self):
        raw = json.dumps({"state": 7, "pad": "x" * (100 * 1024)}).encode("utf-8")
        self._write("numeric.json", raw)
        self._assert_invalid()

    def test_oversized_record_that_is_not_an_object_is_invalid(self):
        raw = json.dumps(["RETAIN_BLOCKED", "x" * (100 * 1024)]).encode("utf-8")
        self._write("list.json", raw)
        self._assert_invalid()

    def test_record_over_one_mebibyte_is_invalid(self):
        self._write("huge.json", _stale("RETAIN_BLOCKED", _MAX_LIMIT + 1))
        self._assert_invalid()

    def test_active_record_over_one_mebibyte_is_invalid(self):
        self._write("huge.json", _stale("ACTIVE", _MAX_LIMIT + 1))
        self._assert_invalid()

    def test_empty_record_is_invalid(self):
        self._write("empty.json", b"")
        self._assert_invalid()

    # Unsafe files fail closed at every size.

    def test_symlinked_record_is_invalid(self):
        for size in (None, 437 * 1024):
            with self.subTest(size=size):
                outside = self.base / "outside.json"
                outside.write_bytes(_stale("RETAIN_BLOCKED", size))
                outside.chmod(0o600)
                link = self.registry_dir / "linked.json"
                os.symlink(outside, link)
                try:
                    self._assert_invalid()
                    self.assertTrue(link.is_symlink())
                    self.assertTrue(outside.exists())
                finally:
                    link.unlink()
                    outside.unlink()

    def test_record_with_mode_0644_is_invalid(self):
        for size in (None, 437 * 1024):
            with self.subTest(size=size):
                path = self._write("open.json", _stale("RETAIN_BLOCKED", size), mode=0o644)
                try:
                    self._assert_invalid()
                    self.assertEqual(path.stat().st_mode & 0o777, 0o644)
                finally:
                    path.unlink()

    def test_record_with_two_hard_links_is_invalid(self):
        for size in (None, 437 * 1024):
            with self.subTest(size=size):
                path = self._write("linked.json", _stale("RETAIN_BLOCKED", size))
                second = self.base / "second-link.json"
                os.link(path, second)
                try:
                    self._assert_invalid()
                    self.assertEqual(path.stat().st_nlink, 2)
                finally:
                    second.unlink()
                    path.unlink()

    # Small records behave as before.

    def test_small_records_behave_as_before(self):
        records = {
            "active-old": {"state": "ACTIVE", "trust_sha256": "old",
                           "trust_binding_sha256": "old"},
            "active-match": {"state": "ACTIVE", "trust_sha256": "current",
                             "trust_binding_sha256": "new"},
            "active-no-stamp": {"state": "ACTIVE"},
            "review-old": {"state": "REVIEW_PENDING", "trust_sha256": "old",
                           "trust_binding_sha256": "old"},
            "repair-no-binding": {"state": "REPAIR", "trust_sha256": "current"},
            "removed": {"state": "REMOVED", "trust_binding_sha256": "old"},
            "retained": {"state": "RETAIN_BLOCKED"},
        }
        for task_id, record in records.items():
            self._write(task_id + ".json", json.dumps(record).encode("utf-8"))
        before = self._snapshot()
        self.assertEqual(self._scan(), 4)
        self.assertEqual(self._snapshot(), before)

    def test_record_of_exactly_64_kib_behaves_as_before(self):
        self._write("active.json", _stale("ACTIVE", _SMALL_LIMIT))
        self._write("legacy.json", _stale("RETAIN_BLOCKED", _SMALL_LIMIT))
        self.assertEqual(self._scan(), 1)

    def test_small_record_without_state_is_invalid(self):
        self._write("stateless.json", _record(trust_binding_sha256="old"))
        self._assert_invalid()

    def test_small_record_that_is_not_json_is_invalid(self):
        self._write("broken.json", b"{not json")
        self._assert_invalid()

    def test_missing_registry_directory_counts_zero(self):
        self.registry_dir.rmdir()
        self.assertEqual(self._scan(), 0)

    def test_registry_directory_not_private_is_invalid(self):
        self._write("active.json", _stale("ACTIVE"))
        self.registry_dir.chmod(0o755)
        self._assert_invalid()


if __name__ == "__main__":
    unittest.main()
