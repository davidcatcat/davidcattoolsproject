"""Public S1a file/exit/privacy tests; all fixture mutations occur outside inspect."""

import copy
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import replace
from unittest.mock import patch

from llmtool.cli import main
from llmtool import task_lifecycle as t
from llmtool import task_lifecycle_cli as adapter
from llmtool.errors import LLMToolError
from test_task_lifecycle import fixture, wire_events, NOW
from test_action_contract_cli import ForbiddenEnvironment, ForbiddenStdin


class TaskCLITests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='s1a-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.contract, self.expect, self.events = fixture()
        self.contract_file = self.write('contract.json', self.contract.to_json())
        self.expect_file = self.write('expect.json', self.expect.to_json())
        self.events_file = self.write('events.jsonl', wire_events(self.events))

    def write(self, name, text):
        path = self.root/name
        path.write_bytes(text.encode('utf-8') if type(text) is str else text)
        return path

    def args(self):
        return ['task', 'inspect', '--contract', str(self.contract_file), '--expect', str(self.expect_file),
                '--events', str(self.events_file), '--observed-at', NOW, '--json']

    def snapshot(self):
        return {str(p.relative_to(self.root)): (p.lstat().st_mode, p.read_bytes())
                for p in self.root.rglob('*') if p.is_file() and not p.is_symlink()}

    def invoke(self, args=None, guards=True):
        before = self.snapshot()
        stdout, stderr = io.StringIO(), io.StringIO()
        original_open = os.open
        opened = []
        def read_only_open(path, flags, *values, **kwargs):
            self.assertFalse(flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND))
            self.assertTrue(flags & os.O_NOFOLLOW)
            if not flags & os.O_DIRECTORY:
                opened.append(str(path))
                self.assertIn(str(path), ('contract.json', 'expect.json', 'events.jsonl'))
            return original_open(path, flags, *values, **kwargs)
        with ExitStack() as stack:
            stack.enter_context(redirect_stdout(stdout)); stack.enter_context(redirect_stderr(stderr))
            if guards:
                stack.enter_context(patch('sys.stdin', ForbiddenStdin()))
                stack.enter_context(patch('os.environ', ForbiddenEnvironment()))
                for target in ('llmtool.cli.resolve_config', 'llmtool.config.resolve_config',
                               'llmtool.cli.api_key', 'llmtool.config.api_key',
                               'llmtool.cli.user_config_path', 'llmtool.config.user_config_path',
                               'llmtool.cli.create_client', 'llmtool.providers.create_client',
                               'llmtool.cli.collect_git_snapshot', 'subprocess.Popen', 'subprocess.run',
                               'os.system', 'os.spawnv', 'socket.socket',
                               'urllib.request.OpenerDirector.open', 'builtins.open',
                               'os.write', 'os.mkdir', 'os.makedirs', 'os.unlink', 'os.remove',
                               'os.rmdir', 'os.rename', 'os.replace', 'os.link', 'os.symlink',
                               'os.chmod', 'os.chown', 'os.truncate', 'time.time'):
                    stack.enter_context(patch(target, side_effect=AssertionError('effect tripwire')))
                stack.enter_context(patch('os.open', side_effect=read_only_open))
            result = main(self.args() if args is None else args)
        self.assertEqual(before, self.snapshot())
        output, error = stdout.getvalue(), stderr.getvalue()
        for marker in ('SECRET', 'Traceback', str(self.root), '/repo', 'writer-session'):
            self.assertNotIn(marker, output+error)
        if output:
            data = json.loads(output)
            self.assertFalse(data['authorized'])
            self.assertEqual(data['runtime_evidence_status'], 'NOT_VERIFIED')
        return result, output, error, opened

    def test_success_reads_expect_before_events_and_no_effects(self):
        result, output, error, opened = self.invoke()
        self.assertEqual((result, error), (0, ''))
        self.assertEqual(opened, ['expect.json', 'contract.json', 'events.jsonl'])
        self.assertEqual(json.loads(output)['structural_validation'], 'SUCCESS')
        self.assertEqual(json.loads(output)['reported_state'], 'COMPLETED')
        self.assertLess(len(output.encode('utf-8')), t.OUTPUT_LIMIT)

    def test_required_expect_observation_json_and_unknown_cli_use_exit2(self):
        for option in ('--expect', '--observed-at', '--contract', '--events', '--json'):
            args = self.args(); index = args.index(option)
            del args[index:index+(1 if option == '--json' else 2)]
            with self.subTest(option=option), self.assertRaises(SystemExit) as raised:
                self.invoke(args)
            self.assertEqual(raised.exception.code, 2)
        with self.assertRaises(SystemExit) as raised:
            self.invoke(self.args()+['--provider', 'SECRET'])
        self.assertEqual(raised.exception.code, 2)

    def test_self_consistent_malicious_event_file_cannot_override_expect(self):
        raw = [e.to_dict() for e in self.events]
        for e in raw:
            e['epoch'] = 8; e['source_sha'] = '2'*40
            e['task']['run_id'] = 'b'*32
            e['agent']['root'] = '/attacker/root'
        self.events_file.write_text('\n'.join(json.dumps(e) for e in raw))
        code, output, _, _ = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output)['code'], 'TASK_BINDING_MISMATCH')

    def test_expect_same_file_inode_alias_rejects_before_event_parsing(self):
        args = self.args(); args[args.index('--events')+1] = str(self.expect_file)
        with patch('llmtool.task_lifecycle_cli.parse_events', side_effect=AssertionError('alias parsed')):
            code, output, _, _ = self.invoke(args)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output)['code'], 'TASK_EXPECT_EVENTS_ALIAS')

    def test_authorship_missing_expired_and_false_completion_are_exit1(self):
        self.expect_file.write_text(replace(self.expect, authored_by=None).to_json())
        code, output, _, _ = self.invoke()
        self.assertEqual(code, 1)
        self.assertIn('AUTHORSHIP_INCOMPLETE', json.loads(output)['findings'])
        self.expect_file.write_text(self.expect.to_json())
        args = self.args(); args[args.index('--observed-at')+1] = '2026-10-12T12:05:04Z'
        code, output, _, _ = self.invoke(args)
        self.assertEqual((code, json.loads(output)['structural_validation']), (1, 'STALE'))
        self.events_file.write_text(wire_events(self.events[:-1]+(replace(self.events[-1], evidence=()),)))
        code, output, _, _ = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output)['reported_state'], 'COMPLETED')
        self.assertEqual(json.loads(output)['missing_evidence'], ['review'])

    def test_unknown_missing_duplicate_fields_invalid_types_and_secret_bytes(self):
        original = self.expect.to_dict()
        variants = []
        for key in ('task', 'agents', 'authored_by', 'epoch'):
            raw = copy.deepcopy(original); del raw[key]; variants.append(json.dumps(raw))
        raw = copy.deepcopy(original); raw['heartbeat_ttl_seconds'] = True; variants.append(json.dumps(raw))
        raw = copy.deepcopy(original); raw['agent_override'] = 'SECRET'; variants.append(json.dumps(raw))
        variants += ['{"epoch":7,"epoch":8}', '{', '[]', 'NaN', b'\xff',
                     ' '* (t.FILE_LIMIT+1)]
        for raw in variants:
            self.expect_file.write_bytes(raw if type(raw) is bytes else raw.encode('utf-8'))
            with self.subTest(raw=str(raw)[:30]):
                code, output, _, opened = self.invoke()
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(output)['structural_validation'], 'INVALID')
                self.assertEqual(opened, ['expect.json'])
        self.expect_file.write_text(self.expect.to_json())
        for raw in ('{', 'NaN', '\n', '{"secret":"SECRET"}', b'\xff',
                    ' '* (t.EVENT_LIMIT+1), wire_events(self.events)*26):
            self.events_file.write_bytes(raw if type(raw) is bytes else raw.encode('utf-8'))
            with self.subTest(events=str(raw)[:30]):
                self.assertEqual(self.invoke()[0], 2)

    def test_leaf_ancestor_symlink_fifo_directory_and_hardlink_reject(self):
        leaf = self.root/'linked.json'; leaf.symlink_to(self.expect_file)
        parent = self.root/'linked-parent'; parent.symlink_to(self.root, target_is_directory=True)
        fifo = self.root/'pipe.json'; os.mkfifo(fifo)
        for filename in (leaf, parent/'expect.json', fifo, self.root):
            args = self.args(); args[args.index('--expect')+1] = str(filename)
            with self.subTest(filename=filename.name):
                self.assertEqual(self.invoke(args, guards=False)[0], 2)
        hard = self.root/'hard.json'; os.link(self.expect_file, hard)
        self.assertEqual(self.invoke(guards=False)[0], 2)

    def test_input_spelling_path_escape_and_unsafe_controls_reject(self):
        for filename in (str(self.root/'x'/'..'/'expect.json'), '/dev/null', '/proc/self/environ',
                         'SECRET\n.json', str(self.expect_file)+'\u202e'):
            args = self.args(); args[args.index('--expect')+1] = filename
            with self.subTest(filename=filename):
                self.assertEqual(self.invoke(args, guards=False)[0], 2)

    def test_changed_leaf_rejected_and_no_descriptor_leak(self):
        replacement = self.write('replacement.json', self.expect.to_json())
        original_stat = os.stat
        changed = []
        def replace_before_stat(path, *args, **kwargs):
            if str(path) == 'expect.json' and not changed:
                os.replace(replacement, self.expect_file); changed.append(True)
            return original_stat(path, *args, **kwargs)
        with patch('os.stat', side_effect=replace_before_stat), self.assertRaises(LLMToolError):
            adapter.read_task_file(str(self.expect_file))
        self.assertTrue(changed)
        # Repeated failures must close all owned descriptors, including parents.
        original_open, original_close = os.open, os.close
        active = set()
        def remember_open(*args, **kwargs):
            fd = original_open(*args, **kwargs); active.add(fd); return fd
        def remember_close(fd):
            active.discard(fd); return original_close(fd)
        # A symlink fails before fdopen; every directory FD remains observable.
        link = self.root/'bad.json'; link.symlink_to(self.expect_file)
        with patch('os.open', side_effect=remember_open), patch('os.close', side_effect=remember_close):
            for _ in range(20):
                with self.assertRaises(OSError):
                    adapter.read_task_file(str(link))
                self.assertEqual(active, set())

    def test_changed_contents_with_stable_metadata_rejected_by_double_read(self):
        original_fdopen = os.fdopen
        class ChangingRead:
            def __init__(self, stream):
                self.stream = stream; self.reads = 0
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def read(self, size):
                self.reads += 1
                value = self.stream.read(size)
                return value if self.reads == 1 else value+b' '
            def seek(self, offset):
                return self.stream.seek(offset)
            def fileno(self):
                return self.stream.fileno()
        with patch('os.fdopen', side_effect=lambda fd, mode: ChangingRead(original_fdopen(fd, mode))), \
                self.assertRaises(LLMToolError):
            adapter.read_task_file(str(self.expect_file))

    def test_broken_output_preserves_incomplete_exit_without_sink_write(self):
        class Broken:
            def write(self, value):
                raise BrokenPipeError()
        self.events_file.write_text('')
        with patch('sys.stdout', Broken()), patch('os.open', wraps=os.open) as opened:
            self.assertEqual(main(self.args()), 1)
        self.assertTrue(all(call.args[0] != '/dev/null' for call in opened.call_args_list))


if __name__ == '__main__':
    unittest.main()
