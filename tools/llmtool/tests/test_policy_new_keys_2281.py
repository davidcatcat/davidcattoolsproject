"""#2281 rows 9, 19 and 20: optional integer policy keys and their accessors.

A1: each of the five keys is accepted by the loader (a temporary policy file)
    and by the drift check's policy.json schema (an in-memory document) when it
    is an integer of at least 1; zero, a negative value, a bool or a string is
    refused (loader POLICY_INVALID, drift integer-range finding).
A2: each accessor returns the key value when present and its default when
    absent; the defaults are 180, 540, 2000, 40 and 1800.
A3: every existing key, accessor and check behaves exactly as before; an
    unknown key is still refused by both.
"""

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

from llmtool import repair_policy as rp
from llmtool.errors import LLMToolError

# The drift check is a script, not an installed module; load it by path.
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "guidance_drift_check.py"
_SPEC = importlib.util.spec_from_file_location("guidance_drift_check_newkeys_2281", _SCRIPT)
drift = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(drift)

# The required policy keys, before any optional key (#2367 values).
BASE = {"schema_version": 1, "repair_max": 5, "parent_debug_max": 1,
        "group_debug_max": 3, "child_max": 3, "replan_max": 1,
        "test_fail_max": 5, "review_fail_max": 6,
        "waiting_notify_after_hours": 24}

# #2281 rows 9, 19, 20: key -> (accessor, default). The defaults equal the
# current hard-coded values.
NEW_KEYS = {
    "remote_test_module_timeout_seconds": (rp.remote_test_module_timeout_seconds, 180),
    "remote_test_total_timeout_seconds": (rp.remote_test_total_timeout_seconds, 540),
    "reviewer_excerpt_max_chars": (rp.reviewer_excerpt_max_chars, 2000),
    "ci_excerpt_max_lines": (rp.ci_excerpt_max_lines, 40),
    "monitor_ec2_busy_window_seconds": (rp.monitor_ec2_busy_window_seconds, 1800),
}

INVALID = (0, -1, True, "5")


class PolicyNewKeysTests(unittest.TestCase):
    def _write(self, policy):
        handle, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(handle, "w") as stream:
            stream.write(json.dumps(policy))
        self.addCleanup(os.unlink, path)
        return path

    def _refuses(self, policy):
        with self.assertRaises(LLMToolError) as caught:
            rp.load_policy(self._write(policy))
        self.assertEqual(str(caught.exception), "POLICY_INVALID")

    def _drift(self, policy):
        return drift.RULE_SCHEMAS["policy.json"].errors(policy, "$")

    def _range_finding(self, key):
        return f"$.{key} must be an integer >= 1"

    # A1
    def test_keys_are_optional_positive_keys(self):
        self.assertTrue(set(NEW_KEYS) <= set(rp.ALL_OPTIONAL_POSITIVE_POLICY_KEYS))
        for key in NEW_KEYS:
            with self.subTest(key=key):
                self.assertNotIn(key, rp.POLICY_KEYS)
                self.assertNotIn(key, rp.OPTIONAL_POLICY_LIMITS)

    def test_loader_accepts_positive_integers(self):
        for key, (_accessor, default) in NEW_KEYS.items():
            for value in (1, default, 10 ** 9):
                with self.subTest(key=key, value=value):
                    self.assertEqual(
                        rp.load_policy(self._write(dict(BASE, **{key: value}))),
                        dict(BASE, **{key: value}))

    def test_drift_accepts_positive_integers(self):
        for key, (_accessor, default) in NEW_KEYS.items():
            for value in (1, default, 10 ** 9):
                with self.subTest(key=key, value=value):
                    self.assertEqual(self._drift(dict(BASE, **{key: value})), [])

    def test_loader_refuses_invalid_values(self):
        for key in NEW_KEYS:
            for value in INVALID:
                with self.subTest(key=key, value=value):
                    self._refuses(dict(BASE, **{key: value}))

    def test_drift_refuses_invalid_values(self):
        for key in NEW_KEYS:
            for value in INVALID:
                with self.subTest(key=key, value=value):
                    self.assertEqual(self._drift(dict(BASE, **{key: value})),
                                     [self._range_finding(key)])

    # A2
    def test_accessors_return_default_when_absent(self):
        path = self._write(BASE)
        for key, (accessor, default) in NEW_KEYS.items():
            with self.subTest(key=key):
                self.assertGreaterEqual(default, 1)
                self.assertEqual(accessor(path), default)
                # The repository policy.json omits the key too.
                self.assertEqual(accessor(), default)
                # Same shape as local_test_timeout_seconds: the caller may pass
                # another default.
                self.assertEqual(accessor(path, 17), 17)

    def test_accessors_return_present_value(self):
        for key, (accessor, _default) in NEW_KEYS.items():
            with self.subTest(key=key):
                self.assertEqual(accessor(self._write(dict(BASE, **{key: 7}))), 7)

    # A3
    def test_existing_keys_and_rules_are_unchanged(self):
        self.assertEqual(rp.POLICY_KEYS, (
            "repair_max", "parent_debug_max", "group_debug_max", "child_max",
            "replan_max", "test_fail_max", "review_fail_max",
            "waiting_notify_after_hours"))
        self.assertEqual(rp.OPTIONAL_POLICY_LIMITS, {"timeout_continuation_max": (0, 3)})
        self.assertEqual(
            rp.load_policy(self._write(dict(BASE, timeout_continuation_max=3))),
            dict(BASE, timeout_continuation_max=3))
        self._refuses(dict(BASE, timeout_continuation_max=4))
        self._refuses(dict(BASE, repair_max=-1))
        self._refuses({key: value for key, value in BASE.items() if key != "repair_max"})

    def test_existing_accessor_is_unchanged(self):
        self.assertEqual(rp.local_test_timeout_seconds(self._write(BASE)),
                         rp.LOCAL_TEST_TIMEOUT_SECONDS)
        self.assertEqual(rp.local_test_timeout_seconds(self._write(BASE), 90), 90)

    def test_unknown_key_is_still_refused_by_both(self):
        self._refuses(dict(BASE, unknown_limit=1))
        findings = self._drift(dict(BASE, unknown_limit=1))
        self.assertEqual(len(findings), 1)
        self.assertIn('unknown key "unknown_limit"', findings[0])

    def test_repository_policy_is_not_edited(self):
        policy = rp.load_policy()
        for key in NEW_KEYS:
            with self.subTest(key=key):
                self.assertNotIn(key, policy)


if __name__ == "__main__":
    unittest.main()
