"""Tests for large-stream Codex review JSONL parsing (#2212)."""

import json
import unittest

from llmtool import review_dispatch
from llmtool.errors import LLMToolError
import review_trust_fixture


TASK = 'a' * 32
HEAD = 'b' * 40
RUN_ID = 'c' * 32
CONTRACT = 'd' * 64
THREAD_ID = '0123456789abcdef'


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def _codex_review_text(*, run_id=RUN_ID, contract=CONTRACT, head=HEAD, task_id=TASK, version=1):
    return (
        '<!-- activity: type=review_verdict actor=Codex target=PM status=PASS -->\n'
        f'Verdict: PASS | Version: v{version} | HEAD: {head}\n'
        'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n'
        f'LLMTOOL-REVIEW-BINDING v1 task={task_id} run={run_id} '
        f'contract={contract} head={head}'
    )


def _codex_verdict(*, run_id=RUN_ID, head=HEAD, task_id=TASK, review_text=None, **changes):
    if review_text is None:
        review_text = _codex_review_text(run_id=run_id, head=head, task_id=task_id)
    verdict = {
        'task_id': task_id,
        'run_id': run_id,
        'head': head,
        'verdict': 'PASS',
        'review': review_text,
    }
    verdict.update(changes)
    return verdict


def _codex_stream_with_events(*extra_events, review_text=None, thread_id=THREAD_ID):
    verdict = _codex_verdict(review_text=review_text)
    events = [
        {'type': 'thread.started', 'thread_id': thread_id},
        *extra_events,
        {'type': 'item.completed', 'item': {
            'type': 'agent_message', 'text': json.dumps(verdict)}},
    ]
    return '\n'.join(json.dumps(event) for event in events).encode('utf-8')


class ReviewDispatchLargeStreamTests(unittest.TestCase):
    """Test suite for parsing large Codex review streams up to 16 MiB ceiling."""

    def test_3mib_stream_with_valid_final_review_accepted(self):
        # Build intermediate events totalling ~3 MiB
        intermediate = {
            'type': 'item.completed',
            'item': {
                'type': 'command_output',
                'text': 'safe_output_chunk_' * 175000,
            },
        }
        raw = _codex_stream_with_events(intermediate)
        self.assertGreaterEqual(len(raw), 3 * 1024 * 1024)
        self.assertLess(len(raw), 4 * 1024 * 1024)

        parsed = review_dispatch._parse_codex_review(
            raw, task_id=TASK, run_id=RUN_ID, contract_digest=CONTRACT, head=HEAD)
        self.assertEqual(parsed['verdict'], 'PASS')
        self.assertEqual(parsed['thread_id'], THREAD_ID)
        self.assertEqual(parsed['review_text'], _codex_review_text())

        # Also verify _parse_codex_review_inner directly
        inner_parsed = review_dispatch._parse_codex_review_inner(
            raw, task_id=TASK, run_id=RUN_ID, contract_digest=CONTRACT, head=HEAD)
        self.assertEqual(inner_parsed, parsed)

    def test_final_message_over_1mib_refused(self):
        # A final message exceeding 1 MiB (_CODEX_REVIEW_MAX_OUTPUT)
        oversized_verdict = json.dumps(_codex_verdict()) + (' ' * (1024 * 1024 + 1024))
        events = [
            {'type': 'thread.started', 'thread_id': THREAD_ID},
            {'type': 'item.completed', 'item': {
                'type': 'agent_message', 'text': oversized_verdict}},
        ]
        raw = '\n'.join(json.dumps(event) for event in events).encode('utf-8')

        with self.assertRaises(LLMToolError) as raised:
            review_dispatch._parse_codex_review(
                raw, task_id=TASK, run_id=RUN_ID, contract_digest=CONTRACT, head=HEAD)
        self.assertEqual(raised.exception.review_dispatch_code, 'REVIEW_NOT_POSTED')
        self.assertEqual(raised.exception.review_failure_reason, 'OUTPUT_UNREADABLE')

        with self.assertRaises(LLMToolError) as raised_inner:
            review_dispatch._parse_codex_review_inner(
                raw, task_id=TASK, run_id=RUN_ID, contract_digest=CONTRACT, head=HEAD)
        self.assertEqual(raised_inner.exception.review_dispatch_code, 'REVIEW_NOT_POSTED')
        self.assertEqual(raised_inner.exception.review_failure_reason, 'OUTPUT_UNREADABLE')

    def test_stream_over_hard_ceiling_refused(self):
        # Stream exceeding the 16 MiB hard ceiling (_CODEX_REVIEW_HARD_CEILING)
        base = _codex_stream_with_events()
        padding_size = review_dispatch._CODEX_REVIEW_HARD_CEILING - len(base) + 1024
        raw = base + (b' ' * padding_size)
        self.assertGreater(len(raw), review_dispatch._CODEX_REVIEW_HARD_CEILING)

        with self.assertRaises(LLMToolError) as raised:
            review_dispatch._parse_codex_review(
                raw, task_id=TASK, run_id=RUN_ID, contract_digest=CONTRACT, head=HEAD)
        self.assertEqual(raised.exception.review_dispatch_code, 'REVIEW_NOT_POSTED')
        self.assertEqual(raised.exception.review_failure_reason, 'OUTPUT_UNREADABLE')

        with self.assertRaises(LLMToolError) as raised_inner:
            review_dispatch._parse_codex_review_inner(
                raw, task_id=TASK, run_id=RUN_ID, contract_digest=CONTRACT, head=HEAD)
        self.assertEqual(raised_inner.exception.review_dispatch_code, 'REVIEW_NOT_POSTED')
        self.assertEqual(raised_inner.exception.review_failure_reason, 'OUTPUT_UNREADABLE')

    def test_malformed_jsonl_in_3mib_stream_refused(self):
        # A 3 MiB stream containing malformed JSONL line
        large_filler = json.dumps({
            'type': 'item.completed',
            'item': {'type': 'tool_output', 'text': 'filler_data_' * 180000},
        })
        lines = [
            json.dumps({'type': 'thread.started', 'thread_id': THREAD_ID}),
            '{"type": bad json line without closing brace',
            large_filler,
            json.dumps({'type': 'item.completed', 'item': {
                'type': 'agent_message', 'text': json.dumps(_codex_verdict())}}),
        ]
        raw = '\n'.join(lines).encode('utf-8')
        self.assertGreaterEqual(len(raw), 3 * 1024 * 1024)

        with self.assertRaises(LLMToolError) as raised:
            review_dispatch._parse_codex_review(
                raw, task_id=TASK, run_id=RUN_ID, contract_digest=CONTRACT, head=HEAD)
        self.assertEqual(raised.exception.review_dispatch_code, 'REVIEW_NOT_POSTED')
        self.assertEqual(raised.exception.review_failure_reason, 'OUTPUT_NOT_JSONL')

        with self.assertRaises(LLMToolError) as raised_inner:
            review_dispatch._parse_codex_review_inner(
                raw, task_id=TASK, run_id=RUN_ID, contract_digest=CONTRACT, head=HEAD)
        self.assertEqual(raised_inner.exception.review_dispatch_code, 'REVIEW_NOT_POSTED')
        self.assertEqual(raised_inner.exception.review_failure_reason, 'OUTPUT_NOT_JSONL')

    def test_oversized_intermediate_event_skipped_and_valid_review_accepted(self):
        # An intermediate non-agent event > 1 MiB is skipped, not kept
        large_intermediate = {
            'type': 'item.completed',
            'item': {
                'type': 'file_diff_viewer',
                'diff': 'diff_chunk_' * 120000,  # > 1 MiB
            },
        }
        raw = _codex_stream_with_events(large_intermediate)
        self.assertGreater(len(json.dumps(large_intermediate).encode('utf-8')), 1024 * 1024)

        parsed = review_dispatch._parse_codex_review(
            raw, task_id=TASK, run_id=RUN_ID, contract_digest=CONTRACT, head=HEAD)
        self.assertEqual(parsed['verdict'], 'PASS')
        self.assertEqual(parsed['review_text'], _codex_review_text())

    def test_oversized_intermediate_agent_message_skipped(self):
        # An intermediate agent_message > 1 MiB is skipped when followed by valid final review
        intermediate_msg = {
            'type': 'item.completed',
            'item': {
                'type': 'agent_message',
                'text': 'analysis_text_' * 100000,  # > 1 MiB
            },
        }
        raw = _codex_stream_with_events(intermediate_msg)
        self.assertGreater(len(intermediate_msg['item']['text'].encode('utf-8')), 1024 * 1024)

        parsed = review_dispatch._parse_codex_review(
            raw, task_id=TASK, run_id=RUN_ID, contract_digest=CONTRACT, head=HEAD)
        self.assertEqual(parsed['verdict'], 'PASS')
        self.assertEqual(parsed['review_text'], _codex_review_text())

    def test_jsonl_shape_on_3mib_stream(self):
        # _jsonl_shape reports the shape of the bad line in a 3 MiB stream
        lines = [
            json.dumps({'type': 'thread.started', 'thread_id': THREAD_ID}),
            'not_json_entry_123',
            json.dumps({'type': 'item.completed', 'item': {
                'type': 'data', 'text': 'pad_' * 600000}}),
        ]
        raw = '\n'.join(lines).encode('utf-8')
        self.assertGreaterEqual(len(raw), 2 * 1024 * 1024)

        shape = review_dispatch._jsonl_shape(raw)
        self.assertIn('OUTPUT_SHAPE line=2', shape)


if __name__ == '__main__':
    unittest.main()
