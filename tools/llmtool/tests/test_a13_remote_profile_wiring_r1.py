"""#2793: Remote test validation wiring to TestProfile (regression table)."""

import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import remote_test, remote_test_part1, task_advance, test_profile
from llmtool.test_profile import TestProfile, TestProfileError, default_profile

HEAD = 'a' * 40
TASK_ID = '1' * 32
RUN_ID = '2' * 32
PM_SESSION_ID = 'pm-' + '3' * 32
NATIVE = 'test_review_authority_native'


def _task(*, read_paths=(), write_paths=(), completion_tests=None, callback_tests=None):
    developer = {
        'read_paths': list(read_paths),
        'write_paths': list(write_paths),
    }
    if callback_tests is not None:
        developer['direct_result_callback'] = {
            'state': 'PM_EVIDENCE_READY',
            'evidence': {
                'common_dir': '/repo/.git',
                'result': {'result_head': HEAD},
                'tests': [{'path': path} for path in callback_tests],
            },
        }
    completion = {
        'executor_task_id': 'executor-1',
        'state': 'WAITING_REVIEW',
        'evidence': {
            'common_dir': '/repo/.git',
            'result': {'result_head': HEAD},
        },
    }
    if completion_tests is not None:
        completion['evidence']['tests'] = [{'path': path} for path in completion_tests]
    return {
        'task_id': TASK_ID,
        'run_id': RUN_ID,
        'pm_session_id': PM_SESSION_ID,
        'backend_session_semantics': 'ACTIVE_PM_RUNTIME_NO_PROVIDER_CALL',
        'pm_provenance': {'source': 'ACTIVE_PM_RUNTIME', 'provider_invoked': False},
        'developer': developer,
        'completion': completion,
        'remote_test_receipts': [],
        'completion_events': [],
        'completion_event_tombstones': [],
        'remote_test_outcomes': [],
        'published_pr': None,
    }


class _Store:
    def __init__(self, task):
        self.task = task

    def get(self, task_id):
        if task_id != self.task['task_id']:
            raise KeyError(task_id)
        return self.task

    def append_remote_test_receipt(self, task_id, receipt):
        self.task['remote_test_receipts'].append(receipt)
        return receipt

    def record_published_pr(self, task_id, pr, head_sha):
        self.task['published_pr'] = {'pr': pr, 'head_sha': head_sha}
        return self.task['published_pr']

    def append_remote_test_outcome(self, task_id, outcome):
        counter = len(self.task['remote_test_outcomes'])
        self.task['remote_test_outcomes'].append(outcome)
        return {'outcome': outcome, 'counter': counter, 'created': True}

    def record_completion_event(self, task_id, **event):
        saved = dict(event, task_id=task_id, state='PENDING')
        self.task['completion_events'].append(saved)
        return {'event': saved, 'created': True}


def _pass_receipt_bytes(modules):
    payload = {
        'result': 'PASS', 'exit_code': 0, 'source_sha': HEAD, 'tree': 'c' * 40,
        'fetch_head': HEAD, 'modules': list(modules), 'identity': {'uid': 65534},
        'started_at': '2026-11-29T00:00:00Z', 'ended_at': '2026-11-29T00:00:01Z',
        'elapsed_s': 1.0, 'ran_total': len(modules),
        'final': ['OK'] * len(modules), 'failures': [],
        'log_bytes': 0, 'log_sha256': 'd' * 64, 'remote_log_path': None,
        'module_results': [{'module': name, 'ran': 1, 'skipped': 0, 'final': 'OK'}
                           for name in modules],
    }
    return json.dumps(payload, separators=(',', ':')).encode('utf-8')


class RemoteProfileValidationRegressionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.config = Path(temp.name) / 'remote-test.json'
        self.config.write_text(json.dumps({
            'host': 'ec2.example.org', 'user': 'runner',
            'repo_path': '/home/runner/project', 'python': '/usr/bin/python3',
        }), encoding='utf-8')
        self.config.chmod(0o600)

    def _run_remote(self, task, modules=()):
        calls = []

        def transport(argv, script, timeout):
            calls.append((argv, script, timeout))
            return SimpleNamespace(stdout=_pass_receipt_bytes(modules), stderr=b'', returncode=0)

        store = _Store(task)
        with patch.object(remote_test, 'read_d6_registration',
                          return_value=SimpleNamespace(result_sha=HEAD)):
            result = remote_test.run_remote_test(
                TASK_ID, executor_task='d6-task', sha=HEAD,
                ref='refs/pull/425/head', modules=modules, store=store,
                config_path=self.config, transport=transport)
        return result, calls

    def test_default_declared_plus_pr_changed_union(self):
        task = _task(
            read_paths=['tools/llmtool/tests/test_alpha.py'],
            write_paths=['tools/llmtool/tests/test_beta.py'],
        )
        self.assertEqual(remote_test_part1._task_path_modules(task), ('test_alpha', 'test_beta'))
        self.assertEqual(remote_test_part1._admitted_modules(task), ('test_alpha', 'test_beta'))

        def github_get(path):
            if path in ('pulls/17/files?per_page=100', 'pulls/17/files?per_page=100&page=1'):
                return [{'filename': 'tools/llmtool/tests/test_gamma.py', 'status': 'modified'}]
            return []

        changed, code = task_advance._pr_changed_test_modules(
            17, HEAD, github_get, {'head': {'sha': HEAD}, 'changed_files': 1})
        self.assertIsNone(code)
        self.assertEqual(changed, ('test_gamma',))
        self.assertEqual(
            remote_test_part1.remote_required_modules(task, changed),
            ('test_alpha', 'test_beta', 'test_gamma'),
        )

    def test_removed_file_ignored_by_pr_changed_while_declared_remains(self):
        task = _task(read_paths=['tools/llmtool/tests/test_declared.py'])

        def github_get(path):
            if path in ('pulls/17/files?per_page=100', 'pulls/17/files?per_page=100&page=1'):
                return [{'filename': 'tools/llmtool/tests/test_removed.py', 'status': 'removed'}]
            return []

        changed, code = task_advance._pr_changed_test_modules(
            17, HEAD, github_get, {'head': {'sha': HEAD}, 'changed_files': 1})
        self.assertIsNone(code)
        self.assertEqual(changed, ())
        self.assertEqual(remote_test_part1._task_path_modules(task), ('test_declared',))
        self.assertEqual(remote_test_part1.remote_required_modules(task, changed), ('test_declared',))

    def test_darwin_only_exclusion(self):
        task = _task(read_paths=[
            f'tools/llmtool/tests/{NATIVE}.py',
            'tools/llmtool/tests/test_remote_test.py',
        ])
        self.assertEqual(remote_test_part1._task_path_modules(task), ('test_remote_test', NATIVE))
        self.assertEqual(remote_test_part1.remote_required_modules(task), ('test_remote_test',))

    def test_invalid_path_not_admitted_and_never_passes(self):
        task = _task(
            read_paths=['docs/readme.md', 'scripts/test_helper.py'],
            completion_tests=['scripts/test_helper.py', 'tools/llmtool/tests/test_Upper.py'],
        )
        self.assertIsNone(default_profile().kind_for('docs/readme.md'))
        self.assertEqual(remote_test_part1._task_path_modules(task), ())
        self.assertEqual(remote_test_part1._admitted_modules(task), ())
        result, calls = self._run_remote(task, modules=('test_helper',))
        self.assertEqual((result['result'], result['code']), ('ADMISSION_DENIED', 'MODULE_NOT_ADMITTED'))
        self.assertEqual(calls, [])

    def test_invalid_missing_or_raising_profile_admits_nothing_and_never_passes(self):
        for bad_entries in ((), [('only_one',)], [('not_regex', 'unittest')],
                            [(re.compile(r'tools/llmtool/tests/test_a\.py\Z'), 'pytest')]):
            with self.subTest(bad_entries=bad_entries):
                with self.assertRaises(TestProfileError) as ctx:
                    TestProfile(bad_entries)
                self.assertEqual(ctx.exception.CODE, 'TEST_PROFILE_INVALID')
                self.assertEqual(str(ctx.exception), 'TEST_PROFILE_INVALID')

        task = _task(
            read_paths=['tools/llmtool/tests/test_remote_test.py'],
            callback_tests=['tools/llmtool/tests/test_remote_test.py'],
        )
        for patch_kwargs in (
            {'return_value': None},
            {'return_value': 'not-a-profile'},
            {'side_effect': TestProfileError()},
            {'side_effect': RuntimeError('profile load failed')},
        ):
            with self.subTest(patch_kwargs=patch_kwargs):
                with patch.object(test_profile, 'default_profile', **patch_kwargs):
                    self.assertEqual(remote_test_part1._task_path_modules(task), ())
                    self.assertEqual(remote_test_part1._admitted_modules(task), ())
                    self.assertEqual(remote_test_part1.remote_required_modules(task), ())
                    result, calls = self._run_remote(task, modules=('test_remote_test',))
                self.assertEqual((result['result'], result['code']),
                                 ('ADMISSION_DENIED', 'MODULE_NOT_ADMITTED'))
                self.assertEqual(calls, [])

    def test_non_default_profile_moves_derived_modules_and_excludes_scripts(self):
        custom = TestProfile((
            (re.compile(r'alt/tests/test_[A-Za-z0-9_]+\.py\Z'), 'unittest'),
            (re.compile(r'scripts/test_[A-Za-z0-9_]+\.py\Z'), 'script'),
        ))
        self.assertNotEqual(custom, default_profile())
        with self.assertRaises(AttributeError):
            custom.extra = True

        task_paths = _task(
            read_paths=['alt/tests/test_custom_a.py', 'tools/llmtool/tests/test_old_default.py'],
            write_paths=['scripts/test_custom_script.py'],
        )
        task_evidence = _task(
            completion_tests=['alt/tests/test_from_completion.py', 'scripts/test_script_only.py'],
            callback_tests=['alt/tests/test_from_callback.py', 'tools/llmtool/tests/test_old.py'],
        )
        with patch.object(test_profile, 'default_profile', return_value=custom):
            self.assertEqual(remote_test_part1._task_path_modules(task_paths), ('test_custom_a',))
            self.assertEqual(remote_test_part1._admitted_modules(task_paths), ('test_custom_a',))
            self.assertEqual(remote_test_part1._admitted_modules(task_evidence), ('test_from_callback',))
            task_evidence['developer'].pop('direct_result_callback')
            self.assertEqual(remote_test_part1._admitted_modules(task_evidence), ('test_from_completion',))

    def test_lowercase_stem_and_runner_constraints_preserved(self):
        long_stem = 'test_' + 'a' * 81
        task = _task(
            read_paths=[
                'tools/llmtool/tests/test_UpperCase.py',
                f'tools/llmtool/tests/{long_stem}.py',
                'tools/llmtool/tests/test_valid_lower_1.py',
                'scripts/test_script_runner.py',
            ],
            completion_tests=[
                'tools/llmtool/tests/test_UpperCase.py',
                'tools/llmtool/tests/test_valid_lower_2.py',
            ],
        )
        self.assertEqual(remote_test_part1._task_path_modules(task), ('test_valid_lower_1',))
        task['developer']['read_paths'] = []
        self.assertEqual(remote_test_part1._admitted_modules(task), ('test_valid_lower_2',))

    def test_skipped_only_run_never_becomes_pass(self):
        receipt = {
            'result': 'PASS', 'exit_code': 0, 'source_sha': HEAD, 'tree': 'c' * 40,
            'fetch_head': HEAD, 'modules': ['test_remote_test'], 'identity': {'uid': 65534},
            'started_at': '2026-11-29T00:00:00Z', 'ended_at': '2026-11-29T00:00:01Z',
            'elapsed_s': 1.0, 'ran_total': 1, 'final': ['OK (skipped=1)'],
            'failures': [], 'log_bytes': 0, 'log_sha256': 'd' * 64,
            'remote_log_path': None,
            'module_results': [{'module': 'test_remote_test', 'ran': 1, 'skipped': 1,
                                'final': 'OK (skipped=1)'}],
        }
        self.assertFalse(remote_test.pass_receipt_covers(receipt, ('test_remote_test',)))


if __name__ == '__main__':
    unittest.main()
