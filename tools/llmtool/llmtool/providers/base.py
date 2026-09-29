"""Shared implementation metadata and request/reply contract.

Capabilities describe technical ability, never authorization, policy or approval.
They contain no deployment configuration and make no per-model guarantees.
"""

from dataclasses import dataclass, fields
from typing import ClassVar, Protocol


@dataclass(frozen=True)
class ProviderCapabilities:
    """Closed, ordered boolean metadata for a provider/runtime implementation.

    chat: accepts llmtool's text conversation/request contract.
    large_context: can carry large-context workflows; no fixed model window.
    reasoning_control: can control/pass reasoning options; endpoint support varies.
    streaming: llmtool implementation supports streaming responses.
    code_edit: runtime can directly modify code/files.
    shell: runtime can launch commands.
    git: runtime can directly operate Git.

    Execution capabilities do not grant permission to exercise them.
    """

    chat: bool
    large_context: bool
    reasoning_control: bool
    streaming: bool
    code_edit: bool
    shell: bool
    git: bool

    def __post_init__(self):
        if any(type(getattr(self, field.name)) is not bool for field in fields(self)):
            raise TypeError("capabilities must be booleans")

    def to_dict(self) -> dict[str, bool]:
        """Return a detached, safe dictionary in declaration order."""
        return {field.name: getattr(self, field.name) for field in fields(self)}


@dataclass(frozen=True)
class Reply:
    content: str
    metadata: dict


class Provider(Protocol):
    TYPE: ClassVar[str]
    CAPABILITIES: ClassVar[ProviderCapabilities]

    def __init__(self, provider: dict, generation: dict, key=None):
        ...

    @staticmethod
    def validate_options(options: dict) -> None:
        """Reject unsupported implementation-specific options with LLMToolError."""
        ...

    @staticmethod
    def validate_ready(provider: dict) -> None:
        """Reject incomplete deployment settings before credentials or I/O."""
        ...

    def complete(self, messages: list) -> Reply:
        """Return final content and safe diagnostics, or raise LLMToolError."""
        ...
