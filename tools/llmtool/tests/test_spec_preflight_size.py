import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "spec_preflight.py"

SPEC = importlib.util.spec_from_file_location("spec_preflight", SCRIPT)
spec_preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(spec_preflight)
spec_tier = spec_preflight.spec_tier


class SpecTierTests(unittest.TestCase):
    def test_one_write_small_context_is_tier_1(self):
        self.assertEqual(spec_tier({"write": ["a"]}, context_bytes=1000), 1)

    def test_one_write_larger_context_is_tier_2(self):
        self.assertEqual(spec_tier({"write": ["a"]}, context_bytes=2000), 2)

    def test_two_writes_5000_bytes_is_tier_3(self):
        self.assertEqual(spec_tier({"write": ["a", "b"]}, context_bytes=5000), 3)

    def test_three_writes_9000_bytes_is_tier_4(self):
        self.assertEqual(spec_tier({"write": ["a", "b", "c"]}, context_bytes=9000), 4)

    def test_four_writes_is_tier_5(self):
        self.assertEqual(spec_tier({"write": ["a", "b", "c", "d"]}), 5)

    def test_one_write_huge_context_never_fits(self):
        self.assertEqual(spec_tier({"write": ["a"]}, context_bytes=30000),
                         spec_preflight.LADDER_MAX_TIER)

    def test_spec_with_no_read_or_write_key_is_tier_1(self):
        self.assertEqual(spec_tier({"request": "r"}), 1)

    def test_agent_three_reads_one_write_is_tier_1(self):
        # 3 reads + 1 write = 6 turns, within tier 1 limit of 6.
        spec = {"read": ["a", "b", "c"], "write": ["d"], "provider": "gw-agent"}
        self.assertEqual(spec_tier(spec), 1)

    def test_agent_six_reads_one_write_is_tier_2(self):
        # 6 reads + 1 write = 9 turns, past tier 1 but within tier 2.
        spec = {"read": ["a", "b", "c", "d", "e", "f"], "write": ["g"], "provider": "gw-agent"}
        self.assertEqual(spec_tier(spec), 2)

    def test_agent_eight_reads_two_writes_is_tier_4(self):
        # 8 reads + 2 writes = 13 turns, past tiers 1-3, within tier 4.
        spec = {"read": ["a", "b", "c", "d", "e", "f", "g", "h"], "write": ["i", "j"], "provider": "gw-agent"}
        self.assertEqual(spec_tier(spec), 4)

    def test_agent_twenty_reads_one_write_never_fits(self):
        # 20 reads + 1 write = 23 turns, beyond every tier limit.
        spec = {"read": [str(i) for i in range(20)], "write": ["a"], "provider": "gw-agent"}
        self.assertEqual(spec_tier(spec), spec_preflight.LADDER_MAX_TIER)


if __name__ == "__main__":
    unittest.main()
