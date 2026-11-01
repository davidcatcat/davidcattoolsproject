"""Initial ``run`` must carry a verifiable llmtool authorization record (Codex Security FIX, PR #745)."""
from __future__ import annotations

import os
import time

from helpers import GatewayCase, reply, tool_call


class RunAuthorizationTests(GatewayCase):
    def assert_refused(self, outcome):
        code, result, audit = outcome
        self.assertEqual((code, result['status'], result['code']), (5, 'POLICY_REFUSED', 'RUN_AUTHORIZATION_INVALID'))
        self.assertIsNone(result['session_id'])
        self.assert_audit_ok(audit, result)
        self.assertEqual(self.server.requests, [], 'refusal reached the provider')
        self.assertFalse(os.path.isdir(os.path.join(self.state, 'sessions')) and
                         os.listdir(os.path.join(self.state, 'sessions')))

    def test_valid_record_runs(self):
        self.server.script = [reply(tool_call('finish', summary='ok'))]
        code, result, audit = self.run_task()
        self.assertEqual((code, result['status']), (0, 'DONE'), result)
        import json
        with open(audit) as fh:
            header = json.loads(fh.readline())
        self.assertEqual(len(header['authorization_sha256']), 64)

    def test_flag_omitted_is_usage_error(self):
        code = self._run_without_flag()
        self.assertEqual(code, 2)
        self.assertEqual(self.server.requests, [])

    def _run_without_flag(self):
        from api_agent_gateway import cli
        return cli.main(['run', '--workdir', self.repo, '--profile', 'fake',
                         '--task-file', self._file('task', self.task()), '--result-file', self._file('result'),
                         '--audit-file', os.path.join(self.out, 'a-noflag.jsonl'), '--state-dir', self.state,
                         '--profiles-file', self.profiles_path], env={'HOME': self.tmp})

    def test_missing_file(self):
        self.assert_refused(self.run_task(auth=os.path.join(self.out, 'nope.json')))

    def test_malformed(self):
        path = os.path.join(self.out, 'bad-auth.json')
        with open(path, 'w') as fh:
            fh.write('{not json')
        self.assert_refused(self.run_task(auth=path))
        t = self.task()
        for bad in ({'schema_version': 2}, {'authorization_ref': ''}, {'authorization_ref': None},
                    {'checked_at': 'yesterday'}, {'scope_digest': None}):
            with self.subTest(bad=bad):
                self.assert_refused(self.run_task(t, auth=dict(self.auth_for(None, t), **bad)))

    def test_stale_or_future(self):
        t = self.task()
        self.assert_refused(self.run_task(t, auth=self.auth_for(None, t, checked_at=time.time() - 3600)))
        self.assert_refused(self.run_task(t, auth=self.auth_for(None, t, checked_at=time.time() + 3600)))

    def test_scope_digest_mismatch(self):
        t = self.task()
        wider = self.task(read_paths=['src/', 'docs/'])
        self.assert_refused(self.run_task(t, auth=self.auth_for(None, t, scope_digest='0' * 64)))
        # A record issued for a different scope does not authorize this one.
        self.assert_refused(self.run_task(wider, auth=self.auth_for(None, t)))

    def test_binding_mismatches(self):
        t = self.task()
        for field, value in (('data_class', 'internal-code'), ('profile', 'other-profile'),
                             ('egress_basis', 'another-basis'), ('task_id', 'task-999'),
                             ('session_id', 'f' * 32)):
            with self.subTest(field=field):
                self.assert_refused(self.run_task(t, auth=self.auth_for(None, t, **{field: value})))

    def test_resume_record_also_binds_profile_and_class(self):
        self.server.script = [reply(tool_call('finish', summary='one'))]
        code, result, _ = self.run_task()
        sid = result['session_id']
        before = len(self.server.requests)
        t = self.task()
        for field, value in (('data_class', 'internal-code'), ('profile', 'other-profile'),
                             ('egress_basis', 'x-basis')):
            with self.subTest(field=field):
                code, result, _ = self.resume(sid, t, auth=self.auth_for(sid, t, **{field: value}))
                self.assertEqual(result['code'], 'SESSION_AUTHORIZATION_INVALID')
        self.assertEqual(len(self.server.requests), before)
