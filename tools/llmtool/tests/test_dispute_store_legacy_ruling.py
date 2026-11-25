"""#2087 (PR A4): TaskStore refuses new legacy-set RULED writes while stored legacy entries validate.

Exercises only the real TaskStore (create, get, update, compare_and_update) on a
temporary store directory, with no TempTaskStore, DisputeStore, or patched store
methods. All IDs, sessions and digests are fixed placeholder values (Owner #763 A).
"""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from llmtool import task_store
from llmtool.errors import LLMToolError


TASK = 'a' * 32
RUN = 'b' * 32
HEAD = 'c' * 40
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
STAMP_4 = '2026-11-23T00:03:00Z'
REF = 'https://github.com/example/repo/pull/42#issuecomment-1001'
REF_2 = 'https://github.com/example/repo/pull/42#issuecomment-1003'
REF_3 = 'https://github.com/example/repo/pull/42#issuecomment-1004'
REF_4 = 'https://github.com/example/repo/pull/42#issuecomment-1005'
RULING_REF = 'https://github.com/example/repo/pull/42#issuecomment-1002'
RULING_COMMENT_ID = 1002


def _base_record():
    contract = {
        'schema_version': 1, 'contract_version': 1, 'task_id': TASK,
        'goal': 'test legacy ruling store gate', 'authorized_scope': ['pm-planning'],
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
        'request': 'test legacy ruling store gate',
        'request_sha256': hashlib.sha256(b'test legacy ruling store gate').hexdigest(),
        'response': None, 'usage': None, 'error_code': None,
        'developer': None, 'pm_assignment': None, 'completion': None,
        'review_dispatch_claim': [formal_claim],
    }


def _open_entry(finding_id='F1', *, evidence_ref=REF, with_history=False):
    entry = {
        'finding_id': finding_id, 'pr': PR, 'head_sha': HEAD, 'review_version': 1,
        'severity': 'High', 'state': 'OPEN', 'evidence_ref': evidence_ref,
        'opened_at': STAMP_1,
    }
    if with_history:
        entry['history'] = [{
            'event': 'OPENED', 'at': STAMP_1, 'actor': f'pm:{PM_SESSION}',
            'head_sha': HEAD, 'refs': {},
        }]
    return entry


def _legacy_ruling(outcome='UPHELD'):
    return {
        'outcome': outcome, 'head_sha': HEAD, 'reviewer': 'codex-reviewer',
        'reviewer_task': ARBITER_REVIEWER, 'reviewer_session': CODEX_THREAD,
        'evidence_ref': RULING_REF, 'ruled_at': STAMP_2,
    }


def _history_event(event, *, actor='llmtool', at=STAMP_2, refs=None):
    return {
        'event': event, 'at': at, 'actor': actor, 'head_sha': HEAD,
        'refs': {} if refs is None else refs,
    }


class DisputeStoreLegacyRulingTests(unittest.TestCase):
    def _new_store(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        return task_store.TaskStore(root), root / f'{TASK}.json'

    def _assert_transition_refused(self, task_path, fn):
        before = task_path.read_bytes()
        with self.assertRaises(LLMToolError) as caught:
            fn()
        self.assertEqual(caught.exception.task_store_error_code,
                         'REVIEW_DISPUTE_TRANSITION_INVALID')
        self.assertEqual(task_path.read_bytes(), before)

    def test_new_legacy_ruled_write_refused_via_update_and_compare_and_update(self):
        for with_history in (False, True):
            for with_other in (False, True):
                with self.subTest(with_history=with_history, with_other=with_other):
                    store, task_path = self._new_store()
                    created = store.create(_base_record())
                    entries = [_open_entry('F1', evidence_ref=REF, with_history=with_history)]
                    if with_other:
                        entries.append(
                            _open_entry('F2', evidence_ref=REF_2, with_history=with_history))
                    rec = store.compare_and_update(
                        TASK, expected={'record_sha256': created['record_sha256']},
                        review_disputes=entries)
                    ruled_f1 = dict(entries[0], state='RULED', ruling=_legacy_ruling())
                    if with_history:
                        ruled_f1['history'] = list(entries[0]['history']) + [
                            _history_event('RULED', at=STAMP_2),
                        ]
                    proposed = [ruled_f1, entries[1]] if with_other else [ruled_f1]
                    self._assert_transition_refused(
                        task_path,
                        lambda: store.update(TASK, review_disputes=proposed))
                    self._assert_transition_refused(
                        task_path,
                        lambda: store.compare_and_update(
                            TASK, expected={'record_sha256': rec['record_sha256']},
                            review_disputes=proposed))

    def test_stored_legacy_ruled_entry_validates_and_allows_later_writes(self):
        # Simulates a legacy RULED entry persisted before A4 (patches nothing in task_store).
        store, task_path = self._new_store()
        created = store.create(_base_record())
        open_f1 = _open_entry('F1', evidence_ref=REF, with_history=False)
        open_f2 = _open_entry('F2', evidence_ref=REF_2, with_history=False)
        open_f3 = _open_entry('F3', evidence_ref=REF_3, with_history=False)
        store.compare_and_update(
            TASK, expected={'record_sha256': created['record_sha256']},
            review_disputes=[open_f1, open_f2, open_f3])

        # Load the persisted record file, set F1 to a legacy RULED entry, recompute
        # digest with task_store._digest, and write atomically.
        disk_record = json.loads(task_path.read_text(encoding='utf-8'))
        legacy_ruled = dict(open_f1, state='RULED', ruling=_legacy_ruling())
        disk_record['review_disputes'] = [legacy_ruled, open_f2, open_f3]
        disk_record['record_sha256'] = task_store._digest(disk_record)
        tmp_path = task_path.with_name(f'.{TASK}.json.tmp')
        tmp_path.write_bytes(task_store._json_bytes(disk_record))
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, task_path)

        # get() reads and validates the stored legacy RULED entry.
        loaded = store.get(TASK)
        self.assertEqual(loaded['review_disputes'][0], legacy_ruled)

        # A later unrelated non-dispute update succeeds and keeps the legacy entry unchanged.
        updated = store.update(TASK, state='PM_UNAVAILABLE', error_code='PROVIDER_TIMEOUT')
        self.assertEqual(updated['state'], 'PM_UNAVAILABLE')
        self.assertEqual(updated['review_disputes'][0], legacy_ruled)

        # Later dispute writes leaving the legacy RULED entry unchanged are accepted.
        open_f4 = _open_entry('F4', evidence_ref=REF_4, with_history=False)
        appended = store.compare_and_update(
            TASK, expected={'record_sha256': updated['record_sha256']},
            review_disputes=[legacy_ruled, open_f2, open_f3, open_f4])
        self.assertEqual(appended['review_disputes'][0], legacy_ruled)

        withdrawn_f2 = dict(open_f2, state='WITHDRAWN')
        withdrawn = store.compare_and_update(
            TASK, expected={'record_sha256': appended['record_sha256']},
            review_disputes=[legacy_ruled, withdrawn_f2, open_f3, open_f4])
        self.assertEqual(withdrawn['review_disputes'][0], legacy_ruled)
        self.assertEqual(withdrawn['review_disputes'][1]['state'], 'WITHDRAWN')

        # A new legacy-set RULED write on another finding still refuses.
        legacy_f3 = dict(open_f3, state='RULED', ruling=_legacy_ruling('WITHDRAWN'))
        self._assert_transition_refused(
            task_path,
            lambda: store.compare_and_update(
                TASK, expected={'record_sha256': withdrawn['record_sha256']},
                review_disputes=[legacy_ruled, withdrawn_f2, legacy_f3, open_f4]))

    def test_arbitrate_ruled_round_trip_still_accepted(self):
        store, _ = self._new_store()
        created = store.create(_base_record())
        cd = created['task_binding']['contract_digest']
        open_f1 = _open_entry('F1', evidence_ref=REF, with_history=True)
        rec = store.compare_and_update(
            TASK, expected={'record_sha256': created['record_sha256']},
            review_disputes=[open_f1])
        ruling_text = (
            '<!-- activity: type=dispute_ruling actor=Codex target=PM status=WITHDRAWN -->\n'
            f'Ruling: F1 WITHDRAWN | PR: #{PR} | Version: 1 | HEAD: {HEAD}\n'
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
        entry_claimed = dict(
            open_f1,
            arbiter_claim={'reviewer_task': ARBITER_REVIEWER, 'version': 2},
            history=list(open_f1['history']) + [
                _history_event('ARBITER_CLAIMED', at=STAMP_2,
                               refs={'reviewer_task': ARBITER_REVIEWER, 'version': 2}),
            ],
        )
        rec = store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_dispatch_claim=rec['review_dispatch_claim'] + [claimed],
            review_disputes=[entry_claimed])
        completed = dict(
            claimed, state='COMPLETED', native_session_id=CODEX_THREAD, updated_at=STAMP_3,
            review_result={
                'task_id': TASK, 'run_id': RUN, 'contract_digest': cd,
                'head': HEAD, 'verdict': 'RULING', 'outcome': 'WITHDRAWN',
                'review_text': ruling_text,
                'source': {'kind': 'CODEX_JSONL', 'output_sha256': OUTPUT_DIGEST},
            },
        )
        entry_completed = dict(
            entry_claimed,
            history=list(entry_claimed['history']) + [
                _history_event('ARBITER_COMPLETED', at=STAMP_3, refs={
                    'output_sha256': OUTPUT_DIGEST,
                    'claim_sha256': task_store._canonical_claim_sha256(completed),
                    'review_text_sha256': hashlib.sha256(
                        ruling_text.encode('utf-8')).hexdigest(),
                }),
            ],
        )
        rec = store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_dispatch_claim=[rec['review_dispatch_claim'][0], completed],
            review_disputes=[entry_completed])
        entry_ruled = dict(
            entry_completed,
            state='RULED',
            ruling={
                'outcome': 'WITHDRAWN', 'head_sha': HEAD, 'reviewer': 'codex-reviewer',
                'reviewer_task': ARBITER_REVIEWER, 'reviewer_session': CODEX_THREAD,
                'evidence_ref': RULING_REF, 'ruled_at': STAMP_4, 'path': 'arbitrate',
                'finding_id': 'F1', 'review_version': 1, 'arbiter_claim_version': 2,
                'rules_sha256': RULES_DIGEST,
                'review_text_sha256': hashlib.sha256(
                    ruling_text.encode('utf-8')).hexdigest(),
                'claim_sha256': task_store._canonical_claim_sha256(completed),
                'source_kind': 'CODEX_JSONL', 'output_sha256': OUTPUT_DIGEST,
                'comment_id': RULING_COMMENT_ID, 'contract_digest': cd, 'run_id': RUN,
            },
            history=list(entry_completed['history']) + [
                _history_event('RULED', at=STAMP_4, refs={'comment_id': RULING_COMMENT_ID}),
            ],
        )
        saved = store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_disputes=[entry_ruled])
        self.assertEqual(store.get(TASK), saved)


if __name__ == '__main__':
    unittest.main()
