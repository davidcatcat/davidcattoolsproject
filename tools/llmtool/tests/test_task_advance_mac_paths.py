import unittest
from pathlib import Path

from llmtool import task_advance

WORKFLOW = Path(__file__).resolve().parents[3] / '.github/workflows/llmtool-mac-ci.yml'
HEAD = 'a' * 40
LINUX = {'id': 1, 'name': 'check (Linux 3.14)', 'head_sha': HEAD,
         'status': 'completed', 'conclusion': 'success'}


def _merge_checks(paths):
    def get(path):
        if path.startswith('commits/'):
            return {'total_count': 1, 'check_runs': [LINUX]}
        return [{'filename': name} for name in paths]
    return task_advance._merge_checks(None, 1, HEAD, {'mergeable': True}, get)


def _workflow_paths():
    block = WORKFLOW.read_text(encoding='utf-8').split('pull_request:', 1)[1]
    block = block.split('paths:', 1)[1].split('workflow_dispatch:', 1)[0]
    return tuple(line.strip()[3:-1] for line in block.splitlines()
                 if line.strip().startswith('- '))


class MacCiPathFilterTests(unittest.TestCase):
    def test_list_matches_mac_workflow_pull_request_paths(self):
        self.assertEqual(_workflow_paths(), task_advance.MAC_CI_PR_PATHS)

    def test_reports_only_pr_needs_linux_but_not_mac(self):
        self.assertIsNone(_merge_checks(['reports/x/results.json', 'reports/y.md']))
        self.assertFalse(task_advance._mac_ci_runs(['tools/llmtool/README.md']))

    def test_llmtool_pr_still_needs_mac(self):
        self.assertEqual(_merge_checks(['tools/llmtool/llmtool/a.py']),
                         'MERGE_REQUIRED_CHECK_MISSING')
