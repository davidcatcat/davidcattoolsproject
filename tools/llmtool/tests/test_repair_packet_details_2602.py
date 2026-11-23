"""#2602 item l: task_advance repair packets carry CI/EC2 failure details and read modules."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool import review_repair, task_advance, task_submission_cli
from llmtool.task_store import _json_bytes

import review_trust_fixture
import worker_policy_fixture
from test_review_repair import EXECUTOR, HEAD, PR, TASK, TempTaskStore, _record


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def _ec2_receipt(record, *, excerpt='', failures=None, modules=('test_alpha',)):
    receipt = {
        'task_id': record['task_id'],
        'run_id': record['run_id'],
        'executor_task': EXECUTOR,
        'source_sha': HEAD,
        'result': 'FAIL',
        'modules': list(modules),
        'ran_total': 1,
        'elapsed_s': 0.05,
        'failure_excerpt': excerpt,
    }
    if failures is not None:
        receipt['failures'] = list(failures)
    receipt['receipt_digest'] = hashlib.sha256(_json_bytes(receipt)).hexdigest()
    return receipt


class RepairPacketDetails2602Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store_dir = Path(self.temp.name) / 'store'
        self.store_dir.mkdir()
        self.d6_root = Path(self.temp.name) / 'd6'
        (self.d6_root / 'tools' / 'llmtool' / 'tests').mkdir(parents=True)
        (self.d6_root / 'scripts').mkdir(parents=True)
        (self.d6_root / 'tools' / 'llmtool' / 'tests' / 'test_alpha.py').write_text(
            'import unittest\n', encoding='utf-8')
        (self.d6_root / 'scripts' / 'test_beta.py').write_text(
            'import unittest\n', encoding='utf-8')

        worker_policy_fixture.install(self)
        for patcher in (
                patch.object(review_repair.worker_dispatch, 'evaluate',
                             return_value=type('Decision', (), {'allowed': True})()),
                patch.object(review_repair.shutil, 'which', return_value='/usr/bin/agy')):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _base_record(self):
        record = _record()
        record['completion']['state'] = 'WAITING_REVIEW'
        record['completion']['evidence']['result'] = {'result_head': HEAD}
        record['review_dispatch_claim'] = []
        record['completion_events'] = []
        record['remote_test_outcomes'] = [{
            'result': 'ADMISSION_DENIED',
            'code': 'MODULE_NOT_ADMITTED',
            'executor_task': EXECUTOR,
            'sha': HEAD,
            'modules': [],
        }]
        return record

    def _d6_snapshot(self):
        return {
            'root': str(self.d6_root),
            'task_id': EXECUTOR,
            'result_head': HEAD,
            'recorded_result_head': HEAD,
            'registered_result_head': HEAD,
            'dirty': False,
            'state': 'CLEAN',
        }

    def _advance_repair(self, record, *, github_get=None):
        store = TempTaskStore(str(self.store_dir), record)
        dispatched = []

        def adapter(**request):
            dispatched.append(request)
            return {'status': 'DISPATCHED', 'head': HEAD}

        def recorder(**request):
            return {
                'task_id': request['repair_task_id'],
                'pm_assignment': {'acceptance': ['Repair the failed test.']},
            }

        def repair_call(**kwargs):
            return review_repair.repair_review(
                **kwargs,
                dispatch_adapter=adapter,
                current_head=lambda _task, _executor: HEAD,
                repair_task_recorder=recorder,
            )

        pr_obj = {'number': PR, 'head': {'sha': HEAD}, 'state': 'open', 'merged': False}
        with patch.object(task_advance, '_d6_snapshot', return_value=self._d6_snapshot()), \
             patch.object(task_advance, '_pr_snapshot', return_value=pr_obj), \
             patch.object(task_advance, '_review_outcomes', return_value=[]):
            result = task_advance.advance(
                task_id=TASK,
                store=store,
                pr_number=PR,
                github_get=github_get or (lambda _path: []),
                repair_review_call=repair_call,
            )
        return result, dispatched

    def test_ci_failure_with_parseable_details_includes_packet_details_and_read_modules(self):
        record = self._base_record()
        orig_read = list(record['developer']['read_paths'])
        orig_write = list(record['developer']['write_paths'])
        check_run = {
            'id': 42,
            'name': 'check (Linux 3.14)',
            'head_sha': HEAD,
            'status': 'completed',
            'conclusion': 'failure',
            'details_url': 'https://example.test/actions/runs/9001/job/9002',
            'output': {
                'title': 'Linux CI failed',
                'summary': 'FAIL: test_one (test_alpha.AlphaTests)\nAssertionError: expected 1',
            },
        }

        def github_get(path):
            if path == f'commits/{HEAD}/check-runs?per_page=100':
                return {'total_count': 1, 'check_runs': [check_run]}
            if path == 'check-runs/42':
                return check_run
            if path == f'pulls/{PR}/files?per_page=100':
                return [
                    {'filename': 'tools/llmtool/llmtool/review_repair.py', 'status': 'modified'},
                    {'filename': 'tools/llmtool/tests/test_alpha.py', 'status': 'modified'},
                ]
            return {'number': PR, 'head': {'sha': HEAD}, 'state': 'open', 'merged': False}

        result, dispatched = self._advance_repair(record, github_get=github_get)
        self.assertEqual((result['step'], result['codes']), ('CI_FAILED', []))
        self.assertEqual(len(dispatched), 1)
        packet = dispatched[0]['packet']
        self.assertEqual(task_submission_cli._validate_repair_packet(packet), packet)
        text = packet['reviewer_text_excerpt_untrusted_data']
        self.assertIn('CI_CHECK_FAILURE check=check (Linux 3.14) run=42 conclusion=failure', text)
        self.assertIn('workflow_run=9001 job=9002', text)
        self.assertIn('failing_tests=test_one (test_alpha.AlphaTests)', text)
        self.assertIn('failing_modules=tools/llmtool/tests/test_alpha.py', text)
        self.assertIn('AssertionError: expected 1', text)
        dev = dispatched[0]['developer']
        self.assertEqual(dev['read_paths'], orig_read + ['tools/llmtool/tests/test_alpha.py'])
        self.assertEqual(dev['write_paths'], orig_write)

    def test_ec2_failure_with_parseable_details_includes_packet_details_and_read_modules(self):
        record = self._base_record()
        record['remote_test_outcomes'] = []
        receipt = _ec2_receipt(
            record,
            excerpt='FAIL: test_two (test_beta.BetaTests)\nAssertionError: boom',
            failures=['test_beta'],
            modules=('test_beta',),
        )
        record['remote_test_receipts'] = [receipt]
        orig_read = list(record['developer']['read_paths'])
        orig_write = list(record['developer']['write_paths'])

        result, dispatched = self._advance_repair(record)
        self.assertEqual((result['step'], result['codes']), ('REPAIR_REVIEW', []))
        self.assertEqual(len(dispatched), 1)
        packet = dispatched[0]['packet']
        self.assertEqual(task_submission_cli._validate_repair_packet(packet), packet)
        text = packet['reviewer_text_excerpt_untrusted_data']
        self.assertIn('REMOTE_TEST_FAILURE check=EC2 remote-test', text)
        self.assertIn(f"receipt={receipt['receipt_digest'][:8]}", text)
        self.assertIn(f'executor={EXECUTOR}', text)
        self.assertIn('failing_tests=test_two (test_beta.BetaTests)', text)
        self.assertIn('failing_modules=scripts/test_beta.py', text)
        self.assertIn('AssertionError: boom', text)
        dev = dispatched[0]['developer']
        self.assertEqual(dev['read_paths'], orig_read + ['scripts/test_beta.py'])
        self.assertEqual(dev['write_paths'], orig_write)

    def test_unavailable_ci_and_ec2_details_keep_packet_with_note_and_unchanged_scope(self):
        # CI without output
        ci_record = self._base_record()
        orig_read = list(ci_record['developer']['read_paths'])
        orig_write = list(ci_record['developer']['write_paths'])
        empty_run = {
            'id': 43,
            'name': 'check (docs)',
            'head_sha': HEAD,
            'status': 'completed',
            'conclusion': 'failure',
        }

        def github_get(path):
            if path == f'commits/{HEAD}/check-runs?per_page=100':
                return {'total_count': 1, 'check_runs': [empty_run]}
            if path == 'check-runs/43':
                return empty_run
            if path == f'pulls/{PR}/files?per_page=100':
                return [{'filename': 'docs/guide.md', 'status': 'modified'}]
            return {'number': PR, 'head': {'sha': HEAD}, 'state': 'open', 'merged': False}

        ci_result, ci_dispatched = self._advance_repair(ci_record, github_get=github_get)
        self.assertEqual((ci_result['step'], ci_result['codes']), ('CI_FAILED', []))
        self.assertEqual(len(ci_dispatched), 1)
        ci_text = ci_dispatched[0]['packet']['reviewer_text_excerpt_untrusted_data']
        self.assertTrue(ci_text.startswith(
            f'CI_CHECK_FAILURE check=check (docs) run=43 conclusion=failure head={HEAD}\n'))
        self.assertIn('failing_tests=unavailable', ci_text)
        self.assertIn('failing_modules=unavailable', ci_text)
        self.assertIn('Failure details unavailable.', ci_text)
        self.assertEqual(ci_dispatched[0]['developer']['read_paths'], orig_read)
        self.assertEqual(ci_dispatched[0]['developer']['write_paths'], orig_write)

        # EC2 with empty excerpt and nonexistent module
        ec2_record = self._base_record()
        ec2_record['remote_test_outcomes'] = []
        ec2_record['remote_test_receipts'] = [
            _ec2_receipt(ec2_record, excerpt='', failures=['test_nonexistent'],
                         modules=('test_nonexistent',))
        ]
        ec2_result, ec2_dispatched = self._advance_repair(ec2_record)
        self.assertEqual((ec2_result['step'], ec2_result['codes']), ('REPAIR_REVIEW', []))
        self.assertEqual(len(ec2_dispatched), 1)
        ec2_text = ec2_dispatched[0]['packet']['reviewer_text_excerpt_untrusted_data']
        self.assertIn('REMOTE_TEST_FAILURE check=EC2 remote-test', ec2_text)
        self.assertIn('failing_tests=unavailable', ec2_text)
        self.assertIn('failing_modules=unavailable', ec2_text)
        self.assertIn('Failure details unavailable.', ec2_text)
        self.assertEqual(ec2_dispatched[0]['developer']['read_paths'], orig_read)
        self.assertEqual(ec2_dispatched[0]['developer']['write_paths'], orig_write)

    def test_admissible_read_scope_excludes_sensitive_and_non_test_paths_and_respects_bounds(self):
        orig_read = ['tools/llmtool/llmtool']
        orig_write = ['tools/llmtool/llmtool/review_repair.py']
        candidates = [
            '../secret.py',
            '/etc/passwd',
            'tools/llmtool/llmtool/review_repair.py',
            '.github/workflows/ci.yml',
            'tools/llmtool/tests/test_alpha.py',
            'scripts/test_beta.py',
        ]
        with patch.object(review_repair.sensitive_paths, 'is_sensitive',
                          side_effect=lambda p: p == 'scripts/test_beta.py'):
            merged = review_repair._merge_repair_read_paths(orig_read, candidates, orig_write)
        self.assertEqual(merged, ['tools/llmtool/llmtool', 'tools/llmtool/tests/test_alpha.py'])

        many = [f'tools/llmtool/tests/test_mod_{i}.py' for i in range(40)]
        bounded = review_repair._merge_repair_read_paths(orig_read, many, orig_write)
        self.assertEqual(len(bounded), 1 + review_repair._MAX_EXTRA_READ_PATHS)

    def test_screening_and_length_bounds_apply_to_ec2_details(self):
        record = self._base_record()
        record['remote_test_outcomes'] = []
        long_excerpt = 'FAIL: test_two (test_beta.BetaTests)\n' + ('x' * 3000)
        receipt = _ec2_receipt(record, excerpt=long_excerpt, failures=['test_beta'],
                               modules=('test_beta',))
        record['remote_test_receipts'] = [receipt]

        seen = []
        real_screen = task_submission_cli._screen_text

        def fake_screen(text, limit):
            seen.append((text, limit))
            return real_screen(text, limit)

        with patch.object(task_submission_cli, '_screen_text', side_effect=fake_screen):
            result, dispatched = self._advance_repair(record)
        self.assertEqual(result['step'], 'REPAIR_REVIEW')
        self.assertGreaterEqual(len(seen), 2)
        packet = dispatched[0]['packet']
        self.assertLessEqual(
            len(packet['reviewer_text_excerpt_untrusted_data']),
            review_repair.MAX_EXCERPT_CHARS,
        )
        encoded = json.dumps(packet, ensure_ascii=True, sort_keys=True,
                             separators=(',', ':')).encode('utf-8')
        self.assertLessEqual(len(encoded), 8192)


if __name__ == '__main__':
    unittest.main()
