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
import os
import shutil
import stat
import subprocess
from datetime import datetime

from .chat_tui_task import UNKNOWN
from .test_output_excerpts import _emitted_sensitive

HISTORY_SHOWN = 20  # Most recent stored turns rendered on connect.
TOOL_LABEL_CELLS = 120  # W57: most display cells of a tool line's description.
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
    "PM_CONFIG_INVALID": "the selected PM runtime configuration in ~/.config/llmtool/config.yaml "
                         "is invalid; check pm_runtime.claude_config_dir for Claude or "
                         "for Codex, set roles.pm.account to an accounts.<name> entry with kind: codex "
                         "and config_dir, then configure roles.pm.model and connect again",
    "PM_STATE_INVALID": "the llmtool PM runtime state directory must be a plain user directory",
}


def permission_mode():
    """The permission mode pm_runtime starts the PM with (W56d status line)."""
    from . import pm_runtime
    return pm_runtime.PM_PERMISSION_MODE


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
        binding = self.current_binding(session_id)
        return PMRuntime(session_id, cwd=cwd, runner=self.runner, state_root=self.state_root,
                         environ=self.environ,
                         runtime=(binding or {}).get('runtime', 'claude-code'),
                         account=(binding or {}).get('account'))

    def current_binding(self, session_id=None):
        from .pm_runtime import current_binding
        return current_binding(session_id, state_root=self.state_root, environ=self.environ,
                               home=None if self.environ is None else self.environ.get('HOME'))

    def create_dedicated(self, cwd, runtime='claude-code', account=None):
        from .pm_runtime import create_dedicated_pm
        if account is not None:
            kind = next((row['kind'] for row in self.accounts() if row['name'] == account), None)
            expected = 'codex' if runtime == 'codex' else 'claude'
            if kind != expected:
                from .errors import LLMToolError
                error = LLMToolError('PM runtime and account kind do not match')
                error.pm_runtime_code = 'PM_CONFIG_INVALID'
                raise error
        return create_dedicated_pm(cwd, runtime=runtime, account=account,
                                   state_root=self.state_root, environ=self.environ,
                                   home=None if self.environ is None else self.environ.get('HOME'))

    def list_bindings(self):
        from .pm_runtime import list_bindings
        return list_bindings(state_root=self.state_root, environ=self.environ,
                             home=None if self.environ is None else self.environ.get('HOME'))

    def select_binding(self, session_id):
        from .pm_runtime import select_binding
        return select_binding(session_id, state_root=self.state_root)

    def accounts(self):
        """Return only account metadata needed by the TUI, never config contents."""
        from . import config, role_config
        home = None if self.environ is None else self.environ.get('HOME')
        path = config.user_config_path(home)
        user = config._read_yaml(path, 'user config') if os.path.lexists(path) else {}
        accounts, _ = role_config.validate_role_config(user)
        rows = []
        for name, value in sorted(accounts.items()):
            folder = value['config_dir']
            try:
                info = os.lstat(folder)
                private = (stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and
                           not info.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
                status = 'private' if private else 'not private'
            except OSError:
                private, status = False, 'unavailable'
            rows.append({'name': name, 'kind': value['kind'], 'status': status,
                         'config_dir': folder if private else None})
        return rows

    def account_checks(self):
        """Run provider login status probes, discarding all provider output."""
        results = []
        source = os.environ if self.environ is None else self.environ
        for account in self.accounts():
            if account['status'] != 'private':
                results.append(False)
                continue
            kind = account['kind']
            executable = shutil.which(kind, path=source.get('PATH'))
            if not executable:
                results.append(False)
                continue
            env = dict(source)
            if kind == 'claude':
                env['CLAUDE_CONFIG_DIR'] = account['config_dir']
                argv = [executable, 'auth', 'status']
            else:
                env['CODEX_HOME'] = account['config_dir']
                argv = [executable, 'login', 'status']
            try:
                result = subprocess.run(argv, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        env=env, timeout=10, check=False)
                results.append(result.returncode == 0)
            except (OSError, subprocess.SubprocessError):
                results.append(False)
        return results

    def read_history(self, session_id):
        from .pm_runtime import read_history
        return read_history(session_id, after=0, limit=200, state_root=self.state_root)

    def read_all_turns(self, session_id):
        from .pm_runtime import read_history
        turns = []
        after = 0
        while True:
            page = read_history(session_id, after=after, limit=200, state_root=self.state_root)
            page_turns = page['turns']
            if not page_turns:
                return turns
            turns.extend(page_turns)
            after = page['next_after']


_USAGE_KEYS = ('input_tokens', 'output_tokens', 'cache_read_input_tokens',
               'cache_creation_input_tokens')


def usage_totals(turns):
    """Return recorded token totals and the number of recorded and missing turns."""
    totals = {key: None for key in _USAGE_KEYS}
    counted = missing = 0
    for turn in turns:
        usage = turn.get('usage') if type(turn) is dict else None
        recorded = False
        if type(usage) is dict:
            for key in _USAGE_KEYS:
                value = usage.get(key)
                if type(value) is int and value >= 0:
                    if totals[key] is None:
                        totals[key] = 0
                    totals[key] += value
                    recorded = True
        if recorded:
            counted += 1
        else:
            missing += 1
    return totals, counted, missing


def usage_status_line(turns, formatter):
    """Format PM turn usage, preserving UNKNOWN when no turns recorded usage."""
    if not turns:
        return '用量（本 session）：尚無回合'
    totals, counted, missing = usage_totals(turns)
    if not counted:
        return '用量（本 session）：UNKNOWN（%d 回合沒有用量紀錄）' % missing
    def display(key):
        return '未回報' if totals[key] is None else formatter(totals[key])

    line = ('用量（本 session）：輸入 %s · 輸出 %s · 快取讀取 %s · 快取建立 %s tokens（%d 回合'
            % (display('input_tokens'), display('output_tokens'),
               display('cache_read_input_tokens'), display('cache_creation_input_tokens'), counted))
    if missing:
        line += '，%d 回合沒有紀錄' % missing
    return line + '）'


def binding_text(binding):
    """Dedicated-binding status from pm_runtime.current_binding (a dict or None)."""
    if type(binding) is not dict:
        return "NOT dedicated (no pm_runtime binding)"
    return ("dedicated (pm_runtime binding created " + safe_text(binding.get("created_at"))
            + ")" if binding.get("dedicated") is True else "NOT dedicated")


def account_text(runtime, binding=None):
    """Display the selected provider and configured account name, never its directory."""
    provider = (binding.get('runtime') if type(binding) is dict else None) or \
        getattr(runtime, 'runtime', 'claude-code')
    account = binding.get('account') if type(binding) is dict else None
    return safe_text(provider) + " account " + safe_text(account or "default")


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


def tool_label(event):
    """A screened tool description, or its name; never display a command or tool output."""
    from .tui_markdown import display_width
    from . import pm_runtime
    raw_name = event.get("name")
    name = safe_name(raw_name)
    if (not isinstance(raw_name, str) or '/' in raw_name
            or not all(char.isprintable() for char in raw_name)):
        name = "Tool"
    text = event.get("description")
    if type(text) is not str:
        return name
    text = " ".join("".join(char for char in text
                            if char.isprintable() or char.isspace()).split())
    text = text[:pm_runtime.TOOL_DESCRIPTION_CHARS]
    shown = safe_text(text) if text else WITHHELD
    if shown == WITHHELD or not shown.strip():
        return name
    if display_width(shown) <= TOOL_LABEL_CELLS:
        return shown
    cut, used = [], 0
    for char in shown:
        width = display_width(char)
        if used + width > TOOL_LABEL_CELLS - 1:
            break
        cut.append(char)
        used += width
    return "".join(cut) + "…"


def _local_time(value, now=None):
    """A stored UTC ISO time as local HH:MM, with the date when it is not today."""
    try:
        when = datetime.fromisoformat(value).astimezone()
    except (TypeError, ValueError):
        return "時間不明"
    now = now or datetime.now().astimezone()
    return when.strftime("%H:%M" if when.date() == now.date() else "%m-%d %H:%M")


def _local_history_time(value):
    """A stored turn's local date and time for the replay header."""
    try:
        when = datetime.fromisoformat(value).astimezone()
    except (TypeError, ValueError):
        return "時間不明"
    return when.strftime("%m-%d %H:%M")


def _elapsed(turn):
    try:
        seconds = (datetime.fromisoformat(turn["ended_at"])
                   - datetime.fromisoformat(turn["started_at"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


def history_turns(result, now=None):
    """Return screened fields for the newest HISTORY_SHOWN stored turns."""
    turns = result.get("turns") if type(result) is dict else None
    turns = [turn for turn in turns if type(turn) is dict] if type(turns) is list else []
    shown = []
    for turn in turns[-HISTORY_SHOWN:]:
        tools = turn.get("tool_names") if type(turn.get("tool_names")) is list else []
        shown.append({
            "time": _local_time(turn.get("started_at"), now),
            "header_time": _local_history_time(turn.get("started_at")),
            "background": turn.get("origin") == "background",
            "owner": safe_text(turn.get("owner_text")),
            "status": safe_code(turn.get("status")),
            "reply": safe_text(turn.get("result_text"), ""),
            "tools": [safe_name(name) for name in tools],
            "usage": turn.get("usage") if type(turn.get("usage")) is dict else {},
            "elapsed": _elapsed(turn)})
    return shown, len(turns)
