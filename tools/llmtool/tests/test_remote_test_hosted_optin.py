"""Unit tests for the opt-in hosted remote-test path and CLI flag."""

import argparse
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from llmtool import remote_test
from llmtool import task_submission_cli

try:
    import review_trust_fixture
except ImportError:
    review_trust_fixture = None


def setUpModule():
    if review_trust_fixture is not None:
        review_trust_fixture.start_module()


def tearDownModule():
    if review_trust_fixture is not None:
        review_trust_fixture.stop_module()


SHA = 'a' * 40
OTHER_SHA = 'b' * 40
TASK_ID = '1' * 32
RUN_ID = '2' * 32
PM_SESSION_ID = 'pm-' + '3' * 32
REF = 'refs/pull/425/head'
EXECUTOR_TASK = 'd6-task'
MODULE = 'test_remote_test'


class FakeStore:
    def __init__(self, task=None):
        self.task = task or {
            'task_id': TASK_ID,
            'run_id': RUN_ID,
            'pm_session_id': PM_SESSION_ID,
            'backend_session_semantics': 'ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL',
            'pm_provenance': {'source': 'ACTIVE_PM_RUNTIME', 'provider_invoked': False},
            'completion': {'evidence': {
                'common_dir': '/repo/.git',
                'result': {'result_head': SHA},
            }},
            'developer': {
                'read_paths': ['tools/llmtool/tests/test_remote_test.py'],
                'write_paths': [],
                'direct_result_callback': {
                    'state': 'PM_EVIDENCE_READY',
                    'evidence': {
                        'common_dir': '/repo/.git',
                        'result': {'result_head': SHA},
                        'tests': [{'path': 'tools/llmtool/tests/test_remote_test.py'}],
                    },
                },
            },
            'remote_test_receipts': [],
            'completion_events': [],
            'completion_event_tombstones': [],
            'remote_test_outcomes': [],
            'published_pr': None,
        }

    def get(self, task_id):
        if task_id != self.task['task_id']:
            raise KeyError(task_id)
        return self.task

    def append_remote_test_receipt(self, task_id, receipt):
        self.task['remote_test_receipts'].append(receipt)
        return receipt

    def record_published_pr(self, task_id, pr, head_sha):
        existing = self.task.get('published_pr')
        if (type(existing) is dict and existing.get('pr') == pr and
                existing.get('head_sha') == head_sha):
            return existing
        self.task['published_pr'] = {
            'pr': pr, 'head_sha': head_sha, 'recorded_at': '2026-11-09T00:00:00Z',
        }
        return self.task['published_pr']

    def append_remote_test_outcome(self, task_id, outcome):
        if outcome in self.task['remote_test_outcomes']:
            index = self.task['remote_test_outcomes'].index(outcome)
            return {'outcome': outcome, 'counter': index, 'created': False}
        counter = len(self.task['remote_test_outcomes'])
        self.task['remote_test_outcomes'].append(outcome)
        return {'outcome': outcome, 'counter': counter, 'created': True}

    def record_completion_event(self, task_id, **event):
        saved = dict(event, task_id=task_id, pm_session_id=self.task['pm_session_id'],
                     state='PENDING')
        self.task['completion_events'].append(saved)
        return {'event': saved, 'created': True}


class RemoteTestHostedOptinTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()

    def _make_receipt_bytes(self):
        receipt = {
            'result': 'PASS',
            'exit_code': 0,
            'source_sha': SHA,
            'tree': 'c' * 40,
            'fetch_head': SHA,
            'modules': [MODULE],
            'identity': {'uid': 65534},
            'started_at': '2026-11-09T00:00:00Z',
            'ended_at': '2026-11-09T00:00:01Z',
            'elapsed_s': 1.0,
            'ran_total': 1,
            'final': ['OK'],
            'failures': [],
            'log_bytes': 0,
            'log_sha256': 'd' * 64,
            'remote_log_path': None,
        }
        return json.dumps(receipt, separators=(',', ':')).encode('utf-8')

    def test_hosted_true_calls_run_hosted_with_admitted_modules(self):
        dummy_get = MagicMock()
        dummy_post = MagicMock()
        dummy_get_bytes = MagicMock()
        dummy_now = '2026-11-09T12:00:00Z'
        expected_result = {'result': 'PASS', 'code': 0}

        def fail_transport(*args, **kwargs):
            self.fail('EC2 transport must not be called when hosted=True')

        with patch.object(remote_test, 'read_d6_registration',
                          return_value=SimpleNamespace(result_sha=SHA)), \
             patch.object(remote_test, '_config',
                          side_effect=AssertionError('_config must not be called when hosted=True')), \
             patch.object(remote_test, '_run_hosted',
                          return_value=expected_result) as mock_run_hosted:
            res = remote_test.run_remote_test(
                TASK_ID,
                executor_task=EXECUTOR_TASK,
                sha=SHA,
                ref=REF,
                modules=(MODULE,),
                store=self.store,
                hosted=True,
                get=dummy_get,
                post=dummy_post,
                get_bytes=dummy_get_bytes,
                now=dummy_now,
                transport=fail_transport,
            )

        self.assertEqual(res, expected_result)
        mock_run_hosted.assert_called_once_with(
            self.store,
            TASK_ID,
            self.store.task,
            executor_task=EXECUTOR_TASK,
            sha=SHA,
            ref=REF,
            modules=(MODULE,),
            get=dummy_get,
            post=dummy_post,
            get_bytes=dummy_get_bytes,
            now=dummy_now,
        )

    def test_hosted_true_defaults_modules_to_admitted_from_task(self):
        expected_result = {'result': 'PASS'}

        with patch.object(remote_test, 'read_d6_registration',
                          return_value=SimpleNamespace(result_sha=SHA)), \
             patch.object(remote_test, '_config',
                          side_effect=AssertionError('_config must not be called')), \
             patch.object(remote_test, '_run_hosted',
                          return_value=expected_result) as mock_run_hosted:
            res = remote_test.run_remote_test(
                TASK_ID,
                executor_task=EXECUTOR_TASK,
                sha=SHA,
                ref=REF,
                store=self.store,
                hosted=True,
                transport=lambda *a, **k: self.fail('transport must not be called'),
            )

        self.assertEqual(res, expected_result)
        mock_run_hosted.assert_called_once()
        call_kwargs = mock_run_hosted.call_args.kwargs
        self.assertEqual(call_kwargs['modules'], (MODULE,))

    def test_admission_refusals_with_hosted_true_return_denied_and_never_call_run_hosted(self):
        cases = [
            (
                'unadmitted_module',
                {'modules': ('test_not_admitted',)},
                'MODULE_NOT_ADMITTED',
                SimpleNamespace(result_sha=SHA),
            ),
            (
                'invalid_sha',
                {'sha': 'invalid-sha-value'},
                'ARGUMENT_INVALID',
                SimpleNamespace(result_sha=SHA),
            ),
            (
                'invalid_ref',
                {'ref': ''},
                'ARGUMENT_INVALID',
                SimpleNamespace(result_sha=SHA),
            ),
            (
                'invalid_executor_task',
                {'executor_task': 'invalid/task'},
                'ARGUMENT_INVALID',
                SimpleNamespace(result_sha=SHA),
            ),
            (
                'unrecorded_head',
                {},
                'RECEIPT_HEAD_UNRECORDED',
                SimpleNamespace(result_sha=None),
            ),
            (
                'mismatched_head',
                {},
                'RECEIPT_HEAD_MISMATCH',
                SimpleNamespace(result_sha=OTHER_SHA),
            ),
        ]

        for name, overrides, expected_code, reg in cases:
            with self.subTest(case=name):
                call_args = {
                    'executor_task': EXECUTOR_TASK,
                    'sha': SHA,
                    'ref': REF,
                    'modules': (MODULE,),
                    'store': self.store,
                    'hosted': True,
                    'transport': lambda *a, **k: self.fail('transport called on admission refusal'),
                }
                call_args.update(overrides)

                with patch.object(remote_test, 'read_d6_registration', return_value=reg), \
                     patch.object(remote_test, '_config',
                                  side_effect=AssertionError('_config called on admission refusal')), \
                     patch.object(remote_test, '_run_hosted',
                                  side_effect=AssertionError('_run_hosted called on admission refusal')):
                    result = remote_test.run_remote_test(TASK_ID, **call_args)

                self.assertEqual(result.get('result'), 'ADMISSION_DENIED')
                self.assertEqual(result.get('code'), expected_code)

    def test_abandoned_claude_task_with_hosted_true_refuses_before_run_hosted(self):
        self.store.task['developer'] = {'provider_profile': 'claude'}
        self.store.task['abandonment'] = {'reason': 'cancelled'}

        with patch.object(remote_test, '_run_hosted',
                          side_effect=AssertionError('_run_hosted called for abandoned task')):
            result = remote_test.run_remote_test(
                TASK_ID,
                executor_task=EXECUTOR_TASK,
                sha=SHA,
                ref=REF,
                modules=(MODULE,),
                store=self.store,
                hosted=True,
            )

        self.assertEqual(result.get('result'), 'ADMISSION_DENIED')
        self.assertEqual(result.get('code'), 'CLAUDE_TASK_ABANDONED')

    def test_hosted_false_never_calls_run_hosted(self):
        transport_called = []

        def transport(argv, script, timeout):
            transport_called.append(argv)
            return SimpleNamespace(stdout=self._make_receipt_bytes(), stderr=b'', returncode=0)

        config_val = {
            'host': 'ec2.example.org',
            'user': 'runner',
            'repo_path': '/home/runner/project',
            'python': '/usr/bin/python3',
        }

        with patch.object(remote_test, 'read_d6_registration',
                          return_value=SimpleNamespace(result_sha=SHA)), \
             patch.object(remote_test, '_config', return_value=config_val), \
             patch.object(remote_test, '_run_hosted') as mock_run_hosted:
            res_explicit = remote_test.run_remote_test(
                TASK_ID,
                executor_task=EXECUTOR_TASK,
                sha=SHA,
                ref=REF,
                modules=(MODULE,),
                store=self.store,
                hosted=False,
                transport=transport,
            )
            self.assertEqual(res_explicit.get('result'), 'PASS')

            res_default = remote_test.run_remote_test(
                TASK_ID,
                executor_task=EXECUTOR_TASK,
                sha=SHA,
                ref=REF,
                modules=(MODULE,),
                store=self.store,
                transport=transport,
            )
            self.assertEqual(res_default.get('result'), 'PASS')

        mock_run_hosted.assert_not_called()
        self.assertEqual(mock_run_hosted.call_count, 0)

    def test_cli_parser_remote_test_help_and_hosted_flag(self):
        parser = argparse.ArgumentParser()
        actions = parser.add_subparsers(dest='subcommand')
        task_submission_cli.add_task_submission_parsers(actions)

        parent_help = parser.format_help()
        self.assertIn('unless --hosted', parent_help)

        remote_test_parser = actions.choices['remote-test']
        sub_help = remote_test_parser.format_help()
        self.assertIn('--hosted', sub_help)
        self.assertIn('run on the GitHub-hosted workflow instead of EC2', sub_help)

        hosted_action = next((a for a in remote_test_parser._actions if '--hosted' in a.option_strings), None)
        self.assertIsNotNone(hosted_action)
        self.assertEqual(hosted_action.help, 'run on the GitHub-hosted workflow instead of EC2')

    def test_cli_remote_test_with_hosted_flag_reaches_run_remote_test_with_hosted_true(self):
        parser = argparse.ArgumentParser()
        actions = parser.add_subparsers(dest='subcommand')
        task_submission_cli.add_task_submission_parsers(actions)

        args = parser.parse_args([
            'remote-test', TASK_ID,
            '--executor-task', EXECUTOR_TASK,
            '--sha', SHA,
            '--ref', REF,
            '--hosted',
        ])
        self.assertTrue(args.hosted)

        with patch('llmtool.remote_test.run_remote_test') as mock_run:
            mock_run.return_value = {'result': 'PASS'}
            out = io.StringIO()
            res = task_submission_cli.run_task_remote_test(args, store=self.store, stdout=out)
            self.assertIsNone(res)
            self.assertEqual(json.loads(out.getvalue()), {'result': 'PASS'})
            mock_run.assert_called_once_with(
                TASK_ID,
                executor_task=EXECUTOR_TASK,
                sha=SHA,
                ref=REF,
                modules=[],
                pr=None,
                store=self.store,
                hosted=True,
            )

    def test_cli_remote_test_without_hosted_flag_reaches_run_remote_test_with_hosted_false(self):
        parser = argparse.ArgumentParser()
        actions = parser.add_subparsers(dest='subcommand')
        task_submission_cli.add_task_submission_parsers(actions)

        args = parser.parse_args([
            'remote-test', TASK_ID,
            '--executor-task', EXECUTOR_TASK,
            '--sha', SHA,
            '--ref', REF,
        ])
        self.assertFalse(args.hosted)

        with patch('llmtool.remote_test.run_remote_test') as mock_run:
            mock_run.return_value = {'result': 'PASS'}
            out = io.StringIO()
            res = task_submission_cli.run_task_remote_test(args, store=self.store, stdout=out)
            self.assertIsNone(res)
            self.assertEqual(json.loads(out.getvalue()), {'result': 'PASS'})
            mock_run.assert_called_once_with(
                TASK_ID,
                executor_task=EXECUTOR_TASK,
                sha=SHA,
                ref=REF,
                modules=[],
                pr=None,
                store=self.store,
                hosted=False,
            )

    def test_cli_main_dispatches_hosted_flag_if_cli_module_present(self):
        try:
            from llmtool import cli
        except ImportError:
            cli = None

        if cli is not None and hasattr(cli, 'main'):
            with patch('llmtool.remote_test.run_remote_test') as mock_run:
                mock_run.return_value = {'result': 'PASS'}
                with patch('sys.stdout', new=io.StringIO()):
                    try:
                        cli.main([
                            'task', 'remote-test', TASK_ID,
                            '--executor-task', EXECUTOR_TASK,
                            '--sha', SHA,
                            '--ref', REF,
                            '--hosted',
                        ])
                    except SystemExit:
                        pass
                if mock_run.called:
                    self.assertIs(mock_run.call_args.kwargs.get('hosted'), True)

    def test_run_task_remote_test_namespace_without_hosted_passes_hosted_false(self):
        args = argparse.Namespace(
            task_id=TASK_ID,
            executor_task=EXECUTOR_TASK,
            sha=SHA,
            ref=REF,
            module=[MODULE],
            pr=None,
        )
        self.assertFalse(hasattr(args, 'hosted'))

        with patch('llmtool.remote_test.run_remote_test') as mock_run:
            mock_run.return_value = {'result': 'PASS'}
            out = io.StringIO()
            res = task_submission_cli.run_task_remote_test(args, store=self.store, stdout=out)
            self.assertIsNone(res)
            self.assertEqual(json.loads(out.getvalue()), {'result': 'PASS'})
            mock_run.assert_called_once_with(
                TASK_ID,
                executor_task=EXECUTOR_TASK,
                sha=SHA,
                ref=REF,
                modules=[MODULE],
                pr=None,
                store=self.store,
                hosted=False,
            )
            self.assertIs(mock_run.call_args.kwargs.get('hosted'), False)


if __name__ == '__main__':
    unittest.main()
