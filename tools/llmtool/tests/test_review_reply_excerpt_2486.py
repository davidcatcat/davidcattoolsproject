"""#2486: a refused plain reply leaves screened evidence on the refusal receipt.

Every receipt still records the fixed-word shape and stays ``not_a_verdict``.
The new ``reply_excerpt`` field holds the bounded reply text only after the
existing sensitive-content screen passes. A screen refusal or undecodable
bytes keep a fixed refusal word and no text.
"""
import json
import unittest
from pathlib import Path

from unittest.mock import patch

from llmtool import review_dispatch, task_submission_cli
from llmtool.errors import LLMToolError
import test_review_dispatch as base
from test_review_dispatch import HEAD, setUpModule, tearDownModule  # noqa: F401


def _pass_screen(text, _limit):
    return text


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
        return json.loads(path.read_text(encoding='utf-8'))

    def test_short_plain_reply_keeps_shape_and_screened_excerpt(self):
        # (a) A short plain-text reply is refused as non-JSONL.
        reply = 'short synthetic reply'
        exc = self._refusal(reply.encode('utf-8'), _pass_screen)

        receipt = self._receipt(exc)
        self.assertEqual(receipt['failure_reason'], 'OUTPUT_NOT_JSONL')
        self.assertEqual(receipt['excerpt'], exc.review_refusal['excerpt'])
        self.assertTrue(receipt['excerpt'].startswith('OUTPUT_SHAPE '))
        self.assertEqual(receipt['reply_excerpt'], reply)
        self.assertNotIn('reply_excerpt_truncated', receipt)

    def test_jsonl_stream_with_plain_reply_keeps_shape_and_excerpt(self):
        # The PR #2484 form: a readable agent message followed by a bad line.
        reply = 'synthetic plain reviewer reply'
        raw = base._codex_event_stream(reply) + b'\nnot jsonl\n'
        exc = self._refusal(raw, _pass_screen)

        receipt = self._receipt(exc)
        self.assertEqual(receipt['failure_reason'], 'OUTPUT_NOT_JSONL')
        self.assertTrue(receipt['excerpt'].startswith('REPLY_SHAPE '))
        self.assertEqual(receipt['reply_excerpt'], reply)

    def test_long_reply_excerpt_is_bounded_and_marked_truncated(self):
        # (b) The excerpt is cut to the bound and marked truncated.
        reply = '\n'.join('line %02d synthetic reply content' % index
                          for index in range(60))
        exc = self._refusal(reply.encode('utf-8'), _pass_screen)

        receipt = self._receipt(exc)
        excerpt = receipt['reply_excerpt']
        self.assertLessEqual(len(excerpt.encode('utf-8')), 2048)
        self.assertLessEqual(len(excerpt.splitlines()), 40)
        self.assertTrue(reply.startswith(excerpt))
        self.assertIs(receipt['reply_excerpt_truncated'], True)
        self.assertTrue(receipt['excerpt'].startswith('OUTPUT_SHAPE '))

    def test_screened_reply_keeps_a_fixed_word_and_no_text(self):
        # (c) The refusal-flow test injects the screen; its refusal keeps no text.
        reply = 'synthetic reply refused by the screen'

        def refuse(_text, _limit):
            raise LLMToolError('sensitive')

        exc = self._refusal(reply.encode('utf-8'), refuse)

        receipt = self._receipt(exc)
        self.assertEqual(receipt['label'], review_dispatch.REVIEW_OUTPUT_WITHHELD)
        self.assertNotIn('excerpt', receipt)
        self.assertNotIn('reply_excerpt', receipt)
        self.assertNotIn(reply, json.dumps(receipt))

    def test_undecodable_reply_keeps_a_fixed_word_and_no_text(self):
        # (d) Undecodable bytes cannot be screened, so only a word is stored.
        exc = self._refusal(
            b'\xff\xfe', lambda *_args: self.fail('the screen was called'))

        receipt = self._receipt(exc)
        self.assertEqual(receipt['label'], review_dispatch.REVIEW_OUTPUT_UNDECODABLE)
        self.assertNotIn('excerpt', receipt)
        self.assertNotIn('reply_excerpt', receipt)

    def test_valid_jsonl_verdict_is_unchanged_and_writes_no_receipt(self):
        # (e) A valid JSONL verdict keeps the existing success path.
        task = self.store.get(self.task_id)
        raw = base._codex_event_stream(base._codex_review_text(
            run_id=task['run_id'],
            contract=task['task_binding']['contract_digest']))

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
        reply = 'short synthetic reply'
        exc = self._refusal(reply.encode('utf-8'), _pass_screen)

        receipt = self._receipt(exc)
        self.assertIs(receipt['not_a_verdict'], True)
        self.assertEqual(receipt['failure_reason'], 'OUTPUT_NOT_JSONL')
        self.assertIs(type(exc.review_refusal['receipt_path']), str)
        claim = self.store.get(self.task_id)['review_dispatch_claim'][0]
        self.assertEqual(claim['state'], 'UNKNOWN')
        self.assertNotIn('review_result', claim)


if __name__ == '__main__':
    unittest.main()
