"""#1893 follow-up: a raw U+2028 inside a Codex JSONL event does not split it (High Debug)."""
import json
import tempfile
import unittest
from pathlib import Path

from llmtool import high_debug


class HighDebugJsonlSplitTests(unittest.TestCase):
    def test_raw_line_separators_inside_events_keep_thread_and_usage(self):
        usage = {'input_tokens': 5, 'cached_input_tokens': 1, 'output_tokens': 2,
                 'reasoning_output_tokens': 0}
        for sep in ('\u2028', '\u2029', '\x85'):
            with self.subTest(sep=hex(ord(sep))):
                events = [{'type': 'thread.started', 'thread_id': '0123456789abcdef', 'note': sep},
                          {'type': 'turn.completed', 'usage': usage, 'note': 'a' + sep + 'b'}]
                raw = '\n'.join(json.dumps(e, ensure_ascii=False) for e in events).encode('utf-8')
                self.assertEqual(high_debug._thread_id_from_events(raw), '0123456789abcdef')
                self.assertEqual(high_debug._usage_from_events(raw),
                                 {'input': 5, 'cached': 1, 'output': 2, 'reasoning': 0})

    def test_raw_separators_keep_rollout_session_model_and_effort(self):
        for sep in ('\u2028', '\u2029', '\u0085'):
            with self.subTest(sep=hex(ord(sep))):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    rollout = root / '2026' / '10' / '27' / 'rollout-test-thread-1234567890.jsonl'
                    rollout.parent.mkdir(parents=True)
                    record = {'type': 'session_meta',
                              'payload': {'model': 'gpt-test',
                                          'reasoning_effort': 'high',
                                          'note': 'a' + sep + 'b'}}
                    rollout.write_text(json.dumps(record, ensure_ascii=False) + '\n',
                                       encoding='utf-8')
                    self.assertEqual(high_debug._session_observed('thread-1234567890', root),
                                     ('gpt-test', 'high'))


if __name__ == '__main__':
    unittest.main()
