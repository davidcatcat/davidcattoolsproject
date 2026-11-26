"""#2793 H2b: default_profile matches the current CI names; construction refuses invalid names."""
import unittest

from llmtool.ci_profile import CiProfile, CiProfileError, default_profile


class DefaultProfileTests(unittest.TestCase):
    def test_default_equals_current_ci_names(self):
        self.assertEqual(default_profile(), CiProfile(
            linux_code_check='check (Linux 3.14)',
            docs_check='check (docs)',
            gateway_check='gateway tests (Linux 3.14)',
            ai_loop_check='ai-loop helper tests (Python 3.14)',
            macos_check='check (macOS 3.13)',
            linux_workflow_file='llmtool-linux-ci.yml',
            macos_workflow_file='llmtool-mac-ci.yml',
        ))

    def test_default_field_values(self):
        profile = default_profile()
        self.assertEqual(profile.linux_code_check, 'check (Linux 3.14)')
        self.assertEqual(profile.docs_check, 'check (docs)')
        self.assertEqual(profile.gateway_check, 'gateway tests (Linux 3.14)')
        self.assertEqual(profile.ai_loop_check, 'ai-loop helper tests (Python 3.14)')
        self.assertEqual(profile.macos_check, 'check (macOS 3.13)')
        self.assertEqual(profile.linux_workflow_file, 'llmtool-linux-ci.yml')
        self.assertEqual(profile.macos_workflow_file, 'llmtool-mac-ci.yml')


class ImmutabilityTests(unittest.TestCase):
    def test_profile_fields_cannot_be_changed(self):
        profile = default_profile()
        for name in ('linux_code_check', 'docs_check', 'gateway_check',
                     'ai_loop_check', 'macos_check',
                     'linux_workflow_file', 'macos_workflow_file'):
            with self.subTest(name=name):
                with self.assertRaises(AttributeError):
                    setattr(profile, name, 'renamed')


class RejectionTests(unittest.TestCase):
    def _refuse(self, value):
        with self.assertRaises(CiProfileError) as caught:
            CiProfile(
                linux_code_check=value,
                docs_check='ok',
                gateway_check='ok',
                ai_loop_check='ok',
                macos_check='ok',
                linux_workflow_file='ok',
                macos_workflow_file='ok',
            )
        return caught.exception

    def test_empty_name_refused(self):
        self.assertEqual(self._refuse('').code, 'CI_PROFILE_NAME_INVALID')

    def test_non_printable_name_refused(self):
        self.assertEqual(self._refuse('bad\nname').code, 'CI_PROFILE_NAME_INVALID')

    def test_over_100_character_name_refused(self):
        self.assertEqual(self._refuse('a' * 101).code, 'CI_PROFILE_NAME_INVALID')

    def test_non_string_name_refused(self):
        self.assertEqual(self._refuse(7).code, 'CI_PROFILE_NAME_INVALID')

    def test_refusal_message_has_fixed_code_and_no_input_value(self):
        cases = (
            ('', 'empty'),
            ('bad\nname', 'non-printable'),
            ('a' * 101, 'over-100'),
            (7, 'non-string'),
        )
        for value, kind in cases:
            with self.subTest(kind=kind):
                exception = self._refuse(value)
                self.assertEqual(exception.code, 'CI_PROFILE_NAME_INVALID')
                self.assertEqual(str(exception), 'ci profile name invalid')
                if isinstance(value, str) and value:
                    self.assertNotIn(value, str(exception))


if __name__ == '__main__':
    unittest.main()
