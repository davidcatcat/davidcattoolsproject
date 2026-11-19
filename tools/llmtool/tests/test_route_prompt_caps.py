"""#2281: prompt-size cap tests for Claude and AGY Developer routes."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from llmtool.developer_routes import (
    AgyRoute,
    ClaudeRoute,
    RouteError,
    load_route_config,
    max_prompt_bytes_ok,
    resolve_route_max_prompt_bytes,
)
from llmtool.developer_routes.base import (
    DEFAULT_AGY_MAX_PROMPT_BYTES,
    DEFAULT_CLAUDE_MAX_PROMPT_BYTES,
)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "guidance_drift_check.py"
SPEC = importlib.util.spec_from_file_location("guidance_drift_check", SCRIPT)
guidance_drift_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guidance_drift_check)


class RoutePromptCapsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_max_prompt_bytes_ok(self):
        for valid in (1, 100, 65536, 524288, 10_000_000):
            with self.subTest(valid=valid):
                self.assertTrue(max_prompt_bytes_ok(valid))

        for invalid in (0, -1, -500, True, False, "65536", 65536.0, None, [], {}):
            with self.subTest(invalid=invalid):
                self.assertFalse(max_prompt_bytes_ok(invalid))

    def test_loader_accepts_repository_keys(self):
        config = load_route_config()
        self.assertEqual(config["routes"]["claude"]["max_prompt_bytes"], 65536)
        # #2281 (Owner 2026-11-19): AGY is 5 MiB; Claude is unchanged.
        self.assertEqual(config["routes"]["agy"]["max_prompt_bytes"], 5242880)

    def test_loader_refuses_bad_max_prompt_bytes_values(self):
        base_config = load_route_config()
        bad_values = (0, -1, -100, True, False, "65536", 65536.0, None, [65536])
        for route_name in ("claude", "agy"):
            for bad in bad_values:
                with self.subTest(route=route_name, bad=bad):
                    broken = json.loads(json.dumps(base_config))
                    broken["routes"][route_name]["max_prompt_bytes"] = bad
                    path = self.root / "broken.json"
                    path.write_text(json.dumps(broken), encoding="utf-8")
                    with self.assertRaises(RouteError) as caught:
                        load_route_config(path)
                    self.assertEqual(caught.exception.code, "ROUTE_CONFIG_INVALID")

    def test_resolver_default_and_configured_values(self):
        config_without_caps = {"routes": {"claude": {}, "agy": {}}}
        self.assertEqual(
            resolve_route_max_prompt_bytes("claude", config=config_without_caps),
            DEFAULT_CLAUDE_MAX_PROMPT_BYTES,
        )
        self.assertEqual(
            resolve_route_max_prompt_bytes("agy", config=config_without_caps),
            DEFAULT_AGY_MAX_PROMPT_BYTES,
        )

        config_with_caps = {
            "routes": {
                "claude": {"max_prompt_bytes": 32768},
                "agy": {"max_prompt_bytes": 100000},
            }
        }
        self.assertEqual(
            resolve_route_max_prompt_bytes("claude", config=config_with_caps),
            32768,
        )
        self.assertEqual(
            resolve_route_max_prompt_bytes("agy", config=config_with_caps),
            100000,
        )

        claude_adapter = ClaudeRoute({"executable": "claude"})
        agy_adapter = AgyRoute({"executable": "agy"})
        self.assertEqual(
            resolve_route_max_prompt_bytes(claude_adapter, config=config_with_caps),
            32768,
        )
        self.assertEqual(
            resolve_route_max_prompt_bytes(agy_adapter, config=config_with_caps),
            100000,
        )

    def test_resolver_refuses_invalid_routes_and_values(self):
        with self.assertRaises(RouteError) as caught:
            resolve_route_max_prompt_bytes("unknown_route", config={"routes": {}})
        self.assertEqual(caught.exception.code, "ROUTE_CONFIG_INVALID")

        with self.assertRaises(RouteError) as caught:
            resolve_route_max_prompt_bytes(
                "claude", config={"routes": {"claude": {"max_prompt_bytes": 0}}}
            )
        self.assertEqual(caught.exception.code, "ROUTE_CONFIG_INVALID")

    def test_configured_smaller_value_enforced_by_claude_route(self):
        model = load_route_config()["routes"]["claude"]["default_model"]
        route = ClaudeRoute({"executable": "claude", "max_prompt_bytes": 100})
        request = {
            "root": str(self.root),
            "model": model,
            "effort": "high",
            "expected_branch": "main",
            "expected_head": "0" * 40,
            "write_paths": ["source.py"],
            "executable": "/usr/bin/claude",
            "prompt": "x" * 100,
        }
        validated = route.validate_request(request)
        self.assertEqual(validated["prompt"], "x" * 100)

        request["prompt"] = "x" * 101
        with self.assertRaises(RouteError) as caught:
            route.validate_request(request)
        self.assertEqual(caught.exception.code, "CLAUDE_REQUEST_INVALID")

    def test_configured_smaller_value_enforced_by_agy_route(self):
        model = load_route_config()["routes"]["agy"]["default_model"]
        route = AgyRoute({"executable": "agy", "max_prompt_bytes": 100})
        request = {
            "root": str(self.root),
            "model": model,
            "effort": "medium",
            "expected_branch": "main",
            "expected_head": "0" * 40,
            "write_paths": ["source.py"],
            "executable": "/usr/bin/agy",
            "log_path": "agy.log",
            "prompt": "x" * 100,
        }
        validated = route.validate_request(request)
        self.assertEqual(validated["prompt"], "x" * 100)

        request["prompt"] = "x" * 101
        with self.assertRaises(RouteError) as caught:
            route.validate_request(request)
        self.assertEqual(caught.exception.code, "AGY_REQUEST_INVALID")

    def test_drift_check_schema_accepts_max_prompt_bytes(self):
        schema = guidance_drift_check.DEVELOPER_ROUTE
        valid_entry = {
            "executable": "claude",
            "default_model": "claude-opus-5-5",
            "allowed_models": ["claude-opus-5-5"],
            "default_effort": "high",
            "default_timeout": 1800,
            "max_timeout": 3600,
            "max_prompt_bytes": 65536,
        }
        self.assertEqual(schema.errors(valid_entry, "route"), [])

        for bad in (0, -1, True, False, "65536"):
            with self.subTest(bad=bad):
                bad_entry = dict(valid_entry, max_prompt_bytes=bad)
                errors = schema.errors(bad_entry, "route")
                self.assertTrue(
                    any("max_prompt_bytes must be an integer >= 1" in err for err in errors),
                    errors,
                )

    def test_fresh_process_startup_with_agy_only_route_file(self):
        base_config = load_route_config()
        agy_only_config = {
            "schema_version": 1,
            "routes": {"agy": base_config["routes"]["agy"]},
        }
        if "roles" in base_config:
            agy_only_config["roles"] = base_config["roles"]
        agy_path = self.root / "agy_only_routes.json"
        agy_path.write_text(json.dumps(agy_only_config), encoding="utf-8")

        tools_llmtool = str(Path(__file__).resolve().parents[1])
        code = (
            "import sys\n"
            "from pathlib import Path\n"
            f"tools_llmtool = {tools_llmtool!r}\n"
            "if tools_llmtool not in sys.path:\n"
            "    sys.path.insert(0, tools_llmtool)\n"
            "from llmtool import developer_routes\n"
            "orig_load = developer_routes.load_route_config\n"
            f"substitute_path = Path({str(agy_path)!r})\n"
            "developer_routes.load_route_config = lambda path=None: orig_load(substitute_path if path is None else path)\n"
            "assert 'llmtool.cli' not in sys.modules\n"
            "assert 'llmtool.task_submission_cli' not in sys.modules\n"
            "from llmtool import cli, task_submission_cli\n"
            "try:\n"
            "    exit_code = cli.main(['--help'])\n"
            "except SystemExit as exc:\n"
            "    exit_code = exc.code\n"
            "if exit_code != 0:\n"
            "    sys.exit(exit_code)\n"
            "loaded = developer_routes.load_route_config()\n"
            "assert 'agy' in loaded['routes']\n"
            "assert 'claude' not in loaded['routes']\n"
            "assert developer_routes.resolve_route_max_prompt_bytes('agy') == 5242880\n"
            "try:\n"
            "    developer_routes.resolve_route_max_prompt_bytes('claude')\n"
            "    sys.exit(201)\n"
            "except developer_routes.RouteError as exc:\n"
            "    if exc.code != 'ROUTE_CONFIG_INVALID':\n"
            "        sys.exit(202)\n"
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
        self.assertIn("Read-only, provider-agnostic LLM CLI foundation.", proc.stdout)
        self.assertIn("{ask,", proc.stdout)
        self.assertIn("task", proc.stdout)

    def test_fresh_process_startup_with_malformed_route_file(self):
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
            "assert 'llmtool.cli' not in sys.modules\n"
            "assert 'llmtool.task_submission_cli' not in sys.modules\n"
            "from llmtool import cli, task_submission_cli\n"
            "try:\n"
            "    exit_code = cli.main(['--help'])\n"
            "except SystemExit as exc:\n"
            "    exit_code = exc.code\n"
            "if exit_code != 0:\n"
            "    sys.exit(exit_code)\n"
            "try:\n"
            "    developer_routes.load_route_config()\n"
            "    sys.exit(101)\n"
            "except developer_routes.RouteError as exc:\n"
            "    if exc.code != 'ROUTE_CONFIG_INVALID':\n"
            "        sys.exit(102)\n"
            "try:\n"
            "    developer_routes.resolve_route_max_prompt_bytes('agy')\n"
            "    sys.exit(103)\n"
            "except developer_routes.RouteError as exc:\n"
            "    if exc.code != 'ROUTE_CONFIG_INVALID':\n"
            "        sys.exit(104)\n"
            "try:\n"
            "    developer_routes.resolve_route_max_prompt_bytes('claude')\n"
            "    sys.exit(105)\n"
            "except developer_routes.RouteError as exc:\n"
            "    if exc.code != 'ROUTE_CONFIG_INVALID':\n"
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
        self.assertIn("Read-only, provider-agnostic LLM CLI foundation.", proc.stdout)
        self.assertIn("{ask,", proc.stdout)
        self.assertIn("task", proc.stdout)


if __name__ == "__main__":
    unittest.main()
