import errno
import hashlib
import json
import os
import stat
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore
from llmtool import worker_availability as wa
from llmtool import worker_dispatch
from test_task_store import record as make_task_record


def stamp(value):
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime(
        '%Y-%m-%dT%H:%M:%SZ')


import review_trust_fixture
import worker_policy_fixture


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


class WorkerAvailabilityStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'tasks'
        # #1692: the shared test policy, never the live worker-dispatch.json.
        self.policy_path = worker_policy_fixture.install(self)
        self.store = TaskStore(self.root)

    def _observation(self, *, worker='codex-developer', layer='runtime',
                     status='PASS', observed=None, fresh_until=None, source='LOCAL_OBSERVATION',
                     evidence=None):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        observed = observed or now - timedelta(seconds=1)
        fresh_until = fresh_until or now + timedelta(hours=1)
        evidence = evidence or hashlib.sha256(b'bounded local evidence').hexdigest()
        return self.store.record_worker_observation(
            worker_key=worker, layer=layer, status=status,
            observed_at=stamp(observed), fresh_until=stamp(fresh_until),
            source=source, evidence_sha256=evidence)

    def test_missing_observations_are_unknown_and_reopen_persists(self):
        view = self.store.get_worker_observations()
        self.assertEqual(view['codex-developer']['runtime']['status'], 'UNKNOWN')
        self.assertIsNone(view['codex-developer']['runtime']['observed_at'])

        saved = self._observation()
        self.assertEqual(saved['codex-developer']['runtime']['status'], 'PASS')
        reopened = TaskStore(self.root).get_worker_observations()
        self.assertEqual(reopened['codex-developer']['runtime']['status'], 'PASS')
        self.assertEqual(reopened['codex-developer']['runtime']['source'],
                         'LOCAL_OBSERVATION')
        self.assertEqual(reopened['codex-developer']['endpoint']['status'], 'UNKNOWN')
        self.assertEqual(reopened['codex-developer']['authentication']['status'],
                         'UNKNOWN')

    def test_expired_observation_reads_unknown_without_erasing_evidence(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self._observation(observed=now - timedelta(days=2),
                          fresh_until=now - timedelta(days=1))
        entry = TaskStore(self.root).get_worker_observations()['codex-developer']['runtime']
        self.assertEqual(entry['status'], 'UNKNOWN')
        self.assertEqual(entry['source'], 'LOCAL_OBSERVATION')
        self.assertRegex(entry['evidence_sha256'], r'^[0-9a-f]{64}$')

    def test_replacement_is_bounded_and_unknown_never_authorizes_policy(self):
        first = self._observation(status='FAIL')
        second = self._observation(status='PASS')
        self.assertEqual(second['codex-developer']['runtime']['status'], 'PASS')
        self.assertEqual(len(list(self.root.glob('[0-9a-f]' * 32 + '.json'))), 0)
        self.assertTrue((self.root / '.worker-availability.json').is_file())
        self.assertEqual(first['opencode-developer']['runtime']['status'], 'UNKNOWN')

    def test_observations_do_not_change_dispatch_policy_or_task_assignment(self):
        task = self.store.create(make_task_record())
        self.assertEqual(Path(worker_dispatch._POLICY_PATH), self.policy_path)
        before_policy = self.policy_path.read_bytes()
        before_task = self.store.get(task['task_id'])
        self._observation(worker='opencode-developer', status='PASS')
        self.assertEqual(self.policy_path.read_bytes(), before_policy)
        self.assertEqual(self.store.get(task['task_id']), before_task)

    def test_invalid_worker_layer_status_source_digest_and_time_rejected(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        valid = dict(worker_key='codex-developer', layer='runtime', status='PASS',
                     observed_at=stamp(now - timedelta(seconds=1)),
                     fresh_until=stamp(now + timedelta(hours=1)),
                     source='LOCAL_OBSERVATION', evidence_sha256='a' * 64)
        invalid_cases = (
            dict(valid, worker_key='unknown-worker'),
            dict(valid, layer='overall'),
            dict(valid, status='READY'),
            dict(valid, source='PROVIDER_PROBE'),
            dict(valid, evidence_sha256='not-a-digest'),
            dict(valid, observed_at=stamp(now + timedelta(minutes=1))),
            dict(valid, observed_at='2026-10-24T01:02:03+00:00'),
            dict(valid, fresh_until=stamp(now - timedelta(seconds=2))),
            dict(valid, fresh_until=stamp(now + timedelta(days=31))),
            # #2622: the route-failure fields belong only, and in pairs, to the
            # AGY and opencode-developer runtime layer.
            dict(valid, worker_key='agy', route_failure_count=1),
            dict(valid, worker_key='agy', route_paused=False),
            dict(valid, route_failure_count=1, route_paused=False),
            dict(valid, worker_key='agy', layer='quota',
                 route_failure_count=1, route_paused=False),
            dict(valid, worker_key='agy', route_failure_count=True,
                 route_paused=False),
            dict(valid, worker_key='agy', route_failure_count=-1,
                 route_paused=False),
            dict(valid, worker_key='agy', route_failure_count=1, route_paused=1),
            dict(valid, worker_key='agy', route_failure_count=0, route_paused=True),
            dict(valid, worker_key='agy', route_failure_count=10001,
                 route_paused=False),
        )
        for item in invalid_cases:
            with self.subTest(item=item):
                with self.assertRaises(LLMToolError):
                    self.store.record_worker_observation(**item)

    def test_route_failure_count_and_pause_fields_round_trip(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        common = dict(layer='runtime', status='FAIL',
                      observed_at=stamp(now - timedelta(seconds=1)),
                      fresh_until=stamp(now + timedelta(hours=1)),
                      source='LOCAL_OBSERVATION', evidence_sha256='b' * 64)
        saved = self.store.record_worker_observation(
            worker_key='agy', route_failure_count=2, route_paused=False, **common)
        entry = saved['agy']['runtime']
        self.assertEqual((entry['route_failure_count'], entry['route_paused']),
                         (2, False))
        reopened = TaskStore(self.root).get_worker_observations()['agy']['runtime']
        self.assertEqual((reopened['route_failure_count'], reopened['route_paused']),
                         (2, False))

        paused = self.store.record_worker_observation(
            worker_key='agy', route_failure_count=3, route_paused=True, **common)
        self.assertEqual((paused['agy']['runtime']['route_failure_count'],
                          paused['agy']['runtime']['route_paused']), (3, True))
        # The fields belong only to the route runtime entry.
        self.assertNotIn('route_failure_count', saved['codex-developer']['runtime'])
        self.assertNotIn('route_paused', saved['agy']['quota'])

    def test_sidecar_rejects_symlink_wrong_mode_and_corrupt_digest(self):
        self._observation()
        path = self.root / '.worker-availability.json'
        original = path.read_bytes()
        path.write_bytes(original[:-2] + b'!\n')
        with self.assertRaises(LLMToolError):
            self.store.get_worker_observations()
        path.write_bytes(original)
        path.chmod(0o644)
        with self.assertRaises(LLMToolError):
            self.store.get_worker_observations()
        path.chmod(0o600)
        target = self.root / 'target'
        target.write_text('{}', encoding='utf-8')
        path.unlink()
        path.symlink_to(target)
        with self.assertRaises(LLMToolError):
            self.store.get_worker_observations()

    def test_malformed_sidecar_closes_descriptor_before_rejecting(self):
        self._observation()
        path = self.root / '.worker-availability.json'
        path.chmod(0o644)
        real_open = os.open
        opened = []

        def tracking_open(name, *args, **kwargs):
            fd = real_open(name, *args, **kwargs)
            if name == '.worker-availability.json':
                opened.append(fd)
            return fd

        with patch('llmtool.task_store.os.open', side_effect=tracking_open):
            with self.assertRaises(LLMToolError):
                self.store.get_worker_observations()
        self.assertEqual(len(opened), 1)
        with self.assertRaises(OSError):
            os.fstat(opened[0])

    def test_lock_failure_closes_descriptor(self):
        real_open = os.open
        opened = []

        def tracking_open(name, *args, **kwargs):
            fd = real_open(name, *args, **kwargs)
            if name == '.worker-availability.lock':
                opened.append(fd)
            return fd

        self._observation()  # #1837 P2: a read takes the lock only once a write made it
        with patch('llmtool.task_store.os.open', side_effect=tracking_open):
            with patch('llmtool.task_store.fcntl.flock', side_effect=OSError('synthetic')):
                with self.assertRaises(LLMToolError):
                    self.store.get_worker_observations()
        self.assertEqual(len(opened), 1)
        with self.assertRaises(OSError):
            os.fstat(opened[0])

    def test_concurrent_lock_create_enoent_is_retried(self):
        original_open = os.open
        attempts = []

        def lose_first_create(name, flags, *args, **kwargs):
            if name == '.worker-availability.lock':
                attempts.append(flags)
                if len(attempts) == 1:
                    raise FileNotFoundError(errno.ENOENT, 'private filesystem detail')
            return original_open(name, flags, *args, **kwargs)

        with patch('llmtool.task_store.os.open', side_effect=lose_first_create):
            saved = self._observation()
        self.assertEqual(saved['codex-developer']['runtime']['status'], 'PASS')
        self.assertEqual(len(attempts), 2)
        self.assertTrue(all(flags & os.O_CREAT and flags & os.O_NOFOLLOW
                            for flags in attempts))

    def test_persistent_lock_create_enoent_is_bounded_and_unavailable(self):
        original_open = os.open
        attempts = []

        def always_enoent(name, flags, *args, **kwargs):
            if name == '.worker-availability.lock':
                attempts.append(name)
                raise FileNotFoundError(errno.ENOENT, 'private filesystem detail')
            return original_open(name, flags, *args, **kwargs)

        clock = [0.0]

        def monotonic():
            return clock[0]

        def advance_sleep(seconds):
            clock[0] += seconds

        with patch('llmtool.task_store.TASK_UPDATE_LOCK_TIMEOUT_SECONDS', 0.1), \
                patch('llmtool.task_store.time.monotonic', side_effect=monotonic), \
                patch('llmtool.task_store.time.sleep', side_effect=advance_sleep), \
                patch('llmtool.task_store.os.open', side_effect=always_enoent):
            with self.assertRaises(LLMToolError) as caught:
                self._observation()
        self.assertEqual(caught.exception.task_store_error_code,
                         'WORKER_AVAILABILITY_UNAVAILABLE')
        self.assertNotIn('private filesystem detail', str(caught.exception))
        self.assertGreater(len(attempts), 1)
        self.assertLessEqual(len(attempts), 12)
        self.assertGreaterEqual(clock[0], 0.1)
        self.assertLess(clock[0], 0.12)

    def test_duplicate_observation_keys_rejected(self):
        self._observation()
        path = self.root / '.worker-availability.json'
        record = json.loads(path.read_text(encoding='utf-8'))
        record['observations'].append(dict(record['observations'][0]))
        from llmtool.task_store import _digest, _json_bytes
        record.pop('record_sha256')
        record['record_sha256'] = _digest(record)
        path.write_bytes(_json_bytes(record))
        with self.assertRaises(LLMToolError):
            self.store.get_worker_observations()

    def test_concurrent_updates_remain_valid_and_are_not_lost(self):
        def write(index):
            now = datetime.now(timezone.utc).replace(microsecond=0)
            self.store.record_worker_observation(
                worker_key='codex-developer', layer='runtime',
                status='PASS' if index % 2 else 'FAIL',
                observed_at=stamp(now - timedelta(seconds=1)),
                fresh_until=stamp(now + timedelta(hours=1)),
                source='LOCAL_OBSERVATION',
                evidence_sha256=hashlib.sha256(str(index).encode()).hexdigest())

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(write, range(16)))
        entry = TaskStore(self.root).get_worker_observations()['codex-developer']['runtime']
        self.assertIn(entry['status'], ('PASS', 'FAIL'))
        self.assertEqual(stat.S_IMODE((self.root / '.worker-availability.json').stat().st_mode),
                         0o600)
        self.assertEqual(stat.S_IMODE((self.root / '.worker-availability.lock').stat().st_mode),
                         0o600)
        self.assertFalse(any(path.name.endswith('.tmp') for path in self.root.iterdir()))

    def test_expected_current_item_replaces_it(self):
        self._observation(status='FAIL')
        current = self.store.get_worker_observations()['codex-developer']['runtime']
        self.assertEqual(current['status'], 'FAIL')
        now = datetime.now(timezone.utc).replace(microsecond=0)
        saved = self.store.record_worker_observation(
            worker_key='codex-developer', layer='runtime', status='PASS',
            observed_at=stamp(now - timedelta(seconds=1)),
            fresh_until=stamp(now + timedelta(hours=1)),
            source='LOCAL_OBSERVATION',
            evidence_sha256=hashlib.sha256(b'updated evidence').hexdigest(),
            expected=current)
        self.assertEqual(saved['codex-developer']['runtime']['status'], 'PASS')
        after = self.store.get_worker_observations()['codex-developer']['runtime']
        self.assertEqual(after['status'], 'PASS')

    def test_expected_mismatch_raises_and_leaves_file_unchanged(self):
        self._observation(status='FAIL')
        path = self.root / '.worker-availability.json'
        bytes_before = path.read_bytes()
        current = dict(self.store.get_worker_observations()['codex-developer']['runtime'])
        current['status'] = 'PASS'
        now = datetime.now(timezone.utc).replace(microsecond=0)
        with self.assertRaises(LLMToolError) as caught:
            self.store.record_worker_observation(
                worker_key='codex-developer', layer='runtime', status='PASS',
                observed_at=stamp(now - timedelta(seconds=1)),
                fresh_until=stamp(now + timedelta(hours=1)),
                source='LOCAL_OBSERVATION',
                evidence_sha256=hashlib.sha256(b'updated evidence').hexdigest(),
                expected=current)
        self.assertEqual(str(caught.exception), 'WORKER_AVAILABILITY_CHANGED')
        self.assertEqual(path.read_bytes(), bytes_before)

        # Absence: expecting an item when none exists raises and leaves file unchanged
        absent_expected = dict(current)
        with self.assertRaises(LLMToolError) as caught:
            self.store.record_worker_observation(
                worker_key='opencode-developer', layer='quota', status='PASS',
                observed_at=stamp(now - timedelta(seconds=1)),
                fresh_until=stamp(now + timedelta(hours=1)),
                source='LOCAL_OBSERVATION',
                evidence_sha256=hashlib.sha256(b'updated evidence').hexdigest(),
                expected=absent_expected)
        self.assertEqual(str(caught.exception), 'WORKER_AVAILABILITY_CHANGED')
        self.assertEqual(path.read_bytes(), bytes_before)

    def test_expected_none_behaves_as_before(self):
        self._observation(status='FAIL')
        now = datetime.now(timezone.utc).replace(microsecond=0)
        saved = self.store.record_worker_observation(
            worker_key='codex-developer', layer='runtime', status='PASS',
            observed_at=stamp(now - timedelta(seconds=1)),
            fresh_until=stamp(now + timedelta(hours=1)),
            source='LOCAL_OBSERVATION',
            evidence_sha256=hashlib.sha256(b'updated evidence').hexdigest(),
            expected=None)
        self.assertEqual(saved['codex-developer']['runtime']['status'], 'PASS')


class GwAgentAvailabilityProbeTests(unittest.TestCase):
    """#2367: gw-agent probe/hold classes reuse the existing sidecar."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'tasks'
        self.policy_path = worker_policy_fixture.install(self)
        self.store = TaskStore(self.root)
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        # #2609 RCA (c7082417851): the tests step a logical clock forward, and
        # the store refuses observations dated after its own clock (product rule,
        # unchanged). The tests therefore control the one clock both read, so no
        # observation is ever later than the store's time.
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

    def sidecar(self):
        return self.root / '.worker-availability.json'

    def at(self, delta=timedelta(0)):
        """Advance the shared test clock to self.now + delta and return it."""
        self.clock = self.now + delta
        return self.clock

    def test_infrastructure_pause_is_cleared_only_by_a_passing_probe(self):
        result = wa.record_gw_agent_failure(
            self.store, 'infrastructure', now=self.at(), task_id='1' * 32)
        self.assertEqual(result['action'], 'INFRASTRUCTURE_PAUSE')
        self.assertEqual(wa.gw_agent_refusal(self.store, now=self.at()),
                         'WORKER_GW_AGENT_UNAVAILABLE')

        calls = []
        report = wa.probe_gw_agent(
            self.store, now=self.at(timedelta(minutes=1)),
            call=lambda: calls.append('probe') or {
                'tcp': 'PASS', 'auth': 'PASS', 'inference': 'PASS'})
        self.assertEqual(report['status'], 'PASS')
        self.assertTrue(report['provider_call'])
        self.assertEqual(calls, ['probe'])
        self.assertIsNone(wa.gw_agent_refusal(
            self.store, now=self.at(timedelta(minutes=1))))

    def test_model_failure_steps_down_once_then_pauses_without_a_probe_call(self):
        first = wa.record_gw_agent_failure(
            self.store, 'model', now=self.at(), task_id='2' * 32)
        self.assertEqual(first['action'], 'STEP_DOWN')
        self.assertIsNone(wa.gw_agent_refusal(self.store, now=self.at()))

        second = wa.record_gw_agent_failure(
            self.store, 'model', now=self.at(timedelta(minutes=1)), task_id='3' * 32)
        self.assertEqual(second['action'], 'STEP_DOWN')
        self.assertIsNone(wa.gw_agent_refusal(self.store, now=self.at(timedelta(minutes=1))))

        third = wa.record_gw_agent_failure(
            self.store, 'model', now=self.at(timedelta(minutes=2)), task_id='4' * 32)
        self.assertEqual(third['action'], 'PAUSE')
        self.assertEqual(wa.gw_agent_refusal(
            self.store, now=self.at(timedelta(minutes=2))),
            'WORKER_GW_AGENT_MODEL_PAUSED')

        called = []
        report = wa.probe_gw_agent(
            self.store, now=self.at(timedelta(minutes=3)),
            call=lambda: called.append('provider'))
        self.assertFalse(report['provider_call'])
        self.assertEqual(report['reason'], 'WORKER_GW_AGENT_MODEL_PAUSED')
        self.assertEqual(called, [])
        self.assertEqual(wa.gw_agent_refusal(
            self.store, now=self.at(timedelta(minutes=3))),
            'WORKER_GW_AGENT_MODEL_PAUSED')

    def test_429_retry_after_blocks_the_probe_until_it_expires(self):
        result = wa.record_gw_agent_failure(
            self.store, 'rate_limit', now=self.at(), retry_after=120,
            task_id='4' * 32)
        self.assertEqual(result['action'], 'RATE_LIMITED')
        called = []
        early = wa.probe_gw_agent(
            self.store, now=self.at(timedelta(seconds=60)),
            call=lambda: called.append('provider'))
        self.assertFalse(early['provider_call'])
        self.assertEqual(called, [])

        late = wa.probe_gw_agent(
            self.store, now=self.at(timedelta(seconds=121)),
            call=lambda: {'tcp': 'PASS', 'auth': 'PASS', 'inference': 'PASS'})
        self.assertEqual(late['status'], 'PASS')
        self.assertTrue(late['provider_call'])
        self.assertIsNone(wa.gw_agent_refusal(
            self.store, now=self.at(timedelta(seconds=121))))

    def test_security_hold_is_not_auto_recovered_by_a_probe(self):
        result = wa.record_gw_agent_failure(
            self.store, 'security', now=self.at(), task_id='5' * 32)
        self.assertEqual(result['action'], 'SECURITY_HOLD')
        called = []
        report = wa.probe_gw_agent(
            self.store, now=self.at(timedelta(minutes=1)),
            call=lambda: called.append('provider'))
        self.assertFalse(report['provider_call'])
        self.assertEqual(report['reason'], 'WORKER_GW_AGENT_SECURITY_HOLD')
        self.assertEqual(called, [])
        self.assertEqual(wa.gw_agent_refusal(
            self.store, now=self.at(timedelta(minutes=1))),
            'WORKER_GW_AGENT_SECURITY_HOLD')

    def test_failed_hold_write_refuses_a_later_admission(self):
        class FailingStore:
            root = self.root / 'failing'

            def record_worker_observation(self, **_observation):
                raise LLMToolError('synthetic hold write failure')

        store = FailingStore()
        self.addCleanup(wa._GW_HOLD_WRITE_FAILED.pop, wa._store_key(store), None)
        result = wa.record_gw_agent_failure(store, 'security', now=self.at())
        self.assertEqual(result['action'], 'HOLD_WRITE_FAILED')
        self.assertEqual(wa.gw_agent_refusal(store, now=self.at()),
                         'WORKER_GW_AGENT_HOLD_UNRECORDED')

    def test_one_model_failure_is_a_step_down_even_with_the_task_record(self):
        class ScanStore:
            root = self.root

            def __init__(self, store, developer):
                self.store = store
                self.developer = developer

            def get_worker_observations(self):
                return self.store.get_worker_observations()

            def record_worker_observation(self, **observation):
                return self.store.record_worker_observation(**observation)

            def task_ids(self):
                return ['a' * 32]

            def get(self, _task_id):
                return {'developer': self.developer}

        developer = {'provider_profile': 'gw-agent', 'state': 'DEVELOPER_UNAVAILABLE',
                     'error_code': 'DEVELOPER_GW_AGENT_MODEL_ERROR',
                     'created_at': wa._stamp(self.now - timedelta(minutes=1))}
        store = ScanStore(self.store, developer)
        first = wa.record_gw_agent_failure(store, 'model', now=self.at(),
                                           task_id='a' * 32)
        self.assertEqual(first['action'], 'STEP_DOWN')
        self.assertIsNone(wa.gw_agent_refusal(store, now=self.at()))
        second = wa.record_gw_agent_failure(store, 'model', now=self.at(),
                                            task_id='a' * 32)
        self.assertEqual(second['action'], 'STEP_DOWN')
        self.assertIsNone(wa.gw_agent_refusal(store, now=self.at()))
        third = wa.record_gw_agent_failure(store, 'model', now=self.at(),
                                           task_id='a' * 32)
        self.assertEqual(third['action'], 'PAUSE')
        self.assertEqual(wa.gw_agent_refusal(store, now=self.at()),
                         'WORKER_GW_AGENT_MODEL_PAUSED')

    def test_failure_classes_use_fixed_codes_only(self):
        cases = (
            ('DEVELOPER_GW_AGENT_QUOTA_EXHAUSTED_HTTP_429', 'rate_limit'),
            ('DEVELOPER_GW_AGENT_AUDIT_INVALID', 'security'),
            ('DEVELOPER_GW_AGENT_TIMEOUT', 'infrastructure'),
            ('DEVELOPER_GW_AGENT_MODEL_ERROR_HTTP_500', 'infrastructure'),
            ('DEVELOPER_GW_AGENT_MODEL_UNVERIFIED', 'model'),
            ('DEVELOPER_GW_AGENT_MODEL_ERROR', 'model'),
            ('DEVELOPER_GW_AGENT_RESULT_UNKNOWN', 'unknown'),
        )
        for code, expected in cases:
            with self.subTest(code=code):
                self.assertEqual(wa.classify_gw_agent_failure(code), expected)

    def test_unknown_outcome_writes_nothing_and_does_not_pause(self):
        result = wa.record_gw_agent_failure(
            self.store, 'DEVELOPER_GW_AGENT_RESULT_UNKNOWN', now=self.at())
        self.assertEqual(result['action'], 'NONE')
        self.assertIsNone(wa.gw_agent_refusal(self.store, now=self.at()))
        self.assertFalse(self.sidecar().exists())

    def test_other_developer_routes_are_unaffected(self):
        wa.record_gw_agent_failure(self.store, 'model', now=self.at())
        wa.record_gw_agent_failure(self.store, 'model', now=self.at(timedelta(seconds=1)))
        wa.record_gw_agent_failure(self.store, 'model', now=self.at(timedelta(seconds=2)))
        self.assertFalse(wa.fresh_quota_fail(self.store, 'opencode-developer'))
        self.assertIsNone(wa.quota_refusal(self.store, 'opencode-developer'))
        self.assertIsNotNone(wa.gw_agent_refusal(self.store, now=self.at()))


if __name__ == '__main__':
    unittest.main()
