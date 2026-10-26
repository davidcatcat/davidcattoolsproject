import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import remote_test


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
            'completion_event_tombstones': [],
        }

    def get(self, task_id):
        if task_id != self.task['task_id']:
            raise KeyError(task_id)
        return self.task

    def append_remote_test_receipt(self, task_id, receipt):
        self.task['remote_test_receipts'].append(receipt)
        return receipt

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

    def receipt(self, result='PASS', *, fetch=SHA, exit_code=0, final=('OK',)):
        return json.dumps({
            'result': result, 'exit_code': exit_code, 'source_sha': SHA,
            'tree': 'c' * 40, 'fetch_head': fetch, 'modules': ['test_remote_test'],
            'identity': {'uid': 65534}, 'started_at': '2026-10-27T00:00:00Z',
            'ended_at': '2026-10-27T00:00:01Z', 'elapsed_s': 1.0,
            'ran_total': 1, 'final': list(final), 'failures': [],
            'log_bytes': 0, 'log_sha256': 'd' * 64,
            'remote_log_path': '/tmp/llmtool-remote-log.Abc123/remote-test.log',
        }, separators=(',', ':')).encode()

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

    def test_fail_and_sha_mismatch_are_not_pass(self):
        failed = self.run_it(self.receipt('FAIL', exit_code=1, final=('FAILED (failures=1)',)))
        self.assertEqual(failed['result'], 'FAIL')
        self.store = Store()
        mismatch = self.run_it(self.receipt('SHA_MISMATCH', fetch=OTHER, exit_code=41))
        self.assertEqual(mismatch['result'], 'SHA_MISMATCH')

    def test_timeout_unavailable_and_unknown_receipts(self):
        timed = self.run_it(error=__import__('subprocess').TimeoutExpired('ssh', 600))
        self.assertEqual(timed['result'], 'TIMEOUT')
        unavailable = self.run_it(error=OSError('offline'))
        self.assertEqual(unavailable['result'], 'UNAVAILABLE')
        malformed = self.run_it(b'{not-json')
        self.assertEqual(malformed['result'], 'UNKNOWN')
        oversize = self.run_it(b' ' * 4097)
        self.assertEqual(oversize['result'], 'UNKNOWN')

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

    def test_identical_call_deduplicates_remote_run_receipt_and_event(self):
        first = self.run_it()
        second = self.run_it()
        self.assertEqual(first, second)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.store.task['remote_test_receipts']), 1)
        self.assertEqual(len(self.store.task['completion_events']), 1)


if __name__ == '__main__':
    unittest.main()
