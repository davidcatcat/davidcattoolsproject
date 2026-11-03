"""Read-only, provider-agnostic LLM CLI foundation."""

from importlib.metadata import PackageNotFoundError, version as _distribution_version

try:
    __version__ = _distribution_version("llmtool")
except PackageNotFoundError:
    # A source checkout can be imported without installing the distribution.
    __version__ = "unknown"
