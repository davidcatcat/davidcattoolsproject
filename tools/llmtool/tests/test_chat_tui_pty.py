"""Mac PTY regression coverage for the real chat TUI input/rendering path."""

import fcntl
import os
import pty
import re
import signal
import struct
import sys
import tempfile
import termios
import threading
import time
import unittest
from pathlib import Path


SOURCE = r'''
import os
import sys
import time
sys.path.insert(0, os.environ["LLMTOOL_PACKAGE"])
from llmtool import chat, chat_tui_view

class FakeClient:
    def stream(self, messages):
        reply = os.environ["FAKE_REPLY"]
        delay = float(os.environ.get("FAKE_DELAY", "0"))
        for start in range(0, len(reply), 1024):
            if delay:
                time.sleep(delay)
            yield reply[start:start + 1024]

chat.create_client = lambda config, value: FakeClient()
chat._streams = lambda client: True
chat.api_key = lambda config: "unused"

def fake_copy(text):
    with open(os.environ["FAKE_COPY_FILE"], "w", encoding="utf-8") as output:
        output.write(text)
    return True

chat_tui_view.copy_to_clipboard = fake_copy
from llmtool import entrypoint
sys.exit(entrypoint.main(["chat", "--tui"]))
'''


def _reply_text():
    rows = []
    for number in range(1, 91):
        marker = "第%03d段" % number
        rows.append((marker + " 中文 wrapped words ") * 10 + marker + " — END MARKER")
    return "\n".join(rows)


class ChatTUIPTYTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != "darwin":
            raise unittest.SkipTest("PTY TUI evidence is admitted only on macOS")
        try:
            import pyte
        except ImportError:
            raise unittest.SkipTest("pyte is required to render the PTY terminal") from None
        try:
            master, slave = pty.openpty()
            os.close(master)
            os.close(slave)
        except OSError:
            raise unittest.SkipTest("a usable pseudo-terminal is unavailable") from None
        cls.pyte = pyte

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.screen = self.pyte.Screen(80, 24)
        self.stream = self.pyte.ByteStream(self.screen)
        self.lock = threading.Lock()
        self.alive = True
        self.pid = None
        self.master = None
        config = self.root / ".config" / "llmtool" / "config.yaml"
        config.parent.mkdir(parents=True)
        config.write_text(
            "active_provider: fake\nproviders:\n  fake:\n"
            "    base_url: https://example.invalid\n    model: pty-test\n"
            "    api_key_env: LLMTOOL_PTY_UNUSED\n",
            encoding="utf-8",
        )
        self.copy_path = self.root / "clipboard.txt"
        delay = 0.4 if self._testMethodName == "test_streaming_keeps_scrolled_view_fixed_then_follows_from_bottom" else 0.0
        self._spawn(delay)
        self.wait_for(lambda rows: any("full-screen" in row for row in rows), "TUI startup")

    def tearDown(self):
        self._stop_child()
        self.temp.cleanup()

    def _stop_child(self):
        if self.master is not None:
            try:
                os.write(self.master, b"\x04")
            except OSError:
                pass
            if self.pid is not None:
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
            self.alive = False
            try:
                os.close(self.master)
            except OSError:
                pass
            self.master = None

    def _spawn(self, delay):
        self.pid, self.master = pty.fork()
        if self.pid == 0:
            environment = os.environ.copy()
            for name in ("TERM_PROGRAM",):
                environment.pop(name, None)
            environment.update({
                "HOME": str(self.root),
                "LLMTOOL_HOME": str(self.root / "llmtool-home"),
                "XDG_STATE_HOME": str(self.root / "state"),
                "TERM": "xterm-256color",
                "LANG": "en_US.UTF-8",
                "LC_ALL": "en_US.UTF-8",
                "LLMTOOL_PACKAGE": str(Path(__file__).resolve().parents[1]),
                "FAKE_REPLY": _reply_text(),
                "FAKE_DELAY": str(delay),
                "FAKE_COPY_FILE": str(self.copy_path),
            })
            os.execve(sys.executable, [sys.executable, "-c", SOURCE], environment)
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
                self.stream.feed(data)

    def rows(self):
        with self.lock:
            return [self.screen.display[row].rstrip() for row in range(self.screen.lines)]

    def wait_for(self, condition, description, timeout=15):
        deadline = time.monotonic() + min(timeout, 15)
        while time.monotonic() < deadline:
            current = self.rows()
            if condition(current):
                return current
            time.sleep(0.025)
        self.fail("timed out waiting for " + description + ": " + repr(self.rows()))

    def wait_for_stable_screen(self, description, stable_for=0.3, timeout=15):
        deadline = time.monotonic() + min(timeout, 15)
        previous = None
        unchanged_since = None
        while time.monotonic() < deadline:
            current = self.rows()
            now = time.monotonic()
            if current == previous:
                if unchanged_since is None:
                    unchanged_since = now
                elif now - unchanged_since >= stable_for:
                    return current
            else:
                previous = current
                unchanged_since = now
            time.sleep(0.025)
        self.fail("screen did not stabilize after " + description + ": " + repr(self.rows()))

    def send(self, value):
        os.write(self.master, value.encode("utf-8"))

    def wheel(self, up, count=1, x=40, y=10):
        code = 64 if up else 65
        for _index in range(count):
            self.send("\x1b[<%d;%d;%dM" % (code, x, y))

    def wheel_and_wait_stable(self, up, description):
        previous = self.rows()
        self.wheel(up)
        self.wait_for(lambda rows: rows != previous, description + " redraw")
        return self.wait_for_stable_screen(description)

    def send_question(self):
        self.send("pty question\r")

    def await_reply_end(self):
        return self.wait_for(lambda rows: any("END MARKER" in row for row in rows), "reply output")

    def test_wrapped_cjk_wheel_moves_one_row_and_round_trips(self):
        self.send_question()
        self.await_reply_end()
        self.wait_for_stable_screen("reply output")
        before = self.rows()
        count = 7
        for _index in range(count):
            previous = self.rows()
            current = self.wheel_and_wait_stable(True, "wheel-up")
            self.assertEqual(current[1], previous[0])
        for _index in range(count):
            previous = self.rows()
            current = self.wheel_and_wait_stable(False, "wheel-down")
            self.assertEqual(current[0], previous[1])
        self.assertEqual(self.rows(), before)

    def test_drag_at_top_edge_autoscrolls_and_copies_selected_panel_text(self):
        self.send_question()
        self.await_reply_end()
        self.wait_for_stable_screen("reply output")
        before = self.rows()
        self.send("\x1b[<0;12;9M")
        self.send("\x1b[<32;12;1M")
        time.sleep(0.35)
        moved = self.rows()
        self.assertNotEqual(moved[0], before[0], "edge drag should keep scrolling while held")
        self.send("\x1b[<0;12;1m")
        self.wait_for(lambda _rows: self.copy_path.exists(), "clipboard write")
        copied = self.copy_path.read_text(encoding="utf-8")
        self.assertGreater(len(copied), 20)
        self.assertIn(copied, _reply_text(), "clipboard must equal the selected reply text")

    def test_streaming_keeps_scrolled_view_fixed_then_follows_from_bottom(self):
        self.send_question()
        self.wait_for(lambda rows: any("第030段" in row for row in rows), "streamed backlog")
        self.wheel(True, 2)
        self.wait_for(lambda rows: "捲動" in " ".join(rows), "streaming scroll state")
        scrolled_rows = self.wait_for_stable_screen("streaming wheel-up batch")
        scrolled = scrolled_rows[0]
        time.sleep(1.0)
        self.assertTrue("捲動" in " ".join(self.rows()))
        self.assertEqual(self.rows()[0], scrolled)
        self.wheel(False, 200)
        self.wait_for(lambda rows: any("END MARKER" in row for row in rows), "stream completion")
        followed = self.wait_for_stable_screen("streaming wheel-down batch")
        self.assertIn("最新", " ".join(followed))

    def test_resize_preserves_first_visible_text_while_scrolled_up(self):
        self.send_question()
        self.await_reply_end()
        self.wait_for_stable_screen("reply output")
        self.wheel(True, 5)
        self.wait_for(lambda rows: "捲動" in " ".join(rows), "resize scroll state")
        original = self.wait_for_stable_screen("resize wheel-up batch")[0]
        original_paragraph = re.search(r"第\d{3}段", original)
        self.assertIsNotNone(original_paragraph, "visible row should identify its paragraph")
        packed = struct.pack("HHHH", 24, 45, 0, 0)
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, packed)
        os.kill(self.pid, signal.SIGWINCH)
        with self.lock:
            self.screen.resize(lines=24, columns=45)
        resized = self.wait_for_stable_screen("resized render")
        self.assertLess(len(resized[0]), len(original))
        self.assertIn(original_paragraph.group(), resized[0], "resize should preserve the same paragraph at the top")


if __name__ == "__main__":
    unittest.main()
