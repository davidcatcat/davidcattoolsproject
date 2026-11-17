"""#1893 follow-up: _usage_from_events skips malformed JSONL and checks the last usage (High Debug)."""
import json
import unittest

from llmtool import high_debug


def _events_raw(*entries):
    """Build raw JSONL bytes: dicts are json.dumps-ed, str entries are used verbatim."""
    lines = [json.dumps(entry, ensure_ascii=False) if isinstance(entry, dict) else entry
             for entry in entries]
    return '\n'.join(lines).encode('utf-8')


class HighDebugUsageEventsTests(unittest.TestCase):
    def test_no_turn_completed_returns_unknown(self):
        raw = _events_raw(
            {'type': 'thread.started', 'thread_id': '0123456789abcdef'},
            {'type': 'message.completed', 'note': 'no usage here'},
        )
        self.assertEqual(high_debug._usage_from_events(raw), 'UNKNOWN')

    def test_missing_negative_or_non_integer_last_usage_returns_unknown(self):
        good = {'input_tokens': 5, 'cached_input_tokens': 1, 'output_tokens': 2,
                'reasoning_output_tokens': 0}
        cases = {
            'missing': {k: v for k, v in good.items() if k != 'output_tokens'},
            'negative': {**good, 'input_tokens': -3},
            'non_integer': {**good, 'output_tokens': 2.5},
        }
        for name, bad_usage in cases.items():
            with self.subTest(name=name):
                raw = _events_raw(
                    {'type': 'turn.completed', 'usage': good},
                    {'type': 'turn.completed', 'usage': bad_usage},
                )
                self.assertEqual(high_debug._usage_from_events(raw), 'UNKNOWN')

    def test_earlier_non_json_line_returns_last_valid_usage(self):
        usage = {'input_tokens': 5, 'cached_input_tokens': 1, 'output_tokens': 2,
                 'reasoning_output_tokens': 0}
        raw = _events_raw(
            '{this is not valid json',
            {'type': 'turn.completed', 'usage': usage},
        )
        self.assertEqual(high_debug._usage_from_events(raw),
                         {'input': 5, 'cached': 1, 'output': 2, 'reasoning': 0})


if __name__ == '__main__':
    unittest.main()
