"""#1893 follow-up: codex_direct_developer._thread_from_events thread.started event order."""
import json
import unittest

from llmtool import codex_direct_developer


def _raw(events):
    return '\n'.join(json.dumps(e, ensure_ascii=False) for e in events).encode('utf-8')


class CodexDirectThreadOrderTests(unittest.TestCase):
    def test_first_event_not_thread_started_returns_none(self):
        raw = _raw([{'type': 'item.completed',
                     'item': {'type': 'agent_message', 'text': 'hello'}},
                    {'type': 'thread.started', 'thread_id': '0123456789abcdef'}])
        self.assertIsNone(codex_direct_developer._thread_from_events(raw))

    def test_conflicting_thread_ids_return_none(self):
        raw = _raw([{'type': 'thread.started', 'thread_id': '0123456789abcdef'},
                    {'type': 'thread.started', 'thread_id': 'fedcba9876543210'}])
        self.assertIsNone(codex_direct_developer._thread_from_events(raw))

    def test_repeated_thread_id_is_returned(self):
        raw = _raw([{'type': 'thread.started', 'thread_id': '0123456789abcdef'},
                    {'type': 'item.completed',
                     'item': {'type': 'agent_message', 'text': 'ok'}},
                    {'type': 'thread.started', 'thread_id': '0123456789abcdef'}])
        self.assertEqual(codex_direct_developer._thread_from_events(raw),
                         '0123456789abcdef')


if __name__ == '__main__':
    unittest.main()
