import unittest
import target


class LadderTest(unittest.TestCase):
    def test_first_middle_last_and_duplicates(self):
        self.assertEqual(target.insert_before([2, 3, 4], 2, 1), [1, 2, 3, 4])
        self.assertEqual(target.insert_before([2, 3, 4], 3, 1), [2, 1, 3, 4])
        self.assertEqual(target.insert_before([2, 3, 4], 4, 1), [2, 3, 1, 4])
        self.assertEqual(target.insert_before([2, 3, 2], 2, 1), [1, 2, 3, 2])

    def test_missing_marker_appends_without_mutation(self):
        source = [1, 2]
        self.assertEqual(target.insert_before(source, 8, 3), [1, 2, 3])
        self.assertEqual(source, [1, 2])
