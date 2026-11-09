"""Developer output refusal flow and rerun refusal, with a mocked screen (Owner #763 A).

No test value here looks like a credential. The diff is plain code that the
real screen passes, and a mock reports the one screen hit.
"""

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import high_debug
from llmtool import task_submission_cli as submission_cli
from llmtool.errors import LLMToolError
from llmtool.task_store import new_id

import review_trust_fixture


def setUpModule():
    # Fixture trust, so no route entry stops with TRUST_MISSING before its own
    # duplicate check on a host without local trust state.
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


_EXECUTOR = 'executor-d6-1'
_ADDED = 'colour = lantern'
_DIFF = (
    'diff --git a/example.py b/example.py\n'
    '--- a/example.py\n'
    '+++ b/example.py\n'
    '@@ -1 +1 @@\n'
    '-colour = None\n'
    '+' + _ADDED + '\n'
)
# Output line 6 of _DIFF is its only added line.
_HIT_LINE = 6
_REF = 'https://github.com/example/project/issues/763#issuecomment-1'
_DUPLICATE = 'developer assignment already exists; duplicate dispatch refused'


class _Store:
    """The Task store surface the rejection writer and the delegate gate use."""

    def __init__(self, root):
        self.root = root
        self.records = {}

    def _ensure_root(self):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def task_ids(self):
        return sorted(self.records)

    def completion_d6_task_ids(self, executor_task_id):
        return sorted(
            task_id for task_id, record in self.records.items()
            if type(record.get('completion')) is dict and
            record['completion'].get('executor_task_id') == executor_task_id)

    def get(self, task_id):
        return copy.deepcopy(self.records[task_id])

    def update(self, task_id, **changes):
        self.records[task_id].update(copy.deepcopy(changes))
        return self.get(task_id)


class RefusalFlowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = _Store(Path(temp.name).resolve() / 'tasks')

    def _task(self):
        task_id = new_id()
        self.store.records[task_id] = {
            'task_id': task_id,
            'developer': {'state': 'DEVELOPER_DISPATCHING', 'artifact': None,
                          'artifact_sha256': None, 'error_code': None,
                          'created_at': '2026-11-08T00:00:00Z'},
            'completion': {'executor_task_id': _EXECUTOR}}
        return task_id

    def _dispatch(self):
        return submission_cli._check_delegate_repair_cap(
            Namespace(task_id=self._task(), executor_task=_EXECUTOR), self.store)

    def test_plain_diff_passes_the_real_screen(self):
        self.assertIsNone(
            submission_cli._strict_developer_output_rejection(_DIFF, ['example.py']))

    def test_mocked_hit_is_refused_recorded_and_gates_the_next_dispatch(self):
        task_id = self._task()
        with patch.object(submission_cli, 'developer_diff_sensitive',
                          return_value=True) as diff_screen, \
                patch.object(high_debug, '_screen_hits',
                             return_value=[('keyword_assign', 1)]) as record_screen:
            self.assertEqual(
                submission_cli._strict_developer_output_rejection(_DIFF, ['example.py']),
                'DEVELOPER_OUTPUT_SENSITIVE')
            submission_cli._persist_developer_output_rejection(
                self.store, task_id, self.store.get(task_id)['developer'],
                'DEVELOPER_OUTPUT_SENSITIVE', None, _DIFF)
        self.assertEqual(diff_screen.call_args.args[:2], (_DIFF, _ADDED))
        record_screen.assert_called_once_with(_ADDED)

        path = high_debug._screen_location(task_id, 'developer-output', self.store.root)
        raw = path.read_text(encoding='utf-8')
        record = json.loads(raw)
        self.assertEqual((record['code'], record['source']),
                         ('DEVELOPER_OUTPUT_SENSITIVE', 'DEVELOPER_OUTPUT'))
        self.assertEqual(record['hits'], [['keyword_assign', _HIT_LINE]])
        self.assertEqual((record['total'], record['omitted']), (1, 0))
        self.assertEqual(record['d6'], _EXECUTOR)
        self.assertEqual(record['source_sha256'],
                         hashlib.sha256(_DIFF.encode('utf-8')).hexdigest())
        self.assertIsNone(record['disposition'])
        task = self.store.get(task_id)
        self.assertEqual(task['developer']['state'], 'DEVELOPER_OUTPUT_REJECTED')
        self.assertEqual(task['developer']['error_code'], 'DEVELOPER_OUTPUT_SENSITIVE')
        for text in (raw, json.dumps(task)):
            self.assertNotIn('lantern', text)
            self.assertNotIn('colour', text)

        with self.assertRaises(LLMToolError) as raised:
            self._dispatch()
        self.assertEqual(
            str(raised.exception),
            'DELEGATE_SCREEN_DISPOSITION_REQUIRED: Task ' + task_id +
            ' family=developer-output rules=keyword_assign lines=' + str(_HIT_LINE))

        high_debug.record_screen_disposition(
            task_id, 'developer-output', [('keyword_assign', _HIT_LINE)],
            'FALSE_POSITIVE', _REF, root=self.store.root)
        self.assertIsNone(self._dispatch())


class _RouteStore:
    """Holds one Task and records every write attempt."""

    def __init__(self, task):
        self.task = copy.deepcopy(task)
        self.writes = []

    def get(self, task_id):
        if task_id != self.task['task_id']:
            raise AssertionError('unexpected Task read')
        return copy.deepcopy(self.task)

    def _write(self, name, *_args, **_kwargs):
        self.writes.append(name)
        return copy.deepcopy(self.task)

    def update(self, *args, **kwargs):
        return self._write('update', *args, **kwargs)

    def compare_and_update(self, *args, **kwargs):
        return self._write('compare_and_update', *args, **kwargs)

    def d6_bind_compare_and_update(self, *args, **kwargs):
        return self._write('d6_bind_compare_and_update', *args, **kwargs)

    def create(self, *args, **kwargs):
        return self._write('create', *args, **kwargs)

    def record_repair_cap_override(self, *args, **kwargs):
        return self._write('record_repair_cap_override', *args, **kwargs)


class RefusedTaskRerunTests(unittest.TestCase):
    task_id = 'a' * 32
    head = 'd' * 40
    acceptance = ['record the bounded result']

    def _task(self, profile):
        refused = {
            'provider_profile': profile, 'state': 'DEVELOPER_OUTPUT_REJECTED',
            'error_code': 'DEVELOPER_OUTPUT_SENSITIVE', 'artifact': None,
            'artifact_sha256': None, 'usage': None, 'source_sha': self.head,
            'executor_task_id': _EXECUTOR, 'read_paths': ['src.py'],
            'write_paths': ['src.py'], 'acceptance': list(self.acceptance),
            'created_at': '2026-11-08T00:00:00Z'}
        return {
            'schema_version': 2, 'task_id': self.task_id, 'run_id': 'b' * 32,
            'state': 'PM_RESPONDED', 'response': 'PM response',
            'request': 'Bounded request', 'task_binding': {'contract_digest': 'c' * 64},
            'pm_assignment': {'route': profile, 'risk': 'LOW', 'goal': 'Bounded goal',
                              'acceptance': list(self.acceptance)},
            'developer': refused, 'completion': {'executor_task_id': _EXECUTOR}}

    def _args(self, provider, mode):
        return Namespace(
            task_id=self.task_id, provider=provider, mode=mode, background=False,
            owner_ref='https://github.com/davidcatcat/davidcattoolsproject/issues/289'
                      '#issuecomment-1',
            context_stdin=False, context='Bounded Task context.', selection_source='PM',
            acceptance=list(self.acceptance),
            egress_basis='Approved non-sensitive Task context.', data_class='INTERNAL',
            executor_task=_EXECUTOR, read_path=['src.py'], write_path=['src.py'],
            repair_packet=None)

    def _assert_refused(self, call, store):
        before = copy.deepcopy(store.task)
        provider_guard = Mock(side_effect=AssertionError('provider call'))
        with patch.object(submission_cli, 'resolve_profile_config', provider_guard), \
                patch.object(submission_cli.codex_direct_developer, 'run', provider_guard), \
                patch.object(submission_cli.opencode_direct_developer, 'run', provider_guard):
            with self.assertRaises(LLMToolError) as raised:
                call()
        self.assertEqual(str(raised.exception), _DUPLICATE)
        provider_guard.assert_not_called()
        self.assertEqual(store.writes, [])
        self.assertEqual(store.task, before)

    def test_every_route_entry_refuses_a_refused_task(self):
        def runner_route(entry):
            def call(store, args, runner):
                return entry(args, store=store, route_runner=runner)
            return call

        routes = (
            ('claude', 'claude', 'direct', runner_route(submission_cli.run_task_claude_delegate)),
            ('agy', 'agy', 'direct', runner_route(submission_cli.run_task_agy_delegate)),
            ('gw-agent', 'gw-agent', 'direct',
             runner_route(submission_cli.run_task_gw_agent_delegate)),
            ('opencode', 'opencode', 'proposal',
             lambda store, args, _runner: submission_cli.run_task_opencode_delegate(
                 args, store=store)),
            ('company-gw', 'company-gw', 'proposal',
             lambda store, args, _runner: submission_cli.run_task_delegate(
                 args, store=store)),
        )
        for name, profile, mode, call in routes:
            with self.subTest(route=name):
                store = _RouteStore(self._task(profile))
                args = self._args(profile, mode)
                runner = Mock(side_effect=AssertionError('route call'))
                self._assert_refused(lambda: call(store, args, runner), store)
                runner.assert_not_called()

    def test_codex_direct_does_not_take_a_refused_record_as_timeout_recovery(self):
        store = _RouteStore(self._task('codex'))
        args = self._args('codex', 'direct')
        registration = SimpleNamespace(
            task_id=_EXECUTOR, trusted_agent_id='codex-developer', root='/registered/root',
            source_sha=self.head, result_sha=self.head)
        snapshot = SimpleNamespace(head=self.head)
        with patch.object(submission_cli, '_delegate_d6_binding',
                          return_value=(registration, snapshot)), \
                patch.object(submission_cli.task_completion, 'validate_task_binding'), \
                patch.object(submission_cli, '_codex_timeout_recovery',
                             wraps=submission_cli._codex_timeout_recovery) as recovery:
            self._assert_refused(
                lambda: submission_cli.run_task_codex_delegate(args, store=store), store)
        recovery.assert_called_once()
        self.assertEqual(recovery.call_args.args[0]['error_code'],
                         'DEVELOPER_OUTPUT_SENSITIVE')


if __name__ == '__main__':
    unittest.main()
