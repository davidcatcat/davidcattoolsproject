"""#2602 (b): repair-created Tasks share the original Task's Debug lineage.

Every case writes through the real TaskStore. A repair Task carries only its
repair_packet; the Debug lineage key, its members, the shared Debug count and
the high-debug lock key must follow the original Task named there.

Test data (Owner #763 A): plain English prose only, with no credential-shaped
strings. The reviewer excerpt is stored as plain fixture prose and is never
read or interpreted by the lineage code.
"""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_MODULE_HOME = tempfile.TemporaryDirectory()
_MODULE_HOME_PATCH = None
_IMPORT_HOME_PATCH = patch.dict(os.environ, {'HOME': _MODULE_HOME.name})
_IMPORT_HOME_PATCH.start()
from llmtool import high_debug, lineage_counts, start_failure, task_submission_cli, worker_dispatch
from llmtool.task_store import TaskStore, new_id, record_lineage
from llmtool.task_submission_cli import _record
from review_trust_fixture import start_module as _start_trust_fixture
from review_trust_fixture import stop_module as _stop_trust_fixture
import worker_policy_fixture
_IMPORT_HOME_PATCH.stop()

SOURCE = 'a' * 40
HEAD = 'b' * 40
MAIN = 'c' * 40
AT = '2026-11-20T00:00:00Z'
FAMILY = 'pr-2602'
REVIEW = 'Synthetic reviewer note for the repair lineage test.'


def setUpModule():
    _start_trust_fixture()
    global _MODULE_HOME_PATCH
    _MODULE_HOME_PATCH = patch.dict(os.environ, {'HOME': _MODULE_HOME.name})
    _MODULE_HOME_PATCH.start()
    user_config = Path(_MODULE_HOME.name) / '.config' / 'llmtool' / 'config.yaml'
    user_config.parent.mkdir(parents=True, exist_ok=True)
    user_config.write_text('generation: {}\n', encoding='utf-8')


def tearDownModule():
    if _MODULE_HOME_PATCH is not None:
        _MODULE_HOME_PATCH.stop()
    _stop_trust_fixture()
    _MODULE_HOME.cleanup()


def pending(callback, executor):
    return {
        'assignment_id': new_id(), 'selection_source': 'PM', 'state': 'DEVELOPER_PENDING',
        'provider_profile': 'claude', 'provider_type': None, 'model': None,
        'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
        'session_id': 'dev-' + new_id(), 'source_sha': SOURCE,
        'source_sha_binding': 'D6_REGISTRY_OBSERVED',
        'read_paths': ['src.py'], 'write_paths': ['src.py'],
        'acceptance': ['the focused test passes'], 'context_sha256': 'b' * 64,
        'egress_basis': 'Plain project text for a bounded change.', 'input_bytes': 10,
        'max_tokens': None, 'artifact_format': 'unified_diff', 'artifact': None,
        'artifact_sha256': None, 'usage': None, 'error_code': None, 'execution': None,
        'created_at': AT, 'owner_assignment_ref': None, 'executor_task_id': executor,
        'timeout_seconds': 600, 'direct_result_callback': callback,
    }


def repair_packet(original_id, **changes):
    packet = {'task': original_id, 'pr': 2602, 'executor_task': 'executor-repair',
              'failed_head': HEAD, 'version': 1, 'verdict_comment_id': 17,
              'findings_count': 1,
              'reviewer_text_excerpt_untrusted_data': REVIEW}
    packet.update(changes)
    return packet


def used_entry():
    return dict(high_debug._failed_entry(
        family=FAMILY, failed_corrections=None,
        early_reason='Synthetic repair lineage test.', head=HEAD,
        d6=high_debug._d6_name('executor', 1, HEAD), code='HIGH_DEBUG_RESERVED',
        observed='UNKNOWN', effort='UNKNOWN', usage='UNKNOWN', recorded_at=AT,
        model_requested='synthetic-debug-model', rules_sha256='d' * 64),
        status='RCA_CONFIRMED')


class RepairLineageCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        worker_policy_fixture.install(self)
        repository = patch.object(worker_dispatch, 'repository_config',
            return_value={'slug': 'davidcatcat/davidcattoolsproject',
                          'dispatch_issue': 289, 'dispatch_evidence_pr': 283})
        repository.start()
        self.addCleanup(repository.stop)
        self.store = TaskStore(Path(temp.name).resolve() / 'tasks')

    def task(self):
        request = 'Synthetic bounded repair lineage task.'
        task_id, run_id = new_id(), new_id()
        task = _record(task_id, run_id, 'pm-' + new_id(), 'pm-profile', request,
                       state='PM_RESPONDED', provider_type='openai-compatible',
                       model='pm-model', backend='NO_NATIVE_SESSION_ID_EXPOSED',
                       request_sha=hashlib.sha256(request.encode()).hexdigest())
        task.update(pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                                      'base_url': None},
                    response='PM accepted a bounded request.', error_code=None,
                    pm_assignment={'schema_version': 1, 'route': 'claude', 'risk': 'LOW',
                                   'goal': 'Synthetic repair lineage task.',
                                   'acceptance': ['the lineage is shared']})
        self.store.create(task)
        return task

    def bind(self, task, repair_packet_text=None):
        executor = 'executor-' + task['task_id'][:12]
        registration = SimpleNamespace(
            task_id=executor, root='/registered/root', common_dir='/repo/.git',
            branch='task-branch', trusted_agent_id='claude-developer',
            session_ref='session-ref', native_session_id=None)
        callback = task_submission_cli._direct_result_callback(
            task, registration, SOURCE, test_policy='LOCAL')
        completion = {'state': 'WAITING_EXECUTOR', 'executor_task_id': executor,
                      'reviewer_task_id': None, 'evidence': dict(callback['evidence']),
                      'missing_gates': [], 'pm_dispatch': 'NOT_STARTED',
                      'pm_response': None, 'pm_recommendation': None, 'pm_usage': None,
                      'pm_error_code': None}
        extra = ({} if repair_packet_text is None else
                 {'repair_packet': repair_packet_text})
        return self.store.d6_bind_compare_and_update(
            task['task_id'], executor_task_id=executor,
            expected={'schema_version': 2, 'state': 'PM_RESPONDED',
                      'developer': None, 'completion': None},
            developer=pending(callback, executor), completion=completion, **extra)

    def repair(self, original_id, **packet_changes):
        task = self.task()
        packet = repair_packet(original_id, **packet_changes)
        packet['executor_task'] = 'executor-' + task['task_id'][:12]
        self.bind(task, start_failure.canonical(packet))
        return task['task_id']

    def root_task(self):
        task = self.task()
        line = {'lineage_id': task['task_id'], 'kind': 'root', 'parent': None,
                'replaces': [], 'state': 'OPEN', 'reason': None,
                'base_sha': MAIN, 'recorded_at': AT}
        record_lineage(self.store, task['task_id'], line)
        return self.store.get(task['task_id'])

    def member_task(self, root_id, kind):
        task = self.task()
        line = {'lineage_id': root_id, 'kind': kind, 'parent': root_id,
                'replaces': [new_id()] if kind == 'replan' else [],
                'state': 'OPEN', 'reason': None, 'base_sha': MAIN, 'recorded_at': AT}
        record_lineage(self.store, task['task_id'], line)
        return self.store.get(task['task_id'])

    def key(self, task_id):
        return lineage_counts.debug_lineage_key(self.store, self.store.get(task_id))

    def used(self, task_id):
        record = self.store.get(task_id)
        return lineage_counts.chain_debug_used(
            self.store, record, record.get('high_debug', []),
            high_debug._debug_entry_used)


class DebugLineageRepairTests(RepairLineageCase):
    def test_repair_task_takes_the_original_debug_lineage_key(self):
        original = self.task()['task_id']
        repair = self.repair(original)
        self.assertEqual(self.key(repair), original)
        self.assertEqual(self.key(repair), self.key(original))
        members = lineage_counts.debug_lineage_members(
            self.store, self.store.get(repair))
        self.assertEqual(set(members), {original, repair})
        # The high-debug lock key is the same value as the lineage key.
        self.assertEqual(self.key(repair), lineage_counts.debug_lineage_key(
            self.store, self.store.get(original)))

    def test_repair_under_a_child_or_replan_takes_the_root_key(self):
        root = self.root_task()['task_id']
        for kind in ('child', 'replan'):
            with self.subTest(kind=kind):
                member = self.member_task(root, kind)['task_id']
                repair = self.repair(member)
                self.assertEqual(self.key(member), root)
                self.assertEqual(self.key(repair), root)
                members = lineage_counts.debug_lineage_members(
                    self.store, self.store.get(repair))
                self.assertIn(root, members)
                self.assertIn(member, members)
                self.assertIn(repair, members)

    def test_one_debug_on_either_task_counts_once_in_the_shared_chain(self):
        original = self.task()['task_id']
        repair = self.repair(original)
        self.store.append_high_debug(repair, used_entry())
        self.assertEqual(self.used(repair), (1, 1))
        self.assertEqual(self.used(original), (1, 1))
        self.store.append_high_debug(original, used_entry())
        self.assertEqual(self.used(repair), (2, 2))
        self.assertEqual(self.used(original), (2, 2))
        sibling = self.repair(original)
        self.store.append_high_debug(sibling, used_entry())
        self.assertEqual(self.used(original), (3, 3))
        self.assertEqual(self.used(sibling), (3, 3))

    def test_unusable_or_dangling_packet_gives_none(self):
        missing = repair_packet(new_id())
        del missing['findings_count']
        for value in ('not a packet', start_failure.canonical(missing),
                      json.dumps(repair_packet(new_id()), sort_keys=True), 7, None):
            with self.subTest(value=type(value).__name__):
                task = {'task_id': new_id(), 'repair_packet': value}
                self.assertIsNone(lineage_counts.debug_lineage_key(self.store, task))
        dangling = self.repair(new_id())
        self.assertIsNone(self.key(dangling))
        self.assertIsNone(lineage_counts.debug_lineage_members(
            self.store, self.store.get(dangling)))

    def test_repair_of_a_repair_resolves_once_and_a_cycle_gives_none(self):
        original = self.task()['task_id']
        first = self.repair(original)
        second = self.repair(first)
        self.assertEqual(self.key(second), original)
        members = lineage_counts.debug_lineage_members(
            self.store, self.store.get(second))
        self.assertEqual({original, first, second} - set(members), set())
        self.store.append_high_debug(second, used_entry())
        self.assertEqual(self.used(original), (1, 1))
        # A cycle is refused (None), never followed forever.
        a, b = self.task()['task_id'], self.task()['task_id']
        self.bind(self.store.get(a), start_failure.canonical(
            repair_packet(b, executor_task='executor-' + a[:12])))
        self.bind(self.store.get(b), start_failure.canonical(
            repair_packet(a, executor_task='executor-' + b[:12])))
        self.assertIsNone(self.key(a))
        self.assertIsNone(self.key(b))

    def test_repair_resolution_is_bounded(self):
        original = self.task()['task_id']
        previous = original
        for _ in range(lineage_counts.MAX_CHAIN + 1):
            previous = self.repair(previous)
        self.assertIsNone(self.key(previous))

    def test_tasks_without_repair_packet_keep_their_key_and_repair_counts(self):
        plain = self.task()['task_id']
        self.assertEqual(self.key(plain), plain)
        repair = self.repair(plain)
        record = self.store.get(repair)
        counts = lineage_counts.lineage_counts(
            self.store, record, base_family=FAMILY)
        self.assertEqual(counts['chain'], [repair])
        self.assertEqual(lineage_counts.chain_review_repairs(self.store, record), 0)


if __name__ == '__main__':
    unittest.main()
