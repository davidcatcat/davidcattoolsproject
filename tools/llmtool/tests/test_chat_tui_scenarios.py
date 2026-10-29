"""User-scenario suite for the real chat TUI: Mac PTY (fake provider).

Each test drives the real ``llmtool chat --tui`` entry in a pseudo-terminal (pty.fork + pyte, temp
HOME) with a fake chat provider, a fake ``claude`` PM and a fake ``pbcopy`` on PATH; see
tui_pty_driver. This is fake-provider evidence, never real PM acceptance. Scenarios that need
code not on this branch skip with the reason (NOT RUN); a skip is never a PASS.
"""

import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tui_pty_driver as driver  # noqa: E402

SOURCE = driver.PACKAGE / "llmtool"
# W55 (PR #652): Esc pauses the PM queue; Enter on an empty draft sends the next one.
HAS_W55 = "def _pm_resume" in (SOURCE / "chat_tui.py").read_text(encoding="utf-8")
# W56 (PRs #653, #654): Markdown/table/code/diff renderer wired into the conversation pane.
HAS_W56 = ((SOURCE / "tui_markdown.py").exists() and
           "def render_markdown" in (SOURCE / "chat_tui_view.py").read_text(encoding="utf-8"))
W55_MISSING = "NOT RUN: needs W55 (Esc pauses the PM queue, PR #652), not on this branch"
W56_MISSING = "NOT RUN: needs W56 (Markdown renderer in the pane, PRs #653/#654), not on this branch"


def _short_lines(count=120):
    """One unwrapped, numbered line per row (80 columns), so scroll distances are exact."""
    return "\n".join("行%03d 短行 short line" % number for number in range(1, count + 1)) + "\nEND MARKER"


def _chinese_markdown():
    parts = ["# 長篇中文回覆", "", "以下是**重點整理**，每段都會自動換行：", ""]
    for number in range(1, 41):
        parts.append("## 第%02d節" % number)
        parts.append("- 項目：說明文字（第%02d段）" % number + "這是一段很長的中文內容，用來測試寬字元換行，"
                     "包含標點符號、English words 與數字 12345，" * 2 + "段尾%02d" % number)
        parts.append("")
    parts.append("全文結束 END MARKER")
    return "\n".join(parts)


RICH_MARKDOWN = "\n".join([
    "## 摘要", "", "這是**粗體**與`行內碼`。", "",
    "| 欄位 | 數值 |", "| --- | --- |", "| 甲 | 100 |", "| 乙 | 200 |", "",
    "```python", "def hello():", "    return '你好'", "```", "",
    "```diff", "-刪除的行", "+新增的行", "```", "", "完成 END MARKER",
])

REPLIES = {
    "short": {"text": _short_lines()},
    "cjk": {"text": _chinese_markdown()},
    "stream": {"text": _short_lines(), "chunk": 25, "delay": 0.12},
    "second": {"text": "第二個回覆 SECOND MARKER"},
    "rich": {"text": RICH_MARKDOWN},
    "*": {"text": "default reply END MARKER"},
}

LINE = re.compile(r"行(\d{3})")


def _line_numbers(rows):
    return [int(match.group(1)) for row in rows for match in [LINE.match(row)] if match]


def _pm_working(rows):
    """The pane's working line (first turn: 暖機中…, then 處理中…/思考中…)."""
    return re.search(r"^PM (暖機中|處理中|思考中)… \d+s", "\n".join(rows), re.M) is not None


def _pm_idle(rows):
    """The status row says the PM turn is over (main: idle; W56 layout: 待命/已中斷).

    W56d leads the PM status row with "▸▸ PM 模式"; main starts it with "PM".
    """
    return rows[-1].startswith(("PM", "▸▸ PM")) and any(word in rows[-1] for word in ("idle", "待命", "已中斷"))


def _pm_interrupted(rows):
    """The interrupted turn's end line (main: "PM turn INTERRUPTED"; W56 layout: "已中斷 · N 秒")."""
    text = "\n".join(rows)
    return "PM turn INTERRUPTED" in text or "已中斷 · " in text


def _width(row):
    from wcwidth import wcswidth
    return wcswidth(row)


class ScreenRowsTests(unittest.TestCase):
    """The driver reads pyte rows the way a real terminal shows them (CJK wheel flake root cause)."""

    def setUp(self):
        try:
            import pyte
        except ImportError:
            self.skipTest("pyte is required to render the PTY terminal (put it on PYTHONPATH); NOT RUN")
        self.screen = pyte.Screen(10, 1)
        self.stream = pyte.ByteStream(self.screen)

    def test_wide_character_shifted_left_by_one_column(self):
        # The flaky run caught the TUI redrawing a wrapped CJK row one column to the left.
        self.stream.feed("abc\x1b[1;5H中".encode("utf-8"))
        self.stream.feed("\x1b[1;4H中".encode("utf-8"))
        with self.assertRaises(IndexError):
            self.screen.display   # pyte 0.8.2 leaves the old second half as an empty cell
        self.assertEqual(driver.screen_rows(self.screen), ["abc中"])

    def test_wide_character_overwritten_by_a_narrow_one(self):
        self.stream.feed("中文x".encode("utf-8"))
        self.stream.feed("\x1b[1;1Ha".encode("utf-8"))
        self.assertEqual(driver.screen_rows(self.screen), ["a 文x"])

    def test_wide_second_half_overwritten(self):
        self.stream.feed("中文".encode("utf-8"))
        self.stream.feed("\x1b[1;2Hb".encode("utf-8"))
        self.assertEqual(driver.screen_rows(self.screen), [" b文"])


class TUIScenarioCase(unittest.TestCase):
    slow = 1.0

    @classmethod
    def setUpClass(cls):
        cls.pyte = driver.require_pty()

    def setUp(self):
        self.terminals = []

    def tearDown(self):
        for terminal in self.terminals:
            terminal.stop()

    def open_terminal(self, rows=24, columns=80):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        terminal = driver.Terminal(self.pyte, temp.name, REPLIES, rows, columns, self.slow)
        self.terminals.append(terminal)
        terminal.wait_text("full-screen", "TUI startup")
        return terminal

    def ask(self, terminal, question, marker="END MARKER"):
        terminal.submit(question)
        terminal.wait_text(marker, question + " reply")
        # The status row leaves "working" up to one refresh (0.5 s) after the text: since
        # #630 saves history, finish_send no longer shares the last chunk's redraw.
        terminal.wait_for(lambda rows: "working" not in rows[-1], question + " turn end")
        return terminal.wait_stable(question + " reply")

    # -- scenario bodies shared with the load run (S13) --------------------------------
    def scenario_wheel_and_trackpad(self, terminal):
        bottom = self.ask(terminal, "short")
        pane = terminal.pane()
        top = _line_numbers(pane)[0]
        # One wheel notch moves exactly one row.
        terminal.wheel(True)
        terminal.wait_for(lambda rows: _line_numbers(terminal.pane())[:1] == [top - 1], "one-row wheel")
        self.assertIn("捲動", terminal.text())
        # A trackpad flick is a burst of wheel reports in one write: one row each.
        terminal.wheel(True, 10)
        terminal.wait_for(lambda rows: _line_numbers(terminal.pane())[:1] == [top - 11], "trackpad burst")
        # A slower trackpad swipe: reports spread over time.
        terminal.wheel_steps(True, 5, gap=0.02)
        terminal.wait_for(lambda rows: _line_numbers(terminal.pane())[:1] == [top - 16], "trackpad swipe")
        terminal.wheel(False, 16)
        terminal.wait_for(lambda rows: rows == bottom, "back to the bottom")
        self.assertIn("最新", terminal.text())

    def scenario_copy(self, terminal):
        self.ask(terminal, "short")
        pane = terminal.pane()
        terminal.press(1, 5)
        terminal.drag(6, 7)
        terminal.release(6, 7)
        terminal.wait_text("已複製", "copied status")
        copied = terminal.wait_clipboard()
        self.assertTrue(copied.startswith(pane[4]), copied)
        self.assertIn("\n" + pane[5] + "\n", copied)
        self.assertTrue(pane[6].startswith(copied.rsplit("\n", 1)[1]), copied)
        self.assertIn(copied, _short_lines(), "clipboard must equal the selected reply text")

    def scenario_queue_fifo(self, terminal):
        terminal.connect_pm()
        first = "slow:%d 第一則" % (4 * self.slow)
        terminal.submit(first)
        terminal.wait_for(_pm_working, "first turn working")
        for number, text in enumerate(("第二則", "第三則", "第四則"), 1):
            terminal.submit(text)
            terminal.wait_text("已排入佇列（第 %d 則）" % number, "queued draft %d" % number)
        terminal.wait_for(lambda _rows: len(terminal.claude_messages()) == 4, "queue drained", 40)
        terminal.wait_text("PM 回覆：第四則", "last queued reply", 20)
        self.assertEqual(terminal.claude_messages(), [first, "第二則", "第三則", "第四則"])
        self.assertEqual(len(terminal.claude_starts()), 1, "one PM process serves the whole queue")


class TUIUserScenarioTests(TUIScenarioCase):
    """Mac PTY (fake provider) user scenarios S1–S13 (W56 Owner work package C)."""

    def test_s01_long_chinese_markdown_answer(self):
        """[Mac PTY (fake provider)] S1 long Chinese answer with Markdown wraps and loses no text"""
        terminal = self.open_terminal()
        rows = self.ask(terminal, "cjk")
        for row in rows:
            self.assertLessEqual(_width(row), 80, row)
        self.assertIn("段尾40", terminal.text())
        # Walk the whole reply from the top: every section and paragraph end is shown intact.
        terminal.wheel(True, 400)
        terminal.wait_text("長篇中文回覆", "reply top")
        seen = set()
        for _page in range(80):
            pane = terminal.wait_stable("page")
            text = "\n".join(pane)
            seen.update(re.findall(r"第(\d{2})節", text))
            seen.update("end" + found for found in re.findall(r"段尾(\d{2})", text))
            if "全文結束" in text:
                break
            terminal.wheel(False, 15)
        expected = {"%02d" % n for n in range(1, 41)} | {"end%02d" % n for n in range(1, 41)}
        self.assertEqual(seen, expected)

    def test_s02_trackpad_and_wheel_scrolling(self):
        """[Mac PTY (fake provider)] S2 wheel notch and trackpad bursts move one row per report"""
        self.scenario_wheel_and_trackpad(self.open_terminal())

    def test_s03_scroll_up_keeps_reading_position(self):
        """[Mac PTY (fake provider)] S3 scrolled up, typing and new output keep the reading position"""
        terminal = self.open_terminal()
        self.ask(terminal, "short")
        terminal.wheel(True, 30)
        terminal.wait_text("捲動", "scrolled state")
        terminal.wait_stable("scrolled up")
        reading_pane = terminal.pane()
        terminal.send("草稿")
        terminal.wait_text("> 草稿", "draft echo")
        self.assertEqual(terminal.pane(), reading_pane, "typing a draft must not move the pane")
        terminal.send("\x7f\x7f")
        terminal.submit("second")
        time.sleep(1.0)
        self.assertEqual(terminal.pane(), reading_pane, "new output must not move a scrolled pane")
        terminal.wheel(False, 400)
        terminal.wait_text("SECOND MARKER", "new reply at the bottom")
        self.assertIn("最新", terminal.text())

    def test_s04_scroll_while_reply_streams(self):
        """[Mac PTY (fake provider)] S4 scrolling during a streamed reply holds, then follows"""
        terminal = self.open_terminal()
        terminal.submit("stream")
        terminal.wait_for(lambda rows: max(_line_numbers(rows) or [0]) >= 30, "streamed backlog")
        terminal.wheel(True, 3)
        terminal.wait_text("捲動", "streaming scroll state")
        time.sleep(0.3)   # the status row ticks while streaming: compare the pane only
        held = terminal.pane()
        for _sample in range(3):
            time.sleep(0.5)
            self.assertEqual(terminal.pane(), held, "the pane moved while scrolled up")
        self.assertNotIn("END MARKER", terminal.text(), "the stream should still be running")
        self.assertIn("新輸出", terminal.text(), "status should flag output below the view")
        terminal.wheel(False, 200)
        start = max(_line_numbers(terminal.wait_text("最新", "back at the bottom")) or [0])
        terminal.wait_for(lambda rows: max(_line_numbers(rows) or [0]) > start + 5,
                          "following the stream again")
        terminal.wait_text("END MARKER", "stream end", 30)
        self.assertIn("最新", terminal.text())

    def test_s05_drag_select_across_pages(self):
        """[Mac PTY (fake provider)] S5 an edge drag scrolls more than a page and copies it all"""
        terminal = self.open_terminal()
        self.ask(terminal, "short")
        last = _line_numbers(terminal.pane())[-1]
        terminal.press(3, 20)
        terminal.drag(3, 1)
        terminal.wait_for(lambda rows: (_line_numbers(rows) or [999])[0] < last - 45, "edge autoscroll", 20)
        terminal.release(3, 1)
        terminal.wait_text("已複製", "copied status")
        copied = terminal.wait_clipboard()
        self.assertGreater(copied.count("\n"), 2 * 21, "selection should span more than two pages")
        self.assertIn(copied, _short_lines(), "clipboard must be contiguous reply text")

    def test_s06_copy_to_clipboard(self):
        """[Mac PTY (fake provider)] S6 a drag copies exactly the selection through pbcopy"""
        terminal = self.open_terminal()
        self.scenario_copy(terminal)
        count = Path(str(terminal.pbcopy_file) + ".count").read_text(encoding="utf-8").split()
        self.assertGreaterEqual(len(count), 1)

    def test_s07_resize_and_reflow(self):
        """[Mac PTY (fake provider)] S7 narrower, shorter and wider terminals reflow at the bottom"""
        terminal = self.open_terminal()
        self.ask(terminal, "cjk")
        tall = len(terminal.pane())
        for rows, columns in ((16, 50), (30, 100), (24, 80)):
            terminal.resize(rows, columns)
            terminal.wait_for(lambda current, columns=columns: any(
                row in ("-" * columns, "─" * columns) for row in current), "%dx%d rule" % (rows, columns))
            screen = terminal.wait_stable("%dx%d render" % (rows, columns))
            self.assertEqual(len(screen), rows)
            for row in screen:
                self.assertLessEqual(_width(row), columns, row)
            self.assertIn("全文結束 END MARKER", terminal.text(), "bottom must follow after resize")
            self.assertTrue(any("最新" in row for row in screen[-2:]), screen[-2:])
            if rows == 16:
                self.assertLess(len(terminal.pane()), tall)

    def test_s08_long_pm_think_shows_working_line(self):
        """[Mac PTY (fake provider)] S8 a long PM think shows a ticking working line, never the thought"""
        terminal = self.open_terminal()
        terminal.connect_pm()
        terminal.submit("think:3.5 長考題")
        for second in (1, 2, 3):
            terminal.wait_text("PM 思考中… %ds" % second, "working line at %ds" % second)
        self.assertNotIn("PRIVATE_REASONING", terminal.text())
        terminal.wait_text("PM 回覆：長考題", "reply after thinking")
        terminal.wait_for(_pm_idle, "turn end")
        text = terminal.text()
        self.assertNotIn("PM 思考中", text, "the working line must be replaced by the reply")
        self.assertNotIn("PRIVATE_REASONING", text)

    def test_s09_three_queued_drafts_sent_fifo(self):
        """[Mac PTY (fake provider)] S9 three drafts queued during a turn go out FIFO, one per turn"""
        self.scenario_queue_fifo(self.open_terminal())

    @unittest.skipUnless(HAS_W55, W55_MISSING)
    def test_s10_esc_interrupts_and_pauses_queue(self):
        """[Mac PTY (fake provider)] S10 Esc interrupts the PM turn and pauses the queue (W55)"""
        terminal = self.open_terminal()
        terminal.connect_pm()
        terminal.submit("slow:6 第一則")
        terminal.wait_for(_pm_working, "first turn working")
        terminal.submit("第二則")
        terminal.wait_text("已排入佇列（第 1 則）")
        terminal.submit("第三則")
        terminal.wait_text("已排入佇列（第 2 則）")
        terminal.esc()
        terminal.wait_for(_pm_interrupted, "interrupted turn end")
        terminal.wait_for(lambda rows: re.search("佇列已?暫停", "\n".join(rows)), "queue paused notice")
        time.sleep(1.5)
        self.assertEqual(terminal.claude_messages(), ["slow:6 第一則"], "Esc must pause the queue")
        terminal.submit("")
        terminal.wait_text("PM 回覆：第二則", "Enter sends the next queued draft")
        terminal.wait_for(_pm_idle, "second turn end")
        time.sleep(1.0)
        self.assertEqual(terminal.claude_messages(), ["slow:6 第一則", "第二則"])
        terminal.submit("/pm queue send")
        terminal.wait_text("PM 回覆：第三則", "/pm queue send resumes")
        self.assertEqual(terminal.claude_messages(), ["slow:6 第一則", "第二則", "第三則"])

    def test_s11_pm_interrupt_reconnect_restores_history(self):
        """[Mac PTY (fake provider)] S11 an interrupted PM session reconnects with its history"""
        terminal = self.open_terminal()
        terminal.connect_pm()
        terminal.submit("第一題")
        terminal.wait_text("PM 回覆：第一題")
        terminal.wait_for(_pm_idle, "first turn end")
        terminal.submit("slow:6 第二題")
        terminal.wait_for(_pm_working, "second turn working")
        terminal.esc()
        terminal.wait_for(_pm_interrupted, "interrupted turn end")
        terminal.submit("/pm disconnect")
        terminal.wait_text("PM disconnected", "disconnect")
        terminal.submit("/pm connect")
        terminal.wait_text("PM 歷史（最近 2 輪，存在本機）", "restored history")
        joined = "\n".join(terminal.wait_stable("history"))
        self.assertNotIn("(history)", joined)          # W57: the live layout, no prefix
        self.assertIn("已中斷 ·", joined)              # the interrupted turn's usage line
        self.assertIn("PM 回覆：第一題", joined)
        terminal.submit("第三題")
        terminal.wait_text("PM 回覆：第三題", "turn after reconnect")
        starts = terminal.claude_starts()
        self.assertEqual(len(starts), 2)
        self.assertIn("--session-id", starts[0])
        self.assertIn("--resume", starts[1], "reconnect must resume the same session")
        self.assertEqual(starts[0][starts[0].index("--session-id") + 1],
                         starts[1][starts[1].index("--resume") + 1])
        self.assertEqual(terminal.claude_messages(), ["第一題", "slow:6 第二題", "第三題"])

    @unittest.skipUnless(HAS_W56, W56_MISSING)
    def test_s12_markdown_table_code_diff_render(self):
        """[Mac PTY (fake provider)] S12 Markdown, a table, a code block and a diff render (W56)"""
        terminal = self.open_terminal()
        self.ask(terminal, "rich")
        text = terminal.text()
        self.assertIn("粗體", text)
        self.assertNotIn("**粗體**", text, "bold markers must not show raw")
        self.assertNotIn("```", text, "code fences must not show raw")
        self.assertNotIn("| --- |", text, "the table rule must not show raw")
        self.assertTrue(any("甲" in row and "100" in row for row in terminal.rows()), text)
        self.assertIn("def hello():", text)
        self.assertIn("return '你好'", text)
        self.assertTrue(any("刪除的行" in row for row in terminal.rows()), text)
        self.assertTrue(any("新增的行" in row for row in terminal.rows()), text)


    def test_s14_w57_tool_lines_history_status_and_hidden_task_mode(self):
        """[Mac PTY (fake provider)] S14 tool lines say what they do, /pm history, /status (W57)"""
        terminal = self.open_terminal(rows=30, columns=100)
        dumps = []

        def dump(name):
            dumps.append("=== " + name + " ===\n" + "\n".join(terminal.wait_stable(name)))

        terminal.submit("/status")
        terminal.wait_text("連線：未檢查", "chat /status")
        status = terminal.text()
        self.assertIn("實際模型：尚未呼叫", status)
        self.assertNotIn("not probed", status)
        dump("/status in chat mode")
        terminal.submit("/task mode profile x")
        terminal.wait_text("Task 模式是開發測試用；要派工請用 /pm connect 和 PM 說", "hidden /task")
        dump("/task without the dev flag")
        terminal.connect_pm()
        terminal.submit("tools:%d 看進度" % (3 * self.slow))
        terminal.wait_text("▸ 執行中：跑 focused 測試… 1s", "live tool line")
        terminal.wait_text("▸ 執行中：跑 focused 測試… 2s", "the seconds advance")
        dump("live tool line")
        terminal.wait_for(_pm_idle, "tool turn end", 30)
        terminal.wait_text("PM 回覆：看進度", "reply after the tools")
        done = terminal.text()
        self.assertRegex(done, "▸ 使用工具 2 次（1 次失敗）：✗ Read docs/plan.md（[0-9]+s，失敗）")
        dump("finished tool line")
        terminal.send("\x1b[18~")   # F7
        terminal.wait_text("▾ 使用工具 2 次", "F7 expands")
        expanded = terminal.text()
        self.assertRegex(expanded, "✓ 跑 focused 測試（[0-9]+s）")
        self.assertNotIn("SECRET_COMMAND", expanded)
        self.assertNotIn("SECRET_OUTPUT", expanded)
        dump("F7 expanded")
        terminal.submit("/pm history")
        terminal.wait_for(lambda rows: "PM 歷史（最近 1 輪，存在本機）" in
                          terminal.text().rsplit("> /pm history", 1)[-1], "/pm history")
        history = terminal.text()
        self.assertNotIn("(history)", history)
        self.assertIn("▸ 用過的工具：Bash、Read", history)
        dump("/pm history")
        terminal.submit("/status")
        terminal.wait_text("狀態（PM 模式）", "PM /status")
        self.assertIn("權限模式（本機設定）：auto", terminal.text())
        dump("/status in PM mode")
        out = os.environ.get("LLMTOOL_PTY_DUMP")
        if out:
            Path(out).write_text("\n\n".join(dumps) + "\n", encoding="utf-8")


class TUILoadScenarioTests(TUIScenarioCase):
    """Mac PTY (fake provider) S13: scenarios S2, S6 and S9 while every CPU runs a busy loop."""

    slow = 3.0

    def test_s13_scenarios_stable_under_cpu_load(self):
        """[Mac PTY (fake provider)] S13 S2/S6/S9 pass while a CPU burner loads the Mac"""
        idle = os.getloadavg()[0]
        with driver.CPUBurner() as burner:
            time.sleep(5)
            loads = [os.getloadavg()[0]]
            for name, body in (("S2", self.scenario_wheel_and_trackpad),
                               ("S6", self.scenario_copy),
                               ("S9", self.scenario_queue_fifo)):
                with self.subTest(scenario=name):
                    body(self.open_terminal())
                loads.append(os.getloadavg()[0])
        sys.stderr.write("\nS13 load: %d burner processes; load1 %.2f before, %s during\n"
                         % (burner.count, idle, " ".join("%.2f" % value for value in loads)))
        self.assertGreater(max(loads), 1.0, "the burner should have loaded the Mac")


if __name__ == "__main__":
    unittest.main()
