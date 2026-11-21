"""#2281 row 12: the Claude CLI review envelope limit follows the policy key
claude_review_envelope_limit_bytes (A1 absent -> unchanged, A2 configured
value applies, A3 invalid or oversized -> POLICY_INVALID). Real policy loader
and decoder on a temp policy file; screen mocked; no network, no Claude process."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from llmtool import repair_policy as rp
from llmtool.errors import LLMToolError
from llmtool.providers import claude_cli

BASE = json.loads((Path(__file__).resolve().parent.parent / 'policy.json').read_text('utf-8'))
KEY = 'claude_review_envelope_limit_bytes'
CAP = claude_cli.REVIEW_ENVELOPE_LIMIT_MAX
DEFAULT = claude_cli.REVIEW_ENVELOPE_LIMIT
SESSION = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'
MISSING = {k: v for k, v in BASE.items() if k != 'review_fail_max'}


def envelope(size):
    """A valid envelope padded with trailing whitespace to exactly size bytes."""
    text = json.dumps(dict(type='result', subtype='success', is_error=False,
                           session_id=SESSION, result='{}'))
    return (text + ' ' * (size - len(text))).encode('utf-8')


class ClaudeEnvelopePolicyTest(unittest.TestCase):
    def policy(self, policy):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'policy.json'
        path.write_bytes(policy if type(policy) is bytes else json.dumps(policy).encode('utf-8'))
        patches = (mock.patch.object(rp, 'policy_path', return_value=str(path)),
                   mock.patch('llmtool.task_review.screen', lambda value: None))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def code(self, raw):
        with self.assertRaises(LLMToolError) as caught:
            claude_cli.decode_review_envelope(raw)
        return caught.exception.review_code

    def test_decoder_policy_boundaries(self):
        self.assertNotIn(KEY, BASE)
        for policy, limit in ((BASE, DEFAULT), (dict(BASE, **{KEY: 2048}), 2048),
                              (dict(BASE, **{KEY: 2 * 1024 * 1024}), 2 * 1024 * 1024)):
            with self.subTest(limit=limit):
                self.policy(policy)
                self.assertEqual(claude_cli._review_envelope_limit(), limit)
                result = claude_cli.decode_review_envelope(envelope(limit))
                self.assertEqual(result['runtime_session_id'], SESSION)
                self.assertEqual(self.code(envelope(limit + 1)), 'REVIEW_WORKER_ENVELOPE_INVALID')
        self.policy(dict(BASE, **{KEY: 2048}))
        self.assertEqual(claude_cli.decode_review_envelope(envelope(1500))['content'], '{}')
        self.policy(dict(BASE, **{KEY: 1024}))
        self.assertEqual(self.code(envelope(1500)), 'REVIEW_WORKER_ENVELOPE_INVALID')

    def test_invalid_policy_has_exact_decoder_code(self):
        for policy in (dict(BASE, **{KEY: 0}), dict(BASE, **{KEY: -1}), dict(BASE, **{KEY: True}),
                       dict(BASE, **{KEY: '2048'}), dict(BASE, **{KEY: CAP + 1}),
                       dict(BASE, **{KEY: 2 ** 62}), MISSING):
            with self.subTest(policy=policy.get(KEY)):
                self.policy(policy)
                with self.assertRaises(LLMToolError):
                    claude_cli._review_envelope_limit()
                self.assertEqual(self.code(envelope(512)), 'POLICY_INVALID')

    def test_parser_recursion_is_policy_invalid(self):
        # Formal v4 F1: the reviewer's file repro, plus RecursionError injected
        # at the loader only (CPython may parse the file without recursing).
        self.policy(b'[' * 1100 + b']' * 1100)
        for patch in (mock.patch.object(rp, 'policy_path', wraps=rp.policy_path),
                      mock.patch.object(rp, 'load_policy', side_effect=RecursionError)):
            with self.subTest(patch=patch.attribute), patch:
                with self.assertRaises(LLMToolError):
                    claude_cli._review_envelope_limit()
                self.assertEqual(self.code(envelope(512)), 'POLICY_INVALID')

    def test_read_pipe_honors_configured_and_generic_limits(self):
        payload = b'x' * (claude_cli.OUTPUT_LIMIT + claude_cli.OUTPUT_LIMIT // 2)
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / 'out.bin'
            src.write_bytes(payload)
            for limit, expected in ((2 * 1024 * 1024, True), (None, 'limit')):
                with self.subTest(limit=limit), open(src, 'rb') as stream:
                    out, result = bytearray(), False
                    while not result:
                        result = claude_cli.ClaudeCLIProvider._read_pipe(stream, out, limit)
                    self.assertEqual(result, expected)
                    if expected is True:
                        self.assertEqual(bytes(out), payload)


if __name__ == '__main__':
    unittest.main()
