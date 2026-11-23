"""#2602 item (f): dev-form session ids resume through binding.json.

A gw-agent Task records its session id in the ``dev-`` form, while the
gateway session home's binding.json holds the 32-hex id. A repair must resume
the bound session, and must refuse whenever the binding does not prove it
belongs to that repair.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from llmtool.developer_routes import GwAgentRoute, RouteError
from llmtool.developer_routes.base import capture_snapshot, run_route
from llmtool.developer_routes.gw_agent import (
    open_session_home,
    private_write,
    write_session_binding,
)

SESSION_HEX = '0123456789abcdef0123456789abcdef'
OTHER_HEX = 'fedcba9876543210fedcba9876543210'
DEV_SESSION = 'dev-' + SESSION_HEX
ROOT_TASK = 'task-root-synthetic-2602'
EXECUTOR_TASK = 'task-exec-synthetic-2602'


class _Boundary:
    def prepare(self, root, write_paths, state_dirs=()):
        return [], 'a' * 64

    def verify(self, root, write_paths, state_dirs=(), *, runner):
        return {'verified': True, 'policy_sha256': 'a' * 64, 'probes': []}


class GwAgentSessionResume2602Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.store_root = self.base / 'store' / 'task-store'
        self.store_root.parent.mkdir(parents=True, mode=0o700)

        package = self.base / 'gateway'
        (package / 'api_agent_gateway').mkdir(parents=True, mode=0o700)
        self.route = GwAgentRoute({
            'package_path': str(package),
            'profile_name': 'synthetic',
            'profile': {
                'adapter': 'openai-chat-tools',
                'base_url': 'http://127.0.0.1:9/v1',
                'model': 'synthetic-model',
                'auth_env': None,
                'context_cap_tokens': 10000,
                'max_output_tokens': 1000,
                'allowed_data_classes': ['synthetic'],
            },
            'default_model': 'synthetic-model',
            'default_effort': 'low',
            'authorization_ref': 'synthetic-authorization-ref',
            'allowed_data_classes': ['synthetic'],
        })

        self.root = self.base / 'repo'
        self.root.mkdir()
        subprocess.run(['/usr/bin/git', 'init', str(self.root)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['/usr/bin/git', '-C', str(self.root), 'config',
                        'user.name', 'Test'], check=True)
        subprocess.run(['/usr/bin/git', '-C', str(self.root), 'config',
                        'user.email', 'test@example.invalid'], check=True)
        (self.root / 'source.py').write_text('synthetic original\n')
        subprocess.run(['/usr/bin/git', '-C', str(self.root), 'add',
                        'source.py'], check=True)
        subprocess.run(['/usr/bin/git', '-C', str(self.root), 'commit', '-m',
                        'synthetic initial'], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.branch = subprocess.check_output(
            ['/usr/bin/git', '-C', str(self.root), 'branch', '--show-current'],
            text=True).strip()
        self.head = subprocess.check_output(
            ['/usr/bin/git', '-C', str(self.root), 'rev-parse', 'HEAD'],
            text=True).strip()

    def _make_home(self, executor_task_id=EXECUTOR_TASK):
        home = open_session_home(self.store_root, executor_task_id)
        for name in ('meta.json', 'history.json'):
            private_write(home / name, b'{"synthetic": true}\n')
        return home

    def _bind(self, home, *, executor_task_id=EXECUTOR_TASK,
              root_task_id=ROOT_TASK, session_id=SESSION_HEX):
        write_session_binding(
            home,
            root_task_id=root_task_id,
            executor_task_id=executor_task_id,
            session_id=session_id,
            data_class='synthetic',
            scope_digest='a' * 64,
        )

    def _request(self, session_id, session_home, *, task_id=ROOT_TASK):
        return {
            'root': str(self.root),
            'model': 'synthetic-model',
            'effort': 'low',
            'expected_branch': self.branch,
            'expected_head': self.head,
            'write_paths': ['source.py'],
            'executable': sys.executable,
            'task': {
                'schema_version': 1,
                'task_id': task_id,
                'source_sha': self.head,
                'goal': 'Edit one synthetic source file.',
                'acceptance': ['source.py changes'],
                'read_paths': ['source.py'],
                'write_paths': ['source.py'],
                'egress_basis': 'Synthetic test content only.',
                'data_class': 'synthetic',
                'budgets': {
                    'max_turns': 4, 'max_input_tokens': 10000,
                    'max_output_tokens': 1000, 'max_wall_seconds': 60,
                    'max_tool_result_bytes': 4096, 'max_sent_bytes': 100000,
                },
                'findings': [],
            },
            'session_id': session_id,
            'session_home': str(session_home),
        }

    def _prepare(self, request):
        validated = self.route.validate_request(request)
        tmpdir = tempfile.mkdtemp(prefix='gw-agent-resume-2602-')
        self.addCleanup(shutil.rmtree, tmpdir, True)
        with mock.patch.object(self.route, '_load_credential', return_value=None):
            self.route.prepare_files(tmpdir, validated)
            authorization = json.loads(
                (Path(tmpdir) / 'authorization.json').read_text(encoding='utf-8'))
            argv = self.route.build_argv(sys.executable, validated)
        return validated, authorization, argv, Path(tmpdir)

    def _assert_refused_before_model(self, request):
        runner = mock.Mock(side_effect=AssertionError('the model must not be called'))
        with mock.patch.object(self.route, '_load_credential', return_value=None), \
                self.assertRaises(RouteError) as caught:
            run_route(self.route, request, gate=lambda: True, runner=runner,
                      boundary=_Boundary(), snapshot=capture_snapshot)
        self.assertEqual(caught.exception.code, 'GW_AGENT_SESSION_UNAVAILABLE')
        runner.assert_not_called()

    def test_dev_form_session_resumes_with_the_bound_hex_id(self):
        home = self._make_home()
        self._bind(home)

        validated, authorization, argv, tmpdir = self._prepare(
            self._request(DEV_SESSION, home))

        self.assertEqual(validated['session_id'], SESSION_HEX)
        self.assertEqual(authorization['session_id'], SESSION_HEX)
        self.assertEqual(argv[argv.index('resume') + 1], SESSION_HEX)
        self.assertTrue(
            (tmpdir / 'state' / 'sessions' / SESSION_HEX / 'meta.json').is_file())

    def test_hex_session_still_resumes(self):
        home = self._make_home()
        self._bind(home, session_id=OTHER_HEX)

        validated, authorization, argv, _tmpdir = self._prepare(
            self._request(OTHER_HEX, home))

        self.assertEqual(validated['session_id'], OTHER_HEX)
        self.assertEqual(authorization['session_id'], OTHER_HEX)
        self.assertEqual(argv[argv.index('resume') + 1], OTHER_HEX)

    def test_mismatched_executor_refuses_before_the_model(self):
        home = self._make_home(EXECUTOR_TASK)
        self._bind(home, executor_task_id='task-exec-synthetic-other')

        self._assert_refused_before_model(self._request(DEV_SESSION, home))

    def test_mismatched_root_refuses_before_the_model(self):
        home = self._make_home()
        self._bind(home, root_task_id='task-root-synthetic-other')

        self._assert_refused_before_model(self._request(DEV_SESSION, home))

    def test_missing_binding_refuses_before_the_model(self):
        home = self._make_home()

        self._assert_refused_before_model(self._request(DEV_SESSION, home))

    def test_malformed_binding_refuses_before_the_model(self):
        home = self._make_home()
        private_write(home / 'binding.json', b'not json\n')

        self._assert_refused_before_model(self._request(DEV_SESSION, home))

    def test_session_id_in_neither_valid_form_is_refused(self):
        home = self._make_home()
        for bad_id in ('dev-' + 'a' * 31, 'dev-' + 'A' * 32, 'dev-', 'session-' + SESSION_HEX):
            with self.subTest(session_id=bad_id):
                with self.assertRaises(RouteError) as caught:
                    self.route.validate_request(self._request(bad_id, home))
                self.assertEqual(caught.exception.code, 'GW_AGENT_SESSION_UNAVAILABLE')


if __name__ == '__main__':
    unittest.main()
