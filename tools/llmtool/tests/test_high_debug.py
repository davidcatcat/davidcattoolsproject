"""Isolated contract checks for the one-shot high-debug workflow."""
import tempfile
import unittest
import subprocess
import json
import hashlib
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from io import StringIO

import review_trust_fixture
import test_task_store as task_store_fixtures

from llmtool import high_debug
from llmtool import cli
from llmtool import test_output_excerpts
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore

_REAL_POST_GITHUB_RECORD = high_debug._post_github_record
_RECOVERY_REF = 'https://github.com/org/repo/issues/1097#issuecomment-3'


def _keyword_lines(count):
    """``count`` lines, each one distinct keyword_assign screen hit."""
    # synthetic-fixture: screen-tool test
    line = 'pass' + 'word = "synthetic-' + 'fixture"\n'
    return line * count


def _fixture_value():
    return 'synthetic-' + 'fixture'


class HighDebugTests(unittest.TestCase):
    def setUp(self):
        self.unposted_root = tempfile.TemporaryDirectory()
        self.addCleanup(self.unposted_root.cleanup)
        self._poster_patch = patch.object(high_debug, '_post_github_record',
                                          return_value=True)
        self._poster = self._poster_patch.start()
        self.addCleanup(self._poster_patch.stop)
        self._store_root_patch = patch.object(
            high_debug.task_store, 'default_root',
            return_value=Path(self.unposted_root.name) / 'tasks')
        self._store_root_patch.start()
        self.addCleanup(self._store_root_patch.stop)

    @staticmethod
    def _report(outcome_lines, prose=''):
        parts = []
        for section in high_debug.REPORT_SECTIONS[:-1]:
            parts.append('## ' + section + '\n' + (prose if section == 'Root Cause' else 'text') + '\n')
        parts.append('## Outcome/cost\n' + outcome_lines + '\n')
        return '\n'.join(parts)

    def test_outcome_status_ignores_words_outside_outcome_section(self):
        report = self._report('RCA_CONFIRMED\nCost: low', prose='This was BLOCKED earlier.\nBLOCKED')
        self.assertEqual(high_debug._outcome_status(report), 'RCA_CONFIRMED')

    def test_outcome_status_accepts_bold_form_on_its_own_line(self):
        report = self._report('**RCA_CONFIRMED**\n\nModel: gpt-6.1-sol; usage UNKNOWN')
        self.assertEqual(high_debug._outcome_status(report), 'RCA_CONFIRMED')

    def test_outcome_status_accepts_status_with_supported_trailing_text(self):
        cases = (
            ('RCA_CONFIRMED', 'RCA_CONFIRMED'),
            ('**RCA_CONFIRMED**', 'RCA_CONFIRMED'),
            ('**RCA_CONFIRMED** — reason', 'RCA_CONFIRMED'),  # PR #939 regression
            ('RCA_UNCONFIRMED - reason', 'RCA_UNCONFIRMED'),
            ('BLOCKED: reason', 'BLOCKED'),
        )
        for line, expected in cases:
            with self.subTest(line=line):
                self.assertEqual(high_debug._outcome_status(self._report(line)), expected)

    def test_outcome_status_inside_prose_is_report_invalid(self):
        report = self._report('Reason: RCA_CONFIRMED')
        self.assertEqual(high_debug._outcome_status(report), 'REPORT_INVALID')

    def test_outcome_status_two_words_is_report_invalid(self):
        report = self._report('**RCA_CONFIRMED**\nBLOCKED')
        self.assertEqual(high_debug._outcome_status(report), 'REPORT_INVALID')

    def test_outcome_status_with_another_status_in_trailing_text_is_report_invalid(self):
        lines = (
            'RCA_CONFIRMED — RCA_UNCONFIRMED',
            'BLOCKED: RCA_CONFIRMED',
            'RCA_CONFIRMED - not BLOCKED',
        )
        for line in lines:
            with self.subTest(line=line):
                self.assertEqual(high_debug._outcome_status(self._report(line)),
                                 'REPORT_INVALID')

    def test_outcome_status_missing_section_is_report_invalid(self):
        report = '## Root Cause\nRCA_CONFIRMED\n'
        self.assertEqual(high_debug._outcome_status(report), 'REPORT_INVALID')

    def test_codex_invocation_enables_search_and_keeps_read_only_sandbox(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = str(Path(directory) / 'report.md')
            with patch.object(high_debug.subprocess, 'run') as run:
                high_debug._run_codex(prompt='diagnosis', cwd=directory,
                                      output_path=output_path)

        argv = run.call_args.args[0]
        self.assertEqual(argv[0:2], ['codex', 'exec'])
        self.assertIn('web_search="live"', argv)
        self.assertIn('-s', argv)
        self.assertEqual(argv[argv.index('-s') + 1], 'read-only')
        self.assertIn('model_reasoning_effort=high', argv)

    def test_codex_invocation_is_isolated_like_the_developer_route(self):
        secret_name = 'LLMTOOL_HIGH_DEBUG_TEST_SECRET'
        with tempfile.TemporaryDirectory() as directory:
            output_path = str(Path(directory) / 'report.md')
            with patch.dict(high_debug.os.environ, {secret_name: 'x'}), \
                 patch.object(high_debug.subprocess, 'run') as run:
                high_debug._run_codex(prompt='diagnosis', cwd=directory,
                                      output_path=output_path)
        argv = run.call_args.args[0]
        env = run.call_args.kwargs['env']
        self.assertIn('--ignore-user-config', argv)
        self.assertIn('--ignore-rules', argv)
        self.assertIn('shell_environment_policy.inherit="none"', argv)
        self.assertIn('allow_login_shell=false', argv)
        self.assertTrue(any(item.startswith('shell_environment_policy.set.PATH=')
                            for item in argv))
        # Rollouts must stay on: the observed model is read from them.
        self.assertNotIn('--ephemeral', argv)
        self.assertNotIn(secret_name, env)
        self.assertTrue(set(env) <= {
            'PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'LC_CTYPE',
            'CODEX_HOME', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'TMPDIR'})

    def test_long_executor_task_gets_a_valid_unique_d6_name(self):
        executor = 'e' * 70 + '-' + 'f' * 25
        first = high_debug._d6_name(executor, 1, 'a' * 40)
        second = high_debug._d6_name(executor, 2, 'a' * 40)
        other = high_debug._d6_name('e' * 70 + '-' + 'g' * 25, 1, 'a' * 40)
        for name in (first, second, other):
            self.assertRegex(name, r'\A[a-z0-9][a-z0-9-]{0,95}\Z')
        self.assertEqual(len({first, second, other}), 3)
        self.assertEqual(high_debug._d6_name('executor', 3, 'b' * 40),
                         'executor-hd3-bbbbbbb')

    def test_dispatch_refusal_has_stable_code(self):
        store = SimpleNamespace(get=lambda _task: {
            'task_id': 'a' * 32,
            'review_repair_claims': [
                {'family': 'family', 'pr': 17},
                {'family': 'family', 'pr': 17},
            ],
        })
        with patch.object(high_debug.worker_dispatch, 'evaluate',
                          return_value=SimpleNamespace(allowed=False)):
            with self.assertRaises(LLMToolError) as caught:
                high_debug.run_high_debug(store=store, task_id='a' * 32,
                    executor_task='executor', pr_number=17, family='family',
                    failed_corrections=2)
        self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_DISPATCH_REFUSED')

    def _repo(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        subprocess.run(['git', '-C', str(root), '-c', 'user.name=t', '-c', 'user.email=t@e',
                        'commit', '-q', '--allow-empty', '-m', 'base'], check=True)
        return root

    def _failing_case(self, store, root, *, returncode=0, report=None, create=None,
                      runner_error=None, dirty=False):
        registration = SimpleNamespace(result_sha='a' * 40, root=str(root),
                                       common_dir=str(root / '.git'))
        created = []
        def default_create(**kwargs):
            return str(root)
        def recording_create(**kwargs):
            created.append(kwargs['task_id'])
            return (create or default_create)(**kwargs)
        def runner(*, prompt, cwd, output_path):
            if runner_error is not None:
                raise runner_error
            Path(output_path).write_text(report or '', encoding='utf-8')
            if dirty:
                (Path(cwd) / 'unexpected.txt').write_text('changed', encoding='utf-8')
            return subprocess.CompletedProcess([], returncode, stdout=b'', stderr=b'')
        with patch.object(high_debug.worker_dispatch, 'evaluate',
                          return_value=SimpleNamespace(allowed=True)), \
             patch.object(high_debug.task_completion, 'collect_git_snapshot',
                          return_value=SimpleNamespace(head='a' * 40)):
            with self.assertRaises(LLMToolError) as caught:
                high_debug.run_high_debug(store=store, task_id='a' * 32,
                    executor_task='executor', pr_number=17, family='family',
                    failed_corrections=2, runner=runner, create_d6=recording_create,
                    repo_root=str(root),
                    read_registration=lambda *_args, **_kwargs: registration)
        self._last_failing_exception = caught.exception
        return caught.exception.high_debug_code, created

    def test_failed_codex_attempts_are_recorded_and_retry_uses_a_new_d6(self):
        store = _Store()
        root = self._repo()
        cases = (
            ({'returncode': 1}, 'HIGH_DEBUG_RUN_FAILED'),
            ({'runner_error': subprocess.TimeoutExpired('codex', 1800)},
             'HIGH_DEBUG_RUN_FAILED'),
            ({'dirty': True}, 'HIGH_DEBUG_WROTE_FILES'),
        )
        for attempt, (kwargs, code) in enumerate(cases, 1):
            with self.subTest(code=code, attempt=attempt):
                observed, created = self._failing_case(store, root, **kwargs)
                self.assertEqual(observed, code)
                self.assertEqual(created, [f'executor-hd{attempt}-aaaaaaa'])
                entry = store.entries[-1]
                self.assertEqual((entry['status'], entry['report'], entry['d6']),
                                 ('RUN_FAILED', code, created[0]))
                (root / 'unexpected.txt').unlink(missing_ok=True)
        self.assertEqual(len(store.entries), len(cases))
        self.assertEqual(store.events, [])

    def _failed_report(self, report):
        store = _Store()
        caught, created = self._failing_case(store, self._repo(), report=report)
        self.assertEqual(caught, 'HIGH_DEBUG_REPORT_INVALID')
        self.assertEqual(len(created), 1)
        self.assertEqual(len(store.entries), 1)
        self.assertEqual(store.events, [])
        entry = store.entries[0]
        self.assertEqual(entry['status'], 'RUN_FAILED')
        self.assertEqual(entry['report_sha256'],
                         hashlib.sha256(entry['report'].encode('utf-8')).hexdigest())
        self.assertFalse(high_debug._emitted_sensitive(entry['report']))
        return entry

    def test_oversized_clean_report_is_truncated_at_utf8_boundary(self):
        report = 'head' + '🙂' * 6000
        entry = self._failed_report(report)
        self.assertIn('report truncated', entry['report'])
        self.assertLessEqual(len(entry['report'].encode('utf-8')), high_debug._REPORT_LIMIT)
        stored_head = entry['report'].split('report truncated\n', 1)[1]
        self.assertTrue(report.startswith(stored_head))
        self.assertNotIn('�', stored_head)

    def test_sensitive_report_within_limit_stores_fixed_lines_only(self):
        # synthetic-fixture: screen-tool test
        report = 'safe\nBear' + 'er ' + 'a' * 40
        entry = self._failed_report(report)
        self.assertEqual(entry['report'],
                         'HIGH_DEBUG_REPORT_INVALID\ncheck: SENSITIVE\nbytes: ' +
                         str(len(report.encode('utf-8'))) +
                         '\nlimit: 16384\nhit: auth_scheme line=2')

    def test_oversized_report_sensitive_beyond_kept_head_stores_fixed_lines(self):
        # synthetic-fixture: screen-tool test
        report = 'safe head' + 'x' * 17000 + '\nBear' + 'er ' + 'b' * 40
        entry = self._failed_report(report)
        self.assertEqual(entry['report'],
                         'HIGH_DEBUG_REPORT_INVALID\ncheck: SIZE,SENSITIVE\nbytes: ' +
                         str(len(report.encode('utf-8'))) +
                         '\nlimit: 16384\nhit: auth_scheme line=2')

    def test_cut_that_creates_sensitive_match_falls_back_to_fixed_lines(self):
        report = 'x' * 17000
        store = _Store()
        with patch.object(high_debug, '_emitted_sensitive',
                          side_effect=(False, True, False)) as scan:
            self._failing_case(store, self._repo(), report=report)
        stored = store.entries[0]['report']
        self.assertEqual(stored,
                         'HIGH_DEBUG_REPORT_INVALID\ncheck: SIZE\nbytes: 17000\nlimit: 16384')
        self.assertEqual(scan.call_count, 3)
        self._poster.assert_called_once()

    def test_kept_truncated_report_with_sections_and_outcome_stays_run_failed(self):
        complete = ''.join('## ' + heading + '\nDetails.\n'
                           for heading in high_debug.REPORT_SECTIONS)
        complete += 'RCA_CONFIRMED\n'
        report = complete + ('x' * (high_debug._REPORT_LIMIT + 1000))
        entry = self._failed_report(report)
        self.assertEqual(entry['status'], 'RUN_FAILED')
        self.assertIn('RCA_CONFIRMED', entry['report'])
        self.assertIn('report truncated', entry['report'])

    def test_oversized_report_refusal_carries_safe_detail(self):
        report = 'x' * 20000
        entry = self._failed_report(report)
        self.assertEqual(entry['report'].split('\nreport truncated\n', 1)[0],
                         'HIGH_DEBUG_REPORT_INVALID\ncheck: SIZE\nbytes: 20000\nlimit: 16384')
        self.assertTrue(entry['report'].endswith('x' * 100))
        self._failing_case(_Store(), self._repo(), report=report)
        self.assertEqual(self._last_failing_exception.high_debug_detail,
                         'check=SIZE bytes=20000 limit=16384')

    def test_success_posts_one_metadata_only_monitor_comment(self):
        report = ''.join('## ' + heading + '\nDetails.\n'
                         for heading in high_debug.REPORT_SECTIONS)
        report += 'RCA_CONFIRMED\nprivate report phrase'
        self._run_case(report)
        self._poster.assert_called_once()
        args, kwargs = self._poster.call_args
        body = args[1]
        self.assertEqual(body.splitlines()[0],
            '<!-- activity: type=developer_progress actor=Codex target=PM status=WAITING -->')
        for field in ('Task: ' + 'a' * 32, 'D6: executor-hd1-aaaaaaa',
                      'Family: family', 'Examined SHA: ' + 'a' * 40,
                      'Requested model: gpt-6.1-sol', 'Observed model: UNKNOWN',
                      'Effort: UNKNOWN', 'Status: RCA_CONFIRMED', 'Report SHA-256:',
                      'Usage: UNKNOWN'):
            self.assertIn(field, body)
        self.assertNotIn('private report phrase', body)
        self.assertNotIn('Details.', body)

    def test_failed_attempt_variants_post_once_with_blocked_marker(self):
        cases = (
            ({'returncode': 1}, 'HIGH_DEBUG_RUN_FAILED'),
            ({'runner_error': subprocess.TimeoutExpired('codex', 1800)},
             'HIGH_DEBUG_RUN_FAILED'),
            ({'dirty': True}, 'HIGH_DEBUG_WROTE_FILES'),
            ({'report': 'private report ' + 'x' * 20000},
             'HIGH_DEBUG_REPORT_INVALID'),
            # synthetic-fixture: screen-tool test
            ({'report': 'safe head ' + 'x' * 17000 + '\nBear' + 'er ' + 'c' * 40},
             'HIGH_DEBUG_REPORT_INVALID'),
        )
        for kwargs, code in cases:
            with self.subTest(code=code):
                self._poster.reset_mock()
                self._failing_case(_Store(), self._repo(), **kwargs)
                self._poster.assert_called_once()
                body = self._poster.call_args.args[1]
                self.assertEqual(body.splitlines()[0],
                    '<!-- activity: type=developer_progress actor=Codex target=PM status=BLOCKED -->')
                self.assertIn('Refusal code: ' + code, body)
                self.assertNotIn('private report', body)
                self.assertNotIn('safe head', body)

    def test_unsafe_observed_model_becomes_unknown_in_comment(self):
        entry = {'d6': 'run-1', 'family': 'family', 'examined_sha': 'a' * 40,
                 'model_requested': 'gpt-6.1-sol', 'model_observed': 'unsafe model',
                 'effort_observed': 'high', 'status': 'RCA_UNCONFIRMED',
                 'report_sha256': 'b' * 64, 'usage': {'input': 1, 'cached': 0,
                 'output': 2, 'reasoning': 1}}
        body = high_debug._record_body(entry, 'a' * 32)
        self.assertIn('Observed model: UNKNOWN', body)
        self.assertIn('Effort: high', body)
        self.assertIn('Usage: input=1,cached=0,output=2,reasoning=1', body)

    def test_github_poster_uses_trusted_slug_argv_and_stdin(self):
        completed = subprocess.CompletedProcess([], 0, stdout=b'', stderr=b'')
        with patch.object(high_debug.shutil, 'which', return_value='/trusted/gh') as which, \
             patch.object(high_debug.task_completion, '_trusted_slug',
                          return_value='owner/repo'), \
             patch.object(high_debug.subprocess, 'run', return_value=completed) as run, \
             patch.dict(high_debug.os.environ, {'GH_CONFIG_DIR': '/safe/gh-config',
                                                'GH_TOKEN': 'ambient-secret'}):
            self.assertTrue(_REAL_POST_GITHUB_RECORD(17, 'monitor body', '/repo'))
        which.assert_called_once_with('gh', path=high_debug.task_completion._GH_PATH)
        args, kwargs = run.call_args
        self.assertEqual(args[0], ['/trusted/gh', 'pr', 'comment', '17', '--repo',
                                   'owner/repo', '--body-file', '-'])
        self.assertEqual(kwargs['input'], b'monitor body')
        self.assertEqual(kwargs['cwd'], '/repo')
        self.assertFalse(kwargs['shell'])
        self.assertEqual(kwargs['timeout'], 30)
        self.assertEqual(kwargs['env']['GH_CONFIG_DIR'], '/safe/gh-config')
        self.assertNotIn('GH_TOKEN', kwargs['env'])

    def test_github_poster_uses_issue_comment_only_when_pr_reports_not_a_pull_request(self):
        rejected = subprocess.CompletedProcess(
            [], 1, stdout=b'', stderr=b'Could not resolve to a PullRequest\n')
        posted = subprocess.CompletedProcess([], 0, stdout=b'', stderr=b'')
        with patch.object(high_debug.shutil, 'which', return_value='/trusted/gh'), \
             patch.object(high_debug.task_completion, '_trusted_slug',
                          return_value='owner/repo'), \
             patch.object(high_debug.subprocess, 'run',
                          side_effect=(rejected, posted)) as run:
            self.assertTrue(_REAL_POST_GITHUB_RECORD(17, 'monitor body', '/repo'))
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [['/trusted/gh', 'pr', 'comment', '17', '--repo', 'owner/repo',
              '--body-file', '-'],
             ['/trusted/gh', 'issue', 'comment', '17', '--repo', 'owner/repo',
              '--body-file', '-']])
        self.assertEqual(run.call_args_list[1].kwargs['input'], b'monitor body')

    def test_github_poster_does_not_retry_other_pr_comment_failures(self):
        failures = (b'', b'HTTP 502: Bad Gateway\n',
                    b'error connecting to api.github.com\n')
        for stderr in failures:
            with self.subTest(stderr=stderr):
                rejected = subprocess.CompletedProcess([], 1, stdout=b'', stderr=stderr)
                with patch.object(high_debug.shutil, 'which', return_value='/trusted/gh'), \
                     patch.object(high_debug.task_completion, '_trusted_slug',
                                  return_value='owner/repo'), \
                     patch.object(high_debug.subprocess, 'run',
                                  return_value=rejected) as run:
                    self.assertFalse(
                        _REAL_POST_GITHUB_RECORD(17, 'monitor body', '/repo'))
                self.assertEqual(run.call_count, 1)

    def test_ambiguous_failure_stays_unposted_without_second_write_or_stderr(self):
        rejected = subprocess.CompletedProcess(
            [], 1, stdout=b'', stderr=b'HTTP 502: Bad Gateway\n')
        entry = {'d6': 'run-1', 'status': 'RCA_CONFIRMED', 'report_sha256': 'b' * 64}
        with patch.object(high_debug, '_post_github_record', _REAL_POST_GITHUB_RECORD), \
             patch.object(high_debug.shutil, 'which', return_value='/trusted/gh'), \
             patch.object(high_debug.task_completion, '_trusted_slug',
                          return_value='owner/repo'), \
             patch.object(high_debug.subprocess, 'run',
                          return_value=rejected) as run:
            self.assertFalse(high_debug._post_record(entry, 17, '/repo', 'a' * 32))
        self.assertEqual(run.call_count, 1)
        path = Path(self.unposted_root.name) / 'high-debug-unposted' / 'run-1.json'
        saved = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(saved['reason'], 'POST_FAILED')
        self.assertIn('Status: RCA_CONFIRMED', saved['body'])
        self.assertNotIn('HTTP 502', path.read_text(encoding='utf-8'))

    def test_both_comment_failures_leave_unposted_record(self):
        rejected = subprocess.CompletedProcess(
            [], 1, stdout=b'', stderr=b'Could not resolve to a PullRequest\n')
        entry = {'d6': 'run-1', 'status': 'RCA_CONFIRMED', 'report_sha256': 'b' * 64}
        with patch.object(high_debug, '_post_github_record', _REAL_POST_GITHUB_RECORD), \
             patch.object(high_debug.shutil, 'which', return_value='/trusted/gh'), \
             patch.object(high_debug.task_completion, '_trusted_slug',
                          return_value='owner/repo'), \
             patch.object(high_debug.subprocess, 'run',
                          side_effect=(rejected, rejected)) as run:
            self.assertFalse(high_debug._post_record(entry, 17, '/repo', 'a' * 32))
        self.assertEqual(run.call_count, 2)
        path = Path(self.unposted_root.name) / 'high-debug-unposted' / 'run-1.json'
        saved = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(saved['reason'], 'POST_FAILED')
        self.assertIn('Status: RCA_CONFIRMED', saved['body'])

    def test_github_poster_without_trusted_slug_does_not_run_subprocess(self):
        with patch.object(high_debug.shutil, 'which', return_value='/trusted/gh'), \
             patch.object(high_debug.task_completion, '_trusted_slug', return_value=None), \
             patch.object(high_debug.subprocess, 'run') as run:
            self.assertFalse(_REAL_POST_GITHUB_RECORD(17, 'monitor body', '/repo'))
        run.assert_not_called()

    def test_unposted_failure_preserves_run_and_writes_private_retry_record(self):
        store = _Store()
        self._poster.return_value = False
        caught, _created = self._failing_case(store, self._repo(), returncode=1)
        self.assertEqual(caught, 'HIGH_DEBUG_RUN_FAILED')
        self.assertEqual(len(store.entries), 1)
        self.assertEqual(store.events, [])
        self.assertEqual(self._last_failing_exception.high_debug_detail,
                         'github_record=UNPOSTED')
        record_path = Path(self.unposted_root.name) / 'high-debug-unposted' / \
            (store.entries[0]['d6'] + '.json')
        saved = json.loads(record_path.read_text(encoding='utf-8'))
        self.assertEqual(saved['reason'], 'POST_FAILED')
        self.assertEqual(saved['task_id'], 'a' * 32)
        self.assertIn('Refusal code: HIGH_DEBUG_RUN_FAILED', saved['body'])
        self.assertEqual(record_path.stat().st_mode & 0o777, 0o600)

    def test_success_result_and_event_survive_false_or_raising_poster(self):
        report = ''.join('## ' + heading + '\nDetails.\n'
                         for heading in high_debug.REPORT_SECTIONS)
        report += 'RCA_UNCONFIRMED'
        for poster_result in (False, RuntimeError('post failed')):
            with self.subTest(poster_result=type(poster_result).__name__):
                self._poster.reset_mock()
                if isinstance(poster_result, Exception):
                    self._poster.side_effect = poster_result
                else:
                    self._poster.side_effect = None
                    self._poster.return_value = poster_result
                output = StringIO()
                with redirect_stdout(output):
                    _result, _invoke, store = self._run_case(report)
                self.assertEqual(len(store.entries), 1)
                self.assertEqual(store.entries[0]['status'], 'RCA_UNCONFIRMED')
                self.assertEqual(len(store.events), 1)
                record_path = Path(self.unposted_root.name) / 'high-debug-unposted' / \
                    (store.entries[0]['d6'] + '.json')
                saved = json.loads(record_path.read_text(encoding='utf-8'))
                self.assertEqual(saved['reason'], 'POST_FAILED')
                self.assertEqual(record_path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(json.loads(output.getvalue())['github_record'], 'UNPOSTED')
        self._poster.side_effect = None

    def test_screen_rejection_stores_no_body_and_skips_poster(self):
        entry = {'d6': 'run-1', 'family': 'family', 'examined_sha': 'a' * 40,
                 'model_requested': 'gpt-6.1-sol', 'model_observed': 'UNKNOWN',
                 'effort_observed': 'UNKNOWN', 'status': 'RUN_FAILED',
                 'report_sha256': 'b' * 64, 'usage': 'UNKNOWN'}
        with patch.object(high_debug, '_emitted_sensitive', return_value=True):
            self.assertFalse(high_debug._post_record(entry, 17, '/repo', 'a' * 32,
                                                     'HIGH_DEBUG_RUN_FAILED'))
        self._poster.assert_not_called()
        path = Path(self.unposted_root.name) / 'high-debug-unposted' / 'run-1.json'
        saved = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(saved['reason'], 'SCREEN_REJECTED')
        self.assertNotIn('body', saved)

    def test_unposted_write_failure_is_swallowed(self):
        self._poster.return_value = False
        with patch.object(high_debug, '_write_unposted', side_effect=OSError('no space')):
            self.assertFalse(high_debug._post_record(
                {'d6': 'run-1', 'status': 'RCA_CONFIRMED', 'report_sha256': 'b' * 64},
                17, '/repo', 'a' * 32))

    def test_pre_attempt_refusal_does_not_post(self):
        store = SimpleNamespace(get=lambda _task: {'task_id': 'a' * 32})
        with self.assertRaises(LLMToolError):
            high_debug.run_high_debug(store=store, task_id='a' * 32,
                executor_task='executor', pr_number=1, family='family',
                failed_corrections=1)
        self._poster.assert_not_called()

    def test_oversized_evidence_refusal_has_size_detail(self):
        store = _Store()
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / 'evidence.txt'
            evidence.write_bytes(b'x' * 8193)
            with patch.object(high_debug.worker_dispatch, 'evaluate',
                              return_value=SimpleNamespace(allowed=True)):
                with self.assertRaises(LLMToolError) as caught:
                    high_debug.run_high_debug(store=store, task_id='a' * 32,
                        executor_task='executor', pr_number=17, family='family',
                        failed_corrections=2, evidence=str(evidence))
        self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_EVIDENCE_INVALID')
        self.assertEqual(caught.exception.high_debug_detail, 'bytes=8193 limit=8192')

    def _cli_refusal_line(self, action, detail):
        error = LLMToolError('safe refusal')
        error.high_debug_code = 'HIGH_DEBUG_REPORT_INVALID'
        error.review_repair_code = 'REVIEW_REPAIR_REFUSED'
        if detail is not None:
            error.high_debug_detail = detail
        args = SimpleNamespace(command='task', action=action)
        output = StringIO()
        with patch.object(cli, 'parser_for_cli',
                          return_value=SimpleNamespace(parse_args=lambda _argv: args)), \
             patch('llmtool.source_freshness.require_fresh_source'), \
             patch.object(cli.task_submission_cli, 'run_task_high_debug', side_effect=error), \
             patch.object(cli.task_submission_cli, 'run_task_repair', side_effect=error), \
             redirect_stderr(output):
            self.assertEqual(cli.main([]), 1)
        return output.getvalue()

    def test_cli_prints_allowlisted_high_debug_detail(self):
        self.assertEqual(self._cli_refusal_line('high-debug', 'check=SIZE bytes=20000 limit=16384'),
                         'llmtool: high-debug refused: HIGH_DEBUG_REPORT_INVALID '
                         '(check=SIZE bytes=20000 limit=16384)\n')

    def test_cli_omits_high_debug_detail_that_fails_allowlist(self):
        self.assertEqual(self._cli_refusal_line('high-debug', 'check=SIZE\nunsafe'),
                         'llmtool: high-debug refused: HIGH_DEBUG_REPORT_INVALID\n')
        self.assertEqual(self._cli_refusal_line('repair', 'check=SIZE bytes=20000'),
                         'llmtool: repair refused: REVIEW_REPAIR_REFUSED\n')

    def test_failed_create_costs_no_entry_and_retry_skips_a_leftover_d6(self):
        store = _Store()
        root = self._repo()
        def leaves_branch(**kwargs):
            subprocess.run(['git', '-C', str(root), 'branch',
                            'llmtool/high-debug-' + kwargs['task_id']], check=True)
            raise subprocess.TimeoutExpired('lifecycle', 180)
        def leaves_record(**kwargs):
            registry = root / '.git' / 'llmtool-worktrees'
            registry.mkdir(exist_ok=True)
            (registry / (kwargs['task_id'] + '.json')).write_text('{}', encoding='utf-8')
            raise subprocess.CalledProcessError(1, 'lifecycle')
        def leaves_nothing(**_kwargs):
            raise subprocess.CalledProcessError(1, 'lifecycle')
        expected = (
            (leaves_branch, 'executor-hd1-aaaaaaa'),
            (leaves_record, 'executor-hd2-aaaaaaa'),
            (leaves_nothing, 'executor-hd3-aaaaaaa'),
            (leaves_nothing, 'executor-hd3-aaaaaaa'),
        )
        for create, name in expected:
            with self.subTest(name=name):
                observed, created = self._failing_case(store, root, create=create)
                self.assertEqual(observed, 'HIGH_DEBUG_D6_CREATE_FAILED')
                self.assertEqual(created, [name])
        self.assertEqual(store.entries, [])

    def test_failed_attempt_keeps_its_code_when_recording_fails(self):
        store = _Store()
        def refuse(_task_id, _entry):
            raise LLMToolError('limit')
        store.append_high_debug = refuse
        observed, _created = self._failing_case(store, self._repo(), returncode=1)
        self.assertEqual(observed, 'HIGH_DEBUG_RUN_FAILED')

    def _run_case(self, report, *, dirty=False, snapshot_head=None, prompt_capture=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        store = _Store()
        registration = SimpleNamespace(result_sha='a' * 40, root=str(root),
                                       common_dir=str(root / '.git'))
        def runner(*, prompt, cwd, output_path):
            if prompt_capture is not None:
                prompt_capture.append(prompt)
            Path(output_path).write_text(report, encoding='utf-8')
            if dirty:
                (Path(cwd) / 'unexpected.txt').write_text('changed', encoding='utf-8')
            return subprocess.CompletedProcess([], 0, stdout=b'{"model":"gpt-6.1-sol"}\n', stderr=b'')
        def invoke():
            with patch.object(high_debug.worker_dispatch, 'evaluate',
                              return_value=SimpleNamespace(allowed=True)), \
                 patch.object(high_debug.task_completion, 'collect_git_snapshot',
                              return_value=SimpleNamespace(head=snapshot_head or 'a' * 40)):
                return high_debug.run_high_debug(store=store, task_id='a' * 32,
                    executor_task='executor', pr_number=17, family='family',
                    failed_corrections=2, runner=runner,
                    create_d6=lambda **_kwargs: str(root),
                    read_registration=lambda *_args, **_kwargs: registration)
        with patch.object(high_debug.worker_dispatch, 'evaluate',
                          return_value=SimpleNamespace(allowed=True)), \
             patch.object(high_debug.task_completion, 'read_d6_registration',
                          return_value=registration):
            return invoke(), invoke, store

    def test_happy_path_records_report_and_replay_event_once(self):
        report = ''.join('## ' + heading + '\nDetails.\n' for heading in high_debug.REPORT_SECTIONS)
        report += 'RCA_CONFIRMED'
        (result, invoke, store) = self._run_case(report)
        self.assertEqual(len(store.entries), 1)
        self.assertEqual(store.entries[0]['status'], 'RCA_CONFIRMED')
        self.assertEqual(len(store.events), 1)
        self.assertEqual(invoke(), None)
        self.assertEqual(len(store.events), 1)

    def test_prompt_requires_outcome_status_at_section_start(self):
        prompts = []
        report = ''.join('## ' + heading + '\nDetails.\n'
                         for heading in high_debug.REPORT_SECTIONS)
        report += 'RCA_CONFIRMED'
        self._run_case(report, prompt_capture=prompts)
        self.assertEqual(len(prompts), 1)
        self.assertIn(
            'The Outcome/cost section must start with exactly one status word alone on its own line:',
            prompts[0])
        self.assertIn('RCA_CONFIRMED, RCA_UNCONFIRMED or BLOCKED.', prompts[0])

    def test_missing_report_section_is_stored_as_invalid(self):
        (result, _invoke, store) = self._run_case('RCA_UNCONFIRMED')
        self.assertEqual(store.entries[0]['status'], 'REPORT_INVALID')

    def test_heading_report_sections_are_accepted(self):
        report = ''.join('## ' + heading + '\nDetails.\n' for heading in high_debug.REPORT_SECTIONS)
        report += 'RCA_CONFIRMED'
        (result, _invoke, store) = self._run_case(report)
        self.assertEqual(store.entries[0]['status'], 'RCA_CONFIRMED')

    def test_bold_report_sections_are_accepted(self):
        report = ''.join('**' + heading + '**\nDetails.\n' for heading in high_debug.REPORT_SECTIONS)
        report += 'RCA_CONFIRMED'
        (result, _invoke, store) = self._run_case(report)
        self.assertEqual(store.entries[0]['status'], 'RCA_CONFIRMED')

    def test_report_section_only_in_prose_is_invalid(self):
        report = ''.join('## ' + heading + '\nDetails.\n' for heading in high_debug.REPORT_SECTIONS if heading != 'Root Cause')
        report += 'The Root Cause is described in prose.\nRCA_UNCONFIRMED'
        (result, _invoke, store) = self._run_case(report)
        self.assertEqual(store.entries[0]['status'], 'REPORT_INVALID')

    def _sections(self, outcome_body):
        report = ''.join('## ' + heading + '\nDetails.\n'
                         for heading in high_debug.REPORT_SECTIONS[:-1])
        return report + '## Outcome/cost\n' + outcome_body

    def _assert_screen_detail_count(self, detail, total_hits):
        _rules, remainder = detail.split(' lines=', 1)
        lines, more = remainder.split(' more=', 1)
        displayed = len(lines.split(','))
        self.assertEqual(int(more), total_hits - displayed)
        self.assertLessEqual(len(detail), 128)

    def test_blocked_in_prose_does_not_count(self):
        report = self._sections('RCA_CONFIRMED\n').replace(
            'Details.', 'This was BLOCKED earlier.', 1)
        (result, _invoke, store) = self._run_case(report)
        self.assertEqual(store.entries[0]['status'], 'RCA_CONFIRMED')

    def test_two_status_words_in_outcome_are_invalid(self):
        (result, _invoke, store) = self._run_case(
            self._sections('RCA_CONFIRMED\nBLOCKED\n'))
        self.assertEqual(store.entries[0]['status'], 'REPORT_INVALID')

    def test_missing_outcome_section_is_invalid(self):
        report = ''.join('## ' + heading + '\nDetails.\n'
                         for heading in high_debug.REPORT_SECTIONS[:-1])
        report += 'RCA_CONFIRMED\n'
        (result, _invoke, store) = self._run_case(report)
        self.assertEqual(store.entries[0]['status'], 'REPORT_INVALID')

    def test_dirty_diagnosis_worktree_is_refused(self):
        with self.assertRaises(LLMToolError) as caught:
            self._run_case('RCA_UNCONFIRMED', dirty=True)
        self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_WROTE_FILES')

    def test_moved_d6_head_is_refused(self):
        with self.assertRaises(LLMToolError) as caught:
            self._run_case('RCA_UNCONFIRMED', snapshot_head='b' * 40)
        self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_HEAD_MISMATCH')

    def test_invalid_correction_count_is_refused_before_d6(self):
        store = SimpleNamespace(get=lambda _task: {'task_id': 'a' * 32})
        with patch.object(high_debug.worker_dispatch, 'evaluate',
                          return_value=SimpleNamespace(allowed=True)):
            with self.assertRaises(LLMToolError) as caught:
                high_debug.run_high_debug(store=store, task_id='a' * 32,
                    executor_task='executor', pr_number=1, family='family',
                    failed_corrections=1)
        self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_ARGUMENT_INVALID')

    def test_overstated_correction_count_uses_stored_family_claims(self):
        store = SimpleNamespace(get=lambda _task: {
            'task_id': 'a' * 32,
            'review_repair_claims': [{'family': 'family', 'pr': 17},
                                     {'family': 'family', 'pr': 17}],
        })
        with patch.object(high_debug.worker_dispatch, 'evaluate',
                          return_value=SimpleNamespace(allowed=True)):
            with self.assertRaises(LLMToolError) as caught:
                high_debug.run_high_debug(store=store, task_id='a' * 32,
                    executor_task='executor', pr_number=17, family='family',
                    failed_corrections=3)
        self.assertEqual(caught.exception.high_debug_code,
                         'HIGH_DEBUG_ARGUMENT_INVALID')

    def test_neither_and_both_reason_forms_are_refused(self):
        store = SimpleNamespace(get=lambda _task: {'task_id': 'a' * 32})
        with patch.object(high_debug.worker_dispatch, 'evaluate',
                          return_value=SimpleNamespace(allowed=True)):
            for kwargs in ({}, {'failed_corrections': 2, 'early_reason': 'early'}):
                with self.subTest(kwargs=kwargs):
                    with self.assertRaises(LLMToolError) as caught:
                        high_debug.run_high_debug(store=store, task_id='a' * 32,
                            executor_task='executor', pr_number=1, family='family', **kwargs)
                    self.assertEqual(caught.exception.high_debug_code,
                                     'HIGH_DEBUG_ARGUMENT_INVALID')

    def test_sensitive_evidence_is_refused(self):
        store = SimpleNamespace(get=lambda _task: {
            'task_id': 'a' * 32,
            'review_repair_claims': [
                {'family': 'family', 'pr': 17},
                {'family': 'family', 'pr': 17},
            ],
        })
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / 'evidence.txt'
            # synthetic-fixture: screen-tool test
            evidence.write_text('Bear' + 'er ' + 'a' * 40, encoding='utf-8')
            with patch.object(high_debug.worker_dispatch, 'evaluate',
                              return_value=SimpleNamespace(allowed=True)):
                with self.assertRaises(LLMToolError) as caught:
                    high_debug.run_high_debug(store=store, task_id='a' * 32,
                        executor_task='executor', pr_number=1, family='family',
                        failed_corrections=2, evidence=str(evidence))
            self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_EVIDENCE_SENSITIVE')

    def test_screen_hits_are_sorted_unique_and_line_aware(self):
        with patch.object(high_debug, '_SENSITIVE', re.compile(r'FLAG')):
            self.assertEqual(high_debug._screen_hits('ok\nFLAG\nFLAG'),
                             [('unclassified', 2), ('unclassified', 3)])
        with patch.object(high_debug, '_SENSITIVE', re.compile(r'FLAG.*END', re.S)):
            self.assertEqual(high_debug._screen_hits('FLAG\nEND'),
                             [('unclassified', 1)])

    def test_screen_rules_match_the_output_excerpt_sensitive_prefix(self):
        self.assertEqual('|'.join(pattern.pattern for _name, pattern
                                  in high_debug._SCREEN_RULES),
                         test_output_excerpts._SENSITIVE_PREFIX)
        self.assertTrue(all(pattern.flags & re.IGNORECASE
                            for _name, pattern in high_debug._SCREEN_RULES))

    def _real_task_store_task(self):
        review_trust_fixture.install_for_case(self)
        return task_store_fixtures.TaskStoreTests._codex_dispatch_task(self)

    def test_sensitive_rejection_report_is_bounded_and_real_store_validates(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory).resolve() / 'tasks')
            task = self._real_task_store_task()
            store.create(task)
            hits = [('rule', line) for line in range(1, 601)]
            report = high_debug._report_rejection(
                'HIGH_DEBUG_REPORT_INVALID', 'SENSITIVE', 600, 'benign text', hits)
            entry = high_debug._failed_entry(
                family='test-family', failed_corrections=2, early_reason=None,
                head='a' * 40, d6='executor-hd1-aaaaaaa',
                code='HIGH_DEBUG_REPORT_INVALID', report=report,
                observed='UNKNOWN', effort='UNKNOWN', usage='UNKNOWN',
                recorded_at='2026-10-27T00:00:00Z')
            saved = store.append_high_debug(task['task_id'], entry)
            stored = store.get(task['task_id'])['high_debug']

            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0]['status'], 'RUN_FAILED')
            self.assertEqual(saved['high_debug'], stored)
            self.assertEqual(sum(line.startswith('hit: ')
                                 for line in report.splitlines()), 32)
            self.assertTrue(report.endswith('omitted hits: 568'))
            self.assertEqual(stored[0]['report_sha256'],
                             hashlib.sha256(report.encode('utf-8')).hexdigest())
            self.assertLessEqual(len(report.encode('utf-8')), 16384)
            self.assertFalse(test_output_excerpts._emitted_sensitive(stored[0]['report']))

    def test_real_store_rejects_clean_oversized_high_debug_report(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory).resolve() / 'tasks')
            task = self._real_task_store_task()
            store.create(task)
            prior = high_debug._failed_entry(
                family='test-family', failed_corrections=2, early_reason=None,
                head='a' * 40, d6='executor-hd1-aaaaaaa',
                code='HIGH_DEBUG_RUN_FAILED', observed='UNKNOWN',
                effort='UNKNOWN', usage='UNKNOWN',
                recorded_at='2026-10-27T00:00:00Z')
            store.append_high_debug(task['task_id'], prior)
            oversized = high_debug._failed_entry(
                family='test-family', failed_corrections=2, early_reason=None,
                head='a' * 40, d6='executor-hd2-aaaaaaa',
                code='HIGH_DEBUG_REPORT_INVALID', report='x' * 16385,
                observed='UNKNOWN', effort='UNKNOWN', usage='UNKNOWN',
                recorded_at='2026-10-27T00:00:00Z')

            with self.assertRaises(LLMToolError) as caught:
                store.append_high_debug(task['task_id'], oversized)
            self.assertEqual(caught.exception.task_store_error_code,
                             'HIGH_DEBUG_INVALID')
            self.assertEqual(store.get(task['task_id'])['high_debug'], [prior])

    def test_screen_rule_first_match_and_unclassified(self):
        table = tuple((name, re.compile(pattern)) for name, pattern in
                      (('alpha', 'shared|alpha'), ('beta', 'shared|beta'), ('gamma', 'gamma')))
        with patch.object(high_debug, '_SCREEN_RULES', table):
            self.assertEqual(high_debug._screen_rule('shared gamma'), 'alpha')
            self.assertEqual(high_debug._screen_rule('beta'), 'beta')
            self.assertEqual(high_debug._screen_rule('gamma'), 'gamma')
            self.assertEqual(high_debug._screen_rule('plain'), 'unclassified')
        with patch.object(high_debug, '_SENSITIVE', re.compile(r'eyJplain')):
            self.assertEqual(high_debug._screen_hits('eyJplain'), [('unclassified', 1)])
        real_names = [name for name, _pattern in high_debug._SCREEN_RULES]
        self.assertEqual(len(real_names), len(set(real_names)))
        self.assertEqual(set(real_names), {'private_key', 'cloud_key', 'provider_key',
                                           'git_host_key', 'chat_key', 'auth_scheme',
                                           'keyword_assign'})
        high_debug._write_screen_record('a' * 32, 'family',
            'HIGH_DEBUG_EVIDENCE_SENSITIVE', 'EVIDENCE', [('unclassified', 1)], 4)
        high_debug.record_screen_disposition('a' * 32, 'family', [('unclassified', 1)],
            'FALSE_POSITIVE', 'https://github.com/org/repo/issues/1#issuecomment-2')

    def test_screen_sensitive_line_checks_scan_escaped_unicode_form(self):
        with patch.object(high_debug, '_SENSITIVE', re.compile(r'never-match')), \
             patch.object(high_debug, '_sensitive_jwt',
                          side_effect=lambda value: r'\xe9' in value), \
             patch.object(high_debug, '_sensitive_url',
                          side_effect=lambda value: r'\xe9' in value):
            self.assertEqual(high_debug._screen_hits('é'),
                             [('jwt', 1), ('url_userinfo', 1)])

    def test_screen_rule_requires_pattern_to_begin_at_match_text(self):
        table = (('later', re.compile('beta')),
                 ('starts_here', re.compile('alpha|beta')))
        with patch.object(high_debug, '_SCREEN_RULES', table):
            self.assertEqual(high_debug._screen_rule('alpha beta'), 'starts_here')

    def test_screen_record_counts_omitted_hits_and_refuses_disposition(self):
        task_id, family = 'a' * 32, 'family'
        hits = [('unclassified', number) for number in range(1, 36)]
        with patch.object(high_debug, '_screen_hits', return_value=hits):
            located = high_debug._screen_hits('benign')
        high_debug._write_screen_record(task_id, family, 'HIGH_DEBUG_EVIDENCE_SENSITIVE',
                                        'EVIDENCE', located, 35)
        record = json.loads(high_debug._screen_path(task_id, family).read_text())
        self.assertEqual(record['omitted'], 3)
        self.assertEqual(record['hits'], [list(hit) for hit in hits[:32]])
        detail = high_debug._screen_detail(hits[:32], omitted=record['omitted'])
        self._assert_screen_detail_count(
            detail, record['omitted'] + len(record['hits']))
        record_path = high_debug._screen_path(task_id, family)
        for invalid_omitted in ('3', -1):
            invalid_record = dict(record, omitted=invalid_omitted)
            record_path.write_text(json.dumps(invalid_record), encoding='utf-8')
            with self.assertRaises(LLMToolError) as caught:
                high_debug.record_screen_disposition(task_id, family, hits[:32],
                    'FALSE_POSITIVE', 'https://github.com/org/repo/issues/1#issuecomment-2')
            self.assertEqual(caught.exception.high_debug_code,
                             'HIGH_DEBUG_SCREEN_RECORD_INVALID')
        record_path.write_text(json.dumps(record), encoding='utf-8')
        before = record_path.read_bytes()
        with self.assertRaises(LLMToolError) as caught:
            high_debug.record_screen_disposition(task_id, family, hits[:32], 'FALSE_POSITIVE',
                'https://github.com/org/repo/issues/1#issuecomment-2')
        self.assertEqual(caught.exception.high_debug_code,
                         'HIGH_DEBUG_SCREEN_HITS_OMITTED')
        self.assertEqual(record_path.read_bytes(), before)
        self.assertIsNone(json.loads(before)['disposition'])

    def _write_screen_hits(self, source, count):
        """Record ``count`` unique hits for ``source``; return them and the path."""
        task_id, family = 'a' * 32, 'family'
        hits = [('unclassified', number) for number in range(1, count + 1)]
        high_debug._write_screen_record(task_id, family,
                                        high_debug._SCREEN_SOURCE_CODES[source],
                                        source, hits, count)
        return hits, high_debug._screen_path(task_id, family)

    def test_exactly_limit_unique_hits_accept_a_complete_disposition(self):
        limit = high_debug._SCREEN_HIT_LIMIT
        ref = 'https://github.com/org/repo/issues/1#issuecomment-2'
        report = ''.join('## ' + heading + '\nDetails.\n'
                         for heading in high_debug.REPORT_SECTIONS) + 'RCA_CONFIRMED'
        for source in high_debug._SCREEN_SOURCE_CODES:
            with self.subTest(source=source):
                hits, path = self._write_screen_hits(source, limit)
                record = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(len(record['hits']), limit)
                self.assertEqual(record['omitted'], 0)
                high_debug.record_screen_disposition('a' * 32, 'family', hits,
                                                     'FALSE_POSITIVE', ref)
                record = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(record['disposition']['ref'], ref)
                _result, _invoke, store = self._run_case(report)
                self.assertEqual(store.entries[0]['status'], 'RCA_CONFIRMED')

    def test_repeated_hits_do_not_count_toward_the_limit(self):
        limit = high_debug._SCREEN_HIT_LIMIT
        task_id, family = 'a' * 32, 'family'
        hits = [('unclassified', number) for number in range(1, limit + 1)]
        high_debug._write_screen_record(task_id, family, 'HIGH_DEBUG_EVIDENCE_SENSITIVE',
                                        'EVIDENCE', hits + hits[:1], limit)
        path = high_debug._screen_path(task_id, family)
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['omitted'], 0)
        high_debug.record_screen_disposition(task_id, family, hits, 'FALSE_POSITIVE',
            'https://github.com/org/repo/issues/1#issuecomment-2')
        self.assertIsNotNone(
            json.loads(path.read_text(encoding='utf-8'))['disposition'])

    def test_over_limit_hits_cannot_be_disposed_for_any_source(self):
        limit = high_debug._SCREEN_HIT_LIMIT
        for source in high_debug._SCREEN_SOURCE_CODES:
            hits, path = self._write_screen_hits(source, limit + 1)
            before = path.read_bytes()
            for supplied in (hits[:limit], hits, []):
                with self.subTest(source=source, supplied=len(supplied)):
                    with self.assertRaises(LLMToolError) as caught:
                        high_debug.record_screen_disposition('a' * 32, 'family', supplied,
                            'FALSE_POSITIVE',
                            'https://github.com/org/repo/issues/1#issuecomment-2')
                    self.assertEqual(caught.exception.high_debug_code,
                                     'HIGH_DEBUG_SCREEN_HITS_OMITTED')
                    self.assertEqual(path.read_bytes(), before)
                    self.assertEqual([item.name for item in path.parent.iterdir()],
                                     [path.name])
            self.assertIsNone(json.loads(before)['disposition'])

    def test_screen_disposition_module_entry_refuses_overflow_and_keeps_record(self):
        hits, path = self._write_screen_hits('EVIDENCE', high_debug._SCREEN_HIT_LIMIT + 1)
        before = path.read_bytes()
        argv = ['screen-disposition', '--task-id', 'a' * 32, '--family', 'family',
                '--classification', 'FALSE_POSITIVE',
                '--ref', 'https://github.com/org/repo/issues/1#issuecomment-2']
        for rule, line in hits:
            argv.extend(('--hit', rule + ':' + str(line)))
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(high_debug._screen_disposition_main(argv), 1)
        self.assertEqual(out.getvalue(), '')
        self.assertEqual(err.getvalue(), 'HIGH_DEBUG_SCREEN_HITS_OMITTED\n')
        self.assertEqual(path.read_bytes(), before)

    def test_legacy_disposed_overflow_record_keeps_high_debug_gate_closed(self):
        limit = high_debug._SCREEN_HIT_LIMIT
        ref = 'https://github.com/org/repo/issues/1#issuecomment-2'
        for source in high_debug._SCREEN_SOURCE_CODES:
            with self.subTest(source=source):
                hits, path = self._write_screen_hits(source, limit + 1)
                record = json.loads(path.read_text(encoding='utf-8'))
                record['disposition'] = {'classification': 'FALSE_POSITIVE', 'ref': ref,
                                         'recorded_at': record['recorded_at']}
                path.write_text(json.dumps(record), encoding='utf-8')
                before = path.read_bytes()
                create, runner = unittest.mock.Mock(), unittest.mock.Mock()
                with patch.object(high_debug.worker_dispatch, 'evaluate') as evaluate:
                    with self.assertRaises(LLMToolError) as caught:
                        high_debug.run_high_debug(store=_Store(), task_id='a' * 32,
                            executor_task='executor', pr_number=17, family='family',
                            failed_corrections=2, runner=runner, create_d6=create)
                self.assertEqual(caught.exception.high_debug_code,
                                 'HIGH_DEBUG_SCREEN_RECORD_INVALID')
                evaluate.assert_not_called()
                create.assert_not_called()
                runner.assert_not_called()
                with self.assertRaises(LLMToolError) as caught:
                    high_debug.record_screen_disposition('a' * 32, 'family', hits[:limit],
                                                         'FALSE_POSITIVE', ref)
                self.assertEqual(caught.exception.high_debug_code,
                                 'HIGH_DEBUG_SCREEN_RECORD_INVALID')
                self.assertEqual(path.read_bytes(), before)

    def test_pending_overflow_record_blocks_before_evidence_or_worker_start(self):
        missing = str(Path(self.unposted_root.name) / 'missing')
        for source in high_debug._SCREEN_SOURCE_CODES:
            with self.subTest(source=source):
                hits, path = self._write_screen_hits(
                    source, high_debug._SCREEN_HIT_LIMIT + 3)
                before = path.read_bytes()
                create, runner = unittest.mock.Mock(), unittest.mock.Mock()
                with patch.object(high_debug.worker_dispatch, 'evaluate') as evaluate:
                    with self.assertRaises(LLMToolError) as caught:
                        high_debug.run_high_debug(store=_Store(), task_id='a' * 32,
                            executor_task='executor', pr_number=17, family='family',
                            failed_corrections=2, evidence=missing,
                            runner=runner, create_d6=create)
                # A read of the missing evidence would refuse with another code.
                self.assertEqual(caught.exception.high_debug_code,
                                 'HIGH_DEBUG_SCREEN_DISPOSITION_REQUIRED')
                self._assert_screen_detail_count(caught.exception.high_debug_detail,
                                                 len(hits))
                evaluate.assert_not_called()
                create.assert_not_called()
                runner.assert_not_called()
                self.assertEqual(path.read_bytes(), before)

    def test_failed_attempts_preserve_the_screen_record(self):
        ref = 'https://github.com/org/repo/issues/1#issuecomment-2'
        hits, path = self._write_screen_hits('EVIDENCE', high_debug._SCREEN_HIT_LIMIT)
        before = path.read_bytes()
        with patch.object(high_debug.os, 'replace', side_effect=OSError('disk')):
            with self.assertRaises(OSError):
                high_debug.record_screen_disposition('a' * 32, 'family', hits,
                                                     'FALSE_POSITIVE', ref)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual([item.name for item in path.parent.iterdir()], [path.name])
        high_debug.record_screen_disposition('a' * 32, 'family', hits,
                                             'FALSE_POSITIVE', ref)
        disposed = path.read_bytes()
        code, _created = self._failing_case(_Store(), self._repo(), returncode=1)
        self.assertEqual(code, 'HIGH_DEBUG_RUN_FAILED')
        self.assertEqual(path.read_bytes(), disposed)

    def test_many_screen_hits_evidence_refusal_detail_counts_overflow_once(self):
        hits = [('unclassified', number) for number in range(1, 36)]
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / 'benign.txt'
            evidence.write_text('plain input', encoding='utf-8')
            with patch.object(high_debug, '_emitted_sensitive', return_value=True), \
                 patch.object(high_debug, '_screen_hits', return_value=hits):
                with self.assertRaises(LLMToolError) as caught:
                    high_debug.run_high_debug(store=_Store(), task_id='a' * 32,
                        executor_task='executor', pr_number=17, family='family',
                        failed_corrections=2, evidence=str(evidence))
        self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_EVIDENCE_SENSITIVE')
        self._assert_screen_detail_count(caught.exception.high_debug_detail, len(hits))

    def test_many_screen_hits_write_failure_detail_counts_overflow_once(self):
        hits = [('unclassified', number) for number in range(1, 36)]
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / 'benign.txt'
            evidence.write_text('plain input', encoding='utf-8')
            with patch.object(high_debug, '_emitted_sensitive', return_value=True), \
                 patch.object(high_debug, '_screen_hits', return_value=hits), \
                 patch.object(high_debug, '_write_screen_record', side_effect=OSError('disk')):
                with self.assertRaises(LLMToolError) as caught:
                    high_debug.run_high_debug(store=_Store(), task_id='a' * 32,
                        executor_task='executor', pr_number=17, family='family',
                        failed_corrections=2, evidence=str(evidence))
        self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_EVIDENCE_SENSITIVE')
        self.assertTrue(caught.exception.high_debug_detail.startswith('screen_record=UNRECORDED'))
        self._assert_screen_detail_count(caught.exception.high_debug_detail, len(hits))

    def test_many_screen_hits_report_refusal_detail_counts_overflow_once(self):
        report = ''.join('## ' + heading + '\nDetails.\n'
                         for heading in high_debug.REPORT_SECTIONS) + 'RCA_CONFIRMED'
        hits = [('unclassified', number) for number in range(1, 36)]
        with patch.object(high_debug, '_emitted_sensitive', return_value=True), \
             patch.object(high_debug, '_screen_hits', return_value=hits):
            with self.assertRaises(LLMToolError) as caught:
                self._run_case(report)
        self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_REPORT_INVALID')
        self._assert_screen_detail_count(caught.exception.high_debug_detail, len(hits))

    def test_many_persisted_screen_hits_rerun_detail_counts_overflow_once(self):
        task_id, family = 'a' * 32, 'family'
        hits = [('unclassified', number) for number in range(1, 36)]
        high_debug._write_screen_record(task_id, family, 'HIGH_DEBUG_EVIDENCE_SENSITIVE',
                                        'EVIDENCE', hits, 35)
        with self.assertRaises(LLMToolError) as caught:
            high_debug.run_high_debug(store=_Store(), task_id=task_id,
                executor_task='executor', pr_number=17, family=family,
                failed_corrections=2)
        self.assertEqual(caught.exception.high_debug_code,
                         'HIGH_DEBUG_SCREEN_DISPOSITION_REQUIRED')
        record = json.loads(high_debug._screen_path(task_id, family).read_text())
        self.assertEqual(record['omitted'], 3)
        self._assert_screen_detail_count(
            caught.exception.high_debug_detail, record['omitted'] + len(record['hits']))

    def test_pending_screen_record_blocks_before_missing_evidence_read(self):
        task_id, family = 'a' * 32, 'family'
        high_debug._write_screen_record(task_id, family, 'HIGH_DEBUG_EVIDENCE_SENSITIVE',
                                        'EVIDENCE', [('unclassified', 1)], 4)
        store = _Store()
        create, runner = unittest.mock.Mock(), unittest.mock.Mock()
        with self.assertRaises(LLMToolError) as caught:
            high_debug.run_high_debug(store=store, task_id=task_id,
                executor_task='executor', pr_number=17, family=family,
                failed_corrections=2, evidence=str(Path(self.unposted_root.name) / 'missing'),
                runner=runner, create_d6=create)
        self.assertEqual(caught.exception.high_debug_code,
                         'HIGH_DEBUG_SCREEN_DISPOSITION_REQUIRED')
        create.assert_not_called()
        runner.assert_not_called()

    def test_invalid_screen_record_directory_refuses_before_d6_or_runner(self):
        for kind in ('file', 'dangling_symlink', 'symlink_directory', 'lstat_error'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                task_id, family = 'a' * 32, 'family'
                store_root = Path(directory) / 'tasks'
                screen_dir = store_root.parent / 'high-debug-screen'
                record_path = screen_dir / (task_id + '-' + family + '.json')
                screen_dir.mkdir(parents=True)
                lstat_patch = None
                if kind == 'file':
                    screen_dir.rmdir()
                    screen_dir.write_text('directory replaced by file', encoding='utf-8')
                elif kind == 'dangling_symlink':
                    screen_dir.rmdir()
                    screen_dir.symlink_to(Path(directory) / 'missing-target')
                elif kind == 'symlink_directory':
                    target = Path(directory) / 'empty-target'
                    target.mkdir()
                    screen_dir.rmdir()
                    screen_dir.symlink_to(target, target_is_directory=True)
                elif kind == 'lstat_error':
                    lstat_patch = patch.object(Path, 'lstat', side_effect=PermissionError())
                create, runner = unittest.mock.Mock(), unittest.mock.Mock()
                with ExitStack() as stack:
                    stack.enter_context(patch.object(high_debug.task_store, 'default_root',
                                                     return_value=store_root))
                    if lstat_patch is not None:
                        stack.enter_context(lstat_patch)
                    with self.assertRaises(LLMToolError) as caught:
                        high_debug.run_high_debug(store=_Store(), task_id=task_id,
                            executor_task='executor', pr_number=17, family=family,
                            failed_corrections=2, runner=runner, create_d6=create)
                self.assertEqual(caught.exception.high_debug_code,
                                 'HIGH_DEBUG_SCREEN_RECORD_INVALID')
                create.assert_not_called()
                runner.assert_not_called()

    def test_screen_record_contains_no_refused_text_and_disposition_is_exact(self):
        task_id, family = 'a' * 32, 'family'
        high_debug._write_screen_record(task_id, family, 'HIGH_DEBUG_EVIDENCE_SENSITIVE',
                                        'EVIDENCE', [('keyword_assign', 2)], 91)
        path = high_debug._screen_path(task_id, family)
        raw = path.read_text(encoding='utf-8')
        self.assertNotIn('refused', raw)
        self.assertNotIn('credential', raw)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        for supplied, expected in (
                ([('keyword_assign', 1)], 'HIGH_DEBUG_SCREEN_DISPOSITION_MISMATCH'),
                ([('keyword_assign', 2), ('unknown', 3)], 'HIGH_DEBUG_SCREEN_DISPOSITION_MISMATCH')):
            with self.assertRaises(LLMToolError) as caught:
                high_debug.record_screen_disposition(task_id, family, supplied,
                    'FALSE_POSITIVE', 'https://github.com/org/repo/issues/1#issuecomment-2')
            self.assertEqual(caught.exception.high_debug_code, expected)
        for classification, ref in (("BAD", 'https://github.com/org/repo/issues/1#issuecomment-2'),
                                    ('FALSE_POSITIVE', 'https://example.com/1')):
            with self.assertRaises(LLMToolError) as caught:
                high_debug.record_screen_disposition(task_id, family, [('keyword_assign', 2)],
                                                     classification, ref)
            self.assertEqual(caught.exception.high_debug_code,
                             'HIGH_DEBUG_SCREEN_DISPOSITION_INVALID')
        high_debug.record_screen_disposition(task_id, family, [('keyword_assign', 2)],
            'SYNTHETIC_TEST_DATA', 'https://github.com/org/repo/issues/1#issuecomment-2')

    def test_screen_gate_blocks_before_d6_or_runner_until_disposition(self):
        task_id, family = 'a' * 32, 'family'
        high_debug._write_screen_record(task_id, family, 'HIGH_DEBUG_EVIDENCE_SENSITIVE',
                                        'EVIDENCE', [('unclassified', 1)], 4)
        store = _Store()
        create, runner = unittest.mock.Mock(), unittest.mock.Mock()
        with self.assertRaises(LLMToolError) as caught:
            high_debug.run_high_debug(store=store, task_id=task_id,
                executor_task='executor', pr_number=17, family=family,
                failed_corrections=2, runner=runner, create_d6=create)
        self.assertEqual(caught.exception.high_debug_code,
                         'HIGH_DEBUG_SCREEN_DISPOSITION_REQUIRED')
        self.assertEqual(caught.exception.high_debug_detail, ' rules=unclassified lines=1')
        create.assert_not_called()
        runner.assert_not_called()

    def test_screen_gate_allows_rerun_after_disposition_and_keeps_record(self):
        task_id, family = 'a' * 32, 'family'
        high_debug._write_screen_record(task_id, family, 'HIGH_DEBUG_EVIDENCE_SENSITIVE',
                                        'EVIDENCE', [('unclassified', 1)], 4)
        high_debug.record_screen_disposition(task_id, family, [('unclassified', 1)],
            'FALSE_POSITIVE', 'https://github.com/org/repo/issues/1#issuecomment-2')
        report = ''.join('## ' + heading + '\nDetails.\n' for heading in high_debug.REPORT_SECTIONS)
        report += 'RCA_CONFIRMED'
        _result, _invoke, store = self._run_case(report)
        self.assertEqual(store.entries[0]['status'], 'RCA_CONFIRMED')
        self.assertTrue(high_debug._screen_path(task_id, family).exists())

    def test_screen_disposition_is_scoped_to_task_and_family(self):
        high_debug._write_screen_record('a' * 32, 'family', 'HIGH_DEBUG_REPORT_INVALID',
                                        'REPORT', [('jwt', 4)], 10)
        with self.assertRaises(LLMToolError) as caught:
            high_debug.record_screen_disposition('b' * 32, 'family', [('jwt', 4)],
                'FALSE_POSITIVE', 'https://github.com/org/repo/issues/1#issuecomment-2')
        self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_SCREEN_NO_REFUSAL')
        with self.assertRaises(LLMToolError) as caught:
            high_debug.record_screen_disposition('a' * 32, 'other', [('jwt', 4)],
                'FALSE_POSITIVE', 'https://github.com/org/repo/issues/1#issuecomment-2')
        self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_SCREEN_NO_REFUSAL')

    def test_bad_and_symlinked_screen_records_are_rejected(self):
        task_id, family = 'a' * 32, 'family'
        path = high_debug._screen_path(task_id, family)
        path.parent.mkdir(parents=True)
        path.write_text('{bad', encoding='utf-8')
        with self.assertRaises(LLMToolError) as caught:
            high_debug.record_screen_disposition(task_id, family, [], 'FALSE_POSITIVE',
                'https://github.com/org/repo/issues/1#issuecomment-2')
        self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_SCREEN_RECORD_INVALID')
        path.unlink()
        path.write_bytes(b'x' * (high_debug._SCREEN_RECORD_LIMIT + 1))
        with self.assertRaises(LLMToolError) as caught:
            high_debug.record_screen_disposition(task_id, family, [], 'FALSE_POSITIVE',
                'https://github.com/org/repo/issues/1#issuecomment-2')
        self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_SCREEN_RECORD_INVALID')
        path.unlink()
        target = Path(self.unposted_root.name) / 'outside.json'
        target.write_text('{}', encoding='utf-8')
        path.symlink_to(target)
        with self.assertRaises(LLMToolError) as caught:
            high_debug.record_screen_disposition(task_id, family, [], 'FALSE_POSITIVE',
                'https://github.com/org/repo/issues/1#issuecomment-2')
        self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_SCREEN_RECORD_INVALID')

    def test_screen_write_failure_keeps_refusal_and_marks_detail(self):
        store = _Store()
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / 'benign.txt'
            evidence.write_text('plain input', encoding='utf-8')
            with patch.object(high_debug, '_emitted_sensitive', return_value=True), \
                 patch.object(high_debug, '_screen_hits', return_value=[('unclassified', 1)]), \
                 patch.object(high_debug, '_write_screen_record', side_effect=OSError('disk')):
                with self.assertRaises(LLMToolError) as caught:
                    high_debug.run_high_debug(store=store, task_id='a' * 32,
                        executor_task='executor', pr_number=17, family='family',
                        failed_corrections=2, evidence=str(evidence))
        self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_EVIDENCE_SENSITIVE')
        self.assertEqual(caught.exception.high_debug_detail,
                         'screen_record=UNRECORDED rules=unclassified lines=1')

    def test_unlocated_screen_match_uses_line_zero(self):
        evidence = Path(self.unposted_root.name) / 'plain-evidence.txt'
        evidence.write_text('benign', encoding='utf-8')
        with patch.object(high_debug, '_emitted_sensitive', return_value=True), \
             patch.object(high_debug, '_screen_hits', return_value=[]):
            with self.assertRaises(LLMToolError) as caught:
                high_debug.run_high_debug(store=_Store(), task_id='a' * 32,
                    executor_task='executor', pr_number=17, family='family',
                    failed_corrections=2, evidence=str(evidence))
        self.assertEqual(caught.exception.high_debug_detail,
                         ' rules=unlocated lines=0')
        record = json.loads(high_debug._screen_path('a' * 32, 'family').read_text())
        self.assertEqual(record['hits'], [['unlocated', 0]])

    def test_size_only_refusal_does_not_write_screen_record(self):
        store = _Store()
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / 'large.txt'
            evidence.write_bytes(b'x' * 8193)
            with self.assertRaises(LLMToolError):
                high_debug.run_high_debug(store=store, task_id='a' * 32,
                    executor_task='executor', pr_number=17, family='family',
                    failed_corrections=2, evidence=str(evidence))
        self.assertFalse(high_debug._screen_path('a' * 32, 'family').exists())

    def test_screen_disposition_module_entry_reports_one_json_line(self):
        task_id, family = 'a' * 32, 'family'
        high_debug._write_screen_record(task_id, family, 'HIGH_DEBUG_EVIDENCE_SENSITIVE',
                                        'EVIDENCE', [('unclassified', 1)], 4)
        out, err = StringIO(), StringIO()
        argv = ['screen-disposition', '--task-id', task_id, '--family', family,
                '--hit', 'unclassified:1', '--classification', 'FALSE_POSITIVE',
                '--ref', 'https://github.com/org/repo/issues/1#issuecomment-2']
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(high_debug._screen_disposition_main(argv), 0)
        self.assertEqual(out.getvalue(), '{"state": "RECORDED"}\n')
        self.assertEqual(err.getvalue(), '')

    def test_screen_disposition_module_entry_refusal_prints_code_only(self):
        out, err = StringIO(), StringIO()
        argv = ['screen-disposition', '--task-id', 'a' * 32, '--family', 'family',
                '--hit', 'unclassified:1', '--classification', 'FALSE_POSITIVE',
                '--ref', 'https://github.com/org/repo/issues/1#issuecomment-2']
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(high_debug._screen_disposition_main(argv), 1)
        self.assertEqual(out.getvalue(), '')
        self.assertEqual(err.getvalue(), 'HIGH_DEBUG_SCREEN_NO_REFUSAL\n')

    def test_new_screen_refusal_resets_prior_disposition(self):
        task_id, family = 'a' * 32, 'family'
        high_debug._write_screen_record(task_id, family, 'HIGH_DEBUG_EVIDENCE_SENSITIVE',
                                        'EVIDENCE', [('unclassified', 1)], 4)
        high_debug.record_screen_disposition(task_id, family, [('unclassified', 1)],
            'FALSE_POSITIVE', 'https://github.com/org/repo/issues/1#issuecomment-2')
        high_debug._write_screen_record(task_id, family, 'HIGH_DEBUG_REPORT_INVALID',
                                        'REPORT', [('jwt', 7)], 8)
        record = json.loads(high_debug._screen_path(task_id, family).read_text())
        self.assertIsNone(record['disposition'])
        self.assertEqual(record['hits'], [['jwt', 7]])

    def test_sensitive_report_rejection_stores_only_screened_text(self):
        report = ''.join('## ' + heading + '\nDetails.\n' for heading in high_debug.REPORT_SECTIONS)
        report += 'RCA_CONFIRMED\nPRIVATE-REFUSED-TEXT'
        with patch.object(high_debug, '_emitted_sensitive', return_value=True), \
             patch.object(high_debug, '_screen_hits', return_value=[('unclassified', 8)]):
            with self.assertRaises(LLMToolError) as caught:
                self._run_case(report)
        self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_REPORT_INVALID')
        entry = Path(self.unposted_root.name) / 'high-debug-screen' / ('a' * 32 + '-family.json')
        raw = entry.read_text(encoding='utf-8')
        self.assertNotIn('PRIVATE-REFUSED-TEXT', raw)
        self.assertNotIn('PRIVATE-REFUSED-TEXT', caught.exception.high_debug_detail)
        self.assertNotIn('PRIVATE-REFUSED-TEXT', high_debug._report_rejection(
            'HIGH_DEBUG_REPORT_INVALID', 'SENSITIVE', 99, report, [('unclassified', 8)]))

    def _recover_main(self, *extra, family='family', d6='executor'):
        """Run ``screen-recover`` through the module entry; outputs stay text-free."""
        argv = ['screen-recover', '--task-id', 'a' * 32, '--family', family, '--d6', d6]
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = high_debug._screen_disposition_main(argv + list(extra))
        for text in (out.getvalue(), err.getvalue()):
            self.assertNotIn(_fixture_value(), text)
        return code, out.getvalue(), err.getvalue()

    def _recover_every_batch(self, source):
        """Confirm each pending batch through the module entry; return the last result.

        ``source`` is the original refused file that every command rereads.
        """
        for _round in range(16):
            code, out, err = self._recover_main('--source', source)
            self.assertEqual((code, err), (0, ''))
            pending = json.loads(out)
            self.assertEqual(pending['state'], 'PENDING')
            self.assertLessEqual(len(pending['hits']), high_debug._SCREEN_HIT_LIMIT)
            argv = ['--source', source, '--batch', str(pending['batch']), '--classification',
                    'SYNTHETIC_TEST_DATA', '--ref', _RECOVERY_REF]
            for hit in pending['hits']:
                argv.extend(('--hit', hit))
            code, out, err = self._recover_main(*argv)
            self.assertEqual((code, err), (0, ''))
            result = json.loads(out)
            if result['state'] == 'CLEARED':
                return result
            self.assertEqual(result['state'], 'CONFIRMED')
        self.fail('recovery did not clear')

    def _clean_report(self):
        return ''.join('## ' + heading + '\nDetails.\n'
                       for heading in high_debug.REPORT_SECTIONS) + 'RCA_CONFIRMED'

    def _evidence_path(self):
        return Path(self.unposted_root.name) / 'evidence.txt'

    def _assert_no_file_holds_fixture(self, *originals):
        """Only the ``originals`` the refusal came from may hold its text."""
        for item in Path(self.unposted_root.name).rglob('*'):
            if item.is_file() and item not in originals:
                self.assertNotIn(_fixture_value().encode('utf-8'), item.read_bytes())
        self.assertFalse((Path(self.unposted_root.name) / 'high-debug-screen-source').exists())

    def _refuse_overflow_evidence(self, count):
        evidence = self._evidence_path()
        evidence.write_text(_keyword_lines(count), encoding='utf-8')
        with self.assertRaises(LLMToolError) as caught:
            high_debug.run_high_debug(store=_Store(), task_id='a' * 32,
                executor_task='executor', pr_number=17, family='family',
                failed_corrections=2, evidence=str(evidence))
        self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_EVIDENCE_SENSITIVE')
        self.assertNotIn(_fixture_value(), caught.exception.high_debug_detail)
        return evidence.read_bytes(), caught.exception

    def test_overflow_evidence_refusal_recovers_by_batches_then_reruns(self):
        raw, refusal = self._refuse_overflow_evidence(33)
        self._assert_screen_detail_count(refusal.high_debug_detail, 33)
        path = high_debug._screen_path('a' * 32, 'family')
        record = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual((record['source'], record['total'], record['omitted'], record['d6']),
                         ('EVIDENCE', 33, 1, 'executor'))
        self.assertEqual(record['source_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertNotIn(_fixture_value(), path.read_text(encoding='utf-8'))
        create, runner = unittest.mock.Mock(), unittest.mock.Mock()
        with patch.object(high_debug.worker_dispatch, 'evaluate') as evaluate:
            with self.assertRaises(LLMToolError) as caught:
                high_debug.run_high_debug(store=_Store(), task_id='a' * 32,
                    executor_task='executor', pr_number=17, family='family',
                    failed_corrections=2, runner=runner, create_d6=create)
        self.assertEqual(caught.exception.high_debug_code,
                         'HIGH_DEBUG_SCREEN_DISPOSITION_REQUIRED')
        evaluate.assert_not_called()
        create.assert_not_called()
        runner.assert_not_called()
        result = self._recover_every_batch(str(self._evidence_path()))
        self.assertEqual((result['confirmed'], result['total'], result['batches']), (33, 33, 2))
        self._assert_no_file_holds_fixture(self._evidence_path())
        _result, _invoke, store = self._run_case(self._clean_report())
        self.assertEqual(store.entries[0]['status'], 'RCA_CONFIRMED')

    def test_overflow_report_refusal_recovers_by_batches_then_reruns(self):
        report = self._sections('RCA_CONFIRMED\n') + _keyword_lines(100)
        with self.assertRaises(LLMToolError) as caught:
            self._run_case(report)
        self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_REPORT_INVALID')
        self.assertNotIn(_fixture_value(), caught.exception.high_debug_detail)
        path = high_debug._screen_path('a' * 32, 'family')
        record = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual((record['source'], record['total'], record['omitted'], record['d6']),
                         ('REPORT', 100, 68, 'executor'))
        self.assertEqual(record['source_sha256'],
                         hashlib.sha256(report.encode('utf-8')).hexdigest())
        with self.assertRaises(LLMToolError) as caught:
            high_debug.record_screen_disposition('a' * 32, 'family',
                [tuple(hit) for hit in record['hits']], 'SYNTHETIC_TEST_DATA', _RECOVERY_REF)
        self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_SCREEN_HITS_OMITTED')
        # The run deletes its report file, so nothing it left can recover this.
        self._assert_no_file_holds_fixture()
        self.assertEqual(self._recover_main(),
                         (1, '', 'HIGH_DEBUG_SCREEN_SOURCE_MISSING\n'))
        # A file that still holds the identical report bytes is a valid source.
        original = Path(self.unposted_root.name) / 'report-original.txt'
        original.write_bytes(report.encode('utf-8'))
        result = self._recover_every_batch(str(original))
        self.assertEqual((result['confirmed'], result['total'], result['batches']),
                         (100, 100, 4))
        self._assert_no_file_holds_fixture(original)
        history = high_debug._screen_history_for(path)
        self.assertEqual(sorted(item.name for item in history.iterdir()),
                         [record['refusal_id'] + f'-b{number:06d}.json'
                          for number in range(1, 5)] +
                         [record['refusal_id'] + '-refusal.json'])
        for item in history.iterdir():
            self.assertNotIn(_fixture_value(), item.read_text(encoding='utf-8'))
        _result, _invoke, store = self._run_case(self._clean_report())
        self.assertEqual(store.entries[0]['status'], 'RCA_CONFIRMED')

    def test_changing_family_or_deleting_the_record_keeps_the_block(self):
        self._refuse_overflow_evidence(33)
        create, runner = unittest.mock.Mock(), unittest.mock.Mock()

        def rerun(family):
            with self.assertRaises(LLMToolError) as caught:
                high_debug.run_high_debug(store=_Store(), task_id='a' * 32,
                    executor_task='executor', pr_number=17, family=family,
                    early_reason='new family', runner=runner, create_d6=create)
            return caught.exception

        refused = rerun('other')
        self.assertEqual(refused.high_debug_code, 'HIGH_DEBUG_SCREEN_DISPOSITION_REQUIRED')
        self.assertTrue(refused.high_debug_detail.startswith('family=family '))
        self.assertEqual(self._recover_main(family='other'),
                         (1, '', 'HIGH_DEBUG_SCREEN_NO_REFUSAL\n'))
        self.assertEqual(self._recover_main(d6='executor-2'),
                         (1, '', 'HIGH_DEBUG_SCREEN_RECOVERY_BINDING\n'))
        high_debug._screen_path('a' * 32, 'family').unlink()
        for family in ('family', 'other'):
            with self.subTest(family=family):
                self.assertEqual(rerun(family).high_debug_code,
                                 'HIGH_DEBUG_SCREEN_RECORD_INVALID')
        self.assertEqual(self._recover_main(),
                         (1, '', 'HIGH_DEBUG_SCREEN_RECORD_INVALID\n'))
        create.assert_not_called()
        runner.assert_not_called()

    def test_overflow_evidence_without_its_original_stays_blocked(self):
        self._refuse_overflow_evidence(33)
        evidence = str(self._evidence_path())
        self.assertEqual(self._recover_main(),
                         (1, '', 'HIGH_DEBUG_SCREEN_SOURCE_MISSING\n'))
        self._evidence_path().unlink()
        self.assertEqual(self._recover_main('--source', evidence),
                         (1, '', 'HIGH_DEBUG_SCREEN_SOURCE_MISSING\n'))
        self._assert_no_file_holds_fixture()
        create, runner = unittest.mock.Mock(), unittest.mock.Mock()
        with self.assertRaises(LLMToolError) as caught:
            high_debug.run_high_debug(store=_Store(), task_id='a' * 32,
                executor_task='executor', pr_number=17, family='family',
                failed_corrections=2, runner=runner, create_d6=create)
        self.assertEqual(caught.exception.high_debug_code,
                         'HIGH_DEBUG_SCREEN_DISPOSITION_REQUIRED')
        create.assert_not_called()
        runner.assert_not_called()

    def test_screen_recover_module_entry_refusals_print_code_only(self):
        _hits, path = self._write_screen_hits('EVIDENCE', 1)
        before = path.read_bytes()
        self.assertEqual(self._recover_main(),
                         (1, '', 'HIGH_DEBUG_SCREEN_RECOVERY_NOT_NEEDED\n'))
        base = ['--task-id', 'a' * 32, '--family', 'family']
        confirm = ['--hit', 'unclassified:1', '--classification', 'FALSE_POSITIVE',
                   '--ref', _RECOVERY_REF]
        for argv, code in (
                (['screen-recover'] + base, 'HIGH_DEBUG_SCREEN_RECOVERY_INVALID'),
                (['screen-recover'] + base + ['--d6', 'executor', '--batch', '1'],
                 'HIGH_DEBUG_SCREEN_RECOVERY_INVALID'),
                (['screen-recover'] + base + ['--d6', 'executor', '--batch', 'x'] + confirm,
                 'HIGH_DEBUG_SCREEN_RECOVERY_INVALID'),
                (['screen-recover'] + base + ['--d6', 'executor', '--hit', 'bad'],
                 'HIGH_DEBUG_SCREEN_RECOVERY_INVALID'),
                (['screen-recover'] + base + ['--d6', 'executor', '--unknown'],
                 'HIGH_DEBUG_SCREEN_RECOVERY_INVALID'),
                (['screen-disposition'] + base + ['--d6', 'executor'] + confirm,
                 'HIGH_DEBUG_SCREEN_DISPOSITION_INVALID'),
                (['screen-disposition'] + base + ['--batch', '1'] + confirm,
                 'HIGH_DEBUG_SCREEN_DISPOSITION_INVALID'),
                (['screen-disposition'] + base + ['--source', 'evidence.txt'] + confirm,
                 'HIGH_DEBUG_SCREEN_DISPOSITION_INVALID')):
            with self.subTest(argv=argv):
                out, err = StringIO(), StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    self.assertEqual(high_debug._screen_disposition_main(argv), 1)
                self.assertEqual((out.getvalue(), err.getvalue()), ('', code + '\n'))
        self.assertEqual(path.read_bytes(), before)

    def test_usage_comes_from_last_turn_completed_event(self):
        events = (b'{"type":"thread.started","thread_id":"thread-1234567890"}\n'
                  b'{"type":"turn.completed","usage":{"input_tokens":1,"cached_input_tokens":0,"output_tokens":2,"reasoning_output_tokens":1}}\n'
                  b'{"type":"turn.completed","usage":{"input_tokens":7,"cached_input_tokens":3,"output_tokens":5,"reasoning_output_tokens":2}}\n')
        self.assertEqual(high_debug._usage_from_events(events),
                         {'input': 7, 'cached': 3, 'output': 5, 'reasoning': 2})

    def test_session_model_and_effort_are_read_from_rollout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rollout = root / '2026' / '10' / '27' / 'rollout-test-thread-1234567890.jsonl'
            rollout.parent.mkdir(parents=True)
            rollout.write_text('{"type":"session_meta","payload":{"model":"gpt-test","reasoning_effort":"high","other":"ignored"}}\n', encoding='utf-8')
            self.assertEqual(high_debug._session_observed('thread-1234567890', root),
                             ('gpt-test', 'high'))

    def test_missing_session_file_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(high_debug._session_observed('thread-1234567890', directory),
                             ('UNKNOWN', 'UNKNOWN'))

    def test_conflicting_session_models_are_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            day = root / '2026' / '10' / '27'
            day.mkdir(parents=True)
            for suffix, model in (('a', 'gpt-a'), ('b', 'gpt-b')):
                (day / f'rollout-{suffix}-thread-1234567890.jsonl').write_text(
                    json.dumps({'payload': {'model': model, 'effort': 'high'}}), encoding='utf-8')
            self.assertEqual(high_debug._session_observed('thread-1234567890', root),
                             ('UNKNOWN', 'high'))

    def test_symlinked_session_file_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'sessions'
            day = root / '2026' / '10' / '27'
            day.mkdir(parents=True)
            target = Path(directory) / 'outside.jsonl'
            target.write_text('{"payload":{"model":"gpt-test","effort":"high"}}', encoding='utf-8')
            (day / 'rollout-test-thread-1234567890.jsonl').symlink_to(target)
            self.assertEqual(high_debug._session_observed('thread-1234567890', root),
                             ('UNKNOWN', 'UNKNOWN'))

    def test_oversized_session_file_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            day = root / '2026' / '10' / '27'
            day.mkdir(parents=True)
            rollout = day / 'rollout-test-thread-1234567890.jsonl'
            rollout.write_bytes(b' ' * (high_debug._SESSION_READ_LIMIT + 1))
            self.assertEqual(high_debug._session_observed('thread-1234567890', root),
                             ('UNKNOWN', 'UNKNOWN'))


class HighDebugWrongTaskOrFamilyTests(unittest.TestCase):
    """#914: run_high_debug refuses a wrong Task, executor D6 or family before any run."""

    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        store_root_patch = patch.object(high_debug.task_store, 'default_root',
                                        return_value=Path(root.name) / 'tasks')
        store_root_patch.start()
        self.addCleanup(store_root_patch.stop)
        poster_patch = patch.object(high_debug, '_post_github_record',
                                    return_value=True)
        self.poster = poster_patch.start()
        self.addCleanup(poster_patch.stop)

    _REMOVED = object()

    def _refusal(self, store, *, executor_task='executor', family='family',
                 read_registration=None):
        create, runner = unittest.mock.Mock(), unittest.mock.Mock()
        reader = read_registration or unittest.mock.Mock()
        with patch.object(high_debug.worker_dispatch, 'evaluate',
                          return_value=SimpleNamespace(allowed=True)):
            with self.assertRaises(LLMToolError) as caught:
                high_debug.run_high_debug(store=store, task_id='a' * 32,
                    executor_task=executor_task, pr_number=17, family=family,
                    failed_corrections=2, runner=runner, create_d6=create,
                    read_registration=reader)
        if read_registration is None:
            # The refusal came before the D6 registration was read.
            reader.assert_not_called()
        create.assert_not_called()
        runner.assert_not_called()
        self.poster.assert_not_called()
        self.assertEqual(store.entries, [])
        self.assertEqual(store.events, [])
        return caught.exception.high_debug_code

    @classmethod
    def _store(cls, **changes):
        """The module ``_Store`` whose record has ``changes``; _REMOVED drops a key."""
        store = _Store()
        get = store.get

        def changed(task_id):
            record = get(task_id)
            for key, value in changes.items():
                if value is cls._REMOVED:
                    record.pop(key, None)
                else:
                    record[key] = value
            return record
        store.get = changed
        return store

    def test_record_naming_another_task_is_task_invalid(self):
        self.assertEqual(self._refusal(self._store(task_id='b' * 32)),
                         'HIGH_DEBUG_TASK_INVALID')

    def test_executor_task_other_than_completion_is_d6_unavailable(self):
        self.assertEqual(self._refusal(_Store(), executor_task='other-executor'),
                         'HIGH_DEBUG_D6_UNAVAILABLE')

    def test_missing_completion_is_d6_unavailable(self):
        for completion in (self._REMOVED, None):
            with self.subTest(completion=completion):
                self.assertEqual(self._refusal(self._store(completion=completion)),
                                 'HIGH_DEBUG_D6_UNAVAILABLE')

    def test_registration_reader_error_is_d6_unavailable(self):
        reader = unittest.mock.Mock(side_effect=OSError('registry unavailable'))
        self.assertEqual(self._refusal(_Store(), read_registration=reader),
                         'HIGH_DEBUG_D6_UNAVAILABLE')
        reader.assert_called_once()

    def test_failed_corrections_for_family_without_claims_is_argument_invalid(self):
        self.assertEqual(self._refusal(_Store(), family='other-family'),
                         'HIGH_DEBUG_ARGUMENT_INVALID')


class _Store:
    def __init__(self):
        self.entries = []
        self.events = []
    def get(self, task_id):
        return {'task_id': task_id, 'run_id': 'b' * 32,
                'review_repair_claims': [
                    {'family': 'family', 'pr': 17},
                    {'family': 'family', 'pr': 17}],
                'completion': {'executor_task_id': 'executor',
                               'evidence': {'common_dir': '/tmp/ignored'}},
                'high_debug': list(self.entries)}
    @staticmethod
    def _completion_now():
        return '2026-10-27T00:00:00Z'
    def append_high_debug(self, _task_id, entry):
        self.entries.append(entry)
    def record_completion_event(self, _task_id, **event):
        if not any(item['event_id'] == event['event_id'] for item in self.events):
            self.events.append(event)
            return {'created': True}
        return {'created': False}


if __name__ == '__main__':
    unittest.main()
