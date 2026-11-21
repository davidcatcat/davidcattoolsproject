"""#2457: EC2 runs more declared test modules than the maximum in batches at one HEAD."""

import contextlib
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from llmtool import merge_train_eligibility, remote_test, task_advance, task_submission_cli
from llmtool.task_store import _json_bytes

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "spec_preflight.py"
_SPEC = importlib.util.spec_from_file_location("spec_preflight_2457", SCRIPT)
spec_preflight = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(spec_preflight)

SHA = 'a' * 40
OTHER_SHA = 'b' * 40
REF = 'feature/batches'
EXECUTOR = 'executor-1'
TASK_ID = '1' * 32
RUN_ID = '2' * 32
MAXIMUM = remote_test._MAX_REMOTE_MODULES
CONFIG = {'host': 'ec2.invalid', 'user': 'runner', 'repo_path': '/srv/repo',
          'python': '/usr/bin/python3'}
HEAD = {'result_head': SHA}


def module_names(count):
    return [f'test_batch_{index:02d}' for index in range(count)]


def task_for(count):
    paths = [f'tools/llmtool/tests/{name}.py' for name in module_names(count)]
    return {
        'task_id': TASK_ID, 'run_id': RUN_ID, 'pm_session_id': 'pm-1',
        'developer': {'read_paths': paths, 'write_paths': []},
        'completion': {'executor_task_id': EXECUTOR,
                       'evidence': {'common_dir': '/repo/.git',
                                    'result': {'result_head': SHA}}},
        'remote_test_receipts': [], 'remote_test_outcomes': [],
    }


class FakeStore:
    def __init__(self, task):
        self.task = task

    def get(self, task_id):
        return self.task

    def append_remote_test_receipt(self, task_id, receipt):
        saved = dict(receipt)
        self.task['remote_test_receipts'].append(saved)
        return saved

    def append_remote_test_outcome(self, task_id, outcome):
        self.task['remote_test_outcomes'].append(outcome)
        return {'outcome': outcome, 'counter': len(self.task['remote_test_outcomes'])}


def receipt_bytes(modules, result='PASS', sha=SHA):
    passed = result == 'PASS'
    value = {
        'result': result, 'exit_code': 0 if passed else 1, 'source_sha': sha,
        'tree': 'c' * 40, 'fetch_head': sha, 'modules': list(modules),
        'identity': {'uid': 1234}, 'started_at': '2026-11-21T00:00:00Z',
        'ended_at': '2026-11-21T00:01:00Z', 'elapsed_s': 1.5,
        'ran_total': len(modules) if passed else 1,
        'final': ['OK'] * len(modules) if passed else ['FAILED (failures=1)'],
        'failures': [] if passed else [modules[0]],
        'log_bytes': 10, 'log_sha256': 'd' * 64, 'remote_log_path': None,
    }
    return json.dumps(value, separators=(',', ':')).encode()


class Transport:
    """Stands in for SSH: answers each call with the next scripted result (default PASS)."""

    def __init__(self, results=()):
        self.calls = []
        self.shas = []
        self.results = list(results)

    def __call__(self, argv, script, timeout):
        modules = argv[12:]
        self.calls.append(tuple(modules))
        self.shas.append(argv[8])
        result = self.results.pop(0) if self.results else 'PASS'
        return SimpleNamespace(returncode=0 if result == 'PASS' else 1,
                               stdout=receipt_bytes(modules, result, argv[8]))


def sealed(modules, result='PASS', sha=SHA):
    value = {'task_id': TASK_ID, 'run_id': RUN_ID, 'executor_task': EXECUTOR,
             'source_sha': sha, 'result': result, 'modules': list(modules),
             'ran_total': 1, 'failures': [] if result == 'PASS' else [modules[0]],
             'final': ['OK'] if result == 'PASS' else ['FAILED (failures=1)'],
             'elapsed_s': 0.5}
    value['receipt_digest'] = hashlib.sha256(_json_bytes(value)).hexdigest()
    return value


class BatchCase(unittest.TestCase):
    def setUp(self):
        for target, kwargs in (
                ('read_d6_registration', {'return_value': SimpleNamespace(
                    result_sha=SHA, root='/repo/task')}),
                ('_config', {'return_value': dict(CONFIG)}),
                ('_ssh_timeout', {'return_value': 600}),
                ('record_remote_test_event', {'return_value': None}),
                ('record_remote_test_outcome_event', {'return_value': {'event_id': 'ev'}})):
            patcher = mock.patch.object(remote_test, target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_once(self, store, transport, modules=()):
        return remote_test.run_remote_test(
            TASK_ID, executor_task=EXECUTOR, sha=SHA, ref=REF, modules=modules,
            store=store, transport=transport)

    def status(self, task, sha=SHA):
        return task_advance._remote_test_status(task, {'result_head': sha})

    def action(self, task):
        return task_advance._pre_review_action(task, HEAD, None, {}, SHA)


class BatchRunTests(BatchCase):
    def test_a_eight_modules_run_as_one_batch_unchanged(self):
        store, transport = FakeStore(task_for(8)), Transport()
        saved = self.run_once(store, transport)
        self.assertEqual(saved['result'], 'PASS')
        self.assertEqual(transport.calls, [tuple(module_names(8))])
        self.assertEqual(self.status(store.task)[0], 'PASS')
        self.assertEqual(self.action(store.task), 'DISPATCH_REVIEW')
        self.assertEqual(remote_test.remote_test_batches(module_names(8)),
                         (tuple(module_names(8)),))

    def test_b_nine_to_sixteen_modules_run_as_two_batches_and_are_then_satisfied(self):
        for count in (MAXIMUM + 1, 12, 2 * MAXIMUM):
            with self.subTest(count=count):
                store, transport = FakeStore(task_for(count)), Transport()
                self.run_once(store, transport)
                self.assertIsNone(self.status(store.task)[0])
                self.assertEqual(self.action(store.task), 'RUN_REMOTE_TEST')
                self.run_once(store, transport)
                self.assertEqual(len(transport.calls), 2)
                self.assertTrue(all(len(batch) <= MAXIMUM for batch in transport.calls))
                self.assertEqual(sorted(transport.calls[0] + transport.calls[1]),
                                 module_names(count))
                self.assertEqual(set(transport.shas), {SHA})
                self.assertEqual(len(store.task['remote_test_receipts']), 2)
                self.assertEqual(self.status(store.task)[0], 'PASS')
                self.assertEqual(self.action(store.task), 'DISPATCH_REVIEW')

    def test_b_batches_split_at_the_maximum(self):
        batches = remote_test.remote_test_batches(module_names(2 * MAXIMUM + 1))
        self.assertEqual([len(batch) for batch in batches], [MAXIMUM, MAXIMUM, 1])
        self.assertEqual(remote_test.remote_test_batch_count(2 * MAXIMUM + 1), 3)
        self.assertEqual(remote_test.remote_test_batch_count(0), 0)

    def test_c_a_failing_batch_is_unsatisfied_and_routes_the_ec2_repair(self):
        store, transport = FakeStore(task_for(12)), Transport(['FAIL'])
        self.run_once(store, transport)
        status, _, receipt = self.status(store.task)
        self.assertEqual(status, 'FAIL')
        self.assertEqual(receipt['result'], 'FAIL')
        self.assertEqual(self.action(store.task), 'REPAIR_REVIEW')

    def test_c_a_failing_second_batch_after_a_passing_first_is_unsatisfied(self):
        store, transport = FakeStore(task_for(12)), Transport(['PASS', 'FAIL'])
        self.run_once(store, transport)
        self.run_once(store, transport)
        self.assertEqual(self.status(store.task)[0], 'FAIL')
        self.assertEqual(self.action(store.task), 'REPAIR_REVIEW')

    def test_g_explicit_module_selection_stays_limited_to_the_maximum(self):
        store, transport = FakeStore(task_for(12)), Transport()
        too_many = module_names(12)[:MAXIMUM + 1]
        denied = self.run_once(store, transport, modules=too_many)
        self.assertEqual((denied['result'], denied['code']),
                         ('ADMISSION_DENIED', 'MODULE_NOT_ADMITTED'))
        self.assertEqual(transport.calls, [])
        allowed = module_names(12)[2:2 + MAXIMUM]
        self.assertEqual(self.run_once(store, transport, modules=allowed)['result'], 'PASS')
        self.assertEqual(transport.calls, [tuple(allowed)])

    def test_an_undeclared_module_is_still_refused(self):
        store, transport = FakeStore(task_for(12)), Transport()
        denied = self.run_once(store, transport, modules=['test_not_declared'])
        self.assertEqual(denied['code'], 'MODULE_NOT_ADMITTED')
        self.assertEqual(transport.calls, [])


class BatchStatusTests(BatchCase):
    def task_with(self, count, *receipts):
        task = task_for(count)
        task['remote_test_receipts'] = list(receipts)
        return task

    def batches(self, count):
        return remote_test.remote_test_batches(module_names(count))

    def test_d_batches_at_different_heads_are_unsatisfied(self):
        first, second = self.batches(12)
        task = self.task_with(12, sealed(first, sha=OTHER_SHA), sealed(second))
        self.assertIsNone(self.status(task)[0])
        self.assertEqual(self.action(task), 'RUN_REMOTE_TEST')
        task = self.task_with(12, sealed(first), sealed(second, sha=OTHER_SHA))
        self.assertIsNone(self.status(task)[0])

    def test_d_a_pass_at_the_head_never_satisfies_a_batch_recorded_for_another_head(self):
        first, second = self.batches(12)
        task = self.task_with(12, sealed(first), sealed(second))
        self.assertEqual(self.status(task)[0], 'PASS')
        self.assertIsNone(self.status(task, sha=OTHER_SHA)[0])

    def test_e_a_later_pass_never_hides_an_earlier_fail_at_the_same_head(self):
        first, second = self.batches(12)
        task = self.task_with(
            12, sealed(second), sealed(first, 'FAIL'), sealed(first))
        self.assertEqual(self.status(task)[0], 'FAIL')
        self.assertNotEqual(self.action(task), 'DISPATCH_REVIEW')
        task = self.task_with(12, sealed(first, 'FAIL'), sealed(first))
        self.assertEqual(self.status(task)[0], 'FAIL')

    def test_e_satisfied_only_when_every_module_passes_after_the_last_fail(self):
        first, second = self.batches(12)
        task = self.task_with(
            12, sealed(first), sealed(second), sealed(first, 'FAIL'))
        self.assertEqual(self.status(task)[0], 'FAIL')
        task['remote_test_receipts'].append(sealed(first))
        self.assertEqual(self.status(task)[0], 'FAIL')
        task['remote_test_receipts'].append(sealed(second))
        self.assertEqual(self.status(task)[0], 'PASS')
        task['remote_test_receipts'].append(sealed(second, 'FAIL'))
        self.assertEqual(self.status(task)[0], 'FAIL')

    def test_e_a_rerun_after_a_fail_is_not_served_from_an_older_pass(self):
        first, second = self.batches(12)
        task = self.task_with(12, sealed(second), sealed(first, 'FAIL'))
        store, transport = FakeStore(task), Transport()
        cached = dict(sealed(first), command_digest=remote_test._command_digest(
            remote_test._ssh_argv(CONFIG, SHA, REF, first)))
        task['remote_test_receipts'].insert(0, cached)
        self.run_once(store, transport)
        self.assertEqual(transport.calls, [first])

    def test_receipt_with_a_wrong_digest_is_ignored(self):
        first, second = self.batches(12)
        forged = dict(sealed(second), ran_total=99)
        task = self.task_with(12, sealed(first), forged)
        self.assertIsNone(self.status(task)[0])

    def test_unchanged_for_eight_or_fewer_modules(self):
        names = tuple(module_names(8))
        task = self.task_with(8, sealed(names, 'FAIL'), sealed(names))
        self.assertEqual(self.status(task)[0], 'PASS')
        task = self.task_with(8, sealed(names), sealed(names, 'FAIL'))
        self.assertEqual(self.status(task)[0], 'FAIL')


def full_receipt(modules, result='PASS', sha=SHA):
    """A sealed receipt with the fields the direct Developer evidence check reads."""
    passed = result == 'PASS'
    value = {'task_id': TASK_ID, 'run_id': RUN_ID, 'executor_task': EXECUTOR,
             'source_sha': sha, 'result': result, 'modules': list(modules),
             'ran_total': len(modules) if passed else 1,
             'failures': [] if passed else [modules[0]],
             'final': ['OK'] * len(modules) if passed else ['FAILED (failures=1)'],
             'elapsed_s': 0.5}
    value['receipt_digest'] = hashlib.sha256(_json_bytes(value)).hexdigest()
    return value


class ConsumerBatchTests(BatchCase):
    """Every consumer of remote-test evidence applies the all-batches rule."""

    PR_FILES = [{'filename': 'tools/llmtool/llmtool/example_module.py'}]

    def task_with(self, count, *receipts, outcomes=()):
        task = task_for(count)
        task['remote_test_receipts'] = list(receipts)
        task['remote_test_outcomes'] = list(outcomes)
        task['completion']['state'] = 'PM_EVIDENCE_READY'
        return task

    def batches(self, count):
        return remote_test.remote_test_batches(module_names(count))

    def eligibility(self, task):
        """Run member_check with every gate before the EC2 receipt gate satisfied."""
        pull = {'state': 'open', 'merged': False, 'mergeable': True}
        with mock.patch.object(merge_train_eligibility, '_pr_snapshot',
                               return_value=(pull, SHA)), \
                mock.patch.object(merge_train_eligibility, '_review_version', return_value=1), \
                mock.patch.object(merge_train_eligibility, '_ci_gate',
                                  return_value=(None, (10, 11))), \
                mock.patch.object(merge_train_eligibility, '_rule_file_code',
                                  return_value=None), \
                mock.patch.object(merge_train_eligibility, '_live_train_code',
                                  return_value=None):
            return merge_train_eligibility.member_check(
                FakeStore(task), TASK_ID, 7, lambda path: list(self.PR_FILES),
                train_root=None, sleep=lambda seconds: None)

    def assert_unsatisfied(self, task):
        self.assertNotEqual(self.action(task), 'DISPATCH_REVIEW')
        result = self.eligibility(task)
        self.assertFalse(result['eligible'])
        self.assertEqual(result['code'], merge_train_eligibility.TRAIN_MEMBER_REMOTE_TEST_MISSING)

    def assert_satisfied(self, task):
        self.assertEqual(self.action(task), 'DISPATCH_REVIEW')
        result = self.eligibility(task)
        self.assertTrue(result['eligible'], result)
        return result

    def test_a_one_passing_batch_of_twelve_satisfies_neither_gate(self):
        first, _ = self.batches(12)
        task = self.task_with(12, sealed(first))
        self.assertEqual(self.action(task), 'RUN_REMOTE_TEST')
        self.assert_unsatisfied(task)
        task = self.task_with(12, sealed(self.batches(12)[1]))
        self.assertEqual(self.action(task), 'RUN_REMOTE_TEST')
        self.assert_unsatisfied(task)

    def test_a_a_newest_pass_never_hides_an_earlier_fail_in_the_merge_train_gate(self):
        first, second = self.batches(12)
        self.assert_unsatisfied(self.task_with(12, sealed(first, 'FAIL'), sealed(second)))
        self.assert_unsatisfied(self.task_with(12, sealed(first), sealed(second, 'FAIL')))
        self.assert_unsatisfied(
            self.task_with(12, sealed(first), sealed(second), sealed(first, 'FAIL')))

    def test_a_receipts_for_another_head_never_satisfy_the_merge_train_gate(self):
        first, second = self.batches(12)
        self.assert_unsatisfied(
            self.task_with(12, sealed(first, sha=OTHER_SHA), sealed(second, sha=OTHER_SHA)))

    def test_a_a_forged_receipt_never_satisfies_the_merge_train_gate(self):
        first, second = self.batches(12)
        forged = dict(sealed(second), ran_total=99)
        self.assert_unsatisfied(self.task_with(12, sealed(first), forged))

    def test_b_both_batches_at_the_head_satisfy_both_gates(self):
        first, second = self.batches(12)
        task = self.task_with(12, sealed(first), sealed(second))
        result = self.assert_satisfied(task)
        self.assertEqual(result['member']['remote_test_receipt_digest'],
                         task['remote_test_receipts'][-1]['receipt_digest'])

    def test_b_a_repass_after_a_fail_satisfies_both_gates(self):
        first, second = self.batches(12)
        task = self.task_with(12, sealed(first, 'FAIL'), sealed(first), sealed(second))
        self.assert_satisfied(task)

    def test_b_batch_runs_through_the_run_path_satisfy_both_gates(self):
        store, transport = FakeStore(self.task_with(12)), Transport()
        self.run_once(store, transport)
        self.assert_unsatisfied(store.task)
        self.run_once(store, transport)
        self.assert_satisfied(store.task)

    def test_c_an_old_module_denial_never_stands_in_for_the_batches(self):
        first, second = self.batches(12)
        denial = {'executor_task': EXECUTOR, 'sha': SHA, 'result': 'ADMISSION_DENIED',
                  'code': 'MODULE_NOT_ADMITTED', 'modules': []}
        task = self.task_with(12, outcomes=[denial])
        self.assertIsNone(self.status(task)[0])
        self.assertEqual(self.action(task), 'RUN_REMOTE_TEST')
        task = self.task_with(12, sealed(first), outcomes=[denial])
        self.assertEqual(self.action(task), 'RUN_REMOTE_TEST')
        task = self.task_with(12, sealed(first), sealed(second), outcomes=[denial])
        self.assertEqual(self.status(task)[0], 'PASS')
        self.assertEqual(self.action(task), 'DISPATCH_REVIEW')

    def test_c_a_module_denial_still_counts_for_eight_or_fewer_modules(self):
        denial = {'executor_task': EXECUTOR, 'sha': SHA, 'result': 'ADMISSION_DENIED',
                  'code': 'MODULE_NOT_ADMITTED', 'modules': []}
        task = self.task_with(8, outcomes=[denial])
        self.assertEqual(self.status(task)[0], 'ADMISSION_DENIED')
        self.assertEqual(self.action(task), 'DISPATCH_REVIEW')

    def test_d_the_direct_developer_path_selects_remote_batches_not_local(self):
        for count in (MAXIMUM, MAXIMUM + 1, 12, 2 * MAXIMUM):
            with self.subTest(count=count):
                task = task_for(count)
                read_paths = task['developer']['read_paths']
                self.assertEqual(
                    task_submission_cli._remote_receipt_test_policy(task, read_paths=read_paths),
                    'REMOTE_RECEIPT')
                self.assertEqual(
                    len(task_submission_cli._remote_task_test_paths(task, read_paths=read_paths)),
                    count)

    def test_review_handoff_shows_every_passing_batch(self):
        first, second = self.batches(12)
        task = self.task_with(12, sealed(first), sealed(second))
        line = task_submission_cli._review_ec2_line(task, SHA)
        rows = line.split('\n')
        self.assertEqual(len(rows), 2)
        for row, batch, sealed_receipt in zip(rows, (first, second),
                                              task['remote_test_receipts']):
            self.assertIn(f"modules={','.join(batch)}", row)
            self.assertIn(f"receipt={sealed_receipt['receipt_digest'][:8]}", row)
        self.assertTrue(rows[0].startswith('batch 1 of 2: '))
        self.assertTrue(rows[1].startswith('batch 2 of 2: '))

    def test_review_handoff_leaves_out_receipts_behind_a_later_fail_and_repeats(self):
        first, second = self.batches(12)
        task = self.task_with(12, sealed(first), sealed(second, 'FAIL'),
                              sealed(first), sealed(second), sealed(second))
        rows = task_submission_cli._review_ec2_line(task, SHA).split('\n')
        self.assertEqual(len(rows), 2)
        self.assertIn(f"modules={','.join(first)}", rows[0])
        self.assertIn(f"modules={','.join(second)}", rows[1])

    def test_review_handoff_for_eight_modules_stays_one_unlabelled_line(self):
        names = tuple(module_names(8))
        task = self.task_with(8, sealed(names))
        line = task_submission_cli._review_ec2_line(task, SHA)
        self.assertNotIn('\n', line)
        self.assertTrue(line.startswith('EC2 remote-test: result=PASS'))

    def test_review_handoff_with_one_batch_of_twelve_shows_the_failing_receipt_only(self):
        first, _ = self.batches(12)
        task = self.task_with(12, sealed(first, 'FAIL'))
        line = task_submission_cli._review_ec2_line(task, SHA)
        self.assertNotIn('\n', line)
        self.assertIn('result=FAIL', line)

    def match(self, task):
        paths = task_submission_cli._remote_task_test_paths(
            task, read_paths=task['developer']['read_paths'])
        return task_submission_cli._matching_remote_test_receipt(
            task, executor_task=EXECUTOR, source_sha=SHA, paths=paths)

    def test_d_direct_evidence_needs_every_batch_to_pass_at_the_head(self):
        first, second = self.batches(12)
        task = self.task_with(12, full_receipt(first))
        self.assertEqual(self.match(task), (None, 'REMOTE_TEST_RECEIPT_MISSING'))
        task = self.task_with(12, full_receipt(first), full_receipt(second))
        receipt, gate = self.match(task)
        self.assertIsNone(gate)
        self.assertEqual(receipt['modules'], list(second))
        task = self.task_with(12, full_receipt(first, sha=OTHER_SHA), full_receipt(second))
        self.assertEqual(self.match(task), (None, 'REMOTE_TEST_RECEIPT_MISSING'))

    def test_d_direct_evidence_ignores_a_pass_that_a_later_fail_supersedes(self):
        first, second = self.batches(12)
        task = self.task_with(
            12, full_receipt(first), full_receipt(second), full_receipt(first, 'FAIL'))
        self.assertEqual(self.match(task), (None, 'REMOTE_TEST_RECEIPT_MISSING'))
        task['remote_test_receipts'].append(full_receipt(first))
        self.assertEqual(self.match(task), (None, 'REMOTE_TEST_RECEIPT_MISSING'))
        task['remote_test_receipts'].append(full_receipt(second))
        self.assertIsNone(self.match(task)[1])

    def test_d_direct_evidence_for_eight_modules_still_needs_one_covering_pass(self):
        names = tuple(module_names(8))
        task = self.task_with(8, full_receipt(names))
        self.assertIsNone(self.match(task)[1])
        task = self.task_with(8, full_receipt(names[:4]), full_receipt(names[4:]))
        self.assertEqual(self.match(task), (None, 'REMOTE_TEST_RECEIPT_MISSING'))


class SpecPreflightBatchTests(unittest.TestCase):
    def modules(self, count):
        return [f'tools/llmtool/tests/test_case_{index}.py' for index in range(count)]

    def test_f_more_than_eight_modules_are_accepted_and_report_the_batch_count(self):
        self.assertIsNone(spec_preflight.declared_module_batch_note(
            {'read': self.modules(8)}, maximum=8))
        self.assertEqual(
            spec_preflight.declared_module_batch_note({'read': self.modules(9)}, maximum=8),
            'test_module_batches:count=9:max=8:batches=2')
        self.assertEqual(
            spec_preflight.declared_module_batch_note({'read': self.modules(17)}, maximum=8),
            'test_module_batches:count=17:max=8:batches=3')

    def test_f_preflight_main_passes_nine_modules_and_names_two_batches(self):
        spec = {'request': 'request text', 'summary': 'summary text', 'goal': 'goal text',
                'acceptance': ['acceptance text'], 'read': self.modules(9)}
        with tempfile.TemporaryDirectory() as directory:
            spec_path = Path(directory) / 'spec.json'
            spec_path.write_text(json.dumps(spec), encoding='utf-8')
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(remote_test, '_MAX_REMOTE_MODULES', 8), \
                    mock.patch.object(spec_preflight, 'large_file_lines', return_value=2000), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = spec_preflight.main([str(spec_path)])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().strip(), 'SPEC_PREFLIGHT_OK')
        self.assertIn('test_module_batches:count=9:max=8:batches=2', err.getvalue())


if __name__ == '__main__':
    unittest.main()
