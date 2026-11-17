"""#2275: a failed or no-change Claude run keeps structured evidence only.

Every case drives the real path: a fake Claude CLI process output goes through
the real run_route and ClaudeRoute parser in a real temporary Git D6, then the
real run_task_claude_delegate failure handling, then a real TaskStore in a
temporary store directory, and the record is read back (and printed by
``task show``). No record is built by hand for the store.

Test data rule (Owner #763 A): no test here holds a password-, API-key-,
token- or other credential-shaped string, and no screen function is touched.
The provider text is a plain marker word that must never be stored or printed.
"""

import contextlib
import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import task_completion
from llmtool import task_store as task_store_module
from llmtool import task_submission_cli
from llmtool import worker_dispatch
from llmtool.developer_routes import claude as claude_module
from llmtool.developer_routes import run_route
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore

try:
    import review_trust_fixture
    import worker_policy_fixture
except ImportError:
    from tests import review_trust_fixture
    from tests import worker_policy_fixture


_SESSION = '12345678-1234-4234-8234-123456789abc'
_OWNER_REF = ('https://github.com/davidcatcat/davidcattoolsproject/'
              'issues/289#issuecomment-1234567890')
_MODEL = 'claude-test-1'
_TASK_ID = 'a' * 32
_EXECUTOR_ID = 'c' * 32
_ACCEPTANCE = ['the requested changes are complete']
_PROSE = 'PROSE-MARKER'
_DROP = object()
_EVIDENCE = 'claude_failure_evidence'


def setUpModule():
    review_trust_fixture.start_module()
    worker_policy_fixture.start_module()


def tearDownModule():
    worker_policy_fixture.stop_module()
    review_trust_fixture.stop_module()


def _cli(**fields):
    """A Claude CLI JSON result; a field set to _DROP is omitted."""
    obj = {'type': 'result', 'subtype': 'success', 'is_error': False,
           'session_id': _SESSION, 'result': f'Provider prose {_PROSE}.',
           'num_turns': 3, 'duration_ms': 4321, 'permission_denials': [],
           'usage': {'input_tokens': 3, 'output_tokens': 2},
           'modelUsage': {_MODEL: {'outputTokens': 2}}}
    obj.update(fields)
    return json.dumps({key: value for key, value in obj.items()
                       if value is not _DROP}).encode('utf-8')


def _process(code, stdout=b'', stderr=b'', *, write=None, error=None, content='after\n'):
    """The fake Claude CLI process run_route starts (argv, cwd, env, timeout)."""
    def run(_argv, cwd, _env, _timeout):
        if error is not None:
            raise error
        if write is not None:
            (Path(cwd) / write).write_text(content)
        return code, stdout, stderr
    run.last_cleanup_status = 'CONFIRMED'
    return run


class _Boundary:
    """A verified write boundary; the D6 checks in run_route stay real."""

    policy = 'a' * 64

    def prepare(self, _root, _paths, _state_dirs=()):
        return (), self.policy

    def verify(self, _root, _paths, _state_dirs=(), *, runner):
        return {'verified': True, 'policy_sha256': self.policy}


class _Dispatch:
    """worker_dispatch for the delegate only: evaluate allows, all else is real."""

    def __getattr__(self, name):
        return getattr(worker_dispatch, name)

    @staticmethod
    def evaluate(*_args, **_kwargs):
        return SimpleNamespace(allowed=True, reason_code='OWNER_ASSIGNMENT_MATCHED')


class _Shutil:
    """shutil for the delegate only: which finds the fake CLI path."""

    def __getattr__(self, name):
        return getattr(shutil, name)

    @staticmethod
    def which(_name):
        return '/fake/bin/claude'


class _MutatingStore(TaskStore):
    """A real TaskStore; mutate (if set) rewrites the BLOCKED developer write."""

    mutate = None

    def update(self, task_id, **changes):
        developer = changes.get('developer')
        if (self.mutate is not None and type(developer) is dict and
                developer.get('state') == 'BLOCKED'):
            changes['developer'] = self.mutate(dict(developer))
        return super().update(task_id, **changes)


def _git(root, *args):
    git = shutil.which('git') or '/usr/bin/git'
    subprocess.run([git, '-C', str(root), '-c', 'user.name=Test',
                    '-c', 'user.email=test@example.invalid',
                    '-c', 'commit.gpgsign=false', *args],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class ClaudeFailureEvidenceTests(unittest.TestCase):

    def _d6(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        root = base / 'd6'
        root.mkdir()
        git = shutil.which('git') or '/usr/bin/git'
        subprocess.run([git, 'init', str(root)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (root / 'src.py').write_text('before\n')
        _git(root, 'add', 'src.py')
        _git(root, 'commit', '-m', 'initial')
        snapshot = task_completion.collect_git_snapshot(str(root))
        registration = SimpleNamespace(
            task_id=_EXECUTOR_ID, trusted_agent_id='claude-developer',
            session_ref='session-ref', native_session_id=None, root=snapshot.root,
            common_dir=snapshot.common_dir, branch=snapshot.branch,
            source_sha=snapshot.head)
        return base, registration, snapshot

    def _store(self, base, store_class):
        store = store_class(base / 'tasks')
        request = 'Bounded Claude Task.'
        run_id = hashlib.sha256((_TASK_ID + 'run').encode()).hexdigest()[:32]
        task = task_submission_cli._record(
            _TASK_ID, run_id, 'pm-' + 'e' * 32, 'pm-profile', request,
            state='PM_RESPONDED', provider_type='openai-compatible', model='pm-model',
            backend='NO_NATIVE_SESSION_ID_EXPOSED',
            request_sha=hashlib.sha256(request.encode()).hexdigest())
        task.update(pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                                      'base_url': None},
                    response='PM accepted a bounded Claude assignment.', error_code=None,
                    pm_assignment={'schema_version': 1, 'route': 'claude', 'risk': 'LOW',
                                   'goal': 'Complete the bounded change.',
                                   'acceptance': list(_ACCEPTANCE)})
        store.create(task)
        return store

    def _run(self, process, *, mutate=None, route_config=None, registry_after=None):
        """Run the real delegate once; return (status, store, printed text).

        route_config adds keys to the Claude route configuration;
        registry_after(registration) gives the D6 registry entry the delegate
        reads back after the route result (default: unchanged)."""
        base, registration, snapshot = self._d6()
        after = registration if registry_after is None else registry_after(registration)
        store = self._store(base, _MutatingStore)
        store.mutate = mutate
        args = SimpleNamespace(
            task_id=_TASK_ID, owner_ref=_OWNER_REF, mode='direct', context_stdin=False,
            selection_source='PM', acceptance=list(_ACCEPTANCE),
            read_path=['src.py'], write_path=['src.py'],
            egress_basis='Approved non-sensitive Task context.', repair_packet=None,
            executor_task=_EXECUTOR_ID)

        def route_runner(route, request, **kwargs):
            # The real run_route, with the fake CLI process as its runner.
            return run_route(route, request, runner=process, **kwargs)

        cli = task_submission_cli
        patches = (
            patch.object(cli, 'worker_dispatch', _Dispatch()),
            patch.object(cli, 'shutil', _Shutil()),
            patch.object(cli, '_delegate_d6_binding', return_value=(registration, snapshot)),
            patch.object(cli, 'load_route_config',
                         return_value={'routes': {'claude': {'denied_dirs': [],
                                                             **(route_config or {})}}}),
            patch.object(cli, '_claude_developer_profile',
                         return_value=(_MODEL, 'low', '/fake/claude-config')),
            patch.object(cli, 'for_platform', return_value=_Boundary()),
            patch.object(cli, '_developer_card_prefix', return_value=('', 'b' * 64)),
            patch.object(cli, '_remote_receipt_test_policy', return_value='LOCAL'),
            patch.object(cli, '_delegate_chain_gate',
                         side_effect=lambda _args, _store, _binding, claim: claim()),
            patch.object(cli, '_record_skill_delivery'),
            patch.object(cli, '_claude_committed_carry', return_value=None),
            patch.object(cli, 'run_task_direct_result_event', return_value=0),
            patch.object(task_completion, 'bind_executor_task',
                         return_value=(registration, snapshot)),
            patch.object(task_completion, 'read_d6_registration', return_value=after),
            patch.object(claude_module.github_identity, 'ai_github_env',
                         side_effect=lambda env, _home: env),
        )
        printed = io.StringIO()
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            stack.enter_context(contextlib.redirect_stdout(printed))
            status = cli.run_task_claude_delegate(args, store=store,
                                                  route_runner=route_runner)
        return status, store, printed.getvalue()

    def _assert_failure(self, process, code, expected, *, prose_free=True, **run_options):
        status, store, printed = self._run(process, **run_options)
        self.assertEqual(status, 1)
        record = store.get(_TASK_ID)
        developer = record['developer']
        self.assertEqual((developer['state'], developer['error_code']), ('BLOCKED', code))
        self.assertEqual(developer['execution']['status'], 'BLOCKED')
        self.assertEqual(developer.get(_EVIDENCE), expected)
        for key, value in expected.items():
            # Enum, bool and int values only; never provider text.
            self.assertIn(type(value), (bool, int) if key != 'subtype' else (str,))
        if 'subtype' in expected:
            self.assertIn(expected['subtype'], task_store_module.CLAUDE_CLI_SUBTYPES)
        # The failure path prints the task view; it carries the same evidence.
        view = json.loads(printed.strip().splitlines()[-1])
        self.assertEqual(view['developer'].get(_EVIDENCE), expected)
        shown = io.StringIO()
        self.assertEqual(task_submission_cli.run_task_show(
            SimpleNamespace(task_id=_TASK_ID), store=store, stdout=shown), 0)
        self.assertEqual(json.loads(shown.getvalue())['developer'].get(_EVIDENCE), expected)
        if prose_free:
            self.assertNotIn(_PROSE, json.dumps(record))
            self.assertNotIn(_PROSE, printed)
            self.assertNotIn(_PROSE, shown.getvalue())
        return store, developer

    def test_subtype_enum_matches_the_store(self):
        self.assertEqual(set(claude_module.CLI_SUBTYPES.values()) |
                         {claude_module.CLI_SUBTYPE_OTHER},
                         set(task_store_module.CLAUDE_CLI_SUBTYPES))
        self.assertEqual(claude_module.CLI_MAX_TURNS,
                         task_store_module.CLAUDE_FAILURE_MAX_TURNS)
        self.assertEqual(claude_module.CLI_MAX_DURATION_MS,
                         task_store_module.CLAUDE_FAILURE_MAX_DURATION_MS)

    def test_status_failed_keeps_structured_fields(self):
        cases = (('error_max_turns', 'ERROR_MAX_TURNS'),
                 ('error_during_execution', 'ERROR_DURING_EXECUTION'),
                 ('error_max_budget_usd', 'ERROR_MAX_BUDGET_USD'))
        for subtype, expected in cases:
            with self.subTest(subtype=subtype):
                self._assert_failure(
                    _process(1, _cli(subtype=subtype, is_error=True, num_turns=12,
                                     duration_ms=98765), f'{_PROSE} stderr'.encode()),
                    'CLAUDE_STATUS_FAILED',
                    {'subtype': expected, 'is_error': True, 'num_turns': 12,
                     'duration_ms': 98765, 'process_exit_code': 1})

    def test_unknown_subtype_is_other(self):
        self._assert_failure(
            _process(1, _cli(subtype='error_brand_new_kind', is_error=True)),
            'CLAUDE_STATUS_FAILED',
            {'subtype': 'OTHER', 'is_error': True, 'num_turns': 3,
             'duration_ms': 4321, 'process_exit_code': 1})

    def test_output_invalid_keeps_only_what_was_observed(self):
        # Not JSON: no CLI field exists, so only the exit code is kept.
        self._assert_failure(
            _process(1, f'plain words {_PROSE}'.encode(), f'{_PROSE} stderr'.encode()),
            'CLAUDE_OUTPUT_INVALID', {'process_exit_code': 1})
        # A refused result object still gives its well-typed fields.
        self._assert_failure(
            _process(1, _cli(session_id='not-a-session', subtype='error_during_execution',
                             is_error=True)),
            'CLAUDE_OUTPUT_INVALID',
            {'subtype': 'ERROR_DURING_EXECUTION', 'is_error': True, 'num_turns': 3,
             'duration_ms': 4321, 'process_exit_code': 1})
        # A missing subtype is absent, not invented.
        self._assert_failure(
            _process(1, _cli(subtype=_DROP, is_error=True)),
            'CLAUDE_OUTPUT_INVALID',
            {'is_error': True, 'num_turns': 3, 'duration_ms': 4321,
             'process_exit_code': 1})

    def test_missing_or_ill_typed_fields_are_absent(self):
        cases = ({'num_turns': _DROP, 'duration_ms': _DROP},
                 {'num_turns': True, 'duration_ms': '4321'},
                 {'num_turns': -1, 'duration_ms': 1.5},
                 {'num_turns': 1_000_001, 'duration_ms': 86_400_001})
        for fields in cases:
            with self.subTest(fields=repr(fields)):
                self._assert_failure(
                    _process(1, _cli(subtype='error_during_execution', is_error=True,
                                     **fields)),
                    'CLAUDE_STATUS_FAILED',
                    {'subtype': 'ERROR_DURING_EXECUTION', 'is_error': True,
                     'process_exit_code': 1})

    def test_quota_exhausted_keeps_structured_fields(self):
        _store, developer = self._assert_failure(
            _process(1, _cli(subtype='error_during_execution', is_error=True,
                             api_error_status=429)),
            'CLAUDE_QUOTA_EXHAUSTED',
            {'subtype': 'ERROR_DURING_EXECUTION', 'is_error': True, 'num_turns': 3,
             'duration_ms': 4321, 'process_exit_code': 1})
        self.assertEqual(developer['execution']['provider_evidence'],
                         {'condition': 'QUOTA_EXHAUSTED'})
        # Quota stderr with non-JSON stdout: no CLI field, and no stderr text kept.
        self._assert_failure(
            _process(1, b'not json', f'usage limit reached {_PROSE}'.encode()),
            'CLAUDE_QUOTA_EXHAUSTED', {'process_exit_code': 1})

    def test_other_post_launch_route_errors_keep_evidence(self):
        self._assert_failure(
            _process(2, _cli()), 'CLAUDE_EXIT_NONZERO',
            {'subtype': 'SUCCESS', 'is_error': False, 'num_turns': 3,
             'duration_ms': 4321, 'process_exit_code': 2})
        self._assert_failure(
            _process(-9, _cli()), 'CLAUDE_INTERRUPTED',
            {'subtype': 'SUCCESS', 'is_error': False, 'num_turns': 3,
             'duration_ms': 4321, 'process_exit_code': -9})

    def test_no_changes_keeps_structured_fields(self):
        _store, developer = self._assert_failure(
            _process(0, _cli(result='The file already matched.')), 'CLAUDE_NO_CHANGES',
            {'subtype': 'SUCCESS', 'is_error': False, 'num_turns': 3,
             'duration_ms': 4321, 'process_exit_code': 0})
        # The #1824 diagnosis is unchanged and separate.
        self.assertIn('no_changes_diagnosis', developer)

    def test_output_too_large_after_a_route_result_keeps_evidence(self):
        # The run succeeds and edits only src.py; the delegate then refuses the
        # diff against the configured route cap (the minimum, 1024 bytes).
        _store, developer = self._assert_failure(
            _process(0, _cli(num_turns=7, duration_ms=2468), write='src.py',
                     content='a line of plain text\n' * 100),
            'CLAUDE_OUTPUT_TOO_LARGE',
            {'subtype': 'SUCCESS', 'is_error': False, 'num_turns': 7,
             'duration_ms': 2468, 'process_exit_code': 0},
            route_config={'max_diff_bytes': 1024})
        self.assertNotIn('no_changes_diagnosis', developer)

    def test_d6_binding_changed_after_a_route_result_keeps_evidence(self):
        # The run succeeds; the D6 registry entry read back after it differs.
        def moved(registration):
            return SimpleNamespace(**dict(vars(registration),
                                          session_ref='session-ref-moved'))
        _store, developer = self._assert_failure(
            _process(0, _cli(num_turns=5, duration_ms=1357), write='src.py'),
            'CLAUDE_D6_BINDING_CHANGED',
            {'subtype': 'SUCCESS', 'is_error': False, 'num_turns': 5,
             'duration_ms': 1357, 'process_exit_code': 0},
            registry_after=moved)
        self.assertNotIn('no_changes_diagnosis', developer)

    def test_failure_before_a_process_result_keeps_none(self):
        status, store, _printed = self._run(_process(0, error=TimeoutError()))
        self.assertEqual(status, 1)
        developer = store.get(_TASK_ID)['developer']
        self.assertEqual(developer['error_code'], 'CLAUDE_TIMEOUT')
        self.assertIsNone(developer['execution'])
        self.assertNotIn(_EVIDENCE, developer)

    def test_successful_run_is_unchanged(self):
        status, store, _printed = self._run(_process(0, _cli(), write='src.py'))
        self.assertEqual(status, 0)
        developer = store.get(_TASK_ID)['developer']
        self.assertEqual(developer['state'], 'DEVELOPER_RESPONDED')
        self.assertIsNone(developer['error_code'])
        self.assertNotIn(_EVIDENCE, developer)

    def test_no_work_proof_is_unchanged(self):
        _store, developer = self._assert_failure(
            _process(1, _cli(subtype='error_max_turns', is_error=True)),
            'CLAUDE_STATUS_FAILED',
            {'subtype': 'ERROR_MAX_TURNS', 'is_error': True, 'num_turns': 3,
             'duration_ms': 4321, 'process_exit_code': 1})
        without = {key: value for key, value in developer.items() if key != _EVIDENCE}
        for probe in (lambda _sha, write_paths=None: True,
                      lambda _sha, write_paths=None: False):
            self.assertEqual(
                task_submission_cli._launched_run_did_no_work(developer, probe),
                task_submission_cli._launched_run_did_no_work(without, probe))

    def test_record_without_evidence_still_loads(self):
        def drop(developer):
            developer.pop(_EVIDENCE)
            return developer
        status, store, _printed = self._run(
            _process(1, _cli(subtype='error_max_turns', is_error=True)), mutate=drop)
        self.assertEqual(status, 1)
        developer = store.get(_TASK_ID)['developer']
        self.assertEqual(developer['error_code'], 'CLAUDE_STATUS_FAILED')
        self.assertNotIn(_EVIDENCE, developer)

        # A blocked record without execution facts loads; with evidence it is refused.
        def no_execution(developer):
            developer['execution'] = None
            developer.pop(_EVIDENCE)
            return developer
        status, store, _printed = self._run(
            _process(1, _cli(subtype='error_max_turns', is_error=True)),
            mutate=no_execution)
        self.assertEqual(status, 1)
        self.assertIsNone(store.get(_TASK_ID)['developer']['execution'])

    def test_store_refuses_invalid_evidence(self):
        good = {'subtype': 'ERROR_MAX_TURNS', 'is_error': True, 'num_turns': 3,
                'duration_ms': 4321, 'process_exit_code': 1}
        bad_values = (
            None, [], {}, 'ERROR_MAX_TURNS',
            dict(good, extra=1),
            dict(good, subtype='error_max_turns'), dict(good, subtype='NEW_KIND'),
            dict(good, subtype=1),
            dict(good, is_error=1), dict(good, is_error='true'),
            dict(good, num_turns=True), dict(good, num_turns=-1),
            dict(good, num_turns='3'), dict(good, num_turns=1_000_001),
            dict(good, duration_ms=1.5), dict(good, duration_ms=86_400_001),
            dict(good, process_exit_code=256), dict(good, process_exit_code=True),
        )
        cases = [(repr(value)[:60], lambda developer, value=value: dict(
            developer, **{_EVIDENCE: value})) for value in bad_values]
        # The same record without evidence loads (test_record_without_evidence_still_loads).
        cases.append(('execution None', lambda developer: dict(developer, execution=None)))
        for label, mutate in cases:
            with self.subTest(case=label):
                with self.assertRaises(LLMToolError) as refused:
                    self._run(_process(1, _cli(subtype='error_max_turns', is_error=True)),
                              mutate=mutate)
                self.assertEqual(getattr(refused.exception, 'task_store_error_code', None),
                                 'TASK_STORE_INVALID')


if __name__ == '__main__':
    unittest.main()
