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

def _fake_detail(task_id, store=None):
    with open(_os.environ["FAKE_TASK_DETAIL"], encoding="utf-8") as source:
        return _json.load(source)[task_id]

_status.read_task_detail = _fake_detail
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

    def open_terminal(self, environ, git_remote=None, items=None, details=None, rows=24):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(os.path.realpath(temp.name))
        if git_remote:
            for args in (("init", "-q"), ("remote", "add", "origin", git_remote)):
                subprocess.run(("git",) + args, cwd=root, check=True, stdin=subprocess.DEVNULL)
        background = root / "background.json"
        background.write_text(json.dumps(_items() if items is None else items), encoding="utf-8")
        detail = root / "task-detail.json"
        detail.write_text(json.dumps(details or {}), encoding="utf-8")
        environ = dict(environ, FAKE_BACKGROUND=str(background), FAKE_TASK_DETAIL=str(detail))
        terminal = driver.Terminal(self.pyte, root, {"*": {"text": REPLY}}, rows, 80, self.slow,
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


    def test_w59_background_task_detail_during_a_pm_turn(self):
        """[Mac PTY (fake provider)] W59: Enter on the ↓ panel opens a read-only Task detail during a
        PM turn; the turn finishes while it is open; Esc returns to the same reading position
        with the draft kept; the PR URL is an OSC 8 link on iTerm2"""
        now = datetime.now(timezone.utc)
        task_a, task_b = "a" * 32, "b" * 32
        items = [{"role": "Developer", "task": task_a, "pr": "#654", "state": "RUNNING",
                  "updated_at": (now - timedelta(minutes=12)).isoformat()},
                 {"role": "Reviewer", "task": task_b, "pr": "#661", "state": "WAITING_REVIEW",
                  "updated_at": (now - timedelta(minutes=3)).isoformat()}]
        details = {task_id: {"task_id": task_id, "request": "W59 PTY 詳情 " + task_id[:4],
                             "state": "PM_RESPONDED", "developer": None,
                             "created_at": now.isoformat(), "updated_at": now.isoformat(),
                             "review_dispatch_claim": {"pr": pr, "state": "CLAIMED", "version": 1}}
                   for task_id, pr in ((task_a, 654), (task_b, 661))}
        terminal = self.open_terminal({"COLORFGBG": "15;0", "TERM_PROGRAM": "iTerm.app"},
                                      git_remote="git@github.com:o/r.git", items=items,
                                      details=details, rows=16)
        terminal.connect_pm()
        terminal.wait_text("背景 2 · ↓ 查看", "status line background count")
        terminal.submit("第零則 " + "長訊息內容，" * 40)
        terminal.wait_text("PM 回覆：第零則", "first PM reply", 20)
        terminal.wait_for(lambda rows: "待命" in rows[-1], "first turn end", 20)
        slow = "slow:%d 第一則" % (8 * self.slow)
        terminal.submit(slow)
        terminal.wait_for(lambda rows: "Esc 中斷" in rows[-1], "second turn working")
        bottom = terminal.pane()
        terminal.wheel(True, 4)
        terminal.wait_for(lambda rows: terminal.pane()[4:8] == bottom[0:4], "scrolled back 4 rows")
        before = terminal.wait_stable("reading position")
        top = terminal.pane()[:4]
        terminal.send("\x1b[B")
        terminal.wait_text("背景工作 2 項", "↓ panel")
        terminal.send("\x1b[B")
        terminal.send("\r")
        rows = terminal.wait_text("Task 詳情（唯讀）", "detail opened")
        terminal.wait_text("W59 PTY 詳情 bbbb", "detail of the selected Task")
        self.assertIn("Esc 中斷", before[-1])
        terminal.send("\x1b[6~")                                   # PageDown in the detail
        terminal.wait_text("PR: #661", "PR line scrolled into view")
        self.assertIn(";" + "https://github.com/davidcatcat/davidcattoolsproject/pull/661\x1b\\",
                      terminal.raw_text())
        terminal.send("草稿 W59")
        terminal.wait_text("草稿 W59", "draft typed while the detail is open")
        # The PM turn keeps going and ends while the detail is shown.
        rows = terminal.wait_for(lambda rows: "待命" in rows[-1], "turn end with detail open", 30)
        self.assertIn("最近事件", "\n".join(rows))                     # still the detail
        self.assertIn("Esc 返回", rows[-1])
        self.assertNotIn("PM 回覆：第一則", "\n".join(rows))
        self.dump(terminal, "w59-task-detail")
        terminal.esc()
        terminal.wait_for(lambda rows: "最近事件" not in "\n".join(rows), "Esc closed it")
        self.assertEqual(terminal.wait_stable("back in the chat") and terminal.pane()[:4], top)
        self.assertTrue(any(row.startswith("pm> 草稿 W59") for row in terminal.rows()),
                        terminal.rows())
        self.assertEqual(terminal.claude_messages()[-1], slow)       # the draft was not sent
        terminal.wheel(False, 200)
        terminal.wait_text("PM 回覆：第一則", "reply that arrived while the detail was open")

if __name__ == "__main__":
    unittest.main()
