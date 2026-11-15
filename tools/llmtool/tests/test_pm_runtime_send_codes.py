"""#1230 refusal-code pin: PMRuntime.send closed-runtime and idempotency-key codes. No process starts."""
import sys
import tempfile
import unittest

from llmtool import pm_runtime
from llmtool.errors import LLMToolError

SESSION = '12345678-1234-4123-8123-123456789abc'


class PmRuntimeSendCodeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir='/private/tmp' if sys.platform == 'darwin' else None)
        self.addCleanup(tmp.cleanup)
        self.runtime = pm_runtime.PMRuntime(SESSION, cwd=tmp.name, runner=lambda *args: self.fail('no process'))

    def code(self, *args, **kwargs):
        with self.assertRaises(LLMToolError) as caught:
            self.runtime.send(*args, **kwargs)
        return caught.exception.pm_runtime_code

    def test_send_before_open_or_after_close_is_runtime_closed(self):
        self.assertEqual(self.code('hello'), 'PM_RUNTIME_CLOSED')
        self.runtime._child = object()
        self.runtime._closed = True
        self.assertEqual(self.code('hello'), 'PM_RUNTIME_CLOSED')

    def test_bad_idempotency_key_is_invalid(self):
        self.runtime._child = object()
        for key in ('', 'bad\x00key', 'bad\u200bkey', 'k' * 257, 7):
            with self.subTest(key=key):
                self.assertEqual(self.code('hello', idempotency_key=key), 'PM_IDEMPOTENCY_KEY_INVALID')


if __name__ == '__main__':
    unittest.main()
