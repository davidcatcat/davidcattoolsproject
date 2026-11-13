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


if __name__ == '__main__':
    unittest.main()
