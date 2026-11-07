"""Contract tests for the fail-closed PM session liveness check and transfer fence."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from llmtool import active_pm_inbox, pm_runtime
from llmtool import pm_session_liveness as liveness
from llmtool.errors import LLMToolError


SESSION = '11111111-2222-4333-8444-555555555555'
NEW_KEY = 'pm-66666666-7777-4888-9999-aaaaaaaaaaaa'
THREAD = 'abcdefab-cdef-abcd-efab-cdefabcdefab'
NOW = 1800000000.0
CLEAN_PS = '1 /sbin/launchd\n2 /usr/bin/other --flag value\n'

# Audit hooks see every open() and os.open(), and cannot be removed once added.
_audit = {'recording': False, 'opened': []}


def _audit_hook(event, args):
    if _audit['recording'] and event == 'open':
        _audit['opened'].append(str(args[0]))


sys.addaudithook(_audit_hook)


def _unavailable(error):
    def lister():
        raise error
    return lister


class FakeChild:
    def __init__(self):
        self.stdin = io.BytesIO()
        self.stdout = iter(())
        self.stderr = None

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        pass

    def kill(self):
        pass


class LivenessCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.cwd = self.home / 'repo'
        self.cwd.mkdir()
        self.state = self.home / 'state'
        pm_runtime._ensure_state_dir(self.state)
        self.first = self.home / 'claude-one'
        self.second = self.home / 'claude-two'
        self.codex_dir = self.home / 'codex-account'
        for path in (self.first, self.second, self.codex_dir):
            path.mkdir(mode=0o700)
        self.config = self.home / '.config/llmtool/config.yaml'
        self.config.parent.mkdir(parents=True)
        self.write_config()
        self.environ = {'HOME': str(self.home), 'PATH': '/usr/bin:/bin'}

    def write_config(self, extra=''):
        self.config.write_text(
            'accounts:\n'
            '  claude-one:\n    kind: claude\n    config_dir: ' + str(self.first) + '\n'
            '  claude-two:\n    kind: claude\n    config_dir: ' + str(self.second) + '\n'
            '  codex-pm:\n    kind: codex\n    config_dir: ' + str(self.codex_dir) + '\n' + extra,
            encoding='utf-8')

    def check(self, session=SESSION, runtime='claude-code', ps=CLEAN_PS, **overrides):
        cwd = overrides.pop('cwd', str(self.cwd))
        options = {'state_root': self.state, 'environ': self.environ, 'home': str(self.home),
                   'clock': lambda: NOW, 'process_lister': lambda: ps}
        options.update(overrides)
        return liveness.check_session(session, runtime, cwd, **options)

    def assertResult(self, result, state, *codes):
        self.assertEqual(result['state'], state, result)
        for code in codes:
            self.assertIn(code, result['evidence'])

    def sessions(self, config_dir):
        path = Path(config_dir) / 'sessions'
        path.mkdir(mode=0o700, exist_ok=True)
        return path

    def register(self, config_dir, session=SESSION, pid=4242):
        path = self.sessions(config_dir) / (str(pid) + '.json')
        path.write_text(json.dumps({'sessionId': session, 'pid': pid, 'cwd': str(self.cwd)}),
                        encoding='utf-8')
        return path

    def transcript(self, config_dir, session=SESSION, age=1000):
        path = active_pm_inbox.claude_session_file(
            session, str(self.cwd), {'CLAUDE_CONFIG_DIR': str(config_dir)})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}\n', encoding='utf-8')
        os.utime(path, (NOW - age, NOW - age))
        return path

    def bind_claude(self):
        return pm_runtime.create_dedicated_pm(
            str(self.cwd), account='claude-one', state_root=self.state, environ=self.environ,
            home=str(self.home))['native_session_id']

    def bind_codex(self):
        return pm_runtime.create_dedicated_pm(
            str(self.cwd), runtime='codex', account='codex-pm', state_root=self.state,
            environ=self.environ, home=str(self.home))['native_session_id']

    def update_binding(self, session, **fields):
        binding = pm_runtime.current_binding(session, state_root=self.state,
                                             resolve_legacy_account=False)
        binding.update(fields)
        pm_runtime._write_json_atomic(self.state, pm_runtime._binding_path(self.state, session),
                                      binding)

    def save_thread(self, session, value=THREAD):
        pm_runtime._write_json_atomic(
            self.state, pm_runtime._runtime_state_path(self.state, 'pm-' + session),
            {'codex_thread_id': value})

    def assertLockFree(self, session):
        with pm_runtime.transfer_lock(session, self.state):
            pass


class StoppedTests(LivenessCase):
    def test_unbound_claude_session_with_every_check_clean_is_stopped(self):
        self.sessions(self.first)
        self.transcript(self.first)
        result = self.check()
        self.assertResult(result, 'STOPPED', 'PROCESS_CLEAN', 'REGISTRY_CLEAN', 'TRANSCRIPT_QUIET')

    def test_bound_claude_session_needs_a_free_lock_and_releases_it(self):
        session = self.bind_claude()
        self.sessions(self.first)
        self.transcript(self.first, session)
        result = self.check(session)
        self.assertResult(result, 'STOPPED', 'LOCK_FREE', 'PROCESS_CLEAN', 'REGISTRY_CLEAN')
        self.assertLockFree(session)

    def test_a_valid_binding_is_enough_without_a_transcript(self):
        session = self.bind_claude()
        self.sessions(self.first)
        result = self.check(session)
        self.assertResult(result, 'STOPPED', 'LOCK_FREE', 'PROCESS_CLEAN', 'REGISTRY_CLEAN')
        self.assertNotIn('NO_CLAUDE_EVIDENCE', result['evidence'])

    def test_evidence_is_fixed_codes_only(self):
        self.sessions(self.first)
        self.transcript(self.first)
        result = self.check()
        self.assertEqual(set(result), {'state', 'evidence'})
        for code in result['evidence']:
            self.assertRegex(code, r'\A[A-Z_]+\Z')


class LockHeldTests(LivenessCase):
    def test_a_bound_session_reads_as_active_under_the_callers_lock_without_the_flag(self):
        session = self.bind_claude()
        self.sessions(self.first)
        self.transcript(self.first, session)
        with pm_runtime.transfer_lock(session, self.state):
            self.assertResult(self.check(session), 'ACTIVE', 'LOCK_BUSY')
            result = self.check(session, lock_held=True)
        self.assertResult(result, 'STOPPED', 'LOCK_FREE', 'PROCESS_CLEAN', 'REGISTRY_CLEAN')
        self.assertNotIn('LOCK_BUSY', result['evidence'])

    def test_an_unbound_claude_session_is_stopped_and_records_the_held_lock(self):
        self.sessions(self.first)
        self.transcript(self.first)
        with pm_runtime.transfer_lock(SESSION, self.state):
            result = self.check(lock_held=True)
        self.assertResult(result, 'STOPPED', 'LOCK_FREE', 'PROCESS_CLEAN', 'REGISTRY_CLEAN')

    def test_a_bound_codex_session_is_stopped_under_the_callers_lock(self):
        session = self.bind_codex()
        with pm_runtime.transfer_lock(session, self.state):
            result = self.check(session, 'codex', lock_held=True)
        self.assertEqual(result, {'state': 'STOPPED', 'evidence': ['PROCESS_CLEAN', 'LOCK_FREE']})

    def test_the_probe_is_skipped(self):
        session = self.bind_claude()
        self.sessions(self.first)
        with patch.object(liveness, '_lock_evidence') as probe:
            self.assertResult(self.check(session, lock_held=True), 'STOPPED', 'LOCK_FREE')
        probe.assert_not_called()

    def test_it_never_turns_other_evidence_into_stopped(self):
        session = self.bind_claude()
        self.sessions(self.first)
        self.assertResult(self.check(session, ps='77 claude --resume ' + session + '\n',
                                     lock_held=True), 'ACTIVE', 'PROCESS_MATCH')
        self.assertResult(self.check(session, process_lister=_unavailable(OSError()),
                                     lock_held=True), 'UNKNOWN', 'PROCESS_LIST_FAILED')
        self.assertResult(self.check(runtime='codex', lock_held=True), 'UNKNOWN', 'CODEX_UNBOUND')
        pm_runtime._binding_path(self.state, SESSION).write_text('{', encoding='utf-8')
        self.assertResult(self.check(lock_held=True), 'UNKNOWN', 'BINDING_INVALID')


class ProcessCheckTests(LivenessCase):
    def setUp(self):
        super().setUp()
        self.sessions(self.first)
        self.transcript(self.first)

    def test_the_id_as_an_argv_item_of_any_executable_is_active(self):
        for line in ('77 claude --resume ' + SESSION,
                     '77 node /opt/claude/cli.js --resume ' + SESSION,
                     '77 /usr/bin/python3 wrapper.py ' + SESSION,
                     '77 sh -c claude --session-id ' + SESSION,
                     '77 claude --resume=' + SESSION,
                     '77 node "' + SESSION + '"'):
            with self.subTest(line=line):
                self.assertResult(self.check(ps=CLEAN_PS + line + '\n'), 'ACTIVE', 'PROCESS_MATCH')

    def test_an_unparsable_line_containing_the_id_is_active(self):
        for line in ('garbage ' + SESSION, SESSION, "77 sh -c 'claude" + SESSION):
            with self.subTest(line=line):
                self.assertResult(self.check(ps=CLEAN_PS + line + '\n'), 'ACTIVE', 'PROCESS_MATCH')

    def test_a_stray_quote_cannot_merge_the_id_into_another_item(self):
        line = "77 node it's --resume " + SESSION + " 'x\n"
        self.assertResult(self.check(ps=line), 'ACTIVE', 'PROCESS_MATCH')

    def test_bytes_listing_is_accepted(self):
        result = self.check(ps=('77 node x ' + SESSION + '\n').encode('utf-8'))
        self.assertResult(result, 'ACTIVE', 'PROCESS_MATCH')

    def test_default_lister_asks_ps_for_uncut_lines_with_a_timeout(self):
        with patch.object(liveness.subprocess, 'check_output',
                          return_value=CLEAN_PS.encode('utf-8')) as ps:
            result = self.check(process_lister=None)
        self.assertResult(result, 'STOPPED', 'PROCESS_CLEAN')
        self.assertEqual(ps.call_args.args[0], ('ps', '-axo', 'pid=,command=', '-ww'))
        self.assertEqual(ps.call_args.kwargs['timeout'], 2)

    def test_ps_failure_and_timeout_are_unknown_never_no_process(self):
        for error in (subprocess.CalledProcessError(1, 'ps'),
                      subprocess.TimeoutExpired('ps', 2), FileNotFoundError('ps')):
            with self.subTest(error=type(error).__name__):
                with patch.object(liveness.subprocess, 'check_output', side_effect=error):
                    result = self.check(process_lister=None)
                self.assertResult(result, 'UNKNOWN', 'PROCESS_LIST_FAILED')

    def test_injected_lister_failure_is_unknown(self):
        result = self.check(process_lister=_unavailable(RuntimeError('boom')))
        self.assertResult(result, 'UNKNOWN', 'PROCESS_LIST_FAILED')

    def test_empty_or_unusable_listing_is_unknown(self):
        for listing in ('', ' \n', b'', None, 'PID COMMAND\n'):
            with self.subTest(listing=listing):
                self.assertResult(self.check(ps=listing), 'UNKNOWN', 'PROCESS_LIST_FAILED')


class BindingCheckTests(LivenessCase):
    def test_a_held_lock_is_active(self):
        session = self.bind_claude()
        self.sessions(self.first)
        with pm_runtime.transfer_lock(session, self.state):
            self.assertResult(self.check(session), 'ACTIVE', 'LOCK_BUSY')

    def test_a_free_lock_alone_is_never_stopped(self):
        session = self.bind_claude()
        self.assertResult(self.check(session), 'UNKNOWN', 'LOCK_FREE', 'NO_CLAUDE_EVIDENCE')

    def test_an_unusable_lock_file_is_unknown_not_busy(self):
        session = self.bind_claude()
        self.sessions(self.first)
        (self.state / ('pm-' + session + '.lock')).symlink_to(self.home / 'elsewhere')
        self.assertResult(self.check(session), 'UNKNOWN', 'LOCK_CHECK_FAILED')

    def test_a_malformed_binding_is_unknown(self):
        self.sessions(self.first)
        pm_runtime._binding_path(self.state, SESSION).write_text('{', encoding='utf-8')
        self.assertResult(self.check(), 'UNKNOWN', 'BINDING_INVALID')

    def test_a_binding_for_another_runtime_or_directory_is_unknown(self):
        session = self.bind_codex()
        self.sessions(self.first)
        self.assertResult(self.check(session), 'UNKNOWN', 'BINDING_INVALID')
        other = self.home / 'other'
        other.mkdir()
        self.assertResult(self.check(session, 'codex', cwd=str(other)), 'UNKNOWN',
                          'BINDING_INVALID')


class ClaudeRegistryTests(LivenessCase):
    def test_a_matching_session_with_a_live_pid_is_active(self):
        self.register(self.first, pid=os.getpid())
        self.assertResult(self.check(), 'ACTIVE', 'REGISTRY_LIVE_MATCH')

    def test_eperm_counts_as_live(self):
        self.register(self.first)
        with patch.object(liveness.os, 'kill', side_effect=PermissionError()):
            self.assertResult(self.check(), 'ACTIVE', 'REGISTRY_LIVE_MATCH')

    def test_a_dead_pid_is_not_a_match(self):
        self.register(self.first)
        self.transcript(self.first)
        with patch.object(liveness.os, 'kill', side_effect=ProcessLookupError()):
            self.assertResult(self.check(), 'STOPPED', 'REGISTRY_CLEAN')

    def test_a_pid_probe_failure_is_unknown(self):
        self.register(self.first)
        with patch.object(liveness.os, 'kill', side_effect=OSError()):
            self.assertResult(self.check(), 'UNKNOWN', 'REGISTRY_UNREADABLE')

    def test_another_sessions_live_entry_is_not_a_match_and_is_not_signalled(self):
        self.register(self.first, session=NEW_KEY[3:], pid=os.getpid())
        self.transcript(self.first)
        with patch.object(liveness.os, 'kill') as kill:
            self.assertResult(self.check(), 'STOPPED', 'REGISTRY_CLEAN')
        kill.assert_not_called()

    def test_malformed_entries_are_unknown(self):
        path = self.sessions(self.first) / 'bad.json'
        for content in ('{', '[]', '{}', json.dumps({'sessionId': SESSION}),
                        json.dumps({'pid': 7}),
                        json.dumps({'sessionId': 7, 'pid': 7}),
                        json.dumps({'sessionId': SESSION, 'pid': 'x'}),
                        json.dumps({'sessionId': SESSION, 'pid': True}),
                        json.dumps({'sessionId': SESSION, 'pid': 0}),
                        json.dumps({'sessionId': SESSION, 'pid': -1}),
                        json.dumps({'sessionId': SESSION, 'pid': 2 ** 40}),
                        'x' * (liveness._MAX_REGISTRY_BYTES + 1)):
            with self.subTest(content=content[:48]):
                path.write_text(content, encoding='utf-8')
                with patch.object(liveness.os, 'kill') as kill:
                    self.assertResult(self.check(), 'UNKNOWN', 'REGISTRY_UNREADABLE')
                kill.assert_not_called()

    def test_a_symlink_or_special_entry_is_unknown(self):
        target = self.home / 'target.json'
        target.write_text(json.dumps({'sessionId': NEW_KEY[3:], 'pid': 7}), encoding='utf-8')
        link = self.sessions(self.first) / 'link.json'
        link.symlink_to(target)
        self.assertResult(self.check(), 'UNKNOWN', 'REGISTRY_UNREADABLE')
        link.unlink()
        os.mkfifo(self.sessions(self.first) / 'pipe.json')
        self.assertResult(self.check(), 'UNKNOWN', 'REGISTRY_UNREADABLE')

    def test_an_unreadable_entry_is_unknown(self):
        if os.getuid() == 0:
            self.skipTest('file modes do not bind root')
        self.register(self.first).chmod(0)
        self.assertResult(self.check(), 'UNKNOWN', 'REGISTRY_UNREADABLE')

    def test_a_symlinked_sessions_directory_or_config_directory_is_unknown(self):
        real = self.home / 'real-sessions'
        real.mkdir()
        (self.first / 'sessions').symlink_to(real)
        self.assertResult(self.check(), 'UNKNOWN', 'REGISTRY_UNREADABLE')
        (self.first / 'sessions').unlink()
        self.sessions(self.second)
        link = self.home / 'linked-config'
        link.symlink_to(self.second)
        self.environ['CLAUDE_CONFIG_DIR'] = str(link)
        self.assertResult(self.check(), 'UNKNOWN', 'REGISTRY_UNREADABLE')

    def test_every_claude_account_is_checked(self):
        self.sessions(self.first)
        self.register(self.second, pid=os.getpid())
        self.assertResult(self.check(), 'ACTIVE', 'REGISTRY_LIVE_MATCH')

    def test_a_malformed_entry_in_any_account_is_unknown(self):
        self.sessions(self.first)
        (self.sessions(self.second) / 'bad.json').write_text('{', encoding='utf-8')
        self.assertResult(self.check(), 'UNKNOWN', 'REGISTRY_UNREADABLE')

    def test_config_dir_from_the_environment_is_checked(self):
        extra = self.home / 'from-env'
        extra.mkdir(mode=0o700)
        self.register(extra, pid=os.getpid())
        self.sessions(self.first)
        self.transcript(self.first)
        self.assertResult(self.check(), 'STOPPED', 'REGISTRY_CLEAN')
        self.environ['CLAUDE_CONFIG_DIR'] = str(extra)
        self.assertResult(self.check(), 'ACTIVE', 'REGISTRY_LIVE_MATCH')

    def test_legacy_and_default_config_dirs_are_checked(self):
        self.sessions(self.first)
        legacy = self.home / 'legacy'
        legacy.mkdir(mode=0o700)
        self.register(legacy, pid=os.getpid())
        self.write_config('pm_runtime:\n  claude_config_dir: ' + str(legacy) + '\n')
        self.assertResult(self.check(), 'ACTIVE', 'REGISTRY_LIVE_MATCH')
        self.write_config()
        default = self.home / '.claude'
        default.mkdir(mode=0o700)
        self.register(default, pid=os.getpid())
        self.assertResult(self.check(), 'ACTIVE', 'REGISTRY_LIVE_MATCH')

    def test_a_binding_account_that_is_not_a_configured_claude_account_is_unknown(self):
        session = self.bind_claude()
        self.sessions(self.first)
        for account in ('gone', 'codex-pm'):
            with self.subTest(account=account):
                self.update_binding(session, account=account)
                self.assertResult(self.check(session), 'UNKNOWN', 'CLAUDE_CONFIG_UNAVAILABLE')

    def test_an_invalid_user_config_is_unknown(self):
        self.sessions(self.first)
        self.config.write_text('accounts: []\n', encoding='utf-8')
        self.assertResult(self.check(), 'UNKNOWN', 'CLAUDE_CONFIG_UNAVAILABLE')

    def test_key_files_are_never_opened(self):
        sessions = self.sessions(self.first)
        (sessions / 'registry.key').write_text('FAKE_VALUE', encoding='utf-8')
        entry = self.register(self.first, session=NEW_KEY[3:])
        self.transcript(self.first)
        _audit['opened'] = []
        _audit['recording'] = True
        try:
            result = self.check()
        finally:
            _audit['recording'] = False
        self.assertResult(result, 'STOPPED', 'REGISTRY_CLEAN')
        self.assertIn(str(entry), _audit['opened'])
        self.assertFalse([path for path in _audit['opened'] if path.endswith('.key')])


class ClaudeEvidenceFloorTests(LivenessCase):
    def test_a_transcript_without_a_sessions_directory_is_unknown(self):
        self.transcript(self.first)
        self.assertResult(self.check(), 'UNKNOWN', 'TRANSCRIPT_WITHOUT_REGISTRY')

    def test_that_stays_unknown_when_another_directory_has_a_registry(self):
        self.transcript(self.first)
        self.sessions(self.second)
        self.assertResult(self.check(), 'UNKNOWN', 'TRANSCRIPT_WITHOUT_REGISTRY')

    def test_a_transcript_with_an_unreadable_sessions_path_is_unknown(self):
        self.transcript(self.first)
        (self.first / 'sessions').write_text('not a directory', encoding='utf-8')
        self.assertResult(self.check(), 'UNKNOWN', 'REGISTRY_UNREADABLE')

    def test_no_transcript_and_no_sessions_directory_anywhere_is_unknown(self):
        self.assertResult(self.check(), 'UNKNOWN', 'NO_CLAUDE_EVIDENCE')

    def test_a_recent_transcript_in_any_directory_is_active(self):
        self.sessions(self.first)
        self.transcript(self.second, age=5)
        self.assertResult(self.check(), 'ACTIVE', 'TRANSCRIPT_RECENT')

    def test_the_live_window_boundary(self):
        self.sessions(self.first)
        window = pm_runtime._LIVE_WINDOW_SECONDS
        self.transcript(self.first, age=window)
        self.assertResult(self.check(), 'ACTIVE', 'TRANSCRIPT_RECENT')
        self.transcript(self.first, age=window + 1)
        self.assertResult(self.check(), 'STOPPED', 'TRANSCRIPT_QUIET')

    def test_a_transcript_from_the_future_is_not_quiet(self):
        self.sessions(self.first)
        self.transcript(self.first, age=-60)
        self.assertResult(self.check(), 'ACTIVE', 'TRANSCRIPT_RECENT')

    def test_a_datetime_clock_is_accepted(self):
        self.sessions(self.first)
        self.transcript(self.first, age=5)
        result = self.check(clock=lambda: datetime.fromtimestamp(NOW, timezone.utc))
        self.assertResult(result, 'ACTIVE', 'TRANSCRIPT_RECENT')

    def test_a_symlinked_transcript_is_unknown(self):
        self.sessions(self.first)
        path = self.transcript(self.first)
        target = self.home / 'target.jsonl'
        path.rename(target)
        path.symlink_to(target)
        self.assertResult(self.check(), 'UNKNOWN', 'TRANSCRIPT_UNREADABLE')


class UnknownClaudeSessionTests(LivenessCase):
    """A registry with no entry for an ID says nothing: that ID may never have run."""

    def assertUnproven(self, **options):
        """UNKNOWN both on its own and under the caller's real lock, every check clean."""
        result = self.check(**options)
        self.assertResult(result, 'UNKNOWN', 'NO_CLAUDE_EVIDENCE', 'PROCESS_CLEAN',
                          'REGISTRY_CLEAN')
        with pm_runtime.transfer_lock(SESSION, self.state):
            held = self.check(lock_held=True, **options)
        self.assertResult(held, 'UNKNOWN', 'NO_CLAUDE_EVIDENCE', 'PROCESS_CLEAN',
                          'REGISTRY_CLEAN', 'LOCK_FREE')

    def test_sessions_directory_without_a_transcript_is_not_enough_evidence(self):
        self.sessions(self.first)
        self.assertUnproven()

    def test_an_unrelated_registry_entry_is_not_proof(self):
        self.register(self.first, session=NEW_KEY[3:], pid=os.getpid())
        self.assertUnproven()

    def test_a_dead_registry_entry_for_the_id_is_not_proof(self):
        self.register(self.first)
        with patch.object(liveness.os, 'kill', side_effect=ProcessLookupError()):
            self.assertUnproven()

    def test_another_sessions_transcript_is_not_proof(self):
        self.sessions(self.first)
        self.transcript(self.first, session=NEW_KEY[3:])
        self.assertUnproven()

    def test_the_sessions_transcript_for_another_directory_is_not_proof(self):
        self.sessions(self.first)
        self.transcript(self.first)
        other = self.home / 'other'
        other.mkdir()
        self.assertUnproven(cwd=str(other))

    def test_a_transcript_in_any_config_directory_is_proof(self):
        self.sessions(self.first)
        self.sessions(self.second)
        self.transcript(self.second)
        with pm_runtime.transfer_lock(SESSION, self.state):
            result = self.check(lock_held=True)
        self.assertResult(result, 'STOPPED', 'TRANSCRIPT_QUIET', 'LOCK_FREE')
        self.assertNotIn('NO_CLAUDE_EVIDENCE', result['evidence'])

    def test_active_evidence_still_outranks_it(self):
        self.sessions(self.first)
        running = '77 claude --resume ' + SESSION + '\n'
        self.assertResult(self.check(ps=running), 'ACTIVE', 'PROCESS_MATCH')
        self.assertResult(self.check(ps=running, lock_held=True), 'ACTIVE', 'PROCESS_MATCH')
        self.register(self.first, pid=os.getpid())
        self.assertResult(self.check(), 'ACTIVE', 'REGISTRY_LIVE_MATCH')
        self.assertResult(self.check(lock_held=True), 'ACTIVE', 'REGISTRY_LIVE_MATCH')

    def test_a_failed_check_keeps_its_own_code(self):
        self.sessions(self.first)
        result = self.check(process_lister=_unavailable(OSError()), lock_held=True)
        self.assertResult(result, 'UNKNOWN', 'PROCESS_LIST_FAILED', 'NO_CLAUDE_EVIDENCE')
        (self.sessions(self.first) / 'bad.json').write_text('{', encoding='utf-8')
        self.assertResult(self.check(), 'UNKNOWN', 'REGISTRY_UNREADABLE', 'NO_CLAUDE_EVIDENCE')


class CodexTests(LivenessCase):
    def test_a_bound_session_with_a_free_lock_and_clean_processes_is_stopped(self):
        session = self.bind_codex()
        result = self.check(session, 'codex')
        self.assertEqual(result, {'state': 'STOPPED', 'evidence': ['PROCESS_CLEAN', 'LOCK_FREE']})
        self.assertLockFree(session)

    def test_a_started_session_with_its_thread_is_stopped_when_clean(self):
        session = self.bind_codex()
        self.update_binding(session, session_started=True)
        self.save_thread(session)
        self.assertResult(self.check(session, 'codex'), 'STOPPED', 'PROCESS_CLEAN', 'LOCK_FREE')

    def test_the_thread_id_in_a_process_is_active(self):
        session = self.bind_codex()
        self.save_thread(session)
        result = self.check(session, 'codex', ps='77 node codex exec resume ' + THREAD + '\n')
        self.assertResult(result, 'ACTIVE', 'PROCESS_MATCH')

    def test_the_native_id_in_a_process_is_active(self):
        session = self.bind_codex()
        result = self.check(session, 'codex', ps='77 codex ' + session + '\n')
        self.assertResult(result, 'ACTIVE', 'PROCESS_MATCH')

    def test_a_held_lock_is_active(self):
        session = self.bind_codex()
        with pm_runtime.transfer_lock(session, self.state):
            self.assertResult(self.check(session, 'codex'), 'ACTIVE', 'LOCK_BUSY')

    def test_ps_failure_is_unknown(self):
        session = self.bind_codex()
        result = self.check(session, 'codex', process_lister=_unavailable(OSError()))
        self.assertResult(result, 'UNKNOWN', 'PROCESS_LIST_FAILED')

    def test_an_unbound_session_is_unknown(self):
        self.assertResult(self.check(runtime='codex'), 'UNKNOWN', 'CODEX_UNBOUND')

    def test_a_started_session_without_a_thread_is_unknown(self):
        session = self.bind_codex()
        self.update_binding(session, session_started=True)
        self.assertResult(self.check(session, 'codex'), 'UNKNOWN', 'CODEX_STATE_INVALID')

    def test_malformed_runtime_state_is_unknown(self):
        session = self.bind_codex()
        path = pm_runtime._runtime_state_path(self.state, 'pm-' + session)
        path.write_text('{', encoding='utf-8')
        self.assertResult(self.check(session, 'codex'), 'UNKNOWN', 'CODEX_STATE_INVALID')
        self.save_thread(session, 'not a thread')
        self.assertResult(self.check(session, 'codex'), 'UNKNOWN', 'CODEX_STATE_INVALID')


class InvalidInputTests(LivenessCase):
    def test_unsupported_runtime_is_unknown(self):
        self.sessions(self.first)
        for runtime in ('gemini', '', None, ['codex']):
            with self.subTest(runtime=runtime):
                self.assertResult(self.check(runtime=runtime), 'UNKNOWN', 'RUNTIME_UNSUPPORTED')

    def test_invalid_session_id_is_unknown(self):
        for session in ('pm-' + SESSION, 'not-a-uuid', '', None):
            with self.subTest(session=session):
                self.assertResult(self.check(session), 'UNKNOWN', 'SESSION_ID_INVALID')

    def test_invalid_cwd_is_unknown(self):
        for cwd in ('', None, 'bad\ndirectory'):
            with self.subTest(cwd=cwd):
                self.assertResult(self.check(cwd=cwd), 'UNKNOWN', 'CWD_INVALID')

    def test_a_failing_clock_is_unknown(self):
        self.sessions(self.first)
        result = self.check(clock=_unavailable(RuntimeError('boom')))
        self.assertResult(result, 'UNKNOWN', 'CLAUDE_CONFIG_UNAVAILABLE')

    def test_any_unexpected_exception_is_unknown(self):
        with patch.object(liveness, '_collect', side_effect=RuntimeError('boom')):
            self.assertEqual(self.check(), {'state': 'UNKNOWN', 'evidence': ['CHECK_FAILED']})


class TransferLockTests(LivenessCase):
    def test_it_is_held_for_the_whole_block_then_released(self):
        with pm_runtime.transfer_lock(SESSION, self.state):
            with self.assertRaises(LLMToolError) as caught:
                with pm_runtime.transfer_lock(SESSION, self.state):
                    self.fail('a second holder entered the block')
            self.assertEqual(caught.exception.pm_runtime_code, 'PM_SESSION_BUSY')
        self.assertLockFree(SESSION)

    def test_it_is_released_when_the_block_raises(self):
        with self.assertRaises(RuntimeError):
            with pm_runtime.transfer_lock(SESSION, self.state):
                raise RuntimeError('boom')
        self.assertLockFree(SESSION)

    def test_it_excludes_a_driver_on_the_same_lock_file(self):
        session = self.bind_claude()
        runner = Mock()
        runtime = pm_runtime.PMRuntime(session, cwd=str(self.cwd), account='claude-one',
                                       runner=runner, state_root=self.state,
                                       environ=self.environ)
        with pm_runtime.transfer_lock(session, self.state):
            with self.assertRaises(LLMToolError) as caught:
                runtime.open()
        self.assertEqual(caught.exception.pm_runtime_code, 'PM_SESSION_BUSY')
        runner.assert_not_called()


class FenceTests(LivenessCase):
    def fence_path(self, session=SESSION):
        return self.state / ('pm-' + session + '.fence.json')

    def test_absent_fence_reads_as_none(self):
        self.assertIsNone(pm_runtime.read_session_fence(SESSION, self.state))

    def test_written_fence_round_trips_as_a_private_file(self):
        pm_runtime.write_session_fence(SESSION, NEW_KEY, '2026-11-07T00:00:00Z', self.state)
        self.assertEqual(pm_runtime.read_session_fence(SESSION, self.state),
                         {'native_session_id': SESSION, 'new_session_key': NEW_KEY,
                          'at': '2026-11-07T00:00:00Z'})
        self.assertEqual(self.fence_path().stat().st_mode & 0o777, 0o600)

    def test_an_invalid_fence_is_not_written(self):
        for new_key, at in (('pm-' + SESSION, 'now'), ('bad key', 'now'), (None, 'now'),
                            (NEW_KEY, ''), (NEW_KEY, None)):
            with self.subTest(new_key=new_key, at=at):
                with self.assertRaises(LLMToolError):
                    pm_runtime.write_session_fence(SESSION, new_key, at, self.state)
                self.assertFalse(self.fence_path().exists())

    def unreadable_fences(self):
        """Yield once for each malformed or unsafe fence file left in place."""
        path = self.fence_path(self.session)
        target = self.home / 'fence-target.json'
        target.write_text(json.dumps({'native_session_id': self.session,
                                      'new_session_key': NEW_KEY, 'at': 'now'}),
                          encoding='utf-8')
        for content in ('{', '[]', '{}', json.dumps({'native_session_id': 'other',
                                                     'new_session_key': NEW_KEY, 'at': 'now'})):
            path.write_text(content, encoding='utf-8')
            yield 'content ' + content[:24]
            path.unlink()
        path.symlink_to(target)
        yield 'symlink'
        path.unlink()
        path.symlink_to(self.home / 'missing')
        yield 'dangling symlink'
        path.unlink()
        path.mkdir()
        yield 'directory'
        path.rmdir()

    def test_a_malformed_or_unsafe_fence_reads_as_fenced(self):
        self.session = SESSION
        for label in self.unreadable_fences():
            with self.subTest(label):
                value = pm_runtime.read_session_fence(SESSION, self.state)
                self.assertIsNotNone(value)
                self.assertNotIn('new_session_key', value)

    def seed_history(self, session):
        path = pm_runtime._history_path(self.state, 'pm-' + session)
        path.write_text(json.dumps({
            'pm_session_id': session,
            'turns': [{'turn_id': 't1', 'seq': 1, 'status': 'ACTIVE'}]}), encoding='utf-8')
        path.chmod(0o600)
        return path

    def assertRefused(self, runtime, session):
        with self.assertRaises(LLMToolError) as caught:
            runtime.open()
        self.assertEqual(caught.exception.pm_runtime_code, 'PM_SESSION_TRANSFERRED')
        self.assertIsNone(runtime._child)
        self.assertIsNone(runtime._lock_fd)
        self.assertLockFree(session)

    def assertUntouched(self, session, history, binding_before, transcript=None):
        self.assertEqual(history[0].read_bytes(), history[1])
        self.assertEqual(pm_runtime._binding_path(self.state, session).read_bytes(),
                         binding_before)
        self.assertEqual([turn['turn_id'] for turn in pm_runtime.read_history(
            session, state_root=self.state)['turns']], ['t1'])
        if transcript is not None:
            self.assertEqual(transcript.read_bytes(), b'{}\n')

    def claude_runtime(self, session, runner):
        return pm_runtime.PMRuntime(session, cwd=str(self.cwd), account='claude-one',
                                    runner=runner, state_root=self.state, environ=self.environ)

    def codex_runtime(self, session):
        return pm_runtime.PMRuntime(session, cwd=str(self.cwd), state_root=self.state,
                                    environ=self.environ)

    def test_a_fenced_claude_session_is_refused_with_no_child_and_nothing_changed(self):
        session = self.bind_claude()
        transcript = self.transcript(self.first, session)
        path = self.seed_history(session)
        history = (path, path.read_bytes())
        binding = pm_runtime._binding_path(self.state, session).read_bytes()
        pm_runtime.write_session_fence(session, NEW_KEY, 'now', self.state)
        calls = []
        self.assertRefused(self.claude_runtime(session, lambda *args: calls.append(args)), session)
        self.assertEqual(calls, [])
        self.assertUntouched(session, history, binding, transcript)

    def test_a_fenced_codex_session_is_refused_with_no_child_and_nothing_changed(self):
        session = self.bind_codex()
        path = self.seed_history(session)
        history = (path, path.read_bytes())
        binding = pm_runtime._binding_path(self.state, session).read_bytes()
        pm_runtime.write_session_fence(session, NEW_KEY, 'now', self.state)
        with patch('llmtool.pm_runtime_codex.subprocess.Popen') as popen:
            self.assertRefused(self.codex_runtime(session), session)
        popen.assert_not_called()
        self.assertUntouched(session, history, binding)

    def test_an_unreadable_fence_refuses_both_runtimes(self):
        for bind, make in ((self.bind_claude,
                            lambda session: self.claude_runtime(session, self.fail)),
                           (self.bind_codex, self.codex_runtime)):
            self.session = bind()
            for label in self.unreadable_fences():
                with self.subTest(label):
                    self.assertRefused(make(self.session), self.session)

    def test_an_unfenced_claude_session_still_opens(self):
        session = self.bind_claude()
        calls = []

        def runner(args, cwd, env):
            calls.append(args)
            return FakeChild()

        runtime = self.claude_runtime(session, runner)
        self.addCleanup(runtime.close)
        with patch.object(pm_runtime, '_session_live_elsewhere', return_value=False):
            runtime.open()
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
