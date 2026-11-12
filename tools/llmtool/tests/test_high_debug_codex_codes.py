"""#1502 WP-B: Codex AI Debug failure classes (_codex_failure_code)."""

import json
import unittest

from llmtool import high_debug


class CodexFailureCodeTests(unittest.TestCase):
    def test_quota_and_login_text_on_stderr(self):
        self.assertEqual(high_debug._codex_failure_code(b'', b"You've hit your usage limit."),
                         'HIGH_DEBUG_QUOTA_EXHAUSTED')
        self.assertEqual(high_debug._codex_failure_code(b'', b'Error: not logged in'),
                         'HIGH_DEBUG_AUTH_FAILED')

    def test_error_events_are_read(self):
        failed = json.dumps({'type': 'turn.failed', 'error': {'message': '401 Unauthorized'}})
        self.assertEqual(high_debug._codex_failure_code(failed.encode() + b'\n', b''),
                         'HIGH_DEBUG_AUTH_FAILED')
        error = json.dumps({'type': 'error', 'message': 'stream error: 429 rate limit'})
        self.assertEqual(high_debug._codex_failure_code(error.encode() + b'\n', b''),
                         'HIGH_DEBUG_QUOTA_EXHAUSTED')

    def test_login_wins_over_quota(self):
        self.assertEqual(high_debug._codex_failure_code(b'', b'usage limit; please log in again'),
                         'HIGH_DEBUG_AUTH_FAILED')

    def test_agent_text_and_other_failures_stay_run_failed(self):
        message = json.dumps({'type': 'item.completed',
                              'item': {'type': 'agent_message', 'text': 'the rate limit test is flaky'}})
        self.assertEqual(high_debug._codex_failure_code(message.encode() + b'\nnot json\n', b''),
                         'HIGH_DEBUG_RUN_FAILED')
        self.assertEqual(high_debug._codex_failure_code(b'', b'segmentation fault'),
                         'HIGH_DEBUG_RUN_FAILED')
        self.assertEqual(high_debug._codex_failure_code(None, None), 'HIGH_DEBUG_RUN_FAILED')

    def test_fallback_codes_are_only_quota_and_login(self):
        self.assertEqual(high_debug.DEBUG_FALLBACK_CODES,
                         {'HIGH_DEBUG_QUOTA_EXHAUSTED', 'HIGH_DEBUG_AUTH_FAILED'})
        self.assertIn('HIGH_DEBUG_AUTH_FAILED', high_debug._REFUSAL_CODES)


if __name__ == '__main__':
    unittest.main()
