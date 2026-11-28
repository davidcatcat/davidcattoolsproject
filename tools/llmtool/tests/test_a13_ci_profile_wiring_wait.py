"""#2793 A13.0 slice 6: advance CI wait, Mac capacity and CI routing read the CI profile."""

from __future__ import annotations

import contextlib
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import ci_profile, task_advance  # noqa: E402
from llmtool.ci_profile import CiProfile, default_profile  # noqa: E402

try:
    import test_task_advance as tta  # noqa: E402
except ImportError:
    from tests import test_task_advance as tta  # noqa: E402

HEAD = tta.HEAD
NUMBER = 17
WORKFLOW_RUN_ID = 101
JOB_ID = 202
DETAILS_URL = f'https://example.test/actions/runs/{WORKFLOW_RUN_ID}/job/{JOB_ID}'
CHECKS_READ = f'commits/{HEAD}/check-runs?per_page=100'
FILES = [{'filename': 'src/app.py'}]
_NO_PROFILE = object()

ALT_PROFILE = CiProfile(
    linux_code_check='wait-alt-linux-check',
    docs_check='wait-alt-docs-check',
    gateway_check='wait-alt-gateway-check',
    ai_loop_check='wait-alt-ai-loop-check',
    macos_check='wait-alt-macos-check',
    linux_workflow_file='wait-alt-linux-ci.yml',
    macos_workflow_file='wait-alt-mac-ci.yml',
)
DEFAULTS = default_profile()
NOT_GREEN = {'step': 'MERGE_REFUSED', 'codes': ['MERGE_CHECKS_NOT_GREEN'], 'result': None}


def setUpModule():
    tta.setUpModule()


def tearDownModule():
    tta.tearDownModule()


def _bad_profiles():
    duplicate = CiProfile(
        linux_code_check='same', docs_check='b', gateway_check='c',
        ai_loop_check='d', macos_check='same',
        linux_workflow_file='l.yml', macos_workflow_file='m.yml',
    )
    bad_path = CiProfile(
        linux_code_check='a', docs_check='b', gateway_check='c',
        ai_loop_check='d', macos_check='e',
        linux_workflow_file='l.yml', macos_workflow_file='dir/m.yml',
    )
    return (
        ('missing_none', None),
        ('invalid_str', DEFAULTS.linux_code_check),
        ('invalid_namespace', types.SimpleNamespace(macos_check=DEFAULTS.macos_check)),
        ('duplicate_check', duplicate),
        ('path_separator', bad_path),
    )


def _reply(*runs):
    return {'total_count': len(runs), 'check_runs': list(runs)}, FILES


def _advance(reply, *, profile=_NO_PROFILE, merge=False, merge_once=None):
    """One real advance step on a fake GitHub; returns result, reads, dispatches, repairs."""
    record = tta.base_record()
    store = tta.CiGateStore(record)
    calls, dispatched, repaired = [], [], []
    gh = lambda *_args: types.SimpleNamespace(returncode=0, stdout='', stderr='')  # noqa: E731
    options = {} if profile is _NO_PROFILE else {'profile': profile}
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(task_advance, '_d6_snapshot', return_value=tta.d6()))
        stack.enter_context(mock.patch.object(task_advance, '_review_outcomes', return_value=[]))
        if merge:
            stack.enter_context(mock.patch.object(
                task_advance, '_merged_review_target', return_value=None))
        if merge_once is not None:
            stack.enter_context(mock.patch.object(task_advance, '_merge_once', merge_once))
        result = task_advance.advance(
            task_id=record['task_id'], store=store, pr_number=NUMBER, merge=merge,
            github_get=tta.ci_github_get(reply, calls),
            dispatch_review_call=lambda **kw: dispatched.append(kw) or {'sent': True},
            repair_review_call=lambda **kw: repaired.append(kw) or {'repair': 'sent'},
            publish_gh_runner=gh, merge_gh_runner=gh, **options)
    return result, calls, dispatched, repaired


def _capacity_fixture(*, check=ALT_PROFILE.macos_check,
                      workflow='.github/workflows/' + ALT_PROFILE.macos_workflow_file):
    run = {'id': 1, 'name': check, 'conclusion': 'failure', 'head_sha': HEAD,
           'details_url': DETAILS_URL}
    jobs = [
        {'name': 'select', 'conclusion': 'cancelled', 'head_sha': HEAD, 'id': 301},
        {'name': 'shards (1/2)', 'conclusion': 'skipped', 'head_sha': HEAD, 'id': 302},
        {'name': 'shards (2/2)', 'conclusion': 'skipped', 'head_sha': HEAD, 'id': 303},
        {'name': check, 'conclusion': 'failure', 'head_sha': HEAD, 'id': JOB_ID,
         'steps': [{'name': 'Shard results', 'conclusion': 'failure'}]},
    ]
    reads = []

    def github_get(path):
        reads.append(path)
        if path == f'pulls/{NUMBER}/files?per_page=100':
            return [{'filename': 'tools/llmtool/llmtool/task_advance.py'}]
        if path == f'actions/runs/{WORKFLOW_RUN_ID}':
            return {'head_sha': HEAD, 'path': workflow}
        if path == f'actions/runs/{WORKFLOW_RUN_ID}/jobs?per_page=100':
            return {'total_count': len(jobs), 'jobs': jobs}
        raise AssertionError(f'unexpected path: {path}')

    return run, github_get, reads


def _route(run, github_get, repairs, **options):
    record = {'task_id': '1' * 32, 'run_id': '2' * 32,
              'developer': {'provider_profile': 'custom'}, 'review_repair_claims': []}
    return task_advance._route_ci_failure(
        store=tta.CiGateStore(record), record=record, task_id=record['task_id'],
        executor_task='executor-1', number=NUMBER,
        d6={'result_head': HEAD, 'root': '/dummy/worktree'}, related={}, run=run,
        fail_code='MAC_CI_FAILED', github_get=github_get,
        repair_review_call=lambda **kw: repairs.append(kw) or {'repair': 'sent'},
        **options)


class DefaultProfileParityTests(unittest.TestCase):
    def test_explicit_default_profile_matches_legacy_advance(self):
        reply = _reply(tta.check_run(conclusion='failure'))
        legacy, legacy_calls, _, legacy_repairs = _advance(reply)
        explicit, explicit_calls, _, explicit_repairs = _advance(reply, profile=default_profile())
        self.assertEqual(explicit, legacy)
        self.assertEqual(explicit_calls, legacy_calls)
        self.assertEqual(len(legacy_repairs), 1)
        self.assertEqual(explicit_repairs[0]['ci_failure'], legacy_repairs[0]['ci_failure'])


class SubstitutedMacPathTests(unittest.TestCase):
    """(a) CI wait, Mac routing after PASS and Mac capacity use the profile names."""

    def test_merge_wait_routes_substituted_mac_check_only(self):
        merge_once = mock.Mock(return_value=dict(NOT_GREEN))
        alt = _reply(tta.check_run(4, name=ALT_PROFILE.linux_code_check),
                     tta.check_run(5, name=ALT_PROFILE.macos_check, conclusion='timed_out'))
        result, _, _, repaired = _advance(alt, profile=ALT_PROFILE, merge=True,
                                          merge_once=merge_once)
        self.assertEqual(result['step'], 'CI_FAILED')
        self.assertEqual(len(repaired), 1)
        self.assertEqual(repaired[0]['ci_failure']['check_name'], ALT_PROFILE.macos_check)
        # The step's resolved profile is passed down to the merge gate.
        self.assertIs(merge_once.call_args.kwargs['profile'], ALT_PROFILE)

        orig = _reply(tta.check_run(4, name=DEFAULTS.linux_code_check),
                      tta.check_run(5, name=DEFAULTS.macos_check, conclusion='timed_out'))
        result, _, _, repaired = _advance(orig, profile=ALT_PROFILE, merge=True,
                                          merge_once=mock.Mock(return_value=dict(NOT_GREEN)))
        self.assertEqual((result['step'], result['codes']),
                         ('MERGE_REFUSED', ['MERGE_CHECKS_NOT_GREEN']))
        self.assertEqual(repaired, [])

    def test_mac_capacity_uses_substituted_check_and_workflow_file(self):
        run, github_get, _ = _capacity_fixture()
        repairs = []
        step, codes, evidence = _route(run, github_get, repairs, profile=ALT_PROFILE)
        self.assertEqual((step, codes), ('WAITING_ENV', ['CI_ENV_MAC_CAPACITY_CANCELLED']))
        self.assertEqual(evidence['check'], ALT_PROFILE.macos_check)
        self.assertEqual(repairs, [])

        run, github_get, _ = _capacity_fixture()
        self.assertIsNone(task_advance._mac_capacity_cancel(run, HEAD, github_get, NUMBER))
        run, github_get, _ = _capacity_fixture(
            workflow='.github/workflows/' + DEFAULTS.macos_workflow_file)
        self.assertIsNone(task_advance._mac_capacity_cancel(
            run, HEAD, github_get, NUMBER, profile=ALT_PROFILE))
        run, github_get, _ = _capacity_fixture(check=DEFAULTS.macos_check)
        self.assertIsNone(task_advance._mac_capacity_cancel(
            run, HEAD, github_get, NUMBER, profile=ALT_PROFILE))


class SubstitutedLinuxRoutingTests(unittest.TestCase):
    """(b) CI failure routing before review follows the substituted Linux check name."""

    def test_substituted_linux_failure_routes_and_default_name_waits(self):
        for via_patch in (False, True):
            with self.subTest(via_patch=via_patch):
                options = {} if via_patch else {'profile': ALT_PROFILE}
                patcher = (mock.patch.object(ci_profile, 'default_profile',
                                             return_value=ALT_PROFILE)
                           if via_patch else contextlib.nullcontext())
                with patcher as patched:
                    result, _, dispatched, repaired = _advance(_reply(tta.check_run(
                        name=ALT_PROFILE.linux_code_check, conclusion='failure')), **options)
                    if via_patch:
                        self.assertEqual(patched.call_count, 1)
                    self.assertEqual(result['step'], 'CI_FAILED')
                    self.assertEqual(dispatched, [])
                    self.assertEqual(len(repaired), 1)
                    self.assertEqual(repaired[0]['ci_failure']['check_name'],
                                     ALT_PROFILE.linux_code_check)

                    result, _, dispatched, repaired = _advance(_reply(tta.check_run(
                        name=DEFAULTS.linux_code_check, conclusion='failure')), **options)
                    self.assertEqual((result['step'], result['codes']),
                                     ('WAITING_CI', ['LINUX_CI_PENDING']))
                    self.assertEqual((dispatched, repaired), ([], []))


class InvalidProfileTests(unittest.TestCase):
    """(c) An invalid profile refuses; the default names are never used instead."""

    def test_helpers_refuse_before_any_read(self):
        github = mock.Mock(side_effect=AssertionError('github_get must not be called'))
        run = {'id': 1, 'name': DEFAULTS.macos_check, 'conclusion': 'failure',
               'head_sha': HEAD, 'details_url': DETAILS_URL}
        for kind, bad in _bad_profiles():
            with self.subTest(kind=kind):
                self.assertEqual(
                    task_advance._review_ci_gate(NUMBER, HEAD, github, profile=bad),
                    ('REFUSED', task_advance.MERGE_CI_PROFILE_INVALID))
                repairs = []
                self.assertEqual(
                    _route(run, github, repairs, profile=bad),
                    ('REFUSED', [task_advance.MERGE_CI_PROFILE_INVALID], None))
                self.assertEqual(repairs, [])
                self.assertIsNone(task_advance._mac_capacity_cancel(
                    run, HEAD, github, NUMBER, profile=bad))
        github.assert_not_called()

    def test_advance_refuses_without_default_fallback(self):
        default_failure = _reply(tta.check_run(conclusion='failure'))
        cases = [(kind, {'profile': bad}, contextlib.nullcontext())
                 for kind, bad in _bad_profiles()]
        cases.append(('default_raises', {}, mock.patch.object(
            ci_profile, 'default_profile', side_effect=RuntimeError('profile unavailable'))))
        for kind, options, patcher in cases:
            with self.subTest(kind=kind):
                with patcher:
                    result, calls, dispatched, repaired = _advance(default_failure, **options)
                    self.assertEqual((result['step'], result['codes']),
                                     ('REFUSED', [task_advance.MERGE_CI_PROFILE_INVALID]))
                    self.assertNotIn(CHECKS_READ, calls)
                    self.assertEqual((dispatched, repaired), ([], []))

                    result, _, _, repaired = _advance(default_failure, merge=True, **options)
                    self.assertEqual((result['step'], result['codes']),
                                     ('MERGE_REFUSED', [task_advance.MERGE_CI_PROFILE_INVALID]))
                    self.assertEqual(repaired, [])

                    mac = _reply(tta.check_run(5, name=DEFAULTS.macos_check,
                                               conclusion='timed_out'))
                    result, _, _, repaired = _advance(
                        mac, merge=True, merge_once=mock.Mock(return_value=dict(NOT_GREEN)),
                        **options)
                    self.assertEqual(result['step'], 'MERGE_REFUSED')
                    self.assertEqual(repaired, [])


if __name__ == '__main__':
    unittest.main()
