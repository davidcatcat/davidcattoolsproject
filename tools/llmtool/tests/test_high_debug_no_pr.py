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
        self.holder = TASK

    def d6_journal_holder(self, executor_task):
        if isinstance(self.holder, Exception):
            raise self.holder
        return self.holder if executor_task == 'executor' else None

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
        self.assertEqual(entry['diagnosis_issue'], 42)
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

    def test_issue_target_that_is_not_a_plain_issue_refuses_before_any_entry(self):
        cases = [{'number': 42, 'pull_request': {}}, {'number': 43}, None, ['x']]
        for value in cases:
            with self.subTest(value=value):
                self.github_get.return_value = value
                code, reader = self.refusal(_NoPRStore())
                self.assertEqual(code, 'HIGH_DEBUG_ISSUE_INVALID')
                reader.assert_not_called()
                self.poster.assert_not_called()
        self.github_get.side_effect = LLMToolError('GITHUB_EVIDENCE_UNAVAILABLE')
        code, _reader = self.refusal(_NoPRStore())
        self.assertEqual(code, 'HIGH_DEBUG_ISSUE_INVALID')

    def test_plain_issue_check_reads_the_issue_endpoint(self):
        self.assertTrue(high_debug._is_plain_issue(42))
        self.github_get.assert_called_once_with('issues/42')
        self.github_get.return_value = {'number': 42, 'pull_request': {'url': 'x'}}
        self.assertFalse(high_debug._is_plain_issue(42))

    def post(self, issue_only, returncode=0):
        result = subprocess.CompletedProcess([], returncode, stdout=b'', stderr=b'')
        completion = high_debug.task_completion
        which = patch.object(high_debug.shutil, 'which', return_value='/usr/bin/gh')
        slug = patch.object(completion, '_trusted_slug', return_value='owner/repo')
        run = patch.object(high_debug.subprocess, 'run', return_value=result)
        with which, slug, run as runner:
            posted = _REAL_POST_GITHUB_RECORD(42, 'body', '/repo', issue_only=issue_only)
        return posted, [call.args[0][1] for call in runner.call_args_list]

    def test_issue_only_post_writes_only_an_issue_comment(self):
        self.assertEqual(self.post(True), (True, ['issue']))
        self.github_get.assert_called_once_with('issues/42')

    def test_issue_only_post_never_writes_on_a_pull_request(self):
        for value in ({'number': 42, 'pull_request': {}}, {'number': 7}):
            with self.subTest(value=value):
                self.github_get.return_value = value
                self.assertEqual(self.post(True), (False, []))
        self.github_get.side_effect = LLMToolError('GITHUB_EVIDENCE_UNAVAILABLE')
        self.assertEqual(self.post(True), (False, []))

    def test_pr_target_post_keeps_the_pr_comment_path(self):
        self.assertEqual(self.post(False), (True, ['pr']))
        self.github_get.assert_not_called()

    def test_no_completion_refuses_a_d6_the_task_does_not_hold(self):
        other = dict(executor_task_id='other')
        cases = [None, {}, other, dict(direct_result_callback=other)]
        cases.append(dict(direct_result_callback='x'))
        for developer in cases:
            with self.subTest(developer=developer):
                code, reader = self.refusal(_NoPRStore(developer=developer))
                self.assertEqual(code, 'HIGH_DEBUG_D6_NOT_BOUND')
                reader.assert_not_called()
                self.poster.assert_not_called()

    def test_task_holds_d6_reads_the_developer_and_its_callback(self):
        holders = [dict(developer=HOLDER), dict(developer=dict(executor_task_id='executor'))]
        for task in holders:
            self.assertTrue(high_debug._task_holds_d6(task, 'executor'))
        other = dict(executor_task_id='other')
        others = [{}, dict(developer='x'), dict(developer=other)]
        others.append(dict(developer=dict(direct_result_callback=other)))
        for task in others:
            self.assertFalse(high_debug._task_holds_d6(task, 'executor'))

    def test_no_completion_refuses_when_another_task_took_over_the_d6(self, holders=('b' * 32, None)):
        for holder in holders:
            with self.subTest(holder=holder):
                store = _NoPRStore()
                store.holder = holder
                code, reader = self.refusal(store)
                self.assertEqual(code, 'HIGH_DEBUG_D6_NOT_BOUND')
                reader.assert_not_called()
                self.poster.assert_not_called()

    def test_no_completion_refuses_when_the_journal_is_unreadable(self):
        unreadable = LLMToolError('TASK_D6_BINDING_SCAN_FAILED')
        self.test_no_completion_refuses_when_another_task_took_over_the_d6(holders=(unreadable,))

    def test_failed_task_without_completion_that_still_holds_the_d6_runs(self):
        with patch.dict(HOLDER, state='DEVELOPER_OUTPUT_APPLY_FAILED'):
            self.test_issue_debug_without_completion_uses_the_trusted_checkout()


class FormalRcaConsumerNoPRTests(unittest.TestCase):
    def setUp(self):
        review_trust_fixture.install_for_case(self)
        worker_policy_fixture.install(self)
        self.policy = repair_policy.load_policy()

    def test_review_gate_never_takes_a_no_pr_rca(self):
        body = _comment()['body']
        packet = dict(reviewer_text_excerpt_untrusted_data=body, failed_head=GATE_HEAD, pr=PR, executor_task='executor', version=1)
        family = 'pr-%d' % PR
        stamp = '2026-10-27T03:00:00Z'
        entry = rca_entry(family=family, examined_sha=GATE_HEAD, recorded_at=stamp)
        counts = {'review_repairs': 3, 'review_heads': {OLD_HEAD: 'FAIL'}}
        review = {'executor_task': 'executor', 'pr': PR, 'version': 1, 'review_kind': 'formal',
                  'head_sha': GATE_HEAD, 'state': 'COMPLETED', 'updated_at': stamp,
                  'review_result': {'task_id': GATE_TASK, 'head': GATE_HEAD, 'verdict': 'FAIL',
                                    'review_text': body}}
        task = {'task_id': GATE_TASK, 'high_debug': [entry], 'review_dispatch_claim': [review]}
        found = review_repair._review_gate(task, [], packet, family, self.policy, counts=counts)
        self.assertEqual(found['status'], 'RCA_CONFIRMED')
        task['high_debug'] = [dict(entry, no_pr=True)]
        with self.assertRaises(LLMToolError) as caught:
            review_repair._review_gate(task, [], packet, family, self.policy, counts=counts)
        self.assertEqual(caught.exception.review_repair_code, 'REPAIR_DEBUG_REQUIRED')

    def test_bound_run_check_refuses_a_no_pr_entry_first(self):
        # With no refusal, the None Task below would raise AttributeError instead.
        check = task_submission_cli._require_bound_high_debug_run
        with self.assertRaises(ValueError):
            check(None, TASK, rca_entry(no_pr=True), 'executor')

    def test_repair_packet_rca_never_uses_a_no_pr_entry(self):
        packet = dict(task=TASK, executor_task='executor', failed_head=HEAD)
        packet['high_debug_report_sha256'] = REPORT_SHA
        completion = {'executor_task_id': 'executor'}
        only_no_pr = _NoPRStore(completion=completion, entries=[rca_entry(no_pr=True)])
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli._check_repair_packet_rca(packet, only_no_pr, 'executor', TASK)
        self.assertEqual(str(caught.exception), 'DELEGATE_HIGH_DEBUG_TASK_INVALID')

    def test_repair_rca_accepts_current_head_with_verified_issue_and_lineage(self):
        from test_task_delegate_repair_cap import (
            _Store, _args, _entry, _diagnosis, _HIGH_DEBUG_TASK,
            _CURRENT_TASK, _HEAD,
        )
        entry = dict(_entry(family='no-pr'), no_pr=True, diagnosis_issue=1943)
        source = _diagnosis([entry])
        store = _Store(count=4, diagnosis=source)
        store.records[_CURRENT_TASK] = {'task_id': _CURRENT_TASK, 'continues': _HIGH_DEBUG_TASK}
        slug = review_trust_fixture.OWNER_SLUG
        args = _args(high_debug_family='no-pr',
                     high_debug_ref=f'https://github.com/{slug}/issues/1943#issuecomment-1')
        binding = (None, SimpleNamespace(head=_HEAD))
        def check(current_binding=binding):
            return task_submission_cli._check_repair_rca(
                args, store, current_binding, '2026-11-04T00:00:00Z')

        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_HIGH_DEBUG_ISSUE_UNVERIFIED$'):
            check()
        source['completion_events'].append({'source': 'GITHUB', 'issue_number': 1943})
        self.assertIsNone(check())
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_HIGH_DEBUG_CAUSE_UNVERIFIED$'):
            check((None, SimpleNamespace(head='b' * 40)))
        del store.records[_CURRENT_TASK]['continues']
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_HIGH_DEBUG_LINEAGE_MISMATCH$'):
            check()
        store.records[_CURRENT_TASK]['continues'] = _HIGH_DEBUG_TASK
        args.high_debug_ref = f'https://github.com/{slug}/issues/2443#issuecomment-1'
        with self.assertRaisesRegex(LLMToolError, '^DELEGATE_HIGH_DEBUG_TASK_INVALID$'):
            check()

    def test_repair_rca_refuses_when_only_a_no_pr_entry_exists(self):
        slug = review_trust_fixture.OWNER_SLUG
        args = SimpleNamespace(executor_task='executor', high_debug_task=TASK, repair_packet=None)
        args.high_debug_ref = 'https://github.com/%s/issues/1943#issuecomment-1' % slug
        args.high_debug_family = 'no-pr'
        completion = {'executor_task_id': 'executor'}
        store = _NoPRStore(completion=completion, entries=[rca_entry(no_pr=True)])
        with self.assertRaises(LLMToolError) as caught:
            task_submission_cli._check_repair_rca(args, store, None, None)
        self.assertTrue(str(caught.exception).startswith('DELEGATE_HIGH_DEBUG_TASK_INVALID'))


class TaskStoreNoPRTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = TaskStore(Path(temporary.name).resolve() / 'tasks')
        review_trust_fixture.install_for_case(self)
        self.task = task_store_fixtures.TaskStoreTests._codex_dispatch_task(self)
        self.store.create(self.task)

    def entry(self, **changes):
        options = dict(family='pr-17', failed_corrections=None, early_reason='review FAIL 2')
        options.update(head='a' * 40, d6='executor-1-hd1-aaaaaaa', code='HIGH_DEBUG_RUN_FAILED')
        options.update(observed='UNKNOWN', effort='UNKNOWN', usage='UNKNOWN')
        options.update(recorded_at='2026-10-27T00:00:30Z', no_pr=True)
        options.update(changes)
        return high_debug._failed_entry(**options)

    def test_no_pr_must_be_exactly_true(self):
        saved = self.store.append_high_debug(self.task['task_id'], self.entry())
        self.assertIs(saved['high_debug'][0].get('no_pr'), True)
        for value in (False, 'true', 1):
            with self.subTest(value=value):
                bad = dict(self.entry(), no_pr=value)
                with self.assertRaises(LLMToolError) as caught:
                    self.store.append_high_debug(self.task['task_id'], bad)
                self.assertEqual(caught.exception.task_store_error_code, 'HIGH_DEBUG_INVALID')

    def test_second_repair_claim_never_binds_a_no_pr_rca(self):
        task_id = self.task['task_id']
        entry = dict(self.entry(), status='RCA_CONFIRMED')
        self.store.append_high_debug(task_id, entry)
        first = dict(executor_task='executor-1', pr=17, head_sha='b' * 40, version=1, attempt=1)
        first.update(family='pr-17', state='CLAIMED', repair_task_id=new_id(), comment_id=9)
        first.update(packet_sha256='b' * 64, claimed_at='2026-10-27T00:00:00Z')
        for claims in ([first], [dict(first, state='DISPATCHED')]):
            saved = self.store.get(task_id)
            expected = {'record_sha256': saved['record_sha256']}
            self.store.compare_and_update(task_id, expected=expected, review_repair_claims=claims)
        first = dict(first, state='DISPATCHED')
        second = dict(first, head_sha='a' * 40, version=2, attempt=2, state='CLAIMED')
        second.update(repair_task_id=new_id(), claimed_at='2026-10-27T00:01:00Z')
        second['high_debug_report_sha256'] = entry['report_sha256']
        saved = self.store.get(task_id)
        expected = {'record_sha256': saved['record_sha256']}
        with self.assertRaises(LLMToolError) as caught:
            claims = [first, second]
            self.store.compare_and_update(task_id, expected=expected, review_repair_claims=claims)
        self.assertEqual(caught.exception.task_store_error_code, 'REVIEW_REPAIR_CLAIM_INVALID')

    def journal(self, task_id):
        directory = self.store._dirfd()
        try:
            journal = self.store._read_d6_journal_at(directory)
            self.store._append_d6_journal_at(directory, journal, task_id, frozenset({'d6-x'}))
        finally:
            high_debug.os.close(directory)

    def test_journal_holder_is_the_newest_entry_for_the_d6(self):
        self.assertIsNone(self.store.d6_journal_holder('d6-x'))
        for task_id in ('a' * 32, 'b' * 32):
            self.journal(task_id)
            self.assertEqual(self.store.d6_journal_holder('d6-x'), task_id)
        self.assertIsNone(self.store.d6_journal_holder('d6-y'))

    def test_unreadable_journal_fails_closed(self):
        self.journal('a' * 32)
        (self.store.root / high_debug.task_store.D6_BINDING_JOURNAL_NAME).write_bytes(b'{x')
        with self.assertRaises(LLMToolError) as caught:
            self.store.d6_journal_holder('d6-x')
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_D6_BINDING_SCAN_FAILED')


if __name__ == '__main__':
    unittest.main()
