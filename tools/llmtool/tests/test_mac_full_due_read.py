"""#1485 piece 1: mac_full_due GitHub read helpers."""

import unittest

from llmtool import mac_full_due as due

SHA = 'a' * 40
OK = {'jobs': [{'name': name, 'conclusion': 'success'} for name in due.JOBS]}


def reader(pulls, jobs=OK, runs=()):
    pages = {'branches/main': {'commit': {'sha': SHA, 'commit': {
        'committer': {'date': '2026-01-01T00:00:00Z'}}}},
        f'commits/{SHA}/pulls': pulls, due.RUNS: {'workflow_runs': list(runs)}}
    return lambda path: pages.get(path, jobs)


class ReadTests(unittest.TestCase):
    def test_merge_time_and_read_errors(self):
        pull = [{'merge_commit_sha': SHA, 'merged_at': '2026-01-01T00:30:00Z'}]
        self.assertEqual(due.main_state(reader(pull))[1] - due.main_state(reader([]))[1], 1800)
        with self.assertRaises(due.StateError):
            due.main_state(reader('x'))
        with self.assertRaises(due.StateError):
            due.full_runs(reader([], runs=[{'status': None}]))

    def test_newest_finished_run_decides_and_needs_all_jobs(self):
        run = {'id': 1, 'status': 'completed', 'conclusion': 'success', 'head_sha': SHA}
        failed = dict(run, id=2, conclusion='failure')
        self.assertEqual(due.sha_result(reader([]), [run], SHA), 'PASS')
        self.assertEqual(due.sha_result(reader([]), [failed, run], SHA), 'FAIL')
        self.assertIsNone(due.sha_result(reader([]), [run], SHA, skip_id=1))
        self.assertIsNone(due.sha_result(reader([], {'jobs': []}), [run], SHA))
