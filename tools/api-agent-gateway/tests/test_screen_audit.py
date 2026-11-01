"""Screening patterns and audit chain integrity."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from helpers import FAKE_API_KEY, FAKE_AWS, FAKE_GH, PEM_BODY, PEM_HEAD, PEM_TAIL
from api_agent_gateway.audit import AuditError, AuditLog, verify
from api_agent_gateway.screen import PLACEHOLDER, Screener


class ScreenTests(unittest.TestCase):
    def setUp(self):
        self.s = Screener([FAKE_API_KEY])

    def test_token_shapes_redacted(self):
        jwt = 'ey' + 'JhbGciOiJIUzI1NiJ9.' + 'eyJzdWIiOiIxMjM0In0.' + 'abcdefghijklmnop'
        for secret in (FAKE_AWS, FAKE_GH, 's' + 'k-' + 'A1b2C3d4E5f6G7h8', 'xo' + 'xb-' + '1' * 24, jwt,
                       FAKE_API_KEY):
            with self.subTest(secret=secret[:6]):
                out, n = self.s.text(f'value = {secret} end')
                self.assertNotIn(secret, out)
                self.assertIn(PLACEHOLDER, out)
                self.assertGreaterEqual(n, 1)

    def test_private_key_block_and_partial(self):
        block = f'{PEM_HEAD}\n{PEM_BODY}\n{PEM_BODY}\n{PEM_TAIL}\nafter'
        out, _ = self.s.text(block)
        self.assertNotIn(PEM_BODY, out)
        self.assertTrue(out.endswith('after'))
        out, _ = self.s.text(f'{PEM_HEAD}\n{PEM_BODY}\n')  # no END marker
        self.assertNotIn(PEM_BODY, out)
        out, _ = self.s.text(f'line\n{PEM_BODY}\n{PEM_TAIL}\n')  # range started mid-key
        self.assertNotIn(PEM_BODY, out)

    def test_assignment_and_bearer(self):
        out, _ = self.s.text('password = "correct-horse-battery"\nAuthorization: Bearer abcdefgh12345678')
        self.assertNotIn('correct-horse-battery', out)
        self.assertNotIn('abcdefgh12345678', out)

    def test_code_left_alone(self):
        code = 'token = get_token()\nsha = "' + 'a' * 64 + '"\npassword = os.environ["X"]\n'
        self.assertEqual(self.s.text(code), (code, 0))

    def test_nested_obj_and_residual(self):
        obj = {'messages': [{'content': f'x {FAKE_GH}'}, {'tool_calls': [{'arguments': FAKE_AWS}]}]}
        out, n = self.s.obj(obj)
        self.assertEqual(n, 2)
        self.assertFalse(self.s.residual(json.dumps(out)))
        self.assertTrue(self.s.residual(json.dumps(obj)))


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='gwagent-audit-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, 'a.jsonl')

    def make(self):
        log = AuditLog(self.path)
        log.append('header', run_id='r')
        log.append('model_call', turn=1)
        last = log.append('end', status='DONE')
        log.close()
        return last

    def test_chain_and_mode(self):
        last = self.make()
        self.assertEqual(verify(self.path), last)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_exclusive_create_and_no_follow(self):
        self.make()
        with self.assertRaises(AuditError):
            AuditLog(self.path)
        link = os.path.join(self.tmp, 'link.jsonl')
        os.symlink(os.path.join(self.tmp, 'target'), link)
        with self.assertRaises(AuditError):
            AuditLog(link)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'target')))

    def test_tamper_detected(self):
        self.make()
        with open(self.path) as fh:
            lines = fh.readlines()
        variants = {
            'edit': [lines[0], lines[1].replace('"turn":1', '"turn":2'), lines[2]],
            'drop': [lines[0], lines[2]],
            'reorder': [lines[1], lines[0], lines[2]],
            'truncate': [lines[0], lines[1], lines[2][:-1]],
        }
        for name, content in variants.items():
            with self.subTest(name=name):
                bad = os.path.join(self.tmp, f'{name}.jsonl')
                with open(bad, 'w') as fh:
                    fh.writelines(content)
                with self.assertRaises(AuditError):
                    verify(bad)


if __name__ == '__main__':
    unittest.main()
