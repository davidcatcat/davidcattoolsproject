"""#2275: a failed or no-change Claude route run keeps bounded, non-secret CLI evidence.

Fake CLI output only. No test string is credential-shaped: the sensitive-screen
cases inject a screen that flags a plain synthetic marker and still check the
real refusal behaviour (Owner #763 A, point 2).

The delegate runs against the Claude wiring fixture's store, which keeps a
partial Task record: a strict task_store._validate of that record fails on the
top-level shape (task_store.py:2207-2213) whatever the developer holds. So the
developer outcome the real delegate wrote is put onto a real TaskStore Claude
CLI record, which the real store checks on every write and read, and that
record is what the store checks below.
"""

import contextlib
import hashlib
import io
import json
import unittest
from unittest import mock

from llmtool import task_store
from llmtool import task_submission_cli
from llmtool import worker_dispatch
from llmtool.developer_routes.base import RouteError, RouteResult
from llmtool.developer_routes.claude import ClaudeRoute, cli_result_fields
from llmtool.errors import LLMToolError

try:
    import test_claude_route_wiring
except ImportError:
    from tests import test_claude_route_wiring

try:
    import test_task_store
except ImportError:
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


SESSION = '4f9c2a1e-8b7d-4c3e-9a2f-1d0e5b6c7a88'
# Plain synthetic markers; flagged only by the injected screen below. The
# subtype marker fits the subtype shape, so only the screen can refuse it.
MARKER = 'SYNTHETIC-SCREEN-MARKER-2275'
SUBTYPE_MARKER = 'synthetic_screen_marker_2275'
EVIDENCE_KEYS = {'subtype', 'is_error', 'num_turns', 'duration_ms',
                 'process_exit_code', 'result_head'}
# Developer keys the delegate's BLOCKED outcome sets (run_task_claude_delegate).
OUTCOME_KEYS = ('state', 'provider_type', 'model', 'error_code', 'artifact',
                'artifact_sha256', 'execution', 'usage', 'no_changes_diagnosis',
                'cli_failure_evidence', 'blocked_reason', 'progress')


def _cli_json(drop=(), **overrides):
    """Fake Claude CLI --output-format json stdout."""
    obj = {'type': 'result', 'subtype': 'success', 'is_error': False, 'num_turns': 2,
           'duration_ms': 1500, 'session_id': SESSION,
           'result': 'Provider halted: quokka lattice 7731 stalled at phase nine.'}
    obj.update(overrides)
    for key in drop:
        obj.pop(key, None)
    return json.dumps(obj).encode('utf-8')


def _facts(code, stdout):
    return {'process_exit_code': code, 'stdout_sha256': hashlib.sha256(stdout).hexdigest(),
            'elapsed_ms': 25, 'cleanup_status': 'CONFIRMED'}


def _failing_runner(stdout, code, then):
    """Route runner that parses fake stdout with the real route, then fails post-spawn.

    As run_route does, every RouteError after the process ran carries the
    run's execution facts."""
    def runner(route, request, **_kwargs):
        if not isinstance(route, ClaudeRoute):
            raise RouteError('CLAUDE_TEST_ROUTE_UNEXPECTED')
        try:
            parsed = route.parse_output(stdout, b'', '')
            then(route, parsed)
        except RouteError as exc:
            exc.execution_facts = _facts(code, stdout)
            raise
        raise RouteError('CLAUDE_TEST_DID_NOT_FAIL')
    return runner


def _status_failed(route, parsed):
    if route.quota_exhausted(parsed, b''):
        raise RouteError('CLAUDE_TEST_UNEXPECTED_QUOTA')
    route.validate_status(parsed)


def _quota(route, parsed):
    if not route.quota_exhausted(parsed, b''):
        raise RouteError('CLAUDE_TEST_QUOTA_NOT_SEEN')
    raise RouteError('CLAUDE_QUOTA_EXHAUSTED',
                     provider_evidence=({'condition': 'QUOTA_EXHAUSTED'}
                                        if route.provider_quota_reported(parsed) else None))


def _exit_nonzero(route, parsed):
    route.validate_status(parsed)
    raise RouteError('CLAUDE_EXIT_NONZERO')


def _flagging(original, marker=MARKER):
    def screen(text):
        return (type(text) is str and marker in text) or original(text)
    return screen


def _with_developer(record, developer):
    variant = dict(record, developer=developer)
    variant['record_sha256'] = task_store._digest(variant)
    return variant


class ClaudeFailureEvidenceTests(unittest.TestCase):
    def _run(self, route_runner, *, setup=None, runner_error=None):
        case = test_claude_route_wiring.ClaudeRouteWiringTests(
            methodName='test_route_failure_records_no_delivery')
        cli, store, args, _reg, _evals, _prompts, _reqs, patches, _runner = case._setup(
            runner_error=runner_error or RouteError('CLAUDE_STATUS_FAILED'))
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            if setup is not None:
                setup(stack, cli)
            status = cli.run_task_claude_delegate(
                args, store=store, route_runner=route_runner or _runner)
        self.assertEqual(status, 1)
        return store, args.task_id

    def _real_store_case(self):
        """One real TaskStore (test_task_store fixture) for this test, cleaned up after it."""
        case = getattr(self, '_store_case', None)
        if case is None:
            case = test_task_store.TaskStoreTests(
                methodName='test_claude_cli_accepts_blocked_isolation_record')
            case.setUp()
            self.addCleanup(case.doCleanups)
            self._store_case = case
        return case

    def _real_record(self, produced):
        """The delegate's BLOCKED developer outcome, written to a real Claude CLI record.

        The real TaskStore validates the write and the read, as for the
        delegate's own store.update; the record is then checked again strictly."""
        case = self._real_store_case()
        store = case.store
        task_id = case._claude_cli_task()['task_id']
        pending = store.get(task_id)['developer']
        dispatching = dict(pending, state='DEVELOPER_DISPATCHING',
                           provider_type='claude-cli', model=produced['model'],
                           execution=None)
        blocked = dict(dispatching, **{key: produced[key] for key in OUTCOME_KEYS
                                       if key in produced})
        with mock.patch.object(worker_dispatch, 'authorize_task_store_change',
                               return_value=None):
            for developer in (dispatching, blocked):
                current = store.get(task_id)
                store.compare_and_update(
                    task_id, expected={'record_sha256': current['record_sha256']},
                    developer=developer)
        record = store.get(task_id)
        task_store._validate(record)
        return record

    def _evidence(self, store, task_id, error_code, exit_code):
        developer = store.get(task_id)['developer']
        self.assertEqual(developer['state'], 'BLOCKED')
        self.assertEqual(developer['error_code'], error_code)
        self.assertEqual(developer['execution']['status'], 'BLOCKED')
        self.assertEqual(developer['execution']['process_exit_code'], exit_code)
        evidence = developer['cli_failure_evidence']
        self.assertEqual(set(evidence), EVIDENCE_KEYS)
        self.assertEqual(evidence['process_exit_code'], exit_code)
        # The real store accepts the evidence the delegate wrote, unchanged.
        record = self._real_record(developer)
        self.assertEqual(record['developer']['cli_failure_evidence'], evidence)
        return record, evidence

    # -- each failure path ------------------------------------------------

    def test_status_failed_keeps_evidence(self):
        stdout = _cli_json(subtype='error_during_execution', is_error=True,
                           num_turns=3, duration_ms=1234)
        store, task_id = self._run(_failing_runner(stdout, 1, _status_failed))
        _record, evidence = self._evidence(store, task_id, 'CLAUDE_STATUS_FAILED', 1)
        self.assertEqual(evidence, {
            'subtype': 'error_during_execution', 'is_error': True, 'num_turns': 3,
            'duration_ms': 1234, 'process_exit_code': 1,
            'result_head': 'Provider halted: quokka lattice 7731 stalled at phase nine.'})

    def test_output_invalid_non_json_keeps_exit_code_only(self):
        stdout = b'plain text that is not the CLI json result'
        store, task_id = self._run(_failing_runner(stdout, 2, _status_failed))
        _record, evidence = self._evidence(store, task_id, 'CLAUDE_OUTPUT_INVALID', 2)
        self.assertEqual(evidence, {
            'subtype': None, 'is_error': None, 'num_turns': None, 'duration_ms': None,
            'process_exit_code': 2, 'result_head': None})

    def test_output_invalid_json_keeps_typed_fields(self):
        # Parsed JSON that the route refuses (no session id) still gives its fields.
        stdout = _cli_json(drop=('session_id',), num_turns=4, duration_ms=2000)
        store, task_id = self._run(_failing_runner(stdout, 0, _status_failed))
        _record, evidence = self._evidence(store, task_id, 'CLAUDE_OUTPUT_INVALID', 0)
        self.assertEqual(evidence['subtype'], 'success')
        self.assertIs(evidence['is_error'], False)
        self.assertEqual(evidence['num_turns'], 4)
        self.assertEqual(evidence['duration_ms'], 2000)
        self.assertEqual(evidence['result_head'],
                         'Provider halted: quokka lattice 7731 stalled at phase nine.')

    def test_quota_exhausted_keeps_evidence(self):
        stdout = _cli_json(is_error=True, api_error_status=429, num_turns=0,
                           duration_ms=300, result='Usage limit reached; quokka resets later.')
        store, task_id = self._run(_failing_runner(stdout, 1, _quota))
        record, evidence = self._evidence(store, task_id, 'CLAUDE_QUOTA_EXHAUSTED', 1)
        self.assertEqual(record['developer']['execution']['provider_evidence'],
                         {'condition': 'QUOTA_EXHAUSTED'})
        self.assertEqual(evidence, {
            'subtype': 'success', 'is_error': True, 'num_turns': 0, 'duration_ms': 300,
            'process_exit_code': 1,
            'result_head': 'Usage limit reached; quokka resets later.'})

    def test_other_post_spawn_error_keeps_evidence(self):
        stdout = _cli_json(num_turns=5, duration_ms=4000)
        store, task_id = self._run(_failing_runner(stdout, 3, _exit_nonzero))
        _record, evidence = self._evidence(store, task_id, 'CLAUDE_EXIT_NONZERO', 3)
        self.assertEqual((evidence['subtype'], evidence['is_error'], evidence['num_turns'],
                          evidence['duration_ms']), ('success', False, 5, 4000))

    def test_no_changes_keeps_evidence(self):
        stdout = _cli_json(num_turns=1, duration_ms=900,
                           result='No edit needed: quokka lattice already matches.')
        held = {}

        def setup(stack, cli):
            original = cli._delegate_d6_binding

            def binding(args):
                held['pair'] = original(args)
                return held['pair']
            stack.enter_context(mock.patch.object(cli, '_delegate_d6_binding', binding))
            held.update(stack=stack, cli=cli)

        def runner(route, request, **_kwargs):
            route.parse_output(stdout, b'', '')
            cli, stack = held['cli'], held['stack']
            registration, snapshot = held['pair']
            completion = cli.task_completion
            read_registration = completion.read_d6_registration
            collect = completion.collect_git_snapshot
            # The D6 is unchanged and has no diff: the delegate's own
            # CLAUDE_NO_CHANGES path then runs.
            stack.enter_context(mock.patch.object(
                cli, '_record_skill_delivery', lambda *a, **k: None))
            stack.enter_context(mock.patch.object(
                completion, 'read_d6_registration',
                lambda task_id, *a, **k: (registration if task_id == registration.task_id
                                          else read_registration(task_id, *a, **k))))
            stack.enter_context(mock.patch.object(
                completion, 'collect_git_snapshot',
                lambda root, *a, **k: (snapshot if str(root) == str(registration.root)
                                       else collect(root, *a, **k))))
            stack.enter_context(mock.patch.object(
                completion, '_worktree_diff', lambda *a, **k: ((), b'')))
            stack.enter_context(mock.patch.object(
                cli, '_claude_committed_carry', lambda *a, **k: None))
            return RouteResult('claude', 'DONE', '', SESSION, request['model'],
                               request['effort'], None, (), 0,
                               hashlib.sha256(stdout).hexdigest(), 10, 'CONFIRMED', {}, None)

        store, task_id = self._run(runner, setup=setup)
        record, evidence = self._evidence(store, task_id, 'CLAUDE_NO_CHANGES', 0)
        self.assertIn('no_changes_diagnosis', record['developer'])
        self.assertEqual(evidence, {
            'subtype': 'success', 'is_error': False, 'num_turns': 1, 'duration_ms': 900,
            'process_exit_code': 0,
            'result_head': 'No edit needed: quokka lattice already matches.'})

    # -- the head is bounded and screened ---------------------------------

    def test_head_is_bounded_and_whitespace_folded(self):
        text = '  first\n\n  line  ' + ' '.join('quokka%03d' % i for i in range(60))
        stdout = _cli_json(subtype='error_max_turns', is_error=True, result=text)
        store, task_id = self._run(_failing_runner(stdout, 1, _status_failed))
        _record, evidence = self._evidence(store, task_id, 'CLAUDE_STATUS_FAILED', 1)
        head = evidence['result_head']
        self.assertEqual(head, ' '.join(text.split())[:200].rstrip())
        self.assertLessEqual(len(head), 200)
        self.assertTrue(head.startswith('first line quokka000'))

    def test_screen_hit_drops_head_and_is_never_stored(self):
        cases = {
            'in_head': 'Run stopped near %s in quokka step.' % MARKER,
            'after_head': ' '.join('quokka%03d' % i for i in range(40)) + ' ' + MARKER,
        }
        for name, text in cases.items():
            with self.subTest(name):
                stdout = _cli_json(subtype='error_during_execution', is_error=True,
                                   num_turns=6, result=text)

                def setup(stack, cli):
                    stack.enter_context(mock.patch.object(
                        cli, '_emitted_sensitive', _flagging(cli._emitted_sensitive)))
                store, task_id = self._run(_failing_runner(stdout, 1, _status_failed),
                                           setup=setup)
                record, evidence = self._evidence(store, task_id, 'CLAUDE_STATUS_FAILED', 1)
                self.assertIsNone(evidence['result_head'])
                self.assertEqual(evidence['num_turns'], 6)
                self.assertNotIn(MARKER, json.dumps(store.get(task_id), default=str))
                self.assertNotIn(MARKER, json.dumps(record))

    def test_screen_hit_drops_subtype_and_is_never_stored(self):
        # codex-reviewer v1 F1: the subtype is screened like the result head.
        stdout = _cli_json(subtype=SUBTYPE_MARKER, is_error=True, num_turns=6,
                           duration_ms=700)

        def setup(stack, cli):
            stack.enter_context(mock.patch.object(
                cli, '_emitted_sensitive',
                _flagging(cli._emitted_sensitive, SUBTYPE_MARKER)))
        store, task_id = self._run(_failing_runner(stdout, 1, _status_failed),
                                   setup=setup)
        record, evidence = self._evidence(store, task_id, 'CLAUDE_STATUS_FAILED', 1)
        # Only the subtype is dropped; the other typed fields and the head stay.
        self.assertEqual(evidence, {
            'subtype': None, 'is_error': True, 'num_turns': 6, 'duration_ms': 700,
            'process_exit_code': 1,
            'result_head': 'Provider halted: quokka lattice 7731 stalled at phase nine.'})
        self.assertNotIn(SUBTYPE_MARKER, json.dumps(store.get(task_id), default=str))
        self.assertNotIn(SUBTYPE_MARKER, json.dumps(record))

    def test_prompt_echo_and_control_text_drop_head(self):
        cases = {
            # Repeats the delegate's own instructions, which are sent text.
            'echo': 'I will read only read_paths and edit only write_paths here.',
            'control': 'Colour \x1b[31mred quokka',
        }
        for name, text in cases.items():
            with self.subTest(name):
                stdout = _cli_json(subtype='error_during_execution', is_error=True,
                                   result=text)
                store, task_id = self._run(_failing_runner(stdout, 1, _status_failed))
                _record, evidence = self._evidence(store, task_id, 'CLAUDE_STATUS_FAILED', 1)
                self.assertIsNone(evidence['result_head'])
                self.assertEqual(evidence['subtype'], 'error_during_execution')

    def test_short_prompt_fragment_in_extra_text_drops_head(self):
        # Codex security v91: "use Git, publish" (16 normalized characters) is
        # from the delegate's own sent instructions. No 20-character run of
        # this text was sent, so only the 12-character window catches it.
        text = 'Quokka step tried to use Git, publish halted at gate seven.'
        stdout = _cli_json(subtype='error_during_execution', is_error=True,
                           num_turns=2, result=text)
        store, task_id = self._run(_failing_runner(stdout, 1, _status_failed))
        record, evidence = self._evidence(store, task_id, 'CLAUDE_STATUS_FAILED', 1)
        self.assertIsNone(evidence['result_head'])
        self.assertEqual(evidence['subtype'], 'error_during_execution')
        self.assertEqual(evidence['num_turns'], 2)
        self.assertNotIn('halted at gate seven', json.dumps(store.get(task_id), default=str))
        self.assertNotIn('halted at gate seven', json.dumps(record))

    def test_result_head_echo_window(self):
        head = task_submission_cli._claude_result_head
        sent = ('Please keep the amber quokka lattice stable.', 'goal')
        cases = {
            # A 12-character fragment inside extra text; case and whitespace folded.
            'fragment_in_extra_text': ('Run halted; AMBER   quokka seen at gate nine.', None),
            # A 16-character fragment: below the 20-character v93 run.
            'fragment_16': ('Wombat said the amber quokka twice.', None),
            'exact_echo': ('Please keep the amber quokka lattice stable.', None),
            'contained_echo': ('the amber quokka lattice', None),
            # Unrelated provider error text is kept.
            'unrelated': ('Provider stopped: wombat ledger 4410 timed out.',
                          'Provider stopped: wombat ledger 4410 timed out.'),
            # Only 11 characters ("mber quokka") are shared, below the window,
            # so the head is kept.
            'eleven_shared': ('Saw umber quokkas.', 'Saw umber quokkas.'),
        }
        for name, (text, expected) in cases.items():
            with self.subTest(name):
                self.assertEqual(head(text, sent), expected)

    def test_no_changes_reason_echo_run_unchanged(self):
        # The v93 no-changes reason keeps its own 20-character run (no other
        # behaviour change); only the result head uses the 12-character window.
        index = task_submission_cli._SentTextIndex(
            ('Please keep the amber quokka lattice stable.',))
        self.assertEqual(index.run, 20)
        self.assertFalse(index.echoes('Wombat said the amber quokka twice.'))
        self.assertTrue(index.echoes('Wombat: keep the amber quokka lattice now.'))

    def test_pre_launch_refusal_stores_no_evidence(self):
        # A refusal without execution facts never ran the process.
        store, task_id = self._run(None, runner_error=RouteError('CLAUDE_STATUS_FAILED'))
        developer = store.get(task_id)['developer']
        self.assertEqual(developer['error_code'], 'CLAUDE_STATUS_FAILED')
        self.assertIsNone(developer['execution'])
        self.assertNotIn('cli_failure_evidence', developer)

    # -- the route and helpers --------------------------------------------

    def test_cli_result_fields_strict_types(self):
        self.assertIsNone(cli_result_fields(['not', 'a', 'dict']))
        self.assertEqual(cli_result_fields({}), {
            'subtype': None, 'is_error': None, 'num_turns': None, 'duration_ms': None,
            'result': None})
        bad = cli_result_fields({'subtype': 'Not A Subtype', 'is_error': 1,
                                 'num_turns': True, 'duration_ms': -1, 'result': 7})
        self.assertEqual(set(bad.values()), {None})
        self.assertIsNone(cli_result_fields({'num_turns': 1_000_001})['num_turns'])
        self.assertIsNone(cli_result_fields({'duration_ms': 86_400_001})['duration_ms'])
        self.assertIsNone(cli_result_fields({'subtype': 'x' * 65})['subtype'])

    def test_parse_output_result_unchanged(self):
        route = ClaudeRoute({'executable': '/fake/claude'})
        stdout = _cli_json(num_turns=2)
        parsed = route.parse_output(stdout, b'', '')
        self.assertEqual(set(parsed), {'status', 'summary', 'session_id', 'usage', 'raw'})
        self.assertEqual(parsed['status'], 'DONE')
        self.assertEqual(parsed['raw'], json.loads(stdout))
        self.assertEqual(route.parsed_cli_fields['num_turns'], 2)
        with self.assertRaises(RouteError):
            route.parse_output(b'not json', b'', '')
        self.assertIsNone(route.parsed_cli_fields)

    def test_result_head_helper(self):
        head = task_submission_cli._claude_result_head
        sent = ('an unrelated prompt',)
        self.assertEqual(head('  a\tquokka\n line  ', sent), 'a quokka line')
        self.assertIsNone(head(None, sent))
        self.assertIsNone(head('   ', sent))
        self.assertIsNone(head('quokka text', ()))  # sent text unavailable: fail closed

    def test_subtype_helper(self):
        subtype = task_submission_cli._claude_cli_subtype
        self.assertEqual(subtype('error_max_turns'), 'error_max_turns')
        self.assertIsNone(subtype(None))
        self.assertIsNone(subtype(7))
        self.assertEqual(subtype(SUBTYPE_MARKER), SUBTYPE_MARKER)
        with mock.patch.object(task_submission_cli, '_emitted_sensitive',
                               _flagging(task_submission_cli._emitted_sensitive,
                                         SUBTYPE_MARKER)):
            self.assertIsNone(subtype(SUBTYPE_MARKER))
            self.assertEqual(subtype('error_max_turns'), 'error_max_turns')

    # -- TaskStore accepts the field strictly -----------------------------

    def _stored_failure(self):
        stdout = _cli_json(subtype='error_during_execution', is_error=True, num_turns=3)
        store, task_id = self._run(_failing_runner(stdout, 1, _status_failed))
        record, _evidence = self._evidence(store, task_id, 'CLAUDE_STATUS_FAILED', 1)
        return record

    def assertRefused(self, record):
        with self.assertRaises(LLMToolError) as caught:
            task_store._validate(record)
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_STORE_INVALID')

    def test_store_accepts_record_without_field(self):
        record = self._stored_failure()
        developer = dict(record['developer'])
        del developer['cli_failure_evidence']
        task_store._validate(_with_developer(record, developer))

    def test_store_refuses_malformed_evidence(self):
        record = self._stored_failure()
        developer = record['developer']
        good = developer['cli_failure_evidence']
        variants = {
            'not_dict': ['subtype'],
            'missing_key': {k: v for k, v in good.items() if k != 'result_head'},
            'extra_key': dict(good, prompt='quokka'),
            'subtype_free_text': dict(good, subtype='Error During Execution'),
            'subtype_type': dict(good, subtype=3),
            'is_error_int': dict(good, is_error=1),
            'turns_bool': dict(good, num_turns=True),
            'turns_negative': dict(good, num_turns=-1),
            'turns_over': dict(good, num_turns=1_000_001),
            'duration_str': dict(good, duration_ms='900'),
            'duration_over': dict(good, duration_ms=86_400_001),
            'exit_mismatch': dict(good, process_exit_code=2),
            'exit_none_mismatch': dict(good, process_exit_code=None),
            'exit_bool': dict(good, process_exit_code=True),
            'head_empty': dict(good, result_head=''),
            'head_padded': dict(good, result_head=' quokka '),
            'head_control': dict(good, result_head='quokka\x07'),
            'head_format_char': dict(good, result_head='quo​kka'),
            'head_long': dict(good, result_head='q' * 201),
            'head_type': dict(good, result_head=5),
        }
        for name, evidence in variants.items():
            with self.subTest(name):
                self.assertRefused(_with_developer(
                    record, dict(developer, cli_failure_evidence=evidence)))
        # The bound itself is accepted.
        task_store._validate(_with_developer(
            record, dict(developer, cli_failure_evidence=dict(good, result_head='q' * 200))))

    def test_store_refuses_evidence_outside_a_claude_run_failure(self):
        record = self._stored_failure()
        developer = record['developer']
        evidence = developer['cli_failure_evidence']
        without = {k: v for k, v in developer.items() if k != 'cli_failure_evidence'}
        contexts = {
            'pre_launch': dict(without, execution=None),
            'dispatch_denied': dict(without, error_code='CLAUDE_DISPATCH_DENIED'),
            'not_claude_code': dict(without, error_code='ROUTE_OUTPUT_TOO_LARGE'),
        }
        for name, base in contexts.items():
            with self.subTest(name):
                # The record without the evidence is valid; only the evidence is refused.
                task_store._validate(_with_developer(record, base))
                self.assertRefused(_with_developer(
                    record, dict(base, cli_failure_evidence=evidence)))

    def test_store_screens_head(self):
        record = self._stored_failure()
        developer = record['developer']
        evidence = dict(developer['cli_failure_evidence'],
                        result_head='Synthetic %s head' % MARKER)
        variant = _with_developer(record, dict(developer, cli_failure_evidence=evidence))
        task_store._validate(variant)
        with mock.patch.object(task_store, '_emitted_sensitive',
                               _flagging(task_store._emitted_sensitive)):
            self.assertRefused(variant)

    def test_store_screens_subtype(self):
        # codex-reviewer v1 F1: TaskStore refuses a sensitive subtype value.
        record = self._stored_failure()
        developer = record['developer']
        evidence = dict(developer['cli_failure_evidence'], subtype=SUBTYPE_MARKER)
        variant = _with_developer(record, dict(developer, cli_failure_evidence=evidence))
        task_store._validate(variant)
        with mock.patch.object(task_store, '_emitted_sensitive',
                               _flagging(task_store._emitted_sensitive, SUBTYPE_MARKER)):
            self.assertRefused(variant)
            # A null subtype stays valid under the same screen.
            task_store._validate(_with_developer(record, dict(
                developer, cli_failure_evidence=dict(evidence, subtype=None))))


if __name__ == '__main__':
    unittest.main()
