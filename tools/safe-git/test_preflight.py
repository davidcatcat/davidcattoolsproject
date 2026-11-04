"""Same public CLI as daily use, real disposable Git repo and Python venv."""
import importlib.util
import argparse
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
REAL_LOAD_TRUST = gate._load_trust

# Child processes do not inherit an in-process patch. Load the same script and
# point its real trust reader at the fixture home; it never reads a real trust.json.
_CHILD_BOOTSTRAP = (
    "import functools, importlib.util, sys\n"
    "spec = importlib.util.spec_from_file_location('preflight', {script!r})\n"
    "gate = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(gate)\n"
    "gate._load_trust = functools.partial(gate._load_trust, home={home!r})\n"
    "sys.exit(gate.main(sys.argv[1:]))\n"
)


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="safe-git-preflight-")
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name).resolve()
        self.make_trust_fixture()
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

    def make_trust_fixture(self):
        """Temporary owner home with a private trust.json; separate from self.parent."""
        temp = tempfile.TemporaryDirectory(prefix="safe-git-trust-")
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        self.home = base / "home"
        config = self.home / ".config" / "llmtool"
        config.mkdir(mode=0o700, parents=True)
        for path in (self.home, self.home / ".config", config):
            path.chmod(0o700)
        self.trust_canonical = base / "canonical"
        self.trust_workspace = base / "workspace"
        for path in (self.trust_canonical, self.trust_workspace / "tasks"):
            path.mkdir(mode=0o700, parents=True)
        self.trust_file = config / "trust.json"
        self.write_trust({
            "schema_version": 1, "canonical_root": str(self.trust_canonical),
            "workspace_root": str(self.trust_workspace),
            "tasks_root": str(self.trust_workspace / "tasks"),
            "repository": {}, "ci_trust": {}, "tools": {}})
        patcher = patch.object(gate, "_load_trust", lambda: REAL_LOAD_TRUST(home=str(self.home)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_trust(self, value):
        if self.trust_file.exists():
            self.trust_file.unlink()
        self.trust_file.write_text(json.dumps(value))
        self.trust_file.chmod(0o600)

    def git(self, *args):
        return gate.git(self.root, *args).decode()

    def invoke(self, args=None):
        bootstrap = _CHILD_BOOTSTRAP.format(script=str(SCRIPT), home=str(self.home))
        return subprocess.run([sys.executable, "-B", "-I", "-c", bootstrap,
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

    def linked_worktree_args(self):
        root = self.parent / "linked"
        subprocess.run(["/usr/bin/git", "-C", str(self.root), "worktree", "add",
                        "--detach", str(root), "HEAD"], check=True, capture_output=True)
        root.chmod(0o700)
        gitdir = Path(gate.git(root, "rev-parse", "--path-format=absolute",
                               "--absolute-git-dir").decode().strip())
        exclude = gitdir / "info/exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text("tools/\n")
        venv = root / "tools/llmtool/.venv"
        subprocess.run([sys.executable, "-B", "-I", "-m", "venv", "--without-pip",
                        str(venv)], check=True, capture_output=True)
        site = next(venv.glob("lib/python*/site-packages"))
        (site / "llmtool.py").write_text("# synthetic module, no product code\n")
        baseline = self.parent / "linked-baseline.json"
        baseline.write_text(json.dumps(gate.inventory(root)))
        baseline.chmod(0o600)
        args = self.args.copy()
        values = {"--root": str(root), "--branch": "", "--baseline": str(baseline)}
        for flag, value in values.items():
            args[args.index(flag) + 1] = value
        return root, args

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

    def test_linked_gitdir_is_validated_and_pinned(self):
        root, args = self.linked_worktree_args()
        gitdir = gate.git_directory(root)
        self.assertEqual(gitdir, Path(self.git(
            "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
        ) / "worktrees" / gitdir.name)
        result = self.invoke(args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"PASS: read-only preflight\n")
        self.assertEqual(result.stderr, b"")

        gitdir.chmod(0o777)
        self.assert_stop(self.invoke(args))
        gitdir.chmod(0o700)

        moved = gitdir.with_name(gitdir.name + "-target")
        gitdir.rename(moved)
        gitdir.symlink_to(moved, target_is_directory=True)
        self.assert_stop(self.invoke(args))

    def test_gitdir_change_between_reads_is_rejected(self):
        parsed = argparse.Namespace(
            root=str(self.root), uid=os.getuid(), root_mode="700", parent_mode="700",
            branch="task", head=self.git("rev-parse", "HEAD").strip(),
            common_dir=str(self.root / ".git"), origin="file://" + str(self.root),
            write_candidate=None, baseline=str(self.baseline), report=None)
        pinned = self.root / ".git"
        with patch.object(gate, "git_directory",
                          side_effect=[pinned, self.parent / "changed-gitdir"]):
            with self.assertRaises(gate.Rejected):
                gate.check(parsed)

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

    def test_private_write_into_trust_roots_rejected(self):
        # #369 PR-7: canonical_root and workspace_root (incl. tasks/) come from trust.
        targets = []
        for folder in (self.trust_canonical / "private",
                       self.trust_workspace / "private",
                       self.trust_workspace / "tasks" / "private"):
            folder.mkdir(mode=0o700)
            folder.chmod(0o700)
            targets.append(folder / "candidate.json")
        for target in targets:
            with self.subTest(target=target.parent.relative_to(self.trust_canonical.parent)):
                self.assert_stop(self.invoke(self.candidate_args(target)))
                self.assert_stop(self.invoke(self.args + ["--report", str(target)]))
                self.assertFalse(target.exists())
                with self.assertRaises(gate.Rejected):
                    gate.private_write(target, {}, self.root, self.root / ".git", os.getuid(),
                                       gate._load_trust())
                self.assertFalse(target.exists())
        # Control: the same private layout outside the trust roots is accepted.
        outside = self.parent / "private"
        outside.mkdir(mode=0o700)
        outside.chmod(0o700)
        result = self.invoke(self.candidate_args(outside / "candidate.json"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"CANDIDATE: review required\n")

    def test_missing_or_invalid_trust_fails_closed_before_git(self):
        candidate = self.parent / "candidate.json"
        self.trust_file.unlink()
        for args in (self.args, self.candidate_args(candidate)):
            result = self.invoke(args)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, b"STOP: preflight rejected (TRUST_MISSING)\n")
        self.assertFalse(candidate.exists())
        with patch.object(gate, "git", side_effect=AssertionError("git before trust")):
            with self.assertRaises(gate.TrustError) as caught:
                gate.check(argparse.Namespace(
                    root=str(self.root), uid=os.getuid(), root_mode="700", parent_mode="700",
                    branch="task", head="0" * 40, common_dir=str(self.root / ".git"),
                    origin="file://" + str(self.root), write_candidate=str(candidate),
                    baseline=None, report=None))
        self.assertEqual(str(caught.exception), "TRUST_MISSING")
        self.write_trust({"schema_version": 2})
        result = self.invoke()
        self.assertEqual(result.stderr, b"STOP: preflight rejected (TRUST_SCHEMA_INVALID)\n")
        self.trust_file.chmod(0o644)
        result = self.invoke()
        self.assertEqual(result.stderr, b"STOP: preflight rejected (TRUST_FILE_NOT_PRIVATE)\n")
        for output in (result.stdout, result.stderr):
            self.assertNotIn(str(self.home).encode(), output)

    def trust_with(self, **roots):
        value = {
            "schema_version": 1, "canonical_root": str(self.trust_canonical),
            "workspace_root": str(self.trust_workspace),
            "tasks_root": str(self.trust_workspace / "tasks"),
            "repository": {}, "ci_trust": {}, "tools": {}}
        value.update(roots)
        self.write_trust(value)

    def assert_trust_rejected(self, code):
        with self.assertRaises(gate.TrustError) as caught:
            gate._load_trust()
        self.assertEqual(caught.exception.code, code)

    def test_symlinked_trust_root_rejected_before_any_write(self):
        # v1 FIX: a root spelled through a symlink would let private_write compare
        # the symlink spelling and miss outputs under the physical repository.
        link = self.parent / "canonical-link"
        link.symlink_to(self.root, target_is_directory=True)
        target = self.root / "private" / "candidate.json"
        (self.root / "private").mkdir(mode=0o700)
        self.trust_with(canonical_root=str(link))
        self.assert_trust_rejected("TRUST_PATH_INVALID")
        for args in (self.candidate_args(target), self.args + ["--report", str(target)]):
            result = self.invoke(args)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, b"STOP: preflight rejected (TRUST_PATH_INVALID)\n")
        self.assertFalse(target.exists())
        # A symlinked ancestor is refused the same way (realpath != configured path).
        parent_link = self.parent / "workspace-parent-link"
        parent_link.symlink_to(self.trust_workspace.parent, target_is_directory=True)
        self.trust_with(workspace_root=str(parent_link / "workspace"),
                        tasks_root=str(parent_link / "workspace" / "tasks"))
        self.assert_trust_rejected("TRUST_PATH_INVALID")
        # Control: the physical spelling of the same repository is a valid root and
        # then denies the write under it.
        self.trust_with(canonical_root=str(self.root))
        self.assertEqual(gate._load_trust()[0], self.root)
        self.assert_stop(self.invoke(self.candidate_args(target)))
        self.assertFalse(target.exists())

    def test_overlapping_trust_roots_rejected(self):
        canonical = str(self.trust_canonical)
        workspace = str(self.trust_workspace)
        nested = self.trust_canonical / "workspace"
        (nested / "tasks").mkdir(mode=0o700, parents=True)
        cases = {
            "same": dict(workspace_root=canonical, tasks_root=str(self.trust_canonical / "x")),
            "workspace inside canonical": dict(workspace_root=str(nested),
                                               tasks_root=str(nested / "tasks")),
            "canonical inside workspace": dict(canonical_root=str(self.trust_workspace / "tasks")),
            "tasks equals workspace": dict(tasks_root=workspace),
            "tasks outside workspace": dict(tasks_root=canonical),
            "root is home": dict(canonical_root=str(self.home)),
            "root contains trust config": dict(canonical_root=str(self.home.parent)),
        }
        (self.trust_canonical / "x").mkdir(mode=0o700)
        for name, roots in cases.items():
            with self.subTest(case=name):
                self.trust_with(**roots)
                self.assert_trust_rejected("TRUST_PATHS_OVERLAP")
        result = self.invoke()
        self.assertEqual(result.stderr, b"STOP: preflight rejected (TRUST_PATHS_OVERLAP)\n")
        self.assertNotIn(str(self.home).encode(), result.stderr)

    def test_non_private_foreign_or_missing_trust_root_rejected(self):
        self.trust_canonical.chmod(0o777)
        self.assert_trust_rejected("TRUST_PATH_INVALID")
        self.trust_canonical.chmod(0o720)
        self.assert_trust_rejected("TRUST_PATH_INVALID")
        self.trust_canonical.chmod(0o700)
        cases = {
            # Root-owned system directory: owner check fails (st_uid != uid).
            "foreign owner": dict(canonical_root="/usr" if os.path.realpath("/usr") == "/usr" else "/"),
            "missing": dict(canonical_root=str(self.trust_canonical.parent / "absent")),
            "regular file": dict(canonical_root=str(self.trust_file)),
        }
        for name, roots in cases.items():
            with self.subTest(case=name):
                self.trust_with(**roots)
                self.assert_trust_rejected("TRUST_PATH_INVALID")
        for bad in ("relative/tasks", str(self.trust_workspace) + "/tasks/", 7,
                    str(self.trust_workspace / "ta\nsks")):
            with self.subTest(tasks_root=bad):
                self.trust_with(tasks_root=bad)
                self.assert_trust_rejected("TRUST_SCHEMA_INVALID")
        self.trust_with()
        self.assertEqual(gate._load_trust(), (self.trust_canonical, self.trust_workspace))

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
