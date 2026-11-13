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


if __name__ == '__main__':
    unittest.main()