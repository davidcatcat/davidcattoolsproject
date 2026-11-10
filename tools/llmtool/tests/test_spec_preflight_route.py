import contextlib
import io
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

# Load the script by path, the same way test_spec_preflight.py does it.
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "spec_preflight.py"
ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

SPEC = importlib.util.spec_from_file_location("spec_preflight", SCRIPT)
spec_preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(spec_preflight)
ladder_tier = spec_preflight.ladder_tier


class RouteTierTests(unittest.TestCase):
    """Ladder rows are filtered by route: int tiers for None, '<route>-N' for a route."""

    def row(self, tier, outcome, attempts=1):
        return {"tier": tier, "outcome": outcome, "attempts": attempts}

    def test_route_none_ignores_gw_agent_rows(self):
        # Without a route, only int tiers count, so 'gw-agent-1' rows are skipped.
        log = [self.row("gw-agent-1", "SOLVED")] * 3
        self.assertEqual(ladder_tier(log), (1, 0, False))

    def test_route_gw_agent_promotes_and_ignores_int_tiers(self):
        # Three first-try SOLVED 'gw-agent-1' rows promote to 2, while the
        # int-tier row is ignored by the route filter.
        log = [self.row(1, "SOLVED"), self.row("gw-agent-1", "SOLVED")] * 3
        self.assertEqual(ladder_tier(log, route="gw-agent"), (2, 0, False))

    def test_route_ignores_non_matching_rows(self):
        # 'gw-agent-x' and 'gw-agent-' are not tier rows, and 'other-1'
        # belongs to another route, so none of them count.
        log = [self.row("gw-agent-x", "SOLVED"), self.row("gw-agent-", "SOLVED"),
               self.row("other-1", "SOLVED")] * 3
        self.assertEqual(ladder_tier(log, route="gw-agent"), (1, 0, False))

    def _write_files(self, directory, spec, log_lines):
        spec_path = Path(directory) / "spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        log_path = Path(directory) / "ladder.jsonl"
        log_path.write_text("\n".join(json.dumps(line) for line in log_lines) + "\n",
                            encoding="utf-8")
        return spec_path, log_path

    def _run_main(self, spec, log_lines):
        # A short clean gw-agent spec: 3 reads, 1 write, no context file.
        with tempfile.TemporaryDirectory() as directory:
            spec_path, log_path = self._write_files(directory, spec, log_lines)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = spec_preflight.main([str(spec_path), "--ladder", str(log_path)])
            return status, output.getvalue()

    def test_main_gw_agent_log_of_three_solved_rows_is_current_2(self):
        spec = {
            "provider": "gw-agent",
            "request": "Run a short gw-agent probe.",
            "summary": "Probe the gw-agent ladder.",
            "goal": "Confirm the ladder line.",
            "acceptance": ["Print the preflight line."],
            "read": ["a.txt", "b.txt", "c.txt"],
            "write": ["out.txt"],
        }
        log = [{"tier": "gw-agent-1", "outcome": "SOLVED", "attempts": 1}] * 3
        status, output = self._run_main(spec, log)
        # The spec fits tier 1, so tier=1 and the route log lifts current to 2.
        self.assertEqual((status, output), (0, "SPEC_PREFLIGHT_OK:tier=1,current=2\n"))

    def test_main_gw_agent_empty_log_is_current_1(self):
        spec = {
            "provider": "gw-agent",
            "request": "Run a short gw-agent probe.",
            "summary": "Probe the gw-agent ladder.",
            "goal": "Stay at current tier 1.",
            "acceptance": ["Print the preflight line."],
            "read": ["a.txt", "b.txt", "c.txt"],
            "write": ["out.txt"],
        }
        status, output = self._run_main(spec, [])
        self.assertEqual((status, output), (0, "SPEC_PREFLIGHT_OK:tier=1,current=1\n"))


if __name__ == "__main__":
    unittest.main()
