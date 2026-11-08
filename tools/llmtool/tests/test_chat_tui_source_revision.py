"""#1277 A: _source_revision walks source_dir and at most its 8 nearest parents."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmtool import chat_tui


# Synthetic, non-sensitive hex revision used only as a detached HEAD value.
REVISION = '0123456789abcdef0123456789abcdef01234567'


def _nested_module(root, depth):
    """Create root/a1/.../a<depth>/pkg/mod.py; source_dir is root/a1/.../a<depth>."""
    source_dir = root.joinpath(*('a%d' % index for index in range(1, depth + 1)))
    package = source_dir / 'pkg'
    package.mkdir(parents=True)
    module = package / 'mod.py'
    module.touch()
    return source_dir, module


def _write_git(directory):
    git = directory / '.git'
    git.mkdir()
    (git / 'HEAD').write_text(REVISION + '\n')


class SourceRevisionWalkDepthTests(unittest.TestCase):
    def test_git_directory_at_eighth_parent_is_found(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'checkout'
            source_dir, module = _nested_module(root, 8)
            self.assertEqual(Path(os.path.abspath(module)).parent.parent, source_dir)
            self.assertEqual(source_dir.parents[7], root)
            _write_git(root)
            with patch('subprocess.run', side_effect=AssertionError('must not run Git')):
                self.assertEqual(chat_tui._source_revision(module), REVISION[:8])

    def test_git_directory_at_ninth_parent_is_not_found(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'checkout'
            source_dir, module = _nested_module(root, 9)
            self.assertEqual(source_dir.parents[8], root)
            _write_git(root)
            with patch('subprocess.run', side_effect=AssertionError('must not run Git')):
                self.assertIsNone(chat_tui._source_revision(module))

    def test_short_parent_chain_without_git_returns_none(self):
        with tempfile.TemporaryDirectory() as temp:
            source_dir = Path(os.path.abspath(temp))
            package = source_dir / 'pkg'
            package.mkdir()
            module = package / 'mod.py'
            module.touch()
            parents = list(source_dir.parents)
            if len(parents) >= 8:
                self.skipTest('temporary directory is too deep for a short parent chain')
            if any(os.path.lexists(candidate / '.git') for candidate in parents):
                self.skipTest('an ancestor of the temporary directory holds .git')
            with patch('subprocess.run', side_effect=AssertionError('must not run Git')):
                self.assertIsNone(chat_tui._source_revision(module))


if __name__ == '__main__':
    unittest.main()
