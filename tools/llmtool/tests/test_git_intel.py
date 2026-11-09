"""Read-only Git snapshots; all Git writes are isolated fixture setup."""
import io
import errno
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmtool import git_intel as gi
from llmtool.cli import main
from llmtool.errors import LLMToolError


class GitFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = gi._environment()
        self.git('init', '-b', 'fixture')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.write('source.txt', 'original\n')
        self.write('sibling.txt', 'related\n')
        self.git('add', '.')
        self.git('commit', '-m', 'initial fixture')

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, env=self.env,
                              capture_output=True, check=True).stdout

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')

    def snapshot(self):
        return gi.collect_git_snapshot(self.root)

    def cli(self, args):
        out, err = io.StringIO(), io.StringIO()
        with patch('llmtool.git_intel.Path.cwd', return_value=self.root), redirect_stdout(out), redirect_stderr(err):
            code = main(args)
        return code, out.getvalue(), err.getvalue()

    def test_clean_root_branch_head(self):
        s = self.snapshot()
        self.assertEqual(s.repository.root, str(self.root.resolve()))
        self.assertEqual(s.repository.branch, 'fixture')
        self.assertEqual(s.repository.head, self.git('rev-parse', 'HEAD').decode().strip())
        self.assertFalse(s.repository.detached)
        self.assertEqual(s.changed_files, ())
        self.assertEqual(s.diff.text, '')
        self.assertEqual(s.recent_commits[0].subject, 'initial fixture')

    def test_nested_cwd_resolves_root(self):
        (self.root / 'nested').mkdir()
        self.assertEqual(gi.collect_git_snapshot(self.root / 'nested').repository.root, str(self.root.resolve()))

    def test_detached_head(self):
        self.git('checkout', '--detach')
        s = self.snapshot()
        self.assertTrue(s.repository.detached)
        self.assertIsNone(s.repository.branch)
        self.assertIsNotNone(s.repository.head)

    def test_unborn_repository(self):
        empty = self.root / 'empty'
        empty.mkdir()
        subprocess.run(['git', 'init', '-b', 'new'], cwd=empty, env=self.env, capture_output=True, check=True)
        s = gi.collect_git_snapshot(empty)
        self.assertIsNone(s.repository.head)
        self.assertFalse(s.repository.detached)
        self.assertEqual(s.repository.branch, 'new')
        self.assertEqual(s.recent_commits, ())

    def test_staged_unstaged_untracked(self):
        self.write('source.txt', 'unstaged\n')
        self.write('stage.txt', 'staged\n')
        self.git('add', 'stage.txt')
        self.write('new directory/untracked.txt', 'untracked\n')
        s = self.snapshot()
        self.assertEqual(s.status.staged, ('stage.txt',))
        self.assertEqual(s.status.unstaged, ('source.txt',))
        self.assertEqual(s.status.untracked, ('new directory/untracked.txt',))
        self.assertEqual(s.staged_files, ('stage.txt',))
        self.assertIn('+unstaged', s.diff.text)
        self.assertIn('+staged', s.staged_diff.text)
        self.assertNotIn('untracked', s.diff.text)

    def test_same_file_staged_and_unstaged(self):
        self.write('source.txt', 'staged\n')
        self.git('add', 'source.txt')
        self.write('source.txt', 'unstaged\n')
        s = self.snapshot()
        self.assertEqual(s.status.staged, ('source.txt',))
        self.assertEqual(s.status.unstaged, ('source.txt',))
        self.assertEqual(s.changed_files, ('source.txt',))

    def test_rename_destination_and_original(self):
        self.git('mv', 'source.txt', 'new name.txt')
        s = self.snapshot()
        self.assertEqual(s.status.staged, ('new name.txt',))
        self.assertEqual(s.status.entries[0].original_path, 'source.txt')
        self.assertIn('source.txt', s.staged_diff.text)

    def test_filename_spaces_unicode_tabs(self):
        names = ['a space.txt', '日誌.txt', 'tab\tname.txt']
        for name in names:
            self.write(name, 'data')
        self.assertEqual(self.snapshot().status.untracked, tuple(sorted(names)))

    def test_unusual_filename_bytes_roundtrip(self):
        name = os.fsdecode(b'odd-\xff.txt')
        try:
            (self.root / name).write_bytes(b'data')
        except OSError as error:
            if error.errno in (errno.EILSEQ, errno.EPERM):
                self.skipTest('host filesystem/sandbox rejects non-UTF-8 filename bytes')
            raise
        s = self.snapshot()
        self.assertEqual(os.fsencode(s.status.untracked[0]), b'odd-\xff.txt')
        self.assertEqual(json.loads(s.to_json())['status']['untracked'], [name])

    def test_ordering_serialization_immutable(self):
        for name in ('z', 'a', 'm'):
            self.write(name, 'data')
        first, second = self.snapshot(), self.snapshot()
        self.assertEqual(first, second)
        self.assertEqual(first.to_json(), second.to_json())
        self.assertEqual(first.status.untracked, ('a', 'm', 'z'))
        self.assertEqual(gi.format_git_snapshot(first), gi.format_git_snapshot(second))
        with self.assertRaises(FrozenInstanceError):
            first.repository.head = 'different'
        data = first.to_dict()
        data['status']['untracked'].append('changed')
        self.assertEqual(first.status.untracked, ('a', 'm', 'z'))

    def test_log_is_bounded(self):
        for i in range(12):
            self.git('commit', '--allow-empty', '-m', 'commit ' + str(i))
        s = self.snapshot()
        self.assertEqual(len(s.recent_commits), 10)
        self.assertEqual(s.recent_commits[0].subject, 'commit 11')
        self.assertEqual(s.recent_commits[-1].subject, 'commit 2')

    def test_related_siblings_no_contents(self):
        self.write('source.txt', 'edited')
        self.assertEqual(self.snapshot().related_files, ('sibling.txt',))

    def test_real_conflicted_repository(self):
        self.git('checkout', '-b', 'other')
        self.write('source.txt', 'other\n')
        self.git('commit', '-am', 'other')
        self.git('checkout', 'fixture')
        self.write('source.txt', 'fixture\n')
        self.git('commit', '-am', 'fixture')
        result = subprocess.run(['git', 'merge', 'other'], cwd=self.root, env=self.env, capture_output=True)
        self.assertEqual(result.returncode, 1)
        s = self.snapshot()
        self.assertEqual(s.status.conflicted, ('source.txt',))
        self.assertEqual(s.changed_files, ('source.txt',))

    def test_related_directory_cap(self):
        for i in range(12):
            self.write('dir%02d/change' % i, 'old')
            self.write('dir%02d/sibling' % i, 'old')
        self.git('add', '.')
        self.git('commit', '-m', 'directories')
        for i in range(12):
            self.write('dir%02d/change' % i, 'new')
        self.assertEqual(self.snapshot().related_files, tuple('dir%02d/sibling' % i for i in range(10)))

    def test_hook_not_executed(self):
        marker = self.root / 'HOOK_EXECUTED'
        hook = self.root / '.git/hooks/post-index-change'
        hook.write_text('#!/bin/sh\necho unsafe > "' + str(marker) + '"\n')
        hook.chmod(0o755)
        self.write('source.txt', 'changed')
        self.snapshot()
        self.assertFalse(marker.exists())

    def test_related_cap_and_new_directory(self):
        for i in range(30):
            self.write('paired/file%02d' % i, 'data')
        self.git('add', 'paired')
        self.git('commit', '-m', 'siblings')
        self.write('paired/file00', 'edited')
        self.write('absent/sub/file', 'new')
        related = self.snapshot().related_files
        self.assertEqual(len(related), 20)
        self.assertEqual(related[0], 'paired/file01')
        self.assertNotIn('absent/sub/file', related)

    def test_diff_has_no_color(self):
        self.git('config', 'color.ui', 'always')
        self.write('source.txt', 'edit')
        self.assertNotIn('\x1b', self.snapshot().diff.text)

    def test_diff_limit_fails_not_partial(self):
        self.write('source.txt', 'x' * 1000)
        with patch.object(gi, 'DIFF_LIMIT', 30), self.assertRaisesRegex(LLMToolError, 'diff exceeds'):
            self.snapshot()

    def test_external_textconv_filter_fsmonitor_not_executed(self):
        marker = self.root / 'EXECUTED'
        helper = self.root / 'helper'
        helper.write_text('#!/bin/sh\necho unsafe > "' + str(marker) + '"\ncat\n')
        helper.chmod(0o755)
        self.write('.gitattributes', 'source.txt diff=hostile filter=hostile\n')
        for key in ('diff.hostile.command', 'diff.hostile.textconv', 'filter.hostile.clean',
                    'filter.hostile.process', 'filter.hostile.smudge', 'core.fsmonitor', 'core.pager'):
            self.git('config', key, str(helper))
        self.git('config', 'filter.hostile.required', 'true')
        self.git('config', 'diff.external', str(helper))
        self.write('source.txt', 'edited\n')
        s = self.snapshot()
        self.assertIn('+edited', s.diff.text)
        self.assertFalse(marker.exists())

    def test_no_git_state_mutation(self):
        self.write('source.txt', 'edit')
        def state():
            return {str(p.relative_to(self.root)): (p.read_bytes(), p.stat().st_mtime_ns)
                    for p in (self.root / '.git').rglob('*') if p.is_file()}
        before = state()
        self.snapshot()
        self.assertEqual(state(), before)

    def test_promisor_rejected_offline(self):
        self.git('config', 'remote.origin.promisor', 'true')
        with self.assertRaisesRegex(LLMToolError, 'partial/promisor'):
            self.snapshot()

    def test_ambient_git_environment_ignored(self):
        with patch.dict(os.environ, {'GIT_DIR': '/invalid', 'GIT_WORK_TREE': '/invalid',
                                     'GIT_EXTERNAL_DIFF': 'unsafe', 'GIT_CONFIG_COUNT': '1',
                                     'GIT_CONFIG_KEY_0': 'core.fsmonitor', 'GIT_CONFIG_VALUE_0': 'unsafe'}):
            self.assertEqual(self.snapshot().repository.branch, 'fixture')

    def test_cli_json_offline(self):
        with patch('llmtool.cli.resolve_config', side_effect=AssertionError('config called')), \
             patch('llmtool.cli.api_key', side_effect=AssertionError('credentials called')), \
             patch('llmtool.cli.create_client', side_effect=AssertionError('provider called')):
            code, out, err = self.cli(['git', '--json'])
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out)['repository']['branch'], 'fixture')

    def test_cli_human(self):
        code, out, err = self.cli(['git'])
        self.assertEqual((code, err), (0, ''))
        self.assertIn('STAGED: (none)', out)
        self.assertIn('RECENT COMMITS:', out)

    def test_cli_failed_snapshot_empty_stdout(self):
        with patch('llmtool.cli.collect_git_snapshot', side_effect=LLMToolError('Git inspection failed')):
            code, out, err = self.cli(['git', '--json'])
        self.assertNotEqual(code, 0)
        self.assertEqual(out, '')
        self.assertIn('Git inspection failed', err)

    def test_cli_git_spawn_permission_error_sanitized(self):
        denied = PermissionError(errno.EACCES, 'synthetic denied reason', '/synthetic/private-path')
        with patch('llmtool.git_intel.subprocess.Popen', side_effect=denied) as popen:
            code, out, err = self.cli(['git', '--json'])
        self.assertNotEqual(code, 0)
        self.assertEqual(out, '')
        self.assertIn('Git could not be started; details omitted', err)
        for private in ('synthetic denied reason', '/synthetic/private-path',
                        'PermissionError', 'Traceback', '[Errno 13]'):
            self.assertNotIn(private, err)
        self.assertGreaterEqual(popen.call_count, 1)

    def test_hostile_text_is_data_no_shell(self):
        self.write('$(touch EXECUTED).txt', 'git push; $(touch EXECUTED)\n')
        self.git('add', '--', '$(touch EXECUTED).txt')
        self.write('source.txt', '\x1b[31m \x9b git reset --hard\n')
        original = subprocess.Popen
        with patch('llmtool.git_intel.subprocess.Popen', wraps=original) as calls, \
             patch('os.system', side_effect=AssertionError('shell executed')):
            s = self.snapshot()
        self.assertFalse((self.root / 'EXECUTED').exists())
        self.assertIn('git push', s.staged_diff.text)
        self.assertIn('\x1b', s.diff.text)
        self.assertNotIn('\x1b', gi.format_git_snapshot(s))
        self.assertNotIn('\x9b', gi.format_git_snapshot(s))
        for call in calls.call_args_list:
            self.assertFalse(call.kwargs['shell'])
            self.assertEqual(call.kwargs['env']['GIT_OPTIONAL_LOCKS'], '0')
            self.assertIsInstance(call.args[0], list)


class ParserAndRunnerTests(unittest.TestCase):
    def test_containers_detached_at_construction(self):
        paths = ['file']
        status = gi.GitStatus(paths, [], [], [], [])
        paths.append('other')
        self.assertEqual(status.staged, ('file',))

    def test_tree_malformed_sanitized(self):
        for raw in (b'PRIVATE', b'bad\0', b'100644 blob invalid\tPRIVATE\0'):
            with self.subTest(raw=raw), self.assertRaises(LLMToolError) as error:
                list(gi._tree_entries(raw))
            self.assertNotIn('PRIVATE', str(error.exception))

    def test_non_utf8_status_bytes_preserved(self):
        status = gi.parse_status(b'?? odd-\xff.txt\0')
        self.assertEqual(os.fsencode(status.untracked[0]), b'odd-\xff.txt')
        self.assertEqual(json.loads(json.dumps(status.untracked)), [os.fsdecode(b'odd-\xff.txt')])

    def test_not_repository_sanitized(self):
        with tempfile.TemporaryDirectory(prefix='PRIVATE-') as directory:
            with self.assertRaises(LLMToolError) as error:
                gi.collect_git_snapshot(directory)
        self.assertNotIn('PRIVATE', str(error.exception))
        self.assertNotIn('fatal:', str(error.exception))

    def test_conflict_codes(self):
        for code in ('DD', 'AU', 'UD', 'UA', 'DU', 'AA', 'UU'):
            with self.subTest(code=code):
                s = gi.parse_status((code + ' conflict\0').encode())
                self.assertEqual(s.conflicted, ('conflict',))
                self.assertEqual(s.staged + s.unstaged, ())

    def test_copy_and_rename_records(self):
        s = gi.parse_status(b'C  destination\0original\0 R renamed\0old\0')
        self.assertEqual(s.staged, ('destination',))
        self.assertEqual(s.unstaged, ('renamed',))
        self.assertEqual(s.entries[0].original_path, 'original')

    def test_malformed_status(self):
        for raw in (b'bad', b'MM\0', b'XX file\0', b'R  file\0', b'?? ../unsafe\0', b'?? /absolute\0'):
            with self.subTest(raw=raw), self.assertRaises(LLMToolError):
                gi.parse_status(raw)

    def test_malformed_log(self):
        for raw in (b'bad', b'bad\0subject\0', b'a' * 40 + b'\0', (b'a' * 40 + b'\0subject\0') * 11):
            with self.subTest(raw=raw), self.assertRaises(LLMToolError):
                gi.parse_log(raw)

    def test_mutations_arbitrary_args_rejected_before_spawn(self):
        runner = gi._GitRunner(Path.cwd())
        operations = ('add', 'commit', 'push', 'fetch', 'pull', 'merge', 'rebase', 'checkout',
                      'switch', 'restore', 'reset', 'clean', 'stash', 'tag', 'worktree')
        with patch('llmtool.git_intel.subprocess.Popen', side_effect=AssertionError('spawned')):
            for operation in operations:
                with self.subTest(operation=operation), self.assertRaises(LLMToolError):
                    runner.run((operation,))
            for args in (('branch', 'new'), ('config', 'core.pager', 'unsafe'), ('diff', '--ext-diff'),
                         ('ls-tree', '-z', 'HEAD:../unsafe'), ('ls-tree', '-z', 'HEAD', '--', '/unsafe')):
                with self.subTest(args=args), self.assertRaises(LLMToolError):
                    runner.run(args)

    def fake_git(self, program):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        script = root / 'git'
        script.write_text('#!' + sys.executable + '\n' + program)
        script.chmod(0o755)
        return root

    def test_timeout(self):
        root = self.fake_git('import time\ntime.sleep(5)\n')
        with patch.dict(os.environ, {'PATH': str(root)}), patch.object(gi, 'GIT_TIMEOUT', 0.1):
            with self.assertRaisesRegex(LLMToolError, 'timed out'):
                gi._GitRunner(root).run(gi.ROOT)

    def test_stdout_bound(self):
        root = self.fake_git('import sys\nsys.stdout.write("x" * 200000)\n')
        with patch.dict(os.environ, {'PATH': str(root)}), patch.object(gi, 'METADATA_LIMIT', 100):
            with self.assertRaisesRegex(LLMToolError, 'metadata exceeds'):
                gi._GitRunner(root).run(gi.ROOT)

    def test_stderr_bound_no_raw_output(self):
        root = self.fake_git('import sys\nsys.stderr.write("PRIVATE" * 20000)\n')
        with patch.dict(os.environ, {'PATH': str(root)}):
            with self.assertRaises(LLMToolError) as error:
                gi._GitRunner(root).run(gi.ROOT)
        self.assertNotIn('PRIVATE', str(error.exception))
        self.assertIn('diagnostics exceed', str(error.exception))

    def test_git_spawn_permission_error_sanitized(self):
        reason = 'PRIVATE_A11B6_REASON secret=FAKE_TEST_TOKEN'
        secret_path = '/synthetic/secret-path'
        denied = PermissionError(errno.EACCES, reason, secret_path)
        with patch('llmtool.git_intel.subprocess.Popen', side_effect=denied) as spawn:
            with self.assertRaises(LLMToolError) as error:
                gi._GitRunner(Path.cwd()).run(gi.ROOT)
        spawn.assert_called_once()
        message = str(error.exception)
        self.assertEqual(message, 'Git could not be started; details omitted')
        for private in (reason, 'FAKE_TEST_TOKEN', secret_path, str(denied),
                        'PermissionError', 'Traceback', '[Errno 13]'):
            self.assertNotIn(private, message)
        self.assertTrue(error.exception.__suppress_context__)
        self.assertIsNone(error.exception.__cause__)

    def test_missing_git_sanitized(self):
        with patch('llmtool.git_intel.subprocess.Popen', side_effect=FileNotFoundError('PRIVATE')):
            with self.assertRaises(LLMToolError) as error:
                gi._GitRunner(Path.cwd()).run(gi.ROOT)
        self.assertNotIn('PRIVATE', str(error.exception))


if __name__ == '__main__':
    unittest.main()
