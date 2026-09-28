"""Small shared response contract; task commands do not depend on transports."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Reply:
    content: str
    metadata: dict


class Provider(Protocol):
    def complete(self, messages: list) -> Reply:
        """Return final content and safe diagnostics, or raise LLMToolError."""
        ...
