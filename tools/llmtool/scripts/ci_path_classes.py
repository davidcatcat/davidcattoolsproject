#!/usr/bin/env python3
"""Classify repository paths for the Linux and Mac CI selectors."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PATH_CLASSES = ("docs", "runtime", "gateway", "ai-loop", "workflow", "other")
LINUX_CLASS_FLAGS = {
    "docs": (False, True, False, False),
    "runtime": (True, False, False, False),
    "gateway": (True, False, True, False),
    "ai-loop": (False, False, False, True),
    "workflow": (True, False, False, False),
    "other": (True, False, False, False),
}


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
    # Python under skills/ is code: its tests run in the Linux shard.
    if path.startswith("skills/") and path.endswith(".py"):
        return "other"
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


def linux_flags(data: bytes) -> tuple[bool, bool, bool, bool]:
    """Return code, docs, gateway, and ai-loop flags for changed paths."""
    names = data.split(b"\0")
    if names and names[-1] == b"":
        names.pop()
    code = docs = gateway = ai_loop = False
    for raw_name in names:
        path = raw_name.decode("utf-8", "surrogateescape")
        path_class = classify_path(path)
        class_code, class_docs, class_gateway, class_ai_loop = LINUX_CLASS_FLAGS[path_class]
        code |= class_code
        docs |= class_docs or path.endswith(".md")
        gateway |= class_gateway
        ai_loop |= class_ai_loop
    return code, docs, gateway, ai_loop


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("changed_paths", help="file containing NUL-separated paths")
    parser.add_argument("--linux-flags", action="store_true",
                        help="print Linux code/docs/gateway/ai_loop booleans")
    args = parser.parse_args(argv)
    try:
        data = Path(args.changed_paths).read_bytes()
    except OSError as exc:
        print(f"cannot read changed paths: {exc}", file=sys.stderr)
        return 2
    if args.linux_flags:
        print(" ".join("true" if flag else "false" for flag in linux_flags(data)))
    else:
        for path_class in classify_paths(data):
            print(path_class)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
