"""Focused tests for the Claude Code hook protocol and policy checks."""

import io
import json
import os
import subprocess
import tempfile
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

    @staticmethod
    def _run_hook(payload, part=1):
        arguments = ['--part', str(part)] if part != 1 else []
        with mock.patch.object(hook.sys, 'stdin', io.StringIO(json.dumps(payload))), \
                mock.patch.object(hook.sys, 'stdout', io.StringIO()) as stdout:
            hook.main(arguments)
            return json.loads(stdout.getvalue())

    @mock.patch.object(hook, '_commit', return_value='a' * 40)
    @mock.patch.object(hook, 'build_manifest', return_value={
        'manifest_sha256': 'd' * 64,
        'entries': [{'path': 'skill.md', 'sha256': 'e' * 64, 'size': 8}],
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
        'entries': [{'path': 'skill.md', 'sha256': 'e' * 64, 'size': 8}],
    })
    @mock.patch.object(hook, 'read_manifest_files')
    @mock.patch.object(hook, '_record')
    @mock.patch.object(hook.skill_ledger, 'record')
    @mock.patch.object(hook.skill_ledger, 'check', return_value={
        'status': 'UNVERIFIED', 'reason': 'NOT_FOUND',
    })
    def test_last_part_alone_does_not_record_delivery(
            self, _check, _progress, delivery, read_files, _build, _commit):
        read_files.return_value = [('skills.txt', 'x' * 20000)]
        self._run_hook(self._session(), part=3)
        delivery.assert_not_called()

    @mock.patch.object(hook, '_commit', return_value='a' * 40)
    @mock.patch.object(hook, 'build_manifest', return_value={
        'manifest_sha256': 'd' * 64,
        'entries': [{'path': 'skill.md', 'sha256': 'e' * 64, 'size': 8}],
    })
    @mock.patch.object(hook, 'read_manifest_files')
    @mock.patch.object(hook, '_record')
    def test_missing_middle_part_does_not_record_delivery(
            self, delivery, read_files, _build, _commit):
        read_files.return_value = [('skills.txt', 'x' * 20000)]
        seen = set()

        def record(values):
            seen.add(values['head'])

        def check(*values, **_kwargs):
            return {'status': 'VERIFIED' if values[8] in seen else 'UNVERIFIED'}

        with mock.patch.object(hook.skill_ledger, 'record', side_effect=record), \
                mock.patch.object(hook.skill_ledger, 'check', side_effect=check):
            self._run_hook(self._session(), part=1)
            self._run_hook(self._session(), part=3)
        delivery.assert_not_called()

    def test_content_digest_ignores_commit_and_changes_with_required_content(self):
        first = {'commit': 'a' * 40, 'entries': [
            {'path': 'CLAUDE.md', 'sha256': 'b' * 64, 'size': 20},
        ]}
        same_content = dict(first, commit='c' * 40)
        changed = {'entries': [
            {'path': 'CLAUDE.md', 'sha256': 'd' * 64, 'size': 20},
        ]}
        self.assertEqual(hook._content_digest(first), hook._content_digest(same_content))
        self.assertNotEqual(hook._content_digest(first), hook._content_digest(changed))

    def test_check_recomputes_content_binding_across_commits(self):
        payload = {
            'session_id': 'session-main', 'agent_id': 'main',
            'cwd': '/repo', 'model': 'claude-test',
        }
        manifest = {'entries': [
            {'path': 'CLAUDE.md', 'sha256': 'b' * 64, 'size': 20},
        ]}
        with mock.patch.object(hook, '_commit', side_effect=['a' * 40, 'c' * 40]), \
                mock.patch.object(hook, '_manifest_context', return_value=(manifest, [])), \
                mock.patch.object(hook.skill_ledger, 'check', return_value={
                    'status': 'UNVERIFIED', 'reason': 'NOT_FOUND',
                }) as ledger_check:
            hook._check(payload, 'merge')
            hook._check(payload, 'merge')
        first = ledger_check.call_args_list[0].args
        second = ledger_check.call_args_list[1].args
        self.assertEqual(first[8:10], second[8:10])

        changed = {'entries': [
            {'path': 'CLAUDE.md', 'sha256': 'd' * 64, 'size': 20},
        ]}
        with mock.patch.object(hook, '_commit', return_value='e' * 40), \
                mock.patch.object(hook, '_manifest_context', return_value=(changed, [])), \
                mock.patch.object(hook.skill_ledger, 'check', return_value={
                    'status': 'UNVERIFIED', 'reason': 'MANIFEST_CHANGED',
                }) as ledger_check:
            hook._check(payload, 'merge')
        self.assertNotEqual(first[8:10], ledger_check.call_args.args[8:10])

    @mock.patch.object(hook, '_commit', return_value='a' * 40)
    @mock.patch.object(hook, 'build_manifest', return_value={
        'manifest_sha256': 'd' * 64,
        'entries': [{'path': 'skill.md', 'sha256': 'e' * 64, 'size': 8}],
    })
    @mock.patch.object(hook, 'read_manifest_files', return_value=[('skill.md', 'required')])
    @mock.patch.object(hook, '_record')
    def test_compact_redelivers_and_records_last_part(
            self, record, _files, _build, _commit):
        with mock.patch.object(hook.skill_ledger, 'record'), \
                mock.patch.object(hook.skill_ledger, 'check', return_value={
                    'status': 'VERIFIED', 'reason': None,
                }):
            response = self._run_hook(self._session('compact'))
        self.assertIn('required', response['hookSpecificOutput']['additionalContext'])
        record.assert_called_once()

    @mock.patch.object(hook, '_commit', return_value='a' * 40)
    @mock.patch.object(hook, 'build_manifest', return_value={
        'manifest_sha256': 'd' * 64,
        'entries': [{'path': 'skill.md', 'sha256': 'e' * 64, 'size': 8}],
    })
    @mock.patch.object(hook, 'read_manifest_files')
    @mock.patch.object(hook, '_record')
    def test_compact_and_resume_do_not_reuse_startup_part_markers(
            self, delivery, read_files, _build, _commit):
        read_files.return_value = [('skills.txt', 'x' * 20000)]
        seen = set()

        def record(values):
            seen.add((values['head'], values['manifest_sha256']))

        def check(*values, **_kwargs):
            return {'status': 'VERIFIED' if (values[8], values[9]) in seen
                    else 'UNVERIFIED'}

        with mock.patch.object(hook.skill_ledger, 'record', side_effect=record), \
                mock.patch.object(hook.skill_ledger, 'check', side_effect=check):
            self._run_hook(self._session(), part=1)
            self._run_hook(self._session(), part=2)
            self._run_hook(self._session('compact'), part=3)
            self._run_hook(self._session('resume'), part=3)
        delivery.assert_not_called()

    def test_emit_flushes_before_success(self):
        stream = mock.Mock()
        with mock.patch.object(hook.sys, 'stdout', stream):
            self.assertTrue(hook._emit({'ok': True}))
        stream.write.assert_called_once()
        stream.flush.assert_called_once()

    def test_emit_failure_is_reported(self):
        stream = mock.Mock()
        stream.flush.side_effect = OSError('closed')
        with mock.patch.object(hook.sys, 'stdout', stream), \
                mock.patch.object(hook, '_log') as log:
            self.assertFalse(hook._emit({'ok': True}))
        log.assert_called_once_with('HOOK_OUTPUT_FAILED')

    def test_output_flush_failure_does_not_finalize_delivery(self):
        stream = mock.Mock()
        stream.flush.side_effect = OSError('closed')
        payload = self._session()
        with mock.patch.object(hook.sys, 'stdin', io.StringIO(json.dumps(payload))), \
                mock.patch.object(hook.sys, 'stdout', stream), \
                mock.patch.object(hook, 'handle', return_value={'hookSpecificOutput': {}}), \
                mock.patch.object(hook, '_finalize_delivery') as finalize:
            hook.main([])
        finalize.assert_not_called()

    def test_bad_part_argument_returns_event_specific_error(self):
        payload = self._session('compact')
        with mock.patch.dict(os.environ, {'LLMTOOL_HOOK_MODE': 'enforce'}), \
                mock.patch.object(hook.sys, 'stdin', io.StringIO(json.dumps(payload))), \
                mock.patch.object(hook.sys, 'stdout', io.StringIO()) as stdout:
            hook.main(['--part', 'bad'])
        response = json.loads(stdout.getvalue())
        self.assertEqual(response['hookSpecificOutput']['hookEventName'], 'SessionStart')
        self.assertEqual(response['hookSpecificOutput']['error'], 'PART_OUT_OF_RANGE')

    @mock.patch.object(hook, '_commit', return_value='a' * 40)
    @mock.patch.object(hook, 'build_manifest', return_value={
        'manifest_sha256': 'd' * 64,
        'entries': [{'path': 'skill.md', 'sha256': 'e' * 64, 'size': 8}],
    })
    @mock.patch.object(hook, 'read_manifest_files', return_value=[('skill.md', 'required')])
    @mock.patch.object(hook.skill_ledger, 'record')
    def test_parent_and_child_delivery_bind_separate_agent_ids(
            self, ledger_record, _files, _build, _commit):
        with mock.patch.dict(os.environ, {'LLMTOOL_ROLE': 'pm'}), \
                mock.patch.object(hook.skill_ledger, 'check', return_value={
                    'status': 'VERIFIED', 'reason': None,
                }):
            self._run_hook(self._session())
            child = {
                'hook_event_name': 'SubagentStart', 'session_id': 'session-main',
                'agent_id': 'child-1', 'agent_type': 'developer', 'cwd': '/repo',
                'model': 'claude-test',
            }
            self._run_hook(child)
        delivered = [call.args[0] for call in ledger_record.call_args_list
                     if call.args[0]['evidence_level'] == 'HOOK_DELIVERED']
        self.assertEqual(delivered[0]['agent_id'], 'main')
        self.assertEqual(delivered[1]['agent_id'], 'child-1')
        self.assertEqual(delivered[1]['role'], 'developer')

    @mock.patch.object(hook, '_log')
    def test_unknown_subagent_role_is_unverified(self, _log):
        with mock.patch.dict(os.environ, {'LLMTOOL_HOOK_MODE': 'enforce'}):
            response = hook.handle({
                'hook_event_name': 'SubagentStart', 'agent_id': 'child',
                'agent_type': 'unknown-role', 'session_id': 'session',
            })
        self.assertEqual(
            response['hookSpecificOutput']['error'], 'UNVERIFIED_ROLE',
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
            "bash -lc 'gh pr merge 12'",
            "bash -ec 'git push origin topic'",
            "cd work && bash -ec 'gh -R owner/repo pr merge 12'",
            "sudo bash -lc 'gh --repo owner/repo pr merge 12'",
            'gh pr comment 12 --body-file verdict.txt',
            'gh pr review 12 -F verdict.txt',
            'gh pr comment 12 --body-file=v.md',
            'gh pr review 12 -Fv.md',
            'gh --repo=o/r pr merge 12',
            'gh -Ro/r pr merge 12',
            'echo ready; git push origin topic',
        )
        for command in protected:
            with self.subTest(command=command):
                self.assertTrue(hook._protected(command))
        self.assertFalse(hook._protected('git status --short'))
        self.assertFalse(hook._protected('gh pr comment 12 --body ordinary'))
        self.assertFalse(hook._protected('echo git push origin topic'))
        self.assertFalse(hook._protected('echo ready'))

    def test_protected_commands_map_to_their_gates(self):
        cases = {
            'gh pr merge 12': ['merge'],
            'git push origin topic': ['publish'],
            'llmtool task delegate abc': ['dispatch'],
            'llmtool task complete abc': ['complete'],
            'llmtool task advance abc': ['complete'],
            'gh pr comment 12 --body review_verdict=PASS': ['review'],
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(hook._protected_gates(command), expected)

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

    def test_malformed_bash_payloads_deny_in_enforce_mode(self):
        payloads = (
            {'hook_event_name': 'PreToolUse', 'tool_name': 'Bash'},
            {'hook_event_name': 'PreToolUse', 'tool_name': 'Bash', 'tool_input': 'bad'},
            {'hook_event_name': 'PreToolUse', 'tool_name': 'Bash',
             'tool_input': {'command': 12}},
            {'hook_event_name': 'PreToolUse', 'tool_input': {'command': 'git push'}},
        )
        with mock.patch.dict(os.environ, {'LLMTOOL_HOOK_MODE': 'enforce'}):
            for payload in payloads:
                with self.subTest(payload=payload):
                    response = hook.handle(payload)
                    self.assertEqual(
                        response['hookSpecificOutput']['permissionDecision'], 'deny',
                    )

    def test_malformed_bash_payloads_allow_and_log_in_observe_mode(self):
        payload = {'hook_event_name': 'PreToolUse', 'tool_name': 'Bash',
                   'tool_input': {'command': None}}
        with mock.patch.object(hook, '_log') as log:
            self.assertEqual(hook.handle(payload), {})
        log.assert_called_once_with('BASH_INPUT_INVALID')

    def test_malformed_shell_command_is_protected_and_fails_closed(self):
        payload = {'hook_event_name': 'PreToolUse', 'tool_name': 'Bash',
                   'tool_input': {'command': "git push origin topic 'unmatched"}}
        with mock.patch.dict(os.environ, {'LLMTOOL_HOOK_MODE': 'enforce'}):
            with mock.patch.object(hook, '_check') as check:
                response = hook.handle(payload)
        check.assert_not_called()
        self.assertEqual(
            response['hookSpecificOutput']['permissionDecisionReason'],
            'COMMAND_UNPARSEABLE',
        )

    @mock.patch.object(hook, '_check', return_value={
        'status': 'UNVERIFIED', 'reason': 'NOT_FOUND',
    })
    def test_balanced_publish_command_checks_publish_receipt(self, check):
        payload = {'hook_event_name': 'PreToolUse', 'tool_name': 'Bash',
                   'tool_input': {'command': "bash -c 'git push origin topic'"}}
        with mock.patch.dict(os.environ, {'LLMTOOL_HOOK_MODE': 'observe'}):
            self.assertEqual(hook.handle(payload), {})
        check.assert_called_once_with(payload, 'publish')

    def test_out_of_order_parts_need_all_current_generation_markers(self):
        pending = {
            'base': {'head': 'h', 'manifest_sha256': 'm'}, 'part': 3,
            'count': 3, 'generation': 'compact', 'payload': {}, 'role': 'pm',
            'repo': '/repo', 'content_digest': 'digest', 'manifest': {},
            'event_name': 'SessionStart',
        }
        observed = set()

        def record(values):
            observed.add((values['head'], values['manifest_sha256']))

        def check(*values, **_kwargs):
            return {'status': 'VERIFIED' if (values[8], values[9]) in observed
                    else 'UNVERIFIED'}

        with mock.patch.object(hook, '_PENDING_DELIVERY', pending), \
                mock.patch.object(hook.skill_ledger, 'record', side_effect=record), \
                mock.patch.object(hook.skill_ledger, 'check', side_effect=check), \
                mock.patch.object(hook, '_record') as delivered:
            hook._finalize_delivery()
        delivered.assert_not_called()

    @mock.patch.object(hook, '_commit', return_value='a' * 40)
    @mock.patch.object(hook, 'build_manifest', return_value={
        'manifest_sha256': 'd' * 64,
        'entries': [{'path': 'skill.md', 'sha256': 'e' * 64, 'size': 8}],
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

    def test_record_then_check_uses_content_bound_receipt_without_ledger_mocks(self):
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / 'repo'
            repo.mkdir()
            subprocess.run(['git', 'init', '-q'], cwd=repo, check=True)
            subprocess.run(['git', 'config', 'user.email', 'hook@example.invalid'],
                           cwd=repo, check=True)
            subprocess.run(['git', 'config', 'user.name', 'Hook Tests'],
                           cwd=repo, check=True)
            files = {
                'CLAUDE.md': 'test governance\n',
                'skills/ai-team-delivery/SKILL.md': 'delivery\n',
                'skills/ai-team-delivery/pm-continuation.md': 'pm\n',
                'skills/ai-team-delivery/pm-continuation-reference.md': 'reference\n',
            }
            for relative, content in files.items():
                target = repo / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding='utf-8')
            subprocess.run(['git', 'add', '-A'], cwd=repo, check=True)
            subprocess.run(['git', 'commit', '-qm', 'fixture'], cwd=repo, check=True)
            ledger_path = Path(directory) / 'skill-ledger.jsonl'
            with mock.patch.object(hook.skill_ledger, '_path', return_value=ledger_path), \
                    mock.patch.dict(os.environ, {
                        'LLMTOOL_GATE': 'complete',
                        'LLMTOOL_ROLE': 'pm',
                        'LLMTOOL_HOOK_MODE': 'enforce',
                    }):
                payload = self._session(cwd=str(repo))
                _, contexts = hook._manifest_context(
                    str(repo), hook._commit(str(repo)), 'pm', 'complete',
                )
                for part in range(1, len(contexts) + 1):
                    self._run_hook(payload, part=part)
                response = hook.handle({
                    'hook_event_name': 'PreToolUse', 'tool_name': 'Bash',
                    'tool_input': {'command': 'llmtool task complete task-1'},
                    'session_id': 'session-main', 'cwd': str(repo),
                    'model': 'claude-test',
                })
            self.assertEqual(response, {})


if __name__ == '__main__':
    unittest.main()
