import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import warnings
from unittest.mock import patch

import llmtool.source_freshness as freshness
from llmtool import task_submission_cli
from llmtool.cli import main
from llmtool.errors import LLMToolError


class SourceFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package_file = self.root / 'tools' / 'llmtool' / 'llmtool' / '__init__.py'
        self.package_file.parent.mkdir(parents=True)
        self.package_file.touch()
        self._git('init', '-q', '-b', 'main')
        self._git('config', 'user.name', 'Test')
        self._git('config', 'user.email', 'test@example.invalid')
        (self.root / 'tracked.txt').write_text('one\n')
        self._git('add', 'tracked.txt', 'tools/llmtool/llmtool/__init__.py')
        self._git('commit', '-q', '-m', 'first')
        self._git('update-ref', 'refs/remotes/origin/main', self._git('rev-parse', 'HEAD'))
        self.package_patch = patch.object(freshness.llmtool, '__file__',
                                          str(self.package_file))
        self.package_patch.start()
        self.addCleanup(self.package_patch.stop)
        self.home_temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.home_temp.cleanup)
        self.home = Path(self.home_temp.name)
        self.allow_patch = patch.dict(os.environ, {'HOME': str(self.home)}, clear=False)
        self.allow_patch.start()
        self.addCleanup(self.allow_patch.stop)
        os.environ.pop('LLMTOOL_ALLOW_STALE_SOURCE', None)

    def _git(self, *argv):
        result = subprocess.run(['git', *argv], cwd=self.root, check=True,
                                capture_output=True, text=True)
        return result.stdout.strip()

    def _stale(self):
        (self.root / 'tracked.txt').write_text('two\n')
        self._git('commit', '-q', '-am', 'second')

    def _pin_data(self, **updates):
        value = {
            'schema_version': 1,
            'approved_sha': self._git('rev-parse', 'HEAD'),
            'previous_sha': None,
            'approved_at': 0,
        }
        value.update(updates)
        return value

    def _pin_path(self):
        parent = self.home / '.local' / 'state' / 'llmtool'
        parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        parent.chmod(0o700)
        return parent / 'approved-install.json'

    def _write_pin(self, value=None, raw=None):
        path = self._pin_path()
        data = raw if raw is not None else json.dumps(value or self._pin_data()).encode()
        path.write_bytes(data)
        path.chmod(0o600)
        return path

    def _make_behind_pin(self):
        behind = self._git('rev-parse', 'HEAD')
        self._stale()
        approved = self._git('rev-parse', 'HEAD')
        self._git('reset', '--hard', behind)
        self._write_pin(self._pin_data(approved_sha=approved))

    def test_ok(self):
        self.assertEqual(freshness.source_freshness(), 'OK')
        self.assertEqual(freshness.require_fresh_source(), 'OK')

    def test_stale(self):
        self._stale()
        self.assertEqual(freshness.source_freshness(), 'STALE')
        with self.assertRaises(LLMToolError) as caught:
            freshness.require_fresh_source()
        self.assertEqual(caught.exception.completion_code, 'LLMTOOL_SOURCE_STALE')

    def test_fetch_updates_tracking_ref_from_local_bare_origin(self):
        support = tempfile.TemporaryDirectory()
        self.addCleanup(support.cleanup)
        support_root = Path(support.name)
        bare = support_root / 'origin.git'
        peer = support_root / 'peer'
        subprocess.run(['git', 'init', '--bare', '-q', str(bare)], check=True,
                       capture_output=True, text=True)
        self._git('remote', 'add', 'origin', str(bare))
        self._git('push', '-q', 'origin', 'main')
        self._git('fetch', '-q', 'origin', 'main')

        subprocess.run(['git', 'clone', '-q', '--branch', 'main', str(bare), str(peer)],
                       check=True, capture_output=True, text=True)
        subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=peer, check=True)
        subprocess.run(['git', 'config', 'user.email', 'test@example.invalid'],
                       cwd=peer, check=True)
        (peer / 'tracked.txt').write_text('remote update\n')
        subprocess.run(['git', 'commit', '-q', '-am', 'remote update'], cwd=peer,
                       check=True, capture_output=True, text=True)
        subprocess.run(['git', 'push', '-q', 'origin', 'main'], cwd=peer, check=True,
                       capture_output=True, text=True)

        self.assertEqual(freshness.source_freshness(), 'STALE')

    def test_fetch_timeout_falls_back_to_existing_tracking_ref(self):
        self._stale()
        original_popen = subprocess.Popen

        class TimedOutFetch:
            pid = 12345
            timed_out = False

            def communicate(self, timeout=None):
                if not self.timed_out:
                    self.timed_out = True
                    raise subprocess.TimeoutExpired(['git', 'fetch'], timeout)
                return '', ''

        def popen_with_fetch_timeout(argv, *args, **kwargs):
            if argv[:2] == ['git', 'fetch']:
                return TimedOutFetch()
            return original_popen(argv, *args, **kwargs)

        with patch.object(freshness.subprocess, 'Popen', side_effect=popen_with_fetch_timeout), \
                patch.object(freshness.os, 'killpg', side_effect=ProcessLookupError):
            self.assertEqual(freshness.source_freshness(), 'STALE')

    def test_fetch_timeout_kills_child_holding_stdout(self):
        self._stale()
        original_popen = subprocess.Popen

        def popen_with_fake_git(argv, *args, **kwargs):
            if argv[:2] == ['git', 'fetch']:
                # The child inherits the captured stdout pipe and outlives fake git.
                code = ('import subprocess, time; '
                        'subprocess.Popen(["/bin/sleep", "30"]); time.sleep(30)')
                return original_popen([sys.executable, '-c', code], *args, **kwargs)
            return original_popen(argv, *args, **kwargs)

        started = time.monotonic()
        with patch.object(freshness.subprocess, 'Popen', side_effect=popen_with_fake_git):
            self.assertEqual(freshness.source_freshness(), 'STALE')
        self.assertLess(time.monotonic() - started, freshness._TIMEOUT_SECONDS + 2)

    def test_dirty(self):
        (self.root / 'tracked.txt').write_text('dirty\n')
        self.assertEqual(freshness.source_freshness(), 'DIRTY')
        with self.assertRaises(LLMToolError) as caught:
            freshness.require_fresh_source()
        self.assertEqual(caught.exception.completion_code, 'LLMTOOL_SOURCE_DIRTY')

    def test_unknown(self):
        self.package_patch.stop()
        self.package_patch = patch.object(freshness.llmtool, '__file__',
                                          str(Path(self.temp.name) / 'not-a-repo' /
                                              'tools/llmtool/llmtool/__init__.py'))
        self.package_patch.start()
        self.assertEqual(freshness.source_freshness(), 'UNKNOWN')
        with self.assertRaises(LLMToolError) as caught:
            freshness.require_fresh_source()
        self.assertEqual(caught.exception.completion_code, 'LLMTOOL_SOURCE_UNKNOWN')

    def test_override_warns_and_returns_state(self):
        self._stale()
        os.environ['LLMTOOL_ALLOW_STALE_SOURCE'] = '1'
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            self.assertEqual(freshness.require_fresh_source(), 'STALE')
        self.assertTrue(caught)
        self.assertIn('LLMTOOL_SOURCE_STALE', str(caught[0].message))

    def test_pin_equal_head_ignores_ahead_origin_without_fetch(self):
        self._stale()
        ahead = self._git('rev-parse', 'HEAD')
        self._git('reset', '--hard', 'HEAD^')
        self._git('update-ref', 'refs/remotes/origin/main', ahead)
        self._write_pin(self._pin_data())
        original_popen = subprocess.Popen
        fetch_calls = []

        def record_fetch(argv, *args, **kwargs):
            if argv[:2] == ['git', 'fetch']:
                fetch_calls.append(argv)
            return original_popen(argv, *args, **kwargs)

        with patch.object(freshness.subprocess, 'Popen', side_effect=record_fetch):
            self.assertEqual(freshness.source_freshness(), 'OK')
        self.assertEqual(fetch_calls, [])

    def test_pin_head_ahead_is_unapproved_and_has_completion_code(self):
        approved = self._git('rev-parse', 'HEAD')
        self._write_pin(self._pin_data(approved_sha=approved))
        self._stale()
        self.assertEqual(freshness.source_freshness(), 'UNAPPROVED')
        with self.assertRaises(LLMToolError) as caught:
            freshness.require_fresh_source()
        self.assertEqual(caught.exception.completion_code, 'LLMTOOL_SOURCE_UNAPPROVED')

    def test_pin_head_behind_is_stale(self):
        self._make_behind_pin()
        self.assertEqual(freshness.source_freshness(), 'STALE')

    def test_pin_diverged_is_stale(self):
        self._git('checkout', '-q', '-b', 'approved')
        (self.root / 'approved.txt').write_text('approved\n')
        self._git('add', 'approved.txt')
        self._git('commit', '-q', '-m', 'approved side')
        approved = self._git('rev-parse', 'HEAD')
        self._git('checkout', '-q', 'main')
        (self.root / 'current.txt').write_text('current\n')
        self._git('add', 'current.txt')
        self._git('commit', '-q', '-m', 'current side')
        self.assertNotEqual(self._git('merge-base', approved, 'HEAD'), approved)
        self.assertNotEqual(self._git('merge-base', approved, 'HEAD'), self._git('rev-parse', 'HEAD'))
        self._write_pin(self._pin_data(approved_sha=approved))
        self.assertEqual(freshness.source_freshness(), 'STALE')

    def test_pin_unknown_sha_is_unknown(self):
        self._write_pin(self._pin_data(approved_sha='a' * 40))
        self.assertEqual(freshness.source_freshness(), 'UNKNOWN')

    def test_dirty_tree_with_pin_is_dirty(self):
        self._write_pin(self._pin_data())
        (self.root / 'tracked.txt').write_text('dirty\n')
        self.assertEqual(freshness.source_freshness(), 'DIRTY')

    def test_override_warns_and_returns_unapproved(self):
        approved = self._git('rev-parse', 'HEAD')
        self._write_pin(self._pin_data(approved_sha=approved))
        self._stale()
        os.environ['LLMTOOL_ALLOW_STALE_SOURCE'] = '1'
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            self.assertEqual(freshness.require_fresh_source(), 'UNAPPROVED')
        self.assertTrue(caught)
        self.assertIn('LLMTOOL_SOURCE_UNAPPROVED', str(caught[0].message))

    def test_no_pin_keeps_origin_main_result(self):
        self._stale()
        self.assertFalse(freshness.pin_path().exists())
        self.assertEqual(freshness.source_freshness(), 'STALE')

    def test_invalid_pin_table_returns_unknown(self):
        valid = json.dumps(self._pin_data()).encode()

        def symlink_valid(path):
            target = path.parent / 'target.json'
            target.write_bytes(valid)
            target.chmod(0o600)
            path.symlink_to(target)

        def dangling_symlink(path):
            path.symlink_to(path.parent / 'missing.json')

        def hard_link(path):
            source = path.parent / 'target.json'
            source.write_bytes(valid)
            source.chmod(0o600)
            os.link(source, path)

        def directory(path):
            path.mkdir(mode=0o700)

        def writable_parent(path):
            path.parent.chmod(0o720)
            path.write_bytes(valid)
            path.chmod(0o600)

        def oversized(path):
            path.write_bytes(b' ' * 4097)
            path.chmod(0o600)

        rows = [
            ('mode 644', lambda p: (p.write_bytes(valid), p.chmod(0o644))),
            ('mode 640', lambda p: (p.write_bytes(valid), p.chmod(0o640))),
            ('symlink', symlink_valid),
            ('dangling symlink', dangling_symlink),
            ('hard link', hard_link),
            ('directory', directory),
            ('group writable parent', writable_parent),
            ('oversize', oversized),
            ('bad JSON', lambda p: self._write_raw(p, b'{')),
            ('duplicate keys', lambda p: self._write_raw(p, b'{"schema_version":1,"schema_version":1}')),
            ('extra field', lambda p: self._write_pin(self._pin_data(extra='x'))),
            ('missing field', lambda p: self._write_pin({k: v for k, v in self._pin_data().items() if k != 'approved_at'})),
            ('boolean schema', lambda p: self._write_pin(self._pin_data(schema_version=True))),
            ('wrong schema', lambda p: self._write_pin(self._pin_data(schema_version=2))),
            ('uppercase SHA', lambda p: self._write_pin(self._pin_data(approved_sha='A' * 40))),
            ('short SHA', lambda p: self._write_pin(self._pin_data(approved_sha='a' * 39))),
            ('bad previous SHA', lambda p: self._write_pin(self._pin_data(previous_sha='z' * 40))),
            ('boolean timestamp', lambda p: self._write_pin(self._pin_data(approved_at=True))),
            ('negative timestamp', lambda p: self._write_pin(self._pin_data(approved_at=-1))),
        ]
        for label, make_invalid in rows:
            with self.subTest(pin=label):
                home_temp = tempfile.TemporaryDirectory()
                self.addCleanup(home_temp.cleanup)
                self.home = Path(home_temp.name)
                with patch.dict(os.environ, {'HOME': str(self.home)}, clear=False):
                    path = self._pin_path()
                    make_invalid(path)
                    self.assertEqual(freshness.source_freshness(), 'UNKNOWN')

    @staticmethod
    def _write_raw(path, data):
        path.write_bytes(data)
        path.chmod(0o600)

    def test_cli_refuses_before_handlers_when_source_is_stale(self):
        cases = (
            (['task', 'delegate', 't', '--provider', 'codex',
              '--selection-source', 'PM', '--executor-task', 'e',
              '--read-path', 'a.txt', '--write-path', 'a.txt',
              '--acceptance', 'done', '--egress-basis', 'fixture'],
             'run_task_delegate'),
            (['task', 'remote-test', 't', '--executor-task', 'e', '--sha', 's',
              '--ref', 'r'], 'run_task_remote_test'),
            (['task', 'review-dispatch', 't', '--executor-task', 'e', '--pr', '1'],
             'run_task_review_dispatch'),
        )
        for argv, handler_name in cases:
            with self.subTest(handler=handler_name), \
                    patch.object(freshness, 'source_freshness', return_value='STALE'), \
                    patch(f'llmtool.task_submission_cli.{handler_name}') as handler:
                self.assertEqual(main(argv), 1)
                handler.assert_not_called()

    def test_assignment_scope_errors_have_stable_codes_without_paths(self):
        cases = (
            ((['a.txt'], ['b.txt']), 'WRITE_PATH_NOT_IN_READ_PATHS'),
            ((['../secret'], ['../secret']), 'PATH_OUTSIDE_REPO'),
            ((['a.txt', 'a.txt'], ['a.txt']), 'DUPLICATE_PATH'),
        )
        for paths, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(LLMToolError) as caught:
                    task_submission_cli._assignment_paths(*paths)
                self.assertIn(code, str(caught.exception))
                self.assertNotIn('../secret', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
