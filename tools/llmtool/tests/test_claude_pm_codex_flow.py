"""Offline end-to-end Claude-PM flow through llmtool (#352).

An active Claude PM records its decision (record-pm, no provider call), llmtool
delegates the low-risk assignment to the Codex Developer route, an Executor
applies the returned diff in a registered D6 worktree, `task complete` verifies
the result, and the Claude PM reads the bounded evidence back with `task show`.
Every provider is a fake; no network, credential or real CLI is used.
"""

import json
import os
import subprocess
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from llmtool.providers.base import Reply
from llmtool.task_store import TaskStore
from llmtool.task_submission_cli import (
    _EXECUTOR_RESULT_SYSTEM, run_task_complete, run_task_delegate,
    run_task_record_pm, run_task_show)
from review_trust_fixture import start_module as _start_trust_fixture
from review_trust_fixture import stop_module as _stop_trust_fixture

# Reuse the registered-D6 Executor fixture (temp Git repo, D6 registration and
# ENV-2 marker) without re-collecting its tests in this module.
import test_task_completion as completion_fixture


def setUpModule():
    _start_trust_fixture()


def tearDownModule():
    _stop_trust_fixture()

_NEW_TEST = ('import unittest\nclass Fixture(unittest.TestCase):\n'
             '    def test_ok(self): self.assertEqual(2, 1 + 1)\n')


class FakeCodexDeveloper:
    """Stands in for the codex-cli client: returns one unified diff proposal."""

    def __init__(self, diff):
        self.diff, self.calls = diff, []

    def complete(self, messages):
        self.calls.append(messages)
        return Reply(self.diff, {})


def codex_developer_config(profile, require_configured=True):
    return {'active_provider': profile, 'providers': {profile: {
        'type': 'codex-cli', 'model': 'gpt-6-luna', 'timeout_seconds': 10,
        'provider_options': {'reasoning_effort': 'high'}}},
        'generation': {'temperature': 0, 'max_tokens': 64}}


class ClaudePMCodexFlowTests(unittest.TestCase):
    setUp = completion_fixture.TaskCompletionTests.setUp
    _git = completion_fixture.TaskCompletionTests._git
    _write_registration = completion_fixture.TaskCompletionTests._write_registration
    _write_env2_marker = completion_fixture.TaskCompletionTests._write_env2_marker

    def _proposal(self):
        """Build the diff a Codex Developer would propose, leaving the tree clean."""
        path = self.root / self.source_path
        original = path.read_text()
        path.write_text(_NEW_TEST)
        diff = subprocess.check_output(['/usr/bin/git', 'diff', '--', self.source_path],
                                       cwd=self.root, text=True)
        path.write_text(original)
        self.assertEqual(self._git('status', '--porcelain'), '')
        return diff

    def _executor_applies(self, artifact):
        """The Executor (not llmtool) applies and commits the accepted proposal."""
        subprocess.run(['/usr/bin/git', 'apply', '--index', '-'], cwd=self.root,
                       input=artifact, text=True, check=True)
        self._git('commit', '-m', 'executor applies Codex proposal')
        head = self._git('rev-parse', 'HEAD')
        registry = Path(self.common) / 'llmtool-worktrees' / (self.task_id + '.json')
        record = json.loads(registry.read_text())
        record['head_sha'] = head
        self._write_env2_marker(record, self.root, submitted_head=head, current_head=head)
        registry.write_text(json.dumps(record))
        registry.chmod(0o600)
        return head

    def test_claude_pm_records_delegates_to_codex_and_reads_result(self):
        store = TaskStore(self.base / 'tasks')
        forbidden = AssertionError('the active Claude PM is never called as a provider')

        # 1. The active Claude PM records its own low-risk Codex assignment.
        response = json.dumps({
            'schema_version': 1, 'summary': 'Small, testable fixture change.',
            'developer_assignment': {
                'route': 'codex', 'risk': 'LOW',
                'goal': 'Make the fixture test assert real arithmetic.',
                'acceptance': ['the fixture test passes'],
            },
        })
        envelope = StringIO(json.dumps({'request': 'Tighten the fixture test.',
                                        'response': response}))
        out = StringIO()
        with patch('llmtool.task_submission_cli.resolve_profile_config',
                   side_effect=forbidden), redirect_stdout(out):
            self.assertEqual(run_task_record_pm(Namespace(stdin_json=True), store=store,
                                                input_stream=envelope), 0)
        task_id = json.loads(out.getvalue())['task_id']
        recorded = store.get(task_id)
        self.assertEqual(recorded['pm_provenance']['source'], 'ACTIVE_PM_RUNTIME')
        self.assertIs(recorded['pm_provenance']['provider_invoked'], False)
        self.assertEqual(recorded['pm_assignment']['route'], 'codex')
        # G4: the record names the active runtime, not a codex-cli PM profile.
        self.assertEqual(recorded['pm_provider_type'], 'active-pm-runtime')
        self.assertEqual(recorded['pm_route_binding'], {
            'type': 'active-pm-runtime', 'model': None, 'base_url': None})

        # 2. llmtool delegates to the Codex Developer route (fake codex-cli client).
        codex = FakeCodexDeveloper(self._proposal())
        self.assertRegex(codex.diff, r'(?m)^index [0-9a-f]+\.\.[0-9a-f]+ 100644$')
        profiles = []

        def resolve(profile, require_configured=True):
            profiles.append(profile)
            return codex_developer_config(profile, require_configured)

        previous = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        out = StringIO()
        with patch('llmtool.task_submission_cli.resolve_profile_config',
                   side_effect=resolve), \
             patch('llmtool.task_submission_cli.api_key', side_effect=forbidden), \
             patch('llmtool.task_submission_cli.create_client',
                   return_value=codex) as create, \
             redirect_stdout(out):
            code = run_task_delegate(Namespace(
                task_id=task_id, provider='codex', selection_source='PM',
                executor_task=self.task_id, read_path=[self.source_path],
                write_path=[self.source_path], acceptance=['the fixture test passes'],
                egress_basis='Synthetic offline fixture; no sensitive data.',
                context='Fixture test file only.'), store=store)
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual(len(codex.calls), 1)
        # G1: the Developer call uses its own profile, never the Codex PM profile.
        self.assertEqual(profiles, ['codex-developer'])
        self.assertEqual(create.call_count, 1)
        # G2: the Developer profile's reasoning effort reaches the codex-cli client.
        self.assertEqual(create.call_args.args[0]['providers']['codex-developer']
                         ['provider_options'], {'reasoning_effort': 'high'})
        delegated = store.get(task_id)
        developer = delegated['developer']
        self.assertEqual(developer['state'], 'DEVELOPER_RESPONDED')
        self.assertEqual(developer['provider_profile'], 'codex')
        self.assertEqual(developer['provider_type'], 'codex-cli')
        self.assertEqual(delegated['completion']['state'], 'WAITING_EXECUTOR')
        audit = delegated['dispatch_audit']
        self.assertEqual({entry['worker_key'] for entry in audit}, {'codex-developer'})
        self.assertEqual({entry['decision'] for entry in audit}, {'ALLOWED'})
        self.assertIn('DISPATCH', {entry['action'] for entry in audit})

        # 3. The Executor applies the proposal in the registered D6 worktree.
        result_head = self._executor_applies(developer['artifact'])

        # 4. Executor-result callback: real D6/Git/test evidence, no PM provider call.
        out = StringIO()
        with patch('llmtool.task_submission_cli.task_completion._github_get',
                   return_value={'ref': 'refs/heads/main', 'object': {
                       'type': 'commit', 'sha': self.source_sha}}), \
             patch('llmtool.task_submission_cli.task_completion._trusted_slug',
                   return_value='davidcatcat/davidcattoolsproject'), \
             patch('llmtool.task_submission_cli.resolve_profile_config',
                   side_effect=forbidden), \
             patch('llmtool.task_submission_cli.create_client', side_effect=forbidden), \
             redirect_stdout(out):
            code = run_task_complete(Namespace(task_id=task_id,
                executor_task=self.task_id, reviewer_task=None, pr=None), store=store)
        self.assertEqual(code, 0, out.getvalue())

        # 5. The Claude PM reads the durable result with `task show`.
        out = StringIO()
        self.assertEqual(run_task_show(Namespace(task_id=task_id), store=store,
                                       stdout=out), 0)
        shown = json.loads(out.getvalue())
        completion = shown['completion']
        self.assertEqual(completion['state'], 'PM_EVIDENCE_READY')
        self.assertEqual(completion['pm_dispatch'], 'NOT_STARTED')
        self.assertEqual(completion['missing_gates'], [])
        handoff = shown['active_pm_handoff']
        self.assertIs(handoff['provider_invoked'], False)
        self.assertEqual(handoff['instructions'], _EXECUTOR_RESULT_SYSTEM)
        packet = handoff['packet']
        self.assertEqual(packet['task_id'], task_id)
        self.assertEqual(packet['source_sha'], self.source_sha)
        self.assertEqual(packet['result_head'], result_head)
        self.assertEqual(packet['changed_paths'], [self.source_path])
        self.assertEqual([test['exit_code'] for test in packet['tests']], [0])
        self.assertEqual(packet['developer']['provider'], 'codex')
        self.assertEqual(packet['pm_assignment']['route'], 'codex')
        self.assertEqual(packet['review_pr_ci'], {'stage': 'EXECUTOR_RESULT',
            'formal_review_state': 'NOT_STARTED', 'ci_state': 'NOT_STARTED'})
        # The bounded packet never carries code, the diff or test output.
        rendered = json.dumps(packet)
        self.assertNotIn('diff --git', rendered)
        self.assertNotIn('assertEqual(2, 1 + 1)', rendered)


if __name__ == '__main__':
    unittest.main()
