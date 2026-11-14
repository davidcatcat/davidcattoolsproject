"""Tests for llmtool.file_ops (#1502): temporary git repositories only, no network."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from llmtool import file_ops  # noqa: E402
from llmtool.errors import LLMToolError  # noqa: E402

GIT = shutil.which('git')
FORBIDDEN_VERBS = {'commit', 'push', 'reset', 'clean', 'checkout', 'restore', 'stash', 'switch'}


def _fixture_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update({
        'GIT_CONFIG_GLOBAL': os.devnull,
        'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_AUTHOR_NAME': 'File Ops Test',
        'GIT_AUTHOR_EMAIL': 'file-ops@example.invalid',
        'GIT_COMMITTER_NAME': 'File Ops Test',
        'GIT_COMMITTER_EMAIL': 'file-ops@example.invalid',
    })
    return env


def git(root, *args):
    return subprocess.run(['git', *args], cwd=root, env=_fixture_env(), check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout


def _git_verb(argv):
    """First git subcommand in an argv, skipping ``-c key=value`` options."""
    rest = list(argv[1:])
    while rest:
        item = rest.pop(0)
        if item == '-c':
            rest.pop(0)
        elif not item.startswith('-'):
            return item
    return None


class ParseOpsTests(unittest.TestCase):
    def assertCode(self, code, value):
        with self.assertRaises(LLMToolError) as ctx:
            file_ops.parse_ops(value)
        self.assertEqual(code, ctx.exception.code)

    def test_valid_json_text_and_list(self):
        text = '[{"op": "delete", "path": "a.txt"}, {"op": "move", "from": "d/", "to": "e/"}]'
        expected = [{'op': 'delete', 'path': 'a.txt'}, {'op': 'move', 'from': 'd/', 'to': 'e/'}]
        self.assertEqual(expected, file_ops.parse_ops(text))
        self.assertEqual(expected, file_ops.parse_ops(expected))
        self.assertEqual(expected, file_ops.parse_ops(file_ops.parse_ops(expected)))

    def test_shape_refused(self):
        bad = [
            'not json', '{"op": "delete"}', [], 'null',
            [{'op': 'delete', 'path': 'a'}] * 17,
            [{'op': 'delete', 'path': 'a', 'extra': 'x'}],
            [{'op': 'delete'}],
            [{'op': 'move', 'from': 'a'}],
            [{'op': 'copy', 'path': 'a'}],
            ['a.txt'],
            [{'op': 'move', 'from': 'a/', 'to': 'b'}],
            [{'op': 'move', 'from': 'a', 'to': 'a'}],
            [{'op': 'move', 'from': 'a/', 'to': 'a/b/'}],
        ]
        for value in bad:
            with self.subTest(value=str(value)[:60]):
                self.assertCode('FILE_OPS_INVALID', value)

    def test_sixteen_ops_allowed(self):
        ops = [{'op': 'delete', 'path': f'f{i}.txt'} for i in range(16)]
        self.assertEqual(16, len(file_ops.parse_ops(ops)))

    def test_bad_paths_refused(self):
        bad = ['', '/abs.txt', '/', 'a\\b.txt', '.', '..', 'a/./b', 'a/../b', '../up',
               'a//b', 'a/b//', '.git', '.git/', '.git/config', 'x/.git/y', '.GIT/HEAD',
               'café.txt', 'tab\there', 'a' * 513, 7, None]
        for path in bad:
            with self.subTest(path=repr(path)[:40]):
                self.assertCode('FILE_OPS_PATH_INVALID', [{'op': 'delete', 'path': path}])
                self.assertCode('FILE_OPS_PATH_INVALID', [{'op': 'move', 'from': 'ok.txt', 'to': path}])

    def test_long_and_dotted_names_allowed(self):
        ok = ['a' * 512, '.gitignore', 'dir/.github/x.yml', 'a b/c-d_e.txt', 'dir/']
        for path in ok:
            self.assertEqual(path, file_ops.parse_ops([{'op': 'delete', 'path': path}])[0]['path'])


@unittest.skipUnless(GIT, 'git is required')
class _RepoCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = os.path.realpath(tmp.name)
        self.root = os.path.join(base, 'repo')
        self.outside = os.path.join(base, 'outside')
        os.makedirs(self.root)
        os.makedirs(self.outside)
        git(self.root, 'init', '-q')
        self.write('README.txt', 'readme\n')
        self.write('docs/a.txt', 'alpha\n')
        self.write('docs/b.txt', 'bravo\n')
        self.write('docs/sub/c.txt', 'charlie\n')
        self.write('src/tool.py', 'print(1)\n')
        self.head = self.commit()

    def write(self, rel, text):
        path = os.path.join(self.root, *rel.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(text)

    def commit(self):
        git(self.root, 'add', '-A')
        git(self.root, '-c', 'commit.gpgsign=false', 'commit', '-q', '-m', 'fixture')
        return self.rev('HEAD')

    def rev(self, spec):
        return git(self.root, 'rev-parse', spec).strip()

    def status(self):
        return git(self.root, 'status', '--porcelain=v1', '--untracked-files=all', '--no-renames')

    def exists(self, rel):
        return os.path.lexists(os.path.join(self.root, *rel.split('/')))

    def make_plan(self, ops):
        return file_ops.plan(self.root, self.head, ops)

    def assertCode(self, code, fn, *args):
        with self.assertRaises(LLMToolError) as ctx:
            fn(*args)
        self.assertEqual(code, ctx.exception.code)
        return ctx.exception

    def assertUnchanged(self):
        self.assertEqual('', self.status())
        self.assertEqual(self.head, self.rev('HEAD'))
        for rel in ('README.txt', 'docs/a.txt', 'docs/b.txt', 'docs/sub/c.txt', 'src/tool.py'):
            self.assertTrue(self.exists(rel), rel)


class PlanTests(_RepoCase):
    def test_plan_is_read_only_and_expands_directories(self):
        p = self.make_plan([{'op': 'delete', 'path': 'docs/'}])
        self.assertEqual(['docs/a.txt', 'docs/b.txt', 'docs/sub/c.txt'],
                         [f['path'] for f in p['files']])
        self.assertEqual(self.rev('HEAD:docs/a.txt'), p['files'][0]['blob'])
        self.assertUnchanged()

    def test_plan_digest_is_sha256_of_canonical_plan(self):
        p = self.make_plan([{'op': 'move', 'from': 'README.txt', 'to': 'doc/README.txt'}])
        canonical = json.dumps({'head': p['head'], 'ops': p['ops'], 'files': p['files']},
                               sort_keys=True, separators=(',', ':'))
        self.assertEqual(hashlib.sha256(canonical.encode('ascii')).hexdigest(), p['sha256'])

    def test_head_mismatch_refused(self):
        self.assertCode('FILE_OPS_HEAD_MISMATCH', file_ops.plan, self.root, '0' * 40,
                        [{'op': 'delete', 'path': 'README.txt'}])
        self.assertCode('FILE_OPS_INVALID', file_ops.plan, self.root, 'HEAD',
                        [{'op': 'delete', 'path': 'README.txt'}])

    def test_dirty_tree_refused(self):
        ops = [{'op': 'delete', 'path': 'README.txt'}]
        self.write('src/tool.py', 'print(2)\n')
        self.assertCode('FILE_OPS_D6_NOT_CLEAN', self.make_plan, ops)
        git(self.root, 'checkout', '--', 'src/tool.py')
        self.write('new/untracked.txt', 'new\n')
        self.assertCode('FILE_OPS_D6_NOT_CLEAN', self.make_plan, ops)

    def test_untracked_or_empty_source_refused(self):
        for ops in ([{'op': 'delete', 'path': 'missing.txt'}],
                    [{'op': 'delete', 'path': 'nodir/'}],
                    [{'op': 'delete', 'path': 'docs'}],
                    [{'op': 'move', 'from': 'missing.txt', 'to': 'x.txt'}]):
            with self.subTest(ops=ops):
                self.assertCode('FILE_OPS_PRECHECK_FAILED', self.make_plan, ops)
        self.assertUnchanged()

    def test_destination_exists_refused(self):
        self.write('.gitignore', 'ignored.txt\n')
        self.write('other/b.txt', 'other\n')
        self.head = self.commit()
        self.write('ignored.txt', 'local\n')  # ignored: tree is clean, path is taken
        for to in ('src/tool.py', 'ignored.txt', 'docs', 'README.txt/inner.txt'):
            with self.subTest(to=to):
                self.assertCode('FILE_OPS_PRECHECK_FAILED', self.make_plan,
                                [{'op': 'move', 'from': 'docs/a.txt', 'to': to}])
        # Directory move: one destination (other/b.txt) is already tracked.
        self.assertCode('FILE_OPS_PRECHECK_FAILED', self.make_plan,
                        [{'op': 'move', 'from': 'docs/', 'to': 'other/'}])
        self.assertTrue(self.exists('ignored.txt'))

    def test_symlink_escape_refused(self):
        os.symlink(self.outside, os.path.join(self.root, 'escape'))
        self.head = self.commit()  # tracked symlink, clean tree
        for ops in ([{'op': 'move', 'from': 'README.txt', 'to': 'escape/README.txt'}],
                    [{'op': 'delete', 'path': 'escape'}],
                    [{'op': 'move', 'from': 'escape', 'to': 'moved'}],
                    [{'op': 'delete', 'path': 'escape/'}]):
            with self.subTest(ops=ops):
                self.assertCode('FILE_OPS_PRECHECK_FAILED', self.make_plan, ops)
        self.assertEqual([], os.listdir(self.outside))
        self.assertTrue(os.path.islink(os.path.join(self.root, 'escape')))

    def test_path_touched_twice_refused(self):
        for ops in ([{'op': 'delete', 'path': 'docs/a.txt'}, {'op': 'delete', 'path': 'docs/'}],
                    [{'op': 'move', 'from': 'README.txt', 'to': 'x.txt'},
                     {'op': 'move', 'from': 'src/tool.py', 'to': 'x.txt'}],
                    [{'op': 'move', 'from': 'README.txt', 'to': 'new'},
                     {'op': 'move', 'from': 'src/tool.py', 'to': 'new/tool.py'}]):
            with self.subTest(ops=ops):
                self.assertCode('FILE_OPS_PRECHECK_FAILED', self.make_plan, ops)

    def test_one_bad_op_refuses_whole_batch(self):
        ops = [{'op': 'delete', 'path': 'README.txt'},
               {'op': 'move', 'from': 'docs/', 'to': 'archive/'},
               {'op': 'delete', 'path': 'missing.txt'}]
        self.assertCode('FILE_OPS_PRECHECK_FAILED', self.make_plan, ops)
        self.assertUnchanged()
        self.assertFalse(self.exists('archive'))

    def test_too_many_files_refused(self):
        for i in range(file_ops.MAX_FILES + 1):
            self.write(f'many/f{i:03d}.txt', f'{i}\n')
        self.head = self.commit()
        self.assertCode('FILE_OPS_PRECHECK_FAILED', self.make_plan,
                        [{'op': 'delete', 'path': 'many/'}])
        self.assertEqual('', self.status())


class ApplyTests(_RepoCase):
    def apply_ok(self, ops):
        p = self.make_plan(ops)
        result = file_ops.apply(self.root, p)
        self.assertEqual('APPLIED', result['status'])
        self.assertFalse(result['isolate'])
        self.assertEqual(self.head, self.rev('HEAD'))
        self.assertEqual(result['evidence'], file_ops.verify(self.root, p))
        self.assertEqual(p['sha256'], result['evidence']['plan_sha256'])
        return p, result

    def test_delete_file(self):
        self.apply_ok([{'op': 'delete', 'path': 'README.txt'}])
        self.assertFalse(self.exists('README.txt'))
        self.assertEqual('D  README.txt\n', self.status())

    def test_delete_directory(self):
        self.apply_ok([{'op': 'delete', 'path': 'docs/'}])
        for rel in ('docs/a.txt', 'docs/b.txt', 'docs/sub/c.txt'):
            self.assertFalse(self.exists(rel))
        self.assertEqual(3, len(self.status().splitlines()))

    def test_move_file_into_new_directory(self):
        blob = self.rev('HEAD:README.txt')
        self.apply_ok([{'op': 'move', 'from': 'README.txt', 'to': 'moved/deep/README.txt'}])
        self.assertFalse(self.exists('README.txt'))
        self.assertEqual(blob, self.rev(':moved/deep/README.txt'))

    def test_move_directory_keeps_blob_ids(self):
        before = {rel: self.rev(f'HEAD:docs/{rel}') for rel in ('a.txt', 'b.txt', 'sub/c.txt')}
        p, result = self.apply_ok([{'op': 'move', 'from': 'docs/', 'to': 'archive/docs/'}])
        for rel, blob in before.items():
            self.assertEqual(blob, self.rev(f':archive/docs/{rel}'))
            self.assertFalse(self.exists(f'docs/{rel}'))
        self.assertEqual({f['blob'] for f in result['evidence']['files']}, set(before.values()))
        self.assertEqual(self.head, result['evidence']['head'])

    def test_paths_are_literal_not_globs(self):
        self.write('docs/a*.txt', 'star\n')
        self.head = self.commit()
        self.apply_ok([{'op': 'delete', 'path': 'docs/a*.txt'}])
        self.assertTrue(self.exists('docs/a.txt'))
        self.assertFalse(self.exists('docs/a*.txt'))

    def test_repeat_apply_is_noop(self):
        p, _ = self.apply_ok([{'op': 'move', 'from': 'src/', 'to': 'lib/'}])
        status = self.status()
        again = file_ops.apply(self.root, p)
        self.assertEqual('VERIFIED', again['status'])
        self.assertTrue(again['noop'])
        self.assertEqual(status, self.status())

    def test_partial_state_refused(self):
        p = self.make_plan([{'op': 'delete', 'path': 'README.txt'},
                            {'op': 'delete', 'path': 'src/tool.py'}])
        git(self.root, 'rm', '-q', '--', 'README.txt')
        self.assertCode('FILE_OPS_PARTIAL_STATE', file_ops.apply, self.root, p)
        self.assertTrue(self.exists('src/tool.py'))

    def test_changes_outside_plan_refused(self):
        p = self.make_plan([{'op': 'delete', 'path': 'README.txt'}])
        self.write('src/tool.py', 'print(2)\n')
        self.assertCode('FILE_OPS_D6_NOT_CLEAN', file_ops.apply, self.root, p)
        self.assertTrue(self.exists('README.txt'))

    def test_apply_reruns_prechecks(self):
        self.write('.gitignore', 'late.txt\n')
        self.head = self.commit()
        p = self.make_plan([{'op': 'delete', 'path': 'README.txt'},
                            {'op': 'move', 'from': 'docs/a.txt', 'to': 'late.txt'}])
        self.write('late.txt', 'appeared\n')  # ignored, so the tree is still clean
        self.assertCode('FILE_OPS_PRECHECK_FAILED', file_ops.apply, self.root, p)
        self.assertTrue(self.exists('README.txt'))
        self.assertTrue(self.exists('docs/a.txt'))
        self.assertEqual('', self.status())

    def test_head_mismatch_refused_at_apply(self):
        p = self.make_plan([{'op': 'delete', 'path': 'README.txt'}])
        self.write('extra.txt', 'extra\n')
        self.commit()
        self.assertCode('FILE_OPS_HEAD_MISMATCH', file_ops.apply, self.root, p)
        self.assertTrue(self.exists('README.txt'))

    def test_tampered_plan_refused(self):
        p = self.make_plan([{'op': 'delete', 'path': 'README.txt'}])
        tampered = json.loads(json.dumps(p))
        tampered['files'][0]['path'] = 'src/tool.py'
        self.assertCode('FILE_OPS_INVALID', file_ops.apply, self.root, tampered)
        resealed = file_ops._seal(p['head'], p['ops'], tampered['files'])
        self.assertCode('FILE_OPS_PRECHECK_FAILED', file_ops.apply, self.root, resealed)
        self.assertUnchanged()

    def test_verify_refuses_unapplied_tree(self):
        p = self.make_plan([{'op': 'delete', 'path': 'README.txt'}])
        self.assertCode('FILE_OPS_VERIFY_FAILED', file_ops.verify, self.root, p)

    def test_mid_run_failure_stops_and_isolates(self):
        p = self.make_plan([{'op': 'delete', 'path': 'README.txt'},
                            {'op': 'delete', 'path': 'docs/a.txt'},
                            {'op': 'move', 'from': 'src/tool.py', 'to': 'lib/tool.py'}])
        real_mutate = file_ops._mutate
        calls = []

        def flaky(root, args):
            calls.append(list(args))
            if len(calls) == 2:
                return False
            return real_mutate(root, args)

        with mock.patch.object(file_ops, '_mutate', side_effect=flaky):
            result = file_ops.apply(self.root, p)
        self.assertEqual('FAILED', result['status'])
        self.assertTrue(result['isolate'])
        self.assertEqual(1, result['completed'])
        self.assertEqual(2, len(calls))
        self.assertFalse(self.exists('README.txt'))
        self.assertTrue(self.exists('docs/a.txt'))
        self.assertTrue(self.exists('src/tool.py'))
        self.assertFalse(self.exists('lib'))
        self.assertEqual(self.head, self.rev('HEAD'))
        self.assertCode('FILE_OPS_PARTIAL_STATE', file_ops.apply, self.root, p)

    # -- TOCTOU: a parent is swapped for a symlink after the plan checks ran.

    def swap_after_recheck(self, swap):
        """Run ``swap`` right after apply's own re-run of the plan checks."""
        real_make_plan = file_ops._make_plan

        def wrapped(*args, **kwargs):
            result = real_make_plan(*args, **kwargs)
            swap()
            return result

        return mock.patch.object(file_ops, '_make_plan', side_effect=wrapped)

    def spy_mutate(self, after=None):
        real_mutate = file_ops._mutate
        calls = []

        def spy(root, args):
            calls.append(list(args))
            ok = real_mutate(root, args)
            if after:
                after(len(calls))
            return ok

        return calls, mock.patch.object(file_ops, '_mutate', side_effect=spy)

    def assertIsolated(self, result, completed):
        self.assertEqual('FAILED', result['status'])
        self.assertTrue(result['isolate'])
        self.assertEqual(completed, result['completed'])
        self.assertEqual(self.head, self.rev('HEAD'))

    def test_existing_destination_parent_swapped_for_symlink(self):
        p = self.make_plan([{'op': 'move', 'from': 'README.txt', 'to': 'docs/README.txt'}])

        def swap():
            os.rename(os.path.join(self.root, 'docs'), os.path.join(self.root, 'docs-aside'))
            os.symlink(self.outside, os.path.join(self.root, 'docs'))

        calls, spy = self.spy_mutate()
        with self.swap_after_recheck(swap), spy:
            result = file_ops.apply(self.root, p)
        self.assertIsolated(result, 0)
        self.assertEqual([], calls)
        self.assertEqual([], os.listdir(self.outside))
        self.assertTrue(self.exists('README.txt'))

    def test_new_destination_parent_swapped_for_symlink_mid_run(self):
        p = self.make_plan([{'op': 'delete', 'path': 'README.txt'},
                            {'op': 'move', 'from': 'src/tool.py', 'to': 'lib/new/tool.py'}])

        def after(count):
            if count == 1:
                os.symlink(self.outside, os.path.join(self.root, 'lib'))

        calls, spy = self.spy_mutate(after)
        with spy:
            result = file_ops.apply(self.root, p)
        self.assertIsolated(result, 1)
        self.assertEqual(1, len(calls))
        self.assertEqual([], os.listdir(self.outside))
        self.assertTrue(self.exists('src/tool.py'))
        self.assertTrue(os.path.islink(os.path.join(self.root, 'lib')))

    def test_source_parent_swapped_for_symlink(self):
        p = self.make_plan([{'op': 'delete', 'path': 'docs/a.txt'}])
        moved_aside = os.path.join(self.outside, 'docs')

        def swap():
            os.rename(os.path.join(self.root, 'docs'), moved_aside)
            os.symlink(moved_aside, os.path.join(self.root, 'docs'))

        calls, spy = self.spy_mutate()
        with self.swap_after_recheck(swap), spy:
            result = file_ops.apply(self.root, p)
        self.assertIsolated(result, 0)
        self.assertEqual([], calls)
        self.assertTrue(os.path.isfile(os.path.join(moved_aside, 'a.txt')))

    def test_result_path_checked_after_each_op(self):
        p = self.make_plan([{'op': 'move', 'from': 'README.txt', 'to': 'moved/README.txt'},
                            {'op': 'delete', 'path': 'src/tool.py'}])

        def after(count):
            if count == 1:  # result parent becomes a symlink (still inside the root)
                os.rename(os.path.join(self.root, 'moved'), os.path.join(self.root, 'aside'))
                os.symlink(os.path.join(self.root, 'aside'), os.path.join(self.root, 'moved'))

        calls, spy = self.spy_mutate(after)
        with spy:
            result = file_ops.apply(self.root, p)
        self.assertIsolated(result, 1)
        self.assertIn('symlink', result['reason'])
        self.assertEqual(1, len(calls))
        self.assertTrue(self.exists('src/tool.py'))

    def test_destination_parents_created_one_level_at_a_time(self):
        made = []
        real_mkdir = os.mkdir

        def spy_mkdir(path, *args, **kwargs):
            made.append((path, kwargs.get('dir_fd') is not None))
            return real_mkdir(path, *args, **kwargs)

        p = self.make_plan([{'op': 'move', 'from': 'README.txt', 'to': 'a/b/c/README.txt'}])
        with mock.patch.object(file_ops.os, 'mkdir', side_effect=spy_mkdir), \
                mock.patch.object(file_ops.os, 'makedirs', side_effect=AssertionError('makedirs')):
            self.assertEqual('APPLIED', file_ops.apply(self.root, p)['status'])
        # One bare name per level, always relative to a directory fd.
        self.assertEqual([('a', True), ('b', True), ('c', True)], made)

    def test_ancestor_swapped_during_parent_creation(self):
        p = self.make_plan([{'op': 'move', 'from': 'README.txt', 'to': 'a/b/c/README.txt'}])
        real_mkdir = os.mkdir
        made = []

        def swapping_mkdir(path, *args, **kwargs):
            made.append(path)
            if len(made) == 2:  # 'a' was created and opened; swap it for a symlink outside
                os.rename(os.path.join(self.root, 'a'), os.path.join(self.root, 'a-aside'))
                os.symlink(self.outside, os.path.join(self.root, 'a'))
            return real_mkdir(path, *args, **kwargs)

        calls, spy = self.spy_mutate()
        with mock.patch.object(file_ops.os, 'mkdir', side_effect=swapping_mkdir), spy:
            result = file_ops.apply(self.root, p)
        self.assertIsolated(result, 0)
        self.assertEqual([], calls)
        self.assertEqual([], os.listdir(self.outside))  # nothing created through the symlink
        self.assertFalse(any(os.path.isabs(m) for m in made))
        self.assertTrue(os.path.islink(os.path.join(self.root, 'a')))
        self.assertTrue(self.exists('README.txt'))

    def test_source_parent_swapped_during_destination_creation(self):
        p = self.make_plan([{'op': 'move', 'from': 'docs/a.txt', 'to': 'fresh/a.txt'}])
        moved_aside = os.path.join(self.outside, 'docs')
        real_mkdir = os.mkdir

        def swapping_mkdir(path, *args, **kwargs):
            made = real_mkdir(path, *args, **kwargs)
            if not os.path.lexists(moved_aside):
                os.rename(os.path.join(self.root, 'docs'), moved_aside)
                os.symlink(moved_aside, os.path.join(self.root, 'docs'))
            return made

        calls, spy = self.spy_mutate()
        with mock.patch.object(file_ops.os, 'mkdir', side_effect=swapping_mkdir), spy:
            result = file_ops.apply(self.root, p)
        self.assertIsolated(result, 0)
        self.assertIn('docs/a.txt', result['reason'])
        self.assertEqual([], calls)  # no git mutation after the swap
        self.assertEqual(['a.txt', 'b.txt', 'sub'], sorted(os.listdir(moved_aside)))
        self.assertFalse(self.exists('fresh/a.txt'))

    def test_git_runs_as_argument_lists_without_forbidden_verbs(self):
        real_run = subprocess.run
        seen = []

        def spy(*args, **kwargs):
            seen.append((args, kwargs))
            return real_run(*args, **kwargs)

        p = self.make_plan([{'op': 'delete', 'path': 'README.txt'},
                            {'op': 'move', 'from': 'docs/', 'to': 'old/'}])
        with mock.patch.object(file_ops.subprocess, 'run', side_effect=spy):
            self.assertEqual('APPLIED', file_ops.apply(self.root, p)['status'])
        verbs = set()
        for args, kwargs in seen:
            self.assertFalse(kwargs.get('shell', False))
            argv = args[0] if args else kwargs['args']
            self.assertIsInstance(argv, list)
            self.assertEqual('git', argv[0])
            verbs.add(_git_verb(argv))
        self.assertIn('rm', verbs)
        self.assertIn('mv', verbs)
        self.assertFalse(verbs & FORBIDDEN_VERBS)
        self.assertEqual(self.head, self.rev('HEAD'))

    def test_only_rm_and_mv_mutations(self):
        with self.assertRaises(LLMToolError) as ctx:
            file_ops._mutate(self.root, ['commit', '-m', 'x'])
        self.assertEqual('FILE_OPS_INVALID', ctx.exception.code)
        self.assertEqual(self.head, self.rev('HEAD'))


if __name__ == '__main__':
    unittest.main()
