"""#1943 WP-3: owner acceptance checks for TUI test-only PR changes."""

import unittest

import llmtool.task_advance
from llmtool import task_advance


class OwnerAcceptanceTuiTestOnlyTestCase(unittest.TestCase):
    def test_owner_acceptance_false_for_test_chat_tui(self):
        files = [{'filename': 'tools/llmtool/tests/test_chat_tui.py'}]
        self.assertFalse(task_advance._owner_acceptance_required(files))

    def test_owner_acceptance_false_for_test_tui_markdown(self):
        files = [{'filename': 'tools/llmtool/tests/test_tui_markdown.py'}]
        self.assertFalse(task_advance._owner_acceptance_required(files))

    def test_owner_acceptance_false_for_both_test_files_together(self):
        files = [
            {'filename': 'tools/llmtool/tests/test_chat_tui.py'},
            {'filename': 'tools/llmtool/tests/test_tui_markdown.py'},
        ]
        self.assertFalse(task_advance._owner_acceptance_required(files))

    def test_owner_acceptance_false_for_test_files_plus_non_tui_module(self):
        files_single = [
            {'filename': 'tools/llmtool/tests/test_chat_tui.py'},
            {'filename': 'tools/llmtool/llmtool/task_advance.py'},
        ]
        self.assertFalse(task_advance._owner_acceptance_required(files_single))

        files_both = [
            {'filename': 'tools/llmtool/tests/test_chat_tui.py'},
            {'filename': 'tools/llmtool/tests/test_tui_markdown.py'},
            {'filename': 'tools/llmtool/llmtool/task_advance.py'},
        ]
        self.assertFalse(task_advance._owner_acceptance_required(files_both))

    def test_owner_acceptance_true_for_chat_tui_product_code(self):
        files = [{'filename': 'tools/llmtool/llmtool/chat_tui.py'}]
        self.assertTrue(task_advance._owner_acceptance_required(files))

    def test_owner_acceptance_true_for_tui_markdown_editor_product_code(self):
        files = [{'filename': 'tools/llmtool/llmtool/tui_markdown_editor.py'}]
        self.assertTrue(task_advance._owner_acceptance_required(files))

    def test_owner_acceptance_true_for_tui_pty_driver(self):
        files = [{'filename': 'tools/llmtool/tests/tui_pty_driver.py'}]
        self.assertTrue(task_advance._owner_acceptance_required(files))

    def test_owner_acceptance_true_for_test_file_plus_chat_tui_product_code(self):
        files = [
            {'filename': 'tools/llmtool/tests/test_chat_tui.py'},
            {'filename': 'tools/llmtool/llmtool/chat_tui.py'},
        ]
        self.assertTrue(task_advance._owner_acceptance_required(files))

    def test_owner_acceptance_true_for_non_list(self):
        for candidate in (None, 'not a list', 123, {'filename': 'tools/llmtool/tests/test_chat_tui.py'}):
            with self.subTest(candidate=candidate):
                self.assertTrue(task_advance._owner_acceptance_required(candidate))

    def test_owner_acceptance_true_for_item_without_str_filename(self):
        for item in ({'filename': None}, {'filename': 123}, {}, None, 'not-a-dict'):
            with self.subTest(item=item):
                self.assertTrue(task_advance._owner_acceptance_required([item]))


if __name__ == '__main__':
    unittest.main()
