import json
import tempfile
import unittest
from pathlib import Path

from llmtool import authorization_workflow_grant as grant
from llmtool import sensitive_paths


class WorkflowGrantTests(unittest.TestCase):
    ISSUE = 2281
    PATH = '.github/workflows/ci.yml'
    OWNER = ('https://github.com/davidcatcat/davidcattoolsproject/'
             'issues/2281#issuecomment-1')

    def _write(self, directory, entries):
        path = Path(directory) / 'workflow-grants.json'
        path.write_text(json.dumps({'schema_version': 1, 'grants': entries}),
                        encoding='utf-8')
        return path

    def _entry(self, paths=None, issue=None, mode='write', owner=OWNER):
        return {
            'issue': self.ISSUE if issue is None else issue,
            'owner_record_url': owner,
            'mode': mode,
            'paths': list(paths or (self.PATH,)),
        }

    def _decide(self, grants_path, issue=None, path=None, mode='write', root=None):
        return grant.evaluate_workflow_grant(
            issue=self.ISSUE if issue is None else issue,
            path=self.PATH if path is None else path,
            mode=mode,
            grants_path=grants_path,
            root=Path(grants_path).parent if root is None else root)

    def test_real_rule_file_starts_empty(self):
        rule = json.loads(grant._grant_list_path().read_text(encoding='utf-8'))
        self.assertEqual(rule, {'schema_version': 1, 'grants': []})
        with tempfile.TemporaryDirectory() as directory:
            decision = self._decide(None, root=Path(directory))
        self.assertFalse(decision.allowed)
        self.assertIs(decision.code, grant.WorkflowGrantCode.GRANT_NOT_FOUND)

    def test_real_rule_file_is_an_exact_sensitive_path(self):
        self.assertTrue(
            sensitive_paths.is_sensitive('tools/llmtool/workflow-grants.json'))

    def test_exact_path_is_granted_for_issue_and_mode_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(directory, [
                self._entry(),
                self._entry(['.github/workflows/release.yml'], mode='read'),
            ])
            allowed = self._decide(path)
            read_allowed = self._decide(
                path, path='.github/workflows/release.yml', mode='read')
            cases = (
                ('wrong mode', self._decide(path, mode='read')),
                ('wrong write path', self._decide(
                    path, path='.github/workflows/release.yml')),
                ('other path', self._decide(
                    path, path='.github/workflows/other.yml')),
                ('other issue', self._decide(path, issue=self.ISSUE + 1)),
            )
        self.assertTrue(allowed.allowed)
        self.assertIs(allowed.code, grant.WorkflowGrantCode.GRANTED)
        self.assertTrue(read_allowed.allowed)
        self.assertIs(read_allowed.code, grant.WorkflowGrantCode.GRANTED)
        for label, decision in cases:
            with self.subTest(case=label):
                self.assertFalse(decision.allowed)
                self.assertIs(decision.code, grant.WorkflowGrantCode.GRANT_NOT_FOUND)

    def test_case_confusion_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(directory, [
                self._entry(['.github/workflows/CI.yml']),
            ])
            decision = self._decide(path, path='.github/workflows/ci.yml')
        self.assertFalse(decision.allowed)
        self.assertIs(decision.code, grant.WorkflowGrantCode.GRANT_PATH_CONFUSION)

    def test_unicode_normalization_confusion_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(directory, [
                self._entry(['.github/workflows/caf\u00e9.yml']),
            ])
            decision = self._decide(
                path, path='.github/workflows/cafe\u0301.yml')
        self.assertFalse(decision.allowed)
        self.assertIs(decision.code, grant.WorkflowGrantCode.GRANT_PATH_INVALID)

    def test_duplicate_and_dot_components_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(directory, [self._entry()])
            decisions = (
                self._decide(path, path='.github//workflows/ci.yml'),
                self._decide(path, path='.github/./workflows/ci.yml'),
                self._decide(path,
                             path='.github/workflows/../workflows/ci.yml'),
                self._decide(path, path='/github/workflows/ci.yml'),
            )
        for decision in decisions:
            with self.subTest(code=decision.code):
                self.assertFalse(decision.allowed)
                self.assertIs(decision.code,
                              grant.WorkflowGrantCode.GRANT_PATH_INVALID)

    def test_prefix_and_cross_path_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(directory, [self._entry()])
            prefixed = self._decide(path, path=self.PATH + '/extra')
            cross_path = self._decide(
                path, path='.github/workflows/other.yml')
            directory_grant = self._decide(
                path, path='.github/workflows')
        self.assertFalse(prefixed.allowed)
        self.assertIs(prefixed.code, grant.WorkflowGrantCode.GRANT_PATH_PREFIX)
        self.assertFalse(cross_path.allowed)
        self.assertIs(cross_path.code, grant.WorkflowGrantCode.GRANT_NOT_FOUND)
        self.assertFalse(directory_grant.allowed)
        self.assertIs(directory_grant.code,
                      grant.WorkflowGrantCode.GRANT_PATH_INVALID)

    def test_symlinked_component_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'target'
            (target / 'workflows').mkdir(parents=True)
            (target / 'workflows' / 'ci.yml').write_text(
                'name: ci\n', encoding='utf-8')
            (root / '.github').symlink_to(target, target_is_directory=True)
            path = self._write(root, [self._entry()])
            decision = self._decide(path, root=root)
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
        with tempfile.TemporaryDirectory() as directory:
            missing = self._decide(Path(directory) / 'absent.json')
            results = []
            for label, content in malformed:
                with self.subTest(case=label):
                    path = Path(directory) / 'workflow-grants.json'
                    if isinstance(content, bytes):
                        path.write_bytes(content)
                    else:
                        path.write_text(json.dumps(content), encoding='utf-8')
                    results.append(self._decide(path))
        self.assertFalse(missing.allowed)
        self.assertIs(missing.code, grant.WorkflowGrantCode.GRANT_FILE_MISSING)
        for decision in results:
            self.assertFalse(decision.allowed)
            self.assertIs(decision.code,
                          grant.WorkflowGrantCode.GRANT_FILE_MALFORMED)

    def test_owner_record_url_is_required(self):
        with tempfile.TemporaryDirectory() as directory:
            cases = (
                ('missing key', {key: value for key, value in self._entry().items()
                                 if key != 'owner_record_url'}),
                ('empty', self._entry(owner='')),
                ('null', self._entry(owner=None)),
            )
            missing = []
            for label, entry in cases:
                with self.subTest(case=label):
                    path = self._write(directory, [entry])
                    missing.append(self._decide(path))
            invalid = []
            for value in ('owner-record', 'http://github.com/owner/record',
                          'https://github.com/'):
                with self.subTest(value=value):
                    path = self._write(directory, [self._entry(owner=value)])
                    invalid.append(self._decide(path))
        for decision in missing:
            self.assertFalse(decision.allowed)
            self.assertIs(decision.code,
                          grant.WorkflowGrantCode.GRANT_OWNER_RECORD_MISSING)
        for decision in invalid:
            self.assertFalse(decision.allowed)
            self.assertIs(decision.code,
                          grant.WorkflowGrantCode.GRANT_OWNER_RECORD_INVALID)

    def test_invalid_issue_and_mode_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(directory, [self._entry()])
            bad_issues = [
                grant.evaluate_workflow_grant(
                    issue=value, path=self.PATH, mode='write',
                    grants_path=path, root=Path(directory))
                for value in (True, 0, -1, '2281', None)
            ]
            bad_modes = [
                grant.evaluate_workflow_grant(
                    issue=self.ISSUE, path=self.PATH, mode=value,
                    grants_path=path, root=Path(directory))
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
