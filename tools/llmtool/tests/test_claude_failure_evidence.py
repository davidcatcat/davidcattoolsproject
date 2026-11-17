"""#2275: Claude CLI failure evidence kept on BLOCKED developer records.

Every CLI output here is fake. The screen-refusal tests patch the detector
with a plain marker word (test data rule, Owner #763 A point 2); no test
contains a credential-shaped string.
"""

import contextlib
import copy
import io
import json
import unittest
from unittest import mock
import uuid

from llmtool.developer_routes.base import RouteError, RouteResult
from llmtool.developer_routes.claude import ClaudeRoute, cli_result_fields
from llmtool.errors import LLMToolError
from llmtool import task_store as task_store_module
from llmtool import task_submission_cli as cli_module

try:
    import test_claude_route_wiring
except ImportError:
    from tests import test_claude_route_wiring

try:
    import review_trust_fixture
    import worker_policy_fixture
except ImportError:
    from tests import review_trust_fixture
    from tests import worker_policy_fixture


def setUpModule():
    review_trust_fixture.start_module()
    worker_policy_fixture.start_module()


def tearDownModule():
    worker_policy_fixture.stop_module()
    review_trust_fixture.stop_module()


# A plain marker the patched detector treats as a screen hit; not credential-shaped.
SCREEN_MARKER = 'ZETA-SCREEN-MARKER-2275'
RESULT_TEXT = 'Quokka lantern zeta halted\n  before any edit; nothing to change.'
RESULT_HEAD = 'Quokka lantern zeta halted before any edit; nothing to change.'


def _marker_detector(value):
    return type(value) is str and SCREEN_MARKER in value


def _facts(exit_code=1):
    return {'process_exit_code': exit_code, 'stdout_sha256': 'a' * 64,
            'elapsed_ms': 42, 'cleanup_status': 'CONFIRMED'}


def _cli_stdout(**changes):
    obj = {'type': 'result', 'subtype': 'error_during_execution', 'is_error': True,
           'num_turns': 7, 'duration_ms': 4321, 'result': RESULT_TEXT,
           'session_id': str(uuid.uuid4())}
    obj.update(changes)
    return json.dumps(obj).encode('utf-8')


def _evidence(**changes):
    value = {'subtype': 'error_during_execution', 'is_error': True, 'num_turns': 7,
             'duration_ms': 4321, 'process_exit_code': 1, 'result_head': RESULT_HEAD}
    value.update(changes)
    return value


def _blocked_developer(**changes):
    developer = {'provider_profile': 'claude',
                 'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
                 'state': 'BLOCKED', 'error_code': 'CLAUDE_STATUS_FAILED',
                 'execution': None, 'cli_result_evidence': _evidence()}
    developer.update(changes)
    return developer


class CliResultFieldsTests(unittest.TestCase):
    def test_parse_keeps_typed_fields_even_when_output_is_invalid(self):
        route = ClaudeRoute({})
        with self.assertRaises(RouteError) as caught:
            route.parse_output(_cli_stdout(session_id='not-a-session'), b'', '')
        self.assertEqual(caught.exception.code, 'CLAUDE_OUTPUT_INVALID')
        self.assertEqual(route.cli_result, {
            'subtype': 'error_during_execution', 'is_error': True, 'num_turns': 7,
            'duration_ms': 4321, 'result': RESULT_TEXT})

    def test_parse_resets_fields_for_non_json_output(self):
        route = ClaudeRoute({})
        route.parse_output(_cli_stdout(), b'', '')
        self.assertIsNotNone(route.cli_result)
        with self.assertRaises(RouteError):
            route.parse_output(b'plain text, not JSON', b'', '')
        self.assertIsNone(route.cli_result)

    def test_wrong_types_give_none(self):
        fields = cli_result_fields({'subtype': 3, 'is_error': 'yes', 'num_turns': True,
                                    'duration_ms': 1.5, 'result': ['x']})
        self.assertEqual(fields, {'subtype': None, 'is_error': None, 'num_turns': None,
                                  'duration_ms': None, 'result': None})
        self.assertIsNone(cli_result_fields(['not', 'an', 'object']))


class ResultHeadTests(unittest.TestCase):
    SENT = ('Unrelated sent prompt text for this unit test only.',)

    def test_head_is_one_bounded_line(self):
        self.assertEqual(cli_module._claude_result_head(RESULT_TEXT, self.SENT), RESULT_HEAD)
        long_text = ' '.join('word%d' % index for index in range(2000))
        head = cli_module._claude_result_head(long_text, self.SENT)
        self.assertLessEqual(len(head), task_store_module.CLI_RESULT_HEAD_MAX_CHARS)
        self.assertTrue(long_text.startswith(head))

    def test_control_characters_empty_and_non_text_give_none(self):
        for text in ('halted\x1b[31m', 'halted\x00', 'halted\x85', '   \n\t ', '', None, 7):
            with self.subTest(text=text):
                self.assertIsNone(cli_module._claude_result_head(text, self.SENT))

    def test_echo_of_sent_text_or_missing_sent_text_gives_none(self):
        sent = ('Prompt says: quokka lantern zeta halted before any edit.',)
        self.assertIsNone(cli_module._claude_result_head(RESULT_TEXT, sent))
        self.assertIsNone(cli_module._claude_result_head(RESULT_TEXT, ()))

    def test_screen_hit_anywhere_drops_the_head(self):
        # The marker sits past the head cut, so the whole text screen must catch it.
        text = 'x ' * 300 + SCREEN_MARKER
        with mock.patch.object(cli_module, '_emitted_sensitive', _marker_detector):
            self.assertIsNone(cli_module._claude_result_head(text, self.SENT))
            self.assertIsNone(cli_module._claude_result_head(
                SCREEN_MARKER + ' stopped', self.SENT))
            # Real behaviour stays: clean text still passes the patched screen.
            self.assertEqual(cli_module._claude_result_head(RESULT_TEXT, self.SENT),
                             RESULT_HEAD)


class StoreValidatorTests(unittest.TestCase):
    def test_valid_evidence_and_nullable_values(self):
        self.assertTrue(task_store_module._valid_cli_result_evidence(_blocked_developer()))
        for key in ('subtype', 'is_error', 'num_turns', 'duration_ms', 'result_head'):
            with self.subTest(null=key):
                self.assertTrue(task_store_module._valid_cli_result_evidence(
                    _blocked_developer(cli_result_evidence=_evidence(**{key: None}))))
        self.assertTrue(task_store_module._valid_cli_result_evidence(_blocked_developer(
            error_code='CLAUDE_NO_CHANGES',
            cli_result_evidence=_evidence(subtype='success', is_error=False,
                                          process_exit_code=0))))

    def test_malformed_evidence_is_refused(self):
        too_long = 'a' * (task_store_module.CLI_RESULT_HEAD_MAX_CHARS + 1)
        cases = {
            'bool turns': _evidence(num_turns=True),
            'negative turns': _evidence(num_turns=-1),
            'turns over cap': _evidence(num_turns=task_store_module.CLI_RESULT_MAX_TURNS + 1),
            'float duration': _evidence(duration_ms=1.0),
            'duration over cap': _evidence(
                duration_ms=task_store_module.CLI_RESULT_MAX_DURATION_MS + 1),
            'exit over cap': _evidence(process_exit_code=256),
            'exit under cap': _evidence(process_exit_code=-256),
            'bool exit': _evidence(process_exit_code=False),
            'int is_error': _evidence(is_error=1),
            'upper subtype': _evidence(subtype='Success'),
            'long subtype': _evidence(subtype='a' * 65),
            'head too long': _evidence(result_head=too_long),
            'head newline': _evidence(result_head='two\nlines'),
            'head padded': _evidence(result_head=' padded'),
            'head empty': _evidence(result_head=''),
            'head format char': _evidence(result_head='a​b'),
            'extra key': dict(_evidence(), stderr='x'),
            'missing key': {k: v for k, v in _evidence().items() if k != 'subtype'},
            'all null': dict.fromkeys(_evidence()),
            'not a dict': ['success'],
        }
        for name, evidence in cases.items():
            with self.subTest(case=name):
                self.assertFalse(task_store_module._valid_cli_result_evidence(
                    _blocked_developer(cli_result_evidence=evidence)))

    def test_only_blocked_claude_cli_records(self):
        cases = {
            'other provider': {'provider_profile': 'agy'},
            'other semantics': {'backend_session_semantics': 'NO_NATIVE_SESSION_ID_EXPOSED'},
            'responded': {'state': 'DEVELOPER_RESPONDED', 'error_code': None},
            'other code': {'error_code': 'AGY_STATUS_FAILED'},
            'owner denied': {'error_code': 'CLAUDE_DISPATCH_DENIED'},
        }
        for name, changes in cases.items():
            with self.subTest(case=name):
                self.assertFalse(task_store_module._valid_cli_result_evidence(
                    _blocked_developer(**changes)))

    def test_store_screen_refuses_a_flagged_head(self):
        developer = _blocked_developer(
            cli_result_evidence=_evidence(result_head='halted ' + SCREEN_MARKER))
        self.assertTrue(task_store_module._valid_cli_result_evidence(developer))
        with mock.patch.object(task_store_module, '_emitted_sensitive', _marker_detector):
            self.assertFalse(task_store_module._valid_cli_result_evidence(developer))
            self.assertTrue(task_store_module._valid_cli_result_evidence(_blocked_developer()))


class DelegateEvidenceTests(unittest.TestCase):
    def _run(self, runner_error, *, stdout=None, stderr=b'', extra_patches=(),
             result_factory=None):
        case = test_claude_route_wiring.ClaudeRouteWiringTests(
            methodName='test_route_failure_records_no_delivery')
        cli, store, args, _reg, _evals, _prompts, _reqs, patches, runner = case._setup(
            runner_error=runner_error)

        def parsing_runner(route, request, **kwargs):
            if stdout is not None:
                try:
                    route.parse_output(stdout, stderr, '')
                except RouteError:
                    pass
            if result_factory is not None:
                return result_factory(request)
            return runner(route, request, **kwargs)

        with contextlib.ExitStack() as stack:
            for item in (*patches, *extra_patches):
                stack.enter_context(item)
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            status = cli.run_task_claude_delegate(args, store=store, route_runner=parsing_runner)
        self.assertEqual(status, 1)
        return store, args.task_id

    def _failure(self, code, exit_code=1):
        error = RouteError(code)
        error.execution_facts = _facts(exit_code)
        return error

    def test_listed_failure_paths_store_the_evidence(self):
        cases = (
            ('CLAUDE_STATUS_FAILED', _cli_stdout(), _evidence()),
            ('CLAUDE_QUOTA_EXHAUSTED', _cli_stdout(api_error_status=429), _evidence()),
            ('CLAUDE_OUTPUT_INVALID', _cli_stdout(session_id='not-a-session'), _evidence()),
            ('CLAUDE_OUTPUT_INVALID', b'plain text, not JSON',
             dict(dict.fromkeys(_evidence()), process_exit_code=1)),
            ('CLAUDE_MODEL_UNVERIFIED',
             _cli_stdout(subtype='success', is_error=False, num_turns=2, duration_ms=900),
             _evidence(subtype='success', is_error=False, num_turns=2, duration_ms=900,
                       process_exit_code=0)),
            ('CLAUDE_EXIT_NONZERO', _cli_stdout(num_turns=True, duration_ms=-5),
             _evidence(num_turns=None, duration_ms=None, process_exit_code=3)),
        )
        for code, stdout, expected in cases:
            with self.subTest(code=code, expected=expected):
                store, task_id = self._run(
                    self._failure(code, expected['process_exit_code']), stdout=stdout)
                developer = store.get(task_id)['developer']
                self.assertEqual(developer['state'], 'BLOCKED')
                self.assertEqual(developer['error_code'], code)
                self.assertEqual(developer['cli_result_evidence'], expected)

    def test_credential_screen_hit_in_cli_output_is_never_stored(self):
        stdout = _cli_stdout(result='Halted: ' + SCREEN_MARKER + ' was in the reply.')
        with mock.patch.object(cli_module, '_emitted_sensitive', _marker_detector):
            store, task_id = self._run(self._failure('CLAUDE_STATUS_FAILED'), stdout=stdout)
        record = store.get(task_id)
        self.assertEqual(record['developer']['cli_result_evidence'],
                         _evidence(result_head=None))
        self.assertNotIn(SCREEN_MARKER, json.dumps(record))

    def test_no_changes_stores_the_evidence(self):
        stdout = _cli_stdout(subtype='success', is_error=False, num_turns=3,
                             duration_ms=1234)
        session = json.loads(stdout)['session_id']

        def result_factory(request):
            return RouteResult('claude', 'DONE', '', session, request['model'],
                               request['effort'], None, (), 0, 'b' * 64, 5,
                               'CONFIRMED', {}, None)

        store, task_id = self._run(
            self._failure('CLAUDE_STATUS_FAILED'), stdout=stdout,
            result_factory=result_factory,
            extra_patches=(
                mock.patch.object(cli_module.task_completion, '_worktree_diff',
                                  return_value=((), b'')),
                mock.patch.object(cli_module, '_claude_committed_carry', return_value=None)))
        developer = store.get(task_id)['developer']
        self.assertEqual(developer['error_code'], 'CLAUDE_NO_CHANGES')
        self.assertIn('no_changes_diagnosis', developer)
        self.assertEqual(developer['cli_result_evidence'], _evidence(
            subtype='success', is_error=False, num_turns=3, duration_ms=1234,
            process_exit_code=0))

    def test_pre_spawn_refusal_stores_no_evidence(self):
        store, task_id = self._run(RouteError('CLAUDE_REQUEST_INVALID'))
        self.assertNotIn('cli_result_evidence', store.get(task_id)['developer'])

    def test_store_loads_legacy_and_refuses_malformed_records(self):
        store, task_id = self._run(self._failure('CLAUDE_STATUS_FAILED'),
                                   stdout=_cli_stdout())
        stored = store.get(task_id)
        task_store_module._validate(copy.deepcopy(stored))
        legacy = copy.deepcopy(stored)
        del legacy['developer']['cli_result_evidence']
        legacy['record_sha256'] = task_store_module._digest(legacy)
        task_store_module._validate(legacy)
        for key, value in (('num_turns', True), ('result_head', 'a\nb'),
                           ('process_exit_code', 999), ('subtype', 'Bad Subtype')):
            with self.subTest(key=key):
                bad = copy.deepcopy(stored)
                bad['developer']['cli_result_evidence'][key] = value
                bad['record_sha256'] = task_store_module._digest(bad)
                with self.assertRaises(LLMToolError):
                    task_store_module._validate(bad)


if __name__ == '__main__':
    unittest.main()
