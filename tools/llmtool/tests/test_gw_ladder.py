import importlib.util
import json
import pathlib
import shutil
import subprocess
import tempfile
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
        process.wait.side_effect = [subprocess.TimeoutExpired("command", 60), -9]
        process.stdout = mock.Mock()
        process.stdout.read.return_value = b""
        process.stderr = mock.Mock()
        process.stderr.read.return_value = b""
        with mock.patch.object(scorer.subprocess, "Popen", return_value=process):
            result = scorer._run_tests(["python3"], pathlib.Path("."))
        self.assertEqual(result[0], "ERROR")
        self.assertEqual(result[3], "test process exceeded 60 seconds")
        process.kill.assert_called_once()
