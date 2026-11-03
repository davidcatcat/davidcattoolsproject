"""Read-only, provider-agnostic LLM CLI foundation."""

from importlib.metadata import PackageNotFoundError, version as _distribution_version
from pathlib import Path
import re


def _source_version():
    """Read the project version for imports from an uninstalled source checkout."""
    try:
        path = Path(__file__).resolve().parent.parent / "pyproject.toml"
        with path.open("rb") as source:
            data = source.read(8193)
        if len(data) > 8192:
            return "unknown"
        match = re.search(rb"(?ms)^\[project\]\s*$.*?^version\s*=\s*['\"]([^'\"]+)['\"]", data)
        return match.group(1).decode("ascii") if match else "unknown"
    except (OSError, UnicodeError, ValueError):
        return "unknown"

try:
    __version__ = _distribution_version("llmtool")
except PackageNotFoundError:
    # A source checkout can be imported without installing the distribution.
    __version__ = _source_version()
