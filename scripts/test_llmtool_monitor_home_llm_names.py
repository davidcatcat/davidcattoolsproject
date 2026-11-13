import inspect
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

    def test_roster_names_come_from_the_name_constants(self):
        # #120 A3: one place holds the roster names of the two home LLM rows.
        self.assertIs(mon.WORKER_ROSTER[2][0], mon.HOME_GW)
        self.assertIs(mon.WORKER_ROSTER[3][0], mon.HOME_GW_AGENT)
        self.assertIs(mon.GW_ROSTER["gw-agent developer"], mon.HOME_GW_AGENT)
        self.assertEqual([row[1] for row in mon.WORKER_ROSTER[2:]],
                         ["company-gw", "gw-agent"])

    def test_roster_rows_use_the_home_llm_names(self):
        # Owner #120 c6800664561: user-visible names say home LLM / gw-agent.
        self.assertEqual((mon.HOME_GW, mon.HOME_GW_AGENT), ("home LLM gw", "home LLM gw-agent"))
        self.assertNotIn("公司", inspect.getsource(mon.office_rows))


if __name__ == "__main__":
    unittest.main()
