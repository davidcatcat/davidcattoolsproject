"""#2157: optional per-route max_task_paths in developer-routes.json, not wired yet."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llmtool import developer_routes as dr  # noqa: E402
from llmtool.developer_routes import base  # noqa: E402

CONFIG = ROOT / 'developer-routes.json'
ROUTES = ('agy', 'claude', 'gw-agent', 'opencode')


def config_with(route, value):
    data = json.loads(CONFIG.read_text(encoding='utf-8'))
    data['routes'][route]['max_task_paths'] = value
    return data


class MaxTaskPathsTests(unittest.TestCase):
    def load(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'developer-routes.json'
            path.write_text(json.dumps(data), encoding='utf-8')
            return dr.load_route_config(path)

    def test_default_is_32_for_every_registered_route(self):
        self.assertEqual((base.DEFAULT_MAX_TASK_PATHS, base.HARD_MAX_TASK_PATHS), (32, 32))
        config = dr.load_route_config()
        for route in ROUTES:
            with self.subTest(route=route):
                self.assertEqual(dr.resolve_route_max_task_paths(route, config=config), 32)

    def test_explicit_32_is_accepted_for_that_route(self):
        config = self.load(config_with('agy', 32))
        self.assertEqual(dr.resolve_route_max_task_paths('agy', config=config), 32)
        self.assertEqual(dr.resolve_route_max_task_paths('claude', config=config), 32)

    def test_values_other_than_32_fail_closed_at_load_until_wired(self):
        for value in (0, -1, 1, 8, 31, 33, True, 32.0, '32', None):
            with self.subTest(value=repr(value)):
                with self.assertRaises(dr.RouteError) as caught:
                    self.load(config_with('gw-agent', value))
                self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')

    def test_resolver_refuses_bad_values_and_unknown_routes(self):
        for value in (33, 0, 8, False, '8'):
            with self.subTest(value=repr(value)):
                config = dict(routes=dict(agy=dict(max_task_paths=value)))
                with self.assertRaises(dr.RouteError):
                    dr.resolve_route_max_task_paths('agy', config=config)
        for route in ('codex', None):
            with self.subTest(route=route):
                with self.assertRaises(dr.RouteError):
                    dr.resolve_route_max_task_paths(route, config=dict(routes=dict()))

    def test_developer_route_instances_resolve_by_name(self):
        empty = dict(routes=dict())
        good = dict(routes=dict(agy=dict(max_task_paths=32)))
        bad = dict(routes=dict(agy=dict(max_task_paths=8)))
        self.assertEqual(dr.resolve_route_max_task_paths(dr.AgyRoute({}), config=good), 32)
        self.assertEqual(dr.resolve_route_max_task_paths(dr.ClaudeRoute({}), config=empty), 32)
        with self.assertRaises(dr.RouteError):
            dr.resolve_route_max_task_paths(dr.AgyRoute({}), config=bad)

if __name__ == '__main__':
    unittest.main()
