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
from llmtool import task_review
from llmtool.errors import LLMToolError
from llmtool.providers import claude_cli

REPO_POLICY = Path(__file__).resolve().parent.parent / 'policy.json'
BASE = json.loads(REPO_POLICY.read_text('utf-8'))
# Synthetic fixture value (labelled synthetic per #981; not a real session
# id): the nil UUID is a valid UUID, so the decoder's
# str(uuid.UUID(value['session_id'])) != value['session_id'] check passes.
_SESSION = '00000000-0000-0000-0000-000000000000'


def _envelope(result):
    # Outer Claude SDK result/success envelope; the inner ``result`` is itself
    # a JSON document (decode_review_envelope runs load_json(value['result'])).
    return json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False,
                       'session_id': _SESSION, 'result': result}).encode('utf-8')


class ClaudeEnvelopePolicyTest(unittest.TestCase):
    KEY = 'claude_review_envelope_limit_bytes'
    # The inner ``result`` must be a valid JSON document (the decoder runs
    # load_json(value['result'], ...) at claude_cli.py:87); built via the
    # module-level _envelope()/json.dumps so escaping is always correct.
    # ENVELOPE_OK is small (within the limit); ENVELOPE_BIG's outer body is
    # > 2048 bytes so the envelope gate (load_json(raw, limit)) trips first.
    ENVELOPE_OK = _envelope(json.dumps('ok'))
    ENVELOPE_BIG = _envelope(json.dumps('z' * 2200))
    def _write(self, tmpdir, policy):
        path = Path(tmpdir) / 'policy.json'
        path.write_bytes(json.dumps(policy).encode('utf-8'))
        return str(path)
    def _apply(self, policy_path, raw):
        # Exercise the provider's REAL _review_envelope_limit -> loader lookup:
        # only the policy path is redirected to the temp file, so the provider
        # cannot be bypassed by a regression to its old hard-coded constant.
        with mock.patch.object(rp, 'policy_path', return_value=policy_path), \
                mock.patch('llmtool.task_review.screen', lambda value: None):
            return claude_cli.decode_review_envelope(raw)
    def _refusal_code(self, call, raw):
        # The LLMToolError message is a generic 'review rejected; details
        # omitted' (the code is not its string form), so capture the exact code
        # the module-level reject() (imported at call-time from task_review)
        # was handed; that is the definitive 'clear refusal' signal.
        seen = []

        def _refuse(code):
            seen.append(code)
            raise LLMToolError(code)

        with mock.patch.object(task_review, 'reject', side_effect=_refuse):
            try:
                call(raw)
            except LLMToolError:
                return seen[0]
        self.fail('no refusal was raised')
    def test_default_limit_unchanged_when_key_absent(self):
        # A1: the key is absent in the repo policy. The provider's REAL
        # _review_envelope_limit (unmocked; only the policy path is redirected)
        # returns the hard-coded default, and an in-limit envelope is accepted
        # through that real lookup -- pinning A1 to the provider, not just the
        # loader (a regression to a bypass of the policy would fail this).
        with mock.patch.object(rp, 'policy_path', return_value=str(REPO_POLICY)):
            self.assertEqual(claude_cli._review_envelope_limit(),
                             claude_cli.REVIEW_ENVELOPE_LIMIT)
        self.assertEqual(self._apply(str(REPO_POLICY), self.ENVELOPE_OK)['content'],
                         json.dumps('ok'))
    def test_configured_limit_applies(self):
        limit = 2048
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write(tmpdir, dict(BASE, **{self.KEY: limit}))
            # A2: the provider's REAL _review_envelope_limit (unmocked; only the
            # policy path redirected) reads the configured value from policy.
            with mock.patch.object(rp, 'policy_path', return_value=path):
                self.assertEqual(claude_cli._review_envelope_limit(), limit)
            self.assertEqual(self._apply(path, self.ENVELOPE_OK)['content'],
                             json.dumps('ok'))
            self.assertEqual(self._refusal_code(
                lambda raw: self._apply(path, raw), self.ENVELOPE_BIG),
                'REVIEW_WORKER_ENVELOPE_INVALID')
    def test_invalid_policy_refuses(self):
        missing = dict(BASE)
        del missing['review_fail_max']
        with tempfile.TemporaryDirectory() as tmpdir:
            for policy in (dict(BASE, **{self.KEY: 0}), missing):
                self.assertEqual(self._refusal_code(
                    lambda raw: self._apply(self._write(tmpdir, policy), raw),
                    self.ENVELOPE_OK), 'POLICY_INVALID')
    def test_live_output_path_honors_configured_limit(self):
        # F1: the live output path must not refuse a configured envelope limit
        # above the generic 1 MiB OUTPUT_LIMIT. _read_pipe now takes a per-call
        # limit: a 1.5 MiB payload (above OUTPUT_LIMIT) is read to completion
        # under the 2 MiB configured limit, but is refused ('limit') under the
        # generic default -- so the CLI limit is preserved and the configured
        # value can take effect.
        payload = b'x' * (1536 * 1024)  # 1.5 MiB, above OUTPUT_LIMIT (1 MiB)
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / 'out.bin'
            src.write_bytes(payload)
            for limit, done_expected in ((2 * 1024 * 1024, True),
                                         (claude_cli.OUTPUT_LIMIT, False)):
                out = bytearray()
                done = False
                with open(src, 'rb') as stream:
                    while not done:
                        r = claude_cli.ClaudeCLIProvider._read_pipe(
                            stream, out, limit)
                        if r == 'limit':
                            break
                        if r:
                            done = True
                self.assertEqual(done, done_expected)
                if done_expected:
                    self.assertEqual(len(out), len(payload))
                else:
                    self.assertLess(len(out), len(payload))


if __name__ == '__main__':
    unittest.main()
