import unittest
from unittest.mock import patch

from llmtool import task_advance
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
    def test_cli_route(self):
        parsed = parser_for_cli().parse_args(['task', 'advance', '1' * 32, '--pr', '17'])
        self.assertEqual(parsed.action, 'advance')
        with patch('llmtool.task_submission_cli.run_task_advance', return_value=0) as handler:
            self.assertEqual(main(['task', 'advance', '1' * 32, '--pr', '17']), 0)
        handler.assert_called_once()

    def test_developer_attention_and_publish_gates(self):
        blocked = base_record(developer={'state': 'DEVELOPER_OUTCOME_UNKNOWN'})
        self.assertEqual(task_advance.decide(blocked, d6(), PR, {}), 'DEVELOPER_ATTENTION')
        self.assertEqual(task_advance.decide(base_record(), d6(False), PR, {}), 'NEEDS_PUBLISH')
        self.assertEqual(task_advance.decide(base_record(), d6(recorded=None), PR, {}), 'NEEDS_PUBLISH')
        self.assertEqual(task_advance.decide(base_record(), d6(), None, {}), 'NEEDS_PUBLISH')
        self.assertEqual(task_advance.decide(base_record(), d6(),
            {'number': 17, 'head': {'sha': 'b' * 40}}, {}), 'NEEDS_PUBLISH')

    def test_dispatch_unknown_result_and_review_outcomes(self):
        self.assertEqual(task_advance.decide(base_record(), d6(), PR, {}), 'DISPATCH_REVIEW')
        claim = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'CLAIMED'}
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]), d6(), PR, {}),
                         'REVIEW_OUTCOME_UNKNOWN')
        claim['state'] = 'DISPATCHED'
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
        claim = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'DISPATCHED',
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
                                            'reviewer_session_id': 'session-1'})

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


if __name__ == '__main__':
    unittest.main()
