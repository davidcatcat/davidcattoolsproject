"""Offline contract tests for the gw-agent Developer route."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from llmtool.developer_routes import GwAgentRoute, RouteError
from llmtool.developer_routes import gw_agent
from llmtool.developer_routes.gw_agent import _scope_digest
from llmtool.developer_routes.base import capture_snapshot, run_route


class Boundary:
    def prepare(self, root, write_paths, state_dirs=()):
        return ['/verified-boundary'], 'a' * 64

    def verify(self, root, write_paths, state_dirs=(), *, runner):
        return {'verified': True, 'policy_sha256': 'a' * 64, 'probes': []}


class DirectBoundary(Boundary):
    def prepare(self, root, write_paths, state_dirs=()):
        return [], 'a' * 64


class GwAgentRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.root.mkdir()
        subprocess.run(['/usr/bin/git', 'init', str(self.root)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['/usr/bin/git', '-C', str(self.root), 'config', 'user.name', 'Test'], check=True)
        subprocess.run(['/usr/bin/git', '-C', str(self.root), 'config', 'user.email',
                        'test@example.invalid'], check=True)
        (self.root / 'source.py').write_text('original\n')
        subprocess.run(['/usr/bin/git', '-C', str(self.root), 'add', 'source.py'], check=True)
        subprocess.run(['/usr/bin/git', '-C', str(self.root), 'commit', '-m', 'initial'], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.branch = subprocess.check_output(
            ['/usr/bin/git', '-C', str(self.root), 'branch', '--show-current'], text=True).strip()
        self.head = subprocess.check_output(
            ['/usr/bin/git', '-C', str(self.root), 'rev-parse', 'HEAD'], text=True).strip()
        self.credential = 'sentinel-' + 'gw-agent-' + 'api-' + 'key-71a9'
        package = Path(__file__).resolve().parents[2] / 'api-agent-gateway'
        self.route = GwAgentRoute({
            'package_path': str(package), 'profile_name': 'fake',
            'profile': {'adapter': 'openai-chat-tools', 'base_url': 'http://127.0.0.1:9/v1',
                        'model': 'fake-model', 'auth_env': 'GW_AGENT_API_KEY',
                        'context_cap_tokens': 10000, 'max_output_tokens': 1000,
                        'allowed_data_classes': ['synthetic'], 'session_expiry_hours': 72,
                        'timeout_seconds': 5},
            'default_model': 'fake-model', 'default_effort': 'low',
            'authorization_ref': 'pm-task-ref',
        })

    def request(self):
        return {
            'root': str(self.root), 'model': 'fake-model', 'effort': 'low',
            'expected_branch': self.branch, 'expected_head': self.head,
            'write_paths': ['source.py'], 'executable': sys.executable,
            'task': {'schema_version': 1, 'task_id': 'task-001', 'source_sha': self.head,
                     'goal': 'Edit one file', 'acceptance': ['source changes'],
                     'read_paths': ['source.py'], 'write_paths': ['source.py'],
                     'egress_basis': 'authorized-task-ref', 'data_class': 'synthetic',
                     'budgets': {'max_turns': 4, 'max_input_tokens': 10000,
                                 'max_output_tokens': 1000, 'max_wall_seconds': 60,
                                 'max_tool_result_bytes': 4096, 'max_sent_bytes': 100000}},
        }

    def run_route_with_credential(self, *args, **kwargs):
        credential_loader = mock.patch.object(
            gw_agent.llm_config, 'resolve_profile_config', return_value={})
        value_loader = mock.patch.object(
            gw_agent.llm_config, 'api_' + 'key', return_value=self.credential)
        with credential_loader, value_loader:
            return run_route(*args, **kwargs)

    @staticmethod
    def _write_json(path, value):
        Path(path).write_text(json.dumps(value, sort_keys=True))

    def test_private_files_env_secret_result_and_audit_collection(self):
        seen = {}
        record = {'seq': 1, 'prev_sha256': '0' * 64, 'type': 'end'}
        audit_digest = hashlib.sha256(json.dumps(
            record, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

        def runner(argv, cwd, env, timeout):
            seen['argv'] = argv
            seen['env'] = env
            self.assertEqual(env['GW_AGENT_API_KEY'], self.credential)
            self.assertEqual(env['PYTHONPATH'], self.route.package_path)
            self.assertIn('-B', argv)
            self.assertIn('-P', argv)
            self.assertIn('api_agent_gateway', argv)
            self.assertNotIn(self.credential, ' '.join(argv))
            task_file = Path(argv[argv.index('--task-file') + 1])
            auth_file = Path(argv[argv.index('--authorization-file') + 1])
            profiles_file = Path(argv[argv.index('--profiles-file') + 1])
            for path in (task_file, auth_file, profiles_file):
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertNotIn(self.credential, path.read_text())
            seen['task'] = json.loads(task_file.read_text())
            seen['auth'] = json.loads(auth_file.read_text())
            (self.root / 'source.py').write_text('changed\n')
            result = {'schema_version': 1, 'status': 'DONE', 'code': None,
                      'run_id': 'a' * 24, 'session_id': 'session_12345678',
                      'task_id': 'task-001', 'changed_paths': ['source.py'],
                      'summary': 'done', 'served_model': 'fake-model',
                      'usage': {'status': 'REPORTED', 'calls': 1}, 'turns': 2,
                      'budget_used': {}, 'audit_sha256': audit_digest}
            result_path = Path(argv[argv.index('--result-file') + 1])
            self._write_json(result_path, result)
            result_path.chmod(0o600)
            audit_path = Path(argv[argv.index('--audit-file') + 1])
            audit_path.write_text(json.dumps(record, sort_keys=True, separators=(',', ':')) + '\n')
            audit_path.chmod(0o600)
            private_dir = task_file.parent
            for path in private_dir.rglob('*'):
                if path.is_file():
                    self.assertNotIn(self.credential, path.read_bytes().decode('utf-8'))
            return 0, b'route output', b''

        result = self.run_route_with_credential(
            self.route, self.request(), gate=lambda: True, runner=runner,
            boundary=Boundary(), snapshot=capture_snapshot)
        self.assertEqual(result.status, 'DONE')
        self.assertEqual(result.changed_files, ('source.py',))
        self.assertNotIn(self.credential, repr(result))
        self.assertNotIn(self.credential, repr(seen['task']))
        self.assertNotIn(self.credential, Path(self.route.audit_copy_path).read_text())
        self.assertEqual(Path(self.route.audit_copy_path).parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(seen['auth']['scope_digest'], _scope_digest(seen['task']))

    def test_status_exit_model_and_quota_mapping(self):
        for status, code, exit_code in (
                ('BLOCKED', 'NO_TOOL_CALL', 3), ('MODEL_ERROR', 'HTTP_429', 4),
                ('BUDGET_EXCEEDED', 'TURN_BUDGET_EXCEEDED', 6),
                ('POLICY_REFUSED', 'DATA_CLASS_NOT_AUTHORIZED', 5)):
            self.route._status = status
            self.route._gateway_code = code
            self.route.validate_exit(exit_code, {})
            with self.assertRaises(RouteError) as caught:
                self.route.validate_status({})
            self.assertEqual(caught.exception.code, 'GW_AGENT_' + status)
            self.assertEqual(caught.exception.facts['gateway_code'], code)
        with self.assertRaises(RouteError) as caught:
            self.route.validate_exit(1, {})
        self.assertEqual(caught.exception.code, 'GW_AGENT_OUTPUT_INVALID')
        self.route._result = {'served_model': 'UNKNOWN'}
        self.assertIsNone(self.route.verified_model('', 'fake-model'))
        self.route._result = {'served_model': 'other-model'}
        self.assertEqual(self.route.verified_model('', 'fake-model'), 'other-model')
        self.route._gateway_code = 'HTTP_429'
        self.assertTrue(self.route.quota_exhausted({}, b''))

    def test_run_route_prioritizes_scope_and_quota_statuses(self):
        record = {'seq': 1, 'prev_sha256': '0' * 64, 'type': 'end'}
        digest = hashlib.sha256(json.dumps(
            record, ensure_ascii=False, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()

        def run(status, gateway_code, exit_code, *, outside_write=False):
            def runner(argv, cwd, env, timeout):
                if outside_write:
                    (self.root / 'outside.py').write_text('outside\n')
                result = {'schema_version': 1, 'status': status, 'code': gateway_code,
                          'run_id': 'b' * 24, 'session_id': None, 'task_id': 'task-001',
                          'changed_paths': ['outside.py'] if outside_write else [],
                          'summary': 'bounded', 'served_model': 'fake-model', 'usage': {},
                          'turns': 1, 'budget_used': {}, 'audit_sha256': digest}
                result_path = Path(argv[argv.index('--result-file') + 1])
                self._write_json(result_path, result)
                result_path.chmod(0o600)
                audit_path = Path(argv[argv.index('--audit-file') + 1])
                audit_path.write_text(json.dumps(record, sort_keys=True,
                                                 separators=(',', ':')) + '\n')
                audit_path.chmod(0o600)
                return exit_code, b'', b''

            with self.assertRaises(RouteError) as caught:
                self.run_route_with_credential(
                    self.route, self.request(), gate=lambda: True, runner=runner,
                    boundary=Boundary(), snapshot=capture_snapshot)
            return caught.exception

        scope_error = run('BLOCKED', 'NO_TOOL_CALL', 3, outside_write=True)
        self.assertEqual(scope_error.code, 'GW_AGENT_WRITE_SCOPE_VIOLATION')
        self.assertEqual(scope_error.facts['changed_files'], ('outside.py',))
        (self.root / 'outside.py').unlink()
        quota_error = run('MODEL_ERROR', 'HTTP_429', 4)
        self.assertEqual(quota_error.code, 'GW_AGENT_QUOTA_EXHAUSTED')

    def test_audit_verifier_rejects_self_declared_digest_on_broken_chain(self):
        with tempfile.TemporaryDirectory() as temp:
            result_path = Path(temp) / 'result.json'
            result_path.write_text(json.dumps({'task_id': 'task-001',
                                               'audit_sha256': 'a' * 64}))
            result_path.chmod(0o600)
            audit_path = Path(temp) / 'audit.jsonl'
            audit_path.write_text(json.dumps({'seq': 2, 'prev_sha256': 'f' * 64,
                                              'type': 'end', 'sha256': 'a' * 64}) + '\n')
            audit_path.chmod(0o600)
            with self.assertRaises(RouteError) as caught:
                self.route.collect(temp, {'root': str(self.root),
                                          'task': {'task_id': 'task-001'}})
            self.assertEqual(caught.exception.code, 'GW_AGENT_AUDIT_INVALID')

    def test_offline_gateway_subprocess_edits_only_authorized_file(self):
        gateway_tests = Path(self.route.package_path) / 'tests'
        sys.path.insert(0, str(gateway_tests))
        try:
            from helpers import FakeServer, reply, tool_call
        finally:
            sys.path.remove(str(gateway_tests))
        server = FakeServer()
        self.addCleanup(server.close)
        self.route.profile['base_url'] = f'http://127.0.0.1:{server.port}/v1'

        server.script.extend((reply(tool_call('apply_edit', path='source.py',
                                             old='original\n', new='changed\n'),
                                    model='fake-model'),
                              reply(tool_call('finish', summary='done'), model='fake-model')))
        request = self.request()
        request['task']['goal'] = 'Change source.py from original to changed.'
        result = self.run_route_with_credential(
            self.route, request, gate=lambda: True,
            boundary=DirectBoundary(), snapshot=capture_snapshot)
        self.assertEqual(result.status, 'DONE')
        self.assertEqual(result.changed_files, ('source.py',))
        self.assertEqual((self.root / 'source.py').read_text(), 'changed\n')
        self.assertEqual(len(server.requests), 2)
        audit_records = [json.loads(line) for line in Path(self.route.audit_copy_path).read_text().splitlines()]
        self.assertTrue(any(record.get('type') == 'tool'
                            and record.get('tool') == 'apply_edit'
                            and record.get('ok') is True
                            for record in audit_records))


if __name__ == '__main__':
    unittest.main()
