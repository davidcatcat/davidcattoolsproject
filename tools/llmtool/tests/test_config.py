import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

import yaml

from llmtool.config import (
    DEFAULTS, api_key, init_config, resolve_config, show_config, user_config_path,
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

    def resolve(self, cli=None, env=None):
        return resolve_config(cli, {} if env is None else env, self.cwd, self.home)

    def test_defaults_and_independent_instances(self):
        config = self.resolve()
        self.assertEqual(config, DEFAULTS)
        config["generation"]["max_tokens"] = 1
        self.assertEqual(self.resolve()["generation"]["max_tokens"], 2000)

    def test_user_config(self):
        self.user.write_text("generation:\n  max_tokens: 321\n", encoding="utf-8")
        self.assertEqual(self.resolve()["generation"]["max_tokens"], 321)

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
        with patch("llmtool.cli.resolve_config", side_effect=lambda *_: self.resolve(env=env)), \
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
            {"providers": {"new": {"model": "missing-endpoint"}}},
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
