import unittest
import target


class LadderTest(unittest.TestCase):
    def test_default_page_size(self):
        self.assertEqual(target.DEFAULT_PAGE_SIZE, 20)
