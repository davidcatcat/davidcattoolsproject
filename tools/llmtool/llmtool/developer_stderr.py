"""#2503: a bounded, screened tail of a Developer CLI's stderr for the PM.

The excerpt is made where the raw stderr is read (run_route, the OpenCode
runner), so route results and errors carry only this screened text, never the
raw stream. It is Developer-runtime output and unverified.
"""

from __future__ import annotations

import unicodedata

from .test_output_excerpts import _emitted_sensitive

# The TaskStore bound for developer.stderr_excerpt (task_store imports it from
# here, so this module must not import task_store).
DEVELOPER_STDERR_EXCERPT_MAX_BYTES = 4 * 1024

# The whole stream is screened before any cut; a larger stream is withheld.
SCREEN_LIMIT_BYTES = 1024 * 1024
EARLIER_TRUNCATED = '[earlier text truncated] '
WITHHELD = '[stderr withheld: it did not pass the credential and character screen]'


def _normalized(raw):
    """Decode stderr; control and format characters become '?'."""
    text = raw.decode('utf-8', 'replace') if type(raw) is bytes else raw
    if type(text) is not str:
        return None
    return ''.join('?' if ((ord(char) < 32 and char not in '\t\n\r') or
                           127 <= ord(char) <= 159 or
                           unicodedata.category(char) == 'Cf') else char
                   for char in text).strip()


def _passes(text, limit):
    try:
        return len(text.encode('utf-8')) <= limit and not _emitted_sensitive(text)
    except Exception:
        return False


def _tail(text, kept):
    return EARLIER_TRUNCATED + text[len(text) - kept:].lstrip()


def excerpt(raw):
    """Return the screened stderr tail (at most the store limit), WITHHELD, or None.

    raw is the stream (bytes) or an excerpt made earlier (str); blank gives
    None. The whole text is screened first, so a cut never splits credential
    material past the screen; the tail (the last lines are the most useful) is
    kept, marked at the start when cut, and screened again. Any refusal gives
    only the fixed WITHHELD text: the stream is never reworded to pass."""
    limit = DEVELOPER_STDERR_EXCERPT_MAX_BYTES
    text = _normalized(raw)
    if not text:
        return None
    if not _passes(text, SCREEN_LIMIT_BYTES):
        return WITHHELD
    if len(text.encode('utf-8')) > limit:
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if len(_tail(text, middle).encode('utf-8')) <= limit:
                low = middle
            else:
                high = middle - 1
        text = _tail(text, low)
    return text if _passes(text, limit) else WITHHELD


def safe_excerpt(raw):
    """excerpt(), but never raises: an unexpected failure gives None."""
    try:
        return excerpt(raw)
    except Exception:
        return None
