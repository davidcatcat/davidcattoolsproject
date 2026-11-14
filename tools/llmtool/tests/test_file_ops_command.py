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
        self.assertEqual(sorted(self.status().splitlines()), ['A  docs/a.txt', 'D  a.txt'])
        self.assertEqual(git(self.root, 'rev-parse', 'HEAD').strip(), self.head)
        rc, out = self.run_cmd(task)
        self.assertEqual((rc, out['status'], out['noop']), (0, 'VERIFIED', True))
        self.assertEqual(self.store.get(task['task_id'])['record_sha256'],
                         saved['record_sha256'])
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


if __name__ == '__main__':
    unittest.main()
