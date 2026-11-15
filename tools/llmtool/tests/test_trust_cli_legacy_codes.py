"""#1230 refusal-code pin: trust_cli legacy source resolution fails closed with TRUST_LEGACY_UNAVAILABLE."""
import unittest
from unittest import mock

from llmtool import trust_cli
from llmtool.trust import TrustError


class LegacySourceCodeTests(unittest.TestCase):
    def code(self):
        with self.assertRaises(TrustError) as caught:
            trust_cli._legacy_sources()
        return caught.exception.code

    def test_missing_or_failing_legacy_module_is_unavailable(self):
        with mock.patch.object(trust_cli.importlib.util, 'spec_from_file_location', return_value=None):
            self.assertEqual(self.code(), 'TRUST_LEGACY_UNAVAILABLE')
        loaderless = mock.Mock(loader=None)
        with mock.patch.object(trust_cli.importlib.util, 'spec_from_file_location', return_value=loaderless):
            self.assertEqual(self.code(), 'TRUST_LEGACY_UNAVAILABLE')
        with mock.patch.object(trust_cli.importlib.util, 'module_from_spec', side_effect=RuntimeError('private')):
            self.assertEqual(self.code(), 'TRUST_LEGACY_UNAVAILABLE')


if __name__ == '__main__':
    unittest.main()
