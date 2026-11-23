"""#2602 (a): a --pr Debug may run only for the FAIL its exact head carries.

The review family proves the FAIL through the existing diagnosis-findings
reader (review_result.py); the -ec2 family proves it through the existing
remote-test receipt reader. A PASS or no verdict refuses with one code and
counts nothing. The PR-less --issue entry is unchanged.
"""
import hashlib
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from llmtool import high_debug
from llmtool.errors import LLMToolError

TASK = 'a' * 32
HEAD = 'a' * 40
OTHER_HEAD = 'b' * 40
PR = 17
FAMILY = 'pr-17'
EC2_FAMILY = 'pr-17-ec2'
REPORT = ''.join('## ' + heading + '\nDetails.\n'
                 for heading in high_debug.REPORT_SECTIONS) + 'RCA_CONFIRMED'
FINDINGS = 'Finding: F1 [High] synthetic.py:1 synthetic finding\n'


def review_claim(*, task_id=TASK, head=HEAD, verdict='FAIL',
                 text=FINDINGS, executor='executor', pr=PR):
    """A formal review claim shaped like the recorded dispatch result."""
    return {'review_kind': 'formal', 'executor_task': executor, 'pr': pr, 'version': 1,
            'head_sha': head, 'state': 'COMPLETED',
            'updated_at': '2026-11-23T00:00:00Z',
            'review_result': {'task_id': task_id, 'head': head,
                              'verdict': verdict, 'review_text': text}}


def remote_receipt(result='FAIL', *, head=HEAD, executor='executor'):
    """A stored remote-test receipt: the real fields plus the store's bookkeeping."""
    receipt = {
        'result': result,
        'exit_code': 0 if result == 'PASS' else 1,
        'source_sha': head,
        'tree': 'b' * 40,
        'fetch_head': head,
        'modules': ['test_high_debug_requires_fail_2602'],
        'identity': {'uid': 1000},
        'started_at': '2026-11-23T00:00:00Z',
        'ended_at': '2026-11-23T00:01:00Z',
        'elapsed_s': 60.0,
        'ran_total': 1,
        'final': ['OK'] if result == 'PASS' else ['FAILED (failures=1)'],
        'failures': ([] if result == 'PASS' else
                     ['test_high_debug_requires_fail_2602']),
        'log_bytes': 10,
        'log_sha256': 'c' * 64,
        'remote_log_path': None,
    }
    if result != 'PASS':
        receipt['failure_excerpt'] = 'FAIL: test_x (synthetic.Case)\nAssertionError\n'
    receipt.update(task_id=TASK, run_id='b' * 32, executor_task=executor,
                   pm_session='c' * 32, command_digest='d' * 64,
                   receipt_digest=hashlib.sha256(
                       json.dumps(receipt, sort_keys=True).encode('utf-8')).hexdigest())
    return receipt


class _Store:
    def __init__(self, *, family=FAMILY, claims=None, receipts=None, completion=True,
                 developer=None):
        self.entries, self.events, self.served = [], [], set()
        self.family, self.claims, self.receipts = family, claims, receipts
        self.completion, self.developer = completion, developer

    def task_ids(self):
        return sorted(self.served)

    def d6_journal_holder(self, executor_task):
        return TASK if executor_task == 'executor' else None

    def get(self, task_id):
        self.served.add(task_id)
        record = {'task_id': task_id, 'run_id': 'b' * 32,
                  'review_repair_claims': [{'family': self.family, 'pr': PR}] * 2,
                  'high_debug': list(self.entries)}
        if self.completion:
            record['completion'] = {'executor_task_id': 'executor',
                                    'evidence': {'common_dir': '/tmp/ignored'}}
        if self.developer is not None:
            record['developer'] = self.developer
        if self.claims is not None:
            record['review_dispatch_claim'] = self.claims
        if self.receipts is not None:
            record['remote_test_receipts'] = self.receipts
        return record

    @staticmethod
    def _completion_now():
        return '2026-11-23T00:00:00Z'

    def append_high_debug(self, _task_id, entry):
        self.entries.append(entry)

    def finalize_high_debug(self, _task_id, index, entry):
        self.entries[index] = entry

    def record_completion_event(self, _task_id, **event):
        self.events.append(event)
        return {'created': True}


class HeadFailRequiredTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.root = self.tmp / 'repo'
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        patches = (
            patch.object(high_debug, '_post_github_record', return_value=True),
            patch.object(high_debug.task_store, 'default_root',
                         return_value=self.tmp / 'tasks'),
            patch.object(high_debug, '_debug_rules',
                         return_value=('RULES stub\n', 'c' * 64)),
            patch.object(high_debug.worker_dispatch, 'evaluate',
                         return_value=SimpleNamespace(allowed=True)),
            patch.object(high_debug.task_completion, 'collect_git_snapshot',
                         return_value=SimpleNamespace(head=HEAD)),
            patch.object(high_debug, '_d6_taken', return_value=False),
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def runner(self, *, prompt, cwd, output_path):
        Path(output_path).write_text(REPORT, encoding='utf-8')
        return subprocess.CompletedProcess([], 0, stdout=b'', stderr=b'')

    def invoke(self, store, *, family=FAMILY, pr_number=PR, issue_number=None,
               early_reason=None, runner=None, create=None):
        registration = SimpleNamespace(
            result_sha=None if issue_number is not None else HEAD,
            root=str(self.root), common_dir=str(self.root / '.git'))
        create = create or Mock(return_value=str(self.root))
        runner = runner or self.runner
        options = dict(store=store, task_id=TASK, executor_task='executor',
                       family=family, runner=runner, create_d6=create,
                       repo_root=str(self.root),
                       read_registration=lambda *_args, **_kwargs: registration)
        if issue_number is not None:
            options.update(issue_number=issue_number,
                           early_reason=early_reason or 'pre-pr-failure')
        else:
            options.update(pr_number=pr_number, failed_corrections=2)
        with redirect_stdout(StringIO()):
            high_debug.run_high_debug(**options)
        return create, runner

    def refused(self, store, **kwargs):
        create, runner = Mock(), Mock()
        with self.assertRaises(LLMToolError) as caught:
            self.invoke(store, create=create, runner=runner, **kwargs)
        self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_FAIL_REQUIRED')
        self.assertEqual(store.entries, [])
        create.assert_not_called()
        runner.assert_not_called()
        return caught.exception

    def test_formal_fail_at_the_head_runs_the_review_family(self):
        store = _Store(claims=[review_claim()])
        self.invoke(store)
        self.assertEqual([entry['status'] for entry in store.entries], ['RCA_CONFIRMED'])

    def test_pass_only_at_the_head_refuses_and_counts_nothing(self):
        store = _Store(claims=[review_claim(verdict='PASS',
                                            text='Verdict: PASS style text\n')])
        self.refused(store)

    def test_no_verdict_at_the_head_refuses_and_counts_nothing(self):
        self.refused(_Store())

    def test_fail_at_another_head_refuses(self):
        self.refused(_Store(claims=[review_claim(head=OTHER_HEAD)]))

    def test_fail_receipt_at_the_head_runs_the_ec2_family(self):
        store = _Store(family=EC2_FAMILY, receipts=[remote_receipt('FAIL')])
        self.invoke(store, family=EC2_FAMILY)
        self.assertEqual([entry['status'] for entry in store.entries], ['RCA_CONFIRMED'])

    def test_pass_receipt_at_the_head_refuses_the_ec2_family(self):
        store = _Store(family=EC2_FAMILY, receipts=[remote_receipt('PASS')])
        self.refused(store, family=EC2_FAMILY)

    def test_fail_receipt_at_another_head_refuses(self):
        store = _Store(family=EC2_FAMILY,
                       receipts=[remote_receipt('FAIL', head=OTHER_HEAD)])
        self.refused(store, family=EC2_FAMILY)

    def test_issue_entry_is_unchanged_and_needs_no_fail(self):
        store = _Store(family='no-pr', completion=False,
                       developer={'direct_result_callback':
                                  {'executor_task_id': 'executor'}})
        with patch.object(high_debug, '_is_plain_issue', return_value=True), \
             patch.object(high_debug, '_no_pr_repo_root', return_value=str(self.root)):
            self.invoke(store, family='no-pr', issue_number=42)
        self.assertEqual([entry['status'] for entry in store.entries], ['RCA_CONFIRMED'])
        self.assertIs(store.entries[0].get('no_pr'), True)


if __name__ == '__main__':
    unittest.main()
