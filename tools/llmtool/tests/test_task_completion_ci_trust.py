"""CI trust in the completion gate (#369 PR-8): ci_trust drives the CI checks.

All GitHub responses are recorded fixture shapes; trust is a fixture TrustConfig.
"""
import contextlib
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool import task_completion
from llmtool.errors import LLMToolError
import review_trust_fixture

_JOBS = json.loads((Path(__file__).resolve().parent / 'fixtures' /
                    'github_actions_jobs_369pr8.json').read_text(encoding='utf-8'))
SHA = 'a' * 40
MAC = '.github/workflows/llmtool-mac-ci.yml'
LINUX = '.github/workflows/llmtool-linux-ci.yml'
DOCS_PATHS = ['docs/design/a13-trust-json-design-369.md']
CODE_PATHS = ['tools/llmtool/llmtool/task_completion.py']


def setUpModule(): review_trust_fixture.start_module()
def tearDownModule(): review_trust_fixture.stop_module()


def _job(kind, **overrides):
    job = copy.deepcopy(_JOBS[kind])
    job['head_sha'] = SHA
    job.update(overrides)
    return job


def _check(check_id, name, *, app_id=15368, conclusion='success'):
    return {'id': check_id, 'name': name, 'head_sha': SHA, 'status': 'completed',
            'conclusion': conclusion,
            'app': {'id': app_id, 'slug': 'github-actions', 'name': 'GitHub Actions'}}


def _workflow_responses(workflow_id, path, name, run_id, run_number, job, check, *,
                        run_head_sha=SHA):
    return [
        {'id': workflow_id, 'path': path, 'name': name, 'state': 'active'},
        {'total_count': 1, 'workflow_runs': [{
            'id': run_id, 'run_number': run_number, 'run_attempt': 1,
            'workflow_id': workflow_id, 'path': path, 'event': 'pull_request',
            'head_sha': run_head_sha, 'status': 'completed', 'conclusion': 'success',
            'pull_requests': [{'number': 10, 'head': {'sha': SHA}}]}]},
        {'total_count': 1, 'jobs': [job]},
        {'total_count': 1, 'check_runs': [check]},
    ]


def _mac(job=None, check=None):
    return _workflow_responses(42, MAC, 'llmtool Mac CI', 100, 5,
                               job or _job('self_hosted_mac_job'),
                               check or _check(1, 'check (macOS 3.13)'))


def _linux(job=None, check=None):
    return _workflow_responses(43, LINUX, 'llmtool Linux CI', 200, 8,
                               job or _job('self_hosted_linux_job'),
                               check or _check(61, 'check (Linux 3.14)'))


def _linux_docs():
    job = _job('self_hosted_linux_job', name='check (docs)')
    return _linux(job, _check(61, 'check (docs)'))


def _hosted_trust(labels=('ubuntu-24.04',)):
    document = review_trust_fixture.owner_ci_trust_document()
    document['mode'] = 'github-hosted'
    document['workflows'] = [
        {'path': '.github/workflows/hosted-ci.yml', 'name': 'hosted CI',
         'runner_names': None, 'runner_labels': list(labels),
         'code_check': 'check (hosted)', 'docs_check': 'check (hosted docs)'}]
    return review_trust_fixture.make_trust(ci_trust=document)


def _hosted(job=None, check=None):
    return _workflow_responses(44, '.github/workflows/hosted-ci.yml', 'hosted CI', 300, 3,
                               job or _job('github_hosted_job'),
                               check or _check(81, 'check (hosted)'))


def _collect(api, changed_paths=None, config=None):
    trust_patch = (review_trust_fixture.use_trust(config) if config is not None
                   else contextlib.nullcontext())
    side_effect = (lambda path: api[path]) if isinstance(api, dict) else api
    with trust_patch, patch('llmtool.task_completion._github_get', side_effect=side_effect) as get:
        result = task_completion._collect_ci_evidence(
            pr_number=10, result_sha=SHA, changed_paths=changed_paths)
    return result, get


def _responses_by_request(*groups):
    responses = {}
    for workflow, runs, jobs, checks in groups:
        responses[f"actions/workflows/{workflow['path'].rsplit('/', 1)[-1]}"] = workflow
        run = runs['workflow_runs'][0]
        responses[
            f"actions/workflows/{workflow['id']}/runs?head_sha={SHA}&event=pull_request&per_page=100"
        ] = runs
        responses[
            f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100"
        ] = jobs
        responses[f'commits/{SHA}/check-runs?per_page=100'] = checks
    return responses


class OwnerValuesGoldenTests(unittest.TestCase):
    """Owner-equivalent ci_trust gives the exact pre-PR-8 evidence dict."""

    def test_code_path_golden(self):
        result, get = _collect(_mac() + _linux(), CODE_PATHS)
        self.assertEqual(result, {
            'workflow_id': 42, 'run_id': 100, 'run_attempt': 1, 'run_number': 5,
            'successful_checks': ['check (Linux 3.14)', 'check (macOS 3.13)'],
            'check_ids': {'check (macOS 3.13)': 1, 'check (Linux 3.14)': 61},
            'check_outcomes': {'check (macOS 3.13)': 'SUCCESS',
                               'check (Linux 3.14)': 'SUCCESS'},
            'linux_required': True, 'docs_only': False,
            'required_checks': ['check (Linux 3.14)', 'check (macOS 3.13)'],
            'workflow_path': MAC, 'workflow_name': 'llmtool Mac CI'})
        self.assertEqual(list(result['check_ids']), ['check (macOS 3.13)', 'check (Linux 3.14)'])
        paths = [call.args[0] for call in get.call_args_list]
        self.assertEqual(paths, [
            'actions/workflows/llmtool-mac-ci.yml',
            f'actions/workflows/42/runs?head_sha={SHA}&event=pull_request&per_page=100',
            'actions/runs/100/attempts/1/jobs?per_page=100',
            f'commits/{SHA}/check-runs?per_page=100',
            'actions/workflows/llmtool-linux-ci.yml',
            f'actions/workflows/43/runs?head_sha={SHA}&event=pull_request&per_page=100',
            'actions/runs/200/attempts/1/jobs?per_page=100',
            f'commits/{SHA}/check-runs?per_page=100'])

    def test_reports_only_code_path_needs_linux_but_not_mac(self):
        result, get = _collect(_linux(), ['reports/x/results.json'])
        self.assertEqual(result['required_checks'], ['check (Linux 3.14)'])
        self.assertEqual(get.call_args_list[0].args[0], 'actions/workflows/llmtool-linux-ci.yml')

    def test_llmtool_code_path_still_needs_mac(self):
        result, _ = _collect(_mac() + _linux(), ['tools/llmtool/llmtool/a.py'])
        self.assertEqual(result['required_checks'],
                         ['check (Linux 3.14)', 'check (macOS 3.13)'])

    def test_docs_only_path_golden(self):
        result, get = _collect(_linux_docs(), DOCS_PATHS)
        self.assertEqual(result, {
            'workflow_id': 43, 'run_id': 200, 'run_attempt': 1, 'run_number': 8,
            'successful_checks': ['check (docs)'], 'check_ids': {'check (docs)': 61},
            'check_outcomes': {'check (docs)': 'SUCCESS'},
            'linux_required': True, 'docs_only': True,
            'required_checks': ['check (docs)'],
            'workflow_path': LINUX, 'workflow_name': 'llmtool Linux CI'})
        self.assertEqual(get.call_args_list[0].args[0], 'actions/workflows/llmtool-linux-ci.yml')
        self.assertEqual(len(get.call_args_list), 4)

    def test_code_path_without_runs_golden(self):
        no_runs = {'total_count': 0, 'workflow_runs': []}
        api = [_mac()[0], no_runs, _linux()[0], no_runs]
        result, _ = _collect(api, CODE_PATHS)
        self.assertEqual(result, {
            'workflow_id': 42, 'run_id': None, 'run_attempt': None, 'run_number': None,
            'successful_checks': [], 'check_ids': {}, 'linux_required': True,
            'check_outcomes': {'check (macOS 3.13)': 'MISSING',
                               'check (Linux 3.14)': 'MISSING'},
            'docs_only': False,
            'required_checks': ['check (Linux 3.14)', 'check (macOS 3.13)'],
            'workflow_path': MAC, 'workflow_name': 'llmtool Mac CI'})

    def test_required_check_outcomes_distinguish_failed_pending_and_untrusted(self):
        def collect_one(api):
            with patch('llmtool.task_completion._github_get', side_effect=api):
                return task_completion._collect_one_ci_workflow(
                    pr_number=10, result_sha=SHA, workflow_path=LINUX,
                    workflow_name='llmtool Linux CI', check_name='check (docs)',
                    runner_names=task_completion._LINUX_CI_RUNNER_NAMES)

        failed = _linux_docs()
        failed[1]['workflow_runs'][0]['conclusion'] = 'failure'
        failed[2]['jobs'][0].update(status='completed', conclusion='failure')
        failed[3]['check_runs'][0]['conclusion'] = 'failure'
        result = collect_one(failed)
        self.assertEqual(result['check_outcomes'], {'check (docs)': 'FAILED'})

        pending = _linux_docs()
        pending[1]['workflow_runs'][0].update(status='in_progress', conclusion=None)
        pending[2]['jobs'][0].update(status='in_progress', conclusion=None)
        pending[3]['check_runs'][0].update(status='in_progress', conclusion=None)
        result = collect_one(pending)
        self.assertEqual(result['check_outcomes'], {'check (docs)': 'PENDING'})

        untrusted = _linux_docs()
        untrusted[3]['check_runs'][0]['app']['id'] = 999
        result = collect_one(untrusted)
        self.assertEqual(result['check_outcomes'], {'check (docs)': 'MISSING'})

    def test_linux_failure_is_reported_when_mac_jobs_are_invalid(self):
        invalid_mac = _mac()
        invalid_mac[2]['jobs'][0]['runner_name'] = 'untrusted-runner'
        failed_linux = _linux()
        failed_linux[1]['workflow_runs'][0]['conclusion'] = 'failure'
        failed_linux[2]['jobs'][0]['conclusion'] = 'failure'
        failed_linux[3]['check_runs'][0]['conclusion'] = 'failure'
        result, _ = _collect(_responses_by_request(
            (invalid_mac[0], invalid_mac[1], invalid_mac[2], invalid_mac[3]),
            (failed_linux[0], failed_linux[1], failed_linux[2], failed_linux[3])),
            CODE_PATHS)
        self.assertEqual(result['check_outcomes'], {'check (Linux 3.14)': 'FAILED'})
        self.assertIn('check (Linux 3.14)', result['required_checks'])
        self.assertEqual(result['workflow_path'], LINUX)

    def test_not_run_checks_match_legacy_display(self):
        self.assertEqual(task_completion._not_run_checks(False),
                         ('Linux Python 3.9', 'Windows CI'))
        self.assertEqual(task_completion._not_run_checks(True),
                         ('Linux Python 3.9', 'Windows CI',
                          'check (macOS 3.13)', 'check (Linux 3.14)'))

    def test_fixture_ci_trust_equals_legacy_constants(self):
        ci = review_trust_fixture.make_trust().ci_trust
        mac, linux = ci['workflows']
        self.assertEqual((mac['path'], mac['name'], set(mac['runner_names']), mac['code_check']),
                         (task_completion._CI_WORKFLOW_PATH, task_completion._CI_WORKFLOW_NAME,
                          task_completion._CI_RUNNER_NAMES, task_completion._MAC_CHECK))
        self.assertEqual((linux['path'], linux['name'], set(linux['runner_names']),
                          linux['code_check'], linux['docs_check']),
                         (task_completion._LINUX_CI_WORKFLOW_PATH,
                          task_completion._LINUX_CI_WORKFLOW_NAME,
                          task_completion._LINUX_CI_RUNNER_NAMES,
                          task_completion._LINUX_CHECK, task_completion._DOCS_CHECK))
        self.assertEqual(tuple(ci['not_run_checks']), task_completion._NOT_RUN_CHECKS)
        self.assertEqual(ci['github_actions_app_id'], task_completion._GITHUB_ACTIONS_APP_ID)


class SelfHostedRunnerTests(unittest.TestCase):
    def _rejects(self, api, code='CI_WORKFLOW_JOBS_INVALID', config=None, paths=CODE_PATHS):
        with self.assertRaises(LLMToolError) as raised:
            _collect(api, paths, config)
        self.assertEqual(raised.exception.completion_code, code)

    def test_runner_name_not_in_list_is_refused(self):
        self._rejects(_mac(_job('self_hosted_mac_job', runner_name='other-runner')))

    def test_prefix_substring_and_wildcard_like_names_are_refused(self):
        for name in ('davidproject-self-action-3', 'davidproject-self', 'self-action',
                     'davidproject-self-action-2 ', 'davidproject-self-action*', '',
                     'DAVIDPROJECT-SELF-ACTION', None, ['davidproject-self-action']):
            with self.subTest(runner=name):
                self._rejects(_mac(_job('self_hosted_mac_job', runner_name=name)))

    def test_every_trusted_runner_name_is_accepted(self):
        for name in ('davidproject-self-action', 'davidproject-self-action-2'):
            with self.subTest(runner=name):
                result, _ = _collect(_mac(_job('self_hosted_mac_job', runner_name=name)) +
                                     _linux(), CODE_PATHS)
                self.assertIn('check (macOS 3.13)', result['successful_checks'])

    def test_runner_labels_set_but_labels_differ_is_refused(self):
        document = review_trust_fixture.owner_ci_trust_document()
        document['workflows'][0]['runner_labels'] = ['self-hosted', 'macOS', 'ARM64']
        config = review_trust_fixture.make_trust(ci_trust=document)
        for labels in (['self-hosted', 'macOS'], ['self-hosted', 'macOS', 'ARM64', 'extra'],
                       ['self-hosted', 'macos', 'ARM64'], None, 'self-hosted'):
            with self.subTest(labels=labels):
                self._rejects(_mac(_job('self_hosted_mac_job', labels=labels)), config=config)
        result, _ = _collect(_mac() + _linux(), CODE_PATHS, config)
        self.assertEqual(result['successful_checks'], ['check (Linux 3.14)', 'check (macOS 3.13)'])

    def test_runner_labels_null_ignores_labels_like_legacy(self):
        result, _ = _collect(_mac(_job('self_hosted_mac_job', labels=['anything'])) + _linux(),
                             CODE_PATHS)
        self.assertIn('check (macOS 3.13)', result['successful_checks'])

    def test_docs_path_uses_linux_runner_list(self):
        job = _job('self_hosted_linux_job', name='check (docs)',
                   runner_name='davidproject-self-action')
        self._rejects(_linux(job, _check(61, 'check (docs)')), paths=DOCS_PATHS)

    def test_run_head_sha_mismatch(self):
        api = _workflow_responses(42, MAC, 'llmtool Mac CI', 100, 5,
                                  _job('self_hosted_mac_job'), _check(1, 'check (macOS 3.13)'),
                                  run_head_sha='b' * 40)
        self._rejects(api, 'CI_WORKFLOW_RUN_MISMATCH')

    def test_workflow_path_mismatch(self):
        api = _mac()
        api[0]['path'] = '.github/workflows/other.yml'
        self._rejects(api, 'CI_WORKFLOW_MISMATCH')

    def test_check_url_for_other_slug_is_refused(self):
        job = _job('self_hosted_mac_job', check_run_url=(
            'https://api.github.com/repos/otherorg/other-repo/check-runs/1'))
        self._rejects(_mac(job))

    def test_wrong_app_id_is_not_success(self):
        result, _ = _collect(_mac(check=_check(1, 'check (macOS 3.13)', app_id=999)) + _linux(),
                             CODE_PATHS)
        self.assertEqual(result['successful_checks'], ['check (Linux 3.14)'])

    def test_trust_app_id_other_than_15368_fails_closed(self):
        document = review_trust_fixture.owner_ci_trust_document()
        document['github_actions_app_id'] = 999
        self._rejects(_mac(), 'TRUST_CI_INVALID',
                      config=review_trust_fixture.make_trust(ci_trust=document))

    def test_three_workflows_require_union_of_code_checks(self):
        document = review_trust_fixture.owner_ci_trust_document()
        document['workflows'].append(
            {'path': '.github/workflows/extra-ci.yml', 'name': 'extra CI',
             'runner_names': ['extra-runner'], 'runner_labels': None,
             'code_check': 'check (extra)', 'docs_check': None})
        config = review_trust_fixture.make_trust(ci_trust=document)
        extra_job = _job('self_hosted_linux_job', name='check (extra)', run_id=400,
                         runner_name='extra-runner', check_run_url=(
                             'https://api.github.com/repos/davidcatcat/'
                             'davidcattoolsproject/check-runs/91'))
        extra = _workflow_responses(45, '.github/workflows/extra-ci.yml', 'extra CI', 400, 2,
                                    extra_job, _check(91, 'check (extra)'))
        result, _ = _collect(_mac() + _linux() + extra, CODE_PATHS, config)
        self.assertEqual(result['required_checks'],
                         ['check (Linux 3.14)', 'check (extra)', 'check (macOS 3.13)'])
        self.assertEqual(result['successful_checks'], result['required_checks'])
        self.assertEqual(result['workflow_path'], MAC)
        docs, _ = _collect(_linux_docs(), DOCS_PATHS, config)
        self.assertEqual(docs['required_checks'], ['check (docs)'])


class GitHubHostedRunnerTests(unittest.TestCase):
    def _rejects(self, job, config=None):
        with self.assertRaises(LLMToolError) as raised:
            _collect(_hosted(job), CODE_PATHS, config or _hosted_trust())
        self.assertEqual(raised.exception.completion_code, 'CI_WORKFLOW_JOBS_INVALID')

    def test_exact_labels_and_github_actions_group_succeeds(self):
        result, _ = _collect(_hosted(), CODE_PATHS, _hosted_trust())
        self.assertEqual(result['successful_checks'], ['check (hosted)'])
        self.assertEqual(result['required_checks'], ['check (hosted)'])
        self.assertEqual(result['workflow_path'], '.github/workflows/hosted-ci.yml')

    def test_runner_name_is_not_consulted(self):
        result, _ = _collect(_hosted(_job('github_hosted_job', runner_name='anything')),
                             CODE_PATHS, _hosted_trust())
        self.assertEqual(result['successful_checks'], ['check (hosted)'])

    def test_self_hosted_label_is_refused(self):
        self._rejects(_job('github_hosted_job', labels=['ubuntu-24.04', 'self-hosted']))
        # Even a (schema-invalid) trust that lists self-hosted cannot accept it.
        self._rejects(_job('github_hosted_job', labels=['self-hosted']),
                      _hosted_trust(labels=('self-hosted',)))

    def test_other_runner_group_is_refused(self):
        for group in ('Default', 'github actions', 'GitHub Actions ', None):
            with self.subTest(group=group):
                self._rejects(_job('github_hosted_job', runner_group_name=group))

    def test_label_subset_or_superset_is_refused(self):
        config = _hosted_trust(labels=('ubuntu-24.04', 'x64'))
        for labels in (['ubuntu-24.04'], ['ubuntu-24.04', 'x64', 'gpu'], [], None):
            with self.subTest(labels=labels):
                self._rejects(_job('github_hosted_job', labels=labels), config)

    def test_wrong_app_id_is_not_success(self):
        result, _ = _collect(_hosted(check=_check(81, 'check (hosted)', app_id=1)),
                             CODE_PATHS, _hosted_trust())
        self.assertEqual(result['successful_checks'], [])

    def test_label_alternatives_trusted(self):
        # Each alternative matches when the job has exact labels.
        config = _hosted_trust(labels=(('macos-15',), ('macos-15-intel',)))
        for label in ('macos-15', 'macos-15-intel'):
            with self.subTest(label=label):
                result, _ = _collect(_hosted(_job('github_hosted_job', labels=[label])),
                                     CODE_PATHS, config)
                self.assertEqual(result['successful_checks'], ['check (hosted)'])

    def test_label_alternatives_untrusted_labels_refused(self):
        # Subset, superset, self-hosted, wrong or missing labels are refused.
        config = _hosted_trust(labels=(('macos-15',), ('macos-15-intel',)))
        for labels in (['macos-15', 'macos-15-intel'], ['macos-14'],
                       ['macos-15', 'self-hosted'], ['macos'], [], None):
            with self.subTest(labels=labels):
                self._rejects(_job('github_hosted_job', labels=labels), config)

    def test_label_alternatives_untrusted_runner_groups_refused(self):
        # An alternative match in the wrong runner group is refused.
        config = _hosted_trust(labels=(('macos-15',), ('macos-15-intel',)))
        for group in ('Default', 'github actions', None):
            with self.subTest(group=group):
                self._rejects(
                    _job('github_hosted_job', labels=['macos-15'], runner_group_name=group),
                    config)


class TrustedSlugTests(unittest.TestCase):
    def test_github_get_uses_trusted_slug(self):
        config = review_trust_fixture.make_trust('otherorg/other-repo')
        with review_trust_fixture.use_trust(config), \
                patch('llmtool.task_completion.shutil.which', return_value='/fixture/gh'), \
                patch('llmtool.task_completion.subprocess.Popen',
                      side_effect=OSError) as popen:
            with self.assertRaises(LLMToolError):
                task_completion._github_get('pulls/10')
        self.assertEqual(popen.call_args.args[0][-1], 'repos/otherorg/other-repo/pulls/10')

    def test_pr_from_other_slug_is_head_mismatch(self):
        pr = {'state': 'open', 'merged': False, 'head': {'sha': SHA},
              'base': {'repo': {'full_name': 'davidcatcat/davidcattoolsproject'}},
              'user': {'login': 'author-user'}}
        config = review_trust_fixture.make_trust('otherorg/other-repo')
        with review_trust_fixture.use_trust(config), \
                patch('llmtool.task_completion._github_get', return_value=pr):
            with self.assertRaises(LLMToolError) as raised:
                task_completion.collect_github_evidence(
                    pr_number=10, result_sha=SHA, task_id='1' * 32, run_id='2' * 32,
                    contract_digest='3' * 64, executor_identity='executor-agent',
                    executor_session_ref='s', executor_native_session_id='n',
                    reviewer_task_id='reviewer-task', common_dir='/nonexistent')
        self.assertEqual(raised.exception.completion_code, 'PR_HEAD_MISMATCH')

    def test_merged_empty_prs_run_requires_trusted_slug(self):
        api = _mac()
        run = api[1]['workflow_runs'][0]
        run.update({'pull_requests': [], 'head_branch': 'feature/merged',
                    'head_repository': {'full_name': 'davidcatcat/davidcattoolsproject'}})
        config = review_trust_fixture.make_trust('otherorg/other-repo')
        with review_trust_fixture.use_trust(config), \
                patch('llmtool.task_completion._github_get', side_effect=api):
            with self.assertRaises(LLMToolError) as raised:
                task_completion._collect_ci_evidence(
                    pr_number=10, result_sha=SHA, changed_paths=CODE_PATHS,
                    pr_merged=True, pr_head_ref='feature/merged')
        self.assertEqual(raised.exception.completion_code, 'CI_WORKFLOW_RUN_MISMATCH')

    def test_missing_trust_fails_closed_with_trust_code(self):
        from llmtool import trust
        with patch.object(trust, 'load_trust', side_effect=trust.TrustError('TRUST_MISSING')), \
                patch('llmtool.task_completion._github_get') as get:
            with self.assertRaises(LLMToolError) as raised:
                task_completion._collect_ci_evidence(pr_number=10, result_sha=SHA,
                                                     changed_paths=CODE_PATHS)
        self.assertEqual(raised.exception.completion_code, 'TRUST_MISSING')
        get.assert_not_called()


if __name__ == '__main__':
    unittest.main()
