import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY

import yaml

from llmtool.config import (
    DEFAULTS, _apply_user, _read_yaml, inspect_config, resolve_config, user_config_path,
)
from llmtool.errors import LLMToolError
from llmtool.role_config import resolve_role

# The claude-reviewer model listed in developer-routes.json (#1502 WP-E).
REVIEWER_MODEL = "claude-sonnet-5-5"


class RoleConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.cwd = self.root / "project"
        self.cwd.mkdir()
        self.user = user_config_path(self.home)
        self.user.parent.mkdir(parents=True)
        self.account_dir = self.root / "account"
        self.account_dir.mkdir(mode=0o700)
        self.account_dir.chmod(0o700)

    def save(self, value):
        self.user.write_text(yaml.safe_dump(value), encoding="utf-8")

    def account_config(self):
        return {"accounts": {"work": {"kind": "claude", "config_dir": str(self.account_dir)}},
                # #1502 WP-E: a reviewer model must be the registry's listed model.
                "roles": {"reviewer": {"account": "work", "model": REVIEWER_MODEL,
                                       "effort": "high"}}}

    def test_precedence_cli_over_environment_over_role_over_legacy_over_builtin(self):
        self.assertEqual(resolve_role("coder", environ={}, home=self.home), {
            "role": "coder", "provider": None, "provider_profile": None, "account": None,
            "config_dir": None, "model": None, "effort": None, "source": "builtin"})
        self.save({"pm_runtime": {"claude_config_dir": str(self.account_dir)}})
        alias = resolve_role("pm", environ={}, home=self.home)
        self.assertEqual(alias["provider"], "claude")
        self.assertEqual(alias["config_dir"], str(self.account_dir))
        self.assertEqual(alias["source"], "pm_runtime")
        self.save({"accounts": self.account_config()["accounts"],
                   "roles": {"pm": {"account": "work", "model": "role-model", "effort": "low"},
                             "reviewer": self.account_config()["roles"]["reviewer"]},
                   "pm_runtime": {"claude_config_dir": str(self.account_dir)}})
        loaded = DEFAULTS.copy()
        _apply_user(loaded, _read_yaml(user_config_path(self.home), "user config"))
        self.assertNotIn("roles", loaded)
        self.assertNotIn("accounts", loaded)
        role_only = resolve_role("pm", environ={}, home=self.home)
        self.assertEqual((role_only["account"], role_only["model"], role_only["source"]),
                         ("work", "role-model", "roles"))
        env = {"LLMTOOL_ROLE_PM_MODEL": "env-model", "LLMTOOL_ROLE_PM_EFFORT": "medium"}
        cli = {"model": "cli-model", "effort": "max"}
        before = self.user.read_bytes()
        resolved = resolve_role("pm", cli=cli, environ=env, home=self.home)
        self.assertEqual((resolved["model"], resolved["effort"], resolved["source"]),
                         ("cli-model", "max", "cli"))
        self.assertEqual(self.user.read_bytes(), before)
        env_only = resolve_role("pm", environ=env, home=self.home)
        self.assertEqual((env_only["model"], env_only["effort"], env_only["source"]),
                         ("env-model", "medium", "env"))
        reviewer = resolve_role("reviewer", environ={}, home=self.home)
        self.assertEqual((reviewer["account"], reviewer["model"], reviewer["source"]),
                         ("work", REVIEWER_MODEL, "roles"))

    def test_reviewer_model_outside_the_registry_is_refused_at_every_layer(self):
        # #1502 WP-E R1: the resolver applies the shared check after precedence.
        self.save(self.account_config())
        for cli, env in (
                (None, {"LLMTOOL_ROLE_REVIEWER_MODEL": "claude-other-model"}),
                ({"model": "gpt-6.1-sol"}, {}),
                ({"model": "anthropic/" + REVIEWER_MODEL}, {})):
            with self.subTest(cli=cli, env=env):
                with self.assertRaises(LLMToolError) as caught:
                    resolve_role("reviewer", cli=cli, environ=env, home=self.home)
                self.assertEqual(caught.exception.role_config_code, "ROLE_MODEL_NOT_ALLOWED")
        config = self.account_config()
        config["roles"]["reviewer"]["model"] = REVIEWER_MODEL.upper()
        self.save(config)
        with self.assertRaises(LLMToolError) as caught:
            resolve_role("reviewer", environ={}, home=self.home)
        self.assertEqual(caught.exception.role_config_code, "ROLE_MODEL_NOT_ALLOWED")

    def test_project_config_cannot_set_roles_or_accounts(self):
        self.cwd.joinpath(".llmtool.yaml").write_text("roles: {pm: {model: attacker}}\n", encoding="utf-8")
        with self.assertRaisesRegex(LLMToolError, "project config: forbidden or unsupported field"):
            resolve_config(environ={}, cwd=self.cwd, home=self.home)
        self.cwd.joinpath(".llmtool.yaml").write_text("accounts: {}\n", encoding="utf-8")
        with self.assertRaisesRegex(LLMToolError, "project config: forbidden or unsupported field"):
            resolve_config(environ={}, cwd=self.cwd, home=self.home)

    def test_legacy_alias_conflict_has_sanitized_code(self):
        other = self.root / "other"
        other.mkdir(mode=0o700)
        other.chmod(0o700)
        self.save({"accounts": {"work": {"kind": "claude", "config_dir": str(self.account_dir)}},
                   "roles": {"pm": {"account": "work"}},
                   "pm_runtime": {"claude_config_dir": str(other)}})
        with self.assertRaises(LLMToolError) as caught:
            resolve_role("pm", environ={}, home=self.home)
        self.assertEqual(caught.exception.role_config_code, "ROLE_CONFIG_ALIAS_CONFLICT")
        self.assertNotIn(str(other), str(caught.exception))

    def test_account_dirs_require_owned_private_real_directory_and_never_read_files(self):
        good = self.account_config()
        self.save(good)
        self.account_dir.joinpath("credentials.json").write_text("secret", encoding="utf-8")
        self.assertEqual(resolve_role("reviewer", environ={}, home=self.home)["config_dir"],
                         str(self.account_dir))
        symlink = self.root / "account-link"
        symlink.symlink_to(self.account_dir, target_is_directory=True)
        cases = [str(symlink)]
        writable = self.root / "writable"
        writable.mkdir()
        writable.chmod(0o777)
        cases.append(str(writable))
        cases.append(str(self.root / "missing"))
        file_path = self.root / "not-directory"
        file_path.write_text("x", encoding="utf-8")
        cases.append(str(file_path))
        for bad_path in cases:
            with self.subTest(path=bad_path):
                self.save({"accounts": {"work": {"kind": "claude", "config_dir": bad_path}},
                           "roles": {"reviewer": {"account": "work"}}})
                with self.assertRaises(LLMToolError) as caught:
                    resolve_role("reviewer", environ={}, home=self.home)
                self.assertNotIn(bad_path, str(caught.exception))

    def test_unknown_keys_and_bad_account_or_role_fields_are_rejected(self):
        invalid = (
            {"accounts": {"work": {"kind": "claude", "config_dir": str(self.account_dir), "token": "x"}}},
            {"accounts": {"work": {"kind": "owner", "config_dir": str(self.account_dir)}}},
            {"roles": {"coder": {"unknown": "x"}}},
            {"roles": {"coder": {"account": "missing"}}},
        )
        for config in invalid:
            with self.subTest(config=config):
                self.save(config)
                with self.assertRaises(LLMToolError):
                    resolve_role("coder", environ={}, home=self.home)

    def test_builtin_config_and_inspection_output_remain_neutral(self):
        result = resolve_role("pm", environ={}, home=self.home)
        self.assertIsNone(result["config_dir"])
        self.assertIsNone(result["account"])
        self.assertEqual(inspect_config(environ={}, cwd=self.cwd, home=self.home), {
            "settings": ANY, "provider_type": "openai-compatible",
            "readiness": "not-configured", "credential": "not-configured"})

    def test_role_environment_overrides_are_scoped_to_the_named_role(self):
        self.save(self.account_config())
        env = {"LLMTOOL_ROLE_PM_MODEL": "pm-model"}
        reviewer = resolve_role("reviewer", environ=env, home=self.home)
        self.assertEqual((reviewer["model"], reviewer["source"]), (REVIEWER_MODEL, "roles"))
        pm = resolve_role("pm", environ=env, home=self.home)
        self.assertEqual((pm["model"], pm["source"]), ("pm-model", "env"))

    def test_account_and_provider_overrides_are_order_independent(self):
        self.save(self.account_config())
        for cli in (
                {"account": "work", "provider": "claude"},
                {"provider": "claude", "account": "work"}):
            with self.subTest(cli=cli):
                resolved = resolve_role("coder", cli=cli, environ={}, home=self.home)
                self.assertEqual((resolved["account"], resolved["provider"], resolved["source"]),
                                 ("work", "claude", "cli"))

    def test_provider_override_cannot_mismatch_legacy_account_directory(self):
        self.save({"pm_runtime": {"claude_config_dir": str(self.account_dir)}})
        with self.assertRaises(LLMToolError) as caught:
            resolve_role("pm", cli={"provider": "codex"}, environ={}, home=self.home)
        self.assertEqual(caught.exception.role_config_code, "ROLE_CONFIG_OVERRIDE_INVALID")

    def test_general_config_hides_roles_and_defers_unused_account_dir_checks(self):
        missing = self.root / "missing-unused-account"
        self.save({
            "accounts": {
                "unused": {"kind": "claude", "config_dir": str(missing)},
                "work": self.account_config()["accounts"]["work"],
            },
            "roles": {"reviewer": self.account_config()["roles"]["reviewer"]},
        })
        config = resolve_config(environ={}, cwd=self.cwd, home=self.home)
        from llmtool.config import show_config
        rendered = show_config(config)
        self.assertNotIn("accounts:", rendered)
        self.assertNotIn("roles:", rendered)
        self.assertEqual(resolve_role("reviewer", environ={}, home=self.home)["account"], "work")


if __name__ == "__main__":
    unittest.main()
