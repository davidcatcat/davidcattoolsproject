#!/usr/bin/env python3
"""Claude Code PreToolUse gate for PM Owner-ask GitHub comments.

Reads one hook payload from stdin. Every GitHub issue or pull request comment
in the shell command, including each call in a compound command, is judged on
its own body: when the body carries the waiting_owner marker it must also
carry the owner-ask line. A body file the hook cannot read (stdin ``-``, a
relative path, ``~`` or ``$VAR``) is judged on the command text instead, so a
routine comment is not refused only because its file is out of reach. A
refusal exits 2 with one fixed reason, without echoing the body. Every other
command exits 0.
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
# Shell control operators end one simple command. shlex.split keeps them as
# standalone words, so each gh comment call is judged on its own body.
SEPARATORS = frozenset({";", ";;", "&", "&&", "|", "||", "|&", "(", ")"})
STDIN_BODY = "-"

try:
    from pm_owner_marker import MARKER_ASK
except ImportError:  # pragma: no cover - mirrors pm_owner_marker.MARKER_ASK
    MARKER_ASK = "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->"


def _is_comment_start(words, index: int) -> bool:
    return (index + 2 < len(words)
            and words[index].rsplit("/", 1)[-1] == "gh"
            and words[index + 1] in ("issue", "pr")
            and words[index + 2] == "comment")


def _comment_argvs(command: str):
    """Return one argument list per gh issue/pr comment call in the command."""
    try:
        words = shlex.split(command, posix=True)
    except ValueError:
        return []
    starts = [index for index in range(len(words) - 2)
              if _is_comment_start(words, index)]
    argvs = []
    for start in starts:
        end = start + 3
        while (end < len(words) and words[end] not in SEPARATORS
               and not _is_comment_start(words, end)):
            end += 1
        argvs.append(words[start + 3:end])
    return argvs


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


def _has_ask_line(body: str) -> bool:
    return any(ASK_LINE.match(line.strip()) for line in body.splitlines())


def _asks(body: str) -> bool:
    return MARKER_ASK in body and not _has_ask_line(body)


def _body_refuses(spec, command: str) -> bool:
    """Return True when the effective body is an ungated Owner ask.

    An unreadable body (stdin ``-`` or a path that does not resolve from the
    hook process) is judged on the command text, which carries a heredoc or
    redirect body, instead of being refused for being unreadable.
    """
    from_file, value = spec
    if not from_file:
        return _asks(value)
    if value != STDIN_BODY:
        try:
            with open(value, encoding="utf-8") as stream:
                return _asks(stream.read())
        except (OSError, UnicodeError):
            pass
    return _asks(command)


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
    argvs = _comment_argvs(command)
    for argv in argvs:
        spec = _body_spec(argv)
        if spec is not None and _body_refuses(spec, command):
            print(REASON, file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
