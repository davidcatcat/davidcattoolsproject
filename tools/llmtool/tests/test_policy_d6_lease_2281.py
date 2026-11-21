"""#2281 L7 (limit row 17): the D6 writer lease length is one policy.json key.

d6_worktree_lifecycle.py no longer keeps the 24-hour lease as a constant; it
reads d6_writer_lease_hours through llmtool.repair_policy.load_policy. The key
is validated like the L0b keys (an integer >= 1, no upper validation bound),
an invalid or missing value refuses POLICY_INVALID, and the expiry written for
the repository policy is still 24 hours after the writer's activity.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from llmtool import repair_policy as rp
from llmtool.errors import LLMToolError

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import d6_worktree_lifecycle as lifecycle  # noqa: E402

KEY = "d6_writer_lease_hours"
DAY = 24 * 60 * 60
# A valid policy without the lease key (the limits after #2367).
BASE = {"schema_version": 1, "repair_max": 5, "parent_debug_max": 1,
        "group_debug_max": 3, "child_max": 3, "replan_max": 1,
        "test_fail_max": 5, "review_fail_max": 6,
        "waiting_notify_after_hours": 24}
INVALID_VALUES = (0, -1, -24, "24", True, False, 2.5, 24.0, None, [24], {"hours": 24})


class _PolicyFiles(unittest.TestCase):
    def _write(self, value):
        handle, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(handle, "w") as stream:
            stream.write(value if type(value) is str else json.dumps(value))
        self.addCleanup(os.unlink, path)
        return path

    def _lease_code(self, path):
        with self.assertRaises(lifecycle.LifecycleError) as caught:
            lifecycle._lease_seconds(path)
        return caught.exception.code


class PolicyKeyLoadingTests(_PolicyFiles):
    def test_repository_policy_sets_the_lease_to_24_hours(self):
        self.assertEqual(rp.load_policy()[KEY], 24)
        self.assertIn(KEY, rp.OPTIONAL_POSITIVE_POLICY_KEYS)
        self.assertNotIn(KEY, rp.POLICY_KEYS)

    def test_positive_integers_load_without_an_upper_bound(self):
        for value in (1, 24, 10 ** 9):
            with self.subTest(value=value):
                self.assertEqual(rp.load_policy(self._write(dict(BASE, **{KEY: value})))[KEY],
                                 value)

    def test_absent_key_still_loads(self):
        # Optional like the L0b keys: other policy readers are unaffected.
        self.assertNotIn(KEY, rp.load_policy(self._write(BASE)))

    def test_invalid_values_refuse_policy_invalid(self):
        for value in INVALID_VALUES:
            with self.subTest(value=value):
                with self.assertRaises(LLMToolError) as caught:
                    rp.load_policy(self._write(dict(BASE, **{KEY: value})))
                self.assertEqual(str(caught.exception), "POLICY_INVALID")


class LifecycleLeaseTests(_PolicyFiles):
    def test_lifecycle_reads_the_policy_loader(self):
        module, error = lifecycle._repair_policy()
        self.assertIs(module, rp)
        self.assertIs(error, LLMToolError)

    def test_repository_lease_is_24_hours(self):
        self.assertEqual(lifecycle._lease_seconds(), DAY)
        self.assertFalse(hasattr(lifecycle, "LEASE_SECONDS"))

    def test_hours_are_converted_to_seconds(self):
        for hours in (1, 24, 48):
            with self.subTest(hours=hours):
                path = self._write(dict(BASE, **{KEY: hours}))
                self.assertEqual(lifecycle._lease_seconds(path), hours * 60 * 60)

    def test_expiry_is_unchanged_at_24_hours(self):
        start = 1_000_000
        expected = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(start + DAY))
        self.assertEqual(lifecycle._lease_expiry(start), expected)
        self.assertEqual(lifecycle._lease_expiry(start, seconds=DAY), expected)
        record = {"state": "ACTIVE", "lease_expires_at": lifecycle._lease_expiry(start)}
        self.assertEqual(lifecycle.lease_status(record, now=start + DAY - 1), "LEASE_ACTIVE")
        self.assertEqual(lifecycle.lease_status(record, now=start + DAY), "LEASE_EXPIRED")

    def test_expiry_follows_the_policy_value(self):
        path = self._write(dict(BASE, **{KEY: 1}))
        with patch.object(rp, "policy_path", return_value=Path(path)):
            self.assertEqual(lifecycle._lease_seconds(), 60 * 60)
            self.assertEqual(lifecycle._lease_expiry(1_000_000),
                             time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                           time.gmtime(1_000_000 + 60 * 60)))

    def test_invalid_values_refuse_policy_invalid(self):
        for value in INVALID_VALUES:
            with self.subTest(value=value):
                self.assertEqual(self._lease_code(self._write(dict(BASE, **{KEY: value}))),
                                 "POLICY_INVALID")

    def test_missing_key_or_unreadable_policy_refuses_with_no_fallback(self):
        parent = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, parent)
        missing = os.path.join(parent, "policy.json")
        for path in (self._write(BASE), self._write("not json"), missing):
            with self.subTest(path=os.path.basename(path)):
                self.assertEqual(self._lease_code(path), "POLICY_INVALID")

    def test_length_beyond_the_lease_time_format_refuses(self):
        # The loader has no upper bound; an expiry that the lease time format
        # cannot hold would write an unreadable lease, so it refuses.
        for hours in (10 ** 9, 10 ** 18):
            with self.subTest(hours=hours):
                self.assertEqual(self._lease_code(self._write(dict(BASE, **{KEY: hours}))),
                                 "POLICY_INVALID")


class LifecycleRefusesBeforeChangeTests(_PolicyFiles):
    """A bad policy stops create and record-result before any Git or file access."""

    def setUp(self):
        bad = self._write(dict(BASE, **{KEY: 0}))
        parent = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, parent)
        self.missing_root = os.path.join(parent, "absent-canonical-root")
        patchers = (
            patch.object(rp, "policy_path", return_value=Path(bad)),
            patch.object(lifecycle, "_load_trust",
                         return_value={"tasks_root": self.missing_root}),
            patch.object(lifecycle.subprocess, "run",
                         side_effect=AssertionError("no Git before the policy check")),
        )
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_create_refuses_policy_invalid_first(self):
        with self.assertRaises(lifecycle.LifecycleError) as caught:
            lifecycle.create_worktree(
                canonical_root=self.missing_root, task_id="l7-policy-fixture",
                agent_id="fixture-agent", session_ref="fixture session",
                lease_ref="fixture lease", branch="test/l7-policy-fixture",
                base_sha="0" * 40)
        self.assertEqual(caught.exception.code, "POLICY_INVALID")
        self.assertFalse(os.path.exists(self.missing_root))

    def test_record_result_refuses_policy_invalid_first(self):
        with self.assertRaises(lifecycle.LifecycleError) as caught:
            lifecycle.record_result(task_id="l7-policy-fixture",
                                    canonical_root=self.missing_root)
        self.assertEqual(caught.exception.code, "POLICY_INVALID")


if __name__ == "__main__":
    unittest.main()
