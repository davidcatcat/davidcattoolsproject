from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock


MONITOR_PATH = Path(__file__).resolve().with_name("llmtool_monitor.py")
SPEC = importlib.util.spec_from_file_location("llmtool_monitor", MONITOR_PATH)
assert SPEC is not None and SPEC.loader is not None
monitor = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = monitor
SPEC.loader.exec_module(monitor)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _comment(
    cid: int,
    created_at: float,
    updated_at: float | None = None,
    body: str = "status update",
    issue: int = 10,
    login: str = "davidcatcat",
) -> dict:
    actual_updated = created_at if updated_at is None else updated_at
    return {
        "id": cid,
        "body": body,
        "created_at": _iso(created_at),
        "updated_at": _iso(actual_updated),
        "html_url": f"https://github.com/davidcatcat/davidcattoolsproject/issues/{issue}#issuecomment-{cid}",
        "issue_url": f"https://api.github.com/repos/davidcatcat/davidcattoolsproject/issues/{issue}",
        "user": {"login": login},
    }


def _noop_runner(*args, **kwargs):
    return subprocess.CompletedProcess(args[0] if args else [], 1, stdout="", stderr="")


class GitHubSourceIncrementalReadsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 1762660000.0
        self.repo = "davidcatcat/davidcattoolsproject"

    def _make_source(self):
        return monitor.GitHubSource(self.repo, runner=_noop_runner, clock=lambda: self.now)

    def test_first_refresh_is_full_read_and_next_uses_updated_since(self) -> None:
        source = self._make_source()
        calls: list[str] = []
        c1 = _comment(1, self.now - 600, updated_at=self.now - 500, body="first note")
        c2 = _comment(2, self.now - 300, updated_at=self.now - 100, body="second note")

        def fake_get(path: str):
            calls.append(path)
            if path.startswith(f"repos/{self.repo}/issues/comments?"):
                if "sort=created" in path:
                    return [c2, c1]
                return []
            if path.startswith(f"repos/{self.repo}/pulls?"):
                return []
            return []

        with mock.patch.object(source, "_get", side_effect=fake_get):
            source.refresh(force=True)
            first_comment_calls = [c for c in calls if "/issues/comments?" in c]
            self.assertEqual(len(first_comment_calls), 1)
            expected_full_since = _iso(self.now - source.window)
            self.assertEqual(
                first_comment_calls[0],
                f"repos/{self.repo}/issues/comments?since={expected_full_since}"
                "&sort=created&direction=desc&per_page=100&page=1",
            )

            calls.clear()
            self.now += 30.0
            source.refresh(force=True)
            second_comment_calls = [c for c in calls if "/issues/comments?" in c]
            self.assertEqual(len(second_comment_calls), 1)
            expected_delta_since = _iso((self.now - 30.0 - 100.0) - 120.0)
            self.assertEqual(
                second_comment_calls[0],
                f"repos/{self.repo}/issues/comments?since={expected_delta_since}"
                "&sort=updated&direction=asc&per_page=100&page=1",
            )

    def test_edited_comment_replaces_old_and_old_comments_drop_out(self) -> None:
        source = self._make_source()
        t0 = self.now
        expiring = _comment(1, t0 - source.window + 10, updated_at=t0 - source.window + 10, body="soon old")
        editable_v1 = _comment(2, t0 - 3600, updated_at=t0 - 3600, body="draft body")
        middle = _comment(3, t0 - 1800, updated_at=t0 - 1800, body="middle body")

        ancient_edited = _comment(99, t0 - source.window - 500, updated_at=t0 + 15, body="ancient edit")
        editable_v2 = _comment(2, t0 - 3600, updated_at=t0 + 20, body="revised body")
        newest = _comment(4, t0 + 25, updated_at=t0 + 25, body="newest body")

        def fake_get(path: str):
            if path.startswith(f"repos/{self.repo}/issues/comments?"):
                if "sort=created" in path:
                    return [middle, editable_v1, expiring]
                return [ancient_edited, editable_v2, newest]
            return []

        with (
            mock.patch.object(source, "_get", side_effect=fake_get),
            mock.patch.object(monitor, "github_events", wraps=monitor.github_events) as spy_events,
            mock.patch.object(monitor, "owner_pending", wraps=monitor.owner_pending) as spy_pending,
            mock.patch.object(monitor, "review_verdicts", wraps=monitor.review_verdicts) as spy_verdicts,
        ):
            source.refresh(force=True)
            self.assertEqual([c["id"] for c in spy_events.call_args.args[0]], [3, 2, 1])

            self.now = t0 + 30.0
            source.refresh(force=True)

            passed_comments = spy_events.call_args.args[0]
            self.assertEqual([c["id"] for c in passed_comments], [4, 3, 2])
            self.assertEqual(passed_comments[2]["body"], "revised body")
            self.assertEqual(spy_pending.call_args.args[0], passed_comments)
            self.assertEqual(spy_verdicts.call_args.args[0], passed_comments)

    def test_failed_comment_read_keeps_previous_and_forces_full_read(self) -> None:
        source = self._make_source()
        t0 = self.now
        c1 = _comment(1, t0 - 400, updated_at=t0 - 400, body="kept comment")
        uncommitted = _comment(2, t0 + 10, updated_at=t0 + 10, body="from failed read")
        c3 = _comment(3, t0 + 50, updated_at=t0 + 50, body="from recovery full read")

        mode = "initial"
        comment_Urls: list[str] = []

        def fake_get(path: str):
            if path.startswith(f"repos/{self.repo}/issues/comments?"):
                comment_Urls.append(path)
                page = path.rsplit("&page=", 1)[-1]
                if mode == "initial":
                    return [c1]
                if mode == "fail_page2":
                    if page == "1":
                        return [uncommitted] * monitor.GITHUB_PAGE
                    raise RuntimeError("simulated comment read failure")
                if mode == "fail_non_list":
                    return {"message": "not a list"}
                return [c3, c1]
            return []

        with mock.patch.object(source, "_get", side_effect=fake_get):
            source.refresh(force=True)
            self.assertFalse(source.read_failed)
            self.assertEqual([e.summary for e in source.events], ["kept comment"])

            # Fail on page 2 after page 1 returned data: nothing from page 1 may become current.
            mode = "fail_page2"
            self.now = t0 + 30.0
            comment_Urls.clear()
            source.refresh(force=True)
            self.assertTrue(source.read_failed)
            self.assertEqual([u.rsplit("&page=", 1)[-1] for u in comment_Urls], ["1", "2"])
            self.assertTrue(all("sort=updated" in u for u in comment_Urls))
            self.assertEqual([c["id"] for c in source._comments], [1])
            self.assertEqual([e.summary for e in source.events], ["kept comment"])

            # Next refresh must be a full read; test non-list failure as well.
            mode = "fail_non_list"
            self.now = t0 + 60.0
            comment_Urls.clear()
            source.refresh(force=True)
            self.assertTrue(source.read_failed)
            self.assertEqual(len(comment_Urls), 1)
            self.assertIn("sort=created&direction=desc", comment_Urls[0])
            self.assertEqual([c["id"] for c in source._comments], [1])
            self.assertEqual([e.summary for e in source.events], ["kept comment"])

            # Recovery refresh must still do a full read.
            mode = "recover"
            self.now = t0 + 90.0
            comment_Urls.clear()
            source.refresh(force=True)
            self.assertFalse(source.read_failed)
            self.assertEqual(len(comment_Urls), 1)
            self.assertIn("sort=created&direction=desc", comment_Urls[0])
            self.assertEqual([c["id"] for c in source._comments], [3, 1])

    def test_non_list_on_page_2_of_incremental_read(self) -> None:
        source = self._make_source()
        t0 = self.now
        c1 = _comment(1, t0 - 400, updated_at=t0 - 400, body="kept comment")
        uncommitted = _comment(2, t0 + 10, updated_at=t0 + 10, body="from failed read")
        c3 = _comment(3, t0 + 50, updated_at=t0 + 50, body="from recovery full read")

        mode = "initial"
        comment_urls: list[str] = []

        def fake_get(path: str):
            if path.startswith(f"repos/{self.repo}/issues/comments?"):
                comment_urls.append(path)
                page = path.rsplit("&page=", 1)[-1]
                if mode == "initial":
                    return [c1]
                if mode == "fail_non_list_page2":
                    if page == "1":
                        return [uncommitted] * monitor.GITHUB_PAGE
                    return {"message": "not a list"}
                return [c3, c1]
            return []

        with mock.patch.object(source, "_get", side_effect=fake_get):
            source.refresh(force=True)
            self.assertFalse(source.read_failed)
            self.assertEqual([c["id"] for c in source._comments], [1])
            self.assertEqual([e.summary for e in source.events], ["kept comment"])

            mode = "fail_non_list_page2"
            self.now = t0 + 30.0
            comment_urls.clear()
            source.refresh(force=True)
            self.assertTrue(source.read_failed)
            self.assertEqual([u.rsplit("&page=", 1)[-1] for u in comment_urls], ["1", "2"])
            self.assertTrue(all("sort=updated" in u for u in comment_urls))
            self.assertEqual([c["id"] for c in source._comments], [1])
            self.assertEqual([e.summary for e in source.events], ["kept comment"])

            mode = "recover"
            self.now = t0 + 60.0
            comment_urls.clear()
            source.refresh(force=True)
            self.assertFalse(source.read_failed)
            self.assertEqual(len(comment_urls), 1)
            self.assertIn("sort=created&direction=desc", comment_urls[0])
            self.assertEqual([c["id"] for c in source._comments], [3, 1])

    def test_thirty_minute_full_reread_and_page_limit_forces_full_read(self) -> None:
        source = self._make_source()
        t0 = self.now
        c1 = _comment(1, t0 - 200, updated_at=t0 - 100, body="initial")
        comment_queries: list[str] = []
        hit_page_limit = False

        def fake_get(path: str):
            if path.startswith(f"repos/{self.repo}/issues/comments?"):
                comment_queries.append(path)
                if hit_page_limit and "sort=updated" in path:
                    page_num = int(path.rsplit("&page=", 1)[-1])
                    base = (page_num - 1) * monitor.GITHUB_PAGE
                    return [
                        _comment(100 + base + i, t0 - 50 + i, updated_at=t0 + i)
                        for i in range(monitor.GITHUB_PAGE)
                    ]
                return [c1]
            return []

        with mock.patch.object(source, "_get", side_effect=fake_get):
            # Initial full read at t0.
            source.refresh(force=True)
            self.assertIn("sort=created&direction=desc", comment_queries[-1])

            # At t0 + 1799s (just under 30 minutes), still incremental.
            self.now = t0 + 1799.0
            comment_queries.clear()
            source.refresh(force=True)
            self.assertEqual(len(comment_queries), 1)
            self.assertIn("sort=updated&direction=asc", comment_queries[0])

            # At t0 + 1800s (30 minutes after last full read at t0), full re-read.
            self.now = t0 + 1800.0
            comment_queries.clear()
            source.refresh(force=True)
            self.assertEqual(len(comment_queries), 1)
            self.assertIn("sort=created&direction=desc", comment_queries[0])

            # If an incremental read hits GITHUB_PAGES full pages, the next refresh does a full read.
            hit_page_limit = True
            self.now = t0 + 1830.0
            comment_queries.clear()
            source.refresh(force=True)
            self.assertEqual(len(comment_queries), monitor.GITHUB_PAGES)
            self.assertTrue(all("sort=updated&direction=asc" in q for q in comment_queries))

            hit_page_limit = False
            self.now = t0 + 1860.0
            comment_queries.clear()
            source.refresh(force=True)
            self.assertEqual(len(comment_queries), 1)
            self.assertIn("sort=created&direction=desc", comment_queries[0])

    def test_merged_result_equals_full_read(self) -> None:
        t0 = self.now
        c_drop = _comment(1, t0 - 12 * 3600 + 20, updated_at=t0 - 12 * 3600 + 20, body="drops out")
        c_keep = _comment(2, t0 - 7200, updated_at=t0 - 7200, body="stays unchanged")
        c_edit_v1 = _comment(3, t0 - 3600, updated_at=t0 - 3600, body="before edit")
        c_edit_v2 = _comment(3, t0 - 3600, updated_at=t0 + 40, body="after edit")
        c_new_1 = _comment(4, t0 + 20, updated_at=t0 + 20, body="added first")
        c_new_2 = _comment(5, t0 + 50, updated_at=t0 + 50, body="added second")

        inc_source = self._make_source()
        step = 0

        def inc_get(path: str):
            if path.startswith(f"repos/{self.repo}/issues/comments?"):
                if step == 0:
                    return [c_edit_v1, c_keep, c_drop]
                if step == 1:
                    return [c_edit_v1, c_new_1]
                return [c_new_1, c_edit_v2, c_new_2]
            return []

        with (
            mock.patch.object(inc_source, "_get", side_effect=inc_get),
            mock.patch.object(monitor, "github_events", wraps=monitor.github_events) as inc_events_spy,
            mock.patch.object(monitor, "owner_pending", wraps=monitor.owner_pending) as inc_pending_spy,
            mock.patch.object(monitor, "review_verdicts", wraps=monitor.review_verdicts) as inc_verdicts_spy,
        ):
            inc_source.refresh(force=True)
            step = 1
            self.now = t0 + 30.0
            inc_source.refresh(force=True)
            step = 2
            self.now = t0 + 60.0
            inc_source.refresh(force=True)
            inc_comments = inc_events_spy.call_args.args[0]
            inc_pending_in = inc_pending_spy.call_args.args[0]
            inc_verdicts_in = inc_verdicts_spy.call_args.args[0]

        full_source = self._make_source()

        def full_get(path: str):
            if path.startswith(f"repos/{self.repo}/issues/comments?"):
                return [c_new_2, c_new_1, c_edit_v2, c_keep]
            return []

        with (
            mock.patch.object(full_source, "_get", side_effect=full_get),
            mock.patch.object(monitor, "github_events", wraps=monitor.github_events) as full_events_spy,
            mock.patch.object(monitor, "owner_pending", wraps=monitor.owner_pending) as full_pending_spy,
            mock.patch.object(monitor, "review_verdicts", wraps=monitor.review_verdicts) as full_verdicts_spy,
        ):
            full_source.refresh(force=True)
            full_comments = full_events_spy.call_args.args[0]
            full_pending_in = full_pending_spy.call_args.args[0]
            full_verdicts_in = full_verdicts_spy.call_args.args[0]

        self.assertEqual(inc_comments, full_comments)
        self.assertEqual(inc_pending_in, full_pending_in)
        self.assertEqual(inc_verdicts_in, full_verdicts_in)
        self.assertEqual(inc_source.events, full_source.events)
        self.assertEqual(inc_source.pending, full_source.pending)
        self.assertEqual(inc_source.verdicts, full_source.verdicts)


if __name__ == "__main__":
    unittest.main()
