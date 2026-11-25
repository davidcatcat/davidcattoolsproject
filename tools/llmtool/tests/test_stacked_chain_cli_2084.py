"""#2084 slice 2: CLI stacked-chain builder and task complete wiring."""

from argparse import Namespace
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from llmtool import skill_gate
from llmtool import task_completion
from llmtool import task_submission_cli
from llmtool.task_store import TaskStore

_ENV = {
    'PATH': '/usr/bin:/bin',
    'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
    'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.invalid',
    'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.invalid',
    'GIT_TERMINAL_PROMPT': '0',
}
_AGENT = 'gw-agent'
_SESSION = 'session-1'
_FINGERPRINT = 'cpython3.13-testbase-local-0123456789ab'


class _DictStore:
    """Minimal in-memory store for _stacked_chain unit tests."""

    def __init__(self, records=None, *, fail_on=()):
        self.records = dict(records or {})
        self.fail_on = set(fail_on)

    def get(self, task_id):
        if task_id in self.fail_on:
            raise RuntimeError('store read failed')
        return self.records[task_id]


def _hex(char):
    return char * 40


def _id(index):
    return f'{index:032x}'


def _make_record(task_id, *, continues=None, source_sha=None, result_head=None,
                 write_paths=None, completion_state='PM_EVIDENCE_READY'):
    record = {
        'task_id': task_id,
        'developer': {
            'source_sha': source_sha,
            'write_paths': write_paths,
        },
        'completion': {
            'state': completion_state,
            'evidence': {'result': {'result_head': result_head}},
        },
    }
    if continues is not None:
        record['continues'] = continues
    return record


class StackedChainBuilderTests(unittest.TestCase):
    def test_no_continues_returns_none(self):
        task = _make_record(_id(1), source_sha=_hex('a'), write_paths=['b.txt'])
        self.assertIsNone(task_submission_cli._stacked_chain(task, _DictStore()))

    def test_three_task_chain_is_root_first(self):
        s0, s1, s2 = _hex('0'), _hex('1'), _hex('2')
        t1 = _make_record(_id(1), source_sha=s0, result_head=s1,
                          write_paths=['a.txt'], completion_state='PM_RETURNED')
        t2 = _make_record(_id(2), continues=_id(1), source_sha=s1,
                          result_head=s2, write_paths=['b.txt'],
                          completion_state='PM_EVIDENCE_READY')
        t3 = _make_record(_id(3), continues=_id(2), source_sha=s2,
                          write_paths=['c.txt'])
        store = _DictStore({_id(1): t1, _id(2): t2, _id(3): t3})
        self.assertEqual(
            task_submission_cli._stacked_chain(t3, store),
            [
                {'source_sha': s0, 'result_sha': s1, 'write_paths': ['a.txt']},
                {'source_sha': s1, 'result_sha': s2, 'write_paths': ['b.txt']},
            ],
        )

    def test_stops_at_first_non_stacked_ancestor(self):
        s0, s1, s2 = _hex('0'), _hex('1'), _hex('2')
        t1 = _make_record(_id(1), source_sha=s0, result_head=s1,
                          write_paths=['a.txt'], completion_state='WAITING_EXECUTOR')
        t2 = _make_record(_id(2), continues=_id(1), source_sha=s1,
                          result_head=s2, write_paths=['b.txt'])
        t3 = _make_record(_id(3), continues=_id(2), source_sha=s2,
                          write_paths=['c.txt'])
        store = _DictStore({_id(1): t1, _id(2): t2, _id(3): t3})
        self.assertEqual(
            task_submission_cli._stacked_chain(t3, store),
            [{'source_sha': s1, 'result_sha': s2, 'write_paths': ['b.txt']}],
        )

    def test_non_stacked_first_predecessor_returns_none(self):
        s0, s1 = _hex('0'), _hex('1')
        cases = [
            ('incomplete', _make_record(_id(1), source_sha=s0, result_head=s1,
                                        write_paths=['a.txt'],
                                        completion_state='WAITING_EXECUTOR')),
            ('head mismatch', _make_record(_id(1), source_sha=s0, result_head=_hex('9'),
                                           write_paths=['a.txt'])),
            ('bad task_id', _make_record(_id(99), source_sha=s0, result_head=s1,
                                         write_paths=['a.txt'])),
            ('empty write_paths', _make_record(_id(1), source_sha=s0, result_head=s1,
                                               write_paths=[])),
            ('bad source_sha', _make_record(_id(1), source_sha='bad', result_head=s1,
                                            write_paths=['a.txt'])),
        ]
        t2 = _make_record(_id(2), continues=_id(1), source_sha=s1, write_paths=['b.txt'])
        for label, pred in cases:
            with self.subTest(case=label):
                store = _DictStore({_id(1): pred, _id(2): t2})
                self.assertIsNone(task_submission_cli._stacked_chain(t2, store))

    def test_cycle_depth_limit_and_exception_return_none(self):
        s0, s1, s2 = _hex('0'), _hex('1'), _hex('2')
        # Cycle: t3 -> t2 -> t1 -> t2
        t1 = _make_record(_id(1), continues=_id(2), source_sha=s0,
                          result_head=s1, write_paths=['a.txt'])
        t2 = _make_record(_id(2), continues=_id(1), source_sha=s1,
                          result_head=s2, write_paths=['b.txt'])
        t3 = _make_record(_id(3), continues=_id(2), source_sha=s2,
                          write_paths=['c.txt'])
        self.assertIsNone(task_submission_cli._stacked_chain(
            t3, _DictStore({_id(1): t1, _id(2): t2, _id(3): t3})))

        # Depth above _CONTINUATION_LINEAGE_MAX -> None.
        limit = task_submission_cli._CONTINUATION_LINEAGE_MAX
        records = {}
        for index in range(1, limit + 3):
            prev = _id(index - 1) if index > 1 else None
            src = f'{index - 1:040x}'
            res = f'{index:040x}'
            records[_id(index)] = _make_record(
                _id(index), continues=prev, source_sha=src, result_head=res,
                write_paths=['a.txt'])
        deep_task = records[_id(limit + 2)]
        self.assertIsNone(task_submission_cli._stacked_chain(
            deep_task, _DictStore(records)))

        # Store read exception -> None (never raises).
        t2_err = _make_record(_id(2), continues=_id(1), source_sha=s1,
                              write_paths=['b.txt'])
        self.assertIsNone(task_submission_cli._stacked_chain(
            t2_err, _DictStore(fail_on={_id(1)})))

    def test_real_pm_evidence_ready_shape_and_evidence_level_ignored(self):
        s0, s1 = _hex('0'), _hex('1')
        real_pred = {
            'task_id': _id(1),
            'developer': {'source_sha': s0, 'write_paths': ['a.txt']},
            'completion': {
                'state': 'PM_EVIDENCE_READY',
                'executor_task_id': 'd6-1',
                'reviewer_task_id': 'rev-1',
                'missing_gates': [],
                'pm_dispatch': 'NOT_STARTED',
                'evidence': {
                    'task_id': _id(1),
                    'run_id': _id(101),
                    'contract_digest': 'a' * 64,
                    'execution_mode': 'DIRECT_D6',
                    'source_sha': s0,
                    'result_head': s0,
                    'result': {
                        'executor_task_id': 'd6-1',
                        'source_sha': s0,
                        'result_head': s1,
                        'changed_paths': ['a.txt'],
                        'developer_changed_paths': ['a.txt'],
                        'diff_sha256': 'b' * 64,
                    },
                    'tests': [],
                    'review_pr_ci': {'formal_review_state': 'ACCEPTED'},
                },
            },
        }
        t2 = _make_record(_id(2), continues=_id(1), source_sha=s1, write_paths=['b.txt'])
        self.assertEqual(
            task_submission_cli._stacked_chain(
                t2, _DictStore({_id(1): real_pred, _id(2): t2})),
            [{'source_sha': s0, 'result_sha': s1, 'write_paths': ['a.txt']}],
        )

        evidence_only = {
            'task_id': _id(1),
            'developer': {'source_sha': s0, 'write_paths': ['a.txt']},
            'completion': {
                'state': 'PM_EVIDENCE_READY',
                'evidence': {'result_head': s1},
            },
        }
        bad_cases = [
            ('evidence-level only', evidence_only),
            ('evidence not dict', {
                'task_id': _id(1),
                'developer': {'source_sha': s0, 'write_paths': ['a.txt']},
                'completion': {'state': 'PM_EVIDENCE_READY', 'evidence': [s1]},
            }),
            ('result not dict', {
                'task_id': _id(1),
                'developer': {'source_sha': s0, 'write_paths': ['a.txt']},
                'completion': {'state': 'PM_EVIDENCE_READY', 'evidence': {'result': [s1]}},
            }),
            ('result_head not 40-hex', {
                'task_id': _id(1),
                'developer': {'source_sha': s0, 'write_paths': ['a.txt']},
                'completion': {
                    'state': 'PM_EVIDENCE_READY',
                    'evidence': {'result': {'result_head': '0' * 39}},
                },
            }),
        ]
        for label, pred in bad_cases:
            with self.subTest(case=label):
                self.assertIsNone(task_submission_cli._stacked_chain(
                    t2, _DictStore({_id(1): pred, _id(2): t2})))


class StackedChainCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base_dir = Path(temporary.name).resolve()
        self.root = str(self.base_dir / 'repo')
        os.makedirs(self.root, mode=0o700)
        self.store = TaskStore(self.base_dir / 'store')
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 't')
        self.git('config', 'user.email', 't@example.invalid')
        self.write('.gitignore', 'tools/\ntools/llmtool/.d6-bootstrap-report.json\n')
        self.write('a.txt', 'header\nmiddle\nkeep1\nkeep2\nkeep3\nfooter\n')
        self.write('b.txt', 'b0\n')
        self.write('c.txt', 'c0\n')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'base')
        self.base = self.git('rev-parse', 'HEAD')
        self.common = self.git('rev-parse', '--path-format=absolute',
                               '--git-common-dir')

    def git_run(self, *args, check=True):
        return subprocess.run(
            ['/usr/bin/git', '-C', self.root, *args], env=_ENV, check=check,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)

    def git(self, *args):
        return self.git_run(*args).stdout.strip()

    def write(self, relative, content):
        path = Path(self.root).joinpath(*relative.split('/'))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')

    def commit_file(self, relative, content, message):
        self.write(relative, content)
        self.git('add', '-A')
        self.git('commit', '-q', '-m', message)
        return self.git('rev-parse', 'HEAD')

    def branch(self):
        return self.git('branch', '--show-current')

    def register_d6(self, d6_id, *, source, head, branch):
        registry = Path(self.common) / 'llmtool-worktrees'
        registry.mkdir(mode=0o700, exist_ok=True)
        os.chmod(registry, 0o700)
        identity = os.stat(self.root)
        report_path = (Path(self.root) / 'tools' / 'llmtool' /
                       '.d6-bootstrap-report.json')
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report = {
            'result': 'PASS', 'task_id': d6_id, 'root_realpath': self.root,
            'head_sha': head, 'tooling_sha': source,
            'cache_fingerprint': _FINGERPRINT,
        }
        raw = json.dumps(report, sort_keys=True).encode('utf-8')
        report_path.write_bytes(raw)
        os.chmod(report_path, 0o600)
        marker = {
            'schema_version': 1, 'result': 'PASS', 'task_id': d6_id,
            'root_realpath': self.root, 'submitted_head_sha': head,
            'current_head_sha': head, 'tooling_sha': source,
            'environment_fingerprint': _FINGERPRINT,
            'report_sha256': hashlib.sha256(raw).hexdigest(),
        }
        record = {
            'schema_version': 1, 'task_id': d6_id, 'state': 'ACTIVE',
            'lock_state': 'LOCKED', 'git_common_dir': self.common,
            'trusted_agent_id': _AGENT, 'session_ref': _SESSION,
            'root_realpath': self.root, 'branch': branch,
            'base_sha': source, 'head_sha': head,
            'physical_identity': {
                'device': identity.st_dev, 'inode': identity.st_ino,
                'uid': identity.st_uid,
            },
            'env2_pass_marker': marker,
        }
        record_path = registry / (d6_id + '.json')
        record_path.write_text(json.dumps(record), encoding='utf-8')
        os.chmod(record_path, 0o600)

    def artifact(self, source, head, trusted_base_sha):
        tip = task_completion._developer_tip(
            self.root, source, head, trusted_base_sha)
        raw = task_completion._git(
            self.root, ('diff', '--binary', '--no-ext-diff', '--no-textconv',
                        '--no-renames', source, tip, '--'),
            limit=task_completion._GIT_LIMIT)
        text = raw.decode('utf-8')
        return text, hashlib.sha256(text.encode('utf-8')).hexdigest()

    def create_task(self, task_id, *, d6_id, source, branch, write_paths,
                    artifact=None, artifact_sha256=None, continues=None,
                    completion_state='WAITING_EXECUTOR', result_head=None,
                    claude_route=False):
        run_id = _id(100 + int(task_id, 16))
        contract = task_submission_cli._contract(task_id, '0' * 64, continues=continues)
        record = task_submission_cli._record(
            task_id, run_id, 'pm-' + _id(999), 'active-pm-runtime', 'goal',
            state='PM_RESPONDED', provider_type='active-pm-runtime', model=None,
            backend='ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL', request_sha='0' * 64,
            contract=contract)
        record['response'] = json.dumps({
            'schema_version': 1, 'summary': 'ok', 'developer_assignment': None})
        record['pm_route_binding'] = {
            'type': 'active-pm-runtime', 'model': None, 'base_url': None}
        record['pm_provenance'] = {
            'source': 'ACTIVE_PM_RUNTIME', 'provider_invoked': False,
            'native_session_id_status': 'UNAVAILABLE', 'usage_status': 'UNKNOWN',
            'session_restored': False, 'automatic_callback': False,
        }
        if continues is not None:
            record['continues'] = continues
        developer = {
            'assignment_id': _id(200 + int(task_id, 16)),
            'selection_source': 'PM',
            'state': 'DEVELOPER_RESPONDED',
            'provider_profile': 'claude' if claude_route else 'gw-agent',
            'provider_type': 'claude-cli' if claude_route else 'gw-agent-cli',
            'model': 'test-model',
            'backend_session_semantics': (
                'NO_NATIVE_SESSION_ID_EXPOSED' if claude_route
                else 'CLI_REPORTED_SESSION_ID'),
            'session_id': 'dev-' + _id(300 + int(task_id, 16)),
            'source_sha': source,
            'source_sha_binding': 'D6_REGISTRY_OBSERVED',
            'read_paths': list(write_paths),
            'write_paths': list(write_paths),
            'acceptance': ['ok'],
            'context_sha256': 'c' * 64,
            'egress_basis': 'test',
            'input_bytes': 10,
            'max_tokens': None,
            'artifact_format': 'unified_diff',
            'artifact': None if claude_route else artifact,
            'artifact_sha256': None if claude_route else artifact_sha256,
            'usage': None,
            'error_code': None,
            'created_at': '2026-11-25T00:00:00Z',
        }
        if claude_route:
            developer['executor_task_id'] = d6_id
            developer['claude_result'] = {
                'executor_task_id': d6_id,
                'head_sha': result_head or source,
            }
        evidence = {
            'task_id': task_id,
            'run_id': run_id,
            'contract_digest': record['task_binding']['contract_digest'],
            'execution_mode': 'DIRECT_D6',
            'root': self.root,
            'common_dir': self.common,
            'branch': branch,
            'source_sha': source,
            'result_head': source,
            'executor_identity': _AGENT,
            'executor_session_ref': _SESSION,
            'executor_native_session_id': None,
            'executor_native_session': 'UNKNOWN',
        }
        if result_head is not None:
            evidence['result'] = {'result_head': result_head}
        record['developer'] = developer
        record['completion'] = {
            'state': completion_state,
            'executor_task_id': d6_id,
            'reviewer_task_id': None,
            'evidence': evidence,
            'missing_gates': [],
            'pm_dispatch': 'NOT_STARTED',
            'pm_response': None,
            'pm_recommendation': None,
            'pm_usage': None,
            'pm_error_code': None,
        }
        self.store.create(record)
        return self.store.get(task_id)

    def _run_complete(self, task_id, d6_id, *, main_sha):
        args = Namespace(task_id=task_id, executor_task=d6_id,
                         reviewer_task=None, pr=None)
        fake_tests = lambda **kw: [{
            'path': 'tools/llmtool/tests/test_stacked_chain_cli_2084.py',
            'exit_code': 0, 'source_sha': kw['result_sha'],
            'output_sha256': '0' * 64,
        }]
        with mock.patch.object(task_completion, 'read_authenticated_main_sha',
                               return_value=main_sha), \
                mock.patch.object(task_completion, '_trusted_slug',
                                  return_value='owner/repo'), \
                mock.patch.object(task_completion, 'run_bounded_tests',
                                  side_effect=fake_tests), \
                mock.patch.object(skill_gate, 'check_completion'), \
                contextlib.redirect_stdout(io.StringIO()):
            return task_submission_cli.run_task_complete(args, store=self.store)

    def _two_step_with_update_branch(self):
        self.git('checkout', '-q', '-b', 'task1')
        task1 = self.commit_file(
            'a.txt', 'header\ntask1\nkeep1\nkeep2\nkeep3\nfooter\n', 'task1')
        self.git('checkout', '-q', '-b', 'task2')
        self.commit_file('b.txt', 'b1\n', 'task2')
        self.git('checkout', '-q', 'main')
        main_sha = self.commit_file(
            'a.txt', 'header\nmiddle\nkeep1\nkeep2\nkeep3\nmain\n', 'main moves')
        self.git('checkout', '-q', 'task2')
        self.git('merge', '-q', '--no-edit', 'main')
        return task1, main_sha, self.git('rev-parse', 'HEAD')

    def test_stacked_two_tasks_update_branch_complete_accepts_and_persists(self):
        task1, main_sha, head = self._two_step_with_update_branch()
        branch = self.branch()
        d6_id = 'stacked-2084-task2'
        self.register_d6(d6_id, source=task1, head=head, branch=branch)
        art, digest = self.artifact(task1, head, main_sha)

        self.create_task(
            _id(1), d6_id='stacked-2084-task1', source=self.base, branch='task1',
            write_paths=['a.txt'], completion_state='PM_EVIDENCE_READY',
            result_head=task1)
        self.create_task(
            _id(2), d6_id=d6_id, source=task1, branch=branch,
            write_paths=['b.txt'], artifact=art, artifact_sha256=digest,
            continues=_id(1))

        # Negative control: when the builder gives None, strict check refuses.
        self.create_task(
            _id(3), d6_id=d6_id, source=task1, branch=branch,
            write_paths=['b.txt'], artifact=art, artifact_sha256=digest,
            continues=_id(1))
        with mock.patch.object(task_submission_cli, '_stacked_chain',
                               return_value=None):
            rc_neg = self._run_complete(_id(3), d6_id, main_sha=main_sha)
        self.assertEqual(rc_neg, 1)
        self.assertEqual(
            self.store.get(_id(3))['completion']['missing_gates'],
            ['RESULT_SCOPE_MISMATCH'])

        # Real TaskStore write and read-back with stacked_chain_used + stacked_chain.
        rc = self._run_complete(_id(2), d6_id, main_sha=main_sha)
        self.assertEqual(rc, 0)
        saved = self.store.get(_id(2))
        self.assertEqual(saved['completion']['state'], 'PM_EVIDENCE_READY')
        result = saved['completion']['evidence']['result']
        self.assertIs(result['stacked_chain_used'], True)
        self.assertEqual(
            result['stacked_chain'],
            [{'source_sha': self.base, 'result_sha': task1, 'write_paths': ['a.txt']}])

    def test_stacked_merge_path_outside_chain_refuses(self):
        self.git('checkout', '-q', '-b', 'task1')
        task1 = self.commit_file('a.txt', 'task1\n', 'task1 a')
        self.git('checkout', '-q', '-b', 'task2')
        self.commit_file('b.txt', 'b1\n', 'task2 b')
        self.git('checkout', '-q', 'main')
        main_sha = self.commit_file('a.txt', 'main a\n', 'main a')
        self.git('checkout', '-q', 'task2')
        self.git_run('merge', '--no-edit', 'main', check=False)
        self.write('a.txt', 'resolved merge\n')
        self.write('d.txt', 'd\n')
        self.git('add', 'a.txt', 'd.txt')
        self.git('commit', '-q', '--no-edit')
        head = self.git('rev-parse', 'HEAD')
        branch = self.branch()
        d6_id = 'stacked-2084-outside'
        self.register_d6(d6_id, source=task1, head=head, branch=branch)
        art, digest = self.artifact(task1, head, main_sha)

        self.create_task(
            _id(1), d6_id='stacked-2084-task1', source=self.base, branch='task1',
            write_paths=['a.txt'], completion_state='PM_RETURNED',
            result_head=task1)
        self.create_task(
            _id(2), d6_id=d6_id, source=task1, branch=branch,
            write_paths=['b.txt'], artifact=art, artifact_sha256=digest,
            continues=_id(1))
        rc = self._run_complete(_id(2), d6_id, main_sha=main_sha)
        self.assertEqual(rc, 1)
        self.assertEqual(
            self.store.get(_id(2))['completion']['missing_gates'],
            ['RESULT_SCOPE_MISMATCH'])

    def test_broken_link_omits_chain_and_refuses(self):
        task1, main_sha, head = self._two_step_with_update_branch()
        branch = self.branch()
        d6_id = 'stacked-2084-broken'
        self.register_d6(d6_id, source=task1, head=head, branch=branch)
        art, digest = self.artifact(task1, head, main_sha)

        self.create_task(
            _id(1), d6_id='stacked-2084-task1', source=self.base, branch='task1',
            write_paths=['a.txt'], completion_state='PM_EVIDENCE_READY',
            result_head=_hex('f'))
        self.create_task(
            _id(2), d6_id=d6_id, source=task1, branch=branch,
            write_paths=['b.txt'], artifact=art, artifact_sha256=digest,
            continues=_id(1))
        self.assertIsNone(
            task_submission_cli._stacked_chain(self.store.get(_id(2)), self.store))
        rc = self._run_complete(_id(2), d6_id, main_sha=main_sha)
        self.assertEqual(rc, 1)
        self.assertEqual(
            self.store.get(_id(2))['completion']['missing_gates'],
            ['RESULT_SCOPE_MISMATCH'])

    def test_predecessor_not_completed_omits_chain_and_refuses(self):
        task1, main_sha, head = self._two_step_with_update_branch()
        branch = self.branch()
        d6_id = 'stacked-2084-incomplete'
        self.register_d6(d6_id, source=task1, head=head, branch=branch)
        art, digest = self.artifact(task1, head, main_sha)

        self.create_task(
            _id(1), d6_id='stacked-2084-task1', source=self.base, branch='task1',
            write_paths=['a.txt'], completion_state='WAITING_EXECUTOR',
            result_head=task1)
        self.create_task(
            _id(2), d6_id=d6_id, source=task1, branch=branch,
            write_paths=['b.txt'], artifact=art, artifact_sha256=digest,
            continues=_id(1))
        self.assertIsNone(
            task_submission_cli._stacked_chain(self.store.get(_id(2)), self.store))
        rc = self._run_complete(_id(2), d6_id, main_sha=main_sha)
        self.assertEqual(rc, 1)
        self.assertEqual(
            self.store.get(_id(2))['completion']['missing_gates'],
            ['RESULT_SCOPE_MISMATCH'])

    def test_recording_stub_omits_stacked_chain_when_not_stacked(self):
        s0, s1, s2 = _hex('0'), _hex('1'), _hex('2')
        self.create_task(
            _id(1), d6_id='d6-1', source=s0, branch='task1',
            write_paths=['a.txt'], completion_state='PM_EVIDENCE_READY',
            result_head=s1)
        # 1. no-continues
        self.create_task(
            _id(2), d6_id='d6-2', source=s1, branch='task2',
            write_paths=['b.txt'], artifact='diff', artifact_sha256='0' * 64)
        # 2. cross-D6-not-stacked (continues points to incomplete task)
        self.create_task(
            _id(3), d6_id='d6-3', source=s0, branch='task3',
            write_paths=['c.txt'], completion_state='WAITING_EXECUTOR',
            result_head=s1)
        self.create_task(
            _id(4), d6_id='d6-4', source=s1, branch='task4',
            write_paths=['b.txt'], artifact='diff', artifact_sha256='0' * 64,
            continues=_id(3))
        # 3. same-D6-segments (artifact_segments is not None)
        self.create_task(
            _id(5), d6_id='d6-5', source=s1, branch='task5',
            write_paths=['b.txt'], artifact='diff', artifact_sha256='0' * 64,
            continues=_id(1))

        recorded_kwargs = []

        def fake_collector(**kwargs):
            recorded_kwargs.append(dict(kwargs))
            return {
                'root': self.root, 'common_dir': self.common,
                'branch': kwargs['expected_branch'], 'source_sha': kwargs['source_sha'],
                'result_head': s2, 'changed_paths': list(kwargs['write_paths']),
                'developer_changed_paths': list(kwargs['write_paths']),
                'diff_sha256': '1' * 64, 'executor_identity': _AGENT,
            }

        with mock.patch.object(task_completion, 'collect_executor_result',
                               side_effect=fake_collector), \
                mock.patch.object(task_submission_cli, '_completion_repair_artifact_segments',
                                  side_effect=[None, None, [('a', 'd', s1, s2)]]):
            for task_id, d6_id in ((_id(2), 'd6-2'), (_id(4), 'd6-4'), (_id(5), 'd6-5')):
                self.assertEqual(self._run_complete(task_id, d6_id, main_sha=s0), 0)

        self.assertEqual(len(recorded_kwargs), 3)
        for call_kwargs in recorded_kwargs:
            self.assertNotIn('stacked_chain', call_kwargs)
        self.assertNotIn('artifact_segments', recorded_kwargs[0])
        self.assertNotIn('artifact_segments', recorded_kwargs[1])
        self.assertIn('artifact_segments', recorded_kwargs[2])

        # _repair_head_admitted also passes no stacked_chain keyword.
        repair_kwargs = []

        def fake_repair_collector(**kwargs):
            repair_kwargs.append(dict(kwargs))
            return {'result_head': s2}

        task5 = self.store.get(_id(5))
        task5['review_repair_claims'] = [{'repair_task_id': _id(6)}]
        with mock.patch.object(task_completion, 'read_authenticated_main_sha',
                               return_value=s0), \
                mock.patch.object(task_completion, 'ensure_main_commit',
                                  return_value=True), \
                mock.patch.object(task_submission_cli, '_completion_repair_artifact_segments',
                                  return_value=[('a', 'd', s1, s2)]), \
                mock.patch.object(task_completion, 'collect_executor_result',
                                  side_effect=fake_repair_collector):
            admitted = task_submission_cli._repair_head_admitted(
                task5, self.store, task5['completion']['evidence'],
                executor_task='d6-5', head=s2)
        self.assertTrue(admitted)
        self.assertEqual(len(repair_kwargs), 1)
        self.assertNotIn('stacked_chain', repair_kwargs[0])
        self.assertIn('artifact_segments', repair_kwargs[0])

        # _repair_head_admitted also passes no stacked_chain keyword to collect_claude_result.
        self.create_task(
            _id(6), d6_id='d6-6', source=s1, branch='task6',
            write_paths=['b.txt'], continues=_id(1), result_head=s2,
            claude_route=True)
        claude_repair_kwargs = []

        def fake_claude_repair_collector(**kwargs):
            claude_repair_kwargs.append(dict(kwargs))
            return {'result_head': s2}

        task6 = self.store.get(_id(6))
        task6['review_repair_claims'] = [{'repair_task_id': _id(7)}]
        with mock.patch.object(task_completion, 'read_authenticated_main_sha',
                               return_value=s0), \
                mock.patch.object(task_completion, 'ensure_main_commit',
                                  return_value=True), \
                mock.patch.object(task_submission_cli, '_completion_repair_artifact_segments',
                                  return_value=[('a', 'd', s1, s2)]), \
                mock.patch.object(task_completion, 'collect_claude_result',
                                  side_effect=fake_claude_repair_collector):
            claude_admitted = task_submission_cli._repair_head_admitted(
                task6, self.store, task6['completion']['evidence'],
                executor_task='d6-6', head=s2)
        self.assertTrue(claude_admitted)
        self.assertEqual(len(claude_repair_kwargs), 1)
        self.assertNotIn('stacked_chain', claude_repair_kwargs[0])
        self.assertIn('artifact_segments', claude_repair_kwargs[0])

    def test_claude_route_passes_stacked_chain_only_when_stacked(self):
        s0, s1, s2 = _hex('0'), _hex('1'), _hex('2')
        self.create_task(
            _id(1), d6_id='d6-1', source=s0, branch='task1',
            write_paths=['a.txt'], completion_state='PM_RETURNED',
            result_head=s1)
        self.create_task(
            _id(2), d6_id='d6-2', source=s1, branch='task2',
            write_paths=['b.txt'], continues=_id(1), result_head=s2,
            claude_route=True)
        self.create_task(
            _id(3), d6_id='d6-3', source=s1, branch='task3',
            write_paths=['b.txt'], result_head=s2, claude_route=True)

        recorded = []

        def fake_claude_collector(**kwargs):
            recorded.append(dict(kwargs))
            return {
                'root': self.root, 'common_dir': self.common,
                'branch': kwargs['expected_branch'], 'source_sha': kwargs['source_sha'],
                'result_head': s2, 'changed_paths': ['b.txt'],
                'developer_changed_paths': ['b.txt'],
                'diff_sha256': '1' * 64, 'executor_identity': _AGENT,
            }

        with mock.patch.object(task_completion, 'collect_claude_result',
                               side_effect=fake_claude_collector), \
                mock.patch.object(task_submission_cli, '_completion_repair_artifact_segments',
                                  return_value=None):
            self.assertEqual(self._run_complete(_id(2), 'd6-2', main_sha=s0), 0)
            self.assertEqual(self._run_complete(_id(3), 'd6-3', main_sha=s0), 0)

        self.assertEqual(len(recorded), 2)
        self.assertEqual(
            recorded[0]['stacked_chain'],
            [{'source_sha': s0, 'result_sha': s1, 'write_paths': ['a.txt']}])
        self.assertNotIn('stacked_chain', recorded[1])


if __name__ == '__main__':
    unittest.main()
