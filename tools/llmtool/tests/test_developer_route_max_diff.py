"""Tests for per-route max_diff_bytes (#1815)."""

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from llmtool import developer_routes, task_store
import llmtool.developer_routes.base
from llmtool.developer_routes import (
    RouteError,
    load_route_config,
    resolve_route_max_diff_bytes,
)

ROUTES_FILE = Path(__file__).resolve().parents[1] / 'developer-routes.json'
DRIFT_SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'guidance_drift_check.py'


class ResolveRouteMaxDiffTests(unittest.TestCase):
    def test_real_file_values(self):
        self.assertEqual(resolve_route_max_diff_bytes('agy'), 5242880)
        self.assertEqual(resolve_route_max_diff_bytes('claude'), 262144)
        self.assertEqual(resolve_route_max_diff_bytes('opencode'), 5242880)
        self.assertEqual(resolve_route_max_diff_bytes('gw-agent'), 65536)

    def test_injected_config_validation(self):
        base_cfg = load_route_config()
        for bad in (True, 1.5, 0, 1023, 5242880 + 1):
            with self.subTest(bad=bad):
                cfg = copy.deepcopy(base_cfg)
                cfg['routes']['agy']['max_diff_bytes'] = bad
                with self.assertRaises(RouteError) as caught:
                    resolve_route_max_diff_bytes('agy', config=cfg)
                self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')

        for ok in (1024, 512 * 1024, 5242880):
            with self.subTest(ok=ok):
                cfg = copy.deepcopy(base_cfg)
                cfg['routes']['agy']['max_diff_bytes'] = ok
                self.assertEqual(resolve_route_max_diff_bytes('agy', config=cfg), ok)

    def test_unknown_route_refused(self):
        with self.assertRaises(RouteError) as caught:
            resolve_route_max_diff_bytes('nope')
        self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')


class LoaderMaxDiffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'developer-routes.json'
        self.shipped = json.loads(ROUTES_FILE.read_text(encoding='utf-8'))

    def load_with(self, route, mutate):
        data = copy.deepcopy(self.shipped)
        mutate(data['routes'][route])
        self.path.write_text(json.dumps(data), encoding='utf-8')
        return load_route_config(self.path)

    def assert_invalid(self, route, mutate):
        with self.assertRaises(RouteError) as caught:
            self.load_with(route, mutate)
        self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')

    def test_temp_copy_invalid_and_key_removed(self):
        self.assert_invalid('agy', lambda entry: entry.update(max_diff_bytes='x'))
        loaded = self.load_with('agy', lambda entry: entry.pop('max_diff_bytes', None))
        self.assertNotIn('max_diff_bytes', loaded['routes']['agy'])


class GuidanceDriftSchemaTests(unittest.TestCase):
    def test_developer_routes_schema(self):
        spec = importlib.util.spec_from_file_location(
            'guidance_drift_check', DRIFT_SCRIPT)
        drift_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(drift_mod)

        with open(ROUTES_FILE, encoding='utf-8') as f:
            real_data = json.load(f)
        self.assertEqual(drift_mod.RULE_SCHEMAS['developer-routes.json'].errors(real_data, '$'), [])

        bad_data = copy.deepcopy(real_data)
        bad_data['routes']['agy']['max_diff_bytes'] = 1023
        self.assertEqual(len(drift_mod.RULE_SCHEMAS['developer-routes.json'].errors(bad_data, '$')), 1)

    def test_task_store_artifact_limit_matches_route_hard_limit_and_drift_schema(self):
        self.assertEqual(task_store.MAX_ARTIFACT_BYTES, developer_routes.base.HARD_MAX_DIFF_BYTES)
        spec = importlib.util.spec_from_file_location(
            'guidance_drift_check', DRIFT_SCRIPT)
        drift_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(drift_mod)

        with open(ROUTES_FILE, encoding='utf-8') as f:
            real_data = json.load(f)
        bad_data = copy.deepcopy(real_data)
        bad_data['routes']['agy']['max_diff_bytes'] = 5242880 + 1
        self.assertEqual(len(drift_mod.RULE_SCHEMAS['developer-routes.json'].errors(bad_data, '$')), 1)


if __name__ == '__main__':
    unittest.main()
