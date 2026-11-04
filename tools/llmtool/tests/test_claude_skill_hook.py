"""Focused tests for single-index delivery and Read-bound skill evidence."""

import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from llmtool import claude_skill_hook as hook
from llmtool.skill_manifest import resolve


class ClaudeSkillHookTests(unittest.TestCase):
    def setUp(self):
        hook._failed_compactions.clear()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.repo = root / 'repo'
        self.repo.mkdir()
        self.ledger = root / 'skill-ledger.jsonl'
        subprocess.run(['git', 'init', '-q'], cwd=self.repo, check=True)
        subprocess.run(['git', 'config', 'user.email', 'hook@example.invalid'], cwd=self.repo, check=True)
        subprocess.run(['git', 'config', 'user.name', 'Hook Tests'], cwd=self.repo, check=True)
        paths = {path for role in ('pm', 'developer', 'reviewer', 'high-debug')
                 for gate in ('dispatch', 'develop', 'review', 'complete', 'publish', 'merge')
                 for path in resolve(role, gate)}
        paths.add('CLAUDE.md')
        for relative in paths:
            target = self.repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('line one\nline two\n', encoding='utf-8')
        self.commit()
        self.env = mock.patch.dict(os.environ, {
            'LLMTOOL_HOOK_MODE': 'enforce', 'LLMTOOL_ROLE': 'developer',
            'LLMTOOL_GATE': 'publish', 'LLMTOOL_TASK_ID': 'task-1',
            'LLMTOOL_RUN_ID': 'run-1', 'LLMTOOL_MODEL': 'claude-test',
        }, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.ledger_path = mock.patch.object(hook.skill_ledger, '_path', return_value=self.ledger)
        self.ledger_path.start()
        self.addCleanup(self.ledger_path.stop)

    def commit(self):
        subprocess.run(['git', 'add', '-A'], cwd=self.repo, check=True)
        subprocess.run(['git', 'commit', '-qm', 'fixture'], cwd=self.repo, check=True)

    def event(self, payload):
        with mock.patch.object(hook.sys, 'stdin', io.StringIO(json.dumps(payload))), \
                mock.patch.object(hook.sys, 'stdout', io.StringIO()) as stdout:
            hook.main([])
            return json.loads(stdout.getvalue())

    def session(self, source='startup', **values):
        value = {'hook_event_name': 'SessionStart', 'source': source,
                 'session_id': 'session-1', 'cwd': str(self.repo), 'model': 'claude-test'}
        value.update(values)
        return value

    def post_read(self, path, **values):
        payload = {'hook_event_name': 'PostToolUse', 'tool_name': 'Read',
                   'session_id': 'session-1', 'cwd': str(self.repo),
                   'tool_input': {'file_path': str(path)}, 'model': 'claude-test'}
        payload.update(values)
        return self.event(payload)

    def protected(self, **values):
        payload = {'hook_event_name': 'PreToolUse', 'tool_name': 'Bash',
                   'tool_input': {'command': 'git push origin branch'},
                   'session_id': 'session-1', 'cwd': str(self.repo), 'model': 'claude-test'}
        payload.update(values)
        return self.event(payload)

    def required_paths(self, role='developer', gate='publish'):
        manifest = hook.build_manifest(
            str(self.repo), hook._commit(str(self.repo)), role, gate,
            provider='claude',
        )
        return tuple(entry['path'] for entry in manifest['entries'])

    def read_all(self):
        for relative in self.required_paths():
            self.post_read(self.repo / relative)

    def test_start_event_emits_one_bounded_index_for_every_role_gate_pair(self):
        for role in ('pm', 'developer', 'reviewer', 'high-debug'):
            for gate in ('dispatch', 'develop', 'review', 'complete', 'publish', 'merge'):
                with self.subTest(role=role, gate=gate), \
                        mock.patch.dict(os.environ, {'LLMTOOL_ROLE': role, 'LLMTOOL_GATE': gate}):
                    result = self.event(self.session())
                    context = result['hookSpecificOutput']['additionalContext']
                    manifest = hook.build_manifest(str(self.repo), hook._commit(str(self.repo)),
                                                    role, gate, provider='claude')
                    self.assertLessEqual(len(context), 9000)
                    self.assertIn('role: ' + role, context)
                    self.assertIn('gate: ' + gate, context)
                    self.assertIn(manifest['manifest_sha256'], context)
                    self.assertIn('Read every listed file.', context)
                    for entry in manifest['entries']:
                        self.assertIn(entry['path'] + ' sha256=' + entry['sha256'], context)

    def test_start_context_is_a_single_index(self):
        result = self.event(self.session())
        self.assertEqual(set(result['hookSpecificOutput']), {'hookEventName', 'additionalContext'})

    def test_read_records_path_and_current_blob_hash(self):
        target = self.repo / self.required_paths()[0]
        self.post_read(target)
        records = hook.skill_ledger._read(self.ledger)
        rows = [r for r in records if r.get('event_type') == 'OBSERVED_READ']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['session_id'], 'session-1')
        self.assertEqual(rows[0]['agent_id'], 'main')
        self.assertEqual(rows[0]['role'], 'developer')
        self.assertEqual(rows[0]['provider'], 'claude')
        self.assertEqual(rows[0]['model'], 'claude-test')
        self.assertEqual(rows[0]['repo'], os.path.realpath(self.repo))
        self.assertEqual(rows[0]['path'], self.required_paths()[0])
        self.assertEqual(rows[0]['blob_sha256'], hook._safe_repo_file(str(self.repo), str(target))[1])

    def test_partial_read_is_not_counted_and_zero_offset_is_start(self):
        target = self.repo / self.required_paths()[0]
        self.post_read(target, tool_input={'file_path': str(target), 'offset': 2})
        self.assertFalse(any(r.get('event_type') == 'OBSERVED_READ'
                             for r in hook.skill_ledger._read(self.ledger)))
        self.post_read(target, tool_input={'file_path': str(target), 'offset': 0})
        rows = [r for r in hook.skill_ledger._read(self.ledger)
                if r.get('event_type') == 'OBSERVED_READ']
        self.assertEqual(len(rows), 1)

    def test_nonrequired_missing_and_symlink_reads_record_nothing(self):
        extra = self.repo / 'extra.md'
        extra.write_text('extra\n', encoding='utf-8')
        absent = self.repo / 'absent.md'
        link = self.repo / self.required_paths()[0]
        missing_required = self.repo / self.required_paths()[1]
        missing_required.unlink()
        link.unlink()
        link.symlink_to(extra)
        for target in (extra, absent, link, missing_required):
            self.post_read(target)
        self.assertFalse(any(r.get('event_type') == 'OBSERVED_READ'
                             for r in hook.skill_ledger._read(self.ledger)))

    def test_not_read_denies_enforce_and_observe_logs_but_allows(self):
        denied = self.protected()
        self.assertEqual(denied['hookSpecificOutput']['permissionDecisionReason'], 'NOT_READ')
        with mock.patch.dict(os.environ, {'LLMTOOL_HOOK_MODE': 'observe'}), \
                mock.patch.object(hook, '_log') as log:
            self.assertEqual(self.protected(), {})
            log.assert_called_once_with('NOT_READ')

    def test_all_required_reads_allow_protected_command(self):
        self.read_all()
        self.assertEqual(self.protected(), {})

    def test_dirty_required_file_cannot_be_recorded_as_read(self):
        target = self.repo / 'CLAUDE.md'
        target.write_text('uncommitted replacement\n', encoding='utf-8')
        self.post_read(target)
        self.assertFalse(any(r.get('event_type') == 'OBSERVED_READ'
                             for r in hook.skill_ledger._read(self.ledger)))
        self.assertEqual(
            self.protected()['hookSpecificOutput']['permissionDecisionReason'],
            'NOT_READ',
        )

    def test_tool_events_need_no_repeated_session_model(self):
        with mock.patch.dict(os.environ, {'LLMTOOL_MODEL': ''}):
            self.event(self.session(model='claude-opus-5-5'))
            for relative in self.required_paths():
                self.post_read(self.repo / relative, model=None)
            self.assertEqual(self.protected(model=None), {})

    def test_compaction_append_failure_invalidates_session(self):
        self.read_all()
        with mock.patch.object(hook, '_append_event', side_effect=OSError('ledger unavailable')):
            self.event(self.session('compact'))
        denied = self.protected()
        self.assertEqual(denied['hookSpecificOutput']['permissionDecisionReason'], 'COMPACTED')

    def test_changed_blob_denies_as_stale(self):
        target = self.repo / self.required_paths()[0]
        self.post_read(target)
        target.write_text('changed after Read\n', encoding='utf-8')
        denied = self.protected()
        self.assertEqual(denied['hookSpecificOutput']['permissionDecisionReason'], 'STALE_BLOB')

    def test_compaction_invalidates_prior_reads(self):
        for source in ('compact', 'resume'):
            with self.subTest(source=source):
                self.read_all()
                self.event(self.session(source))
                denied = self.protected()
                self.assertEqual(denied['hookSpecificOutput']['permissionDecisionReason'], 'COMPACTED')

    def test_child_reads_do_not_satisfy_parent_and_parent_does_not_satisfy_child(self):
        self.read_all()
        child = {'agent_id': 'child-1', 'agent_type': 'developer'}
        denied = self.protected(**child)
        self.assertEqual(denied['hookSpecificOutput']['permissionDecisionReason'], 'NOT_READ')
        for relative in self.required_paths():
            self.post_read(self.repo / relative, **child)
        self.assertEqual(self.protected(**child), {})

    def test_reads_of_other_sessions_do_not_count(self):
        self.read_all()
        denied = self.protected(session_id='session-other')
        self.assertEqual(denied['hookSpecificOutput']['permissionDecisionReason'], 'NOT_READ')

    def test_malformed_stdin_fails_closed_in_enforce_and_observe_defaults(self):
        with mock.patch.object(hook.sys, 'stdin', io.StringIO('{')), \
                mock.patch.object(hook.sys, 'stdout', io.StringIO()) as stdout:
            hook.main([])
            self.assertEqual(json.loads(stdout.getvalue())['hookSpecificOutput']['permissionDecision'], 'deny')
        with mock.patch.dict(os.environ, {'LLMTOOL_HOOK_MODE': 'unexpected'}), \
                mock.patch.object(hook.sys, 'stdin', io.StringIO('{')), \
                mock.patch.object(hook.sys, 'stdout', io.StringIO()) as stdout:
            hook.main([])
            self.assertEqual(json.loads(stdout.getvalue()), {})

    def test_protected_word_matcher(self):
        cases = {
            'git push origin topic': ['publish'],
            'git -C /repo push': ['publish'],
            'git -c user.name=Test push': ['publish'],
            'command git push': ['publish'],
            'env git push': ['publish'],
            'exec git push': ['publish'],
            'time git push': ['publish'],
            'nohup git push': ['publish'],
            'sudo git push': ['publish'],
            '! git push': ['publish'],
            'if git push; then :; fi': ['publish'],
            'zsh -c "git push"': ['publish'],
            'bash -c "git push"': ['publish'],
            'gh pr -R o/r merge 1': ['merge'],
            'gh -R o/r pr merge 1': ['merge'],
            'gh pr --repo o/r merge 1': ['merge'],
            'gh --repo o/r pr merge 1': ['merge'],
            'gh pr merge 1 --repo o/r': ['merge'],
            'gh pr --repo=o/r merge 1': ['merge'],
            'gh --repo=o/r pr merge 1': ['merge'],
            'gh pr merge 12': ['merge'],
            'gh pr comment 12 --body x': ['review'],
            'gh pr review 12': ['review'],
            'gh issue comment 12': ['review'],
            'llmtool task delegate task-1': ['dispatch'],
            'llmtool task complete task-1': ['complete'],
            'llmtool task advance task-1': ['complete'],
            'python -m llmtool.cli task complete a': ['complete'],
            'llmtool --json task complete a': ['complete'],
            'python -m llmtool task complete a': ['complete'],
            'python3.13 -m llmtool.cli task complete a': ['complete'],
            '.venv/bin/python -B -m llmtool.cli task complete a': ['complete'],
            'python -I -m llmtool.cli task complete a': ['complete'],
            'FOO=1 git push': ['publish'],
            'git push origin main # don\'t': ['publish'],
            'git commit -m "push fix"': [],
            'git log --grep=push': [],
            'grep -rn "git push" .': [],
            'rg "gh pr merge" docs': [],
            'echo git push origin topic': [],
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(hook._protected_gates(command), expected)

    def test_missing_hook_event_for_bash_fails_closed(self):
        result = hook.handle({'tool_name': 'Bash', 'tool_input': {'command': 'git push'}})
        self.assertEqual(result['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertEqual(result['hookSpecificOutput']['permissionDecisionReason'], 'INPUT_INVALID')


if __name__ == '__main__':
    unittest.main()
