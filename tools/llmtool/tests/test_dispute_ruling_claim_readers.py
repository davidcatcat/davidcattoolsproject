"""#2087 (Advisor slice A1b-i): formal readers skip dispute_ruling review claims.

Exercises real TaskStore records with the A1 dispute_ruling claim shapes
(placeholder IDs and digests only; no runtime started).
"""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import (high_debug, lineage_counts, review_dispatch, review_repair,
                     task_advance, task_store)
from llmtool.errors import LLMToolError

import review_trust_fixture


TASK = 'a' * 32
RUN = 'b' * 32
HEAD = 'c' * 40
EXECUTOR = 'executor-d6'
FORMAL_REVIEWER = 'reviewer-00000000000000000001'
FORMAL_REVIEWER_2 = 'reviewer-00000000000000000003'
ARBITER_REVIEWER = 'reviewer-00000000000000000002'
PR = 42
PM_SESSION = 'pm-test-session'
EVENT_ID = 'e' * 32
CLAUDE_UUID = '00000000-0000-4000-8000-000000000001'
CODEX_THREAD = '0123456789abcdef0123456789abcdef'
RULES_DIGEST = '1' * 64
DISPUTE_TEXT_DIGEST = '2' * 64
OUTPUT_DIGEST = '3' * 64
STAMP_1 = '2026-11-23T00:00:00Z'
STAMP_2 = '2026-11-23T00:01:00Z'
STAMP_3 = '2026-11-23T00:02:00Z'
REF = 'https://github.com/example/repo/pull/42#issuecomment-1001'
RULING_COMMENT_ID = 1002


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def _verdict_comment(contract_digest, *, verdict='FAIL', version=1, comment_id=1001,
                     actor='Claude'):
    marker = 'FIX' if verdict == 'FAIL' else verdict
    detail = ('Finding: F1 [High] tools/a.py:1 missing guard\n'
              if verdict == 'FAIL' else 'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n')
    body = (
        f'<!-- activity: type=review_verdict actor={actor} target=PM status={marker} -->\n'
        f'Verdict: {verdict} | Version: v{version} | HEAD: {HEAD}\n'
        f'LLMTOOL-REVIEW-BINDING v1 task={TASK} run={RUN} contract={contract_digest} head={HEAD}\n'
        + detail
    )
    return {'id': comment_id, 'body': body, 'created_at': STAMP_1, 'updated_at': STAMP_1}


def _ruling_text(contract_digest, *, outcome='UPHELD', actor='Codex'):
    return (
        f'<!-- activity: type=dispute_ruling actor={actor} target=PM status={outcome} -->\n'
        f'Ruling: F1 {outcome} | PR: #{PR} | Version: 1 | HEAD: {HEAD}\n'
        f'LLMTOOL-REVIEW-BINDING v1 task={TASK} run={RUN} contract={contract_digest} head={HEAD}\n'
        'Arbitration reasoning.'
    )


def _ruling_comment(contract_digest, *, outcome='UPHELD', actor='Codex'):
    return {
        'id': RULING_COMMENT_ID,
        'body': _ruling_text(contract_digest, outcome=outcome, actor=actor),
        'created_at': STAMP_3,
        'updated_at': STAMP_3,
    }


def _formal_claim(contract_digest, *, version=1, verdict='FAIL',
                  reviewer_task=FORMAL_REVIEWER, actor='Claude'):
    comment = _verdict_comment(contract_digest, verdict=verdict, version=version, actor=actor)
    return {
        'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': version,
        'state': 'COMPLETED', 'reviewer_task': reviewer_task,
        'native_session_id': f'formal-session-{version}',
        'account_mode': 'ROLES', 'account': 'reviewer-a',
        'claimed_at': STAMP_1, 'updated_at': STAMP_1,
        'review_result': {
            'task_id': TASK, 'run_id': RUN, 'contract_digest': contract_digest,
            'head': HEAD, 'verdict': verdict,
            'review_text': comment['body'],
        },
    }


def _arbiter_claim(contract_digest, *, state='COMPLETED', version=2, verdict='RULING',
                   outcome='UPHELD', reviewer='codex-reviewer', purpose='dispute_ruling'):
    claim = {
        'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': version,
        'state': state, 'reviewer_task': ARBITER_REVIEWER,
        'native_session_id': (None if state == 'CLAIMED' else
                              CLAUDE_UUID if reviewer == 'claude-reviewer' else CODEX_THREAD),
        'account_mode': 'ROLES', 'account': 'reviewer-b',
        'rules_sha256': RULES_DIGEST,
        'claimed_at': STAMP_2, 'updated_at': STAMP_3,
        'purpose': purpose,
        'dispute': {
            'pr': PR, 'finding_id': 'F1', 'head_sha': HEAD,
            'review_version': 1, 'dispute_text_sha256': DISPUTE_TEXT_DIGEST,
        },
        'reviewer': reviewer,
    }
    if state == 'COMPLETED':
        text = '' if verdict == 'BLOCKED' else _ruling_text(contract_digest, outcome=outcome)
        result = {
            'task_id': TASK, 'run_id': RUN, 'contract_digest': contract_digest,
            'head': HEAD, 'verdict': verdict, 'review_text': text,
            'source': {'kind': 'CODEX_JSONL', 'output_sha256': OUTPUT_DIGEST},
        }
        if verdict == 'RULING':
            result['outcome'] = outcome
        claim['review_result'] = result
    return claim


def _reviewer_event(*, version=1, outcome='REVIEW_PASS', event_id=EVENT_ID,
                    comment_id=1001, contract_digest=None):
    if outcome == 'REVIEW_PASS':
        identity = {'task': TASK, 'run': RUN, 'head': HEAD, 'version': version,
                    'outcome': outcome, 'comment_id': comment_id}
        computed_id = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()
        ).hexdigest()[:32]
    else:
        computed_id = event_id
    return {
        'event_id': computed_id,
        'task_id': TASK,
        'run_id': RUN,
        'pm_session_id': PM_SESSION,
        'session_id': 'review-' + computed_id,
        'source': 'REVIEWER',
        'source_sha': HEAD,
        'sequence': version,
        'payload_sha256': '4' * 64,
        'state': 'PENDING',
        'received_at': STAMP_2,
        'consumed_at': None,
        'pm_decision_sha256': None,
        'acked_at': None,
        'dispatch_intent': None,
        'review_kind': 'formal',
        **({'outcome': outcome, 'version': version} if outcome == 'REVIEW_PASS' else {}),
    }


def _base_task_record():
    contract = {
        'schema_version': 1, 'contract_version': 1, 'task_id': TASK,
        'goal': 'test dispute ruling claim readers', 'authorized_scope': ['pm-planning'],
        'criteria': [{'criterion_id': 'pm_response', 'description': 'Record result.',
                      'kind': 'ARTIFACT'}],
        'forbidden_scope': [], 'previous_digest': None,
    }
    parsed = task_store.parse_task_contract(json.dumps(contract))
    binding = vars(parsed.binding(RUN))
    record = {
        'schema_version': 2, 'task_id': TASK, 'run_id': RUN,
        'pm_session_id': PM_SESSION, 'pm_provider_profile': 'test',
        'pm_provider_type': 'test', 'pm_model': None,
        'pm_route_binding': {'type': 'test', 'model': None, 'base_url': None},
        'backend_session_semantics': 'NOT_STARTED', 'native_session_id': None,
        'state': 'PM_PENDING', 'created_at': STAMP_1, 'updated_at': STAMP_1,
        'task_contract': contract, 'task_binding': binding,
        'request': 'test dispute ruling claim readers',
        'request_sha256': hashlib.sha256(b'test dispute ruling claim readers').hexdigest(),
        'response': None, 'usage': None, 'error_code': None,
        'developer': None, 'pm_assignment': None, 'completion': None,
    }
    return record, binding['contract_digest']


def _dispute_entry():
    return {
        'finding_id': 'F1', 'pr': PR, 'head_sha': HEAD, 'review_version': 1,
        'severity': 'High', 'state': 'OPEN', 'evidence_ref': REF,
        'opened_at': STAMP_1,
    }


class DisputeRulingClaimReadersTests(unittest.TestCase):
    def _new_store(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return task_store.TaskStore(Path(temp.name).resolve() / 'store')

    def _create_store_with_claims(self, claims, *, events=None, with_dispute=True):
        store = self._new_store()
        base, contract_digest = _base_task_record()
        formal_claims = [c for c in claims if 'purpose' not in c]
        ruling_claims = [c for c in claims if 'purpose' in c]
        if formal_claims:
            base['review_dispatch_claim'] = formal_claims
        rec = store.create(base)
        if with_dispute:
            rec = store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_disputes=[_dispute_entry()])
        for arbiter in ruling_claims:
            claimed = {
                key: value for key, value in arbiter.items()
                if key != 'review_result'
            }
            claimed['state'] = 'CLAIMED'
            claimed['native_session_id'] = None
            current_claims = list(rec.get('review_dispatch_claim') or [])
            rec = store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_dispatch_claim=current_claims + [claimed])
            if arbiter['state'] != 'CLAIMED':
                rec = store.compare_and_update(
                    TASK, expected={'record_sha256': rec['record_sha256']},
                    review_dispatch_claim=current_claims + [arbiter])
        if events:
            with patch.object(task_store.TaskStore, '_completion_now', return_value=STAMP_2):
                for ev in events:
                    store.record_completion_event(
                        TASK,
                        event_id=ev['event_id'],
                        run_id=ev['run_id'],
                        session_id=ev['session_id'],
                        source=ev['source'],
                        source_sha=ev['source_sha'],
                        sequence=ev['sequence'],
                        payload_sha256=ev['payload_sha256'],
                        review_kind=ev.get('review_kind'),
                    )
            rec = store.get(TASK)
        return store, rec, contract_digest

    def _arbiter_variants(self, contract_digest):
        return (
            ('COMPLETED_RULING', _arbiter_claim(contract_digest, state='COMPLETED', verdict='RULING')),
            ('COMPLETED_WITHHELD', _arbiter_claim(contract_digest, state='COMPLETED', verdict='BLOCKED')),
            ('UNKNOWN', _arbiter_claim(contract_digest, state='UNKNOWN')),
            ('CLAIMED', _arbiter_claim(contract_digest, state='CLAIMED')),
            ('DISPATCHED', _arbiter_claim(contract_digest, state='DISPATCHED',
                                          reviewer='claude-reviewer')),
        )

    def test_failed_event_skips_ruling_claims_and_matches_baseline(self):
        _, baseline_digest = _base_task_record()
        formal = _formal_claim(baseline_digest, version=1, verdict='FAIL')
        event = _reviewer_event(version=1, outcome='REVIEW_FAIL')
        _, baseline_task, _ = self._create_store_with_claims(
            [formal], events=[event], with_dispute=False)
        github_get = lambda _path: [_verdict_comment(baseline_digest, verdict='FAIL', version=1)]
        expected_packet, expected_event = review_repair._failed_event(
            baseline_task, executor_task=EXECUTOR, pr_number=PR, github_get=github_get)

        for label, arbiter in self._arbiter_variants(baseline_digest):
            with self.subTest(state=label):
                store, task, _ = self._create_store_with_claims(
                    [formal, arbiter], events=[event], with_dispute=True)
                packet, got_event = review_repair._failed_event(
                    store.get(TASK), executor_task=EXECUTOR, pr_number=PR, github_get=github_get)
                self.assertEqual(packet, expected_packet)
                self.assertEqual(got_event, expected_event)

        # Any dict with a purpose key is treated as a ruling claim even without 'dispute_ruling'.
        other_purpose = dict(formal, version=2, reviewer_task=ARBITER_REVIEWER, purpose='other')
        packet, got_event = review_repair._failed_event(
            dict(baseline_task, review_dispatch_claim=[formal, other_purpose]),
            executor_task=EXECUTOR, pr_number=PR, github_get=github_get)
        self.assertEqual(packet, expected_packet)
        self.assertEqual(got_event, expected_event)

    def test_dispatch_review_refuses_ruling_claim_version_without_posting(self):
        _, contract_digest = _base_task_record()
        formal = _formal_claim(contract_digest, version=1, verdict='FAIL')
        executor = SimpleNamespace(root='/repo/worktree', common_dir='/repo/.git')
        gate = SimpleNamespace(allowed=True, reason_code='ALLOWED')

        # Baseline: without any ruling claim, version=1 replays the formal COMPLETED claim.
        base_store, _, _ = self._create_store_with_claims([formal], with_dispute=False)
        base_posters = []
        with patch.object(review_dispatch, '_preflight',
                          return_value=(base_store.get(TASK), executor, HEAD, 'd' * 40, 'claude-reviewer')), \
                patch.object(review_dispatch.worker_dispatch, 'evaluate', return_value=gate), \
                patch.object(review_dispatch, '_resolve_github_login', return_value='reviewer-user'):
            base_out = review_dispatch.dispatch_review(
                store=base_store, task_id=TASK, executor_task=EXECUTOR, pr_number=PR, version=1,
                comment_lister=lambda _pr: [
                    dict(_verdict_comment(contract_digest, verdict='FAIL', version=1),
                         user={'login': 'reviewer-user'})],
                comment_poster=lambda *args: base_posters.append(args))
        self.assertEqual(base_out['version'], 1)
        self.assertEqual(base_posters, [])

        for label, arbiter in self._arbiter_variants(contract_digest):
            with self.subTest(state=label):
                store, task, _ = self._create_store_with_claims(
                    [formal, arbiter], with_dispute=True)
                poster_calls = []
                with patch.object(review_dispatch, '_preflight',
                                  return_value=(task, executor, HEAD, 'd' * 40, 'codex-reviewer')), \
                        patch.object(review_dispatch.worker_dispatch, 'evaluate', return_value=gate), \
                        patch.object(review_dispatch, '_resolve_github_login', return_value='reviewer-user'), \
                        self.assertRaises(LLMToolError) as caught:
                    review_dispatch.dispatch_review(
                        store=store, task_id=TASK, executor_task=EXECUTOR, pr_number=PR, version=2,
                        comment_lister=lambda _pr: [],
                        comment_poster=lambda *args: poster_calls.append(args))
                self.assertEqual(caught.exception.review_dispatch_code, 'REVIEW_DISPATCH_VERSION_USED')
                self.assertEqual(poster_calls, [])

    def test_lineage_counts_unchanged_with_ruling_claims(self):
        _, contract_digest = _base_task_record()
        formal = _formal_claim(contract_digest, version=1, verdict='FAIL')
        base_store, base_task, _ = self._create_store_with_claims([formal], with_dispute=False)
        expected_counts = lineage_counts.lineage_counts(
            base_store, base_task, base_family=f'pr-{PR}', include_successors=True)
        expected_repairs = lineage_counts.chain_review_repairs(base_store, base_task)
        expected_debug = lineage_counts.chain_debug_used(
            base_store, base_task, base_task.get('high_debug', []), high_debug._debug_entry_used)

        for label, arbiter in self._arbiter_variants(contract_digest):
            with self.subTest(state=label):
                store, task, _ = self._create_store_with_claims(
                    [formal, arbiter], with_dispute=True)
                self.assertEqual(
                    lineage_counts.lineage_counts(
                        store, task, base_family=f'pr-{PR}', include_successors=True),
                    expected_counts)
                self.assertEqual(lineage_counts.chain_review_repairs(store, task), expected_repairs)
                self.assertEqual(
                    lineage_counts.chain_debug_used(
                        store, task, task.get('high_debug', []), high_debug._debug_entry_used),
                    expected_debug)

    def test_merge_pass_claim_never_accepts_ruling_claim_as_pass(self):
        _, contract_digest = _base_task_record()
        formal_pass_1 = _formal_claim(contract_digest, version=1, verdict='PASS')
        event_1 = _reviewer_event(version=1, outcome='REVIEW_PASS', comment_id=1001)
        base_record = {'task_id': TASK, 'run_id': RUN, 'review_dispatch_claim': [formal_pass_1]}
        self.assertEqual(task_advance._merge_pass_claim(base_record, PR, HEAD, [event_1]), 1)

        for label, arbiter in self._arbiter_variants(contract_digest):
            with self.subTest(state=label):
                vetoed = {'task_id': TASK, 'run_id': RUN,
                          'review_dispatch_claim': [formal_pass_1, arbiter]}
                self.assertEqual(
                    task_advance._merge_pass_claim(vetoed, PR, HEAD, [event_1]),
                    'MERGE_NEWER_REVIEW_EXISTS')

                formal_pass_3 = _formal_claim(
                    contract_digest, version=3, verdict='PASS', reviewer_task=FORMAL_REVIEWER_2)
                event_3 = _reviewer_event(version=3, outcome='REVIEW_PASS', comment_id=1003)
                passed = {'task_id': TASK, 'run_id': RUN,
                          'review_dispatch_claim': [formal_pass_1, arbiter, formal_pass_3]}
                self.assertEqual(
                    task_advance._merge_pass_claim(passed, PR, HEAD, [event_1, event_3]), 3)

    def test_review_outcomes_returns_no_outcome_for_ruling_claim(self):
        _, contract_digest = _base_task_record()
        formal = {
            k: v for k, v in _formal_claim(contract_digest, version=1, verdict='PASS').items()
            if k != 'review_result'
        }
        formal['state'] = 'DISPATCHED'
        event_1 = _reviewer_event(version=1, outcome='REVIEW_PASS', comment_id=1001)
        base_record = {
            'task_id': TASK, 'run_id': RUN,
            'task_binding': {'contract_digest': contract_digest},
            'developer': {'provider_profile': 'agy', 'model': 'gemini-2.5-pro'},
            'review_dispatch_claim': [formal],
            'completion_events': [event_1],
        }
        comments = [
            _verdict_comment(contract_digest, verdict='PASS', version=1, comment_id=1001),
            _ruling_comment(contract_digest, outcome='UPHELD', actor='Codex'),
        ]
        expected_outcomes = task_advance._review_outcomes(
            base_record, [formal], lambda _path: comments)
        self.assertEqual(len(expected_outcomes), 1)
        self.assertEqual(expected_outcomes[0]['version'], 1)

        for label, arbiter in self._arbiter_variants(contract_digest):
            with self.subTest(state=label):
                record = dict(base_record, review_dispatch_claim=[formal, arbiter])
                outcomes = task_advance._review_outcomes(
                    record, [formal, arbiter], lambda _path: comments)
                self.assertEqual(outcomes, expected_outcomes)
                self.assertFalse(any(item.get('version') == 2 for item in outcomes))
