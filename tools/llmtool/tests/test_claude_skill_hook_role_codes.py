"""#1230 refusal-code pin: claude_skill_hook refuses an unverified role with UNVERIFIED_ROLE."""
import io
import unittest
from unittest import mock

from llmtool import claude_skill_hook as hook


class SkillHookRoleCodeTests(unittest.TestCase):
    def run_handle(self, payload, environ):
        err = io.StringIO()
        with mock.patch.dict('os.environ', environ), mock.patch('sys.stderr', err):
            return hook.handle(payload), err.getvalue()

    def test_session_start_with_unknown_role_is_denied_in_enforce(self):
        payload = {'hook_event_name': 'SessionStart', 'source': 'startup'}
        response, log = self.run_handle(payload, {'LLMTOOL_HOOK_MODE': 'enforce', 'LLMTOOL_ROLE': 'intruder'})
        self.assertEqual(response['hookSpecificOutput']['error'], 'UNVERIFIED_ROLE')
        self.assertEqual(log, 'claude_skill_hook: UNVERIFIED_ROLE\n')
        response, log = self.run_handle(payload, {'LLMTOOL_HOOK_MODE': 'observe', 'LLMTOOL_ROLE': 'intruder'})
        self.assertEqual(response, {})
        self.assertEqual(log, 'claude_skill_hook: UNVERIFIED_ROLE\n')

    def test_unknown_subagent_type_and_read_role_are_unverified(self):
        payload = {'hook_event_name': 'SubagentStart', 'agent_type': 'unknown-agent'}
        response, log = self.run_handle(payload, {'LLMTOOL_HOOK_MODE': 'enforce'})
        self.assertEqual(response['hookSpecificOutput']['error'], 'UNVERIFIED_ROLE')
        self.assertEqual(log, 'claude_skill_hook: UNVERIFIED_ROLE\n')
        payload = {'hook_event_name': 'PostToolUse', 'tool_name': 'Read', 'tool_input': {'file_path': 'x'}}
        response, log = self.run_handle(payload, {'LLMTOOL_HOOK_MODE': 'enforce', 'LLMTOOL_ROLE': 'intruder'})
        self.assertEqual(response, {})
        self.assertEqual(log, 'claude_skill_hook: UNVERIFIED_ROLE\n')


if __name__ == '__main__':
    unittest.main()
