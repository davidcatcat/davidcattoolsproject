"""#2622 PR 1b: gw-agent lowest-tier pause threshold from policy.json.

Pins A1-A3 against the real worker availability record on a temporary TaskStore:
- A1: gw-agent pauses after repair_policy.route_failure_pause_after() consecutive
  model failures at the lowest tier (3 with repository policy.json or when the
  key is omitted; configured positive integers are honoured).
- A2: a gw-agent pause stored before this change (_MODEL_STEP_TWO + FAIL) still
  reads as paused and stays paused on probes, successes and further failures.
- A3: an invalid policy raises POLICY_INVALID without falling back to a default.
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, Path(__file__).resolve().parent):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from llmtool import repair_policy  # noqa: E402
from llmtool import worker_availability as wa  # noqa: E402
from llmtool import worker_dispatch  # noqa: E402
from llmtool.errors import LLMToolError  # noqa: E402
from llmtool.task_store import TaskStore  # noqa: E402

import review_trust_fixture  # noqa: E402
import worker_policy_fixture  # noqa: E402


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


def _base_policy():
    return {
        'schema_version': 1,
        'repair_max': 5,
        'parent_debug_max': 3,
        'group_debug_max': 3,
        'child_max': 3,
        'replan_max': 1,
        'test_fail_max': 5,
        'review_fail_max': 6,
        'waiting_notify_after_hours': 24,
    }


class GwAgentPausePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'tasks'
        worker_policy_fixture.install(self)
        self.store = TaskStore(self.root)
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.clock = self.now
        test = self

        class _Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return test.clock if tz is None else test.clock.astimezone(tz)

        for module in ('llmtool.task_store', 'llmtool.worker_availability'):
            clock = patch(module + '.datetime', _Clock)
            clock.start()
            self.addCleanup(clock.stop)

        allowed = worker_dispatch.PolicyDecision(
            'gw-agent', True, 'OWNER_POLICY_ALLOWED', '0' * 64)
        policy = patch.object(worker_dispatch, 'evaluate', return_value=allowed)
        policy.start()
        self.addCleanup(policy.stop)

    def at(self, delta=timedelta(0)):
        self.clock = self.now + delta
        return self.clock

    def sidecar(self):
        return self.root / '.worker-availability.json'

    def write_policy(self, data):
        path = Path(self.temp.name).resolve() / 'policy.json'
        path.write_text(json.dumps(data), encoding='utf-8')
        return path

    def test_repository_policy_pauses_after_three_consecutive_failures(self):
        expected = repair_policy.route_failure_pause_after()
        self.assertEqual(expected, 3)

        for step in range(1, expected):
            result = wa.record_gw_agent_failure(
                self.store, 'model', now=self.at(timedelta(minutes=step - 1)),
                task_id=f'{step:032x}')
            self.assertEqual(result['action'], 'STEP_DOWN')
            self.assertEqual(result['count'], step)
            self.assertIsNone(wa.gw_agent_refusal(
                self.store, now=self.at(timedelta(minutes=step - 1))))

        paused = wa.record_gw_agent_failure(
            self.store, 'model', now=self.at(timedelta(minutes=expected - 1)),
            task_id=f'{expected:032x}')
        self.assertEqual(paused['action'], 'PAUSE')
        self.assertEqual(paused['count'], expected)
        self.assertEqual(
            wa.gw_agent_refusal(self.store, now=self.at(timedelta(minutes=expected - 1))),
            'WORKER_GW_AGENT_MODEL_PAUSED')

        called = []
        report = wa.probe_gw_agent(
            self.store, now=self.at(timedelta(minutes=expected)),
            call=lambda: called.append('provider'))
        self.assertFalse(report['provider_call'])
        self.assertEqual(report['reason'], 'WORKER_GW_AGENT_MODEL_PAUSED')
        self.assertEqual(called, [])

    def test_omitted_key_defaults_to_three_consecutive_failures(self):
        policy_file = self.write_policy(_base_policy())
        with patch.object(repair_policy, 'policy_path', return_value=policy_file):
            for step in (1, 2):
                result = wa.record_gw_agent_failure(
                    self.store, 'model', now=self.at(timedelta(minutes=step)),
                    task_id=f'{step:032x}')
                self.assertEqual(result['action'], 'STEP_DOWN')
                self.assertIsNone(wa.gw_agent_refusal(
                    self.store, now=self.at(timedelta(minutes=step))))
            paused = wa.record_gw_agent_failure(
                self.store, 'model', now=self.at(timedelta(minutes=3)),
                task_id=f'{3:032x}')
            self.assertEqual(paused['action'], 'PAUSE')
            self.assertEqual(
                wa.gw_agent_refusal(self.store, now=self.at(timedelta(minutes=3))),
                'WORKER_GW_AGENT_MODEL_PAUSED')

    def test_configured_policy_threshold_is_honoured(self):
        for threshold in (1, 4):
            with self.subTest(threshold=threshold):
                temp_dir = tempfile.TemporaryDirectory()
                self.addCleanup(temp_dir.cleanup)
                store = TaskStore(Path(temp_dir.name).resolve() / 'tasks')
                data = _base_policy()
                data['route_failure_pause_after'] = threshold
                policy_file = Path(temp_dir.name).resolve() / 'policy.json'
                policy_file.write_text(json.dumps(data), encoding='utf-8')
                with patch.object(repair_policy, 'policy_path', return_value=policy_file):
                    for step in range(1, threshold):
                        result = wa.record_gw_agent_failure(
                            store, 'model', now=self.at(timedelta(minutes=step)),
                            task_id=f'{step:032x}')
                        self.assertEqual(result['action'], 'STEP_DOWN')
                        self.assertIsNone(wa.gw_agent_refusal(
                            store, now=self.at(timedelta(minutes=step))))
                    paused = wa.record_gw_agent_failure(
                        store, 'model', now=self.at(timedelta(minutes=threshold)),
                        task_id=f'{threshold:032x}')
                    self.assertEqual(paused['action'], 'PAUSE')
                    self.assertEqual(
                        wa.gw_agent_refusal(store, now=self.at(timedelta(minutes=threshold))),
                        'WORKER_GW_AGENT_MODEL_PAUSED')

    def test_previously_stored_gw_agent_pause_still_reads_as_paused(self):
        observed = self.at()
        fresh_until = observed + wa.GW_AGENT_HOLD_FRESH_FOR
        self.store.record_worker_observation(
            worker_key=wa.GW_AGENT_WORKER,
            layer=wa.GW_AGENT_MODEL_LAYER,
            status='FAIL',
            observed_at=wa._stamp(observed),
            fresh_until=wa._stamp(fresh_until),
            source='LOCAL_OBSERVATION',
            evidence_sha256=wa._MODEL_STEP_TWO,
        )

        self.assertEqual(
            wa.gw_agent_refusal(self.store, now=self.at(timedelta(minutes=1))),
            'WORKER_GW_AGENT_MODEL_PAUSED')

        called = []
        report = wa.probe_gw_agent(
            self.store, now=self.at(timedelta(minutes=2)),
            call=lambda: called.append('provider'))
        self.assertFalse(report['provider_call'])
        self.assertEqual(report['reason'], 'WORKER_GW_AGENT_MODEL_PAUSED')
        self.assertEqual(called, [])

        wa.record_gw_agent_success(self.store, now=self.at(timedelta(minutes=3)))
        self.assertEqual(
            wa.gw_agent_refusal(self.store, now=self.at(timedelta(minutes=3))),
            'WORKER_GW_AGENT_MODEL_PAUSED')

        again = wa.record_gw_agent_failure(
            self.store, 'model', now=self.at(timedelta(minutes=4)),
            task_id='b' * 32)
        self.assertEqual(again['action'], 'PAUSE')
        self.assertEqual(
            wa.gw_agent_refusal(self.store, now=self.at(timedelta(minutes=4))),
            'WORKER_GW_AGENT_MODEL_PAUSED')

    def test_invalid_policy_raises_policy_invalid_without_fallback(self):
        bad_cases = (
            dict(_base_policy(), route_failure_pause_after=0),
            dict(_base_policy(), route_failure_pause_after='3'),
        )
        for index, bad_data in enumerate(bad_cases):
            with self.subTest(index=index):
                policy_file = self.write_policy(bad_data)
                with patch.object(repair_policy, 'policy_path', return_value=policy_file):
                    with self.assertRaises(LLMToolError) as caught:
                        wa.record_gw_agent_failure(
                            self.store, 'model', now=self.at(), task_id='c' * 32)
                    self.assertEqual(str(caught.exception), 'POLICY_INVALID')
                self.assertFalse(self.sidecar().exists())


if __name__ == '__main__':
    unittest.main()
