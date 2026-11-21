"""#2503 PR-6: route capability and size advice (path rules, shell asks, size limits)."""

import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from llmtool import read_scope_advice as advice
from llmtool import task_store, task_submission_cli


class RoutePathTest(unittest.TestCase):
    def test_opencode_refused_paths_come_from_its_validator(self):
        items = advice.route_path_items('opencode', [
            '.github/workflows/llmtool-linux-ci.yml', '.claude/skills/role-cards/pm.md',
            'tools/llmtool/llmtool/agy.py'])
        self.assertEqual([item['path'] for item in items],
                         ['.claude/skills/role-cards/pm.md',
                          '.github/workflows/llmtool-linux-ci.yml'])
        self.assertTrue(all(item['kind'] == 'ROUTE_PATH_DENIED' for item in items))

    def test_other_routes_get_no_path_advice(self):
        for provider in ('agy', 'claude', 'gw-agent', None):
            self.assertEqual(advice.route_path_items(provider, ['.claude/skills/x.md']), [])

    def test_validator_is_injectable(self):
        def validate(path):
            if path.startswith('secret/'):
                raise ValueError
        self.assertEqual([i['path'] for i in advice.route_path_items(
            'opencode', ['secret/a.py', 'ok/b.py'], validate=validate)], ['secret/a.py'])


class ShellAskTest(unittest.TestCase):
    def test_capability_comes_from_route_code(self):
        self.assertIs(advice.route_has_shell('claude'), False)
        self.assertIs(advice.route_has_shell('gw-agent'), False)
        self.assertIs(advice.route_has_shell('opencode'), False)
        self.assertIsNone(advice.route_has_shell('agy'))
        self.assertIsNone(advice.route_has_shell('unknown'))

    def test_ask_is_reported_for_routes_without_shell(self):
        for text in ('Run the focused tests after the edit.', 'Then git commit the change.',
                     'Execute the migration script.'):
            with self.subTest(text=text):
                items = advice.shell_items('claude', [text])
                self.assertEqual([i['kind'] for i in items], ['ROUTE_NO_SHELL'])

    def test_negated_limits_are_not_asks(self):
        for text in ('Do not run tests, use Git, publish.', 'Developers never run the tests.',
                     "Don't git commit.", 'Edit without running any command scripts.'):
            with self.subTest(text=text):
                self.assertEqual(advice.shell_items('claude', [text]), [])

    def test_route_with_shell_or_unknown_gets_nothing(self):
        self.assertEqual(advice.shell_items('agy', ['Run the tests.']), [])
        with mock.patch.object(advice, 'route_has_shell', return_value=True):
            self.assertEqual(advice.shell_items('claude', ['Run the tests.']), [])


class SizeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'big.py').write_bytes(b'x' * 300)
        (root / 'small.py').write_bytes(b'x' * 50)
        (root / 'long.py').write_text('\n' * 30)

    def test_read_file_and_total_limits(self):
        items = advice.size_items('opencode', self.tmp.name, ['big.py', 'small.py'], [],
                                  limits={'read_file_bytes': 200, 'read_total_bytes': 320})
        self.assertEqual([(i['kind'], i['path']) for i in items],
                         [('READ_FILE_TOO_LARGE', 'big.py'), ('READ_TOTAL_TOO_LARGE', None)])

    def test_write_line_guide(self):
        items = advice.size_items('gw-agent', self.tmp.name, [], ['long.py', 'small.py'],
                                  limits={'write_file_lines': 20})
        self.assertEqual([(i['kind'], i['path']) for i in items],
                         [('WRITE_FILE_TOO_LONG', 'long.py')])

    def test_limits_come_from_existing_configuration(self):
        from llmtool.opencode_direct_developer import (resolved_max_source_file_bytes,
                                                       resolved_max_source_total_bytes)
        self.assertEqual(advice.route_size_limits('opencode'),
                         {'read_file_bytes': resolved_max_source_file_bytes(),
                          'read_total_bytes': resolved_max_source_total_bytes()})
        self.assertEqual(set(advice.route_size_limits('gw-agent')), {'write_file_lines'})
        self.assertEqual(advice.route_size_limits('claude'), {})
        self.assertEqual(advice.route_size_limits('agy'), {})

    def test_missing_files_are_skipped(self):
        self.assertEqual(advice.size_items('opencode', self.tmp.name, ['nope.py'], [],
                                           limits={'read_file_bytes': 1}), [])


class CombinedRouteAdviceTest(unittest.TestCase):
    def test_route_items_join_the_list_and_validate(self):
        store = mock.Mock()
        store.get.return_value = {'pm_assignment': {'goal': 'Run the focused tests.'}}
        args = types.SimpleNamespace(task_id='1' * 32, provider='opencode', acceptance=['none'],
                                     repair_packet=None, high_debug_task=None,
                                     read_path=['.claude/skills/x.md'],
                                     write_path=['.claude/skills/x.md'], cwd='/nonexistent')
        items = task_submission_cli._scope_advice_items(args, store)
        kinds = [i['kind'] for i in items]
        self.assertIn('ROUTE_PATH_DENIED', kinds)
        self.assertIn('ROUTE_NO_SHELL', kinds)
        task_store._validate_read_scope_advice([advice.entry(items, None, None)])

    def test_route_check_failure_keeps_other_items(self):
        store = mock.Mock()
        store.get.return_value = {'pm_assignment': {'goal': 'g'}}
        args = types.SimpleNamespace(task_id='1' * 32, provider='opencode', acceptance=['none'],
                                     repair_packet=None, high_debug_task=None,
                                     read_path=['CLAUDE.md'], write_path=['CLAUDE.md'],
                                     cwd='/nonexistent')
        with mock.patch.object(advice, 'route_path_items', side_effect=RuntimeError('boom')), \
                mock.patch.object(advice, 'size_items', side_effect=RuntimeError('boom')):
            items = task_submission_cli._scope_advice_items(args, store)
        self.assertIn(('COMPANION_WRITE', 'tools/llmtool/guidance-drift.json'),
                      [(i['kind'], i['path']) for i in items])


if __name__ == '__main__':
    unittest.main()
