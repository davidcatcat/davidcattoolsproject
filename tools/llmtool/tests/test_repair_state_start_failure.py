"""#2188 follow-up: a repair whose Claude Developer stopped with CLAUDE_STATUS_FAILED.

PR #2215 v91: a status failure with no native session id and no diff does not
prove Claude never ran, so derive_repair_state keeps it UNKNOWN. The
review-repair decision retries it only when the repair Task's executor D6,
read fresh with task_completion.collect_git_snapshot, is clean at the claim
head. A dirty D6, a moved HEAD, an unreadable D6 or a run with a native
session id stays REPAIR_OUTCOME_UNKNOWN.

PR #2217 v91: a clean git status is not enough. A git-ignored file, an empty
directory, a nested .git entry or a hidden skip-worktree change left by the
failed attempt also keeps it REPAIR_OUTCOME_UNKNOWN, and so does a run whose
process cleanup is not CONFIRMED. The retry goes through review_repair with
auto=True and the old claim id, so its caps still apply.

Test data rule (Owner #763 A): no test here holds a password-, API-key-,
token- or other credential-shaped string. The D6 registry and Git reads are
injected for the decision tests; the decision logic under test is the real
one. The workspace tests run the real checks on a throwaway temp repository.
"""

import os
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from llmtool import task_advance, task_completion

import review_trust_fixture
import worker_policy_fixture


_SESSION = '12345678-1234-4234-8234-123456789abc'
_HEAD = 'a' * 40
_MOVED = 'b' * 40
_EXECUTOR = 'c' * 32
_REPAIR_TASK = 'd' * 32
_TASK = 'e' * 32
_ROOT = '/registered/d6'
_COMMON = '/repo/.git'
_BRANCH = 'task-branch'


def setUpModule():
    review_trust_fixture.start_module()
    worker_policy_fixture.start_module()


def tearDownModule():
    worker_policy_fixture.stop_module()
    review_trust_fixture.stop_module()


def _execution(**fields):
    execution = {'status': 'BLOCKED', 'native_session_id': None,
                 'process_exit_code': 1, 'stdout_sha256': None, 'elapsed_ms': 7,
                 'cleanup_status': 'CONFIRMED', 'usage_source': 'CLI_REPORTED',
                 'source_bytes': None, 'source_file_count': None,
                 'source_manifest_sha256': None}
    execution.update(fields)
    return execution


def _record(error_code='CLAUDE_STATUS_FAILED', execution=None, *,
            state='BLOCKED', top_state='PM_RESPONDED'):
    if execution is None:
        execution = _execution()
    return {'state': top_state,
            'developer': {'state': state, 'error_code': error_code,
                          'provider_profile': 'claude', 'artifact': None,
                          'executor_task_id': _EXECUTOR,
                          'execution': execution}}


def _repair_claim(repair_record):
    """Build the repair claim the advance loop builds from a repair record."""
    state = task_advance.derive_repair_state(repair_record)
    claim = {'head_sha': _HEAD, 'state': 'DISPATCHED', 'repair_task_id': _REPAIR_TASK,
             'repair_state': state}
    executor = (task_advance._claude_status_failed_executor(repair_record)
                if state == 'UNKNOWN' else None)
    if executor is not None:
        claim['repair_start_failure_executor'] = executor
    return claim


def _decide(repair_record):
    record = {'developer': {'state': 'DEVELOPER_RESPONDED'},
              'review_dispatch_claim': [{'pr': 7, 'head_sha': _HEAD,
                                         'state': 'COMPLETED', 'version': 1}]}
    d6 = {'root': _ROOT, 'common_dir': _COMMON, 'branch': _BRANCH,
          'result_head': _HEAD, 'recorded_result_head': _HEAD,
          'dirty': False, 'state': 'CLEAN'}
    pr = {'number': 7, 'head': {'sha': _HEAD}}
    related = {'events': [{'source': 'REVIEWER', 'source_sha': _HEAD, 'version': 1,
                           'outcome': 'REVIEW_FAIL', 'findings_count': 1}],
               'repair_claims': [_repair_claim(repair_record)]}
    return task_advance.decide(record, d6, pr, related)


def _registration(task_id, common_dir, *, allowed_states):
    if task_id != _EXECUTOR or common_dir != _COMMON:
        raise AssertionError('unexpected D6 registry read')
    return SimpleNamespace(task_id=task_id, root=_ROOT, common_dir=_COMMON,
                           branch=_BRANCH, result_sha=_HEAD, state='REPAIR')


def _snapshot(head=_HEAD, clean=True, root=_ROOT, common_dir=_COMMON, branch=_BRANCH):
    return task_completion.GitSnapshot(root, common_dir, branch, head, clean)


_OTHERS_ARGS = ('ls-files', '-z', '--others', '--directory')
_INDEX_ARGS = ('ls-files', '-z', '-v', '--cached')


class _D6Reads:
    """Inject the D6 registry read, the Git snapshot and the workspace reads."""

    def __init__(self, test, snapshot=None, *, snapshot_error=None, registry_error=None,
                 others=b'', index=b'H src.py\0H docs/notes.md\0', git_error=None,
                 nested=False):
        self.roots = []
        self.git_calls = []
        self.walked = []

        def read_registration(task_id, common_dir, *, allowed_states):
            if registry_error is not None:
                raise registry_error
            return _registration(task_id, common_dir, allowed_states=allowed_states)

        def collect(root):
            self.roots.append(root)
            if snapshot_error is not None:
                raise snapshot_error
            return snapshot

        def git(root, args, **_kwargs):
            self.git_calls.append((root, tuple(args)))
            if git_error is not None:
                raise git_error
            if tuple(args) == _OTHERS_ARGS:
                return others
            if tuple(args) == _INDEX_ARGS:
                return index
            raise AssertionError('unexpected Git read')

        def nested_git(root):
            self.walked.append(root)
            return nested

        for target, name, value in (
                (task_advance.task_completion, 'read_d6_registration', read_registration),
                (task_advance.task_completion, 'collect_git_snapshot', collect),
                (task_advance.task_completion, '_git', git),
                (task_advance, '_nested_git_entry', nested_git)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            test.addCleanup(patcher.stop)


class RepairStateStartFailureTests(unittest.TestCase):

    def test_status_failure_stays_unknown(self):
        self.assertEqual(task_advance.derive_repair_state(_record()), 'UNKNOWN')
        execution = _execution()
        del execution['native_session_id']
        self.assertEqual(task_advance.derive_repair_state(_record(execution=execution)),
                         'UNKNOWN')
        # Only the developer field is updated, so the top level may read PM_PENDING.
        self.assertEqual(task_advance.derive_repair_state(_record(top_state='PM_PENDING')),
                         'UNKNOWN')
        for execution in (_execution(native_session_id=_SESSION),
                          _execution(diff_sha256='d' * 64), None):
            with self.subTest(execution=execution):
                self.assertEqual(
                    task_advance.derive_repair_state(_record(execution=execution)), 'UNKNOWN')

    def test_status_failure_executor_only_without_session_or_diff(self):
        self.assertEqual(task_advance._claude_status_failed_executor(_record()), _EXECUTOR)
        execution = _execution()
        del execution['native_session_id']
        self.assertEqual(
            task_advance._claude_status_failed_executor(_record(execution=execution)),
            _EXECUTOR)
        no_executor = _record()
        del no_executor['developer']['executor_task_id']
        cases = (
            _record(execution=_execution(native_session_id=_SESSION)),
            _record(execution=_execution(diff_sha256='d' * 64)),
            # The failed process might still write to the D6.
            _record(execution=_execution(cleanup_status='UNCONFIRMED')),
            _record(execution=_execution(cleanup_status=None)),
            _record(error_code='CLAUDE_TIMEOUT'),
            _record(state='DEVELOPER_OUTCOME_UNKNOWN'),
            no_executor,
            None,
        )
        for record in cases:
            with self.subTest(record=record):
                self.assertIsNone(task_advance._claude_status_failed_executor(record))
        for execution in (None, 'BLOCKED', []):
            with self.subTest(execution=execution):
                record = _record()
                record['developer']['execution'] = execution
                self.assertIsNone(task_advance._claude_status_failed_executor(record))

    def test_other_states_and_codes_are_unchanged(self):
        cases = (
            (_record(state='DEVELOPER_DISPATCHING'), 'RUNNING'),
            (_record(error_code='CLAUDE_TIMEOUT'), 'UNKNOWN'),
            (_record(error_code='CLAUDE_TIMEOUT', top_state='PM_PENDING'), 'PENDING'),
            (_record(state='DEVELOPER_UNAVAILABLE'), 'UNAVAILABLE'),
            (_record(state='DEVELOPER_OUTCOME_UNKNOWN',
                     execution=_execution(status='UNKNOWN')), 'UNKNOWN'),
        )
        for record, expected in cases:
            with self.subTest(state=record['developer']['state'],
                              code=record['developer']['error_code']):
                self.assertEqual(task_advance.derive_repair_state(record), expected)

    def test_claude_no_changes_still_maps_to_blocked(self):
        for execution in (_execution(native_session_id=_SESSION, process_exit_code=0),
                          _execution(), None):
            with self.subTest(execution=execution is not None):
                record = _record(error_code='CLAUDE_NO_CHANGES')
                record['developer']['execution'] = execution
                self.assertEqual(task_advance.derive_repair_state(record), 'BLOCKED')


class ReviewRepairRetryTests(unittest.TestCase):

    def test_clean_d6_at_claim_head_retries(self):
        reads = _D6Reads(self, _snapshot())
        self.assertEqual(_decide(_record()), 'REPAIR_RETRY')
        self.assertEqual(reads.roots, [_ROOT])
        # The leftover read applies no exclude rule, so ignored files count.
        self.assertEqual(reads.git_calls, [(_ROOT, _OTHERS_ARGS), (_ROOT, _INDEX_ARGS)])
        self.assertEqual(reads.walked, [_ROOT])
        # The top level may still read PM_PENDING.
        self.assertEqual(_decide(_record(top_state='PM_PENDING')), 'REPAIR_RETRY')

    def test_ignored_untracked_or_empty_dir_leftover_stays_unknown(self):
        # A clean status snapshot, yet the failed attempt left a path behind.
        for others in (b'debug.log\0', b'tools/llmtool/__pycache__/\0',
                       b'scratch/\0', b'notes.txt\0debug.log\0'):
            with self.subTest(others=others):
                reads = _D6Reads(self, _snapshot(), others=others)
                self.assertEqual(_decide(_record()), 'REPAIR_OUTCOME_UNKNOWN')
                self.assertEqual(reads.git_calls, [(_ROOT, _OTHERS_ARGS)])

    def test_hidden_index_change_stays_unknown(self):
        # skip-worktree (S, s), assume-unchanged (h) or unmerged (M) entries
        # can hide a change from git status.
        for index in (b'H src.py\0S docs/notes.md\0', b'h src.py\0',
                      b's src.py\0', b'M src.py\0', b'Hsrc.py\0'):
            with self.subTest(index=index):
                _D6Reads(self, _snapshot(), index=index)
                self.assertEqual(_decide(_record()), 'REPAIR_OUTCOME_UNKNOWN')

    def test_nested_git_entry_stays_unknown(self):
        reads = _D6Reads(self, _snapshot(), nested=True)
        self.assertEqual(_decide(_record()), 'REPAIR_OUTCOME_UNKNOWN')
        self.assertEqual(reads.walked, [_ROOT])

    def test_unreadable_workspace_stays_unknown(self):
        reads = _D6Reads(self, _snapshot(), git_error=OSError('git read failed'))
        self.assertEqual(_decide(_record()), 'REPAIR_OUTCOME_UNKNOWN')
        self.assertEqual(reads.walked, [])
        with mock.patch.object(task_advance, '_repair_workspace_untouched',
                               side_effect=OSError('walk failed')):
            self.assertEqual(_decide(_record()), 'REPAIR_OUTCOME_UNKNOWN')

    def test_unconfirmed_cleanup_stays_unknown_without_d6_read(self):
        reads = _D6Reads(self, _snapshot())
        record = _record(execution=_execution(cleanup_status='UNCONFIRMED'))
        self.assertEqual(_decide(record), 'REPAIR_OUTCOME_UNKNOWN')
        self.assertEqual((reads.roots, reads.git_calls), ([], []))

    def test_dirty_d6_stays_unknown(self):
        reads = _D6Reads(self, _snapshot(clean=False))
        self.assertEqual(_decide(_record()), 'REPAIR_OUTCOME_UNKNOWN')
        self.assertEqual(reads.roots, [_ROOT])

    def test_moved_head_stays_unknown(self):
        _D6Reads(self, _snapshot(head=_MOVED))
        self.assertEqual(_decide(_record()), 'REPAIR_OUTCOME_UNKNOWN')

    def test_snapshot_binding_mismatch_stays_unknown(self):
        for snapshot in (_snapshot(root='/other/d6'), _snapshot(common_dir='/other/.git'),
                         _snapshot(branch='other-branch')):
            with self.subTest(snapshot=snapshot):
                _D6Reads(self, snapshot)
                self.assertEqual(_decide(_record()), 'REPAIR_OUTCOME_UNKNOWN')

    def test_unreadable_d6_stays_unknown(self):
        for kwargs in ({'snapshot_error': OSError('git read failed')},
                       {'registry_error': OSError('registry read failed')}):
            with self.subTest(kwargs=sorted(kwargs)):
                _D6Reads(self, None, **kwargs)
                self.assertEqual(_decide(_record()), 'REPAIR_OUTCOME_UNKNOWN')

    def test_native_session_or_diff_stays_unknown_without_d6_read(self):
        reads = _D6Reads(self, _snapshot())
        for execution in (_execution(native_session_id=_SESSION),
                          _execution(diff_sha256='d' * 64)):
            with self.subTest(execution=execution):
                self.assertEqual(_decide(_record(execution=execution)),
                                 'REPAIR_OUTCOME_UNKNOWN')
        self.assertEqual(reads.roots, [])

    def test_other_unknown_repairs_never_read_d6(self):
        reads = _D6Reads(self, _snapshot())
        record = _record(error_code='CLAUDE_TIMEOUT')
        self.assertEqual(_decide(record), 'REPAIR_OUTCOME_UNKNOWN')
        self.assertEqual(reads.roots, [])

    def test_d6_without_common_dir_stays_unknown(self):
        reads = _D6Reads(self, _snapshot())
        claim = _repair_claim(_record())
        self.assertFalse(task_advance._repair_d6_unchanged(claim, {'root': _ROOT}))
        self.assertFalse(task_advance._repair_d6_unchanged(dict(claim, head_sha='x'),
                                                           {'common_dir': _COMMON}))
        self.assertEqual(reads.roots, [])

    def test_remote_fail_path_is_unchanged(self):
        # The EC2 FAIL path does not read the D6, so it stays UNKNOWN.
        related = {'repair_claims': [_repair_claim(_record())]}
        self.assertEqual(task_advance._remote_fail_action(related, _HEAD),
                         'REPAIR_OUTCOME_UNKNOWN')


class NestedGitEntryTests(unittest.TestCase):
    """The real walk on a throwaway directory."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = os.path.realpath(temp.name)
        with open(os.path.join(self.root, '.git'), 'w') as stream:
            stream.write('gitdir: /registered/common/worktrees/d6\n')
        os.makedirs(os.path.join(self.root, 'pkg', 'sub'))
        for name in ('pkg/mod.py', 'pkg/sub/data.txt'):
            with open(os.path.join(self.root, name), 'w') as stream:
                stream.write('plain text\n')

    def test_root_git_link_alone_is_fine(self):
        self.assertFalse(task_advance._nested_git_entry(self.root))

    def test_nested_git_entry_is_found(self):
        for name in ('pkg/.git', 'pkg/sub/.GIT'):
            with self.subTest(name=name):
                path = os.path.join(self.root, name)
                with open(path, 'w') as stream:
                    stream.write('gitdir: /elsewhere\n')
                self.assertTrue(task_advance._nested_git_entry(self.root))
                os.remove(path)
        os.mkdir(os.path.join(self.root, 'pkg', '.git'))
        self.assertTrue(task_advance._nested_git_entry(self.root))

    def test_walk_limit_and_missing_root_fail_closed(self):
        with mock.patch.object(task_advance, '_REPAIR_WALK_LIMIT', 2):
            self.assertTrue(task_advance._nested_git_entry(self.root))
        with self.assertRaises(OSError):
            task_advance._nested_git_entry(os.path.join(self.root, 'missing'))


_GIT = '/usr/bin/git'


@unittest.skipUnless(os.access(_GIT, os.X_OK), 'needs /usr/bin/git')
class RepairWorkspaceUntouchedTests(unittest.TestCase):
    """The real Git reads on a throwaway repository: status alone misses these."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = os.path.realpath(temp.name)
        self.root = os.path.join(self.home, 'repo')
        os.makedirs(os.path.join(self.root, 'pkg'))
        self._write('src.py', 'value = 1\n')
        self._write('pkg/mod.py', 'other = 2\n')
        self._write('.gitignore', '*.log\n')
        self._setup_git('init', '-q')
        self._setup_git('add', '--', 'src.py', 'pkg/mod.py', '.gitignore')
        self._setup_git('commit', '-q', '-m', 'base')

    def _write(self, name, text):
        with open(os.path.join(self.root, name), 'w') as stream:
            stream.write(text)

    def _setup_git(self, *args):
        env = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'HOME': self.home,
               'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
               'GIT_TERMINAL_PROMPT': '0'}
        subprocess.run([_GIT, '-c', 'user.name=Repair Test',
                        '-c', 'user.email=repair-test@example.invalid',
                        '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null',
                        *args], cwd=self.root, env=env, check=True,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)

    def _assert_status_clean_but_touched(self):
        self.assertIs(task_completion.collect_git_snapshot(self.root).clean, True)
        self.assertIs(task_advance._repair_workspace_untouched(self.root), False)

    def test_tracked_files_only_is_untouched(self):
        self.assertIs(task_completion.collect_git_snapshot(self.root).clean, True)
        self.assertIs(task_advance._repair_workspace_untouched(self.root), True)

    def test_git_ignored_file_is_a_leftover(self):
        self._write('debug.log', 'left by the failed attempt\n')
        self._assert_status_clean_but_touched()

    def test_git_ignored_file_in_tracked_dir_is_a_leftover(self):
        self._write('pkg/trace.log', 'left by the failed attempt\n')
        self._assert_status_clean_but_touched()

    def test_empty_directory_is_a_leftover(self):
        os.mkdir(os.path.join(self.root, 'scratch'))
        self._assert_status_clean_but_touched()

    def test_skip_worktree_change_is_a_leftover(self):
        self._setup_git('update-index', '--skip-worktree', 'src.py')
        self._write('src.py', 'value = 2\n')
        self._assert_status_clean_but_touched()

    def test_assume_unchanged_change_is_a_leftover(self):
        self._setup_git('update-index', '--assume-unchanged', 'src.py')
        self._write('src.py', 'value = 3\n')
        self._assert_status_clean_but_touched()

    def test_nested_git_file_is_a_leftover(self):
        self._write('pkg/.git', 'gitdir: /elsewhere\n')
        try:
            untouched = task_advance._repair_workspace_untouched(self.root)
        except Exception:
            untouched = False  # A failed read keeps it UNKNOWN as well.
        self.assertIs(untouched, False)

    def test_untracked_file_is_a_leftover(self):
        self._write('notes.txt', 'left by the failed attempt\n')
        self.assertIs(task_completion.collect_git_snapshot(self.root).clean, False)
        self.assertIs(task_advance._repair_workspace_untouched(self.root), False)


class _Store:
    def __init__(self, records):
        self.records = records

    def get(self, task_id):
        return self.records[task_id]


class _RepairRefused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.review_repair_code = code


class AdvanceRepairRetryTests(unittest.TestCase):
    """One real _advance_step with the D6, GitHub and repair dispatch injected."""

    def setUp(self):
        d6 = {'root': _ROOT, 'common_dir': _COMMON, 'branch': _BRANCH,
              'task_id': _EXECUTOR, 'result_head': _HEAD, 'recorded_result_head': _HEAD,
              'registered_result_head': _HEAD, 'dirty': False, 'state': 'CLEAN',
              'registration_state': 'REPAIR'}
        for name, kwargs in (
                ('external_action_refusal', {'return_value': None}),
                ('_closed_train_member_head', {'return_value': None}),
                ('_d6_snapshot', {'return_value': d6}),
                ('_review_outcomes', {'return_value': []}),
                ('_merged_review_target', {'return_value': None}),
                ('_remote_test_status', {'return_value': ('PASS', None, None)}),
                ('_ack_consumed_events', {'side_effect': lambda result, *_a, **_k: result})):
            patcher = mock.patch.object(task_advance, name, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(task_advance.shutil, 'which',
                                    return_value='/usr/local/bin/claude')
        patcher.start()
        self.addCleanup(patcher.stop)

    def _advance(self, repair_record, repair_call):
        record = {
            'task_id': _TASK,
            'developer': {'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'claude'},
            'completion': {'executor_task_id': _EXECUTOR},
            'review_dispatch_claim': [{'pr': 7, 'head_sha': _HEAD, 'state': 'COMPLETED',
                                       'version': 1}],
            'completion_events': [{'source': 'REVIEWER', 'source_sha': _HEAD, 'version': 1,
                                   'outcome': 'REVIEW_FAIL', 'findings_count': 1}],
            'review_repair_claims': [{'head_sha': _HEAD, 'state': 'DISPATCHED',
                                      'repair_task_id': _REPAIR_TASK}],
        }
        store = _Store({_TASK: record, _REPAIR_TASK: repair_record})

        def github_get(path):
            if path == 'pulls/7':
                return {'number': 7, 'head': {'sha': _HEAD}}
            raise AssertionError('unexpected GitHub read')

        review_call = mock.Mock(side_effect=AssertionError('no review dispatch'))
        return task_advance._advance_step(
            task_id=_TASK, store=store, guard=lambda: None, pr_number=7,
            dispatch_review_call=review_call, repair_review_call=repair_call,
            github_get=github_get, publish_git_runner=None, publish_gh_runner=None,
            merge_gh_runner=None)

    def test_retry_dispatches_a_counted_auto_repair(self):
        _D6Reads(self, _snapshot())
        repair_call = mock.Mock(return_value={'state': 'DISPATCHED'})
        result = self._advance(_record(), repair_call)
        self.assertEqual(result['step'], 'REPAIR_RETRY')
        self.assertEqual(result['codes'], [])
        repair_call.assert_called_once()
        kwargs = repair_call.call_args.kwargs
        # auto=True: review_repair applies its chain-wide limit; the old claim
        # id makes it a retry of that claim, not a fresh repair chain.
        self.assertEqual((kwargs['retry_claim_id'], kwargs['auto'], kwargs['executor_task'],
                          kwargs['task_id'], kwargs['pr_number']),
                         (_REPAIR_TASK, True, _EXECUTOR, _TASK, 7))

    def test_repair_cap_still_stops_the_retry(self):
        _D6Reads(self, _snapshot())
        for code in ('REPAIR_LIMIT_REACHED', 'REVIEW_REPAIR_PM_REQUIRED'):
            with self.subTest(code=code):
                repair_call = mock.Mock(side_effect=_RepairRefused(code))
                result = self._advance(_record(), repair_call)
                repair_call.assert_called_once()
                if code == 'REVIEW_REPAIR_PM_REQUIRED':
                    self.assertEqual((result['step'], result['codes']), ('STOPPED', [code]))
                else:
                    self.assertEqual((result['step'], result['codes']),
                                     ('REPAIR_RETRY', [code]))

    def test_git_ignored_leftover_never_dispatches(self):
        _D6Reads(self, _snapshot(), others=b'debug.log\0')
        repair_call = mock.Mock(side_effect=AssertionError('no repair dispatch'))
        result = self._advance(_record(), repair_call)
        self.assertEqual((result['step'], result['codes']),
                         ('REPAIR_OUTCOME_UNKNOWN', ['REPAIR_OUTCOME_UNKNOWN']))
        repair_call.assert_not_called()

    def test_unreadable_d6_never_dispatches(self):
        for kwargs in ({'registry_error': OSError('registry read failed')},
                       {'snapshot_error': OSError('git read failed')},
                       {'git_error': OSError('git read failed')}):
            with self.subTest(kwargs=sorted(kwargs)):
                _D6Reads(self, _snapshot(), **kwargs)
                repair_call = mock.Mock(side_effect=AssertionError('no repair dispatch'))
                result = self._advance(_record(), repair_call)
                self.assertEqual(result['step'], 'REPAIR_OUTCOME_UNKNOWN')
                repair_call.assert_not_called()

    def test_other_repair_outcomes_keep_their_step(self):
        _D6Reads(self, _snapshot())
        cases = ((_record(error_code='CLAUDE_TIMEOUT'), 'REPAIR_OUTCOME_UNKNOWN'),
                 (_record(state='DEVELOPER_DISPATCHING'), 'WAITING_REPAIR'))
        for repair_record, step in cases:
            with self.subTest(step=step):
                repair_call = mock.Mock(side_effect=AssertionError('no repair dispatch'))
                result = self._advance(repair_record, repair_call)
                self.assertEqual(result['step'], step)
                repair_call.assert_not_called()


if __name__ == '__main__':
    unittest.main()
