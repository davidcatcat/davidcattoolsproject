"""#1943 WP-5: no-PR High Debug (--issue) and the no_pr refusal at the formal RCA consumers."""
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import patch

import review_trust_fixture
import test_task_store as task_store_fixtures
import worker_policy_fixture
from llmtool import high_debug, repair_policy, review_repair, task_submission_cli
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore, new_id
from test_high_debug import _Store, _stub_debug_rules
from test_lineage_counts import HEAD as GATE_HEAD, OLD_HEAD, PR, TASK as GATE_TASK, _comment

_REAL_POST_GITHUB_RECORD = high_debug._post_github_record

TASK = 'a' * 32
HEAD = 'c' * 40
REPORT = 'Root cause evidence.'
REPORT_SHA = hashlib.sha256(REPORT.encode()).hexdigest()
HOLDER = dict(direct_result_callback=dict(executor_task_id='executor'))


class _NoPRStore(_Store):
    def __init__(self, *, completion=None, claims=None, entries=None, developer=HOLDER):
        super().__init__()
        self.completion, self.claims = completion, list(claims or [])
        self.entries, self.developer = list(entries or []), developer

    def get(self, task_id):
        self.served.add(task_id)
        record = dict(task_id=task_id, run_id='b' * 32, review_repair_claims=self.claims)
        record.update(completion=self.completion, high_debug=list(self.entries))
        if self.developer is not None:
            record['developer'] = self.developer
        return record


def full_report():
    parts = ['## ' + section + '\ntext\n' for section in high_debug.REPORT_SECTIONS[:-1]]
    return '\n'.join(parts + ['## Outcome/cost\nRCA_CONFIRMED\n'])


def rca_entry(**changes):
    entry = dict(family='pr-17', status='RCA_CONFIRMED', examined_sha=HEAD, report=REPORT)
    entry.update(report_sha256=REPORT_SHA, d6='executor-hd1-aaaaaaa')
    entry.update(changes)
    return entry


class HighDebugNoPRTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.trust_config = SimpleNamespace(canonical_root=str(self.tmp / 'canonical'))
        env = dict(GH_CONFIG_DIR='/safe/gh-config', PATH='/usr/bin')
        allowed = SimpleNamespace(allowed=True)
        patches = [patch.object(high_debug, '_post_github_record', return_value=True)]
        root_patch = patch.object(high_debug.task_store, 'default_root', return_value=self.tmp)
        patches.append(root_patch)
        patches.append(patch.object(high_debug.worker_dispatch, 'evaluate', return_value=allowed))
        env_patch = patch.object(high_debug.ai_github_exec, 'verified_ai_env', return_value=env)
        patches.append(env_patch)
        config = self.trust_config
        completion = high_debug.task_completion
        patches.append(patch.object(completion, '_trust_config', return_value=config))
        patches.append(patch.object(high_debug.trust, 'verify_repository', return_value=None))
        issue = patch.object(completion, '_github_get', return_value={'number': 42})
        patches.append(issue)
        started = [item.start() for item in patches]
        for item in patches:
            self.addCleanup(item.stop)
        self.poster, self.github_get = started[0], started[-1]
        _stub_debug_rules(self)

    def repo(self):
        root = self.tmp / 'repo'
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        git = ['git', '-C', str(root), '-c', 'user.name=t', '-c', 'user.email=t@e']
        subprocess.run(git + ['commit', '-q', '--allow-empty', '-m', 'base'], check=True)
        self.trust_config.canonical_root = str(root)
        return root

    def issue_options(self, store, **changes):
        options = dict(store=store, task_id=TASK, executor_task='executor', issue_number=42)
        options.update(family='no-pr', early_reason='pre-pr-failure')
        options.update(changes)
        return options

    def test_issue_debug_without_completion_uses_the_trusted_checkout(self):
        root, store, prompts = self.repo(), _NoPRStore(), []
        common = str(root / '.git')
        registration = SimpleNamespace(root=str(root), common_dir=common, result_sha=None)

        def runner(*, prompt, cwd, output_path):
            prompts.append(prompt)
            Path(output_path).write_text(full_report(), encoding='utf-8')
            out = b'{"model":"gpt-6.1-sol"}\n'
            return subprocess.CompletedProcess([], 0, stdout=out, stderr=b'')

        options = self.issue_options(store, runner=runner, repo_root='/caller/ignored')
        options['create_d6'] = lambda **_kwargs: str(root)
        options['read_registration'] = lambda *_args, **_kwargs: registration
        snapshot = SimpleNamespace(head=HEAD)
        completion = high_debug.task_completion
        collect = patch.object(completion, 'collect_git_snapshot', return_value=snapshot)
        with collect:
            high_debug.run_high_debug(**options)
        (entry,) = store.entries
        self.assertIs(entry.get('no_pr'), True)
        self.assertEqual((entry['family'], entry['examined_sha']), ('no-pr', HEAD))
        self.assertEqual(entry['status'], 'RCA_CONFIRMED')
        self.assertEqual(self.poster.call_args[0][0], 42)
        self.assertIs(self.poster.call_args.kwargs.get('issue_only'), True)
        self.github_get.assert_any_call('issues/42')
        self.assertIn('Issue: 42 (no PR)', prompts[0])

    def refusal(self, store, **changes):
        reader, runner, create = mock.Mock(), mock.Mock(), mock.Mock()
        options = self.issue_options(store, runner=runner, create_d6=create)
        options['read_registration'] = reader
        options.update(changes)
        with self.assertRaises(LLMToolError) as caught:
            high_debug.run_high_debug(**options)
        self.assertEqual((store.entries, store.events), ([], []))
        runner.assert_not_called()
        create.assert_not_called()
        return caught.exception.high_debug_code, reader

    def test_trust_failure_refuses_d6_unavailable_before_any_registry_read(self):
        completion = high_debug.task_completion
        failing = LLMToolError('TRUST_FAILED')
        with patch.object(completion, '_trust_config', side_effect=failing):
            code, reader = self.refusal(_NoPRStore())
        self.assertEqual(code, 'HIGH_DEBUG_D6_UNAVAILABLE')
        reader.assert_not_called()
        self.poster.assert_not_called()

    def test_registry_failure_refuses_d6_unavailable(self):
        self.repo()
        broken = mock.Mock(side_effect=OSError('registry'))
        code, _reader = self.refusal(_NoPRStore(), read_registration=broken)
        self.assertEqual(code, 'HIGH_DEBUG_D6_UNAVAILABLE')

    def test_mismatched_completion_refuses_without_the_canonical_fallback(self):
        other = dict(executor_task_id='other', evidence=dict(common_dir='/tmp/x'))
        for completion in (other, {'executor_task_id': 'executor'}, 'bad'):
            with self.subTest(completion=completion):
                code, reader = self.refusal(_NoPRStore(completion=completion))
                self.assertEqual(code, 'HIGH_DEBUG_D6_UNAVAILABLE')
                reader.assert_not_called()

    def test_invalid_target_combinations_refuse_before_any_entry(self):
        cases = [dict(issue_number=42, failed_corrections=2, early_reason=None)]
        cases.append(dict(issue_number=42, family='pr-1'))
        cases.append(dict(issue_number=None, pr_number=17))
        broken = dict(issue_number=None, pr_number=17, failed_corrections=2, early_reason=None)
        cases.append(broken)
        cases.append(dict(issue_number=None))
        cases.append(dict(pr_number=17))
        cases.append(dict(issue_number=True))
        for changes in cases:
            with self.subTest(changes=changes):
                code, _reader = self.refusal(_NoPRStore(), **changes)
                self.assertEqual(code, 'HIGH_DEBUG_ARGUMENT_INVALID')
                self.poster.assert_not_called()

    def cli_args(self, **changes):
        args = SimpleNamespace(task_id=TASK, executor_task='executor', evidence_file=None)
        args.route, args.pr, args.issue, args.family = 'codex', None, 42, 'no-pr'
        args.failed_corrections, args.early = None, 'pre-pr-failure'
        for key, value in changes.items():
            setattr(args, key, value)
        return args

    def test_cli_invalid_target_combinations_refuse(self):
        cases = [dict(issue=None), dict(pr=17), dict(failed_corrections=2, early=None)]
        cases += [dict(family='pr-1'), dict(pr=17, issue=None)]
        for changes in cases:
            with self.subTest(changes=changes):
                args = self.cli_args(**changes)
                with self.assertRaises(LLMToolError) as caught:
                    task_submission_cli.run_task_high_debug(args, store=_NoPRStore())
                code = caught.exception.high_debug_code
                self.assertEqual(code, 'HIGH_DEBUG_ARGUMENT_INVALID')

    def test_cli_passes_the_issue_target(self):
        target = 'llmtool.high_debug.run_high_debug_with_fallback'
        with patch(target, return_value={'status': 'RCA_CONFIRMED'}) as fallback:
            result = task_submission_cli.run_task_high_debug(self.cli_args(), store=_NoPRStore())
        self.assertEqual(result, {'status': 'RCA_CONFIRMED'})
        kwargs = fallback.call_args.kwargs
        got = (kwargs['issue_number'], kwargs['pr_number'], kwargs['family'])
        self.assertEqual(got, (42, None, 'no-pr'))


if __name__ == '__main__':
    unittest.main()
