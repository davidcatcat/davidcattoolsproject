"""#1909 item 2: UNKNOWN review claim settles only when Reviewer D6 binds the session."""

import os
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

from llmtool import review_result
from llmtool.errors import LLMToolError
try:
    from .test_review_result import (
        CONTRACT, EXECUTOR, HEAD, PR, REVIEWER, RUN, TASK,
        ReconcileStore, ReviewResultTests, _bind_reviewer, _comment, _store,
    )
except ImportError:
    from test_review_result import (
        CONTRACT, EXECUTOR, HEAD, PR, REVIEWER, RUN, TASK,
        ReconcileStore, ReviewResultTests, _bind_reviewer, _comment, _store,
    )


class ReviewerSessionBoundTests(unittest.TestCase):
    run_result = ReviewResultTests.run_result

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ReconcileStore(_store(self.temp.name, claim_state='UNKNOWN'), 'gw-agent')
        self.common = _bind_reviewer(self.temp.name)

    def state(self, version=1):
        return next(item['state'] for item in self.store.get(TASK)['review_dispatch_claim']
                    if item['version'] == version)

    def assert_unbound_refusal(self, comments, **kwargs):
        with self.assertRaises(LLMToolError) as caught:
            self.run_result(comments, **kwargs)
        self.assertEqual(caught.exception.review_dispatch_code, 'REVIEW_REVIEWER_SESSION_UNBOUND')
        self.assertEqual(self.state(), 'UNKNOWN')
        self.assertEqual(self.store.get(TASK).get('completion_events') or [], [])

    def test_bound_session_settles_to_dispatched(self):
        # (a) bound: DISPATCHED
        result = self.run_result([_comment()], common_dir=self.common)
        self.assertEqual(result['state'], 'REVIEW_PASS')
        self.assertTrue(result['created'])
        self.assertEqual(self.state(), 'DISPATCHED')

    def test_pending_session_refuses_until_bound(self):
        # (b) #1899 shape (native_session_id None, session_ref 'pending-'+REVIEWER)
        # refuses REVIEW_REVIEWER_SESSION_UNBOUND; once bound, same verdict gives created True
        _bind_reviewer(self.temp.name, native_session_id=None,
                       session_ref='pending-' + REVIEWER)
        self.assert_unbound_refusal([_comment()], common_dir=self.common)

        _bind_reviewer(self.temp.name, native_session_id='review-session',
                       session_ref='claude-reviewer-session')
        result = self.run_result([_comment()], common_dir=self.common)
        self.assertEqual(result['state'], 'REVIEW_PASS')
        self.assertTrue(result['created'])
        self.assertEqual(self.state(), 'DISPATCHED')

    def test_other_bound_session_refuses(self):
        # (c) other bound session refuses
        _bind_reviewer(self.temp.name, native_session_id='other-session')
        self.assert_unbound_refusal([_comment()], common_dir=self.common)

    def test_record_bad_mode_and_deleted_refuses(self):
        # (d) record mode 0o644, then deleted, refuses
        record_path = os.path.join(self.common, 'llmtool-worktrees', f'{REVIEWER}.json')
        os.chmod(record_path, 0o644)
        self.assert_unbound_refusal([_comment()], common_dir=self.common)

        os.unlink(record_path)
        self.assert_unbound_refusal([_comment()], common_dir=self.common)

    def test_trust_config_failure_refuses(self):
        # (e) _trust_config raising LLMToolError refuses
        with patch.object(review_result.task_completion, '_trust_config',
                          side_effect=LLMToolError('trust failure')):
            self.assert_unbound_refusal([_comment()], common_dir=None)

    def test_fail_verdict_without_record_records_review_fail(self):
        # (f) FAIL verdict, no record: REVIEW_FAIL
        record_path = os.path.join(self.common, 'llmtool-worktrees', f'{REVIEWER}.json')
        os.unlink(record_path)
        result = self.run_result([_comment('FAIL')], common_dir=self.common)
        self.assertEqual(result['state'], 'REVIEW_FAIL')
        self.assertTrue(result['created'])
        self.assertEqual(self.state(), 'UNKNOWN')
        event = self.store.get(TASK)['completion_events'][0]
        self.assertEqual(event['source'], 'REVIEWER')
        self.assertEqual(event['source_sha'], HEAD)

    def test_worktree_default_common_dir_settles(self):
        # (g) git repo + 'git worktree add' D6, cwd in D6, _trust_config gives
        # canonical_root=repo: _default_common_dir() == D6 --git-common-dir; PASS settles.
        with tempfile.TemporaryDirectory() as td:
            base = os.path.realpath(td)
            repo = os.path.join(base, 'repo')
            d6 = os.path.join(base, 'd6-' + REVIEWER)
            env = {
                'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
                'GIT_AUTHOR_NAME': 'test',
                'GIT_AUTHOR_EMAIL': 'test@example.com',
                'GIT_COMMITTER_NAME': 'test',
                'GIT_COMMITTER_EMAIL': 'test@example.com',
            }
            subprocess.run(['git', 'init', repo], env=env, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(['git', '-C', repo, 'commit', '--allow-empty', '-m', 'initial'],
                           env=env, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(['git', '-C', repo, 'worktree', 'add', d6],
                           env=env, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            _bind_reviewer(base)
            d6_common = subprocess.check_output(
                ['git', '-C', d6, 'rev-parse', '--path-format=absolute', '--git-common-dir'],
                env=env).decode('utf-8', 'strict').strip()

            old_cwd = os.getcwd()
            os.chdir(d6)
            try:
                with patch.object(review_result.task_completion, '_trust_config',
                                  return_value=types.SimpleNamespace(canonical_root=repo)):
                    self.assertEqual(review_result._default_common_dir(), d6_common)
                    self.store = ReconcileStore(_store(base, claim_state='UNKNOWN'), 'gw-agent')
                    result = self.run_result([_comment()])
                    self.assertEqual(result['state'], 'REVIEW_PASS')
                    self.assertTrue(result['created'])
                    self.assertEqual(self.state(), 'DISPATCHED')
            finally:
                os.chdir(old_cwd)


if __name__ == '__main__':
    unittest.main()
