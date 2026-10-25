"""Background Claude Developer dispatch receipt (Issue #365).

Every case drives a fake `claude` executable from a scenario file; no real
session, model or network call is made.
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import test_task_completion as fixtures

_SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'claude_bg_dispatch.py'
_spec = importlib.util.spec_from_file_location('claude_bg_dispatch_under_test', _SCRIPT)
dispatch_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dispatch_tool)

TARGET = '2b128608-8b94-4c12-96b9-e6cdf9a388c7'
COPY = '95c9758e-1111-4222-8333-944455556666'
OTHER = '3c239719-0000-4000-8000-000000000000'

# The fake reads the scenario, appends every argv to calls.jsonl, and on
# `--bg` stamps the launch time and writes the scenario's transcripts.
FAKE_CLAUDE = r'''
import json, os, sys, time
from pathlib import Path
home = Path(os.environ['FAKE_CLAUDE_DIR'])
scenario = json.loads((home / 'scenario.json').read_text())
state_path = home / 'state.json'
state = json.loads(state_path.read_text()) if state_path.exists() else {'polls': 0}
argv = sys.argv[1:]
with open(home / 'calls.jsonl', 'a') as log:
    log.write(json.dumps({'argv': argv, 'cwd': os.getcwd()}) + '\n')

def fill(value):
    text = json.dumps(value)
    text = text.replace('"LAUNCH"', str(state.get('launch_ms', 0) + 1)).replace('"OLD"', '1')
    return json.loads(text.replace('NEW-SESSION', state.get('new_session', 'NEW-SESSION')))

def save():
    state_path.write_text(json.dumps(state))

if argv[:2] == ['agents', '--json']:
    if '--all' in argv:
        rows = scenario.get('all', [])
    elif 'launch_ms' not in state:
        rows = scenario.get('before', [])
    else:
        after = scenario.get('after', [[]])
        rows = after[min(state['polls'], len(after) - 1)]
        state['polls'] += 1
        save()
    print(json.dumps(fill(rows)))
elif '--bg' in argv:
    state['launch_ms'] = int(time.time() * 1000)
    if '--session-id' in argv:
        state['new_session'] = argv[argv.index('--session-id') + 1]
    save()
    now = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(time.time() + 0.5))
    for item in scenario.get('transcripts', []):
        sid = fill(item['session'])
        folder = Path(os.environ['CLAUDE_CONFIG_DIR']) / 'projects' / item.get('project', 'p')
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / (sid + '.jsonl'), 'a') as out:
            out.write(json.dumps({'type': 'user', 'cwd': '/old/cwd',
                                  'timestamp': '2026-01-01T00:00:00.000Z'}) + '\n')
            for index in range(item.get('old_actions', 0)):
                out.write(json.dumps({'type': 'assistant',
                    'timestamp': '2026-01-01T00:00:%02d.000Z' % (index % 60),
                    'message': {'content': [{'type': 'tool_use', 'name': 'Old'}]}}) + '\n')
            if item.get('new_action'):
                out.write(json.dumps({'type': 'assistant', 'timestamp': now,
                    'message': {'content': [{'type': 'text', 'text': 'x'},
                                            {'type': 'tool_use', 'name': 'Bash'}]}}) + '\n')
    print(scenario.get('launch_stdout', 'backgrounded'))
    sys.exit(scenario.get('launch_exit', 0))
elif argv[:1] == ['stop']:
    print('stopped')
else:
    sys.exit(9)
'''


def row(session, *, name='claude-developer', status='busy', state=None, started='LAUNCH',
        cwd='/repo', kind='background'):
    value = {'sessionId': session, 'id': session[:8], 'name': name, 'status': status,
             'startedAt': started, 'cwd': cwd, 'kind': kind, 'pid': 1}
    if state is not None:
        value['state'] = state
    return value


class ClaudeBgDispatchTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.TaskCompletionTests('test_failed_test_subprocess_is_recorded_with_nonzero_status')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.config = self.home / 'claude-config'
        self.config.mkdir()
        self.claude = self.home / 'claude'
        self.claude.write_text('#!' + sys.executable + ' -B\n' + FAKE_CLAUDE)
        self.claude.chmod(0o700)
        env = patch.dict(os.environ, {'FAKE_CLAUDE_DIR': str(self.home),
                                      'CLAUDE_CONFIG_DIR': str(self.config)})
        env.start()
        self.addCleanup(env.stop)
        self.root = str(self.f.root)
        self.cwd = str(self.home)  # the session's recorded cwd must exist to launch there

    def scenario(self, **value):
        (self.home / 'scenario.json').write_text(json.dumps(value))

    def calls(self):
        path = self.home / 'calls.jsonl'
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def launches(self):
        return [call for call in self.calls() if '--bg' in call['argv']]

    def stops(self):
        return [call['argv'][1] for call in self.calls() if call['argv'][:1] == ['stop']]

    def run_tool(self, *args, timeout='1'):
        out = StringIO()
        argv = ['--claude', str(self.claude), '--poll-interval', '0.01'] + list(args)
        if args and args[0] == 'dispatch':
            argv += ['--repo-root', self.root, '--timeout', timeout]
        with redirect_stdout(out):
            code = dispatch_tool._main(argv)
        return code, json.loads(out.getvalue())

    def dispatch(self, session=TARGET, *, name='claude-developer', task='executor-task'):
        return self.run_tool('dispatch', '--session', session, '--name', name,
                             '--task-id', task, '--prompt', 'Task Contract: do the work')

    def test_busy_with_new_action_is_delivered_and_old_history_is_ignored(self):
        self.scenario(
            all=[row(TARGET, status='done', started='OLD', cwd=self.cwd)],
            before=[], after=[[], [row(TARGET, status='busy', cwd=self.cwd)]],
            transcripts=[{'session': TARGET, 'old_actions': 58, 'new_action': True}])
        code, receipt = self.dispatch()
        self.assertEqual(code, 0, receipt)
        self.assertEqual(receipt['result'], 'DELIVERED')
        self.assertEqual(receipt['actual_session_id'], TARGET)
        self.assertFalse(receipt['copy_started'])
        self.assertTrue(receipt['name_ok'])
        self.assertEqual(receipt['first_action']['tool'], 'Bash')
        self.assertEqual(receipt['d6_task_id'], 'executor-task')
        self.assertEqual(receipt['d6_root'], self.root)
        # The registry names a label, not this session: the PM must update it.
        self.assertEqual(receipt['session_ref_update_required'],
                         {'task_id': 'executor-task', 'from': 'Executor D6 session',
                          'to': TARGET})
        [launch] = self.launches()
        argv = launch['argv']
        self.assertEqual(argv[argv.index('--resume') + 1], TARGET)
        self.assertEqual(argv[argv.index('-n') + 1], 'claude-developer')
        self.assertEqual(argv[argv.index('--add-dir') + 1], self.root)
        self.assertEqual(os.path.realpath(launch['cwd']), os.path.realpath(self.cwd))
        self.assertEqual(self.stops(), [])

    def test_only_resumed_history_is_not_a_receipt(self):
        self.scenario(
            all=[row(TARGET, status='done', started='OLD', cwd=self.cwd)],
            after=[[row(TARGET, status='busy', cwd=self.cwd)]],
            transcripts=[{'session': TARGET, 'old_actions': 58, 'new_action': False}])
        code, receipt = self.dispatch()
        self.assertEqual(code, 1)
        self.assertEqual(receipt['result'], 'NOT_DELIVERED')
        self.assertEqual(receipt['reason'], 'NO_NEW_ACTION')
        self.assertIsNone(receipt['first_action'])
        self.assertEqual(self.stops(), [TARGET[:8]])

    def test_short_id_resolves_to_the_full_uuid(self):
        self.scenario(
            all=[row(TARGET, status='done', started='OLD', cwd=self.cwd),
                 row(OTHER, name='someone-else', status='done', started='OLD', cwd=self.cwd)],
            after=[[row(TARGET, cwd=self.cwd)]],
            transcripts=[{'session': TARGET, 'new_action': True}])
        code, receipt = self.dispatch(TARGET[:8])
        self.assertEqual(code, 0, receipt)
        self.assertEqual(receipt['resolved_session_id'], TARGET)
        argv = self.launches()[0]['argv']
        self.assertEqual(argv[argv.index('--resume') + 1], TARGET)

    def test_short_id_of_a_stopped_session_resolves_from_its_transcript(self):
        folder = self.config / 'projects' / 'p'
        folder.mkdir(parents=True)
        (folder / (TARGET + '.jsonl')).write_text(json.dumps(
            {'type': 'user', 'cwd': self.cwd, 'timestamp': '2026-01-01T00:00:00.000Z'}) + '\n')
        self.scenario(after=[[row(TARGET, cwd=self.cwd)]],
                      transcripts=[{'session': TARGET, 'new_action': True}])
        code, receipt = self.dispatch(TARGET[:8])
        self.assertEqual(code, 0, receipt)
        self.assertEqual(receipt['resolved_session_id'], TARGET)

    def test_unknown_or_ambiguous_session_is_rejected_before_launch(self):
        self.scenario(all=[row(TARGET, status='done', started='OLD'),
                           row(OTHER, status='done', started='OLD')])
        for target, reason in (('ffffffff', 'SESSION_NOT_FOUND'),
                               ('no-such-name', 'SESSION_NOT_FOUND'),
                               ('aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee', 'SESSION_NOT_FOUND'),
                               ('claude-developer', 'SESSION_AMBIGUOUS')):
            with self.subTest(target=target):
                code, result = self.dispatch(target)
                self.assertEqual(code, 2)
                self.assertEqual(result['reason'], reason)
        self.assertEqual(self.launches(), [])

    def test_blocked_and_waiting_sessions_are_stopped(self):
        for status, state, reason in (('blocked', None, 'SESSION_BLOCKED'),
                                      ('waiting', None, 'SESSION_WAITING'),
                                      ('busy', 'blocked', 'SESSION_BLOCKED')):
            with self.subTest(status=status, state=state):
                for name in ('calls.jsonl', 'state.json'):
                    (self.home / name).unlink(missing_ok=True)
                self.scenario(
                    all=[row(TARGET, status='done', started='OLD', cwd=self.cwd)],
                    after=[[row(TARGET, status=status, state=state, cwd=self.cwd)]],
                    transcripts=[{'session': TARGET, 'new_action': True}])
                code, receipt = self.dispatch()
                self.assertEqual(code, 1)
                self.assertEqual(receipt['reason'], reason)
                self.assertEqual(receipt['stop']['session'], TARGET[:8])
                self.assertEqual(self.stops(), [TARGET[:8]])

    def test_vanished_or_never_started_session_fails(self):
        self.scenario(all=[row(TARGET, status='done', started='OLD', cwd=self.cwd)],
                      after=[[row(TARGET, cwd=self.cwd)], []])
        code, receipt = self.dispatch()
        self.assertEqual(code, 1)
        self.assertEqual(receipt['reason'], 'SESSION_GONE')
        self.assertEqual(self.stops(), [TARGET])
        for name in ('calls.jsonl', 'state.json'):
            (self.home / name).unlink()
        self.scenario(all=[row(TARGET, status='done', started='OLD', cwd=self.cwd)],
                      after=[[]])
        code, receipt = self.dispatch()
        self.assertEqual(code, 1)
        self.assertEqual(receipt['reason'], 'SESSION_NOT_STARTED')
        self.assertEqual(self.stops(), [])  # nothing identified, nothing to stop

    def test_copy_with_wrong_name_reports_the_actual_session(self):
        original = row(TARGET, status='idle', started='OLD', cwd=self.cwd)
        copy = row(COPY, name='pr370-fix-d6-worktree-ui', cwd=self.cwd)
        self.scenario(
            all=[original], before=[original], after=[[original, copy]],
            launch_stdout='Session already running; started a copy: ' + COPY[:8],
            transcripts=[{'session': COPY, 'old_actions': 58, 'new_action': True}])
        code, receipt = self.dispatch()
        self.assertEqual(code, 0, receipt)
        self.assertEqual(receipt['result'], 'DELIVERED')
        self.assertEqual(receipt['resolved_session_id'], TARGET)
        self.assertEqual(receipt['actual_session_id'], COPY)
        self.assertTrue(receipt['copy_started'])
        self.assertFalse(receipt['name_ok'])
        self.assertEqual(receipt['actual_name'], 'pr370-fix-d6-worktree-ui')
        self.assertEqual(receipt['session_ref_update_required']['to'], COPY)
        self.assertEqual(set(receipt['warnings']),
                         {'COPY_STARTED', 'NAME_MISMATCH', 'SESSION_REF_UPDATE_REQUIRED'})

    def test_busy_target_is_refused_as_a_second_writer(self):
        self.scenario(all=[row(TARGET, started='OLD', cwd=self.cwd)],
                      before=[row(TARGET, started='OLD', cwd=self.cwd)])
        code, result = self.dispatch()
        self.assertEqual(code, 2)
        self.assertEqual(result['reason'], 'SESSION_BUSY')
        self.assertEqual(self.launches(), [])

    def test_unregistered_task_role_and_prompt_are_rejected_before_launch(self):
        self.scenario(all=[row(TARGET, status='done', started='OLD', cwd=self.cwd)])
        code, result = self.dispatch(task='no-such-task')
        self.assertEqual((code, result['reason']), (2, 'D6_REGISTRATION_INVALID'))
        code, result = self.dispatch(name='Bad Name')
        self.assertEqual((code, result['reason']), (2, 'ROLE_NAME_INVALID'))
        code, result = self.run_tool('dispatch', '--session', TARGET, '--name', 'claude-developer',
                                     '--task-id', 'executor-task', '--prompt=--evil')
        self.assertEqual((code, result['reason']), (2, 'PROMPT_INVALID'))
        self.assertEqual(self.launches(), [])

    def test_failed_launch_is_not_delivered(self):
        self.scenario(all=[row(TARGET, status='done', started='OLD', cwd=self.cwd)],
                      launch_exit=1)
        code, receipt = self.dispatch()
        self.assertEqual((code, receipt['reason']), (1, 'LAUNCH_FAILED'))

    def test_new_session_starts_in_the_d6_root_with_its_own_uuid(self):
        self.scenario(after=[[row('NEW-SESSION', cwd=self.root)]],
                      transcripts=[{'session': 'NEW-SESSION', 'new_action': True}])
        code, receipt = self.run_tool('dispatch', '--new', '--name', 'claude-developer',
                                      '--task-id', 'executor-task', '--prompt', 'work')
        self.assertEqual(code, 0, receipt)
        [launch] = self.launches()
        argv = launch['argv']
        new_id = argv[argv.index('--session-id') + 1]
        self.assertTrue(dispatch_tool.UUID_RE.fullmatch(new_id))
        self.assertEqual(receipt['actual_session_id'], new_id)
        self.assertEqual(os.path.realpath(launch['cwd']), self.root)

    def test_watch_returns_on_report_and_stops_a_blocked_session(self):
        report = self.home / 'report.md'
        self.scenario(after=[[row(TARGET)]])
        (self.home / 'state.json').write_text(json.dumps({'polls': 0, 'launch_ms': 1}))
        report.write_text('PASS')
        code, result = self.run_tool('watch', '--session-id', TARGET, '--report-file',
                                     str(report), '--timeout', '1')
        self.assertEqual((code, result['result']), (0, 'REPORT_PRESENT'))
        report.unlink()
        self.scenario(after=[[row(TARGET, status='waiting')]])
        code, result = self.run_tool('watch', '--session-id', TARGET, '--report-file',
                                     str(report), '--timeout', '1')
        self.assertEqual((code, result['reason']), (1, 'SESSION_WAITING'))
        self.assertEqual(self.stops(), [TARGET[:8]])
        self.scenario(after=[[]])
        code, result = self.run_tool('watch', '--session-id', TARGET, '--report-file',
                                     str(report), '--timeout', '1')
        self.assertEqual((code, result['reason']), (1, 'SESSION_GONE'))

    def test_resumed_session_keeping_its_old_start_time_is_still_identified(self):
        # Not active before the launch, active after it: that is this launch.
        self.scenario(all=[row(TARGET, status='done', started='OLD', cwd=self.cwd)],
                      after=[[row(TARGET, started='OLD', cwd=self.cwd)]],
                      transcripts=[{'session': TARGET, 'new_action': True}])
        code, receipt = self.dispatch()
        self.assertEqual(code, 0, receipt)
        self.assertEqual(receipt['actual_session_id'], TARGET)
        self.assertFalse(receipt['copy_started'])

    def test_transcript_is_read_incrementally_and_waits_for_complete_lines(self):
        folder = self.config / 'projects' / 'p'
        folder.mkdir(parents=True)
        path = folder / (TARGET + '.jsonl')
        old = {'type': 'assistant', 'timestamp': '2026-01-01T00:00:00.000Z',
               'message': {'content': [{'type': 'tool_use', 'name': 'Old'}]}}
        new = dict(old, timestamp='2026-10-26T00:00:00.000Z',
                   message={'content': [{'type': 'tool_use', 'name': 'Read'}]})
        since = 1792972800000  # 2026-10-26T00:00:00Z
        line = json.dumps(new)
        path.write_text(json.dumps(old) + '\n' + line[:10])
        offsets = {}
        self.assertIsNone(dispatch_tool.first_action_since(TARGET, since, offsets))
        with open(path, 'a') as stream:
            stream.write(line[10:] + '\n')
        self.assertEqual(dispatch_tool.first_action_since(TARGET, since, offsets),
                         {'tool': 'Read', 'timestamp': '2026-10-26T00:00:00.000Z'})

    def test_transcripts_are_read_from_claude_config_dir_only(self):
        other = self.home / 'other-account'
        (other / 'projects' / 'p').mkdir(parents=True)
        (other / 'projects' / 'p' / (TARGET + '.jsonl')).write_text('{}\n')
        self.scenario()
        code, result = self.dispatch(TARGET)
        self.assertEqual((code, result['reason']), (2, 'SESSION_NOT_FOUND'))
        with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(other)}):
            self.assertEqual(dispatch_tool.transcript_paths(TARGET),
                             [str(other / 'projects' / 'p' / (TARGET + '.jsonl'))])


if __name__ == '__main__':
    unittest.main()
