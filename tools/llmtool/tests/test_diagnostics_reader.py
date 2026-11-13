"""Public offline reader behavior, real POSIX containment and adversarial snapshots."""

import calendar
import io
import json
import os
import stat
import tempfile
import threading
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

from llmtool import cli, diagnostics as d
from llmtool import diagnostics_reader as r
from llmtool.diagnostics_settings import DiagnosticsSettings
from llmtool.diagnostics_store import DiagnosticsStore, persistence_supported
from llmtool import diagnostics_store
from llmtool.diagnostics_summary import RunSummary

RUN_ID = 'a' * 32
PROMPT = 'PRIVATE_PROMPT_MARKER'
RESPONSE = 'PRIVATE_RESPONSE_MARKER'


def event(number, kind, data, *, run_id=RUN_ID, timestamp=None, component='cli.ask', severity=None):
    severity = severity or ('error' if kind is d.EventType.ERROR else
                            'warning' if kind is d.EventType.WARNING else 'info')
    return d.DiagnosticEvent(run_id=run_id, event_id=number,
        timestamp=timestamp or '2026-10-04T00:00:00.000Z', type=kind,
        component=component, severity=d.DiagnosticSeverity(severity), data=data)


# Default fixture events are stamped 2026-10-04T00:00:00Z. Writer retention
# (max_age_days=14) compares completed-run ages with the wall clock, so a later
# DiagnosticsStore.start() would age-evict earlier fixtures once real time
# passes 2026-10-18. Pin only the writer's clock to the fixture instant.
FIXTURE_EPOCH = calendar.timegm((2026, 10, 4, 0, 0, 0))


@contextmanager
def fixture_clock():
    with patch.object(diagnostics_store.time, 'time', return_value=FIXTURE_EPOCH):
        yield


def sample_events(run_id=RUN_ID):
    return [event(1, d.EventType.RUN_START, {'command': 'ask', 'mode': 'normal'}, run_id=run_id),
            event(2, d.EventType.PROVIDER_REQUEST,
                  {'provider_type': 'openai', 'profile': 'test'}, run_id=run_id, component='provider.openai'),
            event(3, d.EventType.WARNING,
                  {'code': 'DIAGNOSTICS_EVENT_DROPPED', 'message': 'metadata omitted'}, run_id=run_id),
            event(4, d.EventType.ERROR,
                  {'code': 'PROVIDER_HTTP_ERROR', 'message': 'provider failed',
                   'safe_cause': {'http_status': 500}}, run_id=run_id, component='provider.openai'),
            event(5, d.EventType.RUN_END, {'status': 'error', 'duration_ms': 5},
                  run_id=run_id, severity='error')]


def snapshot(path):
    if not path.exists():
        return None
    result = {}
    for entry in [path, *path.rglob('*')]:
        info = entry.lstat()
        raw = (entry.read_bytes() if stat.S_ISREG(info.st_mode) else
               os.readlink(entry) if stat.S_ISLNK(info.st_mode) else None)
        result[str(entry.relative_to(path))] = (info.st_mode, info.st_uid, info.st_ino,
                                                info.st_mtime_ns, info.st_ctime_ns,
                                                info.st_nlink, raw)
    return result


@unittest.skipUnless(persistence_supported(), 'requires POSIX diagnostics backend')
class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / 'state'
        self.reader = r.DiagnosticsReader(home=self.home)

    def completed(self, run_id=RUN_ID):
        writer = DiagnosticsStore(DiagnosticsSettings(persist_metadata=True, max_runs=1000),
                                  home=self.home).start(run_id)
        self.assertIsNotNone(writer)
        self.addCleanup(writer.close)
        events = sample_events(run_id)
        for item in events:
            self.assertTrue(writer.append(item))
        summary = RunSummary(run_id=run_id, mode=d.DiagnosticMode.NORMAL, status='error',
            started_at=events[0].timestamp, ended_at=events[-1].timestamp, duration_ms=5,
            event_count=len(events), dropped_event_count=1, trace_bytes=writer.trace_bytes,
            provider_type='openai', final_error_code=d.ErrorCode.PROVIDER_HTTP_ERROR)
        self.assertTrue(writer.finish(summary))
        return self.home / 'runs' / run_id

    def rewrite_events(self, directory, values):
        raw = b''.join(json.dumps(value, ensure_ascii=True).encode() + b'\n' for value in values)
        (directory / 'trace.jsonl').write_bytes(raw)
        summary = json.loads((directory / 'summary.json').read_bytes())
        summary.update(trace_bytes=len(raw), event_count=len(values))
        (directory / 'summary.json').write_text(json.dumps(summary))
        return raw

    def invoke(self, argv, *, home=None):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {'LLMTOOL_HOME': str(home or self.home)}), \
                patch.object(cli.sys, 'stdout', out), patch.object(cli.sys, 'stderr', err):
            status = cli.main(argv)
        return status, out.getvalue(), err.getvalue()

    def assert_trace_failure(self, code=None, *, errors=False, json_modes=(False, True)):
        for use_json in json_modes:
            json_flag = ['--json'] if use_json else []
            argv = ['trace', RUN_ID, *json_flag, *(['--errors'] if errors else [])]
            status, out, err = self.invoke(argv)
            self.assertEqual(status, 1)
            self.assertEqual(out, '')
            self.assertNotIn(str(self.base), err)
            self.assertNotIn(PROMPT, err)
            self.assertNotIn(RESPONSE, err)
            self.assertNotIn('Traceback', err)
            if code:
                self.assertIn('code=' + code, err)

    def test_missing_store_and_runs_do_not_create_anything(self):
        before = snapshot(self.base)
        self.assertEqual(self.reader.runs()['runs'], [])
        self.assertEqual(self.invoke(['runs']), (0, 'No saved diagnostics runs.\n', ''))
        self.assert_trace_failure('NOT_FOUND')
        self.assertEqual(snapshot(self.base), before)
        self.home.mkdir(mode=0o700)
        before = snapshot(self.home)
        self.assertEqual(self.reader.runs()['runs'], [])
        self.assert_trace_failure('NOT_FOUND')
        self.assertEqual(snapshot(self.home), before)

    def test_saved_run_json_human_and_structured_filter(self):
        self.completed()
        value = self.reader.trace(RUN_ID)
        self.assertEqual(set(value), {'schema', 'schema_version', 'run_id', 'state', 'summary',
                                     'events', 'filtered', 'omitted_partial_line'})
        self.assertEqual(value['schema'], 'llmtool.diagnostics.trace')
        self.assertEqual(value['schema_version'], 1)
        self.assertEqual(value['state'], 'complete')
        self.assertEqual(value['summary']['status'], 'error')
        self.assertEqual([item['event_id'] for item in value['events']], [1, 2, 3, 4, 5])
        filtered = self.reader.trace(RUN_ID, errors=True)
        self.assertEqual([item['type'] for item in filtered['events']], ['warning', 'error'])
        self.assertEqual(filtered['summary'], value['summary'])
        self.assertTrue(filtered['filtered'])
        status, out, err = self.invoke(['trace', RUN_ID, '--errors', '--json'])
        self.assertEqual((status, err), (0, ''))
        self.assertEqual(json.loads(out), filtered)
        status, out, err = self.invoke(['trace', RUN_ID])
        self.assertEqual((status, err), (0, ''))
        self.assertIn('component=provider.openai', out)
        self.assertIn('PROVIDER_HTTP_ERROR', out)
        self.assertIn('state=complete', out)

    def test_reader_never_resolves_config_credentials_provider_or_auth(self):
        self.completed()
        before = snapshot(self.home)
        with ExitStack() as stack:
            for name in ('llmtool.cli.resolve_config', 'llmtool.cli.api_key',
                         'llmtool.cli.create_client', 'llmtool.cli.load_skill',
                         'llmtool.config._read_yaml',
                         'llmtool.diagnostics_settings.resolve_diagnostics_settings',
                         'builtins.open', 'pathlib.Path.open',
                         'socket.socket', 'subprocess.run', 'subprocess.Popen',
                         'llmtool.diagnostics_store.DiagnosticsStore.start'):
                stack.enter_context(patch(name, side_effect=AssertionError('forbidden access')))
            for argv in (['runs'], ['trace', RUN_ID], ['trace', RUN_ID, '--json'],
                         ['trace', RUN_ID, '--errors', '--json']):
                self.assertEqual(self.invoke(argv)[0], 0)
        self.assertEqual(snapshot(self.home), before)

    def test_no_mutation_calls_and_all_open_flags_readonly(self):
        self.completed()
        before = snapshot(self.home)
        original_open = os.open
        opened = []
        def read_only(name, flags, *args, **kwargs):
            self.assertEqual(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND), 0)
            self.assertTrue(flags & os.O_NOFOLLOW)
            opened.append(name)
            return original_open(name, flags, *args, **kwargs)
        with ExitStack() as stack:
            stack.enter_context(patch.object(r.os, 'open', side_effect=read_only))
            for name in ('mkdir', 'makedirs', 'write', 'unlink', 'rmdir', 'chmod', 'fchmod',
                         'rename', 'replace', 'link'):
                stack.enter_context(patch.object(os, name, side_effect=AssertionError('mutation')))
            self.assertEqual(self.reader.runs()['damaged_count'], 0)
            self.assertEqual(self.reader.trace(RUN_ID)['state'], 'complete')
        self.assertTrue(opened)
        self.assertTrue(all(str(name) == str(self.home.anchor) or str(name) in
                            (*self.home.parts, 'runs', RUN_ID, '.store.lock', '.run.lock',
                             'trace.jsonl', 'summary.json') for name in opened))
        self.assertEqual(snapshot(self.home), before)

    def test_run_id_and_unrequested_flags_rejected_without_echo(self):
        for identity in ('../x', '/tmp/x', 'A' * 32, 'a' * 31, 'a' * 33, PROMPT, 'a' * 32 + '\n'):
            status, out, err = self.invoke(['trace', identity, '--json'])
            self.assertEqual((status, out), (1, ''))
            self.assertIn('code=ID_INVALID', err)
            self.assertNotIn(identity, err)
        for argv in (['runs', '--json'], ['runs', '--errors'], ['trace', RUN_ID, '--provider', PROMPT],
                     ['trace', RUN_ID, '--save-diagnostics'], ['doctor', '--json']):
            with patch.object(cli.sys, 'stderr', io.StringIO()) as err, self.assertRaises(SystemExit) as caught:
                cli.main(argv)
            self.assertEqual(caught.exception.code, 2)
            self.assertEqual(err.getvalue(), 'llmtool: invalid command or options; use --help\n')
        self.assertFalse(self.home.exists())

    def test_supported_extensions_unknown_events_and_free_text_omitted(self):
        directory = self.completed()
        values = [item.to_dict() for item in sample_events()]
        values[0]['future'] = {'value': PROMPT}
        values[0]['data']['future'] = RESPONSE
        values[1]['data']['profile'] = PROMPT
        values[1]['data']['model'] = RESPONSE
        values[2]['type'] = 'future.action'
        values[2]['severity'] = 'error'
        values[2]['data'] = {'content': PROMPT}
        values[3]['component'] = 'private.component'
        values[3]['data']['message'] = PROMPT + ' ' + str(self.base)
        values[3]['data']['future'] = RESPONSE
        values[3]['data']['safe_cause']['future'] = RESPONSE
        self.rewrite_events(directory, values)
        summary = json.loads((directory / 'summary.json').read_bytes())
        summary['extension'] = {'value': PROMPT}
        (directory / 'summary.json').write_text(json.dumps(summary))
        value = self.reader.trace(RUN_ID)
        self.assertEqual(value['events'][2]['type'], 'unknown')
        self.assertEqual(value['events'][2]['component'], 'unknown')
        self.assertEqual(value['events'][2]['data'], {})
        self.assertEqual(value['events'][3]['component'], 'unknown')
        self.assertEqual(value['events'][3]['data']['safe_cause'], {'http_status': 500})
        filtered = self.reader.trace(RUN_ID, errors=True)
        self.assertEqual([item['event_id'] for item in filtered['events']], [4])
        for argv in (['runs'], ['trace', RUN_ID], ['trace', RUN_ID, '--json']):
            status, out, err = self.invoke(argv)
            self.assertEqual((status, err), (0, ''))
            for marker in (PROMPT, RESPONSE, str(self.base), 'future.action', 'private.component'):
                self.assertNotIn(marker, out + err)

    def test_invalid_event_schema_types_ids_sequence_controls_and_bounds(self):
        directory = self.completed()
        baseline = [item.to_dict() for item in sample_events()]
        cases = [('schema', 'other'), ('schema_version', True), ('event_id', True),
                 ('event_id', 2), ('run_id', 'b' * 32), ('timestamp', '2026-02-30T00:00:00.000Z'),
                 ('component', '../private'), ('severity', 'warning'), ('span_id', 3),
                 ('parent_span_id', 'b' * 16), ('data', []), ('type', 'future /private'),
                 ('schema_version', 0), ('schema_version', 2)]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                values = json.loads(json.dumps(baseline))
                values[0][key] = value
                self.rewrite_events(directory, values)
                self.assert_trace_failure('FORMAT_UNSUPPORTED' if key == 'schema_version' and type(value) is int else None,
                                          errors=True)
        for field in ('schema', 'schema_version', 'timestamp', 'parent_span_id', 'data'):
            with self.subTest(missing=field):
                values = json.loads(json.dumps(baseline))
                del values[0][field]
                self.rewrite_events(directory, values)
                self.assert_trace_failure(errors=True)
        for extra in ('bad\x1bcontrol', '\u202econtrol', 'sk-' + 'A' * 20,
                      'Bearer ' + 'A' * 20, 'x' * 2049, '\ud800'):
            with self.subTest(extra=extra[:12]):
                values = json.loads(json.dumps(baseline))
                values[0]['extension'] = extra
                self.rewrite_events(directory, values)
                self.assert_trace_failure(errors=True)
        for extra in ({'a': {'b': {'c': 'deep'}}}, list(range(65))):
            values = json.loads(json.dumps(baseline))
            values[0]['extension'] = extra
            self.rewrite_events(directory, values)
            self.assert_trace_failure(errors=True)

    def test_invalid_json_duplicates_nonfinite_utf8_and_line_bound(self):
        directory = self.completed()
        for raw in (b'{"schema":1,"schema":2}\n', b'{"x":NaN}\n', b'{"x":Infinity}\n',
                    b'{"x":1e309}\n', b'\xff\n', b'[]\n', b'\n',
                    b'{' + b' ' * d.EVENT_MAX_BYTES + b'}\n'):
            with self.subTest(raw=raw[:15]):
                (directory / 'trace.jsonl').write_bytes(raw)
                self.assert_trace_failure(errors=True)

    def test_exact_event_line_and_message_bounds_and_provider_scalars(self):
        directory = self.completed()
        values = [item.to_dict() for item in sample_events()]
        values[3]['data']['message'] = 'x' * 1024
        self.rewrite_events(directory, values)
        raw = (directory / 'trace.jsonl').read_bytes()
        first, rest = raw.split(b'\n', 1)
        padding = d.EVENT_MAX_BYTES - len(first)
        raw = first + b' ' * padding + b'\n' + rest
        (directory / 'trace.jsonl').write_bytes(raw)
        summary = json.loads((directory / 'summary.json').read_bytes())
        summary['trace_bytes'] = len(raw)
        (directory / 'summary.json').write_text(json.dumps(summary))
        self.assertEqual(self.reader.trace(RUN_ID)['state'], 'complete')
        (directory / 'trace.jsonl').write_bytes(first + b' ' * (padding + 1) + b'\n' + rest)
        self.assert_trace_failure('LIMIT_EXCEEDED')
        for key, bad in (('message', 'x' * 1025), ('retryable', 1),
                         ('fallback_allowed', True), ('safe_cause', {'http_status': True}),
                         ('safe_cause', {'http_status': 600}), ('code', 'future.error')):
            with self.subTest(key=key):
                values = [item.to_dict() for item in sample_events()]
                values[3]['data'][key] = bad
                self.rewrite_events(directory, values)
                self.assert_trace_failure(errors=True)
        values = [item.to_dict() for item in sample_events()]
        values[1]['type'] = 'provider.response'
        values[1]['data'] = {'provider_type': 'openai', 'duration_ms': 0,
                             'total_tokens': d.MAX_TOKEN_COUNT, 'token_usage_available': True}
        self.rewrite_events(directory, values)
        self.assertEqual(self.reader.trace(RUN_ID)['events'][1]['data']['total_tokens'], d.MAX_TOKEN_COUNT)
        for key, bad in (('duration_ms', -1), ('duration_ms', 1.0), ('total_tokens', True),
                         ('total_tokens', d.MAX_TOKEN_COUNT + 1), ('token_usage_available', 1)):
            with self.subTest(key=key):
                changed = json.loads(json.dumps(values))
                changed[1]['data'][key] = bad
                self.rewrite_events(directory, changed)
                self.assert_trace_failure(errors=True)

    def test_exact_event_count_and_default_directory_scan_bounds(self):
        directory = self.completed()
        values = [event(1, d.EventType.RUN_START, {'command': 'ask', 'mode': 'normal'})]
        values.extend(event(number, d.EventType.WARNING,
                            {'code': 'DIAGNOSTICS_EVENT_DROPPED', 'message': 'omitted'})
                      for number in range(2, 32))
        values.append(event(32, d.EventType.RUN_END, {'status': 'error', 'duration_ms': 5}, severity='error'))
        self.rewrite_events(directory, [item.to_dict() for item in values])
        self.assertEqual(len(self.reader.trace(RUN_ID)['events']), 32)
        for number in range(r.RUN_SCAN_LIMIT - 1):
            (self.home / 'runs' / ('unknown-' + str(number))).write_text('KEEP')
        self.assertEqual(self.reader.runs()['unknown_count'], 999)
        (self.home / 'runs' / 'overflow').write_text('KEEP')
        self.assertEqual(self.invoke(['runs'])[0], 1)

    def test_home_component_bound_rejects_before_open(self):
        path = Path('/' + '/'.join(['x'] * r.HOME_COMPONENT_LIMIT))
        with patch.object(r, 'resolve_diagnostics_home', return_value=path), \
                patch.object(r.os, 'open', side_effect=AssertionError('overdeep open')):
            self.assert_trace_failure('LIMIT_EXCEEDED')

    def test_summary_validation_and_completion_consistency(self):
        directory = self.completed()
        original = json.loads((directory / 'summary.json').read_bytes())
        cases = [('run_id', 'b' * 32), ('schema_version', 2), ('schema_version', True),
                 ('event_count', True), ('event_count', 33), ('trace_bytes', 0),
                 ('duration_ms', 6), ('mode', 'debug'), ('status', 'success'),
                 ('started_at', '2026-10-03T00:00:00.000Z'), ('provider_type', PROMPT),
                 ('final_error_code', PROMPT), ('persisted', False), ('command', PROMPT)]
        for key, value in cases:
            with self.subTest(key=key):
                summary = {**original, key: value}
                (directory / 'summary.json').write_text(json.dumps(summary))
                self.assert_trace_failure()
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'\xff', b' ' * 4097):
            (directory / 'summary.json').write_bytes(raw)
            self.assert_trace_failure()
        summary = dict(original)
        del summary['duration_ms']
        (directory / 'summary.json').write_text(json.dumps(summary))
        self.assert_trace_failure()
        (directory / 'summary.json').write_text(json.dumps(original))
        values = [item.to_dict() for item in sample_events()]
        values[-1]['type'] = 'future.end'
        self.rewrite_events(directory, values)
        self.assert_trace_failure()

    def test_unsafe_directories_files_owner_and_nonregular_inputs(self):
        directory = self.completed()
        for path in (self.home, self.home / 'runs', directory, directory / 'summary.json',
                     directory / 'trace.jsonl', directory / '.run.lock', self.home / '.store.lock'):
            with self.subTest(path=path.name):
                original = stat.S_IMODE(path.stat().st_mode)
                path.chmod(0o755 if path.is_dir() else 0o644)
                before = snapshot(self.home)
                self.assert_trace_failure()
                self.assertEqual(snapshot(self.home), before)
                path.chmod(original)
        with patch.object(r.os, 'geteuid', return_value=os.geteuid() + 1):
            self.assert_trace_failure()
        for filename in ('summary.json', 'trace.jsonl', '.run.lock', '.reservation'):
            path = directory / filename
            raw = path.read_bytes() if path.exists() else None
            if path.exists():
                path.unlink()
            path.mkdir(mode=0o700)
            self.assert_trace_failure()
            path.rmdir()
            os.mkfifo(path, 0o600)
            self.assert_trace_failure()
            path.unlink()
            if raw is not None:
                path.write_bytes(raw)
                path.chmod(0o600)

    def test_shared_writable_ancestor_rejected_without_reading(self):
        # #137 R26: refused below a shared-writable, non-sticky ancestor.
        self.completed()
        self.addCleanup(self.base.chmod, stat.S_IMODE(self.base.stat().st_mode))
        for mode in (0o777, 0o770):
            with self.subTest(mode=oct(mode)):
                self.base.chmod(mode)
                before = snapshot(self.home)
                self.assert_trace_failure()
                self.assertEqual(snapshot(self.home), before)
        self.base.chmod(0o1777)
        status, out, err = self.invoke(['trace', RUN_ID])
        self.assertEqual((status, err), (0, ''))

    def test_symlink_at_every_boundary_never_reads_target(self):
        directory = self.completed()
        victim = self.base / 'victim'
        victim.write_text(PROMPT)
        before = victim.read_bytes()
        for path in (self.home, self.home / 'runs', directory, directory / 'summary.json',
                     directory / 'trace.jsonl', directory / '.run.lock', self.home / '.store.lock'):
            with self.subTest(path=path.name):
                saved = path.with_name(path.name + '-saved')
                path.rename(saved)
                path.symlink_to(victim)
                self.assert_trace_failure()
                self.assertEqual(victim.read_bytes(), before)
                path.unlink()
                saved.rename(path)
        link = self.base / 'parent-link'
        link.symlink_to(self.base, target_is_directory=True)
        self.assertEqual(self.invoke(['runs'], home=link / 'state')[0], 1)
        self.assertEqual(victim.read_bytes(), before)

    def test_hardlinked_file_and_missing_locks_fail_without_repair(self):
        directory = self.completed()
        path = directory / 'summary.json'
        linked = self.base / 'hardlink'
        os.link(path, linked)
        before = snapshot(self.home)
        self.assert_trace_failure()
        self.assertEqual(snapshot(self.home), before)
        linked.unlink()
        for path in (self.home / '.store.lock', directory / '.run.lock'):
            raw = path.read_bytes()
            path.unlink()
            before = snapshot(self.home)
            self.assert_trace_failure()
            self.assertEqual(snapshot(self.home), before)
            if path.name == '.store.lock':
                self.assertEqual(self.invoke(['runs'])[0], 1)
            else:
                self.assertEqual(self.reader.runs()['damaged_count'], 1)
            path.write_bytes(raw)
            path.chmod(0o600)

    def test_active_run_never_reads_trace_or_claims_complete(self):
        writer = DiagnosticsStore(DiagnosticsSettings(persist_metadata=True), home=self.home).start(RUN_ID)
        self.addCleanup(writer.close)
        self.assertTrue(writer.append(sample_events()[0]))
        before = snapshot(self.home)
        with patch.object(r, '_read', side_effect=AssertionError('active bytes')):
            trace = self.reader.trace(RUN_ID)
            rows = self.reader.runs()['runs']
        self.assertEqual(trace['state'], 'incomplete')
        self.assertEqual(trace['events'], [])
        self.assertIsNone(trace['summary'])
        self.assertEqual(rows[0]['status'], 'incomplete')
        self.assertEqual(snapshot(self.home), before)

    def test_root_lock_busy_is_bounded_unavailable_and_releases(self):
        import fcntl
        self.completed()
        fd = os.open(self.home / '.store.lock', os.O_RDONLY)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = snapshot(self.home)
        with patch.object(r.time, 'monotonic', side_effect=[0, .1, .5, 2.1] * 3), \
                patch.object(r.time, 'sleep') as sleeping, \
                patch.object(r, '_names', side_effect=AssertionError('contended artifact scan')), \
                patch.object(r, '_read', side_effect=AssertionError('contended artifact read')):
            self.assert_trace_failure('STORE_UNAVAILABLE')
            status, out, err = self.invoke(['runs'])
            self.assertEqual((status, out), (1, ''))
            self.assertIn('code=STORE_UNAVAILABLE', err)
        self.assertEqual(sleeping.call_args_list, [unittest.mock.call(.01)] * 6)
        self.assertEqual(snapshot(self.home), before)
        fcntl.flock(fd, fcntl.LOCK_UN)
        self.assertEqual(self.reader.trace(RUN_ID)['state'], 'complete')

    def test_root_lock_retry_succeeds_after_writer_releases(self):
        import fcntl
        self.completed()
        before = snapshot(self.home)
        original_flock = fcntl.flock
        for method in ('runs', 'trace'):
            with self.subTest(method=method):
                fd = os.open(self.home / '.store.lock', os.O_RDONLY)
                try:
                    original_flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    operations = []
                    def flock(descriptor, operation):
                        operations.append(operation)
                        return original_flock(descriptor, operation)
                    def release(delay):
                        self.assertEqual(delay, .01)
                        original_flock(fd, fcntl.LOCK_UN)
                    with patch.object(r.time, 'monotonic', side_effect=[0, .1]), \
                            patch.object(r.time, 'sleep', side_effect=release) as sleeping, \
                            patch.object(fcntl, 'flock', side_effect=flock):
                        value = getattr(self.reader, method)(*([RUN_ID] if method == 'trace' else []))
                    sleeping.assert_called_once_with(.01)
                    self.assertEqual(operations, [fcntl.LOCK_SH | fcntl.LOCK_NB] * 3 +
                                     [fcntl.LOCK_UN] * 2)
                    self.assertEqual(value['state'] if method == 'trace' else value['runs'][0]['status'],
                                     'complete' if method == 'trace' else 'error')
                finally:
                    os.close(fd)
        self.assertEqual(snapshot(self.home), before)

    def test_busy_run_lock_never_retries_or_inspects_mutable_artifacts(self):
        import fcntl
        writer = DiagnosticsStore(DiagnosticsSettings(persist_metadata=True), home=self.home).start(RUN_ID)
        self.assertIsNotNone(writer)
        self.addCleanup(writer.close)
        original_flock, original_names, original_stat = fcntl.flock, r._names, os.stat
        for method in ('runs', 'trace'):
            with self.subTest(method=method):
                operations = []
                def flock(fd, operation):
                    operations.append(operation)
                    return original_flock(fd, operation)
                def names(fd, limit):
                    if os.fstat(fd).st_ino == os.fstat(writer.directory).st_ino:
                        raise AssertionError('busy run scanned')
                    return original_names(fd, limit)
                def statting(name, *args, **kwargs):
                    if name in r.RUN_FILES - {'.run.lock'}:
                        raise AssertionError('busy artifact statted')
                    return original_stat(name, *args, **kwargs)
                with patch.object(r.time, 'sleep', side_effect=AssertionError('busy run waited')), \
                        patch.object(r.time, 'monotonic', return_value=0) as clock, \
                        patch.object(fcntl, 'flock', side_effect=flock), \
                        patch.object(r, '_names', side_effect=names), \
                        patch.object(r.os, 'stat', side_effect=statting), \
                        patch.object(r, '_read', side_effect=AssertionError('busy bytes')):
                    value = getattr(self.reader, method)(*([RUN_ID] if method == 'trace' else []))
                self.assertEqual(clock.call_count, 1)  # Root only; no run deadline.
                self.assertEqual(operations, [fcntl.LOCK_SH | fcntl.LOCK_NB] * 2 + [fcntl.LOCK_UN])
                row = value if method == 'trace' else value['runs'][0]
                self.assertEqual(row.get('state', row.get('status')), 'incomplete')
                if method == 'trace':
                    self.assertEqual((row['events'], row['summary']), ([], None))

    def test_trace_io_and_validation_errors_have_distinct_safe_codes(self):
        self.completed()
        for target in ('_read', '_names'):
            for failure in (OSError(PROMPT), PermissionError(RESPONSE), ValueError(PROMPT),
                            TypeError(RESPONSE), KeyError(PROMPT), r.ReaderError('STORE_UNSAFE')):
                with self.subTest(target=target, failure=type(failure).__name__):
                    code = (failure.code if isinstance(failure, r.ReaderError) else
                            'STORE_UNAVAILABLE' if isinstance(failure, OSError) else 'FORMAT_INVALID')
                    with patch.object(r, target, side_effect=failure):
                        self.assert_trace_failure(code)
        with patch.object(r.os, 'read', side_effect=OSError(PROMPT)):
            self.assert_trace_failure('STORE_UNAVAILABLE')

    def test_partial_and_published_without_summary_remain_incomplete(self):
        directory = self.completed()
        (directory / 'summary.json').unlink()
        value = self.reader.trace(RUN_ID)
        self.assertEqual(value['state'], 'incomplete')
        self.assertEqual(len(value['events']), 5)
        self.assertIsNone(value['summary'])
        (directory / 'trace.jsonl').rename(directory / 'trace.partial.jsonl')
        raw = (sample_events()[0].to_json() + '\n{"unfinished":').encode()
        (directory / 'trace.partial.jsonl').write_bytes(raw)
        value = self.reader.trace(RUN_ID)
        self.assertEqual(value['state'], 'incomplete')
        self.assertEqual(len(value['events']), 1)
        self.assertTrue(value['omitted_partial_line'])
        self.assertEqual(self.reader.runs()['runs'][0]['status'], 'incomplete')
        (directory / 'trace.partial.jsonl').write_bytes(b'bad-complete-line\n')
        self.assert_trace_failure('FORMAT_INVALID')

    def test_completed_newline_and_both_trace_artifacts_reject(self):
        directory = self.completed()
        path = directory / 'trace.jsonl'
        original = path.read_bytes()
        path.write_bytes(original[:-1])
        self.assert_trace_failure()
        path.write_bytes(original)
        (directory / 'trace.partial.jsonl').write_bytes(b'')
        (directory / 'trace.partial.jsonl').chmod(0o600)
        self.assert_trace_failure()
        self.assertEqual(self.reader.runs()['damaged_count'], 1)

    def test_unknown_and_damaged_listing_entries_are_generic_and_preserved(self):
        directory = self.completed()
        unknown = self.home / 'runs' / (PROMPT + '\x1b')
        unknown.write_text(RESPONSE)
        (directory / 'summary.json').write_text('damaged ' + PROMPT)
        before = snapshot(self.home)
        result = self.reader.runs()
        self.assertEqual(result['runs'], [])
        self.assertEqual((result['damaged_count'], result['unknown_count']), (1, 1))
        status, out, err = self.invoke(['runs'])
        self.assertEqual((status, err), (0, ''))
        self.assertIn('damaged=1 unknown=1', out)
        self.assertNotIn(PROMPT, out)
        self.assertNotIn(RESPONSE, out)
        self.assertNotIn('\x1b', out)
        self.assertEqual(snapshot(self.home), before)

    def test_runs_reads_summaries_only_and_newest_order_with_ties(self):
        with fixture_clock():
            directories = [self.completed(identity) for identity in ('a' * 32, 'b' * 32, 'c' * 32)]
        summary = json.loads((directories[0] / 'summary.json').read_bytes())
        summary.update(started_at='2026-10-03T00:00:00.000Z', ended_at='2026-10-03T00:00:00.000Z')
        (directories[0] / 'summary.json').write_text(json.dumps(summary))
        original = r._read
        with patch.object(r, '_read', side_effect=lambda parent, name, limit:
                          original(parent, name, limit) if name == 'summary.json' else
                          self.fail('runs read trace content')):
            rows = self.reader.runs()['runs']
        self.assertEqual([row['run_id'] for row in rows], ['c' * 32, 'b' * 32, 'a' * 32])

    def test_inspect_store_reports_malformed_summary_metadata_as_degraded(self):
        directory = self.completed()
        healthy = self.reader.inspect_store()
        self.assertEqual((healthy['state'], healthy['metadata'], healthy['damaged_count'],
                          healthy['run_count'], healthy['active_count'], healthy['unknown_count']),
                         ('present', 'ok', 0, 1, 0, 0))
        summary_path = directory / 'summary.json'
        original = summary_path.read_bytes()
        raw_cases = (b'damaged\n', b'{"schema":1,"schema":1}\n', b'{"x":NaN}\n', b'\xff\n', b'')
        for raw in raw_cases:
            with self.subTest(raw=raw[:12]):
                summary_path.write_bytes(raw)
                summary_path.chmod(0o600)
                before = snapshot(self.home)
                value = self.reader.inspect_store()
                self.assertEqual((value['state'], value['metadata'], value['damaged_count']),
                                 ('present', 'degraded', 1))
                self.assertEqual(snapshot(self.home), before)
        original_value = json.loads(original)
        for key, value in (('schema_version', 2), ('schema_version', True),
                           ('run_id', 'b' * 32), ('event_count', d.MAX_RUN_EVENTS + 1),
                           ('status', 'unknown')):
            with self.subTest(key=key, value=value):
                summary_path.write_text(json.dumps({**original_value, key: value}))
                summary_path.chmod(0o600)
                self.assertEqual(self.reader.inspect_store()['metadata'], 'degraded')
        summary_path.write_bytes(original)
        summary_path.chmod(0o600)
        self.assertEqual(self.reader.inspect_store()['metadata'], 'ok')

    def test_inspect_store_never_reads_trace_and_flags_missing_trace(self):
        directory = self.completed()
        original_read = r._read
        read_names = []
        def summary_only(parent, name, limit):
            read_names.append(name)
            self.assertEqual(name, 'summary.json')
            return original_read(parent, name, limit)
        with patch.object(r, '_read', side_effect=summary_only):
            value = self.reader.inspect_store()
        self.assertEqual((value['metadata'], value['damaged_count'], read_names),
                         ('ok', 0, ['summary.json']))
        (directory / 'trace.jsonl').unlink()
        with patch.object(r, '_read', side_effect=AssertionError('trace or summary read')):
            value = self.reader.inspect_store()
        self.assertEqual((value['metadata'], value['damaged_count']), ('degraded', 1))
        (directory / 'summary.json').unlink()
        self.assertEqual(self.reader.inspect_store()['metadata'], 'ok')  # Incomplete, not damaged.

    def test_inspect_store_active_and_unknown_entries_stay_ok(self):
        writer = DiagnosticsStore(DiagnosticsSettings(persist_metadata=True), home=self.home).start(RUN_ID)
        self.assertIsNotNone(writer)
        self.addCleanup(writer.close)
        (self.home / 'runs' / 'unknown-entry').write_text('KEEP')
        with patch.object(r, '_read', side_effect=AssertionError('busy bytes')):
            value = self.reader.inspect_store()
        self.assertEqual((value['state'], value['run_count'], value['active_count'],
                          value['unknown_count'], value['damaged_count'], value['metadata']),
                         ('present', 1, 1, 1, 0, 'ok'))

    def test_inspect_store_counts_staging_bytes_without_reading_contents(self):
        DiagnosticsStore(DiagnosticsSettings(persist_metadata=True), home=self.home).start(RUN_ID).close()
        stage = self.home / ('.run-init-' + 'b' * 32)
        stage.mkdir(mode=0o700)
        reservation = stage / '.reservation'
        with reservation.open('wb') as stream:
            stream.truncate(1024 * 1024)
        reservation.chmod(0o600)
        unknown = self.home / 'unmanaged'
        (unknown / 'nested').mkdir(parents=True, mode=0o700)
        (unknown / 'nested' / 'payload').write_text('opaque')
        before = snapshot(self.home)
        value = self.reader.inspect_store()
        self.assertEqual((value['staging_count'], value['staging_bytes'],
                          value['root_unknown_count'], value['root_unknown_bytes']),
                         (1, 1024 * 1024, 1, 6))
        self.assertEqual(snapshot(self.home), before)

    def test_inspect_store_uses_writer_scan_limit_for_many_empty_stages(self):
        self.assertEqual(r.ROOT_SCAN_LIMIT, diagnostics_store.SCAN_LIMIT)
        DiagnosticsStore(DiagnosticsSettings(persist_metadata=True), home=self.home).start(RUN_ID).close()
        for number in range(1500):
            (self.home / ('.run-init-' + format(number, '032x'))).mkdir(mode=0o700)
        with patch.object(r, '_read', side_effect=AssertionError('contents read')):
            value = self.reader.inspect_store()
        self.assertEqual(value['staging_count'], 1500)
        self.assertFalse(value['staging_scan_limited'])

    def test_inspect_store_shares_scan_budget_across_stage_contents(self):
        DiagnosticsStore(DiagnosticsSettings(persist_metadata=True), home=self.home).start(RUN_ID).close()
        for number in range(2600):
            stage = self.home / ('.run-init-' + format(number, '032x'))
            stage.mkdir(mode=0o700)
            for part in range(3):
                file_path = stage / ('f' + str(part))
                file_path.touch(mode=0o600)
        value = self.reader.inspect_store()
        self.assertTrue(value['staging_scan_limited'])

    def test_inspect_store_accounts_unusual_stage_children_without_refusal(self):
        DiagnosticsStore(DiagnosticsSettings(persist_metadata=True), home=self.home).start(RUN_ID).close()
        stage = self.home / ('.run-init-' + 'c' * 32)
        stage.mkdir(mode=0o700)
        plain = stage / 'plain'
        plain.write_bytes(b'plain')
        plain.chmod(0o644)
        hardlink = stage / 'hardlink'
        os.link(plain, hardlink)
        external = self.base / 'external'
        external.write_bytes(b'not followed')
        (stage / 'symlink').symlink_to(external)
        value = self.reader.inspect_store()
        self.assertEqual(value['staging_count'], 1)
        self.assertEqual(value['staging_bytes'], len(b'plain') * 2 + len(str(external)))

    def test_inspect_store_unknown_scan_errors_and_depth_are_capacity_limited(self):
        DiagnosticsStore(DiagnosticsSettings(persist_metadata=True), home=self.home).start(RUN_ID).close()
        unknown = self.home / 'unmanaged'
        unknown.mkdir(mode=0o700)
        with patch.object(r, '_account_entry', side_effect=PermissionError('unreadable')):
            value = self.reader.inspect_store()
        self.assertTrue(value['staging_scan_limited'])

        current = unknown
        for _ in range(r.ACCOUNT_DEPTH_LIMIT + 2):
            current = current / 'd'
            current.mkdir(mode=0o700)
        value = self.reader.inspect_store()
        self.assertTrue(value['staging_scan_limited'])

    def test_inspect_store_charges_run_files_to_shared_scan_budget(self):
        DiagnosticsStore(DiagnosticsSettings(persist_metadata=True), home=self.home).start(RUN_ID).close()
        for number in range(8500):
            (self.home / ('.run-init-' + format(number, '032x'))).mkdir(mode=0o700)
        runs = self.home / 'runs'
        for number in range(499):
            directory = runs / format(number + 1, '032x')
            directory.mkdir(mode=0o700)
            for name in ('.run.lock', '.reservation', 'trace.partial.jsonl'):
                (directory / name).touch(mode=0o600)
        value = self.reader.inspect_store()
        self.assertTrue(value['staging_scan_limited'])

    def test_inspect_store_empty_stage_and_unsafe_stage(self):
        DiagnosticsStore(DiagnosticsSettings(persist_metadata=True), home=self.home).start(RUN_ID).close()
        stage = self.home / ('.run-init-' + 'b' * 32)
        stage.mkdir(mode=0o700)
        value = self.reader.inspect_store()
        self.assertEqual((value['staging_count'], value['staging_bytes']), (1, 0))
        stage.chmod(0o755)
        with self.assertRaises(r.ReaderError) as caught:
            self.reader.inspect_store()
        self.assertEqual(caught.exception.code, 'STORE_UNAVAILABLE')
        stage.chmod(0o700)
        stage_info = stage.lstat()
        original_private = r._private
        def foreign_owner(info, directory=False):
            if (info.st_dev, info.st_ino) == (stage_info.st_dev, stage_info.st_ino):
                raise r.ReaderError('STORE_UNSAFE')
            return original_private(info, directory)
        with patch.object(r, '_private', side_effect=foreign_owner):
            with self.assertRaises(r.ReaderError) as caught:
                self.reader.inspect_store()
        self.assertEqual(caught.exception.code, 'STORE_UNAVAILABLE')
        stage.rmdir()
        stage.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(r.ReaderError) as caught:
            self.reader.inspect_store()
        self.assertEqual(caught.exception.code, 'STORE_UNAVAILABLE')

    def test_inspect_store_absent_and_unsupported_metadata_states(self):
        value = self.reader.inspect_store()
        self.assertEqual((value['state'], value['metadata'], value['damaged_count'],
                          value['run_count'], value['active_count'], value['unknown_count']),
                         ('absent', 'ok', 0, 0, 0, 0))
        value = r.DiagnosticsReader(home=self.home, platform='win32').inspect_store()
        self.assertEqual((value['state'], value['metadata'], value['damaged_count']),
                         ('unsupported', 'unsupported', 0))

    def test_inspect_store_is_read_only_and_never_repairs_damage(self):
        directory = self.completed()
        (directory / 'summary.json').write_text(PROMPT)
        (directory / 'summary.json').chmod(0o600)
        before = snapshot(self.home)
        original_open = os.open
        def read_only(name, flags, *args, **kwargs):
            self.assertEqual(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND), 0)
            return original_open(name, flags, *args, **kwargs)
        with ExitStack() as stack:
            stack.enter_context(patch.object(r.os, 'open', side_effect=read_only))
            for name in ('mkdir', 'makedirs', 'write', 'unlink', 'rmdir', 'chmod', 'fchmod',
                         'rename', 'replace', 'link'):
                stack.enter_context(patch.object(os, name, side_effect=AssertionError('mutation')))
            value = self.reader.inspect_store()
        self.assertEqual((value['metadata'], value['damaged_count']), ('degraded', 1))
        self.assertEqual(snapshot(self.home), before)

    def test_runs_recorded_success_never_verifies_missing_completion_evidence(self):
        directory = self.completed()
        summary_path = directory / 'summary.json'
        trace_path = directory / 'trace.jsonl'
        summary = json.loads(summary_path.read_bytes())
        summary.update(status='success', final_error_code=None)
        values = [item.to_dict() for item in sample_events()]
        values[-1]['data']['status'] = 'success'
        values[-1]['severity'] = 'info'
        valid_trace = b''.join(json.dumps(value).encode() + b'\n' for value in values)
        for raw in (b'', b'malformed\n', b''.join(json.dumps(value).encode() + b'\n'
                                                for value in values[:-1])):
            with self.subTest(trace=raw[:20]):
                trace_path.write_bytes(raw)
                summary_path.write_text(json.dumps({**summary, 'trace_bytes': len(raw),
                                                    'event_count': raw.count(b'\n')}))
                before = snapshot(self.home)
                status, out, err = self.invoke(['runs'])
                self.assertEqual((status, err), (0, ''))
                self.assertIn('Saved summaries only; trace evidence unverified.', out)
                self.assertIn('recorded_status=success', out)
                self.assertNotIn(' status=success', out)
                self.assertNotIn('state=complete', out)
                self.assert_trace_failure('FORMAT_INVALID')
                self.assertEqual(snapshot(self.home), before)
        trace_path.unlink()
        self.assertEqual(self.reader.runs()['damaged_count'], 1)
        self.assert_trace_failure('FORMAT_INVALID')
        trace_path.write_bytes(valid_trace)
        trace_path.chmod(0o600)
        summary_path.unlink()
        for missing_trace in (False, True):
            if missing_trace:
                trace_path.unlink()
            before = snapshot(self.home)
            status, out, err = self.invoke(['runs'])
            self.assertEqual((status, err), (0, ''))
            self.assertIn('recorded_status=incomplete', out)
            self.assertNotIn('recorded_status=success', out)
            status, out, err = self.invoke(['trace', RUN_ID, '--json'])
            self.assertEqual((status, err), (0, ''))
            value = json.loads(out)
            self.assertEqual(value['state'], 'incomplete')
            self.assertIsNone(value['summary'])
            self.assertEqual(snapshot(self.home), before)

    def test_run_output_limit_and_scan_limit_include_unrelated_entries(self):
        directory = self.completed()
        summary = json.loads((directory / 'summary.json').read_bytes())
        for number in range(101):
            identity = format(number, '032x')
            extra = self.home / 'runs' / identity
            extra.mkdir(mode=0o700)
            for name, raw in (('.run.lock', b''), ('trace.jsonl', b''),
                              ('summary.json', json.dumps({**summary, 'run_id': identity}).encode())):
                (extra / name).write_bytes(raw)
                (extra / name).chmod(0o600)
        result = self.reader.runs()
        self.assertEqual(len(result['runs']), 100)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['runs'][0]['run_id'], RUN_ID)
        with patch.object(r, 'RUN_SCAN_LIMIT', 101):
            self.assertEqual(self.invoke(['runs'])[0], 1)
        # Unknown names count against the same bound, not a separate unbounded scan.
        (self.home / 'runs' / 'unknown').write_text('KEEP')
        with patch.object(r, 'RUN_SCAN_LIMIT', 102):
            self.assertEqual(self.invoke(['runs'])[0], 1)

    def test_file_event_structure_aggregate_and_output_limits(self):
        directory = self.completed()
        (directory / 'trace.jsonl').write_bytes(b' ' * (r.TRACE_MAX_BYTES + 1))
        self.assert_trace_failure('LIMIT_EXCEEDED')
        events = [event(1, d.EventType.RUN_START, {'command': 'ask', 'mode': 'normal'})]
        events.extend(event(number, d.EventType.WARNING,
                            {'code': 'DIAGNOSTICS_EVENT_DROPPED', 'message': 'omitted'})
                      for number in range(2, 33))
        events.append(event(33, d.EventType.RUN_END, {'status': 'error', 'duration_ms': 5}, severity='error'))
        self.rewrite_events(directory, [item.to_dict() for item in events])
        self.assert_trace_failure('LIMIT_EXCEEDED')
        self.rewrite_events(directory, [item.to_dict() for item in sample_events()])
        with patch.object(r, 'RUNS_READ_MAX_BYTES', 1):
            self.assertEqual(self.invoke(['runs'])[0], 1)
        with patch('llmtool.diagnostics_cli.READER_OUTPUT_MAX_BYTES', 1):
            status, out, err = self.invoke(['trace', RUN_ID, '--json'])
            self.assertEqual((status, out), (1, ''))
            self.assertIn('code=LIMIT_EXCEEDED', err)
        for number in range(14):
            (directory / ('unknown-' + str(number))).write_text('KEEP')
        self.assert_trace_failure('LIMIT_EXCEEDED')

    def test_replacement_during_read_never_displays_a_stale_snapshot(self):
        directory = self.completed()
        original_read = os.read
        replaced = False
        def changing_read(fd, count):
            nonlocal replaced
            raw = original_read(fd, count)
            if not replaced and raw:
                replaced = True
                path = directory / 'trace.jsonl'
                path.rename(directory / 'old-trace')
                path.write_text(PROMPT)
                path.chmod(0o600)
            return raw
        before = (directory / 'trace.jsonl').read_bytes()
        with patch.object(r.os, 'read', side_effect=changing_read):
            self.assert_trace_failure('STORE_UNAVAILABLE', json_modes=(True,))
        self.assert_trace_failure('FORMAT_INVALID')
        self.assertEqual((directory / 'old-trace').read_bytes(), before)

    def test_truncation_during_read_fails_and_writer_lock_is_released(self):
        directory = self.completed()
        original_read = os.read
        changed = False
        def changing_read(fd, count):
            nonlocal changed
            raw = original_read(fd, count)
            if raw and not changed:
                changed = True
                (directory / 'trace.jsonl').write_bytes(b'')
            return raw
        with patch.object(r.os, 'read', side_effect=changing_read):
            self.assert_trace_failure('STORE_UNAVAILABLE', json_modes=(True,))
        self.assert_trace_failure('FORMAT_INVALID')
        import fcntl
        for path in (self.home / '.store.lock', directory / '.run.lock'):
            fd = os.open(path, os.O_RDONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)

    def test_run_directory_replacement_or_publication_race_fails_safely(self):
        directory = self.completed()
        original_read = r._read
        changed = False
        def change_after_read(parent, name, limit):
            nonlocal changed
            raw = original_read(parent, name, limit)
            if not changed:
                changed = True
                directory.rename(directory.with_name('old-run'))
                directory.mkdir(mode=0o700)
            return raw
        with patch.object(r, '_read', side_effect=change_after_read):
            self.assert_trace_failure('STORE_UNAVAILABLE', json_modes=(True,))
        self.assert_trace_failure('STORE_UNSAFE')  # Replacement lacks the required run lock.

    def test_real_cleanup_waits_for_reader_and_snapshot_remains_valid(self):
        self.completed()
        attempted, finished = threading.Event(), threading.Event()
        writers = []
        original_lock = diagnostics_store._store_lock
        @contextmanager
        def signaled_lock(fd):
            attempted.set()
            with original_lock(fd):
                yield
        def cleanup():
            writer = DiagnosticsStore(DiagnosticsSettings(persist_metadata=True, max_runs=1),
                                      home=self.home).start('b' * 32)
            writers.append(writer)
            finished.set()
        thread = threading.Thread(target=cleanup)
        original_read = r._read
        launched = False
        def read_while_cleanup_waits(parent, name, limit):
            nonlocal launched
            if not launched:
                launched = True
                thread.start()
                self.assertTrue(attempted.wait(1))
                self.assertFalse(finished.is_set())
            return original_read(parent, name, limit)
        with patch.object(diagnostics_store, '_store_lock', side_effect=signaled_lock), \
                patch.object(r, '_read', side_effect=read_while_cleanup_waits):
            self.assertEqual(self.reader.trace(RUN_ID)['state'], 'complete')
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(finished.is_set())
        self.assertIsNotNone(writers[0])
        writers[0].close()
        self.assertFalse((self.home / 'runs' / RUN_ID).exists())
        self.assertTrue((self.home / 'runs' / ('b' * 32)).exists())

    def test_real_publication_during_active_read_is_only_incomplete(self):
        writer = DiagnosticsStore(DiagnosticsSettings(persist_metadata=True), home=self.home).start(RUN_ID)
        self.addCleanup(writer.close)
        events = sample_events()
        for value in events:
            self.assertTrue(writer.append(value))
        summary = RunSummary(run_id=RUN_ID, mode=d.DiagnosticMode.NORMAL, status='error',
            started_at=events[0].timestamp, ended_at=events[-1].timestamp, duration_ms=5,
            event_count=5, dropped_event_count=0, trace_bytes=writer.trace_bytes)
        published = threading.Event()
        results = []
        original_publish = diagnostics_store._publish
        def publication(parent, source, target):
            original_publish(parent, source, target)
            if target == 'summary.json':
                published.set()
        thread = threading.Thread(target=lambda: results.append(writer.finish(summary)))
        original_lock = r._lock
        @contextmanager
        def lock_while_publishing(parent, name):
            with original_lock(parent, name) as acquired:
                if name == '.run.lock':
                    self.assertFalse(acquired)
                    thread.start()
                    self.assertTrue(published.wait(1))
                yield acquired
        with patch.object(diagnostics_store, '_publish', side_effect=publication), \
                patch.object(r, '_lock', side_effect=lock_while_publishing):
            value = self.reader.trace(RUN_ID)
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, [True])
        self.assertEqual((value['state'], value['events'], value['summary']), ('incomplete', [], None))
        self.assertEqual(self.reader.trace(RUN_ID)['state'], 'complete')

    def test_actual_publication_temp_and_hardlink_windows_are_incomplete(self):
        for phase in ('summary_temp', 'trace_link', 'summary_link'):
            with self.subTest(phase=phase):
                self.home = self.base / phase
                self.reader = r.DiagnosticsReader(home=self.home)
                identity = {'summary_temp': '1', 'trace_link': '2', 'summary_link': '3'}[phase] * 32
                writer = DiagnosticsStore(DiagnosticsSettings(persist_metadata=True),
                                          home=self.home).start(identity)
                self.addCleanup(writer.close)
                values = sample_events(identity)
                for value in values:
                    self.assertTrue(writer.append(value))
                summary = RunSummary(run_id=identity, mode=d.DiagnosticMode.NORMAL, status='error',
                    started_at=values[0].timestamp, ended_at=values[-1].timestamp, duration_ms=5,
                    event_count=5, dropped_event_count=0, trace_bytes=writer.trace_bytes)
                directory = self.home / 'runs' / identity
                entered, release = threading.Event(), threading.Event()
                results = []
                original_publish, original_unlink = diagnostics_store._publish, os.unlink

                def pause():
                    entered.set()
                    if not release.wait(5):
                        raise RuntimeError('publication test timed out')

                def publishing(parent, source, target):
                    if phase == 'summary_temp' and target == 'summary.json':
                        self.assertTrue(source.startswith('.summary-'))
                        self.assertTrue((directory / source).exists())
                        pause()
                    original_publish(parent, source, target)

                def unlinking(name, *args, **kwargs):
                    if ((phase == 'trace_link' and name == 'trace.partial.jsonl') or
                            (phase == 'summary_link' and str(name).startswith('.summary-'))):
                        self.assertEqual(os.stat(name, dir_fd=kwargs['dir_fd']).st_nlink, 2)
                        pause()
                    return original_unlink(name, *args, **kwargs)

                thread = threading.Thread(target=lambda: results.append(writer.finish(summary)))
                with patch.object(diagnostics_store, '_publish', side_effect=publishing), \
                        patch.object(os, 'unlink', side_effect=unlinking):
                    thread.start()
                    try:
                        self.assertTrue(entered.wait(3))
                        before = snapshot(self.home)
                        original_stat = os.stat
                        inspected = []

                        def statting(name, *args, **kwargs):
                            inspected.append(str(name))
                            return original_stat(name, *args, **kwargs)

                        with ExitStack() as stack:
                            stack.enter_context(patch.object(r, '_read', side_effect=AssertionError('active bytes')))
                            stack.enter_context(patch.object(r.os, 'stat', side_effect=statting))
                            for name in ('mkdir', 'makedirs', 'write', 'unlink', 'rmdir', 'chmod',
                                         'fchmod', 'rename', 'replace', 'link'):
                                stack.enter_context(patch.object(os, name, side_effect=AssertionError('mutation')))
                            value = self.reader.trace(identity)
                            self.assertEqual((value['state'], value['events'], value['summary']),
                                             ('incomplete', [], None))
                            rows = self.reader.runs()
                            self.assertEqual(rows['damaged_count'], 0)
                            row = next(row for row in rows['runs'] if row['run_id'] == identity)
                            self.assertEqual(row['status'], 'incomplete')
                        self.assertFalse(any(name in r.RUN_FILES - {'.run.lock'} or
                                             name.startswith('.summary-') for name in inspected))
                        self.assertEqual(snapshot(self.home), before)
                    finally:
                        release.set()
                        thread.join(3)
                self.assertFalse(thread.is_alive())
                self.assertEqual(results, [True])
                self.assertEqual(self.reader.trace(identity)['state'], 'complete')

    def test_non_posix_and_invalid_home_safe_no_io(self):
        with patch.object(r.os, 'open', side_effect=AssertionError('unsupported I/O')):
            for method in ('runs', 'trace'):
                reader = r.DiagnosticsReader(home=self.home, platform='win32')
                with self.assertRaises(r.ReaderError) as caught:
                    getattr(reader, method)(*([RUN_ID] if method == 'trace' else []))
                self.assertEqual(caught.exception.code, 'STORE_UNAVAILABLE')
        for home in ('relative', str(self.base / '../state'), str(self.base) + '\n'):
            self.assertEqual(self.invoke(['runs'], home=home)[0], 1)
            self.assertEqual(self.invoke(['trace', RUN_ID], home=home)[0], 1)

    def test_broken_pipe_keeps_zero_and_interrupt_keeps_130(self):
        self.completed()
        output = io.StringIO()
        with patch.dict(os.environ, {'LLMTOOL_HOME': str(self.home)}), \
                patch.object(cli.sys, 'stdout', output), \
                patch.object(output, 'write', side_effect=BrokenPipeError):
            self.assertEqual(cli.main(['trace', RUN_ID, '--json']), 0)
        with patch.object(r.DiagnosticsReader, 'trace', side_effect=KeyboardInterrupt), \
                patch.object(cli.sys, 'stderr', io.StringIO()) as err:
            self.assertEqual(cli.main(['trace', RUN_ID]), 130)
        self.assertEqual(err.getvalue(), 'llmtool: interrupted\n')


if __name__ == '__main__':
    unittest.main()
