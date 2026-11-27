"""#2793 A13.0 wiring (b): completion takes the Mac check name from ci_profile.

R1 keeps every default-profile decision and refusal identical. R2 proves the
real completion entry points require and accept a substituted profile's macOS
check name and do not accept the original one. R3 proves a missing, invalid or
ambiguous profile refuses with one fixed code before any evidence is read, and
that one completion decision resolves the provider once.
"""

from __future__ import annotations

import contextlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from llmtool import ci_profile, task_completion
from llmtool.ci_profile import CiProfile
from llmtool.errors import LLMToolError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import review_trust_fixture  # noqa: E402


def setUpModule(): review_trust_fixture.start_module()
def tearDownModule(): review_trust_fixture.stop_module()


SHA = 'a' * 40
PR_NUMBER = 10
SLUG = 'davidcatcat/davidcattoolsproject'
MAC_PATH = '.github/workflows/llmtool-mac-ci.yml'
LINUX_PATH = '.github/workflows/llmtool-linux-ci.yml'
MAC_CHECK = 'check (macOS 3.13)'
LINUX_CHECK = 'check (Linux 3.14)'
CODE_PATHS = ['tools/llmtool/llmtool/task_completion.py']
NON_MAC_PATHS = ['reports/results.json']
_MISSING = object()

ALT_PROFILE = CiProfile(
    linux_code_check='alt-linux-check',
    docs_check='alt-docs-check',
    gateway_check='alt-gateway-check',
    ai_loop_check='alt-ai-loop-check',
    macos_check='alt-macos-check',
    linux_workflow_file='alt-linux-ci.yml',
    macos_workflow_file='alt-mac-ci.yml',
)

#: A valid profile whose Mac name collides with the Linux trust check.
COLLIDING_MAC_PROFILE = CiProfile(
    linux_code_check='alt-linux-check',
    docs_check='alt-docs-check',
    gateway_check='alt-gateway-check',
    ai_loop_check='alt-ai-loop-check',
    macos_check=LINUX_CHECK,
    linux_workflow_file='alt-linux-ci.yml',
    macos_workflow_file='alt-mac-ci.yml',
)


def _workflow(responses, check_runs, *, path, name, workflow_id, run_id,
              check_name, check_id, runner, outcome='success'):
    responses['actions/workflows/' + path.rsplit('/', 1)[-1]] = {
        'id': workflow_id, 'path': path, 'name': name, 'state': 'active'}
    responses[
        f'actions/workflows/{workflow_id}/runs?head_sha={SHA}'
        '&event=pull_request&per_page=100'
    ] = {'total_count': 1, 'workflow_runs': [{
        'id': run_id, 'run_number': 5, 'run_attempt': 1,
        'workflow_id': workflow_id, 'path': path, 'event': 'pull_request',
        'head_sha': SHA, 'status': 'completed', 'conclusion': outcome,
        'pull_requests': [{'number': PR_NUMBER, 'head': {'sha': SHA}}]}]}
    responses[f'actions/runs/{run_id}/attempts/1/jobs?per_page=100'] = {
        'total_count': 1, 'jobs': [{
            'id': run_id + 1, 'run_id': run_id, 'name': check_name,
            'runner_name': runner,
            'check_run_url': (f'https://api.github.com/repos/{SLUG}/'
                              f'check-runs/{check_id}'),
            'status': 'completed', 'conclusion': outcome}]}
    check_runs.append({
        'id': check_id, 'name': check_name, 'head_sha': SHA,
        'status': 'completed', 'conclusion': outcome,
        'app': {'id': 15368, 'slug': 'github-actions', 'name': 'GitHub Actions'}})


def _responses(mac_check=MAC_CHECK, *, linux_only=False):
    """Return recorded GitHub responses for the Linux run and optional Mac run."""
    responses, check_runs = {}, []
    if not linux_only:
        _workflow(responses, check_runs, path=MAC_PATH, name='llmtool Mac CI',
                  workflow_id=42, run_id=100, check_name=mac_check, check_id=21,
                  runner='davidproject-self-action')
    _workflow(responses, check_runs, path=LINUX_PATH, name='llmtool Linux CI',
              workflow_id=43, run_id=200, check_name=LINUX_CHECK, check_id=61,
              runner='ec2-self-runner')
    responses[f'commits/{SHA}/check-runs?per_page=100'] = {
        'total_count': len(check_runs), 'check_runs': check_runs}
    return responses


def _review_responses():
    binding = task_completion._review_binding_line('1' * 32, '2' * 32, '3' * 64, SHA)
    return {
        f'pulls/{PR_NUMBER}': {
            'state': 'open', 'merged': False,
            'head': {'sha': SHA, 'ref': 'fix/ci-profile'},
            'base': {'ref': 'main', 'repo': {'full_name': SLUG}},
            'user': {'login': 'author-user'}},
        f'pulls/{PR_NUMBER}/reviews?per_page=100': [{
            'state': 'APPROVED', 'commit_id': SHA,
            'user': {'login': 'reviewer-user'},
            'submitted_at': '2026-10-21T10:00:00Z',
            'body': 'FORMAL SECURITY/FUNCTIONAL/QA ACCEPT\n' + binding}],
        f'issues/{PR_NUMBER}/comments?per_page=100': [],
    }


def _collect(responses, changed_paths, *, profile=_MISSING, trust=None):
    """Run the real completion CI entry point and return its evidence and reads."""
    trust_patch = (review_trust_fixture.use_trust(trust) if trust is not None
                   else contextlib.nullcontext())
    profile_patch = (mock.patch.object(ci_profile, 'default_profile',
                                       return_value=profile)
                     if profile is not _MISSING else contextlib.nullcontext())
    with trust_patch, profile_patch, \
            mock.patch.object(task_completion, '_github_get',
                              side_effect=lambda path: responses[path]) as get:
        result = task_completion._collect_ci_evidence(
            pr_number=PR_NUMBER, result_sha=SHA, changed_paths=changed_paths)
    return result, get


class DefaultProfileTests(unittest.TestCase):
    """R1: the default profile keeps every completion decision as before."""

    def test_code_path_requires_the_default_mac_check(self):
        result, _ = _collect(_responses(), CODE_PATHS)
        self.assertEqual(result['required_checks'], [LINUX_CHECK, MAC_CHECK])
        self.assertEqual(result['successful_checks'], [LINUX_CHECK, MAC_CHECK])
        self.assertEqual(result['workflow_path'], MAC_PATH)

    def test_non_mac_path_excludes_the_default_mac_check(self):
        result, get = _collect(_responses(linux_only=True), NON_MAC_PATHS)
        self.assertEqual(result['required_checks'], [LINUX_CHECK])
        self.assertEqual(result['successful_checks'], [LINUX_CHECK])
        self.assertEqual(get.call_args_list[0].args[0],
                         'actions/workflows/llmtool-linux-ci.yml')


class SubstitutedProfileTests(unittest.TestCase):
    """R2: a non-default profile's name is required and the original is not."""

    def test_mac_path_requires_and_accepts_the_profile_name(self):
        result, _ = _collect(_responses(ALT_PROFILE.macos_check), CODE_PATHS,
                             profile=ALT_PROFILE)
        expected = sorted([LINUX_CHECK, ALT_PROFILE.macos_check])
        self.assertEqual(result['required_checks'], expected)
        self.assertEqual(result['successful_checks'], expected)
        self.assertNotIn(MAC_CHECK, result['successful_checks'])

    def test_mac_path_does_not_accept_the_original_name(self):
        result, _ = _collect(_responses(MAC_CHECK), CODE_PATHS, profile=ALT_PROFILE)
        self.assertEqual(result['required_checks'],
                         sorted([LINUX_CHECK, ALT_PROFILE.macos_check]))
        self.assertEqual(result['successful_checks'], [LINUX_CHECK])
        self.assertEqual(
            result['check_outcomes'][ALT_PROFILE.macos_check], 'MISSING')

    def test_non_mac_path_excludes_the_mac_workflow_by_trust_identity(self):
        result, get = _collect(_responses(linux_only=True), NON_MAC_PATHS,
                               profile=ALT_PROFILE)
        self.assertEqual(result['required_checks'], [LINUX_CHECK])
        self.assertEqual(get.call_args_list[0].args[0],
                         'actions/workflows/llmtool-linux-ci.yml')

    def test_non_mac_path_keeps_linux_when_macos_name_matches_it(self):
        result, get = _collect(_responses(linux_only=True), NON_MAC_PATHS,
                               profile=COLLIDING_MAC_PROFILE)
        self.assertEqual(result['required_checks'], [LINUX_CHECK])
        self.assertEqual(result['successful_checks'], [LINUX_CHECK])
        self.assertEqual(get.call_args_list[0].args[0],
                         'actions/workflows/llmtool-linux-ci.yml')

    def _collect_github(self, responses, profile):
        """Run collect_github_evidence with the real CI completion path."""
        reviewer = SimpleNamespace(
            trusted_agent_id='claude-reviewer', session_ref='Reviewer session',
            native_session_id='native-reviewer', github_login='reviewer-user',
            root='/reviewer', common_dir='/common', branch='review/branch')
        snapshot = SimpleNamespace(
            common_dir='/common', branch='review/branch', head=SHA)
        with mock.patch.object(ci_profile, 'default_profile', return_value=profile), \
                mock.patch.object(task_completion, '_github_get',
                                  side_effect=lambda path: responses[path]), \
                mock.patch.object(task_completion, 'read_d6_registration',
                                  return_value=reviewer), \
                mock.patch.object(task_completion, 'collect_git_snapshot',
                                  return_value=snapshot):
            return task_completion.collect_github_evidence(
                pr_number=PR_NUMBER, result_sha=SHA, task_id='1' * 32,
                run_id='2' * 32, contract_digest='3' * 64,
                executor_identity='executor-agent',
                executor_session_ref='Executor D6 session',
                executor_native_session_id='native-executor',
                reviewer_task_id='reviewer-task', common_dir='/common',
                changed_paths=CODE_PATHS,
                task_record={'developer': {'provider_profile': 'codex'}})

    def test_github_entry_accepts_the_profile_name(self):
        result = self._collect_github(
            {**_review_responses(), **_responses(ALT_PROFILE.macos_check)},
            ALT_PROFILE)
        self.assertEqual(result['missing_gates'], [])
        self.assertIn(ALT_PROFILE.macos_check,
                      result['evidence']['successful_checks'])
        self.assertNotIn(MAC_CHECK, result['evidence']['successful_checks'])

    def test_github_entry_rejects_the_original_name(self):
        result = self._collect_github(
            {**_review_responses(), **_responses(MAC_CHECK)}, ALT_PROFILE)
        self.assertIn('REQUIRED_CI_MISSING', result['missing_gates'])
        self.assertNotIn(ALT_PROFILE.macos_check,
                         result['evidence']['successful_checks'])
        self.assertIn(LINUX_CHECK, result['evidence']['successful_checks'])


class RefusedProfileTests(unittest.TestCase):
    """R3: missing, invalid or ambiguous profiles refuse with one fixed code."""

    @staticmethod
    def _bad_profiles():
        empty = CiProfile(
            linux_code_check='a', docs_check='b', gateway_check='c',
            ai_loop_check='d', macos_check='e',
            linux_workflow_file='l.yml', macos_workflow_file='m.yml')
        object.__setattr__(empty, 'macos_check', '')
        duplicate = CiProfile(
            linux_code_check='same', docs_check='same', gateway_check='c',
            ai_loop_check='d', macos_check='e',
            linux_workflow_file='l.yml', macos_workflow_file='m.yml')
        deleted = CiProfile(
            linux_code_check='a', docs_check='b', gateway_check='c',
            ai_loop_check='d', macos_check='e',
            linux_workflow_file='l.yml', macos_workflow_file='m.yml')
        object.__delattr__(deleted, 'macos_check')
        return (
            ('missing', None),
            ('not_callable', object()),
            ('raising', mock.Mock(side_effect=RuntimeError('unavailable'))),
            ('non_profile', mock.Mock(return_value=object())),
            ('ambiguous_sequence', mock.Mock(return_value=[ALT_PROFILE])),
            ('mutated_empty_name', mock.Mock(return_value=empty)),
            ('deleted_field', mock.Mock(return_value=deleted)),
            ('ambiguous_duplicate_names', mock.Mock(return_value=duplicate)),
        )

    def test_ci_entry_refuses_before_any_github_read(self):
        for kind, provider in self._bad_profiles():
            with self.subTest(kind=kind):
                with mock.patch.object(ci_profile, 'default_profile', provider), \
                        mock.patch.object(task_completion, '_github_get') as get:
                    with self.assertRaises(LLMToolError) as caught:
                        task_completion._collect_ci_evidence(
                            pr_number=PR_NUMBER, result_sha=SHA,
                            changed_paths=CODE_PATHS)
                self.assertEqual(caught.exception.completion_code,
                                 'CI_PROFILE_INVALID')
                get.assert_not_called()

    def test_ambiguous_mac_mapping_refuses_before_any_read(self):
        with mock.patch.object(ci_profile, 'default_profile',
                               return_value=COLLIDING_MAC_PROFILE), \
                mock.patch.object(task_completion, '_github_get') as get:
            with self.assertRaises(LLMToolError) as caught:
                task_completion._collect_ci_evidence(
                    pr_number=PR_NUMBER, result_sha=SHA,
                    changed_paths=CODE_PATHS)
        self.assertEqual(caught.exception.completion_code, 'CI_PROFILE_INVALID')
        get.assert_not_called()

    def test_github_entry_refuses_before_any_read(self):
        with mock.patch.object(ci_profile, 'default_profile', None), \
                mock.patch.object(task_completion, '_github_get') as get:
            with self.assertRaises(LLMToolError) as caught:
                task_completion.collect_github_evidence(
                    pr_number=PR_NUMBER, result_sha=SHA,
                    task_id='1' * 32, run_id='2' * 32,
                    contract_digest='3' * 64,
                    executor_identity='executor-agent',
                    executor_session_ref='executor-session',
                    executor_native_session_id='native-executor',
                    reviewer_task_id='reviewer-task', common_dir='/nonexistent')
        self.assertEqual(caught.exception.completion_code, 'CI_PROFILE_INVALID')
        get.assert_not_called()


class SingleResolutionTests(unittest.TestCase):
    """R3: one completion decision resolves the live provider once."""

    def test_github_decision_resolves_the_profile_once(self):
        provider = mock.Mock(wraps=ci_profile.default_profile)
        reviewer = SimpleNamespace(
            trusted_agent_id='claude-reviewer', session_ref='Reviewer session',
            native_session_id='native-reviewer', github_login='reviewer-user',
            root='/reviewer', common_dir='/common', branch='review/branch')
        snapshot = SimpleNamespace(
            common_dir='/common', branch='review/branch', head=SHA)
        responses = {**_review_responses(), **_responses()}
        with mock.patch.object(ci_profile, 'default_profile', provider), \
                mock.patch.object(task_completion, '_github_get',
                                  side_effect=lambda path: responses[path]), \
                mock.patch.object(task_completion, 'read_d6_registration',
                                  return_value=reviewer), \
                mock.patch.object(task_completion, 'collect_git_snapshot',
                                  return_value=snapshot):
            result = task_completion.collect_github_evidence(
                pr_number=PR_NUMBER, result_sha=SHA, task_id='1' * 32,
                run_id='2' * 32, contract_digest='3' * 64,
                executor_identity='executor-agent',
                executor_session_ref='Executor D6 session',
                executor_native_session_id='native-executor',
                reviewer_task_id='reviewer-task', common_dir='/common',
                changed_paths=CODE_PATHS,
                task_record={'developer': {'provider_profile': 'codex'}})
        self.assertEqual(result['missing_gates'], [])
        self.assertEqual(provider.call_count, 1)


if __name__ == '__main__':
    unittest.main()
