"""Acceptance-only tests for #2438 interrupted-dispatch reassignment."""

import unittest


class ReassignAcceptance2438Test(unittest.TestCase):
    def test_sum_is_correct(self):
        self.assertEqual(2 + 2, 4)

    def test_planned_acceptance_failure(self):
        """planned failure for #2438 live acceptance; the repair changes the expected value to 4"""
        self.assertEqual(2 + 2, 5)


if __name__ == "__main__":
    unittest.main()
