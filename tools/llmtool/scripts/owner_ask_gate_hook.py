#!/usr/bin/env python3
"""Claude Code PreToolUse gate for PM Owner-ask GitHub comments.

Reads one hook payload from stdin. When the shell command posts a GitHub issue
or pull request comment whose body carries the waiting_owner marker, it must
also carry the owner-ask line. Otherwise the hook refuses with exit code 2 and
one fixed reason, without echoing the body. Every other command exits 0.
"""
from __future__ import annotations

import json
import re
import shlex
import sys

REASON = ("owner_ask_gate_hook: refused comment: missing owner-ask line "
          "or unreadable body file")
ASK_LINE = re.compile(r"<!-- owner-ask: v=1 reserved_row=R-[A-Z][A-Z0-9-]* -->\Z")
TEXT_FLAGS = ("--body", "-b")
FILE_FLAGS = ("--body-file", "-F")

try:
    from pm_owner_marker import MARKER_ASK
except ImportError:  # pragma: no cover - mirrors pm_owner_marker.MARKER_ASK
    MARKER_ASK = "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->"


def _comment_argv(command: str):
    """Return the arguments of a gh comment call, or None for anything else."""
    try:
        words = shlex.split(command, posix=True)
    except ValueError:
        return None
    for index in range(len(words) - 2):
        if (words[index].rsplit("/", 1)[-1] == "gh"
                and words[index + 1] in ("issue", "pr")
                and words[index + 2] == "comment"):
            return words[index + 3:]
    return None


def _body_spec(argv):
    """Return (from_file, value) for the effective body option, or None."""
    spec = None
    index = 0
    while index < len(argv):
        word = argv[index]
        if word in TEXT_FLAGS or word in FILE_FLAGS:
            if index + 1 < len(argv):
                spec = (word in FILE_FLAGS, argv[index + 1])
            index += 2
            continue
        if word.startswith("--body-file="):
            spec = (True, word[len("--body-file="):])
        elif word.startswith("--body="):
            spec = (False, word[len("--body="):])
        index += 1
    return spec


def _read_body(spec) -> str:
    from_file, value = spec
    if not from_file:
        return value
    with open(value, encoding="utf-8") as stream:
        return stream.read()


def _has_ask_line(body: str) -> bool:
    return any(ASK_LINE.match(line.strip()) for line in body.splitlines())


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (OSError, UnicodeError, ValueError):
        return 0
    if type(payload) is not dict or payload.get("tool_name") != "Bash":
        return 0
    tool_input = payload.get("tool_input")
    if type(tool_input) is not dict:
        return 0
    command = tool_input.get("command")
    if type(command) is not str:
        return 0
    argv = _comment_argv(command)
    if argv is None:
        return 0
    spec = _body_spec(argv)
    if spec is None:
        return 0
    try:
        body = _read_body(spec)
    except (OSError, UnicodeError):
        print(REASON, file=sys.stderr)
        return 2
    if MARKER_ASK in body and not _has_ask_line(body):
        print(REASON, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
