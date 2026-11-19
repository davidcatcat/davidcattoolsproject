"""#1886: PM continuation repairs join the completion artifact chain.

Unit tests of task_submission_cli._completion_repair_artifact_segments over a
fake Task store with the D6 index. The D6 registration and Git ancestry are
injected; the real-Git regression tests for the PR #2407 and PR #2402 shapes
are in test_task_completion.py.
"""
import copy
import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llmtool import review_repair_recovery
from llmtool import task_submission_cli as submission_cli
from llmtool.errors import LLMToolError
from llmtool.review_repair import _worker_key

EXECUTOR = 'executor-d6'
ROOT_DIR, COMMON_DIR, BRANCH = '/repo/task', '/repo/.git', 'task-branch'
SOURCE, H1, H2, H3, H4 = ('a' * 40, 'b' * 40, 'c' * 40, 'd' * 40, 'e' * 40)
HISTORY = (SOURCE, H1, H2, H3, H4)
ROOT_ID = '1' * 32
PATH = 'sample.py'


def artifact(text):
    """A one-file proposal; the builder reads only its diff headers and digest."""
    body = f'diff --git a/{PATH} b/{PATH}\n# {text}\n'
    return body, hashlib.sha256(body.encode()).hexdigest()


class ChainStore:
    """A read-only Task store with get() and the D6 index TaskStore has."""

    def __init__(self, records):
        self.records = records

    def get(self, task_id):
        if task_id not in self.records:
            raise LLMToolError('task not found')
        return copy.deepcopy(self.records[task_id])

    def completion_d6_task_ids(self, executor_task_id):
        return sorted(task_id for task_id, record in self.records.items()
                      if type(record.get('completion')) is dict and
                      record['completion'].get('executor_task_id') == executor_task_id)


def root_task(claims=None):
    body, digest = artifact('original')
    task = {
        'schema_version': 2, 'task_id': ROOT_ID, 'run_id': '2' * 32,
        'task_binding': {'task_id': ROOT_ID, 'run_id': '2' * 32,
                         'contract_digest': '3' * 64},
        'developer': {'state': 'DEVELOPER_RESPONDED', 'source_sha': SOURCE,
                      'write_paths': [PATH], 'artifact': body, 'artifact_sha256': digest},
        'completion': {'executor_task_id': EXECUTOR, 'evidence': {}},
    }
    if claims is not None:
        task['review_repair_claims'] = claims
    return task


def continuation(task_id, continues, head, *, text=None, state='DEVELOPER_RESPONDED',
                 profile='codex', write_paths=None, audit='ALLOWED', executor=EXECUTOR):
    """A continuation Task as record-pm --continues and a delegate store it."""
    run_id, digest = task_id[::-1], hashlib.sha256(task_id.encode()).hexdigest()
    assignment = hashlib.sha256(('assignment' + task_id).encode()).hexdigest()[:32]
    body, body_digest = artifact(text or ('continuation ' + task_id))
    record = {
        'schema_version': 2, 'task_id': task_id, 'run_id': run_id, 'continues': continues,
        'task_binding': {'task_id': task_id, 'run_id': run_id, 'contract_digest': digest},
        'developer': {'state': state, 'provider_profile': profile,
                      'assignment_id': assignment, 'source_sha': head,
                      'executor_task_id': executor,
                      'write_paths': [PATH] if write_paths is None else write_paths,
                      'artifact': body if state == 'DEVELOPER_RESPONDED' else None,
                      'artifact_sha256': (body_digest if state == 'DEVELOPER_RESPONDED'
                                          else None)},
        'completion': {'executor_task_id': executor, 'evidence': {
            'task_id': task_id, 'run_id': run_id, 'contract_digest': digest,
            'root': ROOT_DIR, 'branch': BRANCH, 'common_dir': COMMON_DIR,
            'source_sha': head}},
        'dispatch_audit': [],
    }
    if audit is not None:
        record['dispatch_audit'].append({
            'attempt_id': hashlib.sha256(('attempt' + task_id).encode()).hexdigest()[:32],
            'worker_key': _worker_key(profile), 'action': 'DISPATCH', 'decision': audit,
            'reason_code': 'OWNER_POLICY_ALLOWED', 'policy_sha256': None,
            'at': '2026-11-20T00:00:00Z', 'assignment_id': assignment,
            'source_sha': head, 'contract_digest': digest})
    return record


def claim_repair(head, text):
    body, digest = artifact(text)
    return {'developer': {'state': 'DEVELOPER_RESPONDED', 'source_sha': head,
                          'artifact': body, 'artifact_sha256': digest}}


def segment(record, start, end):
    developer = record['developer']
    return (developer['artifact'], developer['artifact_sha256'], start, end)


def is_ancestor(_root, ancestor, descendant):
    """The injected D6 history: one line, SOURCE first."""
    if ancestor not in HISTORY or descendant not in HISTORY:
        return False
    return HISTORY.index(ancestor) <= HISTORY.index(descendant)


class ContinuationArtifactChainTests(unittest.TestCase):
    def setUp(self):
        self.assertIsInstance(_worker_key('codex'), str)
        self.result_head = H3

    def build(self, task, records, *, store=None, history=is_ancestor, **options):
        registration = SimpleNamespace(root=ROOT_DIR, branch=BRANCH, source_sha=SOURCE,
                                       result_sha=self.result_head)
        store = ChainStore({ROOT_ID: task, **records}) if store is None else store
        with patch.object(submission_cli.task_completion, 'read_d6_registration',
                          return_value=registration), \
                patch.object(submission_cli.task_completion, '_is_ancestor',
                             side_effect=history):
            return submission_cli._completion_repair_artifact_segments(
                task, store, executor_task=EXECUTOR, common_dir=COMMON_DIR,
                root=ROOT_DIR, branch=BRANCH, source_sha=SOURCE, **options)

    def assert_chain_invalid(self, task, records, **options):
        with self.assertRaises(LLMToolError) as raised:
            self.build(task, records, **options)
        self.assertEqual(raised.exception.completion_code, 'DEVELOPER_ARTIFACT_CHAIN_INVALID')

    def original(self, task, end):
        return segment(task, SOURCE, end)

    # A1 and A6: the real failure shapes complete.

    def test_pr_2407_shape_two_chained_continuations_without_root_claims(self):
        task = root_task()
        first = continuation('4' * 32, ROOT_ID, H1)
        second = continuation('5' * 32, '4' * 32, H2)
        segments = self.build(task, {'4' * 32: first, '5' * 32: second})
        self.assertEqual(segments, [self.original(task, H1), segment(first, H1, H2),
                                    segment(second, H2, H3)])

    def test_pr_2402_shape_continuation_before_the_recorded_review_repair(self):
        claims = [{'pr': 10, 'attempt': 1, 'state': 'DISPATCHED', 'head_sha': H2,
                   'repair_task_id': '6' * 32}]
        task = root_task(claims)
        repair = claim_repair(H2, 'review repair')
        later = continuation('4' * 32, ROOT_ID, H1)
        segments = self.build(task, {'6' * 32: repair, '4' * 32: later})
        self.assertEqual(segments, [self.original(task, H1), segment(later, H1, H2),
                                    segment(repair, H2, H3)])

    def test_review_ec2_and_continuation_repairs_mixed(self):
        self.result_head = H4
        claims = [{'pr': 10, 'family': 'pr-10', 'attempt': 1, 'state': 'DISPATCHED',
                   'head_sha': H1, 'repair_task_id': '6' * 32},
                  {'pr': 10, 'family': 'pr-10-ec2', 'attempt': 1, 'state': 'DISPATCHED',
                   'head_sha': H3, 'repair_task_id': '7' * 32}]
        task = root_task(claims)
        review = claim_repair(H1, 'review repair')
        ec2 = claim_repair(H3, 'test repair')
        middle = continuation('4' * 32, ROOT_ID, H2)
        segments = self.build(task, {'6' * 32: review, '7' * 32: ec2, '4' * 32: middle})
        self.assertEqual(segments, [self.original(task, H1), segment(review, H1, H2),
                                    segment(middle, H2, H3), segment(ec2, H3, H4)])

    def test_no_commit_continuation_is_skipped_only_when_the_head_did_not_move(self):
        task = root_task()
        refused = continuation('4' * 32, ROOT_ID, H1, state='BLOCKED')
        responded = continuation('5' * 32, '4' * 32, H1)
        segments = self.build(task, {'4' * 32: refused, '5' * 32: responded})
        self.assertEqual(segments, [self.original(task, H1), segment(responded, H1, H3)])
        moved = continuation('5' * 32, '4' * 32, H2)
        self.assert_chain_invalid(task, {'4' * 32: refused, '5' * 32: moved})

    def test_continuation_named_by_a_claim_is_one_repair_not_two(self):
        repair_id = '4' * 32
        claims = [{'pr': 10, 'attempt': 1, 'state': 'DISPATCHED', 'head_sha': H1,
                   'repair_task_id': repair_id}]
        task = root_task(claims)
        repair = continuation(repair_id, ROOT_ID, H1)
        segments = self.build(task, {repair_id: repair})
        self.assertEqual(segments, [self.original(task, H1), segment(repair, H1, H3)])

    # A3: a repair is admitted only on recorded authorization and lineage.

    def test_unauthorized_continuation_is_refused(self):
        task = root_task()
        for name, change in (
                ('no audit', lambda record: record.update(dispatch_audit=[])),
                ('blocked', lambda record: record['dispatch_audit'][0].update(
                    decision='BLOCKED')),
                ('other assignment', lambda record: record['dispatch_audit'][0].update(
                    assignment_id='9' * 32)),
                ('other head', lambda record: record['dispatch_audit'][0].update(
                    source_sha=H2)),
                ('other contract', lambda record: record['dispatch_audit'][0].update(
                    contract_digest='9' * 64)),
                ('other worker', lambda record: record['dispatch_audit'][0].update(
                    worker_key='other-worker'))):
            with self.subTest(name):
                record = continuation('4' * 32, ROOT_ID, H1)
                change(record)
                self.assert_chain_invalid(task, {'4' * 32: record})

    def test_wrong_task_binding_is_refused(self):
        task = root_task()
        evidence_changes = {'task_id': '9' * 32, 'run_id': '9' * 32,
                            'contract_digest': '9' * 64, 'root': '/repo/other',
                            'branch': 'other-branch', 'common_dir': '/repo/other/.git'}
        for key, value in evidence_changes.items():
            with self.subTest(evidence=key):
                record = continuation('4' * 32, ROOT_ID, H1)
                record['completion']['evidence'][key] = value
                self.assert_chain_invalid(task, {'4' * 32: record})
        with self.subTest('developer bound to another D6'):
            record = continuation('4' * 32, ROOT_ID, H1)
            record['developer']['executor_task_id'] = 'other-d6'
            self.assert_chain_invalid(task, {'4' * 32: record})
        with self.subTest('stored binding names another Task'):
            record = continuation('4' * 32, ROOT_ID, H1)
            record['task_binding']['task_id'] = '9' * 32
            self.assert_chain_invalid(task, {'4' * 32: record})

    def test_wrong_head_is_refused(self):
        task = root_task()
        record = continuation('4' * 32, ROOT_ID, H1)
        record['developer']['source_sha'] = H2
        self.assert_chain_invalid(task, {'4' * 32: record})
        for head in ('f' * 40, 'not-a-sha'):
            with self.subTest(head=head):
                record = continuation('4' * 32, ROOT_ID, head)
                self.assert_chain_invalid(task, {'4' * 32: record})

    def test_same_d6_and_head_without_lineage_is_not_admitted(self):
        task = root_task()
        unlinked = continuation('4' * 32, ROOT_ID, H1)
        del unlinked['continues']
        self.assertIsNone(self.build(task, {'4' * 32: unlinked}))
        other_root = continuation('8' * 32, '9' * 32, SOURCE)
        del other_root['continues']
        elsewhere = continuation('4' * 32, '8' * 32, H1)
        self.assertIsNone(self.build(task, {'8' * 32: other_root, '4' * 32: elsewhere}))
        other_d6 = continuation('4' * 32, ROOT_ID, H1, executor='other-d6')
        self.assertIsNone(self.build(task, {'4' * 32: other_d6}))

    def test_forked_cyclic_or_unreadable_lineage_is_refused(self):
        task = root_task()
        self.assert_chain_invalid(task, {'4' * 32: continuation('4' * 32, ROOT_ID, H1),
                                         '5' * 32: continuation('5' * 32, ROOT_ID, H2)})
        self.assert_chain_invalid(task, {'4' * 32: continuation('4' * 32, '5' * 32, H1),
                                         '5' * 32: continuation('5' * 32, '4' * 32, H2)})
        self.assert_chain_invalid(task, {'4' * 32: continuation('4' * 32, '9' * 32, H1)})

    def test_continuation_outside_its_own_scope_is_refused(self):
        task = root_task()
        record = continuation('4' * 32, ROOT_ID, H1, write_paths=['other.py'])
        self.assert_chain_invalid(task, {'4' * 32: record})

    def test_heads_not_on_one_line_are_refused(self):
        task = root_task()
        records = {'4' * 32: continuation('4' * 32, ROOT_ID, H1),
                   '5' * 32: continuation('5' * 32, '4' * 32, H2)}
        self.assert_chain_invalid(task, records,
                                  history=lambda _root, older, newer: older == newer)

    # Unchanged paths.

    def test_without_claims_or_continuations_the_single_artifact_path_stays(self):
        self.assertIsNone(self.build(root_task(), {}))

    def test_claude_route_and_index_free_stores_read_the_claims_only(self):
        task = root_task()
        records = {'4' * 32: continuation('4' * 32, ROOT_ID, H1)}
        self.assertIsNone(self.build(task, records, continuations=False))
        index_free = SimpleNamespace(get=ChainStore({ROOT_ID: task, **records}).get)
        self.assertIsNone(self.build(task, records, store=index_free))

    # A4: the builder reads; it never changes a repair record.

    def test_building_the_chain_changes_no_record(self):
        claims = [{'pr': 10, 'attempt': 1, 'state': 'DISPATCHED', 'head_sha': H2,
                   'repair_task_id': '6' * 32}]
        task = root_task(claims)
        records = {'6' * 32: claim_repair(H2, 'review repair'),
                   '4' * 32: continuation('4' * 32, ROOT_ID, H1)}
        store = ChainStore({ROOT_ID: task, **records})
        before = copy.deepcopy(store.records)
        self.assertEqual(len(self.build(task, records, store=store)), 3)
        self.assertEqual(store.records, before)
        self.assertEqual(task['review_repair_claims'], claims)


class DispatchAuthorizedTests(unittest.TestCase):
    """E3 keeps its behaviour after its check moved into dispatch_authorized."""

    def test_e3_refuses_without_an_allowed_dispatch_and_passes_with_one(self):
        repair = continuation('4' * 32, ROOT_ID, H1)
        root = {'developer': {'provider_profile': 'codex'}}
        claim = {'head_sha': H1}
        review_repair_recovery.check_authorization(root, repair, claim)
        repair['dispatch_audit'] = []
        with self.assertRaises(LLMToolError) as raised:
            review_repair_recovery.check_authorization(root, repair, claim)
        self.assertEqual(raised.exception.review_repair_code,
                         'REPAIR_RECOVERY_EVIDENCE_INVALID')

    def test_adapter_route_needs_its_adapter_authorization(self):
        record = continuation('4' * 32, ROOT_ID, H1, profile='opencode')
        if _worker_key('opencode') is None:
            self.skipTest('no opencode worker key')
        args = (record['developer'], record['task_binding'], record['dispatch_audit'], H1)
        self.assertFalse(review_repair_recovery.dispatch_authorized('opencode', *args))
        transition = record['dispatch_audit'][0]
        record['dispatch_audit'].append(dict(
            transition, attempt_id='c' * 32, authorization_id=transition['attempt_id']))
        self.assertTrue(review_repair_recovery.dispatch_authorized('opencode', *args))


if __name__ == '__main__':
    unittest.main()
