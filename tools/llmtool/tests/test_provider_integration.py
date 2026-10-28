"""Cross-provider public integration seams; all transports remain offline."""
import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from llmtool.cli import main
from llmtool.config import api_key, resolve_config
from llmtool.configure import run_configure
from llmtool.errors import LLMToolError
from llmtool.providers import PROVIDER_REGISTRY, client_type, create_client
from llmtool.providers.base import Reply
from test_configure import InteractiveInput, TTY

PROFILES = {
    "office-qwen": {"type": "openai-compatible", "base_url": "https://gateway.example",
                    "model": "qwen-test", "provider_options": {"enable_thinking": False}},
    "openai-main": {"type": "openai", "model": "openai-test", "api_key": "FAKE_KEY",
                    "provider_options": {"reasoning_effort": "low"}},
    "local-claude": {"type": "claude-cli"},
    "local-codex": {"type": "codex-cli"},
}


class ProviderIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.cwd = Path(self.temp.name) / "project"
        self.cwd.mkdir()
        self.path = self.home / ".config/llmtool/config.yaml"
        self.path.parent.mkdir(parents=True)
        self.write_config()

    def write_config(self, profiles=None, active="office-qwen"):
        self.path.write_text(yaml.safe_dump({"providers": PROFILES if profiles is None else profiles,
                                             "active_provider": active}))

    def resolve(self, cli=None, env=None):
        return resolve_config(cli, {} if env is None else env, self.cwd, self.home,
                              require_configured=True)

    def cli(self, args):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {}, clear=True), \
                patch("llmtool.config.user_config_path", return_value=self.path), \
                patch("llmtool.config.Path.cwd", return_value=self.cwd), \
                redirect_stdout(out), redirect_stderr(err):
            status = main(args)
        return status, out.getvalue(), err.getvalue()

    def transport(self, stack, client):
        """Exercise real complete()/normalization, replacing only transport I/O."""
        if client.TYPE in ("claude-cli", "codex-cli"):
            stack.enter_context(patch("shutil.which", return_value="/fake/runtime"))
            invoke = stack.enter_context(patch.object(type(client), "_invoke",
                                                       return_value=("FINAL", 0.125)))
            return invoke
        response = Mock()
        response.status = 200
        if client.TYPE == "openai":
            payload = {"status": "completed", "model": "openai-test", "output": [
                {"type": "reasoning", "summary": "PRIVATE_REASONING"},
                {"type": "message", "role": "assistant", "content": [
                    {"type": "output_text", "text": " FINAL "}]}],
                "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}}
        else:
            payload = {"model": "qwen-test", "choices": [{"finish_reason": "stop", "message": {
                "content": " FINAL ", "reasoning_content": "PRIVATE_REASONING"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}}
        payload["internal_event"] = "PRIVATE_EVENT"
        response.read.return_value = json.dumps(payload).encode()
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=False)
        return stack.enter_context(patch.object(client.opener, "open", return_value=context))

    def test_registry_and_factory_for_every_profile(self):
        self.assertEqual(PROVIDER_REGISTRY.names(), tuple(sorted(p["type"] for p in PROFILES.values())))
        for name, profile in PROFILES.items():
            with self.subTest(name=name):
                config = self.resolve({"provider": name})
                client = create_client(config, api_key(config, {}))
                self.assertIs(type(client), client_type(profile["type"]))
                self.assertEqual(client.provider, config["providers"][name])

    def test_selection_precedence_and_profile_isolation(self):
        original = self.path.read_bytes()
        baseline = self.resolve()
        self.assertEqual(baseline["active_provider"], "office-qwen")
        for name in PROFILES:
            with self.subTest(name=name):
                self.assertEqual(self.resolve(env={"LLMTOOL_PROVIDER": name})["active_provider"], name)
                config = self.resolve({"provider": name, "model": "override-model"},
                                      {"LLMTOOL_PROVIDER": "openai-main"})
                self.assertEqual(config["active_provider"], name)
                self.assertEqual(config["providers"][name]["model"], "override-model")
                for other in PROFILES:
                    if other != name:
                        self.assertEqual(config["providers"][other], baseline["providers"][other])
        self.assertEqual(self.path.read_bytes(), original)

    def test_invalid_profile_and_type_are_sanitized_before_io(self):
        for args in (["ask", "test", "--provider", "SECRET_PROFILE"],
                     ["ask", "test", "--provider", "SECRET\nPROFILE"]):
            with self.subTest(args=args), patch("llmtool.cli.create_client") as factory:
                status, out, err = self.cli(args)
                self.assertEqual(status, 1)
                self.assertEqual(out, "")
                self.assertNotIn("SECRET", err)
                factory.assert_not_called()
        for value in ("SECRET_TYPE", [], None, "anthropic"):
            profiles = copy.deepcopy(PROFILES)
            profiles["local-codex"]["type"] = value
            self.write_config(profiles)
            with self.subTest(value=value), self.assertRaisesRegex(
                    LLMToolError, "^unsupported provider type; use llmtool providers$"):
                self.resolve()
        self.write_config(active="missing")
        with self.assertRaisesRegex(LLMToolError, "^active provider is not defined"):
            self.resolve()

    def test_cross_provider_fields_rejected_even_on_inactive_profiles(self):
        cases = []
        for name in ("local-claude", "local-codex"):
            invalid_reasoning = ("none" if name == "local-codex" else "low")
            cases.extend((name, field, value) for field, value in (
                ("base_url", "https://secret.example"), ("api_key", "SECRET"),
                ("api_key_env", "SECRET_ENV"), ("provider_options", {"enable_thinking": False}),
                ("provider_options", {"reasoning_effort": invalid_reasoning})))
        cases.extend([
            ("openai-main", "base_url", "https://secret.example"),
            ("openai-main", "provider_options", {"enable_thinking": False}),
            ("office-qwen", "provider_options", {"reasoning_effort": "low"}),
            ("office-qwen", "provider_options", {"enable_thinking": "false"}),
            ("office-qwen", "base_url", "https://user:SECRET@gateway.example"),
            ("openai-main", "api_key_env", "SECRET_ENV"),
        ])
        for name, field, value in cases:
            profiles = copy.deepcopy(PROFILES)
            profiles[name][field] = value
            self.write_config(profiles)
            with self.subTest(name=name, field=field, value=value), self.assertRaises(LLMToolError) as error:
                self.resolve({"provider": "local-claude"})
            self.assertNotIn("SECRET", str(error.exception))

        profiles = copy.deepcopy(PROFILES)
        profiles["local-codex"]["provider_options"] = {"reasoning_effort": "low"}
        self.write_config(profiles)
        self.resolve({"provider": "local-claude"})

    def test_config_show_redacts_all_profiles_without_credential_lookup(self):
        profiles = copy.deepcopy(PROFILES)
        profiles["office-qwen"]["api_key_env"] = "UNAVAILABLE_TEST_KEY"
        self.write_config(profiles)
        status, out, err = self.cli(["config", "show", "--provider", "local-codex"])
        self.assertEqual((status, err), (0, ""))
        shown = yaml.safe_load(out)
        self.assertEqual(shown["active_provider"], "local-codex")
        self.assertNotIn("FAKE_KEY", out)
        for name in ("office-qwen", "openai-main"):
            self.assertEqual(shown["providers"][name]["api_key"], "***REDACTED***")
        self.assertEqual(yaml.safe_load(self.path.read_text())["active_provider"], "office-qwen")

    def test_capabilities_are_deterministic_and_implementation_owned(self):
        first = self.cli(["providers", "--json"])
        self.assertEqual(first, self.cli(["providers", "--json"]))
        self.assertEqual((first[0], first[2]), (0, ""))
        metadata = json.loads(first[1])
        self.assertEqual(tuple(metadata), PROVIDER_REGISTRY.names())
        for name, values in metadata.items():
            implementation = client_type(name).CAPABILITIES
            self.assertEqual(values, implementation.to_dict())
            self.assertIs(values["streaming"], implementation.streaming)
            for capability in ("shell", "git", "code_edit"):
                self.assertIs(values[capability], False)
            if name.endswith("-cli"):
                self.assertEqual([key for key, value in values.items() if value], ["chat"])
        text = self.cli(["providers"])
        self.assertEqual(text, self.cli(["providers"]))
        self.assertEqual(text[1].count("Provider: "), 4)

    def test_common_reply_contract_from_real_normalizers(self):
        for name in PROFILES:
            with self.subTest(name=name), ExitStack() as stack:
                config = self.resolve({"provider": name})
                client = create_client(config, api_key(config, {}))
                transport = self.transport(stack, client)
                reply = client.complete([{"role": "user", "content": "test"}])
                self.assertIsInstance(reply, Reply)
                self.assertEqual(reply.content, "FINAL")
                self.assertIsInstance(reply.metadata["elapsed_seconds"], (float, int))
                self.assertGreaterEqual(reply.metadata["elapsed_seconds"], 0)
                self.assertLessEqual(set(reply.metadata), {"elapsed_seconds", "model", "input_tokens",
                    "output_tokens", "prompt_tokens", "completion_tokens", "total_tokens"})
                for secret in ("PRIVATE_REASONING", "PRIVATE_EVENT", "FAKE_KEY"):
                    self.assertNotIn(secret, repr(reply))
                transport.assert_called_once()

    def test_ask_dispatch_stdout_and_failure_contract_for_all_profiles(self):
        before = self.path.read_bytes()
        for name in PROFILES:
            with self.subTest(name=name), ExitStack() as stack:
                client = create_client(self.resolve({"provider": name}),
                                       "FAKE_KEY" if name == "openai-main" else None)
                self.transport(stack, client)
                factory = stack.enter_context(patch("llmtool.cli.create_client", return_value=client))
                self.assertEqual(self.cli(["ask", "test", "--provider", name]), (0, "FINAL\n", ""))
                self.assertEqual(factory.call_args.args[0]["active_provider"], name)
                stack.enter_context(patch.object(client, "complete", side_effect=LLMToolError("safe failure")))
                status, output, error = self.cli(["ask", "test", "--provider", name])
                self.assertEqual((status, output), (1, ""))
                self.assertRegex(error, r"^llmtool: safe failure\n"
                                 r"llmtool: code=PROVIDER_RUNTIME_ERROR run_id=[0-9a-f]{32} trace=not-saved\n$")
        self.assertEqual(self.path.read_bytes(), before)

    def test_returned_commands_are_only_printed(self):
        sentinel = self.cwd / "must-not-exist"
        text = f"touch {sentinel}\ngit checkout -b must-not-exist"
        for name in PROFILES:
            with self.subTest(name=name), patch.object(client_type(PROFILES[name]["type"]),
                    "complete", return_value=Reply(text, {})), \
                    patch("subprocess.Popen", side_effect=AssertionError("execution")), \
                    patch("os.system", side_effect=AssertionError("execution")):
                self.assertEqual(self.cli(["ask", "test", "--provider", name]), (0, text + "\n", ""))
                self.assertFalse(sentinel.exists())

    def test_configure_all_families_preserves_other_profiles_and_never_invokes_runtime(self):
        for name, profile in PROFILES.items():
            with self.subTest(name=name), ExitStack() as stack:
                self.write_config()
                answers = ["yes", name, profile["type"]]
                if profile["type"] == "openai-compatible":
                    answers.append("https://new.example")
                answers.extend(["new-model", ""])
                if not profile["type"].endswith("-cli"):
                    answers.extend(["", ""])
                answers.extend(["", "", "office-qwen"])  # generation and active profile
                input_fn = InteractiveInput(answers)
                secret = Mock(return_value="")
                stack.enter_context(patch("sys.stdin", TTY()))
                stack.enter_context(patch("subprocess.Popen", side_effect=AssertionError("runtime")))
                stack.enter_context(patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("HTTP")))
                out = io.StringIO()
                run_configure(self.home, input_fn, secret, out, io.StringIO())
                saved = yaml.safe_load(self.path.read_text())
                for other in PROFILES:
                    if other != name:
                        resolved = self.resolve()["providers"][other]
                        self.assertEqual(saved["providers"][other], PROFILES[other])
                        for key, value in PROFILES[other].items():
                            self.assertEqual(resolved[key], value)
                self.assertEqual(saved["active_provider"], "office-qwen")
                self.assertIn("Base URL: " + saved["providers"]["office-qwen"]["base_url"], out.getvalue())
                prompts = " ".join(input_fn.prompts)
                if profile["type"].endswith("-cli"):
                    secret.assert_not_called()
                    self.assertNotIn("API key", prompts)
                    self.assertNotIn("Base URL", prompts)
                else:
                    secret.assert_called_once()
                if profile["type"] == "openai":
                    self.assertNotIn("Base URL", prompts)
                    self.assertIsNone(saved["providers"][name]["base_url"])

    def test_configure_summary_for_different_active_provider_types(self):
        for active in ("openai-main", "local-claude", "local-codex"):
            with self.subTest(active=active), patch("sys.stdin", TTY()):
                self.write_config()
                answers = InteractiveInput(["yes", "office-qwen", "openai-compatible",
                    "https://new.example", "new-model", "", "", "", "", "", active])
                out = io.StringIO()
                run_configure(self.home, answers, lambda *a, **k: "", out, io.StringIO())
                self.assertNotIn("Base URL:", out.getvalue())
                self.assertIn("Type: " + PROFILES[active]["type"], out.getvalue())
