"""#2793 A13.0 wiring (a): CI profile wiring for main_incident and main_incident_cli."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import ci_profile  # noqa: E402
from llmtool import cli  # noqa: E402
from llmtool import mac_full_due as due  # noqa: E402
from llmtool import main_ci_job_fetch as fetch  # noqa: E402
from llmtool import main_incident as mi  # noqa: E402
from llmtool import main_incident_cli as mic  # noqa: E402
from llmtool import main_incident_store as store  # noqa: E402
from llmtool import pm_caller  # noqa: E402
from llmtool.ci_profile import CiProfile, CiProfileError, default_profile  # noqa: E402

A, B = 'a' * 40, 'b' * 40
TEST_ID = 'tests.test_mod.Case.test_x'
FAIL_LINE = 'FAIL: test_x (tests.test_mod.Case)'
CHANGED = 'tools/llmtool/llmtool/x.py'
OWNER_SESSION = '00000000-0000-4000-8000-000000000001'
NOW = 1794528000

ALT_PROFILE = CiProfile(
    linux_code_check='alt-linux-check',
    docs_check='alt-docs-check',
    gateway_check='alt-gateway-check',
    ai_loop_check='alt-ai-loop-check',
    macos_check='alt-macos-check',
    linux_workflow_file='alt-linux-ci.yml',
    macos_workflow_file='alt-mac-ci.yml',
)


def _linux_run(run_id, sha, conclusion='success', *, name, **extra):
    run = {'id': run_id, 'name': name, 'head_sha': sha, 'status': 'completed',
           'conclusion': conclusion}
    run.update(extra)
    return run


def _full_run(run_id, sha, conclusion='success', *, macos_check, **extra):
    job_conclusion = 'success' if conclusion == 'success' else 'failure'
    jobs = [{'name': name, 'conclusion': job_conclusion}
            for name in due.SHARD_JOBS + (macos_check,)]
    run = {'id': run_id, 'event': 'workflow_dispatch', 'head_branch': 'main',
           'head_sha': sha, 'status': 'completed', 'conclusion': conclusion, 'jobs': jobs}
    run.update(extra)
    return run


class _IncidentGitHub:
    """Read-only GitHub fake parameterized by CiProfile."""

    def __init__(self, commits, profile):
        self.commits = list(commits)
        self.profile = profile
        self.linux = {}
        self.mac = []
        self.jobs = {}
        self.pulls = {}
        self.files = {}
        self.annotations = {}
        self.calls = []
        self._next = 1

    def _id(self):
        self._next += 1
        return self._next

    def add_linux(self, sha, conclusion, *, check_name=None, tests=False):
        run_id = self._id()
        name = self.profile.linux_code_check if check_name is None else check_name
        self.linux.setdefault(sha, []).append(
            {'id': run_id, 'name': name, 'head_sha': sha, 'status': 'completed',
             'conclusion': conclusion})
        if tests:
            self.annotations[run_id] = [{'message': FAIL_LINE}]
        return run_id

    def add_mac(self, sha, conclusion, *, macos_check=None, tests=False):
        run_id = 1000 + self._id()
        check = self.profile.macos_check if macos_check is None else macos_check
        self.mac.insert(0, {'id': run_id, 'head_sha': sha, 'head_branch': 'main',
                            'event': 'workflow_dispatch', 'status': 'completed',
                            'conclusion': conclusion})
        jobs = []
        for idx, name in enumerate(due.SHARD_JOBS + (check,)):
            failed = conclusion != 'success' and idx == 0
            jobs.append({'id': run_id * 10 + idx, 'name': name,
                         'conclusion': 'failure' if failed else 'success',
                         'steps': [{'name': 'Run tests',
                                    'conclusion': 'failure' if failed else 'success'}]})
            if failed and tests:
                self.annotations[run_id * 10 + idx] = [{'message': FAIL_LINE}]
        self.jobs[run_id] = jobs
        return run_id

    def passed(self, sha):
        self.add_linux(sha, 'success')
        self.add_mac(sha, 'success')

    def code_failure(self, sha):
        self.add_linux(sha, 'failure', tests=True)
        self.add_mac(sha, 'failure', tests=True)

    def merged(self, sha, pr, files=(CHANGED,)):
        self.pulls[sha] = [{'number': pr, 'merge_commit_sha': sha}]
        self.files[pr] = [{'filename': name} for name in files]

    def __call__(self, path):
        self.calls.append(path)
        if path == mic.COMMITS:
            return [{'sha': sha} for sha in self.commits]
        if path == due.runs_path(self.profile.macos_workflow_file):
            return {'workflow_runs': self.mac}
        for sha in self.commits:
            if path == mic.linux_runs_path(self.profile.linux_code_check, sha):
                return {'check_runs': self.linux.get(sha, [])}
            if path == f'commits/{sha}/pulls':
                return self.pulls.get(sha, [])
        for run_id, jobs in self.jobs.items():
            if path == f'actions/runs/{run_id}/jobs?per_page=100':
                return {'jobs': jobs}
        for pr, files in self.files.items():
            if path == f'pulls/{pr}/files?per_page={mic.MAX_FILES}':
                return files
        for check_id, items in self.annotations.items():
            if path == f'check-runs/{check_id}/annotations?per_page=100':
                return items
        raise KeyError(path)


class R1DefaultProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        home = mock.patch.dict(os.environ, {'HOME': self.tmp.name})
        home.start()
        self.addCleanup(home.stop)
        fence = mock.patch.object(pm_caller, 'fence_refusal_code', return_value=None)
        fence.start()
        self.addCleanup(fence.stop)
        self.root = Path(self.tmp.name) / 'state' / 'tasks'
        self.state_root = Path(self.tmp.name) / 'pm-state'

    def test_main_incident_default_profile_matches_original_behavior(self):
        defaults = default_profile()
        history = [{'sha': B, 'pr': 2}, {'sha': A, 'pr': 1}]
        runs = [
            _full_run(4, B, 'failure', macos_check=defaults.macos_check, failed_tests=[TEST_ID]),
            _linux_run(3, B, 'failure', name=defaults.linux_code_check, failed_tests=[TEST_ID]),
            _full_run(2, A, 'success', macos_check=defaults.macos_check),
            _linux_run(1, A, 'success', name=defaults.linux_code_check),
        ]
        result = mi.classify_incident(history, runs)
        self.assertTrue(result['incident'])
        self.assertEqual((result['pass_sha'], result['fail_sha'], result['class']),
                         (A, B, mi.CODE))

    def test_main_incident_cli_scan_default_profile(self):
        defaults = default_profile()
        github = _IncidentGitHub([B, A], defaults)
        github.passed(A)
        github.code_failure(B)
        github.merged(B, 12)
        args = cli.parser_for_cli().parse_args(
            ['ci', 'main-incident', 'scan', '--pm-session', OWNER_SESSION])
        out = io.StringIO()
        with redirect_stdout(out):
            status = mic.run_main_incident(
                args, github_get=github, root=self.root, environ={},
                state_root=self.state_root, now=NOW)
        payload = json.loads(out.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(payload['code'], mic.CREATED)
        self.assertEqual(payload['class'], mi.CODE)
        self.assertIn(due.runs_path(defaults.macos_workflow_file), github.calls)
        self.assertIn(mic.linux_runs_path(defaults.linux_code_check, B), github.calls)


class R2SubstitutedProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        home = mock.patch.dict(os.environ, {'HOME': self.tmp.name})
        home.start()
        self.addCleanup(home.stop)
        fence = mock.patch.object(pm_caller, 'fence_refusal_code', return_value=None)
        fence.start()
        self.addCleanup(fence.stop)
        self.root = Path(self.tmp.name) / 'state' / 'tasks'
        self.state_root = Path(self.tmp.name) / 'pm-state'

    def test_main_incident_uses_substituted_profile_and_ignores_original_names(self):
        defaults = default_profile()
        history = [{'sha': B, 'pr': 2}, {'sha': A, 'pr': 1}]
        alt_runs = [
            _full_run(4, B, 'failure', macos_check=ALT_PROFILE.macos_check,
                      failed_tests=[TEST_ID]),
            _linux_run(3, B, 'failure', name=ALT_PROFILE.linux_code_check,
                       failed_tests=[TEST_ID]),
            _full_run(2, A, 'success', macos_check=ALT_PROFILE.macos_check),
            _linux_run(1, A, 'success', name=ALT_PROFILE.linux_code_check),
        ]
        orig_runs = [
            _full_run(2, A, 'success', macos_check=defaults.macos_check),
            _linux_run(1, A, 'success', name=defaults.linux_code_check),
        ]
        for via_patch in (False, True):
            with self.subTest(via_patch=via_patch):
                if via_patch:
                    with mock.patch.object(ci_profile, 'default_profile', return_value=ALT_PROFILE):
                        self.assertEqual(mi.run_kind(alt_runs[1]), mi.LINUX)
                        self.assertEqual(mi.run_kind(alt_runs[0]), mi.FULL_MAC)
                        self.assertIsNone(mi.run_kind(orig_runs[1]))
                        self.assertIsNone(mi.run_kind(orig_runs[0]))
                        self.assertFalse(mi.trusted_pass(orig_runs, A))
                        result = mi.classify_incident(history, alt_runs)
                else:
                    self.assertEqual(mi.run_kind(alt_runs[1], profile=ALT_PROFILE), mi.LINUX)
                    self.assertEqual(mi.run_kind(alt_runs[0], profile=ALT_PROFILE), mi.FULL_MAC)
                    self.assertIsNone(mi.run_kind(orig_runs[1], profile=ALT_PROFILE))
                    self.assertIsNone(mi.run_kind(orig_runs[0], profile=ALT_PROFILE))
                    self.assertFalse(mi.trusted_pass(orig_runs, A, profile=ALT_PROFILE))
                    result = mi.classify_incident(history, alt_runs, profile=ALT_PROFILE)
                self.assertEqual((result['pass_sha'], result['fail_sha'], result['class']),
                                 (A, B, mi.CODE))

    def test_main_incident_cli_scan_uses_substituted_profile(self):
        defaults = default_profile()
        for via_patch in (False, True):
            with self.subTest(via_patch=via_patch):
                root = self.root / f'patch_{via_patch}' / 'tasks'
                github = _IncidentGitHub([B, A], ALT_PROFILE)
                github.passed(A)
                github.code_failure(B)
                github.merged(B, 12)
                args = cli.parser_for_cli().parse_args(
                    ['ci', 'main-incident', 'scan', '--pm-session', OWNER_SESSION])
                out = io.StringIO()
                with redirect_stdout(out):
                    if via_patch:
                        with mock.patch.object(ci_profile, 'default_profile',
                                               return_value=ALT_PROFILE):
                            status = mic.run_main_incident(
                                args, github_get=github, root=root, environ={},
                                state_root=self.state_root, now=NOW)
                    else:
                        status = mic.run_main_incident(
                            args, github_get=github, root=root, environ={},
                            state_root=self.state_root, now=NOW, profile=ALT_PROFILE)
                payload = json.loads(out.getvalue())
                self.assertEqual(status, 0)
                self.assertEqual(payload['code'], mic.CREATED)
                self.assertEqual(payload['class'], mi.CODE)
                self.assertTrue(all(defaults.macos_workflow_file not in call
                                    for call in github.calls))
                self.assertTrue(all('check%20%28Linux%203.14%29' not in call
                                    for call in github.calls))
                self.assertIn(due.runs_path(ALT_PROFILE.macos_workflow_file), github.calls)
                self.assertIn(mic.linux_runs_path(ALT_PROFILE.linux_code_check, B), github.calls)

    def test_main_incident_cli_jobs_passes_profile(self):
        github = mock.Mock()
        args = cli.parser_for_cli().parse_args(['ci', 'main-incident', 'jobs'])
        out = io.StringIO()
        with mock.patch.object(fetch, 'inspect_jobs',
                               return_value={'state': fetch.GREEN, 'reason': None,
                                             'runs': [], 'failing': []}) as inspect_mock:
            with redirect_stdout(out):
                status = mic.run_main_incident(args, github_get=github, profile=ALT_PROFILE)
        self.assertEqual(status, 0)
        inspect_mock.assert_called_once_with(github, profile=ALT_PROFILE)


class R3InvalidOrAmbiguousProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        home = mock.patch.dict(os.environ, {'HOME': self.tmp.name})
        home.start()
        self.addCleanup(home.stop)
        fence = mock.patch.object(pm_caller, 'fence_refusal_code', return_value=None)
        fence.start()
        self.addCleanup(fence.stop)
        self.root = Path(self.tmp.name) / 'state' / 'tasks'
        self.state_root = Path(self.tmp.name) / 'pm-state'

    def _bad_profiles(self):
        mutated = CiProfile(
            linux_code_check='a', docs_check='b', gateway_check='c',
            ai_loop_check='d', macos_check='e',
            linux_workflow_file='l.yml', macos_workflow_file='m.yml',
        )
        object.__setattr__(mutated, 'linux_code_check', '')
        bad_path = CiProfile(
            linux_code_check='a', docs_check='b', gateway_check='c',
            ai_loop_check='d', macos_check='e',
            linux_workflow_file='l.yml', macos_workflow_file='dir/m.yml',
        )
        shard_collision = CiProfile(
            linux_code_check='a', docs_check='b', gateway_check='c',
            ai_loop_check='d', macos_check=due.SHARD_JOBS[0],
            linux_workflow_file='l.yml', macos_workflow_file='m.yml',
        )
        return (
            ('missing_none', None),
            ('invalid_str', 'check (Linux 3.14)'),
            ('invalid_dict', {'linux_code_check': 'check (Linux 3.14)'}),
            ('invalid_namespace', types.SimpleNamespace(linux_code_check='check (Linux 3.14)')),
            ('mutated_empty', mutated),
            ('path_separator', bad_path),
            ('ambiguous_list', [ALT_PROFILE, default_profile()]),
            ('ambiguous_tuple', (ALT_PROFILE,)),
            ('shard_collision', shard_collision),
        )

    def test_main_incident_and_cli_refuse_bad_profiles_without_reads(self):
        run = _linux_run(1, A, name=ALT_PROFILE.linux_code_check)
        history = [{'sha': A, 'pr': 1}]
        for kind, bad in self._bad_profiles():
            with self.subTest(kind=kind):
                github = mock.Mock(side_effect=AssertionError('github_get must not be called'))
                with self.assertRaises(due.ProfileError):
                    mi.run_kind(run, profile=bad)
                with self.assertRaises(due.ProfileError):
                    mi.trusted_pass([run], A, profile=bad)
                with self.assertRaises(due.ProfileError):
                    mi.classify_incident(history, [run], profile=bad)
                with self.assertRaises(due.ProfileError):
                    mic.collect(github, profile=bad)
                with self.assertRaises(due.ProfileError):
                    mic.scan_state(github, observed_at='2026-11-27T00:00:00Z', profile=bad)

                args = cli.parser_for_cli().parse_args(
                    ['ci', 'main-incident', 'scan', '--pm-session', OWNER_SESSION])
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    status = mic.run_main_incident(
                        args, github_get=github, root=self.root, environ={},
                        state_root=self.state_root, now=NOW, profile=bad)
                payload = json.loads(out.getvalue())
                self.assertEqual(status, 2)
                self.assertEqual(payload, {'code': mic.PROFILE_INVALID, 'profile': 'invalid'})
                self.assertEqual(err.getvalue(), f'llmtool: {mic.PROFILE_INVALID}\n')
                self.assertEqual(store.list_records(self.root), [])

    def test_patched_default_profile_error_refused(self):
        for exc in (CiProfileError(), RuntimeError('missing profile')):
            with self.subTest(exc=type(exc).__name__):
                github = mock.Mock(side_effect=AssertionError('github_get must not be called'))
                with mock.patch.object(ci_profile, 'default_profile', side_effect=exc):
                    with self.assertRaises(due.ProfileError):
                        mi.classify_incident([{'sha': A, 'pr': 1}], [])
                    args = cli.parser_for_cli().parse_args(
                        ['ci', 'main-incident', 'scan', '--pm-session', OWNER_SESSION])
                    out, err = io.StringIO(), io.StringIO()
                    with redirect_stdout(out), redirect_stderr(err):
                        status = mic.run_main_incident(
                            args, github_get=github, root=self.root, environ={},
                            state_root=self.state_root, now=NOW)
                    self.assertEqual(status, 2)
                    self.assertEqual(json.loads(out.getvalue())['code'], mic.PROFILE_INVALID)

                    jobs_args = cli.parser_for_cli().parse_args(['ci', 'main-incident', 'jobs'])
                    j_out, j_err = io.StringIO(), io.StringIO()
                    with redirect_stdout(j_out), redirect_stderr(j_err):
                        j_status = mic.run_main_incident(jobs_args, github_get=github)
                    self.assertEqual(j_status, 2)
                    self.assertEqual(json.loads(j_out.getvalue())['reason'], fetch.PROFILE_INVALID)

    def test_one_pass_resolves_profile_once(self):
        history = [{'sha': B, 'pr': 2}, {'sha': A, 'pr': 1}]
        runs = [
            _full_run(4, B, 'failure', macos_check=ALT_PROFILE.macos_check,
                      failed_tests=[TEST_ID]),
            _linux_run(3, B, 'failure', name=ALT_PROFILE.linux_code_check,
                       failed_tests=[TEST_ID]),
            _full_run(2, A, 'success', macos_check=ALT_PROFILE.macos_check),
            _linux_run(1, A, 'success', name=ALT_PROFILE.linux_code_check),
        ]
        with mock.patch.object(ci_profile, 'default_profile',
                               return_value=ALT_PROFILE) as patched:
            mi.classify_incident(history, runs)
            self.assertEqual(patched.call_count, 1)

        github = _IncidentGitHub([B, A], ALT_PROFILE)
        github.passed(A)
        github.code_failure(B)
        github.merged(B, 12)
        args = cli.parser_for_cli().parse_args(
            ['ci', 'main-incident', 'scan', '--pm-session', OWNER_SESSION])
        with mock.patch.object(ci_profile, 'default_profile',
                               return_value=ALT_PROFILE) as patched:
            out = io.StringIO()
            with redirect_stdout(out):
                status = mic.run_main_incident(
                    args, github_get=github, root=self.root, environ={},
                    state_root=self.state_root, now=NOW)
            self.assertEqual(status, 0)
            self.assertEqual(patched.call_count, 1)


if __name__ == '__main__':
    unittest.main()
