"""Read only explicitly provided data. Never discover files or invoke Git."""

import os
import stat
import io
from pathlib import Path

from .errors import LLMToolError

INPUT_LIMIT = 16 * 1024 * 1024


def _stdin_has_data(stream):
    """Probe queued input without reading, polling or waiting for a writer.

    POSIX pipes use FIONREAD. Unsupported streams fail closed. A writer that
    supplies data only after this snapshot is not waited for in explicit mode.
    """
    if stream.isatty():
        return False
    if isinstance(stream, (io.StringIO, io.BytesIO)):
        return stream.tell() < len(stream.getvalue())
    descriptor = stream.fileno()
    mode = os.fstat(descriptor).st_mode
    if stat.S_ISREG(mode):
        return os.lseek(descriptor, 0, os.SEEK_CUR) < os.fstat(descriptor).st_size
    if stat.S_ISCHR(mode) and os.fstat(descriptor).st_rdev == os.stat(os.devnull).st_rdev:
        return False
    if os.name != "posix" or not (stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)):
        raise LLMToolError("cannot safely probe stdin; use a terminal or empty regular input")
    import array
    import fcntl
    import termios
    queued = array.array("i", [0])
    fcntl.ioctl(descriptor, termios.FIONREAD, queued, True)
    return queued[0] > 0


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
        if (text is not None or filename is not None) and _stdin_has_data(stdin):
            raise LLMToolError("provide only one of positional text, --file, or non-empty stdin")
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
