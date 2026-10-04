"""Public offline doctor: credential-free readiness and nonmutating snapshots."""

import io
import os
import stat
import tempfile
import unittest
from collections.abc import Mapping
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from llmtool import cli, config, doctor, diagnostics_reader as r
from llmtool.diagnostics_settings import DiagnosticsSettings
from llmtool.diagnostics_store import DiagnosticsStore, persistence_supported

PRIVATE = 'PRIVATE_DOCTOR_PATH_MODEL_PROFILE_ORIGIN'
SECRET = 'sk-' + 'a' * 40
RUN_ID = 'a' * 32


class KeysOnlyEnvironment(Mapping):
    """Default Mapping.__contains__ fetches values, so it is a real tripwire."""
    def __init__(self, present):
        self.names = ('DOCTOR_SECRET',) if present else ()

    def __iter__(self):
        return iter(self.names)

    def __len__(self):
        return len(self.names)

    def __getitem__(self, key):
        if key == 'DOCTOR_SECRET':
            raise AssertionError('credential environment value fetched')
        raise KeyError(key)


class CaseInsensitiveEnvironment(Mapping):
    """Windows-like keys with both credential get paths forbidden."""
    def __init__(self, name, present, value=SECRET):
        self.secret_name = name.upper()
        self.data = {self.secret_name: value} if present else {}
        self.secret_fetches = []

    def __iter__(self):
        return iter(self.data)

    def __len__(self):
        return len(self.data)

    def __getitem__(self, key):
        if key.upper() == self.secret_name:
            self.secret_fetches.append(key)
            raise AssertionError('case-insensitive credential value fetched')
        return self.data[key.upper()]

    def get(self, key, default=None):
        if key.upper() == self.secret_name:
            self.secret_fetches.append(key)
            raise AssertionError('case-insensitive credential get used')
        return self.data.get(key.upper(), default)


def snapshot(path):
    result = {}
    for item in [path, *path.rglob('*')]:
        info = item.lstat()
        raw = (item.read_bytes() if stat.S_ISREG(info.st_mode) else
               os.readlink(item) if stat.S_ISLNK(info.st_mode) else None)
        result[str(item.relative_to(path))] = (info.st_mode, info.st_uid, info.st_ino,
                                               info.st_mtime_ns, info.st_ctime_ns, info.st_nlink, raw)
    return result


class DoctorFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.user = self.base / 'user'
        self.project = self.base / 'project'
        self.project.mkdir()
        self.state = self.base / 'state'
        self.path = self.user / '.config/llmtool/config.yaml'
        self.path.parent.mkdir(parents=True)

    def write_config(self, kind='openai-compatible', extra='', policy=''):
        fields = '    type: ' + kind + '\n'
        if kind == 'openai-compatible':
            fields += '    base_url: http://' + PRIVATE.lower() + '.invalid\n'
        if kind in ('openai', 'openai-compatible'):
            fields += '    model: ' + PRIVATE + '\n'
        self.path.write_text('providers:\n  default:\n' + fields + extra + policy)

    def invoke(self, argv=('doctor',), *, environment=None, output=None):
        out = io.StringIO() if output is None else output
        err = io.StringIO()
        env = {'LLMTOOL_HOME': str(self.state)} if environment is None else environment
        with patch('llmtool.config.Path.home', return_value=self.user), \
                patch('llmtool.config.Path.cwd', return_value=self.project), \
                patch.dict(os.environ, env, clear=True), redirect_stdout(out), redirect_stderr(err):
            status = cli.main(list(argv))
        return status, out.getvalue(), err.getvalue()

    def assert_safe(self, result, code=None):
        status, out, err = result
        for marker in (PRIVATE, SECRET, str(self.base), 'DOCTOR_SECRET', 'Traceback'):
            self.assertNotIn(marker, out + err)
        self.assertLess(len((out + err).encode()), 2048)
        if code is not None:
            self.assertEqual((status, out), (1, ''))
            self.assertIn('code=' + code, err)

    def windows_inspection(self, name, present, *, native_codec, value=SECRET):
        """Use a synthetic Windows codec on Linux, never native-Windows evidence."""
        if native_codec:
            environment = os._Environ({name.upper(): value} if present else {}, str.upper, str, str, str)
            original = os._Environ.__getitem__
            fetches = []
            def guarded(instance, key):
                if instance is environment and key.upper() == name.upper():
                    fetches.append(key)
                    raise AssertionError('os._Environ Windows-codec credential fetched')
                return original(instance, key)
            guard = patch.object(os._Environ, '__getitem__', new=guarded)
        else:
            environment = CaseInsensitiveEnvironment(name, present, value)
            fetches = environment.secret_fetches
            guard = ExitStack()
        try:
            with patch('llmtool.config.sys.platform', 'win32'), guard:
                return config.inspect_config(environ=environment, cwd=self.project, home=self.user)
        finally:
            self.assertEqual(fetches, [])


class DoctorTests(DoctorFixture):
    def test_default_missing_store_factual_output_and_no_mutation(self):
        before = snapshot(self.base)
        result = self.invoke()
        self.assertEqual((result[0], result[2]), (0, ''))
        self.assertIn('persist_metadata=false\n', result[1])
        self.assertIn('readiness=not-configured executable=not-applicable credential=not-configured', result[1])
        self.assertIn('store=absent ', result[1])
        self.assertIn('authentication and inference unverified', result[1])
        self.assertEqual(snapshot(self.base), before)
        self.assert_safe(result)

    def test_policy_bounds_and_user_only_layering(self):
        self.write_config(policy='diagnostics:\n  persist_metadata: true\n  max_runs: 7\n'
                          '  max_total_bytes: 1048576\n  max_age_days: 3\n  max_run_bytes: 65536\n')
        before = snapshot(self.base)
        result = self.invoke()
        for line in ('persist_metadata=true', 'max_runs=7', 'max_total_bytes=1048576',
                     'max_age_days=3', 'max_run_bytes=65536'):
            self.assertIn(line + '\n', result[1])
        self.assertEqual(result[0], 0)
        self.assertEqual(snapshot(self.base), before)
        (self.project / '.llmtool.yaml').write_text('diagnostics:\n  persist_metadata: false\n')
        self.assert_safe(self.invoke(), 'CONFIG_INVALID')

    def test_credential_key_iteration_only_not_values_or_default_contains(self):
        self.write_config(extra='    api_key_env: DOCTOR_SECRET\n')
        for present in (False, True):
            with self.subTest(present=present):
                value = config.inspect_config(environ=KeysOnlyEnvironment(present),
                                              cwd=self.project, home=self.user)
                self.assertEqual(value['credential'], 'env-configured-' + ('present' if present else 'absent'))
        result = self.invoke(environment={'LLMTOOL_HOME': str(self.state), 'DOCTOR_SECRET': ''})
        self.assertIn('credential=env-configured-present', result[1])
        self.assert_safe(result)

    def test_literal_presence_without_display_or_resolution(self):
        self.write_config('openai', extra='    api_key: ' + SECRET + '\n')
        result = self.invoke()
        self.assertEqual(result[0], 0)
        self.assertIn('readiness=ready executable=not-applicable credential=literal-configured', result[1])
        self.assert_safe(result)

    def test_real_os_environ_never_fetches_configured_credential_value(self):
        self.write_config(extra='    api_key_env: DOCTOR_SECRET\n')
        original = type(os.environ).__getitem__
        def guarded(environment, key):
            if key == 'DOCTOR_SECRET':
                raise AssertionError('real os.environ credential value fetched')
            return original(environment, key)
        with patch.dict(os.environ, {'DOCTOR_SECRET': SECRET}, clear=False):
            with patch.object(type(os.environ), '__getitem__', new=guarded):
                value = config.inspect_config(environ=os.environ, cwd=self.project, home=self.user)
        self.assertEqual(value['credential'], 'env-configured-present')

    def test_credential_binding_collisions_fail_before_known_env_value_fetch(self):
        class CollisionEnvironment(Mapping):
            def __init__(self, name):
                self.name = name
            def __iter__(self):
                return iter((self.name,))
            def __len__(self):
                return 1
            def __getitem__(self, key):
                raise AssertionError('known binding value fetched: ' + key)
        for name in config.INSPECTION_ENV_NAMES:
            for inactive in (False, True):
                extra = '    api_key_env: ' + name + '\n'
                if inactive:
                    extra = '  inactive:\n    type: openai-compatible\n' + extra
                self.write_config(extra=extra)
                with self.subTest(name=name, inactive=inactive), self.assertRaises(config.LLMToolError):
                    config.inspect_config(environ=CollisionEnvironment(name), cwd=self.project, home=self.user)
                with patch.object(r.DiagnosticsReader, 'inspect_store', side_effect=AssertionError('store inspected')), \
                        patch('llmtool.doctor.shutil.which', side_effect=AssertionError('executable lookup')):
                    self.assert_safe(self.invoke(), 'CONFIG_INVALID')
        # The restriction belongs to doctor only; ordinary layered config is unchanged.
        self.write_config(extra='    api_key_env: LLMTOOL_MODEL\n')
        normal = config.resolve_config(environ={'LLMTOOL_MODEL': 'legacy-model'}, cwd=self.project, home=self.user)
        self.assertEqual(normal['providers']['default']['model'], 'legacy-model')

    def test_all_provider_readiness_and_executable_absolute_contract(self):
        for kind in ('openai', 'openai-compatible', 'claude-cli', 'codex-cli'):
            for found in (None, 'relative-executable', '/private/' + PRIVATE):
                self.write_config(kind)
                with self.subTest(kind=kind, found=found), patch('llmtool.doctor.shutil.which', return_value=found) as lookup:
                    result = self.invoke()
                self.assertEqual(result[0], 0)
                self.assertIn('provider_type=' + kind, result[1])
                ready = 'not-configured' if kind == 'openai' else 'ready'
                self.assertIn('readiness=' + ready, result[1])
                if kind.endswith('-cli'):
                    lookup.assert_called_once_with(kind[:-4])
                    self.assertIn('executable=' + ('available' if found and found.startswith('/') else 'missing'), result[1])
                else:
                    lookup.assert_not_called()
                self.assert_safe(result)

    def test_windows_reserved_bindings_lower_mixed_case_active_inactive_no_value_fetch(self):
        for reserved in config.INSPECTION_ENV_NAMES:
            for name in (reserved.lower(), reserved.swapcase()):
                for inactive in (False, True):
                    extra = '    api_key_env: ' + name + '\n'
                    if inactive:
                        extra = '  inactive:\n    type: openai-compatible\n' + extra
                    self.write_config(extra=extra)
                    for native_codec in (False, True):
                        with self.subTest(name=name, inactive=inactive, os_environ_codec=native_codec), \
                                self.assertRaises(config.LLMToolError):
                            self.windows_inspection(name, True, native_codec=native_codec)
                    with patch('llmtool.config.sys.platform', 'win32'), \
                            patch.object(r.DiagnosticsReader, 'inspect_store', side_effect=AssertionError('store inspected')), \
                            patch('llmtool.doctor.shutil.which', side_effect=AssertionError('runtime lookup')):
                        self.assert_safe(self.invoke(), 'CONFIG_INVALID')

    def test_windows_credential_presence_lower_mixed_case_empty_absent_no_value_fetch(self):
        for name in ('doctor_secret', 'DoCtOr_SeCrEt', 'DOCTOR_SECRET'):
            self.write_config(extra='    api_key_env: ' + name + '\n')
            for present, value in ((False, SECRET), (True, ''), (True, SECRET)):
                for native_codec in (False, True):
                    with self.subTest(name=name, present=present, empty=value == '', os_environ_codec=native_codec):
                        result = self.windows_inspection(name, present, native_codec=native_codec, value=value)
                        self.assertEqual(result['credential'], 'env-configured-' + ('present' if present else 'absent'))

    def test_posix_credential_and_reserved_names_remain_case_sensitive(self):
        self.write_config(extra='    api_key_env: doctor_secret\n')
        with patch('llmtool.config.sys.platform', 'linux'):
            value = config.inspect_config(environ=KeysOnlyEnvironment(True), cwd=self.project, home=self.user)
        self.assertEqual(value['credential'], 'env-configured-absent')
        self.write_config(extra='    api_key_env: llmtool_model\n')
        with patch('llmtool.config.sys.platform', 'linux'):
            value = config.inspect_config(environ={'LLMTOOL_MODEL': 'nonsecret-override'}, cwd=self.project, home=self.user)
        self.assertEqual(value['credential'], 'env-configured-absent')
        # Doctor normalization never changes ordinary config resolution.
        with patch('llmtool.config.sys.platform', 'win32'):
            normal = config.resolve_config(environ={'LLMTOOL_MODEL': 'legacy-model'}, cwd=self.project, home=self.user)
        self.assertEqual(normal['providers']['default']['model'], 'legacy-model')

    def test_existing_config_selection_overrides_without_profile_value_output(self):
        self.write_config(policy='  private-profile:\n    type: codex-cli\n')
        result = self.invoke(environment={'LLMTOOL_HOME': str(self.state), 'LLMTOOL_PROVIDER': 'private-profile'})
        self.assertEqual(result[0], 0)
        self.assertIn('provider_type=codex-cli', result[1])
        self.assertNotIn('private-profile', result[1])
        self.write_config()
        (self.project / '.llmtool.yaml').write_text('providers:\n  default:\n    model: null\n')
        self.assertIn('readiness=not-configured', self.invoke()[1])
        result = self.invoke(environment={'LLMTOOL_HOME': str(self.state), 'LLMTOOL_MODEL': 'override'})
        self.assertIn('readiness=ready', result[1])

    def test_invalid_config_fixed_error_before_store_or_provider_access(self):
        bad = ('providers: [\n', 'providers: {}\nproviders: {}\n', 'generation:\n  max_tokens: -1\n',
               'diagnostics:\n  persist_metadata: yes-please\n', 'diagnostics:\n  max_runs: 1001\n',
               'providers:\n  default:\n    type: ' + PRIVATE + '\n',
               'providers:\n  default:\n    api_key: ' + SECRET + '\n    api_key_env: DOCTOR_SECRET\n',
               'active_provider: ' + PRIVATE + '\n', 'x: &anchor {}\ny: *anchor\n',
               'x: ' + 'a' * (64 * 1024))
        for raw in bad:
            self.path.write_text(raw)
            with self.subTest(raw=raw[:30]), \
                    patch.object(r.DiagnosticsReader, 'inspect_store', side_effect=AssertionError('store accessed')), \
                    patch('llmtool.doctor.shutil.which', side_effect=AssertionError('runtime accessed')):
                self.assert_safe(self.invoke(), 'CONFIG_INVALID')

    def test_offline_no_client_network_process_auth_or_secret_loader(self):
        self.write_config('codex-cli')
        original_open = io.open
        accesses = []
        def safe_open(path, *args, **kwargs):
            accesses.append(Path(path))
            self.assertIn(Path(path), (self.path, self.project / '.llmtool.yaml'))
            return original_open(path, *args, **kwargs)
        with ExitStack() as stack:
            for target in ('llmtool.cli.resolve_config', 'llmtool.config.resolve_config',
                           'llmtool.config.resolve_profile_config', 'llmtool.cli.api_key', 'llmtool.config.api_key',
                           'llmtool.cli.create_client', 'llmtool.providers.create_client',
                           'socket.socket', 'urllib.request.build_opener', 'subprocess.Popen',
                           'subprocess.run', 'llmtool.providers.codex_cli.CodexCLIProvider.__init__',
                           'llmtool.providers.claude_cli.ClaudeCLIProvider.__init__'):
                stack.enter_context(patch(target, side_effect=AssertionError(target)))
            stack.enter_context(patch('io.open', side_effect=safe_open))
            result = self.invoke()
        self.assertEqual(result[0], 0)
        self.assertEqual(accesses, [self.path])  # absent project is statted, never opened
        self.assert_safe(result)

    def test_unsupported_platform_reports_without_store_or_location_io(self):
        self.write_config()
        with patch('llmtool.diagnostics_store.sys.platform', 'win32'), \
                patch('llmtool.diagnostics_reader.resolve_diagnostics_home', side_effect=AssertionError('location')), \
                patch('llmtool.diagnostics_reader.os.open', side_effect=AssertionError('store open')):
            result = self.invoke()
        self.assertEqual(result[0], 0)
        self.assertIn('persistence_supported=false', result[1])
        self.assertIn('store=unsupported', result[1])
        self.assert_safe(result)

    def test_bad_home_and_unrequested_options_keep_safe_error_contract(self):
        for home in ('relative/' + PRIVATE, '/tmp/../' + PRIVATE, '/tmp/\x1b' + PRIVATE):
            self.assert_safe(self.invoke(environment={'LLMTOOL_HOME': home}), 'STORE_UNSAFE')
        for argv in (('doctor', '--json'), ('doctor', '--save-diagnostics'), ('doctor', PRIVATE), ('doctor', '--provider', PRIVATE)):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as caught:
                result = self.invoke(argv)
            self.assertEqual(caught.exception.code, 2)
        self.assertFalse(self.state.exists())

    def test_broken_pipe_zero_and_interrupt_130(self):
        class Closed(io.StringIO):
            def write(self, value):
                raise BrokenPipeError(PRIVATE)
        self.assertEqual(self.invoke(output=Closed())[0], 0)
        with patch.object(r.DiagnosticsReader, 'inspect_store', side_effect=KeyboardInterrupt):
            self.assertEqual(self.invoke(), (130, '', 'llmtool: interrupted\n'))


@unittest.skipUnless(persistence_supported(), 'requires POSIX diagnostics backend')
class DoctorStoreTests(DoctorFixture):
    def start(self):
        writer = DiagnosticsStore(DiagnosticsSettings(persist_metadata=True), home=self.state).start(RUN_ID)
        self.assertIsNotNone(writer)
        self.addCleanup(writer.close)
        return writer

    def test_existing_store_never_reads_artifact_bodies_or_writes(self):
        writer = self.start()
        writer.close()
        directory = self.state / 'runs' / RUN_ID
        (directory / 'summary.json').write_text(PRIVATE + SECRET)
        (directory / 'summary.json').chmod(0o600)
        (self.state / 'runs' / PRIVATE).symlink_to(self.path)
        before = snapshot(self.base)
        original_open = os.open
        def readonly(name, flags, *args, **kwargs):
            self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            self.assertNotIn(str(name), ('summary.json', 'trace.jsonl', 'trace.partial.jsonl', '.reservation'))
            return original_open(name, flags, *args, **kwargs)
        with ExitStack() as stack:
            stack.enter_context(patch('llmtool.diagnostics_reader.os.open', side_effect=readonly))
            stack.enter_context(patch('llmtool.diagnostics_reader.os.read', side_effect=AssertionError('body read')))
            for target in ('os.mkdir', 'os.chmod', 'os.unlink', 'os.rename', 'os.replace', 'os.link', 'os.write',
                           'llmtool.diagnostics_store.DiagnosticsStore.start'):
                stack.enter_context(patch(target, side_effect=AssertionError(target)))
            result = self.invoke()
        self.assertEqual(result[0], 0)
        self.assertIn('store=present run_count=1 active_count=0 unknown_count=1', result[1])
        self.assertIn('metadata completion and write access unverified', result[1])
        self.assertEqual(snapshot(self.base), before)
        self.assert_safe(result)

    def test_busy_run_artifacts_not_inspected_or_repaired(self):
        writer = self.start()
        directory = self.state / 'runs' / RUN_ID
        (directory / (PRIVATE + '.tmp')).write_text(SECRET)
        before = snapshot(self.base)
        original_stat = os.stat
        def stat_without_artifacts(name, *args, **kwargs):
            self.assertNotIn(str(name), ('trace.partial.jsonl', '.reservation', PRIVATE + '.tmp'))
            return original_stat(name, *args, **kwargs)
        with patch('llmtool.diagnostics_reader.os.stat', side_effect=stat_without_artifacts):
            result = self.invoke()
        self.assertEqual(result[0], 0)
        self.assertIn('active_count=1', result[1])
        self.assertEqual(snapshot(self.base), before)
        self.assertFalse(writer.closed)

    def test_unsafe_modes_wrong_owner_and_nonregular_components(self):
        writer = self.start()
        writer.close()
        directory = self.state / 'runs' / RUN_ID
        for path in (self.state, self.state / 'runs', directory, directory / '.run.lock', directory / '.reservation'):
            mode = stat.S_IMODE(path.stat().st_mode)
            path.chmod(0o755 if path.is_dir() else 0o644)
            before = snapshot(self.base)
            self.assert_safe(self.invoke(), 'STORE_UNSAFE')
            self.assertEqual(snapshot(self.base), before)
            path.chmod(mode)
        with patch('llmtool.diagnostics_reader.os.geteuid', return_value=os.geteuid() + 1):
            self.assert_safe(self.invoke(), 'STORE_UNSAFE')
        (directory / '.reservation').unlink()
        os.mkfifo(directory / '.reservation', 0o600)
        self.assert_safe(self.invoke(), 'STORE_UNSAFE')

    def test_symlinks_at_home_runs_run_lock_artifact_and_ancestor_boundaries(self):
        writer = self.start()
        writer.close()
        directory = self.state / 'runs' / RUN_ID
        for path in (directory / '.reservation', directory / '.run.lock', directory,
                     self.state / 'runs', self.state):
            backup = path.with_name(path.name + '-original')
            path.rename(backup)
            path.symlink_to(backup, target_is_directory=backup.is_dir())
            before = snapshot(self.base)
            self.assert_safe(self.invoke())
            self.assertEqual(self.invoke()[0], 1)
            self.assertEqual(snapshot(self.base), before)
            path.unlink()
            backup.rename(path)
        alias = self.base / 'alias'
        alias.symlink_to(self.base, target_is_directory=True)
        self.assertEqual(self.invoke(environment={'LLMTOOL_HOME': str(alias / 'state')})[0], 1)

    def test_hardlinked_lock_and_artifact_missing_lock_no_repair(self):
        writer = self.start()
        writer.close()
        directory = self.state / 'runs' / RUN_ID
        for path in (self.state / '.store.lock', directory / '.run.lock', directory / '.reservation'):
            linked = self.base / 'linked'
            os.link(path, linked)
            before = snapshot(self.base)
            self.assert_safe(self.invoke(), 'STORE_UNSAFE')
            self.assertEqual(snapshot(self.base), before)
            linked.unlink()
        (directory / '.run.lock').unlink()
        before = snapshot(self.base)
        self.assert_safe(self.invoke(), 'STORE_UNSAFE')
        self.assertEqual(snapshot(self.base), before)

    def test_scan_home_and_file_size_bounds(self):
        writer = self.start()
        writer.close()
        directory = self.state / 'runs' / RUN_ID
        for name, limit in (('.reservation', 1024), ('summary.json', r.SUMMARY_MAX_BYTES),
                            ('trace.partial.jsonl', r.TRACE_MAX_BYTES)):
            path = directory / name
            if not path.exists():
                path.touch(mode=0o600)
            original = path.read_bytes()
            with path.open('wb') as stream:
                stream.truncate(limit + 1)
            self.assert_safe(self.invoke(), 'LIMIT_EXCEEDED')
            path.write_bytes(original)
        with patch.object(r, 'RUN_SCAN_LIMIT', 0):
            self.assert_safe(self.invoke(), 'LIMIT_EXCEEDED')
        with patch.object(r, 'RUN_FILE_SCAN_LIMIT', 1):
            self.assert_safe(self.invoke(), 'LIMIT_EXCEEDED')
        with patch.object(r, 'HOME_COMPONENT_LIMIT', 2), \
                patch('llmtool.diagnostics_reader.os.open', side_effect=AssertionError('open before bound')):
            self.assert_safe(self.invoke(), 'LIMIT_EXCEEDED')

    def test_permission_and_lock_failure_generic_bounded_no_partial_stdout(self):
        writer = self.start()
        writer.close()
        with patch('llmtool.diagnostics_reader.os.open', side_effect=PermissionError(PRIVATE + SECRET)):
            self.assert_safe(self.invoke(), 'STORE_UNAVAILABLE')
        import fcntl
        lock = os.open(self.state / '.store.lock', os.O_RDONLY)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch('llmtool.diagnostics_reader.time.monotonic', side_effect=[0, 3]):
                self.assert_safe(self.invoke(), 'STORE_UNAVAILABLE')
        finally:
            os.close(lock)
        self.assertEqual(self.invoke()[0], 0)

    def test_populated_missing_and_unsafe_root_lock_never_repaired(self):
        writer = self.start()
        writer.close()
        lock = self.state / '.store.lock'
        lock.chmod(0o644)
        before = snapshot(self.base)
        self.assert_safe(self.invoke(), 'STORE_UNSAFE')
        self.assertEqual(snapshot(self.base), before)
        lock.unlink()
        before = snapshot(self.base)
        self.assert_safe(self.invoke(), 'STORE_UNSAFE')
        self.assertEqual(snapshot(self.base), before)

    def test_artifact_replacement_during_stat_fails_before_output(self):
        writer = self.start()
        writer.close()
        path = self.state / 'runs' / RUN_ID / '.reservation'
        original_stat = os.stat
        replaced = []
        def replace_after_stat(name, *args, **kwargs):
            info = original_stat(name, *args, **kwargs)
            if str(name) == '.reservation' and not replaced:
                moved = self.base / 'moved-reservation'
                path.rename(moved)
                path.write_bytes(b'')
                path.chmod(0o600)
                replaced.append(moved)
            return info
        with patch('llmtool.diagnostics_reader.os.stat', side_effect=replace_after_stat):
            self.assert_safe(self.invoke(), 'STORE_UNAVAILABLE')

    def test_run_and_home_replacement_races_fail_before_output(self):
        writer = self.start()
        writer.close()
        directory = self.state / 'runs' / RUN_ID
        original_names = r._names
        swapped = []
        def replace_after_scan(fd, limit):
            names = original_names(fd, limit)
            if not swapped and limit == r.RUN_FILE_SCAN_LIMIT:
                moved = directory.with_name('moved')
                directory.rename(moved)
                directory.mkdir(mode=0o700)
                swapped.append(moved)
            return names
        with patch.object(r, '_names', side_effect=replace_after_scan):
            self.assert_safe(self.invoke(), 'STORE_UNAVAILABLE')
        directory.rmdir()
        swapped[0].rename(directory)
        original_check = r._check_link
        def replace_home(parent, name, fd, directory=False):
            original_check(parent, name, fd, directory)
            if name == 'runs':
                self.state.rename(self.base / 'moved-state')
                self.state.mkdir(mode=0o700)
        with patch.object(r, '_check_link', side_effect=replace_home):
            self.assert_safe(self.invoke(), 'STORE_UNAVAILABLE')


if __name__ == '__main__':
    unittest.main()
