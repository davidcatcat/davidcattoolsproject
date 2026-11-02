import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from llmtool import cli, review_repair, run_budget, task_submission_cli
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


class TempTaskStore:
    """Small persistent TaskStore fake preserving compare-and-update semantics."""

    def __init__(self, root, record):
        self.path = Path(root) / 'task.json'
        self.path.write_text(json.dumps(record))

    def get(self, task_id):
        return json.loads(self.path.read_text())

    def compare_and_update(self, task_id, *, expected, **changes):
        record = self.get(task_id)
        if any(record.get(key) != value for key, value in expected.items()):
            raise RuntimeError('conflict')
        record.update(changes)
        record['record_sha256'] = hashlib.sha256(json.dumps(
            {key: value for key, value in record.items() if key != 'record_sha256'},
            sort_keys=True).encode()).hexdigest()
        self.path.write_text(json.dumps(record))
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
        'developer': {'assignment_id': 'f' * 32, 'provider_profile': 'codex',
                      'model': 'gpt-test', 'owner_assignment_ref': None,
                      'selection_source': 'PM', 'read_paths': ['tools/llmtool/llmtool'],
                      'write_paths': ['tools/llmtool/llmtool/review_repair.py'],
                      'acceptance': ['Repair the reported issue.'],
                      'egress_basis': 'Task authorized source files only.',
                      'state': 'DEVELOPER_RESPONDED'},
        'completion': {'executor_task_id': EXECUTOR, 'evidence': {'common_dir': '/tmp/git'}},
        'pm_assignment': {'route': 'codex', 'risk': 'LOW', 'goal': 'Repair the issue.',
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
        result = review_repair._delegate_repair(
            task=task, developer=developer, packet={'verdict_comment_id': 9,
                'failed_head': HEAD, 'version': 1, 'findings_count': 1,
                'reviewer_text_excerpt_untrusted_data': 'Fix the reported issue.'},
            executor_task=EXECUTOR, delegate=lambda args, store: captured.append(args) or 0,
            store=object())
        self.assertEqual(result, 0)
        self.assertEqual(captured[0].provider, 'agy')
        self.assertEqual(captured[0].mode, 'direct')
        self.assertEqual(captured[0].owner_ref, developer['owner_assignment_ref'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TempTaskStore(self.temp.name, _record())
        self.calls = []
        self.runtime_path_patch = patch.object(
            review_repair.shutil, 'which', return_value='/usr/bin/codex')
        self.runtime_path_patch.start()

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
        with patch.object(review_repair.worker_dispatch, 'evaluate',
                          return_value=decision), \
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

    def test_remote_test_fail_receipt_enters_real_repair_path(self):
        receipt = {
            'task_id': TASK, 'run_id': RUN, 'executor_task': EXECUTOR,
            'source_sha': HEAD, 'result': 'FAIL', 'modules': ['test_remote'],
            'failure_excerpt': 'FAIL: test_remote\nAssertionError',
        }
        receipt['receipt_digest'] = hashlib.sha256(json.dumps(
            receipt, ensure_ascii=True, sort_keys=True,
            separators=(',', ':')).encode('utf-8')).hexdigest()
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
        self.assertEqual(packet['reviewer_text_excerpt_untrusted_data'],
                         'FAIL: test_remote\nAssertionError')
        self.assertEqual(task_submission_cli._validate_repair_packet(packet), packet)
        claim = self.store.get(TASK)['review_repair_claims'][0]
        self.assertIsNone(claim['comment_id'])
        self.assertEqual(claim['version'], 1)

    def test_remote_test_repair_claim_passes_real_task_store_validation(self):
        # Use the production record-pm path, then attach the same executor
        # fixture shape used by the active-PM tests before the real store check.
        from test_active_pm_inbox import ActivePMInboxTests

        fixture = ActivePMInboxTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.store = TaskStore(Path(self.temp.name).resolve())
        response = json.dumps({'schema_version': 1, 'summary': 'Active PM plan.',
            'developer_assignment': {'route': 'codex', 'risk': 'LOW',
                'goal': 'Repair the reported issue.',
                'acceptance': ['Repair the reported issue.']}})
        with redirect_stdout(StringIO()):
            task = task_submission_cli.run_task_record_pm(
                Namespace(stdin_json=True, pm_session_id=fixture.session,
                          pm_runtime='claude-code', pm_cwd=str(fixture.pm_cwd)),
                store=self.store,
                input_stream=StringIO(json.dumps({
                    'request': 'Bounded non-sensitive remote-test repair fixture.',
                    'response': response})),
                return_record=True, task_id=TASK)
        template = fixture._task()
        completion = json.loads(json.dumps(template['completion']))
        completion['evidence'].update(
            task_id=task['task_id'], run_id=task['run_id'],
            contract_digest=task['task_binding']['contract_digest'])
        task.update(developer=template['developer'], completion=completion)
        self.store.update(TASK, developer=task['developer'], completion=completion)
        receipt = {
            'task_id': TASK, 'run_id': RUN, 'executor_task': EXECUTOR,
            'source_sha': HEAD, 'result': 'FAIL', 'modules': ['test_remote'],
            'failure_excerpt': 'FAIL: test_remote\nAssertionError',
        }
        receipt['receipt_digest'] = hashlib.sha256(json.dumps(
            receipt, ensure_ascii=True, sort_keys=True,
            separators=(',', ':')).encode('utf-8')).hexdigest()
        result = self.dispatch(
            remote_test_receipt=receipt,
            repair_task_recorder=lambda **request: {
                'task_id': request['repair_task_id'],
                'pm_assignment': {'acceptance': ['Repair the reported issue.']},
            })
        claim = self.store.get(TASK)['review_repair_claims'][0]
        self.assertEqual(claim['version'], 1)
        self.assertIsNone(claim['comment_id'])
        self.assertEqual(result['claim']['state'], 'DISPATCHED')

    def test_replay_after_restart_does_not_dispatch_twice(self):
        self.dispatch()
        restarted = TempTaskStore(self.temp.name, self.store.get(TASK))
        self.store = restarted
        result = self.dispatch()
        self.assertTrue(result['replayed'])
        self.assertEqual(result['repair_task_id'],
                         self.store.get(TASK)['review_repair_claims'][0]['repair_task_id'])
        self.assertEqual(len(self.calls), 1)

    def test_failed_claim_starts_a_new_repair_attempt(self):
        self.dispatch()
        record = self.store.get(TASK)
        record['review_repair_claims'][0]['state'] = 'FAILED'
        self.store = TempTaskStore(self.temp.name, record)
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
        retry_id = self.store.get(TASK)['review_repair_claims'][-1]['repair_task_id']
        result = self.dispatch(family='network', retry_claim_id=retry_id,
                               github_get=lambda _path: [_comment(version=2)])
        claims = self.store.get(TASK)['review_repair_claims']
        self.assertEqual(result['claim']['attempt'], 2)
        self.assertEqual([claim['state'] for claim in claims], ['FAILED', 'DISPATCHED'])

    def test_third_attempt_without_report_is_refused_at_limit(self):
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
                         'REPAIR_LIMIT_REACHED')

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

    def test_missing_provider_runtime_refused_before_claim(self):
        recorded = []
        with self.assertRaisesRegex(Exception, 'refused') as raised:
            self.dispatch(runtime_path=None,
                repair_task_recorder=lambda **kwargs: recorded.append(kwargs))
        self.assertEqual(raised.exception.review_repair_code, 'RUNTIME_NOT_ON_PATH')
        self.assertEqual(recorded, [])
        self.assertNotIn('review_repair_claims', self.store.get(TASK))

    def test_failed_recheck_is_resumable_without_claiming_replay(self):
        decisions = iter([type('D', (), {'allowed': True})(),
                          type('D', (), {'allowed': False, 'reason_code': 'PAUSED'})()])
        with patch.object(review_repair.worker_dispatch, 'evaluate',
                          side_effect=lambda *a, **k: next(decisions)):
            with self.assertRaisesRegex(Exception, 'refused'):
                review_repair.repair_review(
                    store=self.store, task_id=TASK, executor_task=EXECUTOR,
                    pr_number=PR, github_get=lambda _path: [_comment()],
                    current_head=lambda _task, _executor: HEAD,
                    dispatch_adapter=lambda **_kwargs: self.fail('must not dispatch'),
                    repair_task_recorder=lambda **kwargs: {'task_id': kwargs['repair_task_id']})
        result = self.dispatch()
        self.assertFalse(result['replayed'])
        self.assertTrue(result['next_action'].startswith('task delegate '))
        self.assertEqual(self.calls, [])

    def test_claim_cas_failure_does_not_create_repair_task(self):
        class ConflictStore(TempTaskStore):
            def compare_and_update(self, *args, **kwargs):
                raise RuntimeError('conflict')
        record = self.store.get(TASK)
        self.store = ConflictStore(self.temp.name, record)
        recorded = []
        with self.assertRaisesRegex(Exception, 'refused'):
            self.dispatch(repair_task_recorder=lambda **kwargs: recorded.append(kwargs))
        self.assertEqual(recorded, [])

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
                          return_value=type('Decision', (), {'allowed': True})()):
            result = review_repair.repair_review(
                store=self.store, task_id=TASK, executor_task=EXECUTOR,
                pr_number=PR, github_get=lambda _path: [_comment()],
                current_head=lambda _task, _executor: HEAD,
                delegate_callable=lambda args, store: seen.append((args, store)) or 0,
                repair_task_recorder=recorder)
        args, passed_store = seen[0]
        self.assertIs(passed_store, self.store)
        self.assertEqual((args.provider, args.mode, args.executor_task),
                         ('codex', 'direct', EXECUTOR))
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
        self.assertEqual(assignment['route'], 'codex')
        self.assertEqual(assignment['goal'], task['pm_assignment']['goal'])
        self.assertIn('comment id 9', assignment['acceptance'][-1])
        self.assertIn(HEAD, assignment['acceptance'][-1])
        self.assertIn('findings count 1', assignment['acceptance'][-1])
        self.assertTrue(return_record)
        self.assertEqual(recorded_task_id, repair_task_id)
        self.assertEqual(result['task_id'], repair_task_id)

    def test_repair_task_rejects_recorder_without_task_id(self):
        task = _record()
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


if __name__ == '__main__':
    unittest.main()
