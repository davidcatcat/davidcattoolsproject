"""Public offline CLI security/failure journeys; runtime writes are fixture setup."""

import copy
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from unittest.mock import patch

from llmtool.cli import main
from llmtool import action_contract as c
from llmtool import action_contract_cli as adapter
from llmtool.errors import LLMToolError
import test_action_contract as fixtures


class ForbiddenStdin:
    def read(self, *args):
        raise AssertionError('stdin read')
    def fileno(self):
        raise AssertionError('stdin probed')


class ForbiddenEnvironment(dict):
    # argparse/gettext and terminal sizing consult these stdlib display settings.
    # No product location, configuration or credential variable may be read.
    display_keys = ('LANGUAGE', 'LC_ALL', 'LC_MESSAGES', 'LANG', 'COLUMNS', 'LINES')
    def __getitem__(self, key):
        if key in self.display_keys:
            raise KeyError(key)
        raise AssertionError('environment value read')
    def get(self, key, default=None):
        if key in self.display_keys:
            return default
        raise AssertionError('environment value read')


class ContractCLITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='a11b1-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root/'home'; self.home.mkdir()
        self.config = self.home/'.config'/'llmtool'; self.config.mkdir(parents=True)
        (self.config/'config.yaml').write_text('PRIVATE_INVALID_CONFIG')
        self.state_home = self.root/'absent-store'
        self.action = fixtures.make_action()
        self.approval = fixtures.make_approval(self.action)
        self.action_file = self.write('action.json', self.action.to_json().encode())
        self.approval_file = self.write('approval.json', self.approval.to_json().encode())
        self.state_file = self.write('state.json', self.action.expected_state.to_json().encode())

    def write(self, name, raw):
        path = self.root/name
        path.write_bytes(raw)
        return path

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file() and not p.is_symlink()}

    def approval_args(self, *extra):
        return ['approval','validate','--action',str(self.action_file),
                '--approval',str(self.approval_file),'--state',str(self.state_file),
                '--nonce','8'*64,'--now','150',*extra]

    def invoke(self, args, tripwires=True):
        before = self.snapshot()
        output, error = io.StringIO(), io.StringIO()
        opens = []
        original_open, original_stat = os.open, os.stat
        permitted_names = {p.name for p in (self.action_file, self.approval_file, self.state_file)}
        # These tripwires execute through cli.main, including adapter input reads.
        def bounded_open(path, flags, *values, **kwargs):
            self.assertEqual(flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND), 0)
            self.assertTrue(flags & os.O_NOFOLLOW)
            opens.append((str(path), flags))
            if not flags & os.O_DIRECTORY:
                self.assertIn(str(path), permitted_names)
            return original_open(path, flags, *values, **kwargs)
        def bounded_stat(path, *values, **kwargs):
            self.assertIn(str(path), permitted_names)
            self.assertFalse(kwargs.get('follow_symlinks', True))
            return original_stat(path, *values, **kwargs)
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {'HOME':str(self.home),
                'LLMTOOL_HOME':str(self.state_home),'API_KEY':'PRIVATE_SECRET'}, clear=True))
            stack.enter_context(patch('llmtool.cli.sys.stdin', ForbiddenStdin()))
            stack.enter_context(redirect_stdout(output)); stack.enter_context(redirect_stderr(error))
            if tripwires:
                for target in ('llmtool.cli.resolve_config', 'llmtool.config.resolve_config',
                    'llmtool.cli.api_key', 'llmtool.config.api_key',
                    'llmtool.cli.user_config_path', 'llmtool.config.user_config_path',
                    'llmtool.cli.create_client', 'llmtool.providers.create_client',
                    'llmtool.cli.collect_git_snapshot', 'subprocess.Popen', 'subprocess.run',
                    'os.system', 'socket.socket', 'urllib.request.OpenerDirector.open',
                    'builtins.open', 'os.write', 'os.mkdir', 'os.makedirs', 'os.unlink',
                    'os.remove', 'os.rmdir', 'os.rename', 'os.replace', 'os.link',
                    'os.symlink', 'os.chmod', 'os.chown', 'os.truncate'):
                    stack.enter_context(patch(target, side_effect=AssertionError('tripwire')))
                stack.enter_context(patch('os.open', side_effect=bounded_open))
                stack.enter_context(patch('os.stat', side_effect=bounded_stat))
                stack.enter_context(patch('os.environ', ForbiddenEnvironment()))
            result = main(args)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.state_home.exists())
        for marker in ('PRIVATE', 'Traceback', str(self.root), self.action.repository.canonical_root,
                       self.approval.nonce):
            self.assertNotIn(marker, output.getvalue()+error.getvalue())
        return result, output.getvalue(), error.getvalue(), opens

    def test_action_validation_all_kinds_zero_config_provider_network_git_or_writes(self):
        for kind in c.ActionKind:
            self.action_file.write_text(fixtures.make_action(kind).to_json())
            result, output, error, opens = self.invoke(['action','validate','--file',str(self.action_file),'--json'])
            data = json.loads(output)
            self.assertEqual((result,error), (0,''))
            self.assertEqual(data['status'], 'PROPOSED')
            self.assertFalse(data['authorized'])
            self.assertEqual(data['kind'], kind.value)
            self.assertEqual(data['audit']['event_type'], 'PROPOSED')
            self.assertEqual([name for name, flags in opens if not flags & os.O_DIRECTORY], ['action.json'])

    def test_user_json_receipt_cli_binding_pass_is_never_authorized(self):
        result, output, error, opens = self.invoke(self.approval_args('--json'))
        data = json.loads(output)
        self.assertEqual((result,error), (0,''))
        self.assertEqual(data['status'], 'NOT_AUTHORIZED')
        self.assertEqual(data['state_checked'], 'SUPPLIED_ONLY')
        self.assertTrue(data['bindings_valid'])
        self.assertFalse(data['trusted_user_source'])
        self.assertFalse(data['authorized'])
        self.assertEqual(data['audit']['event_type'], 'PROPOSED')
        self.assertEqual([name for name, flags in opens if not flags & os.O_DIRECTORY],
                         ['action.json','approval.json','state.json'])
        result, output, error, _ = self.invoke(self.approval_args())
        self.assertIn('NOT_AUTHORIZED bindings=PASS', output)
        self.assertEqual((result,error), (0,''))

    def test_action_and_approval_schema_version_public_cli_exact_key(self):
        for path, value, args in (
                (self.action_file, self.action, ['action','validate','--file',str(self.action_file),'--json']),
                (self.approval_file, self.approval, self.approval_args('--json'))):
            raw = value.to_dict()
            self.assertEqual(raw['schema_version'], 1)
            self.assertNotIn('version', raw)
            self.action_file.write_text(self.action.to_json())
            path.write_text(json.dumps(raw))
            result, output, error, _ = self.invoke(args)
            self.assertEqual((result,error), (0,''))
            self.assertFalse(json.loads(output)['authorized'])
            invalid_texts = [value.to_json()[:-1] + ',"schema_version":1}']
            for variant in ('legacy', 'both', 'missing', True, '1', 1.0, 2):
                invalid = copy.deepcopy(raw)
                if variant == 'legacy':
                    invalid['version'] = invalid.pop('schema_version')
                elif variant == 'both':
                    invalid['version'] = 1
                elif variant == 'missing':
                    del invalid['schema_version']
                else:
                    invalid['schema_version'] = variant
                invalid_texts.append(json.dumps(invalid))
            for text in invalid_texts:
                with self.subTest(contract=type(value), text=text):
                    path.write_text(text)
                    result, output, error, _ = self.invoke(args)
                    self.assertEqual((result,output), (1,''))
                    self.assertEqual(error,'llmtool: ACTION_CONTRACT_INVALID; details omitted\n')
            path.write_text(value.to_json())

    def test_public_every_hashed_action_field_stale_receipt(self):
        accepted = rejected = 0
        for kind in c.ActionKind:
            action = fixtures.make_action(kind)
            receipt = fixtures.make_approval(action)
            self.approval_file.write_text(receipt.to_json())
            self.state_file.write_text(action.expected_state.to_json())
            for path, value in fixtures.leaves(action.to_dict()):
                raw = fixtures.changed_leaf(action.to_dict(), path, fixtures.alternative(value))
                self.action_file.write_text(json.dumps(raw))
                with self.subTest(kind=kind, field=path):
                    result, output, error, _ = self.invoke(self.approval_args('--json'))
                    self.assertEqual(result, 1)
                    if output:
                        accepted += 1
                        data = json.loads(output)
                        self.assertFalse(data['bindings_valid'])
                        self.assertFalse(data['authorized'])
                    else:
                        rejected += 1
                        self.assertIn('ACTION_CONTRACT_INVALID', error)
        self.assertEqual(accepted+rejected, 109)

    def test_supplied_state_head_repo_files_index_mismatch_fail_closed(self):
        for kind in c.ActionKind:
            action = fixtures.make_action(kind)
            self.action_file.write_text(action.to_json())
            self.approval_file.write_text(fixtures.make_approval(action).to_json())
            for path, value in fixtures.leaves(action.expected_state.to_dict()):
                self.state_file.write_text(json.dumps(fixtures.changed_leaf(
                    action.expected_state.to_dict(), path, fixtures.alternative(value))))
                with self.subTest(kind=kind, state=path):
                    result, output, error, _ = self.invoke(self.approval_args('--json'))
                    self.assertEqual(result, 1)
                    if output:
                        self.assertEqual(json.loads(output)['code'], 'STATE_MISMATCH')
                    else:
                        self.assertIn('ACTION_CONTRACT_INVALID', error)

    def test_public_expiry_nonce_consumption_and_issuance_order(self):
        cases = [(['--now','99'],'NOT_YET_VALID'), (['--now','200'],'EXPIRED'),
                 (['--nonce','9'*64],'NONCE_MISMATCH'),
                 (['--consumed-nonce','8'*64],'NONCE_CONSUMED')]
        for extra, code in cases:
            result, output, error, _ = self.invoke(self.approval_args(*extra,'--json'))
            self.assertEqual((result,error), (1,''))
            self.assertEqual(json.loads(output)['code'],code)
            self.assertFalse(json.loads(output)['authorized'])
        self.approval_file.write_text(json.dumps({**self.approval.to_dict(),'issued_at':99}))
        result, output, _, _ = self.invoke(self.approval_args('--json'))
        self.assertEqual(result,1)
        self.assertEqual(json.loads(output)['code'],'BINDING_MISMATCH')

    def test_malformed_duplicate_unknown_controls_nonfinite_version_no_partial_stdout(self):
        valid = self.action.to_dict()
        cases = ['{PRIVATE', '{"schema_version":1,"schema_version":1}',
            json.dumps({**valid,'PRIVATE_unknown':True}), json.dumps({**valid,'schema_version':True}),
            json.dumps({**valid,'schema_version':2}), json.dumps({**valid,'kind':'COMMAND'}),
            json.dumps({**valid,'action_id':'PRIVATE\x1b'}), '{"x":NaN}', '['*500+']'*500]
        for text in cases:
            self.action_file.write_text(text)
            result, output, error, _ = self.invoke(['action','validate','--file',str(self.action_file),'--json'])
            self.assertEqual((result,output), (1,''))
            self.assertEqual(error,'llmtool: ACTION_CONTRACT_INVALID; details omitted\n')

    def test_model_approved_raw_shell_and_trust_extension_never_authority(self):
        for source in ('MODEL','approved','SYSTEM'):
            self.approval_file.write_text(json.dumps({**self.approval.to_dict(),'source':source}))
            result, output, _, _ = self.invoke(self.approval_args('--json'))
            self.assertEqual((result,output), (1,''))
        self.approval_file.write_text(self.approval.to_json())
        for field in ('approved','authorized','trusted_context','signature','reasoning'):
            self.approval_file.write_text(json.dumps({**self.approval.to_dict(),field:'PRIVATE_APPROVED'}))
            self.assertEqual(self.invoke(self.approval_args('--json'))[:2],(1,''))
        for field in ('command','shell','argv','executable','env','raw_content','tool'):
            raw=copy.deepcopy(self.action.to_dict()); raw['payload'][field]='PRIVATE_APPROVED;touch marker'
            self.action_file.write_text(json.dumps(raw))
            self.assertEqual(self.invoke(['action','validate','--file',str(self.action_file),'--json'])[:2],(1,''))

    def test_input_symlink_parent_hardlink_fifo_directory_device_utf8_and_bound(self):
        paths = [self.root/'missing.json', self.root/'directory.json']
        paths[1].mkdir()
        link=self.root/'link.json'; link.symlink_to(self.action_file); paths.append(link)
        parent=self.root/'linked'; parent.symlink_to(self.root,target_is_directory=True)
        paths.append(parent/'action.json')
        hard=self.root/'hard.json'; os.link(self.action_file,hard); paths.append(hard)
        # The original action input is hardlinked too; both aliases must reject.
        paths.append(self.action_file)
        device=self.root/'device.json'; device.symlink_to('/dev/zero'); paths.append(device)
        fifo=self.root/'fifo.json'; os.mkfifo(fifo); paths.append(fifo)
        paths.append(self.write('binary.json',b'\xffPRIVATE'))
        paths.append(self.write('large.json',b'x'*(c.JSON_LIMIT+1)))
        paths.append(self.root/'absent'/'..'/'action.json')
        paths.append(self.root/'no-json.txt')
        for path in paths:
            with self.subTest(path=path.name):
                result, output, error, _=self.invoke(['action','validate','--file',str(path),'--json'],tripwires=False)
                self.assertEqual((result,output),(1,''))
                self.assertEqual(error,'llmtool: ACTION_INPUT_INVALID; details omitted\n')

    def test_input_at_limit_is_read_and_parsed_without_truncation(self):
        raw=self.action.to_json().encode()
        self.action_file.write_bytes(raw+b' '*(c.JSON_LIMIT-len(raw)))
        self.assertEqual(self.invoke(['action','validate','--file',str(self.action_file)])[:1],(0,))

    def test_input_changed_during_read_is_rejected(self):
        original=os.fstat
        calls=0
        def changed(descriptor):
            nonlocal calls
            result=original(descriptor); calls+=1
            if calls==2:
                values=list(result); values[6]+=1
                return os.stat_result(values)
            return result
        with patch('os.fstat',side_effect=changed):
            result,output,error,_=self.invoke(['action','validate','--file',str(self.action_file)],tripwires=False)
        self.assertEqual((result,output),(1,''))
        self.assertEqual(error,'llmtool: ACTION_INPUT_INVALID; details omitted\n')

    def launch(self, args, *, cwd=None, stdout=subprocess.PIPE):
        repo = Path(__file__).resolve().parents[3]
        env = {'HOME':str(self.home), 'LLMTOOL_HOME':str(self.state_home),
               'PATH':str(self.root/'absent-bin'), 'PYTHONDONTWRITEBYTECODE':'1',
               'PYTHONPATH':str(repo/'tools'/'llmtool')}
        return subprocess.run([sys.executable, '-m', 'llmtool', *args],
            cwd=cwd or repo, env=env, stdout=stdout, stderr=subprocess.PIPE,
            text=True, timeout=10)

    def test_real_closed_pipe_action_approval_human_json(self):
        before = self.snapshot()
        for args in (['action','validate','--file',str(self.action_file)], self.approval_args()):
            for extra in ([], ['--json']):
                with self.subTest(command=args[0], json=bool(extra)):
                    reader, writer = os.pipe()
                    os.close(reader)  # No reader exists before the child writes.
                    try:
                        # Keep a child process so the kernel exercises a real closed pipe.
                        result = self.launch([*args, *extra], stdout=writer)
                    finally:
                        os.close(writer)
                    self.assertEqual((result.returncode, result.stderr), (0, ''))
                    self.assertNotIn('Traceback', result.stderr)
                    self.assertNotIn('Exception ignored', result.stderr)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.state_home.exists())

    def test_public_input_ordinary_posix_components_all_four_switches(self):
        # Input names are literal filesystem data; Action target vocabulary stays strict.
        for name in ('My Projects', 'dir+name', '@inputs', '~archive', '-dash',
                     '.git', '.aws', 'key.pem', '$HOME', 'file;touch', 'café'):
            directory = self.root/name; directory.mkdir()
            paths = [directory/'action.json', directory/'approval.json', directory/'state.json']
            for path, contract in zip(paths, (self.action, self.approval, self.action.expected_state)):
                path.write_text(contract.to_json())
            before = self.snapshot()
            for extra in ([], ['--json']):
                cases = [(['action','validate','--file',str(paths[0]),*extra], 'PROPOSED'),
                    (['approval','validate','--action',str(paths[0]),'--approval',str(paths[1]),
                      '--state',str(paths[2]),'--nonce','8'*64,'--now','150',*extra], 'NOT_AUTHORIZED')]
                for args, status in cases:
                    with self.subTest(name=name, command=args[0], json=bool(extra)):
                        result, output, error, _ = self.invoke(args, tripwires=False)
                        self.assertEqual((result,error), (0,''))
                        if extra:
                            data = json.loads(output)
                            self.assertEqual(data['status'], status)
                            self.assertFalse(data['authorized'])
                        else:
                            self.assertIn(status, output)
                            self.assertIn('authorized=false', output)
                        self.assertNotIn(str(self.root), output)
            self.assertEqual(before, self.snapshot())
        # Keep a child process here to verify relative-path resolution from its cwd.
        result = self.launch(['action','validate','--file','./action.json','--json'], cwd=self.root)
        self.assertEqual((result.returncode,result.stderr), (0,''))
        self.assertFalse(json.loads(result.stdout)['authorized'])
        self.assertFalse(self.state_home.exists())

    def input_cases(self, filename):
        yield ['action','validate','--file',filename,'--json']
        for switch in ('--action','--approval','--state'):
            args = self.approval_args('--json')
            args[args.index(switch)+1] = filename
            yield args

    def test_public_input_rejection_classification_all_four_switches(self):
        directory = self.root/'directory.json'; directory.mkdir()
        leaf = self.root/'leaf.json'; leaf.symlink_to(self.action_file)
        ancestor = self.root/'ancestor'; ancestor.symlink_to(self.root, target_is_directory=True)
        hard = self.root/'hard.json'; os.link(self.write('hard-source.json', b'{}'), hard)
        fifo = self.root/'fifo.json'; os.mkfifo(fifo)
        unsafe = [str(self.root/'missing.json'), str(directory), str(leaf),
            str(ancestor/'action.json'), str(hard), str(fifo),
            str(self.write('large.json',b'x'*(c.JSON_LIMIT+1))),
            str(self.write('binary.json',b'\xffPRIVATE')), str(self.write('non-json.txt',b'{}')),
            str(self.root/'absent'/'..'/'action.json'), str(self.root/'..'/self.root.name/'action.json'),
            '/dev/action.json', '/proc/action.json', '/sys/action.json',
            '/dev/null', '/proc/self/environ', '/sys/kernel/uevent_seqnum']
        for filename in unsafe:
            for args in self.input_cases(filename):
                with self.subTest(filename=filename, switch=args):
                    result, output, error, _ = self.invoke(args, tripwires=False)
                    self.assertEqual((result,output,error),
                        (1,'','llmtool: ACTION_INPUT_INVALID; details omitted\n'))
        # Invalid spellings cannot be passed through execve (NUL), so use cli.main.
        for filename in ('PRIVATE\0.json', 'PRIVATE\n.json', 'PRIVATE\x7f.json',
                         'PRIVATE\x85.json', 'PRIVATE\u200b.json', 'PRIVATE\u202e.json',
                         'PRIVATE\udcff.json', 'x'*1025+'.json', 'x/'*32+'action.json'):
            for args in self.input_cases(filename):
                with self.subTest(spelling=filename, switch=args):
                    result, output, error, _ = self.invoke(args, tripwires=False)
                    self.assertEqual((result,output,error),
                        (1,'','llmtool: ACTION_INPUT_INVALID; details omitted\n'))
        self.assertFalse(self.state_home.exists())

    def test_public_legal_input_malformed_content_and_target_rules_unchanged(self):
        directory = self.root/'My Projects'; directory.mkdir()
        malformed = directory/'malformed.json'; malformed.write_text('{PRIVATE')
        for args in self.input_cases(str(malformed)):
            result, output, error, _ = self.invoke(args, tripwires=False)
            self.assertEqual((result,output,error),
                (1,'','llmtool: ACTION_CONTRACT_INVALID; details omitted\n'))
        for target in ('a b.py', '$HOME/file', 'file;touch', '.git/config', 'key.pem'):
            raw = fixtures.changed_leaf(self.action.to_dict(), ('payload','target','path'), target)
            raw['scope'] = [target]
            self.action_file.write_text(json.dumps(raw))
            result, output, error, _ = self.invoke(
                ['action','validate','--file',str(self.action_file),'--json'])
            self.assertEqual((result,output,error),
                (1,'','llmtool: ACTION_CONTRACT_INVALID; details omitted\n'))

    def test_unexpected_internal_errors_sanitized_and_no_traceback(self):
        with patch.object(adapter,'parse_action',side_effect=RuntimeError('PRIVATE_EXCEPTION')):
            result,output,error,_=self.invoke(['action','validate','--file',str(self.action_file)])
        self.assertEqual((result,output),(1,''))
        self.assertEqual(error,'llmtool: ACTION_INTERNAL_ERROR; details omitted\n')

    def test_help_and_usage_no_execute_apply_yes_or_approval_generation(self):
        for args in (['action','--help'],['action','validate','--help'],['approval','--help'],
                     ['approval','validate','--help']):
            output,error=io.StringIO(),io.StringIO()
            with redirect_stdout(output),redirect_stderr(error),self.assertRaises(SystemExit) as result:
                main(args)
            self.assertEqual(result.exception.code,0)
            for option in ('--execute','--apply','--yes','--approve','--trusted-context'):
                self.assertNotIn(option,output.getvalue())
        for args in (['action','execute'],['action','apply'],['approval','grant'],
                ['approval','validate'],['action','validate','--file',str(self.action_file),'--yes']):
            output,error=io.StringIO(),io.StringIO()
            with redirect_stdout(output),redirect_stderr(error),self.assertRaises(SystemExit) as result:
                main(args)
            self.assertEqual(result.exception.code,2)
            self.assertEqual(output.getvalue(),'')
            self.assertNotIn(str(self.root),error.getvalue())

    def test_actual_launchers_offline_hostile_executable_tripwires(self):
        executable=self.root/'bin';executable.mkdir()
        marker=self.root/'unexpected-executable'
        for name in ('git','claude','codex','sh','bash'):
            script=executable/name
            script.write_text('#!/bin/sh\n: > '+str(marker)+'\nexit 99\n')
            script.chmod(0o700)
        repo=Path(__file__).resolve().parents[3]
        env={'HOME':str(self.home),'LLMTOOL_HOME':str(self.state_home),
             'PATH':str(executable),'PYTHONDONTWRITEBYTECODE':'1',
             'PYTHONPATH':str(repo/'tools'/'llmtool'), 'API_KEY':'PRIVATE_SECRET'}
        before=self.snapshot()
        for launcher in ((sys.executable,'-m','llmtool'),
                         (sys.executable,str(repo/'tools'/'llmtool'/'llmtool.py'))):
            # Keep real launches to verify entry points and the isolated hostile PATH.
            result=subprocess.run([*launcher,'action','validate','--file',str(self.action_file),'--json'],
                cwd=repo,env=env,capture_output=True,text=True,timeout=10)
            self.assertEqual((result.returncode,result.stderr),(0,''))
            self.assertFalse(json.loads(result.stdout)['authorized'])
        self.assertFalse(marker.exists())
        self.assertEqual(before,self.snapshot())
        self.assertFalse(self.state_home.exists())
