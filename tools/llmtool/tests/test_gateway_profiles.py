"""Tests for the private gateway profile file loader."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from llmtool.errors import LLMToolError
from llmtool.gateway_profiles import load_gateway_profile


class GatewayProfileTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.directory), True)
        self.path = self.directory / "gateway-profiles.json"
        self.env_name = "GATEWAY_TEST_KEY_ENV"
        self.env_value = "placeholder-" + "value"
        self.environ = {self.env_name: self.env_value}
        self.host = "gw.example.invalid"

    def profile(self, **changes):
        value = {
            "base_url": "https://" + self.host,
            "model": "test-model",
            "api_key_env": self.env_name,
            "context_cap_tokens": 4000,
            "max_output_tokens": 500,
        }
        value.update(changes)
        return value

    def document(self, profiles=None, default="work"):
        return {
            "schema_version": 1,
            "default": default,
            "profiles": {"work": self.profile()} if profiles is None else profiles,
        }

    def write(self, value, mode=0o600):
        text = value if isinstance(value, str) else json.dumps(value)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.chmod(self.path, mode)
        return self.path

    def load(self, name=None, environ=None):
        return load_gateway_profile(
            name, path=self.path, environ=self.environ if environ is None else environ)

    def assert_refused(self, code, name=None, environ=None):
        with self.assertRaises(LLMToolError) as raised:
            self.load(name, environ)
        error = raised.exception
        self.assertIsNotNone(error.diagnostic)
        self.assertEqual(code, error.diagnostic.code.value)
        return error

    def test_default_profile(self):
        self.write(self.document())
        result = self.load()
        self.assertEqual({
            "name": "work",
            "base_url": "https://" + self.host,
            "model": "test-model",
            "api_key_env": self.env_name,
            "context_cap_tokens": 4000,
            "max_output_tokens": 500,
        }, result)

    def test_named_profile(self):
        profiles = {
            "work": self.profile(),
            "other": self.profile(model="other-model"),
        }
        self.write(self.document(profiles))
        result = self.load("other")
        self.assertEqual("other", result["name"])
        self.assertEqual("other-model", result["model"])

    def test_two_profiles_and_default_choice(self):
        profiles = {
            "work": self.profile(),
            "other": self.profile(model="other-model"),
        }
        self.write(self.document(profiles, default="other"))
        self.assertEqual("other", self.load()["name"])
        self.assertEqual("work", self.load("work")["name"])

    def test_result_keys_are_exact(self):
        self.write(self.document())
        result = self.load()
        self.assertEqual({
            "name", "base_url", "model", "api_key_env",
            "context_cap_tokens", "max_output_tokens",
        }, set(result))
        self.assertNotIn(self.env_value, repr(result))

    def test_base_url_path_is_refused(self):
        self.write(self.document({"work": self.profile(
            base_url="https://" + self.host + "/v1")}))
        self.assert_refused("CONFIG_INVALID")

    def test_base_url_query_is_refused(self):
        self.write(self.document({"work": self.profile(
            base_url="https://" + self.host + "?key=1")}))
        self.assert_refused("CONFIG_INVALID")

    def test_non_http_base_url_is_refused(self):
        self.write(self.document({"work": self.profile(
            base_url="ftp://" + self.host)}))
        self.assert_refused("CONFIG_INVALID")

    def test_open_mode_is_refused(self):
        self.write(self.document(), mode=0o644)
        self.assert_refused("CONFIG_INVALID")

    def test_symlink_is_refused(self):
        target = self.write(self.document())
        link = self.directory / "linked.json"
        os.symlink(target, link)
        with self.assertRaises(LLMToolError) as raised:
            load_gateway_profile(path=link, environ=self.environ)
        self.assertIsNotNone(raised.exception.diagnostic)
        self.assertEqual("CONFIG_INVALID", raised.exception.diagnostic.code.value)

    def test_error_text_has_no_host_or_env_value(self):
        self.write(self.document({"work": self.profile(
            base_url="https://" + self.host + "?key=1")}))
        error = self.assert_refused("CONFIG_INVALID")
        self.assertNotIn(self.host, str(error))
        self.assertNotIn(self.env_value, str(error))

    def test_missing_file_is_refused(self):
        self.assert_refused("CONFIG_INVALID")

    def test_bad_json_is_refused(self):
        self.write("{not json")
        self.assert_refused("CONFIG_INVALID")

    def test_unknown_top_level_field_is_refused(self):
        document = self.document()
        document["extra"] = True
        self.write(document)
        self.assert_refused("CONFIG_INVALID")

    def test_unknown_profile_field_is_refused(self):
        value = self.profile()
        value["extra"] = True
        self.write(self.document({"work": value}))
        self.assert_refused("CONFIG_INVALID")

    def test_bad_token_cap_is_refused(self):
        self.write(self.document({"work": self.profile(context_cap_tokens=0)}))
        self.assert_refused("CONFIG_INVALID")

    def test_unknown_profile_is_refused(self):
        self.write(self.document())
        self.assert_refused("CONFIG_PROVIDER_NOT_READY", name="missing")

    def test_missing_env_var_is_refused(self):
        self.write(self.document())
        self.assert_refused("AUTH_CREDENTIAL_MISSING", environ={})

    def test_empty_env_var_is_refused(self):
        self.write(self.document())
        error = self.assert_refused(
            "AUTH_CREDENTIAL_MISSING", environ={self.env_name: ""})
        self.assertNotIn(self.env_value, str(error))


if __name__ == "__main__":
    unittest.main()
