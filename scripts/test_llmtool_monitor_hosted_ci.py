"""Hosted CI in the monitor: runs and jobs are read without a self-hosted
runner, GitHub-hosted runner names are kept, and active llmtool CI runs show
as office rows that never call an unfinished, unread or unknown result 成功.

Synthetic payloads and a fake gh runner only; no network."""
import json
import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llmtool_monitor as monitor  # noqa: E402

REPO = "example-owner/example-repo"
STARTED = "2026-11-09T10:00:00Z"
NOW = monitor._github_time(STARTED) + 600   # ten minutes after the runs started
RUNNERS_PATH = f"repos/{REPO}/actions/runners?per_page=100"
RUNS_PATH = f"repos/{REPO}/actions/runs?per_page=30"
NOT_GOOD = ("待命", "閒置", "完成", "成功")


def jobs_path(run_id: int) -> str:
    return f"repos/{REPO}/actions/runs/{run_id}/jobs"


RUNS = {"workflow_runs": [
    {"id": 501, "name": "llmtool Linux CI", "status": "in_progress", "head_sha": "abc1234",
     "head_branch": "feature", "pull_requests": [{"number": 42}],
     "run_started_at": STARTED, "updated_at": STARTED},
    {"id": 502, "name": "llmtool Mac CI", "status": "queued", "head_sha": "def5678",
     "head_branch": "main", "pull_requests": [],
     "run_started_at": STARTED, "updated_at": STARTED},
    {"id": 503, "name": "some other workflow", "status": "in_progress", "head_sha": "abc1234",
     "head_branch": "feature", "pull_requests": [],
     "run_started_at": STARTED, "updated_at": STARTED},
    {"id": 400, "name": "llmtool Linux CI", "status": "completed", "conclusion": "cancelled",
     "head_sha": "0a1b2c3", "head_branch": "main", "pull_requests": [],
     "run_started_at": STARTED, "updated_at": STARTED},
]}
JOBS = {
    501: {"jobs": [
        {"name": "check (Linux 3.14)", "status": "in_progress", "runner_name": "GitHub Actions 7",
         "started_at": STARTED, "labels": ["ubuntu-24.04"]},
        {"name": "check (docs)", "status": "in_progress", "runner_name": None,
         "started_at": STARTED, "labels": ["ubuntu-24.04"]},
        {"name": "lint", "status": "queued", "labels": ["ubuntu-24.04"]},
    ]},
    502: {"jobs": []},
    503: {"jobs": [{"name": "build", "status": "in_progress", "runner_name": "GitHub Actions 9"}]},
    400: {"jobs": [
        {"name": "check (Linux 3.14)", "status": "completed", "conclusion": "cancelled",
         "runner_name": "GitHub Actions 3", "completed_at": STARTED},
    ]},
}


class FakeGh:
    """Stands in for `gh api -X GET <path>`: answers from a dict, else HTTP 404."""

    def __init__(self, answers: dict) -> None:
        self.answers = answers
        self.paths: list[str] = []

    def __call__(self, argv, **_kwargs):
        path = argv[-1]
        self.paths.append(path)
        if path not in self.answers:
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"HTTP 404")
        return SimpleNamespace(returncode=0, stdout=json.dumps(self.answers[path]).encode(),
                               stderr=b"")


def answers(runs=RUNS, runners=()) -> dict:
    found = {RUNNERS_PATH: {"total_count": len(runners), "runners": list(runners)}, RUNS_PATH: runs}
    found.update({jobs_path(run_id): payload for run_id, payload in JOBS.items()})
    return found


def refreshed(fake: FakeGh, source=None, hosted=True):
    """refresh_ci() once; a new source has want_hosted_ci set as the app sets it."""
    if source is None:
        source = monitor.GitHubSource(REPO, runner=fake, clock=lambda: NOW)
        source.want_hosted_ci = hosted
    with mock.patch.object(monitor.shutil, "which", return_value="/usr/bin/gh"):
        source.refresh_ci()
    return source


def view(ci_at=NOW, **fields) -> monitor.CiView:
    return monitor.CiView([], ci_at, ci_at=ci_at, **fields)


def hosted_rows() -> list:
    source = refreshed(FakeGh(answers()))
    return monitor.hosted_ci_rows(source.ci_runs, source.ci_view, NOW)


class ReadEveryRefreshTest(unittest.TestCase):
    def test_runs_and_jobs_read_without_want_ci_or_runners(self):
        fake = FakeGh(answers())
        source = refreshed(fake)
        self.assertTrue(source.want_hosted_ci)
        self.assertFalse(source.want_ci)
        self.assertEqual(source.ci_view.runners, [])
        self.assertIn(RUNS_PATH, fake.paths)
        self.assertIn(jobs_path(501), fake.paths)
        self.assertIn(jobs_path(502), fake.paths)
        self.assertIsNotNone(source.ci_runs)
        self.assertEqual(source.ci_view.ci_at, NOW)
        self.assertEqual(source.ci_view.ci_error, "")
        self.assertEqual(set(source.ci_view.active), {501, 502, 503})

    def test_failed_read_is_unknown_not_the_older_answer(self):
        good = refreshed(FakeGh(answers()))
        self.assertIsNotNone(good.ci_runs)
        broken = answers()
        del broken[RUNS_PATH]
        good.runner = FakeGh(broken)
        refreshed(good.runner, good)
        self.assertIsNone(good.ci_runs)
        self.assertIsNone(good.ci_view.active)
        self.assertIsNone(good.ci_view.last_done)
        self.assertTrue(good.ci_view.ci_error)
        rows = monitor.hosted_ci_rows(good.ci_runs, good.ci_view, NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].state, monitor.UNCHECKED)

    def test_direct_constructor_reads_no_runs_without_the_flag(self):
        fake = FakeGh(answers())
        source = refreshed(fake, hosted=False)
        self.assertFalse(source.want_hosted_ci)
        self.assertEqual(fake.paths, [RUNNERS_PATH])
        self.assertIsNone(source.ci_runs)   # not read: unknown, not "no runs"
        self.assertIsNone(source.ci_view.active)
        self.assertEqual(source.ci_view.ci_at, 0.0)
        self.assertEqual(source.ci_view.ci_error, "")

    def test_registered_runner_still_reads_runs_without_the_flag(self):
        runner = {"name": "ec2-self-runner", "os": "Linux", "status": "online",
                  "busy": False, "labels": [{"name": "self-hosted"}]}
        fake = FakeGh(answers(runners=[runner]))
        source = refreshed(fake, hosted=False)
        self.assertIn(RUNS_PATH, fake.paths)
        self.assertIn(jobs_path(501), fake.paths)
        self.assertIsNotNone(source.ci_runs)

    def test_app_poll_turns_hosted_ci_on(self):
        source = monitor.GitHubSource(REPO, runner=FakeGh(answers()), clock=lambda: NOW)
        self.assertFalse(source.want_hosted_ci)   # off by default
        mon = monitor.Monitor()
        mon.github = source
        mon.polls = 1   # past the first-poll summary line
        mon.disk_due = mon.pm_board_due = mon.office_due = math.inf   # no local scans
        snapshot = SimpleNamespace(processes=[], lingering=[], hosts=frozenset(),
                                   watched_groups={})
        with mock.patch.object(monitor, "collect", return_value=snapshot), \
                mock.patch.object(monitor, "scan_tasks", return_value=[]), \
                mock.patch.object(monitor, "track_one_shot", return_value=({}, [], [])):
            mon.poll()
        self.assertTrue(source.want_hosted_ci)
        self.assertFalse(source.want_ci)   # no self-hosted runner on this Mac
        fake = FakeGh(answers())
        source.runner = fake
        refreshed(fake, source)
        self.assertIn(RUNS_PATH, fake.paths)
        self.assertIn(jobs_path(501), fake.paths)
        self.assertEqual(len(monitor.hosted_ci_rows(source.ci_runs, source.ci_view, NOW)), 2)


class HostedRunnerNameTest(unittest.TestCase):
    def test_runner_name_with_spaces_is_kept(self):
        counts: dict = {}
        runs = monitor.ci_runner_names([("in_progress", "llmtool Linux CI（PR #42 @abc1234）",
                                         None, None, 501)],
                                       lambda run_id: JOBS[run_id], [], counts)
        runners = [entry[3] for entry in runs if entry[0] == "in_progress"]
        self.assertIn("GitHub Actions 7", runners)
        finished = monitor.ci_finished_jobs(JOBS[400], "llmtool Linux CI（main @0a1b2c3）")
        self.assertEqual([job[0] for job in finished], ["GitHub Actions 3"])

    def test_runner_name_is_bounded_and_cleaned(self):
        name = monitor.ci_job_runner({"runner_name": "GitHub\x07 Actions\x1b 5" + "x" * 100})
        self.assertLessEqual(len(name), 64)
        self.assertTrue(name.startswith("GitHub Actions 5"))
        self.assertNotIn("\x07", name)
        self.assertNotIn("\x1b", name)

    def test_in_progress_job_without_runner_still_runs(self):
        counts: dict = {}
        runs = monitor.ci_runner_names([("in_progress", "llmtool Linux CI（PR #42 @abc1234）",
                                         None, None, 501)],
                                       lambda run_id: JOBS[run_id], [], counts)
        texts = [entry[1] for entry in runs if entry[0] == "in_progress"]
        self.assertTrue(any(text.endswith("check (docs)") for text in texts))
        no_runner = [entry for entry in runs if entry[1].endswith("check (docs)")]
        self.assertEqual(no_runner[0][0], "in_progress")
        self.assertIsNone(no_runner[0][3])
        self.assertEqual(counts[501], (("check (Linux 3.14)", "check (docs)"), 1))


class HostedRowsTest(unittest.TestCase):
    def test_running_and_queued_rows_with_job_counts(self):
        rows = hosted_rows()
        self.assertEqual(len(rows), 2)   # the non-llmtool workflow gets no row
        linux = next(row for row in rows if row.key == "ci-run:501")
        self.assertEqual(linux.state, monitor.WORKING)
        self.assertIn("llmtool Linux CI", linux.name)
        self.assertIn("PR #42", linux.name)
        self.assertIn("@abc1234", linux.name)
        self.assertIn("執行中 2 個", linux.detail)
        self.assertIn("check (Linux 3.14)", linux.detail)
        self.assertIn("check (docs)", linux.detail)
        self.assertIn("排隊 1 個", linux.detail)
        self.assertIn("已過 10 分鐘", linux.detail)
        self.assertEqual(linux.age, 600)

    def test_run_with_no_jobs_shows_as_queued(self):
        mac = next(row for row in hosted_rows() if row.key == "ci-run:502")
        self.assertEqual(mac.state, monitor.QUEUED)
        self.assertNotIn("執行中", mac.detail)
        self.assertIn("@def5678", mac.name)

    def test_in_progress_run_without_visible_job_is_queued(self):
        ci = [("in_progress", "llmtool Linux CI（PR #42 @abc1234）", None, None, 601),
              ("in_progress", "llmtool Mac CI（main @def5678）", None, None, 602)]
        active = {601: ("llmtool Linux CI", "llmtool Linux CI（PR #42 @abc1234）", None, (), 0),
                  602: ("llmtool Mac CI", "llmtool Mac CI（main @def5678）", None, None, None)}
        rows = monitor.hosted_ci_rows(ci, view(active=active, last_done={}), NOW)
        self.assertEqual([row.state for row in rows], [monitor.QUEUED, monitor.QUEUED])
        self.assertIn("排隊工作數未確認", rows[1].detail)
        for row in rows:
            for word in NOT_GOOD + ("執行中",):
                self.assertNotIn(word, row.detail)

    def test_unread_or_stale_data_gives_one_unchecked_row(self):
        active = {501: ("llmtool Linux CI", "llmtool Linux CI（PR #42 @abc1234）", None, ("a",), 0)}
        ci = [("in_progress", "llmtool Linux CI（PR #42 @abc1234）：a", None, None, 501)]
        cases = [
            (None, None),
            (None, view(active=None, ci_error="逾時")),
            (ci, view(ci_at=0.0, active=active)),
            (ci, view(ci_at=NOW - monitor.GITHUB_STALE - 1, active=active)),
        ]
        for runs, state in cases:
            with self.subTest(runs=runs, view=state):
                rows = monitor.hosted_ci_rows(runs, state, NOW)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0].state, monitor.UNCHECKED)
                self.assertIn("未確認", rows[0].detail)
                for word in NOT_GOOD:
                    self.assertNotIn(word, rows[0].name + rows[0].detail)
        rows = monitor.hosted_ci_rows(None, view(active=None, ci_error="逾時"), NOW)
        self.assertIn("逾時", rows[0].detail)
        rows = monitor.hosted_ci_rows(ci, view(ci_at=NOW - monitor.GITHUB_STALE - 1, active=active), NOW)
        self.assertIn("沒更新", rows[0].detail)


class ConclusionWordsTest(unittest.TestCase):
    OTHERS = ("cancelled", "timed_out", "skipped", "failure", "neutral",
              "action_required", "stale", "", None, ["success"], 1)

    def test_only_success_reads_success(self):
        self.assertEqual(monitor.ci_result_word("success"), "成功")
        for conclusion in self.OTHERS:
            with self.subTest(conclusion=conclusion):
                self.assertNotEqual(monitor.ci_result_word(conclusion), "成功")
        self.assertEqual(monitor.ci_result_word("cancelled"), "取消")
        self.assertEqual(monitor.ci_result_word("timed_out"), "逾時")
        self.assertEqual(monitor.ci_result_word("skipped"), "略過")
        self.assertEqual(monitor.ci_result_word("neutral"), "結果不明")
        self.assertEqual(monitor.ci_result_word(None), "結果不明")

    def test_finished_jobs_and_last_results_never_read_success(self):
        for conclusion in self.OTHERS:
            with self.subTest(conclusion=conclusion):
                job = {"name": "check", "status": "completed", "conclusion": conclusion,
                       "runner_name": "GitHub Actions 3", "completed_at": STARTED}
                finished = monitor.ci_finished_jobs({"jobs": [job]}, "llmtool Linux CI")
                self.assertNotEqual(finished[0][2], "成功")
                run = {"id": 7, "name": "llmtool Linux CI", "status": "completed",
                       "conclusion": conclusion, "updated_at": STARTED}
                last = monitor.ci_last_results({"workflow_runs": [run]})
                self.assertNotEqual(last["llmtool Linux CI"][0], "成功")

    def test_last_result_on_row_keeps_its_word(self):
        linux = next(row for row in hosted_rows() if row.key == "ci-run:501")
        self.assertIn("上一次結果：取消", linux.detail)
        self.assertNotIn("成功", linux.detail)


if __name__ == "__main__":
    unittest.main()
