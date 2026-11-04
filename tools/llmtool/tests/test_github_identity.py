from dataclasses import FrozenInstanceError
from pathlib import Path
import unittest
from unittest.mock import patch

from llmtool.github_identity import (
    GitHubIdentity,
    GitHubIdentityError,
    ai_gh_config_dir,
    ai_login,
    github_identity,
    owner_login,
)


class GitHubIdentityTests(unittest.TestCase):
    def test_configured_values(self):
        identity = github_identity("/Users/example")
        self.assertEqual(owner_login(), "davidcatcat")
        self.assertEqual(ai_login(), "ddavidccatssun")
        self.assertEqual(identity.owner_login, "davidcatcat")
        self.assertEqual(identity.ai_login, "ddavidccatssun")
        self.assertEqual(
            ai_gh_config_dir("/Users/example"),
            "/Users/example/.config/gh-ai",
        )
        self.assertEqual(
            identity.ai_gh_config_dir,
            "/Users/example/.config/gh-ai",
        )

    def test_relative_home_is_refused(self):
        with self.assertRaises(ValueError):
            ai_gh_config_dir("relative/home")

    def test_logins_are_distinct(self):
        identity = github_identity("/Users/example")
        self.assertNotEqual(identity.owner_login, identity.ai_login)

    def test_invalid_or_duplicate_logins_raise_coded_error(self):
        for logins in (("bad login", "ddavidccatssun"), ("same", "same")):
            with self.subTest(logins=logins):
                with patch("llmtool.github_identity._GITHUB_LOGINS", logins):
                    with self.assertRaises(GitHubIdentityError) as caught:
                        github_identity("/Users/example")
                self.assertEqual(caught.exception.code, "GITHUB_IDENTITY_INVALID")

    def test_identity_is_frozen(self):
        identity = github_identity("/Users/example")
        self.assertIsInstance(identity, GitHubIdentity)
        with self.assertRaises(FrozenInstanceError):
            identity.owner_login = "changed"

    def test_module_source_has_no_process_or_file_access(self):
        source_path = Path(__file__).parents[1] / "llmtool" / "github_identity.py"
        source = source_path.read_text(encoding="utf-8")
        for forbidden in ("subprocess", "os.environ", "open("):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
