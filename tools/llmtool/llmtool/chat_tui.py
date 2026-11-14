"""Opt-in full-screen chat UI over the existing ChatSession (A3.5 UX-1/UX-2).

Display layer only: ChatSession still owns config, provider/skill selection,
history (memory-only or saved locally), requests and errors. No tools, shell,
Git or file writes. Model output is untrusted and rendered via terminal_safe_text.
prompt-toolkit is an optional dependency (``llmtool[tui]``); when it is absent
or stdin/stdout is not a terminal, the existing line chat runs instead, before
any UI starts. A UI failure never drops into, or replays input through, the REPL.
"""

import asyncio
import collections
import os
import re
import shlex
import signal
import stat
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import (__version__, chat_tui_events, chat_tui_pm, chat_tui_status, chat_tui_task, chat_tui_theme,
               chat_tui_view, pm_runtime)
from .chat import HELP, run_chat
from .config import selected_provider
from .errors import LLMToolError
from .providers.cli_lifecycle import CLIHostTermination, background_cli_invocation, tui_cli_signal_scope
from .terminal import terminal_safe_text

# A template: key_help() fills in the developer-only parts (W57).
KEY_HELP = """Keys
  Enter                 Send message (or run /command)
  換行 = Shift+Enter (terminal must send it) / Option+Enter / Ctrl-J
                        Insert newline; paste keeps newlines
                        macOS Terminal.app sends plain Enter for Shift+Enter; use Ctrl-J
                        or Option+Enter, or configure Terminal → Settings → Profiles →
                        Keyboard: add Shift+Return → Send Text \\033[13;2u
  Up/Down               Move in input; on first/last line recall earlier inputs
  /                     Command picker (existing commands only): type to filter, Up/Down
                        choose and scroll, Enter completes the draft to the choice without
                        running it (a draft already equal to the choice runs, as without
                        the picker), Esc closes the picker; Esc Enter still inserts a newline
  Ctrl-R                Search this session's earlier inputs: type to filter, Ctrl-R/Up/Down
                        choose, Enter puts the entry in the draft without running it,
                        Esc cancels and leaves the draft unchanged
  Tab                   Complete a slash command; elsewhere switch focus to/from the conversation
  Shift-Tab             Switch focus between input and conversation
  PageUp/PageDown       Scroll conversation one screen (Fn+↑/↓ on a Mac keyboard); when it is
                        focused Up/Down scroll one row, Home/End go to the top/bottom
  Mouse wheel           Scroll conversation one row per wheel step (trackpad works the same);
                        the view stays where you read it while output arrives, and scrolling
                        back to the bottom follows new output again (status shows 最新)
  Drag                  Select text in the conversation or a read-only pane (Task detail,
                        lists); dragging past the top/bottom edge keeps
                        scrolling and extends the selection. The selection is copied to the
                        system clipboard while you drag and again on release (pbcopy; status
                        shows 已複製), so Cmd+V pastes exactly it; no Cmd+C is needed.
                        Double-click selects a word. Ctrl-C with a selection copies it again
                        and clears it instead of exiting
                        Warp and Terminal.app: drag, then Cmd+V where you want the text.
                        Cmd+C goes to the terminal, never to llmtool: it copies only the
                        terminal's own selection and, with none, leaves the clipboard (which
                        already holds the drag) unchanged
  Shift-drag (Warp), Fn-drag (Terminal.app)
                        The terminal's own selection while mouse reporting is on; Cmd+C
                        then copies it as in any terminal
  F6                    Toggle mouse reporting off/on: off hands the mouse to the terminal
                        (its own select/copy; the wheel then no longer scrolls)
  F7                    Expand/collapse the latest PM turn's tool summary (one line per
                        call: what it does, its result and seconds; never its command
                        or output)
  F1                    Toggle this help (shown in place of the conversation)
%s  Esc (during a reply)  Interrupt the chat reply: a streamed reply stops at its next chunk
                        and the text shown so far stays, marked interrupted; a provider
                        that answers in one piece returns first, then that reply is shown
                        marked interrupted. Ctrl-C interrupts first, a second Ctrl-C exits.
                        Esc works with focus in the input or in the conversation pane
  Ctrl-C, Ctrl-D        Exit (Ctrl-D only with empty input)
%s
Talk to the PM: the dedicated PM (/pm …) is the product entry
  /pm list              List saved PM sessions; /pm use selects one without connecting
  /pm use N|SESSION     Select a saved session; then /pm connect to connect
  /pm new [--runtime claude|codex] [--account NAME] [CWD]
                        Create a dedicated PM session (CWD defaults to the current directory)
  /pm accounts [--check]  Show configured account names and folder status; --check prints
                        only login yes/no results, never provider output
  /pm connect [SESSION [CWD]]  Connect to the dedicated PM session: the saved binding when
                        no SESSION is given; "pm>" prompt, Enter sends
  /pm disconnect        End the connection and return to chat mode
  /pm history           Show the turns pm_runtime stored for this PM session
  /logs                 Show recent PM turns
  /attention            Show what needs attention
  /pm                   Show the connected PM and its state
  Enter (during a turn) Queue the draft (已排入佇列（第 N 則）): queued messages are sent one turn
                        at a time, in order, each after the previous turn COMPLETED; a
                        notice during a streaming reply is shown below it, never inside it
  /pm queue             List the queued messages; /pm queue clear drops them,
                        /pm queue send resumes a paused queue (sends them all)
  Esc (during a turn)   Interrupt the PM turn (focus in the input or the conversation pane)
                        and pause the queue: nothing more is sent. Enter on an empty draft
                        sends the next queued message, /pm queue send resumes them all.
                        Ctrl-C interrupts and pauses the queue; a second Ctrl-C exits
PM setup
  設定 PM（帳號、Codex、工作目錄、預設）：docs/guides/llmtool-pm-setup-zh-tw.md
A PM turn and a chat request never run together: in chat mode, Enter during a PM turn
keeps the draft and sends nothing until the turn ends.
A CWD with spaces: /pm new "/path/with space" or /pm new /path/with\\ space.
Esc, a failed or refused turn, Ctrl-C or a disconnect pauses the queue: nothing more is sent
until /pm queue send, and the pane lists what is waiting.
PM replies stream in as pm_runtime reports them, under a 「PM」 label; your message shows
under 「你」 below a rule with the local time. A turn's tool use is one line saying what it
does (▸ 執行中：description… 23s, counting every second, then ✓ description（4s）or
✗ description（38s，失敗）; the tool name only when the PM gave no description); F7 lists
every call. Commands and tool output are never
shown. The turn ends with a short usage line (seconds · tokens). /pm history replays stored
turns beneath the 「上次對話（時間）」 header. PM thinking is never shown as the answer: a busy line
above the input shows what the PM is doing and the seconds, and stays until the turn ends. It
also shows background work while the PM waits.
Chat replies are never sent to or shown as the PM.

Display
  /theme [dark|light|auto]
                        Colour theme; auto follows the terminal background (COLORFGBG) and
                        is dark when it is unknown. NO_COLOR or a colourless terminal always
                        draws without colour. Saved in ~/.local/state/llmtool/tui-settings.json
  /links [on|off]       Clickable path/file.py:123 (file://) and #123 (GitHub issue/PR of the
                        git remote) links; only on terminals with OSC 8 links (iTerm2, WezTerm,
                        Warp, kitty). Copy still gives the text as shown
  /settings [auto-resume on|off]  Automatic PM resume after reconnect (saved locally)
In PM mode the status bar starts with ▸▸ PM 模式 · the PM permission mode · 背景 N (Tasks of
this PM session with a Developer or Reviewer at work, read from TaskStore every %d s) ·
↓ 查看 · 佇列 N. With an empty input, Down opens the background list (role, Task/PR, state,
age); Esc closes it. In that list Up/Down choose a row and Enter opens its read-only Task
detail (Core state, PR link, recent events, read once from TaskStore); PM replies keep
arriving meanwhile, and Esc returns to the chat at the same place with the draft kept.
The detail lists its evidence and where each value comes from (PR, CONFLICT when TaskStore
names different PRs, PR state, CI head SHA, Reviewer source, read time; UNKNOWN with the
reason when Core has none). The detail has focus when it opens: c copies the Task ID it
shows and C (Shift-C) copies the whole detail (or a whole developer list) as plain text.
Tab moves to the input line, where c and C are typed as usual; Tab again returns. A drag in the detail selects and
copies like the conversation (double-click: a word; Ctrl-C copies the selection again).

A chat reply is never a Task: to dispatch work, /pm connect and tell the PM.%s"""

# W57: Task mode is a developer/test path, shown and usable only with LLMTOOL_TUI_DEV=1 or
# llmtool chat --tui --dev; without it /task prints TASK_HIDDEN and nothing else.
DEV_ENV = "LLMTOOL_TUI_DEV"
TASK_HIDDEN = "Task 模式是開發測試用；要派工請用 /pm connect 和 PM 說"
# #1819 (Owner B): the read-only Task views (/task list, /task show TASK_ID, /task events and
# their F2/F3/F4 panes) work without developer mode; /task submit and /task mode stay
# developer-only and print TASK_HIDDEN.
READ_ONLY_TASK_COMMANDS = ("/task list", "/task show", "/task events")
READ_TASK_HELP = """

Task views (read-only, local TaskStore; nothing is submitted or dispatched)
  /task show TASK_ID    Open a Task's detail
  /task list            Open the Task list (stage, update time, request) from TaskStore
  /task events          Open recent events across Tasks (inbox, GitHub, tests, reviews)"""
DEV_KEY_HELP = """  F2                    Toggle the last Task detail (Esc also closes it)
  F3                    Toggle the Task list (/task list): Up/Down choose, Enter opens the
                        detail, a shows all / hides completed or idle groups, F5 or r refreshes,
                        Esc closes; refreshes itself every
                        %d s while shown (local TaskStore read only, no AI call)
  F4                    Toggle recent events across Tasks (/task events): newest first;
                        F5 or r refreshes, Esc closes; never acknowledges or polls
""" % chat_tui_status.REFRESH_SECONDS
DEV_EXIT_HELP = "A Task submit in progress cannot be cancelled; exit waits for it to finish or time out.\n"
DEV_TASK_HELP = """

Developer mode (LLMTOOL_TUI_DEV=1 or --dev)
Formal task commands (developer/test path, separate from ordinary chat; results come
only from llmtool Core and nothing is dispatched)
  /task submit --provider PROFILE REQUEST
                        開發測試用：只登記，不會派工 — records one Task in Core (also from
                        chat mode, and to resend a request Task mode refused as a repeat)
  /task mode profile PROFILE
                        Task mode: Enter submits each non-command draft as a formal PM
                        Task to PM provider PROFILE (no implicit default); "task>" prompt
  /task mode off        Back to chat mode: Enter sends to the chat provider again
  /task show TASK_ID    Open a Task's detail
  /task list            Open the Task list (stage, update time, request) from TaskStore
  /task events          Open recent events across Tasks (inbox, GitHub, tests, reviews)
A PM turn and a Task request never run together either: after /task mode, Enter during a
PM turn keeps the draft and submits nothing until the turn ends.
A Task receipt shows the PM provider, Task ID, Run ID and state recorded by Core;
已建立／待派工 means no Developer has been dispatched.
Stage and next responsible are derived by the TUI from Core fields; values Core does
not record are UNKNOWN or NOT_AVAILABLE."""


def dev_enabled(environ=None):
    """The developer flag: LLMTOOL_TUI_DEV=1 (the --dev option passes dev=True)."""
    return (os.environ if environ is None else environ).get(DEV_ENV) == "1"


def key_help(dev):
    """The key and command help; Task submit and mode help only with the dev flag (#1819)."""
    if dev:
        return KEY_HELP % (DEV_KEY_HELP, DEV_EXIT_HELP, chat_tui_status.REFRESH_SECONDS,
                           DEV_TASK_HELP)
    return KEY_HELP % (DEV_KEY_HELP, "", chat_tui_status.REFRESH_SECONDS, READ_TASK_HELP)


def read_only_task_command(text):
    """#1819: True for exactly /task list, /task events or /task show TASK_ID."""
    words = text.split()
    return (words in (["/task", "list"], ["/task", "events"]) or
            (len(words) == 3 and words[:2] == ["/task", "show"]))


def task_store_root():
    """The local TaskStore root; only looked at here, never created."""
    return Path.home() / ".local" / "state" / "llmtool" / "tasks"


def task_store_absent():
    """#1819 (security v91 F1): True when nothing exists at the TaskStore root.

    Without developer mode the read-only views then show an empty list or events, or
    "task not found", and call nothing in TaskStore, which would create the root. Anything
    at the path (even an entry TaskStore refuses) still goes to TaskStore as before.
    """
    return not os.path.lexists(task_store_root())


TASK_NOT_FOUND = "task not found: %s (no local TaskStore)"


# Existing commands only: (label shown, text a completion puts in the draft, summary).
# Picking one edits the input draft; it never runs the command.
SLASH_COMMANDS = (
    ("/pm connect", "/pm connect", "Talk to the PM: connect to the dedicated PM session"),
    ("/pm new", "/pm new", "Create a dedicated PM session"),
    ("/pm", "/pm", "Show the PM connection"),
    ("/pm disconnect", "/pm disconnect", "End the PM connection"),
    ("/pm history", "/pm history", "Show recent PM turns"),
    ("/pm queue", "/pm queue", "List or resume queued PM messages"),
    ("/help", "/help", "Show commands"),
    ("/status", "/status", "狀態：PM 模式看 PM，聊天模式看供應商與模型"),
    ("/provider", "/provider ", "List profiles or switch provider"),
    ("/skill", "/skill ", "List skills, select one, or clear"),
    ("/clear", "/clear", "Clear conversation"),
    ("/history", "/history", "Show user/assistant turns"),
    ("/exit", "/exit", "Exit"),
    ("/quit", "/quit", "Exit"),
    ("/task submit", "/task submit --provider ",
     "開發測試用：只登記，不會派工 (--provider PROFILE REQUEST)"),
    ("/task show", "/task show ", "Show a task by TASK_ID"),
    ("/task mode", "/task mode ", "Task mode for drafts: profile PROFILE, or off"),
    ("/task list", "/task list", "Task list with status from TaskStore"),
    ("/task events", "/task events", "Recent Task events from TaskStore"),
    ("/theme", "/theme ", "Colour theme: dark, light or auto (saved)"),
    ("/links", "/links ", "Clickable file and #PR links: on or off (saved)"),
    ("/settings", "/settings ", "PM 重連後自動接續：/settings auto-resume on|off"),
    ("/pm list", "/pm list", "List dedicated PM sessions"),
    ("/pm use", "/pm use ", "Select a dedicated PM session without connecting"),
    ("/pm accounts", "/pm accounts", "List configured PM accounts and folder status"),
    ("/logs", "/logs", "PM 回合紀錄：時間、狀態、工具"),
    ("/attention", "/attention", "需要注意的事：卡住、失敗、未讀"),
)
MENU_LIMIT = 5  # Most picker rows shown at once; more matches scroll.
BACKGROUND_LIMIT = 6  # Most rows of the ↓ background panel shown below its header.
TASK_ID = re.compile(r"[0-9a-f]{32}")   # W59: a background row opens a detail only for one
TURN_ROW_REASON = "這一列是 PM 自行開始的背景回合，沒有 Task 記錄"


def _attention_text(value):
    text = " ".join(str(value).split())
    text = terminal_safe_text(text)
    return text if len(text) <= 160 else text[:159] + "…"


# Status bar fields in display order, and the order they drop when the row is too narrow.
STATUS_FIELDS = ("mode", "session", "provider", "model", "state", "queue", "usage", "keys",
                 "scroll", "mouse", "help")
STATUS_DROP_ORDER = ("help", "keys", "usage", "model", "provider", "scroll", "session", "mouse")
# PM mode (W56d): the longer ▸▸ lead stays; the scroll position and session go before the
# keys that act now (Esc 中斷, F7 工具).
PM_STATUS_DROP_ORDER = ("attention", "background_item", "help", "scroll", "session", "mouse", "identity", "keys")
STATUS_KEY_FIELDS = ("keys", "mouse", "help")   # drawn as status.key
STATUS_SEPARATOR = " | "
ESC_SEQUENCE_TIMEOUT = 0.4  # Seconds a lone Esc waits for a following Enter (Esc Enter = newline).
# PM typewriter: the CLI emits text in bursts (0.6–1 s apart); received text is queued and
# released every tick at PM_CHARS_PER_TICK (≈67 chars/s). A long queue raises the rate to
# len/PM_MAX_LAG_TICKS until it empties, so the pane never lags the stream by more than
# about PM_MAX_LAG_TICKS ticks (≈1 s). The end of a block, of the turn or an interrupt
# flushes everything at once: total time unchanged.
PM_TICK_SECONDS = 0.03
PM_CHARS_PER_TICK = 2
PM_MAX_LAG_TICKS = 33
PM_USAGE = ("usage: /pm new [--runtime claude|codex] [--account NAME] [CWD]\n"
            "       /pm list [--all]\n       /pm use N|SESSION\n"
            "       /pm accounts [--check]\n       /pm connect [SESSION [CWD]]\n"
            "       /pm disconnect\n       /pm history\n       /pm queue [clear|send]\n"
            "       /pm\n(a CWD with spaces: quote it or escape the spaces)")
PM_SUBCOMMAND_USAGE = {
    "new": "usage: /pm new [--runtime claude|codex] [--account NAME] [CWD]",
    "list": "usage: /pm list [--all]",
    "use": "usage: /pm use N|SESSION",
    "accounts": "usage: /pm accounts [--check]",
}


def _pm_subcommand_usage(command):
    return PM_SUBCOMMAND_USAGE.get(command, PM_USAGE)


PM_ACCOUNTS_GUIDANCE = (
    "Claude and Codex PM accounts are added under accounts in "
    "~/.config/llmtool/config.yaml. Example:\n"
    "accounts:\n"
    "  claude-NAME:\n"
    "    kind: claude\n"
    "    config_dir: <CLAUDE_CONFIG_DIR>\n"
    "  codex-NAME:\n"
    "    kind: codex\n"
    "    config_dir: <CODEX_HOME>\n"
    "The built-in default Claude account is used meanwhile.")
# Busy-line and running-tool refresh cadence; this is the existing PM timer.
PM_BUSY_SECONDS = 0.5
PM_BUSY_GLYPHS = ('✽', '✻', '✶', '✢')
# P0-3: how long the background watcher waits per pm_runtime.background_turn call; it
# re-checks that the runtime is still alive between waits.
PM_WATCH_SECONDS = 0.5
PM_BACKGROUND_LABEL = "PM（背景通知）"
QUEUE_PREVIEW_COLUMNS = 60   # Columns of each queued message shown by /pm queue and a pause.
# Layout (W56a): role labels, the indent of a PM reply body, and the owner header prefix
# the PM paths write (shown as the 「你」 label).
OWNER_LABEL = "你"
PM_LABEL = "PM"
OWNER_PREFIX = "owner → PM"
BODY_INDENT = "  "
TOOL_DETAIL_INDENT = "      "
PM_STATE_LABELS = {"thinking": "思考中", "working": "回答中", "interrupting": "中斷中…",
                   "idle": "待命", "connecting": "連線中…", "closing": "斷線中…"}
TURN_STATUS_LABELS = {"INTERRUPTED": "已中斷", "ERROR": "錯誤", "REPLAY": "未重送",
                      "ACTIVE": "未結束", "NOT_SENT": "未送出", "ABSORBED": "併入背景回合"}


def _enable_mouse_drag_reporting(output):
    """Also set xterm button-event tracking (DECSET 1002) with mouse reporting.

    prompt_toolkit sets 1000 (clicks) and 1003 (any motion). A drag needs motion events
    while a button is held; 1002 reports exactly those, on terminals with or without 1003
    (Terminal.app, Warp, iTerm2 all have it), and after 1003 it also stops the stream of
    hover events nothing uses. Reset with the rest when mouse reporting goes off.
    """
    enable, disable = output.enable_mouse_support, output.disable_mouse_support

    def enable_with_drag():
        enable()
        output.write_raw("\x1b[?1002h")

    def disable_with_drag():
        output.write_raw("\x1b[?1002l")
        disable()

    output.enable_mouse_support = enable_with_drag
    output.disable_mouse_support = disable_with_drag


def slash_matches(text, dev=True):
    """Commands matching a one-line slash draft: prefix matches first, then substring.
    Without the dev flag only the read-only /task commands are offered (#1819)."""
    if "\n" in text or not text.startswith("/"):
        return []
    query = text.lower()
    commands = [c for c in SLASH_COMMANDS if dev or not c[0].startswith("/task")
                or c[0] in READ_ONLY_TASK_COMMANDS]
    # Keep the command table's order: new commands are appended there so they do
    # not move existing picker entries when they happen to share a prefix.
    ordered = commands
    prefix = [c for c in ordered if c[0].startswith(query)]
    inside = [c for c in ordered if c not in prefix and len(query) > 2 and query[1:] in c[0]]
    return prefix + inside


def _common_prefix(values):
    """Return the longest case-sensitive common prefix of command labels."""
    if not values:
        return ""
    prefix = values[0]
    for value in values[1:]:
        length = 0
        for left, right in zip(prefix, value):
            if left != right:
                break
            length += 1
        prefix = prefix[:length]
        if not prefix:
            break
    return prefix


def _pm_created_at(binding):
    """Parse a saved binding timestamp for newest-first display; invalid dates sort last."""
    value = binding.get("created_at")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (TypeError, ValueError):
        return None


def _pm_newest_first(bindings):
    return sorted(bindings, key=lambda binding: _pm_created_at(binding) or
                  datetime.min.replace(tzinfo=timezone.utc), reverse=True)


def _pm_relative_age(binding, now=None):
    created = _pm_created_at(binding)
    if created is None:
        return "age unknown"
    now = datetime.now(timezone.utc) if now is None else now
    seconds = max(0, int((now - created).total_seconds()))
    if seconds < 60:
        amount, unit = seconds, "s"
    elif seconds < 3600:
        amount, unit = seconds // 60, "m"
    elif seconds < 86400:
        amount, unit = seconds // 3600, "h"
    else:
        amount, unit = seconds // 86400, "d"
    return "%d%s ago" % (amount, unit)


def history_matches(entries, query):
    """Earlier inputs (oldest first) containing query, case-insensitive; newest first, once each."""
    needle = query.lower()
    seen = set()
    result = []
    for entry in reversed(entries):
        if entry in seen or not entry.strip():
            continue
        seen.add(entry)
        if needle in entry.lower():
            result.append(entry)
    return result


def fit_width(text, width, get_cwidth):
    """Cut text to at most width terminal cells, ending with an ellipsis when cut."""
    if width <= 0:
        return ""
    if get_cwidth(text) <= width:
        return text
    result = []
    used = 0
    for char in text:
        char_width = get_cwidth(char)
        if used + char_width > width - 1:
            break
        result.append(char)
        used += char_width
    return "".join(result) + "…"


def _source_revision(source_file=None):
    """Read a checkout revision from bounded Git metadata files, without running Git."""
    # Keep the lexical path: resolving it changes /var to /private/var on macOS and
    # can make a temporary checkout fixture appear outside the source tree being read.
    source_path = Path(os.path.abspath(os.fspath(
        __file__ if source_file is None else source_file)))
    source_dir = source_path.parent.parent

    def read_small(path, limit=4096):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            return None
        with path.open("rb") as source:
            data = source.read(limit + 1)
        return data if len(data) <= limit else None

    def ref_name(value):
        prefix = b"ref: "
        if not value.startswith(prefix):
            return None
        name = value[len(prefix):].strip().decode("ascii")
        parts = name.split("/")
        if (not name.startswith("refs/") or any(part in ("", ".", "..") for part in parts)
                or "\\" in name):
            return None
        return name

    def revision(value):
        value = value.strip()
        if re.fullmatch(rb"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", value):
            return value[:8].decode("ascii").lower()
        return None

    def packed_ref(path, name):
        data = read_small(path, 4 * 1024 * 1024)
        if data is None:
            return None
        for line in data.splitlines():
            if line.endswith(b" " + name.encode("ascii")):
                found = revision(line.split(b" ", 1)[0])
                if found:
                    return found
        return None

    try:
        git_dir = None
        for candidate in (source_dir, *list(source_dir.parents)[:8]):
            marker = candidate / ".git"
            try:
                marker_info = marker.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISDIR(marker_info.st_mode):
                git_dir = marker
                source_dir = candidate
                break
            if not stat.S_ISREG(marker_info.st_mode):
                return None
            marker_data = read_small(marker)
            if marker_data is None:
                return None
            if not marker_data.startswith(b"gitdir: "):
                return None
            git_path = Path(marker_data[len(b"gitdir: "):].decode("utf-8").strip())
            git_dir = git_path if git_path.is_absolute() else candidate / git_path
            source_dir = candidate
            break
        if git_dir is None:
            return None
        head = read_small(git_dir / "HEAD")
        if head is None:
            return None
        direct = revision(head)
        if direct:
            return direct
        name = ref_name(head)
        if name is None:
            return None
        try:
            common_data = read_small(git_dir / "commondir")
        except FileNotFoundError:
            common_data = None
        common_dir = git_dir
        if common_data:
            common_path = Path(common_data.decode("utf-8").strip())
            common_dir = common_path if common_path.is_absolute() else git_dir / common_path
        ref_path = Path(*name.split("/"))
        for root in (git_dir, common_dir):
            try:
                found = revision(read_small(root / ref_path) or b"")
            except OSError:
                found = None
            if found:
                return found
        for root in (common_dir, git_dir):
            try:
                found = packed_ref(root / "packed-refs", name)
            except OSError:
                found = None
            if found:
                return found
        return None
    except (OSError, UnicodeError, ValueError):
        return None


FAILED = "llmtool: chat terminal UI failed; details omitted"
_TOOLKIT_MODULES = (
    "prompt_toolkit.application", "prompt_toolkit.document", "prompt_toolkit.filters",
    "prompt_toolkit.history", "prompt_toolkit.key_binding", "prompt_toolkit.layout",
    "prompt_toolkit.layout.controls", "prompt_toolkit.layout.dimension",
    "prompt_toolkit.styles", "prompt_toolkit.utils", "prompt_toolkit.widgets",
)


def _load_toolkit():
    """Return the prompt-toolkit 3.x module or None; never raises.

    Every submodule ChatTUI uses is imported here, so a partial or broken install
    selects the line-chat fallback before any UI exists.
    """
    try:
        import importlib
        import prompt_toolkit
        version = getattr(prompt_toolkit, "__version__", "")
        if not isinstance(version, str) or version.split(".")[0] != "3":
            return None
        for name in _TOOLKIT_MODULES:
            importlib.import_module(name)
        return prompt_toolkit
    except Exception:
        return None


def _is_terminal(stream):
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def tui_unavailable_reason(stdin, stdout):
    if not (_is_terminal(stdin) and _is_terminal(stdout)):
        return "stdin/stdout is not a terminal"
    if _load_toolkit() is None:
        return "optional dependency prompt-toolkit 3.x is not installed (install llmtool[tui])"
    return None


def run_chat_tui(session, stdin=None, stdout=None, stderr=None, dev=False):
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    reason = tui_unavailable_reason(stdin, stdout)
    if reason is not None:
        # Decided before any UI exists, so the line chat starts from a clean terminal.
        print("llmtool: --tui unavailable: " + reason + "; using line chat", file=stderr)
        return run_chat(session, stdout=stdout, stderr=stderr)
    try:
        ui = None
        try:
            ui = ChatTUI(session, dev=dev or None)
            status = ui.run()
        except Exception:
            status = 1
        unsent = ui.unsent_queue_text() if ui is not None else None
        if unsent:
            print(terminal_safe_text(unsent), file=stderr)   # never dropped silently (W52 F2)
        if status != 0:
            print(FAILED, file=stderr)
        return status
    finally:
        session.active = False


def _short_count(value):
    """2400 -> '2.4k'; small counts stay exact."""
    if value >= 1000000:
        return "%.1fM" % (value / 1000000.0)
    if value >= 1000:
        return "%.1fk" % (value / 1000.0)
    return str(value)


def usage_line(event, elapsed):
    """Short turn-end line: '已中斷 · 39.8 秒 · 2.4k tokens' (tokens: input + output)."""
    status = chat_tui_pm.safe_code(event.get("status"))
    parts = [] if status == "COMPLETED" else [TURN_STATUS_LABELS.get(status, status)]
    parts.append("%.1f 秒" % elapsed)
    usage = event.get("usage") if type(event.get("usage")) is dict else {}
    counts = [usage.get(key) for key in ("input_tokens", "output_tokens")]
    counts = [value for value in counts if type(value) is int and type(value) is not bool]
    if counts:
        parts.append(_short_count(sum(counts)) + " tokens")
    return " · ".join(parts)


def history_usage_line(turn):
    """The short turn-end line for a stored turn: status, seconds and tokens it recorded."""
    status = turn["status"]
    parts = [] if status == "COMPLETED" else [TURN_STATUS_LABELS.get(status, status)]
    if turn["elapsed"] is not None:
        parts.append("%.1f 秒" % turn["elapsed"])
    counts = [turn["usage"].get(key) for key in ("input_tokens", "output_tokens")]
    counts = [value for value in counts if type(value) is int and type(value) is not bool]
    if counts:
        parts.append(_short_count(sum(counts)) + " tokens")
    return " · ".join(parts)


def tool_outcome(event):
    """ok / error / denied / unknown for a TOOL_RESULT, from its typed reason."""
    reason = event.get("reason")
    if reason == "PERMISSION_DENIED":
        return "denied"
    if reason == "TOOL_ERROR" or event.get("is_error"):
        return "error"
    return "ok" if reason == "OK" else "unknown"


TOOL_FAILURE_TEXT = {"error": "失敗", "denied": "自動拒絕"}


def _seconds(started, ended):
    """Whole seconds between two monotonic times, or None when the start is unknown."""
    return None if started is None or ended is None else int(max(0.0, ended - started))


def tool_item(call, now):
    """One tool call as the Owner reads it (W57): ▸ 執行中：… 23s / ✓ …（4s）/ ✗ …（38s，原因）.

    ``call`` is [label, outcome, started, ended] (monotonic times; started None when the
    TOOL_USE was never seen, and then no duration is shown)."""
    label, outcome, started, ended = call
    if outcome is None:
        seconds = _seconds(started, now)
        return "▸ 執行中：" + label + "…" + ("" if seconds is None else " %ds" % seconds)
    seconds = _seconds(started, ended)
    notes = [] if seconds is None else ["%ds" % seconds]
    if outcome == "ok":
        mark = "✓ "
    elif outcome in TOOL_FAILURE_TEXT:
        mark = "✗ "
        notes.append(TOOL_FAILURE_TEXT[outcome])
    else:
        mark = "? "
        notes.append("結果不明")
    return mark + label + ("（" + "，".join(notes) + "）" if notes else "")


def tool_summary_lines(calls, expanded, now=None):
    """[(text, token)] segments for a turn's tools: collapsed, the running call or the last
    one on one line; expanded (F7), one line per call. ``now`` is the monotonic clock."""
    now = time.monotonic() if now is None else now
    failed = sum(1 for call in calls if call[1] in TOOL_FAILURE_TEXT)
    token = "tool.error" if failed else "tool.summary"
    note = "（%d 次失敗）" % failed if failed else ""
    if expanded:
        segments = [(BODY_INDENT + "▾ 使用工具 %d 次" % len(calls) + note, token)]
        for call in calls:
            segments.append(("\n" + TOOL_DETAIL_INDENT + tool_item(call, now),
                             "tool.error" if call[1] in TOOL_FAILURE_TEXT else "tool.summary"))
        return segments
    running = [call for call in calls if call[1] is None]
    if running:
        line = tool_item(running[-1], now)
        if len(calls) > 1:
            line += "（第 %d 個工具%s）" % (len(calls), "；%d 次失敗" % failed if failed else "")
        return [(BODY_INDENT + line, token)]
    # The last failure, when there is one, is what the Owner needs to see; else the last call.
    shown = ([call for call in calls if call[1] in TOOL_FAILURE_TEXT] or calls)[-1]
    head = "▸ " if len(calls) == 1 else "▸ 使用工具 %d 次%s：" % (len(calls), note)
    return [(BODY_INDENT + head + tool_item(shown, now), token)]


def _tokens(usage):
    """Compact provider-reported usage for the status bar; UNKNOWN when absent."""
    if usage is None:
        return "UNKNOWN"
    return "%d+%d=%d" % (usage["prompt_tokens"], usage["completion_tokens"], usage["total_tokens"])


def _row_identity(item):
    """W59: what identifies a ↓ panel row across refreshes (role and Task/turn)."""
    return (item.get("role"), item.get("task"))


class _TaskViewLinker:
    """W59: OSC 8 links on the detail's full PR URLs only (Core binds PRs to the repo;
    a bare #N would follow the cwd's git remote and could point at another repo)."""

    def __init__(self, base):
        self.base = base
        self._own = chat_tui_theme.Linker(os.getcwd(), None) if base is None else None

    def links(self, text, start, end):
        url = chat_tui_status.pr_url()
        if url is None:
            return []
        pr_link = re.compile(re.escape(url) + r"\d{1,9}(?![\w/])")
        found = []
        line_start = text.rfind("\n", 0, start) + 1
        line_end = text.find("\n", end)
        line_end = len(text) if line_end < 0 else line_end
        for match in pr_link.finditer(text, line_start, line_end):
            if match.end() > start and match.start() < end:
                found.append((match.start(), match.end(), match.group(0)))
        result, last = [], -1
        for low, high, url in sorted(found):
            if low >= last:
                result.append((low, high, url))
                last = high
        return result

    def open_sequence(self, url):
        return (self.base or self._own).open_sequence(url)


class ChatTUI:
    """Conversation pane, persistent multiline input, help and status bar."""

    def __init__(self, session, input=None, output=None, pm_api=None, dev=None):
        from prompt_toolkit.application import Application
        from prompt_toolkit.document import Document
        from prompt_toolkit.filters import Condition, has_focus
        from prompt_toolkit.history import InMemoryHistory
        from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.key_binding.bindings.mouse import load_mouse_bindings
        from prompt_toolkit.keys import Keys
        from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.layout.dimension import Dimension
        from prompt_toolkit.layout.processors import Processor, Transformation
        from prompt_toolkit.styles import DynamicStyle, Style
        from prompt_toolkit.utils import get_cwidth
        from prompt_toolkit.widgets import TextArea

        # prompt_toolkit 3.0.53 does not accept a synthetic "s-enter" binding.
        # Decode the common terminal encodings into the existing Esc+Enter binding.
        ANSI_SEQUENCES["\x1b[13;2u"] = (Keys.Escape, Keys.ControlM)
        ANSI_SEQUENCES["\x1b[27;2;13~"] = (Keys.Escape, Keys.ControlM)

        self._Document = Document
        self._Style = Style
        self._app_styles = {}
        self._get_cwidth = get_cwidth
        self.session = session
        self.source_revision = _source_revision()
        self.dev = dev_enabled() if dev is None else dev   # W57: Task mode only with the flag
        self.busy = False
        self.quit_pending = False
        self.show_help = False
        # Mouse reporting: on, the wheel scrolls the conversation; off (F6), the terminal's
        # own drag-select and copy work. The Application reads this filter every render.
        self.mouse_enabled = True
        # Mouse drag in the conversation: last pointer (x, y) and the auto-scroll timer handle
        # used while the pointer is held at or beyond the pane edge.
        self._drag_pointer = None
        self._drag_origin = None     # cell of the press; a drag counts only after it moved
        self._drag_moved = False
        self._autoscroll_handle = None
        self.worker = None
        self.worker_kind = None
        # Chat request interrupt (Esc or the first Ctrl-C): the worker's cancel event,
        # whether it was requested, and the reply block still streaming into the pane.
        self.send_cancel = None
        self.send_interrupted = False
        self.stream_raw = None       # text of the streaming chat reply (None: no block open)
        self.stream_head = 0         # where that block's "assistant:" header starts in the pane
        self.stream_start = 0        # where that block's text starts in the pane
        self.stream_seen = False     # chunks of the current reply were already shown
        self.menu_items = []
        self.menu_index = 0
        self.menu_offset = 0
        self.searching = False
        self.search_query = ""
        self.search_matches = []
        # Task mode: PM provider profile chosen with /task mode, else None (chat mode).
        self.task_profile = None
        # (profile, request) of the last Task-mode submit: the same draft is not resent.
        self.last_task_request = None
        self.last_task_id = None
        self.show_detail = False
        self.detail_task_id = None
        # Task list: rows read from TaskStore on a reader thread (never busy/single-flight).
        self.show_list = False
        self.list_records = []
        self.list_records_all = []
        self.list_groups = []
        self.list_hidden_count = 0
        self.list_show_all = False
        self.list_index = 0
        self.list_unreadable = 0
        self.list_error = None
        self.list_loaded = None  # (monotonic, local clock text) of the last finished read
        self.list_reader = None
        self.unseen_output = False
        self.show_events = False
        # Real-time PM: every call goes through pm_runtime S2 (chat_tui_pm.PMRuntimeAPI).
        self.pm_api = pm_api or chat_tui_pm.PMRuntimeAPI()
        self.pm_runtime = None
        self.pm_binding = None       # pm_runtime.current_binding() of the connected session
        self.pm_block_index = None   # block_index of the streaming text block
        self.pm_target = None        # (session_id, cwd) of the connected PM
        self.pm_mode = False
        self.pm_state = None         # connecting / idle / working / interrupting / error(CODE)
        self.pm_pending = False      # a connect/disconnect/history worker is running
        self.pm_turn_active = False
        self.pm_turn_started = None
        self.pm_interrupted = False
        self.pm_block_raw = None     # raw text of the streaming PM block (None: no block open)
        self.pm_block_start = 0      # where that block's displayed text starts in the pane
        self.pm_queue = ""           # received PM text not yet released to the pane
        self.pm_tail = []            # notices shown below the open PM block (W55 F2-b)
        self._owner_output = False   # output of an Owner key/command: never splits a PM block
        self._pm_tick_handle = None  # loop.call_later handle of the next typewriter tick
        self._pm_rate = PM_CHARS_PER_TICK   # characters released per tick (raised when behind)
        self.pm_warm = False         # connected, nothing received yet: the PM process is cold
        # Exit/connect hand-off: a runtime opened by the connect worker but not yet adopted
        # is closed by whichever side sees the other first (close_pm or the worker).
        self._pm_lock = threading.Lock()
        self._pm_exiting = False
        self._pm_unadopted = None
        self.pm_deltas = False       # text deltas seen since the last assistant/tool boundary
        self.pm_tools = {}
        # W56a layout: the 「PM」 label is shown once per turn; the turn's tools are one
        # summary line kept in place. pm_tool_block = [start, end, calls, turn seq] of the
        # latest summary in the pane (F7 re-renders it expanded or collapsed).
        self.pm_labelled = False
        self.pm_tool_calls = []      # [label, outcome, started, ended] in call order (W57)
        self.pm_tool_ids = {}        # tool_use_id -> index in pm_tool_calls
        self.pm_tool_block = None
        self.pm_tools_expanded = False
        self.pm_last_status = None   # TURN_END status of the last finished turn
        self.colour = chat_tui_view.colour_enabled()
        # W56d: theme and OSC 8 links, from the saved settings (chat_tui_theme).
        self.settings = chat_tui_theme.load_settings()
        self.theme = chat_tui_theme.resolve_theme(self.settings["theme"])
        self.links_supported = chat_tui_theme.osc8_supported()
        self._linker = None          # set by the repo lookup (the linker property)
        self._linker_lookup = None   # its thread: started on first use, git never on the UI
        # W56d status line: PM-dispatched work in flight (TaskStore, one read per
        # REFRESH_SECONDS while a PM is connected) and the ↓ panel listing it.
        self.background = []         # chat_tui_status.background_items of the bound PM
        self.background_key = None   # PM session the items belong to
        self.background_error = None
        self.background_loaded = None    # monotonic time of the last finished read
        self.background_reader = None
        self.attention = None
        self.attention_key = None
        self.attention_read_at = None
        self.show_background = False
        # W59: the selected panel row is an identity, (role, task), changed only by the
        # Owner's navigation (opening the panel, Up/Down) and never by a draw or a refresh;
        # background_index is where it last was, used only when Up/Down leave a vanished row.
        self.background_selected = None
        self.background_index = 0
        # W59: read-only Task detail opened from the panel; read once on a reader thread
        # (never busy), dropped when a newer open or Esc replaced it (task_view_seq).
        self.show_task_view = False
        self.task_view_seq = 0
        self._task_view_linker = (None, None)   # (base Linker, wrapper) last handed out
        # W60: the Task ID the open detail shows (c copies it; None when the detail has no
        # Task record), fixed when the detail opens, and when c was last pressed.
        self.task_view_task_id = None
        self.task_copy_at = None
        # W60: what the detail's last copy was ("Task ID", "全文", None for a selection).
        self.task_copy_label = None
        self.copy_labels = {}    # W60: id(view) -> "全文" after C in a developer pane
        self._list_header_rows = 2       # W60: header rows above the first listed Task
        self._list_shown_index = None    # list_index the list view last scrolled to
        # W52 F2: drafts sent during a turn wait here (FIFO) and go one turn at a time after
        # a COMPLETED turn. Esc (W55), a failed/refused turn, Ctrl-C or a disconnect pauses
        # it (the reason text) until /pm queue send, or Enter on an empty draft sends one.
        self.pm_outbox = collections.deque()
        self.pm_outbox_paused = None
        self._auto_resume_pending = False
        self._auto_resume_sent = False
        self._auto_resume_unconfirmed = False
        self._auto_resume_connection = None
        self.pm_turn_status = None   # TURN_END status of the current turn
        self.pm_turn_error = None    # RUNTIME_ERROR code of the current turn
        self.pm_turn_refused = False # a tool was refused (PERMISSION_DENIED) in this turn
        self.pm_interrupt_by = None  # "esc" or "ctrl-c"
        # Token of the current turn: events and the end from an older turn's worker (one
        # dropped by a disconnect, a fork or exit) are ignored.
        self._pm_turn_seq = 0
        # P0-3: turns Claude Code starts by itself (background task notifications) are shown
        # as turns labelled PM_BACKGROUND_LABEL. One that arrives while an Owner turn is still
        # shown waits here (its events buffered) until that turn ends.
        self.pm_label = PM_LABEL
        self._pm_background_live = {}   # key -> pm_background_turns() item of a running one
        self.pm_header_open = False     # the next PM block goes right under a header line
        self.pm_turn_kind = None         # "owner" or "background" while a turn is active
        self.pm_background_started = None   # start of a background turn inside an Owner turn
        self._pm_background_waiting = collections.deque()
        self._pm_held_attempts = set()   # turn ids whose draft went back to the queue
        self._quit_warned = False    # first Ctrl-C with queued messages only warned
        # W58b-4: the fixed busy line above the input, animated by the existing PM tick.
        self._pm_busy_frame = 0
        self._pm_busy_handle = None
        # W52 F3: a moving drag copies its selection shortly after it changes, so the
        # clipboard is right even when the terminal never reports the release (Warp).
        self._drag_copy_handle = None
        self._loop = None
        self._received_signal = None
        # The conversation pane keeps its own rows, reading position, selection and
        # follow state (chat_tui_view); no scrollbar: the terminal's own is the only one.
        self.view = chat_tui_view.ConversationView(get_cwidth)
        self.conversation = chat_tui_view.make_pane(
            self.view, colour=self.colour, styler=self._style,
            linker=lambda: self.linker if self.links_on() else None)
        # W59: the Task detail is its own view, so the conversation keeps its reading position.
        self.task_view = chat_tui_view.ConversationView(get_cwidth)
        self.task_pane = chat_tui_view.make_pane(
            self.task_view, colour=self.colour, styler=self._style,
            linker=self.task_view_linker)
        history_text = ("History is stored locally." if session.history_saved
                        else "History stays in memory.")
        revision_label = (" (" + self.source_revision + ")" if self.source_revision else "")
        opening = ("LLMTool " + __version__ + revision_label
                   + " interactive chat (full-screen)\n" + session.status()
                   + "\n" + history_text + " F1 or /help for help; Ctrl-C exits.")
        if session.history_notice:
            opening += "\nllmtool: " + session.history_notice
            session.history_notice = None
        self.conversation.set_text(
            terminal_safe_text(opening
                               + "\nChat mode talks to the chat provider directly; it is not a PM."
                               + " /pm connect (or /pm new) talks to the dedicated PM."
                               + (" /task submit is for development testing only: it records"
                                  " a Task and dispatches nothing." if self.dev else "")))
        class PMGhostHint(Processor):
            """Draw a dim PM command hint after the cursor without changing its buffer."""
            def apply_transformation(processor_self, transformation_input):
                document = transformation_input.document
                if (document.cursor_position != len(document.text) or
                        document.cursor_position_row != transformation_input.lineno):
                    return Transformation(transformation_input.fragments)
                size = self.app.output.get_size()
                line = document.current_line_before_cursor
                prompt_width = get_cwidth("pm> " if self.pm_mode else
                                          "task> " if self.task_profile else "> ")
                hint = self.pm_ghost_hint(document.text,
                                          size.columns - prompt_width - get_cwidth(line),
                                          get_cwidth)
                if not hint:
                    return Transformation(transformation_input.fragments)
                fragments = list(transformation_input.fragments)
                fragments.append((self._style("meta.usage"), hint))
                return Transformation(fragments)

        self.input = TextArea(multiline=True, wrap_lines=True, focus_on_click=True,
                              prompt=lambda: ("pm> " if self.pm_mode else
                                              "task> " if self.task_profile else "> "),
                              height=lambda: Dimension(
                                  min=min(4, max(1, self.input.buffer.document.line_count)),
                                  max=min(4, max(1, self.input.buffer.document.line_count))),
                              history=InMemoryHistory(),
                              # Ctrl-R search never edits the draft; Enter or Esc ends it.
                              read_only=Condition(lambda: self.searching),
                              input_processors=[PMGhostHint()])
        self.help = TextArea(text=self.help_text(), read_only=True, scrollbar=True,
                             wrap_lines=True, style="class:help",
                             height=Dimension(min=1, weight=1))
        # W60: the developer panes (F2 detail, F3 list, F4 events) are read-only views like
        # the conversation, so drag-select, double-click, Ctrl-C and C copy work the same.
        self.detail_view = self._read_view()
        self.detail = chat_tui_view.make_pane(self.detail_view, colour=self.colour,
                                              styler=self._style)
        detail_focused = has_focus(self.detail)
        showing_help = Condition(lambda: self.show_help)
        showing_detail = Condition(lambda: self.show_detail and not self.show_help)
        showing_list = Condition(lambda: self.show_list and not self.show_detail
                                 and not self.show_help)
        showing_events = Condition(lambda: self.show_events and not self.show_list
                                   and not self.show_detail and not self.show_help)
        showing_task_view = Condition(lambda: self.task_view_visible())
        task_view_open = Condition(lambda: self.show_task_view)   # also under help/dev panes
        task_view_focused = has_focus(self.task_pane)
        self.events_view = self._read_view()
        self.events = chat_tui_view.make_pane(self.events_view, colour=self.colour,
                                              styler=self._style)
        events_focused = has_focus(self.events)
        self.list_view = self._read_view()
        self.list_window = chat_tui_view.make_pane(self.list_view, colour=self.colour,
                                                   styler=self._style)
        list_focused = has_focus(self.list_window)
        # The developer pane on screen (layout and keys), if any.
        dev_pane_shown = Condition(lambda: self._mouse_view() in (
            self.detail_view, self.list_view, self.events_view))
        picker_shown = Condition(lambda: sum(self.picker_layout()) > 0)
        menu_shown = picker_shown & Condition(lambda: not self.searching)
        searching = Condition(lambda: self.searching)
        # Opened only by typed/pasted insertion, so recalled history never opens it
        # and Up/Down keep recalling input history when the menu is closed.
        self.input.buffer.on_text_insert += lambda _buffer: self._refresh_menu(True)
        self.input.buffer.on_text_changed += lambda _buffer: self._refresh_menu(False)
        # Typing closes the ↓ background panel (it opens only on an empty input).
        self.input.buffer.on_text_changed += lambda _buffer: self._close_background_on_text()
        showing_background = Condition(lambda: self.background_visible())   # layout only
        # W59: keys follow the Owner's open panel even when no row fits on screen.
        panel_open = Condition(lambda: self.background_open())
        self.background_window = Window(
            FormattedTextControl(self.background_text), style="class:menu",
            height=lambda: Dimension.exact(max(1, self.background_rows())), wrap_lines=False)
        self.menu_window = Window(
            FormattedTextControl(self.menu_text), style="class:menu",
            height=lambda: Dimension.exact(max(1, sum(self.picker_layout()))), wrap_lines=False)
        self.busy_window = Window(FormattedTextControl(self.busy_fragments),
                                  height=1, wrap_lines=False)
        self.rule_window = Window(height=1, char=chat_tui_view.RULE_CHAR,
                                  style=lambda: self._style("rule"))
        self.status_window = Window(FormattedTextControl(self.status_fragments),
                                    style=lambda: self._style("status.bar"),
                                    height=Dimension.exact(1), wrap_lines=False)
        root = HSplit([ConditionalContainer(self.conversation,
                                            filter=~showing_help & ~showing_detail & ~showing_list
                                            & ~showing_events & ~showing_task_view),
                       ConditionalContainer(self.task_pane, filter=showing_task_view),
                       ConditionalContainer(self.help, filter=showing_help),
                       ConditionalContainer(self.detail, filter=showing_detail),
                       ConditionalContainer(self.list_window, filter=showing_list),
                       ConditionalContainer(self.events, filter=showing_events),
                       ConditionalContainer(self.busy_window,
                                            filter=Condition(lambda: self.busy_rows() > 0)),
                       self.rule_window,
                       ConditionalContainer(self.background_window, filter=showing_background),
                       ConditionalContainer(self.menu_window, filter=picker_shown),
                       self.input, self.status_window],
                      height=Dimension(min=1, weight=1))

        keys = KeyBindings()
        input_focused = has_focus(self.input)

        @keys.add("enter", filter=input_focused)
        def _submit(event):
            if self.searching:
                if picker_shown():
                    self.accept_search()
                else:
                    self.close_menu()  # Unseen search: keep the draft, send nothing.
            elif not (menu_shown() and self.accept_menu()):
                self.submit()

        @keys.add("up", filter=input_focused & (menu_shown | searching), eager=True)
        def _menu_up(event):
            self.move_menu(-1)

        @keys.add("down", filter=input_focused & (menu_shown | searching), eager=True)
        def _menu_down(event):
            self.move_menu(1)

        # Not eager: an eager lone Esc would fire before Esc Enter (newline) is recognised,
        # closing the picker and letting the Enter submit. Lone Esc fires after the timeout.
        @keys.add("escape", filter=input_focused & (menu_shown | searching))
        def _menu_close(event):
            self.close_menu()

        pm_turn = Condition(lambda: self.pm_turn_active)
        chat_request = Condition(lambda: self.busy and self.worker_kind == "chat")
        conversation_focused = has_focus(self.conversation)
        # Esc interrupts from the input or while reading in the conversation pane (W52 B2);
        # there it touches nothing else, so a kept selection stays.
        reading_or_typing = input_focused | conversation_focused

        # W56d: Down on an empty PM input opens the background panel while it lists work;
        # Esc closes it first (a second Esc then interrupts a running turn as before).
        background_listed = Condition(lambda: self.pm_mode and not self.input.text
                                      and bool(self.background_all()))

        @keys.add("down", filter=input_focused & background_listed & ~picker_shown
                  & ~panel_open & ~showing_task_view, eager=True)
        def _background_open(event):
            self.background_index = 0
            self.background_selected = _row_identity(self.background_all()[0])
            self.show_background = True

        # W59: Up/Down choose a panel row; Enter opens its read-only Task detail.
        @keys.add("up", filter=input_focused & panel_open & ~picker_shown & ~searching,
                  eager=True)
        def _background_up(event):
            self.move_background(-1)

        @keys.add("down", filter=input_focused & panel_open & ~picker_shown & ~searching,
                  eager=True)
        def _background_down(event):
            self.move_background(1)

        @keys.add("escape", filter=panel_open & ~picker_shown)
        def _background_close(event):
            self.show_background = False

        @keys.add("escape", filter=reading_or_typing & pm_turn & ~picker_shown
                  & ~panel_open & ~task_view_open)
        def _pm_interrupt(event):
            self.interrupt_pm()

        @keys.add("escape", filter=reading_or_typing & chat_request & ~picker_shown
                  & ~panel_open & ~task_view_open)
        def _send_interrupt(event):
            self.interrupt_send()

        # W59: Esc closes the Task detail (never interrupts the PM turn behind it).
        @keys.add("escape", filter=showing_task_view & ~picker_shown)
        def _task_view_close(event):
            self.close_task_view()

        @keys.add("escape", filter=showing_detail & ~picker_shown)
        def _detail_close(event):
            self.toggle_detail(False)

        @keys.add("enter", filter=input_focused & panel_open & ~picker_shown & ~searching)
        def _background_enter(event):
            self.open_background_detail()

        # W59: with the detail open Enter never sends (the draft stays); it only completes a
        # picker suggestion. Up/Down scroll the detail instead of recalling input history.
        @keys.add("enter", filter=input_focused & showing_task_view & ~searching)
        def _task_view_enter(event):
            if menu_shown():
                self.accept_menu()

        # W60: c copies the detail's Task ID; with a draft it types into the draft as before.
        # W60: c and C act only while the detail pane has focus (it takes focus when it
        # opens; Tab goes to the input and back), never on the input line, so a draft such
        # as "cat" is always typed as it is.
        @keys.add("c", filter=showing_task_view & task_view_focused & ~picker_shown
                  & ~searching)
        def _task_view_copy(event):
            self.copy_task_id()

        # W60: C (Shift-C) copies the whole detail text, every line, as plain text.
        @keys.add("C", filter=showing_task_view & task_view_focused & ~picker_shown
                  & ~searching)
        def _task_view_copy_all(event):
            self.copy_task_detail()

        # W60: the same C in the developer panes (F2 detail, F3 whole list, F4 events).
        @keys.add("C", filter=dev_pane_shown & ~picker_shown & ~searching
                  & (detail_focused | list_focused | events_focused))
        def _dev_pane_copy_all(event):
            self.copy_pane_all()

        # F2's selected Task detail and F3's list/detail copy the full selected Task ID.
        @keys.add("c", filter=dev_pane_shown & ~picker_shown & ~searching
                  & (detail_focused | list_focused))
        def _dev_pane_copy_task_id(event):
            self.copy_dev_task_id()

        @keys.add("up", filter=(detail_focused | events_focused) & dev_pane_shown)
        def _dev_pane_up(event):
            self._mouse_view().scroll_rows(-1)

        @keys.add("down", filter=(detail_focused | events_focused) & dev_pane_shown)
        def _dev_pane_down(event):
            self._mouse_view().scroll_rows(1)

        # W60: Up/Down on an empty input with nothing to open or recall say why, once.
        nothing_to_open = Condition(lambda: not self.input.text and not self.background_open()
                                    and not (self.pm_mode and self.background_all()))

        @keys.add("down", filter=input_focused & nothing_to_open & ~picker_shown & ~searching
                  & ~showing_task_view & ~dev_pane_shown)
        def _nothing_down(event):
            self.hint_no_panel()

        @keys.add("up", filter=input_focused & nothing_to_open & ~picker_shown & ~searching
                  & ~showing_task_view & ~dev_pane_shown
                  & Condition(lambda: not self.input.buffer.history.get_strings()))
        def _nothing_up(event):
            self.hint_no_panel()

        @keys.add("up", filter=(input_focused | task_view_focused) & showing_task_view
                  & ~menu_shown & ~searching, eager=True)
        def _task_view_up(event):
            self.task_view.scroll_rows(-1)

        @keys.add("down", filter=(input_focused | task_view_focused) & showing_task_view
                  & ~menu_shown & ~searching, eager=True)
        def _task_view_down(event):
            self.task_view.scroll_rows(1)

        @keys.add("c-r", filter=input_focused)
        def _history_search(event):
            if self.searching:
                self.move_menu(1)  # Next older match, as in shell reverse search.
            else:
                self.start_search()

        @keys.add("<any>", filter=input_focused & searching)
        def _search_type(event):
            if event.data.isprintable():
                self.type_search(event.data)

        @keys.add("<bracketed-paste>", filter=input_focused & searching)
        def _search_paste(event):
            self.type_search(event.data.replace("\r\n", "\n").replace("\r", "\n"))

        @keys.add("backspace", filter=input_focused & searching)
        def _search_erase(event):
            self.search_query = self.search_query[:-1]
            self._refresh_search()

        @keys.add("c-j", filter=input_focused)
        @keys.add("escape", "enter", filter=input_focused)
        def _newline(event):
            # Ctrl-J and Esc+Enter (including decoded Shift+Enter variants) insert in
            # the draft, never send; end a history search first.
            self.close_menu()
            self.input.buffer.insert_text("\n")

        @keys.add("tab", eager=True)
        def _tab(event):
            if not input_focused():
                self.toggle_focus()
                return
            if self.searching:
                self.toggle_focus()
                return
            text = self.input.text
            if not text.startswith("/"):
                self.toggle_focus()
                return
            self._complete_slash_tab()

        @keys.add("tab", filter=input_focused & menu_shown, eager=True)
        def _tab_complete(event):
            # A visible picker must not consume Tab before slash completion. Esc
            # remains the explicit way to dismiss it without changing the draft.
            self._complete_slash_tab()

        @keys.add("s-tab")
        def _focus(event):
            self.toggle_focus()

        @keys.add("pageup")
        def _page_up(event):
            self.scroll(-1)

        @keys.add("pagedown")
        def _page_down(event):
            self.scroll(1)

        @keys.add("up", filter=conversation_focused)
        def _row_up(event):
            self.view.scroll_rows(-1)

        @keys.add("down", filter=conversation_focused)
        def _row_down(event):
            self.view.scroll_rows(1)

        @keys.add("home", filter=conversation_focused)
        def _to_top(event):
            self.view.to_top()

        @keys.add("end", filter=conversation_focused)
        def _to_bottom(event):
            self.view.to_bottom()

        @keys.add(Keys.Vt100MouseEvent)
        def _mouse_event(event):
            return self.mouse_event(event)

        @keys.add("f1")
        def _help(event):
            self.toggle_help()

        @keys.add("f2")
        def _detail(event):
            self.toggle_detail(not self.show_detail)

        @keys.add("f3")
        def _list(event):
            self.toggle_list(not self.show_list)

        @keys.add("f4")
        def _events(event):
            self.toggle_events(not self.show_events)

        @keys.add("f6")
        def _mouse(event):
            self.toggle_mouse()

        @keys.add("f7")
        def _tools(event):
            self.toggle_tools()

        @keys.add("escape", filter=events_focused & showing_events)
        def _events_close(event):
            self.toggle_events(False)

        @keys.add("f5", filter=showing_list | showing_events)
        @keys.add("r", filter=list_focused | events_focused)
        def _list_refresh(event):
            self.refresh_list()

        @keys.add("up", filter=list_focused)
        def _list_up(event):
            self.move_list(-1)

        @keys.add("down", filter=list_focused)
        def _list_down(event):
            self.move_list(1)

        @keys.add("a", filter=list_focused)
        def _list_show_all(event):
            self.list_show_all = not self.list_show_all
            self._project_list_records()
            self._list_shown_index = None
            self.app.invalidate()

        @keys.add("enter", filter=list_focused)
        def _list_open(event):
            self.open_selected()

        @keys.add("escape", filter=list_focused & ~showing_detail)
        def _list_close(event):
            self.toggle_list(False)

        @keys.add("c-c")
        def _interrupt(event):
            # With a selection, Ctrl-C copies it again and clears it instead of interrupting
            # or exiting (a second Ctrl-C then acts as usual). Cmd+C never arrives here:
            # Warp and Terminal.app keep it for their own copy (W52 F3, Warp log).
            view = self._mouse_view()
            if view is not None and view.selected_range():
                self._forget_copy_label(view)
                view.copy_selection()
                view.clear_selection()
                view.copy_note = "選取已清除，再按 Ctrl-C 中斷/離開"
                return
            self.request_quit()

        @keys.add("c-d", filter=Condition(lambda: not self.input.text))
        def _eof(event):
            self.request_quit()

        self.app = Application(
            layout=Layout(root, focused_element=self.input), key_bindings=keys, full_screen=True,
            mouse_support=Condition(lambda: self.mouse_enabled), refresh_interval=0.5,
            input=input, output=output,
            style=DynamicStyle(self._app_style))
        # prompt_toolkit waits timeoutlen (1s) after a lone Esc in case Esc Enter follows;
        # that made dismissing the picker feel stuck. Shortened only while the picker is
        # visible on screen, so the Esc Enter window is unchanged otherwise.
        self._default_timeoutlen = self.app.timeoutlen
        # A resize can hide the picker without any key press; re-check before each draw.
        self.app.before_render += lambda _app: self._sync_picker()
        # A shown list re-reads TaskStore when stale; redraws never read more often.
        self.app.before_render += lambda _app: self._list_tick()
        self.app.before_render += lambda _app: self._sync_list_view()   # W60
        # The connected PM's background work: at most one TaskStore read per interval.
        self.app.before_render += lambda _app: self._background_tick()
        self._default_mouse_binding = load_mouse_bindings().bindings[0]
        _enable_mouse_drag_reporting(self.app.output)
        if self.links_supported:
            chat_tui_theme.install_osc8_guard(self.app.output)
        self.view.on_copied = self.app.invalidate   # thread-safe: shows 已複製 when pbcopy is done
        self.task_view.on_copied = self.app.invalidate   # W60: the Task ID copy result

    # -- rendering -----------------------------------------------------------------
    def status_fragments(self):
        session = self.session
        model = selected_provider(session.config).get("model")
        reported = session.last_reported_model
        state = session.state
        if self.busy:
            started = session.request_started
            verb = "interrupting" if self.send_interrupted else "working"
            if started is None:
                state = verb
            else:
                state = "%s %.1fs" % (verb, max(0.0, time.monotonic() - started))
        elif state == "error":
            state = "error (" + (session.last_error_code or "unclassified") + ")"
        if self.quit_pending:
            state = "exit pending"
        elif session.last_elapsed is not None and not self.busy and state == "idle":
            state = "ready %.1fs" % session.last_elapsed

        def compact(value, width):
            value = terminal_safe_text(str(value)).replace("\n", " ").replace("\t", " ")
            return fit_width(value, width, self._get_cwidth)

        # One status row at any width. Prefer the actual reported model when available;
        # long names never expand the footer. When the terminal is too narrow, fields
        # drop in STATUS_DROP_ORDER; the state stays last and is cut only if alone too wide.
        fields = {
            "mode": "TASK→PM:" + compact(self.task_profile or "", 12),
            "provider": "p:" + compact(session.provider, 10),
            "model": "m:" + compact(reported or model or "default", 14),
            "state": compact(state, 14),
            "usage": "t:" + compact(_tokens(session.last_usage), 14),
            "keys": self.status_keys(),
            "scroll": self._scroll_status(),
            "mouse": "F6 滑鼠:" + ("開" if self.mouse_enabled else "關"),
            "help": "F1",
        }
        width = max(1, self.app.output.get_size().columns)
        # The mode field appears only in Task mode, so the chat footer is unchanged.
        shown = [key for key in STATUS_FIELDS
                 if key in fields and (key != "mode" or self.task_profile)]
        if self.pm_mode:
            # Chat provider/model/usage are not the PM's; in PM mode only PM fields show.
            fields.update(self.pm_status_fields(compact))
            shown = ["mode", "attention", "background_item", "identity", "session", "state", "keys",
                     "scroll", "mouse", "help"]
        shown = [key for key in shown if fields.get(key)]
        if self.busy or self.pm_turn_active or self.task_view_visible():
            # Keep identity ahead of optional context while busy or in the Task detail;
            # Task detail key hints remain last so the copy/return actions stay visible.
            pm_drop_order = ("attention", "background_item", "help", "identity", "scroll", "session", "mouse", "keys")
        else:
            # At idle, preserve the PM runtime/account identity at narrow widths.
            pm_drop_order = PM_STATUS_DROP_ORDER
        for name in pm_drop_order if self.pm_mode else STATUS_DROP_ORDER:
            if self._get_cwidth(STATUS_SEPARATOR.join(fields[key] for key in shown)) <= width:
                break
            if name == "keys" and "keys" in shown and self.task_view_visible():
                # W60: in the detail the optional key hints go first (from the end), so
                # "Esc 返回" stays as long as anything fits.
                items = fields["keys"].split(" · ")
                while len(items) > 1 and self._get_cwidth(STATUS_SEPARATOR.join(
                        fields[key] for key in shown)) > width:
                    items.pop()
                    fields["keys"] = " · ".join(items)
                if self._get_cwidth(STATUS_SEPARATOR.join(
                        fields[key] for key in shown)) <= width:
                    break
            if name in shown:
                shown.remove(name)
        if self._get_cwidth(STATUS_SEPARATOR.join(fields[key] for key in shown)) > width:
            return [("", fit_width(STATUS_SEPARATOR.join(fields[key] for key in shown), width,
                                   self._get_cwidth))]
        fragments = []
        for key in shown:
            if fragments:
                fragments.append(("", STATUS_SEPARATOR))
            fragments.append((self._style("status.key") if key in STATUS_KEY_FIELDS else "",
                              fields[key]))
        return fragments

    def status_text(self):
        """The status bar as plain text (what status_fragments draws)."""
        return "".join(text for _style, text in self.status_fragments())

    def status_keys(self):
        """Keys that do something right now (status bar)."""
        keys = []
        if self.task_view_visible():
            keys.append("Esc 返回")   # W59: Esc closes the detail; it never interrupts here
            copied = self._task_copy_status()
            if copied:
                keys.append(copied)
            elif self.app.layout.has_focus(self.task_pane):
                if self.task_view_task_id:
                    keys.append("c 複製 Task ID")
                keys.append(chat_tui_status.COPY_ALL_STATUS)
                keys.append("拖曳即複製")
            else:
                keys.append("Tab 回詳情（c/C 複製）")
        elif self.pm_turn_active:
            keys.append("Esc 中斷")
        elif self.busy and self.worker_kind == "chat" and not self.send_interrupted:
            keys.append("Esc 中斷")
        elif self.pm_mode and self.pm_outbox and self.pm_outbox_paused:
            keys.append("Enter 送下一則")   # W55: an empty Enter sends the next queued draft
        if self.pm_mode and self._tool_block_valid():
            keys.append("F7 工具")
        return " · ".join(keys)

    def _scroll_status(self):
        """Status field of the pane on screen: a developer pane reports its own copy and
        position (W60); otherwise the conversation's, as before."""
        view = self._mouse_view()
        if view in (self.detail_view, self.list_view, self.events_view):
            # The copy result, else what copies here (kept where the bar has room for it).
            return (self._pane_copy_status(view, self.copy_labels.get(id(view)))
                    or "拖曳即複製 · " + chat_tui_status.COPY_ALL_STATUS)
        return self._copy_hint(self.view.status())

    @staticmethod
    def _copy_hint(status):
        """W60: say what the copy is for. With mouse reporting on, the terminal's Cmd+C has
        no selection of its own, so the hints name Cmd+V (paste) and Ctrl-C, never Cmd+C."""
        if status.startswith("已複製") and "；" not in status:
            return status + " · Cmd+V 貼上"
        if status.startswith("已選"):
            return status + " · Ctrl-C 複製"
        return status

    def _style(self, token):
        return chat_tui_view.token_style(token, self.colour, self.theme)

    def _app_style(self):
        """The Application's class styles from the current theme table (cached per theme)."""
        key = (self.theme, self.colour)
        if key not in self._app_styles:
            self._app_styles[key] = self._Style.from_dict({
                "status": self._style("status.bar"), "help": self._style("ui.help"),
                "rule": self._style("rule"), "conversation.selected": self._style("ui.selected"),
                "menu": self._style("ui.menu"), "menu.current": self._style("ui.menu.current"),
                "menu.header": self._style("ui.menu.header")})
        return self._app_styles[key]

    @property
    def linker(self):
        """The Linker, or None (no links drawn) until the repo lookup has finished.

        The first use starts git in a thread; when it ends the pane is redrawn with links.
        """
        if self._linker is None and self.links_supported and self._linker_lookup is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            self._linker_lookup = threading.Thread(
                target=self._lookup_repo, args=(loop,), name="llmtool-chat-repo-info",
                daemon=True)
            self._linker_lookup.start()
        return self._linker

    def _lookup_repo(self, loop):
        try:
            top, slug = chat_tui_theme.repo_info()
        except Exception:
            top, slug = None, None
        linker = chat_tui_theme.Linker(top or os.getcwd(), slug)
        if loop is None or not self._post(loop, self.finish_repo_lookup, linker):
            self._linker = linker

    def finish_repo_lookup(self, linker):
        self._linker = linker
        self.app.invalidate()

    def links_on(self):
        """OSC 8 links are drawn: a supporting terminal and not turned off with /links."""
        return self.links_supported and self.settings["links"] == "on"

    def _save_settings(self):
        try:
            chat_tui_theme.save_settings(self.settings)
        except OSError as exc:
            return " (not saved: " + (exc.strerror or "write failed") + ")"
        return ""

    def help_text(self):
        return key_help(self.dev) + "\n\n" + HELP

    def command_help_text(self):
        """Short, PM-first help for /help; F1 retains the full interaction guide."""
        lines = [
            "Talk to the PM",
            "  /pm list               List saved PM sessions",
            "  /pm use N|SESSION      Select a session without connecting",
            "  /pm accounts [--check] Show account names and folder status",
            "  /pm connect             Connect to your PM session",
            "  /pm new [--runtime claude|codex] [--account NAME] [CWD]",
            "  /pm                    Show the PM connection",
            "  /pm history             Show recent PM turns",
            "  /pm queue               List, clear, or resume queued messages",
            "  /pm disconnect         Leave the PM session",
            "PM setup",
            "  設定 PM（帳號、Codex、工作目錄、預設）：docs/guides/llmtool-pm-setup-zh-tw.md",
            "Chat",
            "  /status                 Show the current chat or PM status",
            "  /logs                   Show recent PM turns",
            "  /attention              Show what needs attention",
            "  /provider [PROFILE]     List or switch chat profiles",
            "  /skill [NAME]           List, choose, or clear a skill",
            "  /history                Show recent chat turns",
            "  /clear                  Clear the chat",
            "  /theme [dark|light|auto] Set the display theme",
            "  /settings auto-resume on|off  Set automatic PM resume",
            "  /links [on|off]         Set clickable links",
            "  /exit, /quit            Leave chat",
            "Type / to choose a command. Enter runs a command you typed. F1 shows key help.",
        ]
        if self.dev:
            lines.extend(("Developer tools", "  /task ...               Developer and test commands"))
        else:
            lines.extend(("Task views (read-only)",
                          "  /task list, /task show TASK_ID, /task events  (F3, F2, F4)"))
        return "\n".join(lines)

    def status_report(self):
        """/status (W57): what the current mode is talking to, in the status bar's words."""
        theme = "主題（本機設定）：%s（目前 %s%s）" % (self.settings["theme"], self.theme,
                                     "" if self.colour else "，無色彩")
        if self.pm_mode and self.pm_target:
            count = self.background_count()
            if self.pm_outbox:
                queue = "%d 則" % len(self.pm_outbox) + (
                    "（暫停：" + self.pm_outbox_paused + "）" if self.pm_outbox_paused else "")
            else:
                queue = "沒有"
            runtime = self._pm_runtime_name()
            lines = ["狀態（PM 模式）",
                     "PM session：" + self.pm_target[0] + "（" + chat_tui_pm.safe_text(runtime)
                     + "，cwd " + chat_tui_pm.safe_text(pm_runtime.display_path(
                         str(self.pm_target[1])) or "UNKNOWN") + "）"]
            if runtime == "claude-code":
                lines.extend((
                    "Claude 帳號目錄：" + chat_tui_pm.safe_text(
                        pm_runtime.display_path(
                            getattr(self.pm_runtime, "claude_config_dir", None)) or "UNKNOWN"),
                    "權限模式（本機設定）：" + chat_tui_pm.permission_mode()
                    + "（自動，不跳出確認）"))
            else:
                # The folder and permission rows describe Claude only (#683 B6).
                binding = self.pm_binding if type(self.pm_binding) is dict else {}
                lines.append("帳號：" + chat_tui_pm.safe_text(binding.get("account") or None))
            lines.extend((
                "PM 狀態：" + self.pm_status_fields(lambda value, _width: value)["state"],
                "背景：" + ("讀取中" if count is None else
                          "UNKNOWN（TaskStore：" + chat_tui_pm.safe_text(
                              str(self.background_error or "read failed")).replace("\n", " ")
                          + "）" if count == "?" else "%d 件進行中" % count),
                "佇列（本機設定）：" + queue, theme))
            try:
                turns = self.pm_api.read_all_turns(self.pm_target[0])
                usage = chat_tui_pm.usage_status_line(turns, _short_count)
            except Exception:
                usage = "用量（本 session）：UNKNOWN（PM 歷史無法讀取）"
            lines.extend((usage, "Context 與額度：UNAVAILABLE（llmtool 沒有這項資料）"))
            return "\n  ".join(lines)
        session = self.session
        model = selected_provider(session.config).get("model")
        served = session.last_served_model or session.last_reported_model
        if served is None:
            served = "尚未呼叫" if session.last_elapsed is None else "上次回覆沒有回報"
        state = {"idle": "待命", "working": "處理中", "interrupted": "已中斷"}.get(
            session.state, session.state)
        if session.state == "error":
            state = "錯誤（" + (session.last_error_code or "未分類") + "）"
        lines = ["狀態（聊天模式，不是 PM）",
                 "供應商（本機設定）：" + session.provider + "（" + selected_provider(session.config)["type"]
                 + "）",
                 "設定的模型（本機設定）：" + (model or "供應商預設"),
                 "實際模型：" + served,
                 "技能：" + (session.skill.name if session.skill else "無"),
                 "對話：%d 輪" % (len(session.history) // 2),
                 "狀態：" + state
                 + ("（上次 %.1f 秒）" % session.last_elapsed
                    if session.last_elapsed is not None else ""),
                 "用量：" + ("尚無" if session.last_usage is None
                           else _tokens(session.last_usage) + " tokens（供應商回報）"),
                 "連線：未檢查",
                 theme]
        return "\n  ".join(lines)

    def run_display_command(self, text):
        """/theme, /links and /settings: change local display and resume settings."""
        parts = text.split()
        if parts[0] == "/settings":
            if len(parts) == 1:
                self.append("Settings: auto-resume %s. /settings auto-resume on|off"
                            % self.settings.get("auto_resume", "on"))
                return
            if len(parts) != 3 or parts[1] != "auto-resume" or parts[2] not in ("on", "off"):
                self.append("usage: /settings [auto-resume on|off]")
                return
            self.settings["auto_resume"] = parts[2]
            if parts[2] == "off":
                self._auto_resume_pending = False
            self.append("Settings: auto-resume " + parts[2] + self._save_settings())
            return
        if parts[0] == "/theme":
            if len(parts) == 1:
                self.append("Theme: %s (drawn %s%s). /theme dark|light|auto"
                            % (self.settings["theme"], self.theme,
                               "" if self.colour else ", no colour: NO_COLOR or terminal"))
                return
            if len(parts) != 2 or parts[1] not in chat_tui_theme.THEME_CHOICES:
                self.append("usage: /theme dark|light|auto")
                return
            self.settings["theme"] = parts[1]
            self.theme = chat_tui_theme.resolve_theme(parts[1])
            self.append("Theme: %s (drawn %s)%s" % (parts[1], self.theme, self._save_settings()))
            return
        if len(parts) == 1:
            self.append("Links: %s%s. /links on|off" % (
                "on" if self.links_on() else "off",
                "" if self.links_supported else " (this terminal has no OSC 8 links)"))
            return
        if len(parts) != 2 or parts[1] not in ("on", "off"):
            self.append("usage: /links on|off")
            return
        self.settings["links"] = parts[1]
        saved = self._save_settings()
        if parts[1] == "on" and not self.links_supported:
            self.append("Links: off — this terminal has no OSC 8 links (iTerm2, WezTerm, Warp "
                        "and kitty have them); the setting is kept for those" + saved)
            return
        self.append("Links: " + parts[1] + saved)

    def picker_entries(self):
        if self.searching:
            # Earlier input is shown, never executed; one row each, escaped for the terminal.
            return [" " + terminal_safe_text(entry).replace("\n", " ↵ ").replace("\t", " ") + " "
                    for entry in self.search_matches]
        return [" %-14s %s " % (label, summary) for label, _fill, summary in self.menu_items]

    def picker_layout(self):
        """(header, entry, "more") rows that fit beside the minimum chat layout.

        The picker shrinks instead of raising the minimum terminal height; (0, 0, 0)
        hides it when not even one row is free.
        """
        if not (self.searching or self.menu_items):
            return 0, 0, 0
        input_rows = min(4, max(1, self.input.buffer.document.line_count))
        free = self.app.output.get_size().rows - 3 - input_rows - self.busy_rows()
        if free < 1:
            return 0, 0, 0
        header = 1 if self.searching else 0
        space = free - header
        count = len(self.picker_entries())
        shown = min(count, MENU_LIMIT, space)
        hint = 0
        if count > shown:
            if space > shown:
                hint = 1
            elif shown >= 2:
                shown, hint = shown - 1, 1
        return header, shown, hint

    def _pm_busy_wanted(self):
        return (self.pm_turn_active and self.pm_runtime is not None
                and self.pm_turn_status is None and self.pm_turn_error is None
                and self.pm_state != "closing")

    def _pm_waiting_wanted(self):
        count = self.background_count()
        return (self.pm_mode and self.pm_runtime is not None and not self.pm_turn_active
                and not self.menu_items and not self.searching and not self.background_open()
                and self.pm_state != "closing" and type(count) is int and count > 0)

    def _pm_waiting_line(self):
        items = self.background_all()
        line = "◌ PM 在等背景工作，完成會自動通知：" + chat_tui_status.background_summary(items[0])
        if len(items) > 1:
            line += "（另 %d 項）" % (len(items) - 1)
        return line

    def busy_rows(self):
        input_rows = min(4, max(1, self.input.buffer.document.line_count))
        free = self.app.output.get_size().rows - 3 - input_rows
        return int((self._pm_busy_wanted() or self._pm_waiting_wanted()) and free >= 1)

    def busy_fragments(self):
        text = self._pm_busy_line() if self._pm_busy_wanted() else self._pm_waiting_line()
        line = fit_width(text, self.app.output.get_size().columns,
                         self._get_cwidth)
        return [(self._style("meta.usage"), line)]

    def _pm_busy_line(self):
        elapsed = int(max(0.0, time.monotonic() - (self.pm_turn_started or time.monotonic())))
        if self.pm_state == "interrupting":
            verb = "中斷中…"
        elif self.pm_state == "thinking":
            verb = "思考中…"
        elif self.pm_warm:
            verb = "暖機中…"
        elif any(call[1] is None for call in self.pm_tool_calls):
            verb = "執行工具中…"
        elif self.pm_block_raw is not None:
            verb = "回答中…"
        else:
            verb = "處理中…"
        line = "%s %s（%d 秒" % (PM_BUSY_GLYPHS[self._pm_busy_frame % len(PM_BUSY_GLYPHS)],
                                  verb, elapsed)
        if self.pm_outbox:
            line += " · 佇列 %d 則" % len(self.pm_outbox)
        return line + "）"

    def _menu_top(self, shown, count):
        """First visible entry: follows the selection and stays inside the list."""
        top = max(min(self.menu_offset, self.menu_index), self.menu_index - shown + 1)
        self.menu_offset = max(0, min(top, count - shown))
        return self.menu_offset

    def menu_text(self):
        header, shown, hint = self.picker_layout()
        width = max(1, self.app.output.get_size().columns)
        entries = self.picker_entries()
        rows = []
        if header:
            query = terminal_safe_text(self.search_query).replace("\n", " ↵ ")
            title = " history search: " + query + ("" if entries else "  (no match)")
            rows.append(("class:menu.header", fit_width(title, width, self._get_cwidth)))
        top = self._menu_top(shown, len(entries)) if shown else 0
        for index in range(top, top + shown):
            style = "class:menu.current" if index == self.menu_index else "class:menu"
            rows.append((style, fit_width(entries[index], width, self._get_cwidth)))
        if hint:
            more = " … %d more (%d above, %d below)" % (
                len(entries) - shown, top, len(entries) - top - shown)
            rows.append(("class:menu", fit_width(more, width, self._get_cwidth)))
        result = []
        for row in rows:
            result.extend([row, ("", "\n")])
        return result[:-1]

    # -- slash-command picker (suggests and completes the draft; never executes) ----
    def _refresh_menu(self, typed):
        if not typed and not self.menu_items:
            return
        buffer = self.input.buffer
        text = buffer.text
        if buffer.cursor_position != len(text) or self.busy:
            items = []
        else:
            items = slash_matches(text, self.dev)
        # Select the exact command match when the draft equals its label.
        selected = next((index for index, item in enumerate(items)
                         if item[0] == text), 0)
        self._set_menu(items, selected=selected)

    def _set_menu(self, items, selected=None):
        if selected is None:
            text = self.input.text
            selected = next((index for index, item in enumerate(items)
                             if item[0] == text), 0)
        if items != self.menu_items or self.menu_index != selected:
            self.menu_index = selected
            self.menu_offset = 0
        self.menu_items = items
        self._sync_picker()

    def _complete_slash_tab(self):
        """Complete the slash draft or extend it to its candidates' common prefix."""
        text = self.input.text
        if self.input.buffer.cursor_position != len(text):
            self.close_menu()
            return
        # Tab completion is narrower than the interactive picker's fuzzy search:
        # only commands whose full label starts with the typed draft are candidates.
        commands = [command for command in SLASH_COMMANDS
                    if self.dev or not command[0].startswith("/task")
                    or command[0] in READ_ONLY_TASK_COMMANDS]
        matches = [command for command in commands if command[0].startswith(text)]
        if not matches:
            self.close_menu()
            return
        if len(matches) == 1:
            self.close_menu()
            fill = matches[0][1]
            self.input.buffer.set_document(self._Document(fill, len(fill)))
            return
        prefix = _common_prefix([item[0] for item in matches])
        if len(prefix) > len(text):
            self.input.buffer.set_document(self._Document(prefix, len(prefix)))
            matches = [command for command in commands if command[0].startswith(prefix)]
        self._set_menu(matches)

    def _sync_picker(self):
        """Match picker state to what is on screen.

        A history search with no free row ends and keeps the draft, so nothing can
        replace it unseen. The short lone-Esc timeout applies only while the picker is
        visible; otherwise the usual Esc Enter window holds.
        """
        if self.searching and not sum(self.picker_layout()):
            self.close_menu()  # Comes back here with searching off.
            return
        # The detail and events panes are text areas with Esc-prefixed bindings, so a
        # lone Esc (close) would otherwise wait the full default timeout.
        shown = (sum(self.picker_layout()) > 0 or (self.show_detail and not self.show_help)
                 or self.events_visible() or self.task_view_visible() or self.pm_turn_active
                 or self.background_open()
                 or (self.busy and self.worker_kind == "chat"))
        self.app.timeoutlen = ESC_SEQUENCE_TIMEOUT if shown else self._default_timeoutlen

    def move_menu(self, direction):
        count = len(self.search_matches) if self.searching else len(self.menu_items)
        if count:
            self.menu_index = (self.menu_index + direction) % count

    def close_menu(self):
        """Close the command picker and end any history search; the draft is unchanged."""
        self.searching = False
        self.search_query = ""
        self.search_matches = []
        self.menu_index = 0
        self.menu_offset = 0
        self._set_menu([])

    # -- Ctrl-R history search (this session's in-memory inputs only; never executes) ----
    def start_search(self):
        self._set_menu([])
        self.searching = True
        self.search_query = ""
        self._refresh_search()  # Ends at once if there is no free row to show it.

    def type_search(self, text):
        self.search_query += text
        self._refresh_search()

    def _refresh_search(self):
        self.search_matches = history_matches(self.input.buffer.history.get_strings(),
                                              self.search_query)
        self.menu_index = 0
        self.menu_offset = 0
        self._sync_picker()

    def accept_search(self):
        """Enter while searching: the chosen entry becomes the draft; nothing runs."""
        chosen = self.search_matches[self.menu_index] if self.search_matches else None
        self.close_menu()
        if chosen is not None:
            self.input.buffer.set_document(self._Document(chosen, len(chosen)))

    def accept_menu(self):
        """Enter with the picker open; True if Enter was consumed.

        Enter only completes a draft that differs from the chosen suggestion and never
        runs it. A draft the user already typed in full runs as it would with no picker.
        """
        if not self.menu_items:
            return False
        _label, fill, _summary = self.menu_items[self.menu_index]
        text = self.input.text
        self.close_menu()
        if text in (fill, fill.rstrip()):
            return False  # Draft already is this command; Enter submits it as typed.
        self.input.buffer.set_document(self._Document(fill, len(fill)))
        return True

    def append(self, text, show_working=True, segments=None, sources=()):
        """Add untrusted text; follow new output only if the caret was at the end.

        ``segments`` ([(text, token)]) styles the block; without it an owner header gets
        the 「你」 layout, an ``llmtool:`` notice ``role.system`` and anything else ``text``.
        New output closes the Task detail so it is never hidden behind that pane. During a
        PM turn the busy line stays fixed above the input. Output
        of an Owner key or command while a PM block streams goes below that block (W55).
        """
        if self._owner_output and self.pm_block_raw is not None:
            self._pm_notice(text)
            return
        self._pm_finalize()  # Any other output ends the streaming PM block (shown in full).
        block = self._stream_detach()  # A streaming chat reply stays one block, below.
        if self.show_detail:
            self.toggle_detail(False)
        if self.show_list or self.show_events:
            self.unseen_output = True  # The pane header says so; it stays for browsing.
            if self.show_events:
                self.render_events()
        old = self.conversation.text
        if segments is None:
            segments = self._notice_segments(terminal_safe_text(text))
        else:
            segments = [(terminal_safe_text(part), token) for part, token in segments]
        separator = "\n\n" if old else ""
        self.view.set_tail(len(old), [(separator, "text")] + segments,
                           [(low + len(separator), high + len(separator), *rest)
                            for low, high, *rest in sources])
        if block is not None:
            self._stream_attach(block)
        if show_working:
            self._pm_busy_show()
        self.app.invalidate()

    # -- layout (W56a) -----------------------------------------------------------------
    def _notice_segments(self, text):
        if text.startswith(OWNER_PREFIX):
            return self._owner_segments(text, rule=False)
        if text.startswith("llmtool:"):
            return [(text, "role.system")]
        return [(text, "text")]

    def _pm_runtime_name(self):
        """The connected PM's runtime: from its binding, else from the runtime object."""
        binding = self.pm_binding if type(self.pm_binding) is dict else {}
        return binding.get('runtime', getattr(self.pm_runtime, 'runtime', 'claude-code'))

    def _append_account(self, before, after):
        """A PM notice with the provider and account name, never its config directory."""
        account = chat_tui_pm.account_text(self.pm_runtime, self.pm_binding)
        self.append(before + account + after,
                    segments=[(before, "text"), (account, "role.system"), (after, "text")])

    @staticmethod
    def _rule_segments():
        """A turn rule: drawn across the pane, local time at the right end."""
        return [(chat_tui_view.RULE_CHAR * 2 + " " + time.strftime("%H:%M"), "rule"), ("\n", "text")]

    def _owner_segments(self, text, rule):
        """'owner → PM<note>:\nbody' as the 「你」 label (note kept) over the indented body."""
        header, _newline, body = text.partition("\n")
        note = header[len(OWNER_PREFIX):].rstrip(":").rstrip()
        segments = self._rule_segments() if rule else []
        segments += [(OWNER_LABEL, "role.owner")]
        if note:
            segments.append((note, "role.system"))
        segments.append(("\n" + BODY_INDENT + body.replace("\n", "\n" + BODY_INDENT), "text"))
        return segments

    def _render_width(self):
        width = self.view.width
        if not width:
            try:
                width = self.app.output.get_size().columns
            except Exception:
                width = 80
        return max(1, width)

    def _body_segments(self, text, indent):
        """Reply text (already terminal-safe) through the Markdown renderer, indented."""
        segments = []
        for number, line in enumerate(chat_tui_view.render_markdown(
                text, self._render_width() - len(indent))):
            if number:
                segments.append(("\n", "text"))
            if indent:
                segments.append((indent, "text"))
            segments.extend(line)
        return segments

    def _body(self, text, indent):
        """(segments, sources) of a reply body: copies of it give back ``text``."""
        segments = self._body_segments(text, indent)
        return segments, [(0, sum(len(part) for part, _token in segments), text,
                           self._render_width() - len(indent), indent)]

    def _pm_open_block(self):
        """Open the PM text block: under the 「PM」 label the first time in a turn."""
        if self.pm_header_open:   # right under a background turn's header line
            self.pm_header_open = False
            self.pm_block_start = len(self.conversation.text) + 1   # after "\n"
        elif self.pm_labelled:
            self.append("", show_working=False)   # a blank line, then the block
            self.pm_block_start = len(self.conversation.text)
        else:
            self.append(self.pm_label, show_working=False, segments=[(self.pm_label, "role.pm")])
            self.pm_block_start = len(self.conversation.text) + 1   # after "\n"
            self.pm_labelled = True
        self.pm_block_raw = ""

    def _tool_block_valid(self):
        block = self.pm_tool_block
        return block is not None and self.conversation.text[block[0]:block[1]] == block[4]

    def _pm_tool_summary(self):
        """Show this turn's tools as one line (in place once it exists)."""
        segments = tool_summary_lines(self.pm_tool_calls, self.pm_tools_expanded)
        shown = "".join(part for part, _token in segments)
        if self.pm_tool_block is not None and self.pm_tool_block[3] == self._pm_turn_seq \
                and self._tool_block_valid():
            self._replace_block(segments, shown)
            return
        label = [] if self.pm_labelled else [(self.pm_label, "role.pm"), ("\n", "text")]
        self.pm_labelled = True
        self.append(shown, segments=label + segments)
        end = len(self.conversation.text)
        self.pm_tool_block = [end - len(shown), end, self.pm_tool_calls, self._pm_turn_seq, shown]

    def _replace_block(self, segments, shown):
        """Re-render the latest tool summary where it is; offsets after it move along."""
        start, end = self.pm_tool_block[0], self.pm_tool_block[1]
        delta = len(shown) - (end - start)
        self.view.replace(start, end, segments)
        for name in ("pm_block_start", "stream_head", "stream_start"):
            value = getattr(self, name)
            if value is not None and value >= end:
                setattr(self, name, value + delta)
        self.pm_tool_block[1], self.pm_tool_block[4] = start + len(shown), shown
        self.app.invalidate()

    def _pm_tool_tick(self):
        """Every busy-line tick: a running tool's seconds advance in its line (W57)."""
        block = self.pm_tool_block
        if (block is not None and block[3] == self._pm_turn_seq and self._tool_block_valid()
                and any(call[1] is None for call in block[2])):
            segments = tool_summary_lines(block[2], self.pm_tools_expanded)
            shown = "".join(part for part, _token in segments)
            if shown != block[4]:
                self._replace_block(segments, shown)

    def toggle_tools(self):
        """F7: expand or collapse the latest tool summary (and the ones after it)."""
        self.pm_tools_expanded = not self.pm_tools_expanded
        if self._tool_block_valid():
            segments = tool_summary_lines(self.pm_tool_block[2], self.pm_tools_expanded)
            self._replace_block(segments, "".join(part for part, _token in segments))

    def conversation_visible(self):
        return not (self.show_help or self.show_detail or self.show_list or self.show_events
                    or self.show_task_view)

    def scroll(self, direction):
        """PageUp/PageDown: one screen of rows; the pane keeps or resumes following itself."""
        if self._mouse_view() is self.list_view:
            self.move_list_page(direction)
            return
        (self._mouse_view() or self.view).page(direction)

    # -- mouse: wheel, drag selection, edge auto-scroll, copy ------------------------------
    def mouse_event(self, event):
        """Every vt100 mouse event: the conversation pane's own handling, else the default.

        The wheel scrolls the conversation from anywhere while it is shown (never the input
        or its history). A left drag that starts on the pane selects text, keeps selecting
        while the pointer is beyond the pane, and copies on release. Other clicks (input,
        picker, status) go to prompt_toolkit's own handler.
        """
        parsed = chat_tui_view.parse_mouse(event.data)
        if parsed is None or self._mouse_pane() is None or not self._pane_mouse(*parsed):
            return self._default_mouse_binding.call(event)
        return None

    def _mouse_pane(self):
        """(view, pane) that the wheel and drag selection act on: the W59 Task detail when it
        is shown (W60: same selection and copy as the conversation), else the conversation
        when it is shown, else None."""
        if self.task_view_visible():
            return self.task_view, self.task_pane
        if self.show_detail and not self.show_help:
            return self.detail_view, self.detail       # F2, also opened from the F3 list
        if self.list_visible():
            return self.list_view, self.list_window
        if self.events_visible():
            return self.events_view, self.events
        if self.conversation_visible():
            return self.view, self.conversation
        return None

    def _mouse_view(self):
        shown = self._mouse_pane()
        return None if shown is None else shown[0]

    def _pane_geometry(self):
        shown = self._mouse_pane()
        info = None if shown is None else shown[1].window.render_info
        if info is None:
            return None
        return info._x_offset, info._y_offset, info.window_height

    def _pane_mouse(self, kind, button, x, y):
        from prompt_toolkit.mouse_events import MouseButton, MouseEventType
        view = self._mouse_view()
        geometry = self._pane_geometry()
        if geometry is None:
            return False
        left, top, height = geometry
        inside = top <= y < top + height
        if kind == MouseEventType.SCROLL_UP:
            view.scroll_rows(-chat_tui_view.WHEEL_ROWS)
            return True
        if kind == MouseEventType.SCROLL_DOWN:
            view.scroll_rows(chat_tui_view.WHEEL_ROWS)
            return True
        if view.dragging and button == MouseButton.LEFT and kind in (MouseEventType.MOUSE_MOVE,
                                                                     MouseEventType.MOUSE_UP):
            # A click (no movement) never scrolls, selects or touches the clipboard; a
            # release never scrolls either, it only extends to the pointer.
            if (x, y) != self._drag_origin:
                self._drag_moved = True
            if kind == MouseEventType.MOUSE_MOVE:
                if self._drag_moved:
                    self._drag_to(x, y)
            else:
                self._autoscroll_stop()
                self._drag_copy_stop()   # the release copies right away
                if self._drag_moved:
                    self._extend_to(x, y)
                view.end_drag(view.head)
            return True
        if not inside:
            return False
        if kind == MouseEventType.MOUSE_DOWN and button == MouseButton.LEFT:
            self._autoscroll_stop()
            self._drag_copy_stop()   # a drag whose release never came is replaced
            self._forget_copy_label(view)   # W60: a drag copy is reported as plain 已複製
            view.begin_drag(view.offset_at(view.top + y - top, x - left))
            self._drag_pointer = self._drag_origin = (x, y)
            self._drag_moved = False
            return True
        if kind == MouseEventType.MOUSE_DOWN:
            view.clear_selection()
        return True  # Clicks on the pane never move focus or the input caret.

    def _extend_to(self, x, y):
        """Extend the selection to the pointer's cell (clamped to the pane), no scrolling."""
        geometry = self._pane_geometry()
        if geometry is None:
            return
        left, top, height = geometry
        view = self._mouse_view()
        row = max(view.top, min(view.top + height - 1, view.top + y - top))
        view.extend_drag(view.offset_at(row, max(0, x - left)))
        self.app.invalidate()

    def _drag_to(self, x, y):
        """Extend a moving drag to the pointer; at or beyond an edge, scroll a row first.

        Only reached after the pointer left the press cell. The pane's first and last rows
        count as the edge because Terminal.app clamps drags that leave the window to them.
        """
        view = self._mouse_view()
        geometry = self._pane_geometry()
        if geometry is None:
            return
        left, top, height = geometry
        self._drag_pointer = (x, y)
        if y <= top and view.top > 0:
            view.scroll_rows(-1)
            row = view.top
        elif y >= top + height - 1 and not view.following:
            view.scroll_rows(1)
            row = view.top + height - 1
        else:
            row = max(view.top, min(view.top + height - 1, view.top + y - top))
        view.extend_drag(view.offset_at(row, max(0, x - left)))
        at_edge = y <= top or y >= top + height - 1
        if at_edge and self._autoscroll_handle is None:
            # Mouse events arrive on the running loop; keep scrolling without new events.
            self._autoscroll_handle = asyncio.get_running_loop().call_later(
                chat_tui_view.AUTOSCROLL_SECONDS, self._autoscroll_tick)
        elif not at_edge:
            self._autoscroll_stop()
        if self._drag_copy_handle is None:
            self._drag_copy_handle = asyncio.get_running_loop().call_later(
                chat_tui_view.DRAG_COPY_SECONDS, self._drag_copy_tick)
        self.app.invalidate()

    def _drag_copy_tick(self):
        """Copy the selection of a drag still in progress (W52 F3).

        Warp reports no release when the button goes up outside its grid (its log shows
        the drag events and then no Released), and it keeps Cmd+C for itself, so a copy
        made only on release left the old clipboard. The worker keeps only the newest
        request, so the clipboard ends with the selection as it was last drawn.
        """
        self._drag_copy_handle = None
        view = self._mouse_view()
        if view is not None and view.dragging and self._drag_moved:
            view.copy_selection()
            self.app.invalidate()

    def _drag_copy_stop(self):
        if self._drag_copy_handle is not None:
            self._drag_copy_handle.cancel()
            self._drag_copy_handle = None

    def _autoscroll_tick(self):
        self._autoscroll_handle = None
        view = self._mouse_view()
        if view is not None and view.dragging and self._drag_pointer is not None:
            self._drag_to(*self._drag_pointer)

    def _autoscroll_stop(self):
        if self._autoscroll_handle is not None:
            self._autoscroll_handle.cancel()
            self._autoscroll_handle = None

    def toggle_focus(self):
        self.close_menu()  # Also restores the default Esc timeout.
        layout = self.app.layout
        pane = (self.help if self.show_help else self.detail if self.show_detail
                else self.list_window if self.show_list
                else self.events if self.show_events
                else self.task_pane if self.show_task_view else self.conversation)
        layout.focus(pane if layout.has_focus(self.input) else self.input)

    def toggle_mouse(self):
        """F6: mouse reporting on (wheel scrolls, drag selects and copies) or off (the
        terminal's own drag-select and copy; the wheel no longer reaches the pane)."""
        self.mouse_enabled = not self.mouse_enabled
        self._autoscroll_stop()
        for view in self._read_views():
            view.clear_selection()
        if self.mouse_enabled:
            self.append("llmtool: mouse on; the wheel scrolls the conversation and a drag selects"
                        " and copies text. F6 turns it off to select with the terminal instead")
        else:
            self.append("llmtool: mouse off; drag to select and copy text with the terminal."
                        " Fn+↑/↓ (PageUp/PageDown) scroll; F6 turns the wheel back on")

    def _leave_conversation(self):
        """Any pane switch ends a drag, its auto-scroll timer and the selection, so nothing
        hidden keeps scrolling, extending or capturing Ctrl-C."""
        self._autoscroll_stop()
        self._drag_copy_stop()
        self._drag_pointer = None
        for view in self._read_views():   # W60: every read-only pane's selection
            view.clear_selection()

    def toggle_help(self):
        self.show_help = not self.show_help
        self._leave_conversation()
        # Never leave focus on a hidden pane; the help pane itself scrolls when focused.
        # Closing help over the list or events pane gives it focus back, so Esc closes it.
        pane = (self.list_window if self.list_visible()
                else self.events if self.events_visible() else self.input)
        self.app.layout.focus(pane)
        self._sync_picker()

    def toggle_detail(self, show):
        """Show the last Task detail read from Core in place of the conversation."""
        if show and not self.detail.text:
            self.append("llmtool: no Task detail loaded yet; use /task show TASK_ID")
            show = False
        self._leave_conversation()
        self.show_detail = show
        # Closing a detail opened from the list goes back to the list. A detail opened from
        # the list has focus (W60: C and ↑↓ act on it); one shown after a Task mode request
        # leaves focus on the input (Tab reaches it), so typing continues there.
        self.app.layout.focus((self.detail if self.show_list else self.input) if show else
                              self.list_window if self.show_list else self.input)
        self._sync_picker()

    # -- Task list (TaskStore reads only; never a provider or network call) ----------
    def toggle_list(self, show):
        self._leave_conversation()
        self.show_list = show
        self._list_shown_index = None   # W60: scroll to the selection when shown
        if show:
            self.show_detail = False
            self.show_help = False
            self.refresh_list()
            self.app.layout.focus(self.list_window)
        else:
            self.unseen_output = self.unseen_output and self.show_events
            self.app.layout.focus(self.events if self.show_events else self.input)
        self._sync_picker()

    def toggle_events(self, show):
        """Recent events across Tasks; shares the list's TaskStore reader and interval."""
        self._leave_conversation()
        self.show_events = show
        if show:
            self.show_list = False
            self.show_detail = False
            self.show_help = False
            self.render_events()
            self.refresh_list()
            self.app.layout.focus(self.events)
        else:
            self.unseen_output = False
            self.app.layout.focus(self.input)
        self._sync_picker()

    def render_events(self):
        if self.list_loaded is None:
            text = "Recent events: reading TaskStore…"
        elif self.list_error is not None:
            text = "Recent events: llmtool: " + self.list_error
        else:
            text = chat_tui_events.recent_text(self.list_records_all, self.list_unreadable,
                                               self.list_loaded[1])
        if self.unseen_output:
            text = "new conversation output — Esc shows it\n" + text
        self._set_read_text(self.events_view, terminal_safe_text(text))

    def refresh_list(self):
        """Start one TaskStore read unless one is already running."""
        if self.list_reader is not None:
            return
        self._loop = asyncio.get_running_loop()
        self.list_reader = threading.Thread(target=self._read_list, args=(self._loop,),
                                            name="llmtool-chat-task-list", daemon=True)
        self.list_reader.start()

    def _read_list(self, loop):
        try:
            if not self.dev and task_store_absent():
                # #1819 F1: a read-only view never creates the store; absent reads as empty.
                outcome = ([], 0, None)
            else:
                from .task_store import TaskStore
                records, unreadable = chat_tui_status.load_records(TaskStore())
                outcome = (records, unreadable, None)
        except LLMToolError as exc:
            outcome = ([], 0, str(exc))
        except Exception:
            outcome = ([], 0, "task list read failed; details omitted")
        try:
            loop.call_soon_threadsafe(self.finish_list, outcome)
        except RuntimeError:
            pass

    def finish_list(self, outcome):
        records, unreadable, error = outcome
        selected = self.selected_task_id()
        self.list_reader = None
        self.list_records_all = records
        self.list_unreadable, self.list_error = unreadable, error
        self._project_list_records(selected)
        self.list_loaded = (time.monotonic(), time.strftime("%H:%M:%S"))
        if self.show_events:
            self.render_events()
        self.app.invalidate()

    def _project_list_records(self, selected=None):
        """Keep F3 selection and actions bound to each visible group's newest Task."""
        if selected is None:
            selected = self.selected_task_id()
        self.list_groups, self.list_hidden_count = chat_tui_status.group_rows(
            self.list_records_all, datetime.now(timezone.utc), self.list_show_all)
        self.list_records = [group["record"] for group in self.list_groups]
        ids = [chat_tui_task.value(record, "task_id") for record in self.list_records]
        self.list_index = ids.index(selected) if selected in ids else min(
            self.list_index, max(0, len(self.list_records) - 1))

    def list_visible(self):
        """The list is on screen: not hidden behind the detail or help pane."""
        return self.show_list and not self.show_detail and not self.show_help

    def events_visible(self):
        """The recent-events pane is on screen (the list, detail and help cover it)."""
        return (self.show_events and not self.show_list and not self.show_detail
                and not self.show_help)

    def _list_tick(self):
        # Interval reads only while the list or events pane is actually visible (#510 review v2).
        if ((self.list_visible() or self.events_visible()) and self.list_reader is None
                and self.list_loaded is not None
                and time.monotonic() - self.list_loaded[0] >= chat_tui_status.REFRESH_SECONDS):
            self.refresh_list()

    def selected_task_id(self):
        if not self.list_records:
            return None
        return chat_tui_task.value(self.list_records[self.list_index], "task_id")

    def move_list(self, direction):
        if self.list_records:
            self.list_index = max(0, min(len(self.list_records) - 1, self.list_index + direction))

    def open_selected(self):
        task_id = self.selected_task_id()
        if task_id is None or task_id in (chat_tui_task.UNKNOWN, chat_tui_task.NOT_AVAILABLE):
            return
        if self.busy:
            self.append("llmtool: a request is in progress; open the Task again when idle")
            return
        self.start_task("show", task_id, None)

    def list_text(self):
        """The rows that fit the list pane (selected row in view)."""
        return self._list_fragments(full=False)

    def _list_group(self, index, record):
        """Return the projection for a row, including transient placeholder rows."""
        if (index < len(self.list_groups)
                and self.list_groups[index].get("record") is record):
            return self.list_groups[index]
        groups, _hidden = chat_tui_status.group_rows(
            [record], datetime.now(timezone.utc), True)
        return groups[0]

    def _list_fragments(self, full):
        """List fragments; ``full``: every listed Task (W60: the scrollable list view)."""
        width = max(1, self.app.output.get_size().columns)
        cut = lambda text: fit_width(terminal_safe_text(text).replace("\n", " "), width,
                                     self._get_cwidth)
        if self.list_loaded is None:
            state = "reading TaskStore…"
        else:
            state = ("%d shown, %d hidden, %d unreadable"
                     % (len(self.list_records), self.list_hidden_count, self.list_unreadable))
        header = " Tasks (stage derived by the TUI): " + state + "; a shows all"
        if self.list_loaded is not None:
            header += ("; refreshed %s; auto every %ds"
                       % (self.list_loaded[1], chat_tui_status.REFRESH_SECONDS))
        rows = [("class:menu.header", cut(header)),
                ("class:menu.header", cut("  " + "Task ID".ljust(8) + "  " + "Stage (derived)".ljust(26)
                                          + "Rounds".ljust(12) + "PR".ljust(12) + "Owner".ljust(8)
                                          + "Request (Core, first line) — Enter detail; F5/r refresh; Esc close"))]
        if self.unseen_output:
            rows.append(("class:menu.header", cut(" new conversation output — Esc shows it")))
        if self.list_error is not None:
            rows.append(("", cut(" llmtool: " + self.list_error)))
        elif self.list_loaded is not None and not self.list_records:
            rows.append(("", cut(" no Tasks in TaskStore")))
        info = self.list_window.window.render_info
        space = max(1, (info.window_height if info is not None else 20) - len(rows))
        top = max(0, min(self.list_index - space + 1, len(self.list_records) - space))
        top = min(top, self.list_index)
        if full:
            self._list_header_rows = len(rows)
            top, space = 0, len(self.list_records)
        for index in range(top, min(len(self.list_records), top + space)):
            record = self.list_records[index]
            item = chat_tui_status.row(record)
            group = self._list_group(index, record)
            stage = item["stage"] + " " * max(0, 26 - self._get_cwidth(item["stage"]))
            rounds = ("+%d rounds" % group["rounds"] if group["rounds"] else "-").ljust(12)
            text = (("▶ " if index == self.list_index else "  ") + item["task_id"][:8] + "  "
                    + stage + rounds + (group["pr"] or "-").ljust(12)
                    + ("Owner" if group["owner"] else "").ljust(8) + item["name"])
            rows.append(("class:menu.current" if index == self.list_index else "", cut(text)))
        result = []
        for row in rows:
            result.extend([row, ("", "\n")])
        return result[:-1]

    _LIST_TOKENS = {"class:menu.header": "ui.menu.header", "class:menu.current": "ui.menu.current"}

    def _sync_list_view(self):
        """W60: the whole list in the list's read view, so it scrolls (wheel, PgUp/PgDn,
        Up/Down past the edge), a drag selects across pages and C copies every row."""
        if not self.list_visible():
            return
        view = self.list_view
        segments = [(text, self._LIST_TOKENS.get(style, "text"))
                    for style, text in self._list_fragments(full=True)]
        text = "".join(part for part, _token in segments)
        if text != view.text or not view.spans:
            view.set_tail(0, segments)
            # A short first draw ("reading TaskStore…") fits the pane and turns following
            # on; the list never follows its end, it keeps the selection in view instead.
            view.following = view.unseen = False
        if self._list_shown_index != self.list_index:
            self._list_shown_index = self.list_index
            self._list_scroll_to(self._list_header_rows + self.list_index
                                 if self.list_index else 0)

    def _list_scroll_to(self, line):
        """Keep text line ``line`` of the list view on screen (rows are one line each)."""
        view = self.list_view
        info = self.list_window.window.render_info
        height = max(1, info.window_height if info is not None else 20)
        top = view.top
        if line < top:
            top = line
        elif line >= top + height:
            top = line - height + 1
        if top == view.top and not view.following:
            return
        starts = [0] + [index + 1 for index, char in enumerate(view.text) if char == "\n"]
        top = max(0, min(top, len(starts) - 1))
        view.top, view.top_offset, view.following = top, starts[top], False

    def move_list_page(self, direction):
        """W60 PgUp/PgDn in F3: the selection moves a screen; the view keeps it shown."""
        info = self.list_window.window.render_info
        height = max(1, (info.window_height if info is not None else 20) - 1)
        self.move_list(direction * height)

    def list_plain_text(self):
        """W60 C in F3: every listed Task as plain text (not only the rows on screen)."""
        fragments = [text for _style, text in self._list_fragments(full=True)]
        lines = "".join(fragments).split("\n")[:self._list_header_rows]
        for index, record in enumerate(self.list_records):
            item = chat_tui_status.row(record)
            group = self._list_group(index, record)
            stage = item["stage"] + " " * max(0, 26 - self._get_cwidth(item["stage"]))
            rounds = ("+%d rounds" % group["rounds"] if group["rounds"] else "-").ljust(12)
            lines.append(("▶ " if index == self.list_index else "  ") + item["task_id"] + "  "
                         + stage + rounds + (group["pr"] or "-").ljust(12)
                         + ("Owner" if group["owner"] else "").ljust(8) + item["name"])
        return terminal_safe_text("\n".join(lines))

    def set_detail(self, text, task_id=None):
        self.detail_task_id = task_id
        safe_text = terminal_safe_text(text)
        self._set_read_text(self.detail_view, safe_text, keep=False, highlight_lines=3)
        self.show_help = False
        self.toggle_detail(True)

    # -- actions -------------------------------------------------------------------
    def submit(self):
        self._owner_output = True
        try:
            self._submit()
        finally:
            self._owner_output = False

    def _submit(self):
        text = self.input.text
        if self.busy:
            # Single flight: the typed input stays in the editor, nothing is queued.
            self.append("llmtool: a request is in progress; input kept, send again when idle")
            return
        if not text.strip():
            self.input.buffer.reset()
            if self.pm_mode and self.pm_outbox and self.pm_outbox_paused:
                self._pm_resume(everything=False)   # W55: Enter sends the next queued draft
            return
        if text.lstrip().startswith("/"):
            self.input.buffer.reset(append_to_history=True)
            self.run_command(text)
        elif self.pm_mode:
            self.submit_pm(text)
        elif self._pm_turn_refuses("this draft"):
            return   # W52 B1: never a chat or Task request beside a PM turn; draft kept.
        elif self.task_profile is not None:
            self.submit_task_draft(text)
        else:
            self.input.buffer.reset(append_to_history=True)
            self.start_send(text)

    def _pm_turn_refuses(self, what):
        """A PM turn and a chat/Task request never overlap, whatever the route (W52 B1)."""
        if not self.pm_turn_active:
            return False
        self.append("llmtool: a PM turn is in progress; " + what + " was not sent (input "
                    "kept) and is not queued for the PM. Send it when the turn ends, /pm "
                    "connect returns to PM mode (Enter queues there), Esc interrupts the turn.")
        return True

    def submit_task_draft(self, text):
        """Task mode Enter: one formal Core submit per distinct draft.

        The draft leaves the editor before the worker starts, and busy is set in the
        same key handler, so a repeated Enter, a resize or a redraw finds nothing to
        send. Resending the identical request needs the explicit /task submit command.
        """
        key = (self.task_profile, text.strip())
        if key == self.last_task_request:
            self.append("llmtool: this exact request was already submitted in Task mode as Task "
                        + (self.last_task_id or chat_tui_task.UNKNOWN) + "; not sent again. "
                        "Edit the draft, or use /task submit --provider " + self.task_profile
                        + " REQUEST to create another Task on purpose.")
            return
        self.input.buffer.reset(append_to_history=True)
        self.last_task_request = key
        self.last_task_id = None
        self.append("task> " + text.strip())
        self.start_task("submit", self.task_profile, text.strip(), from_mode=True)

    def run_command(self, text):
        self.append("> " + text.strip())
        if text.strip().split(maxsplit=1)[0] == "/pm":
            self.run_pm_command(text)
            return
        if text.strip().split(maxsplit=1)[0] == "/task":
            if self.dev or read_only_task_command(text):
                self.run_task_command(text)
            else:
                self.append("llmtool: " + TASK_HIDDEN)
            return
        if text.strip() == "/help":
            self.append(self.command_help_text())
            return
        if text.strip() == "/status":
            self.append(self.status_report())
            return
        if text.strip() == "/logs":
            if self.pm_runtime is None or not self.pm_target:
                self.append("PM 紀錄：尚未連線 PM。先 /pm connect。")
                return
            try:
                turns = self.pm_api.read_recent_turns(
                    self.pm_target[0], chat_tui_pm.LOGS_SHOWN)
                rows = chat_tui_pm.log_lines(turns)
            except Exception:
                self.append("PM 紀錄：UNKNOWN（PM 歷史無法讀取）")
                return
            if not rows:
                self.append("PM 紀錄：尚無回合")
                return
            self.append("PM 紀錄（最近 %d 回合）\n" % len(rows)
                        + "\n".join("  " + row for row in rows)
                        + "\n  不顯示工具指令與輸出")
            return
        if text.strip() == "/attention":
            self.append(self.attention_report())
            return
        if text.strip().split(maxsplit=1)[0] in ("/theme", "/links", "/settings"):
            self.run_display_command(text.strip())
            return
        try:
            output = self.session.command(text)
        except LLMToolError as exc:
            self.append("llmtool: " + str(exc))
            return
        if text.strip() == "/clear":
            # The pane mirrors the cleared in-memory conversation.
            self.view.clear_selection()
            self.conversation.set_text("")
        self.append(output)
        if not self.session.active:
            self.app.exit(result=0)

    def run_task_command(self, text):
        """Run an explicit TaskStore command; chat text never enters this route."""
        parts = text.strip().split(maxsplit=3)
        if len(parts) >= 2 and parts[1] == "submit":
            if (len(parts) != 4 or parts[2] != "--provider" or
                    not parts[3].strip()):
                self.append("usage: /task submit --provider PROFILE REQUEST")
                return
            # Keep the existing CLI's explicit provider and Task/Run creation path.
            # split(maxsplit=3) leaves the provider option and request together;
            # parse them without interpreting request text as shell syntax.
            option_parts = parts[3].split(maxsplit=1)
            if len(option_parts) != 2:
                self.append("usage: /task submit --provider PROFILE REQUEST")
                return
            provider, request = option_parts
            if not provider or not request.strip():
                self.append("usage: /task submit --provider PROFILE REQUEST")
                return
            if not self._pm_turn_refuses("/task submit"):
                self.start_task("submit", provider, request.strip())
            return
        if len(parts) == 3 and parts[1] == "show":
            self.start_task("show", parts[2], None)
            return
        if len(parts) == 2 and parts[1] == "list":
            self.toggle_list(True)
            return
        if len(parts) == 2 and parts[1] == "events":
            self.toggle_events(True)
            return
        words = text.split()
        if words[1:] == ["mode", "off"]:
            self.set_task_mode("off")
            return
        if len(words) == 4 and words[1:3] == ["mode", "profile"]:
            self.set_task_mode(words[3])   # W57: the word "profile" is never a profile name
            return
        self.append("usage: /task submit --provider PROFILE REQUEST\n"
                    "       /task show TASK_ID\n"
                    "       /task mode profile PROFILE\n"
                    "       /task mode off\n"
                    "       /task list\n"
                    "       /task events")

    def set_task_mode(self, profile):
        """Explicit chat/Task choice; the profile is checked by Core at submit time."""
        self.pm_mode = False  # A PM connection stays open; /pm connect returns to it.
        if profile == "off":
            self.task_profile = None
            self.last_task_request = None
            self.append("chat mode: Enter sends drafts to chat provider " + self.session.provider
                        + " (not a PM); chat replies are never Tasks")
            return
        self.task_profile = profile
        self.last_task_request = None
        self.append("Task mode: Enter submits each draft as a formal PM Task to PM provider "
                    "profile " + profile + " through llmtool Core (task submit). Nothing is sent "
                    "to the chat provider. The PM type, model and session are shown from the "
                    "Core record after each submit. /task mode off returns to chat.")

    def start_task(self, action, first, request, from_mode=False):
        self.busy = True
        self.worker_kind = "task"
        if action == "submit":
            self.append("llmtool: task intake only; Developer dispatch is not implied. "
                        "Submitting to PM provider profile " + first + "...")
        self._loop = asyncio.get_running_loop()
        self.worker = threading.Thread(target=self._task_request,
                                       args=(action, first, request, from_mode, self._loop),
                                       name="llmtool-chat-task")
        self.worker.start()

    def _task_request(self, action, first, request, from_mode, loop):
        """Worker thread: existing Core task submit/show with task-local captured output.

        After a submit that created a Task, the detail is read back from Core with
        task show, so the detail pane never reuses the submit output.
        """
        result = {"action": action, "first": first, "from_mode": from_mode, "code": None,
                  "view": None, "raw": "", "error": None, "detail": None}
        try:
            if action == "show" and not self.dev and task_store_absent():
                # #1819 F1: a read-only show never calls Core or TaskStore, which would
                # create the root; with no store there is no Task to show.
                result["error"] = TASK_NOT_FOUND % first
            else:
                self._core_task(action, first, request, result)
        except LLMToolError as exc:
            result["error"] = str(exc)
        except BaseException:
            result["error"] = "task command failed; details omitted"
        try:
            loop.call_soon_threadsafe(self.finish_task, result)
        except RuntimeError:
            pass

    def _core_task(self, action, first, request, result):
        """Worker thread: the existing Core task submit/show, filling ``result``."""
        from argparse import Namespace
        from .task_submission_cli import run_task_show, run_task_submit

        if action == "submit":
            with background_cli_invocation():
                result["code"], result["raw"] = self._core_call(
                    run_task_submit, Namespace(provider=first, request=[request]))
        else:
            result["code"], result["raw"] = self._core_call(
                run_task_show, Namespace(task_id=first))
        result["view"] = chat_tui_task.parse_core_view(result["raw"])
        if action == "show":
            if result["view"] is not None:
                result["detail"] = self._detail_text(result["view"])
        elif result["view"] is not None and result["code"] == 0:
            _code, raw = self._core_call(
                run_task_show, Namespace(task_id=result["view"]["task_id"]))
            shown = chat_tui_task.parse_core_view(raw)
            if shown is not None:
                result["detail"] = self._detail_text(shown)

    @staticmethod
    def _detail_text(view):
        """Worker thread: status summary from the stored record, then Core's task show view."""
        from .task_store import TaskStore
        try:
            record = TaskStore().get(view["task_id"])
        except Exception:
            summary = ("\n".join(chat_tui_status.task_owner_summary(None)) + "\n\n"
                       + "Status: " + chat_tui_task.UNKNOWN + " (TaskStore record unreadable)")
            extra = ""
        else:
            summary = ("\n".join(chat_tui_status.task_owner_summary(record)) + "\n\n"
                       + chat_tui_status.summary_text(record) + "\n\n"
                       + chat_tui_events.timeline_text(record))
            extra = chat_tui_status.detail_extra(record)
        text = summary + "\n\n" + chat_tui_task.detail_text(view)
        return text + ("\n" + extra if extra else "")

    @staticmethod
    def _core_call(function, args):
        import io
        output = io.StringIO()
        code = function(args, stdout=output)
        return code, output.getvalue().strip()

    def task_outcome_text(self, result):
        """Conversation text for a finished task command, built only from Core output."""
        view, error, raw = result["view"], result["error"], result["raw"]
        if result["action"] == "submit":
            text = chat_tui_task.receipt_text(result["code"], view, result["first"],
                                              raw=raw, error=error)
            if result["detail"] is not None:
                text += "\n  Task detail opened from Core (F2 toggles, Esc closes)."
            elif view is not None:
                text += "\n  Detail: /task show " + chat_tui_task.value(view, "task_id")
            return "task result:\n" + text
        if view is None:
            return ("llmtool task command failed:\n"
                    + (error or raw or "task command returned no result"))
        return ("task result:\nTask " + chat_tui_task.value(view, "task_id") + " state "
                + chat_tui_task.value(view, "state") + "; dispatch: "
                + chat_tui_task.dispatch_status(view)
                + "\n  Task detail opened from Core (F2 toggles, Esc closes).")

    def start_send(self, text):
        self.busy = True
        self.worker_kind = "chat"
        self.send_cancel = threading.Event()
        self.send_interrupted = False
        self.stream_seen = False
        self.append("you:\n" + text, segments=self._rule_segments() + [("you:", "role.owner"), ("\n" + text, "text")])
        self._loop = asyncio.get_running_loop()
        self.worker = threading.Thread(target=self._request,
                                       args=(text, self.send_cancel, self._loop),
                                       name="llmtool-chat-request")
        self.worker.start()

    def _request(self, text, cancel, loop):
        """Worker thread: one immutable captured input; ChatSession owns rollback.

        Streamed reply text is posted to the event loop chunk by chunk; the cancel
        event (Esc / first Ctrl-C) is read by ChatSession between chunks.
        """
        def on_text(chunk):
            self._post(loop, self.stream_text, chunk)
        try:
            with background_cli_invocation():
                outcome = (True, self.session.send(text, on_text=on_text, cancel=cancel))
        except LLMToolError as exc:
            outcome = (False, str(exc))
        except BaseException:
            outcome = (False, "chat request failed; history unchanged; retry or select another provider")
        try:
            loop.call_soon_threadsafe(self.finish_send, outcome)
        except RuntimeError:
            pass  # Loop already closed; state stays in ChatSession only.

    def finish_task(self, result):
        """Event-loop callback for a task command; only Core output reaches the screen."""
        def render():
            view = result["view"]
            if result["from_mode"] and view is not None:
                self.last_task_id = chat_tui_task.value(view, "task_id")
            self.append(self.task_outcome_text(result))
            if result["detail"] is not None:
                task_id = (chat_tui_task.value(view, "task_id") if view is not None else None)
                self.set_detail(result["detail"], task_id=task_id)
        self._finish(render)

    def stream_text(self, text):
        """Event-loop callback: show one streamed chunk of the chat reply in place."""
        if not (self.busy and self.worker_kind == "chat") or not text:
            return
        if self.stream_raw is None:
            self._stream_attach("")
        self.stream_raw += text
        self.stream_seen = True
        self._block_render(self.stream_start, *self._body(terminal_safe_text(self.stream_raw), ""))

    def _stream_attach(self, raw):
        """Open the chat reply block at the end of the pane with the text so far."""
        self.stream_head = len(self.conversation.text)
        self.append("assistant:", segments=[("assistant:", "role.pm")])
        self.stream_raw = raw
        self.stream_start = len(self.conversation.text) + 1   # after "\n"
        if raw:
            self._block_render(self.stream_start, *self._body(terminal_safe_text(raw), ""))

    def _stream_detach(self):
        """Take an open chat reply block off the pane end; append() puts it back
        below the new output, so an interrupt notice never splits the reply."""
        raw = self.stream_raw
        if raw is None:
            return None
        self.stream_raw = None
        self.conversation.set_text(self.conversation.text[:self.stream_head])
        return raw

    def _stream_finalize(self):
        """End the chat reply block where it is; its text stays as shown."""
        self.stream_raw = None

    def finish_send(self, outcome):
        """Runs as an event-loop callback, where an exception would bypass run()."""
        ok, text = outcome

        def render():
            streamed = self.stream_seen   # shown chunk by chunk: never printed again
            self._stream_finalize()
            if not ok:
                self.append("llmtool: " + (text or ""))
                return
            if not streamed:
                body, sources = self._body(terminal_safe_text(text or ""), "")
                self.append("assistant:\n", segments=[("assistant:", "role.pm"), ("\n", "text")] + body,
                            sources=[(low + len("assistant:\n"), high + len("assistant:\n"), *rest)
                                     for low, high, *rest in sources])
            if self.session.last_interrupted:
                self.append("llmtool: reply interrupted"
                            + (" after the text above; it stays in history as shown"
                               if streamed else
                               "; the provider answered in one piece, so the whole reply"
                               " above is kept in history"))
        self._finish(render)

    def _finish(self, render):
        self.busy = False
        self.worker = None
        self.worker_kind = None
        self.send_cancel = None
        self.send_interrupted = False
        if self._received_signal is not None:
            self.app.exit(result=0)
            return
        try:
            render()
        except Exception:
            self.app.exit(result=1)  # Sanitized FAILED; ChatSession history is already correct.
            return
        self.app.invalidate()
        if self.quit_pending:
            self.app.exit(result=0)
            return
        self._maybe_auto_resume()

    # -- real-time PM (pm_runtime only; never ChatSession) ---------------------------
    def pm_status_fields(self, compact):
        """PM-mode status fields: mode, background item, session, PM state and queue count."""
        session_id = self.pm_target[0][:8] if self.pm_target else chat_tui_task.UNKNOWN
        state = self.pm_state or chat_tui_task.UNKNOWN
        elapsed = (" %.1fs" % max(0.0, time.monotonic() - self.pm_turn_started)
                   if self.pm_turn_started is not None else "")
        if self.pm_warm and state in ("working", "idle"):
            # First turn, nothing received yet: cold start, not a slow PM.
            state = "暖機中…" + (elapsed if state == "working" else "")
        elif state in ("working", "thinking"):
            state = PM_STATE_LABELS[state] + elapsed
        elif state == "idle" and self.pm_last_status == "INTERRUPTED":
            state = "已中斷"
        elif state.startswith("error("):
            state = "錯誤 " + state[len("error("):-1]
        else:
            state = PM_STATE_LABELS.get(state, state)
        # Keep the PM mode and permission state in the lead; runtime/account can drop first.
        binding = self.pm_binding if type(self.pm_binding) is dict else {}
        runtime = self._pm_runtime_name()
        if runtime == 'claude-code':
            runtime = 'claude'
        account = binding.get('account') or 'default'
        lead = ["▸▸ " + PM_LABEL + " 模式", (chat_tui_pm.permission_mode() if runtime == 'claude' else 'workspace-write')]
        count = self.background_count()
        if count is not None:
            lead.append("背景 %s" % count)
            if count and not self.input.text:
                lead.append("↓ 查看")
        if self.pm_outbox:
            lead.append("佇列 %d" % len(self.pm_outbox) + (" 暫停" if self.pm_outbox_paused else ""))
        fields = {"mode": " · ".join(lead),
                "identity": compact(chat_tui_pm.safe_text(runtime) + "/"
                                     + chat_tui_pm.safe_text(account), 20),
                "session": compact(session_id, 8),
                "state": compact(state, 18)}
        attention_count = len(self.attention_lines())
        if attention_count:
            fields["attention"] = "注意 %d" % attention_count
        if type(count) is int and count > 0:
            items = self.background_all()
            if items:
                fields["background_item"] = chat_tui_status.background_summary(items[0])
        return fields

    # -- PM background work (W56d) ---------------------------------------------------
    def background_all(self):
        """Listed background work of the connected PM: Task items, then #665 turns."""
        if not self.pm_target or self.background_key != self.pm_target[0]:
            return list(self.pm_background_turns())
        return self.background + list(self.pm_background_turns())

    def background_count(self):
        """Items in flight; "?" after a failed read; None before the first read."""
        if not self.pm_target or self.background_key != self.pm_target[0]:
            return None
        if self.background_error is not None:
            return "?"
        return len(self.background_all())

    def _background_tick(self):
        key = self.pm_target[0] if self.pm_target else None
        if key is None or self.background_reader is not None:
            return
        if (key == self.background_key and self.background_loaded is not None and
                time.monotonic() - self.background_loaded < chat_tui_status.REFRESH_SECONDS):
            return
        self._loop = asyncio.get_running_loop()
        self.background_reader = threading.Thread(
            target=self._read_background, args=(self._loop, key),
            name="llmtool-chat-pm-background", daemon=True)
        self.background_reader.start()

    def _read_background(self, loop, key):
        try:
            outcome = (chat_tui_status.read_background(key), None)
        except LLMToolError as exc:
            outcome = ([], str(exc))
        except Exception:
            outcome = ([], "TaskStore read failed; details omitted")
        try:
            attention = chat_tui_status.read_attention(key)
        except Exception:
            attention = None
        try:
            loop.call_soon_threadsafe(self.finish_attention, key, attention)
            loop.call_soon_threadsafe(self.finish_background, key, outcome)
        except RuntimeError:
            pass

    def finish_attention(self, key, value):
        self.attention = value
        self.attention_key = key
        self.attention_read_at = time.strftime("%H:%M:%S")
        self.app.invalidate()

    def attention_lines(self):
        """Current connected PM attention facts, including local queue/runtime signals."""
        if not self.pm_runtime or not self.pm_target:
            return []
        lines = []
        partial = False
        snapshot = (self.attention if self.attention_key == self.pm_target[0]
                    and type(self.attention) is dict else None)
        if snapshot is not None:
            task_bad = snapshot.get("task_unreadable_count", 0)
            inbox_bad = snapshot.get("inbox_unreadable_count", 0)
            partial = task_bad > 0 or inbox_bad > 0
            if partial:
                lines.append(_attention_text(
                    "部分讀取失敗：Tasks %s；PM inbox %s（計數可能重複；注意事項與未讀事件總數未知）"
                    % (task_bad, inbox_bad)))
            for item in snapshot.get("items", []):
                if type(item) is not dict:
                    continue
                text = "%s｜%s" % (item.get("label", ""), str(item.get("task", ""))[:8])
                if item.get("reason"):
                    text += "｜" + str(item["reason"])
                lines.append(_attention_text(text))
            unread = snapshot.get("unread", 0)
            if unread > 0:
                lines.append(_attention_text(
                    "PM 至少有 %s 則未讀事件（總數未知）" % unread if partial
                    else "PM 有 %s 則未讀事件" % unread))
        if self.pm_outbox and self.pm_outbox_paused:
            lines.append(_attention_text("PM 佇列暫停：" + str(self.pm_outbox_paused)))
        if type(self.pm_state) is str and self.pm_state.startswith("error("):
            lines.append(_attention_text("最近的 PM 回合出錯"))
        return lines

    def attention_report(self):
        """Read-only /attention report for this connected PM session."""
        if not self.pm_runtime or not self.pm_target:
            return "注意事項：尚未連線 PM。先 /pm connect。"
        if self.attention_key != self.pm_target[0]:
            return "注意事項：讀取中，稍後再試。"
        snapshot = (self.attention if self.attention_key == self.pm_target[0]
                    and type(self.attention) is dict else None)
        if snapshot is None:
            heading = "注意事項：UNKNOWN（TaskStore 無法讀取）"
            facts = self.attention_lines()
        else:
            partial = (snapshot.get("task_unreadable_count", 0) > 0 or
                       snapshot.get("inbox_unreadable_count", 0) > 0)
            count = len(self.attention_lines())
            heading = ("注意事項：UNKNOWN（TaskStore 部分無法讀取）" if partial
                       else "注意事項（%d 項）" % count)
            facts = self.attention_lines()
        rows = [heading, "  來源：TaskStore（本機）｜讀取於 " +
                (self.attention_read_at or time.strftime("%H:%M:%S"))]
        shown = facts[:20]
        rows.extend("  " + _attention_text(line) for line in shown)
        if len(facts) > 20:
            rows.append("  另有 %d 項未顯示" % (len(facts) - 20))
        elif not facts and snapshot is not None and not partial:
            rows.append("  目前沒有")
        rows.append("  需要你決定：無法提供（Core 尚未記錄）")
        return "\n".join(rows)

    def finish_background(self, key, outcome):
        self.background_reader = None
        self.background, self.background_error = outcome
        self.background_key = key
        self.background_loaded = time.monotonic()
        # W59: a refresh changes the data, never whether the Owner's panel is open.
        self.app.invalidate()

    def background_loading(self):
        """W60: the connected PM's first background read has not finished yet."""
        key = self.pm_target[0] if self.pm_target else None
        return key is not None and (self.background_key != key or self.background_loaded is None)

    def background_open(self):
        """The Owner opened the ↓ panel and has not closed it (keys follow this)."""
        return self.show_background and self.pm_mode

    def background_visible(self):
        """The panel has rows on screen (layout only)."""
        return self.background_open() and self.background_rows() > 0

    def _close_background_on_text(self):
        if self.input.text:
            self.show_background = False

    def background_rows(self):
        """Panel rows (header + items) that fit beside the minimum chat layout."""
        input_rows = min(4, max(1, self.input.buffer.document.line_count))
        free = (self.app.output.get_size().rows - 3 - input_rows - self.busy_rows()
                - sum(self.picker_layout()))
        items = self.background_all()
        body = min(len(items) + (1 if self._background_note(items) else 0), BACKGROUND_LIMIT)
        return max(0, min(1 + body, free))

    def _background_note(self, items):
        """Placeholder row text when the selection is not a listed row, else None."""
        if not self.show_background:
            return None
        if self.background_selected is None:
            # Listed rows with no selection need no extra row (↑↓ select one; Enter says
            # UNKNOWN); an empty list still shows why nothing is listed.
            return None if items else "  背景清單目前是空的 — Enter 不會開任何 Task；Esc 關閉"
        if self._selected_index(items) is None:
            return ("  選中的 Task %s 已不在清單 — Enter 不會開其他列；↑↓ 重新選擇" % str(
                self.background_selected[1] or chat_tui_task.UNKNOWN)[:8])
        return None

    def background_text(self):
        width = max(1, self.app.output.get_size().columns)
        cut = lambda text: fit_width(terminal_safe_text(text).replace("\n", " "), width,
                                     self._get_cwidth)
        items = self.background_all()
        rows = self.background_rows()
        header = " 背景工作 %d 項（TaskStore，每 %d 秒更新）— Esc 關閉 · ↑↓ 選擇 · Enter 詳情" % (
            len(items), chat_tui_status.REFRESH_SECONDS)
        if self.background_error is not None:
            header += " — 讀取失敗：" + self.background_error
        result = [("class:menu.header", cut(header))]
        selected = self._selected_index(items)   # drawing never changes the selection
        shown = max(0, rows - 1)
        note = self._background_note(items)
        if note is not None and shown:
            # Its own row, so a narrow header never cuts it; nothing else is highlighted.
            result += [("", "\n"), ("class:menu.header", cut(note))]
            shown -= 1
        first = 0 if selected is None else max(0, selected - shown + 1)   # selected in view
        for index in range(first, min(len(items), first + shown)):
            item = items[index]
            task = item.get("task") or chat_tui_task.UNKNOWN
            line = "  %-9s %-8s %-8s %-26s %s" % (
                item.get("role", ""), task[:8], item.get("pr", ""), item.get("state", ""),
                chat_tui_status.age_text(item.get("updated_at")))
            style = "class:menu.current" if index == selected else "class:menu"
            result += [("", "\n"), (style, cut(line))]
        return result

    # -- W59: read-only Task detail from the ↓ panel (TaskStore read, never busy) ------
    def _selected_index(self, items):
        """Row of the selected identity in ``items``, or None when it is not listed."""
        for index, item in enumerate(items):
            if _row_identity(item) == self.background_selected:
                return index
        return None

    def move_background(self, direction):
        """Up/Down: the only change of the selection after the panel opened."""
        items = self.background_all()
        if not items:
            return
        current = self._selected_index(items)
        if current is None:
            # The selected row left the list: Down takes the row now at its place, Up the
            # one above it.
            target = self.background_index + (0 if direction > 0 else -1)
        else:
            target = current + direction
        self.background_index = max(0, min(len(items) - 1, target))
        self.background_selected = _row_identity(items[self.background_index])

    def task_view_visible(self):
        """The Task detail is on screen (help and the developer panes cover it)."""
        return self.show_task_view and not (self.show_help or self.show_detail or self.show_list
                                            or self.show_events)

    def task_view_linker(self):
        """Links of the detail: the pane's own (#123, path:line), plus full PR URLs."""
        if not self.links_on():
            return None
        base = self.linker
        if self._task_view_linker[1] is None or self._task_view_linker[0] is not base:
            self._task_view_linker = (base, _TaskViewLinker(base))
        return self._task_view_linker[1]

    def _set_task_view(self, text):
        view = self.task_view
        safe_text = terminal_safe_text(text)
        view.set_text(safe_text)
        if safe_text.startswith("Task 詳情（唯讀）—"):
            start = safe_text.find("\n") + 1
            if safe_text.startswith("狀態：", start):
                view._clip_spans(0)
                end = start
                for _ in range(3):
                    newline = safe_text.find("\n", end)
                    end = len(safe_text) if newline < 0 else newline + 1
                view._add_segments(start, [(safe_text[start:end], "role.pm")])
        chat_tui_view.add_pr_url_styles(view, chat_tui_status.pr_url())
        view.top = view.top_offset = 0          # a detail is read from its first line
        view.following = view.unseen = False

    def open_background_detail(self):
        """Enter on the panel: show the selected row's detail, read on a reader thread."""
        items = self.background_all()
        selected = self.background_selected
        # Refreshes may shift, drop or empty rows: open only the selected row. Without one,
        # Enter still belongs to the panel: it says why and never reads another Task, sends
        # the draft or resumes the queue.
        index = None if selected is None else self._selected_index(items)
        if index is None:
            if selected is None:
                reason = "沒有選中的列；沒有開啟任何 Task，按 Esc 返回後用 ↓ 選擇"
            else:
                reason = ("選中的 Task " + str(selected[1] or chat_tui_task.UNKNOWN)[:8]
                          + " 已不在背景清單（清單剛更新）；沒有開啟其他列，按 Esc 返回再選一次")
            if self.background_error is not None:
                reason += "；背景清單讀取失敗：" + str(self.background_error)
            self._show_task_view(chat_tui_status.task_detail_text(
                None, time.strftime("%H:%M:%S"), reason))
            return
        tasks = (len(self.background) if self.pm_target and
                 self.background_key == self.pm_target[0] else 0)
        task_id = str(items[index].get("task") or "")
        now = time.strftime("%H:%M:%S")
        if index >= tasks:
            self._show_task_view(chat_tui_status.task_detail_text(None, now, TURN_ROW_REASON))
        elif not TASK_ID.fullmatch(task_id):
            self._show_task_view(chat_tui_status.task_detail_text(
                None, now, "背景清單的 Task ID 無法辨識"))
        else:
            self._show_task_view("Task 詳情（唯讀）— Esc 返回對話" + chat_tui_status.COPY_HINT
                                 + chat_tui_status.COPY_ALL_HINT + "\n讀取中…（TaskStore，本機）Task " + task_id, task_id)
            self._loop = asyncio.get_running_loop()
            threading.Thread(target=self._read_task_view,
                             args=(self._loop, self.task_view_seq, task_id),
                             name="llmtool-chat-task-detail", daemon=True).start()

    def _show_task_view(self, text, task_id=None):
        """Replace the conversation by the detail; its reading position and the draft stay.

        ``task_id`` is the validated Task the detail reads (what c copies); a refresh of the
        ↓ panel never changes it.
        """
        self.task_view_task_id = task_id
        self.task_copy_at = None
        self.task_copy_label = None
        self.task_view.copied = None   # a new detail starts without the last copy's status
        self.show_background = False
        self._leave_conversation()
        self.task_view_seq += 1
        self.show_task_view = True
        self._set_task_view(text)
        # W60: the detail has focus, so c/C/↑↓ act on it; Tab goes to the draft (kept).
        self.app.layout.focus(self.task_pane)
        self._sync_picker()
        self.app.invalidate()

    def _read_task_view(self, loop, seq, task_id):
        try:
            record = chat_tui_status.read_task_detail(task_id)
            text = chat_tui_status.task_detail_text(record, time.strftime("%H:%M:%S"),
                                                    copy_hint=True)
        except Exception:
            text = chat_tui_status.task_detail_text(
                None, time.strftime("%H:%M:%S"), "TaskStore 讀取失敗（找不到或無法讀取）；細節省略",
                copy_hint=True)
        self._post(loop, self.finish_task_view, seq, text)

    def finish_task_view(self, seq, text):
        if seq != self.task_view_seq or not self.show_task_view:
            return   # closed, or replaced by a newer open
        self._set_task_view(text)
        self.app.invalidate()

    def copy_task_id(self):
        """W60 c: the open detail's Task ID to the clipboard (the pane's clipboard worker)."""
        self.task_copy_at = time.monotonic()
        self.task_copy_label = "Task ID"
        if self.task_view_task_id:
            self.task_view.copy(self.task_view_task_id)
        self.app.invalidate()

    def copy_dev_task_id(self):
        """W62 c: copy the selected Task ID in the F2 detail or F3 list/detail pane."""
        view = self._mouse_view()
        if view is self.list_view:
            task_id = self.selected_task_id()
        elif view is self.detail_view:
            task_id = getattr(self, "detail_task_id", None)
        else:
            return
        if not task_id or task_id in (chat_tui_task.UNKNOWN, chat_tui_task.NOT_AVAILABLE):
            return
        view.clear_selection()
        self.copy_labels[id(view)] = "Task ID"
        view.copy(task_id)
        self.app.invalidate()

    def copy_task_detail(self):
        """W60 C: the whole detail (every line, not only the screen) as the plain text shown.

        The detail text was made terminal-safe when it was set, so the copy carries no live
        escape sequence; links are drawn around the text and are not part of it.
        """
        view = self.task_view
        self.task_copy_at = time.monotonic()
        self.task_copy_label = "全文"
        view.clear_selection()
        view.copy_range(0, len(view.text))
        self.app.invalidate()

    def _task_copy_status(self):
        """Status text of the detail's last copy while it is recent, else None."""
        now = time.monotonic()
        if (self.task_copy_at is not None and self.task_copy_label == "Task ID"
                and not self.task_view_task_id):
            return ("沒有 Task ID 可複製"
                    if now - self.task_copy_at < chat_tui_view.COPIED_SECONDS else None)
        return self._pane_copy_status(self.task_view, self.task_copy_label)

    def _pane_copy_status(self, view, label):
        """A read pane's copy result while it is recent (or a selection), else None."""
        # The worker stamps ``copied`` when pbcopy returns, so a slow copy still reports.
        finished = (view.copied is not None
                    and time.monotonic() - view.copied[1] < chat_tui_view.COPIED_SECONDS)
        if not (finished or view.copy_pending() or view.selected_range()):
            return None
        return (label + " " if label and not view.selected_range() else "") + self._copy_hint(
            view.status())

    def _read_view(self):
        """A read-only ConversationView for a developer pane (starts at the top, no follow)."""
        view = chat_tui_view.ConversationView(self._get_cwidth)
        view.following = False
        return view

    def _read_views(self):
        return (self.view, self.task_view, self.detail_view, self.list_view, self.events_view)

    def _forget_copy_label(self, view):
        if view is self.task_view:
            self.task_copy_label = None
        self.copy_labels.pop(id(view), None)

    def _set_read_text(self, view, text, keep=True, highlight_lines=0):
        """Replace a developer pane's text (already terminal-safe); ``keep`` the place."""
        top_offset = view.top_offset
        view.set_tail(0, [(text, "text")])
        if highlight_lines:
            end = 0
            for _ in range(highlight_lines):
                newline = text.find("\n", end)
                end = len(text) if newline < 0 else newline + 1
            view._add_segments(0, [(text[:end], "role.pm")])
        if view is self.detail_view:
            chat_tui_view.add_pr_url_styles(view, chat_tui_status.pr_url())
        if not keep:
            view.top = view.top_offset = 0
        else:
            view.top_offset = min(top_offset, len(text))
        view.following = view.unseen = False

    def copy_pane_all(self):
        """W60 C in F2/F3/F4: the whole pane (for F3 every listed Task, not only the rows
        shown) as plain terminal-safe text, through the pane's clipboard worker."""
        view = self._mouse_view()
        if view not in (self.detail_view, self.list_view, self.events_view):
            return
        text = self.list_plain_text() if view is self.list_view else view.text
        view.clear_selection()
        self.copy_labels[id(view)] = "全文"
        view.copy(text)
        self.app.invalidate()

    def hint_no_panel(self):
        """W60: Up/Down with nothing to open say what to do instead (one line, not repeated)."""
        if self.pm_mode and self.background_loading():
            hint = "llmtool: 背景 Task 讀取中…（TaskStore，本機）；讀完後 ↓ 可看"
        elif self.pm_mode:
            hint = "llmtool: 沒有背景 Task；PM 派工後 ↓ 可看"
        else:
            hint = "llmtool: 沒有 Task 面板；/pm connect 連 PM 後 ↓ 看背景 Task"
        hint += "；F3 看 Task 清單"
        if not self.conversation.text.rstrip("\n").endswith(hint):
            self.append(hint)

    def close_task_view(self):
        """Esc: back to the chat; its reading position and the draft were never touched."""
        self.task_view_task_id = None
        self.task_copy_at = None
        self.task_copy_label = None
        self._leave_conversation()   # ends a drag in the detail and its selection
        self.show_task_view = False
        self.task_view_seq += 1
        self.app.layout.focus(self.input)
        self._sync_picker()
        self.app.invalidate()

    @staticmethod
    def pm_ghost_hint(text, width=None, get_cwidth=None):
        """Display-only completion for the /pm and /settings commands accepted by the TUI."""
        hints = {
            "/pm ": "new | list [--all] | use N|SESSION | accounts [--check] | "
                    "connect [SESSION [CWD]] | disconnect | history | queue",
            "/pm new ": "[--runtime claude|codex] [--account NAME] [CWD]",
            "/pm accounts ": "[--check]",
            "/pm use ": "N|SESSION",
            "/pm connect ": "[SESSION [CWD]]",
            "/settings": " auto-resume on|off",
            "/settings ": "auto-resume on|off",
            "/settings auto-resume": " on|off",
            "/settings auto-resume ": "on|off",
        }
        hint = hints.get(text, "")
        if width is not None and get_cwidth is not None:
            return fit_width(hint, width, get_cwidth)
        return hint

    def run_pm_command(self, text):
        # Shell-style words, so a CWD with spaces can be quoted or escaped (W52 B3).
        try:
            parts = shlex.split(text)
        except ValueError:
            self.append("llmtool: /pm: unbalanced quote or trailing backslash; quote a path "
                        "with spaces as \"/path/with space\" or escape it as /path/with\\ space\n"
                        + PM_USAGE)
            return
        if parts == ["/pm"]:
            if self.pm_runtime is None:
                binding = self.pm_api.current_binding()
                if binding:
                    self.append("PM: not connected. selected "
                                + chat_tui_pm.safe_text(binding.get('runtime', 'claude-code'))
                                + " account " + chat_tui_pm.safe_text(binding.get('account') or 'default')
                                + "; /pm connect to connect.")
                else:
                    self.append("PM: not connected. /pm connect uses the saved dedicated PM binding; "
                                "/pm new creates one.")
            else:
                binding = self.pm_binding if type(self.pm_binding) is dict else {}
                self._append_account(
                    "PM: " + chat_tui_pm.safe_text(binding.get('runtime', 'claude-code'))
                    + " session " + self.pm_target[0] + " (cwd "
                    + chat_tui_pm.safe_text(str(self.pm_target[1])) + "), "
                    + chat_tui_pm.binding_text(self.pm_binding) + ", ",
                    ", state " + (self.pm_state or chat_tui_task.UNKNOWN)
                    + ("; PM mode" if self.pm_mode else "; not in PM mode"))
            return
        if parts[1:2] == ["list"]:
            if parts[2:] not in ([], ["--all"]):
                if parts[2].startswith("-"):
                    self.append("unknown option " + parts[2] + "\n" +
                                _pm_subcommand_usage("list"))
                else:
                    self.append(_pm_subcommand_usage("list"))
                return
            try:
                bindings = _pm_newest_first(self.pm_api.list_bindings())
                current = self.pm_api.current_binding()
            except Exception:
                self.append("llmtool: PM sessions unavailable")
                return
            if not bindings:
                self.append("PM sessions: none")
                return
            current_id = current.get('native_session_id') if type(current) is dict else None
            all_rows = parts[2:] == ["--all"]
            shown_bindings = bindings if all_rows else bindings[:10]
            try:
                repo_root, _slug = chat_tui_theme.repo_info()
            except Exception:
                repo_root = None
            repo_root = os.path.realpath(repo_root) if repo_root else None
            lines = ["PM sessions:"]
            for number, binding in enumerate(shown_bindings, 1):
                cwd = binding.get('cwd')
                shown_cwd = str(cwd) if cwd is not None else "unknown"
                if repo_root and isinstance(cwd, str) and os.path.isabs(cwd):
                    try:
                        resolved_cwd = os.path.realpath(cwd)
                        if os.path.commonpath((repo_root, resolved_cwd)) == repo_root:
                            shown_cwd = os.path.relpath(resolved_cwd, repo_root) or "."
                    except (OSError, ValueError):
                        pass
                session_id = chat_tui_pm.safe_text(binding.get('native_session_id'))
                lines.append("  %d. %s · account %s · %s · %s · cwd %s%s" % (
                    number, chat_tui_pm.safe_text(binding.get('runtime', 'claude-code')),
                    chat_tui_pm.safe_text(binding.get('account') or 'default'),
                    session_id[:8], _pm_relative_age(binding),
                    chat_tui_pm.safe_text(shown_cwd),
                    " · current" if binding.get('native_session_id') == current_id else ""))
            if not all_rows and len(bindings) > 10:
                lines.append("  … %d more (/pm list --all)" % (len(bindings) - 10))
            self.append("\n".join(lines))
            return
        if parts[1:2] == ["use"]:
            unknown = next((part for part in parts[2:] if part.startswith("-")), None)
            if unknown is not None:
                self.append("unknown option " + unknown + "\n" +
                            _pm_subcommand_usage("use"))
                return
            if len(parts) != 3:
                self.append(_pm_subcommand_usage("use"))
                return
            try:
                bindings = _pm_newest_first(self.pm_api.list_bindings())
                # Short decimal tokens are list positions. An eight-character
                # decimal token can also be a session ID prefix, so keep it in
                # the prefix lookup below.
                number = (int(parts[2]) - 1
                          if parts[2].isdigit() and len(parts[2]) < 8 else None)
                if number is not None:
                    session_id = (bindings[number]['native_session_id']
                                  if 0 <= number < len(bindings) else None)
                else:
                    matches = [row for row in bindings
                               if isinstance(row.get('native_session_id'), str) and
                               row['native_session_id'].startswith(parts[2])]
                    session_id = (matches[0]['native_session_id']
                                  if len(parts[2]) >= 8 and len(matches) == 1 else
                                  parts[2] if any(row.get('native_session_id') == parts[2]
                                                  for row in bindings) else None)
                if session_id is None:
                    raise ValueError()
                binding = self.pm_api.select_binding(session_id)
            except Exception:
                self.append("llmtool: PM session not selected; use /pm list and /pm use N|SESSION")
                return
            self.append("PM selected: " + chat_tui_pm.safe_text(binding.get('runtime', 'claude-code'))
                        + " account " + chat_tui_pm.safe_text(binding.get('account') or 'default')
                        + " · " + chat_tui_pm.safe_text(binding.get('native_session_id'))[:8]
                        + "; /pm connect to connect")
            return
        if parts[1:2] == ["accounts"]:
            if parts[2:] not in ([], ["--check"]):
                if parts[2] != "--check" and parts[2].startswith("-"):
                    self.append("unknown option " + parts[2] + "\n" +
                                _pm_subcommand_usage("accounts"))
                else:
                    self.append(_pm_subcommand_usage("accounts"))
                return
            try:
                if parts[2:] == ["--check"]:
                    accounts = self.pm_api.accounts()
                    checks = self.pm_api.account_checks()
                    if not accounts:
                        self.append(PM_ACCOUNTS_GUIDANCE)
                    else:
                        self.append("\n".join(
                            "%s: logged in %s" % (chat_tui_pm.safe_text(account['name']),
                                                  "yes" if checks[index] else "no")
                            for index, account in enumerate(accounts)))
                else:
                    accounts = self.pm_api.accounts()
                    lines = ["  %s · %s · folder %s" % (
                        chat_tui_pm.safe_text(row['name']), chat_tui_pm.safe_text(row['kind']),
                        chat_tui_pm.safe_text(row['status'])) for row in accounts]
                    self.append("PM accounts:\n" + ("\n".join(lines) if lines else
                                                        PM_ACCOUNTS_GUIDANCE))
            except Exception:
                self.append("llmtool: PM accounts unavailable")
            return
        if parts[1:2] == ["queue"] and len(parts) <= 3:
            self.run_queue_command(parts[2] if len(parts) == 3 else None)
            return
        if self.pm_pending:
            self.append("llmtool: a PM connect/disconnect/history request is in progress")
            return
        if parts[1:2] == ["connect"] and len(parts) <= 4:
            session_id = parts[2] if len(parts) >= 3 else None
            if self.pm_runtime is not None:
                if session_id in (None, self.pm_target[0]):
                    self.pm_mode, self.task_profile = True, None
                    self.append("PM mode: Enter sends to PM session " + self.pm_target[0])
                else:
                    self.append("llmtool: already connected to PM session " + self.pm_target[0]
                                + "; /pm disconnect first")
                return
            self.pm_state = "connecting"
            self._pm_worker(self._pm_connect, session_id, parts[3] if len(parts) == 4 else None)
            return
        if parts[1:2] == ["new"]:
            runtime, account, cwd = "claude-code", None, None
            try:
                index = 2
                while index < len(parts):
                    if parts[index] == "--runtime":
                        if index + 1 >= len(parts):
                            raise ValueError()
                        value = parts[index + 1]
                        if value not in ("claude", "codex"):
                            raise ValueError()
                        runtime = "claude-code" if value == "claude" else "codex"
                        index += 2
                    elif parts[index] == "--account":
                        if index + 1 >= len(parts):
                            raise ValueError()
                        account = parts[index + 1]
                        index += 2
                    elif parts[index].startswith("-"):
                        self.append("unknown option " + parts[index] + "\n" +
                                    _pm_subcommand_usage("new"))
                        return
                    elif cwd is None:
                        cwd = parts[index]
                        index += 1
                    else:
                        raise ValueError()
            except ValueError:
                self.append(_pm_subcommand_usage("new"))
                return
            if self.pm_runtime is not None:
                self.append("llmtool: connected to PM session " + self.pm_target[0]
                            + "; /pm disconnect before creating another")
                return
            self._pm_worker(self._pm_new, cwd or os.getcwd(), runtime, account)
            return
        if parts == ["/pm", "disconnect"]:
            if self.pm_runtime is None:
                self.append("PM: not connected")
                return
            self._auto_resume_pending = False
            self.pm_state = "closing"
            self._pm_busy_stop()   # never redrawn once disconnecting
            self._pm_worker(self._pm_disconnect, self.pm_runtime)
            return
        if parts == ["/pm", "history"]:
            if self.pm_runtime is None:
                self.append("PM: not connected")
                return
            self._pm_worker(self._pm_history, self.pm_target[0], self.pm_runtime)
            return
        self.append(PM_USAGE)

    # -- PM queue (W52 F2) ---------------------------------------------------------------
    def run_queue_command(self, action):
        if action is None:
            self.append(self._queue_text("PM 佇列"))
        elif action == "clear":
            count = len(self.pm_outbox)
            self.pm_outbox.clear()
            self.pm_outbox_paused = None
            self.append("llmtool: PM 佇列已清除 %d 則（未送出）" % count)
        elif action == "send":
            self._pm_resume(everything=True)
        else:
            self.append("usage: /pm queue [clear|send]")

    def _pm_resume(self, everything):
        """/pm queue send resumes the whole queue; Enter on an empty draft (W55) sends only
        the next queued draft and the rest stays paused."""
        if self.pm_outbox:
            self._auto_resume_pending = False
        again = "/pm queue send" if everything else "Enter"
        if not self.pm_outbox:
            self.append("llmtool: PM 佇列是空的")
        elif self.pm_runtime is None:
            self.append("llmtool: PM 未連線；/pm connect 之後再 %s（佇列保留 %d 則）"
                        % (again, len(self.pm_outbox)))
        elif self.pm_pending or self.pm_state == "closing":
            # finish_pm_connect/history/disconnect never send: resuming now would stall.
            self.append("llmtool: a PM connect/disconnect/history request is in progress; "
                        "%s again when it ends（佇列保留 %d 則）" % (again, len(self.pm_outbox)))
        elif not everything and self.pm_turn_active:
            self._pm_notice("llmtool: PM 本輪尚未結束；結束後按 Enter 送下一則（佇列暫停，剩 %d 則）"
                            % len(self.pm_outbox))
        elif self.pm_turn_active:
            self.pm_outbox_paused = None
            self._pm_notice("llmtool: PM 佇列繼續：目前回合結束後送出下一則（共 %d 則）"
                            % len(self.pm_outbox))
        elif everything:
            self.pm_outbox_paused = None
            self._pm_send_next(explicit=True)
        else:
            text = self.pm_outbox.popleft()   # still paused: the rest waits for the next Enter
            self._pm_start_turn(text, "owner → PM（自佇列送出，佇列暫停，剩 %d 則）:\n"
                                % len(self.pm_outbox))

    def _queue_text(self, title):
        if not self.pm_outbox:
            return title + "：空"
        lines = [title + "：%d 則%s" % (len(self.pm_outbox), "，暫停（" + self.pm_outbox_paused
                                       + "）" if self.pm_outbox_paused else "")]
        for number, text in enumerate(self.pm_outbox, 1):
            shown = chat_tui_pm.safe_text(text).strip().replace("\t", " ")
            first = shown.split("\n")[0] + (" …" if "\n" in shown else "")
            lines.append("  %d. " % number + fit_width(first, QUEUE_PREVIEW_COLUMNS,
                                                       self._get_cwidth))
        return "\n".join(lines)

    def _pm_pause(self, reason, text=None):
        """Stop sending queued messages; they stay listed and /pm queue send resumes."""
        if not self.pm_outbox or self.pm_outbox_paused:
            return
        self.pm_outbox_paused = reason
        self._pm_notice(text or (self._queue_text("llmtool: PM 佇列暫停，沒有送出")
                                 + "\n/pm queue send 繼續送出；/pm queue clear 清除"))

    def _pm_esc_pause(self):
        self._pm_pause("Esc 中斷", "llmtool: 已中斷；佇列已暫停（剩 %d 則）：Enter 送下一則，"
                       "/pm queue send 全部送出，/pm queue clear 清空" % len(self.pm_outbox))

    def _pm_notice(self, text):
        """W55 F2-b: a notice while a PM block streams goes below that block (the tail), so
        the reply stays one contiguous block; otherwise it is appended as usual."""
        if self.pm_block_raw is None:
            self.append(text)
            return
        self._pm_flush()   # the text received so far is shown above the notice
        self.pm_tail.append(terminal_safe_text(text))
        self._pm_render(final=False)

    def _pm_send_next(self, explicit=False):
        if (not self.pm_outbox or self.pm_outbox_paused or self.pm_turn_active
                or self._pm_background_waiting):
            return   # a waiting background turn goes first; its end sends the next draft
        if self.pm_runtime is None or self.pm_pending or self.pm_state == "closing":
            self._pm_pause("PM 未連線")
            return
        blocked = getattr(self.pm_runtime, "automatic_sends_blocked", None)
        if not explicit and callable(blocked) and blocked():
            # A lifecycle frame contradicted a receipt pm_runtime had already latched.
            self._pm_pause("PM 協定異常", "llmtool: PM 回報的訊息狀態前後矛盾，自動送出已停止；"
                           "/pm queue send 由你決定是否送出")
            return
        text = self.pm_outbox.popleft()
        self._pm_start_turn(text, "owner → PM（自佇列送出，剩 %d 則）:\n" % len(self.pm_outbox))

    def unsent_queue_text(self):
        """What run_chat_tui prints after exit when queued messages were never sent."""
        if not self.pm_outbox:
            return None
        return self._queue_text("llmtool: 離開時 PM 佇列仍有未送出的訊息（沒有送出）")

    def _pm_new(self, cwd, runtime, account, loop):
        try:
            outcome = {"binding": self.pm_api.create_dedicated(cwd, runtime, account)}
        except LLMToolError as exc:
            outcome = {"error": chat_tui_pm.error_code(exc)}
        except Exception:
            outcome = {"error": chat_tui_task.UNKNOWN}
        self._post(loop, self.finish_pm_new, outcome)

    def finish_pm_new(self, outcome):
        self.pm_pending = False
        if "error" in outcome:
            self.append("llmtool: dedicated PM session not created. "
                        + chat_tui_pm.error_text(outcome["error"]))
            return
        binding = outcome["binding"] if type(outcome["binding"]) is dict else {}
        self.append("Dedicated PM session " + chat_tui_pm.safe_text(binding.get("native_session_id"))
                    + " dedicated binding created (cwd " + chat_tui_pm.safe_text(binding.get("cwd"))
                    + "); the session starts on the first message. /pm connect connects to it.")

    def _pm_worker(self, target, *args):
        self.pm_pending = True
        self._loop = asyncio.get_running_loop()
        threading.Thread(target=target, args=(*args, self._loop),
                         name="llmtool-chat-pm", daemon=True).start()

    def _post(self, loop, callback, *args):
        try:
            loop.call_soon_threadsafe(callback, *args)
            return True
        except RuntimeError:
            return False  # Loop closed: the TUI has exited.

    def _pm_read_history(self, session_id, _runtime):
        try:
            return self.pm_api.read_history(session_id), None
        except LLMToolError as exc:
            return None, chat_tui_pm.error_code(exc)

    def _pm_connect(self, session_id, cwd, loop):
        outcome = {"session": session_id, "cwd": cwd}
        runtime = None
        try:
            if session_id is None or cwd is None:
                # Without a session: the saved binding. Without a cwd: that session's binding.
                binding = self.pm_api.current_binding(session_id)
                if binding is None:
                    if session_id is None:
                        self._post(loop, self.finish_pm_connect, {"no_binding": True})
                        return
                    cwd = os.getcwd()
                else:
                    session_id, cwd = binding["native_session_id"], binding["cwd"]
                outcome.update(session=session_id, cwd=cwd)
            runtime = self.pm_api.runtime(session_id, cwd)
            runtime.open()
            with self._pm_lock:
                if self._pm_exiting:
                    exiting = True
                else:
                    exiting, self._pm_unadopted = False, runtime
            if exiting:  # The TUI exited while open() ran: nothing will adopt it.
                self._close_runtime(runtime)
                return
            outcome["runtime"] = runtime
            try:
                outcome["binding"] = self.pm_api.current_binding(session_id)
            except LLMToolError:
                outcome["binding"] = None
            outcome["history"], outcome["history_error"] = self._pm_read_history(session_id, runtime)
        except LLMToolError as exc:
            outcome["error"] = chat_tui_pm.error_code(exc)
        except Exception:
            outcome["error"] = chat_tui_task.UNKNOWN
        if not self._post(loop, self.finish_pm_connect, outcome) and "runtime" in outcome:
            self._drop_unadopted(runtime)

    def _drop_unadopted(self, runtime):
        """Close a runtime nobody adopted (completion could not be delivered)."""
        with self._pm_lock:
            mine = self._pm_unadopted is runtime
            if mine:
                self._pm_unadopted = None
        if mine:
            self._close_runtime(runtime)

    def finish_pm_connect(self, outcome):
        self.pm_pending = False
        if outcome.get("no_binding"):
            self.pm_state = None
            self.append("llmtool: PM not connected: no dedicated PM binding is saved. "
                        "/pm new [CWD] creates a dedicated PM session.")
            return
        if "error" in outcome:
            self.pm_state = None
            self.append("llmtool: PM not connected. " + chat_tui_pm.error_text(outcome["error"]))
            return
        with self._pm_lock:
            adopted = self._pm_unadopted is outcome["runtime"] and not self._pm_exiting
            if self._pm_unadopted is outcome["runtime"]:
                self._pm_unadopted = None
        if not adopted or self._received_signal is not None or self.app.is_done:
            self._close_runtime(outcome["runtime"])   # Closing twice is a no-op in pm_runtime.
            return
        self.pm_runtime = outcome["runtime"]
        # A new allowance belongs to an adopted connection, never a failed attempt.
        connection = object()
        self._auto_resume_connection = connection
        self._auto_resume_sent = False
        self._auto_resume_pending = (self.settings.get("auto_resume", "on") == "on"
                                     and not self.pm_outbox and self.pm_outbox_paused is None)
        self._auto_resume_unconfirmed = self._auto_resume_pending
        self.pm_binding = outcome.get("binding")
        if callable(getattr(self.pm_runtime, "background_turn", None)):
            threading.Thread(target=self._pm_watch, args=(self.pm_runtime, self._loop),
                             name="llmtool-chat-pm-watch", daemon=True).start()
        self.pm_target = (outcome["session"], getattr(self.pm_runtime, "cwd", outcome["cwd"]))
        self.pm_mode, self.task_profile, self.pm_state = True, None, "idle"
        # Status shows 暖機中… until the first turn's first output; W41 A: no warm-up message.
        self.pm_warm = True
        self._append_account(
            "PM mode: Enter sends each draft in real time to the PM session "
            + self.pm_target[0] + " (" + chat_tui_pm.safe_text(self._pm_runtime_name()) + ", cwd "
            + chat_tui_pm.safe_text(str(self.pm_target[1]))
            + ") through pm_runtime; " + chat_tui_pm.binding_text(self.pm_binding) + "; ",
            ". pm_runtime checked at connect time that the session did not look "
            "open elsewhere. Esc interrupts a turn; /pm disconnect ends the "
            "connection. Chat replies are never the PM.")
        self._append_history(outcome["history"], outcome["history_error"])
        if self._auto_resume_pending:
            session = self.pm_target[0]
            runtime = self.pm_runtime
            loop = self._loop
            threading.Thread(
                target=self._read_auto_resume,
                args=(connection, runtime, session, loop),
                name="llmtool-chat-pm-resume", daemon=True).start()
        self.app.invalidate()

    def _read_auto_resume(self, connection, runtime, session, loop):
        """Read resume evidence off the UI thread, bound to one adopted connection."""
        try:
            result = chat_tui_status.read_background(session)
        except Exception:
            result = None
        self._post(loop, self.finish_auto_resume_read, connection, runtime, session, result)

    def finish_auto_resume_read(self, connection, runtime, session, result):
        """Accept a background snapshot only for the still-current PM connection."""
        if (self._auto_resume_connection is not connection or self.pm_runtime is not runtime
                or not self.pm_target or self.pm_target[0] != session
                or not self._auto_resume_pending or not self._auto_resume_unconfirmed):
            return
        self._auto_resume_unconfirmed = False
        self._auto_resume_pending = isinstance(result, list) and bool(result)
        self._maybe_auto_resume()

    def _maybe_auto_resume(self):
        """Send one resume turn for this connection, only after work is confirmed and idle."""
        if not self._auto_resume_pending or self._auto_resume_sent:
            return
        if (self.settings.get("auto_resume", "on") != "on" or self.pm_runtime is None
                or not self.pm_mode or self.pm_state in ("closing", "exiting")
                or self._received_signal is not None or self.app.is_done or self.quit_pending
                or self.pm_outbox or self.pm_outbox_paused is not None):
            self._auto_resume_pending = False
            return
        if self._auto_resume_unconfirmed:
            return
        if (self.busy or self.pm_pending or self.pm_turn_active or self._pm_background_waiting
                or self.pm_state != "idle"):
            return
        try:
            busy = getattr(self.pm_runtime, "turn_active")()
        except Exception:
            self._auto_resume_pending = False
            return
        if busy is not False:
            if busy is True:
                return
            self._auto_resume_pending = False
            return
        try:
            blocked = getattr(self.pm_runtime, "automatic_sends_blocked")()
        except Exception:
            self._auto_resume_pending = False
            return
        if blocked is not False:
            self._auto_resume_pending = False
            return
        self._auto_resume_pending = False  # claim before starting; at most once per connect
        self._auto_resume_sent = True
        line = "（自動）重新連線：請接續進行中的工作並回報狀態"
        self.append(line, segments=[(line, "role.system")])
        self._pm_start_turn(line, "owner → PM（自動恢復）:\n")

    @staticmethod
    def _close_runtime(runtime):
        try:
            runtime.close(timeout=2)
        except Exception:
            pass

    def _pm_disconnect(self, runtime, loop):
        self._close_runtime(runtime)
        self._post(loop, self.finish_pm_disconnect)

    def finish_pm_disconnect(self):
        self.pm_pending = False
        self._auto_resume_pending = False
        if self.pm_turn_active:
            self._pm_drop_turn()   # the worker's end may come later or never; it is stale now
        self._pm_background_waiting.clear()
        self._pm_background_live.clear()
        self.pm_runtime, self.pm_target, self.pm_mode, self.pm_state = None, None, False, None
        self.pm_binding, self.pm_warm = None, False
        self.append("PM disconnected; chat mode (Enter sends to chat provider "
                    + self.session.provider + ", not a PM)")
        self._pm_pause("PM 已斷線")

    def _pm_history(self, session_id, runtime, loop):
        history, code = self._pm_read_history(session_id, runtime)
        self._post(loop, self.finish_pm_history, history, code)

    def finish_pm_history(self, history, code):
        self.pm_pending = False
        self._append_history(history, code)
        self._maybe_auto_resume()

    def _append_history(self, history, code):
        if code:
            self.append("llmtool: PM history unavailable. " + chat_tui_pm.error_text(code))
            return
        segments, sources = self._history_segments(history)
        self.append("".join(part for part, _token in segments), segments=segments,
                    sources=sources)

    def _history_segments(self, history):
        """Stored turns in the live layout (W57): a rule with the time, 「你」 and 「PM」
        labels, Markdown bodies, the turn's tools on one line and a short usage line."""
        turns, _total = chat_tui_pm.history_turns(history)
        header_time = turns[-1]["header_time"] if turns else "時間不明"
        header = "上次對話（%s）" % header_time
        segments = [(header, "role.system")]
        if not turns:
            segments.append(("\n" + BODY_INDENT + "還沒有存下的對話", "text"))
        sources = []
        for index, turn in enumerate(turns):
            segments += [("\n\n", "text"),
                         (chat_tui_view.RULE_CHAR * 2 + " " + turn["time"], "rule"),
                         ("\n", "text")]
            if turn["background"]:
                segments.append((PM_LABEL + "（背景通知）", "role.pm"))
            else:
                segments += [(OWNER_LABEL, "role.owner"),
                             ("\n" + BODY_INDENT + terminal_safe_text(turn["owner"]).replace(
                                 "\n", "\n" + BODY_INDENT), "text"),
                             ("\n\n" + PM_LABEL, "role.pm")]
            if turn["tools"]:
                segments += [("\n" + BODY_INDENT + "▸ 用過的工具：" + "、".join(turn["tools"]),
                              "tool.summary"), ("\n", "text")]
            segments.append(("\n", "text"))
            reply = terminal_safe_text(turn["reply"])
            if not reply:
                if index == len(turns) - 1 and turn["status"] == "INTERRUPTED":
                    reply = "輸入「繼續」讓 PM 接著做"
                else:
                    reply = "（沒有存下回覆文字）"
            body, body_sources = self._body(reply, BODY_INDENT)
            offset = sum(len(part) for part, _token in segments)
            sources += [(low + offset, high + offset, *rest) for low, high, *rest in body_sources]
            segments += body
            line = history_usage_line(turn)
            if line:
                segments += [("\n\n", "text"), (line, "meta.usage")]
        return segments, sources

    def submit_pm(self, text):
        """Enter in PM mode: one pm_runtime turn at a time; the draft leaves the editor and
        the turn is marked active in the same key handler, so nothing is sent twice."""
        if self.pm_runtime is None or self.pm_pending:
            self.append("llmtool: the PM is not connected; /pm connect")
            return
        self._auto_resume_pending = False
        self.input.buffer.reset(append_to_history=True)
        if self.pm_turn_active or self.pm_outbox:
            # W52 F2: a turn is running (or a paused queue waits): queue it, in order.
            self.pm_outbox.append(text)
            self._pm_notice("owner → PM（已排入佇列（第 %d 則））:\n" % len(self.pm_outbox)
                            + chat_tui_pm.safe_text(text)
                            + ("\n  — 佇列暫停（" + self.pm_outbox_paused + "）；/pm queue send 繼續"
                               if self.pm_outbox_paused else ""))
            if not self.pm_turn_active:
                self._pm_send_next()
            return
        self._pm_start_turn(text, "owner → PM:\n")

    def _pm_start_turn(self, text, header):
        self._pm_begin(PM_LABEL, "owner")
        self.append(header, segments=self._owner_segments(header + chat_tui_pm.safe_text(text),
                                                        rule=True))   # also refreshes the busy line
        # One idempotency key per submitted draft: pm_runtime never runs it twice.
        key = uuid.uuid4().hex
        self._loop = asyncio.get_running_loop()
        threading.Thread(target=self._pm_turn,
                         args=(self.pm_runtime, text, key, self._loop, self._pm_turn_seq),
                         name="llmtool-chat-pm-turn", daemon=True).start()

    def _pm_begin(self, label, kind):
        """Mark a new turn active (Owner or background) and return its token."""
        self.pm_turn_active, self.pm_turn_started = True, time.monotonic()
        self.pm_interrupted, self.pm_deltas, self.pm_tools = False, False, {}
        self.pm_turn_status = self.pm_turn_error = self.pm_interrupt_by = None
        self.pm_turn_refused = False
        self.pm_block_index = None
        self.pm_state = "working"
        self.pm_label, self.pm_turn_kind = label, kind
        self._pm_turn_seq += 1
        self._pm_reset_blocks()
        return self._pm_turn_seq

    def _pm_reset_blocks(self):
        """A new turn (or a background turn inside one): its own label and tool summary."""
        self.pm_labelled, self.pm_tool_calls, self.pm_tool_ids = False, [], {}
        self.pm_tool_block = None
        self.pm_header_open = False

    def _pm_turn(self, runtime, text, key, loop, seq):
        """Worker thread: forward each pm_runtime event to the event loop as it arrives."""
        # written: send() returned, so the message may have gone out; receipt: pm_runtime
        # reported the attempt's outcome (its Owner TURN_END, or a replay of an earlier one).
        written = receipt = False
        try:
            events = runtime.send(text, idempotency_key=key)
            written = True
            for event in events:
                if type(event) is dict:
                    self._post(loop, self.pm_event, event, seq)
                    owner_end = event.get("type") == "TURN_END" and event.get("origin") is None
                    if event.get("type") == "TURN_REPLAY" or (owner_end and receipt):
                        receipt = True
                        continue
                    if not owner_end:
                        continue
                    receipt = True
                    if event.get("status") in ("NOT_SENT", "UNKNOWN"):
                        # NOT_SENT: Claude Code withdrew it unprocessed (or it was never
                        # written). UNKNOWN: its fate is unconfirmed. Either way it goes back
                        # to the queue once, never lost; an UNKNOWN one is never sent again
                        # without the Owner.
                        self._post(loop, self._pm_hold, text, seq, event["status"],
                                   event.get("reason"), event.get("turn_id"))
        except LLMToolError as exc:
            if chat_tui_pm.error_code(exc) == "PM_TURN_ACTIVE" and not written:
                # P0-3: a background turn started first; nothing was sent. The draft goes
                # back to the front of the queue and is sent when that turn ends.
                self._post(loop, self._pm_hold, text, seq, "HELD")
                self._post(loop, self.finish_pm_turn, seq)
                return
            self._post(loop, self.pm_event, {"type": "RUNTIME_ERROR",
                                             "code": chat_tui_pm.error_code(exc)}, seq)
        except BaseException:
            self._post(loop, self.pm_event, {"type": "RUNTIME_ERROR",
                                             "code": chat_tui_task.UNKNOWN}, seq)
        if written and not receipt:
            # The turn ended without a receipt (a forked session, a broken iterator): the
            # message may have been delivered, so it is held as UNKNOWN.
            self._post(loop, self._pm_hold, text, seq, "UNKNOWN")
        self._post(loop, self.finish_pm_turn, seq)

    def _pm_hold(self, text, seq, status, reason=None, turn_id=None):
        """Put a draft back at the queue head, once per attempt; a late hold never changes
        the status of a newer turn."""
        if turn_id is not None:
            if turn_id in self._pm_held_attempts:
                return
            self._pm_held_attempts.add(turn_id)
        self._auto_resume_pending = False
        self.pm_outbox.appendleft(text)
        if not self._pm_stale(seq):
            self.pm_turn_status = status
        if status == "HELD":
            self._pm_notice("llmtool: PM 正在處理背景通知；這則訊息沒有送出，保留在佇列第 1 則，"
                            "背景回合結束後送出")
        elif status == "UNKNOWN":
            self.pm_outbox_paused = "UNKNOWN"   # only the Owner resends it
            self._pm_notice("llmtool: 無法確認 PM 是否收到這則訊息（UNKNOWN），未自動重送；"
                            "可用 /pm queue send 重送")
        elif reason in ("refused", "discarded"):
            # Claude Code turned it down: sending it again by itself would just repeat that.
            self.pm_outbox_paused = "Claude Code " + reason
            self._pm_notice("llmtool: Claude Code 沒有處理這則訊息（" + reason + "），保留在佇列"
                            "第 1 則並暫停；/pm queue send 重送")
        else:
            self._pm_notice("llmtool: Claude Code 沒有處理這則訊息（已撤回），保留在佇列第 1 則")

    def _pm_watch(self, runtime, loop):
        """Watcher thread (P0-3): while connected, take each turn Claude Code starts by
        itself between Owner turns and forward it like an Owner turn's events."""
        while runtime.is_alive():
            try:
                events = runtime.background_turn(timeout=PM_WATCH_SECONDS)
            except Exception:
                return   # pm_runtime could not record it; the next Owner send reports why
            if events is None:
                continue
            turn = {"runtime": runtime, "seq": None, "events": [], "ended": False}
            if not self._post(loop, self.pm_background_start, turn):
                return
            try:
                for event in events:
                    if type(event) is dict:
                        self._post(loop, self.pm_background_event, turn, event)
            except LLMToolError as exc:
                self._post(loop, self.pm_background_event, turn,
                           {"type": "RUNTIME_ERROR", "code": chat_tui_pm.error_code(exc)})
            except BaseException:
                self._post(loop, self.pm_background_event, turn,
                           {"type": "RUNTIME_ERROR", "code": chat_tui_task.UNKNOWN})
            self._post(loop, self.pm_background_end, turn)

    def pm_background_start(self, turn):
        if turn["runtime"] is not self.pm_runtime or self.pm_state == "closing":
            turn["seq"] = -1   # an old connection's turn: ignored
        elif self.pm_turn_active:
            turn["queued_at"] = datetime.now(timezone.utc).isoformat()
            self._pm_background_waiting.append(turn)   # shown when the Owner turn ends
        else:
            self._pm_background_show(turn)

    def _pm_background_header(self, note):
        """W56a layout: a turn rule, then the 「PM（背景通知）」 role label with its note; the
        turn's text follows under that label."""
        self.pm_label = PM_BACKGROUND_LABEL
        self._pm_reset_blocks()
        text = PM_BACKGROUND_LABEL + " " + note
        self.append(text, segments=self._rule_segments() + [
            (PM_BACKGROUND_LABEL, "role.pm"), (" " + note, "role.system")])
        self.pm_labelled = self.pm_header_open = True

    def _pm_background_track(self, key, turn_id=None):
        self._pm_background_live[key] = {
            "role": PM_LABEL, "task": turn_id or "背景通知", "pr": "", "state": "背景回合",
            "updated_at": datetime.now(timezone.utc).isoformat()}

    def pm_background_turns(self):
        """W56d hook: background turns of the connected PM (running, then waiting to be
        shown), shaped like chat_tui_status.background_items; they count in 背景 N and
        are listed in the ↓ panel."""
        if self.pm_runtime is None:
            return []
        waiting = [{"role": PM_LABEL, "task": "背景通知", "pr": "", "state": "等待顯示",
                    "updated_at": item.get("queued_at")}
                   for item in self._pm_background_waiting]
        return list(self._pm_background_live.values()) + waiting

    def _pm_background_show(self, turn):
        turn["seq"] = self._pm_begin(PM_BACKGROUND_LABEL, "background")
        self._pm_background_track(("turn", turn["seq"]))
        self._pm_background_header("Claude Code 自行開始一輪（背景工作的通知）")
        for event in turn["events"]:
            self.pm_event(event, turn["seq"])
        turn["events"] = []
        if turn["ended"]:
            self.finish_pm_turn(turn["seq"])

    def pm_background_event(self, turn, event):
        if turn["seq"] is None:
            turn["events"].append(event)
        else:
            self.pm_event(event, turn["seq"])

    def pm_background_end(self, turn):
        if turn["seq"] is None:
            turn["ended"] = True
        else:
            self.finish_pm_turn(turn["seq"])

    def _pm_stale(self, seq):
        """True for a worker callback of a turn that is no longer the active one."""
        return seq is not None and (seq != self._pm_turn_seq or not self.pm_turn_active)

    def _pm_drop_turn(self):
        """The connection is gone (disconnect, exit): end the turn here. Its worker's late
        events and end are stale from now on, the busy-line timer is gone."""
        self._pm_turn_seq += 1
        self._pm_finalize()
        self.pm_turn_active, self.pm_turn_started, self.pm_interrupted = False, None, False
        self._pm_busy_stop()

    def _pm_extend(self, text):
        """Queue text for the current PM block; the typewriter tick releases it. Every
        update is screened (see chat_tui_pm.screen_stream), so the pane shows only
        redacted, settled text."""
        if not text:
            return
        if self.pm_block_raw is None:
            self._pm_open_block()   # the reply streams while the busy line stays visible
        self.pm_queue += text
        self._pm_rate = max(self._pm_rate, -(-len(self.pm_queue) // PM_MAX_LAG_TICKS))
        self._pm_schedule_tick()

    def _pm_schedule_tick(self):
        if self._pm_tick_handle is not None or not self.pm_queue:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return   # No loop (unit tests): _pm_tick is driven directly; a flush empties it.
        self._pm_tick_handle = loop.call_later(PM_TICK_SECONDS, self._pm_tick)

    def _pm_tick(self):
        """Release a slice of the queued PM text, in order; more per tick when behind."""
        self._pm_tick_handle = None
        if self.pm_block_raw is None or not self.pm_queue:
            self.pm_queue, self._pm_rate = "", PM_CHARS_PER_TICK
            return
        self.pm_block_raw += self.pm_queue[:self._pm_rate]
        self.pm_queue = self.pm_queue[self._pm_rate:]
        if not self.pm_queue:
            self._pm_rate = PM_CHARS_PER_TICK
        self._pm_render(final=False)
        self._pm_schedule_tick()

    def _pm_flush(self):
        """Release every queued character now (block/turn end, interrupt, other output)."""
        if self._pm_tick_handle is not None:
            self._pm_tick_handle.cancel()
            self._pm_tick_handle = None
        if self.pm_queue and self.pm_block_raw is not None:
            self.pm_block_raw += self.pm_queue
        self.pm_queue, self._pm_rate = "", PM_CHARS_PER_TICK

    def _pm_render(self, final):
        shown = terminal_safe_text(chat_tui_pm.screen_stream(self.pm_block_raw, final))
        segments, sources = self._body(shown, BODY_INDENT)
        for notice in self.pm_tail:   # W55: notices stay below the streaming block
            segments += [("\n\n", "text")] + self._notice_segments(notice)
        self._block_render(self.pm_block_start, segments, sources)

    def _block_render(self, start, segments, sources=()):
        """Replace the pane text from a streaming block's start with its current text.

        The view keeps its reading position (rows above the block are unchanged) and
        follows only while it is at the bottom.
        """
        self.view.set_tail(start - 1, [("\n", "text")] + segments,
                           [(low + 1, high + 1, *rest) for low, high, *rest in sources])
        self.app.invalidate()

    def _pm_finalize(self):
        """End the streaming block: show its full screened text, held-back tail included."""
        self._pm_flush()
        if self.pm_block_raw is None:
            return
        self._pm_render(final=True)
        self.pm_block_raw = None
        self.pm_tail = []   # the notices stay where they are, below the finished block

    def _pm_busy_show(self):
        """Keep one timer for the busy line and running-tool durations."""
        if not self._pm_busy_wanted():
            self._pm_busy_cancel()
            return
        if self._pm_busy_handle is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                self.app.invalidate()
                return   # No loop (unit tests): the line is rendered on demand.
            self._pm_busy_handle = loop.call_later(PM_BUSY_SECONDS, self._pm_busy_tick)
        self.app.invalidate()

    def _pm_busy_tick(self):
        self._pm_busy_handle = None
        if self.pm_turn_active:
            self._pm_tool_tick()
            self._pm_busy_frame = (self._pm_busy_frame + 1) % len(PM_BUSY_GLYPHS)
        self._pm_busy_show()

    def _pm_busy_cancel(self):
        if self._pm_busy_handle is not None:
            self._pm_busy_handle.cancel()
            self._pm_busy_handle = None

    def _pm_busy_stop(self):
        """Cancel the busy-line timer, turn or no turn."""
        self._pm_busy_cancel()

    def pm_event(self, event, seq=None):
        if self._pm_stale(seq):
            return
        kind = event.get("type")
        if kind != "THINKING" and self.pm_state == "thinking":
            self.pm_state = "working"   # Thinking ended: text, a tool or the end follows.
        if kind in ("TEXT_DELTA", "ASSISTANT_TEXT", "TOOL_USE", "TURN_END", "RUNTIME_ERROR"):
            self.pm_warm = False        # The process answered: the cold start is over.
        if kind == "BACKGROUND_TURN_START":
            # Claude Code runs a turn of its own before it takes the Owner message.
            self._pm_finalize()
            self.pm_background_started = time.monotonic()
            self.pm_block_index, self.pm_deltas = None, False
            self._pm_background_track(("sub", self._pm_turn_seq), event.get("turn_id"))
            self._pm_background_header("Claude Code 先處理一則背景通知；這則訊息等它結束後才開始")
            return
        if kind == "TURN_ACCEPTED":
            if self.pm_label != PM_LABEL:
                self._pm_finalize()
                self.pm_label, self.pm_block_index, self.pm_deltas = PM_LABEL, None, False
                self._pm_reset_blocks()   # the Owner reply opens under its own 「PM」 label
                self.pm_turn_started = time.monotonic()   # the Owner turn's own timing
                notice = "Claude Code 開始處理你的訊息"
                self.append(notice, segments=[(notice, "role.system")])
            return
        if (kind == "TURN_END" and event.get("origin") == "background"
                and self.pm_turn_kind == "owner"):
            elapsed = max(0.0, time.monotonic() - (self.pm_background_started or time.monotonic()))
            line = usage_line(event, elapsed)
            self.append(line, segments=[(line, "meta.usage")])
            self._pm_background_live.pop(("sub", self._pm_turn_seq), None)
            self.pm_block_index, self.pm_deltas = None, False
            return
        if kind == "THINKING":
            # Status only: thinking content never reaches the pane (pm_runtime sends none).
            if self.pm_state == "working":
                self.pm_state = "thinking"
            self._pm_busy_show()
            self.app.invalidate()
        elif kind == "TEXT_DELTA" and type(event.get("text")) is str:
            index = event.get("block_index")
            if self.pm_block_raw is not None and index != self.pm_block_index:
                self._pm_finalize()   # A new content block starts a new PM block.
            self.pm_block_index = index
            self._pm_extend(event["text"])
            self.pm_deltas = True
        elif kind == "ASSISTANT_TEXT" and type(event.get("text")) is str:
            # The same text already streamed as deltas; show it only when it did not.
            if not self.pm_deltas:
                self._pm_extend(event["text"])
            self._pm_finalize()
            self.pm_deltas = False
            self._pm_busy_show()   # until the next text, tool or the turn end
        elif kind == "TOOL_USE":
            label = chat_tui_pm.tool_label(event)   # W57: what it does, else its name
            self.pm_tools[event.get("tool_use_id")] = label
            self.pm_tool_ids[event.get("tool_use_id")] = len(self.pm_tool_calls)
            self.pm_tool_calls.append([label, None, time.monotonic(), None])
            self._pm_finalize()   # the summary never splits a streaming block
            self._pm_tool_summary()
            self.pm_deltas = False
        elif kind == "TOOL_RESULT":
            if event.get("reason") == "PERMISSION_DENIED":
                self.pm_turn_refused = True   # a refused turn pauses the queue (finish_pm_turn)
            index = self.pm_tool_ids.get(event.get("tool_use_id"))
            if index is None:   # a result without its TOOL_USE still counts
                index = self.pm_tool_ids[event.get("tool_use_id")] = len(self.pm_tool_calls)
                self.pm_tool_calls.append([self.pm_tools.get(event.get("tool_use_id"),
                                                             chat_tui_pm.safe_name(None)),
                                           None, None, None])   # start unknown: no duration
            self.pm_tool_calls[index][1] = tool_outcome(event)
            self.pm_tool_calls[index][3] = time.monotonic()
            self._pm_finalize()
            self._pm_tool_summary()
        elif kind == "RUNTIME_ERROR":
            code = chat_tui_pm.safe_code(event.get("code"))
            self.pm_state = "error(" + code + ")"
            self.pm_turn_error = code
            stderr = chat_tui_pm.stderr_text(event)
            self.append("llmtool: " + chat_tui_pm.error_text(code)
                        + ("\n  stderr (screened): " + stderr if stderr else ""))
            if code == "PM_SESSION_FORKED":
                # pm_runtime closed itself; the TUI no longer holds a usable connection.
                self.pm_runtime, self.pm_mode = None, False
                self._pm_busy_stop()
        elif kind == "TURN_REPLAY":
            self.pm_turn_status = "REPLAY"
            notice = chat_tui_pm.replay_text(event)
            self.append(notice, segments=[(notice, "role.system")])
        elif kind == "TURN_END":
            self.pm_turn_status = chat_tui_pm.safe_code(event.get("status"))
            elapsed = max(0.0, time.monotonic() - (self.pm_turn_started or time.monotonic()))
            self.pm_last_status = self.pm_turn_status
            # A short right-aligned line; /pm history keeps the full usage.
            line = usage_line(event, elapsed)
            self.append(line, segments=[(line, "meta.usage")])   # append() flushes first

    def finish_pm_turn(self, seq=None):
        if self._pm_stale(seq):
            return
        if self.pm_outbox:
            self._auto_resume_pending = False
        status, error = self.pm_turn_status, self.pm_turn_error
        if (self.pm_interrupt_by in ("esc", "ctrl-c") or error is not None
                or self.pm_turn_refused or status not in ("COMPLETED", "ABSORBED")):
            self._auto_resume_pending = False
        for kind in ("turn", "sub"):   # the background turn of this turn (if any) is over
            self._pm_background_live.pop((kind, self._pm_turn_seq), None)
        self._pm_finalize()   # Nothing queued survives the turn (interrupt without TURN_END).
        self.pm_turn_active, self.pm_turn_started = False, None
        self._pm_busy_stop()
        if self.pm_state in ("working", "thinking", "interrupting"):
            self.pm_state = "idle"
        if self.pm_outbox and not self.pm_outbox_paused:
            if self.pm_runtime is None or self.pm_state == "closing":
                self._pm_pause("PM 已斷線")
            elif self.pm_interrupt_by == "ctrl-c":
                self._pm_pause("Ctrl-C 中斷")   # whatever the end: a COMPLETED can race it
            elif self.pm_interrupt_by == "esc":
                self._pm_esc_pause()   # W55: drafts queued after Esc are not sent either
            elif error is not None:
                self._pm_pause("上一回合失敗 " + error)
            elif self.pm_turn_refused:
                self._pm_pause("權限被拒")   # even when the turn still ended COMPLETED
            elif status in ("COMPLETED", "ABSORBED"):
                self._pm_send_next()
            elif status in ("HELD", "NOT_SENT"):
                # The background turn that held the draft sends it when it ends. If that
                # turn is already over (it ended before the hold was handled), drain now.
                runtime = self.pm_runtime
                busy = getattr(runtime, "turn_active", None)
                if not self._pm_background_waiting and not (callable(busy) and busy()):
                    self._pm_send_next()
            else:
                self._pm_pause("上一回合沒有完成（" + (status or "沒有 TURN_END") + "）")
        while self._pm_background_waiting and not self.pm_turn_active:
            turn = self._pm_background_waiting.popleft()
            if turn["runtime"] is self.pm_runtime and self.pm_state != "closing":
                self._pm_background_show(turn)
            else:
                turn["seq"] = -1
        self._maybe_auto_resume()
        self.app.invalidate()

    def interrupt_pm(self, by="esc"):
        if not self.pm_turn_active or self.pm_interrupted or self.pm_runtime is None:
            return
        self._auto_resume_pending = False
        self.pm_interrupted, self.pm_state = True, "interrupting"
        self.pm_interrupt_by = by
        # W55: visible at once, below a streaming reply, before the turn actually ends.
        self._pm_notice("llmtool: 已中斷，等待 PM 結束本輪…")
        if by == "esc":
            self._pm_esc_pause()   # nothing is sent after an Esc interrupt
        try:
            self.pm_runtime.interrupt()
        except LLMToolError as exc:
            self._pm_notice("llmtool: " + chat_tui_pm.error_text(chat_tui_pm.error_code(exc)))
        except Exception:
            self._pm_notice("llmtool: " + chat_tui_pm.error_text(chat_tui_task.UNKNOWN))

    def close_pm(self):
        """Exit path: close the adopted runtime and any one a connect worker opened but has
        not handed over yet; a connect still inside open() closes its own (see _pm_connect)."""
        with self._pm_lock:
            self._pm_exiting = True
            self._auto_resume_pending = False
            runtimes = [self.pm_runtime, self._pm_unadopted]
            self.pm_runtime, self._pm_unadopted = None, None
        # Exit: the turn's late worker callbacks are stale; nothing is rendered any more.
        self._pm_turn_seq += 1
        self.pm_turn_active, self.pm_turn_started = False, None
        self._pm_background_waiting.clear()
        self._pm_background_live.clear()
        for handle in (self._pm_tick_handle, self._pm_busy_handle, self._drag_copy_handle,
                       self._autoscroll_handle):
            if handle is not None:
                handle.cancel()
        self._pm_tick_handle = self._pm_busy_handle = self._drag_copy_handle = None
        self._autoscroll_handle = None
        for runtime in runtimes:
            if runtime is not None:
                self._close_runtime(runtime)

    def interrupt_send(self):
        """Esc or the first Ctrl-C during a chat request: ask the worker to stop.

        The request stays single-flight until the worker ends: a streamed reply ends
        at its next chunk, a reply that arrives in one piece ends when the provider
        returns. Nothing is killed; no provider call is made.
        """
        if not (self.busy and self.worker_kind == "chat") or self.send_interrupted:
            return False
        self.send_interrupted = True
        if self.send_cancel is not None:
            self.send_cancel.set()
        if self.stream_raw is not None:
            self.append("llmtool: interrupt requested; the reply stops at its next chunk")
        else:
            self.append("llmtool: interrupt requested; a streamed reply stops at its next chunk,"
                        " a reply that arrives in one piece is shown marked interrupted when"
                        " the provider returns")
        return True

    def request_quit(self):
        self._owner_output = True
        try:
            self._request_quit()
        finally:
            self._owner_output = False

    def _request_quit(self):
        self.close_menu()  # Ctrl-C/Ctrl-D: also restores the default Esc timeout.
        if self.pm_turn_active and not self.pm_interrupted:
            self.interrupt_pm("ctrl-c")   # also pauses the queue (finish_pm_turn)
            self.append("llmtool: Ctrl-C again exits (the PM connection is closed on exit)")
            return
        if self.pm_outbox and not self._quit_warned and not self.pm_turn_active:
            self._quit_warned = True
            self.append("llmtool: PM 佇列還有 %d 則未送出；再按一次 Ctrl-C 離開（不會送出）"
                        % len(self.pm_outbox))
            return
        if self.busy and self.worker_kind == "chat" and not self.send_interrupted:
            self.interrupt_send()
            self.append("llmtool: Ctrl-C again exits")
            return
        if self.busy:
            if not self.quit_pending:
                self.quit_pending = True
                if self.worker_kind == "chat":
                    self.append("llmtool: exit requested; exiting when the interrupted request "
                                "ends or reaches the provider timeout.")
                else:
                    self.append("llmtool: exit requested; the in-flight request cannot be "
                                "cancelled. Exiting when it finishes or reaches the provider "
                                "timeout.")
            return
        self.app.exit(result=0)

    def _handle_signal(self, signum):
        """Defer process-signal exit until any owned CLI child is cleaned up."""
        if self._received_signal is None:
            self._received_signal = signum
        self.close_menu()
        if self.busy:
            self.quit_pending = True
            self.app.invalidate()
        else:
            self.app.exit(result=0)

    def run(self):
        with tui_cli_signal_scope(self._handle_signal):
            # The scope above owns signal routing so background CLI children
            # can be stopped before the process exits.
            try:
                result = self.app.run(handle_sigint=False)
            finally:
                self.close_pm()  # Never leave the pm_runtime child behind, signal or not.
        if self._received_signal == signal.SIGINT:
            raise KeyboardInterrupt() from None
        if self._received_signal is not None:
            raise CLIHostTermination(128 + self._received_signal) from None
        return 0 if result is None else result
