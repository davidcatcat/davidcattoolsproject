import json
import io
import os
import site
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml
from unittest.mock import Mock, patch

from test_provider import success
from llmtool.context import format_context_packet, parse_context_packet
from llmtool import task_submission_cli
from llmtool.cli import TASK_ACTIONS, parser_for_cli

ENTRYPOINT = Path(__file__).resolve().parents[1] / "llmtool.py"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.requests.append((self.path, dict(self.headers), json.loads(body)))
        status, payload, delay, headers = self.server.reply
        if delay:
            time.sleep(delay)
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass


class CLITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = "http://127.0.0.1:" + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cwd = Path(self.temp.name)
        self.home = self.cwd / "home"
        self.home.mkdir()
        # Do not inherit user config, provider overrides, credentials or proxies.
        self.env = {
            "PYTHONUSERBASE": site.getuserbase(),
            "HOME": str(self.home), "PATH": os.environ.get("PATH", ""),
            "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
            "LLMTOOL_BASE_URL": self.base_url,
            "LLMTOOL_MODEL": "test-model",
        }
        self.server.requests = []
        self.server.reply = (200, success(), 0, {})

    def run_cli(self, *args, data=b""):
        return subprocess.run(
            [sys.executable, str(ENTRYPOINT), *args], input=data,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=self.cwd, env=self.env, timeout=10, check=False,
        )

    def run_trusted_cli(self, *args):
        """CLI child under the fixture trust (#369 PR-7 grant deny-list reads trust)."""
        import review_trust_fixture
        return subprocess.run(
            review_trust_fixture.cli_command(*args), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
            cwd=self.cwd, env=self.env, timeout=10, check=False,
        )

    def assert_failure(self, result):
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertTrue(result.stderr.startswith(b"llmtool:"), result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)

    def test_help_and_version_without_config(self):
        for args in (("--help",), ("ask", "--help"), ("config", "--help"),
                     ("configure", "--help"), ("--version",)):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(b"llmtool", result.stdout)
        self.assertEqual(self.server.requests, [])

    def test_task_action_table_matches_parsers_and_freshness_policy(self):
        self.assertEqual(list(TASK_ACTIONS), sorted(TASK_ACTIONS))
        for function_name, _needs_fresh_source in TASK_ACTIONS.values():
            self.assertTrue(callable(getattr(task_submission_cli, function_name)))

        expected_freshness = {
            'delegate', 'review-dispatch', 'high-debug', 'repair', 'remote-test', 'drive',
            'claude-bind', 'file-ops',
        }
        self.assertEqual(
            {action for action, (_function_name, fresh) in TASK_ACTIONS.items() if fresh},
            expected_freshness,
        )
        self.assertEqual(
            {action for action, (_function_name, fresh) in TASK_ACTIONS.items() if not fresh},
            set(TASK_ACTIONS) - expected_freshness,
        )

        parser = parser_for_cli()
        task_parser = parser._subparsers._group_actions[0].choices['task']
        parsed_actions = set(task_parser._subparsers._group_actions[0].choices)
        unchanged_actions = {
            'inspect', 'review', 'review-request', 'review-wait', 'review-bundle', 'train',
        }
        self.assertEqual(parsed_actions - set(TASK_ACTIONS), unchanged_actions)

    def test_claude_bind_cli_dispatches_to_handler(self):
        from llmtool.cli import main
        from unittest.mock import patch
        called = []
        with patch('llmtool.source_freshness.require_fresh_source',
                   return_value='OK') as freshness, \
                patch.object(task_submission_cli, 'run_task_claude_bind_checked',
                             side_effect=lambda args: called.append(args) or 0):
            rc = main(['task', 'claude-bind', 'a' * 32, '--executor-task', 'd6-task',
                       '--owner-ref', 'https://github.com/example/repo/issues/1',
                       '--read-path', 'tools/llmtool/tests/test_fixture.py',
                       '--write-path', 'tools/llmtool/tests/test_fixture.py'])
        self.assertEqual(rc, 0)
        self.assertEqual(len(called), 1)
        self.assertEqual(called[0].action, 'claude-bind')
        freshness.assert_called_once()
        parsed = called[0]
        self.assertEqual(parsed.task_id, 'a' * 32)
        self.assertEqual(parsed.executor_task, 'd6-task')
        self.assertEqual(parsed.owner_ref, 'https://github.com/example/repo/issues/1')

    def test_file_ops_cli_needs_fresh_source_and_dispatches(self):
        from llmtool.cli import main
        called = []
        with patch('llmtool.source_freshness.require_fresh_source',
                   return_value='OK') as freshness, \
                patch.object(task_submission_cli, 'run_task_file_ops',
                             side_effect=lambda args: called.append(args) or 0):
            rc = main(['task', 'file-ops', 'a' * 32, '--executor-task', 'd6-task',
                       '--pm-session', 'pm-test-session'])
        self.assertEqual(rc, 0)
        freshness.assert_called_once()
        self.assertEqual(len(called), 1)
        self.assertEqual((called[0].action, called[0].task_id, called[0].executor_task,
                          called[0].pm_session, called[0].common_dir),
                         ('file-ops', 'a' * 32, 'd6-task', 'pm-test-session', None))

    def test_review_bundle_is_local_early_dispatch_and_sanitized_json(self):
        result = self.run_cli('task','review-bundle','--help')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn(b'--evidence',result.stdout)
        self.assertIn(b'--output',result.stdout)
        result = self.run_trusted_cli('task','review-bundle','--contract','/private/missing-contract',
            '--expect','/private/missing-expect','--events','/private/missing-events',
            '--base','1'*40,'--evidence','/private/missing-index',
            '--output','/private/missing-output','--json')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(result.stderr,b'')
        data = json.loads(result.stdout)
        self.assertEqual(data['status'],'BLOCKED')
        self.assertFalse(data['authorized'])
        self.assertEqual(data['runtime_evidence_status'],'NOT_VERIFIED')
        self.assertNotIn(b'/private/',result.stdout)
        self.assertNotIn(b'Traceback',result.stdout)
        self.assertEqual(self.server.requests,[])

    def test_review_exchange_commands_dispatch_locally_without_provider(self):
        for command in ('review-request','review-wait'):
            result = self.run_cli('task',command,'--help')
            self.assertEqual(result.returncode,0,result.stderr)
        # Fixture trust: these children must never read the real trust.json.
        request = self.run_trusted_cli('task','review-request','--bundle','/private/missing-bundle',
            '--bundle-sha256','0'*64,'--expect','/private/missing-expect',
            '--output','/private/missing-request','--json')
        wait = self.run_trusted_cli('task','review-wait','--request','/private/missing-request',
            '--request-sha256','0'*64,'--receipt','/private/missing-receipt',
            '--verdict','/private/missing-verdict','--timeout','0',
            '--output','/private/missing-result','--json')
        for result in (request,wait):
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(json.loads(result.stdout)['status'],'BLOCKED')
            self.assertFalse(json.loads(result.stdout)['authorized'])
            self.assertEqual(result.stderr,b'')
            self.assertNotIn(b'/private/',result.stdout)
        self.assertEqual(self.server.requests,[])

    def test_test_excerpts_public_command_retains_failed_exit_and_provenance(self):
        from test_test_output_excerpts import UNIT_FAILURE, FAIL_ID
        import hashlib
        path = (self.cwd / 'PRIVATE_CAPTURE_PATH.log').resolve()
        path.write_bytes(UNIT_FAILURE)
        result = self.run_cli('test-excerpts', '--file', str(path), '--exit-code', '7', '--json')
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(result.stderr, b'')
        evidence = json.loads(result.stdout)
        self.assertEqual(evidence['schema_version'], 1)
        self.assertEqual(evidence['status'], 'COMPLETE')
        self.assertEqual(evidence['test_status'], 'FAILED')
        self.assertEqual(evidence['test_exit_code'], 7)
        self.assertEqual(evidence['failure_ids'], [FAIL_ID])
        self.assertEqual(evidence['source']['sha256'], hashlib.sha256(UNIT_FAILURE).hexdigest())
        self.assertNotIn(b'PRIVATE_CAPTURE_PATH', result.stdout + result.stderr)
        self.assertEqual(path.read_bytes(), UNIT_FAILURE)
        self.assertEqual(self.server.requests, [])

    def test_test_excerpts_zero_unknown_signal_and_inconsistent_metadata(self):
        from test_test_output_excerpts import UNIT_FAILURE, unittest_capture
        path = (self.cwd / 'capture.log').resolve()
        cases = ((unittest_capture([], run_count=2), 0, 0, 'COMPLETE', 'NO_FAILURES_REPORTED'),
                 (unittest_capture([], run_count=2), 5, 5, 'INCOMPLETE', 'FAILED'),
                 (b'opaque failure\n', 3, 3, 'INCOMPLETE', 'FAILED'),
                 (b'opaque result\n', 0, 1, 'INCOMPLETE', 'UNKNOWN'),
                 (UNIT_FAILURE, 0, 1, 'INCOMPLETE', 'FAILED'),
                 (UNIT_FAILURE, -15, 1, 'COMPLETE', 'FAILED'))
        for raw, captured_exit, cli_exit, status, test_status in cases:
            with self.subTest(captured_exit=captured_exit, status=status):
                path.write_bytes(raw)
                result = self.run_cli('test-excerpts', '--file', str(path), '--exit-code', str(captured_exit), '--json')
                self.assertEqual(result.returncode, cli_exit, result.stderr)
                self.assertEqual(result.stderr, b'')
                evidence = json.loads(result.stdout)
                self.assertEqual(evidence['test_exit_code'], captured_exit)
                self.assertEqual(evidence['status'], status)
                self.assertEqual(evidence['test_status'], test_status)
                self.assertNotEqual(evidence['test_status'], 'PASS')
        self.assertEqual(self.server.requests, [])

    def test_test_excerpts_ignores_hostile_config_credentials_and_stdin(self):
        from test_test_output_excerpts import UNIT_FAILURE
        self.env['LLMTOOL_MODEL'] = 'INVALID_MODEL_VALUE'
        self.env['LLMTOOL_API_KEY'] = 'UNUSED_CREDENTIAL_VALUE'
        project = self.cwd / '.llmtool.yaml'
        project.write_text('private_project: [MALFORMED_PROJECT_VALUE')
        config = self.home / '.config/llmtool/config.yaml'
        config.parent.mkdir(parents=True)
        config.write_text('private_user: [MALFORMED_USER_VALUE')
        path = (self.cwd / 'capture.log').resolve()
        path.write_bytes(UNIT_FAILURE)
        before = {p.relative_to(self.cwd).as_posix(): p.read_bytes()
                  for p in self.cwd.rglob('*') if p.is_file()}
        result = self.run_cli('test-excerpts', '--file', str(path), '--exit-code', '1', '--json',
                              data=b'UNSELECTED_STDIN_VALUE')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr, b'')
        self.assertEqual(json.loads(result.stdout)['status'], 'COMPLETE')
        for marker in (b'UNUSED_CREDENTIAL_VALUE', b'MALFORMED_PROJECT_VALUE',
                       b'MALFORMED_USER_VALUE', b'UNSELECTED_STDIN_VALUE'):
            self.assertNotIn(marker, result.stdout + result.stderr)
        self.assertEqual(self.server.requests, [])
        self.assertEqual(before, {p.relative_to(self.cwd).as_posix(): p.read_bytes()
                                  for p in self.cwd.rglob('*') if p.is_file()})

    def test_test_excerpts_rejects_unsafe_files_without_stderr_source_leaks(self):
        from test_test_output_excerpts import UNIT_FAILURE, MAX_FILE_BYTES
        path = (self.cwd / 'PRIVATE_CAPTURE_PATH.log').resolve()
        for raw in (b'api_key=FIXTURE_SECRET_VALUE\n' + UNIT_FAILURE,
                    UNIT_FAILURE + b'\x1b[31m', b'\xff\x00', b'x' * (MAX_FILE_BYTES + 1)):
            with self.subTest(size=len(raw)):
                path.write_bytes(raw)
                result = self.run_cli('test-excerpts', '--file', str(path), '--exit-code', '1', '--json')
                self.assert_failure(result)
                self.assertNotIn(b'PRIVATE_CAPTURE_PATH', result.stderr)
                self.assertNotIn(b'FIXTURE_SECRET_VALUE', result.stderr)
                self.assertNotIn(b'\x1b', result.stderr)
                self.assertEqual(path.read_bytes(), raw)
        link = self.cwd / 'PRIVATE_SYMLINK.log'
        link.symlink_to(path)
        for file in (self.cwd.resolve() / link.name, self.cwd.resolve(), self.cwd.resolve() / 'PRIVATE_MISSING.log'):
            result = self.run_cli('test-excerpts', '--file', str(file), '--exit-code', '1', '--json')
            self.assert_failure(result)
            self.assertNotIn(b'PRIVATE_', result.stderr)
        self.assertEqual(self.server.requests, [])

    def test_test_excerpts_truncated_sensitive_excerpt_fails_closed_without_leak(self):
        from test_test_output_excerpts import (
            EMITTED_SENSITIVE_ERROR, FAIL_BODY, FAIL_ID, SYNTHETIC_AWS_KEY, unittest_capture)
        path = (self.cwd / 'PRIVATE_CAPTURE_PATH.log').resolve()
        raw = unittest_capture([('FAIL', FAIL_ID, FAIL_BODY + '\n' + SYNTHETIC_AWS_KEY + 'MORE')])
        bound = raw.index(SYNTHETIC_AWS_KEY.encode()) + len(SYNTHETIC_AWS_KEY) - raw.index(b'FAIL: ')
        escaped = unittest_capture([('FAIL', FAIL_ID, FAIL_BODY + '\n' + SYNTHETIC_AWS_KEY + '測試')])
        for capture, extra, expected_error in (
            (raw, ('--max-excerpt-bytes', str(bound)), EMITTED_SENSITIVE_ERROR),
            (escaped, (), 'test-excerpts: source contains sensitive-content indicators; details omitted'),
        ):
            with self.subTest(extra=extra):
                path.write_bytes(capture)
                result = self.run_cli('test-excerpts', '--file', str(path), '--exit-code', '1',
                                      '--json', *extra)
                self.assert_failure(result)
                self.assertEqual(result.stderr, b'llmtool: ' + expected_error.encode() + b'\n')
                for marker in (SYNTHETIC_AWS_KEY.encode(), b'AKIA', b'PRIVATE_CAPTURE_PATH', b'evidence'):
                    self.assertNotIn(marker, result.stdout + result.stderr)
                self.assertEqual(path.read_bytes(), capture)
        self.assertEqual(self.server.requests, [])

    def write_worker_config(self):
        config = self.home / '.config/llmtool/config.yaml'
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text('active_provider: decoy\nproviders:\n  decoy:\n    base_url: ' + self.base_url +
                          '\n    model: decoy-model\n  company-test:\n    base_url: ' + self.base_url +
                          '\n    model: PRIVATE_MODEL_NAME\n    timeout_seconds: 60\n')
        return config

    def test_test_triage_worker_public_live_route_is_blocked_with_zero_requests(self):
        from test_test_output_excerpts import UNIT_FAILURE
        config = self.write_worker_config()
        before = config.read_bytes()
        path = (self.cwd / 'PRIVATE_CAPTURE_PATH.log').resolve()
        path.write_bytes(UNIT_FAILURE)
        self.env['LLMTOOL_PROVIDER'] = 'decoy'
        result = self.run_cli('test-triage-worker', '--file', str(path), '--exit-code', '1',
                              '--profile', 'company-test', '--json')
        self.assert_failure(result)
        self.assertEqual(result.stderr, b'llmtool: test-triage-worker: live company route is not '
                                        b'authorized; details omitted\n')
        for marker in (b'PRIVATE_', self.base_url.encode(), b'company-test', b'Traceback'):
            self.assertNotIn(marker, result.stdout + result.stderr)
        self.assertEqual(self.server.requests, [])
        self.assertEqual(config.read_bytes(), before)
        self.assertEqual(path.read_bytes(), UNIT_FAILURE)

    def test_test_triage_worker_local_rejections_are_fixed_and_leak_free(self):
        from test_test_output_excerpts import UNIT_FAILURE
        self.write_worker_config()
        path = (self.cwd / 'PRIVATE_CAPTURE_PATH.log').resolve()
        cases = ((UNIT_FAILURE, 'PRIVATE_MISSING_PROFILE', b'explicit profile unavailable'),
                 (b'api_key=PRIVATE_SECRET_VALUE\n' + UNIT_FAILURE, 'company-test',
                  b'source contains sensitive-content indicators'),
                 (b'opaque output\n', 'company-test', b'selection incomplete'))
        for raw, profile, category in cases:
            with self.subTest(category=category):
                path.write_bytes(raw)
                result = self.run_cli('test-triage-worker', '--file', str(path), '--exit-code', '1',
                                      '--profile', profile, '--json')
                self.assert_failure(result)
                self.assertIn(category, result.stderr)
                self.assertNotIn(b'PRIVATE_', result.stdout + result.stderr)
        for extra in ((), ('--profile', 'company-test'), ('--json',)):
            result = self.run_cli('test-triage-worker', '--file', str(path), '--exit-code', '1', *extra)
            self.assert_failure(result)
        help_result = self.run_cli('test-triage-worker', '--help')
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn(b'--profile', help_result.stdout)
        self.assertEqual(self.server.requests, [])

    def test_test_triage_worker_phase0_grant_arguments(self):
        import hashlib
        from test_test_output_excerpts import UNIT_FAILURE
        self.write_worker_config()
        path = (self.cwd / 'PRIVATE_CAPTURE_PATH.log').resolve()
        path.write_bytes(UNIT_FAILURE)
        grant_dir = (self.cwd / 'grant').resolve()
        grant_dir.mkdir(mode=0o700)
        grant = grant_dir / 'grant.json'
        grant.write_text(json.dumps({'schema_version': 1, 'kind': 'llmtool.company-phase0-grant',
                                     'policy_id': 'phase0-cli', 'version': 1, 'profile': 'company-test',
                                     'origin': self.base_url, 'model': 'PRIVATE_MODEL_NAME',
                                     'capture_sha256': ['0' * 64], 'max_calls': 1,
                                     'not_before': '2000-01-01T00:00:00Z', 'not_after': '2100-01-01T00:00:00Z',
                                     'authorizer_ref': 'https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-1'}))
        os.chmod(grant, 0o600)
        pin = hashlib.sha256(grant.read_bytes()).hexdigest()
        base = ('test-triage-worker', '--file', str(path), '--exit-code', '1', '--profile', 'company-test', '--json')
        for extra, category in ((('--phase0-grant', str(grant)), b'live company route is not authorized'),
                                (('--phase0-grant-sha256', pin), b'live company route is not authorized'),
                                (('--phase0-grant', str(grant), '--phase0-grant-sha256', '0' * 64),
                                 b'phase0 grant invalid'),
                                (('--phase0-grant', str(grant), '--phase0-grant-sha256', pin),
                                 b'phase0 grant does not cover capture')):
            with self.subTest(extra=extra[0]):
                result = self.run_trusted_cli(*base, *extra)
                self.assert_failure(result)
                self.assertIn(category, result.stderr)
                for marker in (b'PRIVATE_', self.base_url.encode(), str(grant).encode(), b'Traceback'):
                    self.assertNotIn(marker, result.stdout + result.stderr)
        self.assertEqual(self.server.requests, [])

    def test_worker_v2_fake_local_http_preserves_failed_exit(self):
        from llmtool import test_triage_worker as worker
        from test_test_output_excerpts import UNIT_FAILURE
        import hashlib
        self.write_worker_config()
        path = (self.cwd / 'synthetic-capture.log').resolve(); path.write_bytes(UNIT_FAILURE)
        selection = worker.select_test_excerpts(path, 7)
        _, _, refs = worker.build_packet(selection)
        classifications = [dict(id=item, category='unknown', rationale='synthetic only',
            excerpt_refs=[ref for ref, owner in refs.items() if owner == item])
            for item in selection['failure_ids']]
        self.server.reply = (200, success(json.dumps({'classifications': classifications})), 0, {})
        private = (self.cwd / 'private-grant').resolve(); private.mkdir(mode=0o700)
        grant = private / 'synthetic-only.json'
        grant.write_text(json.dumps(dict(schema_version=1,kind='llmtool.company-phase0-grant',
            policy_id='phase0-local-fixture',version=1,profile='company-test',origin=self.base_url,
            model='PRIVATE_MODEL_NAME',capture_sha256=[hashlib.sha256(UNIT_FAILURE).hexdigest()],max_calls=1,
            not_before='2000-01-01T00:00:00Z',not_after='2100-01-01T00:00:00Z',
            authorizer_ref='https://github.com/davidcatcat/davidcattoolsproject/issues/120#issuecomment-1')))
        grant.chmod(0o600); pin = hashlib.sha256(grant.read_bytes()).hexdigest()
        result = self.run_trusted_cli('test-triage-worker','--file',str(path),'--exit-code','7',
            '--profile','company-test','--json','--phase0-grant',str(grant),'--phase0-grant-sha256',pin)
        self.assertEqual(result.returncode,7,result.stderr); self.assertEqual(result.stderr,b'')
        value=json.loads(result.stdout); self.assertEqual(value['schema_version'],2)
        self.assertEqual(set(value['digests']),{'request_body','reply'})
        self.assertEqual(len(self.server.requests),1)
        self.assertNotIn(b'PRIVATE_',result.stdout)
        self.assertNotIn(self.base_url.encode(),result.stdout)
        self.assertNotIn(str(grant).encode(),result.stdout)

    def test_test_excerpts_arguments_help_and_bounded_output(self):
        from test_test_output_excerpts import UNIT_FAILURE
        path = (self.cwd / 'capture.log').resolve()
        path.write_bytes(UNIT_FAILURE)
        help_result = self.run_cli('test-excerpts', '--help')
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn(b'--exit-code', help_result.stdout)
        for extra in ((), ('--exit-code', '1'), ('--json',),
                      ('--exit-code', 'PRIVATE_INVALID_VALUE', '--json'),
                      ('--exit-code', '256', '--json'),
                      ('--exit-code', '1', '--json', '--max-excerpts', '0'),
                      ('--exit-code', '1', '--json', '--max-excerpt-bytes', '127'),
                      ('--exit-code', '1', '--json', '--provider', 'PRIVATE_PROFILE')):
            result = self.run_cli('test-excerpts', '--file', str(path), *extra)
            self.assert_failure(result)
            self.assertNotIn(b'PRIVATE_', result.stderr)
        result = self.run_cli('test-excerpts', '--file', str(path), '--exit-code', '1', '--json',
                              '--max-excerpt-bytes', '128', '--max-excerpts', '1')
        self.assertEqual(result.returncode, 1, result.stderr)
        evidence = json.loads(result.stdout)
        self.assertEqual(len(evidence['excerpts']), 1)
        self.assertLessEqual(len(evidence['excerpts'][0]['text'].encode()), 128)
        self.assertTrue(evidence['excerpts'][0]['truncated'])
        self.assertEqual(self.server.requests, [])

    def test_test_excerpts_does_not_invoke_provider_shell_repo_or_credentials(self):
        from contextlib import ExitStack
        from llmtool.cli import main
        from test_test_output_excerpts import UNIT_FAILURE
        path = (self.cwd / 'capture.log').resolve()
        path.write_bytes(UNIT_FAILURE)
        out, err = io.StringIO(), io.StringIO()
        with ExitStack() as stack:
            for name in ('llmtool.cli.resolve_config', 'llmtool.cli.api_key', 'llmtool.cli.create_client',
                         'llmtool.cli.collect_git_snapshot', 'llmtool.cli.load_skill',
                         'subprocess.run', 'subprocess.Popen', 'socket.socket',
                         'os.scandir', 'os.listdir', 'os.chdir'):
                stack.enter_context(patch(name, side_effect=AssertionError('unexpected operation')))
            stack.enter_context(patch('sys.stdout', out))
            stack.enter_context(patch('sys.stderr', err))
            self.assertEqual(main(['test-excerpts', '--file', str(path), '--exit-code', '1', '--json']), 1)
        self.assertEqual(err.getvalue(), '')
        self.assertEqual(json.loads(out.getvalue())['status'], 'COMPLETE')

    def test_test_excerpts_broken_pipe_does_not_turn_failed_capture_into_success(self):
        from llmtool.cli import main
        from test_test_output_excerpts import UNIT_FAILURE
        path = (self.cwd / 'capture.log').resolve()
        path.write_bytes(UNIT_FAILURE)

        class Broken:
            def write(self, value):
                raise BrokenPipeError()

        with patch('sys.stdout', Broken()):
            self.assertEqual(main(['test-excerpts', '--file', str(path), '--exit-code', '7', '--json']), 7)

    def test_configure_noninteractive_fails_without_touching_real_home_or_network(self):
        result = self.run_cli("configure")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"input ended", result.stderr)
        self.assertNotIn(b"API key", result.stdout + result.stderr)
        self.assertFalse((self.home / ".config/llmtool/config.yaml").exists())
        self.assertEqual(self.server.requests, [])

    def test_providers_without_config_or_auth_and_ignores_hostile_config(self):
        self.env.pop("LLMTOOL_BASE_URL")
        self.env.pop("LLMTOOL_MODEL")
        user = self.home / ".config/llmtool/config.yaml"
        for hostile in (False, True):
            if hostile:
                user.parent.mkdir(parents=True)
                user.write_text("providers: {default: {api_key_env: PRIVATE_TEST_KEY}}")
                (self.cwd / ".llmtool.yaml").write_text("invalid: [SECRET_PROJECT")
                self.env["PRIVATE_TEST_KEY"] = "SECRET_CREDENTIAL"
            before = user.read_bytes() if hostile else None
            for args in (("providers",), ("providers", "--json")):
                result = self.run_cli(*args, data=b"SECRET_INPUT")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, b"")
                self.assertIn(b"openai-compatible", result.stdout)
                self.assertNotIn(b"SECRET", result.stdout)
                self.assertNotIn(b"PRIVATE_TEST_KEY", result.stdout)
                if "--json" in args:
                    self.assertEqual(list(json.loads(result.stdout)),
                                     ["claude-cli", "codex-cli", "openai", "openai-compatible"])
                self.assertEqual(self.server.requests, [])
                self.assertEqual(user.read_bytes() if user.exists() else None, before)

    def test_direct_argument(self):
        result = self.run_cli("ask", "why did this fail?")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"final answer\n")
        self.assertEqual(result.stderr, b"")
        self.assertEqual(self.server.requests[0][2]["messages"][1]["content"], "why did this fail?")

    def test_stdin_all_commands_and_unicode(self):
        for command in ("ask", "analyze-log", "review-diff", "context-pack"):
            with self.subTest(command=command):
                packet_text = json.dumps({name: [] for name in
                                          ("facts", "evidence", "inferences", "hypotheses", "unknowns", "verify_next")})
                self.server.reply = (200, success(packet_text if command == "context-pack" else ("NEXT_COMMANDS:\n(none)" if command == "analyze-log" else "final answer")), 0, {})
                result = self.run_cli(command, data="測試 evidence\n".encode())
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = format_context_packet(parse_context_packet(packet_text)) if command == "context-pack" else ("NEXT_COMMANDS:\n(none)" if command == "analyze-log" else "final answer")
                self.assertEqual(result.stdout.decode(), expected + "\n")
                path, headers, payload = self.server.requests[-1]
                self.assertEqual(path, "/v1/chat/completions")
                self.assertEqual(payload["messages"][1]["content"], "測試 evidence\n")
                self.assertNotIn("chat_template_kwargs", payload)
                self.assertEqual(headers["Content-Type"], "application/json")

    def test_file(self):
        path = self.cwd / "app.log"
        path.write_text("ERROR evidence\n")
        self.server.reply = (200, success("NEXT_COMMANDS:\n(none)"), 0, {})
        result = self.run_cli("analyze-log", "--file", str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.server.requests[0][2]["messages"][1]["content"], "ERROR evidence\n")

    def test_nonempty_stdin_conflicts_with_explicit_input_before_provider(self):
        path = self.cwd / "app.log"
        path.write_text("private log")
        for args, expected in ((('ask', 'private-text'), 'private-text'),
                               (('ask', '--file', str(path)), 'private log')):
            with self.subTest(args=args):
                result = self.run_cli(*args, data=b'pipe must remain unread')
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(b"provide only one", result.stderr)
                self.assertEqual(result.stdout, b"")
                self.assertEqual(self.server.requests, [])

    def test_closed_stdin_has_sanitized_cli_error(self):
        result = self.run_cli("ask", data=b"")
        self.assert_failure(result)

    @unittest.skipUnless(os.name == 'posix', 'POSIX shell/fd regression')
    def test_explicit_input_with_closed_stdin_and_regular_file_shell_loops(self):
        tasks = self.cwd / 'tasks'
        tasks.write_text('first\nsecond\n')
        diff = self.cwd / 'proposal.diff'
        diff.write_text('proposed diff')
        files = self.cwd / 'files'
        files.write_text(str(diff) + '\n')
        scripts = (
            ('"$1" "$2" ask q <&-', (), ['q']),
            ('while read q; do "$1" "$2" ask "$q" || exit; done < "$3"', (str(tasks),), ['first', 'second']),
            ('while read f; do "$1" "$2" review-diff --file "$f" || exit; done < "$3"', (str(files),), ['proposed diff']),
        )
        for script, extra, expected in scripts:
            self.server.requests = []
            result = subprocess.run(['/bin/sh', '-c', script, 'regression', sys.executable, str(ENTRYPOINT), *extra],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    cwd=self.cwd, env=self.env, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([r[2]['messages'][1]['content'] for r in self.server.requests], expected)

    @unittest.skipUnless(os.name == 'posix', 'POSIX open-pipe regression')
    def test_stdin_only_unconfigured_profile_fails_before_open_pipe_eof(self):
        reader, writer = os.pipe()
        try:
            env = dict(self.env)
            env.pop('LLMTOOL_BASE_URL')
            env.pop('LLMTOOL_MODEL')
            result = subprocess.run([sys.executable, str(ENTRYPOINT), 'ask'], stdin=reader,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    cwd=self.cwd, env=env, timeout=5)
            self.assert_failure(result)
            self.assertEqual(self.server.requests, [])
        finally:
            os.close(reader)
            os.close(writer)

    def test_broken_pipe_is_suppressed_at_cli_boundary(self):
        from llmtool.cli import main
        class Broken:
            def isatty(self):
                return False
            def write(self, value):
                raise BrokenPipeError()
        with patch("sys.stdout", Broken()):
            self.assertEqual(main(["skills"]), 0)

    def test_model_output_escaped_only_for_tty_human_ask(self):
        from llmtool.cli import main
        from llmtool.providers.base import Reply
        class TTY(io.StringIO):
            def isatty(self):
                return True
        reply = Reply("answer\x1b]52;c;secret\x07\rspoof\u202e", {})
        client = Mock()
        client.complete.return_value = reply
        for stream, expected in ((TTY(), "answer\\x1b]52;c;secret\\x07\\x0dspoof\\u202e\n"),
                                 (io.StringIO(), reply.content + "\n")):
            with self.subTest(tty=stream.isatty()), patch("llmtool.cli.resolve_config", return_value={}), \
                    patch("llmtool.cli.api_key", return_value=None), patch("llmtool.cli.create_client", return_value=client), \
                    patch("llmtool.cli.sys.stdout", stream):
                self.assertEqual(main(["ask", "prompt"]), 0)
                self.assertEqual(stream.getvalue(), expected)

    def test_binary_and_empty_input(self):
        for data in (b"", b" \n\t", b"text\x00binary", b"\xff\xfe", b"\x01"):
            with self.subTest(data=data):
                self.assert_failure(self.run_cli("ask", data=data))
        path = self.cwd / "binary"
        path.write_bytes(b"\x00data")
        self.assert_failure(self.run_cli("ask", "--file", str(path)))
        self.assertEqual(self.server.requests, [])

    def test_missing_file_directory_and_fifo(self):
        fifo = self.cwd / "fifo"
        os.mkfifo(fifo)
        for path in (self.cwd / "secret-filename", self.cwd, fifo):
            with self.subTest(path=path):
                result = self.run_cli("ask", "--file", str(path))
                self.assert_failure(result)
                self.assertNotIn(b"secret-filename", result.stderr)
        self.assertEqual(self.server.requests, [])

    def test_verbose_stdout_stderr_separation(self):
        result = self.run_cli("ask", "evidence", "--verbose")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"final answer\n")
        for text in (b"model=qwen3.6-35b-a3b", b"prompt_tokens=20", b"completion_tokens=5", b"elapsed_seconds="):
            self.assertIn(text, result.stderr)
            self.assertNotIn(text, result.stdout)
        self.assertNotIn(b"private-reasoning", result.stdout + result.stderr)

    def test_provider_failures_never_emit_partial_content(self):
        truncated = success("partial-private-content")
        truncated["choices"][0]["finish_reason"] = "length"
        for status, body in (
            (500, {"error": "private-input-and-key"}),
            (200, b"private-malformed-json"), (200, {}),
            (200, success("")), (200, truncated),
            (200, {"error": {"message": "private-input-and-key"}}),
            (204, b""),
        ):
            with self.subTest(status=status, body=body):
                self.server.reply = (status, body, 0, {})
                result = self.run_cli("ask", "private-input", "--verbose")
                self.assert_failure(result)
                self.assertNotIn(b"private", result.stderr)

    def test_socket_timeout(self):
        self.server.reply = (200, success(), 0.2, {})
        result = self.run_cli("ask", "test", "--timeout", "0.03")
        self.assert_failure(result)
        self.assertIn(b"timed out", result.stderr)

    def test_redirect_does_not_forward_prompt_or_key(self):
        self.server.reply = (307, b"private-redirect", 0, {"Location": self.base_url + "/other"})
        result = self.run_cli("ask", "test")
        self.assert_failure(result)
        self.assertIn(b"307", result.stderr)
        self.assertEqual(len(self.server.requests), 1)

    def test_no_execution_or_automatic_file_read(self):
        sentinel = self.cwd / "must-not-exist"
        output = f"touch {sentinel}; git reset --hard; cat ~/.ssh/id_rsa"
        self.server.reply = (200, success(output), 0, {})
        result = self.run_cli("review-diff", data=b"diff evidence only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.decode().strip(), output)
        self.assertFalse(sentinel.exists())
        self.assertEqual(self.server.requests[0][2]["messages"][1]["content"], "diff evidence only")

    def test_config_commands_use_temporary_home_and_no_network(self):
        user = self.home / ".config/llmtool/config.yaml"
        result = self.run_cli("config", "path")
        self.assertEqual(result.stdout.decode().strip(), str(user))
        self.assertEqual(result.returncode, 0)
        result = self.run_cli("config", "init")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(user.stat().st_mode & 0o777, 0o600)
        created = yaml.safe_load(user.read_text())
        self.assertIsNone(created["providers"]["default"]["base_url"])
        self.assertIsNone(created["providers"]["default"]["model"])
        self.assertNotIn("192.0.2.10", user.read_text())
        self.assertNotIn("gw", user.read_text())
        user.write_text("generation: {max_tokens: 42}")
        self.assert_failure(self.run_cli("config", "init"))
        self.assertIn("42", user.read_text())
        self.assertEqual(self.run_cli("config", "init", "--force").returncode, 0)
        shown = self.run_cli("config", "show", "--max-tokens", "17")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(yaml.safe_load(shown.stdout)["generation"]["max_tokens"], 17)
        self.assertEqual(self.server.requests, [])

    def test_config_show_redacts_key_and_auth_is_sent_only_when_configured(self):
        user = self.home / ".config/llmtool/config.yaml"
        user.parent.mkdir(parents=True)
        user.write_text("providers:\n  default:\n    api_key_env: TEST_KEY\n")
        self.env["TEST_KEY"] = "fake-private-key"
        result = self.run_cli("config", "show")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(b"fake-private-key", result.stdout + result.stderr)
        self.assertIn(b"REDACTED", result.stdout)
        result = self.run_cli("ask", "test")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.server.requests[0][1]["Authorization"], "Bearer fake-private-key")

    def test_cli_overrides_reach_wire(self):
        self.env["LLMTOOL_MODEL"] = "env-route"
        result = self.run_cli("ask", "test", "--model", "cli-route", "--max-tokens", "37", "--temperature", "0.2")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.server.requests[0][2]
        self.assertEqual(payload["model"], "cli-route")
        self.assertEqual(payload["max_tokens"], 37)
        self.assertEqual(payload["temperature"], 0.2)

    def test_invalid_arguments_and_yaml_do_not_echo_sensitive_input(self):
        result = self.run_cli("ask", "--unknown-secret-flag", "sensitive-input")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(b"sensitive", result.stderr)
        self.assertNotIn(b"secret", result.stderr)
        (self.cwd / ".llmtool.yaml").write_text("generation: [private-secret")
        result = self.run_cli("ask", "test")
        self.assert_failure(result)
        self.assertNotIn(b"private-secret", result.stderr)

    def test_input_limit(self):
        result = self.run_cli("context-pack", data=b"a" * (16 * 1024 * 1024 + 1))
        self.assert_failure(result)
        self.assertIn(b"16 MiB", result.stderr)
        self.assertEqual(self.server.requests, [])

    def test_ambient_proxy_is_ignored(self):
        self.env.update({"http_proxy": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1", "no_proxy": ""})
        result = self.run_cli("ask", "test")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_malicious_project_fails_before_any_http_or_secret_output(self):
        self.env.pop("LLMTOOL_BASE_URL")
        user = self.home / ".config/llmtool/config.yaml"
        user.parent.mkdir(parents=True)
        user.write_text(yaml.safe_dump({"providers": {"default": {
            "base_url": "http://127.0.0.1:1", "api_key": "FAKE_TEST_SECRET",
        }}}))
        self.env["AWS_SECRET_ACCESS_KEY"] = "FAKE_ENV_SECRET"
        project = self.cwd / ".llmtool.yaml"
        for fragment in (
            {"providers": {"default": {"base_url": self.base_url}}},
            {"providers": {"default": {"api_key": "FAKE_PROJECT_SECRET"}}},
            {"providers": {"default": {"api_key_env": "AWS_SECRET_ACCESS_KEY"}}},
            {"providers": {"default": {"type": "openai-compatible"}}},
            {"active_provider": "default"},
        ):
            with self.subTest(fragment=fragment):
                project.write_text(yaml.safe_dump(fragment))
                result = self.run_cli("ask", "private-prompt", "--verbose")
                self.assert_failure(result)
                self.assertIn(b"project", result.stderr)
                for secret in (b"FAKE_TEST_SECRET", b"FAKE_ENV_SECRET", b"FAKE_PROJECT_SECRET",
                               b"AWS_SECRET_ACCESS_KEY", b"private-prompt", self.base_url.encode()):
                    self.assertNotIn(secret, result.stdout + result.stderr)
                self.assertEqual(self.server.requests, [])

    def test_safe_project_overrides_still_reach_selected_endpoint(self):
        self.env.pop("LLMTOOL_MODEL")
        (self.cwd / ".llmtool.yaml").write_text("""providers:
  default:
    model: project-model
    timeout_seconds: 3
generation:
  max_tokens: 39
  temperature: 0.4
""")
        result = self.run_cli("ask", "test")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.server.requests[0][2]
        self.assertEqual(payload["model"], "project-model")
        self.assertEqual(payload["max_tokens"], 39)
        self.assertEqual(payload["temperature"], 0.4)

    def test_named_profile_cli_selection_and_optional_thinking_payload(self):
        self.env.pop("LLMTOOL_BASE_URL")
        self.env.pop("LLMTOOL_MODEL")
        user = self.home / ".config/llmtool/config.yaml"
        user.parent.mkdir(parents=True)
        user.write_text(yaml.safe_dump({"providers": {
            "default": {"base_url": "http://127.0.0.1:1", "api_key": "fake-other-key"},
            "local": {"base_url": self.base_url, "model": "local-model", "api_key_env": "LOCAL_TEST_KEY"},
        }}))
        self.env["LOCAL_TEST_KEY"] = "fake-selected-key"
        self.env["LLMTOOL_PROVIDER"] = "default"
        result = self.run_cli("ask", "test", "--provider", "local")
        self.assertEqual(result.returncode, 0, result.stderr)
        path, headers, payload = self.server.requests[0]
        self.assertEqual(headers["Authorization"], "Bearer fake-selected-key")
        self.assertEqual(payload["model"], "local-model")
        self.assertNotIn("chat_template_kwargs", payload)
        shown = self.run_cli("config", "show", "--provider", "local")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(yaml.safe_load(shown.stdout)["active_provider"], "local")
        self.assertNotIn(b"fake-other-key", shown.stdout)
        self.assertNotIn(b"fake-selected-key", shown.stdout)

    def test_no_user_config_ask_fails_and_show_remains_available(self):
        self.env.pop("LLMTOOL_BASE_URL")
        self.env.pop("LLMTOOL_MODEL")
        result = self.run_cli("ask", "test")
        self.assert_failure(result)
        self.assertRegex(result.stderr.decode(),
                         r"^llmtool: provider base_url is not configured\n"
                         r"llmtool: code=CONFIG_PROVIDER_NOT_READY run_id=[0-9a-f]{32} trace=not-saved\n$")
        shown = self.run_cli("config", "show")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        provider = yaml.safe_load(shown.stdout)["providers"]["default"]
        self.assertIsNone(provider["base_url"])
        self.assertIsNone(provider["model"])
        self.assertEqual(self.server.requests, [])

    def test_missing_selected_field_fails_without_leaking_key(self):
        self.env.pop("LLMTOOL_BASE_URL")
        self.env.pop("LLMTOOL_MODEL")
        user = self.home / ".config/llmtool/config.yaml"
        user.parent.mkdir(parents=True)
        for fields, missing in (({"model": "test"}, "base_url"),
                                ({"base_url": self.base_url}, "model")):
            with self.subTest(fields=fields):
                user.write_text(yaml.safe_dump({"providers": {"default": {
                    **fields, "api_key": "FAKE_PRIVATE_KEY",
                }}}))
                result = self.run_cli("ask", "private-input", "--verbose")
                self.assert_failure(result)
                lines = result.stderr.decode().splitlines()
                self.assertEqual(lines[0], "llmtool: provider " + missing + " is not configured")
                self.assertRegex(lines[1], r"^llmtool: code=CONFIG_PROVIDER_NOT_READY run_id=[0-9a-f]{32} trace=not-saved$")
                self.assertRegex(lines[2], r"^run_id=[0-9a-f]{32}$")
                self.assertEqual(lines[3:], ["diagnostics_persisted=false"])
                self.assertEqual(self.server.requests, [])

    def test_explicit_user_config_thinking_false_still_sent(self):
        self.env.pop("LLMTOOL_BASE_URL")
        self.env.pop("LLMTOOL_MODEL")
        user = self.home / ".config/llmtool/config.yaml"
        user.parent.mkdir(parents=True)
        user.write_text(yaml.safe_dump({"providers": {"default": {
            "base_url": self.base_url, "model": "explicit-model",
            "provider_options": {"enable_thinking": False},
        }}}))
        result = self.run_cli("ask", "test")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.server.requests[0][2]
        self.assertEqual(payload["model"], "explicit-model")
        self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": False})


if __name__ == "__main__":
    unittest.main()
