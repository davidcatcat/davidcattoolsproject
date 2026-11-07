"""Synthetic #120 Phase 1 module. Review vehicle only; it never reaches main."""
import unittest


def scale(value):
    return value * 3


UNRELATED_CONSTANT = 7


class SyntheticPhase1Test(unittest.TestCase):
    def test_scale_multiplies_by_three(self):
        self.assertEqual(scale(2), 6)

    def test_unrelated_constant_is_unchanged(self):
        self.assertEqual(UNRELATED_CONSTANT, 7)


if __name__ == '__main__':
    unittest.main()
