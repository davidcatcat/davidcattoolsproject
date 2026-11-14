"""#1946: a failed Codex Reviewer exit names no stderr text in its refusal."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import review_dispatch
from llmtool.errors import LLMToolError

FIXED = 'Codex Reviewer exited unsuccessfully; details omitted'


class CodexExitMessageTests(unittest.TestCase):
    def refuse(self, stderr, returncode=1):
        result = SimpleNamespace(returncode=returncode, stdout=b'', stderr=stderr)
        with patch.object(review_dispatch.shutil, 'which', return_value='/bin/codex'), \
                patch.object(review_dispatch.subprocess, 'run', return_value=result), \
                self.assertRaises(LLMToolError) as raised:
            review_dispatch._dispatch_codex(
                reviewer_root='/repo/reviewer-d6', prompt='review prompt',
                task={'task_id': 'a' * 32}, head='b' * 40)
        self.assertEqual(raised.exception.review_dispatch_code, 'REVIEW_NOT_POSTED')
        return raised.exception

    def test_stderr_text_never_reaches_the_message(self):
        for stderr in (b'synthetic-marker stderr line', b'synthetic-marker\nsecond line',
                       'synthetic-marker \u754c'.encode('utf-8'), b'\xffsynthetic-marker'):
            with self.subTest(stderr=stderr):
                exc = self.refuse(stderr)
                self.assertEqual(str(exc), FIXED)
                self.assertNotIn('synthetic', str(exc))
                self.assertNotIn('synthetic', repr(exc.args))

    def test_empty_or_missing_stderr_gives_the_same_fixed_message(self):
        for stderr in (b'', b'   \n', None, 'not bytes'):
            with self.subTest(stderr=stderr):
                self.assertEqual(str(self.refuse(stderr, returncode=2)), FIXED)


if __name__ == '__main__':
    unittest.main()
