"""Static provider registry; metadata lookup never constructs a client."""

import re
from types import MappingProxyType
from typing import Iterable

from .openai_compatible import OpenAICompatibleClient
from .openai import OpenAIProvider
from .claude_cli import ClaudeCLIProvider
from .base import Provider, ProviderCapabilities
from ..errors import LLMToolError


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


PROVIDER_REGISTRY = ProviderRegistry((OpenAIProvider, OpenAICompatibleClient, ClaudeCLIProvider))


def client_type(name: str) -> type[Provider]:
    return PROVIDER_REGISTRY.client_type(name)


def get_provider_capabilities(type_name: str) -> ProviderCapabilities:
    """Pure metadata lookup: no config, credentials, client or I/O required."""
    return PROVIDER_REGISTRY.capabilities(type_name)


def create_client(config, key=None) -> Provider:
    provider = config["providers"][config["active_provider"]]
    implementation = client_type(provider["type"])
    implementation.validate_ready(provider)
    return implementation(provider, config["generation"], key)
