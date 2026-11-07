"""#369 PR-10a guard: production Python gains no new Owner-specific literal.

Scans every ``.py`` file under ``tools/llmtool/llmtool`` and
``tools/llmtool/scripts`` plus ``tools/llmtool/llmtool.py``. ``tests/``,
``eval/`` and ``build/`` are not scanned. Files are located from this test
file, never from the cwd or HOME. The test reads no trust.json, starts no
subprocess and uses no network.

Today's hits sit on an exact, labelled allowlist. A new hit fails, and so does
an allowlist entry that no longer matches any line, so the list can only
shrink.
"""

import re
import unittest
from pathlib import Path

from llmtool import github_identity


_LLMTOOL_ROOT = Path(__file__).resolve().parent.parent
_REPO_PREFIX = 'tools/llmtool/'
_SCAN_DIRS = ('llmtool', 'scripts')
_SCAN_FILES = ('llmtool.py',)
_SKIP_PARTS = frozenset(('__pycache__',))

# Hit rules. Each matches a concrete Owner-specific literal on one line.
_STATIC_RULES = (
    # An Owner home path.
    re.compile(r'/Users/|/home/'),
    # A concrete GitHub repository URL or SSH origin: github.com (also the
    # regex spelling github\.com) followed by a literal owner/name.
    re.compile(r'github\\*\.com[/:][A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+'),
    # A fixed issue reference: issues/ followed by a digit or by a regex
    # alternation group of issue numbers.
    re.compile(r'issues/(?:[0-9]|\(\?:)'),
    # A runner name.
    re.compile(r'-self-runner|-self-action'),
)

LEGACY_VERIFY = 'LEGACY_VERIFY'
RETAIN_RELEASE_REFS = 'RETAIN_RELEASE_REFS'
GW_AGENT_POLICY_REFS = 'GW_AGENT_POLICY_REFS'
TRIAGE_AUTHORIZER = 'TRIAGE_AUTHORIZER'
GITHUB_LOGINS = 'GITHUB_LOGINS'
LABELS = frozenset((LEGACY_VERIFY, RETAIN_RELEASE_REFS, GW_AGENT_POLICY_REFS,
                    TRIAGE_AUTHORIZER, GITHUB_LOGINS))

# Exact (repository-relative path, stripped line text) -> label.
# LEGACY_VERIFY: legacy constants kept for ``llmtool trust verify-legacy``,
# removed by #369 PR-10b.
_REVIEW_BUNDLE = 'tools/llmtool/llmtool/review_bundle.py'
_REVIEW_ORIGINS = 'tools/llmtool/llmtool/review_origins.py'
_TASK_COMPLETION = 'tools/llmtool/llmtool/task_completion.py'
_TRIAGE_WORKER = 'tools/llmtool/llmtool/test_triage_worker.py'
_WORKER_DISPATCH = 'tools/llmtool/llmtool/worker_dispatch.py'
_GITHUB_IDENTITY = 'tools/llmtool/llmtool/github_identity.py'
_D6_LIFECYCLE = 'tools/llmtool/scripts/d6_worktree_lifecycle.py'

ALLOWLIST = {
    (_REVIEW_BUNDLE,
     "'/Users/davidsun/git/davidcataiporject/davidcattoolsproject',"):
        LEGACY_VERIFY,
    (_REVIEW_BUNDLE,
     "'/Users/davidsun/.local/share/llmtool-dev-workspaces/davidcattoolsproject'))"):
        LEGACY_VERIFY,
    (_REVIEW_ORIGINS,
     "'git@github.com:davidcatcat/davidcattoolsproject.git',"):
        LEGACY_VERIFY,
    (_REVIEW_ORIGINS,
     "'https://github.com/davidcatcat/davidcattoolsproject.git',"):
        LEGACY_VERIFY,
    (_TASK_COMPLETION,
     "_CI_RUNNER_NAMES = frozenset(('davidproject-self-action', "
     "'davidproject-self-action-2'))"):
        LEGACY_VERIFY,
    (_TASK_COMPLETION,
     "'ec2-self-runner', 'ec2-self-runner-2', 'ec2-self-runner-3',"):
        LEGACY_VERIFY,
    (_TASK_COMPLETION,
     "'ec2-self-runner-4'))"):
        LEGACY_VERIFY,
    (_D6_LIFECYCLE,
     '"/Users/davidsun/.local/share/llmtool-dev-workspaces/"'):
        LEGACY_VERIFY,
    (_D6_LIFECYCLE,
     '"git@github.com:davidcatcat/davidcattoolsproject.git",'):
        LEGACY_VERIFY,
    (_D6_LIFECYCLE,
     '"https://github.com/davidcatcat/davidcattoolsproject.git",'):
        LEGACY_VERIFY,
    (_D6_LIFECYCLE,
     '"/Users/davidsun/git/davidcataiporject/davidcattoolsproject")'):
        LEGACY_VERIFY,
    (_D6_LIFECYCLE,
     r'r"https://github\.com/davidcatcat/davidcattoolsproject/issues/(?:289|352)"'):
        LEGACY_VERIFY,
    (_D6_LIFECYCLE,
     r'r"https://github\.com/" + re.escape(trust["slug"]) + r"/issues/(?:" +'):
        RETAIN_RELEASE_REFS,
    (_D6_LIFECYCLE,
     r'r"https://github\.com/davidcatcat/davidcattoolsproject/issues/(?:289|529)"'):
        RETAIN_RELEASE_REFS,
    (_WORKER_DISPATCH,
     "'https://github.com/davidcatcat/davidcattoolsproject/issues/120')"):
        GW_AGENT_POLICY_REFS,
    (_WORKER_DISPATCH,
     "'https://github.com/davidcatcat/davidcattoolsproject/'"):
        GW_AGENT_POLICY_REFS,
    (_WORKER_DISPATCH,
     "'issues/120#issuecomment-6594101520')"):
        GW_AGENT_POLICY_REFS,
    (_WORKER_DISPATCH,
     "'https://github.com/davidcatcat/davidcattoolsproject/issues/120')) or"):
        GW_AGENT_POLICY_REFS,
    (_TRIAGE_WORKER,
     r"r'https://github\.com/davidcatcat/davidcattoolsproject/issues/120"
     r"#issuecomment-[0-9]{1,20}')"):
        TRIAGE_AUTHORIZER,
    (_GITHUB_IDENTITY,
     '_GITHUB_LOGINS = ("davidcatcat", "ddavidccatssun")'):
        GITHUB_LOGINS,
}


def find_hits(text, ai_login):
    """Return ``[(line_number, stripped_line)]`` for Owner-specific literals.

    A plain function on text: no file, environment or trust access.
    """
    rules = _STATIC_RULES
    if ai_login:
        rules = rules + (re.compile(re.escape(ai_login)),)
    hits = []
    for number, line in enumerate(text.split('\n'), 1):
        if any(rule.search(line) for rule in rules):
            hits.append((number, line.strip()))
    return hits


def find_violations(rel_path, text, ai_login, allowlist):
    """Return ``(violations, matched_keys)`` for one file's text.

    ``violations`` is ``[(rel_path, line_number, stripped_line)]`` for hits
    not on the allowlist; ``matched_keys`` is the set of allowlist keys hit.
    """
    violations = []
    matched = set()
    for number, stripped in find_hits(text, ai_login):
        key = (rel_path, stripped)
        if key in allowlist:
            matched.add(key)
        else:
            violations.append((rel_path, number, stripped))
    return violations, matched


def stale_entries(allowlist, matched_keys):
    """Return allowlist keys that matched no scanned line, sorted."""
    return sorted(set(allowlist) - set(matched_keys))


def format_violation(violation):
    rel_path, number, stripped = violation
    return f'{rel_path}:{number}: {stripped}'


def production_files(root=_LLMTOOL_ROOT):
    """Return ``[(repository-relative path, Path)]`` for the scanned files."""
    found = []
    for name in _SCAN_DIRS:
        for path in (root / name).rglob('*.py'):
            relative = path.relative_to(root)
            if _SKIP_PARTS.intersection(relative.parts) or not path.is_file():
                continue
            found.append((_REPO_PREFIX + relative.as_posix(), path))
    for name in _SCAN_FILES:
        path = root / name
        if path.is_file():
            found.append((_REPO_PREFIX + name, path))
    return sorted(found)


class OwnerLiteralGuardTest(unittest.TestCase):
    def test_production_python_has_no_new_owner_literal(self):
        files = production_files()
        scanned = {rel for rel, _ in files}
        self.assertIn('tools/llmtool/llmtool.py', scanned)
        self.assertIn(_GITHUB_IDENTITY, scanned)
        self.assertIn(_D6_LIFECYCLE, scanned)
        for rel in scanned:
            self.assertFalse(rel.startswith(('tools/llmtool/tests/',
                                             'tools/llmtool/eval/',
                                             'tools/llmtool/build/')), rel)
            self.assertNotIn('/__pycache__/', rel)
        login = github_identity.ai_login()
        self.assertTrue(login)
        violations = []
        matched = set()
        for rel, path in files:
            text = path.read_text(encoding='utf-8')
            found, hit_keys = find_violations(rel, text, login, ALLOWLIST)
            violations.extend(found)
            matched |= hit_keys
        self.assertEqual(
            violations, [],
            'Owner-specific literal outside the #369 allowlist:\n' +
            '\n'.join(format_violation(item) for item in violations))
        stale = stale_entries(ALLOWLIST, matched)
        self.assertEqual(
            stale, [],
            'Allowlist entries no longer match any line; remove them:\n' +
            '\n'.join(f'{rel}: {text}' for rel, text in stale))

    def test_allowlist_labels_are_known(self):
        for key, label in ALLOWLIST.items():
            self.assertIn(label, LABELS, key)
            rel, text = key
            self.assertTrue(rel.startswith(_REPO_PREFIX), key)
            self.assertEqual(text, text.strip(), key)
            self.assertTrue(text, key)


class FindHitsTest(unittest.TestCase):
    LOGIN = 'example-ai-bot'

    def assertHit(self, line):
        self.assertEqual(find_hits(line, self.LOGIN), [(1, line.strip())], line)

    def assertNoHit(self, line):
        self.assertEqual(find_hits(line, self.LOGIN), [], line)

    def test_new_owner_path_is_reported(self):
        self.assertHit("ROOT = Path('/Users/someone/git/project')")
        self.assertHit("    CACHE = '/home/someone/.cache/tool'")

    def test_repo_url_and_origin_are_reported(self):
        self.assertHit("URL = 'https://github.com/acme/widget'")
        self.assertHit("ORIGIN = 'git@github.com:acme/widget.git'")
        self.assertHit(r"PATTERN = r'https://github\.com/acme/widget/pull/[0-9]+'")
        self.assertHit(r"PATTERN = 'https://github\\.com/acme/widget'")

    def test_issue_reference_is_reported(self):
        self.assertHit("REF = 'https://example.invalid/x/y/issues/42'")
        self.assertHit("REF = 'issues/7#issuecomment-1'")
        self.assertHit(r"REF = r'/issues/(?:1|2)#issuecomment-[0-9]+'")

    def test_runner_name_is_reported(self):
        self.assertHit("RUNNERS = ('box-self-runner',)")
        self.assertHit("RUNNERS = ('mac-self-action-3',)")

    def test_ai_login_is_reported(self):
        self.assertHit("LOGIN = 'example-ai-bot'")
        self.assertEqual(find_hits("LOGIN = 'example-ai-bot'", ''), [])

    def test_generic_slug_code_is_not_reported(self):
        for line in (
                "url = f'https://github.com/{slug}/issues/{number}'",
                "url = 'https://github.com/' + slug + '/issues/' + str(issue)",
                r"pattern = r'https://github\.com/' + re.escape(slug) + r'/issues/' + str(n)",
                r"pattern = r'https://github\.com/' + slug + r'/(?:issues/' + str(n)",
                "origins = {'git@github.com:' + slug + '.git'}",
                "env['KEY'] = 'url.https://github.com/.insteadOf'",
                r"api = r'https://api\.github\.com/repos/' + re.escape(slug) + r'/'",
                "comments = get(f'issues/{pr_number}/comments?per_page=100')",
                "runner = config['runner_name']",
                "home = Path.home()",
        ):
            self.assertNoHit(line)

    def test_line_numbers_count_every_line(self):
        text = "a = 1\n\nb = '/Users/x'\nc = 2\r\nd = 'ci-self-runner'\n"
        self.assertEqual(find_hits(text, self.LOGIN),
                         [(3, "b = '/Users/x'"), (5, "d = 'ci-self-runner'")])


class FindViolationsTest(unittest.TestCase):
    LOGIN = 'example-ai-bot'
    REL = 'tools/llmtool/llmtool/example.py'

    def test_allowlisted_line_is_not_reported(self):
        line = "    LEGACY = 'https://github.com/acme/widget.git'"
        allowlist = {(self.REL, line.strip()): LEGACY_VERIFY}
        violations, matched = find_violations(self.REL, line, self.LOGIN, allowlist)
        self.assertEqual(violations, [])
        self.assertEqual(matched, {(self.REL, line.strip())})
        self.assertEqual(stale_entries(allowlist, matched), [])

    def test_allowlist_is_exact_per_path_and_text(self):
        line = "LEGACY = 'https://github.com/acme/widget.git'"
        allowlist = {('tools/llmtool/llmtool/other.py', line): LEGACY_VERIFY,
                     (self.REL, line + ' # changed'): LEGACY_VERIFY}
        violations, matched = find_violations(self.REL, line, self.LOGIN, allowlist)
        self.assertEqual(violations, [(self.REL, 1, line)])
        self.assertEqual(matched, set())

    def test_violation_names_path_and_line_number(self):
        text = "x = 1\nROOT = '/Users/someone/project'\n"
        violations, _ = find_violations(self.REL, text, self.LOGIN, {})
        self.assertEqual(len(violations), 1)
        message = format_violation(violations[0])
        self.assertIn(self.REL + ':2:', message)

    def test_unmatched_allowlist_entry_is_stale(self):
        allowlist = {(self.REL, "OLD = '/Users/someone/gone'"): LEGACY_VERIFY}
        violations, matched = find_violations(self.REL, 'x = 1\n', self.LOGIN, allowlist)
        self.assertEqual(violations, [])
        self.assertEqual(stale_entries(allowlist, matched),
                         [(self.REL, "OLD = '/Users/someone/gone'")])


if __name__ == '__main__':
    unittest.main()
