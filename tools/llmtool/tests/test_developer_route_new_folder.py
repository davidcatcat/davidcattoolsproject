"""Tests for missing parent folder creation before boundary preparation."""

from pathlib import Path
import subprocess
import tempfile
import unittest

from llmtool.developer_routes.base import (
    DeveloperRoute, RouteError, capture_snapshot, run_route,
)


class FakeRoute(DeveloperRoute):
    name = 'FAKE'
    worker_key = 'fake'
    family = 'test-family'

    def validate_profile(self, model, effort):
        return model, effort

    def build_argv(self, executable, request):
        return [executable, 'run']

    def build_env(self, tmpdir):
        return {'TMPDIR': tmpdir}

    def parse_output(self, stdout, stderr, log_text):
        return {'status': 'DONE', 'summary': 'ok', 'session_id': 'session_1234',
                'usage': {'count': 1}}

    def verified_model(self, log_text, requested):
        return requested

    def quota_exhausted(self, parsed, stderr):
        return False


class RecordingBoundary:
    def __init__(self, check_dir):
        self.check_dir = Path(check_dir)
        self.folder_existed = None

    def prepare(self, root, write_paths, state_dirs=()):
        self.folder_existed = self.check_dir.is_dir()
        return ['/boundary'], 'a' * 64

    def verify(self, root, write_paths, state_dirs=(), *, runner):
        return {'verified': True, 'policy_sha256': 'a' * 64, 'probes': []}


class DeveloperRouteNewFolderTests(unittest.TestCase):
    def setUp(self):
        self.route = FakeRoute({})

    def _init_repo(self, root):
        subprocess.run(['/usr/bin/git', 'init', str(root)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['/usr/bin/git', '-C', str(root), 'config', 'user.name', 'Test'], check=True)
        subprocess.run(['/usr/bin/git', '-C', str(root), 'config', 'user.email', 'test@example.invalid'], check=True)
        (root / 'base.txt').write_text('base\n')
        subprocess.run(['/usr/bin/git', '-C', str(root), 'add', 'base.txt'], check=True)
        subprocess.run(['/usr/bin/git', '-C', str(root), 'commit', '-m', 'initial'], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        branch = subprocess.check_output(['/usr/bin/git', '-C', str(root), 'branch', '--show-current'],
                                         text=True).strip()
        head = subprocess.check_output(['/usr/bin/git', '-C', str(root), 'rev-parse', 'HEAD'],
                                       text=True).strip()
        return {'root': str(root), 'model': 'fake-model', 'effort': 'low',
                'expected_branch': branch, 'expected_head': head,
                'executable': '/fake/cli'}

    def _commit_all(self, root, message='update'):
        subprocess.run(['/usr/bin/git', '-C', str(root), 'add', '-A'], check=True)
        subprocess.run(['/usr/bin/git', '-C', str(root), 'commit', '-m', message], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return subprocess.check_output(['/usr/bin/git', '-C', str(root), 'rev-parse', 'HEAD'],
                                       text=True).strip()

    def test_missing_parent_folder_created_before_boundary_prepare(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            root.mkdir()
            base_request = self._init_repo(root)
            request = dict(base_request, write_paths=['a/b/new.py'])
            target_folder = root / 'a' / 'b'
            boundary = RecordingBoundary(target_folder)

            def runner(*args):
                (target_folder / 'new.py').write_text('content\n')
                return 0, b'ok', b''

            result = run_route(self.route, request, gate=lambda: True, runner=runner,
                               boundary=boundary, snapshot=capture_snapshot)
            self.assertTrue(boundary.folder_existed)
            self.assertTrue(target_folder.is_dir())
            self.assertEqual(result.changed_files, ('a/b/new.py',))

    def test_symlinked_folder_fails_and_creates_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            root.mkdir()
            base_request = self._init_repo(root)
            real_dir = root / 'real_dir'
            real_dir.mkdir()
            sym_dir = root / 'sym_dir'
            sym_dir.symlink_to(real_dir)
            head = self._commit_all(root, 'add symlink')
            request = dict(base_request, write_paths=['sym_dir/new_sub/new.py'],
                           expected_head=head)
            boundary = RecordingBoundary(sym_dir / 'new_sub')

            with self.assertRaises(RouteError) as caught:
                run_route(self.route, request, gate=lambda: True,
                          runner=lambda *args: (0, b'ok', b''),
                          boundary=boundary, snapshot=capture_snapshot)
            self.assertEqual(caught.exception.code, 'BLOCKED_WRITE_ISOLATION')
            self.assertEqual(caught.exception.blocked_reason, 'BOUNDARY_PREPARATION_FAILED')
            self.assertFalse((real_dir / 'new_sub').exists())
            self.assertFalse((sym_dir / 'new_sub').exists())

    def test_path_resolving_outside_root_fails_and_creates_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            root.mkdir()
            base_request = self._init_repo(root)
            outside_dir = Path(temp) / 'outside'
            outside_dir.mkdir()
            outside_link = root / 'outside_link'
            outside_link.symlink_to(outside_dir)
            head = self._commit_all(root, 'add outside link')
            request = dict(base_request, write_paths=['outside_link/new_sub/new.py'],
                           expected_head=head)
            boundary = RecordingBoundary(outside_link / 'new_sub')

            with self.assertRaises(RouteError) as caught:
                run_route(self.route, request, gate=lambda: True,
                          runner=lambda *args: (0, b'ok', b''),
                          boundary=boundary, snapshot=capture_snapshot)
            self.assertEqual(caught.exception.code, 'BLOCKED_WRITE_ISOLATION')
            self.assertEqual(caught.exception.blocked_reason, 'BOUNDARY_PREPARATION_FAILED')
            self.assertFalse((outside_dir / 'new_sub').exists())

    def test_path_under_git_fails_and_creates_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            root.mkdir()
            base_request = self._init_repo(root)
            git_target = root / '.git' / 'new_folder'
            request = dict(base_request, write_paths=['.git/new_folder/new.py'])
            boundary = RecordingBoundary(git_target)

            with self.assertRaises(RouteError) as caught:
                run_route(self.route, request, gate=lambda: True,
                          runner=lambda *args: (0, b'ok', b''),
                          boundary=boundary, snapshot=capture_snapshot)
            self.assertEqual(caught.exception.code, 'BLOCKED_WRITE_ISOLATION')
            self.assertEqual(caught.exception.blocked_reason, 'BOUNDARY_PREPARATION_FAILED')
            self.assertFalse(git_target.exists())

    def test_case_folded_git_path_fails_and_creates_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            root.mkdir()
            base_request = self._init_repo(root)
            git_target = root / '.GIT' / 'newdir'
            request = dict(base_request, write_paths=['.GIT/newdir/x.py'])
            boundary = RecordingBoundary(git_target)

            with self.assertRaises(RouteError) as caught:
                run_route(self.route, request, gate=lambda: True,
                          runner=lambda *args: (0, b'ok', b''),
                          boundary=boundary, snapshot=capture_snapshot)
            self.assertEqual(caught.exception.code, 'BLOCKED_WRITE_ISOLATION')
            self.assertEqual(caught.exception.blocked_reason, 'BOUNDARY_PREPARATION_FAILED')
            self.assertFalse(git_target.exists())

    def test_existing_parent_folder_changes_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            root.mkdir()
            base_request = self._init_repo(root)
            existing_dir = root / 'existing_dir'
            existing_dir.mkdir()
            request = dict(base_request, write_paths=['existing_dir/new.py'])
            boundary = RecordingBoundary(existing_dir)

            def runner(*args):
                (existing_dir / 'new.py').write_text('content\n')
                return 0, b'ok', b''

            result = run_route(self.route, request, gate=lambda: True, runner=runner,
                               boundary=boundary, snapshot=capture_snapshot)
            self.assertTrue(boundary.folder_existed)
            self.assertEqual(result.changed_files, ('existing_dir/new.py',))


if __name__ == '__main__':
    unittest.main()
