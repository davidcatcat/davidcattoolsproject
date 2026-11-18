"""#2281 W3: workflow grants on the shared Developer route request contract.

The grant source is always the merged W1 module. These tests build a throwaway
Git repository with a reviewed rule-file fixture, so no real grant, credential
or repository file is used.
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool import authorization_workflow_grant
from llmtool.developer_routes.base import (
    DeveloperRoute, RouteError, WORKFLOW_GRANT_REFUSED)

GRANT_PATH = 'tools/llmtool/workflow-grants.json'
PATH = '.github/workflows/ci.yml'
OTHER = '.github/workflows/other.yml'
RELEASE = '.github/workflows/release.yml'
ISSUE = 2281
OWNER = ('https://github.com/davidcatcat/davidcattoolsproject/'
         'issues/2281#issuecomment-1')
ROUTE_NAMES = ('agy', 'claude', 'gw-agent')


class _TestRoute(DeveloperRoute):
    """Minimal adapter: only the shared request validation is under test."""

    def __init__(self, name):
        super().__init__({})
        self._name = name

    @property
    def name(self):
        return self._name

    @property
    def worker_key(self):
        return self._name

    @property
    def family(self):
        return 'test'

    def validate_profile(self, model, effort):
        if model != 'test-model' or effort != 'test-effort':
            raise RouteError('TEST_PROFILE_INVALID')
        return model, effort

    def build_argv(self, executable, request):
        return []

    def build_env(self, tmpdir):
        return {}

    def parse_output(self, stdout, stderr, log_text):
        return {}

    def verified_model(self, log_text, requested):
        return requested

    def quota_exhausted(self, parsed, stderr):
        return False


class RouteWorkflowGrantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self._git('init', '-q')
        (self.root / 'README.md').write_text('fixture\n', encoding='utf-8')
        self._git('add', '--', 'README.md')
        self._git('commit', '-q', '-m', 'fixture')

    def tearDown(self):
        self.temp.cleanup()

    def _git(self, *arguments):
        return subprocess.run(
            ['git', '-C', str(self.root),
             '-c', 'user.name=Workflow Grant Route Test',
             '-c', 'user.email=test@example.invalid',
             '-c', 'commit.gpgsign=false', *arguments],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def _entry(self, paths=None, issue=None, mode='write', owner=OWNER):
        return {
            'issue': ISSUE if issue is None else issue,
            'owner_record_url': owner,
            'mode': mode,
            'paths': list(paths or (PATH,)),
        }

    def _commit(self, entries):
        path = self.root / GRANT_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'schema_version': 1, 'grants': entries}),
                        encoding='utf-8')
        self._git('add', '--', GRANT_PATH)
        self._git('commit', '-q', '-m', 'grant fixture')
        return self._head()

    def _head(self):
        return self._git('rev-parse', 'HEAD').stdout.decode('ascii').strip()

    def _request(self, write_paths=(), read_paths=None, issue=ISSUE,
                 task=None, head=None):
        value = {
            'root': str(self.root),
            'model': 'test-model',
            'effort': 'test-effort',
            'expected_branch': 'main',
            'expected_head': head or self._head(),
            'write_paths': list(write_paths),
            'executable': '/bin/true',
            'issue': issue,
        }
        if read_paths is not None:
            value['read_paths'] = list(read_paths)
        if task is not None:
            value['task'] = task
        return value

    def _validate(self, name='claude', **kwargs):
        return _TestRoute(name).validate_request(self._request(**kwargs))

    def _assert_refused(self, code=None, name='claude', **kwargs):
        with self.assertRaises(RouteError) as caught:
            self._validate(name=name, **kwargs)
        if code is not None:
            self.assertEqual(caught.exception.code, code)
        return caught.exception

    def _assert_grant_refused(self, facts_code=None, name='claude', **kwargs):
        exc = self._assert_refused(WORKFLOW_GRANT_REFUSED, name=name, **kwargs)
        if facts_code is not None:
            self.assertEqual(exc.observed_facts.get('workflow_grant_code'),
                             facts_code)
        return exc

    def test_granted_workflow_path_is_accepted_for_exact_path_and_mode(self):
        self._commit([
            self._entry(mode='write'),
            self._entry([RELEASE], mode='read'),
        ])
        for name in ROUTE_NAMES:
            with self.subTest(route=name, mode='write'):
                value = self._validate(name, write_paths=[PATH])
                self.assertEqual(value['write_paths'], (PATH,))
            with self.subTest(route=name, mode='read'):
                value = self._validate(name, read_paths=[RELEASE])
                self.assertEqual(value['write_paths'], ())
            with self.subTest(route=name, mode='task-read'):
                value = self._validate(
                    name, task={'read_paths': [RELEASE], 'write_paths': []})
                self.assertEqual(value['write_paths'], ())

    def test_granted_path_is_not_accepted_for_other_path_or_mode(self):
        self._commit([
            self._entry(mode='write'),
            self._entry([RELEASE], mode='read'),
        ])
        cases = (
            ('other write path', {'write_paths': [OTHER]}, 'GRANT_NOT_FOUND'),
            ('read mode on write grant', {'read_paths': [PATH]},
             'GRANT_NOT_FOUND'),
            ('write mode on read grant', {'write_paths': [RELEASE]},
             'GRANT_NOT_FOUND'),
            ('other issue', {'write_paths': [PATH], 'issue': ISSUE + 1},
             'GRANT_NOT_FOUND'),
        )
        for label, kwargs, facts_code in cases:
            with self.subTest(case=label):
                self._assert_grant_refused(facts_code=facts_code, **kwargs)

    def test_ungranted_workflow_paths_are_refused_on_every_route(self):
        self._commit([])
        for name in ROUTE_NAMES:
            for mode in ('write', 'read'):
                with self.subTest(route=name, mode=mode):
                    kwargs = ({'write_paths': [PATH]} if mode == 'write'
                              else {'read_paths': [PATH]})
                    self._assert_grant_refused(
                        facts_code='GRANT_NOT_FOUND', name=name, **kwargs)

    def test_path_confusion_and_cross_path_are_refused(self):
        self._commit([self._entry(mode='write')])
        cases = (
            ('case changed', '.github/workflows/CI.yml'),
            ('dot segment', '.github/workflows/../workflows/ci.yml'),
            ('duplicate separator', '.github//workflows/ci.yml'),
            ('extra component', PATH + '/extra'),
            ('directory only', '.github/workflows'),
            ('cross path', OTHER),
        )
        for label, path in cases:
            with self.subTest(case=label):
                self._assert_refused(write_paths=[path])

    def test_symlinked_workflow_component_is_refused(self):
        self._commit([self._entry(mode='write')])
        target = self.root / 'target'
        (target / 'workflows').mkdir(parents=True)
        (target / 'workflows' / 'ci.yml').write_text(
            'name: ci\n', encoding='utf-8')
        (self.root / '.github').symlink_to(target, target_is_directory=True)
        self._assert_grant_refused(
            facts_code='GRANT_PATH_SYMLINK', write_paths=[PATH])

    def test_forged_or_missing_grant_identity_is_refused(self):
        self._commit([self._entry(mode='write')])
        missing = self._request(write_paths=[PATH])
        del missing['issue']
        with self.assertRaises(RouteError) as caught:
            _TestRoute('agy').validate_request(missing)
        self.assertEqual(caught.exception.code, WORKFLOW_GRANT_REFUSED)
        self.assertEqual(caught.exception.observed_facts.get(
            'workflow_grant_code'), 'GRANT_ISSUE_INVALID')
        self._assert_grant_refused(
            facts_code='GRANT_ISSUE_INVALID',
            write_paths=[PATH], task={'write_paths': [PATH]},
            issue='not-an-issue')
        self._assert_grant_refused(
            facts_code='GRANT_BASE_COMMIT_INVALID', write_paths=[PATH],
            task={'write_paths': [PATH]}, head='0' * 40)

    def test_conflicting_grant_identity_is_refused(self):
        self._commit([self._entry(mode='write')])
        self._assert_grant_refused(
            facts_code='GRANT_ISSUE_INVALID', write_paths=[PATH],
            task={'issue': ISSUE + 1})
        self._assert_grant_refused(
            facts_code='GRANT_BASE_COMMIT_INVALID', write_paths=[PATH],
            task={'base_commit': '0' * 40})

    def test_paths_outside_workflows_behave_as_before(self):
        self._commit([])
        value = self._validate(
            write_paths=['tools/llmtool/x.py'],
            read_paths=['docs/readme.md', '.github/other/p.yml',
                        '.github/workflows-other/ci.yml'])
        self.assertEqual(value['write_paths'], ('tools/llmtool/x.py',))
        self._assert_refused('CLAUDE_REQUEST_INVALID', write_paths=['/tmp/x'])
        self._assert_refused('CLAUDE_REQUEST_INVALID', write_paths=['a/../b'])

    def test_grant_is_read_only_through_the_w1_module(self):
        self._commit([])
        allowed = authorization_workflow_grant.WorkflowGrantDecision(
            True, authorization_workflow_grant.WorkflowGrantCode.GRANTED)
        denied = authorization_workflow_grant.WorkflowGrantDecision(
            False, authorization_workflow_grant.WorkflowGrantCode.GRANT_NOT_FOUND)
        with patch.object(
                authorization_workflow_grant, 'evaluate_workflow_grant',
                return_value=allowed) as evaluate:
            self._validate(write_paths=[PATH], read_paths=[RELEASE])
        self.assertEqual(evaluate.call_count, 2)
        self.assertEqual(
            {call.kwargs['mode'] for call in evaluate.call_args_list},
            {'write', 'read'})
        self.assertEqual(
            {call.kwargs['path'] for call in evaluate.call_args_list},
            {PATH, RELEASE})
        with patch.object(
                authorization_workflow_grant, 'evaluate_workflow_grant',
                return_value=denied):
            self._assert_grant_refused(
                facts_code='GRANT_NOT_FOUND', write_paths=[PATH])
        with patch.object(
                authorization_workflow_grant, 'evaluate_workflow_grant',
                return_value=allowed) as evaluate:
            self._validate(write_paths=['tools/llmtool/x.py'],
                           read_paths=['docs/readme.md'])
        evaluate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
