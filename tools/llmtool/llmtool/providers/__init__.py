"""Static provider registry; metadata lookup never constructs a client."""

import re
from types import MappingProxyType
from typing import Iterable
from urllib.parse import urlsplit

from .openai_compatible import OpenAICompatibleClient
from .openai import OpenAIProvider
from .claude_cli import ClaudeCLIProvider
from .codex_cli import CodexCLIProvider
from .base import Provider, ProviderCapabilities
from ..errors import LLMToolError, coded_error


class ProviderRegistry:
    """Build once from explicit implementation classes; no plugin discovery."""

    def __init__(self, implementations: Iterable[type[Provider]]):
        registered = {}
        for implementation in implementations:
            name = implementation.TYPE
            if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", name):
                raise LLMToolError("invalid provider type metadata")
            if name in registered:
                raise LLMToolError("duplicate provider type")
            if type(implementation.CAPABILITIES) is not ProviderCapabilities:
                raise LLMToolError("invalid provider capability metadata")
            registered[name] = implementation
        self._types = MappingProxyType(registered)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._types))

    def client_type(self, name: str) -> type[Provider]:
        if not isinstance(name, str) or name not in self._types:
            raise LLMToolError("unsupported provider type; use llmtool providers")
        return self._types[name]

    def capabilities(self, name: str) -> ProviderCapabilities:
        return self.client_type(name).CAPABILITIES


PROVIDER_REGISTRY = ProviderRegistry((OpenAIProvider, OpenAICompatibleClient, ClaudeCLIProvider, CodexCLIProvider))


def client_type(name: str) -> type[Provider]:
    return PROVIDER_REGISTRY.client_type(name)


def get_provider_capabilities(type_name: str) -> ProviderCapabilities:
    """Pure metadata lookup: no config, credentials, client or I/O required."""
    return PROVIDER_REGISTRY.capabilities(type_name)


def _origin(url: str | None) -> tuple[str, str, int | None] | None:
    """Extract (scheme, lowercase host, port with default 80/443) from a URL."""
    if not url or not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url.strip())
    except Exception:
        return None
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if not scheme or not host:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        if scheme == "http":
            port = 80
        elif scheme == "https":
            port = 443
    return (scheme, host, port)


def create_client(config, key=None) -> Provider:
    active_name = config.get("active_provider")
    providers = config.get("providers", {})
    provider = providers.get(active_name) if isinstance(providers, dict) else None

    # Refuse direct company-gw access or any profile matching its origin.
    is_direct_gw = active_name == "company-gw"
    if not is_direct_gw and isinstance(providers, dict) and "company-gw" in providers:
        gw_profile = providers["company-gw"]
        if isinstance(gw_profile, dict):
            gw_origin = _origin(gw_profile.get("base_url"))
            active_origin = _origin(provider.get("base_url")) if isinstance(provider, dict) else None
            if gw_origin is not None and active_origin == gw_origin:
                is_direct_gw = True

    if is_direct_gw:
        provider_type = provider.get("type") if isinstance(provider, dict) else None
        source = f"provider.{provider_type}" if provider_type else "provider"
        raise coded_error(
            "direct company-gw provider access is retired per Owner decision 2026-11-11; use gw-agent",
            "COMPANY_GW_DIRECT_RETIRED",
            source,
            user_action_required=True,
        )

    provider = config["providers"][config["active_provider"]]
    implementation = client_type(provider["type"])
    implementation.validate_ready(provider)
    return implementation(provider, config["generation"], key)
