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

if __name__ == '__main__':
    unittest.main()
