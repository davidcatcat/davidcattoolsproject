"""Sensitive output screening applies to added diff content only."""

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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
    # synthetic-fixture: screen-tool test
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


def _many_hits_diff(count):
    # Output lines 6 onward are refused added lines, one distinct hit each.
    # synthetic-fixture: screen-tool test
    added = ('+' + _keyword_line() + '\n') * count
    return (
        'diff --git a/example.py b/example.py\n'
        '--- a/example.py\n'
        '+++ b/example.py\n'
        '@@ -1 +1,' + str(count) + ' @@\n'
        '-safe = None\n'
    ) + added


def _digest(hits):
    """Independent SHA-256 of a complete sorted (rule, line) hit list."""
    canonical = json.dumps([[rule, line] for rule, line in hits], separators=(',', ':'))
    return hashlib.sha256(canonical.encode('ascii')).hexdigest()


class _Store:
    """The Task store surface the rejection writer and delegate gate use."""

    def __init__(self, root):
        self.root = root
        self.records = {}

    def _ensure_root(self):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def task_ids(self):
        return sorted(self.records)

    def completion_d6_task_ids(self, executor_task_id):
        matching = []
        for task_id, record in self.records.items():
            completion = record.get('completion')
            if (isinstance(completion, dict) and
                    completion.get('executor_task_id') == executor_task_id):
                matching.append(task_id)
        return sorted(matching)

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
        # Every call re-dispatches the same Task, as a rerun after a refused
        # delegate does. The gate skips the Task being dispatched, so a second
        # call does not count an earlier call as a prior Developer run and
        # these tests reach only the screen gate, never the repair cap (D7).
        if getattr(self, '_current', None) not in self.store.records:
            self._current = self._task()
        return submission_cli._check_delegate_repair_cap(
            Namespace(task_id=self._current, executor_task=self.executor), self.store)

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
                                       'recorded_at', 'disposition', 'total',
                                       'hits_sha256', 'source_sha256', 'd6',
                                       'refusal_id'})
        self.assertEqual(record['code'], 'DEVELOPER_OUTPUT_SENSITIVE')
        self.assertEqual(record['source'], 'DEVELOPER_OUTPUT')
        self.assertEqual(record['hits'], [['keyword_assign', 7]])
        self.assertEqual(record['omitted'], 0)
        self.assertEqual(record['total'], 1)
        self.assertEqual(record['hits_sha256'], _digest([('keyword_assign', 7)]))
        self.assertEqual(record['source_sha256'],
                         hashlib.sha256(diff.encode('utf-8')).hexdigest())
        self.assertEqual(record['d6'], self.executor)
        self.assertEqual(record['bytes'], len(diff.encode('utf-8')))
        self.assertIsNone(record['disposition'])
        # No copy of the screened source is ever kept.
        self.assertFalse((self.store.root.parent / 'high-debug-screen-source').exists())
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

    def test_partial_hit_list_is_refused_and_rerun_stays_refused(self):
        limit = high_debug._SCREEN_HIT_LIMIT
        task_id = self._refuse(_many_hits_diff(limit + 8))
        path = self._record_path(task_id)
        before = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(len(before['hits']), limit)
        self.assertEqual(before['omitted'], 8)
        with self.assertRaises(LLMToolError) as raised:
            self._dispose(task_id, before['hits'])
        self.assertEqual(raised.exception.high_debug_code,
                         'HIGH_DEBUG_SCREEN_HITS_OMITTED')
        self.assertNotIn(_keyword_value(), str(raised.exception))
        self.assertEqual(json.loads(path.read_text(encoding='utf-8')), before)
        with self.assertRaises(LLMToolError) as raised:
            self._dispatch()
        self.assertTrue(str(raised.exception).startswith(
            'DELEGATE_SCREEN_DISPOSITION_REQUIRED: Task ' + task_id))
        self.assertNotIn(_keyword_value(), str(raised.exception))

    def test_every_record_source_refuses_disposition_with_omitted_hits(self):
        limit = high_debug._SCREEN_HIT_LIMIT
        hits = [('keyword_assign', line) for line in range(1, limit + 2)]
        for source, code in high_debug._SCREEN_SOURCE_CODES.items():
            with self.subTest(source=source):
                task_id = self._task()
                high_debug._write_screen_record(
                    task_id, 'developer-output', code, source, hits, 1,
                    root=self.store.root)
                with self.assertRaises(LLMToolError) as raised:
                    self._dispose(task_id, hits[:limit])
                self.assertEqual(raised.exception.high_debug_code,
                                 'HIGH_DEBUG_SCREEN_HITS_OMITTED')
                record = json.loads(
                    self._record_path(task_id).read_text(encoding='utf-8'))
                self.assertEqual(record['omitted'], 1)
                self.assertIsNone(record['disposition'])

    def test_stored_disposition_cannot_clear_omitted_hits(self):
        task_id = self._refuse(
            _many_hits_diff(high_debug._SCREEN_HIT_LIMIT + 1))
        path = self._record_path(task_id)
        record = json.loads(path.read_text(encoding='utf-8'))
        record['disposition'] = {'classification': 'SYNTHETIC_TEST_DATA',
                                 'ref': self.ref,
                                 'recorded_at': record['recorded_at']}
        path.write_text(json.dumps(record), encoding='utf-8')
        before = path.read_bytes()
        with self.assertRaises(LLMToolError) as raised:
            high_debug._read_screen_record(task_id, 'developer-output',
                                           root=self.store.root)
        self.assertEqual(raised.exception.high_debug_code,
                         'HIGH_DEBUG_SCREEN_RECORD_INVALID')
        with self.assertRaises(LLMToolError) as raised:
            self._dispatch()
        self.assertNotIn(_keyword_value(), str(raised.exception))
        self.assertEqual(path.read_bytes(), before)

    def test_record_with_omitted_zero_accepts_a_full_disposition(self):
        limit = high_debug._SCREEN_HIT_LIMIT
        task_id = self._refuse(_many_hits_diff(limit))
        path = self._record_path(task_id)
        record = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(len(record['hits']), limit)
        self.assertEqual(record['omitted'], 0)
        self._dispose(task_id, record['hits'])
        self.assertIsNone(self._dispatch())
        record = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(record['disposition']['classification'],
                         'SYNTHETIC_TEST_DATA')

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


_SOURCES = ('EVIDENCE', 'REPORT', 'DEVELOPER_OUTPUT')
_HIT_COUNTS = (0, 1, 32, 33, 100)
_OUT_OF_RANGE = 'HIGH_DEBUG_SCREEN_BATCH_OUT_OF_RANGE'
_WRONG = 'HIGH_DEBUG_SCREEN_BATCH_WRONG'
_REPEATED = 'HIGH_DEBUG_SCREEN_BATCH_REPEATED'
_RECOVERY_INVALID = 'HIGH_DEBUG_SCREEN_RECOVERY_INVALID'
_SOURCE_MISSING = 'HIGH_DEBUG_SCREEN_SOURCE_MISSING'
_BASE_SHA = 'b' * 40


def _keyword_text(count):
    """``count`` lines with one distinct keyword hit each; 0 has no locatable hit."""
    return ''.join(_keyword_line() + '\n' for _number in range(count)) or 'plain\n'


def _source_text(source, count):
    """Screened text of ``source`` holding exactly ``count`` distinct hits."""
    return _many_hits_diff(count) if source == 'DEVELOPER_OUTPUT' else _keyword_text(count)


class ScreenBatchRecoveryTests(unittest.TestCase):
    """#1097 batched, source-bound recovery for every screen record source."""

    executor = 'executor-d6-1'
    ref = 'https://github.com/example/project/issues/1097#issuecomment-1'

    def setUp(self):
        self._fresh_store()
        # The original of a refusal is one file outside the state directories:
        # the evidence or report file, or the fake D6 whose diff is that file.
        for target, name, fake in (
                (high_debug.task_store, 'default_root', lambda: self.store.root),
                (submission_cli, 'TaskStore', lambda *_root: self.store),
                (submission_cli.task_completion, 'read_d6_registration',
                 lambda _d6, common_dir, allowed_states=None:
                     SimpleNamespace(root=common_dir)),
                (submission_cli.task_completion, '_worktree_diff',
                 lambda root, _base: (['example.py'], Path(root).read_bytes()))):
            active = patch.object(target, name, side_effect=fake)
            active.start()
            self.addCleanup(active.stop)

    def _fresh_store(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = _Store(Path(temp.name).resolve() / 'tasks')
        self.originals = Path(temp.name).resolve() / 'originals'
        self.originals.mkdir()

    def _original(self, task_id):
        return self.originals / task_id

    def _source_argument(self, task_id, family):
        """The named source of a recovery; Developer output needs none."""
        return None if family == 'developer-output' else str(self._original(task_id))

    def _task(self):
        task_id = new_id()
        self.store.records[task_id] = {
            'task_id': task_id,
            'developer': {'state': 'DEVELOPER_DISPATCHING', 'artifact': None,
                          'artifact_sha256': None, 'error_code': None,
                          'source_sha': _BASE_SHA,
                          'created_at': '2026-11-07T00:00:00Z'},
            'completion': {'executor_task_id': self.executor,
                           'evidence': {'common_dir': str(self._original(task_id))}}}
        return task_id

    def _refuse(self, source, count, *, task_id=None):
        """Store one refusal of ``source`` the way its writer does."""
        task_id = task_id or self._task()
        text = _source_text(source, count)
        self._original(task_id).write_text(text, encoding='utf-8')
        if source == 'DEVELOPER_OUTPUT':
            submission_cli._persist_developer_output_rejection(
                self.store, task_id, self.store.get(task_id)['developer'],
                'DEVELOPER_OUTPUT_SENSITIVE', None, text)
            return task_id, 'developer-output', text
        raw = text.encode('utf-8')
        high_debug._write_screen_record(
            task_id, 'family', high_debug._SCREEN_SOURCE_CODES[source], source,
            high_debug._screen_hits(text) or [('unlocated', 0)], len(raw),
            root=self.store.root, screened=raw, d6=self.executor)
        return task_id, 'family', text

    @staticmethod
    def _expected_hits(source, count):
        if not count:
            return [('unlocated', 0)]
        # Developer output lines 6 onward are the added lines.
        first = 6 if source == 'DEVELOPER_OUTPUT' else 1
        return [('keyword_assign', line) for line in range(first, first + count)]

    def _record_path(self, task_id, family):
        return high_debug._screen_location(task_id, family, self.store.root)

    def _record(self, task_id, family):
        return json.loads(self._record_path(task_id, family).read_text(encoding='utf-8'))

    def _write_record(self, task_id, family, record):
        self._record_path(task_id, family).write_text(json.dumps(record), encoding='utf-8')

    def _state_files(self):
        """Every file llmtool keeps beside the Task store."""
        return {path for path in self.store.root.parent.rglob('*')
                if path.is_file() and self.originals not in path.parents}

    def _assert_no_refused_text_copy(self):
        self.assertFalse((self.store.root.parent / 'high-debug-screen-source').exists())
        for path in self._state_files():
            self.assertNotIn(_keyword_value().encode('utf-8'), path.read_bytes())

    def _history(self, task_id, family):
        return high_debug._screen_history_for(self._record_path(task_id, family))

    def _history_names(self, task_id, family):
        return sorted(path.name for path in self._history(task_id, family).iterdir())

    def _pending(self, task_id, family, *, d6=None):
        return high_debug.screen_recovery_pending(
            task_id, family, self.executor if d6 is None else d6, root=self.store.root,
            source=self._source_argument(task_id, family))

    def _confirm(self, task_id, family, batch, hits, *, d6=None,
                 classification='SYNTHETIC_TEST_DATA', ref=None):
        return high_debug.confirm_screen_batch(
            task_id, family, self.executor if d6 is None else d6, batch, hits,
            classification, self.ref if ref is None else ref, root=self.store.root,
            source=self._source_argument(task_id, family))

    @staticmethod
    def _parse(pending):
        return [(item.split(':')[0], int(item.split(':')[1])) for item in pending['hits']]

    def _confirm_next(self, task_id, family):
        pending = self._pending(task_id, family)
        return self._confirm(task_id, family, pending['batch'], self._parse(pending))

    def _dispose(self, task_id, family, hits):
        high_debug.record_screen_disposition(task_id, family, hits, 'SYNTHETIC_TEST_DATA',
                                             self.ref, root=self.store.root)

    def _dispatch(self):
        current = self._task()
        return submission_cli._check_delegate_repair_cap(
            Namespace(task_id=current, executor_task=self.executor), self.store)

    def _assert_refused(self, code, call, *args, **kwargs):
        with self.assertRaises(LLMToolError) as raised:
            call(*args, **kwargs)
        self.assertEqual(raised.exception.high_debug_code, code)
        self.assertNotIn(_keyword_value(), str(raised.exception))

    def _gate(self, source, task_id, family):
        """The gate that refuses a rerun after a refusal of ``source``."""
        if source == 'DEVELOPER_OUTPUT':
            return submission_cli._check_developer_screen_disposition(
                self.store, task_id, self.store.get(task_id)['developer'])
        return high_debug._require_screen_cleared(task_id, family)

    def _assert_blocked(self, source, task_id, family):
        with self.assertRaises(LLMToolError) as raised:
            self._gate(source, task_id, family)
        if source == 'DEVELOPER_OUTPUT':
            self.assertTrue(str(raised.exception).startswith(
                ('DELEGATE_SCREEN_DISPOSITION_REQUIRED: Task ' + task_id,
                 'DELEGATE_SCREEN_RECORD_INVALID: Task ' + task_id)))
        else:
            self.assertIn(raised.exception.high_debug_code,
                          ('HIGH_DEBUG_SCREEN_DISPOSITION_REQUIRED',
                           'HIGH_DEBUG_SCREEN_RECORD_INVALID'))
        for text in (str(raised.exception),
                     getattr(raised.exception, 'high_debug_detail', '')):
            self.assertNotIn(_keyword_value(), text)

    def _assert_stored_files_bounded_and_text_free(self, task_id, family):
        paths = [self._record_path(task_id, family)] + sorted(
            self._history(task_id, family).iterdir())
        for path in paths:
            raw = path.read_bytes()
            self.assertLessEqual(len(raw), high_debug._SCREEN_RECORD_LIMIT)
            self.assertNotIn(_keyword_value().encode('utf-8'), raw)
            self.assertNotIn(_keyword_line().encode('utf-8'), raw)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_refusal_keeps_total_and_digests_for_every_hit_count(self):
        limit = high_debug._SCREEN_HIT_LIMIT
        for source in _SOURCES:
            for count in _HIT_COUNTS:
                with self.subTest(source=source, count=count):
                    task_id, family, text = self._refuse(source, count)
                    expected = self._expected_hits(source, count)
                    record = self._record(task_id, family)
                    self.assertEqual(record['total'], len(expected))
                    self.assertEqual(record['hits'], [list(hit) for hit in expected[:limit]])
                    self.assertEqual(record['omitted'], max(0, len(expected) - limit))
                    self.assertEqual(record['hits_sha256'], _digest(expected))
                    self.assertEqual(record['source_sha256'],
                                     hashlib.sha256(text.encode('utf-8')).hexdigest())
                    self.assertEqual(record['d6'], self.executor)
                    self.assertIsNone(record['disposition'])
                    # No refusal keeps its source, however many hits it has.
                    self._assert_no_refused_text_copy()
                    self.assertEqual(self._history_names(task_id, family),
                                     [record['refusal_id'] + '-refusal.json'])
                    refusal = json.loads((self._history(task_id, family) / (
                        record['refusal_id'] + '-refusal.json')).read_text(encoding='utf-8'))
                    self.assertEqual(refusal['event'], 'REFUSAL')
                    self.assertEqual(refusal['hits_sha256'], record['hits_sha256'])
                    self.assertEqual(refusal['source_sha256'], record['source_sha256'])
                    self._assert_stored_files_bounded_and_text_free(task_id, family)
                    self._assert_blocked(source, task_id, family)

    def test_stored_hits_clear_by_disposition_and_need_no_recovery(self):
        for source in _SOURCES:
            for count in (0, 1, 32):
                with self.subTest(source=source, count=count):
                    task_id, family, _text = self._refuse(source, count)
                    expected = self._expected_hits(source, count)
                    self._assert_refused('HIGH_DEBUG_SCREEN_RECOVERY_NOT_NEEDED',
                                         self._pending, task_id, family)
                    self._assert_refused('HIGH_DEBUG_SCREEN_RECOVERY_NOT_NEEDED',
                                         self._confirm, task_id, family, 1, expected)
                    self._assert_blocked(source, task_id, family)
                    self._dispose(task_id, family, expected)
                    self.assertIsNone(self._gate(source, task_id, family))

    def test_overflow_clears_only_after_every_batch_is_confirmed(self):
        limit = high_debug._SCREEN_HIT_LIMIT
        for source in _SOURCES:
            for count, batches in ((33, 2), (100, 4)):
                with self.subTest(source=source, count=count):
                    self._fresh_store()
                    task_id, family, _text = self._refuse(source, count)
                    expected = self._expected_hits(source, count)
                    self._assert_refused('HIGH_DEBUG_SCREEN_HITS_OMITTED',
                                         self._dispose, task_id, family, expected)
                    confirmed = []
                    for batch in range(1, batches + 1):
                        pending = self._pending(task_id, family)
                        self.assertEqual(
                            (pending['state'], pending['batch'], pending['batches'],
                             pending['confirmed'], pending['total']),
                            ('PENDING', batch, batches, len(confirmed), count))
                        hits = self._parse(pending)
                        self.assertLessEqual(len(hits), limit)
                        self.assertNotIn(_keyword_value(), json.dumps(pending))
                        self._assert_blocked(source, task_id, family)
                        result = self._confirm(task_id, family, batch, hits)
                        confirmed.extend(hits)
                        self.assertEqual(result['confirmed'], len(confirmed))
                        self.assertEqual(result['state'],
                                         'CLEARED' if batch == batches else 'CONFIRMED')
                    self.assertEqual(confirmed, expected)
                    self.assertIsNone(self._gate(source, task_id, family))
                    record = self._record(task_id, family)
                    self.assertEqual(record['disposition']['classification'],
                                     'BATCH_CONFIRMED')
                    self.assertEqual(record['disposition']['ref'], self.ref)
                    self._assert_refused('HIGH_DEBUG_SCREEN_NO_REFUSAL',
                                         self._pending, task_id, family)
                    if source == 'DEVELOPER_OUTPUT':
                        self.assertIsNone(self._dispatch())

    def test_history_stays_readable_and_append_only_after_clearing(self):
        for source in _SOURCES:
            with self.subTest(source=source):
                task_id, family, _text = self._refuse(source, 100)
                prefix = self._record(task_id, family)['refusal_id']
                for _batch in range(4):
                    self._confirm_next(task_id, family)
                self.assertEqual(self._history_names(task_id, family),
                                 [f'{prefix}-b{number:06d}.json' for number in range(1, 5)] +
                                 [prefix + '-refusal.json'])
                events = [json.loads((self._history(task_id, family) / name)
                                     .read_text(encoding='utf-8'))
                          for name in self._history_names(task_id, family)]
                self.assertEqual([event['event'] for event in events],
                                 ['BATCH'] * 4 + ['REFUSAL'])
                self.assertEqual([event['batch'] for event in events[:4]], [1, 2, 3, 4])
                self.assertEqual([hit for event in events[:4] for hit in event['hits']],
                                 [list(hit) for hit in self._expected_hits(source, 100)])
                for event in events:
                    self.assertEqual((event['task_id'], event['family'], event['d6']),
                                     (task_id, family, self.executor))
                self._assert_stored_files_bounded_and_text_free(task_id, family)
                self.assertIsNone(self._gate(source, task_id, family))
                # A cleared record holds only while its history covers every hit.
                (self._history(task_id, family) / f'{prefix}-b000003.json').unlink()
                self._assert_blocked(source, task_id, family)

    def test_partial_confirmation_clears_nothing_and_progress_persists(self):
        for source in _SOURCES:
            with self.subTest(source=source):
                task_id, family, _text = self._refuse(source, 100)
                expected = self._expected_hits(source, 100)
                before = self._record_path(task_id, family).read_bytes()
                for _batch in range(3):
                    self._confirm_next(task_id, family)
                self.assertEqual(self._record_path(task_id, family).read_bytes(), before)
                self.assertIsNone(self._record(task_id, family)['disposition'])
                self._assert_blocked(source, task_id, family)
                self._assert_refused('HIGH_DEBUG_SCREEN_HITS_OMITTED',
                                     self._dispose, task_id, family, expected)
                pending = self._pending(task_id, family)
                self.assertEqual((pending['state'], pending['batch'], pending['confirmed']),
                                 ('PENDING', 4, 96))
                self.assertEqual(self._parse(pending), expected[96:])

    def test_interrupted_final_confirmation_is_completed_by_the_next_read(self):
        for source in _SOURCES:
            with self.subTest(source=source):
                task_id, family, _text = self._refuse(source, 33)
                self._confirm_next(task_id, family)
                hits = self._parse(self._pending(task_id, family))
                with patch.object(high_debug, '_store_disposition',
                                  side_effect=OSError('disk')):
                    with self.assertRaises(OSError):
                        self._confirm(task_id, family, 2, hits)
                self.assertIsNone(self._record(task_id, family)['disposition'])
                self._assert_blocked(source, task_id, family)
                self._assert_refused(_REPEATED, self._confirm, task_id, family, 2, hits)
                self.assertEqual(self._pending(task_id, family)['state'], 'CLEARED')
                self.assertIsNone(self._gate(source, task_id, family))

    def test_wrong_repeated_or_out_of_range_batches_refuse_and_store_nothing(self):
        for source in _SOURCES:
            with self.subTest(source=source):
                task_id, family, _text = self._refuse(source, 100)
                expected = self._expected_hits(source, 100)
                first, second = expected[:32], expected[32:64]
                names = self._history_names(task_id, family)
                for batch, hits, code in (
                        (0, first, _OUT_OF_RANGE), (5, first, _OUT_OF_RANGE),
                        (-1, first, _OUT_OF_RANGE), (True, first, _OUT_OF_RANGE),
                        ('1', first, _OUT_OF_RANGE),
                        (2, second, _WRONG), (1, second, _WRONG),
                        (1, first[:-1], _WRONG), (1, first + second[:1], _WRONG),
                        (1, first + first[:1], _WRONG), (1, [], _WRONG),
                        (1, [('Bad', 6)], _RECOVERY_INVALID),
                        (1, 'keyword_assign:6', _RECOVERY_INVALID)):
                    with self.subTest(batch=batch, hits=len(hits)):
                        self._assert_refused(code, self._confirm, task_id, family, batch, hits)
                for classification, ref in (
                        ('BATCH_CONFIRMED', self.ref), ('UNKNOWN', self.ref),
                        ('SYNTHETIC_TEST_DATA',
                         'https://github.com/example/project/pull/1#issuecomment-1')):
                    self._assert_refused(_RECOVERY_INVALID, self._confirm, task_id, family,
                                         1, first, classification=classification, ref=ref)
                self.assertEqual(self._history_names(task_id, family), names)
                self._confirm(task_id, family, 1, first)
                self._assert_refused(_REPEATED, self._confirm, task_id, family, 1, first)
                self._assert_refused(_REPEATED, self._confirm, task_id, family, 1, second)
                self._assert_refused(_WRONG, self._confirm, task_id, family, 3, expected[64:96])
                self.assertEqual(len(self._history_names(task_id, family)), len(names) + 1)
                pending = self._pending(task_id, family)
                self.assertEqual((pending['batch'], pending['confirmed']), (2, 32))
                self._assert_blocked(source, task_id, family)

    def test_tampered_or_missing_evidence_refuses_with_a_named_code(self):
        def edit_record(**changes):
            def tamper(_source, task_id, family):
                self._write_record(task_id, family,
                                   dict(self._record(task_id, family), **changes))
            return tamper

        def change_source(_source, task_id, _family):
            path = self._original(task_id)
            path.write_bytes(path.read_bytes() + b'\n')

        def remove_source(_source, task_id, _family):
            self._original(task_id).unlink()

        def other_source_with_its_digest(source, task_id, family):
            # Both digests agree with the new source, but it holds other hits.
            raw = _source_text(source, 34).encode('utf-8')
            self._original(task_id).write_bytes(raw)
            edit_record(source_sha256=hashlib.sha256(raw).hexdigest())(
                source, task_id, family)

        def remove_refusal_event(_source, task_id, family):
            prefix = self._record(task_id, family)['refusal_id']
            (self._history(task_id, family) / (prefix + '-refusal.json')).unlink()

        def remove_history(_source, task_id, family):
            history = self._history(task_id, family)
            for path in history.iterdir():
                path.unlink()
            history.rmdir()

        def edit_batch(_source, task_id, family):
            self._confirm_next(task_id, family)
            prefix = self._record(task_id, family)['refusal_id']
            path = self._history(task_id, family) / (prefix + '-b000001.json')
            event = json.loads(path.read_text(encoding='utf-8'))
            event['hits'][0][1] += 1000
            path.write_text(json.dumps(event), encoding='utf-8')

        def remove_record(_source, task_id, family):
            self._record_path(task_id, family).unlink()

        cases = (
            ('source changed', change_source, 'HIGH_DEBUG_SCREEN_SOURCE_MISMATCH'),
            ('source missing', remove_source, 'HIGH_DEBUG_SCREEN_SOURCE_MISSING'),
            ('other source', other_source_with_its_digest, 'HIGH_DEBUG_SCREEN_HITS_MISMATCH'),
            ('hit digest', edit_record(hits_sha256='0' * 64), 'HIGH_DEBUG_SCREEN_HITS_MISMATCH'),
            ('source digest', edit_record(source_sha256='0' * 64),
             'HIGH_DEBUG_SCREEN_SOURCE_MISMATCH'),
            ('no source digest', edit_record(source_sha256=None),
             'HIGH_DEBUG_SCREEN_SOURCE_MISSING'),
            ('refusal id', edit_record(refusal_id='0' * 16), 'HIGH_DEBUG_SCREEN_HISTORY_INVALID'),
            ('hidden overflow', edit_record(omitted=0, total=32),
             'HIGH_DEBUG_SCREEN_RECORD_INVALID'),
            ('total', edit_record(total=34), 'HIGH_DEBUG_SCREEN_RECORD_INVALID'),
            ('refusal event', remove_refusal_event, 'HIGH_DEBUG_SCREEN_HISTORY_INVALID'),
            ('history', remove_history, 'HIGH_DEBUG_SCREEN_HISTORY_INVALID'),
            ('batch edited', edit_batch, 'HIGH_DEBUG_SCREEN_HISTORY_INVALID'),
            ('record deleted', remove_record, 'HIGH_DEBUG_SCREEN_RECORD_INVALID'),
        )
        for source in _SOURCES:
            for name, tamper, code in cases:
                with self.subTest(source=source, case=name):
                    task_id, family, _text = self._refuse(source, 33)
                    first = self._expected_hits(source, 33)[:32]
                    tamper(source, task_id, family)
                    self._assert_refused(code, self._pending, task_id, family)
                    self._assert_refused(code, self._confirm, task_id, family, 1, first)
                    self._assert_blocked(source, task_id, family)

    def test_source_too_large_to_reread_refuses(self):
        for source in _SOURCES:
            with self.subTest(source=source):
                task_id, family, _text = self._refuse(source, 33)
                with patch.object(high_debug, '_SCREEN_SOURCE_LIMIT', 16):
                    self._assert_refused('HIGH_DEBUG_SCREEN_SOURCE_MISMATCH',
                                         self._pending, task_id, family)
                self._assert_blocked(source, task_id, family)

    def test_recovery_creates_no_copy_of_the_refused_text(self):
        for source in _SOURCES:
            with self.subTest(source=source):
                self._fresh_store()
                task_id, family, text = self._refuse(source, 100)
                self._assert_no_refused_text_copy()
                before = self._state_files()
                prefix = self._record(task_id, family)['refusal_id']
                for _batch in range(4):
                    self._confirm_next(task_id, family)
                    self._assert_no_refused_text_copy()
                # The batch events are the only new files, and they are text-free.
                self.assertEqual(self._state_files() - before, {
                    self._history(task_id, family) / f'{prefix}-b{number:06d}.json'
                    for number in range(1, 5)})
                self.assertEqual(self._original(task_id).read_text(encoding='utf-8'), text)
                self.assertIsNone(self._gate(source, task_id, family))

    def test_missing_original_source_refuses_recovery(self):
        for source in _SOURCES:
            with self.subTest(source=source):
                task_id, family, _text = self._refuse(source, 65)
                second = self._expected_hits(source, 65)[32:64]
                self._confirm_next(task_id, family)
                names = self._history_names(task_id, family)
                self._original(task_id).unlink()
                self._assert_refused(_SOURCE_MISSING, self._pending, task_id, family)
                self._assert_refused(_SOURCE_MISSING, self._confirm, task_id, family, 2, second)
                self.assertEqual(self._history_names(task_id, family), names)
                self.assertIsNone(self._record(task_id, family)['disposition'])
                self._assert_blocked(source, task_id, family)

    def test_evidence_and_report_recovery_must_name_the_source(self):
        for source in ('EVIDENCE', 'REPORT'):
            with self.subTest(source=source):
                task_id, family, _text = self._refuse(source, 33)
                self._assert_refused(_SOURCE_MISSING, high_debug.screen_recovery_pending,
                                     task_id, family, self.executor, root=self.store.root)
                self._assert_blocked(source, task_id, family)

    def test_developer_output_is_reread_from_its_d6_only(self):
        task_id, family, _text = self._refuse('DEVELOPER_OUTPUT', 33)
        self.assertEqual(self._pending(task_id, family)['state'], 'PENDING')
        submission_cli.task_completion._worktree_diff.assert_called_with(
            str(self._original(task_id)), _BASE_SHA)
        self._assert_refused(_RECOVERY_INVALID, high_debug.screen_recovery_pending,
                             task_id, family, self.executor, root=self.store.root,
                             source=str(self._original(task_id)))
        # A D6 with no diff (a text route never wrote one) has no source.
        self._original(task_id).write_bytes(b'')
        self._assert_refused(_SOURCE_MISSING, self._pending, task_id, family)
        self._assert_blocked('DEVELOPER_OUTPUT', task_id, family)

    def test_legacy_record_without_digests_stays_blocked(self):
        limit = high_debug._SCREEN_HIT_LIMIT
        for source in _SOURCES:
            for count in (1, 33):
                with self.subTest(source=source, count=count):
                    task_id, family, _text = self._refuse(source, count)
                    expected = self._expected_hits(source, count)
                    record = self._record(task_id, family)
                    for key in ('total', 'hits_sha256', 'source_sha256', 'd6', 'refusal_id'):
                        del record[key]
                    self._write_record(task_id, family, record)
                    self._assert_refused('HIGH_DEBUG_SCREEN_RECOVERY_LEGACY',
                                         self._pending, task_id, family)
                    self._assert_refused('HIGH_DEBUG_SCREEN_RECOVERY_LEGACY',
                                         self._confirm, task_id, family, 1, expected[:limit])
                    self._assert_blocked(source, task_id, family)
                    if count > limit:
                        self._assert_refused('HIGH_DEBUG_SCREEN_HITS_OMITTED',
                                             self._dispose, task_id, family, expected[:limit])
                        self._assert_blocked(source, task_id, family)

    def test_recovery_refuses_another_d6_family_or_task(self):
        for source in _SOURCES:
            with self.subTest(source=source):
                task_id, family, _text = self._refuse(source, 33)
                first = self._expected_hits(source, 33)[:32]
                names = self._history_names(task_id, family)
                for call_task, call_family, d6, code in (
                        (task_id, family, 'executor-d6-2', 'HIGH_DEBUG_SCREEN_RECOVERY_BINDING'),
                        (task_id, family, '../executor-d6-1', _RECOVERY_INVALID),
                        (task_id, 'other-family', self.executor, 'HIGH_DEBUG_SCREEN_NO_REFUSAL'),
                        (self._task(), family, self.executor, 'HIGH_DEBUG_SCREEN_NO_REFUSAL')):
                    with self.subTest(task=call_task, family=call_family, d6=d6):
                        self._assert_refused(code, self._pending, call_task, call_family, d6=d6)
                        self._assert_refused(code, self._confirm, call_task, call_family,
                                             1, first, d6=d6)
                # Rebinding the record to another D6 does not rebind its history.
                self._write_record(task_id, family,
                                   dict(self._record(task_id, family), d6='executor-d6-2'))
                for call in (lambda: self._pending(task_id, family, d6='executor-d6-2'),
                             lambda: self._confirm(task_id, family, 1, first,
                                                   d6='executor-d6-2')):
                    self._assert_refused('HIGH_DEBUG_SCREEN_HISTORY_INVALID', call)
                self.assertEqual(self._history_names(task_id, family), names)
                self._assert_blocked(source, task_id, family)

    def test_record_without_d6_cannot_be_recovered(self):
        for source in _SOURCES:
            with self.subTest(source=source):
                task_id = self._task()
                raw = _source_text(source, 33).encode('utf-8')
                high_debug._write_screen_record(
                    task_id, 'family', high_debug._SCREEN_SOURCE_CODES[source], source,
                    self._expected_hits(source, 33), len(raw), root=self.store.root,
                    screened=raw)
                self.assertIsNone(self._record(task_id, 'family')['d6'])
                self._assert_refused('HIGH_DEBUG_SCREEN_RECOVERY_BINDING',
                                     self._pending, task_id, 'family')

    def test_new_refusal_restarts_recovery_and_keeps_old_history(self):
        for source in _SOURCES:
            with self.subTest(source=source):
                task_id, family, _text = self._refuse(source, 33)
                first = self._record(task_id, family)['refusal_id']
                self._confirm_next(task_id, family)
                self._refuse(source, 33, task_id=task_id)
                second = self._record(task_id, family)['refusal_id']
                self.assertNotEqual(first, second)
                pending = self._pending(task_id, family)
                self.assertEqual((pending['batch'], pending['confirmed']), (1, 0))
                self.assertEqual(self._history_names(task_id, family), sorted(
                    (first + '-b000001.json', first + '-refusal.json',
                     second + '-refusal.json')))
                self._assert_blocked(source, task_id, family)

    def test_record_stays_within_its_size_limit_for_many_hits(self):
        task_id = self._task()
        hits = [('url_userinfo', 10 ** 12 + line) for line in range(5000)]
        high_debug._write_screen_record(
            task_id, 'family', 'HIGH_DEBUG_EVIDENCE_SENSITIVE', 'EVIDENCE', hits, 1,
            root=self.store.root, screened=b'plain', d6=self.executor)
        record = high_debug._read_screen_record(task_id, 'family', self.store.root)
        self.assertEqual((record['total'], record['omitted'], len(record['hits'])),
                         (5000, 5000 - high_debug._SCREEN_HIT_LIMIT,
                          high_debug._SCREEN_HIT_LIMIT))
        self.assertEqual(record['hits_sha256'], _digest(hits))
        self._assert_stored_files_bounded_and_text_free(task_id, 'family')


if __name__ == '__main__':
    unittest.main()
