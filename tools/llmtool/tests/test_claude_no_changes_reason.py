"""#1824: a Claude CLAUDE_NO_CHANGES run keeps a bounded, screened diagnosis.

Test data rule (Owner #763 A): no test here holds a password-, API-key-,
token- or other credential-shaped string. The refusal tests inject the screen
function with a plain marker word and still check the real refusal behaviour.
"""

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import task_store as task_store_module
from llmtool import task_submission_cli
from llmtool.developer_routes import RouteError
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore

import review_trust_fixture
import test_claude_route_wiring as wiring
import worker_policy_fixture


_SESSION = '12345678-1234-4234-8234-123456789abc'
_OWNER_REF = ('https://github.com/davidcatcat/davidcattoolsproject/'
              'issues/289#issuecomment-1234567890')
_MARKER = 'SCREEN-HIT-MARKER'
_DROP = object()


def setUpModule():
    review_trust_fixture.start_module()
    worker_policy_fixture.start_module()


def tearDownModule():
    worker_policy_fixture.stop_module()
    review_trust_fixture.stop_module()


def _cli_stdout(**fields):
    """A parsed-shape Claude CLI JSON result; a field set to _DROP is omitted."""
    obj = {'type': 'result', 'subtype': 'success', 'is_error': False,
           'session_id': _SESSION, 'result': 'Done.', 'num_turns': 3,
           'permission_denials': [],
           'usage': {'input_tokens': 3, 'output_tokens': 2},
           'modelUsage': {'claude-test-1': {'outputTokens': 2}}}
    obj.update(fields)
    return json.dumps({key: value for key, value in obj.items()
                       if value is not _DROP}).encode('utf-8')


def _marker_screen(seen):
    def screen(text):
        seen.append(text)
        return _MARKER in text
    return screen


class ClaudeNoChangesDiagnosisRouteTests(unittest.TestCase):
    """run_task_claude_delegate with the real ClaudeRoute parser on CLI JSON."""

    def _run(self, stdout=None, *, changed_paths=(), raw=b'', runner_error=None,
             extra_patches=(), goal=None, prompts=None):
        cli, store, args, _reg, _ev, _pr, _rq, patches, _runner = (
            wiring.ClaudeRouteWiringTests._setup(
                self, changed_paths=changed_paths, raw=raw))
        if goal is not None:
            store.task['pm_assignment'] = dict(store.task['pm_assignment'], goal=goal)
        result = SimpleNamespace(session_id=_SESSION, exit_code=0,
            stdout_sha256='e' * 64, elapsed_ms=42, cleanup_status='CONFIRMED',
            usage={'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5})

        def runner(route, request, *, gate, **_kwargs):
            if prompts is not None:
                prompts.append(request['prompt'])
            self.assertTrue(gate().allowed)
            if runner_error is not None:
                raise runner_error
            if stdout is not None:
                route.parse_output(stdout, b'', '')
            return result

        with contextlib.ExitStack() as stack:
            for item in (*patches, *extra_patches):
                stack.enter_context(item)
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            status = cli.run_task_claude_delegate(args, store=store, route_runner=runner)
        return status, store.task['developer']

    def _assert_no_changes_evidence(self, status, developer):
        self.assertEqual(status, 1)
        self.assertEqual(developer['state'], 'BLOCKED')
        self.assertEqual(developer['error_code'], 'CLAUDE_NO_CHANGES')
        self.assertIsNone(developer['artifact'])
        self.assertEqual(developer['execution'], {
            'status': 'BLOCKED', 'native_session_id': _SESSION,
            'process_exit_code': 0, 'stdout_sha256': 'e' * 64, 'elapsed_ms': 42,
            'cleanup_status': 'CONFIRMED', 'usage_source': 'CLI_REPORTED',
            'source_bytes': None, 'source_file_count': None,
            'source_manifest_sha256': None})
        self.assertEqual(developer['usage']['total_tokens'], 5)

    def test_reason_is_the_last_non_empty_result_line(self):
        status, developer = self._run(_cli_stdout(
            result='Read the files.\nNo edit was needed.\n\n   \n', num_turns=4))
        self._assert_no_changes_evidence(status, developer)
        # Four turns with no denial does not prove no write was tried: UNKNOWN.
        self.assertEqual(developer['no_changes_diagnosis'], {
            'reason': 'No edit was needed.', 'write_attempted': 'UNKNOWN', 'num_turns': 4})

    def test_ordinary_line_endings_are_kept(self):
        for text in ('Read the files.\r\nNo edit was needed.\r\n',
                     'Read the files.\rNo edit was needed.'):
            with self.subTest(text=repr(text)):
                status, developer = self._run(_cli_stdout(result=text))
                self._assert_no_changes_evidence(status, developer)
                self.assertEqual(developer['no_changes_diagnosis']['reason'],
                                 'No edit was needed.')

    def test_screen_hit_gives_unknown_and_keeps_no_provider_text(self):
        for text in (f'First line.\nThe {_MARKER} line.',
                     # A hit past the 200-character cut still refuses the line.
                     'Plain words ' * 20 + _MARKER):
            with self.subTest(text=text[:40]):
                seen = []
                status, developer = self._run(_cli_stdout(result=text), extra_patches=(
                    patch.object(task_submission_cli, '_emitted_sensitive',
                                 side_effect=_marker_screen(seen)),))
                self._assert_no_changes_evidence(status, developer)
                self.assertEqual(developer['no_changes_diagnosis']['reason'], 'UNKNOWN')
                self.assertTrue(any(_MARKER in value for value in seen))
                self.assertNotIn(_MARKER, json.dumps(developer))

    def test_non_printable_or_empty_result_gives_unknown(self):
        for text in ('Stopped here\x07', 'Hidden​mark', '', '\n  \n', _DROP, 7):
            with self.subTest(text=repr(text)):
                status, developer = self._run(_cli_stdout(result=text))
                self._assert_no_changes_evidence(status, developer)
                self.assertEqual(developer['no_changes_diagnosis']['reason'], 'UNKNOWN')

    def test_control_characters_are_refused_before_normalizing(self):
        # Each would otherwise be split away or stripped and leave a clean line.
        for text in ('Earlier line\x1bpart.\nNo edit was needed.',
                     'Part one\x1cNo edit was needed.',
                     'Part one\x1eNo edit was needed.',
                     'Part one\x85No edit was needed.',
                     'Part one\x0bNo edit was needed.',
                     'Part one\x0cNo edit was needed.',
                     '\tNo edit was needed.',
                     'No edit was needed.\x00',
                     'No edit was needed.\x7f',
                     '\x9bNo edit was needed.',
                     'Part one No edit was needed.'):
            with self.subTest(text=repr(text)):
                status, developer = self._run(_cli_stdout(result=text))
                self._assert_no_changes_evidence(status, developer)
                self.assertEqual(developer['no_changes_diagnosis']['reason'], 'UNKNOWN')

    def test_long_line_is_cut_to_200_characters(self):
        # Wording shares no 20-character run with the prompt (v93 echo rule).
        line = 'The loader already had this exact fix in place earlier. ' * 8
        status, developer = self._run(_cli_stdout(result='Intro.\n' + line))
        self._assert_no_changes_evidence(status, developer)
        reason = developer['no_changes_diagnosis']['reason']
        self.assertLessEqual(len(reason), 200)
        self.assertEqual(reason, line.strip()[:200].rstrip())
        self.assertNotIn('\n', reason)

    def test_reason_echoing_a_prompt_sentence_gives_unknown(self):
        sentence = 'Do not run tests, use Git, publish, or use network services.'
        for text in (
                # The whole sentence, exactly as sent.
                sentence,
                # A 20+ character run of it, other case and spacing.
                'Stopping:  DO NOT RUN TESTS,   use git, so nothing changed.',
                # A whole short line that was sent (the goal), under 20 characters.
                'claude   GOAL'):
            with self.subTest(text=text):
                prompts = []
                status, developer = self._run(_cli_stdout(result='Intro.\n' + text),
                                              prompts=prompts)
                self.assertEqual(len(prompts), 1)
                self.assertIn(sentence, prompts[0])
                self._assert_no_changes_evidence(status, developer)
                self.assertEqual(developer['no_changes_diagnosis']['reason'], 'UNKNOWN')

    def test_reason_echoing_a_context_fragment_gives_unknown(self):
        goal = 'Use the "strict" parser for the config loader.'
        for text in (
                # The goal is JSON-escaped in the prompt; its decoded text still counts.
                'I left THE "STRICT"   PARSER alone.',
                # A run of the acceptance item.
                'Did not Record The Bounded Result yet.'):
            with self.subTest(text=text):
                prompts = []
                status, developer = self._run(_cli_stdout(result=text), goal=goal,
                                              prompts=prompts)
                self.assertIn('\\"strict\\" parser', prompts[0])
                self._assert_no_changes_evidence(status, developer)
                self.assertEqual(developer['no_changes_diagnosis']['reason'], 'UNKNOWN')

    def test_genuine_short_reason_is_kept(self):
        prompts = []
        status, developer = self._run(_cli_stdout(result='The file already matched.'),
                                      prompts=prompts)
        self.assertNotIn('the file already matched', prompts[0].casefold())
        self._assert_no_changes_evidence(status, developer)
        self.assertEqual(developer['no_changes_diagnosis']['reason'],
                         'The file already matched.')

    def test_echo_check_fails_closed_without_sent_text(self):
        reason = task_submission_cli._claude_no_changes_reason
        self.assertEqual(reason('The file already matched.', ()), 'UNKNOWN')
        self.assertEqual(reason('The file already matched.', None), 'UNKNOWN')
        self.assertEqual(reason('The file already matched.', ('Unrelated sent text.',)),
                         'The file already matched.')

    def test_write_attempted_comes_only_from_structured_fields(self):
        write = {'tool_name': 'Write', 'tool_input': {'file_path': 'src.py'}}
        edit = {'tool_name': 'Edit', 'tool_input': {'file_path': 'src.py'}}
        read = {'tool_name': 'Read', 'tool_input': {}}
        cases = (
            # YES: an explicit write-tool permission denial.
            ({'permission_denials': [write]}, 'YES'),
            ({'permission_denials': [read, edit]}, 'YES'),
            ({'permission_denials': [write], 'num_turns': 1}, 'YES'),
            ({'permission_denials': [write], 'num_turns': _DROP}, 'YES'),
            # NO: a successful result of at most one turn and no denial.
            ({'permission_denials': [], 'num_turns': 1}, 'NO'),
            ({'permission_denials': [], 'num_turns': 0}, 'NO'),
            # UNKNOWN: absent denials alone do not prove no write was tried.
            ({'permission_denials': []}, 'UNKNOWN'),
            ({'permission_denials': [read]}, 'UNKNOWN'),
            ({'permission_denials': [read], 'num_turns': 1}, 'UNKNOWN'),
            ({'permission_denials': [], 'num_turns': _DROP}, 'UNKNOWN'),
            ({'permission_denials': [], 'num_turns': '1'}, 'UNKNOWN'),
            ({'permission_denials': [], 'num_turns': True}, 'UNKNOWN'),
            ({'permission_denials': [], 'num_turns': 1,
              'subtype': 'error_max_turns'}, 'UNKNOWN'),
            ({'permission_denials': [], 'num_turns': 1, 'is_error': True}, 'UNKNOWN'),
            ({'permission_denials': _DROP, 'num_turns': 1}, 'UNKNOWN'),
            ({'permission_denials': 'Write'}, 'UNKNOWN'),
            ({'permission_denials': [{'tool_name': 5}]}, 'UNKNOWN'),
            ({'permission_denials': [{'tool_name': 'Write'}, 'Edit']}, 'UNKNOWN'),
        )
        for fields, expected in cases:
            with self.subTest(fields=repr(fields)):
                # The result text never decides the value.
                status, developer = self._run(_cli_stdout(
                    result='I could not write the file.', **fields))
                self._assert_no_changes_evidence(status, developer)
                self.assertEqual(
                    developer['no_changes_diagnosis']['write_attempted'], expected)

    def test_num_turns_is_an_int_or_unknown(self):
        for turns, expected in ((0, 0), (12, 12), (_DROP, 'UNKNOWN'), (True, 'UNKNOWN'),
                                ('12', 'UNKNOWN'), (-1, 'UNKNOWN'), (1.5, 'UNKNOWN')):
            with self.subTest(turns=repr(turns)):
                status, developer = self._run(_cli_stdout(num_turns=turns))
                self._assert_no_changes_evidence(status, developer)
                self.assertEqual(developer['no_changes_diagnosis']['num_turns'], expected)

    def test_result_without_parsed_cli_object_gets_no_diagnosis(self):
        # The run returned a result but the route kept no parsed CLI object.
        status, developer = self._run(None)
        self._assert_no_changes_evidence(status, developer)
        self.assertNotIn('no_changes_diagnosis', developer)

    def test_other_outcomes_get_no_diagnosis(self):
        stdout = _cli_stdout(result='Edited the file.')
        status, developer = self._run(stdout, changed_paths=('outside.py',),
                                      raw=b'diff --git a/outside.py b/outside.py\n')
        self.assertEqual((status, developer['error_code']),
                         (1, 'CLAUDE_WRITE_SCOPE_VIOLATION'))
        self.assertNotIn('no_changes_diagnosis', developer)

        status, developer = self._run(stdout, changed_paths=('src.py',),
                                      raw=b'diff --git a/src.py b/src.py\n')
        self.assertEqual((status, developer['state']), (0, 'DEVELOPER_RESPONDED'))
        self.assertNotIn('no_changes_diagnosis', developer)

        for code in ('CLAUDE_TIMEOUT', 'CLAUDE_NO_CHANGES'):
            with self.subTest(code=code):
                # No parsed CLI result: the route failed before returning one.
                status, developer = self._run(stdout, runner_error=RouteError(code))
                self.assertEqual((status, developer['error_code']), (1, code))
                self.assertIsNone(developer['execution'])
                self.assertNotIn('no_changes_diagnosis', developer)


class ClaudeNoChangesDiagnosisStoreTests(unittest.TestCase):
    """TaskStore accepts no_changes_diagnosis strictly; old records stay valid."""

    def _dispatching(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = TaskStore(Path(temp.name).resolve() / 'tasks')
        task_id, executor_id = 'a' * 32, 'c' * 32
        request = 'Bounded Claude Task.'
        run_id = hashlib.sha256((task_id + 'run').encode()).hexdigest()[:32]
        task = task_submission_cli._record(task_id, run_id, 'pm-' + 'e' * 32,
            'pm-profile', request, state='PM_RESPONDED',
            provider_type='openai-compatible', model='pm-model',
            backend='NO_NATIVE_SESSION_ID_EXPOSED',
            request_sha=hashlib.sha256(request.encode()).hexdigest())
        task.update(pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                                      'base_url': None},
            response='PM accepted a bounded Claude assignment.', error_code=None,
            pm_assignment={'schema_version': 1, 'route': 'claude', 'risk': 'LOW',
                           'goal': 'Complete the bounded change.',
                           'acceptance': ['the requested changes are complete']})
        store.create(task)
        registration = SimpleNamespace(task_id=executor_id,
            trusted_agent_id='claude-developer', session_ref='session-ref',
            native_session_id=None, root='/registered/d6', common_dir='/repo/.git',
            branch='task-branch')
        pending = {
            'assignment_id': '1' * 32, 'selection_source': 'PM',
            'state': 'DEVELOPER_PENDING', 'provider_profile': 'claude',
            'execution': None, 'provider_type': None, 'model': None,
            'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
            'session_id': 'dev-' + '2' * 32, 'source_sha': 'f' * 40,
            'source_sha_binding': 'D6_REGISTRY_OBSERVED',
            'read_paths': ['src.py'], 'write_paths': ['src.py'],
            'acceptance': ['the requested changes are complete'],
            'context_sha256': '1' * 64,
            'egress_basis': 'Approved non-sensitive Task context.',
            'input_bytes': 10, 'max_tokens': None,
            'artifact_format': 'unified_diff', 'artifact': None,
            'artifact_sha256': None, 'usage': None, 'error_code': None,
            'created_at': task_submission_cli._timestamp(),
            'owner_assignment_ref': _OWNER_REF, 'executor_task_id': executor_id,
            'direct_result_callback': task_submission_cli._direct_result_callback(
                task, registration, 'f' * 40, test_policy='LOCAL'),
        }
        store.compare_and_update(task_id, expected={'developer': None}, developer=pending)
        dispatching = dict(pending, state='DEVELOPER_DISPATCHING',
                           provider_type='claude-cli', model='claude-test')
        store.compare_and_update(task_id, expected={'developer': pending},
                                 developer=dispatching)
        blocked = dict(dispatching, state='BLOCKED', error_code='CLAUDE_NO_CHANGES',
            usage={'prompt_tokens': 5, 'completion_tokens': 2, 'total_tokens': 7},
            execution={'status': 'BLOCKED', 'native_session_id': _SESSION,
                'process_exit_code': 0, 'stdout_sha256': 'e' * 64,
                'elapsed_ms': 42, 'cleanup_status': 'CONFIRMED',
                'usage_source': 'CLI_REPORTED', 'source_bytes': None,
                'source_file_count': None, 'source_manifest_sha256': None})
        return store, task_id, dispatching, blocked

    def _assert_refused(self, store, task_id, dispatching, developer):
        with self.assertRaises(LLMToolError) as refused:
            store.compare_and_update(task_id, expected={'developer': dispatching},
                                     developer=developer)
        self.assertEqual(getattr(refused.exception, 'task_store_error_code', None),
                         'TASK_STORE_INVALID')
        self.assertEqual(store.get(task_id)['developer'], dispatching)

    def test_legacy_blocked_record_without_diagnosis_is_valid(self):
        store, task_id, dispatching, blocked = self._dispatching()
        saved = store.compare_and_update(task_id, expected={'developer': dispatching},
                                         developer=blocked)
        self.assertNotIn('no_changes_diagnosis', saved['developer'])
        self.assertNotIn('no_changes_diagnosis', store.get(task_id)['developer'])

    def test_valid_diagnosis_is_kept(self):
        for diagnosis in ({'reason': 'No edit was needed.', 'write_attempted': 'NO',
                           'num_turns': 4},
                          {'reason': 'UNKNOWN', 'write_attempted': 'YES', 'num_turns': 0},
                          {'reason': 'UNKNOWN', 'write_attempted': 'UNKNOWN',
                           'num_turns': 'UNKNOWN'},
                          {'reason': 'x' * 200, 'write_attempted': 'NO', 'num_turns': 1}):
            with self.subTest(diagnosis=diagnosis['reason'][:20]):
                store, task_id, dispatching, blocked = self._dispatching()
                developer = dict(blocked, no_changes_diagnosis=diagnosis)
                store.compare_and_update(task_id, expected={'developer': dispatching},
                                         developer=developer)
                self.assertEqual(store.get(task_id)['developer']['no_changes_diagnosis'],
                                 diagnosis)

    def test_invalid_diagnosis_is_refused(self):
        store, task_id, dispatching, blocked = self._dispatching()
        good = {'reason': 'No edit was needed.', 'write_attempted': 'NO', 'num_turns': 4}
        bad_diagnoses = (
            None, 'No edit was needed.', [],
            {'reason': 'No edit was needed.', 'write_attempted': 'NO'},
            dict(good, extra='value'),
            dict(good, reason=''), dict(good, reason='x' * 201),
            dict(good, reason='two\nlines'), dict(good, reason=' padded'),
            dict(good, reason='bell\x07'), dict(good, reason='hidden​mark'),
            dict(good, reason=5),
            dict(good, write_attempted='MAYBE'), dict(good, write_attempted=True),
            dict(good, num_turns=True), dict(good, num_turns=-1),
            dict(good, num_turns='4'), dict(good, num_turns=1.0),
            dict(good, num_turns=1_000_001),
        )
        for diagnosis in bad_diagnoses:
            with self.subTest(diagnosis=repr(diagnosis)[:60]):
                self._assert_refused(store, task_id, dispatching,
                                     dict(blocked, no_changes_diagnosis=diagnosis))
        for other in (dict(blocked, error_code='CLAUDE_WRITE_SCOPE_VIOLATION'),
                      dict(blocked, execution=None),
                      dict(dispatching)):
            with self.subTest(error_code=other['error_code'], state=other['state']):
                self._assert_refused(store, task_id, dispatching,
                                     dict(other, no_changes_diagnosis=good))

    def test_screened_reason_is_refused_by_the_store(self):
        store, task_id, dispatching, blocked = self._dispatching()
        seen = []
        developer = dict(blocked, no_changes_diagnosis={
            'reason': f'The {_MARKER} line.', 'write_attempted': 'NO', 'num_turns': 2})
        with patch.object(task_store_module, '_emitted_sensitive',
                          side_effect=_marker_screen(seen)):
            self._assert_refused(store, task_id, dispatching, developer)
        self.assertIn(f'The {_MARKER} line.', seen)


if __name__ == '__main__':
    unittest.main()
