"""#1502 WP-D: used AI Debugs counted over a Task's continuation chain."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool import high_debug, lineage_counts, repair_policy
from llmtool import task_submission_cli as cli
from llmtool.errors import LLMToolError
from llmtool.repair_policy import UNKNOWN
from llmtool.task_store import TaskStore, new_id

import review_trust_fixture

PARENT_USED = 'HIGH_DEBUG_DEBUG_PARENT_USED'


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def entry(status):
    return dict(high_debug._failed_entry(
        family='family', failed_corrections=2, early_reason=None, head='a' * 40,
        d6='executor-hd1-aaaaaaa', code='HIGH_DEBUG_RUN_FAILED', observed='UNKNOWN',
        effort='UNKNOWN', usage='UNKNOWN', recorded_at='2026-11-14T00:00:00Z'),
        status=status)


class ChainDebugUsedTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = TaskStore(Path(temp.name).resolve() / 'tasks')

    def task(self, continues=None):
        request = 'Bounded Debug chain test Task.'
        task = cli._record(new_id(), new_id(), 'pm-debug-chain-test', 'pm-profile', request,
                           state='PM_RESPONDED', provider_type='openai-compatible',
                           model='pm-model', backend='NO_NATIVE_SESSION_ID_EXPOSED',
                           request_sha=hashlib.sha256(request.encode()).hexdigest())
        task.update(pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                                      'base_url': None}, response='PM reply.')
        if continues is not None:
            task['continues'] = continues
        self.store.create(task)
        return task['task_id']

    def debug(self, task_id, status='RCA_CONFIRMED'):
        self.store.append_high_debug(task_id, entry(status))

    def used(self, task):
        return lineage_counts.chain_debug_used(
            self.store, task, task.get('high_debug', []), high_debug._DEBUG_USED_STATUSES)

    def admit(self, task):
        try:
            high_debug._admit_debug(self.store, task, task.get('high_debug', []))
        except LLMToolError as error:
            return error.high_debug_code
        return None

    def check(self, task_id, used, code):
        task = self.store.get(task_id)
        self.assertEqual((self.used(task), self.admit(task)), (used, code))

    def test_unlinked_task_counts_as_today(self):
        alone = self.task()
        self.check(alone, (0, 0), None)
        self.debug(alone, 'RUN_FAILED')
        self.check(alone, (0, 0), None)
        self.debug(alone)
        self.check(alone, (1, 1), PARENT_USED)

    def test_parent_debug_on_the_predecessor_refuses(self):
        first = self.task()
        second = self.task(continues=first)
        self.check(second, (0, 0), None)
        self.debug(first)
        self.check(second, (1, 1), PARENT_USED)

    def test_successor_debug_is_counted(self):
        first = self.task()
        second = self.task(continues=first)
        self.debug(second, 'REPORT_INVALID')
        self.check(first, (1, 1), PARENT_USED)

    def test_group_count_covers_the_whole_chain(self):
        first = self.task()
        middle = self.task(continues=first)
        last = self.task(continues=middle)
        self.debug(first)
        self.debug(middle, 'RUN_FAILED')
        self.debug(last, 'BLOCKED')
        task = self.store.get(middle)
        self.assertEqual(self.used(task), (2, 2))
        self.assertEqual(repair_policy.admit_debug(
            kind='child', group_debug_used=self.used(task)[1],
            policy=repair_policy.load_policy()), 'DEBUG_GROUP_USED')

    def test_chain_in_doubt_is_unknown_and_refuses_as_used(self):
        first = self.task()
        second = self.task(continues=first)
        task = self.store.get(second)
        real_get = self.store.get

        def unreadable(task_id):
            if task_id == first:
                raise OSError('unreadable')
            return real_get(task_id)

        def malformed(task_id):
            record = real_get(task_id)
            return dict(record, high_debug='bad') if task_id == first else record
        cases = {'unreadable predecessor': ('get', unreadable),
                 'malformed predecessor entries': ('get', malformed),
                 'store scan fails': ('task_ids', OSError('scan'))}
        for name, (method, effect) in cases.items():
            with self.subTest(name), patch.object(self.store, method, side_effect=effect):
                self.assertEqual((self.used(task), self.admit(task)),
                                 ((UNKNOWN, UNKNOWN), PARENT_USED))


if __name__ == '__main__':
    unittest.main()
