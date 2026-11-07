"""Focused replay display and PM configuration guidance tests."""
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmtool import chat_tui, chat_tui_pm


class PMReplayDisplayTests(unittest.TestCase):
    def setUp(self):
        self.ui = object.__new__(chat_tui.ChatTUI)
        self.ui._body = lambda text, indent: ([(indent + text, "text")], [])

    def render(self, turns):
        segments, _sources = self.ui._history_segments({"turns": turns})
        return "".join(part for part, _token in segments)

    @staticmethod
    def turn(started_at, *, status="COMPLETED", reply="saved reply"):
        return {"started_at": started_at, "ended_at": None, "owner_text": "question",
                "status": status, "result_text": reply, "tool_names": [], "usage": {}}

    def test_header_uses_last_turn_local_date_and_time(self):
        timestamp = "2026-10-28T00:00:00+00:00"
        expected = datetime.fromisoformat(timestamp).astimezone().strftime("%m-%d %H:%M")
        rendered = self.render([self.turn("2026-10-27T00:00:00+00:00"),
                                self.turn(timestamp)])
        self.assertTrue(rendered.startswith("上次對話（" + expected + "）"))

    def test_only_empty_final_interrupted_turn_gets_continue_hint(self):
        interrupted = self.turn("2026-10-28T00:00:00+00:00", status="INTERRUPTED", reply="")
        interrupted_with_reply = self.turn("2026-10-28T00:00:00+00:00", status="INTERRUPTED",
                                           reply="partial reply")
        rendered = self.render([interrupted_with_reply])
        self.assertIn("partial reply", rendered)
        self.assertNotIn("輸入「繼續」讓 PM 接著做", rendered)

        rendered = self.render([interrupted, self.turn("2026-10-28T00:01:00+00:00")])
        self.assertIn("（沒有存下回覆文字）", rendered)
        self.assertNotIn("輸入「繼續」讓 PM 接著做", rendered)

        rendered = self.render([interrupted])
        self.assertIn("輸入「繼續」讓 PM 接著做", rendered)
        self.assertNotIn("（沒有存下回覆文字）", rendered)

    def test_normal_replay_text_is_unchanged(self):
        rendered = self.render([self.turn("2026-10-28T00:00:00+00:00", reply="saved reply")])
        self.assertIn("  saved reply", rendered)
        self.assertNotIn("輸入「繼續」讓 PM 接著做", rendered)


class PMConfigHintTests(unittest.TestCase):
    def test_invalid_config_hint_covers_claude_and_codex(self):
        hint = chat_tui_pm.error_text("PM_CONFIG_INVALID")
        self.assertIn("~/.config/llmtool/config.yaml", hint)
        self.assertIn("pm_runtime.claude_config_dir for Claude", hint)
        self.assertIn("set roles.pm.account to an accounts.<name> entry with kind: codex and config_dir", hint)
        self.assertIn("configure roles.pm.model", hint)


class PMUsageStatusTests(unittest.TestCase):
    def test_usage_totals_counts_only_nonnegative_integer_values(self):
        turns = [
            {"usage": {"input_tokens": 12, "output_tokens": 3,
                       "cache_read_input_tokens": 4, "cache_creation_input_tokens": 5}},
            {"usage": {"input_tokens": True, "output_tokens": -1,
                       "cache_read_input_tokens": 0}},
            {"usage": "missing"},
            "missing",
            {"usage": {"output_tokens": 7, "other_tokens": 90}},
        ]
        self.assertEqual(chat_tui_pm.usage_totals(turns), (
            {"input_tokens": 12, "output_tokens": 10, "cache_read_input_tokens": 4,
             "cache_creation_input_tokens": 5}, 3, 2))

    def test_usage_status_line_formats_each_branch(self):
        formatter = chat_tui._short_count
        self.assertEqual(chat_tui_pm.usage_status_line([], formatter),
                         "用量（本 session）：尚無回合")
        self.assertEqual(chat_tui_pm.usage_status_line([{"usage": {}}, "missing"], formatter),
                         "用量（本 session）：UNKNOWN（2 回合沒有用量紀錄）")
        turns = [{"usage": {"input_tokens": 2400, "output_tokens": 10,
                             "cache_read_input_tokens": 3,
                             "cache_creation_input_tokens": 1000}}, {"usage": None}]
        self.assertEqual(chat_tui_pm.usage_status_line(turns, formatter),
                         "用量（本 session）：輸入 2.4k · 輸出 10 · 快取讀取 3 · 快取建立 1.0k tokens"
                         "（1 回合，1 回合沒有紀錄）")
        self.assertEqual(chat_tui_pm.usage_status_line(turns[:1], formatter),
                         "用量（本 session）：輸入 2.4k · 輸出 10 · 快取讀取 3 · 快取建立 1.0k tokens"
                         "（1 回合）")

    def test_missing_cache_usage_is_reported_and_recorded_zero_stays_zero(self):
        formatter = chat_tui._short_count
        turns = [{"usage": {"input_tokens": 22, "output_tokens": 14}}]
        totals, counted, missing = chat_tui_pm.usage_totals(turns)
        self.assertEqual(totals, {"input_tokens": 22, "output_tokens": 14,
                                  "cache_read_input_tokens": None,
                                  "cache_creation_input_tokens": None})
        self.assertEqual((counted, missing), (1, 0))
        self.assertEqual(chat_tui_pm.usage_status_line(turns, formatter),
                         "用量（本 session）：輸入 22 · 輸出 14 · 快取讀取 未回報 · 快取建立 未回報 tokens"
                         "（1 回合）")

        zero_cache = [{"usage": {"input_tokens": 22, "output_tokens": 14,
                                  "cache_read_input_tokens": 0}}]
        self.assertEqual(chat_tui_pm.usage_status_line(zero_cache, formatter),
                         "用量（本 session）：輸入 22 · 輸出 14 · 快取讀取 0 · 快取建立 未回報 tokens"
                         "（1 回合）")

    def test_usage_gaps_mark_partial_token_totals(self):
        formatter = chat_tui._short_count
        turns = [
            {"usage": {"input_tokens": 1, "output_tokens": 1,
                       "cache_read_input_tokens": 0}},
            {"usage": {"input_tokens": 1, "output_tokens": 1}},
        ]
        self.assertEqual(chat_tui_pm.usage_gaps(turns), {
            "input_tokens": 0, "output_tokens": 0,
            "cache_read_input_tokens": 1, "cache_creation_input_tokens": 2,
        })
        self.assertEqual(chat_tui_pm.usage_status_line(turns, formatter),
                         "用量（本 session）：輸入 2 · 輸出 2 · 快取讀取 ≥0 · 快取建立 未回報 tokens"
                         "（2 回合，部分回合缺少部分種類）")

    def test_true_and_negative_values_count_as_usage_gaps(self):
        turns = [
            {"usage": {"input_tokens": 1, "output_tokens": 2}},
            {"usage": {"input_tokens": True, "output_tokens": -1,
                       "cache_read_input_tokens": 0}},
        ]
        self.assertEqual(chat_tui_pm.usage_gaps(turns), {
            "input_tokens": 1, "output_tokens": 1,
            "cache_read_input_tokens": 1, "cache_creation_input_tokens": 2,
        })

    def test_truncated_history_marks_totals_as_lower_bounds(self):
        formatter = chat_tui._short_count
        turns = [{"seq": seq, "usage": {"input_tokens": 1, "output_tokens": 2,
                                          "cache_read_input_tokens": 3,
                                          "cache_creation_input_tokens": 4}}
                 for seq in range(51, 251)]
        self.assertEqual(chat_tui_pm.usage_status_line(turns, formatter),
                         "用量（本 session）：輸入 ≥200 · 輸出 ≥400 · 快取讀取 ≥600 · 快取建立 ≥800 tokens"
                         "（≥200 回合，前 50 回合已不在紀錄中）")

        turns[0]["seq"] = 1
        self.assertNotIn("≥", chat_tui_pm.usage_status_line(turns, formatter))

    def test_codex_cached_input_tokens_count_as_cache_read(self):
        # #683: Codex reports cache reads as cached_input_tokens and no cache creation.
        formatter = chat_tui._short_count
        turns = [{"usage": {"input_tokens": 1200, "cached_input_tokens": 800,
                            "output_tokens": 30}},
                 {"usage": {"input_tokens": 100, "cached_input_tokens": 50,
                            "output_tokens": 5}}]
        totals, counted, missing = chat_tui_pm.usage_totals(turns)
        self.assertEqual(totals, {"input_tokens": 1300, "output_tokens": 35,
                                  "cache_read_input_tokens": 850,
                                  "cache_creation_input_tokens": None})
        self.assertEqual((counted, missing), (2, 0))
        self.assertEqual(chat_tui_pm.usage_gaps(turns)["cache_read_input_tokens"], 0)
        self.assertEqual(chat_tui_pm.usage_status_line(turns, formatter),
                         "用量（本 session）：輸入 1.3k · 輸出 35 · 快取讀取 850 · 快取建立 未回報 tokens"
                         "（2 回合）")
        self.assertEqual(turns[0]["usage"], {"input_tokens": 1200, "cached_input_tokens": 800,
                                             "output_tokens": 30})   # the record is not changed

    def test_cache_read_input_tokens_wins_over_cached_input_tokens(self):
        formatter = chat_tui._short_count
        turns = [{"usage": {"input_tokens": 10, "output_tokens": 2,
                            "cache_read_input_tokens": 3, "cached_input_tokens": 99,
                            "cache_creation_input_tokens": 4}}]
        self.assertEqual(chat_tui_pm.usage_status_line(turns, formatter),
                         "用量（本 session）：輸入 10 · 輸出 2 · 快取讀取 3 · 快取建立 4 tokens"
                         "（1 回合）")
        # An invalid Claude value is not replaced by the Codex key.
        turns[0]["usage"]["cache_read_input_tokens"] = None
        self.assertIn("快取讀取 未回報", chat_tui_pm.usage_status_line(turns, formatter))

    def test_turn_end_usage_text_reads_codex_cached_input_tokens(self):
        codex = {"input_tokens": 1200, "cached_input_tokens": 800, "output_tokens": 30}
        self.assertEqual(chat_tui_pm.usage_text(codex),
                         "input 1200, output 30, cache read input 800 (provider-reported)")
        self.assertIn("usage: input 1200, output 30, cache read input 800 (provider-reported)",
                      chat_tui_pm.turn_end_text({"type": "TURN_END", "status": "COMPLETED",
                                                 "usage": codex}, 1.0))
        self.assertNotIn("cache creation", chat_tui_pm.usage_text(codex))
        claude = {"input_tokens": 5, "output_tokens": 6, "cache_read_input_tokens": 7,
                  "cache_creation_input_tokens": 8}
        self.assertEqual(chat_tui_pm.usage_text(claude),
                         "input 5, output 6, cache read input 7, cache creation input 8 "
                         "(provider-reported)")
        self.assertEqual(chat_tui_pm.usage_text({}), chat_tui_pm.UNKNOWN)

    def test_read_all_turns_pages_until_empty_page(self):
        # This mocked fixture tests the paging loop, not the store's 200-turn capacity.
        turns = [{"seq": index} for index in range(1, 451)]
        calls = []

        def read_history(session_id, *, after, limit, state_root):
            calls.append((session_id, after, limit, state_root))
            page = [turn for turn in turns if turn['seq'] > after][:limit]
            return {"turns": page, "next_after": page[-1]['seq'] if page else after}

        api = chat_tui_pm.PMRuntimeAPI(state_root="state-root")
        with patch("llmtool.pm_runtime.read_history", side_effect=read_history):
            self.assertEqual(api.read_all_turns("session-id"), turns)
        self.assertEqual(calls, [("session-id", 0, 200, "state-root"),
                                 ("session-id", 200, 200, "state-root"),
                                 ("session-id", 400, 200, "state-root"),
                                 ("session-id", 450, 200, "state-root")])


class PMStatusRowTests(unittest.TestCase):
    """#683 RD3, RD5, B6: /status cwd, model and permission text for Claude and Codex."""

    def test_permission_mode_is_auto_for_claude_and_the_sandbox_for_codex(self):
        from llmtool import pm_runtime
        self.assertEqual(chat_tui_pm.permission_mode(), pm_runtime.PM_PERMISSION_MODE)
        self.assertEqual(chat_tui_pm.permission_mode('claude-code'), 'auto')
        self.assertEqual(chat_tui_pm.permission_mode('codex'), 'workspace-write')

    def test_codex_sandbox_text_matches_the_sandbox_codex_is_started_with(self):
        from unittest.mock import Mock
        from llmtool import pm_runtime_codex
        runtime = pm_runtime_codex.CodexRuntime(Mock(cwd="/pm-cwd"))
        popen = Mock()
        with patch.object(runtime, "_environment", return_value={"PATH": "/bin"}), \
                patch.object(pm_runtime_codex.shutil, "which", return_value="/bin/codex"), \
                patch.object(pm_runtime_codex.subprocess, "Popen", popen):
            runtime._launch("hello")
        argv = popen.call_args.args[0]
        self.assertEqual(argv[argv.index("--sandbox") + 1], chat_tui_pm.CODEX_SANDBOX)

    def test_cwd_text_is_home_relative_or_full_never_dot(self):
        with patch.dict("os.environ", {"HOME": "/home/pm-user"}):
            self.assertEqual(chat_tui_pm.cwd_text("/home/pm-user/repo/sub"), "~/repo/sub")
            self.assertEqual(chat_tui_pm.cwd_text("/home/pm-user"), "~")
            self.assertEqual(chat_tui_pm.cwd_text("/srv/repo"), "/srv/repo")
            self.assertEqual(chat_tui_pm.cwd_text("/"), "[root]")
        self.assertEqual(chat_tui_pm.cwd_text(None), chat_tui_pm.UNKNOWN)
        self.assertEqual(chat_tui_pm.cwd_text(""), chat_tui_pm.UNKNOWN)
        self.assertEqual(chat_tui_pm.cwd_text("/srv/bad\x01dir"), chat_tui_pm.UNKNOWN)

    def test_model_status_line_for_claude_and_codex(self):
        from unittest.mock import Mock
        label = "模型（本機設定，不是實際使用的模型）："
        api = Mock()
        api.pm_model.return_value = "claude-test-model"
        self.assertEqual(chat_tui_pm.model_status_line(api, "claude-code", "claude-a"),
                         label + "claude-test-model")
        api.pm_model.assert_called_once_with("claude-a")
        api.pm_model.return_value = None
        self.assertEqual(chat_tui_pm.model_status_line(api, "claude-code"),
                         label + "llmtool 未設定（Claude Code 使用預設模型）")
        api.pm_model.side_effect = OSError("config unreadable")
        self.assertEqual(chat_tui_pm.model_status_line(api, "claude-code"),
                         label + "UNKNOWN（設定無法讀取）")
        codex = Mock()
        self.assertEqual(chat_tui_pm.model_status_line(codex, "codex", "codex-a"),
                         label + "llmtool 未設定（使用 Codex CLI 預設模型）")
        codex.pm_model.assert_not_called()

    def test_pm_model_resolves_roles_pm_for_the_bound_account(self):
        from llmtool import role_config
        calls = []

        def resolve(role, **options):
            calls.append((role, options))
            return {"model": "claude-test-model"}

        api = chat_tui_pm.PMRuntimeAPI(environ={"HOME": "/home/pm-user"})
        with patch.object(role_config, "resolve_role", side_effect=resolve):
            self.assertEqual(api.pm_model("claude-a"), "claude-test-model")
            self.assertEqual(api.pm_model(), "claude-test-model")
        self.assertEqual(calls, [
            ("pm", {"environ": {"HOME": "/home/pm-user"}, "home": "/home/pm-user",
                    "cli": {"account": "claude-a"}}),
            ("pm", {"environ": {"HOME": "/home/pm-user"}, "home": "/home/pm-user"})])
        with patch.object(role_config, "resolve_role", return_value={"model": None}):
            self.assertIsNone(api.pm_model())
        with patch.object(role_config, "resolve_role",
                          side_effect=role_config.LLMToolError("bad config")):
            with self.assertRaises(role_config.LLMToolError):
                api.pm_model()


class PMLogsTests(unittest.TestCase):
    def test_log_lines_screens_and_bounds_tool_names(self):
        turns = [
            {"started_at": "2026-10-28T00:00:00+00:00", "status": "COMPLETED",
             "tool_names": ["/bin/echo hi"]},
            {"started_at": "2026-10-28T00:00:00+00:00", "status": "COMPLETED",
             "tool_names": ["a" + chr(10) + "b"]},
            {"started_at": "2026-10-28T00:00:00+00:00", "status": "COMPLETED",
             "tool_names": ["a" * 70]},
        ]
        for turn in turns[:2]:
            rows = chat_tui_pm.log_lines([turn])
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0].endswith("Tool"))
        rows = chat_tui_pm.log_lines([turns[2]])
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].endswith("a" * 59 + "…"))
        self.assertEqual(chat_tui_pm.tool_label({"name": "a" * 70}), "a" * 70)

    def test_log_lines_screens_status_and_reason_with_safe_code(self):
        rows = chat_tui_pm.log_lines([
            {"status": "plain status words"},
            {"status": 7},
            {"status": "COMPLETED", "reason": "plain reason words"},
            {"status": "COMPLETED", "reason": 7},
        ])
        self.assertEqual(rows[0].split("｜")[1], "UNKNOWN")
        self.assertEqual(rows[1].split("｜")[1], "UNKNOWN")
        self.assertEqual(rows[2].split("｜")[-1], "UNKNOWN")
        self.assertEqual(rows[3].split("｜")[-1], "UNKNOWN")

    def test_log_lines_statuses_origin_tools_reason_and_invalid_rows(self):
        with patch.object(chat_tui_pm, "safe_name",
                          side_effect=lambda name: "[withheld]" if name == "stubbed-tool" else name):
            rows = chat_tui_pm.log_lines([
                {"started_at": "2026-10-28T00:00:00+00:00", "status": "COMPLETED",
                 "origin": "foreground", "tool_names": ["stubbed-tool"]},
                {"started_at": "2026-10-28T00:00:00+00:00", "status": "INTERRUPTED",
                 "origin": "background", "tool_names": [], "reason": "USER_INTERRUPT"},
                {"started_at": "2026-10-28T00:00:00+00:00", "status": "ERROR"},
                {"started_at": "2026-10-28T00:00:00+00:00", "status": "ACTIVE"},
                {"started_at": "2026-10-28T00:00:00+00:00", "status": "OTHER"},
                "skip this row",
            ], now=datetime.fromisoformat("2026-10-28T00:00:00+00:00"))
        self.assertIn("｜完成｜前景｜[withheld]", rows[0])
        self.assertEqual(rows[1].split("｜")[1:], ["已中斷", "背景", "無工具", "USER_INTERRUPT"])
        self.assertEqual(rows[2].split("｜")[1:], ["錯誤", "前景", "無工具"])
        self.assertEqual(rows[3].split("｜")[1:], ["進行中", "前景", "無工具"])
        self.assertEqual(rows[4].split("｜")[1:], ["OTHER", "前景", "無工具"])
        self.assertEqual(len(rows), 5)

    def test_tool_label_keeps_existing_description_and_name_behavior(self):
        self.assertEqual(chat_tui_pm.tool_label({"name": "Read", "description": "Read docs/a.md"}),
                         "Read docs/a.md")
        self.assertEqual(chat_tui_pm.tool_label({"name": "/bin/echo hi"}), "Tool")

    def test_log_lines_never_shows_message_or_tool_input_fields(self):
        row = {"started_at": "2026-10-28T00:00:00+00:00", "status": "COMPLETED",
               "owner_text": "plain owner fixture", "result_text": "plain result fixture",
               "tool_names": ["plain-tool"], "input": {"command": "plain command fixture"}}
        rendered = "\n".join(chat_tui_pm.log_lines([row]))
        for private_value in (row["owner_text"], row["result_text"], row["input"]["command"]):
            self.assertNotIn(private_value, rendered)

    def test_read_recent_turns_passes_latest_and_state_root(self):
        expected = [{"seq": 1}]
        with patch("llmtool.pm_runtime.read_history", return_value={"turns": expected}) as read:
            api = chat_tui_pm.PMRuntimeAPI(state_root="state-root")
            self.assertIs(api.read_recent_turns("session-id", 20), expected)
        read.assert_called_once_with("session-id", latest=20, state_root="state-root")


if __name__ == "__main__":
    unittest.main()
