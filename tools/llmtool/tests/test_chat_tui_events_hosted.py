"""Tests for GitHub-hosted remote test events in chat TUI."""

import unittest

from llmtool.chat_tui_events import task_events


class TestChatTuiEventsHosted(unittest.TestCase):
    """Test receipt events with hosted run_id and remote_test_dispatches events."""

    def test_receipt_with_valid_hosted_run_id(self):
        record = {
            "task_id": "0123456789abcdef0123456789abcdef",
            "remote_test_receipts": [
                {
                    "result": "PASS",
                    "exit_code": 0,
                    "ran_total": 12,
                    "source_sha": "a" * 40,
                    "ended_at": "2026-11-09T01:00:00Z",
                    "actions": {"run_id": 12345},
                }
            ],
        }
        events = task_events(record)
        test_events = [e for e in events if e.get("kind") == "TEST"]
        self.assertEqual(len(test_events), 1)
        self.assertEqual(
            test_events[0]["text"],
            "remote test receipt PASS, exit 0, ran 12, failed modules UNKNOWN, "
            "sha aaaaaaaaaaaa, GitHub-hosted run 12345",
        )

    def test_receipt_without_actions_or_invalid_run_id(self):
        cases = [
            None,
            "not-a-dict",
            {},
            {"run_id": 0},
            {"run_id": -1},
            {"run_id": (1 << 53) + 1},
            {"run_id": True},
            {"run_id": "12345"},
        ]
        for actions in cases:
            with self.subTest(actions=actions):
                item = {
                    "result": "PASS",
                    "exit_code": 0,
                    "ran_total": 12,
                    "source_sha": "a" * 40,
                    "ended_at": "2026-11-09T01:00:00Z",
                }
                if actions is not None:
                    item["actions"] = actions
                record = {
                    "task_id": "0123456789abcdef0123456789abcdef",
                    "remote_test_receipts": [item],
                }
                events = task_events(record)
                test_events = [e for e in events if e.get("kind") == "TEST"]
                self.assertEqual(len(test_events), 1)
                self.assertEqual(
                    test_events[0]["text"],
                    "remote test receipt PASS, exit 0, ran 12, failed modules UNKNOWN, "
                    "sha aaaaaaaaaaaa",
                )

    def test_dispatch_entry_pending(self):
        record = {
            "task_id": "0123456789abcdef0123456789abcdef",
            "remote_test_dispatches": [
                {
                    "state": "DISPATCHED",
                    "dispatched_at": "2026-11-09T01:00:00Z",
                    "sha": "b" * 40,
                }
            ],
        }
        events = task_events(record)
        test_events = [e for e in events if e.get("kind") == "TEST"]
        self.assertEqual(len(test_events), 1)
        self.assertEqual(test_events[0]["time"], "2026-11-09T01:00:00Z")
        self.assertEqual(
            test_events[0]["text"],
            "hosted remote-test dispatch DISPATCHED, run pending, sha bbbbbbbbbbbb",
        )

    def test_dispatch_entry_with_run_id(self):
        record = {
            "task_id": "0123456789abcdef0123456789abcdef",
            "remote_test_dispatches": [
                {
                    "state": "RUN_FOUND",
                    "dispatched_at": "2026-11-09T01:05:00Z",
                    "sha": "c" * 40,
                    "run_id": 99999,
                }
            ],
        }
        events = task_events(record)
        test_events = [e for e in events if e.get("kind") == "TEST"]
        self.assertEqual(len(test_events), 1)
        self.assertEqual(test_events[0]["time"], "2026-11-09T01:05:00Z")
        self.assertEqual(
            test_events[0]["text"],
            "hosted remote-test dispatch RUN_FOUND, run 99999, sha cccccccccccc",
        )

    def test_dispatch_entry_values_fallback(self):
        record = {
            "task_id": "0123456789abcdef0123456789abcdef",
            "remote_test_dispatches": [
                {
                    "dispatched_at": "2026-11-09T01:00:00Z",
                }
            ],
        }
        events = task_events(record)
        test_events = [e for e in events if e.get("kind") == "TEST"]
        self.assertEqual(len(test_events), 1)
        self.assertEqual(
            test_events[0]["text"],
            "hosted remote-test dispatch UNKNOWN, run pending, sha UNKNOWN",
        )

    def test_dispatch_entry_with_six_fractional_digits(self):
        record = {
            "task_id": "0123456789abcdef0123456789abcdef",
            "remote_test_dispatches": [
                {
                    "state": "DISPATCHED",
                    "dispatched_at": "2026-11-09T01:00:00.123456Z",
                    "sha": "b" * 40,
                }
            ],
        }
        events = task_events(record)
        test_events = [e for e in events if e.get("kind") == "TEST"]
        self.assertEqual(len(test_events), 1)
        self.assertEqual(test_events[0]["time"], "2026-11-09T01:00:00.123456Z")
        self.assertEqual(
            test_events[0]["text"],
            "hosted remote-test dispatch DISPATCHED, run pending, sha bbbbbbbbbbbb",
        )

    def test_dispatch_entry_rejected_fraction_lengths(self):
        rejected = [
            "2026-11-09T01:00:00.1Z",
            "2026-11-09T01:00:00.1234567Z",
            "not-a-date",
            12345,
            None,
        ]
        for bad_at in rejected:
            with self.subTest(bad_at=bad_at):
                item = {
                    "state": "DISPATCHED",
                    "sha": "b" * 40,
                }
                if bad_at is not None:
                    item["dispatched_at"] = bad_at
                record = {
                    "task_id": "0123456789abcdef0123456789abcdef",
                    "remote_test_dispatches": [item],
                }
                events = task_events(record)
                test_events = [e for e in events if e.get("kind") == "TEST"]
                self.assertEqual(len(test_events), 0)


class TestChatTuiEventsWorkerId(unittest.TestCase):
    """#1792: dispatch audit worker ids read through stored_worker_id."""

    def _dispatch_text(self, worker_key):
        item = {
            "at": "2026-11-09T01:00:00Z",
            "action": "DISPATCH",
            "decision": "ALLOW",
            "worker_key": worker_key,
            "reason_code": "WORKER_DISPATCH_ALLOWED",
        }
        record = {
            "task_id": "0123456789abcdef0123456789abcdef",
            "dispatch_audit": [item],
        }
        events = [e for e in task_events(record) if e.get("kind") == "DISPATCH"]
        self.assertEqual(len(events), 1)
        # Display only: the stored record is never rewritten.
        self.assertEqual(record["dispatch_audit"][0]["worker_key"], worker_key)
        return events[0]["text"]

    def test_retired_alias_renders_as_current_id(self):
        text = self._dispatch_text("company-gw-agent")
        self.assertIn(" for gw-agent (", text)
        self.assertNotIn("company-gw-agent", text)

    def test_current_and_unknown_ids_unchanged(self):
        for worker_key in ("gw-agent", "claude-developer", "company-gw",
                           "some-future-worker"):
            with self.subTest(worker_key=worker_key):
                text = self._dispatch_text(worker_key)
                self.assertIn(" for " + worker_key + " (", text)

    def test_missing_worker_key_matches_previous_rendering(self):
        record = {"dispatch_audit": [{"at": "2026-11-09T01:00:00Z"}]}
        events = [e for e in task_events(record) if e.get("kind") == "DISPATCH"]
        self.assertEqual(len(events), 1)
        self.assertNotIn("gw-agent", events[0]["text"])


if __name__ == "__main__":
    unittest.main()
