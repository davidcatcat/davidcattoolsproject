"""Focused tests for the Claude Code hook protocol and policy checks."""

import io
import json
import os
import unittest
from unittest import mock

from llmtool import claude_skill_hook as hook


class ClaudeSkillHookTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {
            'LLMTOOL_HOOK_MODE': 'observe',
            'LLMTOOL_TASK_ID': 'task-1',
            'LLMTOOL_RUN_ID': 'run-1',
            'LLMTOOL_MODEL': 'claude-test',
        }, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    @staticmethod
    def _session(source='startup', **values):
        payload = {
            'hook_event_name': 'SessionStart', 'source': source,
            'session_id': 'session-main', 'cwd': '/repo', 'model': 'claude-test',
        }
        payload.update(values)
        return payload

    @mock.patch.object(hook, '_commit', return_value='a' * 40)
    @mock.patch.object(hook, 'build_manifest', return_value={
        'manifest_sha256': 'd' * 64,
    })
    @mock.patch.object(hook, 'read_manifest_files')
    def test_context_is_split_to_limit_and_names_part_count(
            self, read_files, _build, _commit):
        read_files.return_value = [('skills.txt', 'x' * 20000)]
        first = hook.handle(self._session(), part=1)
        context = first['hookSpecificOutput']['additionalContext']
        self.assertLessEqual(len(context), 9000)
        self.assertIn('part 1 of 3', context)
        second = hook.handle(self._session(), part=2)
        third = hook.handle(self._session(), part=3)
        self.assertIn('part 2 of 3', second['hookSpecificOutput']['additionalContext'])
        self.assertIn('part 3 of 3', third['hookSpecificOutput']['additionalContext'])
        self.assertIn('manifest_sha256=' + 'd' * 64, context)

    @mock.patch.object(hook, '_commit', return_value='a' * 40)
    @mock.patch.object(hook, 'build_manifest', return_value={
        'manifest_sha256': 'd' * 64,
    })
    @mock.patch.object(hook, 'read_manifest_files', return_value=[('skill.md', 'required')])
    @mock.patch.object(hook, '_record')
    def test_compact_redelivers_and_records_last_part(
            self, record, _files, _build, _commit):
        response = hook.handle(self._session('compact'))
        self.assertIn('required', response['hookSpecificOutput']['additionalContext'])
        record.assert_called_once()

    @mock.patch.object(hook, '_commit', return_value='a' * 40)
    @mock.patch.object(hook, 'build_manifest', return_value={
        'manifest_sha256': 'd' * 64,
    })
    @mock.patch.object(hook, 'read_manifest_files', return_value=[('skill.md', 'required')])
    @mock.patch.object(hook.skill_ledger, 'record')
    def test_parent_and_child_delivery_bind_separate_agent_ids(
            self, ledger_record, _files, _build, _commit):
        with mock.patch.dict(os.environ, {'LLMTOOL_ROLE': 'pm'}):
            hook.handle(self._session())
        child = {
            'hook_event_name': 'SubagentStart', 'session_id': 'session-main',
            'agent_id': 'child-1', 'agent_type': 'developer', 'cwd': '/repo',
            'model': 'claude-test',
        }
        hook.handle(child)
        self.assertEqual(ledger_record.call_args_list[0].args[0]['agent_id'], 'main')
        self.assertEqual(ledger_record.call_args_list[1].args[0]['agent_id'], 'child-1')
        self.assertEqual(ledger_record.call_args_list[1].args[0]['role'], 'developer')

    @mock.patch.object(hook, '_log')
    def test_unknown_subagent_role_is_unverified(self, _log):
        with mock.patch.dict(os.environ, {'LLMTOOL_HOOK_MODE': 'enforce'}):
            response = hook.handle({
                'hook_event_name': 'SubagentStart', 'agent_id': 'child',
                'agent_type': 'unknown-role', 'session_id': 'session',
            })
        self.assertEqual(
            response['hookSpecificOutput']['permissionDecisionReason'],
            'UNVERIFIED_ROLE',
        )

    def test_protected_command_forms(self):
        protected = (
            'gh pr merge 12',
            'git push origin topic',
            'gh pr comment 12 --body review_verdict=PASS',
            'gh pr review 12 --body review_verdict: FAIL',
            'llmtool task delegate abc',
            'llmtool task complete abc',
            'llmtool task advance abc',
            '/usr/bin/env MODE=x gh pr merge 12',
            "/usr/bin/env bash -c 'git push origin topic'",
            "bash -c 'llmtool task complete abc'",
        )
        for command in protected:
            with self.subTest(command=command):
                self.assertTrue(hook._protected(command))
        self.assertFalse(hook._protected('git status --short'))
        self.assertFalse(hook._protected('gh pr comment 12 --body ordinary'))

    @mock.patch.object(hook, '_check', return_value={
        'status': 'UNVERIFIED', 'reason': 'WRONG_HEAD',
    })
    def test_deny_enforce_and_allow_observe(self, _check):
        payload = {
            'hook_event_name': 'PreToolUse', 'tool_name': 'Bash',
            'tool_input': {'command': 'git push origin topic'},
        }
        with mock.patch.dict(os.environ, {'LLMTOOL_HOOK_MODE': 'enforce'}):
            denied = hook.handle(payload)
        self.assertEqual(denied['hookSpecificOutput']['permissionDecision'], 'deny')
        with mock.patch.dict(os.environ, {'LLMTOOL_HOOK_MODE': 'observe'}):
            self.assertEqual(hook.handle(payload), {})

    @mock.patch.object(hook, '_commit', return_value='a' * 40)
    @mock.patch.object(hook, 'build_manifest', return_value={
        'manifest_sha256': 'd' * 64,
    })
    @mock.patch.object(hook, 'read_manifest_files', return_value=[('skill.md', 'x')])
    @mock.patch.object(hook.skill_ledger, 'check', return_value={
        'status': 'UNVERIFIED', 'reason': 'LEDGER_UNREADABLE',
    })
    def test_ledger_unreadable_blocks_protected_command_in_enforce(
            self, _check, _files, _build, _commit):
        payload = {
            'hook_event_name': 'PreToolUse', 'tool_name': 'Bash',
            'tool_input': {'command': 'gh pr merge 12'},
            'session_id': 'session-main', 'cwd': '/repo',
        }
        with mock.patch.dict(os.environ, {'LLMTOOL_HOOK_MODE': 'enforce'}):
            response = hook.handle(payload)
        self.assertEqual(response['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertEqual(
            response['hookSpecificOutput']['permissionDecisionReason'],
            'LEDGER_UNREADABLE',
        )

    def test_malformed_json_observe_and_enforce(self):
        for mode, expected in (('observe', {}), ('enforce', 'deny')):
            with self.subTest(mode=mode):
                with mock.patch.dict(os.environ, {'LLMTOOL_HOOK_MODE': mode}):
                    with mock.patch.object(hook.sys, 'stdin', io.StringIO('{')):
                        with mock.patch.object(
                                hook.sys, 'stdout', io.StringIO()) as stdout:
                            hook.main([])
                            value = json.loads(stdout.getvalue())
                if expected == 'deny':
                    self.assertEqual(
                        value['hookSpecificOutput']['permissionDecision'], 'deny',
                    )
                else:
                    self.assertEqual(value, expected)


if __name__ == '__main__':
    unittest.main()
