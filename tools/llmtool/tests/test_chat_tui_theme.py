"""W56d: themes, the TUI settings file, OSC 8 links and the PM status line with background work."""

import asyncio
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmtool import chat_tui, chat_tui_status, chat_tui_theme, chat_tui_view
from llmtool.errors import LLMToolError
from tests.test_chat_tui import HAVE_TOOLKIT, Base

if HAVE_TOOLKIT:
    from prompt_toolkit.data_structures import Size
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

ESC = "\x1b"
SESSION = "abcdef01-2345-4789-8abc-def012345678"


def _record(task_id, developer_state="DEVELOPER_DISPATCHING", updated="2026-10-30T01:00:00Z",
            session=SESSION, **extra):
    record = {"task_id": task_id, "pm_session_id": "pm-" + session, "state": "PM_RESPONDED",
              "developer": {"state": developer_state}, "updated_at": updated,
              "backend_session_semantics": "ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL",
              "pm_provenance": {"source": "ACTIVE_PM_RUNTIME", "provider_invoked": False}}
    record.update(extra)
    return record


class ThemeChoiceTests(unittest.TestCase):
    def test_auto_follows_colorfgbg_and_defaults_dark(self):
        for value, theme in (("15;0", "dark"), ("0;15", "light"), ("0;default;7", "light"),
                             ("7;8", "dark"), ("12;4", "dark"), ("0;11", "light"),
                             ("", "dark"), ("default;default", "dark"), ("x", "dark")):
            with self.subTest(value=value):
                self.assertEqual(chat_tui_theme.auto_theme({"COLORFGBG": value}), theme)
        self.assertEqual(chat_tui_theme.auto_theme({}), "dark")
        self.assertEqual(chat_tui_theme.resolve_theme("light", {"COLORFGBG": "15;0"}), "light")
        self.assertEqual(chat_tui_theme.resolve_theme("dark", {"COLORFGBG": "0;15"}), "dark")
        self.assertEqual(chat_tui_theme.resolve_theme("auto", {"COLORFGBG": "0;15"}), "light")

    def test_token_style_per_theme_and_mono(self):
        dark = chat_tui_view.token_style("diff.add", True, "dark")
        light = chat_tui_view.token_style("diff.add", True, "light")
        self.assertNotEqual(dark, light)
        self.assertEqual(chat_tui_view.token_style("diff.add", False, "light"),
                         chat_tui_view.THEME_MONO["diff.add"])
        self.assertEqual(chat_tui_view.token_style("md.nonsense", True, "light"), "")
        # The light table never uses ANSI yellow or bright black (unreadable on white).
        for token, style in chat_tui_view.THEME_LIGHT.items():
            with self.subTest(token=token):
                self.assertNotIn("ansiyellow", style)
                self.assertNotIn("ansibrightblack", style)


class SettingsFileTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "state" / "llmtool" / "tui-settings.json"

    def test_defaults_when_missing_and_round_trip_with_mode_0600(self):
        self.assertEqual(chat_tui_theme.load_settings(self.path), {"theme": "auto", "links": "on"})
        chat_tui_theme.save_settings({"theme": "light", "links": "off", "other": 1}, self.path)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(chat_tui_theme.load_settings(self.path), {"theme": "light", "links": "off"})
        self.assertEqual(self.path.read_text(), '{"links": "off", "theme": "light"}')
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["tui-settings.json"])

    def test_malformed_foreign_and_linked_files_are_ignored(self):
        self.path.parent.mkdir(parents=True)
        for content in ("not json", '["dark"]', '{"theme": "neon", "links": "maybe"}',
                        "x" * (chat_tui_theme.SETTINGS_MAX_BYTES + 1)):
            with self.subTest(content=content[:20]):
                self.path.write_text(content)
                self.assertEqual(chat_tui_theme.load_settings(self.path),
                                 {"theme": "auto", "links": "on"})
        target = self.path.parent / "elsewhere.json"
        target.write_text('{"theme": "light"}')
        self.path.unlink()
        self.path.symlink_to(target)
        self.assertEqual(chat_tui_theme.load_settings(self.path)["theme"], "auto")

    def test_save_replaces_atomically(self):
        chat_tui_theme.save_settings({"theme": "dark", "links": "on"}, self.path)
        before = self.path.stat().st_ino
        chat_tui_theme.save_settings({"theme": "light", "links": "on"}, self.path)
        self.assertNotEqual(self.path.stat().st_ino, before)   # renamed in, never rewritten


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.linker = chat_tui_theme.Linker("/repo", "owner/tools", host="mac")

    def urls(self, text):
        return [(text[low:high], url) for low, high, url in self.linker.links(text, 0, len(text))]

    def test_osc8_only_on_known_terminals(self):
        for environ, expected in (({"TERM_PROGRAM": "iTerm.app"}, True),
                                  ({"TERM_PROGRAM": "WezTerm"}, True),
                                  ({"TERM_PROGRAM": "WarpTerminal"}, True),
                                  ({"TERM": "xterm-kitty"}, True),
                                  ({"TERM_PROGRAM": "Apple_Terminal"}, False),
                                  ({"TERM_PROGRAM": "vscode"}, False),
                                  ({"TERM_PROGRAM": "iTerm.app", "TMUX": "/tmp/t"}, False),
                                  ({"TERM_PROGRAM": "iTerm.app", "TERM": "screen-256color"}, False),
                                  ({}, False)):
            with self.subTest(environ=environ):
                self.assertIs(chat_tui_theme.osc8_supported(environ), expected)

    def test_file_and_issue_references(self):
        found = self.urls("see llmtool/chat_tui.py:123 and #654, also ./a b/c.md:2:5 or /abs/x.py:9")
        self.assertEqual(found, [
            ("llmtool/chat_tui.py:123", "file://mac/repo/llmtool/chat_tui.py"),
            ("#654", "https://github.com/owner/tools/issues/654"),
            ("b/c.md:2:5", "file://mac/repo/b/c.md"),
            ("/abs/x.py:9", "file://mac/abs/x.py")])
        self.assertEqual(self.urls("README.md:4"), [("README.md:4", "file://mac/repo/README.md")])

    def test_text_that_only_looks_like_a_reference_stays_plain(self):
        for text in ("example.com:443", "at 12:30", "v1.5:30", "https://h/x/y.py:3", "#12abc",
                     "a&#12;", "issue#12", "x.py: 3", "/path/x.py", "dir/file.py:12x"):
            with self.subTest(text=text):
                self.assertEqual(self.urls(text), [])

    def test_no_repository_slug_no_issue_links_and_urls_are_escaped(self):
        linker = chat_tui_theme.Linker("/my repo", None, host="mac")
        text = "#5 and 文件/說明.md:3"
        self.assertEqual([(text[a:b], u) for a, b, u in linker.links(text, 0, len(text))],
                         [("文件/說明.md:3", "file://mac/my%20repo/%E6%96%87%E4%BB%B6/"
                                             "%E8%AA%AA%E6%98%8E.md")])
        self.assertTrue(all(32 < ord(c) < 127 for c in linker.file_url("a\x1b]b.py")))

    def test_a_reference_crossing_the_row_is_found_from_either_row(self):
        text = "x " * 10 + "tools/llmtool/chat_tui_view.py:950 end"
        low = text.index("tools")
        for start, end in ((0, low + 5), (low + 5, len(text))):
            with self.subTest(start=start):
                self.assertEqual([(a, b) for a, b, _u in self.linker.links(text, start, end)],
                                 [(low, low + len("tools/llmtool/chat_tui_view.py:950"))])
        self.assertEqual(self.linker.links(text, 0, 5), [])

    @staticmethod
    def git_answers(root):
        def run(args, **kwargs):
            class Done:
                returncode = 0
                stdout = {"config": "git@github.com:davidcatcat/davidcattoolsproject.git\n",
                          "rev-parse": root + "\n"}[args[1]]
            return Done()
        return run

    def test_remote_slug(self):
        with tempfile.TemporaryDirectory() as root, \
                patch("subprocess.run", side_effect=self.git_answers(root)):
            self.assertEqual(chat_tui_theme.repo_info(),
                             (os.path.realpath(root), "davidcatcat/davidcattoolsproject"))
        for remote, slug in (("https://github.com/a/b", "a/b"), ("https://github.com/a/b.git", "a/b"),
                             ("https://gitlab.com/a/b.git", None), ("", None)):
            match = chat_tui_theme._GITHUB_REMOTE.match(remote)
            self.assertEqual(match.group(1) if match else None, slug)


    def test_repo_info_is_empty_when_git_fails_or_answers_junk(self):
        import subprocess
        from unittest.mock import MagicMock

        class Junk:
            returncode = 0
            stdout = MagicMock()

        failures = (FileNotFoundError("git"), subprocess.TimeoutExpired("git", 2.0),
                    ValueError("not enough values to unpack"), RuntimeError("boom"))
        for error in failures:
            with self.subTest(error=type(error).__name__), patch("subprocess.run", side_effect=error):
                self.assertEqual(chat_tui_theme.repo_info(), (None, None))
        with patch("subprocess.run", return_value=Junk()):
            self.assertEqual(chat_tui_theme.repo_info(), (None, None))
        # A root that is not an absolute, existing directory is no repo info at all.
        for root in ("junk", "relative/dir", "/no/such/llmtool-repo"):
            with self.subTest(root=root), patch("subprocess.run", side_effect=self.git_answers(root)):
                self.assertEqual(chat_tui_theme.repo_info(), (None, None))
        # A mocked Popen (as provider tests patch it) makes subprocess.run itself fail.
        with patch("subprocess.Popen") as spawn:
            spawn.return_value.__enter__.return_value.communicate.return_value = MagicMock()
            self.assertEqual(chat_tui_theme.repo_info(), (None, None))


class Recorder:
    """An output whose calls are recorded as (name, data)."""

    def __init__(self):
        self.calls = []
        for name in ("write", "cursor_goto", "cursor_up", "cursor_down", "cursor_forward",
                     "cursor_backward", "erase_end_of_line", "erase_down", "erase_screen",
                     "reset_attributes", "set_attributes", "flush", "hide_cursor",
                     "show_cursor", "quit_alternate_screen", "disable_mouse_support"):
            setattr(self, name, lambda *args, name=name: self.calls.append((name, args)))
        self.write_raw = lambda data: self.calls.append(("raw", data))

    def stream(self):
        return "".join(args if name == "raw" else args[0] if name == "write" else "<%s>" % name
                       for name, args in self.calls)


class OSC8GuardTests(unittest.TestCase):
    def test_a_link_covers_only_the_cell_written_after_its_open(self):
        output = Recorder()
        chat_tui_theme.install_osc8_guard(output)
        opening = "\x1b]8;id=llmtool-1;file://mac/a\x1b\\"
        output.write_raw(opening)
        output.set_attributes("attrs")
        output.write("a")
        output.write_raw(opening)
        output.write("b")
        output.write(" ")            # an unlinked neighbour: the link closes first
        output.write_raw(opening)
        output.write("c")
        output.cursor_goto(5, 0)     # a partial redraw jumps elsewhere: closed first
        output.write("z")
        output.write_raw(opening)
        output.write("d")
        output.flush()
        close = chat_tui_theme.OSC8_CLOSE
        self.assertEqual(output.stream(), opening + "<set_attributes>a" + opening + "b" + close + " "
                         + opening + "c" + close + "<cursor_goto>z" + opening + "d" + close
                         + "<flush>")

    def test_nothing_changes_without_links(self):
        output = Recorder()
        chat_tui_theme.install_osc8_guard(output)
        output.write("ab")
        output.write_raw("\x1b[?1002h")
        output.cursor_up(1)
        self.assertEqual(output.stream(), "ab\x1b[?1002h<cursor_up>")


@unittest.skipUnless(HAVE_TOOLKIT, "prompt_toolkit is not installed")
class PaneLinkTests(unittest.TestCase):
    def pane_rows(self, view, linker, width=60):
        from prompt_toolkit.utils import get_cwidth
        pane = chat_tui_view.make_pane(view, colour=True, linker=lambda: linker)
        content = pane.control.create_content(width, 10)
        return [content.get_line(index) for index in range(content.line_count)]

    def test_links_are_zero_width_escapes_and_copy_returns_the_source(self):
        from prompt_toolkit.utils import get_cwidth
        view = chat_tui_view.ConversationView(get_cwidth)
        view.set_text("改 llmtool/a.py:12 見 #7 吧")
        linker = chat_tui_theme.Linker("/r", "o/r", host="h")
        row = self.pane_rows(view, linker)[0]
        shown = "".join(text for style, text in row if "[ZeroWidthEscape]" not in style)
        self.assertEqual(shown, view.text)            # the visible cells are the text itself
        escapes = [text for style, text in row if "[ZeroWidthEscape]" in style]
        self.assertEqual(len(escapes), len("llmtool/a.py:12") + len("#7"))   # one per cell
        self.assertTrue(all(e.startswith("\x1b]8;id=llmtool-") for e in escapes))
        self.assertIn("file://h/r/llmtool/a.py", escapes[0])
        self.assertIn("https://github.com/o/r/issues/7", escapes[-1])
        linked = [style for style, text in row if text == "a" and "[ZeroWidthEscape]" not in style]
        self.assertTrue(all("underline" in style for style in linked))
        start = view.text.index("llmtool")
        self.assertEqual(view.copy_text(start, start + 15), "llmtool/a.py:12")
        view.begin_drag(0)
        view.end_drag(len(view.text))
        self.assertEqual(view.selected_text(), "改 llmtool/a.py:12 見 #7 吧")

    def test_no_linker_no_escapes(self):
        from prompt_toolkit.utils import get_cwidth
        view = chat_tui_view.ConversationView(get_cwidth)
        view.set_text("llmtool/a.py:12 #7")
        rows = self.pane_rows(view, None)
        self.assertFalse(any("[ZeroWidthEscape]" in style for row in rows for style, _t in row))


class BackgroundItemTests(unittest.TestCase):
    def test_items_roles_prs_and_age(self):
        records = [
            _record("t-run", updated="2026-10-30T01:00:00Z"),
            _record("t-review", developer_state="DEVELOPER_RESPONDED", updated="2026-10-30T00:30:00Z",
                    review_dispatch_claim={"pr": 654, "version": 2}),
            _record("t-done", developer_state="DEVELOPER_RESPONDED",
                    completion={"state": "PM_RETURNED", "pm_recommendation": {"decision": "ACCEPT"}}),
            _record("t-intake", developer=None, state="PM_PENDING"),
        ]
        records[1]["developer"]["direct_result_callback"] = {
            "review_handoff": {"dispatch_state": "PENDING"}}
        items = chat_tui_status.background_items(records)
        self.assertEqual([(i["role"], i["task"], i["pr"], i["state"]) for i in items],
                         [("Reviewer", "t-review", "#654", "WAITING_REVIEW"),
                          ("Developer", "t-run", "", "RUNNING")])
        now = datetime(2026, 10, 30, 1, 12, 5, tzinfo=timezone.utc)
        self.assertEqual(chat_tui_status.age_text("2026-10-30T01:00:00Z", now), "12m")
        self.assertEqual(chat_tui_status.age_text("2026-10-30T01:12:00+00:00", now), "5s")
        self.assertEqual(chat_tui_status.age_text("2026-10-28T01:00:00Z", now), "2d")
        self.assertEqual(chat_tui_status.age_text(None, now), "?")

    def test_read_background_uses_the_pm_session_binding(self):
        class Store:
            root = Path("relative")   # the status index stays off: plain reads
            records = {"a": _record("a"), "b": _record("b", session="11111111-2345-4789-8abc-def012345678"),
                       "c": dict(_record("c"), pm_provenance=None)}

            def task_ids(self):
                return list(self.records)

            def get(self, task_id):
                return self.records[task_id]
        self.assertEqual([i["task"] for i in chat_tui_status.read_background(SESSION, Store())], ["a"])
        with self.assertRaises(LLMToolError):
            chat_tui_status.read_background("not a session", Store())


@unittest.skipUnless(HAVE_TOOLKIT, "prompt_toolkit is not installed")
class StatusLineTests(Base):
    def setUp(self):
        super().setUp()
        self.pipe = create_pipe_input()
        self.pipe.__enter__()
        self.addCleanup(self.pipe.__exit__, None, None, None)
        self.ui = chat_tui.ChatTUI(self.session, input=self.pipe, output=DummyOutput())
        self.ui.pm_mode, self.ui.pm_state, self.ui.pm_target = True, "idle", (SESSION, "/tmp")

    def loaded(self, items, error=None):
        self.ui.finish_background(SESSION, (items, error))

    def test_status_line_lead(self):
        ui = self.ui
        self.assertTrue(ui.status_text().startswith("▸▸ PM 模式 · auto | claude/default | abcdef01 | 待命"))
        self.loaded([])
        self.assertTrue(ui.status_text().startswith("▸▸ PM 模式 · auto · 背景 0 | "))
        items = chat_tui_status.background_items([_record("t1"), _record("t2")])
        self.loaded(items)
        ui.pm_outbox.append("next")
        self.assertTrue(ui.status_text().startswith("▸▸ PM 模式 · auto · 背景 2 · ↓ 查看 · 佇列 1 | "),
                        ui.status_text())
        ui.input.text = "draft"
        self.assertNotIn("↓ 查看", ui.status_text())   # Down moves in the draft then
        self.loaded([], "TaskStore read failed")
        self.assertIn("背景 ? · 佇列 1", ui.status_text())
        ui.pm_target = (SESSION.replace("a", "b"), "/tmp")   # another PM: no stale count
        self.assertNotIn("背景", ui.status_text())

    def test_panel_lists_role_task_pr_state_and_age(self):
        ui = self.ui
        updated = (datetime.now(timezone.utc) - timedelta(minutes=7)).isoformat()
        record = _record("0123456789abcdef", developer_state="DEVELOPER_RESPONDED", updated=updated,
                         review_repair_claims=[{"pr": 77, "state": "DISPATCHED", "version": 1}])
        self.loaded(chat_tui_status.background_items([record]))
        ui.show_background = True
        self.assertTrue(ui.background_visible())
        rows = "".join(text for _style, text in ui.background_text()).split("\n")
        self.assertIn("背景工作 1 項", rows[0])
        self.assertIn("Esc 關閉", rows[0])
        self.assertRegex(rows[1], r"^  Developer +01234567 +#77 +REPAIRING +7m$")
        ui.input.text = "x"
        self.assertFalse(ui.show_background)          # typing closes it

    def test_hook_for_background_turns(self):
        ui = self.ui
        self.loaded([])
        turn = {"role": "PM", "task": "turn-1", "pr": "", "state": "BACKGROUND_TURN",
                "updated_at": None}
        with patch.object(chat_tui.ChatTUI, "pm_background_turns", lambda _ui: [turn]):
            self.assertIn("背景 1 · ↓ 查看", ui.status_text())
            self.assertEqual(ui.background_all(), [turn])


@unittest.skipUnless(HAVE_TOOLKIT, "prompt_toolkit is not installed")
class DisplayCommandTests(Base):
    def drive(self, steps, environ=None):
        output = DummyOutput()
        output.get_size = lambda: Size(rows=24, columns=100)
        with patch.dict(os.environ, environ or {}), create_pipe_input() as pipe:
            ui = chat_tui.ChatTUI(self.session, input=pipe, output=output)
            self.ui = ui

            async def main():
                task = asyncio.ensure_future(ui.app.run_async(handle_sigint=False))
                await asyncio.sleep(0.05)
                for step in steps:
                    if isinstance(step, str):
                        pipe.send_text(step)
                        await asyncio.sleep(0.05)
                    else:
                        result = step(ui)
                        if asyncio.iscoroutine(result):
                            await result
                if not task.done():
                    pipe.send_text("\x03")
                return await asyncio.wait_for(task, 5)
            return ui, asyncio.run(main())

    def test_theme_command_switches_saves_and_restyles(self):
        path = self.temp_root / "tui-settings.json"

        def dark(ui):
            self.assertEqual(ui.theme, "dark")
            self.assertEqual(ui._style("diff.add"), chat_tui_view.THEME_DARK["diff.add"])

        def light(ui):
            self.assertEqual(ui.theme, "light")
            self.assertEqual(ui._style("diff.add"), chat_tui_view.THEME_LIGHT["diff.add"])
            self.assertEqual(ui._app_style().get_attrs_for_style_str("class:menu").bgcolor, "e8e8e8")
            self.assertIn("Theme: light (drawn light)", ui.conversation.text)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

        self.drive([dark, "/theme light\r", light, "/theme neon\r",
                    lambda ui: self.assertIn("usage: /theme dark|light|auto", ui.conversation.text)],
                   {"COLORFGBG": "15;0", "TERM": "xterm-256color"})
        self.assertEqual(chat_tui_theme.load_settings(path)["theme"], "light")
        self.drive([lambda ui: self.assertEqual(ui.theme, "light"), "/theme auto\r",
                    lambda ui: self.assertEqual(ui.theme, "light")],   # auto reads COLORFGBG
                   {"COLORFGBG": "0;15", "TERM": "xterm-256color"})

    def test_no_color_keeps_the_mono_column(self):
        def mono(ui):
            self.assertFalse(ui.colour)
            self.assertEqual(ui._style("diff.add"), chat_tui_view.THEME_MONO["diff.add"])
            self.assertEqual(ui._style("ui.menu"), "reverse")
        self.drive(["/theme light\r", mono], {"NO_COLOR": "1", "TERM": "xterm-256color"})

    def test_links_command_never_enables_an_unsupported_terminal(self):
        def refused(ui):
            self.assertFalse(ui.links_on())
            self.assertIn("this terminal has no OSC 8 links", ui.conversation.text)
            self.assertIsNone(ui.linker)
        self.drive(["/links on\r", refused], {"TERM_PROGRAM": "Apple_Terminal", "TERM": "xterm-256color"})

        def toggled(ui):
            self.assertTrue(ui.links_supported)
            self.assertFalse(ui.links_on())
            self.assertIn("Links: off", ui.conversation.text)
        with patch.object(chat_tui_theme, "repo_info", return_value=("/r", "o/r")):
            self.drive([lambda ui: self.assertTrue(ui.links_on()), "/links off\r", toggled],
                       {"TERM_PROGRAM": "iTerm.app", "TERM": "xterm-256color"})
        self.assertEqual(chat_tui_theme.load_settings(self.temp_root / "tui-settings.json")["links"], "off")

    def test_startup_never_runs_git_and_links_survive_git_failing(self):
        environ = {"TERM_PROGRAM": "iTerm.app", "TERM": "xterm-256color"}
        calls = []

        def broken(args, **kwargs):
            calls.append(args)
            raise OSError("git unavailable")
        with patch("subprocess.run", side_effect=broken), patch.dict(os.environ, environ), \
                create_pipe_input() as pipe:
            ui = chat_tui.ChatTUI(self.session, input=pipe, output=DummyOutput())
            self.assertEqual(calls, [])          # the constructor runs no git
            self.assertIsNone(ui._linker_lookup)
            ui.linker                            # first use starts the lookup thread
            ui._linker_lookup.join(5)
            linker = ui.linker
            self.assertEqual((linker.base_dir, linker.repo_slug), (os.getcwd(), None))
            self.assertIs(ui.linker, linker)     # resolved once
            self.assertEqual(len(calls), 1)      # no root: the remote is not asked
        with patch("subprocess.run", side_effect=broken):
            ui, result = self.drive([lambda ui: self.assertTrue(ui.links_on())], environ)
        self.assertEqual(result, 0)
        with patch.object(chat_tui_theme, "repo_info", side_effect=ValueError("junk")):
            ui, result = self.drive([self.until_linked,
                                     lambda ui: self.assertIsNone(ui.linker.repo_slug)], environ)
        self.assertEqual(result, 0)

    @staticmethod
    async def until_linked(ui):
        for _ in range(250):
            if ui._linker is not None:
                return
            await asyncio.sleep(0.02)

    def test_hung_git_never_delays_a_draw_and_links_come_after(self):
        release = threading.Event()

        def hung(cwd=None):
            release.wait(10)                     # a git that hangs until the test lets it go
            return "/r", "o/r"
        drawn = []

        def first_draws(ui):
            self.assertIsNone(ui.linker)
            ui.append("見 llmtool/a.py:12 與 #7")
            for _ in range(3):
                started = time.monotonic()
                content = ui.conversation.control.create_content(80, 10)
                rows = [content.get_line(i) for i in range(content.line_count)]
                drawn.append(time.monotonic() - started)
                self.assertFalse(any("[ZeroWidthEscape]" in style
                                     for row in rows for style, _text in row))
            release.set()

        def linked(ui):
            self.assertEqual(ui.linker.repo_slug, "o/r")
            content = ui.conversation.control.create_content(80, 10)
            escapes = "".join(text for i in range(content.line_count)
                              for style, text in content.get_line(i)
                              if "[ZeroWidthEscape]" in style)
            self.assertIn("\x1b]8;id=llmtool-", escapes)
            self.assertIn("https://github.com/o/r/issues/7", escapes)
        started = time.monotonic()
        with patch.object(chat_tui_theme, "repo_info", side_effect=hung):
            ui, result = self.drive([first_draws, self.until_linked, linked],
                                    {"TERM_PROGRAM": "iTerm.app", "TERM": "xterm-256color"})
        self.assertEqual(result, 0)
        self.assertLess(max(drawn), 0.5)
        self.assertLess(time.monotonic() - started, 8)

    @staticmethod
    async def until_closed(ui):
        # A lone Esc waits for the input parser's and the key processor's Esc timeouts.
        for _ in range(150):
            if not ui.show_background:
                return
            await asyncio.sleep(0.02)

    def test_down_opens_the_background_panel_and_esc_closes_it(self):
        def connect(ui):
            ui.pm_mode, ui.pm_state, ui.pm_target = True, "idle", (SESSION, "/tmp")
            ui.finish_background(SESSION, (chat_tui_status.background_items([_record("t1")]), None))

        def shown(ui):
            self.assertTrue(ui.background_visible())
            self.assertIn("背景工作 1 項", "".join(t for _s, t in ui.background_text()))

        def closed(ui):
            self.assertFalse(ui.show_background)
        self.drive([connect, ESC + "[B", shown, ESC, lambda ui: self.until_closed(ui), closed])

    def test_background_tick_reads_once_per_interval(self):
        self.background_tick.stop()
        calls = []

        def read(key):
            calls.append(key)
            return chat_tui_status.background_items([_record("t1")])

        def connect(ui):
            ui.pm_mode, ui.pm_state, ui.pm_target = True, "idle", (SESSION, "/tmp")
            ui.app.invalidate()

        async def settled(ui):
            for _ in range(100):
                if ui.background_count() == 1:
                    break
                await asyncio.sleep(0.02)
            for _ in range(5):            # more redraws inside the interval: no more reads
                ui.app.invalidate()
                await asyncio.sleep(0.02)
            self.assertEqual(calls, [SESSION])
            self.assertIn("背景 1 · ↓ 查看", ui.status_text())
            ui.background_loaded = time.monotonic() - chat_tui_status.REFRESH_SECONDS
            ui.app.invalidate()
            for _ in range(100):
                if len(calls) == 2:
                    break
                await asyncio.sleep(0.02)
            self.assertEqual(calls, [SESSION, SESSION])
        with patch.object(chat_tui_status, "read_background", side_effect=read):
            self.drive([connect, settled])


if __name__ == "__main__":
    unittest.main()
