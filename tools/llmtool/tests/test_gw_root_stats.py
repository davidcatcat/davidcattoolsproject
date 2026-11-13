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


# The default is the retired gw Agent id (#1502 WP-E): these fixtures are Task
# records written before the rename, which gw_root_stats.py reads as gw-agent
# through worker_dispatch.stored_worker_id (WorkerIdTests covers both ids).
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


class WorkerIdTests(unittest.TestCase):
    """#1502 WP-E: audits under the retired id and under gw-agent both count."""

    def test_current_worker_id_is_gw_agent(self):
        self.assertEqual(stats.GW_WORKER, 'gw-agent')

    def test_legacy_and_new_ids_both_count_as_gw_dispatches(self):
        for worker in ('company-gw-agent', 'gw-agent'):
            with self.subTest(worker=worker):
                item = task('t1', 'no tag', worker=worker)
                self.assertEqual(stats.first_gw_dispatch(item), '2026-11-12T00:00:00Z')
                self.assertEqual(sorted(stats.group_roots([], [item])), ['t1'])

    def test_other_ids_do_not_count(self):
        for worker in ('company-gw', 'agy', 'claude-developer', None, 7):
            with self.subTest(worker=worker):
                self.assertIsNone(stats.first_gw_dispatch(task('t1', 'no tag', worker=worker)))

    def test_mixed_audit_uses_the_earliest_entry_of_either_id(self):
        item = task('t1', 'no tag', at='2026-11-12T05:00:00Z', worker='gw-agent')
        item['dispatch_audit'].append({'action': 'DISPATCH', 'decision': 'ALLOWED',
                                       'worker_key': 'company-gw-agent',
                                       'at': '2026-11-12T01:00:00Z'})
        self.assertEqual(stats.first_gw_dispatch(item), '2026-11-12T01:00:00Z')
        row = stats.root_row('t1', None, [item], {})
        self.assertEqual((row['kind'], row['first_dispatch']),
                         ('UNTAGGED', '2026-11-12T01:00:00Z'))


class RootRowTests(unittest.TestCase):
    def test_known_and_unknown_fields(self):
        done = task('t1', 'ROOT=r1 a', published_pr={'pr': 7},
                    completion={'executor_task_id': 'd1'}, developer={'usage': {'total_tokens': 10}})
        failed = task('t2', 'ROOT=r1 b', developer={'error_code': 'X'})
        github = {'7': {'merged_at': '2026-11-12T01:00:00Z'}}
        row = stats.root_row('r1', {'root_id': 'r1', 'source_url': 'u'}, [done, failed], github)
        self.assertEqual((row['kind'], row['tasks'], row['d6s'], row['prs']), ('ROOT', 2, 1, '7'))
        self.assertEqual((row['total_tokens'], row['d6_records']), ('UNKNOWN', 'UNKNOWN'))
        self.assertEqual((row['last_merged_at'], row['error_codes']), ('2026-11-12T01:00:00Z', 'X'))
        self.assertEqual(stats.root_row('t9', None, [task('t9', 'x')], {})['kind'], 'UNTAGGED')
        self.assertEqual(stats.root_row('r9', None, [task('t9', 'ROOT=r9')], {})['kind'], 'UNDECLARED')


class MainTests(unittest.TestCase):
    def test_main_writes_csv_and_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / 'tasks').mkdir()
            ledger = base / 'roots.jsonl'
            ledger.write_text(json.dumps({'root_id': 'r1'}) + '\nnot json\n', encoding='utf-8')
            item = task('t1', 'ROOT=r1 a', developer={'usage': {'total_tokens': 10}})
            (base / 'tasks' / 't1.json').write_text(json.dumps(item), encoding='utf-8')
            self.assertEqual(stats.main(['--ledger', str(ledger), '--tasks', str(base / 'tasks'),
                                         '--out-dir', str(base / 'out')]), 0)
            text = (base / 'out' / 'roots.csv').read_text(encoding='utf-8')
            rows = list(csv.DictReader(text.splitlines()))
            self.assertEqual([(row['root_id'], row['total_tokens'], row['last_merged_at'])
                              for row in rows], [('r1', '10', 'UNKNOWN')])
            summary = json.loads((base / 'out' / 'summary.json').read_text(encoding='utf-8'))
            self.assertEqual((summary['roots'], summary['tokens_known'], summary['merge_known']),
                             (1, 1, 0))
