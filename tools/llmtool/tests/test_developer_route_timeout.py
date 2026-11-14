"""Per-route default_timeout and max_timeout in developer-routes.json."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from llmtool import developer_routes, task_completion
from llmtool.developer_routes import RouteError, load_route_config, resolve_route_timeout
from llmtool.developer_routes.base import DeveloperRoute, run_route

ROUTES_FILE = Path(__file__).resolve().parents[1] / 'developer-routes.json'
ROUTE_NAMES = ('agy', 'claude', 'gw-agent', 'opencode')
LONG_ROUTES = ('claude', 'opencode')
SHORT_ROUTES = ('agy', 'gw-agent')
EXPECTED_LIMITS = {'agy': (600, 600), 'claude': (600, 1800),
                   'gw-agent': (600, 600), 'opencode': (600, 1800)}
NOT_INT = ('600', 600.0, 1.5, True, False, [600], {'s': 600}, b'600')
HARD_CAP = developer_routes.HARD_TIMEOUT_CAP_SECONDS


class NamedRoute(DeveloperRoute):
    """Minimal adapter; its name selects the registry entry."""
    worker_key = 'fake'
    family = 'test-family'

    def __init__(self, name):
        super().__init__({})
        self._name = name

    @property
    def name(self):
        return self._name

    def validate_profile(self, model, effort):
        return model, effort

    def build_argv(self, executable, request):
        return [executable, 'run']

    def build_env(self, tmpdir):
        return {'TMPDIR': tmpdir}

    def parse_output(self, stdout, stderr, log_text):
        return {'status': 'DONE', 'summary': 'ok', 'session_id': 'session_1234'}

    def verified_model(self, log_text, requested):
        return requested

    def quota_exhausted(self, parsed, stderr):
        return False


class FakeBoundary:
    def prepare(self, root, write_paths, state_dirs=()):
        return ['/boundary'], 'a' * 64

    def verify(self, root, write_paths, state_dirs=(), *, runner):
        return {'verified': True, 'policy_sha256': 'a' * 64, 'probes': []}


class ShippedTimeoutTests(unittest.TestCase):
    def test_every_route_has_its_limits(self):
        shipped = json.loads(ROUTES_FILE.read_text(encoding='utf-8'))
        config = load_route_config()
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                default, maximum = EXPECTED_LIMITS[route]
                for source in (shipped['routes'][route], config['routes'][route]):
                    self.assertEqual(source['default_timeout'], default)
                    self.assertEqual(source['max_timeout'], maximum)

    def test_constants_and_exports(self):
        self.assertEqual(developer_routes.DEFAULT_TIMEOUT_SECONDS, 600)
        self.assertEqual(developer_routes.HARD_TIMEOUT_CAP_SECONDS, 1800)
        self.assertIn('resolve_route_timeout', developer_routes.__all__)


class ResolveRouteTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.config = load_route_config()

    def assert_refused(self, route, requested, code, config=None):
        with self.assertRaises(RouteError) as caught:
            resolve_route_timeout(route, requested,
                                  config=self.config if config is None else config)
        self.assertEqual(caught.exception.code, code)

    def test_every_route_defaults_to_600(self):
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                self.assertEqual(resolve_route_timeout(route, config=self.config), 600)
                self.assertEqual(resolve_route_timeout(route), 600)

    def test_long_routes_accept_up_to_1800(self):
        for route in LONG_ROUTES:
            for requested in (1, 600, 601, 1200, 1800):
                with self.subTest(route=route, requested=requested):
                    self.assertEqual(
                        resolve_route_timeout(route, requested, config=self.config), requested)

    def test_short_routes_refuse_above_600(self):
        for route in SHORT_ROUTES:
            for requested in (1, 600):
                with self.subTest(route=route, requested=requested):
                    self.assertEqual(
                        resolve_route_timeout(route, requested, config=self.config), requested)
            for requested in (601, 1200, 1800):
                with self.subTest(route=route, requested=requested):
                    self.assert_refused(route, requested, 'TIMEOUT_OVER_ROUTE_MAX')

    def test_above_hard_cap_refused_for_every_route(self):
        for route in ROUTE_NAMES:
            for requested in (1801, 3600, 10 ** 9):
                with self.subTest(route=route, requested=requested):
                    self.assert_refused(route, requested, 'TIMEOUT_OVER_HARD_CAP')

    def test_non_int_zero_negative_and_bool_refused(self):
        for route in ROUTE_NAMES:
            for requested in NOT_INT + (0, -1, -600):
                with self.subTest(route=route, requested=requested):
                    self.assert_refused(route, requested, 'TIMEOUT_INVALID')

    def test_unknown_route_refused(self):
        for route in ('codex', 'FAKE', '', None, 7):
            with self.subTest(route=route):
                self.assert_refused(route, None, 'ROUTE_CONFIG_INVALID')

    def test_hand_built_config_limits_are_checked(self):
        for limits in ({'default_timeout': 700, 'max_timeout': 600},
                       {'default_timeout': 600, 'max_timeout': 1801},
                       {'default_timeout': True, 'max_timeout': 600},
                       {'default_timeout': 600}):
            with self.subTest(limits=limits):
                config = {'routes': {'claude': limits}}
                self.assert_refused('claude', None, 'ROUTE_CONFIG_INVALID', config=config)

    def test_route_without_limits_keeps_600_baseline(self):
        config = copy.deepcopy(self.config)
        for key in ('default_timeout', 'max_timeout'):
            del config['routes']['claude'][key]
        self.assertEqual(resolve_route_timeout('claude', config=config), 600)
        self.assertEqual(resolve_route_timeout('claude', 600, config=config), 600)
        self.assert_refused('claude', 601, 'TIMEOUT_OVER_ROUTE_MAX', config=config)


class LoaderTimeoutTests(unittest.TestCase):
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

    def test_default_above_max_refused(self):
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                self.assert_invalid(route, lambda entry: entry.update(
                    default_timeout=entry['max_timeout'] + 1))

    def test_max_above_hard_cap_refused(self):
        for route in ROUTE_NAMES:
            for maximum in (1801, 3600):
                with self.subTest(route=route, maximum=maximum):
                    self.assert_invalid(route, lambda entry, maximum=maximum: entry.update(
                        max_timeout=maximum))

    def bad_limit_values(self, route, key):
        """Bad values for one key; each breaks the 1 <= default <= max <= 1800 rule."""
        entry = self.shipped['routes'][route]
        values = NOT_INT + (None, 0, -1, -600, HARD_CAP + 1, 3600)
        if key == 'default_timeout':
            values += (entry['max_timeout'] + 1,)
        else:
            values += (entry['default_timeout'] - 1,)
        return values

    def assert_resolver_refuses(self, route, key, value):
        config = copy.deepcopy(load_route_config())
        config['routes'][route][key] = value
        with self.assertRaises(RouteError) as caught:
            resolve_route_timeout(route, config=config)
        self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')

    def test_bad_values_refused(self):
        for route in ROUTE_NAMES:
            for key in ('default_timeout', 'max_timeout'):
                for value in self.bad_limit_values(route, key):
                    with self.subTest(route=route, key=key, value=value):
                        # The resolver refuses every bad value in a hand-built config.
                        self.assert_resolver_refuses(route, key, value)
                        try:
                            json.dumps(value)
                        except TypeError:
                            continue  # not JSON (bytes); covered by the resolver only
                        data = copy.deepcopy(self.shipped)
                        data['routes'][route][key] = value
                        self.path.write_text(json.dumps(data), encoding='utf-8')
                        written = json.loads(self.path.read_text(encoding='utf-8'))
                        # The file is valid JSON and still holds the bad value.
                        self.assertIs(type(written['routes'][route][key]), type(value))
                        self.assertEqual(written['routes'][route][key], value)
                        with self.assertRaises(RouteError) as caught:
                            load_route_config(self.path)
                        self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')

    def test_only_one_key_refused(self):
        for route in ROUTE_NAMES:
            for key in ('default_timeout', 'max_timeout'):
                with self.subTest(route=route, key=key):
                    self.assert_invalid(route, lambda entry, key=key: entry.pop(key))

    def test_valid_limits_load(self):
        for route in ROUTE_NAMES:
            for default, maximum in ((1, 1), (600, 1800), (1800, 1800)):
                with self.subTest(route=route, default=default, maximum=maximum):
                    entry = self.load_with(route, lambda entry, d=default, m=maximum:
                                           entry.update(default_timeout=d,
                                                        max_timeout=m))['routes'][route]
                    self.assertEqual((entry['default_timeout'], entry['max_timeout']),
                                     (default, maximum))

    def test_route_without_limits_still_loads(self):
        def drop(entry):
            del entry['default_timeout']
            del entry['max_timeout']
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                entry = self.load_with(route, drop)['routes'][route]
                self.assertNotIn('default_timeout', entry)
                self.assertNotIn('max_timeout', entry)


class RunRouteTimeoutTests(unittest.TestCase):
    """base.py takes its timeout from the registry, not a fixed 600."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.request = {'root': self.temp.name, 'model': 'fake-model', 'effort': 'low',
                        'expected_branch': 'task', 'expected_head': 'a' * 40,
                        'write_paths': ['out.txt'], 'executable': '/fake/cli'}
        self.git = task_completion.GitSnapshot('/root', '/git', 'task', 'a' * 40, True)
        self.snapshot = {'files': {}, 'git_state': {'head': 'a' * 40}}

    def run_with(self, route, **extra):
        calls = []

        def runner(*args):
            calls.append(args)
            return 0, b'output', b''

        with mock.patch('llmtool.developer_routes.base.task_completion.collect_git_snapshot',
                        return_value=self.git):
            with mock.patch('llmtool.developer_routes.base._git_status_paths', return_value=()):
                run_route(route, dict(self.request, **extra), gate=lambda: True,
                          runner=runner, boundary=FakeBoundary(),
                          snapshot=lambda root: self.snapshot)
        self.assertEqual(len(calls), 1)
        return calls[0][3]

    def assert_request_refused(self, route, timeout, refusal):
        calls = []
        with self.assertRaises(RouteError) as caught:
            run_route(route, dict(self.request, timeout=timeout), gate=lambda: True,
                      runner=lambda *args: calls.append(args), boundary=FakeBoundary(),
                      snapshot=lambda root: self.snapshot)
        self.assertEqual(caught.exception.code, route.name.upper() + '_REQUEST_INVALID')
        self.assertEqual(caught.exception.facts, {'timeout_refusal': refusal})
        self.assertEqual(calls, [])

    def test_default_and_long_timeout_reach_runner(self):
        for name in ('claude', 'opencode'):
            with self.subTest(name=name):
                self.assertEqual(self.run_with(NamedRoute(name)), 600)
                self.assertEqual(self.run_with(NamedRoute(name), timeout=1200), 1200)
                self.assertEqual(self.run_with(NamedRoute(name), timeout=1800), 1800)

    def test_adapter_name_maps_to_registry_entry(self):
        self.assertEqual(self.run_with(NamedRoute('CLAUDE'), timeout=1800), 1800)
        self.assert_request_refused(NamedRoute('gw_agent'), 601, 'TIMEOUT_OVER_ROUTE_MAX')

    def test_short_route_refuses_before_runner(self):
        self.assertEqual(self.run_with(NamedRoute('agy'), timeout=600), 600)
        self.assert_request_refused(NamedRoute('agy'), 601, 'TIMEOUT_OVER_ROUTE_MAX')
        self.assert_request_refused(NamedRoute('agy'), 1200, 'TIMEOUT_OVER_ROUTE_MAX')

    def test_hard_cap_and_invalid_refused_before_runner(self):
        for name in ('claude', 'agy', 'FAKE'):
            with self.subTest(name=name):
                self.assert_request_refused(NamedRoute(name), 1801, 'TIMEOUT_OVER_HARD_CAP')
                for value in NOT_INT + (0, -1):
                    self.assert_request_refused(NamedRoute(name), value, 'TIMEOUT_INVALID')

    def test_explicit_none_timeout_refused(self):
        with self.assertRaises(RouteError) as caught:
            NamedRoute('claude').validate_request(dict(self.request, timeout=None))
        self.assertEqual(caught.exception.code, 'CLAUDE_REQUEST_INVALID')

    def test_unregistered_adapter_keeps_600_baseline(self):
        self.assertEqual(self.run_with(NamedRoute('FAKE')), 600)
        self.assertEqual(self.run_with(NamedRoute('FAKE'), timeout=600), 600)
        self.assert_request_refused(NamedRoute('FAKE'), 601, 'TIMEOUT_OVER_ROUTE_MAX')

    def test_adapter_config_cannot_raise_registry_limit(self):
        route = NamedRoute('agy')
        route.config.update(default_timeout=600, max_timeout=1800)
        self.assert_request_refused(route, 1200, 'TIMEOUT_OVER_ROUTE_MAX')


if __name__ == '__main__':
    unittest.main()
