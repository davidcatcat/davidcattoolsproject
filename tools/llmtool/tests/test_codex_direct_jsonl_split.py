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

    def test_raw_line_separators_inside_final_summary_keep_the_result(self):
        thread_id = '0123456789abcdef'
        prompt_tokens = 1111
        completion_tokens = 2222
        for sep in ('\u2028', '\u2029', '\x85'):
            with self.subTest(sep=hex(ord(sep))):
                summary = 'first' + sep + 'second'
                final = {'status': 'DONE', 'summary': summary, 'tests': ['passed'],
                         'self_review': 'diff checked', 'blocked_reason': None}
                events = [{'type': 'thread.started', 'thread_id': thread_id},
                          {'type': 'item.completed',
                           'item': {'type': 'agent_message',
                                    'text': json.dumps(final, ensure_ascii=False)}},
                          {'type': 'turn.completed',
                           'usage': {'input_tokens': prompt_tokens,
                                     'output_tokens': completion_tokens}}]
                raw = '\n'.join(json.dumps(e, ensure_ascii=False) for e in events).encode('utf-8')
                self.assertIn(sep.encode('utf-8'), raw)
                result_thread_id, result, usage = codex_direct_developer._result_from_events(raw)
                self.assertEqual(result_thread_id, thread_id)
                self.assertEqual(result['summary'], summary)
                self.assertEqual(usage['prompt_tokens'], prompt_tokens)
                self.assertEqual(usage['completion_tokens'], completion_tokens)


if __name__ == '__main__':
    unittest.main()
