"""#1138: diagnostics rejects credentials placed directly next to non-ASCII text.

Sensitive-detector tests (#763/#981 exception): every credential-shaped value
here is SYNTHETIC, assembled at runtime from parts and never a real credential.
"""

import unittest

from llmtool import diagnostics as d
from llmtool import test_output_excerpts as excerpts

CJK = '中文'
FILL = 'Q' * 24
# Under re.IGNORECASE an ASCII class also matches some non-ASCII letters
# (U+0130, U+0131, U+017F, U+212A); CJK is a non-ASCII word; space is a control.
NEIGHBOURS = (CJK, 'İ', 'ı', 'ſ', 'K', ' ')


def _cloud_keys():
    # Synthetic cloud-key shapes: 4-letter prefix + 16 uppercase/digit chars.
    return [prefix + FILL[:16] for prefix in ('AK' + 'IA', 'AS' + 'IA')]


def _families():
    scheme = 'BEARER'.lower()
    key = 'api' + '_key'
    jwt = '.'.join(('eyJ' + FILL, FILL, FILL))
    return (['sk-' + FILL, 'ghp_' + FILL, 'xoxb-' + FILL, jwt,
             scheme + ' ' + FILL, key + '=' + FILL] + _cloud_keys())


class CredentialAfterNonAsciiTests(unittest.TestCase):
    def test_credential_next_to_non_ascii_or_space_is_rejected(self):
        for near in NEIGHBOURS:
            for index, value in enumerate(_families()):
                for text in (near + value, value + near, near + value + near,
                             near + value + ' '):
                    with self.subTest(near=ord(near[0]), family=index, case=len(text)):
                        with self.assertRaises(ValueError):
                            d.safe_diagnostic_text(text)

    def test_ascii_word_prefix_still_not_a_credential(self):
        for text in ('mask-' + FILL, 'xghp_' + FILL, 'myxoxb-' + FILL,
                     'pre' + 'api' + '_key' + '=' + FILL):
            with self.subTest(case=len(text)):
                self.assertEqual(d.safe_diagnostic_text(text), text)

    def test_cloud_key_inside_ascii_word_is_not_a_credential(self):
        for value in _cloud_keys():
            for text in ('x' + value, value + 'x', '_' + value, value + '7',
                         value + '_', 'Z' + value + 'Z'):
                with self.subTest(case=text[:2] + text[-1:]):
                    self.assertEqual(d.safe_diagnostic_text(text), text)


class ExcerptCloudKeyBoundaryTests(unittest.TestCase):
    """The shared _CLOUD_KEY also gates captured test-output excerpts."""

    def test_cloud_key_next_to_non_ascii_or_space_is_screened(self):
        for near in NEIGHBOURS:
            for value in _cloud_keys():
                for text in (near + value, value + near, near + value + near,
                             near + value + ' ', 'ok ' + near + value + ' end'):
                    with self.subTest(near=ord(near[0]), case=len(text)):
                        self.assertTrue(excerpts._sensitive(text))
                        self.assertTrue(excerpts._emitted_json_sensitive(text))

    def test_cloud_key_inside_ascii_word_is_not_screened(self):
        for value in _cloud_keys():
            for text in ('x' + value, value + 'x', '_' + value, value + '7'):
                with self.subTest(case=text[:2] + text[-1:]):
                    self.assertFalse(excerpts._sensitive(text))


if __name__ == '__main__':
    unittest.main()
