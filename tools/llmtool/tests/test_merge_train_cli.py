"""``llmtool task train`` build, status, merge and close (#1182 T3b2)."""

import contextlib
import copy
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from llmtool import cli, merge_train, merge_train_cli, merge_train_eligibility
from llmtool import merge_train_ops, merge_train_store, pm_caller
from llmtool import source_freshness, task_advance, task_completion, task_submission_cli
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore

SESSION = '3f2b8c1e-6a4d-4e2b-9c1a-7d5e8f9a0b1c'
OTHER_SESSION = '9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d'
TASK_A = 'a' * 32
TASK_B = 'b' * 32
TASK_C = 'c' * 32
TASK_D = 'd' * 32
EXECUTOR = 'e' * 32
TRAIN_ID = 'train-0001'
TRAIN_PR = 500


class FakeStore:
    def __init__(self, records):
        self.records = records
        self.calls = []

    def get(self, task_id):
        self.calls.append(task_id)
        if task_id not in self.records:
            raise KeyError(task_id)
        return copy.deepcopy(self.records[task_id])


class Recorder:
    """A fake step; records every call and returns ``result``."""

    def __init__(self, result=None, side_effect=None):
        self.result = result
        self.side_effect = side_effect
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.side_effect is not None:
            return self.side_effect(*args, **kwargs)
        return copy.deepcopy(self.result)


class Forbidden:
    """An adapter the CLI may pass on but must never call itself."""

    def __call__(self, *args, **kwargs):
        raise AssertionError('adapter must not be called by the CLI')


def _record(train_id=TRAIN_ID, state='BUILT', task_ids=(TASK_A, TASK_B)):
    members = []
    for index, task_id in enumerate(task_ids):
        members.append({
            'task_id': task_id, 'pr': 101 + index, 'head': str(index + 1) * 40,
            'executor_task': EXECUTOR, 'review_version': 1,
            'linux_check_run_id': None, 'macos_check_run_id': None,
            'remote_test_receipt_digest': 'abcdef12'})
    return {
        'schema_version': 1, 'train_id': train_id, 'main_sha': 'c' * 40,
        'members': members, 'excluded': [], 'train_branch': 'llmtool/train-' + train_id,
        'train_head': 'd' * 40, 'train_pr': TRAIN_PR, 'state': state,
        'merge_attempt': None, 'merge_sha': None,
        'created_at': '2026-11-08T00:00:00Z'}


class MergeTrainCliTest(unittest.TestCase):
    def setUp(self):
        environment = mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for name in pm_caller.ENVIRONMENT_KEYS:
            os.environ.pop(name, None)
        # The executor-task pattern belongs to task_store and is outside this
        # Task; pin it so test records stay valid whatever its exact format.
        pattern = mock.patch.object(merge_train_store, '_EXECUTOR_TASK',
                                    re.compile(r'[0-9a-f]{32}'))
        pattern.start()
        self.addCleanup(pattern.stop)
        # cli.py checks source freshness before build, merge and close; keep the
        # tests off the real checkout and record each check.
        self.freshness = Recorder('OK')
        fresh = mock.patch.object(source_freshness, 'require_fresh_source', self.freshness)
        fresh.start()
        self.addCleanup(fresh.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = temporary.name
        self.train_root = os.path.join(self.base, 'tasks')
        self.train_dir = os.path.join(self.base, merge_train_store.DIRECTORY_NAME)
        self.caller = pm_caller.resolve_caller_session(SESSION, environ={})
        self.other = pm_caller.resolve_caller_session(OTHER_SESSION, environ={})
        self.store = FakeStore({
            TASK_A: {'task_id': TASK_A, 'pm_session_id': self.caller},
            TASK_B: {'task_id': TASK_B, 'pm_session_id': self.caller},
            TASK_C: {'task_id': TASK_C, 'pm_session_id': self.other},
            TASK_D: {'task_id': TASK_D, 'pm_session_id': self.caller},
        })
        self.root = os.path.join(self.base, 'repo')
        self.github_get = Forbidden()
        self.gh_runner = Forbidden()
        self.git_runner = Forbidden()

    def _create(self, **kwargs):
        return merge_train_store.create(_record(**kwargs), root=self.train_root)

    def _adapters(self, **inject):
        adapters = {'store': self.store, 'train_root': self.train_root, 'root': self.root,
                    'github_get': self.github_get, 'gh_runner': self.gh_runner,
                    'git_runner': self.git_runner}
        adapters.update(inject)
        return adapters

    def _main(self, argv, **inject):
        """Run ``cli.main`` with fakes injected; return (exit, one JSON object, runs)."""
        original = merge_train_cli.run_task_train
        runs = []

        def wrapped(args):
            runs.append(args)
            return original(args, **self._adapters(**inject))

        out = io.StringIO()
        with mock.patch.object(merge_train_cli, 'run_task_train', wrapped), \
                contextlib.redirect_stdout(out):
            try:
                code = cli.main(['task', 'train', *argv])
            except SystemExit as exc:
                code = exc.code
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 1, out.getvalue())
        return code, json.loads(lines[0]), runs

    def _run(self, argv, **adapters):
        """Call run_task_train directly (defaults not injected); one JSON object."""
        args = cli.parser_for_cli().parse_args(['task', 'train', *argv])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = merge_train_cli.run_task_train(args, **adapters)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 1, out.getvalue())
        return code, json.loads(lines[0])

    def assertRefused(self, value, code, train_id=None, task_id=None):
        self.assertEqual(value['step'], 'REFUSED')
        self.assertEqual(value['codes'], [code])
        self.assertIsNone(value['result'])
        self.assertEqual(value['task_id'], task_id)
        if train_id is None:
            self.assertNotIn('train_id', value)
        else:
            self.assertEqual(value['train_id'], train_id)

    # Parser

    def test_parser_accepts_each_command(self):
        parser = cli.parser_for_cli()
        args = parser.parse_args(['task', 'train', 'build', '--member', TASK_A + ':101',
                                  '--member', TASK_B + ':102', '--pm-session', SESSION])
        self.assertEqual((args.action, args.train_action), ('train', 'build'))
        self.assertEqual(args.member, [TASK_A + ':101', TASK_B + ':102'])
        self.assertEqual(args.pm_session, SESSION)
        args = parser.parse_args(['task', 'train', 'status', TRAIN_ID])
        self.assertEqual((args.train_action, args.train_id), ('status', TRAIN_ID))
        for action in ('merge', 'close'):
            args = parser.parse_args(['task', 'train', action, TRAIN_ID])
            self.assertEqual((args.train_action, args.train_id, args.pm_session),
                             (action, TRAIN_ID, None))
            args = parser.parse_args(['task', 'train', action, TRAIN_ID,
                                      '--pm-session', SESSION])
            self.assertEqual(args.pm_session, SESSION)

    def _stale(self):
        def stale():
            error = LLMToolError('LLMTOOL_SOURCE_STALE')
            error.completion_code = 'LLMTOOL_SOURCE_STALE'
            raise error
        self.freshness.side_effect = stale

    def assertInputRefused(self, argv):
        """``task train *argv`` prints one TRAIN_INPUT_INVALID JSON object, exits 1,
        writes nothing to stderr and calls no freshness check, store or step."""
        steps = {name: Recorder({'step': 'BUILT', 'codes': [], 'result': None})
                 for name in ('build_call', 'merge_call', 'close_call', 'advance_call')}
        adapters = self._adapters(**steps)
        original = merge_train_cli.run_task_train
        runs = []

        def wrapped(args):
            runs.append(args)
            return original(args, **adapters)

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(merge_train_cli, 'run_task_train', wrapped), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(['task', 'train', *argv])
            except SystemExit as exc:
                code = exc.code
        self.assertEqual(code, 1)
        self.assertEqual(err.getvalue(), '')
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 1, out.getvalue())
        self.assertRefused(json.loads(lines[0]), merge_train_cli.TRAIN_INPUT_INVALID)
        self.assertEqual(runs, [])
        self.assertEqual(self.freshness.calls, [])
        self.assertEqual(self.store.calls, [])
        for name, step in steps.items():
            self.assertEqual(step.calls, [], name)

    def _valid_argv(self):
        return {
            'build': ['build', '--member', TASK_A + ':101', '--member', TASK_B + ':102',
                      '--pm-session', SESSION],
            'status': ['status', TRAIN_ID],
            'merge': ['merge', TRAIN_ID, '--pm-session', SESSION],
            'close': ['close', TRAIN_ID, '--pm-session', SESSION],
        }

    def test_malformed_input_refuses_once_without_any_call(self):
        member_a, member_b = TASK_A + ':101', TASK_B + ':102'
        cases = [
            [],
            ['bogus'],
            ['status'],
            ['status', TRAIN_ID, 'extra'],
            ['status', 'Bad_ID'],
            ['merge', TRAIN_ID, '--bogus'],
            ['merge', '../escape'],
            ['close'],
            ['build'],
            ['build', '--member', member_a],
            ['build', '--member', member_a, '--member', member_b, '--bogus'],
            ['build', '--member', member_a, '--member', 'not-a-member'],
            ['build', '--member', member_a, '--member', TASK_B + ':0'],
            ['build', '--member', member_a, '--member', TASK_B + ':01'],
            ['build', '--member', member_a, '--member', TASK_B.upper() + ':102'],
            ['build', '--member', member_a, '--member', member_a],
            ['build', '--member', member_a, '--member', TASK_B + ':101'],
            ['build'] + [item for index in range(7) for item in
                         ('--member', format(index + 1, '032x') + ':' + str(index + 1))],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                self.assertInputRefused(argv + ['--pm-session', SESSION]
                                        if argv[:1] == ['build'] else argv)

    def test_unknown_train_level_option_refuses_before_every_subcommand(self):
        for name, argv in self._valid_argv().items():
            for option in (['--bogus'], ['-x'], ['--bogus=1'], ['--member', TASK_A + ':101'],
                           ['--pm-session', SESSION], ['--train-id', TRAIN_ID]):
                with self.subTest(command=name, option=option):
                    self.assertInputRefused(option + argv)

    def test_unknown_subcommand_option_refuses(self):
        for name, argv in self._valid_argv().items():
            for option in (['--bogus'], ['-x'], ['--bogus=1'], ['--json']):
                with self.subTest(command=name, option=option, where='end'):
                    self.assertInputRefused(argv + option)
                with self.subTest(command=name, option=option, where='after-name'):
                    self.assertInputRefused(argv[:1] + option + argv[1:])
        # status takes no caller session.
        self.assertInputRefused(['status', TRAIN_ID, '--pm-session', SESSION])

    def test_help_flags_refuse_at_every_train_level(self):
        member_a, member_b = TASK_A + ':101', TASK_B + ':102'
        cases = [
            ['-h'], ['--help'], ['-h', 'build'], ['--help', 'status', TRAIN_ID],
            ['-h', 'merge', TRAIN_ID, '--pm-session', SESSION], ['-h', 'bogus'],
            ['bogus', '-h'], ['--help', '--bogus'],
            ['build', '-h'], ['build', '--help'],
            ['build', '--member', member_a, '--member', member_b, '--help'],
            ['build', '-h', '--member', 'not-a-member'],
            ['build', '--member', member_a, '-h', '--pm-session', SESSION],
            ['status', '-h'], ['status', TRAIN_ID, '--help'], ['status', '--help', 'Bad_ID'],
            ['merge', '--help'], ['merge', TRAIN_ID, '-h', '--pm-session', SESSION],
            ['merge', '../escape', '-h'],
            ['close', '-h', TRAIN_ID], ['close', TRAIN_ID, '--pm-session', SESSION, '--help'],
            ['close', '--help', '--bogus'],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                self.assertInputRefused(argv)

    def test_missing_option_values_refuse(self):
        member_a = TASK_A + ':101'
        cases = [
            ['build', '--member'],
            ['build', '--member', member_a, '--member'],
            ['build', '--member', '--pm-session', SESSION],
            ['build', '--member', member_a, '--member', TASK_B + ':102', '--pm-session'],
            ['merge', TRAIN_ID, '--pm-session'],
            ['merge', '--pm-session', SESSION],
            ['close', TRAIN_ID, '--pm-session'],
            ['close', '--pm-session', SESSION],
            ['status'],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                self.assertInputRefused(argv)

    def test_malformed_semantic_input_refuses_before_a_stale_source_check(self):
        self._stale()
        member_a = TASK_A + ':101'
        cases = [
            ['build', '--member', member_a, '--pm-session', SESSION],
            ['build', '--member', member_a, '--member', 'not-a-member', '--pm-session', SESSION],
            ['build', '--member', member_a, '--member', member_a, '--pm-session', SESSION],
            ['build', '--pm-session', SESSION] + [
                item for index in range(7) for item in
                ('--member', format(index + 1, '032x') + ':' + str(index + 1))],
            ['merge', 'Bad_ID', '--pm-session', SESSION],
            ['merge', '../escape', '--pm-session', SESSION],
            ['close', 'Bad_ID', '--pm-session', SESSION],
            ['close', '', '--pm-session', SESSION],
            ['status', 'Bad_ID'],
            ['--bogus', 'merge', TRAIN_ID, '--pm-session', SESSION],
            ['close', TRAIN_ID, '--pm-session', SESSION, '-h'],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                self.assertInputRefused(argv)

    def test_other_task_commands_keep_the_generic_parser_error(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(['task', 'inspect', '--bogus'])
            except SystemExit as exc:
                code = exc.code
        self.assertEqual(code, 2)
        self.assertEqual(out.getvalue(), '')
        self.assertIn('invalid command or options', err.getvalue())
        self.assertNotIn(merge_train_cli.TRAIN_INPUT_INVALID, err.getvalue())
        self.assertEqual(self.freshness.calls, [])

    def test_six_members_are_accepted(self):
        records = {}
        argv = ['build']
        for index in range(6):
            task_id = format(index + 1, '032x')
            records[task_id] = {'task_id': task_id, 'pm_session_id': self.caller}
            argv += ['--member', task_id + ':' + str(index + 1)]
        self.store.records = records
        build = Recorder({'step': 'BUILT', 'codes': [], 'result': {'train_id': TRAIN_ID}})
        code, value, _ = self._main(argv + ['--pm-session', SESSION], build_call=build)
        self.assertEqual(code, 0)
        self.assertEqual(build.calls[0][0][0],
                         [(format(index + 1, '032x'), index + 1) for index in range(6)])

    # cli.py dispatch

    def test_cli_dispatch_reaches_run_task_train(self):
        self._create()
        code, value, runs = self._main(['status', TRAIN_ID])
        self.assertEqual(code, 0)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].action, 'train')
        self.assertEqual(value['step'], 'STATUS')
        # status changes nothing, so it does not need fresh source.
        self.assertEqual(self.freshness.calls, [])

    def test_fresh_source_is_checked_before_build_merge_and_close(self):
        self._create()
        build = Recorder({'step': 'BUILT', 'codes': [], 'result': {'train_id': TRAIN_ID}})
        merge = Recorder({'step': 'MERGED', 'codes': [], 'result': None})
        close = Recorder({'step': 'CLOSED', 'codes': [], 'result': None})
        cases = [['build', '--member', TASK_A + ':101', '--member', TASK_B + ':102',
                  '--pm-session', SESSION],
                 ['merge', TRAIN_ID, '--pm-session', SESSION],
                 ['close', TRAIN_ID, '--pm-session', SESSION]]
        for argv in cases:
            with self.subTest(argv=argv[0]):
                self.freshness.calls.clear()
                code, _, runs = self._main(argv, build_call=build, merge_call=merge,
                                           close_call=close)
                self.assertEqual(code, 0)
                self.assertEqual(len(runs), 1)
                self.assertEqual(self.freshness.calls, [((), {})])
        self.assertEqual((len(build.calls), len(merge.calls), len(close.calls)), (1, 1, 1))

    def test_stale_source_stops_build_merge_and_close_before_any_call(self):
        before = self._create()

        def stale():
            error = LLMToolError('LLMTOOL_SOURCE_STALE')
            error.completion_code = 'LLMTOOL_SOURCE_STALE'
            raise error

        self.freshness.side_effect = stale
        build, merge, close, advance = Recorder(), Recorder(), Recorder(), Recorder()
        adapters = self._adapters(build_call=build, merge_call=merge, close_call=close,
                                  advance_call=advance)
        original = merge_train_cli.run_task_train
        cases = [['build', '--member', TASK_A + ':101', '--member', TASK_B + ':102',
                  '--pm-session', SESSION],
                 ['merge', TRAIN_ID, '--pm-session', SESSION],
                 ['close', TRAIN_ID, '--pm-session', SESSION]]
        for argv in cases:
            with self.subTest(argv=argv[0]):
                runs = []

                def wrapped(args):
                    runs.append(args)
                    return original(args, **adapters)

                out, err = io.StringIO(), io.StringIO()
                with mock.patch.object(merge_train_cli, 'run_task_train', wrapped), \
                        contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = cli.main(['task', 'train', *argv])
                self.assertEqual(code, 1)
                self.assertEqual(runs, [])
                lines = out.getvalue().splitlines()
                self.assertEqual(len(lines), 1, out.getvalue())
                self.assertRefused(json.loads(lines[0]), 'LLMTOOL_SOURCE_STALE',
                                   None if argv[0] == 'build' else TRAIN_ID)
                self.assertEqual(err.getvalue(), '')
        self.assertEqual((build.calls, merge.calls, close.calls, advance.calls),
                         ([], [], [], []))
        self.assertEqual(self.store.calls, [])
        self.assertEqual(merge_train_store.get(TRAIN_ID, root=self.train_root), before)
        self.assertEqual(len(self.freshness.calls), 3)
        # status reads only and still runs from stale source.
        code, value, _ = self._main(['status', TRAIN_ID])
        self.assertEqual((code, value['step']), (0, 'STATUS'))
        self.assertEqual(len(self.freshness.calls), 3)

    # build

    def test_build_passes_members_in_order_with_caller(self):
        build = Recorder({'step': 'BUILT', 'codes': [], 'result': {'train_id': TRAIN_ID}})
        code, value, _ = self._main(['build', '--member', TASK_B + ':102',
                                     '--member', TASK_A + ':101', '--pm-session', SESSION],
                                    build_call=build)
        self.assertEqual(code, 0)
        self.assertEqual(value, {'train_id': TRAIN_ID, 'step': 'BUILT', 'codes': [],
                                 'result': {'train_id': TRAIN_ID}})
        self.assertEqual(len(build.calls), 1)
        args, kwargs = build.calls[0]
        # The (task_id, pr) pair shape merge_train_build.build_train accepts.
        self.assertEqual(args, ([(TASK_B, 102), (TASK_A, 101)],))
        self.assertIs(kwargs['store'], self.store)
        self.assertEqual(kwargs['caller_key'], self.caller)
        self.assertIs(kwargs['github_get'], self.github_get)
        self.assertIs(kwargs['git_runner'], self.git_runner)
        self.assertIs(kwargs['gh_runner'], self.gh_runner)
        self.assertEqual(kwargs['root'], self.root)
        self.assertEqual(kwargs['train_root'], self.train_root)
        self.assertEqual(self.store.calls, [TASK_B, TASK_A])

    def test_build_uses_environment_session(self):
        os.environ['CLAUDE_CODE_SESSION_ID'] = SESSION
        build = Recorder({'step': 'BUILT', 'codes': [], 'result': {'train_id': TRAIN_ID}})
        code, _, _ = self._main(['build', '--member', TASK_A + ':101',
                                 '--member', TASK_B + ':102'], build_call=build)
        self.assertEqual(code, 0)
        self.assertEqual(build.calls[0][1]['caller_key'], self.caller)

    def test_build_refuses_non_owner_before_any_call(self):
        build = Recorder({'step': 'BUILT', 'codes': [], 'result': {'train_id': TRAIN_ID}})
        code, value, _ = self._main(['build', '--member', TASK_A + ':101',
                                     '--member', TASK_C + ':103', '--pm-session', SESSION],
                                    build_call=build)
        self.assertEqual(code, 1)
        self.assertRefused(value, pm_caller.NOT_TASK_OWNER, task_id=TASK_C)
        self.assertEqual(build.calls, [])

    def test_build_refuses_unreadable_task_record(self):
        build = Recorder({'step': 'BUILT', 'codes': [], 'result': None})
        code, value, _ = self._main(['build', '--member', TASK_A + ':101',
                                     '--member', 'f' * 32 + ':104', '--pm-session', SESSION],
                                    build_call=build)
        self.assertEqual(code, 1)
        self.assertRefused(value, pm_caller.TASK_RECORD_UNAVAILABLE, task_id='f' * 32)
        self.assertEqual(build.calls, [])

    def test_build_refuses_missing_and_conflicting_session(self):
        build = Recorder({'step': 'BUILT', 'codes': [], 'result': None})
        argv = ['build', '--member', TASK_A + ':101', '--member', TASK_B + ':102']
        code, value, _ = self._main(argv, build_call=build)
        self.assertEqual(code, 1)
        self.assertRefused(value, pm_caller.SESSION_MISSING)
        os.environ['CLAUDE_CODE_SESSION_ID'] = OTHER_SESSION
        code, value, _ = self._main(argv + ['--pm-session', SESSION], build_call=build)
        self.assertEqual(code, 1)
        self.assertRefused(value, pm_caller.SESSION_CONFLICT)
        self.assertEqual(build.calls, [])
        self.assertEqual(self.store.calls, [])

    def test_missing_build_module_refuses_unavailable(self):
        package = merge_train_cli.__name__.rpartition('.')[0]
        with mock.patch.dict(sys.modules, {package + '.merge_train_build': None}):
            code, value, _ = self._main(['build', '--member', TASK_A + ':101',
                                         '--member', TASK_B + ':102',
                                         '--pm-session', SESSION])
        self.assertEqual(code, 1)
        self.assertRefused(value, merge_train_cli.TRAIN_BUILD_UNAVAILABLE)

    def test_build_default_module_and_adapters(self):
        build = Recorder({'step': 'BUILT', 'codes': [], 'result': {'train_id': TRAIN_ID}})
        module = types.ModuleType('merge_train_build')
        module.build_train = build
        package = merge_train_cli.__name__.rpartition('.')[0]
        with mock.patch.dict(sys.modules, {package + '.merge_train_build': module}):
            code, value = self._run(['build', '--member', TASK_A + ':101',
                                     '--member', TASK_B + ':102', '--pm-session', SESSION],
                                    store=self.store, train_root=self.train_root)
        self.assertEqual(code, 0)
        self.assertEqual(value['train_id'], TRAIN_ID)
        kwargs = build.calls[0][1]
        self.assertIs(kwargs['github_get'], task_completion._github_get)
        self.assertIs(kwargs['gh_runner'], task_advance._publish_run_gh)
        self.assertIs(kwargs['git_runner'], task_advance._publish_run_git)
        self.assertEqual(kwargs['root'], os.getcwd())

    # merge

    def test_merge_passes_train_id_after_checking_record_members(self):
        self._create()
        merge = Recorder({'step': 'MERGED', 'codes': [], 'result': {'merge_sha': 'f' * 40}})
        code, value, _ = self._main(['merge', TRAIN_ID, '--pm-session', SESSION],
                                    merge_call=merge)
        self.assertEqual(code, 0)
        self.assertEqual(value, {'train_id': TRAIN_ID, 'step': 'MERGED', 'codes': [],
                                 'result': {'merge_sha': 'f' * 40}})
        self.assertEqual(self.store.calls, [TASK_A, TASK_B])
        self.assertEqual(len(merge.calls), 1)
        args, kwargs = merge.calls[0]
        self.assertEqual(args, (TRAIN_ID,))
        self.assertEqual(kwargs, {'store': self.store, 'github_get': self.github_get,
                                  'gh_runner': self.gh_runner, 'root': self.root,
                                  'train_root': self.train_root})

    def test_merge_refuses_non_owner_and_changes_nothing(self):
        before = self._create()
        self.store.records[TASK_B]['pm_session_id'] = self.other
        merge = Recorder({'step': 'MERGED', 'codes': [], 'result': None})
        code, value, _ = self._main(['merge', TRAIN_ID, '--pm-session', SESSION],
                                    merge_call=merge)
        self.assertEqual(code, 1)
        self.assertRefused(value, pm_caller.NOT_TASK_OWNER, TRAIN_ID, TASK_B)
        self.assertEqual(merge.calls, [])
        self.assertEqual(merge_train_store.get(TRAIN_ID, root=self.train_root), before)

    def test_merge_refuses_missing_record(self):
        os.makedirs(self.train_dir, mode=0o700)
        merge = Recorder({'step': 'MERGED', 'codes': [], 'result': None})
        code, value, _ = self._main(['merge', TRAIN_ID, '--pm-session', SESSION],
                                    merge_call=merge)
        self.assertEqual(code, 1)
        self.assertRefused(value, merge_train_store.TRAIN_RECORD_UNAVAILABLE, TRAIN_ID)
        self.assertEqual(merge.calls, [])
        self.assertEqual(self.store.calls, [])

    def test_merge_refuses_missing_session_before_reading(self):
        self._create()
        merge = Recorder({'step': 'MERGED', 'codes': [], 'result': None})
        code, value, _ = self._main(['merge', TRAIN_ID], merge_call=merge)
        self.assertEqual(code, 1)
        self.assertRefused(value, pm_caller.SESSION_MISSING, TRAIN_ID)
        self.assertEqual(merge.calls, [])
        self.assertEqual(self.store.calls, [])

    def test_merge_default_is_merge_train_ops(self):
        from llmtool import merge_train_ops
        self._create()
        merge = Recorder({'step': 'WAITING_CI', 'codes': [], 'result': None})
        with mock.patch.object(merge_train_ops, 'merge_train', merge):
            code, value = self._run(['merge', TRAIN_ID, '--pm-session', SESSION],
                                    store=self.store, train_root=self.train_root)
        self.assertEqual(code, 0)
        self.assertEqual(value['step'], 'WAITING_CI')
        kwargs = merge.calls[0][1]
        self.assertIs(kwargs['github_get'], task_completion._github_get)
        self.assertIs(kwargs['gh_runner'], task_advance._publish_run_gh)
        self.assertEqual(kwargs['root'], os.getcwd())

    # close

    def _merged(self, task_ids):
        """Create a record for ``task_ids`` and move it BUILT -> MERGED."""
        built = self._create(task_ids=task_ids)
        attempt = {'pr': TRAIN_PR, 'head_sha': built['train_head'], 'state': 'CLAIMED',
                   'claimed_at': '2026-11-08T00:00:00Z'}
        claimed = merge_train_store.compare_and_update(
            TRAIN_ID, built['record_sha256'], root=self.train_root, state='MERGE_CLAIMED',
            merge_attempt=attempt)
        return merge_train_store.compare_and_update(
            TRAIN_ID, claimed['record_sha256'], root=self.train_root, state='MERGED',
            merge_sha='f' * 40, merge_attempt=dict(attempt, state='MERGED'))

    @staticmethod
    def _open_member_pr(path):
        """A fake GitHub read: every member PR is still open and not merged."""
        number = int(path.split('/')[1])
        return {'number': number, 'state': 'open', 'merged': False}

    @staticmethod
    def _member_pr_comment():
        return ('<!-- activity: type=task actor=PM target=PR status=TRACKING -->\n'
                f'Closed by llmtool merge train {TRAIN_ID}: this change was merged in '
                f'train PR #{TRAIN_PR} as squash commit {"f" * 40}.')

    def test_close_wiring_reports_a_failed_member_pr_close(self):
        self._merged((TASK_A, TASK_B))
        advance = Recorder({'step': 'CLOSED', 'codes': []})

        def runner(root, args):
            return types.SimpleNamespace(returncode=1 if args[2] == '101' else 0)

        gh_runner = Recorder(side_effect=runner)
        code, value, _ = self._main(['close', TRAIN_ID, '--pm-session', SESSION],
                                    advance_call=advance,
                                    github_get=Recorder(side_effect=self._open_member_pr),
                                    gh_runner=gh_runner)
        # The train is still CLOSED; the failed PR close is reported, not retried.
        self.assertEqual(code, 0)
        self.assertEqual(value, {
            'train_id': TRAIN_ID, 'step': 'CLOSED',
            'codes': [merge_train_ops.TRAIN_MEMBER_PR_CLOSE_FAILED],
            'result': {'train_id': TRAIN_ID, 'tasks': [TASK_A, TASK_B],
                       'closed_prs': [102], 'failed_prs': [101]}})
        self.assertEqual(len(gh_runner.calls), 2)
        self.assertEqual(merge_train_store.get(TRAIN_ID, root=self.train_root)['state'],
                         'CLOSED')

    def test_close_passes_train_id_and_owner_checked_advance(self):
        self._create(state='BUILT')
        advance = Recorder({'step': 'CLOSED', 'codes': []})

        def close_step(train_id, *, store, advance_call, github_get, gh_runner, root,
                       train_root):
            first = advance_call(task_id=TASK_A, pr_number=101, integration_pr=TRAIN_PR)
            second = advance_call(task_id=TASK_B, pr_number=102, integration_pr=TRAIN_PR)
            return {'step': 'CLOSED', 'codes': [],
                    'result': {'first': first['step'], 'second': second['step']}}

        close = Recorder(side_effect=close_step)
        code, value, _ = self._main(['close', TRAIN_ID, '--pm-session', SESSION],
                                    close_call=close, advance_call=advance)
        self.assertEqual(code, 0)
        self.assertEqual(value, {'train_id': TRAIN_ID, 'step': 'CLOSED', 'codes': [],
                                 'result': {'first': 'CLOSED', 'second': 'CLOSED'}})
        args, kwargs = close.calls[0]
        self.assertEqual(args, (TRAIN_ID,))
        self.assertIs(kwargs['store'], self.store)
        self.assertEqual(kwargs['train_root'], self.train_root)
        # The close step gets the same GitHub reader, gh runner and root.
        self.assertIs(kwargs['github_get'], self.github_get)
        self.assertIs(kwargs['gh_runner'], self.gh_runner)
        self.assertEqual(kwargs['root'], self.root)
        self.assertEqual(advance.calls, [
            ((), {'task_id': TASK_A, 'pr_number': 101, 'integration_pr': TRAIN_PR}),
            ((), {'task_id': TASK_B, 'pr_number': 102, 'integration_pr': TRAIN_PR})])

    def test_close_stops_at_once_when_a_member_owner_changes_between_steps(self):
        before = self._merged((TASK_A, TASK_B, TASK_D))

        def advance_step(**kwargs):
            # The second member is adopted away after the first member closes.
            self.store.records[TASK_B]['pm_session_id'] = self.other
            return {'step': 'CLOSED', 'codes': []}

        advance = Recorder(side_effect=advance_step)
        # The real close_train treats an Exception from one member advance as an
        # open member and goes on; the refusal must still stop the whole close.
        code, value, _ = self._main(['close', TRAIN_ID, '--pm-session', SESSION],
                                    advance_call=advance)
        self.assertEqual(code, 1)
        self.assertRefused(value, pm_caller.NOT_TASK_OWNER, TRAIN_ID, TASK_B)
        self.assertEqual([call[1]['task_id'] for call in advance.calls], [TASK_A])
        self.assertEqual(merge_train_store.get(TRAIN_ID, root=self.train_root), before)

    def test_close_refusal_holds_when_the_step_swallows_it(self):
        self._create()

        def swallowing(train_id, *, store, advance_call, github_get, gh_runner, root,
                       train_root):
            self.store.records[TASK_A]['pm_session_id'] = self.other
            for task_id in (TASK_A, TASK_B):
                try:
                    advance_call(task_id=task_id, pr_number=101, integration_pr=TRAIN_PR)
                except BaseException:
                    pass
            return {'step': 'CLOSED', 'codes': [], 'result': None}

        advance = Recorder({'step': 'CLOSED', 'codes': []})
        code, value, _ = self._main(['close', TRAIN_ID, '--pm-session', SESSION],
                                    close_call=Recorder(side_effect=swallowing),
                                    advance_call=advance)
        self.assertEqual(code, 1)
        self.assertRefused(value, pm_caller.NOT_TASK_OWNER, TRAIN_ID, TASK_A)
        self.assertEqual(advance.calls, [])

    def test_close_refuses_non_owner_before_any_call(self):
        self._create()
        self.store.records[TASK_A]['pm_session_id'] = self.other
        close = Recorder({'step': 'CLOSED', 'codes': [], 'result': None})
        advance = Recorder({'step': 'CLOSED', 'codes': []})
        code, value, _ = self._main(['close', TRAIN_ID, '--pm-session', SESSION],
                                    close_call=close, advance_call=advance)
        self.assertEqual(code, 1)
        self.assertRefused(value, pm_caller.NOT_TASK_OWNER, TRAIN_ID, TASK_A)
        self.assertEqual(close.calls, [])
        self.assertEqual(advance.calls, [])

    def test_close_default_binds_store_into_task_advance(self):
        from llmtool import merge_train_ops
        self._create()
        advance = Recorder({'step': 'CLOSED', 'codes': []})

        def close_step(train_id, *, store, advance_call, github_get, gh_runner, root,
                       train_root):
            outcome = advance_call(task_id=TASK_A, pr_number=101, integration_pr=TRAIN_PR)
            return {'step': 'CLOSED', 'codes': [], 'result': {'first': outcome['step']}}

        close = Recorder(side_effect=close_step)
        with mock.patch.object(merge_train_ops, 'close_train', close), \
                mock.patch.object(task_advance, 'advance', advance):
            code, value = self._run(['close', TRAIN_ID, '--pm-session', SESSION],
                                    store=self.store, train_root=self.train_root)
        self.assertEqual(code, 0)
        self.assertEqual(value['result'], {'first': 'CLOSED'})
        close_kwargs = close.calls[0][1]
        self.assertIs(close_kwargs['github_get'], task_completion._github_get)
        self.assertIs(close_kwargs['gh_runner'], task_advance._publish_run_gh)
        self.assertEqual(close_kwargs['root'], os.getcwd())
        kwargs = advance.calls[0][1]
        self.assertIs(kwargs['store'], self.store)
        self.assertEqual((kwargs['task_id'], kwargs['pr_number'], kwargs['integration_pr']),
                         (TASK_A, 101, TRAIN_PR))

    # status

    def test_status_reads_only(self):
        before = self._create()
        build, merge, close = Recorder(), Recorder(), Recorder()
        code, value, _ = self._main(['status', TRAIN_ID], build_call=build,
                                    merge_call=merge, close_call=close)
        self.assertEqual(code, 0)
        self.assertEqual(value['train_id'], TRAIN_ID)
        self.assertEqual(value['step'], 'STATUS')
        self.assertEqual(value['codes'], [])
        self.assertEqual(value['result'], {field: before[field] for field in (
            'train_id', 'state', 'main_sha', 'train_head', 'train_pr', 'members',
            'excluded', 'merge_sha')})
        self.assertEqual((build.calls, merge.calls, close.calls), ([], [], []))
        self.assertEqual(self.store.calls, [])
        self.assertEqual(merge_train_store.get(TRAIN_ID, root=self.train_root), before)

    def test_status_of_missing_train_creates_nothing(self):
        code, value, _ = self._main(['status', TRAIN_ID])
        self.assertEqual(code, 1)
        self.assertRefused(value, merge_train_store.TRAIN_RECORD_UNAVAILABLE, TRAIN_ID)
        self.assertFalse(os.path.lexists(self.train_dir))
        # A missing parent of the record directory is not created either.
        nested = os.path.join(self.base, 'absent', 'tasks')
        code, value, _ = self._main(['status', TRAIN_ID], train_root=nested)
        self.assertEqual(code, 1)
        self.assertRefused(value, merge_train_store.TRAIN_RECORD_UNAVAILABLE, TRAIN_ID)
        self.assertFalse(os.path.lexists(os.path.join(self.base, 'absent')))

    def test_status_of_unknown_train_id_refuses_unavailable(self):
        self._create()
        code, value, _ = self._main(['status', 'train-0002'])
        self.assertEqual(code, 1)
        self.assertRefused(value, merge_train_store.TRAIN_RECORD_UNAVAILABLE, 'train-0002')

    def test_status_leaves_directory_mode_and_existence_unchanged(self):
        before = self._create()
        mode = os.stat(self.train_dir).st_mode
        # status never goes through the create-or-repair directory open.
        with mock.patch.object(merge_train_store, '_open_directory', Forbidden()):
            code, value, _ = self._main(['status', TRAIN_ID])
        self.assertEqual((code, value['step']), (0, 'STATUS'))
        self.assertEqual(os.stat(self.train_dir).st_mode, mode)
        # A directory with another mode is refused, not repaired: a get() would
        # chmod it back to 0o700.
        os.chmod(self.train_dir, 0o750)
        self.addCleanup(os.chmod, self.train_dir, 0o700)
        code, value, _ = self._main(['status', TRAIN_ID])
        self.assertEqual(code, 1)
        self.assertRefused(value, merge_train_store.TRAIN_RECORD_INVALID, TRAIN_ID)
        self.assertEqual(os.stat(self.train_dir).st_mode & 0o7777, 0o750)
        self.assertTrue(os.path.isdir(self.train_dir))
        os.chmod(self.train_dir, 0o700)
        self.assertEqual(merge_train_store.get(TRAIN_ID, root=self.train_root), before)

    # Wiring: the real library steps, with only readers and runners faked

    @staticmethod
    def _commands(runner):
        """The argument lists a fake git or gh runner was called with."""
        return [args[1] for args, _ in runner.calls]

    def _task_store(self, task_ids):
        """A real TaskStore under the temporary root, built as test_pm_caller.py
        setUp does, holding one caller-owned Task record per id."""
        home = Path(self.base).resolve()
        os.environ['HOME'] = str(home)
        store = TaskStore(home / 'tasks')
        request = 'Bounded non-sensitive merge train build task.'
        for index, task_id in enumerate(task_ids):
            task = task_submission_cli._record(
                task_id, format(index + 1, 'x') * 32, self.caller, 'pm-profile', request,
                state='PM_RESPONDED', provider_type='openai-compatible', model='pm-model',
                backend='NO_NATIVE_SESSION_ID_EXPOSED',
                request_sha=hashlib.sha256(request.encode()).hexdigest())
            task.update(pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                                          'base_url': None},
                        response='PM returned a bounded task.', error_code=None,
                        pm_assignment={'schema_version': 1, 'route': 'codex', 'risk': 'LOW',
                                       'goal': 'Build one merge train.',
                                       'acceptance': ['the caller owns the Task']})
            store.create(task)
            self.assertEqual(store.get(task_id)['pm_session_id'], self.caller)
        return store

    def test_build_wiring_runs_the_real_build_train(self):
        # build_call is not injected: the real merge_train_build.build_train runs
        # against a real TaskStore. The Task records have no PM_EVIDENCE_READY
        # completion, so the real eligibility gate excludes each member; the
        # repository root does not exist, so the candidate build reads no Git
        # object and nothing is built.
        self.assertFalse(os.path.lexists(self.root))
        store = self._task_store((TASK_A, TASK_B))
        # Log every read, then go through the real TaskStore read path.
        reads, real_get = [], store.get

        def logged_get(task_id):
            reads.append(task_id)
            return real_get(task_id)

        store.get = logged_get
        train_root = str(store.root)
        # The train records live beside the TaskStore root: the same self.train_dir.
        self.assertEqual(os.path.realpath(train_root), os.path.realpath(self.train_root))
        main_read = Recorder('c' * 40)
        github_get, git_runner, gh_runner = Recorder(), Recorder(), Recorder()
        with mock.patch.object(task_completion, 'read_authenticated_main_sha', main_read):
            code, value, _ = self._main(['build', '--member', TASK_B + ':102',
                                         '--member', TASK_A + ':101', '--pm-session', SESSION],
                                        store=store, train_root=train_root,
                                        github_get=github_get, git_runner=git_runner,
                                        gh_runner=gh_runner)
        self.assertEqual(code, 1)
        self.assertEqual(value['step'], 'REFUSED')
        self.assertEqual(len(value['codes']), 1)
        self.assertNotEqual(value['codes'], [merge_train.TRAIN_MEMBERS_INVALID])
        self.assertNotIn('train_id', value)
        not_ready = merge_train_eligibility.TRAIN_MEMBER_NOT_READY
        # The eligibility gate saw both members, in the given order.
        self.assertEqual(value['result']['excluded'], [
            {'task_id': TASK_B, 'pr': 102, 'code': not_ready},
            {'task_id': TASK_A, 'pr': 101, 'code': not_ready}])
        self.assertEqual(len(main_read.calls), 1)
        # CLI owner check, build_train owner check, then eligibility: each in order,
        # each a read of the real stored record.
        self.assertEqual(reads, [TASK_B, TASK_A] * 3)
        self.assertEqual(self.store.calls, [])
        # Nothing pushed, no PR created, no train recorded.
        self.assertFalse(any(args[:1] == ['push'] for args in self._commands(git_runner)))
        self.assertFalse(any(args[:2] == ['pr', 'create']
                             for args in self._commands(gh_runner)))
        self.assertEqual((git_runner.calls, gh_runner.calls, github_get.calls), ([], [], []))
        self.assertFalse(os.path.lexists(self.train_dir))

    def test_merge_wiring_runs_the_real_merge_train(self):
        # merge_call is not injected: the real merge_train_ops.merge_train runs.
        before = self._create()
        current_main = 'e' * 40
        self.assertNotEqual(current_main, before['main_sha'])
        main_read = Recorder(current_main)
        github_get, gh_runner = Recorder(), Recorder()
        with mock.patch.object(task_completion, 'read_authenticated_main_sha', main_read):
            code, value, _ = self._main(['merge', TRAIN_ID, '--pm-session', SESSION],
                                        github_get=github_get, gh_runner=gh_runner)
        self.assertEqual(code, 0)
        self.assertEqual(value, {
            'train_id': TRAIN_ID, 'step': 'SUPERSEDED',
            'codes': [merge_train_ops.TRAIN_MAIN_MOVED],
            'result': {'train_id': TRAIN_ID, 'main_sha': before['main_sha'],
                       'current_main': current_main}})
        self.assertEqual(len(main_read.calls), 1)
        self.assertEqual(merge_train_store.get(TRAIN_ID, root=self.train_root)['state'],
                         'SUPERSEDED')
        self.assertFalse(any(args[:2] == ['pr', 'merge'] for args in self._commands(gh_runner)))
        self.assertEqual(gh_runner.calls, [])
        self.assertEqual(self.store.calls, [TASK_A, TASK_B])

    def test_close_wiring_runs_the_real_close_train(self):
        # close_call is not injected: the real merge_train_ops.close_train runs.
        # Record order differs from task-id order.
        self._merged((TASK_B, TASK_A))

        def advance_step(**kwargs):
            # Log into the store's read log, so the owner re-check order shows.
            self.store.calls.append(('advance', kwargs['task_id']))
            return {'step': 'CLOSED', 'codes': []}

        advance = Recorder(side_effect=advance_step)
        # After the record is CLOSED the real step reads and closes each open member PR.
        github_get = Recorder(side_effect=self._open_member_pr)
        gh_runner = Recorder(types.SimpleNamespace(returncode=0))
        code, value, _ = self._main(['close', TRAIN_ID, '--pm-session', SESSION],
                                    advance_call=advance, github_get=github_get,
                                    gh_runner=gh_runner)
        self.assertEqual(code, 0)
        self.assertEqual(value, {'train_id': TRAIN_ID, 'step': 'CLOSED', 'codes': [],
                                 'result': {'train_id': TRAIN_ID, 'tasks': [TASK_B, TASK_A],
                                            'closed_prs': [101, 102], 'failed_prs': []}})
        self.assertEqual([args[0] for args, _ in github_get.calls], ['pulls/101', 'pulls/102'])
        self.assertEqual(gh_runner.calls, [
            ((self.root, ['pr', 'close', str(pr), '--comment', self._member_pr_comment()]), {})
            for pr in (101, 102)])
        self.assertEqual(merge_train_store.get(TRAIN_ID, root=self.train_root)['state'],
                         'CLOSED')
        self.assertEqual(advance.calls, [
            ((), {'task_id': TASK_B, 'pr_number': 101, 'integration_pr': TRAIN_PR}),
            ((), {'task_id': TASK_A, 'pr_number': 102, 'integration_pr': TRAIN_PR})])
        # CLI owner checks first, then a re-check right before each member advance.
        self.assertEqual(self.store.calls, [TASK_B, TASK_A,
                                            TASK_B, ('advance', TASK_B),
                                            TASK_A, ('advance', TASK_A)])

    def test_close_wiring_keeps_the_train_merged_while_a_member_is_open(self):
        before = self._merged((TASK_A, TASK_B))

        def advance_step(**kwargs):
            if kwargs['task_id'] == TASK_A:
                return {'step': 'WAITING_CI', 'codes': []}
            return {'step': 'CLOSED', 'codes': []}

        advance = Recorder(side_effect=advance_step)
        code, value, _ = self._main(['close', TRAIN_ID, '--pm-session', SESSION],
                                    advance_call=advance)
        self.assertEqual(code, 1)
        self.assertEqual(value, {'train_id': TRAIN_ID, 'step': 'WAITING_MEMBERS',
                                 'codes': [merge_train_ops.TRAIN_MEMBERS_OPEN],
                                 'result': {'train_id': TRAIN_ID, 'open': [TASK_A]}})
        self.assertEqual([call[1]['task_id'] for call in advance.calls], [TASK_A, TASK_B])
        stored = merge_train_store.get(TRAIN_ID, root=self.train_root)
        self.assertEqual(stored['state'], 'MERGED')
        self.assertEqual(stored, before)

    # Output and exit codes

    def test_exit_code_follows_step(self):
        self._create()
        expected = {'BUILT': 0, 'MERGED': 0, 'CLOSED': 0, 'SUPERSEDED': 0, 'WAITING_CI': 0,
                    'MERGE_REFUSED': 1, 'CLOSE_REFUSED': 1, 'WAITING_MEMBERS': 1}
        for step, exit_code in expected.items():
            with self.subTest(step=step):
                merge = Recorder({'step': step, 'codes': [], 'result': None})
                code, value, _ = self._main(['merge', TRAIN_ID, '--pm-session', SESSION],
                                            merge_call=merge)
                self.assertEqual(code, exit_code)
                self.assertEqual(value['step'], step)

    def test_step_output_is_suppressed(self):
        self._create()

        def noisy(train_id, **kwargs):
            print('noise from the step')
            return {'step': 'MERGED', 'codes': ['TRAIN_PROVENANCE_COMMENT_FAILED'],
                    'result': None}

        code, value, _ = self._main(['merge', TRAIN_ID, '--pm-session', SESSION],
                                    merge_call=Recorder(side_effect=noisy))
        self.assertEqual(code, 0)
        self.assertEqual(value['codes'], ['TRAIN_PROVENANCE_COMMENT_FAILED'])

    def test_failing_or_malformed_step_refuses(self):
        self._create()

        def explode(train_id, **kwargs):
            raise RuntimeError('secret detail')

        for merge in (Recorder(side_effect=explode), Recorder(None),
                      Recorder({'codes': []}), Recorder({'step': 'MERGED', 'codes': 'X'}),
                      Recorder({'step': 'MERGED', 'codes': [], 'result': object()})):
            with self.subTest(result=merge.result):
                code, value, _ = self._main(['merge', TRAIN_ID, '--pm-session', SESSION],
                                            merge_call=merge)
                self.assertEqual(code, 1)
                self.assertRefused(value, merge_train_cli.TRAIN_STEP_FAILED, TRAIN_ID)
                self.assertNotIn('secret', json.dumps(value))


if __name__ == '__main__':
    unittest.main()
