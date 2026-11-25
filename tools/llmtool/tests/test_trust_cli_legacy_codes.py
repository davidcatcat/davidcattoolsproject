"""#1230: trust init no longer resolves legacy source constants."""
import unittest

from llmtool import trust_cli


class LegacySourceRemovalTests(unittest.TestCase):
    def test_legacy_sources_helper_is_removed(self):
        self.assertFalse(hasattr(trust_cli, "_legacy_sources"))


if __name__ == "__main__":
    unittest.main()
