import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from llmtool.test_runner import (
    _counts_and_failures,
    collect_changed_paths,
    run_plan,
)


class TestRunnerTests(unittest.TestCase):
    def test_counts_and_failure_ids_are_bounded_structured_fields(self):
        log = (b"FAIL: test_bad (tests.test_sample.SampleTests)\n"
               b"Ran 3 tests in 0.01s\n\n"
               b"FAILED (failures=1, skipped=1)\n")
        result = _counts_and_failures(log, 1)
        self.assertEqual(result["counts"], {
            "tests_run": 3, "passed": 1, "failed": 1, "errors": 0, "skipped": 1,
        })
        self.assertEqual(result["failure_ids"], [
            "tests.test_sample::SampleTests.test_bad",
        ])

    def test_unknown_output_does_not_claim_tests_passed(self):
        result = _counts_and_failures(b"Ran 9 tests\ncustom output\n", 0)
        self.assertEqual(result["counts"]["tests_run"], 0)
        self.assertEqual(result["counts"]["passed"], 0)

    def test_changed_paths_include_tracked_and_untracked_nul_records(self):
        root = Path(tempfile.mkdtemp())
        responses = {
            ("diff", "--name-only", "-z", "--no-renames", "a" * 40, "--"):
                b"tools/llmtool/llmtool/task_lifecycle.py\0",
            ("ls-files", "--others", "--exclude-standard", "-z"):
                b"tools/llmtool/tests/test_new.py\0tools/llmtool/llmtool/task_lifecycle.py\0",
        }

        def fake_git(_root, *args):
            return responses[args]

        with patch("llmtool.test_runner._git", side_effect=fake_git):
            paths = collect_changed_paths(root, "a" * 40)
        self.assertEqual(paths, [
            "tools/llmtool/llmtool/task_lifecycle.py",
            "tools/llmtool/tests/test_new.py",
        ])

    def test_nonzero_runner_failure_cannot_be_mistaken_for_all_exit_zero(self):
        # Summary validation rejects unsupported/high-risk plans conservatively;
        # command result parsing retains the nonzero status for the caller.
        result = _counts_and_failures(b"", 1)
        self.assertEqual(result["counts"]["tests_run"], 0)
        self.assertEqual(result["failure_ids"], [])

    def test_known_impact_executes_only_static_and_selected_test_plan(self):
        root = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory() as temp:
            log_dir = Path(temp) / "logs"
            calls = []

            def fake_run(argv, **kwargs):
                calls.append((argv, kwargs))
                kwargs["stdout"].write(b"")
                return CompletedProcess(argv, 0, None)

            with patch("llmtool.test_runner.collect_changed_paths", return_value=[
                    "tools/llmtool/llmtool/task_lifecycle.py"]):
                result = run_plan("b0f3b3536490ef604a57702b6efa4153ca71bf11",
                                  cwd=root, log_dir=log_dir, runner=fake_run)

        self.assertEqual(result["plan"]["levels"][-1]["level"], "L1")
        self.assertEqual(len(calls), 4)  # diff-check, L0 syntax, two mapped L1 modules
        self.assertEqual(calls[1][0], ["make", "syntax"])
        self.assertEqual(calls[2][0][0], "python3")
        self.assertEqual(calls[3][0][0], "python3")
        self.assertEqual(result["summary"]["reported_status"], "NO_TESTS_OBSERVED")
        self.assertEqual(result["summary"]["provenance"], "CALLER_ASSERTED_UNVERIFIED")
        self.assertFalse(result["summary"]["execution_verified"])

    def test_unknown_impact_keeps_l3_full_check(self):
        root = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory() as temp:
            calls = []

            def fake_run(argv, **kwargs):
                calls.append((argv, kwargs))
                kwargs["stdout"].write(b"")
                return CompletedProcess(argv, 0, None)

            with patch("llmtool.test_runner.collect_changed_paths", return_value=[
                    "tools/llmtool/llmtool/new_unknown.py"]):
                result = run_plan("b0f3b3536490ef604a57702b6efa4153ca71bf11",
                                  cwd=root, log_dir=Path(temp) / "logs", runner=fake_run)

        self.assertEqual(result["plan"]["levels"][-1]["level"], "L3")
        self.assertEqual(calls[-1][0], ["make", "check"])
        self.assertFalse(result["summary"]["execution_verified"])


if __name__ == "__main__":
    unittest.main()
