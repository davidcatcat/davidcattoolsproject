"""#2087 (Advisor slice A1b-ii): CLI readers exclude dispute_ruling claims by purpose.

Covers task_submission_cli._is_ruling_claim, _dispute_fail_verdict,
dispute_rule (disputed-claim lookup), and _claude_review_claim_matches.
Claims use the shapes the merged A1 TaskStore accepts and are written through
the real TaskStore where a store is involved. No runtime is started; all IDs
and digests are fixed placeholders (Owner #763 A).
"""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import review_repair, task_completion, task_store, task_submission_cli
from llmtool.errors import LLMToolError


TASK = 'a' * 32
RUN = 'b' * 32
HEAD = 'c' * 40
EXECUTOR = 'executor-d6'
FORMAL_REVIEWER = 'reviewer-00000000000000000001'
ARBITER_REVIEWER = 'reviewer-00000000000000000002'
FORMAL_SESSION = 'formal-session-1'
ARBITER_SESSION = '0123456789abcdef0123456789abcdef'
RULES_DIGEST = '1' * 64
DISPUTE_TEXT_DIGEST = '2' * 64
OUTPUT_DIGEST = '3' * 64
PR = 42
STAMP_1 = '2026-11-23T00:00:00Z'
STAMP_2 = '2026-11-23T00:01:00Z'
STAMP_3 = '2026-11-23T00:02:00Z'
REPOSITORY = 'davidcatcat/davidcattoolsproject'
REF = f'https://github.com/{REPOSITORY}/pull/{PR}#issuecomment-1001'
RULING_REF = f'https://github.com/{REPOSITORY}/pull/{PR}#issuecomment-1002'
RULING_COMMENT_ID = 1002
COMMON_DIR = '/tmp/git'
FORMAL_FINDINGS = 'Finding: F1 [High] a.py:1 missing guard'


def _formal_verdict_text(status='FAIL', version=1, findings=FORMAL_FINDINGS):
    marker = 'FIX' if status == 'FAIL' else status
    return (f'<!-- activity: type=review_verdict actor=Claude target=PM status={marker} -->\n'
            f'Verdict: {status} | Version: v{version} | HEAD: {HEAD}\n{findings}\n')


def _ruling_text(contract_digest, outcome='UPHELD', actor='Codex'):
    return (f'<!-- activity: type=dispute_ruling actor={actor} target=PM status={outcome} -->\n'
            f'Ruling: F1 {outcome} | PR: #{PR} | HEAD: {HEAD}\n'
            f'LLMTOOL-REVIEW-BINDING v1 task={TASK} run={RUN} contract={contract_digest} head={HEAD}\n'
            'Arbitration reasoning.')


def _formal_claim(contract_digest, *, version=1, status='FAIL',
                  reviewer_task=FORMAL_REVIEWER, session=FORMAL_SESSION,
                  review_text=None):
    if review_text is None:
        review_text = _formal_verdict_text(status=status, version=version)
    return {
        'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': version,
        'state': 'COMPLETED', 'reviewer_task': reviewer_task,
        'native_session_id': session,
        'account_mode': 'EXPLICIT_CLAUDE_CONFIG_DIR', 'account': 'reviewer-a',
        'claimed_at': STAMP_1, 'updated_at': STAMP_1,
        'review_result': {
            'task_id': TASK, 'run_id': RUN, 'contract_digest': contract_digest,
            'head': HEAD, 'verdict': status, 'review_text': review_text,
        },
    }


def _ruling_claim(contract_digest, *, version=2, state='COMPLETED',
                  reviewer='codex-reviewer', reviewer_task=ARBITER_REVIEWER,
                  session=ARBITER_SESSION, verdict='RULING', outcome='UPHELD',
                  text=None, purpose='dispute_ruling'):
    claim = {
        'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': version,
        'state': state, 'reviewer_task': reviewer_task,
        'native_session_id': session,
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
        review_text = _ruling_text(contract_digest, outcome=outcome) if text is None else text
        result = {
            'task_id': TASK, 'run_id': RUN, 'contract_digest': contract_digest,
            'head': HEAD, 'verdict': verdict, 'review_text': review_text,
            'source': {'kind': 'CODEX_JSONL', 'output_sha256': OUTPUT_DIGEST},
        }
        if verdict == 'RULING':
            result['outcome'] = outcome
        claim['review_result'] = result
    return claim


def _base_record(*, claims=None, disputes=None):
    contract = {
        'schema_version': 1, 'contract_version': 1, 'task_id': TASK,
        'goal': 'test dispute ruling claim cli readers',
        'authorized_scope': ['pm-planning'],
        'criteria': [{'criterion_id': 'pm_response', 'description': 'Record result.',
                      'kind': 'ARTIFACT'}],
        'forbidden_scope': [], 'previous_digest': None,
    }
    parsed = task_store.parse_task_contract(json.dumps(contract))
    contract_digest = parsed.binding(RUN).contract_digest
    if claims is None:
        claims = [_formal_claim(contract_digest)]
    record = {
        'schema_version': 2, 'task_id': TASK, 'run_id': RUN,
        'pm_session_id': 'pm-test-session', 'pm_provider_profile': 'test',
        'pm_provider_type': 'test', 'pm_model': None,
        'pm_route_binding': {'type': 'test', 'model': None, 'base_url': None},
        'backend_session_semantics': 'NOT_STARTED', 'native_session_id': None,
        'state': 'PM_PENDING', 'created_at': STAMP_1, 'updated_at': STAMP_1,
        'task_contract': contract, 'task_binding': vars(parsed.binding(RUN)),
        'request': 'test dispute ruling claim cli readers',
        'request_sha256': hashlib.sha256(b'test dispute ruling claim cli readers').hexdigest(),
        'response': None, 'usage': None, 'error_code': None,
        'developer': None,
        'pm_assignment': None, 'completion': None,
        'review_dispatch_claim': claims,
    }
    if disputes is not None:
        record['review_disputes'] = disputes
    return record, contract_digest


class RulingClaimCliReadersTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = task_store.TaskStore(Path(self.temp.name).resolve())

    def _create_record(self, *, claims=None, disputes=None):
        base, contract_digest = _base_record(claims=claims, disputes=disputes)
        rec = self.store.create(base)
        return rec, contract_digest

    def _open_dispute(self, *, review_version=1):
        return {
            'finding_id': 'F1', 'pr': PR, 'head_sha': HEAD,
            'review_version': review_version, 'severity': 'High',
            'state': 'OPEN', 'evidence_ref': REF, 'opened_at': STAMP_1,
        }

    # --- A1: _is_ruling_claim ---

    def test_is_ruling_claim_by_purpose_and_text(self):
        _, digest = _base_record()
        self.assertTrue(task_submission_cli._is_ruling_claim(
            _ruling_claim(digest, text='not a ruling marker')))
        self.assertTrue(task_submission_cli._is_ruling_claim({'purpose': None}))
        self.assertTrue(task_submission_cli._is_ruling_claim({'purpose': 'other'}))
        # Existing text-based exclusion preserved when purpose is absent.
        self.assertTrue(task_submission_cli._is_ruling_claim({
            'review_result': {'review_text': _ruling_text(digest)},
        }))
        self.assertFalse(task_submission_cli._is_ruling_claim(_formal_claim(digest)))
        self.assertFalse(task_submission_cli._is_ruling_claim('not-a-dict'))

    # --- A2: _dispute_fail_verdict ---

    def test_dispute_fail_verdict_baseline_and_purpose_exclusion(self):
        rec, digest = self._create_record(disputes=[self._open_dispute(review_version=1)])
        claim, findings = task_submission_cli._dispute_fail_verdict(rec, PR, HEAD)
        self.assertEqual(claim['version'], 1)
        self.assertEqual(findings, {'F1': 'High'})

        # Adding a COMPLETED ruling claim at version 2 (via real TaskStore) leaves
        # the formal v1 FAIL verdict selected.
        ruling = _ruling_claim(digest, version=2, state='COMPLETED')
        updated = self.store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_dispatch_claim=rec['review_dispatch_claim'] + [ruling])
        claim2, findings2 = task_submission_cli._dispute_fail_verdict(updated, PR, HEAD)
        self.assertEqual(claim2['version'], 1)
        self.assertEqual(findings2, {'F1': 'High'})

        # Even if a purpose-bearing claim had non-ruling text and verdict='FAIL',
        # it is excluded by purpose alone.
        synthetic_purpose_fail = dict(_formal_claim(digest, version=2), purpose='dispute_ruling')
        with_synthetic = dict(rec, review_dispatch_claim=rec['review_dispatch_claim'] + [synthetic_purpose_fail])
        claim3, findings3 = task_submission_cli._dispute_fail_verdict(with_synthetic, PR, HEAD)
        self.assertEqual(claim3['version'], 1)
        self.assertEqual(findings3, {'F1': 'High'})

        # Legacy text-based exclusion still holds when purpose is absent.
        legacy_ruling = dict(_formal_claim(digest, version=2, review_text=_ruling_text(digest)))
        with_legacy = dict(rec, review_dispatch_claim=rec['review_dispatch_claim'] + [legacy_ruling])
        claim4, _ = task_submission_cli._dispute_fail_verdict(with_legacy, PR, HEAD)
        self.assertEqual(claim4['version'], 1)

    def test_dispute_fail_verdict_refuses_when_only_ruling_claim_exists(self):
        _, digest = _base_record()
        ruling_only = _ruling_claim(digest, version=1, state='COMPLETED')
        rec, _ = self._create_record(
            claims=[ruling_only],
            disputes=[self._open_dispute(review_version=1)],
        )
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli._dispute_fail_verdict(rec, PR, HEAD)
        self.assertEqual(caught.exception.dispute_code, 'DISPUTE_NO_FAIL_VERDICT')

    # --- A2: dispute_rule disputed-claim lookup ---

    def test_dispute_rule_baseline_and_excludes_ruling_claim_by_purpose(self):
        _, digest = _base_record()
        rec, _ = self._create_record(
            claims=[_formal_claim(digest, version=1)],
            disputes=[self._open_dispute(review_version=1)],
        )
        ruling = _ruling_claim(digest, version=2, state='COMPLETED', outcome='UPHELD')
        self.store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_dispatch_claim=rec['review_dispatch_claim'] + [ruling])

        ruling_body = ruling['review_result']['review_text']
        comment = {
            'id': RULING_COMMENT_ID, 'body': ruling_body,
            'issue_url': f'https://api.github.com/repos/{REPOSITORY}/issues/{PR}',
            'created_at': STAMP_2, 'updated_at': STAMP_2,
        }
        reg = SimpleNamespace(
            task_id=ARBITER_REVIEWER, trusted_agent_id='codex-reviewer',
            native_session_id=ARBITER_SESSION, result_sha=HEAD,
            root='/registered/' + ARBITER_REVIEWER, common_dir=COMMON_DIR,
            branch=ARBITER_REVIEWER + '-branch',
        )

        with patch.object(task_completion, '_trusted_slug', return_value=REPOSITORY), \
             patch.object(review_repair, 'developer_family', return_value='gemini'), \
             patch.object(task_submission_cli, '_dispute_arbiter_registration', return_value=reg), \
             patch.object(task_submission_cli, '_dispute_ruling_claim', return_value=ruling):
            result = task_submission_cli.dispute_rule(
                self.store, TASK, pr=PR, finding_id='F1', outcome='UPHELD',
                reviewer='codex-reviewer', reviewer_task=ARBITER_REVIEWER,
                reviewer_session=ARBITER_SESSION, evidence_ref=RULING_REF,
                current_head=lambda _r, _p: HEAD,
                github_get=lambda _path: comment,
            )
        self.assertEqual(result['state'], 'RULED')
        self.assertEqual(result['outcome'], 'UPHELD')

    def test_dispute_rule_refuses_when_disputed_version_is_only_a_ruling_claim(self):
        _, digest = _base_record()
        # Only a ruling claim exists at review_version=1 (no formal claim at v1).
        ruling_at_v1 = _ruling_claim(
            digest, version=1, state='COMPLETED', text='non-ruling text')
        self._create_record(
            claims=[ruling_at_v1],
            disputes=[self._open_dispute(review_version=1)],
        )
        with patch.object(task_completion, '_trusted_slug', return_value=REPOSITORY):
            with self.assertRaises(LLMToolError) as caught:
                task_submission_cli.dispute_rule(
                    self.store, TASK, pr=PR, finding_id='F1', outcome='UPHELD',
                    reviewer='codex-reviewer', reviewer_task=ARBITER_REVIEWER,
                    reviewer_session=ARBITER_SESSION, evidence_ref=RULING_REF,
                    current_head=lambda _r, _p: HEAD,
                )
        self.assertEqual(caught.exception.dispute_code, 'DISPUTE_NO_FAIL_VERDICT')

    # --- A3: _claude_review_claim_matches ---

    def test_claude_review_claim_matches_excludes_ruling_claims(self):
        _, digest = _base_record()
        ruling = _ruling_claim(digest, version=2, state='COMPLETED')
        rec_ruling_only, _ = self._create_record(
            claims=[ruling],
            disputes=[self._open_dispute(review_version=1)],
        )

        # Ruling claim's own Reviewer Task, PR, head and session return False.
        self.assertFalse(task_submission_cli._claude_review_claim_matches(
            rec_ruling_only, ARBITER_REVIEWER, PR, HEAD, ARBITER_SESSION))

        # Even a ruling claim without ruling text is excluded by purpose.
        ruling_no_marker = _ruling_claim(
            digest, version=2, state='COMPLETED', text='PASS', verdict='RULING')
        rec_no_marker = dict(rec_ruling_only, review_dispatch_claim=[ruling_no_marker])
        self.assertFalse(task_submission_cli._claude_review_claim_matches(
            rec_no_marker, ARBITER_REVIEWER, PR, HEAD, ARBITER_SESSION))

        # Formal claim with matching args returns True.
        formal = _formal_claim(digest, version=1, status='PASS')
        updated = self.store.compare_and_update(
            TASK, expected={'record_sha256': rec_ruling_only['record_sha256']},
            review_dispatch_claim=[formal, ruling])
        self.assertTrue(task_submission_cli._claude_review_claim_matches(
            updated, FORMAL_REVIEWER, PR, HEAD, FORMAL_SESSION))

        # Even if a ruling claim shared the same reviewer_task and session, it is
        # excluded before counting, so the one formal claim still returns True.
        same_actor_ruling = _ruling_claim(
            digest, version=2, state='COMPLETED',
            reviewer_task=FORMAL_REVIEWER, session=FORMAL_SESSION)
        rec_shared = dict(updated, review_dispatch_claim=[formal, same_actor_ruling])
        self.assertTrue(task_submission_cli._claude_review_claim_matches(
            rec_shared, FORMAL_REVIEWER, PR, HEAD, FORMAL_SESSION))

        # Two matching formal claims still return False.
        formal2 = _formal_claim(digest, version=2, status='PASS')
        rec_two_formal = dict(updated, review_dispatch_claim=[formal, formal2])
        self.assertFalse(task_submission_cli._claude_review_claim_matches(
            rec_two_formal, FORMAL_REVIEWER, PR, HEAD, FORMAL_SESSION))
