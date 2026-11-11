import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from contextlib import redirect_stdout
from unittest.mock import call, patch
from pathlib import Path

from llmtool import review_dispatch
from llmtool import review_result
from llmtool import task_advance
from llmtool import task_drive
from llmtool import task_submission_cli
from llmtool.errors import LLMToolError
from llmtool.task_store import _json_bytes
from llmtool.cli import main, parser_for_cli


HEAD = 'a' * 40
MAIN_SHA = 'e' * 40
PR = {'number': 17, 'head': {'sha': HEAD}, 'merged': False}


def base_record(**changes):
    record = {
        'task_id': '1' * 32, 'run_id': '2' * 32,
        'developer': {'state': 'DEVELOPER_RESPONDED'},
        'completion': {'executor_task_id': 'executor-1', 'state': 'WAITING_REVIEW',
                      'evidence': {'result': {'result_head': HEAD}}},
        'review_dispatch_claim': [], 'review_repair_claims': [],
        'completion_events': [],
        'remote_test_outcomes': [{'result': 'ADMISSION_DENIED',
            'code': 'MODULE_NOT_ADMITTED', 'executor_task': 'executor-1',
            'sha': HEAD, 'modules': []}],
    }
    record.update(changes)
    return record


def d6(clean=True, recorded=HEAD):
    return {'root': '/repo/task', 'task_id': 'executor-1', 'result_head': HEAD,
            'recorded_result_head': recorded, 'registered_result_head': recorded,
            'dirty': not clean}


def remote_receipt(record, *, source_sha=HEAD, result='PASS', excerpt=None,
                   executor_task='executor-1', **changes):
    receipt = {
        'task_id': record['task_id'], 'run_id': record['run_id'],
        'executor_task': executor_task, 'source_sha': source_sha,
        'result': result, 'modules': ['test_task_advance'],
        'ran_total': 1, 'elapsed_s': 0.01,
    }
    if excerpt is not None:
        receipt['failure_excerpt'] = excerpt
    receipt.update(changes)
    receipt['receipt_digest'] = hashlib.sha256(_json_bytes(receipt)).hexdigest()
    return receipt


def green_linux_ci(path, pr=PR):
    if path.startswith('commits/') and path.endswith('/check-runs?per_page=100'):
        head = path[len('commits/'):].split('/', 1)[0]
        return {'total_count': 1, 'check_runs': [{'id': 1, 'name': 'check (Linux 3.14)',
            'head_sha': head, 'status': 'completed', 'conclusion': 'success'}]}
    if path.startswith('pulls/') and path.endswith('/files?per_page=100'):
        return [{'filename': 'src/module.py'}]
    if path.startswith('pulls/'):
        return pr
    return []


def with_current_main(github_get, status='ahead'):
    """Answer the merge step's current-main reads, then defer to github_get."""
    def read(path):
        if path == 'branches/main':
            return {'commit': {'sha': MAIN_SHA}}
        if path == f'compare/{MAIN_SHA}...{HEAD}':
            return {'status': status}
        return github_get(path)
    return read


class TaskAdvanceMergeDocsRuleTests(unittest.TestCase):
    """_merge_checks must use the shared docs-only rule of CI and task complete."""

    def _checks(self, filenames, names):
        pr = {'number': 17, 'head': {'sha': HEAD}, 'state': 'open', 'mergeable': True}

        def github_get(path):
            if path.startswith('commits/') and path.endswith('/check-runs?per_page=100'):
                runs = [{'id': index + 1, 'name': name, 'head_sha': HEAD,
                         'status': 'completed', 'conclusion': 'success'}
                        for index, name in enumerate(names)]
                return {'total_count': len(runs), 'check_runs': runs}
            if path.startswith('pulls/') and path.endswith('/files?per_page=100'):
                return [{'filename': name} for name in filenames]
            return pr

        return task_advance._merge_checks({}, 17, HEAD, pr, github_get)

    def test_skills_json_and_docs_markdown_need_only_docs_check(self):
        self.assertIsNone(self._checks(['skills/x.json', 'docs/y.md'], ['check (docs)']))

    def test_deep_llmtool_markdown_needs_code_checks(self):
        self.assertEqual(
            self._checks(['tools/llmtool/llmtool/deep/z.md'], ['check (docs)']),
            'MERGE_REQUIRED_CHECK_MISSING')
        self.assertEqual(
            self._checks(['tools/llmtool/llmtool/deep/z.md'],
                         ['check (docs)', 'check (Linux 3.14)']),
            'MERGE_REQUIRED_CHECK_MISSING')
        self.assertIsNone(
            self._checks(['tools/llmtool/llmtool/deep/z.md'],
                         ['check (Linux 3.14)', 'check (macOS 3.13)']))

    def test_top_level_readme_stays_docs_only(self):
        self.assertIsNone(self._checks(['README.md'], ['check (docs)']))


class TaskAdvanceTests(unittest.TestCase):
    def _completed_codex_review(self, verdict, *, recorded=True, finding=True):
        task_id, run_id, contract_digest = '1' * 32, '2' * 32, 'd' * 64
        body = (f'<!-- activity: type=review_verdict actor=Codex target=PM status={verdict} -->\n'
                f'Verdict: {verdict} | Version: v1 | HEAD: {HEAD}\n')
        if verdict == 'PASS':
            body += 'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n'
        body += (f'LLMTOOL-REVIEW-BINDING v1 task={task_id} run={run_id} '
                 f'contract={contract_digest} head={HEAD}\n')
        if verdict != 'PASS' and finding:
            body += 'Finding: the changed branch loses the retry count.\n'
        claim = {
            'executor_task': 'executor-1', 'pr': 17, 'head_sha': HEAD,
            'version': 1, 'state': 'COMPLETED', 'updated_at': '2026-10-28T00:00:00Z',
            'review_result': {
                'task_id': task_id, 'run_id': run_id,
                'contract_digest': contract_digest, 'head': HEAD,
                'verdict': verdict, 'review_text': body,
            },
        }
        record = base_record(
            schema_version=2, pm_session_id='pm-session',
            task_binding={'contract_digest': contract_digest},
            developer={'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'claude'},
            review_dispatch_claim=[claim])
        if recorded:
            outcome = 'REVIEW_PASS' if verdict == 'PASS' else 'REVIEW_FAIL'
            identity = {'task': task_id, 'run': run_id, 'head': HEAD,
                        'version': 1, 'outcome': outcome, 'comment_id': 0}
            event_id = hashlib.sha256(json.dumps(
                identity, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()[:32]
            record['completion_events'] = [{
                'event_id': event_id, 'source': 'REVIEWER', 'source_sha': HEAD,
                'version': 1, 'outcome': outcome,
            }]
        return record, claim

    def test_cli_route(self):
        parsed = parser_for_cli().parse_args(['task', 'advance', '1' * 32, '--pr', '17'])
        self.assertEqual(parsed.action, 'advance')
        self.assertFalse(parsed.merge)
        with patch('llmtool.task_submission_cli.run_task_advance', return_value=0) as handler:
            self.assertEqual(main(['task', 'advance', '1' * 32, '--pr', '17']), 0)
        handler.assert_called_once()
        with patch('llmtool.task_submission_cli.run_task_advance', return_value=0) as handler:
            self.assertEqual(main(['task', 'advance', '1' * 32, '--pr', '17', '--merge']), 0)
        self.assertTrue(handler.call_args.args[0].merge)
        # #1124: the adapter runs only for the Task's own PM session.
        native = '3f2b8c1e-4d5a-4b6c-8d7e-9f0a1b2c3d4e'
        owner_store = type('Store', (), {
            'get': lambda self, _task_id: {'pm_session_id': 'pm-' + native}})()
        for argv, expected in ((['task', 'advance', '1' * 32], False),
                               (['task', 'advance', '1' * 32, '--merge'], True)):
            args = parser_for_cli().parse_args(argv + ['--pm-session', native])
            with patch('llmtool.task_advance.advance', return_value={'codes': []}) as advance, \
                    patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': '',
                                            'CODEX_THREAD_ID': ''}), \
                    redirect_stdout(io.StringIO()):
                task_submission_cli.run_task_advance(args, store=owner_store)
            self.assertEqual(advance.call_args.kwargs['merge'], expected)

    def test_claude_result_route_and_advance(self):
        parsed = parser_for_cli().parse_args([
            'task', 'claude-result', '1' * 32, '--executor-task', 'executor-1',
            '--head', HEAD, '--pm-session', 'pm-session'])
        self.assertEqual(parsed.action, 'claude-result')
        with patch('llmtool.task_submission_cli.run_task_claude_result', return_value=0) as handler:
            self.assertEqual(main([
                'task', 'claude-result', '1' * 32, '--executor-task', 'executor-1',
                '--head', HEAD, '--pm-session', 'pm-session']), 0)
        handler.assert_called_once()
        with self.assertRaises(SystemExit):
            parser_for_cli().parse_args([
                'task', 'claude-result', '1' * 32, '--executor-task', 'executor-1',
                '--head', HEAD])
        record = base_record(developer={
            'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'claude',
            'claude_result': {'executor_task_id': 'executor-1', 'head_sha': HEAD,
                              'recorded_at': '2026-10-29T00:00:00Z'},
        })
        self.assertEqual(task_advance.decide(record, d6(), PR, {}), 'DISPATCH_REVIEW')

    def test_developer_attention_and_publish_gates(self):
        blocked = base_record(developer={'state': 'DEVELOPER_OUTCOME_UNKNOWN'})
        self.assertEqual(task_advance.decide(blocked, d6(), PR, {}), 'DEVELOPER_ATTENTION')
        self.assertEqual(task_advance.decide(base_record(), d6(False), PR, {}), 'PUBLISH')
        self.assertEqual(task_advance.decide(base_record(), d6(recorded=None), PR, {}), 'PUBLISH')
        self.assertEqual(task_advance.decide(base_record(), d6(), None, {}), 'PUBLISH')
        self.assertEqual(task_advance.decide(base_record(), d6(),
            {'number': 17, 'head': {'sha': 'b' * 40}}, {}), 'PUBLISH')

    def test_advance_executes_publish_and_returns_only_bounded_fields(self):
        record = base_record(developer={'state': 'DEVELOPER_RESPONDED',
                                        'write_paths': ['allowed.txt']})
        store = type('S', (), {'get': lambda self, _id: record})()
        binding = {'root': '/d6', 'common_dir': '/canonical/.git',
                   'branch': 'feat/task', 'task_id': '3' * 32, 'result_head': HEAD,
                   'recorded_result_head': None, 'dirty': True, 'state': 'DIRTY'}
        published = {'pr': 17, 'head': HEAD}
        with patch.object(task_advance, '_d6_snapshot', return_value=binding), \
             patch.object(task_advance, '_review_outcomes', return_value=[]), \
             patch.object(task_advance, '_publish_task', return_value=published) as publish:
            result = task_advance.advance(task_id=record['task_id'], store=store,
                publish_git_runner=lambda *_args: None,
                publish_gh_runner=lambda *_args: None,
                record_result_call=lambda **_kwargs: None)
        publish.assert_called_once()
        self.assertEqual(result, {'task_id': record['task_id'], 'codes': [],
                                  'pr': 17, 'head': HEAD})

    def _advance_no_claim(self, pr):
        record = base_record()
        store = type('S', (), {'get': lambda self, _id: record})()
        dispatched = []
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_review_outcomes', return_value=[]):
            result = task_advance.advance(task_id=record['task_id'], store=store,
                pr_number=17, github_get=lambda path: green_linux_ci(path, pr),
                dispatch_review_call=lambda **kwargs: dispatched.append(kwargs) or {})
        return result, dispatched

    def test_advance_merged_pr_without_claim_never_dispatches_review(self):
        merged = dict(PR, merged=True, merge_commit_sha='c' * 40)
        self.assertEqual(task_advance.decide(base_record(), d6(), merged, {}),
                         'PR_MERGED_NO_REVIEW')
        result, dispatched = self._advance_no_claim(merged)
        self.assertEqual(dispatched, [])
        self.assertEqual(result['step'], 'PR_MERGED_NO_REVIEW')
        self.assertNotEqual(result['step'], 'DISPATCH_REVIEW')
        self.assertIn('PR_MERGED_NO_REVIEW', result['codes'])
        self.assertIsNone(result['result'])

    def test_advance_open_pr_without_claim_still_dispatches_review(self):
        result, dispatched = self._advance_no_claim(dict(PR))
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0]['pr_number'], 17)
        self.assertEqual(result['step'], 'DISPATCH_REVIEW')

    def test_dispatch_unknown_result_and_review_outcomes(self):
        self.assertEqual(task_advance.decide(base_record(), d6(), PR, {}), 'DISPATCH_REVIEW')
        claim = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'CLAIMED'}
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]), d6(), PR, {}),
                         'REVIEW_OUTCOME_UNKNOWN')
        claim['state'] = 'DISPATCHED'
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]), d6(), PR, {}),
                         'RECORD_REVIEW_RESULT')
        claim['state'] = 'COMPLETED'
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]), d6(), PR, {}),
                         'RECORD_REVIEW_RESULT')
        related = {'events': [{'source': 'REVIEWER', 'source_sha': HEAD,
                               'version': 1, 'outcome': 'REVIEW_BLOCKED'}]}
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]), d6(), PR, related),
                         'REVIEWER_ATTENTION')
        related['events'][0]['outcome'] = 'REVIEW_FAIL'
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]), d6(), PR, related),
                         'REPAIR_REVIEW')
        related['repair_claims'] = [{'head_sha': HEAD, 'state': 'DISPATCHED',
                                     'repair_state': 'RUNNING'}]
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]), d6(), PR, related),
                         'WAITING_REPAIR')
        related['repair_claims'][0]['repair_state'] = 'UNAVAILABLE'
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]), d6(), PR, related),
                         'REPAIR_RETRY')
        related['repair_claims'][0]['repair_state'] = 'BLOCKED'
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]), d6(), PR, related),
                         'REPAIR_RETRY')
        related['repair_claims'][0]['repair_state'] = 'OUTPUT_REJECTED'
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]), d6(), PR, related),
                         'REPAIR_RETRY')
        related['repair_claims'][0]['repair_state'] = 'UNKNOWN'
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]), d6(), PR, related),
                         'REPAIR_OUTCOME_UNKNOWN')
        related['repair_claims'][0]['repair_state'] = 'RECORD_UNAVAILABLE'
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]), d6(), PR, related),
                         'DEVELOPER_ATTENTION')
        related['repair_claims'][0]['repair_state'] = 'RESPONDED'
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]), d6(), PR, related),
                         'PUBLISH')
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]), d6(False), PR, related),
                         'PUBLISH')
        new_head = 'b' * 40
        self.assertEqual(task_advance.decide(base_record(review_dispatch_claim=[claim]),
            {'root': '/repo/task', 'result_head': new_head, 'recorded_result_head': new_head,
             'dirty': False}, {'number': 17, 'head': {'sha': new_head}},
            {'events': [{'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                         'outcome': 'REVIEW_FAIL'}], 'repair_claims': []}), 'RUN_REMOTE_TEST')
        repaired_record = base_record(
            review_dispatch_claim=[claim],
            remote_test_receipts=[remote_receipt(
                base_record(), source_sha=new_head)])
        self.assertEqual(task_advance.decide(repaired_record,
            {'root': '/repo/task', 'result_head': new_head, 'recorded_result_head': new_head,
             'dirty': False}, {'number': 17, 'head': {'sha': new_head}},
            {'events': [{'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                         'outcome': 'REVIEW_FAIL'}], 'repair_claims': []}), 'DISPATCH_REVIEW')

    def test_advance_requires_exact_head_remote_test_then_dispatches_idempotently(self):
        class Store:
            def __init__(self):
                self.record = base_record()
            def get(self, _task_id):
                return self.record

        store = Store()
        store.record['remote_test_outcomes'] = []
        calls = []
        def remote_test(args, *, store):
            calls.append((args.sha, args.executor_task, args.ref))
            store.record.setdefault('remote_test_receipts', []).append(
                remote_receipt(store.record))
            return 0
        dispatched = []
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_submission_cli, 'run_task_remote_test', side_effect=remote_test):
            first = task_advance.advance(task_id=store.record['task_id'], store=store,
                pr_number=17, dispatch_review_call=lambda **kwargs: dispatched.append(kwargs),
                publish_gh_runner=lambda *_args: type('R', (), {'returncode': 0})())
            second = task_advance.advance(task_id=store.record['task_id'], store=store,
                pr_number=17, github_get=green_linux_ci,
                dispatch_review_call=lambda **kwargs: dispatched.append(kwargs))
        self.assertEqual(first['step'], 'RUN_REMOTE_TEST')
        self.assertEqual(first['result'], {'task_id': store.record['task_id'],
            'executor_task': 'executor-1', 'head': HEAD,
            'remote_test': {'result': 'PASS', 'modules': ['test_task_advance'],
                            'ran_total': 1, 'elapsed_s': 0.01,
                            'receipt_digest': store.record['remote_test_receipts'][0]
                                ['receipt_digest'][:8],
                            'hosted_run_id': None}})
        self.assertEqual(second['step'], 'DISPATCH_REVIEW')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls, [(HEAD, 'executor-1', 'refs/pull/17/head')])
        self.assertEqual(len(dispatched), 1)

    def test_review_repair_advances_from_registered_d6_without_changing_cwd(self):
        new_head = 'b' * 40
        record = base_record(
            review_dispatch_claim=[{'pr': 17, 'head_sha': HEAD, 'version': 1,
                                    'state': 'DISPATCHED'}],
            completion_events=[{'source': 'REVIEWER', 'source_sha': HEAD,
                                'version': 1, 'outcome': 'REVIEW_FAIL'}])
        class Store:
            def __init__(self):
                self.record = record
            def get(self, _task_id):
                return self.record

        store = Store()
        snapshot = {'root': '/registered/d6', 'result_head': HEAD,
                    'recorded_result_head': HEAD, 'registered_result_head': HEAD,
                    'dirty': False}
        published, dispatched = [], []
        def repair(**_kwargs):
            snapshot['dirty'] = True
            return {'repair_task_id': 'repair-task'}
        def publish(*_args, **_kwargs):
            published.append(snapshot['root'])
            snapshot.update(result_head=new_head, recorded_result_head=new_head,
                            registered_result_head=new_head, dirty=False)
            return {'pr': 17, 'head': new_head}
        def remote_test(_args, *, store):
            store.record.setdefault('remote_test_receipts', []).append(
                remote_receipt(store.record, source_sha=new_head))
            return 0
        def dispatch(**kwargs):
            dispatched.append((kwargs, snapshot['result_head']))
            return {'state': 'DISPATCHED'}

        initial_cwd = os.getcwd()
        with patch.object(task_advance, '_d6_snapshot', side_effect=lambda *_args: dict(snapshot)), \
             patch.object(task_advance, '_pr_snapshot', side_effect=lambda *_args: {
                 'number': 17, 'head': {'sha': snapshot['result_head']}, 'merged': False}), \
             patch.object(task_advance, '_review_outcomes', return_value=[]), \
             patch.object(task_advance, '_publish_task', side_effect=publish), \
             patch.object(task_submission_cli, 'run_task_remote_test', side_effect=remote_test):
            repaired = task_advance.advance(task_id=record['task_id'], store=store,
                pr_number=17, repair_review_call=repair, github_get=lambda _path: [])
            published_result = task_advance.advance(task_id=record['task_id'], store=store,
                pr_number=17, github_get=lambda _path: [])
            tested = task_advance.advance(task_id=record['task_id'], store=store,
                pr_number=17, github_get=lambda _path: [],
                publish_gh_runner=lambda *_args: type('R', (), {'returncode': 0})())
            reviewed = task_advance.advance(task_id=record['task_id'], store=store,
                pr_number=17, github_get=green_linux_ci, dispatch_review_call=dispatch)

        self.assertEqual(repaired['step'], 'REPAIR_REVIEW')
        self.assertEqual(published_result, {'task_id': record['task_id'], 'codes': [],
                                             'pr': 17, 'head': new_head})
        self.assertEqual([tested['step'], reviewed['step']],
                         ['RUN_REMOTE_TEST', 'DISPATCH_REVIEW'])
        self.assertEqual(published, ['/registered/d6'])
        self.assertEqual(dispatched[0][0]['version'], 2)
        self.assertEqual(dispatched[0][1], new_head)
        self.assertEqual(os.getcwd(), initial_cwd)

    def test_old_head_remote_test_receipt_does_not_satisfy_gate(self):
        record = base_record(remote_test_receipts=[remote_receipt(
            base_record(), source_sha='b' * 40)])
        record['remote_test_outcomes'] = []
        self.assertEqual(task_advance.decide(record, d6(), PR, {}), 'RUN_REMOTE_TEST')

    def test_remote_test_gate_does_not_override_existing_review_states(self):
        record = base_record(remote_test_outcomes=[])
        claim = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'CLAIMED'}
        record['review_dispatch_claim'] = [claim]
        self.assertEqual(task_advance.decide(record, d6(), PR, {}),
                         'REVIEW_OUTCOME_UNKNOWN')

        claim['state'] = 'DISPATCHED'
        self.assertEqual(task_advance.decide(record, d6(), PR, {}),
                         'RECORD_REVIEW_RESULT')

        related = {'events': [{'source': 'REVIEWER', 'source_sha': HEAD,
                               'version': 1, 'outcome': 'REVIEW_FAIL'}]}
        self.assertEqual(task_advance.decide(record, d6(), PR, related), 'REPAIR_REVIEW')

        related['events'][0]['outcome'] = 'REVIEW_PASS'
        record['completion'] = {'executor_task_id': 'executor-1',
            'state': 'PM_EVIDENCE_READY', 'evidence': {
                'result': {'result_head': HEAD}, 'review_pr_ci': {'stage': 'READY'}}}
        self.assertEqual(task_advance.decide(record, d6(), PR, related),
                         'READY_FOR_INTEGRATION')

    def test_remote_test_fail_uses_repair_path_with_excerpt(self):
        record = base_record(remote_test_receipts=[remote_receipt(
            base_record(), result='FAIL', excerpt='FAIL: test_example\nAssertionError: café')])
        store = type('S', (), {'get': lambda self, _id: record})()
        seen = {}
        def repair(**kwargs):
            seen.update(kwargs)
            return {'state': 'DISPATCHED'}
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR):
            result = task_advance.advance(task_id=record['task_id'], store=store,
                pr_number=17, cwd='/repo/task', repair_review_call=repair)
        self.assertEqual(result['step'], 'REPAIR_REVIEW')
        self.assertEqual(seen['remote_test_receipt']['failure_excerpt'],
                         'FAIL: test_example\nAssertionError: café')

    def test_tampered_remote_test_receipt_is_skipped(self):
        receipt = remote_receipt(base_record(), result='FAIL', excerpt='failure')
        receipt['failure_excerpt'] = 'tampered'
        record = base_record(remote_test_receipts=[receipt])
        self.assertEqual(task_advance._remote_test_status(record, d6()),
                         ('ADMISSION_DENIED', None, None))

    def test_timeout_and_sha_mismatch_receipts_require_attention(self):
        single_timeout = base_record(remote_test_outcomes=[{
            'result': 'TIMEOUT', 'executor_task': 'executor-1',
            'sha': HEAD, 'modules': ['test_task_advance'],
        }])
        self.assertEqual(task_advance.decide(single_timeout, d6(), PR, {}),
                         'RUN_REMOTE_TEST')
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_run_remote_test', return_value={}):
            single_result = task_advance.advance(
                task_id=single_timeout['task_id'],
                store=type('S', (), {'get': lambda self, _id: single_timeout})(),
                pr_number=17)
        self.assertEqual(single_result['step'], 'RUN_REMOTE_TEST')
        for outcome, code in (('TIMEOUT', 'REMOTE_TEST_TIMEOUT'),
                              ('SHA_MISMATCH', 'REMOTE_TEST_SHA_MISMATCH')):
            record = base_record(remote_test_outcomes=[])
            if outcome == 'TIMEOUT':
                record['remote_test_outcomes'] = [
                    {'result': 'TIMEOUT', 'executor_task': 'executor-1',
                     'sha': HEAD, 'modules': ['test_task_advance']},
                    {'result': 'TIMEOUT', 'executor_task': 'executor-1',
                     'sha': HEAD, 'modules': ['test_task_advance']},
                ]
            else:
                record['remote_test_receipts'] = [remote_receipt(
                    base_record(), result=outcome)]
            self.assertEqual(task_advance.decide(record, d6(), PR, {}),
                             'NEEDS_ATTENTION')
            store = type('S', (), {'get': lambda self, _id: record})()
            with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
                 patch.object(task_advance, '_pr_snapshot', return_value=PR):
                result = task_advance.advance(task_id=record['task_id'], store=store,
                    pr_number=17)
            self.assertEqual(result['step'], 'NEEDS_ATTENTION')
            self.assertEqual(result['codes'], [code])

    def test_no_admitted_modules_admission_denied_allows_review(self):
        record = base_record(remote_test_outcomes=[{
            'result': 'ADMISSION_DENIED', 'code': 'MODULE_NOT_ADMITTED',
            'executor_task': 'executor-1', 'sha': HEAD, 'modules': [],
        }])
        self.assertEqual(task_advance.decide(record, d6(), PR, {}), 'DISPATCH_REVIEW')

    def test_completed_codex_pass_with_recorded_event_reaches_integration(self):
        record, claim = self._completed_codex_review('PASS')
        outcomes = task_advance._review_outcomes(
            record, [claim], lambda _path: self.fail('completed review must not fetch GitHub'))
        self.assertEqual(outcomes, [{'source': 'REVIEWER', 'source_sha': HEAD,
                                     'version': 1, 'outcome': 'REVIEW_PASS',
                                     'findings_count': 0}])
        record['completion'] = {'state': 'PM_EVIDENCE_READY', 'evidence': {
            'result': {'result_head': HEAD}, 'review_pr_ci': {'stage': 'READY'}}}
        self.assertEqual(task_advance.decide(record, d6(), PR, {'events': outcomes}),
                         'READY_FOR_INTEGRATION')

    def test_completed_codex_fail_with_recorded_event_requests_repair(self):
        record, claim = self._completed_codex_review('FAIL')
        outcomes = task_advance._review_outcomes(
            record, [claim], lambda _path: self.fail('completed review must not fetch GitHub'))
        self.assertEqual(outcomes, [{'source': 'REVIEWER', 'source_sha': HEAD,
                                     'version': 1, 'outcome': 'REVIEW_FAIL',
                                     'findings_count': 1}])
        self.assertEqual(task_advance.decide(record, d6(), PR, {'events': outcomes}),
                         'REPAIR_REVIEW')

    def test_completed_fail_without_finding_stops_with_no_repair_claim(self):
        record, claim = self._completed_codex_review('FAIL', finding=False)
        outcomes = task_advance._review_outcomes(
            record, [claim], lambda _path: self.fail('completed review must not fetch GitHub'))
        self.assertEqual(outcomes[0]['findings_count'], 0)
        self.assertEqual(task_advance.decide(record, d6(), PR, {'events': outcomes}),
                         'REVIEW_NO_CODE_FINDING')

        class Store:
            writes = 0

            def get(self, _task_id):
                return record

            def compare_and_update(self, *_args, **_kwargs):
                Store.writes += 1

        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch('llmtool.task_advance.shutil.which', return_value='/bin/claude'):
            result = task_advance.advance(
                task_id=record['task_id'], store=Store(), pr_number=17, cwd='/repo/task',
                repair_review_call=lambda **_kwargs: self.fail('no repair may start'),
                github_get=lambda _path: [])
        self.assertEqual(result['step'], 'REVIEW_NO_CODE_FINDING')
        self.assertEqual(result['codes'], ['REVIEW_NO_CODE_FINDING'])
        self.assertEqual(Store.writes, 0)
        self.assertEqual(record['review_repair_claims'], [])

        calls = []

        def one_advance(**_kwargs):
            calls.append(_kwargs)
            return result

        driven = task_drive.drive(task_id=record['task_id'], advance_call=one_advance,
                                  sleep=lambda _seconds: self.fail('drive must not poll'))
        self.assertEqual(driven['step'], 'REVIEW_NO_CODE_FINDING')
        self.assertEqual(len(calls), 1)

    def test_no_finding_stop_runs_after_active_repair_branches(self):
        record, claim = self._completed_codex_review('FAIL', finding=False)
        outcomes = task_advance._review_outcomes(record, [claim], lambda _path: [])
        for repair_state, action in (('BLOCKED', 'REPAIR_RETRY'), ('RUNNING', 'WAITING_REPAIR'),
                                     ('UNKNOWN', 'REPAIR_OUTCOME_UNKNOWN'),
                                     ('RECORD_UNAVAILABLE', 'DEVELOPER_ATTENTION'),
                                     ('RESPONDED', 'PUBLISH')):
            with self.subTest(repair_state=repair_state):
                related = {'events': outcomes, 'repair_claims': [
                    {'head_sha': HEAD, 'state': 'DISPATCHED', 'repair_state': repair_state}]}
                self.assertEqual(task_advance.decide(record, d6(), PR, related), action)

    def test_completed_fail_with_finding_goes_to_repair(self):
        record, _claim = self._completed_codex_review('FAIL')
        repairs = []

        class Store:
            def get(self, _task_id):
                return record

        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch('llmtool.task_advance.shutil.which', return_value='/bin/claude'):
            result = task_advance.advance(
                task_id=record['task_id'], store=Store(), pr_number=17, cwd='/repo/task',
                repair_review_call=lambda **kwargs: repairs.append(kwargs) or {'state': 'OK'},
                github_get=lambda _path: [])
        self.assertEqual(result['step'], 'REPAIR_REVIEW')
        self.assertEqual(len(repairs), 1)

    def test_dispatched_claim_comment_fail_goes_to_repair(self):
        record = base_record(review_dispatch_claim=[
            {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'DISPATCHED'}])
        for event in ({}, {'findings_count': 0}):
            with self.subTest(event=event):
                related = {'events': [dict(event, source='REVIEWER', source_sha=HEAD,
                                           version=1, outcome='REVIEW_FAIL')]}
                self.assertEqual(task_advance.decide(record, d6(), PR, related),
                                 'REPAIR_REVIEW')

    def test_codex_fix_verdict_with_finding_parses_and_classifies_as_fail(self):
        review = '\n'.join((MARKER, STATUS, BINDING, 'Finding: the retry count is lost.'))
        raw = codex_jsonl(thread_started(), agent_message(bound_verdict(
            verdict='FIX', review=review)))
        parsed = review_dispatch._parse_codex_review_inner(
            raw, task_id=TASK, run_id=RUN, contract_digest=DIGEST, head=HEAD)
        self.assertEqual(parsed['verdict'], 'FIX')
        record, claim = self._completed_codex_review('FAIL')
        claim['review_result'].update(verdict='FIX', review_text=parsed['review_text'])
        latest, _event_id = review_result._completed_claim_result(record, claim, version=1)
        self.assertEqual(latest[2:], ('REVIEW_FAIL', 1))

    def test_claude_no_changes_repair_is_blocked_and_other_blocked_stays_unknown(self):
        blocked = {'developer': {'state': 'BLOCKED', 'error_code': 'CLAUDE_NO_CHANGES'}}
        for repair_record in (blocked, dict(blocked, state='PM_PENDING')):
            with self.subTest(repair_record=repair_record):
                self.assertEqual(task_advance.derive_repair_state(repair_record), 'BLOCKED')
        for code in ('CLAUDE_RESULT_UNKNOWN', 'CLAUDE_WRITE_SCOPE_VIOLATION', None):
            with self.subTest(code=code):
                self.assertEqual(task_advance.derive_repair_state(
                    {'developer': {'state': 'BLOCKED', 'error_code': code}}), 'UNKNOWN')
        record, _claim = self._completed_codex_review('FAIL')
        related = {'events': [{'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                               'outcome': 'REVIEW_FAIL', 'findings_count': 1}],
                   'repair_claims': [{'head_sha': HEAD, 'state': 'DISPATCHED',
                                      'repair_state': task_advance.derive_repair_state(blocked)}]}
        self.assertEqual(task_advance.decide(record, d6(), PR, related), 'REPAIR_RETRY')

    def test_completed_codex_review_without_event_requests_recording(self):
        record, claim = self._completed_codex_review('FAIL', recorded=False)
        outcomes = task_advance._review_outcomes(
            record, [claim], lambda _path: self.fail('completed review must not fetch GitHub'))
        self.assertEqual(outcomes, [])
        self.assertEqual(task_advance.decide(record, d6(), PR, {'events': outcomes}),
                         'RECORD_REVIEW_RESULT')

    def test_tampered_completed_codex_review_text_produces_no_outcome(self):
        record, claim = self._completed_codex_review('FAIL')
        claim['review_result']['review_text'] = claim['review_result']['review_text'].replace(
            f'head={HEAD}', f"head={'b' * 40}")
        outcomes = task_advance._review_outcomes(
            record, [claim], lambda _path: self.fail('completed review must not fetch GitHub'))
        self.assertEqual(outcomes, [])

    def test_repair_record_states_are_derived_for_waiting_and_retry(self):
        expected = (
            ({'state': 'PM_PENDING'}, 'PENDING', 'WAITING_REPAIR'),
            ({'developer': {'state': 'DEVELOPER_PENDING'}}, 'PENDING', 'WAITING_REPAIR'),
            ({'developer': {'state': 'DEVELOPER_DISPATCHING'}}, 'RUNNING', 'WAITING_REPAIR'),
            ({'developer': {'state': 'DEVELOPER_UNAVAILABLE'}}, 'UNAVAILABLE', 'REPAIR_RETRY'),
            ({'developer': {'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                            'error_code': 'CODEX_BLOCKED'}}, 'BLOCKED', 'REPAIR_RETRY'),
            ({'developer': {'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                            'execution': {'status': 'BLOCKED'}},
              'error_code': 'CODEX_BLOCKED'}, 'BLOCKED', 'REPAIR_RETRY'),
            ({'developer': {'state': 'DEVELOPER_OUTCOME_UNKNOWN'}}, 'UNKNOWN',
             'REPAIR_OUTCOME_UNKNOWN'),
            ({'developer': {'state': 'DEVELOPER_RESPONDED'}}, 'RESPONDED',
             'PUBLISH'),
            ({'developer': {'state': 'DEVELOPER_OUTPUT_REJECTED'}},
             'OUTPUT_REJECTED', 'REPAIR_RETRY'),
        )
        review_claim = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'DISPATCHED'}
        for repair_record, derived, action in expected:
            with self.subTest(repair_record=repair_record):
                self.assertEqual(task_advance.derive_repair_state(repair_record), derived)
                related = {
                    'events': [{'source': 'REVIEWER', 'source_sha': HEAD,
                                'version': 1, 'outcome': 'REVIEW_FAIL'}],
                    'repair_claims': [{'head_sha': HEAD, 'state': 'DISPATCHED',
                                      'repair_state': derived}],
                }
                self.assertEqual(task_advance.decide(
                    base_record(review_dispatch_claim=[review_claim]), d6(), PR, related), action)

    def test_missing_repair_task_record_is_developer_attention_with_specific_code(self):
        class Store:
            record = base_record(
                review_dispatch_claim=[{'pr': 17, 'head_sha': HEAD, 'version': 1,
                                        'state': 'DISPATCHED'}],
                review_repair_claims=[{'head_sha': HEAD, 'state': 'DISPATCHED',
                                       'repair_task_id': '3' * 32}])
            def __init__(self, unreadable=False):
                self.unreadable = unreadable
                self.writes = 0
            def get(self, task_id):
                if task_id == '3' * 32:
                    if self.unreadable:
                        raise OSError('unreadable')
                    return None
                return self.record
            def compare_and_update(self, *_args, **_kwargs):
                self.writes += 1

        for unreadable in (False, True):
            with self.subTest(unreadable=unreadable):
                store = Store(unreadable=unreadable)
                with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
                     patch.object(task_advance, '_pr_snapshot', return_value=PR), \
                     patch.object(task_advance, '_review_outcomes', return_value=[{
                         'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                         'outcome': 'REVIEW_FAIL'}]):
                    result = task_advance.advance(task_id=store.record['task_id'], store=store,
                        pr_number=17, github_get=lambda _path: [])
                self.assertEqual(result['step'], 'DEVELOPER_ATTENTION')
                self.assertEqual(result['codes'], ['REPAIR_TASK_RECORD_UNAVAILABLE'])
                self.assertEqual(store.writes, 0)

    def test_nonblocked_unknown_repair_has_specific_actionable_code(self):
        class Store:
            record = base_record(
                review_dispatch_claim=[{'pr': 17, 'head_sha': HEAD, 'version': 1,
                                        'state': 'DISPATCHED'}],
                review_repair_claims=[{'head_sha': HEAD, 'state': 'DISPATCHED',
                                       'repair_task_id': '3' * 32}])
            def get(self, task_id):
                if task_id == '3' * 32:
                    return {'developer': {'state': 'DEVELOPER_OUTCOME_UNKNOWN'}}
                return self.record

        store = Store()
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[{
                 'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_FAIL'}]):
            result = task_advance.advance(task_id=store.record['task_id'], store=store,
                pr_number=17, github_get=lambda _path: [])
        self.assertEqual(result['step'], 'REPAIR_OUTCOME_UNKNOWN')
        self.assertEqual(result['codes'], ['REPAIR_OUTCOME_UNKNOWN'])

    def test_pass_retries_completion_and_reaches_integration(self):
        claim = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'DISPATCHED'}
        related = {'events': [{'source': 'REVIEWER', 'source_sha': HEAD,
                               'version': 1, 'outcome': 'REVIEW_PASS'}]}
        record = base_record(review_dispatch_claim=[claim])
        self.assertEqual(task_advance.decide(record, d6(), PR, related), 'RUN_TASK_COMPLETE')
        record['completion'] = {'state': 'PM_EVIDENCE_READY', 'evidence': {
            'result': {'result_head': HEAD}, 'review_pr_ci': {'stage': 'READY'}}}
        self.assertEqual(task_advance.decide(record, d6(), PR, related), 'READY_FOR_INTEGRATION')

    def test_restart_does_not_dispatch_twice(self):
        class Store:
            def __init__(self):
                self.record = base_record()
            def get(self, _task_id):
                return self.record

        store = Store()
        dispatched = []
        def dispatch(**kwargs):
            dispatched.append(kwargs['version'])
            store.record['review_dispatch_claim'] = [{
                'pr': 17, 'head_sha': HEAD, 'version': kwargs['version'],
                'state': 'DISPATCHED', 'reviewer_task': 'reviewer-1'}]
            return store.record['review_dispatch_claim'][0]
        def review_result(**kwargs):
            return {'state': 'NOT_READY'}
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR):
            first = task_advance.advance(task_id=store.record['task_id'], store=store,
                pr_number=17, dispatch_review_call=dispatch, github_get=green_linux_ci)
            second = task_advance.advance(task_id=store.record['task_id'], store=store,
                pr_number=17, record_review_result_call=review_result, github_get=lambda _url: [])
        self.assertEqual(first['step'], 'DISPATCH_REVIEW')
        self.assertEqual(second['step'], 'RECORD_REVIEW_RESULT')
        self.assertEqual(dispatched, [1])

    def _advance_dispatched_review(self, *, claim_changes=None, reviewer_session_done=False,
                                   outcomes=None, completion=None):
        claim = {'executor_task': 'executor-1', 'pr': 17, 'head_sha': HEAD,
                 'version': 1, 'state': 'DISPATCHED',
                 'account_mode': 'DEFAULT_SECOND_ACCOUNT',
                 'claimed_at': '2026-10-27T00:00:00Z', 'updated_at': '2026-10-27T00:00:01Z',
                 'reviewer_task': 'reviewer-1', 'native_session_id': 'session-1'}
        claim.update(claim_changes or {})
        record = base_record(review_dispatch_claim=[claim])
        if completion is not None:
            record['completion'] = completion
        class Store:
            def get(self, _task_id):
                return record
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=outcomes or []):
            result = task_advance.advance(task_id=record['task_id'], store=Store(),
                pr_number=17, reviewer_session_done=reviewer_session_done,
                record_review_result_call=lambda **_kwargs: {'state': 'NOT_READY'},
                github_get=lambda _url: [])
        return result

    def test_done_reviewer_without_verdict_is_actionable(self):
        result = self._advance_dispatched_review(reviewer_session_done=True)
        self.assertEqual(result['step'], 'REVIEW_NOT_POSTED')
        self.assertEqual(result['next'], 'resume the reviewer to post its verdict')
        self.assertEqual(result['result'], {'reviewer_task': 'reviewer-1',
                                            'reviewer_session_id': 'session-1',
                                            'pr': 17, 'head_sha': HEAD, 'version': 1})
        self.assertEqual(result['codes'], ['REVIEW_VERDICT_NOT_POSTED'])

    def test_done_reviewer_with_persisted_missing_verdict_remains_actionable(self):
        result = self._advance_dispatched_review(reviewer_session_done=True, outcomes=[{
            'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
            'outcome': 'REVIEW_BLOCKED'}])
        self.assertEqual(result['step'], 'REVIEW_NOT_POSTED')
        self.assertEqual(result['next'], 'resume the reviewer to post its verdict')
        self.assertEqual(result['result'], {'reviewer_task': 'reviewer-1',
                                            'reviewer_session_id': 'session-1',
                                            'pr': 17, 'head_sha': HEAD, 'version': 1})
        self.assertEqual(result['codes'], ['REVIEW_VERDICT_NOT_POSTED'])

    def test_done_reviewer_with_current_head_pass_keeps_pass_path(self):
        result = self._advance_dispatched_review(reviewer_session_done=True, outcomes=[{
            'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
            'outcome': 'REVIEW_PASS'}], completion={
                'executor_task_id': 'executor-1', 'state': 'PM_EVIDENCE_READY', 'evidence': {
                    'result': {'result_head': HEAD}, 'review_pr_ci': {'stage': 'READY'}}})
        self.assertEqual(result['step'], 'READY_FOR_INTEGRATION')

    def test_older_head_verdict_does_not_count_and_running_reviewer_waits(self):
        old_head = 'b' * 40
        done = self._advance_dispatched_review(reviewer_session_done=True, outcomes=[{
            'source': 'REVIEWER', 'source_sha': old_head, 'version': 1,
            'outcome': 'REVIEW_PASS'}])
        running = self._advance_dispatched_review()
        self.assertEqual(done['step'], 'REVIEW_NOT_POSTED')
        self.assertEqual(running['step'], 'RECORD_REVIEW_RESULT')

    def test_restart_does_not_dispatch_repair_twice(self):
        class Store:
            def __init__(self):
                self.record = base_record(review_dispatch_claim=[{
                    'pr': 17, 'head_sha': HEAD, 'version': 1,
                    'state': 'DISPATCHED', 'reviewer_task': 'reviewer-1'}])
                self.repair = {'state': 'PM_PENDING'}
            def get(self, task_id):
                return self.repair if task_id == 'repair-one' else self.record

        store = Store()
        repaired = []
        def repair(**kwargs):
            repaired.append(kwargs['task_id'])
            store.record['review_repair_claims'] = [{
                'head_sha': HEAD, 'state': 'DISPATCHED',
                'repair_task_id': 'repair-one'}]
            return {'repair_task_id': 'repair-one'}
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[{
                 'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_FAIL'}]):
            first = task_advance.advance(task_id=store.record['task_id'], store=store,
                pr_number=17, cwd='/repo/task', repair_review_call=repair,
                github_get=lambda _url: [])
            second = task_advance.advance(task_id=store.record['task_id'], store=store,
                pr_number=17, cwd='/repo/task', repair_review_call=repair,
                github_get=lambda _url: [])
        self.assertEqual(first['step'], 'REPAIR_REVIEW')
        self.assertEqual(second['step'], 'WAITING_REPAIR')
        self.assertEqual(repaired, ['1' * 32])

    def test_unavailable_repair_is_failed_and_retried_once(self):
        class Store:
            def __init__(self):
                self.record = base_record(
                    developer={'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'codex'},
                    review_dispatch_claim=[{'pr': 17, 'head_sha': HEAD,
                        'version': 1, 'state': 'DISPATCHED'}],
                    review_repair_claims=[{'head_sha': HEAD, 'state': 'DISPATCHED',
                        'repair_task_id': '3' * 32, 'attempt': 1}],
                    record_sha256='d' * 64)
                self.repair = {'developer': {'state': 'DEVELOPER_UNAVAILABLE'}}
            def get(self, task_id):
                return self.repair if task_id == '3' * 32 else self.record
            def compare_and_update(self, _task_id, *, expected, **changes):
                if expected != {'record_sha256': self.record['record_sha256']}:
                    raise RuntimeError('conflict')
                self.record.update(changes)
                self.record['record_sha256'] = 'e' * 64
                return self.record

        store = Store()
        called = []
        def retry(**kwargs):
            called.append((store.record['review_repair_claims'][0]['state'],
                           kwargs.get('retry_claim_id')))
            print('{"repair_task_id":"4' + '4' * 31 + '"}')
            return {'ignored': True}
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[{
                 'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_FAIL'}]), \
             patch.object(task_advance.shutil, 'which', return_value='/usr/bin/codex'):
            result = task_advance.advance(task_id=store.record['task_id'], store=store,
                pr_number=17, cwd='/repo/task', repair_review_call=retry,
                github_get=lambda _path: [])
        self.assertEqual(result['step'], 'REPAIR_RETRY')
        self.assertEqual(result['result'], {'repair_task_id': '4' * 32})
        self.assertEqual(called, [('DISPATCHED', '3' * 32)])
        self.assertEqual(store.record['review_repair_claims'][0]['state'], 'DISPATCHED')

    def test_repair_retry_limit_error_is_reported_after_failed_claim(self):
        class Store:
            def __init__(self):
                self.record = base_record(
                    review_dispatch_claim=[{'pr': 17, 'head_sha': HEAD,
                        'version': 1, 'state': 'DISPATCHED'}],
                    review_repair_claims=[{'head_sha': HEAD, 'state': 'DISPATCHED',
                        'repair_task_id': '3' * 32, 'attempt': 3}],
                    record_sha256='d' * 64)
                self.repair = {'developer': {'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                    'error_code': 'CODEX_BLOCKED', 'execution': {'status': 'BLOCKED'}}}
            def get(self, task_id):
                return self.repair if task_id == '3' * 32 else self.record
            def compare_and_update(self, _task_id, *, expected, **changes):
                self.record.update(changes)
                self.record['record_sha256'] = 'e' * 64

        store = Store()
        def exhausted(**_kwargs):
            error = RuntimeError('bounded attempt limit')
            error.review_repair_code = 'REPAIR_LIMIT_REACHED'
            raise error
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[{
                 'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_FAIL'}]):
            result = task_advance.advance(task_id=store.record['task_id'], store=store,
                pr_number=17, cwd='/repo/task', repair_review_call=exhausted,
                github_get=lambda _path: [])
        self.assertEqual(result['step'], 'REPAIR_RETRY')
        self.assertIn('REPAIR_LIMIT_REACHED', result['codes'])
        self.assertEqual(store.record['review_repair_claims'][0]['state'], 'DISPATCHED')

    def test_missing_runtime_stops_before_repair_claim_transition(self):
        class Store:
            def __init__(self):
                self.record = base_record(
                    developer={'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'codex'},
                    review_dispatch_claim=[{'pr': 17, 'head_sha': HEAD,
                        'version': 1, 'state': 'DISPATCHED'}],
                    review_repair_claims=[{'head_sha': HEAD, 'state': 'DISPATCHED',
                        'repair_task_id': '3' * 32, 'attempt': 1}])
                self.repair = {'developer': {'state': 'DEVELOPER_UNAVAILABLE'}}
                self.updated = False
            def get(self, task_id):
                return self.repair if task_id == '3' * 32 else self.record
            def compare_and_update(self, *_args, **_kwargs):
                self.updated = True
                raise AssertionError('claim transition must not occur')

        store = Store()
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[{
                 'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_FAIL'}]), \
             patch.object(task_advance.shutil, 'which', return_value=None):
            result = task_advance.advance(task_id=store.record['task_id'], store=store,
                pr_number=17, cwd='/repo/task', repair_review_call=lambda **_kwargs:
                    self.fail('repair provider must not run'), github_get=lambda _path: [])
        self.assertEqual(result['step'], 'RUNTIME_NOT_ON_PATH')
        self.assertEqual(result['codes'], ['RUNTIME_NOT_ON_PATH'])
        self.assertFalse(store.updated)

    def test_separate_tasks_and_merge_train_closure_decision(self):
        blocked = base_record(task_id='3' * 32,
                              developer={'state': 'DEVELOPER_OUTCOME_UNKNOWN'})
        ready = base_record(review_dispatch_claim=[{
            'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'DISPATCHED'}],
            completion={'executor_task_id': 'executor-1', 'state': 'PM_EVIDENCE_READY', 'evidence': {
                'result': {'result_head': HEAD}, 'review_pr_ci': {'stage': 'READY'}}})
        related = {'events': [{'source': 'REVIEWER', 'source_sha': HEAD,
                               'version': 1, 'outcome': 'REVIEW_PASS'}]}
        self.assertEqual(task_advance.decide(blocked, d6(), PR, {}), 'DEVELOPER_ATTENTION')
        self.assertEqual(task_advance.decide(ready, d6(), PR, related), 'READY_FOR_INTEGRATION')

    def test_merge_train_closure_records_one_merge_event(self):
        class Store:
            def __init__(self, record):
                self.record = record
            def get(self, _task_id):
                return self.record

        ready = base_record(review_dispatch_claim=[{
            'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'DISPATCHED'}],
            completion={'state': 'PM_EVIDENCE_READY', 'evidence': {
                'result': {'result_head': HEAD}, 'review_pr_ci': {'stage': 'READY'}}})
        store = Store(ready)
        saved = []
        integration = {'number': 27, 'merged': True,
                       'head': {'sha': 'c' * 40}, 'merge_commit_sha': 'd' * 40}
        def pr_snapshot(number, _get):
            return PR if number == 17 else integration
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', side_effect=pr_snapshot), \
             patch.object(task_advance, '_integration_delta_verified',
                          return_value=True) as delta, \
             patch.object(task_advance, '_review_outcomes', return_value=[{
                 'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}]):
            result = task_advance.advance(task_id=ready['task_id'], store=store,
                pr_number=17, integration_pr=27,
                github_get=lambda url: {'status': 'ahead'} if url.startswith('compare/') else [],
                record_github_event_call=lambda *args: saved.append(args))
        self.assertEqual(result['step'], 'CLOSED')
        self.assertEqual(delta.call_args.args[1:5], (HEAD, 'c' * 40, 'd' * 40, 27))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0][-2:], ('MERGED', 'd' * 40))

    def test_verified_merge_closes_when_d6_binding_is_unavailable(self):
        class Store:
            def __init__(self, record):
                self.record = record
            def get(self, _task_id):
                return self.record

        ready = base_record(review_dispatch_claim=[{
            'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'DISPATCHED'}],
            completion={'state': 'PM_EVIDENCE_READY', 'evidence': {
                'result': {'result_head': HEAD}, 'review_pr_ci': {'stage': 'READY'}}})
        merged_review = {'number': 17, 'merged': True,
                         'head': {'sha': HEAD}, 'merge_commit_sha': 'd' * 40}
        integration = {'number': 27, 'merged': True,
                       'head': {'sha': 'c' * 40}, 'merge_commit_sha': 'e' * 40}
        for integration_pr, pr_value, expected_number, expected_sha in (
                (None, merged_review, 17, 'd' * 40),
                (27, PR, 27, 'e' * 40)):
            with self.subTest(integration_pr=integration_pr):
                saved = []
                def pr_snapshot(number, _get):
                    return pr_value if number == 17 else integration
                with patch.object(task_advance, '_d6_snapshot', return_value={
                        'state': 'D6_BINDING_UNAVAILABLE',
                        'code': 'D6_BINDING_UNAVAILABLE'}), \
                     patch.object(task_advance, '_pr_snapshot', side_effect=pr_snapshot), \
                     patch.object(task_advance, '_integration_delta_verified',
                                  return_value=True) as delta, \
                     patch.object(task_advance, '_review_outcomes', return_value=[{
                         'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                         'outcome': 'REVIEW_PASS'}]):
                    result = task_advance.advance(task_id=ready['task_id'], store=Store(ready),
                        pr_number=17, integration_pr=integration_pr,
                        github_get=lambda _url: {'status': 'ahead'},
                        record_github_event_call=lambda *args: saved.append(args))
                self.assertEqual(result['step'], 'CLOSED')
                if integration_pr is None:
                    delta.assert_not_called()
                else:
                    self.assertEqual(delta.call_args.args[1:5], (HEAD, 'c' * 40, 'e' * 40, 27))
                self.assertEqual(saved[0][4], expected_number)
                self.assertEqual(saved[0][-2:], ('MERGED', expected_sha))

    def test_integration_compare_uses_real_github_path_validator(self):
        from llmtool.task_completion import _GITHUB_API_PATH
        ready = base_record(review_dispatch_claim=[{
            'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'DISPATCHED'}],
            completion={'state': 'PM_EVIDENCE_READY', 'evidence': {
                'result': {'result_head': HEAD}, 'review_pr_ci': {'stage': 'READY'}}})
        integration = {'number': 27, 'merged': True,
                       'head': {'sha': 'c' * 40}, 'merge_commit_sha': 'd' * 40}
        def get(path):
            self.assertTrue(_GITHUB_API_PATH.fullmatch(path), path)
            return {'status': 'ahead'}
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', side_effect=lambda n, _: PR if n == 17 else integration), \
             patch.object(task_advance, '_integration_delta_verified', return_value=True), \
             patch.object(task_advance, '_review_outcomes', return_value=[{
                 'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}]):
            result = task_advance.advance(task_id=ready['task_id'], store=type('S', (), {
                'get': lambda self, _id: ready})(), pr_number=17, integration_pr=27, github_get=get,
                record_github_event_call=lambda *args, **kwargs: None)
        self.assertEqual(result['step'], 'CLOSED')

    def test_d6_snapshot_failure_is_typed(self):
        record = base_record()
        record['completion']['evidence']['common_dir'] = '/invalid'
        with patch.object(task_advance.task_completion, 'read_d6_registration',
                          side_effect=RuntimeError('unavailable')):
            snapshot = task_advance._d6_snapshot(record, 'executor-1')
        self.assertEqual(snapshot['state'], 'D6_BINDING_UNAVAILABLE')
        self.assertEqual(snapshot['code'], 'TASK_ADVANCE_REFUSED')

    def test_completion_action_captures_nested_json_output(self):
        class Store:
            def get(self, _task_id):
                return base_record(completion={'executor_task_id': 'executor-1',
                    'state': 'WAITING_REVIEW', 'evidence': {'result': {'result_head': HEAD}}},
                    review_dispatch_claim=[{'pr': 17, 'head_sha': HEAD,
                        'version': 1, 'state': 'DISPATCHED', 'reviewer_task': 'reviewer'}])
        store = Store()
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[{
                 'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}]):
            # Exercise advance's capture around the production completion adapter.
            def completion(*_args, **_kwargs):
                print('{"task_id":"done"}')
                return 0
            result = task_advance.advance(task_id='1' * 32, store=store, pr_number=17,
                run_task_complete_call=completion, github_get=lambda _path: [])
        self.assertEqual(result['result'], {'task_id': 'done'})

    def test_review_result_checks_posted_pass_before_missing_verdict_mapping(self):
        claim = {'executor_task': 'executor-1', 'pr': 17, 'head_sha': HEAD,
                 'version': 1, 'state': 'DISPATCHED', 'reviewer_task': 'reviewer-1',
                 'native_session_id': 'session-1'}
        record = base_record(review_dispatch_claim=[claim], schema_version=2,
            run_id='2' * 32, pm_session_id='pm-session', task_binding={
                'contract_digest': 'd' * 64}, developer={
                    'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'codex'})
        record['completion_events'] = []

        class Store:
            def __init__(self):
                self.record = record
            def get(self, _task_id):
                return self.record
            def record_completion_event(self, _task_id, **event):
                self.record['completion_events'].append(event)
                return {'created': True}

        comment = {'id': 9, 'created_at': '2026-10-27T01:00:00Z',
            'updated_at': '2026-10-27T01:00:00Z', 'body':
            '<!-- activity: type=review_verdict actor=Claude target=PM status=PASS -->\n'
            f'Verdict: PASS | Version: v1 | HEAD: {HEAD}\n'}
        store = Store()
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance.task_completion, 'is_formal_claude_pass',
                          return_value=True):
            result = task_advance.advance(task_id=record['task_id'], store=store,
                pr_number=17, reviewer_session_done=True, github_get=lambda _url: [comment])
        self.assertEqual(result['step'], 'RECORD_REVIEW_RESULT')
        self.assertEqual(result['result']['state'], 'REVIEW_PASS')
        self.assertEqual(len(store.record['completion_events']), 1)

    def test_repair_action_captures_nested_json_output(self):
        claim = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'DISPATCHED'}
        failed = base_record(review_dispatch_claim=[claim])
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[{
                 'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_FAIL'}]):
            def repair(**_kwargs):
                print('{"repair_task_id":"repair-1"}')
                return {'ignored': True}
            result = task_advance.advance(task_id=failed['task_id'],
                store=type('S', (), {'get': lambda self, _id: failed})(),
                pr_number=17, cwd='/repo/task', repair_review_call=repair,
                github_get=lambda _path: [])
        self.assertEqual(result['result'], {'repair_task_id': 'repair-1'})

    def test_advance_acks_only_superseded_events_and_is_idempotent(self):
        old_event = {'event_id': 'a' * 32, 'source': 'REVIEWER',
                     'source_sha': 'b' * 40, 'state': 'PM_CONSUMED'}
        record = base_record(pm_session_id='pm-session', completion_events=[old_event])
        class Store:
            def get(self, _task_id):
                return record
        store = Store()
        calls = []
        def ack(_store, event_id, _session_id):
            calls.append(event_id)
            old_event['state'] = 'ACKED'
            return {'acknowledged_now': True}
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[]), \
             patch.object(task_advance.active_pm_inbox, 'ack', side_effect=ack):
            first = task_advance.advance(task_id=record['task_id'], store=store,
                pr_number=17, dispatch_review_call=lambda **_kwargs: {'state': 'DISPATCHED'},
                github_get=green_linux_ci)
            second = task_advance.advance(task_id=record['task_id'], store=store,
                pr_number=17, dispatch_review_call=lambda **_kwargs: {'state': 'DISPATCHED'},
                github_get=green_linux_ci)
        self.assertEqual(first['step'], 'DISPATCH_REVIEW')
        self.assertEqual(first['acked'], ['a' * 32])
        self.assertEqual(second['step'], first['step'])
        self.assertEqual(second['acked'], [])
        self.assertEqual(calls, ['a' * 32])

    def test_advance_keeps_current_head_reviewer_event_for_decide(self):
        claim = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'DISPATCHED'}
        current_event = {'event_id': 'a' * 32, 'source': 'REVIEWER',
                         'source_sha': HEAD, 'version': 1, 'outcome': 'REVIEW_BLOCKED',
                         'state': 'PM_CONSUMED'}
        record = base_record(pm_session_id='pm-session', review_dispatch_claim=[claim],
                             completion_events=[current_event])
        class Store:
            def get(self, _task_id):
                return record
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[]), \
             patch.object(task_advance.active_pm_inbox, 'ack') as ack:
            result = task_advance.advance(task_id=record['task_id'], store=Store(),
                pr_number=17, github_get=lambda _url: [])
        expected = task_advance.decide(record, d6(), PR, {'events': [current_event],
                                                          'repair_claims': []})
        self.assertEqual(result['step'], expected)
        self.assertEqual(result['step'], 'REVIEWER_ATTENTION')
        self.assertEqual(result['acked'], [])
        ack.assert_not_called()

    def test_advance_closed_acks_every_unacked_event(self):
        claim = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'DISPATCHED'}
        events = [
            {'event_id': 'a' * 32, 'source': 'REVIEWER', 'source_sha': HEAD,
             'version': 1, 'outcome': 'REVIEW_PASS', 'state': 'PM_CONSUMED'},
            {'event_id': 'b' * 32, 'source': 'HOST_WORKER', 'state': 'PENDING'},
            {'event_id': 'c' * 32, 'source': 'HOST_WORKER', 'state': 'ACKED'},
        ]
        record = base_record(pm_session_id='pm-session', review_dispatch_claim=[claim],
            completion={'executor_task_id': 'executor-1', 'state': 'PM_EVIDENCE_READY',
                'evidence': {'result': {'result_head': HEAD}, 'review_pr_ci': {'stage': 'READY'}}},
            completion_events=events)
        class Store:
            def get(self, _task_id):
                return record
        calls = []
        def ack(_store, event_id, _session_id):
            calls.append(event_id)
            return {'acknowledged_now': True}
        merged_pr = dict(PR, merged=True, merge_commit_sha='d' * 40)
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=merged_pr), \
             patch.object(task_advance, '_review_outcomes', return_value=[{
                 'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}]), \
             patch.object(task_advance.active_pm_inbox, 'ack', side_effect=ack):
            result = task_advance.advance(task_id=record['task_id'], store=Store(),
                pr_number=17, github_get=lambda _url: [],
                record_github_event_call=lambda *_args, **_kwargs: None)
        self.assertEqual(result['step'], 'CLOSED')
        self.assertEqual(result['acked'], ['a' * 32, 'b' * 32])
        self.assertEqual(calls, ['a' * 32, 'b' * 32])

    def test_advance_closed_acks_newly_recorded_merge_event(self):
        claim = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'DISPATCHED'}
        record = base_record(pm_session_id='pm-session', review_dispatch_claim=[claim],
            completion={'state': 'PM_EVIDENCE_READY', 'evidence': {
                'result': {'result_head': HEAD}, 'review_pr_ci': {'stage': 'READY'}}})
        class Store:
            def __init__(self):
                self.record = record
            def get(self, _task_id):
                return self.record
        store = Store()
        def record_merge_event(_store, _record, session_id, event_id,
                               issue_number, item_id, marker_type, status, sha):
            store.record['completion_events'].append({
                'event_id': event_id, 'source': 'GITHUB', 'source_sha': sha,
                'session_id': session_id, 'issue_number': issue_number,
                'item_id': item_id, 'marker_type': marker_type, 'status': status,
                'state': 'PENDING'})
        def ack(_store, event_id, _session_id):
            event = next(item for item in store.record['completion_events']
                         if item['event_id'] == event_id)
            event['state'] = 'ACKED'
            return {'acknowledged_now': True}
        merged_pr = dict(PR, merged=True, merge_commit_sha='d' * 40)
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=merged_pr), \
             patch.object(task_advance, '_review_outcomes', return_value=[{
                 'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}]), \
             patch.object(task_advance.active_pm_inbox, 'ack', side_effect=ack):
            result = task_advance.advance(task_id=record['task_id'], store=store,
                pr_number=17, github_get=lambda _url: [],
                record_github_event_call=record_merge_event)
        merge_event = next(event for event in store.record['completion_events']
                           if event.get('source') == 'GITHUB')
        self.assertEqual(result['step'], 'CLOSED')
        self.assertEqual(result['acked'], [merge_event['event_id']])
        self.assertEqual(merge_event['state'], 'ACKED')

    def test_advance_ack_failure_adds_code_without_changing_step(self):
        event = {'event_id': 'a' * 32, 'source': 'REVIEWER',
                 'source_sha': 'b' * 40, 'state': 'PM_CONSUMED'}
        record = base_record(pm_session_id='pm-session', completion_events=[event])
        class Store:
            def get(self, _task_id):
                return record
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[]), \
             patch.object(task_advance.active_pm_inbox, 'ack', side_effect=RuntimeError('store')):
            result = task_advance.advance(task_id=record['task_id'], store=Store(),
                pr_number=17, dispatch_review_call=lambda **_kwargs: {'state': 'DISPATCHED'},
                github_get=green_linux_ci)
        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        self.assertEqual(result['result'], {'state': 'DISPATCHED'})
        self.assertEqual(result['next'], 'DISPATCH_REVIEW')
        self.assertEqual(result['codes'], ['ACK_SKIPPED'])
        self.assertEqual(result['acked'], [])

    def test_cli_advance_prints_one_json_object_for_action_steps(self):
        cases = []

        repair_record = base_record(
            review_dispatch_claim=[{'pr': 17, 'head_sha': HEAD, 'version': 1,
                                    'state': 'DISPATCHED'}],
            completion_events=[{'source': 'REVIEWER', 'source_sha': HEAD,
                                'version': 1, 'outcome': 'REVIEW_FAIL'}])
        def repair_review(**_kwargs):
            print('{"repair_task_id":"repair-new"}')
            return {'ignored': True}
        cases.append((repair_record, {'repair_review_call': repair_review,
                                      'cwd': '/repo/task'}, 'REPAIR_REVIEW', None))

        retry_record = base_record(
            review_dispatch_claim=[{'pr': 17, 'head_sha': HEAD, 'version': 1,
                                    'state': 'DISPATCHED'}],
            review_repair_claims=[{'head_sha': HEAD, 'state': 'DISPATCHED',
                                   'repair_task_id': 'repair-old', 'attempt': 1}],
            completion_events=[{'source': 'REVIEWER', 'source_sha': HEAD,
                                'version': 1, 'outcome': 'REVIEW_FAIL'}])
        class RetryStore:
            def get(self, task_id):
                if task_id == 'repair-old':
                    return {'developer': {'state': 'DEVELOPER_UNAVAILABLE'}}
                return retry_record
        def repair_retry(**_kwargs):
            print('{"repair_task_id":"repair-retry"}')
            return {'ignored': True}
        cases.append((retry_record, {'repair_review_call': repair_retry,
                                     'cwd': '/repo/task'}, 'REPAIR_RETRY', RetryStore()))

        completion_record = base_record(
            review_dispatch_claim=[{'pr': 17, 'head_sha': HEAD, 'version': 1,
                                    'state': 'DISPATCHED'}],
            completion_events=[{'source': 'REVIEWER', 'source_sha': HEAD,
                                'version': 1, 'outcome': 'REVIEW_PASS'}])
        def run_completion(_args, **_kwargs):
            print('{"completion":"recorded"}')
            return 0
        cases.append((completion_record, {'run_task_complete_call': run_completion},
                      'RUN_TASK_COMPLETE', None))

        dispatch_record = base_record()
        def dispatch_review(**_kwargs):
            print('{"reviewer_task":"reviewer-new"}')
            return {'ignored': True}
        cases.append((dispatch_record, {'dispatch_review_call': dispatch_review,
                                        'github_get': green_linux_ci},
                      'DISPATCH_REVIEW', None))

        # #1124: each case runs as the Task's own PM session.
        native = '3f2b8c1e-4d5a-4b6c-8d7e-9f0a1b2c3d4e'
        for record, adapters, expected_step, custom_store in cases:
            with self.subTest(step=expected_step):
                record['pm_session_id'] = 'pm-' + native
                store = custom_store or type('Store', (), {
                    'get': lambda self, _task_id: record})()
                with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
                     patch.object(task_advance, '_pr_snapshot', return_value=PR), \
                     patch.object(task_advance, '_review_outcomes', return_value=[]), \
                     patch.dict(os.environ, {'CLAUDE_CODE_SESSION_ID': '',
                                             'CODEX_THREAD_ID': ''}):
                    adapters.setdefault('github_get', lambda _path: [])
                    args = parser_for_cli().parse_args([
                        'task', 'advance', record['task_id'], '--pr', '17',
                        '--pm-session', native])
                    output = io.StringIO()
                    with redirect_stdout(output):
                        task_submission_cli.run_task_advance(args, store=store,
                            **adapters)
                lines = output.getvalue().splitlines()
                self.assertEqual(len(lines), 1)
                self.assertTrue(lines[0])
                result = json.loads(lines[0])
                self.assertEqual(result['task_id'], record['task_id'])
                self.assertEqual(result['step'], expected_step)
                self.assertIn('next', result)
                self.assertIn('codes', result)


def check_run(run_id=1, *, head=HEAD, name='check (Linux 3.14)',
              status='completed', conclusion='success'):
    return {'id': run_id, 'head_sha': head, 'name': name, 'status': status,
            'conclusion': conclusion}


class CiGateStore:
    def __init__(self, record):
        self.record = record

    def get(self, _task_id):
        return self.record


class TaskAdvanceCiGateTests(unittest.TestCase):
    def advance_with_ci(self, reply, *, pr=PR):
        record = base_record()
        store = CiGateStore(record)
        calls = []

        def github_get(path):
            calls.append(path)
            if path.startswith('commits/'):
                if isinstance(reply, Exception):
                    raise reply
                return reply[0]
            if path.endswith('/files?per_page=100'):
                return reply[1]
            return pr

        dispatched = []
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_review_outcomes', return_value=[]):
            result = task_advance.advance(task_id=record['task_id'], store=store,
                pr_number=17, github_get=github_get,
                dispatch_review_call=lambda **kw: dispatched.append(kw) or {'sent': True})
        return result, dispatched, calls

    def test_success_dispatches_reviewer(self):
        result, dispatched, calls = self.advance_with_ci(
            ({'total_count': 1, 'check_runs': [check_run()]},
             [{'filename': 'src/app.py'}]))
        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        self.assertEqual(len(dispatched), 1)
        checks_read = f'commits/{HEAD}/check-runs?per_page=100'
        files_read = 'pulls/17/files?per_page=100'
        self.assertIn(checks_read, calls)
        self.assertIn(files_read, calls)
        self.assertLess(calls.index(checks_read), calls.index(files_read))

    def test_missing_run_waits_without_dispatch(self):
        result, dispatched, _ = self.advance_with_ci(
            ({'total_count': 0, 'check_runs': []}, [{'filename': 'src/app.py'}]))
        self.assertEqual((result['step'], result['codes']),
                         ('WAITING_CI', ['LINUX_CI_PENDING']))
        self.assertEqual(dispatched, [])

    def test_incomplete_run_waits_without_dispatch(self):
        result, dispatched, _ = self.advance_with_ci(
            ({'total_count': 1, 'check_runs': [check_run(status='in_progress',
                                                         conclusion=None)]},
             [{'filename': 'src/app.py'}]))
        self.assertEqual((result['step'], result['codes']),
                         ('WAITING_CI', ['LINUX_CI_PENDING']))
        self.assertEqual(dispatched, [])

    def test_non_success_conclusion_fails_without_dispatch(self):
        result, dispatched, _ = self.advance_with_ci(
            ({'total_count': 1, 'check_runs': [check_run(conclusion='failure')]},
             [{'filename': 'src/app.py'}]))
        self.assertEqual((result['step'], result['codes']),
                         ('CI_FAILED', ['LINUX_CI_FAILED']))
        self.assertEqual(dispatched, [])

    def test_read_failure_and_malformed_reply_wait_without_dispatch(self):
        for reply in (RuntimeError('unavailable'), ({'check_runs': []}, [])):
            with self.subTest(reply=reply):
                result, dispatched, _ = self.advance_with_ci(reply)
                self.assertEqual((result['step'], result['codes']),
                                 ('WAITING_CI', ['LINUX_CI_UNAVAILABLE']))
                self.assertEqual(dispatched, [])

    def test_docs_only_selects_docs_check(self):
        result, dispatched, _ = self.advance_with_ci(
            ({'total_count': 1, 'check_runs': [check_run(name='check (docs)')]},
             [{'filename': 'docs/guide.md'}]))
        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        self.assertEqual(len(dispatched), 1)

    def test_stale_head_run_is_ignored(self):
        result, dispatched, _ = self.advance_with_ci(
            ({'total_count': 1, 'check_runs': [check_run(head='b' * 40)]},
             [{'filename': 'src/app.py'}]))
        self.assertEqual((result['step'], result['codes']),
                         ('WAITING_CI', ['LINUX_CI_PENDING']))
        self.assertEqual(dispatched, [])

    def test_highest_id_run_decides(self):
        scenarios = (([check_run(2, conclusion='failure'), check_run(3)],
                      'DISPATCH_REVIEW', []),
                     ([check_run(2), check_run(3, conclusion='failure')],
                      'CI_FAILED', ['LINUX_CI_FAILED']))
        for runs, expected_step, expected_codes in scenarios:
            with self.subTest(runs=runs):
                result, dispatched, _ = self.advance_with_ci(
                    ({'total_count': 2, 'check_runs': runs},
                     [{'filename': 'src/app.py'}]))
                self.assertEqual(result['step'], expected_step)
                self.assertEqual(result['codes'], expected_codes)
                self.assertEqual(len(dispatched), int(expected_step == 'DISPATCH_REVIEW'))

    def test_remote_test_summary_and_pr_comment_exclude_excerpt(self):
        record = base_record(remote_test_outcomes=[])
        store = CiGateStore(record)
        receipt = remote_receipt(record, result='FAIL', excerpt='private failure text')
        receipt.update(ran_total=12, elapsed_s=3.5)
        receipt.pop('receipt_digest')
        receipt['receipt_digest'] = hashlib.sha256(_json_bytes(receipt)).hexdigest()
        calls = []

        def remote_test(_args, *, store):
            store.record.setdefault('remote_test_receipts', []).append(receipt)
            return 0

        def gh_runner(_root, argv):
            calls.append(argv)
            return type('Response', (), {'returncode': 0})()

        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[]), \
             patch.object(task_submission_cli, 'run_task_remote_test', side_effect=remote_test):
            result = task_advance.advance(task_id=record['task_id'], store=store,
                pr_number=17, publish_gh_runner=gh_runner)
        summary = result['result']['remote_test']
        self.assertEqual(summary, {'result': 'FAIL', 'modules': ['test_task_advance'],
            'ran_total': 12, 'elapsed_s': 3.5,
            'receipt_digest': receipt['receipt_digest'][:8],
            'hosted_run_id': None})
        self.assertEqual(result['step'], 'RUN_REMOTE_TEST')
        self.assertEqual(calls[0][:3], ['pr', 'comment', '17'])
        body = calls[0][calls[0].index('--body') + 1]
        self.assertTrue(body.startswith(
            '<!-- activity: type=task actor=PM target=PR status=TRACKING -->\n'))
        self.assertIn('EC2 remote-test', body)
        self.assertIn('D6=executor-1', body)
        self.assertNotIn(d6()['root'], body)
        self.assertNotIn('private failure text', body)

    def test_remote_test_comment_text_and_malformed_actions(self):
        record = base_record(remote_test_outcomes=[])
        d6_obj = {'task_id': 'executor-1', 'root': '/repo'}
        calls = []

        def gh_runner(_root, argv):
            calls.append(argv)
            return type('Response', (), {'returncode': 0})()

        # EC2 receipt: body text after marker line starts with 'EC2 remote-test, result='
        ec2_receipt = remote_receipt(record, result='PASS')
        summary_ec2 = task_advance._remote_test_receipt_summary(
            {'remote_test_receipts': [ec2_receipt]}, HEAD)
        self.assertIsNotNone(summary_ec2)
        self.assertIsNone(summary_ec2['hosted_run_id'])
        task_advance._post_remote_test_comment(gh_runner, '/repo', 17, record['task_id'], d6_obj, HEAD, summary_ec2)
        body_ec2 = calls[-1][calls[-1].index('--body') + 1]
        marker, rest_ec2 = body_ec2.split('\n', 1)
        self.assertEqual(marker, '<!-- activity: type=task actor=PM target=PR status=TRACKING -->')
        self.assertEqual(
            rest_ec2,
            f"EC2 remote-test, result=PASS, modules=test_task_advance, tests run=1, seconds=0.01, receipt={ec2_receipt['receipt_digest'][:8]}, Task={record['task_id']}, D6=executor-1, head={HEAD}")

        # Hosted receipt: body text after marker line starts with 'remote-test (GitHub-hosted run <id>), result='
        hosted_receipt = remote_receipt(record, result='PASS', actions={'run_id': 7890})
        summary_hosted = task_advance._remote_test_receipt_summary(
            {'remote_test_receipts': [hosted_receipt]}, HEAD)
        self.assertIsNotNone(summary_hosted)
        self.assertEqual(summary_hosted['hosted_run_id'], 7890)
        task_advance._post_remote_test_comment(gh_runner, '/repo', 17, record['task_id'], d6_obj, HEAD, summary_hosted)
        body_hosted = calls[-1][calls[-1].index('--body') + 1]
        marker, rest_hosted = body_hosted.split('\n', 1)
        self.assertEqual(marker, '<!-- activity: type=task actor=PM target=PR status=TRACKING -->')
        self.assertEqual(
            rest_hosted,
            f"remote-test (GitHub-hosted run 7890), result=PASS, modules=test_task_advance, tests run=1, seconds=0.01, receipt={hosted_receipt['receipt_digest'][:8]}, Task={record['task_id']}, D6=executor-1, head={HEAD}")

        # Malformed actions blocks give no run id and the EC2 text
        malformed = (
            'not-a-dict',
            {},
            {'run_id': 'bad'},
            {'run_id': 0},
            {'run_id': -1},
            {'run_id': None},
            {'run_id': True},
            {'run_id': 3.14},
        )
        for bad_actions in malformed:
            with self.subTest(bad_actions=bad_actions):
                bad_receipt = remote_receipt(record, result='PASS', actions=bad_actions)
                summary_bad = task_advance._remote_test_receipt_summary(
                    {'remote_test_receipts': [bad_receipt]}, HEAD)
                self.assertIsNotNone(summary_bad)
                self.assertIsNone(summary_bad['hosted_run_id'])
                task_advance._post_remote_test_comment(gh_runner, '/repo', 17, record['task_id'], d6_obj, HEAD, summary_bad)
                body_bad = calls[-1][calls[-1].index('--body') + 1]
                marker, rest_bad = body_bad.split('\n', 1)
                self.assertEqual(marker, '<!-- activity: type=task actor=PM target=PR status=TRACKING -->')
                self.assertEqual(
                    rest_bad,
                    f"EC2 remote-test, result=PASS, modules=test_task_advance, tests run=1, seconds=0.01, receipt={bad_receipt['receipt_digest'][:8]}, Task={record['task_id']}, D6=executor-1, head={HEAD}")

    def test_remote_test_comment_failure_adds_only_warning_code(self):
        record = base_record(remote_test_outcomes=[])
        store = CiGateStore(record)
        receipt = remote_receipt(record)
        receipt.update(ran_total=1, elapsed_s=1.0)
        receipt.pop('receipt_digest')
        receipt['receipt_digest'] = hashlib.sha256(_json_bytes(receipt)).hexdigest()

        def remote_test(_args, *, store):
            store.record.setdefault('remote_test_receipts', []).append(receipt)
            return 0

        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[]), \
             patch.object(task_submission_cli, 'run_task_remote_test', side_effect=remote_test):
            result = task_advance.advance(task_id=record['task_id'], store=store,
                pr_number=17,
                publish_gh_runner=lambda *_args: type('Response', (), {'returncode': 1})())
        self.assertEqual(result['codes'], ['REMOTE_TEST_COMMENT_FAILED'])
        self.assertEqual(result['result']['remote_test']['result'], 'PASS')


class TaskAdvancePublishTests(unittest.TestCase):
    TASK_ID = '1' * 32
    EXECUTOR_TASK = '3' * 32
    BRANCH = 'feat/task-publish'
    WRITE_PATHS = ['allowed.txt']

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(os.path.join(self.tmp.name, 'task'))
        self.bare = os.path.join(self.tmp.name, 'remote.git')
        os.mkdir(self.root)
        self.git(['init', '--bare', self.bare], cwd=self.tmp.name)
        self.git(['init', '-b', self.BRANCH], cwd=self.root)
        self.git(['config', 'user.name', 'Task Test'], cwd=self.root)
        self.git(['config', 'user.email', 'task-test@example.invalid'], cwd=self.root)
        with open(os.path.join(self.root, 'allowed.txt'), 'w', encoding='utf-8') as stream:
            stream.write('base\n')
        self.git(['add', '--', 'allowed.txt'], cwd=self.root)
        self.git(['commit', '-m', 'base'], cwd=self.root)
        self.git(['remote', 'add', 'origin', self.bare], cwd=self.root)
        self.git(['push', 'origin', 'HEAD:refs/heads/main'], cwd=self.root)
        self.git(['push', '-u', 'origin', self.BRANCH], cwd=self.root)
        self.initial_head = self.git(['rev-parse', 'HEAD'], cwd=self.root).strip()
        self.recorded = []
        self.gh_calls = []
        self.pr_exists = False

    @staticmethod
    def git(args, *, cwd):
        return subprocess.run(['git', *args], cwd=cwd, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout

    @staticmethod
    def git_runner(root, args):
        return subprocess.run(['git', *args], cwd=root, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)

    def record_result(self, **kwargs):
        self.recorded.append(kwargs)
        return self.git(['rev-parse', 'HEAD'], cwd=self.root).strip()

    def gh(self, _root, args):
        self.gh_calls.append(list(args))
        if args[:2] == ['pr', 'list']:
            pulls = []
            if self.pr_exists:
                pulls = [{'number': 23,
                          'headRefOid': self.git(['rev-parse', 'HEAD'], cwd=self.root).strip()}]
            return subprocess.CompletedProcess(['gh', *args], 0,
                json.dumps(pulls).encode(), b'')
        if args[:2] == ['pr', 'create']:
            self.pr_exists = True
            return subprocess.CompletedProcess(['gh', *args], 0, b'', b'')
        if args[:2] == ['pr', 'comment']:
            return subprocess.CompletedProcess(['gh', *args], 0, b'', b'')
        raise AssertionError('unexpected fake gh command')

    def publish(self, *, write_paths=None):
        record = {'task_id': self.TASK_ID,
                  'developer': {'write_paths': self.WRITE_PATHS if write_paths is None else write_paths}}
        d6_binding = {'root': self.root, 'common_dir': os.path.join(self.tmp.name, 'canonical', '.git'),
                      'branch': self.BRANCH, 'task_id': self.EXECUTOR_TASK,
                      'recorded_result_head': self.initial_head}
        return task_advance._publish_task(record, d6_binding, self.EXECUTOR_TASK,
            pre_pr_gate=lambda _head, _branch: None,
            git_runner=self.git_runner, gh_runner=self.gh,
            record_result_call=self.record_result)

    def head(self):
        return self.git(['rev-parse', 'HEAD'], cwd=self.root).strip()

    def pushed_head(self):
        return self.git(['ls-remote', 'origin', f'refs/heads/{self.BRANCH}'],
                        cwd=self.root).split()[0]

    def change_file(self, text='published\n'):
        with open(os.path.join(self.root, 'allowed.txt'), 'w', encoding='utf-8') as stream:
            stream.write(text)

    def gated_store(self, write_paths=('allowed.txt', 'tools/llmtool/tests/test_task_advance.py'),
                    **changes):
        class Store:
            def __init__(self, record):
                self.record = record
                self.published = []
            def get(self, _task_id):
                return self.record
            def record_published_pr(self, task_id, pr, head):
                self.published.append((task_id, pr, head))

        return Store(base_record(
            developer={'state': 'DEVELOPER_RESPONDED', 'write_paths': list(write_paths)},
            completion={'executor_task_id': self.EXECUTOR_TASK, 'state': 'WAITING_REVIEW',
                        'evidence': {}},
            remote_test_outcomes=[], **changes))

    def advance_gated(self, store, remote_test, *, dirty=False, repair=None, github_get=None):
        head = self.head()
        binding = {'root': self.root, 'task_id': self.EXECUTOR_TASK, 'branch': self.BRANCH,
                   'common_dir': os.path.join(self.tmp.name, 'canonical', '.git'),
                   'result_head': head, 'recorded_result_head': None if dirty else head,
                   'dirty': dirty, 'state': 'DIRTY' if dirty else 'CLEAN'}
        with patch.object(task_advance, '_d6_snapshot', return_value=binding), \
             patch.object(task_advance, '_review_outcomes', return_value=[]), \
             patch.object(task_submission_cli, 'run_task_remote_test', side_effect=remote_test):
            return task_advance.advance(task_id=self.TASK_ID, store=store,
                publish_git_runner=self.git_runner, publish_gh_runner=self.gh,
                record_result_call=self.record_result, repair_review_call=repair,
                github_get=github_get or (lambda path: self.fail(f'unexpected read: {path}')))

    def assert_no_pr(self):
        self.assertFalse(self.pr_exists)
        self.assertFalse(any(args[:2] == ['pr', 'create'] for args in self.gh_calls))

    def test_pass_pushes_branch_tests_its_ref_then_opens_pr(self):
        self.change_file()
        store = self.gated_store()
        seen = []
        def remote_test(args, *, store):
            seen.append((args.sha, args.ref, args.pr, self.pushed_head(), self.pr_exists))
            store.record.setdefault('remote_test_receipts', []).append(remote_receipt(
                store.record, source_sha=args.sha, executor_task=self.EXECUTOR_TASK))
            return 0

        result = self.advance_gated(store, remote_test, dirty=True)

        head = self.head()
        self.assertNotEqual(head, self.initial_head)
        self.assertEqual(seen, [(head, f'refs/heads/{self.BRANCH}', None, head, False)])
        self.assertEqual(result, {'task_id': self.TASK_ID, 'codes': [], 'pr': 23, 'head': head})
        self.assertEqual(store.published, [(self.TASK_ID, 23, head)])
        comment = next(args for args in self.gh_calls if args[:2] == ['pr', 'comment'])
        self.assertEqual(comment[2], '23')
        self.assertIn(f'head={head}', comment[comment.index('--body') + 1])

    def test_fail_leaves_no_pr_and_next_step_repairs(self):
        self.change_file()
        store = self.gated_store()
        def remote_test(args, *, store):
            store.record.setdefault('remote_test_receipts', []).append(remote_receipt(
                store.record, source_sha=args.sha, result='FAIL', excerpt='FAIL: test_example',
                executor_task=self.EXECUTOR_TASK))
            return 0

        held = self.advance_gated(store, remote_test, dirty=True)

        self.assertEqual((held['step'], held['next'], held['codes'], held['pr']),
                         ('RUN_REMOTE_TEST', 'REPAIR_REVIEW', ['REMOTE_TEST_FAIL'], None))
        self.assertEqual(held['head'], self.pushed_head())
        self.assertEqual(held['result']['remote_test']['result'], 'FAIL')
        self.assert_no_pr()

        repairs = []
        repaired = self.advance_gated(store, lambda *_a, **_k: self.fail('no second test'),
            repair=lambda **kwargs: repairs.append(kwargs) or {'repair_task_id': 'repair-1'})

        self.assertEqual(repaired['step'], 'REPAIR_REVIEW')
        self.assertIsNone(repairs[0]['pr_number'])
        self.assertEqual(repairs[0]['remote_test_receipt']['failure_excerpt'], 'FAIL: test_example')
        self.assert_no_pr()
        self.assertEqual(store.published, [])

    def test_unavailable_or_busy_waits_then_retries_on_next_step(self):
        self.change_file()
        store = self.gated_store()
        replies = ['REMOTE_BUSY', 'SSH_UNAVAILABLE', None]
        refs = []
        def remote_test(args, *, store):
            refs.append(args.ref)
            code = replies.pop(0)
            if code is None:
                store.record.setdefault('remote_test_receipts', []).append(remote_receipt(
                    store.record, source_sha=args.sha, executor_task=self.EXECUTOR_TASK))
            else:
                print(json.dumps({'result': 'UNAVAILABLE', 'code': code}))
            return 0

        for dirty, code in ((True, 'REMOTE_BUSY'), (False, 'SSH_UNAVAILABLE')):
            with self.subTest(code=code):
                held = self.advance_gated(store, remote_test, dirty=dirty)
                self.assertEqual((held['step'], held['codes'], held['pr']),
                                 ('WAITING_REMOTE_TEST', ['REMOTE_TEST_UNAVAILABLE', code], None))
                self.assert_no_pr()

        opened = self.advance_gated(store, remote_test)

        self.assertEqual(opened['pr'], 23)
        self.assertEqual(refs, [f'refs/heads/{self.BRANCH}'] * 3)

    def test_hosted_remote_test_codes_hold_at_waiting_remote_test_and_are_not_fail(self):
        new_codes = ('HOSTED_PENDING', 'HOSTED_QUEUE_FULL', 'HOSTED_DISPATCH_UNCONFIRMED')
        for code in new_codes:
            with self.subTest(code=code):
                self.change_file(f'{code}\n')
                store = self.gated_store()
                def remote_test(args, *, store):
                    print(json.dumps({'result': 'UNAVAILABLE', 'code': code}))
                    return 0

                held = self.advance_gated(store, remote_test, dirty=True)
                self.assertEqual((held['step'], held['codes'], held['pr']),
                                 ('WAITING_REMOTE_TEST', ['REMOTE_TEST_UNAVAILABLE', code], None))
                self.assertEqual(held['next'], 'WAITING_REMOTE_TEST')
                self.assertNotIn('REMOTE_TEST_FAIL', held['codes'])
                self.assertNotEqual(held['step'], 'RUN_REMOTE_TEST')
                self.assertNotEqual(held['next'], 'REPAIR_REVIEW')
                self.assert_no_pr()

    def test_pre_pr_remote_gate_hosted_codes_hold_at_waiting_remote_test_and_are_not_fail(self):
        new_codes = ('HOSTED_PENDING', 'HOSTED_QUEUE_FULL', 'HOSTED_DISPATCH_UNCONFIRMED')
        for code in new_codes:
            with self.subTest(code=code):
                self.change_file(f'clean-{code}\n')
                store = self.gated_store()
                def remote_test(args, *, store):
                    print(json.dumps({'result': 'UNAVAILABLE', 'code': code}))
                    return 0

                held = self.advance_gated(store, remote_test, dirty=False)
                self.assertEqual((held['step'], held['codes'], held['pr']),
                                 ('WAITING_REMOTE_TEST', ['REMOTE_TEST_UNAVAILABLE', code], None))
                self.assertEqual(held['next'], 'WAITING_REMOTE_TEST')
                self.assertNotIn('REMOTE_TEST_FAIL', held['codes'])
                self.assertNotEqual(held['step'], 'RUN_REMOTE_TEST')
                self.assertNotEqual(held['next'], 'REPAIR_REVIEW')
                self.assert_no_pr()

    def test_docs_only_or_no_module_head_opens_pr_then_uses_pr_ref_step(self):
        self.change_file()
        store = self.gated_store(write_paths=['allowed.txt'])

        opened = self.advance_gated(
            store, lambda *_a, **_k: self.fail('no branch test without a module'), dirty=True)

        head = self.head()
        self.assertEqual(opened, {'task_id': self.TASK_ID, 'codes': [], 'pr': 23, 'head': head})
        refs = []
        def remote_test(args, *, store):
            refs.append((args.ref, args.pr))
            store.record['remote_test_outcomes'].append({
                'result': 'ADMISSION_DENIED', 'code': 'MODULE_NOT_ADMITTED',
                'executor_task': self.EXECUTOR_TASK, 'sha': args.sha, 'modules': []})
            return 0

        step = self.advance_gated(store, remote_test, github_get=lambda _path: {
            'number': 23, 'head': {'sha': head}, 'state': 'open', 'merged': False})

        self.assertEqual(step['step'], 'RUN_REMOTE_TEST')
        self.assertEqual(refs, [('refs/pull/23/head', 23)])

    def moved_pr_gh(self, *, close_code=0):
        """A created PR #41 whose listed and viewed head never reaches the branch tip."""
        stale, sent = 'f' * 40, {}
        def reply(args, text='', code=0):
            return subprocess.CompletedProcess(['gh', *args], code, text.encode(), b'')
        def gh(_root, args):
            args = list(args)
            self.gh_calls.append(args)
            if args[:2] == ['pr', 'list']:
                return reply(args, json.dumps(
                    [{'number': 41, 'headRefOid': stale}] if self.pr_exists else []))
            if args[:2] == ['pr', 'create']:
                self.pr_exists = True
                sent.update(title=args[args.index('--title') + 1],
                            body=args[args.index('--body') + 1])
                return reply(args, 'https://github.com/owner/repo/pull/41\n')
            if args[:2] == ['pr', 'view']:
                return reply(args, json.dumps({
                    'number': 41, 'url': 'https://github.com/owner/repo/pull/41',
                    'headRefName': self.BRANCH, 'headRefOid': stale,
                    'isCrossRepository': False, 'state': 'OPEN',
                    'author': {'login': 'ai-bot'}, 'title': sent['title'],
                    'body': sent['body']}))
            if args[:2] == ['repo', 'view']:
                return reply(args, json.dumps({'nameWithOwner': 'owner/repo'}))
            if args[:2] == ['api', 'user']:
                return reply(args, json.dumps({'login': 'ai-bot'}))
            if args[:2] == ['pr', 'close']:
                return reply(args, '', close_code)
            if args[:2] == ['pr', 'comment']:
                return reply(args)
            raise AssertionError(f'unexpected fake gh command: {args}')
        return gh

    def test_advance_returns_and_saves_publish_operation_after_create(self):
        fields = {'task_id', 'repository', 'branch', 'pr', 'creation_url', 'expected_head',
                  'observed_head', 'recheck', 'close_attempted', 'record_comment',
                  'outcome', 'next_action'}
        for close_code, code, outcome, saves in (
                (0, 'PUBLISH_HEAD_MOVED', 'CLOSED', True),
                (1, 'PUBLISH_MOVED_PR_OPEN', 'CLOSE_FAILED_LEFT_OPEN', True),
                (0, 'PUBLISH_HEAD_MOVED', 'CLOSED', False)):
            with self.subTest(close_code=close_code, saves=saves):
                self.setUp()
                self.change_file()
                store = self.gated_store(write_paths=['allowed.txt'])
                saved = []
                if saves:
                    store.record_publish_operation = (
                        lambda task_id, operation: saved.append((task_id, operation)))
                self.gh = self.moved_pr_gh(close_code=close_code)
                with patch.object(task_advance, '_publish_pause'):
                    result = self.advance_gated(
                        store, lambda *_a, **_k: self.fail('no remote test'), dirty=True)
                operation = result['publish_operation']
                self.assertEqual(set(operation), fields)
                self.assertEqual((operation['pr'], operation['recheck'], operation['outcome'],
                                  operation['close_attempted'], operation['record_comment']),
                                 (41, 'PASS', outcome, True, 'POSTED'))
                self.assertNotIn('pr', result)
                self.assertNotIn('dispatch_audit', result)
                self.assertEqual(store.published, [])
                if saves:
                    self.assertEqual(result['codes'], [code])
                    self.assertEqual(saved, [(self.TASK_ID, operation)])
                else:
                    self.assertEqual(result['codes'], [code, 'PUBLISH_OPERATION_RECORD_FAILED'])
                if code == 'PUBLISH_MOVED_PR_OPEN':
                    self.assertEqual(result['open_pr']['number'], 41)
                    self.assertIn('PUBLISH_MOVED_PR_OPEN', result['open_pr']['error'])
                    self.assertIn('PR #41', result['open_pr']['error'])
                    self.assertIn('still open', result['open_pr']['error'])
                else:
                    self.assertNotIn('open_pr', result)
                self.assertEqual([args for args in self.gh_calls if args[:2] == ['pr', 'close']],
                                 [['pr', 'close', '41', '--repo', 'owner/repo']])
                self.assertTrue(all('--delete-branch' not in args for args in self.gh_calls))
                self.assertEqual(self.pushed_head(), self.head())

    def test_head_moved_during_remote_test_refuses_pr(self):
        self.change_file()
        store = self.gated_store()
        def remote_test(args, *, store):
            store.record.setdefault('remote_test_receipts', []).append(remote_receipt(
                store.record, source_sha=args.sha, executor_task=self.EXECUTOR_TASK))
            self.git(['commit', '--allow-empty', '-m', 'moved'], cwd=self.root)
            self.git(['push', 'origin', f'HEAD:refs/heads/{self.BRANCH}'], cwd=self.root)
            return 0

        result = self.advance_gated(store, remote_test, dirty=True)

        self.assertEqual(result, {'task_id': self.TASK_ID, 'codes': ['PUBLISH_HEAD_MOVED']})
        self.assert_no_pr()
        self.assertEqual(store.published, [])

    def test_pushed_branch_moved_alone_is_refused(self):
        tested = self.head()
        self.git(['commit', '--allow-empty', '-m', 'moved'], cwd=self.root)
        self.git(['push', 'origin', f'HEAD:refs/heads/{self.BRANCH}'], cwd=self.root)
        self.git(['reset', '--hard', tested], cwd=self.root)
        with self.assertRaises(task_advance._PublishRefusal) as caught:
            task_advance._require_published_head(self.git_runner, self.root, self.BRANCH, tested)
        self.assertEqual(caught.exception.code, 'PUBLISH_HEAD_MOVED')

    def test_pass_for_older_head_does_not_clear_new_head(self):
        self.change_file()
        store = self.gated_store()
        store.record['remote_test_receipts'] = [remote_receipt(
            store.record, source_sha=self.initial_head, executor_task=self.EXECUTOR_TASK)]
        tested = []
        def remote_test(args, *, store):
            tested.append(args.sha)
            print(json.dumps({'result': 'UNAVAILABLE', 'code': 'REMOTE_BUSY'}))
            return 0

        held = self.advance_gated(store, remote_test, dirty=True)

        self.assertEqual(tested, [self.head()])
        self.assertEqual(held['step'], 'WAITING_REMOTE_TEST')
        self.assert_no_pr()

    def test_branch_moved_before_ec2_fetch_needs_attention_without_pr(self):
        self.change_file()
        store = self.gated_store()
        def remote_test(args, *, store):
            store.record.setdefault('remote_test_receipts', []).append(remote_receipt(
                store.record, source_sha=args.sha, result='SHA_MISMATCH',
                executor_task=self.EXECUTOR_TASK))
            return 0

        held = self.advance_gated(store, remote_test, dirty=True)

        self.assertEqual((held['step'], held['codes'], held['pr']),
                         ('NEEDS_ATTENTION', ['REMOTE_TEST_SHA_MISMATCH'], None))
        self.assert_no_pr()

    def test_remote_test_refusal_is_named_and_opens_no_pr(self):
        self.change_file()
        store = self.gated_store()
        def remote_test(_args, *, store):
            print(json.dumps({'result': 'ADMISSION_DENIED', 'code': 'RECEIPT_HEAD_MISMATCH'}))
            return 0

        held = self.advance_gated(store, remote_test, dirty=True)

        self.assertEqual((held['step'], held['codes'], held['pr']),
                         ('REMOTE_TEST_REFUSED',
                          ['PUBLISH_REMOTE_TEST_REFUSED', 'RECEIPT_HEAD_MISMATCH'], None))
        self.assert_no_pr()

    def test_remote_test_error_opens_no_pr(self):
        self.change_file()
        store = self.gated_store()
        def remote_test(_args, *, store):
            error = RuntimeError('adapter failed')
            error.remote_test_code = 'REMOTE_TEST_ADAPTER_FAILED'
            raise error

        held = self.advance_gated(store, remote_test, dirty=True)

        self.assertEqual(held['codes'],
                         ['PUBLISH_REMOTE_TEST_REFUSED', 'REMOTE_TEST_ADAPTER_FAILED'])
        self.assert_no_pr()

    def test_open_pr_skips_branch_gate_and_keeps_pr_ref_step(self):
        self.pr_exists = True
        self.change_file()
        store = self.gated_store()

        result = self.advance_gated(
            store, lambda *_a, **_k: self.fail('an open PR uses the PR-ref step'), dirty=True)

        self.assertEqual(result, {'task_id': self.TASK_ID, 'codes': [], 'pr': 23,
                                  'head': self.head()})
        self.assertEqual(store.published, [])

    def test_decide_without_pr_follows_exact_head_remote_status(self):
        def record(result, **receipt):
            return base_record(remote_test_outcomes=[], remote_test_receipts=[
                remote_receipt(base_record(), result=result, **receipt)])

        self.assertEqual(task_advance.decide(
            base_record(remote_test_outcomes=[]), d6(), None, {}), 'PUBLISH')
        self.assertEqual(task_advance.decide(record('PASS'), d6(), None, {}), 'PUBLISH')
        self.assertEqual(task_advance.decide(
            record('FAIL', excerpt='failure'), d6(), None, {}), 'REPAIR_REVIEW')
        self.assertEqual(task_advance.decide(record('SHA_MISMATCH'), d6(), None, {}),
                         'NEEDS_ATTENTION')
        self.assertEqual(task_advance.decide(record('FAIL', excerpt='failure'), d6(), None, {
            'repair_claims': [{'head_sha': HEAD, 'state': 'DISPATCHED',
                               'repair_state': 'RUNNING'}]}), 'WAITING_REPAIR')
        self.assertEqual(task_advance.decide(
            record('FAIL', excerpt='failure', source_sha='b' * 40), d6(), None, {}), 'PUBLISH')

    def test_publish_commits_records_pushes_and_returns_matching_pr_head(self):
        with open(os.path.join(self.root, 'allowed.txt'), 'w', encoding='utf-8') as stream:
            stream.write('published\n')
        result = self.publish()
        self.assertEqual(result['pr'], 23)
        self.assertEqual(result['head'], self.git(['rev-parse', 'HEAD'], cwd=self.root).strip())
        self.assertEqual(self.recorded[0]['task_id'], self.EXECUTOR_TASK)
        self.assertEqual(self.git(['show', '-s', '--format=%s', 'HEAD'], cwd=self.root).strip(),
                         f'llmtool Task {self.TASK_ID}')
        self.assertEqual(self.git(['ls-remote', 'origin', f'refs/heads/{self.BRANCH}'],
                                  cwd=self.root).split()[0], result['head'])

    def test_second_publish_is_noop_and_reports_existing_pr_head(self):
        with open(os.path.join(self.root, 'allowed.txt'), 'w', encoding='utf-8') as stream:
            stream.write('published\n')
        first = self.publish()
        d6_binding = {'root': self.root, 'common_dir': os.path.join(self.tmp.name, 'canonical', '.git'),
                      'branch': self.BRANCH, 'task_id': self.EXECUTOR_TASK,
                      'recorded_result_head': first['head']}
        second = task_advance._publish_task(
            {'task_id': self.TASK_ID, 'developer': {'write_paths': self.WRITE_PATHS}},
            d6_binding, self.EXECUTOR_TASK, pre_pr_gate=lambda _head, _branch: None,
            git_runner=self.git_runner, gh_runner=self.gh,
            record_result_call=self.record_result)
        self.assertEqual(second, first)
        self.assertEqual(self.git(['rev-parse', 'HEAD'], cwd=self.root).strip(), first['head'])
        self.assertEqual(len(self.recorded), 1)

    def test_out_of_scope_change_is_refused_without_commit(self):
        with open(os.path.join(self.root, 'outside.txt'), 'w', encoding='utf-8') as stream:
            stream.write('outside\n')
        with self.assertRaises(task_advance._PublishRefusal) as caught:
            self.publish()
        self.assertEqual(caught.exception.code, 'PUBLISH_OUT_OF_SCOPE')
        self.assertEqual(self.git(['rev-parse', 'HEAD'], cwd=self.root).strip(), self.initial_head)

    def test_staged_rename_from_outside_scope_is_refused_without_commit(self):
        os.mkdir(os.path.join(self.root, 'outside'))
        with open(os.path.join(self.root, 'outside', 'source.py'), 'w', encoding='utf-8') as stream:
            stream.write('value = 1\n')
        self.git(['add', '--', 'outside/source.py'], cwd=self.root)
        self.git(['commit', '-m', 'add outside source'], cwd=self.root)
        self.initial_head = self.git(['rev-parse', 'HEAD'], cwd=self.root).strip()
        os.mkdir(os.path.join(self.root, 'inside'))
        self.git(['mv', 'outside/source.py', 'inside/source.py'], cwd=self.root)

        with self.assertRaises(task_advance._PublishRefusal) as caught:
            self.publish(write_paths=['inside/source.py'])

        self.assertEqual(caught.exception.code, 'PUBLISH_OUT_OF_SCOPE')
        self.assertEqual(self.git(['rev-parse', 'HEAD'], cwd=self.root).strip(), self.initial_head)

    def test_main_master_and_default_branches_are_refused(self):
        for branch in ('main', 'master', 'default'):
            with self.subTest(branch=branch):
                current = self.git(['branch', '--show-current'], cwd=self.root).strip()
                self.git(['branch', '-m', branch], cwd=self.root)
                d6_binding = {'root': self.root,
                    'common_dir': os.path.join(self.tmp.name, 'canonical', '.git'),
                    'branch': branch, 'task_id': self.EXECUTOR_TASK,
                    'recorded_result_head': self.initial_head}
                with self.assertRaises(task_advance._PublishRefusal) as caught:
                    task_advance._publish_task(
                        {'task_id': self.TASK_ID,
                         'developer': {'write_paths': self.WRITE_PATHS}},
                        d6_binding, self.EXECUTOR_TASK,
                        pre_pr_gate=lambda _head, _branch: None, git_runner=self.git_runner,
                        gh_runner=self.gh, record_result_call=self.record_result)
                self.assertEqual(caught.exception.code, 'PUBLISH_PROTECTED_BRANCH')
                self.assertEqual(self.git(['rev-parse', 'HEAD'], cwd=self.root).strip(),
                                 self.initial_head)
                self.git(['branch', '-m', current], cwd=self.root)

    def test_ignored_python_cache_is_refused_without_commit(self):
        os.mkdir(os.path.join(self.root, '__pycache__'))
        with open(os.path.join(self.root, '__pycache__', 'stray.pyc'), 'wb') as stream:
            stream.write(b'ignored')
        self.git(['config', 'core.excludesfile', os.path.join(self.tmp.name, 'ignore')], cwd=self.root)
        with open(os.path.join(self.tmp.name, 'ignore'), 'w', encoding='utf-8') as stream:
            stream.write('__pycache__/\n')
        with self.assertRaises(task_advance._PublishRefusal) as caught:
            self.publish()
        self.assertEqual(caught.exception.code, 'PUBLISH_IGNORED_STRAY')
        self.assertEqual(self.git(['rev-parse', 'HEAD'], cwd=self.root).strip(), self.initial_head)

    def test_ignored_cache_under_bootstrap_venv_does_not_block_publish(self):
        cache = os.path.join(self.root, 'tools', 'llmtool', '.venv',
                             'lib', 'python3', 'site-packages', 'pkg', '__pycache__')
        os.makedirs(cache)
        with open(os.path.join(cache, 'module.pyc'), 'wb') as stream:
            stream.write(b'ignored')
        self.git(['config', 'core.excludesfile', os.path.join(self.tmp.name, 'ignore')], cwd=self.root)
        with open(os.path.join(self.tmp.name, 'ignore'), 'w', encoding='utf-8') as stream:
            stream.write('tools/llmtool/.venv/\n')
        with open(os.path.join(self.root, 'allowed.txt'), 'w', encoding='utf-8') as stream:
            stream.write('published with ignored venv cache\n')

        result = self.publish()

        self.assertEqual(result['head'], self.git(['rev-parse', 'HEAD'], cwd=self.root).strip())
        self.assertNotEqual(result['head'], self.initial_head)

    def test_non_fast_forward_remote_is_refused_without_commit(self):
        peer = os.path.join(self.tmp.name, 'peer')
        self.git(['clone', '--branch', self.BRANCH, self.bare, peer], cwd=self.tmp.name)
        self.git(['config', 'user.name', 'Peer'], cwd=peer)
        self.git(['config', 'user.email', 'peer@example.invalid'], cwd=peer)
        with open(os.path.join(peer, 'allowed.txt'), 'w', encoding='utf-8') as stream:
            stream.write('remote moved\n')
        self.git(['add', '--', 'allowed.txt'], cwd=peer)
        self.git(['commit', '-m', 'remote move'], cwd=peer)
        self.git(['push', 'origin', self.BRANCH], cwd=peer)
        with open(os.path.join(self.root, 'allowed.txt'), 'w', encoding='utf-8') as stream:
            stream.write('local change\n')
        with self.assertRaises(task_advance._PublishRefusal) as caught:
            self.publish()
        self.assertEqual(caught.exception.code, 'PUBLISH_NON_FAST_FORWARD')
        self.assertEqual(self.git(['rev-parse', 'HEAD'], cwd=self.root).strip(), self.initial_head)

    def move_main(self, path, text):
        """Advance origin/main from a separate clone and return the new main SHA."""
        peer = os.path.join(self.tmp.name, 'main-peer')
        self.git(['clone', '--branch', 'main', self.bare, peer], cwd=self.tmp.name)
        self.git(['config', 'user.name', 'Main Peer'], cwd=peer)
        self.git(['config', 'user.email', 'main-peer@example.invalid'], cwd=peer)
        with open(os.path.join(peer, path), 'w', encoding='utf-8') as stream:
            stream.write(text)
        self.git(['add', '--', path], cwd=peer)
        self.git(['commit', '-m', 'main moved'], cwd=peer)
        self.git(['push', 'origin', 'HEAD:refs/heads/main'], cwd=peer)
        return self.git(['rev-parse', 'HEAD'], cwd=peer).strip()

    def test_real_moved_main_is_merged_and_the_merge_is_pushed(self):
        main_head = self.move_main('main-file.txt', 'main\n')
        self.change_file()

        result = self.publish()

        head = self.head()
        self.assertEqual(result, {'pr': 23, 'head': head})
        self.assertEqual(self.pushed_head(), head)
        self.assertEqual(self.git(['rev-parse', 'HEAD^2'], cwd=self.root).strip(), main_head)
        self.assertEqual(self.git(['show', 'HEAD^1:allowed.txt'], cwd=self.root), 'published\n')
        self.assertEqual(self.git(['show', 'HEAD:main-file.txt'], cwd=self.root), 'main\n')
        self.assertEqual(len(self.recorded), 1)

    def test_real_merge_conflict_aborts_and_pushes_nothing(self):
        self.move_main('allowed.txt', 'main\n')
        self.change_file()

        with self.assertRaises(task_advance._PublishRefusal) as caught:
            self.publish()

        self.assertEqual(caught.exception.code, 'PUBLISH_MERGE_CONFLICT')
        self.assertNotEqual(self.git_runner(
            self.root, ['rev-parse', '-q', '--verify', 'MERGE_HEAD']).returncode, 0)
        self.assertEqual(self.git(['status', '--porcelain'], cwd=self.root), '')
        self.assertEqual(self.pushed_head(), self.initial_head)
        self.assertEqual(self.recorded, [])
        self.assertEqual(self.gh_calls, [])


class TaskAdvancePublishLookupTests(unittest.TestCase):
    def setUp(self):
        self.record = base_record()
        self.record['remote_test_outcomes'] = []
        self.snapshot = dict(d6(), branch='feature/task', dirty=False)
        self.gh_calls = []

    def _gh(self, _root, args):
        self.gh_calls.append(list(args))
        if args[:2] == ['pr', 'list']:
            return subprocess.CompletedProcess(['gh', *args], 0,
                json.dumps([{'number': 41, 'headRefOid': HEAD}]).encode(), b'')
        raise AssertionError('unexpected gh call')

    def _advance(self, *, pr_number=None, gh=None):
        store = type('Store', (), {'get': lambda _self, _task_id: self.record})()
        return task_advance.advance(task_id=self.record['task_id'], store=store,
            pr_number=pr_number, publish_gh_runner=gh or self._gh,
            github_get=lambda path: {'number': 41 if path == 'pulls/41' else 17,
                'head': {'sha': HEAD}, 'state': 'open', 'merged': False})

    def test_clean_recorded_d6_looks_up_existing_pr_and_advances_remote_test(self):
        with patch.object(task_advance, '_d6_snapshot', return_value=self.snapshot), \
             patch.object(task_advance.task_submission_cli, 'run_task_remote_test', return_value=0):
            result = self._advance()
        self.assertEqual(result['step'], 'RUN_REMOTE_TEST')
        self.assertEqual(self.gh_calls, [[
            'pr', 'list', '--head', 'feature/task', '--state', 'all',
            '--json', 'number,headRefOid']])

    def test_explicit_pr_skips_lookup(self):
        with patch.object(task_advance, '_d6_snapshot', return_value=self.snapshot), \
             patch.object(task_advance, '_publish_run_gh', side_effect=AssertionError('lookup')):
            result = self._advance(pr_number=17)
        self.assertEqual(result['step'], 'RUN_REMOTE_TEST')
        self.assertEqual(self.gh_calls, [])

    def test_lookup_failure_leaves_publish_selected(self):
        def failed(_root, args):
            self.gh_calls.append(list(args))
            return subprocess.CompletedProcess(['gh', *args], 1, b'', b'failed')
        with patch.object(task_advance, '_d6_snapshot', return_value=self.snapshot), \
             patch.object(task_advance, '_publish_task', return_value={'pr': 42, 'head': HEAD}) as publish:
            result = self._advance(gh=failed)
        self.assertEqual(result['pr'], 42)
        publish.assert_called_once()

    def test_publication_title_and_body_from_pm_record(self):
        self.record.update(response=json.dumps({'summary': 'Repair task advance\nextra'}),
            pm_assignment={'goal': 'Find the published PR and prepare review.',
                           'acceptance': ['Find exactly one PR at this head.', 'Keep explicit PR.']})
        title, body = task_advance._publication_text(
            self.record, 'executor-1', 'feature/task')
        self.assertEqual(title, 'Repair task advance extra')
        self.assertEqual(body, '\n'.join([
            'Repair task advance\nextra', '',
            'Find the published PR and prepare review.', '', 'Acceptance:',
            '- Find exactly one PR at this head.', '- Keep explicit PR.', '',
            f'Task ID: {self.record["task_id"]}', 'D6 executor: executor-1',
            'Branch: feature/task']))
        long_record = dict(self.record, response=json.dumps({'summary': 's' * 130}))
        long_title, _ = task_advance._publication_text(
            long_record, 'executor-1', 'feature/task')
        self.assertEqual(long_title, 's' * 120)

    def test_unreadable_response_uses_legacy_publication_text(self):
        self.record.update(response='not json', pm_assignment={'goal': 'goal'})
        self.assertEqual(task_advance._publication_text(
            self.record, 'executor-1', 'feature/task'),
            (f'llmtool Task {self.record["task_id"]}',
             f'Automated publication for llmtool Task {self.record["task_id"]}.'))

    def test_publication_title_uses_summary_when_assignment_is_unreadable(self):
        for assignment in (None, {'goal': '', 'acceptance': ['acceptance']}):
            with self.subTest(assignment=assignment):
                record = dict(self.record,
                    response=json.dumps({'summary': 'Keep this readable title'}),
                    pm_assignment=assignment)
                self.assertEqual(task_advance._publication_text(
                    record, 'executor-1', 'feature/task'),
                    ('Keep this readable title',
                     f'Automated publication for llmtool Task {record["task_id"]}.'))

    def test_create_pr_uses_main_base_and_task_text(self):
        calls = []
        def gh(_root, args):
            calls.append(list(args))
            if args[:2] == ['pr', 'list']:
                pulls = [] if len(calls) == 1 else [{'number': 41, 'headRefOid': HEAD}]
                return subprocess.CompletedProcess(['gh', *args], 0,
                    json.dumps(pulls).encode(), b'')
            if args[:2] == ['pr', 'create']:
                return subprocess.CompletedProcess(['gh', *args], 0, b'', b'')
            raise AssertionError('unexpected gh call')
        number = task_advance._publish_pr(gh, '/d6', 'feature/task',
            self.record['task_id'], HEAD, 'Task summary', 'Task body')
        self.assertEqual(number, 41)
        create = next(args for args in calls if args[:2] == ['pr', 'create'])
        self.assertEqual(create, ['pr', 'create', '--head', 'feature/task',
            '--base', 'main', '--title', 'Task summary', '--body', 'Task body'])

    def test_stale_open_pr_head_catches_up_on_second_read(self):
        old_head = 'b' * 40
        pulls = [
            [{'number': 41, 'headRefOid': old_head}],
            [{'number': 41, 'headRefOid': HEAD}],
        ]

        def gh(_root, args):
            self.gh_calls.append(list(args))
            self.assertEqual(args[:2], ['pr', 'list'])
            return subprocess.CompletedProcess(['gh', *args], 0,
                json.dumps(pulls.pop(0)).encode(), b'')

        with patch.object(task_advance, '_publish_pause') as pause:
            number = task_advance._publish_pr(gh, '/d6', 'feature/task',
                self.record['task_id'], HEAD, 'Task summary', 'Task body')
        self.assertEqual(number, 41)
        self.assertEqual(len(self.gh_calls), 2)
        pause.assert_called_once_with(2)

    def test_stale_open_pr_head_fails_after_four_reads_without_sleep(self):
        old_head = 'b' * 40

        def gh(_root, args):
            self.gh_calls.append(list(args))
            self.assertEqual(args[:2], ['pr', 'list'])
            return subprocess.CompletedProcess(['gh', *args], 0,
                json.dumps([{'number': 41, 'headRefOid': old_head}]).encode(), b'')

        with patch.object(task_advance, '_publish_pause') as pause:
            with self.assertRaises(task_advance._PublishRefusal) as caught:
                task_advance._publish_pr(gh, '/d6', 'feature/task',
                    self.record['task_id'], HEAD, 'Task summary', 'Task body')
        self.assertEqual(caught.exception.code, 'PUBLISH_GITHUB_FAILED')
        self.assertEqual(len(self.gh_calls), 4)
        self.assertEqual(pause.call_args_list, [call(2)] * 3)

    def _fake_publish(self, *, main_state='contained', fetch_failed=False):
        initial_head, committed_head, merged_head = 'a' * 40, 'b' * 40, 'c' * 40
        state = {'head': initial_head}
        events = []
        git_calls = []

        def result(args, code=0, output=b''):
            return subprocess.CompletedProcess(['git', *args], code, output, b'')

        def git(_root, args):
            args = list(args)
            git_calls.append(args)
            events.append(('git', tuple(args)))
            if args == ['rev-parse', '--show-toplevel']:
                return result(args, output=b'/fake/d6\n')
            if args == ['branch', '--show-current']:
                return result(args, output=b'feature/task\n')
            if args in (['ls-files', '--others', '--ignored', '--exclude-standard', '-z'],
                        ['ls-files', '--others', '--exclude-standard', '-z']):
                return result(args)
            if args == ['diff', '--no-renames', '--name-only', '-z', 'HEAD']:
                return result(args, output=b'allowed.txt\0')
            if args == ['ls-remote', '--heads', 'origin', 'refs/heads/feature/task']:
                return result(args, output=f'{initial_head}\trefs/heads/feature/task\n'.encode())
            if args == ['rev-parse', '--verify', 'HEAD']:
                return result(args, output=(state['head'] + '\n').encode())
            if args == ['merge-base', '--is-ancestor', initial_head, 'HEAD']:
                return result(args)
            if args == ['merge-base', '--is-ancestor', 'origin/main', 'HEAD']:
                return result(args, 0 if main_state == 'contained' else 1)
            if args == ['add', '--', 'allowed.txt']:
                return result(args)
            if args == ['diff', '--cached', '--no-renames', '--name-only', '-z', 'HEAD']:
                return result(args, output=b'allowed.txt\0')
            if args[:1] == ['commit']:
                state['head'] = committed_head
                return result(args)
            if args == ['fetch', 'origin', 'main']:
                return result(args, 1 if fetch_failed else 0)
            if args == ['merge', '--no-edit', 'origin/main']:
                if main_state == 'conflict':
                    return result(args, 1)
                state['head'] = merged_head
                return result(args)
            if args == ['ls-files', '--unmerged', '-z']:
                return result(args, output=b'100644 conflict\0' if main_state == 'conflict' else b'')
            if args == ['merge', '--abort']:
                events.append(('abort', tuple(args)))
                return result(args)
            if args == ['push', 'origin', 'HEAD:refs/heads/feature/task']:
                events.append(('push', state['head']))
                return result(args)
            raise AssertionError(f'unexpected git call: {args}')

        def record_result(**_kwargs):
            events.append(('record', state['head']))
            return state['head']

        def gh(_root, args):
            if args[:2] == ['pr', 'list']:
                return subprocess.CompletedProcess(['gh', *args], 0,
                    json.dumps([{'number': 23, 'headRefOid': state['head']}]).encode(), b'')
            raise AssertionError(f'unexpected gh call: {args}')

        record = {'task_id': '1' * 32, 'developer': {'write_paths': ['allowed.txt']}}
        binding = {'root': '/fake/d6', 'common_dir': '/fake/canonical/.git',
                   'branch': 'feature/task', 'task_id': '3' * 32,
                   'recorded_result_head': initial_head}
        try:
            value = task_advance._publish_task(record, binding, '3' * 32,
                pre_pr_gate=lambda _head, _branch: None,
                git_runner=git, gh_runner=gh, record_result_call=record_result)
        except task_advance._PublishRefusal as exc:
            value = exc.code
        return value, events, git_calls, committed_head, merged_head

    def test_main_already_contained_does_not_merge(self):
        result, events, git_calls, committed, _merged = self._fake_publish()
        self.assertEqual(result['head'], committed)
        self.assertIn(['merge-base', '--is-ancestor', 'origin/main', 'HEAD'], git_calls)
        self.assertNotIn(['merge', '--no-edit', 'origin/main'], git_calls)

    def test_moved_main_merges_then_records_then_pushes_new_head(self):
        result, events, _git_calls, _committed, merged = self._fake_publish(main_state='moved')
        self.assertEqual(result['head'], merged)
        merge = events.index(('git', ('merge', '--no-edit', 'origin/main')))
        record = events.index(('record', merged))
        push = events.index(('push', merged))
        self.assertLess(merge, record)
        self.assertLess(record, push)

    def test_merge_conflict_aborts_and_never_pushes(self):
        result, events, _git_calls, _committed, _merged = self._fake_publish(main_state='conflict')
        self.assertEqual(result, 'PUBLISH_MERGE_CONFLICT')
        self.assertIn(('abort', ('merge', '--abort')), events)
        self.assertFalse(any(event[0] == 'push' for event in events))

    def test_fetch_failure_refuses_without_push(self):
        result, events, _git_calls, _committed, _merged = self._fake_publish(fetch_failed=True)
        self.assertEqual(result, 'PUBLISH_COMMAND_FAILED')
        self.assertFalse(any(event[0] == 'push' for event in events))



class RetiredD6MergeClosureTests(unittest.TestCase):
    """#547: a merged Task closes after its D6 is retired, via the real _d6_snapshot."""

    MERGED_PR = {'number': 17, 'merged': True, 'head': {'sha': HEAD}, 'merge_commit_sha': 'd' * 40}
    TRAIN = {'number': 541, 'merged': True, 'head': {'sha': 'c' * 40}, 'merge_commit_sha': 'e' * 40}

    def setUp(self):
        import os
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # A git common dir with no llmtool-worktrees registry: the D6 is retired.
        self.common = os.path.realpath(self.tmp.name) + '/.git'
        os.makedirs(self.common)

    def ready(self, outcome='REVIEW_PASS'):
        record = base_record(
            review_dispatch_claim=[{'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'COMPLETED'}],
            completion={'executor_task_id': 'executor-1', 'state': 'PM_EVIDENCE_READY',
                        'evidence': {'common_dir': self.common, 'result': {'result_head': HEAD},
                                     'review_pr_ci': {'stage': 'READY'}}})
        record['completion_events'] = [
            {'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1, 'outcome': outcome}]
        return record

    def advance(self, record, prs, *, compare='ahead', integration_pr=None, registry_code=None):
        from llmtool import task_completion
        saved = []

        def get(path):
            if path.startswith('pulls/'):
                return prs.get(int(path.split('/')[1]))
            if path.startswith('compare/'):
                return {'status': compare}
            return []

        store = type('S', (), {'get': lambda self, _id: record})()
        if registry_code is None:
            snapshot = task_advance._d6_snapshot(record, 'executor-1')
            result = task_advance.advance(
                task_id=record['task_id'], store=store, pr_number=17, integration_pr=integration_pr,
                github_get=get, record_github_event_call=lambda *a: saved.append(a))
        else:
            with patch.object(task_completion, 'read_d6_registration',
                              side_effect=lambda *a, **k: task_completion._stop(registry_code)):
                snapshot = task_advance._d6_snapshot(record, 'executor-1')
                result = task_advance.advance(
                    task_id=record['task_id'], store=store, pr_number=17,
                    integration_pr=integration_pr, github_get=get,
                    record_github_event_call=lambda *a: saved.append(a))
        return snapshot, result, saved

    def test_merged_pr_closes_with_missing_registry(self):
        snapshot, result, saved = self.advance(self.ready(), {17: self.MERGED_PR})
        self.assertEqual(snapshot['state'], 'D6_BINDING_UNAVAILABLE')
        self.assertEqual(result['step'], 'CLOSED')
        self.assertEqual(saved[0][4], 17)
        self.assertEqual(saved[0][-2:], ('MERGED', 'd' * 40))

    def test_train_merge_closes_with_invalid_registry(self):
        with patch.object(task_advance, '_integration_delta_verified',
                          return_value=True) as delta:
            snapshot, result, saved = self.advance(
                self.ready(), {17: PR, 541: self.TRAIN}, integration_pr=541,
                registry_code='D6_REGISTRY_INVALID')
        self.assertEqual(snapshot['code'], 'D6_REGISTRY_INVALID')
        self.assertEqual(result['step'], 'CLOSED')
        self.assertEqual(delta.call_args.args[1:5], (HEAD, 'c' * 40, 'e' * 40, 541))

    def test_train_merge_with_unverified_delta_does_not_close(self):
        with patch.object(task_advance, '_integration_delta_verified', return_value=False):
            _, result, saved = self.advance(
                self.ready(), {17: PR, 541: self.TRAIN}, integration_pr=541,
                registry_code='D6_REGISTRY_INVALID')
        self.assertNotEqual(result['step'], 'CLOSED')
        self.assertEqual(saved, [])

    def test_without_merge_proof_the_retired_d6_still_refuses(self):
        other_head = dict(self.MERGED_PR, head={'sha': 'f' * 40})
        cases = (
            ('not merged', self.ready(), {17: PR}, 'ahead', None),
            ('train diverged', self.ready(), {17: PR, 541: self.TRAIN}, 'diverged', 541),
            ('merged at another head', self.ready(), {17: other_head}, 'ahead', None),
            ('review failed', self.ready('REVIEW_FAIL'), {17: self.MERGED_PR}, 'ahead', None),
        )
        for name, record, prs, compare, train in cases:
            with self.subTest(name):
                _, result, saved = self.advance(record, prs, compare=compare, integration_pr=train,
                                                registry_code='D6_REGISTRY_INVALID')
                self.assertEqual(result['step'], 'D6_BINDING_UNAVAILABLE')
                self.assertEqual(saved, [])

    def test_opt_in_merge_claims_once_and_posts_provenance(self):
        review = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'COMPLETED',
                  'review_result': {'verdict': 'PASS'}}
        event = {'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}
        record = {'task_id': '1' * 32, 'record_sha256': 'r' * 64,
                  'review_dispatch_claim': [review]}
        class Store:
            def __init__(self):
                self.attempt = None
            def compare_and_update(self, _task_id, *, expected, merge_attempt):
                if self.attempt != expected.get('merge_attempt'):
                    raise RuntimeError('claim race')
                self.attempt = dict(merge_attempt)
        store = Store()
        calls = []
        pr = {'number': 17, 'head': {'sha': HEAD}, 'mergeable': True,
              'labels': [{'name': 'mac-ci'}]}
        @with_current_main
        def github_get(path):
            if path.startswith('commits/'):
                return {'total_count': 2, 'check_runs': [
                    {'head_sha': HEAD, 'name': 'check (Linux 3.14)', 'status': 'completed',
                     'conclusion': 'success'},
                    {'head_sha': HEAD, 'name': 'check (macOS 3.13)', 'status': 'completed',
                     'conclusion': 'success'}]}
            if path.startswith('pulls/') and path.endswith('/files?per_page=100'):
                return [{'filename': 'src/module.py'}]
            if path.startswith('pulls/'):
                return pr
            return [{'filename': 'src/module.py'}]
        def gh_runner(_root, args):
            calls.append(args)
            if args[1] == 'merge':
                return type('R', (), {'returncode': 0})()
            return type('R', (), {'returncode': 0})()
        result = task_advance._merge_once(store=store, record=record, number=17,
            head=HEAD, pr=pr, events=[event], github_get=github_get,
            gh_runner=gh_runner, root='/repo')
        self.assertEqual(result['step'], 'MERGED')
        self.assertEqual(store.attempt['state'], 'MERGED')
        self.assertEqual(calls[0], ['pr', 'merge', '17', '--squash',
                                    '--match-head-commit', HEAD])
        self.assertEqual(calls[1][:3], ['pr', 'comment', '17'])
        replay = task_advance._merge_once(store=store,
            record=dict(record, merge_attempt=store.attempt), number=17,
            head=HEAD, pr=pr, events=[event], github_get=github_get,
            gh_runner=gh_runner, root='/repo')
        self.assertEqual(replay['codes'], ['MERGE_ALREADY_CLAIMED'])
        self.assertEqual(len(calls), 2)

    def test_merge_adds_mac_ci_label_once_before_waiting_for_ci(self):
        review = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'COMPLETED',
                  'review_result': {'verdict': 'PASS'}}
        event = {'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}
        linux_only_checks = {'total_count': 1, 'check_runs': [
            {'head_sha': HEAD, 'name': 'check (Linux 3.14)', 'status': 'completed',
             'conclusion': 'success'}]}
        green_checks = {'total_count': 2, 'check_runs': [
            {'head_sha': HEAD, 'name': 'check (Linux 3.14)', 'status': 'completed',
             'conclusion': 'success'},
            {'head_sha': HEAD, 'name': 'check (macOS 3.13)', 'status': 'completed',
             'conclusion': 'success'}]}
        skipped_macos_checks = {'total_count': 2, 'check_runs': [
            {'head_sha': HEAD, 'name': 'check (Linux 3.14)', 'status': 'completed',
             'conclusion': 'success'},
            {'head_sha': HEAD, 'name': 'check (macOS 3.13)', 'status': 'completed',
             'conclusion': 'skipped'}]}
        successful_and_skipped_macos_checks = {'total_count': 3, 'check_runs': [
            {'head_sha': HEAD, 'name': 'check (Linux 3.14)', 'status': 'completed',
             'conclusion': 'success'},
            {'head_sha': HEAD, 'name': 'check (macOS 3.13)', 'status': 'completed',
             'conclusion': 'success'},
            {'head_sha': HEAD, 'name': 'check (macOS 3.13)', 'status': 'completed',
             'conclusion': 'skipped'}]}

        def run_case(*, verdict='PASS', head=HEAD, current=None, gh_code=0,
                     checks=linux_only_checks):
            record = {'task_id': '1' * 32, 'record_sha256': 'r' * 64,
                      'review_dispatch_claim': [dict(review, head_sha=head,
                          review_result={'verdict': verdict})]}
            original = {'number': 17, 'head': {'sha': HEAD}, 'mergeable': True,
                        'labels': []}
            current = original if current is None else current
            calls = []

            class Store:
                def compare_and_update(self, *_args, **_kwargs):
                    return None

            @with_current_main
            def github_get(path):
                if path.startswith('commits/'):
                    return checks
                if path.endswith('/files?per_page=100'):
                    return [{'filename': 'src/module.py'}]
                if path.startswith('pulls/'):
                    return current
                return []

            def gh_runner(_root, args):
                calls.append(args)
                return type('R', (), {'returncode': gh_code})()

            result = task_advance._merge_once(store=Store(), record=record,
                number=17, head=HEAD, pr=original, events=[event],
                github_get=github_get, gh_runner=gh_runner, root='/repo')
            return result, calls

        result, calls = run_case()
        self.assertEqual(result['step'], 'WAITING_CI')
        self.assertEqual(calls, [['pr', 'edit', '17', '--add-label', 'mac-ci']])

        result, calls = run_case(checks=skipped_macos_checks)
        self.assertEqual(result['step'], 'WAITING_CI')
        self.assertEqual(calls, [['pr', 'edit', '17', '--add-label', 'mac-ci']])

        labeled = {'number': 17, 'head': {'sha': HEAD}, 'mergeable': True,
                   'labels': [{'name': 'mac-ci'}]}
        result, calls = run_case(current=labeled, checks=green_checks)
        self.assertEqual(result['step'], 'MERGED')
        self.assertFalse(any('--add-label' in call for call in calls))

        result, calls = run_case(current=labeled,
            checks=successful_and_skipped_macos_checks)
        self.assertEqual(result['step'], 'MERGED')
        self.assertFalse(any('--add-label' in call for call in calls))

        pending_macos = {'total_count': 2, 'check_runs': [
            {'head_sha': HEAD, 'name': 'check (Linux 3.14)', 'status': 'completed',
             'conclusion': 'success'},
            {'head_sha': HEAD, 'name': 'check (macOS 3.13)', 'status': 'in_progress',
             'conclusion': None}]}
        result, calls = run_case(checks=pending_macos)
        self.assertEqual(result['step'], 'MERGE_REFUSED')
        self.assertEqual(result['codes'], ['MERGE_CHECKS_NOT_GREEN'])
        self.assertEqual(result['result'], {'macos_check_pending': True})
        self.assertEqual(calls, [])

        result, calls = run_case(current=labeled, checks=skipped_macos_checks)
        self.assertEqual(result['codes'], ['MERGE_MACOS_CHECK_REQUIRED'])
        self.assertEqual(calls, [])

        result, calls = run_case(current=labeled, checks=linux_only_checks)
        self.assertEqual(result['codes'], ['MERGE_REQUIRED_CHECK_MISSING'])
        self.assertEqual(calls, [])

        result, calls = run_case(verdict='FIX')
        self.assertEqual(result['codes'], ['MERGE_REVIEW_NOT_PASS'])
        self.assertEqual(calls, [])

        result, calls = run_case(head='b' * 40)
        self.assertEqual(result['codes'], ['MERGE_REVIEW_NOT_PASS'])
        self.assertEqual(calls, [])

        closed = {'number': 17, 'head': {'sha': HEAD}, 'state': 'closed',
                  'mergeable': True, 'labels': []}
        result, calls = run_case(current=closed)
        self.assertEqual(result['codes'], ['MERGE_PR_NOT_OPEN'])
        self.assertEqual(calls, [])

        result, calls = run_case(gh_code=1)
        self.assertEqual(result['codes'], ['MERGE_MAC_CI_LABEL_FAILED'])
        self.assertEqual(calls, [['pr', 'edit', '17', '--add-label', 'mac-ci']])

    def test_skipped_mac_label_helper_requires_exact_head_green_linux_and_open_mergeable_pr(self):
        class Store:
            def get(self, _task_id):
                return base_record()

        def run_case(*, head=HEAD, mergeable=True, labels=None, files=None,
                     checks=None):
            pr = {'number': 17, 'head': {'sha': head}, 'state': 'open',
                  'merged': False, 'mergeable': mergeable,
                  'labels': [] if labels is None else labels}
            check_runs = checks or [
                {'head_sha': HEAD, 'name': 'check (Linux 3.14)',
                 'status': 'completed', 'conclusion': 'success'},
                {'head_sha': HEAD, 'name': 'check (macOS 3.13)',
                 'status': 'completed', 'conclusion': 'skipped'},
            ]
            calls = []

            def github_get(path):
                if path.startswith('commits/'):
                    return {'total_count': len(check_runs), 'check_runs': check_runs}
                if path.endswith('/files?per_page=100'):
                    return [{'filename': f} for f in (files or ['src/module.py'])]
                if path.startswith('pulls/'):
                    return pr
                return []

            def runner(_root, args):
                calls.append(args)
                return type('R', (), {'returncode': 0})()

            with patch.object(task_advance, '_d6_snapshot', return_value=d6()):
                result = task_advance.label_mac_ci_for_skipped_check(
                    task_id=base_record()['task_id'], store=Store(), pr_number=17,
                    github_get=github_get, merge_gh_runner=runner, cwd='/repo')
            self.assertFalse(any(len(call) > 1 and call[1] == 'merge'
                                 for call in calls))
            return result, calls

        result, calls = run_case()
        self.assertEqual(result['step'], 'WAITING_CI')
        self.assertEqual(calls, [['pr', 'edit', '17', '--add-label', 'mac-ci']])

        result, calls = run_case(labels=[{'name': 'mac-ci'}])
        self.assertEqual(result['step'], 'WAITING_CI')
        self.assertEqual(calls, [])

        variants = [
            ('pending Linux', [
                {'head_sha': HEAD, 'name': 'check (Linux 3.14)',
                 'status': 'in_progress', 'conclusion': None},
                {'head_sha': HEAD, 'name': 'check (macOS 3.13)',
                 'status': 'completed', 'conclusion': 'skipped'}]),
            ('failed Linux', [
                {'head_sha': HEAD, 'name': 'check (Linux 3.14)',
                 'status': 'completed', 'conclusion': 'failure'},
                {'head_sha': HEAD, 'name': 'check (macOS 3.13)',
                 'status': 'completed', 'conclusion': 'skipped'}]),
            ('pending Mac', [
                {'head_sha': HEAD, 'name': 'check (Linux 3.14)',
                 'status': 'completed', 'conclusion': 'success'},
                {'head_sha': HEAD, 'name': 'check (macOS 3.13)',
                 'status': 'in_progress', 'conclusion': None}]),
            ('failed Mac', [
                {'head_sha': HEAD, 'name': 'check (Linux 3.14)',
                 'status': 'completed', 'conclusion': 'success'},
                {'head_sha': HEAD, 'name': 'check (macOS 3.13)',
                 'status': 'completed', 'conclusion': 'failure'}]),
            ('successful Mac', [
                {'head_sha': HEAD, 'name': 'check (Linux 3.14)',
                 'status': 'completed', 'conclusion': 'success'},
                {'head_sha': HEAD, 'name': 'check (macOS 3.13)',
                 'status': 'completed', 'conclusion': 'success'}]),
        ]
        for label, check_runs in variants:
            with self.subTest(label=label):
                result, calls = run_case(checks=check_runs)
                self.assertIsNone(result)
                self.assertEqual(calls, [])

        for kwargs in (
                {'head': 'b' * 40},
                {'mergeable': None},
                {'files': ['tools/llmtool/llmtool/chat_tui.py']}):
            with self.subTest(kwargs=kwargs):
                result, calls = run_case(**kwargs)
                self.assertIsNone(result)
                self.assertEqual(calls, [])

    def test_advance_preserves_terminal_ci_failure_separately_from_pending(self):
        claim = {'pr': 17, 'head_sha': HEAD, 'version': 1,
                 'state': 'COMPLETED', 'review_result': {'verdict': 'PASS'}}
        event = {'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}

        for missing, expected_step, expected_codes in (
                (['REQUIRED_CI_MISSING', 'REQUIRED_CI_FAILED'], 'CI_FAILED',
                 ['REQUIRED_CI_MISSING', 'REQUIRED_CI_FAILED']),
                (['REQUIRED_CI_MISSING', 'REQUIRED_CI_PENDING'], 'WAITING_CI',
                 ['REQUIRED_CI_MISSING', 'REQUIRED_CI_PENDING'])):
            class Store:
                def __init__(self):
                    self.record = base_record(review_dispatch_claim=[claim],
                        completion_events=[event])
                def get(self, _task_id):
                    return self.record

            store = Store()

            def completion(_args, *, store):
                store.record['completion']['missing_gates'] = missing
                return 1

            with self.subTest(missing=missing), \
                 patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
                 patch.object(task_advance, '_pr_snapshot', return_value=PR), \
                 patch.object(task_advance, '_review_outcomes', return_value=[]):
                result = task_advance.advance(task_id=store.record['task_id'],
                    store=store, pr_number=17, run_task_complete_call=completion)
            self.assertEqual(result['step'], expected_step)
            self.assertEqual(result['codes'], expected_codes)

    def test_merge_refuses_unqualified_reviews_checks_heads_and_owner_acceptance(self):
        review = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'COMPLETED',
                  'review_result': {'verdict': 'PASS'}}
        event = {'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}

        class Store:
            def __init__(self):
                self.attempt = None
            def compare_and_update(self, _task_id, *, expected, merge_attempt):
                if self.attempt != expected.get('merge_attempt'):
                    raise RuntimeError('claim race')
                self.attempt = dict(merge_attempt)

        def run_case(*, record=None, pr=None, checks=None, second_pr=None, events=None,
                     files=None, comments=None):
            record = dict(record or {
                'task_id': '1' * 32, 'record_sha256': 'r' * 64,
                'review_dispatch_claim': [review],
            })
            pr = pr or {'number': 17, 'head': {'sha': HEAD}, 'mergeable': True,
                        'labels': [{'name': 'mac-ci'}], 'body': ''}
            checks = checks if checks is not None else [
                {'head_sha': HEAD, 'name': 'check (Linux 3.14)', 'status': 'completed',
                 'conclusion': 'success'},
                {'head_sha': HEAD, 'name': 'check (macOS 3.13)', 'status': 'completed',
                 'conclusion': 'success'},
            ]
            calls = []
            pulls = 0
            @with_current_main
            def github_get(path):
                nonlocal pulls
                if path.startswith('commits/'):
                    return {'total_count': len(checks), 'check_runs': checks}
                if path.endswith('/files?per_page=100'):
                    return files if files is not None else [{'filename': 'src/module.py'}]
                if path.endswith('/comments?per_page=100'):
                    return comments if comments is not None else []
                if path.startswith('pulls/'):
                    pulls += 1
                    return second_pr if pulls == 1 and second_pr is not None else pr
                return []
            def gh_runner(_root, args):
                calls.append(args)
                return type('R', (), {'returncode': 0})()
            result = task_advance._merge_once(store=Store(), record=record, number=17,
                head=HEAD, pr=pr, events=[event] if events is None else events,
                github_get=github_get,
                gh_runner=gh_runner, root='/repo')
            return result, calls

        fix_record = {'task_id': '1' * 32, 'record_sha256': 'r' * 64,
            'review_dispatch_claim': [dict(review, review_result={'verdict': 'FIX'})]}
        newer_fix = dict(review, version=2, review_result={'verdict': 'FIX'})
        newer_fix_event = {'source': 'REVIEWER', 'source_sha': HEAD, 'version': 2,
                           'outcome': 'REVIEW_FAIL'}
        claude_pass = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'DISPATCHED'}
        claude_fix = {'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                      'outcome': 'REVIEW_FAIL'}
        moved = {'number': 17, 'head': {'sha': 'b' * 40}, 'mergeable': True,
                 'labels': [], 'body': ''}
        tui = {'number': 17, 'head': {'sha': HEAD}, 'mergeable': True,
               'labels': [], 'body': '<!-- llmtool-owner-accepted -->'}
        cases = [
            ('Claude PASS', {'record': {'task_id': '1' * 32, 'record_sha256': 'r' * 64,
                'review_dispatch_claim': [claude_pass]}}, 'MERGED'),
            ('Claude FIX', {'record': {'task_id': '1' * 32, 'record_sha256': 'r' * 64,
                'review_dispatch_claim': [claude_pass]}, 'events': [claude_fix]},
             'MERGE_REVIEW_NOT_PASS'),
            ('Claude wrong head', {'record': {'task_id': '1' * 32, 'record_sha256': 'r' * 64,
                'review_dispatch_claim': [dict(claude_pass, head_sha='b' * 40)]}},
             'MERGE_REVIEW_NOT_PASS'),
            ('Claude newer FIX', {'record': {'task_id': '1' * 32, 'record_sha256': 'r' * 64,
                'review_dispatch_claim': [claude_pass]},
                'events': [event, newer_fix_event]}, 'MERGE_NEWER_REVIEW_EXISTS'),
            ('FIX', {'record': fix_record}, 'MERGE_REVIEW_NOT_PASS'),
            ('newer FIX', {'record': {'task_id': '1' * 32, 'record_sha256': 'r' * 64,
                'review_dispatch_claim': [review, newer_fix]},
                'events': [event, newer_fix_event]}, 'MERGE_NEWER_REVIEW_EXISTS'),
            ('stale initial head', {'pr': moved}, 'MERGE_STALE_HEAD'),
            ('moved before merge', {'second_pr': moved}, 'MERGE_STALE_HEAD'),
            ('conflicting PR', {'record': {'task_id': '1' * 32, 'record_sha256': 'r' * 64,
                'review_dispatch_claim': [review, dict(review, pr=18)]}},
             'MERGE_CONFLICTING_PR'),
            ('unlabeled chat TUI path', {'pr': tui, 'files': [
                {'filename': 'tools/llmtool/llmtool/chat_tui.py'}]},
             'MERGE_OWNER_ACCEPTANCE_MANUAL'),
            ('unlabeled TUI module path', {'files': [
                {'filename': 'tools/llmtool/llmtool/tui_markdown_editor.py'}]},
             'MERGE_OWNER_ACCEPTANCE_MANUAL'),
            ('TUI test path', {'files': [
                {'filename': 'tools/llmtool/tests/test_tui_markdown.py'}]},
             'MERGE_OWNER_ACCEPTANCE_MANUAL'),
            ('chat TUI test path', {'files': [
                {'filename': 'tools/llmtool/tests/test_chat_tui.py'}]},
             'MERGE_OWNER_ACCEPTANCE_MANUAL'),
            ('TUI PTY driver path', {'files': [
                {'filename': 'tools/llmtool/tests/tui_pty_driver.py'}]},
             'MERGE_OWNER_ACCEPTANCE_MANUAL'),
            ('negative tui_markdown path', {'files': [
                {'filename': 'tools/llmtool/llmtool/tui_markdown.py'}]},
             'MERGE_OWNER_ACCEPTANCE_MANUAL'),
            ('pending check', {'checks': [{'head_sha': HEAD, 'name': 'check (Linux 3.14)',
                'status': 'in_progress', 'conclusion': None}]}, 'MERGE_CHECKS_NOT_GREEN'),
            ('cancelled check', {'checks': [{'head_sha': HEAD, 'name': 'check (Linux 3.14)',
                'status': 'completed', 'conclusion': 'cancelled'}]}, 'MERGE_CHECKS_NOT_GREEN'),
            ('failed check', {'checks': [{'head_sha': HEAD, 'name': 'check (Linux 3.14)',
                'status': 'completed', 'conclusion': 'failure'}]}, 'MERGE_CHECKS_NOT_GREEN'),
            ('missing checks', {'checks': []}, 'MERGE_CHECKS_MISSING'),
            ('missing required macOS check', {'files': [{'filename': 'src/module.py'}],
                'checks': [{'head_sha': HEAD,
                'name': 'check (Linux 3.14)', 'status': 'completed', 'conclusion': 'success'}]},
             'MERGE_REQUIRED_CHECK_MISSING'),
            ('skipped required macOS check', {'checks': [
                {'head_sha': HEAD, 'name': 'check (Linux 3.14)', 'status': 'completed',
                 'conclusion': 'success'},
                {'head_sha': HEAD, 'name': 'check (macOS 3.13)', 'status': 'completed',
                 'conclusion': 'skipped'}]}, 'MERGE_MACOS_CHECK_REQUIRED'),
            ('tests still require macOS', {'files': [
                {'filename': 'tools/llmtool/tests/test_task_advance.py'}],
                'checks': [{'head_sha': HEAD, 'name': 'check (Linux 3.14)',
                    'status': 'completed', 'conclusion': 'success'}]},
             'MERGE_REQUIRED_CHECK_MISSING'),
        ]
        for label, kwargs, code in cases:
            with self.subTest(label=label):
                result, calls = run_case(**kwargs)
                if code == 'MERGED':
                    self.assertEqual(result['step'], 'MERGED')
                    self.assertEqual(sum(call[1] == 'merge' for call in calls), 1)
                else:
                    self.assertEqual(result['codes'], [code])
                    self.assertEqual(calls, [])

    def test_merge_unknown_retries_only_after_fresh_open_same_head_read(self):
        review = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'COMPLETED',
                  'review_result': {'verdict': 'PASS'}}
        record = {'task_id': '1' * 32, 'record_sha256': 'r' * 64,
                  'review_dispatch_claim': [review]}
        event = {'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}
        class Store:
            def __init__(self):
                self.attempt = None
            def compare_and_update(self, _task_id, *, expected, merge_attempt):
                if self.attempt != expected.get('merge_attempt'):
                    raise RuntimeError('claim race')
                self.attempt = dict(merge_attempt)
        store, calls = Store(), []
        pr = {'number': 17, 'head': {'sha': HEAD}, 'state': 'open', 'merged': False,
              'mergeable': True,
              'labels': [{'name': 'mac-ci'}]}
        def github_get(path):
            if path.startswith('commits/'):
                return {'total_count': 1, 'check_runs': [
                    {'head_sha': HEAD, 'name': 'check (Linux 3.14)', 'status': 'completed',
                     'conclusion': 'failure'}]}
            if path.endswith('/files?per_page=100'):
                return [{'filename': 'src/module.py'}]
            if path.startswith('pulls/'):
                return pr
            return []
        def ambiguous(_root, args):
            calls.append(args)
            raise OSError('connection lost')
        result = task_advance._merge_once(store=store, record=record, number=17,
            head=HEAD, pr=pr, events=[event], github_get=github_get,
            gh_runner=ambiguous, root='/repo')
        self.assertEqual(result['codes'], ['MERGE_CHECKS_NOT_GREEN'])
        self.assertEqual(calls, [])
        github_get_green = with_current_main(lambda path: (
            {'total_count': 2, 'check_runs': [
                {'head_sha': HEAD, 'name': 'check (Linux 3.14)', 'status': 'completed',
                 'conclusion': 'success'},
                {'head_sha': HEAD, 'name': 'check (macOS 3.13)', 'status': 'completed',
                 'conclusion': 'success'}]} if path.startswith('commits/') else
            [{'filename': 'src/module.py'}] if path.endswith('/files?per_page=100') else
            pr))
        result = task_advance._merge_once(store=store, record=record, number=17,
            head=HEAD, pr=pr, events=[event], github_get=github_get_green,
            gh_runner=ambiguous, root='/repo')
        self.assertEqual(result['codes'], ['MERGE_OUTCOME_UNKNOWN'])
        self.assertEqual(store.attempt['state'], 'UNKNOWN')
        retry_record = dict(record, merge_attempt=store.attempt)
        result = task_advance._merge_once(store=store, record=retry_record, number=17,
            head=HEAD, pr=pr, events=[event], github_get=github_get_green,
            gh_runner=ambiguous, root='/repo')
        self.assertEqual(result['codes'], ['MERGE_OUTCOME_UNKNOWN'])
        self.assertEqual(len(calls), 2)

    def test_merge_unknown_retry_refuses_moved_head(self):
        review = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'COMPLETED',
                  'review_result': {'verdict': 'PASS'}}
        event = {'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}
        prior = {'pr': 17, 'head_sha': HEAD, 'state': 'UNKNOWN',
                 'claimed_at': '2026-11-06T00:00:00Z'}
        record = {'task_id': '1' * 32, 'record_sha256': 'r' * 64,
                  'review_dispatch_claim': [review], 'merge_attempt': prior}

        class Store:
            def __init__(self):
                self.attempt = prior
                self.updates = []
            def compare_and_update(self, _task_id, *, expected, merge_attempt):
                self.updates.append((expected, merge_attempt))
                self.attempt = dict(merge_attempt)

        store, calls, reads = Store(), [], []
        pr = {'number': 17, 'head': {'sha': HEAD}, 'state': 'open',
              'merged': False, 'mergeable': True, 'labels': [{'name': 'mac-ci'}]}
        current = dict(pr, head={'sha': 'b' * 40})
        def github_get(path):
            reads.append(path)
            if path.startswith('commits/'):
                return {'total_count': 2, 'check_runs': [
                    {'head_sha': HEAD, 'name': 'check (Linux 3.14)', 'status': 'completed',
                     'conclusion': 'success'},
                    {'head_sha': HEAD, 'name': 'check (macOS 3.13)', 'status': 'completed',
                     'conclusion': 'success'}]}
            if path.endswith('/files?per_page=100'):
                return [{'filename': 'src/module.py'}]
            return current
        result = task_advance._merge_once(store=store, record=record, number=17,
            head=HEAD, pr=pr, events=[event],
            github_get=github_get,
            gh_runner=lambda _root, args: calls.append(args), root='/repo')
        self.assertEqual(result['codes'], ['MERGE_STALE_HEAD'])
        self.assertIn('pulls/17', reads)
        self.assertEqual(calls, [])
        self.assertEqual(store.updates, [])
        self.assertEqual(store.attempt, prior)

    def test_failed_merge_that_freshly_reads_merged_posts_provenance(self):
        review = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'COMPLETED',
                  'review_result': {'verdict': 'PASS'}}
        event = {'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}
        record = {'task_id': '1' * 32, 'record_sha256': 'r' * 64,
                  'review_dispatch_claim': [review]}

        class Store:
            def __init__(self):
                self.attempt = None
            def compare_and_update(self, _task_id, *, expected, merge_attempt):
                if self.attempt != expected.get('merge_attempt'):
                    raise RuntimeError('claim race')
                self.attempt = dict(merge_attempt)

        store, calls = Store(), []
        opened = {'number': 17, 'head': {'sha': HEAD}, 'state': 'open',
                  'merged': False, 'mergeable': True, 'labels': [{'name': 'mac-ci'}]}
        merged = dict(opened, state='closed', merged=True)
        @with_current_main
        def github_get(path):
            if path.startswith('commits/'):
                return {'total_count': 2, 'check_runs': [
                    {'head_sha': HEAD, 'name': 'check (Linux 3.14)', 'status': 'completed',
                     'conclusion': 'success'},
                    {'head_sha': HEAD, 'name': 'check (macOS 3.13)', 'status': 'completed',
                     'conclusion': 'success'}]}
            if path.endswith('/files?per_page=100'):
                return [{'filename': 'src/module.py'}]
            return opened if sum(call[1] == 'merge' for call in calls) == 0 else merged
        def failed_merge(_root, args):
            calls.append(args)
            if args[1] == 'merge':
                return type('R', (), {'returncode': 1})()
            return type('R', (), {'returncode': 0})()
        result = task_advance._merge_once(store=store, record=record, number=17,
            head=HEAD, pr=opened, events=[event], github_get=github_get,
            gh_runner=failed_merge, root='/repo')
        self.assertEqual(result['step'], 'MERGED')
        self.assertEqual(store.attempt['state'], 'MERGED')
        self.assertEqual(calls[1][:3], ['pr', 'comment', '17'])
        self.assertIn('merge provenance', calls[1][-1])

    def test_prior_claimed_attempt_is_refused(self):
        review = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'COMPLETED',
                  'review_result': {'verdict': 'PASS'}}
        event = {'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}
        prior = {'pr': 17, 'head_sha': HEAD, 'state': 'CLAIMED',
                 'claimed_at': '2026-11-06T00:00:00Z'}
        result = self._run_prior_merge_attempt(prior, review, event)
        self.assertEqual(result['codes'], ['MERGE_ALREADY_CLAIMED'])

    def test_unknown_retry_requires_explicit_open_and_unmerged_fields(self):
        review = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'COMPLETED',
                  'review_result': {'verdict': 'PASS'}}
        event = {'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}
        prior = {'pr': 17, 'head_sha': HEAD, 'state': 'UNKNOWN',
                 'claimed_at': '2026-11-06T00:00:00Z'}
        pr_without_status = {'number': 17, 'head': {'sha': HEAD}, 'mergeable': True,
                             'labels': [{'name': 'mac-ci'}]}
        result = self._run_prior_merge_attempt(prior, review, event,
                                               pr=pr_without_status)
        self.assertEqual(result['codes'], ['MERGE_ALREADY_CLAIMED'])

    def test_prior_attempt_for_other_pr_or_head_is_conflicting(self):
        review = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'COMPLETED',
                  'review_result': {'verdict': 'PASS'}}
        event = {'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}
        for prior in (
                {'pr': 18, 'head_sha': HEAD, 'state': 'UNKNOWN',
                 'claimed_at': '2026-11-06T00:00:00Z'},
                {'pr': 17, 'head_sha': 'b' * 40, 'state': 'UNKNOWN',
                 'claimed_at': '2026-11-06T00:00:00Z'}):
            with self.subTest(prior=prior):
                result = self._run_prior_merge_attempt(prior, review, event)
                self.assertEqual(result['codes'], ['MERGE_CONFLICTING_PR'])

    def _run_prior_merge_attempt(self, prior, review, event, pr=None):
        record = {'task_id': '1' * 32, 'record_sha256': 'r' * 64,
                  'review_dispatch_claim': [review], 'merge_attempt': prior}
        class Store:
            def compare_and_update(self, *_args, **_kwargs):
                raise AssertionError('must not update a refused attempt')
        pr = pr or {'number': 17, 'head': {'sha': HEAD}, 'state': 'open', 'merged': False,
                    'mergeable': True, 'labels': [{'name': 'mac-ci'}]}
        @with_current_main
        def github_get(path):
            if path.startswith('commits/'):
                return {'total_count': 2, 'check_runs': [
                    {'head_sha': HEAD, 'name': 'check (Linux 3.14)', 'status': 'completed',
                     'conclusion': 'success'},
                    {'head_sha': HEAD, 'name': 'check (macOS 3.13)', 'status': 'completed',
                     'conclusion': 'success'}]}
            if path.endswith('/files?per_page=100'):
                return [{'filename': 'src/module.py'}]
            return pr
        return task_advance._merge_once(store=Store(), record=record, number=17,
            head=HEAD, pr=pr, events=[event], github_get=github_get,
            gh_runner=lambda *_args: (_ for _ in ()).throw(AssertionError('merge called')),
            root='/repo')

    def test_failed_then_successful_merge_uses_real_task_store(self):
        from llmtool.task_store import TaskStore
        from llmtool.task_store import new_id
        request = 'Bounded non-sensitive merge retry task.'
        request_sha = hashlib.sha256(request.encode()).hexdigest()
        task_id, run_id = new_id(), new_id()
        task = task_submission_cli._record(
            task_id, run_id, 'pm-' + new_id(), 'pm-profile', request,
            state='PM_RESPONDED', provider_type='openai-compatible', model='pm-model',
            backend='NO_NATIVE_SESSION_ID_EXPOSED', request_sha=request_sha)
        task.update(pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                                      'base_url': None},
                    response='PM returned a bounded task.', error_code=None,
                    pm_assignment={'schema_version': 1, 'route': 'codex', 'risk': 'LOW',
                                   'goal': 'Retry a safely verified merge.',
                                   'acceptance': ['the PR is merged']})
        with tempfile.TemporaryDirectory() as temp:
            store = TaskStore(Path(temp).resolve() / 'tasks')
            store.create(task)
            actual = store.get(task_id)
            review = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'COMPLETED',
                      'review_result': {'verdict': 'PASS'}}
            event = {'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                     'outcome': 'REVIEW_PASS'}
            pr = {'number': 17, 'head': {'sha': HEAD}, 'state': 'open', 'merged': False,
                  'mergeable': True, 'labels': [{'name': 'mac-ci'}]}
            checks = {'total_count': 2, 'check_runs': [
                {'head_sha': HEAD, 'name': 'check (Linux 3.14)', 'status': 'completed',
                 'conclusion': 'success'},
                {'head_sha': HEAD, 'name': 'check (macOS 3.13)', 'status': 'completed',
                 'conclusion': 'success'}]}
            calls = []
            @with_current_main
            def github_get(path):
                if path.startswith('commits/'):
                    return checks
                if path.endswith('/files?per_page=100'):
                    return [{'filename': 'src/module.py'}]
                return pr
            def gh_runner(_root, args):
                calls.append(args)
                if args[1] == 'merge' and sum(call[1] == 'merge' for call in calls) == 1:
                    return type('R', (), {'returncode': 1})()
                return type('R', (), {'returncode': 0})()
            record = dict(actual, review_dispatch_claim=[review])
            first = task_advance._merge_once(store=store, record=record, number=17,
                head=HEAD, pr=pr, events=[event], github_get=github_get,
                gh_runner=gh_runner, root='/repo')
            self.assertEqual(first['codes'], ['MERGE_OUTCOME_UNKNOWN'])
            persisted = store.get(task_id)
            self.assertEqual(persisted['merge_attempt']['state'], 'UNKNOWN')
            second_record = dict(persisted, review_dispatch_claim=[review])
            second = task_advance._merge_once(store=store, record=second_record, number=17,
                head=HEAD, pr=pr, events=[event], github_get=github_get,
                gh_runner=gh_runner, root='/repo')
            self.assertEqual(second['step'], 'MERGED')
            attempts = [call for call in calls if call[1] == 'merge']
            self.assertEqual(len(attempts), 2)
            self.assertEqual(store.get(task_id)['merge_attempt']['state'], 'MERGED')
            self.assertNotEqual(second_record['merge_attempt']['claimed_at'],
                                store.get(task_id)['merge_attempt']['claimed_at'])
            datetime.fromisoformat(
                store.get(task_id)['merge_attempt']['claimed_at'].replace('Z', '+00:00'))

    def test_successful_merge_keeps_merged_state_when_record_or_comment_warns(self):
        review = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'COMPLETED',
                  'review_result': {'verdict': 'PASS'}}
        event = {'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}
        record = {'task_id': '1' * 32, 'record_sha256': 'r' * 64,
                  'review_dispatch_claim': [review]}

        class Store:
            def __init__(self):
                self.attempt = None
                self.fail_record_once = True
            def compare_and_update(self, _task_id, *, expected, merge_attempt):
                if 'record_sha256' in expected:
                    self.attempt = dict(merge_attempt)
                    return
                if self.attempt != expected.get('merge_attempt'):
                    raise RuntimeError('claim changed')
                if merge_attempt['state'] == 'MERGED' and self.fail_record_once:
                    self.fail_record_once = False
                    raise RuntimeError('temporary record error')
                self.attempt = dict(merge_attempt)

        store, gh_calls = Store(), []
        pr = {'number': 17, 'head': {'sha': HEAD}, 'mergeable': True,
              'labels': [{'name': 'mac-ci'}]}
        checks = {'total_count': 2, 'check_runs': [
            {'head_sha': HEAD, 'name': 'check (Linux 3.14)', 'status': 'completed',
             'conclusion': 'success'},
            {'head_sha': HEAD, 'name': 'check (macOS 3.13)', 'status': 'completed',
             'conclusion': 'success'}]}
        @with_current_main
        def github_get(path):
            if path.startswith('commits/'):
                return checks
            if path.endswith('/files?per_page=100'):
                return [{'filename': 'src/module.py'}]
            return pr
        def gh_runner(_root, args):
            gh_calls.append(args)
            return type('R', (), {'returncode': 0 if args[1] == 'merge' else 1})()
        with patch('llmtool.mac_full_suite.request_full_suite',
                   return_value='MAC_FULL_SUITE_DISPATCHED'):
            result = task_advance._merge_once(store=store, record=record, number=17,
                head=HEAD, pr=pr, events=[event], github_get=github_get,
                gh_runner=gh_runner, root='/repo')
            self.assertEqual(result['step'], 'MERGED')
            self.assertEqual(result['codes'], [
                'MERGE_RESULT_RECORD_FAILED', 'MERGE_PROVENANCE_COMMENT_FAILED'])
            self.assertEqual(store.attempt['state'], 'MERGED')
            self.assertEqual(sum(call[1] == 'merge' for call in gh_calls), 1)

    def test_successful_merge_requests_no_mac_full_suite(self):
        """Owner #1485: a single merge requests no FULL; the sweep command decides."""
        review = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'COMPLETED',
                  'review_result': {'verdict': 'PASS'}}
        event = {'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}
        record = {'task_id': '1' * 32, 'record_sha256': 'r' * 64,
                  'review_dispatch_claim': [review]}

        class Store:
            def __init__(self):
                self.attempt = None
                self.fail_record_once = True
            def compare_and_update(self, _task_id, *, expected, merge_attempt):
                if 'record_sha256' in expected:
                    self.attempt = dict(merge_attempt)
                    return
                if self.attempt != expected.get('merge_attempt'):
                    raise RuntimeError('claim changed')
                if merge_attempt['state'] == 'MERGED' and self.fail_record_once:
                    self.fail_record_once = False
                    raise RuntimeError('temporary record error')
                self.attempt = dict(merge_attempt)

        store, gh_calls = Store(), []
        pr = {'number': 17, 'head': {'sha': HEAD}, 'mergeable': True,
              'labels': [{'name': 'mac-ci'}]}
        checks = {'total_count': 2, 'check_runs': [
            {'head_sha': HEAD, 'name': 'check (Linux 3.14)', 'status': 'completed',
             'conclusion': 'success'},
            {'head_sha': HEAD, 'name': 'check (macOS 3.13)', 'status': 'completed',
             'conclusion': 'success'}]}
        @with_current_main
        def github_get(path):
            if path.startswith('commits/'):
                return checks
            if path.endswith('/files?per_page=100'):
                return [{'filename': 'src/module.py'}]
            return pr
        def gh_runner(_root, args):
            gh_calls.append(args)
            return type('R', (), {'returncode': 0 if args[1] == 'merge' else 1})()
        with patch('llmtool.mac_full_suite.request_full_suite',
                   return_value='MAC_FULL_SUITE_DISPATCH_FAILED') as mock_suite:
            result = task_advance._merge_once(store=store, record=record, number=17,
                head=HEAD, pr=pr, events=[event], github_get=github_get,
                gh_runner=gh_runner, root='/repo')
            self.assertEqual(result['step'], 'MERGED')
            self.assertEqual(mock_suite.call_count, 0)
            self.assertNotIn('MAC_FULL_SUITE_DISPATCH_FAILED', result['codes'])


class TaskAdvanceMergeCurrentMainTests(unittest.TestCase):
    """Owner decision #1317: task advance --merge allows mergeable heads behind main."""

    LINUX = {'head_sha': HEAD, 'name': 'check (Linux 3.14)', 'status': 'completed',
             'conclusion': 'success'}
    MACOS = dict(LINUX, name='check (macOS 3.13)')
    COMPARE_PATH = f'compare/{MAIN_SHA}...{HEAD}'
    _MISSING = object()

    def merge(self, *, main_reply=None, compare_reply=None, check_runs=None,
              labels=('mac-ci',), mergeable_state='clean', mergeable=True,
              pr_head=HEAD, current_pr_head=None):
        from llmtool.task_completion import _GITHUB_API_PATH
        main_reply = {'commit': {'sha': MAIN_SHA}} if main_reply is None else main_reply
        compare_reply = {'status': 'ahead'} if compare_reply is None else compare_reply
        check_runs = [self.LINUX, self.MACOS] if check_runs is None else check_runs
        record = base_record(
            record_sha256='r' * 64,
            review_dispatch_claim=[{'pr': 17, 'head_sha': HEAD, 'version': 1,
                                    'state': 'COMPLETED',
                                    'review_result': {'verdict': 'PASS'}}],
            completion_events=[{'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                                'outcome': 'REVIEW_PASS'}])
        pr = {'number': 17, 'head': {'sha': pr_head}, 'state': 'open', 'merged': False,
              'mergeable': mergeable, 'labels': [{'name': name} for name in labels]}
        if mergeable_state is not self._MISSING:
            pr['mergeable_state'] = mergeable_state
        reads, calls, claims = [], [], []

        class Store:
            def get(self, _task_id):
                return record

            def compare_and_update(self, _task_id, *, expected, merge_attempt):
                claims.append(dict(merge_attempt))

        def reply(value):
            if isinstance(value, Exception):
                raise value
            return value

        pulls_calls = 0

        def github_get(path):
            nonlocal pulls_calls
            # The same validator guards the production GitHub read.
            self.assertTrue(_GITHUB_API_PATH.fullmatch(path), path)
            reads.append(path)
            if path == 'branches/main':
                return reply(main_reply)
            if path.startswith('compare/'):
                return reply(compare_reply)
            if path.startswith('commits/'):
                return {'total_count': len(check_runs), 'check_runs': check_runs}
            if path.endswith('/files?per_page=100'):
                return [{'filename': 'src/module.py'}]
            if path == 'pulls/17':
                pulls_calls += 1
                if pulls_calls > 1 and current_pr_head is not None:
                    return dict(pr, head={'sha': current_pr_head})
                return pr
            return []

        def gh_runner(_root, args):
            calls.append(args)
            return type('R', (), {'returncode': 0})()

        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_review_outcomes', return_value=[]):
            result = task_advance.advance(
                task_id=record['task_id'], store=Store(), pr_number=17, merge=True,
                github_get=github_get, merge_gh_runner=gh_runner)
        return result, reads, calls, claims

    def assert_refused(self, outcome, code):
        result, _reads, calls, claims = outcome
        self.assertEqual(result['step'], 'MERGE_REFUSED')
        self.assertEqual(result['codes'], [code])
        self.assertEqual(calls, [])
        self.assertEqual(claims, [])

    def test_head_behind_or_diverged_from_main_merges_when_mergeable(self):
        for status in ('behind', 'diverged'):
            for state in ('clean', 'behind', 'unstable', 'has_hooks'):
                with self.subTest(status=status, state=state):
                    result, _reads, calls, claims = self.merge(
                        compare_reply={'status': status, 'behind_by': 2},
                        mergeable_state=state)
                    self.assertEqual(result['step'], 'MERGED')
                    self.assertEqual(calls[0], ['pr', 'merge', '17', '--squash',
                                                '--match-head-commit', HEAD])
                    self.assertEqual(sum(call[1] == 'merge' for call in calls), 1)
                    self.assertEqual([claim['state'] for claim in claims],
                                     ['CLAIMED', 'MERGED'])
                    self.assertEqual(result['result']['main_behind_by'], 2)

    def test_head_with_current_main_merges(self):
        with patch('llmtool.mac_full_suite.request_full_suite',
                   return_value='MAC_FULL_SUITE_DISPATCHED'):
            for status in ('ahead', 'identical'):
                with self.subTest(status=status):
                    result, reads, calls, claims = self.merge(compare_reply={'status': status})
                    self.assertEqual(result['step'], 'MERGED')
                    self.assertEqual(calls[0], ['pr', 'merge', '17', '--squash',
                                                '--match-head-commit', HEAD])
                    self.assertEqual(sum(call[1] == 'merge' for call in calls), 1)
                    self.assertEqual([claim['state'] for claim in claims],
                                     ['CLAIMED', 'MERGED'])
                    self.assertEqual(result['result']['main_behind_by'], 0)
                    self.assertEqual(reads[-3:], ['pulls/17', 'branches/main',
                                                  self.COMPARE_PATH])

    def test_behind_head_refusals(self):
        cases = (
            ('dirty mergeable_state', {'mergeable_state': 'dirty'}, 'MERGE_PR_CONFLICT'),
            ('unknown mergeable_state', {'mergeable_state': 'unknown'}, 'MERGE_MERGEABLE_UNKNOWN'),
            ('missing mergeable_state', {'mergeable_state': self._MISSING}, 'MERGE_MERGEABLE_UNKNOWN'),
            ('None mergeable_state', {'mergeable_state': None}, 'MERGE_MERGEABLE_UNKNOWN'),
            ('blocked mergeable_state', {'mergeable_state': 'blocked'}, 'MERGE_PR_NOT_MERGEABLE'),
            ('mergeable False', {'mergeable': False}, 'MERGE_PR_NOT_MERGEABLE'),
            ('behind_by missing', {'compare_reply': {'status': 'behind'}}, 'MERGE_CHECKS_UNAVAILABLE'),
            ('behind_by 0', {'compare_reply': {'status': 'behind', 'behind_by': 0}}, 'MERGE_CHECKS_UNAVAILABLE'),
            ('behind_by text', {'compare_reply': {'status': 'behind', 'behind_by': '2'}}, 'MERGE_CHECKS_UNAVAILABLE'),
        )
        for name, kwargs, expected_code in cases:
            with self.subTest(name):
                opts = {'compare_reply': {'status': 'behind', 'behind_by': 2}}
                opts.update(kwargs)
                self.assert_refused(self.merge(**opts), expected_code)

    def test_behind_pr_with_stale_head_refuses_with_same_code(self):
        other_head = 'b' * 40
        self.assert_refused(
            self.merge(compare_reply={'status': 'behind', 'behind_by': 2},
                       current_pr_head=other_head),
            'MERGE_STALE_HEAD')
        self.assert_refused(
            self.merge(compare_reply={'status': 'behind', 'behind_by': 2},
                       pr_head=other_head),
            'MERGE_STALE_HEAD')

    def test_behind_pr_with_clean_mergeable_state_reaches_mac_label_step(self):
        result, _reads, calls, claims = self.merge(
            compare_reply={'status': 'behind', 'behind_by': 2},
            check_runs=[self.LINUX], labels=(), mergeable_state='clean')
        self.assertEqual(result['step'], 'WAITING_CI')
        self.assertEqual(calls, [['pr', 'edit', '17', '--add-label', 'mac-ci']])
        self.assertEqual(claims, [])

    def test_diverged_pr_with_clean_mergeable_state_merges(self):
        result, _reads, calls, claims = self.merge(
            compare_reply={'status': 'diverged', 'ahead_by': 5, 'behind_by': 3},
            mergeable_state='clean', labels=('mac-ci',))
        self.assertEqual(result['step'], 'MERGED')
        self.assertEqual(calls[0], ['pr', 'merge', '17', '--squash',
                                    '--match-head-commit', HEAD])
        self.assertEqual(sum(call[1] == 'merge' for call in calls), 1)
        self.assertEqual([claim['state'] for claim in claims],
                         ['CLAIMED', 'MERGED'])
        self.assertEqual(result['result']['main_behind_by'], 3)

    def test_current_main_still_reaches_the_mac_label_step(self):
        result, _reads, calls, claims = self.merge(check_runs=[self.LINUX], labels=())
        self.assertEqual(result['step'], 'WAITING_CI')
        self.assertEqual(calls, [['pr', 'edit', '17', '--add-label', 'mac-ci']])
        self.assertEqual(claims, [])

    def test_main_read_failure_or_malformed_reply_fails_closed(self):
        cases = (
            ('main read raises', {'main_reply': RuntimeError('offline')}),
            ('main reply is a list', {'main_reply': []}),
            ('main reply has no commit', {'main_reply': {'name': 'main'}}),
            ('main commit is not an object', {'main_reply': {'commit': MAIN_SHA}}),
            ('main SHA is missing', {'main_reply': {'commit': {}}}),
            ('main SHA is not text', {'main_reply': {'commit': {'sha': 7}}}),
            ('main SHA is short', {'main_reply': {'commit': {'sha': 'e' * 39}}}),
            ('main SHA is long', {'main_reply': {'commit': {'sha': 'e' * 41}}}),
            ('main SHA is not hex', {'main_reply': {'commit': {'sha': 'z' * 40}}}),
            ('compare read raises', {'compare_reply': RuntimeError('offline')}),
            ('compare reply is a list', {'compare_reply': []}),
            ('compare status is missing', {'compare_reply': {'ahead_by': 1}}),
            ('compare status is unknown', {'compare_reply': {'status': 'unknown'}}),
            ('compare status is not text', {'compare_reply': {'status': ['ahead']}}),
        )
        for name, kwargs in cases:
            with self.subTest(name):
                self.assert_refused(self.merge(**kwargs), 'MERGE_CHECKS_UNAVAILABLE')

    def test_malformed_main_sha_is_never_used_in_a_compare_read(self):
        _result, reads, _calls, _claims = self.merge(
            main_reply={'commit': {'sha': 'z' * 40}})
        self.assertFalse(any(path.startswith('compare/') for path in reads))


class TaskAdvanceMacLabelTests(unittest.TestCase):
    def _advance_with_missing_gates(self, missing, label_result=None,
                                    label_error=None):
        claim = {'pr': 17, 'head_sha': HEAD, 'version': 1,
                 'state': 'COMPLETED', 'review_result': {'verdict': 'PASS'}}
        event = {'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}

        class Store:
            def __init__(self):
                self.record = base_record(review_dispatch_claim=[claim],
                                          completion_events=[event])

            def get(self, _task_id):
                return self.record

        store = Store()

        def complete(_args, *, store):
            store.record['completion']['missing_gates'] = missing
            return 0

        def label(**kwargs):
            self.label_kwargs = kwargs
            if label_error is not None:
                raise label_error
            return label_result

        get = lambda _path: PR
        runner = lambda *_args: subprocess.CompletedProcess([], 0)
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[]), \
             patch.object(task_advance, 'label_mac_ci_for_skipped_check',
                          side_effect=label) as label_call:
            result = task_advance.advance(
                task_id=store.record['task_id'], store=store, pr_number=17,
                github_get=get, cwd='/repo/task', merge_gh_runner=runner,
                run_task_complete_call=complete)
        return result, label_call, store, get, runner

    def test_waiting_ci_label_result_appends_ready_code_and_uses_bound_arguments(self):
        result, label_call, store, get, runner = self._advance_with_missing_gates(
            ['CI_WORKFLOW_JOBS_INVALID'], {'step': 'WAITING_CI', 'codes': []})

        self.assertEqual(result['step'], 'WAITING_CI')
        self.assertIn('MAC_CI_LABEL_READY', result['codes'])
        label_call.assert_called_once_with(task_id=store.record['task_id'],
            store=store, pr_number=17, github_get=get,
            merge_gh_runner=runner, cwd='/repo/task')

    def test_merge_refused_label_result_keeps_waiting_ci_and_appends_codes(self):
        result, label_call, *_ = self._advance_with_missing_gates(
            ['CI_WORKFLOW_JOBS_INVALID'],
            {'step': 'MERGE_REFUSED', 'codes': ['MERGE_MAC_CI_LABEL_FAILED']})

        self.assertEqual(result['step'], 'WAITING_CI')
        self.assertIn('MERGE_MAC_CI_LABEL_FAILED', result['codes'])
        label_call.assert_called_once()

    def test_none_label_result_leaves_completion_codes_unchanged(self):
        result, label_call, *_ = self._advance_with_missing_gates(
            ['CI_WORKFLOW_JOBS_INVALID', 'REQUIRED_CI_FAILED'], None)

        self.assertEqual(result['step'], 'CI_FAILED')
        self.assertEqual(result['codes'], ['CI_WORKFLOW_JOBS_INVALID',
                                           'REQUIRED_CI_FAILED'])
        label_call.assert_called_once()

    def test_label_exception_keeps_waiting_ci_and_appends_failure_code(self):
        result, label_call, *_ = self._advance_with_missing_gates(
            ['CI_WORKFLOW_JOBS_INVALID'], label_error=RuntimeError('label failed'))

        self.assertEqual(result['step'], 'WAITING_CI')
        self.assertEqual(result['codes'], ['CI_WORKFLOW_JOBS_INVALID',
                                           'MAC_CI_LABEL_FAILED'])
        label_call.assert_called_once()

    def test_other_missing_gates_do_not_call_mac_label_helper(self):
        result, label_call, *_ = self._advance_with_missing_gates(
            ['EXECUTOR_RESULT_PENDING'])

        self.assertEqual(result['step'], 'WAITING_EXECUTOR')
        label_call.assert_not_called()

    def test_label_helper_uses_only_the_mac_ci_pr_edit_arguments(self):
        record = base_record()

        class Store:
            def get(self, _task_id):
                return record

        current_pr = dict(PR, state='open', mergeable=True, labels=[])
        calls = []

        def runner(root, argv):
            calls.append((root, argv))
            return subprocess.CompletedProcess(argv, 0)

        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=current_pr), \
             patch.object(task_advance, '_merge_checks',
                          return_value='MERGE_MACOS_CHECK_SKIPPED'):
            result = task_advance.label_mac_ci_for_skipped_check(
                task_id=record['task_id'], store=Store(), pr_number=17,
                github_get=lambda _path: current_pr,
                merge_gh_runner=runner, cwd='/repo/task')

        self.assertEqual(result['step'], 'WAITING_CI')
        self.assertEqual(calls, [('/repo/task',
            ['pr', 'edit', '17', '--add-label', 'mac-ci'])])


class TaskAdvanceCodexVerdictTests(unittest.TestCase):
    def _advance_dispatch_result(self, reply, gh_runner):
        record = base_record()

        class Store:
            def get(self, _task_id):
                return record

        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_review_outcomes', return_value=[]):
            result = task_advance.advance(
                task_id=record['task_id'], store=Store(), pr_number=17,
                github_get=green_linux_ci,
                dispatch_review_call=lambda **_kwargs: reply,
                publish_gh_runner=gh_runner)
        return result

    def test_completed_review_posts_verbatim_comment_and_bounds_result(self):
        review_text = 'Verdict: PASS\nFORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n'
        calls = []

        def runner(root, argv):
            calls.append((root, argv))
            return subprocess.CompletedProcess(argv, 0)

        result = self._advance_dispatch_result(
            {'state': 'COMPLETED', 'verdict': 'PASS', 'review_text': review_text}, runner)

        self.assertEqual(calls, [('/repo/task',
            ['pr', 'comment', '17', '--body', review_text])])
        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        self.assertEqual(result['result'], {'state': 'COMPLETED', 'verdict': 'PASS'})

    def test_withheld_and_dispatched_results_do_not_post_comments(self):
        for reply in (
                {'state': 'COMPLETED', 'verdict': 'BLOCKED',
                 'review_dispatch_code': 'REVIEW_TEXT_WITHHELD'},
                {'state': 'COMPLETED', 'verdict': 'PASS', 'review_text': ''},
                {'state': 'DISPATCHED', 'verdict': 'PENDING'}):
            with self.subTest(state=reply['state'], verdict=reply['verdict']):
                calls = []
                result = self._advance_dispatch_result(reply, lambda *_args: calls.append(_args))
                self.assertEqual(calls, [])
                self.assertEqual(result['step'], 'DISPATCH_REVIEW')
                self.assertNotIn('review_text', result['result'])

    def test_comment_failure_and_exception_keep_dispatch_step_and_append_code(self):
        review = {'state': 'COMPLETED', 'verdict': 'FAIL', 'review_text': 'Verdict: FAIL'}
        runners = (
            lambda *_args: subprocess.CompletedProcess([], 1),
            lambda *_args: (_ for _ in ()).throw(RuntimeError('comment failed')),
        )
        for runner in runners:
            with self.subTest(runner=runner):
                result = self._advance_dispatch_result(review, runner)
                self.assertEqual(result['step'], 'DISPATCH_REVIEW')
                self.assertIn('REVIEW_VERDICT_COMMENT_FAILED', result['codes'])
                self.assertEqual(result['result'], {'state': 'COMPLETED', 'verdict': 'FAIL'})


TASK, RUN, DIGEST = '1' * 32, '2' * 32, 'd' * 64
THREAD = '0123456789abcdef0123'
MARKER = '<!-- activity: type=review_verdict actor=Codex target=PM status=FIX -->'
STATUS = f'Verdict: FAIL | Version: v1 | HEAD: {HEAD}'
BINDING = (f'LLMTOOL-REVIEW-BINDING v1 task={TASK} run={RUN} '
           f'contract={DIGEST} head={HEAD}')
MODEL_TEXT = 'SECRET-MODEL-TEXT /Users/someone/private/path'


def codex_jsonl(*events):
    return '\n'.join(json.dumps(event) for event in events).encode('utf-8')


def thread_started(thread=THREAD):
    return {'type': 'thread.started', 'thread_id': thread}


def agent_message(text):
    return {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': text}}


def bound_verdict(**changes):
    verdict = {'task_id': TASK, 'run_id': RUN, 'contract_digest': DIGEST, 'head': HEAD,
               'verdict': 'FAIL', 'review': '\n'.join((MARKER, STATUS, BINDING))}
    verdict.update(changes)
    return json.dumps(verdict)


def unusable_claim(version=1, reason='NO_FINAL_MESSAGE', head=HEAD):
    claim = {'executor_task': 'executor-1', 'pr': 17, 'head_sha': head,
             'version': version, 'state': 'UNKNOWN', 'reviewer_task': f'reviewer-{version}',
             'native_session_id': None, 'account_mode': 'ROLES',
             'claimed_at': '2026-11-07T00:00:00Z', 'updated_at': '2026-11-07T00:00:01Z'}
    if reason is not None:
        claim['failure_reason'] = reason
    return claim


class ClaimStore:
    def __init__(self, record):
        self.record = record
        self.writes = 0

    def get(self, _task_id):
        return self.record

    def compare_and_update(self, _task_id, *, expected, **changes):
        if any(self.record.get(key) != value for key, value in expected.items()):
            raise RuntimeError('conflict')
        self.writes += 1
        self.record = dict(self.record, **changes)
        self.record['record_sha256'] = f'{self.writes:064x}'
        return self.record


class CodexReviewFailureReasonTests(unittest.TestCase):
    """#1105: each refusal of a finished Codex run names its fixed check."""

    def _reason(self, raw):
        with self.assertRaises(LLMToolError) as caught:
            review_dispatch._parse_codex_review(raw, task_id=TASK, run_id=RUN,
                                                contract_digest=DIGEST, head=HEAD)
        self.assertEqual(caught.exception.review_dispatch_code, 'REVIEW_NOT_POSTED')
        return caught.exception.review_failure_reason

    def test_each_refusal_point_records_its_fixed_reason(self):
        two_lines = '\n'.join((MARKER, STATUS))
        cases = {
            'OUTPUT_UNREADABLE': 'not bytes',
            'OUTPUT_NOT_JSONL': b'not json at all\n',
            'THREAD_ID_INVALID': codex_jsonl(thread_started('bad')),
            'MESSAGE_NOT_TEXT': codex_jsonl(thread_started(), {
                'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 5}}),
            'NO_FINAL_MESSAGE': codex_jsonl(thread_started()),
            'NO_THREAD_ID': codex_jsonl(agent_message(bound_verdict())),
            'VERDICT_NOT_JSON': codex_jsonl(thread_started(), agent_message(MODEL_TEXT)),
            'VERDICT_WRONG_FIELDS': codex_jsonl(thread_started(), agent_message(
                json.dumps({'verdict': 'PASS', 'review': MODEL_TEXT}))),
            'VERDICT_BINDING_MISMATCH': codex_jsonl(thread_started(), agent_message(
                bound_verdict(head='b' * 40))),
            'VERDICT_STATUS_INVALID': codex_jsonl(thread_started(), agent_message(
                bound_verdict(verdict='MAYBE'))),
            'VERDICT_TEXT_INVALID': codex_jsonl(thread_started(), agent_message(
                bound_verdict(review=7))),
            'REVIEW_HEADER_MISMATCH': codex_jsonl(thread_started(), agent_message(
                bound_verdict(review=MODEL_TEXT))),
            'REVIEW_BINDING_LINE_MISSING': codex_jsonl(thread_started(), agent_message(
                bound_verdict(review=two_lines + '\n' + MODEL_TEXT))),
        }
        for expected, raw in cases.items():
            with self.subTest(reason=expected):
                self.assertEqual(self._reason(raw), expected)
        with patch('llmtool.review_result._comment_verdict', return_value=None):
            rejected = self._reason(codex_jsonl(thread_started(),
                                                agent_message(bound_verdict())))
        self.assertEqual(rejected, 'REVIEW_CLASSIFIER_REJECTED')
        self.assertEqual(set(cases) | {rejected} | review_dispatch._D6_CREATE_FAILURE_REASONS |
                         {'REVIEW_PROMPT_REFUSED'},
                         review_dispatch.REVIEW_FAILURE_REASONS)

    def _dispatch(self, runner):
        record = base_record(
            record_sha256='f' * 64, task_binding={'contract_digest': DIGEST},
            developer={'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'claude'})
        store = ClaimStore(record)
        executor = type('Executor', (), {'common_dir': '/repo/.git', 'root': '/repo/task'})()
        reviewer = type('Reviewer', (), {'root': '/repo/reviewer'})()
        policy = type('Policy', (), {'allowed': True, 'reason_code': None})()
        role = {'source': 'roles', 'provider': 'codex', 'config_dir': None,
                'model': None, 'effort': None}
        with patch.object(review_dispatch, '_preflight',
                          return_value=(record, executor, HEAD, 'c' * 40, 'codex-reviewer')), \
             patch.object(review_dispatch, '_policy', return_value=policy), \
             patch.object(review_dispatch.role_config, 'resolve_role', return_value=role), \
             patch('llmtool.codex_direct_developer.validate_profile'), \
             patch.object(review_dispatch, '_resolve_github_login', return_value='login'), \
             patch.object(review_dispatch.task_completion, 'read_d6_registration',
                          return_value=reviewer):
            with self.assertRaises(LLMToolError) as caught:
                review_dispatch.dispatch_review(
                    store=store, task_id=TASK, executor_task='executor-1', pr_number=17,
                    version=1, create_d6=lambda **_kwargs: None,
                    prompt_renderer=lambda *_args: 'prompt', codex_runner=runner,
                    bind_session=lambda **_kwargs: self.fail('an unusable run was bound'))
        return store.record['review_dispatch_claim'], caught.exception

    def test_unusable_run_records_only_the_fixed_reason_in_the_claim(self):
        raw = codex_jsonl(thread_started(), agent_message(MODEL_TEXT))

        def runner(*, head, version, **_kwargs):
            return review_dispatch._parse_codex_review(
                raw, task_id=TASK, run_id=RUN, contract_digest=DIGEST,
                head=head, version=version)

        claims, error = self._dispatch(runner)
        self.assertEqual(error.review_dispatch_code, 'REVIEW_NOT_POSTED')
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]['state'], 'UNKNOWN')
        self.assertEqual(claims[0]['failure_reason'], 'VERDICT_NOT_JSON')
        stored = json.dumps(claims)
        self.assertNotIn('SECRET-MODEL-TEXT', stored)
        self.assertNotIn('/Users/someone', stored)

    def test_failures_without_a_fixed_reason_store_none(self):
        for code in ('REVIEW_NOT_POSTED', 'REVIEWER_TIMEOUT'):
            with self.subTest(code=code):
                def runner(**_kwargs):
                    review_dispatch._refuse(code, 'Codex Reviewer exited unsuccessfully')
                claims, error = self._dispatch(runner)
                self.assertEqual(error.review_dispatch_code, code)
                self.assertEqual(claims[0]['state'], 'UNKNOWN')
                self.assertNotIn('failure_reason', claims[0])

    def test_failure_reason_persists_through_real_task_store(self):
        from llmtool.task_store import TaskStore, new_id
        request = 'Bounded non-sensitive review retry task.'
        task_id, run_id = new_id(), new_id()
        task = task_submission_cli._record(
            task_id, run_id, 'pm-' + new_id(), 'pm-profile', request,
            state='PM_RESPONDED', provider_type='openai-compatible', model='pm-model',
            backend='NO_NATIVE_SESSION_ID_EXPOSED',
            request_sha=hashlib.sha256(request.encode()).hexdigest())
        task.update(pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                                      'base_url': None},
                    response='PM returned a bounded task.', error_code=None,
                    pm_assignment={'schema_version': 1, 'route': 'codex', 'risk': 'LOW',
                                   'goal': 'Retry an unusable review.',
                                   'acceptance': ['the failure reason is recorded']})
        with tempfile.TemporaryDirectory() as temp:
            store = TaskStore(Path(temp).resolve() / 'tasks')
            store.create(task)
            # The first write checks the plain claim shape, the second the new
            # field, on the store's one allowed path: CLAIMED, then UNKNOWN.
            plain = dict(unusable_claim(reason=None), state='CLAIMED')
            store.compare_and_update(task_id, expected={
                'record_sha256': store.get(task_id)['record_sha256']},
                review_dispatch_claim=[plain])
            store.compare_and_update(task_id, expected={'review_dispatch_claim': [plain]},
                                     review_dispatch_claim=[unusable_claim()])
            self.assertEqual(store.get(task_id)['review_dispatch_claim'][0]['failure_reason'],
                             'NO_FINAL_MESSAGE')


class TaskAdvanceUnusableReviewTests(unittest.TestCase):
    """#1105: one safe redispatch on the same head, then a named stop."""

    def _advance(self, store, *, comments=(), pr=PR, dispatch=None):
        dispatched, reads = [], []

        def github_get(path):
            reads.append(path)
            if path.startswith('issues/'):
                if comments is None:
                    raise RuntimeError('comments unavailable')
                return list(comments)
            return green_linux_ci(path, pr)

        def default_dispatch(**kwargs):
            return {'state': 'COMPLETED', 'verdict': 'FAIL'}

        def record_dispatch(**kwargs):
            dispatched.append(kwargs)
            return (dispatch or default_dispatch)(**kwargs)

        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_review_outcomes', return_value=[]):
            result = task_advance.advance(task_id=TASK, store=store, pr_number=17,
                                          github_get=github_get,
                                          dispatch_review_call=record_dispatch)
        return result, dispatched, reads

    def test_decide_offers_one_redispatch_then_stops(self):
        one = base_record(review_dispatch_claim=[unusable_claim()])
        self.assertEqual(task_advance.decide(one, d6(), PR, {}), 'REVIEW_REDISPATCH')
        two = base_record(review_dispatch_claim=[
            unusable_claim(1, 'VERDICT_NOT_JSON'), unusable_claim(2)])
        self.assertEqual(task_advance.decide(two, d6(), PR, {}), 'REVIEW_UNUSABLE_STOPPED')
        merged = dict(PR, merged=True)
        self.assertEqual(task_advance.decide(one, d6(), merged, {}), 'PR_MERGED_NO_REVIEW')

    def test_unusable_run_with_no_posted_verdict_redispatches_once_on_same_head(self):
        store = ClaimStore(base_record(review_dispatch_claim=[unusable_claim()]))
        result, dispatched, reads = self._advance(store)
        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        self.assertEqual(result['codes'], ['REVIEW_REDISPATCHED', 'NO_FINAL_MESSAGE'])
        self.assertEqual(len(dispatched), 1)
        self.assertEqual((dispatched[0]['pr_number'], dispatched[0]['version']), (17, 2))
        self.assertIn('issues/17/comments?per_page=100', reads)

    def test_redispatch_version_is_new_across_all_claims(self):
        older = dict(unusable_claim(1, None, head='b' * 40), state='COMPLETED')
        store = ClaimStore(base_record(review_dispatch_claim=[older, unusable_claim(2)]))
        result, dispatched, _ = self._advance(store)
        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        self.assertEqual(dispatched[0]['version'], 3)

    def test_other_version_comment_does_not_block_redispatch(self):
        store = ClaimStore(base_record(review_dispatch_claim=[unusable_claim()]))
        other = {'body': f'Verdict: FAIL | Version: v7 | HEAD: {HEAD}'}
        result, dispatched, _ = self._advance(store, comments=[other])
        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        self.assertEqual(len(dispatched), 1)

    def test_possible_post_keeps_outcome_unknown_and_refuses(self):
        cases = {
            'verdict comment': [{'body': '\n'.join((MARKER, STATUS, BINDING))}],
            'binding line only': [{'body': BINDING}],
            'unreadable body': [{'body': None}],
            'full page': [{'body': 'unrelated'}] * 100,
            'read failed': None,
        }
        for name, comments in cases.items():
            with self.subTest(case=name):
                store = ClaimStore(base_record(review_dispatch_claim=[unusable_claim()]))
                result, dispatched, _ = self._advance(store, comments=comments)
                self.assertEqual(dispatched, [])
                self.assertEqual(result['step'], 'REVIEW_OUTCOME_UNKNOWN')
                self.assertEqual(result['codes'],
                                 ['REVIEW_OUTCOME_UNKNOWN', 'NO_FINAL_MESSAGE'])

    def test_unknown_without_fixed_reason_refuses_without_reading_comments(self):
        for reason in (None, MODEL_TEXT):
            with self.subTest(reason=reason):
                store = ClaimStore(base_record(
                    review_dispatch_claim=[unusable_claim(reason=reason)]))
                result, dispatched, reads = self._advance(store)
                self.assertEqual(dispatched, [])
                self.assertEqual(result['step'], 'REVIEW_OUTCOME_UNKNOWN')
                self.assertEqual(result['codes'], ['REVIEW_OUTCOME_UNKNOWN'])
                self.assertFalse(any(path.startswith('issues/') for path in reads))

    def test_merged_pr_never_redispatches(self):
        store = ClaimStore(base_record(review_dispatch_claim=[unusable_claim()]))
        result, dispatched, _ = self._advance(store, pr=dict(PR, merged=True))
        self.assertEqual(dispatched, [])
        self.assertEqual(result['step'], 'PR_MERGED_NO_REVIEW')

    def test_second_unusable_run_stops_with_named_code_and_no_third_run(self):
        store = ClaimStore(base_record(review_dispatch_claim=[unusable_claim()]))

        def unusable_again(**kwargs):
            store.record = dict(store.record, review_dispatch_claim=(
                store.record['review_dispatch_claim'] +
                [unusable_claim(kwargs['version'], 'VERDICT_NOT_JSON')]))
            review_dispatch._refuse('REVIEW_NOT_POSTED', reason='VERDICT_NOT_JSON')

        first, dispatched, _ = self._advance(store, dispatch=unusable_again)
        self.assertEqual(first['step'], 'DISPATCH_REVIEW')
        self.assertEqual(first['codes'], ['REVIEW_REDISPATCHED', 'NO_FINAL_MESSAGE',
                                          'REVIEW_NOT_POSTED', 'VERDICT_NOT_JSON'])
        self.assertEqual(len(dispatched), 1)
        second, again, reads = self._advance(store, dispatch=unusable_again)
        self.assertEqual(again, [])
        self.assertEqual(second['step'], 'REVIEW_UNUSABLE_STOPPED')
        self.assertEqual(second['next'], 'REVIEW_UNUSABLE_STOPPED')
        self.assertEqual(second['codes'], ['REVIEW_UNUSABLE_STOPPED', 'VERDICT_NOT_JSON'])
        self.assertFalse(any(path.startswith('issues/') for path in reads))


class CreatedPrPublicationTests(unittest.TestCase):
    """#1139: only a verified PR this publish created may be closed."""

    REPO, BRANCH, STALE = 'owner/repo', 'feature/task', 'b' * 40
    TITLE, BODY = 'Task summary', 'Task body'
    URL = 'https://github.com/owner/repo/pull/41'
    FIELDS = {'task_id', 'repository', 'branch', 'pr', 'creation_url', 'expected_head',
              'observed_head', 'recheck', 'close_attempted', 'record_comment',
              'outcome', 'next_action'}

    def setUp(self):
        pause = patch.object(task_advance, '_publish_pause')
        pause.start()
        self.addCleanup(pause.stop)

    def view(self, **changes):
        view = {'number': 41, 'url': self.URL, 'headRefName': self.BRANCH,
                'headRefOid': self.STALE, 'isCrossRepository': False, 'state': 'OPEN',
                'author': {'login': 'ai-bot'}, 'title': self.TITLE, 'body': self.BODY}
        view.update(changes)
        return view

    def publish(self, *, before=(), listing=None, create_out=URL + '\n', create_code=0,
                view=None, view_code=0, repo=REPO, login='ai-bot', close_code=0,
                comment_code=0):
        calls, lists = [], [0]
        listing = [{'number': 41, 'headRefOid': self.STALE}] if listing is None else listing

        def reply(args, text='', code=0):
            return subprocess.CompletedProcess(['gh', *args], code, text.encode(), b'')

        def gh(_root, args):
            args = list(args)
            calls.append(args)
            if args[:2] == ['pr', 'list']:
                lists[0] += 1
                value = list(before) if lists[0] == 1 else listing
                return reply(args, value if type(value) is str else json.dumps(value))
            if args[:2] == ['pr', 'create']:
                return reply(args, create_out, create_code)
            if args[:2] == ['pr', 'view']:
                value = self.view() if view is None else view
                return reply(args, json.dumps(value), view_code)
            if args[:2] == ['repo', 'view']:
                return reply(args, json.dumps({'nameWithOwner': repo}))
            if args[:2] == ['api', 'user']:
                return reply(args, json.dumps({'login': login}))
            if args[:2] == ['pr', 'close']:
                return reply(args, '', close_code)
            if args[:2] == ['pr', 'comment']:
                return reply(args, '', comment_code)
            raise AssertionError(f'unexpected gh call: {args}')

        try:
            return task_advance._publish_pr(gh, '/d6', self.BRANCH, '1' * 32, HEAD,
                                            self.TITLE, self.BODY), None, calls
        except task_advance._PublishRefusal as exc:
            return None, exc, calls

    @staticmethod
    def sent(calls, verb):
        return [args for args in calls if args[:2] == ['pr', verb]]

    def test_unverified_or_foreign_pr_is_never_closed(self):
        cases = (
            ('another Task PR', {'view': self.view(title='Other task', body='Other body')},
             'PUBLISH_MOVED_PR_OPEN', 'FAIL:title', 41),
            ('another Task body', {'view': self.view(body='Other body')},
             'PUBLISH_MOVED_PR_OPEN', 'FAIL:body', 41),
            ('another call PR in listing and view',
             {'listing': [{'number': 40, 'headRefOid': HEAD}], 'view': self.view(number=40)},
             'PUBLISH_MOVED_PR_OPEN', 'FAIL:number', 41),
            ('another user PR', {'login': 'someone-else'},
             'PUBLISH_MOVED_PR_OPEN', 'FAIL:author', 41),
            ('no author', {'view': self.view(author=None)},
             'PUBLISH_MOVED_PR_OPEN', 'FAIL:author', 41),
            ('another repository', {'repo': 'other/repo'},
             'PUBLISH_MOVED_PR_OPEN', 'FAIL:nameWithOwner', 41),
            ('url in another repository',
             {'view': self.view(url='https://github.com/other/repo/pull/41')},
             'PUBLISH_MOVED_PR_OPEN', 'FAIL:url', 41),
            ('another branch', {'view': self.view(headRefName='other/branch')},
             'PUBLISH_MOVED_PR_OPEN', 'FAIL:headRefName', 41),
            ('cross-repository PR', {'view': self.view(isCrossRepository=True)},
             'PUBLISH_MOVED_PR_OPEN', 'FAIL:isCrossRepository', 41),
            ('closed PR', {'view': self.view(state='CLOSED')},
             'PUBLISH_MOVED_PR_OPEN', 'FAIL:state', 41),
            ('unreadable head', {'view': self.view(headRefOid='zz')},
             'PUBLISH_MOVED_PR_OPEN', 'FAIL:headRefOid', 41),
            ('failed view', {'view_code': 1}, 'PUBLISH_MOVED_PR_OPEN', 'FAIL:view', 41),
            ('malformed view', {'view': []}, 'PUBLISH_MOVED_PR_OPEN', 'FAIL:view', 41),
            ('failed list after create', {'listing': 'not json'},
             'PUBLISH_GITHUB_FAILED', 'FAIL:list', 41),
            ('create without URL', {'create_out': ''},
             'PUBLISH_GITHUB_FAILED', 'FAIL:creation_url', None),
            ('create with a non-URL last line', {'create_out': self.URL + '\nWarning: x\n'},
             'PUBLISH_GITHUB_FAILED', 'FAIL:creation_url', None),
        )
        for label, kwargs, code, recheck, number in cases:
            with self.subTest(label):
                value, exc, calls = self.publish(**kwargs)
                self.assertIsNone(value)
                self.assertEqual(exc.code, code)
                self.assertEqual(self.sent(calls, 'close'), [])
                self.assertEqual(self.sent(calls, 'comment'), [])
                operation = exc.operation
                self.assertEqual(set(operation), self.FIELDS)
                self.assertEqual((operation['recheck'], operation['pr'],
                                  operation['close_attempted'], operation['record_comment']),
                                 (recheck, number, False, 'NOT_POSTED'))
                self.assertNotEqual(operation['outcome'], 'CLOSED')
                if number is None:
                    self.assertEqual(self.sent(calls, 'view'), [])

    def test_verified_created_pr_closes_once_with_exact_args_then_records(self):
        for label, close_code, comment_code, code, outcome, comment in (
                ('closed', 0, 0, 'PUBLISH_HEAD_MOVED', 'CLOSED', 'POSTED'),
                ('close failed', 1, 0, 'PUBLISH_MOVED_PR_OPEN', 'CLOSE_FAILED_LEFT_OPEN', 'POSTED'),
                ('record comment failed', 0, 1, 'PUBLISH_HEAD_MOVED', 'CLOSED', 'FAILED')):
            with self.subTest(label):
                value, exc, calls = self.publish(close_code=close_code,
                                                 comment_code=comment_code)
                self.assertIsNone(value)
                self.assertEqual(exc.code, code)
                self.assertEqual(self.sent(calls, 'close'),
                                 [['pr', 'close', '41', '--repo', self.REPO]])
                self.assertTrue(all('--delete-branch' not in args for args in calls))
                self.assertFalse(any(args[0] in ('branch', 'push', 'api') and
                                     args[:2] != ['api', 'user'] for args in calls))
                comments = self.sent(calls, 'comment')
                self.assertEqual(len(comments), 1)
                self.assertEqual(comments[0][:5], ['pr', 'comment', '41', '--repo', self.REPO])
                self.assertEqual(comments[0][5], '--body')
                self.assertGreater(calls.index(comments[0]), calls.index(self.sent(calls, 'close')[0]))
                marker, record = comments[0][6].split('\n', 1)
                self.assertEqual(marker, task_advance._ACTIVITY_MARKER)
                self.assertEqual(json.loads(record)['outcome'], outcome)
                operation = exc.operation
                self.assertEqual(operation, {
                    'task_id': '1' * 32, 'repository': self.REPO, 'branch': self.BRANCH,
                    'pr': 41, 'creation_url': self.URL, 'expected_head': HEAD,
                    'observed_head': self.STALE, 'recheck': 'PASS', 'close_attempted': True,
                    'record_comment': comment, 'outcome': outcome,
                    'next_action': operation['next_action']})
                self.assertIsInstance(operation['next_action'], str)

    def test_matching_created_pr_is_returned_and_nothing_is_closed(self):
        cases = (
            ('listing matches', {'listing': [{'number': 41, 'headRefOid': HEAD}]}, True),
            ('listing matches without URL',
             {'create_out': '', 'listing': [{'number': 41, 'headRefOid': HEAD}]}, True),
            ('other PR listed first',
             {'listing': [{'number': 40, 'headRefOid': HEAD},
                          {'number': 41, 'headRefOid': HEAD}]}, True),
            ('view head caught up',
             {'listing': [{'number': 40, 'headRefOid': HEAD}],
              'view': self.view(headRefOid=HEAD)}, False),
        )
        for label, kwargs, listed in cases:
            with self.subTest(label):
                value, exc, calls = self.publish(**kwargs)
                self.assertIsNone(exc)
                self.assertEqual(value, 41)
                self.assertEqual(self.sent(calls, 'close'), [])
                self.assertEqual(self.sent(calls, 'view') == [], listed)

    def test_existing_pr_or_failed_create_keeps_todays_refusal(self):
        for label, kwargs in (
                ('PR open before this call', {'before': [{'number': 41, 'headRefOid': self.STALE}]}),
                ('failed create', {'create_code': 1})):
            with self.subTest(label):
                value, exc, calls = self.publish(**kwargs)
                self.assertIsNone(value)
                self.assertEqual(exc.code, 'PUBLISH_GITHUB_FAILED')
                self.assertIsNone(exc.operation)
                self.assertEqual(self.sent(calls, 'close'), [])
                self.assertEqual(self.sent(calls, 'view'), [])
        _, _, calls = self.publish(before=[{'number': 41, 'headRefOid': self.STALE}])
        self.assertEqual(self.sent(calls, 'create'), [])

    def test_refusal_result_carries_and_saves_the_operation(self):
        operation = dict.fromkeys(self.FIELDS)
        operation.update(pr=41, recheck='FAIL:state', close_attempted=False)
        class Store:
            def __init__(self, fail=False):
                self.fail, self.saved = fail, []
            def record_publish_operation(self, task_id, item):
                if self.fail:
                    raise RuntimeError('busy')
                self.saved.append((task_id, item))
        store = Store()
        plain = task_advance._publish_refusal_result(
            store, 't', task_advance._PublishRefusal('PUBLISH_GITHUB_FAILED'))
        self.assertEqual(plain, {'task_id': 't', 'codes': ['PUBLISH_GITHUB_FAILED']})
        self.assertEqual(store.saved, [])
        result = task_advance._publish_refusal_result(
            store, 't', task_advance._PublishRefusal('PUBLISH_MOVED_PR_OPEN', operation))
        self.assertEqual(result['codes'], ['PUBLISH_MOVED_PR_OPEN'])
        self.assertEqual(result['publish_operation'], operation)
        self.assertEqual(result['open_pr']['number'], 41)
        self.assertNotIn('pr', result)
        self.assertEqual(store.saved, [('t', operation)])
        failed = task_advance._publish_refusal_result(
            Store(fail=True), 't', task_advance._PublishRefusal('PUBLISH_HEAD_MOVED', operation))
        self.assertEqual(failed['codes'], ['PUBLISH_HEAD_MOVED', 'PUBLISH_OPERATION_RECORD_FAILED'])
        self.assertEqual(failed['publish_operation'], operation)
        self.assertNotIn('open_pr', failed)


class MergeNewerClaimVetoTests(unittest.TestCase):
    """#1139: any newer claim on the same PR and head vetoes an older PASS."""

    @staticmethod
    def claim(version, state='COMPLETED', verdict='PASS', head=HEAD, pr=17):
        claim = {'pr': pr, 'head_sha': head, 'version': version, 'state': state}
        if verdict is not None:
            claim['review_result'] = {'verdict': verdict}
        return claim

    @staticmethod
    def event(version, outcome='REVIEW_PASS', head=HEAD):
        return {'source': 'REVIEWER', 'source_sha': head, 'version': version,
                'outcome': outcome}

    def decide(self, claims, events):
        return task_advance._merge_pass_claim(
            {'review_dispatch_claim': claims}, 17, HEAD, events)

    def test_newer_claim_in_any_state_or_bad_version_refuses(self):
        cases = [(f'newer {state} without event', [self.claim(1), self.claim(2, state, None)],
                  [self.event(1)]) for state in
                 ('CLAIMED', 'RUNNING', 'UNKNOWN', 'DISPATCHED', 'COMPLETED')]
        cases += [
            ('newer claim with FAIL event',
             [self.claim(1), self.claim(2, verdict='FIX')],
             [self.event(1), self.event(2, 'REVIEW_FAIL')]),
            ('newer CLAIMED with FAIL event',
             [self.claim(1), self.claim(2, 'CLAIMED', None)],
             [self.event(1), self.event(2, 'REVIEW_FAIL')]),
        ]
        cases += [(f'version {version!r}', [self.claim(1), self.claim(version, 'CLAIMED', None)],
                   [self.event(1)]) for version in (None, '2', 2.0, True)]
        cases.append(('missing version', [self.claim(1), {'pr': 17, 'head_sha': HEAD,
                                                          'state': 'CLAIMED'}], [self.event(1)]))
        for label, claims, events in cases:
            with self.subTest(label):
                self.assertEqual(self.decide(claims, events), 'MERGE_NEWER_REVIEW_EXISTS')

    def test_highest_pass_merges_and_other_heads_do_not_veto(self):
        cases = (
            ('single PASS', [self.claim(1)], [self.event(1)], 1),
            ('FAIL v1 then PASS v2', [self.claim(1, verdict='FIX'), self.claim(2)],
             [self.event(1, 'REVIEW_FAIL'), self.event(2)], 2),
            ('newer claim on another head', [self.claim(1), self.claim(2, 'CLAIMED', None,
                                                                    head='b' * 40)],
             [self.event(1)], 1),
            ('newer claim on another PR', [self.claim(1), self.claim(2, 'CLAIMED', None, pr=18)],
             [self.event(1)], 1),
            ('bad version on another head', [self.claim(1), self.claim('x', head='b' * 40)],
             [self.event(1)], 1),
            ('older claim in any state', [self.claim(0, 'UNKNOWN', None), self.claim(1)],
             [self.event(1)], 1),
            ('no PASS claim', [self.claim(1, verdict='FIX')], [self.event(1, 'REVIEW_FAIL')],
             'MERGE_REVIEW_NOT_PASS'),
            ('no PASS with newer claim', [self.claim(1, verdict='FIX'),
                                          self.claim(2, 'CLAIMED', None)],
             [self.event(1, 'REVIEW_FAIL')], 'MERGE_REVIEW_NOT_PASS'),
        )
        for label, claims, events, expected in cases:
            with self.subTest(label):
                self.assertEqual(self.decide(claims, events), expected)

    def test_merge_once_refusal_sends_no_claim_and_no_merge(self):
        class Store:
            def __init__(self):
                self.attempts = []
            def compare_and_update(self, _task_id, *, expected, merge_attempt):
                self.attempts.append(merge_attempt)
        pr = {'number': 17, 'head': {'sha': HEAD}, 'state': 'open', 'merged': False,
              'mergeable': True, 'labels': [{'name': 'mac-ci'}], 'body': ''}
        github_get = with_current_main(lambda path: (
            {'total_count': 2, 'check_runs': [
                {'head_sha': HEAD, 'name': name, 'status': 'completed', 'conclusion': 'success'}
                for name in ('check (Linux 3.14)', 'check (macOS 3.13)')]}
            if path.startswith('commits/') else
            [{'filename': 'src/module.py'}] if path.endswith('/files?per_page=100') else
            [] if path.endswith('/comments?per_page=100') else pr))
        for label, newer in (('newer CLAIMED', self.claim(2, 'CLAIMED', None)),
                             ('non-integer version', self.claim('2', 'RUNNING', None))):
            with self.subTest(label):
                store, calls = Store(), []
                record = {'task_id': '1' * 32, 'record_sha256': 'r' * 64,
                          'review_dispatch_claim': [self.claim(1), newer]}
                result = task_advance._merge_once(store=store, record=record, number=17,
                    head=HEAD, pr=pr, events=[self.event(1)], github_get=github_get,
                    gh_runner=lambda _root, args: calls.append(args), root='/repo')
                self.assertEqual(result['codes'], ['MERGE_NEWER_REVIEW_EXISTS'])
                self.assertEqual(store.attempts, [])
                self.assertEqual(calls, [])


class ScriptsOnlyCiTests(unittest.TestCase):
    """#1208: a scripts/-only PR requires Linux CI and macOS CI like any code PR."""

    PR = {'number': 17, 'head': {'sha': HEAD}, 'state': 'open', 'mergeable': True}

    @staticmethod
    def reader(paths, names, conclusion='success'):
        runs = [{'id': index + 1, 'head_sha': HEAD, 'name': name, 'status': 'completed',
                 'conclusion': conclusion} for index, name in enumerate(names)]
        def get(path):
            if path.startswith('commits/'):
                return {'total_count': len(runs), 'check_runs': runs}
            if path.endswith('/files?per_page=100'):
                return [{'filename': item} for item in paths]
            raise AssertionError(path)
        return get

    def test_merge_and_review_ci_rules_by_changed_paths(self):
        scripts = ['scripts/llmtool_monitor.py', 'scripts/tool.sh']
        code_checks = ['check (Linux 3.14)', 'check (macOS 3.13)']
        cases = (
            ('scripts only with Linux and macOS', scripts, code_checks, 'success',
             None, (None, None)),
            ('scripts only without checks', scripts, [], 'success',
             'MERGE_CHECKS_MISSING', ('WAITING_CI', 'LINUX_CI_PENDING')),
            ('scripts only with Linux only', scripts, ['check (Linux 3.14)'], 'success',
             'MERGE_REQUIRED_CHECK_MISSING', (None, None)),
            ('scripts only with macOS only', scripts, ['check (macOS 3.13)'], 'success',
             'MERGE_REQUIRED_CHECK_MISSING', ('WAITING_CI', 'LINUX_CI_PENDING')),
            ('scripts only with skipped checks', scripts, code_checks, 'skipped',
             'MERGE_REQUIRED_CHECK_SKIPPED', ('CI_FAILED', 'LINUX_CI_FAILED')),
            ('one other path', scripts + ['src/module.py'], ['check (macOS 3.13)'], 'success',
             'MERGE_REQUIRED_CHECK_MISSING', ('WAITING_CI', 'LINUX_CI_PENDING')),
            ('docs only', ['docs/guide.md'], ['check (macOS 3.13)'], 'success',
             'MERGE_REQUIRED_CHECK_MISSING', ('WAITING_CI', 'LINUX_CI_PENDING')),
        )
        for label, paths, names, conclusion, merge_code, review in cases:
            with self.subTest(label):
                get = self.reader(paths, names, conclusion)
                self.assertEqual(task_advance._merge_checks({}, 17, HEAD, self.PR, get),
                                 merge_code)
                self.assertEqual(task_advance._review_ci_gate(17, HEAD, get), review)

    def test_review_of_scripts_only_pr_waits_for_exact_head_linux_check(self):
        scripts = ['scripts/llmtool_monitor.py', 'scripts/tool.sh']
        get_waiting = self.reader(scripts, ['check (macOS 3.13)'], 'success')
        self.assertEqual(task_advance._review_ci_gate(17, HEAD, get_waiting),
                         ('WAITING_CI', 'LINUX_CI_PENDING'))
        get_ready = self.reader(scripts, ['check (Linux 3.14)'], 'success')
        self.assertEqual(task_advance._review_ci_gate(17, HEAD, get_ready),
                         (None, None))


class IntegrationDeltaRealGitTests(unittest.TestCase):
    """#1139: an integration head closes only when it adds clean merges of main."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.realpath(tmp.name)
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Delta Test')
        self.git('config', 'user.email', 'delta-test@example.invalid')
        self.git('config', 'commit.gpgsign', 'false')
        self.commit('base.txt', 'base')
        self.git('checkout', '-b', 'side')
        self.commit('side.txt', 'side, never on main before the merge')
        self.side = self.rev('HEAD')
        self.git('checkout', '-b', 'feature', 'main')
        self.commit('feature.txt', 'reviewed')
        self.reviewed = self.rev('HEAD')
        self.git('checkout', 'main')
        self.commit('main.txt', 'main moved')
        self.git('checkout', 'feature')

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, check=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True).stdout

    def rev(self, ref):
        return self.git('rev-parse', ref).strip()

    def commit(self, name, text):
        with open(os.path.join(self.root, name), 'w', encoding='utf-8') as stream:
            stream.write(text + '\n')
        self.git('add', '--', name)
        self.git('commit', '-m', text)

    def integrate(self):
        """Merge feature into main as the integration PR; return (head, merge_sha)."""
        head = self.rev('feature')
        self.git('checkout', 'main')
        self.git('merge', '--no-ff', '--no-edit', 'feature')
        merge = self.rev('HEAD')
        self.git('checkout', 'feature')
        return head, merge

    def verified(self, head, merge, runner=None):
        return task_advance._integration_delta_verified(
            self.root, self.reviewed, head, merge, 27, runner)

    def test_clean_main_merge_closes(self):
        self.git('merge', '--no-edit', 'main')
        calls = []
        def runner(root, args):
            calls.append(list(args))
            return task_advance._publish_run_git(root, args)
        self.assertTrue(self.verified(*self.integrate(), runner))
        self.assertTrue(calls)
        self.assertFalse(any(args[0] == 'fetch' for args in calls))

    def test_extra_commit_after_reviewed_head_refuses(self):
        self.git('merge', '--no-edit', 'main')
        self.commit('feature.txt', 'unreviewed developer commit')
        self.assertFalse(self.verified(*self.integrate()))

    def test_merge_with_extra_change_refuses(self):
        self.git('merge', '--no-ff', '--no-commit', 'main')
        with open(os.path.join(self.root, 'extra.txt'), 'w', encoding='utf-8') as stream:
            stream.write('hidden in the merge\n')
        self.git('add', '--', 'extra.txt')
        self.git('commit', '--no-edit')
        self.assertFalse(self.verified(*self.integrate()))

    def test_merge_of_commit_outside_the_anchor_refuses(self):
        # After the integration merge, main contains side; the anchor is the
        # merge's first parent, which does not.
        self.git('merge', '--no-edit', 'side')
        self.assertFalse(self.verified(*self.integrate()))

    def test_missing_commit_fetches_once_then_refuses(self):
        _head, merge = self.integrate()
        calls = []
        def runner(root, args):
            calls.append(list(args))
            return task_advance._publish_run_git(root, args)
        self.assertFalse(self.verified('f' * 40, merge, runner))
        self.assertEqual([args for args in calls if args[0] == 'fetch'],
                         [['fetch', 'origin', 'main', 'refs/pull/27/head']])

    def test_exact_reviewed_head_and_failures(self):
        def broken(_root, _args):
            raise OSError('git unavailable')
        self.assertTrue(task_advance._integration_delta_verified(
            self.root, self.reviewed, self.reviewed, 'd' * 40, 27, broken))
        self.git('merge', '--no-edit', 'main')
        head, merge = self.integrate()
        for label, args in (
                ('runner raises', (self.root, self.reviewed, head, merge, 27, broken)),
                ('bad number', (self.root, self.reviewed, head, merge, 0, None)),
                ('bad merge sha', (self.root, self.reviewed, head, 'zz', 27, None)),
                ('missing root', (os.path.join(self.root, 'absent'), self.reviewed, head,
                                  merge, 27, None))):
            with self.subTest(label):
                self.assertFalse(task_advance._integration_delta_verified(*args))
        self.assertTrue(self.verified(head, merge))

    def test_ready_for_integration_unverified_delta_does_not_close(self):
        ready = base_record(review_dispatch_claim=[{
            'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'DISPATCHED'}],
            completion={'executor_task_id': 'executor-1', 'state': 'PM_EVIDENCE_READY',
                        'evidence': {'result': {'result_head': HEAD},
                                     'review_pr_ci': {'stage': 'READY'}}})
        integration = {'number': 27, 'merged': True,
                       'head': {'sha': 'c' * 40}, 'merge_commit_sha': 'd' * 40}
        saved = []
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot',
                          side_effect=lambda n, _: PR if n == 17 else integration), \
             patch.object(task_advance, '_integration_delta_verified',
                          return_value=False) as delta, \
             patch.object(task_advance, '_review_outcomes', return_value=[{
                 'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}]):
            result = task_advance.advance(task_id=ready['task_id'], store=type('S', (), {
                'get': lambda self, _id: ready})(), pr_number=17, integration_pr=27,
                github_get=lambda url: {'status': 'ahead'} if url.startswith('compare/') else [],
                record_github_event_call=lambda *args: saved.append(args))
        delta.assert_called()
        self.assertNotEqual(result['step'], 'CLOSED')
        self.assertIn('INTEGRATION_DELTA_UNVERIFIED', result['codes'])
        self.assertEqual(saved, [])


class TaskAdvanceReviewRefusalTests(unittest.TestCase):
    """#763 sub-PR 1180-c: review_refusal dict on refused DISPATCH_REVIEW."""

    def test_key_on_refused_review(self):
        record = base_record()
        store = ClaimStore(record)
        refusal = {
            'failure_reason': 'REVIEW_HEADER_MISMATCH',
            'excerpt': 'synthetic header mismatch excerpt',
            'receipt_path': '/repo/test_receipt.json',
        }

        def dispatch_stub(**_kwargs):
            err = LLMToolError('refused')
            err.review_dispatch_code = 'REVIEW_NOT_POSTED'
            err.review_failure_reason = 'REVIEW_HEADER_MISMATCH'
            err.review_refusal = dict(refusal)
            raise err

        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[]):
            result = task_advance.advance(
                task_id=record['task_id'], store=store, pr_number=17,
                github_get=green_linux_ci, dispatch_review_call=dispatch_stub)

        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        self.assertEqual(result['next'], 'DISPATCH_REVIEW')
        self.assertIsNone(result['result'])
        self.assertEqual(result['codes'], ['REVIEW_NOT_POSTED', 'REVIEW_HEADER_MISMATCH'])
        self.assertEqual(result['review_refusal'], refusal)

    def test_key_with_label_and_none_receipt(self):
        record = base_record()
        store = ClaimStore(record)
        refusal = {
            'failure_reason': 'REVIEW_PROMPT_REFUSED',
            'label': 'SYNTHETIC_SCREEN_LABEL',
            'receipt_path': None,
        }

        def dispatch_stub(**_kwargs):
            err = LLMToolError('refused')
            err.review_dispatch_code = 'REVIEW_NOT_POSTED'
            err.review_failure_reason = 'REVIEW_PROMPT_REFUSED'
            err.review_refusal = dict(refusal)
            raise err

        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[]):
            result = task_advance.advance(
                task_id=record['task_id'], store=store, pr_number=17,
                github_get=green_linux_ci, dispatch_review_call=dispatch_stub)

        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        self.assertEqual(result['review_refusal'], refusal)

    def test_absence_on_other_results(self):
        record = base_record()
        store = ClaimStore(record)

        # Successful review dispatch has no review_refusal key
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[]):
            ok_result = task_advance.advance(
                task_id=record['task_id'], store=store, pr_number=17,
                github_get=green_linux_ci,
                dispatch_review_call=lambda **_k: {'state': 'DISPATCHED'})
        self.assertEqual(ok_result['step'], 'DISPATCH_REVIEW')
        self.assertNotIn('review_refusal', ok_result)

        # Error without review_refusal attribute
        def no_refusal_err(**_k):
            err = LLMToolError('refused')
            err.review_dispatch_code = 'REVIEW_NOT_POSTED'
            err.review_failure_reason = 'REVIEW_HEADER_MISMATCH'
            raise err

        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[]):
            no_attr_result = task_advance.advance(
                task_id=record['task_id'], store=store, pr_number=17,
                github_get=green_linux_ci, dispatch_review_call=no_refusal_err)
        self.assertEqual(no_attr_result['step'], 'DISPATCH_REVIEW')
        self.assertNotIn('review_refusal', no_attr_result)

        # Malformed review_refusal dicts
        malformed_cases = (
            None,
            'not a dict',
            [],
            {'failure_reason': 123, 'excerpt': 'sample'},
            {'failure_reason': '', 'excerpt': 'sample'},
            {'failure_reason': 'REVIEW_HEADER_MISMATCH'},
            {'failure_reason': 'REVIEW_HEADER_MISMATCH', 'excerpt': 'sample', 'label': 'label'},
            {'failure_reason': 'REVIEW_HEADER_MISMATCH', 'excerpt': 123},
            {'failure_reason': 'REVIEW_HEADER_MISMATCH', 'label': 123},
            {'failure_reason': 'REVIEW_HEADER_MISMATCH', 'excerpt': 'sample', 'receipt_path': 123},
            {'failure_reason': 'REVIEW_HEADER_MISMATCH', 'excerpt': 'sample', 'extra_field': 'bad'},
        )
        for malformed in malformed_cases:
            with self.subTest(malformed=malformed):
                def malformed_err(**_k):
                    err = LLMToolError('refused')
                    err.review_dispatch_code = 'REVIEW_NOT_POSTED'
                    err.review_failure_reason = 'REVIEW_HEADER_MISMATCH'
                    err.review_refusal = malformed
                    raise err

                with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
                     patch.object(task_advance, '_pr_snapshot', return_value=PR), \
                     patch.object(task_advance, '_review_outcomes', return_value=[]):
                    bad_result = task_advance.advance(
                        task_id=record['task_id'], store=store, pr_number=17,
                        github_get=green_linux_ci, dispatch_review_call=malformed_err)
                self.assertEqual(bad_result['step'], 'DISPATCH_REVIEW')
                self.assertNotIn('review_refusal', bad_result)

        # Other advance results have no review_refusal key
        rec_needs_attention = base_record(remote_test_outcomes=[
            {'result': 'TIMEOUT', 'executor_task': 'executor-1',
             'sha': HEAD, 'modules': ['test_task_advance']},
            {'result': 'TIMEOUT', 'executor_task': 'executor-1',
             'sha': HEAD, 'modules': ['test_task_advance']},
        ])
        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR):
            attn_result = task_advance.advance(
                task_id=rec_needs_attention['task_id'],
                store=ClaimStore(rec_needs_attention), pr_number=17)
        self.assertEqual(attn_result['step'], 'NEEDS_ATTENTION')
        self.assertNotIn('review_refusal', attn_result)

    def test_real_task_store_accepts_claim_unchanged(self):
        from llmtool.task_store import TaskStore, new_id
        request = 'Bounded non-sensitive review retry task.'
        task_id, run_id = new_id(), new_id()
        task = task_submission_cli._record(
            task_id, run_id, 'pm-' + new_id(), 'pm-profile', request,
            state='PM_RESPONDED', provider_type='openai-compatible', model='pm-model',
            backend='NO_NATIVE_SESSION_ID_EXPOSED',
            request_sha=hashlib.sha256(request.encode()).hexdigest())
        task.update(pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                                      'base_url': None},
                    response='PM returned a bounded task.', error_code=None,
                    pm_assignment={'schema_version': 1, 'route': 'codex', 'risk': 'LOW',
                                   'goal': 'Retry an unusable review.',
                                   'acceptance': ['the failure reason is recorded']})

        with tempfile.TemporaryDirectory() as temp:
            store = TaskStore(Path(temp).resolve() / 'tasks')
            store.create(task)

            plain = dict(unusable_claim(reason=None), state='CLAIMED')
            store.compare_and_update(task_id, expected={
                'record_sha256': store.get(task_id)['record_sha256']},
                review_dispatch_claim=[plain])
            claim = unusable_claim(reason='REVIEW_HEADER_MISMATCH')
            store.compare_and_update(task_id, expected={'review_dispatch_claim': [plain]},
                                     review_dispatch_claim=[claim])

            persisted = store.get(task_id)
            persisted_claim = persisted['review_dispatch_claim'][0]
            self.assertEqual(persisted_claim['state'], 'UNKNOWN')
            self.assertEqual(persisted_claim['failure_reason'], 'REVIEW_HEADER_MISMATCH')
            self.assertNotIn('review_refusal', persisted_claim)
            self.assertNotIn('excerpt', persisted_claim)
            self.assertNotIn('review_refusal', persisted)

    def test_no_gh_call_carries_excerpt(self):
        record = base_record()
        store = ClaimStore(record)
        excerpt = 'synthetic refusal excerpt that must never leak to github'
        calls = []

        def gh_runner(root, args):
            calls.append(list(args))
            return subprocess.CompletedProcess(args, 0, b'[]', b'')

        def dispatch_stub(**_kwargs):
            err = LLMToolError('refused')
            err.review_dispatch_code = 'REVIEW_NOT_POSTED'
            err.review_failure_reason = 'REVIEW_HEADER_MISMATCH'
            err.review_refusal = {
                'failure_reason': 'REVIEW_HEADER_MISMATCH',
                'excerpt': excerpt,
                'receipt_path': None,
            }
            raise err

        with patch.object(task_advance, '_d6_snapshot', return_value=d6()), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[]):
            result = task_advance.advance(
                task_id=record['task_id'], store=store, pr_number=17,
                github_get=green_linux_ci, dispatch_review_call=dispatch_stub,
                publish_gh_runner=gh_runner, merge_gh_runner=gh_runner)

        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        self.assertEqual(result['review_refusal']['excerpt'], excerpt)
        for call_args in calls:
            for item in call_args:
                self.assertNotIn(excerpt, str(item))

    def test_pass_shaped_refused_output_is_not_verdict_at_decide_or_merge_gate(self):
        claim = {
            'executor_task': 'executor-1', 'pr': 17, 'head_sha': HEAD,
            'version': 1, 'state': 'UNKNOWN', 'failure_reason': 'REVIEW_HEADER_MISMATCH',
            'review_result': {'verdict': 'PASS'},
        }
        record = base_record(review_dispatch_claim=[claim])

        # decide() gives REVIEW_REDISPATCH for 1 claim, not READY_FOR_INTEGRATION or RUN_TASK_COMPLETE
        self.assertEqual(task_advance.decide(record, d6(), PR, {}), 'REVIEW_REDISPATCH')

        # On second claim on same head, decide() gives REVIEW_UNUSABLE_STOPPED
        claim2 = dict(claim, version=2)
        record2 = base_record(review_dispatch_claim=[claim, claim2])
        self.assertEqual(task_advance.decide(record2, d6(), PR, {}), 'REVIEW_UNUSABLE_STOPPED')

        # Merge gate: _merge_pass_claim ignores UNKNOWN claims and returns None
        self.assertIsNone(task_advance._merge_pass_claim(record, 17, HEAD, []))

        # _merge_once returns MERGE_REVIEW_NOT_PASS
        merge_result = task_advance._merge_once(
            store=ClaimStore(record), record=record, number=17, head=HEAD, pr=PR,
            events=[], github_get=green_linux_ci,
            gh_runner=lambda *_a: subprocess.CompletedProcess([], 0), root='/repo')
        self.assertEqual(merge_result['step'], 'MERGE_REFUSED')
        self.assertEqual(merge_result['codes'], ['MERGE_REVIEW_NOT_PASS'])

    def test_task_drive_around_real_advance_stops_after_one_advance(self):
        import inspect
        from argparse import Namespace
        record = base_record()
        store = ClaimStore(record)
        dispatched = []

        def counted_dispatch(*args, **kwargs):
            dispatched.append((args, kwargs))
            err = LLMToolError('refused')
            err.review_dispatch_code = 'REVIEW_NOT_POSTED'
            err.review_failure_reason = 'REVIEW_HEADER_MISMATCH'
            err.review_refusal = {
                'failure_reason': 'REVIEW_HEADER_MISMATCH',
                'excerpt': 'synthetic header mismatch excerpt',
                'receipt_path': None,
            }
            raise err

        sig = inspect.signature(task_drive.drive)
        drive_args = []
        drive_kwargs = {}
        first = next(iter(sig.parameters.values()), None)
        if first and first.name == 'args':
            drive_args.append(Namespace(task_id=record['task_id'], store=store, pr=17))
        elif first and first.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            drive_args.append(record['task_id'])
        else:
            drive_kwargs['task_id'] = record['task_id']

        if 'store' in sig.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
            drive_kwargs['store'] = store
        if 'github_get' in sig.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
            drive_kwargs['github_get'] = green_linux_ci
        if 'pr_number' in sig.parameters:
            drive_kwargs['pr_number'] = 17
        elif 'pr' in sig.parameters:
            drive_kwargs['pr'] = 17
        if 'dispatch_review_call' in sig.parameters:
            drive_kwargs['dispatch_review_call'] = counted_dispatch
        if 'sleep' in sig.parameters:
            drive_kwargs['sleep'] = lambda _seconds: None

        snapshot = dict(d6(), branch='feature/task')
        with patch.object(task_advance.task_completion, '_github_get', side_effect=green_linux_ci), \
             patch.object(task_advance, '_d6_snapshot', return_value=snapshot), \
             patch.object(task_advance, '_pr_snapshot', return_value=PR), \
             patch.object(task_advance, '_review_outcomes', return_value=[]), \
             patch.object(task_advance, '_review_ci_gate', return_value=(None, None)), \
             patch.object(task_advance, '_find_published_pr', return_value=17), \
             patch.object(task_advance, 'review_dispatch', review_dispatch), \
             patch.object(review_dispatch, 'dispatch_review', side_effect=counted_dispatch):
            task_drive.drive(*drive_args, **drive_kwargs)

        self.assertEqual(len(dispatched), 1)


if __name__ == '__main__':
    unittest.main()
