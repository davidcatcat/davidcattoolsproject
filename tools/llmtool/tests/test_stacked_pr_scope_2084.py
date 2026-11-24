"""#2084: a trusted-main merge may carry a stacked chain's write paths.

The chain is caller-supplied; these tests build real temporary Git histories and
a D6 registration so the Git-side scope check is exercised end to end.
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from llmtool import task_completion
from llmtool import task_completion_part5
from llmtool.errors import LLMToolError

_ENV = {
    'PATH': '/usr/bin:/bin',
    'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
    'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.invalid',
    'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.invalid',
    'GIT_TERMINAL_PROMPT': '0',
}
_TASK_ID = 'stacked-2084-task'
_AGENT = 'opencode'
_SESSION = 'session-1'
_FINGERPRINT = 'cpython3.13-testbase-local-0123456789ab'


class StackedPrScopeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = str(Path(temporary.name).resolve())
        os.chmod(self.root, 0o700)
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 't')
        self.git('config', 'user.email', 't@example.invalid')
        self.write('.gitignore', 'tools/\ntools/llmtool/.d6-bootstrap-report.json\n')
        self.write('a.txt', 'header\nmiddle\nkeep1\nkeep2\nkeep3\nfooter\n')
        self.write('b.txt', 'b0\n')
        self.write('c.txt', 'c0\n')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'base')
        self.base = self.git('rev-parse', 'HEAD')
        self.common = self.git('rev-parse', '--path-format=absolute',
                               '--git-common-dir')

    # -- Git and D6 helpers -------------------------------------------------
    def git_run(self, *args, check=True):
        return subprocess.run(
            ['/usr/bin/git', '-C', self.root, *args], env=_ENV, check=check,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)

    def git(self, *args):
        return self.git_run(*args).stdout.strip()

    def write(self, relative, content):
        path = Path(self.root).joinpath(*relative.split('/'))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')

    def commit_file(self, relative, content, message):
        self.write(relative, content)
        self.git('add', '-A')
        self.git('commit', '-q', '-m', message)
        return self.git('rev-parse', 'HEAD')

    def branch(self):
        return self.git('branch', '--show-current')

    def register_d6(self, *, source, head, branch):
        registry = Path(self.common) / 'llmtool-worktrees'
        registry.mkdir(mode=0o700)
        os.chmod(registry, 0o700)
        identity = os.stat(self.root)
        report_path = (Path(self.root) / 'tools' / 'llmtool' /
                       '.d6-bootstrap-report.json')
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report = {
            'result': 'PASS', 'task_id': _TASK_ID, 'root_realpath': self.root,
            'head_sha': head, 'tooling_sha': source,
            'cache_fingerprint': _FINGERPRINT,
        }
        raw = json.dumps(report, sort_keys=True).encode('utf-8')
        report_path.write_bytes(raw)
        os.chmod(report_path, 0o600)
        marker = {
            'schema_version': 1, 'result': 'PASS', 'task_id': _TASK_ID,
            'root_realpath': self.root, 'submitted_head_sha': head,
            'current_head_sha': head, 'tooling_sha': source,
            'environment_fingerprint': _FINGERPRINT,
            'report_sha256': hashlib.sha256(raw).hexdigest(),
        }
        record = {
            'schema_version': 1, 'task_id': _TASK_ID, 'state': 'ACTIVE',
            'lock_state': 'LOCKED', 'git_common_dir': self.common,
            'trusted_agent_id': _AGENT, 'session_ref': _SESSION,
            'root_realpath': self.root, 'branch': branch,
            'base_sha': source, 'head_sha': head,
            'physical_identity': {
                'device': identity.st_dev, 'inode': identity.st_ino,
                'uid': identity.st_uid,
            },
            'env2_pass_marker': marker,
        }
        record_path = registry / (_TASK_ID + '.json')
        record_path.write_text(json.dumps(record), encoding='utf-8')
        os.chmod(record_path, 0o600)

    def artifact(self, source, head, trusted_base_sha):
        tip = task_completion._developer_tip(
            self.root, source, head, trusted_base_sha)
        raw = task_completion._git(
            self.root, ('diff', '--binary', '--no-ext-diff', '--no-textconv',
                        '--no-renames', source, tip, '--'),
            limit=task_completion._GIT_LIMIT)
        text = raw.decode('utf-8')
        return text, hashlib.sha256(text.encode('utf-8')).hexdigest()

    def collect(self, *, source, head, branch, write_paths, trusted_base_sha,
                stacked_chain=None, artifact_segments=None):
        artifact, digest = self.artifact(source, head, trusted_base_sha)
        return task_completion.collect_executor_result(
            task_id=_TASK_ID, expected_common_dir=self.common,
            expected_root=self.root, expected_branch=branch,
            source_sha=source,
            task_binding={'task_id': _TASK_ID, 'run_id': 'run-1',
                          'contract_digest': 'd' * 64},
            expected_agent=_AGENT, expected_session_ref=_SESSION,
            expected_native_session_id=None, write_paths=write_paths,
            artifact=artifact, artifact_sha256=digest,
            artifact_segments=artifact_segments,
            trusted_base_sha=trusted_base_sha, stacked_chain=stacked_chain)

    def code(self, call):
        with self.assertRaises(LLMToolError) as caught:
            call()
        return caught.exception.completion_code

    def normalize(self, chain, bound_source):
        return task_completion_part5._normalize_stacked_chain(
            self.root, chain, bound_source)

    def scope_code(self, chain, *, source, result, write_paths,
                   trusted_base_sha, bound_source=None):
        normalized = self.normalize(chain, source if bound_source is None
                                    else bound_source)
        return self.code(lambda: task_completion_part5._stacked_scope_check(
            self.root, normalized, source, result, write_paths,
            trusted_base_sha))

    # -- accepted stacked case ---------------------------------------------
    def _two_step_with_update_branch(self):
        self.git('checkout', '-q', '-b', 'task1')
        task1 = self.commit_file(
            'a.txt', 'header\ntask1\nkeep1\nkeep2\nkeep3\nfooter\n', 'task1')
        self.git('checkout', '-q', '-b', 'task2')
        self.commit_file('b.txt', 'b1\n', 'task2')
        self.git('checkout', '-q', 'main')
        main_sha = self.commit_file(
            'a.txt', 'header\nmiddle\nkeep1\nkeep2\nkeep3\nmain\n', 'main moves')
        self.git('checkout', '-q', 'task2')
        self.git('merge', '-q', '--no-edit', 'main')
        return task1, main_sha, self.git('rev-parse', 'HEAD')

    def test_stacked_update_branch_accepted_with_chain_refused_without(self):
        task1, main_sha, head = self._two_step_with_update_branch()
        branch = self.branch()
        self.register_d6(source=task1, head=head, branch=branch)
        chain = [{'source_sha': self.base, 'result_sha': task1,
                  'write_paths': ['a.txt']}]

        accepted = self.collect(
            source=task1, head=head, branch=branch, write_paths=['b.txt'],
            trusted_base_sha=main_sha, stacked_chain=chain)

        self.assertEqual(accepted['stacked_chain_used'], True)
        self.assertEqual(accepted['stacked_chain'], chain)
        self.assertEqual(accepted['developer_changed_paths'], ['a.txt', 'b.txt'])
        self.assertEqual(self.code(lambda: self.collect(
            source=task1, head=head, branch=branch, write_paths=['b.txt'],
            trusted_base_sha=main_sha)), 'RESULT_SCOPE_MISMATCH')
        self.assertEqual(self.code(lambda: self.collect(
            source=task1, head=head, branch=branch, write_paths=['b.txt'],
            trusted_base_sha=main_sha, stacked_chain=chain,
            artifact_segments=[])), 'DEVELOPER_ARTIFACT_CHAIN_INVALID')

    def test_stacked_update_branch_resolution_accepted_with_chain(self):
        self.git('checkout', '-q', '-b', 'task1')
        task1 = self.commit_file(
            'a.txt', 'header\ntask1\nkeep1\nkeep2\nkeep3\nfooter\n', 'task1')
        self.git('checkout', '-q', '-b', 'task2')
        self.commit_file('b.txt', 'b1\n', 'task2')
        self.git('checkout', '-q', 'main')
        main_sha = self.commit_file(
            'a.txt', 'header\nmain\nkeep1\nkeep2\nkeep3\nfooter\n', 'main moves')
        self.git('checkout', '-q', 'task2')
        self.git_run('merge', '--no-edit', 'main', check=False)
        self.write('a.txt', 'header\nresolved\nkeep1\nkeep2\nkeep3\nfooter\n')
        self.git('add', 'a.txt')
        self.git('commit', '-q', '--no-edit')
        head = self.git('rev-parse', 'HEAD')
        branch = self.branch()
        self.register_d6(source=task1, head=head, branch=branch)
        chain = [{'source_sha': self.base, 'result_sha': task1,
                  'write_paths': ['a.txt']}]

        accepted = self.collect(
            source=task1, head=head, branch=branch, write_paths=['b.txt'],
            trusted_base_sha=main_sha, stacked_chain=chain)

        self.assertEqual(accepted['stacked_chain_used'], True)
        self.assertEqual(accepted['developer_changed_paths'], ['a.txt', 'b.txt'])
        self.assertEqual(self.code(lambda: self.collect(
            source=task1, head=head, branch=branch, write_paths=['b.txt'],
            trusted_base_sha=main_sha)), 'RESULT_SCOPE_MISMATCH')

    def test_single_task_without_chain_result_is_unchanged(self):
        self.git('checkout', '-q', '-b', 'dev')
        head = self.commit_file(
            'a.txt', 'header\ntask1\nkeep1\nkeep2\nkeep3\nfooter\n', 'dev')
        branch = self.branch()
        self.register_d6(source=self.base, head=head, branch=branch)
        without = self.collect(
            source=self.base, head=head, branch=branch,
            write_paths=['a.txt'], trusted_base_sha=self.base)
        explicit_none = self.collect(
            source=self.base, head=head, branch=branch,
            write_paths=['a.txt'], trusted_base_sha=self.base,
            stacked_chain=None)
        self.assertEqual(without, explicit_none)
        self.assertNotIn('stacked_chain', without)
        self.assertNotIn('stacked_chain_used', without)
        self.assertEqual(without['developer_changed_paths'], ['a.txt'])

    # -- malformed / broken chain ------------------------------------------
    def test_malformed_and_broken_chains_refuse(self):
        self.git('checkout', '-q', '-b', 'task1')
        task1 = self.commit_file('a.txt', 'task1\n', 'task1')
        self.git('checkout', '-q', '-b', 'task2')
        task2 = self.commit_file('b.txt', 'b1\n', 'task2')
        valid = {'source_sha': self.base, 'result_sha': task1,
                 'write_paths': ['a.txt']}
        too_many = ['p%d' % index for index in range(1025)]
        cases = {
            'none': None,
            'empty': [],
            'not a list': (valid,),
            'entry not a dict': [None],
            'missing key': [{'source_sha': self.base, 'result_sha': task1}],
            'extra key': [{**valid, 'extra': 1}],
            'bad source': [{**valid, 'source_sha': 'x' * 40}],
            'bad result': [{**valid, 'result_sha': '0' * 39}],
            'empty range': [{**valid, 'result_sha': self.base}],
            'write paths empty': [{**valid, 'write_paths': []}],
            'write paths not a list': [{**valid, 'write_paths': 'a.txt'}],
            'duplicate path': [{**valid, 'write_paths': ['a.txt', 'a.txt']}],
            'non-string path': [{**valid, 'write_paths': [1]}],
            'absolute path': [{**valid, 'write_paths': ['/a.txt']}],
            'parent path': [{**valid, 'write_paths': ['a/../b']}],
            'backslash path': [{**valid, 'write_paths': ['a\\b']}],
            'nul path': [{**valid, 'write_paths': ['a\x00b']}],
            'too long path': [{**valid, 'write_paths': ['a' * 513]}],
            'control path': [{**valid, 'write_paths': ['a\nb']}],
            'non-ascii path': [{**valid, 'write_paths': ['a\u00e9b']}],
            'too many paths': [{**valid, 'write_paths': too_many}],
            'broken link': [valid, {'source_sha': task2, 'result_sha': task1,
                                    'write_paths': ['b.txt']}],
            'last result not bound source': [
                {'source_sha': self.base, 'result_sha': task2,
                 'write_paths': ['a.txt']}],
            'source not ancestor': [
                {'source_sha': task2, 'result_sha': task1,
                 'write_paths': ['a.txt']}],
        }
        for label, chain in cases.items():
            with self.subTest(case=label):
                self.assertEqual(self.code(lambda: self.normalize(chain, task1)),
                                 'RESULT_SCOPE_MISMATCH')

    # -- per-Task and merge scope ------------------------------------------
    def test_earlier_task_commit_outside_its_paths_refuses(self):
        self.git('checkout', '-q', '-b', 'task1')
        self.commit_file('a.txt', 'task1\n', 'task1 a')
        task1 = self.commit_file('x.txt', 'x\n', 'task1 x')
        self.git('checkout', '-q', '-b', 'task2')
        head = self.commit_file('b.txt', 'b1\n', 'task2 b')
        chain = [{'source_sha': self.base, 'result_sha': task1,
                  'write_paths': ['a.txt']}]
        self.assertEqual(self.scope_code(
            chain, source=task1, result=head, write_paths=['b.txt'],
            trusted_base_sha=self.base), 'RESULT_SCOPE_MISMATCH')

    def test_bound_non_merge_cannot_use_an_earlier_path(self):
        self.git('checkout', '-q', '-b', 'task1')
        task1 = self.commit_file('a.txt', 'task1\n', 'task1 a')
        self.git('checkout', '-q', '-b', 'task2')
        head = self.commit_file('a.txt', 'task2 changes a\n', 'task2 a')
        chain = [{'source_sha': self.base, 'result_sha': task1,
                  'write_paths': ['a.txt']}]
        self.assertEqual(self.scope_code(
            chain, source=task1, result=head, write_paths=['b.txt'],
            trusted_base_sha=self.base), 'RESULT_SCOPE_MISMATCH')

    def test_chain_result_not_in_head_history_refuses(self):
        self.git('checkout', '-q', '-b', 'task1')
        task1 = self.commit_file('a.txt', 'task1\n', 'task1 a')
        self.git('checkout', '-q', 'main')
        main_sha = self.commit_file('c.txt', 'c1\n', 'main moves')
        chain = [{'source_sha': self.base, 'result_sha': task1,
                  'write_paths': ['a.txt']}]
        self.assertEqual(self.scope_code(
            chain, source=task1, result=main_sha, write_paths=['b.txt'],
            trusted_base_sha=self.base), 'RESULT_SCOPE_MISMATCH')

    def test_merge_second_parent_not_trusted_main_gets_no_union(self):
        self.git('checkout', '-q', '-b', 'task1')
        task1 = self.commit_file('a.txt', 'task1\n', 'task1 a')
        self.git('checkout', '-q', '-b', 'task2')
        self.commit_file('b.txt', 'b1\n', 'task2 b')
        self.git('checkout', '-q', '-b', 'side', task1)
        self.commit_file('a.txt', 'side a\n', 'side a')
        self.git('checkout', '-q', 'task2')
        self.git('merge', '-q', '--no-edit', 'side')
        head = self.git('rev-parse', 'HEAD')
        chain = [{'source_sha': self.base, 'result_sha': task1,
                  'write_paths': ['a.txt']}]
        self.assertEqual(self.scope_code(
            chain, source=task1, result=head, write_paths=['b.txt'],
            trusted_base_sha=self.base), 'RESULT_SCOPE_MISMATCH')

    def test_merge_path_outside_every_chain_task_refuses(self):
        self.git('checkout', '-q', '-b', 'task1')
        task1 = self.commit_file('a.txt', 'task1\n', 'task1 a')
        self.git('checkout', '-q', '-b', 'task2')
        self.commit_file('b.txt', 'b1\n', 'task2 b')
        self.git('checkout', '-q', 'main')
        main_sha = self.commit_file('a.txt', 'main a\n', 'main a')
        self.git('checkout', '-q', 'task2')
        self.git_run('merge', '--no-edit', 'main', check=False)
        self.write('a.txt', 'resolved merge\n')
        self.write('d.txt', 'd\n')
        self.git('add', 'a.txt', 'd.txt')
        self.git('commit', '-q', '--no-edit')
        head = self.git('rev-parse', 'HEAD')
        chain = [{'source_sha': self.base, 'result_sha': task1,
                  'write_paths': ['a.txt']}]
        self.assertEqual(self.scope_code(
            chain, source=task1, result=head, write_paths=['b.txt'],
            trusted_base_sha=main_sha), 'RESULT_SCOPE_MISMATCH')

    def test_main_only_change_in_merge_is_not_attributed(self):
        self.git('checkout', '-q', '-b', 'task1')
        task1 = self.commit_file('a.txt', 'task1\n', 'task1 a')
        self.git('checkout', '-q', '-b', 'task2')
        self.commit_file('b.txt', 'b1\n', 'task2 b')
        self.git('checkout', '-q', 'main')
        main_sha = self.commit_file('c.txt', 'c1\n', 'main c')
        self.git('checkout', '-q', 'task2')
        self.git('merge', '-q', '--no-edit', 'main')
        head = self.git('rev-parse', 'HEAD')
        self.assertEqual(task_completion._developer_changed_paths(
            self.root, task1, head, main_sha), ('b.txt',))
        chain = [{'source_sha': self.base, 'result_sha': task1,
                  'write_paths': ['a.txt']}]
        normalized = self.normalize(chain, task1)
        task_completion_part5._stacked_scope_check(
            self.root, normalized, task1, head, ['b.txt'], main_sha)


if __name__ == '__main__':
    unittest.main()
