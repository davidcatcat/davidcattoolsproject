"""#2415: Codex Debug observed model and effort from actual CODEX_HOME.

Rule: the observed model and effort of a Codex Debug run are read from the
sessions directory of the same Codex home the run was launched with (the
CODEX_HOME passed to it, else the default ~/.codex), never from a different
account's directory.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from llmtool import high_debug


_STUB_RULES = 'RULES stub\nRULES_END\n\n'
_STUB_RULES_RESULT = (_STUB_RULES, hashlib.sha256(_STUB_RULES.encode('utf-8')).hexdigest())


class _Store:
    def __init__(self):
        self.entries = []
        self.events = []
        self.served = set()

    def task_ids(self):
        return sorted(self.served)

    def finalize_high_debug(self, _task_id, index, entry):
        self.entries[index] = entry

    def get(self, task_id):
        self.served.add(task_id)
        return {'task_id': task_id, 'run_id': 'b' * 32,
                'review_repair_claims': [
                    {'family': 'family', 'pr': 17},
                    {'family': 'family', 'pr': 17}],
                'completion': {'executor_task_id': 'executor',
                               'evidence': {'common_dir': '/tmp/ignored'}},
                'high_debug': list(self.entries)}

    @staticmethod
    def _completion_now():
        return '2026-11-20T12:00:00Z'

    def append_high_debug(self, _task_id, entry):
        self.entries.append(entry)

    def record_completion_event(self, _task_id, **event):
        if not any(item['event_id'] == event['event_id'] for item in self.events):
            self.events.append(event)
            return {'created': True}
        return {'created': False}


class HighDebugCodexHome2415Tests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self._verified_ai_env_patch = patch.object(
            high_debug.ai_github_exec, 'verified_ai_env',
            side_effect=lambda *args, **kwargs: {
                'GH_CONFIG_DIR': '/safe/gh-config',
                'PATH': '/usr/bin',
            })
        self._verified_ai_env_patch.start()
        self.addCleanup(self._verified_ai_env_patch.stop)

        self._poster_patch = patch.object(high_debug, '_post_github_record', return_value=True)
        self._poster_patch.start()
        self.addCleanup(self._poster_patch.stop)

        self._store_root_patch = patch.object(
            high_debug.task_store, 'default_root',
            return_value=Path(self.temp_dir.name) / 'tasks')
        self._store_root_patch.start()
        self.addCleanup(self._store_root_patch.stop)

        self._debug_rules_patch = patch.object(
            high_debug, '_debug_rules', return_value=_STUB_RULES_RESULT)
        self._debug_rules_patch.start()
        self.addCleanup(self._debug_rules_patch.stop)

        self._evaluate_patch = patch.object(
            high_debug.worker_dispatch, 'evaluate',
            return_value=SimpleNamespace(allowed=True))
        self._evaluate_patch.start()
        self.addCleanup(self._evaluate_patch.stop)

        self._snapshot_patch = patch.object(
            high_debug.task_completion, 'collect_git_snapshot',
            return_value=SimpleNamespace(head='a' * 40))
        self._snapshot_patch.start()
        self.addCleanup(self._snapshot_patch.stop)

    def _repo(self):
        repo_dir = Path(self.temp_dir.name) / 'repo'
        repo_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git', 'init', '-q', str(repo_dir)], check=True)
        subprocess.run(['git', '-C', str(repo_dir), '-c', 'user.name=t', '-c', 'user.email=t@e',
                        'commit', '-q', '--allow-empty', '-m', 'base'], check=True)
        return repo_dir

    @staticmethod
    def _clean_report():
        parts = ['## ' + section + '\ntext\n' for section in high_debug.REPORT_SECTIONS[:-1]]
        parts.append('## Outcome/cost\nRCA_CONFIRMED\n')
        return '\n'.join(parts)

    @staticmethod
    def _create_session_file(sessions_dir, thread_id, model='gpt-6.1-sol', effort='high'):
        rollout_dir = Path(sessions_dir) / '2026' / '11' / '20'
        rollout_dir.mkdir(parents=True, exist_ok=True)
        rollout_file = rollout_dir / f'rollout-2026-11-20T12-00-00-{thread_id}.jsonl'
        record = {'type': 'session_meta',
                  'payload': {'model': model,
                              'reasoning_effort': effort}}
        rollout_file.write_text(json.dumps(record, ensure_ascii=False) + '\n', encoding='utf-8')
        return rollout_file

    def _run_debug(self, store, repo_root, thread_id, sessions_dir=None, codex_home=None):
        registration = SimpleNamespace(result_sha='a' * 40, root=str(repo_root),
                                       common_dir=str(repo_root / '.git'))
        def runner(*, prompt, cwd, output_path):
            Path(output_path).write_text(self._clean_report(), encoding='utf-8')
            events = [
                json.dumps({'type': 'thread.started', 'thread_id': thread_id}),
                json.dumps({'type': 'turn.completed',
                            'usage': {'input_tokens': 10, 'cached_input_tokens': 0,
                                      'output_tokens': 5, 'reasoning_output_tokens': 2}}),
            ]
            raw_events = ('\n'.join(events) + '\n').encode('utf-8')
            return subprocess.CompletedProcess([], 0, stdout=raw_events, stderr=b'')

        kwargs = {}
        if sessions_dir is not None:
            kwargs['sessions_dir'] = sessions_dir
        if codex_home is not None:
            kwargs['codex_home'] = codex_home

        high_debug.run_high_debug(
            store=store, task_id='a' * 32, executor_task='executor',
            pr_number=17, family='family', failed_corrections=2,
            runner=runner, create_d6=lambda **_kwargs: str(repo_root),
            repo_root=str(repo_root),
            read_registration=lambda *_args, **_kwargs: registration,
            **kwargs)

    def test_a_run_launched_with_codex_home_observes_recorded_model(self):
        """(a) run launched with CODEX_HOME=X, session file only under X/sessions -> observed = recorded model."""
        thread_id = 'thread-case-a-12345'
        home_x = Path(self.temp_dir.name) / 'home_x'
        self._create_session_file(home_x / 'sessions', thread_id, model='gpt-6.1-sol', effort='high')
        fake_default = Path(self.temp_dir.name) / 'fake_default'
        (fake_default / '.codex' / 'sessions').mkdir(parents=True, exist_ok=True)

        with patch.dict(os.environ, {'CODEX_HOME': str(home_x)}), \
             patch.object(high_debug.Path, 'home', return_value=fake_default):
            # Test direct _session_observed
            observed, effort = high_debug._session_observed(thread_id)
            self.assertEqual(observed, 'gpt-6.1-sol')
            self.assertEqual(effort, 'high')

            # Test run_high_debug
            store = _Store()
            self._run_debug(store, self._repo(), thread_id)
            entry = store.entries[0]
            self.assertEqual(entry['model_observed'], 'gpt-6.1-sol')
            self.assertEqual(entry['effort_observed'], 'high')

        # Also test explicit codex_home parameter without CODEX_HOME env var
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(high_debug.Path, 'home', return_value=fake_default):
            observed, effort = high_debug._session_observed(thread_id, codex_home=home_x)
            self.assertEqual(observed, 'gpt-6.1-sol')
            self.assertEqual(effort, 'high')

            store = _Store()
            self._run_debug(store, self._repo(), thread_id, codex_home=home_x)
            entry = store.entries[0]
            self.assertEqual(entry['model_observed'], 'gpt-6.1-sol')
            self.assertEqual(entry['effort_observed'], 'high')

    def test_b_no_codex_home_uses_default_sessions(self):
        """(b) no CODEX_HOME -> default ~/.codex/sessions as on main."""
        thread_id = 'thread-case-b-12345'
        fake_default = Path(self.temp_dir.name) / 'fake_default'
        self._create_session_file(fake_default / '.codex' / 'sessions', thread_id,
                                  model='gpt-6.1-sol', effort='high')

        env = dict(os.environ)
        env.pop('CODEX_HOME', None)
        with patch.dict(os.environ, env, clear=True), \
             patch.object(high_debug.Path, 'home', return_value=fake_default):
            # Test direct _session_observed
            observed, effort = high_debug._session_observed(thread_id)
            self.assertEqual(observed, 'gpt-6.1-sol')
            self.assertEqual(effort, 'high')

            # Test run_high_debug
            store = _Store()
            self._run_debug(store, self._repo(), thread_id)
            entry = store.entries[0]
            self.assertEqual(entry['model_observed'], 'gpt-6.1-sol')
            self.assertEqual(entry['effort_observed'], 'high')

    def test_c_session_file_in_another_home_yields_unknown(self):
        """(c) session file present only in another home -> UNKNOWN (no cross-account read)."""
        thread_id = 'thread-case-c-12345'
        home_x = Path(self.temp_dir.name) / 'home_x'
        (home_x / 'sessions').mkdir(parents=True, exist_ok=True)
        home_other = Path(self.temp_dir.name) / 'home_other'
        self._create_session_file(home_other / 'sessions', thread_id,
                                  model='gpt-6.1-sol', effort='high')
        fake_default = Path(self.temp_dir.name) / 'fake_default'
        (fake_default / '.codex' / 'sessions').mkdir(parents=True, exist_ok=True)

        with patch.dict(os.environ, {'CODEX_HOME': str(home_x)}), \
             patch.object(high_debug.Path, 'home', return_value=fake_default):
            # Test direct _session_observed
            observed, effort = high_debug._session_observed(thread_id)
            self.assertEqual(observed, 'UNKNOWN')
            self.assertEqual(effort, 'UNKNOWN')

            # Test run_high_debug
            store = _Store()
            self._run_debug(store, self._repo(), thread_id)
            entry = store.entries[0]
            self.assertEqual(entry['model_observed'], 'UNKNOWN')
            self.assertEqual(entry['effort_observed'], 'UNKNOWN')

    def test_d_explicit_sessions_dir_argument_wins(self):
        """(d) explicit sessions_dir argument still wins (existing tests)."""
        thread_id = 'thread-case-d-12345'
        explicit_dir = Path(self.temp_dir.name) / 'explicit_sessions'
        self._create_session_file(explicit_dir, thread_id, model='gpt-6.1-sol', effort='high')
        home_x = Path(self.temp_dir.name) / 'home_x'
        # Conflicting model in home_x / 'sessions'
        self._create_session_file(home_x / 'sessions', thread_id, model='gpt-conflicting', effort='low')

        with patch.dict(os.environ, {'CODEX_HOME': str(home_x)}):
            # Test direct _session_observed with sessions_dir
            observed, effort = high_debug._session_observed(thread_id, sessions_dir=explicit_dir)
            self.assertEqual(observed, 'gpt-6.1-sol')
            self.assertEqual(effort, 'high')

            # Test run_high_debug with sessions_dir
            store = _Store()
            self._run_debug(store, self._repo(), thread_id, sessions_dir=str(explicit_dir))
            entry = store.entries[0]
            self.assertEqual(entry['model_observed'], 'gpt-6.1-sol')
            self.assertEqual(entry['effort_observed'], 'high')

    def test_e_thread_id_with_no_session_file_yields_unknown(self):
        """(e) a thread id with no session file -> UNKNOWN as on main."""
        thread_id = 'thread-case-e-missing-12345'
        home_x = Path(self.temp_dir.name) / 'home_x'
        (home_x / 'sessions').mkdir(parents=True, exist_ok=True)
        fake_default = Path(self.temp_dir.name) / 'fake_default'
        (fake_default / '.codex' / 'sessions').mkdir(parents=True, exist_ok=True)

        with patch.dict(os.environ, {'CODEX_HOME': str(home_x)}), \
             patch.object(high_debug.Path, 'home', return_value=fake_default):
            # Test direct _session_observed
            observed, effort = high_debug._session_observed(thread_id)
            self.assertEqual(observed, 'UNKNOWN')
            self.assertEqual(effort, 'UNKNOWN')

            # Test run_high_debug
            store = _Store()
            self._run_debug(store, self._repo(), thread_id)
            entry = store.entries[0]
            self.assertEqual(entry['model_observed'], 'UNKNOWN')
            self.assertEqual(entry['effort_observed'], 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
