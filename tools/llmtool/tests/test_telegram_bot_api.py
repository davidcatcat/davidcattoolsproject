"""#1160: telegram_bot_api.BotApiTransport over injected fakes; no network."""
import unittest

from llmtool import telegram_bot_api as api
from llmtool.errors import LLMToolError

CHAT = 'opaque-chat-value'
SECRET_TEXT = 'provider says something private'


class FakeHttp:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def __call__(self, method, params):
        self.calls.append((method, params))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def resolve(alias):
    return CHAT if alias == 'owner' else None


class BotApiTransportTests(unittest.TestCase):
    def transport(self, reply):
        http = FakeHttp(reply)
        return api.BotApiTransport(http, resolve), http

    def refused(self, code, call, *args, **kwargs):
        with self.assertRaises(LLMToolError) as caught:
            call(*args, **kwargs)
        self.assertEqual(str(caught.exception), code)
        self.assertNotIn(CHAT, str(caught.exception))
        self.assertNotIn(SECRET_TEXT, str(caught.exception))

    def test_get_updates_params_and_result(self):
        updates = [{'update_id': 5}]
        transport, http = self.transport({'ok': True, 'result': updates})
        result = transport.get_updates(offset=5, allowed_updates=['message'], limit=20)
        self.assertEqual(result, updates)
        params = {'offset': 5, 'allowed_updates': ['message'], 'limit': 20, 'timeout': 0}
        self.assertEqual(http.calls, [('getUpdates', params)])

    def test_get_updates_bad_arguments_refuse_before_any_call(self):
        cases = [(-1, ['message'], 20), (True, ['message'], 20), (1, ['edited_message'], 20)]
        cases += [(1, ['message'], 0), (1, ['message'], 101), (1, 'message', 20)]
        for offset, allowed, limit in cases:
            with self.subTest(offset=offset, allowed=allowed, limit=limit):
                transport, http = self.transport({'ok': True, 'result': []})
                kwargs = dict(offset=offset, allowed_updates=allowed, limit=limit)
                self.refused(api.ARGUMENT_INVALID, transport.get_updates, **kwargs)
                self.assertEqual(http.calls, [])

    def test_get_updates_bad_replies_refuse(self):
        bad = {'ok': False, 'error_code': 400, 'description': SECRET_TEXT}
        limited = {'ok': False, 'error_code': 429, 'description': SECRET_TEXT}
        cases = [(['not', 'a', 'dict'], api.REPLY_INVALID)]
        cases += [({'result': []}, api.REPLY_INVALID), ({'ok': 1}, api.REPLY_INVALID)]
        cases += [({'ok': True}, api.REPLY_INVALID)]
        cases += [({'ok': True, 'result': {}}, api.REPLY_INVALID)]
        cases += [(bad, api.REPLY_INVALID), (limited, api.RATE_LIMITED)]
        cases += [(OSError(SECRET_TEXT), api.CALL_FAILED)]
        for reply, code in cases:
            with self.subTest(code=code, reply=type(reply).__name__):
                transport, _http = self.transport(reply)
                kwargs = dict(offset=1, allowed_updates=['message'], limit=1)
                self.refused(code, transport.get_updates, **kwargs)

    def test_send_outcomes(self):
        limited = {'ok': False, 'error_code': 429, 'parameters': {'retry_after': 7}}
        cases = [({'ok': True, 'result': {'message_id': 1}}, {'status': 'SENT'})]
        refused = {'ok': False, 'error_code': 403, 'description': SECRET_TEXT}
        cases += [(refused, {'status': 'FAILED'})]
        cases += [(limited, {'status': 'RATE_LIMITED', 'retry_after': 7})]
        cases += [({'ok': False, 'error_code': 429}, {'status': 'FAILED'})]
        for reply, expected in cases:
            with self.subTest(expected=expected):
                transport, http = self.transport(reply)
                self.assertEqual(transport.send('owner', 'hello'), expected)
                self.assertEqual(http.calls, [('sendMessage', {'chat_id': CHAT, 'text': 'hello'})])

    def test_send_never_returns_or_leaks_the_chat_value(self):
        transport, _http = self.transport({'ok': True, 'result': {'message_id': 1}})
        self.assertNotIn(CHAT, repr(transport.send('owner', 'hello')))
        self.assertNotIn(CHAT, repr(vars(transport)))

    def test_send_unknown_alias_is_failed_without_a_call(self):
        transport, http = self.transport({'ok': True, 'result': {}})
        self.assertEqual(transport.send('stranger', 'hello'), {'status': 'FAILED'})
        self.assertEqual(http.calls, [])

    def test_send_bad_arguments_and_unreadable_replies_refuse(self):
        transport, http = self.transport({'ok': True, 'result': {}})
        for alias, text in (('', 'x'), ('owner', ''), ('owner', 'x' * 4097), ('owner', None)):
            with self.subTest(alias=alias):
                self.refused(api.ARGUMENT_INVALID, transport.send, alias, text)
        self.assertEqual(http.calls, [])
        for reply, code in ((OSError(SECRET_TEXT), api.CALL_FAILED), ('x', api.REPLY_INVALID)):
            with self.subTest(code=code):
                bad_transport, _http = self.transport(reply)
                self.refused(code, bad_transport.send, 'owner', 'hello')
        self.refused(api.REPLY_INVALID, self.transport({'ok': True})[0].send, 'owner', 'hello')

    def test_constructor_needs_callables(self):
        self.refused(api.ARGUMENT_INVALID, api.BotApiTransport, None, resolve)
        self.refused(api.ARGUMENT_INVALID, api.BotApiTransport, FakeHttp({}), 'x')


if __name__ == '__main__':
    unittest.main()
