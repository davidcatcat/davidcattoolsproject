"""#1502 WP-D D10: task split, replan and drop through the D3 lineage gates."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from llmtool import task_submission_cli as cli
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore, new_id, record_lineage

MAIN = 'e' * 40
PM = 'pm-lineage-test'
AT = '2026-11-13T00:00:00Z'


class LineageCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = TaskStore(Path(temp.name).resolve() / 'tasks')

    def task(self, session=PM):
        request = 'Bounded lineage test Task.'
        task = cli._record(new_id(), new_id(), session, 'pm-profile', request,
                           state='PM_RESPONDED', provider_type='openai-compatible',
                           model='pm-model', backend='NO_NATIVE_SESSION_ID_EXPOSED',
                           request_sha=hashlib.sha256(request.encode()).hexdigest())
        task.update(pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                                      'base_url': None}, response='PM reply.')
        self.store.create(task)
        return task['task_id']

    def member(self, root, kind='child', replaces=()):
        task_id = self.task()
        record_lineage(self.store, task_id, {
            'lineage_id': root, 'kind': kind, 'parent': root, 'replaces': list(replaces),
            'state': 'OPEN', 'reason': None, 'base_sha': MAIN, 'recorded_at': AT})
        return task_id

    def parked_root(self):
        root = self.task()
        record_lineage(self.store, root, {
            'lineage_id': root, 'kind': 'root', 'parent': None, 'replaces': [],
            'state': 'PARKED', 'reason': 'split', 'base_sha': None, 'recorded_at': AT})
        return root

    def refused(self, code, call, *args, **kwargs):
        with self.assertRaises(LLMToolError) as caught:
            call(self.store, *args, **kwargs)
        self.assertEqual(getattr(caught.exception, 'task_store_error_code', None) or
                         str(caught.exception), code)

    def state(self, task_id):
        return self.store.get(task_id)['lineage']['state']


class LineageReadTests(LineageCase):
    def test_a_task_without_lineage_is_its_own_open_root(self):
        plain = self.task()
        line = cli._lineage_of(self.store.get(plain))
        self.assertEqual((line['lineage_id'], line['kind'], line['state'], line['replaces']),
                         (plain, 'root', 'OPEN', []))
        self.refused('LINEAGE_X', lambda _store: cli._lineage_refuse('LINEAGE_X'))
        # A present but malformed lineage fails closed instead of reading as a ROOT.
        for bad in ('x', [], {'kind': 'root'}):
            self.refused('TASK_LINEAGE_INVALID',
                         lambda _store, bad=bad: cli._lineage_of({'task_id': plain, 'lineage': bad}))


class LineageNoneTests(LineageCase):
    def test_an_explicit_none_lineage_fails_closed(self):
        # PR #1698 security Low: a present None lineage is validated, not read as a ROOT.
        plain = self.task()
        self.refused('TASK_LINEAGE_INVALID',
                     lambda _store: cli._lineage_of({'task_id': plain, 'lineage': None}))
        self.assertEqual(cli._lineage_of({'task_id': plain})['kind'], 'root')


class LineageMemberTests(LineageCase):
    def test_lineage_tasks_belong_to_their_pm_and_members_are_read_from_the_store(self):
        root = self.parked_root()
        child, plain = self.member(root), self.task()
        self.assertEqual(cli._lineage_task(self.store, child, PM)['task_id'], child)
        self.refused('LINEAGE_PM_SESSION_DENIED', cli._lineage_task, child, 'pm-other')
        self.assertEqual([item['id'] for item in cli._lineage_members(self.store, root)],
                         [child])
        self.assertEqual(cli._lineage_members(self.store, plain), [])


class LineageNoticeTests(LineageCase):
    def test_owner_notice_only_for_failed_end_states_and_reasons_are_screened(self):
        notice = cli.lineage_owner_notice('a' * 32, 'DROPPED', 'not needed')
        self.assertTrue(notice['body'].startswith(cli.OWNER_NOTICE_MARKER + '\n'))
        self.assertEqual((notice['state'], notice['marker']),
                         ('DROPPED', cli.OWNER_NOTICE_MARKER))
        for state in ('CLOSED', 'OPEN', 'PARKED'):
            self.assertIsNone(cli.lineage_owner_notice('a' * 32, state, 'merged'))
        for bad in ('', 'x' * 501, 'line\nbreak', None):
            self.refused('LINEAGE_REASON_INVALID',
                         lambda _store, value: cli.lineage_owner_notice('a' * 32, 'STOPPED', value), bad)
        self.assertEqual(cli._lineage_reason('Original function unaffected.', 'R'),
                         'Original function unaffected.')
        for reason in ('', 'x' * 501, 'line\nbreak', None):
            self.refused('R', lambda _store, value: cli._lineage_reason(value, 'R'), reason)


class LineageRollUpTests(LineageCase):
    def end(self, task_id, state):
        line = self.store.get(task_id)['lineage']
        record_lineage(self.store, task_id, dict(line, state=state, reason='ended'))

    def test_a_parked_root_rolls_up_once_and_never_notifies(self):
        root = self.parked_root()
        child = self.member(root)
        replan = self.member(root, kind='replan', replaces=[child])
        self.assertIsNone(cli._lineage_roll_up(self.store, root))
        self.end(child, 'STOPPED')
        self.assertIsNone(cli._lineage_roll_up(self.store, root))
        self.end(replan, 'CLOSED')
        self.assertEqual(cli._lineage_roll_up(self.store, root),
                         {'task_id': root, 'state': 'CLOSED', 'owner_notice': None})
        self.assertEqual(self.state(root), 'CLOSED')
        self.assertIsNone(cli._lineage_roll_up(self.store, root))
        other = self.parked_root()
        self.end(self.member(other), 'DROPPED')
        self.assertEqual(cli._lineage_roll_up(self.store, other)['state'], 'FAILED_FINAL')
        self.assertIsNone(cli._lineage_roll_up(self.store, self.task()))


class LineageEndTests(LineageCase):
    def test_drop_needs_a_reason_and_a_child_and_notifies_the_owner(self):
        root = self.parked_root()
        first, second = self.member(root), self.member(root)
        self.refused('DROP_REASON_REQUIRED', cli.lineage_end, first, 'DROPPED',
                     pm_session=PM, reason=' ')
        self.refused('DROP_NOT_CHILD', cli.lineage_end, root, 'DROPPED',
                     pm_session=PM, reason='not needed')
        self.refused('LINEAGE_STATE_INVALID', cli.lineage_end, first, 'CLOSED',
                     pm_session=PM, reason='not needed')
        result = cli.lineage_end(self.store, first, 'DROPPED', pm_session=PM,
                                 reason='Original function is not affected.')
        self.assertEqual(self.state(first), 'DROPPED')
        self.assertEqual(result['owner_notice']['marker'], cli.OWNER_NOTICE_MARKER)
        self.assertIsNone(result['root'])
        self.assertEqual(self.state(root), 'PARKED')
        self.refused('LINEAGE_NOT_OPEN', cli.lineage_end, first, 'STOPPED',
                     pm_session=PM, reason='again')
        self.assertEqual(self.state(second), 'OPEN')


def merged(store, task_id):
    """Record a merge claim and its MERGED outcome, as task advance does."""
    claim = {'pr': 17, 'head_sha': 'a' * 40, 'state': 'CLAIMED',
             'claimed_at': '2026-11-13T00:00:00.000000Z'}
    store.update(task_id, merge_attempt=claim)
    store.update(task_id, merge_attempt=dict(claim, state='MERGED'))


class LineageMergedTests(LineageCase):
    def test_merged_tasks_close_without_notice_and_close_the_root(self):
        root = self.parked_root()
        first, second = self.member(root), self.member(root)
        self.assertIsNone(cli.lineage_close_merged(self.store, first))
        self.assertIsNone(cli.lineage_owner_notice(first, 'CLOSED', 'merged'))
        merged(self.store, first)
        self.assertEqual(cli.lineage_close_merged(self.store, first),
                         {'task_id': first, 'state': 'CLOSED', 'owner_notice': None,
                          'root': None})
        merged(self.store, second)
        closed = cli.lineage_close_merged(self.store, second)
        self.assertEqual(closed['root'], {'task_id': root, 'state': 'CLOSED',
                                          'owner_notice': None})
        self.assertEqual(self.state(root), 'CLOSED')
        self.assertIsNone(cli.lineage_close_merged(self.store, second))
        plain = self.task()
        merged(self.store, plain)
        self.assertIsNone(cli.lineage_close_merged(self.store, plain))


if __name__ == '__main__':
    unittest.main()