import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from llmtool.opencode_direct_developer import (
    AGENT, MODEL, DirectOpenCodeError, configuration, parse_json_events, run,
)


class DirectOpenCodeDeveloperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='llmtool-opencode-direct-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'source.py').write_text('VALUE = 1\n')

    def run_with_valid_binding(self, **kwargs):
        return run(**kwargs, prelaunch_validate=lambda: True)

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
        self.assertEqual(value['agents'][AGENT]['steps'], 1)
        self.assertEqual(value['policies'][-1], {
            'action': 'integration.use', 'resource': '*', 'effect': 'deny'})

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

    def fake_cli(self, body, *, exit_code=0, delay=0, invocation_marker=None,
                 version_output='opencode v2.0.32\n',
                 version_descendant_marker=None, run_descendant_marker=None,
                 version_mutate_path=None, run_invocation_marker=None):
        marker_code = (f"open({str(invocation_marker)!r}, 'a').write('called\\n')\n"
                       if invocation_marker else '')
        mutation_code = (f"    open({str(version_mutate_path)!r}, 'w').write('CHANGED\\n')\n"
                         if version_mutate_path else '')
        run_marker_code = (f"open({str(run_invocation_marker)!r}, 'a').write('run\\n')\n"
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
            "assert sys.argv[4] == 'opencode/deepseek-v4.1-flash-free'\n"
            "assert sys.argv[sys.argv.index('--format') + 1] == 'json'\n"
            "assert sys.argv[sys.argv.index('--agent') + 1] == 'llmtool-direct-developer'\n"
            'cfg = json.loads(os.environ["OPENCODE_CONFIG_CONTENT"])\n'
            'assert cfg["model"] == "opencode/deepseek-v4.1-flash-free"\n'
            'assert cfg["permissions"][0]["effect"] == "deny"\n'
            'assert not os.environ.get("OPENAI_API_KEY")\n'
            'sys.stdin.buffer.read()\n'
            + run_child_code +
            f'time.sleep({delay!r})\n'
            f'sys.stdout.write({body!r})\n'
            'sys.stdout.flush()\n'
            f'raise SystemExit({exit_code})\n'
        )
        cli.write_text(script)
        cli.chmod(0o700)
        return str(cli)

    def test_run_uses_noninteractive_json_cli_and_returns_real_exit_metadata(self):
        body = '\n'.join((
            self.event('step_start'),
            self.event('text', part={'text': 'fixture result'}),
            self.event('step_finish', part={'tokens': {'input': 5, 'output': 8}}),
        )) + '\n'
        result = self.run_with_valid_binding(root=self.root, read_paths=['source.py'], write_paths=['source.py'],
                     prompt='bounded task prompt', timeout_seconds=5,
                     executable=self.fake_cli(body))
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

    def test_private_key_source_is_rejected_before_any_cli_launch(self):
        (self.root / 'source.py').write_text(
            '-----BEGIN PRIVATE KEY-----\nfixture-only\n-----END PRIVATE KEY-----\n')
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
