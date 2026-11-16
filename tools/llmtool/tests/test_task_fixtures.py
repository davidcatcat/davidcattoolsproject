"""Tests for the shared developer Task fixtures in ``task_fixtures`` (#1502)."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from llmtool.task_store import TaskStore, TaskStoreConflict

import review_trust_fixture
import task_fixtures
import worker_policy_fixture


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


class TaskFixturesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'tasks'
        self.store = TaskStore(self.root)
        # #1692: the shared all-ALLOWED test policy, never the live file.
        self.policy_path = worker_policy_fixture.install(self)

    def test_pm_responded_task_has_no_developer(self):
        task = task_fixtures.pm_responded_task()
        self.assertIsNone(task['developer'])
        self.assertEqual(task['state'], 'PM_RESPONDED')
        self.assertEqual(task['pm_assignment']['route'], 'codex')

    def test_create_with_developer_round_trips_for_every_provider(self):
        self.assertEqual(set(task_fixtures.PROVIDERS),
                         {'codex', 'claude', 'agy', 'opencode', 'gw-agent'})
        for provider in task_fixtures.PROVIDERS:
            with self.subTest(provider=provider):
                saved = task_fixtures.create_with_developer(self.store, provider)
                developer = saved['developer']
                self.assertEqual(developer['provider_profile'], provider)
                self.assertEqual(developer['state'], 'DEVELOPER_PENDING')
                self.assertEqual(saved['pm_assignment']['route'], provider)
                stored = self.store.get(saved['task_id'])
                self.assertEqual(stored['record_sha256'], saved['record_sha256'])
                self.assertEqual(stored['developer'], developer)
                self.assertEqual(stored['pm_assignment'], saved['pm_assignment'])

    def test_create_with_developer_applies_developer_changes(self):
        saved = task_fixtures.create_with_developer(
            self.store, 'codex', timeout_seconds=600)
        self.assertEqual(saved['developer']['timeout_seconds'], 600)
        self.assertEqual(
            self.store.get(saved['task_id'])['developer']['timeout_seconds'], 600)

    def test_developer_assignment_rejects_an_unknown_provider(self):
        with self.assertRaises(ValueError):
            task_fixtures.developer_assignment('company-gw')

    def test_create_with_developer_stores_the_assignment_and_refuses_a_second(self):
        # Capture the exact record developer_assignment returns inside the fixture.
        returned = []

        def recording_assignment(*args, **kwargs):
            developer = real_assignment(*args, **kwargs)
            returned.append(copy.deepcopy(developer))
            return developer

        real_assignment = task_fixtures.developer_assignment
        with mock.patch.object(task_fixtures, 'developer_assignment',
                               side_effect=recording_assignment):
            saved = task_fixtures.create_with_developer(self.store, 'codex')
        self.assertEqual(len(returned), 1)
        self.assertEqual(saved['developer'], returned[0])
        stored = self.store.get(saved['task_id'])
        self.assertEqual(stored['developer'], returned[0])

        # The create-then-assign precondition: a second developer is refused
        # and the stored record is unchanged.
        with self.assertRaises(TaskStoreConflict):
            self.store.compare_and_update(
                saved['task_id'], expected={'developer': None},
                developer=task_fixtures.developer_assignment('codex'))
        after = self.store.get(saved['task_id'])
        self.assertEqual(after['developer'], returned[0])
        self.assertEqual(after['record_sha256'], stored['record_sha256'])


if __name__ == '__main__':
    unittest.main()
