"""Offline composition tests. No provider, credential file or peer-root access."""
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from llmtool import opencode_foreground_host as h
from llmtool.run_budget import RunBudget, UsageLedger

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import test_opencode_worker as fixtures


class CompositionTests(unittest.TestCase):
    def fixture(self):
        case = fixtures.OpenCodeWorkerTests()
        self.addCleanup(case.doCleanups)
        job = case.job()
        (Path(job.runtime.state_root) / 'config/service.json').write_text('{"disabled":true}')
        policy = h.HostPolicy(job.agent.root, job.task.contract_digest, ('PUBLIC.txt',))
        a = h.Admission(job.digest, 'synthetic-current-session', 'accepted-c3', 'accepted-egress',
            'atomic-native-job-policy', 'feat/developer', job.source_sha, job.runtime.executable_sha256)
        snap = (job.source_sha, 'feat/developer', b'INDEX', b'', {'PUBLIC.txt': (0o644, 'before')})
        return job, policy, a, snap

    def test_actual_composition_admission_consumption_and_source_checks(self):
        job, policy, a, snap = self.fixture()
        host = h.Host(policy, a, {'synthetic': 'never real'})

        def git(root, *args):
            return ((job.agent.common_dir if args[-1] == '--git-common-dir' else policy.root)
                    + '\n').encode()

        with patch.object(h, 'git', side_effect=git), patch.object(h, 'snapshot', return_value=snap):
            token = host.admit(job, h.hashlib.sha256(h.canonical(h.w.configuration(job))).hexdigest())
            host.check(token, job, 'before')
            host.consume(token, job)
            with self.assertRaises(h.w.WorkerError):
                host.consume(token, job)
            changed = (*snap[:4], {'PUBLIC.txt': (0o644, 'after')})
            with patch.object(h, 'snapshot', return_value=changed):
                host.check(token, job, 'after')
                with self.assertRaises(h.w.WorkerError):
                    host.check(token, job, 'before-client')
            drift = (*snap[:4], {'PUBLIC.txt': (0o600, 'after')})
            with patch.object(h, 'snapshot', return_value=drift):
                with self.assertRaises(h.w.WorkerError):
                    host.check(token, job, 'after')
            host.finalize(token, 'OPENCODE_SOURCE_UNKNOWN', True)
            self.assertIsNone(host.credential)

    def test_policy_binds_root_contract_and_file_allowance(self):
        # The policy is the ONLY source of the accepted root/contract/allowance:
        # a mismatched root, contract, or out-of-allowance job must be refused.
        for field, value in (('root', '/tmp/not-the-registered-root'),
                             ('contract', 'f' * 64)):
            with self.subTest(field=field):
                job, policy, a, snap = self.fixture()
                policy = replace(policy, **{field: value})
                host = h.Host(policy, a, {'synthetic': 'never real'})
                with patch.object(h, 'snapshot', return_value=snap):
                    with self.assertRaises(h.w.WorkerError):
                        host.admit(job, h.hashlib.sha256(h.canonical(h.w.configuration(job))).hexdigest())
        # A read/write path outside the screened allowance is refused.
        job, policy, a, snap = self.fixture()
        job = replace(job, write_paths=('UNAPPROVED.txt',))
        a = replace(a, job_digest=job.digest)
        host = h.Host(policy, a, {'synthetic': 'never real'})
        with patch.object(h, 'snapshot', return_value=snap):
            with self.assertRaises(h.w.WorkerError):
                host.admit(job, h.hashlib.sha256(h.canonical(h.w.configuration(job))).hexdigest())

    def test_nested_native_read_instructions_block_without_reading_contents(self):
        job, policy, a, snap = self.fixture()
        root = Path(job.agent.root)
        nested = root / 'nested'
        nested.mkdir(mode=0o700)
        job = replace(job, read_paths=('nested/PUBLIC.txt',))
        h.no_implicit_instructions(job)
        (nested / 'AGENTS.md').write_text('SYNTHETIC_UNAPPROVED_INSTRUCTION')
        with patch.object(Path, 'read_text', side_effect=AssertionError('must not read instruction')):
            with self.assertRaises(h.w.WorkerError):
                h.no_implicit_instructions(job)

    def test_native_preparation_validates_rules_free_model_and_discards_auth_response(self):
        for bad in (False, True):
            with self.subTest(bad=bad):
                job, policy, a, snap = self.fixture()
                host = h.Host(policy, a, {'synthetic': 'NOT_REAL'})
                token = object()
                host.token = token
                host.consumed = True
                routes = []

                class Response:
                    def __init__(self, data, secret=False):
                        self.data = data
                        self.secret = secret

                    def __enter__(self):
                        return self

                    def __exit__(self, *args):
                        pass

                    def read(self, n):
                        if self.secret:
                            raise AssertionError('credential response must not be read')
                        return json.dumps({'data': self.data}).encode()

                class Opener:
                    def open(self, req, timeout):
                        route = req.full_url.split('127.0.0.1:12345')[-1]
                        routes.append(route)
                        if route == '/api/model':
                            return Response([{'providerID': 'opencode', 'id': job.model.split('/')[1],
                                'enabled': True, 'cost': [{'input': 0, 'output': 0,
                                'cache': {'read': 0, 'write': 0}}]}])
                        if route == '/api/agent':
                            rules = h.w.configuration(job)['permissions']
                            if bad:
                                rules = rules + [{'action': 'shell', 'resource': '*', 'effect': 'allow'}]
                            return Response([{'id': h.w.AGENT, 'steps': job.max_steps,
                                'model': {'providerID': 'opencode', 'id': job.model.split('/')[1]},
                                'permissions': rules}])
                        if route == '/api/session':
                            return Response({'id': 'ses_syntheticProof'})
                        if route.endswith('/permission'):
                            query = json.loads(req.data)
                            action = query['action']
                            resource = query['resources'][0]
                            allow = (action == 'read' and resource in job.read_paths or
                                     action == 'edit' and resource in job.write_paths)
                            return Response({'effect': 'allow' if allow else 'deny'})
                        if route == '/api/credential':
                            return Response(None, secret=True)
                        raise AssertionError(route)

                with patch.object(h.urllib.request, 'build_opener', return_value=Opener()):
                    if bad:
                        with self.assertRaises(h.w.WorkerError):
                            host.prepare_server(token, job, 'http://127.0.0.1:12345', 'SYNTHETIC',
                                                h.time.monotonic() + 10)
                        self.assertNotIn('/api/credential', routes)
                    else:
                        host.prepare_server(token, job, 'http://127.0.0.1:12345', 'SYNTHETIC',
                                            h.time.monotonic() + 10)
                        self.assertEqual(routes.count('/api/credential'), 1)
                        self.assertIsNone(host.credential)

    def test_build_constructs_policy_bound_job_and_refuses_source_drift(self):
        job0, policy, a, snap = self.fixture()
        tmp = tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(tmp.cleanup)
        values = dict(policy=policy, run_id='a' * 32,
            assignment_session='synthetic-integrator-session', expected_branch='feat/developer',
            expected_head=job0.source_sha, executable='/synthetic/opencode.exe',
            executable_sha256='d' * 64, common_dir=job0.agent.common_dir,
            private_parent=str(Path(tmp.name).resolve()), prompt='bounded developer prompt',
            phase='DEVELOP', write_paths=('PUBLIC.txt',), usage=UsageLedger((), 0, 0),
            budget=RunBudget(1, 8, 0, 600000, 100000, 10000),
            current_integrator_session='synthetic-current-session', c2_c3_reference='accepted-c3',
            source_route_privacy_reference='accepted-egress',
            runtime_policy_reference='atomic-native-job-policy')

        def git(root, *args):
            return ((job0.source_sha if args[-1] == 'HEAD' else 'feat/developer') + '\n').encode()

        with patch.object(h, 'git', side_effect=git):
            job, admission = h.build(**values)
        self.assertEqual(job.read_paths, ('PUBLIC.txt',))
        self.assertEqual(job.write_paths, ('PUBLIC.txt',))
        self.assertEqual(job.model, 'opencode/deepseek-v4.1-flash-free')
        self.assertEqual(job.source_sha, job0.source_sha)
        self.assertEqual(job.task.contract_digest, policy.contract)
        self.assertEqual(admission.job_digest, job.digest)
        self.assertEqual(admission.expected_head, job0.source_sha)
        self.assertEqual(admission.runtime_hash, 'd' * 64)
        self.assertTrue((Path(job.runtime.state_root) / 'config/service.json').exists())
        # A stale expected head is refused before any job is constructed.
        bad = dict(values)
        bad['expected_head'] = 'e' * 40
        with patch.object(h, 'git', side_effect=git):
            with self.assertRaises(h.w.WorkerError):
                h.build(**bad)


if __name__ == '__main__':
    unittest.main()
