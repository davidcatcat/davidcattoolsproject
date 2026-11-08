"""task complete CI evidence for a scripts/-only Task (#1208).

A scripts/-only change is code. Like any other code Task it needs the
exact-head Mac and Linux code checks; a Mac success alone never closes the CI
gate. Docs-only paths keep the docs gate.
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool.errors import LLMToolError
from llmtool import task_completion

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import review_trust_fixture  # noqa: E402


def setUpModule(): review_trust_fixture.start_module()
def tearDownModule(): review_trust_fixture.stop_module()


MAC_PATH = '.github/workflows/llmtool-mac-ci.yml'
LINUX_PATH = '.github/workflows/llmtool-linux-ci.yml'
MAC_CHECK = 'check (macOS 3.13)'
LINUX_CHECK = 'check (Linux 3.14)'
DOCS_CHECK = 'check (docs)'
SCRIPTS_PATHS = ['scripts/ci_mac_modules.py', 'scripts/llmtool_monitor.py']
RESULT_SHA = 'a' * 40
PR_NUMBER = 10


class ScriptsOnlyCiTests(unittest.TestCase):
    def setUp(self):
        review_trust_fixture.install_for_case(self)
        self.slug = task_completion._trusted_slug()

    # GitHub fixtures -----------------------------------------------------

    def _workflow(self, responses, check_runs, *, path, name, workflow_id, run_id,
                  check_name, check_id, runner, outcome):
        """Add one workflow's responses; outcome is a conclusion, 'pending' or None (no run)."""
        responses['actions/workflows/' + path.rsplit('/', 1)[-1]] = {
            'id': workflow_id, 'path': path, 'name': name, 'state': 'active'}
        runs_key = (f'actions/workflows/{workflow_id}/runs?head_sha={RESULT_SHA}'
                    '&event=pull_request&per_page=100')
        if outcome is None:
            responses[runs_key] = {'total_count': 0, 'workflow_runs': []}
            return
        status, conclusion = (('in_progress', None) if outcome == 'pending'
                              else ('completed', outcome))
        responses[runs_key] = {'total_count': 1, 'workflow_runs': [{
            'id': run_id, 'run_number': 5, 'run_attempt': 1, 'workflow_id': workflow_id,
            'path': path, 'event': 'pull_request', 'head_sha': RESULT_SHA,
            'status': status, 'conclusion': conclusion,
            'pull_requests': [{'number': PR_NUMBER, 'head': {'sha': RESULT_SHA}}]}]}
        responses[f'actions/runs/{run_id}/attempts/1/jobs?per_page=100'] = {
            'total_count': 1, 'jobs': [{
                'id': run_id + 1, 'run_id': run_id, 'name': check_name,
                'runner_name': runner,
                'check_run_url': (f'https://api.github.com/repos/{self.slug}/'
                                  f'check-runs/{check_id}'),
                'status': status, 'conclusion': conclusion}]}
        check_runs.append({
            'id': check_id, 'name': check_name, 'head_sha': RESULT_SHA,
            'status': status, 'conclusion': conclusion,
            'app': {'id': 15368, 'slug': 'github-actions', 'name': 'GitHub Actions'}})

    def _ci_responses(self, *, mac='success', linux=None, linux_check=LINUX_CHECK):
        responses, check_runs = {}, []
        self._workflow(responses, check_runs, path=MAC_PATH, name='llmtool Mac CI',
                       workflow_id=42, run_id=100, check_name=MAC_CHECK, check_id=21,
                       runner='davidproject-self-action', outcome=mac)
        self._workflow(responses, check_runs, path=LINUX_PATH, name='llmtool Linux CI',
                       workflow_id=43, run_id=200, check_name=linux_check, check_id=61,
                       runner='ec2-self-runner', outcome=linux)
        responses[f'commits/{RESULT_SHA}/check-runs?per_page=100'] = {
            'total_count': len(check_runs), 'check_runs': check_runs}
        return responses

    def _review_responses(self):
        binding = task_completion._review_binding_line(
            '1' * 32, '2' * 32, '3' * 64, RESULT_SHA)
        return {
            f'pulls/{PR_NUMBER}': {
                'state': 'open', 'merged': False,
                'head': {'sha': RESULT_SHA, 'ref': 'fix/scripts'},
                'base': {'ref': 'main', 'repo': {'full_name': self.slug}},
                'user': {'login': 'author-user'}},
            f'pulls/{PR_NUMBER}/reviews?per_page=100': [{
                'state': 'APPROVED', 'commit_id': RESULT_SHA,
                'user': {'login': 'reviewer-user'},
                'submitted_at': '2026-10-21T10:00:00Z',
                'body': 'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n' + binding}],
            f'issues/{PR_NUMBER}/comments?per_page=100': [],
        }

    def _github(self, responses, calls):
        def get(path):
            calls.append(path)
            if path not in responses:
                raise AssertionError('unexpected GitHub path: ' + path)
            return responses[path]
        return get

    def _collect(self, responses, changed_paths):
        calls = []
        with patch.object(task_completion, '_github_get',
                          side_effect=self._github(responses, calls)):
            evidence = task_completion._collect_ci_evidence(
                pr_number=PR_NUMBER, result_sha=RESULT_SHA, changed_paths=changed_paths)
        return evidence, calls

    def _collect_github(self, responses, changed_paths):
        reviewer = SimpleNamespace(
            trusted_agent_id='claude-reviewer', session_ref='Reviewer session',
            native_session_id='native-reviewer', github_login='reviewer-user',
            root='/reviewer', common_dir='/common', branch='review/branch')
        snapshot = SimpleNamespace(common_dir='/common', branch='review/branch',
                                   head=RESULT_SHA)
        calls = []
        with patch.object(task_completion, '_github_get',
                          side_effect=self._github(responses, calls)), \
                patch.object(task_completion, 'read_d6_registration',
                             return_value=reviewer), \
                patch.object(task_completion, 'collect_git_snapshot',
                             return_value=snapshot):
            result = task_completion.collect_github_evidence(
                pr_number=PR_NUMBER, result_sha=RESULT_SHA, task_id='1' * 32,
                run_id='2' * 32, contract_digest='3' * 64,
                executor_identity='executor-agent',
                executor_session_ref='Executor D6 session',
                executor_native_session_id='native-executor',
                reviewer_task_id='reviewer-task', common_dir='/common',
                changed_paths=changed_paths,
                task_record={'developer': {'provider_profile': 'codex'}})
        return result, calls

    def _workflow_reads(self, calls):
        return [path for path in calls if path in (
            'actions/workflows/llmtool-mac-ci.yml',
            'actions/workflows/llmtool-linux-ci.yml')]

    # Cases ---------------------------------------------------------------

    def test_scripts_only_mac_success_without_linux_run_does_not_close_ci_gate(self):
        # Reproduction: before #1208 a Mac success alone closed this gate.
        responses = {**self._review_responses(), **self._ci_responses(mac='success')}
        result, calls = self._collect_github(responses, SCRIPTS_PATHS)
        self.assertIn('REQUIRED_CI_MISSING', result['missing_gates'])
        self.assertNotIn('REQUIRED_CI_FAILED', result['missing_gates'])
        self.assertNotIn('REQUIRED_CI_PENDING', result['missing_gates'])
        evidence = result['evidence']
        self.assertEqual(evidence['required_checks'], [LINUX_CHECK, MAC_CHECK])
        self.assertEqual(evidence['successful_checks'], [MAC_CHECK])
        self.assertIn('actions/workflows/llmtool-linux-ci.yml', calls)

    def test_scripts_only_skipped_linux_does_not_close_ci_gate(self):
        responses = {**self._review_responses(),
                     **self._ci_responses(mac='success', linux='skipped')}
        result, _ = self._collect_github(responses, SCRIPTS_PATHS)
        self.assertIn('REQUIRED_CI_MISSING', result['missing_gates'])
        evidence = result['evidence']
        self.assertEqual(evidence['required_checks'], [LINUX_CHECK, MAC_CHECK])
        self.assertEqual(evidence['successful_checks'], [MAC_CHECK])
        self.assertNotIn(LINUX_CHECK, evidence['ci_check_ids'])

    def test_scripts_only_linux_and_mac_success_closes_ci_gate(self):
        responses = {**self._review_responses(),
                     **self._ci_responses(mac='success', linux='success')}
        result, calls = self._collect_github(responses, SCRIPTS_PATHS)
        self.assertEqual(result['missing_gates'], [])
        evidence = result['evidence']
        self.assertEqual(evidence['required_checks'], [LINUX_CHECK, MAC_CHECK])
        self.assertEqual(evidence['successful_checks'], [LINUX_CHECK, MAC_CHECK])
        self.assertEqual(evidence['ci_check_ids'], {MAC_CHECK: 21, LINUX_CHECK: 61})
        self.assertEqual(evidence['ci_workflow_path'], MAC_PATH)
        self.assertEqual(self._workflow_reads(calls), [
            'actions/workflows/llmtool-mac-ci.yml',
            'actions/workflows/llmtool-linux-ci.yml'])

    def test_scripts_only_helpers_are_removed(self):
        self.assertFalse(hasattr(task_completion, '_scripts_only_ci'))
        self.assertFalse(hasattr(task_completion, '_SCRIPTS_ONLY_SKIPPED_CODE_CHECK'))

    def test_scripts_only_collect_reads_mac_and_linux_workflows(self):
        evidence, calls = self._collect(
            self._ci_responses(mac='success', linux='success'), SCRIPTS_PATHS)
        self.assertEqual(evidence['required_checks'], [LINUX_CHECK, MAC_CHECK])
        self.assertEqual(evidence['successful_checks'], [LINUX_CHECK, MAC_CHECK])
        self.assertTrue(evidence['linux_required'])
        self.assertFalse(evidence['docs_only'])
        # The result keeps today's key set (OwnerValuesGoldenTests); no new key.
        self.assertNotIn('scripts_only', evidence)
        self.assertEqual(self._workflow_reads(calls), [
            'actions/workflows/llmtool-mac-ci.yml',
            'actions/workflows/llmtool-linux-ci.yml'])

    def test_mixed_empty_and_none_paths_keep_mac_and_linux_gate(self):
        for changed_paths in (['scripts/a.py', 'tools/llmtool/llmtool/task_completion.py'],
                              ['scripts/a.py', 'tools/llmtool/README.md'],
                              ['tools/llmtool/llmtool/task_completion.py'], [], None):
            with self.subTest(changed_paths=changed_paths):
                evidence, calls = self._collect(
                    self._ci_responses(mac='success', linux='success'), changed_paths)
                self.assertEqual(evidence['required_checks'], [LINUX_CHECK, MAC_CHECK])
                self.assertEqual(evidence['successful_checks'], [LINUX_CHECK, MAC_CHECK])
                self.assertTrue(evidence['linux_required'])
                self.assertFalse(evidence['docs_only'])
                self.assertNotIn('scripts_only', evidence)
                self.assertEqual(self._workflow_reads(calls), [
                    'actions/workflows/llmtool-mac-ci.yml',
                    'actions/workflows/llmtool-linux-ci.yml'])

    def test_mixed_paths_without_linux_run_still_miss_linux(self):
        responses = {**self._review_responses(), **self._ci_responses(mac='success')}
        result, _ = self._collect_github(
            responses, ['scripts/a.py', 'tools/llmtool/llmtool/task_completion.py'])
        self.assertIn('REQUIRED_CI_MISSING', result['missing_gates'])
        self.assertEqual(result['evidence']['required_checks'], [LINUX_CHECK, MAC_CHECK])

    def test_docs_only_paths_keep_docs_gate(self):
        evidence, calls = self._collect(
            self._ci_responses(mac=None, linux='success', linux_check=DOCS_CHECK),
            ['docs/guide.md', 'README.md'])
        self.assertEqual(evidence['required_checks'], [DOCS_CHECK])
        self.assertEqual(evidence['successful_checks'], [DOCS_CHECK])
        self.assertTrue(evidence['docs_only'])
        self.assertTrue(evidence['linux_required'])
        self.assertNotIn('scripts_only', evidence)
        self.assertEqual(self._workflow_reads(calls),
                         ['actions/workflows/llmtool-linux-ci.yml'])

    def test_scripts_only_mac_missing_pending_or_failed_still_blocks(self):
        for mac, extra in ((None, None), ('pending', 'REQUIRED_CI_PENDING'),
                           ('failure', 'REQUIRED_CI_FAILED')):
            with self.subTest(mac=mac):
                responses = {**self._review_responses(),
                             **self._ci_responses(mac=mac, linux='success')}
                result, _ = self._collect_github(responses, SCRIPTS_PATHS)
                self.assertIn('REQUIRED_CI_MISSING', result['missing_gates'])
                if extra is not None:
                    self.assertIn(extra, result['missing_gates'])
                self.assertEqual(result['evidence']['required_checks'],
                                 [LINUX_CHECK, MAC_CHECK])
                self.assertNotIn(MAC_CHECK, result['evidence']['successful_checks'])

    def test_scripts_only_linux_pending_or_failed_blocks(self):
        for linux, extra in (('pending', 'REQUIRED_CI_PENDING'),
                             ('failure', 'REQUIRED_CI_FAILED')):
            with self.subTest(linux=linux):
                responses = {**self._review_responses(),
                             **self._ci_responses(mac='success', linux=linux)}
                result, _ = self._collect_github(responses, SCRIPTS_PATHS)
                self.assertIn('REQUIRED_CI_MISSING', result['missing_gates'])
                self.assertIn(extra, result['missing_gates'])
                self.assertEqual(result['evidence']['successful_checks'], [MAC_CHECK])

    def test_scripts_only_malformed_mac_job_follows_code_rule(self):
        # Same as any code Task: a malformed Mac job fails closed, unless the
        # Linux workflow confirms a failure, which is then reported.
        responses = self._ci_responses(mac='success', linux='success')
        responses['actions/runs/100/attempts/1/jobs?per_page=100']['jobs'][0][
            'runner_name'] = 'unregistered-runner'
        with self.assertRaises(LLMToolError) as rejected:
            self._collect(responses, SCRIPTS_PATHS)
        self.assertEqual(rejected.exception.completion_code, 'CI_WORKFLOW_JOBS_INVALID')

        responses = self._ci_responses(mac='success', linux='failure')
        responses['actions/runs/100/attempts/1/jobs?per_page=100']['jobs'][0][
            'runner_name'] = 'unregistered-runner'
        evidence, _ = self._collect(responses, SCRIPTS_PATHS)
        self.assertEqual(evidence['check_outcomes'], {LINUX_CHECK: 'FAILED'})
        self.assertEqual(evidence['successful_checks'], [])
        self.assertEqual(evidence['required_checks'], [LINUX_CHECK, MAC_CHECK])
        self.assertEqual(evidence['workflow_path'], LINUX_PATH)

    def test_not_run_checks_do_not_list_linux_for_scripts_only(self):
        base = list(task_completion._ci_trust()['not_run_checks'])
        self.assertEqual(list(task_completion._not_run_checks(False)), base)
        with self.assertRaises(TypeError):
            task_completion._not_run_checks(False, scripts_only=True)

        for changed_paths in (SCRIPTS_PATHS, ['tools/llmtool/llmtool/x.py']):
            with self.subTest(changed_paths=changed_paths):
                responses = {**self._review_responses(),
                             **self._ci_responses(mac='success', linux='success')}
                result, _ = self._collect_github(responses, changed_paths)
                self.assertEqual(result['evidence']['not_run_checks'],
                                 [{'name': name, 'status': 'NOT_RUN'} for name in base])

    def test_scripts_only_with_linux_only_trust_requires_linux(self):
        ci_trust = task_completion._ci_trust()
        linux_only = dict(ci_trust)
        linux_only['workflows'] = [workflow for workflow in ci_trust['workflows']
                                   if workflow['code_check'] == LINUX_CHECK]
        self.assertEqual(len(linux_only['workflows']), 1)
        with patch.object(task_completion, '_ci_trust', return_value=linux_only):
            evidence, calls = self._collect(
                self._ci_responses(mac=None, linux='success'), SCRIPTS_PATHS)
        self.assertEqual(evidence['required_checks'], [LINUX_CHECK])
        self.assertEqual(evidence['successful_checks'], [LINUX_CHECK])
        self.assertEqual(self._workflow_reads(calls),
                         ['actions/workflows/llmtool-linux-ci.yml'])


if __name__ == '__main__':
    unittest.main()
