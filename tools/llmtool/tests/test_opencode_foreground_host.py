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
import review_trust_fixture
import test_opencode_worker as fixtures
import worker_policy_fixture


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

    def _task_store_for_worker_job(self, job, *, with_developer=False):
        review_trust_fixture.install_for_case(self)
        import hashlib
        from llmtool.action_contract import TaskBinding
        from llmtool.task_store import TaskStore
        from llmtool.task_submission_cli import _record

        task_id = '1' * 32
        run_id = '2' * 32
        record = _record(task_id, run_id, 'pm-host-delivery', 'pm', 'bounded request',
            state='PM_RESPONDED', provider_type='openai-compatible', model='pm-model',
            backend='NO_NATIVE_SESSION_ID_EXPOSED',
            request_sha=hashlib.sha256(b'bounded request').hexdigest())
        record['response'] = 'PM response'
        record['pm_route_binding'] = {
            'type': 'openai-compatible', 'model': 'pm-model',
            'base_url': 'https://api.example.invalid'}
        binding = TaskBinding(**record['task_binding'])
        linked_job = replace(job, task=binding)
        if with_developer:
            assignment = '3' * 32
            artifact = 'bounded proposal'
            record['developer'] = {
                'assignment_id': assignment, 'selection_source': 'PM',
                'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'company-gw',
                'provider_type': 'openai-compatible', 'model': 'gw-model',
                'backend_session_semantics': 'NO_NATIVE_SESSION_ID_EXPOSED',
                'session_id': 'dev-' + assignment, 'source_sha': job.source_sha,
                'source_sha_binding': 'D6_REGISTRY_OBSERVED',
                'read_paths': ['PUBLIC.txt'], 'write_paths': ['PUBLIC.txt'],
                'acceptance': ['bounded change passes tests'], 'context_sha256': '4' * 64,
                'egress_basis': 'Public non-sensitive source only', 'input_bytes': 12,
                'max_tokens': 64, 'artifact_format': 'unified_diff',
                'artifact': artifact,
                'artifact_sha256': hashlib.sha256(artifact.encode()).hexdigest(),
                'usage': None, 'error_code': None, 'created_at': record['created_at'],
            }
        record['completion'] = {
            'state': 'WAITING_EXECUTOR', 'executor_task_id': job.task.task_id,
            'reviewer_task_id': None,
            'evidence': {'task_id': task_id, 'run_id': run_id,
                'contract_digest': binding.contract_digest,
                'execution_mode': 'DIRECT_D6', 'source_sha': job.source_sha},
            'missing_gates': [], 'pm_dispatch': 'NOT_STARTED', 'pm_response': None,
            'pm_recommendation': None, 'pm_usage': None, 'pm_error_code': None,
        }
        parent = Path(tempfile.mkdtemp(dir=str(Path(tempfile.gettempdir()).resolve())))
        root = parent / 'tasks'
        import shutil
        self.addCleanup(lambda: shutil.rmtree(parent, ignore_errors=True))
        if with_developer:
            # #1692: the shared all-ALLOWED test policy, never the live file.
            worker_policy_fixture.install(self)
        store = TaskStore(root)
        store.create(record)
        return linked_job, store, record

    def _worker_delivery(self, job, *, ack='CALLER_RETURNED'):
        import hashlib
        import json
        from llmtool.opencode_result_delivery import (DELIVERED, DELIVERED_REASON,
            DELIVERY_SCHEMA, DeliveryEnvelope, receipt_digest)
        from llmtool.opencode_result_receipt import ResultReceipt

        receipt = ResultReceipt('SUCCESS', 'WORKER_RUN_OBSERVED', job.digest,
            job.task, job.agent, job.source_sha, 'OPENCODE_OUTPUT_RETURNED',
            'backend-session', 'a' * 64, False)
        receipt_hash = receipt_digest(receipt.to_dict())
        delivery = DeliveryEnvelope(
                schema=DELIVERY_SCHEMA, state=DELIVERED, reason=DELIVERED_REASON,
                task_id=job.task.task_id, agent_id=job.agent.agent_id,
                job_digest=job.digest, source_sha=job.source_sha,
                integrator_session='integrator-session', receipt_verdict='SUCCESS',
                receipt_reason='WORKER_RUN_OBSERVED', receipt_digest=receipt_hash,
                redelivery=False, worker_launches=0, provider_calls=0,
                accepted=False, ack=ack)
        body = delivery.to_dict()
        for transport_only in ('delivery_digest', 'redelivery', 'ack'):
            body.pop(transport_only)
        digest = hashlib.sha256(json.dumps(
            body, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('ascii')).hexdigest()
        delivery = replace(delivery, delivery_digest=digest)
        return receipt, delivery

    def test_typed_persisted_receipt_and_delivered_ack_bind_to_exact_task(self):
        from llmtool.task_completion import validate_host_worker_receipt

        job, store, record = self._task_store_for_worker_job(self.fixture()[0])
        receipt, delivery = self._worker_delivery(job)
        self.assertTrue(h._persist_task_worker_delivery(job, receipt, delivery, store))
        saved = store.get(record['task_id'])
        evidence = saved['completion']['evidence']
        self.assertEqual(evidence['execution_mode'], 'HOST_WORKER')
        packet = evidence['host_worker_receipt']
        self.assertTrue(validate_host_worker_receipt(packet,
            task_binding=record['task_binding'], task_id=record['task_id'],
            run_id=record['run_id'],
            contract_digest=record['task_binding']['contract_digest'],
            source_sha=job.source_sha))
        self.assertNotIn('synthetic model text', json.dumps(evidence))
        self.assertEqual(saved['completion']['pm_dispatch'], 'NOT_STARTED')

    def test_successful_host_event_invokes_only_the_bound_task_callback(self):
        job, store, record = self._task_store_for_worker_job(self.fixture()[0],
                                                              with_developer=True)
        receipt, delivery = self._worker_delivery(job)
        with patch('llmtool.task_submission_cli.run_task_executor_event', return_value=1) as callback:
            self.assertTrue(h._persist_task_worker_delivery(job, receipt, delivery, store))
        callback.assert_called_once_with(record['task_id'], 'worker-task', store=store)

    def test_missing_delivery_ack_persists_blocked_host_mode_without_receipt_claim(self):
        job, store, record = self._task_store_for_worker_job(self.fixture()[0])
        receipt, delivery = self._worker_delivery(job, ack='UNKNOWN')
        self.assertFalse(h._persist_task_worker_delivery(job, receipt, delivery, store))
        completion = store.get(record['task_id'])['completion']
        self.assertEqual(completion['evidence']['execution_mode'], 'HOST_WORKER')
        self.assertIsNone(completion['evidence']['host_worker_receipt'])
        self.assertIn('HOST_WORKER_RECEIPT_UNAVAILABLE', completion['missing_gates'])

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
                source = h.native_credential.ActiveOpenCodeCredential('/synthetic/no-read.db')
                host = h.Host(policy, a, source)
                token = object()
                host.token = token
                host.consumed = True
                routes = []
                credential_bodies = []

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
                            credential_bodies.append(json.loads(req.data))
                            return Response(None, secret=True)
                        raise AssertionError(route)

                with patch.object(h.urllib.request, 'build_opener', return_value=Opener()), \
                     patch.object(h.native_credential.ActiveOpenCodeCredential, 'take',
                                  return_value={'type': 'key', 'key': 'SYNTHETIC_ONLY'}) as take:
                    if bad:
                        with self.assertRaises(h.w.WorkerError):
                            host.prepare_server(token, job, 'http://127.0.0.1:12345', 'SYNTHETIC',
                                                h.time.monotonic() + 10)
                        self.assertNotIn('/api/credential', routes)
                        take.assert_not_called()
                    else:
                        host.prepare_server(token, job, 'http://127.0.0.1:12345', 'SYNTHETIC',
                                            h.time.monotonic() + 10)
                        self.assertEqual(routes.count('/api/credential'), 1)
                        take.assert_called_once_with(job.runtime.version)
                        self.assertEqual(credential_bodies, [{
                            'integrationID': 'opencode',
                            'value': {'type': 'key', 'key': 'SYNTHETIC_ONLY'},
                            'activate': True,
                        }])
                        self.assertIsNone(host.credential)
                        state = Path(job.runtime.state_root)
                        self.assertFalse((state / 'data/opencode/opencode.db').exists())
                        self.assertFalse(any(b'SYNTHETIC_ONLY' in p.read_bytes()
                            for p in state.rglob('*') if p.is_file()))

    def test_credential_transport_failure_clears_memory_without_state_file_leak(self):
        job, policy, a, snap = self.fixture()
        host = h.Host(policy, a, h.native_credential.ActiveOpenCodeCredential('/synthetic/no-read.db'))
        host.token = object()
        host.consumed = True
        catalog = [{'providerID': 'opencode', 'id': job.model.split('/', 1)[1],
                    'enabled': True, 'cost': [{'input': 0, 'output': 0,
                    'cache': {'read': 0, 'write': 0}}]}]
        opener = self.native_opener(job, catalog,
            {'providerID': 'opencode', 'id': job.model.split('/', 1)[1]}, [])
        credential_bodies = []
        original_open = opener.open
        class Response:
            def __init__(self, data): self.data = data
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, n): return json.dumps({'data': self.data}).encode()
        def fail_after_credential_post(req, timeout):
            route = req.full_url.split('127.0.0.1:12345')[-1]
            if route == '/api/credential':
                credential_bodies.append(req.data)
                raise OSError('synthetic transport failure')
            if route == '/api/session': return Response({'id': 'ses_syntheticProof'})
            if route.endswith('/permission'):
                query = json.loads(req.data)
                action, resource = query['action'], query['resources'][0]
                allowed = ((action == 'read' and resource in job.read_paths) or
                           (action == 'edit' and resource in job.write_paths))
                return Response({'effect': 'allow' if allowed else 'deny'})
            return original_open(req, timeout)
        opener.open = fail_after_credential_post
        with patch.object(h.urllib.request, 'build_opener', return_value=opener), \
             patch.object(h.native_credential.ActiveOpenCodeCredential, 'take',
                          return_value={'type': 'key', 'key': 'SYNTHETIC_ONLY'}):
            with self.assertRaises(OSError):
                host.prepare_server(host.token, job, 'http://127.0.0.1:12345', 'SYNTHETIC',
                                    h.time.monotonic() + 10)
        self.assertEqual(len(credential_bodies), 1)
        self.assertIn(b'SYNTHETIC_ONLY', credential_bodies[0])
        self.assertIsNone(host.credential)
        state = Path(job.runtime.state_root)
        self.assertFalse((state / 'data/opencode/opencode.db').exists())
        self.assertFalse(any(b'SYNTHETIC_ONLY' in p.read_bytes()
            for p in state.rglob('*') if p.is_file()))

    def native_opener(self, job, catalog, agent_model, routes):
        # Offline fake transport only: no provider, socket or credential file.
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
                    return Response(catalog)
                if route == '/api/agent':
                    return Response([{'id': h.w.AGENT, 'steps': job.max_steps,
                        'model': agent_model,
                        'permissions': h.w.configuration(job)['permissions']}])
                raise AssertionError(route)

        return Opener()

    def assert_admission_unknown_without_credential(self, host, job, opener, routes):
        with patch.object(h.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaises(h.w.WorkerError) as caught:
                host.prepare_server(host.token, job, 'http://127.0.0.1:12345', 'SYNTHETIC',
                                    h.time.monotonic() + 10)
        self.assertEqual(caught.exception.worker_code, 'OPENCODE_HOST_ADMISSION_UNKNOWN')
        self.assertNotIn('/api/credential', routes)

    def test_native_catalog_missing_provider_or_id_refused_before_credential(self):
        # Untrusted native catalog metadata missing or malformed providerID/id must
        # refuse with a typed admission error, not an unhandled TypeError, and must
        # never reach the credential import.
        for field in ('providerID', 'id'):
            for bad in ('<missing>', None, '', 123):
                with self.subTest(field=field, bad=bad):
                    job, policy, a, snap = self.fixture()
                    host = h.Host(policy, a, {'synthetic': 'NOT_REAL'})
                    host.token = object()
                    host.consumed = True
                    entry = {'providerID': 'opencode', 'id': job.model.split('/', 1)[1],
                             'enabled': True,
                             'cost': [{'input': 0, 'output': 0,
                                       'cache': {'read': 0, 'write': 0}}]}
                    if bad == '<missing>':
                        del entry[field]
                    else:
                        entry[field] = bad
                    routes = []
                    opener = self.native_opener(job, [entry],
                        {'providerID': 'opencode', 'id': job.model.split('/', 1)[1]}, routes)
                    self.assert_admission_unknown_without_credential(host, job, opener, routes)

    def test_agent_model_missing_provider_or_id_refused_before_credential(self):
        # The selected agent's own model metadata is also untrusted native data:
        # missing or malformed providerID/id must refuse admission instead of
        # raising TypeError, before any credential import is attempted.
        for field in ('providerID', 'id'):
            for bad in ('<missing>', None, '', 123):
                with self.subTest(field=field, bad=bad):
                    job, policy, a, snap = self.fixture()
                    host = h.Host(policy, a, {'synthetic': 'NOT_REAL'})
                    host.token = object()
                    host.consumed = True
                    catalog = [{'providerID': 'opencode', 'id': job.model.split('/', 1)[1],
                                'enabled': True,
                                'cost': [{'input': 0, 'output': 0,
                                          'cache': {'read': 0, 'write': 0}}]}]
                    agent_model = {'providerID': 'opencode', 'id': job.model.split('/', 1)[1]}
                    if bad == '<missing>':
                        del agent_model[field]
                    else:
                        agent_model[field] = bad
                    routes = []
                    opener = self.native_opener(job, catalog, agent_model, routes)
                    self.assert_admission_unknown_without_credential(host, job, opener, routes)

    def test_build_constructs_policy_bound_job_and_refuses_source_drift(self):
        job0, policy, a, snap = self.fixture()
        tmp = tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(tmp.cleanup)
        real_executable = Path(tmp.name) / 'opencode.exe'
        real_executable.write_bytes(b'synthetic executable pin\n')
        real_executable.chmod(0o700)
        symlink_executable = Path(tmp.name) / 'opencode'
        symlink_executable.symlink_to(real_executable)
        executable_sha256 = h.hashlib.sha256(real_executable.read_bytes()).hexdigest()
        values = dict(policy=policy, run_id='a' * 32,
            assignment_session='synthetic-integrator-session', expected_branch='feat/developer',
            expected_head=job0.source_sha, executable=str(symlink_executable),
            executable_sha256=executable_sha256, common_dir=job0.agent.common_dir,
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
        self.assertEqual(job.runtime.executable, str(real_executable))
        self.assertEqual(job.task.contract_digest, policy.contract)
        self.assertEqual(admission.job_digest, job.digest)
        self.assertEqual(admission.expected_head, job0.source_sha)
        self.assertEqual(admission.runtime_hash, executable_sha256)
        self.assertTrue((Path(job.runtime.state_root) / 'config/service.json').exists())
        # A stale expected head is refused before any job is constructed.
        bad = dict(values)
        bad['expected_head'] = 'e' * 40
        with patch.object(h, 'git', side_effect=git):
            with self.assertRaises(h.w.WorkerError):
                h.build(**bad)

        relative = dict(values, executable='opencode')
        with patch.object(h, 'git', side_effect=git), patch.object(h, 'provision_runtime') as provision:
            with self.assertRaises(h.w.WorkerError) as caught:
                h.build(**relative)
            self.assertEqual(caught.exception.worker_code, 'OPENCODE_RUNTIME_INVALID')
            provision.assert_not_called()

    def test_explicit_bindings_retention_and_validation(self):
        job0, policy, a, snap = self.fixture()
        tmp = tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(tmp.cleanup)
        task = h.TaskBinding('custom-task-id', 'b' * 32, 1, policy.contract)
        agent = h.AgentBinding('custom-agent-id', 'custom-session', h.Role.DEVELOPER, policy.root, job0.agent.common_dir, 'feat/developer')
        values = dict(policy=policy, run_id='b' * 32, assignment_session='custom-session', expected_branch='feat/developer', expected_head=job0.source_sha, executable='/synthetic/opencode.exe', executable_sha256='d' * 64, common_dir=job0.agent.common_dir, private_parent=str(Path(tmp.name).resolve()), prompt='bounded developer prompt', phase='DEVELOP', write_paths=('PUBLIC.txt',), usage=UsageLedger((), 0, 0), budget=RunBudget(1, 8, 0, 600000, 100000, 10000), current_integrator_session='synthetic-current-session', c2_c3_reference='accepted-c3', source_route_privacy_reference='accepted-egress', runtime_policy_reference='atomic-native-job-policy', task_binding=task, agent_binding=agent)
        def git(root, *args):
            return ((job0.source_sha if args[-1] == 'HEAD' else 'feat/developer') + chr(10)).encode()
        with patch.object(h, 'git', side_effect=git):
            job, admission = h.build(**values)
        self.assertIs(job.task, task)
        self.assertIs(job.agent, agent)
        self.assertEqual(job.task.run_id, 'b' * 32)
        self.assertEqual(job.agent.session_id, 'custom-session')
        bad_task = replace(task, contract_digest='c' * 64)
        bad_values = dict(values, task_binding=bad_task)
        with patch.object(h, 'git', side_effect=git), patch.object(h, 'provision_runtime') as mock_provision:
            with self.assertRaises(h.w.WorkerError):
                h.build(**bad_values)
            mock_provision.assert_not_called()
        bad_agent = replace(agent, role=h.Role.REVIEWER)
        bad_values = dict(values, agent_binding=bad_agent)
        with patch.object(h, 'git', side_effect=git), patch.object(h, 'provision_runtime') as mock_provision:
            with self.assertRaises(h.w.WorkerError):
                h.build(**bad_values)
            mock_provision.assert_not_called()
        half_values = dict(values, agent_binding=None)
        with patch.object(h, 'git', side_effect=git), patch.object(h, 'provision_runtime') as mock_provision:
            with self.assertRaises(h.w.WorkerError):
                h.build(**half_values)
            mock_provision.assert_not_called()
        mismatch_values = dict(values, run_id='a' * 32)
        with patch.object(h, 'git', side_effect=git), patch.object(h, 'provision_runtime') as mock_provision:
            with self.assertRaises(h.w.WorkerError):
                h.build(**mismatch_values)
            mock_provision.assert_not_called()
        legacy_values = dict(values, task_binding=None, agent_binding=None, run_id='a' * 32, assignment_session='synthetic-integrator-session')
        with patch.object(h, 'git', side_effect=git):
            job_legacy, _ = h.build(**legacy_values)
        self.assertEqual(job_legacy.task.run_id, 'a' * 32)

    def test_build_missing_required_kwarg_fails_at_bind_time(self):
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
        required = ['policy', 'expected_branch', 'expected_head', 'executable', 'executable_sha256', 'common_dir', 'private_parent', 'prompt', 'phase', 'write_paths', 'usage', 'budget', 'current_integrator_session', 'c2_c3_reference', 'source_route_privacy_reference', 'runtime_policy_reference']
        for kw in required:
            bad = dict(values)
            del bad[kw]
            with patch.object(h, 'git', return_value=b'') as mock_git, patch.object(h, 'provision_runtime') as mock_prov:
                with self.assertRaises(TypeError):
                    h.build(**bad)
                mock_prov.assert_not_called()
                mock_git.assert_not_called()

    def test_host_finalize_binds_job_digest_and_run_with_receipt_enforces_wiring(self):
        from llmtool.run_budget import CallPhase, CallUsage
        job, policy, a, snap = self.fixture()
        tmp = tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(tmp.cleanup)
        store = str(Path(tmp.name) / 'host_receipts.json')
        exp = {'task': job.task, 'agent': job.agent}
        processes = (dict(kind='server', pid=11, host_pid=1, reaped=True, exit=0, group_absent=True),
                     dict(kind='client', pid=12, host_pid=1, reaped=True, exit=0, group_absent=True))
        good_res = h.w.WorkerResult(
            'OPENCODE_OUTPUT_RETURNED', 'RETURNED', 'synthetic model text', 'backend-session-host-1',
            UsageLedger(job.usage.calls + (CallUsage(CallPhase.PRIMARY, 10, 5),),
                        job.usage.repair_attempts, 100),
            True, processes, 'a' * 64, 'HOST_OBSERVED', failure_stage='UNKNOWN')

        calls = []

        def fake_worker(j, host):
            calls.append(j.digest)
            tok = host.admit(j, h.hashlib.sha256(h.canonical(h.w.configuration(j))).hexdigest())
            host.check(tok, j, 'before')
            host.consume(tok, j)
            host.check(tok, j, 'after')
            host.finalize(tok, 'OPENCODE_OUTPUT_RETURNED', True)
            return good_res

        def git_ok(root, *args):
            if args == ('rev-parse', 'HEAD'):
                return (job.source_sha + '\n').encode()
            return ((job.agent.common_dir if args[-1] == '--git-common-dir' else policy.root)
                    + '\n').encode()

        with patch.object(h, 'git', side_effect=git_ok), \
             patch.object(h, 'snapshot', return_value=snap), \
             patch.object(h.w, 'run_worker', side_effect=fake_worker):
            res, final, rec = h.run_with_receipt(job, policy, a, {'synthetic': 'cred'},
                                                 expect=exp, receipt_store=store)
        self.assertEqual(len(calls), 1)
        self.assertEqual(final['job_digest'], job.digest)
        self.assertEqual(final['failure_stage'], 'UNKNOWN')
        self.assertEqual((rec.verdict, rec.reason), ('SUCCESS', 'WORKER_RUN_OBSERVED'))
        self.assertFalse(rec.accepted)
        self.assertNotIn('failure_stage', rec.to_dict())
        # Fresh WorkerJob + Admission + Host objects with same pinned task/store after restart:
        # must refuse BEFORE calling fake_worker a second time (len(calls) remains 1, not 2),
        # and must preserve prior SUCCESS in store.
        fresh_job = replace(job)
        fresh_a = replace(a)
        with patch.object(h, 'git', side_effect=git_ok), \
             patch.object(h, 'snapshot', return_value=snap), \
             patch.object(h.w, 'run_worker', side_effect=fake_worker):
            res_dup, final_dup, rec_dup = h.run_with_receipt(fresh_job, policy, fresh_a,
                                                             {'synthetic': 'cred'},
                                                             expect=exp, receipt_store=store)
        self.assertEqual(len(calls), 1)
        self.assertIsNone(res_dup.content)
        self.assertEqual((rec_dup.verdict, rec_dup.reason), ('FAILED', 'DUPLICATE_RESULT'))
        persisted = json.loads(Path(store).read_text())
        self.assertEqual(persisted['receipts'][job.digest]['verdict'], 'SUCCESS')

    def test_run_with_delivery_returns_correlated_machine_readable_handoff(self):
        from llmtool.run_budget import CallPhase, CallUsage
        from llmtool.opencode_result_delivery import DELIVERED
        job, policy, a, snap = self.fixture()
        job, task_store, task_record = self._task_store_for_worker_job(job)
        policy = replace(policy, contract=job.task.contract_digest)
        a = replace(a, job_digest=job.digest)
        tmp = tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(tmp.cleanup)
        store = str(Path(tmp.name) / 'host_receipts.json')
        exp = {'task': job.task, 'agent': job.agent}
        session = 'synthetic-foreground-integrator'
        processes = (dict(kind='server', pid=11, host_pid=1, reaped=True, exit=0, group_absent=True),
                     dict(kind='client', pid=12, host_pid=1, reaped=True, exit=0, group_absent=True))
        good_res = h.w.WorkerResult(
            'OPENCODE_OUTPUT_RETURNED', 'RETURNED', 'synthetic model text', 'backend-session-host-2',
            UsageLedger(job.usage.calls + (CallUsage(CallPhase.PRIMARY, 10, 5),),
                        job.usage.repair_attempts, 100),
            True, processes, 'a' * 64, 'HOST_OBSERVED', failure_stage='UNKNOWN')
        calls = []

        def fake_worker(j, host):
            calls.append(j.digest)
            tok = host.admit(j, h.hashlib.sha256(h.canonical(h.w.configuration(j))).hexdigest())
            host.check(tok, j, 'before')
            host.consume(tok, j)
            host.check(tok, j, 'after')
            host.finalize(tok, 'OPENCODE_OUTPUT_RETURNED', True)
            return good_res

        def git_ok(root, *args):
            if args == ('rev-parse', 'HEAD'):
                return (job.source_sha + '\n').encode()
            return ((job.agent.common_dir if args[-1] == '--git-common-dir' else policy.root)
                    + '\n').encode()

        with patch.object(h, 'git', side_effect=git_ok), \
             patch.object(h, 'snapshot', return_value=snap), \
             patch.object(h.w, 'run_worker', side_effect=fake_worker):
            res, final, rec, delivered = h.run_with_delivery(
                job, policy, a, {'synthetic': 'cred'}, expect=exp,
                receipt_store=store, integrator_session=session,
                task_store=task_store)
        self.assertEqual(len(calls), 1)
        self.assertEqual((rec.verdict, rec.reason), ('SUCCESS', 'WORKER_RUN_OBSERVED'))
        self.assertEqual((delivered.state, delivered.reason), (DELIVERED, 'RECEIPT_DELIVERED'))
        self.assertFalse(delivered.redelivery)
        self.assertFalse(delivered.accepted)
        self.assertEqual((delivered.worker_launches, delivered.provider_calls), (0, 0))
        self.assertEqual(delivered.job_digest, job.digest)
        self.assertEqual(delivered.task_id, job.task.task_id)
        self.assertEqual(delivered.agent_id, job.agent.agent_id)
        self.assertEqual(delivered.source_sha, job.source_sha)
        self.assertEqual(delivered.integrator_session, session)
        self.assertEqual(len(delivered.receipt_digest), 64)
        self.assertEqual(len(delivered.delivery_digest), 64)
        persisted = json.loads(Path(store).read_text())
        self.assertEqual(persisted['deliveries'][job.digest]['integrator_session'], session)
        linked = task_store.get(task_record['task_id'])['completion']
        self.assertEqual(linked['evidence']['execution_mode'], 'HOST_WORKER')
        self.assertEqual(linked['evidence']['host_worker_receipt']['delivery_digest'],
                         delivered.delivery_digest)
        # A replayed invocation must not relaunch the Worker and must not fabricate a
        # durable receipt for the duplicate-refusal receipt returned by the reservation.
        with patch.object(h, 'git', side_effect=git_ok), \
             patch.object(h, 'snapshot', return_value=snap), \
             patch.object(h.w, 'run_worker', side_effect=fake_worker):
            _, _, rec_dup, delivered_dup = h.run_with_delivery(
                replace(job), policy, replace(a), {'synthetic': 'cred'}, expect=exp,
                receipt_store=store, integrator_session=session,
                task_store=task_store)
        self.assertEqual(len(calls), 1)
        self.assertEqual((rec_dup.verdict, rec_dup.reason), ('FAILED', 'DUPLICATE_RESULT'))
        self.assertEqual((delivered_dup.state, delivered_dup.reason),
                         ('NOT_DELIVERED', 'RECEIPT_NOT_DURABLE'))
        self.assertEqual(json.loads(Path(store).read_text())['receipts'][job.digest]['verdict'],
                         'SUCCESS')
        self.assertEqual(task_store.get(task_record['task_id'])['completion']['evidence'][
            'host_worker_receipt']['delivery_digest'], delivered.delivery_digest)

    def test_dispatch_ack_binds_observed_start_durable_receipt_and_same_integrator(self):
        from llmtool.opencode_result_delivery import DELIVERED
        job, policy, a, snap = self.fixture()
        tmp = tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(tmp.cleanup)
        store = str(Path(tmp.name) / 'dispatch_receipts.json')
        expect = {'task': job.task, 'agent': job.agent}
        session = a.current_session
        launches = []

        def forbidden(*args, **kwargs):
            raise AssertionError('offline dispatch seam must not launch subprocess/provider/network')

        def fake_worker(j, host):
            launches.append(j.digest)
            config_digest = h.hashlib.sha256(h.canonical(h.w.configuration(j))).hexdigest()
            token = host.admit(j, config_digest)
            host.check(token, j, 'before')
            host.consume(token, j)
            host.check(token, j, 'after')
            host.finalize(token, 'OPENCODE_OUTPUT_RETURNED', True)
            from llmtool.run_budget import CallPhase, CallUsage
            result = h.w.WorkerResult(
                'OPENCODE_OUTPUT_RETURNED', 'RETURNED', 'synthetic output', 'backend-dispatch-1',
                UsageLedger(j.usage.calls + (CallUsage(CallPhase.PRIMARY, 10, 5),),
                            j.usage.repair_attempts, 100),
                True,
                (dict(kind='server', pid=11, host_pid=1, reaped=True, exit=0, group_absent=True),
                 dict(kind='client', pid=12, host_pid=1, reaped=True, exit=0, group_absent=True)),
                'b' * 64, 'HOST_OBSERVED', failure_stage='UNKNOWN')
            return result

        def git_ok(root, *args):
            if args == ('rev-parse', 'HEAD'):
                return (job.source_sha + '\n').encode()
            if args == ('branch', '--show-current'):
                return (a.expected_branch + '\n').encode()
            if args == ('rev-parse', '--show-toplevel'):
                return (policy.root + '\n').encode()
            if args == ('rev-parse', '--git-common-dir'):
                return (job.agent.common_dir + '\n').encode()
            raise AssertionError('unexpected Git command')

        with patch.object(h.w, 'run_worker', side_effect=fake_worker), \
             patch.object(h.subprocess, 'Popen', side_effect=forbidden), \
             patch.object(h.urllib.request, 'build_opener', side_effect=forbidden), \
             patch.object(h, 'git', side_effect=git_ok), \
             patch.object(h, 'snapshot', return_value=snap):
            result = h.dispatch_with_ack(job, policy, a, {'synthetic': 'never real'},
                                         expect=expect, receipt_store=store,
                                         integrator_session=session)
        self.assertEqual(result.state, 'STARTED')
        self.assertEqual(result.job_digest, job.digest)
        self.assertEqual(result.task_id, job.task.task_id)
        self.assertEqual(result.agent_id, job.agent.agent_id)
        self.assertEqual(result.source_sha, job.source_sha)
        self.assertEqual(result.integrator_session, session)
        self.assertEqual(result.delivery.state, DELIVERED)
        self.assertEqual(result.delivery.integrator_session, session)
        self.assertEqual(result.delivery.receipt_verdict, 'SUCCESS')
        self.assertTrue(result.completion_ack)
        self.assertFalse(result.accepted)
        self.assertEqual(launches, [job.digest])

        # A mismatch fails before the reservation or worker call, with no replay.
        before = Path(store).read_bytes()
        with patch.object(h.w, 'run_worker', side_effect=forbidden):
            missing = h.dispatch_with_ack(job, policy, None, {'synthetic': 'never real'},
                                          expect=expect, receipt_store=store,
                                          integrator_session=session)
        self.assertEqual((missing.state, missing.reason), ('NOT_STARTED', 'ADMISSION_INVALID'))
        self.assertEqual(Path(store).read_bytes(), before)
        with patch.object(h.w, 'run_worker', side_effect=forbidden):
            invalid_session = h.dispatch_with_ack(
                job, policy, a, {'synthetic': 'never real'}, expect=expect,
                receipt_store=store, integrator_session='bad\nsession')
        self.assertEqual((invalid_session.state, invalid_session.reason),
                         ('NOT_STARTED', 'INTEGRATOR_SESSION_INVALID'))
        self.assertEqual(Path(store).read_bytes(), before)

        bad_admission = replace(a, current_session='different-integrator-session')
        with patch.object(h.w, 'run_worker', side_effect=forbidden):
            rejected = h.dispatch_with_ack(replace(job), policy, bad_admission,
                                          {'synthetic': 'never real'}, expect=expect,
                                          receipt_store=store, integrator_session=session)
        self.assertEqual(rejected.state, 'NOT_STARTED')
        self.assertEqual(rejected.reason, 'INTEGRATOR_SESSION_MISMATCH')
        self.assertEqual(Path(store).read_bytes(), before)
        self.assertEqual(launches, [job.digest])

        with patch.object(h.w, 'run_worker', side_effect=forbidden):
            replay = h.dispatch_with_ack(replace(job), policy, replace(a),
                                         {'synthetic': 'never real'}, expect=expect,
                                         receipt_store=store, integrator_session=session)
        self.assertEqual((replay.state, replay.reason), ('NOT_STARTED', 'DUPLICATE_RESULT'))
        self.assertFalse(replay.completion_ack)
        self.assertEqual(launches, [job.digest])

    def test_invalid_runtime_preflight_is_durable_before_reservation_and_launch(self):
        from llmtool import opencode_result_receipt as receipt_module
        job, policy, admission, _ = self.fixture()
        executable=Path(job.runtime.executable)
        link=executable.parent/'synthetic-runtime-link'
        link.symlink_to(executable)
        job=replace(job,runtime=replace(job.runtime,executable=str(link)))
        temp=tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(temp.cleanup)
        store=str(Path(temp.name)/'host_receipts.json')
        expect={'task':job.task,'agent':job.agent}
        with patch.object(h,'git',return_value=(job.source_sha+'\n').encode()), \
             patch.object(receipt_module,'reserve_host_launch',side_effect=AssertionError('reservation must not run')) as reserve, \
             patch.object(h.w,'run_worker',side_effect=AssertionError('worker must not launch')) as worker:
            result,final,receipt=h.run_with_receipt(job,policy,admission,object(),
                expect=expect,receipt_store=store)
        reserve.assert_not_called();worker.assert_not_called()
        self.assertEqual(result.code,'OPENCODE_RUNTIME_INVALID')
        self.assertEqual(result.failure_stage,'PRELAUNCH')
        self.assertFalse(job._attempted)
        self.assertIsNone(final)
        self.assertEqual((receipt.verdict,receipt.worker_code,receipt.failure_stage),
                         ('BLOCKED','OPENCODE_RUNTIME_INVALID','PRELAUNCH'))
        persisted=json.loads(Path(store).read_text())
        self.assertEqual(persisted['receipts'][job.digest]['failure_stage'],'PRELAUNCH')
        replay_result,replay_final,replay_receipt=h.run_with_receipt(
            job,policy,admission,object(),expect=expect,receipt_store=store)
        self.assertIsNone(replay_final)
        self.assertEqual((replay_result.failure_stage,replay_receipt.verdict,
                          replay_receipt.reason),('PRELAUNCH','UNKNOWN','DUPLICATE_RESULT'))
        self.assertEqual(json.loads(Path(store).read_text())['receipts'][job.digest],persisted['receipts'][job.digest])

    def test_invalid_preflight_preserves_existing_launch_reservation(self):
        from llmtool import opencode_result_receipt as receipt_module
        job, policy, admission, _ = self.fixture()
        executable=Path(job.runtime.executable)
        link=executable.parent/'synthetic-reserved-runtime-link'
        link.symlink_to(executable)
        job=replace(job,runtime=replace(job.runtime,executable=str(link)))
        temp=tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(temp.cleanup)
        store=str(Path(temp.name)/'host_receipts.json')
        expect={'task':job.task,'agent':job.agent}
        self.assertIsNone(receipt_module.reserve_host_launch(
            job=job,expect=expect,store_path=store))
        before=json.loads(Path(store).read_text())['receipts'][job.digest]
        self.assertEqual((before['verdict'],before['reason']),('UNKNOWN','LAUNCH_IN_FLIGHT'))
        with patch.object(h,'git',return_value=(job.source_sha+'\n').encode()), \
             patch.object(h.w,'run_worker',side_effect=AssertionError('worker must not launch')) as worker:
            result,final,receipt=h.run_with_receipt(job,policy,admission,object(),
                expect=expect,receipt_store=store)
        worker.assert_not_called()
        self.assertEqual(result.failure_stage,'PRELAUNCH')
        self.assertIsNone(final)
        self.assertEqual((receipt.verdict,receipt.reason),('UNKNOWN','LAUNCH_IN_FLIGHT'))
        after=json.loads(Path(store).read_text())['receipts'][job.digest]
        self.assertEqual(after,before)

    def test_already_reserved_run_refusal_has_prelaunch_stage(self):
        from llmtool import opencode_result_receipt as receipt_module
        job, policy, admission, _ = self.fixture()
        temp=tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(temp.cleanup)
        store=str(Path(temp.name)/'host_receipts.json')
        expect={'task':job.task,'agent':job.agent}
        self.assertIsNone(receipt_module.reserve_host_launch(
            job=job,expect=expect,store_path=store))
        result,final,receipt=h.run_with_receipt(job,policy,admission,object(),
            expect=expect,receipt_store=store)
        self.assertEqual(result.failure_stage,'PRELAUNCH')
        self.assertIsNone(final)
        self.assertEqual((receipt.verdict,receipt.reason),('UNKNOWN','LAUNCH_IN_FLIGHT'))

    def test_unexpected_preflight_io_failure_stays_unknown_and_unreserved(self):
        from llmtool import opencode_result_receipt as receipt_module
        job,policy,admission,_=self.fixture()
        temp=tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(temp.cleanup)
        store=str(Path(temp.name)/'host_receipts.json')
        expect={'task':job.task,'agent':job.agent}
        with patch.object(h,'git',return_value=(job.source_sha+'\n').encode()), \
             patch.object(h.w,'_validate',side_effect=OSError('synthetic unavailable')), \
             patch.object(receipt_module,'reserve_host_launch',side_effect=AssertionError('must not reserve')) as reserve:
            result,final,receipt=h.run_with_receipt(job,policy,admission,object(),
                expect=expect,receipt_store=store)
        reserve.assert_not_called()
        self.assertEqual(result.code,'OPENCODE_RUNTIME_UNKNOWN')
        self.assertEqual(result.failure_stage,'PRELAUNCH')
        self.assertIsNone(final)
        self.assertEqual((receipt.verdict,receipt.reason),('UNKNOWN','OPENCODE_RUNTIME_UNKNOWN'))
        self.assertFalse(job._attempted)
        self.assertFalse(Path(store).exists())

    def test_delivered_unknown_receipt_is_not_completion_ack(self):
        from llmtool.opencode_result_delivery import (ACK_RETURNED, DELIVERED,
                                                      DeliveryEnvelope, receipt_digest)
        from llmtool.opencode_result_receipt import ResultReceipt
        job, policy, admission, _ = self.fixture()
        expect = {'task': job.task, 'agent': job.agent}
        receipt = ResultReceipt('UNKNOWN', 'RECONCILE_ERROR', job.digest, job.task,
                                job.agent, job.source_sha, 'OPENCODE_HOST_UNKNOWN',
                                'backend-session-unknown', None, False)
        digest = receipt_digest(receipt.to_dict())
        delivery = DeliveryEnvelope(
            schema='llmtool.opencode.result_delivery.v1', state=DELIVERED,
            reason='RECEIPT_DELIVERED', task_id=job.task.task_id,
            agent_id=job.agent.agent_id, job_digest=job.digest,
            source_sha=job.source_sha, integrator_session=admission.current_session,
            receipt_verdict='UNKNOWN', receipt_reason='RECONCILE_ERROR',
            receipt_digest=digest, delivery_digest='a' * 64, ack=ACK_RETURNED)
        host_final = {'code': 'OPENCODE_HOST_UNKNOWN', 'launched': True,
                      'one_use_consumed': True, 'job_digest': job.digest}
        with patch.object(h, 'run_with_delivery',
                          return_value=(None, host_final, receipt, delivery)):
            out = h.dispatch_with_ack(job, policy, admission, {'synthetic': 'never real'},
                                      expect=expect, receipt_store='/unused',
                                      integrator_session=admission.current_session)
        self.assertEqual((out.delivery.state, out.delivery.ack), (DELIVERED, ACK_RETURNED))
        self.assertEqual((out.receipt.verdict, out.receipt.reason), ('UNKNOWN', 'RECONCILE_ERROR'))
        self.assertEqual(out.state, 'STARTED')
        self.assertFalse(out.completion_ack)

    def test_run_with_receipt_blocks_before_worker_on_invalid_expect_corrupt_store_in_flight_or_concurrency(self):
        import multiprocessing, os
        from llmtool.run_budget import CallPhase, CallUsage
        job, policy, a, snap = self.fixture()
        tmp = tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(tmp.cleanup)
        exp = {'task': job.task, 'agent': job.agent}
        processes = (dict(kind='server', pid=11, host_pid=1, reaped=True, exit=0, group_absent=True),
                     dict(kind='client', pid=12, host_pid=1, reaped=True, exit=0, group_absent=True))
        good_res = h.w.WorkerResult(
            'OPENCODE_OUTPUT_RETURNED', 'RETURNED', 'synthetic model text', 'backend-session-host-m1',
            UsageLedger(job.usage.calls + (CallUsage(CallPhase.PRIMARY, 10, 5),),
                        job.usage.repair_attempts, 100),
            True, processes, 'a' * 64, 'HOST_OBSERVED', failure_stage='UNKNOWN')

        def git_ok(root, *args):
            if args == ('rev-parse', 'HEAD'):
                return (job.source_sha + '\n').encode()
            return ((job.agent.common_dir if args[-1] == '--git-common-dir' else policy.root)
                    + '\n').encode()

        # 1. Invalid expect (missing binding, wrong type, identity mismatch) -> 0 fake_worker calls.
        store_exp = str(Path(tmp.name) / 'exp_store.json')
        calls = []
        for bad_exp, expected_verdict in (
            (None, ('UNKNOWN', 'EXPECTATION_INVALID')),
            ({'task': job.task}, ('UNKNOWN', 'EXPECTATION_INVALID')),
            ({'task': replace(job.task, run_id='b' * 32), 'agent': job.agent}, ('FAILED', 'TASK_IDENTITY_MISMATCH')),
            ({'task': job.task, 'agent': replace(job.agent, session_id='other-sess')}, ('FAILED', 'AGENT_IDENTITY_MISMATCH')),
        ):
            with self.subTest(bad_exp=bad_exp):
                with patch.object(h, 'git', side_effect=git_ok), \
                     patch.object(h.w, 'run_worker', side_effect=lambda j, host: calls.append(1) or good_res):
                    _, _, rec = h.run_with_receipt(replace(job), policy, replace(a), {'synthetic': 'cred'},
                                                   expect=bad_exp, receipt_store=store_exp)
                self.assertEqual(len(calls), 0)
                self.assertEqual((rec.verdict, rec.reason), expected_verdict)
        self.assertFalse(Path(store_exp).exists())

        # 2. Corrupt / unsafe / inside-repo store -> 0 fake_worker calls.
        corrupt = Path(tmp.name) / 'corrupt_store.json'
        corrupt.write_text('{corrupt')
        os.chmod(corrupt, 0o600)
        in_repo = str(Path(job.agent.root) / 'inside_repo_store.json')
        for bad_store in (str(corrupt), in_repo, ''):
            with self.subTest(bad_store=bad_store):
                with patch.object(h, 'git', side_effect=git_ok), \
                     patch.object(h.w, 'run_worker', side_effect=lambda j, host: calls.append(1) or good_res):
                    _, _, rec = h.run_with_receipt(replace(job), policy, replace(a), {'synthetic': 'cred'},
                                                   expect=exp, receipt_store=bad_store)
                self.assertEqual(len(calls), 0)
                self.assertEqual((rec.verdict, rec.reason), ('UNKNOWN', 'RECEIPT_STORE_INVALID'))

        # 3. Crash / in-flight / UNKNOWN state -> no automatic resend on fresh objects.
        store_crash = str(Path(tmp.name) / 'crash_store.json')
        crash_calls = []

        def crashing_worker(j, host):
            crash_calls.append(1)
            raise RuntimeError('simulated mid-run host crash')

        with patch.object(h, 'git', side_effect=git_ok):
            with self.assertRaises(RuntimeError):
                with patch.object(h.w, 'run_worker', side_effect=crashing_worker):
                    h.run_with_receipt(replace(job), policy, replace(a), {'synthetic': 'cred'},
                                       expect=exp, receipt_store=store_crash)
        self.assertEqual(len(crash_calls), 1)
        # Fresh objects after crash must NOT call fake_worker again; store stays UNKNOWN (reserved)
        # until missing host-side evidence is supplied via record_host_receipt without re-running worker.
        with patch.object(h, 'git', side_effect=git_ok), \
             patch.object(h.w, 'run_worker', side_effect=lambda j, host: crash_calls.append(2) or good_res):
            _, _, rec_after_crash = h.run_with_receipt(replace(job), policy, replace(a), {'synthetic': 'cred'},
                                                       expect=exp, receipt_store=store_crash)
        self.assertEqual(len(crash_calls), 1)
        self.assertEqual(rec_after_crash.verdict, 'UNKNOWN')

        # 4. Separate-process store reopen and synchronized concurrent starts: at most 1 fake Worker launch.
        store_mp = str(Path(tmp.name) / 'mp_store.json')
        ctx = multiprocessing.get_context('fork')
        barrier = ctx.Barrier(2)
        worker_counter = ctx.Value('i', 0)
        out_q = ctx.Queue()

        def mp_entry(idx):
            def mp_worker(j, host):
                with worker_counter.get_lock():
                    worker_counter.value += 1
                host.final = {'code': 'OPENCODE_OUTPUT_RETURNED', 'launched': True,
                              'one_use_consumed': True, 'job_digest': j.digest}
                return good_res

            with patch.object(h, 'git', side_effect=git_ok), \
                 patch.object(h.w, 'run_worker', side_effect=mp_worker):
                barrier.wait(timeout=5)
                _, _, rec_mp = h.run_with_receipt(replace(job), policy, replace(a), {'synthetic': 'cred'},
                                                  expect=exp, receipt_store=store_mp)
                out_q.put((idx, rec_mp.verdict, rec_mp.reason))

        p1 = ctx.Process(target=mp_entry, args=(1,))
        p2 = ctx.Process(target=mp_entry, args=(2,))
        p1.start()
        p2.start()
        p1.join(timeout=10)
        p2.join(timeout=10)
        self.assertEqual(p1.exitcode, 0)
        self.assertEqual(p2.exitcode, 0)
        self.assertEqual(worker_counter.value, 1)
        verdicts = sorted(out_q.get(timeout=2)[1:] for _ in range(2))
        self.assertIn(('SUCCESS', 'WORKER_RUN_OBSERVED'), verdicts)
        other = verdicts[0] if verdicts[1] == ('SUCCESS', 'WORKER_RUN_OBSERVED') else verdicts[1]
        self.assertIn(other, (('UNKNOWN', 'LAUNCH_IN_FLIGHT'), ('FAILED', 'DUPLICATE_RESULT')))
        # Separate process after completion reopening the same store: worker_counter remains 1.
        def mp_reopen():
            def mp_worker_never(j, host):
                with worker_counter.get_lock():
                    worker_counter.value += 1
                return good_res

            with patch.object(h, 'git', side_effect=git_ok), \
                 patch.object(h.w, 'run_worker', side_effect=mp_worker_never):
                _, _, rec_reopen = h.run_with_receipt(replace(job), policy, replace(a), {'synthetic': 'cred'},
                                                      expect=exp, receipt_store=store_mp)
                out_q.put(('reopen', rec_reopen.verdict, rec_reopen.reason))

        p3 = ctx.Process(target=mp_reopen)
        p3.start()
        p3.join(timeout=10)
        self.assertEqual(p3.exitcode, 0)
        self.assertEqual(worker_counter.value, 1)
        self.assertEqual(out_q.get(timeout=2), ('reopen', 'FAILED', 'DUPLICATE_RESULT'))

    def test_run_with_receipt_fails_closed_when_git_head_observation_fails_or_tampered_facts(self):
        from llmtool.run_budget import CallPhase, CallUsage
        job, policy, a, snap = self.fixture()
        tmp = tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(tmp.cleanup)
        store = str(Path(tmp.name) / 'host_receipts2.json')
        store_no_git = str(Path(tmp.name) / 'host_receipts3.json')
        exp = {'task': job.task, 'agent': job.agent}
        processes = (dict(kind='server', pid=11, host_pid=1, reaped=True, exit=0, group_absent=True),
                     dict(kind='client', pid=12, host_pid=1, reaped=True, exit=0, group_absent=True))
        good_res = h.w.WorkerResult(
            'OPENCODE_OUTPUT_RETURNED', 'RETURNED', 'synthetic model text', 'backend-session-host-2',
            UsageLedger(job.usage.calls + (CallUsage(CallPhase.PRIMARY, 10, 5),),
                        job.usage.repair_attempts, 100),
            True, processes, 'a' * 64, 'HOST_OBSERVED', failure_stage='UNKNOWN')

        def fake_worker_tampered(j, host):
            host.final = {'code': 'OPENCODE_OUTPUT_RETURNED', 'launched': True,
                          'one_use_consumed': True, 'job_digest': 'f' * 64}
            return good_res

        def git_ok(root, *args):
            return (job.source_sha + '\n').encode()

        with patch.object(h, 'git', side_effect=git_ok), \
             patch.object(h.w, 'run_worker', side_effect=fake_worker_tampered):
            _, _, rec_tampered = h.run_with_receipt(job, policy, a, {'synthetic': 'cred'},
                                                    expect=exp, receipt_store=store)
        self.assertEqual((rec_tampered.verdict, rec_tampered.reason), ('UNKNOWN', 'HOST_FACTS_DISAGREE'))

        def fake_worker_ok(j, host):
            host.final = {'code': 'OPENCODE_OUTPUT_RETURNED', 'launched': True,
                          'one_use_consumed': True, 'job_digest': j.digest}
            return good_res

        with patch.object(h, 'git', side_effect=OSError('git unavailable')), \
             patch.object(h.w, 'run_worker', side_effect=fake_worker_ok):
            _, _, rec_no_git = h.run_with_receipt(replace(job), policy, replace(a), {'synthetic': 'cred'},
                                                  expect=exp, receipt_store=store_no_git)
        self.assertEqual((rec_no_git.verdict, rec_no_git.reason), ('UNKNOWN', 'SOURCE_NOT_OBSERVED'))


if __name__ == '__main__':
    unittest.main()
