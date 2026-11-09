import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def write_raw_config(self, raw):
        path = self.repo / "tools" / "llmtool" / "guidance-drift.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw, encoding="utf-8")

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

    def test_dispatch_values_are_matched_as_whole_words_in_full_source(self):
        self.agents = "Worker is PAUSED now, **ALLOWED** only, dispatch=OWNER_ASSIGNMENT_ONLY.\n"
        self.claude = "PAUSED and **ALLOWED**, dispatch=OWNER_ASSIGNMENT_ONLY.\n"
        self.write_guidance()
        self.write_config()
        self.assertEqual(check(self.repo), [])

        self.claude = "PAUSEDly and ALLOWED_ONLY\n"
        self.write_guidance()
        self.write_config()
        findings = check(self.repo)
        self.assertTrue(any("MISSING_IN_CLAUDE ALLOWED" in item for item in findings))
        self.assertTrue(any("MISSING_IN_CLAUDE OWNER_ASSIGNMENT_ONLY" in item for item in findings))

    def test_url_and_model_extraction_handles_case_wrappers_and_families(self):
        self.agents = (
            "**HTTPS://x.invalid/a** and https://x.invalid/a_(b).\n"
            "Model `gpt-7-x high`; option --model gpt-8-y.\n"
            "Models qwen3-coder and kimi-k2.\n"
        )
        self.claude = "https://x.invalid/a https://x.invalid/a_(b).\n"
        self.write_guidance()
        self.write_config()
        findings = check(self.repo)
        for missing in ("gpt-7-x", "gpt-8-y", "qwen3-coder", "kimi-k2"):
            self.assertTrue(any(f"MISSING_IN_CLAUDE {missing}" in item for item in findings), missing)
        self.claude += "gpt-7-x and gpt-8-y qwen3-coder kimi-k2\n"
        self.write_guidance()
        self.write_config()
        self.assertEqual(check(self.repo), [])

    def test_model_extraction_handles_sentence_periods_versions_and_partial_names(self):
        source = (
            "gpt-8-y. kimi-k2. gpt-5.5. gpt-5.5.2. "
            "prefixgpt-8-y gpt-8-y.2 xkimi-k2\n"
        )
        models = {
            value for value in guidance_drift_check._hard_values(source)
            if guidance_drift_check.MODEL_NAME.fullmatch(value)
        }
        self.assertEqual(models, {"gpt-8-y", "kimi-k2", "gpt-5.5", "gpt-5.5.2", "gpt-8-y.2"})

    def test_model_extraction_handles_provider_ids_and_avoids_paths_and_prose(self):
        source = (
            "anthropic/claude-opus-5-5 openrouter/qwen3-coder o4-mini glm-5 "
            "minimax-m2 gpt5 gpt-5.5.2.\n"
            "File tools/llmtool/scripts/gpt-5.py; claude-reviewer and Claude-authored.\n"
        )
        models = {
            value for value in guidance_drift_check._hard_values(source)
            if guidance_drift_check.MODEL_NAME.fullmatch(value)
        }
        self.assertEqual(
            models,
            {
                "anthropic/claude-opus-5-5",
                "openrouter/qwen3-coder",
                "o4-mini",
                "glm-5",
                "minimax-m2",
                "gpt5",
                "gpt-5.5.2",
            },
        )

    def test_model_extraction_rejects_file_extensions(self):
        source = (
            "llama-3.md gpt-6-luna.md claude-opus-5-5.py "
            "gpt-5.5 gpt-5.5.2 gpt-6.1-sol claude-opus-5-5 "
            "anthropic/claude-opus-5-5 o4-mini\n"
        )
        models = {
            value for value in guidance_drift_check._hard_values(source)
            if guidance_drift_check.MODEL_NAME.fullmatch(value)
        }
        self.assertEqual(
            models,
            {
                "gpt-5.5",
                "gpt-5.5.2",
                "gpt-6.1-sol",
                "claude-opus-5-5",
                "anthropic/claude-opus-5-5",
                "o4-mini",
            },
        )

    def test_url_extraction_strips_closing_parens_and_quotes(self):
        source = '(see https://x.invalid/a.) and "https://x.invalid/a"\n'
        urls = {value for value in guidance_drift_check._hard_values(source) if value.startswith("https://")}
        self.assertEqual(urls, {"https://x.invalid/a"})

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

    def test_config_decoder_recursion_error_fails_closed(self):
        with patch.object(guidance_drift_check.json, "loads", side_effect=RecursionError) as loads:
            findings = check(self.repo)
        loads.assert_called_once()
        self.assertTrue(callable(loads.call_args.kwargs["object_pairs_hook"]))
        self.assertEqual(
            findings,
            ["tools/llmtool/guidance-drift.json: CONFIG_INVALID RecursionError"],
        )

    def test_duplicate_and_unknown_config_keys_are_invalid(self):
        self.write_raw_config('{"schema_version":1,"schema_version":1,"hashes":{},"codex_only":[]}')
        self.assertTrue(any("CONFIG_INVALID" in item for item in check(self.repo)))
        self.write_config()
        path = self.repo / "tools" / "llmtool" / "guidance-drift.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["unexpected"] = True
        path.write_text(json.dumps(config), encoding="utf-8")
        self.assertTrue(any("CONFIG_INVALID" in item for item in check(self.repo)))

    def test_invalid_codex_only_entries_fail_closed(self):
        for entries in (["https://example.invalid"], [{"value": "x", "reason": "ok", "extra": 1}],
                        [{"value": "x", "reason": ""}], [{"value": "x", "reason": "ok\nnext"}]):
            with self.subTest(entries=entries):
                self.write_config(codex_only=entries)
                self.assertTrue(any("CONFIG_INVALID" in item for item in check(self.repo)))

    def test_guidance_file_missing_and_invalid_are_reported(self):
        agents = self.repo / "AGENTS.md"
        agents.unlink()
        findings = check(self.repo)
        self.assertTrue(any("AGENTS.md: FILE_MISSING" in item for item in findings))

        agents.write_bytes(b"\xff")
        findings = check(self.repo)
        self.assertTrue(any("AGENTS.md: FILE_INVALID" in item for item in findings))

    def test_real_repository_guidance_is_current(self):
        repository = Path(__file__).resolve().parents[3]
        self.assertEqual(check(repository), [])

    def test_claude_md_agy_model_matches_route_default(self):
        repository = Path(__file__).resolve().parents[3]
        claude_md = (repository / "CLAUDE.md").read_text(encoding="utf-8")
        agy_lines = [line for line in claude_md.splitlines() if line.startswith("  - AGY: use `")]
        self.assertEqual(len(agy_lines), 1)
        model = agy_lines[0].split("`", 2)[1]
        routes_path = repository / "tools" / "llmtool" / "developer-routes.json"
        with routes_path.open(encoding="utf-8") as routes_file:
            data = json.load(routes_file)
        self.assertEqual(model, data["routes"]["agy"]["default_model"])


if __name__ == "__main__":
    unittest.main()
