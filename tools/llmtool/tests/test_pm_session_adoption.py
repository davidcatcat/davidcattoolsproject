"""PM session adoption end to end (#683).

A real TaskStore and real session locks in a temporary HOME, an injected process
lister, and no provider. Sessions exist only as local transcripts and bindings.
"""

import hashlib
import io
import json
import os
import tempfile
import threading
import unittest
import uuid
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from llmtool import active_pm_inbox, pm_runtime, worker_dispatch
from llmtool import task_store as task_store_module
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore
from llmtool.task_submission_cli import run_task_delegate, run_task_record_pm
from review_trust_fixture import start_module as _start_trust_fixture
from review_trust_fixture import stop_module as _stop_trust_fixture

# Reuse the store's Developer fixture without re-collecting its tests.
import test_task_store as store_fixture


def setUpModule():
    _start_trust_fixture()


def tearDownModule():
    _stop_trust_fixture()


NOW = 1800000000.0
CLEAN_PS = '1 /sbin/launchd\n2 /usr/bin/other --flag value\n'
AT = '2026-11-07T00:00:00Z'
ACCEPTANCE = 'the proposal is recorded'
ARTIFACT = 'diff --git a/src.py b/src.py\n--- a/src.py\n+++ b/src.py\n@@ -1 +1 @@\n-x=1\n+x=2\n'
OWNER_REF = 'https://github.com/davidcatcat/davidcattoolsproject/issues/289#issuecomment-1'
CLAUDE_STOPPED = ['PROCESS_CLEAN', 'LOCK_FREE', 'REGISTRY_CLEAN', 'TRANSCRIPT_QUIET']
CODEX_STOPPED = ['PROCESS_CLEAN', 'LOCK_FREE']
_RESPONSE = json.dumps({'schema_version': 1, 'summary': 'Active PM plan.',
    'developer_assignment': {'route': 'codex', 'risk': 'LOW',
        'goal': 'Produce a proposal diff.', 'acceptance': [ACCEPTANCE]}})


def _failing_lister():
    raise OSError('ps is unavailable')


class AdoptionCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve()
        self.cwd = self.home / 'repo'
        self.cwd.mkdir()
        self.state = self.home / 'state'
        pm_runtime._ensure_state_dir(self.state)
        self.first = self.home / 'claude-one'
        self.second = self.home / 'claude-two'
        self.codex_dir = self.home / 'codex-account'
        for path in (self.first, self.second, self.codex_dir):
            path.mkdir(mode=0o700)
        (self.first / 'sessions').mkdir(mode=0o700)
        config = self.home / '.config/llmtool/config.yaml'
        config.parent.mkdir(parents=True)
        config.write_text(
            'accounts:\n'
            '  claude-one:\n    kind: claude\n    config_dir: ' + str(self.first) + '\n'
            '  claude-two:\n    kind: claude\n    config_dir: ' + str(self.second) + '\n'
            '  codex-pm:\n    kind: codex\n    config_dir: ' + str(self.codex_dir) + '\n',
            encoding='utf-8')
        # Bound sessions resolve their own account, so they run with no directory override.
        self.account_env = {'HOME': str(self.home), 'PATH': '/usr/bin:/bin'}
        self.environ = {**self.account_env, 'CLAUDE_CONFIG_DIR': str(self.first),
                        'CODEX_HOME': str(self.codex_dir)}
        environment = patch.dict(os.environ, self.environ)
        environment.start()
        self.addCleanup(environment.stop)
        self.store = TaskStore(self.home / 'tasks')

    def claude_transcript(self, native, config_dir=None, age=1000):
        path = active_pm_inbox.claude_session_file(
            native, str(self.cwd), {'CLAUDE_CONFIG_DIR': str(config_dir or self.first)})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}\n', encoding='utf-8')
        os.utime(path, (NOW - age, NOW - age))
        return path

    def codex_transcript(self, session_id):
        path = (self.codex_dir / 'sessions' / '2026' / '11' / '07' /
                ('rollout-20261107-120000-' + session_id + '.jsonl'))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            'timestamp': AT, 'type': 'session_meta',
            'payload': {'id': session_id, 'cwd': str(self.cwd)}}) + '\n', encoding='utf-8')
        return path

    def bind(self, runtime='claude-code', account='claude-one', cwd=None):
        return pm_runtime.create_dedicated_pm(
            str(cwd or self.cwd), runtime=runtime, account=account, state_root=self.state,
            environ=self.account_env, home=str(self.home))['native_session_id']

    def save_thread(self, native, thread):
        pm_runtime._write_json_atomic(
            self.state, pm_runtime._runtime_state_path(self.state, 'pm-' + native),
            {'codex_thread_id': thread})

    def record_task(self, native, runtime='claude-code'):
        return run_task_record_pm(
            Namespace(stdin_json=True, pm_session_id=native, pm_runtime=runtime,
                      pm_cwd=str(self.cwd)),
            store=self.store, return_record=True,
            input_stream=io.StringIO(json.dumps({'request': 'Add a bounded test.',
                                                 'response': _RESPONSE})))

    def stopped_claude(self, native=None, age=1000):
        """(session, transcript, Task) for a Claude session whose transcript has gone quiet."""
        native = native or str(uuid.uuid4())
        transcript = self.claude_transcript(native, age=age)
        return native, transcript, self.record_task(native)

    def new_claude(self):
        native = str(uuid.uuid4())
        self.claude_transcript(native)
        return native

    def adopt(self, old, new, **overrides):
        options = {'environ': self.environ, 'state_root': self.state, 'clock': lambda: NOW,
                   'process_lister': lambda: CLEAN_PS}
        options.update(overrides)
        return active_pm_inbox.adopt_pm_session(self.store, 'pm-' + old, 'pm-' + new,
                                                old_native_id=old, cwd=str(self.cwd), **options)

    def fence(self, native):
        return pm_runtime.read_session_fence(native, self.state)

    def assert_refused(self, code, old, new, task, **options):
        """The refusal carries ``code`` and changes neither the Task nor the fence."""
        before, fence = self.store.get(task['task_id']), self.fence(old)
        with self.assertRaises(LLMToolError) as caught:
            self.adopt(old, new, **options)
        self.assertEqual(caught.exception.task_store_error_code, code)
        self.assertEqual(self.store.get(task['task_id']), before)
        self.assertEqual(self.fence(old), fence)

    def assert_moved(self, task, old, new, *, old_runtime='claude-code',
                     new_runtime='claude-code', new_account=None, liveness=None):
        record = self.store.get(task['task_id'])
        self.assertEqual(record['pm_session_id'], 'pm-' + new)
        self.assertEqual(record['pm_provenance'], {
            **task['pm_provenance'], 'pm_runtime': new_runtime, 'pm_native_session_id': new})
        (audit,) = record['pm_session_adoptions']
        self.assertEqual({key: value for key, value in audit.items() if key != 'at'}, {
            'old_session_key': 'pm-' + old, 'new_session_key': 'pm-' + new,
            'old_runtime': old_runtime, 'new_runtime': new_runtime,
            'new_account': new_account, 'liveness': liveness or CLAUDE_STOPPED})
        self.assertEqual(self.fence(old)['new_session_key'], 'pm-' + new)


class LivenessGateTests(AdoptionCase):
    def test_a_running_process_refuses_and_leaves_the_transcript(self):
        old, transcript, task = self.stopped_claude()
        self.assert_refused('PM_SESSION_ADOPTION_OLD_PM_ACTIVE', old, self.new_claude(), task,
                            process_lister=lambda: '77 claude --resume ' + old + '\n')
        self.assertEqual(transcript.read_bytes(), b'{}\n')

    def test_a_held_session_lock_refuses_as_active(self):
        old, _, task = self.stopped_claude()
        new = self.new_claude()
        with pm_runtime.transfer_lock(old, self.state):
            self.assert_refused('PM_SESSION_ADOPTION_OLD_PM_ACTIVE', old, new, task)

    def test_a_live_registry_entry_refuses(self):
        old, _, task = self.stopped_claude()
        pid = os.getpid()
        (self.first / 'sessions' / (str(pid) + '.json')).write_text(
            json.dumps({'sessionId': old, 'pid': pid, 'cwd': str(self.cwd)}), encoding='utf-8')
        self.assert_refused('PM_SESSION_ADOPTION_OLD_PM_ACTIVE', old, self.new_claude(), task)

    def test_a_recently_written_transcript_refuses(self):
        old, _, task = self.stopped_claude(age=1)
        self.assert_refused('PM_SESSION_ADOPTION_OLD_PM_ACTIVE', old, self.new_claude(), task)

    def test_uncertain_liveness_refuses(self):
        old, _, task = self.stopped_claude()
        self.assert_refused('PM_SESSION_ADOPTION_LIVENESS_UNKNOWN', old, self.new_claude(), task,
                            process_lister=_failing_lister)

    def test_an_unbound_claude_session_with_no_transcript_left_is_uncertain(self):
        old, transcript, task = self.stopped_claude()
        transcript.unlink()
        self.assert_refused('PM_SESSION_ADOPTION_LIVENESS_UNKNOWN', old, self.new_claude(), task)
        self.assertIsNone(self.fence(old))

    def test_a_bound_claude_session_needs_no_transcript(self):
        old, transcript, task = self.stopped_claude(self.bind())
        transcript.unlink()
        new = self.new_claude()
        self.assertEqual(self.adopt(old, new), {'moved': [task['task_id']], 'skipped': []})
        self.assert_moved(task, old, new,
                          liveness=['PROCESS_CLEAN', 'LOCK_FREE', 'REGISTRY_CLEAN'])

    def test_an_unbound_codex_session_is_uncertain(self):
        old = str(uuid.uuid4())
        self.codex_transcript(old)
        task = self.record_task(old, 'codex')
        new = str(uuid.uuid4())
        self.codex_transcript(new)
        self.assert_refused('PM_SESSION_ADOPTION_LIVENESS_UNKNOWN', old, new, task)

    def test_an_old_binding_of_another_runtime_is_uncertain(self):
        old, _, task = self.stopped_claude(self.bind('codex', 'codex-pm'))
        self.assert_refused('PM_SESSION_ADOPTION_LIVENESS_UNKNOWN', old, self.new_claude(), task)

    def test_a_stopped_unbound_pm_is_adopted_and_its_transcript_kept(self):
        old, transcript, task = self.stopped_claude()
        modified = transcript.stat().st_mtime
        new = self.new_claude()
        self.assertEqual(self.adopt(old, new), {'moved': [task['task_id']], 'skipped': []})
        self.assert_moved(task, old, new)
        self.assertEqual(transcript.read_bytes(), b'{}\n')
        self.assertEqual(transcript.stat().st_mtime, modified)

    def test_a_stopped_bound_pm_is_adopted_under_the_real_lock(self):
        old, transcript, task = self.stopped_claude(self.bind())
        new = self.new_claude()
        self.assertEqual(self.adopt(old, new), {'moved': [task['task_id']], 'skipped': []})
        self.assert_moved(task, old, new)
        self.assertEqual(transcript.read_bytes(), b'{}\n')
        with pm_runtime.transfer_lock(old, self.state):
            pass  # Released: taking it again does not raise PM_SESSION_BUSY.

    def test_no_eligible_task_writes_no_fence_and_skips_the_liveness_check(self):
        old, _, task = self.stopped_claude()
        self.store.abandon(task['task_id'], pm_session_id='pm-' + old, reason='Closed first')
        before = self.store.get(task['task_id'])
        result = self.adopt(old, str(uuid.uuid4()), process_lister=_failing_lister)
        self.assertEqual(result, {'moved': [], 'skipped': []})
        self.assertIsNone(self.fence(old))
        self.assertEqual(self.store.get(task['task_id']), before)


class FenceAndRetryTests(AdoptionCase):
    def test_a_fence_naming_another_session_refuses(self):
        old, _, task = self.stopped_claude()
        pm_runtime.write_session_fence(old, 'pm-' + str(uuid.uuid4()), AT, self.state)
        self.assert_refused('PM_SESSION_ADOPTION_FENCED_OTHER_TARGET', old, self.new_claude(),
                            task)

    def test_an_unreadable_fence_matches_no_target(self):
        old, _, task = self.stopped_claude()
        pm_runtime._fence_path(self.state, 'pm-' + old).write_text('{', encoding='utf-8')
        self.assert_refused('PM_SESSION_ADOPTION_FENCED_OTHER_TARGET', old, self.new_claude(),
                            task)

    def test_a_fenced_new_session_refuses(self):
        old, _, task = self.stopped_claude()
        new = self.new_claude()
        pm_runtime.write_session_fence(new, 'pm-' + str(uuid.uuid4()), AT, self.state)
        self.assert_refused('PM_SESSION_ADOPTION_TARGET_FENCED', old, new, task)

    def test_a_crash_between_fence_and_move_resumes_for_the_same_new_session(self):
        old, _, first = self.stopped_claude()
        second = self.record_task(old)
        new = self.new_claude()
        crash = patch.object(self.store, 'adopt_pm_session', side_effect=RuntimeError('crash'))
        with crash, self.assertRaises(RuntimeError):
            self.adopt(old, new)
        fence = self.fence(old)
        self.assertEqual(fence['new_session_key'], 'pm-' + new)
        self.assertEqual(self.store.get(first['task_id'])['pm_session_id'], 'pm-' + old)
        self.assert_refused('PM_SESSION_ADOPTION_FENCED_OTHER_TARGET', old, self.new_claude(),
                            first)

        result = self.adopt(old, new)
        self.assertEqual(set(result['moved']), {first['task_id'], second['task_id']})
        self.assertEqual(result['skipped'], [])
        self.assertEqual(self.fence(old), fence)
        self.assert_moved(first, old, new)
        self.assert_moved(second, old, new)

    def test_a_crash_part_way_through_the_move_resumes_with_the_rest(self):
        old, _, first = self.stopped_claude()
        second = self.record_task(old)
        new = self.new_claude()
        real_adopt = self.store.adopt_pm_session
        done = []

        def crash_on_second(task_id, **kwargs):
            if done:
                raise RuntimeError('crash')
            done.append(task_id)
            return real_adopt(task_id, **kwargs)

        crash = patch.object(self.store, 'adopt_pm_session', side_effect=crash_on_second)
        with crash, self.assertRaises(RuntimeError):
            self.adopt(old, new)
        result = self.adopt(old, new)
        self.assertEqual(len(result['moved']), 1)
        self.assertEqual(set(done + result['moved']), {first['task_id'], second['task_id']})
        self.assert_moved(first, old, new)
        self.assert_moved(second, old, new)

    def test_concurrent_adoptions_leave_one_owner_and_one_audit_entry_per_task(self):
        old, _, first = self.stopped_claude()
        second = self.record_task(old)
        targets = (self.new_claude(), self.new_claude())
        barrier = threading.Barrier(2)
        outcomes = {}

        def run(new):
            barrier.wait(timeout=10)
            try:
                outcomes[new] = self.adopt(old, new)
            except LLMToolError as exc:
                outcomes[new] = exc.task_store_error_code

        threads = [threading.Thread(target=run, args=(new,), daemon=True) for new in targets]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(set(outcomes), set(targets))
        (winner,) = [new for new, outcome in outcomes.items() if type(outcome) is dict]
        (loser,) = set(targets) - {winner}
        self.assertIn(outcomes[loser], ('PM_SESSION_ADOPTION_OLD_PM_ACTIVE',
                                        'PM_SESSION_ADOPTION_FENCED_OTHER_TARGET'))
        self.assert_moved(first, old, winner)
        self.assert_moved(second, old, winner)

        # The fence is checked before the empty-candidate return, so the loser stays refused.
        self.assert_refused('PM_SESSION_ADOPTION_FENCED_OTHER_TARGET', old, loser, first)
        self.assert_refused('PM_SESSION_ADOPTION_FENCED_OTHER_TARGET', old, loser, second)
        self.assert_moved(first, old, winner)
        self.assert_moved(second, old, winner)

        # The winner's own retry finds its fence and nothing left to move.
        settled = [self.store.get(task['task_id']) for task in (first, second)]
        self.assertEqual(self.adopt(old, winner), {'moved': [], 'skipped': []})
        self.assertEqual([self.store.get(task['task_id']) for task in (first, second)], settled)
        self.assert_moved(first, old, winner)
        self.assert_moved(second, old, winner)

    def test_a_restarted_old_session_is_refused_by_pm_runtime_open(self):
        old, _, _task = self.stopped_claude(self.bind())
        self.adopt(old, self.new_claude())
        runtime = pm_runtime.PMRuntime(old, cwd=str(self.cwd), account='claude-one',
                                       runner=self.fail, state_root=self.state,
                                       environ=self.account_env)
        with self.assertRaises(LLMToolError) as caught:
            runtime.open()
        self.assertEqual(caught.exception.pm_runtime_code, 'PM_SESSION_TRANSFERRED')
        self.assertIsNone(runtime._child)

    def test_a_restarted_old_codex_session_is_refused_by_pm_runtime_open(self):
        old = self.bind('codex', 'codex-pm')
        self.codex_transcript(old)
        self.record_task(old, 'codex')
        new = str(uuid.uuid4())
        self.codex_transcript(new)
        self.adopt(old, new)
        runtime = pm_runtime.PMRuntime(old, cwd=str(self.cwd), state_root=self.state,
                                       environ=self.account_env)
        with patch('llmtool.pm_runtime_codex.subprocess.Popen') as popen, \
                self.assertRaises(LLMToolError) as caught:
            runtime.open()
        self.assertEqual(caught.exception.pm_runtime_code, 'PM_SESSION_TRANSFERRED')
        popen.assert_not_called()


class CrossRuntimeTests(AdoptionCase):
    def bound_codex(self):
        new = self.bind('codex', 'codex-pm')
        thread = str(uuid.uuid4())
        self.save_thread(new, thread)
        self.codex_transcript(thread)
        return new

    def bound_second_claude(self):
        new = self.bind(account='claude-two')
        self.claude_transcript(new, self.second)
        return new

    def test_claude_to_claude_across_two_accounts(self):
        old, _, task = self.stopped_claude(self.bind())
        new = self.bound_second_claude()
        result = self.adopt(old, new, environ=self.account_env)
        self.assertEqual(result, {'moved': [task['task_id']], 'skipped': []})
        self.assert_moved(task, old, new, new_account='claude-two')

    def test_a_bound_new_session_is_observed_only_in_its_own_account(self):
        old, _, task = self.stopped_claude(self.bind())
        new = self.bind(account='claude-two')
        self.claude_transcript(new, self.first)
        self.assert_refused('PM_SESSION_ADOPTION_NEW_SESSION_INVALID', old, new, task,
                            environ=self.account_env)

    def test_claude_to_codex(self):
        old, _, task = self.stopped_claude(self.bind())
        new = self.bound_codex()
        result = self.adopt(old, new, environ=self.account_env)
        self.assertEqual(result, {'moved': [task['task_id']], 'skipped': []})
        self.assert_moved(task, old, new, new_runtime='codex', new_account='codex-pm')
        self.assertEqual(active_pm_inbox.recovery(self.store.get(task['task_id']))['runtime'],
                         'codex')

    def test_a_codex_binding_with_no_thread_is_not_a_valid_new_session(self):
        old, _, task = self.stopped_claude(self.bind())
        self.assert_refused('PM_SESSION_ADOPTION_NEW_SESSION_INVALID', old,
                            self.bind('codex', 'codex-pm'), task, environ=self.account_env)

    def test_codex_to_claude(self):
        old = self.bind('codex', 'codex-pm')
        transcript = self.codex_transcript(old)
        content = transcript.read_bytes()
        task = self.record_task(old, 'codex')
        new = self.bound_second_claude()
        result = self.adopt(old, new, environ=self.account_env)
        self.assertEqual(result, {'moved': [task['task_id']], 'skipped': []})
        self.assert_moved(task, old, new, old_runtime='codex', new_account='claude-two',
                          liveness=CODEX_STOPPED)
        self.assertEqual(transcript.read_bytes(), content)

    def test_an_unbound_new_session_must_share_the_task_runtime(self):
        old, _, task = self.stopped_claude()
        new = str(uuid.uuid4())
        self.codex_transcript(new)
        self.assert_refused('PM_SESSION_ADOPTION_NEW_SESSION_INVALID', old, new, task)

    def test_a_new_binding_in_another_directory_refuses(self):
        old, _, task = self.stopped_claude()
        other = self.home / 'other-repo'
        other.mkdir()
        new = self.bind(account='claude-two', cwd=other)
        self.assert_refused('PM_SESSION_ADOPTION_CWD_MISMATCH', old, new, task)


class HistoryTests(AdoptionCase):
    def store_adoptions(self, task, entries):
        current = self.store.get(task['task_id'])
        current['pm_session_adoptions'] = entries
        current['record_sha256'] = task_store_module._digest(current)
        self.store._write(current, create=False)

    def test_a_second_adoption_appends_history_and_never_returns_to_a_fenced_session(self):
        first, _, task = self.stopped_claude()
        second = self.new_claude()
        self.adopt(first, second)
        self.assert_refused('PM_SESSION_ADOPTION_TARGET_FENCED', second, first, task)
        third = self.new_claude()
        self.adopt(second, third)
        record = self.store.get(task['task_id'])
        self.assertEqual(record['pm_session_id'], 'pm-' + third)
        self.assertEqual([item['new_session_key'] for item in record['pm_session_adoptions']],
                         ['pm-' + second, 'pm-' + third])

    def test_a_task_at_the_history_limit_refuses_before_any_fence(self):
        old, _, task = self.stopped_claude()
        self.store_adoptions(task, [{'old_session_key': 'pm-' + str(uuid.uuid4()),
                                     'new_session_key': 'pm-' + old, 'at': AT}] * 32)
        self.assert_refused('PM_SESSION_ADOPTION_LIMIT', old, self.new_claude(), task)
        self.assertIsNone(self.fence(old))

    def history_entries(self):
        """(Task, legacy entry, Claude-to-Codex entry, Codex-to-Claude entry)."""
        old, _, task = self.stopped_claude()
        legacy = {'old_session_key': 'pm-' + str(uuid.uuid4()),
                  'new_session_key': 'pm-' + old, 'at': AT}
        from_claude = {**legacy, 'old_runtime': 'claude-code', 'new_runtime': 'codex',
                       'new_account': None, 'liveness': CLAUDE_STOPPED}
        from_codex = {**legacy, 'old_runtime': 'codex', 'new_runtime': 'claude-code',
                      'new_account': None, 'liveness': CODEX_STOPPED}
        return task, legacy, from_claude, from_codex

    def assert_entry_accepted(self, task, entry):
        with self.subTest(entry=entry):
            self.store_adoptions(task, [entry])
            self.assertEqual(self.store.get(task['task_id'])['pm_session_adoptions'], [entry])

    def assert_entry_rejected(self, task, entry):
        before = self.store.get(task['task_id'])
        with self.subTest(entry=entry):
            with self.assertRaises(LLMToolError) as caught:
                self.store_adoptions(task, [entry])
            self.assertEqual(caught.exception.task_store_error_code,
                             'PM_SESSION_ADOPTION_INVALID')
            self.assertEqual(self.store.get(task['task_id']), before)

    def test_the_validator_accepts_legacy_entries_and_each_runtime_stopped_set(self):
        task, legacy, from_claude, from_codex = self.history_entries()
        for entry in (legacy,
                      from_claude,
                      {**from_claude, 'liveness': ['PROCESS_CLEAN', 'LOCK_FREE',
                                                   'REGISTRY_CLEAN']},
                      {**from_claude, 'liveness': ['TRANSCRIPT_QUIET', 'REGISTRY_CLEAN',
                                                   'LOCK_FREE', 'PROCESS_CLEAN']},
                      {**from_claude, 'new_account': 'codex-pm'},
                      from_codex,
                      {**from_codex, 'liveness': ['LOCK_FREE', 'PROCESS_CLEAN']},
                      {**from_codex, 'new_account': 'claude-two'}):
            self.assert_entry_accepted(task, entry)

    def test_the_validator_checks_liveness_against_the_old_runtime(self):
        task, _, from_claude, from_codex = self.history_entries()
        for entry in (
                # A Claude session is STOPPED only with a clean registry.
                {**from_claude, 'liveness': CODEX_STOPPED},
                {**from_claude, 'liveness': ['PROCESS_CLEAN', 'REGISTRY_CLEAN',
                                             'TRANSCRIPT_QUIET']},
                {**from_claude, 'liveness': ['LOCK_FREE', 'REGISTRY_CLEAN',
                                             'TRANSCRIPT_QUIET']},
                # Codex evidence is exactly a clean process list and a free lock.
                {**from_codex, 'liveness': CODEX_STOPPED + ['REGISTRY_CLEAN']},
                {**from_codex, 'liveness': CODEX_STOPPED + ['TRANSCRIPT_QUIET']},
                {**from_codex, 'liveness': CLAUDE_STOPPED}):
            self.assert_entry_rejected(task, entry)

    def test_the_validator_rejects_every_other_malformed_entry(self):
        task, legacy, from_claude, _ = self.history_entries()
        for entry in ({**legacy, 'old_runtime': 'claude-code'},
                      {**from_claude, 'extra': True},
                      {**from_claude, 'new_runtime': 'other-runtime'},
                      {**from_claude, 'old_runtime': None},
                      {**from_claude, 'old_runtime': 'other-runtime'},
                      {**from_claude, 'old_runtime': ['codex']},
                      {**from_claude, 'new_account': '/private/account'},
                      {**from_claude, 'liveness': 'PROCESS_CLEAN'},
                      {**from_claude, 'liveness': ['PROCESS_CLEAN']},
                      {**from_claude, 'liveness': [['PROCESS_CLEAN']]},
                      {**from_claude, 'liveness': CLAUDE_STOPPED + ['LOCK_BUSY']},
                      {**from_claude, 'liveness': CLAUDE_STOPPED + ['LOCK_FREE']}):
            self.assert_entry_rejected(task, entry)


class RemoteTestReceiptTests(AdoptionCase):
    """#1204: a receipt stays bound to the PM session that ran the test."""

    def append(self, task, native, executor='d6-task'):
        receipt = store_fixture.remote_test_receipt(task, 'pm-' + native,
                                                    executor_task=executor)
        self.assertEqual(self.store.append_remote_test_receipt(task['task_id'], receipt),
                         receipt)
        return receipt

    def assert_append_refused(self, task, receipt):
        before = self.store.get(task['task_id'])
        with self.assertRaises(LLMToolError) as caught:
            self.store.append_remote_test_receipt(task['task_id'], receipt)
        self.assertEqual(caught.exception.task_store_error_code, 'REMOTE_TEST_RECEIPT_INVALID')
        self.assertEqual(self.store.get(task['task_id']), before)

    def assert_receipts_invalid(self, record):
        with self.assertRaises(LLMToolError) as caught:
            task_store_module._validate_remote_test_receipts(record)
        self.assertEqual(caught.exception.task_store_error_code, 'REMOTE_TEST_RECEIPT_INVALID')

    def two_adoptions(self):
        """A Task adopted from A to B, then B to C, with one receipt under each session."""
        first, _, task = self.stopped_claude()
        under_first = self.append(task, first, 'd6-first')
        second = self.new_claude()
        self.assertEqual(self.adopt(first, second)['moved'], [task['task_id']])
        under_second = self.append(task, second, 'd6-second')
        third = self.new_claude()
        self.assertEqual(self.adopt(second, third)['moved'], [task['task_id']])
        under_third = self.append(task, third, 'd6-third')
        return task, (first, second, third), [under_first, under_second, under_third]

    def test_a_stopped_pm_task_with_a_receipt_is_adopted_and_keeps_it(self):
        old, _, task = self.stopped_claude()
        receipt = self.append(task, old)
        stored = self.store.get(task['task_id'])['remote_test_receipts']
        new = self.new_claude()
        self.assertEqual(self.adopt(old, new), {'moved': [task['task_id']], 'skipped': []})
        self.assert_moved(task, old, new)
        record = self.store.get(task['task_id'])
        self.assertEqual(record['pm_session_id'], 'pm-' + new)
        self.assertEqual(record['remote_test_receipts'], stored)
        self.assertEqual(record['remote_test_receipts'], [receipt])
        self.assertEqual(json.dumps(record['remote_test_receipts'], sort_keys=True),
                         json.dumps(stored, sort_keys=True))

        # The identical stored receipt is still returned, unchanged.
        self.assertEqual(self.store.append_remote_test_receipt(task['task_id'], receipt),
                         receipt)
        # A new receipt binds only to the current session.
        self.assert_append_refused(task, store_fixture.remote_test_receipt(
            task, 'pm-' + old, executor_task='d6-later'))
        self.append(task, new, 'd6-later')

    def test_receipts_of_every_earlier_session_in_the_chain_stay_valid(self):
        task, sessions, receipts = self.two_adoptions()
        record = self.store.get(task['task_id'])
        self.assertEqual(record['pm_session_id'], 'pm-' + sessions[2])
        self.assertEqual(record['remote_test_receipts'], receipts)
        self.assertEqual([item['pm_session'] for item in record['remote_test_receipts']],
                         ['pm-' + native for native in sessions])
        task_store_module._validate_remote_test_receipts(record)
        task_store_module._validate(record)

    def test_a_receipt_of_an_unrelated_session_is_refused(self):
        _, _, fresh = self.stopped_claude()
        self.assert_append_refused(fresh, store_fixture.remote_test_receipt(
            fresh, 'pm-' + str(uuid.uuid4())))

        task, _, receipts = self.two_adoptions()
        unrelated = store_fixture.remote_test_receipt(
            task, 'pm-' + str(uuid.uuid4()), executor_task='d6-other')
        self.assert_append_refused(task, unrelated)
        record = self.store.get(task['task_id'])
        self.assertEqual(record['remote_test_receipts'], receipts)
        self.assert_receipts_invalid(dict(record, remote_test_receipts=receipts + [unrelated]))

    def test_a_tampered_chain_refuses_earlier_session_receipts(self):
        task, _, receipts = self.two_adoptions()
        record = self.store.get(task['task_id'])
        first, second = record['pm_session_adoptions']
        stranger = 'pm-' + str(uuid.uuid4())
        tampered = {
            'broken link': [first, {**second, 'old_session_key': stranger}],
            'last entry names another session': [first, {**second, 'new_session_key': stranger}],
            'empty list': [],
        }
        earlier, current = receipts[:2], receipts[2]
        for label, adoptions in tampered.items():
            with self.subTest(label):
                for receipt in earlier:
                    self.assert_receipts_invalid(dict(
                        record, pm_session_adoptions=adoptions, remote_test_receipts=[receipt]))
                # Receipts that all name the current session stay valid, as before.
                task_store_module._validate_remote_test_receipts(dict(
                    record, pm_session_adoptions=adoptions, remote_test_receipts=[current]))
        without = {key: value for key, value in record.items()
                   if key != 'pm_session_adoptions'}
        for receipt in earlier:
            self.assert_receipts_invalid(dict(without, remote_test_receipts=[receipt]))
        task_store_module._validate_remote_test_receipts(
            dict(without, remote_test_receipts=[current]))


class MidFlightWorkerTests(AdoptionCase):
    def gate_open(self):
        # Dispatch policy is covered elsewhere; these Tasks only need a Developer block.
        return patch.object(worker_dispatch, 'authorize_task_store_change', return_value=None)

    def claude_pending(self, task):
        developer = store_fixture.TaskStoreTests._developer_assignment(provider='claude')
        developer.update(owner_assignment_ref=OWNER_REF, executor_task_id='executor-1')
        with self.gate_open():
            return self.store.compare_and_update(
                task['task_id'], expected={'developer': None}, developer=developer)['developer']

    def codex_dispatching(self, task):
        pending = store_fixture.TaskStoreTests._developer_assignment(provider='codex')
        dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                       'provider_type': 'codex-cli', 'model': 'gpt-6-luna'}
        with self.gate_open():
            self.store.compare_and_update(
                task['task_id'], expected={'developer': None}, developer=pending)
            return self.store.update(task['task_id'], developer=dispatching)['developer']

    def assert_delegate_is_duplicate(self, task):
        before = self.store.get(task['task_id'])
        args = Namespace(task_id=task['task_id'], provider='codex', mode='proposal',
                         background=False, acceptance=[ACCEPTANCE], context_stdin=True,
                         selection_source='PM')
        with self.assertRaisesRegex(LLMToolError, 'duplicate dispatch refused'):
            run_task_delegate(args, store=self.store)
        self.assertEqual(self.store.get(task['task_id']), before)

    def assert_result_reaches_only_the_new_pm(self, task, old, new):
        record = self.store.get(task['task_id'])
        event_id = uuid.uuid4().hex
        self.store.record_completion_event(
            task['task_id'], event_id=event_id, run_id=record['run_id'],
            session_id=record['developer']['session_id'], source='DIRECT_D6_DEVELOPER',
            source_sha='a' * 40, sequence=0, payload_sha256='b' * 64)
        (event,) = active_pm_inbox.read_inbox(self.store, 'pm-' + new)['events']
        self.assertEqual(event['event_id'], event_id)
        self.assertEqual(event['pm_session_id'], 'pm-' + new)

        self.assertEqual(active_pm_inbox.read_inbox(self.store, 'pm-' + old)['events'], [])
        with self.assertRaises(LLMToolError):
            active_pm_inbox.ack(self.store, event_id, 'pm-' + old)
        with self.assertRaises(LLMToolError) as abandon:
            self.store.abandon(task['task_id'], pm_session_id='pm-' + old, reason='Old PM')
        self.assertEqual(abandon.exception.task_store_error_code, 'TASK_PM_SESSION_DENIED')
        after = self.store.get(task['task_id'])
        self.assertEqual(after['completion_events'][0]['state'], 'PENDING')
        self.assertNotIn('abandonment', after)

        result = active_pm_inbox.ack(self.store, event_id, 'pm-' + new)
        self.assertEqual(result['event']['state'], 'ACKED')

    def test_a_pending_developer_moves_unchanged_and_reports_to_the_new_pm(self):
        old, _, task = self.stopped_claude()
        developer = self.claude_pending(task)
        new = self.new_claude()
        self.assertEqual(self.adopt(old, new)['moved'], [task['task_id']])
        self.assertEqual(self.store.get(task['task_id'])['developer'], developer)
        self.assertEqual(developer['state'], 'DEVELOPER_PENDING')
        self.assert_delegate_is_duplicate(task)

        result = {'executor_task_id': 'executor-1', 'head_sha': 'a' * 40}
        with self.assertRaises(LLMToolError) as caught:
            self.store.record_claude_result(task['task_id'], pm_session_id='pm-' + old, **result)
        self.assertEqual(caught.exception.task_store_error_code,
                         'CLAUDE_RESULT_BINDING_MISMATCH')
        with self.gate_open():
            saved = self.store.record_claude_result(task['task_id'], pm_session_id='pm-' + new,
                                                    **result)
        self.assertEqual(saved['developer']['state'], 'DEVELOPER_RESPONDED')
        self.assert_result_reaches_only_the_new_pm(task, old, new)

    def test_a_dispatching_developer_moves_unchanged_and_reports_to_the_new_pm(self):
        old, _, task = self.stopped_claude()
        developer = self.codex_dispatching(task)
        new = self.new_claude()
        self.assertEqual(self.adopt(old, new)['moved'], [task['task_id']])
        self.assertEqual(self.store.get(task['task_id'])['developer'], developer)
        self.assertEqual(developer['state'], 'DEVELOPER_DISPATCHING')
        self.assert_delegate_is_duplicate(task)

        responded = {**developer, 'state': 'DEVELOPER_RESPONDED', 'artifact': ARTIFACT,
                     'artifact_sha256': hashlib.sha256(ARTIFACT.encode()).hexdigest()}
        with self.gate_open():
            self.store.update(task['task_id'], developer=responded)
        self.assert_result_reaches_only_the_new_pm(task, old, new)


if __name__ == '__main__':
    unittest.main()
