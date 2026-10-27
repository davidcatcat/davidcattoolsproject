import json
import unittest

from llmtool import codex_direct_developer as direct
from llmtool import task_store


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


def event_stream(*records):
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

    def test_result_usage_includes_valid_cached_and_reasoning_counts(self):
        raw = event_stream(
            {'type': 'thread.started', 'thread_id': THREAD_ID},
            {'type': 'turn.completed', 'usage': {
                'input_tokens': 10, 'output_tokens': 4,
                'cached_input_tokens': 3, 'reasoning_output_tokens': 2}},
            {'type': 'turn.completed', 'usage': {
                'input_tokens': 12, 'output_tokens': 5,
                'cached_input_tokens': True, 'reasoning_output_tokens': -1}},
            {'type': 'item.completed', 'item': {
                'type': 'agent_message', 'text': json.dumps(FINAL)}},
        )

        _, _, usage = direct._result_from_events(raw)

        self.assertEqual(usage, {
            'prompt_tokens': 12, 'completion_tokens': 5, 'total_tokens': 17})

    def test_timeout_usage_includes_valid_cached_and_reasoning_counts(self):
        raw = event_stream(
            {'type': 'turn.completed', 'usage': {
                'input_tokens': 10, 'output_tokens': 4,
                'cached_input_tokens': -1, 'reasoning_output_tokens': False}},
            {'type': 'turn.completed', 'usage': {
                'input_tokens': 12, 'output_tokens': 5,
                'cached_input_tokens': 7, 'reasoning_output_tokens': 3}},
        )

        self.assertEqual(direct._usage_from_events(raw), {
            'prompt_tokens': 12, 'completion_tokens': 5, 'total_tokens': 17,
            'cached_prompt_tokens': 7, 'reasoning_tokens': 3})

    def test_tool_calls_count_started_calls_in_mixed_event_stream(self):
        raw = event_stream(
            {'type': 'thread.started', 'thread_id': THREAD_ID},
            {'type': 'item.started', 'item': {'id': '1', 'type': 'command_execution'}},
            {'type': 'item.completed', 'item': {'id': '1', 'type': 'command_execution'}},
            {'type': 'item.started', 'item': {'id': '2', 'type': 'mcp_tool_call'}},
            {'type': 'item.started', 'item': {'id': '3', 'type': 'function_call'}},
            {'type': 'item.started', 'item': {'id': '4', 'type': 'agent_message'}},
            {'type': 'turn.completed', 'usage': {
                'input_tokens': 12, 'output_tokens': 5}},
            {'type': 'item.completed', 'item': {
                'type': 'agent_message', 'text': json.dumps(FINAL)}},
        )

        _, _, usage = direct._result_from_events(raw)
        timeout_usage = direct._usage_from_events(raw)

        self.assertEqual(usage['tool_calls'], 3)
        self.assertEqual(timeout_usage['tool_calls'], 3)

    def test_unknown_event_shape_does_not_guess_tool_call_count(self):
        raw = event_stream(
            {'type': 'provider.telemetry', 'calls': 5},
            {'type': 'turn.completed', 'usage': {
                'input_tokens': 12, 'output_tokens': 5}},
        )

        usage = direct._usage_from_events(raw)

        self.assertNotIn('tool_calls', usage)

    def test_task_usage_accepts_tool_calls(self):
        task_store._validate_usage({
            'prompt_tokens': 12, 'completion_tokens': 5, 'total_tokens': 17,
            'tool_calls': 3,
        })


if __name__ == '__main__':
    unittest.main()
