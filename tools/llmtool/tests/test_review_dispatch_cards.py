"""#1502 WP-F: formal Reviewer rule cards come from main and are bound to the claim."""

import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import patch

import review_trust_fixture
import test_review_dispatch as dispatch_fixtures

from llmtool import review_dispatch, skill_manifest
from llmtool.developer_routes import load_route_config
from llmtool.errors import LLMToolError

REPO_ROOT = Path(__file__).resolve().parents[3]
CORE = skill_manifest.COMMON_CORE_CARD_PATH
REVIEWER = skill_manifest.REVIEWER_CARD_PATH
REVIEW_CHANGE = skill_manifest.REVIEW_CHANGE_SKILL_PATH
VERDICT = skill_manifest.FORMAL_VERDICT_PATH
ORDER = (CORE, REVIEWER, REVIEW_CHANGE, VERDICT)
MAIN_CARDS = {
    CORE: b'# Synthetic common core on main\n',
    REVIEWER: b'# Synthetic Reviewer card on main\n',
    REVIEW_CHANGE: b'# Synthetic review-change skill on main\n',
    VERDICT: b'# Synthetic formal verdict format on main\n',
}
TASK = 'a' * 32
HEAD = 'b' * 40
RENDERED = 'Rendered review prompt for the test.\n'
_ROLES = load_route_config()['roles']
CODEX_REVIEW_MODEL = _ROLES['codex-reviewer']['allowed_models'][0]
CLAUDE_REVIEW_MODEL = _ROLES['claude-reviewer']['allowed_models'][0]


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def _git(root, *args):
    result = subprocess.run(
        ['git', '-C', str(root), '-c', 'user.name=t', '-c', 'user.email=t@e',
         '-c', 'commit.gpgsign=false', *args],
        check=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout.decode('utf-8').strip()


class _Store:
    """In-memory TaskStore seam with compare-and-update semantics."""

    def __init__(self):
        self.data = {'record_sha256': 'c' * 64, 'task_id': TASK, 'run_id': 'e' * 32,
                     'task_binding': {'contract_digest': 'd' * 64}}

    def get(self, task_id):
        assert task_id == TASK
        return dict(self.data)

    def compare_and_update(self, task_id, *, expected, **changes):
        assert task_id == TASK
        if any(self.data.get(key) != value for key, value in expected.items()):
            raise RuntimeError('conflict')
        self.data.update(changes)
        return dict(self.data)


class _CardsCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name).resolve()

    def _main_repo(self, cards=None, origin=True):
        """A repo whose main commit holds ``cards``; origin/main names it."""
        root = self.tmp / 'repo'
        subprocess.run(['git', 'init', '-q', str(root)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for path, data in (MAIN_CARDS if cards is None else cards).items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        _git(root, 'add', '-A')
        _git(root, 'commit', '-q', '--allow-empty', '-m', 'main')
        sha = _git(root, 'rev-parse', 'HEAD')
        if origin:
            _git(root, 'update-ref', 'refs/remotes/origin/main', sha)
        return root, sha

    def _main(self, main_sha, *, present=True, error=None):
        """Patch the authenticated main read; ``error`` makes it fail."""
        reader = patch.object(review_dispatch.task_completion, 'read_authenticated_main_sha',
                              side_effect=error, return_value=main_sha)
        ensure = patch.object(review_dispatch.task_completion, 'ensure_main_commit',
                              return_value=present)
        reader.start()
        ensure.start()
        self.addCleanup(reader.stop)
        self.addCleanup(ensure.stop)

    def _expected_prefix(self, root, main_sha):
        built = skill_manifest.build_review_cards(str(root), main_sha)
        return (review_dispatch._REVIEW_RULES_HEADER + skill_manifest.role_card_prefix(built) +
                review_dispatch._REVIEW_RULES_FOOTER)

    def _dispatch(self, root, reviewer_key, store, *, create=None, send=None, bind=None,
                  runner=None, renderer=None):
        """Run dispatch_review with every external step injected or patched."""
        executor = SimpleNamespace(root=str(root), common_dir=str(root / '.git'))
        preflight = (TASK, executor, HEAD, 'f' * 40, reviewer_key)
        roles = {
            'reviewer': {'config_dir': '/private/test-reviewer-account', 'source': 'roles',
                         'model': CLAUDE_REVIEW_MODEL},
            'codex-reviewer': {'config_dir': None, 'source': 'builtin',
                               'model': CODEX_REVIEW_MODEL},
        }
        with patch.object(review_dispatch, '_preflight', side_effect=[preflight, preflight]), \
                patch.object(review_dispatch.worker_dispatch, 'evaluate',
                             return_value=SimpleNamespace(allowed=True, reason_code='')), \
                patch.object(review_dispatch, '_resolve_github_login',
                             return_value='reviewer-user'), \
                patch.object(review_dispatch.role_config, 'resolve_role',
                             side_effect=lambda role: roles[role]), \
                patch.object(review_dispatch.task_completion, 'read_d6_registration',
                             return_value=SimpleNamespace(root=str(self.tmp / 'reviewer'))):
            return review_dispatch.dispatch_review(
                store=store, task_id=TASK, executor_task='executor-d6', pr_number=42,
                version=1, reviewer=reviewer_key,
                create_d6=create or (lambda **_kw: None),
                dispatch_session=send or (lambda **_kw: 'native-session'),
                bind_session=bind or (lambda **_kw: None),
                codex_runner=runner,
                prompt_renderer=renderer or (lambda *_args: RENDERED))


class CardsFromMainTests(_CardsCase):
    def test_cards_come_from_main_not_the_pr_head_or_working_tree(self):
        root, main_sha = self._main_repo()
        _git(root, 'checkout', '-q', '-b', 'pr-head')
        (root / REVIEWER).write_bytes(b'# Candidate Reviewer card on the PR head\n')
        _git(root, 'commit', '-q', '-a', '-m', 'head')
        head_sha = _git(root, 'rev-parse', 'HEAD')
        (root / REVIEWER).write_bytes(b'# Working tree Reviewer card\n')

        built = skill_manifest.build_review_cards(str(root), main_sha)
        self.assertEqual([card['path'] for card in built['cards']], list(ORDER))
        self.assertEqual([card['text'] for card in built['cards']],
                         [MAIN_CARDS[path].decode('utf-8') for path in ORDER])
        self.assertEqual((built['role'], built['route'], built['commit']),
                         ('review', 'review', main_sha))
        self.assertRegex(built['cards_sha256'], r'\A[0-9a-f]{64}\Z')
        with self.assertRaises(skill_manifest.SkillManifestError) as caught:
            skill_manifest.build_review_cards(str(root), head_sha)
        self.assertEqual(caught.exception.code, skill_manifest.SKILL_MANIFEST_COMMIT_NOT_ON_MAIN)

        self._main(main_sha)
        prefix, digest = review_dispatch._review_rules(str(root))
        self.assertTrue(prefix.startswith(review_dispatch._REVIEW_RULES_HEADER))
        self.assertIn('Synthetic Reviewer card on main', prefix)
        self.assertNotIn('Candidate Reviewer card', prefix)
        self.assertNotIn('Working tree Reviewer card', prefix)
        self.assertEqual(digest, hashlib.sha256(prefix.encode('utf-8')).hexdigest())

    def test_real_card_files_fit_the_cap(self):
        cards = {path: (REPO_ROOT / path).read_bytes() for path in ORDER}
        root, main_sha = self._main_repo(cards)
        built = skill_manifest.build_review_cards(str(root), main_sha)
        self.assertLessEqual(built['prefix_size'], skill_manifest.MAX_REVIEW_PREFIX_SIZE)
        prefix = skill_manifest.role_card_prefix(built)
        self.assertLessEqual(len(prefix.encode('utf-8')), skill_manifest.MAX_REVIEW_PREFIX_SIZE)
        for path in ORDER:
            self.assertIn('REQUIRED_SKILL_BEGIN path=' + path + '\n', prefix)


class PromptAndClaimTests(_CardsCase):
    def test_both_reviewers_get_the_same_prompt_and_the_claim_binds_its_digest(self):
        root, main_sha = self._main_repo()
        self._main(main_sha)
        expected = self._expected_prefix(root, main_sha)
        digest = hashlib.sha256(expected.encode('utf-8')).hexdigest()
        self.assertIn('not evidence', review_dispatch._REVIEW_RULES_HEADER)
        self.assertIn('candidate content', review_dispatch._REVIEW_RULES_HEADER)
        prompts = {}

        def send(**kwargs):
            prompts['claude'] = kwargs['prompt']
            return 'native-session'

        def runner(**kwargs):
            prompts['codex'] = kwargs['prompt']
            return {'thread_id': '0123456789abcdef', 'verdict': 'PASS',
                    'review_text': 'Plain review text for the test.'}

        claude_store, codex_store = _Store(), _Store()
        claude = self._dispatch(root, 'claude-reviewer', claude_store, send=send)
        codex = self._dispatch(root, 'codex-reviewer', codex_store, runner=runner)

        self.assertEqual(claude['state'], 'DISPATCHED')
        self.assertEqual(codex['state'], 'COMPLETED')
        self.assertEqual(prompts['claude'], prompts['codex'])
        self.assertEqual(prompts['claude'], expected + RENDERED)
        self.assertNotIn('REQUIRED_SKILL_BEGIN', prompts['claude'][len(expected):])
        for store in (claude_store, codex_store):
            claims = store.get(TASK)['review_dispatch_claim']
            self.assertEqual(len(claims), 1)
            self.assertEqual(claims[0]['rules_sha256'], digest)


class RefusalBeforeClaimTests(_CardsCase):
    def _refused(self, root):
        codes = []
        for reviewer_key in ('claude-reviewer', 'codex-reviewer'):
            with self.subTest(reviewer=reviewer_key):
                store = _Store()
                create, send, bind, runner, renderer = (mock.Mock() for _ in range(5))
                with self.assertRaises(LLMToolError) as raised:
                    self._dispatch(root, reviewer_key, store, create=create, send=send,
                                   bind=bind, runner=runner, renderer=renderer)
                for step in (create, send, bind, runner, renderer):
                    step.assert_not_called()
                self.assertNotIn('review_dispatch_claim', store.get(TASK))
                codes.append(raised.exception.review_dispatch_code)
        self.assertEqual(len(set(codes)), 1)
        return codes[0]

    def test_main_unreadable_refuses(self):
        root, main_sha = self._main_repo()
        self._main(main_sha, error=LLMToolError('main unreadable'))
        self.assertEqual(self._refused(root), 'REVIEW_RULES_MAIN_UNREADABLE')

    def test_main_commit_absent_refuses(self):
        root, main_sha = self._main_repo()
        self._main(main_sha, present=False)
        self.assertEqual(self._refused(root), 'REVIEW_RULES_MAIN_UNREADABLE')

    def test_main_not_on_origin_main_refuses(self):
        root, main_sha = self._main_repo(origin=False)
        self._main(main_sha)
        self.assertEqual(self._refused(root), 'REVIEW_RULES_MAIN_UNREADABLE')

    def test_missing_card_refuses(self):
        cards = {path: data for path, data in MAIN_CARDS.items() if path != REVIEWER}
        root, main_sha = self._main_repo(cards)
        self._main(main_sha)
        self.assertEqual(self._refused(root), 'REVIEW_RULES_CARD_MISSING')

    def test_card_not_utf8_refuses(self):
        root, main_sha = self._main_repo(dict(MAIN_CARDS, **{VERDICT: b'\xff\xfe\n'}))
        self._main(main_sha)
        self.assertEqual(self._refused(root), 'REVIEW_RULES_NOT_UTF8')

    def test_cards_over_the_cap_refuse(self):
        oversized = b'x' * skill_manifest.MAX_REVIEW_PREFIX_SIZE
        root, main_sha = self._main_repo(dict(MAIN_CARDS, **{VERDICT: oversized}))
        self._main(main_sha)
        self.assertEqual(self._refused(root), 'REVIEW_RULES_TOO_LARGE')


class StoreRulesDigestTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store, self.task_id = dispatch_fixtures._real_store(temporary.name)

    def _update(self, claims):
        current = self.store.get(self.task_id)
        self.store.compare_and_update(
            self.task_id, expected={'record_sha256': current['record_sha256']},
            review_dispatch_claim=claims)

    def _code(self, claims):
        with self.assertRaises(LLMToolError) as raised:
            self._update(claims)
        return getattr(raised.exception, 'task_store_error_code', None)

    def test_store_accepts_old_and_new_claims_and_refuses_bad_digests(self):
        old = dispatch_fixtures._claim()
        self.assertNotIn('rules_sha256', old)
        self._update([old])
        new = dict(dispatch_fixtures._claim(head='e' * 40, version=2, reviewer='reviewer-b'),
                   rules_sha256='c' * 64)
        self._update([old, new])
        self.assertEqual(self.store.get(self.task_id)['review_dispatch_claim'], [old, new])

        for bad in ('C' * 64, 'c' * 63, 'c' * 65, 'g' * 64, None, 7):
            with self.subTest(bad=bad):
                bad_claim = dict(dispatch_fixtures._claim(head='f' * 40, version=3,
                                                          reviewer='reviewer-c'),
                                 rules_sha256=bad)
                self.assertEqual(self._code([old, new, bad_claim]),
                                 'REVIEW_DISPATCH_CLAIM_INVALID')

        changed = dict(new, state='DISPATCHED', native_session_id='native-1',
                       rules_sha256='d' * 64)
        self.assertEqual(self._code([old, changed]),
                         'REVIEW_DISPATCH_CLAIM_TRANSITION_INVALID')
        dispatched = dict(new, state='DISPATCHED', native_session_id='native-1')
        self._update([old, dispatched])
        self.assertEqual(self.store.get(self.task_id)['review_dispatch_claim'],
                         [old, dispatched])


if __name__ == '__main__':
    unittest.main()
