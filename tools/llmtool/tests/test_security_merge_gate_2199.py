"""#2199: minimal security merge gate in task_advance._merge_once."""

import unittest
from unittest import mock

from llmtool import review_result, task_advance


HEAD = 'a' * 40
OTHER_HEAD = 'b' * 40
MAIN_SHA = 'e' * 40
LINUX = 'check (Linux 3.14)'
MACOS = 'check (macOS 3.13)'
CODE_FILES = ['tools/llmtool/llmtool/module.py']


def _fake_github(paths=CODE_FILES):
    pr = {
        'number': 17,
        'head': {'sha': HEAD},
        'state': 'open',
        'merged': False,
        'mergeable': True,
        'labels': [{'name': 'mac-ci'}],
    }
    runs = [
        {'id': 1, 'name': LINUX, 'head_sha': HEAD, 'status': 'completed', 'conclusion': 'success'},
        {'id': 2, 'name': MACOS, 'head_sha': HEAD, 'status': 'completed', 'conclusion': 'success'},
    ]

    def github_get(path):
        if path == f'commits/{HEAD}/check-runs?per_page=100':
            return {'total_count': len(runs), 'check_runs': runs}
        if path == 'pulls/17/files?per_page=100':
            return [{'filename': item} for item in paths]
        if path == 'pulls/17':
            return pr
        if path == 'branches/main':
            return {'commit': {'sha': MAIN_SHA}}
        if path == f'compare/{MAIN_SHA}...{HEAD}':
            return {'status': 'ahead'}
        if path.endswith('/comments?per_page=100'):
            return []
        raise AssertionError(f'unexpected read: {path}')

    return pr, github_get


def _run_merge_once(claims, events):
    pr, github_get = _fake_github()
    record = {
        'task_id': '1' * 32,
        'record_sha256': 'r' * 64,
        'review_dispatch_claim': claims,
    }
    updates = []
    calls = []

    class Store:
        def compare_and_update(self, *args, **kwargs):
            updates.append((args, kwargs))

    def gh_runner(_root, args):
        calls.append(list(args))
        return type('Result', (), {'returncode': 0})()

    result = task_advance._merge_once(
        store=Store(),
        record=record,
        number=17,
        head=HEAD,
        pr=pr,
        events=events,
        github_get=github_get,
        gh_runner=gh_runner,
        root='/repo',
    )
    return result, calls, updates


def _formal_pass(version=2, head=HEAD):
    claim = {
        'pr': 17,
        'head_sha': head,
        'version': version,
        'state': 'COMPLETED',
        'review_result': {'verdict': 'PASS'},
    }
    event = {
        'source': 'REVIEWER',
        'source_sha': head,
        'version': version,
        'outcome': 'REVIEW_PASS',
    }
    return claim, event


class SecurityMergeGateTests(unittest.TestCase):
    def test_security_fix_or_fail_with_newer_formal_pass_is_refused(self):
        formal_claim, formal_event = _formal_pass(version=2)
        for verdict in ('FIX', 'FAIL', 'BLOCKED'):
            with self.subTest(verdict=verdict):
                sec_claim = {
                    'pr': 17,
                    'head_sha': HEAD,
                    'version': 1,
                    'review_kind': 'security',
                    'state': 'COMPLETED',
                    'review_result': {'verdict': verdict, 'head': HEAD},
                }
                result, calls, updates = _run_merge_once(
                    [sec_claim, formal_claim], [formal_event])
                self.assertEqual(result['step'], 'MERGE_REFUSED')
                self.assertEqual(result['codes'], ['MERGE_SECURITY_REVIEW_NOT_PASS'])
                self.assertEqual(calls, [])
                self.assertEqual(updates, [])

    def test_security_non_pass_at_higher_version_than_formal_pass_is_refused(self):
        formal_claim, formal_event = _formal_pass(version=1)
        sec_claim = {
            'pr': 17,
            'head_sha': HEAD,
            'version': 91,
            'review_kind': 'security',
            'state': 'COMPLETED',
            'review_result': {'verdict': 'FIX', 'head': HEAD},
        }
        result, calls, updates = _run_merge_once(
            [formal_claim, sec_claim], [formal_event])
        self.assertEqual(result['step'], 'MERGE_REFUSED')
        self.assertEqual(result['codes'], ['MERGE_SECURITY_REVIEW_NOT_PASS'])
        self.assertEqual(calls, [])
        self.assertEqual(updates, [])

    def test_security_incomplete_claim_or_fail_event_is_refused(self):
        formal_claim, formal_event = _formal_pass(version=1)
        for state in ('CLAIMED', 'DISPATCHED', 'UNKNOWN'):
            with self.subTest(state=state):
                sec_claim = {
                    'pr': 17,
                    'head_sha': HEAD,
                    'version': 91,
                    'review_kind': 'security',
                    'state': state,
                }
                result, calls, updates = _run_merge_once(
                    [formal_claim, sec_claim], [formal_event])
                self.assertEqual(result['step'], 'MERGE_REFUSED')
                self.assertEqual(result['codes'], ['MERGE_SECURITY_REVIEW_NOT_PASS'])
                self.assertEqual(calls, [])
                self.assertEqual(updates, [])

        sec_event = {
            'source': 'REVIEWER',
            'source_sha': HEAD,
            'review_kind': 'security',
            'outcome': 'REVIEW_FAIL',
        }
        result, calls, updates = _run_merge_once(
            [formal_claim], [formal_event, sec_event])
        self.assertEqual(result['step'], 'MERGE_REFUSED')
        self.assertEqual(result['codes'], ['MERGE_SECURITY_REVIEW_NOT_PASS'])
        self.assertEqual(calls, [])
        self.assertEqual(updates, [])

    def test_security_pass_plus_security_fix_at_same_head_is_refused(self):
        formal_claim, formal_event = _formal_pass(version=1)
        sec_pass = {
            'pr': 17,
            'head_sha': HEAD,
            'version': 91,
            'review_kind': 'security',
            'state': 'COMPLETED',
            'review_result': {'verdict': 'PASS', 'head': HEAD},
        }
        sec_fix = {
            'pr': 17,
            'head_sha': HEAD,
            'version': 92,
            'review_kind': 'security',
            'state': 'COMPLETED',
            'review_result': {'verdict': 'FIX', 'head': HEAD},
        }
        result, calls, updates = _run_merge_once(
            [formal_claim, sec_pass, sec_fix], [formal_event])
        self.assertEqual(result['step'], 'MERGE_REFUSED')
        self.assertEqual(result['codes'], ['MERGE_SECURITY_REVIEW_NOT_PASS'])
        self.assertEqual(calls, [])
        self.assertEqual(updates, [])

    def test_unreadable_or_ambiguous_security_verdict_fails_closed(self):
        formal_claim, formal_event = _formal_pass(version=1)
        ambiguous_cases = [
            ('legacy_v91_without_kind', [formal_claim, {
                'pr': 17, 'head_sha': HEAD, 'version': 91, 'state': 'COMPLETED',
                'review_result': {'verdict': 'PASS'},
            }], [formal_event]),
            ('malformed_review_kind', [formal_claim, {
                'pr': 17, 'head_sha': HEAD, 'version': 91, 'review_kind': 'unknown',
                'state': 'COMPLETED', 'review_result': {'verdict': 'PASS'},
            }], [formal_event]),
            ('missing_review_result', [formal_claim, {
                'pr': 17, 'head_sha': HEAD, 'version': 91, 'review_kind': 'security',
                'state': 'COMPLETED',
            }], [formal_event]),
            ('unknown_verdict_value', [formal_claim, {
                'pr': 17, 'head_sha': HEAD, 'version': 91, 'review_kind': 'security',
                'state': 'COMPLETED', 'review_result': {'verdict': 'MAYBE'},
            }], [formal_event]),
            ('result_head_mismatch', [formal_claim, {
                'pr': 17, 'head_sha': HEAD, 'version': 91, 'review_kind': 'security',
                'state': 'COMPLETED', 'review_result': {'verdict': 'PASS', 'head': OTHER_HEAD},
            }], [formal_event]),
            ('non_dict_claim_entry', [formal_claim, 'corrupt'], [formal_event]),
            ('non_dict_event_entry', [formal_claim], [formal_event, 'corrupt']),
            ('event_with_unknown_outcome', [formal_claim], [formal_event, {
                'source': 'REVIEWER', 'source_sha': HEAD, 'review_kind': 'security',
            }]),
            ('secreview_session_with_formal_kind', [formal_claim], [formal_event, {
                'source': 'REVIEWER', 'source_sha': HEAD, 'review_kind': 'formal',
                'session_id': 'secreview-abc', 'outcome': 'REVIEW_PASS',
            }]),
        ]
        for label, claims, events in ambiguous_cases:
            with self.subTest(label=label):
                result, calls, updates = _run_merge_once(claims, events)
                self.assertEqual(result['step'], 'MERGE_REFUSED')
                self.assertEqual(result['codes'], ['MERGE_SECURITY_REVIEW_UNKNOWN'])
                self.assertEqual(calls, [])
                self.assertEqual(updates, [])

    def test_review_kind_exception_fails_closed(self):
        formal_claim, formal_event = _formal_pass(version=1)
        with mock.patch.object(review_result, 'review_kind', side_effect=RuntimeError('boom')):
            result = task_advance._merge_security_claim(
                {'review_dispatch_claim': [formal_claim]}, 17, HEAD, [formal_event])
        self.assertEqual(result, 'MERGE_SECURITY_REVIEW_UNKNOWN')

    def test_no_security_verdict_merges_as_before(self):
        formal_claim, formal_event = _formal_pass(version=1)
        result, calls, _updates = _run_merge_once([formal_claim], [formal_event])
        self.assertEqual(result['step'], 'MERGED')
        self.assertEqual(sum(call[:2] == ['pr', 'merge'] for call in calls), 1)

    def test_security_pass_at_exact_head_merges_as_before(self):
        formal_claim, formal_event = _formal_pass(version=1)
        sec_claim = {
            'pr': 17,
            'head_sha': HEAD,
            'version': 91,
            'review_kind': 'security',
            'state': 'COMPLETED',
            'review_result': {'verdict': 'PASS', 'head': HEAD},
        }
        sec_event = {
            'source': 'REVIEWER',
            'source_sha': HEAD,
            'version': 91,
            'review_kind': 'security',
            'session_id': 'secreview-1234',
            'outcome': 'REVIEW_PASS',
        }
        result, calls, _updates = _run_merge_once(
            [formal_claim, sec_claim], [formal_event, sec_event])
        self.assertEqual(result['step'], 'MERGED')
        self.assertEqual(sum(call[:2] == ['pr', 'merge'] for call in calls), 1)

    def test_security_verdict_on_another_head_does_not_block(self):
        formal_claim, formal_event = _formal_pass(version=2, head=HEAD)
        old_sec_fail = {
            'pr': 17,
            'head_sha': OTHER_HEAD,
            'version': 91,
            'review_kind': 'security',
            'state': 'COMPLETED',
            'review_result': {'verdict': 'FIX', 'head': OTHER_HEAD},
        }
        old_sec_event = {
            'source': 'REVIEWER',
            'source_sha': OTHER_HEAD,
            'version': 91,
            'review_kind': 'security',
            'outcome': 'REVIEW_FAIL',
        }
        result, calls, _updates = _run_merge_once(
            [old_sec_fail, formal_claim], [old_sec_event, formal_event])
        self.assertEqual(result['step'], 'MERGED')
        self.assertEqual(sum(call[:2] == ['pr', 'merge'] for call in calls), 1)


if __name__ == '__main__':
    unittest.main()
