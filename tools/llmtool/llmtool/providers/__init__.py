"""Provider selection stays outside the CLI and prompt layer."""

from .openai_compatible import OpenAICompatibleClient
from .base import Provider
from ..errors import LLMToolError

CLIENT_TYPES = {"openai-compatible": OpenAICompatibleClient}


def client_type(name):
    if not isinstance(name, str) or name not in CLIENT_TYPES:
        raise LLMToolError("unsupported provider type; A1 supports openai-compatible")
    return CLIENT_TYPES[name]


def create_client(config, key=None) -> Provider:
    provider = config["providers"][config["active_provider"]]
    implementation = client_type(provider["type"])
    implementation.validate_ready(provider)
    return implementation(provider, config["generation"], key)
