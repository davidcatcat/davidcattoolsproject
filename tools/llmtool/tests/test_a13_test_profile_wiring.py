"""#2793 A13.0 wiring: completion classification follows llmtool.test_profile."""

import re
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
    Discovery then looks for that basename under ``tools/llmtool/tests``; an
    empty discovery must not be recorded as PASSED for the classified path.
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
                evidence = task_completion.run_bounded_worktree_tests(
                    root=root, source_sha=SOURCE_SHA,
                    diff_sha256=DIFF_SHA256, changed_paths=list(paths),
                    read_paths=list(paths), write_paths=[])
        self.assertEqual([item['path'] for item in evidence], paths)
        # A zero-test discovery must not be recorded as PASSED. Python 3.12+
        # exits 5 when no tests run; older interpreters exit 0 after printing
        # "Ran 0 tests". Both are the real runner outcome, and neither is a pass.
        self.assertIn(evidence[0]['exit_code'], (0, 5))
        self.assertEqual(evidence[0]['status'], 'FAILED')


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
        bytes_profile = test_profile.TestProfile((
            (re.compile(b'test'), 'unittest'),
        ))
        self._refused(mock.Mock(return_value=bytes_profile))

    def test_real_completion_refuses_a_bytes_pattern_profile(self):
        bytes_profile = test_profile.TestProfile((
            (re.compile(b'test'), 'unittest'),
        ))
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


if __name__ == '__main__':
    unittest.main()
