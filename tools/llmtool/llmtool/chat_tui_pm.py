"""Real-time PM conversation for the full-screen chat: adapter and display text.

The TUI talks to the Owner-designated, dedicated PM session only through
``pm_runtime`` (Owner decision G1 = a). This module never writes Core state
itself and never fakes a reply: every PM line comes from a pm_runtime event.

pm_runtime S1 is the API in use. Features expected from S2 (dedicated binding,
turn_id/seq on every event, TOOL_RESULT.reason, PM_SESSION_LIVE_ELSEWHERE) are
read through the small functions below; while S1 does not report them, the TUI
shows "not reported" instead of guessing.
"""

import re

from .chat_tui_task import UNKNOWN
from .test_output_excerpts import _emitted_sensitive

NOT_REPORTED = "not reported by pm_runtime S1"
HISTORY_SHOWN = 20  # Most recent stored turns rendered on connect.
WITHHELD = "[PM text withheld: it contains credential-like text]"
# While a PM reply streams, the ASCII token still growing at the end (any run of
# non-space ASCII) is not shown until it ends, so a credential split across deltas is
# complete, and redacted, before any part of it is shown. CJK text streams at once.
_OPEN_RUN = re.compile(r"[!-~]+$")

# Recovery guidance per pm_runtime error code (TUI text; pm_runtime returns codes only).
_RECOVERY = {
    "PM_SESSION_INVALID": "the dedicated PM session ID must be a Claude Code session UUID whose "
                          "transcript exists for the given working directory",
    "PM_SESSION_BUSY": "another llmtool driver holds this PM session; close it, then connect again",
    "PM_SESSION_LIVE_ELSEWHERE": "the PM session is open in another process; close that process "
                                 "(the TUI drives only a dedicated session), then connect again",
    "PM_RUNTIME_UNSUPPORTED": "only the claude-code PM runtime is supported",
    "PM_PROCESS_START_FAILED": "the claude CLI must be installed and runnable; then connect again",
    "PM_PROCESS_EXITED": "the PM process ended; /pm disconnect, then /pm connect again",
    "PM_STREAM_INVALID": "the PM process sent output pm_runtime could not read; reconnect",
    "PM_SESSION_FORKED": "Claude Code started a different session; the TUI stopped driving it. "
                         "Check the dedicated session, then connect again",
    "PM_MESSAGE_INVALID": "the message must be non-empty text of at most 16 KiB without control "
                          "characters or credential material",
    "PM_TURN_ACTIVE": "wait for the current PM turn to end (Esc interrupts it)",
    "PM_RUNTIME_CLOSED": "connect with /pm connect SESSION [CWD]",
    "PM_HISTORY_INVALID": "the stored PM history could not be read; the conversation itself is "
                          "unaffected",
    "PM_HISTORY_WRITE_FAILED": "pm_runtime could not save the turn history; check the llmtool state "
                               "directory",
    "PM_STATE_INVALID": "the llmtool PM runtime state directory must be a plain user directory",
}


def error_code(exc):
    """The typed pm_runtime code of an exception, else UNKNOWN (never message text)."""
    code = getattr(exc, "pm_runtime_code", None)
    return code if type(code) is str and code.isupper() and len(code) <= 64 else UNKNOWN


def error_text(code):
    hint = _RECOVERY.get(code)
    return ("PM runtime error " + code + "; retry when (TUI guidance): "
            + (hint or UNKNOWN + " — pm_runtime gives no recovery for this code"))


# -- S2 adapter points --------------------------------------------------------------
def binding_text(_runtime):
    """Dedicated-binding status. S1 keeps no binding record; S2's API plugs in here once
    it is merged (its name and shape are not known yet, so nothing is probed)."""
    return "dedicated: " + NOT_REPORTED


def event_ids(event):
    """(turn_id, seq) when pm_runtime reports them on the event, else (None, None)."""
    turn = event.get("turn_id")
    seq = event.get("seq")
    return (turn if type(turn) is str else None, seq if type(seq) is int else None)


def tool_result_text(event, name):
    """One line for a TOOL_RESULT; never claims a permission denial S1 cannot report."""
    label = "  · tool " + safe_name(name)
    if not event.get("is_error"):
        return label + ": done"
    reason = event.get("reason")
    if reason == "PERMISSION_DENIED":
        return label + ": auto-denied (permission prompts are off until S3)"
    if type(reason) is str and reason:
        return label + ": error (" + reason + ")"
    return (label + ": error (an auto-denied permission is possible; pm_runtime does not "
            "report the reason)")


# -- streamed text screening (pm_runtime S1 screens only the stored result) ----------
def screen_stream(raw, final):
    """Display text for one streamed PM block.

    The whole block is redacted with the existing credential patterns every time, so
    a secret split across deltas is matched once it is complete. While streaming, the
    ASCII token still growing at the end and an unterminated private-key block are
    not shown yet. If anything credential-like remains unclassified, the block is
    withheld entirely.
    """
    from .task_submission_cli import _redact_rejected_output
    if type(raw) is not str:
        return WITHHELD
    shown = _redact_rejected_output(raw)
    if shown.startswith("[REDACTED_SENSITIVE_OUTPUT]") or _emitted_sensitive(shown):
        return WITHHELD
    if final:
        return shown
    cut = len(shown)
    begin = shown.rfind("-----BEGIN")
    if begin != -1 and "-----END" not in shown[begin:]:
        cut = min(cut, begin)
    run = _OPEN_RUN.search(shown)
    if run is not None:
        cut = min(cut, run.start())
    return shown[:cut]


def safe_name(name):
    if type(name) is not str or not name:
        return UNKNOWN
    return "[withheld]" if _emitted_sensitive(name) else name


# -- display helpers -----------------------------------------------------------------
def usage_text(usage):
    if type(usage) is not dict or not usage:
        return UNKNOWN
    parts = []
    for key in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens"):
        value = usage.get(key)
        if type(value) is int:
            parts.append(key.replace("_tokens", "").replace("_", " ") + " " + str(value))
    return (", ".join(parts) + " (provider-reported)") if parts else UNKNOWN


def turn_end_text(event, elapsed):
    status = event.get("status") if type(event.get("status")) is str else UNKNOWN
    turn_id, _seq = event_ids(event)
    return ("  — PM turn " + status + " in %.1fs (measured locally); usage: " % elapsed
            + usage_text(event.get("usage")) + "; turn " + (turn_id[:8] if turn_id else UNKNOWN))


def history_text(result):
    """Stored turns from pm_runtime.read_history, newest HISTORY_SHOWN, oldest first."""
    turns = result.get("turns") if type(result) is dict else None
    if type(turns) is not list or not turns:
        return "PM history: no stored turns"
    shown = [t for t in turns if type(t) is dict][-HISTORY_SHOWN:]
    lines = ["PM history (stored by pm_runtime; last %d of %d turns)" % (len(shown), len(turns))]
    for turn in shown:
        owner = turn.get("owner_text") if type(turn.get("owner_text")) is str else UNKNOWN
        result_text = turn.get("result_text") if type(turn.get("result_text")) is str else ""
        tools = turn.get("tool_names") if type(turn.get("tool_names")) is list else []
        lines.append("(history) %s  owner → PM:\n%s" % (turn.get("started_at") or UNKNOWN, owner))
        lines.append("(history) PM [%s]%s:\n%s" % (
            turn.get("status") or UNKNOWN,
            (" tools: " + ", ".join(str(t) for t in tools)) if tools else "",
            result_text or "(no stored reply text)"))
    return "\n".join(lines)
