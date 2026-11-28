"""#2793 A13: read-scope test search locations come from the project TestProfile."""

import os
import re
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from llmtool import read_scope_advice as advice
from llmtool.test_profile import TestProfile, default_profile

WIDGET = 'tools/llmtool/llmtool/widget.py'
DEFAULT_TEST = 'tools/llmtool/tests/test_widget_default.py'
# Beside the tests, so it does not match the default unittest pattern; the
# historical fixed location searched the whole tests directory and found it.
DEFAULT_SUPPORT = 'tools/llmtool/tests/widget_support.py'
SCRIPT_TEST = 'scripts/test_widget_script.py'
CUSTOM_TEST = 'checks/test_widget_custom.py'
CUSTOM_NON_TEST = 'checks/helper.py'


def _git(root, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.invalid',
               GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@example.invalid')
    subprocess.run(['git', '-C', root, '-c', 'commit.gpgsign=false', *args], check=True,
                   capture_output=True, env=env)


class ReadScopeProfileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        files = {
            WIDGET: 'def widget_fn(value):\n    return value\n',
            DEFAULT_TEST: ('from llmtool import widget\n\n\n'
                           'def test_call():\n    assert widget.widget_fn(1) == 1\n'),
            DEFAULT_SUPPORT: ('from llmtool import widget\n\n\n'
                              'def build():\n    return widget.widget_fn(3)\n'),
            SCRIPT_TEST: 'import llmtool.widget\n',
            CUSTOM_TEST: ('from llmtool import widget\n\n\n'
                          'def test_call():\n    assert widget.widget_fn(2) == 2\n'),
            # References the written file but is not a test path in the custom profile.
            CUSTOM_NON_TEST: "NAME = 'widget.py'\n",
        }
        subprocess.run(['git', 'init', '-q', self.root], check=True)
        for name, text in files.items():
            Path(self.root, name).parent.mkdir(parents=True, exist_ok=True)
            Path(self.root, name).write_text(text)
        _git(self.root, 'add', '-A')
        _git(self.root, 'commit', '-qm', 'base')
        self.custom = TestProfile((
            (re.compile(r'checks/test_[A-Za-z0-9_]+\.py\Z'), 'unittest'),
        ))

    def refs(self, *, profile=None, texts=None):
        return advice.referencing_tests(
            root=self.root, commit='HEAD', read_paths=[], write_paths=[WIDGET],
            profile=profile, texts=texts)

    def kinds(self, items):
        return [(item['kind'], item['path']) for item in items]

    def test_default_profile_matches_no_profile_and_keeps_the_codes(self):
        self.assertEqual(self.refs(profile=default_profile()), self.refs())
        self.assertEqual(self.kinds(self.refs()), [
            (advice.TEST_REFERENCES_WRITE, SCRIPT_TEST),
            (advice.TEST_REFERENCES_WRITE, DEFAULT_TEST),
            (advice.TEST_REFERENCES_WRITE, DEFAULT_SUPPORT),
        ])

    def test_no_profile_uses_the_project_default_profile(self):
        with mock.patch('llmtool.test_profile.default_profile', return_value=self.custom):
            self.assertEqual(self.kinds(self.refs()), [
                (advice.TEST_REFERENCES_WRITE, CUSTOM_TEST),
            ])

    def test_default_profile_keeps_test_directory_support_modules(self):
        # F1: the fixed location returned every match under tools/llmtool/tests.
        self.assertIn((advice.TEST_REFERENCES_WRITE, DEFAULT_SUPPORT),
                      self.kinds(self.refs()))

    def test_non_default_profile_directory_is_searched(self):
        self.assertEqual(self.kinds(self.refs()), [
            (advice.TEST_REFERENCES_WRITE, SCRIPT_TEST),
            (advice.TEST_REFERENCES_WRITE, DEFAULT_TEST),
            (advice.TEST_REFERENCES_WRITE, DEFAULT_SUPPORT),
        ])
        # The custom directory is outside the default profile, and helper.py is
        # in the custom directory but is not one of its test paths.
        self.assertEqual(self.kinds(self.refs(profile=self.custom)), [
            (advice.TEST_REFERENCES_WRITE, CUSTOM_TEST),
        ])

    def test_precise_search_also_uses_the_profile_directory(self):
        self.assertEqual(self.kinds(self.refs(
            profile=self.custom, texts=['Change widget_fn to return 2.'])), [
            (advice.TEST_LOCKS_CHANGE, CUSTOM_TEST),
        ])

    def test_custom_profile_keeps_the_per_path_cap(self):
        fake = types.SimpleNamespace(returncode=0, stdout='\n'.join(
            'HEAD:checks/test_%02d.py' % number for number in range(12)) + '\n')
        items = advice.referencing_tests(
            root='/fake', commit='HEAD', read_paths=[], write_paths=[WIDGET],
            profile=self.custom, runner=lambda *args, **kwargs: fake)
        shown = [item for item in items if item['kind'] == advice.TEST_REFERENCES_WRITE]
        more = [item for item in items if item['kind'] == advice.TEST_REFERENCES_MORE]
        self.assertEqual(len(shown), advice.MAX_TESTS_PER_PATH)
        self.assertEqual(len(more), 1)
        self.assertIn('4 more', more[0]['note'])

    def test_invalid_profile_gives_one_fixed_refusal_without_fixed_fallback(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            raise AssertionError('invalid profile must not search fixed locations')

        invalid = (
            types.SimpleNamespace(entries=()),
            object(),
            types.SimpleNamespace(entries=[('checks/test_x.py', 'unittest')]),
        )
        for profile in invalid:
            with self.subTest(profile=profile):
                items = advice.referencing_tests(
                    root=self.root, commit='HEAD', read_paths=[], write_paths=[WIDGET],
                    runner=runner, profile=profile)
                self.assertEqual([(item['kind'], item['path']) for item in items],
                                 [(advice.TEST_PROFILE_INVALID, None)])
                self.assertIn('invalid', items[0]['note'])
                self.assertIn('refused', items[0]['note'])
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
