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
PM = 'pm-reservation-test'
REQUEST = 'Bounded AI Debug reservation test Task.'
REASON = 'Synthetic early reason.'
RULES = ('RULES stub\nRULES_END\n\n', 'c' * 64)
REPORT = ''.join('## ' + heading + '\nDetails.\n'
                 for heading in high_debug.REPORT_SECTIONS) + 'RCA_CONFIRMED'


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def entry(status='RESERVED', report=None, d6='executor-hd1-aaaaaaa'):
    """One high_debug entry bound to this module's request; status as given."""
    item = high_debug._failed_entry(
        family='pr-17', failed_corrections=None, early_reason=REASON, head=HEAD, d6=d6,
        code=report or ('HIGH_DEBUG_RESERVED' if status == 'RESERVED' else 'HIGH_DEBUG_RUN_FAILED'),
        observed='UNKNOWN', effort='UNKNOWN', usage='UNKNOWN', recorded_at=AT,
        model_requested=high_debug._CODEX_MODEL, rules_sha256=RULES[1])
    item['status'] = status
    return item


QUOTA = ('RUN_FAILED', 'HIGH_DEBUG_QUOTA_EXHAUSTED')


class Crash(BaseException):
    pass


class ReservationCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.store = TaskStore(self.base / 'tasks')
        for name, value in (('_debug_rules', RULES),):
            patcher = patch.object(high_debug, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(high_debug.task_store, 'default_root',
                               return_value=self.base / 'tasks')
        patcher.start()
        self.addCleanup(patcher.stop)

    def task(self, continues=None, completion=False):
        task = cli._record(new_id(), new_id(), PM, 'pm-profile', REQUEST,
                           state='PM_RESPONDED', provider_type='openai-compatible',
                           model='pm-model', backend='NO_NATIVE_SESSION_ID_EXPOSED',
                           request_sha=hashlib.sha256(REQUEST.encode()).hexdigest())
        task.update(pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                                      'base_url': None}, response='PM reply.')
        if completion:
            assignment = {'schema_version': 1, 'route': 'codex', 'risk': 'LOW',
                          'goal': 'Test the AI Debug reservation.',
                          'acceptance': ['dispatch is bounded']}
            task.update(
                response=json.dumps({'schema_version': 1, 'summary': 'Fixture response.',
                                     'developer_assignment': assignment}),
                pm_assignment=assignment,
                completion={'state': 'WAITING_EXECUTOR', 'executor_task_id': 'executor',
                            'reviewer_task_id': None,
                            'evidence': {'common_dir': str(self.base / 'repo' / '.git')},
                            'missing_gates': [], 'pm_dispatch': 'NOT_STARTED',
                            'pm_response': None, 'pm_recommendation': None,
                            'pm_usage': None, 'pm_error_code': None})
        if continues is not None:
            task['continues'] = continues
        self.store.create(task)
        return task['task_id']

    def split(self, count=2):
        """A ROOT and its committed, OPEN split children (Owner B flow)."""
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
        return root, children

    def entries(self, task_id, store=None):
        return (store or self.store).get(task_id).get('high_debug', [])

    def reserve(self, task_id, store=None):
        store = store or self.store
        return high_debug._reserve_debug(store, task_id, self.entries(task_id, store), entry())

    @staticmethod
    def code(call):
        try:
            call()
        except LLMToolError as error:
            return (getattr(error, 'high_debug_code', None) or
                    getattr(error, 'task_store_error_code', None))
        return None

    def race(self, first, second):
        """Reserve on two Tasks at once; an unlocked count would let both pass."""
        barrier = threading.Barrier(2, timeout=1)
        real = lineage_counts.chain_debug_used
        results = []

        def counted(*args):
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass   # the timeout is ignored: a locked count proceeds alone
            return real(*args)

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

    def run_debug(self, task_id, runner, *, create=None, fallback=False, store=None):
        """Drive run_high_debug on the real store; (refusal code or None, D6 creates)."""
        registration = SimpleNamespace(result_sha=HEAD, root=str(self.base / 'executor-d6'),
                                       common_dir=str(self.base / 'repo' / '.git'))
        store = store or self.store
        created = []

        def default_create(**kwargs):
            created.append([item['status'] for item in self.entries(task_id, store)])
            return str(self.base)
        git = SimpleNamespace(run=lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=b''),
                              DEVNULL=subprocess.DEVNULL, PIPE=subprocess.PIPE)
        call = high_debug.run_high_debug_with_fallback if fallback else high_debug.run_high_debug
        with patch.object(high_debug.worker_dispatch, 'evaluate',
                          return_value=SimpleNamespace(allowed=True)), \
             patch.object(high_debug.task_completion, 'collect_git_snapshot',
                          return_value=SimpleNamespace(head=HEAD)), \
             patch.object(high_debug.role_config, 'resolve_role', return_value={
                 'role': 'claude-debug', 'source': 'roles', 'account': 'acct',
                 'config_dir': '/safe/claude', 'provider': 'claude'}), \
             patch.object(high_debug, '_d6_taken', return_value=False), \
             patch.object(high_debug, '_post_record', return_value=True), \
             patch.object(high_debug, 'subprocess', git), \
             redirect_stdout(io.StringIO()):
            try:
                call(store=store, task_id=task_id, executor_task='executor', pr_number=17,
                     family='pr-17', early_reason=REASON, runner=runner,
                     create_d6=create or default_create, repo_root=str(self.base / 'repo'),
                     read_registration=lambda *_args, **_kwargs: registration)
            except LLMToolError as error:
                self.last_error = error
                return error.high_debug_code, created
        return None, created


def succeeds(*, prompt, cwd, output_path):
    Path(output_path).write_text(REPORT, encoding='utf-8')
    return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')


def exits(code, stderr=b''):
    return lambda **_kwargs: SimpleNamespace(returncode=code, stdout=b'', stderr=stderr)


class AtomicAdmissionTests(ReservationCase):
    def test_a_two_chain_tasks_race_for_the_last_slot_and_one_wins(self):
        first = self.task()
        second = self.task(continues=first)
        self.assertEqual(self.race(first, second), [0, 'HIGH_DEBUG_DEBUG_PARENT_USED'])
        self.assertEqual([item['status'] for task_id in (first, second)
                          for item in self.entries(task_id)], ['RESERVED'])

    def test_b_a_chain_and_a_split_group_share_one_cap(self):
        first = self.task()
        second = self.task(continues=first)
        third = self.task(continues=second)
        self.assertEqual(self.reserve(second), 0)
        for task_id in (first, third):
            self.assertEqual(self.code(lambda: self.reserve(task_id)),
                             'HIGH_DEBUG_DEBUG_PARENT_USED')
            self.assertEqual(self.entries(task_id), [])
        _root, (one, two) = self.split()
        self.assertEqual(self.reserve(one), 0)
        self.assertEqual(self.code(lambda: self.reserve(two)), 'HIGH_DEBUG_DEBUG_GROUP_USED')
        self.assertEqual(self.entries(two), [])

    def test_c_a_used_chain_refuses_with_no_new_entry(self):
        first = self.task()
        second = self.task(continues=first)
        self.store.append_high_debug(first, entry('RCA_CONFIRMED'))
        for task_id in (first, second):
            self.assertEqual(self.code(lambda: self.reserve(task_id)),
                             'HIGH_DEBUG_DEBUG_PARENT_USED')
        self.assertEqual((len(self.entries(first)), self.entries(second)), (1, []))

    def test_e_the_same_request_twice_and_concurrently_leaves_one_entry(self):
        task_id = self.task()
        self.assertEqual(high_debug._reserve_debug(self.store, task_id, [], entry()), 0)
        self.assertEqual(self.code(lambda: high_debug._reserve_debug(
            self.store, task_id, [], entry())), 'HIGH_DEBUG_BUSY')
        self.assertEqual(self.code(lambda: self.reserve(task_id)), 'HIGH_DEBUG_DEBUG_PARENT_USED')
        self.assertEqual(len(self.entries(task_id)), 1)
        other = self.task()
        winner, loser = self.race(other, other)
        self.assertEqual(winner, 0)
        self.assertIn(loser, ('HIGH_DEBUG_BUSY', 'HIGH_DEBUG_DEBUG_PARENT_USED'))
        self.assertEqual([item['status'] for item in self.entries(other)], ['RESERVED'])

    def test_f_a_held_chain_lock_blocks_only_its_own_chain(self):
        held, other = self.task(), self.task()
        with patch.object(task_store_module, 'LINEAGE_LOCK_TIMEOUT_SECONDS', 0.2), \
             self.store.chain_lock(held):
            self.assertEqual(self.reserve(other), 0)
            self.assertEqual(self.code(lambda: self.reserve(held)),
                             'HIGH_DEBUG_CHAIN_LOCK_UNAVAILABLE')
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
                first = self.task()
                second = self.task(continues=first)
                spent, asking = (first, second) if spent_on_first else (second, first)
                self.store.append_high_debug(spent, entry('RCA_CONFIRMED'))
                self.assertEqual(self.code(lambda: self.reserve(asking)),
                                 'HIGH_DEBUG_DEBUG_PARENT_USED')
        orphan = self.task(continues=new_id())
        self.assertEqual(lineage_counts.chain_debug_used(
            self.store, self.store.get(orphan), [], high_debug._debug_entry_used),
            (UNKNOWN, UNKNOWN))
        self.assertEqual(self.code(lambda: self.reserve(orphan)), 'HIGH_DEBUG_DEBUG_PARENT_USED')
        self.assertEqual(self.entries(orphan), [])


class FinalizeTests(ReservationCase):
    def test_h_finalize_refuses_everything_but_one_in_place_finalize(self):
        task_id = self.task()
        self.assertEqual(self.reserve(task_id), 0)
        for changed in (dict(entry('RUN_FAILED'), d6='executor-hd9-aaaaaaa'),
                        dict(entry('RUN_FAILED'), early_reason='Another reason.'),
                        entry()):
            with self.subTest(changed=changed['status']):
                self.assertEqual(self.code(lambda: self.store.finalize_high_debug(
                    task_id, 0, changed)), 'HIGH_DEBUG_IMMUTABLE')
        self.store.finalize_high_debug(task_id, 0, entry('RUN_FAILED'))
        self.assertEqual(self.code(lambda: self.store.finalize_high_debug(
            task_id, 0, entry('RCA_CONFIRMED'))), 'HIGH_DEBUG_IMMUTABLE')
        other = self.task()
        self.store.append_high_debug(other, entry(*QUOTA))
        self.assertEqual(self.reserve(other), 1)
        for index in (0, 2, -1):
            with self.subTest(index=index):
                self.assertEqual(self.code(lambda: self.store.finalize_high_debug(
                    other, index, entry('RUN_FAILED'))), 'HIGH_DEBUG_IMMUTABLE')
        self.assertEqual([item['status'] for item in self.entries(other)],
                         ['RUN_FAILED', 'RESERVED'])


class RunTests(ReservationCase):
    def test_reservation_is_stored_before_create_and_finalized_in_place(self):
        task_id = self.task(completion=True)
        self.store.append_high_debug(task_id, entry(*QUOTA))
        code, created = self.run_debug(task_id, succeeds)
        self.assertIsNone(code)
        self.assertEqual(created, [['RUN_FAILED', 'RESERVED']])
        stored = self.entries(task_id)
        self.assertEqual([item['status'] for item in stored], ['RUN_FAILED', 'RCA_CONFIRMED'])
        self.assertEqual(stored[1]['d6'], high_debug._d6_name('executor', 2, HEAD))

    def test_d_a_timeout_finalizes_used_and_the_next_request_refuses(self):
        task_id = self.task(completion=True)

        def times_out(**_kwargs):
            raise subprocess.TimeoutExpired('codex', 1800)
        self.assertEqual(self.run_debug(task_id, times_out)[0], 'HIGH_DEBUG_RUN_FAILED')
        self.assertEqual([(item['status'], item['report']) for item in self.entries(task_id)],
                         [('RUN_FAILED', 'HIGH_DEBUG_RUN_FAILED')])
        runner = Mock()
        self.assertEqual(self.run_debug(task_id, runner),
                         ('HIGH_DEBUG_DEBUG_PARENT_USED', []))
        runner.assert_not_called()

    def test_d_a_crash_leaves_reserved_across_a_restart(self):
        task_id = self.task(completion=True)

        def crashes(**_kwargs):
            raise Crash()
        with self.assertRaises(Crash):
            self.run_debug(task_id, crashes)
        restarted = TaskStore(self.base / 'tasks')
        self.assertEqual([item['status'] for item in self.entries(task_id, restarted)],
                         ['RESERVED'])
        self.assertEqual(self.run_debug(task_id, Mock(), store=restarted),
                         ('HIGH_DEBUG_DEBUG_PARENT_USED', []))
        self.assertEqual(self.code(lambda: self.reserve(task_id, restarted)),
                         'HIGH_DEBUG_DEBUG_PARENT_USED')

    def test_d_a_failed_finalize_keeps_reserved_and_the_original_code(self):
        task_id = self.task(completion=True)
        with patch.object(TaskStore, 'finalize_high_debug', side_effect=LLMToolError('refused')):
            self.assertEqual(self.run_debug(task_id, exits(1))[0], 'HIGH_DEBUG_RUN_FAILED')
        self.assertIn('reservation=UNFINALIZED', self.last_error.high_debug_detail)
        self.assertEqual([item['status'] for item in self.entries(task_id)], ['RESERVED'])

    def test_g_a_failed_create_is_not_used_and_quota_falls_back_once(self):
        task_id = self.task(completion=True)

        def broken(**_kwargs):
            raise subprocess.CalledProcessError(1, 'lifecycle')
        runner = Mock()
        self.assertEqual(self.run_debug(task_id, runner, create=broken)[0],
                         'HIGH_DEBUG_D6_CREATE_FAILED')
        runner.assert_not_called()
        self.assertEqual([(item['status'], item['report']) for item in self.entries(task_id)],
                         [('RUN_FAILED', 'HIGH_DEBUG_D6_CREATE_FAILED')])
        quota = self.task(completion=True)
        runner = Mock(side_effect=[exits(1, b"You've hit your usage limit.")(),
                                   RuntimeError('claude stops')])
        self.assertEqual(self.run_debug(quota, runner, fallback=True)[0],
                         'HIGH_DEBUG_RUN_FAILED')
        self.assertEqual(runner.call_count, 2)
        stored = self.entries(quota)
        self.assertEqual([(item['status'], item['report']) for item in stored],
                         [QUOTA, ('RUN_FAILED', 'HIGH_DEBUG_RUN_FAILED')])
        self.assertEqual(stored[1]['model_requested'], high_debug._CLAUDE_MODEL)

    def test_g_a_failed_codex_finalize_makes_the_claude_admission_refuse(self):
        task_id = self.task(completion=True)
        runner = Mock(return_value=exits(1, b"You've hit your usage limit.")())
        with patch.object(TaskStore, 'finalize_high_debug', side_effect=LLMToolError('refused')):
            self.assertEqual(self.run_debug(task_id, runner, fallback=True)[0],
                             'HIGH_DEBUG_DEBUG_PARENT_USED')
        self.assertEqual(runner.call_count, 1)
        self.assertEqual([item['status'] for item in self.entries(task_id)], ['RESERVED'])


if __name__ == '__main__':
    unittest.main()
