"""#425 P0a: one deliberate, harmless failure to prove EC2 CI reports FAIL. Never merge."""

import unittest


class EC2SimulatedFailureTests(unittest.TestCase):
    def test_simulated_failure_is_reported(self):
        self.assertEqual('EC2_SIMULATED_FAILURE', 'expected to fail')


if __name__ == '__main__':
    unittest.main()
