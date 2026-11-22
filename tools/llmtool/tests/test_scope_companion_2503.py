"""#2503 PR-5: companion-file advice (fixed pairings and tests that reference a written file)."""

import os
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from llmtool import read_scope_advice as advice
from llmtool import task_store, task_submission_cli


def _kinds(items):
    return [(item['kind'], item['path']) for item in items]


class CompanionPairingTest(unittest.TestCase):
    def test_claude_md_needs_guidance_drift_json_in_write(self):
        self.assertEqual(_kinds(advice.companion_items(read_paths=['CLAUDE.md'],
                                                       write_paths=['CLAUDE.md'])),
                         [('COMPANION_WRITE', 'tools/llmtool/guidance-drift.json')])
        self.assertEqual(_kinds(advice.companion_items(read_paths=['AGENTS.md'],
                                                       write_paths=['AGENTS.md'])),
                         [('COMPANION_WRITE', 'tools/llmtool/guidance-drift.json')])

    def test_no_advice_when_hash_file_is_written(self):
        self.assertEqual(advice.companion_items(
            read_paths=[], write_paths=['CLAUDE.md', 'tools/llmtool/guidance-drift.json']), [])

    def test_rule_json_needs_the_schema_file(self):
        items = advice.companion_items(read_paths=[], write_paths=['tools/llmtool/policy.json'])
        self.assertEqual(_kinds(items),
                         [('COMPANION_READ', 'tools/llmtool/scripts/guidance_drift_check.py')])
        self.assertIn('tools/llmtool/policy.json', items[0]['note'])
        self.assertEqual(advice.companion_items(
            read_paths=['tools/llmtool/scripts/guidance_drift_check.py'],
            write_paths=['tools/llmtool/policy.json']), [])

    def test_other_json_and_nested_json_are_not_rule_files(self):
        self.assertEqual(advice.companion_items(
            read_paths=[], write_paths=['tools/llmtool/tests/fixture.json',
                                        'docs/data.json', 'tools/llmtool/.d6-bootstrap-report.json']),
                         [])


class ReferencePatternTest(unittest.TestCase):
    def test_patterns_are_posix_ere_without_word_escape(self):
        for path in ('tools/llmtool/llmtool/developer_routes/agy.py',
                     'scripts/llmtool_monitor.py', 'tools/llmtool/policy.json'):
            for pattern in advice.reference_patterns(path):
                self.assertNotIn('\\b', pattern)

    def test_package_module_has_dotted_pattern(self):
        patterns = advice.reference_patterns('tools/llmtool/llmtool/developer_routes/agy.py')
        self.assertTrue(any('llmtool\\.developer_routes\\.agy' in p for p in patterns))


def _git(root, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.invalid',
               GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@example.invalid')
    subprocess.run(['git', '-C', root, '-c', 'commit.gpgsign=false', *args], check=True, env=env,
                   capture_output=True)


class ReferencingTestsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = self.root = self.tmp.name
        files = {
            'tools/llmtool/llmtool/widget.py': 'X = 1\n',
            'tools/llmtool/llmtool/widgets.py': 'Y = 1\n',
            'tools/llmtool/tests/test_widget.py': 'from llmtool import widget\n',
            'tools/llmtool/tests/test_other.py': 'import llmtool.widget as w\n',
            'tools/llmtool/tests/test_unrelated.py': 'from llmtool import widgets\n',
            'tools/llmtool/policy.json': '{}\n',
            'tools/llmtool/tests/test_policy_size.py': "P = 'tools/llmtool/policy.json'\n",
            'scripts/tool.py': 'Z = 1\n',
            'scripts/test_tool.py': 'import tool\n',
        }
        subprocess.run(['git', 'init', '-q', root], check=True)
        for name, text in files.items():
            Path(root, name).parent.mkdir(parents=True, exist_ok=True)
            Path(root, name).write_text(text)
        _git(root, 'add', '-A')
        _git(root, 'commit', '-qm', 'base')

    def refs(self, write, read=()):
        return advice.referencing_tests(root=self.root, commit='HEAD',
                                        read_paths=list(read), write_paths=list(write))

    def test_tests_importing_the_module_are_found(self):
        self.assertEqual(
            [item['path'] for item in self.refs(['tools/llmtool/llmtool/widget.py'])],
            ['tools/llmtool/tests/test_other.py', 'tools/llmtool/tests/test_widget.py'])

    def test_file_name_needs_a_leading_boundary(self):
        Path(self.root, 'tools/llmtool/tests/test_tox.py').write_text("N = 'tox.py'\n")
        Path(self.root, 'tools/llmtool/tests/test_x.py').write_text("N = 'scripts/x.py'\n")
        _git(self.root, 'add', '-A')
        _git(self.root, 'commit', '-qm', 'more')
        self.assertEqual([item['path'] for item in self.refs(['scripts/x.py'])],
                         ['tools/llmtool/tests/test_x.py'])

    def test_prefix_named_module_is_not_a_match(self):
        paths = [item['path'] for item in self.refs(['tools/llmtool/llmtool/widget.py'])]
        self.assertNotIn('tools/llmtool/tests/test_unrelated.py', paths)

    def test_tests_already_in_scope_are_not_reported(self):
        self.assertEqual(self.refs(['tools/llmtool/llmtool/widget.py'],
                                   read=['tools/llmtool/tests/test_widget.py',
                                         'tools/llmtool/tests/test_other.py']), [])

    def test_json_and_script_references(self):
        self.assertEqual([item['path'] for item in self.refs(['tools/llmtool/policy.json'])],
                         ['tools/llmtool/tests/test_policy_size.py'])
        self.assertEqual([item['path'] for item in self.refs(['scripts/tool.py'])],
                         ['scripts/test_tool.py'])

    def test_written_test_files_are_skipped(self):
        self.assertEqual(self.refs(['tools/llmtool/tests/test_widget.py']), [])

    def test_bounded_with_summary(self):
        fake = types.SimpleNamespace(returncode=0, stdout='\n'.join(
            'HEAD:tools/llmtool/tests/test_%02d.py' % i for i in range(12)) + '\n')
        items = advice.referencing_tests(root=self.root, commit='HEAD', read_paths=[],
                                         write_paths=['tools/llmtool/llmtool/widget.py'],
                                         runner=lambda *a, **k: fake)
        self.assertEqual(len([i for i in items if i['kind'] == 'TEST_REFERENCES_WRITE']),
                         advice.MAX_TESTS_PER_PATH)
        self.assertEqual(items[-1]['kind'], 'TEST_REFERENCES_MORE')
        self.assertIn('4 more', items[-1]['note'])

    def test_named_tests_rank_first(self):
        names = ['HEAD:tools/llmtool/tests/test_a%02d.py' % i for i in range(10)]
        names.append('HEAD:tools/llmtool/tests/test_widget_core.py')
        fake = types.SimpleNamespace(returncode=0, stdout='\n'.join(names))
        items = advice.referencing_tests(root=self.root, commit='HEAD', read_paths=[],
                                         write_paths=['tools/llmtool/llmtool/widget.py'],
                                         runner=lambda *a, **k: fake)
        self.assertIn('tools/llmtool/tests/test_widget_core.py', [i['path'] for i in items])

    def test_git_error_gives_no_result(self):
        def boom(*a, **k):
            raise OSError('no git')
        self.assertEqual(advice.referencing_tests(
            root=self.root, commit='HEAD', read_paths=[],
            write_paths=['tools/llmtool/llmtool/widget.py'], runner=boom), [])
        bad = types.SimpleNamespace(returncode=128, stdout='HEAD:x.py\n')
        self.assertEqual(advice.referencing_tests(
            root=self.root, commit='HEAD', read_paths=[],
            write_paths=['tools/llmtool/llmtool/widget.py'], runner=lambda *a, **k: bad), [])

    def test_items_are_valid_store_entries(self):
        items = (self.refs(['tools/llmtool/llmtool/widget.py']) +
                 advice.companion_items(read_paths=[], write_paths=['CLAUDE.md']))
        task_store._validate_read_scope_advice([advice.entry(items, None, None)])


class CombinedAdviceTest(unittest.TestCase):
    def test_one_failing_check_keeps_the_others(self):
        store = mock.Mock()
        store.get.return_value = {'pm_assignment': {'goal': 'g'}}
        args = types.SimpleNamespace(task_id='1' * 32, acceptance=['none'], repair_packet=None,
                                     high_debug_task=None, read_path=['CLAUDE.md'],
                                     write_path=['CLAUDE.md'], cwd='/nonexistent')
        with mock.patch.object(advice, 'referencing_tests', side_effect=RuntimeError('boom')):
            items = task_submission_cli._scope_advice_items(args, store)
        self.assertEqual(_kinds(items), [('COMPANION_WRITE', 'tools/llmtool/guidance-drift.json')])


if __name__ == '__main__':
    unittest.main()
