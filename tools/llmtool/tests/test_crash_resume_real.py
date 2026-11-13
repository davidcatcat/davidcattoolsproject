"""#1019 A11 row 45: crash resume with a real process.

Each case starts a real child Python process that writes to a TaskStore in a
temporary directory, stops it at one controlled point inside the write path
and kills it with SIGKILL. The parent then checks that:

- the record on disk is the old or the new complete version, never partial;
- no stale lock is left: the task lock is free at once, and the next writer
  finishes inside TASK_UPDATE_LOCK_TIMEOUT_SECONDS instead of failing BUSY;
- a rerun in a fresh process completes;
- an effect recorded through an idempotent API is stored exactly once.

Synchronization is deterministic: the child reports the point it reached on
its stdout pipe, then blocks reading stdin until the parent kills it. The
parent waits on that pipe with select(); no step relies on a sleep. HOME is
a temporary directory and nothing touches the network or real user state.
"""

import fcntl
import hashlib
import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore, new_id
from llmtool.task_verification import AcceptanceCriterion, CriterionKind, TaskContract


# Upper bound for a failure only; a passing run never waits this long.
_SYNC_TIMEOUT_SECONDS = 30

# The child: install one hook at ``point``, run ``mode``, report DONE.
_CHILD = r'''
import json
import os
import sys

from llmtool import task_store
from llmtool.task_store import TaskStore

root, mode, task_id, point, input_path = sys.argv[1:6]
if mode == 'drive':
    from llmtool import task_drive  # Imported before any hook is installed.
real_flock = task_store.fcntl.flock
real_fdopen = os.fdopen
real_replace = os.replace
real_link = os.link


def stop():
    sys.stdout.write('AT ' + point + '\n')
    sys.stdout.flush()
    sys.stdin.read()  # The parent sends SIGKILL while this process waits here.
    os._exit(97)


def task_temporary(name):
    return (isinstance(name, str) and name.startswith('.' + task_id + '.') and
            name.endswith('.tmp'))


if point == 'after_lock':
    def flock(fd, operation):
        result = real_flock(fd, operation)
        if operation & task_store.fcntl.LOCK_EX:
            stop()
        return result
    task_store.fcntl.flock = flock
elif point == 'mid_temp_write':
    class PartialWrite:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.stream.close()
            return False

        def fileno(self):
            return self.stream.fileno()

        def flush(self):
            self.stream.flush()

        def write(self, data):
            self.stream.write(data[:len(data) // 2])
            self.stream.flush()
            stop()

    def fdopen(fd, mode='r', *args, **kwargs):
        stream = real_fdopen(fd, mode, *args, **kwargs)
        return PartialWrite(stream) if mode == 'wb' else stream
    os.fdopen = fdopen
elif point == 'before_link':
    def link(src, dst, *args, **kwargs):
        if task_temporary(src):
            stop()
        return real_link(src, dst, *args, **kwargs)
    os.link = link
elif point == 'before_rename':
    def replace(src, dst, *args, **kwargs):
        if task_temporary(src):
            stop()
        return real_replace(src, dst, *args, **kwargs)
    os.replace = replace
elif point == 'after_rename':
    def replace(src, dst, *args, **kwargs):
        result = real_replace(src, dst, *args, **kwargs)
        if task_temporary(src):
            stop()
        return result
    os.replace = replace
elif point != 'none':
    sys.exit(2)


def load_input():
    with open(input_path, encoding='utf-8') as stream:
        return json.load(stream)


store = TaskStore(root)
if mode == 'update':
    saved = store.update(task_id, state='PM_UNAVAILABLE', error_code='PROVIDER_TIMEOUT')
    print('DONE', saved['state'], flush=True)
elif mode == 'create':
    saved = store.create(load_input())
    print('DONE', saved['state'], flush=True)
elif mode == 'event':
    result = store.record_completion_event(task_id, **load_input())
    print('DONE', 'created' if result['created'] else 'replayed', flush=True)
elif mode == 'drive':
    event = load_input()
    outcome = {}

    def advance(*, task_id, store, merge, **adapters):
        outcome['created'] = store.record_completion_event(task_id, **event)['created']
        return {'task_id': task_id, 'step': 'CLOSED', 'result': None, 'codes': []}

    def offline(*args, **kwargs):
        raise RuntimeError('this drive step must not poll or read GitHub quota')

    result = task_drive.drive(task_id=task_id, store=store, max_steps=1,
                              advance_call=advance, quota_read=offline, sleep=offline)
    print('DONE', result['step'], 'created' if outcome['created'] else 'replayed',
          flush=True)
else:
    sys.exit(2)
'''

# Points inside TaskStore.update / _write, in execution order.
_UPDATE_POINTS = ('after_lock', 'mid_temp_write', 'before_rename', 'after_rename')
# Points inside TaskStore.create (link-based, no task lock).
_CREATE_POINTS = ('mid_temp_write', 'before_link')


def _pm_record(request='Implement a small tested feature'):
    task_id, run_id = new_id(), new_id()
    request_sha = hashlib.sha256(request.encode()).hexdigest()
    contract = TaskContract(
        schema_version=1, task_id=task_id, contract_version=1,
        previous_digest=None, goal='PM intake request sha256:' + request_sha,
        authorized_scope=('pm-planning',), forbidden_scope=(),
        criteria=(AcceptanceCriterion('pm_response', CriterionKind.ARTIFACT,
                                      'Record the PM reply or unavailable status.'),),
    )
    return {
        'schema_version': 1, 'task_id': task_id, 'run_id': run_id,
        'pm_session_id': 'pm-' + new_id(), 'pm_provider_profile': 'pm',
        'pm_provider_type': 'codex-cli', 'pm_model': 'gpt-6-luna',
        'backend_session_semantics': 'EPHEMERAL_SINGLE_CALL', 'native_session_id': None,
        'state': 'PM_PENDING', 'created_at': '2026-10-21T00:00:00Z',
        'updated_at': '2026-10-21T00:00:00Z', 'task_contract': contract.to_dict(),
        'task_binding': contract.binding(run_id).to_dict(), 'request': request,
        'request_sha256': request_sha, 'response': None, 'usage': None,
        'error_code': None, 'record_sha256': None,
    }


def _active_pm_record():
    """A schema 2 record that accepts completion events (as in test_task_store)."""
    value = _pm_record()
    response = json.dumps({
        'schema_version': 1, 'summary': 'Ready.',
        'developer_assignment': {
            'route': 'opencode', 'risk': 'LOW', 'goal': 'Do task.',
            'acceptance': ['A test passes.'],
        },
    }, separators=(',', ':'))
    value.update(
        schema_version=2,
        pm_provider_profile='active-pm-runtime',
        pm_provider_type='active-pm-runtime',
        pm_model=None,
        pm_route_binding={'type': 'active-pm-runtime', 'model': None, 'base_url': None},
        backend_session_semantics='ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL',
        state='PM_RESPONDED',
        response=response,
        error_code=None,
        developer=None,
        pm_assignment={
            'schema_version': 1, 'route': 'opencode', 'risk': 'LOW',
            'goal': 'Do task.', 'acceptance': ['A test passes.'],
        },
        completion=None,
        pm_provenance={
            'source': 'ACTIVE_PM_RUNTIME', 'provider_invoked': False,
            'native_session_id_status': 'UNAVAILABLE', 'usage_status': 'UNKNOWN',
            'session_restored': False, 'automatic_callback': False,
        },
    )
    return value


def _event_args(value):
    return dict(event_id=new_id(), run_id=value['run_id'], session_id='ses_Crash1',
                source='DIRECT_D6_DEVELOPER', source_sha='a' * 40, sequence=0,
                payload_sha256='b' * 64)


class _Lines:
    """Read whole lines from a child pipe with select(); no timed guesses."""

    def __init__(self, stream):
        self.fd = stream.fileno()
        self.buffer = b''

    def readline(self, timeout=_SYNC_TIMEOUT_SECONDS):
        deadline = time.monotonic() + timeout
        while b'\n' not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            ready, _, _ = select.select([self.fd], [], [], remaining)
            if not ready:
                return None
            chunk = os.read(self.fd, 4096)
            if not chunk:
                return None
            self.buffer += chunk
        line, _, self.buffer = self.buffer.partition(b'\n')
        return line.decode('utf-8', 'replace').strip()


@unittest.skipUnless(os.name == 'posix' and hasattr(signal, 'SIGKILL'),
                     'needs POSIX processes and SIGKILL')
class CrashResumeRealProcessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.home = self.base / 'home'
        self.home.mkdir(mode=0o700)
        self.inputs = self.base / 'inputs'
        self.inputs.mkdir(mode=0o700)
        self.root = self.base / 'store' / 'tasks'
        self.store = TaskStore(self.root)

    # -- process helpers -------------------------------------------------

    def _env(self):
        env = os.environ.copy()
        package_root = str(Path(__file__).resolve().parents[1])
        env['PYTHONPATH'] = package_root + os.pathsep + env.get('PYTHONPATH', '')
        env['HOME'] = str(self.home)
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        return env

    def _input(self, value):
        path = self.inputs / (new_id() + '.json')
        path.write_text(json.dumps(value), encoding='utf-8')
        return str(path)

    def _start(self, mode, task_id, point, input_path):
        process = subprocess.Popen(
            [sys.executable, '-B', '-c', _CHILD, str(self.root), mode, task_id,
             point, input_path or ''],
            env=self._env(), cwd=str(self.base), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        self.addCleanup(self._stop, process)
        return process

    @staticmethod
    def _stop(process):
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()

    def _child_failed(self, process, message):
        self._stop_keep_stderr(process)
        stderr = process.stderr.read().decode('utf-8', 'replace')[-4000:]
        self.fail(f'{message}; exit={process.returncode}; stderr={stderr}')

    @staticmethod
    def _stop_keep_stderr(process):
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)

    def _crash(self, mode, task_id, point, input_path=None, before_kill=None):
        """Run ``mode`` in a child, wait until it reaches ``point``, SIGKILL it."""
        process = self._start(mode, task_id, point, input_path)
        line = _Lines(process.stdout).readline()
        if line != 'AT ' + point:
            self._child_failed(process, f'child did not reach {point}: {line!r}')
        self.assertIsNone(process.poll())
        if before_kill is not None:
            before_kill()
        os.kill(process.pid, signal.SIGKILL)
        self.assertEqual(process.wait(timeout=10), -signal.SIGKILL)

    def _rerun(self, mode, task_id, input_path=None):
        """Run ``mode`` again in a fresh, unhooked child and return its DONE line."""
        process = self._start(mode, task_id, 'none', input_path)
        process.stdin.close()
        line = _Lines(process.stdout).readline()
        try:
            code = process.wait(timeout=_SYNC_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            code = None
        if code != 0 or line is None or not line.startswith('DONE'):
            self._child_failed(process, f'rerun did not complete: {line!r}')
        return line

    # -- store observations ----------------------------------------------

    def _raw(self, task_id):
        return (self.root / (task_id + '.json')).read_bytes()

    def _lock_path(self, task_id):
        return self.root / ('.' + task_id + '.json.lock')

    def _lock_is_free(self, task_id):
        lock = self._lock_path(task_id)
        if not lock.exists():
            return True
        fd = os.open(lock, os.O_RDWR | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            fcntl.flock(fd, fcntl.LOCK_UN)
            return True
        finally:
            os.close(fd)

    def _temporaries(self, task_id):
        prefix = '.' + task_id + '.'
        return sorted(path for path in self.root.iterdir()
                      if path.name.startswith(prefix) and path.name.endswith('.tmp'))

    def _assert_kill_point_evidence(self, task_id, point, *, new_state=None):
        """Prove the kill hit the intended point from what it left behind."""
        temporaries = self._temporaries(task_id)
        if point == 'mid_temp_write':
            self.assertEqual(len(temporaries), 1, temporaries)
            partial = temporaries[0].read_bytes()
            self.assertTrue(partial)
            self.assertFalse(partial.endswith(b'\n'))
            with self.assertRaises(ValueError):
                json.loads(partial.decode('utf-8'))
        elif point in ('before_rename', 'before_link'):
            self.assertEqual(len(temporaries), 1, temporaries)
            complete = temporaries[0].read_bytes()
            self.assertTrue(complete.endswith(b'\n'))
            staged = json.loads(complete.decode('utf-8'))
            self.assertEqual(staged['task_id'], task_id)
            if new_state is not None:
                self.assertEqual(staged['state'], new_state)
        else:
            self.assertEqual(temporaries, [])

    def _assert_no_stale_lock(self, task_id):
        self.assertTrue(self._lock_is_free(task_id),
                        'a killed writer left the task lock held')

    # -- cases -------------------------------------------------------------

    def test_update_killed_at_each_point_leaves_old_or_new_record(self):
        created = []
        for point in _UPDATE_POINTS:
            with self.subTest(point=point):
                value = _pm_record()
                task_id = value['task_id']
                old = self.store.create(value)
                created.append(task_id)
                old_raw = self._raw(task_id)

                def lock_is_held(task_id=task_id):
                    # Every update point is inside the task lock.
                    self.assertFalse(self._lock_is_free(task_id),
                                     'the child should hold the task lock here')

                self._crash('update', task_id, point, before_kill=lock_is_held)

                self._assert_kill_point_evidence(task_id, point, new_state='PM_UNAVAILABLE')
                saved = self.store.get(task_id)  # Full validation and digest check.
                if point == 'after_rename':
                    self.assertNotEqual(self._raw(task_id), old_raw)
                    self.assertEqual(saved['state'], 'PM_UNAVAILABLE')
                    self.assertEqual(saved['error_code'], 'PROVIDER_TIMEOUT')
                else:
                    self.assertEqual(self._raw(task_id), old_raw)
                    self.assertEqual(saved, old)
                self._assert_no_stale_lock(task_id)
                self.assertEqual(self.store.task_ids(), sorted(created))

                self.assertEqual(self._rerun('update', task_id), 'DONE PM_UNAVAILABLE')
                final = self.store.get(task_id)
                self.assertEqual(final['state'], 'PM_UNAVAILABLE')
                self.assertEqual(final['error_code'], 'PROVIDER_TIMEOUT')
                for key in set(old) - {'state', 'error_code', 'updated_at', 'record_sha256'}:
                    self.assertEqual(final[key], old[key], key)
                self._assert_no_stale_lock(task_id)
                self.assertEqual(self.store.task_ids(), sorted(created))

    def test_create_killed_before_install_leaves_no_record_and_rerun_creates(self):
        for point in _CREATE_POINTS:
            with self.subTest(point=point):
                value = _pm_record()
                task_id = value['task_id']
                input_path = self._input(value)

                self._crash('create', task_id, point, input_path)

                self._assert_kill_point_evidence(task_id, point, new_state='PM_PENDING')
                self.assertFalse((self.root / (task_id + '.json')).exists())
                self.assertNotIn(task_id, self.store.task_ids())
                with self.assertRaises(LLMToolError):
                    self.store.get(task_id)

                self.assertEqual(self._rerun('create', task_id, input_path), 'DONE PM_PENDING')
                saved = self.store.get(task_id)
                for key in set(value) - {'record_sha256'}:
                    self.assertEqual(saved[key], value[key], key)
                self.assertIn(task_id, self.store.task_ids())
                self._assert_no_stale_lock(task_id)

    def test_completion_event_killed_at_each_point_is_recorded_once(self):
        for point in _UPDATE_POINTS:
            with self.subTest(point=point):
                value = _active_pm_record()
                task_id = value['task_id']
                old = self.store.create(value)
                args = _event_args(value)
                input_path = self._input(args)

                self._crash('event', task_id, point, input_path)

                self._assert_kill_point_evidence(task_id, point)
                saved = self.store.get(task_id)
                stored = [event['event_id'] for event in saved.get('completion_events', [])]
                if point == 'after_rename':
                    self.assertEqual(stored, [args['event_id']])
                    killed_event = saved['completion_events'][0]
                else:
                    self.assertEqual(saved, old)
                    self.assertEqual(stored, [])
                self._assert_no_stale_lock(task_id)

                expected = 'DONE replayed' if point == 'after_rename' else 'DONE created'
                self.assertEqual(self._rerun('event', task_id, input_path), expected)
                final = self.store.get(task_id)
                self.assertEqual([event['event_id'] for event in final['completion_events']],
                                 [args['event_id']])
                if point == 'after_rename':
                    self.assertEqual(final['completion_events'][0], killed_event)
                    self.assertEqual(final, saved)
                self._assert_no_stale_lock(task_id)

    def test_drive_step_killed_mid_write_resumes_without_duplicate_effect(self):
        for point in ('mid_temp_write', 'after_rename'):
            with self.subTest(point=point):
                value = _active_pm_record()
                task_id = value['task_id']
                self.store.create(value)
                args = _event_args(value)
                input_path = self._input(args)

                self._crash('drive', task_id, point, input_path)

                self._assert_kill_point_evidence(task_id, point)
                saved = self.store.get(task_id)
                stored = [event['event_id'] for event in saved.get('completion_events', [])]
                self.assertEqual(stored, [args['event_id']] if point == 'after_rename' else [])
                self._assert_no_stale_lock(task_id)

                expected = ('DONE CLOSED replayed' if point == 'after_rename'
                            else 'DONE CLOSED created')
                self.assertEqual(self._rerun('drive', task_id, input_path), expected)
                final = self.store.get(task_id)
                self.assertEqual([event['event_id'] for event in final['completion_events']],
                                 [args['event_id']])
                self._assert_no_stale_lock(task_id)


if __name__ == '__main__':
    unittest.main()
