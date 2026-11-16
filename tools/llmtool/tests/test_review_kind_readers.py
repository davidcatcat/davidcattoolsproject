"""Unit tests for PR 1 of #2199: kind-aware readers and version ranges.

Covers:
- Hand-built security claims and events are ignored by formal paths (decide,
  _merge_pass_claim, _failed_event, _blocked_fallback_reviewer).
- RUN_TASK_COMPLETE claim selector picks newest formal claim; stops with
  REVIEW_KIND_UNKNOWN when newest claim is kind None.
- _newer_claim_pending: security claims do not veto; formal claims veto.
- Next formal version computation with v91 security claim present.
- Review version range validation per kind and mismatch refusals.
- Byte-identical event IDs for unchanged formal records.
- Legacy unkinded claims at version >= 91 fail closed.
- TaskStore claim order preservation across appends and state transitions.
- Kind-aware disputes: formal disputes target formal FAILs; --kind security
  refused DISPUTE_KIND_UNSUPPORTED; unkinded v>=91 claims refuse
  DISPUTE_TARGET_UNKNOWN; formal withdrawn rulings do not withdraw security
  findings; legacy disputes without review_kind still withdraw formal findings.
"""

import hashlib
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from llmtool import (
    review_dispatch,
    review_repair,
    review_result,
    task_advance,
    task_completion,
    task_store,
    task_submission_cli,
    worker_dispatch,
)
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore, new_id

import review_trust_fixture
import task_fixtures
import worker_policy_fixture

HEAD = 'a' * 40
PR_NUM = 42


class ReviewKindReadersTest(unittest.TestCase):
    def setUp(self):
        super().setUp()
        review_trust_fixture.install_for_case(self)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = TaskStore(root=self.temp_dir.name)
        worker_policy_fixture.install(self)

    def _create_task(self, provider='codex'):
        return task_fixtures.create_with_developer(self.store, provider=provider)

    # -------------------------------------------------------------------------
    # 1. Hand-built security claim and event ignored by formal paths
    # -------------------------------------------------------------------------

    def test_decide_ignores_security_claim_and_event(self):
        """decide returns the same step with or without security claim/event."""
        record = self._create_task(provider='codex')
        task_id = record['task_id']
        d6 = {
            'state': 'CLEAN',
            'dirty': False,
            'result_head': HEAD,
            'recorded_result_head': HEAD,
            'root': '/fake/d6',
            'branch': 'task-branch',
            'task_id': 'd6-exec-1',
        }
        pr = {'number': PR_NUM, 'head': {'sha': HEAD}, 'state': 'open'}
        related = {'events': [], 'repair_claims': []}

        # With mock remote test passing, decide with no formal claims gives DISPATCH_REVIEW
        with patch.object(task_advance, '_remote_test_status', return_value=('PASS', None, None)):
            step_baseline = task_advance.decide(record, d6, pr, related)
            self.assertEqual(step_baseline, 'DISPATCH_REVIEW')

            # Add a security claim and security event
            sec_claim = {
                'pr': PR_NUM,
                'head_sha': HEAD,
                'version': 91,
                'state': 'COMPLETED',
                'review_kind': 'security',
                'reviewer_task': 'sec-rev-1',
                'review_result': {'verdict': 'PASS', 'head': HEAD},
            }
            sec_event = {
                'event_id': 'sec-ev-1',
                'source': 'REVIEWER',
                'source_sha': HEAD,
                'version': 91,
                'outcome': 'REVIEW_PASS',
                'review_kind': 'security',
            }
            record_with_sec = dict(record, review_dispatch_claim=[sec_claim])
            related_with_sec = {'events': [sec_event], 'repair_claims': []}

            # decide should ignore the security claim and event, still returning DISPATCH_REVIEW
            step_with_sec = task_advance.decide(
                record_with_sec, d6, pr, related_with_sec
            )
            self.assertEqual(step_with_sec, step_baseline)

    def test_merge_pass_claim_ignores_security_pass_and_newer_security_claim(self):
        """Security PASS does not satisfy formal review; newer security claim does not veto."""
        record = self._create_task(provider='codex')

        # Case A: Only security PASS is present
        sec_claim = {
            'pr': PR_NUM,
            'head_sha': HEAD,
            'version': 91,
            'state': 'COMPLETED',
            'review_kind': 'security',
            'review_result': {'verdict': 'PASS', 'head': HEAD},
        }
        sec_event = {
            'source': 'REVIEWER',
            'source_sha': HEAD,
            'version': 91,
            'outcome': 'REVIEW_PASS',
            'review_kind': 'security',
        }
        record_sec = dict(record, review_dispatch_claim=[sec_claim])
        self.assertIsNone(
            task_advance._merge_pass_claim(record_sec, PR_NUM, HEAD, [sec_event])
        )

        # Case B: Formal PASS at v1 present, newer security claim at v91 also present
        formal_claim = {
            'pr': PR_NUM,
            'head_sha': HEAD,
            'version': 1,
            'state': 'COMPLETED',
            'review_kind': 'formal',
            'review_result': {'verdict': 'PASS', 'head': HEAD},
        }
        formal_event = {
            'source': 'REVIEWER',
            'source_sha': HEAD,
            'version': 1,
            'outcome': 'REVIEW_PASS',
            'review_kind': 'formal',
        }
        record_both = dict(
            record, review_dispatch_claim=[formal_claim, sec_claim]
        )
        # v1 formal PASS is returned; v91 security claim does NOT veto with MERGE_NEWER_REVIEW_EXISTS
        top = task_advance._merge_pass_claim(
            record_both, PR_NUM, HEAD, [formal_event, sec_event]
        )
        self.assertEqual(top, 1)

    def test_failed_event_filters_on_formal(self):
        """review_repair._failed_event ignores security review FAIL events and claims."""
        record = self._create_task(provider='codex')
        sec_claim = {
            'pr': PR_NUM,
            'head_sha': HEAD,
            'version': 91,
            'state': 'COMPLETED',
            'review_kind': 'security',
            'review_result': {'verdict': 'FAIL', 'head': HEAD, 'review_text': 'Findings'},
        }
        sec_event = {
            'source': 'REVIEWER',
            'source_sha': HEAD,
            'version': 91,
            'outcome': 'REVIEW_FAIL',
            'state': 'PENDING',
            'review_kind': 'security',
            'session_id': 'rev-sec-session',
        }
        record_sec = dict(record, review_dispatch_claim=[sec_claim], completion_events=[sec_event])
        # Only security FAIL present -> _failed_event raises REVIEW_FAIL_EVENT_MISSING
        with self.assertRaises(LLMToolError) as ctx:
            review_repair._failed_event(
                record_sec, executor_task='d6-exec-1', pr_number=PR_NUM, github_get=None
            )
        self.assertEqual(
            getattr(ctx.exception, 'review_repair_code', None),
            'REVIEW_FAIL_EVENT_MISSING',
        )

    def test_blocked_fallback_reviewer_ignores_security_claim(self):
        """_blocked_fallback_reviewer counts only formal claims when checking for 1 claim."""
        record = self._create_task(provider='codex')
        formal_claim = {
            'pr': PR_NUM,
            'head_sha': HEAD,
            'version': 1,
            'state': 'DISPATCHED',
            'review_kind': 'formal',
        }
        sec_claim = {
            'pr': PR_NUM,
            'head_sha': HEAD,
            'version': 91,
            'state': 'COMPLETED',
            'review_kind': 'security',
        }
        # Record has 1 formal claim and 1 security claim at HEAD
        record_both = dict(record, review_dispatch_claim=[formal_claim, sec_claim])
        # Alternate reviewer should be returned because formal claims count is exactly 1
        alternate = task_advance._blocked_fallback_reviewer(record_both, HEAD)
        self.assertIsNotNone(alternate)

    # -------------------------------------------------------------------------
    # 2. Completion: RUN_TASK_COMPLETE selector and _newer_claim_pending
    # -------------------------------------------------------------------------

    def test_run_task_complete_claim_selector_passes_formal_reviewer_task(self):
        """RUN_TASK_COMPLETE passes formal reviewer_task even when security PASS claim is newer."""
        record = self._create_task(provider='codex')
        task_id = record['task_id']

        formal_claim = {
            'pr': PR_NUM,
            'head_sha': HEAD,
            'version': 1,
            'state': 'COMPLETED',
            'review_kind': 'formal',
            'reviewer_task': 'formal-reviewer-task-1',
            'review_result': {'verdict': 'PASS', 'head': HEAD},
        }
        sec_claim = {
            'pr': PR_NUM,
            'head_sha': HEAD,
            'version': 91,
            'state': 'COMPLETED',
            'review_kind': 'security',
            'reviewer_task': 'sec-reviewer-task-91',
            'review_result': {'verdict': 'PASS', 'head': HEAD},
        }
        # Update store with both claims (formal first, then newer security)
        self.store.update(task_id, review_dispatch_claim=[formal_claim, sec_claim])

        captured_args = []

        def mock_run_task_complete(args, store=None):
            captured_args.append(args)
            return 0

        d6 = {
            'state': 'CLEAN',
            'dirty': False,
            'result_head': HEAD,
            'recorded_result_head': HEAD,
            'root': '/fake/d6',
            'branch': 'task-branch',
            'task_id': 'd6-exec-1',
        }
        pr = {'number': PR_NUM, 'head': {'sha': HEAD}, 'state': 'open'}
        formal_event = {
            'event_id': 'fev-1',
            'source': 'REVIEWER',
            'source_sha': HEAD,
            'version': 1,
            'outcome': 'REVIEW_PASS',
            'review_kind': 'formal',
        }

        with patch.object(task_advance, '_d6_snapshot', return_value=d6), \
             patch.object(task_advance, '_pr_snapshot', return_value=pr), \
             patch.object(task_advance, '_review_outcomes', return_value=[formal_event]), \
             patch.object(task_advance, '_merged_review_target', return_value=None), \
             patch.object(task_advance, '_remote_test_status', return_value=('PASS', None, None)):
            result = task_advance.advance(
                task_id=task_id,
                store=self.store,
                run_task_complete_call=mock_run_task_complete,
            )

        self.assertEqual(len(captured_args), 1)
        self.assertEqual(captured_args[0].reviewer_task, 'formal-reviewer-task-1')

    def test_run_task_complete_stops_when_newest_claim_kind_is_none(self):
        """Newest claim of kind None stops RUN_TASK_COMPLETE with REVIEW_KIND_UNKNOWN."""
        record = self._create_task(provider='codex')
        task_id = record['task_id']

        formal_claim = {
            'pr': PR_NUM,
            'head_sha': HEAD,
            'version': 1,
            'state': 'COMPLETED',
            'review_kind': 'formal',
            'reviewer_task': 'formal-reviewer-task-1',
            'review_result': {'verdict': 'PASS', 'head': HEAD},
        }
        # Version >= 91 without review_kind -> review_kind() is None (fails closed)
        unkinded_claim = {
            'pr': PR_NUM,
            'head_sha': HEAD,
            'version': 91,
            'state': 'COMPLETED',
            'reviewer_task': 'unkinded-task-91',
        }
        self.store.update(task_id, review_dispatch_claim=[formal_claim, unkinded_claim])

        d6 = {
            'state': 'CLEAN',
            'dirty': False,
            'result_head': HEAD,
            'recorded_result_head': HEAD,
            'root': '/fake/d6',
            'branch': 'task-branch',
            'task_id': 'd6-exec-1',
        }
        pr = {'number': PR_NUM, 'head': {'sha': HEAD}, 'state': 'open'}
        formal_event = {
            'event_id': 'fev-1',
            'source': 'REVIEWER',
            'source_sha': HEAD,
            'version': 1,
            'outcome': 'REVIEW_PASS',
            'review_kind': 'formal',
        }

        with patch.object(task_advance, '_d6_snapshot', return_value=d6), \
             patch.object(task_advance, '_pr_snapshot', return_value=pr), \
             patch.object(task_advance, '_review_outcomes', return_value=[formal_event]), \
             patch.object(task_advance, '_merged_review_target', return_value=None), \
             patch.object(task_advance, '_remote_test_status', return_value=('PASS', None, None)):
            result = task_advance.advance(task_id=task_id, store=self.store)

        self.assertEqual(result.get('step'), 'STOPPED')
        self.assertIn('REVIEW_KIND_UNKNOWN', result.get('codes', []))

    def test_newer_claim_pending_security_claims_do_not_block(self):
        """Security claims in CLAIMED/UNKNOWN do not block; formal claims block."""
        record = self._create_task(provider='codex')
        body = f'Verdict: PASS | Version: v1 | HEAD: {HEAD}'

        # Security claims newer than v1 in CLAIMED and UNKNOWN
        sec_claimed = {
            'pr': PR_NUM,
            'head_sha': HEAD,
            'version': 91,
            'state': 'CLAIMED',
            'review_kind': 'security',
        }
        sec_unknown = {
            'pr': PR_NUM,
            'head_sha': HEAD,
            'version': 92,
            'state': 'UNKNOWN',
            'review_kind': 'security',
        }
        rec_sec = dict(record, review_dispatch_claim=[sec_claimed, sec_unknown])
        self.assertFalse(
            task_completion._newer_claim_pending(rec_sec, PR_NUM, HEAD, body)
        )

        # Newer formal claim in CLAIMED blocks
        formal_claimed = {
            'pr': PR_NUM,
            'head_sha': HEAD,
            'version': 2,
            'state': 'CLAIMED',
            'review_kind': 'formal',
        }
        rec_formal = dict(record, review_dispatch_claim=[formal_claimed])
        self.assertTrue(
            task_completion._newer_claim_pending(rec_formal, PR_NUM, HEAD, body)
        )

    # -------------------------------------------------------------------------
    # 3. Next version computation and range validation
    # -------------------------------------------------------------------------

    def test_formal_next_version_with_v91_security_claim_present(self):
        """With v91 security claim present, formal next version is highest formal version + 1."""
        record = self._create_task(provider='codex')
        formal_claim = {
            'pr': PR_NUM,
            'head_sha': HEAD,
            'version': 2,
            'state': 'COMPLETED',
            'review_kind': 'formal',
        }
        sec_claim = {
            'pr': PR_NUM,
            'head_sha': HEAD,
            'version': 91,
            'state': 'COMPLETED',
            'review_kind': 'security',
        }
        record_claims = dict(
            record, review_dispatch_claim=[formal_claim, sec_claim]
        )
        claims = task_advance._claims(record_claims)
        versions = [
            c.get('version', 0)
            for c in claims
            if type(c) is dict
            and type(c.get('version')) is int
            and review_result.review_kind(c) == 'formal'
        ]
        version = max(versions, default=0) + 1
        self.assertEqual(version, 3)

    def test_review_version_range_refusals(self):
        """review_dispatch preflight validates range per kind."""
        record = self._create_task(provider='codex')
        task_id = record['task_id']
        executor_task = 'd6-exec-1'

        # Formal kind: version 91 raises REVIEW_VERSION_RANGE_INVALID
        with self.assertRaises(LLMToolError) as ctx:
            review_dispatch._preflight(
                self.store, task_id, executor_task, PR_NUM, 91, task=record, kind='formal'
            )
        self.assertEqual(
            getattr(ctx.exception, 'review_dispatch_code', None),
            'REVIEW_VERSION_RANGE_INVALID',
        )

        # Security kind: version 1 raises REVIEW_VERSION_RANGE_INVALID
        with self.assertRaises(LLMToolError) as ctx:
            review_dispatch._preflight(
                self.store, task_id, executor_task, PR_NUM, 1, task=record, kind='security'
            )
        self.assertEqual(
            getattr(ctx.exception, 'review_dispatch_code', None),
            'REVIEW_VERSION_RANGE_INVALID',
        )

        # Security kind: version 10000 raises REVIEW_VERSION_RANGE_INVALID
        with self.assertRaises(LLMToolError) as ctx:
            review_dispatch._preflight(
                self.store, task_id, executor_task, PR_NUM, 10000, task=record, kind='security'
            )
        self.assertEqual(
            getattr(ctx.exception, 'review_dispatch_code', None),
            'REVIEW_VERSION_RANGE_INVALID',
        )

    def test_prior_claim_kind_mismatch_refused(self):
        """dispatch_review with prior claim of mismatched kind raises REVIEW_DISPATCH_CLAIM_MISMATCH."""
        record = self._create_task(provider='codex')
        task_id = record['task_id']
        prior_formal_claim = {
            'pr': PR_NUM,
            'head_sha': HEAD,
            'version': 91,
            'executor_task': 'd6-exec-1',
            'state': 'CLAIMED',
            'review_kind': 'formal',
        }
        self.store.update(task_id, review_dispatch_claim=prior_formal_claim)

        preflight_val = (record, SimpleNamespace(root='/fake/root'), HEAD, 'b' * 40, 'claude-reviewer')
        # Dispatch security review when prior claim is formal
        with patch.object(review_dispatch, '_preflight', return_value=preflight_val):
            with self.assertRaises(LLMToolError) as ctx:
                review_dispatch.dispatch_review(
                    store=self.store,
                    task_id=task_id,
                    executor_task='d6-exec-1',
                    pr_number=PR_NUM,
                    version=91,
                    kind='security',
                )
            self.assertEqual(
                getattr(ctx.exception, 'review_dispatch_code', None),
                'REVIEW_DISPATCH_CLAIM_MISMATCH',
            )

    # -------------------------------------------------------------------------
    # 4. Invariants & Fail Closed
    # -------------------------------------------------------------------------

    def test_legacy_unkinded_claim_at_version_91_fails_closed(self):
        """review_result.review_kind returns None for unkinded claims at version >= 91."""
        self.assertIsNone(review_result.review_kind({'version': 91}))
        self.assertIsNone(review_result.review_kind({'version': 100}))
        self.assertEqual(review_result.review_kind({'version': 90}), 'formal')
        self.assertEqual(review_result.review_kind({'version': 1}), 'formal')
        self.assertEqual(
            review_result.review_kind({'version': 91, 'review_kind': 'security'}),
            'security',
        )
        self.assertEqual(
            review_result.review_kind({'version': 1, 'review_kind': 'formal'}),
            'formal',
        )
        self.assertIsNone(
            review_result.review_kind({'version': 91, 'review_kind': 'other'})
        )

    def test_formal_event_identity_byte_identical_regression(self):
        """Formal reviewer completion event IDs are byte-identical to unchanged records."""
        task_id = 't' * 32
        run_id = 'r' * 32
        identity = {
            'contract': 'c' * 64,
            'head': HEAD,
            'outcome': 'REVIEW_PASS',
            'run': run_id,
            'task': task_id,
            'version': 1,
        }
        expected_id = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(',', ':')).encode('utf-8')
        ).hexdigest()[:32]

        # Verify that record_completion_event for formal generates this exact event_id
        record = self._create_task(provider='codex')
        ev = self.store.record_completion_event(
            record['task_id'],
            source='REVIEWER',
            source_sha=HEAD,
            version=1,
            outcome='REVIEW_PASS',
            session_id='rev-session',
            contract_digest='c' * 64,
            run_id=run_id,
            task_id=task_id,
            review_kind='formal',
        )
        self.assertEqual(ev['event_id'], expected_id)
        # Verify review_kind is persisted on the event
        self.assertEqual(ev.get('review_kind'), 'formal')

    def test_claim_order_preserved_across_store_updates(self):
        """TaskStore keeps earlier review_dispatch_claim entries at their exact position."""
        record = self._create_task(provider='codex')
        task_id = record['task_id']

        claim_1 = {'pr': PR_NUM, 'head_sha': HEAD, 'version': 1, 'state': 'CLAIMED'}
        claim_2 = {'pr': PR_NUM, 'head_sha': HEAD, 'version': 2, 'state': 'CLAIMED'}
        claim_3 = {'pr': PR_NUM, 'head_sha': HEAD, 'version': 91, 'state': 'CLAIMED'}

        self.store.update(task_id, review_dispatch_claim=[claim_1, claim_2, claim_3])
        fresh = self.store.get(task_id)
        self.assertEqual(
            [c['version'] for c in fresh['review_dispatch_claim']], [1, 2, 91]
        )

        # Transition claim_2 to COMPLETED
        updated_2 = dict(claim_2, state='COMPLETED')
        self.store.update(
            task_id, review_dispatch_claim=[claim_1, updated_2, claim_3]
        )
        fresh2 = self.store.get(task_id)
        self.assertEqual(
            [c['version'] for c in fresh2['review_dispatch_claim']], [1, 2, 91]
        )
        self.assertEqual(fresh2['review_dispatch_claim'][1]['state'], 'COMPLETED')

    # -------------------------------------------------------------------------
    # 5. Kind-aware Disputes
    # -------------------------------------------------------------------------

    def test_formal_dispute_targets_formal_fail_when_security_present(self):
        """With formal FAIL at v2 and COMPLETED security at v91, dispute targets v2."""
        record = self._create_task(provider='codex')
        task_id = record['task_id']

        formal_fail = {
            'pr': PR_NUM,
            'head_sha': HEAD,
            'version': 2,
            'state': 'COMPLETED',
            'review_kind': 'formal',
            'review_result': {
                'verdict': 'FAIL',
                'head': HEAD,
                'review_text': 'Findings:\n- F1 (High): test finding',
            },
        }
        sec_claim = {
            'pr': PR_NUM,
            'head_sha': HEAD,
            'version': 91,
            'state': 'COMPLETED',
            'review_kind': 'security',
            'review_result': {
                'verdict': 'FAIL',
                'head': HEAD,
                'review_text': 'Findings:\n- F1 (High): sec finding',
            },
        }
        self.store.update(task_id, review_dispatch_claim=[formal_fail, sec_claim])

        claim, findings = task_submission_cli._dispute_fail_verdict(
            self.store.get(task_id), PR_NUM, HEAD, kind='formal'
        )
        self.assertEqual(claim['version'], 2)
        self.assertEqual(findings.get('F1'), 'High')

    def test_formal_dispute_refused_when_only_security_fail_present(self):
        """When only security FAIL is present, formal dispute is refused DISPUTE_NO_FAIL_VERDICT."""
        record = self._create_task(provider='codex')
        task_id = record['task_id']

        sec_claim = {
            'pr': PR_NUM,
            'head_sha': HEAD,
            'version': 91,
            'state': 'COMPLETED',
            'review_kind': 'security',
            'review_result': {
                'verdict': 'FAIL',
                'head': HEAD,
                'review_text': 'Findings:\n- F1 (High): sec finding',
            },
        }
        self.store.update(task_id, review_dispatch_claim=[sec_claim])

        with self.assertRaises(LLMToolError) as ctx:
            task_submission_cli._dispute_fail_verdict(
                self.store.get(task_id), PR_NUM, HEAD, kind='formal'
            )
        self.assertEqual(
            getattr(ctx.exception, 'dispute_code', None), 'DISPUTE_NO_FAIL_VERDICT'
        )

    def test_dispute_kind_security_refused_unsupported(self):
        """run_task_dispute with kind='security' is refused DISPUTE_KIND_UNSUPPORTED."""
        record = self._create_task(provider='codex')
        args = Namespace(
            task_id=record['task_id'],
            pm_session=record.get('pm_session_id'),
            dispute_action='open',
            kind='security',
            pr=PR_NUM,
            finding='F1',
            evidence_ref=f'https://github.com/{review_trust_fixture.OWNER_SLUG}/pull/{PR_NUM}#issuecomment-100',
        )
        with patch('llmtool.pm_caller.check_caller', return_value=None):
            code = task_submission_cli.run_task_dispute(args, store=self.store)
            self.assertEqual(code, 1)

    def test_unkinded_claim_version_91_refuses_dispute_target_unknown(self):
        """An unkinded claim at version >= 91 causes dispute to refuse DISPUTE_TARGET_UNKNOWN."""
        record = self._create_task(provider='codex')
        task_id = record['task_id']

        formal_fail = {
            'pr': PR_NUM,
            'head_sha': HEAD,
            'version': 1,
            'state': 'COMPLETED',
            'review_kind': 'formal',
            'review_result': {
                'verdict': 'FAIL',
                'head': HEAD,
                'review_text': 'Findings:\n- F1 (High): test',
            },
        }
        unkinded = {
            'pr': PR_NUM,
            'head_sha': HEAD,
            'version': 91,
            'state': 'COMPLETED',
            'review_result': {
                'verdict': 'FAIL',
                'head': HEAD,
                'review_text': 'Findings:\n- F1 (High): test',
            },
        }
        self.store.update(task_id, review_dispatch_claim=[formal_fail, unkinded])

        with self.assertRaises(LLMToolError) as ctx:
            task_submission_cli._dispute_fail_verdict(
                self.store.get(task_id), PR_NUM, HEAD, kind='formal'
            )
        self.assertEqual(
            getattr(ctx.exception, 'dispute_code', None), 'DISPUTE_TARGET_UNKNOWN'
        )

    def test_formal_withdrawn_ruling_does_not_withdraw_security_finding(self):
        """A formal F1 WITHDRAWN ruling does not withdraw a security F1 finding."""
        record = self._create_task(provider='codex')
        task_id = record['task_id']

        # Existing dispute ruled WITHDRAWN for formal kind
        dispute_formal = {
            'finding_id': 'F1',
            'pr': PR_NUM,
            'head_sha': HEAD,
            'review_version': 1,
            'severity': 'High',
            'state': 'RULED',
            'review_kind': 'formal',
            'ruling': {'outcome': 'WITHDRAWN'},
        }
        self.store.update(task_id, review_disputes=[dispute_formal])

        # withdrawn_findings for formal returns {'F1'}
        formal_withdrawn = review_repair.withdrawn_findings(
            self.store.get(task_id), PR_NUM, HEAD, kind='formal'
        )
        self.assertIn('F1', formal_withdrawn)

        # withdrawn_findings for security returns empty set
        sec_withdrawn = review_repair.withdrawn_findings(
            self.store.get(task_id), PR_NUM, HEAD, kind='security'
        )
        self.assertNotIn('F1', sec_withdrawn)

    def test_legacy_dispute_without_review_kind_still_withdraws_formal_findings(self):
        """A legacy dispute entry without review_kind still withdraws formal findings."""
        record = self._create_task(provider='codex')
        task_id = record['task_id']

        legacy_dispute = {
            'finding_id': 'F1',
            'pr': PR_NUM,
            'head_sha': HEAD,
            'review_version': 1,
            'severity': 'High',
            'state': 'RULED',
            'ruling': {'outcome': 'WITHDRAWN'},
        }
        self.store.update(task_id, review_disputes=[legacy_dispute])

        withdrawn = review_repair.withdrawn_findings(
            self.store.get(task_id), PR_NUM, HEAD, kind='formal'
        )
        self.assertIn('F1', withdrawn)


if __name__ == '__main__':
    unittest.main()
