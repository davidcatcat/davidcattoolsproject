"""Real offline POSIX files/locks/publication and conservative retention behavior."""

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from llmtool import diagnostics as d
from llmtool.diagnostics_settings import DiagnosticsSettings
from llmtool.diagnostics_store import (ABANDONED_GRACE_SECONDS, DiagnosticsStore,
    persistence_supported)
from llmtool.diagnostics_summary import RunSummary, parse_run_summary
from llmtool.diagnostics_runtime import RunContext


def event(identity, number, kind, timestamp=None, data=None):
    return d.DiagnosticEvent(run_id=identity, event_id=number,
        timestamp=d.utc_timestamp() if timestamp is None else timestamp,
        type=kind, component='cli.ask', severity=d.DiagnosticSeverity.INFO,
        data=data or ({'command': 'ask', 'mode': 'normal'} if kind is d.EventType.RUN_START else
                     {'status': 'success', 'duration_ms': 1}))


@unittest.skipUnless(persistence_supported(), 'requires POSIX diagnostics backend')
class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # macOS /var is a symlink: use the physical temp anchor, not a project checkout.
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / 'state'
        self.settings = DiagnosticsSettings(persist_metadata=True)

    def store(self, **limits):
        return DiagnosticsStore(replace(self.settings, **limits), home=self.home)

    def start(self, store=None, identity=None):
        store = self.store() if store is None else store
        writer = store.start(identity)
        self.assertIsNotNone(writer, store.warning)
        self.addCleanup(writer.close)
        return writer

    def complete(self, writer, stamp=None):
        first = event(writer.run_id, 1, d.EventType.RUN_START, stamp)
        last = event(writer.run_id, 2, d.EventType.RUN_END, stamp)
        self.assertTrue(writer.append(first))
        self.assertTrue(writer.append(last))
        summary = RunSummary(run_id=writer.run_id, mode=d.DiagnosticMode.NORMAL,
            status='success', started_at=first.timestamp, ended_at=last.timestamp,
            duration_ms=1, event_count=2, dropped_event_count=0, trace_bytes=writer.trace_bytes)
        self.assertTrue(writer.finish(summary), writer.warning)
        return self.home / 'runs' / writer.run_id

    def test_default_off_no_io_location_or_environment_access(self):
        with patch('os.open', side_effect=AssertionError('I/O')), \
                patch('llmtool.diagnostics_store.resolve_diagnostics_home', side_effect=AssertionError('resolver')):
            store = DiagnosticsStore(home='relative', platform='win32')
            self.assertIsNone(store.start())
            self.assertIsNone(store.warning)
        self.assertFalse(self.home.exists())

    def test_unsupported_backend_safe_bounded_warning_no_io(self):
        with patch('os.open', side_effect=AssertionError('I/O')):
            store = DiagnosticsStore(self.settings, home=self.home, platform='win32')
            self.assertIsNone(store.start())
        self.assertEqual(store.warning.code, d.ErrorCode.DIAGNOSTICS_STORE_UNAVAILABLE)
        self.assertNotIn(str(self.home), store.warning.line())
        self.assertLess(len(store.warning.line()), 256)

    def test_private_layout_atomic_completion_and_schema(self):
        writer = self.start()
        directory = self.home / 'runs' / writer.run_id
        self.assertTrue((directory / '.reservation').exists())
        self.assertTrue((directory / 'trace.partial.jsonl').exists())
        self.assertFalse((directory / 'trace.jsonl').exists())
        self.assertFalse((directory / 'summary.json').exists())
        self.complete(writer)
        self.assertTrue(writer.persisted)
        self.assertFalse((directory / '.reservation').exists())
        self.assertFalse((directory / 'trace.partial.jsonl').exists())
        summary = parse_run_summary((directory / 'summary.json').read_bytes())
        self.assertEqual(summary.trace_bytes, (directory / 'trace.jsonl').stat().st_size)
        self.assertEqual(summary.event_count, 2)
        for path in [self.home, *self.home.rglob('*')]:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)
        self.assertFalse(writer.append(event(writer.run_id, 3, d.EventType.RUN_END)))

    def test_existing_unsafe_permissions_not_chmodded(self):
        self.home.mkdir(mode=0o755)
        self.home.chmod(0o755)  # Preserve the unsafe fixture under a private umask.
        before = self.home.stat().st_mode
        store = self.store()
        self.assertIsNone(store.start())
        self.assertEqual(store.warning.code, d.ErrorCode.DIAGNOSTICS_STORE_UNSAFE)
        self.assertEqual(self.home.stat().st_mode, before)
        self.assertFalse((self.home / 'runs').exists())

    def test_wrong_owner_rejected(self):
        self.home.mkdir(mode=0o700)
        with patch('llmtool.diagnostics_store.os.geteuid', return_value=os.geteuid() + 1):
            store = self.store()
            self.assertIsNone(store.start())
        self.assertEqual(store.warning.code, d.ErrorCode.DIAGNOSTICS_STORE_UNSAFE)

    def test_symlink_components_runs_and_lock_rejected_without_target_mutation(self):
        target = self.base / 'target'
        target.mkdir(mode=0o700)
        self.home.symlink_to(target, target_is_directory=True)
        store = self.store()
        self.assertIsNone(store.start())
        self.assertEqual(list(target.iterdir()), [])
        self.home.unlink()
        self.home.mkdir(mode=0o700)
        (self.home / 'runs').symlink_to(target, target_is_directory=True)
        self.assertIsNone(self.store().start())
        self.assertEqual(list(target.iterdir()), [])
        (self.home / 'runs').unlink()
        (self.home / '.store.lock').unlink()
        victim = target / 'victim'
        victim.write_text('KEEP')
        (self.home / '.store.lock').symlink_to(victim)
        self.assertIsNone(self.store().start())
        self.assertEqual(victim.read_text(), 'KEEP')

    def test_non_directory_component_and_invalid_path_safe(self):
        self.home.write_text('KEEP')
        self.assertIsNone(self.store().start())
        self.assertEqual(self.home.read_text(), 'KEEP')
        for path in ('relative', str(self.base / '../escape'), str(self.base) + '\n'):
            store = DiagnosticsStore(self.settings, home=path)
            self.assertIsNone(store.start())
            self.assertNotIn(path, store.warning.line())

    def test_run_collision_regenerates_and_three_attempts_stop(self):
        writer = self.start(identity='a' * 32)
        second = self.start(identity='a' * 32)
        self.assertNotEqual(writer.run_id, second.run_id)
        store = self.store()
        with patch('llmtool.diagnostics.new_run_id', return_value='a' * 32) as make:
            self.assertIsNone(store.start('a' * 32))
        self.assertEqual(make.call_count, 2)
        self.assertEqual(store.warning.code, d.ErrorCode.DIAGNOSTICS_RUN_COLLISION)
        self.assertTrue((self.home / 'runs' / writer.run_id / '.reservation').exists())

    def test_active_run_kept_and_count_capacity_failure(self):
        writer = self.start(self.store(max_runs=1))
        store = self.store(max_runs=1)
        self.assertIsNone(store.start())
        self.assertEqual(store.warning.code, d.ErrorCode.DIAGNOSTICS_STORE_FULL)
        self.assertTrue((self.home / 'runs' / writer.run_id).exists())

    def test_active_reservations_enforce_total_capacity(self):
        limits = dict(max_run_bytes=65536, max_total_bytes=1048576)
        writers = [self.start(self.store(**limits)) for _ in range(16)]
        store = self.store(**limits)
        self.assertIsNone(store.start())
        self.assertEqual(store.warning.code, d.ErrorCode.DIAGNOSTICS_STORE_FULL)
        self.assertEqual(len(list((self.home / 'runs').iterdir())), 16)
        self.complete(writers[0])
        self.start(self.store(**limits))

    def test_age_and_count_cleanup_oldest_first(self):
        old = self.complete(self.start(), '2000-01-01T00:00:00.000Z')
        self.start()
        self.assertFalse(old.exists())
        # Use future times so age expiry cannot accidentally satisfy count test.
        first = self.complete(self.start(), '2099-01-01T00:00:00.000Z')
        second = self.complete(self.start(), '2099-01-02T00:00:00.000Z')
        third = self.start(self.store(max_runs=3))
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())
        self.assertTrue((self.home / 'runs' / third.run_id).exists())

    def test_byte_cleanup_preserves_unrelated_data(self):
        completed = self.complete(self.start())
        unrelated = self.home / 'unrelated'
        unrelated.write_bytes(b'K' * (1048576 - 65536 - 50))
        store = self.store(max_run_bytes=65536, max_total_bytes=1048576)
        self.start(store)
        self.assertFalse(completed.exists())
        self.assertEqual(unrelated.stat().st_size, 1048576 - 65536 - 50)

    def test_equal_completion_times_evict_by_run_id_under_count_and_byte_pressure(self):
        for pressure in ('count', 'bytes'):
            with self.subTest(pressure=pressure):
                self.home = self.base / pressure
                small_id, large_id = 'a' * 32, 'b' * 32
                stamp = '2099-01-01T00:00:00.000Z'
                # Create the lexically larger ID first and explicitly reverse
                # enumeration: filesystem ordering cannot make this test pass.
                large = self.complete(self.start(identity=large_id), stamp)
                small = self.complete(self.start(identity=small_id), stamp)
                active = self.start(self.store(max_run_bytes=65536), identity='c' * 32)
                unknown = self.home / 'runs' / 'unknown'
                unknown.write_bytes(b'KEEP')
                limits = dict(max_run_bytes=65536)
                if pressure == 'count':
                    limits['max_runs'] = 4
                else:
                    limits['max_total_bytes'] = 1048576
                    large_bytes = sum(p.stat().st_size for p in large.iterdir())
                    filler = self.home / 'unrelated'
                    filler.write_bytes(b'K' * (1048576 - 2 * 65536 - large_bytes - 4))
                original = os.listdir
                def reverse_run_order(directory):
                    names = original(directory)
                    if small_id in names and large_id in names:
                        return [large_id, small_id] + [n for n in names if n not in (small_id, large_id)]
                    return names
                with patch('llmtool.diagnostics_store.os.listdir', side_effect=reverse_run_order):
                    self.start(self.store(**limits), identity='d' * 32)
                self.assertFalse(small.exists())
                self.assertTrue(large.exists())
                self.assertTrue((self.home / 'runs' / active.run_id).exists())
                self.assertEqual(unknown.read_bytes(), b'KEEP')

    def test_unknown_files_and_invalid_completed_runs_never_deleted(self):
        directory = self.complete(self.start())
        (directory / 'foreign').write_text('KEEP')
        (directory / 'foreign').chmod(0o600)
        unrelated = self.home / 'runs' / 'unrelated'
        unrelated.mkdir(mode=0o700)
        (unrelated / 'evidence').write_text('KEEP')
        store = self.store(max_runs=2)
        self.assertIsNone(store.start())
        self.assertTrue((directory / 'foreign').exists())
        self.assertEqual((unrelated / 'evidence').read_text(), 'KEEP')

    def test_unrelated_large_data_disables_save_no_deletion(self):
        self.complete(self.start())
        foreign = self.home / 'foreign'
        foreign.write_bytes(b'Z' * 1048576)
        store = self.store(max_total_bytes=1048576, max_run_bytes=65536)
        self.assertIsNone(store.start())
        self.assertEqual(store.warning.code, d.ErrorCode.DIAGNOSTICS_STORE_FULL)
        self.assertEqual(foreign.stat().st_size, 1048576)

    def test_abandoned_grace_active_lock_then_recovery(self):
        writer = self.start(self.store(max_runs=1))
        directory = self.home / 'runs' / writer.run_id
        self.assertTrue(writer.append(event(writer.run_id, 1, d.EventType.RUN_START)))
        ancient = time.time() - ABANDONED_GRACE_SECONDS - 10
        for path in [*directory.iterdir(), directory]:
            os.utime(path, (ancient, ancient))
        self.assertIsNone(self.store(max_runs=1).start())  # Active lock wins over age.
        writer.close()
        for path in [*directory.iterdir(), directory]:
            os.utime(path, None)
        self.assertIsNone(self.store(max_runs=1).start())  # One-hour grace.
        for path in [*directory.iterdir(), directory]:
            os.utime(path, (ancient, ancient))
        self.start(self.store(max_runs=1))
        self.assertFalse(directory.exists())

    def test_malformed_reservation_and_symlink_trace_kept(self):
        writer = self.start(self.store(max_runs=1))
        directory = self.home / 'runs' / writer.run_id
        writer.close()
        (directory / '.reservation').write_text('{"run_id":"bad"}')
        victim = self.base / 'victim'
        victim.write_text('KEEP')
        (directory / 'trace.partial.jsonl').unlink()
        (directory / 'trace.partial.jsonl').symlink_to(victim)
        self.assertIsNone(self.store(max_runs=1).start())
        self.assertEqual(victim.read_text(), 'KEEP')
        self.assertTrue(directory.exists())

    def test_hardlinked_lock_rejected(self):
        self.home.mkdir(mode=0o700)
        victim = self.base / 'lock'
        victim.write_text('KEEP')
        victim.chmod(0o600)
        os.link(victim, self.home / '.store.lock')
        self.assertIsNone(self.store().start())
        self.assertEqual(victim.read_text(), 'KEEP')

    def test_write_failure_warning_once_partial_retained(self):
        writer = self.start()
        with patch('llmtool.diagnostics_store.os.write', side_effect=OSError('SECRET_RAW_BODY')):
            self.assertFalse(writer.append(event(writer.run_id, 1, d.EventType.RUN_START)))
        warning = writer.warning
        self.assertFalse(writer.append(event(writer.run_id, 1, d.EventType.RUN_START)))
        self.assertIs(writer.warning, warning)
        self.assertEqual(warning.code, d.ErrorCode.DIAGNOSTICS_WRITE_FAILED)
        self.assertNotIn('SECRET_RAW_BODY', warning.line())
        self.assertFalse(writer.persisted)
        self.assertTrue((self.home / 'runs' / writer.run_id / '.reservation').exists())

    def test_reused_store_and_parallel_writers_have_independent_warnings(self):
        store = self.store()
        with patch('llmtool.diagnostics_store.os.open', side_effect=PermissionError('PRIVATE')):
            self.assertIsNone(store.start())
        self.assertIsNotNone(store.warning)
        first = self.start(store)
        second = self.start(store)
        self.assertIsNone(store.warning)
        with patch('llmtool.diagnostics_store.os.write', side_effect=OSError('PRIVATE')):
            self.assertFalse(first.append(event(first.run_id, 1, d.EventType.RUN_START)))
        self.complete(second)
        self.assertIsNotNone(first.warning)
        self.assertIsNone(second.warning)
        self.assertTrue(second.persisted)

    def test_finish_fsync_failure_no_completed_summary(self):
        writer = self.start()
        first = event(writer.run_id, 1, d.EventType.RUN_START)
        last = event(writer.run_id, 2, d.EventType.RUN_END)
        writer.append(first)
        writer.append(last)
        summary = RunSummary(writer.run_id, d.DiagnosticMode.NORMAL, 'success', first.timestamp,
                             last.timestamp, 1, 2, 0, writer.trace_bytes)
        with patch('llmtool.diagnostics_store.os.fsync', side_effect=OSError('SECRET')):
            self.assertFalse(writer.finish(summary))
        self.assertFalse(writer.persisted)
        self.assertFalse((self.home / 'runs' / writer.run_id / 'summary.json').exists())

    def test_target_symlink_publication_does_not_clobber(self):
        writer = self.start()
        directory = self.home / 'runs' / writer.run_id
        first, last = event(writer.run_id, 1, d.EventType.RUN_START), event(writer.run_id, 2, d.EventType.RUN_END)
        writer.append(first)
        writer.append(last)
        victim = self.base / 'victim'
        victim.write_text('KEEP')
        (directory / 'trace.jsonl').symlink_to(victim)
        summary = RunSummary(writer.run_id, d.DiagnosticMode.NORMAL, 'success', first.timestamp,
                             last.timestamp, 1, 2, 0, writer.trace_bytes)
        self.assertFalse(writer.finish(summary))
        self.assertEqual(victim.read_text(), 'KEEP')

    def test_invalid_sequence_and_summary_never_published(self):
        writer = self.start()
        self.assertFalse(writer.append(event(writer.run_id, 2, d.EventType.RUN_START)))
        writer = self.start()
        writer.append(event(writer.run_id, 1, d.EventType.RUN_START))
        summary = RunSummary(writer.run_id, d.DiagnosticMode.NORMAL, 'success', d.utc_timestamp(),
                             d.utc_timestamp(), 1, 1, 0, writer.trace_bytes)
        self.assertFalse(writer.finish(summary))
        self.assertFalse(writer.persisted)

    def test_max_run_bytes_disables_writer_before_overflow(self):
        writer = self.start(self.store(max_run_bytes=65536))
        writer.append(event(writer.run_id, 1, d.EventType.RUN_START))
        # UTF-8 text is <=1 KiB, ensure_ascii JSON expands it enough to hit the
        # byte budget before the independent 32-event limit.
        message = '界' * 330
        for number in range(2, 100):
            obj = d.DiagnosticEvent(run_id=writer.run_id, event_id=number,
                timestamp=d.utc_timestamp(), type=d.EventType.ERROR, component='provider',
                severity=d.DiagnosticSeverity.ERROR,
                data={'code': 'PROVIDER_TIMEOUT', 'message': message})
            if not writer.append(obj):
                break
        self.assertFalse(writer.persisted)
        self.assertEqual(writer.warning.code, d.ErrorCode.DIAGNOSTICS_STORE_FULL)
        self.assertLessEqual((self.home / 'runs' / writer.run_id / 'trace.partial.jsonl').stat().st_size, 65536)

    def test_persisted_observer_metadata_excludes_private_markers(self):
        run = RunContext()
        private = ('PRIVATE_PROMPT', 'PRIVATE_RESPONSE', 'PRIVATE_HTTP_BODY', 'PRIVATE_REASONING',
                   'Bearer SECRET_AUTHORIZATION_VALUE', 'sk-abcdefghijklmnopqrstuv')
        run.request({'active_provider': 'default', 'providers': {'default': {
            'type': 'openai-compatible', 'api_key': private[-1], 'base_url': str(self.home)}}})
        run.response({'model': 'test-model', 'total_tokens': 3, 'elapsed_seconds': 0.1,
                      'prompt': private[0], 'content': private[1], 'body': private[2],
                      'reasoning_content': private[3], 'Authorization': private[4]})
        run.finish('success')
        writer = self.start(identity=run.run_id)
        for obj in run.events:
            self.assertTrue(writer.append(obj))
        summary = RunSummary(run.run_id, d.DiagnosticMode.NORMAL, 'success', run.events[0].timestamp,
            run.events[-1].timestamp, run.events[-1].data['duration_ms'], len(run.events),
            run.dropped_event_count, writer.trace_bytes, provider_type=run.provider_type)
        self.assertTrue(writer.finish(summary))
        raw = b''.join(p.read_bytes() for p in (self.home / 'runs' / run.run_id).iterdir())
        for marker in (*private, str(self.home)):
            self.assertNotIn(marker.encode(), raw)

    def test_held_store_lock_bounded_unavailable_no_extra_run(self):
        import fcntl
        self.complete(self.start())
        descriptor = os.open(self.home / '.store.lock', os.O_RDWR)
        self.addCleanup(os.close, descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store = self.store()
        with patch('llmtool.diagnostics_store.time.monotonic', side_effect=[0, 3]):
            self.assertIsNone(store.start())
        self.assertEqual(store.warning.code, d.ErrorCode.DIAGNOSTICS_STORE_UNAVAILABLE)
        self.assertEqual(len(list((self.home / 'runs').iterdir())), 1)

    def test_permission_failure_safe_without_raw_exception(self):
        store = self.store()
        with patch('llmtool.diagnostics_store.os.open', side_effect=PermissionError('PRIVATE_PATH')):
            self.assertIsNone(store.start())
        self.assertNotIn('PRIVATE_PATH', store.warning.line())
        self.assertFalse(self.home.exists())

    def test_partial_writes_are_completed(self):
        writer = self.start()
        original = os.write
        def short_write(fd, raw):
            return original(fd, raw[:11])
        with patch('llmtool.diagnostics_store.os.write', side_effect=short_write):
            self.complete(writer)
        self.assertTrue(writer.persisted)

    def test_two_processes_reserve_distinct_ids_and_protect_capacity(self):
        source = '''
import sys
from llmtool.diagnostics_settings import DiagnosticsSettings
from llmtool.diagnostics_store import DiagnosticsStore
s = DiagnosticsStore(DiagnosticsSettings(persist_metadata=True, max_runs=2), home=sys.argv[1])
w = s.start()
print(w.run_id if w else 'FAILED:' + s.warning.code.value, flush=True)
sys.stdin.readline()
if w: w.close()
'''
        workers = []
        try:
            for _ in range(2):
                worker = subprocess.Popen([sys.executable, '-c', source, str(self.home)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                workers.append(worker)
            identities = [w.stdout.readline().strip() for w in workers]
            for identity in identities:
                self.assertRegex(identity, r'^[0-9a-f]{32}$')
            self.assertNotEqual(*identities)
            store = self.store(max_runs=2)
            self.assertIsNone(store.start())
            self.assertEqual(store.warning.code, d.ErrorCode.DIAGNOSTICS_STORE_FULL)
        finally:
            for worker in workers:
                _, err = worker.communicate('\n', timeout=10)
                self.assertEqual(worker.returncode, 0, err)
