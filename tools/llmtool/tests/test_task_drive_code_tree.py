import re
import unittest
from unittest.mock import MagicMock, patch

from llmtool import source_freshness
from llmtool import task_drive


class TaskDriveCodeTreeTests(unittest.TestCase):
    def adapter(self, results):
        calls = []

        def advance(**kwargs):
            calls.append(kwargs)
            return results.pop(0)

        return advance, calls

    def test_constant_tree_reader_drives_two_steps(self):
        advance, calls = self.adapter([
            {'task_id': 't', 'step': 'RUN_REMOTE_TEST', 'codes': []},
            {'task_id': 't', 'step': 'READY_FOR_INTEGRATION', 'codes': []},
        ])
        result = task_drive.drive(
            task_id='t',
            advance_call=advance,
            code_tree_read=lambda: '0' * 64,
        )
        self.assertEqual(result['step'], 'READY_FOR_INTEGRATION')
        self.assertEqual(len(calls), 2)

    def test_changed_tree_reader_stops_with_restart_required(self):
        advance, calls = self.adapter([
            {'task_id': 't', 'step': 'RUN_REMOTE_TEST', 'result': {'test': 'data'}, 'codes': []},
            {'task_id': 't', 'step': 'READY_FOR_INTEGRATION', 'codes': []},
        ])
        read_counts = 0

        def changing_reader():
            nonlocal read_counts
            read_counts += 1
            if read_counts <= 2:
                return 'a' * 40
            return 'b' * 40

        result = task_drive.drive(
            task_id='t',
            advance_call=advance,
            code_tree_read=changing_reader,
        )
        self.assertEqual(result['task_id'], 't')
        self.assertEqual(result['step'], 'RESTART_REQUIRED')
        self.assertEqual(result['codes'], ['LLMTOOL_SOURCE_CHANGED'])
        self.assertEqual(result['steps'], 1)
        self.assertEqual(result['result'], {'test': 'data'})
        self.assertEqual(len(calls), 1)

    def test_none_tree_reader_at_start_stops_with_unknown(self):
        advance, calls = self.adapter([
            {'task_id': 't', 'step': 'RUN_REMOTE_TEST', 'codes': []},
        ])
        result = task_drive.drive(
            task_id='t',
            advance_call=advance,
            code_tree_read=lambda: None,
        )
        self.assertEqual(result['task_id'], 't')
        self.assertEqual(result['step'], 'RESTART_REQUIRED')
        self.assertEqual(result['codes'], ['LLMTOOL_SOURCE_UNKNOWN'])
        self.assertEqual(result['steps'], 0)
        self.assertIsNone(result['result'])
        self.assertEqual(len(calls), 0)

    def test_known_then_none_tree_reader_stops_with_unknown_after_one_advance(self):
        advance, calls = self.adapter([
            {'task_id': 't', 'step': 'RUN_REMOTE_TEST', 'result': {'test': 'data'}, 'codes': []},
            {'task_id': 't', 'step': 'READY_FOR_INTEGRATION', 'codes': []},
        ])
        read_counts = 0

        def changing_reader():
            nonlocal read_counts
            read_counts += 1
            if read_counts <= 2:
                return 'a' * 40
            return None

        result = task_drive.drive(
            task_id='t',
            advance_call=advance,
            code_tree_read=changing_reader,
        )
        self.assertEqual(result['task_id'], 't')
        self.assertEqual(result['step'], 'RESTART_REQUIRED')
        self.assertEqual(result['codes'], ['LLMTOOL_SOURCE_UNKNOWN'])
        self.assertEqual(result['steps'], 1)
        self.assertEqual(result['result'], {'test': 'data'})
        self.assertEqual(len(calls), 1)

    @patch('llmtool.source_freshness.subprocess.run')
    def test_code_tree_filtering_and_digest(self, mock_run):
        pkg_id = '1' * 40
        json_id = '2' * 40
        tests_id = '3' * 40
        readme_id = '4' * 40

        def make_stdout(p_id, j_id, t_id, r_id):
            return (
                f'040000 tree {p_id}\ttools/llmtool/llmtool\n'
                f'100644 blob {j_id}\ttools/llmtool/developer-routes.json\n'
                f'040000 tree {t_id}\ttools/llmtool/tests\n'
                f'100644 blob {r_id}\ttools/llmtool/README.md\n'
            )

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=make_stdout(pkg_id, json_id, tests_id, readme_id),
        )
        base_digest = source_freshness.code_tree()
        self.assertIsNotNone(base_digest)
        self.assertEqual(len(base_digest), 64)
        self.assertTrue(re.match(r'^[0-9a-f]{64}\Z', base_digest))

        # Changing only the tests line gives the same digest
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=make_stdout(pkg_id, json_id, '5' * 40, readme_id),
        )
        self.assertEqual(source_freshness.code_tree(), base_digest)

        # Changing only the README line gives the same digest
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=make_stdout(pkg_id, json_id, tests_id, '6' * 40),
        )
        self.assertEqual(source_freshness.code_tree(), base_digest)

        # Changing the package tree id gives a different digest
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=make_stdout('7' * 40, json_id, tests_id, readme_id),
        )
        diff_pkg_digest = source_freshness.code_tree()
        self.assertIsNotNone(diff_pkg_digest)
        self.assertNotEqual(diff_pkg_digest, base_digest)

        # Changing the json blob id gives a different digest
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=make_stdout(pkg_id, '8' * 40, tests_id, readme_id),
        )
        diff_json_digest = source_freshness.code_tree()
        self.assertIsNotNone(diff_json_digest)
        self.assertNotEqual(diff_json_digest, base_digest)

        # returncode 1 returns None
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout=make_stdout(pkg_id, json_id, tests_id, readme_id),
        )
        self.assertIsNone(source_freshness.code_tree())

        # no package line returns None
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                f'100644 blob {json_id}\ttools/llmtool/developer-routes.json\n'
                f'040000 tree {tests_id}\ttools/llmtool/tests\n'
                f'100644 blob {readme_id}\ttools/llmtool/README.md\n'
            ),
        )
        self.assertIsNone(source_freshness.code_tree())

        # run raising OSError returns None
        mock_run.side_effect = OSError('synthetic error')
        self.assertIsNone(source_freshness.code_tree())

    @patch('llmtool.source_freshness.subprocess.run')
    def test_code_tree_synthetic_sha_and_error_handling(self, mock_run):
        # stdout 'a' * 40 + '\n' (bare hex) returns None
        mock_run.side_effect = None
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='a' * 40 + '\n',
        )
        self.assertIsNone(source_freshness.code_tree())

        # returncode 1 returns None
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout='c' * 40 + '\n',
        )
        self.assertIsNone(source_freshness.code_tree())

        # stdout 'xyz' returns None
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='xyz\n',
        )
        self.assertIsNone(source_freshness.code_tree())

        # run raising OSError returns None
        mock_run.side_effect = OSError('synthetic error')
        self.assertIsNone(source_freshness.code_tree())

    @patch('llmtool.source_freshness.subprocess.run')
    def test_code_tree_bare_hex_returns_none(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='a' * 40 + '\n',
        )
        self.assertIsNone(source_freshness.code_tree())
