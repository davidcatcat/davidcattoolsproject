import unittest
from pathlib import Path

from llmtool import task_advance

WORKFLOW = Path(__file__).resolve().parents[3] / '.github/workflows/llmtool-mac-ci.yml'
HEAD = 'a' * 40
LINUX = 'check (Linux 3.14)'
DOCS = 'check (docs)'


def _run(run_id, name):
    return {'id': run_id, 'name': name, 'head_sha': HEAD,
            'status': 'completed', 'conclusion': 'success'}


def _merge_checks(files, names, **kwargs):
    """Run the merge gate with exactly the given exact-head SUCCESS checks."""
    runs = [_run(index, name) for index, name in enumerate(names, 1)]
    def get(path):
        if path.startswith('commits/'):
            return {'total_count': len(runs), 'check_runs': runs}
        return files
    return task_advance._merge_checks(None, 1, HEAD, {'mergeable': True}, get, **kwargs)


def _workflow_paths():
    block = WORKFLOW.read_text(encoding='utf-8').split('pull_request:', 1)[1]
    block = block.split('paths:', 1)[1].split('workflow_dispatch:', 1)[0]
    return tuple(line.strip()[3:-1] for line in block.splitlines()
                 if line.strip().startswith('- '))


class MacCiPathFilterTests(unittest.TestCase):
    def assert_linux_checks(self, paths, old_paths, expected):
        # The fixture supplies exactly what the canonical workflow flags require.
        self.assertEqual(task_advance._required_linux_checks(paths, old_paths), expected)

    def test_list_matches_mac_workflow_pull_request_paths(self):
        self.assertEqual(_workflow_paths(), task_advance.MAC_CI_PR_PATHS)

    def test_reports_only_pr_needs_linux_but_not_mac(self):
        paths = ['reports/x/results.json', 'reports/y.md']
        files = [{'filename': name} for name in paths]
        # Markdown sets the docs flag, so Linux and docs are both required.
        self.assert_linux_checks(paths, (), [LINUX, DOCS])
        self.assertFalse(task_advance._mac_ci_runs(paths))
        self.assertIsNone(_merge_checks(files, [LINUX, DOCS]))
        self.assertEqual(_merge_checks(files, [LINUX]), 'MERGE_REQUIRED_CHECK_MISSING')
        self.assertFalse(task_advance._mac_ci_runs(['tools/llmtool/README.md']))

    def test_llmtool_pr_still_needs_mac(self):
        paths = ['tools/llmtool/llmtool/a.py']
        files = [{'filename': name} for name in paths]
        self.assert_linux_checks(paths, (), [LINUX])
        self.assertTrue(task_advance._mac_ci_runs(paths))
        self.assertEqual(_merge_checks(files, [LINUX]), 'MERGE_REQUIRED_CHECK_MISSING')
        # Only macOS is missing: every Linux-flag check is present.
        self.assertEqual(_merge_checks(files, [LINUX], allow_missing_macos=True),
                         'MERGE_MACOS_CHECK_MISSING')

    def test_rename_out_of_mac_paths_still_needs_mac(self):
        new, old = 'reports/a.py', 'tools/llmtool/llmtool/a.py'
        files = [{'filename': new, 'previous_filename': old}]
        self.assert_linux_checks([new], [old], [LINUX])
        self.assertFalse(task_advance._mac_ci_runs([new]))
        self.assertTrue(task_advance._mac_ci_runs([new, old]))
        self.assertEqual(_merge_checks(files, [LINUX]), 'MERGE_REQUIRED_CHECK_MISSING')
        self.assertEqual(_merge_checks(files, [LINUX], allow_missing_macos=True),
                         'MERGE_MACOS_CHECK_MISSING')
