"""#1502 file_ops wiring: record-pm field, TaskStore evidence and the delegate gate.

Temporary git repositories and a temporary TaskStore only; no network, no
provider. The Developer route is replaced by a stub that records the D6 state
it would start from.
"""

import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from llmtool import file_ops  # noqa: E402
from llmtool import task_store as task_store_module  # noqa: E402
from llmtool import task_submission_cli as cli  # noqa: E402
from llmtool.errors import LLMToolError  # noqa: E402
from llmtool.task_store import TaskStore  # noqa: E402

import review_trust_fixture  # noqa: E402
import worker_policy_fixture  # noqa: E402

GIT = shutil.which('git')
D6 = 'd6-fops'
MOVE_AND_DELETE = [{'op': 'delete', 'path': 'README.txt'},
                   {'op': 'move', 'from': 'docs/', 'to': 'archive/docs/'}]


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def _env():
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update({'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
                'GIT_AUTHOR_NAME': 'File Ops Test', 'GIT_AUTHOR_EMAIL': 'file-ops@example.invalid',
                'GIT_COMMITTER_NAME': 'File Ops Test',
                'GIT_COMMITTER_EMAIL': 'file-ops@example.invalid'})
    return env


def git(root, *args):
    return subprocess.run(['git', *args], cwd=root, env=_env(), check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout


def pm_input(ops=None, route='codex'):
    assignment = {'route': route, 'risk': 'LOW', 'goal': 'Reorganize the docs tree',
                  'acceptance': ['Docs are archived']}
    if ops is not None:
        assignment['file_ops'] = ops
    response = json.dumps({'schema_version': 1, 'summary': 'Archive docs, then edit.',
                           'developer_assignment': assignment})
    return io.BytesIO(json.dumps({'request': 'Archive the docs tree',
                                  'response': response}).encode('utf-8'))


class _StoreCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = os.path.realpath(tmp.name)
        self.store = TaskStore(Path(self.base) / 'tasks')
        worker_policy_fixture.install(self)

    def record_pm(self, ops=None):
        args = SimpleNamespace(pm_session_id=None, pm_runtime=None, pm_cwd=None)
        return cli.run_task_record_pm(args, store=self.store, input_stream=pm_input(ops),
                                      return_record=True)

    def assertStoreCode(self, code, fn, *args, **kwargs):
        with self.assertRaises(LLMToolError) as ctx:
            fn(*args, **kwargs)
        self.assertEqual(code, getattr(ctx.exception, 'task_store_error_code', None))


class RecordPmFileOpsTests(_StoreCase):
    def test_file_ops_stored_and_bound_into_contract_digest(self):
        saved = self.record_pm(MOVE_AND_DELETE)
        self.assertEqual(MOVE_AND_DELETE, saved['pm_assignment']['file_ops'])
        mark = task_store_module.FILE_OPS_CONTRACT_MARK + file_ops.ops_digest(MOVE_AND_DELETE)
        self.assertTrue(saved['task_contract']['goal'].endswith(mark))
        other = self.record_pm([{'op': 'delete', 'path': 'README.txt'}])
        plain = self.record_pm()
        digests = {saved['task_binding']['contract_digest'],
                   other['task_binding']['contract_digest'],
                   plain['task_binding']['contract_digest']}
        self.assertEqual(3, len(digests))

    def test_legacy_assignment_without_file_ops_unchanged(self):
        saved = self.record_pm()
        self.assertEqual({'schema_version', 'route', 'risk', 'goal', 'acceptance'},
                         set(saved['pm_assignment']))
        self.assertNotIn('file_ops', saved['task_contract']['goal'])
        self.assertNotIn('file_ops_evidence', saved)

    def test_parse_ops_refusal_refuses_record_pm(self):
        cases = (
            ('FILE_OPS_PATH_INVALID', [{'op': 'delete', 'path': '../outside.txt'}]),
            ('FILE_OPS_PATH_INVALID', [{'op': 'delete', 'path': '.git/config'}]),
            ('FILE_OPS_INVALID', []),
            ('FILE_OPS_INVALID', [{'op': 'copy', 'path': 'a.txt'}]),
            ('FILE_OPS_INVALID', json.dumps([{'op': 'delete', 'path': 'a.txt'}])),
        )
        for code, ops in cases:
            with self.subTest(ops=str(ops)[:50]):
                with self.assertRaises(LLMToolError) as ctx:
                    self.record_pm(ops)
                self.assertTrue(str(ctx.exception).startswith(code), str(ctx.exception))

    def test_provider_pm_response_with_file_ops_is_not_an_assignment(self):
        response = pm_input(MOVE_AND_DELETE).getvalue()
        envelope = json.loads(response)
        self.assertIsNone(cli._pm_assignment(envelope['response']))
        self.assertIsNotNone(cli._pm_assignment(envelope['response'], allow_file_ops=True))

    def test_stored_file_ops_cannot_drift_from_contract(self):
        saved = self.record_pm(MOVE_AND_DELETE)
        changed = dict(saved['pm_assignment'],
                       file_ops=[{'op': 'delete', 'path': 'README.txt'}])
        with self.assertRaises(LLMToolError):
            self.store.update(saved['task_id'], pm_assignment=changed)
        self.assertEqual(MOVE_AND_DELETE,
                         self.store.get(saved['task_id'])['pm_assignment']['file_ops'])


@unittest.skipUnless(GIT, 'git is required')
class _DelegateCase(_StoreCase):
    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.base, 'repo')
        self.outside = os.path.join(self.base, 'outside')
        os.makedirs(self.root)
        os.makedirs(self.outside)
        git(self.root, 'init', '-q')
        for rel, text in (('README.txt', 'readme\n'), ('docs/a.txt', 'alpha\n'),
                          ('docs/sub/b.txt', 'bravo\n'), ('src/tool.py', 'print(1)\n')):
            self.write(rel, text)
        self.head = self.commit()
        self.route_calls = []
        self.d6_agent = None

    # -- fixture helpers

    def write(self, rel, text):
        path = os.path.join(self.root, *rel.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(text)

    def commit(self):
        git(self.root, 'add', '-A')
        git(self.root, '-c', 'commit.gpgsign=false', 'commit', '-q', '-m', 'fixture')
        return git(self.root, 'rev-parse', 'HEAD').strip()

    def status(self):
        return git(self.root, 'status', '--porcelain=v1', '--untracked-files=all', '--no-renames')

    def exists(self, rel):
        return os.path.lexists(os.path.join(self.root, *rel.split('/')))

    def commit_count(self):
        return git(self.root, 'rev-list', '--count', 'HEAD').strip()

    def binding(self, _args):
        head = git(self.root, 'rev-parse', 'HEAD').strip()
        return (SimpleNamespace(task_id=D6, root=self.root, trusted_agent_id=self.d6_agent),
                SimpleNamespace(head=head, clean=self.status() == ''))

    def developer_route(self, args, *, store):
        # The Developer would start here: record what its D6 holds.
        self.route_calls.append({'status': self.status(),
                                 'evidence': store.get(args.task_id).get('file_ops_evidence')})
        return 0

    def delegate(self, task_id, *, repair_cap=None, **changes):
        args = SimpleNamespace(task_id=task_id, provider='codex', mode='direct',
                               background=False, timeout=None, executor_task=D6,
                               acceptance=['Docs are archived'], cwd=self.root)
        for name, value in changes.items():
            setattr(args, name, value)
        # The agy/opencode/claude/gw-agent timeout default comes from the host
        # route config, which a CI runner may not have; it is not under test here.
        with mock.patch.object(cli, '_delegate_timeout', return_value=600), \
                mock.patch.object(cli, '_delegate_d6_binding', side_effect=self.binding), \
                mock.patch.object(cli, '_check_delegate_repair_cap', return_value=None,
                                  side_effect=repair_cap), \
                mock.patch.object(cli, 'run_task_codex_delegate',
                                  side_effect=self.developer_route):
            return cli.run_task_delegate(args, store=self.store)

    def assertRefused(self, code, task_id, **changes):
        with self.assertRaises(LLMToolError) as ctx:
            self.delegate(task_id, **changes)
        self.assertTrue(str(ctx.exception).startswith(code), str(ctx.exception))
        self.assertEqual([], self.route_calls)

    def assertUnchanged(self, task_id):
        self.assertEqual('', self.status())
        self.assertEqual(self.head, git(self.root, 'rev-parse', 'HEAD').strip())
        self.assertNotIn('file_ops_evidence', self.store.get(task_id))


class DelegateFileOpsTests(_DelegateCase):
    def test_authorized_delete_and_move_run_before_developer(self):
        task = self.record_pm(MOVE_AND_DELETE)
        self.assertEqual(0, self.delegate(task['task_id']))
        self.assertFalse(self.exists('README.txt'))
        self.assertFalse(self.exists('docs/a.txt'))
        self.assertTrue(self.exists('archive/docs/a.txt'))
        self.assertTrue(self.exists('archive/docs/sub/b.txt'))
        # The Developer started on the applied, verified tree.
        self.assertEqual(1, len(self.route_calls))
        self.assertEqual(self.status(), self.route_calls[0]['status'])
        evidence = self.store.get(task['task_id'])['file_ops_evidence']
        self.assertEqual(evidence, self.route_calls[0]['evidence'])
        self.assertEqual('VERIFIED', evidence['status'])
        self.assertEqual((D6, self.head), (evidence['executor_task'], evidence['head']))
        self.assertEqual(file_ops.ops_digest(MOVE_AND_DELETE), evidence['ops_sha256'])
        self.assertEqual(3, len(evidence['files']))
        # No commit, push or reset: HEAD and history unchanged, changes only staged.
        self.assertEqual(self.head, git(self.root, 'rev-parse', 'HEAD').strip())
        self.assertEqual('1', self.commit_count())
        self.assertEqual({'D  README.txt', 'D  docs/a.txt', 'D  docs/sub/b.txt',
                          'A  archive/docs/a.txt', 'A  archive/docs/sub/b.txt'},
                         set(self.status().splitlines()))

    def test_only_git_rm_and_mv_run(self):
        task = self.record_pm(MOVE_AND_DELETE)
        real = file_ops._mutate
        verbs = []

        def spy(root, args):
            verbs.append(args[0])
            return real(root, args)

        with mock.patch.object(file_ops, '_mutate', side_effect=spy):
            self.delegate(task['task_id'])
        self.assertEqual({'rm', 'mv'}, set(verbs))

    def test_repeat_delegate_is_noop_and_reuses_evidence(self):
        task = self.record_pm(MOVE_AND_DELETE)
        self.delegate(task['task_id'])
        first = self.store.get(task['task_id'])
        status = self.status()
        with mock.patch.object(file_ops, '_mutate', side_effect=AssertionError('no mutation')):
            self.delegate(task['task_id'])
        again = self.store.get(task['task_id'])
        self.assertEqual(first['file_ops_evidence'], again['file_ops_evidence'])
        self.assertEqual(first['record_sha256'], again['record_sha256'])
        self.assertEqual(status, self.status())
        self.assertEqual(2, len(self.route_calls))

    def test_task_without_file_ops_is_untouched(self):
        task = self.record_pm()
        with mock.patch.object(file_ops, 'apply', side_effect=AssertionError('not called')):
            self.assertEqual(0, self.delegate(task['task_id']))
        self.assertUnchanged(task['task_id'])

    def test_overwrite_refused_and_nothing_changes(self):
        self.write('archive/docs/a.txt', 'already here\n')
        self.head = self.commit()
        task = self.record_pm(MOVE_AND_DELETE)
        self.assertRefused('FILE_OPS_PRECHECK_FAILED', task['task_id'])
        self.assertUnchanged(task['task_id'])
        self.assertTrue(self.exists('README.txt'))

    def test_untracked_path_refused_and_nothing_changes(self):
        task = self.record_pm([{'op': 'delete', 'path': 'README.txt'},
                               {'op': 'delete', 'path': 'not-tracked.txt'}])
        self.assertRefused('FILE_OPS_PRECHECK_FAILED', task['task_id'])
        self.assertUnchanged(task['task_id'])

    def test_symlink_escape_refused(self):
        os.symlink(self.outside, os.path.join(self.root, 'out'))
        self.head = self.commit()
        task = self.record_pm([{'op': 'move', 'from': 'README.txt', 'to': 'out/README.txt'}])
        self.assertRefused('FILE_OPS_PRECHECK_FAILED', task['task_id'])
        self.assertUnchanged(task['task_id'])
        self.assertEqual([], os.listdir(self.outside))

    def test_dirty_d6_refused(self):
        task = self.record_pm(MOVE_AND_DELETE)
        self.write('src/tool.py', 'print(2)\n')
        self.assertRefused('FILE_OPS_D6_NOT_CLEAN', task['task_id'])
        self.assertTrue(self.exists('README.txt'))
        self.assertNotIn('file_ops_evidence', self.store.get(task['task_id']))

    def test_mid_run_failure_records_failed_and_blocks_developer_and_publish(self):
        task = self.record_pm(MOVE_AND_DELETE)
        real = file_ops._mutate
        calls = []

        def flaky(root, args):
            calls.append(args[0])
            return False if len(calls) == 2 else real(root, args)

        with mock.patch.object(file_ops, '_mutate', side_effect=flaky):
            self.assertRefused('FILE_OPS_FAILED', task['task_id'])
        saved = self.store.get(task['task_id'])
        evidence = saved['file_ops_evidence']
        self.assertEqual(('FAILED', True, 1, 3),
                         (evidence['status'], evidence['isolate'], evidence['completed'],
                          evidence['total']))
        self.assertEqual(self.head, git(self.root, 'rev-parse', 'HEAD').strip())
        # Developer blocked on every later delegate.
        self.assertRefused('FILE_OPS_ISOLATED', task['task_id'])
        # Publish blocked: the store takes no merge claim or published PR.
        self.assertStoreCode('FILE_OPS_ISOLATED', self.store.update, task['task_id'],
                             published_pr={'pr': 7, 'head_sha': 'a' * 40,
                                           'recorded_at': '2026-11-14T00:00:00Z'})
        self.assertStoreCode('FILE_OPS_ISOLATED', self.store.update, task['task_id'],
                             merge_attempt={'pr': 7, 'head_sha': 'a' * 40, 'state': 'CLAIMED',
                                            'claimed_at': '2026-11-14T00:00:00Z'})
        with_developer = dict(self.store.get(task['task_id']), developer={'state': 'x'})
        self.assertStoreCode('FILE_OPS_ISOLATED', task_store_module._validate_file_ops,
                             with_developer)
        self.assertEqual(saved['record_sha256'],
                         self.store.get(task['task_id'])['record_sha256'])

    # -- scope

    def test_scope_is_exactly_write_paths_plus_op_paths(self):
        task = self.record_pm(MOVE_AND_DELETE)
        write_paths = ['src/tool.py']
        self.assertEqual(frozenset(write_paths), cli._d6_scope(task, write_paths))
        self.delegate(task['task_id'])
        saved = self.store.get(task['task_id'])
        ops_paths = {'README.txt', 'docs/a.txt', 'docs/sub/b.txt',
                     'archive/docs/a.txt', 'archive/docs/sub/b.txt'}
        self.assertEqual(frozenset(write_paths) | ops_paths, cli._d6_scope(saved, write_paths))
        self.assertTrue(cli._d6_scope_ok(self.store, task['task_id'], write_paths,
                                         sorted(ops_paths | {'src/tool.py'})))
        for outside in ('docs/new.txt', 'archive/docs/c.txt', 'src/other.py', 'docs/'):
            with self.subTest(path=outside):
                self.assertFalse(cli._d6_scope_ok(self.store, task['task_id'], write_paths,
                                                  ['src/tool.py', outside]))

    def test_failed_evidence_widens_no_scope(self):
        task = self.record_pm(MOVE_AND_DELETE)
        with mock.patch.object(file_ops, '_mutate', return_value=False):
            self.assertRefused('FILE_OPS_FAILED', task['task_id'])
        saved = self.store.get(task['task_id'])
        self.assertEqual(frozenset(['src/tool.py']), cli._d6_scope(saved, ['src/tool.py']))


class DelegateRefusalLeavesD6UntouchedTests(_DelegateCase):
    """A dispatch refusal comes before file_ops: no D6 change and no evidence."""

    def assertRefusedUntouched(self, code, task_id, **changes):
        with mock.patch.object(file_ops, 'plan', side_effect=AssertionError('plan ran')), \
                mock.patch.object(file_ops, 'apply', side_effect=AssertionError('apply ran')):
            self.assertRefused(code, task_id, **changes)
        self.assertUnchanged(task_id)
        self.assertTrue(self.exists('README.txt'))
        self.assertTrue(self.exists('docs/a.txt'))

    def test_unsupported_route_leaves_d6_unchanged(self):
        task = self.record_pm(MOVE_AND_DELETE)
        cases = (
            ('unsupported Developer route', {'provider': 'nope', 'mode': 'proposal'}),
            ('--mode direct is supported only', {'provider': 'nope', 'mode': 'direct'}),
            ('--provider agy requires --mode direct', {'provider': 'agy', 'mode': 'proposal'}),
            ('--background requires --mode direct',
             {'provider': 'codex', 'mode': 'proposal', 'background': True}),
        )
        for code, changes in cases:
            with self.subTest(code=code):
                self.assertRefusedUntouched(code, task['task_id'], **changes)

    def test_paused_worker_leaves_d6_unchanged(self):
        task = self.record_pm(MOVE_AND_DELETE)
        paused = SimpleNamespace(allowed=False, reason_code='WORKER_PAUSED')
        for key, changes in (('codex-developer', {}),
                             ('opencode-developer', {'provider': 'opencode',
                                                     'mode': 'proposal'})):
            with self.subTest(worker=key):
                with mock.patch.object(cli.worker_dispatch, 'evaluate',
                                       return_value=paused) as evaluate:
                    self.assertRefusedUntouched('WORKER_PAUSED', task['task_id'], **changes)
                self.assertEqual(key, evaluate.call_args[0][0])

    def test_refused_dispatch_leaves_d6_unchanged(self):
        task = self.record_pm(MOVE_AND_DELETE)
        cases = (
            ('D6_SOURCE_BINDING_MISMATCH', {'executor_task': 'd6-other'}),
            ('no matching low-risk codex recommendation',
             {'selection_source': 'PM', 'acceptance': ['Another acceptance item']}),
            ('DELEGATE_REPAIR_CAP_TEST',
             {'repair_cap': LLMToolError('DELEGATE_REPAIR_CAP_TEST')}),
        )
        for code, changes in cases:
            with self.subTest(code=code):
                self.assertRefusedUntouched(code, task['task_id'], **changes)
        self.d6_agent = 'claude-developer'
        self.assertRefusedUntouched('D6_CLAUDE_RESERVED', task['task_id'])

    def test_file_ops_apply_after_dispatch_gate(self):
        task = self.record_pm(MOVE_AND_DELETE)
        order = []
        real_plan = file_ops.plan
        real_evaluate = cli.worker_dispatch.evaluate

        def evaluate(*args, **kwargs):
            # The real (fixture all-ALLOWED) decision; only the call order is recorded.
            order.append('dispatch-gate')
            return real_evaluate(*args, **kwargs)

        def plan(*args, **kwargs):
            order.append('file_ops')
            return real_plan(*args, **kwargs)

        with mock.patch.object(cli.worker_dispatch, 'evaluate', side_effect=evaluate), \
                mock.patch.object(file_ops, 'plan', side_effect=plan):
            self.assertEqual(0, self.delegate(task['task_id']))
        # The dispatch gate decides before file_ops plan; later store writes may
        # consult the policy again, so only the leading order is fixed.
        self.assertEqual(['dispatch-gate', 'file_ops'], order[:2])
        self.assertEqual(1, order.count('file_ops'))
        self.assertEqual(1, len(self.route_calls))
        self.assertEqual('VERIFIED', self.route_calls[0]['evidence']['status'])


class FileOpsEvidenceStoreTests(_DelegateCase):
    """TaskStore accepts the evidence field strictly and writes it once."""

    def verified(self):
        task = self.record_pm(MOVE_AND_DELETE)
        self.delegate(task['task_id'])
        return self.store.get(task['task_id'])

    def test_developer_needs_verified_evidence(self):
        task = self.record_pm(MOVE_AND_DELETE)
        self.assertStoreCode('FILE_OPS_EVIDENCE_REQUIRED', task_store_module._validate_file_ops,
                             dict(task, developer={'state': 'x'}))
        saved = self.verified()
        ok = dict(saved, developer={'source_sha': self.head, 'executor_task_id': D6})
        task_store_module._validate_file_ops(ok)
        for developer in ({'source_sha': 'f' * 40, 'executor_task_id': D6},
                          {'source_sha': self.head, 'executor_task_id': 'd6-other'}):
            with self.subTest(developer=developer):
                self.assertStoreCode('FILE_OPS_EVIDENCE_INVALID',
                                     task_store_module._validate_file_ops,
                                     dict(saved, developer=developer))

    def test_evidence_without_file_ops_refused(self):
        plain = self.record_pm()
        evidence = self.verified()['file_ops_evidence']
        self.assertStoreCode('FILE_OPS_EVIDENCE_INVALID', task_store_module._validate_file_ops,
                             dict(plain, file_ops_evidence=evidence))

    def test_malformed_evidence_refused(self):
        saved = self.verified()
        good = saved['file_ops_evidence']
        bad_file = copy.deepcopy(good)
        bad_file['files'][0]['path'] = '../escape.txt'
        cases = {
            'extra key': dict(good, note='x'),
            'missing key': {k: v for k, v in good.items() if k != 'recorded_at'},
            'unknown status': dict(good, status='APPLIED'),
            'ops digest': dict(good, ops_sha256='0' * 64),
            'plan digest': dict(good, plan_sha256='0' * 64),
            'bad file path': bad_file,
            'bad executor': dict(good, executor_task='D6 Upper'),
            'bad head': dict(good, head='HEAD'),
            'bad time': dict(good, recorded_at='yesterday'),
            'not a dict': ['VERIFIED'],
        }
        for label, value in cases.items():
            with self.subTest(label=label):
                self.assertStoreCode('FILE_OPS_EVIDENCE_INVALID',
                                     task_store_module._validate_file_ops,
                                     dict(saved, file_ops_evidence=value))

    def test_failed_evidence_shape_is_strict(self):
        saved = self.verified()
        good = saved['file_ops_evidence']
        failed = {**{k: good[k] for k in ('executor_task', 'head', 'ops_sha256',
                                          'plan_sha256', 'recorded_at')},
                  'status': 'FAILED', 'isolate': True, 'completed': 1, 'total': 3,
                  'reason': 'git rm failed on file 2'}
        task_store_module._validate_file_ops(dict(saved, file_ops_evidence=failed,
                                                  developer=None))
        for label, value in (('not isolated', dict(failed, isolate=False)),
                             ('completed over total', dict(failed, completed=4)),
                             ('empty reason', dict(failed, reason='')),
                             ('non-ASCII reason', dict(failed, reason='café'))):
            with self.subTest(label=label):
                self.assertStoreCode('FILE_OPS_EVIDENCE_INVALID',
                                     task_store_module._validate_file_ops,
                                     dict(saved, file_ops_evidence=value, developer=None))

    def test_evidence_is_written_once(self):
        saved = self.verified()
        good = saved['file_ops_evidence']
        other = dict(good, recorded_at='2026-11-14T00:00:00Z')
        self.assertStoreCode('FILE_OPS_EVIDENCE_TRANSITION_INVALID', self.store.update,
                             saved['task_id'], file_ops_evidence=other)
        self.assertStoreCode('FILE_OPS_EVIDENCE_TRANSITION_INVALID', self.store.update,
                             saved['task_id'], file_ops_evidence=None)
        self.assertEqual(good, self.store.get(saved['task_id'])['file_ops_evidence'])

    def test_verified_may_turn_failed_only_for_the_same_plan(self):
        check = task_store_module._check_file_ops_transition
        good = self.verified()['file_ops_evidence']
        failed = {**{k: good[k] for k in ('executor_task', 'head', 'ops_sha256',
                                          'plan_sha256', 'recorded_at')},
                  'status': 'FAILED', 'isolate': True, 'completed': 0, 'total': 3,
                  'reason': 'post-apply verification failed'}
        check(None, good)
        check(good, good)
        check(good, failed)
        self.assertStoreCode('FILE_OPS_EVIDENCE_TRANSITION_INVALID', check,
                             good, dict(failed, plan_sha256='1' * 64))
        self.assertStoreCode('FILE_OPS_EVIDENCE_TRANSITION_INVALID', check, failed, good)


if __name__ == '__main__':
    unittest.main()
