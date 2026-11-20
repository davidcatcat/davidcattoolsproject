"""#2413: a PR with no EC2-admissible module completes the remote-test step."""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llmtool import remote_test as rt  # noqa: E402
from llmtool import task_advance  # noqa: E402

HEAD = 'a' * 40
OTHER_HEAD = 'b' * 40
EXEC = 'c' * 32
PR = 2413


def files(*paths):
    return [{'filename': path} for path in paths]


class ChangeDataTests(unittest.TestCase):
    def test_docs_only_pr_has_no_admissible_module(self):
        self.assertIs(rt._no_admissible_pr_module(
            PR, get=lambda path: files('docs/readme.md')), True)

    def test_scripts_test_is_not_ec2_admissible(self):
        self.assertIs(rt._no_admissible_pr_module(
            PR, get=lambda path: files('scripts/test_llmtool_monitor.py')), True)

    def test_tools_test_module_keeps_the_existing_run(self):
        self.assertIs(rt._no_admissible_pr_module(
            PR, get=lambda path: files('tools/llmtool/tests/test_alpha.py')), False)

    def test_rename_old_path_also_counts(self):
        batch = [{'filename': 'docs/readme.md',
                  'previous_filename': 'tools/llmtool/tests/test_alpha.py'}]
        self.assertIs(rt._no_admissible_pr_module(PR, get=lambda path: batch), False)

    def test_unreadable_change_data_is_unknown(self):
        def get(path):
            raise OSError('unreadable')
        self.assertIsNone(rt._no_admissible_pr_module(PR, get=get))

    def test_malformed_change_data_is_unknown(self):
        cases = (
            None,
            {},
            [None],
            [{'filename': 1}],
            [{'filename': 'docs/readme.md', 'previous_filename': 1}],
        )
        for batch in cases:
            with self.subTest(batch=batch):
                self.assertIsNone(rt._no_admissible_pr_module(
                    PR, get=lambda path, value=batch: value))

    def test_empty_change_list_is_unknown(self):
        self.assertIsNone(rt._no_admissible_pr_module(PR, get=lambda path: []))

    def test_full_last_page_is_unknown(self):
        full = files(*(f'docs/{index}.md' for index in range(100)))
        self.assertIsNone(rt._no_admissible_pr_module(PR, get=lambda path: full))

    def test_every_page_is_read(self):
        first = files(*(f'docs/{index}.md' for index in range(100)))
        calls = []

        def get(path):
            calls.append(path)
            return first if path.endswith('page=1') or '&page=' not in path else files(
                'docs/last.md')

        self.assertIs(rt._no_admissible_pr_module(PR, get=get), True)
        self.assertEqual(calls[0], 'pulls/2413/files?per_page=100')
        self.assertEqual(calls[-1], 'pulls/2413/files?per_page=100&page=2')


class RecordedOutcomeTests(unittest.TestCase):
    class Store:
        def __init__(self):
            self.saved = []

        def append_remote_test_outcome(self, task_id, outcome):
            self.saved.append((task_id, outcome))
            return {'outcome': outcome, 'counter': len(self.saved)}

    def test_skip_outcome_binds_task_executor_and_exact_head(self):
        store = self.Store()
        with mock.patch.object(rt, 'record_remote_test_outcome_event',
                               return_value={'event_id': 'e' * 32}):
            value = rt._record_no_admissible_module(
                store, 'task-2413', {}, executor_task=EXEC, sha=HEAD)
        task_id, outcome = store.saved[0]
        self.assertEqual(task_id, 'task-2413')
        self.assertEqual(outcome['result'], rt.NOT_REQUIRED)
        self.assertEqual(outcome['code'], rt.NO_ADMISSIBLE_MODULE)
        self.assertEqual(outcome['executor_task'], EXEC)
        self.assertEqual(outcome['sha'], HEAD)
        self.assertEqual(outcome['modules'], [])
        self.assertTrue(value['persisted'])


class AdvanceStatusTests(unittest.TestCase):
    def record(self, outcome):
        return {'task_id': 'task-2413', 'run_id': 'run-2413',
                'completion': {'executor_task_id': EXEC},
                'remote_test_outcomes': [outcome]}

    def outcome(self, **changes):
        value = {'result': rt.NOT_REQUIRED, 'code': rt.NO_ADMISSIBLE_MODULE,
                 'executor_task': EXEC, 'sha': HEAD, 'modules': []}
        value.update(changes)
        return value

    def status(self, outcome):
        return task_advance._remote_test_status(
            self.record(outcome), {'result_head': HEAD})[0]

    def test_exact_head_skip_is_satisfied(self):
        self.assertEqual(self.status(self.outcome()), rt.NOT_REQUIRED)

    def test_other_head_is_not_satisfied(self):
        self.assertIsNone(self.status(self.outcome(sha=OTHER_HEAD)))

    def test_other_executor_is_not_satisfied(self):
        self.assertIsNone(self.status(self.outcome(executor_task='d' * 32)))

    def test_nonempty_modules_are_not_satisfied(self):
        self.assertIsNone(self.status(self.outcome(modules=['test_alpha'])))

    def test_other_code_is_not_satisfied(self):
        self.assertIsNone(self.status(self.outcome(code='MODULE_NOT_ADMITTED')))

    def test_pre_review_continues_after_the_skip(self):
        action = task_advance._pre_review_action(
            self.record(self.outcome()), {'result_head': HEAD},
            {'merged': False}, {'events': [], 'repair_claims': []}, HEAD)
        self.assertEqual(action, 'DISPATCH_REVIEW')

    def test_decide_continues_after_the_skip(self):
        record = self.record(self.outcome())
        record['developer'] = {'state': 'DEVELOPER_RESPONDED'}
        d6 = {'result_head': HEAD, 'recorded_result_head': HEAD,
              'dirty': False, 'state': 'CLEAN'}
        pr = {'number': PR, 'head': {'sha': HEAD}, 'merged': False}
        related = {'events': [], 'repair_claims': []}
        self.assertEqual(task_advance.decide(record, d6, pr, related), 'DISPATCH_REVIEW')

    def test_unknown_status_still_runs_the_remote_test(self):
        action = task_advance._pre_review_action(
            self.record(self.outcome(code='MODULE_NOT_ADMITTED')),
            {'result_head': HEAD}, {'merged': False},
            {'events': [], 'repair_claims': []}, HEAD)
        self.assertEqual(action, 'RUN_REMOTE_TEST')

    def test_missing_outcome_still_runs_the_remote_test(self):
        action = task_advance._pre_review_action(
            {'task_id': 'task-2413', 'run_id': 'run-2413',
             'completion': {'executor_task_id': EXEC},
             'remote_test_receipts': []},
            {'result_head': HEAD}, {'merged': False},
            {'events': [], 'repair_claims': []}, HEAD)
        self.assertEqual(action, 'RUN_REMOTE_TEST')


class ExistingAdmissionTests(unittest.TestCase):
    def test_task_test_modules_are_still_admitted(self):
        task = {'developer': {'write_paths': ['tools/llmtool/tests/test_alpha.py']}}
        self.assertEqual(rt._task_path_modules(task), ('test_alpha',))


if __name__ == '__main__':
    unittest.main()
