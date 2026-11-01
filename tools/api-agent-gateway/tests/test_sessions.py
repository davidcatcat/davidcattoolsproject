"""Sessions: resume binding, every refusal case, lock, retention, screening on resume."""
from __future__ import annotations

import json
import os
import shutil
import time

from helpers import FAKE_AWS, FAKE_GH, GatewayCase, git, reply, tool_call
from api_agent_gateway.screen import PLACEHOLDER
from api_agent_gateway.sessions import Store


class SessionCase(GatewayCase):
    def start(self, task=None):
        self.server.script = [
            reply(tool_call('read_file', path='src/app.py')),
            reply(tool_call('finish', summary='round one')),
        ]
        code, result, _ = self.run_task(task)
        self.assertEqual(code, 0, result)
        self.sid = result['session_id']
        self.base_requests = len(self.server.requests)
        return result

    def session_dir(self):
        return os.path.join(self.state, 'sessions', self.sid)

    def assert_refused(self, code_result, expected):
        code, result, audit = code_result
        self.assertEqual((code, result['status'], result['code']), (5, 'POLICY_REFUSED', expected))
        self.assert_audit_ok(audit, result)
        self.assertEqual(len(self.server.requests), self.base_requests, 'refusal reached the provider')


class ResumeTests(SessionCase):
    def test_resume_continuity_and_findings(self):
        self.start()
        task = self.task(goal='Repair round GOAL2', findings=['F1: greet must not return hello'])
        self.server.script = [reply(tool_call('apply_edit', path='src/app.py', old='"hello"', new='"hi"')),
                              reply(tool_call('finish', summary='repaired'))]
        code, result, audit = self.resume(self.sid, task)
        self.assertEqual(code, 0, result)
        self.assertEqual(result['session_id'], self.sid)
        self.assertEqual(result['changed_paths'], ['src/app.py'])
        msgs = self.server.requests[self.base_requests]['messages']
        text = json.dumps(msgs)
        self.assertIn('round one', text)            # earlier finish call kept
        self.assertIn('return "hello"', json.loads(msgs[3]['content'])['content'])  # earlier tool result
        self.assertIn('Repair round GOAL2', msgs[1]['content'])  # contract updated
        self.assertIn('F1: greet must not return hello', msgs[-1]['content'])
        with open(audit) as fh:
            header = json.loads(fh.readline())
        self.assertEqual(header['session_id'], self.sid)
        self.assertEqual(len(header['authorization_sha256']), 64)
        # Allowed after a commit on top of the recorded HEAD (descendant).
        git(self.repo, 'commit', '-q', '-am', 'dev change')
        task2 = self.task(source_sha=git(self.repo, 'rev-parse', 'HEAD'), goal='round 3')
        self.server.script = [reply(tool_call('finish', summary='three'))]
        code, result, _ = self.resume(self.sid, task2)
        self.assertEqual(code, 0, result)

    def test_scope_changes_refused(self):
        self.start()
        base = self.task()
        budgets = dict(base['budgets'])
        cases = {
            'read': self.task(read_paths=['src/', 'tests/test_app.py', 'docs/']),
            'write': self.task(write_paths=['src/app.py', 'tests/', 'README.md']),
            'basis': self.task(egress_basis='another-basis'),
            'class': self.task(data_class='internal-code'),
        }
        for key in budgets:
            bigger = dict(budgets)
            bigger[key] = budgets[key] + 1
            cases['budget-' + key] = self.task(budgets=bigger)
        for name, task in cases.items():
            with self.subTest(name=name):
                # Even an authorization record computed for the new scope is not enough.
                self.assert_refused(self.resume(self.sid, task, auth=self.auth_for(self.sid, task)),
                                    'SESSION_SCOPE_CHANGED')

    def test_authorization_record_refusals(self):
        self.start()
        task = self.task(goal='repair')
        missing = os.path.join(self.out, 'does-not-exist.json')
        cases = {
            'missing': missing,
            'expired': self.auth_for(self.sid, task, checked_at=time.time() - 3600),
            'future': self.auth_for(self.sid, task, checked_at=time.time() + 3600),
            'digest': self.auth_for(self.sid, task, scope_digest='0' * 64),
            'task': self.auth_for(self.sid, task, task_id='task-999'),
            'session': self.auth_for(self.sid, task, session_id='f' * 32),
            'no-ref': self.auth_for(self.sid, task, authorization_ref=''),
            'bad-time': dict(self.auth_for(self.sid, task), checked_at='yesterday'),
            'schema': self.auth_for(self.sid, task, schema_version=2),
        }
        for name, auth in cases.items():
            with self.subTest(name=name):
                self.assert_refused(self.resume(self.sid, task, auth=auth), 'SESSION_AUTHORIZATION_INVALID')

    def test_binding_refusals(self):
        self.start()
        other = os.path.join(self.tmp, 'repo2')
        shutil.copytree(self.repo, other, symlinks=True)
        self.assert_refused(self.resume(self.sid, self.task(), workdir=other), 'SESSION_WORKDIR_MISMATCH')
        moved = self.task(task_id='task-002')
        self.assert_refused(self.resume(self.sid, moved, auth=self.auth_for(self.sid, moved)),
                            'SESSION_TASK_MISMATCH')
        self.write_profiles(timeout_seconds=11)
        self.assert_refused(self.resume(self.sid, self.task()), 'SESSION_PROFILE_MISMATCH')
        self.write_profiles()
        self.assert_refused(self.resume('0' * 32, self.task()), 'SESSION_NOT_FOUND')
        self.assert_refused(self.resume('../../etc', self.task()), 'SESSION_NOT_FOUND')

    def test_head_must_descend(self):
        self.start()
        git(self.repo, 'checkout', '-q', '--orphan', 'other')
        git(self.repo, 'commit', '-q', '-m', 'unrelated')
        task = self.task(source_sha=git(self.repo, 'rev-parse', 'HEAD'))
        self.assert_refused(self.resume(self.sid, task), 'SESSION_HEAD_NOT_DESCENDANT')
        self.assert_refused(self.resume(self.sid, self.task()), 'SOURCE_SHA_MISMATCH')

    def test_lock_contention(self):
        self.start()
        store = Store(self.state)
        lock = store.lock(self.sid)
        try:
            self.assert_refused(self.resume(self.sid, self.task()), 'SESSION_LOCKED')
            code, out = self.end(self.sid)
            self.assertEqual((code, out['code']), (5, 'SESSION_LOCKED'))
            _, listing = self.sessions()
            self.assertEqual(listing['sessions'][0]['status'], 'running')
        finally:
            lock.release()
        self.server.script = [reply(tool_call('finish', summary='ok'))]
        self.assertEqual(self.resume(self.sid, self.task())[0], 0)

    def test_planted_secrets_screened_on_resume(self):
        self.start()
        hist_path = os.path.join(self.session_dir(), 'history.json')
        with open(hist_path) as fh:
            hist = json.load(fh)
        hist['groups'][0][0]['content'] = 'planted ' + FAKE_AWS
        hist['summary'] = 'summary with ' + FAKE_GH
        os.unlink(hist_path)
        fd = os.open(hist_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as fh:
            json.dump(hist, fh)
        task = self.task(findings=['finding leaks password = "' + 'hunter2' + 'hunter2"'])
        self.server.script = [reply(tool_call('finish', summary='ok'))]
        code, result, audit = self.resume(self.sid, task)
        self.assertEqual(code, 0, result)
        seen = self.server.raw[-1].decode()
        for value in (FAKE_AWS, FAKE_GH, 'hunter2hunter2'):
            self.assertNotIn(value, seen)
        self.assertGreaterEqual(seen.count(PLACEHOLDER), 3)


class RetentionTests(SessionCase):
    def assert_no_content(self):
        blob = self.state_bytes()
        for raw in (b'GOALMARK', b'hello', b'round one', b'src/app.py'):
            self.assertNotIn(raw, blob)

    def test_end_deletes_and_blocks_resume(self):
        self.start()
        self.assertIn(b'GOALMARK', self.state_bytes())  # sanity: history was stored
        code, out = self.end(self.sid)
        self.assertEqual((code, out['status']), (0, 'ENDED'))
        self.assertFalse(os.path.exists(self.session_dir()))
        self.assert_no_content()
        with open(os.path.join(self.state, 'tombstones', self.sid + '.json')) as fh:
            tomb = json.load(fh)
        self.assertEqual(set(tomb), {'session_id', 'created_at', 'last_turn_at', 'ended_at', 'end_reason'})
        self.assert_refused(self.resume(self.sid, self.task()), 'SESSION_ENDED')
        code, out = self.end(self.sid)
        self.assertEqual(out['code'], 'SESSION_ENDED')

    def test_task_terminal_end(self):
        self.start()
        code, out = self.end(self.sid, reason='task_terminal')
        self.assertEqual(out['end_reason'], 'task_terminal')
        self.assert_no_content()

    def _age(self, hours):
        meta_path = os.path.join(self.session_dir(), 'meta.json')
        with open(meta_path) as fh:
            meta = json.load(fh)
        meta['last_turn_at'] -= hours * 3600
        os.unlink(meta_path)
        fd = os.open(meta_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as fh:
            json.dump(meta, fh)

    def test_expiry_default_72h(self):
        self.start()
        self._age(71)
        _, listing = self.sessions()
        self.assertEqual(listing['sessions'][0]['status'], 'active')
        self._age(2)
        _, listing = self.sessions()
        self.assertEqual([(s['status'], s['end_reason']) for s in listing['sessions']], [('ended', 'expired')])
        self.assertFalse(os.path.exists(self.session_dir()))
        self.assert_no_content()
        self.assert_refused(self.resume(self.sid, self.task()), 'SESSION_ENDED')

    def test_expiry_enforced_on_start(self):
        self.write_profiles(session_expiry_hours=1)
        self.start()
        self._age(2)
        self.assert_refused(self.resume(self.sid, self.task()), 'SESSION_ENDED')
        self.assert_no_content()

    def test_sessions_listing_is_content_free(self):
        self.start()
        code, listing = self.sessions()
        self.assertEqual(code, 0)
        entry = listing['sessions'][0]
        self.assertEqual(entry['session_id'], self.sid)
        self.assertEqual(entry['status'], 'active')
        self.assertEqual(set(entry['workdir']), {'dev', 'ino'})
        text = json.dumps(listing)
        for raw in ('GOALMARK', 'hello', 'round one', 'src/'):
            self.assertNotIn(raw, text)

    def test_state_permissions(self):
        self.start()
        for dirpath, dirnames, names in os.walk(self.state):
            self.assertEqual(os.stat(dirpath).st_mode & 0o777, 0o700, dirpath)
            for name in names:
                self.assertEqual(os.stat(os.path.join(dirpath, name)).st_mode & 0o777, 0o600, name)

    def test_unsafe_state_dir_refused(self):
        os.makedirs(self.state)
        os.chmod(self.state, 0o755)
        code, result, _ = self.run_task()
        self.assertEqual((code, result['code']), (5, 'STATE_DIR_UNSAFE'))
