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


if __name__ == "__main__":
    unittest.main()
