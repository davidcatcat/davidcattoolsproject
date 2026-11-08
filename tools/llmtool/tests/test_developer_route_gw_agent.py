"""Offline contract tests for the gw-agent Developer route."""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
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
                    gw_agent.llm_config, 'resolve_profile_config', side_effect=resolve_error)
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
                        'default_effort': 'low', 'denied_dirs': ['.gemini'],
                        'state_dirs': {'policy': 'private-temp-only'}},
                'gw-agent': {
                    'package_path': str(Path(self.temp.name) / 'gateway'),
                    'default_model': 'fake-model', 'default_effort': 'low',
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

    # #120 G1a: the gateway session of one D6 is copied from its private
    # session home into <route tmp>/state for a resume, and back after the
    # audit check. No gateway runs here; the state files are synthetic.
    _SESSION = 'c' * 32
    _STATE = {'meta.json': b'{"schema_version": 1, "note": "synthetic meta"}',
              'history.json': b'{"schema_version": 1, "note": "synthetic history"}'}

    @staticmethod
    def _private_dir(path):
        path = Path(path)
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.chmod(0o700)
        return path

    @staticmethod
    def _write_state(directory, payloads):
        for name, data in payloads.items():
            path = Path(directory) / name
            path.write_bytes(data)
            path.chmod(0o600)

    def _no_credential(self):
        stack = ExitStack()
        stack.enter_context(mock.patch.object(
            gw_agent.llm_config, 'resolve_profile_config', return_value={}))
        stack.enter_context(mock.patch.object(gw_agent.llm_config, 'api_key',
                                              return_value=None))
        return stack

    @staticmethod
    def _foreign_fstat():
        real_fstat = os.fstat

        def fstat(fd):
            info = real_fstat(fd)
            values = list(info[:10])
            values[4] = info.st_uid + 1
            return os.stat_result(values)
        return fstat

    def test_build_argv_runs_without_a_session_and_resumes_the_bound_one(self):
        request = dict(self.request(), _route_tmpdir='/private/route-tmp')
        run_argv = self.route.build_argv(sys.executable, request)
        resume_argv = self.route.build_argv(sys.executable,
                                            dict(request, session_id=self._SESSION))
        prefix = [sys.executable, '-B', '-P', '-m', 'api_agent_gateway']
        self.assertEqual(run_argv[:6], prefix + ['run'])
        self.assertNotIn('resume', run_argv)
        self.assertEqual(resume_argv[:7], prefix + ['resume', self._SESSION])
        # Everything after the command is the same for run and resume.
        self.assertEqual(resume_argv[7:], run_argv[6:])
        self.assertEqual(run_argv[run_argv.index('--state-dir') + 1],
                         str(Path('/private/route-tmp') / 'state'))

    def test_authorization_binds_the_resumed_session_and_null_for_run(self):
        home = self._private_dir(Path(self.temp.name) / 'session-home')
        self._write_state(home, self._STATE)
        resume = dict(self.request(), session_id=self._SESSION, session_home=str(home))
        for name, request, expected in (('run', self.request(), None),
                                        ('resume', resume, self._SESSION)):
            with self.subTest(command=name), tempfile.TemporaryDirectory() as tmpdir, \
                    self._no_credential():
                self.route.prepare_files(tmpdir, request)
                auth = json.loads((Path(tmpdir) / 'authorization.json').read_text())
                self.assertIn('session_id', auth)
                self.assertEqual(auth['session_id'], expected)
                # A run starts with no state; the gateway creates its own.
                self.assertEqual((Path(tmpdir) / 'state').exists(), expected is not None)

    def test_resume_request_needs_a_gateway_session_and_its_home(self):
        home = self._private_dir(Path(self.temp.name) / 'session-home')
        for name, extra in (
                ('no-home', {'session_id': self._SESSION}),
                ('relative-home', {'session_id': self._SESSION, 'session_home': 'home'}),
                ('bad-session', {'session_id': 'not-a-session', 'session_home': str(home)}),
                ('upper-session', {'session_id': 'C' * 32, 'session_home': str(home)})):
            with self.subTest(case=name), self.assertRaises(RouteError) as caught:
                self.route.validate_request(dict(self.request(), **extra))
            self.assertEqual(caught.exception.code, 'GW_AGENT_SESSION_UNAVAILABLE')

    def test_resume_copies_the_bound_state_into_the_route_tmp_privately(self):
        home = self._private_dir(Path(self.temp.name) / 'session-home')
        self._write_state(home, self._STATE)
        request = dict(self.request(), session_id=self._SESSION, session_home=str(home))
        with tempfile.TemporaryDirectory() as tmpdir, self._no_credential():
            self.route.prepare_files(tmpdir, request)
            state = Path(tmpdir) / 'state'
            target = state / 'sessions' / self._SESSION
            for directory in (state, state / 'sessions', target):
                info = directory.lstat()
                self.assertTrue(stat.S_ISDIR(info.st_mode))
                self.assertEqual(stat.S_IMODE(info.st_mode), 0o700)
            self.assertEqual(sorted(path.name for path in target.iterdir()),
                             sorted(self._STATE))
            for name, data in self._STATE.items():
                self.assertEqual(stat.S_IMODE((target / name).lstat().st_mode), 0o600)
                self.assertEqual((target / name).read_bytes(), data)
        # The session home is only read.
        for name, data in self._STATE.items():
            self.assertEqual((home / name).read_bytes(), data)

    def test_resume_refuses_a_symlinked_or_foreign_owned_session_source(self):
        for case in ('symlink', 'foreign-owner', 'missing', 'group-readable'):
            with self.subTest(source=case), tempfile.TemporaryDirectory() as outer, \
                    tempfile.TemporaryDirectory() as tmpdir:
                home = self._private_dir(Path(outer) / 'home')
                self._write_state(home, {'meta.json': self._STATE['meta.json']})
                if case == 'symlink':
                    self._write_state(outer, {'elsewhere.json': self._STATE['history.json']})
                    (home / 'history.json').symlink_to(Path(outer) / 'elsewhere.json')
                elif case != 'missing':
                    self._write_state(home, {'history.json': self._STATE['history.json']})
                if case == 'group-readable':
                    (home / 'history.json').chmod(0o640)
                request = dict(self.request(), session_id=self._SESSION,
                               session_home=str(home))
                root = Path(request['root']).resolve(strict=True)
                with ExitStack() as stack:
                    if case == 'foreign-owner':
                        stack.enter_context(mock.patch.object(
                            gw_agent.os, 'fstat', side_effect=self._foreign_fstat()))
                    with self.assertRaises(RouteError) as caught:
                        self.route._copy_state_in(request, root, Path(tmpdir).resolve())
                self.assertEqual(caught.exception.code, 'GW_AGENT_SESSION_UNAVAILABLE')
                self.assertFalse((Path(tmpdir) / 'state').exists())

    def _gateway_tmp(self, tmpdir, *, audit_ok=True):
        """A route tmp as the gateway leaves it: result, audit and session state."""
        audit_path = Path(tmpdir) / 'audit.jsonl'
        digest = self._write_valid_audit(audit_path, changed_paths=0)
        audit_path.chmod(0o600)
        result = {'schema_version': 1, 'status': 'DONE', 'code': None,
                  'run_id': 'a' * 24, 'session_id': self._SESSION, 'task_id': 'task-001',
                  'changed_paths': [], 'summary': 'done', 'served_model': 'fake-model',
                  'usage': {}, 'turns': 1, 'budget_used': {},
                  'audit_sha256': digest if audit_ok else '0' * 64}
        result_path = Path(tmpdir) / 'result.json'
        self._write_json(result_path, result)
        result_path.chmod(0o600)
        state = self._private_dir(Path(tmpdir) / 'state')
        source = self._private_dir(self._private_dir(state / 'sessions') / self._SESSION)
        self._write_state(source, self._STATE)
        return source

    def _collect_request(self, home, session_id=None):
        return {'root': str(self.root), 'task': {'task_id': 'task-001'},
                'session_home': str(home), 'session_id': session_id}

    def _forget_audit_copy(self):
        if self.route.audit_copy_path is not None:
            shutil.rmtree(Path(self.route.audit_copy_path).parent, ignore_errors=True)

    def test_collect_copies_the_session_state_back_after_the_audit_check(self):
        self.addCleanup(self._forget_audit_copy)
        home = self._private_dir(Path(self.temp.name) / 'session-home')
        for name, session_id in (('run', None), ('resume', self._SESSION)):
            with self.subTest(command=name), tempfile.TemporaryDirectory() as tmpdir:
                for path in list(home.iterdir()):
                    path.unlink()
                self._gateway_tmp(tmpdir)
                self.route.collect(tmpdir, self._collect_request(home, session_id))
                # The audit copy holds the audit only, never the session state.
                audit_copy = Path(self.route.audit_copy_path).read_text(encoding='utf-8')
                self.assertNotIn('synthetic history', audit_copy)
                self._forget_audit_copy()
                # Only the two gateway Store files, each 0600; no temp names left.
                self.assertEqual(sorted(path.name for path in home.iterdir()),
                                 sorted(self._STATE))
                for state_name, data in self._STATE.items():
                    self.assertEqual(stat.S_IMODE((home / state_name).lstat().st_mode),
                                     0o600)
                    self.assertEqual((home / state_name).read_bytes(), data)

    def test_collect_does_not_copy_state_when_the_audit_check_fails(self):
        self.addCleanup(self._forget_audit_copy)
        home = self._private_dir(Path(self.temp.name) / 'session-home')
        with tempfile.TemporaryDirectory() as tmpdir:
            self._gateway_tmp(tmpdir, audit_ok=False)
            with self.assertRaises(RouteError) as caught:
                self.route.collect(tmpdir, self._collect_request(home))
        self.assertEqual(caught.exception.code, 'GW_AGENT_AUDIT_INVALID')
        self.assertEqual(list(home.iterdir()), [])

    def test_copy_out_refuses_a_symlinked_or_foreign_owned_source(self):
        root = self.root.resolve()
        for case in ('symlink', 'foreign-owner', 'other-session'):
            with self.subTest(source=case), tempfile.TemporaryDirectory() as outer, \
                    tempfile.TemporaryDirectory() as tmpdir:
                home = self._private_dir(Path(outer) / 'home')
                source = self._gateway_tmp(tmpdir)
                if case == 'symlink':
                    (source / 'history.json').unlink()
                    self._write_state(outer, {'elsewhere.json': self._STATE['history.json']})
                    (source / 'history.json').symlink_to(Path(outer) / 'elsewhere.json')
                self.route._result = {'session_id': self._SESSION}
                requested = 'd' * 32 if case == 'other-session' else None
                request = self._collect_request(home, requested)
                with ExitStack() as stack:
                    if case == 'foreign-owner':
                        stack.enter_context(mock.patch.object(
                            gw_agent.os, 'fstat', side_effect=self._foreign_fstat()))
                    if case == 'other-session':
                        # A resumed result naming another session stores nothing.
                        self.assertIsNone(self.route._copy_state_out(tmpdir, request, root))
                    else:
                        with self.assertRaises(RouteError) as caught:
                            self.route._copy_state_out(tmpdir, request, root)
                        self.assertEqual(caught.exception.code,
                                         'GW_AGENT_SESSION_PERSIST_FAILED')
                self.assertEqual(list(home.iterdir()), [])

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


if __name__ == '__main__':
    unittest.main()
