"""Offline Claude Reviewer route: exact-HEAD verdict to `task complete` (Issue #352).

Every agent here publishes to GitHub as one account, and GitHub refuses APPROVE
from a PR's own author, so a separate read-only Claude Reviewer posts its
verdict as a PR comment. These tests use a fake `gh` and fake GitHub replies;
no model, network or real GitHub call is made.
"""
import json
import os
import stat
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from llmtool.errors import LLMToolError
from llmtool.providers.base import Reply
from llmtool.task_store import TaskStore
from llmtool.task_submission_cli import run_task_complete
from llmtool import github_identity, task_completion

import test_task_completion as fixtures
import review_trust_fixture


def setUpModule(): review_trust_fixture.start_module()
def tearDownModule(): review_trust_fixture.stop_module()


ACCEPT = 'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT'


def verdict_head(head, status='PASS', actor='Claude', version='v1'):
    """First two lines of the standard formal verdict comment."""
    return (f'<!-- activity: type=review_verdict actor={actor} target=PM status={status} -->\n'
            f'Verdict: {status} | Version: {version} | HEAD: {head}')


class FakeGhTests(unittest.TestCase):
    """`_github_get` reads the private repository through the signed-in gh CLI."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.log = self.base / 'gh.log'

    def _fake_gh(self, body, *, exit_code=0, name='gh'):
        gh = self.base / name
        gh.write_text('#!/bin/sh\n'
                      f'printf "%s\\n" "$@" > {self.log}\n'
                      f'env >> {self.log}\n'
                      f'cat <<\'EOF\'\n{body}\nEOF\n'
                      f'exit {exit_code}\n')
        gh.chmod(0o700)
        return str(gh)

    def _get(self, path, gh):
        hostile = {'GH_TOKEN': 'gh-token-value', 'GITHUB_TOKEN': 'github-token-value',
                   'HTTPS_PROXY': 'http://proxy.invalid', 'GH_HOST': 'evil.invalid',
                   'GH_CONFIG_DIR': str(self.base / 'gh-config')}
        with patch.object(task_completion.shutil, 'which', return_value=gh) as which, \
                patch.dict(os.environ, hostile):
            try:
                return task_completion._github_get(path)
            finally:
                self.which_calls = which.call_args_list

    def test_reads_fixed_repository_path_without_ambient_credentials(self):
        home = str(self.base / 'home')
        with patch.dict(os.environ, {'HOME': home}):
            value = self._get('pulls/10', self._fake_gh('{"number": 10}'))
        self.assertEqual(value, {'number': 10})
        logged = self.log.read_text()
        argv = logged.split('\n')
        self.assertEqual(argv[:8], ['api', '--method', 'GET',
            '-H', 'Accept: application/vnd.github+json',
            '-H', 'X-GitHub-Api-Version: 2022-11-28',
            'repos/davidcatcat/davidcattoolsproject/pulls/10'])
        for leaked in ('gh-token-value', 'github-token-value', 'proxy.invalid',
                       'evil.invalid'):
            self.assertNotIn(leaked, logged)
        self.assertIn('GH_PROMPT_DISABLED=1', logged)
        self.assertIn('GH_CONFIG_DIR=' + github_identity.ai_gh_config_dir(home), logged)
        self.assertEqual(self.which_calls[0].kwargs['path'], task_completion._GH_PATH)

    def test_failures_are_fail_closed_codes(self):
        cases = {
            'GITHUB_EVIDENCE_UNAVAILABLE': ('pulls/10', self._fake_gh('{}', exit_code=1, name='gh-fail')),
            'GITHUB_EVIDENCE_INVALID': ('pulls/10', self._fake_gh('not json', name='gh-text')),
        }
        for code, (path, gh) in cases.items():
            with self.subTest(code=code), self.assertRaises(LLMToolError) as caught:
                self._get(path, gh)
            self.assertEqual(caught.exception.completion_code, code)
        with patch.object(task_completion.shutil, 'which', return_value=None), \
                self.assertRaises(LLMToolError) as caught:
            task_completion._github_get('pulls/10')
        self.assertEqual(caught.exception.completion_code, 'GITHUB_EVIDENCE_UNAVAILABLE')

    def test_rejects_unbounded_output_and_paths_outside_the_repository(self):
        big = self.base / 'big'
        big.write_bytes(b'[' + b'0,' * (600 * 1024) + b'0]')
        gh = self.base / 'gh-big'
        gh.write_text(f'#!/bin/sh\ncat {big}\n')
        gh.chmod(0o700)
        with self.assertRaises(LLMToolError) as caught:
            self._get('pulls/10', str(gh))
        self.assertEqual(caught.exception.completion_code, 'GITHUB_EVIDENCE_TOO_LARGE')
        gh = self._fake_gh('{}')
        for path in ('/user', '../../user', 'pulls/10 --hostname x', 'pulls/10\n', ''):
            with self.subTest(path=path), self.assertRaises(LLMToolError):
                self._get(path, gh)
        self.assertFalse(self.log.exists())


class ClaudeReviewerVerdictTests(unittest.TestCase):
    """A comment verdict from the registered Reviewer login satisfies the formal gate."""

    def setUp(self):
        self.f = fixtures.TaskCompletionTests('test_failed_test_subprocess_is_recorded_with_nonzero_status')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.result_sha, _ = self.f._make_result()
        self.reviewer_root = self.f.base / 'reviewer'
        self.f._git('worktree', 'add', '-b', 'review/branch', str(self.reviewer_root),
                    self.result_sha)
        self.f._write_registration('reviewer-task', 'claude-reviewer', 'claude-reviewer-session',
            self.reviewer_root, 'review/branch', self.result_sha, self.result_sha,
            native_session_id='native-reviewer', github_login='davidcatcat',
            state='REVIEW_PENDING')
        # The PR author is the same account as the Reviewer: no PR review exists.
        self.pr = {'state': 'open', 'merged': False, 'head': {'sha': self.result_sha},
                   'base': {'repo': {'full_name': 'davidcatcat/davidcattoolsproject'}},
                   'user': {'login': 'davidcatcat'}}
        self.checks = {'total_count': 1, 'check_runs': [
            {'id': 1, 'name': 'check (macOS 3.13)', 'head_sha': self.result_sha,
             'status': 'completed', 'conclusion': 'success'}]}
        self.binding = self.f._review_binding(head=self.result_sha)

    def _head(self, **kwargs):
        return verdict_head(self.result_sha, **kwargs)

    def _comment(self, body, *, comment_id=1, at='2026-10-25T10:00:00Z', login='davidcatcat',
                 updated=None):
        return {'id': comment_id, 'user': {'login': login}, 'created_at': at,
                'updated_at': at if updated is None else updated, 'body': body}

    def _collect(self, comments, reviews=(), **overrides):
        api = self.f._github_response_sequence(self.result_sha, self.pr, list(reviews),
                                               self.checks, comments=comments)
        with patch('llmtool.task_completion._github_get', side_effect=api) as get:
            result = task_completion.collect_github_evidence(
                **self.f._github_kwargs(self.result_sha, **overrides))
        self.assertEqual(get.call_args_list[2].args[0], 'issues/10/comments?per_page=100')
        return result

    def test_bound_verdict_comment_is_accepted(self):
        body = '\n'.join((self._head(), ACCEPT, self.binding))
        result = self._collect([self._comment('PM note, not a verdict', comment_id=1),
                                self._comment(body, comment_id=2)])
        self.assertEqual(result['missing_gates'], [])
        evidence = result['evidence']
        self.assertEqual(evidence['formal_review_state'], 'ACCEPTED')
        self.assertEqual(evidence['review_source'], 'PR_COMMENT')
        self.assertEqual(evidence['review_actor'], 'davidcatcat')
        self.assertEqual(evidence['review_commit_sha'], self.result_sha)
        self.assertTrue(evidence['review_binding_verified'])

    def test_only_the_exact_standard_claude_pass_form_accepts(self):
        accept = lambda head: self._comment('\n'.join((head, ACCEPT, self.binding)))
        self.assertEqual(self._collect([accept(self._head())])['missing_gates'], [])
        for label, head in {
            'no version line': '<!-- activity: type=review_verdict actor=Claude target=PM '
                               'status=PASS -->\nVerdict: PASS | HEAD: ' + self.result_sha,
            'status mismatch': self._head().replace('Verdict: PASS', 'Verdict: FIX'),
            'unknown actor': self._head(actor='OpenCode'),
            'extra marker field': self._head().replace('status=PASS', 'status=PASS extra=1'),
            'version zero': self._head(version='v0'),
            'FIX status': self._head(status='FIX'),
            'FAIL status': self._head(status='FAIL'),
            'Codex FIX': self._head(actor='Codex', status='FIX'),
            'leading space on marker': ' ' + self._head(),
            'trailing space on marker': self._head().replace(' -->\n', ' --> \n', 1),
            'leading space on Verdict line': self._head().replace('\nVerdict:', '\n Verdict:', 1),
            'trailing space on Verdict line': self._head() + ' ',
            'tab before Verdict line': self._head().replace('\nVerdict:', '\n\tVerdict:', 1),
        }.items():
            with self.subTest(label):
                result = self._collect([accept(head)])
                self.assertIn('FORMAL_REVIEW_NOT_ACCEPTED', result['missing_gates'])
                self.assertEqual(result['evidence']['formal_review_state'], 'UNVERIFIED')
        # Advisory Codex PASS neither accepts nor overrides a Claude PASS.
        codex_pass = self._comment('\n'.join((self._head(actor='Codex'), ACCEPT, self.binding)),
                                   comment_id=2, at='2026-10-25T11:00:00Z')
        self.assertIn('FORMAL_REVIEW_MISSING', self._collect([codex_pass])['missing_gates'])
        result = self._collect([accept(self._head()), codex_pass])
        self.assertEqual(result['missing_gates'], [])
        # A later standard Codex FIX does override it.
        codex_fix = self._comment(self._head(actor='Codex', status='FIX'),
                                  comment_id=3, at='2026-10-25T12:00:00Z')
        result = self._collect([accept(self._head()), codex_fix])
        self.assertIn('FORMAL_REVIEW_NOT_ACCEPTED', result['missing_gates'])
        # A padded Codex PASS is not the advisory standard form, so it fails closed.
        padded_codex = self._comment(' ' + self._head(actor='Codex'),
                                     comment_id=4, at='2026-10-25T12:00:00Z')
        result = self._collect([accept(self._head()), padded_codex])
        self.assertIn('FORMAL_REVIEW_NOT_ACCEPTED', result['missing_gates'])

    def test_comments_that_are_not_bound_reviewer_verdicts_are_ignored(self):
        other_head = self.f._review_binding(head='f' * 40)
        cases = {
            'no activity line': '\n'.join((ACCEPT, self.binding)),
            'activity not first': '\n'.join(('hello', self._head(), ACCEPT, self.binding)),
            'other activity type': '\n'.join((
                '<!-- activity: type=review_gate actor=PM target=PR-10 status=PASS -->',
                ACCEPT, self.binding)),
            'other head': '\n'.join((verdict_head('f' * 40), ACCEPT, other_head)),
        }
        for label, body in cases.items():
            with self.subTest(label):
                result = self._collect([self._comment(body)])
                self.assertIn('FORMAL_REVIEW_MISSING', result['missing_gates'])
                self.assertEqual(result['evidence']['formal_review_state'], 'UNVERIFIED')
        with self.subTest('other login'):
            body = '\n'.join((self._head(), ACCEPT, self.binding))
            result = self._collect([self._comment(body, login='someone-else')])
            self.assertIn('FORMAL_REVIEW_MISSING', result['missing_gates'])

    def test_verdict_without_accept_marker_or_binding_is_not_accepted(self):
        result = self._collect([self._comment('\n'.join((self._head(status='FIX'), self.binding)))])
        self.assertIn('FORMAL_REVIEW_NOT_ACCEPTED', result['missing_gates'])
        result = self._collect([self._comment(
            '\n'.join((self._head(), ACCEPT, 'HEAD: ' + self.result_sha)))])
        self.assertIn('FORMAL_REVIEW_BINDING_MISMATCH', result['missing_gates'])
        self.assertEqual(result['evidence']['formal_review_state'], 'UNVERIFIED')

    def test_binding_for_another_task_run_or_contract_is_not_accepted(self):
        for override in ({'task_id': '9' * 32}, {'run_id': '9' * 32},
                         {'contract_digest': '9' * 64}):
            other = self.f._review_binding(head=self.result_sha, **override)
            with self.subTest(override=override):
                result = self._collect([self._comment('\n'.join((self._head(), ACCEPT, other)))])
                self.assertIn('FORMAL_REVIEW_BINDING_MISMATCH', result['missing_gates'])
                self.assertEqual(result['evidence']['formal_review_state'], 'UNVERIFIED')

    def test_latest_reviewer_disposition_wins(self):
        accept = self._comment('\n'.join((self._head(), ACCEPT, self.binding)),
                               comment_id=1, at='2026-10-25T10:00:00Z')
        fix = self._comment('\n'.join((
            '<!-- activity: type=review_verdict actor=Claude target=PM status=FIX -->',
            'Verdict: FIX | HEAD: ' + self.result_sha)), comment_id=2, at='2026-10-25T11:00:00Z')
        result = self._collect([fix, accept])
        self.assertIn('FORMAL_REVIEW_NOT_ACCEPTED', result['missing_gates'])
        self.assertEqual(result['evidence']['formal_review_state'], 'UNVERIFIED')
        # A later PR review disposition supersedes an earlier comment verdict.
        changes = [{'state': 'CHANGES_REQUESTED', 'commit_id': self.result_sha,
                    'user': {'login': 'davidcatcat'}, 'submitted_at': '2026-10-25T12:00:00Z',
                    'body': 'needs work'}]
        result = self._collect([accept], reviews=changes)
        self.assertIn('FORMAL_REVIEW_NOT_ACCEPTED', result['missing_gates'])
        # A later accepted comment supersedes an earlier PR review.
        older = [{**changes[0], 'state': 'DISMISSED', 'submitted_at': '2026-10-25T09:00:00Z'}]
        result = self._collect([accept], reviews=older)
        self.assertEqual(result['missing_gates'], [])
        self.assertEqual(result['evidence']['review_source'], 'PR_COMMENT')

    def test_edited_verdict_comment_does_not_accept(self):
        body = '\n'.join((self._head(), ACCEPT, self.binding))
        # An earlier FIX later edited into a standard Claude PASS is not accepted.
        edited = self._comment(body, at='2026-10-25T10:00:00Z', updated='2026-10-25T11:00:00Z')
        result = self._collect([edited])
        self.assertIn('FORMAL_REVIEW_NOT_ACCEPTED', result['missing_gates'])
        self.assertEqual(result['evidence']['formal_review_state'], 'UNVERIFIED')
        # It is still a disposition: it supersedes an earlier unedited PASS.
        earlier = self._comment(body, comment_id=0, at='2026-10-25T09:00:00Z')
        result = self._collect([earlier, edited])
        self.assertIn('FORMAL_REVIEW_NOT_ACCEPTED', result['missing_gates'])
        # An older FIX edited after a newer PASS is ordered by its edit time,
        # so it supersedes that PASS; also against a PR review in between.
        fix = self._comment(self._head(status='FIX'), comment_id=1,
                            at='2026-10-25T09:00:00Z', updated='2026-10-25T12:00:00Z')
        newer_pass = self._comment(body, comment_id=2, at='2026-10-25T10:00:00Z')
        for comments in ([fix, newer_pass], [newer_pass, fix]):
            with self.subTest(order=[c['id'] for c in comments]):
                result = self._collect(comments)
                self.assertIn('FORMAL_REVIEW_NOT_ACCEPTED', result['missing_gates'])
                self.assertEqual(result['evidence']['formal_review_state'], 'UNVERIFIED')
        approved = [{'state': 'APPROVED', 'commit_id': self.result_sha,
                     'user': {'login': 'davidcatcat'}, 'submitted_at': '2026-10-25T11:00:00Z',
                     'body': '\n'.join((ACCEPT, self.binding))}]
        result = self._collect([fix], reviews=approved)
        self.assertIn('FORMAL_REVIEW_NOT_ACCEPTED', result['missing_gates'])
        self.assertEqual(result['evidence']['review_source'], 'PR_COMMENT')
        # An unedited later PASS still wins over the edited FIX.
        latest_pass = self._comment(body, comment_id=3, at='2026-10-25T13:00:00Z')
        self.assertEqual(self._collect([fix, latest_pass])['missing_gates'], [])
        # A missing or malformed edit time fails closed.
        for updated in (None, 'not-a-time'):
            with self.subTest(updated=updated):
                comment = self._comment(body)
                comment['updated_at'] = updated
                with self.assertRaises(LLMToolError) as caught:
                    self._collect([comment])
                self.assertEqual(caught.exception.completion_code, 'REVIEW_EVIDENCE_INVALID')

    def test_same_timestamp_review_and_comment_fail_closed(self):
        at = '2026-10-25T10:00:00Z'
        approved = [{'state': 'APPROVED', 'commit_id': self.result_sha,
                     'user': {'login': 'davidcatcat'}, 'submitted_at': at,
                     'body': '\n'.join((ACCEPT, self.binding))}]
        fix = self._comment('\n'.join((
            '<!-- activity: type=review_verdict actor=Claude target=PM status=FIX -->',
            'Verdict: FIX | HEAD: ' + self.result_sha)), at=at)
        result = self._collect([fix], reviews=approved)
        self.assertIn('FORMAL_REVIEW_NOT_ACCEPTED', result['missing_gates'])
        self.assertEqual(result['evidence']['formal_review_state'], 'UNVERIFIED')
        # Even two acceptances at the same second are not ordered, so not accepted.
        accept = self._comment('\n'.join((self._head(), ACCEPT, self.binding)), at=at)
        result = self._collect([accept], reviews=approved)
        self.assertIn('FORMAL_REVIEW_NOT_ACCEPTED', result['missing_gates'])

    def test_same_second_edited_fix_and_new_pass_fail_closed(self):
        body = '\n'.join((self._head(), ACCEPT, self.binding))
        # An older FIX edited in the same second a newer PASS was posted: the
        # PASS has the larger id, but that is creation order, not edit order.
        fix = self._comment(self._head(status='FIX'), comment_id=1,
                            at='2026-10-25T09:00:00Z', updated='2026-10-25T10:00:00Z')
        new_pass = self._comment(body, comment_id=2, at='2026-10-25T10:00:00Z')
        for comments in ([fix, new_pass], [new_pass, fix]):
            with self.subTest(order=[c['id'] for c in comments]):
                result = self._collect(comments)
                self.assertIn('FORMAL_REVIEW_NOT_ACCEPTED', result['missing_gates'])
                self.assertEqual(result['evidence']['formal_review_state'], 'UNVERIFIED')
        # Same-second verdicts with the same outcome keep the id order.
        twin = self._comment(body, comment_id=3, at='2026-10-25T10:00:00Z')
        self.assertEqual(self._collect([new_pass, twin])['missing_gates'], [])
        other_fix = self._comment(self._head(status='FIX'), comment_id=4,
                                  at='2026-10-25T10:00:00Z')
        result = self._collect([fix, other_fix])
        self.assertIn('FORMAL_REVIEW_NOT_ACCEPTED', result['missing_gates'])

    def test_shared_login_comment_is_accepted_only_through_a_distinct_d6_reviewer(self):
        # Documents the limitation: any agent using the shared account can post
        # this exact comment. The only control is the D6 Reviewer record.
        body = '\n'.join((self._head(), ACCEPT, self.binding))
        self.assertEqual(self._collect([self._comment(body)])['missing_gates'], [])
        api = self.f._github_response_sequence(self.result_sha, self.pr, [], self.checks,
                                               comments=[self._comment(body)])
        with patch('llmtool.task_completion._github_get', side_effect=api), \
                self.assertRaises(LLMToolError) as caught:
            task_completion.collect_github_evidence(
                **self.f._github_kwargs(self.result_sha, reviewer_task_id='no-reviewer'))
        self.assertEqual(caught.exception.completion_code, 'REVIEWER_D6_REGISTRY_UNAVAILABLE')
        result = self._collect([self._comment(body)], executor_identity='claude-reviewer')
        self.assertIn('REVIEWER_D6_SESSION_NOT_INDEPENDENT', result['missing_gates'])
        self.assertEqual(result['evidence']['formal_review_state'], 'UNVERIFIED')
        result = self._collect([self._comment(body)],
                               executor_native_session_id='native-reviewer')
        self.assertIn('REVIEWER_NATIVE_SESSION_NOT_INDEPENDENT', result['missing_gates'])
        self.f._write_registration('reviewer-task', 'claude-reviewer', 'claude-reviewer-session',
            self.reviewer_root, 'review/branch', self.result_sha, self.result_sha,
            native_session_id='native-reviewer', state='REVIEW_PENDING')
        result = self._collect([self._comment(body)])
        self.assertIn('REVIEWER_ACTOR_D6_BINDING_MISSING', result['missing_gates'])
        self.assertIn('FORMAL_REVIEW_MISSING', result['missing_gates'])

    def test_independence_and_comment_shape_gates_still_apply(self):
        body = '\n'.join((self._head(), ACCEPT, self.binding))
        result = self._collect([self._comment(body)],
                               executor_session_ref='claude-reviewer-session')
        self.assertIn('REVIEWER_D6_SESSION_NOT_INDEPENDENT', result['missing_gates'])
        result = self._collect([self._comment(body)], executor_native_session_id=None)
        self.assertIn('REVIEWER_NATIVE_SESSION_NOT_INDEPENDENT', result['missing_gates'])
        for comments in ([self._comment(body)] * 100, [self._comment(body, comment_id='1')],
                         [self._comment(body, at='yesterday')], ['not a comment']):
            api = self.f._github_response_sequence(self.result_sha, self.pr, [], self.checks,
                                                   comments=comments)
            with patch('llmtool.task_completion._github_get', side_effect=api), \
                    self.assertRaises(LLMToolError) as caught:
                task_completion.collect_github_evidence(**self.f._github_kwargs(self.result_sha))
            self.assertEqual(caught.exception.completion_code, 'REVIEW_EVIDENCE_INVALID')

    def test_task_complete_accepts_the_comment_verdict_and_returns_it_to_the_pm(self):
        store = TaskStore(self.f.base / 'tasks')
        task = self.f._task()
        task['completion']['evidence'].update(
            executor_native_session_id='native-executor', executor_native_session='VERIFIED')
        store.create(task)
        binding = self.f._review_binding(task_id=task['task_id'], run_id=task['run_id'],
            contract_digest=task['task_binding']['contract_digest'], head=self.result_sha)
        comments = [self._comment('\n'.join((self._head(), ACCEPT, binding)))]
        api = [{'ref': 'refs/heads/main',
                'object': {'type': 'commit', 'sha': self.f.source_sha}}]
        api.extend(self.f._github_response_sequence(self.result_sha, self.pr, [], self.checks,
                                                   comments=comments))
        git = {'task_id': task['task_id'], 'run_id': task['run_id'],
               'executor_task_id': self.f.task_id, 'executor_identity': 'executor-agent',
               'executor_session_ref': 'Executor D6 session', 'root': str(self.f.root),
               'common_dir': self.f.common, 'branch': 'feat/executor',
               'source_sha': self.f.source_sha, 'result_head': self.result_sha,
               'changed_paths': [self.f.source_path], 'diff_sha256': '5' * 64}
        tests = [{'path': self.f.source_path, 'source_sha': self.result_sha,
                  'exit_code': 0, 'output_sha256': '6' * 64}]
        client = fixtures.FakeClient(Reply(json.dumps({'schema_version': 1,
            'decision': 'ACCEPT', 'summary': 'Reviewed exact HEAD; ready to merge.'}),
            {'prompt_tokens': 5, 'completion_tokens': 3, 'total_tokens': 8}))
        args = Namespace(task_id=task['task_id'], executor_task=self.f.task_id,
                         reviewer_task='reviewer-task', pr=10)
        with patch('llmtool.task_submission_cli.task_completion.collect_executor_result',
                   return_value=git), \
             patch('llmtool.task_submission_cli.task_completion.run_bounded_tests',
                   return_value=tests), \
             patch('llmtool.task_completion._github_get', side_effect=api) as github_get, \
             patch('llmtool.task_store.default_root', return_value=store.root), \
             patch('llmtool.task_submission_cli.resolve_profile_config',
                   return_value=fixtures.pm_config('original-pm')), \
             patch('llmtool.task_submission_cli.api_key', return_value='key-not-recorded'), \
             patch('llmtool.task_submission_cli.selected_provider', return_value={
                   'type': 'openai-compatible', 'model': 'pm-model', 'base_url': None}), \
             patch('llmtool.task_submission_cli.create_client', return_value=client), \
             redirect_stdout(StringIO()):
            self.assertEqual(run_task_complete(args, store=store), 0)
        self.assertEqual(github_get.call_args_list[0].args[0], 'git/ref/heads/main')
        completion = store.get(task['task_id'])['completion']
        self.assertEqual(completion['missing_gates'], [])
        self.assertEqual(completion['reviewer_task_id'], 'reviewer-task')
        review = completion['evidence']['review_pr_ci']
        self.assertEqual(review['formal_review_state'], 'ACCEPTED')
        self.assertEqual(review['review_source'], 'PR_COMMENT')
        self.assertEqual(review['review_commit_sha'], self.result_sha)
        packet = json.loads(client.calls[0][1]['content'])
        self.assertEqual(packet['review_pr_ci']['formal_review_state'], 'ACCEPTED')
        self.assertEqual(packet['result_head'], self.result_sha)


class LifecycleReviewerBindingTests(unittest.TestCase):
    """D6 lifecycle can record the Reviewer login and pinned native Session."""

    def test_cli_passes_optional_bindings_and_rejects_bad_values(self):
        import importlib.util
        path = Path(__file__).resolve().parents[1] / 'scripts' / 'd6_worktree_lifecycle.py'
        spec = importlib.util.spec_from_file_location('d6_lifecycle_under_test', path)
        lifecycle = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lifecycle)
        # #369: _main reads trust.json before dispatching; point it at a
        # private temp trust home so the real ~/.config/llmtool/trust.json is
        # never read.
        trust_temp = tempfile.TemporaryDirectory()
        self.addCleanup(trust_temp.cleanup)
        trust_home = Path(trust_temp.name).resolve()
        trust_dir = trust_home / '.config' / 'llmtool'
        trust_dir.mkdir(parents=True, mode=0o700)
        for directory in (trust_home, trust_home / '.config', trust_dir):
            directory.chmod(0o700)
        trust_path = trust_dir / 'trust.json'
        trust_path.write_text(json.dumps({
            'schema_version': 1, 'canonical_root': str(trust_home / 'missing-canonical'),
            'workspace_root': str(trust_home), 'tasks_root': str(trust_home / 'tasks'),
            'repository': {'slug': 'davidcatcat/davidcattoolsproject',
                           'allowed_origins': [
                               'git@github.com:davidcatcat/davidcattoolsproject.git'],
                           'approval_refs': {}},
            'ci_trust': {}, 'tools': {},
        }), encoding='utf-8')
        trust_path.chmod(0o600)
        original_load_trust = lifecycle._load_trust
        trust_patch = patch.object(
            lifecycle, '_load_trust',
            side_effect=lambda: original_load_trust(home=str(trust_home)))
        trust_patch.start()
        self.addCleanup(trust_patch.stop)
        base = ['--task-id', 'issue352-reviewer', '--agent-id', 'claude-reviewer',
                '--session-ref', 'claude-reviewer-session', '--lease-ref', 'lease',
                '--branch', 'review/issue352', '--base-sha', 'a' * 40]
        with patch.object(lifecycle, 'create_worktree') as create:
            self.assertEqual(lifecycle._main(base + [
                '--github-login', 'davidcatcat',
                '--native-session-id', '6f1c2a51-0d4e-4c55-9d0e-6a7a3a0e9b11']), 0)
        self.assertEqual(create.call_args.kwargs['github_login'], 'davidcatcat')
        self.assertEqual(create.call_args.kwargs['native_session_id'],
                         '6f1c2a51-0d4e-4c55-9d0e-6a7a3a0e9b11')
        with patch.object(lifecycle, 'create_worktree') as create:
            self.assertEqual(lifecycle._main(base), 0)
        self.assertIsNone(create.call_args.kwargs['github_login'])
        with patch.object(lifecycle, 'resume_worktree') as resume, \
                redirect_stderr(StringIO()):
            self.assertEqual(lifecycle._main(['--resume-task-id', 'x', '--resume-root', '/x',
                                              '--github-login', 'davidcatcat']), 1)
        resume.assert_not_called()
        # Bad values are rejected before any filesystem access, so this holds
        # on runners where the default canonical root does not exist.
        missing_root = tempfile.TemporaryDirectory()
        self.addCleanup(missing_root.cleanup)
        repo_root = ['--repo-root', str(Path(missing_root.name) / 'missing')]
        for bad in (['--github-login', 'bad login'], ['--github-login', 'a' * 40],
                    ['--native-session-id', 'x\ny'], ['--native-session-id', '']):
            for root in ([], repo_root):
                with self.subTest(bad=bad, root=root), \
                        patch.object(lifecycle, '_run_git') as git, \
                        redirect_stderr(StringIO()) as err:
                    self.assertEqual(lifecycle._main(base + root + bad), 1)
                git.assert_not_called()
                self.assertIn('D6_TASK_INPUT_INVALID', err.getvalue())

    def test_created_reviewer_record_is_readable_by_task_complete(self):
        import test_d6_worktree_lifecycle as d6
        harness = d6.D6WorktreeLifecycleTests('test_atomic_record_publishes_complete_private_file_without_overwrite')
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        trusted = harness.source / 'tools' / 'llmtool' / 'scripts' / 'd6_worktree_lifecycle.py'
        # The harness setUp already points the lifecycle at a temp trust home
        # whose canonical_root is harness.source (the trusted dispatcher root).
        with patch.dict(os.environ, {'LLMTOOL_D6_TESTING': '1'}), \
                patch.object(d6.lifecycle, '__file__', str(trusted)), \
                redirect_stdout(StringIO()):
            d6.lifecycle.create_worktree(
                canonical_root=harness.source, workspace_root=harness.tasks,
                task_id='issue352-reviewer', agent_id='claude-reviewer',
                session_ref='claude-reviewer-session', lease_ref='lease',
                branch='review/issue352', base_sha=harness.base,
                github_login='davidcatcat', native_session_id='native-reviewer')
        common = str(harness.source / '.git')
        record_path = Path(common) / 'llmtool-worktrees' / 'issue352-reviewer.json'
        self.assertEqual(stat.S_IMODE(record_path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(record_path.read_text())['env2_pass_marker']['result'], 'PASS')
        registration = task_completion.read_d6_registration('issue352-reviewer', common)
        self.assertEqual(registration.github_login, 'davidcatcat')
        self.assertEqual(registration.native_session_id, 'native-reviewer')
        self.assertEqual(registration.result_sha, harness.base)


if __name__ == '__main__':
    unittest.main()
