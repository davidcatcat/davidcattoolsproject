import hashlib
import json
import os
import errno
import select
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool.action_contract import TaskBinding
from llmtool.errors import LLMToolError
from llmtool import worker_dispatch
from llmtool.task_store import TaskStore, TaskStoreConflict, new_id
from llmtool import task_store as task_store_module
from llmtool.task_submission_cli import _record
from llmtool.task_verification import AcceptanceCriterion, CriterionKind, TaskContract


def record(request='Implement a small tested feature'):
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


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'tasks'
        self.store = TaskStore(self.root)

    def _codex_dispatch_task(self):
        request = 'Bounded non-sensitive Codex proposal task.'
        task_id, run_id = new_id(), new_id()
        request_sha = hashlib.sha256(request.encode()).hexdigest()
        task = _record(
            task_id, run_id, 'pm-' + new_id(), 'pm-profile', request,
            state='PM_RESPONDED', provider_type='openai-compatible', model='pm-model',
            backend='NO_NATIVE_SESSION_ID_EXPOSED', request_sha=request_sha)
        task.update(
            pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                              'base_url': None},
            response='PM returned a bounded task.', error_code=None,
            pm_assignment={'schema_version': 1, 'route': 'codex', 'risk': 'LOW',
                           'goal': 'Produce a proposal diff.',
                           'acceptance': ['the proposal is recorded']},
        )
        return task

    @staticmethod
    def _developer_assignment(*, provider='codex', max_tokens=None):
        return {
            'assignment_id': new_id(), 'selection_source': 'PM',
            'state': 'DEVELOPER_PENDING', 'provider_profile': provider,
            'provider_type': None, 'model': None,
            'backend_session_semantics': 'NO_NATIVE_SESSION_ID_EXPOSED',
            'session_id': 'dev-' + new_id(), 'source_sha': 'a' * 40,
            'source_sha_binding': 'D6_REGISTRY_OBSERVED',
            'read_paths': ['src.py'], 'write_paths': ['src.py'],
            'acceptance': ['the proposal is recorded'],
            'context_sha256': 'b' * 64,
            'egress_basis': 'Approved non-sensitive test context.', 'input_bytes': 10,
            'max_tokens': max_tokens, 'artifact_format': 'unified_diff',
            'artifact': None, 'artifact_sha256': None, 'usage': None,
            'error_code': None, 'created_at': '2026-10-23T00:00:00Z',
        }

    def _allow_codex_dispatch(self):
        policy_path = Path(worker_dispatch._POLICY_PATH)
        policy = json.loads(policy_path.read_text(encoding='utf-8'))
        policy['workers']['codex-developer']['dispatch'] = 'ALLOWED'
        temporary_policy = Path(self.temp.name) / 'worker-dispatch.json'
        temporary_policy.write_text(json.dumps(policy), encoding='utf-8')
        patcher = patch.object(worker_dispatch, '_POLICY_PATH', temporary_policy)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_codex_cli_dispatch_and_response_allow_unknown_token_cap(self):
        self._allow_codex_dispatch()
        task = self._codex_dispatch_task()
        self.store.create(task)
        pending = self._developer_assignment(provider='codex', max_tokens=None)
        self.store.compare_and_update(
            task['task_id'], expected={'developer': None}, developer=pending)

        dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                       'provider_type': 'codex-cli', 'model': 'gpt-6-luna'}
        self.store.update(task['task_id'], developer=dispatching)
        artifact = ('diff --git a/src.py b/src.py\n'
                    '--- a/src.py\n+++ b/src.py\n@@ -1 +1 @@\n-x=1\n+x=2\n')
        responded = {
            **dispatching, 'state': 'DEVELOPER_RESPONDED', 'artifact': artifact,
            'artifact_sha256': hashlib.sha256(artifact.encode()).hexdigest(),
        }
        saved = self.store.update(task['task_id'], developer=responded)
        self.assertEqual(saved['developer']['provider_profile'], 'codex')
        self.assertEqual(saved['developer']['provider_type'], 'codex-cli')
        self.assertIsNone(saved['developer']['max_tokens'])
        self.assertIsNone(saved['developer']['usage'])
        before_clear = saved
        with self.assertRaises(worker_dispatch.WorkerDispatchDenied) as caught:
            self.store.update(task['task_id'], developer=None)
        self.assertEqual(caught.exception.dispatch_code,
                         'WORKER_ASSIGNMENT_CLEAR_FORBIDDEN')
        after_clear = self.store.get(task['task_id'])
        self.assertEqual(after_clear['developer'], before_clear['developer'])
        self.assertEqual(len(after_clear['dispatch_audit']),
                         len(before_clear['dispatch_audit']) + 1)
        refusal = after_clear['dispatch_audit'][-1]
        self.assertEqual(refusal['worker_key'], 'codex-developer')
        self.assertEqual(refusal['action'], 'REASSIGN')
        self.assertEqual(refusal['decision'], 'BLOCKED')
        self.assertEqual(refusal['reason_code'], 'WORKER_ASSIGNMENT_CLEAR_FORBIDDEN')
        self.assertRegex(refusal['policy_sha256'], r'^[0-9a-f]{64}$')

    def test_codex_direct_success_persists_native_thread_and_usage(self):
        task = self._codex_dispatch_task()
        self.store.create(task)
        pending = self._developer_assignment(provider='codex', max_tokens=None)
        pending['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
        self.store.compare_and_update(
            task['task_id'], expected={'developer': None}, developer=pending)
        dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                       'provider_type': 'codex-cli', 'model': 'gpt-6-luna'}
        self.store.update(task['task_id'], developer=dispatching)
        artifact = ('diff --git a/src.py b/src.py\n'
                    '--- a/src.py\n+++ b/src.py\n@@ -1 +1 @@\n-x=1\n+x=2\n')
        execution = {'status': 'DONE',
            'native_session_id': '12345678-1234-4234-8234-123456789abc',
            'process_exit_code': 0, 'stdout_sha256': 'c' * 64, 'elapsed_ms': 12,
            'cleanup_status': 'CONFIRMED', 'usage_source': 'CLI_REPORTED',
            'source_bytes': None, 'source_file_count': None,
            'source_manifest_sha256': None}
        responded = {**dispatching, 'state': 'DEVELOPER_RESPONDED',
            'artifact': artifact, 'artifact_sha256': hashlib.sha256(artifact.encode()).hexdigest(),
            'execution': execution,
            'usage': {'prompt_tokens': 13, 'completion_tokens': 8, 'total_tokens': 21}}
        saved = self.store.update(task['task_id'], developer=responded)
        self.assertEqual(saved['developer']['execution']['status'], 'DONE')
        self.assertEqual(saved['developer']['execution']['native_session_id'],
                         execution['native_session_id'])
        self.assertEqual(saved['developer']['usage']['total_tokens'], 21)

    def test_codex_blocked_and_timeout_outcomes_persist_reported_usage(self):
        usage = {'prompt_tokens': 13, 'completion_tokens': 8, 'total_tokens': 21}
        for status, error_code, process_exit in (
                ('BLOCKED', 'CODEX_BLOCKED', 0), ('TIMEOUT', 'CODEX_TIMEOUT', None)):
            with self.subTest(status=status):
                task = self._codex_dispatch_task()
                self.store.create(task)
                pending = self._developer_assignment(provider='codex', max_tokens=None)
                pending['backend_session_semantics'] = 'CLI_REPORTED_SESSION_ID'
                self.store.compare_and_update(
                    task['task_id'], expected={'developer': None}, developer=pending)
                dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                    'provider_type': 'codex-cli', 'model': 'gpt-6-luna'}
                self.store.update(task['task_id'], developer=dispatching)
                execution = {'status': status,
                    'native_session_id': '12345678-1234-4234-8234-123456789abc',
                    'process_exit_code': process_exit, 'stdout_sha256': 'd' * 64,
                    'elapsed_ms': 25, 'cleanup_status': 'CONFIRMED',
                    'usage_source': 'CLI_REPORTED', 'source_bytes': None,
                    'source_file_count': None, 'source_manifest_sha256': None}
                outcome = {**dispatching, 'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                    'error_code': error_code, 'execution': execution, 'usage': usage}
                saved = self.store.update(task['task_id'], developer=outcome)
                self.assertEqual(saved['developer']['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
                self.assertEqual(saved['developer']['execution']['status'], status)
                self.assertEqual(saved['developer']['usage'], usage)

    def test_unknown_token_cap_is_rejected_for_other_routes_and_codex_cannot_claim_one(self):
        self._allow_codex_dispatch()
        task = self._codex_dispatch_task()
        self.store.create(task)
        pending = self._developer_assignment(provider='company-gw', max_tokens=None)
        self.store.compare_and_update(
            task['task_id'], expected={'developer': None}, developer=pending)
        dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                       'provider_type': 'openai-compatible', 'model': 'gw-model'}
        with self.assertRaises(LLMToolError):
            self.store.update(task['task_id'], developer=dispatching)

        other = self._codex_dispatch_task()
        claimed_cap = self._developer_assignment(provider='codex', max_tokens=64)
        other['developer'] = claimed_cap
        with self.assertRaises(LLMToolError):
            self.store.create(other)

    def test_owner_private_creation_and_second_store_read(self):
        value = record()
        created = self.store.create(value)
        loaded = TaskStore(self.root).get(value['task_id'])
        self.assertEqual(created, loaded)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        path = self.root / (value['task_id'] + '.json')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(path.stat().st_nlink, 1)
        self.assertEqual(loaded['task_binding']['task_id'], loaded['task_id'])

    def test_atomic_update_preserves_integrity_and_retains_lock_inode(self):
        value = record()
        self.store.create(value)
        changed = self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                    error_code='PROVIDER_TIMEOUT')
        self.assertEqual(changed['state'], 'PM_UNAVAILABLE')
        self.assertEqual(changed['error_code'], 'PROVIDER_TIMEOUT')
        self.assertEqual(self.store.get(value['task_id']), changed)
        lock = self.root / ('.' + value['task_id'] + '.json.lock')
        self.assertTrue(lock.is_file())
        self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o600)
        self.assertEqual(lock.stat().st_nlink, 1)

    def test_failed_atomic_replace_keeps_previous_record_and_releases_lock(self):
        value = record()
        self.store.create(value)
        previous = self.store.get(value['task_id'])
        with patch('llmtool.task_store.os.replace', side_effect=OSError('private detail')):
            with self.assertRaises(LLMToolError) as caught:
                self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                  error_code='PROVIDER_TIMEOUT')
        self.assertEqual(self.store.get(value['task_id']), previous)
        self.assertEqual(caught.exception.task_store_code, 'UNAVAILABLE')
        lock = self.root / ('.' + value['task_id'] + '.json.lock')
        self.assertTrue(lock.is_file())
        self.assertFalse(any(path.name.endswith('.tmp') for path in self.root.iterdir()))

    def _start_lock_holder(self, lock_path):
        script = (
            'import fcntl, os, sys; '
            'fd=os.open(sys.argv[1], os.O_RDWR|os.O_CREAT, 0o600); '
            'fcntl.flock(fd, fcntl.LOCK_EX); print("LOCKED", flush=True); '
            'sys.stdin.readline(); fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)'
        )
        process = subprocess.Popen(
            [sys.executable, '-B', '-c', script, str(lock_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True)
        self.assertEqual(process.stdout.readline().strip(), 'LOCKED')
        self.addCleanup(self._stop_process, process)
        return process

    @staticmethod
    def _stop_process(process):
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()

    def _writer_env(self):
        env = os.environ.copy()
        package_root = str(Path(__file__).resolve().parents[1])
        env['PYTHONPATH'] = package_root + os.pathsep + env.get('PYTHONPATH', '')
        return env

    @staticmethod
    def _readline_timeout(process, timeout=5):
        ready, _, _ = select.select([process.stdout], [], [], timeout)
        if not ready:
            return None
        return process.stdout.readline().strip()

    def test_sigkill_releases_lock_and_next_update_succeeds(self):
        value = record()
        self.store.create(value)
        lock = self.root / ('.' + value['task_id'] + '.json.lock')
        holder = self._start_lock_holder(lock)
        holder.kill()
        self.assertEqual(holder.wait(timeout=5), -signal.SIGKILL)

        changed = self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                    error_code='PROVIDER_TIMEOUT')
        self.assertEqual(changed['state'], 'PM_UNAVAILABLE')
        self.assertEqual(changed['error_code'], 'PROVIDER_TIMEOUT')
        self.assertTrue(lock.is_file())

    def test_two_process_updates_wait_for_lock_and_both_succeed(self):
        value = record()
        self.store.create(value)
        lock = self.root / ('.' + value['task_id'] + '.json.lock')
        holder = self._start_lock_holder(lock)
        script = (
            'import fcntl, sys\n'
            'from llmtool import task_store\n'
            'from llmtool.task_store import TaskStore\n'
            'real_flock = fcntl.flock\n'
            'def observed_flock(fd, operation):\n'
            '    try:\n'
            '        return real_flock(fd, operation)\n'
            '    except BlockingIOError:\n'
            '        print("WAITING", flush=True)\n'
            '        raise\n'
            'task_store.fcntl.flock = observed_flock\n'
            # A stalled parent must not turn the waiter into BUSY; that path
            # has its own test.
            'task_store.TASK_UPDATE_LOCK_TIMEOUT_SECONDS = 30\n'
            'print("READY", flush=True)\n'
            'TaskStore(sys.argv[1]).update(sys.argv[2], state="PM_UNAVAILABLE", '
            'error_code="PROVIDER_TIMEOUT")\n'
            'print("OK", flush=True)\n'
        )
        writers = [subprocess.Popen(
            [sys.executable, '-B', '-c', script, str(self.root), value['task_id']],
            env=self._writer_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True) for _ in range(2)]
        for writer in writers:
            self.addCleanup(self._stop_process, writer)
        try:
            for writer in writers:
                self.assertEqual(self._readline_timeout(writer), 'READY')
            for writer in writers:
                self.assertEqual(self._readline_timeout(writer), 'WAITING')
            self.assertTrue(all(writer.poll() is None for writer in writers))
        finally:
            holder.stdin.write('release\n')
            holder.stdin.flush()
            self.assertEqual(holder.wait(timeout=5), 0)
        results = [writer.communicate(timeout=5) for writer in writers]
        self.assertTrue(all(writer.returncode == 0 for writer in writers), results)
        self.assertTrue(all(stdout.strip().endswith('OK') for stdout, _ in results), results)
        saved = self.store.get(value['task_id'])
        self.assertEqual(saved['state'], 'PM_UNAVAILABLE')
        self.assertEqual(saved['error_code'], 'PROVIDER_TIMEOUT')

    def test_concurrent_process_cas_keeps_real_conflict(self):
        value = record()
        self.store.create(value)
        lock_name = '.' + value['task_id'] + '.json.lock'
        lock_path = self.root / lock_name
        self.assertFalse(lock_path.exists())
        barrier = Path(self.temp.name) / 'first-lock-barrier'
        barrier.mkdir(mode=0o700)
        start = barrier / 'start'
        open_release = barrier / 'open-release'
        owner_release = barrier / 'owner-release'
        script = (
            'import fcntl, os, sys, time\n'
            'from llmtool import task_store\n'
            'from llmtool.task_store import TaskStore, TaskStoreConflict\n'
            'root, task_id, lock_name, start, open_release, owner_release = sys.argv[1:]\n'
            'print("READY", flush=True)\n'
            'while not os.path.exists(start): time.sleep(0.005)\n'
            'real_open = os.open\n'
            'def synchronized_open(path, flags, *args, **kwargs):\n'
            '    fd = real_open(path, flags, *args, **kwargs)\n'
            '    if path == lock_name:\n'
            '        inode = os.fstat(fd).st_ino\n'
            '        print("OPENED", inode, flush=True)\n'
            '        while not os.path.exists(open_release): time.sleep(0.005)\n'
            '    return fd\n'
            'os.open = synchronized_open\n'
            'real_flock = fcntl.flock\n'
            'first_acquisition = [True]\n'
            'def observed_flock(fd, operation):\n'
            '    try:\n'
            '        result = real_flock(fd, operation)\n'
            '    except BlockingIOError:\n'
            '        print("WAITING", flush=True)\n'
            '        raise\n'
            '    if first_acquisition[0]:\n'
            '        first_acquisition[0] = False\n'
            '        print("ACQUIRED", flush=True)\n'
            '        while not os.path.exists(owner_release): time.sleep(0.005)\n'
            '    return result\n'
            'task_store.fcntl.flock = observed_flock\n'
            # A stalled parent must not turn the waiter into BUSY; that path
            # has its own test.
            'task_store.TASK_UPDATE_LOCK_TIMEOUT_SECONDS = 30\n'
            'try:\n'
            '    TaskStore(root).compare_and_update(task_id, expected={"state": "PM_PENDING"}, '
            'state="PM_UNAVAILABLE", error_code="PROVIDER_TIMEOUT")\n'
            'except TaskStoreConflict as exc:\n'
            '    print("CONFLICT", exc.task_store_code, flush=True)\n'
            '    sys.exit(3)\n'
            'print("UPDATED", flush=True)\n'
        )
        writers = [subprocess.Popen(
            [sys.executable, '-B', '-c', script, str(self.root), value['task_id'],
             lock_name, str(start), str(open_release), str(owner_release)],
            env=self._writer_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True) for _ in range(2)]
        for writer in writers:
            self.addCleanup(self._stop_process, writer)
        try:
            for writer in writers:
                self.assertEqual(self._readline_timeout(writer), 'READY')
            start.touch(mode=0o600)
            inodes = []
            for writer in writers:
                line = self._readline_timeout(writer)
                self.assertIsNotNone(line)
                fields = line.split()
                self.assertEqual(fields[0], 'OPENED', line)
                inodes.append(int(fields[1]))
            self.assertEqual(inodes[0], inodes[1])
            self.assertEqual(lock_path.stat().st_ino, inodes[0])
            open_release.touch(mode=0o600)
            states = [self._readline_timeout(writer) for writer in writers]
            self.assertEqual(sorted(states), ['ACQUIRED', 'WAITING'], states)
        finally:
            open_release.touch(mode=0o600)
            owner_release.touch(mode=0o600)
        results = [writer.communicate(timeout=5) for writer in writers]
        self.assertEqual(sorted(writer.returncode for writer in writers), [0, 3], results)
        self.assertTrue(any('CONFLICT' in stdout for stdout, _ in results), results)
        saved = self.store.get(value['task_id'])
        self.assertEqual(saved['state'], 'PM_UNAVAILABLE')

    def test_lock_wait_timeout_is_bounded_and_classified_busy(self):
        value = record()
        self.store.create(value)
        lock = self.root / ('.' + value['task_id'] + '.json.lock')
        holder = self._start_lock_holder(lock)
        started = time.monotonic()
        with patch.object(task_store_module, 'TASK_UPDATE_LOCK_TIMEOUT_SECONDS', 0.1):
            with self.assertRaises(LLMToolError) as caught:
                self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                  error_code='PROVIDER_TIMEOUT')
        elapsed = time.monotonic() - started
        self.assertEqual(caught.exception.task_store_code, 'BUSY')
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_BUSY')
        self.assertGreaterEqual(elapsed, 0.08)
        self.assertLess(elapsed, 1.0)
        holder.stdin.write('release\n')
        holder.stdin.flush()
        self.assertEqual(holder.wait(timeout=5), 0)

    def test_concurrent_lock_create_enoent_is_retried(self):
        # macOS openat(O_CREAT) may report ENOENT while another process
        # creates the same lock name; the next attempt opens that file.
        value = record()
        self.store.create(value)
        lock_name = '.' + value['task_id'] + '.json.lock'
        original_open = os.open
        attempts = []

        def lose_first_create(path, flags, *args, **kwargs):
            if path == lock_name:
                attempts.append(flags)
                if len(attempts) == 1:
                    raise FileNotFoundError(errno.ENOENT, 'private filesystem detail')
            return original_open(path, flags, *args, **kwargs)

        with patch.object(task_store_module.os, 'open', side_effect=lose_first_create):
            changed = self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                        error_code='PROVIDER_TIMEOUT')
        self.assertEqual(changed['state'], 'PM_UNAVAILABLE')
        self.assertEqual(len(attempts), 2)
        self.assertTrue(all(flags & os.O_CREAT and flags & os.O_NOFOLLOW for flags in attempts))

    def test_persistent_lock_create_enoent_is_bounded_and_unavailable(self):
        value = record()
        self.store.create(value)
        lock_name = '.' + value['task_id'] + '.json.lock'
        original_open = os.open
        attempts = []

        def always_enoent(path, flags, *args, **kwargs):
            if path == lock_name:
                attempts.append(path)
                raise FileNotFoundError(errno.ENOENT, 'private filesystem detail')
            return original_open(path, flags, *args, **kwargs)

        started = time.monotonic()
        with patch.object(task_store_module, 'TASK_UPDATE_LOCK_TIMEOUT_SECONDS', 0.1), \
                patch.object(task_store_module.os, 'open', side_effect=always_enoent):
            with self.assertRaises(LLMToolError) as caught:
                self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                  error_code='PROVIDER_TIMEOUT')
        elapsed = time.monotonic() - started
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_UNAVAILABLE')
        self.assertNotIn('private filesystem detail', str(caught.exception))
        self.assertGreater(len(attempts), 1)
        self.assertGreaterEqual(elapsed, 0.08)
        self.assertLess(elapsed, 1.0)
        self.assertEqual(self.store.get(value['task_id'])['state'], value['state'])

    def test_planted_lock_paths_are_refused_without_touching_them(self):
        value = record()
        self.store.create(value)
        lock = self.root / ('.' + value['task_id'] + '.json.lock')
        outside = Path(self.temp.name) / 'outside-target'

        def symlink():
            outside.write_bytes(b'outside')
            outside.chmod(0o600)
            lock.symlink_to(outside)

        def dangling():
            lock.symlink_to(Path(self.temp.name) / 'missing-target')

        def hardlink():
            outside.write_bytes(b'outside')
            outside.chmod(0o600)
            os.link(outside, lock)

        def loose_mode():
            lock.write_bytes(b'')
            lock.chmod(0o644)

        def fifo():
            os.mkfifo(lock, 0o600)

        def directory():
            lock.mkdir(mode=0o700)

        cases = (('symlink', symlink), ('dangling symlink', dangling), ('hardlink', hardlink),
                 ('mode 0644', loose_mode), ('fifo', fifo), ('directory', directory))
        for label, plant in cases:
            with self.subTest(label):
                plant()
                before = self.store.get(value['task_id'])
                started = time.monotonic()
                with self.assertRaises(LLMToolError) as caught:
                    self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                      error_code='PROVIDER_TIMEOUT')
                self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_INVALID')
                self.assertLess(time.monotonic() - started, 1.0)
                self.assertEqual(self.store.get(value['task_id']), before)
                self.assertFalse((Path(self.temp.name) / 'missing-target').exists())
                if outside.exists():
                    self.assertEqual(outside.read_bytes(), b'outside')
                    self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o600)
                if lock.is_dir() and not lock.is_symlink():
                    lock.rmdir()
                else:
                    lock.unlink()
                if outside.exists():
                    outside.unlink()

    def test_lock_owned_by_another_user_is_refused(self):
        value = record()
        self.store.create(value)
        lock_name = '.' + value['task_id'] + '.json.lock'
        original_open, original_fstat = os.open, os.fstat
        lock_fd = []

        def remember(path, flags, *args, **kwargs):
            fd = original_open(path, flags, *args, **kwargs)
            if path == lock_name:
                lock_fd.append(fd)
            return fd

        def foreign_owner(fd):
            info = original_fstat(fd)
            if lock_fd and fd == lock_fd[0]:
                fields = list(info)
                fields[stat.ST_UID] = os.getuid() + 1
                return os.stat_result(fields)
            return info

        with patch.object(task_store_module.os, 'open', side_effect=remember), \
                patch.object(task_store_module.os, 'fstat', side_effect=foreign_owner):
            with self.assertRaises(LLMToolError) as caught:
                self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                  error_code='PROVIDER_TIMEOUT')
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_INVALID')
        self.assertNotEqual(self.store.get(value['task_id'])['state'], 'PM_UNAVAILABLE')

    def test_lock_open_and_fstat_errors_are_safe_and_close_descriptors(self):
        value = record()
        self.store.create(value)
        lock_name = '.' + value['task_id'] + '.json.lock'
        original_open = os.open
        original_fstat = os.fstat

        def fail_lock_open(path, flags, *args, **kwargs):
            if path == lock_name:
                raise OSError(errno.EIO, 'private filesystem detail')
            return original_open(path, flags, *args, **kwargs)

        with patch.object(task_store_module.os, 'open', side_effect=fail_lock_open):
            with self.assertRaises(LLMToolError) as caught:
                self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                  error_code='PROVIDER_TIMEOUT')
        self.assertEqual(caught.exception.task_store_code, 'UNAVAILABLE')
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_UNAVAILABLE')
        self.assertNotIn('private filesystem detail', str(caught.exception))

        lock_fd = []

        def remember_lock_open(path, flags, *args, **kwargs):
            fd = original_open(path, flags, *args, **kwargs)
            if path == lock_name:
                lock_fd.append(fd)
            return fd

        def fail_lock_fstat(fd):
            if lock_fd and fd == lock_fd[0]:
                raise OSError(errno.EIO, 'private stat detail')
            return original_fstat(fd)

        with patch.object(task_store_module.os, 'open', side_effect=remember_lock_open), \
                patch.object(task_store_module.os, 'fstat', side_effect=fail_lock_fstat):
            with self.assertRaises(LLMToolError) as caught:
                self.store.update(value['task_id'], state='PM_UNAVAILABLE',
                                  error_code='PROVIDER_TIMEOUT')
        self.assertEqual(caught.exception.task_store_code, 'UNAVAILABLE')
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_UNAVAILABLE')
        self.assertNotIn('private stat detail', str(caught.exception))
        self.assertEqual(len(lock_fd), 1)
        with self.assertRaises(OSError) as closed:
            original_fstat(lock_fd[0])
        self.assertEqual(closed.exception.errno, errno.EBADF)

    def test_task_store_failures_expose_safe_distinct_categories(self):
        value = record()
        self.store.create(value)
        self.store.compare_and_update(
            value['task_id'], expected={'state': 'PM_PENDING'},
            state='PM_UNAVAILABLE', error_code='PROVIDER_TIMEOUT')
        with self.assertRaises(TaskStoreConflict) as caught:
            self.store.compare_and_update(
                value['task_id'], expected={'state': 'PM_PENDING'},
                state='PM_UNAVAILABLE', error_code='PROVIDER_TIMEOUT')
        self.assertEqual(caught.exception.task_store_code, 'CONFLICT')
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_CONFLICT')

    def test_unknown_malformed_corrupt_symlink_and_wrong_mode_reject(self):
        with self.assertRaises(LLMToolError):
            self.store.get('../outside')
        with self.assertRaises(LLMToolError):
            self.store.get('0' * 32)
        value = record()
        self.store.create(value)
        path = self.root / (value['task_id'] + '.json')
        raw = json.loads(path.read_text())
        raw['state'] = 'PM_RESPONDED'
        path.write_text(json.dumps(raw))
        with self.assertRaises(LLMToolError):
            self.store.get(value['task_id'])
        path.unlink()
        target = self.root / 'target'
        target.write_text('{}')
        path.symlink_to(target)
        with self.assertRaises(LLMToolError):
            self.store.get(value['task_id'])
        path.unlink()
        self.store.create(value)
        path.chmod(0o644)
        with self.assertRaises(LLMToolError):
            self.store.get(value['task_id'])

    def test_secret_like_request_or_response_never_persists(self):
        for key, secret in (('request', 'api_key=sk-abcdefghijklmnopqrstuvwxyz'),
                            ('response', 'Bearer abcdefghijklmnop')):
            value = record()
            value[key] = secret
            if key == 'request':
                value['request_sha256'] = hashlib.sha256(secret.encode()).hexdigest()
                contract = TaskContract(
                    schema_version=1, task_id=value['task_id'], contract_version=1,
                    previous_digest=None,
                    goal='PM intake request sha256:' + value['request_sha256'],
                    authorized_scope=('pm-planning',), forbidden_scope=(),
                    criteria=(AcceptanceCriterion('pm_response', CriterionKind.ARTIFACT,
                                                  'Record PM outcome.'),),
                )
                value['task_contract'] = contract.to_dict()
                value['task_binding'] = contract.binding(value['run_id']).to_dict()
            else:
                value['state'] = 'PM_RESPONDED'
                value['error_code'] = None
            with self.subTest(key=key), self.assertRaises(LLMToolError):
                self.store.create(value)
        self.assertFalse(self.root.exists())

    def test_state_requires_matching_request_provider_and_outcome(self):
        value = record()
        value.update(state='PM_RESPONDED', request=None,
                     response='A recorded PM reply.', error_code=None)
        with self.assertRaises(LLMToolError):
            self.store.create(value)

        value = record()
        value.update(state='INPUT_REJECTED', backend_session_semantics='EPHEMERAL_SINGLE_CALL',
                     error_code='PM_INPUT_REJECTED')
        with self.assertRaises(LLMToolError):
            self.store.create(value)

    def _active_pm_record(self):
        value = record()
        value.update(
            schema_version=2,
            pm_provider_profile='active-pm-runtime',
            pm_provider_type='active-pm-runtime',
            pm_model=None,
            pm_route_binding={'type': 'active-pm-runtime', 'model': None, 'base_url': None},
            backend_session_semantics='ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL',
            state='PM_RESPONDED',
            response='{"schema_version":1,"summary":"Ready.","developer_assignment":'
                     '{"route":"opencode","risk":"LOW","goal":"Do task.",'
                     '"acceptance":["A test passes."]}}',
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

    def test_active_pm_provenance_is_validated_and_round_trips(self):
        value = self._active_pm_record()
        created = self.store.create(value)
        self.assertEqual(self.store.get(value['task_id']), created)
        self.assertEqual(created['pm_provenance']['usage_status'], 'UNKNOWN')
        self.assertEqual(created['backend_session_semantics'],
                         'ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL')

    def test_legacy_codex_pm_runtime_records_remain_readable(self):
        value = self._active_pm_record()
        value['pm_provider_type'] = 'codex-cli'
        value['pm_route_binding'] = {
            'type': 'codex-cli', 'model': None, 'base_url': None}
        created = self.store.create(value)
        self.assertEqual(self.store.get(value['task_id']), created)
        self.assertEqual(created['pm_provider_type'], 'codex-cli')

    def test_active_pm_provenance_rejects_false_or_incomplete_claims(self):
        mutations = (
            lambda record: record['pm_provenance'].__setitem__('provider_invoked', True),
            lambda record: record['pm_provenance'].__setitem__('session_restored', True),
            lambda record: record['pm_provenance'].__setitem__('automatic_callback', True),
            lambda record: record.__setitem__('usage', {
                'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}),
            lambda record: record.__setitem__('pm_assignment', {
                **record['pm_assignment'], 'risk': 'OTHER'}),
            lambda record: record.__setitem__('pm_assignment', {
                **record['pm_assignment'], 'route': 'company-gw'}),
            lambda record: record.pop('pm_provenance'),
        )
        for mutate in mutations:
            value = self._active_pm_record()
            mutate(value)
            with self.subTest(mutate=mutate), self.assertRaises(LLMToolError):
                self.store.create(value)

    def test_legacy_task_shapes_remain_compatible_without_provenance(self):
        legacy = record()
        self.assertEqual(self.store.create(legacy)['schema_version'], 1)
        self.assertEqual(self.store.get(legacy['task_id'])['task_id'], legacy['task_id'])

        previous_v2 = record()
        previous_v2.update(
            schema_version=2,
            pm_route_binding={'type': 'codex-cli', 'model': 'gpt-6-luna',
                              'base_url': None},
            developer=None,
            pm_assignment=None,
            completion=None,
        )
        saved = self.store.create(previous_v2)
        self.assertEqual(self.store.get(previous_v2['task_id']), saved)
        self.assertNotIn('pm_provenance', saved)

    @staticmethod
    def _completion_event(value, *, event_id=None, sequence=0):
        return dict(event_id=event_id or new_id(), run_id=value['run_id'],
                    session_id='ses_Test123', source='DIRECT_D6_DEVELOPER',
                    source_sha='a' * 40, sequence=sequence,
                    payload_sha256='b' * 64)

    def test_completion_event_persists_before_wake_and_replays_idempotently(self):
        value = self._active_pm_record()
        self.store.create(value)
        args = self._completion_event(value)
        first = self.store.record_completion_event(value['task_id'], **args)
        self.assertTrue(first['created'])
        self.assertEqual(first['event']['state'], 'PENDING')
        with self.assertRaises(LLMToolError) as generic_mutation:
            self.store.update(value['task_id'], completion_events=[])
        self.assertEqual(generic_mutation.exception.task_store_error_code,
                         'COMPLETION_EVENT_MUTATION_FORBIDDEN')
        self.assertEqual(TaskStore(self.root).unacked_completion_events(
            value['task_id']), [first['event']])
        replay = TaskStore(self.root).record_completion_event(value['task_id'], **args)
        self.assertFalse(replay['created'])
        self.assertEqual(replay['event'], first['event'])
        self.assertEqual(len(self.store.get(value['task_id'])['completion_events']), 1)
        with self.assertRaises(LLMToolError) as changed_payload:
            self.store.record_completion_event(value['task_id'],
                **{**args, 'payload_sha256': 'c' * 64})
        self.assertEqual(changed_payload.exception.task_store_error_code,
                         'COMPLETION_EVENT_CONFLICT')
        with self.assertRaises(LLMToolError) as changed_id_same_sequence:
            self.store.record_completion_event(value['task_id'],
                **{**args, 'event_id': new_id()})
        self.assertEqual(changed_id_same_sequence.exception.task_store_error_code,
                         'COMPLETION_EVENT_CONFLICT')
        second = self._completion_event(value, sequence=2)
        self.store.record_completion_event(value['task_id'], **second)
        with self.assertRaises(LLMToolError) as out_of_order:
            self.store.record_completion_event(value['task_id'],
                **self._completion_event(value, sequence=1))
        self.assertEqual(out_of_order.exception.task_store_error_code,
                         'COMPLETION_EVENT_SEQUENCE_INVALID')

    def test_completion_ack_requires_persisted_pm_consumption_and_confirmed_intent(self):
        value = self._active_pm_record()
        self.store.create(value)
        args = self._completion_event(value)
        event_id = args['event_id']
        pm = value['pm_session_id']
        self.store.record_completion_event(value['task_id'], **args)
        with self.assertRaises(LLMToolError) as wrong_pm:
            self.store.consume_completion_event(value['task_id'], event_id,
                pm_session_id='pm-not-the-original', pm_decision_sha256='c' * 64)
        self.assertEqual(wrong_pm.exception.task_store_error_code,
                         'COMPLETION_EVENT_BINDING_MISMATCH')
        with self.assertRaises(LLMToolError):
            self.store.ack_completion_event(value['task_id'], event_id,
                                            pm_session_id=pm, pm_decision_sha256='c' * 64)
        consumed = self.store.consume_completion_event(
            value['task_id'], event_id, pm_session_id=pm, pm_decision_sha256='c' * 64)
        self.assertEqual(consumed['event']['state'], 'PM_CONSUMED')
        self.assertFalse(TaskStore(self.root).consume_completion_event(
            value['task_id'], event_id, pm_session_id=pm,
            pm_decision_sha256='c' * 64)['created'])
        intent_id = new_id()
        intent = self.store.prepare_completion_dispatch_intent(
            value['task_id'], event_id, intent_id=intent_id,
            intent_sha256='d' * 64, pm_session_id=pm)
        self.assertTrue(intent['created'])
        self.assertEqual(intent['event']['dispatch_intent']['state'], 'UNKNOWN')
        with self.assertRaises(LLMToolError) as refused_replay:
            self.store.prepare_completion_dispatch_intent(
                value['task_id'], event_id, intent_id=new_id(),
                intent_sha256='e' * 64, pm_session_id=pm)
        self.assertEqual(refused_replay.exception.task_store_error_code,
                         'COMPLETION_EVENT_CONFLICT')
        self.assertFalse(TaskStore(self.root).prepare_completion_dispatch_intent(
            value['task_id'], event_id, intent_id=intent_id,
            intent_sha256='d' * 64, pm_session_id=pm)['created'])
        with self.assertRaises(LLMToolError):
            self.store.ack_completion_event(value['task_id'], event_id,
                                            pm_session_id=pm, pm_decision_sha256='c' * 64)
        self.assertEqual(TaskStore(self.root).unacked_completion_events(
            value['task_id'])[0]['dispatch_intent']['state'], 'UNKNOWN')
        confirmed = self.store.confirm_completion_dispatch(
            value['task_id'], event_id, intent_id=intent_id,
            receipt_sha256='f' * 64, pm_session_id=pm)
        self.assertEqual(confirmed['event']['dispatch_intent']['state'], 'CONFIRMED')
        self.assertFalse(self.store.confirm_completion_dispatch(
            value['task_id'], event_id, intent_id=intent_id,
            receipt_sha256='f' * 64, pm_session_id=pm)['created'])
        acked = self.store.ack_completion_event(value['task_id'], event_id,
                                                pm_session_id=pm,
                                                pm_decision_sha256='c' * 64)
        self.assertEqual(acked['event']['state'], 'ACKED')
        self.assertEqual(TaskStore(self.root).unacked_completion_events(
            value['task_id']), [])
        self.assertFalse(self.store.ack_completion_event(
            value['task_id'], event_id, pm_session_id=pm,
            pm_decision_sha256='c' * 64)['created'])

    def test_completion_persist_failures_preserve_previous_transition(self):
        value = self._active_pm_record()
        self.store.create(value)
        args = self._completion_event(value)
        pm = value['pm_session_id']
        prior = self.store.get(value['task_id'])
        with patch('llmtool.task_store.os.replace', side_effect=OSError('private detail')):
            with self.assertRaises(LLMToolError):
                self.store.record_completion_event(value['task_id'], **args)
        self.assertEqual(self.store.get(value['task_id']), prior)
        self.store.record_completion_event(value['task_id'], **args)
        self.store.consume_completion_event(value['task_id'], args['event_id'],
                                            pm_session_id=pm,
                                            pm_decision_sha256='c' * 64)
        consumed = self.store.get(value['task_id'])
        with patch('llmtool.task_store.os.replace', side_effect=OSError('private detail')):
            with self.assertRaises(LLMToolError):
                self.store.prepare_completion_dispatch_intent(
                    value['task_id'], args['event_id'],
                    intent_id=new_id(), intent_sha256='d' * 64, pm_session_id=pm)
        self.assertEqual(TaskStore(self.root).get(value['task_id']), consumed)
        intent_id = new_id()
        self.store.prepare_completion_dispatch_intent(
            value['task_id'], args['event_id'],
            intent_id=intent_id, intent_sha256='d' * 64, pm_session_id=pm)
        intent_pending = self.store.get(value['task_id'])
        with patch('llmtool.task_store.os.replace', side_effect=OSError('private detail')):
            with self.assertRaises(LLMToolError):
                self.store.confirm_completion_dispatch(
                    value['task_id'], args['event_id'], intent_id=intent_id,
                    receipt_sha256='e' * 64, pm_session_id=pm)
        self.assertEqual(TaskStore(self.root).get(value['task_id']), intent_pending)
        self.store.confirm_completion_dispatch(
            value['task_id'], args['event_id'], intent_id=intent_id,
            receipt_sha256='e' * 64, pm_session_id=pm)
        confirmed = self.store.get(value['task_id'])
        with patch('llmtool.task_store.os.replace', side_effect=OSError('private detail')):
            with self.assertRaises(LLMToolError):
                self.store.ack_completion_event(value['task_id'], args['event_id'],
                    pm_session_id=pm, pm_decision_sha256='c' * 64)
        self.assertEqual(TaskStore(self.root).get(value['task_id']), confirmed)

    def test_completion_concurrent_duplicate_reconciles_without_second_event(self):
        value = self._active_pm_record()
        self.store.create(value)
        args = self._completion_event(value)
        other = TaskStore(self.root)
        original = self.store._update
        first = True
        def concurrent_writer(*call_args, **call_kwargs):
            nonlocal first
            if first:
                first = False
                other.record_completion_event(value['task_id'], **args)
                raise TaskStoreConflict()
            return original(*call_args, **call_kwargs)
        with patch.object(self.store, '_update', side_effect=concurrent_writer):
            result = self.store.record_completion_event(value['task_id'], **args)
        self.assertFalse(result['created'])
        self.assertEqual(len(self.store.get(value['task_id'])['completion_events']), 1)

    def test_completion_identity_and_legacy_schema_fail_closed(self):
        legacy = record()
        self.store.create(legacy)
        with self.assertRaises(LLMToolError):
            self.store.record_completion_event(legacy['task_id'],
                **self._completion_event(legacy))
        value = self._active_pm_record()
        self.store.create(value)
        args = self._completion_event(value)
        with self.assertRaises(LLMToolError) as mismatch:
            self.store.record_completion_event(value['task_id'],
                **{**args, 'run_id': new_id()})
        self.assertEqual(mismatch.exception.task_store_error_code,
                         'COMPLETION_EVENT_BINDING_MISMATCH')
        with self.assertRaises(LLMToolError):
            self.store.record_completion_event(value['task_id'],
                **{**args, 'session_id': '../other'})


if __name__ == '__main__':
    unittest.main()
