"""#2473: Task Complete accepts a GitHub-proven docs-only PR without an EC2 receipt.

Unit tests for task_submission_cli._docs_only_completion_tests. GitHub is a
fake; the docs-only proof itself is task_advance._pr_docs_only (#2488).
The end-to-end Task Complete cases live in test_task_completion.py
(test_2473_*).
"""

import unittest

from llmtool import task_submission_cli
from llmtool.cli import LLMToolError


HEAD = 'a' * 40
OTHER = 'b' * 40
NUMBER = 17


def result(paths, head=HEAD, developer_paths=None):
    value = {'result_head': head, 'changed_paths': list(paths)}
    if developer_paths is not None:
        value['developer_changed_paths'] = list(developer_paths)
    return value


def github(files, *, head=HEAD, state='open', merged=False, count=None,
           after_head=None, fail_files=False):
    calls = []
    pr_count = len(files) if count is None else count

    def pr(sha):
        return {'number': NUMBER, 'head': {'sha': sha}, 'state': state,
                'merged': merged, 'changed_files': pr_count}

    def get(path):
        calls.append(path)
        if path == f'pulls/{NUMBER}':
            seen = sum(1 for item in calls if item == path)
            return pr(head if seen == 1 or after_head is None else after_head)
        if path.startswith(f'pulls/{NUMBER}/files'):
            if fail_files:
                raise LLMToolError('unavailable')
            return list(files)
        raise AssertionError('unexpected GitHub read ' + path)
    return get, calls


DOC = {'filename': 'docs/guide.md', 'status': 'modified'}


class DocsOnlyCompletionTestsTests(unittest.TestCase):
    def call(self, git_result, get, pr=NUMBER):
        return task_submission_cli._docs_only_completion_tests(pr, git_result, get)

    def test_proven_docs_only_returns_the_not_applicable_record(self):
        get, _ = github([DOC])
        self.assertEqual(self.call(result(['docs/guide.md']), get), [{
            'status': 'NOT_APPLICABLE_DOCS_ONLY', 'changed_paths': ['docs/guide.md'],
            'source_sha': HEAD, 'docs_only': True}])

    def test_developer_attributed_paths_are_the_record_paths(self):
        get, _ = github([DOC])
        tests = self.call(result(['docs/guide.md', 'docs/from-main.md'],
                                 developer_paths=['docs/guide.md']), get)
        self.assertEqual(tests[0]['changed_paths'], ['docs/guide.md'])

    def test_mixed_or_non_markdown_result_keeps_receipt_without_github_reads(self):
        for paths in (['docs/guide.md', 'tools/llmtool/llmtool/x.py'],
                      ['docs/guide.txt'], ['tools/llmtool/x/y.md'], []):
            with self.subTest(paths=paths):
                get, calls = github([DOC])
                self.assertIsNone(self.call(result(paths), get))
                self.assertEqual(calls, [])

    def test_pr_with_code_file_or_code_rename_is_not_docs_only(self):
        cases = ([DOC, {'filename': 'tools/llmtool/llmtool/x.py', 'status': 'added'}],
                 [{'filename': 'docs/guide.md', 'status': 'renamed',
                   'previous_filename': 'tools/llmtool/llmtool/x.py'}],
                 [{'filename': 'docs/guide.md', 'status': 'renamed'}])
        for files in cases:
            with self.subTest(files=files):
                get, _ = github(files)
                self.assertIsNone(self.call(result(['docs/guide.md']), get))

    def test_incomplete_file_list_or_read_failure_fails_closed(self):
        for kwargs in ({'count': 2}, {'fail_files': True}):
            with self.subTest(**kwargs):
                get, _ = github([DOC], **kwargs)
                self.assertIsNone(self.call(result(['docs/guide.md']), get))
        get, _ = github([])
        self.assertIsNone(self.call(result(['docs/guide.md']), get))

    def test_head_mismatch_or_race_fails_closed(self):
        get, _ = github([DOC], head=OTHER)
        self.assertIsNone(self.call(result(['docs/guide.md']), get))
        get, _ = github([DOC], after_head=OTHER)
        self.assertIsNone(self.call(result(['docs/guide.md']), get))

    def test_merged_or_closed_pr_keeps_the_receipt_requirement(self):
        for kwargs in ({'merged': True, 'state': 'closed'}, {'state': 'closed'}):
            with self.subTest(**kwargs):
                get, _ = github([DOC], **kwargs)
                self.assertIsNone(self.call(result(['docs/guide.md']), get))

    def test_missing_pr_or_malformed_result_fails_closed(self):
        get, calls = github([DOC])
        self.assertIsNone(self.call(result(['docs/guide.md']), get, pr=None))
        self.assertIsNone(self.call(None, get))
        self.assertIsNone(self.call({'changed_paths': ['docs/guide.md']}, get))
        self.assertEqual(calls, [])

    def test_unexpected_exception_returns_none(self):
        def broken(_path):
            raise RuntimeError('boom')
        self.assertIsNone(self.call(result(['docs/guide.md']), broken))


if __name__ == '__main__':
    unittest.main()
