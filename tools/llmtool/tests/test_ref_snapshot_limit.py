"""The Git state snapshot reads every ref within REF_SNAPSHOT_MAX_BYTES.

On 2026-11-26 the canonical repository's ref listing (refname, object, peeled
object, type) reached 1,049,417 bytes, over the former fixed 1 MiB bound, so
every AGY and gw-agent dispatch failed its Git snapshot. Synthetic repository;
no network.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from llmtool import task_completion
from llmtool.developer_routes import base as route_base


_GIT = '/usr/bin/git'
_ENV = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
            GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.invalid',
            GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@example.invalid')
_REF_FORMAT = '--format=%(refname) %(objectname) %(*objectname) %(objecttype)'


def _git(root, *args, stdin=None):
    return subprocess.run([_GIT, '-C', str(root), *args], check=True, env=_ENV,
                          input=stdin, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE).stdout


def _repo(temp):
    root = Path(temp) / 'repo'
    root.mkdir()
    _git(root, 'init', '-q', '-b', 'task-branch')
    (root / 'file.txt').write_text('one\n')
    _git(root, 'add', 'file.txt')
    _git(root, 'commit', '-q', '-m', 'init')
    return root


class RefSnapshotLimitTests(unittest.TestCase):
    def test_bound_is_at_least_the_route_byte_ceiling(self):
        self.assertGreaterEqual(route_base.REF_SNAPSHOT_MAX_BYTES, 5 * 1024 * 1024)

    def test_ref_listing_uses_the_named_bound(self):
        calls = []
        real = task_completion._git

        def recording(root, args, **kwargs):
            calls.append((tuple(args), kwargs.get('limit')))
            return real(root, args, **kwargs)

        with tempfile.TemporaryDirectory() as temp:
            root = _repo(temp)
            with mock.patch.object(route_base.task_completion, '_git', side_effect=recording):
                route_base.capture_snapshot(root)
        listings = [limit for args, limit in calls
                    if args[:1] == ('for-each-ref',) and _REF_FORMAT in args]
        self.assertEqual(listings, [route_base.REF_SNAPSHOT_MAX_BYTES])

    def test_snapshot_reads_a_ref_listing_over_one_mebibyte(self):
        with tempfile.TemporaryDirectory() as temp:
            root = _repo(temp)
            head = _git(root, 'rev-parse', 'HEAD').decode().strip()
            name = 'x' * 80
            count = 9000
            commands = ''.join(
                f'create refs/remotes/origin/{name}-{index:05d} {head}\n'
                for index in range(count)).encode()
            _git(root, 'update-ref', '--stdin', stdin=commands)
            _git(root, 'pack-refs', '--all')
            listing = _git(root, 'for-each-ref', _REF_FORMAT)
            self.assertGreater(len(listing), 1024 * 1024)
            snapshot = route_base.capture_snapshot(root)
        refs = snapshot['git_state']['refs']
        remote = [ref for ref in refs if ref.startswith('refs/remotes/origin/')]
        self.assertEqual(len(remote), count)


if __name__ == '__main__':
    unittest.main()
