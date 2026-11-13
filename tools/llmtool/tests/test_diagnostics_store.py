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
from llmtool.diagnostics_reader import DiagnosticsReader
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

    def test_shared_writable_ancestor_rejected_sticky_ancestor_allowed(self):
        # #137 R26: nothing is created below a shared-writable, non-sticky ancestor.
        shared = self.base / 'shared'
        shared.mkdir(mode=0o700)
        self.addCleanup(shared.chmod, 0o700)
        self.home = shared / 'state'
        for mode in (0o777, 0o770, 0o722):
            with self.subTest(mode=oct(mode)):
                shared.chmod(mode)
                store = self.store()
                self.assertIsNone(store.start())
                self.assertEqual(store.warning.code, d.ErrorCode.DIAGNOSTICS_STORE_UNSAFE)
                self.assertEqual(list(shared.iterdir()), [])
        shared.chmod(0o1777)
        self.start()
        self.assertTrue((self.home / 'runs').is_dir())

    def test_ancestor_owner_must_be_root_or_current_user(self):
        from types import SimpleNamespace as N
        from llmtool import diagnostics_reader as r, diagnostics_store as s
        for check, error in ((s._ancestor, s._Unsafe), (r._ancestor, r.ReaderError)):
            check(N(st_mode=stat.S_IFDIR | 0o755, st_uid=0))
            check(N(st_mode=stat.S_IFDIR | 0o1777, st_uid=os.geteuid()))
            for info in (N(st_mode=stat.S_IFDIR | 0o755, st_uid=os.geteuid() + 1),
                         N(st_mode=stat.S_IFREG | 0o755, st_uid=0)):
                with self.subTest(info=info), self.assertRaises(error):
                    check(info)

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

    def test_durable_finish_survives_reader_timeout_then_capacity_reconciles(self):
        limits = dict(max_run_bytes=65536, max_total_bytes=1048576)
        writer = self.start(self.store(**limits))
        reader = DiagnosticsReader(home=self.home)
        # Actual reader root SH remains held beyond the writer's simulated
        # two-second deadline. Publication is independent of the root lock.
        with reader._store():
            with patch('llmtool.diagnostics_store.time.monotonic', side_effect=[0, .1, 2.1]), \
                    patch('llmtool.diagnostics_store.time.sleep') as sleeping:
                directory = self.complete(writer)
            sleeping.assert_called_once_with(.01)
            self.assertTrue(writer.persisted)
            self.assertIsNone(writer.warning)
            self.assertTrue(writer.closed)
            self.assertTrue((directory / '.reservation').exists())
            self.assertEqual(reader.trace(writer.run_id)['state'], 'complete')
        filler = self.home / 'unrelated'
        filler.write_bytes(b'K' * (1048576 - 65536 - 4096))
        before = {name: (directory / name).read_bytes() for name in ('trace.jsonl', 'summary.json')}
        # Charging the stale 64 KiB reservation would force eviction here.
        next_writer = self.start(self.store(**limits))
        self.assertTrue(directory.exists())
        self.assertFalse((directory / '.reservation').exists())
        self.assertTrue((self.home / 'runs' / next_writer.run_id / '.reservation').exists())
        self.assertEqual({name: (directory / name).read_bytes() for name in before}, before)
        self.assertEqual(filler.stat().st_size, 1048576 - 65536 - 4096)
        self.assertEqual(reader.trace(writer.run_id)['state'], 'complete')

    def test_durable_finish_survives_reservation_unlink_failure(self):
        writer = self.start()
        original_unlink = os.unlink
        def unlink(name, *args, **kwargs):
            if name == '.reservation':
                raise PermissionError('PRIVATE_PATH')
            return original_unlink(name, *args, **kwargs)
        with patch('llmtool.diagnostics_store.os.unlink', side_effect=unlink):
            directory = self.complete(writer)
        self.assertTrue(writer.persisted)
        self.assertIsNone(writer.warning)
        self.assertTrue((directory / '.reservation').exists())
        self.assertEqual(DiagnosticsReader(home=self.home).trace(writer.run_id)['state'], 'complete')
        self.start()
        self.assertFalse((directory / '.reservation').exists())

    def test_stale_reservation_reconciliation_preserves_ineligible_runs(self):
        for damage in ('reservation', 'summary', 'trace', 'unknown', 'unsafe', 'incomplete', 'busy'):
            with self.subTest(damage=damage):
                self.home = self.base / damage
                writer = self.start(self.store(max_run_bytes=65536))
                with DiagnosticsReader(home=self.home)._store(), \
                        patch('llmtool.diagnostics_store.time.monotonic', side_effect=[0, 3]):
                    directory = self.complete(writer)
                descriptor = None
                if damage in ('reservation', 'summary', 'trace'):
                    name = { 'reservation': '.reservation', 'summary': 'summary.json',
                             'trace': 'trace.jsonl'}[damage]
                    (directory / name).write_bytes(b'{}')
                elif damage == 'unknown':
                    (directory / 'foreign').write_bytes(b'KEEP')
                    (directory / 'foreign').chmod(0o600)
                elif damage == 'unsafe':
                    (directory / '.reservation').chmod(0o644)
                elif damage == 'incomplete':
                    (directory / 'summary.json').unlink()
                else:
                    import fcntl
                    descriptor = os.open(directory / '.run.lock', os.O_RDONLY)
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                before = {p.name: (p.read_bytes(), p.stat().st_mode) for p in directory.iterdir()}
                filler = self.home / 'unrelated'
                filler.write_bytes(b'K' * (1048576 - 65536 - 4096))
                store = self.store(max_run_bytes=65536, max_total_bytes=1048576)
                try:
                    self.assertIsNone(store.start())
                    self.assertEqual(store.warning.code, d.ErrorCode.DIAGNOSTICS_STORE_FULL)
                    self.assertEqual({p.name: (p.read_bytes(), p.stat().st_mode) for p in directory.iterdir()},
                                     before)
                finally:
                    if descriptor is not None:
                        os.close(descriptor)

    def test_reconciled_candidate_names_allow_count_eviction(self):
        writer = self.start()
        with DiagnosticsReader(home=self.home)._store(), \
                patch('llmtool.diagnostics_store.time.monotonic', side_effect=[0, 3]):
            directory = self.complete(writer)
        self.start(self.store(max_runs=1))
        self.assertFalse(directory.exists())

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

    def test_start_cleanup_close_error_still_releases_remaining_descriptors(self):
        def directory_descriptor_count():
            count = 0
            for name in os.listdir('/dev/fd'):
                try:
                    if stat.S_ISDIR(os.fstat(int(name)).st_mode):
                        count += 1
                except (OSError, ValueError):
                    pass
            return count
        before = directory_descriptor_count()
        real_close = os.close
        calls = [0]
        def closing(fd):
            calls[0] += 1
            if calls[0] == 1:
                real_close(fd)
                raise OSError('injected first transition close fault')
            return real_close(fd)
        with patch('llmtool.diagnostics_store.os.close', side_effect=closing):
            self.assertIsNone(self.store().start())
        self.assertGreaterEqual(calls[0], 2)
        self.assertEqual(directory_descriptor_count(), before)

    def test_partial_writes_are_completed(self):
        writer = self.start()
        original = os.write
        def short_write(fd, raw):
            return original(fd, raw[:11])
        with patch('llmtool.diagnostics_store.os.write', side_effect=short_write):
            self.complete(writer)
        self.assertTrue(writer.persisted)

    def test_start_initialization_faults_rollback_then_save_recovers(self):
        from llmtool import diagnostics_store as module
        faults = ('mkdir', 'directory-open', 'constructor', 'lock-create', 'lock-acquire',
                  'reservation-create', 'reservation-write', 'reservation-short-write',
                  'reservation-fsync', 'trace-create', 'directory-fsync', 'publication-unavailable')
        for fault in faults:
            with self.subTest(fault=fault):
                self.home = self.base / fault
                identity = 'a' * 32
                store = self.store(max_runs=1)
                real_open, real_write, real_sync = os.open, os.write, os.fsync
                real_mkdir, real_constructor = os.mkdir, module.StoredRun
                real_publish = module._publish_run_directory
                calls = [0]
                import fcntl
                real_flock = fcntl.flock
                blocker = []
                def opening(name, *args, **kwargs):
                    if fault == 'directory-open' and str(name).startswith('.run-init-'):
                        calls[0] += 1
                        if calls[0] == 1:
                            raise OSError('injected directory open fault')
                    if ((fault == 'lock-create' and name == '.run.lock') or
                            (fault == 'reservation-create' and name == '.reservation') or
                            (fault == 'trace-create' and name == 'trace.partial.jsonl')):
                        raise OSError('injected initialization fault')
                    return real_open(name, *args, **kwargs)
                def writing(fd, raw):
                    if fault in ('reservation-write', 'reservation-short-write'):
                        calls[0] += 1
                        if fault == 'reservation-short-write' and calls[0] == 1:
                            return real_write(fd, raw[:9])
                        raise OSError('injected write fault')
                    return real_write(fd, raw)
                def syncing(fd):
                    if ((fault == 'reservation-fsync' and stat.S_ISREG(os.fstat(fd).st_mode)) or
                            (fault == 'directory-fsync' and stat.S_ISDIR(os.fstat(fd).st_mode))):
                        raise OSError('injected fsync fault')
                    return real_sync(fd)
                def mkdir(name, *args, **kwargs):
                    if fault == 'mkdir' and str(name).startswith('.run-init-'):
                        raise OSError('injected mkdir fault')
                    return real_mkdir(name, *args, **kwargs)
                def flock(fd, operation):
                    if (fault == 'lock-acquire' and operation & fcntl.LOCK_NB and
                            os.fstat(fd).st_ino != (self.home / '.store.lock').stat().st_ino):
                        stage = next(self.home.glob('.run-init-*'))
                        descriptor = real_open(stage / '.run.lock', os.O_RDONLY)
                        self.addCleanup(os.close, descriptor)
                        real_flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        blocker.append(descriptor)
                    return real_flock(fd, operation)
                def constructor(*args):
                    if fault == 'constructor':
                        raise OSError('injected constructor fault')
                    return real_constructor(*args)
                def publish(*args):
                    if fault == 'publication-unavailable':
                        import errno
                        raise OSError(errno.ENOTSUP, 'unsupported primitive')
                    return real_publish(*args)
                with patch.object(module, 'StoredRun', side_effect=constructor), \
                        patch.object(module.os, 'open', side_effect=opening), \
                        patch.object(module.os, 'write', side_effect=writing), \
                        patch.object(module.os, 'fsync', side_effect=syncing), \
                        patch.object(module.os, 'mkdir', side_effect=mkdir), \
                        patch.object(module, '_publish_run_directory', side_effect=publish), \
                        patch.object(fcntl, 'flock', side_effect=flock):
                    self.assertIsNone(store.start(identity))
                self.assertFalse((self.home / 'runs' / identity).exists())
                stages = list(self.home.glob('.run-init-*'))
                if fault == 'mkdir':
                    self.assertEqual(stages, [])
                else:
                    self.assertEqual(len(stages), 1)  # Cleanup runs on the next store pass.
                    if fault == 'lock-acquire':
                        self.assertEqual(len(blocker), 1)
                        self.assertTrue((stages[0] / '.run.lock').exists())
                    else:
                        self.assertEqual(list(stages[0].iterdir()), [])
                self.complete(self.start(store, identity))  # max_runs=1 unchanged.

    def test_native_directory_publication_never_overwrites_existing_targets(self):
        from llmtool.diagnostics_store import _publish_run_directory
        self.home.mkdir(mode=0o700)
        source = self.home / 'source'
        target = self.home / 'target'
        victim = self.base / 'victim'
        victim.mkdir(mode=0o700)
        descriptor = os.open(self.home, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, descriptor)
        for kind in ('empty-directory', 'symlink'):
            with self.subTest(kind=kind):
                source.mkdir(mode=0o700)
                if kind == 'empty-directory':
                    target.mkdir(mode=0o700)
                else:
                    target.symlink_to(victim, target_is_directory=True)
                before = target.lstat()
                with self.assertRaises(OSError):
                    _publish_run_directory(descriptor, 'source', descriptor, 'target')
                self.assertEqual((target.lstat().st_dev, target.lstat().st_ino),
                                 (before.st_dev, before.st_ino))
                self.assertTrue(source.is_dir())
                if kind == 'empty-directory':
                    target.rmdir()
                else:
                    target.unlink()
                source.rmdir()

    def test_unavailable_native_directory_publication_has_no_rename_fallback(self):
        import ctypes
        from llmtool.diagnostics_store import _publish_run_directory
        class MissingLibrary:
            pass
        with patch.object(ctypes, 'CDLL', return_value=MissingLibrary()), \
                patch('os.rename', side_effect=AssertionError('overwrite fallback')):
            with self.assertRaises(OSError):
                _publish_run_directory(-1, 'source', -1, 'target')

    def test_mkdir_replacement_before_initial_stat_is_preserved(self):
        from llmtool import diagnostics_store as module
        real_mkdir = os.mkdir
        saved = self.base / 'original-created-directory'
        replaced = []
        def mkdir(name, *args, **kwargs):
            real_mkdir(name, *args, **kwargs)
            if str(name).startswith('.run-init-'):
                stage = self.home / name
                stage.rename(saved)
                real_mkdir(stage, mode=0o700)
                replaced.append((stage, stage.stat().st_ino))
        with patch.object(module.os, 'mkdir', side_effect=mkdir), \
                patch.object(module, '_write', side_effect=OSError('later initialization fault')):
            self.assertIsNone(self.store(max_runs=1).start())
        self.assertEqual(len(replaced), 1)
        stage, inode = replaced[0]
        self.assertTrue(stage.is_dir())
        self.assertEqual(stage.stat().st_ino, inode)
        self.assertEqual(list(stage.iterdir()), [])
        self.assertTrue(saved.is_dir())
        self.complete(self.start(self.store(max_runs=1)))
        self.assertFalse(stage.exists())
        self.assertTrue(saved.is_dir())

    def test_postpublication_fsync_failure_preserves_valid_run_until_grace(self):
        from llmtool import diagnostics_store as module
        real_publish, real_sync = module._publish_run_directory, os.fsync
        published = [False]
        def publish(*args):
            real_publish(*args)
            published[0] = True
        def sync(fd):
            if published[0] and stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError('postpublication fsync fault')
            return real_sync(fd)
        store = self.store(max_runs=1)
        identity = 'a' * 32
        with patch.object(module, '_publish_run_directory', side_effect=publish), \
                patch.object(module.os, 'fsync', side_effect=sync):
            self.assertIsNone(store.start(identity))
        directory = self.home / 'runs' / identity
        self.assertTrue(directory.exists())
        self.assertEqual(set(p.name for p in directory.iterdir()),
                         {'.run.lock', '.reservation', 'trace.partial.jsonl'})
        # Actual lock acquisition proves all failed writer descriptors were closed.
        import fcntl
        descriptor = os.open(directory / '.run.lock', os.O_RDONLY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(descriptor)
        self.assertIsNone(store.start())
        ancient = time.time() - ABANDONED_GRACE_SECONDS - 10
        for path in [*directory.iterdir(), directory]:
            os.utime(path, (ancient, ancient))
        self.complete(self.start(store))
        self.assertFalse(directory.exists())

    def test_publication_namespace_races_preserve_unknown_data(self):
        from llmtool import diagnostics_store as module
        real_publish = module._publish_run_directory
        for location in ('source', 'target'):
            with self.subTest(location=location):
                self.home = self.base / location
                identity = 'a' * 32
                kept = self.base / (location + '-verified-stage')
                def publish(parent, stage, runs, target):
                    if location == 'source':
                        source = self.home / stage
                        source.rename(kept)
                        source.mkdir(mode=0o700)
                        (source / 'UNKNOWN').write_bytes(b'KEEP')
                        real_publish(parent, stage, runs, target)
                    else:
                        destination = self.home / 'runs' / target
                        destination.mkdir(mode=0o700)
                        (destination / 'UNKNOWN').write_bytes(b'KEEP')
                        real_publish(parent, stage, runs, target)
                store = self.store()
                with patch.object(module, '_publish_run_directory', side_effect=publish):
                    self.assertIsNone(store.start(identity))
                destination = self.home / 'runs' / identity
                self.assertEqual((destination / 'UNKNOWN').read_bytes(), b'KEEP')
                if location == 'source':
                    self.assertTrue((kept / '.reservation').exists())
                    self.assertEqual(store.warning.code, d.ErrorCode.DIAGNOSTICS_STORE_UNSAFE)
                else:
                    self.assertEqual(list(next(self.home.glob('.run-init-*')).iterdir()), [])

    def test_unknown_staging_bytes_remain_capacity_accounted(self):
        writer = self.start()
        writer.close()
        stage = self.home / '.run-init-unknown'
        stage.mkdir(mode=0o700)
        evidence = stage / '.reservation'
        evidence.write_bytes(b'Z' * 1048576)
        evidence.chmod(0o600)
        store = self.store(max_total_bytes=1048576, max_run_bytes=65536)
        self.assertIsNone(store.start())
        self.assertEqual(store.warning.code, d.ErrorCode.DIAGNOSTICS_STORE_FULL)
        self.assertEqual(evidence.stat().st_size, 1048576)

    def test_empty_private_stages_cleaned_but_other_stages_retained(self):
        self.home.mkdir(mode=0o700)
        prefix = '.run-init-'
        empty = self.home / (prefix + 'a' * 32)
        empty.mkdir(mode=0o700)
        nonempty = self.home / (prefix + 'b' * 32)
        nonempty.mkdir(mode=0o700)
        (nonempty / 'keep').write_text('KEEP')
        symlink = self.home / (prefix + 'c' * 32)
        symlink.symlink_to(self.base, target_is_directory=True)
        wrong_mode = self.home / (prefix + 'd' * 32)
        wrong_mode.mkdir(mode=0o700)
        wrong_mode.chmod(0o755)
        malformed = self.home / (prefix + 'E' * 32)
        malformed.mkdir(mode=0o700)
        self.start()

        self.assertFalse(empty.exists())
        self.assertEqual((nonempty / 'keep').read_text(), 'KEEP')
        self.assertTrue(symlink.is_symlink())
        self.assertTrue(wrong_mode.is_dir())
        self.assertTrue(malformed.is_dir())
        other_owner = self.home / (prefix + 'f' * 32)
        other_owner.mkdir(mode=0o700)
        from llmtool.diagnostics_store import _cleanup_empty_stages
        descriptor = os.open(self.home, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, descriptor)
        with patch('llmtool.diagnostics_store.os.geteuid', return_value=os.geteuid() + 1):
            _cleanup_empty_stages(descriptor, [other_owner.name])
        self.assertTrue(other_owner.is_dir())

    def test_empty_stage_cleanup_failure_does_not_fail_start(self):
        from llmtool import diagnostics_store as module
        with patch.object(module.os, 'rmdir', side_effect=PermissionError('injected')):
            writer = self.start()
        self.assertIsNotNone(writer)

    def test_initialization_reservation_short_writes_complete(self):
        original = os.write
        def short(fd, raw):
            return original(fd, raw[:9])
        with patch('llmtool.diagnostics_store.os.write', side_effect=short):
            writer = self.start()
        directory = self.home / 'runs' / writer.run_id
        reservation = json.loads((directory / '.reservation').read_bytes())
        self.assertEqual(reservation['run_id'], writer.run_id)
        self.complete(writer)

    def test_start_rollback_preserves_unprovable_inventory(self):
        from llmtool import diagnostics_store as module
        for damage in ('unknown', 'replacement', 'hardlink', 'symlink', 'directory'):
            with self.subTest(damage=damage):
                self.home = self.base / damage
                identity = 'a' * 32
                saved = self.base / (damage + '-saved')
                victim = self.base / (damage + '-victim')
                victim.write_bytes(b'KEEP')
                victim.chmod(0o600)
                def fail(fd, raw):
                    directory = next(self.home.glob('.run-init-*'))
                    temporary = directory / '.reservation'
                    if damage == 'unknown':
                        (directory / 'UNKNOWN').write_bytes(b'KEEP')
                        (directory / 'UNKNOWN').chmod(0o600)
                    elif damage == 'directory':
                        directory.rename(saved)
                        directory.mkdir(mode=0o700)
                        (directory / 'UNKNOWN').write_bytes(b'KEEP')
                    elif damage == 'hardlink':
                        os.link(temporary, saved)
                    else:
                        temporary.unlink()
                        if damage == 'replacement':
                            temporary.write_bytes(b'KEEP')
                            temporary.chmod(0o600)
                        else:
                            temporary.symlink_to(victim)
                    raise OSError('injected after mutation')
                with patch.object(module, '_write', side_effect=fail):
                    self.assertIsNone(self.store().start(identity))
                directory = next(self.home.glob('.run-init-*'))
                self.assertTrue(directory.exists())
                self.assertEqual(victim.read_bytes(), b'KEEP')
                if damage == 'directory':
                    self.assertTrue(saved.exists())
                    self.assertEqual((directory / 'UNKNOWN').read_bytes(), b'KEEP')
                elif damage == 'unknown':
                    self.assertEqual((directory / 'UNKNOWN').read_bytes(), b'KEEP')
                elif damage == 'replacement':
                    self.assertEqual((directory / '.reservation').read_bytes(), b'KEEP')
                elif damage == 'hardlink':
                    self.assertTrue(saved.exists())

    def test_capacity_reopen_preserves_mutated_replaced_and_active_candidates(self):
        import fcntl
        for damage in ('directory', 'lock', 'trace', 'trace-content', 'summary', 'unknown', 'active', 'reservation'):
            with self.subTest(damage=damage):
                self.home = self.base / damage
                writer = self.start()
                if damage == 'reservation':
                    with DiagnosticsReader(home=self.home)._store(), \
                            patch('llmtool.diagnostics_store.time.monotonic', side_effect=[0, 3]):
                        directory = self.complete(writer, '2099-01-01T00:00:00.000Z')
                else:
                    directory = self.complete(writer, '2099-01-01T00:00:00.000Z')
                store = self.store(max_runs=1)
                original = store._reopen_candidate
                descriptor = [None]
                changed = [False]
                def reopen(runs, saved, now):
                    if not changed[0]:
                        changed[0] = True
                        if damage == 'directory':
                            directory.rename(self.base / 'retained-directory')
                            directory.mkdir(mode=0o700)
                        elif damage in ('lock', 'trace'):
                            name = '.run.lock' if damage == 'lock' else 'trace.jsonl'
                            raw = (directory / name).read_bytes()
                            (directory / name).unlink()
                            (directory / name).write_bytes(raw)
                            (directory / name).chmod(0o600)
                        elif damage in ('trace-content', 'summary', 'reservation'):
                            name = {'trace-content': 'trace.jsonl', 'summary': 'summary.json',
                                    'reservation': '.reservation'}[damage]
                            (directory / name).write_bytes(b'{}')
                        elif damage == 'unknown':
                            (directory / 'UNKNOWN').write_bytes(b'KEEP')
                            (directory / 'UNKNOWN').chmod(0o600)
                        else:
                            descriptor[0] = os.open(directory / '.run.lock', os.O_RDONLY)
                            fcntl.flock(descriptor[0], fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return original(runs, saved, now)
                try:
                    with patch.object(store, '_reopen_candidate', side_effect=reopen):
                        self.assertIsNone(store.start())
                    self.assertTrue(changed[0])
                    self.assertTrue(directory.exists())
                finally:
                    if descriptor[0] is not None:
                        os.close(descriptor[0])

    def test_initialization_crash_interruption_windows_remain_conservative(self):
        source = r'''import os, signal, sys
from unittest.mock import patch
from llmtool import diagnostics_store as m
from llmtool.diagnostics_settings import DiagnosticsSettings
stage = sys.argv[2]
real_write, real_publish = m._write, m._publish_run_directory
def die(): os.kill(os.getpid(), signal.SIGKILL)
def writing(fd, raw):
    if stage == 'partial':
        os.write(fd, raw[:9]); die()
    real_write(fd, raw)
def publishing(*args):
    if stage == 'prepublication': die()
    real_publish(*args)
    if stage == 'published': die()
with patch.object(m, '_write', writing), patch.object(m, '_publish_run_directory', publishing):
    m.DiagnosticsStore(DiagnosticsSettings(persist_metadata=True), home=sys.argv[1]).start('a' * 32)
'''
        for stage in ('partial', 'prepublication', 'published'):
            with self.subTest(stage=stage):
                self.home = self.base / stage
                result = subprocess.run([sys.executable, '-c', source, str(self.home), stage],
                                        capture_output=True, text=True, timeout=20)
                import signal
                self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
                directory = self.home / 'runs' / ('a' * 32)
                if stage in ('partial', 'prepublication'):
                    self.assertFalse(directory.exists())
                    unknown = next(self.home.glob('.run-init-*'))
                    before = {p.name: p.read_bytes() for p in unknown.iterdir()}
                    self.complete(self.start(self.store(max_runs=1)))
                    ancient = time.time() - ABANDONED_GRACE_SECONDS - 10
                    for path in [*unknown.iterdir(), unknown]:
                        os.utime(path, (ancient, ancient))
                    self.complete(self.start(self.store(max_runs=1)))
                    self.assertEqual({p.name: p.read_bytes() for p in unknown.iterdir()}, before)
                else:
                    self.assertEqual(list(self.home.glob('.run-init-*')), [])
                    self.assertEqual((directory / '.reservation').stat().st_nlink, 1)
                    self.assertIsNone(self.store(max_runs=1).start())
                    ancient = time.time() - ABANDONED_GRACE_SECONDS - 10
                    for path in [*directory.iterdir(), directory]:
                        os.utime(path, (ancient, ancient))
                    self.complete(self.start(self.store(max_runs=1)))
                    self.assertFalse(directory.exists())

    def test_low_nofile_capacity_cleanup_and_fd_lifetime_in_subprocess(self):
        source = r'''import fcntl, os, resource, sys
from llmtool import diagnostics as d
from llmtool.diagnostics_store import DiagnosticsStore
from llmtool.diagnostics_settings import DiagnosticsSettings
from llmtool.diagnostics_summary import RunSummary
resource.setrlimit(resource.RLIMIT_NOFILE, (64, resource.getrlimit(resource.RLIMIT_NOFILE)[1]))
def fd_link(fd):
    try: return os.readlink('/proc/self/fd/%d' % fd)
    except OSError: return 'unavailable'
def descriptors():
    result = set()
    for fd in range(64):
        try: fcntl.fcntl(fd, fcntl.F_GETFD)
        except OSError: continue
        # #1093: libffi can open its own /memfd:libffi descriptor; the store does not own it.
        if not fd_link(fd).startswith('/memfd:libffi'): result.add(fd)
    return result
baseline = descriptors()
baseline_links = {fd: fd_link(fd) for fd in baseline}
def fd_change(number):
    current = descriptors()
    added = ['%d=%s' % (fd, fd_link(fd)) for fd in sorted(current - baseline)]
    removed = ['%d=%s' % (fd, baseline_links[fd]) for fd in sorted(baseline - current)]
    return 'loop %d: added fds %s; removed fds %s' % (number, added, removed)
s = DiagnosticsStore(DiagnosticsSettings(persist_metadata=True, max_runs=80), home=sys.argv[1])
def complete(w):
    assert w is not None, s.warning
    stamp = '2099-01-01T00:00:00.000Z'
    first = d.DiagnosticEvent(run_id=w.run_id, event_id=1, timestamp=stamp, type=d.EventType.RUN_START, component='cli.ask', severity=d.DiagnosticSeverity.INFO, data={'command':'ask','mode':'normal'})
    last = d.DiagnosticEvent(run_id=w.run_id, event_id=2, timestamp=stamp, type=d.EventType.RUN_END, component='cli.ask', severity=d.DiagnosticSeverity.INFO, data={'status':'success','duration_ms':1})
    assert w.append(first) and w.append(last)
    summary = RunSummary(w.run_id, d.DiagnosticMode.NORMAL, 'success', stamp, stamp, 1, 2, 0, w.trace_bytes)
    assert w.finish(summary), w.warning
for number in range(80):
    complete(s.start('%032x' % number))
    assert descriptors() == baseline, fd_change(number)
assert len(os.listdir(os.path.join(sys.argv[1], 'runs'))) == 80
# Existing implementation holds 160 candidate descriptors, above the 64 soft limit.
for number in range(80, 90):
    complete(s.start('%032x' % number))
    assert descriptors() == baseline, fd_change(number)
assert len(os.listdir(os.path.join(sys.argv[1], 'runs'))) == 80
assert not os.path.exists(os.path.join(sys.argv[1], 'runs', '%032x' % 0))
print('NOFILE_64_RUNS_80_NO_LEAK_PASS')
'''
        result = subprocess.run([sys.executable, '-c', source, str(self.home)],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'NOFILE_64_RUNS_80_NO_LEAK_PASS')

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
