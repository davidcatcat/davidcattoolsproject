"""Tests for commit-pinned governance manifests."""

import errno
import hashlib
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from types import SimpleNamespace

from llmtool.skill_manifest import (
    COMMON_CORE_CARD_PATH,
    DEVELOPER_CARD_PATH,
    GIT_READ_TIMEOUT_SECONDS,
    MAX_DEVELOPER_PREFIX_SIZE,
    MAX_SKILL_FILE_SIZE,
    SKILL_MANIFEST_COMMIT_INVALID,
    SKILL_MANIFEST_COMMIT_NOT_LOCAL,
    SKILL_MANIFEST_COMMIT_NOT_ON_MAIN,
    SKILL_MANIFEST_GATE_INVALID,
    SKILL_MANIFEST_MAIN_REF_INVALID,
    SKILL_MANIFEST_NOT_FILE,
    SKILL_MANIFEST_NOT_UTF8,
    SKILL_MANIFEST_PATH_MISSING,
    SKILL_MANIFEST_PREFIX_TOO_LARGE,
    SKILL_MANIFEST_ROLE_INVALID,
    SKILL_MANIFEST_ROUTE_INVALID,
    SKILL_MANIFEST_TOO_LARGE,
    SkillManifestError,
    build_debug_cards,
    build_manifest,
    build_review_cards,
    build_role_cards,
    role_card_routes,
)


FILES = {
    'AGENTS.md': b'governance\n',
    'CLAUDE.md': b'claude governance\n',
    'skills/role-cards/common-core.md': b'common core\n',
    'skills/role-cards/developer.md': b'developer card\n',
    'skills/ai-team-delivery/SKILL.md': b'delivery\n',
    'skills/ai-team-delivery/pm-continuation.md': b'pm\n',
    'skills/ai-team-delivery/pm-continuation-reference.md': b'reference\n',
    'skills/review-change/SKILL.md': b'review\n',
    'skills/ai-team-delivery/formal-verdict.md': b'verdict\n',
    'skills/safe-git/SKILL.md': b'git\n',
}
# Every Developer route's common core is the common-core card (#1502 WP-F2a,
# WP-F3 for OpenCode and gw-agent); none uses AGENTS.md as its core.
COMMON_CORE_ROUTES = ('agy', 'claude-developer', 'gw-agent', 'opencode-developer')
GIT_READ_PREFIX = (
    'git', '--no-replace-objects', '-c', 'core.useReplaceRefs=false',
)


class SkillManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = self.temp.name
        self._git('init', '-q')
        self._git('config', 'user.email', 'manifest-tests@example.invalid')
        self._git('config', 'user.name', 'Manifest Tests')
        # #1757: no automatic gc or maintenance may still write under .git at cleanup.
        self._git('config', 'gc.auto', '0')
        self._git('config', 'maintenance.auto', 'false')
        self._write_files(FILES)
        self.commit = self._commit()

    def tearDown(self):
        # #1757: retry once, after a short pause, when a late git child process
        # left .git not empty (Errno 66 on macOS). Any other error is raised.
        try:
            self.temp.cleanup()
        except OSError as error:
            if error.errno != errno.ENOTEMPTY:
                raise
            time.sleep(0.2)
            if os.path.exists(self.temp.name):
                shutil.rmtree(self.temp.name)

    def _git(self, *args):
        return subprocess.run(
            ['git', *args], cwd=self.repo, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=True,
        )

    def _write_files(self, files):
        for path, content in files.items():
            target = os.path.join(self.repo, path)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, 'wb') as stream:
                stream.write(content)

    def _commit(self, update_main=True):
        self._git('add', '-A')
        self._git('commit', '-qm', 'fixture')
        commit = self._git('rev-parse', 'HEAD').stdout.decode('ascii').strip()
        if update_main:
            # Role cards are read only from commits on origin/main.
            self._git('update-ref', 'refs/remotes/origin/main', commit)
        return commit

    def _manifest(self, role='developer', gate='develop', runner=subprocess.run,
                  provider='codex'):
        return build_manifest(self.repo, self.commit, role, gate,
                              git_runner=runner, provider=provider)

    def _assert_code(self, code, *args, **kwargs):
        repo_root = kwargs.pop('repo_root', self.repo)
        with self.assertRaises(SkillManifestError) as caught:
            build_manifest(repo_root, *args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.args, (code,))
        self.assertEqual(str(caught.exception), code)

    def test_digest_is_stable_and_entries_are_sorted(self):
        first = self._manifest()
        second = self._manifest()
        self.assertEqual(first, second)
        self.assertEqual(
            [entry['path'] for entry in first['entries']],
            sorted(entry['path'] for entry in first['entries']),
        )
        self.assertEqual(first['schema_version'], 1)
        self.assertEqual(len(first['manifest_sha256']), 64)

    def test_changed_required_file_changes_digest_at_new_commit(self):
        self._write_files({'AGENTS.md': b'changed governance\n'})
        newer = self._commit()
        changed = build_manifest(self.repo, newer, 'developer', 'develop')
        original = build_manifest(self.repo, self.commit, 'developer', 'develop')
        self.assertNotEqual(changed['manifest_sha256'], original['manifest_sha256'])
        self.assertNotEqual(changed['entries'][0]['sha256'], original['entries'][0]['sha256'])

    def test_dirty_working_tree_does_not_change_manifest(self):
        before = self._manifest()
        self._write_files({'AGENTS.md': b'uncommitted edit\n'})
        after = self._manifest()
        self.assertEqual(before, after)

    def test_role_and_gate_select_distinct_requirements(self):
        developer = self._manifest('developer', 'develop')
        pm = self._manifest('pm', 'develop')
        review = self._manifest('developer', 'review')
        publish = self._manifest('developer', 'publish')
        self.assertNotEqual(developer['manifest_sha256'], pm['manifest_sha256'])
        self.assertNotEqual(developer['manifest_sha256'], review['manifest_sha256'])
        self.assertNotEqual(developer['manifest_sha256'], publish['manifest_sha256'])

    def test_provider_selects_claude_entrypoint_and_preserves_default(self):
        default = self._manifest()
        default_pm = self._manifest(role='pm')
        claude = self._manifest(provider='claude')
        claude_pm = self._manifest(role='pm', provider='claude')
        default_paths = [entry['path'] for entry in default['entries']]
        default_pm_paths = [entry['path'] for entry in default_pm['entries']]
        claude_paths = [entry['path'] for entry in claude['entries']]
        claude_pm_paths = [entry['path'] for entry in claude_pm['entries']]
        self.assertIn('AGENTS.md', default_paths)
        self.assertNotIn('CLAUDE.md', default_paths)
        self.assertIn('CLAUDE.md', claude_paths)
        self.assertNotIn('AGENTS.md', claude_paths)
        self.assertIn('skills/ai-team-delivery/pm-continuation.md', claude_pm_paths)
        self.assertIn('skills/ai-team-delivery/pm-continuation-reference.md', claude_pm_paths)
        self.assertIn('AGENTS.md', default_pm_paths)
        self.assertNotIn('CLAUDE.md', default_pm_paths)
        self.assertIn('CLAUDE.md', claude_pm_paths)
        self.assertNotIn('AGENTS.md', claude_pm_paths)
        self.assertIn('skills/ai-team-delivery/pm-continuation.md', claude_pm_paths)
        self.assertIn('skills/ai-team-delivery/pm-continuation-reference.md', claude_pm_paths)
        self.assertNotEqual(default['manifest_sha256'], claude['manifest_sha256'])

    def test_injected_git_runner_is_used(self):
        calls = []

        def runner(args, **kwargs):
            calls.append(tuple(args))
            return subprocess.run(args, **kwargs)

        self._manifest(runner=runner)
        self.assertGreater(len(calls), 1)
        self.assertTrue(all(call[:len(GIT_READ_PREFIX)] == GIT_READ_PREFIX for call in calls))
        self.assertIn(GIT_READ_PREFIX + ('rev-parse', '--verify', self.commit + '^{commit}'), calls)
        self.assertTrue(any(call[len(GIT_READ_PREFIX):len(GIT_READ_PREFIX) + 2] == ('cat-file', 'blob') for call in calls))

    def test_git_reads_disable_stdin_and_have_a_timeout(self):
        observed = []
        calls = []

        def runner(args, **kwargs):
            calls.append(tuple(args))
            observed.append(kwargs)
            return subprocess.run(args, **kwargs)

        self._manifest(runner=runner)
        self.assertTrue(observed)
        self.assertTrue(all(call[:len(GIT_READ_PREFIX)] == GIT_READ_PREFIX for call in calls))
        for kwargs in observed:
            self.assertIs(kwargs['stdin'], subprocess.DEVNULL)
            self.assertEqual(kwargs['timeout'], GIT_READ_TIMEOUT_SECONDS)

    def test_invalid_commit_format_or_missing_commit_is_refused(self):
        self._assert_code(SKILL_MANIFEST_COMMIT_INVALID, 'short', 'developer', 'develop')
        # #1931: a well-formed id with no object here is NOT_LOCAL.
        self._assert_code(
            SKILL_MANIFEST_COMMIT_NOT_LOCAL,
            '0' * 40, 'developer', 'develop',
        )

    def test_absent_commit_is_not_local_for_every_card_builder(self):
        # #1931: a main commit not fetched yet is named, not a bare INVALID.
        self.assertNotEqual(SKILL_MANIFEST_COMMIT_NOT_LOCAL, SKILL_MANIFEST_COMMIT_INVALID)
        absent = '1' * 40
        builders = (
            ('role', lambda commit, **kw: build_role_cards(self.repo, commit, 'agy', **kw)),
            ('review', lambda commit, **kw: build_review_cards(self.repo, commit, **kw)),
            ('debug', lambda commit, **kw: build_debug_cards(self.repo, commit, **kw)),
            ('manifest', lambda commit, **kw: build_manifest(
                self.repo, commit, 'developer', 'develop', **kw)),
        )
        blob = self._git('rev-parse', self.commit + ':AGENTS.md').stdout.decode('ascii').strip()

        def broken_cat_file(args, **kwargs):
            if 'cat-file' in args and '-e' in args:
                return SimpleNamespace(returncode=128, stdout=b'')
            return subprocess.run(args, **kwargs)

        for name, build in builders:
            for commit, code, kwargs in (
                    (absent, SKILL_MANIFEST_COMMIT_NOT_LOCAL, {}),
                    (absent.upper(), SKILL_MANIFEST_COMMIT_NOT_LOCAL, {}),
                    # A present object that is not a commit stays INVALID.
                    (blob, SKILL_MANIFEST_COMMIT_INVALID, {}),
                    # A cat-file error other than 'missing' stays INVALID.
                    (absent, SKILL_MANIFEST_COMMIT_INVALID,
                     {'git_runner': broken_cat_file})):
                with self.subTest(builder=name, commit=commit, kwargs=sorted(kwargs)):
                    with self.assertRaises(SkillManifestError) as caught:
                        build(commit, **kwargs)
                    self.assertEqual(caught.exception.code, code)
                    self.assertEqual(str(caught.exception), code)

    def test_annotated_tag_object_id_is_not_a_commit(self):
        tag = self._git('tag', '-a', 'manifest-tag', '-m', 'tag')
        tag_oid = self._git('rev-parse', 'refs/tags/manifest-tag').stdout.decode('ascii').strip()
        self._assert_code(
            SKILL_MANIFEST_COMMIT_INVALID, tag_oid, 'developer', 'develop',
        )

    def test_replace_refs_do_not_change_commit_or_blob_contents(self):
        expected = self._manifest()
        blob = self._git('rev-parse', self.commit + ':AGENTS.md').stdout.decode('ascii').strip()
        replacement_blob = subprocess.run(
            ['git', 'hash-object', '-w', '--stdin'], cwd=self.repo,
            input=b'replaced governance\n', stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=True,
        )
        replacement_blob_oid = replacement_blob.stdout.decode('ascii').strip()
        self._write_files({'AGENTS.md': b'replacement commit\n'})
        replacement_commit = self._commit()

        for original, replacement in (
            (blob, replacement_blob_oid),
            (self.commit, replacement_commit),
        ):
            self._git('replace', original, replacement)
            try:
                self.assertEqual(self._manifest(), expected)
            finally:
                self._git('replace', '-d', original)

        isolated_home = tempfile.TemporaryDirectory()
        try:
            for label, home in (
                ('repository configuration', None),
                ('isolated HOME configuration', isolated_home.name),
            ):
                with self.subTest(configuration=label):
                    if home is None:
                        self._git('config', 'core.useReplaceRefs', 'true')
                    else:
                        env = os.environ.copy()
                        env['HOME'] = home
                        subprocess.run(
                            ['git', 'config', '--global', 'core.useReplaceRefs', 'true'],
                            cwd=self.repo, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=True,
                        )
                    previous_home = os.environ.get('HOME')
                    try:
                        if home is not None:
                            os.environ['HOME'] = home
                            subprocess.run(
                                ['git', 'config', '--unset-all', 'core.useReplaceRefs'],
                                cwd=self.repo, env=os.environ.copy(),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                check=False,
                            )
                        self.assertEqual(self._manifest(), expected)
                    finally:
                        if previous_home is None:
                            os.environ.pop('HOME', None)
                        else:
                            os.environ['HOME'] = previous_home

                    for original, replacement in (
                        (blob, replacement_blob_oid),
                        (self.commit, replacement_commit),
                    ):
                        self._git('replace', original, replacement)
                        previous_home = os.environ.get('HOME')
                        try:
                            if home is not None:
                                os.environ['HOME'] = home
                                subprocess.run(
                                    ['git', 'config', '--unset-all', 'core.useReplaceRefs'],
                                    cwd=self.repo, env=os.environ.copy(),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    check=False,
                                )
                            self.assertEqual(self._manifest(), expected)
                        finally:
                            if previous_home is None:
                                os.environ.pop('HOME', None)
                            else:
                                os.environ['HOME'] = previous_home
                            self._git('replace', '-d', original)
        finally:
            isolated_home.cleanup()

    def test_inherited_git_dir_does_not_redirect_repository_reads(self):
        expected = self._manifest()
        other = tempfile.TemporaryDirectory()
        try:
            subprocess.run(['git', 'init', '-q', other.name], check=True)
            previous = os.environ.get('GIT_DIR')
            os.environ['GIT_DIR'] = os.path.join(other.name, '.git')
            try:
                self.assertEqual(self._manifest(), expected)
            finally:
                if previous is None:
                    os.environ.pop('GIT_DIR', None)
                else:
                    os.environ['GIT_DIR'] = previous
        finally:
            other.cleanup()

    def test_subdirectory_repository_root_is_refused(self):
        subdir = os.path.join(self.repo, 'skills')
        os.makedirs(subdir, exist_ok=True)
        self._assert_code(
            SKILL_MANIFEST_COMMIT_INVALID, self.commit, 'developer', 'develop',
            repo_root=subdir,
        )

    def test_malformed_tree_object_id_is_a_code_only_failure(self):
        malformed_outputs = (
            b'100644 blob not-an-object-id\tAGENTS.md\0',
            b'100644 blob ' + b'\xff' + b'\tAGENTS.md\0',
            '\ud800',
        )
        for output in malformed_outputs:
            with self.subTest(output=type(output).__name__):
                calls = []

                def malformed_runner(args, **kwargs):
                    calls.append(tuple(args))
                    if 'ls-tree' in args:
                        return SimpleNamespace(returncode=0, stdout=output)
                    return subprocess.run(args, **kwargs)

                self._assert_code(
                    SKILL_MANIFEST_PATH_MISSING, self.commit, 'developer', 'develop',
                    git_runner=malformed_runner,
                )
                self.assertTrue(calls)
                self.assertTrue(all(
                    call[:len(GIT_READ_PREFIX)] == GIT_READ_PREFIX
                    for call in calls
                ))

    def test_unknown_role_and_gate_are_refused(self):
        self._assert_code(SKILL_MANIFEST_ROLE_INVALID, self.commit, 'unknown', 'develop')
        self._assert_code(SKILL_MANIFEST_GATE_INVALID, self.commit, 'developer', 'unknown')

    def test_missing_required_path_is_refused(self):
        self._git('rm', '-q', 'skills/safe-git/SKILL.md')
        self.commit = self._commit()
        self._assert_code(
            SKILL_MANIFEST_PATH_MISSING,
            self.commit, 'developer', 'publish',
        )

    def test_symlink_is_not_a_file(self):
        os.remove(os.path.join(self.repo, 'AGENTS.md'))
        os.symlink('skills/ai-team-delivery/SKILL.md', os.path.join(self.repo, 'AGENTS.md'))
        self.commit = self._commit()
        self._assert_code(SKILL_MANIFEST_NOT_FILE, self.commit, 'developer', 'develop')

    def test_tree_at_required_path_is_not_a_file(self):
        os.remove(os.path.join(self.repo, 'skills/review-change/SKILL.md'))
        os.makedirs(os.path.join(self.repo, 'skills/review-change/SKILL.md', 'child'))
        with open(os.path.join(self.repo, 'skills/review-change/SKILL.md', 'child', 'x'), 'wb') as stream:
            stream.write(b'x')
        self.commit = self._commit()
        self._assert_code(SKILL_MANIFEST_NOT_FILE, self.commit, 'developer', 'review')

    def test_malformed_utf8_is_refused(self):
        self._write_files({'AGENTS.md': b'\xff'})
        self.commit = self._commit()
        self._assert_code(SKILL_MANIFEST_NOT_UTF8, self.commit, 'developer', 'develop')

    def test_oversized_file_is_refused(self):
        self._write_files({'AGENTS.md': b'x' * (MAX_SKILL_FILE_SIZE + 1)})
        self.commit = self._commit()
        content_reads = []

        def tracking_runner(args, **kwargs):
            if 'cat-file' in args and 'blob' in args:
                content_reads.append(tuple(args))
            return subprocess.run(args, **kwargs)

        self._assert_code(
            SKILL_MANIFEST_TOO_LARGE, self.commit, 'developer', 'develop',
            git_runner=tracking_runner,
        )
        self.assertEqual(content_reads, [])

    # Role cards (#1502 WP-F design 3.7, piece 1).

    def _assert_card_code(self, code, commit, route, **kwargs):
        with self.assertRaises(SkillManifestError) as caught:
            build_role_cards(self.repo, commit, route, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    def test_role_cards_return_core_then_card_text_and_sha256_per_route(self):
        self.assertEqual(
            role_card_routes(),
            ('agy', 'claude-developer', 'gw-agent', 'opencode-developer'),
        )
        self.assertEqual(role_card_routes(), COMMON_CORE_ROUTES)
        for route in role_card_routes():
            with self.subTest(route=route):
                result = build_role_cards(self.repo, self.commit, route)
                self.assertEqual(
                    [card['path'] for card in result['cards']],
                    [COMMON_CORE_CARD_PATH, DEVELOPER_CARD_PATH],
                )
                for card in result['cards']:
                    content = FILES[card['path']]
                    self.assertEqual(card['text'], content.decode('utf-8'))
                    self.assertEqual(card['sha256'], hashlib.sha256(content).hexdigest())
                    self.assertEqual(card['size'], len(content))
                self.assertEqual(result['route'], route)
                self.assertEqual(result['role'], 'developer')
                self.assertEqual(result['commit'], self.commit.lower())
                self.assertEqual(
                    result['prefix_size'],
                    sum(card['size'] for card in result['cards']),
                )
                self.assertEqual(len(result['cards_sha256']), 64)
                self.assertEqual(result, build_role_cards(self.repo, self.commit, route))
        # Every route shares the same cards; only the route binds the digest.
        built = {route: build_role_cards(self.repo, self.commit, route)
                 for route in role_card_routes()}
        for route, result in built.items():
            self.assertEqual(result['cards'], built['claude-developer']['cards'])
        self.assertEqual(len({result['cards_sha256'] for result in built.values()}),
                         len(built))
        # AGENTS.md is no Developer route's common core any more.
        self.assertTrue(all(card['path'] != 'AGENTS.md'
                            for result in built.values() for card in result['cards']))

    def test_role_cards_come_from_base_commit_not_working_tree_or_newer_head(self):
        base = build_role_cards(self.repo, self.commit, 'claude-developer')
        self._write_files({DEVELOPER_CARD_PATH: b'uncommitted card edit\n'})
        self.assertEqual(build_role_cards(self.repo, self.commit, 'claude-developer'), base)

        newer = self._commit()
        head = build_role_cards(self.repo, newer, 'claude-developer')
        again = build_role_cards(self.repo, self.commit, 'claude-developer')
        self.assertEqual(again, base)
        self.assertEqual(again['cards'][1]['text'], 'developer card\n')
        self.assertEqual(head['cards'][1]['text'], 'uncommitted card edit\n')
        self.assertNotEqual(head['cards_sha256'], base['cards_sha256'])

    def test_role_cards_read_only_through_pinned_git_objects(self):
        calls = []

        def runner(args, **kwargs):
            calls.append(tuple(args))
            self.assertIs(kwargs['stdin'], subprocess.DEVNULL)
            self.assertEqual(kwargs['timeout'], GIT_READ_TIMEOUT_SECONDS)
            return subprocess.run(args, **kwargs)

        build_role_cards(self.repo, self.commit, 'agy', git_runner=runner)
        self.assertTrue(all(call[:len(GIT_READ_PREFIX)] == GIT_READ_PREFIX for call in calls))
        self.assertIn(
            GIT_READ_PREFIX + ('rev-parse', '--verify', self.commit + '^{commit}'), calls,
        )
        tree_reads = [call for call in calls if 'ls-tree' in call]
        self.assertEqual(
            [call[-1] for call in tree_reads], [COMMON_CORE_CARD_PATH, DEVELOPER_CARD_PATH],
        )
        self.assertTrue(all(self.commit in call for call in tree_reads))

    def test_role_cards_refuse_unknown_route_before_any_git_read(self):
        calls = []

        def runner(args, **kwargs):
            calls.append(tuple(args))
            return subprocess.run(args, **kwargs)

        for route in ('unknown', None, 'claude', 'codex-developer', 'CLAUDE-DEVELOPER',
                      'company-gw-agent', ''):
            with self.subTest(route=route):
                self._assert_card_code(
                    SKILL_MANIFEST_ROUTE_INVALID, self.commit, route, git_runner=runner,
                )
        self.assertEqual(calls, [])

    def test_role_cards_refuse_developer_prefix_over_14_kib(self):
        self.assertEqual(MAX_DEVELOPER_PREFIX_SIZE, 14 * 1024)
        self.assertNotEqual(SKILL_MANIFEST_PREFIX_TOO_LARGE, SKILL_MANIFEST_TOO_LARGE)
        self.assertNotEqual(SKILL_MANIFEST_PREFIX_TOO_LARGE, SKILL_MANIFEST_ROUTE_INVALID)
        card_size = len(FILES[DEVELOPER_CARD_PATH])

        for route in COMMON_CORE_ROUTES:
            with self.subTest(route=route):
                self._write_files({COMMON_CORE_CARD_PATH:
                                   b'x' * (MAX_DEVELOPER_PREFIX_SIZE - card_size)})
                self.commit = self._commit()
                at_limit = build_role_cards(self.repo, self.commit, route)
                self.assertEqual(at_limit['prefix_size'], MAX_DEVELOPER_PREFIX_SIZE)

                self._write_files({COMMON_CORE_CARD_PATH:
                                   b'x' * (MAX_DEVELOPER_PREFIX_SIZE - card_size + 1)})
                self.commit = self._commit()
                self._assert_card_code(SKILL_MANIFEST_PREFIX_TOO_LARGE, self.commit, route)

        # The common core alone over the budget is refused too, on every route.
        self._write_files({COMMON_CORE_CARD_PATH: b'x' * (MAX_DEVELOPER_PREFIX_SIZE + 1)})
        self.commit = self._commit()
        for route in COMMON_CORE_ROUTES:
            with self.subTest(route=route, core='over budget'):
                self._assert_card_code(SKILL_MANIFEST_PREFIX_TOO_LARGE, self.commit, route)

        # An oversized AGENTS.md no longer counts toward any Developer prefix.
        self._write_files({COMMON_CORE_CARD_PATH: FILES[COMMON_CORE_CARD_PATH],
                           'AGENTS.md': b'x' * (MAX_DEVELOPER_PREFIX_SIZE + 1)})
        self.commit = self._commit()
        for route in COMMON_CORE_ROUTES:
            with self.subTest(route=route, agents='over budget'):
                self.assertTrue(build_role_cards(self.repo, self.commit, route))

    def test_role_cards_refuse_invalid_commit_and_missing_card(self):
        for commit in ('short', None):
            with self.subTest(commit=commit):
                self._assert_card_code(SKILL_MANIFEST_COMMIT_INVALID, commit, 'agy')
        self._assert_card_code(SKILL_MANIFEST_COMMIT_NOT_LOCAL, '0' * 40, 'agy')
        # A common core missing at the commit refuses every Developer route.
        self._git('rm', '-q', COMMON_CORE_CARD_PATH)
        self.commit = self._commit()
        for route in COMMON_CORE_ROUTES:
            with self.subTest(route=route, missing=COMMON_CORE_CARD_PATH):
                self._assert_card_code(SKILL_MANIFEST_PATH_MISSING, self.commit, route)
        self._git('rm', '-q', DEVELOPER_CARD_PATH)
        self.commit = self._commit()
        for route in role_card_routes():
            with self.subTest(route=route, missing=DEVELOPER_CARD_PATH):
                self._assert_card_code(SKILL_MANIFEST_PATH_MISSING, self.commit, route)

    def test_role_cards_refuse_non_utf8_card(self):
        self._write_files({DEVELOPER_CARD_PATH: b'\xff'})
        self.commit = self._commit()
        self._assert_card_code(SKILL_MANIFEST_NOT_UTF8, self.commit, 'opencode-developer')

    def test_role_cards_accept_main_commit_and_older_main_commit(self):
        older = self.commit
        self._write_files({DEVELOPER_CARD_PATH: b'newer developer card\n'})
        newer = self._commit()
        for main_ref in ('refs/remotes/origin/main', 'origin/main'):
            with self.subTest(main_ref=main_ref):
                current = build_role_cards(self.repo, newer, 'agy', main_ref=main_ref)
                self.assertEqual(current['cards'][1]['text'], 'newer developer card\n')
                previous = build_role_cards(self.repo, older, 'agy', main_ref=main_ref)
                self.assertEqual(previous['cards'][1]['text'], 'developer card\n')
        # The default main ref is origin/main.
        self.assertEqual(build_role_cards(self.repo, older, 'agy'), previous)

    def test_role_cards_refuse_side_branch_commit_not_on_main(self):
        self.assertNotEqual(SKILL_MANIFEST_COMMIT_NOT_ON_MAIN, SKILL_MANIFEST_COMMIT_INVALID)
        main_commit = self.commit
        self._git('checkout', '-q', '-b', 'side')
        self._write_files({DEVELOPER_CARD_PATH: b'side branch card\n'})
        side = self._commit(update_main=False)
        calls = []

        def runner(args, **kwargs):
            calls.append(tuple(args))
            return subprocess.run(args, **kwargs)

        for main_ref in ('refs/remotes/origin/main', 'origin/main'):
            with self.subTest(main_ref=main_ref):
                calls.clear()
                self._assert_card_code(
                    SKILL_MANIFEST_COMMIT_NOT_ON_MAIN, side, 'claude-developer',
                    git_runner=runner, main_ref=main_ref,
                )
                # Refused before any card blob is read.
                self.assertFalse([call for call in calls if 'ls-tree' in call])
        self._assert_card_code(SKILL_MANIFEST_COMMIT_NOT_ON_MAIN, side, 'claude-developer')
        # The main commit itself still works while the side branch exists.
        self.assertTrue(build_role_cards(self.repo, main_commit, 'claude-developer'))
        # The ref given decides: the side commit is accepted against its own
        # branch, and once origin/main includes it.
        self.assertTrue(build_role_cards(
            self.repo, side, 'claude-developer', main_ref='refs/heads/side'))
        self._git('update-ref', 'refs/remotes/origin/main', side)
        self.assertEqual(
            build_role_cards(self.repo, side, 'claude-developer')['cards'][1]['text'],
            'side branch card\n',
        )

    def test_role_cards_refuse_missing_or_malformed_main_ref(self):
        self._git('update-ref', '-d', 'refs/remotes/origin/main')
        self._assert_card_code(SKILL_MANIFEST_MAIN_REF_INVALID, self.commit, 'agy')
        calls = []

        def runner(args, **kwargs):
            calls.append(tuple(args))
            return subprocess.run(args, **kwargs)

        for main_ref in (None, '', '-main', '--all', 'origin/main..side', 'main^',
                         'main~1', 'main@{1}', 'origin main', 'refs/heads/', 'x.lock'):
            with self.subTest(main_ref=main_ref):
                self._assert_card_code(
                    SKILL_MANIFEST_MAIN_REF_INVALID, self.commit, 'agy',
                    git_runner=runner, main_ref=main_ref,
                )
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
