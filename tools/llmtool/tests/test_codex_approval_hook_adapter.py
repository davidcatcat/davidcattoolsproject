"""Track3 offline adapter fixtures; no native hooks or provider invocation."""
import contextlib
import dataclasses
import hashlib
import io
import json
import os
from unittest.mock import patch

from llmtool import codex_approval_audit as a
from llmtool import codex_approval_hook_adapter as h
from test_codex_approval_audit import StoreCase, event, policy, CANARY


class AdapterTests(StoreCase):
    def setUp(self):
        super().setUp()
        self.policy_path = os.path.join(self.base, 'policy.json')
        raw = json.dumps(dataclasses.asdict(policy())).encode()
        with open(self.policy_path, 'wb') as f:
            f.write(raw)
        os.chmod(self.policy_path, 0o600)
        self.pin = hashlib.sha256(raw).hexdigest()

    def invoke(self, raw, name='PreToolUse', pin=None):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(h, 'read_input', return_value=raw), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = h.main(['--event', name, '--session', 'sess-1', '--store', self.store,
                           '--policy', self.policy_path, '--policy-sha256', pin or self.pin])
        return code, out.getvalue(), err.getvalue()

    def test_read_pre_post_nonzero_and_end_are_correlated_without_allow(self):
        self.assertEqual(self.invoke(event('PreToolUse')), (0, '', ''))
        self.assertEqual(self.invoke(event('PostToolUse', response={'exit_code': 9}),
                                     'PostToolUse'), (0, '', ''))
        self.assertEqual(self.invoke(event('SessionEnd'), 'SessionEnd'), (0, '', ''))
        summary = self.summary()
        self.assertEqual(summary['actions'][0]['outcome'], 'EXIT_NONZERO')
        self.assertEqual(summary['status'], 'AUDIT_INCOMPLETE')
        self.assertIn('HOOK_COVERAGE_NOT_VERIFIED', summary['blockers'])

    def test_hard_no_go_returns_fixed_native_deny_and_private_decision(self):
        code, out, err = self.invoke(event('PreToolUse', command='git reset --hard ' + CANARY))
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out), {'hookSpecificOutput': {
            'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
            'permissionDecisionReason': 'Task audit policy denied this action.'}})
        self.assertNotIn(CANARY, out)
        self.assertEqual(a.read_store(self.store)[1][0]['decision'], 'deny')
        self.assertNotEqual(self.summary()['actions'][0]['enforcement'], 'BLOCKED')

    def test_patch_scope_and_mcp_unknown_reuse_collector(self):
        for command, denied in (('*** Begin Patch\n*** Add File: tools/llmtool/a.py\n+x\n*** End Patch', False),
                                ('*** Begin Patch\n*** Add File: forbidden.py\n+x\n*** End Patch', True)):
            code, out, _ = self.invoke(event('PreToolUse', tool='apply_patch', command=command))
            self.assertEqual(code, 0)
            self.assertEqual(bool(out), denied)
        self.assertEqual(self.invoke(event('PreToolUse', tool='mcp__fake__read')), (0, '', ''))

    def test_session_and_event_are_bound_by_hook_definition_not_input(self):
        for raw in (event('PreToolUse', session='other'), event('PostToolUse')):
            code, out, err = self.invoke(raw)
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)['hookSpecificOutput']['permissionDecision'], 'deny')
            self.assertEqual(err, 'codex-approval-hook: incomplete\n')
        self.assertFalse(any(r['kind'] == 'hook' for r in a.read_store(self.store)[1]))

    def test_policy_pin_permissions_and_symlink_fail_closed(self):
        self.assertIn('deny', self.invoke(event('PreToolUse'), pin='0' * 64)[1])
        os.chmod(self.policy_path, 0o644)
        self.assertIn('deny', self.invoke(event('PreToolUse'))[1])
        os.chmod(self.policy_path, 0o600)
        os.rename(self.policy_path, self.policy_path + '.real')
        os.symlink(self.policy_path + '.real', self.policy_path)
        self.assertIn('deny', self.invoke(event('PreToolUse'))[1])

    def test_malformed_and_oversize_are_private_gaps_without_content_output(self):
        for raw in (CANARY.encode(), b'x' * (a.MAX_EVENT_BYTES + 1)):
            code, out, err = self.invoke(raw)
            self.assertEqual(code, 0)
            self.assertIn('deny', out)
            self.assertNotIn(CANARY, out + err)
        self.assertIn('MALFORMED_EVENT', [g['gap'] for g in a.read_store(self.store)[2]])

    def test_store_error_denies_pre_but_post_cannot_undo_execution(self):
        with patch.object(a, 'record_event', side_effect=OSError(CANARY)):
            pre = self.invoke(event('PreToolUse'))
            post = self.invoke(event('PostToolUse'), 'PostToolUse')
        self.assertEqual(pre[0], 0)
        self.assertIn('deny', pre[1])
        self.assertEqual(post[:2], (1, ''))
        self.assertNotIn(CANARY, pre[1] + pre[2] + post[2])

    def test_input_timeout_and_overflow_do_not_authorize(self):
        with patch.object(h, 'read_input', side_effect=a.AuditError(CANARY)):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = h.main(['--event', 'PreToolUse', '--session', 'sess-1',
                               '--store', self.store, '--policy', self.policy_path,
                               '--policy-sha256', self.pin])
        self.assertEqual(code, 0)
        self.assertIn('deny', out.getvalue())
        self.assertNotIn(CANARY, out.getvalue() + err.getvalue())
        self.assertIn('MALFORMED_EVENT', [g['gap'] for g in a.read_store(self.store)[2]])

    def test_bounded_input_reads_actual_pipe_eof(self):
        r, w = os.pipe()
        try:
            os.write(w, b'{}'); os.close(w); w = None
            self.assertEqual(h.read_input(r), b'{}')
        finally:
            os.close(r)
            if w is not None: os.close(w)

    def test_bounded_input_timeout_on_open_undrained_pipe(self):
        r, w = os.pipe()
        try:
            with patch.object(h, 'INPUT_SECONDS', 0.001):
                with self.assertRaises(a.AuditError):
                    h.read_input(r)
        finally:
            os.close(r); os.close(w)

    def test_no_subprocess_or_provider_and_post_deny_is_not_enforcement(self):
        with patch.object(a.subprocess, 'Popen', side_effect=AssertionError('unexpected spawn')):
            self.invoke(event('PreToolUse', command='git reset --hard'))
            self.invoke(event('PostToolUse', command='git reset --hard',
                              response={'exit_code': 0, 'stdout': CANARY}), 'PostToolUse')
            self.invoke(event('SessionEnd'), 'SessionEnd')
        summary = self.summary()
        self.assertEqual(summary['actions'][0]['enforcement'], 'DETECTED_AFTER_EXECUTION')
        self.assertNotIn(CANARY, json.dumps(summary))

    def test_arguments_errors_never_echo_paths_or_secrets(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(h.main(['--unknown', CANARY]), 1)
        self.assertEqual(out.getvalue(), '')
        self.assertEqual(err.getvalue(), 'codex-approval-hook: incomplete\n')

    def test_policy_traversal_and_oversize_are_refused(self):
        for raw in (json.dumps(dataclasses.asdict(policy(allowed_paths=('../escape',)))).encode(),
                    b'x' * (a.MAX_POLICY_BYTES + 1)):
            with open(self.policy_path, 'wb') as f:
                f.write(raw)
            pin = hashlib.sha256(raw).hexdigest()
            self.assertIn('deny', self.invoke(event('PreToolUse'), pin=pin)[1])

    def test_duplicate_missing_and_post_late_remain_gaps(self):
        self.invoke(event('PreToolUse'))
        self.invoke(event('PreToolUse'))
        self.invoke(event('Interrupt'), 'Interrupt')
        self.assertIn('INTERRUPTED_NO_POST', self.summary()['actions'][0]['problems'])
        self.invoke(event('PostToolUse'), 'PostToolUse')
        problems = self.summary()['actions'][0]['problems']
        self.assertIn('DUPLICATE_PRE', problems)
        self.assertIn('POST_AFTER_END', problems)
