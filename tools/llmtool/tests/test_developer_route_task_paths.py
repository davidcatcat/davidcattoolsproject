"""#2157, #2281: per-route max_task_paths in developer-routes.json and dispatch cap."""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llmtool import developer_routes as dr  # noqa: E402
from llmtool import opencode_direct_developer  # noqa: E402
from llmtool import task_completion  # noqa: E402
from llmtool import task_store  # noqa: E402
from llmtool import task_submission_cli as cli  # noqa: E402
from llmtool.developer_routes import base  # noqa: E402
from llmtool.errors import LLMToolError  # noqa: E402

CONFIG = ROOT / 'developer-routes.json'
ROUTES = ('agy', 'claude', 'gw-agent', 'opencode')


def config_with(route, value):
    data = json.loads(CONFIG.read_text(encoding='utf-8'))
    data['routes'][route]['max_task_paths'] = value
    return data


def _sample_paths(count):
    return [f'dir/file_{index}.py' for index in range(count)]


class MaxTaskPathsTests(unittest.TestCase):
    def load(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'developer-routes.json'
            path.write_text(json.dumps(data), encoding='utf-8')
            return dr.load_route_config(path)

    def test_default_is_1024_for_every_registered_route(self):
        self.assertEqual((base.DEFAULT_MAX_TASK_PATHS, base.HARD_MAX_TASK_PATHS), (1024, 1024))
        config = dr.load_route_config()
        for route in ROUTES:
            with self.subTest(route=route):
                self.assertEqual(dr.resolve_route_max_task_paths(route, config=config), 1024)

    def test_four_1024_constants_equal_hard_max_task_paths(self):
        self.assertEqual(cli.DEVELOPER_TASK_MAX_PATHS, base.HARD_MAX_TASK_PATHS)
        self.assertEqual(task_store.DEVELOPER_TASK_MAX_PATHS, base.HARD_MAX_TASK_PATHS)
        self.assertEqual(opencode_direct_developer.MAX_TASK_PATHS, base.HARD_MAX_TASK_PATHS)
        self.assertEqual(task_completion._TASK_PATH_LIMIT, base.HARD_MAX_TASK_PATHS)

    def test_valid_values_in_1_to_1024_are_accepted_for_that_route(self):
        for value in (1, 8, 32, 1024):
            with self.subTest(value=value):
                config = self.load(config_with('agy', value))
                self.assertEqual(dr.resolve_route_max_task_paths('agy', config=config), value)
                self.assertEqual(dr.resolve_route_max_task_paths('claude', config=config), 1024)

    def test_invalid_values_refuse_route_config_invalid_at_load(self):
        for value in (0, -1, 1025, True, False, 32.0, '32', None):
            with self.subTest(value=repr(value)):
                with self.assertRaises(dr.RouteError) as caught:
                    self.load(config_with('gw-agent', value))
                self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')

    def test_resolver_refuses_bad_values_and_unknown_routes(self):
        for value in (1025, 0, -1, True, False, '8', 8.0, None):
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
        good = dict(routes=dict(agy=dict(max_task_paths=8)))
        bad = dict(routes=dict(agy=dict(max_task_paths=1025)))
        self.assertEqual(dr.resolve_route_max_task_paths(dr.AgyRoute({}), config=good), 8)
        self.assertEqual(dr.resolve_route_max_task_paths(dr.ClaudeRoute({}), config=empty), 1024)
        with self.assertRaises(dr.RouteError):
            dr.resolve_route_max_task_paths(dr.AgyRoute({}), config=bad)

    def test_route_without_key_allows_1024_and_refuses_1025(self):
        paths_1024 = _sample_paths(1024)
        paths_1025 = _sample_paths(1025)
        for route in (None, *ROUTES):
            with self.subTest(route=route):
                read_out, write_out = cli._assignment_paths(paths_1024, paths_1024, route=route)
                self.assertEqual(len(read_out), 1024)
                self.assertEqual(len(write_out), 1024)
                with self.assertRaises(LLMToolError) as caught:
                    cli._assignment_paths(paths_1025, ['dir/file_0.py'], route=route)
                self.assertEqual(getattr(caught.exception, 'code', None), 'PATH_LIMIT_EXCEEDED')
                with self.assertRaises(LLMToolError) as caught_write:
                    cli._assignment_paths(paths_1025, paths_1025, route=route)
                self.assertEqual(getattr(caught_write.exception, 'code', None), 'PATH_LIMIT_EXCEEDED')

    def test_route_with_max_task_paths_8_allows_8_and_refuses_9(self):
        paths_8 = _sample_paths(8)
        paths_9 = _sample_paths(9)
        for route in ROUTES:
            with self.subTest(route=route):
                cfg = config_with(route, 8)
                with patch.object(dr, 'load_route_config', return_value=cfg):
                    read_out, write_out = cli._assignment_paths(paths_8, paths_8, route=route)
                    self.assertEqual(len(read_out), 8)
                    self.assertEqual(len(write_out), 8)
                    with self.assertRaises(LLMToolError) as caught_read:
                        cli._assignment_paths(paths_9, ['dir/file_0.py'], route=route)
                    self.assertEqual(getattr(caught_read.exception, 'code', None), 'PATH_LIMIT_EXCEEDED')
                    with self.assertRaises(LLMToolError) as caught_write:
                        cli._assignment_paths(paths_9, paths_9, route=route)
                    self.assertEqual(getattr(caught_write.exception, 'code', None), 'PATH_LIMIT_EXCEEDED')

    def test_route_config_invalid_refuses_before_launch_without_fallback(self):
        with patch.object(dr, 'load_route_config', side_effect=dr.RouteError('ROUTE_CONFIG_INVALID')):
            with self.assertRaises(LLMToolError) as caught:
                cli._assignment_paths(['a.py'], ['a.py'], route='agy')
            self.assertEqual(getattr(caught.exception, 'code', None), 'ROUTE_CONFIG_INVALID')

    def test_route_delegates_refuse_9_paths_when_max_task_paths_is_8(self):
        paths_9 = _sample_paths(9)
        paths_8 = _sample_paths(8)

        class FakeStore:
            def __init__(self, route):
                self.task = {
                    'task_id': 't' * 32,
                    'run_id': 'r' * 32,
                    'schema_version': 2,
                    'state': 'PM_RESPONDED',
                    'request': 'req',
                    'response': 'resp',
                    'developer': None,
                    'task_binding': {'contract_digest': 'c' * 64},
                    'pm_assignment': {
                        'route': route,
                        'risk': 'LOW',
                        'goal': 'g',
                        'acceptance': ['ok'],
                    },
                }

            def get(self, _task_id):
                return self.task

        def make_args(route, paths):
            return SimpleNamespace(
                task_id='t' * 32,
                provider=route,
                mode='direct' if route != 'opencode' else 'proposal',
                context_stdin=False,
                selection_source='PM',
                executor_task='d6-1',
                read_path=list(paths),
                write_path=list(paths),
                acceptance=['ok'],
                egress_basis='public repo',
                data_class='PUBLIC_REPO',
                owner_ref=None,
                timeout=None,
                reassign_from=None,
            )

        delegates = (
            ('opencode', cli.run_task_opencode_delegate, 'opencode-developer'),
            ('agy', cli.run_task_agy_delegate, 'agy-developer'),
            ('claude', cli.run_task_claude_delegate, 'claude-developer'),
            ('gw-agent', cli.run_task_gw_agent_delegate, 'gw-agent-developer'),
        )
        for route, fn, agent in delegates:
            with self.subTest(route=route):
                cfg = config_with(route, 8)
                reg = SimpleNamespace(
                    task_id='d6-1', trusted_agent_id=agent, session_ref='ref',
                    native_session_id='ses-1', root='/tmp/r', common_dir='/tmp/c',
                    branch='b', source_sha='a' * 40, result_sha='a' * 40,
                )
                snap = SimpleNamespace(
                    head='a' * 40, root='/tmp/r', common_dir='/tmp/c',
                    branch='b', clean=True,
                )
                store = FakeStore(route)
                with patch.object(dr, 'load_route_config', return_value=cfg), \
                     patch.object(cli, '_delegate_d6_binding', return_value=(reg, snap)), \
                     patch.object(task_completion, 'validate_task_binding', return_value=None), \
                     patch.object(cli.worker_dispatch, 'evaluate',
                                  return_value=SimpleNamespace(allowed=True, reason_code='OWNER_POLICY_ALLOWED')), \
                     patch.object(cli.worker_availability, 'gw_agent_refusal', return_value=None), \
                     patch('llmtool.gw_agent_sessions.sweep_quietly', return_value=None):
                    with self.assertRaises(LLMToolError) as caught:
                        fn(make_args(route, paths_9), store=store)
                    self.assertEqual(getattr(caught.exception, 'code', None), 'PATH_LIMIT_EXCEEDED')

                    class StopAfterPaths(Exception):
                        pass

                    real_assignment_paths = cli._assignment_paths

                    def check_and_stop(*call_args, **call_kwargs):
                        result = real_assignment_paths(*call_args, **call_kwargs)
                        raise StopAfterPaths(result)

                    with patch.object(cli, '_assignment_paths',
                                      side_effect=check_and_stop) as wrapped:
                        with self.assertRaises(StopAfterPaths):
                            fn(make_args(route, paths_8), store=store)
                        self.assertEqual(wrapped.call_args.kwargs.get('route'), route)

    def test_claude_bind_is_unaffected_by_lowered_claude_route_cap(self):
        paths = ['dir/file_0.py', 'dir/file_1.py', 'dir/file_2.py']
        changed_raw = ('\x00'.join(paths) + '\x00').encode('utf-8')
        task = {
            'schema_version': 2,
            'task_id': 't' * 32,
            'run_id': 'r' * 32,
            'pm_session_id': 'pm-1',
            'pm_provider_profile': 'active-pm-runtime',
            'pm_provider_type': 'active-pm-runtime',
            'pm_model': None,
            'backend_session_semantics': 'ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL',
            'native_session_id': None,
            'state': 'PM_RESPONDED',
            'created_at': '2026-11-24T00:00:00Z',
            'updated_at': '2026-11-24T00:00:00Z',
            'task_contract': {},
            'task_binding': {'contract_digest': 'c' * 64},
            'request': 'req',
            'request_sha256': 'd' * 64,
            'response': 'resp',
            'usage': None,
            'error_code': None,
            'developer': None,
            'pm_assignment': {
                'route': 'claude',
                'risk': 'LOW',
                'goal': 'g',
                'acceptance': ['ok'],
            },
            'pm_provenance': {'source': 'ACTIVE_PM_RUNTIME', 'provider_invoked': False},
            'completion': None,
            'record_sha256': 'e' * 64,
            'abandonment': None,
        }

        class FakeStore:
            def get(self, _task_id):
                return task

            def claude_bind_compare_and_update(self, _task_id, **updates):
                saved = dict(task)
                saved['developer'] = updates['developer']
                saved['completion'] = updates['completion']
                return saved

        reg = SimpleNamespace(
            task_id='d6-1', trusted_agent_id='claude-developer', session_ref='ref',
            native_session_id='sess-1', root='/tmp/r', common_dir='/tmp/c',
            branch='b', source_sha='a' * 40, result_sha='b' * 40,
        )
        snap = SimpleNamespace(
            root='/tmp/r', common_dir='/tmp/c', branch='b', head='b' * 40, clean=True,
        )
        args = SimpleNamespace(
            task_id='t' * 32,
            executor_task='d6-1',
            owner_ref='https://github.com/o/r/issues/1#issuecomment-1',
            read_path=list(paths),
            write_path=list(paths),
            common_dir='/tmp/c',
        )
        cfg = config_with('claude', 1)
        out = io.StringIO()
        with patch.object(dr, 'load_route_config', return_value=cfg), \
             patch.object(task_completion, 'read_d6_registration', return_value=reg), \
             patch.object(task_completion, 'collect_git_snapshot', return_value=snap), \
             patch.object(task_completion, '_git', return_value=changed_raw), \
             patch.object(cli, '_remote_task_test_paths',
                          return_value=('tools/llmtool/tests/test_developer_route_task_paths.py',)), \
             patch.object(cli.worker_dispatch, 'evaluate',
                          return_value=SimpleNamespace(allowed=True, reason_code='OWNER_ASSIGNMENT_MATCHED')):
            rc = cli.run_task_claude_bind(args, store=FakeStore(), stdout=out)
        self.assertEqual(rc, 0)


if __name__ == '__main__':
    unittest.main()
