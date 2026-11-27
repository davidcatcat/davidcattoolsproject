"""Unit coverage for overlapping custom TestProfile entries.

The existing default-profile tests already exercise entry order in
equality and default lookup behaviour.  This module adds focused
coverage for a custom, synthetic profile whose two compiled
relative-path patterns overlap, pinning down the lookup contract of
``kind_for`` in ``llmtool/test_profile.py``: the first entry whose
pattern matches the whole path wins, and the match is anchored at both
ends (fullmatch), so trailing text is never accepted.

Only the public :class:`TestProfile` constructor and ``kind_for`` are
used.  All data is synthetic; the tests perform no file, network, or
process access and contain no credential-shaped strings.
"""

import importlib.util
import os
import re
import unittest

# Load the production module from its file location so the tests do not
# depend on how the developer or CI environment puts the package on
# sys.path.  The module sits in the package directory next to this
# tests directory:  <root>/tools/llmtool/llmtool/test_profile.py,
# i.e. one level up from <root>/tools/llmtool/tests/.
_MODULE_FILE = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    os.pardir, 'llmtool', 'test_profile.py'))
_MODULE_SPEC = importlib.util.spec_from_file_location(
    'llmtool_test_profile', _MODULE_FILE)
test_profile = importlib.util.module_from_spec(_MODULE_SPEC)
_MODULE_SPEC.loader.exec_module(test_profile)

TestProfile = test_profile.TestProfile


class OverlappingTestProfileTests(unittest.TestCase):
    """First-match and fullmatch precedence for overlapping entries."""

    def setUp(self):
        # Two overlapping relative-path patterns: every test_special_*
        # path also matches the broader test_* pattern.  Each entry uses
        # a different valid runner kind.
        self.broad = re.compile(r'tools/demo/tests/test_[A-Za-z0-9_]+\.py\Z')
        self.specific = re.compile(
            r'tools/demo/tests/test_special_[A-Za-z0-9_]+\.py\Z')
        # Entry order A: broad first, specific second.
        self.profile_a = TestProfile((
            (self.broad, 'unittest'),
            (self.specific, 'script'),
        ))
        # Entry order B: the reversed order, specific first.
        self.profile_b = TestProfile((
            (self.specific, 'script'),
            (self.broad, 'unittest'),
        ))

    def test_overlapping_path_returns_first_matching_kind(self):
        # The same path matches both entries; the result is the kind of
        # the first matching entry only.
        path = 'tools/demo/tests/test_special_alpha.py'
        self.assertEqual(self.profile_a.kind_for(path), 'unittest')
        self.assertEqual(self.profile_b.kind_for(path), 'script')

    def test_reversing_entry_order_changes_result(self):
        # The identical path resolves differently once the entry order
        # is reversed.
        path = 'tools/demo/tests/test_special_alpha.py'
        self.assertNotEqual(
            self.profile_a.kind_for(path), self.profile_b.kind_for(path))

    def test_second_entry_is_reached_when_first_does_not_match(self):
        # test_plain_beta.py matches only the broad pattern.  In
        # profile_b the broad pattern is the second entry, so the lookup
        # skips the first entry and is answered by the second one.
        path = 'tools/demo/tests/test_plain_beta.py'
        self.assertEqual(self.profile_b.kind_for(path), 'unittest')
        self.assertEqual(self.profile_a.kind_for(path), 'unittest')

    def test_trailing_text_is_unmatched(self):
        # An otherwise valid path with trailing text matches no pattern
        # at its end, so both profiles report no match.
        path = 'tools/demo/tests/test_special_alpha.py.bak'
        self.assertIsNone(self.profile_a.kind_for(path))
        self.assertIsNone(self.profile_b.kind_for(path))


if __name__ == '__main__':
    unittest.main()
