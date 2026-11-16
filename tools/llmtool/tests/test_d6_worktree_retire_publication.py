"""Publication-rule tests for D6 retire (temp repos only)."""

from __future__ import annotations

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
    return subprocess.run(
        ["/usr/bin/git", "-c", "user.name=D6 test",
         "-c", "user.email=d6@example.invalid", *args],
        cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class D6PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        _git(self.root, "init", "-b", "main")
        self._write("base")
        _git(self.root, "add", "file.txt")
        _git(self.root, "commit", "-m", "base")
        self.base = _git(self.root, "rev-parse", "HEAD")
        self.remote = self.root / ".git" / "refs" / "remotes" / "origin"
        self.remote.mkdir(parents=True)
        self._ref("main", self.base)

    def _write(self, text):
        (self.root / "file.txt").write_text(text + "\n", encoding="utf-8")

    def _commit(self, message):
        self._write(message)
        _git(self.root, "commit", "-am", message)
        return _git(self.root, "rev-parse", "HEAD")

    def _ref(self, name, value):
        _git(self.root, "update-ref", "refs/remotes/origin/" + name, value)

    def _bulk_refs(self, prefix, count, value):
        payload = "".join(
            "update refs/remotes/origin/%s-%03d %s\n" % (prefix, index, value)
            for index in range(count))
        subprocess.run(
            ["/usr/bin/git", "update-ref", "--stdin"], cwd=self.root,
            input=payload, check=True, capture_output=True, text=True)

    def _raw_ref(self, name, value):
        (self.remote / name).write_text(value + "\n", encoding="ascii")

    def _publication(self, branch, head, squash=None):
        return lifecycle._publication(self.root, branch, head, squash, None)

    def _publication_with_calls(self, branch, head, squash=None):
        with patch.object(lifecycle, "_run_git",
                          wraps=lifecycle._run_git) as run:
            result = self._publication(branch, head, squash)
        return result, run.call_count

    def test_ref_count_does_not_change_git_call_count(self):
        head = self._commit("task")
        self._ref("inert", self.base)
        small, small_calls = self._publication_with_calls("task/x", head)
        self.assertIsNone(small)
        self._bulk_refs("bulk", 200, self.base)
        large, large_calls = self._publication_with_calls("task/x", head)
        self.assertIsNone(large)
        self.assertLessEqual(large_calls, 10)
        self.assertEqual(small_calls, large_calls)

    def test_head_in_one_pr_ref_is_origin_pr_head(self):
        head = self._commit("task")
        published = self._commit("published")
        self._bulk_refs("bulk", 200, self.base)
        self._ref("pr-42", published)
        result, calls = self._publication_with_calls("task/x", head)
        self.assertEqual(result, "origin_pr_head")
        self.assertLessEqual(calls, 10)

    def test_head_in_none_is_none(self):
        head = self._commit("task")
        self._bulk_refs("bulk", 200, self.base)
        self.assertIsNone(self._publication("task/x", head))

    def test_head_in_origin_main_is_origin_main(self):
        head = self._commit("task")
        published = self._commit("published")
        self._ref("main", published)
        self._bulk_refs("bulk", 200, self.base)
        self.assertEqual(self._publication("task/x", head), "origin_main")

    def test_squash_evidence_survives_many_inert_refs(self):
        head = self._commit("task")
        tree = _git(self.root, "rev-parse", head + "^{tree}")
        squash = _git(self.root, "commit-tree", tree, "-p", self.base,
                      "-m", "squash")
        self._ref("main", squash)
        self._bulk_refs("bulk", 200, self.base)
        result, calls = self._publication_with_calls(
            "task/x", head, (head, squash))
        self.assertEqual(result, "squash_evidence")
        self.assertLessEqual(calls, 10)

    def test_origin_branch_precedes_other_rules(self):
        head = self._commit("task")
        published = self._commit("published")
        self._ref("main", published)
        self._ref("pr-7", published)
        self._ref("task/precedence", published)
        self.assertEqual(
            self._publication("task/precedence", head), "origin_branch")

    def test_origin_pr_head_precedes_origin_main(self):
        head = self._commit("task")
        published = self._commit("published")
        self._ref("main", published)
        self._ref("pr-7", published)
        self.assertEqual(self._publication("task/x", head), "origin_pr_head")

    def test_origin_head_is_not_a_pr_head(self):
        head = self._commit("task")
        published = self._commit("published")
        self._ref("main", published)
        _git(self.root, "symbolic-ref", "refs/remotes/origin/HEAD",
             "refs/remotes/origin/main")
        self.assertEqual(self._publication("task/x", head), "origin_main")

    def test_dangling_and_non_commit_refs_do_not_change_result(self):
        head = self._commit("task")
        published = self._commit("published")
        self._bulk_refs("bulk", 200, self.base)
        self._ref("pr-9", published)
        before = self._publication("task/x", head)
        self.assertEqual(before, "origin_pr_head")
        self._raw_ref("dangling-ref", "1" * 40)
        blob = _git(self.root, "hash-object", "-w", "file.txt")
        self._raw_ref("blob-ref", blob)
        self.assertEqual(self._publication("task/x", head), before)


if __name__ == "__main__":
    unittest.main()
