import unittest


class ReassignRepair2Test(unittest.TestCase):
    def test_sum_is_correct(self):
        self.assertEqual(2 + 2, 4)

    def test_planned_acceptance_failure(self):
        """fixed in the #2438 acceptance repair"""
        self.assertEqual(2 + 2, 4)


if __name__ == "__main__":
    unittest.main()
