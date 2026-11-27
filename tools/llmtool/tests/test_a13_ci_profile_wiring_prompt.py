"""#2793 A13.0 wiring (a): CI profile wiring for formal review prompt CI status lines."""

from __future__ import annotations

from argparse import Namespace
from contextlib import redirect_stdout
import importlib
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import ci_profile, task_completion, task_submission_cli  # noqa: E402
from llmtool.ci_profile import CiProfile, CiProfileError, default_profile  # noqa: E402

TASK_ID = 'a' * 32
RUN_ID = 'b' * 32
CONTRACT = 'c' * 64
HEAD = 'd' * 40
SOURCE = 'e' * 40
BASE = 'f' * 40
COMMON = '/repo/.git'

ORIGINAL_CHECKS = (
    'check (Linux 3.14)',
    'check (macOS 3.13)',
    'check (docs)',
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


class _FakeStore:
    def __init__(self):
        self.task = {
            'task_id': TASK_ID,
            'run_id': RUN_ID,
            'task_binding': {
                'task_id': TASK_ID,
                'run_id': RUN_ID,
                'contract_digest': CONTRACT,
            },
            'developer': {'provider_profile': 'claude'},
            'pm_assignment': {
                'schema_version': 1,
                'route': 'claude',
                'risk': 'LOW',
                'goal': 'Wire CI profile into review prompt.',
                'acceptance': ['Review prompt reports CI status from CiProfile.'],
            },
            'completion': {
                'executor_task_id': 'executor-d6',
                'evidence': {
                    'common_dir': COMMON,
                    'result': {'result_head': HEAD, 'source_sha': SOURCE},
                },
            },
        }

    def get(self, task_id):
        if task_id != TASK_ID:
            raise AssertionError('unexpected task id')
        return self.task


def _registration(task_id, trusted_agent_id):
    return SimpleNamespace(
        task_id=task_id,
        root='/repo',
        common_dir=COMMON,
        branch='task-branch',
        source_sha=SOURCE,
        result_sha=HEAD,
        trusted_agent_id=trusted_agent_id,
    )


def _snapshot():
    return SimpleNamespace(root='/repo', common_dir=COMMON, branch='task-branch', head=HEAD)


def _check_run(run_id, name, *, status='completed', conclusion='success'):
    return {
        'id': run_id,
        'name': name,
        'head_sha': HEAD,
        'status': status,
        'conclusion': conclusion,
    }


def _render_codex_prompt(*, checks=None, module=None):
    target = task_submission_cli if module is None else module
    store = _FakeStore()
    pr = {'number': 42, 'head': {'sha': HEAD}, 'base': {'sha': BASE}}
    reads = []

    def github_get(path):
        reads.append(path)
        if path == 'pulls/42':
            return pr
        if isinstance(checks, Exception):
            raise checks
        return {'total_count': 0, 'check_runs': []} if checks is None else checks

    registrations = {
        'executor-d6': _registration('executor-d6', 'claude-developer'),
        'reviewer-d6': _registration('reviewer-d6', 'codex-reviewer'),
    }
    args = Namespace(
        task_id=TASK_ID,
        executor_task='executor-d6',
        reviewer_task='reviewer-d6',
        pr=42,
        version=1,
    )
    output = io.StringIO()
    with mock.patch.object(
            task_completion, 'read_d6_registration',
            side_effect=lambda task_id, *_a, **_kw: registrations[task_id]), \
            mock.patch.object(
                task_completion, 'collect_git_snapshot',
                side_effect=[_snapshot(), _snapshot()]), \
            mock.patch.object(task_completion, '_github_get', side_effect=github_get), \
            redirect_stdout(output):
        code = target.run_task_review_prompt(args, store=store)
    return code, output.getvalue(), reads


class R1DefaultProfilePromptTests(unittest.TestCase):
    def test_default_profile_reports_default_check_names_in_order(self):
        checks = {
            'total_count': 4,
            'check_runs': [
                _check_run(1, 'check (Linux 3.14)', conclusion='success'),
                _check_run(2, 'check (macOS 3.13)', status='in_progress', conclusion=None),
                _check_run(3, 'check (docs)', conclusion='success'),
                _check_run(4, 'unrelated-check', conclusion='failure'),
            ],
        }
        code, output, reads = _render_codex_prompt(checks=checks)
        self.assertEqual(code, 0)
        self.assertEqual(reads, ['pulls/42', f'commits/{HEAD}/check-runs?per_page=100'])
        ci_lines = [line for line in output.splitlines() if line.startswith('CI ')]
        self.assertEqual(ci_lines, [
            'CI check (Linux 3.14): status=completed, conclusion=success',
            'CI check (macOS 3.13): status=in_progress, conclusion=UNKNOWN',
            'CI check (docs): status=completed, conclusion=success',
        ])

    def test_default_profile_output_matches_explicit_default_profile(self):
        checks = {
            'total_count': 3,
            'check_runs': [
                _check_run(1, 'check (Linux 3.14)', conclusion='success'),
                _check_run(2, 'check (macOS 3.13)', conclusion='failure'),
                _check_run(3, 'check (docs)', conclusion='success'),
            ],
        }
        code_unpatched, output_unpatched, _ = _render_codex_prompt(checks=checks)
        with mock.patch.object(ci_profile, 'default_profile', return_value=default_profile()):
            code_patched, output_patched, _ = _render_codex_prompt(checks=checks)
        self.assertEqual(code_unpatched, 0)
        self.assertEqual(code_patched, 0)
        self.assertEqual(output_unpatched, output_patched)


class R2SubstitutedProfilePromptTests(unittest.TestCase):
    def test_substituted_profile_reports_alt_names_and_no_original_names(self):
        checks = {
            'total_count': 6,
            'check_runs': [
                _check_run(1, 'check (Linux 3.14)', conclusion='failure'),
                _check_run(2, 'check (macOS 3.13)', conclusion='failure'),
                _check_run(3, 'check (docs)', conclusion='failure'),
                _check_run(4, ALT_PROFILE.linux_code_check, conclusion='success'),
                _check_run(5, ALT_PROFILE.macos_check, status='in_progress', conclusion=None),
                _check_run(6, ALT_PROFILE.docs_check, conclusion='failure'),
            ],
        }
        with mock.patch.object(ci_profile, 'default_profile', return_value=ALT_PROFILE):
            code, output, reads = _render_codex_prompt(checks=checks)
        self.assertEqual(code, 0)
        self.assertEqual(reads, ['pulls/42', f'commits/{HEAD}/check-runs?per_page=100'])
        ci_lines = [line for line in output.splitlines() if line.startswith('CI ')]
        self.assertEqual(ci_lines, [
            'CI alt-linux-check: status=completed, conclusion=success',
            'CI alt-macos-check: status=in_progress, conclusion=UNKNOWN',
            'CI alt-docs-check: status=completed, conclusion=failure',
        ])
        for original in ORIGINAL_CHECKS:
            self.assertNotIn(original, output)

    def test_substituted_profile_with_only_original_checks_reports_none_recorded(self):
        checks = {
            'total_count': 3,
            'check_runs': [
                _check_run(1, 'check (Linux 3.14)', conclusion='success'),
                _check_run(2, 'check (macOS 3.13)', conclusion='success'),
                _check_run(3, 'check (docs)', conclusion='success'),
            ],
        }
        with mock.patch.object(ci_profile, 'default_profile', return_value=ALT_PROFILE):
            code, output, _ = _render_codex_prompt(checks=checks)
        self.assertEqual(code, 0)
        lines = output.splitlines()
        self.assertIn('CI: no run of these checks is recorded for this HEAD yet.', lines)
        self.assertFalse(any(line.startswith('CI ') and line != 'CI: no run of these checks is recorded for this HEAD yet.'
                             for line in lines))
        for original in ORIGINAL_CHECKS:
            self.assertNotIn(original, output)


class R3InvalidOrMissingProfilePromptTests(unittest.TestCase):
    def _bad_profiles(self):
        mutated_empty = CiProfile(
            linux_code_check='a',
            docs_check='b',
            gateway_check='c',
            ai_loop_check='d',
            macos_check='e',
            linux_workflow_file='l.yml',
            macos_workflow_file='m.yml',
        )
        object.__setattr__(mutated_empty, 'linux_code_check', '')

        mutated_bad_char = CiProfile(
            linux_code_check='a',
            docs_check='b',
            gateway_check='c',
            ai_loop_check='d',
            macos_check='e',
            linux_workflow_file='l.yml',
            macos_workflow_file='m.yml',
        )
        object.__setattr__(mutated_bad_char, 'macos_check', 'bad\nname')

        ambiguous_checks = CiProfile(
            linux_code_check='same-check',
            docs_check='b',
            gateway_check='c',
            ai_loop_check='d',
            macos_check='same-check',
            linux_workflow_file='l.yml',
            macos_workflow_file='m.yml',
        )
        return (
            ('missing_none', None),
            ('invalid_str', 'check (Linux 3.14)'),
            ('invalid_dict', {'linux_code_check': 'check (Linux 3.14)'}),
            ('invalid_namespace', SimpleNamespace(linux_code_check='check (Linux 3.14)')),
            ('mutated_empty', mutated_empty),
            ('mutated_non_printable', mutated_bad_char),
            ('ambiguous_list', [ALT_PROFILE, default_profile()]),
            ('ambiguous_tuple', (ALT_PROFILE,)),
            ('ambiguous_duplicate_checks', ambiguous_checks),
        )

    def test_bad_profile_reports_ci_unavailable_without_original_names_or_check_read(self):
        unavailable = 'CI: unavailable; the check runs for this HEAD could not be read.'
        checks = {
            'total_count': 3,
            'check_runs': [
                _check_run(1, 'check (Linux 3.14)', conclusion='success'),
                _check_run(2, 'check (macOS 3.13)', conclusion='success'),
                _check_run(3, 'check (docs)', conclusion='success'),
            ],
        }
        for kind, bad in self._bad_profiles():
            with self.subTest(kind=kind):
                with mock.patch.object(ci_profile, 'default_profile', return_value=bad):
                    code, output, reads = _render_codex_prompt(checks=checks)
                self.assertEqual(code, 0)
                self.assertEqual(reads, ['pulls/42'])
                self.assertIn(unavailable, output.splitlines())
                for original in ORIGINAL_CHECKS:
                    self.assertNotIn(original, output)

    def test_failing_profile_resolver_reports_ci_unavailable(self):
        unavailable = 'CI: unavailable; the check runs for this HEAD could not be read.'
        checks = {
            'total_count': 1,
            'check_runs': [_check_run(1, 'check (Linux 3.14)', conclusion='success')],
        }
        for exc in (CiProfileError(), RuntimeError('missing profile')):
            with self.subTest(exc=type(exc).__name__):
                with mock.patch.object(ci_profile, 'default_profile', side_effect=exc):
                    code, output, reads = _render_codex_prompt(checks=checks)
                self.assertEqual(code, 0)
                self.assertEqual(reads, ['pulls/42'])
                self.assertIn(unavailable, output.splitlines())
                for original in ORIGINAL_CHECKS:
                    self.assertNotIn(original, output)

    def test_fresh_module_load_with_failing_profile_resolver(self):
        unavailable = 'CI: unavailable; the check runs for this HEAD could not be read.'
        checks = {
            'total_count': 1,
            'check_runs': [_check_run(1, 'check (Linux 3.14)', conclusion='success')],
        }
        for exc in (CiProfileError(), RuntimeError('missing profile')):
            with self.subTest(exc=type(exc).__name__):
                with mock.patch.object(ci_profile, 'default_profile', side_effect=exc):
                    reloaded = importlib.reload(task_submission_cli)
                    code, output, reads = _render_codex_prompt(checks=checks, module=reloaded)
                self.assertEqual(code, 0)
                self.assertEqual(reads, ['pulls/42'])
                self.assertIn(unavailable, output.splitlines())
                for original in ORIGINAL_CHECKS:
                    self.assertNotIn(original, output)


if __name__ == '__main__':
    unittest.main()
