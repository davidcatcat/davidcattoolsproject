import os
import tempfile
import unittest
from pathlib import Path

import yaml

from llmtool.config import (
    DEFAULTS, api_key, init_config, resolve_config, show_config, user_config_path,
)
from llmtool.errors import LLMToolError


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
        return resolve_config(cli, env or {}, self.cwd, self.home)

    def test_defaults_and_independent_instances(self):
        config = self.resolve()
        self.assertEqual(config, DEFAULTS)
        config["generation"]["max_tokens"] = 1
        self.assertEqual(self.resolve()["generation"]["max_tokens"], 2000)

    def test_user_config(self):
        self.user.write_text("generation:\n  max_tokens: 321\n", encoding="utf-8")
        self.assertEqual(self.resolve()["generation"]["max_tokens"], 321)

    def test_project_partial_override_and_user_inheritance(self):
        self.user.write_text("providers:\n  company:\n    model: user-route\n    timeout_seconds: 30\n")
        self.project.write_text("providers:\n  company:\n    model: project-route\n")
        provider = self.resolve()["providers"]["company"]
        self.assertEqual(provider["model"], "project-route")
        self.assertEqual(provider["timeout_seconds"], 30)
        self.assertEqual(provider["base_url"], DEFAULTS["providers"]["company"]["base_url"])

    def test_all_environment_overrides(self):
        config = self.resolve(env={
            "LLMTOOL_BASE_URL": "http://localhost:1234/", "LLMTOOL_MODEL": "env-route",
            "LLMTOOL_TIMEOUT": "2.5", "LLMTOOL_MAX_TOKENS": "17",
        })
        provider = config["providers"]["company"]
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
            {"providers": {"company": {"timeout_seconds": -1}}},
            {"providers": {"company": {"model": []}}},
            {"providers": {"company": {"type": "other"}}},
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
        self.user.write_text("providers:\n  company:\n    api_key: test-secret-value\n")
        config = self.resolve()
        self.assertEqual(api_key(config, {}), "test-secret-value")
        rendered = show_config(config)
        self.assertNotIn("test-secret-value", rendered)
        self.assertIn("REDACTED", rendered)
        self.assertEqual(config["providers"]["company"]["api_key"], "test-secret-value")

    def test_api_key_environment_is_explicit(self):
        self.user.write_text("providers:\n  company:\n    api_key_env: TEST_KEY\n")
        config = self.resolve()
        self.assertIn("REDACTED", show_config(config))
        self.assertEqual(api_key(config, {"TEST_KEY": "test-key"}), "test-key")
        for env in ({}, {"TEST_KEY": ""}, {"TEST_KEY": "secret\ninjected"}):
            with self.subTest(env=env), self.assertRaises(LLMToolError):
                api_key(config, env)

    def test_reject_ambiguous_auth(self):
        self.user.write_text("providers:\n  company:\n    api_key: test\n    api_key_env: TEST_KEY\n")
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
        self.project.write_bytes(example.read_bytes())
        self.assertEqual(self.resolve(), DEFAULTS)
