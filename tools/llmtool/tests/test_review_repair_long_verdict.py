# Bounded repair tests for verdicts with blocking findings beyond character 2000.
# TEST DATA RULE (Owner #763 A): No credential-shaped strings appear anywhere.

import hashlib
import json
import unittest
from unittest.mock import Mock, patch

from llmtool import review_repair, task_store, task_submission_cli
from llmtool.errors import LLMToolError

import task_fixtures
from test_review_repair import (
    EXECUTOR,
    HEAD,
    PR,
    RUN,
    TASK,
    ReviewRepairTests,
    TempTaskStore,
    _comment,
    _record,
    setUpModule,
    tearDownModule,
)

if not hasattr(TempTaskStore, 'create'):
    def _temp_task_store_create(self, task):
        return self.put(task)
    TempTaskStore.create = _temp_task_store_create


class ReviewRepairLongVerdictTests(ReviewRepairTests):
    """Test suite covering long verdict handling and findings beyond 2000 chars."""

    def test_constants_and_refusal_codes_exposed(self):
        self.assertGreaterEqual(review_repair.MAX_VERDICT_CHARS, 16384)
        self.assertLessEqual(review_repair.MAX_VERDICT_CHARS, 65536)
        self.assertEqual(review_repair.MAX_VERDICT_BODY_CHARS, review_repair.MAX_VERDICT_CHARS)
        self.assertEqual(review_repair.REVIEW_VERDICT_TOO_LONG, 'REVIEW_VERDICT_TOO_LONG')
        self.assertEqual(review_repair.VERDICT_TOO_LONG_CODE, 'REVIEW_VERDICT_TOO_LONG')

    def test_failed_event_returns_exactly_two_values_packet_and_event(self):
        task = self.store.get(TASK)
        result = review_repair._failed_event(
            task, executor_task=EXECUTOR, pr_number=PR, github_get=lambda _path: [_comment()]
        )
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        packet, event = result
        self.assertIsInstance(packet, dict)
        self.assertIsInstance(event, dict)
        self.assertEqual(packet['failed_head'], HEAD)
        self.assertEqual(packet['findings_count'], 1)
        self.assertEqual(packet['version'], 1)
        self.assertLessEqual(len(packet['reviewer_text_excerpt_untrusted_data']), 2000)

    def test_blocking_finding_after_2000_chars_proceeds_with_repair(self):
        # Header + low/advisory findings + filler to exceed 2000 chars, then High finding.
        prefix = (
            f"<!-- activity: type=review_verdict actor=Claude target=PM status=FIX -->\n"
            f"Verdict: FAIL | Version: v1 | HEAD: {HEAD}\n"
            f"Finding: F1 [Low] src/service.py:10 code style guideline\n"
            f"Finding: F2 [Advisory] src/service.py:20 consider splitting function\n"
        )
        filler = "# Notes and detailed diagnostic trace\n" * 60
        body_prefix = prefix + filler
        self.assertGreater(len(body_prefix), 2000)
        self.assertNotIn("Finding: F3", body_prefix[:2000])

        body = body_prefix + "Finding: F3 [High] src/service.py:30 missing null check causes crash\n"
        comment = {
            'id': 9,
            'body': body,
            'created_at': '2026-10-27T01:00:00Z',
            'updated_at': '2026-10-27T01:00:00Z',
        }

        result = self.dispatch(github_get=lambda _path: [comment])
        self.assertEqual(result['next_action'], 'review-dispatch --version 2')
        self.assertEqual(result['claim']['attempt'], 1)
        self.assertEqual(result['claim']['state'], 'DISPATCHED')

        packet = self.calls[0]['packet']
        self.assertEqual(packet['verdict_comment_id'], 9)
        self.assertEqual(packet['findings_count'], 3)
        self.assertLessEqual(len(packet['reviewer_text_excerpt_untrusted_data']), 2000)
        self.assertEqual(packet['reviewer_text_excerpt_untrusted_data'], body[:2000])

        encoded = json.dumps(packet, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
        self.assertLessEqual(len(encoded.encode('utf-8')), 8192)
        self.assertEqual(task_submission_cli._validate_repair_packet(packet), packet)

    def test_all_low_or_advisory_findings_wherever_they_appear_are_refused(self):
        prefix = (
            f"<!-- activity: type=review_verdict actor=Claude target=PM status=FIX -->\n"
            f"Verdict: FAIL | Version: v1 | HEAD: {HEAD}\n"
            f"Finding: F1 [Low] src/service.py:10 code style guideline\n"
        )
        filler = "# Non-blocking context and review notes\n" * 65
        body = prefix + filler + "Finding: F2 [Advisory] src/service.py:20 consider splitting function\n"
        self.assertGreater(len(body), 2500)

        comment = {
            'id': 9,
            'body': body,
            'created_at': '2026-10-27T01:00:00Z',
            'updated_at': '2026-10-27T01:00:00Z',
        }

        self._assert_refused('REPAIR_NO_BLOCKING_FINDING', github_get=lambda _path: [comment])
        self.assertNotIn('review_repair_claims', self.store.get(TASK))
        self.assertEqual(len(self.calls), 0)

    def test_arbiter_withdrawn_blocking_finding_after_2000_chars(self):
        prefix = (
            f"<!-- activity: type=review_verdict actor=Claude target=PM status=FIX -->\n"
            f"Verdict: FAIL | Version: v1 | HEAD: {HEAD}\n"
            f"Finding: F1 [Low] src/service.py:10 code style guideline\n"
        )
        filler = "# Additional discussion and logs\n" * 60
        body = prefix + filler + "Finding: F3 [High] src/service.py:30 missing null check causes crash\n"

        comment = {
            'id': 9,
            'body': body,
            'created_at': '2026-10-27T01:00:00Z',
            'updated_at': '2026-10-27T01:00:00Z',
        }

        # Case A: F3 was withdrawn by an arbiter ruling -> refused REPAIR_NO_BLOCKING_FINDING
        record = self.store.get(TASK)
        record['review_disputes'] = [{
            'pr': PR,
            'head_sha': HEAD,
            'state': 'RULED',
            'finding_id': 'F3',
            'ruling': {'outcome': 'WITHDRAWN', 'head_sha': HEAD},
        }]
        self.store = TempTaskStore(self.temp.name, record)

        self._assert_refused('REPAIR_NO_BLOCKING_FINDING', github_get=lambda _path: [comment])
        self.assertNotIn('review_repair_claims', self.store.get(TASK))
        self.assertEqual(len(self.calls), 0)

        # Case B: F4 [Blocker] also appears after character 2000 and is not withdrawn -> proceeds
        body_with_f4 = body + "Finding: F4 [Blocker] src/service.py:40 unhandled error loop\n"
        comment_f4 = {
            'id': 10,
            'body': body_with_f4,
            'created_at': '2026-10-27T01:05:00Z',
            'updated_at': '2026-10-27T01:05:00Z',
        }
        result = self.dispatch(github_get=lambda _path: [comment_f4])
        self.assertEqual(result['claim']['attempt'], 1)
        self.assertEqual(result['claim']['state'], 'DISPATCHED')
        self.assertEqual(len(self.calls), 1)

    def test_verdict_body_longer_than_maximum_is_refused(self):
        prefix = (
            f"<!-- activity: type=review_verdict actor=Claude target=PM status=FIX -->\n"
            f"Verdict: FAIL | Version: v1 | HEAD: {HEAD}\n"
            f"Finding: F1 [High] src/service.py:10 buffer overflow defect\n"
        )
        overshoot = review_repair.MAX_VERDICT_CHARS + 100 - len(prefix)
        body = prefix + ("x" * overshoot)
        self.assertGreater(len(body), review_repair.MAX_VERDICT_CHARS)

        comment = {
            'id': 9,
            'body': body,
            'created_at': '2026-10-27T01:00:00Z',
            'updated_at': '2026-10-27T01:00:00Z',
        }

        self._assert_refused(
            review_repair.REVIEW_VERDICT_TOO_LONG,
            github_get=lambda _path: [comment],
        )
        self.assertNotIn('review_repair_claims', self.store.get(TASK))
        self.assertEqual(len(self.calls), 0)

    def test_review_gate_directly_checks_verdict_body_and_length(self):
        task = self.store.get(TASK)
        attempts = []
        policy = {'repair_max': 2, 'review_fail_max': 3}
        packet = {
            'task': TASK,
            'pr': PR,
            'executor_task': EXECUTOR,
            'failed_head': HEAD,
            'version': 1,
            'verdict_comment_id': 9,
            'findings_count': 1,
            'reviewer_text_excerpt_untrusted_data': 'Finding: F1 [Low] a.py:1 style\n',
        }

        # 1. Fallback to excerpt when verdict_body is None has only Low -> refuses
        with self.assertRaises(LLMToolError) as caught:
            review_repair._review_gate(task, attempts, packet, f'pr-{PR}', policy)
        self.assertEqual(caught.exception.review_repair_code, 'REPAIR_NO_BLOCKING_FINDING')

        # 2. verdict_body passed with High finding -> passes
        long_body = (
            "Finding: F1 [Low] a.py:1 style\n"
            + ("# filler line\n" * 150)
            + "Finding: F2 [High] b.py:2 bounds error\n"
        )
        self.assertGreater(len(long_body), 2000)
        gate_result = review_repair._review_gate(
            task, attempts, packet, f'pr-{PR}', policy, verdict_body=long_body
        )
        self.assertIsNone(gate_result)  # attempt 1 has no previous used repairs, returns None

        # 3. verdict_body exceeding maximum -> refuses REVIEW_VERDICT_TOO_LONG
        huge_body = "Finding: F1 [High] a.py:1 bug\n" + ("y" * (review_repair.MAX_VERDICT_CHARS + 10))
        with self.assertRaises(LLMToolError) as caught:
            review_repair._review_gate(
                task, attempts, packet, f'pr-{PR}', policy, verdict_body=huge_body
            )
        self.assertEqual(caught.exception.review_repair_code, review_repair.REVIEW_VERDICT_TOO_LONG)

    def test_task_fixtures_create_with_developer(self):
        dev_task = task_fixtures.create_with_developer(self.store, 'agy')
        self.assertEqual(dev_task['developer']['provider_profile'], 'agy')
        self.assertEqual(dev_task['developer']['state'], 'DEVELOPER_PENDING')

    def test_task_fixtures_create_with_developer_long_verdict_gate(self):
        task = task_fixtures.create_with_developer(self.store, 'agy')
        long_body = (
            "Finding: F1 [Low] a.py:1 style\n"
            + ("# filler line\n" * 150)
            + "Finding: F2 [High] b.py:2 bounds error\n"
        )
        packet = {
            'task': task['task_id'],
            'pr': PR,
            'executor_task': EXECUTOR,
            'failed_head': HEAD,
            'version': 1,
            'verdict_comment_id': 9,
            'findings_count': 2,
            'reviewer_text_excerpt_untrusted_data': long_body[:2000],
        }
        policy = {'repair_max': 2, 'review_fail_max': 3}
        gate_result = review_repair._review_gate(
            task, [], packet, f'pr-{PR}', policy, verdict_body=long_body
        )
        self.assertIsNone(gate_result)


if __name__ == '__main__':
    unittest.main()
