"""#1145: the GitHub reader path guard accepts the poller's since query only.

Every check uses the real task_completion._GITHUB_API_PATH and the real
_github_get guard. The gh lookup is patched to raise a sentinel, so nothing
runs: reaching the lookup means the guard accepted the path; a completion
stop before the lookup means the guard refused it.
"""

import unittest
from unittest.mock import patch

from llmtool import github_events, task_completion
from llmtool.errors import LLMToolError


class _GuardPassed(Exception):
    """Raised by the patched gh lookup: the path passed the guard."""


def _guard_accepts(path):
    with patch.object(task_completion.shutil, 'which',
                      side_effect=_GuardPassed) as which:
        try:
            task_completion._github_get(path)
        except _GuardPassed:
            return True
        except LLMToolError as error:
            if getattr(error, 'completion_code', None) != 'GITHUB_EVIDENCE_UNAVAILABLE':
                raise
            if which.called:
                raise AssertionError('gh lookup ran but the guard did not pass')
            return False
    raise AssertionError('guard neither refused nor reached the gh lookup')


class _Store:
    """Minimal store: one Task bound to the polled issue, with a prior event."""

    def __init__(self, number, prior_received_at=None):
        self.task = {'task_id': 'task-1145', 'run_id': 'run-1145',
                     'pm_session_id': 'pm-1145', 'issue_number': number,
                     'completion_events': []}
        if prior_received_at is not None:
            self.task['completion_events'].append({
                'source': 'GITHUB', 'session_id': f'github-pm-1145-{number}',
                'received_at': prior_received_at, 'sequence': 0,
                'event_id': 'e' * 32})

    def task_ids(self):
        return [self.task['task_id']]

    def get(self, task_id):
        assert task_id == self.task['task_id']
        return self.task


def _poller_paths(number, *, since=None, prior_received_at=None):
    paths = []

    def get(path):
        paths.append(path)
        return [] if '/comments?' in path else {}

    github_events.poll(_Store(number, prior_received_at), 'pm-1145', [number],
                       since=since, github_get=get)
    return paths


VALID_SINCE = 'issues/1145/comments?per_page=100&page=1&since=2026-11-07T01:02:03Z'


class PollerSincePathTests(unittest.TestCase):
    def test_explicit_since_path_built_by_poller_is_accepted(self):
        paths = _poller_paths(1145, since='2026-11-07T01:02:03Z')
        self.assertIn(VALID_SINCE, paths)
        for path in paths:
            self.assertTrue(_guard_accepts(path), path)

    def test_cursor_from_prior_event_path_is_accepted(self):
        paths = _poller_paths(1145, prior_received_at='2026-11-07T10:00:00Z')
        expected = ('issues/1145/comments?per_page=100&page=1'
                    '&since=2026-11-07T09:59:59Z')
        self.assertIn(expected, paths)
        for path in paths:
            self.assertTrue(_guard_accepts(path), path)

    def test_poll_through_real_reader_passes_guard(self):
        # Without a fake reader the poller calls the real _github_get; reaching
        # the patched gh lookup proves the since path passed the guard.
        with patch.object(task_completion.shutil, 'which',
                          side_effect=_GuardPassed):
            with self.assertRaises(_GuardPassed):
                github_events.poll(_Store(1145, '2026-11-07T10:00:00Z'),
                                   'pm-1145', [1145])

    def test_digit_bounds_are_accepted(self):
        for path in (
                'issues/1/comments?per_page=1&page=1&since=2026-11-07T01:02:03Z',
                'issues/9999999/comments?per_page=100&page=100&since=2026-11-07T01:02:03Z',
                'issues/1145/comments?per_page=999&page=999&since=0000-00-00T00:00:00Z'):
            self.assertTrue(_guard_accepts(path), path)
            self.assertIsNotNone(task_completion._GITHUB_API_PATH.fullmatch(path), path)


class RefusedSincePathTests(unittest.TestCase):
    REFUSED = (
        # Non-UTC offsets, fractional seconds, missing or lowercase Z.
        'issues/1145/comments?per_page=100&page=1&since=2026-11-07T01:02:03+08:00',
        'issues/1145/comments?per_page=100&page=1&since=2026-11-07T01:02:03-05:00',
        'issues/1145/comments?per_page=100&page=1&since=2026-11-07T01:02:03+0000',
        'issues/1145/comments?per_page=100&page=1&since=2026-11-07T01:02:03.123Z',
        'issues/1145/comments?per_page=100&page=1&since=2026-11-07T01:02:03',
        'issues/1145/comments?per_page=100&page=1&since=2026-11-07T01:02:03z',
        'issues/1145/comments?per_page=100&page=1&since=2026-11-07 01:02:03Z',
        'issues/1145/comments?per_page=100&page=1&since=2026-11-07T01:02Z',
        # Wrong digit counts.
        'issues/12345678/comments?per_page=100&page=1&since=2026-11-07T01:02:03Z',
        'issues//comments?per_page=100&page=1&since=2026-11-07T01:02:03Z',
        'issues/1145/comments?per_page=1000&page=1&since=2026-11-07T01:02:03Z',
        'issues/1145/comments?per_page=&page=1&since=2026-11-07T01:02:03Z',
        'issues/1145/comments?per_page=100&page=1000&since=2026-11-07T01:02:03Z',
        'issues/1145/comments?per_page=100&page=&since=2026-11-07T01:02:03Z',
        'issues/1145/comments?per_page=100&page=1&since=20261-11-07T01:02:03Z',
        'issues/1145/comments?per_page=100&page=1&since=2026-1-07T01:02:03Z',
        'issues/1145/comments?per_page=100&page=1&since=2026-11-7T01:02:03Z',
        'issues/1145/comments?per_page=100&page=1&since=2026-11-07T1:02:03Z',
        'issues/1145/comments?per_page=100&page=1&since=2026-11-07T01:2:03Z',
        'issues/1145/comments?per_page=100&page=1&since=2026-11-07T01:02:3Z',
        # Non-ASCII digits.
        'issues/1145/comments?per_page=100&page=1&since=２026-11-07T01:02:03Z',
        'issues/١١٤٥/comments?per_page=100&page=1&since=2026-11-07T01:02:03Z',
        # Extra characters or a second parameter after since.
        'issues/1145/comments?per_page=100&page=1&since=2026-11-07T01:02:03ZZ',
        'issues/1145/comments?per_page=100&page=1&since=2026-11-07T01:02:03Z&x=1',
        'issues/1145/comments?per_page=100&page=1&since=2026-11-07T01:02:03Z&since=2026-11-07T01:02:03Z',
        'issues/1145/comments?per_page=100&page=1&since=2026-11-07T01:02:03Z/',
        # Other parameter orders or names around since.
        'issues/1145/comments?since=2026-11-07T01:02:03Z&per_page=100&page=1',
        'issues/1145/comments?per_page=100&since=2026-11-07T01:02:03Z',
        'issues/1145/comments?page=1&per_page=100&since=2026-11-07T01:02:03Z',
        'issues/1145/comments?per_page=100&page=1&x=1&since=2026-11-07T01:02:03Z',
        'issues/1145/comment?per_page=100&page=1&since=2026-11-07T01:02:03Z',
        'pulls/1145/comments?per_page=100&page=1&since=2026-11-07T01:02:03Z',
        # Prefix tricks: '..' segments and a leading '/'.
        '../issues/1145/comments?per_page=100&page=1&since=2026-11-07T01:02:03Z',
        'issues/../1145/comments?per_page=100&page=1&since=2026-11-07T01:02:03Z',
        'x/../issues/1145/comments?per_page=100&page=1&since=2026-11-07T01:02:03Z',
        '/issues/1145/comments?per_page=100&page=1&since=2026-11-07T01:02:03Z',
        # Percent signs, spaces and newlines.
        'issues/1145/comments?per_page=100&page=1&since=2026-11-07T01%3A02%3A03Z',
        'issues/1145/comments?per_page=100&page=1&since=2026-11-07T01:02:03Z%0A',
        'issues/1145/comments?per_page=100&page=1&since=2026-11-07T01:02:03Z ',
        ' issues/1145/comments?per_page=100&page=1&since=2026-11-07T01:02:03Z',
        'issues/1145/comments?per_page=100&page=1&since=2026-11-07T01:02:03Z\n',
        '\nissues/1145/comments?per_page=100&page=1&since=2026-11-07T01:02:03Z',
        'issues/1145/comments?per_page=100&page=1&since=2026-11-07T01:02:03Z\r',
        # ':' anywhere other than the since timestamp.
        'issues/1145:1/comments?per_page=100&page=1',
        'issues/1145/comments?per_page=100&page=1&until=2026-11-07T01:02:03Z',
        'issues/comments?per_page=100&page=1&since=2026-11-07T01:02:03Z',
        'pulls/1145?since=2026-11-07T01:02:03Z',
        'commits/abc:def/check-runs?per_page=100',
        'git/ref/heads/main:x',
        'https://api.github.com/repos/x/y',
        'issues/1145/comments?per_page=10:0&page=1&since=2026-11-07T01:02:03Z',
    )

    def test_refused_paths(self):
        for path in self.REFUSED:
            with self.subTest(path=path):
                self.assertIsNone(task_completion._GITHUB_API_PATH.fullmatch(path))
                self.assertFalse(_guard_accepts(path))

    def test_non_string_path_is_refused(self):
        for path in (None, b'pulls/1', 1145):
            with self.subTest(path=path):
                self.assertFalse(_guard_accepts(path))


class ExistingPathsStayAcceptedTests(unittest.TestCase):
    SHA_A = 'a' * 40
    SHA_B = '0123456789abcdef0123456789abcdef01234567'
    ACCEPTED = (
        'git/ref/heads/main',
        'pulls/1145',
        'pulls/1145/reviews?per_page=100',
        'issues/1145/comments?per_page=100',
        'issues/1145/comments?per_page=100&page=1',
        'issues/1145/comments?per_page=100&page=100',
        'actions/workflows/llmtool-mac-ci.yml',
        f'actions/workflows/123/runs?head_sha={SHA_A}&event=pull_request&per_page=100',
        'actions/runs/123/attempts/1/jobs?per_page=100',
        f'commits/{SHA_B}/check-runs?per_page=100',
        f'compare/{SHA_A}...{SHA_B}',
    )

    def test_paths_accepted_today_stay_accepted(self):
        for path in self.ACCEPTED:
            with self.subTest(path=path):
                self.assertIsNotNone(task_completion._GITHUB_API_PATH.fullmatch(path))
                self.assertTrue(_guard_accepts(path))

    def test_existing_refusals_stay_refused(self):
        for path in ('/pulls/1', 'pulls/../x', '..', 'compare/a...b',
                     f'compare/{self.SHA_A}..{self.SHA_B}', 'x' * 257, '',
                     'pulls/1#x', 'pulls/1%2F'):
            with self.subTest(path=path):
                self.assertFalse(_guard_accepts(path))


if __name__ == '__main__':
    unittest.main()
