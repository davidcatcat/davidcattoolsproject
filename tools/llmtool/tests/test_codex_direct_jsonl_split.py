"""#1893 follow-up: a raw U+2028 inside a Codex JSONL event does not split it (Codex direct)."""
import json
import unittest

from llmtool import codex_direct_developer


class CodexDirectJsonlSplitTests(unittest.TestCase):
    def test_raw_line_separators_inside_events_keep_the_thread(self):
        for sep in ('\u2028', '\u2029', '\x85'):
            with self.subTest(sep=hex(ord(sep))):
                events = [{'type': 'thread.started', 'thread_id': '0123456789abcdef'},
                          {'type': 'item.completed',
                           'item': {'type': 'agent_message', 'text': 'a' + sep + 'b'}}]
                raw = '\n'.join(json.dumps(e, ensure_ascii=False) for e in events).encode('utf-8')
                self.assertEqual(codex_direct_developer._thread_from_events(raw),
                                 '0123456789abcdef')


if __name__ == '__main__':
    unittest.main()
