"""Design c6876861575 items 1-2: provider_evidence from parsed provider output.

Fixtures are scrubbed shapes of real provider output: fixed ids, digests and
counts only. No credential-shaped value appears anywhere (Owner #763 A).
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llmtool.errors import LLMToolError  # noqa: E402
from llmtool import opencode_direct_developer as opencode  # noqa: E402
from llmtool import task_store  # noqa: E402
from llmtool import task_submission_cli as cli  # noqa: E402
from llmtool.developer_routes import base  # noqa: E402
from llmtool.developer_routes.agy import AgyRoute  # noqa: E402
from llmtool.developer_routes.base import RouteError, run_route  # noqa: E402
from llmtool.developer_routes.claude import ClaudeRoute  # noqa: E402
from llmtool.developer_routes.gw_agent import GwAgentRoute  # noqa: E402

HEAD = 'a' * 40
POLICY = 'f' * 64
CLAUDE_SESSION = '00000000-0000-4000-8000-000000000001'
CLAUDE_MODEL = 'claude-test-1'
AGY_CONVERSATION = 'conv-0123abcd'
AGY_MODEL = 'agy-model-1'
OC_SESSION = 'ses_Fixture01'
GW_MODEL = 'model-a'

CLI_USAGE = {'basis': 'CLI_USAGE', 'accepted': True}
STEP_USAGE = {'basis': 'STEP_USAGE', 'accepted': True}
AGY_STATUS = {'basis': 'AGY_STATUS', 'accepted': True}
GATEWAY_USAGE = {'basis': 'GATEWAY_USAGE', 'accepted': True}
QUOTA = {'condition': 'QUOTA_EXHAUSTED'}
RATE = {'condition': 'RATE_LIMITED'}


def _claude_result(**changes):
    value = {
        'type': 'result', 'subtype': 'success', 'is_error': False,
        'duration_ms': 1200, 'num_turns': 3, 'result': 'Edited the file.',
        'session_id': CLAUDE_SESSION, 'total_cost_usd': 0.01,
        'usage': {'input_tokens': 12, 'cache_creation_input_tokens': 0,
                  'cache_read_input_tokens': 40, 'output_tokens': 7},
        'modelUsage': {CLAUDE_MODEL: {'inputTokens': 12, 'outputTokens': 7}},
        'permission_denials': [],
    }
    value.update(changes)
    return json.dumps(value).encode('utf-8')


def _agy_result(**changes):
    value = {'conversation_id': AGY_CONVERSATION, 'status': 'SUCCESS',
             'response': 'Edited the file.',
             'usage': {'input_tokens': 50, 'output_tokens': 9}}
    value.update(changes)
    return json.dumps(value).encode('utf-8')


def _gw_result(**changes):
    value = {
        'schema_version': 1, 'status': 'DONE', 'code': None, 'run_id': 'b' * 24,
        'session_id': 'c' * 32, 'task_id': 'task-1', 'changed_paths': ['x.txt'],
        'summary': 'Edited the file.', 'served_model': GW_MODEL,
        'usage': {'status': 'REPORTED', 'input_tokens': 10, 'output_tokens': 4},
        'turns': 2, 'budget_used': None, 'audit_sha256': 'd' * 64,
    }
    value.update(changes)
    return value


def _events(*events):
    return b''.join(json.dumps(event).encode('utf-8') + b'\n' for event in events)


def _step_finish(tokens=None):
    part = {'type': 'step-finish'}
    if tokens is not None:
        part['tokens'] = tokens
    return {'type': 'step_finish', 'sessionID': OC_SESSION, 'part': part}


STEP_START = {'type': 'step_start', 'sessionID': OC_SESSION, 'part': {'type': 'step-start'}}
TEXT = {'type': 'text', 'sessionID': OC_SESSION, 'part': {'text': 'Edited the file.'}}


def _gw_route(result=None):
    route = GwAgentRoute.__new__(GwAgentRoute)
    route.config = {}
    route.expected_served_model = None
    route._validated_request = None
    route._result = result
    route._tool_failure = None
    route.audit_copy_path = None
    return route


class _Boundary:
    def prepare(self, root, paths, state_dirs=()):
        return [], POLICY

    def verify(self, root, paths, state_dirs=(), *, runner):
        return {'verified': True, 'policy_sha256': POLICY}


def _runner(code, stdout, stderr=b'', log=None):
    def run(argv, cwd, env, timeout):
        if log is not None:
            Path(argv[argv.index('--log-file') + 1]).write_text(log, encoding='utf-8')
        return code, stdout, stderr
    run.last_cleanup_status = 'CONFIRMED'
    return run


class _ClaudeFixture(ClaudeRoute):
    def build_env(self, tmpdir):
        return {'PATH': '/usr/bin:/bin', 'TMPDIR': tmpdir}


class _AgyFixture(AgyRoute):
    def build_env(self, tmpdir):
        return {'PATH': '/usr/bin:/bin', 'TMPDIR': tmpdir}


class _GwFixture(GwAgentRoute):
    def __init__(self, result):
        base.DeveloperRoute.__init__(self, {})
        self.expected_served_model = None
        self._validated_request = None
        self._fixture = result
        self._result = None
        self._tool_failure = None
        self.audit_copy_path = None

    def validate_request(self, request):
        value = base.DeveloperRoute.validate_request(self, request)
        self._validated_request = value
        return value

    def prepare_files(self, tmpdir, request):
        return None

    def build_argv(self, executable, request):
        return [executable]

    def build_env(self, tmpdir):
        return {'PATH': '/usr/bin:/bin', 'TMPDIR': tmpdir}

    def collect(self, tmpdir, request):
        self._result = dict(self._fixture)


class _RouteRunCase(unittest.TestCase):
    """run_route on a temporary root with the Git observations stubbed."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        snapshot = SimpleNamespace(branch='main', head=HEAD, clean=True)
        patches = (
            mock.patch.object(base.task_completion, 'collect_git_snapshot',
                              return_value=snapshot),
            mock.patch.object(base, '_git_status_paths', return_value=()),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self._tmp.cleanup)

    def run_route(self, route, runner, **request):
        value = {'root': self.root, 'effort': 'low', 'expected_branch': 'main',
                 'expected_head': HEAD, 'write_paths': ['x.txt'],
                 'executable': '/bin/true', 'prompt': 'Edit x.txt.'}
        value.update(request)
        return run_route(route, value, gate=lambda: True, runner=runner,
                         boundary=_Boundary(), snapshot=lambda root: {'files': {}})


class ClaudeEvidenceTest(_RouteRunCase):
    def route(self):
        return _ClaudeFixture({'executable': '/bin/true'})

    def test_parsed_result_with_usage_gives_cli_usage(self):
        result = self.run_route(self.route(), _runner(0, _claude_result()),
                                model=CLAUDE_MODEL)
        self.assertEqual(result.provider_evidence, CLI_USAGE)

    def test_exit_zero_without_usage_gives_none(self):
        stdout = json.loads(_claude_result())
        del stdout['usage']
        result = self.run_route(self.route(), _runner(0, json.dumps(stdout).encode()),
                                model=CLAUDE_MODEL)
        self.assertEqual(result.exit_code, 0)
        self.assertIsNone(result.provider_evidence)

    def test_pre_execution_no_changes_result_gives_none(self):
        # A result before any model turn: zero usage, no model output.
        route = self.route()
        parsed = route.parse_output(_claude_result(
            num_turns=0, result='', usage={'input_tokens': 0, 'output_tokens': 0},
            modelUsage={}), b'', '')
        self.assertIsNone(route.provider_evidence(parsed))
        with self.assertRaises(RouteError) as caught:
            self.run_route(route, _runner(0, _claude_result(
                num_turns=0, usage={'input_tokens': 0, 'output_tokens': 0},
                modelUsage={})), model=CLAUDE_MODEL)
        self.assertIsNone(caught.exception.provider_evidence)

    def test_quota_refusal_is_quota_exhausted(self):
        with self.assertRaises(RouteError) as caught:
            self.run_route(self.route(), _runner(1, b'', b'usage limit reached'),
                           model=CLAUDE_MODEL)
        self.assertEqual(caught.exception.code, 'CLAUDE_QUOTA_EXHAUSTED')
        self.assertEqual(caught.exception.provider_evidence, QUOTA)

    def test_nonzero_exit_gives_none(self):
        with self.assertRaises(RouteError) as caught:
            self.run_route(self.route(), _runner(1, _claude_result()), model=CLAUDE_MODEL)
        self.assertEqual(caught.exception.code, 'CLAUDE_EXIT_NONZERO')
        self.assertIsNone(caught.exception.provider_evidence)

    def test_timeout_gives_none(self):
        def runner(argv, cwd, env, timeout):
            raise TimeoutError
        with self.assertRaises(RouteError) as caught:
            self.run_route(self.route(), runner, model=CLAUDE_MODEL)
        self.assertEqual(caught.exception.code, 'CLAUDE_TIMEOUT')
        self.assertIsNone(caught.exception.provider_evidence)

    def test_stderr_fallback_parse_gives_no_basis(self):
        route = self.route()
        parsed = route.parse_output(b'not json', b'rate limit', '')
        self.assertIsNone(route.provider_evidence(parsed))


class AgyEvidenceTest(_RouteRunCase):
    LOG = 'model_resolver: Resolving model %s\n' % AGY_MODEL

    def route(self):
        return _AgyFixture({'executable': '/bin/true'})

    def test_success_status_with_conversation_gives_agy_status(self):
        result = self.run_route(self.route(), _runner(0, _agy_result(), log=self.LOG),
                                model=AGY_MODEL, log_path='agy.log')
        self.assertEqual(result.provider_evidence, AGY_STATUS)

    def test_other_status_gives_none(self):
        result = self.run_route(self.route(),
                                _runner(0, _agy_result(status='COMPLETED'), log=self.LOG),
                                model=AGY_MODEL, log_path='agy.log')
        self.assertIsNone(result.provider_evidence)

    def test_invalid_conversation_gives_no_basis(self):
        route = self.route()
        self.assertIsNone(route.provider_evidence(
            {'session_id': 'x', 'raw': {'status': 'SUCCESS', 'conversation_id': 'x'}}))
        with self.assertRaises(RouteError):
            route.parse_output(_agy_result(conversation_id='short'), b'', '')

    def test_quota_refusal_is_quota_exhausted(self):
        with self.assertRaises(RouteError) as caught:
            self.run_route(self.route(),
                           _runner(0, _agy_result(status='RESOURCE_EXHAUSTED'), log=self.LOG),
                           model=AGY_MODEL, log_path='agy.log')
        self.assertEqual(caught.exception.code, 'AGY_QUOTA_EXHAUSTED')
        self.assertEqual(caught.exception.provider_evidence, QUOTA)


class GwAgentEvidenceTest(_RouteRunCase):
    def test_parsed_usage_and_served_model_give_gateway_usage(self):
        route = _gw_route(_gw_result())
        parsed = route.parse_output(b'', b'', '')
        self.assertEqual(route.provider_evidence(parsed), GATEWAY_USAGE)

    def test_missing_usage_or_routing_alias_gives_none(self):
        for result in (_gw_result(usage={'status': 'UNKNOWN'}),
                       _gw_result(usage={'status': 'REPORTED', 'input_tokens': 0,
                                         'output_tokens': 0})):
            route = _gw_route(result)
            self.assertIsNone(route.provider_evidence(route.parse_output(b'', b'', '')))
        for model in ('gw', 'UNKNOWN'):
            route = _gw_route(_gw_result(served_model=model))
            self.assertIsNone(route.provider_evidence(
                {'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}))

    def test_run_route_success_gives_gateway_usage(self):
        result = self.run_route(_GwFixture(_gw_result()), _runner(0, b''), model=GW_MODEL)
        self.assertEqual(result.provider_evidence, GATEWAY_USAGE)

    def test_http_429_is_quota_exhausted(self):
        fixture = _gw_result(status='MODEL_ERROR', code='HTTP_429',
                             usage={'status': 'UNKNOWN'})
        with self.assertRaises(RouteError) as caught:
            self.run_route(_GwFixture(fixture), _runner(4, b''), model=GW_MODEL)
        self.assertEqual(caught.exception.code, 'GW_AGENT_QUOTA_EXHAUSTED')
        self.assertEqual(caught.exception.provider_evidence, QUOTA)

    def test_other_gateway_failure_gives_none(self):
        fixture = _gw_result(status='MODEL_ERROR', code='HTTP_500')
        with self.assertRaises(RouteError) as caught:
            self.run_route(_GwFixture(fixture), _runner(4, b''), model=GW_MODEL)
        self.assertIsNone(caught.exception.provider_evidence)


class OpenCodeEvidenceTest(unittest.TestCase):
    def test_success_step_usage(self):
        raw = _events(STEP_START, TEXT, _step_finish({'input': 120, 'output': 30}))
        session, text, usage = opencode.parse_json_events(raw)
        self.assertEqual(session, OC_SESSION)
        self.assertIsNotNone(usage)
        self.assertEqual(opencode.step_usage_evidence(opencode._json_event_prefix(raw)[0]),
                         STEP_USAGE)

    def test_step_finish_without_tokens_gives_none(self):
        raw = _events(STEP_START, TEXT, _step_finish())
        opencode.parse_json_events(raw)
        self.assertIsNone(opencode.step_usage_evidence(opencode._json_event_prefix(raw)[0]))

    def test_steps_exhausted_keeps_step_usage(self):
        raw = _events(*(_step_finish({'input': 10, 'output': 2}) for _ in range(3)))
        with self.assertRaises(opencode.DirectOpenCodeError) as caught:
            opencode.parse_json_events(raw, step_limit=3)
        self.assertEqual(caught.exception.direct_opencode_code, 'OPENCODE_STEPS_EXHAUSTED')
        self.assertEqual(caught.exception.provider_evidence, STEP_USAGE)

    def test_steps_exhausted_without_usage_gives_none(self):
        raw = _events(*(_step_finish() for _ in range(3)))
        with self.assertRaises(opencode.DirectOpenCodeError) as caught:
            opencode.parse_json_events(raw, step_limit=3)
        self.assertEqual(caught.exception.direct_opencode_code, 'OPENCODE_STEPS_EXHAUSTED')
        self.assertIsNone(caught.exception.provider_evidence)

    def test_conditions_as_themselves(self):
        cases = (
            ({'type': 'provider.free-limit'}, 'OPENCODE_FREE_LIMIT', QUOTA),
            ({'type': 'provider.rate-limit'}, 'OPENCODE_RATE_LIMITED', RATE),
            ({'type': 'unknown', 'status': 429}, 'OPENCODE_RATE_LIMITED', RATE),
            ({'type': 'provider.transport'}, 'OPENCODE_PROVIDER_FAILED', None),
            ({'type': 'provider.auth'}, 'OPENCODE_AUTH_REQUIRED', None),
        )
        for error, code, evidence in cases:
            raw = _events(STEP_START, {'type': 'error', 'sessionID': OC_SESSION,
                                       'error': error})
            with self.subTest(code=code):
                with self.assertRaises(opencode.DirectOpenCodeError) as caught:
                    opencode.parse_json_events(raw)
                self.assertEqual(caught.exception.direct_opencode_code, code)
                self.assertEqual(caught.exception.provider_evidence, evidence)
                self.assertEqual(opencode.condition_evidence(code), evidence)

    def test_other_refusals_carry_none(self):
        for code in ('OPENCODE_TIMEOUT', 'OPENCODE_CLEANUP_UNKNOWN', 'TOOL_DENIED',
                     'OPENCODE_OUTPUT_INVALID', 'OPENCODE_RUNTIME_UNAVAILABLE'):
            self.assertIsNone(opencode.condition_evidence(code))
            self.assertIsNone(opencode.DirectOpenCodeError(code).provider_evidence)


def _opencode_result(evidence=STEP_USAGE):
    return opencode.DirectResult(
        0, OC_SESSION, 'Edited the file.',
        {'prompt_tokens': 120, 'completion_tokens': 30, 'total_tokens': 150},
        'CLI_REPORTED', 'e' * 64, 1500, 'CONFIRMED', 'opencode v1.2.3',
        100, 1, '9' * 64, provider_evidence=evidence)


class DelegateEvidenceTest(unittest.TestCase):
    def test_opencode_execution_carries_route_evidence(self):
        result = _opencode_result()
        self.assertEqual(cli._direct_execution(result, evidence=True)['provider_evidence'],
                         STEP_USAGE)
        self.assertNotIn('provider_evidence', cli._direct_execution(result))
        refused = {**cli._direct_execution(result, status='UNKNOWN', evidence=True),
                   'usage_source': 'UNKNOWN'}
        # D6_SESSION_REFUSED keeps the parsed usage evidence.
        self.assertEqual(refused['provider_evidence'], STEP_USAGE)
        self.assertNotIn('provider_evidence', cli._direct_execution(
            _opencode_result(evidence=None), evidence=True))

    def test_opencode_steps_exhausted_refusal_keeps_evidence(self):
        raw = _events(*(_step_finish({'input': 10, 'output': 2}) for _ in range(3)))
        with self.assertRaises(opencode.DirectOpenCodeError) as caught:
            opencode.parse_json_events(raw, step_limit=3)
        execution = cli._with_provider_evidence(
            {'status': 'UNKNOWN'}, caught.exception.provider_evidence, 'opencode')
        self.assertEqual(execution['provider_evidence'], STEP_USAGE)

    def test_interruption_gives_none(self):
        saved = {}

        class Store:
            def compare_and_update(self, task_id, *, expected, developer):
                saved['developer'] = developer
                return {'developer': developer}

        developer = {'provider_profile': 'opencode'}
        cli._persist_opencode_interruption(
            Store(), 'task-1', developer, KeyboardInterrupt(), model=opencode.MODEL,
            expected_developer={}, result=_opencode_result())
        self.assertNotIn('provider_evidence', saved['developer']['execution'])

    def test_bind_check_accepts_valid_evidence_only(self):
        execution = cli._direct_execution(_opencode_result())
        developer = {'provider_profile': 'opencode', 'usage': {'total_tokens': 150},
                     'execution': execution}
        self.assertTrue(cli._store_accepts_direct_execution(developer))
        developer['execution'] = dict(execution, provider_evidence=STEP_USAGE)
        self.assertTrue(cli._store_accepts_direct_execution(developer))
        developer['execution'] = dict(execution, provider_evidence=CLI_USAGE)
        self.assertFalse(cli._store_accepts_direct_execution(developer))

    def test_condition_execution(self):
        for profile in ('claude', 'agy', 'gw-agent', 'opencode'):
            execution = cli._condition_execution(QUOTA, profile, 'BLOCKED')
            self.assertEqual(execution['provider_evidence'], QUOTA)
            self.assertEqual(set(execution) - {'provider_evidence'},
                             task_store._DIRECT_EXECUTION_FIELDS)
        self.assertIsNone(cli._condition_execution(RATE, 'claude', 'BLOCKED'))
        self.assertIsNone(cli._condition_execution(CLI_USAGE, 'claude', 'BLOCKED'))
        self.assertIsNone(cli._condition_execution(None, 'claude', 'BLOCKED'))

    def test_codex_and_company_gw_never_get_evidence(self):
        execution = {'status': 'DONE'}
        for profile in ('codex', 'company-gw'):
            for evidence in (CLI_USAGE, STEP_USAGE, QUOTA, RATE):
                self.assertIs(cli._with_provider_evidence(execution, evidence, profile),
                              execution)
                self.assertIsNone(cli._condition_execution(evidence, profile, 'UNKNOWN'))


def _legacy_execution(**changes):
    value = {'status': 'DONE', 'native_session_id': None, 'process_exit_code': 0,
             'stdout_sha256': 'e' * 64, 'elapsed_ms': 10, 'cleanup_status': 'CONFIRMED',
             'usage_source': 'CLI_REPORTED', 'source_bytes': None,
             'source_file_count': None, 'source_manifest_sha256': None}
    value.update(changes)
    return value


class TaskStoreEvidenceTest(unittest.TestCase):
    def test_legacy_execution_without_evidence_is_unchanged(self):
        execution = _legacy_execution()
        self.assertIs(task_store._execution_without_provider_evidence(execution, 'claude'),
                      execution)
        self.assertIsNone(task_store._execution_without_provider_evidence(None, 'claude'))

    def test_valid_shapes(self):
        cases = (('claude', CLI_USAGE), ('opencode', STEP_USAGE), ('agy', AGY_STATUS),
                 ('gw-agent', GATEWAY_USAGE), ('claude', QUOTA), ('agy', QUOTA),
                 ('gw-agent', QUOTA), ('opencode', QUOTA), ('opencode', RATE))
        for profile, evidence in cases:
            with self.subTest(profile=profile, evidence=evidence):
                execution = _legacy_execution(provider_evidence=dict(evidence))
                self.assertEqual(
                    task_store._execution_without_provider_evidence(execution, profile),
                    _legacy_execution())

    def test_invalid_shapes_refused(self):
        invalid = (
            ('claude', {}), ('claude', None), ('claude', 'CLI_USAGE'),
            ('claude', {'basis': 'CLI_USAGE'}),
            ('claude', {'basis': 'CLI_USAGE', 'accepted': False}),
            ('claude', {'basis': 'CLI_USAGE', 'accepted': 1}),
            ('claude', {'basis': 'CLI_USAGE', 'accepted': True, 'note': 'x'}),
            ('claude', {'basis': 'STEP_USAGE', 'accepted': True}),
            ('opencode', {'basis': 'CLI_USAGE', 'accepted': True}),
            ('agy', {'basis': 'GATEWAY_USAGE', 'accepted': True}),
            ('gw-agent', {'basis': 'AGY_STATUS', 'accepted': True}),
            ('claude', {'basis': 'EXIT_ZERO', 'accepted': True}),
            ('claude', {'condition': 'RATE_LIMITED'}),
            ('claude', {'condition': 'quota exhausted'}),
            ('claude', {'condition': 'TIMEOUT'}),
            ('claude', {'condition': 'QUOTA_EXHAUSTED', 'basis': 'CLI_USAGE'}),
            ('codex', {'basis': 'CLI_USAGE', 'accepted': True}),
            ('codex', QUOTA), ('company-gw', QUOTA),
            ('company-gw', {'basis': 'GATEWAY_USAGE', 'accepted': True}),
        )
        for profile, evidence in invalid:
            with self.subTest(profile=profile, evidence=evidence):
                self.assertFalse(task_store.valid_provider_evidence(evidence, profile))
                with self.assertRaises(LLMToolError) as caught:
                    task_store._execution_without_provider_evidence(
                        _legacy_execution(provider_evidence=evidence), profile)
                self.assertEqual(caught.exception.task_store_error_code,
                                 'TASK_STORE_INVALID')

    def test_evidence_refused_beside_launch_or_diff_fields(self):
        for extra in ({'child_pid': 1, 'child_start_time': 'Mon 1', 'child_pgid': 1},
                      {'diff_sha256': 'e' * 64, 'diff_base_sha': HEAD},
                      {'native_session_binding': 'BOUND'}):
            with self.subTest(extra=sorted(extra)):
                with self.assertRaises(LLMToolError):
                    task_store._execution_without_provider_evidence(
                        _legacy_execution(provider_evidence=dict(CLI_USAGE), **extra),
                        'claude')


if __name__ == '__main__':
    unittest.main()
