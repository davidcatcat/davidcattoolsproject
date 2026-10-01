import io
import json
import os
import socket
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import Mock, patch

from llmtool.cli import main
from llmtool.context import ContextPacket
from llmtool.feedback import FeedbackCase
from llmtool.errors import LLMToolError
from llmtool.inputs import read_input
from llmtool.log_safety import filter_log_suggestions
from llmtool.providers.base import Reply
from llmtool.validator import credential_display, classify_command, CommandRisk, sensitive_path, requires_approval

FIXTURES = json.loads(Path(__file__).with_name('fixtures').joinpath('analyze_log_review.json').read_text())
BYPASSES = (
    'kubectl get --raw /api/v1/namespaces/NS/secrets/NAME',
    'kubectl get --raw=/api/v1/secrets', 'oc get secret NAME', 'oc get secrets -o yaml',
    'echo secrets | xargs kubectl get -o yaml',
    "kubectl get pod POD -o jsonpath='{.spec.containers[*].env}'",
    'kubectl exec POD -- env', 'kubectl --server https://evil.invalid get pods',
    'kubectl --token t get pods', 'kubectl --kubeconfig k get pods', 'oc --as admin get pods',
    'cat CONFIG_FILE_PATH', 'grep password <CONFIG_FILE_PATH>', 'cat PRIVATE_KEY_PATH',
    'cat /path/to/postgresql/pg_hba.conf',
    'od -c ~/.ssh/id_rsa', 'aws secretsmanager get-secret-value', 'vault kv get secret/x',
    'helm get values RELEASE', 'git config --list', 'env', 'printenv', 'env X=1 rm FILE', 'git push', 'rm -rf FILE',
)


class ReviewSuggestionTests(unittest.TestCase):
    def test_gateway_pattern_fixtures_preserve_analysis_and_diagnostics(self):
        for fixture in FIXTURES:
            raw = fixture['output']
            output, suppressed = filter_log_suggestions(raw)
            self.assertEqual(output.split('NEXT_COMMANDS:')[0], raw.split('NEXT_COMMANDS:')[0])
            if fixture['name'] == 'mixed suggestions':
                self.assertEqual(suppressed, 2)
                self.assertIn('systemctl status app.service', output)
                self.assertNotIn('cat CONFIG_FILE_PATH', output)
                self.assertNotIn('kubectl get secrets', output)
            else:
                self.assertEqual(suppressed, 0)
            if fixture['name'] == 'ordinary diagnostics':
                self.assertIn('<SERVICE_NAME>', output)
                self.assertIn('These checks may provide more evidence.', output)

    def test_all_bypasses_suppress_only_suggestion(self):
        for command in BYPASSES:
            with self.subTest(command=command):
                output, count = filter_log_suggestions('ROOT_CAUSE:\nObserved failure.\nNEXT_COMMANDS:\n- ' + command)
                self.assertIn('Observed failure.', output)
                self.assertNotIn(command, output)
                self.assertEqual(count, 1)
                self.assertIn('(none)', output)
                if command.startswith(('echo ', 'rm ', 'git push')):
                    self.assertEqual(filter_log_suggestions('NEXT_COMMANDS:\n' + command)[1], 1)

    def test_display_tools_and_sensitive_placeholder_names(self):
        for tool in ('cat', 'grep', 'head', 'tail', 'sed', 'awk', 'less', 'more', 'base64', 'hexdump', 'xxd', 'strings', 'od'):
            for name in ('SECRET', 'CREDENTIAL', 'PASSWORD', 'TOKEN', 'PRIVATE_KEY', 'API_KEY', 'CONFIG_FILE_PATH'):
                command = tool + ' ' + name
                self.assertTrue(credential_display(command))
                self.assertEqual(filter_log_suggestions('NEXT_COMMANDS:\n- ' + command)[1], 1)

    def test_interpreter_names_in_arguments_are_not_programs(self):
        for command in ('ls -la /usr/bin/python3', 'kubectl logs POD -c python', 'grep -n sh app.log'):
            output, count = filter_log_suggestions('NEXT_COMMANDS:\n- ' + command)
            self.assertEqual(count, 0)
            self.assertIn(command, output)

    def test_three_actual_suggestions_and_command_only_controls(self):
        raw = 'ROOT_CAUSE:\nEmoji 👩‍💻 copied \u200btext \u001b[31m\nNEXT_COMMANDS:\n4. pwd\n8. ls\n9. stat FILE\n10. pwd'
        output, count = filter_log_suggestions(raw)
        self.assertEqual(count, 1)
        self.assertIn(raw.split('NEXT_COMMANDS:')[0], output)
        self.assertNotIn('10. pwd', output)
        for control in ('\u200b', '\u200d', '\x1b', '\u202e'):
            self.assertEqual(filter_log_suggestions('NEXT_COMMANDS:\n- p' + control + 'wd')[1], 1)
        output, count = filter_log_suggestions('NEXT_COMMANDS:\nEXTRA_HEADER\n- cat PRIVATE_KEY_PATH')
        self.assertEqual(count, 1)
        self.assertNotIn('cat PRIVATE_KEY_PATH', output)

    def test_cli_preserves_analysis_returns_zero_calls_once_never_executes(self):
        for fixture in FIXTURES:
            client = Mock()
            client.complete.return_value = Reply(fixture['output'], {})
            out, err = io.StringIO(), io.StringIO()
            with patch('sys.stdin', None), patch('llmtool.cli.resolve_config', return_value={}), \
                    patch('llmtool.cli.api_key', return_value=None), patch('llmtool.cli.create_client', return_value=client), \
                    patch('subprocess.run', side_effect=AssertionError('execution')), \
                    patch('subprocess.Popen', side_effect=AssertionError('execution')), \
                    patch('os.system', side_effect=AssertionError('execution')), redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(main(['analyze-log', 'synthetic evidence']), 0)
            client.complete.assert_called_once()
            self.assertIn('ROOT_CAUSE:', out.getvalue())
            if fixture['name'] == 'mixed suggestions':
                self.assertIn('suppressed 2', err.getvalue())
                self.assertNotIn('CONFIG_FILE_PATH', err.getvalue())

    def test_shared_a6_detection_keeps_higher_risks(self):
        self.assertEqual(classify_command('od -c ~/.ssh/id_rsa').risk, CommandRisk.SENSITIVE_READ)
        self.assertEqual(classify_command('kubectl get secret NAME --template={{.data}}').risk, CommandRisk.SENSITIVE_READ)
        self.assertEqual(classify_command('rm credentials.txt').risk, CommandRisk.DESTRUCTIVE)


class F1CredentialTests(unittest.TestCase):
    COMMANDS = (
        'cat /etc/shadow', 'sudo cat /etc/shadow', 'head -n 20 /etc/shadow',
        'cat /etc/gshadow', 'cat /var/lib/postgresql/.pgpass', 'cat ~/.pgpass',
        'cat /proc/1/environ', 'strings /proc/1234/environ', 'cat /proc/self/environ',
        'cat /proc/thread-self/environ', 'cat /proc/*/environ', 'set',
    )

    def test_shared_detection_a6_and_suggestion_suppression(self):
        for path in ('/etc/shadow', '/etc/gshadow', '.pgpass', '~/.pgpass',
                     '/var/lib/postgresql/.pgpass', '/proc/1/environ', '/proc/self/environ'):
            self.assertTrue(sensitive_path(path), path)
        for command in self.COMMANDS:
            with self.subTest(command=command):
                self.assertTrue(credential_display(command))
                risk = classify_command(command).risk
                self.assertEqual(risk, CommandRisk.SENSITIVE_READ)
                self.assertTrue(requires_approval(risk))
                output, suppressed = filter_log_suggestions('ROOT_CAUSE:\nSupplied evidence.\nNEXT_COMMANDS:\n' + command)
                self.assertEqual(suppressed, 1)
                self.assertIn('Supplied evidence.', output)
                self.assertNotIn(command, output)
                self.assertIn('(none)', output)

    def test_nearby_paths_and_set_arguments_are_not_credential_dumps(self):
        for path in ('/etc/hosts', '/etc/shadowing', '/etc/gshadow.txt', '/proc/1/status',
                     '/proc/self/status', '/proc/1234/environ.txt', '/tmp/environ', '.pgpass.example'):
            self.assertFalse(sensitive_path(path), path)
            command = 'cat ' + path
            self.assertFalse(credential_display(command))
            self.assertEqual(classify_command(command).risk, CommandRisk.UNKNOWN)
            output, suppressed = filter_log_suggestions('NEXT_COMMANDS:\n- ' + command)
            self.assertEqual(suppressed, 0)
            self.assertIn(command, output)
        for command in ('set -e', 'set -- VALUE', 'grep set app.log'):
            self.assertFalse(credential_display(command), command)

    def test_cli_suppresses_without_losing_analysis_retry_or_execution(self):
        for command in self.COMMANDS:
            client = Mock()
            client.complete.return_value = Reply('ROOT_CAUSE:\nObserved failure.\nEVIDENCE:\nSupplied log.\nNEXT_COMMANDS:\n- ' + command, {})
            out, err = io.StringIO(), io.StringIO()
            with patch('sys.stdin', None), patch('llmtool.cli.resolve_config', return_value={}), \
                    patch('llmtool.cli.api_key', return_value=None), patch('llmtool.cli.create_client', return_value=client), \
                    patch('subprocess.run', side_effect=AssertionError('execution')), \
                    patch('subprocess.Popen', side_effect=AssertionError('execution')), \
                    patch('os.system', side_effect=AssertionError('execution')), redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(main(['analyze-log', 'synthetic evidence']), 0)
            client.complete.assert_called_once()
            self.assertIn('Observed failure.', out.getvalue())
            self.assertIn('Supplied log.', out.getvalue())
            self.assertNotIn(command, out.getvalue())
            self.assertIn('suppressed 1', err.getvalue())
            self.assertNotIn(command, err.getvalue())


class ReviewInputUnicodeTests(unittest.TestCase):
    def test_none_closed_and_regular_file_explicit_inputs(self):
        closed = io.StringIO(); closed.close()
        with tempfile.TemporaryFile() as file:
            file.write(b'outer loop unread lines'); file.seek(0)
            for stream in (None, closed, file):
                self.assertEqual(read_input('q', None, stream), 'q')
                self.assertIn('ReviewInputUnicodeTests', read_input(None, __file__, stream))

    def test_socket_queued_data_conflicts_and_unsupported_probe_is_nonblocking(self):
        a, b = socket.socketpair()
        self.addCleanup(a.close); self.addCleanup(b.close)
        with a.makefile('rb', buffering=0) as stream:
            self.assertEqual(read_input('q', None, stream), 'q')
            b.sendall(b'queued')
            with self.assertRaises(LLMToolError):
                read_input('q', None, stream)
            with patch('llmtool.inputs.os.name', 'nt'):
                self.assertEqual(read_input('q', None, stream), 'q')

    def test_stdin_only_readiness_failure_does_not_touch_open_pipe(self):
        reader, writer = os.pipe()
        self.addCleanup(os.close, writer)
        with os.fdopen(reader, 'rb', buffering=0) as stream:
            out, err = io.StringIO(), io.StringIO()
            with patch('sys.stdin', stream), patch('llmtool.cli.resolve_config', side_effect=LLMToolError('not configured')), \
                    patch('llmtool.cli.read_input', side_effect=AssertionError('read too early')), \
                    patch('llmtool.cli.api_key') as key, redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(main(['ask']), 1)
            key.assert_not_called()
            self.assertIn('not configured', err.getvalue())

    def test_general_unicode_formats_roundtrip_and_controls_still_fail(self):
        for value in ('emoji 👩‍💻', 'a\u200cb', 'a\u200bb', 'a\u00adb', 'a\ufeffb'):
            packet = ContextPacket([value], [], [], [], [], [])
            self.assertIn(value, packet.to_json())
            case = FeedbackCase.capture('analyze-log', 2, value, value, value)
            self.assertEqual(case.input, value)
        for value in ('a\u202eb', 'a\x85b', 'a\ud800b'):
            with self.assertRaises(LLMToolError):
                ContextPacket([value], [], [], [], [], [])
