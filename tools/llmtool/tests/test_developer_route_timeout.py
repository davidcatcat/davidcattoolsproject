"""Per-route default_timeout and max_timeout in developer-routes.json."""

from argparse import Namespace
from contextlib import redirect_stdout
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from llmtool import developer_routes, task_completion
from llmtool import task_submission_cli as submission_cli
from llmtool.cli import parser_for_cli
from llmtool.developer_routes import RouteError, load_route_config, resolve_route_timeout
from llmtool.developer_routes.base import DeveloperRoute, run_route
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStoreConflict

ROUTES_FILE = Path(__file__).resolve().parents[1] / 'developer-routes.json'
DRIFT_SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'guidance_drift_check.py'
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

    def test_rule_file_schema_cap_matches_hard_cap(self):
        # #1815: guidance_drift_check.py keeps its own copy of the cap because
        # it does not import the llmtool package; the two must stay in step.
        spec = importlib.util.spec_from_file_location(
            'guidance_drift_check_for_timeout_cap', DRIFT_SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.ROUTE_TIMEOUT_CAP_SECONDS, HARD_CAP)


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

    # Bad limit values that JSON can hold: bool, float, str, list, object,
    # null, zero, negative and above the 1800 hard cap.
    JSON_BAD_VALUES = (True, False, 600.0, 1.5, '600', [600], {'s': 600}, None,
                       0, -1, -600, HARD_CAP + 1, 3600)
    # Bad values JSON cannot hold; checked through the resolver only.
    RESOLVER_ONLY_BAD_VALUES = (b'600',)

    def bad_limit_values(self, route, key):
        """JSON-safe bad values for one key; each breaks 1 <= default <= max <= 1800."""
        entry = self.shipped['routes'][route]
        if key == 'default_timeout':
            return self.JSON_BAD_VALUES + (entry['max_timeout'] + 1,)  # default > max
        return self.JSON_BAD_VALUES + (entry['default_timeout'] - 1,)  # max < default

    def assert_resolver_refuses(self, route, key, value):
        config = copy.deepcopy(load_route_config())
        config['routes'][route][key] = value
        with self.assertRaises(RouteError) as caught:
            resolve_route_timeout(route, config=config)
        self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')

    def assert_file_refuses(self, route, key, value):
        data = copy.deepcopy(self.shipped)
        data['routes'][route][key] = value
        self.path.write_text(json.dumps(data), encoding='utf-8')
        # The file is valid JSON and still holds the exact bad value.
        written = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertIs(type(written['routes'][route][key]), type(value))
        self.assertEqual(written['routes'][route][key], value)
        with self.assertRaises(RouteError) as caught:
            load_route_config(self.path)
        self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')

    def test_bad_values_refused(self):
        for route in ROUTE_NAMES:
            for key in ('default_timeout', 'max_timeout'):
                for value in self.bad_limit_values(route, key):
                    with self.subTest(route=route, key=key, value=value):
                        self.assert_resolver_refuses(route, key, value)
                        self.assert_file_refuses(route, key, value)
                for value in self.RESOLVER_ONLY_BAD_VALUES:
                    with self.subTest(route=route, key=key, value=value):
                        self.assert_resolver_refuses(route, key, value)

    def test_bad_value_lists_cover_every_class(self):
        values = self.JSON_BAD_VALUES
        for value in values:
            json.dumps(value)  # every listed value is valid JSON
        for value in self.RESOLVER_ONLY_BAD_VALUES:
            with self.assertRaises(TypeError):
                json.dumps(value)
        self.assertTrue(any(type(v) is bool for v in values))
        self.assertTrue(any(type(v) is float for v in values))
        self.assertTrue(any(type(v) is str for v in values))
        self.assertIn(None, values)
        self.assertTrue(any(type(v) is int and v == 0 for v in values))
        self.assertTrue(any(type(v) is int and v < 0 for v in values))
        self.assertTrue(any(type(v) is int and v > HARD_CAP for v in values))

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


class _StoreReached(Exception):
    """Raised by ReachedStore: the delegate passed its timeout check."""


class ReachedStore:
    """Fake store whose first read proves the timeout was not refused."""

    def get(self, task_id):
        raise _StoreReached()


class CaptureStore:
    """Fake store: records the first developer write, then loses the CAS."""

    def __init__(self, task):
        self.task = task
        self.reads = 0
        self.developers = []

    def get(self, task_id):
        self.reads += 1
        return copy.deepcopy(self.task)

    def d6_bind_compare_and_update(self, task_id, *, executor_task_id, expected,
                                   developer, completion):
        self.developers.append(copy.deepcopy(developer))
        raise TaskStoreConflict()


def timeout_refusals(route):
    """(requested, resolver code) pairs every delegate must refuse for route."""
    cases = [(1801, 'TIMEOUT_OVER_HARD_CAP'), (3600, 'TIMEOUT_OVER_HARD_CAP'),
             (0, 'TIMEOUT_INVALID'), (-1, 'TIMEOUT_INVALID'), (True, 'TIMEOUT_INVALID')]
    if route in SHORT_ROUTES:
        cases += [(601, 'TIMEOUT_OVER_ROUTE_MAX'), (1200, 'TIMEOUT_OVER_ROUTE_MAX'),
                  (1800, 'TIMEOUT_OVER_ROUTE_MAX')]
    return cases


def timeout_accepted(route):
    """Requested values the route accepts; None is the route default."""
    return (None, 1, 600) + ((1200, 1800) if route in LONG_ROUTES else ())


class DelegateTimeoutTests(unittest.TestCase):
    """#1815 piece 2: task delegate --timeout is resolved before launch and recorded."""

    DELEGATES = {
        'agy': submission_cli.run_task_agy_delegate,
        'claude': submission_cli.run_task_claude_delegate,
        'gw-agent': submission_cli.run_task_gw_agent_delegate,
        'opencode': submission_cli.run_task_opencode_delegate,
    }

    def delegate_args(self, **changes):
        values = dict(task_id='a' * 32, mode='direct', background=False, timeout=None,
                      selection_source='PM', owner_ref=None, context_stdin=False,
                      egress_basis='synthetic text', data_class='public',
                      acceptance=['bounded'])
        values.update(changes)
        return Namespace(**values)

    def assert_refusal(self, raised, code, route):
        message = str(raised.exception)
        self.assertTrue(message.startswith(code + ':'), message)
        self.assertIn(route, message)

    def test_timeout_routes_are_the_four_direct_routes(self):
        self.assertEqual(set(submission_cli.TIMEOUT_ROUTES), set(ROUTE_NAMES))
        self.assertEqual(set(self.DELEGATES), set(ROUTE_NAMES))

    def test_parser_timeout_is_optional_int(self):
        parser = parser_for_cli()
        for route in ROUTE_NAMES:
            base = ['task', 'delegate', 'a' * 32, '--provider', route, '--mode', 'direct',
                    '--selection-source', 'PM', '--executor-task', 'executor-task-1',
                    '--read-path', 'sample.py', '--write-path', 'sample.py',
                    '--acceptance', 'focused test passes', '--egress-basis', 'synthetic text']
            with self.subTest(route=route):
                self.assertIsNone(parser.parse_args(base).timeout)
                # The parser only reads an int; the route limits are the resolver's.
                for value in ('600', '1200', '1800', '1801'):
                    self.assertEqual(
                        parser.parse_args(base + ['--timeout', value]).timeout, int(value))
                for value in ('soon', '1.5', ''):
                    with redirect_stdout(io.StringIO()), \
                            mock.patch('sys.stderr', io.StringIO()), \
                            self.assertRaises(SystemExit):
                        parser.parse_args(base + ['--timeout', value])

    def test_helper_defaults_and_accepts_route_limits(self):
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                # No attribute and an explicit None both give the route default.
                self.assertEqual(submission_cli._delegate_timeout(Namespace(), route), 600)
                for requested in timeout_accepted(route):
                    expected = 600 if requested is None else requested
                    self.assertEqual(submission_cli._delegate_timeout(
                        Namespace(timeout=requested), route), expected)

    def test_helper_refuses_with_resolver_code(self):
        for route in ROUTE_NAMES:
            for requested, code in timeout_refusals(route):
                with self.subTest(route=route, requested=requested):
                    with self.assertRaises(LLMToolError) as raised:
                        submission_cli._delegate_timeout(Namespace(timeout=requested), route)
                    self.assert_refusal(raised, code, route)

    def test_delegate_refuses_before_task_read_or_fork(self):
        for route in ROUTE_NAMES:
            for requested, code in timeout_refusals(route):
                for background in (False, True):
                    with self.subTest(route=route, requested=requested,
                                      background=background):
                        args = self.delegate_args(provider=route, timeout=requested,
                                                  background=background)
                        # store=object(): any Task read fails with AttributeError.
                        with mock.patch.object(os, 'fork') as fork, \
                                self.assertRaises(LLMToolError) as raised:
                            submission_cli.run_task_delegate(args, store=object())
                        self.assert_refusal(raised, code, route)
                        fork.assert_not_called()

    def test_route_delegates_refuse_before_task_read(self):
        for route, delegate in self.DELEGATES.items():
            for requested, code in timeout_refusals(route):
                with self.subTest(route=route, requested=requested):
                    with mock.patch('llmtool.gw_agent_sessions.sweep_quietly'), \
                            self.assertRaises(LLMToolError) as raised:
                        delegate(self.delegate_args(timeout=requested), store=object())
                    self.assert_refusal(raised, code, route)

    def test_route_delegates_accept_route_limits(self):
        for route, delegate in self.DELEGATES.items():
            for requested in timeout_accepted(route):
                with self.subTest(route=route, requested=requested):
                    with mock.patch('llmtool.gw_agent_sessions.sweep_quietly'), \
                            self.assertRaises(_StoreReached):
                        delegate(self.delegate_args(timeout=requested), store=ReachedStore())

    def record_opencode_timeout(self, requested):
        """Return the OpenCode developer record written before launch."""
        acceptance = ['focused test passes']
        task = {'task_id': 'a' * 32, 'run_id': 'b' * 32, 'state': 'PM_RESPONDED',
                'schema_version': 2, 'request': 'Update the sample value.',
                'response': 'Route the change to OpenCode.', 'developer': None,
                'task_binding': {'contract_digest': 'c' * 64},
                'pm_assignment': {'route': 'opencode', 'risk': 'LOW',
                                  'acceptance': acceptance,
                                  'goal': 'Update the sample value.'}}
        store = CaptureStore(task)
        registration = SimpleNamespace(
            task_id='executor-task-1', trusted_agent_id='opencode-developer',
            session_ref=None, native_session_id=None, root='/d6', common_dir='/d6-common',
            branch='task')
        snapshot = SimpleNamespace(head='d' * 40)
        args = self.delegate_args(timeout=requested, acceptance=acceptance,
                                  read_path=['sample.py'], write_path=['sample.py'],
                                  executor_task='executor-task-1')
        with mock.patch.object(submission_cli, '_opencode_developer_profile',
                               return_value=('opencode/sample-model-free', 12)), \
                mock.patch.object(submission_cli, '_developer_card_prefix',
                                  return_value=('\nFixture rules.', 'e' * 64)), \
                mock.patch.object(submission_cli.opencode_direct_developer, 'run') as launch, \
                self.assertRaises(LLMToolError) as raised:
            submission_cli.run_task_opencode_delegate(
                args, store=store, d6_binding=(registration, snapshot))
        self.assertIn('duplicate dispatch refused', str(raised.exception))
        launch.assert_not_called()
        self.assertEqual(len(store.developers), 1)
        return store.developers[0]

    def test_opencode_record_carries_resolved_timeout(self):
        for requested, expected in ((None, 600), (600, 600), (1200, 1200), (1800, 1800)):
            with self.subTest(requested=requested):
                developer = self.record_opencode_timeout(requested)
                self.assertEqual(developer['timeout_seconds'], expected)
                self.assertIs(type(developer['timeout_seconds']), int)
                self.assertEqual(developer['provider_profile'], 'opencode')
                self.assertEqual(developer['state'], 'DEVELOPER_PENDING')

    def test_opencode_refusal_writes_no_record(self):
        for requested, code in timeout_refusals('opencode'):
            with self.subTest(requested=requested):
                store = CaptureStore({})
                with self.assertRaises(LLMToolError) as raised:
                    submission_cli.run_task_opencode_delegate(
                        self.delegate_args(timeout=requested), store=store)
                self.assert_refusal(raised, code, 'opencode')
                self.assertEqual(store.reads, 0)
                self.assertEqual(store.developers, [])


if __name__ == '__main__':
    unittest.main()
