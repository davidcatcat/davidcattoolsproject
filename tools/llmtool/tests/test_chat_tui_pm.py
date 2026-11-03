"""Focused replay display and PM configuration guidance tests."""
import sys
import unittest
from datetime import datetime
from pathlib import Path

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
        self.assertIn("pm_runtime.codex_config_dir for Codex", hint)
        self.assertIn("roles.pm account and model", hint)


if __name__ == "__main__":
    unittest.main()
