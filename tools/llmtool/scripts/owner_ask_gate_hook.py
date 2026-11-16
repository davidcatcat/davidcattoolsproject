#!/usr/bin/env python3
"""PreToolUse gate for GitHub issue and pull request comments.

Reads one Claude Code hook payload from stdin. The gate fails closed: stdin
that is not hook JSON with a Bash ``tool_input.command`` string is refused
(exit 2), and a Bash command that calls ``gh issue comment`` or ``gh pr
comment`` is refused whenever the full comment body cannot be known. That
covers command text holding a variable, backtick or ``$(`` expansion, and
every ``--body-file`` or ``-F`` path that is stdin, missing, unreadable,
non-regular, a symlink, or larger than 65536 bytes. A command or body file is
also refused when it holds more waiting_owner WAITING markers than exact
owner-ask lines. The gate judges text only and never parses shell words; any
doubt refuses.
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys


# Must stay identical to pm_owner_marker.MARKER_ASK (#1943).
MARKER_ASK = "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->"
OWNER_ASK_LINE = re.compile(r"<!-- owner-ask: v=1 reserved_row=R-[A-Z0-9-]+ -->")
COMMENT_CALL = re.compile(r"(?<![\w-])gh\s+(?:issue|pr)\s+comment\b")
BODY_FILE = re.compile(
    r"""(?<![\w-])(?:--body-file|-F)(?:\s+|=)("[^"]*"|'[^']*'|[^\s;|&]+)""")
MAX_BODY_BYTES = 65536
REFUSAL = (
    "owner-ask gate: refused gh comment command: waiting_owner WAITING marker "
    "without a matching owner-ask: v=1 reserved_row=R-CODE line")
REFUSAL_UNKNOWN_TEXT = (
    "owner-ask gate: refused gh comment command: waiting_owner gate cannot know "
    "the whole body because the command text has a variable, backtick or $( expansion")
REFUSAL_BODY_FILE = (
    "owner-ask gate: refused gh comment command: waiting_owner gate needs a "
    "readable regular non-symlink --body-file/-F of at most 65536 bytes, not stdin")
REFUSAL_PAYLOAD = (
    "owner-ask gate: refused hook payload: waiting_owner gate needs hook JSON with "
    "a Bash tool_input.command string")


class Refusal(Exception):
    """A fail-closed refusal carrying the fixed stderr message."""


def _counts(text: str) -> tuple[int, int]:
    """Return (waiting_owner marker occurrences, exact owner-ask lines)."""
    asks = sum(1 for line in text.splitlines() if OWNER_ASK_LINE.fullmatch(line))
    return text.count(MARKER_ASK), asks


def _unquote(value: str) -> str:
    if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _read_body_file(path: str) -> str:
    """Read one body file once, refusing anything but a small regular file.

    The size and type checks use the descriptor that is read, so no separate
    pre-open stat can be raced past, and a symlink is never followed.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise Refusal(REFUSAL_BODY_FILE) from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BODY_BYTES:
            raise Refusal(REFUSAL_BODY_FILE)
        data = os.read(descriptor, MAX_BODY_BYTES + 1)
    except OSError as exc:
        raise Refusal(REFUSAL_BODY_FILE) from exc
    finally:
        os.close(descriptor)
    if len(data) > MAX_BODY_BYTES:
        raise Refusal(REFUSAL_BODY_FILE)
    return data.decode("utf-8", "replace")


def _check(command: str) -> None:
    """Raise Refusal unless the whole known comment body is marker-consistent."""
    if "$" in command or "`" in command:
        raise Refusal(REFUSAL_UNKNOWN_TEXT)
    markers, asks = _counts(command)
    if markers > asks:
        raise Refusal(REFUSAL)
    for match in BODY_FILE.finditer(command):
        path = _unquote(match.group(1))
        if path == "-":
            raise Refusal(REFUSAL_BODY_FILE)
        text = _read_body_file(path)
        markers, asks = _counts(text)
        if markers > asks:
            raise Refusal(REFUSAL)


def _refuse(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return _refuse(REFUSAL_PAYLOAD)
    if not isinstance(payload, dict):
        return _refuse(REFUSAL_PAYLOAD)
    tool_name = payload.get("tool_name")
    if tool_name != "Bash":
        if isinstance(tool_name, str) and tool_name:
            return 0
        return _refuse(REFUSAL_PAYLOAD)
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not command:
        return _refuse(REFUSAL_PAYLOAD)
    if COMMENT_CALL.search(command) is None:
        return 0
    try:
        _check(command)
    except Refusal as exc:
        return _refuse(exc.args[0])
    except OSError:
        return _refuse(REFUSAL_BODY_FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
