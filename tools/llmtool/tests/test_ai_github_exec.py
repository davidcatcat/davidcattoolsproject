import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr

from llmtool.ai_github_exec import (
    AiIdentityError,
    clear_verified_ai_env_cache,
    main,
    verified_ai_env,
)
from llmtool.github_identity import ai_login


class AiGithubExecTests(unittest.TestCase):
    def setUp(self):
        clear_verified_ai_env_cache()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.home = self.temp_dir.name
        (Path(self.home) / ".config" / "gh-ai").mkdir(parents=True)
        self.base_env = {"HOME": self.home, "PATH": "/usr/bin", "GH_TOKEN": "secret"}

    def tearDown(self):
        clear_verified_ai_env_cache()
        self.temp_dir.cleanup()

    def invoke(self, argv, runner=None, execer=None):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = main(
                argv,
                base_env=self.base_env,
                home=self.home,
                runner=runner,
                execer=execer,
            )
        return code, stderr.getvalue()

    def test_usage_refusal(self):
        for argv in ([], ["--"], ["--", ""]):
            with self.subTest(argv=argv):
                code, stderr = self.invoke(argv)
                self.assertEqual(code, 2)
                self.assertEqual(stderr, "AI_GH_USAGE\n")

    def test_missing_config_refusal(self):
        (Path(self.home) / ".config" / "gh-ai").rmdir()
        code, stderr = self.invoke(["--", "echo", "ok"])
        self.assertEqual(code, 2)
        self.assertEqual(stderr, "AI_GH_CONFIG_MISSING\n")

    def test_auth_mutation_and_credential_commands_are_refused(self):
        # auth status is an exact match, so any extra argument is refused.
        for argv in (
            ["--", "gh", "auth", "login"],
            ["--", "gh", "auth", "token"],
            ["--", "gh", "auth"],
            ["--", "gh", "auth", "status", "-t"],
            ["--", "gh", "auth", "status", "-at"],
            ["--", "gh", "auth", "status", "--show-token"],
            ["--", "gh", "auth", "status", "--active"],
            ["--", "gh", "auth", "status", "--hostname", "github.com"],
            ["--", "/usr/bin/gh", "auth", "switch", "--user", "owner"],
            ["--", "GH", "auth", "login"],
        ):
            with self.subTest(argv=argv):
                code, stderr = self.invoke(argv)
                self.assertEqual(code, 2)
                self.assertEqual(stderr, "AI_GH_AUTH_REFUSED\n")

    def test_plain_auth_status_launches(self):
        launched = []
        runner = lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 0, b"ddavidccatssun\n", b""
        )
        code, stderr = self.invoke(
            ["--", "gh", "auth", "status"], runner,
            lambda file, argv, env: launched.append((file, argv)),
        )
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(launched, [("gh", ["gh", "auth", "status"])])

    def test_relative_home_refusal(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = main(["--", "echo", "ok"], base_env=self.base_env,
                        home="relative/home")
        self.assertEqual(code, 2)
        self.assertEqual(stderr.getvalue(), "AI_GH_CONFIG_MISSING\n")

    def test_exec_failure_has_stable_refusal(self):
        runner = lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 0, b"ddavidccatssun\n", b""
        )

        def fail_exec(*args):
            raise OSError("private path detail")

        code, stderr = self.invoke(["--", "git", "status"], runner, fail_exec)
        self.assertEqual(code, 2)
        self.assertEqual(stderr, "AI_GH_EXEC_FAILED\n")
        self.assertNotIn("private path detail", stderr)

    def test_login_check_uses_isolated_environment_and_launches_command(self):
        seen = {}

        def runner(argv, **kwargs):
            seen["check_argv"] = argv
            seen["check_kwargs"] = kwargs
            return subprocess.CompletedProcess(argv, 0, b"ddavidccatssun\n", b"ignored")

        def execer(file, argv, env):
            seen["launch"] = (file, argv, env)

        code, stderr = self.invoke(["--", "git", "status", "--short"], runner, execer)
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(seen["check_argv"], ["gh", "api", "user", "--jq", ".login"])
        self.assertIs(seen["check_kwargs"]["stdin"], subprocess.DEVNULL)
        self.assertTrue(seen["check_kwargs"]["shell"] is False)
        self.assertEqual(seen["check_kwargs"]["timeout"], 30)
        self.assertEqual(seen["launch"][0], "git")
        self.assertEqual(seen["launch"][1], ["git", "status", "--short"])
        self.assertNotIn("GH_TOKEN", seen["launch"][2])
        self.assertEqual(seen["launch"][2]["GH_CONFIG_DIR"],
                         os.path.join(self.home, ".config", "gh-ai"))

    def test_login_failures_share_stable_refusal_without_leaking_output(self):
        cases = (
            lambda argv, **kwargs: subprocess.CompletedProcess(
                argv, 1, b"secret-account", b"private diagnostic"
            ),
            lambda argv, **kwargs: subprocess.CompletedProcess(
                argv, 0, b"owner-login\n", b"private diagnostic"
            ),
            lambda argv, **kwargs: (_ for _ in ()).throw(
                subprocess.TimeoutExpired(argv, 30, output=b"secret-account")
            ),
            lambda argv, **kwargs: (_ for _ in ()).throw(OSError("secret environment")),
        )
        for runner in cases:
            with self.subTest(runner=runner):
                code, stderr = self.invoke(["--", "git", "status"], runner)
                self.assertEqual(code, 2)
                self.assertEqual(stderr, "AI_GH_LOGIN_MISMATCH\n")
                self.assertNotIn("secret", stderr)
                self.assertNotIn("owner-login", stderr)

    def test_ai_identity_error_code(self):
        err = AiIdentityError("CUSTOM_CODE")
        self.assertEqual(err.code, "CUSTOM_CODE")

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
                "GH_TOKEN": "custom-gh-val",
                "GITHUB_TOKEN": "custom-github-val",
                "SSH_AUTH_SOCK": "/tmp/custom-sock",
                "CUSTOM_SETTING": "custom-val",
            }
            env = verified_ai_env(
                base_env=base_env,
                home=temp_dir,
                runner=success_runner,
            )
            self.assertEqual(env["CUSTOM_SETTING"], "custom-val")
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
