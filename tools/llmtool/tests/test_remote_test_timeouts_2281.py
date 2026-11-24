"""#2281 row 9 guards: remote test module and total timeouts."""

import contextlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from llmtool import remote_test, repair_policy
from llmtool.errors import LLMToolError

_SHA = 'a' * 40
_CONFIG = {
    'host': 'ec2.example',
    'user': 'runner',
    'repo_path': '/srv/repo',
    'python': '/usr/bin/python3',
}


class RemoteTestTimeoutTests(unittest.TestCase):
    @staticmethod
    def _policy(**values):
        data = {key: 1 for key in repair_policy.POLICY_KEYS}
        data['schema_version'] = 1
        data.update(values)
        return data

    @contextlib.contextmanager
    def _policy_file(self, **values):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'policy.json'
            path.write_text(json.dumps(self._policy(**values)), encoding='utf-8')
            with mock.patch.object(repair_policy, 'policy_path', return_value=path):
                yield path

    @staticmethod
    def _remote_parse_source():
        script = remote_test._REMOTE_SCRIPT
        start = script.index('if len(sys.argv)')
        end = script.index('started=')
        return 'import re,sys\n' + script[start:end]

    def _run_remote_parse(self, *args):
        return subprocess.run(
            [sys.executable, '-c', self._remote_parse_source(), *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

    def test_no_keys_keep_180_480_and_540(self):
        self.assertEqual(
            (180, 540),
            (repair_policy.REMOTE_TEST_MODULE_TIMEOUT_SECONDS,
             repair_policy.REMOTE_TEST_TOTAL_TIMEOUT_SECONDS))
        with self._policy_file(), mock.patch.object(
                remote_test, '_ssh_timeout', return_value=1800):
            self.assertEqual((180, 540), remote_test._remote_test_timeouts())
        script = remote_test._REMOTE_SCRIPT
        self.assertIn(
            'work_deadline=begin+int(tl)-60; '
            'deadline=begin+int(tl)', script)
        self.assertEqual(2, script.count('limit=180'))
        self.assertIn('"-p",mod+".py"],limit=int(ml),env=', script)

    def test_ssh_argv_adds_only_two_numeric_words(self):
        argv = remote_test._ssh_argv(
            _CONFIG, _SHA, 'refs/heads/main', ('test_example',), 180, 540)
        self.assertEqual([
            'ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
            'runner@ec2.example', 'python3', '-', _SHA, 'refs/heads/main',
            '/srv/repo', '/usr/bin/python3', '180', '540', 'test_example',
        ], argv)

    def test_configured_values_reach_the_remote_argv(self):
        with self._policy_file(remote_test_module_timeout_seconds=300,
                               remote_test_total_timeout_seconds=900), \
                mock.patch.object(remote_test, '_ssh_timeout',
                                  return_value=1800):
            self.assertEqual((300, 900), remote_test._remote_test_timeouts())
        argv = remote_test._ssh_argv(
            _CONFIG, _SHA, 'refs/heads/main', ('test_example',), 300, 900)
        self.assertEqual(['300', '900', 'test_example'], argv[-3:])

    def test_remote_script_accepts_valid_timeout_words(self):
        result = self._run_remote_parse(
            _SHA, 'refs/heads/main', '/srv/repo', '/usr/bin/python3',
            '180', '540', 'test_example')
        self.assertEqual(0, result.returncode,
                         result.stderr.decode('utf8', 'replace'))

    def test_remote_script_refuses_bad_timeout_words(self):
        base = [_SHA, 'refs/heads/main', '/srv/repo', '/usr/bin/python3']
        for module_limit, total_limit in (
                ('abc', '540'), ('180', '54x'), ('0', '540'),
                ('180', '60'), ('481', '540'), ('+180', '540')):
            with self.subTest(module=module_limit, total=total_limit):
                result = self._run_remote_parse(
                    *base, module_limit, total_limit, 'test_example')
                self.assertEqual(40, result.returncode,
                                 result.stderr.decode('utf8', 'replace'))

    def test_remote_script_refuses_a_missing_timeout_word_before_unpacking(self):
        result = self._run_remote_parse(
            _SHA, 'refs/heads/main', '/srv/repo', '/usr/bin/python3', '180')
        self.assertEqual(40, result.returncode,
                         result.stderr.decode('utf8', 'replace'))

    def test_local_refuses_module_above_total_minus_reserve(self):
        with self._policy_file(remote_test_module_timeout_seconds=481,
                               remote_test_total_timeout_seconds=540), \
                mock.patch.object(remote_test, '_ssh_timeout',
                                  return_value=1800):
            with self.assertRaises(LLMToolError) as caught:
                remote_test._remote_test_timeouts()
        self.assertTrue(str(caught.exception).startswith(
            'REMOTE_TEST_TIMEOUT_INVALID'))

    def test_local_refuses_total_too_short_for_the_reserve(self):
        with self._policy_file(remote_test_module_timeout_seconds=1,
                               remote_test_total_timeout_seconds=60), \
                mock.patch.object(remote_test, '_ssh_timeout',
                                  return_value=1800):
            with self.assertRaises(LLMToolError) as caught:
                remote_test._remote_test_timeouts()
        self.assertTrue(str(caught.exception).startswith(
            'REMOTE_TEST_TIMEOUT_INVALID'))

    def test_local_refuses_total_not_below_the_ssh_timeout(self):
        margin = remote_test._REMOTE_SSH_TIMEOUT_MARGIN_SECONDS
        for ssh_timeout in (540 + margin, 540 + margin - 1):
            with self.subTest(ssh_timeout=ssh_timeout), self._policy_file(
                    remote_test_module_timeout_seconds=180,
                    remote_test_total_timeout_seconds=540), \
                    mock.patch.object(remote_test, '_ssh_timeout',
                                      return_value=ssh_timeout):
                with self.assertRaises(LLMToolError) as caught:
                    remote_test._remote_test_timeouts()
                self.assertTrue(str(caught.exception).startswith(
                    'REMOTE_TEST_TIMEOUT_INVALID'))
        with self._policy_file(remote_test_module_timeout_seconds=180,
                               remote_test_total_timeout_seconds=540), \
                mock.patch.object(remote_test, '_ssh_timeout',
                                  return_value=541 + margin):
            self.assertEqual((180, 540), remote_test._remote_test_timeouts())

    @contextlib.contextmanager
    def _incident_admission(self):
        from llmtool import main_incident_store as incident_store
        record = {'outcome': incident_store.OPEN, 'record_sha256': 'f' * 64}
        with mock.patch.object(incident_store, 'get', return_value=record), \
                mock.patch.object(incident_store, 'admitted_shas',
                                  return_value={_SHA}), \
                mock.patch.object(incident_store, 'failing_modules',
                                  return_value={'test_example'}), \
                mock.patch.object(incident_store, 'append_remote_test',
                                  return_value={'record_sha256': 'e' * 64}), \
                mock.patch.object(remote_test, '_config', return_value=_CONFIG):
            yield

    def test_incident_route_uses_the_configured_timeouts(self):
        sent = []

        def transport(argv, script, timeout):
            sent.append((argv, script, timeout))
            raise OSError('offline')

        with self._policy_file(remote_test_module_timeout_seconds=300,
                               remote_test_total_timeout_seconds=900), \
                mock.patch.object(remote_test, '_ssh_timeout',
                                  return_value=1800), \
                self._incident_admission():
            result = remote_test.run_incident_remote_test(
                'mi-' + 'a' * 40, sha=_SHA, modules=('test_example',),
                transport=transport)
        self.assertEqual((result['result'], result['code']),
                         ('UNAVAILABLE', 'SSH_UNAVAILABLE'))
        [argv, script, timeout] = sent[0]
        self.assertEqual(argv, remote_test._ssh_argv(
            _CONFIG, _SHA, _SHA, ('test_example',), 300, 900))
        self.assertEqual(['300', '900', 'test_example'], argv[-3:])
        self.assertEqual(timeout, 1800)
        self.assertIs(script, remote_test._REMOTE_SCRIPT)

    def test_incident_route_refuses_invalid_timeouts_before_ssh(self):
        def transport(*args):
            self.fail('transport called')

        with self._policy_file(remote_test_module_timeout_seconds=481,
                               remote_test_total_timeout_seconds=540), \
                mock.patch.object(remote_test, '_ssh_timeout',
                                  return_value=1800), \
                self._incident_admission():
            result = remote_test.run_incident_remote_test(
                'mi-' + 'a' * 40, sha=_SHA, modules=('test_example',),
                transport=transport)
        self.assertEqual(result['result'], 'ADMISSION_DENIED')
        self.assertEqual(result['code'], remote_test.INCIDENT_CONFIG_INVALID)

    def test_command_digest_covers_the_timeout_words(self):
        base = remote_test._ssh_argv(
            _CONFIG, _SHA, 'refs/heads/main', ('test_example',), 180, 540)
        same = list(base)
        module_changed = remote_test._ssh_argv(
            _CONFIG, _SHA, 'refs/heads/main', ('test_example',), 181, 540)
        total_changed = remote_test._ssh_argv(
            _CONFIG, _SHA, 'refs/heads/main', ('test_example',), 180, 541)
        self.assertEqual(remote_test._command_digest(base),
                         remote_test._command_digest(same))
        self.assertNotEqual(remote_test._command_digest(base),
                            remote_test._command_digest(module_changed))
        self.assertNotEqual(remote_test._command_digest(base),
                            remote_test._command_digest(total_changed))


if __name__ == '__main__':
    unittest.main()
