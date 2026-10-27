import ast
import fcntl
import hashlib
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import remote_test
from llmtool.task_store import _json_bytes, _validate_remote_test_receipts


SHA = 'a' * 40
OTHER = 'b' * 40


class Store:
    def __init__(self):
        self.task = {
            'task_id': '1' * 32, 'run_id': '2' * 32, 'pm_session_id': 'pm-' + '3' * 32,
            'backend_session_semantics': 'ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL',
            'pm_provenance': {'source': 'ACTIVE_PM_RUNTIME', 'provider_invoked': False},
            'completion': {'evidence': {
                'common_dir': '/repo/.git',
                'result': {'result_head': SHA},
            }},
            'developer': {'direct_result_callback': {
                'state': 'PM_EVIDENCE_READY',
                'evidence': {'common_dir': '/repo/.git',
                    'result': {'result_head': SHA},
                    'tests': [{'path': 'tools/llmtool/tests/test_remote_test.py'}]},
            }},
            'remote_test_receipts': [], 'completion_events': [],
            'completion_event_tombstones': [], 'remote_test_outcomes': [],
        }

    def get(self, task_id):
        if task_id != self.task['task_id']:
            raise KeyError(task_id)
        return self.task

    def append_remote_test_receipt(self, task_id, receipt):
        self.task['remote_test_receipts'].append(receipt)
        return receipt

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


class RemoteTestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / 'remote-test.json'
        self.config.write_text(json.dumps({
            'host': 'ec2.example.org', 'user': 'runner',
            'repo_path': '/home/runner/project', 'python': '/usr/bin/python3',
        }))
        self.config.chmod(0o600)
        self.store = Store()
        self.calls = []

    def receipt(self, result='PASS', *, fetch=SHA, exit_code=0, final=('OK',),
                failure_excerpt=None):
        receipt = {
            'result': result, 'exit_code': exit_code, 'source_sha': SHA,
            'tree': 'c' * 40, 'fetch_head': fetch, 'modules': ['test_remote_test'],
            'identity': {'uid': 65534}, 'started_at': '2026-10-27T00:00:00Z',
            'ended_at': '2026-10-27T00:00:01Z', 'elapsed_s': 1.0,
            'ran_total': 1, 'final': list(final), 'failures': [],
            'log_bytes': 0, 'log_sha256': 'd' * 64,
            'remote_log_path': '/tmp/llmtool-remote-log.Abc123/remote-test.log',
        }
        if failure_excerpt is not None:
            receipt['failure_excerpt'] = failure_excerpt
        return json.dumps(receipt, separators=(',', ':')).encode()

    def failure_excerpt(self, output):
        parsed = ast.parse(remote_test._REMOTE_SCRIPT)
        sensitive = next(node for node in parsed.body
                         if isinstance(node, ast.Assign) and
                         any(isinstance(target, ast.Name) and target.id == 'SENSITIVE'
                             for target in node.targets))
        helper = next(node for node in parsed.body
                      if isinstance(node, ast.FunctionDef) and
                      node.name == 'failure_excerpt')
        namespace = {'re': re, '_emitted_sensitive': remote_test._emitted_sensitive}
        exec(compile(ast.Module(body=[sensitive, helper], type_ignores=[]),
                     '<remote-excerpt-helper>', 'exec'), namespace)
        return namespace['failure_excerpt']([output.encode()],
            '/tmp/llmtool-remote-test.Abc123', '/home/runner')

    def run_it(self, data=None, *, error=None, modules=('test_remote_test',), sha=SHA):
        def transport(argv, script, timeout):
            self.calls.append((argv, script, timeout))
            if error:
                raise error
            return SimpleNamespace(stdout=self.receipt() if data is None else data,
                                   stderr=b'', returncode=0)
        with patch.object(remote_test, 'read_d6_registration',
                          return_value=SimpleNamespace(result_sha=SHA)):
            return remote_test.run_remote_test(
                self.store.task['task_id'], executor_task='d6-task', sha=sha,
                ref='refs/pull/425/head', modules=modules, store=self.store,
                config_path=self.config, transport=transport)

    def test_pass_is_stored_on_same_task_and_delivered_to_pm_inbox(self):
        result = self.run_it()
        self.assertEqual(result['result'], 'PASS')
        self.assertEqual(len(self.store.task['remote_test_receipts']), 1)
        self.assertEqual(len(self.store.task['completion_events']), 1)
        self.assertEqual(self.store.task['completion_events'][0]['source'], 'EC2_REMOTE_TEST')

    def test_non_ascii_final_line_receipt_digest_matches_store_encoding(self):
        result = self.run_it(self.receipt(final=('é OK',)))

        self.assertEqual(result['result'], 'PASS')
        stored = self.store.task['remote_test_receipts'][0]
        digest_input = dict(stored)
        claimed = digest_input.pop('receipt_digest')
        self.assertEqual(claimed, hashlib.sha256(_json_bytes(digest_input)).hexdigest())

    def test_fail_and_sha_mismatch_are_not_pass(self):
        failed = self.run_it(self.receipt('FAIL', exit_code=1, final=('FAILED (failures=1)',)))
        self.assertEqual(failed['result'], 'FAIL')
        self.store = Store()
        mismatch = self.run_it(self.receipt('SHA_MISMATCH', fetch=OTHER, exit_code=41))
        self.assertEqual(mismatch['result'], 'SHA_MISMATCH')

    def test_failure_excerpt_is_bounded_redacted_and_only_on_failures(self):
        cred_value = ''.join(['sk', '-']) + 'a' * 16
        screened_label = ''.join(('to', 'ken', '='))
        output = ('FAIL: test_expected (test_remote_test.Case)\n'
                  'Traceback (most recent call last):\n'
                  '  File "/tmp/llmtool-remote-test.Abc123/test.py", line 9\n'
                  'AssertionError: expected value\n'
                  + screened_label + cred_value + '\n'
                  '----------------------------------------------------------------------\n'
                  'Ran 1 test in 0.1s\nFAILED (failures=1)')
        excerpt = self.failure_excerpt(output)
        self.assertIn('FAIL: test_expected (test_remote_test.Case)', excerpt)
        self.assertIn('AssertionError: expected value', excerpt)
        self.assertIn('<REMOTE_WORKDIR>/test.py', excerpt)
        self.assertNotIn('/tmp/llmtool-remote-test.Abc123', excerpt)
        self.assertNotIn(cred_value, excerpt)
        url_value = ''.join(('https', '://', 'user', ':', 'value', '@',
                             'example.test/path'))
        jwt_value = '.'.join(('a' * 12, 'b' * 12, 'c' * 12))
        screened_output = ('FAIL: test_expected\n' + url_value + '\n' +
                           jwt_value + '\n' +
                           '----------------------------------------------------------------------\n')
        screened_excerpt = self.failure_excerpt(screened_output)
        self.assertNotIn(url_value, screened_excerpt)
        self.assertNotIn(jwt_value, screened_excerpt)
        long_output = ('FAIL: test_expected (test_remote_test.Case)\n' +
                       ('detail ' + 'x' * 120 + '\n') * 50 +
                       '----------------------------------------------------------------------\n')
        bounded = self.failure_excerpt(long_output)
        self.assertLessEqual(len(bounded.encode('utf-8')), 4096)
        self.assertLessEqual(len(bounded.splitlines()), 40)
        self.assertIn('len(raw)<=32768', remote_test._REMOTE_SCRIPT)

        failed = self.run_it(self.receipt('FAIL', exit_code=1,
            final=('FAILED (failures=1)',), failure_excerpt=excerpt))
        self.assertIn('failure_excerpt', failed)
        self.store = Store()
        passed = self.run_it()
        self.assertNotIn('failure_excerpt', passed)

        receipt = json.loads(self.receipt('FAIL', exit_code=1,
            final=('FAILED (failures=1)',), failure_excerpt='x' * 4097))
        self.assertIsNone(remote_test._validate_remote_receipt(
            json.dumps(receipt, separators=(',', ':')).encode(),
            sha=SHA, modules=('test_remote_test',)))
        capped = json.loads(self.receipt('FAIL', exit_code=1,
            final=('FAILED (failures=1)',), failure_excerpt='x' * 4096))
        self.assertIsNotNone(remote_test._validate_remote_receipt(
            json.dumps(capped, separators=(',', ':')).encode(),
            sha=SHA, modules=('test_remote_test',)))
        passed_with_excerpt = json.loads(self.receipt().decode())
        passed_with_excerpt['failure_excerpt'] = ''
        self.assertIsNone(remote_test._validate_remote_receipt(
            json.dumps(passed_with_excerpt, separators=(',', ':')).encode(),
            sha=SHA, modules=('test_remote_test',)))

        screened_label = ''.join(('to', 'ken', '='))
        cred_value = ''.join(['sk', '-']) + 'a' * 16
        screened = json.loads(self.receipt('FAIL', exit_code=1,
            final=('FAILED (failures=1)',),
            failure_excerpt=screened_label + cred_value).decode())
        self.assertIsNone(remote_test._validate_remote_receipt(
            json.dumps(screened, separators=(',', ':')).encode(),
            sha=SHA, modules=('test_remote_test',)))

    def test_remote_excerpt_screen_is_self_contained(self):
        script = remote_test._REMOTE_SCRIPT
        self.assertNotIn('import llmtool', script)
        self.assertNotIn('sys.path', script)

    def test_timeout_unavailable_and_unknown_receipts(self):
        timed = self.run_it(error=__import__('subprocess').TimeoutExpired('ssh', 600))
        self.assertEqual(timed['result'], 'TIMEOUT')
        unavailable = self.run_it(error=OSError('offline'))
        self.assertEqual(unavailable['result'], 'UNAVAILABLE')
        malformed = self.run_it(b'{not-json')
        self.assertEqual(malformed['result'], 'UNKNOWN')
        oversize = self.run_it(b' ' * 32769)
        self.assertEqual(oversize['result'], 'UNKNOWN')
        self.assertEqual([item['code'] for item in self.store.task['remote_test_outcomes']],
                         ['SSH_TIMEOUT', 'SSH_UNAVAILABLE', 'RECEIPT_INVALID',
                          'RECEIPT_INVALID'])
        self.assertEqual(len(self.store.task['completion_events']), 4)
        self.assertTrue(all(event['source'] == 'EC2_REMOTE_TEST'
                            for event in self.store.task['completion_events']))

    def test_admission_denials_are_persisted_and_have_events(self):
        result = self.run_it(modules=('test_remote_test;id',))
        self.assertEqual(result['code'], 'MODULE_NOT_ADMITTED')
        self.assertTrue(result['persisted'])
        self.assertIsNotNone(result['event_id'])
        self.assertEqual(len(self.store.task['remote_test_outcomes']), 1)
        self.assertEqual(len(self.store.task['completion_events']), 1)

    def test_outcome_event_replay_is_idempotent_but_retry_gets_new_event(self):
        first = self.run_it(error=OSError('offline'))
        saved = self.store.task['remote_test_outcomes'][0]
        replay = remote_test.record_remote_test_outcome_event(
            self.store, self.store.task['task_id'], saved, 0)
        second = self.run_it(error=OSError('offline'))
        self.assertEqual(replay['event_id'], first['event_id'])
        self.assertNotEqual(second['event_id'], first['event_id'])
        self.assertEqual(len(self.store.task['completion_events']), 2)

    def test_admission_and_argv_injection_attempts_never_call_transport(self):
        denied = self.run_it(modules=('test_remote_test;id',))
        self.assertEqual(denied['result'], 'ADMISSION_DENIED')
        injected = self.run_it(sha=SHA + ';id')
        self.assertEqual(injected['result'], 'ADMISSION_DENIED')
        option_ref = remote_test.run_remote_test(
            self.store.task['task_id'], executor_task='d6-task', sha=SHA,
            ref='--prune', modules=('test_remote_test',), store=self.store,
            config_path=self.config, transport=lambda *args: self.fail('transport called'))
        self.assertEqual(option_ref['result'], 'ADMISSION_DENIED')
        self.assertIn('"--end-of-options","origin",ref',
                      remote_test._REMOTE_SCRIPT)
        self.assertEqual(self.calls, [])

    def test_remote_script_installs_package_and_discovers_selected_test(self):
        self.assertIn('"install","--no-deps","-e","tools/llmtool"',
                      remote_test._REMOTE_SCRIPT)
        self.assertIn('"unittest","discover","-s","tests","-p",mod+".py"',
                      remote_test._REMOTE_SCRIPT)

    def test_remote_script_mktemp_dirs_are_verified_as_private(self):
        self.assertNotIn('"mktemp","-d","-m"', remote_test._REMOTE_SCRIPT)
        self.assertIn('info.st_mode&0o777!=0o700', remote_test._REMOTE_SCRIPT)
        self.assertIn('log_info.st_mode&0o777!=0o700', remote_test._REMOTE_SCRIPT)

    def test_nobody_commands_change_directory_through_env_without_cwd(self):
        self.assertIn('["sudo","-n","-u","nobody","env","-i","-C",work]',
                      remote_test._REMOTE_SCRIPT)
        self.assertNotIn('cwd=work', remote_test._REMOTE_SCRIPT)
        self.assertNotIn('cwd=work+', remote_test._REMOTE_SCRIPT)
        self.assertIn('REMOTE_STAGE_FAILED {stage} {type(exc).__name__}',
                      remote_test._REMOTE_SCRIPT)

    def test_receipt_accepts_missing_log_path(self):
        receipt = json.loads(self.receipt().decode())
        receipt['remote_log_path'] = None
        self.assertIsNotNone(remote_test._validate_remote_receipt(
            json.dumps(receipt, separators=(',', ':')).encode(),
            sha=SHA, modules=('test_remote_test',)))

    def test_pass_receipt_requires_tests_nonroot_identity_and_no_failures(self):
        for field, value in (
                ('ran_total', 0), ('identity', {'uid': 0}),
                ('failures', ['test_remote_test'])):
            with self.subTest(field=field):
                receipt = json.loads(self.receipt().decode())
                receipt[field] = value
                raw = json.dumps(receipt, separators=(',', ':')).encode()
                self.assertIsNone(remote_test._validate_remote_receipt(
                    raw, sha=SHA, modules=('test_remote_test',)))

    def test_remote_script_has_deadline_kill_and_bounded_owned_cleanup(self):
        script = remote_test._REMOTE_SCRIPT
        self.assertIn('deadline=begin+540', script)
        self.assertIn('"--kill-after=2s"', script)
        self.assertIn('info.st_uid!=nobody_uid', script)
        self.assertIn('now-info.st_mtime>86400', script)
        self.assertIn('owned_dirs[20:]', script)

    def test_remote_script_lock_helper_excludes_concurrent_run_and_releases(self):
        parsed = ast.parse(remote_test._REMOTE_SCRIPT)
        helper = next(node for node in parsed.body
                      if isinstance(node, ast.FunctionDef) and node.name == 'acquire_lock')
        namespace = {'fcntl': fcntl, 'os': os}
        exec(compile(ast.Module(body=[helper], type_ignores=[]),
                     '<remote-lock-helper>', 'exec'), namespace)
        lock_path = Path(self.temp.name) / 'remote-test.lock'
        lock_path.touch()

        held_fd = namespace['acquire_lock'](str(lock_path))
        try:
            with self.assertRaises(BlockingIOError):
                namespace['acquire_lock'](str(lock_path))
        finally:
            os.close(held_fd)

        released_fd = namespace['acquire_lock'](str(lock_path))
        os.close(released_fd)

    def test_module_admission_is_bounded(self):
        modules = tuple(f'test_module{i}' for i in range(9))
        self.store.task['developer']['direct_result_callback']['evidence']['tests'] = [
            {'path': f'tools/llmtool/tests/{module}.py'} for module in modules]
        denied = self.run_it(modules=modules)
        self.assertEqual((denied['result'], denied['code']),
                         ('ADMISSION_DENIED', 'MODULE_NOT_ADMITTED'))
        self.assertTrue(denied['persisted'])
        self.assertEqual(self.calls, [])

    def test_remote_script_recognizes_unittest_failure_summary(self):
        self.assertIn('line.startswith("FAILED (")', remote_test._REMOTE_SCRIPT)

    def test_task_store_rejects_invalid_pass_receipts(self):
        def stored_receipt(**changes):
            receipt = json.loads(self.receipt().decode())
            receipt.update(changes)
            receipt.update(task_id=self.store.task['task_id'],
                           run_id=self.store.task['run_id'], executor_task='d6-task',
                           pm_session=self.store.task['pm_session_id'],
                           command_digest='e' * 64)
            receipt.pop('receipt_digest', None)
            receipt['receipt_digest'] = hashlib.sha256(_json_bytes(receipt)).hexdigest()
            return receipt

        valid = stored_receipt()
        _validate_remote_test_receipts({'task_id': self.store.task['task_id'],
                                        'run_id': self.store.task['run_id'],
                                        'pm_session_id': self.store.task['pm_session_id'],
                                        'remote_test_receipts': [valid]})
        valid_excerpt = stored_receipt(
            result='FAIL', exit_code=1, failure_excerpt='FAIL: test_expected\nAssertionError')
        _validate_remote_test_receipts({'task_id': self.store.task['task_id'],
                                        'run_id': self.store.task['run_id'],
                                        'pm_session_id': self.store.task['pm_session_id'],
                                        'remote_test_receipts': [valid_excerpt]})
        for changes in ({'ran_total': 0}, {'identity': {'uid': 0}},
                        {'failures': ['test_remote_test']}):
            with self.subTest(changes=changes), self.assertRaises(Exception):
                bad = stored_receipt(**changes)
                _validate_remote_test_receipts({'task_id': self.store.task['task_id'],
                                                'run_id': self.store.task['run_id'],
                                                'pm_session_id': self.store.task['pm_session_id'],
                                                'remote_test_receipts': [bad]})

        oversized = stored_receipt(failure_excerpt='x' * 4097)
        with self.assertRaises(Exception):
            _validate_remote_test_receipts({'task_id': self.store.task['task_id'],
                                            'run_id': self.store.task['run_id'],
                                            'pm_session_id': self.store.task['pm_session_id'],
                                            'remote_test_receipts': [oversized]})

    def test_remote_busy_exit_maps_to_busy_without_receipt(self):
        def transport(argv, script, timeout):
            return SimpleNamespace(stdout=b'', stderr=b'', returncode=75)

        with patch.object(remote_test, 'read_d6_registration',
                          return_value=SimpleNamespace(result_sha=SHA)):
            result = remote_test.run_remote_test(
                self.store.task['task_id'], executor_task='d6-task', sha=SHA,
                ref='refs/pull/425/head', modules=('test_remote_test',), store=self.store,
                config_path=self.config, transport=transport)
        self.assertEqual((result['result'], result['code']), ('UNAVAILABLE', 'REMOTE_BUSY'))
        self.assertTrue(result['persisted'])
        self.assertEqual(len(self.store.task['remote_test_outcomes']), 1)
        self.assertEqual(len(self.store.task['completion_events']), 1)
        self.assertEqual(self.store.task['remote_test_receipts'], [])

    def test_identical_call_deduplicates_remote_run_receipt_and_event(self):
        first = self.run_it()
        second = self.run_it()
        self.assertEqual(first, second)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.store.task['remote_test_receipts']), 1)
        self.assertEqual(len(self.store.task['completion_events']), 1)

    def test_stored_fail_allows_retry_and_appends_second_receipt(self):
        failed = self.run_it(
            self.receipt('FAIL', exit_code=1, final=('FAILED (failures=1)',)))
        self.assertEqual(failed['result'], 'FAIL')

        retried = self.run_it()

        self.assertEqual(retried['result'], 'PASS')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.store.task['remote_test_receipts']), 2)


if __name__ == '__main__':
    unittest.main()
