"""#2793 A13.2: PR-changed test modules follow the project TestProfile.

The admission-side derivation (``task_advance._pr_changed_test_modules``) now
reads the ``unittest`` entries of a project ``TestProfile`` and applies the
existing lowercase module-stem constraint on top.  The validation side
(``remote_test_part1._task_path_modules`` and ``_admitted_modules``) still
derives declared stems from hard-coded tool test paths at the base commit, so
validation and admission agreement on the required coverage set is pending for
the non-default case (boundary note section 1).
"""

import re
import unittest
from unittest.mock import patch

from llmtool import task_advance
from llmtool.test_profile import TestProfile

HEAD = 'a' * 40
OTHER = 'b' * 40
PR = 17
UNAVAILABLE = task_advance.PR_CHANGED_TESTS_UNAVAILABLE


def snapshot(*, head=HEAD, changed_files=None):
    value = {'number': PR, 'head': {'sha': head}, 'state': 'open', 'merged': False}
    if changed_files is not None:
        value['changed_files'] = changed_files
    return value


def github_get(*entries):
    """A files-only GitHub reader; entries are (filename, status) pairs."""
    def get(path):
        if path.startswith(f'pulls/{PR}/files?per_page='):
            return [{'filename': name, 'status': status} for name, status in entries]
        return []
    return get


class DefaultProfileTests(unittest.TestCase):
    """The default profile keeps the previous results exactly."""

    def test_unittest_modules_use_the_lowercase_stem_constraint(self):
        entries = (
            ('tools/llmtool/tests/test_added_2793.py', 'added'),
            ('tools/llmtool/tests/test_changed_2793.py', 'modified'),
            ('tools/llmtool/tests/test_Upper_2793.py', 'added'),
            ('scripts/test_script_2793.py', 'added'),
            ('docs/guide.md', 'modified'),
        )
        modules, code = task_advance._pr_changed_test_modules(
            PR, HEAD, github_get(*entries), snapshot(changed_files=len(entries)))
        self.assertEqual(code, None)
        self.assertEqual(modules, ('test_added_2793', 'test_changed_2793'))

    def test_removed_module_is_ignored(self):
        entries = (('tools/llmtool/tests/test_removed_2793.py', 'removed'),)
        modules, code = task_advance._pr_changed_test_modules(
            PR, HEAD, github_get(*entries), snapshot(changed_files=1))
        self.assertEqual(code, None)
        self.assertEqual(modules, ())

    def test_unreadable_file_list_is_unavailable(self):
        def get(_path):
            raise OSError('unavailable')

        modules, code = task_advance._pr_changed_test_modules(
            PR, HEAD, get, snapshot(changed_files=1))
        self.assertEqual(modules, None)
        self.assertEqual(code, UNAVAILABLE)

    def test_changed_file_count_mismatch_is_unavailable(self):
        entries = (('tools/llmtool/tests/test_added_2793.py', 'added'),)
        modules, code = task_advance._pr_changed_test_modules(
            PR, HEAD, github_get(*entries), snapshot(changed_files=2))
        self.assertEqual(modules, None)
        self.assertEqual(code, UNAVAILABLE)

    def test_pr_head_mismatch_is_unavailable(self):
        entries = (('tools/llmtool/tests/test_added_2793.py', 'added'),)
        modules, code = task_advance._pr_changed_test_modules(
            PR, OTHER, github_get(*entries), snapshot(changed_files=1))
        self.assertEqual(modules, None)
        self.assertEqual(code, UNAVAILABLE)

    def test_unknown_file_status_is_unavailable(self):
        entries = (('tools/llmtool/tests/test_added_2793.py', 'mystery'),)
        modules, code = task_advance._pr_changed_test_modules(
            PR, HEAD, github_get(*entries), snapshot(changed_files=1))
        self.assertEqual(modules, None)
        self.assertEqual(code, UNAVAILABLE)


class ProfileFailureTests(unittest.TestCase):
    """A missing, raising or invalid profile fails closed, never by default paths."""

    ENTRIES = (('tools/llmtool/tests/test_default_2793.py', 'added'),)

    def _call(self, profile):
        return task_advance._pr_changed_test_modules(
            PR, HEAD, github_get(*self.ENTRIES), snapshot(changed_files=1),
            profile=profile)

    def test_missing_profile_is_unavailable(self):
        self.assertEqual(self._call(None), (None, UNAVAILABLE))

    def test_invalid_profile_is_unavailable(self):
        self.assertEqual(self._call(object()), (None, UNAVAILABLE))

    def test_raising_default_profile_is_unavailable(self):
        with patch.object(task_advance, 'default_profile',
                          side_effect=RuntimeError('profile unavailable')):
            modules, code = task_advance._pr_changed_test_modules(
                PR, HEAD, github_get(*self.ENTRIES), snapshot(changed_files=1))
        self.assertEqual(modules, None)
        self.assertEqual(code, UNAVAILABLE)

    def test_default_profile_returning_none_is_unavailable(self):
        with patch.object(task_advance, 'default_profile', return_value=None):
            modules, code = task_advance._pr_changed_test_modules(
                PR, HEAD, github_get(*self.ENTRIES), snapshot(changed_files=1))
        self.assertEqual(modules, None)
        self.assertEqual(code, UNAVAILABLE)


class NonDefaultProfileTests(unittest.TestCase):
    """A supplied profile is honoured and the default paths are not a fallback."""

    PROFILE = TestProfile((
        (re.compile(r'custom/tests/test_(?:alpha|beta)_2793\.py\Z'), 'unittest'),
        (re.compile(r'custom/tests/helper_[a-z0-9_]+\.py\Z'), 'script'),
    ))

    def test_non_default_unittest_entries_are_honoured(self):
        entries = (
            ('custom/tests/test_alpha_2793.py', 'added'),
            ('custom/tests/test_gamma_2793.py', 'added'),
            ('custom/tests/helper_2793.py', 'modified'),
            ('tools/llmtool/tests/test_default_2793.py', 'added'),
        )
        modules, code = task_advance._pr_changed_test_modules(
            PR, HEAD, github_get(*entries), snapshot(changed_files=len(entries)),
            profile=self.PROFILE)
        self.assertEqual(code, None)
        self.assertEqual(modules, ('test_alpha_2793',))

    def test_script_entries_are_not_module_candidates(self):
        profile = TestProfile((
            (re.compile(r'custom/tests/test_[a-z0-9_]+\.py\Z'), 'script'),
        ))
        entries = (('custom/tests/test_script_2793.py', 'added'),)
        modules, code = task_advance._pr_changed_test_modules(
            PR, HEAD, github_get(*entries), snapshot(changed_files=1),
            profile=profile)
        self.assertEqual(code, None)
        self.assertEqual(modules, ())

    def test_default_profile_is_not_used_when_a_profile_is_supplied(self):
        entries = (('tools/llmtool/tests/test_default_2793.py', 'added'),)
        modules, code = task_advance._pr_changed_test_modules(
            PR, HEAD, github_get(*entries), snapshot(changed_files=1),
            profile=self.PROFILE)
        self.assertEqual(code, None)
        self.assertEqual(modules, ())


if __name__ == '__main__':
    unittest.main()
