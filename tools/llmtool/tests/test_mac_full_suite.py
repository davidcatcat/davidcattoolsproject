"""Tests for the full Mac CI request after a merge (mac_full_suite).

Every GitHub read and gh command is a fake; nothing reaches the network.
"""

import copy
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import mac_full_suite, merge_train, merge_train_ops, merge_train_store  # noqa: E402
from llmtool import task_advance, task_completion  # noqa: E402

# Reused as a module for its fakes and helpers; its TestCase class is never
# imported by name, so its tests are not collected again here.
import test_merge_train_ops as ops_fixture  # noqa: E402

DISPATCH = ['workflow', 'run', 'llmtool-mac-ci.yml', '--ref', 'main']
HEAD = 'a' * 40
MAIN_SHA = 'e' * 40
LINUX = 'check (Linux 3.14)'
MACOS = 'check (macOS 3.13)'

DISPATCHED = mac_full_suite.MAC_FULL_SUITE_DISPATCHED
ALREADY_QUEUED = mac_full_suite.MAC_FULL_SUITE_ALREADY_QUEUED
DISPATCH_FAILED = mac_full_suite.MAC_FULL_SUITE_DISPATCH_FAILED
STATE_UNAVAILABLE = mac_full_suite.MAC_FULL_SUITE_STATE_UNAVAILABLE


def _run(status, event='workflow_dispatch', branch='main'):
    return {'id': 1, 'status': status, 'event': event, 'head_branch': branch}


def _runs(*runs, total=None):
    return {'total_count': len(runs) if total is None else total,
            'workflow_runs': [dict(run) for run in runs]}


class FakeReads:
    """Serves one fixed answer for the run list and records each path read."""

    def __init__(self, log, answer):
        self.log, self.answer = log, answer

    def __call__(self, path):
        self.log.append(('read', path))
        if isinstance(self.answer, Exception):
            raise self.answer
        return copy.deepcopy(self.answer)


class FakeRunner:
    """Records each gh command; returns a fixed exit code or raises."""

    def __init__(self, log, returncode=0, error=None):
        self.log, self.returncode, self.error = log, returncode, error

    def __call__(self, root, args):
        self.log.append(('gh', root, list(args)))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(returncode=self.returncode, stdout='', stderr='')


class RequestFullSuiteTests(unittest.TestCase):

    def _request(self, answer, *, returncode=0, error=None):
        log = []
        code = mac_full_suite.request_full_suite(
            gh_runner=FakeRunner(log, returncode, error), root='/repo',
            github_get=FakeReads(log, answer))
        return code, log

    def _gh_calls(self, log):
        return [entry[2] for entry in log if entry[0] == 'gh']

    def test_reads_main_dispatch_runs_of_the_mac_workflow(self):
        self.assertEqual(
            mac_full_suite.RUNS_PATH,
            'actions/workflows/llmtool-mac-ci.yml/runs'
            '?branch=main&event=workflow_dispatch&per_page=30')
        # The path passes the existing GitHub read path's own check.
        self.assertIsNotNone(
            task_completion._GITHUB_API_PATH.fullmatch(mac_full_suite.RUNS_PATH))

    def test_dispatches_when_no_run_waits(self):
        for answer in (_runs(), _runs(_run('completed'), _run('completed'))):
            with self.subTest(answer=answer):
                code, log = self._request(answer)
                self.assertEqual(code, DISPATCHED)
                # One read first, then exactly one dispatch in the repository root.
                self.assertEqual(log, [('read', mac_full_suite.RUNS_PATH),
                                       ('gh', '/repo', DISPATCH)])

    def test_skips_when_a_run_waits_to_start(self):
        for status in ('queued', 'waiting', 'pending', 'requested'):
            with self.subTest(status=status):
                code, log = self._request(_runs(_run('in_progress'), _run(status),
                                                _run('completed')))
                self.assertEqual(code, ALREADY_QUEUED)
                self.assertEqual(self._gh_calls(log), [])

    def test_dispatches_when_a_run_is_only_in_progress(self):
        code, log = self._request(_runs(_run('in_progress'), _run('completed')))
        self.assertEqual(code, DISPATCHED)
        self.assertEqual(self._gh_calls(log), [DISPATCH])

    def test_failed_dispatch_is_reported_and_never_retried(self):
        cases = {'exit 1': {'returncode': 1}, 'exit 2': {'returncode': 2},
                 'runner error': {'error': OSError('gh missing')}}
        for name, kwargs in cases.items():
            with self.subTest(name):
                code, log = self._request(_runs(), **kwargs)
                self.assertEqual(code, DISPATCH_FAILED)
                self.assertEqual(self._gh_calls(log), [DISPATCH])

    def test_unreadable_or_malformed_list_never_dispatches(self):
        cases = {
            'read error': LookupError('no answer'),
            'not a dict': [],
            'none': None,
            'no run list': {'total_count': 0},
            'no total': {'workflow_runs': []},
            'total below list': _runs(_run('completed'), total=0),
            'run not a dict': {'total_count': 1, 'workflow_runs': ['queued']},
            'unknown status': _runs(_run('done')),
            'missing status': _runs({'event': 'workflow_dispatch', 'head_branch': 'main'}),
            'other event': _runs(_run('completed', event='pull_request')),
            'other branch': _runs(_run('completed', branch='feature')),
            'too many runs': _runs(*[_run('completed')] * 31),
        }
        for name, answer in cases.items():
            with self.subTest(name):
                code, log = self._request(answer)
                self.assertEqual(code, STATE_UNAVAILABLE)
                self.assertEqual(self._gh_calls(log), [])

    def test_missing_runner_or_read_is_skipped(self):
        log = []
        for runner, reads in ((None, FakeReads(log, _runs())),
                              (FakeRunner(log), None), (None, None)):
            with self.subTest(runner=runner, reads=reads):
                self.assertEqual(mac_full_suite.request_full_suite(
                    gh_runner=runner, root='/repo', github_get=reads), STATE_UNAVAILABLE)
        self.assertEqual(log, [])

    def test_never_raises(self):
        class BadResult:
            @property
            def returncode(self):
                raise RuntimeError('broken result')

        code = mac_full_suite.request_full_suite(
            gh_runner=lambda _root, _args: BadResult(), root='/repo',
            github_get=lambda _path: _runs())
        self.assertIn(code, (DISPATCH_FAILED, STATE_UNAVAILABLE))


class AdvanceFakes:
    """Fake GitHub reads and gh commands for one task_advance merge of PR 17."""

    def __init__(self, *, linux='success', merge_returncode=0, merge_effect=True,
                 dispatch_returncode=0, runs=None):
        self.linux, self.runs = linux, _runs() if runs is None else runs
        self.merge_returncode, self.merge_effect = merge_returncode, merge_effect
        self.dispatch_returncode = dispatch_returncode
        self.merged = False
        self.reads, self.calls, self.updates = [], [], []

    def pr(self):
        return {'number': 17, 'head': {'sha': HEAD}, 'labels': [],
                'state': 'closed' if self.merged else 'open', 'merged': self.merged,
                'mergeable': None if self.merged else True}

    def github_get(self, path):
        self.reads.append(path)
        if path == mac_full_suite.RUNS_PATH:
            if isinstance(self.runs, Exception):
                raise self.runs
            return copy.deepcopy(self.runs)
        if path == f'commits/{HEAD}/check-runs?per_page=100':
            return {'total_count': 2, 'check_runs': [
                {'name': LINUX, 'head_sha': HEAD, 'status': 'completed',
                 'conclusion': self.linux},
                {'name': MACOS, 'head_sha': HEAD, 'status': 'completed',
                 'conclusion': 'success'}]}
        if path == 'pulls/17/files?per_page=100':
            return [{'filename': 'tools/llmtool/llmtool/example.py'}]
        if path == 'pulls/17':
            return self.pr()
        if path == 'branches/main':
            return {'commit': {'sha': MAIN_SHA}}
        if path == f'compare/{MAIN_SHA}...{HEAD}':
            return {'status': 'ahead'}
        raise LookupError(path)

    def gh(self, _root, args):
        self.calls.append(list(args))
        if args[:2] == ['pr', 'merge']:
            if self.merge_effect:
                self.merged = True
            return SimpleNamespace(returncode=self.merge_returncode, stdout='', stderr='')
        if args[:2] == ['workflow', 'run']:
            return SimpleNamespace(returncode=self.dispatch_returncode, stdout='', stderr='')
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    def dispatches(self):
        return [call for call in self.calls if call[:2] == ['workflow', 'run']]

    def merge_once(self):
        fakes = self

        class Store:
            def compare_and_update(self, *args, **kwargs):
                fakes.updates.append(kwargs)

        review = {'pr': 17, 'head_sha': HEAD, 'version': 1, 'state': 'COMPLETED',
                  'review_result': {'verdict': 'PASS'}}
        event = {'source': 'REVIEWER', 'source_sha': HEAD, 'version': 1,
                 'outcome': 'REVIEW_PASS'}
        record = {'task_id': '1' * 32, 'record_sha256': 'f' * 64,
                  'review_dispatch_claim': [review]}
        return task_advance._merge_once(
            store=Store(), record=record, number=17, head=HEAD, pr=self.pr(),
            events=[event], github_get=self.github_get, gh_runner=self.gh, root='/repo')


class AdvanceMergeRequestTests(unittest.TestCase):

    def _merge(self, **kwargs):
        fakes = AdvanceFakes(**kwargs)
        with mock.patch.object(mac_full_suite, 'request_full_suite',
                               wraps=mac_full_suite.request_full_suite) as spy:
            result = fakes.merge_once()
        return result, fakes, spy

    def test_merge_success_requests_once_after_the_provenance_comment(self):
        result, fakes, spy = self._merge()
        self.assertEqual(result['step'], 'MERGED', result)
        self.assertEqual(result['codes'], [])
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(spy.call_args.kwargs['root'], '/repo')
        self.assertEqual(fakes.dispatches(), [DISPATCH])
        verbs = [call[:2] for call in fakes.calls]
        self.assertEqual(verbs, [['pr', 'merge'], ['pr', 'comment'], ['workflow', 'run']])
        self.assertEqual(fakes.updates[-1]['merge_attempt']['state'], 'MERGED')

    def test_ambiguous_merge_resolved_as_merged_requests_once(self):
        result, fakes, spy = self._merge(merge_returncode=1)
        self.assertEqual(result['step'], 'MERGED', result)
        self.assertEqual(result['codes'], [])
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(fakes.dispatches(), [DISPATCH])

    def test_waiting_run_adds_no_code_and_sends_no_dispatch(self):
        result, fakes, spy = self._merge(runs=_runs(_run('queued')))
        self.assertEqual(result['step'], 'MERGED', result)
        self.assertEqual(result['codes'], [])
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(fakes.dispatches(), [])

    def test_failed_or_unavailable_request_is_a_warning_and_stays_merged(self):
        cases = {DISPATCH_FAILED: {'dispatch_returncode': 1},
                 STATE_UNAVAILABLE: {'runs': LookupError('no answer')}}
        for code, kwargs in cases.items():
            with self.subTest(code):
                result, fakes, spy = self._merge(**kwargs)
                self.assertEqual(result['step'], 'MERGED', result)
                self.assertEqual(result['codes'], [code])
                self.assertEqual(spy.call_count, 1)
                self.assertEqual(fakes.updates[-1]['merge_attempt']['state'], 'MERGED')

    def test_refused_merge_never_requests(self):
        result, fakes, spy = self._merge(linux='failure')
        self.assertEqual(result['step'], 'MERGE_REFUSED', result)
        spy.assert_not_called()
        self.assertEqual(fakes.calls, [])
        self.assertNotIn(mac_full_suite.RUNS_PATH, fakes.reads)

    def test_unknown_merge_never_requests(self):
        result, fakes, spy = self._merge(merge_returncode=1, merge_effect=False)
        self.assertEqual(result['step'], 'MERGE_REFUSED', result)
        self.assertEqual(result['codes'], ['MERGE_OUTCOME_UNKNOWN'])
        spy.assert_not_called()
        self.assertEqual(fakes.dispatches(), [])
        self.assertNotIn(mac_full_suite.RUNS_PATH, fakes.reads)


class TrainGitHub(ops_fixture.FakeGitHub):
    """The merge-train fake reads plus one fixed Mac CI run list."""

    def __init__(self):
        super().__init__()
        self.runs = _runs()

    def __call__(self, path):
        if path == mac_full_suite.RUNS_PATH:
            self.reads.append(path)
            if isinstance(self.runs, Exception):
                raise self.runs
            return copy.deepcopy(self.runs)
        return super().__call__(path)


class TrainGh(ops_fixture.FakeGh):
    """The merge-train fake gh plus a fixed exit code for workflow run."""

    def __init__(self, github):
        super().__init__(github)
        self.dispatch_returncode = 0

    def __call__(self, root, args):
        if list(args[:2]) == ['workflow', 'run']:
            self.calls.append(list(args))
            return SimpleNamespace(returncode=self.dispatch_returncode, stdout='', stderr='')
        return super().__call__(root, args)

    def dispatches(self):
        return [call for call in self.calls if call[:2] == ['workflow', 'run']]


class TrainMergeRequestTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if shutil.which('git') is None:
            raise unittest.SkipTest('git is required')
        cls._repo_dir = tempfile.TemporaryDirectory(prefix='mac-full-suite-')
        root = Path(cls._repo_dir.name) / 'repo'
        root.mkdir()
        git = ops_fixture._git
        git(root, 'init', '-q')
        git(root, 'symbolic-ref', 'HEAD', 'refs/heads/main')
        (root / 'base.txt').write_text('base\n', encoding='utf-8')
        git(root, 'add', 'base.txt')
        git(root, 'commit', '-q', '-m', 'base')
        main = git(root, 'rev-parse', 'HEAD')
        heads = []
        for name in ('a', 'b'):
            git(root, 'checkout', '-q', '-b', 'member-' + name, main)
            (root / (name + '.txt')).write_text(name + '\n', encoding='utf-8')
            git(root, 'add', name + '.txt')
            git(root, 'commit', '-q', '-m', 'member ' + name)
            heads.append(git(root, 'rev-parse', 'HEAD'))
        git(root, 'checkout', '-q', 'main')
        built = merge_train.build_candidate(
            str(root), main, [(ops_fixture.TASK_A, heads[0]), (ops_fixture.TASK_B, heads[1])])
        if built['head'] is None:
            raise AssertionError(built)
        cls.root, cls.main = str(root), main
        cls.head_a, cls.head_b = heads
        cls.train_head = built['head']

    @classmethod
    def tearDownClass(cls):
        cls._repo_dir.cleanup()

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='mac-full-suite-root-')
        self.addCleanup(directory.cleanup)
        self.train_root = str(Path(directory.name) / 'tasks')
        self.github = TrainGitHub()
        self.gh = TrainGh(self.github)
        task_a, task_b = ops_fixture.TASK_A, ops_fixture.TASK_B
        pr_a, pr_b, train_pr = ops_fixture.PR_A, ops_fixture.PR_B, ops_fixture.TRAIN_PR
        self.store = ops_fixture.FakeStore({
            task_a: ops_fixture._task(task_a, pr_a, self.head_a),
            task_b: ops_fixture._task(task_b, pr_b, self.head_b)})
        self.github.pulls[pr_a] = ops_fixture._pull(pr_a, self.head_a)
        self.github.pulls[pr_b] = ops_fixture._pull(pr_b, self.head_b)
        self.github.pulls[train_pr] = ops_fixture._pull(train_pr, self.train_head)
        self.github.files[train_pr] = [{'filename': 'tools/llmtool/llmtool/example.py'}]
        self.github.checks[self.train_head] = [ops_fixture._run(LINUX, self.train_head),
                                               ops_fixture._run(MACOS, self.train_head)]
        patcher = mock.patch.object(task_completion, 'read_authenticated_main_sha',
                                    return_value=self.main)
        self.read_main = patcher.start()
        self.addCleanup(patcher.stop)
        spy = mock.patch.object(mac_full_suite, 'request_full_suite',
                                wraps=mac_full_suite.request_full_suite)
        self.spy = spy.start()
        self.addCleanup(spy.stop)
        merge_train_store.create({
            'schema_version': 1, 'train_id': ops_fixture.TRAIN_ID, 'main_sha': self.main,
            'members': [ops_fixture._member(task_a, pr_a, self.head_a),
                        ops_fixture._member(task_b, pr_b, self.head_b)],
            'excluded': [], 'train_branch': 'llmtool/train-' + ops_fixture.TRAIN_ID,
            'train_head': self.train_head, 'train_pr': train_pr, 'state': 'BUILT',
            'merge_attempt': None, 'merge_sha': None,
            'created_at': '2026-11-08T00:00:00Z'}, root=self.train_root)

    def _merge(self):
        return merge_train_ops.merge_train(
            ops_fixture.TRAIN_ID, store=self.store, github_get=self.github,
            gh_runner=self.gh, root=self.root, train_root=self.train_root)

    def _record(self):
        return merge_train_store.get(ops_fixture.TRAIN_ID, root=self.train_root)

    def _assert_merged_record(self):
        record = self._record()
        self.assertEqual(record['state'], 'MERGED')
        self.assertEqual(record['merge_sha'], ops_fixture.MERGE_SHA)
        self.assertEqual(record['merge_attempt']['state'], 'MERGED')
        return record

    def test_train_merged_requests_once_and_close_requests_none(self):
        result = self._merge()
        self.assertEqual(result['step'], 'MERGED', result)
        self.assertEqual(result['codes'], [])
        self.assertEqual(self.spy.call_count, 1)
        self.assertEqual(self.spy.call_args.kwargs['root'], self.root)
        self.assertEqual(self.gh.dispatches(), [DISPATCH])
        verbs = [call[:2] for call in self.gh.calls]
        self.assertEqual(verbs, [['pr', 'merge'], ['pr', 'comment'], ['workflow', 'run']])
        merged = self._assert_merged_record()
        # Closing the train requests no other run.
        result = merge_train_ops.close_train(
            ops_fixture.TRAIN_ID, store=self.store, train_root=self.train_root,
            advance_call=lambda **_kwargs: {'step': 'CLOSED', 'codes': []})
        self.assertEqual(result['step'], 'CLOSED', result)
        self.assertEqual(self.spy.call_count, 1)
        self.assertEqual(self.gh.dispatches(), [DISPATCH])
        self.assertEqual(self._record()['merge_sha'], merged['merge_sha'])

    def test_dispatch_failure_keeps_the_train_merged(self):
        self.gh.dispatch_returncode = 1
        result = self._merge()
        self.assertEqual(result['step'], 'MERGED', result)
        self.assertEqual(result['codes'], [DISPATCH_FAILED])
        self.assertEqual(self.spy.call_count, 1)
        self.assertEqual(self.gh.dispatches(), [DISPATCH])
        self._assert_merged_record()
        # A merged train is not merged or requested again.
        result = self._merge()
        self.assertEqual(result['codes'], [merge_train_ops.TRAIN_NOT_MERGEABLE])
        self.assertEqual(self.spy.call_count, 1)

    def test_waiting_run_adds_no_code(self):
        self.github.runs = _runs(_run('pending'))
        result = self._merge()
        self.assertEqual(result['step'], 'MERGED', result)
        self.assertEqual(result['codes'], [])
        self.assertEqual(self.gh.dispatches(), [])
        self._assert_merged_record()

    def test_unavailable_run_list_is_reported_and_stays_merged(self):
        self.github.runs = LookupError('no answer')
        result = self._merge()
        self.assertEqual(result['step'], 'MERGED', result)
        self.assertEqual(result['codes'], [STATE_UNAVAILABLE])
        self.assertEqual(self.gh.dispatches(), [])
        self._assert_merged_record()

    def test_refused_train_never_requests(self):
        self.github.checks[self.train_head] = [
            ops_fixture._run(LINUX, self.train_head, 'failure'),
            ops_fixture._run(MACOS, self.train_head)]
        result = self._merge()
        self.assertEqual(result['step'], 'MERGE_REFUSED', result)
        self.spy.assert_not_called()
        self.assertEqual(self.gh.calls, [])
        self.assertEqual(self._record()['state'], 'BUILT')

    def test_unknown_train_merge_never_requests(self):
        self.gh.merge_effect = False
        result = self._merge()
        self.assertEqual(result['codes'], [merge_train_ops.TRAIN_MERGE_OUTCOME_UNKNOWN])
        self.spy.assert_not_called()
        self.assertEqual(self.gh.dispatches(), [])


if __name__ == '__main__':
    unittest.main()
