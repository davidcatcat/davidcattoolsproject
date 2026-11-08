"""gw-agent session home sweep (#120 G1c). Synthetic data only; no gateway or provider call."""

import copy
import fcntl
import io
import json
import os
from argparse import Namespace
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from llmtool import gw_agent_sessions, task_advance, task_completion, task_submission_cli
from llmtool.errors import LLMToolError

import review_trust_fixture


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


DAY = 24 * 60 * 60
NOW = 2_000_000_000.0
SESSION = '0123456789abcdef0123456789abcdef'
DIGEST = 'a' * 64
HISTORY_TEXT = 'synthetic conversation text for a sweep test'
CLOSED = {'task_id': 'root-closed', 'completion_events': [
    {'source': 'GITHUB', 'marker_type': 'merge', 'marker_status': 'MERGED'}]}
ABANDONED = {'task_id': 'root-abandoned', 'abandonment': {'reason': 'synthetic'}}
OPEN = {'task_id': 'root-open', 'state': 'PM_PENDING'}
MISSING = object()  # make_home: leave session_id out of binding.json


class FakeStore:
    def __init__(self, root, records):
        self.root = str(root)
        self.records = records
        self.reads = []

    def task_ids(self):
        return sorted(self.records)

    def get(self, task_id):
        self.reads.append(task_id)
        if task_id not in self.records:
            raise KeyError(task_id)
        return copy.deepcopy(self.records[task_id])


class FakeRunner:
    def __init__(self, result=0):
        self.result = result
        self.calls = []

    def __call__(self, argv, *, env, timeout, cwd):
        self.calls.append({'argv': list(argv), 'env': dict(env), 'timeout': timeout,
                           'cwd': cwd})
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class SweepTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.tmp = Path(self._temp.name).resolve()
        self.root = self.tmp / 'tasks'
        self.root.mkdir(mode=0o700)
        self.base = self.tmp / gw_agent_sessions.SESSIONS_DIR_NAME
        self.base.mkdir(mode=0o700)
        self.package = self.tmp / 'gateway-package'
        (self.package / 'api_agent_gateway').mkdir(mode=0o700, parents=True)
        os.chmod(self.package, 0o700)
        self.records = {'root-closed': CLOSED, 'root-abandoned': ABANDONED,
                        'root-open': OPEN}
        self.store = FakeStore(self.root, self.records)

    def make_home(self, executor, root_task, *, age=None, binding=True, history=True,
                  session_id=SESSION):
        home = self.base / executor
        home.mkdir(mode=0o700)
        if binding:
            value = {'schema_version': 1, 'root_task_id': root_task,
                     'executor_task_id': executor, 'session_id': session_id,
                     'data_class': 'synthetic', 'scope_digest': DIGEST}
            if session_id is MISSING:
                del value['session_id']
            self.write(home / 'binding.json', json.dumps(value))
        self.write(home / 'lock', '')
        session = home / 'sessions' / SESSION
        session.mkdir(mode=0o700, parents=True)
        self.write(session / 'meta.json', json.dumps({'session_id': SESSION}))
        if history:
            self.write(session / 'history.json', json.dumps({'text': HISTORY_TEXT}))
        if age is not None:
            self.set_age(home, age)
        return home

    @staticmethod
    def write(path, text):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)

    @staticmethod
    def set_age(home, age):
        stamp = NOW - age
        for current, dirs, files in os.walk(home, topdown=False):
            for name in files + dirs:
                os.utime(Path(current) / name, (stamp, stamp), follow_symlinks=False)
        os.utime(home, (stamp, stamp), follow_symlinks=False)

    def sweep(self, runner=None):
        runner = runner or FakeRunner()
        return gw_agent_sessions.sweep(self.store, now=NOW, runner=runner,
                                       package_path=str(self.package)), runner

    def test_closed_task_ends_session_and_removes_home(self):
        home = self.make_home('executor-closed', 'root-closed')
        results, runner = self.sweep()
        self.assertEqual(results, [{'executor_task_id': 'executor-closed',
                                    'outcome': gw_agent_sessions.ENDED}])
        self.assertFalse(os.path.lexists(home))
        self.assertEqual(len(runner.calls), 1)
        call = runner.calls[0]
        self.assertEqual(call['argv'], [
            sys.executable, '-B', '-P', '-m', 'api_agent_gateway', 'end', SESSION,
            '--reason', 'task_terminal', '--state-dir', str(home)])
        self.assertEqual(call['timeout'], 30)

    def test_abandoned_task_removes_home(self):
        home = self.make_home('executor-abandoned', 'root-abandoned')
        results, runner = self.sweep()
        self.assertEqual(results, [{'executor_task_id': 'executor-abandoned',
                                    'outcome': gw_agent_sessions.ENDED}])
        self.assertFalse(os.path.lexists(home))
        self.assertEqual(len(runner.calls), 1)

    def test_open_task_is_kept_at_any_age(self):
        for executor, age in (('executor-new', 0), ('executor-old', 365 * DAY)):
            self.make_home(executor, 'root-open', age=age)
        results, runner = self.sweep()
        self.assertEqual(sorted(item['outcome'] for item in results),
                         [gw_agent_sessions.SKIPPED_OPEN] * 2)
        self.assertEqual(runner.calls, [])
        for executor in ('executor-new', 'executor-old'):
            history = self.base / executor / 'sessions' / SESSION / 'history.json'
            self.assertTrue(history.is_file())

    def test_unreadable_or_unknown_task_is_kept(self):
        self.records['root-unknown'] = {'task_id': 'root-unknown', 'state': 'NOT_A_STATE'}
        self.make_home('executor-unknown', 'root-unknown', age=365 * DAY)

        class UnreadableStore(FakeStore):
            def get(self, task_id):
                raise OSError('synthetic unreadable record')

        results, runner = self.sweep()
        self.assertEqual(results, [{'executor_task_id': 'executor-unknown',
                                    'outcome': gw_agent_sessions.SKIPPED_OPEN}])
        self.assertEqual(runner.calls, [])
        # The record says closed, but the store cannot read it: the home stays.
        self.make_home('executor-unreadable', 'root-closed', age=365 * DAY)
        self.store = UnreadableStore(self.root, self.records)
        results, runner = self.sweep()
        self.assertEqual(sorted(item['outcome'] for item in results),
                         [gw_agent_sessions.SKIPPED_OPEN] * 2)
        self.assertEqual(runner.calls, [])
        self.assertTrue((self.base / 'executor-unreadable').is_dir())

    def test_busy_lock_skips_home(self):
        home = self.make_home('executor-closed', 'root-closed')
        fd = os.open(home / 'lock', os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            results, runner = self.sweep()
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        self.assertEqual(results, [{'executor_task_id': 'executor-closed',
                                    'outcome': gw_agent_sessions.SKIPPED_LOCKED}])
        self.assertEqual(runner.calls, [])
        self.assertTrue((home / 'sessions' / SESSION / 'history.json').is_file())

    def test_orphan_older_than_ttl_is_removed_and_newer_is_kept(self):
        old_invalid = self.make_home('executor-old-invalid', 'root-open', binding=False,
                                     age=8 * DAY)
        old_missing = self.make_home('executor-old-missing', 'root-gone', age=8 * DAY)
        new_invalid = self.make_home('executor-new-invalid', 'root-open', binding=False,
                                     age=6 * DAY)
        new_missing = self.make_home('executor-new-missing', 'root-gone', age=6 * DAY)
        results, runner = self.sweep()
        outcomes = {item['executor_task_id']: item['outcome'] for item in results}
        self.assertEqual(outcomes, {
            'executor-old-invalid': gw_agent_sessions.ORPHAN_REMOVED,
            'executor-old-missing': gw_agent_sessions.ORPHAN_REMOVED,
            'executor-new-invalid': gw_agent_sessions.SKIPPED_OPEN,
            'executor-new-missing': gw_agent_sessions.SKIPPED_OPEN})
        self.assertFalse(os.path.lexists(old_invalid))
        self.assertFalse(os.path.lexists(old_missing))
        self.assertTrue(new_invalid.is_dir())
        self.assertTrue(new_missing.is_dir())
        self.assertEqual(runner.calls, [])

    def test_orphan_with_one_recent_file_is_kept(self):
        home = self.make_home('executor-orphan', 'root-gone', age=30 * DAY)
        recent = NOW - DAY
        os.utime(home / 'sessions' / SESSION / 'history.json', (recent, recent))
        results, _ = self.sweep()
        self.assertEqual(results, [{'executor_task_id': 'executor-orphan',
                                    'outcome': gw_agent_sessions.SKIPPED_OPEN}])
        self.assertTrue(home.is_dir())

    def test_terminal_task_with_null_session_id_is_an_orphan_never_ended_early(self):
        young = self.make_home('executor-null-young', 'root-closed', session_id=None,
                               age=DAY)
        old = self.make_home('executor-null-old', 'root-closed', session_id=None,
                             age=8 * DAY)
        results, runner = self.sweep()
        outcomes = {item['executor_task_id']: item['outcome'] for item in results}
        self.assertEqual(outcomes, {
            'executor-null-young': gw_agent_sessions.SKIPPED_OPEN,
            'executor-null-old': gw_agent_sessions.ORPHAN_REMOVED})
        self.assertEqual(runner.calls, [])
        self.assertTrue((young / 'sessions' / SESSION / 'history.json').is_file())
        self.assertFalse(os.path.lexists(old))

    def test_missing_or_malformed_session_id_is_an_orphan(self):
        for index, session_id in enumerate((MISSING, '', 'not-a-session-id',
                                            SESSION.upper(), SESSION + '0', 42)):
            with self.subTest(index=index):
                executor = f'executor-bad-session-{index}'
                home = self.make_home(executor, 'root-closed', session_id=session_id,
                                      age=DAY)
                results, runner = self.sweep()
                self.assertIn({'executor_task_id': executor,
                               'outcome': gw_agent_sessions.SKIPPED_OPEN}, results)
                self.assertNotIn(gw_agent_sessions.ENDED,
                                 [item['outcome'] for item in results])
                self.assertEqual(runner.calls, [])
                self.assertTrue(home.is_dir())

    def test_unknown_task_list_never_makes_a_bound_home_an_orphan(self):
        class NoListStore(FakeStore):
            def task_ids(self):
                raise OSError('synthetic listing failure')

        self.store = NoListStore(self.root, self.records)
        home = self.make_home('executor-missing', 'root-gone', age=30 * DAY)
        results, _ = self.sweep()
        self.assertEqual(results, [{'executor_task_id': 'executor-missing',
                                    'outcome': gw_agent_sessions.SKIPPED_OPEN}])
        self.assertTrue(home.is_dir())

    def test_end_failure_or_timeout_still_removes_history(self):
        failures = (1, subprocess.TimeoutExpired(['gateway'], 30), OSError('synthetic'))
        for index, failure in enumerate(failures):
            with self.subTest(failure=type(failure).__name__):
                executor = f'executor-closed-{index}'
                home = self.make_home(executor, 'root-closed')
                results, runner = self.sweep(FakeRunner(failure))
                self.assertEqual(results, [{
                    'executor_task_id': executor,
                    'outcome': gw_agent_sessions.REMOVED_AFTER_END_FAILURE}])
                self.assertFalse(os.path.lexists(home))
                self.assertEqual(len(runner.calls), 1)

    def test_unsafe_package_is_an_end_failure_and_never_runs(self):
        home = self.make_home('executor-closed', 'root-closed')
        os.chmod(self.package, 0o777)
        try:
            results, runner = self.sweep()
        finally:
            os.chmod(self.package, 0o700)
        self.assertEqual(results, [{'executor_task_id': 'executor-closed',
                                    'outcome': gw_agent_sessions.REMOVED_AFTER_END_FAILURE}])
        self.assertEqual(runner.calls, [])
        self.assertFalse(os.path.lexists(home))

    def test_symlinked_home_is_unlinked_as_a_link(self):
        outside = self.tmp / 'outside-home'
        outside.mkdir(mode=0o700)
        self.write(outside / 'history.json', HISTORY_TEXT)
        link = self.base / 'executor-link'
        link.symlink_to(outside, target_is_directory=True)
        os.utime(link, (NOW - 8 * DAY, NOW - 8 * DAY), follow_symlinks=False)
        results, runner = self.sweep()
        self.assertEqual(results, [{'executor_task_id': 'executor-link',
                                    'outcome': gw_agent_sessions.ORPHAN_REMOVED}])
        self.assertFalse(os.path.lexists(link))
        self.assertEqual((outside / 'history.json').read_text(encoding='utf-8'), HISTORY_TEXT)
        self.assertEqual(runner.calls, [])

    def test_young_symlinked_home_is_kept(self):
        outside = self.tmp / 'outside-home'
        outside.mkdir(mode=0o700)
        link = self.base / 'executor-link'
        link.symlink_to(outside, target_is_directory=True)
        os.utime(link, (NOW, NOW), follow_symlinks=False)
        results, _ = self.sweep()
        self.assertEqual(results, [{'executor_task_id': 'executor-link',
                                    'outcome': gw_agent_sessions.SKIPPED_OPEN}])
        self.assertTrue(os.path.islink(link))

    def test_symlinked_history_and_session_dir_are_not_followed(self):
        outside_file = self.tmp / 'outside-history.json'
        self.write(outside_file, HISTORY_TEXT)
        outside_dir = self.tmp / 'outside-session'
        outside_dir.mkdir(mode=0o700)
        self.write(outside_dir / 'history.json', HISTORY_TEXT)
        home = self.make_home('executor-closed', 'root-closed', history=False)
        (home / 'sessions' / SESSION / 'history.json').symlink_to(outside_file)
        (home / 'sessions' / ('f' * 32)).symlink_to(outside_dir, target_is_directory=True)
        results, _ = self.sweep(FakeRunner(1))
        self.assertEqual(results, [{'executor_task_id': 'executor-closed',
                                    'outcome': gw_agent_sessions.REMOVED_AFTER_END_FAILURE}])
        self.assertFalse(os.path.lexists(home))
        self.assertEqual(outside_file.read_text(encoding='utf-8'), HISTORY_TEXT)
        self.assertEqual((outside_dir / 'history.json').read_text(encoding='utf-8'),
                         HISTORY_TEXT)

    def test_symlinked_binding_makes_an_orphan_and_target_survives(self):
        outside = self.tmp / 'outside-binding.json'
        self.write(outside, json.dumps({
            'schema_version': 1, 'root_task_id': 'root-closed',
            'executor_task_id': 'executor-linked', 'session_id': SESSION,
            'data_class': 'synthetic', 'scope_digest': DIGEST}))
        home = self.make_home('executor-linked', 'root-closed', binding=False)
        (home / 'binding.json').symlink_to(outside)
        self.set_age(home, DAY)  # a young orphan: the closed Task behind the link is ignored
        results, runner = self.sweep()
        self.assertEqual(results, [{'executor_task_id': 'executor-linked',
                                    'outcome': gw_agent_sessions.SKIPPED_OPEN}])
        self.assertEqual(runner.calls, [])
        self.assertTrue(outside.is_file())

    def test_symlinked_sessions_root_is_never_followed(self):
        os.rmdir(self.base)
        outside = self.tmp / 'outside-sessions'
        outside.mkdir(mode=0o700)
        (outside / 'executor-closed').mkdir(mode=0o700)
        self.base.symlink_to(outside, target_is_directory=True)
        results, runner = self.sweep()
        self.assertEqual(results, [])
        self.assertTrue((outside / 'executor-closed').is_dir())
        self.assertEqual(runner.calls, [])

    def test_gateway_env_holds_no_credential(self):
        # The parent environment is replaced: the full one, then one without HOME
        # and LANG. Both must give exactly the four keys and no credential variable.
        environments = (
            {'PATH': '/usr/bin:/bin', 'HOME': str(self.tmp), 'LANG': 'C',
             'LLMTOOL_SWEEP_SYNTHETIC_MARKER': 'marker'},
            {'PATH': '/usr/bin:/bin', 'LLMTOOL_SWEEP_SYNTHETIC_MARKER': 'marker'},
        )
        for index, environment in enumerate(environments):
            with self.subTest(index=index):
                self.make_home(f'executor-closed-{index}', 'root-closed')
                with patch.dict(os.environ, environment, clear=True):
                    _, runner = self.sweep()
                self.assertEqual(len(runner.calls), 1)
                env = runner.calls[0]['env']
                self.assertEqual(set(env), {'PATH', 'HOME', 'LANG', 'PYTHONPATH'})
                self.assertEqual(env['PYTHONPATH'], str(self.package))
                self.assertEqual(env['PATH'], '/usr/bin:/bin')
                self.assertTrue(os.path.isabs(env['HOME']))
                self.assertTrue(env['LANG'])
                self.assertNotIn('LLMTOOL_SWEEP_SYNTHETIC_MARKER', env)
                self.assertNotIn('GW_AGENT_API_KEY', env)
                for key in env:
                    for word in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'CREDENTIAL', 'AUTH'):
                        self.assertNotIn(word, key.upper())
                if 'HOME' in environment:
                    self.assertEqual(env['HOME'], str(self.tmp))
                    self.assertEqual(env['LANG'], 'C')

    def test_results_hold_no_session_content(self):
        self.make_home('executor-closed', 'root-closed')
        self.make_home('executor-open', 'root-open')
        results, _ = self.sweep()
        for item in results:
            self.assertEqual(set(item), {'executor_task_id', 'outcome'})
        self.assertNotIn(HISTORY_TEXT, json.dumps(results))
        self.assertNotIn(SESSION, json.dumps(results))

    def test_non_home_entries_are_untouched(self):
        self.write(self.base / 'stray-file', 'synthetic')
        (self.base / '.hidden').mkdir(mode=0o700)
        results, _ = self.sweep()
        self.assertEqual(results, [])
        self.assertTrue((self.base / 'stray-file').is_file())
        self.assertTrue((self.base / '.hidden').is_dir())

    def test_retired_or_missing_d6_ends_an_open_task(self):
        self.records['root-open'] = dict(OPEN, completion={
            'executor_task_id': 'executor-d6',
            'evidence': {'common_dir': '/synthetic/repo/.git'}})
        error = LLMToolError('synthetic')
        error.completion_code = 'D6_ROOT_MISSING'
        self.make_home('executor-d6', 'root-open')
        with patch.object(task_completion, 'read_d6_registration', side_effect=error):
            results, _ = self.sweep()
        self.assertEqual(results, [{'executor_task_id': 'executor-d6',
                                    'outcome': gw_agent_sessions.ENDED}])

        self.make_home('executor-d6', 'root-open')
        retired = type('Registration', (), {'state': 'RETIRED'})()
        with patch.object(task_completion, 'read_d6_registration', return_value=retired):
            results, _ = self.sweep()
        self.assertEqual(results[0]['outcome'], gw_agent_sessions.ENDED)

    def test_live_or_unreadable_d6_keeps_an_open_task(self):
        self.records['root-open'] = dict(OPEN, completion={
            'executor_task_id': 'executor-d6',
            'evidence': {'common_dir': '/synthetic/repo/.git'}})
        home = self.make_home('executor-d6', 'root-open', age=365 * DAY)
        active = type('Registration', (), {'state': 'ACTIVE'})()
        invalid = LLMToolError('synthetic')
        invalid.completion_code = 'D6_REGISTRY_INVALID'
        unavailable = LLMToolError('synthetic')
        unavailable.completion_code = 'D6_REGISTRY_UNAVAILABLE'
        for kwargs in ({'return_value': active}, {'side_effect': invalid},
                       {'side_effect': unavailable}):
            with patch.object(task_completion, 'read_d6_registration', **kwargs):
                results, runner = self.sweep()
            self.assertEqual(results, [{'executor_task_id': 'executor-d6',
                                        'outcome': gw_agent_sessions.SKIPPED_OPEN}])
            self.assertEqual(runner.calls, [])
        self.assertTrue(home.is_dir())

    def test_sweep_never_raises(self):
        class Broken:
            @property
            def root(self):
                raise RuntimeError('synthetic')

        self.assertEqual(gw_agent_sessions.sweep(Broken()), [])
        self.assertEqual(gw_agent_sessions.sweep(object()), [])

    def test_missing_sessions_dir_is_a_no_op(self):
        os.rmdir(self.base)
        results, runner = self.sweep()
        self.assertEqual(results, [])
        self.assertEqual(runner.calls, [])


class HookTests(unittest.TestCase):
    def test_task_advance_sweeps_only_after_closed_and_keeps_result(self):
        closed = {'task_id': 't1', 'step': 'CLOSED', 'result': None,
                  'next': 'CLOSED', 'codes': [], 'acked': []}
        store = object()
        with patch.object(task_advance, '_advance_step', return_value=dict(closed)), \
             patch.object(gw_agent_sessions, 'sweep',
                          side_effect=RuntimeError('synthetic')) as sweep:
            result = task_advance.advance(task_id='t1', store=store)
        self.assertEqual(result, closed)
        sweep.assert_called_once_with(store)

        waiting = dict(closed, step='WAITING_CI', next='WAITING_CI')
        with patch.object(task_advance, '_advance_step', return_value=dict(waiting)), \
             patch.object(gw_agent_sessions, 'sweep') as sweep:
            self.assertEqual(task_advance.advance(task_id='t1', store=store), waiting)
        sweep.assert_not_called()

    def test_task_advance_refusal_path_is_unchanged_and_does_not_sweep(self):
        with patch.object(gw_agent_sessions, 'sweep') as sweep:
            result = task_advance.advance(task_id='t1', owner_acceptance='synthetic-ref')
        self.assertEqual(result['step'], 'REFUSED')
        self.assertEqual(result['codes'], ['OWNER_ACCEPTANCE_REQUIRES_MERGE'])
        sweep.assert_not_called()

    def test_task_abandon_sweeps_after_save_and_keeps_output(self):
        events = []

        class Store:
            def abandon(self, task_id, *, pm_session_id, reason):
                events.append('abandon')
                return {'task_id': task_id, 'abandonment': {'reason': reason}}

        store = Store()
        args = Namespace(task_id='t1', pm_session='pm-synthetic', reason='synthetic reason')

        def run(sweep_effect):
            stdout = io.StringIO()
            with patch.object(task_submission_cli.active_pm_inbox, 'pm_session_key',
                              return_value='pm-synthetic'), \
                 patch.object(gw_agent_sessions, 'sweep', side_effect=sweep_effect) as sweep:
                code = task_submission_cli.run_task_abandon(args, store=store, stdout=stdout)
            return code, stdout.getvalue(), sweep

        def record_sweep(_store):
            events.append('sweep')
            return []

        baseline = run(record_sweep)
        self.assertEqual(events, ['abandon', 'sweep'])
        failing = run(RuntimeError('synthetic'))
        self.assertEqual(failing[0], 0)
        self.assertEqual(failing[0:2], baseline[0:2])
        failing[2].assert_called_once_with(store)

    def test_gw_agent_delegate_sweeps_once_before_reading_task(self):
        events = []

        class Store:
            def get(self, _task_id):
                events.append('get')
                raise LLMToolError('synthetic task read stop')

        def failing_sweep(_store):
            events.append('sweep')
            raise RuntimeError('synthetic')

        args = Namespace(task_id='a' * 32, mode='direct', context_stdin=False,
                         egress_basis='Synthetic test context.', data_class='SYNTHETIC')
        with patch.object(gw_agent_sessions, 'sweep', side_effect=failing_sweep) as sweep:
            with self.assertRaisesRegex(LLMToolError, 'synthetic task read stop'):
                task_submission_cli.run_task_gw_agent_delegate(args, store=Store())
        self.assertEqual(events, ['sweep', 'get'])
        sweep.assert_called_once()


if __name__ == '__main__':
    unittest.main()
