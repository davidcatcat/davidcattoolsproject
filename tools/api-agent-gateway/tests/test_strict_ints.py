"""Booleans are not integers: ``true`` must never pass as schema_version 1 (Codex re-review v2 LOW)."""
from __future__ import annotations

import json
import os

from helpers import GatewayCase, reply, tool_call
from api_agent_gateway.audit import AuditError, verify


class StrictIntTests(GatewayCase):
    def _rewrite(self, path, mutate):
        with open(path) as fh:
            obj = json.load(fh)
        mutate(obj)
        os.unlink(path)
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as fh:
            json.dump(obj, fh)

    def _start(self):
        self.server.script = [reply(tool_call('finish', summary='one'))]
        code, result, _ = self.run_task()
        self.assertEqual(code, 0, result)
        self.before = len(self.server.requests)
        return result['session_id']

    def test_boolean_schema_version_in_run_authorization(self):
        t = self.task()
        code, result, _ = self.run_task(t, auth=self.auth_for(None, t, schema_version=True))
        self.assertEqual((code, result['code']), (5, 'RUN_AUTHORIZATION_INVALID'))
        self.assertEqual(self.server.requests, [])

    def test_boolean_schema_version_in_resume_authorization(self):
        sid = self._start()
        t = self.task()
        code, result, _ = self.resume(sid, t, auth=self.auth_for(sid, t, schema_version=True))
        self.assertEqual((code, result['code']), (5, 'SESSION_AUTHORIZATION_INVALID'))
        self.assertEqual(len(self.server.requests), self.before)

    def test_boolean_schema_version_in_task(self):
        t = self.task(schema_version=True)
        code, result, _ = self.run_task(t, auth=self.auth_for(None, self.task()))
        self.assertEqual((code, result['code']), (5, 'TASK_INVALID'))
        self.assertEqual(self.server.requests, [])

    def test_boolean_budget_refused(self):
        b = dict(self.task()['budgets'], max_turns=True)
        t = self.task(budgets=b)
        code, result, _ = self.run_task(t)
        self.assertEqual((code, result['code']), (5, 'TASK_INVALID'))

    def test_boolean_schema_version_in_profiles(self):
        with open(self.profiles_path) as fh:
            obj = json.load(fh)
        obj['schema_version'] = True
        os.unlink(self.profiles_path)
        with open(self.profiles_path, 'w') as fh:
            json.dump(obj, fh)
        code, result, _ = self.run_task()
        self.assertEqual((code, result['code']), (5, 'PROFILE_INVALID'))

    def test_boolean_fields_in_session_meta(self):
        for field in ('schema_version', 'turns', 'runs'):
            with self.subTest(field=field):
                sid = self._start()
                meta = os.path.join(self.state, 'sessions', sid, 'meta.json')
                with open(meta) as fh:
                    self.assertIs(type(json.load(fh)[field]), int)
                self._rewrite(meta, lambda m: m.update({field: True}))
                code, result, _ = self.resume(sid, self.task())
                self.assertEqual((code, result['code']), (5, 'SESSION_STATE_INVALID'))
                self.assertEqual(len(self.server.requests), self.before)

    def test_boolean_expiry_hours_treated_as_expired(self):
        sid = self._start()
        meta = os.path.join(self.state, 'sessions', sid, 'meta.json')
        self._rewrite(meta, lambda m: m.update(expiry_hours=True))
        code, result, _ = self.resume(sid, self.task())
        self.assertEqual((code, result['code']), (5, 'SESSION_ENDED'))
        self.assertFalse(os.path.exists(os.path.dirname(meta)))

    def test_boolean_schema_version_in_history(self):
        sid = self._start()
        hist = os.path.join(self.state, 'sessions', sid, 'history.json')
        self._rewrite(hist, lambda h: h.update(schema_version=True))
        code, result, _ = self.resume(sid, self.task())
        self.assertEqual((code, result['code']), (5, 'SESSION_STATE_INVALID'))
        self.assertEqual(len(self.server.requests), self.before)

    def test_boolean_seq_in_audit_record(self):
        self.server.script = [reply(tool_call('finish', summary='ok'))]
        _, _, audit = self.run_task()
        with open(audit) as fh:
            lines = fh.readlines()
        rec = json.loads(lines[1])
        self.assertEqual(rec['seq'], 1)
        rec['seq'] = True
        lines[1] = json.dumps(rec, sort_keys=True, separators=(',', ':')) + '\n'
        bad = os.path.join(self.out, 'bool-seq.jsonl')
        with open(bad, 'w') as fh:
            fh.writelines(lines[:2])
        with self.assertRaises(AuditError):
            verify(bad)
