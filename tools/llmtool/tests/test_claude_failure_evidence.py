"""#2275: non-secret evidence for Claude route failures and CLAUDE_NO_CHANGES.

Test data rule (Owner #763 A): no credential-shaped strings appear here. The
sensitive screen is injected (monkeypatched) to flag a plain marker string, and
the real screening, storing and printing behaviour is then checked.
"""

import contextlib
import copy
import hashlib
import io
import json
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid

from llmtool import task_store as task_store_module
from llmtool import task_submission_cli as cli_module
from llmtool import worker_dispatch
from llmtool.developer_routes.base import RouteError, RouteResult
from llmtool.errors import LLMToolError
from llmtool.task_submission_cli import _claude_failure_evidence, _claude_sent_texts

try:
    import test_claude_route_wiring
    import test_task_store
except ImportError:
    from tests import test_claude_route_wiring
    from tests import test_task_store

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


# A plain marker string. The injected screen below treats it as sensitive; it is
# not credential-shaped and is never a real credential (Owner #763 A).
MARKER = 'SYNTHETIC-SCREEN-MARKER-QZ'

PROMPT = ('You are the Developer. Please update the widget renderer module and keep '
          'the colour palette stable.')
PACKET = {'goal': 'Rename the helper in billing code', 'acceptance': ['tests pass']}
SENT = _claude_sent_texts(PROMPT, PACKET)

# Fixed text of the real Claude delegate prompt (task_submission_cli).
REAL_PROMPT_FRAGMENT = 'registered D6 worktree'


def _flagging_screen(real):
    """An injected screen: the marker is sensitive, everything else is screened as usual."""
    def screen(text, *args, **kwargs):
        if type(text) is str and MARKER in text:
            return True
        return real(text, *args, **kwargs)
    return screen


def _raw(**overrides):
    raw = {'type': 'result', 'subtype': 'error_max_turns', 'is_error': True,
           'num_turns': 7, 'duration_ms': 1234,
           'result': 'Reached the maximum number of turns.',
           'session_id': str(uuid.uuid4())}
    raw.update(overrides)
    return raw


class FailureEvidenceBuilderTests(unittest.TestCase):
    def test_allowlisted_subtype_and_fields_kept(self):
        evidence = _claude_failure_evidence(_raw(), SENT, 1)
        self.assertEqual(evidence, {
            'subtype': 'error_max_turns', 'is_error': True, 'num_turns': 7,
            'duration_ms': 1234, 'exit_code': 1,
            'message': 'Reached the maximum number of turns.'})
        self.assertTrue(task_store_module.valid_failure_evidence(evidence))
        for subtype in sorted(task_store_module.CLAUDE_RESULT_SUBTYPES):
            with self.subTest(subtype=subtype):
                self.assertEqual(
                    _claude_failure_evidence(_raw(subtype=subtype), SENT, 1)['subtype'],
                    subtype)

    def test_unknown_subtype_stored_as_other(self):
        for subtype in ('error_new_kind', 'SUCCESS', '', 'success ', None, 3):
            with self.subTest(subtype=subtype):
                evidence = _claude_failure_evidence(_raw(subtype=subtype), SENT, 1)
                self.assertEqual(evidence['subtype'], 'OTHER')

    def test_prompt_text_in_subtype_never_stored(self):
        for subtype in (PROMPT, PACKET['goal'], 'widget renderer'):
            with self.subTest(subtype=subtype):
                evidence = _claude_failure_evidence(_raw(subtype=subtype), SENT, 1)
                self.assertEqual(evidence['subtype'], 'OTHER')
                self.assertNotIn(subtype, json.dumps(evidence))

    def test_short_prompt_fragment_in_extra_text_dropped(self):
        for message in ('Failed after r module and then stopped',
                        'Stopped early while editing the WIDGET   Renderer files today',
                        'Could not rename the helper in billing code safely'):
            with self.subTest(message=message):
                evidence = _claude_failure_evidence(_raw(result=message), SENT, 1)
                self.assertIsNone(evidence['message'])
                self.assertEqual(evidence['subtype'], 'error_max_turns')

    def test_overlap_below_twelve_characters_kept(self):
        # 'r module an' is 11 normalized characters of sent text.
        evidence = _claude_failure_evidence(_raw(result='Failed: r module an!'), SENT, 1)
        self.assertEqual(evidence['message'], 'Failed: r module an!')

    def test_exact_echo_dropped(self):
        for message in (PROMPT, PACKET['goal'], 'tests pass', PROMPT.upper()):
            with self.subTest(message=message):
                evidence = _claude_failure_evidence(_raw(result=message), SENT, 1)
                self.assertIsNone(evidence['message'])

    def test_unrelated_error_message_kept(self):
        message = 'Internal server error while streaming output'
        evidence = _claude_failure_evidence(
            _raw(subtype='error_during_execution', result='intro line\n' + message), SENT, 2)
        self.assertEqual(evidence['message'], message)
        self.assertEqual(evidence['exit_code'], 2)

    def test_message_is_cut_and_bounded(self):
        message = ('word ' * 100).strip()
        evidence = _claude_failure_evidence(_raw(result=message), SENT, 1)
        self.assertEqual(evidence['message'], ('word ' * 40).rstrip())
        self.assertLessEqual(len(evidence['message']),
                             task_store_module.FAILURE_EVIDENCE_MESSAGE_MAX_CHARS)

    def test_marker_never_stored_with_injected_screen(self):
        screen = _flagging_screen(cli_module._emitted_sensitive)
        with mock.patch.object(cli_module, '_emitted_sensitive', screen):
            evidence = _claude_failure_evidence(
                _raw(result=f'Run failed near {MARKER} in output'), SENT, 1)
        self.assertIsNone(evidence['message'])
        self.assertNotIn(MARKER, json.dumps(evidence))

    def test_control_characters_and_missing_sent_text_drop_message(self):
        self.assertIsNone(_claude_failure_evidence(
            _raw(result='bad\x1bline'), SENT, 1)['message'])
        self.assertIsNone(_claude_failure_evidence(_raw(), None, 1)['message'])
        self.assertIsNone(_claude_failure_evidence(_raw(), (), 1)['message'])
        self.assertIsNone(_claude_failure_evidence(_raw(result=None), SENT, 1)['message'])

    def test_malformed_numbers_are_unknown(self):
        evidence = _claude_failure_evidence(
            _raw(is_error='yes', num_turns=True, duration_ms=-5), SENT, None)
        self.assertEqual(evidence['is_error'], 'UNKNOWN')
        self.assertEqual(evidence['num_turns'], 'UNKNOWN')
        self.assertEqual(evidence['duration_ms'], 'UNKNOWN')
        self.assertEqual(evidence['exit_code'], 'UNKNOWN')
        self.assertEqual(_claude_failure_evidence(_raw(), SENT, False)['exit_code'], 'UNKNOWN')
        self.assertEqual(_claude_failure_evidence(_raw(), SENT, 999)['exit_code'], 'UNKNOWN')
        self.assertIsNone(_claude_failure_evidence('not a dict', SENT, 1))


def _evidence(**overrides):
    value = {'subtype': 'error_during_execution', 'is_error': True, 'num_turns': 3,
             'duration_ms': 4567, 'exit_code': 1,
             'message': 'Internal server error while streaming output'}
    value.update(overrides)
    return value


class FailureEvidenceStoreTests(unittest.TestCase):
    def setUp(self):
        self.case = test_task_store.TaskStoreTests(
            methodName='test_claude_cli_accepts_blocked_isolation_record')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.store = self.case.store
        self.task = self.case._claude_cli_task()

    def _new_task(self):
        """A fresh PENDING Claude CLI Task, so each write is one PENDING -> BLOCKED step."""
        self.task = self.case._claude_cli_task()

    def _write(self, **changes):
        developer = self.store.get(self.task['task_id'])['developer']
        blocked = dict(developer, state='BLOCKED', provider_type='claude-cli',
                       model='claude-test-1', error_code='CLAUDE_STATUS_FAILED',
                       execution=None, artifact=None, artifact_sha256=None)
        blocked.update(changes)
        current = self.store.get(self.task['task_id'])
        with mock.patch.object(worker_dispatch, 'authorize_task_store_change',
                               return_value=None):
            return self.store.compare_and_update(
                self.task['task_id'], expected={'record_sha256': current['record_sha256']},
                developer=blocked)

    def _assert_refused(self, **changes):
        before = self.store.get(self.task['task_id'])
        with self.assertRaises(LLMToolError) as caught:
            self._write(**changes)
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_INVALID')
        self.assertEqual(self.store.get(self.task['task_id']), before)

    def test_valid_evidence_is_stored_and_validates(self):
        for evidence in (_evidence(), _evidence(subtype='OTHER', message=None),
                         _evidence(is_error='UNKNOWN', num_turns='UNKNOWN',
                                   duration_ms='UNKNOWN', exit_code='UNKNOWN')):
            with self.subTest(evidence=evidence):
                self._new_task()
                saved = self._write(failure_evidence=evidence)
                self.assertEqual(saved['developer']['failure_evidence'], evidence)
                loaded = self.store.get(self.task['task_id'])
                self.assertEqual(loaded['developer']['failure_evidence'], evidence)
                task_store_module._validate(copy.deepcopy(loaded), strict=False)

    def test_legacy_record_without_evidence_loads(self):
        self._write()
        loaded = self.store.get(self.task['task_id'])
        self.assertNotIn('failure_evidence', loaded['developer'])
        task_store_module._validate(copy.deepcopy(loaded), strict=False)

    def test_subtype_outside_allowlist_refused(self):
        for subtype in ('error_new_kind', 'other', PROMPT, '', None):
            with self.subTest(subtype=subtype):
                self._assert_refused(failure_evidence=_evidence(subtype=subtype))

    def test_message_failing_screen_refused(self):
        screen = _flagging_screen(task_store_module._emitted_sensitive)
        with mock.patch.object(task_store_module, '_emitted_sensitive', screen):
            self._assert_refused(failure_evidence=_evidence(
                message=f'Run failed near {MARKER} in output'))
        stored = json.dumps(self.store.get(self.task['task_id']))
        self.assertNotIn(MARKER, stored)

    def test_malformed_evidence_refused(self):
        for evidence in (_evidence(exit_code=True), _evidence(num_turns=-1),
                         _evidence(duration_ms=1.5), _evidence(is_error=1),
                         _evidence(message=''), _evidence(message=' padded'),
                         _evidence(message='x' * 201), _evidence(message='tab\there'),
                         dict(_evidence(), extra=1),
                         {k: v for k, v in _evidence().items() if k != 'message'},
                         'not a dict'):
            with self.subTest(evidence=evidence):
                self._assert_refused(failure_evidence=evidence)

    def test_evidence_only_on_blocked_claude_failure(self):
        self._assert_refused(failure_evidence=_evidence(),
                             error_code='BLOCKED_WRITE_ISOLATION')
        self._assert_refused(failure_evidence=_evidence(), error_code=None)


def _stdout_bytes(subtype, is_error, result, session_id, num_turns=3, duration_ms=4567):
    return json.dumps({
        'type': 'result', 'subtype': subtype, 'is_error': is_error,
        'num_turns': num_turns, 'duration_ms': duration_ms, 'result': result,
        'session_id': session_id}).encode('utf-8')


class ClaudeDelegateEvidenceTests(unittest.TestCase):
    """The real Claude delegate failure and NO_CHANGES paths write the evidence."""

    def _run_status_failed(self, result_text, subtype='error_during_execution', extra=()):
        case = test_claude_route_wiring.ClaudeRouteWiringTests(
            methodName='test_route_failure_records_no_delivery')
        cli, store, args, _reg, _evals, _prompts, _reqs, patches, _runner = case._setup(
            runner_error=RouteError('CLAUDE_STATUS_FAILED'))
        session_id = str(uuid.uuid4())
        stdout = _stdout_bytes(subtype, True, result_text, session_id)

        def runner(route, request, **_kwargs):
            # The real ClaudeRoute parse and status check, as run_route calls them.
            parsed = route.parse_output(stdout, b'', '')
            try:
                route.validate_status(parsed)
            except RouteError as exc:
                exc.execution_facts = {
                    'process_exit_code': 1,
                    'stdout_sha256': hashlib.sha256(stdout).hexdigest(),
                    'elapsed_ms': 10, 'cleanup_status': 'CONFIRMED'}
                raise
            raise AssertionError('error result must fail the status check')

        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            for item in extra:
                stack.enter_context(item)
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            status = cli.run_task_claude_delegate(args, store=store, route_runner=runner)
        return status, store, args, out.getvalue() + err.getvalue()

    def test_status_failure_records_evidence(self):
        message = 'Upstream fault 503 during streaming (qv-9)'
        status, store, args, _printed = self._run_status_failed('intro\n' + message)
        self.assertEqual(status, 1)
        record = store.get(args.task_id)
        developer = record['developer']
        self.assertEqual(developer['state'], 'BLOCKED')
        self.assertEqual(developer['error_code'], 'CLAUDE_STATUS_FAILED')
        self.assertEqual(developer['failure_evidence'], {
            'subtype': 'error_during_execution', 'is_error': True, 'num_turns': 3,
            'duration_ms': 4567, 'exit_code': 1, 'message': message})
        self.assertEqual(developer['execution']['process_exit_code'], 1)
        task_store_module._validate(copy.deepcopy(record), strict=False)

    def test_status_failure_drops_prompt_echo_and_unknown_subtype(self):
        status, store, args, _printed = self._run_status_failed(
            'Stopped: the REGISTERED d6   worktree was busy', subtype=REAL_PROMPT_FRAGMENT)
        self.assertEqual(status, 1)
        evidence = store.get(args.task_id)['developer']['failure_evidence']
        self.assertEqual(evidence['subtype'], 'OTHER')
        self.assertIsNone(evidence['message'])
        self.assertNotIn(REAL_PROMPT_FRAGMENT.casefold(), json.dumps(evidence).casefold())

    def test_status_failure_marker_never_stored_or_printed(self):
        extra = (
            mock.patch.object(cli_module, '_emitted_sensitive',
                              _flagging_screen(cli_module._emitted_sensitive)),
            mock.patch.object(task_store_module, '_emitted_sensitive',
                              _flagging_screen(task_store_module._emitted_sensitive)),
        )
        status, store, args, printed = self._run_status_failed(
            f'Run failed near {MARKER} in output', extra=extra)
        self.assertEqual(status, 1)
        record = store.get(args.task_id)
        self.assertIsNone(record['developer']['failure_evidence']['message'])
        self.assertNotIn(MARKER, json.dumps(record))
        self.assertNotIn(MARKER, printed)

    def test_no_changes_records_evidence(self):
        case = test_claude_route_wiring.ClaudeRouteWiringTests(
            methodName='test_route_failure_records_no_delivery')
        cli, store, args, _reg, _evals, _prompts, _reqs, patches, _runner = case._setup(
            runner_error=RouteError('CLAUDE_STATUS_FAILED'))
        session_id = str(uuid.uuid4())
        message = 'Halting: zero file edits were made (qv-7).'
        stdout = _stdout_bytes('success', False, message, session_id,
                               num_turns=1, duration_ms=900)
        state = {}

        def runner(route, request, **_kwargs):
            parsed = route.parse_output(stdout, b'', '')
            route.validate_status(parsed)
            state['after'] = True
            return RouteResult('claude', 'DONE', message, session_id, request['model'],
                               request['effort'], None, (), 0,
                               hashlib.sha256(stdout).hexdigest(), 5, 'CONFIRMED', {}, None)

        out = io.StringIO()
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            completion = cli.task_completion
            real_bind = completion.bind_executor_task
            real_read = completion.read_d6_registration
            real_collect = completion.collect_git_snapshot
            real_diff = completion._worktree_diff

            def bind(*a, **k):
                value = real_bind(*a, **k)
                state['binding'] = value
                return value

            def read(*a, **k):
                return state['binding'][0] if state.get('after') else real_read(*a, **k)

            def collect(*a, **k):
                if not state.get('after'):
                    return real_collect(*a, **k)
                registration, snapshot = state['binding']
                return SimpleNamespace(root=registration.root,
                                       common_dir=registration.common_dir,
                                       branch=registration.branch, head=snapshot.head,
                                       clean=True)

            def diff(*a, **k):
                return ((), b'') if state.get('after') else real_diff(*a, **k)

            stack.enter_context(mock.patch.object(completion, 'bind_executor_task', bind))
            stack.enter_context(mock.patch.object(completion, 'read_d6_registration', read))
            stack.enter_context(mock.patch.object(completion, 'collect_git_snapshot', collect))
            stack.enter_context(mock.patch.object(completion, '_worktree_diff', diff))
            stack.enter_context(mock.patch.object(cli, '_claude_committed_carry',
                                                  return_value=None))
            stack.enter_context(mock.patch.object(cli, '_record_skill_delivery',
                                                  return_value=None))
            stack.enter_context(contextlib.redirect_stdout(out))
            status = cli.run_task_claude_delegate(args, store=store, route_runner=runner)
        self.assertEqual(status, 1)
        record = store.get(args.task_id)
        developer = record['developer']
        self.assertEqual(developer['error_code'], 'CLAUDE_NO_CHANGES')
        self.assertEqual(developer['failure_evidence'], {
            'subtype': 'success', 'is_error': False, 'num_turns': 1,
            'duration_ms': 900, 'exit_code': 0, 'message': message})
        self.assertEqual(developer['execution']['status'], 'BLOCKED')
        self.assertIn('no_changes_diagnosis', developer)
        task_store_module._validate(copy.deepcopy(record), strict=False)


if __name__ == '__main__':
    unittest.main()
