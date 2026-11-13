"""#1138: the PM stream hold keeps a value held right after non-ASCII text.

Every value is built at runtime from harmless parts; no source literal is
credential-shaped.
"""

import unittest

from llmtool import pm_runtime

FILL = 'Q' * 24
# CJK text, and non-ASCII letters that match ASCII letters under IGNORECASE.
NEIGHBOURS = ('\u4e2d\u6587', '\u0130', '\u0131', '\u017f', '\u212a')
SCHEMES = ('bearer', 'basic')


def _values():
    return ('sk-' + FILL, 'ghp_' + FILL, 'github' + '_pat_' + FILL,
            'xoxb-' + FILL, 'eyJ' + FILL + '.' + FILL)


def _scheme_values():
    return tuple(scheme + ' ' + FILL for scheme in SCHEMES)


class StreamHoldNonAsciiTests(unittest.TestCase):
    def test_value_after_non_ascii_text_is_held(self):
        for left in NEIGHBOURS:
            for value in _values():
                with self.subTest(left=hex(ord(left[0])), family=value[:3]):
                    self.assertEqual(pm_runtime._stream_safe_end(left + value), len(left))

    def test_auth_scheme_after_non_ascii_text_matches(self):
        for left in NEIGHBOURS:
            for scheme in SCHEMES:
                with self.subTest(left=hex(ord(left[0])), scheme=scheme):
                    match = pm_runtime._STREAM_SECRET_START.search(left + scheme + ' ' + FILL)
                    self.assertIsNotNone(match)
                    self.assertEqual(match.start(), len(left))

    def test_ascii_word_prefix_is_not_held(self):
        for prefix in ('ma', 'x', 'my', 'pre', '9', '_'):
            for value in _values() + _scheme_values():
                text = prefix + value
                with self.subTest(prefix=prefix, family=value[:3]):
                    self.assertIsNone(pm_runtime._STREAM_SECRET_START.search(text))
                    self.assertEqual(pm_runtime._stream_safe_end(text), len(text))


if __name__ == '__main__':
    unittest.main()
