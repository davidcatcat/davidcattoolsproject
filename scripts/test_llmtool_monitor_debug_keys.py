"""#1502 WP-B: the monitor accepts the optional AI Debug keys exactly as the gate does."""

import json
import os
import sys
import tempfile
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
    workers["gw-agent"] = dict(GW_AGENT)
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


class DebugRosterTests(unittest.TestCase):
    NOW = 1_800_000_000.0

    def _rows(self, keys):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "worker-dispatch.json")
            with open(path, "wb") as stream:
                stream.write(raw(keys))
            rows = mon.office_rows([], self.NOW, codex_root=tmp, claude_root=tmp, canonical=tmp,
                                   opencode_db=os.path.join(tmp, "none.db"),
                                   cwd_of=lambda pid: None, cpu_of=lambda pid: 0.0,
                                   dispatch=path, llmtool_tasks=os.path.join(tmp, "tasks"))
        return {row.name: row for row in rows}

    def test_roster_shows_the_debug_switches(self):
        rows = self._rows(mon.DISPATCH_WORKERS | mon.DISPATCH_DEBUG_WORKERS)
        for name in ("codex debug", "claude debug"):
            self.assertEqual((rows[name].state, rows[name].detail), (mon.IDLE, "可派工"))

    def test_older_file_without_debug_keys_has_no_debug_rows(self):
        rows = self._rows(mon.DISPATCH_WORKERS)
        self.assertNotIn("codex debug", rows)
        self.assertNotIn("claude debug", rows)
        self.assertIn("agy", rows)


if __name__ == "__main__":
    unittest.main()
