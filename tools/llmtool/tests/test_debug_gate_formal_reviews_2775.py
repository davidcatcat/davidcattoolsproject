"""#2775: the High Debug gate counts formally reviewed heads, not every run.

#1502 D1 (O4): AI Debug is first required after the third effective review
repair receives a formal FAIL. A PM_SPEC_CORRECTION continuation that fixes an
EC2 or CI test failure is still a counted run, but it answers no formal review,
so it must not by itself make repair #4 demand a root cause (#2770, #2771).
The repair_max limit is unchanged. Records are synthetic; IDs are fake.
"""

import unittest
from types import SimpleNamespace

from llmtool import repair_policy
from llmtool import task_submission_cli
from llmtool.errors import LLMToolError

from test_task_delegate_repair_cap import _no_high_debug, _patch_trust_and_binding


_POLICY = repair_policy.load_policy()
_EXECUTOR = 'executor-d6'
_WITHOUT_DEBUG = repair_policy.REVIEW_REPAIRS_WITHOUT_DEBUG


def _head(number):
    return f'{number:040x}'


def _ec2_event(number):
    return {'source': 'EC2_REMOTE_TEST', 'source_sha': _head(number), 'event_id': f'ec2-{number}'}


def _reviewer_event(number, **extra):
    return dict({'source': 'REVIEWER', 'source_sha': _head(number),
                 'event_id': f'rv-{number}'}, **extra)


def _claim(number, *, state='COMPLETED', version=1, **extra):
    return dict({'pr': 2770, 'head_sha': _head(number), 'version': version,
                 'state': state, 'executor_task': _EXECUTOR}, **extra)


class _Store:
    """count runs on one D6; each run may carry claims and events."""

    def __init__(self, count, *, events=None, claims=None):
        self.records = {}
        for number in range(count):
            self.records[f'{number:032x}'] = {
                'completion': {'executor_task_id': _EXECUTOR},
                'developer': {'state': 'DEVELOPER_RESPONDED',
                              'created_at': '2026-11-26T00:00:00Z'},
                'completion_events': list((events or {}).get(number, [])),
                'review_dispatch_claim': list((claims or {}).get(number, [])),
            }
        self.writes = []

    def completion_d6_task_ids(self, executor_task_id):
        return sorted(task_id for task_id, record in self.records.items()
                      if record['completion']['executor_task_id'] == executor_task_id)

    def get(self, task_id):
        return self.records[task_id]

    def update(self, *_a, **_kw):
        self.writes.append('update')

    def compare_and_update(self, *_a, **_kw):
        self.writes.append('compare_and_update')


def _tested(count):
    """Every run left an EC2 receipt event, as the real flow does."""
    return {number: [_ec2_event(number)] for number in range(count)}


class FormalReviewedHeadsTests(unittest.TestCase):
    def test_counts_distinct_formal_heads_from_claims_and_events(self):
        records = [
            {'completion_events': [_ec2_event(0), _reviewer_event(1)],
             'review_dispatch_claim': [_claim(1), _claim(1, version=2), _claim(2, state='DISPATCHED')]},
            {'completion_events': [_reviewer_event(3, review_kind='formal')],
             'review_dispatch_claim': []},
        ]
        self.assertEqual(task_submission_cli._formal_reviewed_heads(records), 3)

    def test_ignores_security_ruling_and_unlaunched_claims(self):
        records = [{'completion_events': [_ec2_event(0), _reviewer_event(9, review_kind='security')],
                    'review_dispatch_claim': [
                        _claim(1, review_kind='security'),
                        _claim(2, purpose='dispute_ruling'),
                        _claim(3, state='CLAIMED'),
                        _claim(4, state='UNKNOWN')]}]
        self.assertEqual(task_submission_cli._formal_reviewed_heads(records), 0)

    def test_without_test_evidence_or_with_malformed_data_is_none(self):
        for records in (
                [{'completion_events': [], 'review_dispatch_claim': []}],
                [{'completion_events': [_ec2_event(0)], 'review_dispatch_claim': ['bad']}],
                [{'completion_events': [_ec2_event(0), 'bad'], 'review_dispatch_claim': []}],
                [{'completion_events': [_ec2_event(0)],
                  'review_dispatch_claim': [_claim(1, head_sha=None)]}],
                [{'completion_events': {}, 'review_dispatch_claim': []}],
                ['not-a-record'],
                None):
            with self.subTest(records=records):
                self.assertIsNone(task_submission_cli._formal_reviewed_heads(records))


class DebugGateTests(unittest.TestCase):
    def setUp(self):
        _patch_trust_and_binding(self)

    def _check(self, store):
        return task_submission_cli._check_delegate_repair_cap(_no_high_debug(), store)

    def test_test_repairs_without_formal_reviews_need_no_debug(self):
        # #2770/#2771 shape: the original run and three PSC test repairs, no review yet.
        store = _Store(_WITHOUT_DEBUG + 1, events=_tested(_WITHOUT_DEBUG + 1))
        self.assertIsNone(self._check(store))
        self.assertEqual(store.writes, [])

    def test_three_reviewed_heads_and_extra_test_repairs_need_no_debug(self):
        count = _WITHOUT_DEBUG + 2
        events = _tested(count)
        for number in range(_WITHOUT_DEBUG):
            events[number].append(_reviewer_event(number))
        self.assertIsNone(self._check(_Store(count, events=events)))

    def test_four_reviewed_heads_still_require_debug(self):
        count = _WITHOUT_DEBUG + 1
        events = _tested(count)
        claims = {number: [_claim(number)] for number in range(count)}
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_HIGH_DEBUG_TASK_REQUIRED$'):
            self._check(_Store(count, events=events, claims=claims))

    def test_reviews_counted_from_events_alone_still_require_debug(self):
        count = _WITHOUT_DEBUG + 1
        events = _tested(count)
        for number in range(count):
            events[number].append(_reviewer_event(number))
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_HIGH_DEBUG_TASK_REQUIRED$'):
            self._check(_Store(count, events=events))

    def test_no_test_evidence_keeps_the_old_rule(self):
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_HIGH_DEBUG_TASK_REQUIRED$'):
            self._check(_Store(_WITHOUT_DEBUG + 1))

    def test_malformed_claim_keeps_the_old_rule(self):
        count = _WITHOUT_DEBUG + 1
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_HIGH_DEBUG_TASK_REQUIRED$'):
            self._check(_Store(count, events=_tested(count), claims={0: ['bad']}))

    def test_repair_max_still_counts_every_run(self):
        count = 1 + _POLICY['repair_max']
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_REPAIR_LIMIT_REACHED$'):
            self._check(_Store(count, events=_tested(count)))

    def test_a_supplied_repair_packet_root_cause_is_still_checked(self):
        count = _WITHOUT_DEBUG + 1
        args = _no_high_debug(repair_packet={'high_debug_report_sha256': '0' * 64})
        with self.assertRaises(LLMToolError):
            task_submission_cli._check_delegate_repair_cap(
                args, _Store(count, events=_tested(count)))

    def test_chain_count_uses_the_same_rule(self):
        count = _WITHOUT_DEBUG + 1
        members = {}
        for number in range(count):
            members[f'{number:032x}'] = {
                'completion': {'executor_task_id': _EXECUTOR},
                'developer': {'state': 'DEVELOPER_RESPONDED',
                              'created_at': '2026-11-26T00:00:00Z'},
                'completion_events': [_ec2_event(number)],
                'review_dispatch_claim': [],
            }
        view = {'members': members}
        args = _no_high_debug(task_id='f' * 32)
        store = SimpleNamespace(get=lambda task_id: members[task_id])
        self.assertIsNone(task_submission_cli._check_chain_repair_count(
            args, store, None, _POLICY, view))
        for member in members.values():
            member['review_dispatch_claim'] = [_claim(int(next(
                k for k, v in members.items() if v is member), 16))]
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_HIGH_DEBUG_TASK_REQUIRED$'):
            task_submission_cli._check_chain_repair_count(args, store, None, _POLICY, view)


if __name__ == '__main__':
    unittest.main()
