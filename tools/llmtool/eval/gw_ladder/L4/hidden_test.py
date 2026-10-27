import unittest
import target


class LadderTest(unittest.TestCase):
    def test_summary_across_bands(self):
        self.assertEqual(
            target.summarize_scores([0, 49, 50, 79, 80, 100]),
            {"count": 6, "average": 59.666666666666664,
             "bands": {"low": 2, "mid": 2, "high": 2}},
        )

    def test_empty_and_single_value(self):
        self.assertEqual(
            target.summarize_scores([]),
            {"count": 0, "average": 0.0,
             "bands": {"low": 0, "mid": 0, "high": 0}},
        )
        self.assertEqual(
            target.summarize_scores([80]),
            {"count": 1, "average": 80.0,
             "bands": {"low": 0, "mid": 0, "high": 1}},
        )
