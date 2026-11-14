"""Compact tests for ``task file-ops`` and record-pm file_ops (#1502, child b).

Temporary git repositories, a temporary D6 registry and a temporary TaskStore
only; no network. The PM caller check is injected (the test owns the session
comparison); every other check is the real one. The full matrix is child (c).
"""

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from llmtool import file_ops, pm_caller, task_store, task_submission_cli  # noqa: E402
from llmtool.errors import LLMToolError  # noqa: E402
from llmtool.task_store import TaskStore  # noqa: E402

D6 = 'd6-file-ops-test'
BRANCH = 'd6-branch'
OPS = [{'op': 'move', 'from': 'a.txt', 'to': 'docs/a.txt'}]


def _env():
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update({'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
                'GIT_AUTHOR_NAME': 'File Ops Test', 'GIT_AUTHOR_EMAIL': 'fo@example.invalid',
                'GIT_COMMITTER_NAME': 'File Ops Test', 'GIT_COMMITTER_EMAIL': 'fo@example.invalid'})
    return env


def git(root, *args):
    return subprocess.run(['git', *args], cwd=root, env=_env(), check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout


def _response(ops=OPS):
    assignment = {'route': 'claude', 'risk': 'LOW', 'goal': 'Move one tracked file.',
                  'acceptance': ['The file is moved.']}
    if ops is not None:
        assignment['file_ops'] = ops
    return json.dumps({'schema_version': 1, 'summary': 'Move a file.',
                       'developer_assignment': assignment})


class _NotOwner(pm_caller.PMCallerRefused):
    def __init__(self):
        Exception.__init__(self, 'caller does not own the Task')
        self.code = 'PM_CALLER_NOT_OWNER'


@unittest.skipUnless(shutil.which('git') and os.name == 'posix', 'needs git on POSIX')
class FileOpsCommandTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(os.path.realpath(temp.name))
        self.root = str(base / 'd6')
        os.mkdir(self.root, 0o700)
        git(self.root, 'init', '-q')
        git(self.root, 'symbolic-ref', 'HEAD', 'refs/heads/' + BRANCH)
        Path(self.root, 'a.txt').write_text('alpha\n')
        Path(self.root, 'b.txt').write_text('beta\n')
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-q', '-m', 'init')
        self.head = git(self.root, 'rev-parse', 'HEAD').strip()
        self.common = os.path.join(self.root, '.git')
        registry = Path(self.common) / 'llmtool-worktrees'
        registry.mkdir(mode=0o700)
        info = os.lstat(self.root)
        entry = registry / (D6 + '.json')
        entry.write_text(json.dumps({
            'schema_version': 1, 'task_id': D6, 'state': 'ACTIVE', 'lock_state': 'LOCKED',
            'git_common_dir': self.common, 'trusted_agent_id': 'test-developer',
            'session_ref': 'test-session-ref', 'root_realpath': self.root, 'branch': BRANCH,
            'base_sha': self.head, 'head_sha': self.head,
            'physical_identity': {'device': info.st_dev, 'inode': info.st_ino,
                                  'uid': info.st_uid, 'mode': '0o700'}}))
        os.chmod(entry, 0o600)
        self.store = TaskStore(root=base / 'tasks')
        patcher = mock.patch.object(pm_caller, 'check_caller', side_effect=self._check_caller)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _check_caller(self, store, task_id, pm_session):
        if store.get(task_id)['pm_session_id'] != pm_session:
            raise _NotOwner()

    def record_pm(self, ops=OPS):
        args = argparse.Namespace(pm_session_id=None, pm_runtime=None, pm_cwd=None)
        stream = io.StringIO(json.dumps({'request': 'Move a.txt into docs.',
                                         'response': _response(ops)}))
        return task_submission_cli.run_task_record_pm(
            args, store=self.store, input_stream=stream, return_record=True)

    def run_cmd(self, task, **overrides):
        values = dict(task_id=task['task_id'], executor_task=D6,
                      pm_session=task['pm_session_id'], common_dir=self.common)
        values.update(overrides)
        out = io.StringIO()
        rc = task_submission_cli.run_task_file_ops(
            argparse.Namespace(**values), store=self.store, stdout=out)
        return rc, json.loads(out.getvalue())

    def status(self):
        return git(self.root, 'status', '--porcelain=v1', '--untracked-files=all',
                   '--no-renames')

    def test_record_pm_stores_and_binds_file_ops(self):
        saved = self.record_pm()
        self.assertEqual(saved['file_ops'], {
            'ops': OPS, 'ops_sha256': file_ops.ops_sha256(OPS),
            'contract_digest': saved['task_binding']['contract_digest']})
        self.assertNotIn('file_ops', saved['pm_assignment'])
        self.assertNotIn('file_ops_evidence', saved)
        for changes, code in (({'file_ops': saved['file_ops']}, 'TASK_FILE_OPS_IMMUTABLE'),
                              ({'file_ops_evidence': None}, 'TASK_FILE_OPS_EVIDENCE_FORBIDDEN')):
            with self.assertRaises(LLMToolError) as ctx:
                self.store.update(saved['task_id'], **changes)
            self.assertEqual(ctx.exception.task_store_error_code, code)
        # The stored field and the PM response bind each other.
        other = [{'op': 'delete', 'path': 'b.txt'}]
        for tampered in (
                dict(saved, file_ops=dict(saved['file_ops'], ops=other,
                                          ops_sha256=file_ops.ops_sha256(other))),
                {k: v for k, v in saved.items() if k != 'file_ops'}):
            tampered['record_sha256'] = task_store._digest(tampered)
            with self.assertRaises(LLMToolError) as ctx:
                task_store._validate(tampered)
            self.assertEqual(ctx.exception.task_store_error_code, 'TASK_FILE_OPS_INVALID')
        plain = self.record_pm(ops=None)
        self.assertNotIn('file_ops', plain)
        before = self.store.task_ids()
        with self.assertRaises(LLMToolError) as ctx:
            self.record_pm(ops=[{'op': 'delete', 'path': '../outside.txt'}])
        self.assertEqual(ctx.exception.code, 'FILE_OPS_PATH_INVALID')
        self.assertEqual(self.store.task_ids(), before)

    def test_admission_refusals_in_order_apply_nothing(self):
        task = self.record_pm()
        real_get = self.store.get
        failed = {'state': 'FAILED', 'isolate': True}
        cases = [
            ({}, {'pm_session': 'pm-other-session', 'executor_task': 'd6-missing'},
             'PM_CALLER_NOT_OWNER'),
            ({'schema_version': 1, 'state': 'PM_PENDING'}, {}, 'FILE_OPS_TASK_SCHEMA'),
            ({'state': 'PM_PENDING', 'developer': {}}, {}, 'FILE_OPS_TASK_STATE'),
            ({'file_ops': None, 'developer': {}}, {}, 'FILE_OPS_NOT_RECORDED'),
            ({'developer': {}, 'abandonment': {}}, {}, 'FILE_OPS_DEVELOPER_EXISTS'),
            ({'abandonment': {}}, {'executor_task': 'd6-missing'}, 'FILE_OPS_TASK_ABANDONED'),
            ({'lineage': {'state': 'PARKED'}}, {'executor_task': 'd6-missing'},
             'TASK_LINEAGE_PARKED'),
            ({'file_ops_evidence': failed}, {'executor_task': 'd6-missing'},
             'FILE_OPS_D6_UNAVAILABLE'),
            ({'completion': {'executor_task_id': 'd6-other'}}, {},
             'FILE_OPS_D6_BINDING_MISMATCH'),
            ({'file_ops_evidence': failed}, {}, 'FILE_OPS_EVIDENCE_FAILED'),
        ]
        with mock.patch.object(file_ops, 'plan', side_effect=AssertionError('plan ran')), \
                mock.patch.object(file_ops, 'apply', side_effect=AssertionError('apply ran')):
            for changes, overrides, code in cases:
                with self.subTest(code=code), mock.patch.object(
                        self.store, 'get',
                        side_effect=lambda tid, c=changes: {**real_get(tid), **c}):
                    rc, out = self.run_cmd(task, **overrides)
                    self.assertEqual((rc, out['status'], out['code']), (1, 'REFUSED', code))
            Path(self.root, 'stray.txt').write_text('untracked\n')
            with mock.patch.object(self.store, 'get', side_effect=lambda tid: {
                    **real_get(tid), 'file_ops_evidence': failed}):
                rc, out = self.run_cmd(task)
            self.assertEqual((rc, out['code']), (1, 'FILE_OPS_D6_NOT_CLEAN'))
            os.unlink(os.path.join(self.root, 'stray.txt'))
            self.assertEqual(self.status(), '')
            self.assertNotIn('file_ops_evidence', real_get(task['task_id']))
            Path(self.root, 'c.txt').write_text('gamma\n')
            git(self.root, 'add', 'c.txt')
            git(self.root, 'commit', '-q', '-m', 'move HEAD past the registry')
            rc, out = self.run_cmd(task)
            self.assertEqual((rc, out['code']), (1, 'FILE_OPS_D6_BINDING_MISMATCH'))
        self.assertEqual(self.status(), '')
        self.assertTrue(os.path.exists(os.path.join(self.root, 'a.txt')))
        self.assertNotIn('file_ops_evidence', real_get(task['task_id']))

    def test_verified_evidence_and_idempotent_rerun(self):
        task = self.record_pm()
        rc, out = self.run_cmd(task)
        self.assertEqual((rc, out['status'], out['noop']), (0, 'VERIFIED', False))
        saved = self.store.get(task['task_id'])
        evidence = saved['file_ops_evidence']
        self.assertEqual(out['evidence'], evidence)
        self.assertEqual({k: v for k, v in evidence.items() if k != 'recorded_at'}, {
            'state': 'VERIFIED', 'isolate': False, 'task_id': task['task_id'],
            'run_id': task['run_id'], 'executor_task': D6, 'root': self.root,
            'branch': BRANCH, 'head': self.head, 'ops_sha256': file_ops.ops_sha256(OPS),
            'plan_sha256': file_ops.expected_plan(self.root, self.head, OPS)['sha256'],
            'status_sha256': file_ops.status_sha256(self.root)})
        self.assertRegex(evidence['recorded_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        self.assertEqual(saved['file_ops_reservation']['executor_task'], D6)
        self.assertEqual(self.journal_epoch(), 1)
        self.assertEqual(sorted(self.status().splitlines()), ['A  docs/a.txt', 'D  a.txt'])
        self.assertEqual(git(self.root, 'rev-parse', 'HEAD').strip(), self.head)
        rc, out = self.run_cmd(task)
        self.assertEqual((rc, out['status'], out['noop']), (0, 'VERIFIED', True))
        # The verified re-run writes nothing: no new reservation, no journal claim.
        self.assertEqual(self.store.get(task['task_id'])['record_sha256'],
                         saved['record_sha256'])
        self.assertEqual(self.journal_epoch(), 1)
        Path(self.root, 'stray.txt').write_text('untracked\n')
        rc, out = self.run_cmd(task)
        self.assertEqual((rc, out['code']), (1, 'FILE_OPS_EVIDENCE_MISMATCH'))
        self.assertEqual(self.store.get(task['task_id'])['record_sha256'],
                         saved['record_sha256'])

    def test_failed_evidence_is_sticky(self):
        task = self.record_pm()
        with mock.patch.object(file_ops, '_mutate', return_value=False):
            rc, out = self.run_cmd(task)
        self.assertEqual((rc, out['status']), (1, 'FAILED'))
        evidence = self.store.get(task['task_id'])['file_ops_evidence']
        self.assertEqual((evidence['state'], evidence['isolate']), ('FAILED', True))
        self.assertTrue(re.fullmatch(r'[0-9a-f]{64}', evidence['plan_sha256']))
        rc, out = self.run_cmd(task)
        self.assertEqual((rc, out['code']), (1, 'FILE_OPS_EVIDENCE_FAILED'))
        self.assertTrue(os.path.exists(os.path.join(self.root, 'a.txt')))
        self.assertEqual(self.status(), '')
        current = self.store.get(task['task_id'])
        with self.assertRaises(LLMToolError) as ctx:
            self.store.record_file_ops_evidence(
                task['task_id'], dict(evidence, state='VERIFIED', isolate=False),
                expected={'record_sha256': current['record_sha256']})
        self.assertEqual(ctx.exception.task_store_error_code,
                         'TASK_FILE_OPS_EVIDENCE_IMMUTABLE')
        self.assertEqual(self.store.get(task['task_id'])['file_ops_evidence'], evidence)

    def assert_binding_lock_free(self):
        # Taken exclusively with the real lock: a lock left held fails BUSY here.
        with self.store.d6_binding_lock():
            pass

    def journal_epoch(self):
        path = Path(self.store.root, task_store.D6_BINDING_JOURNAL_NAME)
        return json.loads(path.read_text())['epoch'] if path.exists() else 0

    def bind_refused(self, api, task_id, executor=D6, store=None):
        """The message a bind of ``task_id`` to ``executor`` through ``api`` refuses with."""
        store = self.store if store is None else store
        with self.assertRaises(LLMToolError) as ctx:
            getattr(store, api)(task_id, executor_task_id=executor,
                                expected={'developer': None})
        return str(ctx.exception)

    @staticmethod
    def held_by(task):
        return 'TASK_D6_ALREADY_BOUND: D6 is held by Task ' + task['task_id']

    def test_concurrent_d6_bind_during_apply_is_refused(self):
        task = self.record_pm()
        other = self.record_pm(ops=None)
        real_apply = file_ops.apply
        refusals = []

        def apply_with_bind(root, sealed):
            # A second Task tries to bind the same D6 while file-ops is mid-apply.
            with self.assertRaises(LLMToolError) as ctx:
                self.store.d6_bind_compare_and_update(
                    other['task_id'], executor_task_id=D6, expected={'developer': None})
            refusals.append(ctx.exception.task_store_error_code)
            return real_apply(root, sealed)

        with mock.patch.object(task_store, 'TASK_UPDATE_LOCK_TIMEOUT_SECONDS', 0.2), \
                mock.patch.object(file_ops, 'apply', side_effect=apply_with_bind):
            rc, out = self.run_cmd(task)
        self.assertEqual(refusals, ['TASK_D6_BINDING_BUSY'])
        self.assertEqual((rc, out['status']), (0, 'VERIFIED'))
        self.assertEqual(self.store.get(task['task_id'])['file_ops_evidence']['state'],
                         'VERIFIED')
        self.assertEqual(self.store.get(other['task_id'])['record_sha256'],
                         other['record_sha256'])
        self.assert_binding_lock_free()
        # Past the lock the reservation still holds the D6 for this Task.
        self.assertEqual(self.bind_refused('d6_bind_compare_and_update', other['task_id']),
                         self.held_by(task))
        self.assertEqual(self.store.get(other['task_id'])['record_sha256'],
                         other['record_sha256'])

    def overlapping_bind(self, api, *, pause):
        """B, a separate worker, validates (``pause='validate'``) or also scans
        (``pause='scan'``) before A's claim, then waits on the binding lock
        while A reserves and mutates; A finishes well inside B's timeout."""
        task, other = self.record_pm(), self.record_pm(ops=None)
        binder = TaskStore(root=self.store.root)
        paused, claimed = threading.Event(), threading.Event()
        events = []
        real_lock, real_scan = TaskStore._lock_sidecar, TaskStore._d6_scan_holders
        real_apply = file_ops.apply

        def lock(directory, name, **kwargs):
            if (pause == 'validate' and name == task_store.D6_BINDING_LOCK_NAME and
                    not paused.is_set()):
                events.append('B validated')
                paused.set()
                claimed.wait(10)
            return real_lock(directory, name, **kwargs)

        def scan(task_id, claims, holds):
            found = real_scan(binder, task_id, claims, holds)
            if pause == 'scan' and not paused.is_set():
                events.append('B scanned %d holders' % len(found))
                paused.set()
                claimed.wait(10)
            return found

        def bind():
            try:
                getattr(binder, api)(other['task_id'], executor_task_id=D6,
                                     expected={'developer': None})
                events.append('B bound')
            except LLMToolError as exc:
                events.append(str(exc))

        def apply_after_claim(root, sealed):
            reservation = self.store.get(task['task_id']).get('file_ops_reservation')
            events.append('A reserved %s' % (reservation or {}).get('executor_task'))
            claimed.set()
            result = real_apply(root, sealed)
            events.append('A applied')
            return result

        worker = threading.Thread(target=bind)
        with mock.patch.object(task_store, 'TASK_UPDATE_LOCK_TIMEOUT_SECONDS', 30), \
                mock.patch.object(binder, '_lock_sidecar', side_effect=lock), \
                mock.patch.object(binder, '_d6_scan_holders', side_effect=scan), \
                mock.patch.object(file_ops, 'apply', side_effect=apply_after_claim):
            worker.start()
            self.assertTrue(paused.wait(10))
            rc, out = self.run_cmd(task)
            worker.join(30)
        self.assertFalse(worker.is_alive())
        first = 'B validated' if pause == 'validate' else 'B scanned 0 holders'
        self.assertEqual(events, [first, 'A reserved ' + D6, 'A applied', self.held_by(task)])
        self.assertEqual((rc, out['status']), (0, 'VERIFIED'))
        # B changed nothing and started nothing.
        current = self.store.get(other['task_id'])
        self.assertEqual(current['record_sha256'], other['record_sha256'])
        self.assertIsNone(current['developer'])
        self.assert_binding_lock_free()

    def test_d6_binder_validated_before_claim_refuses_already_bound(self):
        self.overlapping_bind('d6_bind_compare_and_update', pause='validate')

    def test_claude_binder_validated_before_claim_refuses_already_bound(self):
        self.overlapping_bind('claude_bind_compare_and_update', pause='validate')

    def test_d6_binder_scanned_before_claim_refuses_already_bound(self):
        self.overlapping_bind('d6_bind_compare_and_update', pause='scan')

    def test_claude_binder_scanned_before_claim_refuses_already_bound(self):
        self.overlapping_bind('claude_bind_compare_and_update', pause='scan')

    def test_unrelated_d6_unaffected_and_only_this_task_binds_reserved_d6(self):
        task, other = self.record_pm(), self.record_pm(ops=None)
        binder = TaskStore(root=self.store.root)
        events = []
        real_apply = file_ops.apply

        def bind_unrelated():
            try:
                binder.d6_bind_compare_and_update(
                    other['task_id'], executor_task_id='d6-unrelated-test',
                    expected={'developer': None})
                events.append('B bound unrelated')
            except LLMToolError as exc:
                events.append(str(exc))

        worker = threading.Thread(target=bind_unrelated)

        def apply_with_waiter(root, sealed):
            worker.start()
            result = real_apply(root, sealed)
            events.append('A applied')
            return result

        with mock.patch.object(task_store, 'TASK_UPDATE_LOCK_TIMEOUT_SECONDS', 30), \
                mock.patch.object(file_ops, 'apply', side_effect=apply_with_waiter):
            rc, out = self.run_cmd(task)
            worker.join(30)
        self.assertFalse(worker.is_alive())
        self.assertEqual((rc, out['status']), (0, 'VERIFIED'))
        self.assertEqual(events, ['A applied', 'B bound unrelated'])
        for api in ('d6_bind_compare_and_update', 'claude_bind_compare_and_update'):
            with self.subTest(api=api):
                self.assertEqual(self.bind_refused(api, other['task_id']), self.held_by(task))
        # Only the reserving Task may later bind its D6.
        self.store.d6_bind_compare_and_update(task['task_id'], executor_task_id=D6,
                                              expected={'developer': None})
        self.assert_binding_lock_free()

    def assert_reservation_kept(self, task, other, saved):
        """The reservation outlives the failure: a later worker sees it, a re-run refuses."""
        self.assertEqual(saved['file_ops_reservation']['executor_task'], D6)
        self.assertNotIn('file_ops_evidence', saved)
        later = TaskStore(root=self.store.root)
        for api in ('d6_bind_compare_and_update', 'claude_bind_compare_and_update'):
            with self.subTest(api=api):
                self.assertEqual(self.bind_refused(api, other['task_id'], store=later),
                                 self.held_by(task))
        self.assertEqual(later.get(other['task_id'])['record_sha256'], other['record_sha256'])
        rc, out = self.run_cmd(task)
        self.assertEqual((rc, out['code']), (1, 'FILE_OPS_D6_NOT_CLEAN'))
        self.assertEqual(later.get(task['task_id']), saved)
        self.assertEqual(self.journal_epoch(), 1)
        self.assert_binding_lock_free()

    def test_reservation_kept_after_interrupted_mutation(self):
        task, other = self.record_pm(), self.record_pm(ops=None)
        real_apply = file_ops.apply

        def apply_then_stop(root, sealed):
            real_apply(root, sealed)
            raise RuntimeError('worker stopped before the evidence write')

        with mock.patch.object(file_ops, 'apply', side_effect=apply_then_stop):
            with self.assertRaises(RuntimeError):
                self.run_cmd(task)
        self.assert_binding_lock_free()
        self.assertNotEqual(self.status(), '')
        self.assert_reservation_kept(task, other, self.store.get(task['task_id']))

    def test_reservation_kept_after_evidence_store_failure(self):
        task, other = self.record_pm(), self.record_pm(ops=None)
        with mock.patch.object(self.store, 'record_file_ops_evidence',
                               side_effect=LLMToolError('not stored')):
            rc, out = self.run_cmd(task)
        self.assertEqual((rc, out['code'], out['isolate']),
                         (1, 'FILE_OPS_EVIDENCE_NOT_STORED', True))
        self.assert_binding_lock_free()
        self.assert_reservation_kept(task, other, self.store.get(task['task_id']))

    def test_reservation_write_failure_refuses_before_mutation(self):
        task = self.record_pm()
        with mock.patch.object(self.store, '_reserve_file_ops_d6',
                               side_effect=LLMToolError('not stored')), \
                mock.patch.object(file_ops, 'apply', side_effect=AssertionError('apply ran')):
            rc, out = self.run_cmd(task)
        self.assertEqual((rc, out['status'], out['code']),
                         (1, 'REFUSED', 'FILE_OPS_D6_UNAVAILABLE'))
        self.assertEqual(self.status(), '')
        current = self.store.get(task['task_id'])
        self.assertEqual(current['record_sha256'], task['record_sha256'])
        self.assertNotIn('file_ops_reservation', current)
        self.assert_binding_lock_free()

    def test_binding_lock_unavailable_refuses_before_mutation(self):
        task = self.record_pm()
        with mock.patch.object(task_store, 'TASK_UPDATE_LOCK_TIMEOUT_SECONDS', 0.2), \
                mock.patch.object(file_ops, 'plan', side_effect=AssertionError('plan ran')), \
                mock.patch.object(file_ops, 'apply', side_effect=AssertionError('apply ran')):
            with self.store.d6_binding_lock():
                rc, out = self.run_cmd(task)
            self.assertEqual((rc, out['status'], out['code']),
                             (1, 'REFUSED', 'FILE_OPS_D6_UNAVAILABLE'))
            # A caller refusal never takes the lock.
            with self.store.d6_binding_lock():
                rc, out = self.run_cmd(task, pm_session='pm-other-session')
            self.assertEqual((rc, out['code']), (1, 'PM_CALLER_NOT_OWNER'))
        self.assertEqual(self.status(), '')
        self.assertNotIn('file_ops_evidence', self.store.get(task['task_id']))
        self.assert_binding_lock_free()

    @staticmethod
    def adopt(store, task_id, *, locked=True):
        """Move the Task to a new PM session through the real TaskStore adoption.

        ``locked=False`` uses the internal path without the D6 binding lock, to
        reach the defence-in-depth recheck just before apply.
        """
        current = store.get(task_id)
        call = store.adopt_pm_session if locked else store._adopt_pm_session
        return call(
            task_id, old_session_key=current['pm_session_id'],
            new_session_key='pm-' + task_store.new_id(), at='2026-11-14T00:00:00Z',
            expected_record_sha256=current['record_sha256'],
            old_runtime='claude-code', new_runtime='claude-code', new_account=None,
            liveness=['PROCESS_CLEAN', 'LOCK_FREE', 'REGISTRY_CLEAN'])

    def test_adoption_between_caller_check_and_lock_refuses_as_non_owner(self):
        # v93 (CWE-367): the caller check passes, then a separate worker adopts
        # the Task to a new PM session before file-ops takes the binding lock.
        task = self.record_pm()
        rc, out = self.run_cmd(task, pm_session='pm-other-session')
        non_owner = out['code']
        self.assertEqual((rc, non_owner), (1, 'PM_CALLER_NOT_OWNER'))
        adopter = TaskStore(root=self.store.root)
        checked, adopted = threading.Event(), threading.Event()
        calls, results, checks = [], [], []
        real_lock, real_check = self.store.d6_binding_lock, self._check_caller

        def counted_check(store, task_id, pm_session):
            checks.append(pm_session)
            return real_check(store, task_id, pm_session)

        def lock_after_adoption():
            calls.append(len(checks))
            checked.set()
            self.assertTrue(adopted.wait(10))
            return real_lock()

        def adopt_after_check():
            if checked.wait(10):
                results.append(self.adopt(adopter, task['task_id']))
            adopted.set()

        worker = threading.Thread(target=adopt_after_check)
        with mock.patch.object(pm_caller, 'check_caller', side_effect=counted_check), \
                mock.patch.object(self.store, 'd6_binding_lock',
                                  side_effect=lock_after_adoption), \
                mock.patch.object(file_ops, 'plan', side_effect=AssertionError('plan ran')), \
                mock.patch.object(file_ops, 'apply', side_effect=AssertionError('apply ran')), \
                mock.patch.object(self.store, '_reserve_file_ops_d6',
                                  side_effect=AssertionError('reservation written')):
            worker.start()
            rc, out = self.run_cmd(task)
            worker.join(10)
        self.assertFalse(worker.is_alive())
        # The first check ran before the lock and passed; the recheck under it refused.
        self.assertEqual(calls, [1])
        self.assertEqual(len(checks), 2)
        self.assertEqual(len(results), 1)
        self.assertEqual((rc, out['status'], out['code']), (1, 'REFUSED', non_owner))
        current = self.store.get(task['task_id'])
        self.assertEqual(current, results[0])
        self.assertNotEqual(current['pm_session_id'], task['pm_session_id'])
        self.assertNotIn('file_ops_reservation', current)
        self.assertNotIn('file_ops_evidence', current)
        self.assertEqual(self.journal_epoch(), 0)
        self.assertEqual(self.status(), '')
        self.assertTrue(os.path.exists(os.path.join(self.root, 'a.txt')))
        self.assert_binding_lock_free()

    def test_adoption_during_run_waits_for_the_evidence(self):
        # v93: adoption takes the D6 binding lock, so a separate worker that
        # adopts mid-apply waits; A applies and seals evidence first, and B's
        # adoption, compared against the record it read before, then loses.
        task = self.record_pm()
        adopter = TaskStore(root=self.store.root)
        waiting = threading.Event()
        events, results = [], []
        real_apply, real_lock = file_ops.apply, TaskStore._lock_sidecar

        def lock(directory, name, **kwargs):
            if name == task_store.D6_BINDING_LOCK_NAME:
                events.append('B waits')
                waiting.set()
            return real_lock(directory, name, **kwargs)

        def adopt():
            try:
                results.append(self.adopt(adopter, task['task_id']))
                events.append('B adopted')
            except LLMToolError as exc:
                events.append(exc.task_store_error_code)

        worker = threading.Thread(target=adopt)

        def apply_with_adopter(root, sealed):
            worker.start()
            self.assertTrue(waiting.wait(10))
            result = real_apply(root, sealed)
            events.append('A applied')
            return result

        with mock.patch.object(task_store, 'TASK_UPDATE_LOCK_TIMEOUT_SECONDS', 30), \
                mock.patch.object(adopter, '_lock_sidecar', side_effect=lock), \
                mock.patch.object(file_ops, 'apply', side_effect=apply_with_adopter):
            rc, out = self.run_cmd(task)
            worker.join(30)
        self.assertFalse(worker.is_alive())
        self.assertEqual((rc, out['status']), (0, 'VERIFIED'))
        self.assertEqual(events, ['B waits', 'A applied', 'TASK_STORE_CONFLICT'])
        self.assertEqual(results, [])
        current = self.store.get(task['task_id'])
        self.assertEqual(current['file_ops_evidence'], out['evidence'])
        self.assertEqual(current['pm_session_id'], task['pm_session_id'])
        self.assert_binding_lock_free()
        # With evidence sealed, a fresh adoption needs no lock and succeeds.
        with self.store.d6_binding_lock():
            adopted = self.adopt(adopter, task['task_id'])
        self.assertNotEqual(adopted['pm_session_id'], task['pm_session_id'])
        self.assertEqual(adopted['file_ops_evidence'], out['evidence'])

    def test_adoption_while_lock_held_past_deadline_refuses_unchanged(self):
        task = self.record_pm()
        with mock.patch.object(task_store, 'TASK_UPDATE_LOCK_TIMEOUT_SECONDS', 0.2):
            with self.store.d6_binding_lock():
                with self.assertRaises(LLMToolError) as ctx:
                    self.adopt(TaskStore(root=self.store.root), task['task_id'])
        self.assertEqual(ctx.exception.task_store_error_code, 'TASK_D6_BINDING_BUSY')
        self.assertEqual(self.store.get(task['task_id']), task)
        self.assert_binding_lock_free()

    def test_adoption_after_reservation_refuses_before_apply(self):
        # Defence in depth: an adoption after admission that skipped the lock
        # (the internal unlocked path) is caught by the recheck before apply.
        task = self.record_pm()
        real_reserve = self.store._reserve_file_ops_d6
        adopted = []

        def reserve_then_adopt(task_id, executor_task_id, *, expected):
            saved = real_reserve(task_id, executor_task_id, expected=expected)
            adopted.append(self.adopt(TaskStore(root=self.store.root), task_id,
                                      locked=False))
            return saved

        with mock.patch.object(self.store, '_reserve_file_ops_d6',
                               side_effect=reserve_then_adopt), \
                mock.patch.object(file_ops, 'apply', side_effect=AssertionError('apply ran')):
            rc, out = self.run_cmd(task)
        self.assertEqual((rc, out['status'], out['code']),
                         (1, 'REFUSED', 'PM_CALLER_NOT_OWNER'))
        current = self.store.get(task['task_id'])
        self.assertEqual(current, adopted[0])
        self.assertEqual(current['file_ops_reservation']['executor_task'], D6)
        self.assertNotIn('file_ops_evidence', current)
        self.assertEqual(self.status(), '')
        self.assert_binding_lock_free()

    def test_binding_lock_released_on_every_path(self):
        task = self.record_pm()
        real_get = self.store.get
        failed = {'state': 'FAILED', 'isolate': True}
        # Refused by admission under the lock.
        with mock.patch.object(self.store, 'get', side_effect=lambda tid: {
                **real_get(tid), 'file_ops_evidence': failed}):
            rc, out = self.run_cmd(task)
        self.assertEqual((rc, out['code']), (1, 'FILE_OPS_EVIDENCE_FAILED'))
        self.assert_binding_lock_free()
        # Refused by plan before any change.
        refused = LLMToolError('refused')
        refused.code = 'FILE_OPS_PATH_INVALID'
        with mock.patch.object(file_ops, 'plan', side_effect=refused):
            rc, out = self.run_cmd(task)
        self.assertEqual((rc, out['code']), (1, 'FILE_OPS_PATH_INVALID'))
        self.assert_binding_lock_free()
        # An unexpected error still releases the lock.
        with mock.patch.object(file_ops, 'plan', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                self.run_cmd(task)
        self.assert_binding_lock_free()
        self.assertEqual(self.status(), '')
        # Applied but the evidence write fails: isolate, and the lock is released.
        with mock.patch.object(self.store, 'record_file_ops_evidence',
                               side_effect=LLMToolError('not stored')):
            rc, out = self.run_cmd(task)
        self.assertEqual((rc, out['code'], out['isolate']),
                         (1, 'FILE_OPS_EVIDENCE_NOT_STORED', True))
        self.assert_binding_lock_free()


if __name__ == '__main__':
    unittest.main()
