"""#2503: a bounded, screened tail of a Developer CLI's stderr is kept for the PM.

Test data rule (Owner #763 A): no credential-shaped string is used; the screen
refusal case makes the screen refuse a harmless sentinel.
"""

import unittest
from unittest.mock import patch

from llmtool import developer_stderr
from llmtool import task_store
from llmtool import task_submission_cli as cli

LIMIT = task_store.DEVELOPER_STDERR_EXCERPT_MAX_BYTES
SENTINEL = 'SCREEN-REFUSED-SENTINEL-2503'


def _refusing_screen():
    return patch.object(developer_stderr, '_emitted_sensitive',
                        side_effect=lambda text: SENTINEL in text)


class StderrExcerptTests(unittest.TestCase):
    def test_limit_is_4_kib(self):
        self.assertEqual(LIMIT, 4 * 1024)

    def test_present_stderr_is_kept(self):
        self.assertEqual(cli._bounded_stderr_excerpt(b'warning: permission denied for run_command\n'),
                         'warning: permission denied for run_command')

    def test_empty_or_missing_stderr_gives_nothing(self):
        for raw in (None, b'', b'   \n', 7):
            with self.subTest(raw=raw):
                self.assertIsNone(cli._bounded_stderr_excerpt(raw))

    def test_oversized_stderr_keeps_the_tail_with_a_marker(self):
        raw = ('early line\n' * 2000 + 'LAST: quota exhausted').encode()
        excerpt = cli._bounded_stderr_excerpt(raw)
        self.assertTrue(excerpt.startswith(cli.DEVELOPER_REPLY_EARLIER_TRUNCATED))
        self.assertTrue(excerpt.endswith('LAST: quota exhausted'))
        self.assertLessEqual(len(excerpt.encode('utf-8')), LIMIT)
        self.assertTrue(task_store._valid_reported_text(excerpt, LIMIT))

    def test_multibyte_tail_stays_within_the_byte_limit(self):
        excerpt = cli._bounded_stderr_excerpt(('錯誤' * 3000).encode('utf-8'))
        self.assertLessEqual(len(excerpt.encode('utf-8')), LIMIT)

    def test_unsafe_stderr_is_withheld(self):
        with _refusing_screen():
            excerpt = cli._bounded_stderr_excerpt(('notice ' + SENTINEL).encode())
        self.assertEqual(excerpt, cli.DEVELOPER_STDERR_WITHHELD)

    def test_control_characters_are_replaced_so_the_store_accepts_it(self):
        excerpt = cli._bounded_stderr_excerpt(b'\x1b[31merror\x1b[0m: denied\x00')
        self.assertEqual(excerpt, '?[31merror?[0m: denied?')
        self.assertTrue(task_store._valid_reported_text(excerpt, LIMIT))

    def test_attach_never_raises_and_sets_only_an_accepted_value(self):
        developer = {}
        with patch.object(cli, '_bounded_stderr_excerpt', side_effect=RuntimeError):
            cli._attach_stderr_excerpt(developer, b'x')
        self.assertEqual(developer, {})
        cli._attach_stderr_excerpt(developer, b'')
        self.assertEqual(developer, {})
        cli._attach_stderr_excerpt(developer, b'auth: please log in')
        self.assertEqual(developer, {'stderr_excerpt': 'auth: please log in'})

    def test_pm_packet_shows_stderr_next_to_the_reply(self):
        task = {'task_id': 'a' * 32, 'run_id': 'b' * 32,
                'task_binding': {'contract_digest': 'c' * 64}, 'request': 'r',
                'response': 'p', 'pm_assignment': None}
        developer = {'provider_profile': 'agy', 'model': 'm', 'session_id': 's',
                     'usage': None, 'execution': {'native_session_id': None,
                                                  'usage_source': 'UNKNOWN'},
                     'reported': {'summary': 'BLOCKED'}, 'stderr_excerpt': 'soft-deny notice'}
        evidence = {'result': {'source_sha': 'd' * 40, 'changed_paths': [], 'diff_sha256': '0' * 64},
                    'executor_identity': 'agy-developer', 'executor_session_ref': 'ref',
                    'executor_native_session': 'UNKNOWN', 'tests': [], 'review_pr_ci': 'NOT_STARTED'}
        with patch.object(cli, '_pm_assignment_packet', return_value=None):
            packet = cli._direct_result_packet(task, developer, 'executor-d6', evidence)
        self.assertEqual(packet['developer_reported']['summary'], 'BLOCKED')
        self.assertEqual(packet['developer_stderr'],
                         {'label': 'Developer-runtime, unverified', 'excerpt': 'soft-deny notice'})


if __name__ == '__main__':
    unittest.main()
