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

    def _run_case(self, report, *, dirty=False, snapshot_head=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        store = _Store()
        registration = SimpleNamespace(result_sha='a' * 40, root=str(root),
                                       common_dir=str(root / '.git'))
        def runner(*, prompt, cwd, output_path):
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
