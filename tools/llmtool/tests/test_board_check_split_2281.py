"""Tests for the #2281 split of ``llmtool.board_check`` into <= 24 KiB modules."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmtool import board_check  # noqa: E402

MAX_MODULE_BYTES = 24 * 1024
_PACKAGE_DIR = Path(__file__).resolve().parents[1] / 'llmtool'

EXPECTED_PUBLIC_NAMES = (
    'BOARD_SCHEMA_VERSION',
    'MAX_BOARD_BYTES',
    'MAX_GITHUB_PAGES',
    'RUNNING',
    'WAITING_CI',
    'WAITING_REVIEW',
    'WAITING_OWNER',
    'BLOCKED',
    'ACTIONABLE',
    'ANOMALY',
    'UNKNOWN',
    'DONE',
    'CLAIMED',
    'FLAGGED_STATES',
    'LEDGER_SCHEMA_VERSION',
    'MAX_LEDGER_BYTES',
    'MAX_LEDGER_CLAIMS',
    'RESERVATION_TTL',
    'LEDGER_LOCK_TIMEOUT_SECONDS',
    'CLAIM_REFUSED_EXIT',
    'DEVELOPER_ROUTES',
    'BoardUnreadable',
    'LedgerUnreadable',
    'ClaimRefused',
    'BoardLedger',
    'default_board_path',
    'read_board',
    'parse_predicate',
    'default_ledger_path',
    'claim_live',
    'claim_row',
    'check_board',
    'github_reader',
    'list_processes',
    'format_result',
    'add_board_check_parser',
    'run_board_check',
)


class BoardCheckSplitTests(unittest.TestCase):
    """Verify A1 (module size bounds) and A2/A5 (public name availability)."""

    def test_board_check_and_split_parts_are_at_most_24_kib(self):
        modules = sorted(_PACKAGE_DIR.glob('board_check*.py'))
        self.assertGreater(len(modules), 1)
        for path in modules:
            with self.subTest(module=path.name):
                self.assertLessEqual(path.stat().st_size, MAX_MODULE_BYTES)

    def test_public_names_remain_available_from_board_check(self):
        for name in EXPECTED_PUBLIC_NAMES:
            with self.subTest(name=name):
                self.assertTrue(hasattr(board_check, name))


if __name__ == '__main__':
    unittest.main()
