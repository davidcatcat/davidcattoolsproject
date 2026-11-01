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

    def open_terminal(self, environ, git_remote=None, items=None, details=None, rows=24,
                      refresh=None, columns=80):
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
        terminal = driver.Terminal(self.pyte, root, {"*": {"text": REPLY}}, rows, columns, self.slow,
                                   environ=environ, child_prefix=FAKE_BACKGROUND + (
                                       "" if refresh is None else
                                       "\n_status.REFRESH_SECONDS = %d\n" % refresh))
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
        self.assertIn("證據與出處", "\n".join(rows))                   # still the detail (W60 rows)
        self.assertIn("Esc 返回", rows[-1])
        self.assertNotIn("PM 回覆：第一則", "\n".join(rows))
        self.dump(terminal, "w59-task-detail")
        terminal.esc()
        terminal.wait_for(lambda rows: "證據與出處" not in "\n".join(rows), "Esc closed it")
        self.assertEqual(terminal.wait_stable("back in the chat") and terminal.pane()[:4], top)
        self.assertTrue(any(row.startswith("pm> 草稿 W59") for row in terminal.rows()),
                        terminal.rows())
        self.assertEqual(terminal.claude_messages()[-1], slow)       # the draft was not sent
        terminal.wheel(False, 200)
        terminal.wait_text("PM 回覆：第一則", "reply that arrived while the detail was open")

    def test_w59_empty_refresh_enter_and_escape_during_pm_turn(self):
        """[Mac PTY (fake provider)] W59: the background list empties during a PM turn while the
        panel is open; Enter shows UNKNOWN (no other Task, nothing sent), the draft and reading
        position stay, and the first Esc closes the panel without interrupting the PM"""
        now = datetime.now(timezone.utc)
        task_a, task_b = "a" * 32, "b" * 32
        items = [{"role": "Developer", "task": task_a, "pr": "#654", "state": "RUNNING",
                  "updated_at": (now - timedelta(minutes=12)).isoformat()},
                 {"role": "Reviewer", "task": task_b, "pr": "#661", "state": "WAITING_REVIEW",
                  "updated_at": (now - timedelta(minutes=3)).isoformat()}]
        terminal = self.open_terminal({"COLORFGBG": "15;0", "TERM_PROGRAM": "iTerm.app"},
                                      items=items, details={}, rows=16, refresh=1)
        listing = terminal.root / "background.json"
        terminal.connect_pm()
        terminal.wait_text("背景 2 · ↓ 查看", "status line background count")
        slow = "slow:%d 第一則" % (20 * self.slow)
        terminal.submit(slow)
        terminal.wait_for(lambda rows: "Esc 中斷" in rows[-1], "turn working")
        top = terminal.wait_stable("reading position") and terminal.pane()[:4]
        # Phase A: select B, the list empties, Enter -> UNKNOWN; the draft stays; Esc returns.
        terminal.send("\x1b[B")
        terminal.wait_text("背景工作 2 項", "↓ panel")
        terminal.send("\x1b[B")
        listing.write_text("[]", encoding="utf-8")
        terminal.wait_text("選中的 Task bbbbbbbb 已不在清單", "empty refresh keeps the panel", 10)
        terminal.send("\r")
        terminal.wait_text("Core 狀態：UNKNOWN（選中的 Task bbbbbbbb 已不在背景清單",
                           "UNKNOWN detail")
        self.assertNotIn("aaaaaaaa", terminal.text())
        terminal.send("草稿 W59")
        terminal.wait_text("草稿 W59", "draft typed in the detail")
        self.dump(terminal, "w59-empty-refresh-unknown")
        terminal.esc()
        terminal.wait_for(lambda rows: "Core 狀態" not in "\n".join(rows), "Esc closed the detail")
        self.assertEqual(terminal.wait_stable("back in the chat") and terminal.pane()[:4], top)
        self.assertTrue(any(row.startswith("pm> 草稿 W59") for row in terminal.rows()))
        self.assertIn("Esc 中斷", terminal.rows()[-1])                     # still working
        # Phase B: list back, panel open, list empties again; the first Esc only closes it.
        terminal.send("\x15")                                              # Ctrl-U: empty draft
        terminal.wait_for(lambda rows: any(row.rstrip() == "pm>" for row in rows), "draft cleared")
        listing.write_text(json.dumps(items), encoding="utf-8")
        terminal.wait_text("背景 2 · ↓ 查看", "list back", 10)
        terminal.send("\x1b[B")
        terminal.wait_text("背景工作 2 項", "↓ panel again")
        listing.write_text("[]", encoding="utf-8")
        terminal.wait_text("背景工作 0 項", "panel kept open on an empty list", 10)
        terminal.esc()
        terminal.wait_for(lambda rows: "背景工作" not in "\n".join(rows), "first Esc closed it")
        self.assertIn("Esc 中斷", terminal.rows()[-1])                     # not interrupted
        self.assertEqual(terminal.claude_messages(), [slow])               # nothing else sent
        terminal.wait_text("PM 回覆：第一則", "the turn completed", 40)
        self.assertNotIn("已中斷", terminal.text())

    def test_w60_detail_evidence_and_copy_task_id_at_80_and_50_columns(self):
        """[Mac PTY (fake provider)] W60: the detail's evidence rows (PR CONFLICT as stored, CI head,
        Reviewer source, read time) align in display columns at 80 and 50 columns; c copies the
        opened Task ID; an ABANDONED Task listed as RUNNING by the panel never shows RUNNING"""
        now = datetime.now(timezone.utc)
        task_a, task_b = "a" * 32, "b" * 32
        sha = "0123456789abcdef0123456789abcdef01234567"
        items = [{"role": "Developer", "task": task_a, "pr": "", "state": "RUNNING",
                  "updated_at": (now - timedelta(minutes=12)).isoformat()},
                 {"role": "Reviewer", "task": task_b, "pr": "#661 #662", "state": "WAITING_CI",
                  "updated_at": (now - timedelta(minutes=3)).isoformat()}]
        base = {"state": "PM_RESPONDED", "created_at": now.isoformat(),
                "updated_at": "2026-11-02T01:02:03Z", "pm_session_id": "pm-test"}
        details = {
            task_a: dict(base, task_id=task_a, request="W60 已放棄的工作",
                         developer={"state": "DEVELOPER_PENDING"},
                         abandonment={"reason": "需求取消", "pm_session_id": "pm-test",
                                      "abandoned_at": "2026-11-02T01:05:00Z"}),
            task_b: dict(base, task_id=task_b, request="W60 證據詳情",
                         developer={"state": "DEVELOPER_PENDING"},
                         review_dispatch_claim={"pr": 661, "state": "CLAIMED", "version": 1},
                         completion={"state": "WAITING_CI", "evidence": {"review_pr_ci": {
                             "stage": "REVIEW_PR_CI", "pr_number": 662, "pr_state": "OPEN",
                             "ci_head_sha": sha, "review_source": "PR_COMMENT",
                             "review_actor": "reviewer-bot"}}})}
        labels = ("PR", "PR 狀態", "CI head SHA", "Reviewer 來源", "讀取時間")
        for columns in (80, 50):
            with self.subTest(columns=columns):
                terminal = self.open_terminal({"COLORFGBG": "15;0"}, items=items,
                                              details=details, rows=60, columns=columns)
                terminal.connect_pm()
                terminal.wait_text("背景 2", "status line background count")
                terminal.send("\x1b[B")
                terminal.wait_text("背景工作 2 項", "↓ panel")
                terminal.send("\x1b[B")
                terminal.send("\r")
                terminal.wait_text("證據與出處", "evidence block of the selected Task")
                rows = self.dump(terminal, "w60-evidence-%d" % columns)
                self.assertIn("W60證據詳情", "".join(rows).replace(" ", ""))
                start = next(y for y, row in enumerate(rows) if row.startswith("證據與出處"))
                found, colons = {}, set()
                for y in range(start + 1, len(rows)):
                    for label in labels:
                        if (rows[y].startswith("  " + label) and label not in found
                                and rows[y][len(label) + 2] in " ："):
                            found[label] = y
                            colons.add(next(x for x in range(columns)
                                            if terminal.cell(y, x).data == "："))
                self.assertEqual(sorted(found), sorted(labels), rows)
                self.assertEqual(len(colons), 1, rows)              # values in one column
                block = "".join(rows[found["PR"]:found["讀取時間"] + 2]).replace(" ", "")
                for needle in ("CONFLICT", "#661（來源reviewclaim）",
                               "#662（來源completionreview_pr_ci.pr_number）", sha,
                               "PR_COMMENT", "reviewer-bot", "2026-11-02T01:02:03Z"):
                    self.assertIn(needle, block)
                self.assertIn("c複製TaskID", "".join(rows[:3]).replace(" ", ""))   # header hint
                if columns == 80:                     # at 50 the PM status bar drops its keys
                    self.assertIn("c 複製 Task ID", rows[-1])
                terminal.send("c")
                self.assertEqual(terminal.wait_clipboard(), task_b)
                if columns == 80:
                    terminal.wait_text("Task ID 已複製 32 字", "copy result in the status bar")
                self.assertFalse(any(row.startswith("pm> c") for row in terminal.rows()))
                terminal.esc()
                terminal.wait_for(lambda rows: "證據與出處" not in "\n".join(rows), "Esc closed")
                terminal.send("\x1b[B")
                terminal.wait_text("背景工作 2 項", "↓ panel again")
                terminal.send("\r")                                  # opens on the first row: A
                terminal.wait_text("Core 狀態：ABANDONED", "abandoned Task detail")
                rows = self.dump(terminal, "w60-abandoned-%d" % columns)
                self.assertIn("W60已放棄的工作", "".join(rows).replace(" ", ""))
                self.assertNotIn("RUNNING", "\n".join(rows))
                terminal.esc()
                terminal.stop()


    def test_w60_hostile_taskstore_text_reaches_the_terminal_escaped(self):
        """[Mac PTY (fake provider)] W60: TaskStore text with OSC 52, screen clear, OSC 8 to another
        site, C1 CSI and a bidi override reaches the real terminal byte stream only escaped;
        the only OSC 8 links in the detail are full PR URLs of the bound repository"""
        import re
        now = datetime.now(timezone.utc)
        task = "a" * 32
        pr_url = "https://github.com/davidcatcat/davidcattoolsproject/pull/"
        hostile = ("\x1b]52;c;c2VjcmV0\x07 \x1b[2J \x1b]8;;https://evil.example/\x1b\\點我"
                   "\x1b]8;;\x1b\\ \x9b31m \u202e")
        items = [{"role": "Developer", "task": task, "pr": "#661", "state": "RUNNING",
                  "updated_at": (now - timedelta(minutes=2)).isoformat()}]
        details = {task: {
            "task_id": task, "state": "PM_RESPONDED", "pm_session_id": "pm-test",
            "request": "W60 惡意" + hostile,
            "created_at": now.isoformat(), "updated_at": "2026-11-02T01:02:03Z",
            "developer": {"state": "DEVELOPER_PENDING"},
            "completion": {"state": "WAITING_CI", "evidence": {"review_pr_ci": {
                "stage": "REVIEW_PR_CI", "pr_number": 661, "pr_state": "OPEN",
                "ci_head_sha": "ab" * 20, "review_source": "PR_COMMENT",
                "review_actor": ("bot" + hostile + " " + pr_url
                                 + "9 https://evil.example/pull/1 #77")}}},
            "completion_events": [{"source": "LOCAL" + hostile, "state": "S",
                                   "received_at": now.isoformat()}]}}
        terminal = self.open_terminal({"COLORFGBG": "15;0", "TERM_PROGRAM": "iTerm.app"},
                                      git_remote="git@github.com:o/r.git", items=items,
                                      details=details, rows=60)
        terminal.connect_pm()
        terminal.wait_text("背景 1", "status line background count")
        before = len(terminal.raw_text())
        terminal.send("\x1b[B")
        terminal.wait_text("背景工作 1 項", "↓ panel")
        terminal.send("\r")
        terminal.wait_text("證據與出處", "detail with the hostile record")
        rows = self.dump(terminal, "w60-hostile")
        screen = "\n".join(rows)
        self.assertIn("\\x1b]52;c;c2VjcmV0\\x07", screen)              # visible, escaped
        self.assertIn("Reviewer 來源：GitHub PR_COMMENT；actor bot\\x1b]52", screen)
        raw = terminal.raw_text()[before:]
        for live in ("\x1b]52", "\x1b[2J", "\x07", "\x9b", "\u202e", "\x1b]8;;https://evil"):
            self.assertNotIn(live, raw)
        urls = set(re.findall(r"\x1b\]8;id=[^;]*;([^\x1b\x07]*)", raw))
        self.assertIn(pr_url + "9", urls)                               # a full PR URL links
        self.assertEqual({url for url in urls if not url.startswith(pr_url)}, set(), urls)
        self.assertLessEqual(urls, {pr_url + "9", pr_url + "661"})
        terminal.esc()

if __name__ == "__main__":
    unittest.main()
