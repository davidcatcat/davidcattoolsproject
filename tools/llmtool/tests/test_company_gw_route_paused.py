"""The shipped dispatch policy keeps company-gw paused and gw-agent allowed (#120)."""

import unittest
from unittest.mock import patch

from llmtool import worker_dispatch


class CompanyGwRoutePausedTests(unittest.TestCase):
    def setUp(self):
        repository = patch.object(worker_dispatch, "repository_config", return_value={
            "slug": "davidcatcat/davidcattoolsproject", "dispatch_issue": 289,
            "dispatch_evidence_pr": 283})
        repository.start()
        self.addCleanup(repository.stop)

    def test_company_gw_direct_route_is_paused(self):
        decision = worker_dispatch.evaluate('company-gw')
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, 'OWNER_PAUSED')

    def test_gw_agent_route_stays_allowed(self):
        self.assertTrue(worker_dispatch.evaluate('company-gw-agent').allowed)


if __name__ == '__main__':
    unittest.main()
