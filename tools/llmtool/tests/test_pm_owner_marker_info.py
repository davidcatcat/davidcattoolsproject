"""Unit tests for pm_owner_marker OWNER_INFORMATION_STATUSES (#1943)."""
import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools" / "llmtool" / "scripts" / "pm_owner_marker.py"
LLMTOOL_DIR = ROOT / "tools" / "llmtool"

try:
    from llmtool import github_identity
except ImportError:
    if str(LLMTOOL_DIR) not in sys.path:
        sys.path.insert(0, str(LLMTOOL_DIR))
    from llmtool import github_identity

_SPEC = importlib.util.spec_from_file_location("pm_owner_marker_under_test", SCRIPT)
marker = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(marker)


def make_comment(issue_number: int, body: str, created_at: str = "2026-11-15T01:00:00Z",
                 comment_id: int = 1, author_association: str = "MEMBER") -> dict:
    return {
        "id": comment_id,
        "issue_url": f"https://api.github.com/repos/davidcatcat/davidcattoolsproject/issues/{issue_number}",
        "author_association": author_association,
        "body": body,
        "created_at": created_at,
    }


class PMOwnerMarkerInfoTests(unittest.TestCase):
    def test_owner_information_statuses_constant(self):
        self.assertEqual(marker.OWNER_INFORMATION_STATUSES, frozenset({"NOTICE", "INFO", "TRACKING"}))

    def test_pm_notice_alone_opens_no_row(self):
        body = "<!-- activity: type=pm_progress actor=PM target=Owner status=NOTICE -->"
        comments = [make_comment(101, body)]
        latest = marker._waiting_markers(comments)
        self.assertEqual(latest, {})
        self.assertEqual(marker.open_rows_from_latest(latest, []), [])

    def test_waiting_owner_ask_followed_by_pm_notice_stays_open(self):
        c1 = make_comment(
            102,
            "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->",
            created_at="2026-11-15T01:00:00Z",
            comment_id=1,
        )
        c2 = make_comment(
            102,
            "<!-- activity: type=pm_progress actor=PM target=Owner status=NOTICE -->",
            created_at="2026-11-15T01:05:00Z",
            comment_id=2,
        )
        latest = marker._waiting_markers([c1, c2])
        self.assertIn("102", latest)
        self.assertEqual(latest["102"][2], "waiting_owner")
        self.assertEqual(latest["102"][3], "WAITING")
        self.assertEqual(marker.open_rows_from_latest(latest, []), ["102"])

    def test_info_and_tracking_behave_like_notice(self):
        for status in ("INFO", "TRACKING"):
            with self.subTest(status=status):
                body = f"<!-- activity: type=pm_progress actor=PM target=Owner status={status} -->"
                comments = [make_comment(103, body)]
                latest = marker._waiting_markers(comments)
                self.assertEqual(latest, {})
                self.assertEqual(marker.open_rows_from_latest(latest, []), [])

                c1 = make_comment(
                    103,
                    "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->",
                    created_at="2026-11-15T01:00:00Z",
                    comment_id=1,
                )
                c2 = make_comment(
                    103,
                    body,
                    created_at="2026-11-15T01:05:00Z",
                    comment_id=2,
                )
                latest_after = marker._waiting_markers([c1, c2])
                self.assertIn("103", latest_after)
                self.assertEqual(latest_after["103"][2], "waiting_owner")
                self.assertEqual(latest_after["103"][3], "WAITING")
                self.assertEqual(marker.open_rows_from_latest(latest_after, []), ["103"])

    def test_actor_owner_record_not_skipped_by_rule(self):
        c1 = make_comment(
            104,
            "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->",
            created_at="2026-11-15T01:00:00Z",
            comment_id=1,
        )
        c2 = make_comment(
            104,
            "<!-- activity: type=owner_response actor=Owner target=PM status=NOTICE -->",
            created_at="2026-11-15T01:05:00Z",
            comment_id=2,
            author_association="OWNER",
        )
        latest = marker._waiting_markers([c1, c2])
        self.assertEqual(latest, {})
        self.assertEqual(marker.open_rows_from_latest(latest, []), [])

    def test_answered_after_ask_still_settles(self):
        c1 = make_comment(
            105,
            "<!-- activity: type=waiting_owner actor=PM target=Owner status=WAITING -->",
            created_at="2026-11-15T01:00:00Z",
            comment_id=1,
        )
        c2 = make_comment(
            105,
            "<!-- activity: type=pm_progress actor=PM target=Owner status=ANSWERED -->",
            created_at="2026-11-15T01:05:00Z",
            comment_id=2,
        )
        latest = marker._waiting_markers([c1, c2])
        self.assertEqual(latest, {})
        self.assertEqual(marker.open_rows_from_latest(latest, []), [])


if __name__ == "__main__":
    unittest.main()
