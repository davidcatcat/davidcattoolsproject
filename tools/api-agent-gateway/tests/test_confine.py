"""Adversarial confinement tests against the Workspace directly."""
from __future__ import annotations

import errno
import json
import os
import shutil
import socket
import stat
import tempfile
import unittest
from unittest import mock

import helpers  # noqa: F401  (sets sys.path)
from api_agent_gateway.confine import (TEMP_PREFIX, ConfinementError, ConfinementViolation, Scope, Workspace,
                                       normalize)
from api_agent_gateway.tools import ToolExecutor


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='gwagent-confine-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, 'root')
        self.outside = os.path.join(self.tmp, 'outside')
        for d in ('root/src/pkg', 'root/docs', 'root/.git', 'outside'):
            os.makedirs(os.path.join(self.tmp, d))
        self.put('src/a.py', 'alpha\nbeta\ngamma\n')
        self.put('src/pkg/b.py', 'needle here\n')
        self.put('docs/private.md', 'PRIVATE needle\n')
        self.put('.git/config', '[core]\n')
        with open(os.path.join(self.outside, 'secret.txt'), 'w') as fh:
            fh.write('OUTSIDE SECRET\n')
        self.secret = os.path.join(self.outside, 'secret.txt')
        self.hooks = {}
        self.ws = Workspace(self.root, Scope(['src/', 'README.md'], ['src/a.py', 'src/new/', 'src/pkg/']),
                            hook=lambda point, rel: self.hooks.pop(point, lambda: None)())
        self.addCleanup(self.ws.close)

    def put(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as fh:
            fh.write(text)

    def code(self, fn, *a, **kw):
        with self.assertRaises(ConfinementError) as cm:
            fn(*a, **kw)
        return cm.exception.code

    def secret_unchanged(self):
        with open(self.secret) as fh:
            self.assertEqual(fh.read(), 'OUTSIDE SECRET\n')


class NormalizeTests(unittest.TestCase):
    def test_refusals(self):
        cases = {
            '../x': 'PATH_TRAVERSAL', 'src/../../x': 'PATH_TRAVERSAL', '/etc/passwd': 'PATH_ABSOLUTE',
            '~/x': 'PATH_ABSOLUTE', '.git/config': 'PATH_GIT', 'src/.GIT/x': 'PATH_GIT',
            'a//b': 'PATH_INVALID', './a': 'PATH_INVALID', 'a\\b': 'PATH_INVALID', 'a\x00b': 'PATH_INVALID',
            '': 'PATH_INVALID', 'src/' + TEMP_PREFIX + 'x': 'PATH_INVALID',
        }
        for path, expected in cases.items():
            with self.subTest(path=path), self.assertRaises(ConfinementError) as cm:
                normalize(path)
            self.assertEqual(cm.exception.code, expected)
        self.assertEqual(normalize('src/a.py'), 'src/a.py')
        self.assertEqual(normalize('src/'), 'src')
        self.assertEqual(normalize('', allow_root=True), '')

    def test_scope_rejects_whole_repo_entry(self):
        with self.assertRaises(ConfinementError):
            Scope(['.'], [])
        with self.assertRaises(ConfinementError):
            Scope(['/'], [])


class ReadTests(Base):
    def test_read_in_scope(self):
        self.assertEqual(self.ws.read_text('src/a.py')[1], 'alpha\nbeta\ngamma\n')

    def test_out_of_scope_read(self):
        self.assertEqual(self.code(self.ws.read_text, 'docs/private.md'), 'OUT_OF_SCOPE_READ')
        self.assertEqual(self.code(self.ws.read_text, '.git/config'), 'PATH_GIT')
        self.assertEqual(self.code(self.ws.read_text, '../outside/secret.txt'), 'PATH_TRAVERSAL')

    def test_symlink_file_refused(self):
        os.symlink(self.secret, os.path.join(self.root, 'src', 'link.py'))
        self.assertEqual(self.code(self.ws.read_text, 'src/link.py'), 'SYMLINK_REFUSED')

    def test_symlink_directory_component_refused(self):
        os.symlink(self.outside, os.path.join(self.root, 'src', 'out'))
        self.assertEqual(self.code(self.ws.read_text, 'src/out/secret.txt'), 'SYMLINK_REFUSED')

    def test_symlinked_root_refused(self):
        link = os.path.join(self.tmp, 'rootlink')
        os.symlink(self.root, link)
        with self.assertRaises(ConfinementError) as cm:
            Workspace(link, Scope(['src/'], []))
        self.assertEqual(cm.exception.code, 'WORKDIR_INVALID')

    def test_hard_link_refused(self):
        os.link(self.secret, os.path.join(self.root, 'src', 'hard.py'))
        self.assertEqual(self.code(self.ws.read_text, 'src/hard.py'), 'HARD_LINK_REFUSED')
        # An in-scope file hard-linked from outside cannot be edited either.
        os.link(os.path.join(self.root, 'src', 'pkg', 'b.py'), os.path.join(self.outside, 'alias.py'))
        self.assertEqual(self.code(self.ws.apply_edit, 'src/pkg/b.py', 'needle', 'x', False), 'HARD_LINK_REFUSED')

    def test_fifo_refused_without_blocking(self):
        os.mkfifo(os.path.join(self.root, 'src', 'pipe.py'))
        self.assertEqual(self.code(self.ws.read_text, 'src/pipe.py'), 'SPECIAL_FILE')

    def test_socket_refused(self):
        path = os.path.join(self.root, 'src', 's.sock')
        sock = socket.socket(socket.AF_UNIX)
        cwd = os.getcwd()
        try:
            os.chdir(os.path.join(self.root, 'src'))
            sock.bind('s.sock')
        finally:
            os.chdir(cwd)
        self.addCleanup(sock.close)
        self.assertTrue(stat.S_ISSOCK(os.lstat(path).st_mode))
        self.assertIn(self.code(self.ws.read_text, 'src/s.sock'), ('SPECIAL_FILE', 'OS_ERROR'))

    def test_symlink_swap_between_check_and_open(self):
        target = os.path.join(self.root, 'src', 'a.py')

        def swap():
            os.unlink(target)
            os.symlink(self.secret, target)
        self.hooks['read_before_open'] = swap
        self.assertEqual(self.code(self.ws.read_text, 'src/a.py'), 'SYMLINK_REFUSED')

    def test_parent_replacement_during_read(self):
        src = os.path.join(self.root, 'src')

        def replace():
            os.rename(src, os.path.join(self.outside, 'moved'))
            os.symlink(self.outside, src)
        self.hooks['read_after_open'] = replace
        with self.assertRaises(ConfinementViolation):
            self.ws.read_text('src/a.py')

    def test_file_replacement_after_read_detected(self):
        target = os.path.join(self.root, 'src', 'a.py')

        def replace():
            os.unlink(target)
            os.symlink(self.secret, target)
        self.hooks['read_after_open'] = replace
        with self.assertRaises(ConfinementViolation):
            self.ws.read_text('src/a.py')

    def test_list_dir_only_shows_scope(self):
        _, entries, _ = self.ws.list_dir('')
        names = {e['name'] for e in entries}
        self.assertEqual(names, {'src'})  # README.md absent on disk; docs and .git hidden
        _, entries, _ = self.ws.list_dir('src')
        self.assertEqual({e['name'] for e in entries}, {'a.py', 'pkg'})
        self.assertEqual(self.code(self.ws.list_dir, 'docs'), 'OUT_OF_SCOPE_READ')

    def test_grep_bounded_and_confined(self):
        os.symlink(os.path.join(self.root, 'docs', 'private.md'), os.path.join(self.root, 'src', 'l.md'))
        results, truncated = self.ws.grep('needle', '', ignore_case=False, max_results=50)
        self.assertEqual([r['path'] for r in results], ['src/pkg/b.py'])
        self.assertFalse(truncated)
        self.put('src/many.py', 'needle\n' * 30)
        results, truncated = self.ws.grep('needle', 'src', ignore_case=False, max_results=5)
        self.assertEqual(len(results), 5)
        self.assertTrue(truncated)


class WriteTests(Base):
    def test_edit_and_create(self):
        os.chmod(os.path.join(self.root, 'src', 'a.py'), 0o640)
        self.assertEqual(self.ws.apply_edit('src/a.py', 'beta', 'BETA', False), 'src/a.py')
        st = os.lstat(os.path.join(self.root, 'src', 'a.py'))
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o640)
        with open(os.path.join(self.root, 'src', 'a.py')) as fh:
            self.assertEqual(fh.read(), 'alpha\nBETA\ngamma\n')
        os.mkdir(os.path.join(self.root, 'src', 'new'))
        self.ws.apply_edit('src/new/c.py', None, 'C = 3\n', True)
        self.assertEqual(self.code(self.ws.apply_edit, 'src/new/c.py', None, 'again', True), 'ALREADY_EXISTS')
        leftovers = [n for d, _, ns in os.walk(self.root) for n in ns if n.startswith(TEMP_PREFIX)]
        self.assertEqual(leftovers, [])

    def test_edit_requires_unique_old(self):
        self.put('src/pkg/d.py', 'x\nx\n')
        self.assertEqual(self.code(self.ws.apply_edit, 'src/pkg/d.py', 'x', 'y', False), 'EDIT_OLD_AMBIGUOUS')
        self.assertEqual(self.code(self.ws.apply_edit, 'src/pkg/d.py', 'zz', 'y', False), 'EDIT_OLD_NOT_FOUND')

    def test_out_of_scope_write(self):
        self.put('src/readonly.py', 'r\n')
        self.assertEqual(self.code(self.ws.apply_edit, 'src/readonly.py', 'r', 'w', False), 'OUT_OF_SCOPE_WRITE')
        self.assertEqual(self.code(self.ws.apply_edit, 'docs/private.md', 'P', 'w', False), 'OUT_OF_SCOPE_WRITE')
        self.assertEqual(self.code(self.ws.apply_edit, '../outside/secret.txt', 'O', 'w', False), 'PATH_TRAVERSAL')
        self.assertEqual(self.code(self.ws.apply_edit, '.git/config', '[', 'x', False), 'PATH_GIT')
        self.secret_unchanged()

    def test_write_through_symlink_refused(self):
        os.symlink(self.secret, os.path.join(self.root, 'src', 'pkg', 'evil.py'))
        self.assertEqual(self.code(self.ws.apply_edit, 'src/pkg/evil.py', 'OUTSIDE', 'PWNED', False),
                         'SYMLINK_REFUSED')
        self.secret_unchanged()

    def test_symlink_swap_before_rename_edit(self):
        target = os.path.join(self.root, 'src', 'a.py')

        def swap():
            os.unlink(target)
            os.symlink(self.secret, target)
        self.hooks['write_before_rename'] = swap
        self.assertEqual(self.code(self.ws.apply_edit, 'src/a.py', 'beta', 'X', False), 'CONCURRENT_MODIFICATION')
        self.secret_unchanged()
        self.assertTrue(os.path.islink(target))

    def test_symlink_planted_before_create(self):
        os.mkdir(os.path.join(self.root, 'src', 'new'))
        target = os.path.join(self.root, 'src', 'new', 'c.py')
        self.hooks['write_before_rename'] = lambda: os.symlink(self.secret, target)
        self.assertEqual(self.code(self.ws.apply_edit, 'src/new/c.py', None, 'PWNED', True), 'ALREADY_EXISTS')
        self.secret_unchanged()

    def test_hard_link_created_before_rename_does_not_leak(self):
        target = os.path.join(self.root, 'src', 'a.py')
        alias = os.path.join(self.outside, 'alias.py')
        self.hooks['write_before_rename'] = lambda: os.link(target, alias)
        # The link changes st_nlink but not identity/size/mtime; the rename
        # replaces the directory entry, so the outside alias keeps old content.
        try:
            self.ws.apply_edit('src/a.py', 'beta', 'NEW', False)
        except ConfinementError:
            pass
        with open(alias) as fh:
            self.assertEqual(fh.read(), 'alpha\nbeta\ngamma\n')

    def test_parent_replacement_before_rename(self):
        pkg = os.path.join(self.root, 'src', 'pkg')
        moved = os.path.join(self.outside, 'pkg')

        def replace():
            os.rename(pkg, moved)
            os.symlink(self.outside, pkg)
        self.hooks['write_before_rename'] = replace
        with self.assertRaises(ConfinementViolation):
            self.ws.apply_edit('src/pkg/b.py', 'needle', 'X', False)
        with open(os.path.join(moved, 'b.py')) as fh:
            self.assertEqual(fh.read(), 'needle here\n')
        self.assertEqual([n for n in os.listdir(moved) if n.startswith(TEMP_PREFIX)], [])

    def test_parent_replacement_after_rename_detected(self):
        pkg = os.path.join(self.root, 'src', 'pkg')

        def replace():
            os.rename(pkg, os.path.join(self.outside, 'pkg'))
            os.mkdir(pkg)
        self.hooks['write_after_rename'] = replace
        with self.assertRaises(ConfinementViolation):
            self.ws.apply_edit('src/pkg/b.py', 'needle', 'X', False)

    def test_fifo_write_refused(self):
        os.mkfifo(os.path.join(self.root, 'src', 'pkg', 'p.py'))
        self.assertEqual(self.code(self.ws.apply_edit, 'src/pkg/p.py', 'a', 'b', False), 'SPECIAL_FILE')



class ToolOsErrorTests(Base):
    def test_operating_system_refusal_reaches_the_model_as_a_stable_code(self):
        # sandbox-exec refuses the temp file with EPERM, outside any path check.
        executor = ToolExecutor(self.ws, 4096)
        args = json.dumps({'path': 'src/new.py', 'new': 'x', 'create': True})
        with mock.patch.object(self.ws, 'apply_edit',
                               side_effect=PermissionError(errno.EPERM, 'denied', '/private/path')):
            outcome = executor.execute('apply_edit', args)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.code, 'PERMISSION_DENIED')
        self.assertNotIn('/private/path', outcome.content)
        with mock.patch.object(self.ws, 'apply_edit', side_effect=OSError(errno.EIO, 'io')):
            self.assertEqual(executor.execute('apply_edit', args).code, 'OS_ERROR')


if __name__ == '__main__':
    unittest.main()
