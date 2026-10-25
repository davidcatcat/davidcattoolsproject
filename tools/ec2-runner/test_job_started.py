"""Offline checks for the EC2 runners' root-owned job admission hook."""
import io
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

HOOK = str(Path(__file__).with_name('job-started.py'))
REPO = 'davidcatcat/davidcattoolsproject'


class JobAdmissionTests(unittest.TestCase):

    def run_hook(self, account='ghrunner', event=None, **env):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'event.json'
            path.write_text(json.dumps(event if event is not None else {
                'repository': {'full_name': REPO}, 'sender': {'login': 'davidcatcat'},
                'ref': 'refs/heads/main'}))
            values = {
                'RUNNER_NAME': 'ec2-self-runner', 'GITHUB_REPOSITORY': REPO,
                'GITHUB_ACTOR': 'davidcatcat', 'GITHUB_TRIGGERING_ACTOR': 'davidcatcat',
                'GITHUB_SHA': 'a' * 40,
                'GITHUB_WORKFLOW_REF': REPO + '/.github/workflows/llmtool-linux-ci.yml@refs/heads/main',
                'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/heads/main',
                'GITHUB_EVENT_PATH': str(path)}
            values.update(env)
            out = io.StringIO()
            with patch.dict(os.environ, values, clear=True), \
                 patch('pwd.getpwuid', return_value=SimpleNamespace(pw_name=account)), \
                 redirect_stdout(out):
                try:
                    runpy.run_path(HOOK, run_name='__main__')
                except SystemExit as exc:
                    return exc.code, out.getvalue()
            return 0, out.getvalue()

    def refused(self, reason, **kwargs):
        code, out = self.run_hook(**kwargs)
        self.assertEqual(code, 1, out)
        self.assertIn('EC2 host admission refused: ' + reason, out)

    def test_main_push_from_the_owner_is_admitted(self):
        code, out = self.run_hook()
        self.assertEqual(code, 0, out)
        self.assertIn('EC2_HOST_ADMISSION_PASS runner=ec2-self-runner event=push', out)

    def test_each_runner_must_run_under_its_own_account(self):
        self.refused('service account', account='ghrunner2')
        self.refused('service account', account='david.s')
        self.refused('runner identity', RUNNER_NAME='other-runner')
        code, _ = self.run_hook(account='ghrunner2', RUNNER_NAME='ec2-self-runner-2')
        self.assertEqual(code, 0)

    def test_other_workflows_actors_and_repositories_are_refused(self):
        self.refused('workflow', GITHUB_WORKFLOW_REF=REPO + '/.github/workflows/evil.yml@refs/heads/main')
        self.refused('actor', GITHUB_ACTOR='someone')
        self.refused('triggering actor', GITHUB_TRIGGERING_ACTOR='someone')
        self.refused('triggering actor', GITHUB_TRIGGERING_ACTOR='')
        self.refused('repository', GITHUB_REPOSITORY='fork/davidcattoolsproject')
        self.refused('commit SHA', GITHUB_SHA='main')
        self.refused('event type', GITHUB_EVENT_NAME='pull_request_target')
        self.refused('event sender', event={'repository': {'full_name': REPO}, 'sender': {'login': 'x'}})

    def test_pushes_and_manual_runs_only_from_main(self):
        other = REPO + '/.github/workflows/llmtool-linux-ci.yml@refs/heads/other'
        self.refused('push ref', GITHUB_REF='refs/heads/other', GITHUB_WORKFLOW_REF=other)
        self.refused('push ref', GITHUB_WORKFLOW_REF=other)   # workflow from another ref
        code, out = self.run_hook(GITHUB_EVENT_NAME='workflow_dispatch')
        self.assertEqual(code, 0, out)
        self.refused('dispatch ref', GITHUB_EVENT_NAME='workflow_dispatch',
                     GITHUB_REF='refs/heads/other', GITHUB_WORKFLOW_REF=other)

    def test_a_missing_or_bad_event_file_is_refused(self):
        self.refused('event payload', GITHUB_EVENT_PATH='/nonexistent/event.json')
        with tempfile.TemporaryDirectory() as temporary:
            bad, big = Path(temporary) / 'bad.json', Path(temporary) / 'big.json'
            bad.write_text('{not json')
            big.write_bytes(b' ' * (1048576 + 1))
            self.refused('event payload parse', GITHUB_EVENT_PATH=str(bad))
            self.refused('event payload', GITHUB_EVENT_PATH=str(big))

    def test_pull_requests_only_from_the_owner_inside_this_repository(self):
        merge = 'refs/pull/7/merge'
        def pr(head_repo=REPO, author='davidcatcat', base='main'):
            return {'repository': {'full_name': REPO}, 'sender': {'login': 'davidcatcat'},
                    'pull_request': {'head': {'repo': {'full_name': head_repo}},
                                     'user': {'login': author}, 'base': {'ref': base}}}
        env = {'GITHUB_EVENT_NAME': 'pull_request', 'GITHUB_REF': merge,
               'GITHUB_WORKFLOW_REF': REPO + '/.github/workflows/llmtool-linux-ci.yml@' + merge}
        code, out = self.run_hook(event=pr(), **env)
        self.assertEqual(code, 0, out)
        self.refused('fork', event=pr(head_repo='someone/davidcattoolsproject'), **env)
        self.refused('pull request author', event=pr(author='someone'), **env)
        self.refused('pull request base', event=pr(base='release'), **env)
        event = pr()
        del event['pull_request']['head']['repo']
        self.refused('fork', event=event, **env)
        head = dict(env, GITHUB_REF='refs/heads/feature',
                    GITHUB_WORKFLOW_REF=REPO + '/.github/workflows/llmtool-linux-ci.yml@refs/heads/feature')
        self.refused('pull request ref', event=pr(), **head)
        self.refused('workflow pull request ref', event=pr(),
                     **dict(env, GITHUB_WORKFLOW_REF=REPO + '/.github/workflows/llmtool-linux-ci.yml@refs/heads/main'))


if __name__ == '__main__':
    unittest.main()
