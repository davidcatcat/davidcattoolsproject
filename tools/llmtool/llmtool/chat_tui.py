"""Opt-in full-screen chat UI over the existing ChatSession (A3.5 UX-1/UX-2).

Display layer only: ChatSession still owns config, provider/skill selection,
memory-only history, requests and errors. No disk history, tools, shell, Git or
file writes. Model output is untrusted and rendered via terminal_safe_text.
prompt-toolkit is an optional dependency (``llmtool[tui]``); when it is absent
or stdin/stdout is not a terminal, the existing line chat runs instead, before
any UI starts. A UI failure never drops into, or replays input through, the REPL.
"""

import asyncio
import os
import signal
import sys
import threading
import time
import uuid

from . import chat_tui_events, chat_tui_pm, chat_tui_status, chat_tui_task, chat_tui_view
from .chat import HELP, run_chat
from .config import selected_provider
from .errors import LLMToolError
from .providers.cli_lifecycle import CLIHostTermination, background_cli_invocation, tui_cli_signal_scope
from .terminal import terminal_safe_text

KEY_HELP = """Keys
  Enter                 Send message (or run /command)
  Ctrl-J, Esc Enter     Insert newline (multiline message); paste keeps newlines
  Up/Down               Move in input; on first/last line recall earlier inputs
  /                     Command picker (existing commands only): type to filter, Up/Down
                        choose and scroll, Enter completes the draft to the choice without
                        running it (a draft already equal to the choice runs, as without
                        the picker), Esc closes the picker; Esc Enter still inserts a newline
  Ctrl-R                Search this session's earlier inputs: type to filter, Ctrl-R/Up/Down
                        choose, Enter puts the entry in the draft without running it,
                        Esc cancels and leaves the draft unchanged
  Tab, Shift-Tab        Switch focus between input and conversation
  PageUp/PageDown       Scroll conversation one screen (Fn+↑/↓ on a Mac keyboard); when it is
                        focused Up/Down scroll one row, Home/End go to the top/bottom
  Mouse wheel           Scroll conversation one row per wheel step (trackpad works the same);
                        the view stays where you read it while output arrives, and scrolling
                        back to the bottom follows new output again (status shows 最新)
  Drag                  Select conversation text; dragging past the top/bottom edge keeps
                        scrolling and extends the selection. Releasing copies exactly the
                        selected text to the system clipboard (pbcopy; status shows 已複製),
                        so Cmd+V pastes it; double-click selects a word. Ctrl-C with a
                        selection copies it again and clears it instead of exiting
  F6                    Toggle mouse reporting off/on: off hands the mouse to the terminal
                        (its own select/copy; the wheel then no longer scrolls). Terminal.app
                        also selects natively with Fn-drag and Warp with Shift-drag while on
  F1                    Toggle this help (shown in place of the conversation)
  F2                    Toggle the last Task detail (Esc also closes it)
  F3                    Toggle the Task list (/task list): Up/Down choose, Enter opens the
                        detail, F5 or r refreshes, Esc closes; refreshes itself every
                        %d s while shown (local TaskStore read only, no AI call)
  F4                    Toggle recent events across Tasks (/task events): newest first;
                        F5 or r refreshes, Esc closes; never acknowledges or polls
  Esc (during a reply)  Interrupt the chat reply: a streamed reply stops at its next chunk
                        and the text shown so far stays, marked interrupted; a provider
                        that answers in one piece returns first, then that reply is shown
                        marked interrupted. Ctrl-C interrupts first, a second Ctrl-C exits
  Ctrl-C, Ctrl-D        Exit (Ctrl-D only with empty input)
A Task submit in progress cannot be cancelled; exit waits for it to finish or time out.

Talk to the PM: the dedicated Claude Code PM (/pm …) is the product entry
  /pm new [CWD]         Create a dedicated PM session (pm_runtime create_dedicated_pm; CWD
                        defaults to the current directory) and save it as the binding
  /pm connect [SESSION [CWD]]  Connect to the dedicated PM session: the saved binding when
                        no SESSION is given; "pm>" prompt, Enter sends
  /pm disconnect        End the connection and return to chat mode
  /pm history           Show the turns pm_runtime stored for this PM session
  /pm                   Show the connected PM and its state
  Esc (during a turn)   Interrupt the PM turn; Ctrl-C interrupts first, a second Ctrl-C exits
PM replies stream in as pm_runtime reports them; tool use is one line per tool and never
shows tool input or output. PM thinking is never shown as the answer: the PM field says
思考中… meanwhile. Chat replies are never sent to or shown as the PM.

Formal task commands (developer/test path, separate from ordinary chat; results come
only from llmtool Core and nothing is dispatched)
  /task submit --provider PROFILE REQUEST
                        開發測試用：只登記，不會派工 — records one Task in Core (also from
                        chat mode, and to resend a request Task mode refused as a repeat)
  /task mode PROFILE    Task mode: Enter submits each non-command draft as a formal PM
                        Task to PM provider PROFILE (no implicit default); "task>" prompt
  /task mode off        Back to chat mode: Enter sends to the chat provider again
  /task show TASK_ID    Open a Task's detail
  /task list            Open the Task list (stage, update time, request) from TaskStore
  /task events          Open recent events across Tasks (inbox, GitHub, tests, reviews)
A Task receipt shows the PM provider, Task ID, Run ID and state recorded by Core;
已建立／待派工 means no Developer has been dispatched. A chat reply is never a Task.
Stage and next responsible are derived by the TUI from Core fields; values Core does
not record are UNKNOWN or NOT_AVAILABLE.""" % chat_tui_status.REFRESH_SECONDS

# Existing commands only: (label shown, text a completion puts in the draft, summary).
# Picking one edits the input draft; it never runs the command.
SLASH_COMMANDS = (
    ("/help", "/help", "Show commands"),
    ("/status", "/status", "Show provider, model, skill, turns and status"),
    ("/provider", "/provider ", "List profiles or switch provider"),
    ("/skill", "/skill ", "List skills, select one, or clear"),
    ("/clear", "/clear", "Clear conversation"),
    ("/history", "/history", "Show user/assistant turns"),
    ("/exit", "/exit", "Exit"),
    ("/quit", "/quit", "Exit"),
    ("/pm connect", "/pm connect", "Talk to the PM: connect to the dedicated PM session"),
    ("/pm new", "/pm new", "Create a dedicated PM session (pm_runtime binding)"),
    ("/pm disconnect", "/pm disconnect", "End the PM connection"),
    ("/pm history", "/pm history", "Turns pm_runtime stored for this PM"),
    ("/task submit", "/task submit --provider ",
     "開發測試用：只登記，不會派工 (--provider PROFILE REQUEST)"),
    ("/task show", "/task show ", "Show a task by TASK_ID"),
    ("/task mode", "/task mode ", "Task mode for drafts: PROFILE, or off"),
    ("/task list", "/task list", "Task list with status from TaskStore"),
    ("/task events", "/task events", "Recent Task events from TaskStore"),
)
MENU_LIMIT = 5  # Most picker rows shown at once; more matches scroll.
# Status bar fields in display order, and the order they drop when the row is too narrow.
STATUS_FIELDS = ("mode", "provider", "model", "state", "usage", "scroll", "mouse", "help")
STATUS_DROP_ORDER = ("mouse", "help", "usage", "scroll", "model", "provider")
ESC_SEQUENCE_TIMEOUT = 0.4  # Seconds a lone Esc waits for a following Enter (Esc Enter = newline).
# PM typewriter: the CLI emits text in bursts (0.6–1 s apart); received text is queued and
# released every tick at PM_CHARS_PER_TICK (≈67 chars/s). A long queue raises the rate to
# len/PM_MAX_LAG_TICKS until it empties, so the pane never lags the stream by more than
# about PM_MAX_LAG_TICKS ticks (≈1 s). The end of a block, of the turn or an interrupt
# flushes everything at once: total time unchanged.
PM_TICK_SECONDS = 0.03
PM_CHARS_PER_TICK = 2
PM_MAX_LAG_TICKS = 33


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


def slash_matches(text):
    """Commands matching a one-line slash draft: prefix matches first, then substring."""
    if "\n" in text or not text.startswith("/"):
        return []
    query = text.lower()
    prefix = [c for c in SLASH_COMMANDS if c[0].startswith(query)]
    inside = [c for c in SLASH_COMMANDS if c not in prefix and len(query) > 2 and query[1:] in c[0]]
    return prefix + inside


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


def run_chat_tui(session, stdin=None, stdout=None, stderr=None):
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    reason = tui_unavailable_reason(stdin, stdout)
    if reason is not None:
        # Decided before any UI exists, so the line chat starts from a clean terminal.
        print("llmtool: --tui unavailable: " + reason + "; using line chat", file=stderr)
        return run_chat(session, stdout=stdout, stderr=stderr)
    try:
        try:
            status = ChatTUI(session).run()
        except Exception:
            status = 1
        if status != 0:
            print(FAILED, file=stderr)
        return status
    finally:
        session.active = False


def _tokens(usage):
    """Compact provider-reported usage for the status bar; UNKNOWN when absent."""
    if usage is None:
        return "UNKNOWN"
    return "%d+%d=%d" % (usage["prompt_tokens"], usage["completion_tokens"], usage["total_tokens"])


class ChatTUI:
    """Conversation pane, persistent multiline input, help and status bar."""

    def __init__(self, session, input=None, output=None, pm_api=None):
        from prompt_toolkit.application import Application
        from prompt_toolkit.document import Document
        from prompt_toolkit.filters import Condition, has_focus
        from prompt_toolkit.history import InMemoryHistory
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.key_binding.bindings.mouse import load_mouse_bindings
        from prompt_toolkit.keys import Keys
        from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.layout.dimension import Dimension
        from prompt_toolkit.styles import Style
        from prompt_toolkit.utils import get_cwidth
        from prompt_toolkit.widgets import TextArea

        self._Document = Document
        self._get_cwidth = get_cwidth
        self.session = session
        self.busy = False
        self.quit_pending = False
        self.show_help = False
        # Mouse reporting: on, the wheel scrolls the conversation; off (F6), the terminal's
        # own drag-select and copy work. The Application reads this filter every render.
        self.mouse_enabled = True
        # Mouse drag in the conversation: last pointer (x, y) and the auto-scroll timer handle
        # used while the pointer is held at or beyond the pane edge.
        self._drag_pointer = None
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
        # Task list: rows read from TaskStore on a reader thread (never busy/single-flight).
        self.show_list = False
        self.list_records = []
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
        self._loop = None
        self._received_signal = None
        # The conversation pane keeps its own rows, reading position, selection and
        # follow state (chat_tui_view); no scrollbar: the terminal's own is the only one.
        self.view = chat_tui_view.ConversationView(get_cwidth)
        self.conversation = chat_tui_view.make_pane(self.view)
        self.conversation.set_text(
            terminal_safe_text("LLMTool interactive chat (full-screen)\n" + session.status()
                               + "\nHistory stays in memory. F1 or /help for help; Ctrl-C exits."
                               + "\nChat mode talks to the chat provider directly; it is not a PM."
                               + " /pm connect (or /pm new) talks to the dedicated PM."
                               + " /task submit is for development testing only: it records"
                               + " a Task and dispatches nothing."))
        self.input = TextArea(multiline=True, wrap_lines=True, focus_on_click=True,
                              prompt=lambda: ("pm> " if self.pm_mode else
                                              "task> " if self.task_profile else "> "),
                              height=lambda: Dimension(
                                  min=min(4, max(1, self.input.buffer.document.line_count)),
                                  max=min(4, max(1, self.input.buffer.document.line_count))),
                              history=InMemoryHistory(),
                              # Ctrl-R search never edits the draft; Enter or Esc ends it.
                              read_only=Condition(lambda: self.searching))
        self.help = TextArea(text=KEY_HELP + "\n\n" + HELP, read_only=True, scrollbar=True,
                             wrap_lines=True, style="class:help",
                             height=Dimension(min=1, weight=1))
        self.detail = TextArea(text="", read_only=True, scrollbar=True, wrap_lines=True,
                               height=Dimension(min=1, weight=1))
        showing_help = Condition(lambda: self.show_help)
        showing_detail = Condition(lambda: self.show_detail and not self.show_help)
        showing_list = Condition(lambda: self.show_list and not self.show_detail
                                 and not self.show_help)
        showing_events = Condition(lambda: self.show_events and not self.show_list
                                   and not self.show_detail and not self.show_help)
        self.events = TextArea(text="", read_only=True, scrollbar=True, wrap_lines=True,
                               height=Dimension(min=1, weight=1))
        events_focused = has_focus(self.events)
        self.list_window = Window(FormattedTextControl(self.list_text, focusable=True),
                                  wrap_lines=False, height=Dimension(min=1, weight=1))
        list_focused = has_focus(self.list_window)
        picker_shown = Condition(lambda: sum(self.picker_layout()) > 0)
        menu_shown = picker_shown & Condition(lambda: not self.searching)
        searching = Condition(lambda: self.searching)
        # Opened only by typed/pasted insertion, so recalled history never opens it
        # and Up/Down keep recalling input history when the menu is closed.
        self.input.buffer.on_text_insert += lambda _buffer: self._refresh_menu(True)
        self.input.buffer.on_text_changed += lambda _buffer: self._refresh_menu(False)
        self.menu_window = Window(
            FormattedTextControl(self.menu_text), style="class:menu",
            height=lambda: Dimension.exact(max(1, sum(self.picker_layout()))), wrap_lines=False)
        self.rule_window = Window(height=1, char="-", style="class:rule")
        self.status_window = Window(FormattedTextControl(self.status_text), style="class:status",
                                   height=Dimension.exact(1), wrap_lines=False)
        root = HSplit([ConditionalContainer(self.conversation,
                                            filter=~showing_help & ~showing_detail & ~showing_list
                                            & ~showing_events),
                       ConditionalContainer(self.help, filter=showing_help),
                       ConditionalContainer(self.detail, filter=showing_detail),
                       ConditionalContainer(self.list_window, filter=showing_list),
                       ConditionalContainer(self.events, filter=showing_events),
                       self.rule_window,
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

        @keys.add("escape", filter=input_focused & pm_turn & ~picker_shown)
        def _pm_interrupt(event):
            self.interrupt_pm()

        @keys.add("escape", filter=input_focused & chat_request & ~picker_shown)
        def _send_interrupt(event):
            self.interrupt_send()

        @keys.add("escape", filter=showing_detail & ~picker_shown)
        def _detail_close(event):
            self.toggle_detail(False)

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
            # Always a newline in the draft, never a send; ends a history search first.
            self.close_menu()
            self.input.buffer.insert_text("\n")

        @keys.add("tab")
        @keys.add("s-tab")
        def _focus(event):
            self.toggle_focus()

        @keys.add("pageup")
        def _page_up(event):
            self.scroll(-1)

        @keys.add("pagedown")
        def _page_down(event):
            self.scroll(1)

        conversation_focused = has_focus(self.conversation)

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

        @keys.add("enter", filter=list_focused)
        def _list_open(event):
            self.open_selected()

        @keys.add("escape", filter=list_focused & ~showing_detail)
        def _list_close(event):
            self.toggle_list(False)

        @keys.add("c-c")
        def _interrupt(event):
            # Warp may send Ctrl-C for Cmd+C: with a selection, copy it again and clear it
            # instead of interrupting or exiting (a second Ctrl-C then acts as usual).
            if self.view.selected_range() and not self.show_help:
                self.view.copy_selection()
                self.view.clear_selection()
                return
            self.request_quit()

        @keys.add("c-d", filter=Condition(lambda: not self.input.text))
        def _eof(event):
            self.request_quit()

        self.app = Application(
            layout=Layout(root, focused_element=self.input), key_bindings=keys, full_screen=True,
            mouse_support=Condition(lambda: self.mouse_enabled), refresh_interval=0.5,
            input=input, output=output,
            style=Style.from_dict({"status": "reverse", "help": "italic", "rule": "fg:ansibrightblack",
                                   "conversation.selected": "reverse",
                                   "menu": "bg:ansibrightblack fg:ansiwhite",
                                   "menu.current": "reverse",
                                   "menu.header": "bg:ansibrightblack fg:ansiwhite bold"}))
        # prompt_toolkit waits timeoutlen (1s) after a lone Esc in case Esc Enter follows;
        # that made dismissing the picker feel stuck. Shortened only while the picker is
        # visible on screen, so the Esc Enter window is unchanged otherwise.
        self._default_timeoutlen = self.app.timeoutlen
        # A resize can hide the picker without any key press; re-check before each draw.
        self.app.before_render += lambda _app: self._sync_picker()
        # A shown list re-reads TaskStore when stale; redraws never read more often.
        self.app.before_render += lambda _app: self._list_tick()
        self._default_mouse_binding = load_mouse_bindings().bindings[0]
        _enable_mouse_drag_reporting(self.app.output)

    # -- rendering -----------------------------------------------------------------
    def status_text(self):
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
            "mode": (self.pm_status_field(compact) if self.pm_mode
                     else "TASK→PM:" + compact(self.task_profile or "", 12)),
            "provider": "p:" + compact(session.provider, 10),
            "model": "m:" + compact(reported or model or "default", 14),
            "state": compact(state, 14),
            "usage": "t:" + compact(_tokens(session.last_usage), 14),
            "scroll": self.view.status(),
            "mouse": "F6 滑鼠:" + ("開" if self.mouse_enabled else "關"),
            "help": "F1",
        }
        width = max(1, self.app.output.get_size().columns)
        # The mode field appears only in Task mode, so the chat footer is unchanged.
        shown = [key for key in STATUS_FIELDS
                 if key != "mode" or self.task_profile or self.pm_mode]
        if self.pm_mode:
            # Chat provider/model/usage are not the PM's; in PM mode only the PM field shows.
            shown = ["mode", "scroll", "mouse", "help"]
        for name in STATUS_DROP_ORDER:
            if self._get_cwidth(" | ".join(fields[key] for key in shown)) <= width:
                break
            if name in shown:
                shown.remove(name)
        return fit_width(" | ".join(fields[key] for key in shown), width, self._get_cwidth)

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
        free = self.app.output.get_size().rows - 3 - input_rows  # conversation, rule, status
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
            items = slash_matches(text)
        self._set_menu(items)

    def _set_menu(self, items):
        if items != self.menu_items:
            self.menu_index = 0
            self.menu_offset = 0
        self.menu_items = items
        self._sync_picker()

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
                 or self.events_visible() or self.pm_turn_active
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

    def append(self, text):
        """Add untrusted text; follow new output only if the caret was at the end.

        New output closes the Task detail so it is never hidden behind that pane.
        """
        self._pm_finalize()  # Any other output ends the streaming PM block (shown in full).
        block = self._stream_detach()  # A streaming chat reply stays one block, below.
        if self.show_detail:
            self.toggle_detail(False)
        if self.show_list or self.show_events:
            self.unseen_output = True  # The pane header says so; it stays for browsing.
            if self.show_events:
                self.render_events()
        old = self.conversation.text
        self.conversation.set_text(old + ("\n\n" if old else "") + terminal_safe_text(text))
        if block is not None:
            self._stream_attach(block)
        self.app.invalidate()

    def conversation_visible(self):
        return not (self.show_help or self.show_detail or self.show_list or self.show_events)

    def scroll(self, direction):
        """PageUp/PageDown: one screen of rows; the pane keeps or resumes following itself."""
        self.view.page(direction)

    # -- mouse: wheel, drag selection, edge auto-scroll, copy ------------------------------
    def mouse_event(self, event):
        """Every vt100 mouse event: the conversation pane's own handling, else the default.

        The wheel scrolls the conversation from anywhere while it is shown (never the input
        or its history). A left drag that starts on the pane selects text, keeps selecting
        while the pointer is beyond the pane, and copies on release. Other clicks (input,
        picker, status) go to prompt_toolkit's own handler.
        """
        parsed = chat_tui_view.parse_mouse(event.data)
        if parsed is None or not self.conversation_visible() or not self._pane_mouse(*parsed):
            return self._default_mouse_binding.call(event)
        return None

    def _pane_geometry(self):
        info = self.conversation.window.render_info
        if info is None:
            return None
        return info._x_offset, info._y_offset, info.window_height

    def _pane_mouse(self, kind, button, x, y):
        from prompt_toolkit.mouse_events import MouseButton, MouseEventType
        view = self.view
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
            self._drag_to(x, y)
            if kind == MouseEventType.MOUSE_UP:
                self._autoscroll_stop()
                view.end_drag(view.head)
            return True
        if not inside:
            return False
        if kind == MouseEventType.MOUSE_DOWN and button == MouseButton.LEFT:
            self._autoscroll_stop()
            view.begin_drag(view.offset_at(view.top + y - top, x - left))
            self._drag_pointer = (x, y)
            return True
        if kind == MouseEventType.MOUSE_DOWN:
            view.clear_selection()
        return True  # Clicks on the pane never move focus or the input caret.

    def _drag_to(self, x, y):
        """Extend the selection to the pointer; at or beyond an edge, scroll a row first."""
        view = self.view
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
        self.app.invalidate()

    def _autoscroll_tick(self):
        self._autoscroll_handle = None
        if self.view.dragging and self._drag_pointer is not None and self.conversation_visible():
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
                else self.events if self.show_events else self.conversation)
        layout.focus(pane if layout.has_focus(self.input) else self.input)

    def toggle_mouse(self):
        """F6: mouse reporting on (wheel scrolls, drag selects and copies) or off (the
        terminal's own drag-select and copy; the wheel no longer reaches the pane)."""
        self.mouse_enabled = not self.mouse_enabled
        self._autoscroll_stop()
        self.view.clear_selection()
        if self.mouse_enabled:
            self.append("llmtool: mouse on; the wheel scrolls the conversation and a drag selects"
                        " and copies text. F6 turns it off to select with the terminal instead")
        else:
            self.append("llmtool: mouse off; drag to select and copy text with the terminal."
                        " Fn+↑/↓ (PageUp/PageDown) scroll; F6 turns the wheel back on")

    def toggle_help(self):
        self.show_help = not self.show_help
        self._autoscroll_stop()
        self.view.dragging = False   # a drag cannot continue across the pane switch
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
        self.show_detail = show
        # Closing a detail opened from the list goes back to the list.
        self.app.layout.focus(self.list_window if self.show_list and not show else self.input)
        self._sync_picker()

    # -- Task list (TaskStore reads only; never a provider or network call) ----------
    def toggle_list(self, show):
        self.show_list = show
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
            text = chat_tui_events.recent_text(self.list_records, self.list_unreadable,
                                               self.list_loaded[1])
        if self.unseen_output:
            text = "new conversation output — Esc shows it\n" + text
        buffer = self.events.buffer
        position = min(buffer.cursor_position, len(text))
        buffer.set_document(self._Document(terminal_safe_text(text), position),
                            bypass_readonly=True)

    def refresh_list(self):
        """Start one TaskStore read unless one is already running."""
        if self.list_reader is not None:
            return
        self._loop = asyncio.get_running_loop()
        self.list_reader = threading.Thread(target=self._read_list, args=(self._loop,),
                                            name="llmtool-chat-task-list", daemon=True)
        self.list_reader.start()

    def _read_list(self, loop):
        from .task_store import TaskStore
        try:
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
        self.list_records, self.list_unreadable, self.list_error = records, unreadable, error
        self.list_loaded = (time.monotonic(), time.strftime("%H:%M:%S"))
        ids = [chat_tui_task.value(r, "task_id") for r in records]
        # Keep the selection on the same Task when it is still listed.
        self.list_index = ids.index(selected) if selected in ids else min(
            self.list_index, max(0, len(records) - 1))
        if self.show_events:
            self.render_events()
        self.app.invalidate()

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
        width = max(1, self.app.output.get_size().columns)
        cut = lambda text: fit_width(terminal_safe_text(text).replace("\n", " "), width,
                                     self._get_cwidth)
        if self.list_loaded is None:
            state = "reading TaskStore…"
        else:
            state = ("%d shown, %d unreadable; refreshed %s; auto every %ds"
                     % (len(self.list_records), self.list_unreadable, self.list_loaded[1],
                        chat_tui_status.REFRESH_SECONDS))
        rows = [("class:menu.header", cut(" Tasks (TaskStore records; stage derived by the TUI) — "
                                          + state + " — Enter detail, F5/r refresh, Esc close")),
                ("class:menu.header", cut("  " + "Task ID".ljust(8) + "  " + "Stage (derived)".ljust(26)
                                          + "Updated (Core)".ljust(20) + "  Request (Core, first line)"))]
        if self.unseen_output:
            rows.append(("class:menu.header", cut(" new conversation output — Esc shows it")))
        if self.list_error is not None:
            rows.append(("", cut(" llmtool: " + self.list_error)))
        elif self.list_loaded is not None and not self.list_records:
            rows.append(("", cut(" no Tasks in TaskStore")))
        info = self.list_window.render_info
        space = max(1, (info.window_height if info is not None else 20) - len(rows))
        top = max(0, min(self.list_index - space + 1, len(self.list_records) - space))
        top = min(top, self.list_index)
        for index in range(top, min(len(self.list_records), top + space)):
            item = chat_tui_status.row(self.list_records[index])
            stage = item["stage"] + " " * max(0, 26 - self._get_cwidth(item["stage"]))
            text = (("▶ " if index == self.list_index else "  ") + item["task_id"][:8] + "  "
                    + stage + item["updated"][:20] + "  " + item["name"])
            rows.append(("class:menu.current" if index == self.list_index else "", cut(text)))
        result = []
        for row in rows:
            result.extend([row, ("", "\n")])
        return result[:-1]

    def set_detail(self, text):
        self.detail.buffer.set_document(self._Document(terminal_safe_text(text), 0),
                                        bypass_readonly=True)
        self.show_help = False
        self.toggle_detail(True)

    # -- actions -------------------------------------------------------------------
    def submit(self):
        text = self.input.text
        if self.busy:
            # Single flight: the typed input stays in the editor, nothing is queued.
            self.append("llmtool: a request is in progress; input kept, send again when idle")
            return
        if not text.strip():
            self.input.buffer.reset()
            return
        if text.lstrip().startswith("/"):
            self.input.buffer.reset(append_to_history=True)
            self.run_command(text)
        elif self.pm_mode:
            self.submit_pm(text)
        elif self.task_profile is not None:
            self.submit_task_draft(text)
        else:
            self.input.buffer.reset(append_to_history=True)
            self.start_send(text)

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
            self.run_task_command(text)
            return
        if text.strip() == "/help":
            self.append(KEY_HELP + "\n\n" + HELP)
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
        if len(parts) == 3 and parts[1] == "mode":
            self.set_task_mode(parts[2])
            return
        self.append("usage: /task submit --provider PROFILE REQUEST\n"
                    "       /task show TASK_ID\n"
                    "       /task mode PROFILE|off\n"
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
        from argparse import Namespace
        from .task_submission_cli import run_task_show, run_task_submit

        result = {"action": action, "first": first, "from_mode": from_mode, "code": None,
                  "view": None, "raw": "", "error": None, "detail": None}
        try:
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
        except LLMToolError as exc:
            result["error"] = str(exc)
        except BaseException:
            result["error"] = "task command failed; details omitted"
        try:
            loop.call_soon_threadsafe(self.finish_task, result)
        except RuntimeError:
            pass

    @staticmethod
    def _detail_text(view):
        """Worker thread: status summary from the stored record, then Core's task show view."""
        from .task_store import TaskStore
        try:
            record = TaskStore().get(view["task_id"])
        except Exception:
            summary = "Status: " + chat_tui_task.UNKNOWN + " (TaskStore record unreadable)"
            extra = ""
        else:
            summary = (chat_tui_status.summary_text(record) + "\n\n"
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
        self.append("you:\n" + text)
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
                self.set_detail(result["detail"])
        self._finish(render)

    def stream_text(self, text):
        """Event-loop callback: show one streamed chunk of the chat reply in place."""
        if not (self.busy and self.worker_kind == "chat") or not text:
            return
        if self.stream_raw is None:
            self._stream_attach("")
        self.stream_raw += text
        self.stream_seen = True
        self._block_render(self.stream_start, terminal_safe_text(self.stream_raw))

    def _stream_attach(self, raw):
        """Open the chat reply block at the end of the pane with the text so far."""
        self.stream_head = len(self.conversation.text)
        self.append("assistant:")
        self.stream_raw = raw
        self.stream_start = len(self.conversation.text) + 1   # after "\n"
        if raw:
            self._block_render(self.stream_start, terminal_safe_text(raw))

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
                self.append("assistant:\n" + (text or ""))
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
        if self.quit_pending:
            self.app.exit(result=0)

    # -- real-time PM (pm_runtime only; never ChatSession) ---------------------------
    def pm_status_field(self, compact):
        session_id = self.pm_target[0][:8] if self.pm_target else chat_tui_task.UNKNOWN
        state = self.pm_state or chat_tui_task.UNKNOWN
        if state == "working" and self.pm_turn_started is not None:
            state = "working %.1fs" % max(0.0, time.monotonic() - self.pm_turn_started)
            if self.pm_warm:   # first turn, nothing received yet: cold start, not a slow PM
                state = "暖機中… %.1fs" % max(0.0, time.monotonic() - self.pm_turn_started)
        elif state == "idle" and self.pm_warm:
            state = "暖機中…"
        elif state == "thinking":
            state = "思考中…"
        return "PM:" + compact(session_id, 8) + " " + compact(state, 18)

    def run_pm_command(self, text):
        parts = text.split()
        if parts == ["/pm"]:
            if self.pm_runtime is None:
                self.append("PM: not connected. /pm connect uses the saved dedicated PM binding; "
                            "/pm new [CWD] creates one.")
            else:
                self.append("PM: claude-code session " + self.pm_target[0] + " (cwd "
                            + chat_tui_pm.safe_text(str(self.pm_target[1])) + "), "
                            + chat_tui_pm.binding_text(self.pm_binding)
                            + ", state " + (self.pm_state or chat_tui_task.UNKNOWN)
                            + ("; PM mode" if self.pm_mode else "; not in PM mode"))
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
        if parts[1:2] == ["new"] and len(parts) <= 3:
            if self.pm_runtime is not None:
                self.append("llmtool: connected to PM session " + self.pm_target[0]
                            + "; /pm disconnect before creating another")
                return
            self._pm_worker(self._pm_new, parts[2] if len(parts) == 3 else os.getcwd())
            return
        if parts == ["/pm", "disconnect"]:
            if self.pm_runtime is None:
                self.append("PM: not connected")
                return
            self.pm_state = "closing"
            self._pm_worker(self._pm_disconnect, self.pm_runtime)
            return
        if parts == ["/pm", "history"]:
            if self.pm_runtime is None:
                self.append("PM: not connected")
                return
            self._pm_worker(self._pm_history, self.pm_target[0], self.pm_runtime)
            return
        self.append("usage: /pm new [CWD]\n       /pm connect [SESSION [CWD]]\n"
                    "       /pm disconnect\n       /pm history\n       /pm")

    def _pm_new(self, cwd, loop):
        try:
            outcome = {"binding": self.pm_api.create_dedicated(cwd)}
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
        self.pm_binding = outcome.get("binding")
        self.pm_target = (outcome["session"], getattr(self.pm_runtime, "cwd", outcome["cwd"]))
        self.pm_mode, self.task_profile, self.pm_state = True, None, "idle"
        # Status shows 暖機中… until the first turn's first output; W41 A: no warm-up message.
        self.pm_warm = True
        self.append("PM mode: Enter sends each draft in real time to the PM session "
                    + self.pm_target[0] + " (claude-code, cwd "
                    + chat_tui_pm.safe_text(str(self.pm_target[1]))
                    + ") through pm_runtime; " + chat_tui_pm.binding_text(self.pm_binding)
                    + ". pm_runtime checked at connect time that the session did not look "
                    "open elsewhere. Esc interrupts a turn; /pm disconnect ends the "
                    "connection. Chat replies are never the PM.")
        if outcome["history_error"]:
            self.append("llmtool: PM history unavailable. "
                        + chat_tui_pm.error_text(outcome["history_error"]))
        else:
            self.append(chat_tui_pm.history_text(outcome["history"]))

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
        self.pm_runtime, self.pm_target, self.pm_mode, self.pm_state = None, None, False, None
        self.pm_binding, self.pm_warm = None, False
        self.append("PM disconnected; chat mode (Enter sends to chat provider "
                    + self.session.provider + ", not a PM)")

    def _pm_history(self, session_id, runtime, loop):
        history, code = self._pm_read_history(session_id, runtime)
        self._post(loop, self.finish_pm_history, history, code)

    def finish_pm_history(self, history, code):
        self.pm_pending = False
        self.append(("llmtool: PM history unavailable. " + chat_tui_pm.error_text(code)) if code
                    else chat_tui_pm.history_text(history))

    def submit_pm(self, text):
        """Enter in PM mode: one pm_runtime turn at a time; the draft leaves the editor and
        the turn is marked active in the same key handler, so nothing is sent twice."""
        if self.pm_turn_active:
            self.append("llmtool: a PM turn is in progress; input kept. Esc interrupts it.")
            return
        if self.pm_runtime is None or self.pm_pending:
            self.append("llmtool: the PM is not connected; /pm connect")
            return
        self.input.buffer.reset(append_to_history=True)
        self.pm_turn_active, self.pm_turn_started = True, time.monotonic()
        self.pm_interrupted, self.pm_deltas, self.pm_tools = False, False, {}
        self.pm_block_index = None
        self.pm_state = "working"
        self.append("owner → PM:\n" + chat_tui_pm.safe_text(text))
        # One idempotency key per submitted draft: pm_runtime never runs it twice.
        key = uuid.uuid4().hex
        self._loop = asyncio.get_running_loop()
        threading.Thread(target=self._pm_turn, args=(self.pm_runtime, text, key, self._loop),
                         name="llmtool-chat-pm-turn", daemon=True).start()

    def _pm_turn(self, runtime, text, key, loop):
        """Worker thread: forward each pm_runtime event to the event loop as it arrives."""
        try:
            for event in runtime.send(text, idempotency_key=key):
                if type(event) is dict:
                    self._post(loop, self.pm_event, event)
        except LLMToolError as exc:
            self._post(loop, self.pm_event, {"type": "RUNTIME_ERROR",
                                             "code": chat_tui_pm.error_code(exc)})
        except BaseException:
            self._post(loop, self.pm_event, {"type": "RUNTIME_ERROR",
                                             "code": chat_tui_task.UNKNOWN})
        self._post(loop, self.finish_pm_turn)

    def _pm_extend(self, text):
        """Queue text for the current PM block; the typewriter tick releases it. Every
        update is screened (see chat_tui_pm.screen_stream), so the pane shows only
        redacted, settled text."""
        if not text:
            return
        if self.pm_block_raw is None:
            self.append("PM:")
            self.pm_block_raw = ""
            self.pm_block_start = len(self.conversation.text) + 1   # after "\n"
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
        self._block_render(self.pm_block_start,
                           terminal_safe_text(chat_tui_pm.screen_stream(self.pm_block_raw, final)))

    def _block_render(self, start, shown):
        """Replace the pane text from a streaming block's start with its current text.

        The view keeps its reading position (rows above the block are unchanged) and
        follows only while it is at the bottom.
        """
        self.conversation.set_text(self.conversation.text[:start - 1] + "\n" + shown)
        self.app.invalidate()

    def _pm_finalize(self):
        """End the streaming block: show its full screened text, held-back tail included."""
        self._pm_flush()
        if self.pm_block_raw is None:
            return
        self._pm_render(final=True)
        self.pm_block_raw = None

    def pm_event(self, event):
        kind = event.get("type")
        if kind != "THINKING" and self.pm_state == "thinking":
            self.pm_state = "working"   # Thinking ended: text, a tool or the end follows.
        if kind in ("TEXT_DELTA", "ASSISTANT_TEXT", "TOOL_USE", "TURN_END", "RUNTIME_ERROR"):
            self.pm_warm = False        # The process answered: the cold start is over.
        if kind == "THINKING":
            # Status only: thinking content never reaches the pane (pm_runtime sends none).
            if self.pm_state == "working":
                self.pm_state = "thinking"
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
        elif kind == "TOOL_USE":
            name = event.get("name") if type(event.get("name")) is str else None
            self.pm_tools[event.get("tool_use_id")] = name
            self.append("  · tool " + chat_tui_pm.safe_name(name))
            self.pm_deltas = False
        elif kind == "TOOL_RESULT":
            self.append(chat_tui_pm.tool_result_text(event, self.pm_tools.get(event.get("tool_use_id"))))
        elif kind == "RUNTIME_ERROR":
            code = chat_tui_pm.safe_code(event.get("code"))
            self.pm_state = "error(" + code + ")"
            stderr = chat_tui_pm.stderr_text(event)
            self.append("llmtool: " + chat_tui_pm.error_text(code)
                        + ("\n  stderr (screened): " + stderr if stderr else ""))
            if code == "PM_SESSION_FORKED":
                # pm_runtime closed itself; the TUI no longer holds a usable connection.
                self.pm_runtime, self.pm_mode = None, False
        elif kind == "TURN_REPLAY":
            self.append(chat_tui_pm.replay_text(event))
        elif kind == "TURN_END":
            elapsed = max(0.0, time.monotonic() - (self.pm_turn_started or time.monotonic()))
            self.append(chat_tui_pm.turn_end_text(event, elapsed))   # append() flushes first

    def finish_pm_turn(self):
        self._pm_finalize()   # Nothing queued survives the turn (interrupt without TURN_END).
        self.pm_turn_active, self.pm_turn_started = False, None
        if self.pm_state in ("working", "thinking", "interrupting"):
            self.pm_state = "idle"
        self.app.invalidate()

    def interrupt_pm(self):
        if not self.pm_turn_active or self.pm_interrupted or self.pm_runtime is None:
            return
        self.pm_interrupted, self.pm_state = True, "interrupting"
        self.append("llmtool: interrupt sent to the PM; waiting for the turn to end")   # flushes
        try:
            self.pm_runtime.interrupt()
        except LLMToolError as exc:
            self.append("llmtool: " + chat_tui_pm.error_text(chat_tui_pm.error_code(exc)))
        except Exception:
            self.append("llmtool: " + chat_tui_pm.error_text(chat_tui_task.UNKNOWN))

    def close_pm(self):
        """Exit path: close the adopted runtime and any one a connect worker opened but has
        not handed over yet; a connect still inside open() closes its own (see _pm_connect)."""
        with self._pm_lock:
            self._pm_exiting = True
            runtimes = [self.pm_runtime, self._pm_unadopted]
            self.pm_runtime, self._pm_unadopted = None, None
        if self._pm_tick_handle is not None:
            self._pm_tick_handle.cancel()
            self._pm_tick_handle = None
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
        self.close_menu()  # Ctrl-C/Ctrl-D: also restores the default Esc timeout.
        if self.pm_turn_active and not self.pm_interrupted:
            self.interrupt_pm()
            self.append("llmtool: Ctrl-C again exits (the PM connection is closed on exit)")
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
