"""Ensure isolated D6 scripts use the canonical trust byte reader."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
TRUST = ROOT / "tools" / "llmtool" / "llmtool" / "trust.py"
SCRIPTS = ROOT / "tools" / "llmtool" / "scripts"


def _reader(source):
    begin = b"# BEGIN llmtool-trust-reader v1\n"
    end = b"# END llmtool-trust-reader v1\n"
    start = source.index(begin)
    finish = source.index(end, start) + len(end)
    return source[start:finish]


class TrustReaderEmbeddingTests(unittest.TestCase):
    def test_d6_scripts_embed_the_canonical_reader_byte_for_byte(self):
        canonical = _reader(TRUST.read_bytes())
        for name in ("d6_worktree_env.py", "d6_worktree_lifecycle.py"):
            with self.subTest(script=name):
                self.assertEqual(_reader((SCRIPTS / name).read_bytes()), canonical)


if __name__ == "__main__":
    unittest.main()
