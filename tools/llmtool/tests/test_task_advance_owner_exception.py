"""Tests for task advance owner exception handling under issue #1253."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from argparse import Namespace
from contextlib import redirect_stdout

from llmtool import owner_exception, task_advance, task_submission_cli
from llmtool.task_advance import advance
from llmtool.task_submission_cli import run_task_advance


class FakeStore:
    def __init__(self, record):
        self.record = dict(record)
        self.compare_and_updates = []
        self.updates = []

    def get(self, task_id):
        if task_id != self.record.get("task_id"):
            raise KeyError(task_id)
        return dict(self.record)

    def compare_and_update(self, task_id, expected=None, **fields):
        self.compare_and_updates.append((expected, fields))
        self.record.update(fields)
        return dict(self.record)

    def update(self, task_id, **fields):
        self.updates.append(fields)
        self.record.update(fields)
        return dict(self.record)


class FakeGhRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, root, args):
        self.calls.append((root, list(args)))
        return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")


def _base_record(task_id, head):
    return {
        "task_id": task_id,
        "run_id": "run-dummy-001",
        "record_sha256": "rec-dummy-sha256",
        "completion": {
            "executor_task_id": "exec-dummy-001",
            "evidence": {"common_dir": "/tmp/common"},
        },
        "completion_events": [],
        "review_repair_claims": [],
        "review_dispatch_claim": [],
    }


def _base_d6(head):
    return {
        "root": "/tmp/fake-repo",
        "common_dir": "/tmp/common",
        "task_id": "exec-dummy-001",
        "branch": "fake-branch",
        "result_head": head,
        "recorded_result_head": head,
        "registered_result_head": head,
        "dirty": False,
        "state": "CLEAN",
    }


def _make_github_get(head, number, *, check_runs=None, files=None):
    if files is None:
        files = [{"filename": "docs/readme.md"}]
    if check_runs is None:
        check_runs = [
            {
                "id": 1,
                "name": "check (docs)",
                "head_sha": head,
                "status": "completed",
                "conclusion": "success",
            }
        ]

    main_sha = "b" * 40

    def github_get(path):
        if path == f"pulls/{number}":
            return {
                "number": number,
                "state": "open",
                "merged": False,
                "mergeable": True,
                "head": {"sha": head},
            }
        if path == f"commits/{head}/check-runs?per_page=100":
            return {
                "total_count": len(check_runs),
                "check_runs": check_runs,
            }
        if path == f"pulls/{number}/files?per_page=100":
            return files
        if path == "branches/main":
            return {"commit": {"sha": main_sha}}
        if path == f"compare/{main_sha}...{head}":
            return {"status": "identical"}
        raise ValueError(f"Unexpected github_get path: {path}")

    return github_get


class TestTaskAdvanceOwnerException(unittest.TestCase):
    def test_no_merge_refusal_cli_and_advance(self):
        task_id = "task-no-merge"
        url = "https://github.com/example/repo/issues/1253#issuecomment-6788268483"
        record = _base_record(task_id, "a" * 40)
        store = FakeStore(record)

        # 1. advance() refuses without merge before any read
        adv_res = advance(task_id=task_id, store=store, merge=False, owner_exception=url)
        self.assertEqual(adv_res["step"], "REFUSED")
        self.assertEqual(adv_res["codes"], ["OWNER_EXCEPTION_REQUIRES_MERGE"])

        # 2. CLI run_task_advance refuses without --merge
        cli_args = Namespace(
            task_id=task_id,
            merge=False,
            owner_exception=url,
            pr=42,
            integration_pr=None,
            reviewer_session_done=False,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = run_task_advance(cli_args, store=store)
        self.assertEqual(ret, 1)
        self.assertIn("OWNER_EXCEPTION_REQUIRES_MERGE", buf.getvalue())

    def test_verify_code_refusal(self):
        task_id = "task-verify-refusal"
        head = "a" * 40
        number = 42
        url = "https://github.com/example/repo/issues/1253#issuecomment-6788268483"
        record = _base_record(task_id, head)
        store = FakeStore(record)

        with patch.object(task_advance, "_d6_snapshot", return_value=_base_d6(head)), \
             patch.object(owner_exception, "verify", return_value=("OWNER_EXCEPTION_SCOPE", None)):
            gh_runner = FakeGhRunner()
            github_get = _make_github_get(head, number)

            res = advance(
                task_id=task_id,
                store=store,
                pr_number=number,
                merge=True,
                owner_exception=url,
                github_get=github_get,
                merge_gh_runner=gh_runner,
            )

            self.assertEqual(res["step"], "MERGE_REFUSED")
            self.assertEqual(res["codes"], ["OWNER_EXCEPTION_SCOPE"])
            self.assertFalse(any(c[1][:2] == ["pr", "merge"] for c in gh_runner.calls))

    def test_success_merges_and_records_review_exception_without_pass(self):
        task_id = "task-success-merge"
        head = "a" * 40
        number = 42
        url = "https://github.com/example/repo/issues/1253#issuecomment-6788268483"
        record = _base_record(task_id, head)
        store = FakeStore(record)

        fake_evidence = {"exception_slot": "S2a", "verified_files": ["tools/foo.py"]}
        with patch.object(task_advance, "_d6_snapshot", return_value=_base_d6(head)), \
             patch.object(owner_exception, "verify", return_value=(None, fake_evidence)), \
             patch.object(task_advance.mac_full_suite, "request_full_suite",
                          return_value=task_advance.mac_full_suite.MAC_FULL_SUITE_DISPATCHED):
            gh_runner = FakeGhRunner()
            github_get = _make_github_get(head, number)

            res = advance(
                task_id=task_id,
                store=store,
                pr_number=number,
                merge=True,
                owner_exception=url,
                github_get=github_get,
                merge_gh_runner=gh_runner,
            )

            self.assertEqual(res["step"], "MERGED")
            self.assertEqual(res["codes"], [])

            expected_exception = {
                "type": owner_exception.EXCEPTION_TYPE,
                "url": url,
                "evidence": fake_evidence,
            }
            self.assertEqual(res["result"]["review_exception"], expected_exception)

            # Record in store gets review_exception
            stored_record = store.get(task_id)
            self.assertEqual(stored_record.get("review_exception"), expected_exception)

            # PR merge was called
            merge_calls = [c for c in gh_runner.calls if c[1][:2] == ["pr", "merge"]]
            self.assertEqual(len(merge_calls), 1)
            self.assertEqual(merge_calls[0][1], [
                "pr",
                "merge",
                str(number),
                "--squash",
                "--match-head-commit",
                head,
            ])

            # Merge provenance comment names OWNER_EXCEPTION_DETERMINISTIC_REDACTION
            comment_calls = [c for c in gh_runner.calls if c[1][:2] == ["pr", "comment"]]
            self.assertEqual(len(comment_calls), 1)
            body_idx = comment_calls[0][1].index("--body") + 1
            comment_body = comment_calls[0][1][body_idx]
            self.assertIn("OWNER_EXCEPTION_DETERMINISTIC_REDACTION", comment_body)

            # No REVIEW_PASS event or verdict was written
            self.assertFalse(any(
                e.get("source") == "REVIEWER" or e.get("outcome") == "REVIEW_PASS"
                for e in stored_record.get("completion_events", [])
            ))

    def test_failed_and_pending_ci_refuse(self):
        task_id = "task-ci-refusal"
        head = "a" * 40
        number = 42
        url = "https://github.com/example/repo/issues/1253#issuecomment-6788268483"
        fake_evidence = {"slot": "S2a"}

        with patch.object(task_advance, "_d6_snapshot", return_value=_base_d6(head)), \
             patch.object(owner_exception, "verify", return_value=(None, fake_evidence)):
            # 1. Failed CI
            failed_checks = [
                {
                    "id": 1,
                    "name": "check (docs)",
                    "head_sha": head,
                    "status": "completed",
                    "conclusion": "failure",
                }
            ]
            store_fail = FakeStore(_base_record(task_id, head))
            gh_runner_fail = FakeGhRunner()
            github_get_fail = _make_github_get(head, number, check_runs=failed_checks)

            res_fail = advance(
                task_id=task_id,
                store=store_fail,
                pr_number=number,
                merge=True,
                owner_exception=url,
                github_get=github_get_fail,
                merge_gh_runner=gh_runner_fail,
            )
            self.assertEqual(res_fail["step"], "MERGE_REFUSED")
            self.assertIn("MERGE_CHECKS_NOT_GREEN", res_fail["codes"])
            self.assertFalse(any(c[1][:2] == ["pr", "merge"] for c in gh_runner_fail.calls))

            # 2. Pending macOS CI on code PR
            code_files = [{"filename": "tools/llmtool/llmtool/task_advance.py"}]
            pending_checks = [
                {
                    "id": 1,
                    "name": "check (Linux 3.14)",
                    "head_sha": head,
                    "status": "completed",
                    "conclusion": "success",
                },
                {
                    "id": 2,
                    "name": "check (macOS 3.13)",
                    "head_sha": head,
                    "status": "in_progress",
                    "conclusion": None,
                },
            ]
            store_pending = FakeStore(_base_record(task_id, head))
            gh_runner_pending = FakeGhRunner()
            github_get_pending = _make_github_get(
                head, number, check_runs=pending_checks, files=code_files
            )

            res_pending = advance(
                task_id=task_id,
                store=store_pending,
                pr_number=number,
                merge=True,
                owner_exception=url,
                github_get=github_get_pending,
                merge_gh_runner=gh_runner_pending,
            )
            self.assertEqual(res_pending["step"], "MERGE_REFUSED")
            self.assertIn("MERGE_CHECKS_NOT_GREEN", res_pending["codes"])
            self.assertIs(res_pending["result"].get("macos_check_pending"), True)
            self.assertFalse(any(c[1][:2] == ["pr", "merge"] for c in gh_runner_pending.calls))

    def test_ordinary_merge_path_unchanged(self):
        task_id = "task-ordinary-merge"
        head = "a" * 40
        number = 42

        with patch.object(task_advance, "_d6_snapshot", return_value=_base_d6(head)), \
             patch.object(task_advance.mac_full_suite, "request_full_suite",
                          return_value=task_advance.mac_full_suite.MAC_FULL_SUITE_DISPATCHED):
            # 1. Ordinary merge without PASS refuses with MERGE_REVIEW_NOT_PASS
            record_no_pass = _base_record(task_id, head)
            store_no_pass = FakeStore(record_no_pass)
            gh_runner_no_pass = FakeGhRunner()
            github_get = _make_github_get(head, number)

            res_no_pass = advance(
                task_id=task_id,
                store=store_no_pass,
                pr_number=number,
                merge=True,
                owner_exception=None,
                github_get=github_get,
                merge_gh_runner=gh_runner_no_pass,
            )
            self.assertEqual(res_no_pass["step"], "MERGE_REFUSED")
            self.assertIn("MERGE_REVIEW_NOT_PASS", res_no_pass["codes"])
            self.assertFalse(any(c[1][:2] == ["pr", "merge"] for c in gh_runner_no_pass.calls))

            # 2. Ordinary merge with PASS succeeds without review_exception
            record_with_pass = _base_record(task_id, head)
            record_with_pass["review_dispatch_claim"] = [
                {
                    "pr": number,
                    "head_sha": head,
                    "version": 1,
                    "state": "COMPLETED",
                    "review_result": {"verdict": "PASS"},
                }
            ]
            record_with_pass["completion_events"] = [
                {
                    "source": "REVIEWER",
                    "source_sha": head,
                    "version": 1,
                    "outcome": "REVIEW_PASS",
                }
            ]
            store_pass = FakeStore(record_with_pass)
            gh_runner_pass = FakeGhRunner()

            res_pass = advance(
                task_id=task_id,
                store=store_pass,
                pr_number=number,
                merge=True,
                owner_exception=None,
                github_get=github_get,
                merge_gh_runner=gh_runner_pass,
            )
            self.assertEqual(res_pass["step"], "MERGED")
            self.assertEqual(res_pass["codes"], [])
            self.assertNotIn("review_exception", res_pass["result"])
            self.assertNotIn("review_exception", store_pass.get(task_id))

            # Provenance comment does not name OWNER_EXCEPTION_DETERMINISTIC_REDACTION
            comment_calls = [c for c in gh_runner_pass.calls if c[1][:2] == ["pr", "comment"]]
            self.assertEqual(len(comment_calls), 1)
            body_idx = comment_calls[0][1].index("--body") + 1
            comment_body = comment_calls[0][1][body_idx]
            self.assertNotIn("OWNER_EXCEPTION_DETERMINISTIC_REDACTION", comment_body)
            self.assertIn("review_version=1", comment_body)

    def test_real_default_replacement_map_regression(self):
        dummy_entries = [{"value": "sample-dummy-val-1", "placeholder": "sample-dummy-plc-1"}]
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tf:
            json.dump(dummy_entries, tf)
            temp_path = tf.name

        try:
            os.chmod(temp_path, 0o600)
            with patch.dict(os.environ, {"LLMTOOL_REDACT_MAP": temp_path}):
                result = owner_exception.default_replacement_map()
            self.assertEqual(result, [("sample-dummy-val-1", "sample-dummy-plc-1")])
            self.assertIn("_llmtool_redact_known_values", sys.modules)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


if __name__ == "__main__":
    unittest.main()
