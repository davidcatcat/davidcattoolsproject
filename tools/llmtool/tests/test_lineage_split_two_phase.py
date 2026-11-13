"""#1502 Owner B (c6860817605, PR #1768): lineage_split prepare/commit fault injection.

Each child is reserved PENDING (bound to the split op id); the ROOT PARKED write
that records the op and its full child list is the single commit point; then the
children become OPEN. Faults are injected through a store wrapper around the
trusted split writes, so no test sleeps and no production check is bypassed.
"""
import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from llmtool import repair_policy
from llmtool import task_store
from llmtool import task_submission_cli as cli
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore, new_id, record_lineage

MAIN = 'e' * 40
PM = 'pm-two-phase-test'
AT = '2026-11-13T00:00:00Z'
REASON = 'Review FAIL 3.'
CLAIM = {'pr': 17, 'head_sha': 'a' * 40, 'state': 'CLAIMED',
         'claimed_at': '2026-11-13T00:00:00.000000Z'}


class Crash(Exception):
    """Stands in for a process that dies at this point: nothing in the split catches it."""


class FaultStore:
    """A real TaskStore whose trusted split writes run fault(action, task_id, phase).

    phase is 'before' (nothing written yet) or 'after' (the write is stored).
    Task IDs in broken fail every get() with TASK_STORE_UNAVAILABLE.
    """

    def __init__(self, real, fault=None):
        self._real, self._fault, self.broken = real, fault, set()

    def __getattr__(self, name):
        return getattr(self._real, name)

    def get(self, task_id):
        if task_id in self.broken:
            task_store._fail('TASK_STORE_UNAVAILABLE')
        return self._real.get(task_id)

    def _update(self, task_id, *, expected, changes, **kwargs):
        action = kwargs.get('trusted_split')
        if self._fault is not None:
            self._fault(action, task_id, 'before')
        result = self._real._update(task_id, expected=expected, changes=changes, **kwargs)
        if self._fault is not None:
            self._fault(action, task_id, 'after')
        return result


def fault_at(action, task_id=None, phase='before', code='TASK_STORE_UNAVAILABLE',
             error=None, then=None):
    """A fault that fires once at (action, task_id, phase)."""
    fired = []

    def fault(seen_action, seen_task, seen_phase):
        if (fired or seen_action != action or seen_phase != phase or
                (task_id is not None and seen_task != task_id)):
            return
        fired.append(True)
        if then is not None:
            then()
        if error is not None:
            raise error
        if code is not None:
            task_store._fail(code)
    return fault


class TwoPhaseCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = TaskStore(Path(temp.name).resolve() / 'tasks')
        self.child_max = repair_policy.load_policy()['child_max']
        self.assertGreaterEqual(self.child_max, 2)
        self.reads = []

    def task(self):
        request = 'Bounded two-phase split test Task.'
        task = cli._record(new_id(), new_id(), PM, 'pm-profile', request,
                           state='PM_RESPONDED', provider_type='openai-compatible',
                           model='pm-model', backend='NO_NATIVE_SESSION_ID_EXPOSED',
                           request_sha=hashlib.sha256(request.encode()).hexdigest())
        task.update(pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                                      'base_url': None}, response='PM reply.')
        self.store.create(task)
        return task['task_id']

    def main_sha(self):
        self.reads.append(True)
        return MAIN

    def split(self, root, children, store=None):
        return cli.lineage_split(self.store if store is None else store, root,
                                 list(children), pm_session=PM, reason=REASON,
                                 main_sha_reader=self.main_sha)

    def refused(self, code, call):
        with self.assertRaises(LLMToolError) as caught:
            call()
        self.assertEqual(getattr(caught.exception, 'task_store_error_code', None) or
                         str(caught.exception), code)

    def digests(self, *task_ids):
        return {task_id: self.store.get(task_id)['record_sha256'] for task_id in task_ids}

    def line(self, task_id):
        return self.store.get(task_id).get('lineage')

    def state(self, task_id):
        return self.line(task_id)['state']

    def assert_unchanged(self, digests):
        self.assertEqual(self.digests(*digests), digests)

    def assert_no_lineage(self, *task_ids):
        for task_id in task_ids:
            self.assertNotIn('lineage', self.store.get(task_id))

    def assert_unusable(self, child):
        """The central gate refuses every write but the split's own FINALIZE/RELEASE."""
        line = self.line(child)
        self.assertEqual(line['state'], 'PENDING')
        for write in (lambda: self.store.update(child, merge_attempt=CLAIM),
                      lambda: self.store.compare_and_update(
                          child, expected={'record_sha256':
                                           self.store.get(child)['record_sha256']},
                          merge_attempt=CLAIM),
                      lambda: record_lineage(self.store, child,
                                             dict(line, state='STOPPED', reason='x')),
                      lambda: self.store.append_dispatch_audit(child, {'decision': 'ALLOWED'})):
            self.refused('TASK_LINEAGE_PENDING', write)
        self.assertEqual(self.line(child), line)

    def reservation(self, root, op):
        return {'lineage_id': root, 'kind': 'child', 'parent': root, 'replaces': [],
                'state': 'PENDING', 'reason': None, 'base_sha': MAIN, 'recorded_at': AT,
                'split_op': op}

    def root_commit(self, root, prior, op, children):
        base = prior or {'lineage_id': root, 'kind': 'root', 'parent': None, 'replaces': [],
                         'state': 'OPEN', 'reason': None, 'base_sha': None,
                         'recorded_at': AT}
        return dict(base, state='PARKED', reason='split', splits=list(
            base.get('splits', [])) + [{'op_id': op, 'children': list(children)}])


class FailBeforeFirstChildTests(TwoPhaseCase):
    def test_fail_before_child_1_writes_nothing(self):
        root, first, second = self.task(), self.task(), self.task()
        before = self.digests(root, first, second)
        store = FaultStore(self.store, fault_at('RESERVE', first))
        self.refused('TASK_STORE_UNAVAILABLE', lambda: self.split(root, [first, second], store))
        self.assert_unchanged(before)
        self.assert_no_lineage(root, first, second)


class SecondChildFailsTests(TwoPhaseCase):
    def test_child_1_ok_child_2_fails_leaves_no_partial_lineage(self):
        root, first, second = self.task(), self.task(), self.task()
        before = self.digests(root, second)
        store = FaultStore(self.store, fault_at('RESERVE', second))
        self.refused('TASK_STORE_UNAVAILABLE', lambda: self.split(root, [first, second], store))
        self.assert_unchanged(before)  # the ROOT and the failed child: untouched
        self.assert_no_lineage(root, first, second)  # child 1's reservation was removed
        self.assertEqual(cli._lineage_members(self.store, root), [])
        # Nothing left behind blocks the same split from running again.
        self.assertEqual(self.split(root, [first, second])['state'], 'PARKED')
        self.assertEqual([self.state(first), self.state(second)], ['OPEN', 'OPEN'])


class RootCommitFailsTests(TwoPhaseCase):
    def test_all_children_ok_root_commit_fails_leaves_the_root_unchanged(self):
        root, first, second = self.task(), self.task(), self.task()
        before = self.digests(root)
        store = FaultStore(self.store, fault_at('COMMIT', root))
        self.refused('TASK_STORE_UNAVAILABLE', lambda: self.split(root, [first, second], store))
        self.assert_unchanged(before)
        self.assert_no_lineage(root, first, second)

    def test_an_unknown_commit_state_fails_closed_until_the_root_is_readable(self):
        root, first, second = self.task(), self.task(), self.task()
        before = self.digests(root)
        store = FaultStore(self.store)
        store._fault = fault_at('COMMIT', root, then=lambda: store.broken.add(root))
        self.refused('SPLIT_COMMIT_UNKNOWN', lambda: self.split(root, [first, second], store))
        self.assert_unchanged(before)
        # Neither removed nor made usable while the commit state is unknown.
        for child in (first, second):
            self.assert_unusable(child)
        self.refused('SPLIT_COMMIT_UNKNOWN', lambda: cli.lineage_split_recover(store))
        self.assertEqual([self.state(first), self.state(second)], ['PENDING', 'PENDING'])
        # Once the ROOT reads again, the uncommitted reservations are cleaned.
        self.assertEqual(cli.lineage_split_recover(self.store),
                         {'finalized': [], 'released': sorted([first, second])})
        self.assert_no_lineage(root, first, second)
        self.assert_unchanged(before)


class CrashAfterRootCommitTests(TwoPhaseCase):
    def test_crash_right_after_the_root_commit_keeps_the_committed_children(self):
        root, first, second = self.task(), self.task(), self.task()
        store = FaultStore(self.store, fault_at('COMMIT', root, phase='after', code=None,
                                                error=Crash()))
        with self.assertRaises(Crash):
            self.split(root, [first, second], store)
        committed = self.line(root)
        self.assertEqual(committed['state'], 'PARKED')
        self.assertEqual(committed['splits'][0]['children'], [first, second])
        # Committed but not yet OPEN: counted in the lineage, usable by nothing.
        self.assertEqual(sorted(item['id'] for item in cli._lineage_members(self.store, root)),
                         sorted([first, second]))
        for child in (first, second):
            self.assert_unusable(child)
        # Restart recovery finalizes them and never deletes a committed child.
        self.refused('SPLIT_COMMITTED', lambda: task_store.release_split_child(
            self.store, first, op_id=committed['splits'][0]['op_id']))
        root_digest = self.digests(root)
        self.assertEqual(cli.lineage_split_recover(self.store),
                         {'finalized': sorted([first, second]), 'released': []})
        self.assertEqual([self.state(first), self.state(second)], ['OPEN', 'OPEN'])
        self.assert_unchanged(root_digest)

    def test_a_commit_that_reports_an_error_after_storing_is_completed(self):
        root, first = self.task(), self.task()
        store = FaultStore(self.store, fault_at('COMMIT', root, phase='after'))
        result = self.split(root, [first], store)
        self.assertEqual((result['state'], result['pending']), ('PARKED', []))
        self.assertEqual((self.state(root), self.state(first)), ('PARKED', 'OPEN'))


class ConcurrentSplitTests(TwoPhaseCase):
    def test_two_interleaved_reservations_never_commit_past_child_max(self):
        # Store level, with no lineage lock: both ops reserve, then both commit.
        root = self.task()
        first = [self.task() for _ in range(self.child_max)]
        extra = self.task()
        op_a, op_b = new_id(), new_id()
        for child, op in [(child, op_a) for child in first] + [(extra, op_b)]:
            task_store.reserve_split_child(self.store, child, self.reservation(root, op),
                                           expected_sha256=self.digests(child)[child])
        task_store.commit_split_root(self.store, root,
                                     self.root_commit(root, None, op_a, first),
                                     child_max=self.child_max, expected={'lineage': None})
        # B built on the ROOT it read before A committed: refused.
        self.refused('SPLIT_ROOT_NOT_OPEN', lambda: task_store.commit_split_root(
            self.store, root, self.root_commit(root, None, op_b, [extra]),
            child_max=self.child_max, expected=None))
        # B rebuilt on A's commit: the store counts A's children and refuses.
        self.refused('SPLIT_CHILD_LIMIT', lambda: task_store.commit_split_root(
            self.store, root, self.root_commit(root, self.line(root), op_b, [extra]),
            child_max=self.child_max, expected=None))
        self.assertEqual([entry['op_id'] for entry in self.line(root)['splits']], [op_a])
        self.assertEqual(cli.lineage_split_recover(self.store),
                         {'finalized': sorted(first), 'released': [extra]})
        self.assertEqual(len(cli._lineage_members(self.store, root)), self.child_max)
        self.assert_no_lineage(extra)

    def test_a_split_started_while_another_commits_writes_nothing(self):
        root = self.task()
        first, second = [self.task(), self.task()], [self.task(), self.task()]
        before = self.digests(*second)
        seen = []

        def rival():
            with mock.patch.object(task_store, 'TASK_UPDATE_LOCK_TIMEOUT_SECONDS', 0):
                try:
                    self.split(root, second)
                except LLMToolError as exc:
                    seen.append(getattr(exc, 'task_store_error_code', None))
        store = FaultStore(self.store, fault_at('COMMIT', root, code=None, then=rival))
        self.assertEqual(self.split(root, first, store)['children'], first)
        self.assertEqual(seen, ['LINEAGE_LOCK_BUSY'])
        self.assert_unchanged(before)
        self.assert_no_lineage(*second)
        self.assertEqual(sorted(item['id'] for item in cli._lineage_members(self.store, root)),
                         sorted(first))


class SplitMergeRaceTests(TwoPhaseCase):
    def test_a_merge_claim_before_the_root_commit_wins_and_the_split_writes_nothing(self):
        root, first, second = self.task(), self.task(), self.task()
        store = FaultStore(self.store, fault_at(
            'COMMIT', root, code=None,
            then=lambda: self.store.update(root, merge_attempt=CLAIM)))
        self.refused('SPLIT_ROOT_MERGE_STARTED',
                     lambda: self.split(root, [first, second], store))
        self.assertEqual(self.store.get(root)['merge_attempt'], CLAIM)
        self.assert_no_lineage(root, first, second)

    def test_a_root_commit_before_the_merge_claim_wins_and_the_claim_is_refused(self):
        root, first = self.task(), self.task()
        stale = self.digests(root)[root]
        self.split(root, [first])
        self.refused('MERGE_ROOT_PARKED', lambda: self.store.compare_and_update(
            root, expected={'record_sha256': stale}, merge_attempt=CLAIM))
        self.assertNotIn('merge_attempt', self.store.get(root))
        self.assertEqual((self.state(root), self.state(first)), ('PARKED', 'OPEN'))


class StoreBusyUnavailableTests(TwoPhaseCase):
    def test_busy_or_unavailable_at_any_write_refuses_with_its_code(self):
        for code in ('TASK_STORE_BUSY', 'TASK_STORE_UNAVAILABLE'):
            for action in ('RESERVE', 'COMMIT'):
                with self.subTest(code=code, action=action):
                    root, first, second = self.task(), self.task(), self.task()
                    before = self.digests(root)
                    target = second if action == 'RESERVE' else root
                    store = FaultStore(self.store, fault_at(action, target, code=code))
                    self.refused(code, lambda: self.split(root, [first, second], store))
                    self.assert_unchanged(before)
                    self.assert_no_lineage(root, first, second)

    def test_a_cleanup_that_fails_leaves_unusable_reservations_for_recovery(self):
        root, first, second = self.task(), self.task(), self.task()
        before = self.digests(root)
        commit = fault_at('COMMIT', root, code='TASK_STORE_BUSY')

        def fault(action, task_id, phase):
            commit(action, task_id, phase)
            if action == 'RELEASE' and phase == 'before':
                task_store._fail('TASK_STORE_BUSY')
        self.refused('TASK_STORE_BUSY',
                     lambda: self.split(root, [first, second], FaultStore(self.store, fault)))
        self.assert_unchanged(before)
        for child in (first, second):
            self.assert_unusable(child)
        self.assertEqual(cli._lineage_members(self.store, root), [])
        self.assertEqual(cli.lineage_split_recover(self.store),
                         {'finalized': [], 'released': sorted([first, second])})
        self.assert_no_lineage(first, second)


class RestartRecoveryTests(TwoPhaseCase):
    def test_recovery_finalizes_committed_children_and_cleans_uncommitted_ones(self):
        committed_root, kept = self.task(), [self.task(), self.task()]
        open_root, dropped = self.task(), [self.task(), self.task()]
        with self.assertRaises(Crash):
            self.split(committed_root, kept, FaultStore(self.store, fault_at(
                'COMMIT', committed_root, phase='after', code=None, error=Crash())))
        with self.assertRaises(Crash):
            self.split(open_root, dropped, FaultStore(self.store, fault_at(
                'COMMIT', open_root, code=None, error=Crash())))
        self.assertEqual([self.state(task_id) for task_id in kept + dropped], ['PENDING'] * 4)
        # Committed and uncommitted are told apart by the ROOT commit alone.
        self.refused('SPLIT_NOT_COMMITTED',
                     lambda: task_store.finalize_split_child(self.store, dropped[0]))
        op = self.line(kept[0])['split_op']
        self.refused('SPLIT_COMMITTED',
                     lambda: task_store.release_split_child(self.store, kept[0], op_id=op))
        roots = self.digests(committed_root, open_root)
        self.assertEqual(cli.lineage_split_recover(self.store),
                         {'finalized': sorted(kept), 'released': sorted(dropped)})
        self.assertEqual([self.state(task_id) for task_id in kept], ['OPEN', 'OPEN'])
        self.assert_no_lineage(open_root, *dropped)
        self.assert_unchanged(roots)
        # Recovery is idempotent.
        self.assertEqual(cli.lineage_split_recover(self.store),
                         {'finalized': [], 'released': []})


class RepeatedSplitTests(TwoPhaseCase):
    def test_the_same_split_repeated_after_success_changes_nothing(self):
        root, first, second = self.task(), self.task(), self.task()
        done = self.split(root, [first, second])
        before = self.digests(root, first, second)
        self.assertEqual(self.split(root, [first, second]), done)
        self.assertEqual(len(self.reads), 1)  # the re-run read no main SHA
        self.assert_unchanged(before)
        self.assertEqual(len(self.line(root)['splits']), 1)

    def test_the_same_split_repeated_after_a_crash_completes_it_once(self):
        for phase in ('before', 'after'):
            with self.subTest(phase=phase):
                self.reads.clear()
                root, first, second = self.task(), self.task(), self.task()
                with self.assertRaises(Crash):
                    self.split(root, [first, second], FaultStore(self.store, fault_at(
                        'COMMIT', root, phase=phase, code=None, error=Crash())))
                result = self.split(root, [first, second])
                self.assertEqual((result['state'], result['children'], result['base_sha'],
                                  result['pending']), ('PARKED', [first, second], MAIN, []))
                # A committed op is completed without a new main SHA read; an
                # uncommitted one is cleaned and runs again.
                self.assertEqual(len(self.reads), 1 if phase == 'after' else 2)
                self.assertEqual(len(self.line(root)['splits']), 1)
                self.assertEqual([self.state(first), self.state(second)], ['OPEN', 'OPEN'])


class PendingVisibilityTests(TwoPhaseCase):
    def test_no_entry_point_uses_an_uncommitted_pending_child(self):
        root, child = self.task(), self.task()
        with self.assertRaises(Crash):
            self.split(root, [child], FaultStore(self.store, fault_at(
                'COMMIT', root, code=None, error=Crash())))
        # Dispatch audit, merge claim, compare-and-update and lineage ends: refused.
        self.assert_unusable(child)
        self.refused('SPLIT_NOT_COMMITTED',
                     lambda: task_store.finalize_split_child(self.store, child))
        # Never counted: not a member, and the ROOT can still take child_max children.
        self.assertEqual(cli._lineage_members(self.store, root), [])
        self.assertIsNone(cli._lineage_roll_up(self.store, root))
        self.assertIsNone(cli.lineage_close_merged(self.store, child))
        self.refused('LINEAGE_MEMBER_INVALID',
                     lambda: self.split(self.task(), [child]))
        self.refused('REPLAN_CHILD_NOT_OPEN', lambda: cli.lineage_replan(
            self.store, child, self.task(), [child], pm_session=PM, reason='x',
            main_sha_reader=self.main_sha))
        fresh = [self.task() for _ in range(self.child_max)]
        self.assertEqual(self.split(root, fresh)['children'], fresh)
        self.assertEqual(self.state(child), 'PENDING')
        # A lineage end cleans the reservation first and records no end on it.
        self.refused('DROP_NOT_CHILD', lambda: cli.lineage_end(
            self.store, child, 'DROPPED', pm_session=PM, reason='not needed'))
        self.assert_no_lineage(child)


def _forbidden(name):
    def call(*_args, **_kwargs):
        raise AssertionError(name + ' ran for a PENDING split child')
    return call


class PendingEntryPointTests(TwoPhaseCase):
    """Owner B: dispatch, repair, merge and close never use an uncommitted PENDING child.

    The downstream step of each CLI entry point is replaced by a call that fails
    the test if it runs, so a refusal proves the entry point stopped first. The
    PM-caller check is injected (it is not under test); the store, its central
    write gate and every lineage check are real.
    """

    def setUp(self):
        super().setUp()
        self.root, self.child = self.task(), self.task()
        with self.assertRaises(Crash):
            self.split(self.root, [self.child], FaultStore(self.store, fault_at(
                'COMMIT', self.root, code=None, error=Crash())))
        self.assertEqual(self.state(self.child), 'PENDING')
        self.before = self.digests(self.root, self.child)
        caller = mock.patch('llmtool.pm_caller.check_caller', return_value=PM)
        caller.start()
        self.addCleanup(caller.stop)

    def tearDown(self):
        # Nothing an entry point did changed the child or its ROOT.
        self.assert_unchanged(self.before)
        self.assertEqual(self.state(self.child), 'PENDING')

    def cli_refused(self, call):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(call(), 1)
        printed = json.loads(out.getvalue())
        self.assertEqual((printed['step'], printed['codes']), ('REFUSED', ['TASK_LINEAGE_PENDING']))

    def advance_args(self, merge):
        return SimpleNamespace(task_id=self.child, pm_session=PM, merge=merge, pr=None,
                               integration_pr=None, reviewer_session_done=False,
                               owner_acceptance=None, owner_exception=None,
                               executor_task=None, max_steps=1, poll_seconds=0)

    def test_dispatch_never_starts_a_developer(self):
        args = SimpleNamespace(task_id=self.child, provider='opencode', background=True,
                               mode='direct', acceptance=['Bounded acceptance item.'],
                               executor_task=new_id())
        with mock.patch.object(cli.worker_dispatch, 'evaluate',
                               return_value=SimpleNamespace(allowed=True, reason_code=None)), \
                mock.patch.object(cli, '_delegate_d6_binding', _forbidden('D6 binding')), \
                mock.patch.object(cli, '_run_background_delegate', _forbidden('delegate')):
            self.refused('TASK_LINEAGE_PENDING',
                         lambda: cli.run_task_delegate(args, store=self.store))
        self.refused('TASK_LINEAGE_PENDING', lambda: self.store.append_dispatch_audit(
            self.child, {'decision': 'ALLOWED'}))

    def test_repair_never_runs(self):
        args = SimpleNamespace(task_id=self.child, pm_session=PM, executor_task=new_id(),
                               pr=17, family=None, auto_review=False)
        with mock.patch('llmtool.review_repair.repair_review', _forbidden('repair')):
            self.cli_refused(lambda: cli.run_task_repair(args, store=self.store))

    def test_advance_and_drive_never_publish_review_or_merge(self):
        for merge in (False, True):
            with self.subTest(merge=merge), \
                    mock.patch('llmtool.task_advance.advance', _forbidden('advance')):
                self.cli_refused(lambda: cli.run_task_advance(self.advance_args(merge),
                                                              store=self.store))
            with self.subTest(drive=merge), \
                    mock.patch('llmtool.task_drive.drive', _forbidden('drive')):
                self.cli_refused(lambda: cli.run_task_drive(self.advance_args(merge),
                                                            store=self.store))

    def test_merge_refuses_before_any_github_read_or_claim(self):
        from llmtool import task_advance
        result = task_advance.advance(task_id=self.child, store=self.store, merge=True,
                                      github_get=_forbidden('GitHub read'),
                                      merge_gh_runner=_forbidden('gh merge'))
        self.assertEqual(result['step'], 'MERGE_REFUSED')
        self.assertNotIn('merge_attempt', self.store.get(self.child))
        self.refused('TASK_LINEAGE_PENDING',
                     lambda: self.store.update(self.child, merge_attempt=CLAIM))

    def test_close_never_completes_abandons_or_closes(self):
        args = SimpleNamespace(task_id=self.child, executor_task=new_id(),
                               reviewer_task=None, pr=None)
        with mock.patch.object(cli, 'run_task_direct_result_event', _forbidden('complete')):
            self.refused('TASK_LINEAGE_PENDING',
                         lambda: cli.run_task_complete(args, store=self.store))
        self.refused('TASK_LINEAGE_PENDING', lambda: self.store.abandon(
            self.child, pm_session_id=PM, reason='not needed'))
        self.assertIsNone(cli.lineage_close_merged(self.store, self.child))


class SplitVsStartedChildTests(TwoPhaseCase):
    """#1781 (security v91, I4): a split never reserves a child that has started.

    A published PR, a merge claim, a Developer launch or result, a completion or
    a running advance/drive refuses the reservation. The store repeats the check
    under the child lock in the reservation write itself, so a change that lands
    after the split's preflight is caught too. Nothing is written on refusal.
    """

    def reserve(self, root, child):
        return task_store.reserve_split_child(self.store, child, self.reservation(root, new_id()),
                                              expected_sha256=self.digests(child)[child])

    def assert_refused_with_nothing_written(self, code, root, child):
        before = self.digests(root, child)
        # The store's own check, in the locked reservation write.
        self.refused(code, lambda: self.reserve(root, child))
        # The split preflight: refused before any main SHA read or write.
        self.refused(code, lambda: self.split(root, [child]))
        self.assertEqual(self.reads, [])
        self.assert_unchanged(before)
        self.assert_no_lineage(root, child)

    def test_a_published_child_is_refused(self):
        root, child = self.task(), self.task()
        self.store.record_published_pr(child, 17, 'a' * 40)
        self.assert_refused_with_nothing_written('SPLIT_CHILD_STARTED', root, child)

    def test_a_child_with_a_merge_claim_is_refused(self):
        root, child = self.task(), self.task()
        self.store.update(child, merge_attempt=CLAIM)
        self.assert_refused_with_nothing_written('LINEAGE_MEMBER_INVALID', root, child)
        self.assertEqual(self.store.get(child)['merge_attempt'], CLAIM)

    def test_a_developer_launch_result_or_completion_counts_as_started(self):
        for state in ('DEVELOPER_DISPATCHING', 'DEVELOPER_RESPONDED',
                      'DEVELOPER_OUTCOME_UNKNOWN', 'DEVELOPER_OUTPUT_REJECTED'):
            with self.subTest(state=state):
                self.assertTrue(task_store.split_child_started({'developer': {'state': state}}))
        for record in ({'completion': {'state': 'WAITING_REVIEW'}},
                       {'published_pr': {'pr': 17}}, {'merge_attempt': CLAIM}, None):
            with self.subTest(record=record):
                self.assertTrue(task_store.split_child_started(record))
        for record in ({}, {'developer': None}, {'developer': {'state': 'DEVELOPER_PENDING'}}):
            with self.subTest(record=record):
                self.assertFalse(task_store.split_child_started(record))

    def test_a_child_whose_advance_or_drive_is_running_is_refused(self):
        # task advance and task drive hold this lease for their whole run.
        root, child = self.task(), self.task()
        before = self.digests(root, child)
        with self.store.advance_lease(child):
            self.refused('SPLIT_CHILD_ADVANCING', lambda: self.reserve(root, child))
            self.refused('SPLIT_CHILD_ADVANCING', lambda: self.split(root, [child]))
            self.assert_unchanged(before)
            self.assert_no_lineage(root, child)
        # Once the advance has ended, the same split runs.
        self.assertEqual(self.split(root, [child])['state'], 'PARKED')
        self.assertEqual(self.state(child), 'OPEN')

    def test_a_publish_after_the_locked_preflight_refuses_with_nothing_written(self):
        # The child is published between the split's locked digest check and its
        # reservation write: the write's compare-and-set refuses.
        root, child = self.task(), self.task()
        before = self.digests(root)
        store = FaultStore(self.store, fault_at(
            'RESERVE', child, code=None,
            then=lambda: self.store.record_published_pr(child, 17, 'a' * 40)))
        self.refused('SPLIT_CHILD_CHANGED', lambda: self.split(root, [child], store))
        self.assert_unchanged(before)
        self.assert_no_lineage(root, child)
        self.assertEqual(self.store.get(child)['published_pr']['pr'], 17)
        # A re-run now refuses in the preflight.
        self.refused('SPLIT_CHILD_STARTED', lambda: self.split(root, [child]))


DISPUTE_REF = 'https://github.com/davidcatcat/davidcattoolsproject/pull/17#issuecomment-1001'
RULING_REF = 'https://github.com/davidcatcat/davidcattoolsproject/pull/17#issuecomment-1002'


def dispute(**changes):
    entry = {'finding_id': 'F1', 'pr': 17, 'head_sha': 'a' * 40, 'review_version': 1,
             'severity': 'High', 'state': 'OPEN', 'evidence_ref': DISPUTE_REF,
             'opened_at': AT}
    entry.update(changes)
    return entry


def ruling():
    return {'outcome': 'WITHDRAWN', 'head_sha': 'a' * 40, 'reviewer': 'codex-reviewer',
            'reviewer_task': 'arbiter-d6', 'reviewer_session': 'arbiter-session-2',
            'evidence_ref': RULING_REF, 'ruled_at': '2026-11-13T01:00:00Z'}


class SplitAndDisputeGatesTests(TwoPhaseCase):
    """The merged TaskStore update path keeps both gates (merge of D10 and D13).

    The D10 split checks (trusted-split guard, PENDING gate, lineage transition,
    _check_split_write) and the D13 review_disputes transition check each hold
    alone, and one update that carries both a lineage and a review_disputes
    change passes only when both gates pass; otherwise it writes nothing.
    """

    def test_the_dispute_gate_holds_alone_on_split_tasks(self):
        root, first, second = self.task(), self.task(), self.task()
        self.split(root, [first, second])
        for task_id in (root, first):
            with self.subTest(task=task_id):
                line = self.line(task_id)
                opened = dispute()
                ruled = dict(opened, state='RULED', ruling=ruling())
                for bad in ([ruled], [dict(opened, state='WITHDRAWN')]):
                    self.refused('REVIEW_DISPUTE_TRANSITION_INVALID',
                                 lambda bad=bad: self.store.update(task_id,
                                                                   review_disputes=bad))
                self.store.update(task_id, review_disputes=[opened])
                for bad in ([], [dict(opened, severity='Low')]):
                    self.refused('REVIEW_DISPUTE_TRANSITION_INVALID',
                                 lambda bad=bad: self.store.update(task_id,
                                                                   review_disputes=bad))
                self.store.update(task_id, review_disputes=[ruled])
                self.refused('REVIEW_DISPUTE_TRANSITION_INVALID',
                             lambda: self.store.update(task_id, review_disputes=[opened]))
                self.assertEqual(self.store.get(task_id)['review_disputes'], [ruled])
                self.assertEqual(self.line(task_id), line)  # the split lineage is untouched

    def test_the_split_gate_holds_alone_beside_stored_disputes(self):
        root = self.task()
        opened = dispute()
        self.store.update(root, review_disputes=[opened])
        first = [self.task() for _ in range(self.child_max)]
        self.split(root, first)
        self.assertEqual(self.store.get(root)['review_disputes'], [opened])
        # _check_split_write still counts committed children past child_max.
        extra, op = self.task(), new_id()
        task_store.reserve_split_child(self.store, extra, self.reservation(root, op),
                                       expected_sha256=self.digests(extra)[extra])
        before = self.digests(root)
        self.refused('SPLIT_CHILD_LIMIT', lambda: task_store.commit_split_root(
            self.store, root, self.root_commit(root, self.line(root), op, [extra]),
            child_max=self.child_max, expected=None))
        self.assert_unchanged(before)
        self.assertEqual(self.store.get(root)['review_disputes'], [opened])
        self.assertEqual(cli.lineage_split_recover(self.store),
                         {'finalized': [], 'released': [extra]})

    def test_a_trusted_split_write_never_carries_review_disputes(self):
        root, child = self.task(), self.task()
        before = self.digests(root, child)
        op = new_id()
        for task_id, action, line, child_max in (
                (child, 'RESERVE', self.reservation(root, op), None),
                (root, 'COMMIT', self.root_commit(root, None, op, [child]), self.child_max),
                (child, 'FINALIZE', dict(self.reservation(root, op), state='OPEN'), None),
                (child, 'RELEASE', None, None)):
            with self.subTest(action=action):
                self.refused('TASK_LINEAGE_TRANSITION_INVALID', lambda: self.store._update(
                    task_id, expected=None,
                    changes={'lineage': line, 'review_disputes': [dispute()]},
                    trusted_split=action, split_child_max=child_max))
        self.assert_unchanged(before)
        self.assert_no_lineage(root, child)
        for task_id in (root, child):
            self.assertNotIn('review_disputes', self.store.get(task_id))

    def test_a_pending_reservation_takes_no_dispute_write(self):
        root, child = self.task(), self.task()
        op = new_id()
        task_store.reserve_split_child(self.store, child, self.reservation(root, op),
                                       expected_sha256=self.digests(child)[child])
        line = self.line(child)
        before = self.digests(root, child)
        for write in (
                lambda: self.store.update(child, review_disputes=[dispute()]),
                lambda: self.store.compare_and_update(
                    child, expected={'record_sha256': before[child]},
                    review_disputes=[dispute()]),
                lambda: self.store.update(child, lineage=dict(line, state='OPEN'),
                                          review_disputes=[dispute()])):
            self.refused('TASK_LINEAGE_PENDING', write)
        # Its own FINALIZE cannot carry a dispute either.
        self.refused('TASK_LINEAGE_TRANSITION_INVALID', lambda: self.store._update(
            child, expected={'lineage': line},
            changes={'lineage': dict(line, state='OPEN'), 'review_disputes': [dispute()]},
            trusted_split='FINALIZE'))
        self.assert_unchanged(before)
        self.assertNotIn('review_disputes', self.store.get(child))
        # Once the uncommitted reservation is released, the Task takes a dispute.
        task_store.release_split_child(self.store, child, op_id=op)
        self.store.update(child, review_disputes=[dispute()])
        self.assertEqual(self.store.get(child)['review_disputes'], [dispute()])
        self.assert_no_lineage(child)

    def test_one_combined_update_passes_both_gates_or_writes_nothing(self):
        root, child = self.task(), self.task()
        self.split(root, [child])
        line = self.line(child)
        before = self.digests(root, child)
        opened = dispute()
        stopped = dict(line, state='STOPPED', reason='Replaced.')
        # Lineage valid, dispute invalid: the dispute gate refuses the whole write.
        self.refused('REVIEW_DISPUTE_TRANSITION_INVALID', lambda: self.store.update(
            child, lineage=stopped, review_disputes=[dict(opened, state='WITHDRAWN')]))
        self.refused('REVIEW_DISPUTE_INVALID', lambda: self.store.update(
            child, lineage=stopped, review_disputes=[dispute(finding_id='X1')]))
        # Dispute valid, lineage invalid: the lineage gate refuses the whole write.
        no_op = {key: value for key, value in line.items() if key != 'split_op'}
        for bad in (dict(line, state='PENDING'), dict(line, parent=new_id()), no_op):
            with self.subTest(lineage=bad):
                self.refused('TASK_LINEAGE_TRANSITION_INVALID',
                             lambda bad=bad: self.store.update(child, lineage=bad,
                                                               review_disputes=[opened]))
        self.assert_unchanged(before)
        self.assertNotIn('review_disputes', self.store.get(child))
        # Both valid: one write stores both.
        self.store.update(child, lineage=stopped, review_disputes=[opened])
        record = self.store.get(child)
        self.assertEqual((record['lineage'], record['review_disputes']), (stopped, [opened]))
        # An ended lineage stays ended, even beside a valid dispute move.
        self.refused('TASK_LINEAGE_TRANSITION_INVALID', lambda: self.store.update(
            child, lineage=dict(line, state='CLOSED'),
            review_disputes=[dict(opened, state='WITHDRAWN')]))
        record = self.store.get(child)
        self.assertEqual((record['lineage'], record['review_disputes']), (stopped, [opened]))

    def test_a_combined_update_cannot_split_or_claim_outside_the_split_gate(self):
        root, child, extra = self.task(), self.task(), self.task()
        self.split(root, [child])
        line = self.line(root)
        before = self.digests(root, extra)
        op = new_id()
        # Growing the ROOT's splits outside COMMIT, beside a valid dispute: refused.
        self.refused('TASK_LINEAGE_TRANSITION_INVALID', lambda: self.store.update(
            root, lineage=self.root_commit(root, line, op, [extra]),
            review_disputes=[dispute()]))
        # Reserving a child outside RESERVE, beside a valid dispute: refused.
        self.refused('TASK_LINEAGE_TRANSITION_INVALID', lambda: self.store.update(
            extra, lineage=self.reservation(root, op), review_disputes=[dispute()]))
        # A merge claim on the PARKED ROOT, beside a valid dispute: refused.
        self.refused('MERGE_ROOT_PARKED', lambda: self.store.update(
            root, merge_attempt=CLAIM, review_disputes=[dispute()]))
        self.assert_unchanged(before)
        self.assert_no_lineage(extra)
        self.assertEqual(self.line(root), line)
        for task_id in (root, extra):
            self.assertNotIn('review_disputes', self.store.get(task_id))


if __name__ == '__main__':
    unittest.main()
