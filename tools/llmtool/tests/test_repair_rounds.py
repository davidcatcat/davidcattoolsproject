"""Tests for conservative repair-round classification."""

import unittest

from llmtool.repair_rounds import counts_as_repair_round


class RepairRoundTests(unittest.TestCase):
    def test_only_proven_no_work_records_are_excluded(self):
        cases = (
            ({'state': 'DEVELOPER_PENDING'}, False),
            ({'state': 'DEVELOPER_UNAVAILABLE'}, False),
            ({'state': 'DEVELOPER_UNAVAILABLE', 'provider_type': 'gw-agent-cli',
              'error_code': 'DEVELOPER_GW_AGENT_CONFIG_INVALID'}, False),
            ({'state': 'DEVELOPER_UNAVAILABLE', 'provider_type': 'gw-agent-cli',
              'error_code': 'DEVELOPER_GW_AGENT_FAILED'}, True),
            ({'state': 'DEVELOPER_UNAVAILABLE', 'provider_type': 'gw-agent-cli',
              'error_code': 'GW_AGENT_HTTP_429'}, True),
            ({'state': 'DEVELOPER_OUTCOME_UNKNOWN', 'error_code': 'CODEX_BLOCKED',
              'execution': {'changed_files': False}}, False),
            ({'state': 'DEVELOPER_OUTCOME_UNKNOWN', 'error_code': 'CODEX_BLOCKED',
              'execution': {'changed_files': True}}, True),
            ({'state': 'DEVELOPER_OUTPUT_REJECTED', 'error_code': 'CODEX_BLOCKED',
              'execution': {'changed_files': False}}, True),
            ({'state': 'DEVELOPER_OUTPUT_REJECTED',
              'error_code': 'DEVELOPER_OUTPUT_INVALID_ENVELOPE',
              'execution': {'changed_files': False}}, False),
            ({'state': 'DEVELOPER_OUTPUT_REJECTED',
              'error_code': 'DEVELOPER_OUTPUT_SENSITIVE',
              'execution': {'changed_files': False}}, True),
            ({'state': 'DEVELOPER_OUTPUT_REJECTED',
              'error_code': 'DEVELOPER_OUTPUT_OUT_OF_SCOPE',
              'execution': {'changed_files': False}}, True),
            ({'state': 'DEVELOPER_OUTPUT_REJECTED',
              'error_code': 'DEVELOPER_OUTPUT_IGNORED_FILES',
              'execution': {'changed_files': False}}, True),
            ({'state': 'DEVELOPER_OUTPUT_REJECTED',
              'error_code': 'DEVELOPER_OUTPUT_TOO_LARGE',
              'execution': {'changed_files': False}}, True),
            ({'state': 'DEVELOPER_OUTPUT_REJECTED',
              'error_code': 'DEVELOPER_OUTPUT_INVALID_DIFF',
              'execution': {'changed_files': False}}, True),
            *(({'state': 'DEVELOPER_OUTCOME_UNKNOWN', 'error_code': code,
                'execution': {'changed_files': False}}, False)
              for code in ('CODEX_FINAL_JSON_INVALID',
                           'CODEX_FINAL_MESSAGE_INVALID',
                           'CODEX_FINAL_MESSAGE_MISSING')),
            ({'state': 'BLOCKED', 'error_code': 'CODEX_BLOCKED',
              'execution': {'changed_files': False}}, True),
            ({'state': 'DEVELOPER_RESPONDED'}, True),
            ({'state': 'DEVELOPER_DISPATCHING'}, True),
            ({'state': 'DEVELOPER_OUTCOME_UNKNOWN'}, True),
            ({'state': 'DEVELOPER_OUTPUT_REJECTED', 'error_code': 'UNKNOWN',
              'execution': {'changed_files': False}}, True),
            ({'state': 'DEVELOPER_OUTCOME_UNKNOWN', 'error_code': 'CODEX_BLOCKED'}, True),
            ({'state': 'DEVELOPER_OUTCOME_UNKNOWN', 'error_code': 'UNKNOWN',
              'execution': {'changed_files': False}}, True),
            ({'state': 'DEVELOPER_OUTCOME_UNKNOWN', 'error_code': 'UNKNOWN',
              'execution': {'changed_files': 'false'}}, True),
            ({'state': 'UNKNOWN'}, True),
            ({}, True),
            (None, True),
            ([], True),
        )
        for record, expected in cases:
            with self.subTest(record=record):
                self.assertIs(counts_as_repair_round(record), expected)


if __name__ == '__main__':
    unittest.main()
