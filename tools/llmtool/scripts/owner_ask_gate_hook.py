#!/usr/bin/env python3
"""PreToolUse gate for GitHub issue and pull request comments.

Reads one Claude Code hook payload from stdin. A Bash command that calls
``gh issue comment`` or ``gh pr comment`` is refused (exit 2) when the raw
command text, or any readable file given to ``--body-file`` or ``-F``, holds
more waiting_owner WAITING markers than exact owner-ask lines. The gate judges
text only and never parses shell words; any doubt refuses (fail closed).
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys


# Must stay identical to pm_owner_marker.MARKER_ASK (#1943).
MARKER_ASK = "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->"
OWNER_ASK_LINE = re.compile(r"<!-- owner-ask: v=1 reserved_row=R-[A-Z0-9-]+ -->")
COMMENT_CALL = re.compile(r"(?<![\w-])gh\s+(?:issue|pr)\s+comment\b")
BODY_FILE = re.compile(
    r"""(?<![\w-])(?:--body-file|-F)(?:\s+|=)("[^"]*"|'[^']*'|[^\s;|&]+)""")
REFUSAL = (
    "owner-ask gate: refused gh comment command: waiting_owner WAITING marker "
    "without a matching owner-ask: v=1 reserved_row=R-CODE line")


def _counts(text: str) -> tuple[int, int]:
    """Return (waiting_owner marker occurrences, exact owner-ask lines)."""
    asks = sum(1 for line in text.splitlines() if OWNER_ASK_LINE.fullmatch(line))
    return text.count(MARKER_ASK), asks


def _body_file_texts(command: str):
    """Yield the text of each readable file named after --body-file or -F."""
    for match in BODY_FILE.finditer(command):
        path = match.group(1)
        if len(path) > 1 and path[0] == path[-1] and path[0] in "\"'":
            path = path[1:-1]
        if not path or path == "-":
            continue
        target = Path(path)
        try:
            if not target.is_file():
                continue
            yield target.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0  # Not hook JSON: the contract fails open only here.
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return 0
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or COMMENT_CALL.search(command) is None:
        return 0
    for text in (command, *_body_file_texts(command)):
        markers, asks = _counts(text)
        if markers > asks:
            print(REFUSAL, file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
