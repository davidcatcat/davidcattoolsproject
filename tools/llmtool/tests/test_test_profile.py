"""#2793 H3b: merged test evidence profile pin (llmtool.test_profile)."""
import re
import unittest

from llmtool.test_profile import TestProfile, TestProfileError, default_profile

_UNITTEST_RE = r'tools/llmtool/tests/test_[A-Za-z0-9_]+\.py\Z'
_SCRIPT_RE = r'scripts/test_[A-Za-z0-9_]+\.py\Z'

# The current registry, in order.
EXPECTED_ENTRIES = (
    (re.compile(_UNITTEST_RE), 'unittest'),
    (re.compile(_SCRIPT_RE), 'script'),
)


class DefaultProfileTests(unittest.TestCase):
    def setUp(self):
        self.profile = default_profile()

    def test_default_equals_current_registry_in_order(self):
        self.assertEqual(self.profile, TestProfile(EXPECTED_ENTRIES))
        self.assertEqual([kind for _, kind in self.profile.entries],
                         ['unittest', 'script'])

    def test_swapped_order_is_not_equal(self):
        swapped = TestProfile(tuple(reversed(EXPECTED_ENTRIES)))
        self.assertNotEqual(self.profile, swapped)

    def test_kind_for_matching_paths(self):
        self.assertEqual(
            self.profile.kind_for('tools/llmtool/tests/test_foo_1.py'),
            'unittest')
        self.assertEqual(self.profile.kind_for('scripts/test_bar.py'),
                         'script')

    def test_kind_for_outside_or_trailing_text_is_none(self):
        for path in ('tests/test_foo.py',
                     'scripts/build.sh',
                     'tools/llmtool/tests/test_foo',
                     'scripts/test_foo.py.bak',
                     'tools/llmtool/tests/test_foo.py.old',
                     'scripts/test_foo.py extra'):
            with self.subTest(path=path):
                self.assertIsNone(self.profile.kind_for(path))


class ImmutabilityAndEqualityTests(unittest.TestCase):
    def setUp(self):
        self.profile = default_profile()

    def test_post_construction_change_is_refused(self):
        for attr in ('entries', '_entries', '_keys'):
            with self.subTest(attr=attr):
                with self.assertRaises(AttributeError):
                    setattr(self.profile, attr, ())
                with self.assertRaises(AttributeError):
                    delattr(self.profile, attr)
        self.assertEqual(self.profile, TestProfile(EXPECTED_ENTRIES))

    def test_equality_considers_pattern_flags(self):
        base = TestProfile(((re.compile(_SCRIPT_RE), 'script'),))
        again = TestProfile(((re.compile(_SCRIPT_RE), 'script'),))
        ignored = TestProfile(
            ((re.compile(_SCRIPT_RE, re.IGNORECASE), 'script'),))
        self.assertEqual(base, again)
        self.assertNotEqual(base, ignored)


class RefusalCodeTests(unittest.TestCase):
    def _rejected(self, entries, sample=None):
        with self.assertRaises(TestProfileError) as caught:
            TestProfile(entries)
        error = caught.exception
        self.assertEqual(error.CODE, 'TEST_PROFILE_INVALID')
        self.assertEqual(str(error), 'TEST_PROFILE_INVALID')
        if sample is not None:
            self.assertNotIn(sample, str(error))

    def test_empty_entry_list_is_refused(self):
        self._rejected(())

    def test_non_pattern_entry_is_refused(self):
        self._rejected((('scripts/test_[A-Za-z0-9_]+\\.py\\Z', 'script'),),
                       'scripts/test_')
        self._rejected((re.compile(_SCRIPT_RE),))
        self._rejected((re.compile(_SCRIPT_RE), 'script', 1))

    def test_kind_must_be_exactly_one_of_the_two_strings(self):
        self._rejected(((re.compile(_SCRIPT_RE), 'unittest '),),
                       'unittest ')
        self._rejected(((re.compile(_SCRIPT_RE), 'UNITS TEST'),),
                       'UNITS TEST')
        self._rejected(((re.compile(_SCRIPT_RE), 5),))
        permissive = type('Kind', (), {
            '__eq__': lambda self, other: other in ('unittest', 'script'),
        })()
        self._rejected(((re.compile(_SCRIPT_RE), permissive),),
                       'unittest')


if __name__ == '__main__':
    unittest.main()
