"""Tests for company-gw direct access retirement in create_client."""

import unittest
from unittest.mock import patch
from urllib.request import OpenerDirector

from llmtool.errors import LLMToolError
from llmtool.providers import create_client
from llmtool.providers.openai_compatible import OpenAICompatibleClient


def _synthetic_config(active_provider, providers):
    """Build a synthetic configuration without credentials."""
    return {
        "active_provider": active_provider,
        "providers": providers,
        "generation": {"temperature": 0, "max_tokens": 1000},
    }


class TestCompanyGwDirectRetired(unittest.TestCase):
    """Test retirement refusal for company-gw and matching origins."""

    def setUp(self):
        # Patch the HTTP layer so any network call fails immediately.
        self.http_patcher = patch.object(
            OpenerDirector, "open", side_effect=AssertionError("unexpected network call")
        )
        self.mock_open = self.http_patcher.start()

    def tearDown(self):
        self.http_patcher.stop()
        self.mock_open.assert_not_called()

    def test_name_company_gw_refused(self):
        """Active provider named company-gw must be refused before client build."""
        config = _synthetic_config(
            "company-gw",
            {
                "company-gw": {
                    "type": "openai-compatible",
                    "base_url": "http://gw.example.invalid",
                    "model": "synthetic-model",
                    "timeout_seconds": 30,
                }
            },
        )
        with self.assertRaises(LLMToolError) as caught:
            create_client(config)
        exc = caught.exception
        self.assertEqual(str(exc.diagnostic.code), "COMPANY_GW_DIRECT_RETIRED")
        self.assertTrue(exc.diagnostic.user_action_required)
        self.assertIn("Owner decision 2026-11-11", str(exc))
        self.assertIn("gw-agent", str(exc))
        self.mock_open.assert_not_called()

    def test_name_company_gw_refused_before_validation(self):
        """Refusal occurs before validate_ready checks missing fields."""
        config = _synthetic_config(
            "company-gw",
            {
                "company-gw": {
                    "type": "openai-compatible",
                    "timeout_seconds": 30,
                }
            },
        )
        with self.assertRaises(LLMToolError) as caught:
            create_client(config)
        exc = caught.exception
        self.assertEqual(str(exc.diagnostic.code), "COMPANY_GW_DIRECT_RETIRED")
        self.assertTrue(exc.diagnostic.user_action_required)
        self.assertIn("Owner decision 2026-11-11", str(exc))
        self.assertIn("gw-agent", str(exc))
        self.mock_open.assert_not_called()

    def test_alias_with_same_origin_refused(self):
        """Aliases sharing the company-gw origin must be refused."""
        variants = [
            ("case difference", "HTTP://GW.EXAMPLE.INVALID/v1"),
            ("trailing slash", "http://gw.example.invalid/"),
            ("different path", "http://gw.example.invalid/custom/path"),
            ("explicit default http port", "http://gw.example.invalid:80"),
            ("explicit default http port with slash", "http://gw.example.invalid:80/"),
        ]
        for desc, alias_url in variants:
            with self.subTest(variant=desc):
                config = _synthetic_config(
                    "alias-profile",
                    {
                        "company-gw": {
                            "type": "openai-compatible",
                            "base_url": "http://gw.example.invalid/v1",
                            "model": "synthetic-model",
                            "timeout_seconds": 30,
                        },
                        "alias-profile": {
                            "type": "openai-compatible",
                            "base_url": alias_url,
                            "model": "synthetic-model",
                            "timeout_seconds": 30,
                        },
                    },
                )
                with self.assertRaises(LLMToolError) as caught:
                    create_client(config)
                exc = caught.exception
                self.assertEqual(str(exc.diagnostic.code), "COMPANY_GW_DIRECT_RETIRED")
                self.assertTrue(exc.diagnostic.user_action_required)
                self.assertIn("Owner decision 2026-11-11", str(exc))
                self.assertIn("gw-agent", str(exc))
                self.mock_open.assert_not_called()

    def test_https_alias_with_explicit_default_port_refused(self):
        """HTTPS aliases sharing origin with explicit default port 443 must be refused."""
        config = _synthetic_config(
            "alias-profile",
            {
                "company-gw": {
                    "type": "openai-compatible",
                    "base_url": "https://gw.example.invalid",
                    "model": "synthetic-model",
                    "timeout_seconds": 30,
                },
                "alias-profile": {
                    "type": "openai-compatible",
                    "base_url": "https://gw.example.invalid:443/",
                    "model": "synthetic-model",
                    "timeout_seconds": 30,
                },
            },
        )
        with self.assertRaises(LLMToolError) as caught:
            create_client(config)
        exc = caught.exception
        self.assertEqual(str(exc.diagnostic.code), "COMPANY_GW_DIRECT_RETIRED")
        self.assertTrue(exc.diagnostic.user_action_required)
        self.assertIn("Owner decision 2026-11-11", str(exc))
        self.assertIn("gw-agent", str(exc))
        self.mock_open.assert_not_called()

    def test_different_host_allowed(self):
        """Providers with a different host must not be refused."""
        config = _synthetic_config(
            "other-provider",
            {
                "company-gw": {
                    "type": "openai-compatible",
                    "base_url": "http://gw.example.invalid",
                    "model": "synthetic-model",
                    "timeout_seconds": 30,
                },
                "other-provider": {
                    "type": "openai-compatible",
                    "base_url": "http://other.example.invalid",
                    "model": "synthetic-model",
                    "timeout_seconds": 30,
                },
            },
        )
        client = create_client(config)
        self.assertIsInstance(client, OpenAICompatibleClient)
        self.mock_open.assert_not_called()

    def test_different_port_allowed(self):
        """Providers with a different port on the same host must not be refused."""
        config = _synthetic_config(
            "other-port",
            {
                "company-gw": {
                    "type": "openai-compatible",
                    "base_url": "http://gw.example.invalid:8000",
                    "model": "synthetic-model",
                    "timeout_seconds": 30,
                },
                "other-port": {
                    "type": "openai-compatible",
                    "base_url": "http://gw.example.invalid:8001",
                    "model": "synthetic-model",
                    "timeout_seconds": 30,
                },
            },
        )
        client = create_client(config)
        self.assertIsInstance(client, OpenAICompatibleClient)
        self.mock_open.assert_not_called()

    def test_no_company_gw_profile_allowed(self):
        """Configurations without a company-gw profile behave normally."""
        config = _synthetic_config(
            "default",
            {
                "default": {
                    "type": "openai-compatible",
                    "base_url": "http://gw.example.invalid",
                    "model": "synthetic-model",
                    "timeout_seconds": 30,
                }
            },
        )
        client = create_client(config)
        self.assertIsInstance(client, OpenAICompatibleClient)
        self.mock_open.assert_not_called()

    def test_provider_without_base_url_not_refused_with_retired(self):
        """Providers without base_url do not trigger COMPANY_GW_DIRECT_RETIRED."""
        config = _synthetic_config(
            "default",
            {
                "company-gw": {
                    "type": "openai-compatible",
                    "base_url": "http://gw.example.invalid",
                    "model": "synthetic-model",
                    "timeout_seconds": 30,
                },
                "default": {
                    "type": "openai-compatible",
                    "model": "synthetic-model",
                    "timeout_seconds": 30,
                },
            },
        )
        with self.assertRaises(LLMToolError) as caught:
            create_client(config)
        self.assertEqual(str(caught.exception.diagnostic.code), "CONFIG_PROVIDER_NOT_READY")
        self.mock_open.assert_not_called()
