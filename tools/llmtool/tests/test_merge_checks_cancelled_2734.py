"""#2734: a cancelled run is excused by a newer success or skipped run of the same check."""

import unittest

from llmtool import task_advance


HEAD = 'a' * 40
MAIN_SHA = 'e' * 40
LINUX = 'check (Linux 3.14)'
MACOS = 'check (macOS 3.13)'
CODE_FILES = ['tools/llmtool/llmtool/module.py']


def run(name, conclusion='success', status='completed', head=HEAD):
    return {'name': name, 'head_sha': head, 'status': status, 'conclusion': conclusion}


def fake_github(paths, runs):
    """Return an in-memory github_get; ids follow the list order like the fixtures."""
    def github_get(path):
        if path == f'commits/{HEAD}/check-runs?per_page=100':
            numbered = [dict(item, id=index + 1) for index, item in enumerate(runs)]
            return {'total_count': len(numbered), 'check_runs': numbered}
        if path == 'pulls/17/files?per_page=100':
            return [{'filename': item} for item in paths]
        if path == 'pulls/17':
            return open_pr()
        if path == 'branches/main':
            return {'commit': {'sha': MAIN_SHA}}
        if path == f'compare/{MAIN_SHA}...{HEAD}':
            return {'status': 'ahead'}
        if path.endswith('/comments?per_page=100'):
            return []
        raise AssertionError(f'unexpected read: {path}')

    return github_get


def open_pr():
    return {'number': 17, 'head': {'sha': HEAD}, 'state': 'open', 'merged': False,
            'mergeable': True, 'labels': [{'name': 'mac-ci'}]}


def merge_checks(paths, runs):
    return task_advance._merge_checks({}, 17, HEAD, open_pr(),
                                      fake_github(paths, runs))


class CancelledSupersededByNewerSkippedTests(unittest.TestCase):
    def test_cancelled_shards_with_newer_skipped_shards_are_green(self):
        runs = [run('shards (1)', 'cancelled'), run('shards (2)', 'cancelled'),
                run('shards (1)', 'skipped'), run('shards (2)', 'skipped'),
                run(LINUX), run(MACOS)]
        self.assertIsNone(merge_checks(CODE_FILES, runs))

    def test_failure_with_newer_skipped_run_stays_not_green(self):
        runs = [run('shards', 'failure'), run('shards', 'skipped'),
                run(LINUX), run(MACOS)]
        self.assertEqual(merge_checks(CODE_FILES, runs), 'MERGE_CHECKS_NOT_GREEN')

    def test_cancelled_required_linux_with_newer_skipped_is_required_check_skipped(self):
        runs = [run(LINUX, 'cancelled'), run(LINUX, 'skipped'), run(MACOS)]
        self.assertEqual(merge_checks(CODE_FILES, runs),
                         'MERGE_REQUIRED_CHECK_SKIPPED')

    def test_cancelled_run_with_older_skipped_run_stays_not_green(self):
        runs = [run('shards', 'skipped'), run('shards', 'cancelled'),
                run(LINUX), run(MACOS)]
        self.assertEqual(merge_checks(CODE_FILES, runs), 'MERGE_CHECKS_NOT_GREEN')


if __name__ == '__main__':
    unittest.main()
