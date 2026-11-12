import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'gw_root_stats.py'
SPEC = importlib.util.spec_from_file_location('gw_root_stats', SCRIPT)
stats = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stats)


def task(task_id, request, at='2026-11-12T00:00:00Z', worker='company-gw-agent', **extra):
    audit = [{'action': 'DISPATCH', 'decision': 'ALLOWED', 'worker_key': worker, 'at': at}]
    return dict({'task_id': task_id, 'request': request, 'dispatch_audit': audit}, **extra)


class GroupRootsTests(unittest.TestCase):
    def test_tagged_untagged_and_excluded_tasks(self):
        ledger = [{'root_id': 'r1', 'kind': 'ROOT'}]
        tasks = [task('t1', 'ROOT=r1 piece 1'), task('t2', 'ROOT=r1 piece 2'),
                 task('t3', 'no tag'),
                 task('t4', 'no tag', at='2026-11-10T00:00:00Z'),
                 task('t5', 'no tag', worker='agy')]
        roots = stats.group_roots(ledger, tasks)
        self.assertEqual(sorted(roots), ['r1', 't3'])
        self.assertEqual([item['task_id'] for item in roots['r1'][1]], ['t1', 't2'])
        self.assertIsNone(roots['t3'][0])
        self.assertIsNone(stats.root_tag({'request': 'ROOT='}))
        self.assertEqual(stats.root_tag({'request': 'ROOT=r2. piece'}), 'r2')
