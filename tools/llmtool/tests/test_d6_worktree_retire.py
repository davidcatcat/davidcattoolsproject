"""Real local-Git tests for the gated D6 retire command (temp repos only)."""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import d6_worktree_lifecycle as lifecycle  # noqa: E402

OWNER_APPROVED_RETAIN_URL = (
    "https://github.com/davidcatcat/davidcattoolsproject/issues/529"
    "#issuecomment-1234567890")


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
        # Temp trust home only: the real ~/.config/llmtool/trust.json is never read.
        trust_home = self.top / "trust-home"
        trust_dir = trust_home / ".config" / "llmtool"
        trust_dir.mkdir(parents=True, mode=0o700)
        for directory in (trust_home, trust_home / ".config", trust_dir):
            directory.chmod(0o700)
        trust_path = trust_dir / "trust.json"
        trust_path.write_text(json.dumps({
            "schema_version": 1, "canonical_root": str(self.source),
            "workspace_root": str(self.top), "tasks_root": str(self.tasks),
            "repository": {"slug": "davidcatcat/davidcattoolsproject",
                           "allowed_origins": [
                               "git@github.com:davidcatcat/davidcattoolsproject.git"],
                           "approval_refs": {}},
            "ci_trust": {}, "tools": {},
        }), encoding="utf-8")
        trust_path.chmod(0o600)
        original_load_trust = lifecycle._load_trust
        trust_patch = patch.object(
            lifecycle, "_load_trust",
            side_effect=lambda: original_load_trust(home=str(trust_home)))
        trust_patch.start()
        self.addCleanup(trust_patch.stop)

    def _task(self, task_id, *, parent=None, publish=True, reviewer=False):
        root = (parent or self.tasks) / task_id
        branch = ("review/" if reviewer else "task/") + task_id
        _git(self.source, "worktree", "add", "-b", branch, str(root), self.base)
        os.chmod(root, 0o700)
        (root / "tools" / "llmtool" / "app.py").write_text("x = 2\n", encoding="utf-8")
        _git(root, "commit", "-am", "task work")
        head = _git(root, "rev-parse", "HEAD")
        if publish:
            _git(self.source, "update-ref", "refs/remotes/origin/main", head)
            if reviewer:
                _git(self.source, "update-ref", "refs/remotes/origin/" + branch, head)
        venv = root / "tools" / "llmtool" / ".venv"
        (venv / "lib").mkdir(parents=True)
        (venv / "lib" / "site.py").write_text("generated\n", encoding="utf-8")
        os.symlink(self.outside_file, venv / "lib" / "outside-link")
        (root / "tools" / "llmtool" / ".worktree-env.lock").write_text("ok\n", encoding="utf-8")
        info = root.lstat()
        record = {
            "schema_version": 1, "task_id": task_id,
            "trusted_agent_id": "codex-reviewer" if reviewer else "test",
            "session_ref": "s", "lease_ref": "l", "root_realpath": str(root),
            "physical_identity": {"device": info.st_dev, "inode": info.st_ino,
                                  "uid": info.st_uid, "mode": "0o700"},
            "git_common_dir": str(self.source / ".git"), "branch": branch,
            "base_sha": self.base, "head_sha": head, "state": "ACTIVE",
            "last_verified_at": "2026-10-26T00:00:00Z", "evidence_refs": [],
            "retention_paths": ["tools/llmtool/.venv/", "tools/llmtool/.worktree-env.lock"],
            "lock_state": "LOCKED", "disposition": "ACTIVE_TASK",
        }
        lifecycle._atomic_record(self.registry / (task_id + ".json"), record)
        _git(self.source, "worktree", "lock", str(root))
        return root, head

    def _cwds(self):
        # A list of lists hands out one answer per lsof call; a plain list repeats.
        if self.cwds and isinstance(self.cwds[0], list):
            return self.cwds.pop(0)
        return self.cwds

    def _record(self, task_id):
        return json.loads((self.registry / (task_id + ".json")).read_text())

    def _retire(self, task_ids, *, execute=False, squash=None, discard=(), evidence=None,
                reviewed_pr=None):
        output = io.StringIO()
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_process_cwds", side_effect=self._cwds), \
                contextlib.redirect_stdout(output):
            ok = lifecycle.retire_worktrees(
                canonical_root=self.source, task_ids=task_ids, execute=execute,
                squash_evidence=squash, discard_uncommitted=discard,
                unknown_evidence=evidence,
                reviewed_pr_evidence=reviewed_pr,
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

    def test_closed_pr_reviewer_is_retired_with_bound_review_evidence(self):
        root, head = self._task("review-clean", reviewer=True)
        ok, (line,) = self._retire(
            ["review-clean"], execute=True,
            reviewed_pr={"review-clean": ("529", head)})
        self.assertTrue(ok, line)
        self.assertEqual(line["result"], "REMOVED")
        self.assertEqual(line["publication"], "reviewed_pr_head")
        self.assertEqual(line["reviewed_pr_number"], "529")
        self.assertFalse(os.path.lexists(root))

    def test_dirty_reviewer_is_refused_even_with_discard_authorization(self):
        root, head = self._task("review-dirty", reviewer=True)
        (root / "tools" / "llmtool" / "app.py").write_text("dirty\n", encoding="utf-8")
        ok, (line,) = self._retire(
            ["review-dirty"], execute=True, discard=["review-dirty"],
            reviewed_pr={"review-dirty": ("530", head)})
        self.assertFalse(ok)
        self.assertIn("D6_RETIRE_DIRTY_TRACKED", line["reasons"])
        self.assertTrue(root.is_dir())

    def test_executor_cannot_use_reviewer_pr_evidence(self):
        _root, head = self._task("executor-evidence")
        ok, (line,) = self._retire(
            ["executor-evidence"], reviewed_pr={"executor-evidence": ("531", head)})
        self.assertFalse(ok)
        self.assertIn("D6_RETIRE_REVIEW_EVIDENCE_NOT_APPLICABLE", line["reasons"])

    def test_reviewer_pr_head_must_match_registry_and_worktree(self):
        root, _head = self._task("review-mismatch", reviewer=True)
        ok, (line,) = self._retire(
            ["review-mismatch"], reviewed_pr={"review-mismatch": ("532", "0" * 40)})
        self.assertFalse(ok)
        self.assertIn("D6_RETIRE_REVIEW_HEAD_MISMATCH", line["reasons"])
        self.assertTrue(root.is_dir())

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

    def test_lsof_resolver_uses_first_safe_candidate(self):
        real_lstat = os.lstat

        def lstat(path):
            if path == "/usr/sbin/lsof":
                raise FileNotFoundError(path)
            if path == "/usr/bin/lsof":
                return Mock(st_mode=0o100755, st_uid=0)
            return real_lstat(path)

        with patch.object(os, "getuid", return_value=1000), \
                patch.object(os, "lstat", side_effect=lstat):
            self.assertEqual(lifecycle._resolve_lsof(), "/usr/bin/lsof")

    def test_lsof_resolver_rejects_unsafe_candidates(self):
        real_lstat = os.lstat
        cases = (
            Mock(st_mode=0o100775, st_uid=0),
            Mock(st_mode=0o100755, st_uid=1234),
            Mock(st_mode=0o120755, st_uid=0),
        )
        for first in cases:
            def lstat(path, first=first):
                if path == "/usr/sbin/lsof":
                    return first
                if path == "/usr/bin/lsof":
                    raise FileNotFoundError(path)
                return real_lstat(path)

            with patch.object(os, "getuid", return_value=1000), \
                    patch.object(os, "lstat", side_effect=lstat):
                self.assertIsNone(lifecycle._resolve_lsof())

    def test_lsof_unavailable_keeps_retire_liveness_unknown(self):
        self._task("lsof-none")
        output = io.StringIO()
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "_resolve_lsof", return_value=None), \
                contextlib.redirect_stdout(output):
            self.assertIsNone(lifecycle._process_cwds())
            ok = lifecycle.retire_worktrees(
                canonical_root=self.source, task_ids=["lsof-none"], execute=True,
                squash_evidence=None, discard_uncommitted=(),
                unknown_evidence=None, workspace_root=self.tasks)
        lines = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([line["task_id"] for line in lines], ["lsof-none"])
        self.assertFalse(ok)
        self.assertEqual(lines[0]["result"], "BLOCKED")
        self.assertTrue(
            lines[0].get("liveness") == "LIVENESS_UNKNOWN"
            or any("LIVENESS_UNKNOWN" in reason
                   for reason in lines[0].get("reasons", [])))
        self.assertEqual(self._record("lsof-none")["state"], "ACTIVE")

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

    def test_retain_blocked_evidence_is_refused_when_unknown_list_is_truncated(self):
        root, _head = self._task("many-unknown-task")
        logs = root / "tools" / "llmtool" / "logs"
        logs.mkdir()
        for index in range(60):
            (logs / ("run-%02d.log" % index)).write_text("x\n", encoding="utf-8")
        ok, (line,) = self._retire(["many-unknown-task"], execute=True)
        self.assertEqual(line["result"], "RETAIN_BLOCKED")
        ok, (line,) = self._retire(["many-unknown-task"])
        self.assertEqual((line["unknown_count"], line["unknown_truncated"],
                          len(line["unknown_paths"])), (60, True, 50))
        before = self._record("many-unknown-task")
        ok, (line,) = self._retire(["many-unknown-task"], execute=True,
                                   evidence={"many-unknown-task": line["unknown_digest"]})
        self.assertFalse(ok)
        self.assertEqual(line["result"], "BLOCKED")
        self.assertIn("D6_RETIRE_EVIDENCE_TRUNCATED", line["reasons"])
        self.assertFalse(line["registry_updated"])
        self.assertEqual(self._record("many-unknown-task"), before)
        self.assertEqual(len(list(logs.iterdir())), 60)

    def test_failed_registry_write_reports_registry_not_updated(self):
        root = self._dirty_task("write-fail-task")
        # Plan sees no process; the post-discard recheck sees one, so the
        # discard is recorded on the failure path, whose write then fails.
        self.cwds = [[("1", "/")], [("4242", str(root))]]
        with patch.object(lifecycle, "_replace_record",
                          side_effect=lifecycle.LifecycleError("D6_REGISTRY_UPDATE_FAILED")):
            ok, (line,) = self._retire(["write-fail-task"], execute=True,
                                       discard=["write-fail-task"])
        self.assertFalse(ok)
        self.assertEqual(line["reasons"], ["D6_RETIRE_LIVE_PROCESS",
                                           "D6_REGISTRY_UPDATE_FAILED"])
        self.assertTrue(line["discarded_uncommitted"])
        self.assertFalse(line["registry_updated"])
        self.assertTrue(root.is_dir())

    def _mark_retain_blocked(self, task_id):
        record = self._record(task_id)
        record["state"] = "RETAIN_BLOCKED"
        lifecycle._replace_record(self.registry / (task_id + ".json"), record)

    def _retain_blocked_absent(self, task_id):
        root, _head = self._task(task_id)
        self._mark_retain_blocked(task_id)
        shutil.rmtree(root)
        self.assertFalse(os.path.lexists(root))
        return root

    def _release_retain(self, approvals, *, execute=False):
        output = io.StringIO()
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                contextlib.redirect_stdout(output):
            ok = lifecycle.owner_approved_retain_release(
                canonical_root=self.source, approvals=approvals, execute=execute,
                workspace_root=self.tasks)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["mode"], "owner-approved-retain-release")
        return ok, payload["records"]

    def test_owner_approved_retain_release_marks_absent_retain_blocked(self):
        root = self._retain_blocked_absent("owner-release-task")
        ok, (line,) = self._release_retain(
            {"owner-release-task": OWNER_APPROVED_RETAIN_URL}, execute=True)
        self.assertTrue(ok, line)
        self.assertEqual(line["result"], "RETIRED_EXTERNALLY")
        self.assertEqual(line["approval_ref"], OWNER_APPROVED_RETAIN_URL)
        self.assertFalse(os.path.lexists(root))
        record = self._record("owner-release-task")
        self.assertEqual(record["state"], "RETIRED_EXTERNALLY")
        self.assertEqual(record["owner_approval_ref"], OWNER_APPROVED_RETAIN_URL)
        self.assertIn("retired_externally_at", record)

    def test_owner_approved_retain_release_refuses_present_directory(self):
        root, _head = self._task("owner-present-task")
        self._mark_retain_blocked("owner-present-task")
        before = self._record("owner-present-task")
        ok, (line,) = self._release_retain(
            {"owner-present-task": OWNER_APPROVED_RETAIN_URL}, execute=True)
        self.assertFalse(ok)
        self.assertEqual(line["result"], "BLOCKED")
        self.assertEqual(line["reason"], "D6_RELEASE_RETAIN_WORKTREE_PRESENT")
        self.assertTrue(root.is_dir())
        self.assertEqual(self._record("owner-present-task"), before)

    def test_owner_approved_retain_release_refuses_symlinked_path(self):
        root = self._retain_blocked_absent("owner-symlink-task")
        os.symlink(self.outside_file, root)
        before = self._record("owner-symlink-task")
        ok, (line,) = self._release_retain(
            {"owner-symlink-task": OWNER_APPROVED_RETAIN_URL}, execute=True)
        self.assertFalse(ok)
        self.assertEqual(line["reason"], "D6_RELEASE_RETAIN_WORKTREE_PRESENT")
        self.assertTrue(root.is_symlink())
        self.assertEqual(self._record("owner-symlink-task"), before)

    def test_owner_approved_retain_release_refuses_non_retain_blocked(self):
        _root, _head = self._task("owner-active-task")
        before = self._record("owner-active-task")
        ok, (line,) = self._release_retain(
            {"owner-active-task": OWNER_APPROVED_RETAIN_URL}, execute=True)
        self.assertFalse(ok)
        self.assertEqual(line["result"], "BLOCKED")
        self.assertEqual(line["reason"], "D6_RELEASE_RETAIN_RECORD_INVALID")
        self.assertEqual(self._record("owner-active-task"), before)

    def test_owner_approved_retain_release_refuses_bad_url(self):
        self._retain_blocked_absent("owner-bad-url-task")
        before = self._record("owner-bad-url-task")
        bad_urls = (
            "https://github.com/davidcatcat/davidcattoolsproject/issues/352"
            "#issuecomment-1234567890",
            "https://github.com/davidcatcat/davidcattoolsproject/pull/529"
            "#issuecomment-1234567890",
            "https://github.com/other/repo/issues/529#issuecomment-1234567890",
            "not-a-url",
        )
        for url in bad_urls:
            with self.assertRaises(lifecycle.LifecycleError) as caught:
                lifecycle._parse_owner_approved_retain_release(
                    ["owner-bad-url-task=" + url])
            self.assertEqual(caught.exception.code, "D6_TASK_INPUT_INVALID")
            with self.assertRaises(lifecycle.LifecycleError) as caught:
                lifecycle.owner_approved_retain_release(
                    canonical_root=self.source,
                    approvals={"owner-bad-url-task": url}, execute=True,
                    workspace_root=self.tasks)
            self.assertEqual(caught.exception.code, "D6_TASK_INPUT_INVALID")
        self.assertEqual(self._record("owner-bad-url-task"), before)

    def test_owner_approved_retain_release_dry_run_changes_nothing(self):
        self._retain_blocked_absent("owner-dry-task")
        before = self._record("owner-dry-task")
        ok, (line,) = self._release_retain(
            {"owner-dry-task": OWNER_APPROVED_RETAIN_URL})
        self.assertTrue(ok, line)
        self.assertEqual(line["mode"], "dry-run")
        self.assertEqual(line["result"], "RELEASE_READY")
        self.assertEqual(line["approval_ref"], OWNER_APPROVED_RETAIN_URL)
        self.assertEqual(self._record("owner-dry-task"), before)

    def test_owner_approved_retain_release_cli_dry_run_is_wired(self):
        self._retain_blocked_absent("owner-cli-task")
        before = self._record("owner-cli-task")
        output = io.StringIO()
        with patch.dict(os.environ, {"LLMTOOL_D6_TESTING": "1"}), \
                patch.object(lifecycle, "TASKS_ROOT", self.tasks), \
                contextlib.redirect_stdout(output):
            code = lifecycle._main([
                "--owner-approved-retain-release",
                "owner-cli-task=" + OWNER_APPROVED_RETAIN_URL,
                "--repo-root", str(self.source)])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["mode"], "owner-approved-retain-release")
        self.assertFalse(payload["execute"])
        self.assertEqual(payload["records"][0]["result"], "RELEASE_READY")
        self.assertEqual(self._record("owner-cli-task"), before)


if __name__ == "__main__":
    unittest.main()
