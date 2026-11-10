"""Offline CLI coverage using a fake gh executable."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools" / "llmtool" / "scripts" / "pm_owner_marker.py"
LLMTOOL_DIR = ROOT / "tools" / "llmtool"

try:
    from llmtool import github_identity
except ImportError:
    if str(LLMTOOL_DIR) not in sys.path:
        sys.path.insert(0, str(LLMTOOL_DIR))
    from llmtool import github_identity


class PMOwnerMarkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tmp = Path(self.temp.name)
        self.home = self.tmp / "home"
        self.home.mkdir()
        (self.home / ".config" / "gh-ai").mkdir(parents=True)
        (self.tmp / ".config" / "gh-ai").mkdir(parents=True, exist_ok=True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/a/b.git"],
                       cwd=self.repo, check=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.calls = self.tmp / "calls.jsonl"
        self.gh = self.bin / "gh"
        fake_gh_code = self.tmp / "fake_gh.py"
        fake_gh_code.write_text(f"""import json, os, pathlib, sys
args = sys.argv[1:]
if args == ['api', 'user', '--jq', '.login']:
    sys.path.insert(0, {str(LLMTOOL_DIR)!r})
    from llmtool import github_identity
    print(github_identity.ai_login())
    sys.exit(0)
with open(os.environ['FAKE_GH_CALLS'], 'a', encoding='utf-8') as f:
    row = {{'args': args}}
    if '--body-file' in args:
        path = args[args.index('--body-file') + 1]
        row['body'] = pathlib.Path(path).read_text(encoding='utf-8')
        row['private'] = (pathlib.Path(path).stat().st_mode & 0o077) == 0
    f.write(json.dumps(row) + '\\n')
if '--slurp' in args and ('--jq' in args or '--template' in args):
    print('incompatible gh api flags', file=sys.stderr)
    sys.exit(2)
if os.environ.get('FAKE_GH_FAIL') == '1':
    sys.exit(7)
if args[:2] == ['api', '--paginate']:
    endpoint = args[-1]
    if endpoint.endswith('issues/comments?per_page=100'):
        rows = [
          {{'id': 1, 'issue_url': 'https://api.github.com/repos/a/b/issues/17',
           'created_at': '2026-11-01T00:00:00Z', 'author_association': 'MEMBER',
           'body': '<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->\\nQuestion'}},
          {{'id': 2, 'issue_url': 'https://api.github.com/repos/a/b/issues/18',
           'created_at': '2026-11-01T00:00:00Z', 'author_association': 'MEMBER',
           'body': '<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->\\nQuestion'}},
          {{'id': 3, 'issue_url': 'https://api.github.com/repos/a/b/issues/18',
           'created_at': '2026-11-02T00:00:00Z', 'author_association': 'MEMBER',
           'body': '<!-- activity: type=pm_progress actor=PM target=Owner status=ANSWERED -->\\nRelayed'}},
          {{'id': 4, 'issue_url': 'https://api.github.com/repos/a/b/issues/19',
           'created_at': '2026-11-02T00:00:00Z', 'author_association': 'MEMBER',
           'body': '<!-- activity: type=waiting_owner actor=PM target=Owner status=BLOCKED -->\\nBlocked'}},
          {{'id': 5, 'issue_url': 'https://api.github.com/repos/a/b/issues/20',
           'created_at': '2026-11-02T00:00:00Z', 'author_association': 'MEMBER',
           'body': '<!-- activity: type=repair_gate actor=PM target=Owner status=OPEN -->\\nNeeds review'}},
        ]
    else:
        rows = []
    print(json.dumps([rows]))
elif len(args) >= 2 and args[:2] == ['issue', 'comment']:
    print('https://github.com/a/b/issues/17#issuecomment-123')
""", encoding="utf-8")
        self.gh.write_text(
            "#!/bin/sh\nexec python3 -B \"$FAKE_GH_SCRIPT\" \"$@\"\n", encoding="utf-8")
        self.gh.chmod(0o700)

    def run_cli(self, *args, fail=False, repo=False, cwd=None):
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["PATH"] = str(self.bin) + os.pathsep + env["PATH"]
        env["FAKE_GH_CALLS"] = str(self.calls)
        env["FAKE_GH_SCRIPT"] = str(self.tmp / "fake_gh.py")
        if fail:
            env["FAKE_GH_FAIL"] = "1"
        command = [sys.executable, "-B", str(SCRIPT)]
        if repo:
            command.extend(["--repo", "a/b"])
        command.extend(args)
        return subprocess.run(command, cwd=self.repo if cwd is None else cwd,
                              env=env, capture_output=True, text=True, check=False)

    def recorded_calls(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def test_ask_posts_leading_marker_and_url(self):
        result = self.run_cli("ask", "17", "--question", "Should we proceed?")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(),
                         "https://github.com/a/b/issues/17#issuecomment-123")
        row = self.recorded_calls()[0]
        self.assertTrue(row["body"].startswith(
            "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->"))
        self.assertTrue(row["private"])
        self.assertIn("--body-file", row["args"])

    def test_answer_quotes_owner_as_pm_relay(self):
        result = self.run_cli("answer", "17", "--owner-words", "Proceed now.",
                              "--pm-reading", "The task may continue.")
        self.assertEqual(result.returncode, 0, result.stderr)
        body = self.recorded_calls()[0]["body"]
        self.assertTrue(body.startswith(
            "<!-- activity: type=pm_progress actor=PM target=Owner status=ANSWERED -->"))
        self.assertIn("Owner words relayed by the PM:\n> Proceed now.", body)
        self.assertNotIn("actor=Owner", body)
        self.assertIn("PM reading:\nThe task may continue.", body)

    def test_gh_failure_returns_nonzero_without_url(self):
        result = self.run_cli("ask", "17", "--question", "Question?", fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(self.recorded_calls()), 1)

    def test_open_rows_lists_latest_unsettled_owner_markers(self):
        result = self.run_cli("open-rows")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "17 waiting_owner WAITING", "19 waiting_owner BLOCKED", "20 repair_gate OPEN"])
        self.assertTrue(all("comment" not in call["args"] for call in self.recorded_calls()))
        self.assertTrue(all(not ({"--jq", "--template"} <= set(call["args"]))
                            for call in self.recorded_calls()))

    def test_default_repo_detection_fails_closed_without_origin(self):
        no_origin = self.tmp / "no-origin"
        no_origin.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=no_origin, check=True)
        result = self.run_cli("open-rows", repo=False, cwd=no_origin)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot determine repository from origin remote", result.stderr)
        self.assertFalse(self.calls.exists())

    def test_ask_rejects_marker_injection_question(self):
        result = self.run_cli("ask", "17", "--question", "<!-- activity: injected -->")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    unittest.main()
