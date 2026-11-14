"""Tests for merge_train_eligibility.member_check and merge_train_store.list_records."""

import copy
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import merge_train_eligibility as eligibility  # noqa: E402
from llmtool import merge_train_store, sensitive_paths, task_store  # noqa: E402
from llmtool.errors import LLMToolError  # noqa: E402

TASK_ID = '0123456789abcdef0123456789abcdef'
OTHER_TASK = 'fedcba9876543210fedcba9876543210'
THIRD_TASK = 'aaaabbbbccccddddeeeeffff00001111'
EXECUTOR = 'train-member-one'
PR = 41
HEAD = '1' * 40
OTHER_HEAD = '2' * 40
LINUX = 'check (Linux 3.14)'
MACOS = 'check (macOS 3.13)'
DOCS = 'check (docs)'
RULE_FILE = 'tools/llmtool/worker-dispatch.json'
RULES = {'schema_version': 1, 'exact': [RULE_FILE], 'prefixes': ['tools/llmtool/rules/'],
         'globs': []}
PULL = f'pulls/{PR}'
CHECKS = f'commits/{HEAD}/check-runs?per_page=100'
FILES = f'pulls/{PR}/files?per_page=100'
COMMENTS = f'issues/{PR}/comments?per_page=100'


def _receipt(head, result='PASS', ran_total=3):
    body = {'source_sha': head, 'result': result,
            'modules': ['test_merge_train_eligibility'], 'ran_total': ran_total,
            'elapsed_s': 1.5}
    body['receipt_digest'] = hashlib.sha256(task_store._json_bytes(body)).hexdigest()
    return body


def _run(run_id, name, conclusion='success', head=HEAD, status='completed'):
    return {'id': run_id, 'name': name, 'head_sha': head, 'status': status,
            'conclusion': conclusion}


def _checks(*runs):
    return {'total_count': len(runs), 'check_runs': list(runs)}


def _record(**changes):
    record = {
        'task_id': TASK_ID,
        'run_id': 'run-1',
        'completion': {'state': 'PM_EVIDENCE_READY', 'executor_task_id': EXECUTOR},
        'review_dispatch_claim': [
            {'pr': PR, 'head_sha': HEAD, 'state': 'COMPLETED', 'version': 2,
             'review_result': {'verdict': 'PASS'}},
        ],
        'completion_events': [
            {'event_id': 'e' * 32, 'source': 'REVIEWER', 'source_sha': HEAD,
             'version': 2, 'outcome': 'REVIEW_PASS', 'state': 'PENDING'},
        ],
        'remote_test_receipts': [
            _receipt(HEAD, result='FAIL', ran_total=1),
            _receipt(HEAD, result='PASS', ran_total=3),
            _receipt(OTHER_HEAD, result='FAIL', ran_total=2),
        ],
        'merge_attempt': None,
    }
    record.update(changes)
    return record


def _pull(**changes):
    pull = {'number': PR, 'state': 'open', 'merged': False, 'mergeable': True,
            'head': {'sha': HEAD}, 'labels': []}
    pull.update(changes)
    return pull


def _routes(**changes):
    routes = {
        PULL: _pull(),
        CHECKS: _checks(_run(101, LINUX), _run(150, LINUX), _run(90, MACOS, 'skipped'),
                        _run(120, MACOS)),
        FILES: [{'filename': 'tools/llmtool/llmtool/merge_train_eligibility.py'}],
        COMMENTS: [],
    }
    routes.update(changes)
    return routes


class _Reads:
    """A route that answers successive reads in order; the last answer repeats."""

    def __init__(self, *values):
        self.values = list(values)

    def next(self):
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


class FakeGitHub:
    """Read-only fake: a route value is returned, an Exception value is raised."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        if path not in self.routes:
            raise LookupError('unrouted read')
        value = self.routes[path]
        if isinstance(value, _Reads):
            value = value.next()
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)


class MemoryStore:
    """In-memory TaskStore stand-in that fails the test on any write."""

    def __init__(self, records):
        self.records = records
        self.writes = []

    def get(self, task_id):
        if task_id not in self.records:
            raise LLMToolError('TASK_NOT_FOUND')
        return copy.deepcopy(self.records[task_id])

    def compare_and_update(self, *args, **kwargs):
        self.writes.append((args, kwargs))
        raise AssertionError('member_check must not write the TaskStore')

    def update(self, *args, **kwargs):
        self.compare_and_update(*args, **kwargs)


def _member(task_id, pr, head):
    return {'task_id': task_id, 'pr': pr, 'head': head, 'executor_task': EXECUTOR,
            'review_version': 1, 'linux_check_run_id': 7, 'macos_check_run_id': 8,
            'remote_test_receipt_digest': 'c' * 64}


def _train(train_id, members):
    return {'schema_version': 1, 'train_id': train_id, 'main_sha': 'a' * 40,
            'members': members, 'excluded': [], 'train_branch': 'llmtool/train-' + train_id,
            'train_head': 'b' * 40, 'train_pr': None, 'state': 'BUILT',
            'merge_attempt': None, 'merge_sha': None, 'created_at': '2026-11-08T00:00:00Z'}


def _filler_members():
    return [_member(OTHER_TASK, 500, '3' * 40), _member(THIRD_TASK, 501, '4' * 40)]


class _Base(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(sensitive_paths, '_read_list',
                                    return_value=copy.deepcopy(RULES))
        patcher.start()
        self.addCleanup(patcher.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        # The train store lives beside the TaskStore root: <parent>/merge-trains.
        self.train_root = self.home / 'tasks'
        self.train_dir = self.home / merge_train_store.DIRECTORY_NAME
        # The mergeable wait is injected as a no-op that records each call.
        self.sleeps = []

    def check(self, record=None, routes=None, task_id=TASK_ID, pr=PR):
        record = _record() if record is None else record
        self.store = MemoryStore({TASK_ID: record})
        self.github = FakeGitHub(_routes() if routes is None else routes)
        return eligibility.member_check(self.store, task_id, pr, self.github,
                                        train_root=self.train_root,
                                        sleep=self.sleeps.append)

    def assertRefused(self, result, code):
        self.assertEqual(result, {'eligible': False, 'code': code, 'member': None})


class EligibleMemberTests(_Base):
    def test_executor_fixture_is_a_valid_d6_task_id(self):
        self.assertIsNotNone(task_store.D6_TASK_ID.fullmatch(EXECUTOR))

    def test_eligible_member_and_exact_fields(self):
        result = self.check()
        self.assertEqual(result, {'eligible': True, 'code': None, 'member': {
            'task_id': TASK_ID, 'pr': PR, 'head': HEAD, 'executor_task': EXECUTOR,
            'review_version': 2, 'linux_check_run_id': 150, 'macos_check_run_id': 120,
            'remote_test_receipt_digest': _receipt(HEAD)['receipt_digest'],
        }})
        member = result['member']
        self.assertEqual(set(member), merge_train_store.MEMBER_FIELDS)
        self.assertTrue(merge_train_store._valid_member(member))

    def test_check_is_read_only(self):
        self.check()
        self.assertEqual(self.store.writes, [])
        # Every GitHub call is a GET-style read path; the fake has no write path.
        self.assertTrue(set(self.github.calls) <= {PULL, CHECKS, FILES, COMMENTS})
        # A missing train directory is not created by the check.
        self.assertFalse(self.train_dir.exists())

    def test_each_github_path_is_read_once(self):
        self.check()
        for path in set(self.github.calls):
            self.assertEqual(self.github.calls.count(path), 1, path)

    def test_github_get_none_uses_completion_reader(self):
        store = MemoryStore({TASK_ID: _record()})
        fake = FakeGitHub(_routes())
        with mock.patch.object(eligibility.task_completion, '_github_get', fake):
            result = eligibility.member_check(store, TASK_ID, PR, None,
                                              train_root=self.train_root)
        self.assertTrue(result['eligible'])

    def test_docs_only_member_follows_docs_rule(self):
        routes = _routes(**{CHECKS: _checks(_run(55, DOCS)),
                            FILES: [{'filename': 'docs/guide.md'}]})
        result = self.check(routes=routes)
        self.assertTrue(result['eligible'], result)
        self.assertIsNone(result['member']['linux_check_run_id'])
        self.assertIsNone(result['member']['macos_check_run_id'])

    def test_docs_only_member_without_docs_check_refuses(self):
        routes = _routes(**{CHECKS: _checks(_run(55, LINUX)),
                            FILES: [{'filename': 'docs/guide.md'}]})
        self.assertRefused(self.check(routes=routes), 'MERGE_REQUIRED_CHECK_MISSING')

    def test_scripts_only_member_needs_linux_and_macos(self):
        routes = _routes(**{CHECKS: _checks(_run(101, LINUX), _run(77, MACOS)),
                            FILES: [{'filename': 'scripts/tool.sh'}]})
        result = self.check(routes=routes)
        self.assertTrue(result['eligible'], result)
        self.assertEqual(result['member']['linux_check_run_id'], 101)
        self.assertEqual(result['member']['macos_check_run_id'], 77)

    def test_scripts_only_member_without_linux_refuses(self):
        routes = _routes(**{CHECKS: _checks(_run(77, MACOS)),
                            FILES: [{'filename': 'scripts/tool.sh'}]})
        self.assertRefused(self.check(routes=routes), 'MERGE_REQUIRED_CHECK_MISSING')

    def test_scripts_only_member_with_skipped_linux_refuses(self):
        routes = _routes(**{CHECKS: _checks(_run(101, LINUX, 'skipped'), _run(77, MACOS)),
                            FILES: [{'filename': 'scripts/tool.sh'}]})
        self.assertRefused(self.check(routes=routes), 'MERGE_REQUIRED_CHECK_SKIPPED')

    def test_scripts_only_member_without_macos_refuses(self):
        routes = _routes(**{CHECKS: _checks(_run(101, LINUX)),
                            FILES: [{'filename': 'scripts/llmtool_monitor.py'}]})
        self.assertRefused(self.check(routes=routes), 'MERGE_REQUIRED_CHECK_MISSING')

    def test_superseded_train_does_not_block(self):
        created = merge_train_store.create(
            _train('old-train', [_member(TASK_ID, PR, HEAD)] + _filler_members()[:1]),
            root=self.train_root)
        merge_train_store.compare_and_update('old-train', created['record_sha256'],
                                             root=self.train_root, state='SUPERSEDED')
        self.assertTrue(self.check()['eligible'])


class RecordAndPrRefusalTests(_Base):
    def test_record_unavailable(self):
        store = MemoryStore({})
        result = eligibility.member_check(store, TASK_ID, PR, FakeGitHub(_routes()),
                                          train_root=self.train_root)
        self.assertRefused(result, 'TRAIN_MEMBER_RECORD_UNAVAILABLE')

    def test_record_for_another_task_is_unavailable(self):
        self.assertRefused(self.check(record=_record(task_id=OTHER_TASK)),
                           'TRAIN_MEMBER_RECORD_UNAVAILABLE')

    def test_invalid_task_id_is_unavailable(self):
        self.assertRefused(self.check(task_id='not-a-task'),
                           'TRAIN_MEMBER_RECORD_UNAVAILABLE')

    def test_not_ready_state(self):
        record = _record(completion={'state': 'WAITING_PM', 'executor_task_id': EXECUTOR})
        self.assertRefused(self.check(record=record), 'TRAIN_MEMBER_NOT_READY')

    def test_no_executor_task(self):
        record = _record(completion={'state': 'PM_EVIDENCE_READY'})
        self.assertRefused(self.check(record=record), 'TRAIN_MEMBER_NOT_READY')

    def test_no_completion(self):
        self.assertRefused(self.check(record=_record(completion=None)),
                           'TRAIN_MEMBER_NOT_READY')

    def test_pr_read_failed(self):
        routes = _routes(**{PULL: OSError('down')})
        self.assertRefused(self.check(routes=routes), 'TRAIN_MEMBER_PR_UNAVAILABLE')

    def test_pr_malformed(self):
        for pull in ([], _pull(head=None), _pull(head={'sha': 'xyz'}), _pull(number=42)):
            with self.subTest(pull=pull):
                self.assertRefused(self.check(routes=_routes(**{PULL: pull})),
                                   'TRAIN_MEMBER_PR_UNAVAILABLE')

    def test_pr_missing_merged_is_unavailable(self):
        pull = _pull()
        del pull['merged']
        self.assertRefused(self.check(routes=_routes(**{PULL: pull})),
                           'TRAIN_MEMBER_PR_UNAVAILABLE')

    def test_pr_non_bool_merged_is_unavailable(self):
        for merged in (None, 'false', 0, 1):
            with self.subTest(merged=merged):
                self.assertRefused(self.check(routes=_routes(**{PULL: _pull(merged=merged)})),
                                   'TRAIN_MEMBER_PR_UNAVAILABLE')

    def test_pr_missing_state_is_unavailable(self):
        pull = _pull()
        del pull['state']
        self.assertRefused(self.check(routes=_routes(**{PULL: pull})),
                           'TRAIN_MEMBER_PR_UNAVAILABLE')

    def test_pr_non_string_state_is_unavailable(self):
        for state in (None, 1, True, ['open']):
            with self.subTest(state=state):
                self.assertRefused(self.check(routes=_routes(**{PULL: _pull(state=state)})),
                                   'TRAIN_MEMBER_PR_UNAVAILABLE')

    def test_pr_wrong_or_missing_number_is_unavailable(self):
        missing = _pull()
        del missing['number']
        for pull in (_pull(number=PR + 1), _pull(number=str(PR)), _pull(number=None),
                     _pull(number=True), missing):
            with self.subTest(pull=pull):
                self.assertRefused(self.check(routes=_routes(**{PULL: pull})),
                                   'TRAIN_MEMBER_PR_UNAVAILABLE')

    def test_pr_malformed_decides_before_open_check(self):
        # A closed PR whose snapshot is malformed is unavailable, not "not open".
        closed_no_merged = _pull(state='closed')
        del closed_no_merged['merged']
        for pull in (closed_no_merged, _pull(merged='true'), _pull(state=None, merged=True)):
            with self.subTest(pull=pull):
                self.assertRefused(self.check(routes=_routes(**{PULL: pull})),
                                   'TRAIN_MEMBER_PR_UNAVAILABLE')
                self.assertEqual(self.github.calls, [PULL])

    def test_well_formed_closed_or_merged_pr_is_not_open(self):
        for pull in (_pull(state='closed'), _pull(state='closed', merged=True),
                     _pull(merged=True)):
            with self.subTest(pull=pull):
                self.assertRefused(self.check(routes=_routes(**{PULL: pull})),
                                   'TRAIN_MEMBER_PR_NOT_OPEN')

    def test_pr_argument_invalid(self):
        for pr in (0, -1, True, '41', None):
            with self.subTest(pr=pr):
                self.assertRefused(self.check(pr=pr), 'TRAIN_MEMBER_PR_UNAVAILABLE')

    def test_pr_not_open(self):
        self.assertRefused(self.check(routes=_routes(**{PULL: _pull(state='closed')})),
                           'TRAIN_MEMBER_PR_NOT_OPEN')

    def test_pr_already_merged(self):
        self.assertRefused(self.check(routes=_routes(**{PULL: _pull(merged=True)})),
                           'TRAIN_MEMBER_PR_NOT_OPEN')

    def test_merge_claimed(self):
        attempt = {'pr': PR, 'head_sha': HEAD, 'state': 'UNKNOWN',
                   'claimed_at': '2026-11-08T00:00:00Z'}
        self.assertRefused(self.check(record=_record(merge_attempt=attempt)),
                           'TRAIN_MEMBER_MERGE_CLAIMED')

    def test_first_failure_decides(self):
        attempt = {'pr': PR, 'head_sha': HEAD, 'state': 'CLAIMED',
                   'claimed_at': '2026-11-08T00:00:00Z'}
        result = self.check(record=_record(merge_attempt=attempt),
                            routes=_routes(**{PULL: _pull(state='closed')}))
        self.assertRefused(result, 'TRAIN_MEMBER_PR_NOT_OPEN')
        self.assertEqual(self.github.calls, [PULL])


class ReviewRefusalTests(_Base):
    def test_no_review_claim(self):
        record = _record(review_dispatch_claim=[], completion_events=[])
        self.assertRefused(self.check(record=record), 'TRAIN_MEMBER_REVIEW_NOT_PASS')

    def test_review_fail(self):
        record = _record(
            review_dispatch_claim=[{'pr': PR, 'head_sha': HEAD, 'state': 'COMPLETED',
                                    'version': 2, 'review_result': {'verdict': 'FAIL'}}],
            completion_events=[{'event_id': 'e' * 32, 'source': 'REVIEWER',
                                'source_sha': HEAD, 'version': 2,
                                'outcome': 'REVIEW_FAIL', 'state': 'PENDING'}])
        self.assertRefused(self.check(record=record), 'MERGE_REVIEW_NOT_PASS')

    def test_newer_review_exists(self):
        record = _record()
        record['review_dispatch_claim'].append(
            {'pr': PR, 'head_sha': HEAD, 'state': 'DISPATCHED', 'version': 3})
        self.assertRefused(self.check(record=record), 'MERGE_NEWER_REVIEW_EXISTS')

    def test_pass_for_another_head_is_not_pass(self):
        record = _record()
        record['review_dispatch_claim'][0]['head_sha'] = OTHER_HEAD
        record['completion_events'][0]['source_sha'] = OTHER_HEAD
        self.assertRefused(self.check(record=record), 'TRAIN_MEMBER_REVIEW_NOT_PASS')

    def test_review_decides_before_ci(self):
        record = _record(review_dispatch_claim=[], completion_events=[])
        routes = _routes(**{CHECKS: _checks(_run(101, LINUX, 'failure'))})
        self.assertRefused(self.check(record=record, routes=routes),
                           'TRAIN_MEMBER_REVIEW_NOT_PASS')


class CiRefusalTests(_Base):
    def assertCi(self, checks, code, files=None, pull=None):
        changes = {CHECKS: checks}
        if files is not None:
            changes[FILES] = files
        if pull is not None:
            changes[PULL] = pull
        self.assertRefused(self.check(routes=_routes(**changes)), code)

    def test_failed_check(self):
        self.assertCi(_checks(_run(101, LINUX, 'failure'), _run(120, MACOS)),
                      'MERGE_CHECKS_NOT_GREEN')

    def test_skipped_required_linux_check(self):
        self.assertCi(_checks(_run(101, LINUX, 'skipped'), _run(120, MACOS)),
                      'MERGE_REQUIRED_CHECK_SKIPPED')

    def test_missing_macos_check(self):
        self.assertCi(_checks(_run(101, LINUX)), 'MERGE_REQUIRED_CHECK_MISSING')

    def test_skipped_macos_check(self):
        self.assertCi(_checks(_run(101, LINUX), _run(90, MACOS, 'skipped')),
                      'MERGE_MACOS_CHECK_REQUIRED')

    def test_no_checks(self):
        self.assertCi(_checks(), 'MERGE_CHECKS_MISSING')

    def test_checks_unavailable(self):
        self.assertCi(OSError('down'), 'MERGE_CHECKS_UNAVAILABLE')

    def test_tui_change_needs_owner_acceptance(self):
        self.assertCi(_checks(_run(101, LINUX), _run(120, MACOS)),
                      'MERGE_OWNER_ACCEPTANCE_MANUAL',
                      files=[{'filename': 'tools/llmtool/llmtool/chat_tui.py'}])

    def test_not_mergeable(self):
        # A non-bool mergeable now gets one re-read (MergeableRereadTests);
        # mergeable false is refused by the CI gate as before.
        self.assertCi(_checks(_run(101, LINUX), _run(120, MACOS)),
                      'MERGE_PR_NOT_MERGEABLE', pull=_pull(mergeable=False))

    def test_invalid_success_run_id(self):
        self.assertCi(_checks(_run('x', LINUX), _run(120, MACOS)),
                      'MERGE_CHECKS_UNAVAILABLE')


class RuleFileTests(_Base):
    def test_rule_file_refuses(self):
        routes = _routes(**{FILES: [{'filename': 'tools/llmtool/llmtool/a.py'},
                                    {'filename': RULE_FILE}]})
        self.assertRefused(self.check(routes=routes), 'TRAIN_MEMBER_RULE_FILE')

    def test_renamed_rule_file_refuses(self):
        routes = _routes(**{FILES: [{'filename': 'tools/llmtool/llmtool/a.py',
                                     'previous_filename': 'tools/llmtool/rules/x.json'}]})
        self.assertRefused(self.check(routes=routes), 'TRAIN_MEMBER_RULE_FILE')

    def test_unreadable_sensitive_list_refuses(self):
        # pm-continuation.md "Rule-file PRs": when the sensitive-paths list cannot
        # be read, the PR is treated as a rule-file PR. The unreadable-list
        # sentinel therefore refuses TRAIN_MEMBER_RULE_FILE, not PR_UNAVAILABLE.
        with mock.patch.object(sensitive_paths, '_read_list', return_value=None):
            self.assertEqual(sensitive_paths.sensitive_matches(
                [{'filename': 'docs/a.md'}]), (sensitive_paths.SENSITIVE_LIST_UNREADABLE,))
            self.assertEqual(eligibility._rule_file_code([{'filename': 'docs/a.md'}]),
                             'TRAIN_MEMBER_RULE_FILE')
            self.assertRefused(self.check(), 'TRAIN_MEMBER_RULE_FILE')

    def test_unreadable_pr_file_list_read_is_refused_first_by_ci_gate(self):
        # The CI gate reads the same PR file list before the rule-file check,
        # so its own refusal code decides, as the check order requires.
        routes = _routes(**{FILES: OSError('down')})
        self.assertRefused(self.check(routes=routes), 'MERGE_CHECKS_UNAVAILABLE')

    def test_unreadable_file_list_is_pr_unavailable(self):
        for files in (None, {}, [None], [{'filename': 3}], [{}] * 100):
            with self.subTest(files=type(files)):
                self.assertEqual(eligibility._rule_file_code(files),
                                 'TRAIN_MEMBER_PR_UNAVAILABLE')
        self.assertIsNone(eligibility._rule_file_code([{'filename': 'docs/a.md'}]))


class RemoteTestRefusalTests(_Base):
    def test_no_receipt(self):
        self.assertRefused(self.check(record=_record(remote_test_receipts=[])),
                           'TRAIN_MEMBER_REMOTE_TEST_MISSING')

    def test_newest_receipt_failed(self):
        record = _record(remote_test_receipts=[_receipt(HEAD), _receipt(HEAD, 'FAIL')])
        self.assertRefused(self.check(record=record), 'TRAIN_MEMBER_REMOTE_TEST_MISSING')

    def test_receipt_for_another_head(self):
        record = _record(remote_test_receipts=[_receipt(OTHER_HEAD)])
        self.assertRefused(self.check(record=record), 'TRAIN_MEMBER_REMOTE_TEST_MISSING')

    def test_tampered_receipt(self):
        receipt = _receipt(HEAD)
        receipt['ran_total'] = 99
        record = _record(remote_test_receipts=[receipt])
        self.assertRefused(self.check(record=record), 'TRAIN_MEMBER_REMOTE_TEST_MISSING')


class MergeableRereadTests(_Base):
    """GitHub may not have computed mergeable yet: one wait, one fresh re-read."""

    def assertPullReads(self, count):
        self.assertEqual(self.github.calls.count(PULL), count)

    def test_wait_bound_is_at_most_five_seconds(self):
        self.assertGreater(eligibility.MERGEABLE_WAIT_SECONDS, 0)
        self.assertLessEqual(eligibility.MERGEABLE_WAIT_SECONDS, 5)

    def test_null_then_true_is_eligible_after_one_reread(self):
        routes = _routes(**{PULL: _Reads(_pull(mergeable=None), _pull())})
        result = self.check(routes=routes)
        self.assertTrue(result['eligible'], result)
        self.assertEqual(result['member']['head'], HEAD)
        self.assertPullReads(2)
        self.assertEqual(self.sleeps, [eligibility.MERGEABLE_WAIT_SECONDS])
        # The wait comes between the two PR reads.
        self.assertEqual(self.github.calls[:2], [PULL, PULL])

    def test_missing_mergeable_then_true_is_eligible(self):
        first = _pull()
        del first['mergeable']
        result = self.check(routes=_routes(**{PULL: _Reads(first, _pull())}))
        self.assertTrue(result['eligible'], result)
        self.assertPullReads(2)
        self.assertEqual(len(self.sleeps), 1)

    def test_null_twice_is_mergeable_unknown(self):
        routes = _routes(**{PULL: _Reads(_pull(mergeable=None), _pull(mergeable=None))})
        self.assertRefused(self.check(routes=routes),
                           eligibility.TRAIN_MEMBER_MERGEABLE_UNKNOWN)
        self.assertEqual(eligibility.TRAIN_MEMBER_MERGEABLE_UNKNOWN,
                         'TRAIN_MEMBER_MERGEABLE_UNKNOWN')
        self.assertPullReads(2)
        self.assertEqual(len(self.sleeps), 1)
        # Refused before any later gate reads.
        self.assertEqual(self.github.calls, [PULL, PULL])

    def test_non_bool_values_trigger_one_reread(self):
        for value in (None, 'unknown', 0, 1):
            with self.subTest(mergeable=value):
                self.sleeps.clear()
                routes = _routes(**{PULL: _Reads(_pull(mergeable=value),
                                                 _pull(mergeable=value))})
                self.assertRefused(self.check(routes=routes),
                                   eligibility.TRAIN_MEMBER_MERGEABLE_UNKNOWN)
                self.assertPullReads(2)
                self.assertEqual(len(self.sleeps), 1)

    def test_false_never_rereads(self):
        routes = _routes(**{PULL: _Reads(_pull(mergeable=False), _pull())})
        self.assertRefused(self.check(routes=routes), 'MERGE_PR_NOT_MERGEABLE')
        self.assertPullReads(1)
        self.assertEqual(self.sleeps, [])

    def test_true_on_first_read_never_waits(self):
        routes = _routes(**{PULL: _Reads(_pull(), _pull(mergeable=None))})
        self.assertTrue(self.check(routes=routes)['eligible'])
        self.assertPullReads(1)
        self.assertEqual(self.sleeps, [])

    def test_later_gates_judge_the_second_read(self):
        # The second read says false: the CI gate refuses on it, not on the first.
        routes = _routes(**{PULL: _Reads(_pull(mergeable=None), _pull(mergeable=False))})
        self.assertRefused(self.check(routes=routes), 'MERGE_PR_NOT_MERGEABLE')
        self.assertPullReads(2)

    def test_second_read_bypasses_the_snapshot_cache_once(self):
        routes = _routes(**{PULL: _Reads(_pull(mergeable=None), _pull(), _pull(mergeable=None))})
        self.assertTrue(self.check(routes=routes)['eligible'])
        # No third read: later gates see the cached second read.
        self.assertPullReads(2)

    def test_malformed_second_read_is_unavailable(self):
        for second in ([], OSError('down'), _pull(number=PR + 1), _pull(merged='no'),
                       _pull(head={'sha': 'xyz'})):
            with self.subTest(second=second):
                routes = _routes(**{PULL: _Reads(_pull(mergeable=None), second)})
                self.assertRefused(self.check(routes=routes),
                                   'TRAIN_MEMBER_PR_UNAVAILABLE')
                self.assertPullReads(2)

    def test_second_read_with_other_head_is_unavailable(self):
        routes = _routes(**{PULL: _Reads(_pull(mergeable=None),
                                         _pull(head={'sha': OTHER_HEAD}))})
        self.assertRefused(self.check(routes=routes), 'TRAIN_MEMBER_PR_UNAVAILABLE')
        self.assertEqual(self.github.calls, [PULL, PULL])

    def test_second_read_closed_is_not_open(self):
        routes = _routes(**{PULL: _Reads(_pull(mergeable=None),
                                         _pull(state='closed', mergeable=None))})
        self.assertRefused(self.check(routes=routes), 'TRAIN_MEMBER_PR_NOT_OPEN')

    def test_closed_first_read_never_waits(self):
        routes = _routes(**{PULL: _pull(state='closed', mergeable=None)})
        self.assertRefused(self.check(routes=routes), 'TRAIN_MEMBER_PR_NOT_OPEN')
        self.assertPullReads(1)
        self.assertEqual(self.sleeps, [])

    def test_default_sleep_is_time_sleep(self):
        store = MemoryStore({TASK_ID: _record()})
        fake = FakeGitHub(_routes(**{PULL: _Reads(_pull(mergeable=None), _pull())}))
        with mock.patch.object(eligibility.time, 'sleep') as sleep:
            result = eligibility.member_check(store, TASK_ID, PR, fake,
                                              train_root=self.train_root)
        self.assertTrue(result['eligible'], result)
        sleep.assert_called_once_with(eligibility.MERGEABLE_WAIT_SECONDS)


class RouteRemoteTestTests(_Base):
    """The member EC2 rule follows the PR route (docs-only, scripts-only, code)."""

    DOCS_ROUTES = {CHECKS: _checks(_run(55, DOCS)), FILES: [{'filename': 'docs/guide.md'}]}
    SCRIPTS_ROUTES = {CHECKS: _checks(_run(101, LINUX), _run(77, MACOS)),
                      FILES: [{'filename': 'scripts/tool.sh'}]}

    def assertNullDigestMember(self, result):
        self.assertTrue(result['eligible'], result)
        member = result['member']
        self.assertIsNone(member['remote_test_receipt_digest'])
        self.assertEqual(set(member), merge_train_store.MEMBER_FIELDS)
        self.assertTrue(merge_train_store._valid_member(member))

    def test_docs_only_member_without_receipt_is_eligible(self):
        result = self.check(record=_record(remote_test_receipts=[]),
                            routes=_routes(**self.DOCS_ROUTES))
        self.assertNullDigestMember(result)

    def test_docs_only_member_without_receipt_field_is_eligible(self):
        record = _record()
        del record['remote_test_receipts']
        self.assertNullDigestMember(self.check(record=record,
                                               routes=_routes(**self.DOCS_ROUTES)))

    def test_scripts_only_member_without_receipt_is_eligible(self):
        result = self.check(record=_record(remote_test_receipts=[]),
                            routes=_routes(**self.SCRIPTS_ROUTES))
        self.assertNullDigestMember(result)
        self.assertEqual(result['member']['linux_check_run_id'], 101)
        self.assertEqual(result['member']['macos_check_run_id'], 77)

    def test_scripts_only_member_with_other_head_receipt_only_is_eligible(self):
        record = _record(remote_test_receipts=[_receipt(OTHER_HEAD)])
        self.assertNullDigestMember(self.check(record=record,
                                               routes=_routes(**self.SCRIPTS_ROUTES)))

    def test_docs_only_member_with_pass_receipt_records_digest(self):
        result = self.check(routes=_routes(**self.DOCS_ROUTES))
        self.assertTrue(result['eligible'], result)
        self.assertEqual(result['member']['remote_test_receipt_digest'],
                         _receipt(HEAD)['receipt_digest'])

    def test_scripts_only_member_with_pass_receipt_records_digest(self):
        record = _record(remote_test_receipts=[_receipt(HEAD)])
        result = self.check(record=record, routes=_routes(**self.SCRIPTS_ROUTES))
        self.assertTrue(result['eligible'], result)
        self.assertEqual(result['member']['remote_test_receipt_digest'],
                         _receipt(HEAD)['receipt_digest'])

    def test_receipt_optional_member_with_newest_receipt_not_pass_refuses(self):
        record = _record(remote_test_receipts=[_receipt(HEAD), _receipt(HEAD, 'FAIL')])
        for routes in (self.DOCS_ROUTES, self.SCRIPTS_ROUTES):
            with self.subTest(files=routes[FILES]):
                self.assertRefused(self.check(record=record, routes=_routes(**routes)),
                                   'TRAIN_MEMBER_REMOTE_TEST_MISSING')

    def test_receipt_optional_member_with_tampered_receipt_refuses(self):
        receipt = _receipt(HEAD)
        receipt['ran_total'] = 99
        record = _record(remote_test_receipts=[receipt])
        self.assertRefused(self.check(record=record, routes=_routes(**self.DOCS_ROUTES)),
                           'TRAIN_MEMBER_REMOTE_TEST_MISSING')

    def test_receipt_optional_member_with_malformed_receipt_list_refuses(self):
        record = _record(remote_test_receipts={'source_sha': HEAD})
        self.assertRefused(self.check(record=record, routes=_routes(**self.SCRIPTS_ROUTES)),
                           'TRAIN_MEMBER_REMOTE_TEST_MISSING')

    def test_code_member_without_receipt_refuses(self):
        routes = _routes(**{FILES: [{'filename': 'tools/llmtool/llmtool/example.py'}]})
        self.assertRefused(self.check(record=_record(remote_test_receipts=[]), routes=routes),
                           'TRAIN_MEMBER_REMOTE_TEST_MISSING')

    def test_mixed_scripts_and_docs_member_without_receipt_refuses(self):
        # Neither docs-only nor scripts-only: the full code route needs a receipt.
        # The canonical linux_flags() set code and docs, so every required
        # check is green and only the missing receipt refuses.
        routes = _routes(**{CHECKS: _checks(_run(101, LINUX), _run(120, MACOS),
                                            _run(55, DOCS)),
                            FILES: [{'filename': 'scripts/tool.sh'},
                                    {'filename': 'docs/guide.md'}]})
        self.assertRefused(self.check(record=_record(remote_test_receipts=[]), routes=routes),
                           'TRAIN_MEMBER_REMOTE_TEST_MISSING')
        # Control: the same routes with a PASS receipt are eligible.
        self.assertTrue(self.check(routes=routes)['eligible'])

    def test_scripts_only_helper(self):
        self.assertTrue(eligibility._scripts_only(['scripts/a.py']))
        self.assertTrue(eligibility._scripts_only(['scripts/a.py', 'scripts/b.md']))
        for paths in ([], None, 'scripts/a.py', ['scripts/a.md'], ['scripts/a.py', 'a.py'],
                      ['tools/llmtool/llmtool/a.py'], [None]):
            with self.subTest(paths=paths):
                self.assertFalse(eligibility._scripts_only(paths))

    def test_scripts_only_helper_is_local(self):
        # A parallel PR removes the task_advance and task_completion copies.
        source = Path(eligibility.__file__).read_text(encoding='utf-8')
        self.assertNotIn('_scripts_only_ci', source)


class LiveTrainTests(_Base):
    def test_built_train_holding_task_refuses(self):
        merge_train_store.create(
            _train('live-train', [_member(TASK_ID, 900, '5' * 40)] + _filler_members()[:1]),
            root=self.train_root)
        self.assertRefused(self.check(), 'TRAIN_MEMBER_IN_LIVE_TRAIN')

    def test_merge_claimed_train_holding_pr_refuses(self):
        created = merge_train_store.create(
            _train('claimed-train', [_member(OTHER_TASK, PR, '5' * 40),
                                     _member(THIRD_TASK, 501, '4' * 40)]),
            root=self.train_root)
        attempt = {'pr': 900, 'head_sha': 'b' * 40, 'state': 'CLAIMED',
                   'claimed_at': '2026-11-08T00:00:01Z'}
        merge_train_store.compare_and_update(
            'claimed-train', created['record_sha256'], root=self.train_root,
            state='MERGE_CLAIMED', train_pr=900, merge_attempt=attempt)
        self.assertRefused(self.check(), 'TRAIN_MEMBER_IN_LIVE_TRAIN')

    def test_unknown_train_holding_task_refuses(self):
        created = merge_train_store.create(
            _train('unknown-train', [_member(TASK_ID, PR, HEAD)] + _filler_members()[:1]),
            root=self.train_root)
        attempt = {'pr': 900, 'head_sha': 'b' * 40, 'state': 'CLAIMED',
                   'claimed_at': '2026-11-08T00:00:01Z'}
        claimed = merge_train_store.compare_and_update(
            'unknown-train', created['record_sha256'], root=self.train_root,
            state='MERGE_CLAIMED', train_pr=900, merge_attempt=attempt)
        merge_train_store.compare_and_update(
            'unknown-train', claimed['record_sha256'], root=self.train_root,
            state='UNKNOWN', merge_attempt=dict(attempt, state='UNKNOWN'))
        self.assertRefused(self.check(), 'TRAIN_MEMBER_IN_LIVE_TRAIN')

    def test_live_train_without_task_or_pr_does_not_block(self):
        merge_train_store.create(_train('other-train', _filler_members()),
                                 root=self.train_root)
        self.assertTrue(self.check()['eligible'])

    def test_store_refusal(self):
        self.train_dir.mkdir(mode=0o700)
        bad = self.train_dir / 'broken.json'
        bad.write_text('{not json')
        os.chmod(bad, 0o600)
        self.assertRefused(self.check(), 'TRAIN_STORE_UNAVAILABLE')

    def test_train_store_not_written(self):
        merge_train_store.create(_train('other-train', _filler_members()),
                                 root=self.train_root)
        before = sorted(os.listdir(self.train_dir))
        contents = (self.train_dir / 'other-train.json').read_bytes()
        self.check()
        self.assertEqual(sorted(os.listdir(self.train_dir)), before)
        self.assertEqual((self.train_dir / 'other-train.json').read_bytes(), contents)


class ListRecordsTests(_Base):
    def assertInvalid(self):
        with self.assertRaises(LLMToolError) as caught:
            merge_train_store.list_records(self.train_root)
        self.assertEqual(caught.exception.merge_train_code,
                         merge_train_store.TRAIN_RECORD_INVALID)

    def test_missing_directory_is_empty_and_not_created(self):
        self.assertEqual(merge_train_store.list_records(self.train_root), [])
        self.assertFalse(self.train_dir.exists())

    def test_empty_directory(self):
        self.train_dir.mkdir(mode=0o700)
        os.chmod(self.train_dir, 0o700)
        self.assertEqual(merge_train_store.list_records(self.train_root), [])

    def test_sorted_by_train_id_and_validated(self):
        for train_id in ('b-train', 'a-train', 'a'):
            merge_train_store.create(_train(train_id, _filler_members()),
                                     root=self.train_root)
        # A lock file and an in-flight temporary are not records.
        (self.train_dir / '.a.lock').write_text('')
        records = merge_train_store.list_records(self.train_root)
        self.assertEqual([item['train_id'] for item in records], ['a', 'a-train', 'b-train'])
        for item in records:
            self.assertEqual(item, merge_train_store.get(item['train_id'],
                                                         root=self.train_root))

    def test_unparsable_record_is_invalid(self):
        merge_train_store.create(_train('good', _filler_members()), root=self.train_root)
        bad = self.train_dir / 'bad.json'
        bad.write_text('{not json')
        os.chmod(bad, 0o600)
        self.assertInvalid()

    def test_tampered_record_is_invalid(self):
        merge_train_store.create(_train('good', _filler_members()), root=self.train_root)
        path = self.train_dir / 'good.json'
        raw = path.read_text().replace('"BUILT"', '"SUPERSEDED"')
        os.chmod(path, 0o600)
        path.write_text(raw)
        self.assertInvalid()

    def test_unexpected_name_is_invalid(self):
        merge_train_store.create(_train('good', _filler_members()), root=self.train_root)
        stray = self.train_dir / 'README'
        stray.write_text('x')
        os.chmod(stray, 0o600)
        self.assertInvalid()

    def test_symlinked_directory_is_invalid(self):
        target = self.home / 'elsewhere'
        target.mkdir(mode=0o700)
        os.symlink(target, self.train_dir)
        self.assertInvalid()


if __name__ == '__main__':
    unittest.main()
