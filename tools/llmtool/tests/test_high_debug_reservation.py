"""#1502 WP-D O7: atomic AI Debug reservation over the whole lineage."""
import io
import json
import subprocess
import tempfile
import threading
import unittest
from contextlib import redirect_stdout, suppress
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from llmtool import high_debug, lineage_counts
from llmtool.errors import LLMToolError
from llmtool.repair_policy import UNKNOWN
from llmtool.task_store import (TaskStore, commit_split_root, finalize_split_child, new_id,
                                reserve_split_child)

import review_trust_fixture
import test_lineage_counts as lineage_fixture
from test_high_debug import _review_fail_claim

HEAD = 'a' * 40
AT = '2026-11-15T00:00:00Z'
REASON = 'Synthetic early reason.'
RULES = ('RULES stub\nRULES_END\n\n', 'c' * 64)
REPORT = ''.join(f'## {h}\nDetails.\n' for h in high_debug.REPORT_SECTIONS) + 'RCA_CONFIRMED'
QUOTA = ('RUN_FAILED', 'HIGH_DEBUG_QUOTA_EXHAUSTED')
PARENT, CREATE = 'HIGH_DEBUG_DEBUG_PARENT_USED', 'HIGH_DEBUG_D6_CREATE_FAILED'
RUN, IMMUTABLE = 'HIGH_DEBUG_RUN_FAILED', 'HIGH_DEBUG_IMMUTABLE'
MARKER = 'D6 lifecycle rejected (D6_TEST_REJECTED)'
LIMIT = b"You've hit your usage limit."
setUpModule = review_trust_fixture.start_module
tearDownModule = review_trust_fixture.stop_module


def entry(status='RESERVED', report=None):
    code = report or ('HIGH_DEBUG_RESERVED' if status == 'RESERVED' else RUN)
    return dict(high_debug._failed_entry(
        family='pr-17', failed_corrections=None, early_reason=REASON, head=HEAD,
        d6='executor-hd1-aaaaaaa', code=code, observed='UNKNOWN', effort='UNKNOWN',
        usage='UNKNOWN', recorded_at=AT,
        model_requested=high_debug._debug_default_model('codex-debug'),
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
GIT = SimpleNamespace(run=lambda *_a, **_k: SimpleNamespace(returncode=0, stdout=b''),
                      DEVNULL=subprocess.DEVNULL, PIPE=subprocess.PIPE,
                      CalledProcessError=subprocess.CalledProcessError)


class ReservationCase(unittest.TestCase):
    eq = unittest.TestCase.assertEqual

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.store = TaskStore(self.base / 'tasks')
        self._fail_claim_stores = set()
        self._with_fail_claim(self.store)
        self.d6s = self.base / 'd6s'
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

    def _with_fail_claim(self, store):
        """#2602 (a): make a formal FAIL at HEAD visible without rewriting the record."""
        if id(store) in self._fail_claim_stores:
            return store
        self._fail_claim_stores.add(id(store))
        read = store.get

        def get(task_id):
            record = read(task_id)
            if type(record) is dict:
                record = dict(record)
                record.setdefault('review_dispatch_claim', [_review_fail_claim(task_id)])
            return record

        store.get = get
        return store

    def task(self, completion=False, continues=lineage_fixture._NO_LINK):
        task = lineage_fixture.StoreCase.raw(continues)
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
        self.store.create(task)
        return task['task_id']

    def chain(self, count):
        ids = [self.task()]
        for _ in range(count - 1):
            ids.append(self.task(continues=ids[-1]))
        return ids

    def split(self, count=2):
        root, op = self.task(), new_id()
        children = [self.task() for _ in range(count)]

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

    def race(self, *task_ids):
        # An unlocked count would let both pass; the barrier timeout is ignored.
        barrier, results = threading.Barrier(2, timeout=1), []
        real = lineage_counts.chain_debug_used

        def counted(*args):
            used = real(*args)
            with suppress(threading.BrokenBarrierError):
                barrier.wait()
            return used

        def run(task_id):
            try:
                results.append(self.reserve(task_id))
            except LLMToolError as error:
                results.append(error.high_debug_code)
        with patch.object(lineage_counts, 'chain_debug_used', counted):
            threads = [threading.Thread(target=run, args=(i,)) for i in task_ids]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        return sorted(results, key=str)

    def debug(self, task_id, runner, *, create=None, fallback=False, store=None, real=False,
              taken=None):
        store, created = store or self.store, []
        store = self._with_fail_claim(store)
        registration = SimpleNamespace(result_sha=HEAD, root=str(self.base / 'executor-d6'),
                                       common_dir=str(self.base / 'repo' / '.git'))

        def default_create(**_kwargs):
            created.append(self.statuses(task_id, store))
            return str(self.base)
        call = high_debug.run_high_debug_with_fallback if fallback else high_debug.run_high_debug
        with patch.object(high_debug, '_d6_taken', return_value=False, side_effect=taken), \
             patch.object(high_debug, 'subprocess', subprocess if real else GIT), \
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

    def create_fails(self, create, **kwargs):
        task_id, runner = self.task(True), Mock()
        self.eq(self.debug(task_id, runner, create=create, **kwargs)[0], CREATE)
        return task_id, runner

    def spend_two_slots(self, task_id, store=None):
        store = store or self.store
        store.append_high_debug(task_id, entry('RCA_CONFIRMED'))
        store.append_high_debug(task_id, entry('RCA_UNCONFIRMED'))

    def assert_reserved(self, task_id, runner):
        self.eq(self.last_error.high_debug_detail, 'reservation=RESERVED')
        self.eq(self.statuses(task_id), ['RESERVED'])
        self.spend_two_slots(task_id)
        self.eq(self.debug(task_id, runner), (PARENT, []))
        runner.assert_not_called()


class AtomicAdmissionTests(ReservationCase):
    def test_a_chain_race_for_the_last_slot_has_one_winner(self):
        first, second, third, fourth = self.chain(4)
        self.eq(self.reserve(first), 0)
        self.eq(self.reserve(second), 0)
        self.eq(self.race(third, fourth), [0, PARENT])
        self.eq(self.statuses(third) + self.statuses(fourth), ['RESERVED'])

    def test_b_chain_and_split_group_share_a_cap(self):
        first, second, third = self.chain(3)
        for index in range(3):
            self.eq(self.reserve(second), index)
        for task_id in (first, third):
            self.eq(self.refused(task_id), PARENT)
            self.eq(self.entries(task_id), [])
        # Split children share a three-slot limit.
        *within, over = self.split(4)
        for child in within:
            self.eq(self.reserve(child), 0)
        self.eq(self.refused(within[0]), 'HIGH_DEBUG_DEBUG_GROUP_USED')
        self.eq(self.statuses(within[0]), ['RESERVED'])
        self.eq(self.refused(over), 'HIGH_DEBUG_DEBUG_GROUP_USED')
        self.eq(self.entries(over), [])

    def test_c_used_chain_refuses(self):
        first, second = self.chain(2)
        self.store.append_high_debug(first, entry('RCA_CONFIRMED'))
        self.spend_two_slots(first)
        for task_id in (first, second):
            self.eq(self.refused(task_id), PARENT)
        self.eq((len(self.entries(first)), self.entries(second)), (3, []))

    def test_e_repeated_requests_leave_one_entry(self):
        task_id = self.task()
        again = lambda: high_debug._reserve_debug(self.store, task_id, [], entry())  # noqa: E731
        self.eq(again(), 0)
        self.eq(code(again), 'HIGH_DEBUG_BUSY')
        self.eq(len(self.entries(task_id)), 1)
        other = self.task()
        winner, loser = self.race(other, other)
        self.eq(winner, 0)
        self.assertIn(loser, ('HIGH_DEBUG_BUSY', PARENT))
        self.eq(self.statuses(other), ['RESERVED'])

    def test_f_held_lock_blocks_only_its_chain(self):
        held, other = self.task(), self.task()
        with patch.object(high_debug.task_store, 'LINEAGE_LOCK_TIMEOUT_SECONDS', 0.2), \
             self.store.chain_lock(held):
            self.eq(self.reserve(other), 0)
            self.eq(self.refused(held), 'HIGH_DEBUG_CHAIN_LOCK_UNAVAILABLE')
        self.eq(self.entries(held), [])

    def test_i_ported_chain_cases(self):
        alone = self.store.get(self.task())
        for entries, used in (([], 0), ([entry(*QUOTA)], 0), ([entry('RCA_CONFIRMED')], 1),
                              ([entry()], 1), ([entry('RUN_FAILED')], 1)):
            with self.subTest(entries=[item['status'] for item in entries]):
                self.eq(lineage_counts.chain_debug_used(
                    self.store, alone, entries, high_debug._debug_entry_used), (used, used))
        for spent_on_first in (True, False):
            with self.subTest(spent_on_first=spent_on_first):
                pair = self.chain(2)
                spent, asking = pair if spent_on_first else pair[::-1]
                self.store.append_high_debug(spent, entry('RCA_CONFIRMED'))
                self.spend_two_slots(spent)
                self.eq(self.refused(asking), PARENT)
        orphan = self.task(continues=new_id())
        self.eq(lineage_counts.chain_debug_used(
            self.store, self.store.get(orphan), [], high_debug._debug_entry_used),
            (UNKNOWN, UNKNOWN))
        self.eq(self.refused(orphan), 'HIGH_DEBUG_CHAIN_LOCK_UNAVAILABLE')
        self.eq(self.entries(orphan), [])

    def test_h_finalize_only_once_in_place(self):
        task_id, final = self.task(), self.store.finalize_high_debug
        self.eq(self.reserve(task_id), 0)
        failed = entry('RUN_FAILED')
        for changed in (dict(failed, d6='executor-hd9-aaaaaaa'),
                        dict(failed, early_reason='Another reason.'),
                        dict(failed, status='NOT_A_STATUS'), dict(failed, status=None), entry()):
            with self.subTest(changed=changed['status']):
                self.eq(code(lambda: final(task_id, 0, changed)), IMMUTABLE)
        final(task_id, 0, entry('RUN_FAILED'))
        self.eq(code(lambda: final(task_id, 0, entry('RCA_CONFIRMED'))), IMMUTABLE)
        other = self.task()
        self.store.append_high_debug(other, entry(*QUOTA))
        self.eq(self.reserve(other), 1)
        for index in (0, 2, -1):
            with self.subTest(index=index):
                self.eq(code(lambda: final(other, index, entry('RUN_FAILED'))), IMMUTABLE)
        self.eq(self.statuses(other), ['RUN_FAILED', 'RESERVED'])


class RunTests(ReservationCase):
    def test_reserved_before_create_and_finalized_in_place(self):
        task_id = self.task(True)
        self.store.append_high_debug(task_id, entry(*QUOTA))
        self.eq(self.debug(task_id, succeeds), (None, [['RUN_FAILED', 'RESERVED']]))
        self.eq(self.statuses(task_id), ['RUN_FAILED', 'RCA_CONFIRMED'])
        self.eq(self.entries(task_id)[1]['d6'], high_debug._d6_name('executor', 2, HEAD))

    def test_d_timeout_is_used(self):
        task_id, runner = self.task(True), Mock()
        self.eq(self.debug(task_id, raises(subprocess.TimeoutExpired, 'codex', 1800))[0], RUN)
        self.eq(self.pairs(task_id), [('RUN_FAILED', RUN)])
        self.spend_two_slots(task_id)
        self.eq(self.debug(task_id, runner), (PARENT, []))
        runner.assert_not_called()

    def test_d_crash_stays_reserved_after_restart(self):
        task_id = self.task(True)
        with self.assertRaises(Crash):
            self.debug(task_id, raises(Crash))
        restarted = TaskStore(self.base / 'tasks')
        self.eq(self.statuses(task_id, restarted), ['RESERVED'])
        self.spend_two_slots(task_id, restarted)
        self.eq(self.debug(task_id, Mock(), store=restarted), (PARENT, []))
        self.eq(self.refused(task_id, restarted), PARENT)

    def test_d_failed_finalize_keeps_reserved(self):
        task_id = self.task(True)
        with unfinalized():
            self.eq(self.debug(task_id, exits(1))[0], RUN)
        self.assertIn('reservation=UNFINALIZED', self.last_error.high_debug_detail)
        self.eq(self.statuses(task_id), ['RESERVED'])

    def test_g_failed_create_not_used_and_quota_falls_back(self):
        task_id, runner = self.create_fails(rejected)
        runner.assert_not_called()
        self.eq(self.debug(task_id, succeeds), (None, [['RUN_FAILED', 'RESERVED']]))
        self.eq(self.pairs(task_id), [('RUN_FAILED', CREATE), ('RCA_CONFIRMED', REPORT)])
        quota = self.task(True)
        runner = Mock(side_effect=[exits(1, LIMIT)(), RuntimeError('claude stops')])
        self.eq(self.debug(quota, runner, fallback=True)[0], RUN)
        self.eq(runner.call_count, 2)
        self.eq(self.pairs(quota), [QUOTA, ('RUN_FAILED', RUN)])
        self.eq(self.entries(quota)[1]['model_requested'],
                high_debug._debug_default_model('claude-debug'))

    def test_g_unclear_create_stays_reserved(self):
        for case, (create, taken) in enumerate((   # the last leaves a branch or record
                (raises(subprocess.TimeoutExpired, 'lifecycle', 180), None),
                (raises(RuntimeError, 'lifecycle state unknown'), None),
                (lambda **_kwargs: high_debug._refuse(CREATE), None),
                (raises(subprocess.CalledProcessError, 1, 'lifecycle'), None),
                (rejected, [False, True]))):
            with self.subTest(case=case):
                self.assert_reserved(*self.create_fails(create, taken=taken))

    def test_g_real_lifecycle_cancels_only_a_classified_rejection(self):
        (self.base / 'repo').mkdir()
        script = self.base / 'lifecycle.py'
        for name, source, kept in (
                ('killed', 'import os, signal, sys\n'
                           f'sys.stderr.write({MARKER!r} + "\\n"); sys.stderr.flush()\n'
                           'os.kill(os.getpid(), signal.SIGKILL)\n', True),
                ('exit_1', 'import sys\nsys.exit(1)\n', True),
                ('traceback', f'raise RuntimeError({MARKER!r})\n', True),
                ('classified', f'import sys\nsys.exit({MARKER!r})\n', False),
                ('orphan_dir', 'import os, sys\n'   # named, but leaves its D6 directory
                               f'os.makedirs(os.path.join({str(self.d6s)!r}, '
                               'sys.argv[sys.argv.index("--task-id") + 1]))\n'
                               'sys.exit("D6 lifecycle rejected (D6_TASK_ALREADY_EXISTS)")\n',
                 True)):
            with self.subTest(case=name):
                script.write_text(source, encoding='utf-8')
                with patch.object(high_debug, '_LIFECYCLE', script):
                    task_id, runner = self.create_fails(high_debug._create_d6, real=True)
                stored = self.entries(task_id)
                if kept:
                    self.assert_reserved(task_id, runner)
                else:
                    runner.assert_not_called()
                    self.eq(self.pairs(task_id), [('RUN_FAILED', CREATE)])
        self.assertTrue((self.d6s / stored[0]['d6']).is_dir())

    def test_g_cancel_needs_an_absent_d6_on_the_bound_trust_path(self):
        d6, bound = 'executor-hd1-aaaaaaa', high_debug._d6_trust_root()
        absent = high_debug._d6_path_absent
        self.assertTrue(absent(d6, bound))
        (self.d6s / d6).mkdir()
        self.assertFalse(absent(d6, bound))
        (self.d6s / d6).rmdir()
        (self.d6s / d6).symlink_to(self.base / 'missing')
        self.assertFalse(absent(d6, bound))
        self.assertFalse(absent('executor-hd2-aaaaaaa', None))
        for loader in (raises(OSError, 'trust unreadable'),
                       lambda: SimpleNamespace(tasks_root='relative'),
                       lambda: SimpleNamespace(tasks_root=str(self.base / 'none'))):
            with patch.object(high_debug, 'trust', SimpleNamespace(load_trust=loader)):
                self.assertIsNone(high_debug._d6_trust_root())

        def moved(**_kwargs):   # trust moved
            self.d6s = self.base / 'moved'
            self.d6s.mkdir()
            rejected()

        def replaced(**_kwargs):   # same path, another directory
            self.d6s.rename(self.base / 'old')
            self.d6s.mkdir()
            rejected()

        def unreadable(**_kwargs):
            self.d6s = None
            rejected()
        for create in (moved, replaced, unreadable):
            with self.subTest(create=create.__name__):
                self.assert_reserved(*self.create_fails(create))

    def test_g_failed_codex_finalize_refuses_claude(self):
        task_id, runner = self.task(True), Mock(return_value=exits(1, LIMIT)())
        self.spend_two_slots(task_id)
        with unfinalized():
            self.eq(self.debug(task_id, runner, fallback=True)[0], PARENT)
        self.eq(runner.call_count, 1)
        self.eq(self.statuses(task_id), ['RCA_CONFIRMED', 'RCA_UNCONFIRMED', 'RESERVED'])


if __name__ == '__main__':
    unittest.main()
