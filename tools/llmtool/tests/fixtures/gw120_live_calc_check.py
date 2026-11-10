"""Unit tests for the clamp fixture function."""

import unittest

from gw120_live_calc import clamp


class ClampTests(unittest.TestCase):
    def test_below_low_returns_low(self):
        self.assertEqual(clamp(1, 5, 10), 5)

    def test_inside_range_returns_value(self):
        self.assertEqual(clamp(7, 5, 10), 7)

    def test_above_high_returns_high(self):
        self.assertEqual(clamp(12, 5, 10), 10)

    def test_low_greater_than_high_raises(self):
        with self.assertRaises(ValueError):
            clamp(5, 10, 5)

    def test_edges_return_value(self):
        self.assertEqual(clamp(5, 5, 10), 5)
        self.assertEqual(clamp(10, 5, 10), 10)


if __name__ == "__main__":
    unittest.main()
