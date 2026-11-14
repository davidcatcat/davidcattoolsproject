"""#1502 WP-F: AI Debug rule cards come from main and are bound to each Debug entry."""

import hashlib
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import patch

import review_trust_fixture
import test_task_store as task_store_fixtures

from llmtool import high_debug, skill_manifest
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore

REPO_ROOT = Path(__file__).resolve().parents[3]
CORE = skill_manifest.COMMON_CORE_CARD_PATH
DEBUG = skill_manifest.DEBUG_CARD_PATH
PROCEDURE = skill_manifest.HIGH_DEBUG_REPORT_PATH
MAIN_CARDS = {
    CORE: b'# Synthetic common core on main\n',
    DEBUG: b'# Synthetic Debug card on main\n',
    PROCEDURE: b'# Synthetic report procedure on main\n',
}
REPORT = ''.join('## ' + heading + '\nDetails.\n'
                 for heading in high_debug.REPORT_SECTIONS) + 'RCA_CONFIRMED'
TASK_ID = 'a' * 32
FIXED_TAIL = 'Diagnose only; do not edit files.'


def _git(root, *args):
    result = subprocess.run(
        ['git', '-C', str(root), '-c', 'user.name=t', '-c', 'user.email=t@e',
         '-c', 'commit.gpgsign=false', *args],
        check=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout.decode('utf-8').strip()


class _Store:
    def __init__(self):
        self.entries, self.events = [], []

    def get(self, task_id):
        return {'task_id': task_id, 'run_id': 'b' * 32,
                'review_repair_claims': [{'family': 'family', 'pr': 17}] * 2,
                'completion': {'executor_task_id': 'executor',
                               'evidence': {'common_dir': '/tmp/ignored'}},
                'high_debug': list(self.entries)}

    @staticmethod
    def _completion_now():
        return '2026-11-14T00:00:00Z'

    def append_high_debug(self, _task_id, entry):
        self.entries.append(entry)

    def record_completion_event(self, _task_id, **event):
        self.events.append(event)
        return {'created': True}


class _CardsCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name).resolve()
        for target, name, value in (
                (high_debug, '_post_github_record', True),
                (high_debug.task_store, 'default_root', self.tmp / 'tasks')):
            patcher = patch.object(target, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _main_repo(self, cards=None, origin=True):
        """A repo whose main commit holds ``cards``; origin/main names it."""
        root = self.tmp / 'repo'
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
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
        reader = patch.object(high_debug.task_completion, 'read_authenticated_main_sha',
                              side_effect=error, return_value=main_sha)
        ensure = patch.object(high_debug.task_completion, 'ensure_main_commit',
                              return_value=present)
        reader.start()
        ensure.start()
        self.addCleanup(reader.stop)
        self.addCleanup(ensure.stop)

    def _run(self, root, route, runner, *, create=None, store=None):
        registration = SimpleNamespace(result_sha='a' * 40, root=str(root),
                                       common_dir=str(root / '.git'))
        store = store or _Store()
        create = create or (lambda **_kwargs: str(root))
        resolve = (lambda role: {'role': role, 'source': 'roles', 'account': 'acct',
                                 'config_dir': '/safe/claude', 'provider': 'claude'})
        with patch.object(high_debug.worker_dispatch, 'evaluate',
                          return_value=SimpleNamespace(allowed=True)), \
             patch.object(high_debug.role_config, 'resolve_role', side_effect=resolve), \
             patch.object(high_debug.task_completion, 'collect_git_snapshot',
                          return_value=SimpleNamespace(head='a' * 40)), \
             redirect_stdout(StringIO()):
            try:
                high_debug.run_high_debug(
                    store=store, task_id=TASK_ID, executor_task='executor', pr_number=17,
                    family='family', failed_corrections=2, runner=runner,
                    create_d6=create, repo_root=str(root),
                    read_registration=lambda *_args, **_kwargs: registration, route=route)
                code = None
            except LLMToolError as error:
                code = error.high_debug_code
        return code, store


class CardsFromMainTests(_CardsCase):
    def test_cards_come_from_main_not_the_pr_head_or_working_tree(self):
        root, main_sha = self._main_repo()
        _git(root, 'checkout', '-q', '-b', 'pr-head')
        (root / DEBUG).write_bytes(b'# Candidate Debug card on the PR head\n')
        _git(root, 'commit', '-q', '-a', '-m', 'head')
        head_sha = _git(root, 'rev-parse', 'HEAD')
        (root / DEBUG).write_bytes(b'# Working tree Debug card\n')

        built = skill_manifest.build_debug_cards(str(root), main_sha)
        self.assertEqual([card['path'] for card in built['cards']], [CORE, DEBUG, PROCEDURE])
        self.assertEqual([card['text'] for card in built['cards']],
                         [MAIN_CARDS[path].decode('utf-8') for path in (CORE, DEBUG, PROCEDURE)])
        self.assertEqual((built['role'], built['route'], built['commit']),
                         ('debug', 'debug', main_sha))
        self.assertRegex(built['cards_sha256'], r'\A[0-9a-f]{64}\Z')
        with self.assertRaises(skill_manifest.SkillManifestError) as caught:
            skill_manifest.build_debug_cards(str(root), head_sha)
        self.assertEqual(caught.exception.code, skill_manifest.SKILL_MANIFEST_COMMIT_NOT_ON_MAIN)

        self._main(main_sha)
        prefix, digest = high_debug._debug_rules(str(root))
        self.assertIn('Synthetic Debug card on main', prefix)
        self.assertNotIn('Candidate Debug card', prefix)
        self.assertNotIn('Working tree Debug card', prefix)
        self.assertEqual(digest, hashlib.sha256(prefix.encode('utf-8')).hexdigest())

    def test_debug_cards_digest_differs_from_developer_cards(self):
        root, main_sha = self._main_repo(dict(
            MAIN_CARDS, **{skill_manifest.DEVELOPER_CARD_PATH: b'# Synthetic Developer card\n'}))
        debug = skill_manifest.build_debug_cards(str(root), main_sha)
        developer = skill_manifest.build_role_cards(str(root), main_sha, 'claude-developer')
        self.assertNotEqual(debug['cards_sha256'], developer['cards_sha256'])

    def test_real_card_files_fit_the_cap_and_pass_the_screen(self):
        cards = {path: (REPO_ROOT / path).read_bytes() for path in (CORE, DEBUG, PROCEDURE)}
        root, main_sha = self._main_repo(cards)
        built = skill_manifest.build_debug_cards(str(root), main_sha)
        self.assertLessEqual(built['prefix_size'], skill_manifest.MAX_DEBUG_PREFIX_SIZE)
        self._main(main_sha)
        prefix, _digest = high_debug._debug_rules(str(root))
        for path in (CORE, DEBUG, PROCEDURE):
            self.assertIn('REQUIRED_SKILL_BEGIN path=' + path + '\n', prefix)


class PromptAndEntryTests(_CardsCase):
    def test_both_routes_get_the_same_prompt_starting_with_the_rules(self):
        root, main_sha = self._main_repo()
        self._main(main_sha)
        built = skill_manifest.build_debug_cards(str(root), main_sha)
        expected = (high_debug._DEBUG_RULES_HEADER + skill_manifest.role_card_prefix(built) +
                    high_debug._DEBUG_RULES_FOOTER)
        digest = hashlib.sha256(expected.encode('utf-8')).hexdigest()
        prompts = {}

        def codex(*, prompt, cwd, output_path):
            prompts['codex'] = prompt
            Path(output_path).write_text(REPORT, encoding='utf-8')
            return subprocess.CompletedProcess([], 0, stdout=b'', stderr=b'')

        def claude(*, prompt, cwd, output_path):
            prompts['claude'] = prompt
            raise RuntimeError('stop after the prompt is captured')

        code, codex_store = self._run(root, 'codex', codex)
        self.assertIsNone(code)
        code, claude_store = self._run(root, 'claude', claude)
        self.assertEqual(code, 'HIGH_DEBUG_RUN_FAILED')

        self.assertEqual(prompts['codex'], prompts['claude'])
        prompt = prompts['codex']
        self.assertTrue(prompt.startswith(expected))
        self.assertIn('not evidence', expected)
        fixed = prompt[len(expected):]
        self.assertTrue(fixed.startswith('Task: ' + TASK_ID + '\n'))
        self.assertTrue(fixed.endswith(FIXED_TAIL))
        self.assertIn('Evidence:\n', fixed)
        self.assertNotIn('REQUIRED_SKILL_BEGIN', fixed)
        self.assertEqual(codex_store.entries[0]['status'], 'RCA_CONFIRMED')
        self.assertEqual(codex_store.entries[0]['rules_sha256'], digest)
        self.assertEqual(claude_store.entries[0]['status'], 'RUN_FAILED')
        self.assertEqual(claude_store.entries[0]['rules_sha256'], digest)


class RefusalBeforeD6Tests(_CardsCase):
    def _refused(self, root):
        create, runner = mock.Mock(), mock.Mock()
        store = _Store()
        code, _store = self._run(root, 'codex', runner, create=create, store=store)
        create.assert_not_called()
        runner.assert_not_called()
        self.assertEqual(store.entries, [])
        return code

    def test_missing_card_refuses(self):
        cards = {path: data for path, data in MAIN_CARDS.items() if path != DEBUG}
        root, main_sha = self._main_repo(cards)
        self._main(main_sha)
        self.assertEqual(self._refused(root), 'HIGH_DEBUG_RULES_CARD_MISSING')

    def test_main_unreadable_refuses(self):
        root, main_sha = self._main_repo()
        self._main(main_sha, error=LLMToolError('main unreadable'))
        self.assertEqual(self._refused(root), 'HIGH_DEBUG_RULES_MAIN_UNREADABLE')

    def test_main_commit_absent_refuses(self):
        root, main_sha = self._main_repo()
        self._main(main_sha, present=False)
        self.assertEqual(self._refused(root), 'HIGH_DEBUG_RULES_MAIN_UNREADABLE')

    def test_main_not_on_origin_main_refuses(self):
        root, main_sha = self._main_repo(origin=False)
        self._main(main_sha)
        self.assertEqual(self._refused(root), 'HIGH_DEBUG_RULES_MAIN_UNREADABLE')

    def test_card_not_utf8_refuses(self):
        root, main_sha = self._main_repo(dict(MAIN_CARDS, **{DEBUG: b'\xff\xfe\n'}))
        self._main(main_sha)
        self.assertEqual(self._refused(root), 'HIGH_DEBUG_RULES_NOT_UTF8')

    def test_cards_over_the_cap_refuse(self):
        oversized = b'x' * skill_manifest.MAX_DEBUG_PREFIX_SIZE
        root, main_sha = self._main_repo(dict(MAIN_CARDS, **{PROCEDURE: oversized}))
        self._main(main_sha)
        self.assertEqual(self._refused(root), 'HIGH_DEBUG_RULES_TOO_LARGE')

    def test_screen_refusal_refuses(self):
        root, main_sha = self._main_repo()
        self._main(main_sha)
        screened = []

        def screen(text):
            screened.append(text)
            return True

        with patch.object(high_debug, '_emitted_sensitive', side_effect=screen):
            self.assertEqual(self._refused(root), 'HIGH_DEBUG_RULES_SENSITIVE')
        self.assertEqual(len(screened), 1)
        self.assertTrue(screened[0].startswith(high_debug._DEBUG_RULES_HEADER))


class StoreRulesDigestTests(unittest.TestCase):
    def _entry(self, attempt):
        return high_debug._failed_entry(
            family='test-family', failed_corrections=2, early_reason=None,
            head='a' * 40, d6=f'executor-hd{attempt}-aaaaaaa', code='HIGH_DEBUG_RUN_FAILED',
            observed='UNKNOWN', effort='UNKNOWN', usage='UNKNOWN',
            recorded_at='2026-11-14T00:00:00Z')

    def test_store_accepts_old_and_new_entries_and_refuses_bad_digests(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory).resolve() / 'tasks')
            review_trust_fixture.install_for_case(self)
            task = task_store_fixtures.TaskStoreTests._codex_dispatch_task(self)
            store.create(task)
            old = self._entry(1)
            self.assertNotIn('rules_sha256', old)
            store.append_high_debug(task['task_id'], old)
            new = dict(self._entry(2), rules_sha256='c' * 64)
            store.append_high_debug(task['task_id'], new)
            self.assertEqual(store.get(task['task_id'])['high_debug'], [old, new])
            for bad in ('C' * 64, 'c' * 63, 'c' * 65, 'g' * 64, None, 7):
                with self.subTest(bad=bad):
                    with self.assertRaises(LLMToolError) as caught:
                        store.append_high_debug(task['task_id'],
                                                dict(self._entry(3), rules_sha256=bad))
                    self.assertEqual(caught.exception.task_store_error_code,
                                     'HIGH_DEBUG_INVALID')
            self.assertEqual(store.get(task['task_id'])['high_debug'], [old, new])


if __name__ == '__main__':
    unittest.main()
