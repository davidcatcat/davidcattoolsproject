import unittest

from store import latest_entry
from target import report_latest


class LatestReportTests(unittest.TestCase):
    def test_reports_last_entry(self):
        history = [{"value": "queued"}, {"value": "running"}, {"value": "done"}]
        self.assertEqual(report_latest(history), {"value": "done", "present": True})

    def test_reports_single_entry(self):
        self.assertEqual(report_latest([{"value": 7}]), {"value": 7, "present": True})

    def test_empty_history(self):
        self.assertEqual(report_latest([]), {"value": None, "present": False})

    def test_none_is_a_present_value(self):
        self.assertEqual(report_latest([{"value": 1}, {"value": None}]),
                         {"value": None, "present": True})

    def test_store_reads_latest_entry(self):
        entries = [{"value": 1}, {"value": 2}]
        self.assertEqual(latest_entry(entries), {"value": 2})
