"""Isolated contract checks for the one-shot high-debug workflow."""
import tempfile
import unittest
import subprocess
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import high_debug
from llmtool.errors import LLMToolError


class HighDebugTests(unittest.TestCase):
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
        return caught.exception.high_debug_code, created

    def test_failed_codex_attempts_are_recorded_and_retry_uses_a_new_d6(self):
        store = _Store()
        root = self._repo()
        cases = (
            ({'returncode': 1}, 'HIGH_DEBUG_RUN_FAILED'),
            ({'runner_error': subprocess.TimeoutExpired('codex', 1800)},
             'HIGH_DEBUG_RUN_FAILED'),
            ({'report': 'x' * 16385}, 'HIGH_DEBUG_REPORT_INVALID'),
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
            evidence.write_text('Bear' + 'er ' + 'a' * 40, encoding='utf-8')
            with patch.object(high_debug.worker_dispatch, 'evaluate',
                              return_value=SimpleNamespace(allowed=True)):
                with self.assertRaises(LLMToolError) as caught:
                    high_debug.run_high_debug(store=store, task_id='a' * 32,
                        executor_task='executor', pr_number=1, family='family',
                        failed_corrections=2, evidence=str(evidence))
            self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_EVIDENCE_SENSITIVE')

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
