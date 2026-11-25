import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from llmtool import review_result, review_transcript, task_store
from llmtool.errors import LLMToolError


TASK, RUN, HEAD = '1' * 32, '2' * 32, 'a' * 40
EXECUTOR = 'executor-d6'
REVIEWER = 'reviewer-' + 'b' * 20
SESSION = '01234567-89ab-cdef-0123-456789abcdef'
PR = 42


def _make_store(root, *, claim_state='DISPATCHED', account_mode='DEFAULT_SECOND_ACCOUNT',
                native_session_id=SESSION, extra_claim=None):
    contract = {
        'schema_version': 1, 'contract_version': 1, 'task_id': TASK,
        'goal': 'test review usage', 'authorized_scope': ['pm-planning'],
        'criteria': [{'criterion_id': 'pm_response', 'description': 'Record result.',
                      'kind': 'ARTIFACT'}], 'forbidden_scope': [], 'previous_digest': None,
    }
    parsed = task_store.parse_task_contract(json.dumps(contract))
    stamp = '2026-11-26T00:00:00Z'
    claim = {
        'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': 1,
        'state': claim_state, 'reviewer_task': REVIEWER,
        'native_session_id': native_session_id,
        'account_mode': account_mode,
        'claimed_at': stamp, 'updated_at': stamp,
    }
    if extra_claim:
        claim.update(extra_claim)
    record = {
        'schema_version': 2, 'task_id': TASK, 'run_id': RUN,
        'pm_session_id': 'pm-test', 'pm_provider_profile': 'test',
        'pm_provider_type': 'test', 'pm_model': None,
        'pm_route_binding': {'type': 'test', 'model': None, 'base_url': None},
        'backend_session_semantics': 'NOT_STARTED', 'native_session_id': None,
        'state': 'PM_PENDING', 'created_at': stamp, 'updated_at': stamp,
        'task_contract': contract, 'task_binding': vars(parsed.binding(RUN)),
        'request': 'test review usage',
        'request_sha256': hashlib.sha256(b'test review usage').hexdigest(),
        'response': None, 'usage': None, 'error_code': None,
        'developer': {
            'assignment_id': 'e' * 32, 'selection_source': 'PM',
            'state': 'DEVELOPER_RESPONDED',
            'provider_profile': 'codex', 'provider_type': 'codex-cli',
            'model': 'gpt-6-luna',
            'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
            'session_id': 'dev-session', 'source_sha': HEAD,
            'source_sha_binding': 'D6_REGISTRY_OBSERVED',
            'read_paths': ['tools/llmtool'], 'write_paths': ['tools/llmtool'],
            'acceptance': ['Record review usage.'], 'context_sha256': 'f' * 64,
            'egress_basis': 'Bounded task context', 'input_bytes': 1,
            'max_tokens': None, 'artifact_format': 'unified_diff',
            'artifact': 'diff',
            'artifact_sha256': hashlib.sha256(b'diff').hexdigest(),
            'usage': None, 'error_code': None, 'created_at': stamp,
        },
        'pm_assignment': None, 'completion': None,
        'review_dispatch_claim': [claim],
    }
    store = task_store.TaskStore(Path(root).resolve())
    store.create(record)
    return store


def _write_transcript(config_dir, entries, *, session_id=SESSION, reviewer_task=REVIEWER):
    project_dir = os.path.join(config_dir, 'projects', '-tasks-' + reviewer_task)
    os.makedirs(project_dir, exist_ok=True)
    path = os.path.join(project_dir, session_id + '.jsonl')
    with open(path, 'w', encoding='utf-8') as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + '\n')
    return path


def _assistant_entry(msg_id, *, usage=None, stop_reason='end_turn', content=None):
    message = {'id': msg_id, 'stop_reason': stop_reason,
               'content': content or [{'type': 'text', 'text': 'Reviewed.'}]}
    if usage is not None:
        message['usage'] = usage
    return {'type': 'assistant', 'message': message}


def _pass_comment(digest):
    body = (
        '<!-- activity: type=review_verdict actor=Claude target=PM status=PASS -->\n'
        f'Verdict: PASS | Version: v1 | HEAD: {HEAD}\n'
        f'LLMTOOL-REVIEW-BINDING v1 task={TASK} run={RUN} contract={digest} head={HEAD}\n'
        'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n'
    )
    return {'id': 7, 'body': body, 'created_at': '2026-11-26T01:00:00Z',
            'updated_at': '2026-11-26T01:00:00Z', 'user': {'login': 'shared-agent'}}


class TranscriptUsageTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home)

    def test_duplicate_message_ids_counted_once_and_entry_without_usage_ignored(self):
        entries = [
            _assistant_entry('msg_1', stop_reason=None, content=[{'type': 'thinking', 'thinking': '...'}],
                             usage={'input_tokens': 100, 'cache_creation_input_tokens': 200,
                                    'cache_read_input_tokens': 300, 'output_tokens': 10}),
            # Streamed duplicate with same message.id must not be counted twice.
            _assistant_entry('msg_1', stop_reason='tool_use',
                             content=[{'type': 'tool_use', 'id': 't1', 'name': 'Read'}],
                             usage={'input_tokens': 100, 'cache_creation_input_tokens': 200,
                                    'cache_read_input_tokens': 300, 'output_tokens': 10}),
            {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't1', 'content': 'ok'}]}},
            # Assistant entry without usage is skipped.
            _assistant_entry('msg_no_usage', stop_reason=None),
            _assistant_entry('msg_2', stop_reason='end_turn',
                             usage={'input_tokens': 50, 'cache_creation_input_tokens': 0,
                                    'cache_read_input_tokens': 400, 'output_tokens': 40}),
        ]
        _write_transcript(self.home, entries)
        usage, reason = review_transcript.transcript_usage(SESSION, self.home, REVIEWER)
        self.assertIsNone(reason)
        self.assertEqual(usage, {
            'prompt_tokens': 1050,
            'completion_tokens': 50,
            'total_tokens': 1100,
            'cached_prompt_tokens': 700,
        })

    def test_missing_symlink_and_unreadable_transcript_return_unavailable(self):
        self.assertEqual(review_transcript.transcript_usage(SESSION, self.home, REVIEWER),
                         (None, 'TRANSCRIPT_UNAVAILABLE'))
        target = os.path.join(self.home, 'other.jsonl')
        with open(target, 'w', encoding='utf-8') as handle:
            handle.write(json.dumps(_assistant_entry('msg_1', usage={'input_tokens': 10, 'output_tokens': 5})) + '\n')
        project_dir = os.path.join(self.home, 'projects', '-tasks-' + REVIEWER)
        os.makedirs(project_dir, exist_ok=True)
        link_path = os.path.join(project_dir, SESSION + '.jsonl')
        os.symlink(target, link_path)
        self.assertEqual(review_transcript.transcript_usage(SESSION, self.home, REVIEWER),
                         (None, 'TRANSCRIPT_UNAVAILABLE'))
        os.unlink(link_path)
        _write_transcript(self.home, [_assistant_entry('msg_1', usage={'input_tokens': 10, 'output_tokens': 5})])
        with mock.patch('builtins.open', side_effect=OSError('unreadable')):
            self.assertEqual(review_transcript.transcript_usage(SESSION, self.home, REVIEWER),
                             (None, 'TRANSCRIPT_UNAVAILABLE'))

    def test_readable_transcript_without_usage_or_zero_usage_returns_usage_absent(self):
        _write_transcript(self.home, [_assistant_entry('msg_1')])
        self.assertEqual(review_transcript.transcript_usage(SESSION, self.home, REVIEWER),
                         (None, 'USAGE_ABSENT'))
        _write_transcript(self.home, [_assistant_entry('msg_2', usage={
            'input_tokens': 0, 'cache_creation_input_tokens': 0,
            'cache_read_input_tokens': 0, 'output_tokens': 0})])
        self.assertEqual(review_transcript.transcript_usage(SESSION, self.home, REVIEWER),
                         (None, 'USAGE_ABSENT'))


class TaskStoreReviewUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_older_claim_without_usage_keys_and_round_trip_of_both_keys(self):
        store = _make_store(self.temp.name)
        loaded = store.get(TASK)
        claim = loaded['review_dispatch_claim'][0]
        self.assertNotIn('usage', claim)
        self.assertNotIn('usage_unknown', claim)

        usage_value = {'prompt_tokens': 120, 'completion_tokens': 30,
                       'total_tokens': 150, 'cached_prompt_tokens': 20}
        store.compare_and_update(
            TASK, expected={'review_dispatch_claim': loaded['review_dispatch_claim']},
            review_dispatch_claim=[dict(claim, usage=usage_value)])
        self.assertEqual(store.get(TASK)['review_dispatch_claim'][0]['usage'], usage_value)

        with tempfile.TemporaryDirectory() as other_dir:
            other_store = _make_store(other_dir)
            other_claim = other_store.get(TASK)['review_dispatch_claim'][0]
            other_store.compare_and_update(
                TASK, expected={'review_dispatch_claim': [other_claim]},
                review_dispatch_claim=[dict(other_claim, usage_unknown={'reason': 'USAGE_ABSENT'})])
            self.assertEqual(
                other_store.get(TASK)['review_dispatch_claim'][0]['usage_unknown'],
                {'reason': 'USAGE_ABSENT'})

    def test_rejects_both_keys_invalid_reason_and_overwrite(self):
        store = _make_store(self.temp.name)
        claim = store.get(TASK)['review_dispatch_claim'][0]
        with self.assertRaises(LLMToolError):
            store.compare_and_update(
                TASK, expected={'review_dispatch_claim': [claim]},
                review_dispatch_claim=[dict(
                    claim,
                    usage={'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15},
                    usage_unknown={'reason': 'USAGE_ABSENT'})])
        with self.assertRaises(LLMToolError):
            store.compare_and_update(
                TASK, expected={'review_dispatch_claim': [claim]},
                review_dispatch_claim=[dict(claim, usage_unknown={'reason': 'OTHER'})])
        store.compare_and_update(
            TASK, expected={'review_dispatch_claim': [claim]},
            review_dispatch_claim=[dict(claim, usage_unknown={'reason': 'TRANSCRIPT_UNAVAILABLE'})])
        stored = store.get(TASK)['review_dispatch_claim']
        with self.assertRaises(LLMToolError):
            store.compare_and_update(
                TASK, expected={'review_dispatch_claim': stored},
                review_dispatch_claim=[dict(stored[0], usage_unknown={'reason': 'USAGE_ABSENT'})])


class RecordReviewResultUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = os.path.join(self.temp.name, 'claude-home')
        os.makedirs(self.home)
        self.store_dir = os.path.join(self.temp.name, 'store')
        os.makedirs(self.store_dir)

    def test_records_usage_once_and_never_overwrites(self):
        store = _make_store(self.store_dir)
        digest = store.get(TASK)['task_binding']['contract_digest']
        _write_transcript(self.home, [
            _assistant_entry('msg_1', usage={'input_tokens': 80, 'cache_creation_input_tokens': 10,
                                             'cache_read_input_tokens': 20, 'output_tokens': 15}),
        ])
        result = review_result.record_review_result(
            store=store, task_id=TASK, executor_task=EXECUTOR, pr_number=PR,
            github_get=lambda _path: [_pass_comment(digest)], config_dir=self.home)
        self.assertEqual(result['state'], 'REVIEW_PASS')
        claim = store.get(TASK)['review_dispatch_claim'][0]
        self.assertEqual(claim['usage'], {
            'prompt_tokens': 110, 'completion_tokens': 15,
            'total_tokens': 125, 'cached_prompt_tokens': 20,
        })
        self.assertNotIn('usage_unknown', claim)

        _write_transcript(self.home, [
            _assistant_entry('msg_1', usage={'input_tokens': 999, 'output_tokens': 1}),
        ])
        review_result.record_review_result(
            store=store, task_id=TASK, executor_task=EXECUTOR, pr_number=PR,
            github_get=lambda _path: [_pass_comment(digest)], config_dir=self.home)
        self.assertEqual(store.get(TASK)['review_dispatch_claim'][0]['usage']['total_tokens'], 125)

    def test_records_unavailable_absent_and_skips_incomplete_and_codex(self):
        store = _make_store(self.store_dir)
        digest = store.get(TASK)['task_binding']['contract_digest']
        review_result.record_review_result(
            store=store, task_id=TASK, executor_task=EXECUTOR, pr_number=PR,
            github_get=lambda _path: [_pass_comment(digest)], config_dir=self.home)
        self.assertEqual(store.get(TASK)['review_dispatch_claim'][0]['usage_unknown'],
                         {'reason': 'TRANSCRIPT_UNAVAILABLE'})

        with tempfile.TemporaryDirectory() as absent_dir:
            s2 = _make_store(absent_dir)
            d2 = s2.get(TASK)['task_binding']['contract_digest']
            _write_transcript(self.home, [_assistant_entry('msg_1')])
            review_result.record_review_result(
                store=s2, task_id=TASK, executor_task=EXECUTOR, pr_number=PR,
                github_get=lambda _path: [_pass_comment(d2)], config_dir=self.home)
            self.assertEqual(s2.get(TASK)['review_dispatch_claim'][0]['usage_unknown'],
                             {'reason': 'USAGE_ABSENT'})

        with tempfile.TemporaryDirectory() as inc_dir:
            s3 = _make_store(inc_dir)
            d3 = s3.get(TASK)['task_binding']['contract_digest']
            _write_transcript(self.home, [
                _assistant_entry('msg_1', stop_reason='tool_use',
                                 content=[{'type': 'tool_use', 'id': 't1', 'name': 'Read'}],
                                 usage={'input_tokens': 10, 'output_tokens': 5}),
            ])
            review_result.record_review_result(
                store=s3, task_id=TASK, executor_task=EXECUTOR, pr_number=PR,
                github_get=lambda _path: [_pass_comment(d3)], config_dir=self.home)
            c3 = s3.get(TASK)['review_dispatch_claim'][0]
            self.assertNotIn('usage', c3)
            self.assertNotIn('usage_unknown', c3)

        with tempfile.TemporaryDirectory() as codex_dir:
            s4 = _make_store(codex_dir, account_mode='CODEX_CLI')
            d4 = s4.get(TASK)['task_binding']['contract_digest']
            _write_transcript(self.home, [
                _assistant_entry('msg_1', usage={'input_tokens': 10, 'output_tokens': 5}),
            ])
            review_result.record_review_result(
                store=s4, task_id=TASK, executor_task=EXECUTOR, pr_number=PR,
                github_get=lambda _path: [_pass_comment(d4)], config_dir=self.home)
            c4 = s4.get(TASK)['review_dispatch_claim'][0]
            self.assertNotIn('usage', c4)
            self.assertNotIn('usage_unknown', c4)
