"""Tests for merge_train_build.build_train (#1182 T3b1).

The candidate is built in a real temporary Git repository and the train record
is written to a real temporary train store. The TaskStore is in memory, and
GitHub, push and gh are fakes; nothing leaves the machine.
"""

import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import (merge_train, merge_train_build, merge_train_eligibility,  # noqa: E402
                     merge_train_store, pm_caller, task_completion)
from llmtool.errors import LLMToolError  # noqa: E402

GIT = '/usr/bin/git' if os.path.exists('/usr/bin/git') else shutil.which('git')

CALLER = 'pm-session-owner'
OTHER = 'pm-session-other'
NOW = datetime(2026, 11, 8, 12, 34, 56, tzinfo=timezone.utc)
TRAIN_ID = re.compile(r't20261108123456-[0-9a-f]{4}')
PR_NUMBER = 77

TASK_A = 'a' * 32
TASK_B = 'b' * 32
TASK_C = 'c' * 32
TASK_X = 'e' * 32
PRS = {TASK_A: 101, TASK_B: 102, TASK_C: 103, TASK_X: 104}

# Default for _build's members: build from task_ids. Never a valid members value.
_DEFAULT_MEMBERS = object()


def _executor_task():
    """Return a D6 executor id that the train store accepts."""
    for candidate in ('train-t3b1-1182-20261108', 'issue-1182-train', 'task-1182',
                      'task1182', 'a' * 32, 'T1182', 'a'):
        if merge_train_store._EXECUTOR_TASK.fullmatch(candidate) is not None:
            return candidate
    raise AssertionError('no fixture executor id matches the D6 task id pattern')


EXECUTOR = _executor_task()


def _eligible_member(task_id, pr, head):
    return {
        'task_id': task_id, 'pr': pr, 'head': head, 'executor_task': EXECUTOR,
        'review_version': 1, 'linux_check_run_id': 1000 + pr,
        'macos_check_run_id': 2000 + pr, 'remote_test_receipt_digest': 'ab' * 32,
    }


class _Repo:
    """A real Git repository with one base commit and member commits on it."""

    def __init__(self, path, home):
        self.path = str(path)
        self._env = {
            'PATH': '/usr/bin:/bin', 'HOME': str(home), 'LC_ALL': 'C',
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
            'GIT_AUTHOR_NAME': 'fixture', 'GIT_AUTHOR_EMAIL': 'fixture@invalid',
            'GIT_COMMITTER_NAME': 'fixture', 'GIT_COMMITTER_EMAIL': 'fixture@invalid',
        }
        self._run('init', '-q')
        self.base = self.commit_on(None, 'base.txt', 'base\n')

    def _run(self, *args):
        result = subprocess.run(
            [GIT, '-C', self.path, '-c', 'commit.gpgsign=false',
             '-c', 'core.hooksPath=/dev/null', *args],
            env=self._env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            check=True)
        return result.stdout.strip()

    def commit_on(self, parent, name, text):
        if parent is not None:
            self._run('checkout', '-q', '--detach', parent)
        (Path(self.path) / name).write_text(text, encoding='utf-8')
        self._run('add', '--', name)
        self._run('commit', '-q', '-m', 'fixture ' + name)
        return self._run('rev-parse', 'HEAD')


class _Store:
    """In-memory TaskStore: ``get`` returns a copy and logs every read."""

    def __init__(self, records):
        self.records = records
        self.reads = []

    def get(self, task_id):
        self.reads.append(task_id)
        if task_id not in self.records:
            raise KeyError(task_id)
        return copy.deepcopy(self.records[task_id])


class _Runners:
    """Fake git and gh runners that log every call."""

    def __init__(self, push_code=0, create_code=0, listing=None):
        self.push_code = push_code
        self.create_code = create_code
        self.listing = listing
        self.git_calls = []
        self.gh_calls = []
        self.pushed_head = None

    def git(self, root, args):
        self.git_calls.append((root, list(args)))
        if list(args[:2]) == ['push', 'origin'] and self.push_code == 0:
            self.pushed_head = args[2].split(':', 1)[0]
        return types.SimpleNamespace(returncode=self.push_code, stdout='', stderr='')

    def gh(self, root, args):
        self.gh_calls.append((root, list(args)))
        if list(args[:2]) == ['pr', 'create']:
            return types.SimpleNamespace(
                returncode=self.create_code,
                stdout='https://github.com/example/repo/pull/77\nCOMMAND-OUTPUT-MARKER\n',
                stderr='')
        if list(args[:2]) == ['pr', 'list']:
            if self.listing is not None:
                pulls = self.listing(self)
            else:
                pulls = [{'number': PR_NUMBER, 'headRefOid': self.pushed_head,
                          'title': 'LISTING-OUTPUT-MARKER'}]
            return types.SimpleNamespace(returncode=0, stdout=json.dumps(pulls), stderr='')
        return types.SimpleNamespace(returncode=1, stdout='', stderr='')


@unittest.skipIf(GIT is None or os.name != 'posix', 'needs Git on a POSIX system')
class BuildTrainTests(unittest.TestCase):

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='llmtool-train-build-test-')
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve()
        (base / 'repo').mkdir()
        (base / 'home').mkdir()
        self.repo = _Repo(base / 'repo', base / 'home')
        self.train_root = str(base / 'state' / 'tasks')
        self.heads = {
            TASK_A: self.repo.commit_on(self.repo.base, 'shared.txt', 'a\n'),
            TASK_B: self.repo.commit_on(self.repo.base, 'b.txt', 'b\n'),
            TASK_C: self.repo.commit_on(self.repo.base, 'c.txt', 'c\n'),
            # Conflicts with TASK_A on shared.txt.
            TASK_X: self.repo.commit_on(self.repo.base, 'shared.txt', 'x\n'),
        }
        self.store = _Store({
            task_id: {'task_id': task_id, 'pm_session_id': CALLER,
                      'completion': {'state': 'PM_EVIDENCE_READY',
                                     'executor_task_id': EXECUTOR}}
            for task_id in self.heads})
        self.ineligible = {}
        self.real_check = set()
        self.check_calls = []
        self.github_calls = []
        self._real_member_check = merge_train_eligibility.member_check

    def _github_get(self, path):
        self.github_calls.append(path)
        raise AssertionError('the fake member check makes no GitHub read')

    def _fake_check(self, store, task_id, pr, github_get, train_root=None):
        self.check_calls.append((task_id, pr, github_get, train_root))
        self.assertIs(store, self.store)
        self.assertEqual(github_get, self._github_get)
        self.assertEqual(train_root, self.train_root)
        if task_id in self.real_check:
            return self._real_member_check(store, task_id, pr, github_get, train_root)
        if task_id in self.ineligible:
            return {'eligible': False, 'code': self.ineligible[task_id], 'member': None}
        return {'eligible': True, 'code': None,
                'member': _eligible_member(task_id, pr, self.heads[task_id])}

    def _build(self, task_ids, runners=None, main=None, members=_DEFAULT_MEMBERS,
               **patches):
        runners = _Runners() if runners is None else runners
        # A distinct sentinel, so every explicit value, None included, reaches build_train.
        if members is _DEFAULT_MEMBERS:
            members = [(task_id, PRS[task_id]) for task_id in task_ids]
        main_read = mock.Mock(return_value=self.repo.base) if main is None else main
        check = patches.get('check', mock.Mock(side_effect=self._fake_check))
        with mock.patch.object(task_completion, 'read_authenticated_main_sha', main_read), \
                mock.patch.object(merge_train_eligibility, 'member_check', check):
            result = merge_train_build.build_train(
                members, store=self.store, caller_key=CALLER, github_get=self._github_get,
                git_runner=runners.git, gh_runner=runners.gh, root=self.repo.path,
                train_root=self.train_root, now=NOW)
        return result, runners, main_read

    def _records(self):
        return merge_train_store.list_records(self.train_root)

    def _assert_built(self, result, runners, order, excluded=()):
        self.assertEqual(result['step'], 'BUILT')
        self.assertEqual(result['codes'], [])
        value = result['result']
        self.assertEqual(set(value), {'train_id', 'train_pr', 'train_head', 'main_sha',
                                      'members', 'excluded'})
        train_id = value['train_id']
        self.assertIsNotNone(TRAIN_ID.fullmatch(train_id))
        branch = 'llmtool/train-' + train_id
        self.assertEqual(value['train_pr'], PR_NUMBER)
        self.assertEqual(value['main_sha'], self.repo.base)
        self.assertEqual(value['members'], [
            {'task_id': task_id, 'pr': PRS[task_id], 'head': self.heads[task_id]}
            for task_id in order])
        self.assertEqual(value['excluded'], list(excluded))
        self.assertTrue(merge_train.train_delta_verified(
            self.repo.path, self.repo.base, [self.heads[task_id] for task_id in order],
            value['train_head']))

        # The stored record passes the store's own validation.
        record = merge_train_store.get(train_id, root=self.train_root)
        self.assertEqual(record['state'], 'BUILT')
        self.assertEqual(record['train_branch'], branch)
        self.assertEqual(record['train_head'], value['train_head'])
        self.assertEqual(record['train_pr'], PR_NUMBER)
        self.assertEqual(record['main_sha'], self.repo.base)
        self.assertIsNone(record['merge_attempt'])
        self.assertIsNone(record['merge_sha'])
        self.assertEqual(record['members'], [
            _eligible_member(task_id, PRS[task_id], self.heads[task_id])
            for task_id in order])
        self.assertEqual(record['excluded'], list(excluded))
        self.assertEqual(merge_train_store.record_digest(record), record['record_sha256'])

        # One push without force, then one create and one list.
        self.assertEqual(runners.git_calls, [
            (self.repo.path, ['push', 'origin',
                              value['train_head'] + ':refs/heads/' + branch])])
        self.assertEqual(len(runners.gh_calls), 2)
        root, create = runners.gh_calls[0]
        self.assertEqual(root, self.repo.path)
        self.assertEqual(create[:6], ['pr', 'create', '--base', 'main', '--head', branch])
        self.assertEqual(create[6], '--title')
        self.assertEqual(create[8], '--body')
        self.assertEqual(len(create), 10)
        self.assertIn(train_id, create[7])
        body = create[9]
        for text in (train_id, self.repo.base, value['train_head']):
            self.assertIn(text, body)
        for task_id in order:
            self.assertIn(task_id, body)
            self.assertIn(self.heads[task_id], body)
            self.assertIn('#%d' % PRS[task_id], body)
        self.assertEqual(runners.gh_calls[1], (self.repo.path, [
            'pr', 'list', '--head', branch, '--state', 'open', '--json',
            'number,headRefOid']))
        text = json.dumps(result)
        self.assertNotIn('COMMAND-OUTPUT-MARKER', text)
        self.assertNotIn('LISTING-OUTPUT-MARKER', text)
        return record

    def _assert_no_push(self, runners):
        self.assertEqual(runners.git_calls, [])
        self.assertEqual(runners.gh_calls, [])

    # Builds.

    def test_two_member_build(self):
        result, runners, main_read = self._build([TASK_A, TASK_B])
        self._assert_built(result, runners, [TASK_A, TASK_B])
        main_read.assert_called_once_with()
        self.assertEqual([call[:2] for call in self.check_calls],
                         [(TASK_A, PRS[TASK_A]), (TASK_B, PRS[TASK_B])])
        self.assertEqual(len(self._records()), 1)

    def test_three_member_build_keeps_given_order(self):
        result, runners, _ = self._build([TASK_C, TASK_A, TASK_B])
        self._assert_built(result, runners, [TASK_C, TASK_A, TASK_B])

    def test_six_members_is_the_limit(self):
        members = [('%032x' % index, 200 + index) for index in range(1, 8)]
        for count in (1, 7):
            result, runners, main_read = self._build(None, members=members[:count])
            self.assertEqual(result, {'step': 'REFUSED',
                                      'codes': ['TRAIN_MEMBERS_INVALID'], 'result': None})
            main_read.assert_not_called()
            self._assert_no_push(runners)
        self.assertEqual(self.store.reads, [])

    def test_invalid_members_refuse_before_any_read(self):
        invalid = [
            [(TASK_A, 101), (TASK_A, 102)],
            [(TASK_A, 101), (TASK_B, 101)],
            [(TASK_A, 101), (TASK_B, True)],
            [(TASK_A, 101), (TASK_B, 0)],
            [(TASK_A, 101), ('B' * 32, 102)],
            [(TASK_A, 101), (TASK_B, 102, 'extra')],
            [(TASK_A, 101), TASK_B],
            'not-a-list',
            None,
        ]
        for members in invalid:
            result, runners, main_read = self._build(None, members=members)
            self.assertEqual(result['step'], 'REFUSED')
            self.assertEqual(result['codes'], ['TRAIN_MEMBERS_INVALID'])
            main_read.assert_not_called()
            self._assert_no_push(runners)
        self.assertEqual(self.store.reads, [])
        self.assertEqual(self.check_calls, [])
        self.assertEqual(self._records(), [])

    # Caller.

    def test_non_owner_member_refuses_before_any_other_read(self):
        self.store.records[TASK_B]['pm_session_id'] = OTHER
        result, runners, main_read = self._build([TASK_A, TASK_B, TASK_C])
        self.assertEqual(result, {'step': 'REFUSED', 'codes': [pm_caller.NOT_TASK_OWNER],
                                  'result': None})
        main_read.assert_not_called()
        self.assertEqual(self.check_calls, [])
        self.assertEqual(self.github_calls, [])
        # Only the owner checks read the store, and they stop at the refusal.
        self.assertEqual(self.store.reads, [TASK_A, TASK_B])
        self._assert_no_push(runners)
        self.assertEqual(self._records(), [])

    def test_missing_member_record_refuses(self):
        del self.store.records[TASK_B]
        result, runners, main_read = self._build([TASK_A, TASK_B])
        self.assertEqual(result['codes'], [pm_caller.TASK_RECORD_UNAVAILABLE])
        main_read.assert_not_called()
        self._assert_no_push(runners)

    # Main.

    def test_main_unavailable_refuses(self):
        failing = mock.Mock(side_effect=LLMToolError('details omitted'))
        result, runners, _ = self._build([TASK_A, TASK_B], main=failing)
        self.assertEqual(result, {'step': 'REFUSED', 'codes': ['TRAIN_MAIN_UNAVAILABLE'],
                                  'result': None})
        self.assertEqual(self.check_calls, [])
        self._assert_no_push(runners)
        self.assertEqual(self._records(), [])

    def test_malformed_main_refuses(self):
        result, runners, _ = self._build([TASK_A, TASK_B],
                                         main=mock.Mock(return_value='main'))
        self.assertEqual(result['codes'], ['TRAIN_MAIN_UNAVAILABLE'])
        self._assert_no_push(runners)

    # Eligibility.

    def test_ineligible_member_is_excluded(self):
        # TASK_C goes through the real member_check and is not ready.
        self.store.records[TASK_C]['completion']['state'] = 'IMPLEMENTING'
        self.real_check.add(TASK_C)
        result, runners, _ = self._build([TASK_A, TASK_C, TASK_B])
        excluded = [{'task_id': TASK_C, 'pr': PRS[TASK_C],
                     'code': merge_train_eligibility.TRAIN_MEMBER_NOT_READY}]
        self._assert_built(result, runners, [TASK_A, TASK_B], excluded)
        self.assertEqual(self.github_calls, [])

    def test_member_check_error_refuses(self):
        check = mock.Mock(side_effect=RuntimeError('boom'))
        result, runners, _ = self._build([TASK_A, TASK_B], check=check)
        self.assertEqual(result['codes'], ['TRAIN_MEMBER_CHECK_FAILED'])
        self._assert_no_push(runners)
        self.assertEqual(self._records(), [])

    def test_member_check_for_another_pr_refuses(self):
        def check(store, task_id, pr, github_get, train_root=None):
            return {'eligible': True, 'code': None,
                    'member': _eligible_member(task_id, pr + 1, self.heads[task_id])}
        result, runners, _ = self._build([TASK_A, TASK_B], check=mock.Mock(side_effect=check))
        self.assertEqual(result['codes'], ['TRAIN_MEMBER_CHECK_FAILED'])
        self._assert_no_push(runners)

    # Candidate.

    def test_conflicting_member_is_excluded(self):
        result, runners, _ = self._build([TASK_A, TASK_X, TASK_B])
        excluded = [{'task_id': TASK_X, 'pr': PRS[TASK_X],
                     'code': merge_train.TRAIN_MEMBER_CONFLICT}]
        self._assert_built(result, runners, [TASK_A, TASK_B], excluded)

    def test_fewer_than_two_merged_refuses_and_records_nothing(self):
        result, runners, _ = self._build([TASK_A, TASK_X])
        self.assertEqual(result['step'], 'REFUSED')
        self.assertEqual(result['codes'], [merge_train.TRAIN_TOO_FEW_MEMBERS])
        self._assert_no_push(runners)
        self.assertEqual(self._records(), [])

    def test_fewer_than_two_eligible_refuses_and_records_nothing(self):
        self.ineligible[TASK_B] = merge_train_eligibility.TRAIN_MEMBER_RULE_FILE
        result, runners, _ = self._build([TASK_A, TASK_B])
        self.assertEqual(result['step'], 'REFUSED')
        self.assertEqual(result['codes'], [merge_train.TRAIN_TOO_FEW_MEMBERS])
        self._assert_no_push(runners)
        self.assertEqual(self._records(), [])

    def test_unavailable_main_commit_refuses(self):
        missing = 'f' * 40
        result, runners, _ = self._build([TASK_A, TASK_B],
                                         main=mock.Mock(return_value=missing))
        self.assertEqual(result['codes'], [merge_train.TRAIN_BASE_UNAVAILABLE])
        self._assert_no_push(runners)
        self.assertEqual(self._records(), [])

    # Push.

    def test_push_failure_leaves_built_record_and_opens_no_pr(self):
        result, runners, _ = self._build([TASK_A, TASK_B], runners=_Runners(push_code=1))
        self.assertEqual(result['step'], 'REFUSED')
        self.assertEqual(result['codes'], ['TRAIN_PUSH_FAILED'])
        self.assertEqual(runners.gh_calls, [])
        self.assertEqual(len(runners.git_calls), 1)
        records = self._records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['state'], 'BUILT')
        self.assertIsNone(records[0]['train_pr'])
        self.assertEqual(result['result'], {'train_id': records[0]['train_id']})

    def test_push_runner_error_is_push_failure(self):
        runners = _Runners()

        def broken(root, args):
            runners.git_calls.append((root, list(args)))
            raise OSError('no network')
        runners.git = broken
        result, runners, _ = self._build([TASK_A, TASK_B], runners=runners)
        self.assertEqual(result['codes'], ['TRAIN_PUSH_FAILED'])
        self.assertEqual(runners.gh_calls, [])
        self.assertIsNone(self._records()[0]['train_pr'])

    def test_push_never_forces(self):
        for order in ([TASK_A, TASK_B], [TASK_A, TASK_B, TASK_C]):
            result, runners, _ = self._build(order)
            self.assertEqual(result['step'], 'BUILT')
            self.assertEqual(len(runners.git_calls), 1)
            args = runners.git_calls[0][1]
            self.assertEqual(args[:2], ['push', 'origin'])
            self.assertEqual(len(args), 3)
            self.assertFalse(any(arg.startswith('-') for arg in args))
            self.assertFalse(args[2].startswith('+'))
            self.assertNotIn('force', ' '.join(args))
            # The branch is llmtool/train-<train_id>.
            train_id = result['result']['train_id']
            self.assertTrue(args[2].endswith(':refs/heads/llmtool/train-' + train_id))

    # PR.

    def test_pr_listing_at_another_head_refuses(self):
        def listing(runners):
            return [{'number': PR_NUMBER, 'headRefOid': 'f' * 40}]
        result, runners, _ = self._build([TASK_A, TASK_B], runners=_Runners(listing=listing))
        self.assertEqual(result['step'], 'REFUSED')
        self.assertEqual(result['codes'], ['TRAIN_PR_CREATE_FAILED'])
        record = self._records()[0]
        self.assertEqual(record['state'], 'BUILT')
        self.assertIsNone(record['train_pr'])

    def test_pr_listing_with_two_prs_refuses(self):
        def listing(runners):
            return [{'number': PR_NUMBER, 'headRefOid': runners.pushed_head},
                    {'number': PR_NUMBER + 1, 'headRefOid': runners.pushed_head}]
        result, _, _ = self._build([TASK_A, TASK_B], runners=_Runners(listing=listing))
        self.assertEqual(result['codes'], ['TRAIN_PR_CREATE_FAILED'])
        self.assertIsNone(self._records()[0]['train_pr'])

    def test_pr_listing_empty_or_malformed_refuses(self):
        for pulls in ([], {'number': PR_NUMBER}, [{'number': '77', 'headRefOid': None}]):
            self.setUp()
            result, _, _ = self._build([TASK_A, TASK_B],
                                       runners=_Runners(listing=lambda _r, p=pulls: p))
            self.assertEqual(result['codes'], ['TRAIN_PR_CREATE_FAILED'])
            self.assertIsNone(self._records()[0]['train_pr'])

    def test_pr_create_failure_refuses_without_listing(self):
        result, runners, _ = self._build([TASK_A, TASK_B], runners=_Runners(create_code=1))
        self.assertEqual(result['codes'], ['TRAIN_PR_CREATE_FAILED'])
        self.assertEqual([args[:2] for _, args in runners.gh_calls], [['pr', 'create']])
        self.assertIsNone(self._records()[0]['train_pr'])

    # Record.

    def test_record_passes_store_validation(self):
        result, runners, _ = self._build([TASK_A, TASK_B, TASK_C])
        record = self._assert_built(result, runners, [TASK_A, TASK_B, TASK_C])
        body = {key: value for key, value in record.items() if key != 'record_sha256'}
        self.assertTrue(merge_train_store._valid_body(body))
        self.assertIs(merge_train_store._validate(copy.deepcopy(record)).__class__, dict)
        self.assertTrue(merge_train_store._utc_iso(record['created_at']))
        self.assertEqual(merge_train_store.find_by_pr(PR_NUMBER, root=self.train_root),
                         record)


if __name__ == '__main__':
    unittest.main()
