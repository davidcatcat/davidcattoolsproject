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
                        choose, Enter completes the draft to the choice without running it
                        (a draft already equal to the choice runs, as without the picker),
                        Esc closes the picker; Esc Enter still inserts a newline
  Tab, Shift-Tab        Switch focus between input and conversation
  PageUp/PageDown       Scroll conversation (arrows also scroll when it is focused)
  F1                    Toggle this help (shown in place of the conversation)
  Ctrl-C, Ctrl-D        Exit (Ctrl-D only with empty input)
A request in progress cannot be cancelled; exit waits for it to finish or time out.

Formal task commands (separate from ordinary chat)
  /task submit --provider PROFILE REQUEST
  /task show TASK_ID"""

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
)
MENU_LIMIT = 5
ESC_SEQUENCE_TIMEOUT = 0.4  # Seconds a lone Esc waits for a following Enter (Esc Enter = newline).


def slash_matches(text):
    """Commands matching a one-line slash draft: prefix matches first, then substring."""
    if "\n" in text or not text.startswith("/"):
        return []
    query = text.lower()
    prefix = [c for c in SLASH_COMMANDS if c[0].startswith(query)]
    inside = [c for c in SLASH_COMMANDS if c not in prefix and len(query) > 2 and query[1:] in c[0]]
    return (prefix + inside)[:MENU_LIMIT]


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
        self._loop = None
        self._received_signal = None
        self.conversation = TextArea(
            text=terminal_safe_text("LLMTool interactive chat (full-screen)\n" + session.status()
                                    + "\nHistory stays in memory. F1 or /help for help; Ctrl-C exits."),
            read_only=True, scrollbar=True, wrap_lines=True, focus_on_click=True,
            height=Dimension(min=1, weight=1))
        self.conversation.buffer.cursor_position = len(self.conversation.text)
        self.input = TextArea(multiline=True, wrap_lines=True, prompt="> ", focus_on_click=True,
                              height=lambda: Dimension(
                                  min=min(4, max(1, self.input.buffer.document.line_count)),
                                  max=min(4, max(1, self.input.buffer.document.line_count))),
                              history=InMemoryHistory())
        self.help = TextArea(text=KEY_HELP + "\n\n" + HELP, read_only=True, scrollbar=True,
                             wrap_lines=True, style="class:help",
                             height=Dimension(min=1, weight=1))
        showing_help = Condition(lambda: self.show_help)
        menu_active = Condition(lambda: bool(self.menu_items))
        # Opened only by typed/pasted insertion, so recalled history never opens it
        # and Up/Down keep recalling input history when the menu is closed.
        self.input.buffer.on_text_insert += lambda _buffer: self._refresh_menu(True)
        self.input.buffer.on_text_changed += lambda _buffer: self._refresh_menu(False)
        self.menu_window = Window(
            FormattedTextControl(self.menu_text), style="class:menu",
            height=lambda: Dimension.exact(max(1, len(self.menu_items))), wrap_lines=False)
        self.rule_window = Window(height=1, char="-", style="class:rule")
        self.status_window = Window(FormattedTextControl(self.status_text), style="class:status",
                                   height=Dimension.exact(1), wrap_lines=False)
        root = HSplit([ConditionalContainer(self.conversation, filter=~showing_help),
                       ConditionalContainer(self.help, filter=showing_help),
                       self.rule_window,
                       ConditionalContainer(self.menu_window, filter=menu_active),
                       self.input, self.status_window],
                      height=Dimension(min=1, weight=1))

        keys = KeyBindings()
        input_focused = has_focus(self.input)

        @keys.add("enter", filter=input_focused)
        def _submit(event):
            if not self.accept_menu():
                self.submit()

        @keys.add("up", filter=input_focused & menu_active, eager=True)
        def _menu_up(event):
            self.move_menu(-1)

        @keys.add("down", filter=input_focused & menu_active, eager=True)
        def _menu_down(event):
            self.move_menu(1)

        # Not eager: an eager lone Esc would fire before Esc Enter (newline) is recognised,
        # closing the picker and letting the Enter submit. Lone Esc fires after the timeout.
        @keys.add("escape", filter=input_focused & menu_active)
        def _menu_close(event):
            self.close_menu()

        @keys.add("c-j", filter=input_focused)
        @keys.add("escape", "enter", filter=input_focused)
        def _newline(event):
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
                                   "menu.current": "reverse"}))
        # prompt_toolkit waits timeoutlen (1s) after a lone Esc in case Esc Enter follows;
        # that made dismissing the picker feel stuck. Shortened only while the picker is
        # open, so the Esc Enter window is unchanged otherwise.
        self._default_timeoutlen = self.app.timeoutlen

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
            if self._get_cwidth(value) <= width:
                return value
            result = []
            used = 0
            for char in value:
                char_width = self._get_cwidth(char)
                if used + char_width > width - 1:
                    break
                result.append(char)
                used += char_width
            return "".join(result) + "…"

        # Keep a single status row at common terminal widths. Prefer the actual
        # reported model when available; keep the profile, current state and
        # usage visible without allowing long names to expand the footer.
        parts = [
            "p:" + compact(session.provider, 10),
            "m:" + compact(reported or model or "default", 14),
            compact(state, 14),
            "t:" + compact(_tokens(session.last_usage), 14),
            "F1",
        ]
        return " | ".join(parts)

    def menu_text(self):
        rows = []
        for index, (label, _fill, summary) in enumerate(self.menu_items):
            style = "class:menu.current" if index == self.menu_index else "class:menu"
            rows.append((style, " %-14s %s " % (label, summary)))
            rows.append(("", "\n"))
        return rows[:-1]

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
        self.menu_items = items
        self.app.timeoutlen = ESC_SEQUENCE_TIMEOUT if items else self._default_timeoutlen

    def move_menu(self, direction):
        if self.menu_items:
            self.menu_index = (self.menu_index + direction) % len(self.menu_items)

    def close_menu(self):
        self._set_menu([])

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
        """Add untrusted text; follow new output only if the caret was at the end."""
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
        layout = self.app.layout
        pane = self.help if self.show_help else self.conversation
        layout.focus(pane if layout.has_focus(self.input) else self.input)

    def toggle_help(self):
        self.show_help = not self.show_help
        # Never leave focus on a hidden pane; the help pane itself scrolls when focused.
        self.app.layout.focus(self.input)

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
        self.input.buffer.reset(append_to_history=True)
        if text.lstrip().startswith("/"):
            self.run_command(text)
        else:
            self.start_send(text)

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
        self.append("usage: /task submit --provider PROFILE REQUEST\n"
                    "       /task show TASK_ID")

    def start_task(self, action, first, request):
        self.busy = True
        self.worker_kind = "task"
        self.append("llmtool: task intake only; Developer dispatch is not implied")
        self._loop = asyncio.get_running_loop()
        self.worker = threading.Thread(target=self._task_request,
                                       args=(action, first, request, self._loop),
                                       name="llmtool-chat-task")
        self.worker.start()

    def _task_request(self, action, first, request, loop):
        """Run existing task submit/show APIs with task-local captured output."""
        import io
        from argparse import Namespace
        from .task_submission_cli import run_task_show, run_task_submit

        output = io.StringIO()
        try:
            if action == "submit":
                with background_cli_invocation():
                    code = run_task_submit(
                        Namespace(provider=first, request=[request]), stdout=output)
            else:
                code = run_task_show(Namespace(task_id=first), stdout=output)
            rendered = output.getvalue().strip()
            outcome = (code == 0, rendered or "task command returned no result")
        except LLMToolError as exc:
            outcome = (False, str(exc))
        except BaseException:
            outcome = (False, "task command failed; details omitted")
        try:
            loop.call_soon_threadsafe(self.finish_send, outcome)
        except RuntimeError:
            pass

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

    def finish_send(self, outcome):
        """Runs as an event-loop callback, where an exception would bypass run()."""
        ok, text = outcome
        kind = self.worker_kind
        self.busy = False
        self.worker = None
        self.worker_kind = None
        if self._received_signal is not None:
            self.app.exit(result=0)
            return
        try:
            if kind == "task":
                prefix = "task result:\n" if ok else "llmtool task command failed:\n"
            else:
                prefix = "assistant:\n" if ok else "llmtool: "
            self.append(prefix + (text or ""))
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
