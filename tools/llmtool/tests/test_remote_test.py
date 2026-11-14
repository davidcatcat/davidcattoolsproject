import ast
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import remote_test
from llmtool.errors import LLMToolError
from llmtool.task_store import _json_bytes, _validate_remote_test_receipts


SHA = 'a' * 40
OTHER = 'b' * 40


class Store:
    def __init__(self):
        self.task = {
            'task_id': '1' * 32, 'run_id': '2' * 32, 'pm_session_id': 'pm-' + '3' * 32,
            'backend_session_semantics': 'ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL',
            'pm_provenance': {'source': 'ACTIVE_PM_RUNTIME', 'provider_invoked': False},
            'completion': {'evidence': {
                'common_dir': '/repo/.git',
                'result': {'result_head': SHA},
            }},
            'developer': {
                'read_paths': ['tools/llmtool/tests/test_remote_test.py'],
                'write_paths': [],
                'direct_result_callback': {
                'state': 'PM_EVIDENCE_READY',
                'evidence': {'common_dir': '/repo/.git',
                    'result': {'result_head': SHA},
                    'tests': [{'path': 'tools/llmtool/tests/test_remote_test.py'}]},
            }},
            'remote_test_receipts': [], 'completion_events': [],
            'completion_event_tombstones': [], 'remote_test_outcomes': [],
            'published_pr': None,
        }

    def get(self, task_id):
        if task_id != self.task['task_id']:
            raise KeyError(task_id)
        return self.task

    def append_remote_test_receipt(self, task_id, receipt):
        self.task['remote_test_receipts'].append(receipt)
        return receipt

    def record_published_pr(self, task_id, pr, head_sha):
        existing = self.task.get('published_pr')
        if (type(existing) is dict and existing.get('pr') == pr and
                existing.get('head_sha') == head_sha):
            return existing
        self.task['published_pr'] = {
            'pr': pr, 'head_sha': head_sha, 'recorded_at': '2026-10-27T00:00:00Z'}
        return self.task['published_pr']

    def append_remote_test_outcome(self, task_id, outcome):
        if outcome in self.task['remote_test_outcomes']:
            index = self.task['remote_test_outcomes'].index(outcome)
            return {'outcome': outcome, 'counter': index, 'created': False}
        counter = len(self.task['remote_test_outcomes'])
        self.task['remote_test_outcomes'].append(outcome)
        return {'outcome': outcome, 'counter': counter, 'created': True}

    def record_completion_event(self, task_id, **event):
        saved = dict(event, task_id=task_id, pm_session_id=self.task['pm_session_id'],
                     state='PENDING')
        self.task['completion_events'].append(saved)
        return {'event': saved, 'created': True}


class RemoteTestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / 'remote-test.json'
        self.config.write_text(json.dumps({
            'host': 'ec2.example.org', 'user': 'runner',
            'repo_path': '/home/runner/project', 'python': '/usr/bin/python3',
        }))
        self.config.chmod(0o600)
        self.store = Store()
        self.calls = []

    def receipt(self, result='PASS', *, fetch=SHA, exit_code=0, final=('OK',),
                failure_excerpt=None):
        receipt = {
            'result': result, 'exit_code': exit_code, 'source_sha': SHA,
            'tree': 'c' * 40, 'fetch_head': fetch, 'modules': ['test_remote_test'],
            'identity': {'uid': 65534}, 'started_at': '2026-10-27T00:00:00Z',
            'ended_at': '2026-10-27T00:00:01Z', 'elapsed_s': 1.0,
            'ran_total': 1, 'final': list(final), 'failures': [],
            'log_bytes': 0, 'log_sha256': 'd' * 64,
            'remote_log_path': '/tmp/llmtool-remote-log.Abc123/remote-test.log',
        }
        if failure_excerpt is not None:
            receipt['failure_excerpt'] = failure_excerpt
        return json.dumps(receipt, separators=(',', ':')).encode()

    def failure_excerpt(self, output, stderr=''):
        parsed = ast.parse(remote_test._REMOTE_SCRIPT)
        sensitive = next(node for node in parsed.body
                         if isinstance(node, ast.Assign) and
                         any(isinstance(target, ast.Name) and target.id == 'SENSITIVE'
                             for target in node.targets))
        helper = next(node for node in parsed.body
                      if isinstance(node, ast.FunctionDef) and
                      node.name == 'failure_excerpt')
        namespace = {'re': re, '_emitted_sensitive': remote_test._emitted_sensitive}
        exec(compile(ast.Module(body=[sensitive, helper], type_ignores=[]),
                     '<remote-excerpt-helper>', 'exec'), namespace)
        return namespace['failure_excerpt']([output.encode(), stderr.encode()],
            '/tmp/llmtool-remote-test.Abc123', '/home/runner')

    def run_it(self, data=None, *, error=None, modules=('test_remote_test',), sha=SHA,
               pr=None):
        def transport(argv, script, timeout):
            self.calls.append((argv, script, timeout))
            if error:
                raise error
            return SimpleNamespace(stdout=self.receipt() if data is None else data,
                                   stderr=b'', returncode=0)
        with patch.object(remote_test, 'read_d6_registration',
                          return_value=SimpleNamespace(result_sha=SHA)):
            return remote_test.run_remote_test(
                self.store.task['task_id'], executor_task='d6-task', sha=sha,
                ref='refs/pull/425/head', modules=modules, pr=pr, store=self.store,
                config_path=self.config, transport=transport)

    def test_pass_is_stored_on_same_task_and_delivered_to_pm_inbox(self):
        result = self.run_it()
        self.assertEqual(result['result'], 'PASS')
        self.assertEqual(len(self.store.task['remote_test_receipts']), 1)
        self.assertEqual(len(self.store.task['completion_events']), 1)
        self.assertEqual(self.store.task['completion_events'][0]['source'], 'EC2_REMOTE_TEST')

    def test_non_ascii_final_line_receipt_digest_matches_store_encoding(self):
        result = self.run_it(self.receipt(final=('é OK',)))

        self.assertEqual(result['result'], 'PASS')
        stored = self.store.task['remote_test_receipts'][0]
        digest_input = dict(stored)
        claimed = digest_input.pop('receipt_digest')
        self.assertEqual(claimed, hashlib.sha256(_json_bytes(digest_input)).hexdigest())

    def test_fail_and_sha_mismatch_are_not_pass(self):
        failed = self.run_it(self.receipt('FAIL', exit_code=1, final=('FAILED (failures=1)',)))
        self.assertEqual(failed['result'], 'FAIL')
        self.store = Store()
        mismatch = self.run_it(self.receipt('SHA_MISMATCH', fetch=OTHER, exit_code=41))
        self.assertEqual(mismatch['result'], 'SHA_MISMATCH')
        invalid_pass = json.loads(self.receipt(fetch=OTHER).decode())
        self.assertIsNone(remote_test._validate_remote_receipt(
            json.dumps(invalid_pass, separators=(',', ':')).encode(),
            sha=SHA, modules=('test_remote_test',)))

    def test_receipt_validation_accepts_only_exact_skip_summary(self):
        accepted = self.receipt(final=('OK (skipped=2)',))
        rejected = self.receipt(final=('OK (expected failures=1)',))

        self.assertIsNotNone(remote_test._validate_remote_receipt(
            accepted, sha=SHA, modules=('test_remote_test',)))
        self.assertIsNone(remote_test._validate_remote_receipt(
            rejected, sha=SHA, modules=('test_remote_test',)))

    def test_failure_excerpt_is_bounded_redacted_and_only_on_failures(self):
        cred_value = ''.join(['sk', '-']) + 'a' * 16
        screened_label = ''.join(('to', 'ken', '='))
        output = ('FAIL: test_expected (test_remote_test.Case)\n'
                  'Traceback (most recent call last):\n'
                  '  File "/tmp/llmtool-remote-test.Abc123/test.py", line 9\n'
                  'AssertionError: expected value\n'
                  + screened_label + cred_value + '\n'
                  '----------------------------------------------------------------------\n'
                  'Ran 1 test in 0.1s\nFAILED (failures=1)')
        excerpt = self.failure_excerpt(output)
        self.assertIn('FAIL: test_expected (test_remote_test.Case)', excerpt)
        self.assertIn('AssertionError: expected value', excerpt)
        self.assertIn('<REMOTE_WORKDIR>/test.py', excerpt)
        self.assertNotIn('/tmp/llmtool-remote-test.Abc123', excerpt)
        self.assertNotIn(cred_value, excerpt)
        unsafe_header = self.failure_excerpt(
            'FAIL: test_expected token=' + cred_value + '\n'
            'Traceback (most recent call last):\n'
            'AssertionError: expected value\n'
            '----------------------------------------------------------------------\n')
        self.assertNotIn(cred_value, unsafe_header)
        self.assertIn('AssertionError: expected value', unsafe_header)
        self.assertEqual(self.failure_excerpt(''), '')
        with tempfile.TemporaryDirectory() as test_dir:
            fixture = Path(test_dir) / 'test_excerpt_fixture.py'
            fixture.write_text(
                'import unittest\n'
                'class ExcerptFailures(unittest.TestCase):\n'
                '    def test_assertion(self):\n'
                "        self.assertEqual(1, 2, 'expected value')\n"
                '    def test_error(self):\n'
                "        raise RuntimeError('broken')\n")
            generated = subprocess.run(
                [sys.executable, '-m', 'unittest', 'discover', '-s', test_dir,
                 '-p', fixture.name], capture_output=True, check=False)
        self.assertNotEqual(generated.returncode, 0)
        stdout = generated.stdout.decode('utf-8', 'replace')
        stderr = generated.stderr.decode('utf-8', 'replace')
        combined = self.failure_excerpt(stdout, stderr)
        self.assertRegex(combined, r'FAIL: test_assertion '
            r'\(test_excerpt_fixture\.ExcerptFailures(?:\.test_assertion)?\)')
        self.assertRegex(combined, r'ERROR: test_error '
            r'\(test_excerpt_fixture\.ExcerptFailures(?:\.test_error)?\)')
        self.assertIn('AssertionError:', combined)
        self.assertIn('expected value', combined)
        self.assertIn('RuntimeError: broken', combined)
        self.assertEqual(self.failure_excerpt(''), '')
        home_path = self.failure_excerpt(
            'FAIL: test_home\n  File "/home/runner/test.py", line 1\n'
            'AssertionError: failed\n----------------------------------------------------------------------\n')
        self.assertIn('<REMOTE_HOME>/test.py', home_path)
        self.assertNotIn('/home/runner', home_path)
        url_value = ''.join(('https', '://', 'user', ':', 'value', '@',
                             'example.test/path'))
        jwt_value = '.'.join(('a' * 12, 'b' * 12, 'c' * 12))
        screened_output = ('FAIL: test_expected\n' + url_value + '\n' +
                           jwt_value + '\n' +
                           '----------------------------------------------------------------------\n')
        screened_excerpt = self.failure_excerpt(screened_output)
        self.assertNotIn(url_value, screened_excerpt)
        self.assertNotIn(jwt_value, screened_excerpt)
        long_output = ('FAIL: test_expected (test_remote_test.Case)\n' +
                       ('detail ' + 'x' * 120 + '\n') * 50 +
                       '----------------------------------------------------------------------\n')
        bounded = self.failure_excerpt(long_output)
        self.assertLessEqual(len(bounded.encode('utf-8')), 4096)
        self.assertLessEqual(len(bounded.splitlines()), 40)
        self.assertIn('len(raw)<=32768', remote_test._REMOTE_SCRIPT)

        failed = self.run_it(self.receipt('FAIL', exit_code=1,
            final=('FAILED (failures=1)',), failure_excerpt=excerpt))
        self.assertIn('failure_excerpt', failed)
        self.store = Store()
        passed = self.run_it()
        self.assertNotIn('failure_excerpt', passed)

        receipt = json.loads(self.receipt('FAIL', exit_code=1,
            final=('FAILED (failures=1)',), failure_excerpt='x' * 4097))
        self.assertIsNone(remote_test._validate_remote_receipt(
            json.dumps(receipt, separators=(',', ':')).encode(),
            sha=SHA, modules=('test_remote_test',)))
        capped = json.loads(self.receipt('FAIL', exit_code=1,
            final=('FAILED (failures=1)',), failure_excerpt='x' * 4096))
        self.assertIsNotNone(remote_test._validate_remote_receipt(
            json.dumps(capped, separators=(',', ':')).encode(),
            sha=SHA, modules=('test_remote_test',)))
        passed_with_excerpt = json.loads(self.receipt().decode())
        passed_with_excerpt['failure_excerpt'] = ''
        self.assertIsNone(remote_test._validate_remote_receipt(
            json.dumps(passed_with_excerpt, separators=(',', ':')).encode(),
            sha=SHA, modules=('test_remote_test',)))

        screened_label = ''.join(('to', 'ken', '='))
        cred_value = ''.join(['sk', '-']) + 'a' * 16
        screened = json.loads(self.receipt('FAIL', exit_code=1,
            final=('FAILED (failures=1)',),
            failure_excerpt=screened_label + cred_value).decode())
        self.assertIsNone(remote_test._validate_remote_receipt(
            json.dumps(screened, separators=(',', ':')).encode(),
            sha=SHA, modules=('test_remote_test',)))

    def test_remote_excerpt_screen_is_self_contained(self):
        script = remote_test._REMOTE_SCRIPT
        self.assertNotIn('import llmtool', script)
        self.assertNotIn('sys.path', script)

    def test_timeout_unavailable_and_unknown_receipts(self):
        timed = self.run_it(error=__import__('subprocess').TimeoutExpired('ssh', 600))
        self.assertEqual(timed['result'], 'TIMEOUT')
        unavailable = self.run_it(error=OSError('offline'))
        self.assertEqual(unavailable['result'], 'UNAVAILABLE')
        malformed = self.run_it(b'{not-json')
        self.assertEqual(malformed['result'], 'UNKNOWN')
        oversize = self.run_it(b' ' * 32769)
        self.assertEqual(oversize['result'], 'UNKNOWN')
        self.assertEqual([item['code'] for item in self.store.task['remote_test_outcomes']],
                         ['SSH_TIMEOUT', 'SSH_UNAVAILABLE', 'RECEIPT_INVALID',
                          'RECEIPT_INVALID'])
        self.assertEqual(len(self.store.task['completion_events']), 4)
        self.assertTrue(all(event['source'] == 'EC2_REMOTE_TEST'
                            for event in self.store.task['completion_events']))

    def test_admission_denials_are_persisted_and_have_events(self):
        result = self.run_it(modules=('test_remote_test;id',))
        self.assertEqual(result['code'], 'MODULE_NOT_ADMITTED')
        self.assertTrue(result['persisted'])
        self.assertIsNotNone(result['event_id'])
        self.assertEqual(len(self.store.task['remote_test_outcomes']), 1)
        self.assertEqual(len(self.store.task['completion_events']), 1)

    def test_timeout_outcome_can_admit_recorded_pr_result_from_task_paths(self):
        developer = self.store.task['developer']
        developer.update(
            state='TIMEOUT',
            read_paths=['tools/llmtool/tests/test_remote_test.py'],
            write_paths=['tools/llmtool/remote_test.py'])
        callback = developer['direct_result_callback']
        callback.update(state='WAITING_EXECUTOR', missing_gates=['EXECUTOR_TIMEOUT'])
        callback['evidence'].pop('tests')
        self.store.task['completion']['evidence']['review_pr_ci'] = {
            'pr_number': 425, 'head_sha': SHA}

        result = self.run_it(pr=425)

        self.assertEqual(result['result'], 'PASS')
        self.assertEqual(len(self.calls), 1)

    def test_unrecorded_and_mismatched_heads_are_refused_before_transport(self):
        with patch.object(remote_test, 'read_d6_registration',
                          return_value=SimpleNamespace(result_sha=None)):
            unrecorded = remote_test.run_remote_test(
                self.store.task['task_id'], executor_task='d6-task', sha=SHA,
                ref='refs/pull/425/head', modules=('test_remote_test',),
                store=self.store, config_path=self.config,
                transport=lambda *args: self.fail('transport called'))
        self.assertEqual(unrecorded['code'], 'RECEIPT_HEAD_UNRECORDED')

        self.store = Store()
        with patch.object(remote_test, 'read_d6_registration',
                          return_value=SimpleNamespace(result_sha=OTHER)):
            mismatch = remote_test.run_remote_test(
                self.store.task['task_id'], executor_task='d6-task', sha=SHA,
                ref='refs/pull/425/head', modules=('test_remote_test',),
                store=self.store, config_path=self.config,
                transport=lambda *args: self.fail('transport called'))
        self.assertEqual(mismatch['code'], 'RECEIPT_HEAD_MISMATCH')
        self.assertEqual(self.calls, [])

    def test_outcome_event_replay_is_idempotent_but_retry_gets_new_event(self):
        first = self.run_it(error=OSError('offline'))
        saved = self.store.task['remote_test_outcomes'][0]
        replay = remote_test.record_remote_test_outcome_event(
            self.store, self.store.task['task_id'], saved, 0)
        second = self.run_it(error=OSError('offline'))
        self.assertEqual(replay['event_id'], first['event_id'])
        self.assertNotEqual(second['event_id'], first['event_id'])
        self.assertEqual(len(self.store.task['completion_events']), 2)

    def test_admission_and_argv_injection_attempts_never_call_transport(self):
        denied = self.run_it(modules=('test_remote_test;id',))
        self.assertEqual(denied['result'], 'ADMISSION_DENIED')
        injected = self.run_it(sha=SHA + ';id')
        self.assertEqual(injected['result'], 'ADMISSION_DENIED')
        option_ref = remote_test.run_remote_test(
            self.store.task['task_id'], executor_task='d6-task', sha=SHA,
            ref='--prune', modules=('test_remote_test',), store=self.store,
            config_path=self.config, transport=lambda *args: self.fail('transport called'))
        self.assertEqual(option_ref['result'], 'ADMISSION_DENIED')
        self.assertIn('"--end-of-options","origin",ref',
                      remote_test._REMOTE_SCRIPT)
        self.assertEqual(self.calls, [])

    def test_remote_script_installs_package_and_discovers_selected_test(self):
        self.assertIn('"install","--no-deps","-e","tools/llmtool"',
                      remote_test._REMOTE_SCRIPT)
        self.assertIn('"unittest","discover","-s","tests","-p",mod+".py"',
                      remote_test._REMOTE_SCRIPT)

    def test_remote_script_mktemp_dirs_are_verified_as_private(self):
        self.assertNotIn('"mktemp","-d","-m"', remote_test._REMOTE_SCRIPT)
        self.assertIn('info.st_mode&0o777!=0o700', remote_test._REMOTE_SCRIPT)
        self.assertIn('log_info.st_mode&0o777!=0o700', remote_test._REMOTE_SCRIPT)

    def test_nobody_commands_change_directory_through_env_without_cwd(self):
        self.assertIn('["sudo","-n","-u","nobody","env","-i","-C",work]',
                      remote_test._REMOTE_SCRIPT)
        self.assertIn('"HOME":work+"/.home","TMPDIR":work+"/.tmp"',
                      remote_test._REMOTE_SCRIPT)
        self.assertIn('"GIT_OPTIONAL_LOCKS":"0"', remote_test._REMOTE_SCRIPT)
        self.assertIn('["mkdir","-m","700","--",work+"/.home",work+"/.tmp"]',
                      remote_test._REMOTE_SCRIPT)
        self.assertNotIn('cwd=work', remote_test._REMOTE_SCRIPT)
        self.assertNotIn('cwd=work+', remote_test._REMOTE_SCRIPT)
        self.assertIn('REMOTE_STAGE_FAILED {stage} {type(exc).__name__}',
                      remote_test._REMOTE_SCRIPT)

    def test_test_step_argv_uses_private_workspace_home_and_tmp(self):
        parsed = ast.parse(remote_test._REMOTE_SCRIPT)
        test_call = next(node.value for node in ast.walk(parsed)
                         if isinstance(node, ast.Assign) and
                         any(isinstance(target, ast.Name) and target.id == 'test'
                             for target in node.targets) and
                         isinstance(node.value, ast.Call) and
                         isinstance(node.value.func, ast.Name) and
                         node.value.func.id == 'nobody_run')
        cwd_arg = test_call.args[0]
        self.assertIsInstance(cwd_arg, ast.BinOp)
        self.assertIsInstance(cwd_arg.op, ast.Add)
        self.assertIsInstance(cwd_arg.left, ast.Name)
        self.assertEqual(cwd_arg.left.id, 'work')
        self.assertIsInstance(cwd_arg.right, ast.Constant)
        self.assertEqual(cwd_arg.right.value, '/tools/llmtool')
        env_arg = next(keyword.value for keyword in test_call.keywords
                       if keyword.arg == 'env')
        self.assertIsInstance(env_arg, ast.Dict)
        self.assertEqual([key.value for key in env_arg.keys], ['HOME', 'TMPDIR'])
        for value, suffix in zip(env_arg.values, ('/.home', '/.tmp')):
            self.assertIsInstance(value, ast.BinOp)
            self.assertIsInstance(value.op, ast.Add)
            self.assertIsInstance(value.left, ast.Name)
            self.assertEqual(value.left.id, 'work')
            self.assertIsInstance(value.right, ast.Constant)
            self.assertEqual(value.right.value, suffix)

        work = '/tmp/llmtool-remote-test.Abc123'
        env = {'HOME': work + '/.home', 'TMPDIR': work + '/.tmp'}
        nobody = next(node for node in parsed.body
                      if isinstance(node, ast.FunctionDef) and node.name == 'nobody')
        namespace = {}
        exec(compile(ast.Module(body=[nobody], type_ignores=[]),
                     '<remote-nobody-helper>', 'exec'), namespace)
        command = namespace['nobody'](
            work + '/tools/llmtool', ['/usr/bin/timeout', '120', 'python', '-m',
                                      'unittest', 'discover'], env)
        self.assertEqual(command[:8],
                         ['sudo', '-n', '-u', 'nobody', 'env', '-i', '-C',
                          work + '/tools/llmtool'])
        self.assertIn('HOME=' + work + '/.home', command)
        self.assertIn('TMPDIR=' + work + '/.tmp', command)

    def test_receipt_accepts_missing_log_path(self):
        receipt = json.loads(self.receipt().decode())
        receipt['remote_log_path'] = None
        self.assertIsNotNone(remote_test._validate_remote_receipt(
            json.dumps(receipt, separators=(',', ':')).encode(),
            sha=SHA, modules=('test_remote_test',)))

    def test_pass_receipt_requires_tests_nonroot_identity_and_no_failures(self):
        for field, value in (
                ('ran_total', 0), ('identity', {'uid': 0}),
                ('failures', ['test_remote_test'])):
            with self.subTest(field=field):
                receipt = json.loads(self.receipt().decode())
                receipt[field] = value
                raw = json.dumps(receipt, separators=(',', ':')).encode()
                self.assertIsNone(remote_test._validate_remote_receipt(
                    raw, sha=SHA, modules=('test_remote_test',)))

    def test_remote_script_has_deadline_kill_and_bounded_owned_cleanup(self):
        script = remote_test._REMOTE_SCRIPT
        self.assertIn('deadline=begin+540', script)
        self.assertIn('"--kill-after=2s"', script)
        self.assertIn('info.st_uid!=nobody_uid', script)
        self.assertIn('now-info.st_mtime>86400', script)
        self.assertIn('owned_dirs[20:]', script)

    def test_remote_script_open_lock_creates_reuses_validates_and_excludes(self):
        self.assertIn('os.path.expanduser("~")+"/.llmtool-remote-test.lock"',
                      remote_test._REMOTE_SCRIPT)
        self.assertIn('os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600',
                      remote_test._REMOTE_SCRIPT)
        self.assertNotIn('0o666', remote_test._REMOTE_SCRIPT)
        parsed = ast.parse(remote_test._REMOTE_SCRIPT)
        helper = next(node for node in parsed.body
                      if isinstance(node, ast.FunctionDef) and node.name == 'open_lock')
        namespace = {'fcntl': fcntl, 'os': os, 'stat': __import__('stat'),
                     'sys': sys}
        exec(compile(ast.Module(body=[helper], type_ignores=[]),
                     '<remote-lock-helper>', 'exec'), namespace)
        home = Path(self.temp.name)
        lock_path = home / '.llmtool-remote-test.lock'

        held_fd = namespace['open_lock'](str(lock_path))
        try:
            info = lock_path.stat()
            self.assertEqual(info.st_mode & 0o777, 0o600)
            self.assertEqual(info.st_uid, os.getuid())
            self.assertEqual(info.st_nlink, 1)
            with self.assertRaises(BlockingIOError):
                namespace['open_lock'](str(lock_path))
        finally:
            os.close(held_fd)

        original_inode = lock_path.stat().st_ino
        released_fd = namespace['open_lock'](str(lock_path))
        os.close(released_fd)
        self.assertEqual(lock_path.stat().st_ino, original_inode)

        lock_path.chmod(0o644)
        with self.assertRaises(SystemExit) as wrong_mode:
            namespace['open_lock'](str(lock_path))
        self.assertEqual(wrong_mode.exception.code, 76)

        lock_path.unlink()
        lock_path.touch(mode=0o600)
        hard_link = home / 'hard-link'
        os.link(lock_path, hard_link)
        with self.assertRaises(SystemExit) as linked:
            namespace['open_lock'](str(lock_path))
        self.assertEqual(linked.exception.code, 76)
        hard_link.unlink()

        lock_path.unlink()
        target = home / 'target'
        target.touch(mode=0o600)
        lock_path.symlink_to(target)
        with self.assertRaises(SystemExit) as symlink:
            namespace['open_lock'](str(lock_path))
        self.assertEqual(symlink.exception.code, 76)

    def test_remote_script_final_line_recognizes_skip_summaries(self):
        parsed = ast.parse(remote_test._REMOTE_SCRIPT)
        helper = next(node for node in parsed.body
                      if isinstance(node, ast.FunctionDef) and node.name == 'final_line')
        namespace = {'re': re}
        exec(compile(ast.Module(body=[helper], type_ignores=[]),
                     '<remote-final-line-helper>', 'exec'), namespace)

        for summary in ('OK (skipped=2)', 'OK (expected failures=1)',
                        'OK (skipped=2, expected failures=1)',
                        'OK (expected failures=1, skipped=2)'):
            with self.subTest(summary=summary):
                final = namespace['final_line'](['Ran 3 tests', summary])
                self.assertEqual(final, summary)
                self.assertFalse(final.endswith('OK'))

        passing = next(node for node in parsed.body
                       if isinstance(node, ast.FunctionDef) and
                       node.name == 'passing_final')
        namespace = {'re': re}
        exec(compile(ast.Module(body=[passing], type_ignores=[]),
                     '<remote-passing-final-helper>', 'exec'), namespace)
        self.assertTrue(namespace['passing_final']('OK'))
        self.assertTrue(namespace['passing_final']('OK (skipped=2)'))
        for summary in ('OK (skipped=0)', 'OK (skipped=1000000)',
                        'OK (expected failures=1)',
                        'OK (skipped=2, expected failures=1)'):
            with self.subTest(passing_summary=summary):
                self.assertFalse(namespace['passing_final'](summary))

    def test_module_admission_is_bounded(self):
        modules = tuple(f'test_module{i}' for i in range(9))
        self.store.task['developer']['direct_result_callback']['evidence']['tests'] = [
            {'path': f'tools/llmtool/tests/{module}.py'} for module in modules]
        denied = self.run_it(modules=modules)
        self.assertEqual((denied['result'], denied['code']),
                         ('ADMISSION_DENIED', 'MODULE_NOT_ADMITTED'))
        self.assertTrue(denied['persisted'])
        self.assertEqual(self.calls, [])

    def test_test_failed_callback_admits_modules_without_changing_callback(self):
        callback = self.store.task['developer']['direct_result_callback']
        callback.update(state='WAITING_EXECUTOR', missing_gates=['TEST_FAILED'])
        callback['evidence']['verification_state'] = 'FAILED'
        callback_bytes = json.dumps(callback, sort_keys=True,
                                    separators=(',', ':')).encode()

        failed_receipt = self.receipt('FAIL', exit_code=1,
                                      final=('FAILED (failures=1)',))
        failed = self.run_it(failed_receipt)
        self.assertEqual(failed['result'], 'FAIL')
        self.assertEqual(json.dumps(callback, sort_keys=True,
                                    separators=(',', ':')).encode(), callback_bytes)

        passed = self.run_it()
        self.assertEqual(passed['result'], 'PASS')
        self.assertEqual(json.dumps(callback, sort_keys=True,
                                    separators=(',', ':')).encode(), callback_bytes)
        self.assertEqual(len(self.calls), 2)

    def test_non_eligible_waiting_callback_states_do_not_admit_modules(self):
        callback = self.store.task['developer']['direct_result_callback']
        self.store.task['developer']['read_paths'] = []
        self.store.task['developer']['write_paths'] = []
        eligible_evidence = dict(callback['evidence'], verification_state='FAILED')
        variants = (
            {'state': 'WAITING_EXECUTOR', 'missing_gates': ['OTHER'],
             'evidence': eligible_evidence},
            {'state': 'WAITING_EXECUTOR', 'missing_gates': ['TEST_FAILED'],
             'evidence': dict(eligible_evidence, verification_state='PASS')},
            {'state': 'FAILED', 'missing_gates': ['TEST_FAILED'],
             'evidence': eligible_evidence},
            {'state': 'WAITING_EXECUTOR', 'missing_gates': ['TEST_FAILED', 'OTHER'],
             'evidence': eligible_evidence},
        )
        for variant in variants:
            with self.subTest(variant=variant):
                callback.clear()
                callback.update(variant)
                result = self.run_it()
                self.assertEqual((result['result'], result['code']),
                                 ('ADMISSION_DENIED', 'MODULE_NOT_ADMITTED'))
        self.assertEqual(self.calls, [])

    def test_pr_without_completion_review_reads_and_binds_github_head(self):
        with patch.object(remote_test, '_github_get',
                          return_value={'number': 425, 'head': {'sha': SHA}}) as github_get:
            result = self.run_it(pr=425)

        self.assertEqual(result['result'], 'PASS')
        github_get.assert_called_once_with('pulls/425')
        self.assertEqual(len(self.calls), 1)

    def test_pr_without_completion_review_denies_mismatch_or_read_failure_before_ssh(self):
        for response, error, code in (
                ({'number': 425, 'head': {'sha': OTHER}}, None,
                 'RECEIPT_HEAD_MISMATCH'),
                ({'number': 426, 'head': {'sha': SHA}}, None,
                 'RECEIPT_HEAD_UNRECORDED'),
                (None, OSError('unavailable'), 'RECEIPT_HEAD_UNRECORDED')):
            with self.subTest(response=response, error=error):
                with patch.object(remote_test, '_github_get',
                                  side_effect=error if error else None,
                                  return_value=response):
                    result = self.run_it(pr=425)
                self.assertEqual((result['result'], result['code']),
                                 ('ADMISSION_DENIED', code))
        self.assertEqual(self.calls, [])

    def test_pr_records_published_link_after_matching_head_admission(self):
        with patch.object(remote_test, '_github_get',
                          return_value={'number': 425, 'head': {'sha': SHA}}):
            result = self.run_it(pr=425)

        self.assertEqual(result['result'], 'PASS')
        self.assertEqual(self.store.task['published_pr']['pr'], 425)
        self.assertEqual(self.store.task['published_pr']['head_sha'], SHA)

    def run_with_merged_head(self, *, pr=425, pr_head=SHA, admitted=True, sha=SHA):
        """Run with the D6 at SHA while the Task evidence still records an older head."""
        registration = SimpleNamespace(result_sha=SHA, root='/repo/worktree')

        def transport(argv, script, timeout):
            self.calls.append((argv, script, timeout))
            return SimpleNamespace(stdout=self.receipt(), stderr=b'', returncode=0)

        with patch.object(remote_test, 'read_d6_registration', return_value=registration), \
                patch.object(remote_test, '_github_get',
                             return_value={'number': 425, 'head': {'sha': pr_head}}) as github, \
                patch('llmtool.task_submission_cli.result_head_admitted',
                      return_value=admitted) as check:
            result = remote_test.run_remote_test(
                self.store.task['task_id'], executor_task='d6-task', sha=sha,
                ref='refs/pull/425/head', modules=('test_remote_test',), pr=pr,
                store=self.store, config_path=self.config, transport=transport)
        return result, check, github

    def test_older_evidence_head_runs_when_fresh_pr_and_shared_check_agree(self):
        self.store.task['completion']['evidence']['result']['result_head'] = OTHER

        result, check, github = self.run_with_merged_head()

        self.assertEqual(result['result'], 'PASS')
        self.assertEqual(len(self.calls), 1)
        github.assert_called_once_with('pulls/425')
        check.assert_called_once()
        self.assertEqual(check.call_args.kwargs['recorded_head'], OTHER)
        self.assertEqual(check.call_args.kwargs['head'], SHA)
        self.assertEqual(check.call_args.kwargs['root'], '/repo/worktree')
        self.assertEqual(self.store.task['published_pr']['head_sha'], SHA)

    def test_older_review_head_runs_when_fresh_pr_and_shared_check_agree(self):
        self.store.task['completion']['evidence']['review_pr_ci'] = {
            'pr_number': 425, 'head_sha': OTHER}

        result, check, _ = self.run_with_merged_head()

        self.assertEqual(result['result'], 'PASS')
        self.assertEqual(check.call_args.kwargs['recorded_head'], OTHER)

    def test_each_older_recorded_head_must_pass_the_shared_check(self):
        older = 'c' * 40
        evidence = self.store.task['completion']['evidence']
        evidence['result']['result_head'] = older
        evidence['review_pr_ci'] = {'pr_number': 425, 'head_sha': OTHER}

        result, check, _ = self.run_with_merged_head()

        self.assertEqual(result['result'], 'PASS')
        self.assertEqual([call.kwargs['recorded_head'] for call in check.call_args_list],
                         [older, OTHER])

    def test_older_head_is_refused_when_the_shared_check_refuses(self):
        self.store.task['completion']['evidence']['result']['result_head'] = OTHER

        result, check, _ = self.run_with_merged_head(admitted=False)

        self.assertEqual((result['result'], result['code']),
                         ('ADMISSION_DENIED', 'RECEIPT_HEAD_MISMATCH'))
        check.assert_called_once()
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.store.task['published_pr'])

    def test_older_head_is_refused_unless_fresh_pr_and_sha_equal_the_d6_head(self):
        for overrides in ({'pr_head': OTHER}, {'pr_head': 'c' * 40}, {'sha': OTHER}):
            with self.subTest(overrides=overrides):
                self.store = Store()
                self.store.task['completion']['evidence']['result']['result_head'] = OTHER
                result, check, _ = self.run_with_merged_head(**overrides)
                self.assertEqual((result['result'], result['code']),
                                 ('ADMISSION_DENIED', 'RECEIPT_HEAD_MISMATCH'))
                check.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_older_head_is_refused_when_the_fresh_pr_head_is_unreadable(self):
        self.store.task['completion']['evidence']['result']['result_head'] = OTHER
        with patch.object(remote_test, '_github_get', side_effect=OSError('unavailable')):
            result = self.run_it(pr=425)
        self.assertEqual((result['result'], result['code']),
                         ('ADMISSION_DENIED', 'RECEIPT_HEAD_MISMATCH'))
        self.assertEqual(self.calls, [])

    def test_older_head_without_a_pr_keeps_todays_refusal(self):
        self.store.task['completion']['evidence']['result']['result_head'] = OTHER

        result, check, github = self.run_with_merged_head(pr=None)

        self.assertEqual((result['result'], result['code']),
                         ('ADMISSION_DENIED', 'RECEIPT_HEAD_MISMATCH'))
        check.assert_not_called()
        github.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_invalid_pr_mismatch_and_missing_pr_do_not_record_link(self):
        invalid = self.run_it(pr=True)
        self.assertEqual(invalid['code'], 'RECEIPT_HEAD_UNRECORDED')
        self.assertIsNone(self.store.task['published_pr'])

        self.store = Store()
        with patch.object(remote_test, '_github_get',
                          return_value={'number': 425, 'head': {'sha': OTHER}}):
            mismatch = self.run_it(pr=425)
        self.assertEqual(mismatch['code'], 'RECEIPT_HEAD_MISMATCH')
        self.assertIsNone(self.store.task['published_pr'])

        self.store = Store()
        self.run_it()
        self.assertIsNone(self.store.task['published_pr'])

    def test_published_link_survives_ssh_failure_and_receipt_replay(self):
        with patch.object(remote_test, '_github_get',
                          return_value={'number': 425, 'head': {'sha': SHA}}):
            failed = self.run_it(pr=425, error=OSError('offline'))
        self.assertEqual(failed['result'], 'UNAVAILABLE')
        saved = self.store.task['published_pr']
        self.assertEqual((saved['pr'], saved['head_sha']), (425, SHA))

        with patch.object(remote_test, '_github_get',
                          return_value={'number': 425, 'head': {'sha': SHA}}):
            passed = self.run_it(pr=425)
        self.assertEqual(passed['result'], 'PASS')
        saved = self.store.task['published_pr']
        with patch.object(remote_test, '_github_get',
                          return_value={'number': 425, 'head': {'sha': SHA}}):
            replay = self.run_it(pr=425)
        self.assertEqual(replay, passed)
        self.assertEqual(self.store.task['published_pr'], saved)

    def test_pr_with_completion_review_keeps_existing_binding(self):
        self.store.task['completion']['evidence']['review_pr_ci'] = {
            'pr_number': 425, 'head_sha': SHA}
        with patch.object(remote_test, '_github_get',
                          side_effect=AssertionError('unexpected GitHub lookup')):
            result = self.run_it(pr=425)
        self.assertEqual(result['result'], 'PASS')
        self.assertEqual(len(self.calls), 1)

    def test_remote_script_recognizes_unittest_failure_summary(self):
        self.assertIn('line.startswith("FAILED (")', remote_test._REMOTE_SCRIPT)

    def test_task_store_rejects_invalid_pass_receipts(self):
        def stored_receipt(**changes):
            receipt = json.loads(self.receipt().decode())
            receipt.update(changes)
            receipt.update(task_id=self.store.task['task_id'],
                           run_id=self.store.task['run_id'], executor_task='d6-task',
                           pm_session=self.store.task['pm_session_id'],
                           command_digest='e' * 64)
            receipt.pop('receipt_digest', None)
            receipt['receipt_digest'] = hashlib.sha256(_json_bytes(receipt)).hexdigest()
            return receipt

        valid = stored_receipt()
        _validate_remote_test_receipts({'task_id': self.store.task['task_id'],
                                        'run_id': self.store.task['run_id'],
                                        'pm_session_id': self.store.task['pm_session_id'],
                                        'remote_test_receipts': [valid]})
        valid_excerpt = stored_receipt(
            result='FAIL', exit_code=1, failure_excerpt='FAIL: test_expected\nAssertionError')
        _validate_remote_test_receipts({'task_id': self.store.task['task_id'],
                                        'run_id': self.store.task['run_id'],
                                        'pm_session_id': self.store.task['pm_session_id'],
                                        'remote_test_receipts': [valid_excerpt]})
        for changes in ({'ran_total': 0}, {'identity': {'uid': 0}},
                        {'failures': ['test_remote_test']}):
            with self.subTest(changes=changes), self.assertRaises(LLMToolError) as caught:
                bad = stored_receipt(**changes)
                _validate_remote_test_receipts({'task_id': self.store.task['task_id'],
                                                'run_id': self.store.task['run_id'],
                                                'pm_session_id': self.store.task['pm_session_id'],
                                                'remote_test_receipts': [bad]})
            self.assertEqual(caught.exception.task_store_error_code, 'REMOTE_TEST_RECEIPT_INVALID')

        oversized = stored_receipt(failure_excerpt='x' * 4097)
        with self.assertRaises(LLMToolError) as caught:
            _validate_remote_test_receipts({'task_id': self.store.task['task_id'],
                                            'run_id': self.store.task['run_id'],
                                            'pm_session_id': self.store.task['pm_session_id'],
                                            'remote_test_receipts': [oversized]})
        self.assertEqual(caught.exception.task_store_error_code, 'REMOTE_TEST_RECEIPT_INVALID')

    def test_remote_busy_exit_maps_to_busy_without_receipt(self):
        def transport(argv, script, timeout):
            return SimpleNamespace(stdout=b'', stderr=b'', returncode=75)

        with patch.object(remote_test, 'read_d6_registration',
                          return_value=SimpleNamespace(result_sha=SHA)):
            result = remote_test.run_remote_test(
                self.store.task['task_id'], executor_task='d6-task', sha=SHA,
                ref='refs/pull/425/head', modules=('test_remote_test',), store=self.store,
                config_path=self.config, transport=transport)
        self.assertEqual((result['result'], result['code']), ('UNAVAILABLE', 'REMOTE_BUSY'))
        self.assertTrue(result['persisted'])
        self.assertEqual(len(self.store.task['remote_test_outcomes']), 1)
        self.assertEqual(len(self.store.task['completion_events']), 1)
        self.assertEqual(self.store.task['remote_test_receipts'], [])

    def test_unsafe_remote_lock_exit_maps_to_unavailable_without_receipt(self):
        def transport(argv, script, timeout):
            return SimpleNamespace(stdout=b'', stderr=b'', returncode=76)

        with patch.object(remote_test, 'read_d6_registration',
                          return_value=SimpleNamespace(result_sha=SHA)):
            result = remote_test.run_remote_test(
                self.store.task['task_id'], executor_task='d6-task', sha=SHA,
                ref='refs/pull/425/head', modules=('test_remote_test',), store=self.store,
                config_path=self.config, transport=transport)
        self.assertEqual((result['result'], result['code']),
                         ('UNAVAILABLE', 'REMOTE_LOCK_UNSAFE'))
        self.assertTrue(result['persisted'])
        self.assertEqual(len(self.store.task['remote_test_outcomes']), 1)
        self.assertEqual(len(self.store.task['completion_events']), 1)
        self.assertEqual(self.store.task['remote_test_receipts'], [])

    def test_identical_call_deduplicates_remote_run_receipt_and_event(self):
        first = self.run_it()
        second = self.run_it()
        self.assertEqual(first, second)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.store.task['remote_test_receipts']), 1)
        self.assertEqual(len(self.store.task['completion_events']), 1)

    def test_stored_fail_allows_retry_and_appends_second_receipt(self):
        failed = self.run_it(
            self.receipt('FAIL', exit_code=1, final=('FAILED (failures=1)',)))
        self.assertEqual(failed['result'], 'FAIL')

        retried = self.run_it()

        self.assertEqual(retried['result'], 'PASS')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.store.task['remote_test_receipts']), 2)


if __name__ == '__main__':
    unittest.main()
