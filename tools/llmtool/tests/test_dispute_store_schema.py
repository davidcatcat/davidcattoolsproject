"""#2087 (Advisor slice A1-i): TaskStore schema and transitions for dispute arbitration.

Exercises the real TaskStore (compare_and_update, get) on a temporary store
directory with no store mock. All IDs, sessions and digests are fixed
placeholder values (Owner #763 A).
"""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from llmtool import task_store
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
CLAUDE_UUID = '00000000-0000-4000-8000-000000000001'
CODEX_THREAD = '0123456789abcdef0123456789abcdef'
RULES_DIGEST = '1' * 64
DISPUTE_TEXT_DIGEST = '2' * 64
OUTPUT_DIGEST = '3' * 64
EVENT_ID = 'e' * 32
PAYLOAD_DIGEST = 'f' * 64
STAMP_1 = '2026-11-23T00:00:00Z'
STAMP_2 = '2026-11-23T00:01:00Z'
STAMP_3 = '2026-11-23T00:02:00Z'
STAMP_4 = '2026-11-23T00:03:00Z'
REF = 'https://github.com/example/repo/pull/42#issuecomment-1001'
RULING_REF = 'https://github.com/example/repo/pull/42#issuecomment-1002'
RULING_COMMENT_ID = 1002


def _canonical_claim_sha256(claim):
    payload = json.dumps(claim, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def _text_sha256(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _ruling_text(outcome='UPHELD', actor='Codex'):
    return (f'<!-- activity: type=dispute_ruling actor={actor} target=PM status={outcome} -->\n'
            f'Ruling: F1 {outcome} | PR: #{PR} | Version: 1 | HEAD: {HEAD}\n'
            f'LLMTOOL-REVIEW-BINDING v1 task={TASK} run={RUN} contract={CONTRACT} head={HEAD}\n'
            'Arbitration reasoning.')


def _base_record(*, with_dispute=True, with_history=False):
    contract = {
        'schema_version': 1, 'contract_version': 1, 'task_id': TASK,
        'goal': 'test dispute store schema', 'authorized_scope': ['pm-planning'],
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
            'task_id': TASK, 'run_id': RUN, 'contract_digest': parsed.binding(RUN).contract_digest,
            'head': HEAD, 'verdict': 'FAIL',
            'review_text': 'Finding: F1 [High] a.py:1 missing guard',
        },
    }
    record = {
        'schema_version': 2, 'task_id': TASK, 'run_id': RUN,
        'pm_session_id': PM_SESSION, 'pm_provider_profile': 'test',
        'pm_provider_type': 'test', 'pm_model': None,
        'pm_route_binding': {'type': 'test', 'model': None, 'base_url': None},
        'backend_session_semantics': 'NOT_STARTED', 'native_session_id': None,
        'state': 'PM_PENDING', 'created_at': STAMP_1, 'updated_at': STAMP_1,
        'task_contract': contract, 'task_binding': vars(parsed.binding(RUN)),
        'request': 'test dispute store schema',
        'request_sha256': hashlib.sha256(b'test dispute store schema').hexdigest(),
        'response': None, 'usage': None, 'error_code': None,
        'developer': None, 'pm_assignment': None, 'completion': None,
        'review_dispatch_claim': [formal_claim],
    }
    if with_dispute:
        entry = {
            'finding_id': 'F1', 'pr': PR, 'head_sha': HEAD, 'review_version': 1,
            'severity': 'High', 'state': 'OPEN', 'evidence_ref': REF,
            'opened_at': STAMP_1,
        }
        if with_history:
            entry['history'] = [{
                'event': 'OPENED', 'at': STAMP_1, 'actor': f'pm:{PM_SESSION}',
                'head_sha': HEAD, 'refs': {},
            }]
        record['review_disputes'] = [entry]
    return record


def _arbiter_claim(*, reviewer='codex-reviewer', state='CLAIMED', version=2,
                   session=None, review_result=None):
    claim = {
        'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': version,
        'state': state, 'reviewer_task': ARBITER_REVIEWER,
        'native_session_id': session,
        'account_mode': 'ROLES', 'account': 'reviewer-b',
        'rules_sha256': RULES_DIGEST,
        'claimed_at': STAMP_2, 'updated_at': STAMP_2,
        'purpose': 'dispute_ruling',
        'dispute': {
            'pr': PR, 'finding_id': 'F1', 'head_sha': HEAD,
            'review_version': 1, 'dispute_text_sha256': DISPUTE_TEXT_DIGEST,
        },
        'reviewer': reviewer,
    }
    if review_result is not None:
        claim['review_result'] = review_result
    return claim


def _ruling_result(contract_digest, *, verdict='RULING', outcome='UPHELD',
                   text=None, source_kind='CODEX_JSONL', output_sha256=OUTPUT_DIGEST):
    if text is None:
        text = '' if verdict == 'BLOCKED' else _ruling_text(outcome=outcome)
    result = {
        'task_id': TASK, 'run_id': RUN, 'contract_digest': contract_digest,
        'head': HEAD, 'verdict': verdict, 'review_text': text,
        'source': {'kind': source_kind, 'output_sha256': output_sha256},
    }
    if verdict == 'RULING':
        result['outcome'] = outcome
    return result


def _history_event(event, *, actor='llmtool', at=STAMP_2, refs=None):
    return {
        'event': event, 'at': at, 'actor': actor, 'head_sha': HEAD,
        'refs': {} if refs is None else refs,
    }


class DisputeStoreSchemaTests(unittest.TestCase):
    def _new_store(self, *, with_dispute=True, with_history=False):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = task_store.TaskStore(Path(temp.name).resolve())
        rec = store.create(_base_record(with_dispute=False))
        if with_dispute:
            entry = {
                'finding_id': 'F1', 'pr': PR, 'head_sha': HEAD, 'review_version': 1,
                'severity': 'High', 'state': 'OPEN', 'evidence_ref': REF,
                'opened_at': STAMP_1,
            }
            if with_history:
                entry['history'] = [{
                    'event': 'OPENED', 'at': STAMP_1, 'actor': f'pm:{PM_SESSION}',
                    'head_sha': HEAD, 'refs': {},
                }]
            store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_disputes=[entry])
        return store, Path(temp.name).resolve() / f'{TASK}.json'

    def _assert_refused_unchanged(self, store, task_path, fn):
        before = task_path.read_bytes()
        with self.assertRaises(LLMToolError):
            fn()
        self.assertEqual(task_path.read_bytes(), before)

    def _claim_arbiter(self, store, *, reviewer='codex-reviewer', with_history=False):
        rec = store.get(TASK)
        claim = _arbiter_claim(reviewer=reviewer, state='CLAIMED', version=2)
        entry = dict(rec['review_disputes'][0])
        entry['arbiter_claim'] = {'reviewer_task': ARBITER_REVIEWER, 'version': 2}
        if with_history:
            entry['history'] = list(entry.get('history', [])) + [
                _history_event('ARBITER_CLAIMED', refs={
                    'reviewer_task': ARBITER_REVIEWER, 'version': 2,
                }),
            ]
        return store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_dispatch_claim=rec['review_dispatch_claim'] + [claim],
            review_disputes=[entry])

    def _complete_codex_arbiter(self, store, *, outcome='UPHELD', verdict='RULING',
                                with_history=False):
        rec = store.get(TASK)
        contract_digest = rec['task_binding']['contract_digest']
        text = '' if verdict == 'BLOCKED' else _ruling_text(outcome=outcome, actor='Codex')
        result = _ruling_result(contract_digest, verdict=verdict, outcome=outcome,
                                text=text, source_kind='CODEX_JSONL')
        completed = dict(rec['review_dispatch_claim'][1],
                         state='COMPLETED', native_session_id=CODEX_THREAD,
                         updated_at=STAMP_3, review_result=result)
        changes = {'review_dispatch_claim': [rec['review_dispatch_claim'][0], completed]}
        if with_history:
            entry = dict(rec['review_disputes'][0])
            ev_name = 'ARBITER_WITHHELD' if verdict == 'BLOCKED' else 'ARBITER_COMPLETED'
            refs = {'output_sha256': OUTPUT_DIGEST}
            if verdict == 'RULING':
                refs['claim_sha256'] = _canonical_claim_sha256(completed)
                refs['review_text_sha256'] = _text_sha256(text)
            entry['history'] = list(entry.get('history', [])) + [
                _history_event(ev_name, at=STAMP_3, refs=refs),
            ]
            changes['review_disputes'] = [entry]
        return store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']}, **changes)

    def _dispatch_claude_arbiter(self, store, *, with_history=False):
        rec = store.get(TASK)
        dispatched = dict(rec['review_dispatch_claim'][1],
                          state='DISPATCHED', native_session_id=CLAUDE_UUID,
                          updated_at=STAMP_3)
        changes = {'review_dispatch_claim': [rec['review_dispatch_claim'][0], dispatched]}
        if with_history:
            entry = dict(rec['review_disputes'][0])
            entry['history'] = list(entry.get('history', [])) + [
                _history_event('ARBITER_DISPATCHED', at=STAMP_3, refs={
                    'reviewer_task': ARBITER_REVIEWER, 'version': 2,
                }),
            ]
            changes['review_disputes'] = [entry]
        return store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']}, **changes)

    def _complete_claude_arbiter(self, store, *, outcome='WITHDRAWN', verdict='RULING',
                                 with_history=False):
        rec = store.get(TASK)
        contract_digest = rec['task_binding']['contract_digest']
        text = '' if verdict == 'BLOCKED' else _ruling_text(outcome=outcome, actor='Claude')
        result = _ruling_result(contract_digest, verdict=verdict, outcome=outcome,
                                text=text, source_kind='CLAUDE_TRANSCRIPT')
        completed = dict(rec['review_dispatch_claim'][1],
                         state='COMPLETED', updated_at=STAMP_4, review_result=result)
        changes = {'review_dispatch_claim': [rec['review_dispatch_claim'][0], completed]}
        if with_history:
            entry = dict(rec['review_disputes'][0])
            ev_name = 'ARBITER_WITHHELD' if verdict == 'BLOCKED' else 'ARBITER_COMPLETED'
            refs = {'output_sha256': OUTPUT_DIGEST}
            if verdict == 'RULING':
                refs['claim_sha256'] = _canonical_claim_sha256(completed)
                refs['review_text_sha256'] = _text_sha256(text)
            entry['history'] = list(entry.get('history', [])) + [
                _history_event(ev_name, at=STAMP_4, refs=refs),
            ]
            changes['review_disputes'] = [entry]
        return store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']}, **changes)

    def _arbitrate_ruling_dict(self, rec, claim):
        result = claim['review_result']
        return {
            'outcome': result['outcome'],
            'head_sha': HEAD,
            'reviewer': claim['reviewer'],
            'reviewer_task': claim['reviewer_task'],
            'reviewer_session': claim['native_session_id'],
            'evidence_ref': RULING_REF,
            'ruled_at': STAMP_4,
            'path': 'arbitrate',
            'finding_id': 'F1',
            'review_version': 1,
            'arbiter_claim_version': claim['version'],
            'rules_sha256': RULES_DIGEST,
            'review_text_sha256': _text_sha256(result['review_text']),
            'claim_sha256': _canonical_claim_sha256(claim),
            'source_kind': result['source']['kind'],
            'output_sha256': result['source']['output_sha256'],
            'comment_id': RULING_COMMENT_ID,
            'contract_digest': rec['task_binding']['contract_digest'],
            'run_id': RUN,
        }

    # --- Round trips ---

    def test_codex_ruling_round_trip_upheld_and_withdrawn(self):
        for outcome in ('UPHELD', 'WITHDRAWN'):
            with self.subTest(outcome=outcome):
                store, _ = self._new_store(with_history=True)
                claimed = self._claim_arbiter(store, reviewer='codex-reviewer', with_history=True)
                self.assertEqual(claimed['review_dispatch_claim'][1]['state'], 'CLAIMED')
                completed = self._complete_codex_arbiter(store, outcome=outcome, with_history=True)
                read_back = store.get(TASK)
                self.assertEqual(read_back, completed)
                self.assertEqual(read_back['review_dispatch_claim'][1]['review_result']['outcome'], outcome)

    def test_claude_ruling_round_trip_via_dispatched(self):
        store, _ = self._new_store(with_history=True)
        self._claim_arbiter(store, reviewer='claude-reviewer', with_history=True)
        self._dispatch_claude_arbiter(store, with_history=True)
        completed = self._complete_claude_arbiter(store, outcome='UPHELD', with_history=True)
        read_back = store.get(TASK)
        self.assertEqual(read_back, completed)
        self.assertEqual(read_back['review_dispatch_claim'][1]['review_result']['source']['kind'],
                         'CLAUDE_TRANSCRIPT')

    def test_withheld_blocked_round_trip_both_routes(self):
        store_codex, _ = self._new_store(with_history=True)
        self._claim_arbiter(store_codex, reviewer='codex-reviewer', with_history=True)
        saved_codex = self._complete_codex_arbiter(store_codex, verdict='BLOCKED', with_history=True)
        self.assertEqual(store_codex.get(TASK), saved_codex)
        self.assertEqual(saved_codex['review_dispatch_claim'][1]['review_result']['verdict'], 'BLOCKED')

        store_claude, _ = self._new_store(with_history=True)
        self._claim_arbiter(store_claude, reviewer='claude-reviewer', with_history=True)
        self._dispatch_claude_arbiter(store_claude, with_history=True)
        saved_claude = self._complete_claude_arbiter(store_claude, verdict='BLOCKED', with_history=True)
        self.assertEqual(store_claude.get(TASK), saved_claude)
        self.assertEqual(saved_claude['review_dispatch_claim'][1]['review_result']['verdict'], 'BLOCKED')

    def test_ruled_arbitrate_round_trip_and_legacy_entry_validates(self):
        store, _ = self._new_store(with_history=True)
        self._claim_arbiter(store, reviewer='codex-reviewer', with_history=True)
        rec = self._complete_codex_arbiter(store, outcome='WITHDRAWN', with_history=True)
        claim = rec['review_dispatch_claim'][1]
        entry = dict(rec['review_disputes'][0])
        entry['state'] = 'RULED'
        entry['ruling'] = self._arbitrate_ruling_dict(rec, claim)
        entry['history'] = list(entry['history']) + [
            _history_event('RULED', at=STAMP_4, refs={
                'comment_id': RULING_COMMENT_ID,
                'claim_sha256': entry['ruling']['claim_sha256'],
                'review_text_sha256': entry['ruling']['review_text_sha256'],
                'output_sha256': OUTPUT_DIGEST,
            }),
        ]
        saved = store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_disputes=[entry])
        self.assertEqual(store.get(TASK), saved)

        # New legacy-set OPEN -> RULED write refuses REVIEW_DISPUTE_TRANSITION_INVALID (#2087 PR A4).
        legacy_store, legacy_path = self._new_store(with_history=False)
        rec_leg = legacy_store.get(TASK)
        leg_entry = dict(rec_leg['review_disputes'][0], state='RULED', ruling={
            'outcome': 'UPHELD', 'head_sha': HEAD, 'reviewer': 'codex-reviewer',
            'reviewer_task': ARBITER_REVIEWER, 'reviewer_session': CODEX_THREAD,
            'evidence_ref': RULING_REF, 'ruled_at': STAMP_2,
        })
        before_leg = legacy_path.read_bytes()
        with self.assertRaises(LLMToolError) as caught:
            legacy_store.compare_and_update(
                TASK, expected={'record_sha256': rec_leg['record_sha256']},
                review_disputes=[leg_entry])
        self.assertEqual(caught.exception.task_store_error_code,
                         'REVIEW_DISPUTE_TRANSITION_INVALID')
        self.assertEqual(legacy_path.read_bytes(), before_leg)

        # Stored legacy RULED entry (persisted before A4) still validates and reads back.
        stored_leg = json.loads(before_leg.decode('utf-8'))
        stored_leg['review_disputes'] = [leg_entry]
        stored_leg['record_sha256'] = task_store._digest(stored_leg)
        legacy_path.write_bytes(task_store._json_bytes(stored_leg))
        loaded_leg = legacy_store.get(TASK)
        self.assertEqual(loaded_leg['review_disputes'], [leg_entry])
        task_store._validate(loaded_leg)

    def test_open_to_withdrawn_still_accepted(self):
        store, _ = self._new_store(with_history=False)
        rec = store.get(TASK)
        entry = dict(rec['review_disputes'][0], state='WITHDRAWN')
        saved = store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_disputes=[entry])
        self.assertEqual(saved['review_disputes'][0]['state'], 'WITHDRAWN')

    # --- Transition refusals ---

    def test_refuse_formal_dispatched_to_completed(self):
        store, path = self._new_store()
        rec = store.get(TASK)
        formal_claimed = {
            'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': 2,
            'state': 'CLAIMED', 'reviewer_task': FORMAL_REVIEWER,
            'native_session_id': None, 'account_mode': 'ROLES',
            'claimed_at': STAMP_2, 'updated_at': STAMP_2,
        }
        rec = store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_dispatch_claim=rec['review_dispatch_claim'] + [formal_claimed])
        formal_dispatched = dict(formal_claimed, state='DISPATCHED',
                                 native_session_id=CLAUDE_UUID, updated_at=STAMP_2)
        rec = store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_dispatch_claim=[rec['review_dispatch_claim'][0], formal_dispatched])
        completed = dict(formal_dispatched, state='COMPLETED', updated_at=STAMP_3, review_result={
            'task_id': TASK, 'run_id': RUN, 'contract_digest': rec['task_binding']['contract_digest'],
            'head': HEAD, 'verdict': 'PASS', 'review_text': 'PASS',
        })
        self._assert_refused_unchanged(
            store, path,
            lambda: store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_dispatch_claim=[rec['review_dispatch_claim'][0], completed]))

    def test_refuse_ruling_transition_mismatches(self):
        # Codex DISPATCHED -> COMPLETED refused.
        store, path = self._new_store(with_history=True)
        self._claim_arbiter(store, reviewer='codex-reviewer', with_history=True)
        rec = store.get(TASK)
        dispatched_codex = dict(rec['review_dispatch_claim'][1],
                                state='DISPATCHED', native_session_id=CODEX_THREAD,
                                updated_at=STAMP_3)
        rec = store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_dispatch_claim=[rec['review_dispatch_claim'][0], dispatched_codex])
        completed_codex = dict(
            dispatched_codex, state='COMPLETED', updated_at=STAMP_4,
            review_result=_ruling_result(rec['task_binding']['contract_digest'],
                                         source_kind='CODEX_JSONL'))
        self._assert_refused_unchanged(
            store, path,
            lambda: store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_dispatch_claim=[rec['review_dispatch_claim'][0], completed_codex]))

        # Claude CLAIMED -> COMPLETED refused.
        store2, path2 = self._new_store(with_history=True)
        self._claim_arbiter(store2, reviewer='claude-reviewer', with_history=True)
        rec2 = store2.get(TASK)
        completed_claude = dict(
            rec2['review_dispatch_claim'][1], state='COMPLETED',
            native_session_id=CLAUDE_UUID, updated_at=STAMP_3,
            review_result=_ruling_result(rec2['task_binding']['contract_digest'],
                                         source_kind='CLAUDE_TRANSCRIPT'))
        self._assert_refused_unchanged(
            store2, path2,
            lambda: store2.compare_and_update(
                TASK, expected={'record_sha256': rec2['record_sha256']},
                review_dispatch_claim=[rec2['review_dispatch_claim'][0], completed_claude]))

        # Claude DISPATCHED -> COMPLETED with CODEX_JSONL refused.
        store3, path3 = self._new_store(with_history=True)
        self._claim_arbiter(store3, reviewer='claude-reviewer', with_history=True)
        rec3 = self._dispatch_claude_arbiter(store3, with_history=True)
        bad_kind = dict(
            rec3['review_dispatch_claim'][1], state='COMPLETED', updated_at=STAMP_4,
            review_result=_ruling_result(rec3['task_binding']['contract_digest'],
                                         source_kind='CODEX_JSONL'))
        self._assert_refused_unchanged(
            store3, path3,
            lambda: store3.compare_and_update(
                TASK, expected={'record_sha256': rec3['record_sha256']},
                review_dispatch_claim=[rec3['review_dispatch_claim'][0], bad_kind]))

    def test_refuse_dispatched_to_completed_mutating_immutable_keys(self):
        store, path = self._new_store(with_history=True)
        self._claim_arbiter(store, reviewer='claude-reviewer', with_history=True)
        rec = self._dispatch_claude_arbiter(store, with_history=True)
        cd = rec['task_binding']['contract_digest']
        base_completed = dict(
            rec['review_dispatch_claim'][1], state='COMPLETED', updated_at=STAMP_4,
            review_result=_ruling_result(cd, source_kind='CLAUDE_TRANSCRIPT',
                                         text=_ruling_text(actor='Claude')))
        for mut in (
            {'native_session_id': '00000000-0000-4000-8000-000000000099'},
            {'reviewer_task': 'reviewer-00000000000000000099'},
            {'account': 'other-account'},
            {'account_mode': 'EXPLICIT'},
            {'purpose': 'other'},
            {'reviewer': 'codex-reviewer'},
            {'dispute': dict(base_completed['dispute'], review_version=2)},
        ):
            with self.subTest(mut=mut):
                bad = dict(base_completed, **mut)
                self._assert_refused_unchanged(
                    store, path,
                    lambda b=bad: store.compare_and_update(
                        TASK, expected={'record_sha256': rec['record_sha256']},
                        review_dispatch_claim=[rec['review_dispatch_claim'][0], b]))

    def test_refuse_unknown_to_dispatched_settle_on_ruling_claim(self):
        store, path = self._new_store(with_history=True)
        store.record_completion_event(
            TASK, event_id=EVENT_ID, run_id=RUN, session_id='review-' + EVENT_ID,
            source='REVIEWER', source_sha=HEAD, sequence=0, payload_sha256=PAYLOAD_DIGEST)
        self._claim_arbiter(store, reviewer='claude-reviewer', with_history=True)
        rec = store.get(TASK)
        unknown_claim = dict(rec['review_dispatch_claim'][1],
                             state='UNKNOWN', native_session_id=CLAUDE_UUID,
                             updated_at=STAMP_3)
        rec = store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_dispatch_claim=[rec['review_dispatch_claim'][0], unknown_claim])
        settled = dict(unknown_claim, state='DISPATCHED', updated_at=STAMP_4)
        self._assert_refused_unchanged(
            store, path,
            lambda: store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_dispatch_claim=[rec['review_dispatch_claim'][0], settled]))

    # --- Ruling result refusals ---

    def test_refuse_invalid_ruling_results(self):
        store, path = self._new_store(with_history=True)
        self._claim_arbiter(store, reviewer='codex-reviewer', with_history=True)
        rec = store.get(TASK)
        cd = rec['task_binding']['contract_digest']
        base_claim = dict(rec['review_dispatch_claim'][1], state='COMPLETED',
                          native_session_id=CODEX_THREAD, updated_at=STAMP_3)

        bad_results = [
            # RULING without outcome
            {k: v for k, v in _ruling_result(cd).items() if k != 'outcome'},
            # outcome outside UPHELD/WITHDRAWN
            _ruling_result(cd, outcome='DEFERRED'),
            # outcome on BLOCKED
            dict(_ruling_result(cd, verdict='BLOCKED'), outcome='UPHELD'),
            # BLOCKED with text
            _ruling_result(cd, verdict='BLOCKED', text='non-empty'),
            # missing source key
            dict(_ruling_result(cd), source={'kind': 'CODEX_JSONL'}),
            # extra source key
            dict(_ruling_result(cd), source={'kind': 'CODEX_JSONL',
                                             'output_sha256': OUTPUT_DIGEST, 'extra': 1}),
            # source.kind mismatched with reviewer
            _ruling_result(cd, source_kind='CLAUDE_TRANSCRIPT'),
            # extra review_result key
            dict(_ruling_result(cd), extra='bad'),
        ]
        for bad_res in bad_results:
            with self.subTest(bad_res=bad_res):
                bad_claim = dict(base_claim, review_result=bad_res)
                self._assert_refused_unchanged(
                    store, path,
                    lambda bc=bad_claim: store.compare_and_update(
                        TASK, expected={'record_sha256': rec['record_sha256']},
                        review_dispatch_claim=[rec['review_dispatch_claim'][0], bc]))

    def test_refuse_ruling_fields_on_formal_claim(self):
        store, path = self._new_store()
        rec = store.get(TASK)
        cd = rec['task_binding']['contract_digest']
        formal = {
            'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': 2,
            'state': 'CLAIMED', 'reviewer_task': FORMAL_REVIEWER,
            'native_session_id': None, 'account_mode': 'ROLES',
            'claimed_at': STAMP_2, 'updated_at': STAMP_2,
        }
        rec = store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_dispatch_claim=rec['review_dispatch_claim'] + [formal])
        for bad_res in (
            {'task_id': TASK, 'run_id': RUN, 'contract_digest': cd, 'head': HEAD,
             'verdict': 'RULING', 'review_text': 'text'},
            {'task_id': TASK, 'run_id': RUN, 'contract_digest': cd, 'head': HEAD,
             'verdict': 'PASS', 'review_text': 'PASS', 'outcome': 'UPHELD'},
            {'task_id': TASK, 'run_id': RUN, 'contract_digest': cd, 'head': HEAD,
             'verdict': 'PASS', 'review_text': 'PASS',
             'source': {'kind': 'CODEX_JSONL', 'output_sha256': OUTPUT_DIGEST}},
        ):
            with self.subTest(bad_res=bad_res):
                completed = dict(formal, state='COMPLETED', native_session_id=CODEX_THREAD,
                                 updated_at=STAMP_3, review_result=bad_res)
                self._assert_refused_unchanged(
                    store, path,
                    lambda c=completed: store.compare_and_update(
                        TASK, expected={'record_sha256': rec['record_sha256']},
                        review_dispatch_claim=[rec['review_dispatch_claim'][0], c]))

    # --- Claim key refusals ---

    def test_refuse_invalid_claim_keys(self):
        store, path = self._new_store()
        rec = store.get(TASK)
        base = _arbiter_claim(reviewer='codex-reviewer', state='CLAIMED', version=2)
        bad_claims = [
            # partial set
            {k: v for k, v in base.items() if k != 'reviewer'},
            {k: v for k, v in base.items() if k != 'dispute'},
            {k: v for k, v in base.items() if k != 'purpose'},
            # unknown purpose
            dict(base, purpose='formal_review'),
            # dispute differs from entry
            dict(base, dispute=dict(base['dispute'], finding_id='F2')),
            dict(base, dispute=dict(base['dispute'], review_version=2)),
            # claim pr or head_sha differs from dispute
            dict(base, pr=99),
            dict(base, head_sha='e' * 40),
            # non-64-hex dispute_text_sha256
            dict(base, dispute=dict(base['dispute'], dispute_text_sha256='bad')),
        ]
        for bad in bad_claims:
            with self.subTest(bad=bad):
                self._assert_refused_unchanged(
                    store, path,
                    lambda b=bad: store.compare_and_update(
                        TASK, expected={'record_sha256': rec['record_sha256']},
                        review_dispatch_claim=rec['review_dispatch_claim'] + [b]))

    # --- RULED evidence refusals ---

    def test_refuse_invalid_ruled_evidence(self):
        store, path = self._new_store(with_history=True)
        self._claim_arbiter(store, reviewer='codex-reviewer', with_history=True)
        rec = self._complete_codex_arbiter(store, outcome='UPHELD', with_history=True)
        claim = rec['review_dispatch_claim'][1]
        valid_ruling = self._arbitrate_ruling_dict(rec, claim)

        bad_rulings = [
            # partial arbitrate set
            {k: v for k, v in valid_ruling.items() if k != 'claim_sha256'},
            # extra key
            dict(valid_ruling, extra='bad'),
            # wrong path
            dict(valid_ruling, path='manual'),
            # non-64-hex digest
            dict(valid_ruling, rules_sha256='not-hex'),
            # non-int / bool comment_id
            dict(valid_ruling, comment_id=True),
            dict(valid_ruling, comment_id='1002'),
            # mismatched fields against claim / entry
            dict(valid_ruling, outcome='WITHDRAWN'),
            dict(valid_ruling, reviewer_session='wrong-session'),
            dict(valid_ruling, reviewer='claude-reviewer'),
            dict(valid_ruling, reviewer_task='reviewer-00000000000000000099'),
            dict(valid_ruling, arbiter_claim_version=3),
            dict(valid_ruling, source_kind='CLAUDE_TRANSCRIPT'),
            dict(valid_ruling, review_text_sha256='9' * 64),
            dict(valid_ruling, claim_sha256='9' * 64),
            dict(valid_ruling, finding_id='F2'),
            dict(valid_ruling, review_version=2),
            dict(valid_ruling, review_version=True),
        ]
        for bad_r in bad_rulings:
            with self.subTest(bad_r=bad_r):
                entry = dict(
                    rec['review_disputes'][0], state='RULED', ruling=bad_r,
                    history=list(rec['review_disputes'][0]['history']) + [
                        _history_event('RULED', at=STAMP_4),
                    ])
                self._assert_refused_unchanged(
                    store, path,
                    lambda e=entry: store.compare_and_update(
                        TASK, expected={'record_sha256': rec['record_sha256']},
                        review_disputes=[e]))

    def test_refuse_ruled_arbitrate_when_claim_blocked_or_missing(self):
        store, path = self._new_store(with_history=True)
        self._claim_arbiter(store, reviewer='codex-reviewer', with_history=True)
        rec = self._complete_codex_arbiter(store, verdict='BLOCKED', with_history=True)
        claim = rec['review_dispatch_claim'][1]
        ruling = {
            'outcome': 'UPHELD', 'head_sha': HEAD, 'reviewer': 'codex-reviewer',
            'reviewer_task': ARBITER_REVIEWER, 'reviewer_session': CODEX_THREAD,
            'evidence_ref': RULING_REF, 'ruled_at': STAMP_4,
            'path': 'arbitrate', 'finding_id': 'F1', 'review_version': 1,
            'arbiter_claim_version': 2, 'rules_sha256': RULES_DIGEST,
            'review_text_sha256': _text_sha256(''),
            'claim_sha256': _canonical_claim_sha256(claim),
            'source_kind': 'CODEX_JSONL', 'output_sha256': OUTPUT_DIGEST,
            'comment_id': RULING_COMMENT_ID,
            'contract_digest': rec['task_binding']['contract_digest'], 'run_id': RUN,
        }
        entry = dict(
            rec['review_disputes'][0], state='RULED', ruling=ruling,
            history=list(rec['review_disputes'][0]['history']) + [
                _history_event('RULED', at=STAMP_4),
            ])
        self._assert_refused_unchanged(
            store, path,
            lambda: store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_disputes=[entry]))

    # --- Entry history / arbiter_claim refusals ---

    def test_refuse_entry_history_and_arbiter_claim_violations(self):
        store, path = self._new_store(with_history=True)
        rec = store.get(TASK)
        # arbiter_claim set without CLAIMED claim in same write
        bad_entry = dict(rec['review_disputes'][0],
                         arbiter_claim={'reviewer_task': ARBITER_REVIEWER, 'version': 2},
                         history=rec['review_disputes'][0]['history'] + [
                             _history_event('ARBITER_CLAIMED', refs={
                                 'reviewer_task': ARBITER_REVIEWER, 'version': 2})])
        self._assert_refused_unchanged(
            store, path,
            lambda: store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_disputes=[bad_entry]))

        # Claim arbiter legitimately, then test history/arbiter_claim mutations
        rec = self._claim_arbiter(store, reviewer='codex-reviewer', with_history=True)
        entry = rec['review_disputes'][0]
        # history shortened
        self._assert_refused_unchanged(
            store, path,
            lambda: store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_disputes=[dict(entry, history=entry['history'][:1])]))
        # history rewritten
        rewritten = [_history_event('OPENED', actor=f'pm:{PM_SESSION}', at=STAMP_4)] + entry['history'][1:]
        self._assert_refused_unchanged(
            store, path,
            lambda: store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_disputes=[dict(entry, history=rewritten)]))
        # history grown by 2
        grown2 = entry['history'] + [
            _history_event('ARBITER_UNKNOWN'),
            _history_event('ARBITER_COMPLETED'),
        ]
        self._assert_refused_unchanged(
            store, path,
            lambda: store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_disputes=[dict(entry, history=grown2)]))
        # arbiter_claim changed
        self._assert_refused_unchanged(
            store, path,
            lambda: store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_disputes=[dict(entry, arbiter_claim={'reviewer_task': ARBITER_REVIEWER,
                                                            'version': 3})]))

    # --- Formal unchanged ---

    def test_formal_claims_unchanged(self):
        for verdict in ('PASS', 'FIX', 'FAIL', 'BLOCKED'):
            with self.subTest(verdict=verdict):
                store, _ = self._new_store(with_dispute=False)
                rec = store.get(TASK)
                cd = rec['task_binding']['contract_digest']
                formal = {
                    'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': 2,
                    'state': 'CLAIMED', 'reviewer_task': FORMAL_REVIEWER,
                    'native_session_id': None, 'account_mode': 'ROLES',
                    'claimed_at': STAMP_2, 'updated_at': STAMP_2,
                }
                rec = store.compare_and_update(
                    TASK, expected={'record_sha256': rec['record_sha256']},
                    review_dispatch_claim=rec['review_dispatch_claim'] + [formal])
                completed = dict(formal, state='COMPLETED', native_session_id=CODEX_THREAD,
                                 updated_at=STAMP_3, review_result={
                                     'task_id': TASK, 'run_id': RUN, 'contract_digest': cd,
                                     'head': HEAD, 'verdict': verdict,
                                     'review_text': f'Verdict: {verdict}',
                                 })
                saved = store.compare_and_update(
                    TASK, expected={'record_sha256': rec['record_sha256']},
                    review_dispatch_claim=[rec['review_dispatch_claim'][0], completed])
                self.assertEqual(saved['review_dispatch_claim'][1]['review_result']['verdict'],
                                 verdict)

        # Formal CLAIMED -> DISPATCHED and #1636 UNKNOWN -> DISPATCHED settle.
        store2, _ = self._new_store(with_dispute=False)
        store2.record_completion_event(
            TASK, event_id=EVENT_ID, run_id=RUN, session_id='review-' + EVENT_ID,
            source='REVIEWER', source_sha=HEAD, sequence=0, payload_sha256=PAYLOAD_DIGEST)
        rec2 = store2.get(TASK)
        formal2 = {
            'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': 2,
            'state': 'CLAIMED', 'reviewer_task': FORMAL_REVIEWER,
            'native_session_id': None, 'account_mode': 'ROLES',
            'claimed_at': STAMP_2, 'updated_at': STAMP_2,
        }
        rec2 = store2.compare_and_update(
            TASK, expected={'record_sha256': rec2['record_sha256']},
            review_dispatch_claim=rec2['review_dispatch_claim'] + [formal2])
        unknown2 = dict(formal2, state='UNKNOWN', native_session_id=CLAUDE_UUID,
                        updated_at=STAMP_3)
        rec2 = store2.compare_and_update(
            TASK, expected={'record_sha256': rec2['record_sha256']},
            review_dispatch_claim=[rec2['review_dispatch_claim'][0], unknown2])
        settled2 = dict(unknown2, state='DISPATCHED', updated_at=STAMP_4)
        saved2 = store2.compare_and_update(
            TASK, expected={'record_sha256': rec2['record_sha256']},
            review_dispatch_claim=[rec2['review_dispatch_claim'][0], settled2])
        self.assertEqual(saved2['review_dispatch_claim'][1]['state'], 'DISPATCHED')

    # --- Terminal RULED refusals ---

    def test_terminal_ruled_refuses_entry_and_claim_changes(self):
        store, path = self._new_store(with_history=True)
        self._claim_arbiter(store, reviewer='codex-reviewer', with_history=True)
        rec = self._complete_codex_arbiter(store, outcome='UPHELD', with_history=True)
        claim = rec['review_dispatch_claim'][1]
        entry = dict(rec['review_disputes'][0])
        entry['state'] = 'RULED'
        entry['ruling'] = self._arbitrate_ruling_dict(rec, claim)
        entry['history'] = list(entry['history']) + [_history_event('RULED', at=STAMP_4)]
        rec = store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_disputes=[entry])

        # Entry mutations refused: state back to OPEN, ruling edited, history changed, arbiter_claim changed.
        self._assert_refused_unchanged(
            store, path,
            lambda: store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_disputes=[{k: v for k, v in entry.items() if k != 'ruling'} | {'state': 'OPEN'}]))
        self._assert_refused_unchanged(
            store, path,
            lambda: store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_disputes=[dict(entry, ruling=dict(entry['ruling'], outcome='WITHDRAWN'))]))
        self._assert_refused_unchanged(
            store, path,
            lambda: store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_disputes=[dict(entry, history=entry['history'][:-1])]))
        self._assert_refused_unchanged(
            store, path,
            lambda: store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_disputes=[dict(entry, history=entry['history'] + [_history_event('RULED')])]))
        self._assert_refused_unchanged(
            store, path,
            lambda: store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_disputes=[dict(entry, arbiter_claim={'reviewer_task': ARBITER_REVIEWER,
                                                            'version': 9})]))

        # Claim mutation on a RULED entry refused.
        mutated_claim = dict(claim, updated_at=STAMP_4)
        self._assert_refused_unchanged(
            store, path,
            lambda: store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_dispatch_claim=[rec['review_dispatch_claim'][0], mutated_claim]))


if __name__ == '__main__':
    unittest.main()
