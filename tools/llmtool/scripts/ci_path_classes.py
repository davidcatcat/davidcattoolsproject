#!/usr/bin/env python3
"""Classify repository paths for the Linux and Mac CI selectors."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PATH_CLASSES = ("docs", "runtime", "gateway", "ai-loop", "workflow", "other")


def classify_path(path: str) -> str:
    """Return one conservative CI class for a repository-relative path."""
    if path.startswith("tools/ai-loop/"):
        return "ai-loop"
    if path.startswith("tools/api-agent-gateway/"):
        return "gateway"
    if path.startswith(".github/workflows/"):
        return "workflow"
    if path.startswith("tools/llmtool/"):
        relative = path[len("tools/llmtool/"):]
        if "/" not in relative and relative.endswith(".md"):
            return "docs"
        return "runtime"
    if (path.endswith(".md") or path in {"AGENTS.md", "CLAUDE.md"}
            or path.startswith("skills/") or path.startswith("docs/")):
        return "docs"
    return "other"


def classify_paths(data: bytes) -> list[str]:
    """Classify a Git ``--name-only -z`` byte stream in input order."""
    if not isinstance(data, bytes):
        raise TypeError("changed paths must be bytes")
    names = data.split(b"\0")
    if names and names[-1] == b"":
        names.pop()
    return [classify_path(name.decode("utf-8", "surrogateescape"))
            for name in names]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("changed_paths", help="file containing NUL-separated paths")
    args = parser.parse_args(argv)
    try:
        data = Path(args.changed_paths).read_bytes()
    except OSError as exc:
        print(f"cannot read changed paths: {exc}", file=sys.stderr)
        return 2
    for path_class in classify_paths(data):
        print(path_class)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
