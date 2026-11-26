"""#2812: unit tests for usage and usage_unknown recording on dispute arbiter claims."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import (  # noqa: E402
    review_dispatch,
    review_transcript,
    task_store,
    task_submission_cli,
)
from llmtool.errors import LLMToolError  # noqa: E402

import review_trust_fixture  # noqa: E402
import test_dispute_arbitrate_claude_completion as tdacc  # noqa: E402
import test_dispute_arbitrate_launch as tdal  # noqa: E402
import test_dispute_ruling_claim_readers as tdrcr  # noqa: E402
import worker_policy_fixture  # noqa: E402


TASK = tdrcr.TASK
RUN = tdrcr.RUN
HEAD = tdrcr.HEAD
EXECUTOR = tdrcr.EXECUTOR
PM_SESSION = tdrcr.PM_SESSION
PR = tdrcr.PR
FINDING = 'F1'
REVIEW_VERSION = 1
ARBITER_VERSION = 2
THREAD_ID = tdal.THREAD_ID
CLAUDE_SESSION = tdacc.CLAUDE_SESSION
ARBITER_TASK = tdrcr.ARBITER_REVIEWER
DISPUTE_COMMENT_ID = tdal.DISPUTE_COMMENT_ID
RULING_COMMENT_ID = 2002
REVIEW_LOGIN = 'review-account'
REPOSITORY = 'davidcatcat/davidcattoolsproject'
EVIDENCE_REF = f'https://github.com/{REPOSITORY}/pull/{PR}#issuecomment-{DISPUTE_COMMENT_ID}'
STAMP_1 = tdrcr.STAMP_1
STAMP_2 = tdrcr.STAMP_2
STAMP_3 = tdrcr.STAMP_3
STAMP_4 = '2026-11-23T00:03:00Z'


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def _codex_stream(payload, *, thread_id=THREAD_ID, usage=None):
    events = [
        {'type': 'thread.started', 'thread_id': thread_id},
        {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(payload)}},
    ]
    if usage is not None:
        events.append({'type': 'turn.completed', 'usage': usage})
    return ('\n'.join(json.dumps(event) for event in events) + '\n').encode('utf-8')


def _claude_assistant_entry(text, *, usage=None, stop_reason='end_turn'):
    message = {
        'id': 'msg_1',
        'role': 'assistant',
        'stop_reason': stop_reason,
        'content': [{'type': 'text', 'text': text}],
    }
    if usage is not None:
        message['usage'] = usage
    return {'type': 'assistant', 'message': message}


class CodexArbiterUsageTests(tdal._ArbitrateLaunchCase):
    def _run_codex_arbitrate(self, raw_jsonl):
        posted = []

        def comment_get(path):
            if path == f'issues/comments/{DISPUTE_COMMENT_ID}':
                return tdal._dispute_comment(body='Dispute body for F1')
            if path == f'issues/comments/{RULING_COMMENT_ID}':
                return {
                    'id': RULING_COMMENT_ID,
                    'body': posted[0]['body'],
                    'issue_url': f'https://api.github.com/repos/{REPOSITORY}/issues/{PR}',
                    'created_at': '2026-11-23T00:05:00Z',
                    'updated_at': '2026-11-23T00:05:00Z',
                }
            raise AssertionError(f'unexpected github_get: {path}')

        def fake_lister(_pr):
            return [dict(c) for c in posted]

        def fake_poster(_root, pr_num, body):
            comment = {
                'id': RULING_COMMENT_ID,
                'user': {'login': REVIEW_LOGIN},
                'body': body,
                'pr': pr_num,
            }
            posted.append(comment)
            return comment

        with patch.object(review_dispatch.shutil, 'which', return_value='/usr/bin/codex'), \
             patch.object(review_dispatch.subprocess, 'run',
                          return_value=SimpleNamespace(returncode=0, stdout=raw_jsonl, stderr=b'')), \
             patch('llmtool.codex_direct_developer.validate_profile'):
            return task_submission_cli.dispute_arbitrate(
                self.store, TASK, pr=PR, finding_id=FINDING,
                current_head=lambda _r, _p: HEAD,
                github_get=comment_get,
                create_d6=self.fake_create_d6,
                bind_session=self.fake_bind,
                comment_lister=fake_lister,
                comment_poster=fake_poster,
                login_resolver=lambda: REVIEW_LOGIN,
            )

    def test_codex_arbiter_stores_reported_usage_on_completed_claim(self):
        self._seed()
        ruling_text = tdal._ruling_text(
            actor='Codex', marker_status='WITHDRAWN', header_outcome='WITHDRAWN')
        raw_jsonl = _codex_stream({
            'task_id': TASK, 'run_id': RUN, 'head': HEAD,
            'outcome': 'WITHDRAWN', 'ruling': ruling_text,
        }, usage={
            'input_tokens': 140,
            'cached_input_tokens': 40,
            'output_tokens': 30,
            'reasoning_output_tokens': 5,
        })
        res = self._run_codex_arbitrate(raw_jsonl)
        self.assertEqual(res['state'], 'RULED')
        stored = self.store.get(TASK)
        ac = [c for c in stored['review_dispatch_claim'] if c.get('purpose') == 'dispute_ruling'][0]
        self.assertEqual(ac['state'], 'COMPLETED')
        self.assertEqual(ac['usage'], {
            'prompt_tokens': 140,
            'completion_tokens': 30,
            'total_tokens': 170,
            'cached_prompt_tokens': 40,
        })
        self.assertNotIn('usage_unknown', ac)

    def test_codex_arbiter_stores_usage_unknown_when_absent_zero_or_malformed(self):
        ruling_text = tdal._ruling_text(
            actor='Codex', marker_status='UPHELD', header_outcome='UPHELD')
        payload = {
            'task_id': TASK, 'run_id': RUN, 'head': HEAD,
            'outcome': 'UPHELD', 'ruling': ruling_text,
        }
        cases = (
            ('absent', None),
            ('zero', {'input_tokens': 0, 'cached_input_tokens': 0,
                      'output_tokens': 0, 'reasoning_output_tokens': 0}),
            ('malformed', {'input_tokens': 'bad', 'cached_input_tokens': 0,
                           'output_tokens': 10, 'reasoning_output_tokens': 0}),
        )
        for label, usage_payload in cases:
            with self.subTest(case=label):
                self._seed()
                raw_jsonl = _codex_stream(payload, usage=usage_payload)
                res = self._run_codex_arbitrate(raw_jsonl)
                self.assertEqual(res['state'], 'RULED')
                stored = self.store.get(TASK)
                ac = [c for c in stored['review_dispatch_claim']
                      if c.get('purpose') == 'dispute_ruling'][0]
                self.assertEqual(ac['usage_unknown'], {'reason': 'STREAM_USAGE_ABSENT'})
                self.assertNotIn('usage', ac)

    def test_codex_arbiter_withheld_still_stores_usage(self):
        self._seed()
        ruling_text = tdal._ruling_text(
            actor='Codex', marker_status='UPHELD', header_outcome='UPHELD')
        raw_jsonl = _codex_stream({
            'task_id': TASK, 'run_id': RUN, 'head': HEAD,
            'outcome': 'UPHELD', 'ruling': ruling_text,
        }, usage={'input_tokens': 90, 'cached_input_tokens': 10, 'output_tokens': 20})
        with patch.object(task_submission_cli, '_screen_text',
                          side_effect=LLMToolError('WITHHELD')):
            with self.assertRaises(LLMToolError) as caught:
                self._run_codex_arbitrate(raw_jsonl)
        self.assertEqual(caught.exception.dispute_code, 'DISPUTE_RULING_WITHHELD')
        stored = self.store.get(TASK)
        ac = [c for c in stored['review_dispatch_claim'] if c.get('purpose') == 'dispute_ruling'][0]
        self.assertEqual(ac['state'], 'COMPLETED')
        self.assertEqual(ac['review_result']['verdict'], 'BLOCKED')
        self.assertEqual(ac['usage'], {
            'prompt_tokens': 90,
            'completion_tokens': 20,
            'total_tokens': 110,
            'cached_prompt_tokens': 10,
        })
        self.assertNotIn('usage_unknown', ac)


class ClaudeArbiterUsageTests(tdacc.ClaudeRulingCollectionTests):
    def _run_claude_arbitrate(self):
        posted = []
        posted_comment_id = RULING_COMMENT_ID

        def fake_lister(_pr):
            return [dict(c) for c in posted]

        def fake_poster(_root, pr_num, body):
            comment = {
                'id': posted_comment_id,
                'user': {'login': REVIEW_LOGIN},
                'body': body,
                'pr': pr_num,
            }
            posted.append(comment)
            return comment

        def fake_get(path):
            if path == f'issues/comments/{posted_comment_id}':
                return {
                    'id': posted_comment_id,
                    'body': posted[0]['body'],
                    'issue_url': f'https://api.github.com/repos/{REPOSITORY}/issues/{PR}',
                    'created_at': '2026-11-23T00:05:00Z',
                    'updated_at': '2026-11-23T00:05:00Z',
                }
            raise AssertionError(f'unexpected github_get: {path}')

        return task_submission_cli.dispute_arbitrate(
            self.store, TASK, pr=PR, finding_id=FINDING,
            current_head=lambda _r, _p: HEAD,
            github_get=fake_get,
            comment_lister=fake_lister,
            comment_poster=fake_poster,
            login_resolver=lambda: REVIEW_LOGIN,
        )

    def test_claim_usage_helper_supports_claude_arbiter_and_skips_others(self):
        ruling = tdacc._ruling_text(actor='Claude')
        self._write_transcript(tdacc._transcript_bytes([
            _claude_assistant_entry(ruling, usage={
                'input_tokens': 50,
                'cache_creation_input_tokens': 15,
                'cache_read_input_tokens': 25,
                'output_tokens': 10,
            }),
        ]))
        claude_claim = {
            'purpose': 'dispute_ruling',
            'reviewer': 'claude-reviewer',
            'account_mode': 'ROLES',
            'native_session_id': CLAUDE_SESSION,
            'reviewer_task': ARBITER_TASK,
        }
        self.assertEqual(
            review_transcript.claim_usage(claude_claim, config_dir=str(self.claude_home)),
            {'usage': {
                'prompt_tokens': 90,
                'completion_tokens': 10,
                'total_tokens': 100,
                'cached_prompt_tokens': 25,
            }},
        )
        codex_claim = dict(claude_claim, reviewer='codex-reviewer', account_mode='CODEX_CLI')
        self.assertIsNone(
            review_transcript.claim_usage(codex_claim, config_dir=str(self.claude_home)))
        other_purpose = dict(claude_claim, purpose='other_purpose')
        self.assertIsNone(
            review_transcript.claim_usage(other_purpose, config_dir=str(self.claude_home)))

    def test_claude_arbiter_stores_reported_usage_once_and_replay_preserves(self):
        self._seed()
        ruling = tdacc._ruling_text(
            actor='Claude', marker_status='WITHDRAWN', header_outcome='WITHDRAWN')
        raw = tdacc._transcript_bytes([
            _claude_assistant_entry(ruling, usage={
                'input_tokens': 80,
                'cache_creation_input_tokens': 10,
                'cache_read_input_tokens': 20,
                'output_tokens': 15,
            }),
        ])
        self._write_transcript(raw)
        res = self._run_claude_arbitrate()
        self.assertEqual(res['state'], 'RULED')
        stored = self.store.get(TASK)
        ac = [c for c in stored['review_dispatch_claim'] if c.get('purpose') == 'dispute_ruling'][0]
        self.assertEqual(ac['state'], 'COMPLETED')
        self.assertEqual(ac['usage'], {
            'prompt_tokens': 110,
            'completion_tokens': 15,
            'total_tokens': 125,
            'cached_prompt_tokens': 20,
        })
        self.assertNotIn('usage_unknown', ac)

        # Mutating the transcript after completion cannot overwrite recorded usage on replay.
        self._write_transcript(tdacc._transcript_bytes([
            _claude_assistant_entry(ruling, usage={'input_tokens': 999, 'output_tokens': 1}),
        ]))
        after = self._record_bytes()
        replay = self._run_claude_arbitrate()
        self.assertEqual(replay['state'], 'RULED')
        self.assertEqual(self._record_bytes(), after)

    def test_claude_arbiter_stores_usage_unknown_when_absent_zero_or_incomplete_turn(self):
        ruling = tdacc._ruling_text(
            actor='Claude', marker_status='UPHELD', header_outcome='UPHELD')
        cases = (
            ('absent', [_claude_assistant_entry(ruling)]),
            ('zero', [_claude_assistant_entry(ruling, usage={
                'input_tokens': 0, 'cache_creation_input_tokens': 0,
                'cache_read_input_tokens': 0, 'output_tokens': 0,
            })]),
            ('incomplete_turn', [_claude_assistant_entry(
                ruling, stop_reason='tool_use',
                usage={'input_tokens': 10, 'output_tokens': 5})]),
        )
        for label, entries in cases:
            with self.subTest(case=label):
                self._seed()
                self._write_transcript(tdacc._transcript_bytes(entries))
                res = self._run_claude_arbitrate()
                self.assertEqual(res['state'], 'RULED')
                stored = self.store.get(TASK)
                ac = [c for c in stored['review_dispatch_claim']
                      if c.get('purpose') == 'dispute_ruling'][0]
                self.assertEqual(ac['usage_unknown'], {'reason': 'USAGE_ABSENT'})
                self.assertNotIn('usage', ac)

    def test_claude_arbiter_withheld_still_stores_usage(self):
        self._seed()
        ruling = tdacc._ruling_text(actor='Claude')
        self._write_transcript(tdacc._transcript_bytes([
            _claude_assistant_entry(ruling, usage={'input_tokens': 40, 'output_tokens': 10}),
        ]))
        with patch.object(task_submission_cli, '_screen_text',
                          side_effect=LLMToolError('WITHHELD')):
            with self.assertRaises(LLMToolError) as caught:
                self._arbitrate()
        self.assertEqual(caught.exception.dispute_code, 'DISPUTE_RULING_WITHHELD')
        stored = self.store.get(TASK)
        ac = [c for c in stored['review_dispatch_claim'] if c.get('purpose') == 'dispute_ruling'][0]
        self.assertEqual(ac['state'], 'COMPLETED')
        self.assertEqual(ac['review_result']['verdict'], 'BLOCKED')
        self.assertEqual(ac['usage'], {
            'prompt_tokens': 40,
            'completion_tokens': 10,
            'total_tokens': 50,
            'cached_prompt_tokens': 0,
        })
        self.assertNotIn('usage_unknown', ac)


class TaskStoreArbiterUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        worker_policy_fixture.install(self)

    def _seed_claimed(self, sub_name, *, reviewer='codex-reviewer'):
        store = task_store.TaskStore(Path(self.temp.name).resolve() / sub_name)
        base, cd = tdrcr._base_task_record()
        base['review_dispatch_claim'] = [tdrcr._formal_claim(cd, verdict='FAIL', actor='Claude')]
        rec = store.create(base)
        entry = dict(tdrcr._dispute_entry(), evidence_ref=EVIDENCE_REF)
        rec = store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_disputes=[entry])
        claimed = tdrcr._arbiter_claim(cd, state='CLAIMED', version=ARBITER_VERSION, reviewer=reviewer)
        entry_ac = dict(
            entry,
            arbiter_claim={'reviewer_task': claimed['reviewer_task'], 'version': claimed['version']},
        )
        rec = store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_dispatch_claim=rec['review_dispatch_claim'] + [claimed],
            review_disputes=[entry_ac])
        return store, rec, cd

    def _ruling_result(self, cd, *, source_kind='CODEX_JSONL', actor='Codex'):
        text = tdacc._ruling_text(actor=actor, marker_status='UPHELD', header_outcome='UPHELD')
        return {
            'task_id': TASK, 'run_id': RUN, 'contract_digest': cd,
            'head': HEAD, 'verdict': 'RULING', 'outcome': 'UPHELD',
            'review_text': text,
            'source': {'kind': source_kind, 'output_sha256': tdrcr.OUTPUT_DIGEST},
        }

    def test_codex_and_claude_ruling_claim_completion_usage_round_trip(self):
        usage_val = {
            'prompt_tokens': 100, 'completion_tokens': 25,
            'total_tokens': 125, 'cached_prompt_tokens': 10,
        }
        for label, extra in (
            ('usage', {'usage': usage_val}),
            ('usage_unknown', {'usage_unknown': {'reason': 'STREAM_USAGE_ABSENT'}}),
            ('none', {}),
        ):
            with self.subTest(family='codex', case=label):
                store, rec, cd = self._seed_claimed(f'codex-{label}', reviewer='codex-reviewer')
                completed = dict(
                    rec['review_dispatch_claim'][1],
                    state='COMPLETED', native_session_id=THREAD_ID, updated_at=STAMP_3,
                    review_result=self._ruling_result(cd, source_kind='CODEX_JSONL', actor='Codex'),
                    **extra,
                )
                saved = store.compare_and_update(
                    TASK, expected={'record_sha256': rec['record_sha256']},
                    review_dispatch_claim=[rec['review_dispatch_claim'][0], completed])
                self.assertEqual(saved['review_dispatch_claim'][1], completed)

        for label, extra in (
            ('usage', {'usage': usage_val}),
            ('usage_unknown', {'usage_unknown': {'reason': 'USAGE_ABSENT'}}),
            ('none', {}),
        ):
            with self.subTest(family='claude', case=label):
                store, rec, cd = self._seed_claimed(f'claude-{label}', reviewer='claude-reviewer')
                dispatched = dict(
                    rec['review_dispatch_claim'][1],
                    state='DISPATCHED', native_session_id=CLAUDE_SESSION, updated_at=STAMP_2,
                )
                rec = store.compare_and_update(
                    TASK, expected={'record_sha256': rec['record_sha256']},
                    review_dispatch_claim=[rec['review_dispatch_claim'][0], dispatched])
                completed = dict(
                    dispatched,
                    state='COMPLETED', updated_at=STAMP_3,
                    review_result=self._ruling_result(
                        cd, source_kind='CLAUDE_TRANSCRIPT', actor='Claude'),
                    **extra,
                )
                saved = store.compare_and_update(
                    TASK, expected={'record_sha256': rec['record_sha256']},
                    review_dispatch_claim=[rec['review_dispatch_claim'][0], completed])
                self.assertEqual(saved['review_dispatch_claim'][1], completed)

    def test_ruling_claim_usage_transition_and_validation_refusals(self):
        usage_val = {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}
        # Codex rejections
        store, rec, cd = self._seed_claimed('codex-reject', reviewer='codex-reviewer')
        claimed = rec['review_dispatch_claim'][1]
        for bad_claim in (
            dict(claimed, state='COMPLETED', native_session_id=THREAD_ID, updated_at=STAMP_3,
                 review_result=self._ruling_result(cd),
                 usage=usage_val, usage_unknown={'reason': 'STREAM_USAGE_ABSENT'}),
            dict(claimed, state='COMPLETED', native_session_id=THREAD_ID, updated_at=STAMP_3,
                 review_result=self._ruling_result(cd),
                 usage_unknown={'reason': 'OTHER'}),
            dict(claimed, state='UNKNOWN', updated_at=STAMP_3,
                 usage_unknown={'reason': 'STREAM_USAGE_ABSENT'}),
        ):
            with self.assertRaises(LLMToolError):
                store.compare_and_update(
                    TASK, expected={'record_sha256': rec['record_sha256']},
                    review_dispatch_claim=[rec['review_dispatch_claim'][0], bad_claim])

        # Once COMPLETED with usage_unknown, cannot overwrite or add usage.
        completed = dict(
            claimed, state='COMPLETED', native_session_id=THREAD_ID, updated_at=STAMP_3,
            review_result=self._ruling_result(cd),
            usage_unknown={'reason': 'STREAM_USAGE_ABSENT'},
        )
        rec = store.compare_and_update(
            TASK, expected={'record_sha256': rec['record_sha256']},
            review_dispatch_claim=[rec['review_dispatch_claim'][0], completed])
        with self.assertRaises(LLMToolError):
            store.compare_and_update(
                TASK, expected={'record_sha256': rec['record_sha256']},
                review_dispatch_claim=[rec['review_dispatch_claim'][0],
                                       dict(completed, usage=usage_val)])

        # Claude rejections: CLAIMED -> COMPLETED with usage refused; DISPATCHED -> DISPATCHED with usage refused;
        # DISPATCHED -> COMPLETED with both keys refused; COMPLETED -> COMPLETED overwrite refused.
        store2, rec2, cd2 = self._seed_claimed('claude-reject', reviewer='claude-reviewer')
        claimed2 = rec2['review_dispatch_claim'][1]
        with self.assertRaises(LLMToolError):
            store2.compare_and_update(
                TASK, expected={'record_sha256': rec2['record_sha256']},
                review_dispatch_claim=[
                    rec2['review_dispatch_claim'][0],
                    dict(claimed2, state='COMPLETED', native_session_id=CLAUDE_SESSION,
                         updated_at=STAMP_3,
                         review_result=self._ruling_result(
                             cd2, source_kind='CLAUDE_TRANSCRIPT', actor='Claude'),
                         usage=usage_val),
                ])
        dispatched2 = dict(
            claimed2, state='DISPATCHED', native_session_id=CLAUDE_SESSION, updated_at=STAMP_2)
        rec2 = store2.compare_and_update(
            TASK, expected={'record_sha256': rec2['record_sha256']},
            review_dispatch_claim=[rec2['review_dispatch_claim'][0], dispatched2])
        with self.assertRaises(LLMToolError):
            store2.compare_and_update(
                TASK, expected={'record_sha256': rec2['record_sha256']},
                review_dispatch_claim=[rec2['review_dispatch_claim'][0],
                                       dict(dispatched2, usage=usage_val)])
        with self.assertRaises(LLMToolError):
            store2.compare_and_update(
                TASK, expected={'record_sha256': rec2['record_sha256']},
                review_dispatch_claim=[
                    rec2['review_dispatch_claim'][0],
                    dict(dispatched2, state='COMPLETED', updated_at=STAMP_3,
                         review_result=self._ruling_result(
                             cd2, source_kind='CLAUDE_TRANSCRIPT', actor='Claude'),
                         usage=usage_val, usage_unknown={'reason': 'USAGE_ABSENT'}),
                ])
        completed2 = dict(
            dispatched2, state='COMPLETED', updated_at=STAMP_3,
            review_result=self._ruling_result(
                cd2, source_kind='CLAUDE_TRANSCRIPT', actor='Claude'),
            usage=usage_val,
        )
        rec2 = store2.compare_and_update(
            TASK, expected={'record_sha256': rec2['record_sha256']},
            review_dispatch_claim=[rec2['review_dispatch_claim'][0], completed2])
        with self.assertRaises(LLMToolError):
            store2.compare_and_update(
                TASK, expected={'record_sha256': rec2['record_sha256']},
                review_dispatch_claim=[
                    rec2['review_dispatch_claim'][0],
                    dict(completed2, usage={'prompt_tokens': 99, 'completion_tokens': 1,
                                            'total_tokens': 100}),
                ])


if __name__ == '__main__':
    unittest.main()
