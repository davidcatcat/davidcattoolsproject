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
    def test_dispatch_refusal_has_stable_code(self):
        store = SimpleNamespace(get=lambda _task: {'task_id': 'a' * 32})
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
        store = SimpleNamespace(get=lambda _task: {'task_id': 'a' * 32})
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


class _Store:
    def __init__(self):
        self.entries = []
        self.events = []
    def get(self, task_id):
        return {'task_id': task_id, 'run_id': 'b' * 32,
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
