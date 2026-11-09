"""W56a layout: style tokens, styled spans, turn rules, tool summary, usage and status bar."""

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmtool import chat_tui, chat_tui_pm, chat_tui_view, tui_markdown
from tests.test_chat_tui import HAVE_TOOLKIT, Base

if HAVE_TOOLKIT:
    from prompt_toolkit.data_structures import Size
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from prompt_toolkit.utils import get_cwidth

CONTRACT_TOKENS = (
    "text", "md.bold", "md.italic", "md.code", "md.heading", "md.bullet", "md.link", "md.quote",
    "md.table.border", "md.table.header", "md.table.cell",
    "md.codeblock.border", "md.codeblock.lang", "md.codeblock",
    "diff.add", "diff.del", "diff.hunk", "diff.meta", "diff.lineno",
    "syntax.keyword", "syntax.string", "syntax.comment", "syntax.number", "syntax.builtin",
    "role.owner", "role.pm", "role.system", "tool.summary", "tool.error", "meta.usage",
    "rule", "status.bar", "status.key",
)
UI_TOKENS = ("ui.menu", "ui.menu.current", "ui.menu.header", "ui.help", "ui.selected", "ui.link",
             "ui.pr.url")


class StyleTokenTests(unittest.TestCase):
    def test_one_table_per_theme_maps_every_contract_token(self):
        self.assertEqual(set(chat_tui_view.STYLE_TOKENS), {"dark", "light", "mono"})
        for theme, table in chat_tui_view.STYLE_TOKENS.items():
            with self.subTest(theme=theme):
                self.assertEqual(set(table), set(CONTRACT_TOKENS) | set(UI_TOKENS))
        for token, mono in chat_tui_view.THEME_MONO.items():
            with self.subTest(token=token):
                self.assertNotIn("fg:", mono)
                self.assertNotIn("bg:", mono)
                self.assertTrue(set(mono.split()) <= {"bold", "dim", "reverse"}, mono)

    def test_unknown_tokens_fall_back_to_text(self):
        self.assertEqual(chat_tui_view.token_style("md.nonsense"), chat_tui_view.token_style("text"))
        self.assertEqual(chat_tui_view.token_style("role.pm", colour=False), "bold")

    def test_colour_off_for_no_color_and_colourless_terminals(self):
        self.assertTrue(chat_tui_view.colour_enabled({"TERM": "xterm-256color"}))
        self.assertFalse(chat_tui_view.colour_enabled({"TERM": "xterm-256color", "NO_COLOR": "1"}))
        for term in ("", "dumb", "vt100", "xterm-mono"):
            with self.subTest(term=term):
                self.assertFalse(chat_tui_view.colour_enabled({"TERM": term}))

    def test_markdown_fallback_and_renderer(self):
        with patch.object(chat_tui_view, "_tui_markdown", None):
            self.assertEqual(chat_tui_view.render_markdown("**a**\nb", 40),
                             [[("**a**", "text")], [("b", "text")]])

        class Renderer:
            @staticmethod
            def render(text, width):
                return [[("a", "md.bold")], [("w=%d" % width, "text")]]
        with patch.object(chat_tui_view, "_tui_markdown", Renderer):
            with patch.object(chat_tui_view, "_markdown_blocks", lambda text: [(True, [text])]):
                self.assertEqual(chat_tui_view.render_markdown("**a**", 40),
                                 [[("a", "md.bold")], [("w=40", "text")]])

        class Broken:
            @staticmethod
            def render(text, width):
                raise ValueError("bug")
        with patch.object(chat_tui_view, "_tui_markdown", Broken), \
                patch.object(chat_tui_view, "_markdown_blocks", lambda text: [(False, [text])]):
            self.assertEqual(chat_tui_view.render_markdown("x", 40), [[("x", "text")]])


class RealMarkdownTests(unittest.TestCase):
    """The merged W56b+c renderer: blocks at the pane width, prose left to the pane."""

    def test_prose_is_never_hard_wrapped_but_tables_and_code_fit(self):
        self.assertIsNotNone(chat_tui_view._tui_markdown)
        prose = "第一段 " + "中文 wrapped words " * 20 + "END"
        text = prose + "\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```py\nx = 1\n```\n---"
        lines = ["".join(part for part, _token in line)
                 for line in chat_tui_view.render_markdown(text, 30)]
        self.assertEqual(lines[0], prose)                  # one line: the pane soft-wraps it
        self.assertIn("│ a │ b │", lines)
        self.assertTrue(any(line.startswith("┌─ py") and len(line) <= 30 for line in lines))
        self.assertEqual(lines[-1], "─" * 30)
        self.assertEqual([fit for fit, _chunk in chat_tui_view._markdown_blocks(text)],
                         [False, False, True, False, True, True])
        whole = chat_tui_view._tui_markdown.render(text.split("\n\n|")[0] + "\n\n- a\n> b", 99999)
        self.assertEqual(chat_tui_view.render_markdown(text.split("\n\n|")[0] + "\n\n- a\n> b", 30),
                         whole)                            # per-line prose == whole-document prose


class SpanTests(unittest.TestCase):
    def view(self):
        return chat_tui_view.ConversationView(lambda char: 2 if ord(char) > 0x2e80 else 1)

    def test_set_tail_styles_segments_and_clips_on_rewrite(self):
        view = self.view()
        view.set_tail(0, [("你", "role.owner"), ("\n  hi", "text"), ("\nPM", "role.pm")])
        self.assertEqual(view.text, "你\n  hi\nPM")
        self.assertEqual(view.spans, [(0, 1, "role.owner"), (6, 9, "role.pm")])
        self.assertEqual(view.styled(0, 9), [("role.owner", 0, 1), ("text", 1, 6),
                                             ("role.pm", 6, 9)])
        view.set_text("你\n  hi\nP")
        self.assertEqual(view.spans, [(0, 1, "role.owner"), (6, 8, "role.pm")])
        view.set_text("")
        self.assertEqual(view.spans, [])

    def test_wrapped_rows_of_an_indented_line_hang_and_copy_without_breaks(self):
        view = self.view()
        view.set_text("PM\n  " + "abcdefghij" * 3)
        view.layout(12, 10)
        self.assertEqual([view.text[s:e] for _l, s, e in view.rows],
                         ["PM", "  abcdefghij", "abcdefghij", "abcdefghij"])
        self.assertEqual([view.row_indent(i) for i in range(4)], [0, 0, 2, 2])
        bullet = self.view()
        bullet.set_text("  • " + "x" * 20)
        bullet.layout(12, 10)
        self.assertEqual([bullet.row_indent(i) for i in range(len(bullet.rows))], [0, 4, 4])
        self.assertEqual(view.offset_at(2, 2), view.rows[2][1])     # column after the hang
        view.anchor, view.head = 5, len(view.text)
        self.assertEqual(view.selected_text(), "abcdefghij" * 3)   # no soft break copied

    def test_replace_moves_later_spans_selection_and_reading_anchor(self):
        view = self.view()
        view.set_tail(0, [("aa", "text"), ("SUM", "tool.summary"), ("bb", "text"), ("U", "meta.usage")])
        view.anchor, view.head = 5, 7
        view.top_offset = 5
        view.replace(2, 5, [("SUMMARY", "tool.error")])
        self.assertEqual(view.text, "aaSUMMARYbbU")
        self.assertEqual(view.spans, [(2, 9, "tool.error"), (11, 12, "meta.usage")])
        self.assertEqual((view.anchor, view.head), (9, 11))
        self.assertEqual(view.top_offset, 9)
        self.assertEqual(view.token_at(11), "meta.usage")
        self.assertEqual(view.token_at(10), "text")


@unittest.skipUnless(HAVE_TOOLKIT, "prompt_toolkit not installed")
class PaneDrawTests(unittest.TestCase):
    def lines(self, segments, width=30, colour=True):
        view = chat_tui_view.ConversationView(get_cwidth)
        view.set_tail(0, segments)
        pane = chat_tui_view.make_pane(view, colour=colour)
        content = pane.control.create_content(width, 10)
        return [content.get_line(index) for index in range(content.line_count)]

    def test_rule_fills_the_row_with_the_time_at_the_right(self):
        lines = self.lines([("── 14:03", "rule"), ("\n你", "role.owner")], width=20)
        self.assertEqual(lines[0], [("fg:ansibrightblack", "─" * 14 + " 14:03")])
        self.assertEqual(lines[1], [("bold fg:ansigreen", "你")])

    def test_usage_is_flush_right_and_mono_uses_attributes_only(self):
        lines = self.lines([("x\n", "text"), ("1.0 秒 · 2.4k tokens", "meta.usage")], width=30,
                           colour=False)
        style, text = lines[1][0]
        self.assertEqual(style, "dim")
        self.assertEqual(get_cwidth(text), 30)
        self.assertTrue(text.endswith("1.0 秒 · 2.4k tokens"))

    def test_selection_keeps_the_token_style(self):
        view = chat_tui_view.ConversationView(get_cwidth)
        view.set_tail(0, [("ab", "md.bold"), ("cd", "text")])
        view.anchor, view.head = 1, 3
        pane = chat_tui_view.make_pane(view, colour=True)
        line = pane.control.create_content(20, 5).get_line(0)
        self.assertEqual(line, [("bold", "a"), ("bold class:conversation.selected", "b"),
                                ("class:conversation.selected", "c"), ("", "d")])


class TurnTextTests(unittest.TestCase):
    def test_pm_ghost_hint_fits_terminal_input_width_with_ellipsis(self):
        width = len  # The hint is ASCII plus a one-cell ellipsis.
        full_hint = chat_tui.ChatTUI.pm_ghost_hint('/pm ')
        for columns in (80, 120):
            available = columns - width('pm> ') - width('/pm ')
            hint = chat_tui.ChatTUI.pm_ghost_hint('/pm ', available, width)
            with self.subTest(columns=columns):
                self.assertLessEqual(width(hint), available)
                if width(full_hint) > available:
                    self.assertTrue(hint.endswith('…'))

    def test_usage_line_is_short(self):
        event = {"status": "COMPLETED", "usage": {"input_tokens": 2000, "output_tokens": 400,
                                                  "cache_read_input_tokens": 90000}}
        self.assertEqual(chat_tui.usage_line(event, 39.84), "39.8 秒 · 2.4k tokens")
        self.assertEqual(chat_tui.usage_line({"status": "INTERRUPTED", "usage": {}}, 1.0),
                         "已中斷 · 1.0 秒")
        self.assertEqual(chat_tui.usage_line({"status": "odd status"}, 0.5), "UNKNOWN · 0.5 秒")

    def test_tool_summary_says_what_each_call_does(self):
        # W57: [label, outcome, started, ended]; the collapsed line shows the last failure.
        calls = [["列出檔案", "ok", 10.0, 11.0], ["Read a.py", "error", 11.0, 49.5],
                 ["Bash", "ok", 50.0, 54.9], ["跑測試", "denied", 55.0, 55.2]]
        segments = chat_tui.tool_summary_lines(calls, expanded=False, now=60.0)
        self.assertEqual(segments, [("  ▸ 使用工具 4 次（2 次失敗）：✗ 跑測試（0s，自動拒絕）",
                                     "tool.error")])
        one = chat_tui.tool_summary_lines([["列出檔案", "ok", 10.0, 14.2]], False, now=60.0)
        self.assertEqual(one, [("  ▸ ✓ 列出檔案（4s）", "tool.summary")])
        expanded = chat_tui.tool_summary_lines(calls, expanded=True, now=60.0)
        self.assertEqual(expanded, [("  ▾ 使用工具 4 次（2 次失敗）", "tool.error"),
                                    ("\n      ✓ 列出檔案（1s）", "tool.summary"),
                                    ("\n      ✗ Read a.py（38s，失敗）", "tool.error"),
                                    ("\n      ✓ Bash（4s）", "tool.summary"),
                                    ("\n      ✗ 跑測試（0s，自動拒絕）", "tool.error")])

    def test_running_call_shows_live_seconds_and_unknown_start_shows_none(self):
        running = [["列出檔案", "ok", 0.0, 1.0], ["跑 focused 測試", None, 10.0, None]]
        self.assertEqual(chat_tui.tool_summary_lines(running, False, now=33.4),
                         [("  ▸ 執行中：跑 focused 測試… 23s（第 2 個工具）", "tool.summary")])
        self.assertEqual(chat_tui.tool_summary_lines(running[1:], False, now=11.0),
                         [("  ▸ 執行中：跑 focused 測試… 1s", "tool.summary")])
        # A TOOL_RESULT without its TOOL_USE: no start, so no duration is guessed.
        orphan = [["UNKNOWN", "error", None, 5.0]]
        self.assertEqual(chat_tui.tool_summary_lines(orphan, False, now=9.0),
                         [("  ▸ ✗ UNKNOWN（失敗）", "tool.error")])
        self.assertEqual(chat_tui.tool_item(["x", "ok", None, 5.0], 9.0), "✓ x")
        self.assertEqual(chat_tui.tool_item(["x", None, None, None], 9.0), "▸ 執行中：x…")
        self.assertEqual(chat_tui.tool_item(["x", "unknown", 1.0, 3.0], 9.0), "? x（2s，結果不明）")

    def test_tool_label_uses_the_description_screened_and_bounded(self):
        label = chat_tui_pm.tool_label
        self.assertEqual(label({"name": "Bash", "description": "列出\n  檔案"}), "列出 檔案")
        self.assertEqual(label({"name": "Bash"}), "Bash")                  # no description
        self.assertEqual(label({"name": "Bash", "description": "  "}), "Bash")
        self.assertEqual(label({"name": "Bash", "description": 7}), "Bash")
        secret = "export TOKEN=ghp_" + "a" * 36
        shown = label({"name": "Bash", "description": secret})
        self.assertNotIn("a" * 36, shown)
        long = label({"name": "Read", "description": "長" * 200})
        self.assertIn(tui_markdown.display_width(long), (chat_tui_pm.TOOL_LABEL_CELLS - 1,
                                                         chat_tui_pm.TOOL_LABEL_CELLS))
        self.assertTrue(long.endswith("…"))
        self.assertEqual(label({"name": "Bash", "description": "a\x1b[31mb\u202e"}), "a[31mb")

    def test_history_usage_line_is_short(self):
        turns, total = chat_tui_pm.history_turns({"turns": [{
            "started_at": "2026-10-30T01:00:00+00:00", "ended_at": "2026-10-30T01:00:04.500000+00:00",
            "owner_text": "hi", "status": "INTERRUPTED", "result_text": "ok",
            "usage": {"input_tokens": 1500, "output_tokens": 700, "cache_read_input_tokens": 9}}]})
        self.assertEqual(total, 1)
        self.assertEqual(chat_tui.history_usage_line(turns[0]), "已中斷 · 4.5 秒 · 2.2k tokens")
        turns, _total = chat_tui_pm.history_turns({"turns": [{"started_at": "bad"}]})
        self.assertEqual(turns[0]["time"], "時間不明")
        self.assertEqual(chat_tui.history_usage_line(turns[0]), "UNKNOWN")


@unittest.skipUnless(HAVE_TOOLKIT, "prompt_toolkit not installed")
class PMLayoutTests(Base):
    """A PM turn driven by events, without a running loop (as PMTypewriterTests)."""

    def setUp(self):
        super().setUp()
        self.pipe = create_pipe_input()
        self.pipe.__enter__()
        self.addCleanup(self.pipe.__exit__, None, None, None)
        self.ui = chat_tui.ChatTUI(self.session, input=self.pipe, output=DummyOutput())
        ui = self.ui
        ui.pm_mode, ui.pm_state, ui.pm_target = True, "idle", ("abcdef0123456789", "/tmp")

    def start(self, text="請問"):
        ui = self.ui
        ui._pm_turn_seq += 1
        ui.pm_turn_active, ui.pm_turn_started, ui.pm_state = True, time.monotonic(), "working"
        ui.pm_labelled, ui.pm_tool_calls, ui.pm_tool_ids = False, [], {}
        ui.append("owner → PM:\n" + text, segments=ui._owner_segments("owner → PM:\n" + text, rule=True))

    def event(self, kind, **fields):
        self.ui.pm_event(dict(type=kind, **fields))

    def tokens(self, needle):
        view = self.ui.view
        start = view.text.rindex(needle)
        return {token for token, _low, _high in view.styled(start, start + len(needle))}

    def test_one_turn_layout(self):
        ui = self.ui
        self.start()
        self.event("TEXT_DELTA", text="先看 **檔案**", block_index=0)
        self.event("TOOL_USE", tool_use_id="a", name="Read")
        self.event("TOOL_USE", tool_use_id="b", name="Bash")
        self.event("TOOL_RESULT", tool_use_id="a", reason="OK")
        self.event("TOOL_RESULT", tool_use_id="b", reason="TOOL_ERROR")
        self.event("TEXT_DELTA", text="結論", block_index=2)
        self.event("TURN_END", status="COMPLETED", usage={"input_tokens": 1200, "output_tokens": 34})
        ui.finish_pm_turn()
        text = ui.conversation.text
        body = text[text.rindex("── "):]
        self.assertRegex(body, "^── [0-9]{2}:[0-9]{2}\n你\n  請問\n\nPM\n  先看 檔案\n\n"
                               "  ▸ 使用工具 2 次（1 次失敗）：✗ Bash（0s，失敗）\n\n  結論\n\n"
                               "[0-9]+\\.[0-9] 秒 · 1.2k tokens$")
        self.assertEqual(self.tokens("你"), {"role.owner"})
        self.assertEqual(self.tokens("檔案"), {"md.bold"})      # tui_markdown rendered the body
        self.assertEqual(ui.view.token_at(text.rindex("\nPM\n") + 1), "role.pm")
        self.assertEqual(self.tokens("▸ 使用工具"), {"tool.error"})
        self.assertEqual(self.tokens(" 秒 · 1.2k tokens"), {"meta.usage"})
        self.assertEqual(ui.view.token_at(text.rindex("── ")), "rule")

    def test_summary_updates_in_place_while_text_follows_and_f7_expands_it(self):
        ui = self.ui
        self.start()
        self.event("TOOL_USE", tool_use_id="a", name="Read", description="Read chat_tui.py")
        self.event("TEXT_DELTA", text="中間", block_index=1)
        ui._pm_flush()
        ui._pm_render(final=False)
        self.event("TOOL_USE", tool_use_id="b", name="Bash", description="跑 focused 測試")
        text = ui.conversation.text
        self.assertEqual(text.count("執行中："), 1)               # one line, updated in place
        self.assertIn("PM\n  ▸ 執行中：跑 focused 測試… 0s（第 2 個工具）\n\n  中間", text)
        self.assertIn("F7 工具", ui.status_text())
        ui.toggle_tools()
        self.assertIn("  ▾ 使用工具 2 次\n      ▸ 執行中：Read chat_tui.py… 0s\n"
                      "      ▸ 執行中：跑 focused 測試… 0s\n\n  中間", ui.conversation.text)
        self.event("TOOL_RESULT", tool_use_id="a", reason="PERMISSION_DENIED")
        self.assertIn("      ✗ Read chat_tui.py（0s，自動拒絕）", ui.conversation.text)
        self.assertEqual(self.tokens("▾ 使用工具"), {"tool.error"})
        ui.toggle_tools()
        self.assertIn("  ▸ 執行中：跑 focused 測試… 0s（第 2 個工具；1 次失敗）\n\n  中間",
                      ui.conversation.text)
        self.assertNotIn("      ✗", ui.conversation.text)
        self.assertIn("執行工具中…", ui._pm_busy_line())
        self.assertNotIn("執行工具中…", ui.conversation.text)

    def test_running_seconds_advance_on_the_busy_tick(self):
        ui = self.ui
        self.start()
        self.event("TOOL_USE", tool_use_id="a", name="Bash", description="跑 focused 測試")
        self.assertIn("▸ 執行中：跑 focused 測試… 0s", ui.conversation.text)
        ui.pm_tool_calls[0][2] -= 23.2   # the call started 23 s ago
        ui._pm_busy_tick()            # the existing busy tick, not a new timer
        self.assertIn("▸ 執行中：跑 focused 測試… 23s", ui.conversation.text)
        self.assertEqual(ui.conversation.text.count("執行中："), 1)
        self.assertIn("執行工具中…", ui._pm_busy_line())
        self.assertNotIn("執行工具中…", ui.conversation.text)
        ui.pm_tool_calls[0][2] -= 1.0
        ui._pm_busy_tick()
        self.assertIn("… 24s", ui.conversation.text)
        self.event("TOOL_RESULT", tool_use_id="a", reason="OK")
        self.assertRegex(ui.conversation.text, "  ▸ ✓ 跑 focused 測試（24s）")
        before = ui.conversation.text
        ui._pm_busy_tick()            # nothing running: the finished line stays as it is
        self.assertIn("✓ 跑 focused 測試（24s）", ui.conversation.text)
        self.assertEqual(before.count("✓"), ui.conversation.text.count("✓"))

    def test_tool_tick_skips_identical_text_and_replaces_changed_seconds(self):
        ui = self.ui
        call = ["tool", None, time.monotonic() - 3, None]
        shown = "".join(part for part, _token in chat_tui.tool_summary_lines([call], False))
        ui.pm_tool_block = [0, len(shown), [call], ui._pm_turn_seq, shown]
        with patch.object(ui, "_tool_block_valid", return_value=True), \
                patch.object(ui, "_replace_block") as replace:
            ui._pm_tool_tick()
            replace.assert_not_called()
            call[2] -= 1.2
            ui._pm_tool_tick()
            replace.assert_called_once()

    def test_result_without_tool_use_has_no_duration(self):
        ui = self.ui
        self.start()
        self.event("TOOL_RESULT", tool_use_id="zz", reason="TOOL_ERROR")
        self.assertIn("  ▸ ✗ UNKNOWN（失敗）", ui.conversation.text)

    def test_busy_line_verb_precedence_and_queue(self):
        ui = self.ui
        ui.pm_runtime = object()
        ui.pm_turn_active = True
        ui.pm_turn_started = time.monotonic() - 34
        cases = [
            ("interrupting", True, [["tool", None]], "中斷中…"),
            ("thinking", True, [["tool", None]], "思考中…"),
            ("working", True, [["tool", None]], "暖機中…"),
            ("working", False, [["tool", None]], "執行工具中…"),
            ("working", False, [], "回答中…"),
            ("working", False, [], "處理中…"),
        ]
        for state, warm, tools, verb in cases:
            with self.subTest(verb=verb):
                ui.pm_state, ui.pm_warm, ui.pm_tool_calls = state, warm, tools
                ui.pm_block_raw = "reply" if verb == "回答中…" else None
                self.assertIn(verb, ui._pm_busy_line())
        ui.pm_state, ui.pm_warm, ui.pm_tool_calls = "working", False, []
        ui.pm_block_raw = None
        ui.pm_outbox.extend(["one", "two"])
        self.assertRegex(ui._pm_busy_line(), r"✽ 處理中…（34 秒 · 佇列 2 則）")

    def test_busy_wanted_during_stream_and_clears_at_terminal_events(self):
        ui = self.ui
        ui.pm_runtime = object()
        ui.pm_turn_active = True
        self.assertTrue(ui._pm_busy_wanted())
        self.event("TEXT_DELTA", text="stream", block_index=0)
        self.assertTrue(ui._pm_busy_wanted())
        self.event("TURN_END", status="COMPLETED", usage={})
        self.assertFalse(ui._pm_busy_wanted())
        ui.pm_turn_active, ui.pm_turn_status, ui.pm_turn_error = True, None, None
        self.event("RUNTIME_ERROR", code="FAILED")
        self.assertFalse(ui._pm_busy_wanted())
        ui.pm_turn_error = None
        ui.pm_runtime = None
        self.assertFalse(ui._pm_busy_wanted())

    def test_busy_glyph_cycles_and_wraps_on_busy_ticks(self):
        ui = self.ui
        ui.pm_runtime = object()
        ui.pm_turn_active = True
        lines = []
        for _ in range(len(chat_tui.PM_BUSY_GLYPHS) + 1):
            lines.append(ui._pm_busy_line()[0])
            ui._pm_busy_tick()
        self.assertEqual(lines, list(chat_tui.PM_BUSY_GLYPHS) + [chat_tui.PM_BUSY_GLYPHS[0]])
        self.assertEqual(chat_tui.PM_BUSY_SECONDS, 0.5)
        self.assertLessEqual(1 / chat_tui.PM_BUSY_SECONDS, 4)

    def test_busy_rows_and_picker_background_reserve_one_row(self):
        ui = self.ui
        ui.pm_runtime = object()
        ui.pm_turn_active = True
        ui.app.output.get_size = lambda: Size(rows=7, columns=40)
        ui.searching = True
        ui.search_matches = ["plain"] * 8
        ui.pm_turn_active = False
        picker_without_busy = ui.picker_layout()
        ui.pm_turn_active = True
        self.assertEqual(ui.busy_rows(), 1)
        self.assertEqual(sum(ui.picker_layout()), sum(picker_without_busy) - 1)
        ui.searching = False
        ui.search_matches = []
        ui.show_background = True
        ui.pm_mode = True
        ui.app.output.get_size = lambda: Size(rows=6, columns=40)
        ui.pm_turn_active = False
        background_without_busy = ui.background_rows()
        self.assertGreater(background_without_busy, 0)
        ui.pm_turn_active = True
        self.assertEqual(ui.background_rows(), background_without_busy - 1)
        ui.pm_turn_active = False
        self.assertEqual(ui.busy_rows(), 0)
        ui.app.output.get_size = lambda: Size(rows=4, columns=40)
        ui.pm_turn_active = True
        self.assertEqual(ui.busy_rows(), 0)

    def test_waiting_row_yields_to_picker_panel_and_closing(self):
        ui = self.ui
        ui.pm_runtime = object()
        ui.pm_state = "idle"
        ui.background_key = ui.pm_target[0]
        ui.background = [{"role": "Developer", "task": "task1234", "pr": "#654",
                          "state": "RUNNING"}]
        ui.app.output.get_size = lambda: Size(rows=12, columns=40)
        self.assertTrue(ui._pm_waiting_wanted())
        self.assertEqual(ui.busy_rows(), 1)

        ui.menu_items = [("/help", "help", "plain words")]
        self.assertFalse(ui._pm_waiting_wanted())
        self.assertEqual(ui.busy_rows(), 0)
        ui.menu_items = []
        ui.searching = True
        self.assertFalse(ui._pm_waiting_wanted())
        self.assertEqual(ui.busy_rows(), 0)
        ui.searching = False

        ui.show_background = True
        self.assertFalse(ui._pm_waiting_wanted())
        self.assertEqual(ui.busy_rows(), 0)
        ui.show_background = False

        ui.pm_state = "closing"
        self.assertFalse(ui._pm_waiting_wanted())
        self.assertEqual(ui.busy_rows(), 0)

    def test_waiting_only_does_not_schedule_busy_handle(self):
        class Loop:
            def call_later(self, delay, callback):
                raise AssertionError("waiting work must not schedule a timer")

        ui = self.ui
        ui.pm_runtime = object()
        ui.pm_target = ("abcdef0123456789", "/tmp")
        ui.background_key = ui.pm_target[0]
        ui.background = [{"role": "Developer", "task": "task1234", "pr": "#654",
                          "state": "RUNNING"}]
        with patch("llmtool.chat_tui.asyncio.get_running_loop", return_value=Loop()):
            ui._pm_busy_show()
        self.assertTrue(ui._pm_waiting_wanted())
        self.assertIsNone(ui._pm_busy_handle)

    def test_busy_fragments_stay_on_one_row_at_40_columns(self):
        ui = self.ui
        ui.pm_runtime = object()
        ui.pm_turn_active = True
        ui.app.output.get_size = lambda: Size(rows=24, columns=40)
        self.assertLessEqual(get_cwidth(ui.busy_fragments()[0][1]), 40)

    def test_several_busy_updates_keep_exactly_one_tick_pending(self):
        class Handle:
            def __init__(self, callback, delay):
                self.callback = callback
                self.delay = delay
                self.cancelled = False

            def cancel(self):
                self.cancelled = True

        class Loop:
            def __init__(self):
                self.handles = []

            def call_later(self, delay, callback):
                handle = Handle(callback, delay)
                self.handles.append(handle)
                return handle

            def pending(self):
                return [handle for handle in self.handles if not handle.cancelled]

        ui = self.ui
        ui.pm_runtime = object()
        ui.pm_turn_active = True
        loop = Loop()
        with patch("llmtool.chat_tui.asyncio.get_running_loop", return_value=loop):
            for _ in range(5):
                ui._pm_busy_show()
            self.assertEqual(len(loop.pending()), 1)
            for _ in range(5):
                expired = loop.pending()[0]
                expired.cancel()
                expired.callback()
                self.assertEqual(len(loop.pending()), 1)
                ui._pm_busy_show()
            self.assertEqual(len(loop.pending()), 1)
            self.assertEqual(len(loop.handles), 6)
            self.assertEqual(loop.pending()[0].delay, chat_tui.PM_BUSY_SECONDS)
            ui._pm_busy_cancel()

    def test_account_text_is_a_system_notice(self):
        ui = self.ui
        ui.pm_runtime = type("Runtime", (), {"runtime": "claude-code"})()
        ui.pm_binding = {"runtime": "claude-code", "account": "work"}
        ui._append_account("PM mode: …; ", ". Esc interrupts a turn.")
        self.assertTrue(ui.conversation.text.endswith(
            "PM mode: …; claude-code account work. Esc interrupts a turn."))
        self.assertEqual(self.tokens("claude-code account work"), {"role.system"})
        self.assertEqual(self.tokens("PM mode: …; "), {"text"})

    def test_queued_owner_note_and_system_notice_styles(self):
        ui = self.ui
        ui.append("owner → PM（已排入佇列（第 1 則））:\n下一則")
        ui.append("llmtool: PM 佇列暫停")
        self.assertIn("你（已排入佇列（第 1 則））\n  下一則", ui.conversation.text)
        self.assertEqual(self.tokens("（已排入佇列（第 1 則））"), {"role.system"})
        self.assertEqual(self.tokens("llmtool: PM 佇列暫停"), {"role.system"})

    def test_status_bar_states_queue_and_keys(self):
        ui = self.ui
        ui.app.output.get_size = lambda: Size(rows=24, columns=80)
        self.assertEqual(ui.status_text(), "▸▸ PM 模式 · auto | claude/default | abcdef01 | 待命 | 最新 | F6 滑鼠:開 | F1")
        ui.background_key, ui.background = ui.pm_target[0], [{}, {}]
        ui.app.output.get_size = lambda: Size(rows=24, columns=80)
        idle_status = ui.status_text()
        self.assertIn("claude/default", idle_status)
        self.assertNotIn("F1", idle_status)  # idle PM mode drops help before identity at 80 cols
        ui.background_key, ui.background = None, []
        self.start()
        ui.pm_outbox.append("next")
        status = ui.status_text()
        self.assertRegex(status, "^▸▸ PM 模式 · auto · 佇列 1 \\| abcdef01 \\| 回答中 [0-9.]+s \\| Esc 中斷 \\| ")
        ui.pm_state = "thinking"
        self.assertIn("思考中", ui.status_text())
        ui.pm_outbox.clear()
        self.event("TURN_END", status="INTERRUPTED", usage={})
        ui.finish_pm_turn()
        self.assertIn("| 已中斷 |", ui.status_text())
        keys = [style for style, text in ui.status_fragments() if text in ("F1", "F6 滑鼠:開")]
        self.assertEqual(keys, [chat_tui_view.token_style("status.key", ui.colour)] * 2)
        for width in (80, 40, 20, 12, 6):
            ui.app.output.get_size = lambda width=width: Size(rows=24, columns=width)
            with self.subTest(width=width):
                self.assertLessEqual(get_cwidth(ui.status_text()), width)
                self.assertNotIn("\n", ui.status_text())


class RenderCacheTests(unittest.TestCase):
    """Codex v2 P2: a growing streamed line never fills the render cache."""

    def setUp(self):
        chat_tui_view._RENDERED.clear()
        chat_tui_view._rendered_chars[0] = 0

    def test_streaming_one_megabyte_line_keeps_the_cache_within_budget(self):
        class Renderer:   # the cache is under test, not the renderer: a cheap stand-in
            @staticmethod
            def render(text, width):
                return [[(line, "text")] for line in text.split("\n")]
        line = ""
        with patch.object(chat_tui_view, "_tui_markdown", Renderer):
            while len(line) < 1 << 20:
                line += "字" * (1 << 14)
                chat_tui_view.render_markdown("# 標題\n\n" + line, 80)
                self.assertLessEqual(chat_tui_view.render_cache_chars(), 64)   # finished blocks only
            chat_tui_view.render_markdown("# 標題\n\n" + line + "\n\nnext", 80)
            # the finished 1 MB line is over the per-entry share of the budget: not cached
            self.assertLessEqual(chat_tui_view.render_cache_chars(), chat_tui_view.RENDER_CACHE_CHARS)
            for number in range(2000):   # many finished blocks: oldest go first
                chat_tui_view.render_markdown("x" * 4000 + str(number) + "\n\nend", 80)
            self.assertLessEqual(chat_tui_view.render_cache_chars(), chat_tui_view.RENDER_CACHE_CHARS)
        self.assertNotIn(line, {source for source, _width in chat_tui_view._RENDERED})


@unittest.skipUnless(HAVE_TOOLKIT, "prompt_toolkit not installed")
class CopySourceTests(Base):
    """Codex v2 P2: a copy of a rendered reply gives back its source Markdown."""

    SOURCE = ("先看 **檔案** 吧\n\n| 名稱 | 數量 |\n|---|---|\n| 蘋果 | 12 |\n| 香蕉 | 345 |\n\n"
              "```python\nx = 1\ny = compute(x)\n```\n\n結尾")

    def setUp(self):
        super().setUp()
        pipe = create_pipe_input()
        pipe.__enter__()
        self.addCleanup(pipe.__exit__, None, None, None)
        self.ui = chat_tui.ChatTUI(self.session, input=pipe, output=DummyOutput())
        self.copies = []
        self.ui.view._copier = lambda text: self.copies.append(text) or True
        self.ui.pm_turn_seq = 0
        self.ui.pm_block_raw = self.SOURCE           # a finished PM block, rendered as the TUI does
        self.ui._pm_open_block()
        self.ui.pm_block_raw = self.SOURCE
        self.ui._pm_render(final=True)
        self.ui.pm_block_raw = None

    def select(self, first, last=None):
        """Select displayed text from ``first`` through ``last`` (inclusive) and copy it."""
        text = self.ui.view.text
        start = text.rindex(first)
        end = (text.index(last, start) + len(last)) if last else start + len(first)
        copied = self.ui.view.copy_range(start, end)
        self.assertTrue(self.ui.view.wait_copy())
        self.assertEqual(self.copies[-1], copied)
        return copied

    def test_rendering_changed_the_display(self):
        shown = self.ui.view.text
        self.assertIn("  先看 檔案 吧", shown)
        self.assertIn("│ 蘋果 │", shown)
        self.assertIn("│ x = 1", shown)
        self.assertNotIn("**", shown)

    def test_whole_reply_copies_the_exact_source(self):
        self.assertEqual(self.select("先看", "結尾"), self.SOURCE)
        view = self.ui.view
        start = view.text.rindex("\nPM\n") + 1
        self.assertEqual(view.copy_text(start, len(view.text)), "PM\n" + self.SOURCE)

    def test_bold_whole_and_partial(self):
        self.assertEqual(self.select("先看", "吧"), "先看 **檔案** 吧")
        self.assertEqual(self.select("檔案"), "檔案")            # the characters selected
        self.assertEqual(self.select("看 檔"), "看 **檔")        # their source span

    def test_table_whole_and_partial(self):
        table = "| 名稱 | 數量 |\n|---|---|\n| 蘋果 | 12 |\n| 香蕉 | 345 |"
        copied = self.select("名稱", "345")
        self.assertEqual(copied, table[2:-2])
        self.assertEqual(self.select("12"), "12")
        self.assertEqual(self.select("蘋果", "12"), "蘋果 | 12")
        self.assertNotIn("│", self.select("12", "香蕉"))
        self.assertNotIn("─", copied)

    def test_code_whole_and_partial(self):
        self.assertEqual(self.select("x = 1", "compute(x)"), "x = 1\ny = compute(x)")
        self.assertEqual(self.select("compute"), "compute")
        text = self.ui.view.text
        start = text.rindex("┌─ python")
        end = text.index("\n", text.index("└", start))   # through the bottom border
        # borders are UNMAPPED; the language label and the code lines come from the source
        self.assertEqual(self.ui.view.copy_text(start, end), "python\nx = 1\ny = compute(x)\n")

    def body(self, source, width=40):
        """Render ``source`` as a PM body at ``width`` columns; (view, region start)."""
        ui = self.ui
        ui.view.width = width + len(chat_tui.BODY_INDENT)
        body, sources = ui._body(source, chat_tui.BODY_INDENT)
        ui.append("x", segments=body, sources=sources)
        return ui.view, len(ui.view.text) - sum(len(part) for part, _token in body)

    def copy_at(self, view, start, needle, nth=0):
        index = start - 1
        for _ in range(nth + 1):
            index = view.text.index(needle, index + 1)
        return view.copy_text(index, index + len(needle))

    def test_leading_source_spaces_are_not_the_added_indent(self):
        view, start = self.body("  兩個前導空白")
        self.assertEqual(view.text[start:], "    兩個前導空白")       # 2 added + 2 source
        self.assertEqual(view.copy_text(start, start + 2), "")          # the added indent only
        self.assertEqual(view.copy_text(start + 2, start + 4), "  ")    # the source spaces
        self.assertEqual(view.copy_text(start, start + 5), "  兩")
        self.assertEqual(view.copy_text(start, len(view.text)), "  兩個前導空白")

    def test_literal_bullets_in_the_source_keep_their_offsets(self):
        view, start = self.body("• literal •\n- item")
        self.assertEqual(self.copy_at(view, start, "•"), "•")
        self.assertEqual(self.copy_at(view, start, "•", 1), "•")
        self.assertEqual(self.copy_at(view, start, "literal •"), "literal •")
        self.assertEqual(self.copy_at(view, start, "•", 2), "")         # the glyph for "-"
        self.assertEqual(self.copy_at(view, start, "• item"), "item")

    def test_literal_box_characters_inside_a_code_block(self):
        view, start = self.body("```\na │ b ─ c\n```")
        line = view.text.index("a │ b ─ c", start)
        self.assertEqual(view.copy_text(line, line + 9), "a │ b ─ c")
        self.assertEqual(self.copy_at(view, start, "│ b ─"), "│ b ─")
        gutter = view.text.rindex("│ ", start, line)
        self.assertEqual(view.copy_text(gutter, gutter + 3), "a")      # the added gutter is not
        self.assertEqual(self.copy_at(view, start, "┌──"), "")

    def test_mixed_cjk_lines_that_wrap(self):
        cell = "很長的中文欄位內容 mixed 英文 words"
        view, start = self.body("| %s | b |\n|---|---|\n| 1 | 2 |" % cell, width=24)
        rows = [row for row in view.text[start:].split("\n") if "│" in row and "很" in row]
        self.assertGreater(len(rows), 0)
        table_start = view.text.index("┌", start)
        table_end = view.text.index("┘", table_start)
        copied = view.copy_text(table_start, table_end + 1)
        self.assertTrue(copied.startswith(cell[:3]) and "2" in copied and "─" not in copied)
        for piece in ("很長的", "mixed", "英文"):
            self.assertEqual(self.copy_at(view, table_start, piece), piece)
        code = "x = '中文很長很長很長很長很長很長很長很長很長很長' + y"
        view, start = self.body("```py\n%s\n```" % code, width=20)
        first = view.text.index("x = ", start)
        last = view.text.rindex("y", first, view.text.index("└", first)) + 1
        self.assertGreater(view.text[first:last].count("\n"), 0)       # hard-wrapped rows
        self.assertEqual(view.copy_text(first, last), code)            # no "│↪" or breaks
        prose = "中文混合 English 的一行，" * 12
        view, start = self.body(prose, width=30)
        view.layout(32, 20)                                            # soft-wrapped by the pane
        self.assertGreater(len([r for r in view.rows if r[1] >= start]), 2)
        self.assertEqual(view.copy_text(start + 2 + 5, start + 2 + 40), prose[5:40])

    def test_every_mapped_character_is_its_source_character(self):
        from llmtool import chat_tui_copymap as copymap
        sources = [self.SOURCE, "  兩個前導空白", "• literal •", "```\na │ b ─ c\n```",
                   "- a [link](http://x.y) and `code` \\* esc\n1. one\n> quote **b**\n# Head ##"
                   "\n---\ntab\there\n  - nested *it* https://e.com/x.",
                   "| 很長的中文欄位內容需要換行 | b |\n|:-:|--:|\n| x \\| y | 中文中文中文中文中文 |",
                   "| a | b | c |\n|---|---|---|\n| 1 | 2 | 3 | 4 |",
                   "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n-old\n+new\n ctx",
                   "```diff\n@@ -1 +1 @@\n-a\n+b\n```", "```\nunclosed\n"]
        failed = []
        original = copymap._Mapper.block

        def block(mapper, first, count):
            try:
                return original(mapper, first, count)
            except Exception as error:
                failed.append(error)
                raise
        with patch.object(copymap._Mapper, "block", block):
            for width in (8, 20, 40, 80):
                for source in sources:
                    with self.subTest(width=width, source=source[:20]):
                        shown = "\n".join("  " + "".join(t for t, _ in line)
                                          for line in chat_tui_view.render_markdown(source, width))
                        display, offsets = copymap.display_map(
                            source, width, chat_tui_view.FLOW_WIDTH, chat_tui_view._markdown_blocks,
                            chat_tui_view._tui_markdown, "  ")
                        self.assertEqual(display, shown)
                        for index, offset in enumerate(offsets):
                            if offset >= 0 and source[offset] != "\t":
                                self.assertEqual(display[index], source[offset])
        self.assertEqual(failed, [])                                   # no block fell back

    def test_soft_wrapped_hanging_indent_is_never_copied(self):
        ui = self.ui
        body, sources = ui._body("長" * 200, chat_tui.BODY_INDENT)
        ui.append("x", segments=body, sources=sources)
        ui.view.layout(30, 10)
        start = ui.view.text.rindex("長" * 200)
        self.assertEqual(ui.view.copy_text(start, start + 200), "長" * 200)


@unittest.skipUnless(HAVE_TOOLKIT, "prompt_toolkit not installed")
class ChatLayoutTests(Base):
    def test_chat_labels_stay_with_a_rule_and_styles(self):
        with create_pipe_input() as pipe:
            ui = chat_tui.ChatTUI(self.session, input=pipe, output=DummyOutput())
            ui.append("you:\nq", segments=ui._rule_segments() + [("you:", "role.owner"), ("\nq", "text")])
            ui.append("assistant:\n", segments=[("assistant:", "role.pm"), ("\n", "text")]
                      + ui._body_segments("**粗體**", ""))
            text = ui.conversation.text
            self.assertRegex(text, "── [0-9]{2}:[0-9]{2}\nyou:\nq\n\nassistant:\n粗體$")
            self.assertEqual(ui.view.token_at(len(text) - 1), "md.bold")
            start = text.index("assistant:")
            self.assertEqual(ui.view.token_at(start), "role.pm")
            self.assertEqual(ui.view.token_at(text.index("you:")), "role.owner")


if __name__ == "__main__":
    unittest.main()
