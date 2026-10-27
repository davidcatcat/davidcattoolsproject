"""Opt-in full-screen chat UI over the existing ChatSession (A3.5 UX-1/UX-2).

Display layer only: ChatSession still owns config, provider/skill selection,
memory-only history, requests and errors. No disk history, tools, shell, Git or
file writes. Model output is untrusted and rendered via terminal_safe_text.
prompt-toolkit is an optional dependency (``llmtool[tui]``); when it is absent
or stdin/stdout is not a terminal, the existing line chat runs instead, before
any UI starts. A UI failure never drops into, or replays input through, the REPL.
"""

import asyncio
import signal
import sys
import threading
import time

from . import chat_tui_status, chat_tui_task
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
  PageUp/PageDown       Scroll conversation (arrows also scroll when it is focused)
  F1                    Toggle this help (shown in place of the conversation)
  F2                    Toggle the last Task detail (Esc also closes it)
  F3                    Toggle the Task list (/task list): Up/Down choose, Enter opens the
                        detail, F5 or r refreshes, Esc closes; refreshes itself every
                        %d s while shown (local TaskStore read only, no AI call)
  Ctrl-C, Ctrl-D        Exit (Ctrl-D only with empty input)
A request in progress cannot be cancelled; exit waits for it to finish or time out.

Formal task commands (separate from ordinary chat; results come only from llmtool Core)
  /task mode PROFILE    Task mode: Enter submits each non-command draft as a formal PM
                        Task to PM provider PROFILE (no implicit default); "task>" prompt
  /task mode off        Back to chat mode: Enter sends to the chat provider again
  /task submit --provider PROFILE REQUEST
                        Submit one Task explicitly (also from chat mode, and to resend a
                        request Task mode refused as a repeat)
  /task show TASK_ID    Open a Task's detail
  /task list            Open the Task list (stage, update time, request) from TaskStore
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
    ("/task submit", "/task submit --provider ", "Submit a task: --provider PROFILE REQUEST"),
    ("/task show", "/task show ", "Show a task by TASK_ID"),
    ("/task mode", "/task mode ", "Task mode for drafts: PROFILE, or off"),
    ("/task list", "/task list", "Task list with status from TaskStore"),
)
MENU_LIMIT = 5  # Most picker rows shown at once; more matches scroll.
# Status bar fields in display order, and the order they drop when the row is too narrow.
STATUS_FIELDS = ("mode", "provider", "model", "state", "usage", "help")
STATUS_DROP_ORDER = ("help", "usage", "model", "provider")
ESC_SEQUENCE_TIMEOUT = 0.4  # Seconds a lone Esc waits for a following Enter (Esc Enter = newline).


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

    def __init__(self, session, input=None, output=None):
        from prompt_toolkit.application import Application
        from prompt_toolkit.document import Document
        from prompt_toolkit.filters import Condition, has_focus
        from prompt_toolkit.history import InMemoryHistory
        from prompt_toolkit.key_binding import KeyBindings
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
        self.worker = None
        self.worker_kind = None
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
        self._loop = None
        self._received_signal = None
        self.conversation = TextArea(
            text=terminal_safe_text("LLMTool interactive chat (full-screen)\n" + session.status()
                                    + "\nHistory stays in memory. F1 or /help for help; Ctrl-C exits."
                                    + "\nChat mode talks to the chat provider directly; it is not a PM."
                                    + " /task mode PROFILE sends drafts to a PM as formal Tasks."),
            read_only=True, scrollbar=True, wrap_lines=True, focus_on_click=True,
            height=Dimension(min=1, weight=1))
        self.conversation.buffer.cursor_position = len(self.conversation.text)
        self.input = TextArea(multiline=True, wrap_lines=True, focus_on_click=True,
                              prompt=lambda: "task> " if self.task_profile else "> ",
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
                                            filter=~showing_help & ~showing_detail & ~showing_list),
                       ConditionalContainer(self.help, filter=showing_help),
                       ConditionalContainer(self.detail, filter=showing_detail),
                       ConditionalContainer(self.list_window, filter=showing_list),
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

        @keys.add("f1")
        def _help(event):
            self.toggle_help()

        @keys.add("f2")
        def _detail(event):
            self.toggle_detail(not self.show_detail)

        @keys.add("f3")
        def _list(event):
            self.toggle_list(not self.show_list)

        @keys.add("f5", filter=showing_list)
        @keys.add("r", filter=list_focused)
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
            self.request_quit()

        @keys.add("c-d", filter=Condition(lambda: not self.input.text))
        def _eof(event):
            self.request_quit()

        self.app = Application(
            layout=Layout(root, focused_element=self.input), key_bindings=keys, full_screen=True,
            mouse_support=False, refresh_interval=0.5, input=input, output=output,
            style=Style.from_dict({"status": "reverse", "help": "italic", "rule": "fg:ansibrightblack",
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

    # -- rendering -----------------------------------------------------------------
    def status_text(self):
        session = self.session
        model = selected_provider(session.config).get("model")
        reported = session.last_reported_model
        state = session.state
        if self.busy:
            started = session.request_started
            if started is None:
                state = "working"
            else:
                state = "working %.1fs" % max(0.0, time.monotonic() - started)
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
            "help": "F1",
        }
        width = max(1, self.app.output.get_size().columns)
        # The mode field appears only in Task mode, so the chat footer is unchanged.
        shown = [key for key in STATUS_FIELDS if key != "mode" or self.task_profile]
        for name in STATUS_DROP_ORDER:
            if self._get_cwidth(" | ".join(fields[key] for key in shown)) <= width:
                break
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
        shown = sum(self.picker_layout()) > 0 or (self.show_detail and not self.show_help)
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
        if self.show_detail:
            self.toggle_detail(False)
        if self.show_list:
            self.unseen_output = True  # The list header says so; the list stays for browsing.
        buffer = self.conversation.buffer
        safe = terminal_safe_text(text)
        old = buffer.text
        follow = buffer.cursor_position >= len(old)
        new = old + ("\n\n" if old else "") + safe
        position = len(new) if follow else buffer.cursor_position
        buffer.set_document(self._Document(new, position), bypass_readonly=True)
        self.app.invalidate()

    def scroll(self, direction):
        buffer = self.conversation.buffer
        info = self.conversation.window.render_info
        rows = max(1, (info.window_height if info is not None else 10) - 1)
        document = buffer.document
        if direction < 0:
            buffer.cursor_position += document.get_cursor_up_position(count=rows)
        else:
            buffer.cursor_position += document.get_cursor_down_position(count=rows)
            if buffer.document.cursor_position_row >= buffer.document.line_count - 1:
                buffer.cursor_position = len(buffer.text)

    def toggle_focus(self):
        self.close_menu()  # Also restores the default Esc timeout.
        layout = self.app.layout
        pane = (self.help if self.show_help else self.detail if self.show_detail
                else self.list_window if self.show_list else self.conversation)
        layout.focus(pane if layout.has_focus(self.input) else self.input)

    def toggle_help(self):
        self.show_help = not self.show_help
        # Never leave focus on a hidden pane; the help pane itself scrolls when focused.
        self.app.layout.focus(self.input)
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
            self.unseen_output = False
            self.app.layout.focus(self.input)
        self._sync_picker()

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
        self.app.invalidate()

    def list_visible(self):
        """The list is on screen: not hidden behind the detail or help pane."""
        return self.show_list and not self.show_detail and not self.show_help

    def _list_tick(self):
        # Interval reads only while the list is actually visible (#510 review v2).
        if (self.list_visible() and self.list_reader is None and self.list_loaded is not None
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
            self.conversation.buffer.set_document(self._Document(""), bypass_readonly=True)
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
        if len(parts) == 3 and parts[1] == "mode":
            self.set_task_mode(parts[2])
            return
        self.append("usage: /task submit --provider PROFILE REQUEST\n"
                    "       /task show TASK_ID\n"
                    "       /task mode PROFILE|off\n"
                    "       /task list")

    def set_task_mode(self, profile):
        """Explicit chat/Task choice; the profile is checked by Core at submit time."""
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
            summary = chat_tui_status.summary_text(record)
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
        self.append("you:\n" + text)
        self._loop = asyncio.get_running_loop()
        self.worker = threading.Thread(target=self._request, args=(text, self._loop),
                                       name="llmtool-chat-request")
        self.worker.start()

    def _request(self, text, loop):
        """Worker thread: one immutable captured input; ChatSession owns rollback."""
        try:
            with background_cli_invocation():
                outcome = (True, self.session.send(text))
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

    def finish_send(self, outcome):
        """Runs as an event-loop callback, where an exception would bypass run()."""
        ok, text = outcome
        self._finish(lambda: self.append(("assistant:\n" if ok else "llmtool: ") + (text or "")))

    def _finish(self, render):
        self.busy = False
        self.worker = None
        self.worker_kind = None
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

    def request_quit(self):
        self.close_menu()  # Ctrl-C/Ctrl-D: also restores the default Esc timeout.
        if self.busy:
            if not self.quit_pending:
                self.quit_pending = True
                self.append("llmtool: exit requested; the in-flight request cannot be cancelled. "
                            "Exiting when it finishes or reaches the provider timeout.")
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
            result = self.app.run(handle_sigint=False)
        if self._received_signal == signal.SIGINT:
            raise KeyboardInterrupt() from None
        if self._received_signal is not None:
            raise CLIHostTermination(128 + self._received_signal) from None
        return 0 if result is None else result
