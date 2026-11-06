"""Sensitive output screening applies to added diff content only."""

import copy
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import high_debug
from llmtool import task_submission_cli as submission_cli
from llmtool.errors import LLMToolError
from llmtool.task_store import new_id
from llmtool.task_submission_cli import _strict_developer_output_rejection


def _screened_looking_value():
    return 'AK' + 'IA' + ('A' * 16)


def _tracked_diff(old, new):
    return (
        'diff --git a/example.py b/example.py\n'
        '--- a/example.py\n'
        '+++ b/example.py\n'
        '@@ -1 +1 @@\n'
        '-' + old + '\n'
        '+' + new + '\n'
    )


class AddedLineScreenScopeTests(unittest.TestCase):
    def test_match_on_removed_line_is_allowed(self):
        rejection = _strict_developer_output_rejection(
            _tracked_diff(_screened_looking_value(), 'safe'), ['example.py'])
        self.assertIsNone(rejection)

    def test_match_on_added_line_is_rejected(self):
        rejection = _strict_developer_output_rejection(
            _tracked_diff('safe', _screened_looking_value()), ['example.py'])
        self.assertEqual(rejection, 'DEVELOPER_OUTPUT_SENSITIVE')

    def test_match_in_new_file_is_rejected(self):
        patch = (
            'diff --git a/new.py b/new.py\n'
            'new file mode 100644\n'
            '--- /dev/null\n'
            '+++ b/new.py\n'
            '@@ -0,0 +1 @@\n'
            '+' + _screened_looking_value() + '\n'
        )
        rejection = _strict_developer_output_rejection(
            patch, ['new.py'], allow_new_files=True)
        self.assertEqual(rejection, 'DEVELOPER_OUTPUT_SENSITIVE')

    def test_context_line_is_not_screened(self):
        value = _screened_looking_value()
        patch = (
            'diff --git a/example.py b/example.py\n'
            '--- a/example.py\n'
            '+++ b/example.py\n'
            '@@ -1,2 +1,2 @@\n'
            ' ' + value + '\n'
            '-old\n'
            '+new\n'
        )
        rejection = _strict_developer_output_rejection(patch, ['example.py'])
        self.assertIsNone(rejection)


def _keyword_value():
    # Joined so this module holds no literal keyword assignment.
    return 'synthetic-' + 'fixture'


def _keyword_line():
    return 'pass' + 'word = "' + _keyword_value() + '"'


def _sensitive_diff():
    # Output line 5 is an unscreened context line; line 7 is the refused one.
    return (
        'diff --git a/example.py b/example.py\n'
        '--- a/example.py\n'
        '+++ b/example.py\n'
        '@@ -1,2 +1,2 @@\n'
        ' ' + _screened_looking_value() + '\n'
        '-safe = None\n'
        '+' + _keyword_line() + '\n'
    )


class _Store:
    """The Task store surface the rejection writer and delegate gate use."""

    def __init__(self, root):
        self.root = root
        self.records = {}

    def _ensure_root(self):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def task_ids(self):
        return sorted(self.records)

    def get(self, task_id):
        return copy.deepcopy(self.records[task_id])

    def update(self, task_id, **changes):
        self.records[task_id].update(copy.deepcopy(changes))
        return self.get(task_id)


class DeveloperOutputSensitiveRefusalGateTests(unittest.TestCase):
    """#981 code gates 1 and 2 for DEVELOPER_OUTPUT_SENSITIVE."""

    executor = 'executor-d6-1'
    ref = 'https://github.com/example/project/issues/981#issuecomment-1'

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = _Store(Path(temp.name).resolve() / 'tasks')

    def _task(self, *, error_code=None, executor=None):
        task_id = new_id()
        developer = {'state': 'DEVELOPER_DISPATCHING', 'artifact': None,
                     'artifact_sha256': None, 'error_code': error_code,
                     'created_at': '2026-11-07T00:00:00Z'}
        if error_code is not None:
            developer['state'] = 'DEVELOPER_OUTPUT_REJECTED'
        self.store.records[task_id] = {
            'task_id': task_id, 'developer': developer,
            'completion': {'executor_task_id': executor or self.executor}}
        return task_id

    def _refuse(self, value, error_code='DEVELOPER_OUTPUT_SENSITIVE'):
        task_id = self._task()
        developer = self.store.get(task_id)['developer']
        submission_cli._persist_developer_output_rejection(
            self.store, task_id, developer, error_code, None, value)
        return task_id

    def _record_path(self, task_id):
        return (self.store.root.parent / 'high-debug-screen' /
                (task_id + '-developer-output.json'))

    def _dispatch(self):
        current = self._task()
        return submission_cli._check_delegate_repair_cap(
            Namespace(task_id=current, executor_task=self.executor), self.store)

    def _dispose(self, task_id, hits):
        high_debug.record_screen_disposition(
            task_id, 'developer-output', hits, 'SYNTHETIC_TEST_DATA', self.ref,
            root=self.store.root)

    def test_refusal_records_rule_names_and_output_lines_only(self):
        diff = _sensitive_diff()
        self.assertEqual(_strict_developer_output_rejection(diff, ['example.py']),
                         'DEVELOPER_OUTPUT_SENSITIVE')
        task_id = self._refuse(diff)
        raw = self._record_path(task_id).read_text(encoding='utf-8')
        record = json.loads(raw)
        self.assertEqual(set(record), {'code', 'source', 'hits', 'omitted', 'bytes',
                                       'recorded_at', 'disposition'})
        self.assertEqual(record['code'], 'DEVELOPER_OUTPUT_SENSITIVE')
        self.assertEqual(record['source'], 'DEVELOPER_OUTPUT')
        self.assertEqual(record['hits'], [['keyword_assign', 7]])
        self.assertEqual(record['omitted'], 0)
        self.assertEqual(record['bytes'], len(diff.encode('utf-8')))
        self.assertIsNone(record['disposition'])
        for text in (_keyword_value(), _keyword_line(), _screened_looking_value()):
            self.assertNotIn(text, raw)
        self.assertEqual(self.store.get(task_id)['developer']['error_code'],
                         'DEVELOPER_OUTPUT_SENSITIVE')
        self.assertNotIn(_keyword_value(), json.dumps(self.store.get(task_id)))

    def test_rerun_on_same_d6_is_refused_until_disposition(self):
        task_id = self._refuse(_sensitive_diff())
        with self.assertRaises(LLMToolError) as raised:
            self._dispatch()
        self.assertEqual(
            str(raised.exception),
            'DELEGATE_SCREEN_DISPOSITION_REQUIRED: Task ' + task_id +
            ' family=developer-output rules=keyword_assign lines=7')
        self.assertNotIn(_keyword_value(), str(raised.exception))

    def test_rerun_on_other_d6_is_not_refused(self):
        self._refuse(_sensitive_diff())
        current = self._task(executor='executor-d6-2')
        self.assertIsNone(submission_cli._check_delegate_repair_cap(
            Namespace(task_id=current, executor_task='executor-d6-2'), self.store))

    def test_rerun_is_accepted_after_disposition_names_every_hit(self):
        task_id = self._refuse(_sensitive_diff())
        self._dispose(task_id, [('keyword_assign', 7)])
        self.assertIsNone(self._dispatch())
        record = json.loads(self._record_path(task_id).read_text(encoding='utf-8'))
        self.assertEqual(record['disposition']['classification'], 'SYNTHETIC_TEST_DATA')
        self.assertEqual(record['disposition']['ref'], self.ref)

    def test_mismatched_disposition_is_refused_and_rerun_stays_refused(self):
        task_id = self._refuse(_sensitive_diff())
        for hits in ([('keyword_assign', 6)], [],
                     [('keyword_assign', 7), ('cloud_key', 7)],
                     [('auth_scheme', 7)]):
            with self.subTest(hits=hits):
                with self.assertRaises(LLMToolError) as raised:
                    self._dispose(task_id, hits)
                self.assertEqual(raised.exception.high_debug_code,
                                 'HIGH_DEBUG_SCREEN_DISPOSITION_MISMATCH')
        record = json.loads(self._record_path(task_id).read_text(encoding='utf-8'))
        self.assertIsNone(record['disposition'])
        with self.assertRaises(LLMToolError) as raised:
            self._dispatch()
        self.assertTrue(str(raised.exception).startswith(
            'DELEGATE_SCREEN_DISPOSITION_REQUIRED: Task ' + task_id))

    def test_refusal_without_record_is_refused_as_unlocated(self):
        task_id = self._task(error_code='DEVELOPER_OUTPUT_SENSITIVE')
        with self.assertRaises(LLMToolError) as raised:
            self._dispatch()
        self.assertEqual(
            str(raised.exception),
            'DELEGATE_SCREEN_DISPOSITION_REQUIRED: Task ' + task_id +
            ' family=developer-output rules=unlocated lines=0')
        self._dispose(task_id, [('unlocated', 0)])
        self.assertIsNone(self._dispatch())

    def test_other_output_refusals_record_nothing_and_do_not_gate(self):
        task_id = self._refuse(_sensitive_diff(), 'DEVELOPER_OUTPUT_OUT_OF_SCOPE')
        self.assertFalse(self._record_path(task_id).exists())
        self.assertIsNone(self._dispatch())


if __name__ == '__main__':
    unittest.main()
