"""#2281 row 12 (basis #131, #165): the Claude CLI provider's default review
envelope limit follows the policy key claude_review_envelope_limit_bytes
(A1 absent -> unchanged, A2 configured value applies, A3 invalid ->
POLICY_INVALID). Screen mocked; temp policy file; no network, no Claude."""

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


class ClaudeEnvelopePolicyTest(unittest.TestCase):
    KEY = 'claude_review_envelope_limit_bytes'
    ENVELOPE_OK = (b'{"type": "result", "subtype": "success", "is_error": false, '
                   b'"session_id": "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11", '
                   b'"result": "ok"}')
    ENVELOPE_BIG = (b'{"type": "result", "subtype": "success", "is_error": false, '
                    b'"session_id": "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11", '
                    b'"result": "' + b'z' * 2000 + b'"}')
    def _write(self, tmpdir, policy):
        path = Path(tmpdir) / 'policy.json'
        path.write_bytes(json.dumps(policy).encode('utf-8'))
        return str(path)
    def _apply(self, policy_path, raw):
        with mock.patch.object(claude_cli, '_review_envelope_limit',
                               lambda: rp.claude_review_envelope_limit_bytes(policy_path)), \
                mock.patch('llmtool.task_review.screen', lambda value: None):
            return claude_cli.decode_review_envelope(raw)
    def _refusal(self, call, raw):
        try:
            call(raw)
        except LLMToolError as exc:
            return str(exc)
        self.fail('no refusal was raised')
    def test_default_limit_unchanged_when_key_absent(self):
        self.assertEqual(rp.claude_review_envelope_limit_bytes(str(REPO_POLICY)),
                         claude_cli.REVIEW_ENVELOPE_LIMIT)
    def test_configured_limit_applies(self):
        limit = 2048
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write(tmpdir, dict(BASE, **{self.KEY: limit}))
            self.assertEqual(rp.claude_review_envelope_limit_bytes(path), limit)
            self.assertEqual(self._apply(path, self.ENVELOPE_OK)['content'], 'ok')
            self.assertIn(self._refusal(
                lambda raw: self._apply(path, raw), self.ENVELOPE_BIG),
                ('REVIEW_WORKER_OUTPUT_LIMIT', 'REVIEW_WORKER_ENVELOPE_INVALID'))
    def test_invalid_policy_refuses(self):
        missing = dict(BASE)
        del missing['review_fail_max']
        with tempfile.TemporaryDirectory() as tmpdir:
            for policy in (dict(BASE, **{self.KEY: 0}), missing):
                self.assertEqual(self._refusal(
                    lambda raw: self._apply(self._write(tmpdir, policy), raw),
                    self.ENVELOPE_OK), 'POLICY_INVALID')


if __name__ == '__main__':
    unittest.main()
