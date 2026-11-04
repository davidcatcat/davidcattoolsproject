import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "guidance_drift_check.py"
SPEC = importlib.util.spec_from_file_location("guidance_drift_check", SCRIPT)
guidance_drift_check = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = guidance_drift_check
SPEC.loader.exec_module(guidance_drift_check)
check = guidance_drift_check.check


class GuidanceDriftCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        self.agents = (
            "Decision https://example.invalid/decision.\n"
            "Dispatch `ALLOWED`. Model `gpt-6-luna`. Check `check (docs)`.\n"
        )
        self.claude = self.agents
        self.write_guidance()
        self.write_config()

    def tearDown(self):
        self.temp.cleanup()

    def write_guidance(self):
        (self.repo / "AGENTS.md").write_text(self.agents, encoding="utf-8")
        (self.repo / "CLAUDE.md").write_text(self.claude, encoding="utf-8")

    def write_config(self, *, schema_version=1, codex_only=None):
        hashes = {
            name: hashlib.sha256((self.repo / name).read_bytes()).hexdigest()
            for name in ("AGENTS.md", "CLAUDE.md")
        }
        config = {
            "schema_version": schema_version,
            "hashes": hashes,
            "codex_only": [] if codex_only is None else codex_only,
        }
        path = self.repo / "tools" / "llmtool" / "guidance-drift.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config), encoding="utf-8")

    def test_pass(self):
        self.assertEqual(check(self.repo), [])

    def test_hash_stale_for_each_guidance_file(self):
        for name in ("AGENTS.md", "CLAUDE.md"):
            with self.subTest(name=name):
                self.write_guidance()
                self.write_config()
                path = self.repo / name
                path.write_text(path.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
                self.assertTrue(any(f"{name}: HASH_STALE" in item for item in check(self.repo)))

    def test_missing_in_claude_for_each_value_class(self):
        cases = {
            "URL": ("https://example.invalid/decision", "https://missing.invalid/decision"),
            "dispatch": ("ALLOWED", "OWNER_ASSIGNMENT_ONLY"),
            "model": ("gpt-6-luna", "gpt-6.1-sol"),
            "check": ("check (docs)", "check (Linux 3.14)"),
        }
        for label, (present, missing) in cases.items():
            with self.subTest(label=label):
                self.agents = f"`{present}` and `{missing}`\n"
                self.claude = f"`{present}`\n"
                self.write_guidance()
                self.write_config()
                findings = check(self.repo)
                self.assertTrue(any(f"MISSING_IN_CLAUDE {missing}" in item for item in findings))

    def test_stale_exception(self):
        self.write_config(codex_only=[{"value": "https://stale.invalid/value", "reason": "No longer in AGENTS.md."}])
        self.assertTrue(any("STALE_EXCEPTION https://stale.invalid/value" in item for item in check(self.repo)))

    def test_codex_only_exception_covers_missing_claude_value(self):
        self.agents = "Codex policy https://codex.invalid/policy\n"
        self.claude = "Claude guidance\n"
        self.write_guidance()
        self.write_config(codex_only=[{"value": "https://codex.invalid/policy", "reason": "Codex-only operational reference."}])
        self.assertEqual(check(self.repo), [])

    def test_malformed_config_fails_closed(self):
        config = self.repo / "tools" / "llmtool" / "guidance-drift.json"
        config.write_text("{", encoding="utf-8")
        self.assertTrue(any("CONFIG_INVALID" in item for item in check(self.repo)))

    def test_missing_config_fails_closed(self):
        (self.repo / "tools" / "llmtool" / "guidance-drift.json").unlink()
        self.assertTrue(any("CONFIG_MISSING" in item for item in check(self.repo)))

    def test_wrong_schema_fails_closed(self):
        self.write_config(schema_version=2)
        self.assertTrue(any("CONFIG_SCHEMA" in item for item in check(self.repo)))


if __name__ == "__main__":
    unittest.main()
