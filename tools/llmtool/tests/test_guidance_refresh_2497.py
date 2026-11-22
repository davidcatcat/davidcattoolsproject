"""#2497: automatic guidance-drift.json hash refresh during task advance."""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from llmtool import guidance_refresh
from llmtool import task_advance


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "guidance_drift_check.py"
SPEC = importlib.util.spec_from_file_location("guidance_drift_check", SCRIPT)
guidance_drift_check = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = guidance_drift_check
SPEC.loader.exec_module(guidance_drift_check)


class GuidanceRefreshTests(unittest.TestCase):
    TASK_ID = "1" * 32
    EXECUTOR_TASK = "3" * 32
    BRANCH = "feature/guidance-2497"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bare = os.path.join(self.tmp.name, "bare.git")
        self.root = os.path.join(self.tmp.name, "d6")
        self.canonical = os.path.join(self.tmp.name, "canonical")
        os.makedirs(os.path.join(self.canonical, ".git"), exist_ok=True)
        self.git(["init", "--bare", self.bare], cwd=self.tmp.name)
        self.git(["clone", self.bare, self.root], cwd=self.tmp.name)
        self.git(["config", "user.name", "Tester"], cwd=self.root)
        self.git(["config", "user.email", "tester@example.invalid"], cwd=self.root)
        self.git(["checkout", "-b", "main"], cwd=self.root)

        self.agents_text = "Guidance `ALLOWED`. Model `gpt-6-luna`.\n"
        self.claude_text = "Guidance `ALLOWED`. Model `gpt-6-luna`.\n"
        self.write_rel("AGENTS.md", self.agents_text)
        self.write_rel("CLAUDE.md", self.claude_text)
        self.write_rel("allowed.txt", "base\n")
        self.write_rel(
            "tools/llmtool/developer-routes.json",
            json.dumps({
                "schema_version": 1,
                "routes": {
                    "fixture": {
                        "default_model": "gpt-6-luna",
                        "default_effort": "medium",
                        "default_timeout": 1800,
                        "max_timeout": 3600,
                    },
                },
            }) + "\n",
        )
        self.write_rel(
            "tools/llmtool/guidance-drift.json",
            json.dumps({
                "schema_version": 1,
                "hashes": {
                    "AGENTS.md": self.sha256_text(self.agents_text),
                    "CLAUDE.md": self.sha256_text(self.claude_text),
                },
                "codex_only": [],
            }, indent=2) + "\n",
        )
        self.git(["add", "."], cwd=self.root)
        self.git(["commit", "-m", "initial"], cwd=self.root)
        self.git(["push", "origin", "HEAD:refs/heads/main"], cwd=self.root)
        self.git(["checkout", "-b", self.BRANCH], cwd=self.root)
        self.git(["push", "origin", f"HEAD:refs/heads/{self.BRANCH}"], cwd=self.root)
        self.initial_head = self.head()
        self.recorded = []
        self.pr_exists = False
        self.gh_calls = []

    @staticmethod
    def sha256_text(text):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def git(args, *, cwd):
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        return proc.stdout

    @staticmethod
    def git_runner(root, args):
        return subprocess.run(
            ["git", *args],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    def head(self):
        return self.git(["rev-parse", "HEAD"], cwd=self.root).strip()

    def write_rel(self, rel_path, content):
        path = Path(self.root) / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def read_rel(self, rel_path):
        return (Path(self.root) / rel_path).read_text(encoding="utf-8")

    def read_drift_config(self):
        return json.loads(self.read_rel("tools/llmtool/guidance-drift.json"))

    def record_result(self, **kwargs):
        current = self.head()
        self.recorded.append((current, kwargs))
        return current

    def gh(self, _root, args):
        args = list(args)
        self.gh_calls.append(args)
        if args[:2] == ["pr", "list"]:
            pulls = [{"number": 23, "headRefOid": self.head()}] if self.pr_exists else []
            return subprocess.CompletedProcess(["gh", *args], 0, json.dumps(pulls).encode(), b"")
        if args[:2] == ["pr", "create"]:
            self.pr_exists = True
            return subprocess.CompletedProcess(
                ["gh", *args], 0, b"https://github.com/owner/repo/pull/23\n", b""
            )
        raise AssertionError(f"unexpected gh call: {args}")

    def publish(self, *, write_paths):
        record = {"task_id": self.TASK_ID, "developer": {"write_paths": write_paths}}
        d6_binding = {
            "root": self.root,
            "common_dir": os.path.join(self.canonical, ".git"),
            "branch": self.BRANCH,
            "task_id": self.EXECUTOR_TASK,
            "recorded_result_head": self.initial_head,
        }
        return task_advance._publish_task(
            record,
            d6_binding,
            self.EXECUTOR_TASK,
            git_runner=self.git_runner,
            gh_runner=self.gh,
            record_result_call=self.record_result,
        )

    def test_authorized_uncommitted_guidance_edit_refreshes_before_publish(self):
        updated_claude = self.claude_text + "Updated guidance.\n"
        self.write_rel("CLAUDE.md", updated_claude)
        # Before publish, the exact-head drift check fails HASH_STALE.
        self.assertTrue(any("CLAUDE.md: HASH_STALE" in f for f in guidance_drift_check.check(self.root)))

        result = self.publish(write_paths=["CLAUDE.md"])

        published_head = self.head()
        self.assertEqual(result, {"pr": 23, "head": published_head})
        self.assertEqual(self.recorded[0][0], published_head)
        self.assertEqual(guidance_drift_check.check(self.root), [])
        config = self.read_drift_config()
        self.assertEqual(config["hashes"]["CLAUDE.md"], self.sha256_text(updated_claude))
        self.assertEqual(config["hashes"]["AGENTS.md"], self.sha256_text(self.agents_text))

    def test_authorized_committed_guidance_edit_refreshes_before_publish(self):
        updated_claude = self.claude_text + "Committed guidance.\n"
        self.write_rel("CLAUDE.md", updated_claude)
        self.git(["add", "--", "CLAUDE.md"], cwd=self.root)
        self.git(["commit", "-m", "edit CLAUDE.md"], cwd=self.root)
        dev_head = self.head()

        result = self.publish(write_paths=["CLAUDE.md"])

        published_head = self.head()
        self.assertNotEqual(published_head, dev_head)
        self.assertEqual(result, {"pr": 23, "head": published_head})
        self.assertEqual(self.recorded[0][0], published_head)
        self.assertEqual(guidance_drift_check.check(self.root), [])
        self.assertEqual(
            self.read_drift_config()["hashes"]["CLAUDE.md"],
            self.sha256_text(updated_claude),
        )

    def test_guidance_edit_outside_write_scope_is_refused_and_changes_nothing(self):
        updated_claude = self.claude_text + "Unauthorized edit.\n"
        self.write_rel("CLAUDE.md", updated_claude)
        self.git(["add", "--", "CLAUDE.md"], cwd=self.root)
        self.git(["commit", "-m", "edit CLAUDE.md"], cwd=self.root)
        dev_head = self.head()
        before_config = self.read_rel("tools/llmtool/guidance-drift.json")

        with self.assertRaises(guidance_refresh.GuidanceRefreshError) as caught:
            guidance_refresh.refresh_guidance_hashes(
                root=self.root,
                head=dev_head,
                write_paths=["allowed.txt"],
                task_commits=[(dev_head, [self.initial_head])],
                git_runner=self.git_runner,
            )
        self.assertEqual(caught.exception.code, "GUIDANCE_REFRESH_OUT_OF_SCOPE")
        self.assertEqual(self.head(), dev_head)
        self.assertEqual(self.read_rel("tools/llmtool/guidance-drift.json"), before_config)

        with self.assertRaises(task_advance._PublishRefusal) as pub_caught:
            self.publish(write_paths=["allowed.txt"])
        self.assertEqual(pub_caught.exception.code, "PUBLISH_OUT_OF_SCOPE")
        self.assertEqual(self.recorded, [])
        self.assertEqual(self.read_rel("tools/llmtool/guidance-drift.json"), before_config)

    def test_guidance_file_not_in_task_diff_is_refused_and_changes_nothing(self):
        # Task only changed allowed.txt, even though CLAUDE.md is in write_paths.
        self.write_rel("allowed.txt", "changed\n")
        self.git(["add", "--", "allowed.txt"], cwd=self.root)
        self.git(["commit", "-m", "edit allowed.txt"], cwd=self.root)
        dev_head = self.head()
        # Simulate an unapproved/dirty working-tree edit to CLAUDE.md.
        self.write_rel("CLAUDE.md", self.claude_text + "Uncommitted edit.\n")
        before_config = self.read_rel("tools/llmtool/guidance-drift.json")

        with self.assertRaises(guidance_refresh.GuidanceRefreshError) as caught:
            guidance_refresh.refresh_guidance_hashes(
                root=self.root,
                head=dev_head,
                write_paths=["CLAUDE.md", "allowed.txt"],
                task_commits=[(dev_head, [self.initial_head])],
                target_paths=["CLAUDE.md"],
                git_runner=self.git_runner,
            )
        self.assertEqual(caught.exception.code, "GUIDANCE_REFRESH_NOT_IN_TASK_DIFF")
        self.assertEqual(self.head(), dev_head)
        self.assertEqual(self.read_rel("tools/llmtool/guidance-drift.json"), before_config)

    def test_task_without_guidance_changes_leaves_record_untouched(self):
        # Even if guidance-drift.json is stale on another guidance file, a Task
        # that does not change any guidance file leaves guidance-drift.json untouched.
        stale_config = self.read_drift_config()
        stale_config["hashes"]["CLAUDE.md"] = "0" * 64
        self.write_rel("tools/llmtool/guidance-drift.json", json.dumps(stale_config, indent=2) + "\n")
        self.git(["add", "--", "tools/llmtool/guidance-drift.json"], cwd=self.root)
        self.git(["commit", "-m", "stale drift"], cwd=self.root)
        base_head = self.head()
        before_config = self.read_rel("tools/llmtool/guidance-drift.json")

        self.write_rel("allowed.txt", "task change\n")
        result = self.publish(write_paths=["allowed.txt"])

        self.assertEqual(result["head"], self.head())
        self.assertNotEqual(result["head"], base_head)
        self.assertEqual(self.read_rel("tools/llmtool/guidance-drift.json"), before_config)
        # The unapproved stale hash still fails the drift check.
        self.assertTrue(any("CLAUDE.md: HASH_STALE" in f for f in guidance_drift_check.check(self.root)))

    def test_only_changed_guidance_file_hash_is_updated(self):
        # AGENTS.md has a stale hash, Task only edits CLAUDE.md -> only CLAUDE.md updates.
        stale_config = self.read_drift_config()
        stale_config["hashes"]["AGENTS.md"] = "0" * 64
        self.write_rel("tools/llmtool/guidance-drift.json", json.dumps(stale_config, indent=2) + "\n")
        self.git(["add", "--", "tools/llmtool/guidance-drift.json"], cwd=self.root)
        self.git(["commit", "-m", "stale agents"], cwd=self.root)

        updated_claude = self.claude_text + "Only claude changed.\n"
        self.write_rel("CLAUDE.md", updated_claude)
        self.publish(write_paths=["CLAUDE.md"])

        config = self.read_drift_config()
        self.assertEqual(config["hashes"]["CLAUDE.md"], self.sha256_text(updated_claude))
        self.assertEqual(config["hashes"]["AGENTS.md"], "0" * 64)

    def test_head_mismatch_and_dirty_config_are_refused(self):
        updated_claude = self.claude_text + "Edit.\n"
        self.write_rel("CLAUDE.md", updated_claude)
        self.git(["add", "--", "CLAUDE.md"], cwd=self.root)
        self.git(["commit", "-m", "edit CLAUDE.md"], cwd=self.root)
        dev_head = self.head()

        with self.assertRaises(guidance_refresh.GuidanceRefreshError) as caught:
            guidance_refresh.refresh_guidance_hashes(
                root=self.root,
                head="f" * 40,
                write_paths=["CLAUDE.md"],
                task_commits=[(dev_head, [self.initial_head])],
                git_runner=self.git_runner,
            )
        self.assertEqual(caught.exception.code, "GUIDANCE_REFRESH_HEAD_MISMATCH")

        self.write_rel("tools/llmtool/guidance-drift.json", "{}\n")
        with self.assertRaises(guidance_refresh.GuidanceRefreshError) as dirty_caught:
            guidance_refresh.refresh_guidance_hashes(
                root=self.root,
                head=dev_head,
                write_paths=["CLAUDE.md"],
                task_commits=[(dev_head, [self.initial_head])],
                git_runner=self.git_runner,
            )
        self.assertEqual(dirty_caught.exception.code, "GUIDANCE_REFRESH_DIRTY_WORKTREE")


if __name__ == "__main__":
    unittest.main()
