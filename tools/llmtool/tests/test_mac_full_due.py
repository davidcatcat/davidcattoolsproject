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

    def test_e_same_sha_already_passed_is_not_rerun(self):
        self.assertEqual(self.sweep(T0 + 1800), due.DISPATCHED)
        self.github.finish()
        for minute in (35, 60, 600):
            self.assertEqual(self.sweep(T0 + minute * 60), due.VERIFIED)
        self.assertEqual(len(self.gh.calls), 1)

    def test_e_success_without_full_shards_is_not_trusted(self):
        self.assertEqual(self.sweep(T0 + 1800), due.DISPATCHED)
        self.github.finish(jobs=[dict(job, conclusion='skipped') for job in OK_JOBS])
        self.assertEqual(self.sweep(T0 + 2100), due.DISPATCHED)

    def test_f_failed_full_keeps_fail_raises_alert_and_never_passes(self):
        self.assertEqual(self.sweep(T0 + 1800), due.DISPATCHED)
        self.github.finish('failure')
        code, detail = due.request_full(self.gh, '/repo', self.github, now=T0 + 2100)
        self.assertEqual(code, due.FULL_FAILED)
        self.assertEqual(detail['alert']['conclusion'], 'failure')
        self.assertEqual(len(self.gh.calls), 1)
        self.github.merge('f' * 40, T0 + 2200)
        code, detail = due.request_full(self.gh, '/repo', self.github, now=T0 + 4000)
        self.assertEqual((code, detail['alert']['sha']), (due.DISPATCHED, 'a' * 40))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(due.run_ci(None, gh_runner=self.gh, github_get=self.github,
                                        root='/repo', now=T0 + 4100), 1)
        self.assertEqual(json.loads(out.getvalue())['code'], due.ACTIVE_RUN)

    def test_j_paused_sweep_resumes_from_github_state(self):
        self.github.merge('e' * 40, T0)
        self.assertEqual(self.sweep(T0 + 6 * 3600), due.DISPATCHED)
        self.assertEqual(self.github.runs[0]['head_sha'], 'e' * 40)

    def test_k_overlapping_sweep_run_is_seen_before_dispatch(self):
        reads = []

        def other_sweep():
            reads.append(1)
            if len(reads) == 2:
                FakeGh(self.github)('/repo', [])
        self.github.on_runs = other_sweep
        self.assertEqual(self.sweep(T0 + 1800), due.ACTIVE_RUN)
        self.assertEqual(self.gh.calls, [])
        self.assertEqual(len(self.github.runs), 1)

    def test_l_merge_train_requests_full_at_once_without_duplicate(self):
        self.github.merge('1' * 40, T0)
        self.assertEqual(due.request_train_full(self.gh, '/repo', self.github), due.DISPATCHED)
        self.assertEqual(due.request_train_full(self.gh, '/repo', self.github), due.ACTIVE_RUN)
        self.github.finish()
        self.assertEqual(due.request_train_full(self.gh, '/repo', self.github), due.VERIFIED)
        self.assertEqual(len(self.gh.calls), 1)

    def test_read_or_dispatch_failure_is_an_error_never_queued_or_passed(self):
        for prefix in ('branches/', 'commits/', 'actions/workflows/'):
            with self.subTest(prefix=prefix):
                self.github.fail = prefix
                self.assertEqual(self.sweep(T0 + 1800), due.UNAVAILABLE)
        self.github.fail = None
        self.assertEqual(self.sweep(T0 + 1800), due.DISPATCHED)
        self.github.finish()
        self.github.fail = 'actions/runs/'
        self.assertEqual(self.sweep(T0 + 2100), due.UNAVAILABLE)
        self.github.fail = None
        self.github.merge('2' * 40, T0)
        failing = FakeGh(self.github, returncode=1)
        self.assertEqual(due.request_full(failing, '/repo', self.github, now=T0 + 1800)[0],
                         due.DISPATCH_FAILED)
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            self.assertEqual(due.run_ci(None, gh_runner=failing, github_get=self.github,
                                        root='/repo', now=T0 + 1800), 2)
        self.assertIn(due.DISPATCH_FAILED, err.getvalue())

    def test_dispatch_argv_targets_main(self):
        self.sweep(T0 + 1800)
        self.assertEqual(self.gh.calls, [['workflow', 'run', 'llmtool-mac-ci.yml', '--ref', 'main']])


if __name__ == '__main__':
    unittest.main()
