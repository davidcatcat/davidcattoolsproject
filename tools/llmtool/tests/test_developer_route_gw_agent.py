"""Offline contract tests for the gw-agent Developer route."""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from llmtool import task_store
from llmtool.developer_routes import GwAgentRoute, RouteError, for_platform, load_route_config
from llmtool.developer_routes import gw_agent
from llmtool.developer_routes.gw_agent import _scope_digest
from llmtool.developer_routes.base import capture_snapshot, run_route
from llmtool.developer_routes.write_boundary import SandboxExecBoundary, UnsupportedBoundary


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
        home = mock.patch.dict(os.environ, {'HOME': self.temp.name})
        home.start()
        self.addCleanup(home.stop)
        gateway = mock.patch.object(gw_agent, '_gateway_credential_config', return_value={})
        gateway.start()
        self.addCleanup(gateway.stop)
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
            'allowed_data_classes': ['synthetic'],
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
                                 'max_tool_result_bytes': 4096, 'max_sent_bytes': 100000},
                     'findings': []},
        }

    def run_route_with_credential(self, *args, **kwargs):
        credential_loader = mock.patch.object(
            gw_agent.llm_config, 'resolve_profile_config', return_value={})
        value_loader = mock.patch.object(
            gw_agent.llm_config, 'api_' + 'key', return_value=self.credential)
        with credential_loader, value_loader:
            return run_route(*args, **kwargs)

    def test_launcher_below_3_11_drops_the_working_directory_from_sys_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            request = {'_route_tmpdir': tmpdir, 'root': str(Path(tmpdir) / 'root')}
            with mock.patch.object(gw_agent, '_HAS_SAFE_PATH_FLAG', False):
                argv = self.route.build_argv(sys.executable, request)
            self.assertEqual(
                argv,
                [sys.executable, '-B', '-c', gw_agent._SAFE_PATH_LAUNCHER, 'run',
                 '--authorization-file', str(Path(tmpdir) / 'authorization.json'),
                 '--workdir', request['root'], '--profile', self.route.profile_name,
                 '--task-file', str(Path(tmpdir) / 'task.json'),
                 '--result-file', str(Path(tmpdir) / 'result.json'),
                 '--audit-file', str(Path(tmpdir) / 'audit.jsonl'),
                 '--profiles-file', str(Path(tmpdir) / 'profiles.json'),
                 '--state-dir', str(Path(tmpdir) / 'state')])
            with mock.patch.object(gw_agent, '_HAS_SAFE_PATH_FLAG', True):
                argv = self.route.build_argv(sys.executable, request)
            self.assertEqual(
                argv,
                [sys.executable, '-B', '-P', '-m', 'api_agent_gateway', 'run',
                 '--authorization-file', str(Path(tmpdir) / 'authorization.json'),
                 '--workdir', request['root'], '--profile', self.route.profile_name,
                 '--task-file', str(Path(tmpdir) / 'task.json'),
                 '--result-file', str(Path(tmpdir) / 'result.json'),
                 '--audit-file', str(Path(tmpdir) / 'audit.jsonl'),
                 '--profiles-file', str(Path(tmpdir) / 'profiles.json'),
                 '--state-dir', str(Path(tmpdir) / 'state')])
        launcher = gw_agent._SAFE_PATH_LAUNCHER.replace(
            "runpy.run_module('api_agent_gateway', run_name='__main__', alter_sys=True)",
            'import json; print(json.__file__)')
        with tempfile.TemporaryDirectory() as shadow_dir:
            (Path(shadow_dir) / 'json.py').write_text('')
            completed = subprocess.run(
                [sys.executable, '-B', '-c', launcher],
                cwd=shadow_dir,
                capture_output=True,
                text=True,
                check=True)
            printed = completed.stdout.strip()
            self.assertNotIn(shadow_dir, printed)

    def test_missing_optional_key_writes_unauthenticated_profile_and_environment(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
                mock.patch.object(gw_agent.llm_config, 'resolve_profile_config',
                                  return_value={}), \
                mock.patch.object(gw_agent.llm_config, 'api_key', return_value=None):
            self.route.prepare_files(tmpdir, self.request())
            env = self.route.build_env(tmpdir)
            profile_file = json.loads((Path(tmpdir) / 'profiles.json').read_text())
            profile = profile_file['profiles']['fake']
            self.assertIsNone(profile['auth_env'])
            self.assertNotIn('GW_AGENT_API_KEY', env)

    def test_missing_or_empty_configured_key_writes_unauthenticated_profile(self):
        for configured_value in (None, ''):
            missing_key = gw_agent.llm_config.coded_error(
                'configured API key environment variable is missing or empty',
                'AUTH_CREDENTIAL_MISSING', 'config.auth', user_action_required=True)
            with self.subTest(configured_value='missing' if configured_value is None else 'empty'), \
                    tempfile.TemporaryDirectory() as tmpdir, \
                    mock.patch.object(gw_agent.llm_config, 'resolve_profile_config',
                                      return_value={}), \
                    mock.patch.object(gw_agent.llm_config, 'api_key',
                                      side_effect=missing_key):
                if configured_value is None:
                    with mock.patch.dict(os.environ, {}, clear=False):
                        os.environ.pop('GW_AGENT_API_KEY', None)
                        self.route.prepare_files(tmpdir, self.request())
                        env = self.route.build_env(tmpdir)
                else:
                    with mock.patch.dict(os.environ, {'GW_AGENT_API_KEY': ''}):
                        self.route.prepare_files(tmpdir, self.request())
                        env = self.route.build_env(tmpdir)
                profile_file = json.loads((Path(tmpdir) / 'profiles.json').read_text())
                self.assertIsNone(profile_file['profiles']['fake']['auth_env'])
                self.assertNotIn('GW_AGENT_API_KEY', env)

    def test_configured_key_remains_bound_to_gateway_environment(self):
        with tempfile.TemporaryDirectory() as tmpdir, \
                mock.patch.object(gw_agent.llm_config, 'resolve_profile_config',
                                  return_value={}), \
                mock.patch.object(gw_agent.llm_config, 'api_key',
                                  return_value=self.credential):
            self.route.prepare_files(tmpdir, self.request())
            env = self.route.build_env(tmpdir)
            profile_file = json.loads((Path(tmpdir) / 'profiles.json').read_text())
            profile = profile_file['profiles']['fake']
            self.assertEqual(profile['auth_env'], 'GW_AGENT_API_KEY')
            self.assertEqual(env['GW_AGENT_API_KEY'], self.credential)
            self.assertNotIn(self.credential, json.dumps(profile_file))

    def test_malformed_or_unresolvable_credentials_fail_closed_without_disclosure(self):
        cases = (
            ('nul', 'bad\x00secret', None),
            ('overlong', 'x' * 8193, None),
            ('config-error', None, RuntimeError('private configuration detail')),
            ('key-error', None, RuntimeError('private credential detail')),
        )
        for label, credential, resolve_error in cases:
            self.route._credential_resolved = False
            self.route._credential = None
            with tempfile.TemporaryDirectory() as tmpdir, self.subTest(case=label):
                resolver = (mock.patch.object(
                    gw_agent, '_gateway_credential_config', side_effect=resolve_error)
                    if label == 'config-error' else mock.patch.object(
                        gw_agent.llm_config, 'resolve_profile_config', return_value={}))
                key_loader = (mock.patch.object(gw_agent.llm_config, 'api_key',
                                                side_effect=resolve_error)
                              if label == 'key-error' else
                              mock.patch.object(gw_agent.llm_config, 'api_key',
                                                return_value=credential))
                with resolver, key_loader:
                    with self.assertRaises(RouteError) as caught:
                        self.route.prepare_files(tmpdir, self.request())
                self.assertEqual(caught.exception.code, 'GW_AGENT_CREDENTIAL_MISSING')
                self.assertNotIn('private configuration detail', str(caught.exception))
                self.assertEqual(list(Path(tmpdir).iterdir()), [])

    def test_prepare_files_failure_clears_cached_credential(self):
        request = self.request()
        request['task']['non_serializable'] = object()
        with tempfile.TemporaryDirectory() as tmpdir, \
                mock.patch.object(gw_agent.llm_config, 'resolve_profile_config',
                                  return_value={}), \
                mock.patch.object(gw_agent.llm_config, 'api_key',
                                  return_value=self.credential):
            with self.assertRaises(RouteError) as caught:
                self.route.prepare_files(tmpdir, request)
            self.assertEqual(caught.exception.code, 'GW_AGENT_REQUEST_INVALID')
            self.assertIsNone(self.route._credential)
            self.assertFalse(self.route._credential_resolved)

    def test_route_config_accepts_only_supported_auth_env_values(self):
        route_config = {
            'schema_version': 1,
            'routes': {
                'agy': {'executable': 'agy', 'default_model': 'agy-model',
                        'allowed_models': ['agy-model'],
                        'default_effort': 'low', 'denied_dirs': ['.gemini'],
                        'state_dirs': {'policy': 'private-temp-only'}},
                'gw-agent': {
                    'package_path': str(Path(self.temp.name) / 'gateway'),
                    'default_model': 'fake-model', 'allowed_models': ['fake-model'],
                    'default_effort': 'low',
                    'profile_name': 'fake',
                    'profile': {'model': 'fake-model', 'auth_env': None},
                    'authorization_ref': 'pm-task-ref',
                    'allowed_data_classes': ['synthetic'],
                },
            },
        }
        path = Path(self.temp.name) / 'developer-routes.json'
        for auth_env in (None, 'GW_AGENT_API_KEY'):
            route_config['routes']['gw-agent']['profile']['auth_env'] = auth_env
            path.write_text(json.dumps(route_config))
            self.assertEqual(load_route_config(path)['routes']['gw-agent']['profile']['auth_env'],
                             auth_env)
        for auth_env in ('OTHER_KEY', '', 7, False):
            route_config['routes']['gw-agent']['profile']['auth_env'] = auth_env
            path.write_text(json.dumps(route_config))
            with self.subTest(auth_env=auth_env), self.assertRaises(RouteError) as caught:
                load_route_config(path)
            self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')
        route_config['routes']['gw-agent']['profile'].pop('auth_env')
        path.write_text(json.dumps(route_config))
        with self.assertRaises(RouteError) as caught:
            load_route_config(path)
        self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')

        null_profile = dict(self.route.profile, auth_env=None)
        self.assertIsInstance(GwAgentRoute(dict(self.route.config, profile=null_profile)),
                              GwAgentRoute)
        for auth_env in ('OTHER_KEY', '', 7, False):
            with self.subTest(route_auth_env=auth_env), self.assertRaises(RouteError) as caught:
                GwAgentRoute(dict(self.route.config,
                                  profile=dict(self.route.profile, auth_env=auth_env)))
            self.assertEqual(caught.exception.code, 'GW_AGENT_CONFIG_INVALID')
        profile_without_auth_env = dict(self.route.profile)
        profile_without_auth_env.pop('auth_env')
        with self.assertRaises(RouteError) as caught:
            GwAgentRoute(dict(self.route.config, profile=profile_without_auth_env))
        self.assertEqual(caught.exception.code, 'GW_AGENT_CONFIG_INVALID')

    def test_invalid_package_paths_raise_route_error(self):
        package_file = Path(self.temp.name) / 'package-file'
        package_file.write_text('not a directory')
        cases = (
            str(Path(self.temp.name) / 'missing-package'),
            str(package_file),
        )
        for package_path in cases:
            config = dict(self.route.config, package_path=package_path)
            with self.subTest(package_path=package_path):
                with self.assertRaises(RouteError) as caught:
                    GwAgentRoute(config)
                self.assertEqual(caught.exception.code, 'GW_AGENT_CONFIG_INVALID')

        config = dict(self.route.config)
        with mock.patch.object(Path, 'is_dir', side_effect=PermissionError):
            with self.assertRaises(RouteError) as caught:
                GwAgentRoute(config)
        self.assertEqual(caught.exception.code, 'GW_AGENT_CONFIG_INVALID')

    def test_tilde_package_path_expands_and_checks_ownership_and_permissions(self):
        home = Path(self.temp.name) / 'home'
        package = home / '.local' / 'lib' / 'api-agent-gateway'
        gateway = package / 'api_agent_gateway'
        gateway.mkdir(parents=True)
        config = dict(self.route.config, package_path='~/.local/lib/api-agent-gateway')
        with mock.patch.dict('os.environ', {'HOME': str(home)}):
            route = GwAgentRoute(config)
        self.assertEqual(route.package_path, str(package.resolve()))

        for target in (package, gateway):
            original_mode = target.stat().st_mode & 0o777
            for writable_bit in (0o020, 0o002):
                target.chmod(original_mode | writable_bit)
                with self.subTest(target=target, writable_bit=writable_bit), \
                        self.assertRaises(RouteError) as caught:
                    GwAgentRoute(config)
                self.assertEqual(caught.exception.code, 'GW_AGENT_CONFIG_INVALID')
            target.chmod(original_mode)

        with mock.patch.object(gw_agent.os, 'getuid', return_value=os.getuid() + 1):
            with self.assertRaises(RouteError) as caught:
                GwAgentRoute(config)
        self.assertEqual(caught.exception.code, 'GW_AGENT_CONFIG_INVALID')

    def test_pinned_route_still_requires_matching_requested_model(self):
        config = dict(self.route.config, expected_served_model='fake-model')
        pinned = GwAgentRoute(config)
        self.assertEqual(pinned.validate_profile('fake-model', 'low'), ('fake-model', 'low'))
        with self.assertRaises(RouteError) as caught:
            pinned.validate_profile('other-model', 'low')
        self.assertEqual(caught.exception.code, 'GW_AGENT_PROFILE_INVALID')

    def test_unpinned_route_records_well_formed_served_model(self):
        request = self.request()
        validated = self.route.validate_request(request)
        result = {'schema_version': 1, 'status': 'DONE', 'code': None,
                  'run_id': 'a' * 24, 'session_id': None, 'task_id': 'task-001',
                  'changed_paths': [], 'summary': 'done', 'served_model': 'provider/model-v2',
                  'usage': {}, 'turns': 1, 'budget_used': {}, 'audit_sha256': 'b' * 64}
        self.route._result = result
        parsed = self.route.parse_output(b'', b'', '')
        self.assertEqual(self.route.verified_model('', validated['model']), 'provider/model-v2')
        self.assertEqual(validated['model'], 'provider/model-v2')
        self.assertEqual(parsed['status'], 'DONE')

        for malformed in ('', 'bad model'):
            self.route._result = dict(result, served_model=malformed)
            with self.subTest(served_model=malformed), self.assertRaises(RouteError) as caught:
                self.route.parse_output(b'', b'', '')
            self.assertEqual(caught.exception.code, 'GW_AGENT_OUTPUT_INVALID')
        self.route._result = dict(result, served_model='UNKNOWN')
        with self.assertRaises(RouteError) as caught:
            self.route.parse_output(b'', b'', '')
        self.assertEqual(caught.exception.code, 'GW_AGENT_MODEL_UNVERIFIED')

    @staticmethod
    def _write_json(path, value):
        Path(path).write_text(json.dumps(value, sort_keys=True))

    def _write_valid_audit(self, path, status='DONE', code=None, changed_paths=1):
        package = Path(self.route.package_path)
        package_parent = str(package)
        sys.path.insert(0, package_parent)
        try:
            from api_agent_gateway.audit import AuditLog
        finally:
            sys.path.remove(package_parent)
        audit = AuditLog(str(path))
        audit.append('header', run_id='a' * 24, command='run', task_id='task-001')
        audit.append('end', status=status, code=code, turns=1, changed_paths=changed_paths)
        digest = audit.last_sha256
        audit.close()
        return digest

    def test_private_files_env_secret_result_and_audit_collection(self):
        seen = {}

        def runner(argv, cwd, env, timeout):
            seen['argv'] = argv
            seen['env'] = env
            self.assertEqual(env['GW_AGENT_API_KEY'], self.credential)
            self.assertEqual(env['PYTHONPATH'], self.route.package_path)
            self.assertIn('-B', argv)
            if sys.version_info >= (3, 11):
                self.assertIn('-P', argv)
                self.assertIn('api_agent_gateway', argv)
            else:
                self.assertIn('-c', argv)
                self.assertEqual(argv[argv.index('-c') + 1], gw_agent._SAFE_PATH_LAUNCHER)
                self.assertNotIn('-P', argv)
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
            audit_path = Path(argv[argv.index('--audit-file') + 1])
            audit_digest = self._write_valid_audit(audit_path)
            result = {'schema_version': 1, 'status': 'DONE', 'code': None,
                      'run_id': 'a' * 24, 'session_id': 'session_12345678',
                      'task_id': 'task-001', 'changed_paths': ['source.py'],
                      'summary': 'done', 'served_model': 'fake-model',
                      'usage': {'status': 'REPORTED', 'calls': 1}, 'turns': 2,
                      'budget_used': {}, 'audit_sha256': audit_digest}
            result_path = Path(argv[argv.index('--result-file') + 1])
            self._write_json(result_path, result)
            result_path.chmod(0o600)
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

    def test_gateway_data_class_mapping_is_applied_before_scope_digest(self):
        request = self.request()
        request['task']['data_class'] = 'SYNTHETIC'
        with tempfile.TemporaryDirectory() as tmpdir, \
                mock.patch.object(gw_agent.llm_config, 'resolve_profile_config',
                                  return_value={}), \
                mock.patch.object(gw_agent.llm_config, 'api_key', return_value=None):
            self.route._prepare_files(tmpdir, request)
            task = json.loads((Path(tmpdir) / 'task.json').read_text())
            authorization = json.loads((Path(tmpdir) / 'authorization.json').read_text())
            profile = json.loads((Path(tmpdir) / 'profiles.json').read_text())['profiles']['fake']
            self.assertEqual(task['data_class'], 'synthetic')
            self.assertEqual(authorization['data_class'], 'synthetic')
            self.assertEqual(profile['allowed_data_classes'], ['synthetic'])
            self.assertEqual(authorization['scope_digest'], _scope_digest(task))

    def test_omitted_findings_defaults_to_empty(self):
        request = self.request()
        request['task'].pop('findings')
        validated = self.route.validate_request(request)
        self.assertEqual(validated['task']['findings'], [])

    def test_complete_gateway_task_contract_names_the_failing_field(self):
        cases = (('schema_version', 2, 'GW_AGENT_TASK_SCHEMA_VERSION_INVALID'),
                 ('goal', '', 'GW_AGENT_TASK_GOAL_INVALID'),
                 ('acceptance', 'bad', 'GW_AGENT_TASK_ACCEPTANCE_INVALID'),
                 ('data_class', 'INTERNAL', 'GW_AGENT_TASK_DATA_CLASS_INVALID'))
        for field, value, code in cases:
            request = self.request()
            request['task'][field] = value
            with self.subTest(field=field):
                with self.assertRaises(RouteError) as caught:
                    self.route.validate_request(request)
            self.assertEqual(caught.exception.code, code)

    def test_null_gateway_task_identity_retains_bounded_refusal_diagnostic(self):
        with tempfile.TemporaryDirectory() as temp:
            result_path = Path(temp) / 'result.json'
            result_path.write_text(json.dumps({
                'schema_version': 1, 'status': 'POLICY_REFUSED', 'code': 'TASK_INVALID',
                'task_id': None}))
            result_path.chmod(0o600)
            with self.assertRaises(RouteError) as caught:
                self.route.collect(temp, {'root': str(self.root),
                                          'task': {'task_id': 'task-001'}})
        self.assertEqual(caught.exception.code,
                         'GW_AGENT_TASK_ID_MISMATCH_TASK_INVALID')

    def test_long_gateway_code_keeps_bounded_task_identity_diagnostic(self):
        gateway_code = 'GATEWAY_' + 'X' * 40
        with tempfile.TemporaryDirectory() as temp:
            result_path = Path(temp) / 'result.json'
            result_path.write_text(json.dumps({
                'schema_version': 1, 'status': 'POLICY_REFUSED', 'code': gateway_code,
                'task_id': None}))
            result_path.chmod(0o600)
            with self.assertRaises(RouteError) as caught:
                self.route.collect(temp, {'root': str(self.root),
                                          'task': {'task_id': 'task-001'}})
        self.assertTrue(caught.exception.code.startswith('GW_AGENT_TASK_ID_MISMATCH'))
        self.assertLessEqual(len(caught.exception.code), 64)
        self.assertEqual(caught.exception.facts['gateway_code'], gateway_code)

    def test_reported_usage_becomes_the_triplet_the_real_task_store_accepts(self):
        usage = gw_agent._normalize_gw_agent_usage({
            'status': 'REPORTED', 'calls': 7, 'calls_without_usage': 0,
            'input_tokens': 8813, 'output_tokens': 908,
            'provider_private_field': 'discarded'})
        self.assertEqual(usage, {'prompt_tokens': 8813, 'completion_tokens': 908,
                                 'total_tokens': 9721})
        # The CLI normalizes the parsed value a second time.
        self.assertEqual(gw_agent._normalize_gw_agent_usage(dict(usage)), usage)
        usage['served_model'] = 'qwen3.8-27b'
        task_store._validate_usage(usage)

    def test_incomplete_usage_is_unknown_and_never_estimated(self):
        for value in (None, 'bad', {}, {'status': 'UNKNOWN', 'input_tokens': None},
                      {'status': 'PARTIAL', 'input_tokens': 12, 'output_tokens': 3},
                      {'status': 'REPORTED', 'input_tokens': 12, 'output_tokens': None},
                      {'status': 'REPORTED', 'input_tokens': True, 'output_tokens': 1},
                      {'status': 'REPORTED', 'input_tokens': -1, 'output_tokens': 1},
                      {'status': 'REPORTED', 'input_tokens': 50_000_001, 'output_tokens': 1}):
            with self.subTest(value=value):
                self.assertIsNone(gw_agent._normalize_gw_agent_usage(value))

    def test_parse_output_hands_the_store_shape_to_the_cli(self):
        self.route.validate_request(self.request())
        result = {'schema_version': 1, 'status': 'DONE', 'code': None,
                  'run_id': 'a' * 24, 'session_id': None, 'task_id': 'task-001',
                  'changed_paths': [], 'summary': 'done', 'served_model': 'fake-model',
                  'turns': 1, 'budget_used': {}, 'audit_sha256': 'b' * 64,
                  'usage': {'status': 'REPORTED', 'input_tokens': 2, 'output_tokens': 3}}
        self.route._result = result
        self.assertEqual(self.route.parse_output(b'', b'', '')['usage'], {
            'prompt_tokens': 2, 'completion_tokens': 3, 'total_tokens': 5})
        # The CLI must see None here: a dict would claim CLI_REPORTED usage.
        self.route._result = dict(result, usage={'status': 'UNKNOWN', 'input_tokens': None})
        self.assertIsNone(self.route.parse_output(b'', b'', '')['usage'])

    def test_last_tool_failure_prefers_the_failed_write_over_later_probing(self):
        def tool(name, code, ok=False):
            return json.dumps({'type': 'tool', 'tool': name, 'ok': ok, 'code': code}).encode()

        # The tool order of live Task 111797e9: a failed write, then read probes.
        live = [b'{"type":"header"}', tool('read_file', 'NOT_FOUND'),
                tool('apply_edit', 'OS_ERROR'), tool('list_dir', None, ok=True),
                tool('apply_edit', 'PERMISSION_DENIED'), tool('list_dir', 'NOT_FOUND'),
                tool('grep', None, ok=True), tool('finish', None, ok=True)]
        self.assertEqual(gw_agent._last_tool_failure(live), 'APPLY_EDIT_PERMISSION_DENIED')
        self.assertEqual(gw_agent._last_tool_failure(
            [tool('read_file', 'NOT_FOUND'), tool('grep', 'BAD_ARGUMENTS')]),
            'GREP_BAD_ARGUMENTS')

    def test_last_tool_failure_keeps_only_bounded_known_values(self):
        for line in (b'not json', b'[]',
                     json.dumps({'type': 'tool', 'tool': 'not_executed', 'ok': False,
                                 'code': 'NOT_EXECUTED'}).encode(),
                     json.dumps({'type': 'tool', 'tool': 'grep', 'ok': False,
                                 'code': 'raw provider text'}).encode(),
                     json.dumps({'type': 'tool', 'tool': 'grep', 'ok': False,
                                 'code': 'A' * 40}).encode(),
                     json.dumps({'type': 'tool', 'tool': 'grep', 'ok': False,
                                 'code': None}).encode()):
            with self.subTest(line=line):
                self.assertIsNone(gw_agent._last_tool_failure([line]))

    def test_every_tool_failure_fits_the_task_error_code_uncut(self):
        prefix = 'DEVELOPER_GW_AGENT_BLOCKED_'
        longest = 'A' * (37 - len('APPLY_EDIT_'))
        reason = gw_agent._last_tool_failure([json.dumps(
            {'type': 'tool', 'tool': 'apply_edit', 'ok': False, 'code': longest}).encode()])
        self.assertLessEqual(len(prefix + reason), 64)

    def test_model_blocked_status_reports_the_tool_failure_not_its_text(self):
        self.route._status, self.route._gateway_code = 'BLOCKED', 'MODEL_REPORTED_BLOCKED'
        with self.assertRaises(RouteError) as caught:
            self.route.validate_status({})
        self.assertEqual(caught.exception.facts['gateway_code'], 'MODEL_REPORTED_BLOCKED')
        self.route._tool_failure = 'APPLY_EDIT_PERMISSION_DENIED'
        with self.assertRaises(RouteError) as caught:
            self.route.validate_status({})
        self.assertEqual(caught.exception.code, 'GW_AGENT_BLOCKED')
        self.assertEqual(caught.exception.facts['gateway_code'],
                         'APPLY_EDIT_PERMISSION_DENIED')
        # Other gateway outcomes already name their cause.
        self.route._gateway_code = 'NO_TOOL_CALL'
        with self.assertRaises(RouteError) as caught:
            self.route.validate_status({})
        self.assertEqual(caught.exception.facts['gateway_code'], 'NO_TOOL_CALL')

    def test_route_requests_the_gateway_temp_allowance_from_the_sandbox(self):
        plain = SandboxExecBoundary(executable='/bin/true', denied_dirs=('blocked',),
                                    tmpdir='/tmp')
        configured = self.route.configure_boundary(plain)
        self.assertIsInstance(configured, SandboxExecBoundary)
        self.assertTrue(configured.allow_gw_agent_tmp)
        self.assertEqual((configured.executable, configured.denied_dirs, configured.tmpdir),
                         ('/bin/true', ('blocked',), '/tmp'))
        self.assertFalse(plain.allow_gw_agent_tmp)
        unsupported = UnsupportedBoundary()
        self.assertIs(self.route.configure_boundary(unsupported), unsupported)
        self.assertIsNone(self.route.configure_boundary(None))

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
        def run(status, gateway_code, exit_code, *, outside_write=False):
            def runner(argv, cwd, env, timeout):
                if outside_write:
                    (self.root / 'outside.py').write_text('outside\n')
                audit_path = Path(argv[argv.index('--audit-file') + 1])
                digest = self._write_valid_audit(
                    audit_path, status=status, code=gateway_code,
                    changed_paths=1 if outside_write else 0)
                result = {'schema_version': 1, 'status': status, 'code': gateway_code,
                          'run_id': 'b' * 24, 'session_id': None, 'task_id': 'task-001',
                          'changed_paths': ['outside.py'] if outside_write else [],
                          'summary': 'bounded', 'served_model': 'fake-model', 'usage': {},
                          'turns': 1, 'budget_used': {}, 'audit_sha256': digest}
                result_path = Path(argv[argv.index('--result-file') + 1])
                self._write_json(result_path, result)
                result_path.chmod(0o600)
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

    def test_setup_route_errors_are_preserved_and_private_tmp_is_removed(self):
        cases = (
            ('prepare_files', RouteError('GW_AGENT_CONFIG_INVALID',
                                         facts={'detail': 'must not escape'})),
            ('build_env', RouteError('GW_AGENT_CREDENTIAL_MISSING',
                                     facts={'detail': 'must not escape'})),
            ('build_argv', ValueError('must not escape')),
        )
        for method, failure in cases:
            private_dirs = []
            real_mkdtemp = tempfile.mkdtemp

            def record_mkdtemp(*args, **kwargs):
                path = real_mkdtemp(*args, **kwargs)
                private_dirs.append(path)
                return path

            with tempfile.TemporaryDirectory() as isolated_home, \
                    self.subTest(method=method), \
                    mock.patch.dict(os.environ, {'HOME': isolated_home,
                                                 'TMPDIR': isolated_home}), \
                    mock.patch('tempfile.tempdir', None), \
                    mock.patch('llmtool.developer_routes.base.tempfile.mkdtemp',
                               side_effect=record_mkdtemp), \
                    ExitStack() as setup:
                if method in ('build_env', 'build_argv'):
                    setup.enter_context(mock.patch.object(
                        self.route, 'prepare_files', return_value=None))
                if method == 'build_argv':
                    setup.enter_context(mock.patch.object(
                        self.route, 'build_env',
                        return_value={'GW_AGENT_API_KEY': self.credential}))
                setup.enter_context(mock.patch.object(
                    self.route, method, side_effect=failure))
                with self.assertRaises(RouteError) as caught:
                    run_route(self.route, self.request(), gate=lambda: True,
                              runner=lambda *args: (0, b'', b''),
                              boundary=Boundary(), snapshot=capture_snapshot)
                expected = (failure.code if isinstance(failure, RouteError)
                            else 'GW_AGENT_REQUEST_INVALID')
                self.assertEqual(caught.exception.code, expected)
                self.assertEqual(len(private_dirs), 1)
                self.assertFalse(Path(private_dirs[0]).exists())

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
        with mock.patch.object(gw_agent.llm_config, 'resolve_profile_config',
                               return_value={}), \
                mock.patch.object(gw_agent.llm_config, 'api_key', return_value=None):
            result = run_route(self.route, request, gate=lambda: True,
                               boundary=DirectBoundary(), snapshot=capture_snapshot)
        self.assertEqual(result.status, 'DONE')
        self.assertEqual(result.changed_files, ('source.py',))
        self.assertEqual((self.root / 'source.py').read_text(), 'changed\n')
        self.assertEqual(len(server.requests), 2)
        self.assertEqual(len(server.headers), 2)
        self.assertTrue(all(
            not any(name.lower() == 'authorization' for name in headers)
            for headers in server.headers))
        audit_records = [json.loads(line) for line in Path(self.route.audit_copy_path).read_text().splitlines()]
        self.assertTrue(any(record.get('type') == 'tool'
                            and record.get('tool') == 'apply_edit'
                            and record.get('ok') is True
                            for record in audit_records))

    def test_pinned_served_model_mismatch_is_refused_by_run_route(self):
        gateway_tests = Path(self.route.package_path) / 'tests'
        sys.path.insert(0, str(gateway_tests))
        try:
            from helpers import FakeServer, reply, tool_call
        finally:
            sys.path.remove(str(gateway_tests))
        server = FakeServer()
        self.addCleanup(server.close)
        self.route.profile['base_url'] = f'http://127.0.0.1:{server.port}/v1'
        self.route.expected_served_model = 'fake-model'
        server.script.append(reply(tool_call('finish', summary='done'),
                                   model='other-model'))

        with self.assertRaises(RouteError) as caught:
            self.run_route_with_credential(
                self.route, self.request(), gate=lambda: True,
                boundary=DirectBoundary(), snapshot=capture_snapshot)

        self.assertEqual(caught.exception.code, 'GW_AGENT_MODEL_MISMATCH')
        self.assertEqual((self.root / 'source.py').read_text(), 'original\n')
        self.assertEqual(len(server.requests), 1)

    def _alias_route(self, port):
        config = dict(self.route.config, default_model='gw', expected_served_model='gw')
        config['profile'] = dict(config['profile'], model='gw',
                                 base_url=f'http://127.0.0.1:{port}/v1')
        return GwAgentRoute(config)

    def _fake_server(self):
        gateway_tests = Path(self.route.package_path) / 'tests'
        sys.path.insert(0, str(gateway_tests))
        try:
            from helpers import FakeServer, reply, tool_call
        finally:
            sys.path.remove(str(gateway_tests))
        server = FakeServer()
        self.addCleanup(server.close)
        return server, reply, tool_call

    def test_routing_alias_records_the_model_the_provider_really_served(self):
        server, reply, tool_call = self._fake_server()
        route = self._alias_route(server.port)
        server.script.append(reply(tool_call('finish', summary='done'), model='qwen3.8-27b'))
        request = dict(self.request(), model='gw')

        result = self.run_route_with_credential(
            route, request, gate=lambda: True, boundary=DirectBoundary(),
            snapshot=capture_snapshot)

        self.assertEqual(result.model, 'qwen3.8-27b')

    def test_routing_alias_still_refuses_an_unknown_served_model(self):
        server, reply, tool_call = self._fake_server()
        route = self._alias_route(server.port)
        unknown = reply(tool_call('finish', summary='done'))
        del unknown['model']
        server.script.append(unknown)

        with self.assertRaises(RouteError) as caught:
            self.run_route_with_credential(
                route, dict(self.request(), model='gw'), gate=lambda: True,
                boundary=DirectBoundary(), snapshot=capture_snapshot)

        self.assertEqual(caught.exception.code, 'GW_AGENT_MODEL_UNVERIFIED')

    def test_routing_alias_served_model_is_refused_as_unverified(self):
        server, reply, tool_call = self._fake_server()
        route = self._alias_route(server.port)
        server.script.append(reply(tool_call('finish', summary='done'), model='gw'))

        with self.assertRaises(RouteError) as caught:
            self.run_route_with_credential(
                route, dict(self.request(), model='gw'), gate=lambda: True,
                boundary=DirectBoundary(), snapshot=capture_snapshot)

        self.assertEqual(caught.exception.code, 'GW_AGENT_MODEL_UNVERIFIED')
        route._result = {'served_model': 'gw'}
        self.assertIsNone(route.verified_model('', 'gw'))

    def test_blocked_run_keeps_the_tool_code_and_drops_the_model_summary(self):
        server, reply, tool_call = self._fake_server()
        self.route.profile['base_url'] = f'http://127.0.0.1:{server.port}/v1'
        server.script.extend((
            reply(tool_call('apply_edit', path='other.py', new='x', create=True),
                  model='fake-model'),
            reply(tool_call('finish', summary='raw provider words', status='blocked'),
                  model='fake-model')))

        with self.assertRaises(RouteError) as caught:
            self.run_route_with_credential(
                self.route, self.request(), gate=lambda: True,
                boundary=DirectBoundary(), snapshot=capture_snapshot)

        self.assertEqual(caught.exception.code, 'GW_AGENT_BLOCKED')
        self.assertEqual(caught.exception.facts,
                         {'gateway_code': 'APPLY_EDIT_OUT_OF_SCOPE_WRITE'})
        self.assertNotIn('raw provider words', repr(caught.exception.facts))
        self.assertFalse((self.root / 'other.py').exists())

    @unittest.skipUnless(sys.platform == 'darwin', 'requires real macOS sandbox-exec')
    def test_real_sandbox_creates_only_the_allowed_file_through_the_default_boundary(self):
        """The CLI passes a plain for_platform(); the route must still be able to write."""
        server, reply, tool_call = self._fake_server()
        route = self._alias_route(server.port)
        target = 'gw-agent-synthetic.txt'
        server.script.extend((
            reply(tool_call('apply_edit', path=target, new='hello synthetic\n', create=True),
                  model='qwen3.8-27b'),
            reply(tool_call('finish', summary='done'), model='qwen3.8-27b')))
        request = dict(self.request(), model='gw', write_paths=[target])
        request['task'] = dict(request['task'], read_paths=[target], write_paths=[target])

        result = self.run_route_with_credential(
            route, request, gate=lambda: True, boundary=for_platform(),
            snapshot=capture_snapshot)

        self.assertEqual(result.status, 'DONE')
        self.assertEqual(result.model, 'qwen3.8-27b')
        self.assertEqual(result.changed_files, (target,))
        self.assertEqual((self.root / target).read_text(), 'hello synthetic\n')
        status = subprocess.check_output(
            ['/usr/bin/git', '-C', str(self.root), 'status', '--porcelain'], text=True)
        self.assertEqual(status, f'?? {target}\n')

    def test_validate_request_session_parameters(self):
        valid_sess = '0123456789abcdef0123456789abcdef'
        valid_home = str(Path(self.temp.name) / 'valid-home')

        # Session home and session id both valid.
        req = self.request()
        req['session_id'] = valid_sess
        req['session_home'] = valid_home
        val = self.route.validate_request(req)
        self.assertEqual(val.get('session_id'), valid_sess)
        self.assertEqual(val.get('session_home'), valid_home)

        # Session home without session id is valid.
        req2 = self.request()
        req2['session_home'] = valid_home
        val2 = self.route.validate_request(req2)
        self.assertNotIn('session_id', val2)
        self.assertEqual(val2.get('session_home'), valid_home)

        # Session id without session home raises GW_AGENT_SESSION_UNAVAILABLE.
        req3 = self.request()
        req3['session_id'] = valid_sess
        with self.assertRaises(RouteError) as cm:
            self.route.validate_request(req3)
        self.assertEqual(cm.exception.code, 'GW_AGENT_SESSION_UNAVAILABLE')

        # Bad session id values raise GW_AGENT_SESSION_UNAVAILABLE.
        for bad_id in ('short', 'not-hex-characters-32-chars-long!', 'A' * 32, 'a' * 31, 'a' * 33, 12345):
            bad_req = self.request()
            bad_req['session_id'] = bad_id
            bad_req['session_home'] = valid_home
            with self.subTest(bad_id=bad_id), self.assertRaises(RouteError) as cm:
                self.route.validate_request(bad_req)
            self.assertEqual(cm.exception.code, 'GW_AGENT_SESSION_UNAVAILABLE')

        # Bad session home values raise GW_AGENT_SESSION_UNAVAILABLE.
        for bad_home in ('relative/path', '', 12345, None):
            bad_req = self.request()
            bad_req['session_id'] = valid_sess
            bad_req['session_home'] = bad_home
            with self.subTest(bad_home=bad_home), self.assertRaises(RouteError) as cm:
                self.route.validate_request(bad_req)
            self.assertEqual(cm.exception.code, 'GW_AGENT_SESSION_UNAVAILABLE')

    def test_build_argv_run_versus_resume(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Run without session_id
            req_run = {'_route_tmpdir': tmpdir, 'root': str(Path(tmpdir) / 'root')}
            argv_run = self.route.build_argv(sys.executable, req_run)
            self.assertIn('run', argv_run)
            self.assertNotIn('resume', argv_run)

            # Resume with session_id
            sess_id = '0123456789abcdef0123456789abcdef'
            req_resume = {'_route_tmpdir': tmpdir, 'root': str(Path(tmpdir) / 'root'),
                          'session_id': sess_id}
            argv_resume = self.route.build_argv(sys.executable, req_resume)
            self.assertNotIn('run', argv_resume)
            self.assertIn('resume', argv_resume)
            self.assertEqual(argv_resume[argv_resume.index('resume') + 1], sess_id)

            common_tail = [
                '--authorization-file', str(Path(tmpdir) / 'authorization.json'),
                '--workdir', req_run['root'], '--profile', self.route.profile_name,
                '--task-file', str(Path(tmpdir) / 'task.json'),
                '--result-file', str(Path(tmpdir) / 'result.json'),
                '--audit-file', str(Path(tmpdir) / 'audit.jsonl'),
                '--profiles-file', str(Path(tmpdir) / 'profiles.json'),
                '--state-dir', str(Path(tmpdir) / 'state'),
            ]
            self.assertEqual(argv_run[argv_run.index('run') + 1:], common_tail)
            self.assertEqual(argv_resume[argv_resume.index(sess_id) + 1:], common_tail)

            # Check legacy Python 3.9 launcher branch
            with mock.patch.object(gw_agent, '_HAS_SAFE_PATH_FLAG', False):
                legacy_run = self.route.build_argv(sys.executable, req_run)
                legacy_resume = self.route.build_argv(sys.executable, req_resume)
                self.assertIn('run', legacy_run)
                self.assertIn('resume', legacy_resume)
                self.assertEqual(legacy_resume[legacy_resume.index('resume') + 1], sess_id)
                self.assertEqual(legacy_run[legacy_run.index('run') + 1:], common_tail)
                self.assertEqual(legacy_resume[legacy_resume.index(sess_id) + 1:], common_tail)

    def test_authorization_json_session_id_null_and_set(self):
        # Null session_id when omitted
        with tempfile.TemporaryDirectory() as tmpdir, \
                mock.patch.object(gw_agent.llm_config, 'resolve_profile_config', return_value={}), \
                mock.patch.object(gw_agent.llm_config, 'api_key', return_value=None):
            self.route.prepare_files(tmpdir, self.request())
            auth = json.loads((Path(tmpdir) / 'authorization.json').read_text())
            self.assertIsNone(auth['session_id'])

        # Set session_id carried into authorization.json
        sess_id = '0123456789abcdef0123456789abcdef'
        home = Path(self.temp.name) / 'auth-home'
        home.mkdir(mode=0o700)
        os.chmod(home, 0o700)
        for name in ('meta.json', 'history.json'):
            p = home / name
            p.write_bytes(b'{"synthetic": true}\n')
            os.chmod(p, 0o600)

        with tempfile.TemporaryDirectory() as tmpdir, \
                mock.patch.object(gw_agent.llm_config, 'resolve_profile_config', return_value={}), \
                mock.patch.object(gw_agent.llm_config, 'api_key', return_value=None):
            req = self.request()
            req['session_id'] = sess_id
            req['session_home'] = str(home)
            self.route.prepare_files(tmpdir, req)
            auth = json.loads((Path(tmpdir) / 'authorization.json').read_text())
            self.assertEqual(auth['session_id'], sess_id)

    def test_copy_in_keeps_0700_folders_and_0600_files(self):
        sess_id = '0123456789abcdef0123456789abcdef'
        home = Path(self.temp.name) / 'copy-in-home'
        home.mkdir(mode=0o700)
        os.chmod(home, 0o700)
        meta_data = b'{"synthetic": "meta-data"}\n'
        hist_data = b'{"synthetic": "hist-data"}\n'
        for name, data in (('meta.json', meta_data), ('history.json', hist_data)):
            p = home / name
            p.write_bytes(data)
            os.chmod(p, 0o600)

        with tempfile.TemporaryDirectory() as tmpdir, \
                mock.patch.object(gw_agent.llm_config, 'resolve_profile_config', return_value={}), \
                mock.patch.object(gw_agent.llm_config, 'api_key', return_value=None):
            req = self.request()
            req['session_id'] = sess_id
            req['session_home'] = str(home)
            self.route.prepare_files(tmpdir, req)

            state_dir = Path(tmpdir) / 'state'
            sessions_dir = state_dir / 'sessions'
            dest_dir = sessions_dir / sess_id
            for directory in (state_dir, sessions_dir, dest_dir):
                self.assertTrue(directory.is_dir())
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)

            dest_meta = dest_dir / 'meta.json'
            dest_hist = dest_dir / 'history.json'
            self.assertTrue(dest_meta.is_file())
            self.assertTrue(dest_hist.is_file())
            self.assertEqual(stat.S_IMODE(dest_meta.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(dest_hist.stat().st_mode), 0o600)
            self.assertEqual(dest_meta.read_bytes(), meta_data)
            self.assertEqual(dest_hist.read_bytes(), hist_data)

    def test_copy_in_refuses_unsafe_source_and_home_inside_d6_root(self):
        sess_id = '0123456789abcdef0123456789abcdef'

        def make_valid_home(name):
            d = Path(self.temp.name) / name
            d.mkdir(mode=0o700)
            os.chmod(d, 0o700)
            for fname in ('meta.json', 'history.json'):
                f = d / fname
                f.write_bytes(b'{"synthetic": true}\n')
                os.chmod(f, 0o600)
            return d

        # Symlinked meta.json
        home_sym_file = make_valid_home('sym-file')
        (home_sym_file / 'meta.json').unlink()
        target = Path(self.temp.name) / 'target-meta.json'
        target.write_bytes(b'{"synthetic": true}\n')
        os.chmod(target, 0o600)
        os.symlink(target, home_sym_file / 'meta.json')
        with tempfile.TemporaryDirectory() as tmpdir, \
                mock.patch.object(gw_agent.llm_config, 'resolve_profile_config', return_value={}), \
                mock.patch.object(gw_agent.llm_config, 'api_key', return_value=None):
            req = self.request()
            req['session_id'] = sess_id
            req['session_home'] = str(home_sym_file)
            with self.assertRaises(RouteError) as cm:
                self.route.prepare_files(tmpdir, req)
            self.assertEqual(cm.exception.code, 'GW_AGENT_SESSION_UNAVAILABLE')

        # Symlinked session home directory
        real_home = make_valid_home('real-home')
        sym_home = Path(self.temp.name) / 'sym-dir'
        os.symlink(real_home, sym_home)
        with tempfile.TemporaryDirectory() as tmpdir, \
                mock.patch.object(gw_agent.llm_config, 'resolve_profile_config', return_value={}), \
                mock.patch.object(gw_agent.llm_config, 'api_key', return_value=None):
            req = self.request()
            req['session_id'] = sess_id
            req['session_home'] = str(sym_home)
            with self.assertRaises(RouteError) as cm:
                self.route.prepare_files(tmpdir, req)
            self.assertEqual(cm.exception.code, 'GW_AGENT_SESSION_UNAVAILABLE')

        # Group-readable source file
        home_grp_file = make_valid_home('grp-file')
        os.chmod(home_grp_file / 'meta.json', 0o640)
        with tempfile.TemporaryDirectory() as tmpdir, \
                mock.patch.object(gw_agent.llm_config, 'resolve_profile_config', return_value={}), \
                mock.patch.object(gw_agent.llm_config, 'api_key', return_value=None):
            req = self.request()
            req['session_id'] = sess_id
            req['session_home'] = str(home_grp_file)
            with self.assertRaises(RouteError) as cm:
                self.route.prepare_files(tmpdir, req)
            self.assertEqual(cm.exception.code, 'GW_AGENT_SESSION_UNAVAILABLE')

        # Group-readable session home directory
        home_grp_dir = make_valid_home('grp-dir')
        os.chmod(home_grp_dir, 0o750)
        with tempfile.TemporaryDirectory() as tmpdir, \
                mock.patch.object(gw_agent.llm_config, 'resolve_profile_config', return_value={}), \
                mock.patch.object(gw_agent.llm_config, 'api_key', return_value=None):
            req = self.request()
            req['session_id'] = sess_id
            req['session_home'] = str(home_grp_dir)
            with self.assertRaises(RouteError) as cm:
                self.route.prepare_files(tmpdir, req)
            self.assertEqual(cm.exception.code, 'GW_AGENT_SESSION_UNAVAILABLE')

        # Foreign-owned (patched uid) source
        home_foreign = make_valid_home('foreign')
        with tempfile.TemporaryDirectory() as tmpdir, \
                mock.patch.object(gw_agent.os, 'getuid', return_value=os.getuid() + 1), \
                mock.patch.object(gw_agent.llm_config, 'resolve_profile_config', return_value={}), \
                mock.patch.object(gw_agent.llm_config, 'api_key', return_value=None):
            req = self.request()
            req['session_id'] = sess_id
            req['session_home'] = str(home_foreign)
            with self.assertRaises(RouteError) as cm:
                self.route.prepare_files(tmpdir, req)
            self.assertEqual(cm.exception.code, 'GW_AGENT_SESSION_UNAVAILABLE')

        # Home inside D6 root
        home_inside_d6 = self.root / 'sessions'
        home_inside_d6.mkdir(mode=0o700)
        os.chmod(home_inside_d6, 0o700)
        for fname in ('meta.json', 'history.json'):
            f = home_inside_d6 / fname
            f.write_bytes(b'{"synthetic": true}\n')
            os.chmod(f, 0o600)
        with tempfile.TemporaryDirectory() as tmpdir, \
                mock.patch.object(gw_agent.llm_config, 'resolve_profile_config', return_value={}), \
                mock.patch.object(gw_agent.llm_config, 'api_key', return_value=None):
            req = self.request()
            req['session_id'] = sess_id
            req['session_home'] = str(home_inside_d6)
            with self.assertRaises(RouteError) as cm:
                self.route.prepare_files(tmpdir, req)
            self.assertEqual(cm.exception.code, 'GW_AGENT_SESSION_UNAVAILABLE')

    def test_copy_out_writes_0600_files_and_preserves_on_audit_or_session_mismatch(self):
        sess_id = '0123456789abcdef0123456789abcdef'
        home = Path(self.temp.name) / 'copy-out-home'
        home.mkdir(mode=0o700)
        os.chmod(home, 0o700)
        meta_data = b'{"type": "meta", "synthetic": 1}\n'
        hist_data = b'{"type": "history", "synthetic": 2}\n'

        # Successful copy-out writes 0600 files
        with tempfile.TemporaryDirectory() as tmpdir:
            dest_dir = Path(tmpdir) / 'state' / 'sessions' / sess_id
            dest_dir.mkdir(parents=True, mode=0o700)
            os.chmod(dest_dir, 0o700)
            for name, data in (('meta.json', meta_data), ('history.json', hist_data)):
                p = dest_dir / name
                p.write_bytes(data)
                os.chmod(p, 0o600)

            audit_path = Path(tmpdir) / 'audit.jsonl'
            digest = self._write_valid_audit(audit_path)

            result = {
                'schema_version': 1, 'status': 'DONE', 'code': None,
                'run_id': 'a' * 24, 'session_id': sess_id,
                'task_id': 'task-001', 'changed_paths': [],
                'summary': 'done', 'served_model': 'fake-model',
                'usage': {'status': 'REPORTED', 'calls': 1}, 'turns': 1,
                'budget_used': {}, 'audit_sha256': digest,
            }
            res_path = Path(tmpdir) / 'result.json'
            self._write_json(res_path, result)
            res_path.chmod(0o600)

            req = self.request()
            req['session_id'] = sess_id
            req['session_home'] = str(home)
            self.route.collect(tmpdir, req)

            out_meta = home / 'meta.json'
            out_hist = home / 'history.json'
            self.assertTrue(out_meta.is_file())
            self.assertTrue(out_hist.is_file())
            self.assertEqual(stat.S_IMODE(out_meta.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(out_hist.stat().st_mode), 0o600)
            self.assertEqual(out_meta.read_bytes(), meta_data)
            self.assertEqual(out_hist.read_bytes(), hist_data)

            out_meta.unlink()
            out_hist.unlink()

        # Audit check fails -> nothing copied
        with tempfile.TemporaryDirectory() as tmpdir:
            dest_dir = Path(tmpdir) / 'state' / 'sessions' / sess_id
            dest_dir.mkdir(parents=True, mode=0o700)
            for name, data in (('meta.json', meta_data), ('history.json', hist_data)):
                p = dest_dir / name
                p.write_bytes(data)
                os.chmod(p, 0o600)

            audit_path = Path(tmpdir) / 'audit.jsonl'
            self._write_valid_audit(audit_path)

            result = {
                'schema_version': 1, 'status': 'DONE', 'code': None,
                'run_id': 'a' * 24, 'session_id': sess_id,
                'task_id': 'task-001', 'changed_paths': [],
                'summary': 'done', 'served_model': 'fake-model',
                'usage': {}, 'turns': 1,
                'budget_used': {}, 'audit_sha256': '0' * 64,
            }
            res_path = Path(tmpdir) / 'result.json'
            self._write_json(res_path, result)
            res_path.chmod(0o600)

            req = self.request()
            req['session_id'] = sess_id
            req['session_home'] = str(home)
            with self.assertRaises(RouteError) as cm:
                self.route.collect(tmpdir, req)
            self.assertEqual(cm.exception.code, 'GW_AGENT_AUDIT_INVALID')
            self.assertFalse((home / 'meta.json').exists())
            self.assertFalse((home / 'history.json').exists())

        # Result names another session -> copies nothing
        with tempfile.TemporaryDirectory() as tmpdir:
            other_sess = 'fedcba9876543210fedcba9876543210'
            dest_dir = Path(tmpdir) / 'state' / 'sessions' / other_sess
            dest_dir.mkdir(parents=True, mode=0o700)
            for name, data in (('meta.json', meta_data), ('history.json', hist_data)):
                p = dest_dir / name
                p.write_bytes(data)
                os.chmod(p, 0o600)

            audit_path = Path(tmpdir) / 'audit.jsonl'
            digest = self._write_valid_audit(audit_path)

            result = {
                'schema_version': 1, 'status': 'DONE', 'code': None,
                'run_id': 'a' * 24, 'session_id': other_sess,
                'task_id': 'task-001', 'changed_paths': [],
                'summary': 'done', 'served_model': 'fake-model',
                'usage': {'status': 'REPORTED', 'calls': 1}, 'turns': 1,
                'budget_used': {}, 'audit_sha256': digest,
            }
            res_path = Path(tmpdir) / 'result.json'
            self._write_json(res_path, result)
            res_path.chmod(0o600)

            req = self.request()
            req['session_id'] = sess_id
            req['session_home'] = str(home)
            self.route.collect(tmpdir, req)
            self.assertFalse((home / 'meta.json').exists())
            self.assertFalse((home / 'history.json').exists())

        # Unsafe source file raises GW_AGENT_SESSION_PERSIST_FAILED
        with tempfile.TemporaryDirectory() as tmpdir:
            dest_dir = Path(tmpdir) / 'state' / 'sessions' / sess_id
            dest_dir.mkdir(parents=True, mode=0o700)
            meta_path = dest_dir / 'meta.json'
            meta_path.write_bytes(meta_data)
            os.chmod(meta_path, 0o640)
            hist_path = dest_dir / 'history.json'
            hist_path.write_bytes(hist_data)
            os.chmod(hist_path, 0o600)

            audit_path = Path(tmpdir) / 'audit.jsonl'
            digest = self._write_valid_audit(audit_path)

            result = {
                'schema_version': 1, 'status': 'DONE', 'code': None,
                'run_id': 'a' * 24, 'session_id': sess_id,
                'task_id': 'task-001', 'changed_paths': [],
                'summary': 'done', 'served_model': 'fake-model',
                'usage': {'status': 'REPORTED', 'calls': 1}, 'turns': 1,
                'budget_used': {}, 'audit_sha256': digest,
            }
            res_path = Path(tmpdir) / 'result.json'
            self._write_json(res_path, result)
            res_path.chmod(0o600)

            req = self.request()
            req['session_id'] = sess_id
            req['session_home'] = str(home)
            with self.assertRaises(RouteError) as cm:
                self.route.collect(tmpdir, req)
            self.assertEqual(cm.exception.code, 'GW_AGENT_SESSION_PERSIST_FAILED')


class GatewayCredentialConfigTests(unittest.TestCase):
    def test_null_key_env_gives_no_credential_and_skips_company_config(self):
        with mock.patch.object(gw_agent.gateway_profiles, 'load_gateway_profile',
                               return_value={'api_key_env': None}), \
                 mock.patch.object(gw_agent.llm_config, 'resolve_profile_config',
                                   side_effect=AssertionError('company-gw read')):
            config_value = gw_agent._gateway_credential_config()
            self.assertIsNone(gw_agent.llm_config.api_key(config_value))

    def test_named_key_env_is_read_from_the_environment(self):
        name = 'GW_AGENT_TEST_KEY_ENV'
        with mock.patch.object(gw_agent.gateway_profiles, 'load_gateway_profile',
                               return_value={'api_key_env': name}), \
                 mock.patch.dict(os.environ, {name: 'synthetic-value'}):
            config_value = gw_agent._gateway_credential_config()
            self.assertEqual(gw_agent.llm_config.api_key(config_value), 'synthetic-value')


if __name__ == '__main__':
    unittest.main()
