"""#1230 refusal-code pin: every refused Claude review envelope is REVIEW_WORKER_ENVELOPE_INVALID."""
import json
import unittest

from llmtool.errors import LLMToolError
from llmtool.providers.claude_cli import decode_review_envelope

SESSION = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'


def envelope(**changes):
    value = dict(type='result', subtype='success', is_error=False, session_id=SESSION, result='{}')
    value.update(changes)
    return json.dumps(value)


class ReviewEnvelopeCodeTests(unittest.TestCase):
    def test_valid_envelope_is_accepted(self):
        self.assertEqual(decode_review_envelope(envelope())['runtime_session_id'], SESSION)

    def test_refused_envelopes_name_one_code(self):
        cases = {
            'broken json': envelope()[:-1],
            'error subtype': envelope(subtype='error_during_execution'),
            'is error': envelope(is_error=True),
            'api error status': envelope(api_error_status=429),
            'terminal reason': envelope(terminal_reason='max_turns'),
            'stop reason': envelope(stop_reason='tool_use'),
            'permission denials': envelope(permission_denials=['Bash']),
            'negative cost': envelope(total_cost_usd=-1),
            'fast mode state': envelope(fast_mode_state='unknown'),
            'negative duration': envelope(duration_ms=-1),
            'bad uuid': envelope(uuid='not-a-uuid'),
            'unknown key': envelope(extra='private'),
        }
        for label, raw in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(LLMToolError) as caught:
                    decode_review_envelope(raw)
                self.assertEqual(caught.exception.review_code, 'REVIEW_WORKER_ENVELOPE_INVALID')
        with self.assertRaises(LLMToolError) as caught:
            decode_review_envelope(envelope(), forbidden_sessions=(SESSION,))
        self.assertEqual(caught.exception.review_code, 'REVIEW_WORKER_ENVELOPE_INVALID')


if __name__ == '__main__':
    unittest.main()
