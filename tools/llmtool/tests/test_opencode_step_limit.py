"""#1817: a run that spent every agent step is refused OPENCODE_STEPS_EXHAUSTED.

Synthetic newline-delimited JSON events, built the same way as the fixtures in
test_opencode_direct_developer.py. No provider is called.
"""

import json
import unittest

from llmtool.opencode_direct_developer import DirectOpenCodeError, parse_json_events


def event(kind, **fields):
    return json.dumps({'type': kind, 'sessionID': 'ses_Abc123', **fields})


def stream(*events):
    return ('\n'.join(events) + '\n').encode()


def step_finishes(count, *, tokens=None):
    part = {} if tokens is None else {'tokens': {'input': tokens, 'output': tokens}}
    return [event('step_finish', part=part) for _ in range(count)]


class StepLimitClassificationTests(unittest.TestCase):
    # Default limit (12) and one configured limit (5).
    LIMITS = ((None, 12), (5, 5))

    def parse(self, raw, step_limit=None):
        if step_limit is None:
            return parse_json_events(raw)
        return parse_json_events(raw, step_limit=step_limit)

    def assert_refused(self, raw, expected, *, step_limit=None):
        with self.assertRaises(DirectOpenCodeError) as caught:
            self.parse(raw, step_limit)
        self.assertEqual(caught.exception.direct_opencode_code, expected)
        return caught.exception

    def test_closing_summary_at_step_limit_is_exhausted(self):
        for step_limit, count in self.LIMITS:
            with self.subTest(step_limit=step_limit):
                raw = stream(*step_finishes(count),
                             event('text', part={'text': 'summary of unfinished work'}))
                self.assert_refused(raw, 'OPENCODE_STEPS_EXHAUSTED',
                                    step_limit=step_limit)

    def test_closing_summary_keeps_step_usage_evidence(self):
        for step_limit, count in self.LIMITS:
            with self.subTest(step_limit=step_limit):
                raw = stream(*step_finishes(count, tokens=4),
                             event('text', part={'text': 'summary'}))
                error = self.assert_refused(raw, 'OPENCODE_STEPS_EXHAUSTED',
                                            step_limit=step_limit)
                self.assertEqual(error.provider_evidence,
                                 {'basis': 'STEP_USAGE', 'accepted': True})

    def test_no_text_at_step_limit_is_still_exhausted(self):
        for step_limit, count in self.LIMITS:
            with self.subTest(step_limit=step_limit):
                self.assert_refused(stream(*step_finishes(count)),
                                    'OPENCODE_STEPS_EXHAUSTED',
                                    step_limit=step_limit)

    def test_same_session_error_at_step_limit_is_exhausted(self):
        refused_tool = event('error', error={
            'type': 'provider.unknown',
            'name': 'ToolDisabledAfterStepLimit',
        })
        for step_limit, count in self.LIMITS:
            with self.subTest(step_limit=step_limit):
                raw = stream(*step_finishes(count), refused_tool)
                self.assert_refused(raw, 'OPENCODE_STEPS_EXHAUSTED',
                                    step_limit=step_limit)

    def test_tool_denied_keeps_precedence_over_exhaustion(self):
        denied = event('tool_use', part={'state': {'status': 'denied'}})
        for step_limit, count in self.LIMITS:
            with self.subTest(step_limit=step_limit):
                raw = stream(*step_finishes(count), denied)
                self.assert_refused(raw, 'TOOL_DENIED', step_limit=step_limit)

    def test_auth_error_keeps_precedence_over_exhaustion(self):
        auth = event('error', error={'type': 'provider.auth'})
        for step_limit, count in self.LIMITS:
            with self.subTest(step_limit=step_limit):
                raw = stream(*step_finishes(count), auth)
                self.assert_refused(raw, 'OPENCODE_AUTH_REQUIRED',
                                    step_limit=step_limit)

    def test_free_limit_keeps_precedence_over_exhaustion(self):
        limit = event('error', error={'type': 'provider.free-limit'})
        for step_limit, count in self.LIMITS:
            with self.subTest(step_limit=step_limit):
                raw = stream(*step_finishes(count), limit)
                error = self.assert_refused(raw, 'OPENCODE_FREE_LIMIT',
                                            step_limit=step_limit)
                self.assertEqual(error.provider_evidence,
                                 {'condition': 'QUOTA_EXHAUSTED'})

    def test_rate_limit_keeps_precedence_over_exhaustion(self):
        limit = event('error', error={'type': 'provider.rate-limit'})
        for step_limit, count in self.LIMITS:
            with self.subTest(step_limit=step_limit):
                raw = stream(*step_finishes(count), limit)
                error = self.assert_refused(raw, 'OPENCODE_RATE_LIMITED',
                                            step_limit=step_limit)
                self.assertEqual(error.provider_evidence,
                                 {'condition': 'RATE_LIMITED'})

    def test_below_limit_with_text_still_succeeds(self):
        for step_limit, count in self.LIMITS:
            with self.subTest(step_limit=step_limit):
                raw = stream(*step_finishes(count - 1, tokens=2),
                             event('text', part={'text': 'done'}))
                sid, text, usage = self.parse(raw, step_limit)
                self.assertEqual((sid, text), ('ses_Abc123', 'done'))
                self.assertEqual(usage['total_tokens'], 2 * (count - 1) * 2)

    def test_below_limit_without_text_still_succeeds(self):
        for step_limit, count in self.LIMITS:
            with self.subTest(step_limit=step_limit):
                raw = stream(*step_finishes(count - 1))
                sid, text, usage = self.parse(raw, step_limit)
                self.assertEqual((sid, text, usage), ('ses_Abc123', None, None))

    def test_below_limit_same_session_error_stays_provider_failed(self):
        unavailable = event('error', error={'type': 'provider.unavailable'})
        for step_limit, count in self.LIMITS:
            with self.subTest(step_limit=step_limit):
                raw = stream(*step_finishes(count - 1), unavailable)
                self.assert_refused(raw, 'OPENCODE_PROVIDER_FAILED',
                                    step_limit=step_limit)


if __name__ == '__main__':
    unittest.main()
