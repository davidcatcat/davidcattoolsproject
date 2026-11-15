"""#1160 PR 3: Telegram instruction gates and Owner inbox commands, fake transport only."""

import ast
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llmtool import owner_instruction_inbox as inbox  # noqa: E402
from llmtool import telegram_channel as tc  # noqa: E402
from llmtool import telegram_cursor as cursor  # noqa: E402
from llmtool import telegram_instruction as ti  # noqa: E402
from llmtool.errors import LLMToolError  # noqa: E402

NOW = 1_700_000_000
ALIAS = 'owner'
_MISSING = object()


def message(update_id, text=_MISSING, *, date=NOW, chat_id=7, from_id=None,
            chat_type='private', is_bot=False, extra=None):
    body = {
        'message_id': update_id,
        'date': date,
        'chat': {'id': chat_id, 'type': chat_type},
        'from': {'id': chat_id if from_id is None else from_id,
                 'is_bot': is_bot},
    }
    if text is not _MISSING:
        body['text'] = text
    if extra:
        body.update(extra)
    return {'update_id': update_id, 'message': body}


def allowed_update(update):
    return True


class FakeTransport:
    """Records getUpdates and send calls; no network."""

    def __init__(self, updates=None, fail_get=False, fail_send_after=None):
        self.updates = updates
        self.fail_get = fail_get
        self.fail_send_after = fail_send_after
        self.get_calls = []
        self.sent = []

    def get_updates(self, offset, allowed_updates, limit):
        self.get_calls.append({'offset': offset,
                               'allowed_updates': allowed_updates,
                               'limit': limit})
        if self.fail_get:
            raise OSError('offline')
        return self.updates

    def send(self, alias, text):
        if (self.fail_send_after is not None
                and len(self.sent) >= self.fail_send_after):
            raise OSError('send failed')
        self.sent.append((alias, text))


class InstructionTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        os.chmod(self.directory, 0o700)
        self.cursor_path = os.path.join(self.directory, 'cursor.json')
        self.inbox_path = os.path.join(self.directory, 'inbox.json')
        cursor.init_state(self.cursor_path)
        inbox.init_state(self.inbox_path)
        self.limiter = ti.RateLimiter()

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def run_update(self, update, *, allowed=allowed_update, limiter=None,
                   now=NOW):
        return ti.handle_update(
            update, alias=ALIAS, is_allowed=allowed,
            cursor_path=self.cursor_path, inbox_path=self.inbox_path,
            now=now, limiter=limiter or self.limiter)

    def poll(self, transport, *, clock=lambda: NOW, limiter=None,
             max_updates=20):
        return ti.poll_once(
            transport, alias=ALIAS, is_allowed=allowed_update,
            cursor_path=self.cursor_path, inbox_path=self.inbox_path,
            clock=clock, limiter=limiter or ti.RateLimiter(),
            max_updates=max_updates)

    def offset(self):
        return cursor.offset(self.cursor_path, ALIAS)

    def stored(self):
        return inbox.status(self.inbox_path, ALIAS, NOW)


class GateTests(InstructionTest):
    def test_invalid_update_ignored_without_write(self):
        for bad in (None, [], 'update', 5, {}, {'update_id': '5'},
                    {'update_id': True}, {'update_id': 5.0},
                    {'update_id': -1}, {'update_id': 2 ** 63}):
            self.assertEqual(self.run_update(bad),
                             {'outcome': ti.IGNORED_INVALID, 'reply': None})
        self.assertEqual(self.offset(), 0)
        self.assertEqual(self.stored(), [])

    def test_duplicate_update_id_is_inert_before_type_gate(self):
        cursor.advance_offset(self.cursor_path, ALIAS, 5)
        result = self.run_update({'update_id': 5, 'message': 'not-a-dict'})
        self.assertEqual(result,
                         {'outcome': ti.IGNORED_DUPLICATE, 'reply': None})
        self.assertEqual(self.offset(), 5)
        self.assertEqual(self.stored(), [])

    def test_other_update_type_advances_offset(self):
        result = self.run_update({'update_id': 4, 'edited_message': {}})
        self.assertEqual(result, {'outcome': ti.IGNORED_TYPE, 'reply': None})
        self.assertEqual(self.offset(), 4)
        result = self.run_update({'update_id': 5, 'message': 'not-a-dict'})
        self.assertEqual(result, {'outcome': ti.IGNORED_TYPE, 'reply': None})
        self.assertEqual(self.offset(), 5)

    def test_sender_gates_reply_nothing_and_advance(self):
        cases = [
            (10, message(10, '/help', chat_type='group')),
            (11, message(11, '/help', is_bot=True)),
            (12, message(12, '/help', from_id=8)),
            (13, message(13, '/help', extra={'forward_origin': {'type': 'user'}})),
            (14, message(14, '/help', extra={'forward_from': {'id': 7}})),
            (15, message(15, '/help', extra={'forward_from_chat': {'id': 7}})),
            (16, message(16, '/help', extra={'forward_date': NOW})),
            (17, message(17, '/help', extra={'via_bot': {'id': 7}})),
        ]
        for update_id, update in cases:
            result = self.run_update(update)
            self.assertEqual(result,
                             {'outcome': ti.IGNORED_SENDER, 'reply': None})
            self.assertEqual(self.offset(), update_id)
        self.assertEqual(
            self.run_update(message(18, '/help'), allowed=lambda item: False),
            {'outcome': ti.IGNORED_SENDER, 'reply': None})

        def refuse(_update):
            raise RuntimeError('allowlist unavailable')

        self.assertEqual(
            self.run_update(message(19, '/help'), allowed=refuse),
            {'outcome': ti.IGNORED_SENDER, 'reply': None})
        self.assertEqual(self.offset(), 19)
        self.assertEqual(self.stored(), [])

    def test_stale_and_future_edges(self):
        self.assertEqual(
            self.run_update(message(20, '/help', date=NOW - 121)),
            {'outcome': ti.STALE_COMMAND, 'reply': 'STALE_COMMAND'})
        self.assertEqual(
            self.run_update(message(21, '/help', date=NOW - 120)),
            {'outcome': ti.HANDLED, 'reply': ti.HELP_TEXT})
        self.assertEqual(
            self.run_update(message(22, '/help', date=NOW + 31)),
            {'outcome': ti.STALE_COMMAND, 'reply': 'STALE_COMMAND'})
        self.assertEqual(
            self.run_update(message(23, '/help', date=NOW + 30)),
            {'outcome': ti.HANDLED, 'reply': ti.HELP_TEXT})
        self.assertEqual(self.offset(), 23)

    def test_stale_before_rate_and_rate_before_text(self):
        limiter = ti.RateLimiter(max_per_minute=1)
        self.assertEqual(
            self.run_update(message(110, '/help'), limiter=limiter)['outcome'],
            ti.HANDLED)
        self.assertEqual(
            self.run_update(message(111, '/help', date=NOW - 121),
                            limiter=limiter),
            {'outcome': ti.STALE_COMMAND, 'reply': 'STALE_COMMAND'})
        self.assertEqual(
            self.run_update(message(112), limiter=limiter),
            {'outcome': ti.RATE_LIMITED, 'reply': 'RATE_LIMITED'})
        self.assertEqual(
            self.run_update(message(113, '/help', date=NOW - 500,
                                    chat_type='group'), limiter=limiter),
            {'outcome': ti.IGNORED_SENDER, 'reply': None})
        self.assertEqual(self.offset(), 113)

    def test_rate_limit_tenth_allowed_eleventh_refused(self):
        limiter = ti.RateLimiter()
        for index in range(10):
            result = self.run_update(message(30 + index, '/help'),
                                     limiter=limiter)
            self.assertEqual(result, {'outcome': ti.HANDLED,
                                      'reply': ti.HELP_TEXT})
        result = self.run_update(message(40, '/help'), limiter=limiter)
        self.assertEqual(result,
                         {'outcome': ti.RATE_LIMITED, 'reply': 'RATE_LIMITED'})
        self.assertEqual(self.offset(), 40)

    def test_rate_limiter_window(self):
        limiter = ti.RateLimiter(max_per_minute=2)
        self.assertTrue(limiter.allow(ALIAS, NOW))
        self.assertTrue(limiter.allow(ALIAS, NOW))
        self.assertFalse(limiter.allow(ALIAS, NOW))
        self.assertTrue(limiter.allow(ALIAS, NOW + 61))

    def test_media_is_unsupported(self):
        result = self.run_update(message(50))
        self.assertEqual(result,
                         {'outcome': ti.UNSUPPORTED, 'reply': 'UNSUPPORTED'})
        self.assertEqual(self.offset(), 50)
        self.assertEqual(self.stored(), [])

    def test_alias_error_is_fixed_code(self):
        with self.assertRaises(LLMToolError) as caught:
            ti.handle_update(message(1, '/help'), alias='Bad Alias',
                             is_allowed=allowed_update,
                             cursor_path=self.cursor_path,
                             inbox_path=self.inbox_path, now=NOW,
                             limiter=self.limiter)
        self.assertEqual(str(caught.exception),
                         'TELEGRAM_INSTRUCTION_ALIAS_INVALID')


class CommandTests(InstructionTest):
    def test_do_stores_and_replies_queued(self):
        result = self.run_update(message(60, '  /do   hello  world  '))
        code = inbox.instruction_id(ALIAS, 60)[:12]
        self.assertEqual(result,
                         {'outcome': ti.HANDLED, 'reply': code + ' QUEUED'})
        self.assertEqual(self.offset(), 60)
        self.assertEqual(self.stored(), [{'code': code, 'state': 'QUEUED'}])
        items = inbox.peek(self.inbox_path, NOW)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['text'], 'hello  world')

    def test_do_resend_is_inert(self):
        first = self.run_update(message(61, '/do hello'))
        second = self.run_update(message(61, '/do hello'))
        self.assertEqual(first['outcome'], ti.HANDLED)
        self.assertEqual(second,
                         {'outcome': ti.IGNORED_DUPLICATE, 'reply': None})
        self.assertEqual(len(inbox.peek(self.inbox_path, NOW)), 1)
        self.assertEqual(self.offset(), 61)

    def test_do_text_invalid(self):
        for update_id, text in ((62, '/do'), (63, '/do    '),
                                (64, '/do ' + 'x' * 2001)):
            self.assertEqual(self.run_update(message(update_id, text)),
                             {'outcome': ti.HANDLED,
                              'reply': 'DO_TEXT_INVALID'})
        self.assertEqual(self.offset(), 64)
        self.assertEqual(self.stored(), [])

    def test_screened_do_text_is_withheld(self):
        # synthetic-fixture: screen-tool test
        synthetic = '123456789' + ':' + 'A' * 35
        result = self.run_update(message(65, '/do ' + synthetic))
        self.assertEqual(result, {'outcome': ti.CONTENT_WITHHELD,
                                  'reply': 'CONTENT_WITHHELD'})
        self.assertEqual(self.offset(), 65)
        self.assertEqual(self.stored(), [])

    def test_do_disabled_and_full(self):
        inbox.set_enabled(self.inbox_path, False)
        self.assertEqual(self.run_update(message(70, '/do hello')),
                         {'outcome': ti.HANDLED,
                          'reply': 'INBOX_DISABLED'})
        inbox.set_enabled(self.inbox_path, True)
        for offset in range(20):
            inbox.add(self.inbox_path, ALIAS, 1000 + offset, 'queued', NOW)
        self.assertEqual(self.run_update(message(71, '/do hello')),
                         {'outcome': ti.HANDLED, 'reply': 'INBOX_FULL'})
        self.assertEqual(self.offset(), 71)

    def test_do_unavailable_when_state_is_missing(self):
        os.unlink(self.inbox_path)
        self.assertEqual(self.run_update(message(66, '/do hello')),
                         {'outcome': ti.HANDLED,
                          'reply': 'INBOX_UNAVAILABLE'})
        self.assertEqual(self.run_update(message(67, '/inbox')),
                         {'outcome': ti.HANDLED,
                          'reply': 'INBOX_UNAVAILABLE'})
        self.assertEqual(self.offset(), 67)

    def test_inbox_list_show_and_unknown(self):
        self.assertEqual(self.run_update(message(80, '/inbox')),
                         {'outcome': ti.HANDLED, 'reply': 'EMPTY'})
        code_one = inbox.instruction_id(ALIAS, 81)[:12]
        code_two = inbox.instruction_id(ALIAS, 82)[:12]
        self.run_update(message(81, '/do first'))
        self.run_update(message(82, '/do second'))
        listed = code_two + ' QUEUED\n' + code_one + ' QUEUED'
        self.assertEqual(self.run_update(message(83, '/inbox')),
                         {'outcome': ti.HANDLED, 'reply': listed})
        self.assertEqual(self.run_update(message(84, '/inbox ' + code_one)),
                         {'outcome': ti.HANDLED,
                          'reply': code_one + ' QUEUED'})
        self.assertEqual(self.run_update(message(85, '/inbox ' + '0' * 12)),
                         {'outcome': ti.HANDLED, 'reply': 'NOT_FOUND'})
        self.assertEqual(self.run_update(message(86, '/inbox not-a-code')),
                         {'outcome': ti.HANDLED, 'reply': 'CODE_INVALID'})
        self.assertEqual(self.offset(), 86)

    def test_revoke_states_and_bad_code(self):
        code = inbox.instruction_id(ALIAS, 90)[:12]
        self.run_update(message(90, '/do revoke me'))
        self.assertEqual(self.run_update(message(91, '/revoke ' + code)),
                         {'outcome': ti.HANDLED,
                          'reply': code + ' REVOKED'})
        self.assertEqual(self.run_update(message(92, '/inbox ' + code)),
                         {'outcome': ti.HANDLED,
                          'reply': code + ' REVOKED'})
        self.assertEqual(self.run_update(message(93, '/revoke ' + '0' * 12)),
                         {'outcome': ti.HANDLED, 'reply': 'NOT_FOUND'})
        self.assertEqual(self.run_update(message(94, '/revoke nope')),
                         {'outcome': ti.HANDLED, 'reply': 'CODE_INVALID'})
        self.assertEqual(self.offset(), 94)

    def test_revoke_acked_entry_can_be_full(self):
        code = inbox.add(self.inbox_path, ALIAS, 2000, 'queued', NOW)['code']
        for offset in range(1, 20):
            inbox.add(self.inbox_path, ALIAS, 2000 + offset, 'queued', NOW)
        inbox.ack(self.inbox_path, code, NOW)
        inbox.add(self.inbox_path, ALIAS, 2020, 'queued', NOW)
        self.assertEqual(self.run_update(message(95, '/revoke ' + code)),
                         {'outcome': ti.HANDLED, 'reply': 'INBOX_FULL'})

    def test_help_and_unknown_commands(self):
        result = self.run_update(message(100, '/help'))
        self.assertEqual(result, {'outcome': ti.HANDLED,
                                  'reply': ti.HELP_TEXT})
        for word in ('/do', '/inbox', '/revoke', '/help'):
            self.assertIn(word, result['reply'])
        for update_id, text in ((101, '/do@botname hello'),
                                (102, '/inbox@botname'),
                                (103, 'hello world'), (104, '')):
            self.assertEqual(self.run_update(message(update_id, text)),
                             {'outcome': ti.UNKNOWN_COMMAND,
                              'reply': 'UNKNOWN_COMMAND'})
        self.assertEqual(self.offset(), 104)

    def test_every_reply_is_screened(self):
        result = self.run_update(message(120, '/do ordinary note'))
        self.assertEqual(result['reply'],
                         tc.outbound_text(result['reply']))


class PollTests(InstructionTest):
    def test_get_updates_arguments_counts_and_order(self):
        transport = FakeTransport(updates=[message(131, '/help'),
                                           message(130, '/help')])
        self.assertEqual(self.poll(transport),
                         {'status': 'OK', 'received': 2, 'handled': 2,
                          'replied': 2, 'ignored': 0})
        self.assertEqual(transport.get_calls, [{
            'offset': 1, 'allowed_updates': ['message'], 'limit': 20}])
        self.assertEqual([text for _alias, text in transport.sent],
                         [ti.HELP_TEXT, ti.HELP_TEXT])
        self.assertEqual(self.offset(), 131)

    def test_offset_plus_one_and_empty_poll(self):
        cursor.advance_offset(self.cursor_path, ALIAS, 50)
        transport = FakeTransport(updates=[])
        self.assertEqual(self.poll(transport),
                         {'status': 'OK', 'received': 0, 'handled': 0,
                          'replied': 0, 'ignored': 0})
        self.assertEqual(transport.get_calls[0]['offset'], 51)
        self.assertEqual(self.offset(), 50)

    def test_offline_on_exception_and_non_list_without_write(self):
        transport = FakeTransport(updates=[], fail_get=True)
        self.assertEqual(self.poll(transport),
                         {'status': 'OFFLINE', 'received': 0, 'handled': 0,
                          'replied': 0, 'ignored': 0})
        self.assertEqual(self.offset(), 0)
        self.assertEqual(transport.sent, [])
        transport = FakeTransport(updates='not-a-list')
        self.assertEqual(self.poll(transport)['status'], 'OFFLINE')
        self.assertEqual(self.offset(), 0)

    def test_send_exception_stops_unknown(self):
        transport = FakeTransport(updates=[message(140, '/help'),
                                           message(141, '/help')],
                                  fail_send_after=1)
        self.assertEqual(self.poll(transport),
                         {'status': 'UNKNOWN', 'received': 2, 'handled': 2,
                          'replied': 1, 'ignored': 0})
        self.assertEqual(len(transport.sent), 1)
        self.assertEqual(self.offset(), 141)

    def test_invalid_update_counted_as_ignored(self):
        transport = FakeTransport(updates=[message(150, '/help'),
                                           {'update_id': 'bad'},
                                           message(151, '/help')])
        self.assertEqual(self.poll(transport),
                         {'status': 'OK', 'received': 3, 'handled': 2,
                          'replied': 2, 'ignored': 1})
        self.assertEqual(self.offset(), 151)

    def test_sends_never_hold_text_or_ids(self):
        text = 'queue this ordinary note'
        transport = FakeTransport(updates=[message(160, '/do ' + text,
                                                   chat_id=424242)])
        result = self.poll(transport)
        self.assertEqual(result, {'status': 'OK', 'received': 1, 'handled': 1,
                                  'replied': 1, 'ignored': 0})
        self.assertEqual(len(transport.sent), 1)
        alias, sent = transport.sent[0]
        self.assertEqual(alias, ALIAS)
        self.assertEqual(sent, inbox.instruction_id(ALIAS, 160)[:12] + ' QUEUED')
        self.assertNotIn(text, sent)
        self.assertRegex(sent, r'^[0-9a-f]{12} QUEUED$')

    def test_max_updates_bounds(self):
        for value in (0, 101, -1, True, '20'):
            with self.assertRaises(LLMToolError) as caught:
                self.poll(FakeTransport(updates=[]), max_updates=value)
            self.assertEqual(str(caught.exception),
                             'TELEGRAM_INSTRUCTION_MAX_UPDATES_INVALID')


class ModuleRuleTests(unittest.TestCase):
    def setUp(self):
        self.source = Path(ti.__file__).read_text(encoding='utf-8')
        self.tree = ast.parse(self.source)

    def test_imports_are_stdlib_and_sibling_modules_only(self):
        allowed = {'errors', 'owner_instruction_inbox', 'telegram_channel',
                   'telegram_cursor'}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertEqual(alias.name, 're')
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 1)
                self.assertIn(node.module, allowed | {None})

    def test_no_forbidden_import_or_print(self):
        imported = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split('.')[0]
                                for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split('.')[0])
        self.assertFalse(imported & {'socket', 'urllib', 'http', 'subprocess',
                                     'logging'})
        calls = [node for node in ast.walk(self.tree)
                 if isinstance(node, ast.Call)]
        self.assertFalse(any(isinstance(call.func, ast.Name)
                             and call.func.id == 'print' for call in calls))

    def test_docstring_states_the_limits(self):
        doc = ast.get_docstring(self.tree)
        for phrase in ('Git', 'shell', 'bot token', 'data for the PM'):
            self.assertIn(phrase, doc)


if __name__ == '__main__':
    unittest.main()
