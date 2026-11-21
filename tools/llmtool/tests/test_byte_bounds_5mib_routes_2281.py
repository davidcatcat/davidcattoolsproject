"""#2281 (Owner 2026-11-21): every agy/opencode/claude route byte bound is 5 MiB."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from llmtool import task_submission_cli
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
from llmtool.developer_routes import agy as agy_route
from llmtool.developer_routes import claude as claude_route
from llmtool.developer_routes.base import DEFAULT_CLAUDE_MAX_PROMPT_BYTES
from llmtool.errors import LLMToolError
from llmtool.opencode_direct_developer import (
    DEFAULT_MAX_PROMPT_BYTES as OPENCODE_DEFAULT_PROMPT_BYTES,
    DEFAULT_MAX_SOURCE_FILE_BYTES as OPENCODE_DEFAULT_SOURCE_FILE_BYTES,
    DEFAULT_MAX_SOURCE_TOTAL_BYTES as OPENCODE_DEFAULT_SOURCE_TOTAL_BYTES,
    _read_scoped_file,
    _screen_prompt,
)

import test_agy_route_wiring as agy_tests

ROUTES_FILE = Path(__file__).resolve().parents[1] / "developer-routes.json"
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "guidance_drift_check.py"
SPEC = importlib.util.spec_from_file_location("guidance_drift_check_2281", SCRIPT)
guidance_drift_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guidance_drift_check)

BYTE_LIMIT = 5242880
OVER_LIMIT = BYTE_LIMIT + 1
ROUTE_BOUNDS = {
    "agy": ("max_diff_bytes", "max_prompt_bytes"),
    "opencode": (
        "max_diff_bytes",
        "max_prompt_bytes",
        "max_source_file_bytes",
        "max_source_total_bytes",
    ),
    "claude": ("max_diff_bytes", "max_prompt_bytes"),
}
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


def setUpModule():
    import review_trust_fixture
    import worker_policy_fixture

    review_trust_fixture.start_module()
    worker_policy_fixture.start_module()


def tearDownModule():
    import review_trust_fixture
    import worker_policy_fixture

    worker_policy_fixture.stop_module()
    review_trust_fixture.stop_module()


def _padding(size):
    return "x" * size


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

    def test_a_each_route_bound_reads_5_mib(self):
        config = load_route_config()
        for route, keys in ROUTE_BOUNDS.items():
            for key in keys:
                with self.subTest(route=route, key=key):
                    self.assertEqual(config["routes"][route][key], BYTE_LIMIT)
                    self.assertEqual(RESOLVERS[key](route), BYTE_LIMIT)
                    self.assertTrue(VALIDATORS[key](BYTE_LIMIT))
        self.assertEqual(DEFAULT_CLAUDE_MAX_PROMPT_BYTES, BYTE_LIMIT)
        self.assertEqual(claude_route.MAX_PROMPT_BYTES, BYTE_LIMIT)
        self.assertEqual(agy_route.MAX_PROMPT_BYTES, BYTE_LIMIT)
        self.assertEqual(OPENCODE_DEFAULT_PROMPT_BYTES, BYTE_LIMIT)
        self.assertEqual(OPENCODE_DEFAULT_SOURCE_FILE_BYTES, BYTE_LIMIT)
        self.assertEqual(OPENCODE_DEFAULT_SOURCE_TOTAL_BYTES, BYTE_LIMIT)
        self.assertEqual(
            guidance_drift_check.RULE_SCHEMAS["developer-routes.json"].errors(
                json.loads(ROUTES_FILE.read_text(encoding="utf-8")), "$"),
            [],
        )

    def test_b_5_mib_loads_and_5242881_is_refused(self):
        for key, validator in VALIDATORS.items():
            with self.subTest(key=key):
                self.assertTrue(validator(BYTE_LIMIT))
                self.assertFalse(validator(OVER_LIMIT))
        for route, keys in ROUTE_BOUNDS.items():
            for key in keys:
                with self.subTest(route=route, key=key, value=BYTE_LIMIT):
                    loaded = self._load_with(route, key, BYTE_LIMIT)
                    self.assertEqual(loaded["routes"][route][key], BYTE_LIMIT)
                with self.subTest(route=route, key=key, value=OVER_LIMIT):
                    with self.assertRaises(RouteError) as caught:
                        self._load_with(route, key, OVER_LIMIT)
                    self.assertEqual(caught.exception.code, "ROUTE_CONFIG_INVALID")
                    with self.assertRaises(RouteError) as caught:
                        RESOLVERS[key](
                            route, config={"routes": {route: {key: OVER_LIMIT}}}
                        )
                    self.assertEqual(caught.exception.code, "ROUTE_CONFIG_INVALID")

    def test_c_agy_goal_over_old_shared_bound_is_delegated(self):
        old_shared_bound = 64 * 1024
        goal = "AGY goal" + _padding(old_shared_bound + 1)
        case = agy_tests.AgyRouteWiringTests(
            methodName="test_record_pm_accepts_agy_recommendation")
        status, task, _events, prompts = case._run_agy_delegate(goal=goal)
        self.assertEqual(status, 0)
        self.assertEqual(task["developer"]["state"], "DEVELOPER_RESPONDED")
        self.assertEqual(len(prompts), 1)
        self.assertGreater(len(prompts[0].encode("utf-8")), old_shared_bound)
        self.assertIn(goal, prompts[0])

    def test_d_claude_reply_over_1_mib_passes_reply_screen(self):
        old_reply_screen_limit = 1024 * 1024
        reply = _padding(old_reply_screen_limit + 1)
        reported = task_submission_cli._claude_no_change_reply({"result": reply})
        self.assertIsNotNone(reported)
        self.assertNotEqual(reported["summary"], task_submission_cli.CLAUDE_REPLY_WITHHELD)
        self.assertTrue(reported["summary"].endswith(task_submission_cli.CLAUDE_REPLY_TRUNCATED))
        self.assertLessEqual(
            task_submission_cli._reported_size(reported),
            task_submission_cli.CLAUDE_REPLY_MAX_REPORTED_BYTES,
        )
        self.assertEqual(task_submission_cli._CLAUDE_REPLY_SCREEN_LIMIT, BYTE_LIMIT)

    def test_e_opencode_source_and_prompt_over_old_bounds_pass(self):
        old_source_bound = 1024 * 1024
        old_prompt_bound = 64 * 1024
        (self.root / "big.py").write_bytes(b"x" * (old_source_bound + 1))
        captured = _read_scoped_file(self.root, "big.py")
        self.assertIsNotNone(captured)
        self.assertEqual(captured[1], old_source_bound + 1)
        payload = _screen_prompt(_padding(old_prompt_bound + 1))
        self.assertEqual(len(payload), old_prompt_bound + 1)

    def test_f_agy_whole_prompt_refuses_only_above_5_mib(self):
        case = agy_tests.AgyRouteWiringTests(
            methodName="test_record_pm_accepts_agy_recommendation")

        def run(extra):
            status, _task, _events, prompts = case._run_agy_delegate(
                goal="AGY goal" + _padding(extra))
            self.assertEqual(status, 0)
            return prompts[0]

        limit = resolve_route_max_prompt_bytes("agy")
        self.assertEqual(limit, BYTE_LIMIT)
        room = limit - len(run(0).encode("utf-8"))
        self.assertGreater(room, 0)
        fitted = run(room)
        self.assertEqual(len(fitted.encode("utf-8")), limit)
        with self.assertRaisesRegex(LLMToolError, "AGY_SKILL_PROMPT_TOO_LARGE"):
            case._run_agy_delegate(goal="AGY goal" + _padding(room + 1))


if __name__ == "__main__":
    unittest.main()
