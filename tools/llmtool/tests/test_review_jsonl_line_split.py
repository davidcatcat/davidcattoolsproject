"""#1893: a raw U+2028 inside a Codex JSONL event does not split the event."""
import json
import unittest

from llmtool import review_dispatch

TASK = 'a' * 32
RUN = 'c' * 32
DIGEST = 'd' * 64
HEAD = 'b' * 40


def _review_text():
    return ('<!-- activity: type=review_verdict actor=Codex target=PM status=PASS -->\n'
            f'Verdict: PASS | Version: v1 | HEAD: {HEAD}\n'
            'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n'
            f'LLMTOOL-REVIEW-BINDING v1 task={TASK} run={RUN} '
            f'contract={DIGEST} head={HEAD}')


def _raw(separator):
    verdict = {'task_id': TASK, 'run_id': RUN, 'head': HEAD, 'verdict': 'PASS',
               'review': _review_text()}
    events = [
        {'type': 'thread.started', 'thread_id': '0123456789abcdef'},
        {'type': 'item.completed', 'item': {
            'type': 'agent_message', 'text': 'first progress message'}},
        {'type': 'item.completed', 'item': {
            'type': 'command_execution',
            'aggregated_output': 'line one' + separator + 'line two'}},
        {'type': 'item.completed', 'item': {
            'type': 'agent_message', 'text': json.dumps(verdict)}},
    ]
    # ensure_ascii=False keeps the separator raw, as Codex writes it.
    return ('\n'.join(json.dumps(event, ensure_ascii=False) for event in events)
            + '\n').encode('utf-8')


class RawLineSeparatorTests(unittest.TestCase):
    def test_raw_unicode_line_separators_inside_a_json_string_parse(self):
        for separator in ('\u2028', '\u2029', '\x85'):
            with self.subTest(separator=hex(ord(separator))):
                raw = _raw(separator)
                result = review_dispatch._parse_codex_review(
                    raw, task_id=TASK, run_id=RUN, contract_digest=DIGEST, head=HEAD)
                self.assertEqual(result['verdict'], 'PASS')
                self.assertEqual(result['thread_id'], '0123456789abcdef')
                self.assertEqual(result['review_text'], _review_text())
                self.assertEqual(review_dispatch._codex_final_message(raw),
                                 json.loads(raw.decode('utf-8').split('\n')[3])['item']['text'])
                self.assertEqual(review_dispatch._jsonl_shape(raw),
                                 'OUTPUT_SHAPE no_bad_line lines=4')


if __name__ == '__main__':
    unittest.main()
