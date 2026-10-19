"""Interactive UX and state regressions; no live model or auth dependencies."""
import copy
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmtool.chat import ChatSession, run_chat
from llmtool.cli import main
from llmtool.config import resolve_config
from llmtool.errors import LLMToolError
from llmtool.prompts import COMMON, chat_messages, messages_for
from llmtool.providers import create_client
from llmtool.providers.base import Reply
from llmtool.skills import load_skill


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.path = self.home / '.config/llmtool/config.yaml'
        self.path.parent.mkdir(parents=True)
        self.path.write_text('''active_provider: first
providers:
  first:
    base_url: https://example.test
    model: test
    api_key: PRIVATE_KEY
  second:
    type: claude-cli
  incomplete:
    type: openai
''')
        self.config = resolve_config(environ={}, cwd=self.home, home=self.home)
        self.session = ChatSession(self.config)
        self.client = Mock()
        self.client.complete.return_value = Reply('reply', {'internal': 'PRIVATE_REASONING'})
        self.factory = patch('llmtool.chat.create_client', return_value=self.client).start()
        self.addCleanup(patch.stopall)

    def send(self, text='question'):
        return self.session.send(text)

    def repl(self, lines, session=None):
        out, err = io.StringIO(), io.StringIO()
        reader = Mock(side_effect=list(lines) + [EOFError()])
        status = run_chat(session or self.session, reader, out, err)
        return status, out.getvalue(), err.getvalue()

    def cli(self, args, lines):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {}, clear=True), patch('llmtool.config.user_config_path', return_value=self.path), patch('llmtool.config.Path.cwd', return_value=self.home), patch('builtins.input', side_effect=list(lines)+[EOFError()]), redirect_stdout(out), redirect_stderr(err):
            status = main(args)
        return status, out.getvalue(), err.getvalue()

    def test_enter_chat_and_exit(self):
        status, out, err = self.cli(['chat'], ['/exit'])
        self.assertEqual((status, err), (0, ''))
        self.assertIn('LLMTool interactive chat', out)
        self.assertIn('provider: first', out)
        self.assertIn('skill: none', out)
        self.assertIn('> ', out)
        self.factory.assert_not_called()

    def test_eof_clean(self):
        self.assertEqual(self.repl([])[0], 0)
        self.assertFalse(self.session.active)
        self.factory.assert_not_called()

    def test_interrupt_at_input(self):
        status, out, err = self.repl([KeyboardInterrupt()])
        self.assertEqual((status, err), (0, ''))
        self.assertNotIn('Traceback', out)
        self.assertFalse(self.session.active)

    def test_quit_alias(self):
        self.assertEqual(self.repl(['/quit'])[0], 0)
        self.assertFalse(self.session.active)

    def test_help_offline(self):
        output = self.session.command('/help')
        for command in ('/status', '/provider', '/skill', '/clear', '/history', '/exit', '/quit'):
            self.assertIn(command, output)
        self.factory.assert_not_called()

    def test_status_sanitized_offline(self):
        self.send()
        self.factory.reset_mock()
        out = self.session.command('/status')
        self.assertIn('type: openai-compatible', out)
        self.assertIn('conversation turns: 2', out)
        self.assertNotIn('PRIVATE', out)
        self.assertNotIn('https', out)
        self.factory.assert_not_called()

    def test_multiturn_common_messages(self):
        self.send('first question')
        self.client.complete.return_value = Reply('second reply', {})
        self.send('second question')
        messages = self.client.complete.call_args.args[0]
        self.assertEqual(messages, [
            {'role': 'system', 'content': COMMON},
            {'role': 'user', 'content': 'first question'},
            {'role': 'assistant', 'content': 'reply'},
            {'role': 'user', 'content': 'second question'},
        ])
        self.assertEqual(len(self.session.history), 4)

    def test_exact_text_spaces_and_unicode(self):
        text = '  測試 service 500  '
        self.repl([text, '/exit'])
        self.assertEqual(self.client.complete.call_args.args[0][-1]['content'], text)
        self.assertEqual(self.session.history[0]['content'], text)

    def test_blank_input_no_call(self):
        self.repl(['', '   ', '\t', '/exit'])
        self.factory.assert_not_called()
        self.assertEqual(self.session.history, [])

    def test_provider_list_safe_offline(self):
        out = self.session.command('/provider')
        self.assertIn('second (claude-cli)', out)
        self.assertNotIn('PRIVATE', out)
        self.assertNotIn('https', out)
        self.factory.assert_not_called()

    def test_provider_switch_preserves_history_and_config(self):
        original = self.path.read_bytes()
        self.send()
        history = copy.deepcopy(self.session.history)
        self.factory.reset_mock()
        with patch('llmtool.chat.api_key', side_effect=AssertionError):
            output = self.session.command('/provider second')
        self.assertIn('history will be sent', output)
        self.assertEqual(self.session.provider, 'second')
        self.assertEqual(self.session.history, history)
        self.factory.assert_not_called()
        self.send('next')
        self.assertEqual(self.factory.call_args.args[0]['active_provider'], 'second')
        self.assertEqual(self.client.complete.call_args.args[0][1:3], history)
        self.assertEqual(self.config['active_provider'], 'first')
        self.assertEqual(self.path.read_bytes(), original)

    def test_invalid_provider_atomic_and_sanitized(self):
        self.send()
        before = copy.deepcopy(self.session.history)
        for name in ('PRIVATE_UNKNOWN', '../PRIVATE', 'incomplete'):
            with self.subTest(name=name), self.assertRaises(LLMToolError) as error:
                self.session.command('/provider '+name)
            self.assertNotIn('PRIVATE', str(error.exception))
            self.assertEqual(self.session.provider, 'first')
            self.assertEqual(self.session.history, before)

    def test_skill_list_offline(self):
        out = self.session.command('/skill')
        self.assertIn('skill: none', out)
        self.assertIn('analyze-log', out)
        self.factory.assert_not_called()

    def test_skill_selection_preserves_history_config(self):
        original = self.path.read_bytes()
        self.send()
        history = copy.deepcopy(self.session.history)
        self.factory.reset_mock()
        self.session.command('/skill analyze-log')
        self.assertEqual(self.session.skill.name, 'analyze-log')
        self.assertEqual(self.session.history, history)
        self.factory.assert_not_called()
        self.send('next')
        messages = self.client.complete.call_args.args[0]
        self.assertEqual(messages[1:3], history)
        self.assertEqual(messages[0]['content'].count('Selected skill: analyze-log'), 1)
        self.assertEqual(self.path.read_bytes(), original)

    def test_skill_switch_and_clear_rebuild_system_only(self):
        self.session.command('/skill analyze-log')
        self.send()
        self.session.command('/skill review-diff')
        self.send('next')
        system = self.client.complete.call_args.args[0][0]['content']
        self.assertIn(load_skill('review-diff').instructions, system)
        self.assertNotIn('Selected skill: analyze-log', system)
        self.session.command('/skill none')
        self.assertIsNone(self.session.skill)
        self.send('third')
        self.assertEqual(self.client.complete.call_args.args[0][0]['content'], COMMON)
        self.assertEqual(len(self.session.history), 6)

    def test_invalid_skill_keeps_state(self):
        self.session.command('/skill review-diff')
        self.send()
        before = copy.deepcopy(self.session.history)
        for name in ('private-skill', '../private', 'CLEAR'):
            with self.subTest(name=name), self.assertRaises(LLMToolError):
                self.session.command('/skill '+name)
            self.assertEqual(self.session.skill.name, 'review-diff')
            self.assertEqual(self.session.history, before)

    def test_clear_keeps_provider_skill(self):
        self.session.command('/provider second')
        self.session.command('/skill context-pack')
        self.send()
        self.session.command('/clear')
        self.assertEqual(self.session.history, [])
        self.assertEqual(self.session.provider, 'second')
        self.assertEqual(self.session.skill.name, 'context-pack')
        self.send('fresh')
        self.assertEqual(len(self.client.complete.call_args.args[0]), 2)

    def test_history_excludes_system_and_metadata(self):
        self.session.command('/skill analyze-log')
        self.send('my question')
        out = self.session.command('/history')
        self.assertEqual(out, 'user:\nmy question\nassistant:\nreply')
        self.assertNotIn(COMMON, out)
        self.assertNotIn('PRIVATE_REASONING', out)
        self.assertNotIn('ROOT_CAUSE', out)

    def test_empty_history(self):
        self.assertEqual(self.session.command('/history'), 'conversation history is empty')

    def test_provider_error_rollback_recovery_sanitized(self):
        self.send('prior')
        self.client.complete.side_effect = [RuntimeError('PRIVATE raw stderr'), Reply('recovered', {})]
        status, out, err = self.repl(['failed', '/history', 'retry', '/exit'])
        self.assertEqual(status, 0)
        self.assertIn('chat request failed', err)
        self.assertNotIn('PRIVATE', out+err)
        self.assertEqual([t['content'] for t in self.session.history], ['prior', 'reply', 'retry', 'recovered'])
        self.assertNotIn('failed', str(self.client.complete.call_args.args[0]))

    def test_known_provider_errors_hidden(self):
        self.client.complete.side_effect = LLMToolError('provider rejected the request safely')
        with self.assertRaisesRegex(LLMToolError, 'provider rejected the request safely'):
            self.send()
        for error in (OSError('PRIVATE'), UnicodeError('PRIVATE')):
            self.client.complete.side_effect = error
            with self.subTest(error=error), self.assertRaises(LLMToolError) as caught:
                self.send()
            self.assertNotIn('PRIVATE', str(caught.exception))
            self.assertEqual(self.session.history, [])

    def test_interrupt_at_provider_preserves_history(self):
        self.send('prior')
        before = copy.deepcopy(self.session.history)
        self.client.complete.side_effect = KeyboardInterrupt()
        self.assertEqual(self.repl(['interrupted'])[0], 0)
        self.assertEqual(self.session.history, before)
        self.assertFalse(self.session.active)

    def test_chat_io_failure_returns_nonzero_without_traceback(self):
        def fail_input():
            raise OSError("private input details")
        out, err = io.StringIO(), io.StringIO()
        status = run_chat(self.session, fail_input, out, err)
        self.assertEqual(status, 1)
        self.assertIn("chat I/O failed", err.getvalue())
        self.assertNotIn("Traceback", out.getvalue() + err.getvalue())

    def test_tty_reply_and_history_escape_controls_but_keep_stored_data(self):
        class TTY(io.StringIO):
            def isatty(self):
                return True
        self.client.complete.return_value = Reply("reply\x1b]52;c;secret\x07\rspoof\u202e", {})
        out, err = TTY(), io.StringIO()
        lines = iter(["question", "/history", "/exit"])
        status = run_chat(self.session, lambda: next(lines), out, err)
        self.assertEqual(status, 0)
        self.assertIn("\\x1b]52;c;secret\\x07\\x0dspoof\\u202e", out.getvalue())
        self.assertEqual(self.session.history[-1]["content"], "reply\x1b]52;c;secret\x07\rspoof\u202e")

    def test_malformed_commands_do_not_call_provider_or_mutate(self):
        for command in ('/exit extra', '/clear extra', '/history extra', '/status extra', '/help extra', '/skill a b', '/provider a b', '/PRIVATE'):
            with self.subTest(command=command), self.assertRaises(LLMToolError) as caught:
                self.session.command(command)
            self.assertNotIn('PRIVATE', str(caught.exception))
            self.assertTrue(self.session.active)
        self.factory.assert_not_called()

    def test_returned_commands_only_text_no_execution_or_persistence(self):
        before = {p.relative_to(self.home): p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        command = '/provider second\ngit status; touch forbidden; cat /etc/private'
        self.client.complete.return_value = Reply(command, {})
        with patch('subprocess.run', side_effect=AssertionError), patch('subprocess.Popen', side_effect=AssertionError), patch('os.system', side_effect=AssertionError):
            status, out, err = self.repl(['question', '/history', '/exit'])
        self.assertEqual((status, err), (0, ''))
        self.assertIn(command, out)
        self.assertEqual(self.session.provider, 'first')
        after = {p.relative_to(self.home): p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_initial_cli_provider_and_skill(self):
        status, out, err = self.cli(['chat', '--provider', 'second', '--skill', 'review-diff'], ['question', '/exit'])
        self.assertEqual((status, err), (0, ''))
        self.assertIn('provider: second', out)
        self.assertIn('skill: review-diff', out)
        self.assertIn(load_skill('review-diff').instructions, self.client.complete.call_args.args[0][0]['content'])

    def test_incomplete_config_allows_offline_shell(self):
        self.path.write_text('providers: {default: {model: null}}')
        with patch('llmtool.chat.create_client', new=create_client):
            result = self.cli(['chat'], ['/help', '/status', 'question', '/exit'])
        self.assertEqual(result[0], 0)
        self.assertIn('provider base_url is not configured', result[2])

    def test_invalid_reply_rolls_back(self):
        for content in ('', '  ', None):
            self.client.complete.return_value = Reply(content, {})
            with self.subTest(content=content), self.assertRaises(LLMToolError):
                self.send()
            self.assertEqual(self.session.history, [])

    def test_request_is_detached_from_history(self):
        self.send('prior')
        before = copy.deepcopy(self.session.history)
        def mutate(messages):
            messages[1]['content'] = 'mutated'
            raise OSError('private')
        self.client.complete.side_effect = mutate
        with self.assertRaises(LLMToolError):
            self.send('next')
        self.assertEqual(self.session.history, before)

    def test_ask_contract_unchanged(self):
        client = Mock()
        client.complete.return_value = Reply('answer', {})
        with patch('llmtool.cli.create_client', return_value=client), patch('llmtool.cli.read_input', return_value=' exact '):
            result = self.cli(['ask', ' exact '], [])
        self.assertEqual(result, (0, 'answer\n', ''))
        client.complete.assert_called_once_with(messages_for('ask', ' exact '))

    def test_invalid_input_then_continue(self):
        result = self.repl(['bad\x00', 'valid', '/exit'])
        self.assertEqual(result[0], 0)
        self.assertIn('unsupported control', result[2])
        self.assertEqual(self.session.history[0]['content'], 'valid')

    # --- A3.5 UX-2: truthful runtime status (offline; no live provider) ---

    USAGE = {'prompt_tokens': 3, 'completion_tokens': 4, 'total_tokens': 7}

    def test_status_reports_known_usage_and_ignores_extra_metadata(self):
        self.client.complete.return_value = Reply('reply', dict(self.USAGE, model='served-model', elapsed_seconds=9.9,
                                                                internal='PRIVATE_REASONING'))
        self.assertEqual(self.send(), 'reply')
        self.assertEqual(dict(self.session.last_usage), self.USAGE)
        out = self.session.command('/status')
        self.assertIn('usage: prompt 3 / completion 4 / total 7 (provider-reported)', out)
        self.assertIn('reported model: served-model', out)
        self.assertIn('model: test (configured)', out)
        self.assertIn('state: idle', out)
        self.assertIn('connectivity: not probed', out)
        self.assertNotIn('PRIVATE', out)
        self.assertNotIn('9.9', out)
        self.assertEqual(len(self.session.history), 2)

    def test_status_zero_usage_is_known_not_unknown(self):
        self.client.complete.return_value = Reply('reply', {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0})
        self.send()
        self.assertIn('usage: prompt 0 / completion 0 / total 0', self.session.command('/status'))

    def test_invalid_usage_is_unknown_and_reply_still_succeeds(self):
        cases = (
            None, [], 'usage', {},
            {'prompt_tokens': 3, 'completion_tokens': 4},
            {'prompt_tokens': True, 'completion_tokens': 4, 'total_tokens': 5},
            {'prompt_tokens': 3, 'completion_tokens': 4, 'total_tokens': 8},
            {'prompt_tokens': -1, 'completion_tokens': 4, 'total_tokens': 3},
            {'prompt_tokens': 3.0, 'completion_tokens': 4, 'total_tokens': 7},
            {'prompt_tokens': '3', 'completion_tokens': 4, 'total_tokens': 7},
            {'prompt_tokens': 10**13, 'completion_tokens': 0, 'total_tokens': 10**13},
        )
        for index, metadata in enumerate(cases):
            with self.subTest(metadata=metadata):
                self.client.complete.return_value = Reply('ok %d' % index, metadata)
                self.assertEqual(self.send('q %d' % index), 'ok %d' % index)
                self.assertIsNone(self.session.last_usage)
                self.assertIn('usage: UNKNOWN', self.session.command('/status'))
                self.assertEqual(self.session.history[-1]['content'], 'ok %d' % index)
        self.assertEqual(len(self.session.history), 2 * len(cases))

    def test_usage_is_not_stale_after_reply_without_usage(self):
        self.client.complete.return_value = Reply('first', dict(self.USAGE))
        self.send()
        self.assertIsNotNone(self.session.last_usage)
        self.client.complete.return_value = Reply('second', {'model': 'm'})
        self.send('again')
        self.assertIsNone(self.session.last_usage)
        self.assertIn('usage: UNKNOWN', self.session.command('/status'))

    def test_failure_sets_error_unknown_usage_and_keeps_history(self):
        from llmtool.errors import coded_error
        self.client.complete.return_value = Reply('first', dict(self.USAGE))
        self.send('prior')
        before = copy.deepcopy(self.session.history)
        self.client.complete.side_effect = coded_error('provider rate limited the request', 'PROVIDER_RATE_LIMITED',
                                                       'provider.openai-compatible', retryable=True,
                                                       provider_metadata=dict(self.USAGE))
        with self.assertRaises(LLMToolError):
            self.send('next')
        self.assertEqual(self.session.history, before)
        self.assertIsNone(self.session.last_usage)
        out = self.session.command('/status')
        self.assertIn('state: error (PROVIDER_RATE_LIMITED)', out)
        self.assertIn('usage: UNKNOWN', out)
        self.client.complete.side_effect = RuntimeError('PRIVATE 429 Too Many Requests')
        with self.assertRaises(LLMToolError):
            self.send('next')
        out = self.session.command('/status')
        self.assertIn('state: error (unclassified)', out)
        self.assertNotIn('429', out)
        self.assertNotIn('PRIVATE', out)
        self.assertEqual(self.session.history, before)

    def test_legacy_error_text_is_not_classified_as_rate_limit(self):
        self.client.complete.side_effect = LLMToolError('HTTP 429 rate limited')
        with self.assertRaises(LLMToolError):
            self.send()
        self.assertIn('state: error (unclassified)', self.session.command('/status'))

    def test_elapsed_is_measured_with_monotonic_clock(self):
        self.client.complete.return_value = Reply('reply', {'elapsed_seconds': 99.0})
        with patch('llmtool.chat.time.monotonic', side_effect=[10.0, 12.5]):
            self.send()
        self.assertEqual(self.session.last_elapsed, 2.5)
        self.assertIn('elapsed: 2.500s (measured locally)', self.session.command('/status'))

    def test_state_is_working_only_during_request(self):
        seen = []
        def complete(messages):
            seen.append((self.session.state, self.session.request_started is not None))
            return Reply('reply', {})
        self.client.complete.side_effect = complete
        self.assertEqual(self.session.state, 'idle')
        self.send()
        self.assertEqual(seen, [('working', True)])
        self.assertEqual(self.session.state, 'idle')
        self.assertIsNone(self.session.request_started)

    def test_clear_and_provider_switch_reset_status(self):
        for command in ('/clear', '/provider second'):
            with self.subTest(command=command):
                self.client.complete.side_effect = None
                self.client.complete.return_value = Reply('reply', dict(self.USAGE, model='m'))
                self.session.command('/provider first')
                self.send()
                self.session.command(command)
                self.assertEqual(self.session.state, 'idle')
                self.assertIsNone(self.session.last_usage)
                self.assertIsNone(self.session.last_elapsed)
                out = self.session.command('/status')
                self.assertIn('usage: UNKNOWN', out)
                self.assertIn('reported model: UNKNOWN', out)
                self.assertIn('elapsed: UNKNOWN', out)

    def test_failed_provider_switch_keeps_status(self):
        self.client.complete.return_value = Reply('reply', dict(self.USAGE))
        self.send()
        with self.assertRaises(LLMToolError):
            self.session.command('/provider incomplete')
        self.assertEqual(dict(self.session.last_usage), self.USAGE)

    def test_cli_provider_reported_model_is_not_trusted(self):
        self.session.command('/provider second')
        self.client.complete.return_value = Reply('reply', {'model': 'configured-echo'})
        self.send()
        out = self.session.command('/status')
        self.assertIn('reported model: UNKNOWN', out)
        self.assertIn('model: provider default (not configured)', out)

    def test_status_commands_never_call_provider(self):
        self.session.command('/status')
        self.factory.assert_not_called()

    def test_line_mode_banner_unchanged(self):
        status, out, err = self.repl(['/exit'])
        self.assertEqual(out.splitlines()[:5], ['LLMTool interactive chat', 'provider: first', 'type: openai-compatible',
                                                'skill: none', 'conversation turns: 0'])
        self.assertNotIn('usage:', out.split('History stays')[0])

    def test_cli_tui_falls_back_to_line_chat_when_not_a_terminal(self):
        with patch('sys.stdin', io.StringIO('')):
            status, out, err = self.cli(['chat', '--tui'], ['/exit'])
        self.assertEqual(status, 0)
        self.assertIn('LLMTool interactive chat', out)
        self.assertIn('--tui unavailable', err)
        self.assertIn('using line chat', err)
        self.factory.assert_not_called()

    def test_default_chat_never_loads_optional_tui(self):
        with patch.dict(sys.modules, {'llmtool.chat_tui': None, 'prompt_toolkit': None}):
            status, out, err = self.cli(['chat'], ['/exit'])
        self.assertEqual((status, err), (0, ''))
        self.assertIn('LLMTool interactive chat', out)
