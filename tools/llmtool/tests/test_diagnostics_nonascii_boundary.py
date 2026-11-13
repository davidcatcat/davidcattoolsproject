"""#1138: diagnostics rejects credentials placed directly next to non-ASCII text.

Sensitive-detector tests (#763/#981 exception): every credential-shaped value
here is SYNTHETIC, assembled at runtime from parts and never a real credential.
"""

import ast
import unittest
from pathlib import Path

from llmtool import diagnostics as d

CJK = '中文'
FILL = 'Q' * 24
# Under re.IGNORECASE an ASCII class also matches some non-ASCII letters
# (U+0130, U+0131, U+017F, U+212A); CJK is a non-ASCII word; space is a control.
NEIGHBOURS = (CJK, 'İ', 'ı', 'ſ', 'K', ' ')


def _cat(*parts):
    # Joins harmless parts at runtime, so no credential-shaped literal or
    # constant-foldable expression appears in this source (#763 A).
    return ''.join(parts)


def _cloud_keys():
    # Synthetic cloud-key shapes: 4-letter prefix + 16 uppercase/digit chars.
    # synthetic-fixture: screen-tool test
    return [_cat(prefix, FILL[:16]) for prefix in (_cat('AK', 'IA'), _cat('AS', 'IA'))]


def _families():
    scheme = _cat('bear', 'er')
    # synthetic-fixture: screen-tool test
    key = _cat('api', '_key')
    # synthetic-fixture: screen-tool test
    jwt = _cat(_cat('eyJ', FILL), '.', FILL, '.', FILL)
    dashes = _cat('-', '-', '-', '-', '-')
    # synthetic-fixture: screen-tool test
    header = _cat(dashes, 'BEGIN ', 'PRIVATE', ' KEY', dashes)
    # synthetic-fixture: screen-tool test
    return ([_cat('sk-', FILL), _cat('ghp_', FILL), _cat('xoxb-', FILL), jwt,
             # synthetic-fixture: screen-tool test
             _cat(scheme, ' ', FILL), _cat(key, '=', FILL), header] + _cloud_keys())


def _placements(near, value):
    return (near + value, value + near, near + value + near,
            near + value + ' ', 'ok ' + near + value + ' end',
            'ok ' + value + near + ' end')


class CredentialAfterNonAsciiTests(unittest.TestCase):
    def test_credential_next_to_non_ascii_or_space_is_rejected(self):
        for near in NEIGHBOURS:
            for index, value in enumerate(_families()):
                for case, text in enumerate(_placements(near, value)):
                    with self.subTest(near=ord(near[0]), family=index, case=case):
                        with self.assertRaises(ValueError):
                            d.safe_diagnostic_text(text)

    def test_cloud_key_next_to_non_ascii_or_space_is_rejected_everywhere(self):
        # The same screen gates error messages and serialized event data.
        for near in NEIGHBOURS:
            for value in _cloud_keys():
                for case, text in enumerate(_placements(near, value)):
                    with self.subTest(near=ord(near[0]), case=case):
                        with self.assertRaises(ValueError):
                            d.diagnostic_json({'message': text})
                        with self.assertRaises(ValueError):
                            d.DiagnosticError(code=d.ErrorCode.PROVIDER_TIMEOUT,
                                              message=text, component='provider.openai')

    def test_ascii_word_prefix_still_not_a_credential(self):
        # synthetic-fixture: screen-tool test
        for text in (_cat('mask-', FILL), _cat('xghp_', FILL), _cat('myxoxb-', FILL),
                     # synthetic-fixture: screen-tool test
                     _cat('pre', 'api', '_key', '=', FILL)):
            with self.subTest(case=len(text)):
                self.assertEqual(d.safe_diagnostic_text(text), text)

    def test_cloud_key_inside_ascii_word_is_not_a_credential(self):
        for value in _cloud_keys():
            # synthetic-fixture: screen-tool test
            for text in ('x' + value, value + 'x', '_' + value, value + '7',
                         value + '_', 'Z' + value + 'Z'):
                with self.subTest(case=text[:2] + text[-1:]):
                    self.assertEqual(d.safe_diagnostic_text(text), text)


class CloudKeyPatternOwnershipTests(unittest.TestCase):
    """diagnostics owns its cloud-key pattern; the excerpt screen is untouched."""

    def test_pattern_uses_case_sensitive_ascii_lookarounds_not_word_boundary(self):
        self.assertNotIn(r'\b', d._CLOUD_KEY)
        self.assertTrue(d._CLOUD_KEY.startswith(r'(?:(?<!(?-i:[A-Za-z0-9_]))'))
        self.assertTrue(d._CLOUD_KEY.endswith(r'(?!(?-i:[A-Za-z0-9_])))'))
        self.assertIn(d._CLOUD_KEY, d._CREDENTIAL.pattern)

    def test_diagnostics_does_not_import_the_excerpt_module(self):
        tree = ast.parse(Path(d.__file__).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, 'test_output_excerpts')
                self.assertNotIn('test_output_excerpts',
                                 [alias.name for alias in node.names])
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn('test_output_excerpts', alias.name)


if __name__ == '__main__':
    unittest.main()
