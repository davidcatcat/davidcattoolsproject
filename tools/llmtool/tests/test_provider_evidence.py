"""Design c6876861575 items 1-2: provider_evidence from parsed provider output.

Fixtures are scrubbed shapes of real provider output: fixed ids, digests and
counts only. No credential-shaped value appears anywhere (Owner #763 A).
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
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

    def test_signal_termination_with_quota_text_gives_none(self):
        # Review F1: a negative (signal) exit is termination, not a condition.
        # The classifier code is unchanged; only the evidence is withheld.
        for code in (-15, -9, -2):
            with self.subTest(code=code):
                with self.assertRaises(RouteError) as caught:
                    self.run_route(self.route(), _runner(code, b'', b'usage limit reached'),
                                   model=CLAUDE_MODEL)
                self.assertEqual(caught.exception.code, 'CLAUDE_QUOTA_EXHAUSTED')
                self.assertIsNone(caught.exception.provider_evidence)

    def test_signal_termination_after_result_gives_none(self):
        with self.assertRaises(RouteError) as caught:
            self.run_route(self.route(), _runner(-15, _claude_result()), model=CLAUDE_MODEL)
        self.assertEqual(caught.exception.code, 'CLAUDE_INTERRUPTED')
        self.assertIsNone(caught.exception.provider_evidence)

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

    def test_signal_termination_with_quota_status_gives_none(self):
        with self.assertRaises(RouteError) as caught:
            self.run_route(self.route(),
                           _runner(-15, _agy_result(status='RESOURCE_EXHAUSTED'), log=self.LOG),
                           model=AGY_MODEL, log_path='agy.log')
        self.assertEqual(caught.exception.code, 'AGY_QUOTA_EXHAUSTED')
        self.assertIsNone(caught.exception.provider_evidence)


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

    def test_signal_termination_with_http_429_gives_none(self):
        fixture = _gw_result(status='MODEL_ERROR', code='HTTP_429',
                             usage={'status': 'UNKNOWN'})
        with self.assertRaises(RouteError) as caught:
            self.run_route(_GwFixture(fixture), _runner(-15, b''), model=GW_MODEL)
        self.assertIsNone(caught.exception.provider_evidence)

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

    def test_nonzero_exit_conditions_need_a_positive_exit(self):
        # Review F1: run() takes the nonzero-exit evidence from this helper; a
        # signal (negative) exit is termination and gives none.
        self.assertEqual(opencode.exit_condition_evidence(1, 'OPENCODE_FREE_LIMIT'), QUOTA)
        self.assertEqual(opencode.exit_condition_evidence(2, 'OPENCODE_RATE_LIMITED'), RATE)
        for returncode in (-15, -9, -2, 0, None, True, '1'):
            for code in ('OPENCODE_FREE_LIMIT', 'OPENCODE_RATE_LIMITED'):
                with self.subTest(returncode=returncode, code=code):
                    self.assertIsNone(opencode.exit_condition_evidence(returncode, code))
        self.assertIsNone(opencode.exit_condition_evidence(1, 'OPENCODE_PROVIDER_FAILED'))


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
    """Per-profile matrix of the strict shape check TaskStore._validate calls."""

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


# Review F2: real TaskStore records, written and loaded through TaskStore.

def _refused_execution(**changes):
    """The execution an OpenCode D6_SESSION_REFUSED outcome persists."""
    value = {'status': 'UNKNOWN', 'native_session_id': OC_SESSION, 'process_exit_code': 0,
             'stdout_sha256': 'e' * 64, 'elapsed_ms': 1500, 'cleanup_status': 'CONFIRMED',
             'usage_source': 'UNKNOWN', 'source_bytes': 100, 'source_file_count': 1,
             'source_manifest_sha256': '9' * 64}
    value.update(changes)
    return value


def _developer(execution, **changes):
    value = {
        'assignment_id': task_store.new_id(), 'selection_source': 'USER',
        'state': 'DEVELOPER_OUTCOME_UNKNOWN', 'provider_profile': 'opencode',
        'provider_type': 'opencode-cli', 'model': opencode.MODEL,
        'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
        'session_id': 'dev-' + task_store.new_id(), 'source_sha': HEAD,
        'source_sha_binding': 'D6_REGISTRY_OBSERVED',
        'read_paths': ['x.txt'], 'write_paths': ['x.txt'],
        'acceptance': ['Edit x.txt.'], 'context_sha256': '5' * 64,
        'egress_basis': 'Synthetic fixture text only.', 'input_bytes': 100,
        'max_tokens': None, 'artifact_format': 'unified_diff', 'artifact': None,
        'artifact_sha256': None, 'usage': None,
        'error_code': 'OPENCODE_D6_SESSION_REFUSED', 'execution': execution,
        'created_at': '2026-11-14T00:00:00Z',
    }
    value.update(changes)
    return value


def _codex_developer(execution):
    return _developer(execution, provider_profile='codex', provider_type='codex-cli',
                      model=None, backend_session_semantics='NO_NATIVE_SESSION_ID_EXPOSED',
                      error_code='CODEX_PROVIDER_FAILED')


def _task_record(developer):
    request = 'Edit x.txt for the fixture.'
    record = cli._record(
        task_store.new_id(), task_store.new_id(), 'pm-fixture', 'company-gw', request,
        state='PM_RESPONDED', provider_type='openai-compatible', model=GW_MODEL,
        backend='EPHEMERAL_SINGLE_CALL',
        request_sha=hashlib.sha256(request.encode('utf-8')).hexdigest())
    record.update(response='PM planned one edit.', developer=developer,
                  pm_route_binding={'type': 'openai-compatible', 'model': GW_MODEL,
                                    'base_url': None})
    return record


class _StoreCase(unittest.TestCase):
    """A private TaskStore root (0700) in a temporary directory."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store_root = Path(self._tmp.name).resolve() / 'tasks'
        os.mkdir(self.store_root, 0o700)
        os.chmod(self.store_root, 0o700)
        self.store = task_store.TaskStore(self.store_root)

    def write(self, record):
        """Write through TaskStore's validated write path, then load it back."""
        self.store._write(record, create=True)
        return self.store.get(record['task_id'])

    def plant(self, record):
        """Place stored bytes as an older writer left them (no write-time check)."""
        value = dict(record)
        value['record_sha256'] = task_store._digest(value)
        path = self.store_root / task_store.TaskStore._name(value['task_id'])
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(task_store._json_bytes(value))
        os.chmod(path, 0o600)
        return value['task_id']

    def assert_invalid(self, call):
        with self.assertRaises(LLMToolError) as caught:
            call()
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_INVALID')


class TaskStoreRecordEvidenceTest(_StoreCase):
    def test_legacy_record_without_evidence_loads(self):
        task_id = self.plant(_task_record(_developer(_refused_execution())))
        loaded = self.store.get(task_id)
        self.assertEqual(loaded['developer']['execution'], _refused_execution())
        self.assertNotIn('provider_evidence', loaded['developer']['execution'])

    def test_valid_evidence_is_written_and_loaded(self):
        for evidence in (STEP_USAGE, QUOTA, RATE):
            with self.subTest(evidence=evidence):
                loaded = self.write(_task_record(_developer(
                    _refused_execution(provider_evidence=dict(evidence)))))
                self.assertEqual(loaded['developer']['execution']['provider_evidence'],
                                 evidence)
                planted = self.plant(_task_record(_developer(
                    _refused_execution(provider_evidence=dict(evidence)))))
                self.assertEqual(
                    self.store.get(planted)['developer']['execution']['provider_evidence'],
                    evidence)

    def test_invalid_evidence_refused_on_write_and_on_load(self):
        invalid = (
            {}, None, 'STEP_USAGE', {'basis': 'STEP_USAGE'},
            {'basis': 'STEP_USAGE', 'accepted': False},
            {'basis': 'STEP_USAGE', 'accepted': 1},
            {'basis': 'STEP_USAGE', 'accepted': True, 'note': 'x'},
            {'basis': 'CLI_USAGE', 'accepted': True},
            {'basis': 'EXIT_ZERO', 'accepted': True},
            {'condition': 'TIMEOUT'}, {'condition': 'quota exhausted'},
            {'condition': 'QUOTA_EXHAUSTED', 'basis': 'STEP_USAGE'},
        )
        for evidence in invalid:
            with self.subTest(evidence=evidence):
                record = _task_record(_developer(
                    _refused_execution(provider_evidence=copy.deepcopy(evidence))))
                self.assert_invalid(lambda: self.store._write(record, create=True))
                task_id = self.plant(record)
                self.assert_invalid(lambda: self.store.get(task_id))

    def test_codex_record_never_carries_evidence(self):
        execution = _refused_execution(native_session_id=None)
        # Control: the same Codex record without evidence is valid.
        self.write(_task_record(_codex_developer(execution)))
        for evidence in (CLI_USAGE, STEP_USAGE, QUOTA, RATE):
            with self.subTest(evidence=evidence):
                record = _task_record(_codex_developer(
                    dict(execution, provider_evidence=dict(evidence))))
                self.assert_invalid(lambda: self.store._write(record, create=True))
                task_id = self.plant(record)
                self.assert_invalid(lambda: self.store.get(task_id))


class _DelegateStore:
    """In-memory stand-in for the delegate's TaskStore calls; keeps every write."""

    def __init__(self, record):
        self.record = copy.deepcopy(record)
        self.writes = []

    def get(self, task_id):
        return copy.deepcopy(self.record)

    def _apply(self, expected, changes):
        if expected is not None and any(self.record.get(key) != value
                                        for key, value in expected.items()):
            raise task_store.TaskStoreConflict()
        self.writes.append(copy.deepcopy(changes))
        self.record.update(copy.deepcopy(changes))
        return copy.deepcopy(self.record)

    def d6_bind_compare_and_update(self, task_id, *, executor_task_id, expected, **changes):
        return self._apply(expected, changes)

    def update(self, task_id, **changes):
        return self._apply(None, changes)

    def compare_and_update(self, task_id, *, expected, **changes):
        return self._apply(expected, changes)


class OpenCodeDelegateSessionRefusalTest(_StoreCase):
    """The delegate's own D6_SESSION_REFUSED path keeps parsed STEP_USAGE."""

    DIFF = (b'diff --git a/x.txt b/x.txt\n--- a/x.txt\n+++ b/x.txt\n'
            b'@@ -1 +1 @@\n-old\n+new\n')

    def run_delegate(self, result):
        task = _task_record(None)
        task['record_sha256'] = task_store._digest(task)
        # A D6 whose session is UNKNOWN takes none: the real refusal trigger.
        registration = SimpleNamespace(
            task_id='provider-evidence-fixture', trusted_agent_id='opencode-developer',
            session_ref='fixture-session-ref', native_session_id='UNKNOWN',
            root='/nonexistent/d6', common_dir='/nonexistent/d6/.git', branch='main')
        snapshot = SimpleNamespace(root=registration.root, common_dir=registration.common_dir,
                                   branch='main', head=HEAD)
        args = SimpleNamespace(
            task_id=task['task_id'], timeout=None, context_stdin=False,
            selection_source='USER', acceptance=['Edit x.txt.'], read_path=['x.txt'],
            write_path=['x.txt'], egress_basis='Synthetic fixture text only.',
            repair_packet=None)
        store = _DelegateStore(task)
        patches = (
            mock.patch.object(cli, '_delegate_timeout', return_value=600),
            mock.patch.object(cli, '_opencode_developer_prompt', return_value='Edit x.txt.'),
            mock.patch.object(cli, '_opencode_developer_profile',
                              return_value=(opencode.MODEL, 10)),
            mock.patch.object(cli, '_developer_card_prefix', return_value=('', POLICY)),
            mock.patch.object(cli, '_direct_result_callback', return_value={}),
            mock.patch.object(cli.task_completion, 'bind_executor_task',
                              return_value=(registration, snapshot)),
            mock.patch.object(cli, '_bootstrap_registered_d6'),
            mock.patch.object(cli, '_refresh_d6_binding_after_bootstrap',
                              return_value=registration),
            mock.patch.object(cli.opencode_direct_developer, 'run', return_value=result),
            mock.patch.object(cli, '_record_skill_delivery'),
            mock.patch.object(cli.task_completion, 'collect_git_snapshot',
                              return_value=snapshot),
            mock.patch.object(cli.task_completion, '_worktree_diff',
                              return_value=(['x.txt'], self.DIFF)),
            mock.patch.object(cli, '_revision_sources', return_value=lambda path: 'old\n'),
            mock.patch.object(cli, '_developer_output_rejection', return_value=None),
            mock.patch.object(cli, '_print'),
        )
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            code = cli.run_task_opencode_delegate(args, store=store,
                                                  d6_binding=(registration, snapshot))
        return code, store

    def test_session_refusal_persists_step_usage(self):
        code, store = self.run_delegate(_opencode_result())
        self.assertEqual(code, 1)
        developer = store.writes[-1]['developer']
        self.assertEqual(store.record['developer'], developer)
        self.assertEqual(developer['state'], 'DEVELOPER_OUTCOME_UNKNOWN')
        self.assertEqual(developer['error_code'], 'OPENCODE_D6_SESSION_REFUSED')
        self.assertIsNone(developer['usage'])
        self.assertIsNone(developer['artifact'])
        execution = developer['execution']
        self.assertEqual(execution['usage_source'], 'UNKNOWN')
        self.assertEqual(execution['provider_evidence'], STEP_USAGE)
        # The persisted execution passes real TaskStore validation and loads back.
        loaded = self.write(_task_record(_developer(execution)))
        self.assertEqual(loaded['developer']['execution'], execution)

    def test_session_refusal_without_parsed_usage_persists_none(self):
        code, store = self.run_delegate(_opencode_result(evidence=None))
        self.assertEqual(code, 1)
        developer = store.writes[-1]['developer']
        self.assertEqual(developer['error_code'], 'OPENCODE_D6_SESSION_REFUSED')
        self.assertNotIn('provider_evidence', developer['execution'])


if __name__ == '__main__':
    unittest.main()
