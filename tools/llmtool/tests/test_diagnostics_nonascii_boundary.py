"""#1138: diagnostics rejects credentials placed directly next to non-ASCII text."""

import unittest

from llmtool import diagnostics as d

CJK = '中文'
FILL = 'Q' * 24


class CredentialAfterNonAsciiTests(unittest.TestCase):
    def test_credential_after_cjk_text_is_rejected(self):
        scheme = 'BEARER'.lower()
        key = 'api' + '_key'
        jwt = '.'.join(('eyJ' + FILL, FILL, FILL))
        # Under re.IGNORECASE an ASCII class also matches some non-ASCII letters.
        for near in (CJK, '\u0130', '\u0131', '\u017f', '\u212a'):
            for text in (near + 'sk-' + FILL, near + 'ghp_' + FILL, near + 'xoxb-' + FILL,
                         near + jwt, jwt + near, near + scheme + ' ' + FILL,
                         near + key + '=' + FILL):
                with self.subTest(near=ord(near[0]), case=len(text)):
                    with self.assertRaises(ValueError):
                        d.safe_diagnostic_text(text)

    def test_ascii_word_prefix_still_not_a_credential(self):
        for text in ('mask-' + FILL, 'xghp_' + FILL, 'myxoxb-' + FILL,
                     'pre' + 'api' + '_key' + '=' + FILL):
            with self.subTest(case=len(text)):
                self.assertEqual(d.safe_diagnostic_text(text), text)


if __name__ == '__main__':
    unittest.main()