"""#2793 A13.0 wiring (a): CI profile wiring for the task_advance CI steps.

R1 default profile keeps today's behaviour. R2 a substituted profile drives the
CI wait, Mac label, capacity, timeout and failure routing through the real
advance entry points and none of the original names. R3 an unusable profile
refuses with a fixed code before any read, and one advance step resolves it once.
"""

from __future__ import annotations

from pathlib import Path
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import ci_profile, mac_full_due as due, task_advance  # noqa: E402
from llmtool.ci_profile import CiProfile, CiProfileError, default_profile  # noqa: E402

HEAD = 'a' * 40
TASK_ID = '1' * 32
EXECUTOR = 'executor-1'
PR = {'number': 17, 'head': {'sha': HEAD}, 'merged': False,
      'state': 'open', 'mergeable': True}
FILES = [{'filename': 'src/app.py'}]
MAC_FILES = [{'filename': 'tools/llmtool/llmtool/a.py'}]
WORKFLOW_RUN_ID = 101
JOB_ID = 202
DETAILS_URL = f'https://example.test/actions/runs/{WORKFLOW_RUN_ID}/job/{JOB_ID}'
CI_OUTPUT = {'title': 'Tests failed', 'summary': 'FAIL: test_example'}

ALT_PROFILE = CiProfile(
    linux_code_check='alt-linux-check',
    docs_check='alt-docs-check',
    gateway_check='alt-gateway-check',
    ai_loop_check='alt-ai-loop-check',
    macos_check='alt-macos-check',
    linux_workflow_file='alt-linux-ci.yml',
    macos_workflow_file='alt-mac-ci.yml',
)


def _record(**changes):
    record = {
        'task_id': TASK_ID,
        'run_id': '2' * 32,
        'developer': {'state': 'DEVELOPER_RESPONDED'},
        'completion': {
            'executor_task_id': EXECUTOR,
            'state': 'WAITING_REVIEW',
            'evidence': {'result': {'result_head': HEAD}},
        },
        'review_dispatch_claim': [],
        'review_repair_claims': [],
        'completion_events': [],
        'remote_test_outcomes': [{
            'result': 'ADMISSION_DENIED', 'code': 'MODULE_NOT_ADMITTED',
            'executor_task': EXECUTOR, 'sha': HEAD, 'modules': [],
        }],
    }
    record.update(changes)
    return record


def _d6():
    return {'root': '/repo/task', 'task_id': EXECUTOR, 'result_head': HEAD,
            'recorded_result_head': HEAD, 'dirty': False}


def _run(run_id, name, conclusion='success', *, head=HEAD, status='completed',
         details_url=None):
    run = {'id': run_id, 'name': name, 'head_sha': head, 'status': status,
           'conclusion': conclusion}
    if details_url is not None:
        run['details_url'] = details_url
    return run


def _checks(*runs):
    return {'total_count': len(runs), 'check_runs': list(runs)}


class _Store:
    def __init__(self, record):
        self.record = record

    def get(self, _task_id):
        return self.record

    def task_ids(self):
        return [self.record['task_id']]


class _NoReadStore:
    def get(self, _task_id):
        raise AssertionError('store.get must not be called')


class _GitHub:
    """Fake reads for one advance step; records every path it answered."""

    def __init__(self, check_runs=(), files=None, *, pr=None, workflow_run=None,
                 jobs=None):
        self.check_runs = list(check_runs)
        self.files = FILES if files is None else files
        self.pr = PR if pr is None else pr
        self.workflow_run = workflow_run
        self.jobs = jobs
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        if path == f'pulls/{PR["number"]}':
            return self.pr
        if path == f'commits/{HEAD}/check-runs?per_page=100':
            return _checks(*self.check_runs)
        if path == f'pulls/{PR["number"]}/files?per_page=100':
            return self.files
        if path.startswith('check-runs/'):
            run_id = int(path[len('check-runs/'):])
            run = next((item for item in self.check_runs
                        if item.get('id') == run_id), None)
            if run is None:
                raise RuntimeError('not found')
            return dict(run, output=CI_OUTPUT)
        if path.startswith('issues/') and path.endswith('/comments?per_page=100'):
            return []
        if path == f'actions/runs/{WORKFLOW_RUN_ID}':
            return self.workflow_run
        if path == f'actions/runs/{WORKFLOW_RUN_ID}/jobs?per_page=100':
            return self.jobs
        raise AssertionError(f'unexpected path: {path}')


def _advance(record, github, *, merge=False, **options):
    dispatched, repaired = [], []

    def dispatch(**kwargs):
        dispatched.append(kwargs)
        return {'state': 'DISPATCHED'}

    def repair(**kwargs):
        repaired.append(kwargs)
        return {'state': 'DISPATCHED'}

    with mock.patch.object(task_advance, '_d6_snapshot', return_value=_d6()), \
            mock.patch.object(task_advance, '_review_outcomes', return_value=[]):
        result = task_advance.advance(
            task_id=record['task_id'], store=_Store(record), pr_number=17,
            merge=merge, github_get=github, dispatch_review_call=dispatch,
            repair_review_call=repair, **options)
    return result, dispatched, repaired


def _merge_after_pass(record, github, *, profile=None):
    repaired = []

    def repair(**kwargs):
        repaired.append(kwargs)
        return {'state': 'DISPATCHED'}

    with mock.patch.object(task_advance, '_d6_snapshot', return_value=_d6()), \
            mock.patch.object(task_advance, '_review_outcomes', return_value=[]), \
            mock.patch.object(task_advance, '_merge_once', return_value={
                'step': 'MERGE_REFUSED',
                'codes': ['MERGE_CHECKS_NOT_GREEN'],
                'result': None,
            }):
        options = {} if profile is None else {'profile': profile}
        result = task_advance.advance(
            task_id=record['task_id'], store=_Store(record), pr_number=17,
            merge=True, github_get=github, repair_review_call=repair, **options)
    return result, repaired


def _label(record, github, *, profile=None):
    runner_calls = []

    def runner(root, args):
        runner_calls.append((root, list(args)))
        return types.SimpleNamespace(returncode=0)

    with mock.patch.object(task_advance, '_d6_snapshot', return_value=_d6()):
        options = {} if profile is None else {'profile': profile}
        result = task_advance.label_mac_ci_for_skipped_check(
            task_id=record['task_id'], store=_Store(record), pr_number=17,
            github_get=github, merge_gh_runner=runner, **options)
    return result, runner_calls


class R1DefaultProfileTests(unittest.TestCase):
    def test_pre_review_gate_uses_default_checks(self):
        defaults = default_profile()
        result, dispatched, repaired = _advance(
            _record(), _GitHub([_run(1, defaults.linux_code_check)], FILES))
        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(repaired, [])

    def test_default_failed_check_routes_to_repair(self):
        defaults = default_profile()
        result, dispatched, repaired = _advance(
            _record(), _GitHub([_run(1, defaults.linux_code_check, 'failure')], FILES))
        self.assertEqual(result['step'], 'CI_FAILED')
        self.assertEqual(dispatched, [])
        self.assertEqual(len(repaired), 1)
        self.assertEqual(repaired[0]['ci_failure']['check_name'],
                         defaults.linux_code_check)

    def test_default_mac_failure_after_pass_routes_to_repair(self):
        defaults = default_profile()
        result, repaired = _merge_after_pass(
            _record(), _GitHub([_run(5, defaults.macos_check, 'failure')], MAC_FILES))
        self.assertEqual(result['step'], 'CI_FAILED')
        self.assertEqual(len(repaired), 1)
        self.assertEqual(repaired[0]['ci_failure']['check_name'],
                         defaults.macos_check)

    def test_default_mac_label_uses_default_check(self):
        defaults = default_profile()
        github = _GitHub([
            _run(1, defaults.linux_code_check),
            _run(2, defaults.macos_check, 'skipped'),
        ], MAC_FILES)
        result, calls = _label(_record(), github)
        self.assertEqual(result['step'], 'WAITING_CI')
        self.assertEqual(calls, [('/repo/task', [
            'pr', 'edit', '17', '--add-label', 'mac-ci'])])


class R2SubstitutedProfileTests(unittest.TestCase):
    def _advance_variants(self, record, github):
        yield 'profile', lambda: _advance(record, github, profile=ALT_PROFILE)
        yield 'patched', lambda: self._patched(record, github)

    @staticmethod
    def _patched(record, github):
        with mock.patch.object(ci_profile, 'default_profile', return_value=ALT_PROFILE):
            return _advance(record, github)

    def test_pre_review_gate_uses_substituted_checks_and_rejects_original(self):
        for kind, call in self._advance_variants(
                _record(), _GitHub([_run(1, ALT_PROFILE.linux_code_check)], FILES)):
            with self.subTest(via=kind):
                result, dispatched, _ = call()
                self.assertEqual(result['step'], 'DISPATCH_REVIEW')
                self.assertEqual(len(dispatched), 1)

        for kind, call in self._advance_variants(
                _record(), _GitHub([_run(1, 'check (Linux 3.14)')], FILES)):
            with self.subTest(via=kind):
                result, dispatched, _ = call()
                self.assertEqual((result['step'], result['codes']),
                                 ('WAITING_CI', ['LINUX_CI_PENDING']))
                self.assertEqual(dispatched, [])

    def test_failed_and_timed_out_substituted_checks_route_with_their_name(self):
        for conclusion in ('failure', 'timed_out'):
            with self.subTest(conclusion=conclusion):
                result, _, repaired = _advance(
                    _record(),
                    _GitHub([_run(1, ALT_PROFILE.linux_code_check, conclusion)], FILES),
                    profile=ALT_PROFILE)
                self.assertEqual(result['step'], 'CI_FAILED')
                self.assertEqual(len(repaired), 1)
                self.assertEqual(repaired[0]['ci_failure']['check_name'],
                                 ALT_PROFILE.linux_code_check)

        # The original Linux name never satisfies the substituted profile.
        result, _, repaired = _advance(
            _record(), _GitHub([_run(1, 'check (Linux 3.14)', 'failure')], FILES),
            profile=ALT_PROFILE)
        self.assertEqual(result['step'], 'WAITING_CI')
        self.assertEqual(repaired, [])

    def test_mac_failure_after_pass_uses_substituted_check(self):
        result, repaired = _merge_after_pass(
            _record(),
            _GitHub([_run(5, ALT_PROFILE.macos_check, 'failure')], MAC_FILES),
            profile=ALT_PROFILE)
        self.assertEqual(result['step'], 'CI_FAILED')
        self.assertEqual(len(repaired), 1)
        self.assertEqual(repaired[0]['ci_failure']['check_name'],
                         ALT_PROFILE.macos_check)

        result, repaired = _merge_after_pass(
            _record(),
            _GitHub([_run(5, 'check (macOS 3.13)', 'failure')], MAC_FILES),
            profile=ALT_PROFILE)
        self.assertEqual(result['step'], 'MERGE_REFUSED')
        self.assertEqual(repaired, [])

    def test_mac_capacity_uses_substituted_check_and_workflow(self):
        mac_job = {
            'id': JOB_ID, 'name': ALT_PROFILE.macos_check, 'head_sha': HEAD,
            'conclusion': 'failure',
            'steps': [{'name': 'Shard results', 'conclusion': 'failure'}],
        }
        jobs = [
            {'id': 301, 'name': 'select', 'head_sha': HEAD,
             'conclusion': 'cancelled'},
            {'id': 302, 'name': 'shards (1/3)', 'head_sha': HEAD,
             'conclusion': 'skipped'},
            mac_job,
        ]
        github = _GitHub(
            [_run(5, ALT_PROFILE.macos_check, 'failure', details_url=DETAILS_URL)],
            MAC_FILES,
            workflow_run={
                'head_sha': HEAD,
                'path': f'.github/workflows/{ALT_PROFILE.macos_workflow_file}',
            },
            jobs={'total_count': len(jobs), 'jobs': jobs},
        )
        result, repaired = _merge_after_pass(_record(), github, profile=ALT_PROFILE)
        self.assertEqual(result['step'], 'WAITING_ENV')
        self.assertEqual(result['codes'], ['CI_ENV_MAC_CAPACITY_CANCELLED'])
        self.assertEqual(result['result']['check'], ALT_PROFILE.macos_check)
        self.assertEqual(repaired, [])

    def test_mac_label_uses_substituted_checks(self):
        github = _GitHub([
            _run(1, ALT_PROFILE.linux_code_check),
            _run(2, ALT_PROFILE.macos_check, 'skipped'),
        ], MAC_FILES)
        result, calls = _label(_record(), github, profile=ALT_PROFILE)
        self.assertEqual(result['step'], 'WAITING_CI')
        self.assertEqual(calls, [('/repo/task', [
            'pr', 'edit', '17', '--add-label', 'mac-ci'])])

        original = _GitHub([
            _run(1, 'check (Linux 3.14)'),
            _run(2, 'check (macOS 3.13)', 'skipped'),
        ], MAC_FILES)
        result, calls = _label(_record(), original, profile=ALT_PROFILE)
        self.assertIsNone(result)
        self.assertEqual(calls, [])


class R3InvalidOrAmbiguousProfileTests(unittest.TestCase):
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
        duplicate_check = CiProfile(
            linux_code_check='same', docs_check='b', gateway_check='c',
            ai_loop_check='d', macos_check='same',
            linux_workflow_file='l.yml', macos_workflow_file='m.yml',
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
            ('invalid_namespace', types.SimpleNamespace(
                linux_code_check='check (Linux 3.14)')),
            ('mutated_empty', mutated),
            ('path_separator', bad_path),
            ('duplicate_check', duplicate_check),
            ('shard_collision', shard_collision),
            ('ambiguous_list', [ALT_PROFILE, default_profile()]),
            ('ambiguous_tuple', (ALT_PROFILE,)),
        )

    def test_advance_refuses_bad_profile_before_any_read(self):
        for kind, bad in self._bad_profiles():
            for merge in (False, True):
                with self.subTest(kind=kind, merge=merge):
                    github = mock.Mock(side_effect=AssertionError(
                        'github_get must not be called'))
                    result = task_advance.advance(
                        task_id=TASK_ID, store=_NoReadStore(), pr_number=17,
                        merge=merge, github_get=github, profile=bad)
                    expected_step = 'MERGE_REFUSED' if merge else 'REFUSED'
                    self.assertEqual(result['step'], expected_step)
                    self.assertEqual(result['codes'],
                                     [task_advance.MERGE_CI_PROFILE_INVALID])
                    github.assert_not_called()

    def test_direct_helpers_refuse_bad_profile_before_any_read(self):
        for kind, bad in self._bad_profiles():
            with self.subTest(kind=kind):
                github = mock.Mock(side_effect=AssertionError(
                    'github_get must not be called'))
                self.assertEqual(
                    task_advance._review_ci_gate(17, HEAD, github, profile=bad),
                    ('STOPPED', task_advance.MERGE_CI_PROFILE_INVALID))
                repair = mock.Mock()
                self.assertEqual(
                    task_advance._route_ci_failure(
                        store=None, record={}, task_id=TASK_ID,
                        executor_task=EXECUTOR, number=17, d6={}, related={},
                        run={'id': 1, 'name': 'check (Linux 3.14)',
                             'conclusion': 'failure'},
                        fail_code='LINUX_CI_FAILED', github_get=github,
                        repair_review_call=repair, profile=bad),
                    ('REFUSED', [task_advance.MERGE_CI_PROFILE_INVALID], None))
                self.assertIsNone(task_advance._mac_capacity_cancel(
                    {'id': 1, 'name': 'check (macOS 3.13)',
                     'conclusion': 'failure'}, HEAD, github, 17, profile=bad))
                github.assert_not_called()
                repair.assert_not_called()

    def test_patched_default_profile_error_refuses_before_any_read(self):
        for exc in (CiProfileError(), RuntimeError('missing profile')):
            with self.subTest(exc=type(exc).__name__):
                github = mock.Mock(side_effect=AssertionError(
                    'github_get must not be called'))
                with mock.patch.object(ci_profile, 'default_profile', side_effect=exc):
                    result = task_advance.advance(
                        task_id=TASK_ID, store=_NoReadStore(), pr_number=17,
                        github_get=github)
                self.assertEqual(result['step'], 'REFUSED')
                self.assertEqual(result['codes'],
                                 [task_advance.MERGE_CI_PROFILE_INVALID])
                github.assert_not_called()

    def test_one_advance_step_resolves_profile_once(self):
        github = _GitHub([_run(1, ALT_PROFILE.linux_code_check)], FILES)
        with mock.patch.object(ci_profile, 'default_profile',
                               return_value=ALT_PROFILE) as patched:
            result, dispatched, _ = _advance(_record(), github)
        self.assertEqual(result['step'], 'DISPATCH_REVIEW')
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(patched.call_count, 1)


if __name__ == '__main__':
    unittest.main()
