"""#1230 refusal-code pin: repository_config fails closed with TRUST_REPOSITORY_CONFIG_INVALID."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool.config import RepositoryConfigError, repository_config
from llmtool.errors import LLMToolError


class RepositoryConfigCodeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir='/private/tmp' if sys.platform == 'darwin' else None)
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.user = self.home/'.config/llmtool/config.yaml'
        self.user.parent.mkdir(parents=True)

    def code(self):
        with self.assertRaises(RepositoryConfigError) as caught:
            repository_config(self.home)
        return caught.exception.code

    def test_unreadable_user_config_is_invalid(self):
        self.user.write_text('generation: [unclosed\n', encoding='utf-8')
        self.assertEqual(self.code(), 'TRUST_REPOSITORY_CONFIG_INVALID')

    def test_malformed_trust_repository_is_invalid(self):
        for label, trusted in (
                ('no repository', object()),
                ('no approval refs', type('Trust', (), {'repository': {'slug': 'owner/repo'}})()),
                ('repository not a dict', type('Trust', (), {'repository': None})())):
            with self.subTest(label=label), patch('llmtool.trust.load_trust', return_value=trusted):
                self.assertEqual(self.code(), 'TRUST_REPOSITORY_CONFIG_INVALID')
        with patch('llmtool.trust.load_trust', side_effect=LLMToolError('synthetic')):
            self.assertEqual(self.code(), 'TRUST_REPOSITORY_CONFIG_INVALID')


if __name__ == '__main__':
    unittest.main()
