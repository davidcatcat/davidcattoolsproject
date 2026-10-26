"""Real local-Git tests for the gated D6 retire command (temp repos only)."""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import d6_worktree_lifecycle as lifecycle  # noqa: E402


def _git(cwd, *args):
    return subprocess.run(["/usr/bin/git", "-c", "user.name=D6 test",
                           "-c", "user.email=d6@example.invalid", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class D6RetireTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.top = Path(self.temp.name).resolve()
        self.source = self.top / "repo"
        self.source.mkdir(mode=0o700)
        self.tasks = self.top / "tasks"
        self.tasks.mkdir(mode=0o700)
        _git(self.source, "init", "-b", "main")
        _git(self.source, "remote", "add", "origin",
             "git@github.com:davidcatcat/davidcattoolsproject.git")
        (self.source / ".gitignore").write_text(".venv/\n*.lock\n*.log\n__pycache__/\n", encoding="utf-8")
        (self.source / "tools" / "llmtool").mkdir(parents=True)
        (self.source / "tools" / "llmtool" / "app.py").write_text("x = 1\n", encoding="utf-8")
        _git(self.source, "add", ".")
        _git(self.source, "commit", "-m", "base")
        self.base = _git(self.source, "rev-parse", "HEAD")
        self.registry = self.source / ".git" / "llmtool-worktrees"
        self.registry.mkdir(mode=0o700)
        self.outside_file = self.top / "outside-keep.txt"
        self.outside_file.write_text("keep\n", encoding="utf-8")
        self.cwds = [("1", "/")]

    def _task(self, task_id, *, parent=None, publish=True):
        root = (parent or self.tasks) / task_id
        branch = "task/" + task_id
        _git(self.source, "worktree", "add", "-b", branch, str(root), self.base)
        os.chmod(root, 0o700)
        (root / "tools" / "llmtool" / "app.py").write_text("x = 2\n", encoding="utf-8")
        _git(root, "commit", "-am", "task work")
        head = _git(root, "rev-parse", "HEAD")
        if publish:
            _git(self.source, "update-ref", "refs/remotes/origin/main", head)
        venv = root / "tools" / "llmtool" / ".venv"
        (venv / "lib").mkdir(parents=True)
        (venv / "lib" / "site.py").write_text("generated\n", encoding="utf-8")
        os.symlink(self.outside_file, venv / "lib" / "outside-link")
        (root / "tools" / "llmtool" / ".worktree-env.lock").write_text("ok\n", encoding="utf-8")
        info = root.lstat()
        record = {
            "schema_version": 1, "task_id": task_id, "trusted_agent_id": "test",
            "session_ref": "s", "lease_ref": "l", "root_realpath": str(root),
            "physical_identity": {"device": info.st_dev, "inode": info.st_ino,
                                  "uid": info.st_uid, "mode": "0o700"},
            "git_common_dir": str(self.source / ".git"), "branch": branch,
            "base_sha": self.base, "head_sha": self.base, "state": "ACTIVE",
            "last_verified_at": "2026-10-26T00:00:00Z", "evidence_refs": [],
            "retention_paths": ["tools/llmtool/.venv/", "tools/llmtool/.worktree-env.lock"],
            "lock_state": "LOCKED", "disposition": "ACTIVE_TASK",
        }
        lifecycle._atomic_record(self.registry / (task_id + ".json"), record)
        _git(self.source, "worktree", "lock", str(root))
        return root, head

    def _record(self, task_id):
        return json.loads((self.registry / (task_id + ".json")).read_text())

    def _retire(self, task_ids, *, execute=False, squash=None, discard=(), evidence=None):
        output = io.StringIO()
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_process_cwds", side_effect=lambda: self.cwds), \
                contextlib.redirect_stdout(output):
            ok = lifecycle.retire_worktrees(
                canonical_root=self.source, task_ids=task_ids, execute=execute,
                squash_evidence=squash, discard_uncommitted=discard,
                unknown_evidence=evidence,
                workspace_root=self.tasks)
        lines = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([line["task_id"] for line in lines], task_ids)
        return ok, lines

    def test_clean_merged_task_is_retired_and_branch_kept(self):
        root, head = self._task("clean-task")
        ok, (line,) = self._retire(["clean-task"], execute=True)
        self.assertTrue(ok, line)
        self.assertEqual(line["result"], "REMOVED")
        self.assertEqual(line["publication"], "origin_main")
        self.assertFalse(os.path.lexists(root))
        self.assertNotIn(str(root), _git(self.source, "worktree", "list"))
        self.assertEqual(_git(self.source, "rev-parse", "task/clean-task"), head)
        self.assertEqual(self.outside_file.read_text(), "keep\n")
        record = self._record("clean-task")
        self.assertEqual((record["state"], record["lock_state"], record["head_sha"]),
                         ("REMOVED", "UNLOCKED", head))
        self.assertIn("removed_at", record)

    def test_dry_run_removes_nothing(self):
        root, _head = self._task("dry-task")
        before = self._record("dry-task")
        ok, (line,) = self._retire(["dry-task"])
        self.assertTrue(ok)
        self.assertEqual(line["result"], "RETIRE_READY")
        self.assertEqual(line["git_removes_retention_paths"],
                         ["tools/llmtool/.venv/", "tools/llmtool/.worktree-env.lock"])
        self.assertTrue((root / "tools" / "llmtool" / ".venv" / "lib" / "site.py").is_file())
        self.assertEqual(self._record("dry-task"), before)
        self.assertIn("locked", _git(self.source, "worktree", "list", "--porcelain"))

    def test_dirty_task_is_refused(self):
        root, _head = self._task("dirty-task")
        (root / "tools" / "llmtool" / "app.py").write_text("x = 3\n", encoding="utf-8")
        ok, (line,) = self._retire(["dirty-task"], execute=True)
        self.assertFalse(ok)
        self.assertEqual(line["result"], "BLOCKED")
        self.assertIn("D6_RETIRE_DIRTY_TRACKED", line["reasons"])
        self.assertTrue((root / "tools" / "llmtool" / ".venv").is_dir())
        self.assertEqual(self._record("dirty-task")["state"], "ACTIVE")

    def _dirty_task(self, task_id):
        root, head = self._task(task_id)
        _git(self.source, "update-ref", "refs/remotes/origin/task/" + task_id, head)
        (root / "tools" / "llmtool" / "app.py").write_text("x = 3\n", encoding="utf-8")
        (root / "notes.txt").write_text("draft\n", encoding="utf-8")
        return root

    def test_discard_uncommitted_applies_only_to_named_task(self):
        approved = self._dirty_task("approved-task")
        other = self._dirty_task("other-task")
        ok, (first, second) = self._retire(["approved-task", "other-task"], execute=True,
                                           discard=["approved-task"])
        self.assertFalse(ok)
        self.assertEqual(first["result"], "REMOVED", first)
        self.assertTrue(first["discarded_uncommitted"])
        self.assertEqual(first["discard_uncommitted_paths"],
                         ["notes.txt", "tools/llmtool/app.py"])
        self.assertFalse(os.path.lexists(approved))
        record = self._record("approved-task")
        self.assertEqual(record["state"], "REMOVED")
        self.assertTrue(record["discarded_uncommitted"])
        self.assertEqual(record["discarded_uncommitted_paths"],
                         ["notes.txt", "tools/llmtool/app.py"])
        # Unnamed: dirty tracked work is refused exactly as without the
        # option, and the record is left alone because a gate other than
        # unknown content failed.
        self.assertEqual(second["result"], "BLOCKED")
        self.assertEqual(second["reasons"], ["D6_RETIRE_DIRTY_TRACKED",
                                             "D6_RETIRE_UNKNOWN_LOCAL_STATE"])
        self.assertEqual(self._record("other-task")["state"], "ACTIVE")
        self.assertNotIn("discarded_uncommitted", second)
        self.assertEqual((other / "tools" / "llmtool" / "app.py").read_text(), "x = 3\n")
        self.assertEqual((other / "notes.txt").read_text(), "draft\n")

    def test_discard_uncommitted_still_refuses_live_process(self):
        root = self._dirty_task("live-dirty-task")
        self.cwds = [("1", "/"), ("4242", str(root))]
        ok, (line,) = self._retire(["live-dirty-task"], execute=True,
                                   discard=["live-dirty-task"])
        self.assertFalse(ok)
        self.assertEqual(line["reasons"], ["D6_RETIRE_LIVE_PROCESS"])
        self.assertNotIn("discarded_uncommitted", line)
        self.assertEqual((root / "tools" / "llmtool" / "app.py").read_text(), "x = 3\n")
        self.assertEqual((root / "notes.txt").read_text(), "draft\n")
        self.assertEqual(self._record("live-dirty-task")["state"], "ACTIVE")

    def test_discard_uncommitted_dry_run_shows_without_discarding(self):
        root = self._dirty_task("dry-dirty-task")
        before = self._record("dry-dirty-task")
        ok, (line,) = self._retire(["dry-dirty-task"], discard=["dry-dirty-task"])
        self.assertTrue(ok, line)
        self.assertEqual(line["result"], "RETIRE_READY")
        self.assertEqual(line["discard_uncommitted_paths"],
                         ["notes.txt", "tools/llmtool/app.py"])
        self.assertNotIn("discarded_uncommitted", line)
        self.assertEqual((root / "tools" / "llmtool" / "app.py").read_text(), "x = 3\n")
        self.assertEqual((root / "notes.txt").read_text(), "draft\n")
        self.assertEqual(self._record("dry-dirty-task"), before)

    def test_unknown_ignored_file_marks_retain_blocked(self):
        root, _head = self._task("unknown-task")
        (root / "tools" / "llmtool" / "evidence.log").write_text("unique\n", encoding="utf-8")
        ok, (line,) = self._retire(["unknown-task"], execute=True)
        self.assertFalse(ok)
        self.assertEqual(line["result"], "RETAIN_BLOCKED")
        self.assertEqual(line["unknown_paths"], ["tools/llmtool/evidence.log"])
        self.assertTrue((root / "tools" / "llmtool" / "evidence.log").is_file())
        self.assertTrue((root / "tools" / "llmtool" / ".venv").is_dir())
        self.assertEqual(self._record("unknown-task")["state"], "RETAIN_BLOCKED")

    def test_live_process_is_refused(self):
        root, _head = self._task("live-task")
        self.cwds = [("1", "/"), ("4242", str(root / "tools"))]
        ok, (line,) = self._retire(["live-task"], execute=True)
        self.assertFalse(ok)
        self.assertEqual((line["result"], line["live_pids"]), ("BLOCKED", ["4242"]))
        self.assertTrue((root / "tools" / "llmtool" / ".venv").is_dir())
        self.assertEqual(self._record("live-task")["state"], "ACTIVE")

    def test_canonical_root_is_refused(self):
        self._task("canonical-task")
        record = self._record("canonical-task")
        record["root_realpath"] = str(self.source)
        lifecycle._replace_record(self.registry / "canonical-task.json", record)
        ok, (line,) = self._retire(["canonical-task"], execute=True)
        self.assertFalse(ok)
        self.assertEqual(line["reasons"], ["D6_RETIRE_CANONICAL_ROOT"])
        self.assertTrue((self.source / ".git").is_dir())

    def test_path_outside_workspace_parent_is_refused(self):
        elsewhere = self.top / "elsewhere"
        elsewhere.mkdir(mode=0o700)
        root, _head = self._task("outside-task", parent=elsewhere)
        ok, (line,) = self._retire(["outside-task"], execute=True)
        self.assertFalse(ok)
        self.assertEqual(line["reasons"], ["D6_RETIRE_ROOT_OUTSIDE_WORKSPACE"])
        self.assertTrue((root / "tools" / "llmtool" / ".venv").is_dir())

    def test_symlinked_retention_path_is_refused(self):
        root, _head = self._task("symlink-task")
        outside_dir = self.top / "outside-venv"
        outside_dir.mkdir()
        (outside_dir / "keep.txt").write_text("keep\n", encoding="utf-8")
        venv = root / "tools" / "llmtool" / ".venv"
        (venv / "lib" / "site.py").unlink()
        (venv / "lib" / "outside-link").unlink()
        (venv / "lib").rmdir()
        venv.rmdir()
        os.symlink(outside_dir, venv)
        ok, (line,) = self._retire(["symlink-task"], execute=True)
        self.assertFalse(ok)
        self.assertEqual(line["result"], "RETAIN_BLOCKED")
        self.assertIn("D6_RETIRE_RETENTION_PATH_SYMLINK", line["reasons"])
        self.assertTrue(venv.is_symlink())
        self.assertEqual((outside_dir / "keep.txt").read_text(), "keep\n")
        self.assertEqual(self._record("symlink-task")["state"], "RETAIN_BLOCKED")

    def test_unpublished_head_needs_matching_squash_evidence(self):
        self._task("squash-task", publish=False)
        ok, (line,) = self._retire(["squash-task"])
        self.assertFalse(ok)
        self.assertIn("D6_RETIRE_UNPUBLISHED", line["reasons"])
        head = self._record("squash-task")["branch"]
        head = _git(self.source, "rev-parse", head)
        squash = _git(self.source, "commit-tree", "-p", self.base, "-m", "squash (#1)",
                      _git(self.source, "rev-parse", head + "^{tree}"))
        _git(self.source, "update-ref", "refs/remotes/origin/main", squash)
        ok, (line,) = self._retire(["squash-task"], squash={"squash-task": (self.base, squash)})
        self.assertIn("D6_RETIRE_UNPUBLISHED", line["reasons"])
        ok, (line,) = self._retire(["squash-task"], squash={"squash-task": (head, squash)})
        self.assertTrue(ok, line)
        self.assertEqual(line["publication"], "squash_evidence")

    def test_unpublished_active_task_with_unknown_content_is_not_rewritten(self):
        root, _head = self._task("unpublished-task", publish=False)
        cache = root / "tools" / "llmtool" / "__pycache__"
        cache.mkdir()
        (cache / "app.cpython-313.pyc").write_bytes(b"bytecode")
        (root / "tools" / "llmtool" / "evidence.log").write_text("unique\n", encoding="utf-8")
        before = self._record("unpublished-task")
        ok, (line,) = self._retire(["unpublished-task"], execute=True)
        self.assertFalse(ok)
        self.assertEqual(line["result"], "BLOCKED")
        self.assertEqual(line["reasons"], ["D6_RETIRE_UNPUBLISHED",
                                           "D6_RETIRE_UNKNOWN_LOCAL_STATE"])
        self.assertFalse(line["registry_updated"])
        self.assertEqual(self._record("unpublished-task"), before)
        self.assertTrue((cache / "app.cpython-313.pyc").is_file())

    def test_regular_pycache_files_are_discarded_and_others_stay_unknown(self):
        root, _head = self._task("pycache-task")
        caches = [root / "tools" / "llmtool" / "__pycache__",
                  root / "tools" / "__pycache__"]
        for cache in caches:
            cache.mkdir()
            (cache / "app.cpython-313.pyc").write_bytes(b"bytecode")
        odd = root / "odd" / "__pycache__"
        odd.mkdir(parents=True)
        os.symlink(self.outside_file, odd / "link.cpython-313.pyc")
        (odd / "notes.txt").write_text("not bytecode\n", encoding="utf-8")
        ok, (line,) = self._retire(["pycache-task"])
        self.assertFalse(ok)
        self.assertEqual(line["pycache_discard_count"], 2)
        self.assertEqual(line["unknown_paths"], ["odd/__pycache__/link.cpython-313.pyc",
                                                 "odd/__pycache__/notes.txt"])
        (odd / "link.cpython-313.pyc").unlink()
        (odd / "notes.txt").unlink()
        odd.rmdir()
        odd.parent.rmdir()
        ok, (line,) = self._retire(["pycache-task"], execute=True)
        self.assertTrue(ok, line)
        self.assertEqual((line["result"], line["pycache_discard_count"]), ("REMOVED", 2))
        self.assertFalse(os.path.lexists(root))
        self.assertEqual(self.outside_file.read_text(), "keep\n")
        self.assertIn("pycache files=2", self._record("pycache-task")["evidence_refs"][-1])

    def _retain_blocked_task(self, task_id):
        root, _head = self._task(task_id)
        (root / "tools" / "llmtool" / "evidence.log").write_text("reviewed\n", encoding="utf-8")
        ok, (line,) = self._retire([task_id], execute=True)
        self.assertEqual(line["result"], "RETAIN_BLOCKED")
        self.assertEqual(self._record(task_id)["state"], "RETAIN_BLOCKED")
        ok, (line,) = self._retire([task_id])
        self.assertIn("D6_RETIRE_STATE_RETAIN_BLOCKED", line["reasons"])
        return root, line["unknown_digest"]

    def test_retain_blocked_evidence_with_matching_digest_proceeds(self):
        root, digest = self._retain_blocked_task("blocked-task")
        ok, (line,) = self._retire(["blocked-task"], execute=True,
                                   evidence={"blocked-task": digest})
        self.assertTrue(ok, line)
        self.assertEqual(line["result"], "REMOVED")
        self.assertTrue(line["unknown_accepted"])
        self.assertFalse(os.path.lexists(root))
        record = self._record("blocked-task")
        self.assertEqual(record["state"], "REMOVED")
        self.assertIn("accepted unknown digest=" + digest, record["evidence_refs"][-1])

    def test_retain_blocked_evidence_refuses_changed_content(self):
        root, digest = self._retain_blocked_task("changed-task")
        (root / "tools" / "llmtool" / "later.log").write_text("new\n", encoding="utf-8")
        before = self._record("changed-task")
        ok, (line,) = self._retire(["changed-task"], execute=True,
                                   evidence={"changed-task": digest})
        self.assertFalse(ok)
        self.assertEqual(line["result"], "BLOCKED")
        self.assertIn("D6_RETIRE_EVIDENCE_MISMATCH", line["reasons"])
        self.assertEqual(self._record("changed-task"), before)
        self.assertTrue((root / "tools" / "llmtool" / "later.log").is_file())


if __name__ == "__main__":
    unittest.main()
