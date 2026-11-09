"""Tests for GitHub-hosted remote-test dispatch and run lookup."""

import re
import unittest

from llmtool import github_identity
from llmtool import remote_test


_SHA = '0123456789abcdef0123456789abcdef01234567'
_REF = 'main'
_NONCE = 'fedcba9876543210fedcba9876543210'


class HostedRemoteTestConstantsTests(unittest.TestCase):
    def test_hosted_constants(self):
        self.assertEqual(
            remote_test._HOSTED_WORKFLOW_PATH,
            '.github/workflows/llmtool-remote-test.yml',
        )
        self.assertEqual(remote_test._HOSTED_JOB_NAME, 'remote-test')
        self.assertEqual(remote_test._HOSTED_LABELS, ('ubuntu-24.04',))
        self.assertEqual(remote_test._HOSTED_RUNNER_GROUP, 'GitHub Actions')
        self.assertEqual(remote_test._HOSTED_APP_ID, 15368)
        self.assertEqual(
            remote_test._HOSTED_ACTORS,
            (github_identity.owner_login(), github_identity.ai_login()),
        )
        self.assertEqual(
            remote_test._HOSTED_TITLE_PREFIX,
            'llmtool remote-test ',
        )
        self.assertEqual(
            remote_test._HOSTED_ARTIFACT_PREFIX,
            'remote-test-receipt-',
        )
        self.assertEqual(remote_test._HOSTED_ZIP_LIMIT, 65536)
        self.assertEqual(remote_test._HOSTED_MEMBER_LIMIT, 32768)

    def test_nonce_pattern(self):
        self.assertIsNotNone(remote_test._NONCE.fullmatch(_NONCE))
        self.assertIsNone(remote_test._NONCE.fullmatch('short'))
        self.assertIsNone(remote_test._NONCE.fullmatch(_NONCE + 'a'))
        self.assertIsNone(remote_test._NONCE.fullmatch(_NONCE[:-1]))
        self.assertIsNone(remote_test._NONCE.fullmatch(_NONCE.upper()))
        self.assertIsNone(remote_test._NONCE.fullmatch('g' * 32))

    def test_new_nonce(self):
        nonce1 = remote_test._new_nonce()
        nonce2 = remote_test._new_nonce()
        self.assertEqual(len(nonce1), 32)
        self.assertEqual(len(nonce2), 32)
        self.assertIsNotNone(remote_test._NONCE.fullmatch(nonce1))
        self.assertIsNotNone(remote_test._NONCE.fullmatch(nonce2))
        self.assertNotEqual(nonce1, nonce2)


class HostedDispatchTests(unittest.TestCase):
    def test_input_validation_sha(self):
        calls = []

        def fake_post(path, body):
            calls.append((path, body))
            return True

        for bad_sha in ('', 'abc', 'g' * 40, _SHA[:-1], _SHA + '0', _SHA.upper(), None, 123):
            with self.subTest(bad_sha=bad_sha):
                with self.assertRaises(ValueError):
                    remote_test._hosted_dispatch(bad_sha, _REF, ['test_mod'], _NONCE, post=fake_post)
        self.assertEqual(calls, [])

    def test_input_validation_ref(self):
        calls = []

        def fake_post(path, body):
            calls.append((path, body))
            return True

        for bad_ref in ('', ' ', 'bad ref with spaces', None, 123):
            with self.subTest(bad_ref=bad_ref):
                with self.assertRaises(ValueError):
                    remote_test._hosted_dispatch(_SHA, bad_ref, ['test_mod'], _NONCE, post=fake_post)
        self.assertEqual(calls, [])

    def test_input_validation_nonce(self):
        calls = []

        def fake_post(path, body):
            calls.append((path, body))
            return True

        for bad_nonce in ('', 'xyz', _NONCE[:-1], _NONCE + '1', _NONCE.upper(), None, 123):
            with self.subTest(bad_nonce=bad_nonce):
                with self.assertRaises(ValueError):
                    remote_test._hosted_dispatch(_SHA, _REF, ['test_mod'], bad_nonce, post=fake_post)
        self.assertEqual(calls, [])

    def test_input_validation_modules(self):
        calls = []

        def fake_post(path, body):
            calls.append((path, body))
            return True

        # empty
        with self.assertRaises(ValueError):
            remote_test._hosted_dispatch(_SHA, _REF, [], _NONCE, post=fake_post)

        # non-sequence
        for bad_type in ('test_mod', {'test_mod': 1}, None, 42):
            with self.subTest(bad_type=bad_type):
                with self.assertRaises(ValueError):
                    remote_test._hosted_dispatch(_SHA, _REF, bad_type, _NONCE, post=fake_post)

        # > 8 modules
        nine_modules = [f'test_mod_{i}' for i in range(9)]
        with self.assertRaises(ValueError):
            remote_test._hosted_dispatch(_SHA, _REF, nine_modules, _NONCE, post=fake_post)

        # module name invalid
        for bad_module in ('not_a_test', 'test_UPPER', 'test-hyphen', '', 123):
            with self.subTest(bad_module=bad_module):
                with self.assertRaises(ValueError):
                    remote_test._hosted_dispatch(_SHA, _REF, [bad_module], _NONCE, post=fake_post)

        # unsorted modules
        with self.assertRaises(ValueError):
            remote_test._hosted_dispatch(_SHA, _REF, ['test_b', 'test_a'], _NONCE, post=fake_post)

        # duplicate modules
        with self.assertRaises(ValueError):
            remote_test._hosted_dispatch(_SHA, _REF, ['test_a', 'test_a'], _NONCE, post=fake_post)

        self.assertEqual(calls, [])

    def test_exact_dispatch_path_and_body_single_module(self):
        calls = []

        def fake_post(path, body):
            calls.append((path, body))
            return True

        outcome = remote_test._hosted_dispatch(
            _SHA, _REF, ['test_remote_test'], _NONCE, post=fake_post,
        )
        self.assertEqual(outcome, 'DISPATCHED')
        self.assertEqual(len(calls), 1)
        path, body = calls[0]
        self.assertEqual(path, 'actions/workflows/llmtool-remote-test.yml/dispatches')
        self.assertEqual(body, {
            'ref': 'main',
            'inputs': {
                'sha': _SHA,
                'ref': _REF,
                'modules': 'test_remote_test',
                'nonce': _NONCE,
            },
        })

    def test_exact_dispatch_path_and_body_multiple_modules(self):
        calls = []

        def fake_post(path, body):
            calls.append((path, body))
            return True

        mods = ['test_alpha', 'test_beta', 'test_gamma']
        outcome = remote_test._hosted_dispatch(
            _SHA, 'refs/pull/42/head', mods, _NONCE, post=fake_post,
        )
        self.assertEqual(outcome, 'DISPATCHED')
        self.assertEqual(len(calls), 1)
        path, body = calls[0]
        self.assertEqual(path, 'actions/workflows/llmtool-remote-test.yml/dispatches')
        self.assertEqual(body, {
            'ref': 'main',
            'inputs': {
                'sha': _SHA,
                'ref': 'refs/pull/42/head',
                'modules': 'test_alpha test_beta test_gamma',
                'nonce': _NONCE,
            },
        })

    def test_dispatch_unconfirmed_on_post_failure(self):
        def failing_post_exception(path, body):
            raise RuntimeError('network timeout')

        def failing_post_false(path, body):
            return False

        def failing_post_unconfirmed(path, body):
            return 'UNCONFIRMED'

        for fake in (failing_post_exception, failing_post_false, failing_post_unconfirmed):
            with self.subTest(fake=fake.__name__):
                outcome = remote_test._hosted_dispatch(
                    _SHA, _REF, ['test_mod'], _NONCE, post=fake,
                )
                self.assertEqual(outcome, 'UNCONFIRMED')

    def test_default_post_helper_path_validation(self):
        with self.assertRaises(ValueError):
            remote_test._hosted_post('actions/workflows/wrong.yml/dispatches', {'ref': 'main'})


class HostedFindRunTests(unittest.TestCase):
    def setUp(self):
        self.owner = github_identity.owner_login()
        self.ai = github_identity.ai_login()

    def make_run(
        self,
        *,
        nonce=_NONCE,
        title=None,
        branch='main',
        path='.github/workflows/llmtool-remote-test.yml',
        actor=None,
        triggering_actor=None,
        run_id=1001,
    ):
        return {
            'id': run_id,
            'display_title': title if title is not None else ('llmtool remote-test ' + nonce),
            'head_branch': branch,
            'path': path,
            'actor': {'login': actor if actor is not None else self.owner},
            'triggering_actor': {'login': triggering_actor if triggering_actor is not None else self.owner},
        }

    def test_find_run_success_owner_and_ai(self):
        valid_run = self.make_run(actor=self.owner, triggering_actor=self.ai)
        data = {'total_count': 1, 'workflow_runs': [valid_run]}

        def fake_get(path):
            self.assertEqual(
                path,
                'actions/workflows/llmtool-remote-test.yml/runs?event=workflow_dispatch&branch=main',
            )
            return data

        found = remote_test._hosted_find_run(_NONCE, get=fake_get)
        self.assertEqual(found, valid_run)

    def test_filter_title(self):
        bad_title_run = self.make_run(title='different title')
        data = {'total_count': 1, 'workflow_runs': [bad_title_run]}

        found = remote_test._hosted_find_run(_NONCE, get=lambda _: data)
        self.assertIsNone(found)

    def test_filter_branch(self):
        bad_branch_run = self.make_run(branch='feature/abc')
        data = {'total_count': 1, 'workflow_runs': [bad_branch_run]}

        found = remote_test._hosted_find_run(_NONCE, get=lambda _: data)
        self.assertIsNone(found)

    def test_filter_path(self):
        bad_path_run = self.make_run(path='.github/workflows/other.yml')
        data = {'total_count': 1, 'workflow_runs': [bad_path_run]}

        found = remote_test._hosted_find_run(_NONCE, get=lambda _: data)
        self.assertIsNone(found)

    def test_filter_actor(self):
        bad_actor_run = self.make_run(actor='unauthorized_actor')
        data = {'total_count': 1, 'workflow_runs': [bad_actor_run]}

        found = remote_test._hosted_find_run(_NONCE, get=lambda _: data)
        self.assertIsNone(found)

    def test_filter_triggering_actor(self):
        bad_triggering_run = self.make_run(triggering_actor='unauthorized_trigger')
        data = {'total_count': 1, 'workflow_runs': [bad_triggering_run]}

        found = remote_test._hosted_find_run(_NONCE, get=lambda _: data)
        self.assertIsNone(found)

    def test_none_when_empty_or_no_match(self):
        # Empty runs list
        empty_data = {'total_count': 0, 'workflow_runs': []}
        self.assertIsNone(remote_test._hosted_find_run(_NONCE, get=lambda _: empty_data))

        # Runs present but mismatched
        mismatched_data = {
            'total_count': 2,
            'workflow_runs': [
                self.make_run(branch='dev'),
                self.make_run(title='wrong title'),
            ],
        }
        self.assertIsNone(remote_test._hosted_find_run(_NONCE, get=lambda _: mismatched_data))

    def test_ambiguous_when_multiple_matches(self):
        run1 = self.make_run(run_id=1001)
        run2 = self.make_run(run_id=1002)
        data = {'total_count': 2, 'workflow_runs': [run1, run2]}

        found = remote_test._hosted_find_run(_NONCE, get=lambda _: data)
        self.assertEqual(found, 'AMBIGUOUS')

    def test_unreadable_on_errors(self):
        def error_get(path):
            raise RuntimeError('API uncontactable')

        self.assertEqual(remote_test._hosted_find_run(_NONCE, get=error_get), 'UNREADABLE')

        for malformed in (None, 'not a dict', [], 123):
            with self.subTest(malformed=malformed):
                self.assertEqual(
                    remote_test._hosted_find_run(_NONCE, get=lambda _: malformed),
                    'UNREADABLE',
                )

        # missing workflow_runs
        self.assertEqual(
            remote_test._hosted_find_run(_NONCE, get=lambda _: {'total_count': 0}),
            'UNREADABLE',
        )

        # workflow_runs is not a list
        self.assertEqual(
            remote_test._hosted_find_run(_NONCE, get=lambda _: {'workflow_runs': 'invalid'}),
            'UNREADABLE',
        )

        # element of workflow_runs is not a dict
        self.assertEqual(
            remote_test._hosted_find_run(_NONCE, get=lambda _: {'workflow_runs': ['invalid']}),
            'UNREADABLE',
        )

    def test_invalid_nonce(self):
        with self.assertRaises(ValueError):
            remote_test._hosted_find_run('invalid-nonce', get=lambda _: {})


if __name__ == '__main__':
    unittest.main()
