"""Escape terminal controls only at the interactive human display boundary."""


_BIDI_CONTROLS = frozenset(chr(value) for value in (
    0x061C, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
    0x2066, 0x2067, 0x2068, 0x2069, 0x206A, 0x206B, 0x206C, 0x206D,
    0x206E, 0x206F,
))


def contains_unsafe_controls(text):
    """Allow ordinary Unicode whitespace; reject controls and bidi formatting."""
    return any((ord(c) < 32 and c not in '\n\r\t')
               or 0x7f <= ord(c) <= 0x9f or c in _BIDI_CONTROLS for c in text)


def terminal_safe_text(text):
    """Render controls visibly; preserve ordinary Unicode, LF and horizontal tabs."""
    out = []
    for char in text:
        code = ord(char)
        if char in ('\n', '\t') or (code >= 32 and not 0x7f <= code <= 0x9f and char not in _BIDI_CONTROLS):
            out.append(char)
        elif code <= 0xff:
            out.append('\\x' + format(code, '02x'))
        else:
            out.append('\\u' + format(code, '04x'))
    return ''.join(out)


def human_output(text, stream):
    """Escape only on TTYs; preserve exact JSON and redirected text."""
    return terminal_safe_text(text) if getattr(stream, 'isatty', lambda: False)() else text


def silence_broken_pipe(stream):
    """Prevent another buffered flush during interpreter shutdown after EPIPE."""
    import os
    try:
        descriptor = stream.fileno()
        sink = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(sink, descriptor)
        finally:
            os.close(sink)
    except (AttributeError, OSError, ValueError):
        pass  # In-memory/custom streams need no OS descriptor replacement.
