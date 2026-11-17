"""#1160 wiring slice step B: telegram_link.run_once with fakes and temp state."""

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
from llmtool import telegram_cursor as cursor  # noqa: E402
from llmtool import telegram_instruction as ti  # noqa: E402
from llmtool import telegram_link as link  # noqa: E402
from llmtool.errors import LLMToolError  # noqa: E402

NOW = 1_700_000_000
ALIAS = 'owner'
TASK_ID = 'a' * 32
_DEFAULT = object()
_MISSING = object()


def message(update_id, text=_MISSING, *, chat_id=4242, from_id=None,
            extra=None):
    body = {
        'message_id': update_id,
        'date': NOW,
        'chat': {'id': chat_id, 'type': 'private'},
        'from': {'id': chat_id if from_id is None else from_id,
                 'is_bot': False},
    }
    if text is not _MISSING:
        body['text'] = text
    if extra:
        body.update(extra)
    return {'update_id': update_id, 'message': body}


class FakeBotApi:
    """Record (method, params) and return synthetic Bot API replies."""

    def __init__(self, updates=None):
        self.updates = [] if updates is None else updates
        self.calls = []

    def __call__(self, method, params):
        self.calls.append((method, params))
        if method == 'getUpdates':
            return {'ok': True, 'result': self.updates}
        if method == 'sendMessage':
            return {'ok': True, 'result': {'message_id': 1}}
        raise AssertionError('unexpected method')


def _holds_identity(value, target):
    if value is target:
        return True
    if isinstance(value, dict):
        return any(_holds_identity(key, target)
                   or _holds_identity(item, target)
                   for key, item in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_holds_identity(item, target) for item in value)
    return False


def _holds_value(value, target):
    if type(value) is type(target) and value == target:
        return True
    if isinstance(value, dict):
        return any(_holds_value(key, target) or _holds_value(item, target)
                   for key, item in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_holds_value(item, target) for item in value)
    return False


def _import_names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
                names.add(alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
                names.add(node.module.split('.')[0])
            for alias in node.names:
                names.add(alias.name)
    return names


class LinkTest(unittest.TestCase):
    def setUp(self):
        self.directory = os.path.realpath(tempfile.mkdtemp())
        os.chmod(self.directory, 0o700)
        self.cursor_path = os.path.join(self.directory, 'cursor.json')
        self.inbox_path = os.path.join(self.directory, 'inbox.json')
        cursor.init_state(self.cursor_path)
        inbox.init_state(self.inbox_path)
        self.limiter = ti.RateLimiter()
        self.sentinel = object()

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def resolve(self, alias):
        return self.sentinel

    def run_once(self, http_call, *, status_reader=_DEFAULT,
                 resolve_chat=_DEFAULT, is_allowed=_DEFAULT):
        return link.run_once(
            http_call,
            self.resolve if resolve_chat is _DEFAULT else resolve_chat,
            alias=ALIAS,
            is_allowed=(lambda update: True) if is_allowed is _DEFAULT
            else is_allowed,
            cursor_path=self.cursor_path,
            inbox_path=self.inbox_path,
            clock=lambda: NOW,
            limiter=self.limiter,
            status_reader=((lambda: {'tasks': []})
                           if status_reader is _DEFAULT else status_reader))

    def methods(self, fake):
        return [method for method, _params in fake.calls]


class RunOnceTests(LinkTest):
    def test_accepted_inbox_update_is_handled_and_replied_without_leaks(self):
        fake = FakeBotApi([message(501, '/inbox')])
        result = self.run_once(fake)
        self.assertEqual(result['poll'],
                         {'status': 'OK', 'received': 1, 'handled': 1,
                          'replied': 1, 'ignored': 0})
        self.assertEqual(self.methods(fake), ['getUpdates', 'sendMessage'])
        get_params = fake.calls[0][1]
        send_params = fake.calls[1][1]
        self.assertIs(send_params['chat_id'], self.sentinel)
        self.assertEqual(send_params['text'], 'EMPTY')
        self.assertFalse(_holds_identity(get_params, self.sentinel))
        self.assertFalse(_holds_identity(result, self.sentinel))
        self.assertFalse(_holds_value(result, 4242))
        self.assertFalse(_holds_value(result, '/inbox'))

    def test_identity_gate_refusals_store_nothing_and_reply_to_nothing(self):
        forwarded = message(601, '/inbox',
                            extra={'forward_origin': {'type': 'user'}})
        refused = message(602, '/inbox')
        fake = FakeBotApi([forwarded, refused])
        result = self.run_once(
            fake, is_allowed=lambda update: update['update_id'] != 602)
        self.assertEqual(result['poll'],
                         {'status': 'OK', 'received': 2, 'handled': 0,
                          'replied': 0, 'ignored': 2})
        self.assertEqual(self.methods(fake), ['getUpdates'])
        self.assertEqual(inbox.status(self.inbox_path, ALIAS, NOW), [])
        self.assertEqual(cursor.offset(self.cursor_path, ALIAS), 602)

    def test_blocked_task_notifies_once_then_stays_quiet(self):
        fake = FakeBotApi()
        status = {'tasks': [{'task_id': TASK_ID, 'state': 'BLOCKED'}]}

        first = self.run_once(fake, status_reader=lambda: status)
        self.assertEqual(first['notify']['status'], 'SENT')
        self.assertEqual(first['notify']['sent'], 1)
        self.assertEqual(self.methods(fake), ['getUpdates', 'sendMessage'])
        self.assertIs(fake.calls[1][1]['chat_id'], self.sentinel)

        second = self.run_once(fake, status_reader=lambda: status)
        self.assertEqual(second['notify']['status'], 'IDLE')
        self.assertEqual(second['notify']['sent'], 0)
        self.assertEqual(self.methods(fake),
                         ['getUpdates', 'sendMessage', 'getUpdates'])

    def test_raising_status_reader_is_unavailable_without_send(self):
        fake = FakeBotApi()

        def status_reader():
            raise RuntimeError('status unavailable')

        result = self.run_once(fake, status_reader=status_reader)
        self.assertEqual(result['notify'],
                         {'status': 'HOST_STORE_UNAVAILABLE', 'sent': 0})
        self.assertEqual(self.methods(fake), ['getUpdates'])

    def test_non_callable_arguments_raise_the_fixed_code(self):
        fake = FakeBotApi()

        def status_reader():
            return {'tasks': []}

        cases = {
            'http_call': (None, self.resolve, status_reader),
            'resolve_chat': (fake, None, status_reader),
            'status_reader': (fake, self.resolve, None),
        }
        for name, (http_call, resolve_chat, reader) in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(LLMToolError) as caught:
                    link.run_once(
                        http_call, resolve_chat, alias=ALIAS,
                        is_allowed=lambda update: True,
                        cursor_path=self.cursor_path,
                        inbox_path=self.inbox_path, clock=lambda: NOW,
                        limiter=self.limiter, status_reader=reader)
                self.assertEqual(str(caught.exception),
                                 'TELEGRAM_LINK_ARGUMENT_INVALID')
        self.assertEqual(fake.calls, [])


class ModuleRuleTests(unittest.TestCase):
    def test_import_boundaries(self):
        link_tree = ast.parse(
            Path(link.__file__).read_text(encoding='utf-8'))
        cli_tree = ast.parse(
            Path(ROOT, 'llmtool', 'cli.py').read_text(encoding='utf-8'))
        self.assertFalse(_import_names(link_tree)
                         & {'socket', 'http', 'urllib', 'subprocess'})
        self.assertNotIn('telegram_link', _import_names(cli_tree))


if __name__ == '__main__':
    unittest.main()
