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
from llmtool import worker_availability, worker_dispatch
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
        )
        for item in invalid_cases:
            with self.subTest(item=item):
                with self.assertRaises(LLMToolError):
                    self.store.record_worker_observation(**item)

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


def gw_record(*, state='DEVELOPER_UNAVAILABLE', error_code=None, task_id='1' * 32,
              profile='gw-agent'):
    """A minimal gw-agent Developer outcome; only the fields classification reads."""
    return {'task_id': task_id, 'developer': {
        'assignment_id': '2' * 32, 'provider_profile': profile,
        'state': state, 'error_code': error_code}}


class GwAgentAvailabilityProbeTests(unittest.TestCase):
    """#2367: gw-agent failure classes, the availability probe and admission.

    The probe itself is injected (no gateway, no network); everything else is
    the real TaskStore availability record and the real admission checks.
    """

    ROUTE = object()
    BUDGETS = {'max_turns': 512}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'tasks'
        self.policy_path = worker_policy_fixture.install(self)
        self.store = TaskStore(self.root)
        allowed = patch.object(
            worker_dispatch, 'evaluate',
            side_effect=lambda worker, *_args, **_kwargs: worker_dispatch.PolicyDecision(
                worker, True, 'OWNER_POLICY_ALLOWED', 'f' * 64))
        allowed.start()
        self.addCleanup(allowed.stop)
        self.calls = []

    def probe(self, outcome, **extra):
        def run(route, budgets):
            self.calls.append((route, budgets))
            report = {'outcome': outcome,
                      'tcp': 'PASS' if outcome in ('PASS', 'RATE_LIMITED') else 'UNKNOWN',
                      'auth': 'PASS' if outcome in ('PASS', 'RATE_LIMITED') else 'UNKNOWN',
                      'inference': 'PASS' if outcome == 'PASS' else 'FAIL',
                      'retry_after_seconds': None,
                      'checked_at': stamp(datetime.now(timezone.utc)), 'elapsed_ms': 7}
            report.update(extra)
            return report
        return run

    def run_probe(self, outcome, **extra):
        return worker_availability.run_gw_agent_probe(
            self.store, self.ROUTE, self.BUDGETS, probe=self.probe(outcome, **extra))

    def gw_view(self):
        return self.store.get_worker_observations()['gw-agent']

    def refused(self, worker='gw-agent'):
        return worker_availability.quota_refusal(self.store, worker)

    def long_ago(self):
        return (datetime.now(timezone.utc) - worker_availability.FRESH_FOR -
                timedelta(minutes=1))

    def test_failure_classes_are_kept_apart(self):
        cases = (
            ('DEVELOPER_GW_AGENT_TIMEOUT', 'INFRASTRUCTURE'),
            ('DEVELOPER_GW_AGENT_GATEWAY_TIMEOUT', 'INFRASTRUCTURE'),
            ('DEVELOPER_GW_AGENT_MODEL_ERROR', 'INFRASTRUCTURE'),
            ('DEVELOPER_GW_AGENT_MODEL_ERROR_NETWORK_TIMEOUT', 'INFRASTRUCTURE'),
            ('DEVELOPER_GW_AGENT_MODEL_ERROR_HTTP_503', 'INFRASTRUCTURE'),
            ('DEVELOPER_GW_AGENT_MODEL_ERROR_HTTP_401', 'SECURITY'),
            ('DEVELOPER_GW_AGENT_MODEL_ERROR_HTTP_403', 'SECURITY'),
            ('DEVELOPER_GW_AGENT_AUDIT_INVALID', 'SECURITY'),
            ('DEVELOPER_GW_AGENT_POLICY_REFUSED', 'SECURITY'),
            ('DEVELOPER_GW_AGENT_BLOCKED_APPLY_EDIT_DENIED', 'MODEL_CAPABILITY'),
            ('DEVELOPER_GW_AGENT_BUDGET_EXCEEDED', 'MODEL_CAPABILITY'),
            ('DEVELOPER_GW_AGENT_MODEL_ERROR_HTTP_429', None),
            ('DEVELOPER_GW_AGENT_QUOTA_EXHAUSTED', None),
            ('DEVELOPER_GW_AGENT_OUTPUT_INVALID', 'UNKNOWN'),
            ('DEVELOPER_RESULT_UNKNOWN', 'UNKNOWN'),
            ('DEVELOPER_OWNER_PAUSED', None),
        )
        for code, expected in cases:
            with self.subTest(code=code):
                self.assertEqual(
                    worker_availability.classify_failure(gw_record(error_code=code)), expected)
        self.assertEqual(worker_availability.classify_failure(
            gw_record(state='DEVELOPER_OUTPUT_REJECTED')), 'MODEL_CAPABILITY')
        self.assertEqual(worker_availability.classify_failure(
            gw_record(state='DEVELOPER_OUTCOME_UNKNOWN')), 'UNKNOWN')
        self.assertIsNone(worker_availability.classify_failure(
            gw_record(error_code='DEVELOPER_GW_AGENT_TIMEOUT', profile='opencode')))

    def test_gateway_down_then_probe_pass_then_dispatch_allowed(self):
        failure = worker_availability.observe_failure_class(
            self.store, gw_record(error_code='DEVELOPER_GW_AGENT_TIMEOUT'), now=self.long_ago())
        self.assertEqual(failure, 'INFRASTRUCTURE')
        self.assertEqual(self.gw_view()['endpoint']['status'], 'FAIL')
        self.assertEqual(worker_availability.hold_reason(self.store, 'gw-agent'),
                         'INFRASTRUCTURE_PAUSE')
        self.assertEqual(self.refused(), worker_availability.REFUSAL_CODE)

        report = self.run_probe('PASS')
        self.assertEqual(len(self.calls), 1)
        self.assertIs(self.calls[0][0], self.ROUTE)
        self.assertEqual((report['outcome'], report['tcp'], report['auth'], report['inference']),
                         ('PASS', 'PASS', 'PASS', 'PASS'))
        self.assertRegex(report['checked_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        self.assertEqual((report['recorded_layer'], report['recorded_status']),
                         ('endpoint', 'PASS'))
        self.assertIsNone(report['hold'])
        self.assertIsNone(self.refused())
        self.assertFalse(worker_availability.fresh_quota_fail(self.store, 'gw-agent'))

    def test_failed_probe_keeps_the_pause_and_backs_off_without_looping(self):
        worker_availability.observe_failure_class(
            self.store, gw_record(error_code='DEVELOPER_GW_AGENT_MODEL_ERROR_NETWORK_TIMEOUT'),
            now=self.long_ago())
        report = self.run_probe('INFRASTRUCTURE')
        self.assertEqual((report['recorded_layer'], report['recorded_status']),
                         ('endpoint', 'FAIL'))
        self.assertEqual(report['hold'], 'INFRASTRUCTURE_PAUSE')
        for _attempt in range(3):
            with self.assertRaises(LLMToolError) as caught:
                self.run_probe('PASS')
            self.assertEqual(str(caught.exception), 'GW_AGENT_PROBE_NOT_DUE')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.refused(), worker_availability.REFUSAL_CODE)

    def test_inconclusive_probe_never_clears_the_pause(self):
        worker_availability.observe_failure_class(
            self.store, gw_record(error_code='DEVELOPER_GW_AGENT_TIMEOUT'), now=self.long_ago())
        report = self.run_probe('UNKNOWN', tcp='UNKNOWN', auth='UNKNOWN', inference='UNKNOWN')
        self.assertEqual(report['outcome'], 'UNKNOWN')
        self.assertEqual(report['hold'], 'INFRASTRUCTURE_PAUSE')
        self.assertEqual(self.refused(), worker_availability.REFUSAL_CODE)

    def test_two_lowest_tier_capability_failures_pause_and_a_probe_does_not_clear(self):
        failed = gw_record(state='DEVELOPER_OUTPUT_REJECTED')
        record = worker_availability.record_capability_outcome
        self.assertEqual(record(self.store, failed, passed=False, at_lowest_tier=False),
                         'STEP_DOWN')
        self.assertIsNone(worker_availability.hold_reason(self.store, 'gw-agent'))
        self.assertEqual(record(self.store, failed, passed=False, at_lowest_tier=True),
                         'LOWEST_TIER_FAILURE')
        self.assertIsNone(worker_availability.hold_reason(self.store, 'gw-agent'))
        self.assertIsNone(self.refused())
        self.assertEqual(record(self.store, failed, passed=False, at_lowest_tier=True),
                         'PAUSED')
        self.assertEqual(worker_availability.hold_reason(self.store, 'gw-agent'),
                         'MODEL_CAPABILITY_PAUSE')
        self.assertEqual(self.refused(), worker_availability.REFUSAL_CODE)

        report = self.run_probe('PASS')
        self.assertEqual(report['outcome'], 'PASS')
        self.assertEqual(report['hold'], 'MODEL_CAPABILITY_PAUSE')
        self.assertEqual(self.refused(), worker_availability.REFUSAL_CODE)
        self.assertEqual(record(self.store, gw_record(state='DEVELOPER_RESPONDED'),
                                passed=True, at_lowest_tier=True), 'PAUSED')
        self.assertEqual(worker_availability.hold_reason(self.store, 'gw-agent'),
                         'MODEL_CAPABILITY_PAUSE')

    def test_a_pass_resets_the_lowest_tier_streak(self):
        failed = gw_record(state='DEVELOPER_OUTPUT_REJECTED')
        record = worker_availability.record_capability_outcome
        self.assertEqual(record(self.store, failed, passed=False, at_lowest_tier=True),
                         'LOWEST_TIER_FAILURE')
        self.assertEqual(record(self.store, gw_record(state='DEVELOPER_RESPONDED'),
                                passed=True, at_lowest_tier=True), 'CLEAR')
        self.assertEqual(record(self.store, failed, passed=False, at_lowest_tier=True),
                         'LOWEST_TIER_FAILURE')
        self.assertIsNone(worker_availability.hold_reason(self.store, 'gw-agent'))

    def test_infrastructure_or_security_failure_is_never_counted_as_capability(self):
        for code in ('DEVELOPER_GW_AGENT_TIMEOUT', 'DEVELOPER_GW_AGENT_AUDIT_INVALID',
                     'DEVELOPER_RESULT_UNKNOWN'):
            with self.subTest(code=code):
                with self.assertRaises(LLMToolError) as caught:
                    worker_availability.record_capability_outcome(
                        self.store, gw_record(error_code=code), passed=False,
                        at_lowest_tier=True)
                self.assertEqual(str(caught.exception), 'WORKER_FAILURE_CLASS_MISMATCH')
        self.assertEqual(self.gw_view()['runtime']['status'], 'UNKNOWN')

    def test_http_429_retry_after_is_honoured(self):
        report = self.run_probe('RATE_LIMITED', retry_after_seconds=600)
        self.assertEqual((report['recorded_layer'], report['recorded_status']),
                         ('quota', 'FAIL'))
        self.assertEqual(report['retry_after_seconds'], 600)
        quota = self.gw_view()['quota']
        observed = datetime.strptime(quota['observed_at'], '%Y-%m-%dT%H:%M:%SZ')
        fresh_until = datetime.strptime(quota['fresh_until'], '%Y-%m-%dT%H:%M:%SZ')
        self.assertEqual(fresh_until - observed, timedelta(seconds=600))
        with self.assertRaises(LLMToolError) as caught:
            self.run_probe('PASS')
        self.assertEqual(str(caught.exception), 'GW_AGENT_PROBE_RATE_LIMITED')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.refused(), worker_availability.REFUSAL_CODE)

    def test_http_429_without_retry_after_uses_the_existing_window(self):
        self.run_probe('RATE_LIMITED')
        quota = self.gw_view()['quota']
        observed = datetime.strptime(quota['observed_at'], '%Y-%m-%dT%H:%M:%SZ')
        fresh_until = datetime.strptime(quota['fresh_until'], '%Y-%m-%dT%H:%M:%SZ')
        self.assertEqual(fresh_until - observed, worker_availability.FRESH_FOR)

    def test_auth_or_audit_failure_is_not_auto_recovered(self):
        for index, code in enumerate(('DEVELOPER_GW_AGENT_AUDIT_INVALID',
                                      'DEVELOPER_GW_AGENT_MODEL_ERROR_HTTP_401')):
            with self.subTest(code=code):
                self.store = TaskStore(Path(self.temp.name).resolve() / ('security-%d' % index))
                self.calls = []
                failure = worker_availability.observe_failure_class(
                    self.store, gw_record(error_code=code), now=self.long_ago())
                self.assertEqual(failure, 'SECURITY')
                self.assertEqual(worker_availability.hold_reason(self.store, 'gw-agent'),
                                 'SECURITY_STOP')
                with self.assertRaises(LLMToolError) as caught:
                    self.run_probe('PASS')
                self.assertEqual(str(caught.exception), 'GW_AGENT_PROBE_SECURITY_STOP')
                self.assertEqual(self.calls, [])
                self.assertEqual(worker_availability.record_capability_outcome(
                    self.store, gw_record(state='DEVELOPER_RESPONDED'), passed=True,
                    at_lowest_tier=True), 'CLEAR')
                self.assertEqual(worker_availability.hold_reason(self.store, 'gw-agent'),
                                 'SECURITY_STOP')
                self.assertEqual(self.refused(), worker_availability.REFUSAL_CODE)

    def test_probe_authorization_failure_becomes_a_security_stop(self):
        report = self.run_probe('AUTHORIZATION', tcp='PASS', auth='FAIL', inference='UNKNOWN')
        self.assertEqual((report['recorded_layer'], report['recorded_status']),
                         ('authentication', 'FAIL'))
        self.assertEqual(report['hold'], 'SECURITY_STOP')
        with self.assertRaises(LLMToolError) as caught:
            self.run_probe('PASS')
        self.assertEqual(str(caught.exception), 'GW_AGENT_PROBE_SECURITY_STOP')
        self.assertEqual(len(self.calls), 1)

    def test_unknown_outcome_is_not_recorded_or_replayed(self):
        task = self.store.create(make_task_record())
        before_task = self.store.get(task['task_id'])
        before_policy = self.policy_path.read_bytes()
        unknown = gw_record(state='DEVELOPER_OUTCOME_UNKNOWN',
                            error_code='DEVELOPER_RESULT_UNKNOWN', task_id=task['task_id'])
        self.assertEqual(worker_availability.observe_failure_class(self.store, unknown),
                         'UNKNOWN')
        view = self.gw_view()
        for layer in ('endpoint', 'authentication', 'runtime', 'quota'):
            self.assertEqual(view[layer]['status'], 'UNKNOWN')
        self.assertIsNone(worker_availability.hold_reason(self.store, 'gw-agent'))

        self.run_probe('PASS')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.store.get(task['task_id']), before_task)
        self.assertEqual(self.policy_path.read_bytes(), before_policy)

    def test_other_developers_are_unaffected_while_gw_agent_is_paused(self):
        worker_availability.observe_failure_class(
            self.store, gw_record(error_code='DEVELOPER_GW_AGENT_TIMEOUT'))
        failed = gw_record(state='DEVELOPER_OUTPUT_REJECTED')
        for _attempt in range(2):
            worker_availability.record_capability_outcome(
                self.store, failed, passed=False, at_lowest_tier=True)
        self.assertEqual(self.refused(), worker_availability.REFUSAL_CODE)
        for worker in ('claude-developer', 'opencode-developer', 'agy'):
            with self.subTest(worker=worker):
                self.assertFalse(worker_availability.fresh_quota_fail(self.store, worker))
                self.assertIsNone(self.refused(worker))
                self.assertIsNone(worker_availability.hold_reason(self.store, worker))
                for layer in ('endpoint', 'authentication', 'runtime'):
                    self.assertEqual(
                        self.store.get_worker_observations()[worker][layer]['status'],
                        'UNKNOWN')

    def test_unreadable_state_refuses_the_probe_before_any_call(self):
        class Unreadable:
            def get_worker_observations(self):
                raise LLMToolError('WORKER_AVAILABILITY_UNAVAILABLE')

        with self.assertRaises(LLMToolError) as caught:
            worker_availability.run_gw_agent_probe(
                Unreadable(), self.ROUTE, self.BUDGETS, probe=self.probe('PASS'))
        self.assertEqual(str(caught.exception), 'GW_AGENT_PROBE_STATE_UNAVAILABLE')
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
