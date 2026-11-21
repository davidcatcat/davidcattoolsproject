"""#2281 (Owner 2026-11-21): every agy/opencode route byte bound is 5 MiB."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from llmtool.developer_routes import (
    RouteError,
    load_route_config,
    max_diff_bytes_ok,
    max_prompt_bytes_ok,
    max_source_file_bytes_ok,
    max_source_total_bytes_ok,
    resolve_route_max_diff_bytes,
    resolve_route_max_prompt_bytes,
    resolve_route_max_source_file_bytes,
    resolve_route_max_source_total_bytes,
)
from llmtool.developer_routes.base import (
    DEFAULT_CLAUDE_MAX_PROMPT_BYTES,
    DEFAULT_MAX_DIFF_BYTES,
)
from llmtool.opencode_direct_developer import _read_scoped_file, _screen_prompt

ROUTES_FILE = Path(__file__).resolve().parents[1] / "developer-routes.json"
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "guidance_drift_check.py"
SPEC = importlib.util.spec_from_file_location("guidance_drift_check_2281", SCRIPT)
guidance_drift_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guidance_drift_check)

BYTE_LIMIT = 5242880
OVER_LIMIT = BYTE_LIMIT + 1
AGY_BOUNDS = ("max_diff_bytes", "max_prompt_bytes")
OPENCODE_BOUNDS = (
    "max_diff_bytes",
    "max_prompt_bytes",
    "max_source_file_bytes",
    "max_source_total_bytes",
)
RESOLVERS = {
    "max_diff_bytes": resolve_route_max_diff_bytes,
    "max_prompt_bytes": resolve_route_max_prompt_bytes,
    "max_source_file_bytes": resolve_route_max_source_file_bytes,
    "max_source_total_bytes": resolve_route_max_source_total_bytes,
}
VALIDATORS = {
    "max_diff_bytes": max_diff_bytes_ok,
    "max_prompt_bytes": max_prompt_bytes_ok,
    "max_source_file_bytes": max_source_file_bytes_ok,
    "max_source_total_bytes": max_source_total_bytes_ok,
}


class ByteBounds5MiBRoutes2281Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _load_with(self, route, key, value):
        data = json.loads(ROUTES_FILE.read_text(encoding="utf-8"))
        data["routes"][route][key] = value
        path = self.root / "developer-routes.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return load_route_config(path)

    def test_a_repository_bounds_are_5_mib(self):
        config = load_route_config()
        for route, keys in (("agy", AGY_BOUNDS), ("opencode", OPENCODE_BOUNDS)):
            for key in keys:
                with self.subTest(route=route, key=key):
                    self.assertEqual(config["routes"][route][key], BYTE_LIMIT)
                    self.assertEqual(RESOLVERS[key](route), BYTE_LIMIT)

    def test_b_5_mib_value_loads(self):
        for key, validator in VALIDATORS.items():
            with self.subTest(key=key):
                self.assertTrue(validator(BYTE_LIMIT))
        for route, keys in (("agy", AGY_BOUNDS), ("opencode", OPENCODE_BOUNDS)):
            for key in keys:
                with self.subTest(route=route, key=key):
                    loaded = self._load_with(route, key, BYTE_LIMIT)
                    self.assertEqual(loaded["routes"][route][key], BYTE_LIMIT)

    def test_c_5242881_is_refused(self):
        for key, validator in VALIDATORS.items():
            with self.subTest(key=key):
                self.assertFalse(validator(OVER_LIMIT))
        for route, keys in (("agy", AGY_BOUNDS), ("opencode", OPENCODE_BOUNDS)):
            for key in keys:
                with self.subTest(route=route, key=key):
                    with self.assertRaises(RouteError) as caught:
                        self._load_with(route, key, OVER_LIMIT)
                    self.assertEqual(caught.exception.code, "ROUTE_CONFIG_INVALID")
                    with self.assertRaises(RouteError) as caught:
                        RESOLVERS[key](
                            route, config={"routes": {route: {key: OVER_LIMIT}}}
                        )
                    self.assertEqual(caught.exception.code, "ROUTE_CONFIG_INVALID")

    def test_d_opencode_accepts_just_over_the_old_bound(self):
        old_source_bound = 1024 * 1024
        old_prompt_bound = 64 * 1024
        (self.root / "big.py").write_bytes(b"x" * (old_source_bound + 1))
        captured = _read_scoped_file(self.root, "big.py")
        self.assertIsNotNone(captured)
        self.assertEqual(captured[1], old_source_bound + 1)
        payload = _screen_prompt("x" * (old_prompt_bound + 1))
        self.assertEqual(len(payload), old_prompt_bound + 1)

    def test_e_guidance_drift_check_accepts_the_new_values(self):
        shipped = json.loads(ROUTES_FILE.read_text(encoding="utf-8"))
        self.assertEqual(
            guidance_drift_check.RULE_SCHEMAS["developer-routes.json"].errors(shipped, "$"),
            [],
        )
        entry = {
            "executable": "opencode",
            "default_model": "opencode/deepseek-v4.1-flash-free",
            "default_effort": "high",
            "default_timeout": 10800,
            "max_timeout": 18000,
            "max_diff_bytes": BYTE_LIMIT,
            "max_prompt_bytes": BYTE_LIMIT,
            "max_source_file_bytes": BYTE_LIMIT,
            "max_source_total_bytes": BYTE_LIMIT,
        }
        self.assertEqual(guidance_drift_check.DEVELOPER_ROUTE.errors(entry, "route"), [])
        over = dict(entry, max_source_total_bytes=OVER_LIMIT)
        self.assertTrue(guidance_drift_check.DEVELOPER_ROUTE.errors(over, "route"))

    def test_f_claude_bounds_are_unchanged(self):
        claude = load_route_config()["routes"]["claude"]
        self.assertEqual(claude["max_diff_bytes"], 262144)
        self.assertEqual(claude["max_prompt_bytes"], 65536)
        self.assertEqual(resolve_route_max_diff_bytes("claude"), 262144)
        self.assertEqual(resolve_route_max_prompt_bytes("claude"), 65536)
        without_fields = {"routes": {"claude": {}}}
        self.assertEqual(
            resolve_route_max_prompt_bytes("claude", config=without_fields),
            DEFAULT_CLAUDE_MAX_PROMPT_BYTES,
        )
        self.assertEqual(
            resolve_route_max_diff_bytes("claude", config=without_fields),
            DEFAULT_MAX_DIFF_BYTES,
        )


if __name__ == "__main__":
    unittest.main()
