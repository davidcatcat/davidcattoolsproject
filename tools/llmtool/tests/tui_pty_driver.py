"""Shared real-PTY driver for the chat TUI tests (Mac PTY, fake provider).

The child runs the real entry ``llmtool.entrypoint.main(["chat", "--tui"])`` in a pseudo-terminal
with a temp HOME; the chat provider is a fake client and the PM is a fake ``claude`` on PATH.
Evidence from this driver is "Mac PTY (fake provider)", never real PM acceptance.

Screen rows are read from the pyte buffer, not ``Screen.display``: pyte 0.8.2 does not erase the
other half of a wide character that is partly overwritten (a real terminal blanks it), and
``display`` then indexes an empty stub cell (IndexError at screens.py:241). The TUI redraws a
wrapped CJK row shifted by one column, so a snapshot taken while a frame is half fed (an
``os.read`` boundary inside that row) hit it in about 2 of 9 runs.
"""

import fcntl
import json
import os
import pty
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
import unittest
from pathlib import Path

LABEL = "Mac PTY (fake provider)"
PACKAGE = Path(__file__).resolve().parents[1]

CHILD_SOURCE = r'''
import json
import os
import sys
import time
sys.path.insert(0, os.environ["LLMTOOL_PACKAGE"])
from llmtool import chat

class FakeClient:
    """Chat provider: the reply for a question comes from FAKE_REPLIES (JSON file)."""
    def stream(self, messages):
        with open(os.environ["FAKE_REPLIES"], encoding="utf-8") as source:
            replies = json.load(source)
        question = messages[-1]["content"] if messages else ""
        spec = replies.get(question) or replies["*"]
        chunk, delay = int(spec.get("chunk", 1024)), float(spec.get("delay", 0))
        text = spec["text"]
        for start in range(0, len(text), chunk):
            if delay:
                time.sleep(delay)
            yield text[start:start + chunk]

chat.create_client = lambda config, value: FakeClient()
chat._streams = lambda client: True
from llmtool import entrypoint
sys.exit(entrypoint.main(["chat", "--tui"]))
'''

# A Claude Code stand-in speaking the stream-json protocol pm_runtime drives. Each user
# message is logged (FAKE_CLAUDE_LOG, one JSON line) before it is answered. The text picks
# the behaviour: "think:N ..." streams a thinking block, then waits N s before the answer;
# "slow:N ..." waits N s before the answer; "tools:N ..." runs described tools (W57).
# An interrupt control request ends the turn.
# Like Claude Code 2.1.312 it reports command_lifecycle frames for a message sent with a
# uuid: queued when read (also during a turn), started when its turn begins, completed
# after its result; cancel_async_message withdraws a message that has not started.
FAKE_CLAUDE = r'''#!PYTHON
import json, os, queue, sys, threading, time
args = sys.argv[1:]
session = args[args.index("--session-id") + 1] if "--session-id" in args else args[args.index("--resume") + 1]
cwd = os.getcwd()
encoded = "".join(c if c.isascii() and (c.isalnum() or c == "-") else "-" for c in cwd)
transcript = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects", encoded, session + ".jsonl")
if "--session-id" in args:
    os.makedirs(os.path.dirname(transcript), exist_ok=True)
    open(transcript, "a").close()
elif not os.path.exists(transcript):
    sys.stderr.write("No conversation found with session ID: " + session + "\n")
    sys.exit(1)
with open(os.environ["FAKE_CLAUDE_LOG"] + ".starts", "a", encoding="utf-8") as starts:
    starts.write(json.dumps({"argv": args, "at": time.time()}) + "\n")
inbox = queue.Queue()
lock = threading.Lock()
def out(value):
    value.setdefault("session_id", session)
    with lock:
        sys.stdout.write(json.dumps(value, ensure_ascii=False) + "\n")
        sys.stdout.flush()
def lifecycle(item, state):
    if item.get("uuid"):
        out({"type": "command_lifecycle", "command_uuid": item["uuid"], "state": state,
             "uuid": "frame-" + state})
def read():
    for raw in sys.stdin:
        item = json.loads(raw)
        if item.get("type") == "user":
            lifecycle(item, "queued")
        inbox.put(item)
    inbox.put(None)
threading.Thread(target=read, daemon=True).start()
def event(value):
    out({"type": "stream_event", "event": value})
pending = []
def wait(seconds):
    """Sleep, but return True as soon as an interrupt arrives."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            item = inbox.get(timeout=0.02)
        except queue.Empty:
            continue
        if item is None:
            sys.exit(0)
        if item.get("type") == "control_request":
            request = item.get("request") or {}
            if request.get("subtype") == "cancel_async_message":
                for waiting in list(pending):
                    if waiting.get("uuid") == request.get("message_uuid"):
                        pending.remove(waiting)
                        lifecycle(waiting, "cancelled")
                continue
            return True
        pending.append(item)
    return False
while True:
    item = pending.pop(0) if pending else inbox.get()
    if item is None:
        break
    if item.get("type") != "user":
        continue
    lifecycle(item, "started")
    text = "".join(part.get("text", "") for part in item["message"]["content"])
    with open(os.environ["FAKE_CLAUDE_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps({"text": text, "at": time.time()}, ensure_ascii=False) + "\n")
    with open(transcript, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"type": "user", "text": text}, ensure_ascii=False) + "\n")
    hold, thinking, body, tools = 0.0, False, text, 0.0
    head = text.split(" ", 1)[0]
    if head.startswith("tools:"):
        tools = float(head.split(":", 1)[1])
        body = text[len(head):].strip()
    elif head.startswith(("think:", "slow:")):
        thinking = head.startswith("think:")
        hold = float(head.split(":", 1)[1])
        body = text[len(head):].strip()
    out({"type": "system", "subtype": "init"})
    event({"type": "message_start", "message": {}})
    interrupted = False
    if thinking:
        event({"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}})
        event({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "PRIVATE_REASONING"}})
    interrupted = wait(hold)
    if tools:
        out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "SECRET_COMMAND --run", "description": "跑 focused 測試"}}]}})
        interrupted = wait(tools)
        out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1",
             "content": "SECRET_OUTPUT"}]}})
        out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t2", "name": "Read",
             "input": {"file_path": "docs/plan.md"}}]}})
        out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2",
             "content": "no such file", "is_error": True}]}})
    if thinking:
        event({"type": "content_block_stop", "index": 0})
    answer = "PM 回覆：" + body
    index = 1 if thinking else 0
    shown = ""
    if not interrupted:
        event({"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}})
        for start in range(0, len(answer), 6):
            event({"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": answer[start:start + 6]}})
            shown = answer[:start + 6]
            if wait(0.02):
                interrupted = True
                break
        event({"type": "content_block_stop", "index": index})
    if not interrupted:
        out({"type": "assistant", "message": {"content": [{"type": "text", "text": answer}]}})
    out({"type": "result", "subtype": "error_during_execution" if interrupted else "success",
         "result": shown if interrupted else answer, "is_error": False,
         "usage": {"input_tokens": 11, "output_tokens": 7}})
    lifecycle(item, "completed")
'''

FAKE_PBCOPY = r'''#!/bin/sh
# Records what llmtool puts on the clipboard; the Owner's real clipboard is never touched.
cat > "$FAKE_PBCOPY_FILE.part" && mv "$FAKE_PBCOPY_FILE.part" "$FAKE_PBCOPY_FILE"
echo copy >> "$FAKE_PBCOPY_FILE.count"
'''


def skip_reason():
    """Why the PTY suite cannot run here (None: it can). A skip is never a PASS."""
    if sys.platform != "darwin":
        return "PTY TUI evidence is admitted only on macOS"
    try:
        import pyte  # noqa: F401
    except ImportError:
        return "pyte is required to render the PTY terminal (put it on PYTHONPATH); NOT RUN"
    try:
        master, slave = pty.openpty()
        os.close(master)
        os.close(slave)
    except OSError:
        return "a usable pseudo-terminal is unavailable"
    return None


def require_pty():
    reason = skip_reason()
    if reason is not None:
        raise unittest.SkipTest(reason)
    import pyte
    return pyte


def screen_rows(screen):
    """Rows as a real terminal shows them, from the pyte buffer.

    A wide character whose second cell was overwritten, and a second-cell stub whose wide
    character was overwritten, are blank (one column each), as on a real terminal.
    """
    from wcwidth import wcwidth
    rows = []
    for y in range(screen.lines):
        line = screen.buffer[y]
        cells = [line[x].data for x in range(screen.columns)]
        text = []
        x = 0
        while x < screen.columns:
            data = cells[x]
            if not data:
                text.append(" ")   # orphaned second half of a wide character
                x += 1
                continue
            if wcwidth(data[0]) == 2:
                if x + 1 < screen.columns and cells[x + 1] != "":
                    text.append(" ")   # its second half was overwritten
                    x += 1
                    continue
                text.append(data)
                x += 2
                continue
            text.append(data)
            x += 1
        rows.append("".join(text).rstrip())
    return rows


def write_fake_tools(bin_dir):
    bin_dir.mkdir(parents=True, exist_ok=True)
    claude = bin_dir / "claude"
    claude.write_text(FAKE_CLAUDE.replace("PYTHON", sys.executable, 1), encoding="utf-8")
    pbcopy = bin_dir / "pbcopy"
    pbcopy.write_text(FAKE_PBCOPY, encoding="utf-8")
    for path in (claude, pbcopy):
        path.chmod(0o755)


class Terminal:
    """One real ``llmtool chat --tui`` in a pseudo-terminal rendered by pyte."""

    def __init__(self, pyte, root, replies, rows=24, columns=80, slow=1.0, environ=None,
                 child_prefix=""):
        self.slow = slow   # timeout multiplier (the load scenario runs on a busy Mac)
        # W56d: extra child environment (TERM_PROGRAM, COLORFGBG, ...) and code run before the
        # TUI starts (a fake data source); every byte the TUI wrote is kept in ``raw``.
        self.environ = dict(environ or {})
        self.child_prefix = child_prefix
        self.raw = bytearray()
        self.root = Path(os.path.realpath(root))   # no /var -> /private/var symlink (PM paths)
        self.screen = pyte.Screen(columns, rows)
        self.stream = pyte.ByteStream(self.screen)
        self.lock = threading.Lock()
        self.alive = True
        self.pid = self.master = None
        self.bin = self.root / "bin"
        self.pbcopy_file = self.root / "clipboard.txt"
        self.claude_log = self.root / "claude-log.jsonl"
        self.pm_cwd = self.root / "pm-cwd"
        self.pm_cwd.mkdir()
        write_fake_tools(self.bin)
        config = self.root / ".config" / "llmtool" / "config.yaml"
        config.parent.mkdir(parents=True)
        config.write_text(
            "active_provider: fake\nproviders:\n  fake:\n"
            "    base_url: https://example.invalid\n    model: pty-test\n"
            "    api_key_env: LLMTOOL_PTY_UNUSED\n",
            encoding="utf-8",
        )
        self.replies = self.root / "replies.json"
        self.replies.write_text(json.dumps(replies, ensure_ascii=False), encoding="utf-8")
        self._spawn(rows, columns)

    def _spawn(self, rows, columns):
        self.pid, self.master = pty.fork()
        if self.pid == 0:
            # Size the terminal before the TUI asks for it (fd 0 is the slave here).
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
            environment = os.environ.copy()
            environment.pop("TERM_PROGRAM", None)
            environment.update({
                "HOME": str(self.root),
                "CLAUDE_CONFIG_DIR": str(self.root / "claude-config"),
                "LLMTOOL_HOME": str(self.root / "llmtool-home"),
                "XDG_STATE_HOME": str(self.root / "state"),
                "PATH": str(self.bin) + os.pathsep + environment.get("PATH", "/usr/bin:/bin"),
                "TERM": "xterm-256color",
                "LANG": "en_US.UTF-8",
                "LC_ALL": "en_US.UTF-8",
                "LLMTOOL_PACKAGE": str(PACKAGE),
                "FAKE_REPLIES": str(self.replies),
                "FAKE_PBCOPY_FILE": str(self.pbcopy_file),
                "FAKE_CLAUDE_LOG": str(self.claude_log),
                "LLMTOOL_PTY_UNUSED": "placeholder",
            })
            environment.update(self.environ)
            os.chdir(self.root)
            os.execve(sys.executable, [sys.executable, "-c", self.child_prefix + CHILD_SOURCE],
                      environment)
        self.reader = threading.Thread(target=self._read_output, daemon=True)
        self.reader.start()

    def _read_output(self):
        while self.alive:
            try:
                data = os.read(self.master, 65536)
            except OSError:
                return
            if not data:
                return
            with self.lock:
                self.raw += data
                self.stream.feed(data)

    def stop(self):
        if self.master is None:
            return
        try:
            os.write(self.master, b"\x04")
        except OSError:
            pass
        try:
            os.kill(self.pid, signal.SIGTERM)
        except OSError:
            pass
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                found, _status = os.waitpid(self.pid, os.WNOHANG)
            except ChildProcessError:
                found = self.pid
            if found:
                break
            time.sleep(0.02)
        else:
            try:
                os.kill(self.pid, signal.SIGKILL)
                os.waitpid(self.pid, 0)
            except OSError:
                pass
        self.alive = False
        try:
            os.close(self.master)
        except OSError:
            pass
        self.master = None

    # -- screen ------------------------------------------------------------------------
    def rows(self):
        with self.lock:
            return screen_rows(self.screen)

    def text(self):
        return "\n".join(self.rows())

    def raw_text(self):
        with self.lock:
            return self.raw.decode("utf-8", "replace")

    def cell(self, y, x):
        """The pyte cell (data, fg, bg, bold, reverse, ...) at row y, column x."""
        with self.lock:
            return self.screen.buffer[y][x]

    def pane(self):
        """Conversation rows: everything above the status/input rule (a row of dashes)."""
        rows = self.rows()
        for index, row in enumerate(rows):
            if row.startswith("───") or row.startswith("---"):
                return rows[:index]
        return rows

    def wait_for(self, condition, description, timeout=15):
        deadline = time.monotonic() + timeout * self.slow
        while time.monotonic() < deadline:
            current = self.rows()
            if condition(current):
                return current
            time.sleep(0.025)
        raise AssertionError("timed out waiting for " + description + ": " + repr(self.rows()))

    def wait_text(self, needle, description=None, timeout=15):
        return self.wait_for(lambda rows: needle in "\n".join(rows), description or repr(needle),
                             timeout)

    def wait_stable(self, description, stable_for=0.3, timeout=15):
        deadline = time.monotonic() + timeout * self.slow
        previous = unchanged_since = None
        while time.monotonic() < deadline:
            current = self.rows()
            now = time.monotonic()
            if current == previous:
                if now - unchanged_since >= stable_for:
                    return current
            else:
                previous, unchanged_since = current, now
            time.sleep(0.025)
        raise AssertionError("screen did not stabilize after " + description + ": "
                             + repr(self.rows()))

    # -- input -------------------------------------------------------------------------
    def send(self, value):
        os.write(self.master, value.encode("utf-8"))

    def submit(self, line):
        self.send(line + "\r")

    def esc(self, wait=0.6):
        """A lone Esc: the TUI waits ESC_SEQUENCE_TIMEOUT (0.4 s) for a following Enter."""
        self.send("\x1b")
        time.sleep(wait)

    def wheel(self, up, count=1, x=40, y=10):
        code = 64 if up else 65
        self.send("".join("\x1b[<%d;%d;%dM" % (code, x, y) for _ in range(count)))

    def wheel_steps(self, up, count, x=40, y=10, gap=0.0):
        for _index in range(count):
            self.wheel(up, 1, x, y)
            if gap:
                time.sleep(gap)

    def press(self, x, y):
        self.send("\x1b[<0;%d;%dM" % (x, y))

    def drag(self, x, y):
        self.send("\x1b[<32;%d;%dM" % (x, y))

    def release(self, x, y):
        self.send("\x1b[<0;%d;%dm" % (x, y))

    def resize(self, rows, columns):
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        with self.lock:
            self.screen.resize(lines=rows, columns=columns)
        os.kill(self.pid, signal.SIGWINCH)

    # -- fakes -------------------------------------------------------------------------
    def clipboard(self):
        try:
            return self.pbcopy_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def wait_clipboard(self, stable_for=0.3, timeout=15):
        """The fake clipboard once the release copy is in (drag-time copies come first)."""
        deadline = time.monotonic() + timeout * self.slow
        previous, since = None, time.monotonic()
        while time.monotonic() < deadline:
            current = self.clipboard()
            if current != previous:
                previous, since = current, time.monotonic()
            elif current is not None and time.monotonic() - since >= stable_for:
                return current
            time.sleep(0.025)
        raise AssertionError("fake pbcopy did not settle: " + repr(previous))

    def claude_messages(self):
        try:
            lines = self.claude_log.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        return [json.loads(line)["text"] for line in lines if line.strip()]

    def claude_starts(self):
        try:
            lines = Path(str(self.claude_log) + ".starts").read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        return [json.loads(line)["argv"] for line in lines if line.strip()]

    def connect_pm(self):
        self.submit("/pm new " + str(self.pm_cwd))
        self.wait_text("/pm connect connects to it", "/pm new binding")
        self.submit("/pm connect")
        self.wait_text("PM mode", "/pm connect")
        self.wait_text("PM 歷史", "/pm connect history")


class CPUBurner:
    """Busy-loop processes (one per CPU by default) to load the Mac."""

    def __init__(self, count=None):
        self.count = count or os.cpu_count() or 4
        self.children = []

    def __enter__(self):
        for _index in range(self.count):
            self.children.append(subprocess.Popen([sys.executable, "-c", "while True: pass"]))
        return self

    def __exit__(self, *exc):
        for child in self.children:
            child.kill()
            child.wait()
        self.children = []
        return False
