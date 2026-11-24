"""#2281: the guidance drift check accepts every optional policy limit key.

A1: each of the eleven optional limit keys that repair_policy.py accepts, set
    to its loader default, gives no drift finding.
A2: zero, a negative value, a bool or a string for each of those keys gives the
    existing integer-range finding.
A3: an unknown key is still refused; the existing keys and their rules are
    unchanged; the repository policy.json is not edited.

The drift check runs on a temporary policy document, so the repository file and
the existing test_guidance_drift_check.py module stay untouched.
"""

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from llmtool import repair_policy as rp

# The drift check is a script, not an installed module; load it by path.
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "guidance_drift_check.py"
_SPEC = importlib.util.spec_from_file_location("guidance_drift_check_2281", _SCRIPT)
drift = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(drift)

POLICY_LABEL = "tools/llmtool/policy.json"

# The required policy keys, before any optional key (#2367 values).
BASE = {"schema_version": 1, "repair_max": 5, "parent_debug_max": 1,
        "group_debug_max": 3, "child_max": 3, "replan_max": 1,
        "test_fail_max": 5, "review_fail_max": 6,
        "waiting_notify_after_hours": 24}

# The eleven optional keys this Task adds to the drift schema, with the loader
# default each one keeps when absent (repair_policy constants).
OPTIONAL_DEFAULTS = {
    "review_packet_limit_bytes": rp.REVIEW_PACKET_LIMIT_BYTES,
    "review_source_limit_bytes": rp.REVIEW_SOURCE_LIMIT_BYTES,
    "claude_review_envelope_limit_bytes": rp.CLAUDE_REVIEW_ENVELOPE_LIMIT_BYTES,
    "start_failures_max": rp.START_FAILURES_MAX,
    "start_retries_max": rp.START_RETRIES_MAX,
    "committed_result_carries_max": rp.COMMITTED_RESULT_CARRIES_MAX,
    "developer_assignment_wait_seconds": rp.DEVELOPER_ASSIGNMENT_WAIT_SECONDS,
    "developer_assignment_expiry_seconds": rp.DEVELOPER_ASSIGNMENT_EXPIRY_SECONDS,
    "ai_debug_excerpt_max_chars": rp.AI_DEBUG_EXCERPT_MAX_CHARS,
    "gw_agent_max_turns": rp.GW_AGENT_MAX_TURNS,
    "local_test_timeout_seconds": rp.LOCAL_TEST_TIMEOUT_SECONDS,
}


class GuidanceDriftOptionalKeysTests(unittest.TestCase):
    def _write_policy(self, policy):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        rule_dir = root / "tools" / "llmtool"
        rule_dir.mkdir(parents=True)
        (rule_dir / "policy.json").write_text(json.dumps(policy), encoding="utf-8")
        return root

    def _findings(self, policy):
        return drift.check_rule_files(self._write_policy(policy))

    def _range_finding(self, key):
        return f"{POLICY_LABEL}: RULE_SCHEMA $.{key} must be an integer >= 1"

    # A1
    def test_loader_defaults_give_no_drift_finding(self):
        self.assertEqual(set(OPTIONAL_DEFAULTS),
                         set(rp.OPTIONAL_POSITIVE_POLICY_KEYS) - {
                             "spec_preflight_large_file_lines",
                             "merge_train_members_max",
                             "merge_train_files_per_pr_max",
                             "revert_scope_prs_max",
                             "d6_writer_lease_hours",
                             "route_failure_pause_after",
                         })
        for key, default in OPTIONAL_DEFAULTS.items():
            with self.subTest(key=key):
                self.assertGreaterEqual(default, 1)
                self.assertEqual(self._findings(dict(BASE, **{key: default})), [])

    # A2
    def test_invalid_values_give_the_integer_range_finding(self):
        for key in OPTIONAL_DEFAULTS:
            for value in (0, -1, True, "5"):
                with self.subTest(key=key, value=value):
                    self.assertEqual(
                        self._findings(dict(BASE, **{key: value})),
                        [self._range_finding(key)])

    # A3
    def test_unknown_key_is_still_refused(self):
        findings = self._findings(dict(BASE, unknown_limit=1))
        self.assertEqual(len(findings), 1)
        self.assertIn("unknown key", findings[0])
        self.assertTrue(findings[0].startswith(f"{POLICY_LABEL}: RULE_SCHEMA"))

    def test_existing_keys_and_rules_are_unchanged(self):
        # timeout_continuation_max keeps its 0..3 range.
        self.assertEqual(self._findings(dict(BASE, timeout_continuation_max=0)), [])
        self.assertEqual(self._findings(dict(BASE, timeout_continuation_max=3)), [])
        self.assertEqual(
            self._findings(dict(BASE, timeout_continuation_max=4)),
            [f"{POLICY_LABEL}: RULE_SCHEMA $.timeout_continuation_max "
             "must be an integer >= 0 and <= 3"])
        self.assertEqual(
            self._findings(dict(BASE, timeout_continuation_max=-1)),
            [f"{POLICY_LABEL}: RULE_SCHEMA $.timeout_continuation_max "
             "must be an integer >= 0 and <= 3"])
        # A required integer key keeps its >= 0 rule.
        self.assertEqual(
            self._findings(dict(BASE, repair_max=-1)),
            [f"{POLICY_LABEL}: RULE_SCHEMA $.repair_max must be an integer >= 0"])
        # A missing required key is still reported.
        broken = {key: value for key, value in BASE.items() if key != "repair_max"}
        self.assertEqual(
            self._findings(broken),
            [f'{POLICY_LABEL}: RULE_SCHEMA $ is missing key "repair_max"'])

    def test_repository_policy_is_not_edited(self):
        repository = Path(__file__).resolve().parents[1] / "policy.json"
        policy = json.loads(repository.read_text(encoding="utf-8"))
        for key in OPTIONAL_DEFAULTS:
            with self.subTest(key=key):
                self.assertNotIn(key, policy)


if __name__ == "__main__":
    unittest.main()
