"""Read only explicitly provided data. Never discover files or invoke Git."""

import os
import stat
from pathlib import Path

from .errors import LLMToolError

INPUT_LIMIT = 16 * 1024 * 1024


def _decode(raw):
    if len(raw) > INPUT_LIMIT:
        raise LLMToolError("input exceeds 16 MiB")
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        raise LLMToolError("input must be UTF-8 text, not binary") from None
    if any(ord(c) < 32 and c not in "\t\n\r\f\x1b" for c in text):
        raise LLMToolError("binary input is not supported")
    return text


def read_input(text, filename, stdin):
    if text is not None and filename is not None:
        raise LLMToolError("provide only one of positional text, --file, or non-empty stdin")
    try:
        if text is not None:
            raw = text.encode("utf-8")
        elif filename is not None:
            path = Path(filename)
            if not path.is_file():
                raise LLMToolError("--file requires a readable regular file")
            with path.open("rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise LLMToolError("--file requires a readable regular file")
                raw = stream.read(INPUT_LIMIT + 1)
        else:
            raw = b"" if stdin.isatty() else stdin.buffer.read(INPUT_LIMIT + 1)
    except (OSError, UnicodeError, ValueError, AttributeError, RuntimeError):
        raise LLMToolError("cannot read input as UTF-8 text") from None
    result = _decode(raw)
    if not result.strip():
        raise LLMToolError("input is empty; provide text, --file, or stdin")
    return result
