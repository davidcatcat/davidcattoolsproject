"""Tests for commit-pinned governance manifests."""

import os
import subprocess
import tempfile
import unittest

from llmtool.skill_manifest import (
    MAX_SKILL_FILE_SIZE,
    SKILL_MANIFEST_COMMIT_INVALID,
    SKILL_MANIFEST_GATE_INVALID,
    SKILL_MANIFEST_NOT_FILE,
    SKILL_MANIFEST_NOT_UTF8,
    SKILL_MANIFEST_PATH_MISSING,
    SKILL_MANIFEST_ROLE_INVALID,
    SKILL_MANIFEST_TOO_LARGE,
    SkillManifestError,
    build_manifest,
)


FILES = {
    'AGENTS.md': b'governance\n',
    'skills/ai-team-delivery/SKILL.md': b'delivery\n',
    'skills/ai-team-delivery/pm-continuation.md': b'pm\n',
    'skills/ai-team-delivery/pm-continuation-reference.md': b'reference\n',
    'skills/review-change/SKILL.md': b'review\n',
    'skills/ai-team-delivery/formal-verdict.md': b'verdict\n',
    'skills/safe-git/SKILL.md': b'git\n',
}


class SkillManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = self.temp.name
        self._git('init', '-q')
        self._git('config', 'user.email', 'manifest-tests@example.invalid')
        self._git('config', 'user.name', 'Manifest Tests')
        self._write_files(FILES)
        self.commit = self._commit()

    def tearDown(self):
        self.temp.cleanup()

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

    def _commit(self):
        self._git('add', '-A')
        self._git('commit', '-qm', 'fixture')
        return self._git('rev-parse', 'HEAD').stdout.decode('ascii').strip()

    def _manifest(self, role='developer', gate='develop', runner=subprocess.run):
        return build_manifest(self.repo, self.commit, role, gate, git_runner=runner)

    def _assert_code(self, code, *args, **kwargs):
        with self.assertRaises(SkillManifestError) as caught:
            build_manifest(self.repo, *args, **kwargs)
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

    def test_injected_git_runner_is_used(self):
        calls = []

        def runner(args, **kwargs):
            calls.append(tuple(args))
            return subprocess.run(args, **kwargs)

        self._manifest(runner=runner)
        self.assertGreater(len(calls), 1)
        self.assertEqual(calls[0][1:3], ('cat-file', '-e'))

    def test_invalid_commit_format_or_missing_commit_is_refused(self):
        self._assert_code(SKILL_MANIFEST_COMMIT_INVALID, 'short', 'developer', 'develop')
        self._assert_code(
            SKILL_MANIFEST_COMMIT_INVALID,
            '0' * 40, 'developer', 'develop',
        )

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
        self._assert_code(SKILL_MANIFEST_TOO_LARGE, self.commit, 'developer', 'develop')


if __name__ == '__main__':
    unittest.main()
