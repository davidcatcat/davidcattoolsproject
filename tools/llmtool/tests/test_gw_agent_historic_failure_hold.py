"""gw-agent hold scan: a later successful run resolves an older stored failure.

Regression from #2614: failures recorded before the durable S2 scan existed
(already handled at the time) held gw-agent dispatch for MAX_RESET_WINDOW with
WORKER_GW_AGENT_HOLD_UNRECORDED even though later runs had succeeded.
"""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from llmtool.task_store import TaskStore
from llmtool import worker_availability as wa

import review_trust_fixture
import worker_policy_fixture


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


class ScanStore:
    """Real TaskStore observations plus a fixed list of Task developer records."""

    def __init__(self, store, developers):
        self.store = store
        self.root = store.root
        self.records = {f'{index:032x}': {'developer': developer}
                        for index, developer in enumerate(developers)}

    def get_worker_observations(self):
        return self.store.get_worker_observations()

    def record_worker_observation(self, **observation):
        return self.store.record_worker_observation(**observation)

    def task_ids(self):
        return list(self.records)

    def get(self, task_id):
        return self.records[task_id]


def developer(state, error_code, created_at):
    return {'provider_profile': 'gw-agent', 'state': state,
            'error_code': error_code, 'created_at': wa._stamp(created_at)}


class HistoricFailureHoldTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        worker_policy_fixture.install(self)
        self.store = TaskStore(Path(temp.name).resolve() / 'tasks')
        self.now = datetime.now(timezone.utc).replace(microsecond=0)

    def refusal(self, *developers):
        return wa.gw_agent_refusal(ScanStore(self.store, developers), now=self.now)

    def test_older_failures_followed_by_a_success_do_not_hold(self):
        self.assertIsNone(self.refusal(
            developer('DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_MODEL_ERROR_TIMEOUT',
                      self.now - timedelta(days=5)),
            developer('DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_CREDENTIAL_MISSING',
                      self.now - timedelta(days=4)),
            developer('DEVELOPER_UNAVAILABLE',
                      'DEVELOPER_GW_AGENT_BLOCKED_MODEL_REPORTED_BLOCKED',
                      self.now - timedelta(days=3)),
            developer('DEVELOPER_RESPONDED', None, self.now - timedelta(hours=2))))

    def test_failure_after_the_latest_success_still_holds(self):
        self.assertEqual(self.refusal(
            developer('DEVELOPER_RESPONDED', None, self.now - timedelta(hours=3)),
            developer('DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_CREDENTIAL_MISSING',
                      self.now - timedelta(hours=1))),
            'WORKER_GW_AGENT_HOLD_UNRECORDED')

    def test_failure_with_no_success_at_all_still_holds(self):
        self.assertEqual(self.refusal(
            developer('DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_CREDENTIAL_MISSING',
                      self.now - timedelta(hours=1))),
            'WORKER_GW_AGENT_HOLD_UNRECORDED')

    def test_a_responded_run_with_an_error_code_is_not_a_success(self):
        self.assertEqual(self.refusal(
            developer('DEVELOPER_UNAVAILABLE', 'DEVELOPER_GW_AGENT_CREDENTIAL_MISSING',
                      self.now - timedelta(hours=2)),
            developer('DEVELOPER_RESPONDED', 'DEVELOPER_GW_AGENT_CREDENTIAL_MISSING',
                      self.now - timedelta(hours=1))),
            'WORKER_GW_AGENT_HOLD_UNRECORDED')


if __name__ == '__main__':
    unittest.main()
