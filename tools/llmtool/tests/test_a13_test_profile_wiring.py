"""#2793 A13.0 wiring: completion classification follows llmtool.test_profile."""

import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from llmtool import task_completion, test_profile
from llmtool.errors import LLMToolError


SOURCE_SHA = 'b' * 40
DIFF_SHA256 = 'a' * 64
CUSTOM_PROFILE = test_profile.TestProfile((
    (re.compile(r'tools/llmtool/tests/test_custom_[A-Za-z0-9_]+\.py\Z'),
     'script'),
))


def _bytes_pattern_profile():
    """A TestProfile-shaped value carrying a compiled bytes pattern.

    ``TestProfile`` now refuses a bytes pattern at construction, so this
    builds the invalid value directly to model a caller-supplied profile
    that completion must still refuse.
    """
    pattern = re.compile(b'test')
    profile = object.__new__(test_profile.TestProfile)
    object.__setattr__(profile, '_entries', ((pattern, 'unittest'),))
    object.__setattr__(
        profile, '_keys', ((pattern.pattern, pattern.flags, 'unittest'),))
    return profile


def _eligible_with_profile(paths, profile):
    """Run the real worktree completion classification and return its picks."""
    snapshot = {'diff_sha256': DIFF_SHA256, 'changed_paths': list(paths),
                'source_sha': SOURCE_SHA}
    captured = []

    def run_tests(root, source_sha, eligible, **kwargs):
        captured.append(list(eligible))
        return []

    with mock.patch.object(test_profile, 'default_profile',
                           return_value=profile), \
            mock.patch.object(task_completion, '_worktree_snapshot',
                              side_effect=[dict(snapshot), dict(snapshot)]), \
            mock.patch.object(task_completion, '_run_registered_tests',
                              side_effect=run_tests):
        task_completion.run_bounded_worktree_tests(
            root='/nonexistent', source_sha=SOURCE_SHA,
            diff_sha256=DIFF_SHA256, changed_paths=list(paths),
            read_paths=list(paths), write_paths=[])
    return captured[0]


class DefaultProfileClassificationTests(unittest.TestCase):
    def test_existing_paths_keep_their_runner_kinds(self):
        cases = (
            ('tools/llmtool/tests/test_alpha.py', 'unittest'),
            ('scripts/test_beta.py', 'script'),
            ('tools/llmtool/tests/test_alpha.py.bak', None),
            ('scripts/build.sh', None),
            ('tests/test_gamma.py', None),
        )
        for path, expected in cases:
            with self.subTest(path=path):
                self.assertEqual(task_completion._registered_test(path), expected)

    def test_completion_reads_the_default_profile(self):
        with mock.patch.object(test_profile, 'default_profile',
                               wraps=test_profile.default_profile) as provider:
            self.assertEqual(
                task_completion._registered_test('scripts/test_beta.py'),
                'script')
        provider.assert_called_once_with()

    def test_real_completion_selects_both_default_runner_kinds(self):
        paths = ['tools/llmtool/tests/test_alpha.py', 'scripts/test_beta.py']
        expected = [
            ('scripts/test_beta.py', 'script'),
            ('tools/llmtool/tests/test_alpha.py', 'unittest'),
        ]
        self.assertEqual(
            _eligible_with_profile(paths, test_profile.default_profile()),
            expected)


class SubstitutedProfileTests(unittest.TestCase):
    def test_registered_test_follows_the_substituted_profile(self):
        with mock.patch.object(test_profile, 'default_profile',
                               return_value=CUSTOM_PROFILE):
            self.assertEqual(
                task_completion._registered_test(
                    'tools/llmtool/tests/test_custom_one.py'),
                'script')
            self.assertIsNone(task_completion._registered_test(
                'tools/llmtool/tests/test_regular.py'))
            self.assertIsNone(task_completion._registered_test(
                'scripts/test_regular.py'))

    def test_real_completion_uses_the_substituted_profile_without_fallback(self):
        paths = ['tools/llmtool/tests/test_custom_one.py',
                 'tools/llmtool/tests/test_regular.py']
        self.assertEqual(_eligible_with_profile(paths, CUSTOM_PROFILE), [
            ('tools/llmtool/tests/test_custom_one.py', 'script'),
        ])


class ZeroTestDiscoveryTests(unittest.TestCase):
    """A profile that sends a path to unittest discovery must run real tests.

    F1: a substituted profile may classify a ``scripts/`` path as ``unittest``.
    Discovery now runs in the admitted path's own directory rather than
    ``tools/llmtool/tests``; an absent file fails discovery (unless shadowed
    by a subdirectory file, which is refused with ``TEST_PATH_INVALID``)
    and an existing file with zero tests is recorded as FAILED.
    """

    def test_zero_test_unittest_discovery_is_recorded_as_failed(self):
        profile = test_profile.TestProfile((
            (re.compile(r'scripts/test_[A-Za-z0-9_]+\.py\Z'), 'unittest'),
        ))
        paths = ['scripts/test_absent.py']
        snapshot = {'diff_sha256': DIFF_SHA256, 'changed_paths': list(paths),
                    'source_sha': SOURCE_SHA}
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'tools', 'llmtool', 'tests').mkdir(parents=True)
            with mock.patch.object(test_profile, 'default_profile',
                                   return_value=profile), \
                    mock.patch.object(task_completion, '_worktree_snapshot',
                                      side_effect=[dict(snapshot), dict(snapshot)]):
                # scripts/test_absent.py and scripts/ do not exist in root;
                # discovery runs in scripts/ rather than tools/llmtool/tests and
                # fails instead of refusing before Popen (so Popen-mocked tests
                # with absent fixture files still reach the launcher).
                evidence = task_completion.run_bounded_worktree_tests(
                    root=root, source_sha=SOURCE_SHA,
                    diff_sha256=DIFF_SHA256, changed_paths=list(paths),
                    read_paths=list(paths), write_paths=[])
        self.assertEqual([item['path'] for item in evidence], paths)
        self.assertEqual(evidence[0]['status'], 'FAILED')
        self.assertNotEqual(evidence[0]['exit_code'], 0)

    def test_zero_test_unittest_discovery_on_existing_empty_file_fails(self):
        profile = test_profile.TestProfile((
            (re.compile(r'scripts/test_[A-Za-z0-9_]+\.py\Z'), 'unittest'),
        ))
        paths = ['scripts/test_empty.py']
        snapshot = {'diff_sha256': DIFF_SHA256, 'changed_paths': list(paths),
                    'source_sha': SOURCE_SHA}
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'scripts').mkdir(parents=True)
            Path(root, 'scripts', 'test_empty.py').write_text('', encoding='utf-8')
            with mock.patch.object(test_profile, 'default_profile',
                                   return_value=profile), \
                    mock.patch.object(task_completion, '_worktree_snapshot',
                                      side_effect=[dict(snapshot), dict(snapshot)]):
                evidence = task_completion.run_bounded_worktree_tests(
                    root=root, source_sha=SOURCE_SHA,
                    diff_sha256=DIFF_SHA256, changed_paths=list(paths),
                    read_paths=list(paths), write_paths=[])
        self.assertEqual([item['path'] for item in evidence], paths)
        # Python 3.12+ exits 5 when no tests run; older interpreters exit 0
        # after printing "Ran 0 tests". Neither is a pass.
        self.assertIn(evidence[0]['exit_code'], (0, 5))
        self.assertEqual(evidence[0]['status'], 'FAILED')


class NonDefaultDirectoryExecutionTests(unittest.TestCase):
    """Real execution through the public entry runs the admitted file itself."""

    PASS_BODY = (
        'import unittest\n\n'
        'class SampleTest(unittest.TestCase):\n'
        '    def test_ok(self):\n'
        '        self.assertTrue(True)\n'
    )
    FAIL_BODY = (
        'import unittest\n\n'
        'class SampleTest(unittest.TestCase):\n'
        '    def test_fail(self):\n'
        '        self.fail("expected failure")\n'
    )

    def _run_real_worktree(self, root, paths, profile):
        snapshot = {'diff_sha256': DIFF_SHA256, 'changed_paths': list(paths),
                    'source_sha': SOURCE_SHA}
        with mock.patch.object(test_profile, 'default_profile',
                               return_value=profile), \
                mock.patch.object(task_completion, '_worktree_snapshot',
                                  side_effect=[dict(snapshot), dict(snapshot)]):
            return task_completion.run_bounded_worktree_tests(
                root=root, source_sha=SOURCE_SHA,
                diff_sha256=DIFF_SHA256, changed_paths=list(paths),
                read_paths=list(paths), write_paths=[])

    def test_non_default_directory_failing_file_never_passes_when_default_passes(self):
        cases = (
            ('unittest', 'tools/llmtool/tests', 'custom/tests',
             r'custom/tests/test_[A-Za-z0-9_]+\.py\Z'),
            ('script', 'scripts', 'custom/scripts',
             r'custom/scripts/test_[A-Za-z0-9_]+\.py\Z'),
        )
        for kind, default_dir, custom_dir, pattern in cases:
            with self.subTest(kind=kind):
                profile = test_profile.TestProfile(
                    ((re.compile(pattern), kind),))
                admitted = f'{custom_dir}/test_same.py'
                with tempfile.TemporaryDirectory() as root:
                    Path(root, default_dir).mkdir(parents=True)
                    Path(root, custom_dir).mkdir(parents=True)
                    Path(root, default_dir, 'test_same.py').write_text(
                        self.PASS_BODY, encoding='utf-8')
                    Path(root, admitted).write_text(
                        self.FAIL_BODY, encoding='utf-8')
                    evidence = self._run_real_worktree(
                        root, [admitted], profile)
                self.assertEqual([item['path'] for item in evidence], [admitted])
                self.assertEqual(evidence[0]['status'], 'FAILED')
                self.assertNotEqual(evidence[0]['status'], 'PASSED')
                self.assertEqual(evidence[0]['exit_code'], 1)

    def test_non_default_directory_passing_file_passes_for_both_kinds(self):
        cases = (
            ('unittest', 'custom/tests',
             r'custom/tests/test_[A-Za-z0-9_]+\.py\Z'),
            ('script', 'custom/scripts',
             r'custom/scripts/test_[A-Za-z0-9_]+\.py\Z'),
        )
        for kind, custom_dir, pattern in cases:
            with self.subTest(kind=kind):
                profile = test_profile.TestProfile(
                    ((re.compile(pattern), kind),))
                admitted = f'{custom_dir}/test_pass.py'
                with tempfile.TemporaryDirectory() as root:
                    Path(root, custom_dir).mkdir(parents=True)
                    Path(root, admitted).write_text(
                        self.PASS_BODY, encoding='utf-8')
                    evidence = self._run_real_worktree(
                        root, [admitted], profile)
                self.assertEqual([item['path'] for item in evidence], [admitted])
                self.assertEqual(evidence[0]['status'], 'PASSED')
                self.assertEqual(evidence[0]['exit_code'], 0)

    def test_subdirectory_shadow_file_is_refused(self):
        profile = test_profile.TestProfile((
            (re.compile(r'custom/test_[A-Za-z0-9_]+\.py\Z'), 'unittest'),
        ))
        admitted = 'custom/test_shadow.py'
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'custom', 'sub').mkdir(parents=True)
            Path(root, admitted).write_text('', encoding='utf-8')
            Path(root, 'custom', 'sub', 'test_shadow.py').write_text(
                self.PASS_BODY, encoding='utf-8')
            with self.assertRaises(LLMToolError) as caught:
                self._run_real_worktree(root, [admitted], profile)
        self.assertEqual(caught.exception.completion_code, 'TEST_PATH_INVALID')

    def test_glob_filename_is_refused(self):
        profile = test_profile.TestProfile((
            (re.compile(r'custom/test_.*\.py\Z'), 'unittest'),
        ))
        admitted = 'custom/test_[a].py'
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'custom').mkdir(parents=True)
            Path(root, admitted).write_text(self.FAIL_BODY, encoding='utf-8')
            Path(root, 'custom', 'test_a.py').write_text(
                self.PASS_BODY, encoding='utf-8')
            with self.assertRaises(LLMToolError) as caught:
                self._run_real_worktree(root, [admitted], profile)
        self.assertEqual(caught.exception.completion_code, 'TEST_PATH_INVALID')

    def test_absent_single_segment_directory_cannot_import_pythonpath_package(self):
        profile = test_profile.TestProfile((
            (re.compile(r'pkg_on_path/test_[A-Za-z0-9_]+\.py\Z'), 'unittest'),
        ))
        admitted = 'pkg_on_path/test_ok.py'
        with tempfile.TemporaryDirectory() as root:
            pkg = Path(root, 'tools', 'llmtool', 'pkg_on_path')
            pkg.mkdir(parents=True)
            (pkg / '__init__.py').write_text('', encoding='utf-8')
            (pkg / 'test_ok.py').write_text(self.PASS_BODY, encoding='utf-8')
            evidence = self._run_real_worktree(root, [admitted], profile)
        self.assertEqual([item['path'] for item in evidence], [admitted])
        self.assertEqual(evidence[0]['status'], 'FAILED')
        self.assertNotEqual(evidence[0]['exit_code'], 0)

    def test_symlinked_subdirectory_shadow_file_is_refused(self):
        profile = test_profile.TestProfile((
            (re.compile(r'custom/test_[A-Za-z0-9_]+\.py\Z'), 'unittest'),
        ))
        admitted = 'custom/test_shadow.py'
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'custom').mkdir(parents=True)
            Path(root, 'other').mkdir(parents=True)
            Path(root, admitted).write_text('', encoding='utf-8')
            Path(root, 'other', 'test_shadow.py').write_text(
                self.PASS_BODY, encoding='utf-8')
            Path(root, 'custom', 'link_sub').symlink_to(Path(root, 'other'))
            with self.assertRaises(LLMToolError) as caught:
                self._run_real_worktree(root, [admitted], profile)
        self.assertEqual(caught.exception.completion_code, 'TEST_PATH_INVALID')

    def test_symlinked_parent_directory_is_refused(self):
        profile = test_profile.TestProfile((
            (re.compile(r'custom/test_[A-Za-z0-9_]+\.py\Z'), 'unittest'),
        ))
        admitted = 'custom/test_ok.py'
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'other').mkdir(parents=True)
            Path(root, 'other', 'test_ok.py').write_text(
                self.PASS_BODY, encoding='utf-8')
            Path(root, 'custom').symlink_to(Path(root, 'other'))
            with self.assertRaises(LLMToolError) as caught:
                self._run_real_worktree(root, [admitted], profile)
        self.assertEqual(caught.exception.completion_code, 'TEST_PATH_INVALID')

    def test_default_profile_command_digests_stay_unchanged(self):
        paths = ['scripts/test_beta.py', 'tools/llmtool/tests/test_alpha.py']
        expected_argvs = {
            'tools/llmtool/tests/test_alpha.py': [
                sys.executable, '-B', '-m', 'unittest', 'discover',
                '-s', 'tools/llmtool/tests', '-p', 'test_alpha.py',
            ],
            'scripts/test_beta.py': [
                sys.executable, '-I', '-B', '-m', 'unittest', 'discover',
                '-s', 'scripts', '-p', 'test_beta.py',
            ],
        }
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'tools', 'llmtool', 'tests').mkdir(parents=True)
            Path(root, 'scripts').mkdir(parents=True)
            for rel in paths:
                Path(root, rel).write_text(self.PASS_BODY, encoding='utf-8')
            evidence = self._run_real_worktree(
                root, paths, test_profile.default_profile())
        by_path = {item['path']: item for item in evidence}
        for rel, argv in expected_argvs.items():
            expected_digest = hashlib.sha256(
                json.dumps(argv, separators=(',', ':')).encode()).hexdigest()
            self.assertEqual(by_path[rel]['status'], 'PASSED')
            self.assertEqual(by_path[rel]['command_sha256'], expected_digest)


class SingleSnapshotTests(unittest.TestCase):
    def test_real_completion_resolves_each_path_from_one_profile_snapshot(self):
        paths = ['tools/llmtool/tests/test_custom_one.py']
        snapshot = {'diff_sha256': DIFF_SHA256, 'changed_paths': list(paths),
                    'source_sha': SOURCE_SHA}
        captured = []

        def run_tests(root, source_sha, eligible, **kwargs):
            captured.append(list(eligible))
            return []

        provider = mock.Mock(
            side_effect=[CUSTOM_PROFILE, test_profile.default_profile()])
        with mock.patch.object(test_profile, 'default_profile', provider), \
                mock.patch.object(task_completion, '_worktree_snapshot',
                                  side_effect=[dict(snapshot), dict(snapshot)]), \
                mock.patch.object(task_completion, '_run_registered_tests',
                                  side_effect=run_tests):
            task_completion.run_bounded_worktree_tests(
                root='/nonexistent', source_sha=SOURCE_SHA,
                diff_sha256=DIFF_SHA256, changed_paths=list(paths),
                read_paths=list(paths), write_paths=[])
        self.assertEqual(captured[0], [
            ('tools/llmtool/tests/test_custom_one.py', 'script'),
        ])
        self.assertEqual(provider.call_count, 1)


class RefusalTests(unittest.TestCase):
    DEFAULT_PATH = 'tools/llmtool/tests/test_regular.py'

    def _refused(self, provider, path=None):
        with mock.patch.object(test_profile, 'default_profile', provider):
            with self.assertRaises(LLMToolError) as caught:
                task_completion._registered_test(path or self.DEFAULT_PATH)
        self.assertEqual(caught.exception.completion_code,
                         'TEST_PROFILE_INVALID')
        self.assertNotIn(self.DEFAULT_PATH, str(caught.exception))

    def test_missing_provider_refuses(self):
        self._refused(None)
        self._refused(mock.Mock(return_value=None))

    def test_non_callable_provider_refuses(self):
        self._refused(object())

    def test_raising_provider_refuses(self):
        self._refused(mock.Mock(side_effect=RuntimeError('profile unavailable')))

    def test_non_profile_result_refuses(self):
        self._refused(mock.Mock(return_value=object()))

    def test_bytes_pattern_profile_refuses(self):
        self._refused(mock.Mock(return_value=_bytes_pattern_profile()))

    def test_real_completion_refuses_a_bytes_pattern_profile(self):
        bytes_profile = _bytes_pattern_profile()
        paths = ['tools/llmtool/tests/test_alpha.py']
        snapshot = {'diff_sha256': DIFF_SHA256, 'changed_paths': list(paths),
                    'source_sha': SOURCE_SHA}
        with mock.patch.object(test_profile, 'default_profile',
                               return_value=bytes_profile), \
                mock.patch.object(task_completion, '_worktree_snapshot',
                                  side_effect=[dict(snapshot), dict(snapshot)]), \
                mock.patch.object(task_completion, '_run_registered_tests',
                                  return_value=[]):
            with self.assertRaises(LLMToolError) as caught:
                task_completion.run_bounded_worktree_tests(
                    root='/nonexistent', source_sha=SOURCE_SHA,
                    diff_sha256=DIFF_SHA256, changed_paths=list(paths),
                    read_paths=list(paths), write_paths=[])
        self.assertEqual(caught.exception.completion_code,
                         'TEST_PROFILE_INVALID')

    def test_ambiguous_profile_for_a_path_refuses(self):
        ambiguous = test_profile.TestProfile((
            (re.compile(r'custom/tests/.*\.py\Z'), 'unittest'),
            (re.compile(r'custom/tests/test_.*\.py\Z'), 'script'),
        ))
        self._refused(mock.Mock(return_value=ambiguous),
                      'custom/tests/test_alpha.py')

    def test_real_completion_refuses_an_ambiguous_profile(self):
        ambiguous = test_profile.TestProfile((
            (re.compile(r'tools/llmtool/tests/.*\.py\Z'), 'unittest'),
            (re.compile(r'tools/llmtool/tests/test_.*\.py\Z'), 'script'),
        ))
        paths = ['tools/llmtool/tests/test_alpha.py']
        snapshot = {'diff_sha256': DIFF_SHA256, 'changed_paths': list(paths),
                    'source_sha': SOURCE_SHA}
        with mock.patch.object(test_profile, 'default_profile',
                               return_value=ambiguous), \
                mock.patch.object(task_completion, '_worktree_snapshot',
                                  side_effect=[dict(snapshot), dict(snapshot)]), \
                mock.patch.object(task_completion, '_run_registered_tests',
                                  return_value=[]):
            with self.assertRaises(LLMToolError) as caught:
                task_completion.run_bounded_worktree_tests(
                    root='/nonexistent', source_sha=SOURCE_SHA,
                    diff_sha256=DIFF_SHA256, changed_paths=list(paths),
                    read_paths=list(paths), write_paths=[])
        self.assertEqual(caught.exception.completion_code,
                         'TEST_PROFILE_INVALID')


class ConstructorInputRefusalTests(unittest.TestCase):
    def test_non_iterable_entries_refuse_with_fixed_code(self):
        for entries in (None, True, 1):
            with self.subTest(entries=entries):
                with self.assertRaises(
                        test_profile.TestProfileError) as caught:
                    test_profile.TestProfile(entries)
                self.assertEqual(caught.exception.CODE, 'TEST_PROFILE_INVALID')
                self.assertEqual(str(caught.exception), 'TEST_PROFILE_INVALID')

    def test_bytes_pattern_entries_refuse_with_fixed_code(self):
        with self.assertRaises(test_profile.TestProfileError) as caught:
            test_profile.TestProfile(((re.compile(b'test'), 'unittest'),))
        self.assertEqual(caught.exception.CODE, 'TEST_PROFILE_INVALID')
        self.assertEqual(str(caught.exception), 'TEST_PROFILE_INVALID')

    def test_generator_entries_are_still_accepted(self):
        pattern = re.compile(
            r'tools/llmtool/tests/test_[A-Za-z0-9_]+\.py\Z')
        profile = test_profile.TestProfile(
            ((entry, 'unittest') for entry in (pattern,)))
        self.assertEqual(profile.entries, ((pattern, 'unittest'),))

    def test_iterator_errors_after_iteration_starts_propagate(self):
        def entries():
            raise RuntimeError('iterator failed')
            yield

        with self.assertRaises(RuntimeError):
            test_profile.TestProfile(entries())


if __name__ == '__main__':
    unittest.main()
