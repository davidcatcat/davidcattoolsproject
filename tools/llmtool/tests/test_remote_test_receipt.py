import ast
import contextlib
import importlib.util
import io
import json
import os
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import remote_test

SHA = "a" * 40
OTHER_SHA = "b" * 40
SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "remote_test_receipt.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("remote_test_receipt", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


remote_test_receipt = _load_builder()


class RemoteTestReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.src = self.root / "src"
        self.src.mkdir()
        self.venv_py = self.root / "venv-python"
        self.venv_py.write_text("#!/bin/sh\n", encoding="utf-8")
        self._default_tree = "c" * 40

    def _default_git(self, cmd):
        cmd = list(cmd)
        if "rev-parse" in cmd and "HEAD" in cmd:
            return SimpleNamespace(returncode=0, stdout=SHA.encode("ascii"), stderr=b"")
        if "status" in cmd:
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        if "rev-parse" in cmd and any(arg.endswith("^{tree}") for arg in cmd):
            return SimpleNamespace(returncode=0, stdout=self._default_tree.encode("ascii"), stderr=b"")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    def invoke(self, argv, *, runner=None, clock=None, git=None):
        if git is None:
            git = self._default_git
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = remote_test_receipt.main(argv, runner=runner, clock=clock, git=git)
        return code, stdout.getvalue()

    def test_pass_receipt_is_accepted_by_validate_remote_receipt(self):
        out_dir = self.root / "out-pass"
        calls = []

        def fake_runner(python, src, module, timeout):
            calls.append((python, src, module, timeout))
            if module == "test_alpha":
                output = b"Ran 2 tests in 0.010s\n\nOK\n"
            else:
                output = b"Ran 1 test in 0.005s\n\nOK (skipped=1)\n"
            return SimpleNamespace(returncode=0, stdout=output, stderr=b"", timed_out=False)

        code, printed = self.invoke(
            [
                "run",
                "--src", str(self.src),
                "--python", str(self.venv_py),
                "--out", str(out_dir),
                "--sha", SHA,
                "--fetch-head", SHA,
                "test_beta",
                "test_alpha",
            ],
            runner=fake_runner,
        )

        self.assertEqual(code, 0)
        self.assertEqual([call[2] for call in calls], ["test_alpha", "test_beta"])
        self.assertTrue(all(0 < call[3] <= 180 for call in calls))
        receipt_path = out_dir / "receipt.json"
        raw = receipt_path.read_bytes()
        self.assertLessEqual(len(raw), 32768)
        validated = remote_test._validate_remote_receipt(
            raw, sha=SHA, modules=("test_alpha", "test_beta")
        )
        self.assertIsNotNone(validated)
        self.assertEqual(set(validated), remote_test._RECEIPT_FIELDS)
        self.assertNotIn("failure_excerpt", validated)
        self.assertEqual(validated["result"], "PASS")
        self.assertEqual(validated["exit_code"], 0)
        self.assertEqual(validated["source_sha"], SHA)
        self.assertEqual(validated["fetch_head"], SHA)
        self.assertEqual(validated["modules"], ["test_alpha", "test_beta"])
        self.assertEqual(validated["identity"], {"uid": os.getuid()})
        self.assertEqual(validated["ran_total"], 3)
        self.assertEqual(validated["final"], ["OK", "OK (skipped=1)"])
        self.assertEqual(validated["failures"], [])
        self.assertIsNone(validated["remote_log_path"])
        self.assertEqual(
            printed.splitlines(),
            [
                "Ran 2 tests in 0.010s",
                "OK",
                "Ran 1 test in 0.005s",
                "OK (skipped=1)",
                "PASS",
            ],
        )

    def test_fail_receipt_includes_bounded_redacted_excerpt_and_exits_nonzero(self):
        out_dir = self.root / "out-fail"
        fake_home = str(self.root / "fake-home")
        noisy_lines = "".join(f"  detail line {i} " + ("x" * 90) + "\n" for i in range(45))
        failure_text = (
            "unprinted banner line\n"
            "FAIL: test_expected (test_alpha.SampleCase.test_expected)\n"
            "----------------------------------------------------------------------\n"
            "Traceback (most recent call last):\n"
            f'  File "{self.src}/tests/test_alpha.py", line 14, in test_expected\n'
            f'  File "{fake_home}/helper.py", line 3, in check\n'
            "AssertionError: expected value mismatch\n"
            + noisy_lines
            + "----------------------------------------------------------------------\n"
            "Ran 1 test in 0.012s\n\n"
            "FAILED (failures=1)\n"
        ).encode("utf-8")

        def fake_runner(python, src, module, timeout):
            return SimpleNamespace(
                returncode=1,
                stdout=b"",
                stderr=failure_text,
                timed_out=False,
                home=fake_home,
            )

        code, printed = self.invoke(
            [
                "run",
                "--src", str(self.src),
                "--python", str(self.venv_py),
                "--out", str(out_dir),
                "--sha", SHA,
                "--fetch-head", SHA,
                "test_alpha",
            ],
            runner=fake_runner,
        )

        self.assertEqual(code, 1)
        raw = (out_dir / "receipt.json").read_bytes()
        validated = remote_test._validate_remote_receipt(
            raw, sha=SHA, modules=("test_alpha",)
        )
        self.assertIsNotNone(validated)
        self.assertEqual(validated["result"], "FAIL")
        self.assertEqual(validated["exit_code"], 1)
        self.assertEqual(validated["failures"], ["test_alpha"])
        excerpt = validated["failure_excerpt"]
        self.assertIn("FAIL: test_expected (test_alpha.SampleCase.test_expected)", excerpt)
        self.assertIn("<REMOTE_WORKDIR>/tests/test_alpha.py", excerpt)
        self.assertIn("<REMOTE_HOME>/helper.py", excerpt)
        self.assertNotIn(str(self.src), excerpt)
        self.assertNotIn(fake_home, excerpt)
        self.assertLessEqual(len(excerpt.splitlines()), 40)
        self.assertLessEqual(len(excerpt.encode("utf-8")), 4096)
        self.assertNotIn("unprinted banner line", printed)
        self.assertTrue(printed.startswith("Ran 1 test in 0.012s\nFAILED (failures=1)\n"))
        self.assertTrue(printed.endswith("\nFAIL\n"))

    def test_excerpt_screening_uses_patched_screen_function(self):
        out_dir = self.root / "out-screened"
        raw_output = (
            "======================================================================\n"
            "FAIL: test_safe (pkg.part.Case)\n"
            "----------------------------------------------------------------------\n"
            "Traceback (most recent call last):\n"
            "AssertionError: keep this line\n"
            "DROP_MARKER_LINE should be removed\n"
            "======================================================================\n"
            "FAIL: test_unsafe DROP_MARKER_LINE (pkg.part.Case)\n"
            "----------------------------------------------------------------------\n"
            "AssertionError: retained after unsafe header\n"
            "----------------------------------------------------------------------\n"
            "Ran 2 tests in 0.010s\n\n"
            "FAILED (failures=2)\n"
        ).encode("utf-8")

        def fake_screen(line):
            return "DROP_MARKER_LINE" in line or "pkg.part.Case" in line

        def fake_runner(python, src, module, timeout):
            return SimpleNamespace(returncode=1, stdout=raw_output, stderr=b"", timed_out=False)

        with patch.object(remote_test_receipt, "screen_line", side_effect=fake_screen):
            code, printed = self.invoke(
                [
                    "run",
                    "--src", str(self.src),
                    "--python", str(self.venv_py),
                    "--out", str(out_dir),
                    "--sha", SHA,
                    "--fetch-head", SHA,
                    "test_alpha",
                ],
                runner=fake_runner,
            )

        self.assertEqual(code, 1)
        receipt = json.loads((out_dir / "receipt.json").read_text(encoding="utf-8"))
        excerpt = receipt["failure_excerpt"]
        self.assertIn("FAIL: test_safe (pkg.part.Case)", excerpt)
        self.assertIn("AssertionError: keep this line", excerpt)
        self.assertIn("AssertionError: retained after unsafe header", excerpt)
        self.assertNotIn("DROP_MARKER_LINE", excerpt)
        self.assertNotIn("DROP_MARKER_LINE", printed)

    def test_timeout_from_module_runner_and_total_budget(self):
        out_dir = self.root / "out-timeout"

        def timeout_runner(python, src, module, timeout):
            raise subprocess.TimeoutExpired(cmd=module, timeout=timeout, output=b"Ran 1 test\n")

        code, printed = self.invoke(
            [
                "run",
                "--src", str(self.src),
                "--python", str(self.venv_py),
                "--out", str(out_dir),
                "--sha", SHA,
                "--fetch-head", SHA,
                "test_alpha",
                "test_beta",
            ],
            runner=timeout_runner,
        )

        self.assertEqual(code, 124)
        raw = (out_dir / "receipt.json").read_bytes()
        validated = remote_test._validate_remote_receipt(
            raw, sha=SHA, modules=("test_alpha", "test_beta")
        )
        self.assertIsNotNone(validated)
        self.assertEqual(validated["result"], "TIMEOUT")
        self.assertEqual(validated["exit_code"], 124)
        self.assertEqual(validated["failures"], ["test_alpha"])
        self.assertTrue(printed.endswith("TIMEOUT\n"))

        out_budget = self.root / "out-budget-timeout"
        ticks = iter([100.0, 600.0, 645.0, 645.0])
        observed_limits = []

        def budget_runner(python, src, module, timeout):
            observed_limits.append((module, timeout))
            return SimpleNamespace(
                returncode=0,
                stdout=b"Ran 1 test in 1.0s\n\nOK\n",
                stderr=b"",
                timed_out=False,
            )

        code2, _ = self.invoke(
            [
                "run",
                "--src", str(self.src),
                "--python", str(self.venv_py),
                "--out", str(out_budget),
                "--sha", SHA,
                "--fetch-head", SHA,
                "test_alpha",
                "test_beta",
            ],
            runner=budget_runner,
            clock=lambda: next(ticks),
        )

        self.assertEqual(code2, 124)
        self.assertEqual(observed_limits, [("test_alpha", 40.0)])
        budget_receipt = json.loads((out_budget / "receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(budget_receipt["result"], "TIMEOUT")
        self.assertEqual(budget_receipt["failures"], ["test_beta"])

    def test_mismatch_writes_sha_mismatch_receipt_for_sha_and_none(self):
        for suffix, fetch_arg, expected_fetch in (
            ("sha", OTHER_SHA, OTHER_SHA),
            ("none", "none", None),
        ):
            with self.subTest(fetch_arg=fetch_arg):
                out_dir = self.root / f"out-mismatch-{suffix}"
                code, printed = self.invoke(
                    [
                        "mismatch",
                        "--out", str(out_dir),
                        "--sha", SHA,
                        "--fetch-head", fetch_arg,
                        "test_beta",
                        "test_alpha",
                    ]
                )
                self.assertNotEqual(code, 0)
                raw = (out_dir / "receipt.json").read_bytes()
                validated = remote_test._validate_remote_receipt(
                    raw, sha=SHA, modules=("test_alpha", "test_beta")
                )
                self.assertIsNotNone(validated)
                self.assertEqual(validated["result"], "SHA_MISMATCH")
                self.assertIsNone(validated["tree"])
                self.assertEqual(validated["fetch_head"], expected_fetch)
                self.assertEqual(validated["ran_total"], 0)
                self.assertNotEqual(validated["exit_code"], 0)
                self.assertEqual(validated["modules"], ["test_alpha", "test_beta"])
                self.assertEqual(printed, "SHA_MISMATCH\n")

    def test_bad_arguments_and_existing_out_dir_write_no_receipt(self):
        existing_out = self.root / "already-exists"
        existing_out.mkdir()
        code, _ = self.invoke(
            [
                "run",
                "--src", str(self.src),
                "--python", str(self.venv_py),
                "--out", str(existing_out),
                "--sha", SHA,
                "--fetch-head", SHA,
                "test_alpha",
            ],
            runner=lambda *args: self.fail("runner should not be called"),
        )
        self.assertNotEqual(code, 0)
        self.assertFalse((existing_out / "receipt.json").exists())

        nine_modules = [f"test_mod_{i}" for i in range(9)]
        bad_cases = [
            ["run", "--src", str(self.src), "--python", str(self.venv_py), "--out", str(self.root / "b1"), "--sha", "bad", "--fetch-head", "bad", "test_alpha"],
            ["run", "--src", str(self.src), "--python", str(self.venv_py), "--out", str(self.root / "b2"), "--sha", SHA, "--fetch-head", OTHER_SHA, "test_alpha"],
            ["run", "--src", "relative/src", "--python", str(self.venv_py), "--out", str(self.root / "b3"), "--sha", SHA, "--fetch-head", SHA, "test_alpha"],
            ["run", "--src", str(self.root / "missing"), "--python", str(self.venv_py), "--out", str(self.root / "b4"), "--sha", SHA, "--fetch-head", SHA, "test_alpha"],
            ["run", "--src", str(self.src), "--python", "relative/python", "--out", str(self.root / "b5"), "--sha", SHA, "--fetch-head", SHA, "test_alpha"],
            ["run", "--src", str(self.src), "--python", str(self.root / "missing-py"), "--out", str(self.root / "b6"), "--sha", SHA, "--fetch-head", SHA, "test_alpha"],
            ["run", "--src", str(self.src), "--python", str(self.venv_py), "--out", str(self.root / "b7"), "--sha", SHA, "--fetch-head", SHA, "invalid_module"],
            ["run", "--src", str(self.src), "--python", str(self.venv_py), "--out", str(self.root / "b8"), "--sha", SHA, "--fetch-head", SHA, "test_alpha", "test_alpha"],
            ["run", "--src", str(self.src), "--python", str(self.venv_py), "--out", str(self.root / "b9"), "--sha", SHA, "--fetch-head", SHA, *nine_modules],
            ["mismatch", "--out", str(self.root / "b10"), "--sha", SHA, "--fetch-head", SHA, "test_alpha"],
            ["mismatch", "--out", str(self.root / "b11"), "--sha", SHA, "--fetch-head", "invalid", "test_alpha"],
            ["unknown", "--out", str(self.root / "b12")],
        ]
        for idx, argv in enumerate(bad_cases, start=1):
            with self.subTest(case=idx):
                out_candidate = self.root / f"b{idx}"
                rc, _ = self.invoke(
                    argv,
                    runner=lambda *args: self.fail("runner should not be called"),
                )
                self.assertNotEqual(rc, 0)
                self.assertFalse(out_candidate.exists())

    def test_run_module_uses_fresh_home_tmpdir_devnull_and_kills_session(self):
        captured = {}

        class FakeProc:
            pid = 43210
            returncode = 0

            def communicate(self, timeout=None):
                captured["timeout"] = timeout
                captured["home_exists"] = os.path.isdir(captured["env"]["HOME"])
                captured["tmp_exists"] = os.path.isdir(captured["env"]["TMPDIR"])
                return b"Ran 1 test in 0.001s\n\nOK\n", b""

        def fake_popen(argv, **kwargs):
            captured["argv"] = argv
            captured["cwd"] = kwargs.get("cwd")
            captured["env"] = kwargs.get("env")
            captured["stdin"] = kwargs.get("stdin")
            captured["start_new_session"] = kwargs.get("start_new_session")
            return FakeProc()

        killed = []
        with patch.object(remote_test_receipt.subprocess, "Popen", side_effect=fake_popen), \
                patch.object(remote_test_receipt.os, "killpg", side_effect=lambda pid, sig: killed.append((pid, sig))):
            res = remote_test_receipt.run_module(str(self.venv_py), str(self.src), "test_alpha", 120)

        self.assertEqual(res.returncode, 0)
        self.assertFalse(res.timed_out)
        self.assertEqual(
            captured["argv"],
            [str(self.venv_py), "-m", "unittest", "discover", "-s", "tests", "-p", "test_alpha.py"],
        )
        self.assertEqual(captured["cwd"], str(self.src))
        self.assertEqual(captured["stdin"], subprocess.DEVNULL)
        self.assertTrue(captured["start_new_session"])
        self.assertTrue(captured["home_exists"])
        self.assertTrue(captured["tmp_exists"])
        self.assertNotEqual(captured["env"]["HOME"], os.environ.get("HOME"))
        self.assertNotEqual(captured["env"]["TMPDIR"], os.environ.get("TMPDIR"))
        self.assertFalse(os.path.exists(captured["env"]["HOME"]))
        self.assertFalse(os.path.exists(captured["env"]["TMPDIR"]))
        self.assertEqual(killed, [(43210, signal.SIGKILL)])

    def test_sensitive_pattern_matches_remote_script_and_stdlib_only(self):
        builder_source = SCRIPT_PATH.read_text(encoding="utf-8")
        builder_ast = ast.parse(builder_source)
        for node in ast.walk(builder_ast):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertFalse(alias.name.startswith("llmtool"))
            elif isinstance(node, ast.ImportFrom):
                self.assertFalse((node.module or "").startswith("llmtool"))

        def extract_sensitive_pattern(tree):
            assign = next(
                node for node in tree.body
                if isinstance(node, ast.Assign) and
                any(isinstance(t, ast.Name) and t.id == "SENSITIVE" for t in node.targets)
            )
            return assign.value.args[0].value

        remote_ast = ast.parse(remote_test._REMOTE_SCRIPT)
        self.assertEqual(
            extract_sensitive_pattern(builder_ast),
            extract_sensitive_pattern(remote_ast),
        )
        self.assertEqual(
            remote_test_receipt.SENSITIVE.pattern,
            extract_sensitive_pattern(remote_ast),
        )

    def test_checkout_binding_matching_clean_checkout(self):
        out_dir = self.root / "out-checkout-clean"
        tree_sha = "d" * 40
        calls = []

        def custom_git(cmd):
            cmd = list(cmd)
            if "rev-parse" in cmd and "HEAD" in cmd:
                return SimpleNamespace(returncode=0, stdout=SHA.encode("ascii"), stderr=b"")
            if "status" in cmd:
                return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
            if "rev-parse" in cmd and any(arg.endswith("^{tree}") for arg in cmd):
                return SimpleNamespace(returncode=0, stdout=tree_sha.encode("ascii"), stderr=b"")
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        def fake_runner(python, src, module, timeout):
            calls.append(module)
            return SimpleNamespace(
                returncode=0, stdout=b"Ran 1 test in 0.001s\n\nOK\n", stderr=b"", timed_out=False
            )

        code, printed = self.invoke(
            [
                "run",
                "--src", str(self.src),
                "--python", str(self.venv_py),
                "--out", str(out_dir),
                "--sha", SHA,
                "--fetch-head", SHA,
                "test_alpha",
            ],
            runner=fake_runner,
            git=custom_git,
        )

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["test_alpha"])
        raw = (out_dir / "receipt.json").read_bytes()
        validated = remote_test._validate_remote_receipt(raw, sha=SHA, modules=("test_alpha",))
        self.assertIsNotNone(validated)
        self.assertEqual(validated["result"], "PASS")
        self.assertEqual(validated["tree"], tree_sha)
        self.assertTrue(printed.endswith("PASS\n"))

    def test_checkout_binding_different_head_fails_without_running_modules(self):
        out_dir = self.root / "out-checkout-diff-head"
        calls = []

        def custom_git(cmd):
            cmd = list(cmd)
            if "rev-parse" in cmd and "HEAD" in cmd:
                return SimpleNamespace(returncode=0, stdout=OTHER_SHA.encode("ascii"), stderr=b"")
            if "status" in cmd:
                return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        code, printed = self.invoke(
            [
                "run",
                "--src", str(self.src),
                "--python", str(self.venv_py),
                "--out", str(out_dir),
                "--sha", SHA,
                "--fetch-head", SHA,
                "test_alpha",
            ],
            runner=lambda *args: calls.append(args),
            git=custom_git,
        )

        self.assertNotEqual(code, 0)
        self.assertEqual(calls, [])
        raw = (out_dir / "receipt.json").read_bytes()
        validated = remote_test._validate_remote_receipt(raw, sha=SHA, modules=("test_alpha",))
        self.assertIsNotNone(validated)
        self.assertEqual(validated["result"], "FAIL")
        self.assertNotEqual(validated["exit_code"], 0)
        self.assertIsNone(validated["tree"])
        self.assertEqual(validated["ran_total"], 0)
        self.assertEqual(validated["failures"], [])
        self.assertEqual(validated["failure_excerpt"], remote_test_receipt.CHECKOUT_REASON)
        self.assertEqual(validated["final"], [remote_test_receipt.CHECKOUT_REASON])
        self.assertIn(remote_test_receipt.CHECKOUT_REASON, printed)
        self.assertTrue(printed.endswith("FAIL\n"))

    def test_checkout_binding_dirty_tree_fails_without_running_modules(self):
        out_dir = self.root / "out-checkout-dirty"
        calls = []

        def custom_git(cmd):
            cmd = list(cmd)
            if "rev-parse" in cmd and "HEAD" in cmd:
                return SimpleNamespace(returncode=0, stdout=SHA.encode("ascii"), stderr=b"")
            if "status" in cmd:
                return SimpleNamespace(returncode=0, stdout=b" M tracked_file.py\n", stderr=b"")
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        code, printed = self.invoke(
            [
                "run",
                "--src", str(self.src),
                "--python", str(self.venv_py),
                "--out", str(out_dir),
                "--sha", SHA,
                "--fetch-head", SHA,
                "test_alpha",
            ],
            runner=lambda *args: calls.append(args),
            git=custom_git,
        )

        self.assertNotEqual(code, 0)
        self.assertEqual(calls, [])
        raw = (out_dir / "receipt.json").read_bytes()
        validated = remote_test._validate_remote_receipt(raw, sha=SHA, modules=("test_alpha",))
        self.assertIsNotNone(validated)
        self.assertEqual(validated["result"], "FAIL")
        self.assertNotEqual(validated["exit_code"], 0)
        self.assertIsNone(validated["tree"])
        self.assertEqual(validated["ran_total"], 0)
        self.assertEqual(validated["failures"], [])
        self.assertEqual(validated["failure_excerpt"], remote_test_receipt.CHECKOUT_REASON)
        self.assertEqual(validated["final"], [remote_test_receipt.CHECKOUT_REASON])
        self.assertIn(remote_test_receipt.CHECKOUT_REASON, printed)
        self.assertTrue(printed.endswith("FAIL\n"))

    def test_checkout_binding_git_error_fails_without_running_modules(self):
        out_dir = self.root / "out-checkout-err"
        calls = []

        def custom_git(cmd):
            return SimpleNamespace(returncode=128, stdout=b"", stderr=b"fatal: not a git repo")

        code, printed = self.invoke(
            [
                "run",
                "--src", str(self.src),
                "--python", str(self.venv_py),
                "--out", str(out_dir),
                "--sha", SHA,
                "--fetch-head", SHA,
                "test_alpha",
            ],
            runner=lambda *args: calls.append(args),
            git=custom_git,
        )

        self.assertNotEqual(code, 0)
        self.assertEqual(calls, [])
        raw = (out_dir / "receipt.json").read_bytes()
        validated = remote_test._validate_remote_receipt(raw, sha=SHA, modules=("test_alpha",))
        self.assertIsNotNone(validated)
        self.assertEqual(validated["result"], "FAIL")
        self.assertNotEqual(validated["exit_code"], 0)
        self.assertIsNone(validated["tree"])

    def test_checkout_binding_with_temporary_git_repository(self):
        repo_dir = self.root / "temp-repo"
        repo_dir.mkdir()
        try:
            init_res = subprocess.run(
                ["git", "init", str(repo_dir)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if init_res.returncode != 0:
                self.skipTest("git init unavailable")
            subprocess.run(
                ["git", "-C", str(repo_dir), "config", "user.name", "Tester"],
                stdin=subprocess.DEVNULL,
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo_dir), "config", "user.email", "tester@example.org"],
                stdin=subprocess.DEVNULL,
                check=True,
            )
            (repo_dir / "sample.txt").write_text("sample content\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(repo_dir), "add", "sample.txt"],
                stdin=subprocess.DEVNULL,
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo_dir), "commit", "-m", "initial"],
                stdin=subprocess.DEVNULL,
                check=True,
            )
            rev_res = subprocess.run(
                ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            real_sha = rev_res.stdout.decode("ascii", "replace").strip()
            tree_res = subprocess.run(
                ["git", "-C", str(repo_dir), "rev-parse", "HEAD^{tree}"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            expected_tree = tree_res.stdout.decode("ascii", "replace").strip()
        except (OSError, subprocess.SubprocessError) as exc:
            self.skipTest(f"git operations not supported in environment: {exc}")

        calls = []

        def fake_runner(python, src, module, timeout):
            calls.append(module)
            return SimpleNamespace(
                returncode=0, stdout=b"Ran 1 test in 0.002s\n\nOK\n", stderr=b"", timed_out=False
            )

        # 1. Matching clean checkout
        out_clean = self.root / "out-real-clean"
        code_clean, _ = self.invoke(
            [
                "run",
                "--src", str(repo_dir),
                "--python", str(self.venv_py),
                "--out", str(out_clean),
                "--sha", real_sha,
                "--fetch-head", real_sha,
                "test_alpha",
            ],
            runner=fake_runner,
            git=remote_test_receipt.run_git,
        )
        self.assertEqual(code_clean, 0)
        self.assertEqual(calls, ["test_alpha"])
        raw_clean = (out_clean / "receipt.json").read_bytes()
        val_clean = remote_test._validate_remote_receipt(raw_clean, sha=real_sha, modules=("test_alpha",))
        self.assertIsNotNone(val_clean)
        self.assertEqual(val_clean["result"], "PASS")
        self.assertEqual(val_clean["tree"], expected_tree)

        # 2. Different HEAD
        calls.clear()
        out_diff = self.root / "out-real-diff"
        code_diff, _ = self.invoke(
            [
                "run",
                "--src", str(repo_dir),
                "--python", str(self.venv_py),
                "--out", str(out_diff),
                "--sha", OTHER_SHA,
                "--fetch-head", OTHER_SHA,
                "test_alpha",
            ],
            runner=fake_runner,
            git=remote_test_receipt.run_git,
        )
        self.assertNotEqual(code_diff, 0)
        self.assertEqual(calls, [])
        raw_diff = (out_diff / "receipt.json").read_bytes()
        val_diff = remote_test._validate_remote_receipt(raw_diff, sha=OTHER_SHA, modules=("test_alpha",))
        self.assertIsNotNone(val_diff)
        self.assertEqual(val_diff["result"], "FAIL")

        # 3. Dirty tree
        (repo_dir / "untracked.txt").write_text("untracked\n", encoding="utf-8")
        out_dirty = self.root / "out-real-dirty"
        code_dirty, _ = self.invoke(
            [
                "run",
                "--src", str(repo_dir),
                "--python", str(self.venv_py),
                "--out", str(out_dirty),
                "--sha", real_sha,
                "--fetch-head", real_sha,
                "test_alpha",
            ],
            runner=fake_runner,
            git=remote_test_receipt.run_git,
        )
        self.assertNotEqual(code_dirty, 0)
        self.assertEqual(calls, [])
        raw_dirty = (out_dirty / "receipt.json").read_bytes()
        val_dirty = remote_test._validate_remote_receipt(raw_dirty, sha=real_sha, modules=("test_alpha",))
        self.assertIsNotNone(val_dirty)
        self.assertEqual(val_dirty["result"], "FAIL")

    def test_launch_oserror_writes_fail_receipt_with_module_in_failures_and_reason_line(self):
        out_dir = self.root / "out-launch-oserror"
        calls = []

        def oserror_runner(python, src, module, timeout):
            calls.append(module)
            raise OSError("cannot execute binary")

        code, printed = self.invoke(
            [
                "run",
                "--src", str(self.src),
                "--python", str(self.venv_py),
                "--out", str(out_dir),
                "--sha", SHA,
                "--fetch-head", SHA,
                "test_alpha",
            ],
            runner=oserror_runner,
        )

        self.assertNotEqual(code, 0)
        self.assertEqual(calls, ["test_alpha"])
        receipt_path = out_dir / "receipt.json"
        self.assertTrue(receipt_path.exists())
        raw = receipt_path.read_bytes()
        validated = remote_test._validate_remote_receipt(
            raw, sha=SHA, modules=("test_alpha",)
        )
        self.assertIsNotNone(validated)
        self.assertEqual(validated["result"], "FAIL")
        self.assertNotEqual(validated["exit_code"], 0)
        self.assertEqual(validated["failures"], ["test_alpha"])
        self.assertEqual(validated["final"], [remote_test_receipt.LAUNCH_REASON])
        self.assertIn(remote_test_receipt.LAUNCH_REASON, printed)
        self.assertTrue(printed.endswith("FAIL\n"))

    def test_run_tree_unresolvable_writes_null_tree(self):
        out_dir = self.root / "out-tree-null"
        runner_calls = []

        def success_runner(python, src, module, timeout):
            runner_calls.append(module)
            return SimpleNamespace(
                returncode=0,
                stdout=b"Ran 1 test\nOK\n",
                stderr=b"",
                timed_out=False,
                home=None,
            )

        def custom_git(cmd):
            cmd = list(cmd)
            if "rev-parse" in cmd and "HEAD" in cmd:
                return SimpleNamespace(returncode=0, stdout=SHA.encode("ascii"), stderr=b"")
            if "status" in cmd:
                return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
            if "rev-parse" in cmd and any(arg.endswith("^{tree}") for arg in cmd):
                return SimpleNamespace(returncode=128, stdout=b"", stderr=b"fatal: bad tree")
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        code, _ = self.invoke(
            [
                "run",
                "--src", str(self.src),
                "--python", str(self.venv_py),
                "--out", str(out_dir),
                "--sha", SHA,
                "--fetch-head", SHA,
                "test_alpha",
            ],
            runner=success_runner,
            git=custom_git,
        )

        self.assertNotEqual(code, 0)
        raw = (out_dir / "receipt.json").read_bytes()
        validated = remote_test._validate_remote_receipt(raw, sha=SHA, modules=("test_alpha",))
        self.assertIsNotNone(validated)
        self.assertEqual(validated["result"], "FAIL")
        self.assertNotEqual(validated["exit_code"], 0)
        self.assertIsNone(validated["tree"])
        self.assertEqual(runner_calls, [])

    def test_run_module_oserror_on_non_executable_python(self):
        res = remote_test_receipt.run_module(str(self.venv_py), str(self.src), "test_alpha", 120)
        self.assertNotEqual(res.returncode, 0)
        self.assertFalse(res.timed_out)
        self.assertIn(remote_test_receipt.LAUNCH_REASON.encode("utf-8"), res.stderr)


if __name__ == "__main__":
    unittest.main()
