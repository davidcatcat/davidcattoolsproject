import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from argparse import Namespace
from contextlib import nullcontext, redirect_stdout
from io import StringIO
from unittest.mock import Mock, patch

from llmtool import (cli, repair_policy, review_repair, run_budget, task_store,
                     task_submission_cli)
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore

TASK, RUN, HEAD, CONTRACT = 'a' * 32, 'b' * 32, 'c' * 40, 'd' * 64
EXECUTOR, PR, EVENT_ID = 'executor-d6', 42, 'e' * 32


def _comment(status='FAIL', *, comment_id=9, actor='Claude', head=HEAD, version=1):
    marker = 'FIX' if status == 'FAIL' else status
    body = (f'<!-- activity: type=review_verdict actor={actor} target=PM status={marker} -->\n'
            f'Verdict: {status} | Version: v{version} | HEAD: {head}\n'
            'Finding: address the reported issue\n')
    return {'id': comment_id, 'body': body, 'created_at': '2026-10-27T01:00:00Z',
            'updated_at': '2026-10-27T01:00:00Z'}


import review_trust_fixture
import worker_policy_fixture


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


class TempTaskStore:
    """Small persistent TaskStore fake preserving compare-and-update semantics."""

    def __init__(self, root, record):
        self.root = Path(root)
        self.path = self.root / 'task.json'
        records = record if isinstance(record, (list, tuple)) else [record]
        for index, item in enumerate(records):
            self._write_record(item, is_primary=(index == 0))

    def _write_record(self, item, is_primary=False):
        if is_primary:
            (self.root / 'task.json').write_text(
                item if isinstance(item, str) else json.dumps(item))
        elif isinstance(item, dict) and 'task_id' in item:
            (self.root / f"{item['task_id']}.json").write_text(json.dumps(item))
        elif isinstance(item, tuple) and len(item) == 2:
            task_id, content = item
            text = content if isinstance(content, str) else json.dumps(content)
            (self.root / f"{task_id}.json").write_text(text)
        elif isinstance(item, dict):
            (self.root / 'task.json').write_text(json.dumps(item))

    def put(self, item):
        self._write_record(item, is_primary=(isinstance(item, dict) and item.get('task_id') == TASK))
        return item

    def task_ids(self):
        ids = []
        if (self.root / 'task.json').exists():
            try:
                data = json.loads((self.root / 'task.json').read_text())
                if isinstance(data, dict) and 'task_id' in data:
                    ids.append(data['task_id'])
                else:
                    ids.append('task')
            except Exception:
                ids.append('task')
        for p in sorted(self.root.glob('*.json')):
            if p.stem != 'task' and p.stem not in ids:
                ids.append(p.stem)
        return ids

    def get(self, task_id):
        path = self.root / f'{task_id}.json'
        if path.exists():
            return json.loads(path.read_text())
        fallback = self.root / 'task.json'
        if fallback.exists():
            data = json.loads(fallback.read_text())
            if isinstance(data, dict):
                if data.get('task_id') == task_id or task_id == 'task':
                    return data
            elif task_id == 'task':
                return data
        raise KeyError(task_id)

    def compare_and_update(self, task_id, *, expected, **changes):
        record = self.get(task_id)
        if any(record.get(key) != value for key, value in expected.items()):
            raise RuntimeError('conflict')
        record.update(changes)
        record['record_sha256'] = hashlib.sha256(json.dumps(
            {key: value for key, value in record.items() if key != 'record_sha256'},
            sort_keys=True).encode()).hexdigest()
        path = self.root / f'{task_id}.json'
        if not path.exists() and (self.root / 'task.json').exists():
            path = self.root / 'task.json'
        path.write_text(json.dumps(record))
        return record


def _record():
    claim = {'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': 1,
             'state': 'DISPATCHED', 'reviewer_task': 'reviewer-d6',
             'native_session_id': 'review-session', 'account_mode': 'DEFAULT_SECOND_ACCOUNT',
             'claimed_at': '2026-10-27T00:00:00Z', 'updated_at': '2026-10-27T00:00:00Z'}
    event = {'event_id': EVENT_ID, 'source': 'REVIEWER', 'state': 'PENDING',
             'source_sha': HEAD, 'session_id': 'review-' + EVENT_ID,
             'received_at': '2026-10-27T01:01:00Z'}
    record = {
        'task_id': TASK, 'run_id': RUN,
        'developer': {'assignment_id': 'f' * 32, 'provider_profile': 'agy',
                      'model': 'gemini-test', 'owner_assignment_ref': None,
                      'selection_source': 'PM', 'read_paths': ['tools/llmtool/llmtool'],
                      'write_paths': ['tools/llmtool/llmtool/review_repair.py'],
                      'acceptance': ['Repair the reported issue.'],
                      'egress_basis': 'Task authorized source files only.',
                      'state': 'DEVELOPER_RESPONDED'},
        'completion': {'executor_task_id': EXECUTOR, 'evidence': {'common_dir': '/tmp/git'}},
        'pm_assignment': {'route': 'agy', 'risk': 'LOW', 'goal': 'Repair the issue.',
                          'acceptance': ['Repair the reported issue.']},
        'pm_provenance': {'pm_native_session_id': '12345678-1234-1234-1234-123456789abc',
                          'pm_cwd': '/repo'},
        'review_dispatch_claim': [claim], 'completion_events': [event],
        'record_sha256': '0' * 64,
    }
    record['record_sha256'] = hashlib.sha256(json.dumps(
        {key: value for key, value in record.items() if key != 'record_sha256'},
        sort_keys=True).encode()).hexdigest()
    return record


def _receipt(head=HEAD):
    receipt = {
        'task_id': TASK, 'run_id': RUN, 'executor_task': EXECUTOR,
        'source_sha': head, 'result': 'FAIL', 'modules': ['test_remote'],
        'failure_excerpt': 'FAIL: test_remote\nAssertionError',
    }
    receipt['receipt_digest'] = hashlib.sha256(
        task_store._json_bytes(receipt)).hexdigest()
    return receipt


MAX = run_budget.MAX_REPAIR_ATTEMPTS
EC2_FAMILY = f'pr-{PR}-ec2'
# Marks a field that a legacy record does not carry at all.
_MISSING = object()


class ReviewRepairTests(unittest.TestCase):
    def test_repair_limit_uses_run_budget_limit(self):
        self.assertEqual(review_repair.MAX_REPAIR_ATTEMPTS,
                         run_budget.MAX_REPAIR_ATTEMPTS)

    def test_agy_uses_the_owner_gated_repair_route(self):
        self.assertEqual(review_repair._worker_key('agy'), 'agy')
        captured = []
        task = {'task_id': TASK, 'pm_assignment': {'acceptance': ['Fix it.']}}
        developer = {'provider_profile': 'agy', 'owner_assignment_ref':
                     'https://github.com/example/repo/issues/1#issuecomment-2',
                     'read_paths': ['src'], 'write_paths': ['src/file.py']}
        with patch.object(review_repair, '_registered_d6', return_value=(
                type('Registration', (), {'root': '/registered/d6'})(), object())):
            result = review_repair._delegate_repair(
                task=task, developer=developer, packet={'verdict_comment_id': 9,
                    'task': TASK,
                    'failed_head': HEAD, 'version': 1, 'findings_count': 1,
                    'reviewer_text_excerpt_untrusted_data': 'Fix the reported issue.'},
                executor_task=EXECUTOR,
                delegate=lambda args, store: captured.append(args) or 0,
                store=type('S', (), {'get': lambda _self, _task_id: _record()})())
        self.assertEqual(result, 0)
        self.assertEqual(captured[0].provider, 'agy')
        self.assertEqual(captured[0].mode, 'direct')
        self.assertEqual(captured[0].owner_ref, developer['owner_assignment_ref'])
        self.assertEqual(captured[0].cwd, '/registered/d6')

    def test_gw_agent_repair_delegate_args(self):
        self.assertEqual(review_repair._worker_key('gw-agent'), 'gw-agent')
        for data_class in ('SYNTHETIC', 'PUBLIC_REPO'):
            with self.subTest(data_class=data_class):
                captured = []
                task = {'task_id': TASK, 'pm_assignment': {'acceptance': ['Fix it.']}}
                developer = {'provider_profile': 'gw-agent',
                             'data_class': data_class,
                             'read_paths': ['tools/llmtool/llmtool'],
                             'write_paths': ['tools/llmtool/llmtool/review_repair.py'],
                             'acceptance': ['Fix it.'],
                             'egress_basis': 'Task authorized source files only.'}
                with patch.object(review_repair, '_registered_d6', return_value=(
                        type('Registration', (), {'root': '/registered/d6'})(), object())):
                    result = review_repair._delegate_repair(
                        task=task, developer=developer, packet={'verdict_comment_id': 9,
                            'task': TASK,
                            'failed_head': HEAD, 'version': 1, 'findings_count': 1,
                            'reviewer_text_excerpt_untrusted_data': 'Fix the reported issue.'},
                        executor_task=EXECUTOR,
                        delegate=lambda args, store: captured.append(args) or 0,
                        store=type('S', (), {'get': lambda _self, _task_id: _record()})())
                self.assertEqual(result, 0)
                args = captured[0]
                self.assertEqual(args.provider, 'gw-agent')
                self.assertEqual(args.mode, 'direct')
                self.assertEqual(args.read_path, ['tools/llmtool/llmtool'])
                self.assertEqual(args.write_path, ['tools/llmtool/llmtool/review_repair.py'])
                self.assertEqual(args.acceptance, ['Fix it.'])
                self.assertEqual(args.egress_basis, 'Task authorized source files only.')
                self.assertEqual(args.data_class, data_class)
                self.assertEqual(args.cwd, '/registered/d6')

    def test_gw_agent_repair_refuses_missing_or_unknown_data_class(self):
        for bad_class in (None, '', 'UNKNOWN', 'INVALID'):
            with self.subTest(data_class=bad_class):
                task = {'task_id': TASK, 'pm_assignment': {'acceptance': ['Fix it.']}}
                developer = {'provider_profile': 'gw-agent',
                             'read_paths': ['tools/llmtool/llmtool'],
                             'write_paths': ['tools/llmtool/llmtool/review_repair.py'],
                             'acceptance': ['Fix it.']}
                if bad_class is not None:
                    developer['data_class'] = bad_class
                with self.assertRaises(LLMToolError) as caught:
                    review_repair._delegate_repair(
                        task=task, developer=developer, packet={'verdict_comment_id': 9,
                            'task': TASK,
                            'failed_head': HEAD, 'version': 1, 'findings_count': 1,
                            'reviewer_text_excerpt_untrusted_data': 'Fix finding.'},
                        executor_task=EXECUTOR,
                        delegate=lambda args, store: 0,
                        store=type('S', (), {'get': lambda _self, _task_id: _record()})())
                self.assertEqual(caught.exception.review_repair_code,
                                 'REPAIR_DATA_CLASS_INVALID')

    def test_gw_agent_repair_uses_the_data_class_stored_at_delegate_time(self):
        # #1830: the delegate stores data_class on the developer record; a repair
        # reuses it. A legacy record written before the field is still refused.
        stored = {
            'assignment_id': 'f' * 32, 'selection_source': 'PM',
            'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'gw-agent',
            'provider_type': 'gw-agent-cli', 'model': 'fake-model',
            'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
            'session_id': 'dev-' + 'f' * 32, 'source_sha': HEAD,
            'source_sha_binding': 'D6_REGISTRY_OBSERVED',
            'read_paths': ['tools/llmtool/llmtool'],
            'write_paths': ['tools/llmtool/llmtool/review_repair.py'],
            'acceptance': ['Fix it.'], 'context_sha256': '0' * 64,
            'egress_basis': 'Task authorized source files only.', 'input_bytes': 10,
            'max_tokens': None, 'artifact_format': 'unified_diff', 'artifact': None,
            'artifact_sha256': None, 'usage': None, 'error_code': None,
            'execution': None, 'created_at': '2026-11-14T00:00:00Z',
            'timeout_seconds': 600, 'direct_result_callback': None,
            'data_class': 'PUBLIC_REPO',
        }
        legacy = {key: value for key, value in stored.items() if key != 'data_class'}
        # Both key sets are ones the strict store accepts.
        for record in (stored, legacy):
            self.assertIn(frozenset(record) - task_store._DEVELOPER_DATA_CLASS_FIELDS,
                          task_store._DEVELOPER_RECORD_SHAPES)
        task = {'task_id': TASK, 'pm_assignment': {'acceptance': ['Fix it.']}}
        packet = {'verdict_comment_id': 9, 'task': TASK, 'failed_head': HEAD,
                  'version': 1, 'findings_count': 1,
                  'reviewer_text_excerpt_untrusted_data': 'Fix the reported issue.'}
        store = type('S', (), {'get': lambda _self, _task_id: _record()})()
        registered = (type('Registration', (), {'root': '/registered/d6'})(), object())
        captured = []
        with patch.object(review_repair, '_registered_d6', return_value=registered):
            result = review_repair._delegate_repair(
                task=task, developer=stored, packet=packet, executor_task=EXECUTOR,
                delegate=lambda args, store: captured.append(args) or 0, store=store)
        self.assertEqual(result, 0)
        self.assertEqual(captured[0].provider, 'gw-agent')
        self.assertEqual(captured[0].data_class, 'PUBLIC_REPO')
        legacy_calls = []
        with patch.object(review_repair, '_registered_d6', return_value=registered):
            with self.assertRaises(LLMToolError) as caught:
                review_repair._delegate_repair(
                    task=task, developer=legacy, packet=packet, executor_task=EXECUTOR,
                    delegate=lambda args, store: legacy_calls.append(args) or 0,
                    store=store)
        self.assertEqual(caught.exception.review_repair_code, 'REPAIR_DATA_CLASS_INVALID')
        self.assertEqual(legacy_calls, [])

    def test_repair_delegate_refuses_unregistered_or_moved_d6_before_provider(self):
        original = _record()
        repair_task = {'task_id': 'f' * 32,
                       'pm_assignment': {'acceptance': ['Fix it.']}}
        developer = {'provider_profile': 'agy', 'read_paths': ['src'],
                     'write_paths': ['src/file.py'], 'acceptance': ['Fix it.']}
        packet = {'task': TASK, 'failed_head': HEAD, 'version': 1,
                  'verdict_comment_id': 9, 'findings_count': 1,
                  'reviewer_text_excerpt_untrusted_data': 'Finding'}
        provider_calls = []
        store = type('S', (), {'get': lambda _self, _task_id: original})()
        registration = type('Registration', (), {
            'root': '/registered/d6', 'common_dir': '/canonical/.git',
            'branch': 'feat/task', 'result_sha': HEAD})()
        moved_snapshot = type('Snapshot', (), {
            'head': 'f' * 40, 'root': '/registered/d6',
            'common_dir': '/canonical/.git', 'branch': 'feat/task'})()
        for reader, snapshot in ((RuntimeError('missing'), None),
                                 (registration, moved_snapshot)):
            with self.subTest(registration=reader), \
                 patch.object(review_repair.task_completion,
                              'read_d6_registration',
                              side_effect=reader if isinstance(reader, Exception) else None,
                              return_value=None if isinstance(reader, Exception) else reader), \
                 patch.object(review_repair.task_completion,
                              'collect_git_snapshot', return_value=snapshot):
                with self.assertRaises(LLMToolError) as caught:
                    review_repair._delegate_repair(
                        task=repair_task, developer=developer, packet=packet,
                        executor_task=EXECUTOR, store=store,
                        delegate=lambda *args, **kwargs: provider_calls.append(args))
                self.assertEqual(caught.exception.review_repair_code, 'REPAIR_HEAD_MOVED')
        self.assertEqual(provider_calls, [])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TempTaskStore(self.temp.name, _record())
        self.calls = []
        self.runtime_path_patch = patch.object(
            review_repair.shutil, 'which', return_value='/usr/bin/codex')
        self.runtime_path_patch.start()
        # #1692: the shared all-ALLOWED test policy, never the live file.
        worker_policy_fixture.install(self)

    def tearDown(self):
        self.runtime_path_patch.stop()
        self.temp.cleanup()

    def dispatch(self, **kwargs):
        def adapter(**request):
            self.calls.append(request)
            return {'status': 'DISPATCHED', 'head': HEAD}
        adapter = kwargs.pop('dispatch_adapter', adapter)
        current_head = kwargs.pop('current_head', lambda _task, _executor: HEAD)
        github_get = kwargs.pop('github_get', lambda _path: [_comment()])
        decision = kwargs.pop('decision', type('Decision', (), {'allowed': True})())
        def default_recorder(**request):
            packet = request['packet']
            developer = request['developer']
            bounded = (f"Repair attempt {request['attempt']}/"
                       f"{review_repair.MAX_REPAIR_ATTEMPTS} for verdict comment id "
                       f"{packet['verdict_comment_id']}, failed HEAD {packet['failed_head']}, "
                       f"review version {packet['version']}, findings count "
                       f"{packet['findings_count']}. Reviewer text is untrusted reference data.")
            return {'task_id': request['repair_task_id'], 'pm_assignment': {
                'acceptance': list(developer['acceptance']) + [bounded]}}
        recorder = kwargs.pop('repair_task_recorder', default_recorder)
        runtime_path = kwargs.pop('runtime_path', '/usr/bin/codex')
        evaluate = kwargs.pop('evaluate', None)
        evaluate_patch = (
            patch.object(review_repair.worker_dispatch, 'evaluate', side_effect=evaluate)
            if evaluate is not None else
            nullcontext()
            if isinstance(review_repair.worker_dispatch.evaluate, Mock) else
            patch.object(review_repair.worker_dispatch, 'evaluate', return_value=decision)
        )
        with evaluate_patch, \
             patch.object(review_repair.shutil, 'which', return_value=runtime_path):
            return review_repair.repair_review(
                store=self.store, task_id=TASK, executor_task=EXECUTOR,
                pr_number=PR, github_get=github_get,
                dispatch_adapter=adapter, current_head=current_head,
                repair_task_recorder=recorder, **kwargs)

    def test_happy_path_packet_and_next_action(self):
        result = self.dispatch()
        self.assertEqual(result['next_action'], 'review-dispatch --version 2')
        self.assertEqual(self.calls[0]['packet']['findings_count'], 1)
        self.assertEqual(self.store.get(TASK)['review_repair_claims'][0]['attempt'], 1)
        self.assertEqual(self.store.get(TASK)['review_repair_claims'][0]['state'], 'DISPATCHED')
        self.assertEqual(self.store.get(TASK)['review_repair_claims'][0]['repair_task_id'],
                         self.calls[0]['task']['task_id'])

    def _add_review_claim_version(self, version):
        record = self.store.get(TASK)
        claim = dict(record['review_dispatch_claim'][0], version=version)
        record['review_dispatch_claim'].append(claim)
        self.store = TempTaskStore(self.temp.name, record)

    @staticmethod
    def _classified_verdict(comment, *, head, version, task):
        body = comment['body']
        if f'Version: v{version} | HEAD: {head}' not in body:
            return None
        outcome = 'REVIEW_PASS' if 'Verdict: PASS |' in body else 'REVIEW_FAIL'
        return (comment['updated_at'], comment['id'], outcome, 1)

    def test_failed_event_uses_highest_claim_version_for_same_head(self):
        self._add_review_claim_version(2)
        comments = [_comment(status='PASS', comment_id=9, version=1),
                    _comment(status='FAIL', comment_id=10, version=2)]
        with patch.object(review_repair.review_result, '_comment_verdict',
                          side_effect=self._classified_verdict):
            result = self.dispatch(github_get=lambda _path: comments)
        packet = self.calls[0]['packet']
        self.assertEqual(result['next_action'], 'review-dispatch --version 3')
        self.assertEqual(packet['version'], 2)
        self.assertEqual(packet['verdict_comment_id'], 10)

    def test_failed_event_refuses_when_highest_claim_version_passes(self):
        self._add_review_claim_version(2)
        comments = [_comment(status='FAIL', comment_id=9, version=1),
                    _comment(status='PASS', comment_id=10, version=2)]
        with patch.object(review_repair.review_result, '_comment_verdict',
                          side_effect=self._classified_verdict):
            with self.assertRaises(LLMToolError) as caught:
                self.dispatch(github_get=lambda _path: comments)
        self.assertEqual(caught.exception.review_repair_code,
                         'REVIEW_FAIL_EVENT_MISSING')
        self.assertEqual(self.calls, [])

    def test_remote_test_fail_receipt_enters_real_repair_path(self):
        for excerpt in ('FAIL: test_remote\nAssertionError',
                        '失败: test_remote\nAssertionError'):
            with self.subTest(excerpt=excerpt):
                self.store = TempTaskStore(self.temp.name, _record())
                receipt = {
                    'task_id': TASK, 'run_id': RUN, 'executor_task': EXECUTOR,
                    'source_sha': HEAD, 'result': 'FAIL', 'modules': ['test_remote'],
                    'failure_excerpt': excerpt,
                }
                receipt['receipt_digest'] = hashlib.sha256(
                    task_store._json_bytes(receipt)).hexdigest()
                dispatched = []
                def adapter(**request):
                    dispatched.append(request)
                    return {'status': 'DISPATCHED', 'head': HEAD}
                result = self.dispatch(
                    remote_test_receipt=receipt, dispatch_adapter=adapter,
                    repair_task_recorder=lambda **request: {
                        'task_id': request['repair_task_id'],
                        'pm_assignment': {'acceptance': ['Repair the reported issue.']},
                    })
                packet = dispatched[0]['packet']
                self.assertEqual(result['next_action'], 'review-dispatch --version 1')
                self.assertEqual(packet['failed_head'], HEAD)
                self.assertEqual(packet['version'], 0)
                self.assertEqual(packet['verdict_comment_id'], 0)
                self.assertEqual(packet['findings_count'], 0)
                self.assertEqual(packet['reviewer_text_excerpt_untrusted_data'], excerpt)
                self.assertEqual(task_submission_cli._validate_repair_packet(packet), packet)
                claim = self.store.get(TASK)['review_repair_claims'][0]
                self.assertIsNone(claim['comment_id'])
                self.assertEqual(claim['version'], 1)

    def test_remote_test_fail_receipt_with_wrong_digest_creates_no_claim(self):
        receipt = {
            'task_id': TASK, 'run_id': RUN, 'executor_task': EXECUTOR,
            'source_sha': HEAD, 'result': 'FAIL', 'modules': ['test_remote'],
            'failure_excerpt': 'FAIL: test_remote\nAssertionError',
            'receipt_digest': '0' * 64,
        }
        with self.assertRaises(LLMToolError) as caught:
            self.dispatch(remote_test_receipt=receipt)
        self.assertEqual(caught.exception.review_repair_code,
                         'REMOTE_TEST_RECEIPT_INVALID')
        self.assertNotIn('review_repair_claims', self.store.get(TASK))

    def test_remote_test_repair_claim_passes_real_task_store_validation(self):
        # A record-pm Task is sufficient to exercise the persisted claim schema.
        from test_active_pm_inbox import ActivePMInboxTests, _RESPONSE

        fixture = ActivePMInboxTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.store = TaskStore(Path(self.temp.name).resolve())
        with redirect_stdout(StringIO()):
            task = task_submission_cli.run_task_record_pm(
                Namespace(stdin_json=True, pm_session_id=fixture.session,
                          pm_runtime='claude-code', pm_cwd=str(fixture.pm_cwd)),
                store=self.store,
                input_stream=StringIO(json.dumps({
                    'request': 'Bounded non-sensitive remote-test repair fixture.',
                    'response': _RESPONSE})),
                return_record=True, task_id=TASK)
        packet = {
            'task': TASK, 'pr': PR, 'executor_task': EXECUTOR,
            'failed_head': HEAD, 'version': 0, 'verdict_comment_id': 0,
            'findings_count': 0,
            'reviewer_text_excerpt_untrusted_data':
                'FAIL: test_remote\nAssertionError',
        }
        claim = {
            'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD,
            'version': 1, 'attempt': 1, 'family': f'pr-{PR}',
            'state': 'CLAIMED', 'repair_task_id': '1' * 32,
            'comment_id': None,
            'packet_sha256': hashlib.sha256(json.dumps(
                packet, ensure_ascii=True, sort_keys=True,
                separators=(',', ':')).encode('utf-8')).hexdigest(),
            'claimed_at': '2026-10-27T01:00:00Z',
        }
        saved = self.store.compare_and_update(
            TASK, expected={'record_sha256': task['record_sha256']},
            review_repair_claims=[claim])
        self.assertEqual(saved['review_repair_claims'], [claim])

        invalid = dict(claim, version=0, attempt=2, repair_task_id='2' * 32,
                       claimed_at='2026-10-27T01:01:00Z')
        with self.assertRaises(LLMToolError) as caught:
            self.store.compare_and_update(
                TASK, expected={'record_sha256': saved['record_sha256']},
                review_repair_claims=[claim, invalid])
        self.assertEqual(caught.exception.task_store_error_code,
                         'REVIEW_REPAIR_CLAIM_INVALID')

    def test_replay_after_restart_does_not_dispatch_twice(self):
        self.dispatch()
        restarted = TempTaskStore(self.temp.name, self.store.get(TASK))
        self.store = restarted
        result = self.dispatch()
        self.assertTrue(result['replayed'])
        self.assertEqual(result['repair_task_id'],
                         self.store.get(TASK)['review_repair_claims'][0]['repair_task_id'])
        self.assertEqual(len(self.calls), 1)

    def _confirm_rca(self, family=f'pr-{PR}'):
        # #1502 WP-D (D1): a second review repair needs an RCA_CONFIRMED Debug on the failed HEAD.
        record = self.store.get(TASK)
        report = 'Root cause evidence.'
        record['high_debug'] = [{
            'family': family, 'status': 'RCA_CONFIRMED',
            'examined_sha': HEAD, 'recorded_at': '2026-10-27T03:00:00Z',
            'report': report, 'report_sha256': hashlib.sha256(
                report.encode()).hexdigest()}]
        self.store = TempTaskStore(self.temp.name, record)

    def test_failed_claim_starts_a_new_repair_attempt(self):
        self.dispatch()
        record = self.store.get(TASK)
        record['review_repair_claims'][0]['state'] = 'FAILED'
        self.store = TempTaskStore(self.temp.name, record)
        self._confirm_rca()
        result = self.dispatch()
        claims = self.store.get(TASK)['review_repair_claims']
        self.assertEqual(result['claim']['attempt'], 2)
        self.assertEqual(claims[0]['state'], 'FAILED')
        self.assertEqual(claims[1]['state'], 'DISPATCHED')
        self.assertNotEqual(claims[0]['repair_task_id'], claims[1]['repair_task_id'])

    def test_retry_guard_refusal_keeps_dispatched_claim(self):
        self._repair_history(3)
        record = self.store.get(TASK)
        retry_id = record['review_repair_claims'][-1]['repair_task_id']
        with self.assertRaisesRegex(Exception, 'refused') as raised:
            self.dispatch(family='network', retry_claim_id=retry_id,
                          github_get=lambda _path: [_comment(version=4)])
        self.assertEqual(raised.exception.review_repair_code, 'REPAIR_LIMIT_REACHED')
        self.assertEqual(self.store.get(TASK)['review_repair_claims'][-1]['state'],
                         'DISPATCHED')

    def test_retry_claim_is_failed_atomically_with_new_attempt(self):
        self._repair_history(1)
        self._confirm_rca('network')
        retry_id = self.store.get(TASK)['review_repair_claims'][-1]['repair_task_id']
        result = self.dispatch(family='network', retry_claim_id=retry_id,
                               github_get=lambda _path: [_comment(version=2)])
        claims = self.store.get(TASK)['review_repair_claims']
        self.assertEqual(result['claim']['attempt'], 2)
        self.assertEqual([claim['state'] for claim in claims], ['FAILED', 'DISPATCHED'])

    def test_third_attempt_on_a_new_head_at_limit_is_directed_to_split(self):
        # D14 (spec section 7): repairs used up, a new valid FAIL escalates a ROOT.
        result = self.dispatch()
        record = self.store.get(TASK)
        second = dict(record['review_repair_claims'][0], head_sha='a' * 40,
                      version=2, attempt=2, packet_sha256='1' * 64,
                      repair_task_id='1' * 32)
        record['review_repair_claims'].append(second)
        record['review_dispatch_claim'].append({
            'executor_task': EXECUTOR, 'pr': PR, 'head_sha': 'b' * 40,
            'version': 3, 'state': 'DISPATCHED', 'reviewer_task': 'reviewer-d6',
            'native_session_id': 'review-session-2', 'account_mode': 'DEFAULT_SECOND_ACCOUNT',
            'claimed_at': '2026-10-27T00:00:00Z', 'updated_at': '2026-10-27T00:00:00Z'})
        record['completion_events'].append({
            'event_id': '2' * 32, 'source': 'REVIEWER', 'state': 'PENDING',
            'source_sha': 'b' * 40, 'session_id': 'review-' + '2' * 32,
            'received_at': '2026-10-27T02:01:00Z'})
        self.store = TempTaskStore(self.temp.name, record)
        with self.assertRaisesRegex(Exception, 'refused') as raised:
            self.dispatch(github_get=lambda _path: [_comment(
                comment_id=10, head='b' * 40, version=3)],
                current_head=lambda _task, _executor: 'b' * 40)
        self.assertEqual(getattr(raised.exception, 'review_repair_code'),
                         'REPAIR_SPLIT_REQUIRED')
        self.assertEqual(len(self.store.get(TASK)['review_repair_claims']), 2)
        self.assertEqual(len(self.calls), 1)

    def _repair_history(self, count, *, status=None, examined_sha=HEAD):
        record = self.store.get(TASK)
        base = dict(record['review_dispatch_claim'][0])
        record['review_repair_claims'] = []
        for attempt in range(1, count + 1):
            record['review_repair_claims'].append({
                'executor_task': EXECUTOR, 'pr': PR, 'family': 'network',
                'head_sha': HEAD, 'version': attempt, 'attempt': attempt,
                'state': 'DISPATCHED', 'repair_task_id': str(attempt) * 32,
                'comment_id': 9, 'packet_sha256': 'f' * 64,
                'claimed_at': f'2026-10-27T0{attempt}:00:00Z',
            })
        version = count + 1
        base.update(head_sha=HEAD, version=version)
        record['review_dispatch_claim'] = [base]
        event_id = str(version) * 32
        record['completion_events'] = [{
            'event_id': event_id, 'source': 'REVIEWER', 'state': 'PENDING',
            'source_sha': HEAD, 'session_id': 'review-' + event_id,
            'received_at': f'2026-10-27T0{version}:01:00Z'}]
        if status is not None:
            report = 'Root cause evidence.'
            record['high_debug'] = [{
                'family': 'network', 'status': status,
                'examined_sha': examined_sha, 'recorded_at': '2026-10-27T03:00:00Z',
                'report': report, 'report_sha256': hashlib.sha256(
                    report.encode()).hexdigest()}]
        self.store = TempTaskStore(self.temp.name, record)
        return version

    def test_third_attempt_is_refused_and_directed_to_high_debug(self):
        version = self._repair_history(2)
        with self.assertRaisesRegex(Exception, 'refused') as raised:
            self.dispatch(family='network', github_get=lambda _path: [
                _comment(comment_id=11, version=version)])
        self.assertEqual(raised.exception.review_repair_code,
                         'REPAIR_LIMIT_REACHED')
        self.assertEqual(len(self.store.get(TASK)['review_repair_claims']), 2)
        self.assertEqual(self.calls, [])

    def test_confirmed_high_debug_does_not_allow_third_attempt(self):
        version = self._repair_history(2, status='RCA_CONFIRMED')
        with self.assertRaisesRegex(Exception, 'refused') as raised:
            self.dispatch(family='network', github_get=lambda _path: [
                _comment(comment_id=11, version=version)])
        self.assertEqual(raised.exception.review_repair_code, 'REPAIR_LIMIT_REACHED')
        self.assertEqual(len(self.store.get(TASK)['review_repair_claims']), 2)
        self.assertEqual(self.calls, [])

    # #1502 WP-D (O2, D1, D3): the review repair counting gate.
    def _second_fail(self, status=None, head='e' * 40):
        """A repair claim on an earlier HEAD, then a FAIL on HEAD: valid FAIL 2."""
        self._seed_repair_claims([self._stored_claim(attempt=1, repair_id='1' * 32,
                                                     head=head, state='DISPATCHED')])
        if status is not None:
            record = self.store.get(TASK)
            report = 'Root cause evidence.'
            record['high_debug'] = [{
                'family': f'pr-{PR}', 'status': status, 'examined_sha': HEAD,
                'recorded_at': '2026-10-27T03:00:00Z', 'report': report,
                'report_sha256': hashlib.sha256(report.encode()).hexdigest()}]
            self.store = TempTaskStore(self.temp.name, record)

    def test_second_valid_fail_without_debug_is_refused_debug_required(self):
        self._second_fail()
        self._assert_refused('REPAIR_DEBUG_REQUIRED')

    def test_unconfirmed_debug_on_second_fail_escalates_to_split(self):
        for status in ('RCA_UNCONFIRMED', 'BLOCKED'):
            with self.subTest(status=status):
                self._second_fail(status)
                self._assert_refused('REPAIR_SPLIT_REQUIRED')

    def test_confirmed_debug_binds_its_report_into_the_second_repair(self):
        self._second_fail('RCA_CONFIRMED')
        result = self.dispatch()
        digest = hashlib.sha256(b'Root cause evidence.').hexdigest()
        self.assertEqual(result['claim']['attempt'], 2)
        self.assertEqual(result['claim']['high_debug_report_sha256'], digest)
        packet = self.calls[0]['packet']
        self.assertEqual(packet['high_debug_report_sha256'], digest)
        self.assertEqual(packet['high_debug_report_untrusted_data'], 'Root cause evidence.')

    def test_a_second_review_of_one_head_is_one_valid_fail(self):
        # v1 and v2 FAIL on one HEAD count once: an unconfirmed Debug does not split.
        version = self._repair_history(1, status='RCA_UNCONFIRMED')
        self._assert_refused('REPAIR_DEBUG_REQUIRED', family='network',
                             github_get=lambda _path: [_comment(version=version)])

    def test_only_low_or_advisory_findings_are_not_a_valid_fail(self):
        def low(_path):
            comment = _comment()
            comment['body'] = comment['body'].replace(
                'Finding: address the reported issue\n',
                'Finding: F1 [Low] a.py:1 style\nFinding: F2 [Advisory] a.py:2 note\n')
            return [comment]
        self._assert_refused('REPAIR_NO_BLOCKING_FINDING', github_get=low)

    def test_repair_limit_comes_from_policy_json(self):
        from llmtool import repair_policy
        self.assertEqual(repair_policy.load_policy()['repair_max'], MAX)

    def test_legacy_claims_count_as_pr_family(self):
        self._repair_history(2)
        record = self.store.get(TASK)
        for claim in record['review_repair_claims']:
            claim.pop('family')
        self.store = TempTaskStore(self.temp.name, record)
        with self.assertRaisesRegex(Exception, 'refused') as raised:
            self.dispatch(github_get=lambda _path: [_comment(version=3)])
        self.assertEqual(raised.exception.review_repair_code,
                         'REPAIR_LIMIT_REACHED')

    # Row 35: review repairs and EC2 remote-test repairs keep separate counts.

    def _seed_repair_claims(self, claims):
        record = self.store.get(TASK)
        record['review_repair_claims'] = claims
        self.store = TempTaskStore(self.temp.name, record)

    @staticmethod
    def _stored_claim(*, attempt, repair_id, family=f'pr-{PR}', comment_id=9,
                      head='a' * 40, version=None, state='FAILED',
                      packet_sha='f' * 64):
        claim = {'executor_task': EXECUTOR, 'pr': PR, 'head_sha': head,
                 'version': attempt if version is None else version,
                 'attempt': attempt, 'state': state, 'repair_task_id': repair_id,
                 'comment_id': comment_id, 'packet_sha256': packet_sha,
                 'claimed_at': f'2026-10-27T0{attempt}:00:00Z'}
        if family is not None:
            claim['family'] = family
        return claim

    def _fail_last_claim(self):
        record = self.store.get(TASK)
        record['review_repair_claims'][-1]['state'] = 'FAILED'
        self.store = TempTaskStore(self.temp.name, record)

    def _assert_refused(self, code, **kwargs):
        before = self.store.get(TASK).get('review_repair_claims')
        calls = len(self.calls)
        with self.assertRaises(LLMToolError) as caught:
            self.dispatch(**kwargs)
        self.assertEqual(caught.exception.review_repair_code, code)
        self.assertEqual(self.store.get(TASK).get('review_repair_claims'), before)
        self.assertEqual(len(self.calls), calls)

    def test_ec2_repair_claim_is_stored_in_ec2_family(self):
        result = self.dispatch(remote_test_receipt=_receipt())
        self.assertEqual(result['claim']['family'], EC2_FAMILY)
        self.assertEqual(result['claim']['attempt'], 1)
        self.assertIsNone(result['claim']['comment_id'])
        self.store = TempTaskStore(self.temp.name, _record())
        result = self.dispatch(remote_test_receipt=_receipt(), family='network')
        self.assertEqual(result['claim']['family'], 'network-ec2')

    def test_review_repair_claim_stays_in_base_family(self):
        self.assertEqual(self.dispatch()['claim']['family'], f'pr-{PR}')
        self.store = TempTaskStore(self.temp.name, _record())
        self.assertEqual(self.dispatch(family='network')['claim']['family'], 'network')

    def test_ec2_repairs_leave_every_review_repair_available(self):
        self._seed_repair_claims([
            self._stored_claim(attempt=n, family=EC2_FAMILY, comment_id=None,
                               repair_id=f'{n:032x}')
            for n in range(1, MAX + 1)])
        self._confirm_rca()
        for expected in range(1, MAX + 1):
            result = self.dispatch()
            self.assertEqual((result['claim']['family'], result['claim']['attempt']),
                             (f'pr-{PR}', expected))
            self._fail_last_claim()
        self._assert_refused('REPAIR_LIMIT_REACHED')
        self.assertEqual(len(self.calls), MAX)

    def test_review_repairs_leave_every_ec2_repair_available(self):
        self._seed_repair_claims([
            self._stored_claim(attempt=n, repair_id=f'{n:032x}')
            for n in range(1, MAX + 1)])
        for expected in range(1, MAX + 1):
            result = self.dispatch(remote_test_receipt=_receipt())
            self.assertEqual((result['claim']['family'], result['claim']['attempt']),
                             (EC2_FAMILY, expected))
            self._fail_last_claim()
        self._assert_refused('REPAIR_LIMIT_REACHED', remote_test_receipt=_receipt())
        self.assertEqual(len(self.calls), MAX)

    def test_legacy_ec2_claim_counts_on_both_counts(self):
        legacy = self._stored_claim(attempt=1, comment_id=None, repair_id='1' * 32)
        self._seed_repair_claims([legacy] + [
            self._stored_claim(attempt=n, repair_id=f'{n:032x}')
            for n in range(2, MAX + 1)])
        # Review count: the legacy claim plus the review claims reach the cap,
        # so the new valid FAIL on HEAD escalates the ROOT (D14).
        self._assert_refused('REPAIR_SPLIT_REQUIRED')
        # EC2 count: the legacy claim uses one EC2 repair; the store numbers
        # the new '-ec2' claims from 1.
        for expected in range(1, MAX):
            result = self.dispatch(remote_test_receipt=_receipt())
            self.assertEqual((result['claim']['family'], result['claim']['attempt']),
                             (EC2_FAMILY, expected))
            self._fail_last_claim()
        self._assert_refused('REPAIR_LIMIT_REACHED', remote_test_receipt=_receipt())

    def test_claims_without_family_or_with_comment_id_count_as_review_only(self):
        self._seed_repair_claims(
            [self._stored_claim(attempt=1, family=None, comment_id=None,
                                repair_id='1' * 32)] +
            [self._stored_claim(attempt=n, comment_id=9, repair_id=f'{n:032x}')
             for n in range(2, MAX + 1)])
        self._assert_refused('REPAIR_SPLIT_REQUIRED')
        result = self.dispatch(remote_test_receipt=_receipt())
        self.assertEqual((result['claim']['family'], result['claim']['attempt']),
                         (EC2_FAMILY, 1))

    def _receipt_packet_sha(self):
        packet = review_repair._remote_test_failure(
            self.store.get(TASK), executor_task=EXECUTOR, pr_number=PR,
            receipt=_receipt())
        return hashlib.sha256(json.dumps(
            packet, ensure_ascii=True, sort_keys=True,
            separators=(',', ':')).encode('utf-8')).hexdigest()

    def test_legacy_in_flight_ec2_claim_replays_then_retries_into_ec2_family(self):
        legacy = self._stored_claim(
            attempt=1, comment_id=None, head=HEAD, state='DISPATCHED',
            repair_id='1' * 32, packet_sha=self._receipt_packet_sha())
        self._seed_repair_claims([legacy])
        result = self.dispatch(remote_test_receipt=_receipt())
        self.assertTrue(result['replayed'])
        self.assertEqual(result['repair_task_id'], '1' * 32)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.get(TASK)['review_repair_claims'], [legacy])

        result = self.dispatch(remote_test_receipt=_receipt(), retry_claim_id='1' * 32)
        claims = self.store.get(TASK)['review_repair_claims']
        self.assertEqual([claim['state'] for claim in claims], ['FAILED', 'DISPATCHED'])
        self.assertEqual((claims[1]['family'], claims[1]['attempt'], claims[1]['version']),
                         (EC2_FAMILY, 1, 2))
        self.assertEqual(len(self.calls), 1)

    def test_invalid_repair_families_are_refused_before_any_write(self):
        for receipt, family in ((None, EC2_FAMILY), (_receipt(), EC2_FAMILY),
                                (None, 'network-ec2'), (_receipt(), 'x' * 61)):
            with self.subTest(receipt=receipt is not None, family=family):
                self._assert_refused('REPAIR_FAMILY_INVALID', family=family,
                                     remote_test_receipt=receipt)
                self.assertNotIn('review_repair_claims', self.store.get(TASK))
        # The 64 character limit applies to the stored family.
        self.assertEqual(self.dispatch(family='x' * 61)['claim']['family'], 'x' * 61)
        result = self.dispatch(family='y' * 60, remote_test_receipt=_receipt())
        self.assertEqual(result['claim']['family'], 'y' * 60 + '-ec2')

    def test_review_and_ec2_claims_with_same_identity_refuse_without_provider_call(self):
        ec2 = self._stored_claim(attempt=1, family=EC2_FAMILY, comment_id=None,
                                 head=HEAD, version=1, repair_id='1' * 32)
        self._seed_repair_claims([ec2])
        recorded = []
        self._assert_refused(
            'REPAIR_CLAIM_UNAVAILABLE',
            repair_task_recorder=lambda **request: recorded.append(request))
        self.assertEqual(recorded, [])
        self.assertEqual(self.calls, [])

    def test_ec2_claim_after_review_claim_on_same_head_takes_next_version(self):
        self._seed_repair_claims([self._stored_claim(
            attempt=1, head=HEAD, version=1, repair_id='1' * 32)])
        result = self.dispatch(remote_test_receipt=_receipt())
        self.assertEqual((result['claim']['family'], result['claim']['attempt'],
                          result['claim']['version']), (EC2_FAMILY, 1, 2))

    def test_split_family_claims_pass_real_task_store_validation(self):
        from test_active_pm_inbox import ActivePMInboxTests, _RESPONSE

        fixture = ActivePMInboxTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.store = TaskStore(Path(self.temp.name).resolve())
        with redirect_stdout(StringIO()):
            task = task_submission_cli.run_task_record_pm(
                Namespace(stdin_json=True, pm_session_id=fixture.session,
                          pm_runtime='claude-code', pm_cwd=str(fixture.pm_cwd)),
                store=self.store,
                input_stream=StringIO(json.dumps({
                    'request': 'Bounded non-sensitive split repair family fixture.',
                    'response': _RESPONSE})),
                return_record=True, task_id=TASK)
        ec2 = self._stored_claim(attempt=1, family=EC2_FAMILY, comment_id=None,
                                 head=HEAD, version=1, state='CLAIMED',
                                 repair_id='1' * 32)
        saved = self.store.compare_and_update(
            TASK, expected={'record_sha256': task['record_sha256']},
            review_repair_claims=[ec2])
        colliding = self._stored_claim(attempt=1, head=HEAD, version=1,
                                       state='CLAIMED', repair_id='2' * 32)
        with self.assertRaises(LLMToolError) as caught:
            self.store.compare_and_update(
                TASK, expected={'record_sha256': saved['record_sha256']},
                review_repair_claims=[ec2, colliding])
        self.assertEqual(caught.exception.task_store_error_code,
                         'REVIEW_REPAIR_CLAIM_INVALID')
        review = dict(colliding, version=2)
        saved = self.store.compare_and_update(
            TASK, expected={'record_sha256': saved['record_sha256']},
            review_repair_claims=[ec2, review])
        self.assertEqual(saved['review_repair_claims'], [ec2, review])

    def test_head_moved(self):
        with self.assertRaisesRegex(Exception, 'refused') as raised:
            self.dispatch(current_head=lambda _task, _executor: 'a' * 40)
        self.assertEqual(getattr(raised.exception, 'review_repair_code'), 'REPAIR_HEAD_MOVED')
        self.assertEqual(self.calls, [])

    def test_paused_policy_refused(self):
        decision = type('Decision', (), {'allowed': False, 'reason_code': 'PAUSED'})()
        with patch.object(review_repair.worker_dispatch, 'evaluate', return_value=decision):
            with self.assertRaisesRegex(Exception, 'refused') as raised:
                self.dispatch(decision=decision)
        self.assertEqual(getattr(raised.exception, 'review_repair_code'), 'PAUSED')

    def test_gw_agent_paused_policy_refused_before_claim(self):
        record = self.store.get(TASK)
        record['developer']['provider_profile'] = 'gw-agent'
        record['developer']['data_class'] = 'SYNTHETIC'
        record['pm_assignment']['route'] = 'gw-agent'
        self.store = TempTaskStore(self.temp.name, record)
        decision = type('Decision', (), {'allowed': False, 'reason_code': 'PAUSED'})()
        evaluated = []
        def capture_eval(*args, **kwargs):
            evaluated.append(args)
            return decision
        with patch.object(review_repair.worker_dispatch, 'evaluate', side_effect=capture_eval):
            with self.assertRaises(LLMToolError) as caught:
                self.dispatch(decision=decision)
        self.assertEqual(caught.exception.review_repair_code, 'PAUSED')
        self.assertEqual(evaluated, [('gw-agent', None, TASK)])
        self.assertNotIn('review_repair_claims', self.store.get(TASK))
        self.assertEqual(self.calls, [])

    def test_company_route_refused_before_task_record_or_claim(self):
        record = self.store.get(TASK)
        record['developer']['provider_profile'] = 'company-gw'
        record['pm_assignment']['route'] = 'company-gw'
        self.store = TempTaskStore(self.temp.name, record)
        recorded = []
        with self.assertRaisesRegex(Exception, 'refused') as raised:
            self.dispatch(repair_task_recorder=lambda **kwargs: recorded.append(kwargs))
        self.assertEqual(getattr(raised.exception, 'review_repair_code'),
                         'REPAIR_DEVELOPER_ROUTE_UNSUPPORTED')
        self.assertEqual(recorded, [])
        self.assertNotIn('review_repair_claims', self.store.get(TASK))

    def test_codex_developer_repair_is_retired_before_claim(self):
        # #1502 O1 / WP-C: a Codex Developer Task gets no repair route.
        record = self.store.get(TASK)
        record['developer']['provider_profile'] = 'codex'
        record['pm_assignment']['route'] = 'codex'
        self.store = TempTaskStore(self.temp.name, record)
        recorded = []
        with self.assertRaises(LLMToolError) as caught:
            self.dispatch(repair_task_recorder=lambda **kwargs: recorded.append(kwargs))
        self.assertEqual(caught.exception.review_repair_code, 'REPAIR_CODEX_DEVELOPER_RETIRED')
        self.assertEqual((recorded, self.calls), ([], []))
        self.assertNotIn('review_repair_claims', self.store.get(TASK))
        self.assertIsNone(review_repair._worker_key('codex'))

    def test_missing_provider_runtime_refused_before_claim(self):
        # #1502 WP-C: the runtime check applies to the Claude CLI route.
        record = self.store.get(TASK)
        record['developer']['provider_profile'] = 'claude'
        record['developer']['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
        record['pm_assignment']['route'] = 'claude'
        self.store = TempTaskStore(self.temp.name, record)
        recorded = []
        with self.assertRaisesRegex(Exception, 'refused') as raised:
            self.dispatch(runtime_path=None,
                repair_task_recorder=lambda **kwargs: recorded.append(kwargs))
        self.assertEqual(raised.exception.review_repair_code, 'RUNTIME_NOT_ON_PATH')
        self.assertEqual(recorded, [])
        self.assertNotIn('review_repair_claims', self.store.get(TASK))

    # #1502 WP-D D11 repair 1: a counted claim is committed only after the
    # repair Task is recorded and validated and the final authority recheck passes.

    @staticmethod
    def _decisions(*allowed, log=None):
        items = iter([type('D', (), {'allowed': True})() if ok else
                      type('D', (), {'allowed': False, 'reason_code': 'PAUSED'})()
                      for ok in allowed])

        def evaluate(*_args, **_kwargs):
            if log is not None:
                log.append('evaluate')
            return next(items)
        return evaluate

    @staticmethod
    def _recording_recorder(recorded, log=None):
        def recorder(**request):
            recorded.append(request['repair_task_id'])
            if log is not None:
                log.append('record')
            return {'task_id': request['repair_task_id'],
                    'pm_assignment': {'acceptance': ['Repair the reported issue.']}}
        return recorder

    def test_final_recheck_refusal_preserves_claims_and_counts(self):
        recorded = []
        with self.assertRaises(LLMToolError) as caught:
            self.dispatch(evaluate=self._decisions(True, False),
                          repair_task_recorder=self._recording_recorder(recorded))
        self.assertEqual(caught.exception.review_repair_code, 'PAUSED')
        # The prepared Task is surfaced for PM reconciliation, not dispatched.
        self.assertEqual(len(recorded), 1)
        self.assertEqual(caught.exception.review_repair_unclaimed_task_id, recorded[0])
        self.assertNotIn('review_repair_claims', self.store.get(TASK))
        self.assertEqual(self.calls, [])
        # A later authorization permits exactly one dispatch on the unused count.
        result = self.dispatch(repair_task_recorder=self._recording_recorder(recorded))
        # A fresh dispatch carries the provider result; only a reused claim is 'replayed'.
        self.assertNotIn('replayed', result)
        self.assertEqual(result['result'], {'status': 'DISPATCHED', 'head': HEAD})
        self.assertEqual((result['claim']['attempt'], result['claim']['state']),
                         (1, 'DISPATCHED'))
        self.assertEqual(result['repair_task_id'], recorded[1])
        self.assertNotEqual(recorded[0], recorded[1])
        claims = self.store.get(TASK)['review_repair_claims']
        self.assertEqual([claim['repair_task_id'] for claim in claims], [recorded[1]])
        self.assertEqual(len(self.calls), 1)
        replay = self.dispatch()
        self.assertTrue(replay['replayed'])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.store.get(TASK)['review_repair_claims']), 1)

    def test_claim_is_committed_after_record_and_recheck_and_before_provider(self):
        log = []
        test = self

        class LoggingStore(TempTaskStore):
            def compare_and_update(self, task_id, *, expected, **changes):
                log.append('cas:' + '+'.join(
                    claim['state'] for claim in changes.get('review_repair_claims', [])))
                return super().compare_and_update(task_id, expected=expected, **changes)

        self.store = LoggingStore(self.temp.name, self.store.get(TASK))

        def adapter(**request):
            log.append('provider')
            claims = test.store.get(TASK)['review_repair_claims']
            test.assertEqual([claim['state'] for claim in claims], ['CLAIMED'])
            test.assertEqual(claims[0]['repair_task_id'], request['task']['task_id'])
            return {'status': 'DISPATCHED', 'head': HEAD}

        self.dispatch(evaluate=self._decisions(True, True, log=log),
                      repair_task_recorder=self._recording_recorder([], log=log),
                      dispatch_adapter=adapter)
        self.assertEqual(log, ['evaluate', 'record', 'evaluate', 'cas:CLAIMED',
                               'provider', 'cas:DISPATCHED'])

    def test_claim_cas_failure_leaves_claims_unchanged_and_never_dispatches(self):
        class ConflictStore(TempTaskStore):
            def compare_and_update(self, *args, **kwargs):
                raise RuntimeError('conflict')
        record = self.store.get(TASK)
        self.store = ConflictStore(self.temp.name, record)
        recorded = []
        with self.assertRaises(LLMToolError) as caught:
            self.dispatch(repair_task_recorder=self._recording_recorder(recorded))
        self.assertEqual(caught.exception.review_repair_code, 'REPAIR_CLAIM_UNAVAILABLE')
        self.assertEqual(caught.exception.review_repair_unclaimed_task_id, recorded[0])
        self.assertNotIn('review_repair_claims', self.store.get(TASK))
        self.assertEqual(self.calls, [])

    def test_recorder_exception_commits_no_claim_and_never_dispatches(self):
        evaluated = []

        def broken(**_request):
            raise RuntimeError('recorder failure')

        def refusing(**_request):
            review_repair._refuse('REPAIR_PM_SESSION_UNAVAILABLE')

        for recorder, code in ((broken, 'REPAIR_TASK_RECORD_UNAVAILABLE'),
                               (refusing, 'REPAIR_PM_SESSION_UNAVAILABLE')):
            with self.subTest(code=code):
                evaluated.clear()
                with self.assertRaises(LLMToolError) as caught:
                    self.dispatch(evaluate=self._decisions(True, True, log=evaluated),
                                  repair_task_recorder=recorder)
                self.assertEqual(caught.exception.review_repair_code, code)
                self.assertRegex(caught.exception.review_repair_unclaimed_task_id,
                                 r'^[0-9a-f]{32}$')
                # The final recheck and the CAS are never reached.
                self.assertEqual(evaluated, ['evaluate'])
                self.assertNotIn('review_repair_claims', self.store.get(TASK))
                self.assertEqual(self.calls, [])
        result = self.dispatch()
        self.assertEqual(result['claim']['attempt'], 1)
        self.assertEqual(len(self.calls), 1)

    def test_invalid_recorder_result_commits_no_claim_and_never_dispatches(self):
        for label, make in (('none', lambda _id: None),
                            ('not a dict', lambda _id: [_id]),
                            ('no task id', lambda _id: {}),
                            ('null task id', lambda _id: {'task_id': None}),
                            ('other task id', lambda _id: {'task_id': '9' * 32})):
            with self.subTest(label):
                with self.assertRaises(LLMToolError) as caught:
                    self.dispatch(repair_task_recorder=lambda make=make, **request:
                                  make(request['repair_task_id']))
                self.assertEqual(caught.exception.review_repair_code,
                                 'REPAIR_TASK_RECORD_UNAVAILABLE')
                self.assertNotIn('review_repair_claims', self.store.get(TASK))
                self.assertEqual(self.calls, [])

    def test_refused_replacement_retry_keeps_dispatched_claim_and_count(self):
        self._repair_history(1)
        self._confirm_rca('network')
        before = self.store.get(TASK)['review_repair_claims']
        retry_id = before[-1]['repair_task_id']
        recorded = []
        with self.assertRaises(LLMToolError) as caught:
            self.dispatch(family='network', retry_claim_id=retry_id,
                          github_get=lambda _path: [_comment(version=2)],
                          evaluate=self._decisions(True, False),
                          repair_task_recorder=self._recording_recorder(recorded))
        self.assertEqual(caught.exception.review_repair_code, 'PAUSED')
        self.assertEqual(caught.exception.review_repair_unclaimed_task_id, recorded[0])
        # The retried claim is not FAILED and no replacement claim is counted.
        self.assertEqual(self.store.get(TASK)['review_repair_claims'], before)
        self.assertEqual(self.calls, [])
        result = self.dispatch(family='network', retry_claim_id=retry_id,
                               github_get=lambda _path: [_comment(version=2)])
        claims = self.store.get(TASK)['review_repair_claims']
        self.assertEqual(result['claim']['attempt'], 2)
        self.assertEqual([claim['state'] for claim in claims], ['FAILED', 'DISPATCHED'])
        self.assertNotEqual(claims[1]['repair_task_id'], recorded[0])
        self.assertEqual(len(self.calls), 1)

    def test_adapter_failure_records_unknown_and_never_replays(self):
        def fail_adapter(**_kwargs):
            raise RuntimeError('adapter failure')
        with self.assertRaisesRegex(RuntimeError, 'adapter failure'):
            review_repair.repair_review(
                store=self.store, task_id=TASK, executor_task=EXECUTOR,
                pr_number=PR, github_get=lambda _path: [_comment()],
                current_head=lambda _task, _executor: HEAD,
                dispatch_adapter=fail_adapter,
                repair_task_recorder=lambda **kwargs: {'task_id': kwargs['repair_task_id']})
        result = self.dispatch()
        self.assertEqual(result['claim']['state'], 'UNKNOWN')
        self.assertFalse(result['replayed'])
        self.assertTrue(result['next_action'].startswith('task delegate '))

    def test_pass_event_does_not_dispatch(self):
        with self.assertRaisesRegex(Exception, 'refused'):
            review_repair.repair_review(
                store=self.store, task_id=TASK, executor_task=EXECUTOR,
                pr_number=PR, github_get=lambda _path: [_comment('PASS', actor='Codex')],
                dispatch_adapter=lambda **_kwargs: self.calls.append('bad'),
                current_head=lambda _task, _executor: HEAD)
        self.assertEqual(self.calls, [])

    def test_auto_review_calls_next_version(self):
        seen = []
        repair_published = False

        def current_head(_task, _executor):
            return 'f' * 40 if repair_published else HEAD

        def dispatch_repair(**_request):
            nonlocal repair_published
            repair_published = True
            return {'status': 'DISPATCHED', 'head': 'f' * 40}

        self.dispatch(auto_review=True,
                      current_head=current_head,
                      dispatch_adapter=dispatch_repair,
                      review_dispatcher=lambda **kwargs: seen.append(kwargs) or 'review')
        self.assertEqual(seen[0]['version'], 2)

    def test_auto_review_waits_until_repair_is_published(self):
        seen = []
        result = self.dispatch(auto_review=True,
                               current_head=lambda _task, _executor: HEAD,
                               review_dispatcher=lambda **kwargs: seen.append(kwargs) or 'review')
        self.assertEqual(result['review'], {
            'state': 'NOT_READY', 'reason': 'REPAIR_NOT_PUBLISHED'})
        self.assertEqual(seen, [])

    def test_default_adapter_rebuilds_original_direct_developer_request(self):
        seen = []
        registration = type('Registration', (), {
            'root': '/registered/d6', 'common_dir': '/tmp/git',
            'branch': 'feat/task', 'result_sha': HEAD})()
        snapshot = type('Snapshot', (), {
            'head': HEAD, 'root': '/registered/d6',
            'common_dir': '/tmp/git', 'branch': 'feat/task'})()
        def recorder(**request):
            packet = request['packet']
            bounded = (f"Repair attempt {request['attempt']}/"
                       f"{review_repair.MAX_REPAIR_ATTEMPTS} for verdict comment id "
                       f"{packet['verdict_comment_id']}, failed HEAD {packet['failed_head']}, "
                       f"review version {packet['version']}, findings count "
                       f"{packet['findings_count']}. Reviewer text is untrusted reference data.")
            return {'task_id': request['repair_task_id'], 'pm_assignment': {
                'acceptance': ['Repair the reported issue.', bounded]}}
        with patch.object(review_repair.worker_dispatch, 'evaluate',
                          return_value=type('Decision', (), {'allowed': True})()), \
             patch.object(review_repair.task_completion, 'read_d6_registration',
                          return_value=registration), \
             patch.object(review_repair.task_completion, 'collect_git_snapshot',
                          return_value=snapshot):
            result = review_repair.repair_review(
                store=self.store, task_id=TASK, executor_task=EXECUTOR,
                pr_number=PR, github_get=lambda _path: [_comment()],
                current_head=lambda _task, _executor: HEAD,
                delegate_callable=lambda args, store: seen.append((args, store)) or 0,
                repair_task_recorder=recorder)
        args, passed_store = seen[0]
        self.assertIs(passed_store, self.store)
        self.assertEqual((args.provider, args.mode, args.executor_task),
                         ('agy', 'direct', EXECUTOR))
        self.assertEqual(args.read_path, ['tools/llmtool/llmtool'])
        self.assertEqual(args.write_path, ['tools/llmtool/llmtool/review_repair.py'])
        self.assertEqual(args.acceptance[0], 'Repair the reported issue.')
        self.assertIn('comment id 9', args.acceptance[1])
        self.assertIn(HEAD, args.acceptance[1])
        self.assertIn('findings count 1', args.acceptance[1])
        self.assertEqual(args.repair_packet['verdict_comment_id'], 9)
        self.assertEqual(result['result'], 0)

    def test_repair_task_uses_record_pm_path_and_original_session(self):
        task = _record()
        repair_task_id = '1' * 32
        packet = {'verdict_comment_id': 9, 'failed_head': HEAD, 'version': 1,
                  'findings_count': 1}
        seen = []
        def record_pm(args, *, store, input_stream, return_record, task_id=None):
            envelope = json.loads(input_stream.getvalue())
            seen.append((args, store, envelope, return_record, task_id))
            return {'task_id': task_id}
        result = review_repair._record_repair_task(
            task=task, developer=task['developer'], packet=packet,
            attempt=1, store=self.store, repair_task_id=repair_task_id,
            record_pm=record_pm)
        args, store, envelope, return_record, recorded_task_id = seen[0]
        self.assertIs(store, self.store)
        self.assertEqual(args.pm_session_id, task['pm_provenance']['pm_native_session_id'])
        self.assertEqual(args.pm_cwd, task['pm_provenance']['pm_cwd'])
        self.assertEqual(envelope['request'], f'REPAIR 1/2 of {TASK}')
        response = json.loads(envelope['response'])
        self.assertEqual(set(response), {'schema_version', 'summary', 'developer_assignment'})
        self.assertTrue(response['summary'].startswith('LLMTOOL_RUNTIME recorded bounded'))
        self.assertIn('no PM or provider decision was made', response['summary'])
        assignment = response['developer_assignment']
        self.assertEqual(assignment['route'], 'agy')
        self.assertEqual(assignment['goal'], task['pm_assignment']['goal'])
        self.assertIn('comment id 9', assignment['acceptance'][-1])
        self.assertIn(HEAD, assignment['acceptance'][-1])
        self.assertIn('findings count 1', assignment['acceptance'][-1])
        self.assertTrue(return_record)
        self.assertEqual(recorded_task_id, repair_task_id)
        self.assertEqual(result['task_id'], repair_task_id)

    def test_repair_task_rejects_recorder_without_task_id(self):
        task = _record()
        task['developer']['provider_profile'] = 'agy'   # #1502 WP-C: Codex writes no code
        packet = {'verdict_comment_id': 9, 'failed_head': HEAD, 'version': 1,
                  'findings_count': 1}

        def record_pm(args, *, store, input_stream, return_record, task_id=None):
            return {'task_id': None}

        with self.assertRaises(review_repair.LLMToolError) as raised:
            review_repair._record_repair_task(
                task=task, developer=task['developer'], packet=packet,
                attempt=1, store=self.store, repair_task_id='1' * 32,
                record_pm=record_pm)
        self.assertEqual(raised.exception.review_repair_code,
                         'REPAIR_TASK_RECORD_UNAVAILABLE')

    def test_cli_route_reaches_handler(self):
        with patch.object(task_submission_cli, 'run_task_repair', return_value=17) as handler:
            with patch('llmtool.source_freshness.require_fresh_source'):
                self.assertEqual(cli.main(['task', 'repair', TASK, '--executor-task', EXECUTOR,
                                           '--pr', str(PR)]), 17)
        handler.assert_called_once()

    def test_child_task_named_by_root_claim_refuses_without_writes_or_calls(self):
        child_id = '2' * 32
        child_executor = 'executor-child'
        root_record = _record()
        root_record['developer']['provider_profile'] = 'agy'   # #1502 WP-C: Codex writes no code
        root_record['review_repair_claims'] = [{
            'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': 1,
            'attempt': 1, 'family': f'pr-{PR}', 'state': 'DISPATCHED',
            'repair_task_id': child_id, 'comment_id': 9, 'packet_sha256': 'f' * 64,
            'claimed_at': '2026-10-27T01:00:00Z',
        }]
        child_record = _record()
        child_record['task_id'] = child_id
        child_record['run_id'] = '3' * 32
        child_record['completion']['executor_task_id'] = child_executor
        child_record['review_dispatch_claim'] = [{
            'executor_task': child_executor, 'pr': PR, 'head_sha': HEAD, 'version': 1,
            'state': 'DISPATCHED', 'reviewer_task': 'reviewer-d6',
            'native_session_id': 'review-session', 'account_mode': 'DEFAULT_SECOND_ACCOUNT',
            'claimed_at': '2026-10-27T00:00:00Z', 'updated_at': '2026-10-27T00:00:00Z',
        }]
        child_record['completion_events'] = [{
            'event_id': '4' * 32, 'source': 'REVIEWER', 'state': 'PENDING',
            'source_sha': HEAD, 'session_id': 'review-' + '4' * 32,
            'received_at': '2026-10-27T01:01:00Z',
        }]
        child_record['review_repair_claims'] = []

        # 1. Review packet flow
        self.store = TempTaskStore(self.temp.name, [root_record, child_record])
        recorded = []
        adapter_calls = []
        with self.assertRaises(LLMToolError) as caught:
            review_repair.repair_review(
                store=self.store, task_id=child_id, executor_task=child_executor,
                pr_number=PR, github_get=lambda _path: [_comment()],
                current_head=lambda _task, _executor: HEAD,
                dispatch_adapter=lambda **kwargs: adapter_calls.append(kwargs),
                repair_task_recorder=lambda **kwargs: recorded.append(kwargs))
        self.assertEqual(caught.exception.review_repair_code, 'REPAIR_ROOT_TASK_REQUIRED')
        self.assertEqual(self.store.get(child_id).get('review_repair_claims'), [])
        self.assertEqual(recorded, [])
        self.assertEqual(adapter_calls, [])

        # 2. EC2 receipt flow
        child_receipt = {
            'task_id': child_id, 'run_id': child_record['run_id'],
            'executor_task': child_executor, 'source_sha': HEAD,
            'result': 'FAIL', 'modules': ['test_remote'],
            'failure_excerpt': 'FAIL: test_remote\nAssertionError',
        }
        child_receipt['receipt_digest'] = hashlib.sha256(
            task_store._json_bytes(child_receipt)).hexdigest()
        self.store = TempTaskStore(self.temp.name, [root_record, child_record])
        recorded = []
        adapter_calls = []
        with self.assertRaises(LLMToolError) as caught:
            review_repair.repair_review(
                store=self.store, task_id=child_id, executor_task=child_executor,
                pr_number=PR, remote_test_receipt=child_receipt,
                current_head=lambda _task, _executor: HEAD,
                dispatch_adapter=lambda **kwargs: adapter_calls.append(kwargs),
                repair_task_recorder=lambda **kwargs: recorded.append(kwargs))
        self.assertEqual(caught.exception.review_repair_code, 'REPAIR_ROOT_TASK_REQUIRED')
        self.assertEqual(self.store.get(child_id).get('review_repair_claims'), [])
        self.assertEqual(recorded, [])
        self.assertEqual(adapter_calls, [])

    def test_root_task_replays_or_refuses_at_cap_as_before(self):
        child_id = '2' * 32
        root_record = _record()
        root_claim = {
            'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': 1,
            'attempt': 1, 'family': f'pr-{PR}', 'state': 'DISPATCHED',
            'repair_task_id': child_id, 'comment_id': 9,
            'packet_sha256': hashlib.sha256(json.dumps(
                {'executor_task': EXECUTOR, 'failed_head': HEAD, 'findings_count': 1,
                 'pr': PR, 'reviewer_text_excerpt_untrusted_data': _comment()['body'][:2000],
                 'task': TASK, 'verdict_comment_id': 9, 'version': 1},
                sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
            'claimed_at': '2026-10-27T01:00:00Z',
        }
        root_record['review_repair_claims'] = [root_claim]
        child_record = _record()
        child_record['task_id'] = child_id
        child_record['review_repair_claims'] = []

        # Root replays with child record present
        self.store = TempTaskStore(self.temp.name, [root_record, child_record])
        result = self.dispatch()
        self.assertTrue(result['replayed'])
        self.assertEqual(result['repair_task_id'], child_id)
        self.assertEqual(len(self.calls), 0)

        # Root at cap with child record present: a new valid FAIL splits (D14)
        capped_record = _record()
        capped_record['review_repair_claims'] = [
            self._stored_claim(attempt=n, repair_id=f'{n:032x}')
            for n in range(1, MAX + 1)
        ]
        self.store = TempTaskStore(self.temp.name, [capped_record, child_record])
        self._assert_refused('REPAIR_SPLIT_REQUIRED')

    def test_scan_task_ids_failure_gives_repair_count_unavailable_with_no_write(self):
        before = self.store.get(TASK).get('review_repair_claims')
        with patch.object(self.store, 'task_ids', side_effect=RuntimeError('disk error')):
            with self.assertRaises(LLMToolError) as caught:
                self.dispatch()
            self.assertEqual(caught.exception.review_repair_code, 'REPAIR_COUNT_UNAVAILABLE')
        self.assertEqual(self.store.get(TASK).get('review_repair_claims'), before)
        self.assertEqual(self.calls, [])

    def test_scan_unreadable_record_gives_repair_count_unavailable_with_no_write(self):
        before = self.store.get(TASK).get('review_repair_claims')
        # Record cannot be read (corrupt JSON decode failure)
        (self.store.root / 'other.json').write_text('{bad-json')
        with self.assertRaises(LLMToolError) as caught:
            self.dispatch()
        self.assertEqual(caught.exception.review_repair_code, 'REPAIR_COUNT_UNAVAILABLE')
        self.assertEqual(self.store.get(TASK).get('review_repair_claims'), before)
        self.assertEqual(self.calls, [])

        # Record is not a dict
        (self.store.root / 'other.json').write_text(json.dumps('not-a-dict'))
        with self.assertRaises(LLMToolError) as caught:
            self.dispatch()
        self.assertEqual(caught.exception.review_repair_code, 'REPAIR_COUNT_UNAVAILABLE')
        self.assertEqual(self.store.get(TASK).get('review_repair_claims'), before)
        self.assertEqual(self.calls, [])

    def test_malformed_claims_list_does_not_match(self):
        for bad_claims in ('not a list', 42, [None], ['string-item'],
                           [{'repair_task_id': TASK}, 'not-a-dict']):
            with self.subTest(bad_claims=bad_claims):
                other_record = _record()
                other_record['task_id'] = '9' * 32
                other_record['review_repair_claims'] = bad_claims
                self.store = TempTaskStore(self.temp.name, [_record(), other_record])
                calls_before = len(self.calls)
                result = self.dispatch()
                self.assertEqual(result['claim']['attempt'], 1)
                self.assertEqual(len(self.calls), calls_before + 1)

    # #1502 WP-D D14 (spec section 7): legacy Task data under the new limits.

    # Old per-Task cap and override keys; no gate reads them any more.
    _LEGACY_OVERRIDES = {
        'repair_cap_override': {'rounds': 9, 'recorded_at': '2026-10-27T00:00:00Z'},
        'repair_max': 9, 'max_repair_attempts': 9, 'test_fail_max': 9,
        'review_fail_max': 9,
    }

    def _legacy_record(self, claims, *, kind=None, overrides=False):
        """A fresh Task holding legacy repair claims, lineage and override fields."""
        record = _record()
        record['review_repair_claims'] = claims
        if kind is not None:
            record['lineage'] = {'kind': kind}
        if overrides:
            record.update(json.loads(json.dumps(self._LEGACY_OVERRIDES)))
            record['developer'].update(json.loads(json.dumps(self._LEGACY_OVERRIDES)))
        self.store = TempTaskStore(self.temp.name, record)

    def _claims_on_new_heads(self, count, *, ec2=False):
        """count stored claims, each on its own earlier HEAD (one valid FAIL each)."""
        family = dict(family=EC2_FAMILY, comment_id=None) if ec2 else {}
        return [self._stored_claim(attempt=n, repair_id=f'{n:032x}', head=str(n) * 40,
                                   **family)
                for n in range(1, count + 1)]

    def test_d14_unknown_claim_list_is_used_up(self):
        # A claim list that cannot be read is an UNKNOWN count: no review
        # repair and no test-failure repair, and nothing is recorded.
        for claims in ('UNKNOWN', None, 0, {'attempts': 0}):
            for receipt in (None, _receipt()):
                with self.subTest(claims=claims, test_failure=receipt is not None):
                    self._legacy_record(claims)
                    recorded = []
                    self._assert_refused(
                        'REPAIR_CLAIM_UNAVAILABLE', remote_test_receipt=receipt,
                        repair_task_recorder=self._recording_recorder(recorded))
                    self.assertEqual(recorded, [])
        self.assertEqual(self.calls, [])

    def test_d14_legacy_claim_without_a_readable_head_is_used_up(self):
        # Without a HEAD the valid FAIL count is UNKNOWN, which counts as
        # review_fail_max: the Task escalates by its kind instead of repairing,
        # even with a confirmed Debug on the failed HEAD.
        for kind, code in ((None, 'REPAIR_SPLIT_REQUIRED'),
                           ('root', 'REPAIR_SPLIT_REQUIRED'),
                           ('child', 'REPAIR_PM_DECIDE_REQUIRED'),
                           ('replan', 'REPAIR_FAILED_FINAL_REQUIRED')):
            for label, head in (('none', None), ('int', 5), ('missing', _MISSING)):
                for rca in (False, True):
                    legacy = self._stored_claim(attempt=1, repair_id='1' * 32)
                    if head is _MISSING:
                        del legacy['head_sha']
                    else:
                        legacy['head_sha'] = head
                    with self.subTest(kind=kind, head=label, rca=rca):
                        self._legacy_record([legacy], kind=kind)
                        if rca:
                            self._confirm_rca()
                        recorded = []
                        self._assert_refused(
                            code, repair_task_recorder=self._recording_recorder(recorded))
                        self.assertEqual(recorded, [])
        self.assertEqual(self.calls, [])

    def test_d14_claims_in_unknown_or_missing_state_still_count(self):
        policy = repair_policy.load_policy()
        for label, state in (('UNKNOWN', 'UNKNOWN'), ('none', None), ('missing', _MISSING)):
            for ec2, limit, receipt, code in (
                    (False, policy['repair_max'], None, 'REPAIR_SPLIT_REQUIRED'),
                    (True, policy['test_fail_max'], _receipt(), 'REPAIR_LIMIT_REACHED')):
                claims = self._claims_on_new_heads(limit, ec2=ec2)
                for claim in claims:
                    if state is _MISSING:
                        del claim['state']
                    else:
                        claim['state'] = state
                with self.subTest(state=label, test_failure=ec2):
                    self._legacy_record(claims)
                    self._assert_refused(code, remote_test_receipt=receipt)
        self.assertEqual(self.calls, [])

    def test_d14_unreadable_policy_gives_no_repair(self):
        for receipt in (None, _receipt()):
            with self.subTest(test_failure=receipt is not None), \
                    patch.object(repair_policy, 'load_policy',
                                 side_effect=LLMToolError('POLICY_INVALID')):
                self._assert_refused('REPAIR_POLICY_INVALID', remote_test_receipt=receipt)
        self.assertEqual(self.calls, [])

    def test_d14_legacy_override_fields_do_not_raise_the_limits(self):
        policy = repair_policy.load_policy()
        self._legacy_record(self._claims_on_new_heads(policy['repair_max']), overrides=True)
        self._confirm_rca()
        self._assert_refused('REPAIR_SPLIT_REQUIRED')
        self._legacy_record(self._claims_on_new_heads(policy['test_fail_max'], ec2=True),
                            overrides=True)
        self._assert_refused('REPAIR_LIMIT_REACHED', remote_test_receipt=_receipt())
        self.assertEqual(self.calls, [])

    def test_d14_legacy_override_fields_do_not_change_the_debug_step(self):
        self._legacy_record([self._stored_claim(attempt=1, repair_id='1' * 32,
                                                head='e' * 40, state='DISPATCHED')],
                            overrides=True)
        # Valid FAIL #2 still needs AI Debug first ...
        self._assert_refused('REPAIR_DEBUG_REQUIRED')
        # ... and a confirmed Debug still admits repair #2 as usual.
        self._confirm_rca()
        result = self.dispatch()
        self.assertEqual(result['claim']['attempt'], 2)
        self.assertEqual(len(self.calls), 1)

    def test_d14_task_over_the_new_limits_gets_no_further_repair(self):
        # No claim, no recorded Task, no provider call: the next valid FAIL
        # escalates by kind instead of repairing.
        policy = repair_policy.load_policy()
        for extra in (0, 1):
            for kind, code in ((None, 'REPAIR_SPLIT_REQUIRED'),
                               ('root', 'REPAIR_SPLIT_REQUIRED'),
                               ('child', 'REPAIR_PM_DECIDE_REQUIRED'),
                               ('replan', 'REPAIR_FAILED_FINAL_REQUIRED')):
                for rca in (False, True):
                    with self.subTest(extra=extra, kind=kind, rca=rca):
                        self._legacy_record(
                            self._claims_on_new_heads(policy['repair_max'] + extra), kind=kind)
                        if rca:
                            self._confirm_rca()
                        recorded = []
                        self._assert_refused(
                            code, repair_task_recorder=self._recording_recorder(recorded))
                        self.assertEqual(recorded, [])
            with self.subTest(extra=extra, test_failure=True):
                self._legacy_record(
                    self._claims_on_new_heads(policy['test_fail_max'] + extra, ec2=True))
                self._assert_refused('REPAIR_LIMIT_REACHED', remote_test_receipt=_receipt())
        self.assertEqual(self.calls, [])

    # D14 (3): _review_gate lets repair_policy.review_action decide before the
    # repair limit, so a Task already at or over repair_max is directed to its
    # escalation at its next valid FAIL.
    def _over_the_new_repair_limit(self, kind):
        self._legacy_record(
            self._claims_on_new_heads(repair_policy.load_policy()['repair_max']), kind=kind)

    def test_d14_root_over_the_new_limits_is_directed_to_split(self):
        self._over_the_new_repair_limit('root')
        self._assert_refused('REPAIR_SPLIT_REQUIRED')

    def test_d14_child_over_the_new_limits_is_directed_to_pm_decide(self):
        self._over_the_new_repair_limit('child')
        self._assert_refused('REPAIR_PM_DECIDE_REQUIRED')

    def test_d14_replan_over_the_new_limits_is_failed_final(self):
        self._over_the_new_repair_limit('replan')
        self._assert_refused('REPAIR_FAILED_FINAL_REQUIRED')

    def test_d14_at_limit_rereview_of_a_counted_head_is_limit_reached(self):
        # A re-review of a HEAD that already holds a repair claim adds no valid
        # FAIL (spec section 3-3): no escalation, no repair, for every kind.
        policy = repair_policy.load_policy()
        for kind in (None, 'root', 'child', 'replan'):
            for rca in (False, True):
                with self.subTest(kind=kind, rca=rca):
                    claims = self._claims_on_new_heads(policy['repair_max'])
                    claims[-1]['head_sha'] = HEAD
                    self._legacy_record(claims, kind=kind)
                    if rca:
                        self._confirm_rca()
                    self._assert_refused('REPAIR_LIMIT_REACHED')
        self.assertEqual(self.calls, [])


# Counts a legacy record may hold that are not a known non-negative int.
_UNKNOWN_COUNTS = (repair_policy.UNKNOWN, None, -1, '1', 1.5, True, [], {})


class LegacyPolicyGateTests(unittest.TestCase):
    """#1502 WP-D D14 (spec section 7): the repair_policy gates under legacy data."""

    def setUp(self):
        self.policy = repair_policy.load_policy()

    def test_new_limits(self):
        self.assertEqual((self.policy['repair_max'], self.policy['test_fail_max']), (2, 2))
        # FAIL #2 goes to Debug, so escalation by count starts after it.
        self.assertGreater(self.policy['review_fail_max'], 2)

    def test_unknown_review_fail_count_escalates_by_kind(self):
        for kind, expected in (('root', 'SPLIT'), ('child', 'PM_DECIDE'),
                               ('replan', 'FAILED_FINAL')):
            for value in _UNKNOWN_COUNTS:
                for debug_status in (None, 'RCA_CONFIRMED'):
                    with self.subTest(kind=kind, value=value, debug_status=debug_status):
                        self.assertEqual(repair_policy.review_action(
                            kind=kind, fail_count=value, debug_status=debug_status,
                            policy=self.policy), expected)

    def test_unknown_repair_count_admits_no_repair(self):
        self.assertIsNone(repair_policy.admit_repair(
            repair_count=0, rca_confirmed=False, policy=self.policy))
        for value in _UNKNOWN_COUNTS:
            for rca in (False, True):
                with self.subTest(value=value, rca=rca):
                    self.assertEqual(repair_policy.admit_repair(
                        repair_count=value, rca_confirmed=rca, policy=self.policy),
                        'REPAIR_LIMIT_REACHED')

    def test_unknown_test_fail_count_stops(self):
        self.assertEqual(repair_policy.test_failure_action(
            test_fail_count=0, policy=self.policy), 'REPAIR')
        for value in _UNKNOWN_COUNTS:
            with self.subTest(value=value):
                self.assertEqual(repair_policy.test_failure_action(
                    test_fail_count=value, policy=self.policy), 'STOPPED')

    def test_unknown_debug_count_is_used(self):
        for kind, keyword, code in (('root', 'parent_debug_used', 'DEBUG_PARENT_USED'),
                                    ('replan', 'parent_debug_used', 'DEBUG_PARENT_USED'),
                                    ('child', 'group_debug_used', 'DEBUG_GROUP_USED')):
            self.assertIsNone(repair_policy.admit_debug(kind=kind, policy=self.policy))
            for value in _UNKNOWN_COUNTS:
                with self.subTest(kind=kind, value=value):
                    self.assertEqual(repair_policy.admit_debug(
                        kind=kind, policy=self.policy, **{keyword: value}), code)
        # A child at valid FAIL #2 whose shared Debug count is UNKNOWN goes to PM_DECIDE.
        self.assertEqual(repair_policy.review_action(
            kind='child', fail_count=2, policy=self.policy), 'DEBUG')
        for value in _UNKNOWN_COUNTS:
            with self.subTest(child_group_debug=value):
                self.assertEqual(repair_policy.review_action(
                    kind='child', fail_count=2, group_debug_used=value,
                    policy=self.policy), 'PM_DECIDE')

    def test_legacy_override_field_is_ignored_by_the_gates(self):
        p = self.policy
        legacy = dict(p, repair_cap_override={'rounds': 9}, max_repair_attempts=9)
        self.assertEqual(repair_policy.admit_repair(
            repair_count=p['repair_max'], rca_confirmed=True, policy=legacy),
            'REPAIR_LIMIT_REACHED')
        self.assertEqual(repair_policy.test_failure_action(
            test_fail_count=p['test_fail_max'], policy=legacy), 'STOPPED')
        self.assertEqual(repair_policy.review_action(
            kind='root', fail_count=p['review_fail_max'], policy=legacy), 'SPLIT')
        self.assertEqual(repair_policy.admit_debug(
            kind='root', parent_debug_used=p['parent_debug_max'], policy=legacy),
            'DEBUG_PARENT_USED')
        # No gate takes a per-call override.
        for call in (lambda: repair_policy.review_action(
                         kind='root', fail_count=1, policy=p, repair_max=9),
                     lambda: repair_policy.admit_repair(
                         repair_count=0, rca_confirmed=False, policy=p, override=9),
                     lambda: repair_policy.test_failure_action(
                         test_fail_count=0, policy=p, test_fail_max=9)):
            with self.assertRaises(TypeError):
                call()

    def test_policy_file_with_a_legacy_override_key_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            control = Path(temp) / 'policy.json'
            control.write_text(json.dumps(self.policy), encoding='utf-8')
            self.assertEqual(repair_policy.load_policy(control), self.policy)
            for extra in ('repair_cap_override', 'max_repair_attempts'):
                path = Path(temp) / f'{extra}.json'
                path.write_text(json.dumps(dict(self.policy, **{extra: 9})), encoding='utf-8')
                with self.subTest(extra=extra), \
                        self.assertRaisesRegex(LLMToolError, 'POLICY_INVALID'):
                    repair_policy.load_policy(path)

    def test_task_over_the_new_limits_escalates_at_its_next_valid_fail(self):
        p = self.policy
        for over in (0, 1, 5):
            for kind, expected in (('root', 'SPLIT'), ('child', 'PM_DECIDE'),
                                   ('replan', 'FAILED_FINAL')):
                for debug_status in (None, 'RCA_CONFIRMED', 'RCA_UNCONFIRMED'):
                    with self.subTest(over=over, kind=kind, debug_status=debug_status):
                        self.assertEqual(repair_policy.review_action(
                            kind=kind, fail_count=p['review_fail_max'] + over,
                            debug_status=debug_status, policy=p), expected)
            with self.subTest(over=over, gate='counts'):
                self.assertEqual(repair_policy.admit_repair(
                    repair_count=p['repair_max'] + over, rca_confirmed=True, policy=p),
                    'REPAIR_LIMIT_REACHED')
                self.assertEqual(repair_policy.test_failure_action(
                    test_fail_count=p['test_fail_max'] + over, policy=p), 'STOPPED')
                self.assertEqual(repair_policy.admit_debug(
                    kind='root', parent_debug_used=p['parent_debug_max'] + over, policy=p),
                    'DEBUG_PARENT_USED')
                self.assertEqual(repair_policy.admit_debug(
                    kind='child', group_debug_used=p['group_debug_max'] + over, policy=p),
                    'DEBUG_GROUP_USED')
        # Over the Debug limit at valid FAIL #2: an unconfirmed Debug, or a
        # child lineage whose shared Debug is used, escalates instead of repairing.
        for kind, expected in (('root', 'SPLIT'), ('child', 'PM_DECIDE'),
                               ('replan', 'FAILED_FINAL')):
            self.assertEqual(repair_policy.review_action(
                kind=kind, fail_count=2, debug_status='RCA_UNCONFIRMED', policy=p), expected)
        self.assertEqual(repair_policy.review_action(
            kind='child', fail_count=2, group_debug_used=p['group_debug_max'], policy=p),
            'PM_DECIDE')


if __name__ == '__main__':
    unittest.main()
