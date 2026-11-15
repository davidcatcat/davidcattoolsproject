"""#1230 refusal-code pin: claude_skill_hook Bash input, hook failure and output failure codes."""
import io
import json
import unittest
from unittest import mock

from llmtool import claude_skill_hook as hook

BASH = {'hook_event_name': 'PreToolUse', 'tool_name': 'Bash'}


class SkillHookCodeTests(unittest.TestCase):
    def run_handle(self, payload, mode):
        err = io.StringIO()
        with mock.patch.dict('os.environ', {'LLMTOOL_HOOK_MODE': mode}), mock.patch('sys.stderr', err):
            return hook.handle(payload), err.getvalue()

    def test_bash_input_without_a_string_command_is_bash_input_invalid(self):
        for tool_input in (None, 'ls', {'command': None}, {'command': ['ls']}):
            payload = dict(BASH, tool_input=tool_input)
            with self.subTest(tool_input=tool_input):
                response, log = self.run_handle(payload, 'enforce')
                self.assertEqual(response['hookSpecificOutput']['permissionDecision'], 'deny')
                self.assertEqual(response['hookSpecificOutput']['permissionDecisionReason'],
                                 'BASH_INPUT_INVALID')
                self.assertEqual(log, 'claude_skill_hook: BASH_INPUT_INVALID\n')
                response, log = self.run_handle(payload, 'observe')
                self.assertEqual(response, {})
                self.assertEqual(log, 'claude_skill_hook: BASH_INPUT_INVALID\n')

    def test_main_reports_hook_failure_and_output_failure(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict('os.environ', {'LLMTOOL_HOOK_MODE': 'enforce'}), \
                mock.patch.object(hook, 'handle', side_effect=RuntimeError('private detail')), \
                mock.patch('sys.stdin', io.StringIO(json.dumps(BASH))), \
                mock.patch('sys.stdout', out), mock.patch('sys.stderr', err):
            hook.main([])
        response = json.loads(out.getvalue())
        self.assertEqual(response['hookSpecificOutput']['permissionDecisionReason'], 'HOOK_FAILURE')
        self.assertEqual(err.getvalue(), 'claude_skill_hook: HOOK_FAILURE\n')
        self.assertNotIn('private detail', out.getvalue() + err.getvalue())
        closed, err = io.StringIO(), io.StringIO()
        closed.close()
        with mock.patch.object(hook, 'handle', return_value={}), \
                mock.patch('sys.stdin', io.StringIO(json.dumps(BASH))), \
                mock.patch('sys.stdout', closed), mock.patch('sys.stderr', err):
            hook.main([])
        self.assertEqual(err.getvalue(), 'claude_skill_hook: HOOK_OUTPUT_FAILED\n')


if __name__ == '__main__':
    unittest.main()
