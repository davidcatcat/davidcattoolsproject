"""#2486: a duplicate key in an unused Codex event no longer refuses the review.

A duplicate key in ``thread.started``, in a completed ``agent_message`` item or
in the verdict JSON is still refused with the existing code. Any other event
with a duplicate key is skipped without using its content.
"""

import json
import unittest

from llmtool import dispute_arbiter, review_dispatch
from llmtool.errors import LLMToolError

TASK = 'a' * 32
HEAD = 'b' * 40
RUN = 'c' * 32
CONTRACT = 'd' * 64
THREAD_ID = '0123456789abcdef'
PR = 42
FINDING = 'F1'
REVIEW_VERSION = 1
BINDING = (f'LLMTOOL-REVIEW-BINDING v1 task={TASK} run={RUN} '
           f'contract={CONTRACT} head={HEAD}')


def _review_text(*, version=REVIEW_VERSION):
    return (
        '<!-- activity: type=review_verdict actor=Codex target=PM status=PASS -->\n'
        f'Verdict: PASS | Version: v{version} | HEAD: {HEAD}\n'
        'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n'
        f'{BINDING}'
    )


def _verdict(**changes):
    verdict = {'task_id': TASK, 'run_id': RUN, 'head': HEAD,
               'verdict': 'PASS', 'review': _review_text()}
    verdict.update(changes)
    return verdict


def _ruling_text():
    return (
        '<!-- activity: type=dispute_ruling actor=Codex target=PM status=UPHELD -->\n'
        f'Ruling: {FINDING} UPHELD | PR: #{PR} | Version: {REVIEW_VERSION} | HEAD: {HEAD}\n'
        f'{BINDING}\n'
        'Arbiter reasoning for the synthetic case.'
    )


def _ruling_payload():
    return {'task_id': TASK, 'run_id': RUN, 'head': HEAD,
            'outcome': 'UPHELD', 'ruling': _ruling_text()}


def _stream(*lines):
    return ('\n'.join(lines) + '\n').encode('utf-8')


def _thread_started():
    return json.dumps({'type': 'thread.started', 'thread_id': THREAD_ID})


def _agent_message(text):
    return json.dumps({'type': 'item.completed',
                       'item': {'type': 'agent_message', 'text': text}})


def _duplicate_thread_started():
    return ('{"type": "thread.started", "thread_id": "%s", '
            '"thread_id": "%s"}' % (THREAD_ID, THREAD_ID))


def _duplicate_agent_message():
    return ('{"type": "item.completed", "item": {"type": "agent_message", '
            '"text": "synthetic progress", "text": "synthetic progress"}}')


def _duplicate_value(document, key, value):
    return document[:-1] + ', %s: %s}' % (json.dumps(key), json.dumps(value))


def _other_event_with_duplicate():
    return '{"type": "turn.completed", "payload": {"a": 1, "a": 2}}'


def _other_item_with_duplicate():
    return ('{"type": "item.completed", "item": {"type": "command_output", '
            '"text": "synthetic output", "text": "synthetic output"}}')


class ReviewStreamDuplicateKeyTests(unittest.TestCase):
    def _parse(self, raw):
        return review_dispatch._parse_codex_review(
            raw, task_id=TASK, run_id=RUN, contract_digest=CONTRACT, head=HEAD)

    def _assert_refused(self, raw, reason):
        with self.assertRaises(LLMToolError) as caught:
            self._parse(raw)
        self.assertEqual(caught.exception.review_dispatch_code, 'REVIEW_NOT_POSTED')
        self.assertEqual(caught.exception.review_failure_reason, reason)

    def _valid_stream(self, *extra):
        return _stream(_thread_started(), *extra,
                       _agent_message(json.dumps(_verdict())))

    def test_duplicate_key_in_thread_started_is_refused(self):
        raw = _stream(_duplicate_thread_started(),
                      _agent_message(json.dumps(_verdict())))
        self._assert_refused(raw, 'OUTPUT_NOT_JSONL')

    def test_duplicate_key_in_completed_agent_message_is_refused(self):
        self._assert_refused(
            self._valid_stream(_duplicate_agent_message()), 'OUTPUT_NOT_JSONL')

    def test_duplicate_key_in_verdict_json_is_refused(self):
        raw = _stream(
            _thread_started(),
            _agent_message(_duplicate_value(
                json.dumps(_verdict()), 'verdict', 'PASS')))
        self._assert_refused(raw, 'VERDICT_NOT_JSON')

    def test_duplicate_key_in_other_events_is_skipped(self):
        parsed = self._parse(self._valid_stream(
            _other_event_with_duplicate(), _other_item_with_duplicate()))
        self.assertEqual(parsed['verdict'], 'PASS')
        self.assertEqual(parsed['thread_id'], THREAD_ID)
        self.assertEqual(parsed['review_text'], _review_text())
        self.assertNotIn('synthetic output', json.dumps(parsed))

    def test_untyped_duplicate_object_is_still_malformed(self):
        # A JSON object without a string event type is not a Codex event, so it
        # keeps the existing OUTPUT_NOT_JSONL refusal.
        self._assert_refused(
            self._valid_stream('{"dup": 1, "dup": 2}'), 'OUTPUT_NOT_JSONL')


class DisputeStreamDuplicateKeyTests(unittest.TestCase):
    def _parse(self, raw):
        return dispute_arbiter.parse_codex_ruling(
            raw, task_id=TASK, run_id=RUN, contract_digest=CONTRACT,
            head=HEAD, finding_id=FINDING, pr=PR,
            review_version=REVIEW_VERSION)

    def _assert_refused(self, raw, reason):
        with self.assertRaises(LLMToolError) as caught:
            self._parse(raw)
        self.assertEqual(caught.exception.review_dispatch_code, 'REVIEW_NOT_POSTED')
        self.assertEqual(caught.exception.review_failure_reason, reason)

    def _valid_stream(self, *extra):
        return _stream(_thread_started(), *extra,
                       _agent_message(json.dumps(_ruling_payload())))

    def test_duplicate_key_in_thread_started_is_refused(self):
        raw = _stream(_duplicate_thread_started(),
                      _agent_message(json.dumps(_ruling_payload())))
        self._assert_refused(raw, 'OUTPUT_NOT_JSONL')

    def test_duplicate_key_in_completed_agent_message_is_refused(self):
        self._assert_refused(
            self._valid_stream(_duplicate_agent_message()), 'OUTPUT_NOT_JSONL')

    def test_duplicate_key_in_verdict_json_is_refused(self):
        raw = _stream(
            _thread_started(),
            _agent_message(_duplicate_value(
                json.dumps(_ruling_payload()), 'outcome', 'UPHELD')))
        self._assert_refused(raw, 'VERDICT_NOT_JSON')

    def test_duplicate_key_in_other_events_is_skipped(self):
        parsed = self._parse(self._valid_stream(
            _other_event_with_duplicate(), _other_item_with_duplicate()))
        self.assertEqual(parsed['outcome'], 'UPHELD')
        self.assertEqual(parsed['thread_id'], THREAD_ID)
        self.assertEqual(parsed['review_text'], _ruling_text())
        self.assertNotIn('synthetic output', json.dumps(parsed))


if __name__ == '__main__':
    unittest.main()
