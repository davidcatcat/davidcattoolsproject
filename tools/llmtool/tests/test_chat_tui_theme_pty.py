"""W56d in a real Mac PTY: dark/light themes, OSC 8 links and the PM status line ↓ panel.

The real ``llmtool chat --tui`` runs in a pseudo-terminal (tui_pty_driver: temp HOME, fake chat
provider, fake ``claude`` PM). The PM's background Tasks come from a fake
``chat_tui_status.read_background`` (a JSON file), so no real TaskStore is read. Set
LLMTOOL_TUI_EVIDENCE_DIR to keep the screen dumps.
"""

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tui_pty_driver as driver  # noqa: E402

FAKE_BACKGROUND = r'''
import json as _json, os as _os, sys as _sys
_sys.path.insert(0, _os.environ["LLMTOOL_PACKAGE"])
from llmtool import chat_tui_status as _status

def _fake_background(key, store=None):
    with open(_os.environ["FAKE_BACKGROUND"], encoding="utf-8") as source:
        return _json.load(source)

_status.read_background = _fake_background
'''
REPLY = ("改好了：見 llmtool/chat_tui.py:123 與 #654。\n\n"
         "```diff\n+ 新增一行\n- 刪除一行\n```\n\nEND MARKER")
OSC8_OPEN = "\x1b]8;id=llmtool-"
OSC8_CLOSE = "\x1b]8;;\x1b\\"


def _items():
    now = datetime.now(timezone.utc)
    return [{"role": "Developer", "task": "7f3a9c21e0b44d1e", "pr": "#654", "state": "RUNNING",
             "updated_at": (now - timedelta(minutes=12)).isoformat()},
            {"role": "Reviewer", "task": "b81e04aa9d3c4f20", "pr": "#661",
             "state": "WAITING_REVIEW", "updated_at": (now - timedelta(minutes=3)).isoformat()}]


class ThemePTYTests(unittest.TestCase):
    def setUp(self):
        self.pyte = driver.require_pty()
        self.slow = float(os.environ.get("LLMTOOL_PTY_SLOW", "1"))
        self.evidence = os.environ.get("LLMTOOL_TUI_EVIDENCE_DIR")

    def open_terminal(self, environ, git_remote=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(os.path.realpath(temp.name))
        if git_remote:
            for args in (("init", "-q"), ("remote", "add", "origin", git_remote)):
                subprocess.run(("git",) + args, cwd=root, check=True, stdin=subprocess.DEVNULL)
        background = root / "background.json"
        background.write_text(json.dumps(_items()), encoding="utf-8")
        environ = dict(environ, FAKE_BACKGROUND=str(background))
        terminal = driver.Terminal(self.pyte, root, {"*": {"text": REPLY}}, 24, 80, self.slow,
                                   environ=environ, child_prefix=FAKE_BACKGROUND)
        self.addCleanup(terminal.stop)
        terminal.wait_text("full-screen", "TUI startup")
        return terminal

    def dump(self, terminal, name):
        rows = terminal.wait_stable(name)
        if self.evidence:
            Path(self.evidence).mkdir(parents=True, exist_ok=True)
            Path(self.evidence, name + ".txt").write_text("\n".join(rows) + "\n", encoding="utf-8")
            Path(self.evidence, name + ".ans").write_text(self.ansi(terminal), encoding="utf-8")
        return rows

    @staticmethod
    def ansi(terminal):
        """The pyte screen with its colours as 24-bit SGR (``cat`` it in a terminal)."""
        names = {"black": "000000", "red": "cd0000", "green": "00cd00", "brown": "cdcd00",
                 "blue": "0000ee", "magenta": "cd00cd", "cyan": "00cdcd", "white": "e5e5e5",
                 "brightblack": "7f7f7f"}
        lines = []
        with terminal.lock:
            for y in range(terminal.screen.lines):
                line, last = [], None
                for x in range(terminal.screen.columns):
                    cell = terminal.screen.buffer[y][x]
                    if not cell.data:
                        continue
                    key = (cell.fg, cell.bg, cell.bold, cell.reverse, cell.italics)
                    if key != last:
                        sgr = ["0"]
                        for code, colour in (("38", cell.fg), ("48", cell.bg)):
                            value = names.get(colour, colour)
                            if len(value) == 6:
                                sgr.append("%s;2;%d;%d;%d" % (code, int(value[:2], 16),
                                                              int(value[2:4], 16), int(value[4:], 16)))
                        sgr += [flag for flag, on in (("1", cell.bold), ("7", cell.reverse),
                                                      ("3", cell.italics)) if on]
                        line.append("\x1b[" + ";".join(sgr) + "m")
                        last = key
                    line.append(cell.data)
                lines.append("".join(line) + "\x1b[0m")
        return "\n".join(lines) + "\n"

    def ask(self, terminal):
        terminal.submit("看一下")
        terminal.wait_text("END MARKER", "reply")
        terminal.wait_for(lambda rows: "ready" in rows[-1], "reply finished")

    def panel(self, terminal, name):
        """PM mode: the lead counts the fake background work; Down lists it; Esc closes it."""
        terminal.connect_pm()
        rows = terminal.wait_text("背景 2 · ↓ 查看", "status line background count")
        self.assertTrue(rows[-1].startswith("▸▸ PM 模式 · auto · 背景 2 · ↓ 查看"), rows[-1])
        terminal.send("\x1b[B")
        terminal.wait_text("背景工作 2 項", "↓ panel")
        rows = self.dump(terminal, name)
        joined = "\n".join(rows)
        self.assertRegex(joined, r"Developer +7f3a9c21 +#654 +RUNNING +1[23]m")
        self.assertRegex(joined, r"Reviewer +b81e04aa +#661 +WAITING_REVIEW +[34]m")
        terminal.esc()
        terminal.wait_for(lambda rows: "背景工作" not in "\n".join(rows), "panel closed by Esc")

    def test_dark_theme_without_osc8(self):
        """[Mac PTY (fake provider)] dark theme, no OSC 8 on Terminal.app, ↓ background panel"""
        terminal = self.open_terminal({"COLORFGBG": "15;0", "TERM_PROGRAM": "Apple_Terminal"},
                                      git_remote="git@github.com:o/r.git")
        self.ask(terminal)
        self.dump(terminal, "dark-chat")
        self.assertTrue(terminal.cell(23, 0).reverse)            # dark status bar: reverse
        self.panel(terminal, "dark-pm-background-panel")
        self.assertNotIn("\x1b]8;", terminal.raw_text())          # never on an unsupported terminal

    def test_light_theme_with_osc8_links_and_copy(self):
        """[Mac PTY (fake provider)] light theme, OSC 8 links on iTerm2, copy returns the source"""
        terminal = self.open_terminal({"COLORFGBG": "0;15", "TERM_PROGRAM": "iTerm.app"},
                                      git_remote="git@github.com:o/r.git")
        self.ask(terminal)
        rows = self.dump(terminal, "light-chat")
        status = terminal.cell(23, 0)
        self.assertEqual((status.bg, status.reverse), ("dadada", False))   # THEME_LIGHT status.bar
        raw = terminal.raw_text()
        self.assertIn(OSC8_OPEN, raw)
        self.assertIn(";file://", raw)
        self.assertIn("/llmtool/chat_tui.py\x1b\\", raw)
        self.assertIn(";https://github.com/o/r/issues/654\x1b\\", raw)
        self.assertIn(OSC8_CLOSE, raw)
        # Each linked cell is its own link: one open per visible cell of the two references.
        self.assertGreaterEqual(raw.count(OSC8_OPEN), len("llmtool/chat_tui.py:123") + len("#654"))
        # Drag over the reference: the clipboard gets the text, not the escape sequences.
        from wcwidth import wcswidth
        y = next(index for index, row in enumerate(rows) if "llmtool/chat_tui.py:123" in row)
        start = rows[y].index("llmtool/chat_tui.py:123")
        x = wcswidth(rows[y][:start]) + 1
        end = x + len("llmtool/chat_tui.py:123")
        terminal.press(x, y + 1)
        terminal.drag(end - 4, y + 1)
        terminal.drag(end, y + 1)
        terminal.release(end, y + 1)
        self.assertEqual(terminal.wait_clipboard(), "llmtool/chat_tui.py:123")
        self.panel(terminal, "light-pm-background-panel")
        # /theme dark switches live and is saved with mode 0600.
        terminal.submit("/theme dark")
        terminal.wait_text("Theme: dark (drawn dark)", "/theme dark")
        terminal.wait_for(lambda _rows: terminal.cell(23, 0).reverse, "dark status bar")
        settings = terminal.root / ".local" / "state" / "llmtool" / "tui-settings.json"
        self.assertEqual(json.loads(settings.read_text()), {"links": "on", "theme": "dark"})
        self.assertEqual(stat.S_IMODE(settings.stat().st_mode), 0o600)
        self.dump(terminal, "light-then-dark-pm")


if __name__ == "__main__":
    unittest.main()
