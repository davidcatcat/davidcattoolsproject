"""Tests for mac_full_suite request_full_suite."""

from __future__ import annotations

import types
import unittest

from llmtool.mac_full_suite import (
    MAC_FULL_SUITE_ALREADY_QUEUED,
    MAC_FULL_SUITE_DISPATCHED,
    MAC_FULL_SUITE_DISPATCH_FAILED,
    MAC_FULL_SUITE_STATE_UNAVAILABLE,
    request_full_suite,
)

_EXPECTED_PATH = (
    'actions/workflows/llmtool-mac-ci.yml/runs?branch=main&event=workflow_dispatch&per_page=20'
)
_EXPECTED_ARGV = ['workflow', 'run', 'llmtool-mac-ci.yml', '--ref', 'main']


class FakeRunner:
    """Fake gh_runner recording calls and returning a configured result."""

    def __init__(self, returncode: int = 0, raise_exc: Exception | None = None):
        self.returncode = returncode
        self.raise_exc = raise_exc
        self.calls: list[tuple[str, list[str]]] = []

    def __call__(self, root: str, args: list[str]) -> types.SimpleNamespace:
        self.calls.append((root, list(args)))
        if self.raise_exc is not None:
            raise self.raise_exc
        return types.SimpleNamespace(returncode=self.returncode)


class FakeGithubGet:
    """Fake github_get recording paths and returning a configured payload."""

    def __init__(self, response: object = None, raise_exc: Exception | None = None):
        self.response = response
        self.raise_exc = raise_exc
        self.calls: list[str] = []

    def __call__(self, path: str) -> object:
        self.calls.append(path)
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response


class TestMacFullSuite(unittest.TestCase):
    """Unit tests for request_full_suite dispatch and refusal flows."""

    def test_dispatch_when_no_run_waits_empty_runs(self) -> None:
        """Dispatch when the recent runs list is empty."""
        runner = FakeRunner(returncode=0)
        github_get = FakeGithubGet(response={'workflow_runs': []})
        result = request_full_suite(runner, '/repo/root', github_get)

        self.assertEqual(result, MAC_FULL_SUITE_DISPATCHED)
        self.assertEqual(github_get.calls, [_EXPECTED_PATH])
        self.assertEqual(runner.calls, [('/repo/root', _EXPECTED_ARGV)])

    def test_dispatch_when_runs_are_completed(self) -> None:
        """Dispatch when all recent runs are finished."""
        runner = FakeRunner(returncode=0)
        github_get = FakeGithubGet(response={'workflow_runs': [
            {'status': 'completed', 'conclusion': 'success'},
            {'status': 'completed', 'conclusion': 'failure'},
        ]})
        result = request_full_suite(runner, '/repo/root', github_get)

        self.assertEqual(result, MAC_FULL_SUITE_DISPATCHED)
        self.assertEqual(runner.calls, [('/repo/root', _EXPECTED_ARGV)])

    def test_skip_when_run_is_queued(self) -> None:
        """Skip dispatch when any run is queued, waiting, pending, or requested."""
        queued_statuses = ['queued', 'waiting', 'pending', 'requested', 'QUEUED', 'WAITING']
        for status in queued_statuses:
            with self.subTest(status=status):
                runner = FakeRunner(returncode=0)
                github_get = FakeGithubGet(response={'workflow_runs': [
                    {'status': status},
                ]})
                result = request_full_suite(runner, '/repo/root', github_get)

                self.assertEqual(result, MAC_FULL_SUITE_ALREADY_QUEUED)
                self.assertEqual(runner.calls, [])

    def test_skip_when_queued_run_present_among_others(self) -> None:
        """Skip dispatch if a queued run exists among completed runs."""
        runner = FakeRunner(returncode=0)
        github_get = FakeGithubGet(response={'workflow_runs': [
            {'status': 'completed'},
            {'status': 'pending'},
        ]})
        result = request_full_suite(runner, '/repo/root', github_get)

        self.assertEqual(result, MAC_FULL_SUITE_ALREADY_QUEUED)
        self.assertEqual(runner.calls, [])

    def test_dispatch_when_run_only_in_progress(self) -> None:
        """A run only in progress does not stop the request."""
        runner = FakeRunner(returncode=0)
        github_get = FakeGithubGet(response={'workflow_runs': [
            {'status': 'in_progress'},
        ]})
        result = request_full_suite(runner, '/repo/root', github_get)

        self.assertEqual(result, MAC_FULL_SUITE_DISPATCHED)
        self.assertEqual(runner.calls, [('/repo/root', _EXPECTED_ARGV)])

    def test_failure_exit_from_runner(self) -> None:
        """Return MAC_FULL_SUITE_DISPATCH_FAILED when runner exits non-zero."""
        runner = FakeRunner(returncode=1)
        github_get = FakeGithubGet(response={'workflow_runs': []})
        result = request_full_suite(runner, '/repo/root', github_get)

        self.assertEqual(result, MAC_FULL_SUITE_DISPATCH_FAILED)
        self.assertEqual(runner.calls, [('/repo/root', _EXPECTED_ARGV)])

    def test_runner_exception(self) -> None:
        """Return MAC_FULL_SUITE_DISPATCH_FAILED when runner raises an exception."""
        runner = FakeRunner(raise_exc=RuntimeError('runner execution failure'))
        github_get = FakeGithubGet(response={'workflow_runs': []})
        result = request_full_suite(runner, '/repo/root', github_get)

        self.assertEqual(result, MAC_FULL_SUITE_DISPATCH_FAILED)
        self.assertEqual(runner.calls, [('/repo/root', _EXPECTED_ARGV)])

    def test_unreadable_list_read_raises(self) -> None:
        """Return MAC_FULL_SUITE_STATE_UNAVAILABLE when github_get raises."""
        runner = FakeRunner(returncode=0)
        github_get = FakeGithubGet(raise_exc=RuntimeError('network read failure'))
        result = request_full_suite(runner, '/repo/root', github_get)

        self.assertEqual(result, MAC_FULL_SUITE_STATE_UNAVAILABLE)
        self.assertEqual(runner.calls, [])

    def test_malformed_response_not_dict(self) -> None:
        """Return MAC_FULL_SUITE_STATE_UNAVAILABLE when response is not a dict."""
        for malformed in [None, [], 'unexpected string', 42]:
            with self.subTest(malformed=malformed):
                runner = FakeRunner(returncode=0)
                github_get = FakeGithubGet(response=malformed)
                result = request_full_suite(runner, '/repo/root', github_get)

                self.assertEqual(result, MAC_FULL_SUITE_STATE_UNAVAILABLE)
                self.assertEqual(runner.calls, [])

    def test_malformed_workflow_runs_not_list(self) -> None:
        """Return MAC_FULL_SUITE_STATE_UNAVAILABLE when workflow_runs is not a list."""
        for malformed in [{}, {'workflow_runs': None}, {'workflow_runs': 'not-a-list'}]:
            with self.subTest(malformed=malformed):
                runner = FakeRunner(returncode=0)
                github_get = FakeGithubGet(response=malformed)
                result = request_full_suite(runner, '/repo/root', github_get)

                self.assertEqual(result, MAC_FULL_SUITE_STATE_UNAVAILABLE)
                self.assertEqual(runner.calls, [])

    def test_malformed_run_item_not_dict(self) -> None:
        """Return MAC_FULL_SUITE_STATE_UNAVAILABLE when run entry is not a dict."""
        for bad_item in [None, 'not-a-dict', 123, []]:
            with self.subTest(bad_item=bad_item):
                runner = FakeRunner(returncode=0)
                github_get = FakeGithubGet(response={'workflow_runs': [bad_item]})
                result = request_full_suite(runner, '/repo/root', github_get)

                self.assertEqual(result, MAC_FULL_SUITE_STATE_UNAVAILABLE)
                self.assertEqual(runner.calls, [])

    def test_malformed_run_status_not_string(self) -> None:
        """Return MAC_FULL_SUITE_STATE_UNAVAILABLE when run status is not a string."""
        for bad_status in [{}, {'status': None}, {'status': 123}, {'status': []}]:
            with self.subTest(bad_status=bad_status):
                runner = FakeRunner(returncode=0)
                github_get = FakeGithubGet(response={'workflow_runs': [bad_status]})
                result = request_full_suite(runner, '/repo/root', github_get)

                self.assertEqual(result, MAC_FULL_SUITE_STATE_UNAVAILABLE)
                self.assertEqual(runner.calls, [])

    def test_none_runner_or_read(self) -> None:
        """Return MAC_FULL_SUITE_STATE_UNAVAILABLE when runner or github_get is None."""
        runner = FakeRunner(returncode=0)
        github_get = FakeGithubGet(response={'workflow_runs': []})

        self.assertEqual(request_full_suite(None, '/repo/root', github_get),
                         MAC_FULL_SUITE_STATE_UNAVAILABLE)
        self.assertEqual(request_full_suite(runner, '/repo/root', None),
                         MAC_FULL_SUITE_STATE_UNAVAILABLE)
        self.assertEqual(request_full_suite(None, '/repo/root', None),
                         MAC_FULL_SUITE_STATE_UNAVAILABLE)
        self.assertEqual(runner.calls, [])
        self.assertEqual(github_get.calls, [])


if __name__ == '__main__':
    unittest.main()
