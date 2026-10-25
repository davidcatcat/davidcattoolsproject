import json
import re
import subprocess
import sys
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


_PASS_LOG = b"Ran 2 tests in 0.01s\n\nOK\n"
_FAIL_LOG = (b"FAIL: test_bad (tests.test_task_lifecycle.SampleTests)\n"
             b"Ran 2 tests in 0.01s\n\nFAILED (failures=1)\n")
_KNOWN_IMPACT_PATH = "tools/llmtool/llmtool/task_lifecycle.py"


def _selected_module(argv):
    """Return the imported test module name for one selector L1/L2 command."""
    if len(argv) > 2 and argv[0] == "python3":
        found = re.search(r"module='(test_[A-Za-z0-9_]+)'", argv[2])
        if found:
            return found.group(1)
    return None


def _command_key(argv):
    module = _selected_module(argv)
    return module if module else " ".join(argv)


def _fake_runner(outputs):
    """Run a fake command set keyed by module name or joined argv."""
    calls = []

    def run(argv, **kwargs):
        calls.append(list(argv))
        payload, exit_status = outputs.get(_command_key(argv), (b"", 0))
        kwargs["stdout"].write(payload)
        return CompletedProcess(argv, exit_status, None)

    run.calls = calls
    return run


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


class ZeroTestsExitStatusTests(unittest.TestCase):
    """A zero-test observation must never be returned as a success exit code."""

    def _run(self, changed, outputs, *, dependency_tests=()):
        root = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory() as temp:
            with patch("llmtool.test_runner.collect_changed_paths",
                       return_value=list(changed)):
                return run_plan(
                    "b0f3b3536490ef604a57702b6efa4153ca71bf11",
                    cwd=root, dependency_tests=dependency_tests,
                    log_dir=Path(temp) / "logs", runner=_fake_runner(outputs),
                )

    def test_l1_zero_observed_tests_is_not_a_success_exit(self):
        result = self._run([_KNOWN_IMPACT_PATH], {})
        self.assertEqual(result["plan"]["levels"][-1]["level"], "L1")
        self.assertEqual(result["summary"]["reported_status"], "NO_TESTS_OBSERVED")
        self.assertNotEqual(result["exit_status"], 0)

    def test_l2_zero_observed_tests_is_not_a_success_exit(self):
        # L1 modules report real tests; only the L2 dependency command is silent.
        result = self._run(
            [_KNOWN_IMPACT_PATH],
            {"test_task_lifecycle": (_PASS_LOG, 0),
             "test_task_lifecycle_cli": (_PASS_LOG, 0)},
            dependency_tests=["tools/llmtool/tests/test_review_exchange.py"],
        )
        self.assertEqual(result["plan"]["levels"][-1]["level"], "L2")
        self.assertEqual(result["summary"]["reported_status"], "NO_TESTS_OBSERVED")
        self.assertNotEqual(result["exit_status"], 0)

    def test_l3_zero_observed_tests_is_not_a_success_exit(self):
        result = self._run(["tools/llmtool/llmtool/new_unknown.py"], {})
        self.assertEqual(result["plan"]["levels"][-1]["level"], "L3")
        self.assertEqual(result["summary"]["reported_status"], "NO_TESTS_OBSERVED")
        self.assertNotEqual(result["exit_status"], 0)

    def test_genuine_passing_tests_keep_zero_exit(self):
        result = self._run(
            [_KNOWN_IMPACT_PATH],
            {"test_task_lifecycle": (_PASS_LOG, 0),
             "test_task_lifecycle_cli": (_PASS_LOG, 0)},
        )
        self.assertEqual(result["summary"]["reported_status"], "ALL_EXIT_ZERO")
        self.assertEqual(result["exit_status"], 0)

    def test_genuine_passing_l3_full_check_keeps_zero_exit(self):
        result = self._run(
            ["tools/llmtool/llmtool/new_unknown.py"],
            {"make check": (b"Ran 2126 tests in 853.157s\n\nOK\n", 0)},
        )
        self.assertEqual(result["plan"]["levels"][-1]["level"], "L3")
        self.assertEqual(result["summary"]["reported_status"], "ALL_EXIT_ZERO")
        self.assertEqual(result["exit_status"], 0)

    def test_nonzero_test_command_is_never_reported_as_success(self):
        result = self._run(
            [_KNOWN_IMPACT_PATH],
            {"test_task_lifecycle": (_FAIL_LOG, 1),
             "test_task_lifecycle_cli": (_PASS_LOG, 0)},
        )
        self.assertEqual(result["summary"]["reported_status"], "HAS_NONZERO_EXIT")
        self.assertNotEqual(result["exit_status"], 0)

    def test_l0_only_plan_is_not_treated_as_zero_tests(self):
        l0_only = {
            "schema_version": 1, "impact_map_version": 1, "changed_paths": [],
            "levels": [{"level": "L0", "kind": "static", "cwd": "tools/llmtool",
                        "command": ["make", "syntax"]}],
            "fallback_reasons": [], "required_gates": [], "executes_tests": False,
            "pipeline_integrated": False,
        }
        root = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory() as temp:
            with patch("llmtool.test_runner.collect_changed_paths",
                       return_value=[]), \
                    patch("llmtool.test_runner.select_test_plan",
                          return_value=l0_only):
                result = run_plan(
                    "b0f3b3536490ef604a57702b6efa4153ca71bf11",
                    cwd=root, log_dir=Path(temp) / "logs",
                    runner=_fake_runner({}),
                )
        self.assertEqual(result["summary"]["reported_status"], "ALL_EXIT_ZERO")
        self.assertEqual(result["exit_status"], 0)

    def test_unparseable_failing_test_command_still_exits_nonzero(self):
        # A command that dies before unittest prints a summary reports zero
        # observed tests. NO_TESTS_OBSERVED precedence over HAS_NONZERO_EXIT is
        # the existing summary semantics; the exit code must stay nonzero.
        result = self._run(
            [_KNOWN_IMPACT_PATH],
            {"test_task_lifecycle": (b"ImportError: cannot import name 'x'\n", 1),
             "test_task_lifecycle_cli": (_PASS_LOG, 0)},
        )
        self.assertNotEqual(result["exit_status"], 0)
        self.assertEqual(result["summary"]["reported_status"], "NO_TESTS_OBSERVED")

    def _run_cli(self, command_output):
        """Run the real CLI in a child process with only the log writer faked."""
        llmtool_dir = Path(__file__).resolve().parents[1]
        head = subprocess.run(
            ["git", "-C", str(llmtool_dir), "rev-parse", "HEAD"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
            timeout=120,
        ).stdout.decode("ascii").strip()
        script = (
            "import sys\n"
            "import llmtool.test_runner as runner\n"
            "runner.collect_changed_paths = lambda *a, **k: [\n"
            "    'tools/llmtool/llmtool/task_lifecycle.py']\n"
            "class _Result:\n"
            "    returncode = 0\n"
            "def _fake_logged(run_dir, ordinal, run, command, **kwargs):\n"
            "    path = run_dir / ('%03d.log' % ordinal)\n"
            "    path.write_bytes(PAYLOAD)\n"
            "    return _Result(), PAYLOAD, path, 'sha256:' + '0' * 64\n"
            "runner._run_logged = _fake_logged\n"
            "raise SystemExit(runner.main(['--base', sys.argv[1],\n"
            "                              '--log-dir', sys.argv[2]]))\n"
        ).replace("PAYLOAD", repr(command_output))
        with tempfile.TemporaryDirectory() as temp:
            log_dir = Path(temp) / "logs"
            log_dir.mkdir(mode=0o700)
            return subprocess.run(
                [sys.executable, "-c", script, head, str(log_dir)],
                cwd=str(llmtool_dir), stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, check=False, timeout=300,
            )

    def test_cli_process_exit_status_matches_its_json_result(self):
        completed = self._run_cli(b"")
        self.assertEqual(completed.returncode, 1)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["exit_status"], 1)
        self.assertEqual(payload["summary"]["reported_status"],
                         "NO_TESTS_OBSERVED")

    def test_cli_process_exit_zero_still_matches_success_json(self):
        completed = self._run_cli(_PASS_LOG)
        self.assertEqual(completed.returncode, 0)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["exit_status"], 0)
        self.assertEqual(payload["summary"]["reported_status"], "ALL_EXIT_ZERO")


if __name__ == "__main__":
    unittest.main()
