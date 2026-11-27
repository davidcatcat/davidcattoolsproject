"""#2793 A13.0 wiring (b): CI repair check names come from the CI profile.

The default profile accepts and refuses exactly the names it did before. A
substituted profile is used by the real entry points, and a missing, invalid or
ambiguous profile refuses with one fixed code instead of falling back.

Test data (Owner #763 A): plain synthetic check names and a synthetic HEAD only.
No credential-shaped strings.
"""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import ci_profile, review_repair  # noqa: E402
from llmtool.ci_profile import CiProfile  # noqa: E402
from llmtool.errors import LLMToolError  # noqa: E402

import review_trust_fixture  # noqa: E402
import worker_policy_fixture  # noqa: E402
from test_review_repair import (  # noqa: E402
    EXECUTOR, HEAD, PR, TASK, TempTaskStore, _record,
)

DEFAULT_CHECKS = (
    'check (Linux 3.14)',
    'check (docs)',
    'gateway tests (Linux 3.14)',
    'ai-loop helper tests (Python 3.14)',
    'check (macOS 3.13)',
)

ALT_PROFILE = CiProfile(
    linux_code_check='alt-linux-check',
    docs_check='alt-docs-check',
    gateway_check='alt-gateway-check',
    ai_loop_check='alt-ai-loop-check',
    macos_check='alt-macos-check',
    linux_workflow_file='alt-linux-ci.yml',
    macos_workflow_file='alt-mac-ci.yml',
)
ALT_CHECKS = (
    ALT_PROFILE.linux_code_check,
    ALT_PROFILE.docs_check,
    ALT_PROFILE.gateway_check,
    ALT_PROFILE.ai_loop_check,
    ALT_PROFILE.macos_check,
)

_NO_PROFILE = object()


def _profile(**overrides):
    values = {
        'linux_code_check': 'linux-check',
        'docs_check': 'docs-check',
        'gateway_check': 'gateway-check',
        'ai_loop_check': 'ai-loop-check',
        'macos_check': 'macos-check',
        'linux_workflow_file': 'linux-ci.yml',
        'macos_workflow_file': 'mac-ci.yml',
    }
    values.update(overrides)
    return CiProfile(**values)


def _ci(*, check_name=DEFAULT_CHECKS[0], check_run_id=7):
    return {'head': HEAD, 'check_name': check_name, 'check_run_id': check_run_id,
            'conclusion': 'failure', 'excerpt': 'FAIL: test_example\nAssertionError'}


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


class CiProfileWiringRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TempTaskStore(self.temp.name, _record())
        self.calls = []
        dispatch = review_repair.worker_dispatch
        worker_policy_fixture.install(self)
        for patcher in (
                patch.object(dispatch, 'evaluate',
                             return_value=type('Decision', (), {'allowed': True})()),
                patch.object(review_repair.shutil, 'which', return_value='/usr/bin/agy')):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _reset(self):
        self.store = TempTaskStore(self.temp.name, _record())
        self.calls = []

    def _dispatch(self, *, check_name, ci_profile_value=_NO_PROFILE, run_id=7):
        def adapter(**request):
            self.calls.append(request)
            return {'status': 'DISPATCHED', 'head': HEAD}

        def recorder(**request):
            return {'task_id': request['repair_task_id'],
                    'pm_assignment': {'acceptance': ['Repair the failed check.']}}

        def no_github(_path):
            raise AssertionError('a CI repair must not read review comments')

        kwargs = dict(
            store=self.store, task_id=TASK, executor_task=EXECUTOR, pr_number=PR,
            github_get=no_github, dispatch_adapter=adapter,
            current_head=lambda _task, _executor: HEAD,
            repair_task_recorder=recorder,
            ci_failure=_ci(check_name=check_name, check_run_id=run_id),
        )
        if ci_profile_value is not _NO_PROFILE:
            kwargs['profile'] = ci_profile_value
        return review_repair.repair_review(**kwargs)

    def _claims(self):
        return self.store.get(TASK).get('review_repair_claims')

    def _assert_refused(self, code, **kwargs):
        before = self._claims()
        calls = len(self.calls)
        with self.assertRaises(LLMToolError) as caught:
            self._dispatch(**kwargs)
        self.assertEqual(caught.exception.review_repair_code, code)
        self.assertEqual(self._claims(), before)
        self.assertEqual(len(self.calls), calls)

    def _bad_profiles(self):
        mutated = _profile()
        object.__setattr__(mutated, 'macos_check', '')
        bad_path = _profile(macos_workflow_file='dir/mac-ci.yml')
        shard_collisions = tuple(
            (f'shard_collision_{idx}', _profile(macos_check=shard))
            for idx, shard in enumerate(
                ('shards (1/3)', 'shards (2/3)', 'shards (3/3)'), 1)
        )
        return (
            ('missing_none', None),
            ('invalid_text', 'linux-ci.yml'),
            ('invalid_dict', {'linux_code_check': 'linux-check'}),
            ('invalid_namespace', types.SimpleNamespace(linux_code_check='linux-check')),
            ('mutated_empty', mutated),
            ('path_separator', bad_path),
            ('ambiguous_list', [ALT_PROFILE, ci_profile.default_profile()]),
            ('ambiguous_tuple', (ALT_PROFILE,)),
        ) + shard_collisions

    def test_default_profile_accepts_exactly_the_original_names(self):
        self.assertEqual(review_repair.CI_REPAIR_CHECKS, DEFAULT_CHECKS)
        defaults = ci_profile.default_profile()
        names = (
            defaults.linux_code_check, defaults.docs_check, defaults.gateway_check,
            defaults.ai_loop_check, defaults.macos_check,
        )
        self.assertEqual(review_repair._ci_repair_checks(), names)

        for idx, name in enumerate(DEFAULT_CHECKS):
            with self.subTest(check=name):
                self._reset()
                result = self._dispatch(check_name=name, run_id=7 + idx)
                self.assertEqual(result['claim']['state'], 'DISPATCHED')
                self.assertEqual(len(self.calls), 1)
                text = self.calls[0]['packet']['reviewer_text_excerpt_untrusted_data']
                self.assertTrue(text.startswith(
                    f'{review_repair.CI_PACKET_HEADER} check={name} '
                    f'run={7 + idx} conclusion=failure head={HEAD}\n'))

    def test_default_profile_refuses_names_it_does_not_carry(self):
        for idx, name in enumerate(ALT_CHECKS + ('check (unknown)',)):
            with self.subTest(check=name):
                self._reset()
                self._assert_refused('CI_FAILURE_INVALID',
                                     check_name=name, run_id=20 + idx)

    def test_substituted_profile_accepts_its_names_and_refuses_the_originals(self):
        for idx, name in enumerate(ALT_CHECKS):
            with self.subTest(check=name):
                self._reset()
                result = self._dispatch(
                    check_name=name, ci_profile_value=ALT_PROFILE, run_id=30 + idx)
                self.assertEqual(result['claim']['state'], 'DISPATCHED')
                self.assertEqual(len(self.calls), 1)
                text = self.calls[0]['packet']['reviewer_text_excerpt_untrusted_data']
                self.assertTrue(text.startswith(
                    f'{review_repair.CI_PACKET_HEADER} check={name} '
                    f'run={30 + idx} conclusion=failure head={HEAD}\n'))

        for idx, name in enumerate(DEFAULT_CHECKS):
            with self.subTest(check=name):
                self._reset()
                self._assert_refused('CI_FAILURE_INVALID',
                                     check_name=name, ci_profile_value=ALT_PROFILE,
                                     run_id=40 + idx)

    def test_substituted_default_profile_is_used_by_the_real_entry_point(self):
        with patch.object(ci_profile, 'default_profile', return_value=ALT_PROFILE):
            for idx, name in enumerate(ALT_CHECKS):
                with self.subTest(check=name):
                    self._reset()
                    result = self._dispatch(check_name=name, run_id=50 + idx)
                    self.assertEqual(result['claim']['state'], 'DISPATCHED')
                    self.assertEqual(len(self.calls), 1)

            for idx, name in enumerate(DEFAULT_CHECKS):
                with self.subTest(check=name):
                    self._reset()
                    self._assert_refused('CI_FAILURE_INVALID',
                                         check_name=name, run_id=60 + idx)

    def test_ci_failure_helper_uses_the_substituted_profile(self):
        for name in ALT_CHECKS:
            with self.subTest(check=name):
                packet, identity = review_repair._ci_failure(
                    {'task_id': TASK}, executor_task=EXECUTOR, pr_number=PR,
                    ci_failure=_ci(check_name=name), profile=ALT_PROFILE)
                self.assertIn(f'check={name} ', packet[
                    'reviewer_text_excerpt_untrusted_data'])
                self.assertEqual(len(identity), 64)

        for name in DEFAULT_CHECKS:
            with self.subTest(check=name):
                with self.assertRaises(LLMToolError) as caught:
                    review_repair._ci_failure(
                        {'task_id': TASK}, executor_task=EXECUTOR, pr_number=PR,
                        ci_failure=_ci(check_name=name), profile=ALT_PROFILE)
                self.assertEqual(caught.exception.review_repair_code,
                                 'CI_FAILURE_INVALID')

    def test_explicit_bad_profile_refuses_with_fixed_code(self):
        for label, bad in self._bad_profiles():
            with self.subTest(kind=label):
                self._reset()
                self._assert_refused(review_repair.CI_REPAIR_PROFILE_INVALID,
                                     check_name=DEFAULT_CHECKS[0],
                                     ci_profile_value=bad)

    def test_bad_default_profile_refuses_with_fixed_code(self):
        for label, bad in self._bad_profiles():
            with self.subTest(kind=label):
                self._reset()
                with patch.object(ci_profile, 'default_profile', return_value=bad):
                    self._assert_refused(review_repair.CI_REPAIR_PROFILE_INVALID,
                                         check_name=DEFAULT_CHECKS[0])

        for exc in (ci_profile.CiProfileError(), RuntimeError('missing profile')):
            with self.subTest(exc=type(exc).__name__):
                self._reset()
                with patch.object(ci_profile, 'default_profile', side_effect=exc):
                    self._assert_refused(review_repair.CI_REPAIR_PROFILE_INVALID,
                                         check_name=DEFAULT_CHECKS[0])


if __name__ == '__main__':
    unittest.main()
