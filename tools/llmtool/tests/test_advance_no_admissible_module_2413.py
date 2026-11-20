"""#2413: a PR with no EC2-admissible module completes the remote-test step."""
import hashlib
import json
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
TASK = '1' * 32
RUN = '2' * 32
PR = 2413
AT = '2026-01-01T00:00:00Z'
TEST_PATH = 'tools/llmtool/tests/test_advance_no_admissible_module_2413.py'


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


class RunStore:
    """The small TaskStore surface run_remote_test needs for one admission."""

    def __init__(self, task):
        self.task = task
        self.outcomes = []
        self.published = []

    def get(self, task_id):
        return self.task

    def record_published_pr(self, task_id, pr, head):
        self.published.append((task_id, pr, head))

    def append_remote_test_outcome(self, task_id, outcome):
        self.outcomes.append(outcome)
        return {'outcome': outcome, 'counter': len(self.outcomes) - 1}


CONFIG = {'host': 'example.invalid', 'user': 'tester',
          'repo_path': '/srv/repo', 'python': '/usr/bin/python3'}


def remote_task(paths):
    return {
        'task_id': TASK, 'run_id': RUN, 'pm_session_id': 'pm-session',
        'completion': {'executor_task_id': EXEC,
                       'evidence': {'common_dir': '/canonical/.git',
                                    'result': {'result_head': HEAD},
                                    'review_pr_ci': {'pr_number': PR,
                                                     'head_sha': HEAD}}},
        'developer': {'read_paths': list(paths), 'write_paths': list(paths)},
        'remote_test_receipts': [],
    }


def run_remote(task, get, transport=None):
    store = RunStore(task)
    registration = type('Registration', (), {'result_sha': HEAD, 'root': '/d6'})()
    with mock.patch.object(rt, 'read_d6_registration', return_value=registration), \
            mock.patch.object(rt, '_config', return_value=CONFIG), \
            mock.patch.object(rt, '_ssh_timeout', return_value=5), \
            mock.patch.object(rt, 'record_remote_test_outcome_event',
                              return_value={'event_id': 'e' * 32}):
        value = rt.run_remote_test(
            TASK, executor_task=EXEC, sha=HEAD, ref=f'refs/pull/{PR}/head',
            pr=PR, store=store, get=get, transport=transport)
    return value, store


def synthetic_contract():
    """A synthetic Task contract in the exact TaskContract field set.

    The fields are those of task_verification.TaskContract, which the real
    store parses on every write and read; no value is credential-shaped.
    """
    return {
        'schema_version': 1, 'task_id': TASK, 'contract_version': 1,
        'previous_digest': None,
        'goal': 'Persist a no-admissible-module outcome.',
        'authorized_scope': [TEST_PATH], 'forbidden_scope': [],
        'criteria': [{'criterion_id': 'persisted-outcome',
                      'kind': 'DETERMINISTIC_CHECK',
                      'description': 'The persisted outcome is reloaded from the store.'}],
    }


def real_record():
    """A complete Task record the real TaskStore both writes and reloads (#2413 F2)."""
    contract = synthetic_contract()
    # The store re-serialises the contract this way before parsing it.
    parsed = ts.parse_task_contract(json.dumps(
        contract, ensure_ascii=True, separators=(',', ':')))
    request = '{}'
    return {
        'schema_version': 2, 'task_id': TASK, 'run_id': RUN,
        'pm_session_id': 'pm-session', 'pm_provider_profile': 'codex',
        'pm_provider_type': 'codex-cli', 'pm_model': None,
        'backend_session_semantics': 'EPHEMERAL_SINGLE_CALL',
        'native_session_id': None, 'state': 'PM_RESPONDED',
        'created_at': AT, 'updated_at': AT,
        'task_contract': contract,
        'task_binding': parsed.binding(RUN).to_dict(),
        'request': request,
        'request_sha256': hashlib.sha256(request.encode('utf-8')).hexdigest(),
        'response': '{"summary":"synthetic no-module outcome persistence"}',
        'usage': None, 'error_code': None,
        'developer': None, 'pm_assignment': None,
        'published_pr': {'pr': PR, 'head_sha': HEAD, 'recorded_at': AT},
    }


def real_store(root):
    """The real TaskStore on a private temporary root.

    The store opens every root component without following links, so the
    root is resolved first (a temporary directory may sit under a symlink).
    """
    return ts.TaskStore(root=Path(root).resolve())


class RecordedOutcomeTests(unittest.TestCase):
    """Exercise append_remote_test_outcome on a real written and reloaded Task."""

    def test_record_no_admissible_module_uses_the_real_store_append(self):
        with tempfile.TemporaryDirectory() as root:
            store = real_store(root)
            record = real_record()
            store.create(record)
            with mock.patch.object(rt, 'record_remote_test_outcome_event',
                                   return_value={'event_id': 'e' * 32}):
                value = rt._record_no_admissible_module(
                    store, TASK, record, executor_task=EXEC, sha=HEAD, pr=PR)
            reloaded = store.get(TASK)
        self.assertTrue(value['persisted'])
        self.assertEqual(value['outcome']['result'], rt.NOT_REQUIRED)
        self.assertEqual(value['outcome']['code'], rt.NO_ADMISSIBLE_MODULE)
        self.assertEqual(value['outcome']['executor_task'], EXEC)
        self.assertEqual(value['outcome']['sha'], HEAD)
        self.assertEqual(value['outcome']['modules'], [])
        self.assertEqual(value['outcome']['pr'], PR)
        self.assertEqual(reloaded['remote_test_outcomes'], [value['outcome']])

    def test_unpersisted_skip_is_not_reported_as_not_required(self):
        with tempfile.TemporaryDirectory() as root:
            store = real_store(root)
            record = real_record()
            record['published_pr'] = dict(record['published_pr'], pr=PR + 1)
            store.create(record)
            value = rt._record_no_admissible_module(
                store, TASK, record, executor_task=EXEC, sha=HEAD, pr=PR)
            reloaded = store.get(TASK)
        self.assertFalse(value['persisted'])
        self.assertNotEqual(value['result'], rt.NOT_REQUIRED)
        self.assertEqual(value['code'], 'REMOTE_TEST_OUTCOME_NOT_PERSISTED')
        self.assertEqual(reloaded.get('remote_test_outcomes', []), [])

    def test_real_append_refuses_a_different_published_pr(self):
        with tempfile.TemporaryDirectory() as root:
            store = real_store(root)
            record = real_record()
            record['published_pr'] = dict(record['published_pr'], pr=PR + 1)
            store.create(record)
            with self.assertRaises(LLMToolError):
                store.append_remote_test_outcome(TASK, skip_outcome())
            reloaded = store.get(TASK)
        self.assertEqual(reloaded.get('remote_test_outcomes', []), [])

    def test_real_append_refuses_a_different_published_head(self):
        with tempfile.TemporaryDirectory() as root:
            store = real_store(root)
            record = real_record()
            record['published_pr'] = dict(record['published_pr'], head_sha=OTHER_HEAD)
            store.create(record)
            with self.assertRaises(LLMToolError):
                store.append_remote_test_outcome(TASK, skip_outcome())
            reloaded = store.get(TASK)
        self.assertEqual(reloaded.get('remote_test_outcomes', []), [])

    def test_real_append_refuses_a_task_with_no_published_pr(self):
        with tempfile.TemporaryDirectory() as root:
            store = real_store(root)
            record = real_record()
            del record['published_pr']
            store.create(record)
            with self.assertRaises(LLMToolError):
                store.append_remote_test_outcome(TASK, skip_outcome())
            reloaded = store.get(TASK)
        self.assertEqual(reloaded.get('remote_test_outcomes', []), [])

    def test_reloaded_skip_satisfies_only_its_pr_and_head(self):
        with tempfile.TemporaryDirectory() as root:
            store = real_store(root)
            store.create(real_record())
            store.append_remote_test_outcome(TASK, skip_outcome())
            replay = store.append_remote_test_outcome(TASK, skip_outcome())
            reloaded = store.get(TASK)
        self.assertFalse(replay['created'])
        self.assertEqual(reloaded['remote_test_outcomes'], [skip_outcome()])
        # Status reads the outcomes exactly as the real store reloaded them; only
        # the executor binding, normally from the completion, is supplied here.
        view = dict(reloaded, completion={'executor_task_id': EXEC})

        def status(head, pr):
            return task_advance._remote_test_status(view, {'result_head': head}, pr)[0]

        self.assertEqual(status(HEAD, PR), rt.NOT_REQUIRED)
        self.assertIsNone(status(OTHER_HEAD, PR))
        self.assertIsNone(status(HEAD, PR + 1))
        self.assertIsNone(status(HEAD, None))


class RemoteTestAdmissionTests(unittest.TestCase):
    def offline_transport(self, calls):
        def transport(argv, script, timeout):
            calls.append(argv)
            raise OSError('offline')
        return transport

    def test_pr_test_module_runs_when_task_declares_none(self):
        calls = []
        value, store = run_remote(
            remote_task([]), get_at_head(['tools/llmtool/tests/test_alpha.py']),
            self.offline_transport(calls))
        self.assertEqual((value['result'], value['code']),
                         ('UNAVAILABLE', 'SSH_UNAVAILABLE'))
        self.assertEqual(store.outcomes[-1]['modules'], ['test_alpha'])
        self.assertIn('test_alpha', calls[0])
        self.assertEqual(store.published, [(TASK, PR, HEAD)])

    def test_declared_module_still_runs_as_before(self):
        calls = []
        value, store = run_remote(
            remote_task(['tools/llmtool/tests/test_beta.py']),
            get_at_head(['tools/llmtool/tests/test_alpha.py']),
            self.offline_transport(calls))
        self.assertEqual(value['code'], 'SSH_UNAVAILABLE')
        self.assertEqual(store.outcomes[-1]['modules'], ['test_beta'])
        self.assertIn('test_beta', calls[0])
        self.assertNotIn('test_alpha', calls[0])

    def test_docs_only_records_a_head_and_pr_bound_skip(self):
        value, store = run_remote(
            remote_task([]), get_at_head(['docs/readme.md']))
        self.assertTrue(value['persisted'])
        self.assertEqual(value['result'], rt.NOT_REQUIRED)
        self.assertEqual(value['code'], rt.NO_ADMISSIBLE_MODULE)
        self.assertEqual(len(store.outcomes), 1)
        outcome = store.outcomes[0]
        self.assertEqual((outcome['result'], outcome['code']), (
            rt.NOT_REQUIRED, rt.NO_ADMISSIBLE_MODULE))
        self.assertEqual((outcome['executor_task'], outcome['sha'], outcome['modules'],
                          outcome['pr']), (EXEC, HEAD, [], PR))

    def test_unreadable_change_data_fails_closed(self):
        def get(path):
            if path == f'pulls/{PR}':
                return {'number': PR, 'head': {'sha': HEAD}}
            raise OSError('unreadable')

        value, store = run_remote(remote_task([]), get)
        self.assertEqual((value['result'], value['code']),
                         ('ADMISSION_DENIED', 'MODULE_NOT_ADMITTED'))
        self.assertEqual(store.outcomes[-1]['result'], 'ADMISSION_DENIED')

    def test_malformed_change_data_fails_closed(self):
        value, store = run_remote(
            remote_task([]), get_at_head([{'filename': 1}]))
        self.assertEqual((value['result'], value['code']),
                         ('ADMISSION_DENIED', 'MODULE_NOT_ADMITTED'))
        self.assertEqual(store.outcomes[-1]['result'], 'ADMISSION_DENIED')


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
