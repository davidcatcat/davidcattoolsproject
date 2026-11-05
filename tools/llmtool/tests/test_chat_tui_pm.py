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

    def test_read_all_turns_pages_until_empty_page(self):
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


if __name__ == "__main__":
    unittest.main()
