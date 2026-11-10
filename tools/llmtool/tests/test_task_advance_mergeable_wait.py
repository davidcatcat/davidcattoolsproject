"""Tests for waiting on mergeable calculation in task_advance merge step."""

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


def _make_fixtures(head_sha):
    record = {
        'task_id': 'task-advance-mergeable-1',
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
        'mergeable': True,
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
            return {
                'number': 1,
                'head': {'sha': head_sha},
                'state': 'open',
                'merged': False,
                'mergeable': None,
            }
        if path == 'branches/main':
            return {'commit': {'sha': '2' * 40}}
        if path.startswith('compare/'):
            return {'status': 'identical'}
        return {}
    return fake_github_get


class TestTaskAdvanceMergeableWait(unittest.TestCase):
    def test_mergeable_none_then_true_merges_after_one_refetch(self):
        head_sha = 'a' * 40
        record, events, pr = _make_fixtures(head_sha)
        store = FakeStore()
        sleep_calls = []
        pulls_calls = []
        pulls_responses = [
            {'number': 1, 'head': {'sha': head_sha}, 'state': 'open', 'merged': False, 'mergeable': None},
            {'number': 1, 'head': {'sha': head_sha}, 'state': 'open', 'merged': False, 'mergeable': True},
        ]
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
        self.assertEqual(sleep_calls, [5])
        self.assertEqual(len(pulls_calls), 2)

    def test_mergeable_none_four_times_refuses(self):
        head_sha = 'a' * 40
        record, events, pr = _make_fixtures(head_sha)
        store = FakeStore()
        sleep_calls = []
        pulls_calls = []
        pulls_responses = [
            {'number': 1, 'head': {'sha': head_sha}, 'state': 'open', 'merged': False, 'mergeable': None},
            {'number': 1, 'head': {'sha': head_sha}, 'state': 'open', 'merged': False, 'mergeable': None},
            {'number': 1, 'head': {'sha': head_sha}, 'state': 'open', 'merged': False, 'mergeable': None},
            {'number': 1, 'head': {'sha': head_sha}, 'state': 'open', 'merged': False, 'mergeable': None},
        ]
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
        self.assertEqual(sleep_calls, [5, 5, 5])
        self.assertEqual(len(pulls_calls), 4)

    def test_mergeable_false_refuses_no_refetch_no_sleep(self):
        head_sha = 'a' * 40
        record, events, pr = _make_fixtures(head_sha)
        store = FakeStore()
        sleep_calls = []
        pulls_calls = []
        pulls_responses = [
            {'number': 1, 'head': {'sha': head_sha}, 'state': 'open', 'merged': False, 'mergeable': False},
        ]
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
        self.assertEqual(sleep_calls, [])
        self.assertEqual(len(pulls_calls), 1)


if __name__ == '__main__':
    unittest.main()
