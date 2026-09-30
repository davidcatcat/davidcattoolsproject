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
        for error in (LLMToolError('PRIVATE'), OSError('PRIVATE'), UnicodeError('PRIVATE')):
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
        self.assertIn('chat request failed', result[2])

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
