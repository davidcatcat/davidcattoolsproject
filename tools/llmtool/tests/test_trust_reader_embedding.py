"""Ensure isolated D6 scripts use the canonical trust byte reader."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
TRUST = ROOT / "tools" / "llmtool" / "llmtool" / "trust.py"
SCRIPTS = ROOT / "tools" / "llmtool" / "scripts"


def _reader(source, name=b"llmtool-trust-reader"):
    begin = b"# BEGIN " + name + b" v1\n"
    end = b"# END " + name + b" v1\n"
    if source.count(begin) != 1 or source.count(end) != 1:
        raise AssertionError("embedded block markers must appear exactly once")
    start = source.index(begin)
    finish = source.index(end, start) + len(end)
    return source[start:finish]


class TrustReaderEmbeddingTests(unittest.TestCase):
    def test_d6_scripts_embed_the_canonical_reader_byte_for_byte(self):
        canonical = _reader(TRUST.read_bytes())
        for name in ("d6_worktree_env.py", "d6_worktree_lifecycle.py"):
            with self.subTest(script=name):
                self.assertEqual(_reader((SCRIPTS / name).read_bytes()), canonical)

    def test_safe_git_preflight_embeds_the_canonical_reader_byte_for_byte(self):
        # #369 PR-7: the stdlib-only skill script reads trust for its deny-list.
        preflight = ROOT / "tools" / "safe-git" / "preflight.py"
        self.assertEqual(_reader(preflight.read_bytes()), _reader(TRUST.read_bytes()))

    def test_safe_git_preflight_embeds_the_canonical_root_validation_byte_for_byte(self):
        # #369 PR-7 v1 FIX: preflight validates roots exactly like trust.load_trust.
        preflight = ROOT / "tools" / "safe-git" / "preflight.py"
        name = b"llmtool-trust-roots"
        canonical = _reader(TRUST.read_bytes(), name)
        self.assertIn(b"def _validate_roots(value, uid, home):", canonical)
        self.assertEqual(_reader(preflight.read_bytes(), name), canonical)


if __name__ == "__main__":
    unittest.main()
