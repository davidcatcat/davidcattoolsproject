import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from llmtool.config import resolve_config
from llmtool.configure import run_configure
from llmtool.errors import LLMToolError
from llmtool.providers import PROVIDER_REGISTRY, create_client, get_provider_capabilities
from llmtool.providers.codex_cli import CodexCLIProvider


CAPABILITIES = {
    "chat": True, "large_context": False, "reasoning_control": False,
    "streaming": False, "code_edit": False, "shell": False, "git": False,
}


class CodexCLIProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.capture = self.root / "capture.json"
        self.executable = self.root / "fake-codex"
        self.provider = {
            "type": "codex-cli", "model": "test-model", "timeout_seconds": 5,
            "base_url": None, "api_key": None, "api_key_env": None,
            "provider_options": {},
        }
        self.fake()
        self.which = patch("llmtool.providers.codex_cli.shutil.which",
                           return_value=str(self.executable))
        self.which_mock = self.which.start()
        self.addCleanup(self.which.stop)

    def fake(self, mode="success"):
        script = '''#!%s
import json, os, subprocess, sys, time
from pathlib import Path
prompt = sys.stdin.read()
capture = {"argv": sys.argv[1:], "prompt": prompt, "cwd": os.getcwd(),
           "contents": sorted(os.listdir(".")), "env": dict(os.environ)}
mode = %r
if mode == "mutation":
    Path("source.txt").write_text("attempted edit")
    Path("AGENTS.md").write_text("attempted instructions")
    git = subprocess.run(["/usr/bin/git", "status", "--porcelain"],
                         capture_output=True)
    capture["git_status"] = git.returncode
Path(%r).write_text(json.dumps(capture))
if mode == "timeout":
    time.sleep(5)
elif mode == "descendant":
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
elif mode == "large":
    sys.stdout.write("x" * 4096)
elif mode == "large-stderr":
    sys.stderr.write("x" * 4096)
elif mode == "empty":
    pass
elif mode == "invalid":
    sys.stdout.buffer.write(b"\\xff")
elif mode == "failure":
    sys.stderr.write("PRIVATE_DIAGNOSTIC " + prompt + " SECRET_TOKEN")
    sys.exit(7)
else:
    sys.stderr.write("PRIVATE_DIAGNOSTIC SECRET_TOKEN hidden reasoning")
    sys.stdout.write("  CODEX_FINAL  \\n")
''' % (sys.executable, mode, str(self.capture))
        self.executable.write_text(script)
        self.executable.chmod(0o700)

    def client(self, **updates):
        return CodexCLIProvider({**self.provider, **updates},
                                {"temperature": 0, "max_tokens": 100})

    def complete(self, **updates):
        return self.client(**updates).complete([{"role": "user", "content": "private prompt"}])

    def captured(self):
        return json.loads(self.capture.read_text())

    def test_coded_runtime_sites_use_one_fake_process_and_no_private_data(self):
        cases = (("timeout", "PROVIDER_TIMEOUT"), ("failure", "PROVIDER_RUNTIME_ERROR"),
                 ("large", "MODEL_RESPONSE_TOO_LARGE"), ("empty", "MODEL_RESPONSE_INVALID"),
                 ("invalid", "MODEL_RESPONSE_INVALID"))
        for mode, code in cases:
            self.fake(mode)
            with self.subTest(mode=mode), patch("llmtool.providers.codex_cli.OUTPUT_LIMIT", 128), \
                 patch("llmtool.providers.codex_cli.subprocess.Popen", wraps=subprocess.Popen) as spawn:
                with self.assertRaises(LLMToolError) as caught:
                    self.complete(timeout_seconds=0.2 if mode == "timeout" else 5)
                diagnostic = caught.exception.diagnostic
                self.assertEqual(diagnostic.code.value, code)
                self.assertFalse(diagnostic.fallback_allowed)
                self.assertNotIn("PRIVATE_DIAGNOSTIC", diagnostic.to_json())
                if mode == "failure":
                    self.assertEqual(diagnostic.safe_cause["process_status"], 7)
                spawn.assert_called_once()
        with patch("llmtool.providers.codex_cli.shutil.which", return_value=None), \
             patch("llmtool.providers.codex_cli.subprocess.Popen") as spawn:
            with self.assertRaises(LLMToolError) as caught:
                self.complete()
            self.assertEqual(caught.exception.diagnostic.code.value, "ENV_PROVIDER_RUNTIME_MISSING")
            spawn.assert_not_called()

    def test_registration_factory_and_capabilities(self):
        self.assertIn("codex-cli", PROVIDER_REGISTRY.names())
        self.assertEqual(get_provider_capabilities("codex-cli").to_dict(), CAPABILITIES)
        client = create_client({"providers": {"local": self.provider},
                                "active_provider": "local", "generation": {}})
        self.assertIsInstance(client, CodexCLIProvider)

    def test_successful_response_contains_only_final_text(self):
        reply = self.complete()
        self.assertEqual(reply.content, "CODEX_FINAL")
        self.assertEqual(set(reply.metadata), {"model", "elapsed_seconds"})
        self.assertNotIn("PRIVATE_DIAGNOSTIC", repr(reply))
        self.assertNotIn("hidden reasoning", repr(reply))
        self.which_mock.assert_called_once_with("codex")

    def test_argv_and_sandbox_approval_contract(self):
        self.complete()
        capture = self.captured()
        argv = capture["argv"]
        self.assertEqual(argv[:5], ["--no-daemon", "--ask-for-approval", "never", "exec", "--ignore-user-config"])
        for flag in ("--ignore-rules", "--ephemeral", "--skip-git-repo-check"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--cd") + 1], capture["cwd"])
        self.assertEqual(argv[-3:], ["--model", "test-model", "-"])
        settings = [argv[index + 1] for index, value in enumerate(argv) if value == "-c"]
        self.assertIn('default_permissions="llmtool-text"', settings)
        self.assertIn('permissions.llmtool-text.extends=":read-only"', settings)
        self.assertIn('permissions.llmtool-text.network.enabled=false', settings)
        filesystem = next(value for value in settings if ".filesystem=" in value)
        for rule in ('":root"="deny"', '":minimal"="read"', '":tmpdir"="deny"',
                     '":slash_tmp"="deny"', '":workspace_roots"={"."="read"}'):
            self.assertIn(rule, filesystem)
        for control in ('features.shell_tool=false', 'features.unified_exec=false',
                        'features.multi_agent=false', 'features.hooks=false',
                        'features.apps=false', 'features.shell_snapshot=false',
                        'project_doc_max_bytes=0', 'web_search="disabled"'):
            self.assertIn(control, settings)
        self.assertNotIn("--sandbox", argv)  # Would override restricted reads.
        self.assertNotIn("--json", argv)
        self.assertNotIn("private prompt", repr(argv))

    def test_prompt_uses_stdin_and_preserves_conversation(self):
        self.client().complete([{"role": "system", "content": "instruction"},
                                {"role": "user", "content": "hello\n世界"}])
        self.assertEqual(self.captured()["prompt"], "SYSTEM:\ninstruction\n\nUSER:\nhello\n世界")

    def test_prompt_larger_than_pipe_buffer_is_transported(self):
        prompt = "evidence " * 32768
        self.client().complete([{"role": "user", "content": prompt}])
        self.assertEqual(self.captured()["prompt"], "USER:\n" + prompt)

    def test_provider_never_opens_auth_or_prompt_files(self):
        with patch("builtins.open", side_effect=AssertionError("file read")), \
                patch("pathlib.Path.open", side_effect=AssertionError("file read")):
            self.complete()

    def test_user_provider_fields_cannot_override_runtime_controls(self):
        for field in ("executable", "cwd", "sandbox", "approval_policy", "env"):
            with self.subTest(field=field), patch("subprocess.Popen") as spawn:
                with self.assertRaisesRegex(LLMToolError, "forbidden or unsupported field"):
                    self.complete(**{field: "private override"})
                spawn.assert_not_called()

    def test_shell_false_explicit_cwd_and_process_group(self):
        real_popen = subprocess.Popen
        calls = []

        def checked(*args, **kwargs):
            calls.append((args, kwargs.copy()))
            return real_popen(*args, **kwargs)

        with patch("llmtool.providers.codex_cli.subprocess.Popen", side_effect=checked):
            self.complete()
        args, kwargs = calls[0]
        self.assertIsInstance(args[0], list)
        self.assertIs(kwargs["shell"], False)
        self.assertIs(kwargs["close_fds"], True)
        self.assertIs(kwargs["start_new_session"], True)
        self.assertEqual(kwargs["cwd"], self.captured()["cwd"])

    def test_cwd_isolated_outside_repo_no_copy_and_no_repo_mutation(self):
        repository = self.root / "caller"
        repository.mkdir()
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        (repository / "source.txt").write_text("original repository source")
        (repository / "AGENTS.md").write_text("private repository instructions")
        before = {str(path.relative_to(repository)): path.read_bytes()
                  for path in repository.rglob("*") if path.is_file()}
        self.fake("mutation")
        previous = Path.cwd()
        try:
            os.chdir(repository)
            with patch.dict(os.environ, {"TMPDIR": str(repository),
                                         "PWD": str(repository), "GIT_DIR": str(repository / ".git")}):
                self.complete()
        finally:
            os.chdir(previous)
        capture = self.captured()
        runtime = Path(capture["cwd"])
        self.assertNotEqual(runtime, repository)
        self.assertNotIn(repository, runtime.parents)
        self.assertEqual(capture["contents"], [])
        self.assertNotEqual(capture["git_status"], 0)
        self.assertNotIn(str(repository), repr(capture["argv"]))
        self.assertNotIn("PWD", capture["env"])
        self.assertNotIn("GIT_DIR", capture["env"])
        after = {str(path.relative_to(repository)): path.read_bytes()
                 for path in repository.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(runtime.exists())

    def test_environment_is_allowlisted_and_auth_location_preserved(self):
        with patch.dict(os.environ, {"HOME": str(self.root / "auth-home"),
                                     "CODEX_HOME": str(self.root / "auth-state"),
                                     "OPENAI_API_KEY": "PRIVATE_API_KEY",
                                     "NODE_OPTIONS": "PRIVATE_NODE_OPTIONS",
                                     "CODEX_REMOTE": "PRIVATE_REMOTE",
                                     "GIT_WORK_TREE": "PRIVATE_REPOSITORY",
                                     "LLMTOOL_MODEL": "PRIVATE_MODEL"}):
            self.complete()
        environment = self.captured()["env"]
        self.assertEqual(environment["HOME"], str(self.root / "auth-home"))
        self.assertEqual(environment["CODEX_HOME"], str(self.root / "auth-state"))
        self.assertEqual(environment["TMPDIR"], self.captured()["cwd"])
        for name in ("OPENAI_API_KEY", "NODE_OPTIONS", "CODEX_REMOTE", "GIT_WORK_TREE", "LLMTOOL_MODEL"):
            self.assertNotIn(name, environment)

    def test_runtime_paths_reject_relative_and_control_characters(self):
        for name in ("HOME", "CODEX_HOME"):
            for value in ("relative", "/tmp/secret\npath"):
                with self.subTest(name=name, value=value), patch.dict(os.environ, {name: value}):
                    with self.assertRaisesRegex(LLMToolError, "runtime paths must be absolute"):
                        self.complete()
                self.assertFalse(self.capture.exists())

    def test_timeout_is_normalized_and_resources_cleaned(self):
        self.fake("timeout")
        with self.assertRaisesRegex(LLMToolError, "^Codex CLI timed out$"):
            self.complete(timeout_seconds=1)
        self.assertFalse(Path(self.captured()["cwd"]).exists())

    def test_descendant_pipe_handles_are_bounded_by_timeout(self):
        self.fake("descendant")
        with self.assertRaisesRegex(LLMToolError, "^Codex CLI timed out$"):
            self.complete(timeout_seconds=1)
        self.assertFalse(Path(self.captured()["cwd"]).exists())

    def test_nonzero_error_does_not_leak_prompt_stderr_or_secret(self):
        self.fake("failure")
        with self.assertRaises(LLMToolError) as caught:
            self.complete()
        self.assertEqual(str(caught.exception), "Codex CLI exited with status 7")
        for secret in ("PRIVATE_DIAGNOSTIC", "private prompt", "SECRET_TOKEN"):
            self.assertNotIn(secret, str(caught.exception))
        self.assertFalse(Path(self.captured()["cwd"]).exists())

    def test_empty_output_is_rejected(self):
        self.fake("empty")
        with self.assertRaisesRegex(LLMToolError, "^Codex CLI returned no final text$"):
            self.complete()

    def test_invalid_utf8_is_rejected(self):
        self.fake("invalid")
        with self.assertRaisesRegex(LLMToolError, "^Codex CLI returned invalid UTF-8$"):
            self.complete()

    def test_oversized_stdout_is_rejected(self):
        self.fake("large")
        with patch("llmtool.providers.codex_cli.OUTPUT_LIMIT", 128):
            with self.assertRaisesRegex(LLMToolError, "^Codex CLI output exceeded 1 MiB$"):
                self.complete()

    def test_oversized_stderr_is_rejected(self):
        self.fake("large-stderr")
        with patch("llmtool.providers.codex_cli.OUTPUT_LIMIT", 128):
            with self.assertRaisesRegex(LLMToolError, "^Codex CLI output exceeded 1 MiB$"):
                self.complete()

    def test_missing_executable_is_actionable(self):
        with patch("llmtool.providers.codex_cli.shutil.which", return_value=None):
            with self.assertRaisesRegex(LLMToolError, "install codex on PATH"):
                self.complete()

    def test_relative_executable_resolution_is_rejected(self):
        with patch("llmtool.providers.codex_cli.shutil.which", return_value="./codex"):
            with self.assertRaisesRegex(LLMToolError, "install codex on PATH"):
                self.complete()

    def test_killpg_permission_error_falls_back_without_replacing_timeout(self):
        process = Mock(returncode=None)
        process.pid = 1234
        with patch("llmtool.providers.codex_cli.os.killpg", side_effect=PermissionError):
            CodexCLIProvider._kill(process)
        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with()

    def test_start_failures_are_sanitized(self):
        for error in (FileNotFoundError("SECRET_TOKEN"), OSError("SECRET_TOKEN")):
            with self.subTest(error=type(error)), patch(
                    "llmtool.providers.codex_cli.subprocess.Popen", side_effect=error):
                with self.assertRaises(LLMToolError) as caught:
                    self.complete()
                self.assertNotIn("SECRET_TOKEN", str(caught.exception))

    def test_unsupported_options_fail_before_spawn(self):
        for options in ({"reasoning_effort": "high"}, {"tools": []}, [], None):
            with self.subTest(options=options), patch("subprocess.Popen") as spawn:
                with self.assertRaisesRegex(LLMToolError, "unsupported codex-cli provider_options"):
                    self.complete(provider_options=options)
                spawn.assert_not_called()

    def test_model_injection_is_rejected_before_spawn(self):
        for model in ("--model", "-c", "model --cd /tmp", "model\nsecret", "$(touch /tmp/x)",
                      "model;command", "", "m" * 129, 123):
            with self.subTest(model=model), patch("subprocess.Popen") as spawn:
                with self.assertRaisesRegex(LLMToolError, "safe model identifier"):
                    self.complete(model=model)
                spawn.assert_not_called()

    def test_optional_model_uses_cli_default(self):
        self.complete(model=None)
        self.assertNotIn("--model", self.captured()["argv"])

    def test_invalid_conversations_fail_safely(self):
        for messages in ([], None, [{"role": "tool", "content": "secret"}],
                         [{"role": "user", "content": 123}],
                         [{"role": "user", "content": "\ud800"}]):
            with self.subTest(messages=messages), patch("subprocess.Popen") as spawn:
                with self.assertRaisesRegex(LLMToolError, "invalid conversation input"):
                    self.client().complete(messages)
                spawn.assert_not_called()

    def test_temporary_directory_failure_is_sanitized(self):
        with patch("llmtool.providers.codex_cli.tempfile.TemporaryDirectory",
                   side_effect=OSError("SECRET_TOKEN")):
            with self.assertRaisesRegex(LLMToolError, "^Codex CLI isolated working directory is not available$"):
                self.complete()


class CodexCLIConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.cwd = self.root / "project"
        self.cwd.mkdir()
        self.path = self.home / ".config/llmtool/config.yaml"

    def write(self, **fields):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(yaml.safe_dump({
            "providers": {"local-codex": {"type": "codex-cli", **fields}},
            "active_provider": "local-codex",
        }))

    def resolve(self):
        return resolve_config(cwd=self.cwd, home=self.home, environ={}, require_configured=True)

    def test_optional_model_and_no_api_fields(self):
        self.write()
        profile = self.resolve()["providers"]["local-codex"]
        self.assertIsNone(profile["model"])
        self.assertEqual(profile["timeout_seconds"], 120)
        self.assertIsNone(profile["api_key"])

    def test_base_url_rejected(self):
        self.write(base_url="https://example.test")
        with self.assertRaises(LLMToolError):
            self.resolve()

    def test_api_key_rejected(self):
        self.write(api_key="secret")
        with self.assertRaises(LLMToolError):
            self.resolve()

    def test_api_key_env_rejected(self):
        self.write(api_key_env="SECRET_KEY")
        with self.assertRaises(LLMToolError):
            self.resolve()

    def test_user_executable_config_rejected(self):
        self.write(executable="/tmp/arbitrary")
        with self.assertRaises(LLMToolError):
            self.resolve()

    def test_project_executable_and_sandbox_overrides_rejected(self):
        self.write()
        for fields in ({"executable": "/tmp/arbitrary"}, {"sandbox": "danger-full-access"},
                       {"provider_options": {"tools": True}}, {"type": "openai"}):
            with self.subTest(fields=fields):
                (self.cwd / ".llmtool.yaml").write_text(yaml.safe_dump({
                    "providers": {"local-codex": fields},
                }))
                with self.assertRaises(LLMToolError):
                    self.resolve()

    def test_project_model_injection_rejected(self):
        self.write()
        (self.cwd / ".llmtool.yaml").write_text(yaml.safe_dump({
            "providers": {"local-codex": {"model": "--cd"}},
        }))
        with self.assertRaises(LLMToolError):
            self.resolve()

    def test_configure_wizard_skips_http_credentials_and_network(self):
        answers = iter(["local-codex", "codex-cli", "", "", "", "", ""])
        prompts = []

        def answer(prompt):
            prompts.append(prompt)
            return next(answers)

        with patch("subprocess.Popen", side_effect=AssertionError("spawn")):
            run_configure(self.home, answer, lambda *a, **k: self.fail("secret read"),
                          io.StringIO(), io.StringIO())
        profile = self.resolve()["providers"]["local-codex"]
        self.assertEqual(profile["type"], "codex-cli")
        self.assertEqual(profile["provider_options"], {})
        self.assertIsNone(profile["model"])
        self.assertTrue(all("API key" not in value and "Base URL" not in value for value in prompts))
        self.assertIn("not sent by Codex CLI", " ".join(prompts))
        self.assertIn("not enforced by Codex CLI", " ".join(prompts))


if __name__ == "__main__":
    unittest.main()
