"""#2793 A13.0: CI profile name bounds.

Rejection tests with non-default fixtures, per the Coordinator technical
review: each profile field in turn refuses an invalid name with the fixed
error code, a name at the length limit is accepted while one character more
is refused, and a profile built only from non-default names keeps every
given name and differs from the default profile.
"""
import dataclasses
import unittest

from llmtool.ci_profile import CiProfile, CiProfileError, default_profile

_FIXED_CODE = 'CI_PROFILE_NAME_INVALID'

#: Valid, non-default fixture names, one per profile field in declared order.
_VALID = {
    'linux_code_check': 'nightly linux code check',
    'docs_check': 'nightly docs check',
    'gateway_check': 'nightly gateway check',
    'ai_loop_check': 'nightly ai-loop check',
    'macos_check': 'nightly macos check',
    'linux_workflow_file': 'nightly-linux-ci.yml',
    'macos_workflow_file': 'nightly-mac-ci.yml',
}


def _profile(**overrides):
    """Build a CiProfile from the non-default fixture names plus overrides."""
    names = dict(_VALID)
    names.update(overrides)
    return CiProfile(**names)


class PerFieldRefusalTests(unittest.TestCase):
    """An invalid name in only one field is refused with the fixed code."""

    _INVALID_NAMES = (
        ('empty', ''),
        ('non-printable', 'bad\nname'),
        ('over-100', 'a' * 101),
    )

    def test_fixture_covers_every_field_in_declared_order(self):
        field_names = tuple(field.name for field in dataclasses.fields(CiProfile))
        self.assertEqual(tuple(_VALID), field_names)

    def test_invalid_name_in_each_field_is_refused_with_fixed_code(self):
        for field_name in _VALID:
            for kind, bad in self._INVALID_NAMES:
                with self.subTest(field=field_name, kind=kind):
                    with self.assertRaises(CiProfileError) as caught:
                        _profile(**{field_name: bad})
                    self.assertEqual(caught.exception.code, _FIXED_CODE)


class LengthLimitTests(unittest.TestCase):
    """Exactly the longest accepted length passes; one character more fails."""

    def test_name_of_exactly_100_characters_is_accepted(self):
        name = 'x' * 100
        profile = _profile(linux_code_check=name)
        self.assertEqual(profile.linux_code_check, name)
        self.assertEqual(len(profile.linux_code_check), 100)

    def test_name_of_101_characters_is_refused(self):
        with self.assertRaises(CiProfileError) as caught:
            _profile(linux_code_check='x' * 101)
        self.assertEqual(caught.exception.code, _FIXED_CODE)


class NonDefaultProfileTests(unittest.TestCase):
    """A profile built only from non-default names keeps every given name."""

    def test_keeps_every_given_name(self):
        profile = _profile()
        for field_name, value in _VALID.items():
            with self.subTest(field=field_name):
                self.assertEqual(getattr(profile, field_name), value)

    def test_differs_from_default_profile(self):
        self.assertNotEqual(_profile(), default_profile())

    def test_every_field_differs_from_its_default(self):
        profile = _profile()
        default = default_profile()
        for field_name in _VALID:
            with self.subTest(field=field_name):
                self.assertNotEqual(getattr(profile, field_name),
                                    getattr(default, field_name))


if __name__ == '__main__':
    unittest.main()
