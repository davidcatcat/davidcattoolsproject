import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from llmtool.config import resolve_config
from llmtool.errors import LLMToolError
from llmtool.providers import PROVIDER_REGISTRY, get_provider_capabilities
from llmtool.providers.claude_cli import ClaudeCLIProvider


CAPABILITIES = {
    "chat": True, "large_context": False, "reasoning_control": False,
    "streaming": False, "code_edit": False, "shell": False, "git": False,
}


class ClaudeCLIProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args_file = self.root / "args.json"
        self.input_file = self.root / "input.txt"
        self.home_file = self.root / "child-home.txt"
        self.executable = self.root / "fake-claude"
        script = """#!/usr/bin/env python3
import json, os, sys, time
with open(%r, "w", encoding="utf-8") as f:
    json.dump(sys.argv[1:], f)
with open(%r, "w", encoding="utf-8") as f:
    f.write(sys.stdin.read())
with open(%r, "w", encoding="utf-8") as f:
    f.write(os.environ.get("HOME", "") + "\\n" + os.environ.get("LLMTOOL_CLAUDE_CLI_HOME", ""))
mode = os.environ.get("CLAUDE_TEST_MODE", "success")
if mode == "timeout":
    time.sleep(5)
elif mode == "large":
    sys.stdout.write("x" * 4096)
elif mode == "large-stderr":
    sys.stderr.write("x" * 4096)
elif mode == "empty":
    pass
elif mode == "failure":
    sys.stderr.write("PRIVATE_CLI_DIAGNOSTIC")
    sys.exit(7)
else:
    sys.stderr.write("PRIVATE_CLI_DIAGNOSTIC")
    sys.stdout.write("  CLAUDE_FINAL  \\n")
""" % (str(self.args_file), str(self.input_file), str(self.home_file))
        self.executable.write_text(script, encoding="utf-8")
        self.executable.chmod(0o700)
        self.provider_config = {
            "type": "claude-cli", "base_url": None, "model": "sonnet",
            "timeout_seconds": 5, "api_key": None, "api_key_env": None,
            "provider_options": {},
        }
        self.which = patch("llmtool.providers.claude_cli.shutil.which", return_value=str(self.executable))
        self.which.start()
        self.addCleanup(self.which.stop)

    def client(self, **provider_updates):
        provider = {**self.provider_config, **provider_updates}
        return ClaudeCLIProvider(provider, {"temperature": 0, "max_tokens": 100})

    def test_argv_stdin_and_successful_output_use_safe_noninteractive_mode(self):
        prompt = [{"role": "system", "content": "Follow instruction."},
                  {"role": "user", "content": "Reply with marker."}]
        with patch.dict(os.environ, {"CLAUDE_TEST_MODE": "success"}):
            reply = self.client().complete(prompt)
        argv = json.loads(self.args_file.read_text(encoding="utf-8"))
        self.assertEqual(argv, [
            "--print", "--output-format", "text", "--no-session-persistence",
            "--safe-mode", "--tools", "", "--model", "sonnet",
        ])
        self.assertEqual(self.input_file.read_text(encoding="utf-8"),
                         "SYSTEM:\nFollow instruction.\n\nUSER:\nReply with marker.")
        self.assertEqual(reply.content, "CLAUDE_FINAL")
        self.assertNotIn("PRIVATE_CLI_DIAGNOSTIC", repr(reply))

    def test_process_uses_argv_without_shell(self):
        real_popen = subprocess.Popen
        calls = []

        def checked_popen(*args, **kwargs):
            calls.append((args, kwargs.copy()))
            return real_popen(*args, **kwargs)

        with patch("llmtool.providers.claude_cli.subprocess.Popen", side_effect=checked_popen):
            self.client().complete([{"role": "user", "content": "hello"}])
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0][1]["shell"], False)
        self.assertIsInstance(calls[0][0][0], list)

    def test_runtime_home_keeps_claude_auth_separate_from_llmtool_home(self):
        with patch.dict(os.environ, {
            "HOME": str(self.root / "isolated-llmtool-home"),
            "LLMTOOL_CLAUDE_CLI_HOME": str(self.root / "claude-auth-home"),
        }):
            self.client().complete([{"role": "user", "content": "hello"}])
        self.assertEqual(self.home_file.read_text(encoding="utf-8"),
                         str(self.root / "claude-auth-home") + "\n")

    def test_failure_and_stderr_are_sanitized(self):
        with patch.dict(os.environ, {"CLAUDE_TEST_MODE": "failure"}):
            with self.assertRaises(LLMToolError) as caught:
                self.client().complete([{"role": "user", "content": "private prompt"}])
        self.assertEqual(str(caught.exception), "Claude CLI exited with status 7")
        self.assertNotIn("PRIVATE_CLI_DIAGNOSTIC", str(caught.exception))
        self.assertNotIn("private prompt", str(caught.exception))

    def test_timeout_is_normalized(self):
        with patch.dict(os.environ, {"CLAUDE_TEST_MODE": "timeout"}):
            with self.assertRaisesRegex(LLMToolError, "^Claude CLI timed out$"):
                self.client(timeout_seconds=0.1).complete([{"role": "user", "content": "hello"}])

    def test_empty_output_is_rejected(self):
        with patch.dict(os.environ, {"CLAUDE_TEST_MODE": "empty"}):
            with self.assertRaisesRegex(LLMToolError, "^Claude CLI returned no final text$"):
                self.client().complete([{"role": "user", "content": "hello"}])

    def test_oversized_output_is_bounded(self):
        for mode in ("large", "large-stderr"):
            with self.subTest(mode=mode), \
                    patch.dict(os.environ, {"CLAUDE_TEST_MODE": mode}), \
                    patch("llmtool.providers.claude_cli.OUTPUT_LIMIT", 128):
                with self.assertRaisesRegex(LLMToolError, "^Claude CLI output exceeded 1 MiB$"):
                    self.client().complete([{"role": "user", "content": "hello"}])

    def test_missing_executable_fails_safely(self):
        with patch("llmtool.providers.claude_cli.shutil.which", return_value=None):
            with self.assertRaisesRegex(LLMToolError, "^Claude CLI executable is not available$"):
                self.client().complete([{"role": "user", "content": "hello"}])

    def test_unsupported_options_and_injection_shaped_model_are_rejected(self):
        for provider in (
            {**self.provider_config, "provider_options": {"allow_tools": True}},
            {**self.provider_config, "model": "--permission-mode"},
            {**self.provider_config, "base_url": "https://example.test"},
            {**self.provider_config, "api_key": "fake-key"},
            {**self.provider_config, "api_key_env": "FAKE_KEY"},
        ):
            with self.subTest(provider=provider), self.assertRaises(LLMToolError):
                ClaudeCLIProvider.validate_config(provider)
                ClaudeCLIProvider.validate_options(provider["provider_options"])

    def test_capabilities_and_registry(self):
        self.assertIn("claude-cli", PROVIDER_REGISTRY.names())
        self.assertEqual(get_provider_capabilities("claude-cli").to_dict(), CAPABILITIES)


class ClaudeCLIConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.cwd = self.root / "project"
        self.cwd.mkdir()
        self.config_path = self.home / ".config/llmtool/config.yaml"

    def write_user_config(self, provider):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(yaml.safe_dump({
            "providers": {"claude": {"type": "claude-cli", **provider}},
            "active_provider": "claude",
        }), encoding="utf-8")

    def resolve(self):
        return resolve_config(cwd=self.cwd, home=self.home)

    def test_local_cli_profile_needs_no_http_or_api_credential(self):
        self.write_user_config({"model": "sonnet"})
        provider = self.resolve()["providers"]["claude"]
        self.assertEqual(provider["type"], "claude-cli")
        self.assertIsNone(provider["base_url"])
        self.assertIsNone(provider["api_key"])
        self.assertIsNone(provider["api_key_env"])

    def test_user_config_rejects_http_and_auth_fields(self):
        for field, value in (("base_url", "https://example.test"),
                             ("api_key", "fake-key"), ("api_key_env", "FAKE_KEY")):
            with self.subTest(field=field):
                self.write_user_config({field: value})
                with self.assertRaises(LLMToolError):
                    self.resolve()

    def test_project_config_cannot_set_executable_or_auth(self):
        self.write_user_config({"model": "sonnet"})
        for fragment in (
            {"providers": {"claude": {"executable": "/tmp/fake-claude"}}},
            {"providers": {"claude": {"api_key_env": "FAKE_KEY"}}},
        ):
            with self.subTest(fragment=fragment):
                (self.cwd / ".llmtool.yaml").write_text(yaml.safe_dump(fragment), encoding="utf-8")
                with self.assertRaises(LLMToolError):
                    self.resolve()


if __name__ == "__main__":
    unittest.main()
