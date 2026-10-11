"""Same public CLI as daily use, real disposable Git repo and Python venv."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("preflight.py")
spec = importlib.util.spec_from_file_location("preflight", SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="safe-git-preflight-")
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name).resolve()
        self.root = self.parent / "repo"
        self.root.mkdir(mode=0o700)
        self.git("init", "--initial-branch=task")
        self.git("remote", "add", "origin", "file://" + str(self.root))
        (self.root / "tracked.txt").write_text("original\n")
        self.git("add", "--", "tracked.txt")
        self.git("-c", "user.name=Synthetic", "-c", "user.email=test@example.invalid",
                 "commit", "-m", "seed")
        self.venv = self.root / "tools/llmtool/.venv"
        subprocess.run([sys.executable, "-B", "-I", "-m", "venv", "--without-pip",
                        str(self.venv)], check=True, capture_output=True)
        site = next(self.venv.glob("lib/python*/site-packages"))
        (site / "llmtool.py").write_text("# synthetic module, no product code\n")
        (self.root / ".git/info/exclude").write_text("tools/\nignored.txt\n")
        (self.root / "ignored.txt").write_text("authoritative ignored data\n")
        self.baseline = self.parent / "baseline.json"
        self.baseline.write_text(json.dumps(gate.inventory(self.root)))
        self.baseline.chmod(0o600)
        self.args = ["--root", str(self.root), "--branch", "task",
                     "--common-dir", str(self.root / ".git"), "--origin",
                     "file://" + str(self.root), "--head", self.git("rev-parse", "HEAD").strip(),
                     "--baseline", str(self.baseline), "--uid", str(os.getuid()),
                     "--root-mode", "700", "--parent-mode", "700"]

    def git(self, *args):
        return gate.git(self.root, *args).decode()

    def invoke(self, args=None):
        return subprocess.run([sys.executable, "-B", "-I", str(SCRIPT),
                               *(self.args if args is None else args)],
                              capture_output=True, timeout=30)

    def assert_stop(self, result):
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"STOP: preflight rejected\n")

    def test_positive_and_wrong_expected_identities(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"PASS: read-only preflight\n")
        self.assertEqual(result.stderr, b"")
        for flag, value in [("--root", str(self.parent)), ("--branch", "peer"),
                            ("--common-dir", str(self.parent)),
                            ("--origin", "file:///missing"), ("--head", "0" * 40),
                            ("--uid", str(os.getuid() + 1)), ("--root-mode", "755")]:
            with self.subTest(flag=flag):
                args = self.args.copy()
                args[args.index(flag) + 1] = value
                self.assert_stop(self.invoke(args))

    def test_tracked_index_untracked_ignored_content_and_path_drift(self):
        for kind in ("tracked", "index", "untracked", "ignored", "ignored-new"):
            with self.subTest(kind=kind):
                before = gate.inventory(self.root)
                self.baseline.write_text(json.dumps(before))
                if kind in ("tracked", "index"):
                    (self.root / "tracked.txt").write_text(kind)
                    if kind == "index":
                        self.git("add", "--", "tracked.txt")
                elif kind == "untracked":
                    (self.root / "new.txt").write_text(kind)
                elif kind == "ignored":
                    (self.root / "ignored.txt").write_text(kind)
                else:
                    (self.venv / "unexpected").write_text(kind)
                post_mutation = gate.inventory(self.root)
                self.assertNotEqual(before, post_mutation)
                self.assert_stop(self.invoke())
                self.assertEqual(gate.inventory(self.root), post_mutation)

    def test_symlink_root_and_world_writable_parent(self):
        link = self.parent / "link"
        link.symlink_to(self.root, target_is_directory=True)
        args = self.args.copy()
        args[args.index("--root") + 1] = str(link)
        self.assert_stop(self.invoke(args))
        self.parent.chmod(0o777)
        self.addCleanup(self.parent.chmod, 0o700)
        self.assert_stop(self.invoke())

    def test_corrupt_baseline_and_symlink_baseline(self):
        self.baseline.write_text("not-json fake-secret")
        self.assert_stop(self.invoke())
        link = self.parent / "baseline-link"
        link.symlink_to(self.baseline)
        args = self.args.copy()
        args[args.index("--baseline") + 1] = str(link)
        self.assert_stop(self.invoke(args))

    def test_real_import_resolves_outside_own_root(self):
        site = next(self.venv.glob("lib/python*/site-packages"))
        peer = self.parent / "peer"
        peer.mkdir()
        module = peer / "llmtool.py"
        module.write_text("# synthetic peer module\n")
        (site / "llmtool.py").unlink()
        (site / "peer.pth").write_text(str(peer) + "\n")
        # Explicitly reviewed new content inventory: import gate must still reject.
        self.baseline.write_text(json.dumps(gate.inventory(self.root)))
        self.assert_stop(self.invoke())


if __name__ == "__main__":
    unittest.main()
