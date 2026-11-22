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

# Fictional model names used by the fixtures below; the fixture
# developer-routes.json allows all of them.
FIXTURE_MODELS = ("gpt-6-luna", "gpt-6.1-sol", "gpt-7-x", "gpt-8-y", "qwen3-coder", "kimi-k2")
_DELETE = object()


def fixture_routes(models=FIXTURE_MODELS, roles=None, *, allowed=True):
    routes = {
        "schema_version": 1,
        "routes": {
            "fixture": {
                "executable": "fixture-cli",
                "default_model": models[0],
                "allowed_models": list(models),
                "default_effort": "medium",
                "default_timeout": 1800,
                "max_timeout": 3600,
            },
        },
    }
    if not allowed:
        # #2302: allowed_models is optional; the route names only its default.
        del routes["routes"]["fixture"]["allowed_models"]
    if roles is not None:
        routes["roles"] = roles
    return routes


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
        self.write_rule("developer-routes.json", fixture_routes())

    def tearDown(self):
        self.temp.cleanup()

    def write_rule(self, name, value):
        path = self.repo / "tools" / "llmtool" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")

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

    def test_claude_md_agy_line_names_no_model(self):
        # Owner #289 (2026-11-22): the CLAUDE.md AGY line names no model and
        # says the AGY model comes from the configuration, so a model switch
        # by configuration needs no rule-file change.
        repository = Path(__file__).resolve().parents[3]
        claude_md = (repository / "CLAUDE.md").read_text(encoding="utf-8")
        agy_lines = [line for line in claude_md.splitlines() if line.startswith("  - AGY: ")]
        self.assertEqual(len(agy_lines), 1)
        line = agy_lines[0]
        self.assertIn("model comes from the configuration", line)
        self.assertNotIn("gemini", line.lower())
        self.assertNotIn("flash", line.lower())
        self.assertNotIn("hydrogen", line.lower())

    def test_same_family_model_switch_passes_full_check(self):
        # #2302: docs name a same-family model that is not the route default
        # nor any allowed_models entry; the route has no allowed_models and a
        # role has only a default_model. No finding.
        self.agents = "Model `gpt-9-next`; reviewer kimi-k3.\n"
        self.claude = self.agents
        self.write_guidance()
        self.write_config()
        self.write_rule("developer-routes.json", fixture_routes(
            ("gpt-6-luna",), allowed=False,
            roles={"fixture-reviewer": {"default_model": "kimi-k2"}}))
        self.assertEqual(check(self.repo), [])

    def test_cross_family_model_still_fails_full_check(self):
        self.agents = "Model `llama-9-other`.\n"
        self.claude = self.agents
        self.write_guidance()
        self.write_config()
        self.write_rule("developer-routes.json", fixture_routes(("gpt-6-luna",), allowed=False))
        self.assertEqual(check(self.repo), ["AGENTS.md: MODEL_NOT_ALLOWED llama-9-other",
                                            "CLAUDE.md: MODEL_NOT_ALLOWED llama-9-other"])


# #2281 L0b: the limit-table keys (rows 2, 15, 16) at their recorded values;
# L7 adds row 17, the D6 writer lease length in hours.
POLICY_LIMIT_KEYS_2281 = {
    "spec_preflight_large_file_lines": 2000, "merge_train_members_max": 6,
    "merge_train_files_per_pr_max": 100, "revert_scope_prs_max": 5,
    "d6_writer_lease_hours": 24,
}


def valid_rule_files():
    """Minimal valid documents for each registered rule-file schema."""
    return {
        "developer-routes.json": fixture_routes(
            roles={"fixture-reviewer": {"allowed_models": ["gpt-6.1-sol"]}}),
        # #2367 (Owner 2026-11-19): the repository limits.
        "policy.json": {
            "schema_version": 1, "repair_max": 5, "parent_debug_max": 1,
            "group_debug_max": 3, "child_max": 3, "replan_max": 1,
            "test_fail_max": 5, "review_fail_max": 6, "waiting_notify_after_hours": 24,
        },
        "worker-dispatch.json": {
            "schema_version": 1,
            "workers": {
                "fixture-worker": {
                    "dispatch": "PAUSED",
                    "decided_by": "Owner",
                    "decided_at": "2026-11-14",
                    "reason": "Fixture entry",
                    "evidence": "https://example.invalid/decision",
                },
            },
        },
        "workflow-grants.json": {
            "schema_version": 1,
            "grants": [],
        },
        "sensitive-paths.json": {
            "schema_version": 1,
            "exact": ["tools/llmtool/sensitive-paths.json"],
            "prefixes": [".github/"],
            "globs": ["tools/llmtool/llmtool/authorization_*.py"],
        },
        "execution-resources.json": {
            "schema_version": 1,
            "purpose": "Informational inventory only",
            "as_of": "2026-11-14",
            "hosts": [{
                "id": "local-mac", "kind": "owner-local", "os": "macOS",
                "reported_memory_gib": 8, "capabilities": ["macos-native-validation"],
                "reference": "tools/mac-runner/README.md",
            }],
            "live_online_state": "UNKNOWN",
            "live_busy_state": "UNKNOWN",
            "remote_development_authority": "NOT_GRANTED",
        },
    }


class RuleFileSchemaTests(unittest.TestCase):
    """#1502 WP-G: every tools/llmtool/*.json rule file has a strict schema."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        self.rules = self.repo / "tools" / "llmtool"
        self.rules.mkdir(parents=True)
        self.valid = valid_rule_files()
        self.reset_rules()

    def tearDown(self):
        self.temp.cleanup()

    def reset_rules(self):
        for name, value in self.valid.items():
            (self.rules / name).write_text(json.dumps(value), encoding="utf-8")

    def findings_after(self, name, value):
        path = self.rules / name
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
        return guidance_drift_check.check_rule_files(self.repo)

    def test_valid_rule_files_pass(self):
        self.assertEqual(guidance_drift_check.check_rule_files(self.repo), [])

    def test_policy_schema_accepts_the_new_and_old_repair_limits(self):
        # #2367: the schema accepts the 5-repair values and the earlier ones.
        policy = self.valid["policy.json"]
        for values in ({"repair_max": 5, "test_fail_max": 5, "review_fail_max": 6,
                        "group_debug_max": 3},
                       {"repair_max": 2, "test_fail_max": 2, "review_fail_max": 3,
                        "group_debug_max": 1}):
            with self.subTest(values=values):
                self.assertEqual(self.findings_after("policy.json", {**policy, **values}), [])

    def test_policy_schema_accepts_the_2281_limit_keys(self):
        # #2281 L0b: rows 2, 15 and 16 of the limit table are optional keys,
        # each a positive integer with no upper validation bound.
        policy = self.valid["policy.json"]
        self.assertEqual(self.findings_after("policy.json", {**policy, **POLICY_LIMIT_KEYS_2281}), [])
        for key in POLICY_LIMIT_KEYS_2281:
            for value in (1, 10 ** 9):
                with self.subTest(key=key, value=value):
                    self.assertEqual(self.findings_after("policy.json", {**policy, key: value}), [])

    def test_repository_policy_passes_the_rule_schema(self):
        # The real policy.json, with the #2281 keys, matches the registered schema.
        repository = Path(__file__).resolve().parents[3]
        self.assertFalse([item for item in guidance_drift_check.check_rule_files(repository)
                          if item.startswith("tools/llmtool/policy.json:")])

    def test_max_prompt_bytes_accepted(self):
        routes = fixture_routes()
        routes["routes"]["fixture"]["max_prompt_bytes"] = 5242880
        self.assertEqual(self.findings_after("developer-routes.json", routes), [])

    def test_max_source_bytes_accepted(self):
        routes = fixture_routes()
        routes["routes"]["fixture"]["max_source_file_bytes"] = 5242880
        routes["routes"]["fixture"]["max_source_total_bytes"] = 5242880
        self.assertEqual(self.findings_after("developer-routes.json", routes), [])

    def test_budgets_accepted(self):
        routes = fixture_routes()
        routes["routes"]["fixture"]["budgets"] = {
            "max_turns": 512,
            "max_input_tokens": 262144,
            "max_output_tokens": 65536,
            "max_wall_seconds": 1800,
            "max_tool_result_bytes": 262144,
            "max_sent_bytes": 16777216,
        }
        self.assertEqual(self.findings_after("developer-routes.json", routes), [])

    def test_unknown_top_level_key_fails_for_each_rule_file(self):
        for name, value in self.valid.items():
            with self.subTest(name=name):
                self.reset_rules()
                findings = self.findings_after(name, {**value, "unexpected": 1})
                self.assertIn(
                    f'tools/llmtool/{name}: RULE_SCHEMA $ has unknown key "unexpected"', findings)

    def test_missing_key_and_wrong_schema_version_fail(self):
        for name, value in self.valid.items():
            with self.subTest(name=name):
                self.reset_rules()
                broken = dict(value)
                broken["schema_version"] = "1"
                self.assertIn(f"tools/llmtool/{name}: RULE_SCHEMA $.schema_version must be 1",
                              self.findings_after(name, broken))
                del broken["schema_version"]
                self.assertIn(f'tools/llmtool/{name}: RULE_SCHEMA $ is missing key "schema_version"',
                              self.findings_after(name, broken))

    def test_nested_unknown_keys_and_wrong_types_fail(self):
        routes = fixture_routes()
        cases = [
            ("developer-routes.json", {**routes, "routes": {"fixture": {
                **routes["routes"]["fixture"], "extra": True}}}, "unknown key"),
            ("developer-routes.json", {**routes, "routes": {"fixture": {
                **routes["routes"]["fixture"], "allowed_models": "gpt-6-luna"}}}, "must be a list"),
            ("developer-routes.json", {**routes, "routes": {"fixture": {
                **routes["routes"]["fixture"], "allowed_models": []}}}, "at least 1"),
            ("developer-routes.json", {**routes, "routes": {"fixture": {
                **routes["routes"]["fixture"], "max_prompt_bytes": 0}}}, "must be an integer >= 1"),
            ("developer-routes.json", {**routes, "routes": {"fixture": {
                **routes["routes"]["fixture"], "max_prompt_bytes": True}}}, "must be an integer >= 1"),
            ("developer-routes.json", {**routes, "routes": {"fixture": {
                **routes["routes"]["fixture"], "max_prompt_bytes": "65536"}}}, "must be an integer >= 1"),
            ("developer-routes.json", {**routes, "routes": {"fixture": {
                **routes["routes"]["fixture"], "max_source_file_bytes": 0}}}, "must be an integer >= 1"),
            ("developer-routes.json", {**routes, "routes": {"fixture": {
                **routes["routes"]["fixture"], "max_source_file_bytes": True}}}, "must be an integer >= 1"),
            ("developer-routes.json", {**routes, "routes": {"fixture": {
                **routes["routes"]["fixture"], "max_source_file_bytes": "1048576"}}}, "must be an integer >= 1"),
            ("developer-routes.json", {**routes, "routes": {"fixture": {
                **routes["routes"]["fixture"], "max_source_total_bytes": 0}}}, "must be an integer >= 1"),
            ("developer-routes.json", {**routes, "routes": {"fixture": {
                **routes["routes"]["fixture"], "max_source_total_bytes": True}}}, "must be an integer >= 1"),
            ("developer-routes.json", {**routes, "routes": {"fixture": {
                **routes["routes"]["fixture"], "max_source_total_bytes": "2097152"}}}, "must be an integer >= 1"),
            ("developer-routes.json", {**routes, "routes": {"fixture": {
                **routes["routes"]["fixture"], "budgets": "not-a-dict"}}}, "must be an object"),
            ("developer-routes.json", {**routes, "routes": {"fixture": {
                **routes["routes"]["fixture"], "budgets": {
                    "max_turns": 0, "max_wall_seconds": 1800, "max_tool_result_bytes": 262144,
                    "max_sent_bytes": 16777216}}}}, "must be an integer >= 1"),
            ("developer-routes.json", {**routes, "routes": {"fixture": {
                **routes["routes"]["fixture"], "budgets": {
                    "max_turns": 1001, "max_wall_seconds": 1800, "max_tool_result_bytes": 262144,
                    "max_sent_bytes": 16777216}}}}, "must be an integer >= 1 and <= 1000"),
            ("developer-routes.json", {**routes, "routes": {}}, "at least 1"),
            ("sensitive-paths.json", {**self.valid["sensitive-paths.json"], "exact": "x"}, "must be a list"),
            ("sensitive-paths.json", {**self.valid["sensitive-paths.json"], "exact": [1]}, "non-empty string"),
            ("sensitive-paths.json", {**self.valid["sensitive-paths.json"], "exact": ["a/../b"]}, "invalid format"),
            ("sensitive-paths.json", {**self.valid["sensitive-paths.json"], "exact": ["/abs"]}, "invalid format"),
            ("sensitive-paths.json", {**self.valid["sensitive-paths.json"], "prefixes": ["secret"]}, "invalid format"),
            ("sensitive-paths.json", {**self.valid["sensitive-paths.json"], "globs": ["a", "a"]}, "duplicate"),
            ("workflow-grants.json", {"schema_version": 1, "grants": [{
                "issue": 2281, "owner_record_url": "https://example.invalid/record",
                "mode": "write", "paths": ["docs/ci.yml"]}]}, "invalid format"),
            ("workflow-grants.json", {"schema_version": 1, "grants": [{
                "issue": 2281, "owner_record_url": "https://example.invalid/record",
                "mode": "write",
                "paths": [".github/workflows/nested/ci.yml"]}]}, "invalid format"),
            ("workflow-grants.json", {"schema_version": 1, "grants": [{
                "issue": 2281, "mode": "write",
                "paths": [".github/workflows/ci.yml"]}]}, 'missing key "owner_record_url"'),
            ("workflow-grants.json", {"schema_version": 1, "grants": [{
                "issue": 2281, "owner_record_url": "https://example.invalid/record",
                "mode": "admin", "paths": [".github/workflows/ci.yml"]}]}, "must be one of"),
        ]
        host = self.valid["execution-resources.json"]["hosts"][0]
        resources = self.valid["execution-resources.json"]
        cases += [
            ("execution-resources.json", {**resources, "hosts": [{**host, "runners": []}]}, "unknown key"),
            ("execution-resources.json", {**resources, "hosts": [{**host, "reported_memory_gib": True}]}, "integer"),
            ("execution-resources.json", {**resources, "hosts": [{**host, "reported_memory_gib": "8"}]}, "integer"),
            ("execution-resources.json", {**resources, "live_online_state": "ONLINE"}, "must be"),
            ("execution-resources.json", {**resources, "remote_development_authority": "GRANTED"}, "must be"),
            ("execution-resources.json", {**resources, "as_of": "yesterday"}, "invalid format"),
        ]
        valid_routes = self.valid["developer-routes.json"]
        cases += [
            ("developer-routes.json", {**valid_routes, "roles": "codex-reviewer"}, "must be an object"),
            ("developer-routes.json", {**valid_routes, "roles": {}}, "at least 1"),
            ("developer-routes.json", {**valid_routes, "roles": {
                "fixture-reviewer": {"allowed_models": []}}}, "at least 1"),
            ("developer-routes.json", {**valid_routes, "roles": {
                "fixture-reviewer": {"allowed_models": "gpt-6.1-sol"}}}, "must be a list"),
            ("developer-routes.json", {**valid_routes, "roles": {
                "fixture-reviewer": {"allowed_models": ["gpt-6.1-sol"], "default": "x"}}}, "unknown key"),
            ("developer-routes.json", {**valid_routes, "roles": {
                "Fixture Reviewer": {"allowed_models": ["gpt-6.1-sol"]}}}, "invalid key"),
        ]
        policy = self.valid["policy.json"]
        policy_missing = {key: value for key, value in policy.items() if key != "repair_max"}
        cases += [
            ("policy.json", {**policy, "repair_max": "2"}, "integer"),
            ("policy.json", {**policy, "repair_max": True}, "integer"),
            ("policy.json", {**policy, "child_max": -1}, "integer"),
            ("policy.json", {**policy, "review_fail_max": 2.5}, "integer"),
            ("policy.json", {**policy, "waiting_notify_after_hours": 0}, "integer"),
            ("policy.json", policy_missing, 'missing key "repair_max"'),
        ]
        # #2281 L0b: each limit-table key must be a positive integer when present.
        for key in POLICY_LIMIT_KEYS_2281:
            cases += [
                ("policy.json", {**policy, key: 0}, "integer >= 1"),
                ("policy.json", {**policy, key: -1}, "integer >= 1"),
                ("policy.json", {**policy, key: "5"}, "integer >= 1"),
                ("policy.json", {**policy, key: True}, "integer >= 1"),
                ("policy.json", {**policy, key: 2.5}, "integer >= 1"),
            ]
        dispatch = self.valid["worker-dispatch.json"]
        worker = dispatch["workers"]["fixture-worker"]
        worker_missing = {key: value for key, value in worker.items() if key != "evidence"}

        def with_worker(entry, key="fixture-worker"):
            return {**dispatch, "workers": {key: entry}}

        cases += [
            ("worker-dispatch.json", {**dispatch, "workers": {}}, "at least 1"),
            ("worker-dispatch.json", {**dispatch, "workers": []}, "must be an object"),
            ("worker-dispatch.json", with_worker({**worker, "dispatch": "paused"}), "must be one of"),
            ("worker-dispatch.json", with_worker({**worker, "dispatch": "RUNNING"}), "must be one of"),
            ("worker-dispatch.json", with_worker({**worker, "hosts": []}), "unknown key"),
            ("worker-dispatch.json", with_worker(worker_missing), 'missing key "evidence"'),
            ("worker-dispatch.json", with_worker({**worker, "evidence": "http://x.invalid"}), "invalid format"),
            ("worker-dispatch.json", with_worker({**worker, "decided_at": "today"}), "invalid format"),
            ("worker-dispatch.json", with_worker({**worker, "reason": "one\ntwo"}), "one line"),
            ("worker-dispatch.json", with_worker({**worker, "reason": ""}), "non-empty string"),
            ("worker-dispatch.json", with_worker(worker, "Bad Worker"), "invalid key"),
        ]
        for name, value, expected in cases:
            with self.subTest(name=name, expected=expected, value=value):
                self.reset_rules()
                findings = self.findings_after(name, value)
                self.assertTrue(
                    any(item.startswith(f"tools/llmtool/{name}: RULE_SCHEMA ") and expected in item
                        for item in findings),
                    findings,
                )

    def test_every_real_rule_file_has_a_registered_schema(self):
        # #1502 WP-G: no tools/llmtool/*.json rule file is left without a schema.
        repository = Path(__file__).resolve().parents[3]
        rule_files = {path.name for path in (repository / "tools" / "llmtool").glob("*.json")
                      if not path.name.startswith(".")}
        self.assertTrue({"policy.json", "worker-dispatch.json"} <= rule_files)
        self.assertEqual(rule_files - set(guidance_drift_check.RULE_SCHEMAS), set())
        self.assertFalse(any("RULE_SCHEMA_MISSING" in item
                             for item in guidance_drift_check.check_rule_files(repository)))

    def test_routes_without_roles_still_pass(self):
        self.assertEqual(self.findings_after("developer-routes.json", fixture_routes()), [])

    def test_route_without_allowed_models_passes(self):
        # #2302: allowed_models is optional; default_model alone is enough.
        self.assertEqual(self.route_findings(allowed_models=_DELETE), [])

    def test_route_allowed_models_need_not_be_a_single_default_entry(self):
        # #2302: several entries, and a default that is not listed, both pass.
        self.assertEqual(self.route_findings(allowed_models=["gpt-6-luna", "gpt-7-x"]), [])
        self.assertEqual(self.route_findings(default_model="gpt-9-next", allowed_models=["gpt-6-luna"]), [])

    def test_route_default_model_is_still_required(self):
        prefix = "tools/llmtool/developer-routes.json: RULE_SCHEMA $.routes.fixture"
        self.assertIn(f'{prefix} is missing key "default_model"',
                      self.route_findings(default_model=_DELETE, allowed_models=_DELETE))

    def test_role_default_model_shapes_pass(self):
        # #2302: a role may carry default_model, allowed_models, or both.
        for role in ({"default_model": "gpt-6.1-sol"},
                     {"default_model": "gpt-6.1-sol", "allowed_models": ["gpt-6.1-sol", "gpt-7-x"]},
                     {"default_model": "gpt-7-x", "allowed_models": ["gpt-6.1-sol"]},
                     {"allowed_models": ["gpt-6.1-sol", "gpt-7-x"]},
                     {"default_model": "gpt-6.1-sol", "timeout": 600}):
            with self.subTest(role=role):
                routes = fixture_routes(roles={"fixture-reviewer": role})
                self.assertEqual(self.findings_after("developer-routes.json", routes), [])

    def test_role_without_any_model_or_with_bad_default_fails(self):
        prefix = "tools/llmtool/developer-routes.json: RULE_SCHEMA $.roles.fixture-reviewer"
        cases = [
            ({}, f"{prefix} needs default_model or allowed_models"),
            ({"timeout": 600}, f"{prefix} needs default_model or allowed_models"),
            ({"default_model": ""}, f"{prefix}.default_model must be a non-empty string"),
            ({"default_model": ["gpt-6.1-sol"]}, f"{prefix}.default_model must be a non-empty string"),
            ({"default_model": "gpt-6.1-sol\nx"}, f"{prefix}.default_model must be one line"),
            ({"default_model": "gpt-6.1-sol", "allowed_models": []},
             f"{prefix}.allowed_models needs at least 1 item(s)"),
        ]
        for role, expected in cases:
            with self.subTest(role=role):
                routes = fixture_routes(roles={"fixture-reviewer": role})
                self.assertIn(expected, self.findings_after("developer-routes.json", routes))

    def route_findings(self, **changes):
        routes = fixture_routes()
        entry = routes["routes"]["fixture"]
        for key, value in changes.items():
            if value is _DELETE:
                del entry[key]
            else:
                entry[key] = value
        return self.findings_after("developer-routes.json", routes)

    def test_route_timeouts_valid_values_pass(self):
        # #1815, #2281: 1 <= default_timeout <= max_timeout <= 18000.
        for default, maximum in ((1, 1), (600, 600), (600, 1800), (1800, 1800), (1, 1800),
                                 (1800, 3600), (3600, 3600), (1, 3600), (10800, 18000),
                                 (18000, 18000), (1, 18000)):
            with self.subTest(default=default, maximum=maximum):
                self.assertEqual(
                    self.route_findings(default_timeout=default, max_timeout=maximum), [])

    def test_route_timeouts_missing_fail(self):
        prefix = "tools/llmtool/developer-routes.json: RULE_SCHEMA $.routes.fixture"
        for key in ("default_timeout", "max_timeout"):
            with self.subTest(key=key):
                self.assertIn(f'{prefix} is missing key "{key}"',
                              self.route_findings(**{key: _DELETE}))
        findings = self.route_findings(default_timeout=_DELETE, max_timeout=_DELETE)
        self.assertIn(f'{prefix} is missing key "default_timeout"', findings)
        self.assertIn(f'{prefix} is missing key "max_timeout"', findings)

    def test_route_timeouts_non_int_fail(self):
        prefix = "tools/llmtool/developer-routes.json: RULE_SCHEMA $.routes.fixture"
        for key in ("default_timeout", "max_timeout"):
            for value in ("600", 600.0, 1.5, True, False, None, [600], {"s": 600}):
                with self.subTest(key=key, value=value):
                    self.assertIn(f"{prefix}.{key} must be an integer >= 1 and <= 18000",
                                  self.route_findings(**{key: value}))

    def test_route_timeouts_out_of_range_fail(self):
        prefix = "tools/llmtool/developer-routes.json: RULE_SCHEMA $.routes.fixture"
        cases = [
            ({"default_timeout": 0}, "default_timeout must be an integer >= 1 and <= 18000"),
            ({"default_timeout": -5}, "default_timeout must be an integer >= 1 and <= 18000"),
            ({"max_timeout": 0, "default_timeout": 0}, "max_timeout must be an integer >= 1 and <= 18000"),
            ({"max_timeout": 18001}, "max_timeout must be an integer >= 1 and <= 18000"),
            ({"default_timeout": 18001, "max_timeout": 18001},
             "default_timeout must be an integer >= 1 and <= 18000"),
            ({"default_timeout": 10 ** 9}, "default_timeout must be an integer >= 1 and <= 18000"),
            ({"default_timeout": 900, "max_timeout": 600}, "default_timeout must be <= max_timeout"),
            ({"default_timeout": 3600, "max_timeout": 1}, "default_timeout must be <= max_timeout"),
        ]
        for changes, expected in cases:
            with self.subTest(changes=changes):
                self.assertIn(f"{prefix}.{expected}", self.route_findings(**changes))

    def test_route_timeouts_keep_other_strict_checks(self):
        # The timeout keys add no leniency: unknown keys and bad models still fail.
        prefix = "tools/llmtool/developer-routes.json: RULE_SCHEMA $.routes.fixture"
        self.assertIn(f'{prefix} has unknown key "timeout"', self.route_findings(timeout=600))
        self.assertIn(f"{prefix}.allowed_models needs at least 1 item(s)",
                      self.route_findings(allowed_models=[]))
        findings = self.route_findings(default_timeout=900, max_timeout=600, extra=True)
        self.assertIn(f'{prefix} has unknown key "extra"', findings)
        self.assertIn(f"{prefix}.default_timeout must be <= max_timeout", findings)

    def test_invalid_route_timeouts_make_model_allowlist_unavailable(self):
        self.route_findings(max_timeout=18001)
        self.assertEqual(guidance_drift_check._configured_families(self.repo)[1],
                         ["tools/llmtool/developer-routes.json: MODEL_ALLOWLIST_UNAVAILABLE schema invalid"])

    def test_timeout_cap_is_18000(self):
        # This test does not import the llmtool package; the match with
        # llmtool.developer_routes.HARD_TIMEOUT_CAP_SECONDS is checked in
        # test_developer_route_timeout.py, which already imports it.
        self.assertEqual(guidance_drift_check.ROUTE_TIMEOUT_CAP_SECONDS, 18000)
        # #2281: the EC2 remote test timeout is not an AI run; its cap stays 3600.
        self.assertEqual(guidance_drift_check.REMOTE_TEST_TIMEOUT_CAP_SECONDS, 3600)

    def test_real_routes_declare_timeouts(self):
        repository = Path(__file__).resolve().parents[3]
        with (repository / "tools" / "llmtool" / "developer-routes.json").open(encoding="utf-8") as stream:
            routes = json.load(stream)["routes"]
        for name, entry in routes.items():
            with self.subTest(route=name):
                self.assertIs(type(entry["default_timeout"]), int)
                self.assertIs(type(entry["max_timeout"]), int)
                self.assertTrue(1 <= entry["default_timeout"] <= entry["max_timeout"] <= 18000)
                # #2281 (Owner 2026-11-19): 3-hour default, 5-hour maximum.
                self.assertEqual((entry["default_timeout"], entry["max_timeout"]), (10800, 18000))

    @staticmethod
    def fixture_profile(**changes):
        profile = {"adapter": "openai-chat-tools", "model": "gpt-6-luna",
                   "context_cap_tokens": 32768, "max_output_tokens": 16384}
        for key, value in changes.items():
            if value is _DELETE:
                profile.pop(key, None)
            else:
                profile[key] = value
        return profile

    def test_profile_timeout_seconds_is_optional_and_bounded_pass(self):
        # #1817, #2281: the gateway profile may carry an integer timeout_seconds, 1..18000.
        self.assertEqual(self.route_findings(profile=self.fixture_profile()), [])
        for value in (1, 120, 600, 1800, 3600, 10800, 17999, 18000):
            with self.subTest(value=value):
                self.assertEqual(
                    self.route_findings(profile=self.fixture_profile(timeout_seconds=value)), [])

    def test_profile_timeout_seconds_out_of_range_or_non_int_fail(self):
        prefix = "tools/llmtool/developer-routes.json: RULE_SCHEMA $.routes.fixture.profile"
        for value in (0, -1, 18001, 10 ** 9, "600", 600.0, 1.5, True, False, None, [600], {"s": 600}):
            with self.subTest(value=value):
                self.assertIn(f"{prefix}.timeout_seconds must be an integer >= 1 and <= 18000",
                              self.route_findings(profile=self.fixture_profile(timeout_seconds=value)))

    def test_profile_timeout_seconds_keeps_unknown_key_checks(self):
        prefix = "tools/llmtool/developer-routes.json: RULE_SCHEMA $.routes.fixture"
        self.assertIn(f'{prefix}.profile has unknown key "timeout"',
                      self.route_findings(profile=self.fixture_profile(timeout=600)))
        findings = self.route_findings(profile=self.fixture_profile(timeout_seconds=600, extra=True))
        self.assertIn(f'{prefix}.profile has unknown key "extra"', findings)
        # The key belongs to the profile only, not to the route entry.
        self.assertIn(f'{prefix} has unknown key "timeout_seconds"',
                      self.route_findings(timeout_seconds=600))

    def test_real_gw_agent_profile_timeout_is_10800_and_equals_route_default(self):
        # #1817 correction: one call is bounded only by the route's Task timeout.
        repository = Path(__file__).resolve().parents[3]
        with (repository / "tools" / "llmtool" / "developer-routes.json").open(encoding="utf-8") as stream:
            route = json.load(stream)["routes"]["gw-agent"]
        profile = route["profile"]
        self.assertIs(type(profile["timeout_seconds"]), int)
        self.assertEqual(profile["timeout_seconds"], 10800)
        self.assertEqual(profile["timeout_seconds"], route["default_timeout"])

    def test_gw_agent_budgets_schema_accepts_valid_direct_and_multiplier_objects(self):
        # #2281 L9: developer-routes schema accepts budgets object with direct or multiplier tokens.
        direct_budgets = {
            "max_turns": 512, "max_input_tokens": 262144, "max_output_tokens": 65536,
            "max_wall_seconds": 1800, "max_tool_result_bytes": 262144, "max_sent_bytes": 16777216,
        }
        multiplier_budgets = {
            "max_turns": 512, "max_input_tokens_multiplier": 8, "max_output_tokens_multiplier": 4,
            "max_wall_seconds": 1800, "max_tool_result_bytes": 262144, "max_sent_bytes": 16777216,
        }
        self.assertEqual(self.route_findings(budgets=direct_budgets), [])
        self.assertEqual(self.route_findings(budgets=multiplier_budgets), [])

    def test_real_gw_agent_wall_budget_is_10800_and_equals_route_default(self):
        # #2281: whole-run wall budget is no shorter than the route default timeout.
        repository = Path(__file__).resolve().parents[3]
        with (repository / "tools" / "llmtool" / "developer-routes.json").open(encoding="utf-8") as stream:
            route = json.load(stream)["routes"]["gw-agent"]
        budgets = route["budgets"]
        self.assertIs(type(budgets["max_wall_seconds"]), int)
        self.assertEqual(budgets["max_wall_seconds"], 10800)
        self.assertEqual(budgets["max_wall_seconds"], route["default_timeout"])
        self.assertLessEqual(budgets["max_wall_seconds"], route["max_timeout"])

    def test_gw_agent_budgets_schema_rejects_invalid_budgets(self):
        prefix = "tools/llmtool/developer-routes.json: RULE_SCHEMA $.routes.fixture.budgets"
        cases = [
            ({"max_turns": 0, "max_input_tokens": 262144, "max_output_tokens": 65536,
              "max_wall_seconds": 1800, "max_tool_result_bytes": 262144, "max_sent_bytes": 16777216},
             f"{prefix}.max_turns must be an integer >= 1 and <= 1000"),
            ({"max_turns": 1001, "max_input_tokens": 262144, "max_output_tokens": 65536,
              "max_wall_seconds": 1800, "max_tool_result_bytes": 262144, "max_sent_bytes": 16777216},
             f"{prefix}.max_turns must be an integer >= 1 and <= 1000"),
            ({"max_turns": 512, "max_input_tokens": 50_000_001, "max_output_tokens": 65536,
              "max_wall_seconds": 1800, "max_tool_result_bytes": 262144, "max_sent_bytes": 16777216},
             f"{prefix}.max_input_tokens must be an integer >= 1 and <= 50000000"),
            ({"max_turns": 512, "max_input_tokens_multiplier": 0, "max_output_tokens": 65536,
              "max_wall_seconds": 1800, "max_tool_result_bytes": 262144, "max_sent_bytes": 16777216},
              f"{prefix}.max_input_tokens_multiplier must be an integer >= 1"),
            ({"max_turns": 512, "max_output_tokens": 5_000_001, "max_input_tokens": 262144,
              "max_wall_seconds": 1800, "max_tool_result_bytes": 262144, "max_sent_bytes": 16777216},
             f"{prefix}.max_output_tokens must be an integer >= 1 and <= 5000000"),
            ({"max_turns": 512, "max_output_tokens_multiplier": 0, "max_input_tokens": 262144,
              "max_wall_seconds": 1800, "max_tool_result_bytes": 262144, "max_sent_bytes": 16777216},
              f"{prefix}.max_output_tokens_multiplier must be an integer >= 1"),
        ]
        for bad_budgets, expected in cases:
            with self.subTest(budgets=bad_budgets):
                findings = self.route_findings(budgets=bad_budgets)
                self.assertIn(expected, findings)

    def test_unreadable_duplicate_key_and_non_finite_rule_files_fail(self):
        cases = {
            "bad json": "{",
            "duplicate key": '{"schema_version":1,"schema_version":1}',
            "non-finite": '{"schema_version": NaN}',
        }
        for label, raw in cases.items():
            with self.subTest(case=label):
                findings = self.findings_after("sensitive-paths.json", raw)
                self.assertTrue(any("tools/llmtool/sensitive-paths.json: RULE_INVALID" in item
                                    for item in findings), findings)
        (self.rules / "sensitive-paths.json").write_bytes(b"\xff")
        self.assertIn("tools/llmtool/sensitive-paths.json: RULE_INVALID UnicodeDecodeError",
                      guidance_drift_check.check_rule_files(self.repo))

    def test_unregistered_rule_file_fails_and_dotfiles_are_ignored(self):
        (self.rules / "new-rules.json").write_text("{}", encoding="utf-8")
        (self.rules / ".local-report.json").write_text("not json", encoding="utf-8")
        self.assertEqual(
            guidance_drift_check.check_rule_files(self.repo),
            ["tools/llmtool/new-rules.json: RULE_SCHEMA_MISSING no strict schema registered"],
        )

    def test_check_reports_rule_file_findings(self):
        (self.repo / "AGENTS.md").write_text("Guidance\n", encoding="utf-8")
        (self.repo / "CLAUDE.md").write_text("Guidance\n", encoding="utf-8")
        digest = hashlib.sha256(b"Guidance\n").hexdigest()
        (self.rules / "guidance-drift.json").write_text(json.dumps({
            "schema_version": 1, "hashes": {"AGENTS.md": digest, "CLAUDE.md": digest}, "codex_only": [],
        }), encoding="utf-8")
        self.assertEqual(check(self.repo), [])
        self.findings_after("execution-resources.json", {**self.valid["execution-resources.json"], "x": 1})
        self.assertIn('tools/llmtool/execution-resources.json: RULE_SCHEMA $ has unknown key "x"',
                      check(self.repo))

    def test_real_registered_rule_files_match_their_schemas(self):
        repository = Path(__file__).resolve().parents[3]
        findings = guidance_drift_check.check_rule_files(repository)
        registered = [
            item for item in findings
            if any(item.startswith(f"tools/llmtool/{name}:") for name in guidance_drift_check.RULE_SCHEMAS)
        ]
        self.assertEqual(registered, [])
        for name in guidance_drift_check.RULE_SCHEMAS:
            with self.subTest(name=name):
                self.assertTrue((repository / "tools" / "llmtool" / name).is_file())


class ModelNameTests(unittest.TestCase):
    """#1502 WP-G, #2302: normative docs name only models of a configured family."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        rules = self.repo / "tools" / "llmtool"
        rules.mkdir(parents=True)
        (rules / "developer-routes.json").write_text(
            json.dumps(fixture_routes(("gpt-6-luna", "kimi-k2"))), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def write_doc(self, relative, text):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_wrong_model_in_each_normative_doc_fails(self):
        for relative in ("AGENTS.md", "CLAUDE.md", ".claude/skills/demo/SKILL.md",
                         "docs/governance/rule.md", "tools/llmtool/WORKFLOW.md"):
            with self.subTest(doc=relative):
                self.write_doc(relative, "Use `llama-9-wrong` for this route.\n")
                self.assertIn(f"{relative}: MODEL_NOT_ALLOWED llama-9-wrong",
                              guidance_drift_check.check_model_names(self.repo))
                (self.repo / relative).unlink()

    def test_allowed_models_pass_including_case_and_provider_prefix(self):
        self.write_doc("CLAUDE.md", "Use `gpt-6-luna`, GPT-6-LUNA or openrouter/kimi-k2.\n")
        self.assertEqual(guidance_drift_check.check_model_names(self.repo), [])

    def test_same_family_model_other_than_route_default_passes(self):
        # #2302: a doc may name another model of a configured family, so a
        # same-family switch needs no rule-file change.
        self.write_doc("CLAUDE.md", (
            "Use `gpt-9-next`, GPT-7-X, openrouter/gpt-8-y, gpt5, o4-mini, "
            "kimi-k3 or openrouter/kimi-k9.\n"))
        self.assertEqual(guidance_drift_check.check_model_names(self.repo), [])

    def test_same_family_passes_without_allowed_models(self):
        # #2302: families come from default_model when allowed_models is absent.
        (self.repo / "tools" / "llmtool" / "developer-routes.json").write_text(json.dumps(fixture_routes(
            ("gemini-4-hydrogen-medium",), allowed=False,
            roles={"claude-reviewer": {"default_model": "claude-sonnet-5-5"},
                   "codex-reviewer": {"default_model": "gpt-6.1-sol", "allowed_models": ["gpt-6.1-sol"]}})),
            encoding="utf-8")
        self.write_doc("CLAUDE.md", (
            "AGY `gemini-3.9-pro`; review claude-opus-5-5 or anthropic/claude-haiku-5-5; "
            "debug gpt-7-x; not qwen3-coder.\n"))
        self.assertEqual(guidance_drift_check.check_model_names(self.repo),
                         ["CLAUDE.md: MODEL_NOT_ALLOWED qwen3-coder"])

    def test_model_family(self):
        family = guidance_drift_check._model_family
        cases = {
            "gpt-6-luna": "openai", "GPT5": "openai", "o4-mini": "openai",
            "openrouter/gpt-8-y": "openai", "claude-opus-5-5": "claude",
            "anthropic/claude-sonnet-5-5": "claude", "gemini-3.8-flash-medium": "gemini",
            "opencode/deepseek-v4.1-flash-free": "deepseek", "qwen3-coder": "qwen",
            "kimi-k2": "kimi", "glm-5": "glm", "minimax-m2": "minimax", "llama-3": "llama",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(family(name), expected)
        for name in ("gw", "claude2", "claude-reviewer", "gw-agent", "fixture", "gpt-6-luna.md"):
            with self.subTest(name=name):
                self.assertIsNone(family(name))

    def test_role_registry_models_are_allowed(self):
        # #1502 WP-E: review and AI Debug role models count as allowed families.
        (self.repo / "tools" / "llmtool" / "developer-routes.json").write_text(json.dumps(fixture_routes(
            ("gpt-6-luna",),
            roles={"codex-debug": {"allowed_models": ["gpt-7-x"]},
                   "claude-reviewer": {"allowed_models": ["claude-opus-5-5"]}})), encoding="utf-8")
        self.write_doc("AGENTS.md", "Debug `gpt-7-x`, review claude-opus-5-5, route gpt-6-luna, not llama-9-wrong.\n")
        self.assertEqual(guidance_drift_check.check_model_names(self.repo),
                         ["AGENTS.md: MODEL_NOT_ALLOWED llama-9-wrong"])

    def test_invalid_role_registry_fails_closed(self):
        (self.repo / "tools" / "llmtool" / "developer-routes.json").write_text(json.dumps(fixture_routes(
            ("gpt-6-luna",), roles={"codex-debug": {"allowed_models": []}})), encoding="utf-8")
        self.write_doc("CLAUDE.md", "gpt-6-luna\n")
        self.assertEqual(guidance_drift_check.check_model_names(self.repo),
                         ["tools/llmtool/developer-routes.json: MODEL_ALLOWLIST_UNAVAILABLE schema invalid"])

    def test_history_reports_and_non_normative_files_are_not_checked(self):
        for relative in ("docs/history/old.md", "docs/reports/run.md",
                         "tools/llmtool/llmtool/notes.md", "README.txt"):
            self.write_doc(relative, "Old model llama-9-wrong.\n")
        self.assertEqual(guidance_drift_check.check_model_names(self.repo), [])

    def test_worker_ids_state_values_and_counts_are_not_blocked(self):
        self.write_doc("CLAUDE.md", (
            "`claude-developer`, `claude-reviewer`, `codex-reviewer`, `codex-debug`, "
            "`gw-agent`, `claude2`, `agy` and `opencode` are worker IDs or routes.\n"
            "Dispatch `PAUSED`, `ALLOWED`, `OWNER_ASSIGNMENT_ONLY`; two repairs, "
            "at most 3 child Tasks, 30+ minutes.\n"
        ))
        self.assertEqual(guidance_drift_check.check_model_names(self.repo), [])

    def test_each_wrong_model_is_reported_once_per_file(self):
        self.write_doc("CLAUDE.md", "llama-9-wrong and llama-9-wrong and qwen3-coder\n")
        self.assertEqual(guidance_drift_check.check_model_names(self.repo), [
            "CLAUDE.md: MODEL_NOT_ALLOWED llama-9-wrong",
            "CLAUDE.md: MODEL_NOT_ALLOWED qwen3-coder",
        ])

    def test_missing_or_invalid_allowlist_fails_closed(self):
        self.write_doc("CLAUDE.md", "gpt-6-luna\n")
        routes = self.repo / "tools" / "llmtool" / "developer-routes.json"
        routes.write_text(json.dumps({"schema_version": 1, "routes": {"fixture": {"default_model": "x"}}}),
                          encoding="utf-8")
        self.assertEqual(guidance_drift_check.check_model_names(self.repo),
                         ["tools/llmtool/developer-routes.json: MODEL_ALLOWLIST_UNAVAILABLE schema invalid"])
        routes.unlink()
        self.assertEqual(guidance_drift_check.check_model_names(self.repo),
                         ["tools/llmtool/developer-routes.json: MODEL_ALLOWLIST_UNAVAILABLE FileNotFoundError"])

    def test_check_reports_model_findings(self):
        text = "Model `llama-9-wrong`.\n"
        self.write_doc("AGENTS.md", text)
        self.write_doc("CLAUDE.md", text)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        self.write_doc("tools/llmtool/guidance-drift.json", json.dumps({
            "schema_version": 1, "hashes": {"AGENTS.md": digest, "CLAUDE.md": digest}, "codex_only": [],
        }))
        self.assertEqual(check(self.repo), [
            "AGENTS.md: MODEL_NOT_ALLOWED llama-9-wrong",
            "CLAUDE.md: MODEL_NOT_ALLOWED llama-9-wrong",
        ])


if __name__ == "__main__":
    unittest.main()
