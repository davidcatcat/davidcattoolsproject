import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

LLMTOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LLMTOOL_ROOT))

from llmtool import chat_tui  # noqa: E402


REVISION = "abcdef0123456789abcdef0123456789abcdef01"
SHORT_REVISION = REVISION[:8]


def _source_tree(root):
    """Build a package under source_dir nine levels below root and return both paths."""
    source_dir = Path(root)
    for name in ("p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8", "p9"):
        source_dir = source_dir / name
    source_dir = source_dir / "source_dir"
    package = source_dir / "pkg"
    package.mkdir(parents=True)
    module = package / "module.py"
    module.write_text("", encoding="utf-8")
    return source_dir, module


def _write_head(git_dir):
    git_dir.mkdir(parents=True)
    (git_dir / "HEAD").write_text(REVISION + "\n", encoding="ascii")


class SourceRevisionTests(unittest.TestCase):
    def test_eighth_parent_is_read(self):
        with tempfile.TemporaryDirectory() as root:
            source_dir, module = _source_tree(root)
            _write_head(source_dir.parents[7] / ".git")
            with patch("subprocess.run", side_effect=AssertionError("Git must not run")):
                self.assertEqual(chat_tui._source_revision(str(module)), SHORT_REVISION)

    def test_ninth_parent_is_not_read(self):
        with tempfile.TemporaryDirectory() as root:
            source_dir, module = _source_tree(root)
            _write_head(source_dir.parents[8] / ".git")
            with patch("subprocess.run", side_effect=AssertionError("Git must not run")):
                self.assertIsNone(chat_tui._source_revision(str(module)))

    def test_no_git_returns_none_without_running_git(self):
        with tempfile.TemporaryDirectory() as temp:
            source_dir = Path(os.path.abspath(temp))
            if len(list(source_dir.parents)) >= 8:
                self.skipTest("temp directory has too many parents")
            if any(os.path.lexists(str(parent / ".git")) for parent in [source_dir, *source_dir.parents]):
                self.skipTest("ancestor already holds .git")
            package = source_dir / "pkg"
            package.mkdir()
            module = package / "module.py"
            module.write_text("", encoding="utf-8")
            with patch("subprocess.run", side_effect=AssertionError("Git must not run")):
                self.assertIsNone(chat_tui._source_revision(str(module)))


if __name__ == "__main__":
    unittest.main()
