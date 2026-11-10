"""Tests for the mergeable first gate in task_advance merge step.

The first gate re-fetches the PR (up to 3 times, sleeping 5 seconds before
each fetch) while GitHub is still computing mergeability, then passes the
latest PR to _merge_checks and the later gates.
"""

import sys
import unittest
from pathlib import Path

# Add package directory to path for imports.
root_dir = str(Path(__file__).resolve().parents[1])
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from llmtool.task_advance import _merge_once


class FakeProcess:
    def __init__(self, returncode=0, stdout=b'', stderr=b''):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeStore:
    def __init__(self):
        self.updates = []

    def compare_and_update(self, task_id, expected=None, **kwargs):
        self.updates.append((task_id, expected, kwargs))
        return True


def _pull(head_sha, mergeable):
    return {
        'number': 1,
        'head': {'sha': head_sha},
        'state': 'open',
        'merged': False,
        'mergeable': mergeable,
    }


def _make_fixtures(head_sha, mergeable):
    record = {
        'task_id': 'task-advance-mergeable-first-1',
        'record_sha256': '1' * 64,
        'review_dispatch_claim': [{
            'pr': 1,
            'head_sha': head_sha,
            'state': 'COMPLETED',
            'version': 1,
            'review_result': {'verdict': 'PASS'},
        }],
    }
    events = [{
        'source': 'REVIEWER',
        'source_sha': head_sha,
        'version': 1,
        'outcome': 'REVIEW_PASS',
    }]
    pr = {
        'number': 1,
        'head': {'sha': head_sha},
        'state': 'open',
        'merged': False,
        'mergeable': mergeable,
    }
    return record, events, pr


def _make_github_get(head_sha, pulls_responses, pulls_calls):
    def fake_github_get(path):
        if path == f'commits/{head_sha}/check-runs?per_page=100':
            return {
                'total_count': 1,
                'check_runs': [{
                    'id': 101,
                    'name': 'check (docs)',
                    'head_sha': head_sha,
                    'status': 'completed',
                    'conclusion': 'success',
                }],
            }
        if path == 'pulls/1/files?per_page=100':
            return [{'filename': 'docs/guide.md'}]
        if path == 'pulls/1':
            pulls_calls.append(path)
            if pulls_responses:
                return pulls_responses.pop(0)
            return _pull(head_sha, None)
        if path == 'branches/main':
            return {'commit': {'sha': '2' * 40}}
        if path.startswith('compare/'):
            return {'status': 'identical'}
        return {}
    return fake_github_get


class TestTaskAdvanceMergeableFirstGate(unittest.TestCase):
    def test_mergeable_none_then_true_passes_first_gate_after_one_refetch(self):
        head_sha = 'a' * 40
        # First snapshot still computing mergeability; the first gate re-fetch
        # once and resolves it. The later loop's initial fetch also sees True.
        record, events, pr = _make_fixtures(head_sha, None)
        store = FakeStore()
        sleep_calls = []
        pulls_calls = []
        pulls_responses = [_pull(head_sha, True), _pull(head_sha, True)]
        github_get = _make_github_get(head_sha, pulls_responses, pulls_calls)

        outcome = _merge_once(
            store=store,
            record=record,
            number=1,
            head=head_sha,
            pr=pr,
            events=events,
            github_get=github_get,
            gh_runner=lambda root, args: FakeProcess(0),
            root='/fake/root',
            sleep_fn=lambda seconds: sleep_calls.append(seconds),
        )

        self.assertEqual(outcome['step'], 'MERGED')
        # One first-gate re-fetch after one sleep; the later loop fetches once.
        self.assertEqual(sleep_calls, [5])
        self.assertEqual(len(pulls_calls), 2)

    def test_mergeable_none_four_times_refuses_after_three_refetches(self):
        head_sha = 'a' * 40
        # First snapshot and all three first-gate re-fetches still None.
        record, events, pr = _make_fixtures(head_sha, None)
        store = FakeStore()
        sleep_calls = []
        pulls_calls = []
        pulls_responses = [_pull(head_sha, None)] * 3
        github_get = _make_github_get(head_sha, pulls_responses, pulls_calls)

        outcome = _merge_once(
            store=store,
            record=record,
            number=1,
            head=head_sha,
            pr=pr,
            events=events,
            github_get=github_get,
            gh_runner=lambda root, args: FakeProcess(0),
            root='/fake/root',
            sleep_fn=lambda seconds: sleep_calls.append(seconds),
        )

        self.assertEqual(outcome['step'], 'MERGE_REFUSED')
        self.assertIn('MERGE_PR_NOT_MERGEABLE', outcome['codes'])
        # The first gate stops at 3 re-fetches with one sleep each.
        self.assertEqual(sleep_calls, [5, 5, 5])
        self.assertEqual(len(pulls_calls), 3)

    def test_mergeable_true_on_first_snapshot_no_extra_fetch_no_sleep(self):
        head_sha = 'a' * 40
        # Mergeability already known; the first gate must add nothing.
        record, events, pr = _make_fixtures(head_sha, True)
        store = FakeStore()
        sleep_calls = []
        pulls_calls = []
        pulls_responses = [_pull(head_sha, True)]
        github_get = _make_github_get(head_sha, pulls_responses, pulls_calls)

        outcome = _merge_once(
            store=store,
            record=record,
            number=1,
            head=head_sha,
            pr=pr,
            events=events,
            github_get=github_get,
            gh_runner=lambda root, args: FakeProcess(0),
            root='/fake/root',
            sleep_fn=lambda seconds: sleep_calls.append(seconds),
        )

        self.assertEqual(outcome['step'], 'MERGED')
        self.assertEqual(sleep_calls, [])
        # The only pulls/1 call is the later loop's initial fetch.
        self.assertEqual(len(pulls_calls), 1)

    def test_mergeable_false_on_first_snapshot_no_extra_fetch_no_sleep(self):
        head_sha = 'a' * 40
        # Mergeability already known to be false; the first gate adds nothing
        # and the latest PR with mergeable False reaches the gates.
        record, events, pr = _make_fixtures(head_sha, False)
        store = FakeStore()
        sleep_calls = []
        pulls_calls = []
        github_get = _make_github_get(head_sha, [], pulls_calls)

        outcome = _merge_once(
            store=store,
            record=record,
            number=1,
            head=head_sha,
            pr=pr,
            events=events,
            github_get=github_get,
            gh_runner=lambda root, args: FakeProcess(0),
            root='/fake/root',
            sleep_fn=lambda seconds: sleep_calls.append(seconds),
        )

        self.assertEqual(outcome['step'], 'MERGE_REFUSED')
        self.assertIn('MERGE_PR_NOT_MERGEABLE', outcome['codes'])
        self.assertEqual(sleep_calls, [])
        self.assertEqual(len(pulls_calls), 0)


if __name__ == '__main__':
    unittest.main()
