"""#2367: a direct-route result carries the trusted main pin its first publish needs."""

import contextlib
import copy
import io
import json
import unittest
from unittest.mock import patch

from llmtool import task_advance
from llmtool import task_submission_cli
from llmtool.errors import LLMToolError


SOURCE_SHA = 'a' * 40
MAIN_SHA = 'e' * 40
MOVED_MAIN = 'd' * 40
TASK_ID = '1' * 32
RUN_ID = '2' * 32
CONTRACT_DIGEST = '4' * 64
REPOSITORY = 'example-owner/example-repo'


class _Result:
    def __init__(self, returncode=0, stdout=b''):
        self.returncode = returncode
        self.stdout = stdout


def pin(**changes):
    value = {
        'repository': REPOSITORY, 'ref': 'refs/heads/main', 'sha': MAIN_SHA,
        'provenance': 'AUTHENTICATED_TRUSTED_SLUG_GITHUB_REF',
        'task_id': TASK_ID, 'run_id': RUN_ID,
        'contract_digest': CONTRACT_DIGEST, 'source_sha': SOURCE_SHA,
        'verified_result_heads': [],
    }
    value.update(changes)
    return value


class _Store:
    """The small TaskStore surface the direct result event uses."""

    def __init__(self, record):
        self.record = copy.deepcopy(record)
        self.writes = []

    def get(self, task_id):
        if task_id != self.record['task_id']:
            raise AssertionError('unexpected Task read')
        return copy.deepcopy(self.record)

    def compare_and_update(self, task_id, *, expected, **updates):
        if task_id != self.record['task_id']:
            raise AssertionError('unexpected Task write')
        for key, value in expected.items():
            if self.record.get(key) != value:
                raise AssertionError('unexpected expected state for ' + key)
        self.record.update(copy.deepcopy(updates))
        self.writes.append(copy.deepcopy(updates))
        return copy.deepcopy(self.record)

    def update(self, task_id, **updates):
        if task_id != self.record['task_id']:
            raise AssertionError('unexpected Task write')
        self.record.update(copy.deepcopy(updates))
        self.writes.append(copy.deepcopy(updates))
        return copy.deepcopy(self.record)


def direct_record():
    evidence = {
        'task_id': TASK_ID, 'run_id': RUN_ID, 'contract_digest': CONTRACT_DIGEST,
        'executor_task_id': 'executor-1',
        'executor_identity': 'opencode-developer',
        'executor_session_ref': 'session-ref',
        'executor_native_session_id': None,
        'executor_native_session': 'UNKNOWN',
        'execution_mode': 'DIRECT_D6',
        'root': '/repo/task', 'common_dir': '/repo/.git', 'branch': 'feature/task',
        'source_sha': SOURCE_SHA, 'result_head': None,
        'verification_state': 'NOT_STARTED', 'test_policy': 'LOCAL',
    }
    callback = {
        'state': 'WAITING_EXECUTOR', 'executor_task_id': 'executor-1',
        'evidence': evidence, 'missing_gates': [], 'pm_dispatch': 'NOT_STARTED',
        'pm_response': None, 'pm_recommendation': None, 'pm_usage': None,
        'pm_error_code': None,
    }
    developer = {
        'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'opencode',
        'model': 'opencode-model', 'session_id': 'dev-' + '5' * 32,
        'source_sha': SOURCE_SHA,
        'artifact': 'diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n'
                    '@@ -0,0 +1 @@\n+x\n',
        'artifact_sha256': 'f' * 64,
        'execution': {'status': 'SUCCEEDED', 'process_exit_code': 0,
                      'native_session_id': None, 'usage_source': 'UNKNOWN'},
        'read_paths': ['a.py'], 'write_paths': ['a.py'], 'usage': None,
        'direct_result_callback': callback,
    }
    return {
        'schema_version': 2, 'task_id': TASK_ID, 'run_id': RUN_ID,
        'pm_session_id': 'pm-' + '3' * 32,
        'pm_provider_profile': 'pm-profile', 'pm_provider_type': 'openai-compatible',
        'pm_model': 'pm-model', 'backend_session_semantics': 'EPHEMERAL_SINGLE_CALL',
        'native_session_id': None, 'state': 'PM_RESPONDED',
        'pm_route_binding': {'type': 'openai-compatible', 'model': 'pm-model',
                             'base_url': None},
        'created_at': '2026-01-01T00:00:00Z', 'updated_at': '2026-01-01T00:00:00Z',
        'task_contract': {}, 'task_binding': {'contract_digest': CONTRACT_DIGEST},
        'request': 'request', 'request_sha256': 'f' * 64,
        'response': json.dumps({'schema_version': 1, 'summary': 'ok',
                                'developer_assignment': None}),
        'usage': None, 'error_code': None, 'record_sha256': '0' * 64,
        'pm_assignment': None, 'developer': developer,
        'completion': {'executor_task_id': 'executor-1',
                       'state': 'WAITING_EXECUTOR', 'evidence': {
                           'task_id': TASK_ID, 'run_id': RUN_ID,
                           'contract_digest': CONTRACT_DIGEST,
                           'executor_identity': 'opencode-developer',
                           'executor_session_ref': 'session-ref',
                           'executor_native_session_id': None,
                           'executor_native_session': 'UNKNOWN',
                           'execution_mode': 'DIRECT_D6', 'root': '/repo/task',
                           'common_dir': '/repo/.git', 'branch': 'feature/task',
                           'source_sha': SOURCE_SHA, 'result_head': None}},
    }


def worktree_result():
    return {
        'source_sha': SOURCE_SHA, 'result_head': None, 'diff_sha256': 'b' * 64,
        'changed_paths': ['a.py'], 'root': '/repo/task',
        'common_dir': '/repo/.git', 'branch': 'feature/task',
    }


class DirectPublishPinTests(unittest.TestCase):
    def test_direct_result_records_the_validated_pin_the_publish_lookup_reads(self):
        store = _Store(direct_record())
        expected_pin = pin()
        with patch.object(task_submission_cli.task_completion, 'validate_task_binding',
                          return_value=None), \
                patch.object(task_submission_cli.task_completion,
                             'collect_executor_worktree_result',
                             return_value=worktree_result()), \
                patch.object(task_submission_cli, '_direct_result_tests', return_value=[]), \
                patch.object(task_submission_cli, '_recollect_direct_worktree',
                             return_value=None), \
                patch.object(task_submission_cli, '_capture_trusted_main_pin',
                             return_value=expected_pin), \
                patch.object(task_submission_cli.task_completion, 'ensure_main_commit',
                             return_value=True), \
                patch.object(task_submission_cli.task_completion, '_trusted_slug',
                             return_value=REPOSITORY), \
                patch.object(task_submission_cli.active_pm_inbox, 'recovery',
                             return_value=None), \
                patch.object(task_submission_cli, 'resolve_profile_config',
                             side_effect=LLMToolError('provider unavailable')):
            with contextlib.redirect_stdout(io.StringIO()):
                code = task_submission_cli.run_task_direct_result_event(
                    TASK_ID, 'executor-1', store=store)

        self.assertEqual(code, 1)
        self.assertEqual(store.record['developer']['direct_result_callback']['state'],
                         'WAITING_PM')
        stored = store.record['completion']['evidence']['trusted_main_pin']
        self.assertEqual(stored, expected_pin)
        # The advance publish base lookup reads exactly this completion pin.
        with patch.object(task_submission_cli.task_completion, '_trusted_slug',
                          return_value=REPOSITORY):
            self.assertEqual(task_advance._publish_recorded_main_pin(store.record), MAIN_SHA)

    def test_publish_base_includes_the_recorded_pin_when_remote_main_moved(self):
        calls = []

        def runner(_root, args):
            calls.append(list(args))
            if args[:2] == ['ls-remote', '--heads']:
                return _Result(0, f'{MOVED_MAIN}\trefs/heads/main\n'.encode())
            if args[:1] == ['cat-file'] and args[1] == '-e':
                return _Result(0 if args[2] == f'{MAIN_SHA}^{{commit}}' else 1)
            raise AssertionError('unexpected git call: ' + repr(args))

        self.assertEqual(
            task_advance._publish_base_commits(runner, '/repo/task', None, MAIN_SHA),
            [MAIN_SHA])
        self.assertEqual([args[:2] for args in calls],
                         [['ls-remote', '--heads'], ['cat-file', '-e'],
                          ['cat-file', '-e']])

    def test_recorded_pin_is_materialized_before_the_base_lookup_gives_up(self):
        present = set()

        def materialize(sha):
            present.add(sha)

        def runner(_root, args):
            if args[:2] == ['ls-remote', '--heads']:
                return _Result(0, f'{MOVED_MAIN}\trefs/heads/main\n'.encode())
            if args[:1] == ['cat-file'] and args[1] == '-e':
                return _Result(0 if args[2] == f'{MAIN_SHA}^{{commit}}' and
                               MAIN_SHA in present else 1)
            raise AssertionError('unexpected git call: ' + repr(args))

        self.assertEqual(
            task_advance._publish_base_commits(
                runner, '/repo/task', None, MAIN_SHA, materialize=materialize),
            [MAIN_SHA])
        # Only the recorded authenticated pin is materialized, never the
        # unauthenticated remote tip.
        self.assertEqual(present, {MAIN_SHA})

    def test_unmaterializable_recorded_pin_still_fails_closed(self):
        def materialize(_sha):
            raise RuntimeError('fetch unavailable')

        def runner(_root, args):
            if args[:2] == ['ls-remote', '--heads']:
                return _Result(0, f'{MOVED_MAIN}\trefs/heads/main\n'.encode())
            if args[:1] == ['cat-file'] and args[1] == '-e':
                return _Result(1)
            raise AssertionError('unexpected git call: ' + repr(args))

        with self.assertRaises(task_advance._PublishRefusal) as caught:
            task_advance._publish_base_commits(
                runner, '/repo/task', None, MAIN_SHA, materialize=materialize)
        self.assertEqual(caught.exception.code, 'PUBLISH_BINDING_UNAVAILABLE')

    def test_committed_content_screen_still_refuses_with_a_recorded_pin(self):
        def runner(_root, _args):
            return _Result(0, b'--- a/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+flagged\n')

        with patch.object(task_submission_cli, 'developer_diff_sensitive',
                          return_value=True):
            with self.assertRaises(task_advance._PublishRefusal) as caught:
                task_advance._publish_screen_commits(
                    runner, '/repo/task', [('c' * 40, [(SOURCE_SHA)])])
        self.assertEqual(caught.exception.code, 'DEVELOPER_OUTPUT_SENSITIVE')

    def test_missing_malformed_or_forged_pin_is_not_used(self):
        def record_with(value):
            return {'task_id': TASK_ID, 'run_id': RUN_ID,
                    'task_binding': {'contract_digest': CONTRACT_DIGEST},
                    'developer': {'source_sha': SOURCE_SHA},
                    'completion': {'evidence': (
                        {} if value is None else {'trusted_main_pin': value})}}

        cases = {
            'missing': None,
            'malformed': pin(sha='z' * 40),
            'forged provenance': pin(provenance='LOCAL_REF'),
            'other Task': pin(task_id='9' * 32),
            'other source': pin(source_sha=MOVED_MAIN),
        }
        with patch.object(task_submission_cli.task_completion, '_trusted_slug',
                          return_value=REPOSITORY):
            for name, value in cases.items():
                with self.subTest(case=name):
                    self.assertIsNone(
                        task_advance._publish_recorded_main_pin(record_with(value)))

    def test_unverifiable_base_still_fails_closed(self):
        def runner(_root, args):
            if args[:2] == ['ls-remote', '--heads']:
                return _Result(0, f'{MOVED_MAIN}\trefs/heads/main\n'.encode())
            if args[:1] == ['cat-file'] and args[1] == '-e':
                return _Result(1)
            raise AssertionError('unexpected git call: ' + repr(args))

        with self.assertRaises(task_advance._PublishRefusal) as caught:
            task_advance._publish_base_commits(runner, '/repo/task', None, 'z' * 40)
        self.assertEqual(caught.exception.code, 'PUBLISH_BINDING_UNAVAILABLE')

    def test_remote_branch_head_absent_locally_is_not_a_base(self):
        def runner(_root, args):
            if args[:2] == ['ls-remote', '--heads']:
                return _Result(0, f'{MOVED_MAIN}\trefs/heads/main\n'.encode())
            if args[:1] == ['cat-file'] and args[1] == '-e':
                return _Result(1)
            raise AssertionError('unexpected git call: ' + repr(args))

        with self.assertRaises(task_advance._PublishRefusal) as caught:
            task_advance._publish_base_commits(runner, '/repo/task', MOVED_MAIN)
        self.assertEqual(caught.exception.code, 'PUBLISH_BINDING_UNAVAILABLE')


if __name__ == '__main__':
    unittest.main()
