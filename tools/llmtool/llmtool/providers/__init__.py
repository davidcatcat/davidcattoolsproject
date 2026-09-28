"""Provider selection stays outside the CLI and prompt layer."""

from .openai_compatible import OpenAICompatibleClient
from ..errors import LLMToolError


def create_client(config, key=None):
    provider = config["providers"]["company"]
    if provider["type"] != "openai-compatible":
        raise LLMToolError("unsupported provider type")
    return OpenAICompatibleClient(provider, config["generation"], key)
