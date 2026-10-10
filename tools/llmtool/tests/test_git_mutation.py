"""ADD preflight only: disposable synthetic repositories, never the user's index."""
import ast
import errno
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import struct
import stat
import unicodedata
import unittest
from types import SimpleNamespace
from dataclasses import FrozenInstanceError, replace
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

from llmtool import action_contract as c
from llmtool import git_mutation as g
from llmtool.errors import LLMToolError


class GitPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='llmtool-preflight-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(os.path.realpath(self.temp.name))
        self.git('init', '-q')
        self.git('config', 'user.name', 'Synthetic')
        self.git('config', 'user.email', 'synthetic@example.invalid')
        self.git('config', 'core.fileMode', 'true')
        (self.root/'target.txt').write_bytes(b'original\n')
        (self.root/'other.txt').write_bytes(b'other\n')
        self.git('add', '--', 'target.txt', 'other.txt')
        self.git('commit', '-qm', 'synthetic baseline')
        (self.root/'target.txt').write_bytes(b'authorized change\n')

    def git(self, *args):
        env = {'PATH': os.environ.get('PATH', os.defpath), 'LC_ALL': 'C',
               'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
               'GIT_TERMINAL_PROMPT': '0'}
        return subprocess.check_output(['git', *args], cwd=self.root, env=env,
                                       stderr=subprocess.PIPE)

    def action(self, paths=('target.txt',)):
        root = self.root.stat()
        repo = c.RepositoryBinding('3'*64, str(self.root), root.st_dev, root.st_ino,
                                   self.git('rev-parse', 'HEAD').decode().strip())
        files = []
        for path in paths:
            st = (self.root/path).stat()
            md = c.FileMetadata(st.st_dev, st.st_ino, st.st_mode & 0o777,
                st.st_uid, st.st_gid, st.st_size, st.st_mtime_ns, 1)
            files.append(c.FileExpectation(path, c.FileState(True, c.FileType.REGULAR,
                hashlib.sha256((self.root/path).read_bytes()).hexdigest(), md)))
        payload = c.GitAddPayload(c.GitOperation.ADD, files,
                                  hashlib.sha256((self.root/'.git/index').read_bytes()).hexdigest())
        return c.Action(1, 'action-1', 100, c.ActionKind.GIT_OPERATION,
            c.TaskBinding('task-1', '1'*32, 1, '2'*64), repo, sorted(paths), payload)

    def reject(self, action=None):
        with self.assertRaises(g.GitPreflightError) as caught:
            g.preflight_git_add(action or self.action())
        self.assertNotIn(str(self.root), str(caught.exception))
        self.assertNotIn('PRIVATE_MARKER', str(caught.exception))
        return caught.exception.category

    def test_target_modified_plan_is_immutable_and_index_untouched(self):
        action = self.action()
        before = (self.root/'.git/index').read_bytes()
        result = g.preflight_git_add(action)
        self.assertEqual(result.head, action.repository.expected_head)
        self.assertEqual(result.action_digest, action.digest)
        self.assertEqual(result.state_digest, action.expected_state.digest)
        self.assertEqual(result.index_sha256, action.payload.expected_index_sha256)
        self.assertEqual(result.paths, ('target.txt',))
        self.assertEqual(result.future_add_argv[-3:], ('add', '--', 'target.txt'))
        self.assertNotIn('GIT_OPTIONAL_LOCKS', dict(result.future_environment))
        self.assertEqual((self.root/'.git/index').read_bytes(), before)
        with self.assertRaises(FrozenInstanceError):
            result.head = '0'*40

    def test_unrelated_unstaged_and_untracked_are_preserved(self):
        (self.root/'other.txt').write_bytes(b'user work')
        (self.root/'untracked.txt').write_bytes(b'user artifact')
        result = g.preflight_git_add(self.action())
        self.assertEqual(result.paths, ('target.txt',))
        self.assertEqual((self.root/'other.txt').read_bytes(), b'user work')
        self.assertEqual((self.root/'untracked.txt').read_bytes(), b'user artifact')

    def test_staged_target_unconditionally_rejected(self):
        self.git('add', 'target.txt')
        self.reject()

    def test_unrelated_staged_rejected(self):
        (self.root/'other.txt').write_bytes(b'pre-staged')
        self.git('add', 'other.txt')
        self.reject()

    def test_filter_is_rejected_not_neutralized(self):
        self.git('config', 'filter.encrypt.clean', 'PRIVATE_MARKER')
        self.assertEqual(self.reject(), 'unsafe filter')

    def test_transforming_attribute_rejected(self):
        (self.root/'.gitattributes').write_text('target.txt text eol=lf\n')
        self.assertEqual(self.reject(), 'unsafe attribute')

    def test_include_rejected_without_following(self):
        self.git('config', 'include.path', '/PRIVATE_MARKER/not-present')
        self.assertEqual(self.reject(), 'unsafe config')

    def test_external_excludes_file_rejected_before_other_queries(self):
        action = self.action()
        external = '/PRIVATE_MARKER'
        self.git('config', 'core.excludesFile', external)
        original = g._Reads.query
        queries = []
        def observe(reads, which):
            queries.append(which)
            self.assertIs(which, g._Read.CONFIG)
            return original(reads, which)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(g._Reads, 'query', observe), \
                patch.object(g.subprocess, 'Popen', wraps=g.subprocess.Popen) as spawn, \
                redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(g.GitPreflightError) as caught:
                g.preflight_git_add(action)
        self.assertEqual(str(caught.exception), 'Git ADD preflight rejected: unsafe config')
        self.assertEqual(queries, [g._Read.CONFIG])
        self.assertEqual(spawn.call_count, 1)
        self.assertIn('--no-includes', spawn.call_args.args[0])
        self.assertNotIn(external, repr(spawn.call_args))
        self.assertNotIn(external, repr(caught.exception))
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(stderr.getvalue(), '')

    def test_excludes_file_key_is_case_insensitively_rejected(self):
        for key in (b'core.excludesfile', b'CoRe.ExClUdEsFiLe', b'CORE.EXCLUDESFILE'):
            with self.subTest(key=key):
                with self.assertRaises(g.GitPreflightError) as caught:
                    g._check_config(key + b'\n/PRIVATE_MARKER\0')
                self.assertEqual(caught.exception.category, 'unsafe config')
                self.assertNotIn('PRIVATE_MARKER', str(caught.exception))

    def test_excludes_fifo_never_followed(self):
        action = self.action()
        # Separate synthetic runtime data, outside the bound repository.
        with tempfile.TemporaryDirectory(prefix='llmtool-excludes-') as adjacent:
            fifo = Path(adjacent)/'PRIVATE_MARKER.fifo'
            os.mkfifo(fifo)
            self.git('config', 'core.excludesFile', str(fifo))
            original = g._Reads.query
            queries = []
            def observe(reads, which):
                queries.append(which)
                # Deterministic non-following proof; never let a buggy ignore
                # query reach the FIFO and turn this regression into a hang.
                self.assertIs(which, g._Read.CONFIG)
                return original(reads, which)
            with patch.object(g._Reads, 'query', observe), \
                    patch.object(g.subprocess, 'Popen', wraps=g.subprocess.Popen) as spawn:
                self.assertEqual(self.reject(action), 'unsafe config')
            self.assertEqual(queries, [g._Read.CONFIG])
            self.assertEqual(spawn.call_count, 1)
            self.assertIn('--no-includes', spawn.call_args.args[0])
            self.assertNotIn(str(fifo), repr(spawn.call_args))
            self.assertTrue(stat.S_ISFIFO(fifo.stat().st_mode))

    def test_excludes_null_pin_in_all_reads_and_future_plan(self):
        action = self.action()
        with patch.object(g.subprocess, 'Popen', wraps=g.subprocess.Popen) as spawn:
            result = g.preflight_git_add(action)
        self.assertGreater(spawn.call_count, 1)
        argvs = [call.args[0] for call in spawn.call_args_list]
        self.assertTrue(any('ls-files' in argv for argv in argvs))
        for argv in argvs + [result.future_add_argv]:
            with self.subTest(argv=argv):
                self.assertIn('core.excludesFile=/dev/null', argv)
                pin = argv.index('core.excludesFile=/dev/null')
                self.assertEqual(argv[pin-1], '-c')

    def test_index_lock_preserved(self):
        lock = self.root/'.git/index.lock'
        lock.write_bytes(b'user lock')
        self.reject()
        self.assertEqual(lock.read_bytes(), b'user lock')

    def test_blob_oid_is_exact_bytes(self):
        for fmt in ('sha1', 'sha256'):
            raw = b'line\r\n\x00end'
            self.assertEqual(g.git_blob_oid(raw, fmt),
                             hashlib.new(fmt, b'blob 10\0'+raw).hexdigest())

    def test_production_import_boundary(self):
        package = Path(__file__).resolve().parents[1]/'llmtool'
        reviewed_private = {'_git_containment.py', '_git_supervisor.py',
                            '_git_containment_protocol.py', '_git_process_identity.py',
                            '_git_containment_child.py', '_git_add_private.py', '_git_add_host.py'}
        private_markers = tuple(name[:-3] for name in reviewed_private)
        for filename in package.rglob('*.py'):
            if filename.name == 'git_mutation.py':
                continue
            text = filename.read_text()
            # Only these root-level reviewed helpers may reference each other.
            # Only the sealed disposable-fixture adapter may import preflight.
            public = not (filename.parent == package and filename.name in reviewed_private)
            if public:
                self.assertNotIn('git_mutation', text, filename)
                for marker in private_markers:
                    self.assertNotIn(marker, text, filename)
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, ast.Import) and filename.name != '_git_add_private.py':
                    self.assertFalse(any('git_mutation' in n.name for n in node.names), filename)
                if isinstance(node, ast.ImportFrom) and filename.name != '_git_add_private.py':
                    self.assertNotIn('git_mutation', node.module or '', filename)
                    self.assertFalse(any(n.name == 'git_mutation' for n in node.names), filename)
                if public and isinstance(node, ast.Constant) and isinstance(node.value, str):
                    for marker in ('git_mutation',) + private_markers:
                        self.assertNotIn(marker, node.value, filename)

    def test_scope_and_approval_boundary(self):
        action = self.action()
        self.reject(replace(action, kind=c.ActionKind.FILE_DELETE,
                            payload=c.FileDeletePayload(action.payload.files[0])))
        self.reject(action.to_dict())
        with self.assertRaises(g.GitPreflightError):
            g.preflight_git_add(c.ApprovalValidation(True, False, c.ApprovalCode.NOT_AUTHORIZED))
        import inspect
        self.assertEqual(tuple(inspect.signature(g.preflight_git_add).parameters), ('action',))
        raw = action.to_dict(); raw['scope'] = ['other.txt']
        with self.assertRaises(LLMToolError):
            c.parse_action(raw)
        raw = action.to_dict(); raw['payload']['operation'] = 'PUSH'
        with self.assertRaises(LLMToolError):
            c.parse_action(raw)

    def test_root_device_inode_head_mismatch(self):
        action = self.action()
        for field in ('root_device', 'root_inode'):
            with self.subTest(field=field):
                self.reject(replace(action, repository=replace(action.repository,
                    **{field: getattr(action.repository, field)+1})))
        self.reject(replace(action, repository=replace(action.repository, expected_head='f'*40)))

    def test_nonroot_and_root_owner_required(self):
        with patch.object(g.os, 'geteuid', return_value=0):
            self.reject()
        with patch.object(g.os, 'geteuid', return_value=os.geteuid()+1):
            self.reject()

    def test_root_writable_rejected(self):
        action = self.action()
        for mode in (0o770, 0o707):
            self.root.chmod(mode)
            self.reject(action)
        self.root.chmod(0o700)

    def test_symlink_root_component_rejected(self):
        link = self.root/'alias'; link.symlink_to(self.root, target_is_directory=True)
        action = self.action()
        self.reject(replace(action, repository=replace(action.repository,
                            canonical_root=str(link))))

    def test_gitfile_explicit_rejection(self):
        action = self.action()
        (self.root/'.git').rename(self.root/'git-storage')
        (self.root/'.git').write_text('gitdir: git-storage\n')
        self.reject(action)

    def test_git_directory_symlink_rejected(self):
        action = self.action()
        (self.root/'.git').rename(self.root/'git-storage')
        (self.root/'.git').symlink_to('git-storage', target_is_directory=True)
        self.reject(action)

    def test_commondir_explicit_rejection(self):
        action = self.action()
        (self.root/'.git/commondir').write_text('..\n')
        self.reject(action)

    def test_alternates_explicit_rejection(self):
        (self.root/'.git/objects/info/alternates').write_text('/PRIVATE_MARKER\n')
        self.reject()

    def test_other_worktree_registration_rejected(self):
        (self.root/'.git/worktrees').mkdir()
        self.reject()

    def test_detached_head_rejected(self):
        self.git('checkout', '--detach', '-q')
        self.reject()

    def test_unborn_head_rejected(self):
        action = self.action()
        (self.root/'.git/HEAD').write_text('ref: refs/heads/nonexistent\n')
        self.reject(action)

    def test_bare_config_rejected(self):
        action = self.action()
        self.git('config', 'core.bare', 'true')
        self.reject(action)

    def test_index_missing_rejected(self):
        action = self.action()
        (self.root/'.git/index').unlink()
        self.reject(action)

    def test_wrong_index_sha_rejected(self):
        action = self.action()
        self.reject(replace(action, payload=replace(action.payload, expected_index_sha256='f'*64)))

    def test_index_symlink_and_hardlink_rejected(self):
        action = self.action(); index = self.root/'.git/index'
        index.rename(self.root/'saved-index')
        index.symlink_to(self.root/'saved-index')
        self.reject(action)
        index.unlink(); os.link(self.root/'saved-index', index)
        self.reject(action)

    def test_staged_then_unstaged_target_rejected(self):
        self.git('add', 'target.txt')
        (self.root/'target.txt').write_bytes(b'further edit')
        self.reject()

    def test_untracked_target_supported(self):
        (self.root/'new.txt').write_bytes(b'new authorized file')
        result = g.preflight_git_add(self.action(('new.txt',)))
        self.assertEqual(result.paths, ('new.txt',))

    def test_ignored_untracked_target_rejected(self):
        (self.root/'.gitignore').write_text('new.txt\n')
        (self.root/'new.txt').write_bytes(b'ignored')
        self.reject(self.action(('new.txt',)))

    def test_tracked_target_ignored_pattern_remains_supported(self):
        (self.root/'.gitignore').write_text('target.txt\n')
        g.preflight_git_add(self.action())

    def test_info_exclude_ignored_untracked_target_rejected(self):
        (self.root/'.git/info/exclude').write_text('new.txt\n')
        (self.root/'new.txt').write_bytes(b'ignored')
        self.assertEqual(self.reject(self.action(('new.txt',))), 'file/path mismatch')

    def test_tracked_target_info_exclude_pattern_remains_supported(self):
        (self.root/'.git/info/exclude').write_text('target.txt\n')
        result = g.preflight_git_add(self.action())
        self.assertEqual(result.paths, ('target.txt',))

    def test_staged_rename_and_copy_rejected(self):
        self.git('mv', 'other.txt', 'renamed.txt')
        self.reject()

    def test_staged_copy_rejected(self):
        (self.root/'copy.txt').write_bytes((self.root/'other.txt').read_bytes())
        self.git('add', 'copy.txt')
        self.reject()

    def test_target_deletion_rejected(self):
        action = self.action(); (self.root/'target.txt').unlink()
        self.reject(action)

    def test_unrelated_unstaged_deletion_preserved(self):
        (self.root/'other.txt').unlink()
        g.preflight_git_add(self.action())
        self.assertFalse((self.root/'other.txt').exists())

    def test_file_mode_only_target_supported(self):
        (self.root/'target.txt').write_bytes(b'original\n')
        (self.root/'target.txt').chmod(0o755)
        result = g.preflight_git_add(self.action())
        self.assertEqual(result.bound_blobs[0].mode, 0o100755)

    def test_git_mode_uses_owner_execute_not_group_or_other_execute(self):
        (self.root/'target.txt').chmod(0o645)
        result = g.preflight_git_add(self.action())
        # Native Git in this disposable fixture provides the reference mode.
        self.git('add','target.txt')
        actual_mode = int(self.git('ls-files','--stage','--','target.txt').split()[0],8)
        self.assertEqual(actual_mode,0o100644)
        self.assertEqual(result.bound_blobs[0].mode,actual_mode)

    def test_submodule_gitlink_rejected(self):
        self.git('update-index', '--add', '--cacheinfo', '160000',
                 self.git('rev-parse','HEAD').decode().strip(), 'module')
        self.reject()

    def test_conflict_index_rejected(self):
        self.git('checkout', '-qb', 'synthetic-conflict')
        (self.root/'other.txt').write_bytes(b'branch')
        self.git('add', 'other.txt'); self.git('commit', '-qm', 'branch')
        self.git('checkout', '-q', '-')
        (self.root/'other.txt').write_bytes(b'base')
        self.git('add', 'other.txt'); self.git('commit', '-qm', 'base')
        with self.assertRaises(subprocess.CalledProcessError):
            self.git('merge', 'synthetic-conflict')
        self.assertEqual(self.reject(), 'HEAD mismatch')
        (self.root/'.git/MERGE_HEAD').unlink()
        self.assertEqual(self.reject(), 'conflict')

    def test_parent_symlink_and_nested_repo_rejected(self):
        (self.root/'nested').mkdir()
        (self.root/'nested/file.txt').write_bytes(b'file')
        action = self.action(('nested/file.txt',))
        (self.root/'nested').rename(self.root/'saved-parent')
        (self.root/'nested').symlink_to('saved-parent', target_is_directory=True)
        self.reject(action)
        (self.root/'nested').unlink()
        (self.root/'saved-parent').rename(self.root/'nested')
        (self.root/'nested/.git').mkdir()
        self.reject(action)

    def test_parent_writable_or_owner_mismatch_rejected(self):
        (self.root/'nested').mkdir()
        (self.root/'nested/file.txt').write_bytes(b'file')
        action = self.action(('nested/file.txt',))
        (self.root/'nested').chmod(0o777)
        self.reject(action)

    def test_target_symlink_and_hardlink_rejected(self):
        action = self.action()
        (self.root/'target.txt').rename(self.root/'saved.txt')
        (self.root/'target.txt').symlink_to('saved.txt')
        self.reject(action)
        (self.root/'target.txt').unlink()
        os.link(self.root/'saved.txt', self.root/'target.txt')
        self.reject(action)

    def test_target_nonregular_rejected_without_blocking(self):
        action = self.action(); path = self.root/'target.txt'; path.unlink()
        os.mkfifo(path); self.reject(action)
        path.unlink(); path.mkdir(); self.reject(action)

    def test_target_inode_content_and_metadata_drift(self):
        action = self.action(); path = self.root/'target.txt'
        path.rename(self.root/'saved.txt'); path.write_bytes(b'authorized change\n')
        self.reject(action)
        action = self.action(); path.write_bytes(b'changed')
        self.reject(action)
        action = self.action(); path.chmod(0o755)
        self.reject(action)
        action = self.action()
        target = action.payload.files[0]
        state = replace(target.state, content_sha256='a'*64)
        self.reject(replace(action, payload=replace(action.payload,
                    files=(replace(target, state=state),))))

    def test_case_alias_rejected_even_if_filesystem_resolves_it(self):
        if not (self.root/'TARGET.txt').exists():
            self.skipTest('filesystem does not resolve case aliases')
        action = self.action()
        target = replace(action.payload.files[0], path='TARGET.txt')
        self.reject(replace(action, scope=('TARGET.txt',), payload=replace(action.payload, files=(target,))))

    def test_normalization_alias_rejected(self):
        # A11b.1 rejects NFD combining characters; exact disk NFC spelling is
        # additionally required by preflight (even on normalizing macOS disks).
        nfd = unicodedata.normalize('NFD', 'café.txt')
        (self.root/nfd).write_bytes(b'file')
        if nfd not in os.listdir(self.root):
            self.skipTest('filesystem does not retain decomposed filename bytes')
        action = self.action(('café.txt',)) if (self.root/'café.txt').exists() else None
        if action:
            self.reject(action)
        else:
            # Case-sensitive/non-normalizing hosts: bind the exact existing file
            # metadata but demand its differently normalized spelling.
            base = self.action(); st = (self.root/nfd).stat()
            md = c.FileMetadata(st.st_dev,st.st_ino,st.st_mode&0o777,st.st_uid,st.st_gid,st.st_size,st.st_mtime_ns,1)
            f = c.FileExpectation('café.txt', c.FileState(True,c.FileType.REGULAR,
                                    hashlib.sha256(b'file').hexdigest(),md))
            self.reject(replace(base, scope=('café.txt',), payload=replace(base.payload, files=(f,))))

    def test_semantic_index_matches_git_exact_bytes(self):
        action = self.action(); result = g.preflight_git_add(action)
        raw = self.git('ls-files','--stage','-z')
        actual = tuple((e.path, e.mode, e.oid, e.stage) for e in result.semantic_index)
        expected = []
        for row in raw.split(b'\0'):
            if row:
                info,path = row.split(b'\t'); mode,oid,stage = info.split()
                expected.append((path,int(mode,8),oid.decode(),int(stage)))
        self.assertEqual(actual,tuple(expected))
        self.assertFalse(any(e.skip_worktree or e.intent_to_add or e.assume_unchanged for e in result.semantic_index))
        self.assertEqual(g.hash_git_index(action.repository), result.index_sha256)

    def test_index_v4_parsing_matches_v2_baseline(self):
        expected = g.preflight_git_add(self.action()).semantic_index
        self.git('update-index', '--index-version', '4')
        self.assertEqual(g.preflight_git_add(self.action()).semantic_index, expected)

    def test_index_checksum_invalid_rejected(self):
        index = self.root/'.git/index'; raw = bytearray(index.read_bytes()); raw[20] ^= 1
        index.write_bytes(raw)
        self.assertEqual(self.reject(), 'unsupported index state')

    def test_blob_oid_sha256_repository(self):
        # Build a new synthetic fixture, never clone/copy an existing repository.
        self.root = self.root/'sha256'; self.root.mkdir()
        try:
            self.git('init', '-q', '--object-format=sha256')
        except subprocess.CalledProcessError:
            self.skipTest('local Git does not support sha256 repositories')
        self.git('config','user.name','Synthetic'); self.git('config','user.email','x@example.invalid')
        self.git('config','core.fileMode','true')
        (self.root/'target.txt').write_bytes(b'sha256\n')
        self.git('add','target.txt'); self.git('commit','-qm','sha256')
        result = g.preflight_git_add(self.action())
        self.assertEqual(result.object_format,'sha256')
        self.assertEqual(result.bound_blobs[0].oid,self.git('hash-object','target.txt').decode().strip())

    def test_future_plan_and_sanitized_environment(self):
        action = self.action()
        with patch.dict(os.environ, {'GIT_DIR':'/PRIVATE_MARKER', 'GIT_INDEX_FILE':'/PRIVATE_MARKER',
            'GIT_CONFIG_COUNT':'1','OPENAI_API_KEY':'PRIVATE_MARKER','HOME':'/PRIVATE_MARKER',
            'SSH_AUTH_SOCK':'PRIVATE_MARKER','HTTP_PROXY':'PRIVATE_MARKER','DYLD_INSERT_LIBRARIES':'PRIVATE_MARKER'}):
            result = g.preflight_git_add(action)
        env = dict(result.future_environment)
        self.assertEqual(env['GIT_DIR'],str(self.root/'.git'))
        self.assertEqual(env['GIT_WORK_TREE'],str(self.root))
        for key in ('GIT_INDEX_FILE','GIT_CONFIG_COUNT','OPENAI_API_KEY','HOME','SSH_AUTH_SOCK','HTTP_PROXY','DYLD_INSERT_LIBRARIES'):
            self.assertNotIn(key,env)
        self.assertNotIn('PRIVATE_MARKER',repr(result))
        for pin in ('core.hooksPath=/dev/null','core.fsmonitor=false','status.renames=false','core.autocrlf=false','add.ignoreErrors=false'):
            self.assertIn(pin,result.future_add_argv)

    def test_no_mutation_spawn_and_config_helper_suppression(self):
        hook = self.root/'.git/hooks/post-index-change'
        marker = self.root/'PRIVATE_MARKER'
        hook.write_text('#!/bin/sh\ntouch '+str(marker)+'\n'); hook.chmod(0o755)
        self.git('config','core.fsmonitor',str(hook))
        self.git('config','diff.external',str(hook))
        self.git('config','diff.x.textconv',str(hook))
        self.git('config','credential.helper','!'+str(hook))
        self.git('config','core.sshCommand',str(hook))
        self.git('config','gpg.program',str(hook))
        action = self.action()
        original = g.subprocess.Popen; calls = []
        def observe(argv, **kwargs):
            calls.append((argv,kwargs))
            self.assertFalse(kwargs['shell']); self.assertTrue(kwargs['start_new_session'])
            self.assertEqual(kwargs['env']['GIT_OPTIONAL_LOCKS'],'0')
            self.assertNotIn('add',argv)
            return original(argv,**kwargs)
        before = (self.root/'.git/index').read_bytes()
        with patch.object(g.subprocess,'Popen', side_effect=observe):
            g.preflight_git_add(action)
        self.assertTrue(calls); self.assertFalse(marker.exists())
        self.assertEqual(before,(self.root/'.git/index').read_bytes())

    def test_target_content_change_between_queries_rejected(self):
        action = self.action(); original = g._Reads.query
        def changed(reads, query):
            raw = original(reads,query)
            if query is g._Read.IGNORE:
                (self.root/'target.txt').write_bytes(b'race')
            return raw
        with patch.object(g._Reads,'query',changed):
            self.reject(action)

    def test_socket_target_rejected(self):
        import socket
        action = self.action(); path = self.root/'target.txt'; path.unlink()
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind(str(path)); self.reject(action)

    def test_device_type_check_without_privilege(self):
        from types import SimpleNamespace
        metadata = SimpleNamespace(st_mode=stat.S_IFCHR|0o600,st_uid=os.geteuid(),st_nlink=1)
        with self.assertRaises(g.GitPreflightError):
            g._private(metadata,False,'file/path mismatch')

    def test_parent_uid_checked_independently(self):
        from types import SimpleNamespace
        (self.root/'nested').mkdir()
        (self.root/'nested/file.txt').write_bytes(b'file')
        action = self.action(('nested/file.txt',)); inode = (self.root/'nested').stat().st_ino
        original = g.os.fstat
        def changed(fd):
            st = original(fd)
            if st.st_ino == inode:
                return SimpleNamespace(st_mode=st.st_mode,st_uid=st.st_uid+1,st_nlink=st.st_nlink)
            return st
        with patch.object(g.os,'fstat',changed):
            self.reject(action)

    def test_case_collision_with_index_spelling(self):
        if (self.root/'TARGET.txt').exists():
            self.skipTest('filesystem cannot create distinct case-colliding entries')
        (self.root/'TARGET.txt').write_bytes(b'distinct file')
        self.reject(self.action(('TARGET.txt',)))

    def test_exact_index_nonutf8_path_bytes(self):
        name = os.fsdecode(b'nonutf8-\xff.txt')
        try:
            (self.root/name).write_bytes(b'unrelated')
        except OSError as exc:
            if exc.errno == errno.EILSEQ:
                self.skipTest('filesystem rejects non-UTF-8 entry names')
            raise
        self.git('add','--',name); self.git('commit','-qm','unrelated binary name')
        result = g.preflight_git_add(self.action())
        self.assertIn(b'nonutf8-\xff.txt',tuple(e.path for e in result.semantic_index))

    def test_index_native_extensions_rejected(self):
        raw = (self.root/'.git/index').read_bytes()
        for signature in (b'link', b'sdir', b'evil'):
            prefix = raw[:-20]+struct.pack('>4sI',signature,0)
            forged = prefix+hashlib.sha1(prefix).digest()
            with self.subTest(signature=signature), self.assertRaises(g.GitPreflightError):
                g._parse_index(forged,'sha1')

    def test_native_semantic_path_bytes_mode_oid_stage(self):
        path = b'nonutf8-\xff\tname.txt'
        oid = bytes.fromhex('ab'*20)
        fields = bytearray(40); struct.pack_into('>I',fields,24,0o100755)
        entry = bytes(fields)+oid+struct.pack('>H',len(path)|(2<<12))+path+b'\0'
        entry += b'\0'*(-len(entry)%8)
        prefix = b'DIRC'+struct.pack('>II',2,1)+entry
        raw = prefix+hashlib.sha1(prefix).digest()
        self.assertEqual(g._parse_index(raw,'sha1'),
                         (g.IndexEntry(path,0o100755,'ab'*20,2,False,False,False),))

    def test_split_index_actual_link_extension(self):
        self.git('update-index','--split-index')
        self.reject()

    def test_index_parse_stage_flags(self):
        self.git('update-index','--assume-unchanged','other.txt')
        entries = g._parse_index((self.root/'.git/index').read_bytes(),'sha1')
        self.assertTrue(next(e for e in entries if e.path == b'other.txt').assume_unchanged)
        self.assertTrue(all(e.stage == 0 for e in entries))

    def test_attributes_in_info_and_parent_hierarchy(self):
        (self.root/'nested').mkdir(); (self.root/'nested/deep').mkdir()
        (self.root/'nested/deep/file.txt').write_bytes(b'file')
        (self.root/'nested/.gitattributes').write_text('deep/file.txt ident\n')
        self.reject(self.action(('nested/deep/file.txt',)))
        (self.root/'nested/.gitattributes').unlink()
        (self.root/'.git/info/attributes').write_text('target.txt text\n')
        self.reject()

    def test_unset_attributes_allowed(self):
        (self.root/'.gitattributes').write_text('target.txt -text -ident -filter -eol -crlf -working-tree-encoding\n')
        g.preflight_git_add(self.action())

    def test_attribute_symlink_never_followed(self):
        (self.root/'.gitattributes').symlink_to('/PRIVATE_MARKER')
        self.reject()

    def test_raw_hash_helper_rejects_arbitrary_path(self):
        with self.assertRaises(g.GitPreflightError):
            g.hash_git_index(str(self.root/'.git/index'))

    def test_private_query_rejects_arbitrary_argv_before_spawn(self):
        reads = g._Reads(self.action())
        with patch.object(g.subprocess,'Popen') as spawn:
            with self.assertRaises(g.GitPreflightError):
                reads.query(('add','--','target.txt'))
        spawn.assert_not_called()

    def test_config_values_never_become_argv_or_error(self):
        action = self.action()
        self.git('config','filter.evil.clean','$(touch PRIVATE_MARKER); echo secret')
        with patch.object(g.subprocess,'Popen',wraps=g.subprocess.Popen) as spawn:
            self.reject(action)
        self.assertEqual(spawn.call_count,1)  # only no-includes config read
        self.assertNotIn('PRIVATE_MARKER',repr(spawn.call_args))

    def test_raw_git_stderr_not_exposed(self):
        action = self.action()
        # Git itself rejects this safely parseable but invalid local config.
        (self.root/'.git/config').write_bytes(b'[PRIVATE_MARKER broken\n')
        self.reject(action)

    def test_paths_cannot_inject_options(self):
        action = self.action(); raw = action.to_dict()
        for path in ('-option','sub/-option','x;touch','../x','x\nPRIVATE_MARKER'):
            raw['scope']=[path]; raw['payload']['files'][0]['path']=path
            with self.assertRaises(LLMToolError):
                c.parse_action(raw)

    def test_stat_only_unrelated_change_keeps_semantic_snapshot(self):
        before = g.preflight_git_add(self.action()).semantic_index
        os.utime(self.root/'other.txt',None)
        self.assertEqual(g.preflight_git_add(self.action()).semantic_index,before)

    def test_content_change_during_same_fd_hash_rejected(self):
        action = self.action(); original = g.os.read; changed = False
        inode = (self.root/'target.txt').stat().st_ino
        def read(fd,size):
            nonlocal changed
            data = original(fd,size)
            if not changed and os.fstat(fd).st_ino == inode:
                changed = True
                (self.root/'target.txt').write_bytes(b'race during hash')
            return data
        with patch.object(g.os,'read',read):
            self.reject(action)
        self.assertTrue(changed)

    def test_index_change_between_queries_rejected(self):
        action = self.action(); original = g._Reads.query
        def query(reads,which):
            raw = original(reads,which)
            if which is g._Read.IGNORE:
                self.git('update-index','--assume-unchanged','other.txt')
            return raw
        with patch.object(g._Reads,'query',query):
            self.reject(action)

    def test_config_change_between_queries_rejected(self):
        action = self.action(); original = g._Reads.query
        config = self.root/'.git/config'
        before = config.read_bytes()
        index = (self.root/'.git/index').read_bytes()
        target = (self.root/'target.txt').read_bytes()
        def query(reads,which):
            raw = original(reads,which)
            if which is g._Read.IGNORE:
                # Benign config-only drift: no filter/attribute rule can mask
                # the metadata snapshot invariant being tested here.
                config.write_bytes(before+b'\n[user]\n name = Changed synthetic name\n')
            return raw
        with patch.object(g._Reads,'query',query):
            self.reject(action)
        self.assertEqual((self.root/'.git/index').read_bytes(),index)
        self.assertEqual((self.root/'target.txt').read_bytes(),target)
        self.assertFalse((self.root/'.git/info/attributes').exists())

    def test_info_attributes_change_between_queries_rejected(self):
        attributes = self.root/'.git/info/attributes'
        attributes.write_text('# initial synthetic attributes\n')
        action = self.action(); original = g._Reads.query
        config = (self.root/'.git/config').read_bytes()
        index = (self.root/'.git/index').read_bytes()
        target = (self.root/'target.txt').read_bytes()
        def query(reads,which):
            raw = original(reads,which)
            if which is g._Read.IGNORE:
                # Change only an existing info/attributes file, not its parent
                # directory, config, index or target content.
                attributes.write_text('# changed synthetic attributes\n')
            return raw
        with patch.object(g._Reads,'query',query):
            self.reject(action)
        self.assertEqual((self.root/'.git/config').read_bytes(),config)
        self.assertEqual((self.root/'.git/index').read_bytes(),index)
        self.assertEqual((self.root/'target.txt').read_bytes(),target)

    def test_root_replacement_between_queries_rejected(self):
        action = self.action(); original = g._Reads.query
        def query(reads,which):
            raw = original(reads,which)
            if which is g._Read.IGNORE:
                # Replace namespace with an empty directory, retain old evidence.
                moved = self.root.parent/(self.root.name+'-moved')
                self.root.rename(moved); self.root.mkdir()
                self.addCleanup(lambda: (self.root.rmdir(), moved.rename(self.root)))
            return raw
        with patch.object(g._Reads,'query',query):
            self.reject(action)

    def test_git_directory_replacement_between_queries_rejected(self):
        action = self.action(); original = g._Reads.query
        def query(reads,which):
            raw = original(reads,which)
            if which is g._Read.IGNORE:
                (self.root/'.git').rename(self.root/'git-saved')
                (self.root/'.git').mkdir()
            return raw
        with patch.object(g._Reads,'query',query):
            self.reject(action)

    def test_include_fifo_not_followed(self):
        action = self.action()
        os.mkfifo(self.root/'included-config')
        self.git('config','include.path',str(self.root/'included-config'))
        with patch.object(g.subprocess,'Popen',wraps=g.subprocess.Popen) as spawn:
            self.assertEqual(self.reject(action),'unsafe config')
        self.assertEqual(spawn.call_count,1)
        self.assertIn('--no-includes',spawn.call_args.args[0])

    def _fake_git_failure(self, program, timeout=10):
        reads = g._Reads(self.action()); original = g.subprocess.Popen; owned = []
        def fake(argv, **kwargs):
            child = original([sys.executable,'-c',program],**kwargs)
            owned.append(child)
            return child
        with patch.object(g.subprocess,'Popen',side_effect=fake), patch.object(g,'GIT_TIMEOUT',timeout):
            with self.assertRaises(g.GitPreflightError) as caught:
                reads.query(g._Read.CONFIG)
        self.assertEqual(str(caught.exception),'Git ADD preflight rejected: Git inspection unavailable')
        self.assertIsNotNone(owned[0].returncode)

    def test_read_timeout_terminates_owned_group(self):
        self._fake_git_failure('import time; time.sleep(60)',timeout=0.1)

    def test_read_stdout_bound(self):
        self._fake_git_failure('import os; os.write(1,b"x"*'+str(g.METADATA_LIMIT+1)+')')

    def test_read_stderr_bound_and_omission(self):
        self._fake_git_failure('import os; os.write(2,b"PRIVATE_MARKER"*'+str(g.STDERR_LIMIT//14+2)+')')

    def test_read_nonzero_omits_stderr(self):
        self._fake_git_failure('import sys; sys.stderr.write("PRIVATE_MARKER"); sys.exit(7)')

    def test_index_truncation_and_unknown_version_fail_closed(self):
        raw = (self.root/'.git/index').read_bytes()
        for malformed in (b'', raw[:10], raw[:-1]):
            with self.assertRaises(g.GitPreflightError):
                g._parse_index(malformed,'sha1')
        prefix = bytearray(raw[:-20]); struct.pack_into('>I',prefix,4,5)
        forged = bytes(prefix)+hashlib.sha1(prefix).digest()
        with self.assertRaises(g.GitPreflightError):
            g._parse_index(forged,'sha1')

    def test_promisor_pack_marker_without_config_rejected(self):
        action = self.action()
        (self.root/'.git/objects/pack/synthetic.promisor').write_bytes(b'')
        self.reject(action)

    def test_compound_late_filter_and_attribute_change_never_executes_helper(self):
        # Deliberately compound helper-execution probe, not isolated evidence
        # for config or attribute drift (covered separately above).
        (self.root/'target.txt').write_bytes(b'changed!\n')  # same size as HEAD; forces content comparison
        action = self.action(); original = g._Reads.query
        marker = self.root/'PRIVATE_MARKER'
        def query(reads,which):
            raw = original(reads,which)
            if which is g._Read.ATTRIBUTES:
                (self.root/'.gitattributes').write_text('target.txt filter=late\n')
                self.git('config','filter.late.clean','touch '+str(marker)+'; cat')
            return raw
        with patch.object(g._Reads,'query',query):
            self.reject(action)
        self.assertFalse(marker.exists(), 'preflight must not execute a late clean filter')


class ReadCleanupTests(unittest.TestCase):
    """Fault-injected read queries; no repository/index mutation or supervisor."""
    def reads(self):
        return g._Reads(SimpleNamespace(
            repository=SimpleNamespace(canonical_root=str(Path(__file__).resolve().parents[1])),
            payload=SimpleNamespace(files=())))

    def exited_process(self, output=b'xx', error=b''):
        # Deterministically model an exited but unreaped leader. Real pipe FDs
        # still exercise registration, reads and explicit close, without relying
        # on macOS timing to provoke killpg PermissionError.
        streams = []
        for data in (output, error):
            read_fd, write_fd = os.pipe()
            stream = os.fdopen(read_fd, 'rb', buffering=0)
            self.addCleanup(stream.close)
            try:
                os.write(write_fd,data)
            finally:
                os.close(write_fd)
            streams.append(stream)
        class ExitedProcess:
            pid = 12345  # All signaling is mocked; never signal this number.
            returncode = None
            stdout, stderr = streams
            reaped = False
            kill_called = False
            def kill(self):
                self.kill_called = True
            def wait(self, timeout=None):
                self.reaped = True
                self.returncode = 0
                return 0
        return ExitedProcess()

    def assert_closed(self, child):
        self.assertTrue(child.stdout.closed)
        self.assertTrue(child.stderr.closed)
        for stream in (child.stdout,child.stderr):
            with self.assertRaises(ValueError):
                stream.fileno()

    def test_exited_leader_group_permission_error_preserves_output_bound_failure(self):
        for lane in ('stdout','stderr'):
            with self.subTest(lane=lane):
                child = self.exited_process(b'xx' if lane=='stdout' else b'',
                                            b'xx' if lane=='stderr' else b'')
                original_selector = g.selectors.DefaultSelector
                selected = []
                def selector():
                    selected.append(original_selector())
                    return selected[-1]
                with patch.object(g.subprocess,'Popen',return_value=child), \
                        patch.object(g.os,'killpg',side_effect=PermissionError('PRIVATE_MARKER')), \
                        patch.object(g,'METADATA_LIMIT',1), patch.object(g,'STDERR_LIMIT',1), \
                        patch.object(g.selectors,'DefaultSelector',side_effect=selector):
                    with self.assertRaises(g.GitPreflightError) as caught:
                        self.reads().query(g._Read.CONFIG)
                self.assertEqual(str(caught.exception),
                                 'Git ADD preflight rejected: Git inspection unavailable')
                self.assertTrue(child.kill_called)
                self.assertTrue(child.reaped)
                self.assert_closed(child)
                self.assertFalse(selected[0].get_map())

    def test_live_child_reaped_after_group_signal_failure(self):
        original = g.subprocess.Popen
        for fault in (PermissionError,ProcessLookupError):
            with self.subTest(fault=fault.__name__):
                owned = []
                def spawn(argv, **kwargs):
                    # A finite safety backstop; the assertion below requires
                    # SIGKILL, so natural expiry cannot make this test PASS.
                    child = original([sys.executable,'-c','import time; time.sleep(2)'],**kwargs)
                    owned.append(child)
                    return child
                def emergency_cleanup():
                    if owned and owned[0].returncode is None:
                        owned[0].kill(); owned[0].wait()
                self.addCleanup(emergency_cleanup)
                with patch.object(g.subprocess,'Popen',side_effect=spawn), \
                        patch.object(g.os,'killpg',side_effect=fault('PRIVATE_MARKER')), \
                        patch.object(g.time,'monotonic',side_effect=(0,20)):
                    with self.assertRaises(g.GitPreflightError) as caught:
                        self.reads().query(g._Read.CONFIG)
                self.assertEqual(caught.exception.category,'Git inspection unavailable')
                self.assertEqual(owned[0].returncode,-g.signal.SIGKILL)
                self.assert_closed(owned[0])
                with self.assertRaises(ChildProcessError):
                    os.waitpid(owned[0].pid,os.WNOHANG)

    def test_selector_setup_failure_still_reaps_and_closes(self):
        child = self.exited_process()
        with patch.object(g.subprocess,'Popen',return_value=child), \
                patch.object(g.selectors,'DefaultSelector',side_effect=OSError('PRIVATE_MARKER')), \
                patch.object(g.os,'killpg'):
            with self.assertRaises(g.GitPreflightError) as caught:
                self.reads().query(g._Read.CONFIG)
        self.assertEqual(caught.exception.category,'Git inspection unavailable')
        self.assertTrue(child.reaped)
        self.assert_closed(child)

    def test_cleanup_close_fault_cannot_replace_nonzero_head_failure(self):
        child = self.exited_process(b'')
        child.wait = lambda timeout=None: setattr(child,'returncode',7) or 7
        original_selector = g.selectors.DefaultSelector
        owned = []
        def selector():
            selected = original_selector()
            original_close = selected.close
            def close():
                original_close()
                raise OSError('PRIVATE_MARKER')
            selected.close = close
            owned.append(selected)
            return selected
        with patch.object(g.subprocess,'Popen',return_value=child), \
                patch.object(g.selectors,'DefaultSelector',side_effect=selector), \
                patch.object(g.os,'killpg'):
            with self.assertRaises(g.GitPreflightError) as caught:
                self.reads().query(g._Read.HEAD)
        self.assertEqual(caught.exception.category,'HEAD mismatch')
        self.assert_closed(child)
        self.assertFalse(owned[0].get_map())

    def test_stdout_close_fault_still_closes_stderr_and_preserves_primary(self):
        child = self.exited_process()
        original_close = child.stdout.close
        def close():
            original_close()
            raise OSError('PRIVATE_MARKER')
        with patch.object(g.subprocess,'Popen',return_value=child), \
                patch.object(g.os,'killpg'), patch.object(g,'METADATA_LIMIT',1), \
                patch.object(child.stdout,'close',side_effect=close):
            with self.assertRaises(g.GitPreflightError) as caught:
                self.reads().query(g._Read.CONFIG)
        self.assertEqual(caught.exception.category,'Git inspection unavailable')
        self.assertTrue(child.reaped)
        self.assert_closed(child)

    def test_wait_cleanup_fault_still_closes_pipes_and_preserves_primary(self):
        child = self.exited_process()
        original_wait = child.wait
        def wait(timeout=None):
            original_wait(timeout)
            raise OSError('PRIVATE_MARKER')
        with patch.object(g.subprocess,'Popen',return_value=child), \
                patch.object(g.os,'killpg'), patch.object(g,'METADATA_LIMIT',1), \
                patch.object(child,'wait',side_effect=wait):
            with self.assertRaises(g.GitPreflightError) as caught:
                self.reads().query(g._Read.CONFIG)
        self.assertEqual(caught.exception.category,'Git inspection unavailable')
        self.assertTrue(child.reaped)
        self.assert_closed(child)

    def test_direct_kill_cleanup_fault_still_reaps_and_preserves_primary(self):
        child = self.exited_process()
        with patch.object(g.subprocess,'Popen',return_value=child), \
                patch.object(g.os,'killpg',side_effect=PermissionError('PRIVATE_MARKER')), \
                patch.object(child,'kill',side_effect=PermissionError('PRIVATE_MARKER')), \
                patch.object(g,'METADATA_LIMIT',1):
            with self.assertRaises(g.GitPreflightError) as caught:
                self.reads().query(g._Read.CONFIG)
        self.assertEqual(caught.exception.category,'Git inspection unavailable')
        self.assertTrue(child.reaped)
        self.assert_closed(child)

    def test_cleanup_fault_on_success_is_sanitized(self):
        child = self.exited_process(b'')
        original_close = child.stderr.close
        def close():
            original_close()
            raise OSError('PRIVATE_MARKER')
        with patch.object(g.subprocess,'Popen',return_value=child), \
                patch.object(g.os,'killpg'), patch.object(child.stderr,'close',side_effect=close):
            with self.assertRaises(g.GitPreflightError) as caught:
                self.reads().query(g._Read.CONFIG)
        self.assertEqual(caught.exception.category,'Git inspection unavailable')
        self.assertTrue(child.reaped)
        self.assert_closed(child)


# Independent fixtures per state, so one early reject cannot mask another gate.
def _config_test(key, value):
    def test(self):
        action = self.action()
        self.git('config', key, value)
        before = (self.root/'.git/index').read_bytes()
        self.reject(action)
        self.assertEqual(before,(self.root/'.git/index').read_bytes())
    return test


for _name, _key, _value in (
    ('worktree','core.worktree','/PRIVATE_MARKER'),
    ('includeif','includeIf.gitdir:/PRIVATE_MARKER.path','/PRIVATE_MARKER'),
    ('worktree_config','extensions.worktreeConfig','true'),
    ('filemode','core.fileMode','false'),
    ('split','core.splitIndex','true'),
    ('sparse','core.sparseCheckout','true'),
    ('index_sparse','index.sparse','true'),
    ('partial','extensions.partialClone','x'),
    ('promisor','remote.x.promisor','true'),
    ('smudge','filter.x.smudge','PRIVATE_MARKER'),
    ('process','filter.x.process','PRIVATE_MARKER'),
    ('required','filter.x.required','false'),
):
    setattr(GitPreflightTests,'test_config_'+_name,_config_test(_key,_value))


def _state_test(name):
    def test(self):
        action = self.action()
        path = self.root/'.git'/name
        if name.startswith('rebase') or name == 'sequencer':
            path.mkdir()
        else:
            path.write_bytes(b'synthetic state')
        self.reject(action)
    return test


for _name in ('MERGE_HEAD','CHERRY_PICK_HEAD','REVERT_HEAD','BISECT_LOG','BISECT_START',
              'rebase-apply','rebase-merge','sequencer','sharedindex.synthetic','info/sparse-checkout'):
    setattr(GitPreflightTests,'test_state_'+_name.replace('-','_').replace('/','_').replace('.','_'),_state_test(_name))


def _flag_test(flag):
    def test(self):
        if flag == 'intent-to-add':
            (self.root/'new.txt').write_bytes(b'new')
            self.git('add','-N','new.txt')
        else:
            self.git('update-index','--'+flag,'other.txt')
        raw = (self.root/'.git/index').read_bytes()
        entries = g._parse_index(raw,'sha1')
        self.assertTrue(any(getattr(e,flag.replace('-','_')) for e in entries))
        self.assertEqual(self.reject(),'unsupported index state')
    return test


for _flag in ('skip-worktree','assume-unchanged','intent-to-add'):
    setattr(GitPreflightTests,'test_flag_'+_flag.replace('-','_'),_flag_test(_flag))


def _attr_test(attribute):
    def test(self):
        (self.root/'.gitattributes').write_text('target.txt '+attribute+'\n')
        self.reject()
    return test


for _name, _attribute in (('driverless_lfs','filter=lfs'),('ident','ident'),('crlf','crlf'),
    ('encoding','working-tree-encoding=UTF-16'),('text','text'),('eol','eol=lf')):
    setattr(GitPreflightTests,'test_attribute_'+_name,_attr_test(_attribute))


if __name__ == '__main__':
    unittest.main()
