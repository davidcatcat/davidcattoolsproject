"""#2486: a refused plain reply leaves screened evidence on the refusal receipt.

#2487 security v91 (CWE-532): a pattern screen cannot prove free reviewer text
safe, so the receipt holds a content-free diagnostic instead: fixed categories,
fixed JSON key names, fixed status words and counts. No reply prose is stored.

Every receipt still records the fixed-word shape and stays ``not_a_verdict``.
A screen refusal or undecodable bytes keep a fixed refusal word and no reply
diagnostic.
"""
import json
import unittest
from pathlib import Path

from unittest.mock import patch

from llmtool import review_dispatch, task_submission_cli
from llmtool.errors import LLMToolError
import test_review_dispatch as base
from test_review_dispatch import HEAD, setUpModule, tearDownModule  # noqa: F401

MARKER = 'synthetic-marker-prose'
_FIXED_KINDS = frozenset((
    'code_fence', 'json_array', 'json_object', 'json_scalar', 'text'))
_FIXED_FIRST = frozenset(('blank', 'brace', 'digit', 'letter', 'other'))
_FIXED_STATUS = frozenset(('PASS', 'FIX', 'FAIL', 'BLOCKED'))


def _pass_screen(text, _limit):
    return text


def _refuse_screen(_text, _limit):
    # Test data rule: the screen is injected, never the detector's patterns.
    raise LLMToolError('synthetic screen refusal')


class ReplyExcerptReceiptTests(unittest.TestCase):
    # Reuse the real store and private receipt directory from the #1907 tests.
    setUp = base.ReviewRefusalReceiptTests.setUp
    tearDown = base.ReviewRefusalReceiptTests.tearDown
    _invoke_refusal = base.ReviewRefusalReceiptTests._invoke_refusal

    def _refusal(self, raw, screen):
        task = self.store.get(self.task_id)

        def runner(**_kw):
            return review_dispatch._parse_codex_review(
                raw, task_id=self.task_id, run_id=task['run_id'],
                contract_digest=task['task_binding']['contract_digest'], head=HEAD)

        with patch.object(task_submission_cli, '_screen_text', side_effect=screen), \
                self.assertRaises(LLMToolError) as raised:
            self._invoke_refusal(runner=runner)
        self.assertEqual(raised.exception.review_dispatch_code, 'REVIEW_NOT_POSTED')
        return raised.exception

    def _receipt(self, exc):
        path = Path(exc.review_refusal['receipt_path'])
        self.receipt_text = path.read_text(encoding='utf-8')
        return json.loads(self.receipt_text)

    def _assert_no_text(self, receipt, marker=MARKER):
        self.assertNotIn(marker, self.receipt_text)
        self.assertNotIn('reply_excerpt', receipt)

    def test_short_plain_text_reply_shows_cause_class_without_text(self):
        # (a) A short plain-text reply is refused as non-JSONL. The diagnostic
        # names the cause class (plain text), never the reply itself.
        reply = MARKER + ' short reply'
        exc = self._refusal(reply.encode('utf-8'), _pass_screen)

        receipt = self._receipt(exc)
        self.assertEqual(receipt['failure_reason'], 'OUTPUT_NOT_JSONL')
        self.assertTrue(receipt['excerpt'].startswith('OUTPUT_SHAPE '))
        diagnostic = receipt['reply_diagnostic']
        self.assertEqual(diagnostic['kind'], 'text')
        self.assertEqual(diagnostic['first'], 'letter')
        self.assertEqual(diagnostic['bytes'], len(reply.encode('utf-8')))
        self.assertEqual(diagnostic['lines'], 1)
        self.assertIs(diagnostic['truncated'], False)
        self._assert_no_text(receipt)

    def test_jsonl_stream_with_bad_line_keeps_content_free_diagnostic(self):
        # The PR #2484 form: a readable agent message followed by a bad line.
        reply = MARKER + ' plain reviewer reply'
        raw = base._codex_event_stream(reply) + b'\nnot jsonl\n'
        exc = self._refusal(raw, _pass_screen)

        receipt = self._receipt(exc)
        self.assertEqual(receipt['failure_reason'], 'OUTPUT_NOT_JSONL')
        self.assertTrue(receipt['excerpt'].startswith('REPLY_SHAPE '))
        self.assertEqual(receipt['reply_diagnostic']['kind'], 'text')
        self._assert_no_text(receipt)

    def test_long_reply_diagnostic_is_bounded_without_text(self):
        # (b) A long reply is marked truncated; only counts are stored.
        reply = '\n'.join('line %02d %s' % (index, MARKER) for index in range(60))
        exc = self._refusal(reply.encode('utf-8'), _pass_screen)

        receipt = self._receipt(exc)
        diagnostic = receipt['reply_diagnostic']
        self.assertEqual(diagnostic['lines'], 60)
        self.assertIs(diagnostic['truncated'], True)
        self.assertLessEqual(len(json.dumps(receipt).encode('utf-8')), 4096)
        self._assert_no_text(receipt)

    def test_credential_like_reply_is_not_persisted(self):
        # (c) The screen refusal path stands in for the sensitive detector; the
        # test itself never contains a credential-shaped string (test data rule).
        reply = 'synthetic-sensitive-value\n' + MARKER
        exc = self._refusal(reply.encode('utf-8'), _refuse_screen)

        receipt = self._receipt(exc)
        self.assertEqual(receipt['label'], review_dispatch.REVIEW_OUTPUT_WITHHELD)
        self.assertNotIn('excerpt', receipt)
        self.assertNotIn('reply_diagnostic', receipt)
        self.assertNotIn(MARKER, json.dumps(receipt))
        self.assertNotIn('synthetic-sensitive-value', json.dumps(receipt))

    def test_undecodable_reply_keeps_a_fixed_word_and_no_text(self):
        # (d) Undecodable bytes cannot be screened, so only a word is stored.
        exc = self._refusal(
            b'\xff\xfe', lambda *_args: self.fail('the screen was called'))

        receipt = self._receipt(exc)
        self.assertEqual(receipt['label'], review_dispatch.REVIEW_OUTPUT_UNDECODABLE)
        self.assertNotIn('excerpt', receipt)
        self.assertNotIn('reply_diagnostic', receipt)

    def test_valid_jsonl_verdict_is_unchanged_and_writes_no_receipt(self):
        # (e) A valid JSONL verdict keeps the existing success path.
        task = self.store.get(self.task_id)
        raw = base._codex_event_stream(
            base._codex_review_text(
                run_id=task['run_id'],
                contract=task['task_binding']['contract_digest']),
            run_id=task['run_id'])

        def runner(**_kw):
            return review_dispatch._parse_codex_review(
                raw, task_id=self.task_id, run_id=task['run_id'],
                contract_digest=task['task_binding']['contract_digest'], head=HEAD)

        result = self._invoke_refusal(runner=runner)
        self.assertEqual(result['verdict'], 'PASS')
        self.assertFalse(self.receipt_dir.exists())

    def test_receipt_is_not_a_verdict_and_nothing_is_posted(self):
        # (f) Even with the new field the receipt is not a verdict and no
        # GitHub post is attempted (the module fixtures fail any post).
        reply = MARKER + ' short reply'
        exc = self._refusal(reply.encode('utf-8'), _pass_screen)

        receipt = self._receipt(exc)
        self.assertIs(receipt['not_a_verdict'], True)
        self.assertEqual(receipt['failure_reason'], 'OUTPUT_NOT_JSONL')
        self.assertIs(type(exc.review_refusal['receipt_path']), str)
        claim = self.store.get(self.task_id)['review_dispatch_claim'][0]
        self.assertEqual(claim['state'], 'UNKNOWN')
        self.assertNotIn('review_result', claim)

    def test_arbitrary_private_prose_is_never_persisted(self):
        # (g) Private-looking prose with no credential pattern leaves no words.
        reply = ('A private-looking narrative about a named person and place.\n'
                 'It mentions ' + MARKER + ' only as a test marker.')
        exc = self._refusal(reply.encode('utf-8'), _pass_screen)

        receipt = self._receipt(exc)
        self.assertEqual(receipt['reply_diagnostic']['kind'], 'text')
        for phrase in ('narrative', 'named person', MARKER):
            self.assertNotIn(phrase, self.receipt_text)
        self._assert_no_text(receipt)

    def test_json_reply_diagnostic_keeps_only_fixed_key_names(self):
        # A JSON-looking reply may name arbitrary keys; only fixed verdict key
        # names are kept, so a private key name cannot reach the receipt.
        reply = json.dumps({'task_id': 't', 'private-note': MARKER})
        raw = base._codex_event_stream(reply, plain=True) + b'\nnot jsonl\n'
        exc = self._refusal(raw, _pass_screen)

        receipt = self._receipt(exc)
        diagnostic = receipt['reply_diagnostic']
        self.assertEqual(diagnostic['kind'], 'json_object')
        self.assertEqual(diagnostic['keys'], ['task_id'])
        self.assertNotIn('private-note', self.receipt_text)
        self._assert_no_text(receipt)

    def test_diagnostic_holds_only_fixed_words_and_counts(self):
        # The schema itself proves no free reply text can reach the receipt.
        reply = MARKER + ' short reply'
        exc = self._refusal(reply.encode('utf-8'), _pass_screen)
        diagnostic = self._receipt(exc)['reply_diagnostic']

        self.assertIn(diagnostic['kind'], _FIXED_KINDS)
        self.assertIn(diagnostic['first'], _FIXED_FIRST)
        self.assertIs(type(diagnostic['bytes']), int)
        self.assertIs(type(diagnostic['lines']), int)
        self.assertIs(type(diagnostic['truncated']), bool)
        self.assertEqual(set(diagnostic) - {
            'kind', 'first', 'bytes', 'lines', 'truncated', 'keys',
            'status_words', 'verdict_marker', 'binding_line'}, set())
        for word in diagnostic.get('status_words', ()):
            self.assertIn(word, _FIXED_STATUS)


if __name__ == '__main__':
    unittest.main()
