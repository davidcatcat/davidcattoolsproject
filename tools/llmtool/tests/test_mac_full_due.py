"""Owner #1485 acceptance cases for the main FULL Mac trigger (synthetic GitHub)."""

from __future__ import annotations

import contextlib
import io
import json
import time
import types
import unittest

from llmtool import mac_full_due as due

T0 = 1_700_000_000
OK_JOBS = [{'name': name, 'conclusion': 'success'} for name in due.JOBS]


def stamp(epoch):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(epoch))


class FakeGitHub:
    """main, its merge time and the FULL runs, as GitHub would report them."""

    def __init__(self):
        self.main, self.merged_at, self.runs, self.jobs = 'a' * 40, T0, [], {}
        self.fail, self.reads, self.on_runs = None, [], None

    def merge(self, sha, at):
        self.main, self.merged_at = sha, at

    def finish(self, conclusion='success', jobs=None):
        run = next(r for r in reversed(self.runs) if r['status'] != 'completed')
        run.update(status='completed', conclusion=conclusion)
        self.jobs[run['id']] = OK_JOBS if jobs is None else jobs

    def __call__(self, path):
        self.reads.append(path)
        if self.fail and path.startswith(self.fail):
            raise RuntimeError('read failed')
        if path == 'branches/main':
            return {'commit': {'sha': self.main, 'commit': {'committer': {'date': stamp(0)}}}}
        if path == f'commits/{self.main}/pulls':
            return [{'merge_commit_sha': self.main, 'merged_at': stamp(self.merged_at)}]
        if path == due.RUNS:
            if self.on_runs:
                self.on_runs()
            return {'workflow_runs': list(reversed(self.runs))}
        run_id = int(path.split('/')[2])
        return {'jobs': self.jobs[run_id]}


class FakeGh:
    """gh workflow run: GitHub queues a run for main's current SHA."""

    def __init__(self, github, returncode=0):
        self.github, self.returncode, self.calls = github, returncode, []

    def __call__(self, root, args):
        self.calls.append(args)
        if self.returncode == 0:
            self.github.runs.append({'id': len(self.github.runs) + 1, 'status': 'queued',
                                     'head_sha': self.github.main, 'html_url': 'u'})
        return types.SimpleNamespace(returncode=self.returncode)


class MacFullDueCases(unittest.TestCase):
    def setUp(self):
        self.github = FakeGitHub()
        self.gh = FakeGh(self.github)

    def sweep(self, now):
        return due.request_full(self.gh, '/repo', self.github, now=now)[0]

    def test_a_ten_merges_in_30_minutes_give_one_full_after_quiet(self):
        for minute in range(0, 30, 3):
            self.github.merge(f'{minute:040x}', T0 + minute * 60)
            self.assertEqual(self.sweep(T0 + minute * 60 + 60), due.QUIET)
        self.assertEqual(self.gh.calls, [])
        self.assertEqual(self.sweep(T0 + 27 * 60 + 1800), due.DISPATCHED)
        self.assertEqual(self.sweep(T0 + 27 * 60 + 2100), due.ACTIVE_RUN)
        self.assertEqual(len(self.gh.calls), 1)

    def test_b_merges_40_minutes_apart_give_one_full_each(self):
        self.github.merge('b' * 40, T0)
        self.assertEqual(self.sweep(T0 + 1800), due.DISPATCHED)
        self.github.finish()
        self.github.merge('c' * 40, T0 + 2400)
        self.assertEqual(self.sweep(T0 + 2700), due.QUIET)
        self.assertEqual(self.sweep(T0 + 4200), due.DISPATCHED)
        self.assertEqual([r['head_sha'] for r in self.github.runs], ['b' * 40, 'c' * 40])

    def test_c_no_new_merge_still_triggers_pending_full(self):
        self.assertEqual(self.sweep(T0 + 600), due.QUIET)
        self.assertEqual(self.sweep(T0 + 1801), due.DISPATCHED)

    def test_d_main_moves_during_full_no_duplicate_and_latest_verified(self):
        self.assertEqual(self.sweep(T0 + 1800), due.DISPATCHED)
        self.github.runs[0]['status'] = 'in_progress'
        self.github.merge('d' * 40, T0 + 2000)
        self.assertEqual(self.sweep(T0 + 4000), due.ACTIVE_RUN)
        self.github.finish()
        self.assertEqual(self.sweep(T0 + 4100), due.DISPATCHED)
        self.assertEqual(self.github.runs[-1]['head_sha'], 'd' * 40)
        self.github.finish()
        self.assertEqual(self.sweep(T0 + 4400), due.VERIFIED)
        self.assertEqual(len(self.gh.calls), 2)
