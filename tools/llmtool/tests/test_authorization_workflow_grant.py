import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool import authorization_workflow_grant as grant
from llmtool import sensitive_paths

GRANT_PATH = 'tools/llmtool/workflow-grants.json'


class WorkflowGrantTests(unittest.TestCase):
    ISSUE = 2281
    PATH = '.github/workflows/ci.yml'
    OWNER = ('https://github.com/davidcatcat/davidcattoolsproject/'
             'issues/2281#issuecomment-1')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self._git('init', '-q')

    def tearDown(self):
        self.temp.cleanup()

    def _git(self, *arguments):
        return subprocess.run(
            ['git', '-C', str(self.root),
             '-c', 'user.name=Workflow Grant Test',
             '-c', 'user.email=test@example.invalid',
             '-c', 'commit.gpgsign=false', *arguments],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def _write(self, entries):
        path = self.root / GRANT_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'schema_version': 1, 'grants': entries}),
                        encoding='utf-8')

    def _commit(self, entries):
        self._write(entries)
        self._git('add', '--', GRANT_PATH)
        self._git('commit', '-q', '-m', 'fixture')
        return self._head()

    def _commit_raw(self, raw):
        path = self.root / GRANT_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        self._git('add', '--', GRANT_PATH)
        self._git('commit', '-q', '-m', 'fixture')
        return self._head()

    def _commit_without_grant_file(self):
        path = self.root / GRANT_PATH
        if path.exists():
            path.unlink()
        readme = self.root / 'README.md'
        readme.write_text('fixture\n', encoding='utf-8')
        self._git('add', '-A')
        self._git('commit', '-q', '-m', 'fixture')
        return self._head()

    def _head(self):
        return self._git('rev-parse', 'HEAD').stdout.decode('ascii').strip()

    def _entry(self, paths=None, issue=None, mode='write', owner=OWNER):
        return {
            'issue': self.ISSUE if issue is None else issue,
            'owner_record_url': owner,
            'mode': mode,
            'paths': list(paths or (self.PATH,)),
        }

    def _decide(self, base_commit, issue=None, path=None, mode='write'):
        return grant.evaluate_workflow_grant(
            base_commit=base_commit,
            issue=self.ISSUE if issue is None else issue,
            path=self.PATH if path is None else path,
            mode=mode,
            root=self.root)

    def test_real_rule_file_starts_empty(self):
        rule = json.loads(grant._grant_list_path().read_text(encoding='utf-8'))
        self.assertEqual(rule, {'schema_version': 1, 'grants': []})

    def test_real_rule_file_is_an_exact_sensitive_path(self):
        self.assertTrue(
            sensitive_paths.is_sensitive('tools/llmtool/workflow-grants.json'))

    def test_exact_path_is_granted_for_issue_and_mode_only(self):
        commit = self._commit([
            self._entry(),
            self._entry(['.github/workflows/release.yml'], mode='read'),
        ])
        allowed = self._decide(commit)
        read_allowed = self._decide(
            commit, path='.github/workflows/release.yml', mode='read')
        cases = (
            ('wrong mode', self._decide(commit, mode='read')),
            ('wrong write path', self._decide(
                commit, path='.github/workflows/release.yml')),
            ('other path', self._decide(
                commit, path='.github/workflows/other.yml')),
            ('other issue', self._decide(commit, issue=self.ISSUE + 1)),
        )
        self.assertTrue(allowed.allowed)
        self.assertIs(allowed.code, grant.WorkflowGrantCode.GRANTED)
        self.assertTrue(read_allowed.allowed)
        self.assertIs(read_allowed.code, grant.WorkflowGrantCode.GRANTED)
        for label, decision in cases:
            with self.subTest(case=label):
                self.assertFalse(decision.allowed)
                self.assertIs(decision.code, grant.WorkflowGrantCode.GRANT_NOT_FOUND)

    def test_grant_source_is_bound_to_the_base_commit(self):
        first = self._commit([self._entry()])
        # An uncommitted working-tree edit must not change the answer.
        self._write([])
        self.assertTrue(self._decide(first).allowed)
        second = self._commit([])
        self.assertIs(self._decide(second).code,
                      grant.WorkflowGrantCode.GRANT_NOT_FOUND)
        self.assertTrue(self._decide(first).allowed)

    def test_invalid_base_commit_is_refused(self):
        commit = self._commit([self._entry()])
        with self.subTest(case='unknown commit'):
            decision = self._decide('0' * 40)
            self.assertFalse(decision.allowed)
            self.assertIs(decision.code,
                          grant.WorkflowGrantCode.GRANT_BASE_COMMIT_INVALID)
        for value in (None, '', 'a' * 39, 'A' * 40, 'g' * 40, 123, True):
            with self.subTest(value=value):
                decision = self._decide(value)
                self.assertFalse(decision.allowed)
                self.assertIs(decision.code,
                              grant.WorkflowGrantCode.GRANT_BASE_COMMIT_INVALID)
        self.assertTrue(self._decide(commit).allowed)

    def test_unavailable_git_source_is_refused(self):
        commit = self._commit([self._entry()])
        with patch.object(grant, '_git', side_effect=OSError('git missing')):
            decision = self._decide(commit)
        self.assertFalse(decision.allowed)
        self.assertIs(decision.code,
                      grant.WorkflowGrantCode.GRANT_SOURCE_UNAVAILABLE)

    def test_case_confusion_is_refused(self):
        commit = self._commit([self._entry(['.github/workflows/CI.yml'])])
        decision = self._decide(commit, path='.github/workflows/ci.yml')
        self.assertFalse(decision.allowed)
        self.assertIs(decision.code, grant.WorkflowGrantCode.GRANT_PATH_CONFUSION)

    def test_unicode_normalization_confusion_is_refused(self):
        commit = self._commit([
            self._entry(['.github/workflows/caf\u00e9.yml']),
        ])
        decision = self._decide(
            commit, path='.github/workflows/cafe\u0301.yml')
        self.assertFalse(decision.allowed)
        self.assertIs(decision.code, grant.WorkflowGrantCode.GRANT_PATH_INVALID)

    def test_duplicate_and_dot_components_are_refused(self):
        commit = self._commit([self._entry()])
        decisions = (
            self._decide(commit, path='.github//workflows/ci.yml'),
            self._decide(commit, path='.github/./workflows/ci.yml'),
            self._decide(commit,
                         path='.github/workflows/../workflows/ci.yml'),
            self._decide(commit, path='/github/workflows/ci.yml'),
        )
        for decision in decisions:
            with self.subTest(code=decision.code):
                self.assertFalse(decision.allowed)
                self.assertIs(decision.code,
                              grant.WorkflowGrantCode.GRANT_PATH_INVALID)

    def test_only_top_level_workflow_files_are_paths(self):
        bad_paths = (
            '.github/workflows/nested/ci.yml',
            '.github/workflows/ci',
            '.github/workflows/ci.yml.bak',
            '.github/workflows/.yml',
            '.github/workflows/ci.YML',
        )
        commit = self._commit([self._entry()])
        for path in bad_paths:
            with self.subTest(query=path):
                decision = self._decide(commit, path=path)
                self.assertFalse(decision.allowed)
                self.assertIs(decision.code,
                              grant.WorkflowGrantCode.GRANT_PATH_INVALID)
        for path in bad_paths:
            with self.subTest(entry=path):
                bad_commit = self._commit([self._entry([path])])
                decision = self._decide(bad_commit)
                self.assertFalse(decision.allowed)
                self.assertIs(decision.code,
                              grant.WorkflowGrantCode.GRANT_FILE_MALFORMED)

    def test_prefix_and_cross_path_are_refused(self):
        commit = self._commit([self._entry()])
        prefixed = self._decide(commit, path=self.PATH + '/extra')
        cross_path = self._decide(
            commit, path='.github/workflows/other.yml')
        directory_grant = self._decide(
            commit, path='.github/workflows')
        self.assertFalse(prefixed.allowed)
        self.assertIs(prefixed.code, grant.WorkflowGrantCode.GRANT_PATH_INVALID)
        self.assertFalse(cross_path.allowed)
        self.assertIs(cross_path.code, grant.WorkflowGrantCode.GRANT_NOT_FOUND)
        self.assertFalse(directory_grant.allowed)
        self.assertIs(directory_grant.code,
                      grant.WorkflowGrantCode.GRANT_PATH_INVALID)

    def test_symlinked_component_is_refused(self):
        commit = self._commit([self._entry()])
        target = self.root / 'target'
        (target / 'workflows').mkdir(parents=True)
        (target / 'workflows' / 'ci.yml').write_text(
            'name: ci\n', encoding='utf-8')
        (self.root / '.github').symlink_to(target, target_is_directory=True)
        decision = self._decide(commit)
        self.assertFalse(decision.allowed)
        self.assertIs(decision.code, grant.WorkflowGrantCode.GRANT_PATH_SYMLINK)

    def test_missing_and_malformed_grant_files_are_refused(self):
        valid_entry = self._entry()
        malformed = (
            ('bad json', b'{'),
            ('top level list', b'[]'),
            ('wrong version', {'schema_version': 2, 'grants': []}),
            ('boolean version', {'schema_version': True, 'grants': []}),
            ('grants not a list', {'schema_version': 1, 'grants': {}}),
            ('unknown top field', {'schema_version': 1, 'grants': [],
                                   'extra': 1}),
            ('unknown entry field', {'schema_version': 1, 'grants': [
                {**valid_entry, 'extra': 1}]}),
            ('missing mode', {'schema_version': 1, 'grants': [
                {key: value for key, value in valid_entry.items()
                 if key != 'mode'}]}),
            ('bad issue', {'schema_version': 1, 'grants': [
                {**valid_entry, 'issue': True}]}),
            ('bad mode', {'schema_version': 1, 'grants': [
                {**valid_entry, 'mode': 'admin'}]}),
            ('empty paths', {'schema_version': 1, 'grants': [
                {**valid_entry, 'paths': []}]}),
            ('bad entry path', {'schema_version': 1, 'grants': [
                {**valid_entry, 'paths': ['docs/ci.yml']}]}),
            ('duplicate grant', {'schema_version': 1, 'grants': [
                valid_entry, valid_entry]}),
            ('colliding paths', {'schema_version': 1, 'grants': [
                {**valid_entry,
                 'paths': [self.PATH, '.github/workflows/CI.yml']}]}),
        )
        missing = self._commit_without_grant_file()
        results = []
        for label, content in malformed:
            with self.subTest(case=label):
                raw = content if isinstance(content, bytes) else json.dumps(
                    content).encode('utf-8')
                results.append(self._decide(self._commit_raw(raw)))
        missing_decision = self._decide(missing)
        self.assertFalse(missing_decision.allowed)
        self.assertIs(missing_decision.code,
                      grant.WorkflowGrantCode.GRANT_FILE_MISSING)
        for decision in results:
            self.assertFalse(decision.allowed)
            self.assertIs(decision.code,
                          grant.WorkflowGrantCode.GRANT_FILE_MALFORMED)

    def test_owner_record_url_is_required(self):
        cases = (
            ('missing key', {key: value for key, value in self._entry().items()
                             if key != 'owner_record_url'}),
            ('empty', self._entry(owner='')),
            ('null', self._entry(owner=None)),
        )
        missing = []
        for label, entry in cases:
            with self.subTest(case=label):
                missing.append(self._decide(self._commit([entry])))
        invalid = []
        for value in ('owner-record', 'http://github.com/owner/record',
                      'https://github.com/'):
            with self.subTest(value=value):
                invalid.append(self._decide(
                    self._commit([self._entry(owner=value)])))
        for decision in missing:
            self.assertFalse(decision.allowed)
            self.assertIs(decision.code,
                          grant.WorkflowGrantCode.GRANT_OWNER_RECORD_MISSING)
        for decision in invalid:
            self.assertFalse(decision.allowed)
            self.assertIs(decision.code,
                          grant.WorkflowGrantCode.GRANT_OWNER_RECORD_INVALID)

    def test_invalid_issue_and_mode_are_refused(self):
        commit = self._commit([self._entry()])
        bad_issues = [
            grant.evaluate_workflow_grant(
                base_commit=commit, issue=value, path=self.PATH, mode='write',
                root=self.root)
            for value in (True, 0, -1, '2281', None)
        ]
        bad_modes = [
            grant.evaluate_workflow_grant(
                base_commit=commit, issue=self.ISSUE, path=self.PATH, mode=value,
                root=self.root)
            for value in ('', 'admin', None)
        ]
        for decision in bad_issues:
            self.assertFalse(decision.allowed)
            self.assertIs(decision.code,
                          grant.WorkflowGrantCode.GRANT_ISSUE_INVALID)
        for decision in bad_modes:
            self.assertFalse(decision.allowed)
            self.assertIs(decision.code,
                          grant.WorkflowGrantCode.GRANT_MODE_INVALID)


if __name__ == '__main__':
    unittest.main()
