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

if __name__ == '__main__':
    unittest.main()