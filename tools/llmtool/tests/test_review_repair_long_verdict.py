"""#1686: a bound FAIL verdict's findings are judged in full, not just its excerpt."""

import json
import unittest

import review_trust_fixture
import task_fixtures
from llmtool import review_repair, task_submission_cli

from test_review_repair import (HEAD, PR, TASK, ReviewRepairTests, TempTaskStore,
                                _comment)

_PADDING_LINE = 'Reviewer context line without a finding.\n'
# The eight keys a review-FAIL repair packet keeps; no repair field is added.
_PACKET_KEYS = {
    'task', 'pr', 'executor_task', 'failed_head', 'version',
    'verdict_comment_id', 'findings_count', 'reviewer_text_excerpt_untrusted_data',
}


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def _verdict_body(*segments):
    """The ordinary bound FAIL header followed by the given body segments."""
    marker, verdict = _comment()['body'].splitlines()[:2]
    return marker + '\n' + verdict + '\n' + ''.join(segments)


def _padding(lines):
    return _PADDING_LINE * lines


def _comment_with(body):
    return dict(_comment(), body=body)


class LongVerdictRepairTests(unittest.TestCase):
    """Reuse the existing repair-dispatch helpers without re-running their tests."""

    setUp = ReviewRepairTests.setUp
    tearDown = ReviewRepairTests.tearDown
    dispatch = ReviewRepairTests.dispatch
    _assert_refused = ReviewRepairTests._assert_refused

    def test_blocking_finding_after_the_excerpt_still_admits_a_repair(self):
        body = _verdict_body(
            'Finding: F1 [Low] a.py:1 style\n',
            _padding(100),
            'Finding: F3 [High] a.py:3 late blocker\n')
        self.assertGreater(body.index('Finding: F3'), review_repair.MAX_EXCERPT_CHARS)
        self.assertLessEqual(len(body), review_repair.MAX_VERDICT_BODY_CHARS)
        record = self.store.get(TASK)
        record['developer'] = task_fixtures.developer_assignment(
            'agy', state='DEVELOPER_RESPONDED')
        self.store = TempTaskStore(self.temp.name, record)
        result = self.dispatch(github_get=lambda _path: [_comment_with(body)])
        self.assertEqual(result['claim']['attempt'], 1)
        self.assertEqual(len(self.calls), 1)
        packet = self.calls[0]['packet']
        self.assertEqual(set(packet), _PACKET_KEYS)
        self.assertEqual(task_submission_cli._validate_repair_packet(packet), packet)
        excerpt = packet['reviewer_text_excerpt_untrusted_data']
        self.assertIs(type(excerpt), str)
        self.assertLessEqual(len(excerpt), review_repair.MAX_EXCERPT_CHARS)
        self.assertNotIn('F3', excerpt)
        encoded = json.dumps(packet, ensure_ascii=True, sort_keys=True,
                             separators=(',', ':')).encode('utf-8')
        self.assertLessEqual(len(encoded), 8192)

    def test_low_and_advisory_findings_after_the_excerpt_still_refuse(self):
        body = _verdict_body(
            'Finding: F1 [Low] a.py:1 style\n',
            _padding(100),
            'Finding: F2 [Advisory] a.py:2 note\n')
        self.assertGreater(body.index('Finding: F2'), review_repair.MAX_EXCERPT_CHARS)
        self._assert_refused('REPAIR_NO_BLOCKING_FINDING',
                             github_get=lambda _path: [_comment_with(body)])

    def test_verdict_longer_than_the_maximum_is_refused_and_not_truncated(self):
        self.assertTrue(16384 <= review_repair.MAX_VERDICT_BODY_CHARS <= 65536)
        body = _verdict_body(
            'Finding: F1 [High] a.py:1 blocker\n',
            _padding(1),
            'x' * review_repair.MAX_VERDICT_BODY_CHARS)
        self.assertGreater(len(body), review_repair.MAX_VERDICT_BODY_CHARS)
        self.assertLessEqual(len(body.encode('utf-8')), 65536)
        self._assert_refused('REPAIR_VERDICT_BODY_TOO_LONG',
                             github_get=lambda _path: [_comment_with(body)])

    def test_withdrawn_finding_after_the_excerpt_still_refuses(self):
        # D13 still decides on the full body: a withdrawn late blocker is not a
        # valid FAIL, exactly as for a blocker near the top of the verdict.
        body = _verdict_body(
            'Finding: F1 [Low] a.py:1 style\n',
            _padding(100),
            'Finding: F3 [High] a.py:3 late blocker\n')
        record = self.store.get(TASK)
        record['review_disputes'] = [{
            'pr': PR, 'head_sha': HEAD, 'state': 'RULED', 'finding_id': 'F3',
            'ruling': {'outcome': 'WITHDRAWN', 'head_sha': HEAD},
        }]
        self.store = TempTaskStore(self.temp.name, record)
        self._assert_refused('REPAIR_NO_BLOCKING_FINDING',
                             github_get=lambda _path: [_comment_with(body)])


if __name__ == '__main__':
    unittest.main()
