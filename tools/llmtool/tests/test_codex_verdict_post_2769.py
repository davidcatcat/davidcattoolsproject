import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import review_trust_fixture
import worker_policy_fixture
from llmtool import review_dispatch, task_advance, task_store, task_submission_cli


HEAD = 'b' * 40
MAIN_SHA = 'e' * 40
EXEC = 'executor-d6'
PM_SESSION = 'test-session'
PR_NUMBER = 2769
LOGIN = 'reviewer-user'


def setUpModule():
    review_trust_fixture.start_module()
    worker_policy_fixture.start_module()


def tearDownModule():
    worker_policy_fixture.stop_module()
    review_trust_fixture.stop_module()


def _codex_pass_text(*, task_id, run_id, contract_digest, head=HEAD, version=1):
    return (
        '<!-- activity: type=review_verdict actor=Codex target=PM status=PASS -->\n'
        f'Verdict: PASS | Version: v{version} | HEAD: {head}\n'
        'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n'
        f'LLMTOOL-REVIEW-BINDING v1 task={task_id} run={run_id} '
        f'contract={contract_digest} head={head}'
    )


def _create_real_task(tmp_dir, *, executor_root):
    store = task_store.TaskStore(Path(tmp_dir).resolve() / 'tasks')
    task_id = task_store.new_id()
    run_id = task_store.new_id()
    request = 'Verify Codex verdict posting and merge gate.'
    contract = {
        'schema_version': 1,
        'contract_version': 1,
        'task_id': task_id,
        'goal': request,
        'authorized_scope': ['pm-planning'],
        'criteria': [{
            'criterion_id': 'pm_response',
            'description': 'Record result.',
            'kind': 'ARTIFACT',
        }],
        'forbidden_scope': [],
        'previous_digest': None,
    }
    parsed = task_store.parse_task_contract(json.dumps(contract))
    stamp = '2026-11-26T00:00:00Z'
    record = {
        'schema_version': 2,
        'task_id': task_id,
        'run_id': run_id,
        'pm_session_id': 'pm-' + PM_SESSION,
        'pm_provider_profile': 'test',
        'pm_provider_type': 'test',
        'pm_model': None,
        'pm_route_binding': {'type': 'test', 'model': None, 'base_url': None},
        'backend_session_semantics': 'NO_NATIVE_SESSION_ID_EXPOSED',
        'native_session_id': None,
        'state': 'PM_RESPONDED',
        'created_at': stamp,
        'updated_at': stamp,
        'task_contract': contract,
        'task_binding': vars(parsed.binding(run_id)),
        'request': request,
        'request_sha256': hashlib.sha256(request.encode('utf-8')).hexdigest(),
        'response': 'PM response.',
        'usage': None,
        'error_code': None,
        'developer': {
            'assignment_id': task_store.new_id(),
            'selection_source': 'PM',
            'state': 'DEVELOPER_RESPONDED',
            'provider_profile': 'claude',
            'provider_type': None,
            'model': None,
            'backend_session_semantics': 'NO_NATIVE_SESSION_ID_EXPOSED',
            'session_id': 'dev-' + task_store.new_id(),
            'source_sha': HEAD,
            'source_sha_binding': 'D6_REGISTRY_OBSERVED',
            'read_paths': ['tools/llmtool'],
            'write_paths': ['tools/llmtool'],
            'acceptance': ['Post Codex verdict before recording or merging.'],
            'context_sha256': 'f' * 64,
            'egress_basis': 'Bounded task context.',
            'input_bytes': 1,
            'max_tokens': 1000,
            'artifact_format': 'unified_diff',
            'artifact': None,
            'artifact_sha256': None,
            'usage': None,
            'error_code': None,
            'created_at': stamp,
            'owner_assignment_ref': None,
            'claude_result': {
                'executor_task_id': EXEC,
                'head_sha': HEAD,
                'recorded_at': stamp,
            },
        },
        'pm_assignment': {
            'schema_version': 1,
            'route': 'claude',
            'risk': 'LOW',
            'goal': request,
            'acceptance': ['Post Codex verdict before recording or merging.'],
        },
        'completion': {
            'state': 'WAITING_REVIEW',
            'executor_task_id': EXEC,
            'reviewer_task_id': None,
            'evidence': {
                'task_id': task_id,
                'run_id': run_id,
                'contract_digest': parsed.binding(run_id).contract_digest,
                'executor_identity': 'pm-deterministic',
                'executor_session_ref': 'exec-session',
                'executor_native_session_id': None,
                'executor_native_session': 'UNKNOWN',
                'execution_mode': 'DIRECT_D6',
                'root': executor_root,
                'common_dir': str(Path(executor_root) / '.git'),
                'branch': 'feat/test-2769',
                'source_sha': HEAD,
                'result': {'result_head': HEAD},
                'review_pr_ci': {'stage': 'READY'},
            },
            'missing_gates': [],
            'pm_dispatch': 'NOT_STARTED',
            'pm_response': None,
            'pm_recommendation': None,
            'pm_usage': None,
            'pm_error_code': None,
        },
        'remote_test_outcomes': [{
            'result': 'ADMISSION_DENIED',
            'code': 'MODULE_NOT_ADMITTED',
            'executor_task': EXEC,
            'sha': HEAD,
            'modules': [],
            'at': stamp,
        }],
        'published_pr': {'pr': PR_NUMBER, 'head_sha': HEAD, 'recorded_at': stamp},
    }
    store.create(record)
    return store, task_id


class CodexVerdictPost2769Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = str(Path(self.temp.name).resolve() / 'repo')
        Path(self.root).mkdir(parents=True, exist_ok=True)
        self.post_dir = Path(self.temp.name).resolve() / 'posts'
        self.store, self.task_id = _create_real_task(
            Path(self.temp.name) / 'store', executor_root=self.root)
        self.task = self.store.get(self.task_id)
        self.verdict_text = _codex_pass_text(
            task_id=self.task_id,
            run_id=self.task['run_id'],
            contract_digest=self.task['task_binding']['contract_digest'],
        )
        self.comments = []
        self.posts = []

        post_dir_patch = patch.object(
            review_dispatch, '_verdict_post_dir',
            lambda _store: review_dispatch._private_post_dir(self.post_dir))
        post_dir_patch.start()
        self.addCleanup(post_dir_patch.stop)

        env_patch = patch.dict(
            os.environ, {'CLAUDE_CODE_SESSION_ID': '', 'CODEX_THREAD_ID': ''})
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def _d6(self):
        return {
            'root': self.root,
            'common_dir': str(Path(self.root) / '.git'),
            'task_id': EXEC,
            'branch': 'feat/test-2769',
            'result_head': HEAD,
            'recorded_result_head': HEAD,
            'registered_result_head': HEAD,
            'dirty': False,
            'state': 'CLEAN',
        }

    def _pr(self):
        return {
            'number': PR_NUMBER,
            'head': {'sha': HEAD},
            'base': {'sha': MAIN_SHA},
            'state': 'open',
            'merged': False,
            'mergeable': True,
            'mergeable_state': 'clean',
            'labels': [{'name': 'mac-ci'}],
        }

    def _github_get(self, path):
        if path == 'branches/main':
            return {'commit': {'sha': MAIN_SHA}}
        if path.startswith('compare/'):
            return {'status': 'ahead'}
        if path.startswith('commits/') and path.endswith('/check-runs?per_page=100'):
            return {'total_count': 2, 'check_runs': [
                {'id': 1, 'name': 'check (Linux 3.14)', 'head_sha': HEAD,
                 'status': 'completed', 'conclusion': 'success'},
                {'id': 2, 'name': 'check (macOS 3.13)', 'head_sha': HEAD,
                 'status': 'completed', 'conclusion': 'success'},
            ]}
        if path.endswith('/files?per_page=100'):
            return [{'filename': 'tools/llmtool/llmtool/task_advance.py'}]
        if path.startswith(f'issues/{PR_NUMBER}/comments'):
            return list(self.comments)
        if path == f'pulls/{PR_NUMBER}':
            return self._pr()
        return []

    def _poster(self, _root, pr_number, body):
        self.posts.append((pr_number, body))
        cid = 900 + len(self.posts)
        comment = {
            'id': cid,
            'body': body,
            'user': {'login': LOGIN},
            'created_at': '2026-11-26T00:05:00Z',
            'updated_at': '2026-11-26T00:05:00Z',
        }
        self.comments.append(comment)
        return comment

    def _install_claim(self, claim):
        current = self.store.get(self.task_id)
        claimed = {
            key: value for key, value in claim.items()
            if key not in ('state', 'updated_at', 'review_result')
        }
        claimed['state'] = 'CLAIMED'
        claimed['native_session_id'] = None
        claimed['updated_at'] = claim['claimed_at']
        updated = self.store.compare_and_update(
            self.task_id,
            expected={'record_sha256': current['record_sha256']},
            review_dispatch_claim=[claimed],
        )
        self.store.compare_and_update(
            self.task_id,
            expected={'record_sha256': updated['record_sha256']},
            review_dispatch_claim=[claim],
        )
        return claim

    def _install_completed_codex_claim(self):
        current = self.store.get(self.task_id)
        claim = {
            'executor_task': EXEC,
            'pr': PR_NUMBER,
            'head_sha': HEAD,
            'version': 1,
            'state': 'COMPLETED',
            'reviewer_task': 'reviewer-codex-1',
            'native_session_id': 'codex-thread-1',
            'account_mode': 'CODEX_CLI',
            'claimed_at': '2026-11-26T00:01:00Z',
            'updated_at': '2026-11-26T00:02:00Z',
            'review_result': {
                'task_id': self.task_id,
                'run_id': current['run_id'],
                'contract_digest': current['task_binding']['contract_digest'],
                'head': HEAD,
                'verdict': 'PASS',
                'review_text': self.verdict_text,
            },
        }
        return self._install_claim(claim)

    def test_manual_review_dispatch_posts_caller_verdict_and_prints_real_state(self):
        claim = self._install_completed_codex_claim()
        now = 1000.0
        verdict_post, _ = review_dispatch._reserve_caller_post(
            store=self.store, task_id=self.task_id, claim=claim,
            text=self.verdict_text, clock=lambda: now)
        self.assertEqual(verdict_post, {'state': 'CALLER_POSTING'})

        dispatch_output = dict(claim, verdict='PASS', review_text=self.verdict_text,
                               verdict_post=verdict_post)
        dispatch_output.pop('review_result', None)

        args = SimpleNamespace(
            task_id=self.task_id, executor_task=EXEC, pr=PR_NUMBER,
            version=1, reviewer='codex-reviewer',
            pm_session=self.task['pm_session_id'])
        out = io.StringIO()
        with patch.object(review_dispatch.worker_dispatch, 'evaluate',
                          return_value=SimpleNamespace(allowed=True, reason_code='')):
            rc = task_submission_cli.run_task_review_dispatch(
                args, store=self.store, stdout=out,
                github_get=self._github_get,
                comment_poster=self._poster,
                login_resolver=lambda: LOGIN,
                post_clock=lambda: now,
                dispatch_review_call=lambda **_kw: dict(dispatch_output),
            )
        self.assertIsNone(rc)
        printed = json.loads(out.getvalue().strip())
        self.assertEqual(printed['verdict_post'], {'state': 'POSTED', 'comment_id': 901})
        self.assertEqual(self.posts, [(PR_NUMBER, self.verdict_text)])

        # Second caller-post sees ALREADY_POSTED and posts nothing new.
        out2 = io.StringIO()
        with patch.object(review_dispatch.worker_dispatch, 'evaluate',
                          return_value=SimpleNamespace(allowed=True, reason_code='')):
            rc2 = task_submission_cli.run_task_review_dispatch(
                args, store=self.store, stdout=out2,
                github_get=self._github_get,
                comment_poster=self._poster,
                login_resolver=lambda: LOGIN,
                post_clock=lambda: now + 5,
                dispatch_review_call=lambda **_kw: dict(dispatch_output),
            )
        self.assertIsNone(rc2)
        printed2 = json.loads(out2.getvalue().strip())
        self.assertEqual(printed2['verdict_post'],
                         {'state': 'ALREADY_POSTED', 'comment_id': 901})
        self.assertEqual(len(self.posts), 1)

        # Other results (non-caller-post, e.g. Claude DISPATCHED) print as before.
        claude_res = {'state': 'DISPATCHED', 'reviewer_task': 'reviewer-claude-1'}
        out3 = io.StringIO()
        rc3 = task_submission_cli.run_task_review_dispatch(
            args, store=self.store, stdout=out3,
            dispatch_review_call=lambda **_kw: dict(claude_res),
        )
        self.assertIsNone(rc3)
        self.assertEqual(json.loads(out3.getvalue().strip()), claude_res)

    def test_manual_review_dispatch_prints_failure_state_on_post_error(self):
        claim = self._install_completed_codex_claim()
        now = 1000.0
        verdict_post, _ = review_dispatch._reserve_caller_post(
            store=self.store, task_id=self.task_id, claim=claim,
            text=self.verdict_text, clock=lambda: now)
        dispatch_output = dict(claim, verdict='PASS', review_text=self.verdict_text,
                               verdict_post=verdict_post)
        dispatch_output.pop('review_result', None)

        args = SimpleNamespace(
            task_id=self.task_id, executor_task=EXEC, pr=PR_NUMBER,
            version=1, reviewer='codex-reviewer',
            pm_session=self.task['pm_session_id'])
        out = io.StringIO()

        def failing_poster(_root, _pr, _body):
            raise review_dispatch._PostError('VERDICT_POST_COMMAND_FAILED')

        with patch.object(review_dispatch.worker_dispatch, 'evaluate',
                          return_value=SimpleNamespace(allowed=True, reason_code='')):
            rc = task_submission_cli.run_task_review_dispatch(
                args, store=self.store, stdout=out,
                github_get=self._github_get,
                comment_poster=failing_poster,
                login_resolver=lambda: LOGIN,
                post_clock=lambda: now,
                dispatch_review_call=lambda **_kw: dict(dispatch_output),
            )
        self.assertEqual(rc, 1)
        printed = json.loads(out.getvalue().strip())
        self.assertEqual(printed['verdict_post'],
                         {'state': 'FAILED', 'code': 'VERDICT_POST_COMMAND_FAILED'})

    def test_record_review_result_on_unposted_completed_claim_posts_then_records_nonzero_id(self):
        self._install_completed_codex_claim()
        with patch.object(task_advance, '_d6_snapshot', return_value=self._d6()), \
                patch.object(task_advance, '_pr_snapshot', return_value=self._pr()), \
                patch.object(review_dispatch, '_resolve_github_login', return_value=LOGIN), \
                patch.object(review_dispatch, '_create_issue_comment', side_effect=self._poster), \
                patch.object(review_dispatch.worker_dispatch, 'evaluate',
                             return_value=SimpleNamespace(allowed=True, reason_code='')):
            res = task_advance.advance(
                task_id=self.task_id, store=self.store, pr_number=PR_NUMBER,
                github_get=self._github_get)
        self.assertEqual(res['step'], 'RECORD_REVIEW_RESULT')
        self.assertEqual(res['codes'], [])
        self.assertEqual(res['result']['state'], 'REVIEW_PASS')
        self.assertEqual(res['result']['comment_id'], 901)
        self.assertGreater(res['result']['comment_id'], 0)
        self.assertEqual(self.posts, [(PR_NUMBER, self.verdict_text)])

        # Repeat advance sees the recorded event and posts nothing new, launches no new review.
        with patch.object(task_advance, '_d6_snapshot', return_value=self._d6()), \
                patch.object(task_advance, '_pr_snapshot', return_value=self._pr()), \
                patch.object(review_dispatch, 'dispatch_review',
                             side_effect=AssertionError('must not launch new review')):
            next_res = task_advance.advance(
                task_id=self.task_id, store=self.store, pr_number=PR_NUMBER,
                github_get=self._github_get,
                run_task_complete_call=lambda *_a, **_kw: 0)
        self.assertEqual(next_res['step'], 'RUN_TASK_COMPLETE')
        self.assertEqual(len(self.posts), 1)

    def test_merge_gate_refuses_unposted_codex_pass_and_accepts_posted_one(self):
        self._install_completed_codex_claim()
        # Even if a stale REVIEW_PASS event with comment_id=0 exists, unposted PASS is refused.
        identity_zero = {
            'task': self.task_id, 'run': self.task['run_id'], 'head': HEAD,
            'version': 1, 'outcome': 'REVIEW_PASS', 'comment_id': 0,
        }
        event_id_zero = hashlib.sha256(json.dumps(
            identity_zero, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()[:32]
        payload_zero = hashlib.sha256(json.dumps(
            {'outcome': 'REVIEW_PASS', 'head': HEAD, 'version': 1,
             'comment_id': 0, 'findings_count': 0},
            sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
        self.store.record_completion_event(
            self.task_id, event_id=event_id_zero, run_id=self.task['run_id'],
            session_id='review-' + event_id_zero, source='REVIEWER',
            source_sha=HEAD, sequence=0, payload_sha256=payload_zero,
            review_kind='formal')

        merges = []

        def gh_runner(_root, args):
            if args[:2] == ['pr', 'merge']:
                merges.append(args)
            return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')

        with patch.object(task_advance, '_d6_snapshot', return_value=self._d6()), \
                patch.object(task_advance, '_pr_snapshot', return_value=self._pr()), \
                patch.object(task_advance.github_identity, 'ai_login', return_value=LOGIN):
            refused = task_advance.advance(
                task_id=self.task_id, store=self.store, pr_number=PR_NUMBER,
                merge=True, github_get=self._github_get, merge_gh_runner=gh_runner)
        self.assertEqual(refused['step'], 'MERGE_REFUSED')
        self.assertEqual(refused['codes'], ['MERGE_REVIEW_VERDICT_NOT_POSTED'])
        self.assertEqual(merges, [])

        # A copy posted by another GitHub login is still refused.
        self.comments.append({
            'id': 900,
            'body': self.verdict_text,
            'user': {'login': 'other-user'},
            'created_at': '2026-11-26T00:05:00Z',
            'updated_at': '2026-11-26T00:05:00Z',
        })
        with patch.object(task_advance, '_d6_snapshot', return_value=self._d6()), \
                patch.object(task_advance, '_pr_snapshot', return_value=self._pr()), \
                patch.object(task_advance.github_identity, 'ai_login', return_value=LOGIN):
            copy_refused = task_advance.advance(
                task_id=self.task_id, store=self.store, pr_number=PR_NUMBER,
                merge=True, github_get=self._github_get, merge_gh_runner=gh_runner)
        self.assertEqual(copy_refused['step'], 'MERGE_REFUSED')
        self.assertEqual(copy_refused['codes'], ['MERGE_REVIEW_VERDICT_NOT_POSTED'])
        self.assertEqual(merges, [])

        # Once the exact comment is posted by the verified posting identity, the merge gate accepts it.
        self._poster(self.root, PR_NUMBER, self.verdict_text)
        with patch.object(task_advance, '_d6_snapshot', return_value=self._d6()), \
                patch.object(task_advance, '_pr_snapshot', return_value=self._pr()), \
                patch.object(task_advance.github_identity, 'ai_login', return_value=LOGIN):
            merged = task_advance.advance(
                task_id=self.task_id, store=self.store, pr_number=PR_NUMBER,
                merge=True, github_get=self._github_get, merge_gh_runner=gh_runner)
        self.assertEqual(merged['step'], 'MERGED')
        self.assertEqual(merged['codes'], [])
        self.assertEqual(len(merges), 1)

    def test_claude_reviewer_dispatched_claim_is_unchanged(self):
        claude_claim = {
            'executor_task': EXEC,
            'pr': PR_NUMBER,
            'head_sha': HEAD,
            'version': 1,
            'state': 'DISPATCHED',
            'reviewer_task': 'reviewer-claude-1',
            'native_session_id': 'claude-session-1',
            'account_mode': 'ROLES',
            'claimed_at': '2026-11-26T00:01:00Z',
            'updated_at': '2026-11-26T00:02:00Z',
        }
        self._install_claim(claude_claim)
        identity = {
            'task': self.task_id, 'run': self.task['run_id'], 'head': HEAD,
            'version': 1, 'outcome': 'REVIEW_PASS', 'comment_id': 77,
        }
        event_id = hashlib.sha256(json.dumps(
            identity, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()[:32]
        payload = hashlib.sha256(json.dumps(
            {'outcome': 'REVIEW_PASS', 'head': HEAD, 'version': 1,
             'comment_id': 77, 'findings_count': 0},
            sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
        self.store.record_completion_event(
            self.task_id, event_id=event_id, run_id=self.task['run_id'],
            session_id='review-' + event_id, source='REVIEWER',
            source_sha=HEAD, sequence=0, payload_sha256=payload,
            review_kind='formal')
        merges = []

        def gh_runner(_root, args):
            if args[:2] == ['pr', 'merge']:
                merges.append(args)
            return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')

        with patch.object(task_advance, '_d6_snapshot', return_value=self._d6()), \
                patch.object(task_advance, '_pr_snapshot', return_value=self._pr()), \
                patch.object(task_advance, '_review_outcomes', return_value=[{
                    'source': 'REVIEWER', 'source_sha': HEAD,
                    'version': 1, 'outcome': 'REVIEW_PASS'}]):
            merged = task_advance.advance(
                task_id=self.task_id, store=self.store, pr_number=PR_NUMBER,
                merge=True, github_get=self._github_get, merge_gh_runner=gh_runner)
        self.assertEqual(merged['step'], 'MERGED')
        self.assertEqual(len(merges), 1)


if __name__ == '__main__':
    unittest.main()
