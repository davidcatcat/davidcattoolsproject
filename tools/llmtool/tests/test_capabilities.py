import io
import json
import os
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError, fields
from unittest.mock import patch

import yaml

from llmtool.cli import main
from llmtool.errors import LLMToolError
from llmtool.providers import (
    PROVIDER_REGISTRY, ProviderRegistry, client_type, create_client,
    get_provider_capabilities,
)
from llmtool.providers.base import ProviderCapabilities, Reply
from llmtool.providers.openai_compatible import OpenAICompatibleClient


EXPECTED = {
    "chat": True,
    "large_context": True,
    "reasoning_control": True,
    "streaming": False,
    "code_edit": False,
    "shell": False,
    "git": False,
}


class CapabilityTests(unittest.TestCase):
    def test_expected_fields_and_deterministic_serialization(self):
        capabilities = ProviderCapabilities(**EXPECTED)
        self.assertEqual([field.name for field in fields(capabilities)], list(EXPECTED))
        self.assertEqual(capabilities.to_dict(), EXPECTED)
        self.assertEqual(list(capabilities.to_dict()), list(EXPECTED))
        self.assertEqual(json.loads(json.dumps(capabilities.to_dict())), EXPECTED)
        self.assertEqual(yaml.safe_load(yaml.safe_dump(capabilities.to_dict())), EXPECTED)

    def test_immutable_and_serialization_detached(self):
        capabilities = ProviderCapabilities(**EXPECTED)
        with self.assertRaises(FrozenInstanceError):
            capabilities.chat = False
        with self.assertRaises(FrozenInstanceError):
            capabilities.unknown = True
        serialized = capabilities.to_dict()
        serialized["chat"] = False
        self.assertTrue(capabilities.chat)

    def test_unknown_or_missing_fields_rejected(self):
        with self.assertRaises(TypeError):
            ProviderCapabilities(**EXPECTED, unknown=True)
        with self.assertRaises(TypeError):
            ProviderCapabilities(chat=True)

    def test_non_boolean_values_rejected(self):
        for value in (None, 1, "true", {}, []):
            with self.subTest(value=value), self.assertRaises(TypeError):
                ProviderCapabilities(**{**EXPECTED, "chat": value})

    def test_production_registry_matches_implementation(self):
        self.assertEqual(PROVIDER_REGISTRY.names(), ("openai-compatible",))
        self.assertIs(client_type("openai-compatible"), OpenAICompatibleClient)
        self.assertEqual(get_provider_capabilities("openai-compatible").to_dict(), EXPECTED)

    def test_lookup_without_instantiation_or_validation(self):
        with patch.object(OpenAICompatibleClient, "__init__", side_effect=AssertionError), \
                patch.object(OpenAICompatibleClient, "validate_ready", side_effect=AssertionError), \
                patch.object(OpenAICompatibleClient, "validate_options", side_effect=AssertionError):
            self.assertEqual(get_provider_capabilities("openai-compatible").to_dict(), EXPECTED)

    def test_unsupported_types_fail_safely(self):
        for value in ("openai", "anthropic", "claude-cli", "codex-cli", "secret-value", None, []):
            with self.subTest(value=value):
                for lookup in (client_type, get_provider_capabilities):
                    with self.assertRaisesRegex(LLMToolError, "^unsupported provider type; use llmtool providers$"):
                        lookup(value)

    def test_duplicate_type_rejected(self):
        class Duplicate(OpenAICompatibleClient):
            pass
        with self.assertRaisesRegex(LLMToolError, "duplicate provider type"):
            ProviderRegistry((OpenAICompatibleClient, Duplicate))

    def test_invalid_metadata_rejected(self):
        class InvalidName(OpenAICompatibleClient):
            TYPE = "bad\nname"
        class InvalidCapabilities(OpenAICompatibleClient):
            CAPABILITIES = EXPECTED
        for implementation in (InvalidName, InvalidCapabilities):
            with self.subTest(implementation=implementation), self.assertRaises(LLMToolError):
                ProviderRegistry((implementation,))

    def test_registry_order_independent_of_registration_order(self):
        class ZProvider(OpenAICompatibleClient):
            TYPE = "z-test"
        registry = ProviderRegistry((ZProvider, OpenAICompatibleClient))
        self.assertEqual(registry.names(), ("openai-compatible", "z-test"))
        self.assertIs(registry.capabilities("z-test"), ZProvider.CAPABILITIES)

    def test_factory_supports_another_contract_implementation(self):
        class FakeProvider:
            TYPE = "test-only"
            CAPABILITIES = ProviderCapabilities(**EXPECTED)

            @staticmethod
            def validate_options(options):
                if options:
                    raise LLMToolError("unsupported test options")

            @staticmethod
            def validate_ready(provider):
                if not provider.get("ready"):
                    raise LLMToolError("test provider not ready")

            def __init__(self, provider, generation, key=None):
                self.validate_options(provider["provider_options"])

            def complete(self, messages):
                return Reply(messages[0]["content"], {})

        config = {"active_provider": "test", "providers": {"test": {
            "type": "test-only", "ready": True, "provider_options": {},
        }}, "generation": {}}
        with patch("llmtool.providers.PROVIDER_REGISTRY", ProviderRegistry((FakeProvider,))):
            reply = create_client(config).complete([{"role": "user", "content": "evidence"}])
            self.assertEqual(reply, Reply("evidence", {}))
            config["providers"]["test"]["ready"] = False
            with self.assertRaisesRegex(LLMToolError, "not ready"):
                create_client(config)
        self.assertEqual(PROVIDER_REGISTRY.names(), ("openai-compatible",))


class ProvidersSafetyTests(unittest.TestCase):
    def run_offline(self, args):
        out, err = io.StringIO(), io.StringIO()
        # Parse before denying environment reads: argparse itself reads COLUMNS
        # and LINES for help formatting; provider introspection reads no env.
        from llmtool.cli import parser_for_cli
        parsed = parser_for_cli().parse_args(args)
        with ExitStack() as stack:
            parser = stack.enter_context(patch("llmtool.cli.parser_for_cli"))
            parser.return_value.parse_args.return_value = parsed
            for target in (
                "llmtool.cli.resolve_config", "llmtool.cli.api_key",
                "llmtool.cli.create_client", "llmtool.cli.read_input",
                "llmtool.cli.run_configure", "llmtool.config.api_key",
                "llmtool.config.user_config_path", "builtins.open", "io.open",
                "pathlib.Path.iterdir", "os.scandir", "os.listdir",
                "urllib.request.build_opener", "socket.socket",
                "subprocess.Popen", "os.system",
                "llmtool.providers.openai_compatible.OpenAICompatibleClient.__init__",
            ):
                stack.enter_context(patch(target, side_effect=AssertionError(target)))
            stack.enter_context(patch.object(type(os.environ), "__getitem__", side_effect=AssertionError("env read")))
            stack.enter_context(patch.object(type(os.environ), "__iter__", side_effect=AssertionError("env scan")))
            stack.enter_context(redirect_stdout(out))
            stack.enter_context(redirect_stderr(err))
            self.assertEqual(main(args), 0)
        self.assertEqual(err.getvalue(), "")
        return out.getvalue()

    def test_text_offline_and_deterministic(self):
        expected = "Provider: openai-compatible\n" + "".join(
            "  " + key + ": " + ("yes" if value else "no") + "\n"
            for key, value in EXPECTED.items()
        )
        self.assertEqual(self.run_offline(["providers"]), expected)
        self.assertEqual(self.run_offline(["providers"]), expected)

    def test_json_offline_boolean_order_and_clean_stderr(self):
        output = self.run_offline(["providers", "--json"])
        self.assertEqual(output, self.run_offline(["providers", "--json"]))
        parsed = json.loads(output)
        self.assertEqual(parsed, {"openai-compatible": EXPECTED})
        self.assertEqual(list(parsed["openai-compatible"]), list(EXPECTED))
        self.assertTrue(all(type(value) is bool for value in parsed["openai-compatible"].values()))
