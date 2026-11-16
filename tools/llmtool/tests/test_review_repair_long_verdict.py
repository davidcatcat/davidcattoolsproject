# Bounded repair tests for verdicts with blocking findings beyond character 2000.
# TEST DATA RULE (Owner #763 A): No credential-shaped strings appear anywhere.

import hashlib
import json
import unittest
from unittest.mock import Mock, patch

from llmtool import review_repair, review_result, task_store, task_submission_cli
from llmtool.errors import LLMToolError

import task_fixtures
from test_review_repair import (
    CONTRACT,
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
        self.assertEqual(review_repair.MAX_VERDICT_BYTES, 65536)
        self.assertEqual(review_repair.REVIEW_RESULT_SIZE_LIMIT, 65536)
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

    def test_multibyte_verdict_exceeding_bytes_limit_refuses_repair(self):
        prefix = (
            f"<!-- activity: type=review_verdict actor=Claude target=PM status=FIX -->\n"
            f"Verdict: FAIL | Version: v1 | HEAD: {HEAD}\n"
            f"Finding: F1 [High] src/service.py:10 defect description\n"
        )
        # Using 3-byte UTF-8 character '\u4e16'.
        # 25000 chars * 3 bytes = 75000 bytes > 65536 bytes (review_result size limit),
        # but 25000 + len(prefix) chars is well within MAX_VERDICT_CHARS (65536).
        body = prefix + ("\u4e16" * 25000)
        self.assertLessEqual(len(body), review_repair.MAX_VERDICT_CHARS)
        self.assertGreater(len(body.encode('utf-8')), review_repair.MAX_VERDICT_BYTES)

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

    def test_multibyte_verdict_not_older_than_candidate_refuses_repair(self):
        # Candidate verdict: older valid bound FAIL verdict (comment id 8 at 01:00:00Z)
        comment_candidate = {
            'id': 8,
            'body': (
                f"<!-- activity: type=review_verdict actor=Claude target=PM status=FIX -->\n"
                f"Verdict: FAIL | Version: v1 | HEAD: {HEAD}\n"
                f"Finding: F1 [High] src/service.py:10 earlier finding\n"
            ),
            'created_at': '2026-10-27T01:00:00Z',
            'updated_at': '2026-10-27T01:00:00Z',
        }
        # Newer bound verdict (comment id 9 at 01:05:00Z) whose UTF-8 bytes exceed limit
        prefix = (
            f"<!-- activity: type=review_verdict actor=Claude target=PM status=FIX -->\n"
            f"Verdict: FAIL | Version: v1 | HEAD: {HEAD}\n"
            f"Finding: F2 [High] src/service.py:20 newer finding\n"
        )
        body_too_long = prefix + ("\u4e16" * 25000)
        comment_too_long = {
            'id': 9,
            'body': body_too_long,
            'created_at': '2026-10-27T01:05:00Z',
            'updated_at': '2026-10-27T01:05:00Z',
        }

        self._assert_refused(
            review_repair.REVIEW_VERDICT_TOO_LONG,
            github_get=lambda _path: [comment_candidate, comment_too_long],
        )
        self.assertNotIn('review_repair_claims', self.store.get(TASK))
        self.assertEqual(len(self.calls), 0)

    def test_older_multibyte_verdict_does_not_block_newer_valid_verdict(self):
        # Older over-long verdict (comment id 7 at 00:50:00Z)
        prefix = (
            f"<!-- activity: type=review_verdict actor=Claude target=PM status=FIX -->\n"
            f"Verdict: FAIL | Version: v1 | HEAD: {HEAD}\n"
            f"Finding: F1 [High] src/service.py:10 old finding\n"
        )
        comment_old_too_long = {
            'id': 7,
            'body': prefix + ("\u4e16" * 25000),
            'created_at': '2026-10-27T00:50:00Z',
            'updated_at': '2026-10-27T00:50:00Z',
        }
        # Newer valid bound FAIL verdict (comment id 8 at 01:00:00Z)
        comment_newer_valid = {
            'id': 8,
            'body': (
                f"<!-- activity: type=review_verdict actor=Claude target=PM status=FIX -->\n"
                f"Verdict: FAIL | Version: v1 | HEAD: {HEAD}\n"
                f"Finding: F2 [High] src/service.py:20 current finding\n"
            ),
            'created_at': '2026-10-27T01:00:00Z',
            'updated_at': '2026-10-27T01:00:00Z',
        }

        result = self.dispatch(github_get=lambda _path: [comment_old_too_long, comment_newer_valid])
        self.assertEqual(result['claim']['attempt'], 1)
        self.assertEqual(result['claim']['state'], 'DISPATCHED')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]['packet']['verdict_comment_id'], 8)

    def test_unrelated_overlong_comment_does_not_block_repair(self):
        # An over-long comment not bound to this task / head
        comment_unrelated = {
            'id': 9,
            'body': "Non-verdict discussion comment\n" + ("\u4e16" * 25000),
            'created_at': '2026-10-27T01:05:00Z',
            'updated_at': '2026-10-27T01:05:00Z',
        }
        # Valid bound FAIL verdict
        comment_valid = {
            'id': 8,
            'body': (
                f"<!-- activity: type=review_verdict actor=Claude target=PM status=FIX -->\n"
                f"Verdict: FAIL | Version: v1 | HEAD: {HEAD}\n"
                f"Finding: F1 [High] src/service.py:10 defect description\n"
            ),
            'created_at': '2026-10-27T01:00:00Z',
            'updated_at': '2026-10-27T01:00:00Z',
        }

        result = self.dispatch(github_get=lambda _path: [comment_unrelated, comment_valid])
        self.assertEqual(result['claim']['attempt'], 1)
        self.assertEqual(result['claim']['state'], 'DISPATCHED')
        self.assertEqual(len(self.calls), 1)

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

        # 4. verdict_body with multi-byte text exceeding byte limit -> refuses REVIEW_VERDICT_TOO_LONG
        multibyte_gate_body = (
            "Finding: F1 [High] a.py:1 bug\n"
            + ("\u4e16" * 25000)
        )
        self.assertLessEqual(len(multibyte_gate_body), review_repair.MAX_VERDICT_CHARS)
        self.assertGreater(len(multibyte_gate_body.encode('utf-8')), review_repair.MAX_VERDICT_BYTES)
        with self.assertRaises(LLMToolError) as caught:
            review_repair._review_gate(
                task, attempts, packet, f'pr-{PR}', policy, verdict_body=multibyte_gate_body
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

    def test_shared_binding_predicate_parity_codex_pass(self):
        task = dict(self.store.get(TASK), task_binding={'contract_digest': CONTRACT})
        self.store = TempTaskStore(self.temp.name, task)

        # 1. Valid formal Codex PASS
        body_valid = (
            f"<!-- activity: type=review_verdict actor=Codex target=PM status=PASS -->\n"
            f"Verdict: PASS | Version: v1 | HEAD: {HEAD}\n"
            f"FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n"
            f"LLMTOOL-REVIEW-BINDING v1 task={TASK} run={RUN} contract={CONTRACT} head={HEAD}\n"
        )
        comment_valid = {
            'id': 9,
            'body': body_valid,
            'created_at': '2026-10-27T01:00:00Z',
            'updated_at': '2026-10-27T01:00:00Z',
        }
        self.assertTrue(review_result.is_bound_verdict(comment_valid, head=HEAD, version=1, task=task))
        self.assertTrue(review_repair._is_bound_verdict(comment_valid, head=HEAD, version=1, task=task))
        verdict_valid = review_result._comment_verdict(comment_valid, head=HEAD, version=1, task=task)
        self.assertIsNotNone(verdict_valid)
        self.assertEqual(verdict_valid[2], 'REVIEW_PASS')

        # 2. Malformed Codex PASS (missing FORMAL ACCEPT line)
        body_malformed = (
            f"<!-- activity: type=review_verdict actor=Codex target=PM status=PASS -->\n"
            f"Verdict: PASS | Version: v1 | HEAD: {HEAD}\n"
            f"LLMTOOL-REVIEW-BINDING v1 task={TASK} run={RUN} contract={CONTRACT} head={HEAD}\n"
        )
        comment_malformed = {
            'id': 10,
            'body': body_malformed,
            'created_at': '2026-10-27T01:00:00Z',
            'updated_at': '2026-10-27T01:00:00Z',
        }
        self.assertFalse(review_result.is_bound_verdict(comment_malformed, head=HEAD, version=1, task=task))
        self.assertFalse(review_repair._is_bound_verdict(comment_malformed, head=HEAD, version=1, task=task))
        self.assertIsNone(review_result._comment_verdict(comment_malformed, head=HEAD, version=1, task=task))

    def test_malformed_oversized_codex_pass_does_not_block_repair(self):
        task = dict(self.store.get(TASK), task_binding={'contract_digest': CONTRACT})
        self.store = TempTaskStore(self.temp.name, task)

        # Malformed Codex PASS (missing FORMAL ACCEPT line), but oversized (> 65536 chars)
        prefix = (
            f"<!-- activity: type=review_verdict actor=Codex target=PM status=PASS -->\n"
            f"Verdict: PASS | Version: v1 | HEAD: {HEAD}\n"
            f"LLMTOOL-REVIEW-BINDING v1 task={TASK} run={RUN} contract={CONTRACT} head={HEAD}\n"
        )
        body_malformed_oversized = prefix + ("# diagnostic commentary trace\n" * 2500)
        self.assertGreater(len(body_malformed_oversized), review_repair.MAX_VERDICT_CHARS)

        comment_malformed = {
            'id': 11,
            'body': body_malformed_oversized,
            'created_at': '2026-10-27T01:05:00Z',
            'updated_at': '2026-10-27T01:05:00Z',
        }

        # Case A: Only the malformed oversized comment is present -> missing fail event, NOT verdict too long
        self._assert_refused(
            'REVIEW_FAIL_EVENT_MISSING',
            github_get=lambda _path: [comment_malformed],
        )

        # Case B: Along with a valid bound FAIL verdict -> repair proceeds, selecting the valid FAIL
        comment_valid_fail = {
            'id': 12,
            'body': (
                f"<!-- activity: type=review_verdict actor=Claude target=PM status=FIX -->\n"
                f"Verdict: FAIL | Version: v1 | HEAD: {HEAD}\n"
                f"Finding: F1 [High] src/service.py:10 unhandled exception\n"
            ),
            'created_at': '2026-10-27T01:00:00Z',
            'updated_at': '2026-10-27T01:00:00Z',
        }
        result = self.dispatch(github_get=lambda _path: [comment_malformed, comment_valid_fail])
        self.assertEqual(result['claim']['attempt'], 1)
        self.assertEqual(result['claim']['state'], 'DISPATCHED')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]['packet']['verdict_comment_id'], 12)

    def test_valid_oversized_formal_codex_pass_refuses_too_long(self):
        task = dict(self.store.get(TASK), task_binding={'contract_digest': CONTRACT})
        self.store = TempTaskStore(self.temp.name, task)

        prefix = (
            f"<!-- activity: type=review_verdict actor=Codex target=PM status=PASS -->\n"
            f"Verdict: PASS | Version: v1 | HEAD: {HEAD}\n"
            f"FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n"
            f"LLMTOOL-REVIEW-BINDING v1 task={TASK} run={RUN} contract={CONTRACT} head={HEAD}\n"
        )
        body_valid_oversized = prefix + ("# valid trace context\n" * 3000)
        self.assertGreater(len(body_valid_oversized), review_repair.MAX_VERDICT_CHARS)

        comment_valid_oversized = {
            'id': 11,
            'body': body_valid_oversized,
            'created_at': '2026-10-27T01:05:00Z',
            'updated_at': '2026-10-27T01:05:00Z',
        }

        self._assert_refused(
            review_repair.REVIEW_VERDICT_TOO_LONG,
            github_get=lambda _path: [comment_valid_oversized],
        )

    def test_shared_binding_predicate_parity_other_shapes(self):
        task = dict(self.store.get(TASK), task_binding={'contract_digest': CONTRACT})

        # Mismatched HEAD
        comment_wrong_head = _comment(head='f' * 40)
        self.assertFalse(review_result.is_bound_verdict(comment_wrong_head, head=HEAD, version=1, task=task))
        self.assertFalse(review_repair._is_bound_verdict(comment_wrong_head, head=HEAD, version=1, task=task))
        self.assertIsNone(review_result._comment_verdict(comment_wrong_head, head=HEAD, version=1, task=task))

        # Mismatched version
        comment_wrong_version = _comment(version=2)
        self.assertFalse(review_result.is_bound_verdict(comment_wrong_version, head=HEAD, version=1, task=task))
        self.assertFalse(review_repair._is_bound_verdict(comment_wrong_version, head=HEAD, version=1, task=task))
        self.assertIsNone(review_result._comment_verdict(comment_wrong_version, head=HEAD, version=1, task=task))

        # Same-family Codex reviewer on Codex developer task
        codex_task = dict(task, developer=dict(task['developer'], provider_profile='codex'))
        comment_codex_fail = {
            'id': 15,
            'body': (
                f"<!-- activity: type=review_verdict actor=Codex target=PM status=FIX -->\n"
                f"Verdict: FAIL | Version: v1 | HEAD: {HEAD}\n"
                f"LLMTOOL-REVIEW-BINDING v1 task={TASK} run={RUN} contract={CONTRACT} head={HEAD}\n"
                f"Finding: F1 [High] a.py:1 error\n"
            ),
            'created_at': '2026-10-27T01:00:00Z',
            'updated_at': '2026-10-27T01:00:00Z',
        }
        self.assertFalse(review_result.is_bound_verdict(comment_codex_fail, head=HEAD, version=1, task=codex_task))
        self.assertFalse(review_repair._is_bound_verdict(comment_codex_fail, head=HEAD, version=1, task=codex_task))
        self.assertIsNone(review_result._comment_verdict(comment_codex_fail, head=HEAD, version=1, task=codex_task))

        # Cross-family Codex FAIL
        self.assertTrue(review_result.is_bound_verdict(comment_codex_fail, head=HEAD, version=1, task=task))
        self.assertTrue(review_repair._is_bound_verdict(comment_codex_fail, head=HEAD, version=1, task=task))
        verdict = review_result._comment_verdict(comment_codex_fail, head=HEAD, version=1, task=task)
        self.assertIsNotNone(verdict)
        self.assertEqual(verdict[2], 'REVIEW_FAIL')


if __name__ == '__main__':
    unittest.main()
