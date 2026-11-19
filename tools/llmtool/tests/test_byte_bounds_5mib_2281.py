"""#2281 (Owner 2026-11-19, relayed c7003366463): Developer byte bounds are 5 MiB.

Each test shows that a value just over the old bound is now accepted and that a
value over 5 MiB is still refused with the existing code. The TaskStore artifact
cases are in test_task_store.py (just over 512 KiB, over 5 MiB) and
test_task_store_start_failures.py (exactly MAX_ARTIFACT_BYTES next to a repair
packet).
"""

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from llmtool import opencode_direct_developer, task_store
from llmtool.developer_routes import (
    AgyRoute,
    ClaudeRoute,
    HARD_MAX_ROUTE_BYTES,
    RouteError,
    load_route_config,
    max_diff_bytes_ok,
    max_prompt_bytes_ok,
    max_source_file_bytes_ok,
    max_source_total_bytes_ok,
    max_tool_result_bytes_ok,
    resolve_route_max_diff_bytes,
    resolve_route_max_prompt_bytes,
    resolve_route_max_source_file_bytes,
    resolve_route_max_source_total_bytes,
)
from llmtool.developer_routes import agy as agy_route
from llmtool.developer_routes import base
from llmtool.developer_routes import gw_agent as gw_agent_route
from llmtool.opencode_direct_developer import (
    DirectOpenCodeError,
    _read_scoped_file,
    _screen_prompt,
    _screen_sources,
)

FIVE_MIB = 5 * 1024 * 1024
ROUTES_FILE = Path(__file__).resolve().parents[1] / 'developer-routes.json'
DRIFT_SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'guidance_drift_check.py'
ALL_ROUTES = ('agy', 'claude', 'gw-agent', 'opencode')


class ShippedBoundsTests(unittest.TestCase):
    def test_route_file_values_are_5_mib(self):
        routes = json.loads(ROUTES_FILE.read_text(encoding='utf-8'))['routes']
        # Review F1/F3: Claude and gw-agent are 5 MiB too; no route keeps a lower bound.
        for route in ALL_ROUTES:
            for key in ('max_prompt_bytes', 'max_diff_bytes'):
                with self.subTest(route=route, key=key):
                    self.assertEqual(routes[route][key], FIVE_MIB)
        for key in ('max_source_file_bytes', 'max_source_total_bytes'):
            with self.subTest(key=key):
                self.assertEqual(routes['opencode'][key], FIVE_MIB)
        self.assertEqual(routes['gw-agent']['budgets']['max_tool_result_bytes'], FIVE_MIB)

    def test_resolvers_return_5_mib(self):
        for route in ALL_ROUTES:
            with self.subTest(route=route):
                self.assertEqual(resolve_route_max_prompt_bytes(route), FIVE_MIB)
                self.assertEqual(resolve_route_max_diff_bytes(route), FIVE_MIB)
        self.assertEqual(resolve_route_max_source_file_bytes('opencode'), FIVE_MIB)
        self.assertEqual(resolve_route_max_source_total_bytes('opencode'), FIVE_MIB)

    def test_defaults_mirror_route_values(self):
        self.assertEqual(HARD_MAX_ROUTE_BYTES, FIVE_MIB)
        self.assertEqual(base.DEFAULT_MAX_DIFF_BYTES, FIVE_MIB)
        self.assertEqual(base.DEFAULT_CLAUDE_MAX_PROMPT_BYTES, FIVE_MIB)
        self.assertEqual(base.DEFAULT_GW_AGENT_MAX_PROMPT_BYTES, FIVE_MIB)
        self.assertEqual(base.HARD_MAX_TOOL_RESULT_BYTES, FIVE_MIB)
        self.assertEqual(gw_agent_route._GATEWAY_BUDGETS['max_tool_result_bytes'],
                         (256, FIVE_MIB))
        self.assertEqual(task_store.MAX_DEVELOPER_INPUT_BYTES, FIVE_MIB)
        self.assertEqual(base.DEFAULT_AGY_MAX_PROMPT_BYTES, FIVE_MIB)
        self.assertEqual(base.DEFAULT_OPENCODE_MAX_PROMPT_BYTES, FIVE_MIB)
        self.assertEqual(base.DEFAULT_OPENCODE_MAX_SOURCE_FILE_BYTES, FIVE_MIB)
        self.assertEqual(base.DEFAULT_OPENCODE_MAX_SOURCE_TOTAL_BYTES, FIVE_MIB)
        self.assertEqual(agy_route.MAX_PROMPT_BYTES, FIVE_MIB)
        self.assertEqual(opencode_direct_developer.DEFAULT_MAX_PROMPT_BYTES, FIVE_MIB)
        self.assertEqual(opencode_direct_developer.MAX_PROMPT_BYTES, FIVE_MIB)
        self.assertEqual(opencode_direct_developer.DEFAULT_MAX_SOURCE_FILE_BYTES, FIVE_MIB)
        self.assertEqual(opencode_direct_developer.MAX_SOURCE_FILE_BYTES, FIVE_MIB)
        self.assertEqual(opencode_direct_developer.DEFAULT_MAX_SOURCE_TOTAL_BYTES, FIVE_MIB)
        self.assertEqual(opencode_direct_developer.MAX_SOURCE_TOTAL_BYTES, FIVE_MIB)

    def test_artifact_limit_reaches_the_task_record(self):
        # The hard diff cap is the TaskStore artifact limit, and a whole 5 MiB
        # artifact fits in a record (the record limit keeps the 2x ratio).
        self.assertEqual(task_store.MAX_ARTIFACT_BYTES, FIVE_MIB)
        self.assertEqual(base.HARD_MAX_DIFF_BYTES, task_store.MAX_ARTIFACT_BYTES)
        self.assertGreaterEqual(task_store.MAX_RECORD_BYTES, 2 * task_store.MAX_ARTIFACT_BYTES)
        self.assertEqual(task_store.MAX_EXECUTION_SOURCE_BYTES,
                         resolve_route_max_source_total_bytes('opencode'))

    def test_drift_check_ceiling_matches_route_ceiling(self):
        spec = importlib.util.spec_from_file_location('guidance_drift_check', DRIFT_SCRIPT)
        drift = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(drift)
        self.assertEqual(drift.ROUTE_MAX_BYTES, HARD_MAX_ROUTE_BYTES)


class ValidatorCeilingTests(unittest.TestCase):
    """Security repair (Codex security v91, CWE-400): every byte-bound validator
    fails closed above 5 MiB instead of accepting any positive integer."""

    def test_each_validator_accepts_5_mib_and_refuses_more(self):
        for validator in (max_diff_bytes_ok, max_prompt_bytes_ok, max_source_file_bytes_ok,
                          max_source_total_bytes_ok, max_tool_result_bytes_ok):
            with self.subTest(validator=validator.__name__):
                self.assertTrue(validator(FIVE_MIB))
                self.assertFalse(validator(FIVE_MIB + 1))
                self.assertFalse(validator(10 * FIVE_MIB))

    def test_loader_refuses_each_byte_bound_over_5_mib(self):
        shipped = json.loads(ROUTES_FILE.read_text(encoding='utf-8'))
        cases = [(route, key) for route in ALL_ROUTES
                 for key in ('max_prompt_bytes', 'max_diff_bytes')]
        cases += [('opencode', 'max_source_file_bytes'), ('opencode', 'max_source_total_bytes')]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'developer-routes.json'
            for route, key in cases:
                with self.subTest(route=route, key=key):
                    data = copy.deepcopy(shipped)
                    data['routes'][route][key] = FIVE_MIB + 1
                    path.write_text(json.dumps(data), encoding='utf-8')
                    with self.assertRaises(RouteError) as caught:
                        load_route_config(path)
                    self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')
            data = copy.deepcopy(shipped)
            data['routes']['gw-agent']['budgets']['max_tool_result_bytes'] = FIVE_MIB + 1
            path.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaises(RouteError) as caught:
                load_route_config(path)
            self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')

    def test_adapter_supplied_values_over_5_mib_are_refused(self):
        # An adapter config is checked like the route file; it can never widen a bound.
        cases = (
            (resolve_route_max_prompt_bytes, 'max_prompt_bytes', AgyRoute, 'agy'),
            (resolve_route_max_prompt_bytes, 'max_prompt_bytes', ClaudeRoute, 'claude'),
            (resolve_route_max_source_file_bytes, 'max_source_file_bytes', AgyRoute, 'agy'),
            (resolve_route_max_source_total_bytes, 'max_source_total_bytes', AgyRoute, 'agy'),
        )
        for resolve, key, adapter_type, executable in cases:
            with self.subTest(key=key, adapter=executable):
                adapter = adapter_type({'executable': executable, key: FIVE_MIB + 1})
                with self.assertRaises(RouteError) as caught:
                    resolve(adapter)
                self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')
                adapter = adapter_type({'executable': executable, key: FIVE_MIB})
                self.assertEqual(resolve(adapter), FIVE_MIB)

    def test_agy_adapter_over_5_mib_does_not_widen_its_prompt_check(self):
        route = AgyRoute({'executable': 'agy', 'max_prompt_bytes': FIVE_MIB + 1})
        with tempfile.TemporaryDirectory() as temp:
            request = {
                'root': temp,
                'model': load_route_config()['routes']['agy']['default_model'],
                'effort': 'medium', 'expected_branch': 'main', 'expected_head': '0' * 40,
                'write_paths': ['source.py'], 'executable': '/usr/bin/agy',
                'log_path': 'agy.log', 'prompt': 'x' * (FIVE_MIB + 1),
            }
            with self.assertRaises(RouteError) as caught:
                route.validate_request(request)
        self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')


class LoaderDiffBoundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'developer-routes.json'
        self.shipped = json.loads(ROUTES_FILE.read_text(encoding='utf-8'))

    def _load(self, route, value):
        data = copy.deepcopy(self.shipped)
        data['routes'][route]['max_diff_bytes'] = value
        self.path.write_text(json.dumps(data), encoding='utf-8')
        return load_route_config(self.path)

    def test_diff_bound_just_over_old_and_5_mib_load(self):
        for route in ('agy', 'opencode'):
            for value in (512 * 1024 + 1, FIVE_MIB):
                with self.subTest(route=route, value=value):
                    loaded = self._load(route, value)
                    self.assertEqual(resolve_route_max_diff_bytes(route, config=loaded), value)

    def test_diff_bound_over_5_mib_is_refused(self):
        with self.assertRaises(RouteError) as caught:
            self._load('agy', FIVE_MIB + 1)
        self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')
        with self.assertRaises(RouteError) as caught:
            resolve_route_max_diff_bytes(
                'opencode', config={'routes': {'opencode': {'max_diff_bytes': FIVE_MIB + 1}}})
        self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')


class OpenCodeBoundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_source_file_just_over_old_bound_is_accepted(self):
        size = 1024 * 1024 + 1
        (self.root / 'source.py').write_bytes(b'x' * size)
        captured = _read_scoped_file(self.root, 'source.py')
        self.assertIsNotNone(captured)
        self.assertEqual(captured[1], size)

    def test_source_file_over_5_mib_is_refused(self):
        (self.root / 'source.py').write_bytes(b'x' * (FIVE_MIB + 1))
        with self.assertRaises(DirectOpenCodeError) as caught:
            _read_scoped_file(self.root, 'source.py')
        self.assertEqual(caught.exception.direct_opencode_code, 'OPENCODE_SOURCE_TOO_LARGE')

    def test_source_total_just_over_old_bound_is_accepted(self):
        (self.root / 'one.py').write_bytes(b'x' * (1024 * 1024))
        (self.root / 'two.py').write_bytes(b'x' * (1024 * 1024 + 1))
        total, count, _digest, _entries = _screen_sources(self.root, ['one.py', 'two.py'])
        self.assertEqual(total, 2 * 1024 * 1024 + 1)
        self.assertEqual(count, 2)

    def test_source_total_over_5_mib_is_refused(self):
        # Each file is under the 5 MiB file bound; together they exceed 5 MiB.
        (self.root / 'one.py').write_bytes(b'x' * (3 * 1024 * 1024))
        (self.root / 'two.py').write_bytes(b'x' * (2 * 1024 * 1024 + 1))
        with self.assertRaises(DirectOpenCodeError) as caught:
            _screen_sources(self.root, ['one.py', 'two.py'])
        self.assertEqual(caught.exception.direct_opencode_code, 'OPENCODE_SOURCE_TOO_LARGE')

    def test_prompt_just_over_old_bound_is_accepted(self):
        prompt = 'x' * (64 * 1024 + 1)
        self.assertEqual(_screen_prompt(prompt), prompt.encode('utf-8'))

    def test_prompt_over_5_mib_is_refused(self):
        with self.assertRaises(DirectOpenCodeError) as caught:
            _screen_prompt('x' * (FIVE_MIB + 1))
        self.assertEqual(caught.exception.direct_opencode_code, 'OPENCODE_PROMPT_TOO_LARGE')


class AgyPromptBoundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _request(self, prompt):
        return {
            'root': str(self.root),
            'model': load_route_config()['routes']['agy']['default_model'],
            'effort': 'medium',
            'expected_branch': 'main',
            'expected_head': '0' * 40,
            'write_paths': ['source.py'],
            'executable': '/usr/bin/agy',
            'log_path': 'agy.log',
            'prompt': prompt,
        }

    def test_prompt_just_over_old_bound_is_accepted(self):
        # No max_prompt_bytes in the adapter config: the shipped route value applies.
        route = AgyRoute({'executable': 'agy'})
        prompt = 'x' * (512 * 1024 + 1)
        self.assertEqual(route.validate_request(self._request(prompt))['prompt'], prompt)

    def test_prompt_over_5_mib_is_refused(self):
        route = AgyRoute({'executable': 'agy'})
        with self.assertRaises(RouteError) as caught:
            route.validate_request(self._request('x' * (FIVE_MIB + 1)))
        self.assertEqual(caught.exception.code, 'AGY_REQUEST_INVALID')


if __name__ == '__main__':
    unittest.main()
