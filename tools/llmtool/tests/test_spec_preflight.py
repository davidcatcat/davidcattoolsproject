import contextlib
import io
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "spec_preflight.py"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from llmtool.errors import LLMToolError

SPEC = importlib.util.spec_from_file_location("spec_preflight", SCRIPT)
spec_preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(spec_preflight)
check = spec_preflight.check
from llmtool.task_submission_cli import _read_pm_record_input


class SpecPreflightTests(unittest.TestCase):
    def setUp(self):
        self.spec = {
            "request": "Add a bounded helper.",
            "summary": "Screen a dispatch spec locally.",
            "goal": "Print field names only.",
            "acceptance": ["Return a clean result."],
        }

    def run_main(self, spec):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.json"
            path.write_text(json.dumps(spec), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = spec_preflight.main([str(path)])
            return status, output.getvalue()

    def test_clean_spec(self):
        self.assertEqual(check(self.spec), [])
        status, output = self.run_main(self.spec)
        self.assertEqual((status, output), (0, "SPEC_PREFLIGHT_OK\n"))

    def test_script_runs_without_site_packages_from_temp_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.json"
            path.write_text(json.dumps(self.spec), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-I", "-S", "-B", str(SCRIPT), str(path)],
                cwd=directory,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "SPEC_PREFLIGHT_OK\n")

    def test_sensitive_value_in_each_field_is_not_printed(self):
        sensitive_value = "private-fixture-value"
        # synthetic-fixture: screen-tool test
        sensitive_text = "tok" + "en = " + sensitive_value
        cases = (
            ("request", sensitive_text, "request"),
            ("summary", sensitive_text, "summary"),
            ("goal", sensitive_text, "goal"),
            ("acceptance", [sensitive_text], "acceptance[0]"),
        )
        for key, value, label in cases:
            with self.subTest(field=key):
                spec = dict(self.spec)
                spec[key] = value
                status, output = self.run_main(spec)
                self.assertEqual(status, 1)
                self.assertEqual(output, f"SPEC_PREFLIGHT_FAIL:{label}\n")
                self.assertNotIn(sensitive_value, output)

    def test_goal_and_acceptance_byte_limits(self):
        for size, expected in ((512, []), (513, ["goal"])):
            with self.subTest(goal_bytes=size):
                spec = dict(self.spec, goal="a" * size)
                self.assertEqual(check(spec), expected)
        for size, expected in ((512, []), (513, ["acceptance[0]"])):
            with self.subTest(acceptance_bytes=size):
                self.assertEqual(check(dict(self.spec, acceptance=["a" * size])), expected)

    def test_summary_and_request_byte_boundaries(self):
        for size, expected in ((2048, []), (2049, ["summary"])):
            with self.subTest(summary_bytes=size):
                self.assertEqual(check(dict(self.spec, summary="a" * size)), expected)
        limit = spec_preflight.REQUEST_LIMIT
        for size, expected in ((limit, []), (limit + 1, ["request"])):
            with self.subTest(request_bytes=size):
                self.assertEqual(check(dict(self.spec, request="a" * size)), expected)

    def test_multibyte_text_is_counted_as_utf8_bytes(self):
        self.assertEqual(check(dict(self.spec, goal="é" * 256)), [])
        self.assertEqual(check(dict(self.spec, goal="é" * 257)), ["goal"])
        self.assertEqual(check(dict(self.spec, acceptance=["é" * 257])), ["acceptance[0]"])

    def test_goal_allows_tab_lf_and_cr(self):
        self.assertEqual(check(dict(self.spec, goal="a\tb\nc\rd")), [])

    def test_acceptance_count_boundaries(self):
        for count, expected in ((0, ["acceptance_count"]), (1, []), (16, []),
                                (17, ["acceptance_count"])):
            with self.subTest(count=count):
                self.assertEqual(check(dict(self.spec, acceptance=["ok"] * count)), expected)

    def test_invalid_json_and_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.json"
            path.write_text("{", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = spec_preflight.main([str(path)])
            self.assertEqual((status, output.getvalue()), (2, "SPEC_PREFLIGHT_INVALID\n"))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = spec_preflight.main([str(path / "missing")])
            self.assertEqual((status, output.getvalue()), (2, "SPEC_PREFLIGHT_INVALID\n"))

    def test_strict_json_rejects_duplicate_keys_constants_and_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.json"
            invalid_specs = (
                '{"request":"a","request":"b","summary":"s","goal":"g","acceptance":["a"]}',
                '{"request":"a","summary":"s","goal":"g","acceptance":["a"],"extra":NaN}',
                '{"request":"a","response":"b"}',
            )
            for raw in invalid_specs:
                with self.subTest(raw=raw):
                    path.write_text(raw, encoding="utf-8")
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        status = spec_preflight.main([str(path)])
                    self.assertEqual((status, output.getvalue()),
                                     (2, "SPEC_PREFLIGHT_INVALID\n"))

    def test_extra_dispatch_spec_keys_are_ignored(self):
        spec = dict(self.spec, provider="codex", d6="task-id",
                    branch="feature/spec", paths=["file.py"])
        self.assertEqual(check(spec), [])
        self.assertEqual(self.run_main(spec), (0, "SPEC_PREFLIGHT_OK\n"))

    def test_lone_surrogate_has_fixed_field_label_without_traceback(self):
        fields = ("request", "summary", "goal", "acceptance")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.json"
            for field in fields:
                spec = dict(self.spec)
                if field == "acceptance":
                    spec[field] = ["bad\ud800"]
                    label = "acceptance[0]"
                else:
                    spec[field] = "bad\ud800"
                    label = field
                raw = json.dumps(spec)
                path.write_text(raw, encoding="utf-8")
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    status = spec_preflight.main([str(path)])
                self.assertEqual((status, output.getvalue()),
                                 (1, f"SPEC_PREFLIGHT_FAIL:{label}\n"))

    def test_check_matches_record_pm_refusals(self):
        # synthetic-fixture: screen-tool test
        credential_null = "".join(("api", "_key", " = ", "null"))
        # synthetic-fixture: screen-tool test
        credential_tab = "".join(("api", "_key\t", " = ", "fixture"))
        cases = (
            ("summary too long", dict(self.spec, summary="a" * 2049)),
            ("request too long", dict(self.spec, request="a" * (spec_preflight.REQUEST_LIMIT + 1))),
            ("empty goal", dict(self.spec, goal="")),
            ("blank summary", dict(self.spec, summary=" \n ")),
            ("empty acceptance item", dict(self.spec, acceptance=[""])),
            ("C0 in goal", dict(self.spec, goal="a\x01b")),
            ("C1 in summary", dict(self.spec, summary="a\x80b")),
            ("Cf in acceptance", dict(self.spec, acceptance=["a\u200bb"])),
            ("credential null", dict(self.spec, goal=credential_null)),
            ("credential tab", dict(self.spec, goal=credential_tab)),
            ("clean", dict(self.spec)),
        )
        for name, spec in cases:
            with self.subTest(case=name):
                response = json.dumps({
                    "schema_version": 1,
                    "summary": spec["summary"],
                    "developer_assignment": {
                        "route": spec.get("route", "codex"),
                        "risk": spec.get("risk", "LOW"),
                        "goal": spec["goal"],
                        "acceptance": spec["acceptance"],
                    },
                })
                envelope = json.dumps({"request": spec["request"], "response": response})
                try:
                    _read_pm_record_input(io.BytesIO(envelope.encode("utf-8")))
                except LLMToolError:
                    record_refuses = True
                else:
                    record_refuses = False
                self.assertEqual(bool(check(spec)), record_refuses)

    def test_wrong_field_type_and_argument_count(self):
        spec = dict(self.spec, acceptance=[1])
        status, output = self.run_main(spec)
        self.assertEqual((status, output), (2, "SPEC_PREFLIGHT_INVALID\n"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = spec_preflight.main([])
        self.assertEqual((status, output.getvalue()), (2, "SPEC_PREFLIGHT_INVALID\n"))


class LadderTierTests(unittest.TestCase):
    """WP-120L (#120): the home LLM ladder tier from ladder log entries."""

    def entry(self, tier, outcome, attempts=1):
        return {"tier": tier, "outcome": outcome, "attempts": attempts}

    def test_empty_log_is_t1(self):
        self.assertEqual(spec_preflight.ladder_tier([]), (1, 0, False))

    def test_three_first_try_solved_promote(self):
        log = [self.entry(1, "SOLVED")] * 3
        self.assertEqual(spec_preflight.ladder_tier(log), (2, 0, False))

    def test_t2_streak_moves_on_to_t3(self):
        log = [self.entry(1, "SOLVED")] * 3 + [self.entry(2, "SOLVED")] * 3
        self.assertEqual(spec_preflight.ladder_tier(log), (3, 0, False))

    def test_repair_or_partial_resets_streak(self):
        log = [self.entry(1, "SOLVED"), self.entry(1, "SOLVED", attempts=2),
               self.entry(1, "PARTIAL")]
        self.assertEqual(spec_preflight.ladder_tier(log), (1, 0, False))

    def test_two_failures_drop_one_tier(self):
        log = [self.entry(1, "SOLVED")] * 3 + [self.entry(2, "FAILED")] * 2
        self.assertEqual(spec_preflight.ladder_tier(log), (1, 0, False))

    def test_other_tiers_and_bad_rows_are_ignored(self):
        log = [self.entry("gw-agent-1", "SOLVED"), self.entry(True, "SOLVED"), "x",
               self.entry(3, "FAILED"), self.entry(1, "SOLVED")]
        self.assertEqual(spec_preflight.ladder_tier(log), (1, 1, False))

    def test_top_tier_is_stable_after_three(self):
        log = [self.entry(1, "SOLVED")] * 3
        self.assertEqual(spec_preflight.ladder_tier(log, max_tier=1), (1, 3, True))


if __name__ == "__main__":
    unittest.main()
