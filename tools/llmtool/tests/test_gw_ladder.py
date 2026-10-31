import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
LADDER_ROOT = REPO_ROOT / "tools" / "llmtool" / "eval" / "gw_ladder"
SCRIPT = REPO_ROOT / "tools" / "llmtool" / "scripts" / "gw_ladder.py"
LEVELS = [f"L{number}" for number in range(1, 10)]


def load_scorer():
    spec = importlib.util.spec_from_file_location("gw_ladder_scorer", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GwLadderTests(unittest.TestCase):
    def test_each_original_fails_and_reference_patch_passes(self):
        for level in LEVELS:
            with self.subTest(level=level), tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                destination = root / "tools" / "llmtool" / "eval" / "gw_ladder" / level
                shutil.copytree(LADDER_ROOT / level, destination)
                original = subprocess.run(
                    ["python3", "-I", "-B", "-m", "unittest", "discover",
                     "-s", str(destination), "-p", "hidden_test.py"],
                    cwd=root, capture_output=True, text=True, timeout=10,
                )
                self.assertNotEqual(original.returncode, 0)
                self.assertIn("Ran ", original.stderr + original.stdout)
                applied = subprocess.run(
                    ["git", "apply", str(destination / "reference.patch")],
                    cwd=destination, capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(applied.returncode, 0, applied.stderr)
                patched = subprocess.run(
                    ["python3", "-I", "-B", "-m", "unittest", "discover",
                     "-s", str(destination), "-p", "hidden_test.py"],
                    cwd=root, capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(patched.returncode, 0, patched.stderr + patched.stdout)

    def test_scorer_json_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            destination = root / "tools" / "llmtool" / "eval" / "gw_ladder" / "L1"
            shutil.copytree(LADDER_ROOT / "L1", destination)
            subprocess.run(["git", "apply", str(destination / "reference.patch")],
                           cwd=destination, check=True, capture_output=True, timeout=10)
            result = subprocess.run(
                ["python3", str(SCRIPT), "L1", "--root", str(root)],
                cwd=root, capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            parsed = json.loads(result.stdout)
            self.assertEqual(set(parsed), {"level", "result", "ran", "failures"})
            self.assertEqual(parsed, {"level": "L1", "result": "PASS", "ran": 1, "failures": 0})

    def test_timeout_is_reported_as_error(self):
        scorer = load_scorer()
        process = mock.Mock()
        process.pid = 4242
        process.wait.side_effect = [subprocess.TimeoutExpired("command", 60), -9]
        process.stdout = mock.Mock()
        process.stdout.read.return_value = b""
        process.stderr = mock.Mock()
        process.stderr.read.return_value = b""
        with mock.patch.object(scorer.subprocess, "Popen", return_value=process) as popen, \
                mock.patch.object(scorer.os, "killpg") as killpg:
            result = scorer._run_tests(["python3"], pathlib.Path("."), "unused.json")
        self.assertEqual(result[0], "ERROR")
        self.assertEqual(result[3], "test process exceeded 60 seconds")
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        killpg.assert_any_call(4242, scorer.signal.SIGKILL)

    def _level(self, hidden_test):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = pathlib.Path(directory.name)
        level_dir = root / "tools" / "llmtool" / "eval" / "gw_ladder" / "L1"
        level_dir.mkdir(parents=True)
        (level_dir / "hidden_test.py").write_text(hidden_test, encoding="utf-8")
        return root

    def test_printed_ran_line_and_early_exit_are_not_a_pass(self):
        root = self._level(
            "import os, sys\n"
            "sys.stderr.write('Ran 3 tests in 0.001s\\n\\nOK\\n')\n"
            "sys.stderr.flush()\n"
            "os._exit(0)\n")
        result = load_scorer().score("L1", root)
        self.assertEqual(result, {"level": "L1", "result": "ERROR", "ran": 0, "failures": 0})

    def test_failing_hidden_test_is_fail_with_counts(self):
        root = self._level(
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_a(self): self.assertTrue(False)\n"
            "    def test_b(self): raise RuntimeError('x')\n"
            "    def test_c(self): pass\n")
        result = load_scorer().score("L1", root)
        self.assertEqual(result, {"level": "L1", "result": "FAIL", "ran": 3, "failures": 2})

    def test_descendant_holding_the_pipes_does_not_hang_the_scorer(self):
        # Before the fix the scorer joined its pipe readers without a bound,
        # so a grandchild that inherited stdout kept it waiting forever.
        root = self._level(
            "import subprocess, unittest\n"
            "subprocess.Popen(['sleep', '300'])\n"
            "class T(unittest.TestCase):\n"
            "    def test_ok(self): pass\n")
        scorer = load_scorer()
        finished = []
        worker = threading.Thread(
            target=lambda: finished.append(scorer.score("L1", root)), daemon=True)
        worker.start()
        worker.join(30)
        self.assertFalse(worker.is_alive(), "scorer hung on a descendant's pipe")
        self.assertEqual(finished, [{"level": "L1", "result": "PASS", "ran": 1, "failures": 0}])

    def test_timeout_kills_the_whole_group(self):
        root = self._level(
            "import subprocess, time, unittest\n"
            "subprocess.Popen(['sleep', '300'])\n"
            "class T(unittest.TestCase):\n"
            "    def test_slow(self): time.sleep(300)\n")
        scorer = load_scorer()
        level_dir = root / "tools" / "llmtool" / "eval" / "gw_ladder" / "L1"
        with tempfile.TemporaryDirectory() as directory:
            result_path = str(pathlib.Path(directory) / "result.json")
            command = [sys.executable, "-I", "-B", "-c", scorer._HARNESS,
                       str(level_dir), result_path]
            started = time.monotonic()
            result = scorer._run_tests(command, root, result_path, timeout=2)
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(result[0], "ERROR")
        self.assertEqual(result[3], "test process exceeded 60 seconds")
