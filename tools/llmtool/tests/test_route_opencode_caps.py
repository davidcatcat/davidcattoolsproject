"""#2281: prompt and source-file size cap tests for OpenCode Developer route."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from llmtool.developer_routes import (
    RouteError,
    load_route_config,
    max_prompt_bytes_ok,
    max_source_file_bytes_ok,
    max_source_total_bytes_ok,
    resolve_route_max_prompt_bytes,
    resolve_route_max_source_file_bytes,
    resolve_route_max_source_total_bytes,
)
from llmtool.developer_routes.base import (
    DEFAULT_OPENCODE_MAX_PROMPT_BYTES,
    DEFAULT_OPENCODE_MAX_SOURCE_FILE_BYTES,
    DEFAULT_OPENCODE_MAX_SOURCE_TOTAL_BYTES,
)
from llmtool.opencode_direct_developer import (
    DirectOpenCodeError,
    _read_scoped_file,
    _screen_prompt,
    _screen_sources,
    resolved_max_prompt_bytes,
    resolved_max_source_file_bytes,
    resolved_max_source_total_bytes,
)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "guidance_drift_check.py"
SPEC = importlib.util.spec_from_file_location("guidance_drift_check", SCRIPT)
guidance_drift_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guidance_drift_check)


class RouteOpenCodeCapsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_validators_ok(self):
        # #2281: 1..5 MiB; a value above 5 MiB is refused (fail closed, CWE-400).
        for valid in (1, 100, 65536, 1048576, 2097152, 5242880):
            with self.subTest(valid=valid):
                self.assertTrue(max_prompt_bytes_ok(valid))
                self.assertTrue(max_source_file_bytes_ok(valid))
                self.assertTrue(max_source_total_bytes_ok(valid))

        for invalid in (0, -1, -500, True, False, "1048576", 1048576.0, None, [], {},
                        5242881, 10_000_000):
            with self.subTest(invalid=invalid):
                self.assertFalse(max_prompt_bytes_ok(invalid))
                self.assertFalse(max_source_file_bytes_ok(invalid))
                self.assertFalse(max_source_total_bytes_ok(invalid))

    def test_loader_accepts_repository_keys(self):
        config = load_route_config()
        opencode = config["routes"]["opencode"]
        # #2281 (Owner 2026-11-19): 5 MiB each.
        self.assertEqual(opencode["max_prompt_bytes"], 5242880)
        self.assertEqual(opencode["max_source_file_bytes"], 5242880)
        self.assertEqual(opencode["max_source_total_bytes"], 5242880)

    def test_loader_refuses_bad_source_and_prompt_bytes_values(self):
        base_config = load_route_config()
        bad_values = (0, -1, -100, True, False, "1048576", 1048576.0, None, [1048576],
                      5242881, 10_000_000)
        for key in ("max_prompt_bytes", "max_source_file_bytes", "max_source_total_bytes"):
            for bad in bad_values:
                with self.subTest(key=key, bad=bad):
                    broken = json.loads(json.dumps(base_config))
                    broken["routes"]["opencode"][key] = bad
                    path = self.root / "broken.json"
                    path.write_text(json.dumps(broken), encoding="utf-8")
                    with self.assertRaises(RouteError) as caught:
                        load_route_config(path)
                    self.assertEqual(caught.exception.code, "ROUTE_CONFIG_INVALID")

    def test_resolver_default_and_configured_values(self):
        config_without_caps = {"routes": {"opencode": {}}}
        self.assertEqual(
            resolve_route_max_prompt_bytes("opencode", config=config_without_caps),
            DEFAULT_OPENCODE_MAX_PROMPT_BYTES,
        )
        self.assertEqual(
            resolve_route_max_source_file_bytes("opencode", config=config_without_caps),
            DEFAULT_OPENCODE_MAX_SOURCE_FILE_BYTES,
        )
        self.assertEqual(
            resolve_route_max_source_total_bytes("opencode", config=config_without_caps),
            DEFAULT_OPENCODE_MAX_SOURCE_TOTAL_BYTES,
        )

        config_with_caps = {
            "routes": {
                "opencode": {
                    "max_prompt_bytes": 32768,
                    "max_source_file_bytes": 524288,
                    "max_source_total_bytes": 1048576,
                }
            }
        }
        self.assertEqual(
            resolve_route_max_prompt_bytes("opencode", config=config_with_caps),
            32768,
        )
        self.assertEqual(
            resolve_route_max_source_file_bytes("opencode", config=config_with_caps),
            524288,
        )
        self.assertEqual(
            resolve_route_max_source_total_bytes("opencode", config=config_with_caps),
            1048576,
        )

        self.assertEqual(resolved_max_prompt_bytes(config=config_with_caps), 32768)
        self.assertEqual(resolved_max_source_file_bytes(config=config_with_caps), 524288)
        self.assertEqual(resolved_max_source_total_bytes(config=config_with_caps), 1048576)

    def test_resolver_refuses_invalid_routes_and_values(self):
        with self.assertRaises(RouteError) as caught:
            resolve_route_max_source_file_bytes("unknown_route", config={"routes": {}})
        self.assertEqual(caught.exception.code, "ROUTE_CONFIG_INVALID")

        with self.assertRaises(RouteError) as caught:
            resolve_route_max_source_total_bytes("unknown_route", config={"routes": {}})
        self.assertEqual(caught.exception.code, "ROUTE_CONFIG_INVALID")

        with self.assertRaises(RouteError) as caught:
            resolve_route_max_source_file_bytes(
                "opencode", config={"routes": {"opencode": {"max_source_file_bytes": 0}}}
            )
        self.assertEqual(caught.exception.code, "ROUTE_CONFIG_INVALID")

        with self.assertRaises(RouteError) as caught:
            resolve_route_max_source_total_bytes(
                "opencode", config={"routes": {"opencode": {"max_source_total_bytes": 0}}}
            )
        self.assertEqual(caught.exception.code, "ROUTE_CONFIG_INVALID")

        # #2281 security repair (CWE-400): above 5 MiB is refused by every resolver.
        resolvers = (
            ("max_prompt_bytes", resolve_route_max_prompt_bytes, resolved_max_prompt_bytes),
            ("max_source_file_bytes", resolve_route_max_source_file_bytes,
             resolved_max_source_file_bytes),
            ("max_source_total_bytes", resolve_route_max_source_total_bytes,
             resolved_max_source_total_bytes),
        )
        for key, resolve, resolved in resolvers:
            config = {"routes": {"opencode": {key: 5242881}}}
            with self.subTest(key=key):
                with self.assertRaises(RouteError) as caught:
                    resolve("opencode", config=config)
                self.assertEqual(caught.exception.code, "ROUTE_CONFIG_INVALID")
                with self.assertRaises(DirectOpenCodeError) as caught:
                    resolved(config=config)
                self.assertEqual(caught.exception.direct_opencode_code, "ROUTE_CONFIG_INVALID")
                self.assertEqual(
                    resolve("opencode", config={"routes": {"opencode": {key: 5242880}}}),
                    5242880)

    def test_over_5_mib_config_never_widens_prompt_or_source_reads(self):
        # #2281 security repair: a config above 5 MiB fails closed before any
        # prompt or source is read, rather than permitting a larger read.
        (self.root / "test.py").write_text("x" * 10, encoding="utf-8")
        for key in ("max_prompt_bytes", "max_source_file_bytes", "max_source_total_bytes"):
            config = {"routes": {"opencode": {key: 5242881}}}
            with self.subTest(key=key), \
                    patch("llmtool.developer_routes.load_route_config", return_value=config):
                with self.assertRaises(DirectOpenCodeError) as caught:
                    if key == "max_prompt_bytes":
                        _screen_prompt("x" * 10)
                    elif key == "max_source_file_bytes":
                        _read_scoped_file(self.root, "test.py")
                    else:
                        _screen_sources(self.root, ["test.py"])
                self.assertEqual(caught.exception.direct_opencode_code, "ROUTE_CONFIG_INVALID")

    def test_configured_smaller_value_enforced_prompt(self):
        custom_config = {
            "routes": {
                "opencode": {
                    "max_prompt_bytes": 100,
                }
            }
        }
        with patch("llmtool.developer_routes.load_route_config", return_value=custom_config):
            payload = _screen_prompt("x" * 100)
            self.assertEqual(payload, b"x" * 100)

            with self.assertRaises(DirectOpenCodeError) as caught:
                _screen_prompt("x" * 101)
            self.assertEqual(caught.exception.direct_opencode_code, "OPENCODE_PROMPT_TOO_LARGE")

    def test_configured_smaller_value_enforced_one_source_file(self):
        test_file = self.root / "test.py"
        test_file.write_text("x" * 100, encoding="utf-8")

        custom_config = {
            "routes": {
                "opencode": {
                    "max_source_file_bytes": 100,
                    "max_source_total_bytes": 1000,
                }
            }
        }
        with patch("llmtool.developer_routes.load_route_config", return_value=custom_config):
            captured = _read_scoped_file(self.root, "test.py")
            self.assertIsNotNone(captured)
            self.assertEqual(captured[1], 100)

            test_file.write_text("x" * 101, encoding="utf-8")
            with self.assertRaises(DirectOpenCodeError) as caught:
                _read_scoped_file(self.root, "test.py")
            self.assertEqual(caught.exception.direct_opencode_code, "OPENCODE_SOURCE_TOO_LARGE")

    def test_configured_smaller_value_enforced_source_total(self):
        file1 = self.root / "file1.py"
        file2 = self.root / "file2.py"
        file1.write_text("x" * 70, encoding="utf-8")
        file2.write_text("x" * 70, encoding="utf-8")

        custom_config = {
            "routes": {
                "opencode": {
                    "max_source_file_bytes": 100,
                    "max_source_total_bytes": 120,
                }
            }
        }
        with patch("llmtool.developer_routes.load_route_config", return_value=custom_config):
            with self.assertRaises(DirectOpenCodeError) as caught:
                _screen_sources(self.root, ["file1.py", "file2.py"])
            self.assertEqual(caught.exception.direct_opencode_code, "OPENCODE_SOURCE_TOO_LARGE")

        custom_config_ok = {
            "routes": {
                "opencode": {
                    "max_source_file_bytes": 100,
                    "max_source_total_bytes": 150,
                }
            }
        }
        with patch("llmtool.developer_routes.load_route_config", return_value=custom_config_ok):
            total, count, digest, entries = _screen_sources(self.root, ["file1.py", "file2.py"])
            self.assertEqual(total, 140)
            self.assertEqual(count, 2)

    def test_drift_check_schema_accepts_keys(self):
        schema = guidance_drift_check.DEVELOPER_ROUTE
        valid_entry = {
            "executable": "opencode",
            "default_model": "opencode/deepseek-v4.1-flash-free",
            "allowed_models": ["opencode/deepseek-v4.1-flash-free"],
            "default_effort": "high",
            "default_timeout": 1800,
            "max_timeout": 3600,
            "max_prompt_bytes": 65536,
            "max_source_file_bytes": 1048576,
            "max_source_total_bytes": 2097152,
        }
        self.assertEqual(schema.errors(valid_entry, "route"), [])

        for key in ("max_prompt_bytes", "max_source_file_bytes", "max_source_total_bytes"):
            self.assertEqual(schema.errors(dict(valid_entry, **{key: 5242880}), "route"), [])
            for bad in (0, -1, True, False, "1048576", 5242881):
                with self.subTest(key=key, bad=bad):
                    bad_entry = dict(valid_entry, **{key: bad})
                    errors = schema.errors(bad_entry, "route")
                    self.assertTrue(
                        any(f"{key} must be an integer >= 1 and <= 5242880" in err
                            for err in errors),
                        errors,
                    )

    def test_fresh_process_startup_without_route_config(self):
        malformed_path = self.root / "malformed_routes.json"
        malformed_path.write_text("{ malformed json", encoding="utf-8")

        tools_llmtool = str(Path(__file__).resolve().parents[1])
        code = (
            "import sys\n"
            "from pathlib import Path\n"
            f"tools_llmtool = {tools_llmtool!r}\n"
            "if tools_llmtool not in sys.path:\n"
            "    sys.path.insert(0, tools_llmtool)\n"
            "from llmtool import developer_routes\n"
            "orig_load = developer_routes.load_route_config\n"
            f"substitute_path = Path({str(malformed_path)!r})\n"
            "developer_routes.load_route_config = lambda path=None: orig_load(substitute_path if path is None else path)\n"
            "assert 'llmtool.opencode_direct_developer' not in sys.modules\n"
            "from llmtool import opencode_direct_developer\n"
            "assert opencode_direct_developer.DEFAULT_MAX_PROMPT_BYTES == 5242880\n"
            "assert opencode_direct_developer.DEFAULT_MAX_SOURCE_FILE_BYTES == 5242880\n"
            "assert opencode_direct_developer.DEFAULT_MAX_SOURCE_TOTAL_BYTES == 5242880\n"
            "try:\n"
            "    opencode_direct_developer.resolved_max_prompt_bytes()\n"
            "    sys.exit(101)\n"
            "except opencode_direct_developer.DirectOpenCodeError as exc:\n"
            "    if exc.direct_opencode_code != 'ROUTE_CONFIG_INVALID':\n"
            "        sys.exit(102)\n"
            "try:\n"
            "    opencode_direct_developer.resolved_max_source_file_bytes()\n"
            "    sys.exit(103)\n"
            "except opencode_direct_developer.DirectOpenCodeError as exc:\n"
            "    if exc.direct_opencode_code != 'ROUTE_CONFIG_INVALID':\n"
            "        sys.exit(104)\n"
            "try:\n"
            "    opencode_direct_developer.resolved_max_source_total_bytes()\n"
            "    sys.exit(105)\n"
            "except opencode_direct_developer.DirectOpenCodeError as exc:\n"
            "    if exc.direct_opencode_code != 'ROUTE_CONFIG_INVALID':\n"
            "        sys.exit(106)\n"
            "sys.exit(0)\n"
        )
        env = dict(os.environ, PYTHONPATH=tools_llmtool)
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(proc.returncode, 0, f"Process failed: {proc.stderr}\n{proc.stdout}")


if __name__ == "__main__":
    unittest.main()
