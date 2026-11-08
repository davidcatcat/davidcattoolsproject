import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

import yaml

from llmtool.config import (
    DEFAULTS, api_key, codex_direct_timeout, init_config, legacy_repository_config,
    pm_claude_config_dir, repository_config,
    resolve_config, show_config, user_config_path,
)
from llmtool.errors import LLMToolError
from llmtool.cli import main


class GuardedEnvironment(dict):
    """Fail if low-trust config causes a secret environment lookup."""

    def __getitem__(self, name):
        if name == "AWS_SECRET_ACCESS_KEY":
            raise AssertionError("secret environment variable was accessed")
        return super().__getitem__(name)

    def get(self, name, default=None):
        if name == "AWS_SECRET_ACCESS_KEY":
            raise AssertionError("secret environment variable was accessed")
        return super().get(name, default)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.cwd = Path(self.temp.name) / "project"
        self.cwd.mkdir()
        self.user = user_config_path(self.home)
        self.user.parent.mkdir(parents=True)
        self.project = self.cwd / ".llmtool.yaml"

    def resolve(self, cli=None, env=None, require_configured=False):
        return resolve_config(cli, {} if env is None else env, self.cwd, self.home,
                              require_configured=require_configured)

    def test_defaults_and_independent_instances(self):
        config = self.resolve()
        self.assertEqual(config, DEFAULTS)
        self.assertEqual(codex_direct_timeout(self.cwd, self.home), 600)
        self.assertIsNone(config["providers"]["default"]["base_url"])
        self.assertIsNone(config["providers"]["default"]["model"])
        self.assertEqual(config["providers"]["default"]["provider_options"], {})
        config["generation"]["max_tokens"] = 1
        self.assertEqual(self.resolve()["generation"]["max_tokens"], 2000)

    def test_user_config(self):
        self.user.write_text("generation:\n  max_tokens: 321\n", encoding="utf-8")
        self.assertEqual(self.resolve()["generation"]["max_tokens"], 321)

    def test_repository_identity_comes_from_trust_and_legacy_values_are_isolated(self):
        trusted = type("Trust", (), {"repository": {
            "slug": "example-org/repo.name",
            "approval_refs": {"worker_dispatch_issue": 12,
                              "worker_dispatch_evidence_pr": 34}}})()
        with patch("llmtool.trust.load_trust", return_value=trusted):
            self.assertEqual(repository_config(self.home), {
                "slug": "example-org/repo.name",
                "dispatch_issue": 12,
                "dispatch_evidence_pr": 34,
            })
        self.user.write_text("repository:\n  slug: example-org/repo.name\n"
                             "  dispatch_issue: 12\n  dispatch_evidence_pr: 34\n",
                             encoding="utf-8")
        with self.assertRaisesRegex(LLMToolError, "^TRUST_LEGACY_REPOSITORY_CONFIG$") as caught:
            repository_config(self.home)
        self.assertEqual(str(caught.exception), "TRUST_LEGACY_REPOSITORY_CONFIG")
        self.assertEqual(legacy_repository_config(self.home), {
            "slug": "example-org/repo.name",
            "dispatch_issue": 12,
            "dispatch_evidence_pr": 34,
        })
        self.project.write_text("repository: {slug: project.example/repo, dispatch_issue: 99}\n",
                                encoding="utf-8")
        with self.assertRaisesRegex(LLMToolError, "project config: forbidden or unsupported field"):
            self.resolve()

    def test_load_config_without_trust_and_repository_key_succeeds(self):
        self.assertEqual(self.resolve(), DEFAULTS)

    def test_repository_config_sanitizes_trust_errors(self):
        from llmtool.trust import TrustError
        with patch("llmtool.trust.load_trust", side_effect=TrustError("TRUST_PATH_INVALID")):
            with self.assertRaisesRegex(LLMToolError, "^TRUST_PATH_INVALID$") as caught:
                repository_config(self.home)
        self.assertEqual(str(caught.exception), "TRUST_PATH_INVALID")
        self.assertNotIn(str(self.home), str(caught.exception))

    def test_legacy_repository_identity_without_user_config_does_not_load_yaml(self):
        with patch("llmtool.config.yaml_module", side_effect=LLMToolError("yaml unavailable")):
            self.assertEqual(legacy_repository_config(self.home), {
                "slug": "davidcatcat/davidcattoolsproject",
                "dispatch_issue": 289,
                "dispatch_evidence_pr": 283,
            })

        self.user.write_text("repository: {}\n", encoding="utf-8")
        with patch("llmtool.config.yaml_module", side_effect=LLMToolError("yaml unavailable")):
            with self.assertRaisesRegex(LLMToolError, "yaml unavailable"):
                legacy_repository_config(self.home)

    def test_invalid_repository_identity_is_rejected_with_fixed_message(self):
        for repository in (
            {"slug": "../bad"}, {"slug": "owner.name/repo"},
            {"slug": "owner/repo?x"}, {"slug": "owner/.."},
            {"slug": "owner/repo", "dispatch_issue": True},
            {"slug": "owner/repo", "dispatch_issue": 0},
            {"slug": "owner/repo", "dispatch_issue": 10**9 + 1},
            {"slug": "owner/repo", "dispatch_evidence_pr": False},
            {"slug": "owner/repo", "dispatch_evidence_pr": 0},
            {"unknown": "ignored"},
        ):
            with self.subTest(repository=repository):
                self.user.write_text(yaml.safe_dump({"repository": repository}), encoding="utf-8")
                with self.assertRaisesRegex(LLMToolError, "^TRUST_LEGACY_REPOSITORY_CONFIG$"):
                    repository_config(self.home)
                with self.assertRaisesRegex(LLMToolError, "^user repository: invalid configuration$"):
                    legacy_repository_config(self.home)
                with self.assertRaisesRegex(LLMToolError, "^user repository: invalid configuration$"):
                    self.resolve()

    def test_codex_direct_timeout_configuration(self):
        self.user.write_text("codex_direct:\n  timeout_seconds: 900\n", encoding="utf-8")
        self.assertEqual(self.resolve(), DEFAULTS)
        self.assertEqual(codex_direct_timeout(self.cwd, self.home), 900)
        for value in (1801, 0, -1, 1.5, "900", True):
            with self.subTest(value=value):
                self.user.write_text(yaml.safe_dump({"codex_direct": {"timeout_seconds": value}}))
                with self.assertRaises(LLMToolError):
                    codex_direct_timeout(self.cwd, self.home)

    def test_pm_claude_config_dir_is_trusted_user_config_only(self):
        self.assertIsNone(pm_claude_config_dir(self.home))
        self.user.write_text("pm_runtime:\n  claude_config_dir: /Users/x/.claude-account2\n",
                             encoding="utf-8")
        self.assertEqual(self.resolve(), DEFAULTS)
        self.assertEqual(pm_claude_config_dir(self.home), "/Users/x/.claude-account2")
        for value in ("relative/dir", "", 5, None, "/a\nb", "/" + "a" * 1024):
            with self.subTest(value=value):
                self.user.write_text(yaml.safe_dump({"pm_runtime": {"claude_config_dir": value}}))
                with self.assertRaises(LLMToolError):
                    pm_claude_config_dir(self.home)
                with self.assertRaises(LLMToolError):
                    self.resolve()
        self.user.unlink()
        self.project.write_text("pm_runtime:\n  claude_config_dir: /tmp/attacker\n", encoding="utf-8")
        with self.assertRaisesRegex(LLMToolError, "^project config: forbidden or unsupported field$"):
            self.resolve()
        self.assertIsNone(pm_claude_config_dir(self.home))

    def test_codex_direct_timeout_ignores_project_and_provider_selection(self):
        self.user.write_text("codex_direct:\n  timeout_seconds: 900\n", encoding="utf-8")
        self.project.write_text("[broken project config", encoding="utf-8")
        with patch.dict(os.environ, {"LLMTOOL_PROVIDER": "unavailable-profile"}):
            self.assertEqual(codex_direct_timeout(self.cwd, self.home), 900)

    def test_openai_provider_config_uses_fixed_endpoint_and_explicit_auth(self):
        self.user.write_text("""providers:
  openai-main:
    type: openai
    model: test-model
    api_key_env: OPENAI_API_KEY
active_provider: openai-main
""")
        config = self.resolve(require_configured=True)
        provider = config["providers"]["openai-main"]
        self.assertIsNone(provider["base_url"])
        self.assertEqual(provider["type"], "openai")
        self.assertEqual(api_key(config, {"OPENAI_API_KEY": "sk-test-example"}), "sk-test-example")

    def test_openai_rejects_user_cli_and_environment_endpoint_overrides(self):
        base = {"providers": {"default": {
            "type": "openai", "model": "test-model", "api_key": "sk-test-example",
        }}}
        user_endpoint = {"providers": {"default": {
            "type": "openai", "model": "test-model", "api_key": "sk-test-example",
            "base_url": "https://alt.example",
        }}}
        cases = (
            ("user", {}, {}, user_endpoint),
            ("cli", {"base_url": "https://alt.example"}, {}, base),
            ("environment", {}, {"LLMTOOL_BASE_URL": "https://alt.example"}, base),
        )
        for source, cli, env, fragment in cases:
            with self.subTest(source=source):
                self.user.write_text(yaml.safe_dump(fragment))
                with self.assertRaises(LLMToolError):
                    self.resolve(cli=cli, env=env)

    def test_project_partial_override_and_user_inheritance(self):
        self.user.write_text("providers:\n  default:\n    model: user-route\n    timeout_seconds: 30\n")
        self.project.write_text("providers:\n  default:\n    model: project-route\n")
        provider = self.resolve()["providers"]["default"]
        self.assertEqual(provider["model"], "project-route")
        self.assertEqual(provider["timeout_seconds"], 30)
        self.assertEqual(provider["base_url"], DEFAULTS["providers"]["default"]["base_url"])

    def test_all_environment_overrides(self):
        config = self.resolve(env={
            "LLMTOOL_BASE_URL": "http://localhost:1234/", "LLMTOOL_MODEL": "env-route",
            "LLMTOOL_TIMEOUT": "2.5", "LLMTOOL_MAX_TOKENS": "17",
        })
        provider = config["providers"]["default"]
        self.assertEqual(provider["base_url"], "http://localhost:1234")
        self.assertEqual(provider["model"], "env-route")
        self.assertEqual(provider["timeout_seconds"], 2.5)
        self.assertEqual(config["generation"]["max_tokens"], 17)

    def test_precedence(self):
        self.user.write_text("generation: {max_tokens: 10}")
        self.project.write_text("generation: {max_tokens: 20}")
        env = {"LLMTOOL_MAX_TOKENS": "30"}
        self.assertEqual(self.resolve()["generation"]["max_tokens"], 20)
        self.assertEqual(self.resolve(env=env)["generation"]["max_tokens"], 30)
        self.assertEqual(self.resolve({"max_tokens": "40"}, env)["generation"]["max_tokens"], 40)

    def test_invalid_yaml_and_schema_do_not_echo_values(self):
        for raw in (
            "generation: [sensitive-marker", "- sensitive-marker", "sensitive-marker: true",
            "generation: sensitive-marker", "generation: {}\ngeneration: {}",
            "generation: &a {max_tokens: 1}\nproviders: *a",
            "generation: !!python/object/apply:os.system [sensitive-marker]",
            "? [sensitive-marker]\n: value",
        ):
            with self.subTest(raw=raw):
                self.project.write_text(raw)
                with self.assertRaises(LLMToolError) as caught:
                    self.resolve()
                self.assertNotIn("sensitive-marker", str(caught.exception))

    def test_invalid_config_types_and_values(self):
        for fragment in (
            {"generation": {"max_tokens": True}}, {"generation": {"max_tokens": 1.5}},
            {"generation": {"max_tokens": 0}}, {"generation": {"temperature": float("nan")}},
            {"generation": {"temperature": 3}}, {"generation": {"enable_thinking": True}},
            {"generation": {"enable_thinking": "false"}},
            {"providers": {"default": {"timeout_seconds": -1}}},
            {"providers": {"default": {"model": []}}},
            {"providers": {"default": {"type": "other"}}},
        ):
            with self.subTest(fragment=fragment):
                self.project.write_text(yaml.safe_dump(fragment))
                with self.assertRaises(LLMToolError):
                    self.resolve()

    def test_invalid_inactive_provider_name_is_rejected_without_losing_valid_profiles(self):
        for name in ("bad name", "-leading", "x" * 65):
            with self.subTest(name=name):
                self.user.write_text(yaml.safe_dump({"providers": {"default": {}, name: {}}}))
                with self.assertRaises(LLMToolError):
                    self.resolve()
        self.user.write_text("providers:\n  default: {}\n  office-qwen: {}\n")
        config = self.resolve()
        self.assertIn("default", config["providers"])
        self.assertIn("office-qwen", config["providers"])

    def test_api_key_whitespace_rejected_during_config_and_runtime(self):
        self.user.write_text("providers:\n  default:\n    api_key: valid-printable-key\n")
        config = self.resolve()
        self.assertEqual(api_key(config, {}), "valid-printable-key")
        for key in (" leading", "trailing ", "in ternal", "tab\tkey", "", "control\x01"):
            with self.subTest(key=repr(key)):
                self.user.write_text(yaml.safe_dump({"providers": {"default": {"api_key": key}}}))
                with self.assertRaises(LLMToolError):
                    self.resolve()
        self.user.write_text("providers:\n  default: {}\n")
        config = self.resolve()
        config["providers"]["default"]["api_key"] = "runtime bad "
        with self.assertRaisesRegex(LLMToolError, "API key"):
            api_key(config, {})

    def test_timeout_seconds_finite_upper_bound(self):
        from llmtool.config import MAX_TIMEOUT_SECONDS
        for value in (0, -1, float("nan"), float("inf"), 1e10, MAX_TIMEOUT_SECONDS + 1):
            with self.subTest(value=value):
                self.project.write_text(yaml.safe_dump({"providers": {"default": {"timeout_seconds": value}}}))
                with self.assertRaises(LLMToolError):
                    self.resolve()
        for value in (120, MAX_TIMEOUT_SECONDS):
            self.project.write_text(yaml.safe_dump({"providers": {"default": {"timeout_seconds": value}}}))
            self.assertEqual(self.resolve()["providers"]["default"]["timeout_seconds"], value)

    def test_invalid_environment_numbers(self):
        for name in ("LLMTOOL_TIMEOUT", "LLMTOOL_MAX_TOKENS"):
            for value in ("", "secret-value", "nan", "inf", "0", "-1"):
                with self.subTest(name=name, value=value):
                    with self.assertRaises(LLMToolError) as caught:
                        self.resolve(env={name: value})
                    self.assertNotIn("secret-value", str(caught.exception))

    def test_url_validation(self):
        for url in ("file:///etc/passwd", "http://u:secret@localhost", "http://localhost/v1",
                    "http://localhost?key=secret", "http://localhost/#secret",
                    "http://localhost:bad", "http://localhost:0", "http://localhost\n"):
            with self.subTest(url=url), self.assertRaises(LLMToolError):
                self.resolve(env={"LLMTOOL_BASE_URL": url})

    def test_api_key_optional_and_masked(self):
        self.assertIsNone(api_key(self.resolve(), {}))
        self.user.write_text("providers:\n  default:\n    api_key: test-secret-value\n")
        config = self.resolve()
        self.assertEqual(api_key(config, {}), "test-secret-value")
        rendered = show_config(config)
        self.assertNotIn("test-secret-value", rendered)
        self.assertIn("REDACTED", rendered)
        self.assertEqual(config["providers"]["default"]["api_key"], "test-secret-value")

    def test_api_key_environment_is_explicit(self):
        self.user.write_text("providers:\n  default:\n    api_key_env: TEST_KEY\n")
        config = self.resolve()
        self.assertIn("REDACTED", show_config(config))
        self.assertEqual(api_key(config, {"TEST_KEY": "test-key"}), "test-key")
        for env in ({}, {"TEST_KEY": ""}, {"TEST_KEY": "secret\ninjected"}):
            with self.subTest(env=env), self.assertRaises(LLMToolError):
                api_key(config, env)

    def test_reject_ambiguous_auth(self):
        self.user.write_text("providers:\n  default:\n    api_key: test\n    api_key_env: TEST_KEY\n")
        with self.assertRaises(LLMToolError):
            self.resolve()

    def test_init_permissions_no_overwrite_and_force(self):
        init_config(self.user)
        self.assertEqual(self.resolve(), DEFAULTS)
        self.assertEqual(yaml.safe_load(self.user.read_text()), DEFAULTS)
        self.assertNotIn("10.0.0.10", self.user.read_text())
        self.assertNotIn("gw", self.user.read_text())
        self.assertEqual(self.user.stat().st_mode & 0o777, 0o600)
        self.user.write_text("generation: {max_tokens: 42}")
        with self.assertRaises(LLMToolError):
            init_config(self.user)
        self.assertIn("42", self.user.read_text())
        init_config(self.user, force=True)
        self.assertEqual(self.resolve(), DEFAULTS)

    def test_init_refuses_symlinks_even_with_force(self):
        target = self.cwd / "keep.txt"
        target.write_text("preserve")
        self.user.symlink_to(target)
        for force in (False, True):
            with self.subTest(force=force), self.assertRaises(LLMToolError):
                init_config(self.user, force)
        self.assertEqual(target.read_text(), "preserve")

    def test_config_size_and_encoding(self):
        for raw in (b"#" * (64 * 1024 + 1), b"\xff"):
            self.project.write_bytes(raw)
            with self.assertRaises(LLMToolError):
                self.resolve()

    def test_config_fifo_is_rejected_without_opening(self):
        os.mkfifo(self.project)
        with self.assertRaisesRegex(LLMToolError, "regular config file"):
            self.resolve()

    def test_project_lookup_is_current_directory_only(self):
        self.project.write_text("generation: {max_tokens: 9}")
        child = self.cwd / "nested"
        child.mkdir()
        config = resolve_config({}, {}, child, self.home)
        self.assertEqual(config["generation"]["max_tokens"], 2000)

    def test_example_matches_defaults(self):
        example = Path(__file__).resolve().parents[1] / "config.example.yaml"
        self.user.write_bytes(example.read_bytes())
        self.assertEqual(self.resolve(), DEFAULTS)

    def test_project_forbids_endpoint_auth_type_and_options_even_if_unchanged(self):
        for field, value in (
            ("base_url", "https://evil.example/private-marker"),
            ("api_key", "private-marker"), ("api_key_env", "AWS_SECRET_ACCESS_KEY"),
            ("type", "openai-compatible"), ("provider_options", {}),
            ("api_key", None),
        ):
            with self.subTest(field=field, value=value):
                self.project.write_text(yaml.safe_dump({"providers": {"default": {field: value}}}))
                with self.assertRaisesRegex(LLMToolError, "project provider") as caught:
                    self.resolve()
                self.assertNotIn("private-marker", str(caught.exception))
                self.assertNotIn("AWS_SECRET_ACCESS_KEY", str(caught.exception))
                self.assertNotIn("evil.example", str(caught.exception))

    def test_project_cannot_select_or_define_provider(self):
        for fragment in (
            {"active_provider": "default"},
            {"providers": {"new-untrusted-name": {"model": "test"}}},
            {"providers": None}, {"providers": {"default": None}},
            {"generation": {"provider_options": {"enable_thinking": False}}},
        ):
            with self.subTest(fragment=fragment):
                self.project.write_text(yaml.safe_dump(fragment))
                with self.assertRaisesRegex(LLMToolError, "project"):
                    self.resolve()

    def test_project_rejected_before_env_secret_lookup_or_http(self):
        env = GuardedEnvironment({"AWS_SECRET_ACCESS_KEY": "fake-secret"})
        self.project.write_text("providers:\n  default:\n    api_key_env: AWS_SECRET_ACCESS_KEY\n")
        with self.assertRaisesRegex(LLMToolError, "project provider"):
            self.resolve(env=env)
        # Verify CLI ordering as well: no auth resolution, client, or request.
        stdout, stderr = StringIO(), StringIO()
        with patch("llmtool.cli.resolve_config", side_effect=lambda *_, **__: self.resolve(env=env)), \
                patch("llmtool.cli.api_key") as credentials, \
                patch("llmtool.cli.create_client") as factory, \
                redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(main(["ask", "evidence"]), 1)
        credentials.assert_not_called()
        factory.assert_not_called()
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", stderr.getvalue())
        self.assertNotIn("fake-secret", stderr.getvalue())

    def test_user_key_cannot_be_redirected_even_by_overridden_project_field(self):
        self.user.write_text("providers:\n  default:\n    api_key: FAKE_TEST_SECRET\n")
        self.project.write_text("providers:\n  default:\n    base_url: https://evil.example\n")
        for cli, env in (({}, {}), ({"base_url": "http://localhost:1234"}, {}),
                         ({}, {"LLMTOOL_BASE_URL": "http://localhost:1234"})):
            with self.subTest(cli=cli, env=env), self.assertRaisesRegex(LLMToolError, "project provider"):
                self.resolve(cli=cli, env=env)

    def test_allowed_project_overrides_preserve_endpoint_and_auth(self):
        self.user.write_text("providers:\n  default:\n    api_key: FAKE_TEST_SECRET\n")
        self.project.write_text("""providers:
  default:
    model: project-model
    timeout_seconds: 42
generation:
  max_tokens: 37
  temperature: 0.3
""")
        config = self.resolve()
        profile = config["providers"]["default"]
        self.assertEqual(profile["model"], "project-model")
        self.assertEqual(profile["timeout_seconds"], 42)
        self.assertEqual(profile["base_url"], DEFAULTS["providers"]["default"]["base_url"])
        self.assertEqual(profile["api_key"], "FAKE_TEST_SECRET")
        self.assertEqual(config["generation"], {"max_tokens": 37, "temperature": 0.3})

    def configure_profiles(self):
        self.user.write_text("""providers:
  default:
    api_key: fake-default-key
  local:
    type: openai-compatible
    base_url: http://localhost:1234
    model: local-model
    api_key_env: LOCAL_TEST_KEY
active_provider: local
""")

    def test_named_providers_selection_and_isolated_defaults(self):
        self.configure_profiles()
        config = self.resolve()
        self.assertEqual(config["active_provider"], "local")
        profile = config["providers"]["local"]
        self.assertEqual(profile["base_url"], "http://localhost:1234")
        self.assertEqual(profile["timeout_seconds"], 120)
        self.assertEqual(profile["provider_options"], {})
        self.assertIsNone(profile["api_key"])
        self.assertEqual(api_key(config, {"LOCAL_TEST_KEY": "fake-local-key"}), "fake-local-key")

    def test_selection_precedence_and_overrides_target_selected_profile(self):
        self.configure_profiles()
        self.assertEqual(self.resolve(env={"LLMTOOL_PROVIDER": "default"})["active_provider"], "default")
        config = self.resolve(
            {"provider": "local", "model": "cli-model"},
            {"LLMTOOL_PROVIDER": "default", "LLMTOOL_TIMEOUT": "27", "LLMTOOL_BASE_URL": "http://localhost:4321"},
        )
        self.assertEqual(config["active_provider"], "local")
        self.assertEqual(config["providers"]["local"]["model"], "cli-model")
        self.assertEqual(config["providers"]["local"]["timeout_seconds"], 27)
        self.assertEqual(config["providers"]["local"]["base_url"], "http://localhost:4321")
        self.assertEqual(config["providers"]["default"], {**DEFAULTS["providers"]["default"], "api_key": "fake-default-key"})

    def test_project_cannot_switch_to_existing_trusted_profile(self):
        self.configure_profiles()
        self.project.write_text("active_provider: default\n")
        with self.assertRaisesRegex(LLMToolError, "project config"):
            self.resolve()

    def test_project_cannot_redirect_inactive_profile(self):
        self.configure_profiles()
        self.project.write_text("providers:\n  default:\n    base_url: https://evil.example\n")
        with self.assertRaisesRegex(LLMToolError, "project provider"):
            self.resolve()

    def test_inactive_profile_secret_environment_is_never_read(self):
        self.user.write_text("""providers:
  inactive:
    base_url: http://localhost:1234
    model: local-model
    api_key_env: AWS_SECRET_ACCESS_KEY
""")
        env = GuardedEnvironment({"AWS_SECRET_ACCESS_KEY": "FAKE_UNUSED_SECRET"})
        config = self.resolve(env=env)
        self.assertIsNone(api_key(config, env))
        self.assertNotIn("FAKE_UNUSED_SECRET", show_config(config))

    def test_config_show_redacts_all_profiles_without_resolving_environment(self):
        self.configure_profiles()
        rendered = show_config(self.resolve())
        self.assertNotIn("fake-default-key", rendered)
        self.assertEqual(rendered.count("***REDACTED***"), 2)

    def test_invalid_provider_selection_or_definition(self):
        for fragment in (
            {"active_provider": "missing"}, {"active_provider": []},
            {"providers": {"../name": {}}}, {"providers": []},
            {"providers": {"default": {"type": "anthropic"}}},
            {"providers": {"default": {"provider_options": {"enable_thinking": "false"}}}},
            {"generation": {"enable_thinking": False}},
        ):
            with self.subTest(fragment=fragment):
                self.user.write_text(yaml.safe_dump(fragment))
                with self.assertRaises(LLMToolError):
                    self.resolve()

    def test_provider_options_can_be_disabled_or_omitted(self):
        for options in ({}, {"enable_thinking": None}):
            with self.subTest(options=options):
                self.user.write_text(yaml.safe_dump({"providers": {"default": {"provider_options": options}}}))
                self.assertEqual(self.resolve()["providers"]["default"]["provider_options"], options)

    def test_steps_option_is_allowed_only_for_opencode_developer_profile(self):
        options = {"steps": 24}
        self.user.write_text(yaml.safe_dump({"providers": {
            "opencode-developer": {"provider_options": options},
        }}))
        config = self.resolve()
        self.assertEqual(config["providers"]["opencode-developer"]["provider_options"], options)

        self.user.write_text(yaml.safe_dump({"providers": {
            "default": {"provider_options": options},
        }}))
        with self.assertRaises(LLMToolError):
            self.resolve()

    def test_missing_endpoint_or_model_fails_request_readiness(self):
        for fields, field in (({}, "base_url"), ({"model": "test-model"}, "base_url"),
                              ({"base_url": "http://localhost:1234"}, "model")):
            with self.subTest(fields=fields):
                self.user.write_text(yaml.safe_dump({"providers": {"default": fields}}))
                # Partial configuration remains inspectable without making requests.
                self.assertIsNone(self.resolve()["providers"]["default"][field])
                with self.assertRaisesRegex(LLMToolError, "provider " + field + " is not configured"):
                    self.resolve(require_configured=True)

    def test_ready_named_provider_does_not_require_unused_default(self):
        self.configure_profiles()
        config = self.resolve(require_configured=True)
        self.assertEqual(config["active_provider"], "local")
        self.assertIsNone(config["providers"]["default"]["base_url"])
        self.assertIsNone(config["providers"]["default"]["model"])

    def test_missing_config_cli_fails_before_credentials_client_or_http(self):
        # Real resolution against an empty HOME; fail the test on any HTTP attempt.
        stdout, stderr = StringIO(), StringIO()
        def resolve(args, **kwargs):
            return self.resolve(cli=args, **kwargs)
        with patch("llmtool.cli.resolve_config", side_effect=resolve), \
                patch("llmtool.cli.api_key") as credentials, \
                patch("llmtool.cli.create_client") as factory, \
                patch("urllib.request.OpenerDirector.open") as http, \
                redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(main(["ask", "private-input"]), 1)
        credentials.assert_not_called()
        factory.assert_not_called()
        http.assert_not_called()
        self.assertEqual(stdout.getvalue(), "")
        self.assertRegex(stderr.getvalue(),
                         r"^llmtool: provider base_url is not configured\n"
                         r"llmtool: code=CONFIG_PROVIDER_NOT_READY run_id=[0-9a-f]{32} trace=not-saved\n$")

    def test_explicit_cli_or_environment_can_configure_provider(self):
        for cli, env in (({"base_url": "http://localhost:1234", "model": "test-model"}, {}),
                         ({}, {"LLMTOOL_BASE_URL": "http://localhost:1234", "LLMTOOL_MODEL": "test-model"})):
            with self.subTest(cli=cli, env=env):
                config = self.resolve(cli=cli, env=env, require_configured=True)
                self.assertEqual(config["providers"]["default"]["model"], "test-model")
