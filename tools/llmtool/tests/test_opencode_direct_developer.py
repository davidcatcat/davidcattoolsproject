import json
from datetime import datetime
import os
import signal
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool.opencode_direct_developer import (
    AGENT, MODEL, DirectOpenCodeError, configuration, parse_json_events, run,
)
from llmtool.providers.cli_lifecycle import CLIHostTermination
from llmtool import worker_dispatch


class DirectOpenCodeDeveloperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='llmtool-opencode-direct-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'source.py').write_text('VALUE = 1\n')

    def run_with_valid_binding(self, **kwargs):
        store = kwargs.pop('dispatch_store', object())
        with patch('llmtool.opencode_direct_developer.worker_dispatch.authorize_dispatch'):
            return run(**kwargs, dispatch_store=store, dispatch_task_id='a' * 32,
                       prelaunch_validate=lambda: True)

    def test_direct_adapter_cannot_run_without_dispatch_context(self):
        marker = self.root / 'run-invoked'
        cli = self.fake_cli(self.event('step_start') + '\n',
                            run_invocation_marker=marker)
        with self.assertRaises(DirectOpenCodeError) as caught:
            run(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                prompt='bounded task prompt', prelaunch_validate=lambda: True,
                timeout_seconds=5, executable=cli)
        self.assertEqual(caught.exception.direct_opencode_code,
                         'WORKER_DISPATCH_CONTEXT_UNAVAILABLE')
        self.assertFalse(marker.exists())

    def test_paused_policy_blocks_direct_adapter_before_provider_run(self):
        class Store:
            def __init__(self, task_id, allowed_policy_path):
                from llmtool.action_contract import TaskBinding

                binding = {
                    'task_id': task_id, 'run_id': 'b' * 32,
                    'contract_version': 1, 'contract_digest': 'c' * 64,
                }
                developer = {
                    'assignment_id': 'd' * 32, 'state': 'DEVELOPER_DISPATCHING',
                    'provider_profile': 'opencode', 'provider_type': 'opencode-cli',
                    'model': 'opencode/deepseek-v4.1-flash-free',
                    'source_sha': 'e' * 40,
                }
                with patch.object(worker_dispatch, '_POLICY_PATH', allowed_policy_path):
                    transition = worker_dispatch.audit_entry(
                        worker_dispatch.evaluate('opencode-developer', task_id=task_id),
                        'DISPATCH', binding={
                            'assignment_id': developer['assignment_id'],
                            'source_sha': developer['source_sha'],
                            'contract_digest': binding['contract_digest'],
                        })
                self.record = {
                    'schema_version': 2, 'task_id': task_id,
                    'task_binding': TaskBinding(**binding).to_dict(),
                    'developer': developer, 'dispatch_audit': [transition],
                }

            def get(self, task_id):
                if task_id != self.record['task_id']:
                    raise worker_dispatch.WorkerDispatchDenied('TEST_TASK_MISMATCH')
                return json.loads(json.dumps(self.record))

            def append_dispatch_audit(self, task_id, entry, *, expected=None):
                if (task_id != self.record['task_id'] or type(expected) is not dict or
                        any(self.record.get(key) != value for key, value in expected.items())):
                    raise worker_dispatch.WorkerDispatchDenied('TEST_STORE_CONFLICT')
                self.record['dispatch_audit'].append(json.loads(json.dumps(entry)))
                return json.loads(json.dumps(self.record))

        policy = json.loads(Path(worker_dispatch._POLICY_PATH).read_text(encoding='utf-8'))
        policy['workers']['opencode-developer']['dispatch'] = 'ALLOWED'
        policy['workers']['opencode-developer']['reason'] = 'prior assignment transition'
        allowed_policy_path = self.root / 'worker-dispatch-allowed.json'
        allowed_policy_path.write_text(json.dumps(policy), encoding='utf-8')
        policy['workers']['opencode-developer']['dispatch'] = 'PAUSED'
        policy['workers']['opencode-developer']['reason'] = 'controlled paused test'
        paused_policy_path = self.root / 'worker-dispatch-paused.json'
        paused_policy_path.write_text(json.dumps(policy), encoding='utf-8')
        store = Store('a' * 32, allowed_policy_path)
        run_marker = self.root / 'provider-run-invoked'
        cli = self.fake_cli(self.event('step_start') + '\n',
                            run_invocation_marker=run_marker)
        with patch.object(worker_dispatch, '_POLICY_PATH', paused_policy_path):
            with self.assertRaises(DirectOpenCodeError) as caught:
                run(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                    prompt='bounded task prompt', prelaunch_validate=lambda: True,
                    dispatch_store=store, dispatch_task_id='a' * 32,
                    timeout_seconds=5, executable=cli)
        self.assertEqual(caught.exception.direct_opencode_code, 'OWNER_PAUSED')
        self.assertFalse(run_marker.exists())
        refusal = store.record['dispatch_audit'][-1]
        self.assertEqual(refusal['decision'], 'BLOCKED')
        self.assertEqual(refusal['worker_key'], 'opencode-developer')
        self.assertEqual(refusal['reason_code'], 'OWNER_PAUSED')
        self.assertEqual(refusal['assignment_id'], 'd' * 32)
        self.assertEqual(refusal['source_sha'], 'e' * 40)
        self.assertEqual(refusal['contract_digest'], 'c' * 64)

    @staticmethod
    def event(kind, **fields):
        return json.dumps({'type': kind, 'sessionID': 'ses_Abc123', **fields})

    def test_config_denies_tools_except_exact_read_and_edit_scope(self):
        value = configuration(read_paths=['source.py'], write_paths=['source.py'])
        self.assertEqual(value['model'], MODEL)
        self.assertEqual(value['default_agent'], AGENT)
        self.assertEqual(value['permissions'][0], {
            'action': '*', 'resource': '*', 'effect': 'deny'})
        self.assertEqual(value['permissions'][1:], [
            {'action': 'read', 'resource': 'source.py', 'effect': 'allow'},
            {'action': 'edit', 'resource': 'source.py', 'effect': 'allow'},
        ])
        self.assertEqual(value['mcp'], {'servers': {}})
        self.assertEqual(value['agents'][AGENT]['steps'], 12)
        self.assertEqual(value['agents'][AGENT]['permissions'], value['permissions'])
        self.assertEqual(value['policies'][-1], {
            'action': 'integration.use', 'resource': '*', 'effect': 'deny'})

    def test_configured_agent_steps_are_bounded(self):
        value = configuration(read_paths=['source.py'], write_paths=['source.py'], steps=23)
        self.assertEqual(value['agents'][AGENT]['steps'], 23)
        for steps in (2, 51, True, 12.0, '12'):
            with self.subTest(steps=steps), self.assertRaises(DirectOpenCodeError) as caught:
                configuration(read_paths=['source.py'], write_paths=['source.py'],
                              steps=steps)
            self.assertEqual(caught.exception.direct_opencode_code,
                             'OPENCODE_STEPS_INVALID')

    def test_no_final_text_after_step_limit_has_distinct_error(self):
        raw = '\n'.join([self.event('step_finish', part={}) for _ in range(12)]).encode()
        with self.assertRaises(DirectOpenCodeError) as caught:
            parse_json_events(raw)
        self.assertEqual(caught.exception.direct_opencode_code,
                         'OPENCODE_STEPS_EXHAUSTED')

    def test_json_event_parser_binds_one_session_and_reports_usage(self):
        raw = ('\n'.join((
            self.event('step_start'),
            self.event('text', part={'text': 'change ready'}),
            self.event('step_finish', part={'tokens': {'input': 11, 'output': 7}}),
        )) + '\n').encode()
        sid, text, usage = parse_json_events(raw)
        self.assertEqual(sid, 'ses_Abc123')
        self.assertEqual(text, 'change ready')
        self.assertEqual(usage, {'prompt_tokens': 11, 'completion_tokens': 7,
                                 'total_tokens': 18})

    def test_usage_missing_is_unknown_not_zero(self):
        raw = (self.event('step_start') + '\n' +
               self.event('text', text='done') + '\n').encode()
        self.assertIsNone(parse_json_events(raw)[2])

    def test_one_missing_step_usage_keeps_total_usage_unknown(self):
        raw = ('\n'.join((
            self.event('step_finish', part={'tokens': {'input': 11, 'output': 7}}),
            self.event('step_finish', part={}),
        )) + '\n').encode()
        self.assertIsNone(parse_json_events(raw)[2])

    def test_events_with_mixed_sessions_or_invalid_json_fail_closed(self):
        wrong = json.dumps({'type': 'text', 'sessionID': 'ses_Other'})
        for raw in ((self.event('step_start') + '\n' + wrong + '\n').encode(),
                    b'{not json}\n', b'{}\n'):
            with self.subTest(raw=raw), self.assertRaises(DirectOpenCodeError):
                parse_json_events(raw)

    def test_structured_tool_refusal_is_typed_without_exposing_output(self):
        denied = self.event('tool_use', part={
            'state': {'status': 'error', 'error': {'code': 'PERMISSION_DENIED',
                                                'message': 'FAKE_SECRET_12345678'}}})
        with self.assertRaises(DirectOpenCodeError) as caught:
            parse_json_events((denied + '\n').encode())
        self.assertEqual(caught.exception.direct_opencode_code, 'TOOL_DENIED')
        self.assertNotIn('FAKE_SECRET_12345678', str(caught.exception))
        # Free-form model text cannot manufacture a permission verdict.
        sid, text, _ = parse_json_events((self.event(
            'text', text='PERMISSION_DENIED FAKE_SECRET_12345678') + '\n').encode())
        self.assertEqual(sid, 'ses_Abc123')
        self.assertIn('PERMISSION_DENIED', text)
        # Malformed structured values are untrusted data, never an exception leak.
        with self.assertRaises(DirectOpenCodeError) as malformed:
            parse_json_events((self.event('error', error={'code': ['PERMISSION_DENIED']})
                               + '\n').encode())
        self.assertEqual(malformed.exception.direct_opencode_code,
                         'OPENCODE_PROVIDER_FAILED')

    def test_provider_auth_error_in_stream_is_typed_without_exposing_message(self):
        marker = 'PROVIDER_AUTH_MESSAGE_SECRET_123'
        event = self.event('error', error={
            'type': 'provider.auth', 'message': marker,
        })
        with self.assertRaises(DirectOpenCodeError) as caught:
            parse_json_events((event + '\n').encode())
        error = caught.exception
        self.assertEqual(error.direct_opencode_code, 'OPENCODE_AUTH_REQUIRED')
        self.assertNotIn(marker, str(error))
        self.assertNotIn(marker, repr(error.args))
        self.assertNotIn(marker, repr(vars(error)))

    def test_nonzero_provider_auth_error_is_typed_without_exposing_message(self):
        task_id, run_id = 'a' * 32, 'b' * 32
        marker = 'PROVIDER_AUTH_MESSAGE_SECRET_456'

        class Store:
            def get(self, selected):
                return {'task_id': selected, 'run_id': run_id}

        body = self.event('error', error={
            'type': 'provider.auth', 'message': marker,
        }) + '\n'
        cli = self.fake_cli(body, exit_code=7)
        with patch('llmtool.opencode_direct_developer.worker_dispatch.authorize_dispatch'):
            with self.assertRaises(DirectOpenCodeError) as caught:
                run(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                    prompt='bounded task prompt', prelaunch_validate=lambda: True,
                    dispatch_store=Store(), dispatch_task_id=task_id,
                    timeout_seconds=5, executable=cli)
        error = caught.exception
        self.assertEqual(error.direct_opencode_code, 'OPENCODE_AUTH_REQUIRED')
        self.assertNotIn(marker, str(error))
        self.assertNotIn(marker, repr(error.args))
        self.assertNotIn(marker, repr(vars(error)))

    def test_non_auth_provider_error_stays_provider_failed(self):
        marker = 'PROVIDER_FAILURE_MESSAGE_SECRET_789'
        event = self.event('error', error={
            'type': 'provider.unavailable', 'message': marker,
        })
        with self.assertRaises(DirectOpenCodeError) as caught:
            parse_json_events((event + '\n').encode())
        error = caught.exception
        self.assertEqual(error.direct_opencode_code, 'OPENCODE_PROVIDER_FAILED')
        self.assertNotIn(marker, str(error))
        self.assertNotIn(marker, repr(error.args))
        self.assertNotIn(marker, repr(vars(error)))

    def test_nonzero_structured_denial_has_bound_timing_and_no_secret_leak(self):
        task_id, run_id = 'a' * 32, 'b' * 32

        class Store:
            def get(self, selected):
                return {'task_id': selected, 'run_id': run_id}

        body = '\n'.join((
            self.event('step_start'),
            self.event('error', error={'code': 'PERMISSION_DENIED',
                                       'message': 'FAKE_SECRET_12345678'}),
        )) + '\n'
        cli = self.fake_cli(body, exit_code=7)
        with patch('llmtool.opencode_direct_developer.worker_dispatch.authorize_dispatch'):
            with self.assertRaises(DirectOpenCodeError) as caught:
                run(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                    prompt='bounded task prompt', prelaunch_validate=lambda: True,
                    dispatch_store=Store(), dispatch_task_id=task_id,
                    timeout_seconds=5, executable=cli)
        error = caught.exception
        self.assertEqual(error.direct_opencode_code, 'TOOL_DENIED')
        self.assertEqual((error.task_id, error.run_id, error.native_session_id),
                         (task_id, run_id, 'ses_Abc123'))
        self.assertLessEqual(datetime.fromisoformat(error.started_at.replace('Z', '+00:00')),
                             datetime.fromisoformat(error.ended_at.replace('Z', '+00:00')))
        self.assertNotIn('FAKE_SECRET_12345678', str(error))
        self.assertNotIn('FAKE_SECRET_12345678', repr(error.__dict__))

    def test_permission_request_stops_live_noninteractive_child_promptly(self):
        body = self.event('permission.asked') + '\n'
        cli = self.fake_cli(body, post_output_delay=10)
        began = time.monotonic()
        with self.assertRaises(DirectOpenCodeError) as caught:
            self.run_with_valid_binding(root=self.root, read_paths=['source.py'],
                write_paths=['source.py'], prompt='bounded task prompt',
                timeout_seconds=5, executable=cli)
        self.assertEqual(caught.exception.direct_opencode_code, 'TOOL_DENIED')
        self.assertEqual(caught.exception.cleanup_status, 'CONFIRMED')
        self.assertLess(time.monotonic() - began, 3)

    def fake_cli(self, body, *, exit_code=0, delay=0, invocation_marker=None,
                 version_output='opencode v2.0.32\n',
                 version_descendant_marker=None, run_descendant_marker=None,
                 version_mutate_path=None, run_invocation_marker=None,
                 post_output_delay=0, model=MODEL):
        marker_code = (f"open({str(invocation_marker)!r}, 'a').write('called\\n')\n"
                       if invocation_marker else '')
        mutation_code = (f"    open({str(version_mutate_path)!r}, 'w').write('CHANGED\\n')\n"
                         if version_mutate_path else '')
        run_marker_code = (
            f"open({str(run_invocation_marker)!r}, 'a').write(str(os.getpid()) + '\\n')\n"
            if run_invocation_marker else '')
        version_child_code = ''
        if version_descendant_marker:
            version_child_code = (
                'pid = os.fork()\n'
                'if pid == 0:\n'
                '    for fd in (0, 1, 2):\n'
                '        try: os.close(fd)\n'
                '        except OSError: pass\n'
                '    time.sleep(1)\n'
                f"    open({str(version_descendant_marker)!r}, 'w').write('leaked')\n"
                '    os._exit(0)\n'
            )
        run_child_code = ''
        if run_descendant_marker:
            run_child_code = (
                'pid = os.fork()\n'
                'if pid == 0:\n'
                '    for fd in (0, 1, 2):\n'
                '        try: os.close(fd)\n'
                '        except OSError: pass\n'
                '    time.sleep(1)\n'
                f"    open({str(run_descendant_marker)!r}, 'w').write('leaked')\n"
                '    os._exit(0)\n'
            )
        cli = self.root / 'opencode-fixture'
        script = (
            '#!/usr/bin/env python3\n'
            'import json, os, sys, time\n'
            + marker_code +
            "if sys.argv[1:] == ['--version']:\n"
            + ''.join('    ' + line + '\n' for line in version_child_code.splitlines())
            + mutation_code
            + f'    sys.stdout.write({version_output!r})\n'
            '    raise SystemExit(0)\n'
            + run_marker_code +
            "assert sys.argv[1:4] == ['run', '--standalone', '--model']\n"
            f"assert sys.argv[4] == {model!r}\n"
            "assert sys.argv[sys.argv.index('--format') + 1] == 'json'\n"
            "assert sys.argv[sys.argv.index('--agent') + 1] == 'llmtool-direct-developer'\n"
            'config_path = os.environ["OPENCODE_CONFIG"]\n'
            'assert os.path.dirname(config_path) == os.environ["OPENCODE_CONFIG_DIR"]\n'
            'assert os.stat(config_path).st_mode & 0o777 == 0o600\n'
            'assert os.environ["OPENCODE_CONFIG_PROJECT_DISABLE"] == "1"\n'
            'assert "OPENCODE_CONFIG_CONTENT" not in os.environ\n'
            'cfg = json.load(open(config_path, encoding="utf-8"))\n'
            f'assert cfg["model"] == {model!r}\n'
            'assert cfg["permissions"][0]["effect"] == "deny"\n'
            'assert cfg["agents"]["llmtool-direct-developer"]["mode"] == "primary"\n'
            'assert cfg["agents"]["llmtool-direct-developer"]["permissions"] == cfg["permissions"]\n'
            'assert not os.environ.get("OPENAI_API_KEY")\n'
            'sys.stdin.buffer.read()\n'
            + run_child_code +
            f'time.sleep({delay!r})\n'
            f'sys.stdout.write({body!r})\n'
            'sys.stdout.flush()\n'
            f'time.sleep({post_output_delay!r})\n'
            f'raise SystemExit({exit_code})\n'
        )
        cli.write_text(script)
        cli.chmod(0o700)
        return str(cli)

    def test_run_uses_noninteractive_json_cli_and_returns_real_exit_metadata(self):
        class Store:
            def get(self, selected):
                return {'task_id': selected, 'run_id': 'b' * 32}

        body = '\n'.join((
            self.event('step_start'),
            self.event('text', part={'text': 'fixture result'}),
            self.event('step_finish', part={'tokens': {'input': 5, 'output': 8}}),
        )) + '\n'
        result = self.run_with_valid_binding(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                     prompt='bounded task prompt', timeout_seconds=5,
                     executable=self.fake_cli(body), dispatch_store=Store())
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.session_id, 'ses_Abc123')
        self.assertEqual(result.text, 'fixture result')
        self.assertEqual(result.usage['total_tokens'], 13)
        self.assertEqual(result.usage_source, 'CLI_REPORTED')
        self.assertEqual(result.cleanup_status, 'CONFIRMED')
        self.assertEqual(result.cli_version, 'opencode v2.0.32')
        self.assertEqual(result.source_bytes, len(b'VALUE = 1\n'))
        self.assertEqual(result.source_file_count, 1)
        self.assertEqual(len(result.source_manifest_sha256), 64)
        self.assertEqual(len(result.stdout_sha256), 64)
        self.assertEqual((result.task_id, result.run_id), ('a' * 32, 'b' * 32))
        self.assertLessEqual(datetime.fromisoformat(result.started_at.replace('Z', '+00:00')),
                             datetime.fromisoformat(result.ended_at.replace('Z', '+00:00')))

    def test_private_key_source_is_rejected_before_any_cli_launch(self):
        private_key_header = '-----BEGIN ' + 'PRIVATE' + ' KEY-----'
        private_key_footer = '-----END ' + 'PRIVATE' + ' KEY-----'
        (self.root / 'source.py').write_text(
            private_key_header + '\nfixture-only\n' + private_key_footer + '\n')
        marker = self.root / 'invoked'
        cli = self.fake_cli(self.event('step_start') + '\n', invocation_marker=marker)
        with self.assertRaises(DirectOpenCodeError) as caught:
            self.run_with_valid_binding(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                prompt='bounded task prompt', timeout_seconds=5, executable=cli)
        self.assertEqual(caught.exception.direct_opencode_code, 'OPENCODE_SOURCE_SENSITIVE')
        self.assertFalse(marker.exists())

    def test_source_identity_drift_after_version_probe_blocks_provider_run(self):
        run_marker = self.root / 'run-invoked'
        cli = self.fake_cli(self.event('step_start') + '\n',
                            version_mutate_path=self.root / 'source.py',
                            run_invocation_marker=run_marker)
        with self.assertRaises(DirectOpenCodeError) as caught:
            self.run_with_valid_binding(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                prompt='bounded task prompt', timeout_seconds=5, executable=cli)
        self.assertEqual(caught.exception.direct_opencode_code, 'OPENCODE_SOURCE_CHANGED')
        self.assertFalse(run_marker.exists())

    def test_source_byte_limit_blocks_cli_launch(self):
        (self.root / 'source.py').write_text('x' * (256 * 1024 + 1))
        marker = self.root / 'invoked'
        cli = self.fake_cli(self.event('step_start') + '\n', invocation_marker=marker)
        with self.assertRaises(DirectOpenCodeError) as caught:
            self.run_with_valid_binding(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                prompt='bounded task prompt', timeout_seconds=5, executable=cli)
        self.assertEqual(caught.exception.direct_opencode_code, 'OPENCODE_SOURCE_TOO_LARGE')
        self.assertFalse(marker.exists())

    def test_any_well_formed_version_runs_and_is_recorded(self):
        body = self.event('step_start') + '\n'
        for version in ('opencode v2.1.4', 'opencode v3.0.0-beta.1+build.7'):
            with self.subTest(version=version):
                cli = self.fake_cli(body, version_output=version + '\n')
                result = self.run_with_valid_binding(root=self.root, read_paths=['source.py'],
                    write_paths=['source.py'], prompt='bounded task prompt',
                    timeout_seconds=5, executable=cli)
                self.assertEqual(result.cli_version, version)

    def test_malformed_version_is_rejected_before_provider_run(self):
        marker = self.root / 'run-invoked'
        for version in ('opencode v2.1', 'opencode v2.1.4 (beta)', 'opencode v2.1.4\nextra',
                        'OpenCode 2.1.4', 'opencode v2.1.4-', 'opencode v\u0662.1.4'):
            with self.subTest(version=version):
                cli = self.fake_cli(self.event('step_start') + '\n',
                                    version_output=version + '\n',
                                    run_invocation_marker=marker)
                with self.assertRaises(DirectOpenCodeError) as caught:
                    self.run_with_valid_binding(root=self.root, read_paths=['source.py'],
                        write_paths=['source.py'], prompt='bounded task prompt',
                        timeout_seconds=5, executable=cli)
                self.assertEqual(caught.exception.direct_opencode_code,
                                 'OPENCODE_VERSION_UNSUPPORTED')
                self.assertFalse(marker.exists())

    def test_configured_free_model_is_passed_to_cli_and_config(self):
        cli = self.fake_cli(self.event('step_start') + '\n', model='opencode/other-model-free')
        result = self.run_with_valid_binding(root=self.root, read_paths=['source.py'],
            write_paths=['source.py'], prompt='bounded task prompt',
            timeout_seconds=5, executable=cli, model='opencode/other-model-free')
        self.assertEqual(result.exit_code, 0)

    def test_non_free_or_malformed_model_is_rejected_before_any_cli_launch(self):
        marker = self.root / 'invoked'
        cli = self.fake_cli(self.event('step_start') + '\n', invocation_marker=marker)
        for model in ('opencode/paid-model', 'other/name-free', 'opencode/a/b-free',
                      'opencode/-free', 'opencode/x-free\n', None):
            with self.subTest(model=model):
                with self.assertRaises(DirectOpenCodeError) as caught:
                    self.run_with_valid_binding(root=self.root, read_paths=['source.py'],
                        write_paths=['source.py'], prompt='bounded task prompt',
                        timeout_seconds=5, executable=cli, model=model)
                self.assertEqual(caught.exception.direct_opencode_code,
                                 'OPENCODE_MODEL_INVALID')
        self.assertFalse(marker.exists())

    def test_version_probe_output_is_bounded(self):
        cli = self.fake_cli(self.event('step_start') + '\n',
                            version_output='x' * 5000)
        with self.assertRaises(DirectOpenCodeError) as caught:
            self.run_with_valid_binding(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                prompt='bounded task prompt', timeout_seconds=5, executable=cli)
        self.assertEqual(caught.exception.direct_opencode_code,
                         'OPENCODE_VERSION_OUTPUT_TOO_LARGE')

    def test_prelaunch_d6_drift_rejects_before_developer_process(self):
        run_marker = self.root / 'run-invoked'
        cli = self.fake_cli(self.event('step_start') + '\n',
                            run_invocation_marker=run_marker)
        with self.assertRaises(DirectOpenCodeError) as caught:
            run(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                prompt='bounded task prompt', prelaunch_validate=lambda: False,
                dispatch_store=object(), dispatch_task_id='a' * 32,
                timeout_seconds=5, executable=cli)
        self.assertEqual(caught.exception.direct_opencode_code,
                         'OPENCODE_D6_BINDING_CHANGED')
        self.assertFalse(caught.exception.attempted)
        self.assertFalse(run_marker.exists())

    @unittest.skipUnless(hasattr(os, 'fork'), 'requires POSIX process groups')
    def test_silent_descendant_is_killed_before_reaping_successful_leader(self):
        marker = self.root / 'run-descendant-finished'
        cli = self.fake_cli(self.event('step_start') + '\n',
                            run_descendant_marker=marker)
        result = self.run_with_valid_binding(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                     prompt='bounded task prompt', timeout_seconds=5, executable=cli)
        self.assertEqual(result.cleanup_status, 'CONFIRMED')
        time.sleep(1.1)
        self.assertFalse(marker.exists())

    @unittest.skipUnless(hasattr(os, 'fork'), 'requires POSIX process groups')
    def test_version_probe_kills_silent_descendant_with_owned_lifecycle(self):
        marker = self.root / 'version-descendant-finished'
        cli = self.fake_cli(self.event('step_start') + '\n',
                            version_descendant_marker=marker)
        result = self.run_with_valid_binding(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                     prompt='bounded task prompt', timeout_seconds=5, executable=cli)
        self.assertEqual(result.cleanup_status, 'CONFIRMED')
        time.sleep(1.1)
        self.assertFalse(marker.exists())

    def test_run_times_out_owned_process(self):
        cli = self.fake_cli(self.event('step_start') + '\n', delay=2)
        with self.assertRaises(DirectOpenCodeError) as caught:
            self.run_with_valid_binding(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                prompt='bounded task prompt', timeout_seconds=1, executable=cli)
        self.assertEqual(caught.exception.direct_opencode_code, 'OPENCODE_TIMEOUT')

    def test_keyboard_interrupt_reaps_owned_process_and_attaches_observed_facts(self):
        cli = self.fake_cli(self.event('step_start') + '\n', delay=5)
        from llmtool.providers.cli_lifecycle import CLIProcessLifecycle
        original_check = CLIProcessLifecycle.check
        run_checks = []

        def interrupt_after_launch(lifecycle):
            process_args = getattr(lifecycle.process, 'args', ())
            if isinstance(process_args, (list, tuple)) and 'run' in process_args:
                run_checks.append(1)
                raise KeyboardInterrupt()
            return original_check(lifecycle)

        with patch('llmtool.opencode_direct_developer.CLIProcessLifecycle.check',
                   autospec=True, side_effect=interrupt_after_launch), \
                self.assertRaises(KeyboardInterrupt) as caught:
            self.run_with_valid_binding(root=self.root, read_paths=['source.py'],
                write_paths=['source.py'], prompt='bounded task prompt',
                timeout_seconds=5, executable=cli)
        self.assertEqual(run_checks, [1])
        observed = caught.exception._llmtool_direct_opencode_execution
        self.assertEqual(observed['cleanup_status'], 'CONFIRMED')
        self.assertIsInstance(observed['process_exit_code'], int)
        self.assertEqual(observed['source_bytes'], len(b'VALUE = 1\n'))
        self.assertEqual(observed['source_file_count'], 1)
        self.assertEqual(len(observed['source_manifest_sha256']), 64)

    @unittest.skipUnless(os.name == 'posix', 'requires POSIX process signals')
    def test_sigint_and_sigterm_reap_owned_process_and_preserve_signal_semantics(self):
        for sig, error_type, expected_code in (
                (signal.SIGINT, KeyboardInterrupt, None),
                (signal.SIGTERM, CLIHostTermination, 128 + signal.SIGTERM)):
            with self.subTest(signal=sig):
                marker = self.root / ('signal-' + str(sig.value))
                cli = self.fake_cli(self.event('step_start') + '\n', delay=10,
                                    run_invocation_marker=marker)
                def signal_when_child_starts():
                    deadline = time.monotonic() + 5
                    while not marker.exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    if marker.exists():
                        os.kill(os.getpid(), sig)

                sender = threading.Thread(target=signal_when_child_starts, daemon=True)
                sender.start()
                with self.assertRaises(error_type) as caught:
                    self.run_with_valid_binding(root=self.root,
                        read_paths=['source.py'], write_paths=['source.py'],
                        prompt='bounded task prompt', timeout_seconds=15,
                        executable=cli)
                sender.join(timeout=2)
                self.assertFalse(sender.is_alive())
                observed = caught.exception
                if expected_code is not None:
                    self.assertEqual(observed.code, expected_code)
                facts = observed._llmtool_direct_opencode_execution
                self.assertEqual(facts['cleanup_status'], 'CONFIRMED')
                self.assertLess(facts['process_exit_code'], 0)
                child_pid = int(marker.read_text(encoding='ascii').splitlines()[0])
                with self.assertRaises(ProcessLookupError):
                    os.kill(child_pid, 0)

    def test_nonzero_exit_preserves_only_observed_bounded_execution_metadata(self):
        cli = self.fake_cli(self.event('step_start') + '\n', exit_code=7)
        with self.assertRaises(DirectOpenCodeError) as caught:
            self.run_with_valid_binding(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                prompt='bounded task prompt', timeout_seconds=5, executable=cli)
        error = caught.exception
        self.assertEqual(error.direct_opencode_code, 'OPENCODE_PROVIDER_FAILED')
        self.assertTrue(error.attempted)
        self.assertEqual(error.process_exit_code, 7)
        self.assertEqual(error.cleanup_status, 'CONFIRMED')
        self.assertEqual(len(error.stdout_sha256), 64)
        self.assertGreaterEqual(error.elapsed_ms, 0)
        self.assertEqual(error.task_id, 'a' * 32)
        self.assertIsNone(error.run_id)
        self.assertEqual(error.native_session_id, 'ses_Abc123')
        self.assertIsNotNone(error.started_at)
        self.assertIsNotNone(error.ended_at)

    def test_deeply_nested_json_line_keeps_typed_outcome_and_facts(self):
        # Regression: a nesting depth that makes json.loads raise RecursionError
        # must not turn a typed outcome into an untyped adapter failure.
        body = '[' * 200000 + '\n'
        for exit_code, expected in ((0, 'OPENCODE_OUTPUT_INVALID'),
                                    (7, 'OPENCODE_PROVIDER_FAILED')):
            with self.subTest(exit_code=exit_code):
                cli = self.fake_cli(body, exit_code=exit_code)
                with self.assertRaises(DirectOpenCodeError) as caught:
                    self.run_with_valid_binding(root=self.root, read_paths=['source.py'],
                        write_paths=['source.py'], prompt='bounded task prompt',
                        timeout_seconds=5, executable=cli)
                error = caught.exception
                self.assertEqual(error.direct_opencode_code, expected)
                self.assertTrue(error.attempted)
                self.assertEqual(error.process_exit_code, exit_code)
                self.assertEqual(error.cleanup_status, 'CONFIRMED')
                self.assertEqual(len(error.stdout_sha256), 64)
                self.assertEqual(error.task_id, 'a' * 32)
                self.assertIsNone(error.native_session_id)
                self.assertIsNotNone(error.ended_at)

    def test_deeply_nested_json_line_does_not_replace_timeout_code(self):
        cli = self.fake_cli('[' * 200000 + '\n', post_output_delay=5)
        with self.assertRaises(DirectOpenCodeError) as caught:
            self.run_with_valid_binding(root=self.root, read_paths=['source.py'],
                write_paths=['source.py'], prompt='bounded task prompt',
                timeout_seconds=1, executable=cli)
        error = caught.exception
        self.assertEqual(error.direct_opencode_code, 'OPENCODE_TIMEOUT')
        self.assertEqual(error.cleanup_status, 'CONFIRMED')
        self.assertEqual(len(error.stdout_sha256), 64)
        self.assertIsNone(error.native_session_id)

    def test_run_rejects_out_of_root_and_symlinked_paths(self):
        outside = self.root.parent / 'outside-source.py'
        outside.write_text('VALUE = 0\n')
        self.addCleanup(outside.unlink)
        (self.root / 'linked.py').symlink_to(outside)
        cli = self.fake_cli(self.event('step_start') + '\n')
        for path in ('../outside-source.py', 'linked.py'):
            with self.subTest(path=path), self.assertRaises(DirectOpenCodeError):
                self.run_with_valid_binding(root=self.root, read_paths=[path], write_paths=[path],
                    prompt='bounded task prompt', timeout_seconds=5, executable=cli)


if __name__ == '__main__':
    unittest.main()
