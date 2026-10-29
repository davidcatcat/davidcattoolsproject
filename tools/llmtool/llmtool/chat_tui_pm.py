"""Real-time PM conversation for the full-screen chat: adapter and display text.

The TUI talks to the Owner-designated, dedicated PM session only through
``pm_runtime`` (Owner decision G1 = a). This module never writes Core state
itself and never fakes a reply: every PM line comes from a pm_runtime event.

Built on pm_runtime S2: the dedicated-session binding (create_dedicated_pm,
current_binding; open() refuses an unbound session and one that looks live
elsewhere), turn_id/seq on every event, block_index on text deltas,
TOOL_RESULT.reason, idempotent send (TURN_REPLAY) and typed runtime codes.
What S2 still does not provide is shown as UNKNOWN.
"""

import re

from .chat_tui_task import UNKNOWN
from .test_output_excerpts import _emitted_sensitive

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
    "PM_SESSION_LIVE_ELSEWHERE": "the PM session looks open in another process (a claude process "
                                 "for it, or its transcript changed in the last 20 s); close "
                                 "that process or wait, then connect again",
    "PM_SESSION_NOT_DEDICATED": "the session must have a dedicated pm_runtime binding for this "
                                "working directory: /pm new [CWD] creates one, /pm connect uses it",
    "PM_QUOTA_EXHAUSTED": "the PM's Claude usage limit must reset; then send again",
    "PM_IDEMPOTENCY_KEY_INVALID": "internal TUI error: the send key was rejected; send again",
    "PM_BINDING_WRITE_FAILED": "pm_runtime could not save the binding; check the llmtool state "
                               "directory, then /pm new again",
    "PM_RUNTIME_UNSUPPORTED": "only the claude-code PM runtime is supported",
    "PM_PROCESS_START_FAILED": "the claude CLI must be installed and runnable; then connect again",
    "PM_PROCESS_EXITED": "the PM process ended; /pm disconnect, then /pm connect again",
    "PM_STREAM_INVALID": "the PM process sent output pm_runtime could not read; reconnect",
    "PM_SESSION_FORKED": "Claude Code started a different session; the TUI stopped driving it. "
                         "Check the dedicated session, then connect again",
    "PM_MESSAGE_INVALID": "the message must be non-empty text of at most 16 KiB without control "
                          "characters or credential material",
    "PM_TURN_ACTIVE": "wait for the current PM turn to end (Esc interrupts it)",
    "PM_RUNTIME_CLOSED": "connect with /pm connect",
    "PM_HISTORY_INVALID": "the stored PM history could not be read; the conversation itself is "
                          "unaffected",
    "PM_HISTORY_WRITE_FAILED": "pm_runtime could not save the turn history; check the llmtool state "
                               "directory",
    "PM_CONFIG_INVALID": "pm_runtime.claude_config_dir in ~/.config/llmtool/config.yaml must be "
                         "an absolute path; fix it, then connect again",
    "PM_STATE_INVALID": "the llmtool PM runtime state directory must be a plain user directory",
}


def error_code(exc):
    """The typed pm_runtime code of an exception, else UNKNOWN (never message text)."""
    return safe_code(getattr(exc, "pm_runtime_code", None))


def error_text(code):
    code = safe_code(code)
    hint = _RECOVERY.get(code)
    return ("PM runtime error " + code + "; retry when (TUI guidance): "
            + (hint or UNKNOWN + " — pm_runtime gives no recovery for this code"))


# -- pm_runtime calls (one place; tests substitute state_root and runners) ------------
class PMRuntimeAPI:
    """The pm_runtime S2 calls the TUI makes. Nothing here writes Core state except
    through pm_runtime itself (create_dedicated_pm, PMRuntime)."""

    def __init__(self, *, state_root=None, runner=None, environ=None):
        self.state_root, self.runner = state_root, runner
        self.environ = environ

    def runtime(self, session_id, cwd):
        from .pm_runtime import PMRuntime
        return PMRuntime(session_id, cwd=cwd, runner=self.runner, state_root=self.state_root,
                         environ=self.environ)

    def current_binding(self, session_id=None):
        from .pm_runtime import current_binding
        return current_binding(session_id, state_root=self.state_root)

    def create_dedicated(self, cwd):
        from .pm_runtime import create_dedicated_pm
        return create_dedicated_pm(cwd, state_root=self.state_root)

    def read_history(self, session_id):
        from .pm_runtime import read_history
        return read_history(session_id, after=0, limit=200, state_root=self.state_root)


def binding_text(binding):
    """Dedicated-binding status from pm_runtime.current_binding (a dict or None)."""
    if type(binding) is not dict:
        return "NOT dedicated (no pm_runtime binding)"
    return ("dedicated (pm_runtime binding created " + safe_text(binding.get("created_at"))
            + ")" if binding.get("dedicated") is True else "NOT dedicated")


def account_text(runtime):
    """The Claude account directory the connected PM runs under."""
    return "Claude account " + safe_text(getattr(runtime, "claude_config_dir", None))


def event_ids(event):
    """(turn_id, seq) when pm_runtime reports them on the event, else (None, None)."""
    turn = event.get("turn_id")
    seq = event.get("seq")
    return (turn if type(turn) is str and _HEX_ID.fullmatch(turn) else None,
            seq if type(seq) is int and type(seq) is not bool else None)


def tool_result_text(event, name):
    """One line for a TOOL_RESULT from S2's typed reason (OK / TOOL_ERROR /
    PERMISSION_DENIED). A missing or unknown reason is shown as such, never guessed."""
    label = "  · tool " + safe_name(name)
    reason = event.get("reason")
    if reason == "PERMISSION_DENIED":
        return label + ": auto-denied (pm_runtime reason PERMISSION_DENIED; permission prompts are off until S3)"
    if reason == "TOOL_ERROR":
        return label + ": error (pm_runtime reason TOOL_ERROR)"
    if reason == "OK":
        return label + ": done"
    return (label + (": error" if event.get("is_error") else ": result")
            + " (reason " + safe_code(reason) + ")")


def stderr_text(event):
    """First line of the stderr excerpt pm_runtime attaches to a runtime error."""
    value = event.get("stderr")
    if type(value) is not str or not value.strip():
        return None
    first = safe_text(value.strip().splitlines()[0])
    return first[:160] + ("…" if len(first) > 160 else "")


def replay_text(event):
    turn_id, seq = event_ids(event)
    return ("  — not sent again: pm_runtime already has this message as turn "
            + (turn_id[:8] if turn_id else UNKNOWN) + " #" + (str(seq) if seq is not None else UNKNOWN)
            + " (" + safe_code(event.get("status")) + ")")


# -- streamed text screening (pm_runtime screens only the stored result; gap G13) -----
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


# Every value a PM path shows passes one of these three (live events, history, codes).
_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_HEX_ID = re.compile(r"[0-9a-f]{1,64}")


def safe_text(value, missing=UNKNOWN):
    """Free text from pm_runtime or its stored history: recognisable credentials are
    redacted, and text that stays credential-like is withheld."""
    if type(value) is not str:
        return missing
    return screen_stream(value, final=True)


def safe_name(name):
    """A tool or other short name; withheld when credential-like."""
    if type(name) is not str or not name:
        return UNKNOWN
    return "[withheld]" if _emitted_sensitive(name) else name


def safe_code(value):
    """A typed code (error, status, reason) shown only when it has the code shape."""
    return value if type(value) is str and _CODE.fullmatch(value) else UNKNOWN


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
    status = safe_code(event.get("status"))
    turn_id, seq = event_ids(event)
    return ("  — PM turn " + status + " in %.1fs (measured locally); usage: " % elapsed
            + usage_text(event.get("usage")) + "; turn " + (turn_id[:8] if turn_id else UNKNOWN)
            + " event #" + (str(seq) if seq is not None else UNKNOWN))


def history_text(result):
    """Stored turns from pm_runtime.read_history, newest HISTORY_SHOWN, oldest first."""
    turns = result.get("turns") if type(result) is dict else None
    if type(turns) is not list or not turns:
        return "PM history: no stored turns"
    shown = [t for t in turns if type(t) is dict][-HISTORY_SHOWN:]
    lines = ["PM history (stored by pm_runtime; last %d of %d turns)" % (len(shown), len(turns))]
    for turn in shown:
        # Stored history is not validated by pm_runtime: every shown field is screened.
        tools = turn.get("tool_names") if type(turn.get("tool_names")) is list else []
        lines.append("(history) %s  owner → PM:\n%s" % (
            safe_text(turn.get("started_at")), safe_text(turn.get("owner_text"))))
        lines.append("(history) PM [%s]%s:\n%s" % (
            safe_code(turn.get("status")),
            (" tools: " + ", ".join(safe_name(t) for t in tools)) if tools else "",
            safe_text(turn.get("result_text"), "") or "(no stored reply text)"))
        if type(turn.get("usage")) is dict and turn["usage"]:
            # The pane shows a short usage line per turn; the full numbers are here (W56a).
            lines.append("(history) usage: " + usage_text(turn.get("usage")))
    return "\n".join(lines)
