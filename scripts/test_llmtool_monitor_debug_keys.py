"""#1502 WP-B: the monitor accepts the optional AI Debug keys exactly as the gate does."""

import json
import os
import sys
import unittest

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import llmtool_monitor as mon

EVIDENCE = "https://github.com/davidcatcat/davidcattoolsproject/issues/289"
GW_AGENT = {"dispatch": "PAUSED", "decided_by": "PM", "decided_at": "2026-11-03",
            "reason": "Paused pending the Owner decision tracked by #120",
            "evidence": "https://github.com/davidcatcat/davidcattoolsproject/issues/120"}


def raw(keys):
    workers = {key: {"dispatch": "ALLOWED", "decided_by": "Owner", "decided_at": "2026-11-12",
                     "reason": "test", "evidence": EVIDENCE} for key in sorted(keys)}
    workers["company-gw-agent"] = dict(GW_AGENT)
    return json.dumps({"schema_version": 1, "workers": workers}).encode("utf-8")


class DebugKeyTests(unittest.TestCase):
    def test_debug_keys_are_the_gate_keys(self):
        self.assertEqual(mon.DISPATCH_DEBUG_WORKERS, {"codex-debug", "claude-debug"})
        self.assertFalse(mon.DISPATCH_DEBUG_WORKERS & mon.DISPATCH_WORKERS)

    def test_file_without_debug_keys_is_still_valid(self):
        switches, problem = mon.worker_dispatch(raw(mon.DISPATCH_WORKERS))
        self.assertEqual(problem, "")
        self.assertEqual(set(switches), mon.DISPATCH_WORKERS)

    def test_file_with_both_debug_keys_shows_them(self):
        switches, problem = mon.worker_dispatch(raw(mon.DISPATCH_WORKERS | mon.DISPATCH_DEBUG_WORKERS))
        self.assertEqual(problem, "")
        self.assertEqual(switches["codex-debug"], ("ALLOWED", "Owner", "11-12"))
        self.assertEqual(switches["claude-debug"], ("ALLOWED", "Owner", "11-12"))

    def test_one_debug_key_alone_or_an_unknown_key_is_rejected(self):
        for extra in ({"codex-debug"}, {"claude-debug"}, {"gemini-debug"}):
            with self.subTest(extra=extra):
                self.assertEqual(mon.worker_dispatch(raw(mon.DISPATCH_WORKERS | extra)),
                                 ({}, "rejected"))


if __name__ == "__main__":
    unittest.main()
