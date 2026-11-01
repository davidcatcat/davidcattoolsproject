import dataclasses
import importlib.util
import io
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import task_completion
from llmtool import task_submission_cli
from llmtool.task_store import TaskStore, new_id
from llmtool.task_submission_cli import (
    run_task_codex_delegate, run_task_direct_result_event,
)


class CodexDelegateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='task-codex-delegate-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / 'repo'
        self.workspace_root = self.base / 'workspaces'
        self.workspace_root.mkdir()
        self.root = self.workspace_root / 'executor-task-1'
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        self.store = TaskStore(self.base / 'tasks')
        self._registered_worktree()

    def _git(self, *args, cwd=None):
        return subprocess.run(['/usr/bin/git', *args], cwd=cwd or self.root,
            check=True, capture_output=True, text=True).stdout.strip()

    @staticmethod
    def _completion_git(root, args, *, limit=1024 * 1024, allow_exit_1=False,
                        return_exit_code=False):
        # Apple Git calls confstr for its temp path; retain the normal Git
        # reader's fixed environment while supplying this test process's valid
        # temporary directory so stderr remains a meaningful failure signal.
        env = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'HOME': os.environ['HOME'],
            'TMPDIR': os.environ.get('TMPDIR') or tempfile.gettempdir(),
            'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_ATTR_NOSYSTEM': '1',
            'GIT_OPTIONAL_LOCKS': '0', 'GIT_NO_LAZY_FETCH': '1',
            'GIT_TERMINAL_PROMPT': '0', 'GIT_PAGER': 'cat', 'PAGER': 'cat',
            'GIT_LITERAL_PATHSPECS': '1'}
        safe = ('-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
            '-c', 'core.untrackedCache=false', '-c', 'core.attributesFile=/dev/null',
            '-c', 'core.pager=cat', '-c', 'color.ui=false', '-c', 'protocol.allow=never')
        process = subprocess.run(['/usr/bin/git', '--no-pager', *safe, *args],
            cwd=root, env=env, stdin=subprocess.DEVNULL, capture_output=True, check=False)
        allowed_codes = (0, 1) if allow_exit_1 else (0,)
        if process.returncode not in allowed_codes or process.stderr or len(process.stdout) > limit:
            raise AssertionError('fixture Git read failed')
        return ((process.stdout, process.returncode) if return_exit_code
                else process.stdout)

    def _registered_worktree(self):
        self.repo.mkdir()
        self._git('init', '-b', 'main', cwd=self.repo)
        self._git('config', 'user.name', 'Fixture', cwd=self.repo)
        self._git('config', 'user.email', 'fixture@example.invalid', cwd=self.repo)
        (self.repo / 'src.py').write_text('VALUE = 1\n')
        (self.repo / '.gitignore').write_text('*.pyc\n__pycache__/\n')
        llmtool = self.repo / 'tools' / 'llmtool'
        llmtool.mkdir(parents=True)
        (llmtool / '.gitignore').write_text('*.pyc\n/.venv/\n/.d6-bootstrap-report.json\n')
        report = {'result': 'PASS', 'task_id': 'executor-task-1',
            'root_realpath': str(self.root), 'head_sha': None,
            'tooling_sha': None, 'cache_fingerprint': 'cpython3.13-darwin-arm64-012345abcdef'}
        report_path = llmtool / '.d6-bootstrap-report.json'
        report_path.write_text('{}\n')
        self._git('add', '.gitignore', 'src.py', 'tools/llmtool/.gitignore', cwd=self.repo)
        self._git('commit', '-m', 'fixture base', cwd=self.repo)
        self.source_sha = self._git('rev-parse', 'HEAD', cwd=self.repo)
        self._git('worktree', 'add', '-b', 'feat/codex-delegate', str(self.root),
                  self.source_sha, cwd=self.repo)
        os.chmod(self.root, 0o700)
        # Add the owner-only Env2 report after worktree creation; the bootstrap
        # callback is stubbed, while the registration reader validates its hash.
        report.update(head_sha=self.source_sha, tooling_sha=self.source_sha)
        report_bytes = (json.dumps(report, sort_keys=True, separators=(',', ':')) + '\n').encode()
        report_path = self.root / 'tools' / 'llmtool' / '.d6-bootstrap-report.json'
        report_path.write_bytes(report_bytes)
        report_path.chmod(0o600)
        common_dir = str((self.repo / '.git').resolve())
        registry_dir = Path(common_dir) / 'llmtool-worktrees'
        registry_dir.mkdir(mode=0o700)
        (registry_dir / '.lifecycle.lock').touch(mode=0o600)
        root_stat = self.root.stat()
        registry = {'schema_version': 1, 'task_id': 'executor-task-1',
            'trusted_agent_id': 'executor-agent', 'session_ref': 'executor-session',
            'root_realpath': str(self.root),
            'physical_identity': {'device': root_stat.st_dev, 'inode': root_stat.st_ino,
                                  'uid': root_stat.st_uid, 'mode': '0o700'},
            'git_common_dir': common_dir, 'branch': 'feat/codex-delegate',
            'base_sha': self.source_sha, 'head_sha': self.source_sha,
            'state': 'ACTIVE', 'lock_state': 'LOCKED', 'native_session_id': 'executor-native',
            'env2_approved_tooling_sha': self.source_sha,
            'env2_pass_marker': {'schema_version': 1, 'result': 'PASS',
                'task_id': 'executor-task-1', 'root_realpath': str(self.root),
                'submitted_head_sha': self.source_sha, 'current_head_sha': self.source_sha,
                'environment_fingerprint': report['cache_fingerprint'],
                'tooling_sha': self.source_sha,
                'report_sha256': hashlib.sha256(report_bytes).hexdigest()}}
        record = registry_dir / 'executor-task-1.json'
        record.write_text(json.dumps(registry), encoding='utf-8')
        record.chmod(0o600)
        self.common_dir = common_dir

    def _task(self):
        request = 'Codex direct fixture task'
        response = json.dumps({'schema_version': 1, 'summary': 'PM bounded task.',
            'developer_assignment': {'route': 'codex', 'risk': 'LOW',
                'goal': 'Edit src.py.', 'acceptance': ['src.py is changed']}},
            separators=(',', ':'))
        return task_submission_cli.run_task_record_pm(
            Namespace(pm_session_id=None, pm_runtime=None, pm_cwd=None),
            store=self.store,
            input_stream=io.StringIO(json.dumps(
                {'request': request, 'response': response}, separators=(',', ':'))),
            return_record=True)

    def _fake_codex(self, *, ignored=False, bytecode=False, overwrite_ignored=None,
                    empty_diff=False, large_diff=False):
        final = {'status': 'DONE', 'summary': 'updated', 'tests': [],
                 'self_review': 'checked', 'blocked_reason': None}
        events = '\n'.join((
            json.dumps({'type': 'thread.started',
                        'thread_id': '12345678-1234-4234-8234-123456789abc'}),
            json.dumps({'type': 'item.completed', 'item': {
                'type': 'agent_message', 'text': json.dumps(final)}}),
            json.dumps({'type': 'turn.completed', 'usage': {
                'input_tokens': 2, 'output_tokens': 3}}), ''))
        script = self.bin / 'codex'
        source_path = str(self.root / 'src.py')
        script_body = '#!' + os.sys.executable + '\nfrom pathlib import Path\nimport os,sys\n'
        if large_diff:
            script_body += 'Path(' + repr(source_path) + ").write_text('VALUE = 2\\n' + ('x' * 70000))\n"
        elif not empty_diff:
            script_body += 'Path(' + repr(source_path) + ").write_text('VALUE = 2\\n')\n"
        if ignored:
            ignored_path = (str(ignored) if isinstance(ignored, (str, Path)) else
                            str(self.root / 'tools' / 'llmtool' / 'unittest.pyc'))
            script_body += 'Path(' + repr(ignored_path) + ").write_bytes(b'forged-bytecode')\n"
        if overwrite_ignored is not None:
            script_body += (
                'import time\n'
                'ignored_path = Path(' + repr(str(overwrite_ignored)) + ')\n'
                'before = ignored_path.stat()\n'
                'time.sleep(0.02)\n'
                "ignored_path.write_bytes(b'forged module path!!')\n"
                'os.utime(ignored_path, ns=(before.st_atime_ns, before.st_mtime_ns))\n')
        if bytecode:
            probe = "import src"
            script_body += (
                'import subprocess\n'
                'env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}\n'
                'if any(x == \'shell_environment_policy.set.PYTHONDONTWRITEBYTECODE="1"\' for x in sys.argv):\n'
                ' env["PYTHONDONTWRITEBYTECODE"] = "1"\n'
                'subprocess.run([sys.executable, "-c", ' + repr(probe) + '], cwd=' +
                repr(source_path.rsplit('/', 1)[0]) + ', env=env, check=True)\n')
        script_body += 'sys.stdout.write(' + repr(events) + ')\nsys.stdout.flush()\n'
        script.write_text(script_body, encoding='utf-8')
        script.chmod(0o700)
        return script

    def _dispatch(self, task, *, ignored=False, bytecode=False, bootstrap_error=None,
                  overwrite_ignored=None, malformed_diff=None, callback_handler=None,
                  registration=None, bound_registration=None, codex_error=None,
                  codex_result=None, codex_timeout=600, run_observer=None,
                  empty_diff=False, large_diff=False):
        self._fake_codex(ignored=ignored, bytecode=bytecode,
                         overwrite_ignored=overwrite_ignored,
                         empty_diff=empty_diff, large_diff=large_diff)
        args = Namespace(task_id=task['task_id'], selection_source='PM',
            acceptance=['src.py is changed'], executor_task='executor-task-1',
            read_path=['src.py'], write_path=['src.py'],
            egress_basis='approved bounded fixture')
        snapshot = (SimpleNamespace(root=registration.root,
            common_dir=registration.common_dir, branch=registration.branch,
            head=getattr(registration, 'result_sha', registration.source_sha))
            if registration else None)
        original_cwd = os.getcwd()
        try:
            os.chdir(self.root)
            git_reader = self._completion_git
            def read_git(root, args, *, limit=1024 * 1024, allow_exit_1=False,
                         return_exit_code=False):
                if (malformed_diff is not None and tuple(args[:2]) ==
                        ('diff', '--binary')):
                    return malformed_diff.encode('utf-8')
                return git_reader(root, args, limit=limit,
                                  allow_exit_1=allow_exit_1,
                                  return_exit_code=return_exit_code)
            run_patch = (
                patch('llmtool.task_submission_cli.codex_direct_developer.run',
                      side_effect=codex_error) if codex_error is not None else
                patch('llmtool.task_submission_cli.codex_direct_developer.run',
                      return_value=codex_result) if codex_result is not None else
                patch('llmtool.task_submission_cli.codex_direct_developer.run',
                      wraps=task_submission_cli.codex_direct_developer.run))
            with patch.dict(os.environ, {'PATH': str(self.bin) + os.pathsep + os.environ['PATH']}), \
                    patch('llmtool.task_submission_cli._codex_developer_profile',
                          return_value=({'model': 'gpt-6-luna'}, 'high')), \
                    patch('llmtool.task_submission_cli.codex_direct_timeout',
                          return_value=codex_timeout), \
                    patch('llmtool.task_submission_cli._bootstrap_registered_d6',
                          side_effect=bootstrap_error), \
                    (patch.object(task_completion, 'bind_executor_task',
                          return_value=(registration, snapshot)) if registration else
                     patch.object(task_completion, 'bind_executor_task',
                          wraps=task_completion.bind_executor_task)), \
                    (patch.object(task_completion, 'read_d6_registration',
                          side_effect=[registration, bound_registration])
                     if registration and bound_registration else
                     patch.object(task_completion, 'read_d6_registration',
                          wraps=task_completion.read_d6_registration)), \
                    patch('llmtool.codex_direct_developer.worker_dispatch.authorize_dispatch'), \
                    patch.object(task_completion, '_git', side_effect=read_git), \
                    (patch('llmtool.task_submission_cli._refresh_d6_binding_after_bootstrap',
                          return_value=registration) if registration else
                     patch('llmtool.task_submission_cli._refresh_d6_binding_after_bootstrap',
                          wraps=task_submission_cli._refresh_d6_binding_after_bootstrap)), \
                    (patch('llmtool.task_submission_cli._bind_codex_native_session',
                          return_value='BOUND') if registration else
                     patch('llmtool.task_submission_cli._bind_codex_native_session',
                          wraps=task_submission_cli._bind_codex_native_session)), \
                    run_patch as codex_run, \
                    patch('llmtool.task_submission_cli.run_task_direct_result_event',
                          side_effect=callback_handler, return_value=0) as callback:
                result = run_task_codex_delegate(args, store=self.store)
                if run_observer is not None:
                    run_observer(codex_run.call_args)
        finally:
            os.chdir(original_cwd)
        return result, self.store.get(task['task_id']), callback

    def test_configured_timeout_matches_packet_and_runner(self):
        task = self._task()
        calls = []
        result, _saved, _callback = self._dispatch(
            task, codex_timeout=900, run_observer=calls.append)
        self.assertEqual(result, 0)
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(call.kwargs['timeout'], 900)
        self.assertIn('"wall_clock_seconds":900', call.kwargs['prompt'])

    def test_blocked_result_preserves_typed_reason(self):
        task = self._task()
        result = task_submission_cli.codex_direct_developer.DirectResult(
            status='BLOCKED', summary='blocked safely', tests=(),
            self_review='checked', blocked_reason='cannot safely edit',
            exit_code=0, thread_id='12345678-1234-4234-8234-123456789abc',
            stdout_sha256='a' * 64, elapsed_ms=1, cleanup_status='CONFIRMED',
            usage=None)
        with redirect_stdout(io.StringIO()):
            _result, saved, callback = self._dispatch(
                task, codex_result=result, empty_diff=True)
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(saved['developer']['error_code'], 'CODEX_BLOCKED')
        self.assertEqual(saved['developer']['blocked_reason'], 'cannot safely edit')
        self.assertEqual(saved['developer']['execution']['status'], 'BLOCKED')
        callback.assert_not_called()

    def test_done_result_persists_bounded_developer_report_separately(self):
        task = self._task()
        result = task_submission_cli.codex_direct_developer.DirectResult(
            status='DONE', summary='updated it', tests=('checked one', 'checked two'),
            self_review='reviewed the diff', blocked_reason=None, exit_code=0,
            thread_id='12345678-1234-4234-8234-123456789abc', stdout_sha256='a' * 64,
            elapsed_ms=1, cleanup_status='CONFIRMED', usage=None)
        (self.root / 'src.py').write_text('VALUE = 2\n')
        with redirect_stdout(io.StringIO()):
            _result, saved, callback = self._dispatch(task, codex_result=result)
        self.assertEqual(saved['developer']['reported'], {
            'summary': 'updated it', 'tests': ['checked one', 'checked two'],
            'self_review': 'reviewed the diff'})
        callback.assert_called_once()

    def test_oversized_and_unscreenable_report_fields_are_dropped(self):
        task = self._task()
        result = task_submission_cli.codex_direct_developer.DirectResult(
            status='BLOCKED', summary='x' * (8 * 1024 + 1),
            tests=('safe test', '_'.join(('api', 'key')) + '=' + 'secret'),
            self_review='credential AKIA' + 'A' * 16, blocked_reason=None, exit_code=0,
            thread_id='12345678-1234-4234-8234-123456789abc', stdout_sha256='a' * 64,
            elapsed_ms=1, cleanup_status='CONFIRMED', usage=None)
        with redirect_stdout(io.StringIO()):
            _result, saved, callback = self._dispatch(task, codex_result=result)
        self.assertNotIn('reported', saved['developer'])
        callback.assert_not_called()

    def test_empty_diff_captures_final_message_with_sensitive_screen_and_bound(self):
        task = self._task()
        credential_like = 'AKIA' + 'A' * 16
        message = 'No files changed. ' + credential_like + ' ' + ('x' * 70000)
        result = task_submission_cli.codex_direct_developer.DirectResult(
            status='DONE', summary='no changes', tests=(), self_review='checked',
            blocked_reason=None, exit_code=0,
            thread_id='12345678-1234-4234-8234-123456789abc', stdout_sha256='a' * 64,
            elapsed_ms=1, cleanup_status='CONFIRMED', usage=None,
            final_message=message)
        with redirect_stdout(io.StringIO()):
            _result, saved, callback = self._dispatch(
                task, codex_result=result, empty_diff=True)
        developer = saved['developer']
        self.assertEqual(developer['state'], 'DEVELOPER_OUTPUT_REJECTED')
        self.assertEqual(developer['error_code'], 'DEVELOPER_OUTPUT_INVALID_ENVELOPE')
        capture = developer['rejected_output_capture']
        captured = (self.store.root / capture['ref']).read_text(encoding='utf-8')
        self.assertIn('No files changed.', captured)
        self.assertIn('[REDACTED]', captured)
        self.assertNotIn(credential_like, captured)
        self.assertLessEqual(len(captured.encode('utf-8')), task_submission_cli.RESPONSE_LIMIT)
        callback.assert_not_called()

    def test_oversized_cumulative_diff_keeps_too_large_rejection(self):
        task = self._task()
        with redirect_stdout(io.StringIO()):
            _result, saved, callback = self._dispatch(task, large_diff=True)
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_OUTPUT_REJECTED')
        self.assertEqual(saved['developer']['error_code'], 'DEVELOPER_OUTPUT_TOO_LARGE')
        callback.assert_not_called()

    def test_capture_write_failure_keeps_typed_rejection(self):
        developer = {'assignment_id': new_id(), 'state': 'DEVELOPER_DISPATCHING'}

        class RecordingStore:
            def update(self, task_id, *, developer):
                self.saved = dict(developer)
                return self.saved

        store = RecordingStore()
        with patch.object(task_submission_cli, '_capture_rejected_output',
                          side_effect=OSError('capture unavailable')):
            saved = task_submission_cli._persist_developer_output_rejection(
                store, 'task-id', developer, 'DEVELOPER_OUTPUT_INVALID_DIFF',
                None, 'bad diff')
        self.assertEqual(saved['state'], 'DEVELOPER_OUTPUT_REJECTED')
        self.assertEqual(saved['error_code'], 'DEVELOPER_OUTPUT_INVALID_DIFF')
        self.assertNotIn('rejected_output_capture', saved)

        class CaptureRejectingStore:
            def __init__(self):
                self.calls = []

            def update(self, task_id, *, developer):
                self.calls.append(dict(developer))
                if 'rejected_output_capture' in developer:
                    raise OSError('capture record unavailable')
                return dict(developer)

        store = CaptureRejectingStore()
        with patch.object(task_submission_cli, '_capture_rejected_output',
                          return_value={'ref': 'rejected-developer-output/a.txt',
                                        'sha256': 'a' * 64}):
            saved = task_submission_cli._persist_developer_output_rejection(
                store, 'task-id', dict(developer), 'DEVELOPER_OUTPUT_INVALID_DIFF',
                None, 'bad diff')
        self.assertEqual(len(store.calls), 2)
        self.assertIn('rejected_output_capture', store.calls[0])
        self.assertNotIn('rejected_output_capture', saved)
        self.assertEqual(saved['state'], 'DEVELOPER_OUTPUT_REJECTED')

    def test_timeout_with_changes_is_persisted_by_dispatch_task_store(self):
        task = self._task()

        def timeout(**_kwargs):
            (self.root / 'src.py').write_text('VALUE = 2\n')
            raise task_submission_cli.codex_direct_developer.DirectCodexError(
                'CODEX_TIMEOUT', attempted=True, cleanup_status='CONFIRMED')

        with redirect_stdout(io.StringIO()):
            result, saved, callback = self._dispatch(task, codex_error=timeout)
        developer = saved['developer']
        self.assertEqual(result, 1)
        self.assertEqual(developer['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(developer['error_code'], 'CODEX_TIMEOUT_WITH_CHANGES')
        self.assertEqual(developer['executor_task_id'], 'executor-task-1')
        self.assertEqual(developer['execution']['diff_base_sha'], self.source_sha)
        self.assertRegex(developer['execution']['diff_sha256'], r'^[0-9a-f]{64}$')
        callback.assert_not_called()

    def test_same_d6_followup_dispatch_accepts_committed_timeout_diff(self):
        task = self._task()

        # The dispatch starts from an advanced D6 HEAD while the registry base
        # remains at the original Task worktree source.
        (self.root / 'src.py').write_text('VALUE = 1\n# approved branch progress\n')
        self._git('add', 'src.py')
        self._git('commit', '-m', 'approved branch progress')
        dispatch_source = self._git('rev-parse', 'HEAD')
        registry_path = (Path(self.common_dir) / 'llmtool-worktrees' /
                         'executor-task-1.json')
        registry = json.loads(registry_path.read_text(encoding='utf-8'))
        report_path = (self.root / 'tools' / 'llmtool' /
                       '.d6-bootstrap-report.json')
        report = json.loads(report_path.read_text(encoding='utf-8'))
        report.update(head_sha=dispatch_source, tooling_sha=self.source_sha)
        report_bytes = (json.dumps(report, sort_keys=True,
            separators=(',', ':')) + '\n').encode('utf-8')
        report_path.write_bytes(report_bytes)
        report_path.chmod(0o600)
        registry['head_sha'] = dispatch_source
        registry['env2_approved_tooling_sha'] = None
        registry['env2_pass_marker'].update(
            submitted_head_sha=dispatch_source, current_head_sha=dispatch_source,
            tooling_sha=self.source_sha,
            report_sha256=hashlib.sha256(report_bytes).hexdigest())
        registry_path.write_text(json.dumps(registry), encoding='utf-8')
        registry_path.chmod(0o600)

        def timeout(**_kwargs):
            (self.root / 'src.py').write_text('VALUE = 2\n')
            raise task_submission_cli.codex_direct_developer.DirectCodexError(
                'CODEX_TIMEOUT', attempted=True, cleanup_status='CONFIRMED')

        with redirect_stdout(io.StringIO()):
            _result, _saved, _callback = self._dispatch(task, codex_error=timeout)
        self._git('add', 'src.py')
        self._git('commit', '-m', 'record timeout result')
        result_head = self._git('rev-parse', 'HEAD')
        lifecycle_path = (Path(__file__).resolve().parents[1] / 'scripts' /
                          'd6_worktree_lifecycle.py')
        spec = importlib.util.spec_from_file_location('d6_worktree_lifecycle_fixture',
                                                       lifecycle_path)
        lifecycle = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = lifecycle
        spec.loader.exec_module(lifecycle)
        registry = json.loads(registry_path.read_text(encoding='utf-8'))

        def bootstrap_result(root, task_id):
            report_path = Path(root) / 'tools' / 'llmtool' / '.d6-bootstrap-report.json'
            report = json.loads(report_path.read_text(encoding='utf-8'))
            # The result head advances for this continuation, while the
            # ENV-2 tooling remains bound to the registered base source.
            report.update(head_sha=result_head, tooling_sha=self.source_sha)
            report_bytes = (json.dumps(report, sort_keys=True,
                separators=(',', ':')) + '\n').encode('utf-8')
            report_path.write_bytes(report_bytes)
            report_path.chmod(0o600)
            current = json.loads(registry_path.read_text(encoding='utf-8'))
            current['env2_pass_marker'].update(
                submitted_head_sha=result_head, current_head_sha=result_head,
                tooling_sha=self.source_sha,
                report_sha256=hashlib.sha256(report_bytes).hexdigest())
            registry_path.write_text(json.dumps(current), encoding='utf-8')
            registry_path.chmod(0o600)

        with patch.dict(os.environ, {'LLMTOOL_D6_TESTING': '1'}), \
                patch.object(lifecycle, '_bootstrap', side_effect=bootstrap_result):
            recorded_head = lifecycle.record_result(task_id='executor-task-1',
                canonical_root=self.repo, root=self.root,
                workspace_root=self.workspace_root)
        self.assertEqual(recorded_head, result_head)
        registration, snapshot = task_completion.bind_executor_task(
            'executor-task-1', expected_common_dir=self.common_dir, root=self.root)
        self.assertNotEqual(registration.source_sha, dispatch_source)
        self.assertEqual(snapshot.head, result_head)
        bound_registration = dataclasses.replace(registration,
            native_session_id='12345678-1234-4234-8234-123456789abc')
        other_d6 = SimpleNamespace(**{
            **vars(registration), 'task_id': 'other-d6'})
        with self.assertRaises(task_completion.LLMToolError):
            self._dispatch(task, registration=other_d6,
                bound_registration=SimpleNamespace(**{
                    **vars(other_d6), 'native_session_id': '12345678-1234-4234-8234-123456789abc'}))
        callback_args = []
        def callback_handler(task_id, executor_task_id, *, store):
            callback_args.append((task_id, executor_task_id, store))
            return 0

        with redirect_stdout(io.StringIO()):
            result, saved, callback = self._dispatch(task, registration=registration,
                bound_registration=bound_registration,
                callback_handler=callback_handler)
        self.assertEqual(result, 0)
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_RESPONDED')
        self.assertIn('VALUE = 2', saved['developer']['artifact'])
        self.assertEqual(len(callback_args), 1)
        registry_path = (Path(self.common_dir) / 'llmtool-worktrees' /
                         'executor-task-1.json')
        registry = json.loads(registry_path.read_text(encoding='utf-8'))
        registry['native_session_id'] = bound_registration.native_session_id
        registry_path.write_text(json.dumps(registry), encoding='utf-8')
        registry_path.chmod(0o600)

        def bounded_test_result(*, root, source_sha, diff_sha256, **_kwargs):
            return [{'path': 'src.py', 'status': 'PASS', 'exit_code': 0,
                'source_sha': source_sha, 'result_head': None,
                'diff_sha256': diff_sha256, 'output_sha256': 'b' * 64}]

        task_id, executor_task_id, callback_store = callback_args[0]
        with patch.object(task_completion, 'run_bounded_worktree_tests',
                          side_effect=bounded_test_result), \
             patch.object(task_submission_cli, '_active_pm_runtime', return_value=True), \
             patch.object(task_submission_cli, '_record_ready_event'), \
             redirect_stdout(io.StringIO()):
            callback_result = run_task_direct_result_event(
                task_id, executor_task_id, store=callback_store)
        saved = self.store.get(task['task_id'])
        direct_callback = saved['developer']['direct_result_callback']
        self.assertEqual(callback_result, 0)
        self.assertEqual(direct_callback['state'], 'PM_EVIDENCE_READY',
                         direct_callback['missing_gates'])
        self.assertEqual(direct_callback['missing_gates'], [])
        self.assertEqual(direct_callback['evidence']['source_sha'], dispatch_source)
        self.assertEqual(direct_callback['evidence']['result_head'], result_head)
        self.assertEqual(direct_callback['evidence']['result']['source_sha'], dispatch_source)
        self.assertEqual(direct_callback['evidence']['result']['changed_paths'], ['src.py'])
        self.assertNotIn('D6_TASK_BINDING_MISMATCH', direct_callback['missing_gates'])
        callback.assert_called_once()

    def test_timeout_recovery_dispatch_rejects_committed_out_of_scope_diff(self):
        task = self._task()

        def timeout(**_kwargs):
            (self.root / 'private.py').write_text('VALUE = 2\n')
            raise task_submission_cli.codex_direct_developer.DirectCodexError(
                'CODEX_TIMEOUT', attempted=True, cleanup_status='CONFIRMED')

        with redirect_stdout(io.StringIO()):
            _result, _saved, _callback = self._dispatch(task, codex_error=timeout)
        self._git('add', 'private.py')
        self._git('commit', '-m', 'record out of scope timeout result')
        result_head = self._git('rev-parse', 'HEAD')
        registration = task_completion.D6Registration(
            task_id='executor-task-1', trusted_agent_id='executor-agent',
            session_ref='executor-session', root=str(self.root),
            common_dir=self.common_dir, branch='feat/codex-delegate',
            source_sha=self.source_sha, result_sha=result_head, state='ACTIVE',
            lock_state='LOCKED', device=0, inode=0, uid=os.getuid(),
            native_session_id=None, github_login=None, env2_pass_marker=None,
            env2_approved_tooling_sha=None)
        bound_registration = dataclasses.replace(registration,
            native_session_id='12345678-1234-4234-8234-123456789abc')
        with redirect_stdout(io.StringIO()):
            result, saved, callback = self._dispatch(task, registration=registration,
                bound_registration=bound_registration)
        self.assertEqual(result, 1)
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_OUTPUT_REJECTED')
        self.assertEqual(saved['developer']['error_code'], 'DEVELOPER_OUTPUT_OUT_OF_SCOPE')
        callback.assert_not_called()

    def test_timeout_recovery_requires_recorded_same_d6_result_head(self):
        previous = {
            'state': 'DEVELOPER_OUTCOME_UNKNOWN',
            'error_code': 'CODEX_TIMEOUT_WITH_CHANGES',
            'execution': {'status': 'TIMEOUT', 'diff_base_sha': self.source_sha,
                          'diff_sha256': 'a' * 64},
            'source_sha': self.source_sha, 'executor_task_id': 'executor-task-1',
            'read_paths': ['src.py'], 'write_paths': ['src.py'],
            'acceptance': ['src.py is changed'],
        }
        updated_head = 'b' * 40
        args = dict(executor_task_id='executor-task-1', source_sha=self.source_sha,
            result_head=updated_head, registration_source_sha=self.source_sha,
            registration_result_sha=updated_head, read_paths=['src.py'],
            write_paths=['src.py'], acceptance=['src.py is changed'])
        self.assertTrue(task_submission_cli._codex_timeout_recovery(previous, **args))
        self.assertFalse(task_submission_cli._codex_timeout_recovery(
            previous, **{**args, 'executor_task_id': 'other-d6'}))
        self.assertFalse(task_submission_cli._codex_timeout_recovery(
            previous, **{**args, 'registration_result_sha': self.source_sha}))
        self.assertTrue(task_submission_cli._codex_timeout_recovery(
            previous, **{**args, 'registration_source_sha': 'c' * 40}))
        self.assertFalse(task_submission_cli._codex_timeout_recovery(
            previous, **{**args, 'write_paths': ['other.py']}))
        self.assertFalse(task_submission_cli._codex_timeout_recovery(
            previous, **{**args, 'acceptance': ['other criterion']}))

    def test_timeout_recovery_rejects_sibling_of_timed_out_source_before_launch(self):
        task = self._task()

        # The timed-out attempt starts at A, which descends from the registry
        # base. Move the D6 back to the base and create sibling B, so the
        # registry base is an ancestor of B but A is not.
        (self.root / 'src.py').write_text('VALUE = 1  # attempt A\n')
        self._git('add', 'src.py')
        self._git('commit', '-m', 'attempt A source')
        attempt_source = self._git('rev-parse', 'HEAD')
        registry = task_completion.D6Registration(
            task_id='executor-task-1', trusted_agent_id='executor-agent',
            session_ref='executor-session', root=str(self.root),
            common_dir=self.common_dir, branch='feat/codex-delegate',
            source_sha=self.source_sha, result_sha=attempt_source, state='ACTIVE',
            lock_state='LOCKED', device=0, inode=0, uid=os.getuid(),
            native_session_id=None, github_login=None, env2_pass_marker=None,
            env2_approved_tooling_sha=None)

        def timeout(**_kwargs):
            (self.root / 'src.py').write_text('VALUE = 2\n')
            raise task_submission_cli.codex_direct_developer.DirectCodexError(
                'CODEX_TIMEOUT', attempted=True, cleanup_status='CONFIRMED')

        with redirect_stdout(io.StringIO()):
            _result, saved, _callback = self._dispatch(
                task, registration=registry, codex_error=timeout)
        self.assertEqual(saved['developer']['error_code'],
                         'CODEX_TIMEOUT_WITH_CHANGES')

        self._git('reset', '--hard', self.source_sha)
        (self.root / 'src.py').write_text('VALUE = 3  # sibling B\n')
        self._git('add', 'src.py')
        self._git('commit', '-m', 'sibling B result')
        sibling_head = self._git('rev-parse', 'HEAD')
        sibling_registration = dataclasses.replace(
            registry, result_sha=sibling_head)
        bound_registration = dataclasses.replace(sibling_registration,
            native_session_id='12345678-1234-4234-8234-123456789abc')
        with patch('llmtool.task_submission_cli.codex_direct_developer.run') as launch:
            with self.assertRaises(task_completion.LLMToolError) as caught:
                self._dispatch(task, registration=sibling_registration,
                    bound_registration=bound_registration)
        self.assertEqual(caught.exception.completion_code,
                         'CODEX_TIMEOUT_SOURCE_NOT_ANCESTOR')
        launch.assert_not_called()

    def test_normal_worktree_snapshot_rejects_head_different_from_source(self):
        with patch.object(task_completion, 'collect_git_snapshot',
                          return_value=SimpleNamespace(head='b' * 40)):
            with self.assertRaises(task_completion.LLMToolError) as caught:
                task_completion._worktree_snapshot(
                    root=self.root, source_sha=self.source_sha,
                    read_paths=['src.py'], write_paths=['src.py'])
        self.assertEqual(caught.exception.completion_code,
                         'WORKTREE_SOURCE_HEAD_MISMATCH')

    def test_direct_codex_rejects_malformed_hunk_strictly_and_captures_it(self):
        task = self._task()
        raw = ('diff --git a/src.py b/src.py\n'
               '--- a/src.py\n+++ b/src.py\n'
               '@@ -1,1 +1,2 @@\n\n+VALUE = 2\n')
        output = io.StringIO()
        with redirect_stdout(output):
            result, saved, _callback = self._dispatch(task, malformed_diff=raw)
        self.assertEqual(result, 1)
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_OUTPUT_REJECTED')
        self.assertEqual(saved['developer']['error_code'], 'DEVELOPER_OUTPUT_INVALID_DIFF')
        self.assertIsNone(saved['developer']['artifact'])
        capture = saved['developer']['rejected_output_capture']
        captured = (self.store.root / capture['ref']).read_text(encoding='utf-8')
        self.assertEqual(captured, raw)
        self.assertNotIn(raw, output.getvalue())

    def test_invalid_final_with_scoped_changes_is_recoverable(self):
        task = self._task()

        def invalid_final(**_kwargs):
            (self.root / 'src.py').write_text('VALUE = 2\n')
            raise task_submission_cli.codex_direct_developer.DirectCodexError(
                'CODEX_FINAL_INVALID_WITH_CHANGES', attempted=True,
                process_exit_code=0, stdout_sha256='a' * 64,
                native_session_id='12345678-1234-4234-8234-123456789abc',
                cleanup_status='CONFIRMED', final_message='{malformed final}')

        with redirect_stdout(io.StringIO()):
            result, saved, callback = self._dispatch(task, codex_error=invalid_final)
        developer = saved['developer']
        self.assertEqual(result, 0)
        self.assertEqual(developer['state'], 'DEVELOPER_RESPONDED')
        self.assertEqual(developer['result_status'], 'UNVERIFIED')
        self.assertEqual(developer['final_answer'], 'INVALID')
        self.assertIn('VALUE = 2', developer['artifact'])
        capture = developer['rejected_output_capture']
        self.assertEqual((self.store.root / capture['ref']).read_text(encoding='utf-8'),
                         '{malformed final}')
        callback.assert_called_once()

    def test_invalid_final_without_changes_remains_rejected(self):
        task = self._task()

        def invalid_final(**_kwargs):
            raise task_submission_cli.codex_direct_developer.DirectCodexError(
                'CODEX_FINAL_JSON_INVALID', attempted=True, process_exit_code=0,
                stdout_sha256='a' * 64, final_message='not JSON')

        with redirect_stdout(io.StringIO()):
            result, saved, callback = self._dispatch(task, codex_error=invalid_final)
        self.assertEqual(result, 1)
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_OUTPUT_REJECTED')
        self.assertEqual(saved['developer']['error_code'],
                         'DEVELOPER_OUTPUT_INVALID_ENVELOPE')
        self.assertEqual(saved['developer']['execution']['status'], 'FAILED')
        self.assertIsNone(saved['developer']['execution']['native_session_id'])
        callback.assert_not_called()

    def test_invalid_final_with_out_of_scope_changes_remains_rejected(self):
        task = self._task()

        def invalid_final(**_kwargs):
            (self.root / 'private.py').write_text('VALUE = 2\n')
            raise task_submission_cli.codex_direct_developer.DirectCodexError(
                'CODEX_FINAL_INVALID_WITH_CHANGES', attempted=True,
                process_exit_code=0, stdout_sha256='a' * 64,
                final_message='not JSON')

        with redirect_stdout(io.StringIO()):
            result, saved, callback = self._dispatch(task, codex_error=invalid_final)
        self.assertEqual(result, 1)
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_OUTPUT_REJECTED')
        self.assertEqual(saved['developer']['error_code'], 'DEVELOPER_OUTPUT_OUT_OF_SCOPE')
        self.assertEqual(saved['developer']['execution']['status'], 'FAILED')
        callback.assert_not_called()

    def test_invalid_final_with_scoped_changes_needs_complete_execution_evidence(self):
        task = self._task()

        def invalid_final(**_kwargs):
            (self.root / 'src.py').write_text('VALUE = 2\n')
            raise task_submission_cli.codex_direct_developer.DirectCodexError(
                'CODEX_FINAL_INVALID_WITH_CHANGES', attempted=True,
                process_exit_code=0, stdout_sha256='a' * 64,
                native_session_id='12345678-1234-4234-8234-123456789abc',
                cleanup_status='UNKNOWN', final_message='{malformed final}')

        with redirect_stdout(io.StringIO()):
            result, saved, callback = self._dispatch(task, codex_error=invalid_final)
        execution = saved['developer']['execution']
        self.assertEqual(result, 1)
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(execution['status'], 'FAILED')
        self.assertEqual(execution['cleanup_status'], 'UNKNOWN')
        self.assertNotIn('result_status', saved['developer'])
        callback.assert_not_called()

    def test_direct_codex_persists_diff_with_sensitive_removed_line(self):
        task = self._task()
        raw = ('diff --git a/src.py b/src.py\n'
               '--- a/src.py\n+++ b/src.py\n'
               '@@ -1,1 +1,1 @@\n'
               '-' + ('AK' + 'IA' + ('A' * 16)) + '\n'
               '+VALUE = 2\n')
        with redirect_stdout(io.StringIO()):
            result, saved, callback = self._dispatch(task, malformed_diff=raw)
        self.assertEqual(result, 0)
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_RESPONDED')
        self.assertEqual(saved['developer']['artifact'], raw)
        self.assertEqual(saved['developer']['artifact_sha256'],
                         hashlib.sha256(raw.encode('utf-8')).hexdigest())
        callback.assert_called_once()

    def test_dirty_registered_worktree_keeps_identity_and_runs_callback(self):
        task = self._task()
        result, saved, callback = self._dispatch(task)
        self.assertEqual(result, 0)
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_RESPONDED')
        self.assertIn('VALUE = 2', saved['developer']['artifact'])
        self.assertTrue(saved['developer']['execution']['native_session_binding'])
        callback.assert_called_once()

    def test_new_ignored_bytecode_is_typed_output_rejection(self):
        task = self._task()
        result, saved, _callback = self._dispatch(task, ignored=True)
        self.assertEqual(result, 1)
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_OUTPUT_REJECTED')
        self.assertEqual(saved['developer']['error_code'], 'DEVELOPER_OUTPUT_IGNORED_FILES')

    def test_modified_existing_ignored_file_is_typed_output_rejection(self):
        ignored_path = self.root / 'tools' / 'llmtool' / 'existing.pyc'
        ignored_path.write_bytes(b'old ignored contents')
        task = self._task()
        result, saved, _callback = self._dispatch(task, ignored=str(ignored_path))
        self.assertEqual(result, 1)
        self.assertEqual(saved['developer']['error_code'], 'DEVELOPER_OUTPUT_IGNORED_FILES')

    def test_same_size_venv_overwrite_with_restored_mtime_is_rejected(self):
        ignored_path = (self.root / 'tools' / 'llmtool' / '.venv' / 'lib' /
                        'site-packages' / 'forged.pth')
        ignored_path.parent.mkdir(parents=True)
        original = b'baseline module path'
        ignored_path.write_bytes(original)
        baseline = ignored_path.stat()
        task = self._task()
        result, saved, _callback = self._dispatch(
            task, overwrite_ignored=str(ignored_path))
        changed = ignored_path.stat()
        self.assertEqual(ignored_path.stat().st_size, len(original))
        self.assertEqual(changed.st_mtime_ns, baseline.st_mtime_ns)
        self.assertNotEqual(changed.st_ctime_ns, baseline.st_ctime_ns)
        self.assertEqual(result, 1)
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_OUTPUT_REJECTED')
        self.assertEqual(saved['developer']['error_code'], 'DEVELOPER_OUTPUT_IGNORED_FILES')

    def test_child_test_python_does_not_create_ignored_bytecode(self):
        task = self._task()
        result, saved, callback = self._dispatch(task, bytecode=True)
        self.assertEqual(result, 0)
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_RESPONDED')
        callback.assert_called_once()
        self.assertFalse((self.root / '__pycache__').exists())

    def test_bound_codex_session_reaches_pm_evidence_ready(self):
        task = self._task()
        class MemoryStore:
            def __init__(self, record):
                self.value = dict(record)

            def get(self, _task_id):
                return self.value

            def update(self, _task_id, **changes):
                self.value.update(changes)
                return self.value

            def compare_and_update(self, _task_id, expected, **changes):
                if any(self.value.get(key) != value for key, value in expected.items()):
                    raise AssertionError('unexpected Task update')
                self.value.update(changes)
                return self.value

        self.store = MemoryStore(task)
        Registration = dataclasses.make_dataclass('Registration', [
            ('task_id', object), ('trusted_agent_id', object),
            ('session_ref', object), ('native_session_id', object),
            ('root', object), ('common_dir', object), ('branch', object),
            ('source_sha', object), ('result_sha', object), ('github_login', object),
        ])
        registration = Registration(task_id='executor-task-1',
            trusted_agent_id='executor-agent', session_ref='executor-session',
            native_session_id=None, root=str(self.root), common_dir=self.common_dir,
            branch='feat/codex-delegate', source_sha=self.source_sha,
            result_sha=self.source_sha, github_login='owner')
        bound_registration = dataclasses.replace(registration, native_session_id=
            '12345678-1234-4234-8234-123456789abc')
        snapshot = SimpleNamespace(root=str(self.root), common_dir=self.common_dir,
            branch='feat/codex-delegate', head=self.source_sha)
        result = {'task_id': task['task_id'], 'run_id': task['run_id'],
            'executor_task_id': registration.task_id,
            'executor_identity': registration.trusted_agent_id,
            'executor_session_ref': registration.session_ref,
            'executor_native_session_id': bound_registration.native_session_id,
            'executor_native_session': 'VERIFIED', 'root': str(self.root),
            'common_dir': self.common_dir, 'branch': registration.branch,
            'source_sha': self.source_sha, 'result_head': None,
            'result_mode': 'UNCOMMITTED_WORKTREE', 'changed_paths': ['src.py'],
            'diff_sha256': 'e' * 64, 'status_sha256': 'f' * 64,
            'file_manifest_sha256': 'a' * 64}

        def callback_handler(task_id, executor_task_id, *, store):
            return run_task_direct_result_event(task_id, executor_task_id, store=store)

        with patch.object(task_completion, 'collect_executor_worktree_result',
                          return_value=result) as collect, \
             patch.object(task_completion, 'run_bounded_worktree_tests',
                          return_value=[]), \
             patch.object(task_submission_cli, '_recollect_direct_worktree'), \
             patch.object(task_submission_cli, '_active_pm_runtime', return_value=True), \
             patch.object(task_submission_cli, '_record_ready_event'), \
             redirect_stdout(io.StringIO()):
            _dispatch_result, saved, callback = self._dispatch(task,
                callback_handler=callback_handler, registration=registration,
                bound_registration=bound_registration)

        self.assertEqual(saved['developer']['execution']['native_session_binding'], 'BOUND')
        self.assertEqual(saved['developer']['direct_result_callback']['state'],
                         'PM_EVIDENCE_READY',
                         saved['developer']['direct_result_callback'].get('missing_gates'))
        self.assertEqual(saved['developer']['direct_result_callback']['evidence'][
            'executor_native_session_id'], bound_registration.native_session_id)
        self.assertNotIn('D6_TASK_BINDING_MISMATCH',
                         saved['developer']['direct_result_callback']['missing_gates'])
        self.assertEqual(collect.call_args.kwargs['expected_native_session_id'],
                         bound_registration.native_session_id)
        self.assertIsNone(collect.call_args.kwargs['expected_result_head'])
        self.assertIsNone(saved['developer']['direct_result_callback']['evidence'][
            'result_head'])
        callback.assert_called_once()

    def test_bound_codex_session_rejects_other_registration_changes(self):
        task = self._task()
        class MemoryStore:
            def __init__(self, record):
                self.value = dict(record)

            def get(self, _task_id):
                return self.value

            def update(self, _task_id, **changes):
                self.value.update(changes)
                return self.value

            def compare_and_update(self, _task_id, expected, **changes):
                if any(self.value.get(key) != value for key, value in expected.items()):
                    raise AssertionError('unexpected Task update')
                self.value.update(changes)
                return self.value

        self.store = MemoryStore(task)
        Registration = dataclasses.make_dataclass('Registration', [
            ('task_id', object), ('trusted_agent_id', object),
            ('session_ref', object), ('native_session_id', object),
            ('root', object), ('common_dir', object), ('branch', object),
            ('source_sha', object), ('result_sha', object), ('github_login', object),
        ])
        registration = Registration(task_id='executor-task-1',
            trusted_agent_id='executor-agent', session_ref='executor-session',
            native_session_id=None, root=str(self.root), common_dir=self.common_dir,
            branch='feat/codex-delegate', source_sha=self.source_sha,
            result_sha=self.source_sha, github_login='owner')
        bound_registration = dataclasses.replace(registration,
            native_session_id='12345678-1234-4234-8234-123456789abc',
            github_login='changed-owner')

        with redirect_stdout(io.StringIO()):
            result, saved, callback = self._dispatch(task,
                registration=registration, bound_registration=bound_registration)

        self.assertEqual(result, 1)
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(saved['developer']['error_code'],
                         'CODEX_RESULT_EVIDENCE_UNKNOWN')
        self.assertEqual(saved['completion']['evidence']['executor_native_session'],
                         'UNKNOWN')
        self.assertEqual(saved['developer']['direct_result_callback']['evidence'][
            'executor_native_session'], 'UNKNOWN')
        self.assertNotEqual(saved['developer']['direct_result_callback']['evidence'][
            'verification_state'], 'VERIFIED')
        callback.assert_not_called()

    def test_bootstrap_environment_failure_is_unavailable_and_typed(self):
        from llmtool.opencode_direct_developer import DirectOpenCodeError
        task = self._task()
        result, saved, callback = self._dispatch(
            task, bootstrap_error=DirectOpenCodeError('ENV_BOOTSTRAP_UNAVAILABLE'))
        self.assertEqual(result, 1)
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_UNAVAILABLE')
        self.assertEqual(saved['developer']['error_code'], 'ENV_BOOTSTRAP_UNAVAILABLE')
        callback.assert_not_called()

    def _rebootstrap(self, **registry_changes):
        """Act like ENV-2: write a fresh report and rewrite the registry PASS marker."""
        registry_path = Path(self.common_dir) / 'llmtool-worktrees' / 'executor-task-1.json'
        report_path = self.root / 'tools' / 'llmtool' / '.d6-bootstrap-report.json'

        def bootstrap(_registration):
            report = json.loads(report_path.read_text())
            report['elapsed_ms'] = report.get('elapsed_ms', 0) + 1
            report_bytes = (json.dumps(report, sort_keys=True, separators=(',', ':')) +
                            '\n').encode()
            report_path.write_bytes(report_bytes)
            registry = json.loads(registry_path.read_text())
            registry['env2_pass_marker']['report_sha256'] = hashlib.sha256(
                report_bytes).hexdigest()
            registry.update(registry_changes)
            registry_path.write_text(json.dumps(registry))
            registry_path.chmod(0o600)
        return bootstrap

    def test_bootstrap_rewritten_pass_marker_still_launches_codex(self):
        # Regression (#352 G5): ENV-2 rewrites the PASS marker on every
        # bootstrap; the pre-launch binding check must not treat that as an
        # identity change and swallow it as CODEX_ADAPTER_UNKNOWN.
        task = self._task()
        result, saved, callback = self._dispatch(task, bootstrap_error=self._rebootstrap())
        self.assertEqual(result, 0)
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_RESPONDED')
        self.assertEqual(saved['developer']['execution']['status'], 'DONE')
        callback.assert_called_once()

    def test_bootstrap_that_changes_d6_identity_is_typed_before_launch(self):
        task = self._task()
        with patch('llmtool.task_submission_cli.codex_direct_developer.run') as codex_run:
            result, saved, callback = self._dispatch(
                task, bootstrap_error=self._rebootstrap(session_ref='other-session'))
        self.assertEqual(result, 1)
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_UNAVAILABLE')
        self.assertEqual(saved['developer']['error_code'], 'CODEX_D6_BINDING_CHANGED')
        codex_run.assert_not_called()
        callback.assert_not_called()

    def test_bootstrap_without_a_valid_pass_marker_is_typed_before_launch(self):
        task = self._task()
        marker = {'schema_version': 1, 'result': 'FAIL'}
        with patch('llmtool.task_submission_cli.codex_direct_developer.run') as codex_run:
            result, saved, _callback = self._dispatch(
                task, bootstrap_error=self._rebootstrap(env2_pass_marker=marker))
        self.assertEqual(result, 1)
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_UNAVAILABLE')
        self.assertEqual(saved['developer']['error_code'], 'CODEX_ENV2_MARKER_INVALID')
        codex_run.assert_not_called()

    def test_unexpected_route_exception_keeps_a_stable_class_code(self):
        task = self._task()
        with patch('llmtool.task_submission_cli.codex_direct_developer.run',
                   side_effect=RuntimeError('prompt text and /private/path')):
            output = io.StringIO()
            with redirect_stdout(output):
                result, saved, callback = self._dispatch(task)
        self.assertEqual(result, 1)
        developer = saved['developer']
        # Still fail-closed (no resend), but no longer an opaque adapter UNKNOWN.
        self.assertEqual(developer['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(developer['error_code'], 'CODEX_ADAPTER_RUNTIME_ERROR')
        self.assertNotIn('prompt text', json.dumps(saved))
        self.assertNotIn('/private/path', output.getvalue())
        callback.assert_not_called()

    def test_adapter_exception_after_launch_is_recorded_with_facts_and_unknown_status(self):
        task = self._task()
        with patch('llmtool.codex_direct_developer._result_from_events',
                   side_effect=KeyError('prompt text')):
            with redirect_stdout(io.StringIO()):
                result, saved, callback = self._dispatch(task)
        self.assertEqual(result, 1)
        developer = saved['developer']
        self.assertEqual(developer['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(developer['error_code'], 'CODEX_ADAPTER_KEY_ERROR')
        self.assertEqual(developer['execution']['status'], 'UNKNOWN')
        self.assertEqual(developer['execution']['process_exit_code'], 0)
        self.assertEqual(developer['execution']['native_session_id'],
                         '12345678-1234-4234-8234-123456789abc')
        callback.assert_not_called()

    def test_unowned_launched_process_is_unknown_not_failed(self):
        task = self._task()
        with patch('llmtool.codex_direct_developer.CLIProcessLifecycle.own',
                   side_effect=task_completion.LLMToolError('ownership unknown')):
            with redirect_stdout(io.StringIO()):
                result, saved, callback = self._dispatch(task)
        self.assertEqual(result, 1)
        developer = saved['developer']
        self.assertEqual(developer['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(developer['error_code'], 'CODEX_PROCESS_OWNERSHIP_UNKNOWN')
        self.assertEqual(developer['execution']['status'], 'UNKNOWN')
        callback.assert_not_called()

    def test_bootstrap_that_dirties_the_tree_is_a_binding_change(self):
        task = self._task()
        def dirty(_registration):
            (self.root / 'stray.txt').write_text('left by bootstrap\n')
        with patch('llmtool.task_submission_cli.codex_direct_developer.run') as codex_run:
            with redirect_stdout(io.StringIO()):
                result, saved, _callback = self._dispatch(task, bootstrap_error=dirty)
        self.assertEqual(result, 1)
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_UNAVAILABLE')
        self.assertEqual(saved['developer']['error_code'], 'CODEX_D6_BINDING_CHANGED')
        codex_run.assert_not_called()

    def test_misuse_refusals_name_the_failed_condition(self):
        task = self._task()
        base = dict(task_id=task['task_id'], selection_source='PM',
            acceptance=['src.py is changed'], executor_task='executor-task-1',
            read_path=['src.py'], write_path=['src.py'],
            egress_basis='approved bounded fixture')
        cases = (
            ('context', dict(base, context_stdin=True), str(self.root), '--context-stdin'),
            ('acceptance', dict(base, acceptance=['src.py changed']), str(self.root),
             'exactly'),
            ('cwd', base, str(self.repo), 'registered D6 root'),
        )
        for label, values, cwd, needle in cases:
            with self.subTest(label):
                original = os.getcwd()
                os.chdir(cwd)
                try:
                    with self.assertRaises(task_completion.LLMToolError) as refused:
                        run_task_codex_delegate(Namespace(**values), store=self.store)
                finally:
                    os.chdir(original)
                self.assertIn(needle, str(refused.exception))
                self.assertIsNone(self.store.get(task['task_id'])['developer'])
        with self.subTest('pyyaml'):
            from llmtool.task_submission_cli import _codex_developer_profile
            with patch('llmtool.task_submission_cli.yaml_module',
                       side_effect=task_completion.LLMToolError('PyYAML is required')):
                with self.assertRaises(task_completion.LLMToolError) as refused:
                    _codex_developer_profile()
            self.assertIn('PyYAML', str(refused.exception))
            self.assertIn('.venv/bin/python', str(refused.exception))

    def test_ignored_baseline_failure_is_unavailable_before_launch(self):
        task = self._task()
        with patch('llmtool.task_submission_cli._ignored_file_fingerprints',
                   side_effect=OSError('fixture inventory failure')), \
                patch('llmtool.task_submission_cli.codex_direct_developer.run') as codex_run:
            result, saved, callback = self._dispatch(task)
        self.assertEqual(result, 1)
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_UNAVAILABLE')
        self.assertEqual(saved['developer']['error_code'],
                         'CODEX_IGNORED_BASELINE_UNAVAILABLE')
        codex_run.assert_not_called()
        callback.assert_not_called()

    def test_reviewer_native_independence_fails_closed(self):
        from llmtool.task_submission_cli import _reviewer_native_session_independent
        for values in (("executor", "executor", "codex"),
                       ("codex", "executor", "codex"),
                       ("reviewer", None, "codex"),
                       ("reviewer", "executor", None),
                       (None, "executor", "codex")):
            with self.subTest(values=values):
                self.assertFalse(_reviewer_native_session_independent(*values))
        self.assertTrue(_reviewer_native_session_independent(
            'reviewer', 'executor', 'codex'))

    def test_host_test_gate_uses_fresh_cache_prefix_outside_worktree(self):
        test_path = 'tools/llmtool/tests/test_codex_direct_developer.py'
        source_sha = 'a' * 40
        diff_sha = 'b' * 64
        snapshot = {'diff_sha256': diff_sha, 'changed_paths': [test_path]}
        original_popen = subprocess.Popen
        observed = {}

        def launch_child(argv, **kwargs):
            cache_path = Path(kwargs['env']['PYTHONPYCACHEPREFIX'])
            observed.update(argv=argv, cache_path=cache_path)
            self.assertTrue(cache_path.is_dir())
            self.assertNotIn(self.root, cache_path.parents)
            self.assertEqual(list(cache_path.iterdir()), [])
            return original_popen([os.sys.executable, '-c', "print('child ran')"], **kwargs)

        with patch.object(task_completion, '_worktree_snapshot',
                          side_effect=[snapshot, snapshot]), \
                patch.object(task_completion.subprocess, 'Popen', side_effect=launch_child):
            evidence = task_completion.run_bounded_worktree_tests(
                root=self.root, source_sha=source_sha, diff_sha256=diff_sha,
                changed_paths=[test_path], read_paths=[test_path], write_paths=[test_path])
        self.assertTrue(any(arg == '-B' for arg in observed['argv']))
        self.assertFalse(observed['cache_path'].exists())
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]['exit_code'], 0)

    def test_captured_in_scope_new_file_is_a_valid_codex_result_diff(self):
        new_path = 'new_module.py'
        (self.root / new_path).write_text('VALUE = 3\n')
        before_index = self._completion_git(self.root, ('write-tree',))
        changed, raw = task_completion._worktree_diff(self.root, self.source_sha)
        artifact = raw.decode('utf-8')
        self.assertEqual(changed, (new_path,))
        self.assertIsNone(task_submission_cli._developer_output_rejection(
            artifact, [new_path], allow_new_files=True))
        self.assertEqual(self._completion_git(self.root, ('write-tree',)), before_index)

        outside, outside_raw = task_completion._worktree_diff(self.root, self.source_sha)
        self.assertEqual(outside, (new_path,))
        self.assertEqual(task_submission_cli._developer_output_rejection(
            outside_raw.decode('utf-8'), ['src.py']), 'DEVELOPER_OUTPUT_OUT_OF_SCOPE')

        (self.root / new_path).unlink()
        (self.root / new_path).symlink_to(self.root / 'src.py')
        with self.assertRaises(task_completion.LLMToolError) as symlink:
            task_completion._worktree_diff(self.root, self.source_sha)
        self.assertEqual(symlink.exception.completion_code, 'WORKTREE_FILE_UNSUPPORTED')


if __name__ == '__main__':
    unittest.main()
