"""#1943 G2: _admit_debug maps policy and lineage errors to fixed High Debug codes."""
import unittest
from unittest.mock import patch

from llmtool import high_debug, repair_policy
from llmtool.errors import LLMToolError


class AdmitDebugCodeTests(unittest.TestCase):
    def refused_code(self, task, entries=()):
        with self.assertRaises(LLMToolError) as caught:
            high_debug._admit_debug(task, list(entries))
        return getattr(caught.exception, 'high_debug_code', None)

    def test_unreadable_policy_refuses_policy_invalid(self):
        with patch.object(repair_policy, 'load_policy',
                          side_effect=LLMToolError('POLICY_INVALID')):
            code = self.refused_code({'lineage': {'kind': 'root'}})
        self.assertEqual(code, 'HIGH_DEBUG_POLICY_INVALID')

    def test_unknown_lineage_kind_refuses_lineage_invalid(self):
        for kind in ('bogus', '', 'ROOT', None):
            with self.subTest(kind=kind):
                code = self.refused_code({'lineage': {'kind': kind}})
                self.assertEqual(code, 'HIGH_DEBUG_LINEAGE_INVALID')

    def test_refusal_message_names_no_detail(self):
        with patch.object(repair_policy, 'load_policy',
                          side_effect=LLMToolError('synthetic policy detail')):
            with self.assertRaises(LLMToolError) as caught:
                high_debug._admit_debug({}, [])
        self.assertEqual(caught.exception.high_debug_code, 'HIGH_DEBUG_POLICY_INVALID')
        self.assertNotIn('synthetic', str(caught.exception))

    def test_valid_root_with_no_entries_is_admitted(self):
        self.assertIsNone(high_debug._admit_debug({'lineage': {'kind': 'root'}}, []))
        self.assertIsNone(high_debug._admit_debug({}, []))


if __name__ == '__main__':
    unittest.main()
