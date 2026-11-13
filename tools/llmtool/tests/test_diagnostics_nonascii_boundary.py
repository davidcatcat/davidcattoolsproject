"""#1138: diagnostics credential boundaries ignore non-ASCII neighbours.

A CJK letter or a non-ASCII letter that case-folds to ASCII (U+0130, U+0131,
U+017F, U+212A) next to a credential must not hide it. Every value below is
built at runtime from harmless parts; no source literal is credential-shaped.
"""

import base64
import json
import re
import unittest

from llmtool import diagnostics as d
from llmtool import test_output_excerpts as excerpts

REFUSAL = 'diagnostic text contains recognizable credential material'
NEIGHBOURS = ('中', 'İ', 'ı', 'ſ', 'K', ' ')
KEYWORDS = ('api_key', 'api-key', 'apikey', 'password', 'token', 'secret',
            'access_token', 'client_secret', 'aws_secret_access_key')


def _cat(*parts):
    return ''.join(parts)


def _basic_value():
    # Nine bytes encode to twelve characters without '=' padding, so a
    # following case-folding letter joins the captured value.
    return base64.b64encode(b'ab' + b':' + b'cdefgh').decode('ascii')


def _families():
    yield 'private_key', _cat('-' * 5, 'BEG', 'IN', ' ', 'PRIVATE', ' ', 'KEY', '-' * 5)
    yield 'cloud_key', _cat('AK', 'IA', 'Q' * 16)
    yield 'cloud_key_session', _cat('AS', 'IA', '7' * 16)
    yield 'sk', _cat('s', 'k-', 'q' * 16)
    yield 'github', _cat('gh', 'p_', 'q' * 24)
    yield 'github_pat', _cat('github', '_pat_', 'q' * 24)
    yield 'slack', _cat('xo', 'xb-', 'q' * 24)
    yield 'jwt', _cat('ey', 'J', 'q' * 10, '.', 'q' * 10, '.', 'q' * 10)
    yield 'bearer', _cat('Bea', 'rer', ' ', 'q' * 12)
    yield 'basic', _cat('Ba', 'sic', ' ', _basic_value())
    for keyword in KEYWORDS:
        yield 'assign_' + keyword, _cat(keyword, '=', 'q' * 10)
        yield 'colon_' + keyword, _cat(keyword, ':', ' ', 'q' * 10)


class NonAsciiBoundaryTests(unittest.TestCase):
    def assert_refused(self, text):
        self.assertTrue(d._contains_credential_value(text))
        for create in (lambda: d.safe_diagnostic_text(text),
                       lambda: d.diagnostic_json({'message': text})):
            with self.assertRaises(ValueError) as caught:
                create()
            self.assertEqual(str(caught.exception), REFUSAL)
            self.assertNotIn(text, str(caught.exception))

    def test_every_family_refused_alone(self):
        for name, value in _families():
            with self.subTest(family=name):
                self.assert_refused(value)

    def test_every_family_refused_after_non_ascii_or_space(self):
        for name, value in _families():
            for neighbour in NEIGHBOURS:
                with self.subTest(family=name, before=hex(ord(neighbour))):
                    self.assert_refused(neighbour + value)

    def test_every_family_refused_before_non_ascii_or_space(self):
        for name, value in _families():
            for neighbour in NEIGHBOURS:
                with self.subTest(family=name, after=hex(ord(neighbour))):
                    self.assert_refused(value + neighbour)

    def test_every_family_refused_between_non_ascii_neighbours(self):
        for name, value in _families():
            for neighbour in NEIGHBOURS:
                with self.subTest(family=name, around=hex(ord(neighbour))):
                    self.assert_refused(neighbour + value + neighbour)

    def test_ascii_word_neighbours_still_bound_the_rules(self):
        # The look-arounds still treat ASCII letters, digits and '_' as part
        # of the surrounding word, so these are not recognizable values.
        values = (
            _cat('x', 'AK', 'IA', 'Q' * 16),
            _cat('AK', 'IA', 'Q' * 17),
            _cat('AK', 'IA', 'Q' * 16, '_'),
            _cat('x', 's', 'k-', 'q' * 16),
            _cat('9', 'gh', 'p_', 'q' * 24),
            _cat('_', 'xo', 'xb-', 'q' * 24),
            _cat('x', 'ey', 'J', 'q' * 10, '.', 'q' * 10, '.', 'q' * 10),
            _cat('x', 'Bea', 'rer', ' ', 'q' * 12),
            _cat('x', 'Ba', 'sic', ' ', _basic_value()),
            _cat('x', 'to', 'ken', '=', 'q' * 10),
        )
        for value in values:
            with self.subTest(length=len(value)):
                self.assertFalse(d._contains_credential_value(value))
                self.assertEqual(d.safe_diagnostic_text(value), value)

    def test_status_vocabulary_after_non_ascii_neighbour_still_allowed(self):
        for neighbour in NEIGHBOURS[:-1]:
            for text in (_cat(neighbour, 'to', 'ken', '=', 'missing'),
                         _cat(neighbour, 'Bea', 'rer', ' ', 'redacted'),
                         _cat(neighbour, 'Ba', 'sic', ' ', 'placeholder')):
                with self.subTest(neighbour=hex(ord(neighbour))):
                    self.assertEqual(d.safe_diagnostic_text(text), text)
                    self.assertEqual(json.loads(d.diagnostic_json({'message': text}))['message'], text)

    def test_cloud_key_rule_is_case_sensitive(self):
        # Lowercase or mixed-case prefixes and lowercase bodies are not
        # cloud keys, and neither are non-ASCII lookalikes that IGNORECASE
        # would fold to ASCII uppercase (U+0130, U+0131, U+017F, U+212A).
        body = 'Q' * 16
        values = (
            _cat('ak', 'ia', body),
            _cat('as', 'ia', body),
            _cat('Ak', 'ia', body),
            _cat('AK', 'IA', 'q' * 16),
            _cat('AS', 'IA', 'Q' * 15, 'q'),
            _cat('A', 'K', 'IA', body),
            _cat('AK', 'ı', 'A', body),
            _cat('AK', 'İ', 'A', body),
            _cat('A', 'ſ', 'IA', body),
            _cat('AK', 'IA', 'Q' * 15, 'K'),
            _cat('AK', 'IA', 'ſ' * 16),
        )
        for value in values:
            for text in (value, _cat('中', value, '中'), _cat('note ', value)):
                with self.subTest(length=len(text), first=hex(ord(text[0]))):
                    self.assertIsNone(d._CREDENTIAL.search(text))
                    self.assertFalse(d._contains_credential_value(text))
                    self.assertEqual(d.safe_diagnostic_text(text), text)

    def test_uppercase_cloud_keys_still_refused(self):
        for value in (_cat('AK', 'IA', 'Q' * 8, '7' * 8),
                      _cat('AS', 'IA', '0' * 16),
                      _cat('AK', 'IA', 'Z' * 16)):
            for text in (value, _cat('note ', value, ' end'),
                         _cat('ı', value, 'K')):
                with self.subTest(length=len(text)):
                    self.assert_refused(text)

    def test_cloud_key_rule_is_a_case_sensitive_group(self):
        self.assertTrue(d._DIAGNOSTIC_CLOUD_KEY.startswith('(?-i:'))
        self.assertTrue(d._DIAGNOSTIC_CLOUD_KEY.endswith(')'))
        self.assertTrue(d._CREDENTIAL.flags & re.IGNORECASE)

    def test_patterns_use_ascii_look_arounds_and_local_cloud_rule(self):
        for pattern in (d._CREDENTIAL, d._AUTH_VALUE, d._CREDENTIAL_ASSIGNMENT):
            with self.subTest(pattern=pattern.pattern[:24]):
                self.assertNotIn('\\b', pattern.pattern)
                self.assertIn('(?<!(?-i:[A-Za-z0-9_]))', pattern.pattern)
                self.assertTrue(pattern.flags & re.IGNORECASE)
        self.assertIn('(?!(?-i:[A-Za-z0-9_]))', d._CREDENTIAL.pattern)
        self.assertIn(d._DIAGNOSTIC_CLOUD_KEY, d._CREDENTIAL.pattern)
        self.assertNotIn(excerpts._CLOUD_KEY, d._CREDENTIAL.pattern)


if __name__ == '__main__':
    unittest.main()
