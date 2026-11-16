"""#2188: a REPLAN or split child whose Developer failed to start may be continued.

lineage_counts.check_continues accepts a child or REPLAN predecessor only when
its developer record shows a start failure (BLOCKED, no native session id, no
artifact or artifact digest, no result SHA). Every other child or REPLAN
predecessor still refuses CONTINUES_LINEAGE_CONFLICT. No provider is called.
"""
import copy
import hashlib
import io
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from llmtool import lineage_counts, task_submission_cli as cli
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore, new_id, record_lineage

import review_trust_fixture
import task_fixtures
import worker_policy_fixture

MAIN = 'e' * 40
RESULT_HEAD = 'c' * 40
AT = '2026-11-16T00:00:00Z'
SESSION = 'agy-session-0001'
ARTIFACT = ('diff --git a/src.py b/src.py\n--- a/src.py\n+++ b/src.py\n'
            '@@ -1 +1 @@\n-value = 1\n+value = 2\n')
ARTIFACT_SHA = hashlib.sha256(ARTIFACT.encode('utf-8')).hexdigest()
RESPONSE = json.dumps({
    'schema_version': 1, 'summary': 'Continue a REPLAN whose Developer never started.',
    'developer_assignment': {'route': 'claude', 'risk': 'LOW',
                             'goal': 'Continue the earlier Task.',
                             'acceptance': ['The continuation is recorded.']}})


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def tid(number):
    return f'{number:032x}'


def lineage(kind, root):
    return {'lineage_id': root, 'kind': kind, 'parent': root,
            'replaces': [tid(77)] if kind == 'replan' else [], 'state': 'OPEN',
            'reason': None, 'base_sha': MAIN, 'recorded_at': AT}


def execution(**changes):
    value = {'status': 'BLOCKED', 'native_session_id': None, 'process_exit_code': None,
             'stdout_sha256': None, 'elapsed_ms': None, 'cleanup_status': 'UNKNOWN',
             'usage_source': 'UNKNOWN', 'source_bytes': None, 'source_file_count': None,
             'source_manifest_sha256': None}
    value.update(changes)
    return value


def start_failed_developer(**changes):
    """An AGY developer record BLOCKED before the delegate ran."""
    developer = task_fixtures.developer_assignment(
        'agy', state='BLOCKED', provider_type='agy-cli', error_code='AGY_START_FAILED')
    developer.update(changes)
    return developer


class DictStore:
    """Read-only Task store fake keyed by Task ID."""

    def __init__(self, *records):
        self.records = {record['task_id']: record for record in records}

    def get(self, task_id):
        if task_id not in self.records:
            raise KeyError(task_id)
        return json.loads(json.dumps(self.records[task_id]))

    def task_ids(self):
        return sorted(self.records)


def refusal_code(call):
    try:
        call()
    except LLMToolError as error:
        return getattr(error, 'continues_code', None)
    return None


class StartFailureRuleTests(unittest.TestCase):
    """check_continues over in-memory records: one condition changed at a time."""

    ROOT, MEMBER, NEW = tid(1), tid(2), tid(3)

    def check(self, member):
        store = DictStore({'task_id': self.ROOT}, member)
        return refusal_code(lambda: lineage_counts.check_continues(
            store, self.NEW, self.MEMBER))

    def member(self, kind='replan', **record_changes):
        record = {'task_id': self.MEMBER, 'lineage': lineage(kind, self.ROOT),
                  'developer': start_failed_developer()}
        record.update(record_changes)
        return record

    def test_start_failed_replan_and_child_are_accepted(self):
        for kind in ('replan', 'child'):
            with self.subTest(kind=kind):
                self.assertIsNone(self.check(self.member(kind)))
        # Evidence with no result head, and an execution without a session, still pass.
        for name, changes in (
                ('execution without session', {'developer': start_failed_developer(
                    execution=execution())}),
                ('completion without evidence', {'completion': {
                    'executor_task_id': 'executor-1', 'evidence': None}}),
                ('evidence without result head', {'completion': {
                    'executor_task_id': 'executor-1',
                    'evidence': {'source_sha': MAIN, 'result': {'result_head': None}}}})):
            with self.subTest(case=name):
                self.assertIsNone(self.check(self.member(**changes)))

    def test_any_sign_of_a_run_refuses(self):
        cases = {
            'not blocked': {'state': 'DEVELOPER_OUTCOME_UNKNOWN'},
            'responded': {'state': 'DEVELOPER_RESPONDED', 'error_code': None},
            'native session': {'execution': execution(native_session_id=SESSION)},
            'artifact': {'artifact': ARTIFACT, 'artifact_sha256': ARTIFACT_SHA},
            'artifact only': {'artifact': ARTIFACT},
            'artifact digest only': {'artifact_sha256': ARTIFACT_SHA},
            'execution diff': {'execution': execution(status='TIMEOUT', diff_sha256='d' * 64,
                                                      diff_base_sha='a' * 40)},
            'execution not a dict': {'execution': 'BLOCKED'},
            'claude result': {'claude_result': {'executor_task_id': 'executor-1',
                                                'head_sha': RESULT_HEAD,
                                                'recorded_at': AT}},
            'quota progress': {'progress': {'state': 'QUOTA_EXHAUSTED', 'head_sha': 'UNKNOWN',
                                            'changed_files': [], 'unfinished_checks': [],
                                            'session_id': None}},
            'callback result head': {'direct_result_callback': {
                'state': 'WAITING_PM', 'evidence': {'result_head': RESULT_HEAD}}},
            'callback not a dict': {'direct_result_callback': 'WAITING_PM'},
        }
        for name, changes in cases.items():
            with self.subTest(case=name):
                self.assertEqual(
                    self.check(self.member(developer=start_failed_developer(**changes))),
                    'CONTINUES_LINEAGE_CONFLICT')
        record_cases = {
            'no developer': {'developer': None},
            'developer not a dict': {'developer': 'BLOCKED'},
            'completion result head': {'completion': {
                'executor_task_id': 'executor-1', 'evidence': {'result_head': RESULT_HEAD}}},
            'completion nested result head': {'completion': {
                'executor_task_id': 'executor-1',
                'evidence': {'source_sha': MAIN, 'result': {'result_head': RESULT_HEAD}}}},
            'completion result head equal to source': {'completion': {
                'executor_task_id': 'executor-1',
                'evidence': {'source_sha': MAIN, 'result_head': MAIN}}},
            'completion evidence not a dict': {'completion': {'evidence': 'done'}},
            'completion result not a dict': {'completion': {'evidence': {'result': 'done'}}},
            'completion not a dict': {'completion': 'done'},
        }
        for name, changes in record_cases.items():
            with self.subTest(case=name):
                self.assertEqual(self.check(self.member(**changes)),
                                 'CONTINUES_LINEAGE_CONFLICT')
        missing = self.member()
        del missing['developer']
        self.assertEqual(self.check(missing), 'CONTINUES_LINEAGE_CONFLICT')

    def test_other_checks_are_unchanged_for_a_start_failed_predecessor(self):
        member = self.member()
        successor = {'task_id': tid(4), 'continues': self.MEMBER}
        store = DictStore({'task_id': self.ROOT}, member, successor)
        self.assertEqual(refusal_code(lambda: lineage_counts.check_continues(
            store, self.NEW, self.MEMBER)), 'CONTINUES_PREDECESSOR_HAS_SUCCESSOR')
        store = DictStore({'task_id': self.ROOT}, member)
        self.assertEqual(refusal_code(lambda: lineage_counts.check_continues(
            store, self.MEMBER, self.MEMBER)), 'CONTINUES_SELF')
        cycled = self.member(continues=self.NEW)
        store = DictStore({'task_id': self.ROOT}, cycled, {'task_id': self.NEW})
        self.assertEqual(refusal_code(lambda: lineage_counts.check_continues(
            store, self.NEW, self.MEMBER)), 'CONTINUES_CYCLE')
        # A lineage of the wrong type is still a chain in doubt.
        bad = dict(member, lineage='replan')
        self.assertEqual(refusal_code(lambda: lineage_counts.check_continues(
            DictStore({'task_id': self.ROOT}, bad), self.NEW, self.MEMBER)),
            'CONTINUES_CHAIN_INVALID')

    def test_the_check_does_not_change_the_predecessor(self):
        member = self.member()
        store = DictStore({'task_id': self.ROOT}, member)
        before = copy.deepcopy(store.records)
        lineage_counts.check_continues(store, self.NEW, self.MEMBER)
        self.assertEqual(store.records, before)


class StoreStartFailureTests(unittest.TestCase):
    """Real TaskStore records built with task_fixtures.create_with_developer."""

    def setUp(self):
        worker_policy_fixture.install(self)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = TaskStore(Path(temp.name).resolve() / 'tasks')
        self.root = task_fixtures.create_with_developer(self.store, 'agy')['task_id']

    def lineage_member(self, kind, **developer_changes):
        """A Task whose AGY developer record carries developer_changes, then lineage kind.

        The developer is stored in one create-then-assign step
        (create_with_developer, expected={'developer': None}), as
        test_task_store.py stores an AGY BLOCKED or DEVELOPER_RESPONDED record.
        A second step from DEVELOPER_PENDING would be a dispatch transition the
        worker gate refuses outside the adapter.
        """
        task_id = task_fixtures.create_with_developer(
            self.store, 'agy', **developer_changes)['task_id']
        record_lineage(self.store, task_id, lineage(kind, self.root))
        return task_id

    def start_failed(self, kind='replan', **changes):
        return self.lineage_member(kind, state='BLOCKED', provider_type='agy-cli',
                                   error_code='AGY_START_FAILED', **changes)

    def check(self, predecessor):
        return refusal_code(lambda: lineage_counts.check_continues(
            self.store, new_id(), predecessor))

    def record_pm(self, continues):
        args = Namespace(stdin_json=True, pm_session_id=None, pm_runtime=None,
                         pm_cwd=None, continues=continues)
        return cli.run_task_record_pm(
            args, store=self.store, return_record=True, task_id=None,
            input_stream=io.StringIO(json.dumps({
                'request': 'Continue the REPLAN whose Developer never started.',
                'response': RESPONSE})))

    def test_start_failed_replan_predecessor_is_accepted(self):
        member = self.start_failed()
        record = self.store.get(member)
        self.assertEqual(record['developer']['state'], 'BLOCKED')
        self.assertIsNone(self.check(member))
        successor = self.record_pm(member)
        self.assertEqual(successor['continues'], member)
        # The successor inherits nothing new: no lineage, and counts stay chain-wide.
        self.assertNotIn('lineage', successor)
        counts = lineage_counts.lineage_counts(
            self.store, self.store.get(successor['task_id']), base_family='pr-17',
            include_successors=True)
        self.assertEqual(counts['chain'], [successor['task_id'], member])
        self.assertEqual((counts['review_repairs'], counts['test_fails']), (0, 0))
        # The predecessor now has a successor: a second continuation still refuses.
        self.assertEqual(self.check(member), 'CONTINUES_PREDECESSOR_HAS_SUCCESSOR')

    def test_replan_predecessor_with_a_session_is_refused(self):
        member = self.start_failed(execution=execution(native_session_id=SESSION))
        self.assertEqual(self.store.get(member)['developer']['execution']['native_session_id'],
                         SESSION)
        self.assertEqual(self.check(member), 'CONTINUES_LINEAGE_CONFLICT')
        before = self.store.task_ids()
        with self.assertRaises(LLMToolError) as caught:
            self.record_pm(member)
        self.assertEqual(getattr(caught.exception, 'continues_code', None),
                         'CONTINUES_LINEAGE_CONFLICT')
        self.assertEqual(self.store.task_ids(), before)

    def test_replan_predecessor_with_an_artifact_is_refused(self):
        member = self.lineage_member('replan', state='DEVELOPER_RESPONDED',
                                     provider_type='agy-cli', artifact=ARTIFACT,
                                     artifact_sha256=ARTIFACT_SHA)
        self.assertEqual(self.store.get(member)['developer']['artifact_sha256'], ARTIFACT_SHA)
        self.assertEqual(self.check(member), 'CONTINUES_LINEAGE_CONFLICT')

    def test_split_child_with_output_is_refused(self):
        member = self.lineage_member('child', state='DEVELOPER_RESPONDED',
                                     provider_type='agy-cli', artifact=ARTIFACT,
                                     artifact_sha256=ARTIFACT_SHA)
        self.assertEqual(self.check(member), 'CONTINUES_LINEAGE_CONFLICT')

    def test_pending_replan_predecessor_is_still_refused(self):
        task = task_fixtures.pm_responded_task(route='agy')
        self.store.create(task)
        record_lineage(self.store, task['task_id'], lineage('replan', self.root))
        self.assertEqual(self.check(task['task_id']), 'CONTINUES_LINEAGE_CONFLICT')
        pending = task_fixtures.create_with_developer(self.store, 'agy')['task_id']
        record_lineage(self.store, pending, lineage('replan', self.root))
        self.assertEqual(self.check(pending), 'CONTINUES_LINEAGE_CONFLICT')

    def test_a_root_predecessor_is_accepted_as_before(self):
        # A Task without lineage, its Developer still pending, may be continued.
        self.assertIsNone(self.check(self.root))


if __name__ == '__main__':
    unittest.main()
