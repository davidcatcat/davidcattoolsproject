"""#2281 row 12: the Claude CLI review envelope limit follows the policy key
claude_review_envelope_limit_bytes (A1 absent -> unchanged, A2 configured
value applies, A3 invalid or oversized -> POLICY_INVALID). Screen mocked;
temp policy file; no network, no Claude process."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from llmtool import repair_policy as rp
from llmtool.errors import LLMToolError
from llmtool.providers import claude_cli

REPO_POLICY = Path(__file__).resolve().parent.parent / 'policy.json'
BASE = json.loads(REPO_POLICY.read_text('utf-8'))
KEY = 'claude_review_envelope_limit_bytes'


class ClaudeEnvelopePolicyTest(unittest.TestCase):
    def _limit(self, policy):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'policy.json'
            path.write_text(json.dumps(policy), 'utf-8')
            with mock.patch.object(rp, 'policy_path', return_value=str(path)):
                return claude_cli._review_envelope_limit()

    def test_default_unchanged_when_key_absent(self):
        self.assertNotIn(KEY, BASE)
        self.assertEqual(self._limit(BASE), claude_cli.REVIEW_ENVELOPE_LIMIT)

    def test_configured_limit_applies(self):
        self.assertEqual(self._limit(dict(BASE, **{KEY: 2048})), 2048)
        cap = claude_cli.REVIEW_ENVELOPE_LIMIT_MAX
        self.assertEqual(self._limit(dict(BASE, **{KEY: cap})), cap)

    def test_invalid_or_oversized_policy_refuses(self):
        cap = claude_cli.REVIEW_ENVELOPE_LIMIT_MAX
        missing = {k: v for k, v in BASE.items() if k != 'review_fail_max'}
        for policy in (dict(BASE, **{KEY: 0}), dict(BASE, **{KEY: cap + 1}),
                       dict(BASE, **{KEY: 2 ** 62}), missing):
            with self.assertRaises(LLMToolError):
                self._limit(policy)

    def test_decoder_refuses_before_reading_on_invalid_policy(self):
        with mock.patch.object(claude_cli, '_review_envelope_limit',
                               side_effect=LLMToolError('POLICY_INVALID')), \
                mock.patch('llmtool.task_review.screen', lambda value: None):
            with self.assertRaises(LLMToolError):
                claude_cli.decode_review_envelope(b'{}')

    def test_read_pipe_uses_per_call_limit(self):
        payload = b'x' * (claude_cli.OUTPUT_LIMIT + 1024)
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / 'out.bin'
            src.write_bytes(payload)
            with open(src, 'rb') as stream:
                out, result = bytearray(), None
                while not result:
                    result = claude_cli.ClaudeCLIProvider._read_pipe(
                        stream, out, claude_cli.OUTPUT_LIMIT)
            self.assertEqual(result, 'limit')


if __name__ == '__main__':
    unittest.main()
