"""#1019: waiting PM inbox avoids busy-polling while delivering events promptly.

Synthetic test data only; no credential-shaped strings are used.
"""

import json
import os
import tempfile
import threading
import time
import unittest
import uuid
from argparse import Namespace
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import active_pm_inbox, developer_assignment_inbox, task_submission_cli
from llmtool import task_store as task_store_module
from llmtool.errors import LLMToolError
from llmtool.store_fingerprint import StoreFingerprintError
from llmtool.task_store import TaskStore
from llmtool.worker_dispatch import PolicyDecision


_RESPONSE = json.dumps({
    'schema_version': 1,
    'summary': 'Synthetic PM plan.',
    'developer_assignment': {
        'route': 'agy',
        'risk': 'LOW',
        'goal': 'Synthetic inbox wait test.',
        'acceptance': ['wait works'],
    },
})


class ActivePMInboxWaitTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(os.path.realpath(self._tmp.name))
        self.claude_home = self.base / 'claude-config'
        self.pm_cwd = self.base / 'pm-root'
        self.pm_cwd.mkdir()
        env = patch.dict(os.environ, {
            'CLAUDE_CONFIG_DIR': str(self.claude_home),
            'HOME': str(self.base / 'home'),
        })
        env.start()
        self.addCleanup(env.stop)
        self.session = self._claude_session()
        self.session_key = 'pm-' + self.session
        self.state_dir = self.base / 'state'
        self.state_dir.mkdir(mode=0o700)
        os.chmod(self.state_dir, 0o700)
        self.store = TaskStore(self.state_dir / 'tasks')
        self.store._ensure_root()

    def _claude_session(self):
        native = str(uuid.uuid4())
        path = active_pm_inbox.claude_session_file(native, str(self.pm_cwd))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}\n', encoding='utf-8')
        return native

    def _record_pm(self, session=None, *, request_text='Synthetic inbox wait test.'):
        out = StringIO()
        with redirect_stdout(out):
            code = task_submission_cli.run_task_record_pm(
                Namespace(
                    stdin_json=True,
                    pm_session_id=self.session if session is None else session,
                    pm_runtime=None,
                    pm_cwd=str(self.pm_cwd),
                ),
                store=self.store,
                input_stream=StringIO(json.dumps({
                    'request': request_text,
                    'response': _RESPONSE,
                })),
            )
        self.assertEqual(code, 0)
        return json.loads(out.getvalue())

    def _record_event(self, task, *, digest_char='b'):
        receipt = {
            'task_id': task['task_id'],
            'run_id': task['run_id'],
            'pm_session': task['pm_session_id'],
            'executor_task': 'synthetic-d6-task',
            'source_sha': 'a' * 40,
            'modules': ['test_active_pm_inbox_wait'],
            'receipt_digest': digest_char * 64,
        }
        return active_pm_inbox.record_remote_test_event(
            self.store, task['task_id'], receipt)

    def _populate_synthetic_store(self, total_records, *, other_session,
                                  start_index=1, request_text='Synthetic inbox wait test.'):
        template_view = self._record_pm(session=other_session, request_text=request_text)
        template = self.store.get(template_view['task_id'])
        template.pop('record_sha256', None)
        created = [template_view]
        for offset in range(1, total_records):
            index = start_index + offset
            task_id = f'{index:032x}'
            contract = task_submission_cli._contract(task_id, template['request_sha256'])
            record = dict(template)
            record['task_id'] = task_id
            record['task_contract'] = contract.to_dict()
            record['task_binding'] = contract.binding(record['run_id']).to_dict()
            record['record_sha256'] = task_store_module._digest(record)
            raw = task_store_module._json_bytes(record)
            path = self.store.root / (task_id + '.json')
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, raw)
            finally:
                os.close(fd)
            created.append(record)
        return created

    def test_r1_unchanged_wait_skips_full_reads(self):
        self._record_pm()
        ticks = iter([0.0, 2.0, 4.0, 6.0, 8.0, 10.0])
        read_calls = []
        original_read = active_pm_inbox.read_inbox

        def counting_read(store, session_key):
            read_calls.append(session_key)
            return original_read(store, session_key)

        with patch.object(active_pm_inbox, 'read_inbox', side_effect=counting_read):
            result = active_pm_inbox.wait_inbox(
                self.store,
                self.session_key,
                wait=True,
                timeout=10,
                poll=2.0,
                clock=lambda: next(ticks),
                sleep=lambda _seconds: None,
            )

        self.assertEqual(len(read_calls), 1)
        self.assertIs(result['timed_out'], True)
        self.assertEqual(result['events'], [])

    def test_r2_event_recorded_during_wait_is_delivered(self):
        task = self._record_pm()
        read_calls = []
        original_read = active_pm_inbox.read_inbox

        def counting_read(store, session_key):
            read_calls.append(session_key)
            return original_read(store, session_key)

        sleep_calls = []

        def sleep(seconds):
            sleep_calls.append(seconds)
            if len(sleep_calls) == 2:
                self._record_event(task)

        ticks = iter([0.0, 2.0, 4.0, 6.0])
        with patch.object(active_pm_inbox, 'read_inbox', side_effect=counting_read):
            result = active_pm_inbox.wait_inbox(
                self.store,
                self.session_key,
                wait=True,
                timeout=30,
                poll=2.0,
                clock=lambda: next(ticks),
                sleep=sleep,
            )

        self.assertEqual(len(sleep_calls), 2)
        self.assertEqual(len(read_calls), 2)
        self.assertIs(result['timed_out'], False)
        self.assertEqual(len(result['events']), 1)
        self.assertEqual(result['events'][0]['task_id'], task['task_id'])
        expected = active_pm_inbox.read_inbox(self.store, self.session_key)
        self.assertEqual(result['events'], expected['events'])

    def test_r2_change_made_while_inbox_is_being_read_is_not_missed(self):
        task = self._record_pm()
        read_calls = []
        original_read = active_pm_inbox.read_inbox

        def racing_read(store, session_key):
            read_calls.append(session_key)
            if len(read_calls) == 1:
                snapshot = original_read(store, session_key)
                self._record_event(task)
                return snapshot
            return original_read(store, session_key)

        ticks = iter([0.0, 2.0, 4.0, 6.0])
        with patch.object(active_pm_inbox, 'read_inbox', side_effect=racing_read):
            result = active_pm_inbox.wait_inbox(
                self.store,
                self.session_key,
                wait=True,
                timeout=30,
                poll=2.0,
                clock=lambda: next(ticks),
                sleep=lambda _seconds: None,
            )

        self.assertEqual(len(read_calls), 2)
        self.assertIs(result['timed_out'], False)
        self.assertEqual(len(result['events']), 1)
        self.assertEqual(result['events'][0]['task_id'], task['task_id'])

    def test_r3_non_waiting_timeout_bounds_and_blocked_state(self):
        task = self._record_pm()

        with patch.object(active_pm_inbox, 'fingerprint_directory') as fp:
            non_wait = active_pm_inbox.wait_inbox(
                self.store, self.session_key, wait=False, timeout=10)
        fp.assert_not_called()
        self.assertIs(non_wait['timed_out'], False)
        self.assertEqual(non_wait['events'], [])

        zero_wait = active_pm_inbox.wait_inbox(
            self.store, self.session_key, wait=True, timeout=0,
            clock=lambda: 0.0,
            sleep=lambda _seconds: self.fail('timeout=0 must not sleep'))
        self.assertIs(zero_wait['timed_out'], True)

        for bad in (-1, 3601, True, '5'):
            with self.subTest(timeout=bad), self.assertRaises(LLMToolError):
                active_pm_inbox.wait_inbox(
                    self.store, self.session_key, wait=True, timeout=bad)

        limit_error = LLMToolError('limit reached')
        limit_error.task_store_error_code = 'COMPLETION_EVENT_LIMIT'
        index_path = self.state_dir / 'task-status-index.json'
        if index_path.exists():
            index_path.unlink()
        with patch.object(task_submission_cli, '_active_pm_handoff',
                          return_value={'packet': {'synthetic': True}}), \
             patch.object(active_pm_inbox, 'record_ready_event',
                          side_effect=limit_error):
            blocked = active_pm_inbox.wait_inbox(
                self.store, self.session_key, wait=True, timeout=30,
                sleep=lambda _seconds: self.fail('blocked must return at once'))
        self.assertIs(blocked['timed_out'], False)
        self.assertEqual(blocked['status'], 'ATTENTION')
        self.assertEqual(blocked['blocked'], [{
            'task_id': task['task_id'],
            'code': 'COMPLETION_EVENT_LIMIT',
            'unacknowledged_event_count': 0,
            'action': 'ACK_DELIVERED_EVENTS_THEN_READ_INBOX',
        }])

    def test_r3_assignment_becoming_unclaimed_by_passage_of_time_wakes_wait(self):
        t0 = datetime(2026, 11, 27, 12, 0, 0, tzinfo=timezone.utc)
        d6_root = str(self.base / 'worktree')
        common_dir = str(self.base / 'repo' / '.git')
        decision = PolicyDecision('claude-developer', True, 'OWNER_POLICY_ALLOWED', 'a' * 64)
        registration = SimpleNamespace(root=d6_root, result_sha='1' * 40)

        with patch('llmtool.worker_dispatch.evaluate', return_value=decision), \
             patch.object(developer_assignment_inbox, '_registration',
                          return_value=registration):
            assigned = developer_assignment_inbox.assign(
                self.store,
                worker='claude-developer',
                d6_task_id='synthetic-d6',
                common_dir=common_dir,
                pm_session_key=self.session_key,
                expires_in=10,
                now=t0,
            )

        now_values = iter([
            t0 + timedelta(seconds=1),
            t0 + timedelta(seconds=2),
            t0 + timedelta(seconds=11),
            t0 + timedelta(seconds=11),
        ])
        ticks = iter([0.0, 2.0, 4.0, 6.0])
        read_calls = []
        original_read = active_pm_inbox.read_inbox

        def counting_read(store, session_key):
            read_calls.append(session_key)
            return original_read(store, session_key)

        with patch.object(developer_assignment_inbox, '_now',
                          side_effect=lambda now=None: next(now_values)), \
             patch.object(active_pm_inbox, 'read_inbox', side_effect=counting_read):
            result = active_pm_inbox.wait_inbox(
                self.store,
                self.session_key,
                wait=True,
                timeout=30,
                poll=2.0,
                clock=lambda: next(ticks),
                sleep=lambda _seconds: None,
            )

        self.assertEqual(len(read_calls), 2)
        self.assertIs(result['timed_out'], False)
        self.assertEqual([item['assignment_id'] for item in result['unclaimed_assignments']],
                         [assigned['assignment_id']])

    def test_r3_r4_metadata_only_change_during_wait_updates_inbox(self):
        task = self._record_pm()
        task_path = self.store.root / (task['task_id'] + '.json')
        read_calls = []
        original_read = active_pm_inbox.read_inbox

        def counting_read(store, session_key):
            read_calls.append(session_key)
            return original_read(store, session_key)

        sleep_calls = []

        def sleep(seconds):
            sleep_calls.append(seconds)
            if len(sleep_calls) == 1:
                os.chmod(task_path, 0o644)

        ticks = iter([0.0, 2.0, 4.0, 6.0])
        try:
            with patch.object(active_pm_inbox, 'read_inbox', side_effect=counting_read):
                result = active_pm_inbox.wait_inbox(
                    self.store,
                    self.session_key,
                    wait=True,
                    timeout=6,
                    poll=2.0,
                    clock=lambda: next(ticks),
                    sleep=sleep,
                )
        finally:
            os.chmod(task_path, 0o600)

        self.assertEqual(len(read_calls), 2)
        self.assertIs(result['timed_out'], True)
        self.assertEqual(result['unreadable_task_count'], 1)
        self.assertEqual(result['status'], 'ATTENTION')

    def test_r4_fails_closed_when_fingerprint_unavailable(self):
        task = self._record_pm()
        read_calls = []
        original_read = active_pm_inbox.read_inbox

        def counting_read(store, session_key):
            read_calls.append(session_key)
            return original_read(store, session_key)

        sleep_calls = []

        def sleep(seconds):
            sleep_calls.append(seconds)
            if len(sleep_calls) == 2:
                self._record_event(task)

        ticks = iter([0.0, 2.0, 4.0, 6.0])
        with patch.object(active_pm_inbox, 'fingerprint_directory',
                          side_effect=StoreFingerprintError), \
             patch.object(active_pm_inbox, 'read_inbox', side_effect=counting_read):
            result = active_pm_inbox.wait_inbox(
                self.store,
                self.session_key,
                wait=True,
                timeout=30,
                poll=2.0,
                clock=lambda: next(ticks),
                sleep=sleep,
            )

        self.assertEqual(len(read_calls), 3)
        self.assertIs(result['timed_out'], False)
        self.assertEqual(len(result['events']), 1)

        class RootlessStore:
            pass

        rootless_calls = []

        def fake_read(_store, session_key):
            rootless_calls.append(session_key)
            return {
                'pm_session_id': session_key,
                'status': 'OK',
                'events': [],
                'blocked': [],
                'unreadable_task_count': 0,
                'unclaimed_assignments': [],
                'claimed_assignments': [],
            }

        rootless_ticks = iter([0.0, 2.0, 4.0])
        with patch.object(active_pm_inbox, 'read_inbox', side_effect=fake_read):
            rootless = active_pm_inbox.wait_inbox(
                RootlessStore(),
                self.session_key,
                wait=True,
                timeout=4,
                poll=2.0,
                clock=lambda: next(rootless_ticks),
                sleep=lambda _seconds: None,
            )
        self.assertEqual(len(rootless_calls), 2)
        self.assertIs(rootless['timed_out'], True)

    def test_r1_warm_inbox_read_in_new_store_skips_unchanged_non_pending_tasks(self):
        other_session = self._claude_session()
        self._populate_synthetic_store(10, other_session=other_session, start_index=1)
        none_tasks = [self._record_pm() for _ in range(10)]
        idle_tasks = [self._record_pm() for _ in range(3)]
        pending_tasks = [self._record_pm() for _ in range(3)]
        acked_ids = []
        for task in idle_tasks:
            event = self._record_event(task, digest_char='d')
            active_pm_inbox.ack(self.store, event['event_id'], self.session_key,
                                task_id=task['task_id'])
            acked_ids.append(event['event_id'])
        for task in pending_tasks:
            self._record_event(task, digest_char='e')

        warm = active_pm_inbox.read_inbox(self.store, self.session_key)
        self.assertEqual(len(warm['events']), 3)
        index_path = self.state_dir / 'task-status-index.json'
        before_stat = index_path.stat()

        new_store = TaskStore(self.store.root)
        get_calls = []
        original_get = new_store.get

        def counting_get(task_id):
            get_calls.append(task_id)
            return original_get(task_id)

        with patch.object(new_store, 'get', side_effect=counting_get):
            second = active_pm_inbox.read_inbox(new_store, self.session_key)

        self.assertEqual(second['events'], warm['events'])
        self.assertEqual(sorted(get_calls), sorted(t['task_id'] for t in pending_tasks))
        after_stat = index_path.stat()
        self.assertEqual((before_stat.st_ino, before_stat.st_mtime_ns),
                         (after_stat.st_ino, after_stat.st_mtime_ns))

        # Updating one NONE task triggers a validated read of only that changed task + pending.
        self._record_event(none_tasks[0], digest_char='f')
        get_calls.clear()
        changed_store = TaskStore(self.store.root)
        original_changed_get = changed_store.get

        def counting_changed_get(task_id):
            get_calls.append(task_id)
            return original_changed_get(task_id)

        with patch.object(changed_store, 'get', side_effect=counting_changed_get):
            after_change = active_pm_inbox.read_inbox(changed_store, self.session_key)

        self.assertEqual(len(after_change['events']), 4)
        self.assertEqual(sorted(get_calls),
                         sorted([none_tasks[0]['task_id']] +
                                [t['task_id'] for t in pending_tasks]))

        # Session-scan ack without task_id still finds IDLE (already-acked) events.
        idempotent = active_pm_inbox.ack(
            TaskStore(self.store.root), acked_ids[0], self.session_key)
        self.assertIs(idempotent['acknowledged_now'], False)
        self.assertEqual(idempotent['event']['state'], 'ACKED')

    def test_r4_damaged_or_changed_record_fails_closed_on_read_inbox(self):
        none_task = self._record_pm()
        pending_task = self._record_pm()
        self._record_event(pending_task, digest_char='e')
        initial = active_pm_inbox.read_inbox(self.store, self.session_key)
        self.assertEqual(initial['unreadable_task_count'], 0)
        self.assertEqual(len(initial['events']), 1)

        bad_path = self.store.root / (none_task['task_id'] + '.json')
        os.chmod(bad_path, 0o644)
        try:
            damaged = active_pm_inbox.read_inbox(
                TaskStore(self.store.root), self.session_key)
        finally:
            os.chmod(bad_path, 0o600)

        self.assertEqual(damaged['unreadable_task_count'], 1)
        self.assertEqual(damaged['status'], 'ATTENTION')
        self.assertEqual(len(damaged['events']), 1)

    def test_r3_r4_ready_handoff_rereads_before_reporting_events(self):
        task = self._record_pm()
        event = self._record_event(task, digest_char='b')
        scanned = self.store.get(task['task_id'])
        scanned['developer'] = {
            'route': 'agy',
            'session_id': 'synthetic-session',
            'source_sha': 'a' * 40,
            'direct_result_callback': {'state': 'PM_EVIDENCE_READY'},
        }
        handoff = {'packet': {'synthetic': True}}
        binding = active_pm_inbox._handoff_event(scanned, handoff)
        scanned['completion_events'] = [dict(event, event_id=binding['event_id'])]

        def scan_then_ack(_store, _session_key, *, include_idle=False):
            active_pm_inbox.ack(
                self.store, event['event_id'], self.session_key, task_id=task['task_id'])
            return [scanned], 0

        with patch.object(task_submission_cli, '_active_pm_handoff',
                          return_value=handoff), \
             patch.object(active_pm_inbox, '_session_records',
                          side_effect=scan_then_ack):
            after_ack = active_pm_inbox.read_inbox(self.store, self.session_key)

        self.assertEqual(after_ack['events'], [])
        self.assertEqual(after_ack['blocked'], [])
        self.assertEqual(after_ack['status'], 'OK')

        task_path = self.store.root / (task['task_id'] + '.json')

        def scan_then_damage(_store, _session_key, *, include_idle=False):
            os.chmod(task_path, 0o644)
            return [scanned], 0

        try:
            with patch.object(task_submission_cli, '_active_pm_handoff',
                              return_value=handoff), \
                 patch.object(active_pm_inbox, '_session_records',
                              side_effect=scan_then_damage):
                after_damage = active_pm_inbox.read_inbox(self.store, self.session_key)
        finally:
            os.chmod(task_path, 0o600)

        self.assertEqual(after_damage['status'], 'ATTENTION')
        self.assertEqual(len(after_damage['blocked']), 1)
        self.assertEqual(after_damage['blocked'][0]['task_id'], task['task_id'])

    def test_t2_synthetic_five_thousand_record_measurement(self):
        other_session = self._claude_session()
        realistic_request = (
            'Synthetic realistic PM request for inbox read measurement.\n' +
            ('Synthetic context detail line without sensitive material.\n' * 60)
        )
        self._populate_synthetic_store(
            2500, other_session=other_session, start_index=1,
            request_text=realistic_request)
        session_tasks = self._populate_synthetic_store(
            2500, other_session=self.session, start_index=2501,
            request_text=realistic_request)
        for index in range(32):
            self._record_event(session_tasks[index], digest_char='b')
        ready_task = session_tasks[32]

        # Warm the status index once before measuring steady-state reads and wait.
        initial = active_pm_inbox.read_inbox(self.store, self.session_key)
        self.assertEqual(len(initial['events']), 32)

        cpu_before = time.process_time()
        wall_before = time.monotonic()
        read_before = active_pm_inbox.read_inbox(
            TaskStore(self.store.root), self.session_key)
        read_before_cpu = time.process_time() - cpu_before
        read_before_elapsed = time.monotonic() - wall_before
        self.assertEqual(len(read_before['events']), 32)
        print(f'T2 read_inbox before change (records=5000, pending=32): '
              f'cpu_s={read_before_cpu:.4f} elapsed_s={read_before_elapsed:.4f}')

        self._record_event(ready_task, digest_char='c')
        cpu_before = time.process_time()
        wall_before = time.monotonic()
        read_after = active_pm_inbox.read_inbox(
            TaskStore(self.store.root), self.session_key)
        read_after_cpu = time.process_time() - cpu_before
        read_after_elapsed = time.monotonic() - wall_before
        self.assertEqual(len(read_after['events']), 33)
        print(f'T2 read_inbox after change (records=5000, pending=33): '
              f'cpu_s={read_after_cpu:.4f} elapsed_s={read_after_elapsed:.4f}')

        for item in session_tasks[:33]:
            for event in self.store.get(item['task_id']).get('completion_events', []):
                active_pm_inbox.ack(self.store, event['event_id'], self.session_key,
                                    task_id=item['task_id'])

        cpu_before = time.process_time()
        wall_before = time.monotonic()
        unchanged = active_pm_inbox.wait_inbox(
            self.store, self.session_key, wait=True, timeout=2, poll=0.2)
        unchanged_cpu = time.process_time() - cpu_before
        unchanged_elapsed = time.monotonic() - wall_before
        self.assertIs(unchanged['timed_out'], True)
        self.assertEqual(unchanged['events'], [])
        print(f'T2 unchanged wait (records=5000): cpu_s={unchanged_cpu:.4f} '
              f'elapsed_s={unchanged_elapsed:.4f}')

        recorded_at = []
        errors = []

        def writer():
            try:
                time.sleep(0.2)
                recorded_at.append(time.monotonic())
                self._record_event(ready_task, digest_char='d')
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            delivered = active_pm_inbox.wait_inbox(
                self.store, self.session_key, wait=True, timeout=10, poll=0.2)
            delivered_at = time.monotonic()
        finally:
            thread.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertIs(delivered['timed_out'], False)
        self.assertEqual(len(delivered['events']), 1)
        delay_s = delivered_at - recorded_at[0]
        print(f'T2 event delivery (records=5000): delay_s={delay_s:.4f}')


if __name__ == '__main__':
    unittest.main()
