import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from llmtool.configure import run_configure
from llmtool.errors import LLMToolError

ENTRYPOINT = Path(__file__).resolve().parents[1] / "llmtool.py"


class InteractiveInput:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return next(self.answers)


class TTY:
    def isatty(self):
        return True


class ConfigureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.path = self.home / ".config/llmtool/config.yaml"

    def answers(self, url="https://llm.example:8000", model="test-model", **overrides):
        values = ["", "", url, model, "", "", "", "", "", ""]
        for index, value in overrides.items():
            values[int(index)] = value
        return InteractiveInput(values)

    def run_wizard(self, answers, secret="", **kwargs):
        out, err = io.StringIO(), io.StringIO()
        with patch("sys.stdin", TTY()):
            result = run_configure(
                home=self.home, input_fn=answers, secret_fn=lambda prompt, stream=None: secret,
                stdout=out, stderr=err, **kwargs
            )
        return result, out.getvalue(), err.getvalue()

    def test_blank_answers_use_defaults_and_empty_optional_values_unset(self):
        answers = self.answers()
        result, out, err = self.run_wizard(answers)
        self.assertTrue(result)
        config = yaml.safe_load(self.path.read_text())
        self.assertEqual(config["active_provider"], "default")
        provider = config["providers"]["default"]
        self.assertEqual(provider["type"], "openai-compatible")
        self.assertEqual(provider["timeout_seconds"], 120)
        self.assertIsNone(provider["api_key"])
        self.assertIsNone(provider["api_key_env"])
        self.assertEqual(provider["provider_options"], {})
        self.assertEqual(config["generation"], {"temperature": 0.0, "max_tokens": 2000})
        self.assertIn("Configuration saved: " + str(self.path), out)
        self.assertIn("Base URL: https://llm.example:8000", out)
        self.assertIn("Model: test-model", out)
        self.assertEqual(err, "")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("API key (leave blank to unset): ", answers.prompts)

    def test_blank_base_url_and_model_are_saved_as_null(self):
        result, out, err = self.run_wizard(self.answers(url="", model=""))
        self.assertTrue(result)
        saved = yaml.safe_load(self.path.read_text())
        self.assertIsNone(saved["providers"]["default"]["base_url"])
        self.assertIsNone(saved["providers"]["default"]["model"])
        self.assertIn("Base URL: <not configured>", out)
        self.assertIn("Model: <not configured>", out)
        self.assertEqual(err, "")
        self.assertTrue(self.path.is_file())

    def test_partial_base_url_with_blank_model_is_saved(self):
        result, out, _ = self.run_wizard(self.answers(url="https://llm.example", model=""))
        self.assertTrue(result)
        saved = yaml.safe_load(self.path.read_text())
        self.assertEqual(saved["providers"]["default"]["base_url"], "https://llm.example")
        self.assertIsNone(saved["providers"]["default"]["model"])
        self.assertIn("Model: <not configured>", out)

    def test_model_command_rejects_saved_unconfigured_profile_before_http(self):
        self.run_wizard(self.answers(url="", model=""))
        env = {
            "HOME": str(self.home), "PATH": os.environ.get("PATH", ""),
            "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
        }
        result = subprocess.run(
            [sys.executable, str(ENTRYPOINT), "ask", "test input"],
            cwd=self.temp.name, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=10, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"llmtool: provider base_url is not configured\n")

    def test_hidden_api_key_is_never_emitted(self):
        secret = "fake-private-api-token"
        result, out, err = self.run_wizard(self.answers(), secret=secret)
        self.assertTrue(result)
        self.assertNotIn(secret, out + err)
        self.assertIn(secret, self.path.read_text())

    def test_api_key_env_can_be_used_without_literal_key(self):
        answers = self.answers(**{"5": "LLMTOOL_TEST_KEY"})
        result, out, err = self.run_wizard(answers)
        self.assertTrue(result)
        provider = yaml.safe_load(self.path.read_text())["providers"]["default"]
        self.assertIsNone(provider["api_key"])
        self.assertEqual(provider["api_key_env"], "LLMTOOL_TEST_KEY")
        self.assertNotIn("LLMTOOL_TEST_KEY", out + err)

    def test_api_key_and_env_conflict_rejected_without_leak_or_write(self):
        answers = self.answers(**{"5": "TEST_KEY"})
        out, err = io.StringIO(), io.StringIO()
        secret = "fake-conflict-secret"
        with patch("sys.stdin", TTY()), self.assertRaisesRegex(LLMToolError, "choose either"):
            run_configure(self.home, answers, lambda prompt, stream=None: secret, out, err)
        self.assertFalse(self.path.exists())
        self.assertNotIn(secret, out.getvalue() + err.getvalue())

    def test_existing_config_defaults_to_no_and_is_unchanged(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("generation: {max_tokens: 77}\n")
        original = self.path.read_bytes()
        answers = InteractiveInput([""])
        result, out, _ = self.run_wizard(answers)
        self.assertFalse(result)
        self.assertIn("Configuration unchanged", out)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(answers.prompts, ["User config exists. Update it? [y/N]: "])

    def test_update_existing_config_preserves_other_profiles(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(yaml.safe_dump({
            "providers": {"other": {"type": "openai-compatible", "base_url": "https://other.example",
                                      "model": "other-model"}},
            "active_provider": "other", "generation": {"max_tokens": 30},
        }))
        answers = InteractiveInput(["yes", "new", "", "https://new.example", "new-model",
                                    "", "", "false", "", "", ""])
        result, out, _ = self.run_wizard(answers)
        self.assertTrue(result)
        saved = yaml.safe_load(self.path.read_text())
        self.assertIn("other", saved["providers"])
        self.assertEqual(saved["active_provider"], "new")
        self.assertEqual(saved["providers"]["new"]["provider_options"], {"enable_thinking": False})
        self.assertIn("Active provider: new", out)

    def test_invalid_endpoint_or_model_rejected_before_write(self):
        for url, model in (("file:///etc/passwd", "model"),
                           ("https://llm.example", "invalid\x01model")):
            with self.subTest(url=url, model=model):
                with self.assertRaises(LLMToolError):
                    self.run_wizard(self.answers(url=url, model=model))
                self.assertFalse(self.path.exists())

    def test_atomic_update_failure_keeps_existing_file(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("generation: {max_tokens: 77}\n")
        original = self.path.read_bytes()
        answers = InteractiveInput(["yes", "", "", "https://llm.example", "model", "", "", "", "", "", "", ""])
        with patch("sys.stdin", TTY()), patch("llmtool.config.os.replace", side_effect=OSError):
            with self.assertRaises(LLMToolError):
                run_configure(self.home, answers, lambda prompt, stream=None: "", io.StringIO(), io.StringIO())
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob(".llmtool-*")), [])

    def test_non_tty_fails_before_secret_prompt(self):
        answers = self.answers()
        with patch("sys.stdin", io.StringIO("")):
            with self.assertRaisesRegex(LLMToolError, "interactive terminal"):
                run_configure(self.home, answers, lambda *a, **k: self.fail("secret read"), io.StringIO(), io.StringIO())
        self.assertFalse(self.path.exists())

    def test_no_provider_network_call(self):
        answers = self.answers()
        with patch("sys.stdin", TTY()), patch("llmtool.providers.create_client") as create_client:
            run_configure(self.home, answers, lambda prompt, stream=None: "", io.StringIO(), io.StringIO())
        create_client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
