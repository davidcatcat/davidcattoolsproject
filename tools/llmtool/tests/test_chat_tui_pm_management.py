"""Focused coverage for the chat TUI's saved PM account/session management."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import chat_tui_pm, role_config


class PMManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.claude_home = self.root / "claude-home"
        self.claude_work_home = self.root / "claude-work-home"
        self.codex_home = self.root / "codex-home"
        self.unsafe_home = self.root / "unsafe-home"
        for folder in (self.claude_home, self.claude_work_home, self.codex_home,
                       self.unsafe_home):
            folder.mkdir(mode=0o700)
        for folder in (self.claude_home, self.claude_work_home, self.codex_home):
            folder.chmod(0o700)
        self.unsafe_home.chmod(0o777)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self._script("claude", "exit 0\n")
        self._script("codex", "exit 7\n")
        self.environ = {"HOME": str(self.root), "PATH": str(self.bin)}
        self.config = {
            "accounts": {
                "claude-main": {"kind": "claude", "config_dir": str(self.claude_home)},
                "claude-work": {"kind": "claude", "config_dir": str(self.claude_work_home)},
                "codex-main": {"kind": "codex", "config_dir": str(self.codex_home)},
                "unsafe": {"kind": "claude", "config_dir": str(self.unsafe_home)},
            },
            "roles": {},
        }
        self.config_path = self.root / ".config" / "llmtool" / "config.yaml"
        self.config_path.parent.mkdir(parents=True)
        self._write_config()
        self.api = chat_tui_pm.PMRuntimeAPI(state_root=self.state, environ=self.environ)

    def _write_config(self):
        self.config_path.write_text(yaml.safe_dump(self.config, sort_keys=True))

    def _script(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\nprintf '%s\\n' 'secret-provider-output'\n" + body)
        path.chmod(0o700)

    def test_account_listing_hides_paths_and_marks_unsafe_directory(self):
        rows = self.api.accounts()
        self.assertEqual([(row["name"], row["kind"], row["status"]) for row in rows], [
                ("claude-main", "claude", "private"),
                ("claude-work", "claude", "private"),
                ("codex-main", "codex", "private"),
                ("unsafe", "claude", "not private"),
        ], msg="actual account rows: %r" % rows)

    def test_check_discards_provider_output_and_rejects_unsafe_folder(self):
        checks = self.api.account_checks()
        self.assertEqual(checks, [True, True, False, False])

    def test_create_list_select_and_reselect_saved_binding_without_config_write(self):
        config_path = self.config_path
        before = (config_path.read_bytes(), config_path.stat().st_mtime_ns)

        def resolve(role, cli=None, environ=None, home=None):
            account = (cli or {}).get("account")
            if account == "codex-main":
                return {"provider": "codex", "account": account,
                        "config_dir": str(self.codex_home)}
            return {"provider": "claude", "account": account,
                    "config_dir": str(self.claude_home)}

        with patch.object(role_config, "resolve_role", side_effect=resolve):
            first = self.api.create_dedicated(str(self.root), "claude-code", "claude-main")
            second = self.api.create_dedicated(str(self.root), "codex", "codex-main")
            rows = self.api.list_bindings()
            selected = self.api.select_binding(first["native_session_id"])
            current = self.api.current_binding()
            selected_again = self.api.select_binding(second["native_session_id"])
        self.assertEqual([row["native_session_id"] for row in rows],
                         [second["native_session_id"], first["native_session_id"]])
        self.assertEqual(selected["runtime"], "claude-code")
        self.assertEqual(current["native_session_id"], first["native_session_id"])
        self.assertEqual(selected_again["runtime"], "codex")
        self.assertEqual((config_path.read_bytes(), config_path.stat().st_mtime_ns), before)


if __name__ == "__main__":
    unittest.main()
