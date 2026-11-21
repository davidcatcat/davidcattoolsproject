"""#2503 PR-4: advisory read-scope check before a Developer dispatch or repair."""

import argparse
import io
import json
import os
import subprocess
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from llmtool import read_scope_advice, task_store, task_submission_cli
from llmtool.errors import LLMToolError

EXISTING = {'tools/llmtool/llmtool/agy.py', 'tools/llmtool/llmtool/review_result.py',
            'docs/ci/README.md', '.claude/skills/role-cards/pm.md',
            'tools/llmtool/tests/test_task_store.py'}


def exists(path):
    return path in EXISTING


def find(texts, read=(), write=()):
    return read_scope_advice.missing_read_paths(
        texts=texts, read_paths=list(read), write_paths=list(write), exists=exists)


class MissingReadPathsTest(unittest.TestCase):
    def test_missing_path_in_acceptance_is_found(self):
        self.assertEqual(find(['Match the parser in tools/llmtool/llmtool/review_result.py.']),
                         ['tools/llmtool/llmtool/review_result.py'])

    def test_path_already_in_read_or_write_is_not_reported(self):
        self.assertEqual(find(['Edit docs/ci/README.md and check .claude/skills/role-cards/pm.md'],
                              read=['.claude/skills/role-cards/pm.md'],
                              write=['docs/ci/README.md']), [])

    def test_nonexistent_path_is_ignored(self):
        self.assertEqual(find(['See tools/llmtool/llmtool/does_not_exist.py']), [])

    def test_repair_findings_and_rca_reports_are_scanned(self):
        packet = {'verdict_text': 'F2 [High] cite tools/llmtool/llmtool/agy.py parse_output'}
        self.assertEqual(find([packet]), ['tools/llmtool/llmtool/agy.py'])
        self.assertEqual(find([json.dumps(packet)]), ['tools/llmtool/llmtool/agy.py'])
        rca = ['RCA: the fixture builder is in tools/llmtool/tests/test_task_store.py']
        self.assertEqual(find([None, [], rca]), ['tools/llmtool/tests/test_task_store.py'])

    def test_result_is_sorted_and_unique(self):
        self.assertEqual(find(['tools/llmtool/llmtool/review_result.py docs/ci/README.md',
                               ['docs/ci/README.md again']]),
                         ['docs/ci/README.md', 'tools/llmtool/llmtool/review_result.py'])

    def test_git_exists_uses_commit(self):
        with tempfile.TemporaryDirectory() as root:
            env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.invalid',
                       GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@example.invalid')
            subprocess.run(['git', 'init', '-q', root], check=True, env=env)
            Path(root, 'docs').mkdir()
            Path(root, 'docs/a.md').write_text('x')
            subprocess.run(['git', '-C', root, 'add', 'docs/a.md'], check=True, env=env)
            subprocess.run(['git', '-C', root, '-c', 'commit.gpgsign=false', 'commit', '-qm', 'a'],
                           check=True, env=env)
            Path(root, 'docs/b.md').write_text('untracked')
            check = read_scope_advice.git_exists(root)
            self.assertTrue(check('docs/a.md'))
            self.assertFalse(check('docs/b.md'))


def _entry(**kw):
    value = read_scope_advice.entry(
        [read_scope_advice.item('READ_MISSING', 'docs/ci/README.md', 'note')], None, 'a' * 40)
    value.update(kw)
    return value


class StoreValidationTest(unittest.TestCase):
    def test_valid_entries_pass(self):
        task_store._validate_read_scope_advice([_entry()])
        task_store._validate_read_scope_advice([_entry(items=[], pm_reason='named as history',
                                                       source_sha=None)])

    def test_invalid_entries_fail(self):
        bad_items = [
            {'kind': 'lower', 'path': None, 'note': None},
            {'kind': 'READ_MISSING', 'path': '../etc/passwd', 'note': None},
            {'kind': 'READ_MISSING', 'path': '/abs/path.py', 'note': None},
            {'kind': 'READ_MISSING', 'path': None, 'note': 'x' * 301},
            {'kind': 'READ_MISSING', 'path': None},
        ]
        bad = ['x', [], [_entry(at='now')], [_entry(pm_reason='x' * 501)],
               [_entry(pm_reason='')], [_entry(source_sha='nothex')], [_entry()] * 17,
               [dict(_entry(), extra=1)]]
        bad += [[_entry(items=[value])] for value in bad_items]
        for value in bad:
            with self.subTest(value=str(value)[:80]):
                with self.assertRaises(LLMToolError):
                    task_store._validate_read_scope_advice(value)

    def test_transition_is_append_only(self):
        old = [_entry()]
        new = old + [_entry(pm_reason='reason')]
        task_store._check_read_scope_advice_transition(old, new)
        task_store._check_read_scope_advice_transition(None, old)
        for before, after in ((new, old), (old, old), (old, [_entry(pm_reason='x')])):
            with self.assertRaises(LLMToolError):
                task_store._check_read_scope_advice_transition(before, after)


def _args(**kw):
    values = dict(task_id='1' * 32, provider='agy', acceptance=['docs/ci/README.md'],
                  read_path=['tools/x.py'], write_path=['tools/x.py'], repair_packet=None,
                  high_debug_task=None, read_scope_reason=None, read_scope_check=False,
                  timeout=None, reassign_from=None)
    values.update(kw)
    return types.SimpleNamespace(**values)


def _wrap(args, store):
    return task_submission_cli._delegate_with_scope_advice(args, store, lambda: 0)


class DelegateHookTest(unittest.TestCase):
    def setUp(self):
        self.store = mock.Mock()
        self.store.get.return_value = {'pm_assignment': {'goal': 'g'}, 'request': 'r'}
        patcher = mock.patch.object(read_scope_advice, 'git_exists', return_value=exists)
        patcher.start()
        self.addCleanup(patcher.stop)

    def recorded(self):
        self.store.append_read_scope_advice.assert_called_once()
        return self.store.append_read_scope_advice.call_args.args[1]

    def test_missing_files_are_warned_and_recorded(self):
        err = io.StringIO()
        with redirect_stderr(err):
            _wrap(_args(), self.store)
        self.assertIn('SCOPE_ADVICE', err.getvalue())
        self.assertIn('docs/ci/README.md', err.getvalue())
        entry = self.recorded()
        self.assertEqual([item['path'] for item in entry['items']], ['docs/ci/README.md'])
        self.assertEqual(entry['items'][0]['kind'], 'READ_MISSING')
        self.assertIsNone(entry['pm_reason'])
        task_store._validate_read_scope_advice([entry])

    def test_reason_is_recorded(self):
        with redirect_stderr(io.StringIO()):
            _wrap(
                _args(read_scope_reason='only named as history'), self.store)
        self.assertEqual(self.recorded()['pm_reason'], 'only named as history')

    def test_nothing_missing_records_nothing(self):
        err = io.StringIO()
        with redirect_stderr(err):
            _wrap(_args(acceptance=['no paths']), self.store)
        self.store.append_read_scope_advice.assert_not_called()
        self.assertEqual(err.getvalue(), '')

    def test_goal_and_rca_report_are_scanned(self):
        def get(task_id):
            if task_id == '2' * 32:
                return {'high_debug': [{'report': 'fixture in tools/llmtool/tests/test_task_store.py'}]}
            return {'pm_assignment': {'goal': 'cite tools/llmtool/llmtool/agy.py'}}
        self.store.get.side_effect = get
        with redirect_stderr(io.StringIO()):
            _wrap(
                _args(acceptance=['none'], high_debug_task='2' * 32), self.store)
        self.assertEqual([item['path'] for item in self.recorded()['items']],
                         ['tools/llmtool/llmtool/agy.py', 'tools/llmtool/tests/test_task_store.py'])

    def test_repair_packet_is_scanned(self):
        with redirect_stderr(io.StringIO()):
            _wrap(_args(
                acceptance=['none'],
                repair_packet={'finding': 'see tools/llmtool/llmtool/agy.py'}), self.store)
        self.assertEqual([item['path'] for item in self.recorded()['items']],
                         ['tools/llmtool/llmtool/agy.py'])

    def test_advice_errors_give_no_advice_and_dispatch_still_runs(self):
        self.store.get.side_effect = RuntimeError('boom')
        with redirect_stderr(io.StringIO()):
            self.assertEqual(_wrap(_args(), self.store), 0)
        self.store.append_read_scope_advice.assert_not_called()

    def test_dispatch_runs_unchanged_and_advice_is_recorded_after_it(self):
        order = []
        self.store.append_read_scope_advice.side_effect = lambda *a: order.append('record')
        with redirect_stderr(io.StringIO()):
            code = task_submission_cli._delegate_with_scope_advice(
                _args(), self.store, lambda: order.append('dispatch') or 7)
        self.assertEqual(code, 7)
        self.assertEqual(order, ['dispatch', 'record'])

    def test_dispatch_error_propagates_and_nothing_is_recorded(self):
        def fail():
            raise LLMToolError('ROUTE_REFUSED')
        with redirect_stderr(io.StringIO()), self.assertRaises(LLMToolError):
            task_submission_cli._delegate_with_scope_advice(_args(), self.store, fail)
        self.store.append_read_scope_advice.assert_not_called()

    def test_record_failure_never_changes_the_result(self):
        self.store.append_read_scope_advice.side_effect = LLMToolError('full')
        err = io.StringIO()
        with redirect_stderr(err):
            code = task_submission_cli._delegate_with_scope_advice(_args(), self.store, lambda: 0)
        self.assertEqual(code, 0)
        self.assertIn('SCOPE_ADVICE_NOT_RECORDED', err.getvalue())

    def test_check_only_prints_and_does_not_record_or_dispatch(self):
        out = io.StringIO()
        call = mock.Mock()
        with redirect_stdout(out):
            code = task_submission_cli._delegate_with_scope_advice(
                _args(read_scope_check=True), self.store, call)
        self.assertEqual(code, 0)
        advice = json.loads(out.getvalue())['scope_advice']
        self.assertEqual([item['path'] for item in advice], ['docs/ci/README.md'])
        self.store.append_read_scope_advice.assert_not_called()
        call.assert_not_called()

    def test_checked_entry_and_repair_use_the_wrapper(self):
        import inspect
        from llmtool import review_repair
        self.assertIn('_delegate_with_scope_advice',
                      inspect.getsource(task_submission_cli.run_task_delegate_checked))
        self.assertIn('_delegate_with_scope_advice',
                      inspect.getsource(review_repair._delegate_repair))
        self.assertNotIn('_scope_advice', inspect.getsource(task_submission_cli.run_task_delegate))

    def test_parser_accepts_new_flags(self):
        parser = argparse.ArgumentParser()
        task_submission_cli.add_task_submission_parsers(parser.add_subparsers(dest='action'))
        base = ['delegate', '1' * 32, '--provider', 'agy', '--selection-source', 'PM',
                '--executor-task', 'd6', '--read-path', 'a.py', '--write-path', 'a.py',
                '--acceptance', 'x', '--egress-basis', 'y']
        args = parser.parse_args(base + ['--read-scope-reason', 'named only as history',
                                         '--read-scope-check'])
        self.assertEqual(args.read_scope_reason, 'named only as history')
        self.assertTrue(args.read_scope_check)
        args = parser.parse_args(base)
        self.assertIsNone(args.read_scope_reason)
        self.assertFalse(args.read_scope_check)


class TaskStoreAppendTest(unittest.TestCase):
    def test_append_on_a_real_store(self):
        from task_fixtures import pm_responded_task
        with tempfile.TemporaryDirectory() as tmp:
            store = task_store.TaskStore(Path(tmp).resolve() / "tasks")
            task = pm_responded_task(route='agy')
            store.create(task)
            first = store.append_read_scope_advice(task['task_id'], _entry())
            self.assertEqual(len(first['read_scope_advice']), 1)
            second = store.append_read_scope_advice(task['task_id'], _entry(pm_reason='why'))
            self.assertEqual([e['pm_reason'] for e in second['read_scope_advice']], [None, 'why'])
            self.assertEqual(store.get(task['task_id'])['read_scope_advice'],
                             second['read_scope_advice'])
            with self.assertRaises(LLMToolError):
                store.compare_and_update(task['task_id'],
                                         expected={'read_scope_advice': second['read_scope_advice']},
                                         read_scope_advice=[_entry()])


if __name__ == '__main__':
    unittest.main()
