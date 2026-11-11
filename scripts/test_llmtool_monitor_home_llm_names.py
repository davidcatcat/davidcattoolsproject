import os
import sys
import unittest

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import llmtool_monitor as mon


class HomeLlmNameTests(unittest.TestCase):
    def test_activity_feed_names_the_home_llm(self):
        self.assertEqual(mon.MARKER_NAMES["CompanyGW"], "home LLM gw-agent")

    def test_marker_token_stays_company_gw(self):
        self.assertIn("CompanyGW", mon.MARKER_ACTOR_TARGETS["test_result"][0])


if __name__ == "__main__":
    unittest.main()
