import unittest

from llmtool import task_advance
from test_task_advance import HEAD, PR, base_record


def _check(name):
    return {'name': name, 'head_sha': HEAD, 'status': 'completed',
            'conclusion': 'success'}


def _fake_get(paths, check_names):
    checks = {'check_runs': [_check(name) for name in check_names],
              'total_count': len(check_names)}
    files = [{'filename': path} for path in paths]

    def github_get(path):
        if path.startswith('commits/'):
            return checks
        if path.startswith('pulls/') and 'files' in path:
            return files
        raise AssertionError(path)
    return github_get


class MergeChecksDocsRuleTests(unittest.TestCase):
    def _merge_checks(self, paths, check_names, **kwargs):
        pr = dict(PR, state='open', mergeable=True)
        return task_advance._merge_checks(
            base_record(), 17, HEAD, pr, _fake_get(paths, check_names), **kwargs)

    def test_skills_non_markdown_and_docs_need_only_docs_check(self):
        paths = ['skills/x.json', 'docs/y.md']
        self.assertIsNone(self._merge_checks(paths, ['check (docs)']))

    def test_deep_llmtool_markdown_needs_code_checks(self):
        paths = ['tools/llmtool/llmtool/deep/z.md']
        self.assertEqual(self._merge_checks(paths, ['check (docs)']),
                         'MERGE_REQUIRED_CHECK_MISSING')
        self.assertEqual(
            self._merge_checks(paths, ['check (docs)', 'check (Linux 3.14)']),
            'MERGE_REQUIRED_CHECK_MISSING')
        self.assertIsNone(self._merge_checks(
            paths, ['check (Linux 3.14)', 'check (macOS 3.13)']))

    def test_top_level_readme_stays_docs_only(self):
        self.assertIsNone(self._merge_checks(['README.md'], ['check (docs)']))


if __name__ == '__main__':
    unittest.main()
