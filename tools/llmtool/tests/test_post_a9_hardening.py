"""Behavior regressions for the Post-A9 QA findings; all providers are offline."""

import copy
import io
import os
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from llmtool import config, context, feedback, orchestration
from llmtool.cli import main
from llmtool.configure import run_configure
from llmtool.errors import LLMToolError
from llmtool.inputs import read_input
from llmtool.log_safety import OUTPUT_LIMIT, validate_log_suggestions
from llmtool.providers.base import Reply
from llmtool.validator import CommandRisk, classify_command, requires_approval


class KubectlOverrideTests(unittest.TestCase):
    def test_identity_destination_flags_require_approval_in_any_position(self):
        valued = ('-s', '--server', '--token', '--kubeconfig', '--as', '--as-group',
                  '--as-uid', '--as-user-extra', '--client-certificate', '--client-key',
                  '--certificate-authority', '--username', '--password', '--tls-server-name',
                  '--proxy-url', '--cluster', '--user', '--kuberc')
        for option in valued:
            for flag in (option + ' value', option + '=value'):
                for command in ('kubectl ' + flag + ' get pods',
                                'kubectl get pods ' + flag,
                                'kubectl --namespace ns ' + flag + ' --context ctx get pods'):
                    with self.subTest(command=command):
                        risk = classify_command(command).risk
                        self.assertNotEqual(risk, CommandRisk.SAFE_READ)
                        self.assertTrue(requires_approval(risk))
        for flag in ('--insecure-skip-tls-verify', '--insecure-skip-tls-verify=true', '-shttps://evil.invalid'):
            self.assertEqual(classify_command('kubectl ' + flag + ' get pods').risk, CommandRisk.UNKNOWN)

    def test_higher_risks_survive_connection_overrides(self):
        for flag in ('--server=https://evil.invalid', '--token abc', '--as admin'):
            for operation, expected in (('delete pods', CommandRisk.DESTRUCTIVE),
                                        ('apply -f change.yaml', CommandRisk.MUTATING),
                                        ('get secrets -o yaml', CommandRisk.SENSITIVE_READ)):
                for command in ('kubectl ' + flag + ' ' + operation,
                                'kubectl ' + operation + ' --context ctx ' + flag + ' -n ns'):
                    with self.subTest(command=command):
                        self.assertEqual(classify_command(command).risk, expected)
        self.assertEqual(classify_command('kubectl get pods').risk, CommandRisk.SAFE_READ)


class InputProbeTests(unittest.TestCase):
    def pipe(self, data=None, eof=False):
        reader, writer = os.pipe()
        stream = os.fdopen(reader, 'rb', buffering=0)
        self.addCleanup(stream.close)
        if data:
            os.write(writer, data)
        if eof:
            os.close(writer)
        else:
            self.addCleanup(os.close, writer)
            self.pipe_writer = writer
        return stream

    def test_nonempty_real_pipe_conflicts_before_config_credentials_or_provider(self):
        for source in (['task'], ['--file', __file__]):
            out, err = io.StringIO(), io.StringIO()
            with patch('sys.stdin', self.pipe(b'already queued')), \
                    patch('llmtool.cli.resolve_config') as resolve, \
                    patch('llmtool.cli.api_key') as key, \
                    patch('llmtool.cli.create_client') as factory, \
                    redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(main(['ask', *source]), 1)
            self.assertEqual(out.getvalue(), '')
            self.assertIn('provide only one', err.getvalue())
            resolve.assert_not_called()
            key.assert_not_called()
            factory.assert_not_called()

    def test_empty_live_pipe_and_eof_do_not_block_or_consume(self):
        for eof in (False, True):
            stream = self.pipe(eof=eof)
            start = time.monotonic()
            self.assertEqual(read_input('task', None, stream), 'task')
            self.assertLess(time.monotonic() - start, 1)
        stream = self.pipe()
        self.assertEqual(read_input('task', None, stream), 'task')
        os.write(self.pipe_writer, b'later data')
        self.assertEqual(stream.read(10), b'later data')

    def test_real_terminal_and_regular_fd_sources(self):
        import pty
        with tempfile.TemporaryFile() as file:
            file.write(b'data')
            file.seek(0)
            self.assertEqual(read_input('task', None, file), 'task')
            file.seek(4)
            self.assertEqual(read_input('task', None, file), 'task')
        try:
            master, slave = pty.openpty()
        except OSError:
            # The sandboxed Mac CI closes terminals (Issue #99); reported as a
            # skip, so the terminal case is NOT_RUN there, never PASS.
            self.skipTest('no pseudo-terminal available (sandboxed CI)')
        self.addCleanup(os.close, master)
        with os.fdopen(slave, 'rb', buffering=0) as tty:
            self.assertEqual(read_input('task', None, tty), 'task')

    def test_memory_stream_and_unsupported_stream_preserve_explicit_input(self):
        self.assertEqual(read_input('task', None, io.StringIO('data')), 'task')
        self.assertEqual(read_input('task', None, io.StringIO('')), 'task')
        class Unsupported:
            def isatty(self):
                return False
            def fileno(self):
                raise OSError('private detail')
        self.assertEqual(read_input('task', None, Unsupported()), 'task')


class PersistedConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = config.user_config_path(self.root)
        self.path.parent.mkdir(parents=True)
        self.saved = copy.deepcopy(config.DEFAULTS)
        self.saved['providers'] = {name: {
            'type': 'openai-compatible', 'base_url': 'https://' + name + '.example',
            'model': name + '-model', 'timeout_seconds': 73,
            'api_key': None, 'api_key_env': 'TEST_KEY',
            'provider_options': {'enable_thinking': False},
        } for name in ('alpha', 'beta')}
        self.saved['active_provider'] = 'alpha'
        self.saved['generation'] = {'temperature': 0.7, 'max_tokens': 321}
        self.path.write_text(yaml.safe_dump(self.saved))

    def wizard(self, answers, secret=''):
        prompts = []
        answers = iter(answers)
        def answer(prompt):
            prompts.append(prompt)
            return next(answers)
        out, err = io.StringIO(), io.StringIO()
        tty = Mock()
        tty.isatty.return_value = True
        with patch('sys.stdin', tty):
            run_configure(self.root, answer, lambda *a, **k: secret, out, err)
        return yaml.safe_load(self.path.read_text()), ''.join(prompts) + out.getvalue() + err.getvalue()

    def test_blank_edit_preserves_profile_generation_and_other_profile(self):
        after, _ = self.wizard(['yes'] + [''] * 10)
        self.assertEqual(after, self.saved)

    def test_new_profile_preserves_active_generation_and_existing_profiles(self):
        after, _ = self.wizard(['yes', 'new', '', 'https://new.example', 'new-model'] + [''] * 6)
        self.assertEqual(after['active_provider'], 'alpha')
        self.assertEqual(after['generation'], self.saved['generation'])
        for name in self.saved['providers']:
            self.assertEqual(after['providers'][name], self.saved['providers'][name])
        self.assertEqual(after['providers']['new']['model'], 'new-model')

    def test_partial_other_profile_and_generation_mapping_are_preserved_exactly(self):
        self.saved['providers']['beta'] = {'type': 'claude-cli'}
        self.saved['generation'] = {'max_tokens': 321}
        self.path.write_text(yaml.safe_dump(self.saved))
        after, _ = self.wizard(['yes'] + [''] * 10)
        self.assertEqual(after['providers']['beta'], {'type': 'claude-cli'})
        self.assertEqual(after['generation'], {'max_tokens': 321})

    def test_two_live_steps_use_persisted_models_and_credentials_in_order(self):
        plan = orchestration.OrchestrationPlan([
            orchestration.RoleBinding('grinder', 'alpha'), orchestration.RoleBinding('reviewer', 'beta')])
        clients = [Mock(), Mock()]
        for client in clients:
            client.complete.return_value = Reply('safe result', {})
        env = {'LLMTOOL_MODEL': 'wrong', 'LLMTOOL_BASE_URL': 'https://wrong.example',
               'LLMTOOL_PROVIDER': 'missing', 'TEST_KEY': 'SYNTHETIC_SECRET'}
        events = []
        real_resolve, real_key = config.resolve_profile_config, config.api_key
        def resolve(name, **kwargs):
            result = real_resolve(name, **kwargs)
            events.append('ready:' + name)
            return result
        def key(settings):
            events.append('key:' + settings['active_provider'])
            return real_key(settings)
        with patch('llmtool.config.user_config_path', return_value=self.path), patch.dict(os.environ, env), \
                patch('llmtool.orchestration.resolve_profile_config', side_effect=resolve), \
                patch('llmtool.orchestration.api_key', side_effect=key), \
                patch('llmtool.orchestration.create_client', side_effect=clients) as factory:
            result = orchestration.run_orchestration_plan(plan, 'task')
        self.assertEqual(events, ['ready:alpha', 'key:alpha', 'ready:beta', 'key:beta'])
        self.assertEqual([config.selected_provider(c.args[0])['model'] for c in factory.call_args_list],
                         ['alpha-model', 'beta-model'])
        self.assertTrue(all(c.args[1] == 'SYNTHETIC_SECRET' for c in factory.call_args_list))
        self.assertEqual(len(result.steps), 2)

    def test_explicit_changes_and_hidden_key_preservation(self):
        self.saved['providers']['alpha'].update(api_key='STORED_SECRET', api_key_env=None)
        self.path.write_text(yaml.safe_dump(self.saved))
        after, displayed = self.wizard(['yes'] + [''] * 10)
        self.assertEqual(after, self.saved)
        self.assertNotIn('STORED_SECRET', displayed)
        after, displayed = self.wizard(['yes', '', '', 'https://changed.example', 'changed-model',
                                       '99', '', 'true', '0.2', '123', 'beta'], secret='NEW_SECRET')
        self.assertEqual(after['providers']['alpha']['model'], 'changed-model')
        self.assertEqual(after['providers']['alpha']['api_key'], 'NEW_SECRET')
        self.assertEqual(after['generation'], {'temperature': 0.2, 'max_tokens': 123})
        self.assertEqual(after['active_provider'], 'beta')
        self.assertNotIn('NEW_SECRET', displayed)

    def test_profile_resolution_ignores_project_and_generic_env_but_reads_configured_key(self):
        (self.root / '.llmtool.yaml').write_text('providers: {alpha: {model: project-model}}\ngeneration: {max_tokens: 999}')
        env = {'LLMTOOL_MODEL': 'env-model', 'LLMTOOL_BASE_URL': 'https://env.example',
               'LLMTOOL_PROVIDER': 'missing', 'LLMTOOL_MAX_TOKENS': '999', 'TEST_KEY': 'SYNTHETIC_SECRET'}
        with patch.dict(os.environ, env), patch('pathlib.Path.cwd', return_value=self.root):
            for name in ('alpha', 'beta'):
                resolved = config.resolve_profile_config(name, home=self.root)
                self.assertEqual(config.selected_provider(resolved), self.saved['providers'][name])
                self.assertEqual(resolved['generation'], self.saved['generation'])
                self.assertEqual(config.api_key(resolved), 'SYNTHETIC_SECRET')
        normal = config.resolve_config({'provider': 'alpha'}, home=self.root, cwd=self.root, environ=env)
        self.assertEqual(config.selected_provider(normal)['model'], 'env-model')
        self.assertEqual(config.selected_provider(normal)['base_url'], 'https://env.example')
        self.assertEqual(normal['generation']['max_tokens'], 999)

    def test_readiness_failure_and_unknown_profile_stop_before_key_or_later_profile(self):
        self.saved['providers']['alpha']['model'] = None
        self.path.write_text(yaml.safe_dump(self.saved))
        for name in ('alpha', 'missing'):
            plan = orchestration.OrchestrationPlan([
                orchestration.RoleBinding('grinder', name), orchestration.RoleBinding('reviewer', 'beta')])
            with patch('llmtool.config.user_config_path', return_value=self.path), \
                    patch('llmtool.orchestration.api_key') as key, \
                    patch('llmtool.orchestration.create_client') as factory, \
                    patch('llmtool.orchestration.resolve_profile_config', wraps=config.resolve_profile_config) as resolve:
                with self.assertRaises(LLMToolError):
                    orchestration.run_orchestration_plan(plan, 'task')
                self.assertEqual(resolve.call_count, 1)
                key.assert_not_called()
                factory.assert_not_called()


class UnicodeSafetyTests(unittest.TestCase):
    def test_feedback_and_benchmark_public_json_roundtrip(self):
        from llmtool.benchmark import BenchmarkSpec, parse_benchmark_spec, evaluate_benchmark
        text = '中文　內容 hello\u00a0world'
        case = feedback.FeedbackCase.capture('analyze-log', 2, text, text, text)
        self.assertEqual(feedback.parse_feedback_case(case.to_json()), case)
        regression = feedback.RegressionCase.create([case.id], 'analyze-log', 2, text, [
            feedback.RegressionAssertion(feedback.AssertionType.TEXT_CONTAINS, text)])
        spec = BenchmarkSpec(regression, text, tags=[text])
        self.assertEqual(parse_benchmark_spec(spec.to_json()), spec)
        self.assertTrue(evaluate_benchmark(spec, text).passed)

    def test_normal_unicode_separators_and_contract_line_controls(self):
        for value in ('中文　內容', 'hello\u00a0world', 'a\tb\nc\rd', 'a\u2028b'):
            self.assertEqual(feedback._text(value), value)
            self.assertEqual(context._text(value), value)

    def test_unsafe_controls_invalid_unicode_and_blank_text(self):
        for value in ('a\0b', 'a\x1bb', 'a\x7fb', 'a\x85b', 'a\x9fb', 'a\u202eb', 'a\u2066b', 'a\ud800b', '\u3000\u00a0'):
            for validate in (feedback._text, context._text):
                with self.subTest(value=repr(value), validate=validate), self.assertRaises(LLMToolError):
                    validate(value)

    def test_utf8_byte_bound_unchanged(self):
        self.assertEqual(feedback._text('中' * 3, limit=9), '中' * 3)
        with self.assertRaises(LLMToolError):
            feedback._text('中' * 4, limit=9)


class AnalyzeLogSafetyTests(unittest.TestCase):
    def test_safe_supported_suggestions_preserve_text(self):
        for text in ('ROOT_CAUSE:\nUnproven.\nNEXT_COMMANDS:\n- stat /etc/app/config.yaml',
                     'NEXT_COMMANDS:\n(none)', '**NEXT_COMMANDS:**\n1. `kubectl get pods`'):
            validate_log_suggestions(text)

    def test_obvious_credential_display_is_rejected_with_flags_wrappers_pipes(self):
        for command in ('kubectl get secrets -o yaml | grep key',
                        'kubectl -n NS --context CTX get secret/name -o json',
                        'kubectl get -n NS secrets', 'sudo kubectl get pods,secrets',
                        '/usr/bin/kubectl get secrets.v1 -o yaml',
                        'cat /etc/app/config.yaml', 'grep token credentials.txt',
                        'head private.key', 'sudo -u root cat private.key',
                        'sudo -u root grep value credentials.txt', 'env', 'git push', 'rm -rf FILE'):
            with self.subTest(command=command):
                output, count = validate_log_suggestions('NEXT_COMMANDS:\n- ' + command)
                self.assertEqual(count, 1)
                self.assertNotIn(command, output)

    def test_transport_bounds_fail_but_bad_suggestions_are_suppressed(self):
        with self.assertRaises(LLMToolError):
            validate_log_suggestions('x' * (OUTPUT_LIMIT + 1))
        for command in ('sh -c "kubectl get secrets"', '$(anything)', 'pwd\x1b'):
            output, count = validate_log_suggestions('ROOT_CAUSE:\nEvidence\nNEXT_COMMANDS:\n- ' + command)
            self.assertIn('ROOT_CAUSE:', output)
            self.assertEqual(count, 1)

    def test_unsafe_cli_reply_never_reaches_stdout_metadata_or_execution(self):
        for args in (['analyze-log', 'evidence', '--verbose'],
                     ['ask', '--skill', 'analyze-log', 'evidence', '--verbose']):
            client = Mock()
            client.complete.return_value = Reply('NEXT_COMMANDS:\n- kubectl get secrets -o yaml', {'model': 'PRIVATE_METADATA'})
            out, err = io.StringIO(), io.StringIO()
            with patch('sys.stdin', io.StringIO('')), patch('llmtool.cli.resolve_config', return_value={}), \
                    patch('llmtool.cli.api_key', return_value=None), \
                    patch('llmtool.cli.create_client', return_value=client), \
                    patch('subprocess.run', side_effect=AssertionError('execution')), \
                    patch('os.system', side_effect=AssertionError('execution')), \
                    redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(main(args), 0)
            self.assertIn('(none)', out.getvalue())
            self.assertNotIn('kubectl', out.getvalue())
            self.assertIn('suppressed 1', err.getvalue())
            self.assertNotIn('kubectl', err.getvalue())


if __name__ == '__main__':
    unittest.main()
