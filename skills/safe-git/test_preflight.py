"""Same public CLI as daily use, real disposable Git repo and Python venv."""
import importlib.util
import json
import os
import pwd
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

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

    def assert_pass(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"PASS: read-only preflight\n")
        self.assertEqual(result.stderr, b"")

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

    def candidate_args(self, path):
        args = self.args.copy()
        offset = args.index("--baseline")
        args[offset:offset + 2] = ["--write-candidate", str(path)]
        return args

    def test_candidate_and_private_diff_without_promotion(self):
        candidate = self.parent / "candidate.json"
        original = self.baseline.read_bytes()
        result = self.invoke(self.candidate_args(candidate))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"CANDIDATE: review required\n")
        self.assertEqual(candidate.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(candidate.read_text()), gate.inventory(self.root))
        self.assertIsInstance(json.loads(candidate.read_text())["tracked"], list)
        self.assertEqual(self.baseline.read_bytes(), original)
        self.assert_stop(self.invoke(self.candidate_args(candidate)))
        self.assertEqual(self.baseline.read_bytes(), original)
        (self.root / "tracked.txt").write_text("changed fake-token\n")
        (self.root / "added-sensitive-path.txt").write_text("private contents\n")
        (self.root / "ignored.txt").unlink()
        report = self.parent / "report.json"
        args = self.args + ["--report", str(report)]
        self.assert_stop(self.invoke(args))
        changes = json.loads(report.read_text())
        self.assertEqual(changes["tracked"]["changed"], ["tracked.txt"])
        self.assertEqual(changes["untracked"]["added"], ["added-sensitive-path.txt"])
        self.assertEqual(changes["ignored"]["removed"], ["ignored.txt"])
        self.assertEqual(report.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("fake-token", report.read_text())
        self.assertEqual(self.baseline.read_bytes(), original)
        contents = report.read_bytes()
        self.assert_stop(self.invoke(args))
        self.assertEqual(report.read_bytes(), contents)

    def test_evidence_output_path_rejections(self):
        self.assert_pass()
        target = self.parent / "existing-private"
        target.write_text("preserve")
        link = self.parent / "link"
        link.symlink_to(target)
        for path in (target, link, self.root / "forbidden.json"):
            self.assert_stop(self.invoke(self.candidate_args(path)))
            self.assert_stop(self.invoke(self.args + ["--report", str(path)]))
        self.assertEqual(target.read_text(), "preserve")
        self.assertFalse((self.root / "forbidden.json").exists())
        parent_link = self.parent / "parent-link"
        parent_link.symlink_to(self.parent, target_is_directory=True)
        self.assert_stop(self.invoke(self.candidate_args(parent_link / "new.json")))
        self.assertFalse((self.parent / "new.json").exists())

    def test_clean_private_report_and_index_path_delta(self):
        report = self.parent / "clean-report.json"
        result = self.invoke(self.args + ["--report", str(report)])
        self.assertEqual(result.returncode, 0, result.stderr)
        empty = {"added": [], "removed": [], "changed": []}
        self.assertTrue(all(value == empty for value in json.loads(report.read_text()).values()))
        (self.root / "tracked.txt").write_text("stage-change")
        self.git("add", "--", "tracked.txt")
        report = self.parent / "index-report.json"
        self.assert_stop(self.invoke(self.args + ["--report", str(report)]))
        self.assertEqual(json.loads(report.read_text())["index"]["changed"], ["tracked.txt"])

    def test_baseline_mode_0644_rejected(self):
        self.assert_pass()
        original = self.baseline.read_bytes()
        self.baseline.chmod(0o644)
        self.assert_stop(self.invoke())
        self.assertEqual(self.baseline.read_bytes(), original)

    def test_common_dir_group_writable_rejected(self):
        self.assert_pass()
        self.assertEqual(self.git("rev-parse", "--path-format=absolute", "--git-common-dir").strip(),
                         str(self.root / ".git"))
        (self.root / ".git").chmod(0o775)
        self.assert_stop(self.invoke())
        # Target this guard directly: no unrelated inventory or root-mode check.
        with self.assertRaises(gate.Rejected):
            gate.directory(self.root / ".git", os.getuid())

    def test_tracked_parent_symlink_rejected_before_outside_read(self):
        folder = self.root / "a"
        folder.mkdir()
        (folder / "b.txt").write_text("same-content")
        self.git("add", "--", "a/b.txt")
        self.baseline.write_text(json.dumps(gate.inventory(self.root)))
        self.assert_pass()
        outside = self.parent / "outside"
        folder.rename(outside)
        folder.symlink_to(outside, target_is_directory=True)
        self.assertIn("a/b.txt", self.git("ls-files"))
        # Probe the actual reader, proving no outside file is opened even if
        # another inventory mismatch could also reject the public CLI.
        with patch.object(gate.os, "open", wraps=os.open) as opened:
            with self.assertRaises(gate.Rejected):
                gate.file_record(self.root, b"a/b.txt")
            opened.assert_not_called()
        self.assert_stop(self.invoke())

    @unittest.skipUnless(sys.platform == "darwin", "native macOS ACL required")
    def test_mac_acl_write_grant_rejected(self):
        self.assert_pass()
        username = pwd.getpwuid(os.getuid()).pw_name
        subprocess.run(["/bin/chmod", "+a", "user:" + username + " allow write",
                        str(self.parent)], check=True, capture_output=True)
        self.addCleanup(subprocess.run, ["/bin/chmod", "-N", str(self.parent)],
                        check=True, capture_output=True)
        self.assertEqual(self.parent.stat().st_mode & 0o777, 0o700)
        with self.assertRaises(gate.Rejected):
            gate.directory(self.parent, os.getuid(), 0o700)
        self.assert_stop(self.invoke())


if __name__ == "__main__":
    unittest.main()
