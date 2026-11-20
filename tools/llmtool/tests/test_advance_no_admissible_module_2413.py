"""#2413: a PR with no EC2-admissible module completes the remote-test step."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llmtool import remote_test as rt  # noqa: E402
from llmtool import task_advance  # noqa: E402
from llmtool import task_store as ts  # noqa: E402
from llmtool.errors import LLMToolError  # noqa: E402

HEAD = 'a' * 40
OTHER_HEAD = 'b' * 40
EXEC = 'c' * 32
PR = 2413
AT = '2026-01-01T00:00:00Z'


def files(*paths):
    return [{'filename': path} for path in paths]


def get_at_head(paths, head=HEAD):
    """A get() that serves the PR snapshot and its changed files at one head."""
    def get(path):
        if path == f'pulls/{PR}':
            return {'number': PR, 'head': {'sha': head}}
        return files(*paths)
    return get


class ChangeDataTests(unittest.TestCase):
    def test_docs_only_pr_has_no_admissible_module(self):
        self.assertIs(rt._no_admissible_pr_module(
            PR, HEAD, get=get_at_head(['docs/readme.md'])), True)

    def test_scripts_test_is_not_ec2_admissible(self):
        self.assertIs(rt._no_admissible_pr_module(
            PR, HEAD, get=get_at_head(['scripts/test_llmtool_monitor.py'])), True)

    def test_tools_test_module_keeps_the_existing_run(self):
        self.assertIs(rt._no_admissible_pr_module(
            PR, HEAD, get=get_at_head(['tools/llmtool/tests/test_alpha.py'])), False)

    def test_rename_old_path_also_counts(self):
        batch = [{'filename': 'docs/readme.md',
                  'previous_filename': 'tools/llmtool/tests/test_alpha.py'}]

        def get(path):
            if path == f'pulls/{PR}':
                return {'number': PR, 'head': {'sha': HEAD}}
            return batch

        self.assertIs(rt._no_admissible_pr_module(PR, HEAD, get=get), False)

    def test_moved_pr_head_is_unknown(self):
        self.assertIsNone(rt._no_admissible_pr_module(
            PR, HEAD, get=get_at_head(['docs/readme.md'], head=OTHER_HEAD)))

    def test_head_move_during_the_read_is_unknown(self):
        calls = []

        def get(path):
            if path.startswith(f'pulls/{PR}/files'):
                return files('docs/readme.md')
            calls.append(path)
            return {'number': PR,
                    'head': {'sha': HEAD if len(calls) == 1 else OTHER_HEAD}}

        self.assertIsNone(rt._no_admissible_pr_module(PR, HEAD, get=get))

    def test_head_move_between_pages_is_unknown(self):
        first = files(*(f'docs/{index}.md' for index in range(100)))
        heads = []

        def get(path):
            if path == f'pulls/{PR}':
                heads.append(path)
                return {'number': PR,
                        'head': {'sha': HEAD if len(heads) < 3 else OTHER_HEAD}}
            return first if '&page=' not in path else files('docs/last.md')

        self.assertIsNone(rt._no_admissible_pr_module(PR, HEAD, get=get))
        self.assertEqual(len(heads), 3)

    def test_unreadable_change_data_is_unknown(self):
        def get(path):
            if path == f'pulls/{PR}':
                return {'number': PR, 'head': {'sha': HEAD}}
            raise OSError('unreadable')
        self.assertIsNone(rt._no_admissible_pr_module(PR, HEAD, get=get))

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
                def get(path, value=batch):
                    if path == f'pulls/{PR}':
                        return {'number': PR, 'head': {'sha': HEAD}}
                    return value
                self.assertIsNone(rt._no_admissible_pr_module(PR, HEAD, get=get))

    def test_empty_change_list_is_unknown(self):
        self.assertIsNone(rt._no_admissible_pr_module(
            PR, HEAD, get=get_at_head([])))

    def test_full_last_page_is_unknown(self):
        full = files(*(f'docs/{index}.md' for index in range(100)))
        self.assertIsNone(rt._no_admissible_pr_module(
            PR, HEAD, get=get_at_head_full(full)))

    def test_every_page_is_read(self):
        first = files(*(f'docs/{index}.md' for index in range(100)))
        calls = []

        def get(path):
            if path == f'pulls/{PR}':
                return {'number': PR, 'head': {'sha': HEAD}}
            calls.append(path)
            return first if '&page=' not in path else files('docs/last.md')

        self.assertIs(rt._no_admissible_pr_module(PR, HEAD, get=get), True)
        self.assertEqual(calls[0], f'pulls/{PR}/files?per_page=100')
        self.assertEqual(calls[-1], f'pulls/{PR}/files?per_page=100&page=2')


def get_at_head_full(batch):
    def get(path):
        if path == f'pulls/{PR}':
            return {'number': PR, 'head': {'sha': HEAD}}
        return batch
    return get


def skip_outcome(**changes):
    value = {'result': rt.NOT_REQUIRED, 'code': rt.NO_ADMISSIBLE_MODULE,
             'executor_task': EXEC, 'sha': HEAD, 'modules': [], 'pr': PR,
             'at': AT}
    value.update(changes)
    return value


class StoreOutcomeTests(unittest.TestCase):
    def test_real_store_accepts_the_not_required_outcome(self):
        ts._validate_remote_test_outcomes(
            {'remote_test_outcomes': [skip_outcome()]})

    def test_real_store_refuses_an_unbound_not_required_outcome(self):
        unbound = skip_outcome()
        del unbound['pr']
        with self.assertRaises(LLMToolError):
            ts._validate_remote_test_outcomes(
                {'remote_test_outcomes': [unbound]})
        with self.assertRaises(LLMToolError):
            ts._validate_remote_test_outcomes(
                {'remote_test_outcomes': [skip_outcome(modules=['test_alpha'])]})


class RecordedOutcomeTests(unittest.TestCase):
    """Exercise TaskStore.append_remote_test_outcome and its real validator.

    Only the unrelated Task-envelope I/O is stubbed: the outcome shape,
    NOT_REQUIRED admission, PR/HEAD binding and append are the real store code.
    """
    def store_state(self):
        return {'task_id': 'task-2413', 'run_id': 'run-2413',
                'remote_test_outcomes': [],
                'published_pr': {'pr': PR, 'head_sha': HEAD, 'recorded_at': AT},
                'record_sha256': '0' * 64}

    def test_record_no_admissible_module_uses_the_real_store_append(self):
        with tempfile.TemporaryDirectory() as root:
            store = ts.TaskStore(root=root)
            current = self.store_state()
            written = {}

            def update(task_id, *, expected, changes, **kwargs):
                written.update(changes)
                return dict(current, **changes)

            with mock.patch.object(ts.TaskStore, 'get', return_value=current), \
                    mock.patch.object(ts.TaskStore, '_update', side_effect=update), \
                    mock.patch.object(rt, 'record_remote_test_outcome_event',
                                      return_value={'event_id': 'e' * 32}):
                value = rt._record_no_admissible_module(
                    store, 'task-2413', {}, executor_task=EXEC, sha=HEAD, pr=PR)
        self.assertTrue(value['persisted'])
        self.assertEqual(value['outcome']['result'], rt.NOT_REQUIRED)
        self.assertEqual(value['outcome']['code'], rt.NO_ADMISSIBLE_MODULE)
        self.assertEqual(value['outcome']['executor_task'], EXEC)
        self.assertEqual(value['outcome']['sha'], HEAD)
        self.assertEqual(value['outcome']['modules'], [])
        self.assertEqual(value['outcome']['pr'], PR)
        self.assertEqual(written['remote_test_outcomes'], [value['outcome']])

    def test_unpersisted_skip_is_not_reported_as_not_required(self):
        with tempfile.TemporaryDirectory() as root:
            store = ts.TaskStore(root=root)
            current = self.store_state()
            current['published_pr'] = None
            with mock.patch.object(ts.TaskStore, 'get', return_value=current), \
                    mock.patch.object(ts.TaskStore, '_update') as update:
                value = rt._record_no_admissible_module(
                    store, 'task-2413', {}, executor_task=EXEC, sha=HEAD, pr=PR)
        self.assertFalse(value['persisted'])
        self.assertNotEqual(value['result'], rt.NOT_REQUIRED)
        self.assertEqual(value['code'], 'REMOTE_TEST_OUTCOME_NOT_PERSISTED')
        self.assertFalse(update.called)

    def test_real_append_refuses_a_different_published_pr(self):
        with tempfile.TemporaryDirectory() as root:
            store = ts.TaskStore(root=root)
            current = self.store_state()
            current['published_pr'] = dict(current['published_pr'], pr=PR + 1)
            with mock.patch.object(ts.TaskStore, 'get', return_value=current):
                with self.assertRaises(LLMToolError):
                    store.append_remote_test_outcome(
                        'task-2413', skip_outcome())


class AdvanceStatusTests(unittest.TestCase):
    def record(self, outcome):
        return {'task_id': 'task-2413', 'run_id': 'run-2413',
                'completion': {'executor_task_id': EXEC},
                'remote_test_outcomes': [outcome]}

    def status(self, outcome, pr=PR):
        return task_advance._remote_test_status(
            self.record(outcome), {'result_head': HEAD}, pr)[0]

    def test_exact_head_skip_is_satisfied(self):
        self.assertEqual(self.status(skip_outcome()), rt.NOT_REQUIRED)

    def test_other_head_is_not_satisfied(self):
        self.assertIsNone(self.status(skip_outcome(sha=OTHER_HEAD)))

    def test_other_executor_is_not_satisfied(self):
        self.assertIsNone(self.status(skip_outcome(executor_task='d' * 32)))

    def test_nonempty_modules_are_not_satisfied(self):
        self.assertIsNone(self.status(skip_outcome(modules=['test_alpha'])))

    def test_other_code_is_not_satisfied(self):
        self.assertIsNone(self.status(skip_outcome(code='MODULE_NOT_ADMITTED')))

    def test_other_or_missing_pr_is_not_satisfied(self):
        self.assertIsNone(self.status(skip_outcome(pr=PR + 1)))
        self.assertIsNone(self.status(skip_outcome(), pr=None))

    def test_pre_review_continues_after_the_skip(self):
        action = task_advance._pre_review_action(
            self.record(skip_outcome()), {'result_head': HEAD},
            {'number': PR, 'merged': False}, {'events': [], 'repair_claims': []}, HEAD)
        self.assertEqual(action, 'DISPATCH_REVIEW')

    def test_decide_continues_after_the_skip(self):
        record = self.record(skip_outcome())
        record['developer'] = {'state': 'DEVELOPER_RESPONDED'}
        d6 = {'result_head': HEAD, 'recorded_result_head': HEAD,
              'dirty': False, 'state': 'CLEAN'}
        pr = {'number': PR, 'head': {'sha': HEAD}, 'merged': False}
        related = {'events': [], 'repair_claims': []}
        self.assertEqual(task_advance.decide(record, d6, pr, related), 'DISPATCH_REVIEW')

    def test_unknown_status_still_runs_the_remote_test(self):
        action = task_advance._pre_review_action(
            self.record(skip_outcome(code='MODULE_NOT_ADMITTED')),
            {'result_head': HEAD}, {'number': PR, 'merged': False},
            {'events': [], 'repair_claims': []}, HEAD)
        self.assertEqual(action, 'RUN_REMOTE_TEST')

    def test_missing_outcome_still_runs_the_remote_test(self):
        action = task_advance._pre_review_action(
            {'task_id': 'task-2413', 'run_id': 'run-2413',
             'completion': {'executor_task_id': EXEC},
             'remote_test_receipts': []},
            {'result_head': HEAD}, {'number': PR, 'merged': False},
            {'events': [], 'repair_claims': []}, HEAD)
        self.assertEqual(action, 'RUN_REMOTE_TEST')


class ExistingAdmissionTests(unittest.TestCase):
    def test_task_test_modules_are_still_admitted(self):
        task = {'developer': {'write_paths': ['tools/llmtool/tests/test_alpha.py']}}
        self.assertEqual(rt._task_path_modules(task), ('test_alpha',))


if __name__ == '__main__':
    unittest.main()
