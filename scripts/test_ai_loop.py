"""Offline fixture checks; no project clone or provider calls."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('ai_loop', Path(__file__).with_name('ai_loop.py'))
loop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loop)
HEAD = 'a' * 40


def result(status='PASS', findings=None):
    return {'status': status, 'summary': 'checked', 'reviewed_head': HEAD,
            'findings': findings or []}


class ResultTests(unittest.TestCase):
    def test_success_wrapper_and_exact_revision(self):
        raw = {'is_error': False, 'subtype': 'success', 'structured_output': result()}
        self.assertEqual(loop.parse_claude(raw, HEAD)['status'], 'PASS')

    def test_exit_success_without_structured_result_is_not_pass(self):
        with self.assertRaises(loop.Stop):
            loop.parse_claude({'is_error': False, 'subtype': 'success', 'result': 'PASS'}, HEAD)

    def test_quota_or_turn_limit_cannot_pass(self):
        for raw in ({'is_error': True, 'subtype': 'success', 'structured_output': result()},
                    {'is_error': False, 'subtype': 'error_max_turns', 'structured_output': result()}):
            with self.assertRaises(loop.Stop):
                loop.parse_claude(raw, HEAD)

    def test_stale_review_cannot_pass(self):
        with self.assertRaises(loop.Stop):
            loop.validate_result(result(), 'review', 'b' * 40)

    def test_high_finding_cannot_be_hidden_as_nonblocking(self):
        finding = {'severity': 'High', 'blocking': False, 'file': 'source.py',
                   'detail': 'reproducible failure', 'evidence': 'test X fails'}
        with self.assertRaises(loop.Stop):
            loop.validate_result(result(findings=[finding]), 'review', HEAD)

    def test_low_nonblocking_improvement_does_not_force_repairs(self):
        finding = {'severity': 'Low', 'blocking': False, 'file': 'source.py',
                   'detail': 'optional naming change', 'evidence': 'line 20'}
        self.assertEqual(loop.validate_result(result(findings=[finding]), 'review', HEAD)['status'], 'PASS')

    def test_fail_needs_actionable_blocker(self):
        with self.assertRaises(loop.Stop):
            loop.validate_result(result('FAIL'), 'review', HEAD)

    def test_findings_need_evidence(self):
        finding = {'severity': 'High', 'blocking': True, 'file': 'source.py',
                   'detail': 'bug', 'evidence': ''}
        with self.assertRaises(loop.Stop):
            loop.validate_result(result('FAIL', [finding]), 'review', HEAD)

    def test_developer_requires_full_contract(self):
        with self.assertRaises(loop.Stop):
            loop.validate_result({'status': 'PASS'}, 'developer')


class ScopeTests(unittest.TestCase):
    def git_value(self, root, *args):
        if args == ('branch', '--show-current'):
            return 'feat/example'
        if args == ('rev-parse', 'HEAD'):
            return HEAD
        return ''

    def test_unrelated_untracked_work_preserved(self):
        with patch.object(loop, 'git', side_effect=self.git_value), \
             patch.object(loop, 'untracked', return_value={'notes.txt': 'digest'}), \
             patch.object(loop, 'changed', return_value=['notes.txt', 'source.py']):
            self.assertEqual(loop.check_scope(Path('.'), {'allowed_files': ['source.py']},
                {'notes.txt': 'digest'}, 'feat/example', HEAD), ['source.py'])

    def test_unrelated_work_changes_stop(self):
        with patch.object(loop, 'git', side_effect=self.git_value), \
             patch.object(loop, 'untracked', return_value={'notes.txt': 'new'}):
            with self.assertRaises(loop.Stop):
                loop.check_scope(Path('.'), {'allowed_files': ['source.py']},
                    {'notes.txt': 'old'}, 'feat/example', HEAD)

    def test_scope_expansion_stops(self):
        with patch.object(loop, 'git', side_effect=self.git_value), \
             patch.object(loop, 'untracked', return_value={}), \
             patch.object(loop, 'changed', return_value=['AGENTS.md']):
            with self.assertRaises(loop.Stop):
                loop.check_scope(Path('.'), {'allowed_files': ['source.py']}, {}, 'feat/example', HEAD)

    def test_agent_commit_stops(self):
        with patch.object(loop, 'git', return_value='wrong'):
            with self.assertRaises(loop.Stop):
                loop.check_scope(Path('.'), {'allowed_files': ['source.py']}, {}, 'feat/example', HEAD)


class RuntimeTests(unittest.TestCase):
    def test_process_reads_prompt_via_stdin(self):
        rc, out, _ = loop.command([sys.executable, '-c', 'import sys; print(sys.stdin.read())'], Path.cwd(), stdin='task content')
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), 'task content')

    def test_normal_exit_does_not_signal_reaped_pgid(self):
        with patch.object(loop.os, 'killpg') as kill:
            self.assertEqual(loop.command([sys.executable, '-c', 'pass'], Path.cwd())[0], 0)
            kill.assert_not_called()

    def test_timeout_stops(self):
        with self.assertRaises(loop.Stop):
            loop.command([sys.executable, '-c', 'import time; time.sleep(5)'], Path.cwd(), timeout=0.1)

    def test_output_logs_do_not_spill_to_console(self):
        with tempfile.TemporaryDirectory() as runtime:
            stdout, stderr = Path(runtime) / 'stdout', Path(runtime) / 'stderr'
            rc, out, err = loop.command([sys.executable, '-c', 'import sys; print("out"); print("err",file=sys.stderr)'],
                                       Path.cwd(), stdout_path=stdout, stderr_path=stderr)
            self.assertEqual((rc, out, err), (0, '', ''))
            self.assertEqual(stdout.read_text().strip(), 'out')
            self.assertEqual(stderr.read_text().strip(), 'err')

    def test_ci_exact_head_success(self):
        rows = [{'headSha': HEAD, 'status': 'completed', 'conclusion': 'success', 'url': 'run-url'}]
        with patch.object(loop, 'run', return_value=json.dumps(rows)):
            self.assertEqual(loop.wait_ci(Path('.'), HEAD, 'llmtool-ci.yml'), rows)

    def test_ci_failure_never_passes(self):
        rows = [{'headSha': HEAD, 'status': 'completed', 'conclusion': 'failure'}]
        with patch.object(loop, 'run', return_value=json.dumps(rows)):
            with self.assertRaises(loop.Stop):
                loop.wait_ci(Path('.'), HEAD, 'llmtool-ci.yml')

    def test_ci_old_success_and_missing_checks_time_out(self):
        for rows in ([], [{'headSha': 'b'*40, 'status': 'completed', 'conclusion': 'success'}]):
            with patch.object(loop, 'run', return_value=json.dumps(rows)), \
                 patch.object(loop.time, 'monotonic', side_effect=[0, 1201]):
                with self.assertRaises(loop.Stop):
                    loop.wait_ci(Path('.'), HEAD, 'llmtool-ci.yml')


if __name__ == '__main__':
    unittest.main()
