"""#2602 item (u): list only the tests that directly exercise a changed name."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from llmtool import read_scope_advice as advice
from llmtool import task_store

WIDGET = 'tools/llmtool/llmtool/widget.py'
TESTS = 'tools/llmtool/tests/'
CALL = TESTS + 'test_widget_call.py'
ADOPT = TESTS + 'test_widget_import_name.py'
IMPORT = TESTS + 'test_widget_import_only.py'
OTHER = TESTS + 'test_other_call.py'
LIMIT = TESTS + 'test_widget_limit.py'


def _git(root, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.invalid',
               GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@example.invalid')
    subprocess.run(['git', '-C', root, '-c', 'commit.gpgsign=false', *args], check=True,
                   capture_output=True, env=env)


class PreciseReferencesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        files = {
            WIDGET: ('WIDGET_LIMIT = 3\n\n\n'
                     'def widget_fn(value):\n    return value\n\n\n'
                     'def other_fn():\n    return 1\n'),
            CALL: ('from llmtool import widget\n\n\n'
                   'def test_call():\n    assert widget.widget_fn(1) == 1\n'),
            ADOPT: 'from llmtool.widget import widget_fn\n',
            IMPORT: 'from llmtool import widget\n',
            OTHER: ('from llmtool import widget\n\n\n'
                    'def test_call():\n    assert widget.other_fn() == 1\n'),
            LIMIT: ('from llmtool import widget\n\n\n'
                    'def test_limit():\n    assert widget.WIDGET_LIMIT == 3\n'),
        }
        subprocess.run(['git', 'init', '-q', self.root], check=True)
        for name, text in files.items():
            Path(self.root, name).parent.mkdir(parents=True, exist_ok=True)
            Path(self.root, name).write_text(text)
        _git(self.root, 'add', '-A')
        _git(self.root, 'commit', '-qm', 'base')

    def refs(self, write=(WIDGET,), read=(), texts=None):
        return advice.referencing_tests(root=self.root, commit='HEAD', read_paths=list(read),
                                        write_paths=list(write), texts=texts)

    def kinds(self, items):
        return [(item['kind'], item['path']) for item in items]

    def test_u1_no_texts_keeps_the_module_level_list(self):
        expected = [(advice.TEST_REFERENCES_WRITE, CALL),
                    (advice.TEST_REFERENCES_WRITE, ADOPT),
                    (advice.TEST_REFERENCES_WRITE, IMPORT),
                    (advice.TEST_REFERENCES_WRITE, LIMIT),
                    (advice.TEST_REFERENCES_WRITE, OTHER)]
        self.assertEqual(self.kinds(self.refs()), expected)
        self.assertEqual(self.kinds(self.refs(texts=None)), expected)
        self.assertEqual(self.kinds(self.refs(texts=[])), expected)

    def test_u2_texts_naming_a_function_list_only_its_callers(self):
        items = self.refs(texts=['Change widget_fn to return 2.'])
        self.assertEqual(self.kinds(items), [(advice.TEST_LOCKS_CHANGE, CALL)])
        self.assertIn('widget_fn', items[0]['note'])
        self.assertIn('locked-test rule', items[0]['note'])

    def test_u2_texts_naming_other_names(self):
        self.assertEqual([item['path'] for item in self.refs(texts=['Update WIDGET_LIMIT to 4.'])],
                         [LIMIT])
        self.assertEqual([item['path'] for item in self.refs(texts=['`other_fn()` returns 2'])],
                         [OTHER])

    def test_u3_test_outside_write_is_flagged_even_when_read(self):
        items = self.refs(read=[CALL], texts=['Change widget_fn to return 2.'])
        self.assertEqual(self.kinds(items), [(advice.TEST_LOCKS_CHANGE, CALL)])
        self.assertIn('may lock the behaviour the acceptance changes', items[0]['note'])
        self.assertIn('belongs in write under the locked-test rule', items[0]['note'])

    def test_u3_test_in_write_is_not_flagged(self):
        self.assertEqual(self.refs(write=[WIDGET, CALL],
                                   texts=['Change widget_fn to return 2.']), [])

    def test_u4_no_changed_name_falls_back_to_the_module_list(self):
        fallback = self.refs()
        self.assertEqual(self.kinds(self.refs(texts=['Update the documentation only.'])),
                         self.kinds(fallback))
        self.assertEqual(self.kinds(self.refs(texts=['No name from the module appears.'])),
                         self.kinds(fallback))

    def test_u5_internal_error_keeps_the_existing_items(self):
        with mock.patch.object(advice, '_precise_referencing_tests',
                               side_effect=RuntimeError('boom')):
            self.assertEqual(self.kinds(self.refs(texts=['Change widget_fn.'])),
                             self.kinds(self.refs()))

    def test_u2_bound_reports_the_total(self):
        for number in range(12):
            name = TESTS + 'test_widget_extra_%02d.py' % number
            Path(self.root, name).write_text(
                'from llmtool import widget\n\n\ndef test_call():\n'
                '    assert widget.widget_fn(%d) == %d\n' % (number, number))
        _git(self.root, 'add', '-A')
        _git(self.root, 'commit', '-qm', 'more')
        items = self.refs(texts=['Change widget_fn to return 2.'])
        shown = [item for item in items if item['kind'] == advice.TEST_LOCKS_CHANGE]
        more = [item for item in items if item['kind'] == advice.TEST_REFERENCES_MORE]
        self.assertEqual(len(shown), advice.MAX_TESTS_PER_PATH)
        self.assertEqual(len(more), 1)
        self.assertIn('13 test files directly exercise widget_fn', more[0]['note'])
        self.assertIn('shown: the first %d' % advice.MAX_TESTS_PER_PATH, more[0]['note'])

    def test_items_stay_valid_store_entries(self):
        items = self.refs(texts=['Change widget_fn to return 2.'])
        task_store._validate_read_scope_advice([advice.entry(items, None, None)])


if __name__ == '__main__':
    unittest.main()
