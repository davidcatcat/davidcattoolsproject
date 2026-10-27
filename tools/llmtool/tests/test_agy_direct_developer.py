"""Focused fake-runner tests for the direct AGY adapter."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from llmtool import agy_direct_developer as direct


class DirectAgyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'repo'
        self.root.mkdir()
        self._git('init', '-b', 'task/agy')
        self._git('config', 'user.name', 'Fixture')
        self._git('config', 'user.email', 'fixture@example.invalid')
        (self.root / 'src.py').write_text('before\n', encoding='utf-8')
        self._git('add', 'src.py')
        self._git('commit', '-m', 'fixture')
        self.head = self._git('rev-parse', 'HEAD').decode().strip()
        self.log = Path(self.temp.name) / 'agy.log'
        self.log.write_text('model_resolver: Resolving model free-model\n', encoding='utf-8')
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def _git(self, *args):
        return subprocess.check_output(['/usr/bin/git', *args], cwd=self.root,
                                       stderr=subprocess.DEVNULL)

    def _runner(self, *, output=None, code=0, stderr=b'', edit=True,
                log_text='model_resolver: Resolving model free-model\n'):
        def runner(argv, cwd, env, timeout):
            self.calls.append((argv, cwd, env, timeout))
            if edit:
                (self.root / 'src.py').write_text('after\n', encoding='utf-8')
            self.log.write_text(log_text, encoding='utf-8')
            if output is None:
                raw = json.dumps({'conversation_id': 'conv_12345678', 'status': 'completed',
                                  'response': 'done', 'usage': {'input_tokens': 3,
                                  'output_tokens': 2, 'bad': 'skip'}}).encode()
            else:
                raw = output
            return code, raw, stderr
        return runner

    def _run(self, **overrides):
        args = dict(root=self.root, task_id='a' * 32,
                    owner_ref='https://github.com/davidcatcat/davidcattoolsproject/issues/289#issuecomment-6448939227',
                    prompt='bounded', model='free-model', effort='high',
                    expected_branch='task/agy', expected_head=self.head,
                    write_paths=['src.py'], log_path=self.log,
                    runner=self._runner(), write_boundary=lambda: {
                        'verified': True, 'policy_sha256': 'a' * 64})
        args.update(overrides)
        with patch.object(direct.worker_dispatch, 'evaluate', return_value=type(
                'Decision', (), {'allowed': True, 'reason_code': 'OWNER_ASSIGNMENT_MATCHED'})()), \
             patch.object(direct.shutil, 'which', return_value='/usr/local/bin/agy'):
            return direct.run(**args)

    def test_success_and_exact_argv(self):
        result = self._run()
        self.assertEqual(result.status, 'completed')
        self.assertEqual(result.model, 'free-model')
        self.assertEqual(result.usage, {'input_tokens': 3, 'output_tokens': 2})
        self.assertEqual(result.changed_files, ('src.py',))
        argv = self.calls[0][0]
        self.assertEqual(argv, ['/usr/local/bin/agy', '-p', 'bounded', '--model',
            'free-model', '--effort', 'high', '--sandbox', '--output-format', 'json',
            '--print-timeout', '0', '--log-file', str(self.log)])
        self.assertNotIn('--add-dir', argv)

    def test_owner_gate_refuses_before_runner(self):
        with patch.object(direct.worker_dispatch, 'evaluate', return_value=type(
                'Decision', (), {'allowed': False, 'reason_code': 'OWNER_ASSIGNMENT_REQUIRED'})()), \
             patch.object(direct.shutil, 'which', return_value='/usr/local/bin/agy'), \
             self.assertRaises(direct.DirectAgyError) as caught:
            direct.run(root=self.root, task_id='a' * 32, owner_ref='ref', prompt='x',
                model='free-model', effort='high', expected_branch='task/agy',
                expected_head=self.head, write_paths=['src.py'], log_path=self.log,
                runner=self._runner())
        self.assertEqual(caught.exception.agy_code, 'AGY_OWNER_ASSIGNMENT_REQUIRED')
        self.assertFalse(self.calls)

    def test_write_boundary_refuses_before_runner(self):
        with self.assertRaises(direct.DirectAgyError) as caught:
            self._run(write_boundary=None)
        self.assertEqual(caught.exception.agy_code, 'AGY_WRITE_ISOLATION_UNVERIFIED')
        self.assertFalse(self.calls)

    def test_out_of_scope_write_fails(self):
        runner = self._runner(edit=False)
        def write_outside(argv, cwd, env, timeout):
            self.calls.append((argv, cwd, env, timeout))
            (self.root / 'other.py').write_text('outside\n', encoding='utf-8')
            return 0, json.dumps({'conversation_id': 'conv_12345678',
                                  'status': 'completed'}).encode(), b''
        with self.assertRaises(direct.DirectAgyError) as caught:
            self._run(runner=write_outside)
        self.assertEqual(caught.exception.agy_code, 'AGY_WRITE_SCOPE_VIOLATION')

    def test_invalid_and_duplicate_json_fail(self):
        for raw in (b'not-json', b'{"status":"ok","status":"bad"}'):
            with self.subTest(raw=raw), self.assertRaises(direct.DirectAgyError) as caught:
                self._run(runner=self._runner(output=raw, edit=False))
            self.assertEqual(caught.exception.agy_code, 'AGY_OUTPUT_INVALID')

    def test_quota_records_bounded_progress(self):
        raw = json.dumps({'status': 'error', 'error': 'RESOURCE_EXHAUSTED'}).encode()
        with self.assertRaises(direct.DirectAgyError) as caught:
            self._run(runner=self._runner(output=raw, edit=True))
        self.assertEqual(caught.exception.agy_code, 'AGY_QUOTA_EXHAUSTED')
        self.assertEqual(caught.exception.progress['state'], 'QUOTA_EXHAUSTED')
        self.assertEqual(caught.exception.progress['head_sha'], self.head)
        self.assertEqual(caught.exception.progress['unfinished_checks'], ['UNKNOWN'])

    def test_wrong_head_and_dirty_worktree_refuse(self):
        with self.assertRaises(direct.DirectAgyError) as caught:
            self._run(expected_head='f' * 40)
        self.assertEqual(caught.exception.agy_code, 'AGY_D6_BINDING_INVALID')
        (self.root / 'src.py').write_text('dirty\n', encoding='utf-8')
        with self.assertRaises(direct.DirectAgyError) as caught:
            self._run()
        self.assertEqual(caught.exception.agy_code, 'AGY_D6_DIRTY')

    def test_model_resolution_must_be_present_and_match(self):
        for log_text, expected in (('', 'AGY_MODEL_UNVERIFIED'),
                ('model_resolver: Resolving model other-model\n', 'AGY_MODEL_MISMATCH')):
            with self.subTest(expected=expected), self.assertRaises(direct.DirectAgyError) as caught:
                self._run(runner=self._runner(edit=False, log_text=log_text))
            self.assertEqual(caught.exception.agy_code, expected)

    def test_symlink_and_git_state_mutations_fail(self):
        def make_symlink(argv, cwd, env, timeout):
            (self.root / 'link').symlink_to('src.py')
            self.log.write_text('model_resolver: Resolving model free-model\n', encoding='utf-8')
            return 0, json.dumps({'conversation_id': 'conv_12345678',
                                  'status': 'completed'}).encode(), b''
        with self.assertRaises(direct.DirectAgyError) as caught:
            self._run(runner=make_symlink)
        self.assertEqual(caught.exception.agy_code, 'AGY_WRITE_SCOPE_VIOLATION')

        def make_commit(argv, cwd, env, timeout):
            (self.root / 'src.py').write_text('committed\n', encoding='utf-8')
            self._git('add', 'src.py')
            self._git('commit', '-m', 'runner mutation')
            self.log.write_text('model_resolver: Resolving model free-model\n', encoding='utf-8')
            return 0, json.dumps({'conversation_id': 'conv_12345678',
                                  'status': 'completed'}).encode(), b''
        with self.assertRaises(direct.DirectAgyError) as caught:
            self._run(runner=make_commit)
        self.assertEqual(caught.exception.agy_code, 'AGY_GIT_STATE_CHANGED')

    def test_timeout_nonzero_and_stderr_quota_codes(self):
        def timeout(*args):
            raise TimeoutError()
        with self.assertRaises(direct.DirectAgyError) as caught:
            self._run(runner=timeout)
        self.assertEqual(caught.exception.agy_code, 'AGY_TIMEOUT')
        with self.assertRaises(direct.DirectAgyError) as caught:
            self._run(runner=self._runner(code=4, edit=False))
        self.assertEqual(caught.exception.agy_code, 'AGY_EXIT_NONZERO')
        with self.assertRaises(direct.DirectAgyError) as caught:
            self._run(runner=self._runner(output=b'', stderr=b'rate limit', edit=False))
        self.assertEqual(caught.exception.agy_code, 'AGY_QUOTA_EXHAUSTED')


if __name__ == '__main__':
    unittest.main()
