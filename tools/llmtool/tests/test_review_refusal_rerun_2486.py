"""#2486: fixed refusal cause categories and one bounded automatic re-run."""
import json
import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

from llmtool import review_dispatch, task_advance, task_submission_cli
from llmtool.errors import LLMToolError
import test_review_dispatch as base
from test_review_dispatch import HEAD, setUpModule, tearDownModule  # noqa: F401

TASK = 'a' * 32
CONTRACT = 'd' * 64
OTHER_HEAD = 'c' * 40
PR = {'number': 17, 'head': {'sha': HEAD}, 'state': 'open', 'merged': False}
D6 = {'root': '/repo/task', 'task_id': 'executor-1', 'result_head': HEAD,
      'recorded_result_head': HEAD, 'registered_result_head': HEAD, 'dirty': False}


def _pass_screen(text, _limit):
    return text


class ReplyCauseTests(unittest.TestCase):
    """(a), (b), (h): a one-line reply keeps a fixed cause class, never text."""

    setUp = base.ReviewRefusalReceiptTests.setUp
    tearDown = base.ReviewRefusalReceiptTests.tearDown
    _invoke_refusal = base.ReviewRefusalReceiptTests._invoke_refusal

    def _refusal(self, reply):
        task = self.store.get(self.task_id)

        def runner(**_kwargs):
            return review_dispatch._parse_codex_review(
                reply.encode('utf-8'), task_id=self.task_id, run_id=task['run_id'],
                contract_digest=task['task_binding']['contract_digest'], head=HEAD)

        with patch.object(task_submission_cli, '_screen_text', side_effect=_pass_screen), \
                self.assertRaises(LLMToolError) as raised:
            self._invoke_refusal(runner=runner)
        return raised.exception

    def _receipt(self, exc):
        path = Path(exc.review_refusal['receipt_path'])
        self.receipt_text = path.read_text(encoding='utf-8')
        return json.loads(self.receipt_text)

    def test_limit_style_one_line_reply_records_cause_without_text(self):
        reply = 'usage limit reached; retry later'
        receipt = self._receipt(self._refusal(reply))

        self.assertEqual(receipt['failure_reason'], 'OUTPUT_NOT_JSONL')
        diagnostic = receipt['reply_diagnostic']
        self.assertEqual(diagnostic['kind'], 'text')
        self.assertEqual(diagnostic['lines'], 1)
        self.assertEqual(diagnostic['cause'], 'usage_or_rate_limit')
        self.assertNotIn(reply, self.receipt_text)

    def test_unmatched_one_line_reply_records_other_without_text(self):
        reply = 'unexpected output from the reviewer command'
        receipt = self._receipt(self._refusal(reply))

        self.assertEqual(receipt['reply_diagnostic']['cause'], 'other')
        self.assertNotIn(reply, self.receipt_text)

    def test_receipt_stays_not_a_verdict_and_nothing_is_posted(self):
        receipt = self._receipt(self._refusal('unexpected output from the reviewer command'))

        self.assertIs(receipt['not_a_verdict'], True)
        self.assertNotIn('verdict', receipt)
        self.assertNotIn('review_text', receipt)
        claim = self.store.get(self.task_id)['review_dispatch_claim'][0]
        self.assertEqual(claim['state'], 'UNKNOWN')
        self.assertNotIn('review_result', claim)


class _FakeStore:
    def __init__(self, root, record):
        self.root = Path(root)
        self.record = record

    def get(self, _task_id):
        return self.record

    def compare_and_update(self, _task_id, *, expected, **changes):
        self.record = dict(self.record, **changes)
        return self.record


class RefusalRerunTests(unittest.TestCase):
    """(c) to (f): one auditable re-run, then a fixed refusal."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.receipt_dir = Path(self.temp.name).resolve() / 'review-refusals'
        self.receipt_dir.mkdir(mode=0o700)
        self.reads = []

    def _claim(self, version=1, **changes):
        claim = {'executor_task': 'executor-1', 'pr': 17, 'head_sha': HEAD,
                 'version': version, 'state': 'UNKNOWN',
                 'reviewer_task': f'reviewer-{version}', 'native_session_id': None,
                 'account_mode': 'CODEX_CLI',
                 'claimed_at': '2026-11-21T00:00:00Z',
                 'updated_at': '2026-11-21T00:00:01Z',
                 'failure_reason': 'OUTPUT_NOT_JSONL'}
        claim.update(changes)
        return claim

    def _record(self, claims, **changes):
        record = {'task_id': TASK, 'run_id': RUN, 'record_sha256': 'r' * 64,
                  'task_binding': {'contract_digest': CONTRACT},
                  'developer': {'state': 'DEVELOPER_RESPONDED'},
                  'completion': {'executor_task_id': 'executor-1',
                                 'state': 'WAITING_REVIEW',
                                 'evidence': {'result_head': HEAD}},
                  'review_dispatch_claim': claims, 'review_repair_claims': [],
                  'completion_events': [],
                  'remote_test_outcomes': [{
                      'result': 'ADMISSION_DENIED', 'code': 'MODULE_NOT_ADMITTED',
                      'executor_task': 'executor-1', 'sha': HEAD, 'modules': []}]}
        record.update(changes)
        return record

    def _store(self, claims):
        root = Path(self.temp.name).resolve() / 'tasks'
        return _FakeStore(root, self._record(claims))

    def _write_receipt(self, claim, *, contract=CONTRACT, automatic=False):
        record = {'task_id': TASK, 'pr': claim['pr'], 'head': claim['head_sha'],
                  'version': claim['version'],
                  'reviewer_task': claim['reviewer_task'],
                  'failure_reason': claim['failure_reason'],
                  'recorded_at': '2026-11-21T00:00:00Z',
                  'not_a_verdict': True, 'excerpt': 'OUTPUT_SHAPE line=1 of 1 bytes=9 first=letter',
                  'contract_digest': contract}
        if automatic:
            record['automatic_rerun'] = True
        path = self.receipt_dir / f"{TASK}-{claim['head_sha']}-{claim['version']}.json"
        path.write_text(json.dumps(record), encoding='utf-8')
        path.chmod(0o600)
        return path

    def _github_get(self, path):
        self.reads.append(path)
        if path.startswith('issues/'):
            return []
        if path.startswith('commits/'):
            return {'total_count': 1, 'check_runs': [{
                'id': 1, 'name': 'check (Linux 3.14)', 'head_sha': HEAD,
                'status': 'completed', 'conclusion': 'success'}]}
        if path.endswith('/files?per_page=100'):
            return [{'filename': 'src/module.py'}]
        if path.startswith('pulls/'):
            return PR
        return []

    def _advance(self, store, dispatch):
        with patch.object(task_advance, '_d6_snapshot', return_value=dict(D6)), \
                patch.object(task_advance, '_pr_snapshot', return_value=dict(PR)), \
                patch.object(task_advance, '_review_outcomes', return_value=[]):
            return task_advance.advance(
                task_id=TASK, store=store, pr_number=17,
                github_get=self._github_get, dispatch_review_call=dispatch)

    def test_same_head_and_contract_re_runs_exactly_once_and_records_it(self):
        claim = self._claim()
        store = self._store([claim])
        path = self._write_receipt(claim)
        calls = []

        def dispatch(**kwargs):
            calls.append(kwargs)
            return {'state': 'DISPATCHED'}

        result = self._advance(store, dispatch)

        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['version'], 2)
        self.assertIs(calls[0]['refusal_rerun'], True)
        recorded = json.loads(path.read_text(encoding='utf-8'))
        self.assertIs(recorded['not_a_verdict'], True)
        self.assertEqual(recorded['rerun']['version'], 2)

    def test_older_claims_on_the_same_head_do_not_block_the_one_re_run(self):
        older = self._claim(version=1, state='COMPLETED', native_session_id='old')
        older.pop('failure_reason', None)
        latest = self._claim(version=2)
        store = self._store([older, latest])
        self._write_receipt(latest)
        calls = []
        result = self._advance(
            store, lambda **kwargs: calls.append(kwargs) or {'state': 'DISPATCHED'})

        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['version'], 3)
        self.assertIs(calls[0]['refusal_rerun'], True)

    def test_a_second_advance_after_the_re_run_does_not_re_run_again(self):
        first = self._claim()
        rerun = self._claim(version=2, reviewer_task='reviewer-2')
        store = self._store([first, rerun])
        self._write_receipt(first)
        self._write_receipt(rerun, automatic=True)
        calls = []
        result = self._advance(store, lambda **kwargs: calls.append(kwargs) or {})

        self.assertEqual(result['step'], 'REVIEW_UNUSABLE_STOPPED')
        self.assertEqual(calls, [])
        self.assertIn(task_advance.REVIEW_RERUN_ALREADY_DONE, result['codes'])

    def test_changed_contract_does_not_re_run_the_old_attempt(self):
        claim = self._claim()
        store = self._store([claim])
        store.record['task_binding'] = {'contract_digest': 'e' * 64}
        self._write_receipt(claim)
        calls = []
        result = self._advance(store, lambda **kwargs: calls.append(kwargs) or {})

        self.assertEqual(result['step'], 'REVIEW_UNUSABLE_STOPPED')
        self.assertEqual(calls, [])
        self.assertIn(task_advance.REVIEW_RERUN_CONTRACT_CHANGED, result['codes'])

    def test_changed_head_does_not_re_run_the_old_attempt(self):
        claim = self._claim()
        store = self._store([claim])
        self._write_receipt(claim)
        moved = dict(D6, result_head=OTHER_HEAD)
        pr = {'number': 17, 'head': {'sha': OTHER_HEAD}, 'state': 'open', 'merged': False}

        version, code = task_advance._review_refusal_rerun(
            store.record, claim, moved, pr, store, self._github_get)

        self.assertIsNone(version)
        self.assertEqual(code, task_advance.REVIEW_RERUN_HEAD_CHANGED)

    def test_live_reviewer_process_refuses_the_re_run(self):
        claim = self._claim(native_session_id='native-session-live')
        store = self._store([claim])
        self._write_receipt(claim)
        calls = []
        result = self._advance(store, lambda **kwargs: calls.append(kwargs) or {})

        self.assertEqual(result['step'], 'REVIEW_UNUSABLE_STOPPED')
        self.assertEqual(calls, [])
        self.assertIn(task_advance.REVIEW_RERUN_REVIEWER_LIVE, result['codes'])

class ValidVerdictTests(unittest.TestCase):
    """(g): a valid JSONL verdict keeps the existing success path."""

    setUp = base.ReviewRefusalReceiptTests.setUp
    tearDown = base.ReviewRefusalReceiptTests.tearDown

    def test_valid_jsonl_verdict_is_unchanged(self):
        task = self.store.get(self.task_id)
        raw = base._codex_event_stream(
            base._codex_review_text(
                run_id=task['run_id'],
                contract=task['task_binding']['contract_digest']),
            run_id=task['run_id'])
        parsed = review_dispatch._parse_codex_review(
            raw, task_id=self.task_id, run_id=task['run_id'],
            contract_digest=task['task_binding']['contract_digest'], head=HEAD)
        self.assertEqual(parsed['verdict'], 'PASS')
        self.assertFalse(self.receipt_dir.exists())


if __name__ == '__main__':
    unittest.main()
