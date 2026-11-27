"""#2793 A13.0 wiring (a): CI profile wiring for task_advance merge gate and merge_train_eligibility."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import ci_profile  # noqa: E402
from llmtool import mac_full_due as due  # noqa: E402
from llmtool import merge_train_eligibility as eligibility  # noqa: E402
from llmtool import sensitive_paths, task_advance, task_store  # noqa: E402
from llmtool.ci_profile import CiProfile, CiProfileError, default_profile  # noqa: E402
from llmtool.errors import LLMToolError  # noqa: E402

TASK_ID = '0123456789abcdef0123456789abcdef'
EXECUTOR = 'train-member-one'
PR = 41
HEAD = '1' * 40
MAIN_SHA = '2' * 40
RULES = {
    'schema_version': 1,
    'exact': ['tools/llmtool/worker-dispatch.json'],
    'prefixes': ['tools/llmtool/rules/'],
    'globs': [],
}

ALT_PROFILE = CiProfile(
    linux_code_check='alt-linux-check',
    docs_check='alt-docs-check',
    gateway_check='alt-gateway-check',
    ai_loop_check='alt-ai-loop-check',
    macos_check='alt-macos-check',
    linux_workflow_file='alt-linux-ci.yml',
    macos_workflow_file='alt-mac-ci.yml',
)


def _run(run_id, name, conclusion='success', head=HEAD, status='completed'):
    return {
        'id': run_id,
        'name': name,
        'head_sha': head,
        'status': status,
        'conclusion': conclusion,
    }


def _checks(*runs):
    return {'total_count': len(runs), 'check_runs': list(runs)}


def _receipt(head=HEAD):
    body = {
        'source_sha': head,
        'result': 'PASS',
        'task_id': TASK_ID,
        'run_id': 'run-1',
        'executor_task': EXECUTOR,
        'modules': ['test_a13_ci_profile_wiring_gate'],
        'ran_total': 1,
        'final': ['OK'],
        'failures': [],
        'elapsed_s': 0.5,
    }
    body['receipt_digest'] = hashlib.sha256(task_store._json_bytes(body)).hexdigest()
    return body


def _record():
    return {
        'task_id': TASK_ID,
        'run_id': 'run-1',
        'record_sha256': '3' * 64,
        'completion': {'state': 'PM_EVIDENCE_READY', 'executor_task_id': EXECUTOR},
        'review_dispatch_claim': [
            {
                'pr': PR,
                'head_sha': HEAD,
                'state': 'COMPLETED',
                'version': 2,
                'review_result': {'verdict': 'PASS'},
            },
        ],
        'completion_events': [
            {
                'event_id': 'e' * 32,
                'source': 'REVIEWER',
                'source_sha': HEAD,
                'version': 2,
                'outcome': 'REVIEW_PASS',
                'state': 'PENDING',
            },
        ],
        'remote_test_receipts': [_receipt(HEAD)],
        'merge_attempt': None,
    }


def _pull(mergeable=True, labels=()):
    return {
        'number': PR,
        'state': 'open',
        'merged': False,
        'mergeable': mergeable,
        'head': {'sha': HEAD},
        'labels': list(labels),
    }


class _MemoryStore:
    def __init__(self, record):
        self.record = copy.deepcopy(record)
        self.updates = []

    def get(self, task_id):
        if task_id != self.record['task_id']:
            raise LLMToolError('TASK_NOT_FOUND')
        return copy.deepcopy(self.record)

    def compare_and_update(self, task_id, expected=None, **kwargs):
        self.updates.append((task_id, expected, kwargs))
        for key, value in kwargs.items():
            self.record[key] = copy.deepcopy(value)
        return copy.deepcopy(self.record)


class _FakeGitHub:
    def __init__(self, *, files, check_runs, pull=None):
        self.files = list(files)
        self.check_runs = list(check_runs)
        self.pull = _pull() if pull is None else copy.deepcopy(pull)
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        if path == f'pulls/{PR}':
            return copy.deepcopy(self.pull)
        if path == f'pulls/{PR}/files?per_page=100':
            return copy.deepcopy(self.files)
        if path == f'commits/{HEAD}/check-runs?per_page=100':
            return _checks(*self.check_runs)
        if path == f'issues/{PR}/comments?per_page=100':
            return []
        if path == 'branches/main':
            return {'commit': {'sha': MAIN_SHA}}
        if path == f'compare/{MAIN_SHA}...{HEAD}':
            return {'status': 'identical'}
        raise LookupError(f'unexpected path: {path}')


def _fake_gh(root, args):
    return types.SimpleNamespace(returncode=0, stdout=b'', stderr=b'')


class R1DefaultProfileTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(sensitive_paths, '_read_list', return_value=copy.deepcopy(RULES))
        patcher.start()
        self.addCleanup(patcher.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.train_root = Path(tmp.name) / 'tasks'

    def test_merge_checks_and_merge_once_use_default_profile(self):
        defaults = default_profile()
        files = [{'filename': 'tools/llmtool/llmtool/a.py'}]
        runs = [_run(10, defaults.linux_code_check), _run(20, defaults.macos_check)]
        github = _FakeGitHub(files=files, check_runs=runs)
        self.assertIsNone(task_advance._merge_checks(_record(), PR, HEAD, _pull(), github))

        store = _MemoryStore(_record())
        outcome = task_advance._merge_once(
            store=store,
            record=_record(),
            number=PR,
            head=HEAD,
            pr=_pull(),
            events=_record()['completion_events'],
            github_get=github,
            gh_runner=_fake_gh,
            root='/fake/root',
            sleep_fn=lambda _: None,
        )
        self.assertEqual(outcome['step'], 'MERGED')

    def test_member_check_uses_default_profile(self):
        defaults = default_profile()
        files = [{'filename': 'tools/llmtool/llmtool/a.py'}]
        runs = [_run(101, defaults.linux_code_check), _run(202, defaults.macos_check)]
        github = _FakeGitHub(files=files, check_runs=runs)
        result = eligibility.member_check(
            _MemoryStore(_record()), TASK_ID, PR, github,
            train_root=self.train_root, sleep=lambda _: None,
        )
        self.assertTrue(result['eligible'], result)
        self.assertEqual(result['member']['linux_check_run_id'], 101)
        self.assertEqual(result['member']['macos_check_run_id'], 202)


class R2SubstitutedProfileTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(sensitive_paths, '_read_list', return_value=copy.deepcopy(RULES))
        patcher.start()
        self.addCleanup(patcher.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.train_root = Path(tmp.name) / 'tasks'

    def test_merge_checks_uses_substituted_profile_and_rejects_original_names(self):
        defaults = default_profile()
        code_files = [{'filename': 'tools/llmtool/llmtool/a.py'}]
        docs_files = [{'filename': 'docs/guide.md'}]
        for via_patch in (False, True):
            with self.subTest(via_patch=via_patch):
                alt_code_gh = _FakeGitHub(
                    files=code_files,
                    check_runs=[_run(1, ALT_PROFILE.linux_code_check), _run(2, ALT_PROFILE.macos_check)],
                )
                orig_code_gh = _FakeGitHub(
                    files=code_files,
                    check_runs=[_run(1, defaults.linux_code_check), _run(2, defaults.macos_check)],
                )
                alt_docs_gh = _FakeGitHub(
                    files=docs_files,
                    check_runs=[_run(3, ALT_PROFILE.docs_check)],
                )
                orig_docs_gh = _FakeGitHub(
                    files=docs_files,
                    check_runs=[_run(3, defaults.docs_check)],
                )
                if via_patch:
                    with mock.patch.object(ci_profile, 'default_profile', return_value=ALT_PROFILE):
                        self.assertIsNone(task_advance._merge_checks(_record(), PR, HEAD, _pull(), alt_code_gh))
                        self.assertEqual(
                            task_advance._merge_checks(_record(), PR, HEAD, _pull(), orig_code_gh),
                            'MERGE_REQUIRED_CHECK_MISSING',
                        )
                        self.assertIsNone(task_advance._merge_checks(_record(), PR, HEAD, _pull(), alt_docs_gh))
                        self.assertEqual(
                            task_advance._merge_checks(_record(), PR, HEAD, _pull(), orig_docs_gh),
                            'MERGE_REQUIRED_CHECK_MISSING',
                        )
                else:
                    self.assertIsNone(task_advance._merge_checks(
                        _record(), PR, HEAD, _pull(), alt_code_gh, profile=ALT_PROFILE))
                    self.assertEqual(
                        task_advance._merge_checks(
                            _record(), PR, HEAD, _pull(), orig_code_gh, profile=ALT_PROFILE),
                        'MERGE_REQUIRED_CHECK_MISSING',
                    )
                    self.assertIsNone(task_advance._merge_checks(
                        _record(), PR, HEAD, _pull(), alt_docs_gh, profile=ALT_PROFILE))
                    self.assertEqual(
                        task_advance._merge_checks(
                            _record(), PR, HEAD, _pull(), orig_docs_gh, profile=ALT_PROFILE),
                        'MERGE_REQUIRED_CHECK_MISSING',
                    )

    def test_merge_once_and_advance_use_substituted_profile(self):
        defaults = default_profile()
        files = [{'filename': 'tools/llmtool/llmtool/a.py'}]
        alt_runs = [_run(11, ALT_PROFILE.linux_code_check), _run(22, ALT_PROFILE.macos_check)]
        orig_runs = [_run(11, defaults.linux_code_check), _run(22, defaults.macos_check)]

        for via_patch in (False, True):
            with self.subTest(via_patch=via_patch):
                alt_gh = _FakeGitHub(files=files, check_runs=alt_runs)
                orig_gh = _FakeGitHub(files=files, check_runs=orig_runs)
                rec = _record()
                d6 = {
                    'state': 'ACTIVE',
                    'root': '/fake/root',
                    'task_id': EXECUTOR,
                    'result_head': HEAD,
                    'recorded_result_head': HEAD,
                    'dirty': False,
                }
                if via_patch:
                    with mock.patch.object(ci_profile, 'default_profile', return_value=ALT_PROFILE):
                        with mock.patch.object(task_advance, '_d6_snapshot', return_value=d6):
                            with mock.patch.object(task_advance, '_pr_number', return_value=PR):
                                out = task_advance.advance(
                                    task_id=TASK_ID,
                                    store=_MemoryStore(rec),
                                    merge=True,
                                    github_get=alt_gh,
                                    merge_gh_runner=_fake_gh,
                                    sleep_fn=lambda _: None,
                                )
                                refused = task_advance.advance(
                                    task_id=TASK_ID,
                                    store=_MemoryStore(rec),
                                    merge=True,
                                    github_get=orig_gh,
                                    merge_gh_runner=_fake_gh,
                                    sleep_fn=lambda _: None,
                                )
                else:
                    with mock.patch.object(task_advance, '_d6_snapshot', return_value=d6):
                        with mock.patch.object(task_advance, '_pr_number', return_value=PR):
                            out = task_advance.advance(
                                task_id=TASK_ID,
                                store=_MemoryStore(rec),
                                merge=True,
                                github_get=alt_gh,
                                merge_gh_runner=_fake_gh,
                                sleep_fn=lambda _: None,
                                profile=ALT_PROFILE,
                            )
                            refused = task_advance.advance(
                                task_id=TASK_ID,
                                store=_MemoryStore(rec),
                                merge=True,
                                github_get=orig_gh,
                                merge_gh_runner=_fake_gh,
                                sleep_fn=lambda _: None,
                                profile=ALT_PROFILE,
                            )
                self.assertEqual(out['step'], 'MERGED', out)
                self.assertEqual(refused['step'], 'MERGE_REFUSED')
                self.assertEqual(refused['codes'], ['MERGE_REQUIRED_CHECK_MISSING'])

    def test_merge_once_pending_macos_check_uses_substituted_profile(self):
        files = [{'filename': 'tools/llmtool/llmtool/a.py'}]
        runs = [
            _run(11, ALT_PROFILE.linux_code_check),
            _run(22, ALT_PROFILE.macos_check, conclusion=None, status='in_progress'),
        ]
        github = _FakeGitHub(files=files, check_runs=runs)
        outcome = task_advance._merge_once(
            store=_MemoryStore(_record()),
            record=_record(),
            number=PR,
            head=HEAD,
            pr=_pull(),
            events=_record()['completion_events'],
            github_get=github,
            gh_runner=_fake_gh,
            root='/fake/root',
            sleep_fn=lambda _: None,
            profile=ALT_PROFILE,
        )
        self.assertEqual(outcome, {
            'step': 'MERGE_REFUSED',
            'codes': ['MERGE_CHECKS_NOT_GREEN'],
            'result': {'macos_check_pending': True},
        })

    def test_member_check_uses_substituted_profile_and_rejects_original_names(self):
        defaults = default_profile()
        files = [{'filename': 'tools/llmtool/llmtool/a.py'}]
        alt_runs = [_run(301, ALT_PROFILE.linux_code_check), _run(402, ALT_PROFILE.macos_check)]
        orig_runs = [_run(301, defaults.linux_code_check), _run(402, defaults.macos_check)]
        for via_patch in (False, True):
            with self.subTest(via_patch=via_patch):
                alt_gh = _FakeGitHub(files=files, check_runs=alt_runs)
                orig_gh = _FakeGitHub(files=files, check_runs=orig_runs)
                if via_patch:
                    with mock.patch.object(ci_profile, 'default_profile', return_value=ALT_PROFILE):
                        res = eligibility.member_check(
                            _MemoryStore(_record()), TASK_ID, PR, alt_gh,
                            train_root=self.train_root, sleep=lambda _: None,
                        )
                        bad = eligibility.member_check(
                            _MemoryStore(_record()), TASK_ID, PR, orig_gh,
                            train_root=self.train_root, sleep=lambda _: None,
                        )
                else:
                    res = eligibility.member_check(
                        _MemoryStore(_record()), TASK_ID, PR, alt_gh,
                        train_root=self.train_root, sleep=lambda _: None,
                        profile=ALT_PROFILE,
                    )
                    bad = eligibility.member_check(
                        _MemoryStore(_record()), TASK_ID, PR, orig_gh,
                        train_root=self.train_root, sleep=lambda _: None,
                        profile=ALT_PROFILE,
                    )
                self.assertTrue(res['eligible'], res)
                self.assertEqual(res['member']['linux_check_run_id'], 301)
                self.assertEqual(res['member']['macos_check_run_id'], 402)
                self.assertEqual(bad, {
                    'eligible': False,
                    'code': 'MERGE_REQUIRED_CHECK_MISSING',
                    'member': None,
                })


class R3InvalidOrAmbiguousProfileTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.train_root = Path(tmp.name) / 'tasks'

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
            ('invalid_namespace', types.SimpleNamespace(linux_code_check='check (Linux 3.14)')),
            ('mutated_empty', mutated),
            ('path_separator', bad_path),
            ('duplicate_check', duplicate_check),
            ('shard_collision', shard_collision),
            ('ambiguous_list', [ALT_PROFILE, default_profile()]),
            ('ambiguous_tuple', (ALT_PROFILE,)),
        )

    def test_direct_bad_profile_refused_without_github_or_store_reads(self):
        for kind, bad in self._bad_profiles():
            with self.subTest(kind=kind):
                github = mock.Mock(side_effect=AssertionError('github_get must not be called'))
                store = mock.Mock()
                store.get.side_effect = AssertionError('store.get must not be called')

                self.assertEqual(
                    task_advance._merge_checks(_record(), PR, HEAD, _pull(), github, profile=bad),
                    task_advance.MERGE_CI_PROFILE_INVALID,
                )
                self.assertEqual(
                    task_advance._merge_once(
                        store=store, record=_record(), number=PR, head=HEAD, pr=_pull(),
                        events=[], github_get=github, gh_runner=_fake_gh,
                        root='/fake/root', sleep_fn=lambda _: None, profile=bad,
                    ),
                    {
                        'step': 'MERGE_REFUSED',
                        'codes': [task_advance.MERGE_CI_PROFILE_INVALID],
                        'result': None,
                    },
                )
                self.assertEqual(
                    eligibility.member_check(
                        store, TASK_ID, PR, github, train_root=self.train_root,
                        sleep=lambda _: None, profile=bad,
                    ),
                    {
                        'eligible': False,
                        'code': eligibility.TRAIN_MEMBER_CI_PROFILE_INVALID,
                        'member': None,
                    },
                )

    def test_patched_default_profile_error_or_bad_value_refused(self):
        github = mock.Mock(side_effect=AssertionError('github_get must not be called'))
        store = mock.Mock()
        store.get.side_effect = AssertionError('store.get must not be called')

        for kind, bad in self._bad_profiles():
            with self.subTest(kind=kind):
                with mock.patch.object(ci_profile, 'default_profile', return_value=bad):
                    self.assertEqual(
                        task_advance._merge_checks(_record(), PR, HEAD, _pull(), github),
                        task_advance.MERGE_CI_PROFILE_INVALID,
                    )
                    self.assertEqual(
                        task_advance._merge_once(
                            store=store, record=_record(), number=PR, head=HEAD, pr=_pull(),
                            events=[], github_get=github, gh_runner=_fake_gh,
                            root='/fake/root', sleep_fn=lambda _: None,
                        )['codes'],
                        [task_advance.MERGE_CI_PROFILE_INVALID],
                    )
                    self.assertEqual(
                        eligibility.member_check(
                            store, TASK_ID, PR, github, train_root=self.train_root,
                            sleep=lambda _: None,
                        )['code'],
                        eligibility.TRAIN_MEMBER_CI_PROFILE_INVALID,
                    )

        for exc in (CiProfileError(), RuntimeError('missing profile')):
            with self.subTest(exc=type(exc).__name__):
                with mock.patch.object(ci_profile, 'default_profile', side_effect=exc):
                    self.assertEqual(
                        task_advance._merge_checks(_record(), PR, HEAD, _pull(), github),
                        task_advance.MERGE_CI_PROFILE_INVALID,
                    )
                    self.assertEqual(
                        task_advance._merge_once(
                            store=store, record=_record(), number=PR, head=HEAD, pr=_pull(),
                            events=[], github_get=github, gh_runner=_fake_gh,
                            root='/fake/root', sleep_fn=lambda _: None,
                        )['codes'],
                        [task_advance.MERGE_CI_PROFILE_INVALID],
                    )
                    self.assertEqual(
                        eligibility.member_check(
                            store, TASK_ID, PR, github, train_root=self.train_root,
                            sleep=lambda _: None,
                        )['code'],
                        eligibility.TRAIN_MEMBER_CI_PROFILE_INVALID,
                    )

    def test_one_gate_decision_resolves_profile_once(self):
        patcher = mock.patch.object(sensitive_paths, '_read_list', return_value=copy.deepcopy(RULES))
        patcher.start()
        self.addCleanup(patcher.stop)

        files = [{'filename': 'tools/llmtool/llmtool/a.py'}]
        runs = [_run(1, ALT_PROFILE.linux_code_check), _run(2, ALT_PROFILE.macos_check)]
        github = _FakeGitHub(files=files, check_runs=runs)

        with mock.patch.object(ci_profile, 'default_profile', return_value=ALT_PROFILE) as patched:
            self.assertIsNone(task_advance._merge_checks(_record(), PR, HEAD, _pull(), github))
            self.assertEqual(patched.call_count, 1)

        with mock.patch.object(ci_profile, 'default_profile', return_value=ALT_PROFILE) as patched:
            outcome = task_advance._merge_once(
                store=_MemoryStore(_record()),
                record=_record(),
                number=PR,
                head=HEAD,
                pr=_pull(),
                events=_record()['completion_events'],
                github_get=github,
                gh_runner=_fake_gh,
                root='/fake/root',
                sleep_fn=lambda _: None,
            )
            self.assertEqual(outcome['step'], 'MERGED')
            self.assertEqual(patched.call_count, 1)

        with mock.patch.object(ci_profile, 'default_profile', return_value=ALT_PROFILE) as patched:
            res = eligibility.member_check(
                _MemoryStore(_record()), TASK_ID, PR, github,
                train_root=self.train_root, sleep=lambda _: None,
            )
            self.assertTrue(res['eligible'], res)
            self.assertEqual(patched.call_count, 1)


class R4NoGateWeakenedTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(sensitive_paths, '_read_list', return_value=copy.deepcopy(RULES))
        patcher.start()
        self.addCleanup(patcher.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.train_root = Path(tmp.name) / 'tasks'

    def test_existing_refusal_cases_stay_refused_under_substituted_profile(self):
        files = [{'filename': 'tools/llmtool/llmtool/a.py'}]
        cases = (
            ('missing_checks', [], False, 'MERGE_CHECKS_MISSING'),
            ('non_green_check', [_run(1, ALT_PROFILE.linux_code_check, 'failure')], False, 'MERGE_CHECKS_NOT_GREEN'),
            ('skipped_linux', [_run(1, ALT_PROFILE.linux_code_check, 'skipped'), _run(2, ALT_PROFILE.macos_check)], False, 'MERGE_REQUIRED_CHECK_SKIPPED'),
            ('missing_macos_strict', [_run(1, ALT_PROFILE.linux_code_check)], False, 'MERGE_REQUIRED_CHECK_MISSING'),
            ('missing_macos_allowed', [_run(1, ALT_PROFILE.linux_code_check)], True, 'MERGE_MACOS_CHECK_MISSING'),
            ('skipped_macos_strict', [_run(1, ALT_PROFILE.linux_code_check), _run(2, ALT_PROFILE.macos_check, 'skipped')], False, 'MERGE_MACOS_CHECK_REQUIRED'),
            ('skipped_macos_allowed', [_run(1, ALT_PROFILE.linux_code_check), _run(2, ALT_PROFILE.macos_check, 'skipped')], True, 'MERGE_MACOS_CHECK_SKIPPED'),
        )
        for kind, runs, allow_missing_macos, expected in cases:
            with self.subTest(kind=kind):
                github = _FakeGitHub(files=files, check_runs=runs)
                self.assertEqual(
                    task_advance._merge_checks(
                        _record(), PR, HEAD, _pull(), github,
                        allow_missing_macos=allow_missing_macos, profile=ALT_PROFILE,
                    ),
                    expected,
                )
                if not allow_missing_macos:
                    self.assertEqual(
                        eligibility.member_check(
                            _MemoryStore(_record()), TASK_ID, PR, github,
                            train_root=self.train_root, sleep=lambda _: None,
                            profile=ALT_PROFILE,
                        ),
                        {'eligible': False, 'code': expected, 'member': None},
                    )

    def test_pre_review_ci_gate_stays_on_legacy_checks_when_default_profile_patched(self):
        defaults = default_profile()
        files = [{'filename': 'tools/llmtool/llmtool/a.py'}]
        failed_default = _FakeGitHub(
            files=files,
            check_runs=[_run(1, defaults.linux_code_check, 'failure')],
        )
        alt_only = _FakeGitHub(
            files=files,
            check_runs=[_run(2, ALT_PROFILE.linux_code_check, 'failure')],
        )
        with mock.patch.object(ci_profile, 'default_profile', return_value=ALT_PROFILE):
            latest = {}
            self.assertEqual(
                task_advance._review_ci_gate(PR, HEAD, failed_default, latest_out=latest),
                ('CI_FAILED', 'LINUX_CI_FAILED'),
            )
            self.assertIn(latest.get('name'), task_advance._LINUX_FLAG_CHECKS)
            self.assertEqual(
                task_advance._review_ci_gate(PR, HEAD, alt_only),
                ('WAITING_CI', 'LINUX_CI_PENDING'),
            )


if __name__ == '__main__':
    unittest.main()
