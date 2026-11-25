"""#2087 (A2-ii): WITHDRAWN dispute history event and dispute withdraw behaviour.

Exercises the real TaskStore (compare_and_update, get) and task_submission_cli.dispute_withdraw
on a temporary store directory. All IDs, sessions and digests are fixed placeholder values
(Owner #763 A).
"""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from llmtool import review_repair, task_store, task_submission_cli
from llmtool.errors import LLMToolError


TASK = 'a' * 32
RUN = 'b' * 32
HEAD = 'c' * 40
CONTRACT = 'd' * 64
EXECUTOR = 'executor-d6'
FORMAL_REVIEWER = 'reviewer-00000000000000000001'
ARBITER_REVIEWER = 'reviewer-00000000000000000002'
PR = 42
PM_SESSION = 'pm-test-session'
CODEX_THREAD = '0123456789abcdef0123456789abcdef'
RULES_DIGEST = '1' * 64
DISPUTE_TEXT_DIGEST = '2' * 64
OUTPUT_DIGEST = '3' * 64
STAMP_1 = '2026-11-23T00:00:00Z'
STAMP_2 = '2026-11-23T00:01:00Z'
STAMP_3 = '2026-11-23T00:02:00Z'
REF = 'https://github.com/example/repo/pull/42#issuecomment-1001'
RULING_REF = 'https://github.com/example/repo/pull/42#issuecomment-1002'
RULING_COMMENT_ID = 1002


def _base_record():
    contract = {
        'schema_version': 1, 'contract_version': 1, 'task_id': TASK,
        'goal': 'test dispute withdraw history', 'authorized_scope': ['pm-planning'],
        'criteria': [{'criterion_id': 'pm_response', 'description': 'Record result.',
                      'kind': 'ARTIFACT'}],
        'forbidden_scope': [], 'previous_digest': None,
    }
    parsed = task_store.parse_task_contract(json.dumps(contract))
    formal_claim = {
        'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': 1,
        'state': 'COMPLETED', 'reviewer_task': FORMAL_REVIEWER,
        'native_session_id': 'formal-session-1',
        'account_mode': 'ROLES', 'account': 'reviewer-a',
        'claimed_at': STAMP_1, 'updated_at': STAMP_1,
        'review_result': {
            'task_id': TASK, 'run_id': RUN,
            'contract_digest': parsed.binding(RUN).contract_digest,
            'head': HEAD, 'verdict': 'FAIL',
            'review_text': 'Finding: F1 [High] a.py:1 missing guard',
        },
    }
    return {
        'schema_version': 2, 'task_id': TASK, 'run_id': RUN,
        'pm_session_id': PM_SESSION, 'pm_provider_profile': 'test',
        'pm_provider_type': 'test', 'pm_model': None,
        'pm_route_binding': {'type': 'test', 'model': None, 'base_url': None},
        'backend_session_semantics': 'NOT_STARTED', 'native_session_id': None,
        'state': 'PM_PENDING', 'created_at': STAMP_1, 'updated_at': STAMP_1,
        'task_contract': contract, 'task_binding': vars(parsed.binding(RUN)),
        'request': 'test dispute withdraw history',
        'request_sha256': hashlib.sha256(b'test dispute withdraw history').hexdigest(),
        'response': None, 'usage': None, 'error_code': None,
        'developer': None, 'pm_assignment': None, 'completion': None,
        'review_dispatch_claim': [formal_claim],
    }


def _history_event(event, *, actor=f'pm:{PM_SESSION}', at=STAMP_2, refs=None):
    return {
        'event': event, 'at': at, 'actor': actor, 'head_sha': HEAD,
        'refs': {} if refs is None else refs,
    }


def _dispute_entry(*, with_history=True):
    entry = {
        'finding_id': 'F1', 'pr': PR, 'head_sha': HEAD, 'review_version': 1,
        'severity': 'High', 'state': 'OPEN', 'evidence_ref': REF,
        'opened_at': STAMP_1,
    }
    if with_history:
        entry['history'] = [_history_event('OPENED', at=STAMP_1)]
    return entry


class DisputeWithdrawHistoryTests(unittest.TestCase):
    def _new_store(self, *, with_dispute=True, with_history=True):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = task_store.TaskStore(Path(temp.name).resolve())
        rec = store.create(_base_record())
        if with_dispute:
            store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_disputes=[_dispute_entry(with_history=with_history)])
        return store, Path(temp.name).resolve() / f'{TASK}.json'

    def _assert_transition_refused(self, store, task_path, fn):
        before = task_path.read_bytes()
        with self.assertRaises(LLMToolError) as caught:
            fn()
        self.assertEqual(caught.exception.task_store_error_code,
                         'REVIEW_DISPUTE_TRANSITION_INVALID')
        self.assertEqual(task_path.read_bytes(), before)

    def test_withdraw_open_dispute_with_history_appends_one_withdrawn_event(self):
        store, _ = self._new_store(with_history=True)
        result = task_submission_cli.dispute_withdraw(
            store, TASK, pr=PR, finding_id='F1', head=HEAD)
        self.assertEqual(
            (result['state'], result['finding_withdrawn'], result['replay']),
            ('WITHDRAWN', False, False))
        saved = store.get(TASK)
        [entry] = saved['review_disputes']
        self.assertEqual(entry['state'], 'WITHDRAWN')
        self.assertNotIn('ruling', entry)
        self.assertEqual(len(entry['history']), 2)
        self.assertEqual(entry['history'][0], _history_event('OPENED', at=STAMP_1))
        withdrawn_ev = entry['history'][-1]
        self.assertEqual(withdrawn_ev['event'], 'WITHDRAWN')
        self.assertEqual(withdrawn_ev['actor'], f'pm:{PM_SESSION}')
        self.assertEqual(withdrawn_ev['head_sha'], HEAD)
        self.assertEqual(withdrawn_ev['refs'], {})
        self.assertTrue(task_store._valid_utc_seconds(withdrawn_ev['at']))
        self.assertFalse(review_repair.dispute_open_on(saved, PR, HEAD))
        self.assertEqual(review_repair.withdrawn_findings(saved, PR, HEAD, 1), frozenset())

    def test_withdraw_open_dispute_without_history_is_unchanged(self):
        store, _ = self._new_store(with_history=False)
        result = task_submission_cli.dispute_withdraw(
            store, TASK, pr=PR, finding_id='F1', head=HEAD)
        self.assertEqual(
            (result['state'], result['finding_withdrawn'], result['replay']),
            ('WITHDRAWN', False, False))
        [entry] = store.get(TASK)['review_disputes']
        self.assertEqual(entry['state'], 'WITHDRAWN')
        self.assertNotIn('history', entry)
        self.assertNotIn('ruling', entry)

    def test_replay_returns_recorded_state_and_writes_nothing(self):
        store, task_path = self._new_store(with_history=True)
        first = task_submission_cli.dispute_withdraw(
            store, TASK, pr=PR, finding_id='F1', head=HEAD)
        self.assertFalse(first['replay'])
        before_bytes = task_path.read_bytes()
        before_record = store.get(TASK)
        replay = task_submission_cli.dispute_withdraw(
            store, TASK, pr=PR, finding_id='F1', head=HEAD)
        self.assertEqual(
            (replay['state'], replay['finding_withdrawn'], replay['replay']),
            ('WITHDRAWN', False, True))
        self.assertEqual(task_path.read_bytes(), before_bytes)
        self.assertEqual(store.get(TASK), before_record)

    def test_ruled_entry_with_history_still_refused_as_already_ruled(self):
        store, task_path = self._new_store(with_history=True)
        rec = store.get(TASK)
        cd = rec['task_binding']['contract_digest']
        ruling_text = (
            '<!-- activity: type=dispute_ruling actor=Codex target=PM status=UPHELD -->\n'
            f'Ruling: F1 UPHELD | PR: #{PR} | Version: 1 | HEAD: {HEAD}\n'
            f'LLMTOOL-REVIEW-BINDING v1 task={TASK} run={RUN} contract={cd} head={HEAD}\n'
            'Arbitration reasoning.'
        )
        claimed = {
            'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': 2,
            'state': 'CLAIMED', 'reviewer_task': ARBITER_REVIEWER,
            'native_session_id': None, 'account_mode': 'ROLES', 'account': 'reviewer-b',
            'rules_sha256': RULES_DIGEST, 'claimed_at': STAMP_2, 'updated_at': STAMP_2,
            'purpose': 'dispute_ruling',
            'dispute': {
                'pr': PR, 'finding_id': 'F1', 'head_sha': HEAD,
                'review_version': 1, 'dispute_text_sha256': DISPUTE_TEXT_DIGEST,
            },
            'reviewer': 'codex-reviewer',
        }
        entry_claimed = dict(rec['review_disputes'][0])
        entry_claimed['arbiter_claim'] = {'reviewer_task': ARBITER_REVIEWER, 'version': 2}
        entry_claimed['history'] = list(entry_claimed['history']) + [
            _history_event('ARBITER_CLAIMED', actor='llmtool', at=STAMP_2,
                           refs={'reviewer_task': ARBITER_REVIEWER, 'version': 2}),
        ]
        rec = store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_dispatch_claim=rec['review_dispatch_claim'] + [claimed],
            review_disputes=[entry_claimed])
        completed = dict(
            claimed, state='COMPLETED', native_session_id=CODEX_THREAD, updated_at=STAMP_3,
            review_result={
                'task_id': TASK, 'run_id': RUN, 'contract_digest': cd,
                'head': HEAD, 'verdict': 'RULING', 'outcome': 'UPHELD',
                'review_text': ruling_text,
                'source': {'kind': 'CODEX_JSONL', 'output_sha256': OUTPUT_DIGEST},
            },
        )
        rec = store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_dispatch_claim=[rec['review_dispatch_claim'][0], completed])
        entry = dict(rec['review_disputes'][0])
        entry['state'] = 'RULED'
        entry['ruling'] = {
            'outcome': 'UPHELD', 'head_sha': HEAD, 'reviewer': 'codex-reviewer',
            'reviewer_task': ARBITER_REVIEWER, 'reviewer_session': CODEX_THREAD,
            'evidence_ref': RULING_REF, 'ruled_at': STAMP_3, 'path': 'arbitrate',
            'finding_id': 'F1', 'review_version': 1, 'arbiter_claim_version': 2,
            'rules_sha256': RULES_DIGEST,
            'review_text_sha256': hashlib.sha256(ruling_text.encode('utf-8')).hexdigest(),
            'claim_sha256': task_store._canonical_claim_sha256(completed),
            'source_kind': 'CODEX_JSONL', 'output_sha256': OUTPUT_DIGEST,
            'comment_id': RULING_COMMENT_ID, 'contract_digest': cd, 'run_id': RUN,
        }
        entry['history'] = list(entry['history']) + [
            _history_event('RULED', actor='llmtool', at=STAMP_3,
                           refs={'comment_id': RULING_COMMENT_ID}),
        ]
        store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_disputes=[entry])
        before_bytes = task_path.read_bytes()
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli.dispute_withdraw(
                store, TASK, pr=PR, finding_id='F1', head=HEAD)
        self.assertEqual(caught.exception.dispute_code, 'DISPUTE_ALREADY_RULED')
        self.assertEqual(task_path.read_bytes(), before_bytes)

    def test_store_refuses_withdrawn_event_on_invalid_transitions_and_rewrites(self):
        store, task_path = self._new_store(with_history=True)
        rec = store.get(TASK)
        open_entry = rec['review_disputes'][0]
        withdrawn_ev = _history_event('WITHDRAWN', at=STAMP_2)

        # OPEN -> OPEN with WITHDRAWN event
        self._assert_transition_refused(
            store, task_path,
            lambda: store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_disputes=[dict(open_entry,
                                      history=open_entry['history'] + [withdrawn_ev])]))

        # OPEN -> RULED with WITHDRAWN event
        ruled_entry = dict(
            open_entry, state='RULED',
            ruling={
                'outcome': 'UPHELD', 'head_sha': HEAD, 'reviewer': 'codex-reviewer',
                'reviewer_task': ARBITER_REVIEWER, 'reviewer_session': CODEX_THREAD,
                'evidence_ref': RULING_REF, 'ruled_at': STAMP_2,
            },
            history=open_entry['history'] + [withdrawn_ev],
        )
        self._assert_transition_refused(
            store, task_path,
            lambda: store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_disputes=[ruled_entry]))

        # Rewritten earlier event on OPEN -> WITHDRAWN
        rewritten = [_history_event('OPENED', at=STAMP_2), withdrawn_ev]
        self._assert_transition_refused(
            store, task_path,
            lambda: store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_disputes=[dict(open_entry, state='WITHDRAWN', history=rewritten)]))

        # Removed earlier event on OPEN -> WITHDRAWN
        self._assert_transition_refused(
            store, task_path,
            lambda: store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_disputes=[dict(open_entry, state='WITHDRAWN',
                                      history=[withdrawn_ev])]))

        # Dropping history on OPEN -> WITHDRAWN
        no_hist = {k: v for k, v in open_entry.items() if k != 'history'}
        no_hist['state'] = 'WITHDRAWN'
        self._assert_transition_refused(
            store, task_path,
            lambda: store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_disputes=[no_hist]))

        # OPEN -> WITHDRAWN with a non-WITHDRAWN event
        self._assert_transition_refused(
            store, task_path,
            lambda: store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_disputes=[dict(open_entry, state='WITHDRAWN',
                                      history=open_entry['history'] + [
                                          _history_event('RULED', actor='llmtool',
                                                         at=STAMP_2)])]))

        # Second WITHDRAWN event after entry is already WITHDRAWN
        task_submission_cli.dispute_withdraw(
            store, TASK, pr=PR, finding_id='F1', head=HEAD)
        rec_after = store.get(TASK)
        withdrawn_entry = rec_after['review_disputes'][0]
        self._assert_transition_refused(
            store, task_path,
            lambda: store.compare_and_update(
                TASK, expected={'record_sha256': rec_after['record_sha256']},
                review_disputes=[dict(withdrawn_entry,
                                      history=withdrawn_entry['history'] + [
                                          _history_event('WITHDRAWN', at=STAMP_3)])]))


if __name__ == '__main__':
    unittest.main()
