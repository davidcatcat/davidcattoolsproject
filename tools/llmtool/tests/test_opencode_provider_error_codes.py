"""#2098: _classify_provider_error branches not pinned elsewhere (429 keys, precedence)."""
import json
import unittest

from llmtool.opencode_direct_developer import _classify_provider_error


def stream(*errors, extra=()):
    lines = [json.dumps({'type': 'error', 'sessionID': 'ses_Abc123', 'error': error})
             for error in errors]
    lines += [json.dumps(event) for event in extra]
    return ('\n'.join(lines) + '\n').encode()


class OpenCodeProviderErrorCodeTests(unittest.TestCase):
    def test_every_429_key_is_rate_limited(self):
        for error in ({'status': 429}, {'statusCode': 429}, {'status_code': 429},
                      {'code': 429}, {'code': '429'}):
            with self.subTest(error=error):
                self.assertEqual(_classify_provider_error(stream(error)),
                                 'OPENCODE_RATE_LIMITED')

    def test_other_status_values_are_provider_failed(self):
        for error in ({'status': 500}, {'code': '500'}, {'type': 'provider.unavailable'}):
            with self.subTest(error=error):
                self.assertEqual(_classify_provider_error(stream(error)),
                                 'OPENCODE_PROVIDER_FAILED')

    def test_free_limit_wins_over_rate_limit_and_truncation(self):
        raw = stream({'type': 'output-limit'}, {'statusCode': 429},
                     {'type': 'provider.free-limit'})
        self.assertEqual(_classify_provider_error(raw), 'OPENCODE_FREE_LIMIT')

    def test_rate_limit_wins_over_truncation(self):
        raw = stream({'type': 'output-limit'}, {'type': 'provider.rate-limit'})
        self.assertEqual(_classify_provider_error(raw), 'OPENCODE_RATE_LIMITED')

    def test_truncation_from_stderr_only(self):
        raw = stream({'type': 'provider.unavailable'})
        self.assertEqual(_classify_provider_error(raw, stderr=b'StepFailedError'),
                         'OPENCODE_OUTPUT_TRUNCATED')

    def test_no_error_event_is_none(self):
        raw = stream(extra=({'type': 'text', 'sessionID': 'ses_Abc123',
                             'part': {'text': 'done'}},))
        self.assertIsNone(_classify_provider_error(raw))


if __name__ == '__main__':
    unittest.main()
