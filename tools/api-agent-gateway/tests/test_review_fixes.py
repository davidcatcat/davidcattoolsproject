"""Regressions for the security self-review findings."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from helpers import GatewayCase, reply, tool_call
from api_agent_gateway.confine import ConfinementError, Scope, Workspace
from api_agent_gateway.contract import Refusal, parse_profile
from api_agent_gateway.sessions import Store
from api_agent_gateway.tools import ToolExecutor


def _case_insensitive(path: str) -> bool:
    probe = os.path.join(path, 'CaseProbe')
    open(probe, 'w').close()
    try:
        return os.path.exists(os.path.join(path, 'caseprobe'))
    finally:
        os.unlink(probe)


class ProfileHttpAllowlistTests(unittest.TestCase):
    def profile(self, base_url, **extra):
        profile = {
            'adapter': 'openai-chat-tools', 'base_url': base_url, 'model': 'test-model',
            'context_cap_tokens': 4096, 'max_output_tokens': 1024,
            'allowed_data_classes': ['synthetic'],
        }
        profile.update(extra)
        return {'schema_version': 1, 'profiles': {'test': profile}}

    def parse(self, base_url, **extra):
        return parse_profile(self.profile(base_url, **extra), 'test')

    def test_allowlisted_private_ip_http_is_accepted(self):
        self.parse('http://10.20.30.40/v1', http_allow_hosts=['10.20.30.40'])

    def test_unlisted_private_ip_and_public_ip_are_refused(self):
        with self.assertRaises(Refusal) as unlisted:
            self.parse('http://10.20.30.40/v1', http_allow_hosts=['10.20.30.41'])
        self.assertEqual(unlisted.exception.code, 'PROFILE_INVALID')
        with self.assertRaises(Refusal) as public:
            self.parse('http://8.8.8.8/v1', http_allow_hosts=['8.8.8.8'])
        self.assertEqual(public.exception.code, 'PROFILE_INVALID')

    def test_allowlisted_hostname_http_is_accepted(self):
        self.parse('http://gateway.corp.example/v1', http_allow_hosts=['gateway.corp.example'])

    def test_absent_list_preserves_loopback_and_https_rules(self):
        self.parse('http://127.0.0.1/v1')
        self.parse('https://gateway.example/v1')
        with self.assertRaises(Refusal):
            self.parse('http://gateway.example/v1')

    def test_userinfo_query_and_fragment_are_refused(self):
        for url in ('http://user@gateway.example/v1', 'http://@gateway.example/v1',
                    'https://gateway.example/v1?', 'https://gateway.example/v1#'):
            with self.subTest(url=url), self.assertRaises(Refusal):
                self.parse(url, http_allow_hosts=['gateway.example'])


class NameMatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='gwagent-case-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.makedirs(os.path.join(self.tmp, 'src'))
        os.makedirs(os.path.join(self.tmp, 'Tests'))
        with open(os.path.join(self.tmp, 'src', 'New_Test.py'), 'w') as fh:
            fh.write('UNAUTHORIZED\n')
        with open(os.path.join(self.tmp, 'Tests', 't.py'), 'w') as fh:
            fh.write('UNAUTHORIZED\n')
        if not _case_insensitive(self.tmp):
            self.skipTest('filesystem is case-sensitive')
        self.ws = Workspace(self.tmp, Scope(['tests/'], ['src/new_test.py', 'tests/']))
        self.addCleanup(self.ws.close)

    def code(self, fn, *a):
        with self.assertRaises(ConfinementError) as cm:
            fn(*a)
        return cm.exception.code

    def test_case_variant_file_refused(self):
        self.assertEqual(self.code(self.ws.read_text, 'src/new_test.py'), 'NAME_MISMATCH')
        self.assertEqual(self.code(self.ws.apply_edit, 'src/new_test.py', 'UNAUTHORIZED', 'x', False),
                         'NAME_MISMATCH')
        self.assertEqual(self.code(self.ws.apply_edit, 'src/new_test.py', None, 'x', True), 'ALREADY_EXISTS')
        with open(os.path.join(self.tmp, 'src', 'New_Test.py')) as fh:
            self.assertEqual(fh.read(), 'UNAUTHORIZED\n')

    def test_case_variant_directory_refused(self):
        self.assertEqual(self.code(self.ws.read_text, 'tests/t.py'), 'NAME_MISMATCH')
        self.assertEqual(self.code(self.ws.list_dir, 'tests'), 'NAME_MISMATCH')

    def test_unicode_normalization_variant_refused(self):
        nfd = 'café.py'
        with open(os.path.join(self.tmp, 'Tests', nfd), 'w') as fh:
            fh.write('x\n')
        ws = Workspace(self.tmp, Scope(['Tests/café.py'], []))
        self.addCleanup(ws.close)
        if 'café.py' in os.listdir(os.path.join(self.tmp, 'Tests')):
            self.skipTest('filesystem normalizes names on create')
        with self.assertRaises(ConfinementError) as cm:
            ws.read_text('Tests/café.py')
        self.assertIn(cm.exception.code, ('NAME_MISMATCH', 'NOT_FOUND'))


class RobustnessTests(GatewayCase):
    def test_surrogate_path_is_a_tool_error(self):
        bad = {'id': 'c1', 'type': 'function',
               'function': {'name': 'read_file', 'arguments': '{"path": "src/\\ud800"}'}}
        self.server.script = [reply(bad), reply(tool_call('finish', summary='ok'))]
        code, result, audit = self.run_task()
        self.assertEqual(code, 0, result)
        tool = json.loads(self.server.requests[1]['messages'][-1]['content'])
        self.assertEqual(tool['error'], 'PATH_INVALID')
        self.assert_audit_ok(audit, result)

    def test_non_utf8_filename_skipped(self):
        try:
            fd = os.open(os.path.join(os.fsencode(self.repo), b'src', b'bad\xff.py'), os.O_CREAT | os.O_WRONLY)
            os.close(fd)
        except OSError:
            self.skipTest('filesystem refuses non-UTF-8 names')
        self.server.script = [reply(tool_call('list_dir', path='src')), reply(tool_call('finish', summary='ok'))]
        code, result, _ = self.run_task()
        self.assertEqual(code, 0, result)

    def test_unexpected_exception_still_writes_result(self):
        self.server.script = [reply(tool_call('list_dir', path='src'))]
        with mock.patch.object(ToolExecutor, 'execute', side_effect=RuntimeError('boom')):
            code, result, audit = self.run_task()
        self.assertEqual((code, result['status'], result['code']), (5, 'POLICY_REFUSED', 'INTERNAL_ERROR'))
        self.assert_audit_ok(audit, result)
        with open(audit) as fh:
            self.assertNotIn('boom', fh.read())

    def test_finish_reason_allow_listed_in_audit(self):
        body = reply(tool_call('finish', summary='ok'))
        body['choices'][0]['finish_reason'] = 'PROVIDER TEXT LEAK 5521'
        self.server.script = [body]
        code, result, audit = self.run_task()
        with open(audit) as fh:
            blob = fh.read()
        self.assertNotIn('LEAK 5521', blob)
        self.assertIn('"finish_reason":"other"', blob)

    def test_dripping_server_hits_total_deadline(self):
        self.write_profiles(timeout_seconds=1)
        self.server.script = [('drip', 0.2, 40)]
        code, result, _ = self.run_task()
        self.assertEqual((code, result['code']), (4, 'TIMEOUT'))

    def test_unsafe_profiles_file_refused(self):
        os.chmod(self.profiles_path, 0o666)
        code, result, _ = self.run_task()
        self.assertEqual((code, result['code']), (5, 'PROFILES_UNSAFE'))
        self.assertEqual(self.server.requests, [])

    def test_resume_rechecks_expiry_under_lock(self):
        self.server.script = [reply(tool_call('finish', summary='one'))]
        code, result, _ = self.run_task()
        sid = result['session_id']
        meta_path = os.path.join(self.state, 'sessions', sid, 'meta.json')
        with open(meta_path) as fh:
            meta = json.load(fh)
        meta['last_turn_at'] -= 73 * 3600
        os.unlink(meta_path)
        with os.fdopen(os.open(meta_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as fh:
            json.dump(meta, fh)
        before = len(self.server.requests)
        # Simulate sweep() skipping the session because its lock was busy.
        with mock.patch.object(Store, 'sweep', return_value=[]):
            code, result, _ = self.resume(sid, self.task())
        self.assertEqual((code, result['code']), (5, 'SESSION_ENDED'))
        self.assertEqual(len(self.server.requests), before)
        self.assertFalse(os.path.exists(os.path.join(self.state, 'sessions', sid)))


if __name__ == '__main__':
    unittest.main()
