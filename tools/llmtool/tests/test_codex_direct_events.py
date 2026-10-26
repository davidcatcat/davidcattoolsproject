import json
import unittest

from llmtool import codex_direct_developer as direct


THREAD_ID = '12345678-1234-4234-8234-123456789abc'
FINAL = {
    'status': 'DONE',
    'summary': 'updated',
    'tests': ['passed'],
    'self_review': 'diff checked',
    'blocked_reason': None,
}


def events(*messages):
    records = [{'type': 'thread.started', 'thread_id': THREAD_ID}]
    records.extend({
        'type': 'item.completed',
        'item': {'type': 'agent_message', 'text': message},
    } for message in messages)
    return '\n'.join(json.dumps(record) for record in records).encode('utf-8')


class CodexDirectEventsTests(unittest.TestCase):
    def test_last_agent_message_is_used_after_interim_message(self):
        raw = events('Working through the change.', json.dumps(FINAL))

        thread_id, result, usage = direct._result_from_events(raw)

        self.assertEqual(thread_id, THREAD_ID)
        self.assertEqual(result, FINAL)
        self.assertIsNone(usage)

    def test_invalid_last_agent_message_fails_even_after_valid_interim_message(self):
        raw = events(json.dumps(FINAL), '{invalid final json')

        with self.assertRaises(direct.DirectCodexError) as caught:
            direct._result_from_events(raw)

        self.assertEqual(caught.exception.direct_codex_code,
                         'CODEX_FINAL_JSON_INVALID')

    def test_single_agent_message_is_used(self):
        raw = events(json.dumps(FINAL))

        thread_id, result, usage = direct._result_from_events(raw)

        self.assertEqual(thread_id, THREAD_ID)
        self.assertEqual(result, FINAL)
        self.assertIsNone(usage)

    def test_non_string_agent_message_text_still_fails(self):
        raw = events(json.dumps(FINAL), 17)

        with self.assertRaises(direct.DirectCodexError) as caught:
            direct._result_from_events(raw)

        self.assertEqual(caught.exception.direct_codex_code,
                         'CODEX_FINAL_MESSAGE_INVALID')


if __name__ == '__main__':
    unittest.main()
