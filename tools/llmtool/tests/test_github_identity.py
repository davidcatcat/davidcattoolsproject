from dataclasses import FrozenInstanceError
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from llmtool.github_identity import (
    AiIdentityError,
    GitHubIdentity,
    GitHubIdentityError,
    ai_gh_config_dir,
    ai_github_env,
    ai_login,
    clear_verified_ai_env_cache,
    github_identity,
    owner_login,
    verified_ai_env,
)


class GitHubIdentityTests(unittest.TestCase):
    def tearDown(self):
        clear_verified_ai_env_cache()

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

    def test_ai_github_env_removes_inherited_github_and_git_overrides(self):
        base_env = {
            "PATH": "/bin",
            "GH_TOKEN": "secret-gh",
            "GH_CONFIG_DIR": "/owner/gh",
            "GITHUB_TOKEN": "secret-github",
            "GIT_CONFIG_PARAMETERS": "secret-parameters",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "unsafe.key",
            "GIT_CONFIG_VALUE_0": "unsafe-value",
            "GIT_ASKPASS": "/askpass",
            "SSH_ASKPASS": "/ssh-askpass",
            "SSH_AUTH_SOCK": "/tmp/ssh-agent.sock",
            "GIT_SSH_COMMAND": "ssh unsafe",
            "GIT_SSH": "/usr/bin/ssh",
            "GIT_SSH_VARIANT": "ssh",
            "GIT_CONFIG_GLOBAL": "/unsafe/global",
            "GIT_CONFIG_SYSTEM": "/unsafe/system",
            "GIT_CURL_VERBOSE": "1",
            "GIT_TRACE": "/tmp/git-trace",
            "GIT_TRACE_SETUP": "1",
            "KEEP": "value",
        }
        original = dict(base_env)

        result = ai_github_env(base_env, "/Users/example")

        self.assertEqual(base_env, original)
        self.assertEqual(result["PATH"], "/bin")
        self.assertEqual(result["KEEP"], "value")
        self.assertEqual(result["GH_CONFIG_DIR"], "/Users/example/.config/gh-ai")
        self.assertEqual(result["GIT_CONFIG_COUNT"], "3")
        self.assertEqual(result["GIT_CONFIG_KEY_0"], "credential.helper")
        self.assertEqual(result["GIT_CONFIG_VALUE_0"], "")
        self.assertEqual(result["GIT_CONFIG_KEY_1"], "credential.helper")
        self.assertEqual(result["GIT_CONFIG_VALUE_1"], "!gh auth git-credential")
        self.assertEqual(
            result["GIT_CONFIG_KEY_2"],
            "url.https://github.com/.insteadOf",
        )
        self.assertEqual(result["GIT_CONFIG_VALUE_2"], "git@github.com:")
        self.assertFalse(any(name.startswith(("GH_", "GITHUB_"))
                             for name in result if name != "GH_CONFIG_DIR"))
        self.assertNotIn("GIT_CONFIG_PARAMETERS", result)
        self.assertNotIn("GIT_ASKPASS", result)
        self.assertNotIn("SSH_ASKPASS", result)
        self.assertNotIn("SSH_AUTH_SOCK", result)
        self.assertNotIn("GIT_SSH_COMMAND", result)
        for name in ("GIT_SSH", "GIT_SSH_VARIANT", "GIT_CONFIG_GLOBAL",
                     "GIT_CONFIG_SYSTEM", "GIT_CURL_VERBOSE", "GIT_TRACE",
                     "GIT_TRACE_SETUP"):
            self.assertNotIn(name, result)

    def test_verified_ai_env_missing_config_folder_refuses(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(AiIdentityError) as caught:
                verified_ai_env(base_env={"HOME": temp_dir}, home=temp_dir)
            self.assertEqual(caught.exception.code, "AI_GH_CONFIG_MISSING")

    def test_verified_ai_env_relative_home_refuses(self):
        with self.assertRaises(AiIdentityError) as caught:
            verified_ai_env(base_env={"HOME": "/tmp"}, home="relative/home")
        self.assertEqual(caught.exception.code, "AI_GH_CONFIG_MISSING")

    def test_verified_ai_env_gh_error_refuses(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / ".config" / "gh-ai").mkdir(parents=True)
            failing_runner = lambda argv, **kwargs: subprocess.CompletedProcess(
                argv, 1, stdout=b"", stderr=b"error"
            )
            with self.assertRaises(AiIdentityError) as caught:
                verified_ai_env(
                    base_env={"HOME": temp_dir},
                    home=temp_dir,
                    runner=failing_runner,
                )
            self.assertEqual(caught.exception.code, "AI_GH_LOGIN_MISMATCH")

            def os_error_runner(argv, **kwargs):
                raise OSError("gh process error")

            with self.assertRaises(AiIdentityError) as caught:
                verified_ai_env(
                    base_env={"HOME": temp_dir},
                    home=temp_dir,
                    runner=os_error_runner,
                )
            self.assertEqual(caught.exception.code, "AI_GH_LOGIN_MISMATCH")

    def test_verified_ai_env_wrong_login_refuses(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / ".config" / "gh-ai").mkdir(parents=True)
            wrong_login_runner = lambda argv, **kwargs: subprocess.CompletedProcess(
                argv, 0, stdout=b"davidcatcat\n", stderr=b""
            )
            with self.assertRaises(AiIdentityError) as caught:
                verified_ai_env(
                    base_env={"HOME": temp_dir},
                    home=temp_dir,
                    runner=wrong_login_runner,
                )
            self.assertEqual(caught.exception.code, "AI_GH_LOGIN_MISMATCH")

    def test_verified_ai_env_success_strips_credentials_and_returns_env(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / ".config" / "gh-ai").mkdir(parents=True)
            success_runner = lambda argv, **kwargs: subprocess.CompletedProcess(
                argv, 0, stdout=f"{ai_login()}\n".encode("utf-8"), stderr=b""
            )
            base_env = {
                "HOME": temp_dir,
                "PATH": "/bin",
                "GH_TOKEN": "test-gh-token",
                "GITHUB_TOKEN": "test-github-token",
                "SSH_AUTH_SOCK": "/tmp/test.sock",
                "CUSTOM_SETTING": "test-setting",
            }
            env = verified_ai_env(
                base_env=base_env,
                home=temp_dir,
                runner=success_runner,
            )
            self.assertEqual(env["CUSTOM_SETTING"], "test-setting")
            self.assertEqual(
                env["GH_CONFIG_DIR"],
                str(Path(temp_dir) / ".config" / "gh-ai"),
            )
            self.assertNotIn("GH_TOKEN", env)
            self.assertNotIn("GITHUB_TOKEN", env)
            self.assertNotIn("SSH_AUTH_SOCK", env)

    def test_verified_ai_env_caches_success_and_skips_subsequent_gh(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / ".config" / "gh-ai").mkdir(parents=True)
            calls = []

            def tracking_runner(argv, **kwargs):
                calls.append(argv)
                return subprocess.CompletedProcess(
                    argv, 0, stdout=f"{ai_login()}\n".encode("utf-8"), stderr=b""
                )

            env1 = verified_ai_env(
                base_env={"HOME": temp_dir},
                home=temp_dir,
                runner=tracking_runner,
            )
            self.assertEqual(len(calls), 1)

            def failing_runner(argv, **kwargs):
                raise AssertionError("gh should not be called when cached")

            env2 = verified_ai_env(
                base_env={"HOME": temp_dir},
                home=temp_dir,
                runner=failing_runner,
            )
            self.assertEqual(len(calls), 1)
            self.assertEqual(env1, env2)


if __name__ == "__main__":
    unittest.main()
