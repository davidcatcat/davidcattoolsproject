"""B2: interrupted store-owned eviction is recoverable, never foreign repair."""
import errno
import os
from pathlib import Path
import signal
import subprocess
import sys
import unittest
from unittest.mock import patch
import test_diagnostics_store as fixture
from llmtool.diagnostics_store import persistence_supported

ROOT = Path(__file__).resolve().parents[3]


@unittest.skipUnless(persistence_supported(), 'requires POSIX diagnostics backend')
class EvictionRegressions(unittest.TestCase):
    setUp = fixture.StoreTests.setUp
    store = fixture.StoreTests.store
    start = fixture.StoreTests.start
    complete = fixture.StoreTests.complete

    def partial(self, directory):
        identity = directory.stat().st_ino
        original, calls = os.unlink, []
        def unlink(name, *args, **kwargs):
            fd = kwargs.get('dir_fd')
            if fd is not None and os.fstat(fd).st_ino == identity:
                calls.append(name)
                if len(calls) == 2:
                    raise OSError(errno.EIO, 'synthetic eviction interruption')
            return original(name, *args, **kwargs)
        with patch('llmtool.diagnostics_store.os.unlink', side_effect=unlink):
            self.assertIsNone(self.store(max_runs=1).start())
        self.assertEqual(len(calls), 2)

    def test_b2_unlink_fault_next_start_recovers_max_runs_one(self):
        directory = self.complete(self.start(identity='a' * 32))
        self.partial(directory)
        next_store = self.store(max_runs=1)
        writer = next_store.start()
        self.assertIsNotNone(writer, next_store.warning)
        self.addCleanup(writer.close)
        self.assertFalse(directory.exists())

    def test_b2_sigkill_during_unlink_next_start_recovers(self):
        script = '''
import os, signal, sys
from unittest.mock import patch
from llmtool.diagnostics_store import DiagnosticsStore
from llmtool.diagnostics_settings import DiagnosticsSettings
original, original_rmdir, count = os.unlink, os.rmdir, 0
inode = os.stat(sys.argv[2]).st_ino
point = sys.argv[3]
def die(): os.kill(os.getpid(), signal.SIGKILL)
def unlink(name, *args, **kwargs):
    global count
    fd = kwargs.get('dir_fd')
    if fd is not None and os.fstat(fd).st_ino == inode:
        count += 1
        if (point == 'intent' and count == 1) or (point == 'mid' and count == 2): die()
    result = original(name, *args, **kwargs)
    if point == 'receipt' and name.startswith('.eviction-') and name.endswith('.json'): die()
    return result
def rmdir(name, *args, **kwargs):
    if name == 'b' * 32 and point == 'lockless': die()
    result = original_rmdir(name, *args, **kwargs)
    if name == 'b' * 32 and point == 'removed': die()
    return result
with patch('llmtool.diagnostics_store.os.unlink', side_effect=unlink), patch('llmtool.diagnostics_store.os.rmdir', side_effect=rmdir):
    DiagnosticsStore(DiagnosticsSettings(persist_metadata=True, max_runs=1), home=sys.argv[1]).start()
'''
        for point in ('intent', 'mid', 'lockless', 'removed', 'receipt'):
            with self.subTest(point=point):
                self.home = self.base / point
                directory = self.complete(self.start(identity='b' * 32))
                result = subprocess.run([sys.executable, '-c', script, str(self.home), str(directory), point],
                    cwd=ROOT, env={'PYTHONPATH': str(ROOT / 'tools/llmtool'), 'HOME': str(self.base),
                                   'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1'},
                    capture_output=True, timeout=15)
                self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
                store = self.store(max_runs=1)
                writer = store.start()
                self.assertIsNotNone(writer, store.warning)
                self.addCleanup(writer.close)
                self.assertFalse(directory.exists())
                self.assertEqual(list(self.home.glob('.eviction-*.json')), [])

    def test_b2_interrupted_empty_directory_rmdir_recovers(self):
        directory = self.complete(self.start(identity='c' * 32))
        original = os.rmdir
        def rmdir(name, *args, **kwargs):
            if name == directory.name:
                raise OSError(errno.EIO, 'synthetic rmdir interruption')
            return original(name, *args, **kwargs)
        with patch('llmtool.diagnostics_store.os.rmdir', side_effect=rmdir):
            self.assertIsNone(self.store(max_runs=1).start())
        self.assertEqual(list(directory.iterdir()), [])
        store = self.store(max_runs=1)
        writer = store.start()
        self.assertIsNotNone(writer, store.warning)
        self.addCleanup(writer.close)

    def test_b2_recovery_preserves_foreign_directory_replacement(self):
        directory = self.complete(self.start(identity='d' * 32))
        self.partial(directory)
        directory.rename(self.base / 'original')
        directory.mkdir(mode=0o700)
        foreign = directory / 'foreign'
        foreign.write_text('KEEP_FOREIGN')
        foreign.chmod(0o600)
        self.assertIsNone(self.store(max_runs=1).start())
        self.assertEqual(foreign.read_text(), 'KEEP_FOREIGN')

    def test_b2_recovery_preserves_symlink_hardlink_and_active_run(self):
        for attack in ('symlink', 'hardlink', 'active'):
            with self.subTest(attack=attack):
                self.home = self.base / attack
                directory = self.complete(self.start(identity='e' * 32))
                self.partial(directory)
                if attack == 'active':
                    import fcntl
                    fd = os.open(directory / '.run.lock', os.O_RDWR)
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    try:
                        before = sorted(p.name for p in directory.iterdir())
                        self.assertIsNone(self.store(max_runs=1).start())
                        self.assertEqual(sorted(p.name for p in directory.iterdir()), before)
                    finally:
                        os.close(fd)
                else:
                    target = self.base / ('foreign-' + attack)
                    target.write_text('KEEP_FOREIGN')
                    target.chmod(0o600)
                    artifact = directory / 'trace.jsonl'
                    if artifact.exists():
                        artifact.unlink()
                    if attack == 'symlink':
                        artifact.symlink_to(target)
                    else:
                        os.link(target, artifact)
                    self.assertIsNone(self.store(max_runs=1).start())
                    self.assertTrue(artifact.is_symlink() if attack == 'symlink' else artifact.exists())
                    self.assertEqual(target.read_text(), 'KEEP_FOREIGN')

    def test_b2_recovery_preserves_unknown_and_regular_file_replacement(self):
        for damage in ('unknown', 'replacement'):
            with self.subTest(damage=damage):
                self.home = self.base / damage
                directory = self.complete(self.start(identity='f' * 32))
                self.partial(directory)
                if damage == 'unknown':
                    foreign = directory / 'foreign'
                    foreign.write_text('KEEP_FOREIGN')
                    foreign.chmod(0o600)
                else:
                    foreign = directory / 'trace.jsonl'
                    content = foreign.read_bytes()
                    foreign.rename(self.base / 'original-trace')
                    foreign.write_bytes(content)
                    foreign.chmod(0o600)
                before = {p.name: (p.stat().st_ino, p.read_bytes()) for p in directory.iterdir()}
                self.assertIsNone(self.store(max_runs=1).start())
                self.assertEqual({p.name: (p.stat().st_ino, p.read_bytes()) for p in directory.iterdir()}, before)

    def test_b2_unpublished_intent_write_failure_rolls_back_owned_file_only(self):
        from llmtool import diagnostics_store as module
        directory = self.complete(self.start(identity='a' * 32))
        original = module._write
        def write(fd, raw):
            if module.EVICTION_SCHEMA.encode() in raw:
                os.write(fd, raw[:9])
                raise OSError(errno.EIO, 'synthetic intent write fault')
            return original(fd, raw)
        with patch.object(module, '_write', side_effect=write):
            self.assertIsNone(self.store(max_runs=1).start())
        self.assertTrue((directory / 'summary.json').exists())
        self.assertEqual(list(self.home.glob('.eviction-*')), [])
        writer = self.start(self.store(max_runs=1))
        self.assertNotEqual(writer.run_id, directory.name)

    def test_b2_malformed_or_unsafe_intents_never_authorize_foreign_deletion(self):
        for damage in ('malformed', 'symlink', 'hardlink'):
            with self.subTest(damage=damage):
                self.home = self.base / damage
                directory = self.complete(self.start(identity='a' * 32))
                before = {p.name: p.read_bytes() for p in directory.iterdir()}
                journal = self.home / ('.eviction-' + 'f' * 32 + '.json')
                target = self.base / ('foreign-' + damage)
                target.write_text('{}')
                target.chmod(0o600)
                if damage == 'symlink':
                    journal.symlink_to(target)
                elif damage == 'hardlink':
                    os.link(target, journal)
                else:
                    journal.write_text('{}')
                    journal.chmod(0o600)
                self.assertIsNone(self.store(max_runs=1).start())
                self.assertEqual({p.name: p.read_bytes() for p in directory.iterdir()}, before)
                self.assertTrue(journal.exists())
                self.assertEqual(target.read_text(), '{}')
