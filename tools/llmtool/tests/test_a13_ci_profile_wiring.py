"""#2793 A13.0 wiring (a): CI profile wiring for mac_full_due, mac_full_suite and main_ci_job_fetch."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import ci_profile  # noqa: E402
from llmtool import mac_full_due as due  # noqa: E402
from llmtool import mac_full_suite as suite  # noqa: E402
from llmtool import main_ci_job_fetch as fetch  # noqa: E402
from llmtool.ci_profile import CiProfile, CiProfileError, default_profile  # noqa: E402

SHA = 'a' * 40
T0 = 1_700_000_000

ALT_PROFILE = CiProfile(
    linux_code_check='alt-linux-check',
    docs_check='alt-docs-check',
    gateway_check='alt-gateway-check',
    ai_loop_check='alt-ai-loop-check',
    macos_check='alt-macos-check',
    linux_workflow_file='alt-linux-ci.yml',
    macos_workflow_file='alt-mac-ci.yml',
)


def _stamp(epoch):
    import time
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(epoch))


class _FakeGh:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.calls = []

    def __call__(self, root, args):
        self.calls.append((root, list(args)))
        return types.SimpleNamespace(returncode=self.returncode)


class _DueGitHub:
    def __init__(self, workflow_file, mac_check, *, verified=False):
        self.workflow_file = workflow_file
        self.mac_check = mac_check
        self.verified = verified
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        if path == 'branches/main':
            return {'commit': {'sha': SHA, 'commit': {'committer': {'date': _stamp(0)}}}}
        if path == f'commits/{SHA}/pulls':
            return [{'merge_commit_sha': SHA, 'merged_at': _stamp(T0)}]
        expected_runs = due.runs_path(self.workflow_file)
        if path == expected_runs:
            if not self.verified:
                return {'workflow_runs': []}
            return {'workflow_runs': [
                {'id': 11, 'status': 'completed', 'conclusion': 'success', 'head_sha': SHA}
            ]}
        if path == 'actions/runs/11/jobs?per_page=100':
            names = due.SHARD_JOBS + (self.mac_check,)
            return {'jobs': [{'name': name, 'conclusion': 'success'} for name in names]}
        raise AssertionError(f'unexpected path: {path}')


class _SuiteGitHub:
    def __init__(self, workflow_file):
        self.workflow_file = workflow_file
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        expected = (
            f'actions/workflows/{self.workflow_file}'
            '/runs?branch=main&event=workflow_dispatch&per_page=20'
        )
        if path == expected:
            return {'workflow_runs': []}
        raise AssertionError(f'unexpected path: {path}')


class _FetchGitHub:
    def __init__(self, linux_wf, mac_wf):
        self.linux_wf = linux_wf
        self.mac_wf = mac_wf
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        if path == fetch.MAIN:
            return {'commit': {'sha': SHA}}
        if path == fetch.RUNS.format(workflow=self.linux_wf, event='push',
                                     per_page=fetch.RUNS_PER_PAGE):
            return {'workflow_runs': [
                {'id': 101, 'head_sha': SHA, 'run_attempt': 1,
                 'status': 'completed', 'conclusion': 'success'}
            ]}
        if path == fetch.RUNS.format(workflow=self.mac_wf, event='workflow_dispatch',
                                     per_page=fetch.RUNS_PER_PAGE):
            return {'workflow_runs': [
                {'id': 202, 'head_sha': SHA, 'run_attempt': 1,
                 'status': 'completed', 'conclusion': 'success'}
            ]}
        if path == fetch.JOBS.format(run_id=101, per_page=fetch.JOBS_PER_PAGE, page=1):
            return {'total_count': 1, 'jobs': [
                {'id': 1, 'name': 'check (Linux 3.14)', 'run_id': 101,
                 'run_attempt': 1, 'head_sha': SHA, 'status': 'completed',
                 'conclusion': 'success'}
            ]}
        if path == fetch.JOBS.format(run_id=202, per_page=fetch.JOBS_PER_PAGE, page=1):
            jobs = [
                {'id': 2 + idx, 'name': name, 'run_id': 202, 'run_attempt': 1,
                 'head_sha': SHA, 'status': 'completed', 'conclusion': 'success'}
                for idx, name in enumerate(fetch.FULL_SHARDS)
            ]
            jobs.append({'id': 5, 'name': 'check (macOS 3.13)', 'run_id': 202,
                         'run_attempt': 1, 'head_sha': SHA, 'status': 'completed',
                         'conclusion': 'success'})
            return {'total_count': len(jobs), 'jobs': jobs}
        raise AssertionError(f'unexpected path: {path}')


class R1DefaultProfileTests(unittest.TestCase):
    def test_mac_full_due_uses_default_profile(self):
        defaults = default_profile()
        gh = _FakeGh()
        github = _DueGitHub(defaults.macos_workflow_file, defaults.macos_check)
        code, detail = due.request_full(gh, '/repo', github, now=T0 + due.QUIET_SECONDS)
        self.assertEqual(code, due.DISPATCHED)
        self.assertEqual(detail['main_sha'], SHA)
        self.assertIn(due.RUNS, github.calls)
        self.assertEqual(gh.calls, [('/repo', ['workflow', 'run', 'llmtool-mac-ci.yml', '--ref', 'main'])])

    def test_mac_full_suite_uses_default_profile(self):
        defaults = default_profile()
        gh = _FakeGh()
        github = _SuiteGitHub(defaults.macos_workflow_file)
        code = suite.request_full_suite(gh, '/repo', github)
        self.assertEqual(code, suite.MAC_FULL_SUITE_DISPATCHED)
        self.assertEqual(gh.calls, [('/repo', ['workflow', 'run', 'llmtool-mac-ci.yml', '--ref', 'main'])])

    def test_main_ci_job_fetch_uses_default_profile(self):
        defaults = default_profile()
        github = _FetchGitHub(defaults.linux_workflow_file, defaults.macos_workflow_file)
        result = fetch.inspect_jobs(github)
        self.assertEqual(result['state'], fetch.GREEN)
        self.assertEqual(
            [run['workflow'] for run in result['runs']],
            ['llmtool-linux-ci.yml', 'llmtool-mac-ci.yml'],
        )


class R2SubstitutedProfileTests(unittest.TestCase):
    def test_mac_full_due_uses_substituted_profile_workflow_and_check(self):
        for via_patch in (False, True):
            with self.subTest(via_patch=via_patch):
                gh = _FakeGh()
                github = _DueGitHub(ALT_PROFILE.macos_workflow_file, ALT_PROFILE.macos_check)
                if via_patch:
                    with mock.patch.object(ci_profile, 'default_profile', return_value=ALT_PROFILE):
                        code, _ = due.request_full(gh, '/repo', github, now=T0 + due.QUIET_SECONDS)
                else:
                    code, _ = due.request_full(gh, '/repo', github, now=T0 + due.QUIET_SECONDS,
                                               profile=ALT_PROFILE)
                self.assertEqual(code, due.DISPATCHED)
                self.assertEqual(gh.calls, [('/repo', ['workflow', 'run', 'alt-mac-ci.yml', '--ref', 'main'])])
                self.assertTrue(all('llmtool-mac-ci.yml' not in call for call in github.calls))

    def test_mac_full_due_trusted_check_requires_profile_macos_check(self):
        gh = _FakeGh()
        # Run has the original macOS check name, NOT ALT_PROFILE.macos_check -> not trusted -> DISPATCHED.
        github_original_check = _DueGitHub(
            ALT_PROFILE.macos_workflow_file, 'check (macOS 3.13)', verified=True)
        code, _ = due.request_full(gh, '/repo', github_original_check,
                                   now=T0 + due.QUIET_SECONDS, profile=ALT_PROFILE)
        self.assertEqual(code, due.DISPATCHED)

        # Run has ALT_PROFILE.macos_check -> trusted -> VERIFIED.
        gh2 = _FakeGh()
        github_alt_check = _DueGitHub(
            ALT_PROFILE.macos_workflow_file, ALT_PROFILE.macos_check, verified=True)
        code2, _ = due.request_full(gh2, '/repo', github_alt_check,
                                    now=T0 + due.QUIET_SECONDS, profile=ALT_PROFILE)
        self.assertEqual(code2, due.VERIFIED)
        self.assertEqual(gh2.calls, [])

    def test_mac_full_due_run_ci_and_train_use_substituted_profile(self):
        gh = _FakeGh()
        github = _DueGitHub(ALT_PROFILE.macos_workflow_file, ALT_PROFILE.macos_check)
        with mock.patch.object(ci_profile, 'default_profile', return_value=ALT_PROFILE):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = due.run_ci(None, gh_runner=gh, github_get=github,
                                root='/repo', now=T0 + due.QUIET_SECONDS)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue())['code'], due.DISPATCHED)
        self.assertEqual(gh.calls, [('/repo', ['workflow', 'run', 'alt-mac-ci.yml', '--ref', 'main'])])

        gh_train = _FakeGh()
        github_train = _DueGitHub(ALT_PROFILE.macos_workflow_file, ALT_PROFILE.macos_check)
        train_code = due.request_train_full(gh_train, '/repo', github_train, profile=ALT_PROFILE)
        self.assertEqual(train_code, due.DISPATCHED)
        self.assertEqual(gh_train.calls, [('/repo', ['workflow', 'run', 'alt-mac-ci.yml', '--ref', 'main'])])

    def test_mac_full_suite_uses_substituted_profile(self):
        for via_patch in (False, True):
            with self.subTest(via_patch=via_patch):
                gh = _FakeGh()
                github = _SuiteGitHub(ALT_PROFILE.macos_workflow_file)
                if via_patch:
                    with mock.patch.object(ci_profile, 'default_profile', return_value=ALT_PROFILE):
                        code = suite.request_full_suite(gh, '/repo', github)
                else:
                    code = suite.request_full_suite(gh, '/repo', github, profile=ALT_PROFILE)
                self.assertEqual(code, suite.MAC_FULL_SUITE_DISPATCHED)
                self.assertEqual(gh.calls, [('/repo', ['workflow', 'run', 'alt-mac-ci.yml', '--ref', 'main'])])
                self.assertTrue(all('llmtool-mac-ci.yml' not in call for call in github.calls))

    def test_main_ci_job_fetch_uses_substituted_profile(self):
        for via_patch in (False, True):
            with self.subTest(via_patch=via_patch):
                github = _FetchGitHub(ALT_PROFILE.linux_workflow_file, ALT_PROFILE.macos_workflow_file)
                if via_patch:
                    with mock.patch.object(ci_profile, 'default_profile', return_value=ALT_PROFILE):
                        result = fetch.inspect_jobs(github)
                else:
                    result = fetch.inspect_jobs(github, profile=ALT_PROFILE)
                self.assertEqual(result['state'], fetch.GREEN)
                self.assertEqual(
                    [run['workflow'] for run in result['runs']],
                    ['alt-linux-ci.yml', 'alt-mac-ci.yml'],
                )
                self.assertTrue(all('llmtool-linux-ci.yml' not in call
                                    and 'llmtool-mac-ci.yml' not in call
                                    for call in github.calls))


class R3InvalidOrAmbiguousProfileTests(unittest.TestCase):
    def _bad_profiles(self):
        mutated = CiProfile(
            linux_code_check='a', docs_check='b', gateway_check='c',
            ai_loop_check='d', macos_check='e',
            linux_workflow_file='l.yml', macos_workflow_file='m.yml',
        )
        object.__setattr__(mutated, 'macos_workflow_file', '')
        bad_path = CiProfile(
            linux_code_check='a', docs_check='b', gateway_check='c',
            ai_loop_check='d', macos_check='e',
            linux_workflow_file='l.yml', macos_workflow_file='dir/m.yml',
        )
        return (
            ('missing_none', None),
            ('invalid_str', 'llmtool-mac-ci.yml'),
            ('invalid_dict', {'macos_workflow_file': 'llmtool-mac-ci.yml'}),
            ('invalid_namespace', types.SimpleNamespace(macos_workflow_file='llmtool-mac-ci.yml')),
            ('mutated_empty', mutated),
            ('path_separator', bad_path),
            ('ambiguous_list', [ALT_PROFILE, default_profile()]),
            ('ambiguous_tuple', (ALT_PROFILE,)),
        )

    def test_direct_bad_profile_refused_without_dispatch_or_reads(self):
        for kind, bad in self._bad_profiles():
            with self.subTest(kind=kind):
                gh = _FakeGh()
                github = mock.Mock(side_effect=AssertionError('github_get must not be called'))

                code, detail = due.request_full(gh, '/repo', github, now=T0, profile=bad)
                self.assertEqual(code, due.PROFILE_INVALID)
                self.assertEqual(detail, {'profile': 'invalid'})
                self.assertEqual(gh.calls, [])

                train_code = due.request_train_full(gh, '/repo', github, profile=bad)
                self.assertEqual(train_code, due.PROFILE_INVALID)
                self.assertEqual(gh.calls, [])

                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    rc = due.run_ci(None, gh_runner=gh, github_get=github, root='/repo',
                                    now=T0, profile=bad)
                self.assertEqual(rc, 2)
                self.assertEqual(json.loads(out.getvalue())['code'], due.PROFILE_INVALID)
                self.assertIn(due.PROFILE_INVALID, err.getvalue())
                self.assertEqual(gh.calls, [])

                suite_code = suite.request_full_suite(gh, '/repo', github, profile=bad)
                self.assertEqual(suite_code, suite.MAC_FULL_SUITE_PROFILE_INVALID)
                self.assertEqual(gh.calls, [])

                fetch_result = fetch.inspect_jobs(github, profile=bad)
                self.assertEqual(fetch_result['state'], fetch.UNKNOWN)
                self.assertEqual(fetch_result['reason'], fetch.PROFILE_INVALID)
                self.assertEqual(fetch_result['runs'], [])
                self.assertEqual(fetch_result['failing'], [])

    def test_patched_default_profile_error_or_bad_value_refused(self):
        for kind, bad in self._bad_profiles():
            with self.subTest(kind=kind):
                gh = _FakeGh()
                github = mock.Mock(side_effect=AssertionError('github_get must not be called'))
                with mock.patch.object(ci_profile, 'default_profile', return_value=bad):
                    self.assertEqual(due.request_full(gh, '/repo', github, now=T0)[0], due.PROFILE_INVALID)
                    self.assertEqual(suite.request_full_suite(gh, '/repo', github),
                                     suite.MAC_FULL_SUITE_PROFILE_INVALID)
                    self.assertEqual(fetch.inspect_jobs(github)['reason'], fetch.PROFILE_INVALID)
                self.assertEqual(gh.calls, [])

        for exc in (CiProfileError(), RuntimeError('missing profile')):
            with self.subTest(exc=type(exc).__name__):
                gh = _FakeGh()
                github = mock.Mock(side_effect=AssertionError('github_get must not be called'))
                with mock.patch.object(ci_profile, 'default_profile', side_effect=exc):
                    self.assertEqual(due.request_full(gh, '/repo', github, now=T0)[0], due.PROFILE_INVALID)
                    self.assertEqual(suite.request_full_suite(gh, '/repo', github),
                                     suite.MAC_FULL_SUITE_PROFILE_INVALID)
                    self.assertEqual(fetch.inspect_jobs(github)['reason'], fetch.PROFILE_INVALID)
                self.assertEqual(gh.calls, [])


if __name__ == '__main__':
    unittest.main()
