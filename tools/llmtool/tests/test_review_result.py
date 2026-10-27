import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llmtool import cli, review_result, task_store, task_submission_cli


TASK, RUN, HEAD, CONTRACT = 'a' * 32, 'b' * 32, 'c' * 40, 'd' * 64
EXECUTOR, REVIEWER, PR = 'executor-d6', 'reviewer-d6', 42


def _store(root):
    contract = {
        'schema_version': 1, 'contract_version': 1, 'task_id': TASK,
        'goal': 'test review results', 'authorized_scope': ['pm-planning'],
        'criteria': [{'criterion_id': 'pm_response', 'description': 'Record result.',
                      'kind': 'ARTIFACT'}], 'forbidden_scope': [], 'previous_digest': None,
    }
    parsed = task_store.parse_task_contract(json.dumps(contract))
    stamp = '2026-10-27T00:00:00Z'
    claim = {'executor_task': EXECUTOR, 'pr': PR, 'head_sha': HEAD, 'version': 1,
             'state': 'DISPATCHED', 'reviewer_task': REVIEWER,
             'native_session_id': 'review-session', 'account_mode': 'DEFAULT_SECOND_ACCOUNT',
             'claimed_at': stamp, 'updated_at': stamp}
    record = {
        'schema_version': 2, 'task_id': TASK, 'run_id': RUN,
        'pm_session_id': 'pm-test', 'pm_provider_profile': 'test',
        'pm_provider_type': 'test', 'pm_model': None,
        'pm_route_binding': {'type': 'test', 'model': None, 'base_url': None},
        'backend_session_semantics': 'NOT_STARTED', 'native_session_id': None,
        'state': 'PM_PENDING', 'created_at': stamp, 'updated_at': stamp,
        'task_contract': contract, 'task_binding': vars(parsed.binding(RUN)),
        'request': 'test review results',
        'request_sha256': hashlib.sha256(b'test review results').hexdigest(),
        'response': None, 'usage': None, 'error_code': None,
        'developer': None, 'pm_assignment': None, 'completion': None,
        'review_dispatch_claim': [claim],
    }
    store = task_store.TaskStore(Path(root).resolve())
    store.create(record)
    return store


def _comment(status='PASS', *, actor='Claude', head=HEAD, version=1,
             comment_id=7, created='2026-10-27T01:00:00Z', extra=''):
    stamp = 'PASS' if status == 'PASS' else 'FAIL' if status in ('FAIL', 'FIX') else status
    marker_status = 'PASS' if status == 'PASS' else 'FIX' if status == 'FIX' else 'FAIL'
    body = (f'<!-- activity: type=review_verdict actor={actor} target=PM status={marker_status} -->\n'
            f'Verdict: {stamp} | Version: v{version} | HEAD: {head}\n')
    if actor == 'Claude' and status == 'PASS':
        body += (f'LLMTOOL-REVIEW-BINDING v1 task={TASK} run={RUN} '
                 f'contract={CONTRACT} head={head}\nFORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n')
    body += extra
    return {'id': comment_id, 'body': body, 'created_at': created, 'updated_at': created,
            'user': {'login': 'shared-agent'}}


class ReviewResultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = _store(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_result(self, comments, **kwargs):
        digest = self.store.get(TASK)['task_binding']['contract_digest']
        comments = [dict(comment, body=comment['body'].replace(CONTRACT, digest))
                    for comment in comments]
        return review_result.record_review_result(
            store=self.store, task_id=TASK, executor_task=EXECUTOR, pr_number=PR,
            github_get=lambda path: comments, **kwargs)

    def test_formal_claude_pass_is_recorded_and_replay_is_idempotent(self):
        first = self.run_result([_comment()])
        second = self.run_result([_comment()])
        self.assertEqual(first['state'], 'REVIEW_PASS')
        self.assertTrue(first['created'])
        self.assertFalse(second['created'])
        event = self.store.get(TASK)['completion_events'][0]
        self.assertEqual(event['source'], 'REVIEWER')
        self.assertEqual(event['source_sha'], HEAD)

    def test_fail_and_blocked_no_verdict(self):
        failed = self.run_result([_comment('FIX')])
        self.assertEqual(failed['state'], 'REVIEW_FAIL')
        self.assertEqual(self.run_result([], reviewer_session_done=True)['state'], 'REVIEW_BLOCKED')

    def test_no_verdict_yet_does_not_record_event(self):
        result = self.run_result([])
        self.assertEqual(result, {'state': 'NOT_READY', 'event': None})
        self.assertEqual(self.store.get(TASK).get('completion_events', []), [])

    def test_wrong_head_and_version_are_ignored(self):
        result = self.run_result([_comment(head='e' * 40), _comment(version=2)])
        self.assertEqual(result['state'], 'NOT_READY')

    def test_later_fail_supersedes_pass(self):
        later = _comment('FAIL', comment_id=8, created='2026-10-27T02:00:00Z')
        self.assertEqual(self.run_result([_comment(), later])['state'], 'REVIEW_FAIL')

    def test_codex_pass_is_advisory(self):
        self.assertEqual(self.run_result([_comment(actor='Codex')])['state'], 'NOT_READY')

    def test_event_survives_taskstore_restart(self):
        self.run_result([_comment()])
        restarted = task_store.TaskStore(Path(self.temp.name).resolve())
        events = restarted.get(TASK)['completion_events']
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['source'], 'REVIEWER')

    def test_cli_route_reaches_handler(self):
        with patch.object(task_submission_cli, 'run_task_review_result', return_value=17) as handler:
            self.assertEqual(cli.main(['task', 'review-result', TASK,
                                       '--executor-task', EXECUTOR, '--pr', str(PR)]), 17)
        handler.assert_called_once()

