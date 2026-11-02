import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from llmtool import task_advance
from llmtool import task_submission_cli
from llmtool.cli import main, parser_for_cli


HEAD = 'a' * 40
PR = {'number': 17, 'head': {'sha': HEAD}, 'merged': False}


def base_record(**changes):
    record = {
        'task_id': '1' * 32, 'run_id': '2' * 32,
        'developer': {'state': 'DEVELOPER_RESPONDED'},
        'completion': {'executor_task_id': 'executor-1', 'state': 'WAITING_REVIEW',
                      'evidence': {'result': {'result_head': HEAD}}},
        'review_dispatch_claim': [], 'review_repair_claims': [],
        'completion_events': [],
    }
    record.update(changes)
    return record


def d6(clean=True, recorded=HEAD):
    return {'root': '/repo/task', 'result_head': HEAD,
            'recorded_result_head': recorded, 'registered_result_head': recorded,
            'dirty': not clean}


class TaskAdvanceTests(unittest.TestCase):
    def _completed_codex_review(self, verdict, *, recorded=True):
        task_id, run_id, contract_digest = '1' * 32, '2' * 32, 'd' * 64
        body = (f'<!-- activity: type=review_verdict actor=Codex target=PM status={verdict} -->\n'
                f'Verdict: {verdict} | Version: v1 | HEAD: {HEAD}\n')
        if verdict == 'PASS':
            body += 'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n'
        body += (f'LLMTOOL-REVIEW-BINDING v1 task={task_id} run={run_id} '
                 f'contract={contract_digest} head={HEAD}\n')
        claim = {
            'executor_task': 'executor-1', 'pr': 17, 'head_sha': HEAD,
            'version': 1, 'state': 'COMPLETED', 'updated_at': '2026-10-28T00:00:00Z',
            'review_result': {
                'task_id': task_id, 'run_id': run_id,
                'contract_digest': contract_digest, 'head': HEAD,
                'verdict': verdict, 'review_text': body,
            },
        }
        record = base_record(
            schema_version=2, pm_session_id='pm-session',
            task_binding={'contract_digest': contract_digest},
            developer={'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'claude'},
            review_dispatch_claim=[claim])
        if recorded:
            outcome = 'REVIEW_PASS' if verdict == 'PASS' else 'REVIEW_FAIL'
            identity = {'task': task_id, 'run': run_id, 'head': HEAD,
                        'version': 1, 'outcome': outcome, 'comment_id': 0}
            event_id = hashlib.sha256(json.dumps(
                identity, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()[:32]
            record['completion_events'] = [{
                'event_id': event_id, 'source': 'REVIEWER', 'source_sha': HEAD,
                'version': 1, 'outcome': outcome,
            }]
        return record, claim

    def test_cli_route(self):
        parsed = parser_for_cli().parse_args(['task', 'advance', '1' * 32, '--pr', '17'])
        self.assertEqual(parsed.action, 'advance')
        with patch('llmtool.task_submission_cli.run_task_advance', return_value=0) as handler:
            self.assertEqual(main(['task', 'advance', '1' * 32, '--pr', '17']), 0)
        handler.assert_called_once()

    def test_claude_result_route_and_advance(self):
        parsed = parser_for_cli().parse_args([
            'task', 'claude-result', '1' * 32, '--executor-task', 'executor-1',
            '--head', HEAD, '--pm-session', 'pm-session'])
        self.assertEqual(parsed.action, 'claude-result')
        with patch('llmtool.task_submission_cli.run_task_claude_result', return_value=0) as handler:
            self.assertEqual(main([
                'task', 'claude-result', '1' * 32, '--executor-task', 'executor-1',
                '--head', HEAD, '--pm-session', 'pm-session']), 0)
        handler.assert_called_once()
        with self.assertRaises(SystemExit):
            parser_for_cli().parse_args([
                'task', 'claude-result', '1' * 32, '--executor-task', 'executor-1',
                '--head', HEAD])
        record = base_record(developer={
            'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'claude',
            'claude_result': {'executor_task_id': 'executor-1', 'head_sha': HEAD,
                              'recorded_at': '2026-10-29T00:00:00Z'},
        })
        self.assertEqual(task_advance.decide(record, d6(), PR, {}), 'DISPATCH_REVIEW')

    def test_developer_attention_and_publish_gates(self):
        blocked = base_record(developer={'state': 'DEVELOPER_OUTCOME_UNKNOWN'})
        self.assertEqual(task_advance.decide(blocked, d6(), PR, {}), 'DEVELOPER_ATTENTION')
        self.assertEqual(task_advance.decide(base_record(), d6(False), PR, {}), 'PUBLISH')
        self.assertEqual(task_advance.decide(base_record(), d6(recorded=None), PR, {}), 'PUBLISH')
        self.assertEqual(task_advance.decide(base_record(), d6(), None, {}), 'PUBLISH')
        self.assertEqual(task_advance.decide(base_record(), d6(),
            {'number': 17, 'head': {'sha': 'b' * 40}}, {}), 'PUBLISH')

    def test_advance_executes_publish_and_returns_only_bounded_fields(self):
        record = base_record(developer={'state': 'DEVELOPER_RESPONDED',
                                        'write_paths': ['allowed.txt']})
        store = type('S', (), {'get': lambda self, _id: record})()
        binding = {'root': '/d6', 'common_dir': '/canonical/.git',
                   'branch': 'feat/task', 'task_id': '3' * 32, 'result_head': HEAD,
                   'recorded_result_head': None, 'dirty': True, 'state': 'DIRTY'}
        published = {'pr': 17, 'head': HEAD}
        with patch.object(task_advance, '_d6_snapshot', return_value=binding), \
             patch.object(task_advance, '_review_outcomes', return_value=[]), \
             patch.object(task_advance, '_publish_task', return_value=published) as publish:
            result = task_advance.advance(task_id=record['task_id'], store=store,
                publish_git_runner=lambda *_args: None,
                publish_gh_runner=lambda *_args: None,
                record_result_call=lambda **_kwargs: None)
        publish.assert_called_once()
        self.assertEqual(result, {'task_id': record['task_id'], 'codes': [],
                                  'pr': 17, 'head': HEAD})

    def test_dispatch_unknown_result_and_review_outcomes(self):
        self.assertEqual(task_advance.decide(base_record(), d6(), PR, {}), 'DISPATCH_REVIEW')
        claim = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'CLAIMED'}
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]), d6(), PR, {}),
                         'REVIEW_OUTCOME_UNKNOWN')
        claim['state'] = 'DISPATCHED'
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]), d6(), PR, {}),
                         'RECORD_REVIEW_RESULT')
        claim['state'] = 'COMPLETED'
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]), d6(), PR, {}),
                         'RECORD_REVIEW_RESULT')
        related = {'events': [{'source': 'REVIEWER', 'source_sha': HEAD,
                               'version': 1, 'outcome': 'REVIEW_BLOCKED'}]}
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]), d6(), PR, related),
                         'REVIEWER_ATTENTION')
        related['events'][0]['outcome'] = 'REVIEW_FAIL'
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]), d6(), PR, related),
                         'REPAIR_REVIEW')
        related['repair_claims'] = [{'head_sha': HEAD, 'state': 'DISPATCHED',
                                     'repair_state': 'RUNNING'}]
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]), d6(), PR, related),
                         'WAITING_REPAIR')
        related['repair_claims'][0]['repair_state'] = 'UNAVAILABLE'
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]), d6(), PR, related),
                         'REPAIR_RETRY')
        related['repair_claims'][0]['repair_state'] = 'BLOCKED'
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]), d6(), PR, related),
                         'REPAIR_RETRY')
        related['repair_claims'][0]['repair_state'] = 'OUTPUT_REJECTED'
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]), d6(), PR, related),
                         'REPAIR_RETRY')
        related['repair_claims'][0]['repair_state'] = 'UNKNOWN'
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]), d6(), PR, related),
                         'REPAIR_OUTCOME_UNKNOWN')
        related['repair_claims'][0]['repair_state'] = 'RECORD_UNAVAILABLE'
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]), d6(), PR, related),
                         'DEVELOPER_ATTENTION')
        related['repair_claims'][0]['repair_state'] = 'RESPONDED'
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]), d6(), PR, related),
                         'REPAIR_RESULT_NOT_PUBLISHED')
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]), d6(False), PR, related),
                         'PUBLISH')
        new_head = 'b' * 40
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]),
            {'root': '/repo/task', 'result_head': new_head, 'recorded_result_head': new_head,
             'dirty': False}, {'number': 17, 'head': {'sha': new_head}},
            {'events': [{'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                         'outcome': 'REVIEW_FAIL'}], 'repair_claims': []}), 'DISPATCH_REVIEW')

    def test_completed_codex_pass_with_recorded_event_reaches_integration(self):
        record, claim = self._completed_codex_review('PASS')
        outcomes = task_advance._review_outcomes(
            record, [claim], lambda _path: self.fail('completed review must not fetch GitHub'))
        self.assertEqual(outcomes, [{'source': 'REVIEWER', 'source_sha': HEAD,
                                     'version': 1, 'outcome': 'REVIEW_PASS'}])
        record['completion'] = {'state': 'PM_EVIDENCE_READY', 'evidence': {
            'result': {'result_head': HEAD}, 'review_pr_ci': {'stage': 'READY'}}}
        self.assertEqual(task_advance.decide(record, d6(), PR, {'events': outcomes}),
                         'READY_FOR_INTEGRATION')

    def test_completed_codex_fail_with_recorded_event_requests_repair(self):
        record, claim = self._completed_codex_review('FAIL')
        outcomes = task_advance._review_outcomes(
            record, [claim], lambda _path: self.fail('completed review must not fetch GitHub'))
        self.assertEqual(outcomes, [{'source': 'REVIEWER', 'source_sha': HEAD,
                                     'version': 1, 'outcome': 'REVIEW_FAIL'}])
        self.assertEqual(task_advance.decide(record, d6(), PR, {'events': outcomes}),
                         'REPAIR_REVIEW')

    def test_completed_codex_review_without_event_requests_recording(self):
        record, claim = self._completed_codex_review('FAIL', recorded=False)
        outcomes = task_advance._review_outcomes(
            record, [claim], lambda _path: self.fail('completed review must not fetch GitHub'))
        self.assertEqual(outcomes, [])
        self.assertEqual(task_advance.decide(record, d6(), PR, {'events': outcomes}),
                         'RECORD_REVIEW_RESULT')

    def test_tampered_completed_codex_review_text_produces_no_outcome(self):
        record, claim = self._completed_codex_review('FAIL')
        claim['review_result']['review_text'] = claim['review_result']['review_text'].replace(
            f'head={HEAD}', f"head={'b' * 40}")
        outcomes = task_advance._review_outcomes(
            record, [claim], lambda _path: self.fail('completed review must not fetch GitHub'))
        self.assertEqual(outcomes, [])

    def test_repair_record_states_are_derived_for_waiting_and_retry(self):
        expected = (
            ({'state': 'PM_PENDING'}, 'PENDING', 'WAITING_REPAIR'),
            ({'developer': {'state': 'DEVELOPER_PENDING'}}, 'PENDING', 'WAITING_REPAIR'),
            ({'developer': {'state': 'DEVELOPER_DISPATCHING'}}, 'RUNNING', 'WAITING_REPAIR'),
            ({'developer': {'state': 'DEVELOPER_UNAVAILABLE'}}, 'UNAVAILABLE', 'REPAIR_RETRY'),
            ({'developer': {'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                            'error_code': 'CODEX_BLOCKED'}}, 'BLOCKED', 'REPAIR_RETRY'),
            ({'developer': {'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                            'execution': {'status': 'BLOCKED'}},
              'error_code': 'CODEX_BLOCKED'}, 'BLOCKED', 'REPAIR_RETRY'),
            ({'developer': {'state': 'DEVELOPER_OUTCOME_UNKNOWN'}}, 'UNKNOWN',
             'REPAIR_OUTCOME_UNKNOWN'),
            ({'developer': {'state': 'DEVELOPER_RESPONDED'}}, 'RESPONDED',
             'REPAIR_RESULT_NOT_PUBLISHED'),
            ({'developer': {'state': 'DEVELOPER_OUTPUT_REJECTED'}},
             'OUTPUT_REJECTED', 'REPAIR_RETRY'),
        )
        review_claim = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'DISPATCHED'}
        for repair_record, derived, action in expected:
            with self.subTest(repair_record=repair_record):
                self.assertEqual(task_advance.derive_repair_state(repair_record), derived)
                related = {
                    'events': [{'source': 'REVIEWER', 'source_sha': HEAD,
                                'version': 1, 'outcome': 'REVIEW_FAIL'}],
                    'repair_claims': [{'head_sha': HEAD, 'state': 'DISPATCHED',
                                      'repair_state': derived}],
                }
                self.assertEqual(task_advance.decide(
                    base_record(review_dispatch_claim=[review_claim]), d6(), PR, related), action)

    def test_missing_repair_task_record_is_developer_attention_with_specific_code(self):
        class Store:
            record = base_record(
                review_dispatch_claim=[{'pr': 17, 'head_sha': HEAD, 'version': 1,
                                        'state': 'DISPATCHED'}],
                review_repair_claims=[{'head_sha': HEAD, 'state': 'DISPATCHED',
                                       'repair_task_id': '3' * 32}])
            def __init__(self, unreadable=False):
                self.unreadable = unreadable
                self.writes = 0
            def get(self, task_id):
                if task_id == '3' * 32:
                    if self.unreadable:
                        raise OSError('unreadable')
                    return None
                return self.record
            def compare_and_update(self, *_args, **_kwargs):
                self.writes += 1

        for unreadable in (False, True):
            with self.subTest(unreadable=unreadable):
                store = Store(unreadable=unreadable)
                with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
                     patch.object(task_advance, '_pr_snapshot', return_value=PR), \
                     patch.object(task_advance, '_review_outcomes', return_value=[{
                         'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                         'outcome': 'REVIEW_FAIL'}]):
                    result = task_advance.advance(task_id=store.record['task_id'], store=store,
                        pr_number=17, github_get=lambda _path: [])
                self.assertEqual(result['step'], 'DEVELOPER_ATTENTION')
                self.assertEqual(result['codes'], ['REPAIR_TASK_RECORD_UNAVAILABLE'])
                self.assertEqual(store.writes, 0)

    def test_nonblocked_unknown_repair_has_specific_actionable_code(self):
        class Store:
            record = base_record(
                review_dispatch_claim=[{'pr': 17, 'head_sha': HEAD, 'version': 1,
                                        'state': 'DISPATCHED'}],
                review_repair_claims=[{'head_sha': HEAD, 'state': 'DISPATCHED',
                                       'repair_task_id': '3' * 32}])
            def get(self, task_id):
                if task_id == '3' * 32:
                    return {'developer': {'state': 'DEVELOPER_OUTCOME_UNKNOWN'}}
                return self.record

        store = Store()
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[{
                 'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_FAIL'}]):
            result = task_advance.advance(task_id=store.record['task_id'], store=store,
                pr_number=17, github_get=lambda _path: [])
        self.assertEqual(result['step'], 'REPAIR_OUTCOME_UNKNOWN')
        self.assertEqual(result['codes'], ['REPAIR_OUTCOME_UNKNOWN'])

    def test_pass_retries_completion_and_reaches_integration(self):
        claim = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'DISPATCHED'}
        related = {'events': [{'source': 'REVIEWER', 'source_sha': HEAD,
                               'version': 1, 'outcome': 'REVIEW_PASS'}]}
        record = base_record(review_dispatch_claim=[claim])
        self.assertEqual(task_advance.decide(record, d6(), PR, related), 'RUN_TASK_COMPLETE')
        record['completion'] = {'state': 'PM_EVIDENCE_READY', 'evidence': {
            'result': {'result_head': HEAD}, 'review_pr_ci': {'stage': 'READY'}}}
        self.assertEqual(task_advance.decide(record, d6(), PR, related), 'READY_FOR_INTEGRATION')

    def test_restart_does_not_dispatch_twice(self):
        class Store:
            def __init__(self):
                self.record = base_record()
            def get(self, _task_id):
                return self.record

        store = Store()
        dispatched = []
        def dispatch(**kwargs):
            dispatched.append(kwargs['version'])
            store.record['review_dispatch_claim'] = [{
                'pr': 17, 'head_sha': HEAD, 'version': kwargs['version'],
                'state': 'DISPATCHED', 'reviewer_task': 'reviewer-1'}]
            return store.record['review_dispatch_claim'][0]
        def review_result(**kwargs):
            return {'state': 'NOT_READY'}
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR):
            first = task_advance.advance(task_id=store.record['task_id'], store=store,
                pr_number=17, dispatch_review_call=dispatch, github_get=lambda _url: [])
            second = task_advance.advance(task_id=store.record['task_id'], store=store,
                pr_number=17, record_review_result_call=review_result, github_get=lambda _url: [])
        self.assertEqual(first['step'], 'DISPATCH_REVIEW')
        self.assertEqual(second['step'], 'RECORD_REVIEW_RESULT')
        self.assertEqual(dispatched, [1])

    def _advance_dispatched_review(self, *, claim_changes=None, reviewer_session_done=False,
                                   outcomes=None, completion=None):
        claim = {'executor_task': 'executor-1', 'pr': 17, 'head_sha': HEAD,
                 'version': 1, 'state': 'DISPATCHED',
                 'account_mode': 'DEFAULT_SECOND_ACCOUNT',
                 'claimed_at': '2026-10-27T00:00:00Z', 'updated_at': '2026-10-27T00:00:01Z',
                 'reviewer_task': 'reviewer-1', 'native_session_id': 'session-1'}
        claim.update(claim_changes or {})
        record = base_record(review_dispatch_claim=[claim])
        if completion is not None:
            record['completion'] = completion
        class Store:
            def get(self, _task_id):
                return record
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=outcomes or []):
            result = task_advance.advance(task_id=record['task_id'], store=Store(),
                pr_number=17, reviewer_session_done=reviewer_session_done,
                record_review_result_call=lambda **_kwargs: {'state': 'NOT_READY'},
                github_get=lambda _url: [])
        return result

    def test_done_reviewer_without_verdict_is_actionable(self):
        result = self._advance_dispatched_review(reviewer_session_done=True)
        self.assertEqual(result['step'], 'REVIEW_NOT_POSTED')
        self.assertEqual(result['next'], 'resume the reviewer to post its verdict')
        self.assertEqual(result['result'], {'reviewer_task': 'reviewer-1',
                                            'reviewer_session_id': 'session-1',
                                            'pr': 17, 'head_sha': HEAD, 'version': 1})
        self.assertEqual(result['codes'], ['REVIEW_VERDICT_NOT_POSTED'])

    def test_done_reviewer_with_persisted_missing_verdict_remains_actionable(self):
        result = self._advance_dispatched_review(reviewer_session_done=True, outcomes=[{
            'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
            'outcome': 'REVIEW_BLOCKED'}])
        self.assertEqual(result['step'], 'REVIEW_NOT_POSTED')
        self.assertEqual(result['next'], 'resume the reviewer to post its verdict')
        self.assertEqual(result['result'], {'reviewer_task': 'reviewer-1',
                                            'reviewer_session_id': 'session-1',
                                            'pr': 17, 'head_sha': HEAD, 'version': 1})
        self.assertEqual(result['codes'], ['REVIEW_VERDICT_NOT_POSTED'])

    def test_done_reviewer_with_current_head_pass_keeps_pass_path(self):
        result = self._advance_dispatched_review(reviewer_session_done=True, outcomes=[{
            'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
            'outcome': 'REVIEW_PASS'}], completion={
                'executor_task_id': 'executor-1', 'state': 'PM_EVIDENCE_READY', 'evidence': {
                    'result': {'result_head': HEAD}, 'review_pr_ci': {'stage': 'READY'}}})
        self.assertEqual(result['step'], 'READY_FOR_INTEGRATION')

    def test_older_head_verdict_does_not_count_and_running_reviewer_waits(self):
        old_head = 'b' * 40
        done = self._advance_dispatched_review(reviewer_session_done=True, outcomes=[{
            'source': 'REVIEWER', 'source_sha': old_head, 'version': 1,
            'outcome': 'REVIEW_PASS'}])
        running = self._advance_dispatched_review()
        self.assertEqual(done['step'], 'REVIEW_NOT_POSTED')
        self.assertEqual(running['step'], 'RECORD_REVIEW_RESULT')

    def test_restart_does_not_dispatch_repair_twice(self):
        class Store:
            def __init__(self):
                self.record = base_record(review_dispatch_claim=[{
                    'pr': 17, 'head_sha': HEAD, 'version': 1,
                    'state': 'DISPATCHED', 'reviewer_task': 'reviewer-1'}])
                self.repair = {'state': 'PM_PENDING'}
            def get(self, task_id):
                return self.repair if task_id == 'repair-one' else self.record

        store = Store()
        repaired = []
        def repair(**kwargs):
            repaired.append(kwargs['task_id'])
            store.record['review_repair_claims'] = [{
                'head_sha': HEAD, 'state': 'DISPATCHED',
                'repair_task_id': 'repair-one'}]
            return {'repair_task_id': 'repair-one'}
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[{
                 'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_FAIL'}]):
            first = task_advance.advance(task_id=store.record['task_id'], store=store,
                pr_number=17, cwd='/repo/task', repair_review_call=repair,
                github_get=lambda _url: [])
            second = task_advance.advance(task_id=store.record['task_id'], store=store,
                pr_number=17, cwd='/repo/task', repair_review_call=repair,
                github_get=lambda _url: [])
        self.assertEqual(first['step'], 'REPAIR_REVIEW')
        self.assertEqual(second['step'], 'WAITING_REPAIR')
        self.assertEqual(repaired, ['1' * 32])

    def test_unavailable_repair_is_failed_and_retried_once(self):
        class Store:
            def __init__(self):
                self.record = base_record(
                    developer={'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'codex'},
                    review_dispatch_claim=[{'pr': 17, 'head_sha': HEAD,
                        'version': 1, 'state': 'DISPATCHED'}],
                    review_repair_claims=[{'head_sha': HEAD, 'state': 'DISPATCHED',
                        'repair_task_id': '3' * 32, 'attempt': 1}],
                    record_sha256='d' * 64)
                self.repair = {'developer': {'state': 'DEVELOPER_UNAVAILABLE'}}
            def get(self, task_id):
                return self.repair if task_id == '3' * 32 else self.record
            def compare_and_update(self, _task_id, *, expected, **changes):
                if expected != {'record_sha256': self.record['record_sha256']}:
                    raise RuntimeError('conflict')
                self.record.update(changes)
                self.record['record_sha256'] = 'e' * 64
                return self.record

        store = Store()
        called = []
        def retry(**kwargs):
            called.append((store.record['review_repair_claims'][0]['state'],
                           kwargs.get('retry_claim_id')))
            print('{"repair_task_id":"4' + '4' * 31 + '"}')
            return {'ignored': True}
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[{
                 'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_FAIL'}]), \
             patch.object(task_advance.shutil, 'which', return_value='/usr/bin/codex'):
            result = task_advance.advance(task_id=store.record['task_id'], store=store,
                pr_number=17, cwd='/repo/task', repair_review_call=retry,
                github_get=lambda _path: [])
        self.assertEqual(result['step'], 'REPAIR_RETRY')
        self.assertEqual(result['result'], {'repair_task_id': '4' * 32})
        self.assertEqual(called, [('DISPATCHED', '3' * 32)])
        self.assertEqual(store.record['review_repair_claims'][0]['state'], 'DISPATCHED')

    def test_repair_retry_limit_error_is_reported_after_failed_claim(self):
        class Store:
            def __init__(self):
                self.record = base_record(
                    review_dispatch_claim=[{'pr': 17, 'head_sha': HEAD,
                        'version': 1, 'state': 'DISPATCHED'}],
                    review_repair_claims=[{'head_sha': HEAD, 'state': 'DISPATCHED',
                        'repair_task_id': '3' * 32, 'attempt': 3}],
                    record_sha256='d' * 64)
                self.repair = {'developer': {'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                    'error_code': 'CODEX_BLOCKED', 'execution': {'status': 'BLOCKED'}}}
            def get(self, task_id):
                return self.repair if task_id == '3' * 32 else self.record
            def compare_and_update(self, _task_id, *, expected, **changes):
                self.record.update(changes)
                self.record['record_sha256'] = 'e' * 64

        store = Store()
        def exhausted(**_kwargs):
            error = RuntimeError('bounded attempt limit')
            error.review_repair_code = 'REPAIR_LIMIT_REACHED'
            raise error
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[{
                 'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_FAIL'}]):
            result = task_advance.advance(task_id=store.record['task_id'], store=store,
                pr_number=17, cwd='/repo/task', repair_review_call=exhausted,
                github_get=lambda _path: [])
        self.assertEqual(result['step'], 'REPAIR_RETRY')
        self.assertIn('REPAIR_LIMIT_REACHED', result['codes'])
        self.assertEqual(store.record['review_repair_claims'][0]['state'], 'DISPATCHED')

    def test_missing_runtime_stops_before_repair_claim_transition(self):
        class Store:
            def __init__(self):
                self.record = base_record(
                    developer={'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'codex'},
                    review_dispatch_claim=[{'pr': 17, 'head_sha': HEAD,
                        'version': 1, 'state': 'DISPATCHED'}],
                    review_repair_claims=[{'head_sha': HEAD, 'state': 'DISPATCHED',
                        'repair_task_id': '3' * 32, 'attempt': 1}])
                self.repair = {'developer': {'state': 'DEVELOPER_UNAVAILABLE'}}
                self.updated = False
            def get(self, task_id):
                return self.repair if task_id == '3' * 32 else self.record
            def compare_and_update(self, *_args, **_kwargs):
                self.updated = True
                raise AssertionError('claim transition must not occur')

        store = Store()
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[{
                 'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_FAIL'}]), \
             patch.object(task_advance.shutil, 'which', return_value=None):
            result = task_advance.advance(task_id=store.record['task_id'], store=store,
                pr_number=17, cwd='/repo/task', repair_review_call=lambda **_kwargs:
                    self.fail('repair provider must not run'), github_get=lambda _path: [])
        self.assertEqual(result['step'], 'RUNTIME_NOT_ON_PATH')
        self.assertEqual(result['codes'], ['RUNTIME_NOT_ON_PATH'])
        self.assertFalse(store.updated)

    def test_separate_tasks_and_merge_train_closure_decision(self):
        blocked = base_record(task_id='3' * 32,
                              developer={'state': 'DEVELOPER_OUTCOME_UNKNOWN'})
        ready = base_record(review_dispatch_claim=[{
            'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'DISPATCHED'}],
            completion={'executor_task_id': 'executor-1', 'state': 'PM_EVIDENCE_READY', 'evidence': {
                'result': {'result_head': HEAD}, 'review_pr_ci': {'stage': 'READY'}}})
        related = {'events': [{'source': 'REVIEWER', 'source_sha': HEAD,
                               'version': 1, 'outcome': 'REVIEW_PASS'}]}
        self.assertEqual(task_advance.decide(blocked, d6(), PR, {}), 'DEVELOPER_ATTENTION')
        self.assertEqual(task_advance.decide(ready, d6(), PR, related), 'READY_FOR_INTEGRATION')

    def test_merge_train_closure_records_one_merge_event(self):
        class Store:
            def __init__(self, record):
                self.record = record
            def get(self, _task_id):
                return self.record

        ready = base_record(review_dispatch_claim=[{
            'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'DISPATCHED'}],
            completion={'state': 'PM_EVIDENCE_READY', 'evidence': {
                'result': {'result_head': HEAD}, 'review_pr_ci': {'stage': 'READY'}}})
        store = Store(ready)
        saved = []
        integration = {'number': 27, 'merged': True,
                       'head': {'sha': 'c' * 40}, 'merge_commit_sha': 'd' * 40}
        def pr_snapshot(number, _get):
            return PR if number == 17 else integration
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', side_effect=pr_snapshot), \
             patch.object(task_advance, '_review_outcomes', return_value=[{
                 'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}]):
            result = task_advance.advance(task_id=ready['task_id'], store=store,
                pr_number=17, integration_pr=27,
                github_get=lambda url: {'status': 'ahead'} if url.startswith('compare/') else [],
                record_github_event_call=lambda *args: saved.append(args))
        self.assertEqual(result['step'], 'CLOSED')
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0][-2:], ('MERGED', 'd' * 40))

    def test_verified_merge_closes_when_d6_binding_is_unavailable(self):
        class Store:
            def __init__(self, record):
                self.record = record
            def get(self, _task_id):
                return self.record

        ready = base_record(review_dispatch_claim=[{
            'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'DISPATCHED'}],
            completion={'state': 'PM_EVIDENCE_READY', 'evidence': {
                'result': {'result_head': HEAD}, 'review_pr_ci': {'stage': 'READY'}}})
        merged_review = {'number': 17, 'merged': True,
                         'head': {'sha': HEAD}, 'merge_commit_sha': 'd' * 40}
        integration = {'number': 27, 'merged': True,
                       'head': {'sha': 'c' * 40}, 'merge_commit_sha': 'e' * 40}
        for integration_pr, pr_value, expected_number, expected_sha in (
                (None, merged_review, 17, 'd' * 40),
                (27, PR, 27, 'e' * 40)):
            with self.subTest(integration_pr=integration_pr):
                saved = []
                def pr_snapshot(number, _get):
                    return pr_value if number == 17 else integration
                with patch.object(task_advance, '_d6_snapshot', return_value={
                        'state': 'D6_BINDING_UNAVAILABLE',
                        'code': 'D6_BINDING_UNAVAILABLE'}), \
                     patch.object(task_advance, '_pr_snapshot', side_effect=pr_snapshot), \
                     patch.object(task_advance, '_review_outcomes', return_value=[{
                         'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                         'outcome': 'REVIEW_PASS'}]):
                    result = task_advance.advance(task_id=ready['task_id'], store=Store(ready),
                        pr_number=17, integration_pr=integration_pr,
                        github_get=lambda _url: {'status': 'ahead'},
                        record_github_event_call=lambda *args: saved.append(args))
                self.assertEqual(result['step'], 'CLOSED')
                self.assertEqual(saved[0][4], expected_number)
                self.assertEqual(saved[0][-2:], ('MERGED', expected_sha))

    def test_integration_compare_uses_real_github_path_validator(self):
        from llmtool.task_completion import _GITHUB_API_PATH
        ready = base_record(review_dispatch_claim=[{
            'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'DISPATCHED'}],
            completion={'state': 'PM_EVIDENCE_READY', 'evidence': {
                'result': {'result_head': HEAD}, 'review_pr_ci': {'stage': 'READY'}}})
        integration = {'number': 27, 'merged': True,
                       'head': {'sha': 'c' * 40}, 'merge_commit_sha': 'd' * 40}
        def get(path):
            self.assertTrue(_GITHUB_API_PATH.fullmatch(path), path)
            return {'status': 'ahead'}
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', side_effect=lambda n, _: PR if n == 17 else integration), \
             patch.object(task_advance, '_review_outcomes', return_value=[{
                 'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}]):
            result = task_advance.advance(task_id=ready['task_id'], store=type('S', (), {
                'get': lambda self, _id: ready})(), pr_number=17, integration_pr=27, github_get=get,
                record_github_event_call=lambda *args, **kwargs: None)
        self.assertEqual(result['step'], 'CLOSED')

    def test_d6_snapshot_failure_is_typed(self):
        record = base_record()
        record['completion']['evidence']['common_dir'] = '/invalid'
        with patch.object(task_advance.task_completion, 'read_d6_registration',
                          side_effect=RuntimeError('unavailable')):
            snapshot = task_advance._d6_snapshot(record, 'executor-1')
        self.assertEqual(snapshot['state'], 'D6_BINDING_UNAVAILABLE')
        self.assertEqual(snapshot['code'], 'TASK_ADVANCE_REFUSED')

    def test_completion_action_captures_nested_json_output(self):
        class Store:
            def get(self, _task_id):
                return base_record(completion={'executor_task_id': 'executor-1',
                    'state': 'WAITING_REVIEW', 'evidence': {'result': {'result_head': HEAD}}},
                    review_dispatch_claim=[{'pr': 17, 'head_sha': HEAD,
                        'version': 1, 'state': 'DISPATCHED', 'reviewer_task': 'reviewer'}])
        store = Store()
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[{
                 'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}]):
            # Exercise advance's capture around the production completion adapter.
            def completion(*_args, **_kwargs):
                print('{"task_id":"done"}')
                return 0
            result = task_advance.advance(task_id='1' * 32, store=store, pr_number=17,
                run_task_complete_call=completion, github_get=lambda _path: [])
        self.assertEqual(result['result'], {'task_id': 'done'})

    def test_review_result_checks_posted_pass_before_missing_verdict_mapping(self):
        claim = {'executor_task': 'executor-1', 'pr': 17, 'head_sha': HEAD,
                 'version': 1, 'state': 'DISPATCHED', 'reviewer_task': 'reviewer-1',
                 'native_session_id': 'session-1'}
        record = base_record(review_dispatch_claim=[claim], schema_version=2,
            run_id='2' * 32, pm_session_id='pm-session', task_binding={
                'contract_digest': 'd' * 64})
        record['completion_events'] = []

        class Store:
            def __init__(self):
                self.record = record
            def get(self, _task_id):
                return self.record
            def record_completion_event(self, _task_id, **event):
                self.record['completion_events'].append(event)
                return {'created': True}

        comment = {'id': 9, 'created_at': '2026-10-27T01:00:00Z',
            'updated_at': '2026-10-27T01:00:00Z', 'body':
            '<!-- activity: type=review_verdict actor=Claude target=PM status=PASS -->\n'
            f'Verdict: PASS | Version: v1 | HEAD: {HEAD}\n'}
        store = Store()
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance.task_completion, 'is_formal_claude_pass',
                          return_value=True):
            result = task_advance.advance(task_id=record['task_id'], store=store,
                pr_number=17, reviewer_session_done=True, github_get=lambda _url: [comment])
        self.assertEqual(result['step'], 'RECORD_REVIEW_RESULT')
        self.assertEqual(result['result']['state'], 'REVIEW_PASS')
        self.assertEqual(len(store.record['completion_events']), 1)

    def test_repair_action_captures_nested_json_output(self):
        claim = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'DISPATCHED'}
        failed = base_record(review_dispatch_claim=[claim])
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[{
                 'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_FAIL'}]):
            def repair(**_kwargs):
                print('{"repair_task_id":"repair-1"}')
                return {'ignored': True}
            result = task_advance.advance(task_id=failed['task_id'],
                store=type('S', (), {'get': lambda self, _id: failed})(),
                pr_number=17, cwd='/repo/task', repair_review_call=repair,
                github_get=lambda _path: [])
        self.assertEqual(result['result'], {'repair_task_id': 'repair-1'})

    def test_advance_acks_only_superseded_events_and_is_idempotent(self):
        old_event = {'event_id': 'a' * 32, 'source': 'REVIEWER',
                     'source_sha': 'b' * 40, 'state': 'PM_CONSUMED'}
        record = base_record(pm_session_id='pm-session', completion_events=[old_event])
        class Store:
            def get(self, _task_id):
                return record
        store = Store()
        calls = []
        def ack(_store, event_id, _session_id):
            calls.append(event_id)
            old_event['state'] = 'ACKED'
            return {'acknowledged_now': True}
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[]), \
             patch.object(task_advance.active_pm_inbox, 'ack', side_effect=ack):
            first = task_advance.advance(task_id=record['task_id'], store=store,
                pr_number=17, dispatch_review_call=lambda **_kwargs: {'state': 'DISPATCHED'},
                github_get=lambda _url: [])
            second = task_advance.advance(task_id=record['task_id'], store=store,
                pr_number=17, dispatch_review_call=lambda **_kwargs: {'state': 'DISPATCHED'},
                github_get=lambda _url: [])
        self.assertEqual(first['step'], 'DISPATCH_REVIEW')
        self.assertEqual(first['acked'], ['a' * 32])
        self.assertEqual(second['step'], first['step'])
        self.assertEqual(second['acked'], [])
        self.assertEqual(calls, ['a' * 32])

    def test_advance_keeps_current_head_reviewer_event_for_decide(self):
        claim = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'DISPATCHED'}
        current_event = {'event_id': 'a' * 32, 'source': 'REVIEWER',
                         'source_sha': HEAD, 'version': 1, 'outcome': 'REVIEW_BLOCKED',
                         'state': 'PM_CONSUMED'}
        record = base_record(pm_session_id='pm-session', review_dispatch_claim=[claim],
                             completion_events=[current_event])
        class Store:
            def get(self, _task_id):
                return record
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[]), \
             patch.object(task_advance.active_pm_inbox, 'ack') as ack:
            result = task_advance.advance(task_id=record['task_id'], store=Store(),
                pr_number=17, github_get=lambda _url: [])
        expected = task_advance.decide(record, d6(), PR, {'events': [current_event],
                                                          'repair_claims': []})
        self.assertEqual(result['step'], expected)
        self.assertEqual(result['step'], 'REVIEWER_ATTENTION')
        self.assertEqual(result['acked'], [])
        ack.assert_not_called()

    def test_advance_closed_acks_every_unacked_event(self):
        claim = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'DISPATCHED'}
        events = [
            {'event_id': 'a' * 32, 'source': 'REVIEWER', 'source_sha': HEAD,
             'version': 1, 'outcome': 'REVIEW_PASS', 'state': 'PM_CONSUMED'},
            {'event_id': 'b' * 32, 'source': 'HOST_WORKER', 'state': 'PENDING'},
            {'event_id': 'c' * 32, 'source': 'HOST_WORKER', 'state': 'ACKED'},
        ]
        record = base_record(pm_session_id='pm-session', review_dispatch_claim=[claim],
            completion={'executor_task_id': 'executor-1', 'state': 'PM_EVIDENCE_READY',
                'evidence': {'result': {'result_head': HEAD}, 'review_pr_ci': {'stage': 'READY'}}},
            completion_events=events)
        class Store:
            def get(self, _task_id):
                return record
        calls = []
        def ack(_store, event_id, _session_id):
            calls.append(event_id)
            return {'acknowledged_now': True}
        merged_pr = dict(PR, merged=True, merge_commit_sha='d' * 40)
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=merged_pr), \
             patch.object(task_advance, '_review_outcomes', return_value=[{
                 'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}]), \
             patch.object(task_advance.active_pm_inbox, 'ack', side_effect=ack):
            result = task_advance.advance(task_id=record['task_id'], store=Store(),
                pr_number=17, github_get=lambda _url: [],
                record_github_event_call=lambda *_args, **_kwargs: None)
        self.assertEqual(result['step'], 'CLOSED')
        self.assertEqual(result['acked'], ['a' * 32, 'b' * 32])
        self.assertEqual(calls, ['a' * 32, 'b' * 32])

    def test_advance_closed_acks_newly_recorded_merge_event(self):
        claim = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'DISPATCHED'}
        record = base_record(pm_session_id='pm-session', review_dispatch_claim=[claim],
            completion={'state': 'PM_EVIDENCE_READY', 'evidence': {
                'result': {'result_head': HEAD}, 'review_pr_ci': {'stage': 'READY'}}})
        class Store:
            def __init__(self):
                self.record = record
            def get(self, _task_id):
                return self.record
        store = Store()
        def record_merge_event(_store, _record, session_id, event_id,
                               issue_number, item_id, marker_type, status, sha):
            store.record['completion_events'].append({
                'event_id': event_id, 'source': 'GITHUB', 'source_sha': sha,
                'session_id': session_id, 'issue_number': issue_number,
                'item_id': item_id, 'marker_type': marker_type, 'status': status,
                'state': 'PENDING'})
        def ack(_store, event_id, _session_id):
            event = next(item for item in store.record['completion_events']
                         if item['event_id'] == event_id)
            event['state'] = 'ACKED'
            return {'acknowledged_now': True}
        merged_pr = dict(PR, merged=True, merge_commit_sha='d' * 40)
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=merged_pr), \
             patch.object(task_advance, '_review_outcomes', return_value=[{
                 'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}]), \
             patch.object(task_advance.active_pm_inbox, 'ack', side_effect=ack):
            result = task_advance.advance(task_id=record['task_id'], store=store,
                pr_number=17, github_get=lambda _url: [],
                record_github_event_call=record_merge_event)
        merge_event = next(event for event in store.record['completion_events']
                           if event.get('source') == 'GITHUB')
        self.assertEqual(result['step'], 'CLOSED')
        self.assertEqual(result['acked'], [merge_event['event_id']])
        self.assertEqual(merge_event['state'], 'ACKED')

    def test_advance_ack_failure_adds_code_without_changing_step(self):
        event = {'event_id': 'a' * 32, 'source': 'REVIEWER',
                 'source_sha': 'b' * 40, 'state': 'PM_CONSUMED'}
        record = base_record(pm_session_id='pm-session', completion_events=[event])
        class Store:
            def get(self, _task_id):
                return record
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[]), \
             patch.object(task_advance.active_pm_inbox, 'ack', side_effect=RuntimeError('store')):
            result = task_advance.advance(task_id=record['task_id'], store=Store(),
                pr_number=17, dispatch_review_call=lambda **_kwargs: {'state': 'DISPATCHED'},
                github_get=lambda _url: [])
        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        self.assertEqual(result['result'], {'state': 'DISPATCHED'})
        self.assertEqual(result['next'], 'DISPATCH_REVIEW')
        self.assertEqual(result['codes'], ['ACK_SKIPPED'])
        self.assertEqual(result['acked'], [])

    def test_cli_advance_prints_one_json_object_for_action_steps(self):
        cases = []

        repair_record = base_record(
            review_dispatch_claim=[{'pr': 17, 'head_sha': HEAD, 'version': 1,
                                    'state': 'DISPATCHED'}],
            completion_events=[{'source': 'REVIEWER', 'source_sha': HEAD,
                                'version': 1, 'outcome': 'REVIEW_FAIL'}])
        def repair_review(**_kwargs):
            print('{"repair_task_id":"repair-new"}')
            return {'ignored': True}
        cases.append((repair_record, {'repair_review_call': repair_review,
                                      'cwd': '/repo/task'}, 'REPAIR_REVIEW', None))

        retry_record = base_record(
            review_dispatch_claim=[{'pr': 17, 'head_sha': HEAD, 'version': 1,
                                    'state': 'DISPATCHED'}],
            review_repair_claims=[{'head_sha': HEAD, 'state': 'DISPATCHED',
                                   'repair_task_id': 'repair-old', 'attempt': 1}],
            completion_events=[{'source': 'REVIEWER', 'source_sha': HEAD,
                                'version': 1, 'outcome': 'REVIEW_FAIL'}])
        class RetryStore:
            def get(self, task_id):
                if task_id == 'repair-old':
                    return {'developer': {'state': 'DEVELOPER_UNAVAILABLE'}}
                return retry_record
        def repair_retry(**_kwargs):
            print('{"repair_task_id":"repair-retry"}')
            return {'ignored': True}
        cases.append((retry_record, {'repair_review_call': repair_retry,
                                     'cwd': '/repo/task'}, 'REPAIR_RETRY', RetryStore()))

        completion_record = base_record(
            review_dispatch_claim=[{'pr': 17, 'head_sha': HEAD, 'version': 1,
                                    'state': 'DISPATCHED'}],
            completion_events=[{'source': 'REVIEWER', 'source_sha': HEAD,
                                'version': 1, 'outcome': 'REVIEW_PASS'}])
        def run_completion(_args, **_kwargs):
            print('{"completion":"recorded"}')
            return 0
        cases.append((completion_record, {'run_task_complete_call': run_completion},
                      'RUN_TASK_COMPLETE', None))

        dispatch_record = base_record()
        def dispatch_review(**_kwargs):
            print('{"reviewer_task":"reviewer-new"}')
            return {'ignored': True}
        cases.append((dispatch_record, {'dispatch_review_call': dispatch_review},
                      'DISPATCH_REVIEW', None))

        for record, adapters, expected_step, custom_store in cases:
            with self.subTest(step=expected_step):
                store = custom_store or type('Store', (), {
                    'get': lambda self, _task_id: record})()
                with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
                     patch.object(task_advance, '_pr_snapshot', return_value=PR), \
                     patch.object(task_advance, '_review_outcomes', return_value=[]):
                    args = parser_for_cli().parse_args([
                        'task', 'advance', record['task_id'], '--pr', '17'])
                    output = io.StringIO()
                    with redirect_stdout(output):
                        task_submission_cli.run_task_advance(args, store=store,
                            github_get=lambda _path: [], **adapters)
                lines = output.getvalue().splitlines()
                self.assertEqual(len(lines), 1)
                self.assertTrue(lines[0])
                result = json.loads(lines[0])
                self.assertEqual(result['task_id'], record['task_id'])
                self.assertEqual(result['step'], expected_step)
                self.assertIn('next', result)
                self.assertIn('codes', result)


class TaskAdvancePublishTests(unittest.TestCase):
    TASK_ID = '1' * 32
    EXECUTOR_TASK = '3' * 32
    BRANCH = 'feat/task-publish'
    WRITE_PATHS = ['allowed.txt']

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, 'task')
        self.bare = os.path.join(self.tmp.name, 'remote.git')
        os.mkdir(self.root)
        self.git(['init', '--bare', self.bare], cwd=self.tmp.name)
        self.git(['init', '-b', self.BRANCH], cwd=self.root)
        self.git(['config', 'user.name', 'Task Test'], cwd=self.root)
        self.git(['config', 'user.email', 'task-test@example.invalid'], cwd=self.root)
        with open(os.path.join(self.root, 'allowed.txt'), 'w', encoding='utf-8') as stream:
            stream.write('base\n')
        self.git(['add', '--', 'allowed.txt'], cwd=self.root)
        self.git(['commit', '-m', 'base'], cwd=self.root)
        self.git(['remote', 'add', 'origin', self.bare], cwd=self.root)
        self.git(['push', '-u', 'origin', self.BRANCH], cwd=self.root)
        self.initial_head = self.git(['rev-parse', 'HEAD'], cwd=self.root).strip()
        self.recorded = []
        self.gh_calls = []
        self.pr_exists = False

    @staticmethod
    def git(args, *, cwd):
        return subprocess.run(['git', *args], cwd=cwd, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout

    @staticmethod
    def git_runner(root, args):
        return subprocess.run(['git', *args], cwd=root, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)

    def record_result(self, **kwargs):
        self.recorded.append(kwargs)
        return self.git(['rev-parse', 'HEAD'], cwd=self.root).strip()

    def gh(self, _root, args):
        self.gh_calls.append(list(args))
        if args[:2] == ['pr', 'list']:
            pulls = []
            if self.pr_exists:
                pulls = [{'number': 23,
                          'headRefOid': self.git(['rev-parse', 'HEAD'], cwd=self.root).strip()}]
            return subprocess.CompletedProcess(['gh', *args], 0,
                json.dumps(pulls).encode(), b'')
        if args[:2] == ['pr', 'create']:
            self.pr_exists = True
            return subprocess.CompletedProcess(['gh', *args], 0, b'', b'')
        raise AssertionError('unexpected fake gh command')

    def publish(self, *, write_paths=None):
        record = {'task_id': self.TASK_ID,
                  'developer': {'write_paths': self.WRITE_PATHS if write_paths is None else write_paths}}
        d6_binding = {'root': self.root, 'common_dir': os.path.join(self.tmp.name, 'canonical', '.git'),
                      'branch': self.BRANCH, 'task_id': self.EXECUTOR_TASK,
                      'recorded_result_head': self.initial_head}
        return task_advance._publish_task(record, d6_binding, self.EXECUTOR_TASK,
            git_runner=self.git_runner, gh_runner=self.gh,
            record_result_call=self.record_result)

    def test_publish_commits_records_pushes_and_returns_matching_pr_head(self):
        with open(os.path.join(self.root, 'allowed.txt'), 'w', encoding='utf-8') as stream:
            stream.write('published\n')
        result = self.publish()
        self.assertEqual(result['pr'], 23)
        self.assertEqual(result['head'], self.git(['rev-parse', 'HEAD'], cwd=self.root).strip())
        self.assertEqual(self.recorded[0]['task_id'], self.EXECUTOR_TASK)
        self.assertEqual(self.git(['show', '-s', '--format=%s', 'HEAD'], cwd=self.root).strip(),
                         f'llmtool Task {self.TASK_ID}')
        self.assertEqual(self.git(['ls-remote', 'origin', f'refs/heads/{self.BRANCH}'],
                                  cwd=self.root).split()[0], result['head'])

    def test_second_publish_is_noop_and_reports_existing_pr_head(self):
        with open(os.path.join(self.root, 'allowed.txt'), 'w', encoding='utf-8') as stream:
            stream.write('published\n')
        first = self.publish()
        d6_binding = {'root': self.root, 'common_dir': os.path.join(self.tmp.name, 'canonical', '.git'),
                      'branch': self.BRANCH, 'task_id': self.EXECUTOR_TASK,
                      'recorded_result_head': first['head']}
        second = task_advance._publish_task(
            {'task_id': self.TASK_ID, 'developer': {'write_paths': self.WRITE_PATHS}},
            d6_binding, self.EXECUTOR_TASK, git_runner=self.git_runner, gh_runner=self.gh,
            record_result_call=self.record_result)
        self.assertEqual(second, first)
        self.assertEqual(self.git(['rev-parse', 'HEAD'], cwd=self.root).strip(), first['head'])
        self.assertEqual(len(self.recorded), 1)

    def test_out_of_scope_change_is_refused_without_commit(self):
        with open(os.path.join(self.root, 'outside.txt'), 'w', encoding='utf-8') as stream:
            stream.write('outside\n')
        with self.assertRaises(task_advance._PublishRefusal) as caught:
            self.publish()
        self.assertEqual(caught.exception.code, 'PUBLISH_OUT_OF_SCOPE')
        self.assertEqual(self.git(['rev-parse', 'HEAD'], cwd=self.root).strip(), self.initial_head)

    def test_ignored_python_cache_is_refused_without_commit(self):
        os.mkdir(os.path.join(self.root, '__pycache__'))
        with open(os.path.join(self.root, '__pycache__', 'stray.pyc'), 'wb') as stream:
            stream.write(b'ignored')
        self.git(['config', 'core.excludesfile', os.path.join(self.tmp.name, 'ignore')], cwd=self.root)
        with open(os.path.join(self.tmp.name, 'ignore'), 'w', encoding='utf-8') as stream:
            stream.write('__pycache__/\n')
        with self.assertRaises(task_advance._PublishRefusal) as caught:
            self.publish()
        self.assertEqual(caught.exception.code, 'PUBLISH_IGNORED_STRAY')
        self.assertEqual(self.git(['rev-parse', 'HEAD'], cwd=self.root).strip(), self.initial_head)

    def test_non_fast_forward_remote_is_refused_without_commit(self):
        peer = os.path.join(self.tmp.name, 'peer')
        self.git(['clone', '--branch', self.BRANCH, self.bare, peer], cwd=self.tmp.name)
        self.git(['config', 'user.name', 'Peer'], cwd=peer)
        self.git(['config', 'user.email', 'peer@example.invalid'], cwd=peer)
        with open(os.path.join(peer, 'allowed.txt'), 'w', encoding='utf-8') as stream:
            stream.write('remote moved\n')
        self.git(['add', '--', 'allowed.txt'], cwd=peer)
        self.git(['commit', '-m', 'remote move'], cwd=peer)
        self.git(['push', 'origin', self.BRANCH], cwd=peer)
        with open(os.path.join(self.root, 'allowed.txt'), 'w', encoding='utf-8') as stream:
            stream.write('local change\n')
        with self.assertRaises(task_advance._PublishRefusal) as caught:
            self.publish()
        self.assertEqual(caught.exception.code, 'PUBLISH_NON_FAST_FORWARD')
        self.assertEqual(self.git(['rev-parse', 'HEAD'], cwd=self.root).strip(), self.initial_head)


class RetiredD6MergeClosureTests(unittest.TestCase):
    """#547: a merged Task closes after its D6 is retired, via the real _d6_snapshot."""

    MERGED_PR = {'number': 17, 'merged': True, 'head': {'sha': HEAD}, 'merge_commit_sha': 'd' * 40}
    TRAIN = {'number': 541, 'merged': True, 'head': {'sha': 'c' * 40}, 'merge_commit_sha': 'e' * 40}

    def setUp(self):
        import os
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # A git common dir with no llmtool-worktrees registry: the D6 is retired.
        self.common = os.path.realpath(self.tmp.name) + '/.git'
        os.makedirs(self.common)

    def ready(self, outcome='REVIEW_PASS'):
        record = base_record(
            review_dispatch_claim=[{'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'COMPLETED'}],
            completion={'executor_task_id': 'executor-1', 'state': 'PM_EVIDENCE_READY',
                        'evidence': {'common_dir': self.common, 'result': {'result_head': HEAD},
                                     'review_pr_ci': {'stage': 'READY'}}})
        record['completion_events'] = [
            {'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1, 'outcome': outcome}]
        return record

    def advance(self, record, prs, *, compare='ahead', integration_pr=None, registry_code=None):
        from llmtool import task_completion
        saved = []

        def get(path):
            if path.startswith('pulls/'):
                return prs.get(int(path.split('/')[1]))
            if path.startswith('compare/'):
                return {'status': compare}
            return []

        store = type('S', (), {'get': lambda self, _id: record})()
        if registry_code is None:
            snapshot = task_advance._d6_snapshot(record, 'executor-1')
            result = task_advance.advance(
                task_id=record['task_id'], store=store, pr_number=17, integration_pr=integration_pr,
                github_get=get, record_github_event_call=lambda *a: saved.append(a))
        else:
            with patch.object(task_completion, 'read_d6_registration',
                              side_effect=lambda *a, **k: task_completion._stop(registry_code)):
                snapshot = task_advance._d6_snapshot(record, 'executor-1')
                result = task_advance.advance(
                    task_id=record['task_id'], store=store, pr_number=17,
                    integration_pr=integration_pr, github_get=get,
                    record_github_event_call=lambda *a: saved.append(a))
        return snapshot, result, saved

    def test_merged_pr_closes_with_missing_registry(self):
        snapshot, result, saved = self.advance(self.ready(), {17: self.MERGED_PR})
        self.assertEqual(snapshot['state'], 'D6_BINDING_UNAVAILABLE')
        self.assertEqual(result['step'], 'CLOSED')
        self.assertEqual(saved[0][4], 17)
        self.assertEqual(saved[0][-2:], ('MERGED', 'd' * 40))

    def test_train_merge_closes_with_invalid_registry(self):
        snapshot, result, saved = self.advance(
            self.ready(), {17: PR, 541: self.TRAIN}, integration_pr=541,
            registry_code='D6_REGISTRY_INVALID')
        self.assertEqual(snapshot['code'], 'D6_REGISTRY_INVALID')
        self.assertEqual(result['step'], 'CLOSED')
        self.assertEqual(saved[0][4], 541)
        self.assertEqual(saved[0][-2:], ('MERGED', 'e' * 40))

    def test_without_merge_proof_the_retired_d6_still_refuses(self):
        other_head = dict(self.MERGED_PR, head={'sha': 'f' * 40})
        cases = (
            ('not merged', self.ready(), {17: PR}, 'ahead', None),
            ('train diverged', self.ready(), {17: PR, 541: self.TRAIN}, 'diverged', 541),
            ('merged at another head', self.ready(), {17: other_head}, 'ahead', None),
            ('review failed', self.ready('REVIEW_FAIL'), {17: self.MERGED_PR}, 'ahead', None),
        )
        for name, record, prs, compare, train in cases:
            with self.subTest(name):
                _, result, saved = self.advance(record, prs, compare=compare, integration_pr=train,
                                                registry_code='D6_REGISTRY_INVALID')
                self.assertEqual(result['step'], 'D6_BINDING_UNAVAILABLE')
                self.assertEqual(saved, [])


if __name__ == '__main__':
    unittest.main()
