"""#1907: a review refusal receipt holds a fixed-word shape, never model text."""
import json
import unittest

from unittest.mock import patch

from llmtool import review_dispatch, task_submission_cli
from llmtool.errors import LLMToolError
import test_review_dispatch as base
from test_review_dispatch import _codex_event_stream, HEAD, setUpModule, tearDownModule  # noqa: F401

MARKER = 'synthetic-marker reply text'


class RefusalReceiptShapeTests(unittest.TestCase):
    # Reuse the receipt fixture (real store, private receipt directory).
    setUp = base.ReviewRefusalReceiptTests.setUp
    tearDown = base.ReviewRefusalReceiptTests.tearDown
    _invoke_refusal = base.ReviewRefusalReceiptTests._invoke_refusal

    def test_receipt_and_refusal_hold_no_model_text(self):
        self.check_reply(MARKER, 'REPLY_SHAPE bytes=27 lines=1 first=letter')

    def test_reply_that_imitates_an_output_shape_is_still_shaped(self):
        self.check_reply('OUTPUT_SHAPE synthetic leaked words',
                         'REPLY_SHAPE bytes=35 lines=1 first=letter')

    def test_unicode_digit_imitation_is_still_shaped(self):
        # Security v91 F1: Arabic-Indic one is a Unicode decimal, not an ASCII digit.
        self.check_reply('OUTPUT_SHAPE no_bad_line lines=\u0661',
                         'REPLY_SHAPE bytes=33 lines=1 first=letter')

    def test_output_shape_accepts_ascii_digits_only(self):
        self.assertTrue(review_dispatch._OUTPUT_SHAPE.fullmatch(
            'OUTPUT_SHAPE line=2 of 10 bytes=345 first=brace'))
        for digit in ('\u0661', '\uff13', '\u07c1', '\u0e52'):
            for text in ('OUTPUT_SHAPE utf8=invalid bytes=' + digit,
                         'OUTPUT_SHAPE no_bad_line lines=' + digit,
                         'OUTPUT_SHAPE line=1 of %s bytes=3 first=letter' % digit):
                self.assertIsNone(review_dispatch._OUTPUT_SHAPE.fullmatch(text), text)

    def check_reply(self, reply, shape):
        raw = _codex_event_stream(reply)
        task = self.store.get(self.task_id)

        def runner(**_kw):
            return review_dispatch._parse_codex_review(
                raw, task_id=self.task_id, run_id=task['run_id'],
                contract_digest=task['task_binding']['contract_digest'], head=HEAD)

        # The screen passes the reply text through unchanged, as it does for permitted text.
        with patch.object(task_submission_cli, '_screen_text', side_effect=lambda t, _n: t), \
                self.assertRaises(LLMToolError) as raised:
            self._invoke_refusal(runner=runner)
        refusal = raised.exception.review_refusal
        self.assertEqual(refusal['excerpt'], shape)
        text = refusal['receipt_path'].read_text(encoding='utf-8')
        self.assertNotIn('synthetic', text)
        self.assertEqual(json.loads(text)['excerpt'], refusal['excerpt'])

    def test_shape_words_only(self):
        self.assertEqual(review_dispatch._reply_shape(' {x\ny'), 'REPLY_SHAPE bytes=5 lines=2 first=brace')
        self.assertEqual(review_dispatch._reply_shape(''), 'REPLY_SHAPE bytes=0 lines=1 first=blank')
        self.assertEqual(review_dispatch._reply_shape(None), 'REPLY_SHAPE unavailable')


if __name__ == '__main__':
    unittest.main()
