import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "spec_preflight.py"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SPEC = importlib.util.spec_from_file_location("spec_preflight_ladder_under_test", SCRIPT)
spec_preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(spec_preflight)


def write_lines(path, lines):
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


class SpecPreflightLadderTests(unittest.TestCase):
    def setUp(self):
        # Short plain strings; one spec file and one log file per test.
        self.spec = {
            "request": "Add a bounded helper.",
            "summary": "Screen a dispatch spec locally.",
            "goal": "Print field names only.",
            "acceptance": ["Return a clean result."],
        }
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)

    def write_spec(self, extra=None):
        data = dict(self.spec)
        if extra:
            data.update(extra)
        path = self.dir / "spec.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def write_log(self, lines):
        path = self.dir / "ladder.log"
        write_lines(path, lines)
        return path

    def run_main(self, spec_path, extra_args=()):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = spec_preflight.main([str(spec_path), *extra_args])
        return status, output.getvalue()

    def test_no_ladder_still_plain_ok(self):
        spec_path = self.write_spec({"write": ["notes.txt"]})
        status, output = self.run_main(spec_path)
        self.assertEqual((status, output), (0, "SPEC_PREFLIGHT_OK\n"))

    def test_empty_log_one_write_small_spec_is_tier_one(self):
        spec_path = self.write_spec({"write": ["notes.txt"]})
        log_path = self.write_log([])
        status, output = self.run_main(spec_path, ("--ladder", str(log_path)))
        self.assertEqual(status, 0)
        self.assertEqual(output, "SPEC_PREFLIGHT_OK:tier=1,current=1\n")

    def test_empty_log_two_writes_exceeds_tier_one(self):
        spec_path = self.write_spec({"write": ["a.txt", "b.txt"]})
        log_path = self.write_log([])
        status, output = self.run_main(spec_path, ("--ladder", str(log_path)))
        self.assertEqual(status, 2)
        self.assertEqual(output, "SPEC_LADDER_TIER_EXCEEDED:required=3,current=1\n")

    def test_three_first_try_solved_promote_to_tier_two(self):
        row = json.dumps({"tier": 1, "outcome": "SOLVED", "attempts": 1})
        log_path = self.write_log([row, row, row])
        context = self.dir / "context.txt"
        context.write_bytes(b"a" * 2000)
        spec_path = self.write_spec(
            {"write": ["notes.txt"], "context_file": str(context)}
        )
        status, output = self.run_main(spec_path, ("--ladder", str(log_path)))
        self.assertEqual(status, 0)
        self.assertEqual(output, "SPEC_PREFLIGHT_OK:tier=2,current=2\n")

    def test_missing_log_file_is_invalid(self):
        spec_path = self.write_spec({"write": ["notes.txt"]})
        missing = self.dir / "nope.log"
        status, output = self.run_main(spec_path, ("--ladder", str(missing)))
        self.assertEqual(status, 2)
        self.assertEqual(output, "SPEC_PREFLIGHT_INVALID\n")

    def test_extra_and_misspelled_flag_are_invalid(self):
        spec_path = self.write_spec({"write": ["notes.txt"]})
        log_path = self.write_log([])
        for extra in (
            ("--ladder",),
            ("--ladder", str(log_path), "extra"),
            ("--laddear", str(log_path)),
        ):
            with self.subTest(extra=list(extra)):
                status, output = self.run_main(spec_path, extra)
                self.assertEqual(status, 2)
                self.assertEqual(output, "SPEC_PREFLIGHT_INVALID\n")

    def test_bad_json_lines_are_skipped(self):
        good = json.dumps({"tier": 1, "outcome": "SOLVED", "attempts": 1})
        log_path = self.write_log(["not json", "", "42", "null", good, good, good])
        context = self.dir / "context.txt"
        context.write_bytes(b"a" * 2000)
        spec_path = self.write_spec(
            {"write": ["notes.txt"], "context_file": str(context)}
        )
        status, output = self.run_main(spec_path, ("--ladder", str(log_path)))
        self.assertEqual(status, 0)
        self.assertEqual(output, "SPEC_PREFLIGHT_OK:tier=2,current=2\n")


if __name__ == "__main__":
    unittest.main()
