import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from llmtool import review_dispatch, task_store, worker_dispatch
from llmtool.developer_routes import load_route_config
from llmtool.errors import LLMToolError
import worker_policy_fixture


TASK = '1' * 32
RUN = '2' * 32
HEAD = 'a' * 40
EXECUTOR = 'executor-d6'
REVIEWER = 'reviewer-' + 'b' * 20
THREAD_ID = '0123456789abcdef'
PR = 42
CODEX_REVIEW_MODEL = load_route_config()['roles']['codex-reviewer']['allowed_models'][0]
_STUB_RULES_PREFIX = 'RULES stub for codex review usage tests\nRULES_END\n\n'
_STUB_RULES_SHA256 = hashlib.sha256(_STUB_RULES_PREFIX.encode('utf-8')).hexdigest()


def _make_store(root):
    contract = {
        'schema_version': 1, 'contract_version': 1, 'task_id': TASK,
        'goal': 'test codex review usage', 'authorized_scope': ['pm-planning'],
        'criteria': [{'criterion_id': 'pm_response', 'description': 'Record result.',
                      'kind': 'ARTIFACT'}], 'forbidden_scope': [], 'previous_digest': None,
    }
    parsed = task_store.parse_task_contract(json.dumps(contract))
    stamp = '2026-11-26T00:00:00Z'
    record = {
        'schema_version': 2, 'task_id': TASK, 'run_id': RUN,
        'pm_session_id': 'pm-test', 'pm_provider_profile': 'test',
        'pm_provider_type': 'test', 'pm_model': None,
        'pm_route_binding': {'type': 'test', 'model': None, 'base_url': None},
        'backend_session_semantics': 'NOT_STARTED', 'native_session_id': None,
        'state': 'PM_PENDING', 'created_at': stamp, 'updated_at': stamp,
        'task_contract': contract, 'task_binding': vars(parsed.binding(RUN)),
        'request': 'test codex review usage',
        'request_sha256': hashlib.sha256(b'test codex review usage').hexdigest(),
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
            'acceptance': ['Record codex review usage.'], 'context_sha256': 'f' * 64,
            'egress_basis': 'Bounded task context', 'input_bytes': 1,
            'max_tokens': None, 'artifact_format': 'unified_diff',
            'artifact': 'diff',
            'artifact_sha256': hashlib.sha256(b'diff').hexdigest(),
            'usage': None, 'error_code': None, 'created_at': stamp,
        },
        'pm_assignment': None, 'completion': None,
    }
    store = task_store.TaskStore(Path(root).resolve())
    store.create(record)
    return store


def _review_text(contract_digest):
    return (
        '<!-- activity: type=review_verdict actor=Codex target=PM status=PASS -->\n'
        f'Verdict: PASS | Version: v1 | HEAD: {HEAD}\n'
        'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n'
        f'LLMTOOL-REVIEW-BINDING v1 task={TASK} run={RUN} contract={contract_digest} head={HEAD}'
    )


def _codex_stream(contract_digest, *, usage=None):
    verdict = {
        'task_id': TASK, 'run_id': RUN, 'head': HEAD,
        'verdict': 'PASS', 'review': _review_text(contract_digest),
    }
    events = [
        {'type': 'thread.started', 'thread_id': THREAD_ID},
        {'type': 'item.completed', 'item': {
            'type': 'agent_message', 'text': json.dumps(verdict)}},
    ]
    if usage is not None:
        events.append({'type': 'turn.completed', 'usage': usage})
    return '\n'.join(json.dumps(event) for event in events).encode('utf-8')


def _install_worker_policy(testcase):
    worker_policy_fixture.install(testcase)
    repository = patch.object(
        worker_dispatch,
        'repository_config',
        return_value={
            'slug': 'davidcatcat/davidcattoolsproject',
            'dispatch_issue': 289,
            'dispatch_evidence_pr': 283,
        },
    )
    repository.start()
    testcase.addCleanup(repository.stop)


class CodexReviewUsageDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        _install_worker_policy(self)
        self.store = _make_store(Path(self.temp.name) / 'store')
        self.post_dir = Path(self.temp.name).resolve() / 'posts'
        self.executor = SimpleNamespace(root='/repo/executor', common_dir='/repo/.git')
        self.reviewer = SimpleNamespace(root='/repo/reviewer')

    def _dispatch(self, stdout_bytes):
        preflight = (TASK, self.executor, HEAD, 'd' * 40, 'codex-reviewer')
        proc_result = SimpleNamespace(returncode=0, stdout=stdout_bytes, stderr=b'')
        role = {
            'provider': 'codex', 'config_dir': None,
            'model': CODEX_REVIEW_MODEL, 'effort': 'high', 'source': 'roles',
        }
        with patch.object(review_dispatch, '_preflight', side_effect=[preflight, preflight]), \
                patch.object(review_dispatch.worker_dispatch, 'evaluate',
                             return_value=SimpleNamespace(allowed=True, reason_code='')), \
                patch.object(review_dispatch, '_resolve_github_login',
                             return_value='reviewer-user'), \
                patch.object(review_dispatch, '_review_rules',
                             return_value=(_STUB_RULES_PREFIX, _STUB_RULES_SHA256)), \
                patch.object(review_dispatch.role_config, 'resolve_role', return_value=role), \
                patch.object(review_dispatch.task_completion, 'read_d6_registration',
                             return_value=self.reviewer), \
                patch.object(review_dispatch, '_verdict_post_dir',
                             lambda _store: review_dispatch._private_post_dir(self.post_dir)), \
                patch.object(review_dispatch.shutil, 'which', return_value='/bin/codex'), \
                patch.object(review_dispatch.subprocess, 'run', return_value=proc_result):
            return review_dispatch.dispatch_review(
                store=self.store, task_id=TASK, executor_task=EXECUTOR,
                pr_number=PR, version=1, create_d6=lambda **_: None,
                bind_session=lambda **_: None, prompt_renderer=lambda *_: 'prompt')

    def test_codex_stream_with_usage_stores_usage_on_completed_claim(self):
        digest = self.store.get(TASK)['task_binding']['contract_digest']
        stdout = _codex_stream(digest, usage={
            'input_tokens': 120,
            'cached_input_tokens': 30,
            'output_tokens': 25,
            'reasoning_output_tokens': 5,
        })
        result = self._dispatch(stdout)
        self.assertEqual(result['state'], 'COMPLETED')
        self.assertEqual(result['verdict'], 'PASS')
        claim = self.store.get(TASK)['review_dispatch_claim'][0]
        self.assertEqual(claim['state'], 'COMPLETED')
        self.assertEqual(claim['usage'], {
            'prompt_tokens': 120,
            'completion_tokens': 25,
            'total_tokens': 145,
            'cached_prompt_tokens': 30,
        })
        self.assertNotIn('usage_unknown', claim)

    def test_codex_stream_without_or_zero_usage_stores_usage_unknown(self):
        digest = self.store.get(TASK)['task_binding']['contract_digest']
        for name, usage_payload in (
                ('absent', None),
                ('zero', {'input_tokens': 0, 'cached_input_tokens': 0,
                          'output_tokens': 0, 'reasoning_output_tokens': 0}),
                ('malformed', {'input_tokens': 'bad', 'cached_input_tokens': 0,
                               'output_tokens': 10, 'reasoning_output_tokens': 0})):
            with self.subTest(case=name), tempfile.TemporaryDirectory() as sub_dir:
                self.store = _make_store(Path(sub_dir) / 'store')
                self.post_dir = Path(sub_dir).resolve() / 'posts'
                stdout = _codex_stream(digest, usage=usage_payload)
                result = self._dispatch(stdout)
                self.assertEqual(result['state'], 'COMPLETED')
                claim = self.store.get(TASK)['review_dispatch_claim'][0]
                self.assertEqual(claim['usage_unknown'], {'reason': 'STREAM_USAGE_ABSENT'})
                self.assertNotIn('usage', claim)


class TaskStoreCodexReviewUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        _install_worker_policy(self)

    def _claimed_store(self, sub_name):
        store = _make_store(Path(self.temp.name) / sub_name)
        initial = store.get(TASK)
        stamp = '2026-11-26T00:00:00Z'
        claimed = {
            'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': 1,
            'state': 'CLAIMED', 'reviewer_task': REVIEWER,
            'native_session_id': None, 'account_mode': 'CODEX_CLI',
            'claimed_at': stamp, 'updated_at': stamp,
        }
        store.compare_and_update(
            TASK, expected={'record_sha256': initial['record_sha256']},
            review_dispatch_claim=[claimed])
        return store

    def _completed_claim(self, store, claimed, **extra):
        task = store.get(TASK)
        digest = task['task_binding']['contract_digest']
        completed = dict(claimed, state='COMPLETED', native_session_id=THREAD_ID,
                         review_result={
                             'task_id': TASK, 'run_id': RUN,
                             'contract_digest': digest, 'head': HEAD,
                             'verdict': 'PASS', 'review_text': _review_text(digest),
                         })
        completed.update(extra)
        return completed

    def test_completed_codex_claim_round_trip_with_each_key_and_without_keys(self):
        usage_value = {
            'prompt_tokens': 200, 'completion_tokens': 40,
            'total_tokens': 240, 'cached_prompt_tokens': 50,
        }
        for name, extra in (
                ('with_usage', {'usage': usage_value}),
                ('with_usage_unknown', {'usage_unknown': {'reason': 'STREAM_USAGE_ABSENT'}}),
                ('without_usage_keys', {})):
            with self.subTest(case=name):
                store = self._claimed_store(name)
                claimed = store.get(TASK)['review_dispatch_claim'][0]
                completed = self._completed_claim(store, claimed, **extra)
                store.compare_and_update(
                    TASK, expected={'review_dispatch_claim': [claimed]},
                    review_dispatch_claim=[completed])
                loaded = store.get(TASK)['review_dispatch_claim'][0]
                self.assertEqual(loaded, completed)

    def test_rejects_both_keys_non_completed_usage_and_overwrite(self):
        store = self._claimed_store('rejections')
        claimed = store.get(TASK)['review_dispatch_claim'][0]
        usage_value = {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}
        with self.assertRaises(LLMToolError):
            store.compare_and_update(
                TASK, expected={'review_dispatch_claim': [claimed]},
                review_dispatch_claim=[self._completed_claim(
                    store, claimed, usage=usage_value,
                    usage_unknown={'reason': 'STREAM_USAGE_ABSENT'})])
        with self.assertRaises(LLMToolError):
            store.compare_and_update(
                TASK, expected={'review_dispatch_claim': [claimed]},
                review_dispatch_claim=[dict(claimed, state='DISPATCHED',
                                            native_session_id=THREAD_ID,
                                            usage=usage_value)])
        with self.assertRaises(LLMToolError):
            store.compare_and_update(
                TASK, expected={'review_dispatch_claim': [claimed]},
                review_dispatch_claim=[dict(claimed, state='UNKNOWN',
                                            usage_unknown={'reason': 'STREAM_USAGE_ABSENT'})])
        completed = self._completed_claim(
            store, claimed, usage_unknown={'reason': 'STREAM_USAGE_ABSENT'})
        store.compare_and_update(
            TASK, expected={'review_dispatch_claim': [claimed]},
            review_dispatch_claim=[completed])
        stored = store.get(TASK)['review_dispatch_claim']
        with self.assertRaises(LLMToolError):
            store.compare_and_update(
                TASK, expected={'review_dispatch_claim': stored},
                review_dispatch_claim=[dict(stored[0], usage=usage_value)])
