import unittest
import target


class LadderTest(unittest.TestCase):
    def test_inside_and_edges(self):
        self.assertEqual(target.clamp(5, 1, 9), 5)
        self.assertEqual(target.clamp(1, 1, 9), 1)
        self.assertEqual(target.clamp(9, 1, 9), 9)

    def test_outside(self):
        self.assertEqual(target.clamp(-3, 1, 9), 1)
        self.assertEqual(target.clamp(12, 1, 9), 9)
