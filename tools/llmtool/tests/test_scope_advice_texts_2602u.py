"""#2602 item (u) P3b: pass spec texts from task_submission_cli._scope_advice_items to referencing_tests."""

import io
import os
import subprocess
import tempfile
import types
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from llmtool import read_scope_advice as advice
from llmtool import task_store, task_submission_cli

WIDGET = 'tools/llmtool/llmtool/widget.py'
TESTS = 'tools/llmtool/tests/'
CALL = TESTS + 'test_widget_call.py'
OTHER = TESTS + 'test_other_call.py'
IMPORT = TESTS + 'test_widget_import_only.py'


def _git(root, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.invalid',
               GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@example.invalid')
    subprocess.run(['git', '-C', root, '-c', 'commit.gpgsign=false', *args],
                   check=True, capture_output=True, env=env)


class ScopeAdviceTextsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        files = {
            WIDGET: ('def widget_fn(value):\n    return value\n\n\n'
                     'def other_fn():\n    return 1\n'),
            CALL: ('from llmtool import widget\n\n\n'
                   'def test_call():\n    assert widget.widget_fn(1) == 1\n'),
            OTHER: ('from llmtool import widget\n\n\n'
                    'def test_call():\n    assert widget.other_fn() == 1\n'),
            IMPORT: 'from llmtool import widget\n',
        }
        subprocess.run(['git', 'init', '-q', self.root], check=True)
        for name, text in files.items():
            Path(self.root, name).parent.mkdir(parents=True, exist_ok=True)
            Path(self.root, name).write_text(text)
        _git(self.root, 'add', '-A')
        _git(self.root, 'commit', '-qm', 'base')

    def _args(self, **kw):
        values = dict(task_id='1' * 32, provider='agy', acceptance=['none'],
                      repair_packet=None, high_debug_task=None,
                      read_path=[WIDGET], write_path=[WIDGET],
                      read_scope_reason=None, read_scope_check=False,
                      cwd=self.root)
        values.update(kw)
        return types.SimpleNamespace(**values)

    def _store(self, goal='g', rca_report=None):
        store = mock.Mock()

        def get(task_id):
            if task_id == '2' * 32:
                return {'high_debug': [{'report': rca_report}]}
            return {'pm_assignment': {'goal': goal}}

        store.get.side_effect = get
        return store

    def test_goal_naming_changed_function_yields_precise_locked_test(self):
        items = task_submission_cli._scope_advice_items(
            self._args(), self._store(goal='Change widget_fn to return 2.'))
        self.assertEqual([(item['kind'], item['path']) for item in items],
                         [(advice.TEST_LOCKS_CHANGE, CALL)])
        self.assertIn('widget_fn', items[0]['note'])
        self.assertIn('locked-test rule', items[0]['note'])
        task_store._validate_read_scope_advice([advice.entry(items, None, None)])

    def test_locked_test_outside_write_is_flagged_even_when_read(self):
        items = task_submission_cli._scope_advice_items(
            self._args(read_path=[WIDGET, CALL]),
            self._store(goal='Change widget_fn to return 2.'))
        self.assertEqual([(item['kind'], item['path']) for item in items],
                         [(advice.TEST_LOCKS_CHANGE, CALL)])

    def test_test_in_write_is_not_flagged(self):
        items = task_submission_cli._scope_advice_items(
            self._args(write_path=[WIDGET, CALL]),
            self._store(goal='Change widget_fn to return 2.'))
        self.assertEqual(items, [])

    def test_without_matching_names_output_equals_module_level_list(self):
        args = self._args()
        items = task_submission_cli._scope_advice_items(
            args, self._store(goal='Update the documentation only.'))
        expected = advice.referencing_tests(
            root=self.root, commit='HEAD',
            read_paths=args.read_path, write_paths=args.write_path)
        self.assertEqual(items, expected)
        self.assertEqual(
            [(item['kind'], item['path']) for item in items],
            [(advice.TEST_REFERENCES_WRITE, OTHER),
             (advice.TEST_REFERENCES_WRITE, CALL),
             (advice.TEST_REFERENCES_WRITE, IMPORT)])

    def test_acceptance_repair_packet_and_high_debug_reports_reach_check(self):
        cases = [
            self._args(acceptance=['Change widget_fn to return 2.']),
            self._args(repair_packet={'finding': 'Update widget_fn behaviour'}),
            self._args(high_debug_task='2' * 32),
        ]
        for args in cases:
            with self.subTest(args=args):
                store = self._store(goal='g', rca_report='Fix widget_fn')
                items = task_submission_cli._scope_advice_items(args, store)
                self.assertEqual([(item['kind'], item['path']) for item in items],
                                 [(advice.TEST_LOCKS_CHANGE, CALL)])

    def test_precise_lookup_error_falls_back_to_module_level_list(self):
        args = self._args()
        expected = advice.referencing_tests(
            root=self.root, commit='HEAD',
            read_paths=args.read_path, write_paths=args.write_path)
        with mock.patch.object(advice, '_precise_referencing_tests',
                               side_effect=RuntimeError('boom')):
            items = task_submission_cli._scope_advice_items(
                args, self._store(goal='Change widget_fn to return 2.'))
        self.assertEqual(items, expected)

    def test_advice_stays_advisory_and_never_blocks_dispatch(self):
        store = self._store(goal='Change widget_fn to return 2.')
        err = io.StringIO()
        with redirect_stderr(err):
            code = task_submission_cli._delegate_with_scope_advice(
                self._args(), store, lambda: 42)
        self.assertEqual(code, 42)
        self.assertIn(advice.TEST_LOCKS_CHANGE, err.getvalue())
        self.assertIn(CALL, err.getvalue())
        store.append_read_scope_advice.assert_called_once()


if __name__ == '__main__':
    unittest.main()
