"""#1502 WP-D O7 R1: AI Debug admission is reserved atomically over the whole lineage."""
import hashlib
import io
import json
import subprocess
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from llmtool import (high_debug, lineage_counts, task_store as task_store_module,
                     task_submission_cli as cli)
from llmtool.errors import LLMToolError
from llmtool.repair_policy import UNKNOWN
from llmtool.task_store import (TaskStore, commit_split_root, finalize_split_child, new_id,
                                reserve_split_child)

import review_trust_fixture

HEAD = 'a' * 40
AT = '2026-11-15T00:00:00Z'
REQUEST = 'Bounded AI Debug reservation test Task.'
REASON = 'Synthetic early reason.'
RULES = ('RULES stub\nRULES_END\n\n', 'c' * 64)
REPORT = ''.join(f'## {h}\nDetails.\n' for h in high_debug.REPORT_SECTIONS) + 'RCA_CONFIRMED'
QUOTA = ('RUN_FAILED', 'HIGH_DEBUG_QUOTA_EXHAUSTED')
PARENT, CREATE = 'HIGH_DEBUG_DEBUG_PARENT_USED', 'HIGH_DEBUG_D6_CREATE_FAILED'
MARKER = 'D6 lifecycle rejected (D6_TEST_REJECTED)'
LIMIT = b"You've hit your usage limit."
setUpModule = review_trust_fixture.start_module
tearDownModule = review_trust_fixture.stop_module


def entry(status='RESERVED', report=None):
    code = report or 'HIGH_DEBUG_' + ('RESERVED' if status == 'RESERVED' else 'RUN_FAILED')
    return dict(high_debug._failed_entry(
        family='pr-17', failed_corrections=None, early_reason=REASON, head=HEAD,
        d6='executor-hd1-aaaaaaa', code=code, observed='UNKNOWN', effort='UNKNOWN',
        usage='UNKNOWN', recorded_at=AT, model_requested=high_debug._CODEX_MODEL,
        rules_sha256=RULES[1]), status=status)


def code(call):
    try:
        call()
    except LLMToolError as error:
        return (getattr(error, 'high_debug_code', None) or
                getattr(error, 'task_store_error_code', None))


def raises(error, *args):
    def call(**_kwargs):
        raise error(*args)
    return call


def unfinalized():
    return patch.object(TaskStore, 'finalize_high_debug', side_effect=LLMToolError('refused'))


class Crash(BaseException):
    pass


def succeeds(*, prompt, cwd, output_path):
    Path(output_path).write_text(REPORT, encoding='utf-8')
    return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')


def exits(code, stderr=b''):
    return lambda **_kwargs: SimpleNamespace(returncode=code, stdout=b'', stderr=stderr)


rejected = raises(high_debug._D6CreateRejected, 'D6_TEST_REJECTED')


class ReservationCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.store = TaskStore(self.base / 'tasks')
        self.d6s = self.base / 'd6s'   # the trusted D6 parent, read at every trust load
        self.d6s.mkdir()
        role = {'role': 'claude-debug', 'source': 'roles', 'account': 'acct',
                'config_dir': '/safe/claude', 'provider': 'claude'}
        for target, name, value in (
                (high_debug, '_debug_rules', RULES),
                (high_debug.task_store, 'default_root', self.base / 'tasks'),
                (high_debug.worker_dispatch, 'evaluate', SimpleNamespace(allowed=True)),
                (high_debug.task_completion, 'collect_git_snapshot', SimpleNamespace(head=HEAD)),
                (high_debug.role_config, 'resolve_role', role),
                (high_debug, '_post_record', True)):
            self.patch(target, name, return_value=value)
        self.patch(high_debug, 'trust', new=SimpleNamespace(
            load_trust=lambda: SimpleNamespace(tasks_root=str(self.d6s))))

    def patch(self, *args, **kwargs):
        patcher = patch.object(*args, **kwargs)
        patcher.start()
        self.addCleanup(patcher.stop)

    def task(self, continues=None, completion=False):
        task = cli._record(new_id(), new_id(), 'pm-reservation-test', 'pm-profile', REQUEST,
                           state='PM_RESPONDED', provider_type='openai-compatible',
                           model='pm-model', backend='NO_NATIVE_SESSION_ID_EXPOSED',
                           request_sha=hashlib.sha256(REQUEST.encode()).hexdigest())
        task.update(pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                                      'base_url': None}, response='PM reply.')
        if completion:
            plan = {'schema_version': 1, 'route': 'codex', 'risk': 'LOW',
                    'goal': 'Test the AI Debug reservation.', 'acceptance': ['dispatch is bounded']}
            unset = dict.fromkeys(('reviewer_task_id', 'pm_response', 'pm_recommendation',
                                   'pm_usage', 'pm_error_code'))
            task.update(
                response=json.dumps({'schema_version': 1, 'summary': 'Fixture response.',
                                     'developer_assignment': plan}),
                pm_assignment=plan,
                completion=dict(unset, state='WAITING_EXECUTOR', executor_task_id='executor',
                                evidence={'common_dir': str(self.base / 'repo' / '.git')},
                                missing_gates=[], pm_dispatch='NOT_STARTED'))
        if continues is not None:
            task['continues'] = continues
        self.store.create(task)
        return task['task_id']

    def chain(self, count):
        ids = [self.task()]
        for _ in range(count - 1):
            ids.append(self.task(continues=ids[-1]))
        return ids

    def split(self):
        root, op = self.task(), new_id()
        children = [self.task(), self.task()]

        def line(kind, state, **extra):
            return dict({'lineage_id': root, 'kind': kind,
                         'parent': None if kind == 'root' else root, 'replaces': [],
                         'state': state, 'reason': None, 'base_sha': None,
                         'recorded_at': AT}, **extra)
        for child in children:
            reserve_split_child(self.store, child, line('child', 'PENDING', split_op=op),
                                expected_sha256=self.store.get(child)['record_sha256'])
        commit_split_root(self.store, root,
                          line('root', 'PARKED', splits=[{'op_id': op, 'children': children}]),
                          child_max=8, expected={'lineage': None})
        for child in children:
            finalize_split_child(self.store, child)
        return children

    def entries(self, task_id, store=None):
        return (store or self.store).get(task_id).get('high_debug', [])

    def statuses(self, task_id, store=None):
        return [item['status'] for item in self.entries(task_id, store)]

    def pairs(self, task_id):
        return [(item['status'], item['report']) for item in self.entries(task_id)]

    def reserve(self, task_id, store=None):
        store = store or self.store
        return high_debug._reserve_debug(store, task_id, self.entries(task_id, store), entry())

    def refused(self, task_id, store=None):
        return code(lambda: self.reserve(task_id, store))

    def race(self, first, second):
        # F2: each count is taken before the barrier and returned after it, so an
        # unlocked count would let both pass; the barrier timeout is ignored.
        barrier = threading.Barrier(2, timeout=1)
        real = lineage_counts.chain_debug_used
        results = []

        def counted(*args):
            used = real(*args)
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass
            return used

        def run(task_id):
            try:
                results.append(self.reserve(task_id))
            except LLMToolError as error:
                results.append(error.high_debug_code)
        with patch.object(lineage_counts, 'chain_debug_used', counted):
            threads = [threading.Thread(target=run, args=(task_id,))
                       for task_id in (first, second)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        return sorted(results, key=str)

    def run_debug(self, task_id, runner, *, create=None, fallback=False, store=None,
                  real=False, taken=None):
        store, created = store or self.store, []
        registration = SimpleNamespace(result_sha=HEAD, root=str(self.base / 'executor-d6'),
                                       common_dir=str(self.base / 'repo' / '.git'))

        def default_create(**_kwargs):
            created.append(self.statuses(task_id, store))
            return str(self.base)
        git = SimpleNamespace(run=lambda *_a, **_k: SimpleNamespace(returncode=0, stdout=b''),
                              DEVNULL=subprocess.DEVNULL, PIPE=subprocess.PIPE,
                              CalledProcessError=subprocess.CalledProcessError)
        call = high_debug.run_high_debug_with_fallback if fallback else high_debug.run_high_debug
        with patch.object(high_debug, '_d6_taken', return_value=False, side_effect=taken), \
             patch.object(high_debug, 'subprocess', subprocess if real else git), \
             redirect_stdout(io.StringIO()):
            try:
                call(store=store, task_id=task_id, executor_task='executor', pr_number=17,
                     family='pr-17', early_reason=REASON, runner=runner,
                     create_d6=create or default_create, repo_root=str(self.base / 'repo'),
                     read_registration=lambda *_a, **_k: registration)
            except LLMToolError as error:
                self.last_error = error
                return error.high_debug_code, created
        return None, created

    def assert_reserved(self, task_id, runner):
        self.assertEqual(self.last_error.high_debug_detail, 'reservation=RESERVED')
        self.assertEqual(self.statuses(task_id), ['RESERVED'])
        self.assertEqual(self.run_debug(task_id, runner), (PARENT, []))
        runner.assert_not_called()


class AtomicAdmissionTests(ReservationCase):
    def test_a_chain_race_for_the_last_slot_has_one_winner(self):
        first, second = self.chain(2)
        self.assertEqual(self.race(first, second), [0, PARENT])
        self.assertEqual(self.statuses(first) + self.statuses(second), ['RESERVED'])

    def test_b_chain_and_split_group_share_a_cap(self):
        first, second, third = self.chain(3)
        self.assertEqual(self.reserve(second), 0)
        for task_id in (first, third):
            self.assertEqual(self.refused(task_id), PARENT)
            self.assertEqual(self.entries(task_id), [])
        one, two = self.split()
        self.assertEqual(self.reserve(one), 0)
        self.assertEqual(self.refused(two), 'HIGH_DEBUG_DEBUG_GROUP_USED')
        self.assertEqual(self.entries(two), [])

    def test_c_used_chain_refuses(self):
        first, second = self.chain(2)
        self.store.append_high_debug(first, entry('RCA_CONFIRMED'))
        for task_id in (first, second):
            self.assertEqual(self.refused(task_id), PARENT)
        self.assertEqual((len(self.entries(first)), self.entries(second)), (1, []))

    def test_e_repeated_requests_leave_one_entry(self):
        task_id = self.task()
        again = lambda: high_debug._reserve_debug(self.store, task_id, [], entry())  # noqa: E731
        self.assertEqual(again(), 0)
        self.assertEqual(code(again), 'HIGH_DEBUG_BUSY')
        self.assertEqual(self.refused(task_id), PARENT)
        self.assertEqual(len(self.entries(task_id)), 1)
        other = self.task()
        winner, loser = self.race(other, other)
        self.assertEqual(winner, 0)
        self.assertIn(loser, ('HIGH_DEBUG_BUSY', PARENT))
        self.assertEqual(self.statuses(other), ['RESERVED'])

    def test_f_held_lock_blocks_only_its_chain(self):
        held, other = self.task(), self.task()
        with patch.object(task_store_module, 'LINEAGE_LOCK_TIMEOUT_SECONDS', 0.2), \
             self.store.chain_lock(held):
            self.assertEqual(self.reserve(other), 0)
            self.assertEqual(self.refused(held), 'HIGH_DEBUG_CHAIN_LOCK_UNAVAILABLE')
        self.assertEqual(self.entries(held), [])

    def test_i_ported_chain_cases(self):
        alone = self.store.get(self.task())
        for entries, used in (([], 0), ([entry(*QUOTA)], 0), ([entry('RCA_CONFIRMED')], 1),
                              ([entry()], 1), ([entry('RUN_FAILED')], 1)):
            with self.subTest(entries=[item['status'] for item in entries]):
                self.assertEqual(lineage_counts.chain_debug_used(
                    self.store, alone, entries, high_debug._debug_entry_used), (used, used))
        for spent_on_first in (True, False):
            with self.subTest(spent_on_first=spent_on_first):
                pair = self.chain(2)
                spent, asking = pair if spent_on_first else pair[::-1]
                self.store.append_high_debug(spent, entry('RCA_CONFIRMED'))
                self.assertEqual(self.refused(asking), PARENT)
        orphan = self.task(continues=new_id())
        self.assertEqual(lineage_counts.chain_debug_used(
            self.store, self.store.get(orphan), [], high_debug._debug_entry_used),
            (UNKNOWN, UNKNOWN))
        self.assertEqual(self.refused(orphan), PARENT)
        self.assertEqual(self.entries(orphan), [])

    def test_h_finalize_only_once_in_place(self):
        task_id, final = self.task(), self.store.finalize_high_debug
        self.assertEqual(self.reserve(task_id), 0)
        failed = entry('RUN_FAILED')
        for changed in (dict(failed, d6='executor-hd9-aaaaaaa'),
                        dict(failed, early_reason='Another reason.'),
                        dict(failed, status='NOT_A_STATUS'), dict(failed, status=None), entry()):
            with self.subTest(changed=changed['status']):
                self.assertEqual(code(lambda: final(task_id, 0, changed)), 'HIGH_DEBUG_IMMUTABLE')
        final(task_id, 0, entry('RUN_FAILED'))
        self.assertEqual(code(lambda: final(task_id, 0, entry('RCA_CONFIRMED'))),
                         'HIGH_DEBUG_IMMUTABLE')
        other = self.task()
        self.store.append_high_debug(other, entry(*QUOTA))
        self.assertEqual(self.reserve(other), 1)
        for index in (0, 2, -1):
            with self.subTest(index=index):
                self.assertEqual(code(lambda: final(other, index, entry('RUN_FAILED'))),
                                 'HIGH_DEBUG_IMMUTABLE')
        self.assertEqual(self.statuses(other), ['RUN_FAILED', 'RESERVED'])


class RunTests(ReservationCase):
    def test_reserved_before_create_and_finalized_in_place(self):
        task_id = self.task(completion=True)
        self.store.append_high_debug(task_id, entry(*QUOTA))
        self.assertEqual(self.run_debug(task_id, succeeds), (None, [['RUN_FAILED', 'RESERVED']]))
        stored = self.entries(task_id)
        self.assertEqual([item['status'] for item in stored], ['RUN_FAILED', 'RCA_CONFIRMED'])
        self.assertEqual(stored[1]['d6'], high_debug._d6_name('executor', 2, HEAD))

    def test_d_timeout_is_used(self):
        task_id, runner = self.task(completion=True), Mock()
        times_out = raises(subprocess.TimeoutExpired, 'codex', 1800)
        self.assertEqual(self.run_debug(task_id, times_out)[0], 'HIGH_DEBUG_RUN_FAILED')
        self.assertEqual(self.pairs(task_id), [('RUN_FAILED', 'HIGH_DEBUG_RUN_FAILED')])
        self.assertEqual(self.run_debug(task_id, runner), (PARENT, []))
        runner.assert_not_called()

    def test_d_crash_stays_reserved_after_restart(self):
        task_id = self.task(completion=True)
        with self.assertRaises(Crash):
            self.run_debug(task_id, raises(Crash))
        restarted = TaskStore(self.base / 'tasks')
        self.assertEqual(self.statuses(task_id, restarted), ['RESERVED'])
        self.assertEqual(self.run_debug(task_id, Mock(), store=restarted), (PARENT, []))
        self.assertEqual(self.refused(task_id, restarted), PARENT)

    def test_d_failed_finalize_keeps_reserved(self):
        task_id = self.task(completion=True)
        with unfinalized():
            self.assertEqual(self.run_debug(task_id, exits(1))[0], 'HIGH_DEBUG_RUN_FAILED')
        self.assertIn('reservation=UNFINALIZED', self.last_error.high_debug_detail)
        self.assertEqual(self.statuses(task_id), ['RESERVED'])

    def test_g_failed_create_not_used_and_quota_falls_back(self):
        task_id, runner = self.task(completion=True), Mock()
        self.assertEqual(self.run_debug(task_id, runner, create=rejected)[0], CREATE)
        runner.assert_not_called()
        self.assertEqual(self.run_debug(task_id, succeeds), (None, [['RUN_FAILED', 'RESERVED']]))
        self.assertEqual(self.pairs(task_id), [('RUN_FAILED', CREATE), ('RCA_CONFIRMED', REPORT)])
        quota = self.task(completion=True)
        runner = Mock(side_effect=[exits(1, LIMIT)(), RuntimeError('claude stops')])
        self.assertEqual(self.run_debug(quota, runner, fallback=True)[0], 'HIGH_DEBUG_RUN_FAILED')
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(self.pairs(quota), [QUOTA, ('RUN_FAILED', 'HIGH_DEBUG_RUN_FAILED')])
        self.assertEqual(self.entries(quota)[1]['model_requested'], high_debug._CLAUDE_MODEL)

    def test_g_unclear_create_stays_reserved(self):
        # The last case leaves a branch or registry record behind.
        for case, (create, taken) in enumerate((
                (raises(subprocess.TimeoutExpired, 'lifecycle', 180), None),
                (raises(RuntimeError, 'lifecycle state unknown'), None),
                (lambda **_kwargs: high_debug._refuse(CREATE), None),
                (raises(subprocess.CalledProcessError, 1, 'lifecycle'), None),
                (rejected, [False, True]))):
            with self.subTest(case=case):
                task_id, runner = self.task(completion=True), Mock()
                self.assertEqual(
                    self.run_debug(task_id, runner, create=create, taken=taken)[0], CREATE)
                self.assert_reserved(task_id, runner)

    def test_g_real_lifecycle_cancels_only_a_classified_rejection(self):
        (self.base / 'repo').mkdir()
        script = self.base / 'lifecycle.py'
        for name, source, status in (
                ('killed', 'import os, signal, sys\n'
                           f'sys.stderr.write({MARKER!r} + "\\n"); sys.stderr.flush()\n'
                           'os.kill(os.getpid(), signal.SIGKILL)\n', 'RESERVED'),
                ('exit_1', 'import sys\nsys.exit(1)\n', 'RESERVED'),
                ('traceback', f'raise RuntimeError({MARKER!r})\n', 'RESERVED'),
                ('classified', f'import sys\nsys.exit({MARKER!r})\n', 'RUN_FAILED'),
                # A named rejection that leaves an orphan D6 directory is not a cancel.
                ('orphan_dir', 'import os, sys\n'
                               f'os.makedirs(os.path.join({str(self.d6s)!r}, '
                               'sys.argv[sys.argv.index("--task-id") + 1]))\n'
                               'sys.exit("D6 lifecycle rejected (D6_TASK_ALREADY_EXISTS)")\n',
                 'RESERVED')):
            with self.subTest(case=name):
                script.write_text(source, encoding='utf-8')
                task_id, runner = self.task(completion=True), Mock()
                with patch.object(high_debug, '_LIFECYCLE', script):
                    self.assertEqual(self.run_debug(task_id, runner, real=True,
                                                    create=high_debug._create_d6)[0], CREATE)
                stored = self.entries(task_id)
                if status == 'RESERVED':
                    self.assert_reserved(task_id, runner)
                else:
                    runner.assert_not_called()
                    self.assertEqual(self.pairs(task_id), [('RUN_FAILED', CREATE)])
        self.assertTrue((self.d6s / stored[0]['d6']).is_dir())

    def test_g_cancel_needs_an_absent_d6_on_the_bound_trust_path(self):
        d6, bound = 'executor-hd1-aaaaaaa', high_debug._d6_trust_root()
        self.assertTrue(high_debug._d6_path_absent(d6, bound))
        (self.d6s / d6).mkdir()
        self.assertFalse(high_debug._d6_path_absent(d6, bound))
        (self.d6s / d6).rmdir()
        (self.d6s / d6).symlink_to(self.base / 'missing')
        self.assertFalse(high_debug._d6_path_absent(d6, bound))
        self.assertFalse(high_debug._d6_path_absent('executor-hd2-aaaaaaa', None))
        for loader in (raises(OSError, 'trust unreadable'),
                       lambda: SimpleNamespace(tasks_root='relative'),
                       lambda: SimpleNamespace(tasks_root=str(self.base / 'none'))):
            with patch.object(high_debug, 'trust', SimpleNamespace(load_trust=loader)):
                self.assertIsNone(high_debug._d6_trust_root())

        def moved(**_kwargs):   # F1: trust names another directory at cancel time
            self.d6s = self.base / 'moved'
            self.d6s.mkdir()
            rejected()

        def replaced(**_kwargs):   # F1: the same path is now another directory
            self.d6s.rename(self.base / 'old')
            self.d6s.mkdir()
            rejected()

        def unreadable(**_kwargs):
            self.d6s = None
            rejected()
        for create in (moved, replaced, unreadable):
            with self.subTest(create=create.__name__):
                task_id, runner = self.task(completion=True), Mock()
                self.assertEqual(self.run_debug(task_id, runner, create=create)[0], CREATE)
                self.assert_reserved(task_id, runner)

    def test_g_failed_codex_finalize_refuses_claude(self):
        task_id = self.task(completion=True)
        runner = Mock(return_value=exits(1, LIMIT)())
        with unfinalized():
            self.assertEqual(self.run_debug(task_id, runner, fallback=True)[0], PARENT)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(self.statuses(task_id), ['RESERVED'])


if __name__ == '__main__':
    unittest.main()
