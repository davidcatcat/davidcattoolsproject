import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from llmtool import review_dispatch, task_submission_cli
from llmtool.cli import parser_for_cli
from llmtool.task_store import TaskStore, new_id
from llmtool.task_verification import AcceptanceCriterion, CriterionKind, TaskContract

import review_trust_fixture
import worker_policy_fixture


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


OWNER_SESSION = '3f2b8c1e-4d5a-4b6c-8d7e-9f0a1b2c3d4e'
HEAD = 'b' * 40
EXECUTOR_TASK = '4' * 32


class ReviewDispatchVerdictPostTests(unittest.TestCase):
    """#2769: manual task review-dispatch posts a caller-owned Codex verdict."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        env_patch = patch.dict(os.environ, {
            'HOME': str(self.home),
            'CLAUDE_CODE_SESSION_ID': '',
            'CODEX_THREAD_ID': '',
        })
        env_patch.start()
        self.addCleanup(env_patch.stop)
        worker_policy_fixture.install(self)
        self.store = TaskStore(self.home / 'tasks')
        self.post_dir = self.home / 'posts'
        post_dir_patch = patch.object(
            review_dispatch, '_verdict_post_dir',
            lambda _store: review_dispatch._private_post_dir(self.post_dir))
        post_dir_patch.start()
        self.addCleanup(post_dir_patch.stop)

    def _create_completed_codex_task(self, *, pr=17, version=1):
        task_id = new_id()
        run_id = new_id()
        request = 'Verify manual review-dispatch Codex verdict posting.'
        request_sha = hashlib.sha256(request.encode('utf-8')).hexdigest()
        contract = TaskContract(
            schema_version=1,
            task_id=task_id,
            contract_version=1,
            previous_digest=None,
            goal='Post Codex verdict handed to caller.',
            authorized_scope=('pm-planning',),
            forbidden_scope=(),
            criteria=(AcceptanceCriterion(
                criterion_id='pm_response',
                kind=CriterionKind.ARTIFACT,
                description='Record PM decision.',
            ),),
        )
        binding = contract.binding(run_id).to_dict()
        contract_digest = binding['contract_digest']
        review_text = (
            '<!-- activity: type=review_verdict actor=Codex target=PM status=PASS -->\n'
            f'Verdict: PASS | Version: v{version} | HEAD: {HEAD}\n'
            'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n'
            f'LLMTOOL-REVIEW-BINDING v1 task={task_id} run={run_id} '
            f'contract={contract_digest} head={HEAD}'
        )
        reviewer_task = 'reviewer-' + 'a' * 20
        stamp = '2026-11-26T00:00:00Z'
        claim = {
            'executor_task': EXECUTOR_TASK,
            'pr': pr,
            'head_sha': HEAD,
            'version': version,
            'state': 'COMPLETED',
            'reviewer_task': reviewer_task,
            'native_session_id': '0123456789abcdef',
            'account_mode': 'CODEX_CLI',
            'claimed_at': stamp,
            'updated_at': stamp,
            'review_result': {
                'task_id': task_id,
                'run_id': run_id,
                'contract_digest': contract_digest,
                'head': HEAD,
                'verdict': 'PASS',
                'review_text': review_text,
            },
        }
        record = {
            'schema_version': 2,
            'task_id': task_id,
            'run_id': run_id,
            'pm_session_id': 'pm-' + OWNER_SESSION,
            'pm_provider_profile': 'active-pm-runtime',
            'pm_provider_type': 'active-pm-runtime',
            'pm_model': None,
            'pm_route_binding': {'type': 'active-pm-runtime', 'model': None, 'base_url': None},
            'backend_session_semantics': 'NO_NATIVE_SESSION_ID_EXPOSED',
            'native_session_id': None,
            'state': 'PM_RESPONDED',
            'created_at': stamp,
            'updated_at': stamp,
            'task_contract': contract.to_dict(),
            'task_binding': binding,
            'request': request,
            'request_sha256': request_sha,
            'response': 'PM recorded task.',
            'usage': None,
            'error_code': None,
            'developer': None,
            'pm_assignment': None,
            'completion': {
                'state': 'WAITING_REVIEW',
                'executor_task_id': EXECUTOR_TASK,
                'reviewer_task_id': reviewer_task,
                'evidence': {'root': str(self.home)},
                'missing_gates': [],
                'pm_dispatch': 'NOT_STARTED',
                'pm_response': None,
                'pm_recommendation': None,
                'pm_usage': None,
                'pm_error_code': None,
            },
            'review_dispatch_claim': [claim],
            'record_sha256': None,
        }
        self.store.create(record)
        return task_id, claim, review_text

    def _run_cli(self, task_id, *, pr=17, version=1, dispatch_result,
                 comments=None, poster=None):
        args = parser_for_cli().parse_args([
            'task', 'review-dispatch', task_id,
            '--executor-task', EXECUTOR_TASK,
            '--pr', str(pr),
            '--version', str(version),
            '--reviewer', 'codex-reviewer',
            '--pm-session', OWNER_SESSION,
        ])
        output = io.StringIO()
        posts = []

        def default_poster(_root, pr_number, body):
            posts.append((pr_number, body))
            return {
                'id': 901,
                'user': {'login': 'reviewer-user'},
                'body': body,
            }

        with patch('llmtool.review_dispatch.dispatch_review',
                   return_value=dispatch_result), \
                patch.object(review_dispatch, '_resolve_github_login',
                             return_value='reviewer-user'), \
                patch.object(review_dispatch, '_list_issue_comments',
                             return_value=[] if comments is None else list(comments)), \
                patch.object(review_dispatch, '_create_issue_comment',
                             side_effect=poster or default_poster), \
                redirect_stdout(output):
            rc = task_submission_cli.run_task_review_dispatch(args, store=self.store)
        return rc, output.getvalue(), posts

    def test_a1_posts_caller_verdict_prints_real_post_state_and_omits_text(self):
        task_id, claim, review_text = self._create_completed_codex_task()
        verdict_post, keep_text = review_dispatch._reserve_caller_post(
            store=self.store, task_id=task_id, claim=claim, text=review_text,
            clock=lambda: 1000.0)
        self.assertEqual((verdict_post, keep_text), ({'state': 'CALLER_POSTING'}, True))

        dispatch_result = dict(claim)
        dispatch_result.pop('review_result', None)
        dispatch_result['verdict'] = 'PASS'
        dispatch_result['review_text'] = review_text
        dispatch_result['verdict_post'] = verdict_post

        rc, raw_out, posts = self._run_cli(task_id, dispatch_result=dispatch_result)
        self.assertIsNone(rc)
        self.assertEqual(posts, [(17, review_text)])
        parsed = json.loads(raw_out)
        self.assertEqual(parsed['verdict_post'], {'state': 'POSTED', 'comment_id': 901})
        self.assertNotIn('review_text', parsed)
        self.assertNotIn(review_text, raw_out)
        self.assertNotIn('LLMTOOL-REVIEW-BINDING', raw_out)

        name = review_dispatch._verdict_post_name(task_id, claim)
        receipt = review_dispatch._read_receipt(self.post_dir, name)
        self.assertEqual(receipt['state'], 'POSTED')
        self.assertEqual(receipt['comment_id'], 901)

    def test_a2_post_failure_prints_failed_state_with_code_and_exits_1(self):
        task_id, claim, review_text = self._create_completed_codex_task()
        verdict_post, _ = review_dispatch._reserve_caller_post(
            store=self.store, task_id=task_id, claim=claim, text=review_text,
            clock=lambda: 1000.0)

        dispatch_result = dict(claim)
        dispatch_result.pop('review_result', None)
        dispatch_result['verdict'] = 'PASS'
        dispatch_result['review_text'] = review_text
        dispatch_result['verdict_post'] = verdict_post

        def fail_post(_root, _pr_number, _body):
            raise review_dispatch._PostError('VERDICT_POST_COMMAND_FAILED')

        rc, raw_out, _ = self._run_cli(
            task_id, dispatch_result=dispatch_result, poster=fail_post)
        self.assertEqual(rc, 1)
        parsed = json.loads(raw_out)
        self.assertEqual(parsed['verdict_post'], {
            'state': 'FAILED',
            'code': 'VERDICT_POST_COMMAND_FAILED',
        })
        self.assertNotIn('review_text', parsed)
        self.assertNotIn(review_text, raw_out)

        name = review_dispatch._verdict_post_name(task_id, claim)
        receipt = review_dispatch._read_receipt(self.post_dir, name)
        self.assertEqual(receipt['state'], 'FAILED')
        self.assertEqual(receipt['code'], 'VERDICT_POST_COMMAND_FAILED')


if __name__ == '__main__':
    unittest.main()
