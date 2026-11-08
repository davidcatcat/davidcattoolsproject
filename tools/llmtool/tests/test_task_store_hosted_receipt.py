"""Remote test receipts with an optional GitHub Actions block (#1257 R2b1).

All values are synthetic; no credential-shaped strings are used.
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from llmtool.errors import LLMToolError
from llmtool import task_store as task_store_module
from llmtool.task_store import TaskStore, new_id
from llmtool.task_submission_cli import _record

import review_trust_fixture


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


INVALID = 'REMOTE_TEST_RECEIPT_INVALID'
LEGACY_LOG_PATH = '/tmp/llmtool-remote-log.Abc123/remote-test.log'


def actions_block(**overrides):
    block = {
        'run_id': 1001, 'run_attempt': 1, 'job_id': 2002, 'check_run_id': 3003,
        'workflow_path': '.github/workflows/llmtool-remote-test.yml',
        'workflow_sha': 'e' * 40, 'labels': ['ubuntu-24.04'],
        'runner_group': 'GitHub Actions', 'nonce': 'f' * 32,
    }
    block.update(overrides)
    return block


def receipt_digest(receipt):
    body = {key: value for key, value in receipt.items() if key != 'receipt_digest'}
    return hashlib.sha256(json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False).encode() + b'\n').hexdigest()


def receipt(task, *, failed=False, excerpt=None, actions=None, **overrides):
    """One synthetic receipt; ``actions`` and ``excerpt`` add optional keys."""
    value = {
        'result': 'PASS', 'exit_code': 0, 'source_sha': 'a' * 40,
        'tree': 'b' * 40, 'fetch_head': 'a' * 40,
        'modules': ['test_task_store'], 'identity': {'uid': 65534},
        'started_at': '2026-11-09T00:00:00Z', 'ended_at': '2026-11-09T00:00:01Z',
        'elapsed_s': 1.0, 'ran_total': 1, 'final': ['OK'], 'failures': [],
        'log_bytes': 0, 'log_sha256': 'c' * 64, 'remote_log_path': None,
        'task_id': task['task_id'], 'run_id': task['run_id'],
        'executor_task': 'd6-task', 'pm_session': task['pm_session_id'],
        'command_digest': 'd' * 64,
    }
    if failed:
        value.update(result='FAIL', exit_code=1, final=['FAILED (failures=1)'],
                     failures=['test_task_store'], log_bytes=1)
    if excerpt is not None:
        value['failure_excerpt'] = excerpt
    if actions is not None:
        value['actions'] = actions
    value.update(overrides)
    value['receipt_digest'] = receipt_digest(value)
    return value


class HostedReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TaskStore(Path(self.temp.name).resolve() / 'tasks')
        self.task = self._task()
        self.store.create(self.task)
        # One stored legacy receipt fixes the record shape for record validation.
        self.legacy = receipt(self.task, remote_log_path=LEGACY_LOG_PATH)
        self.store.append_remote_test_receipt(self.task['task_id'], self.legacy)
        self.saved = self.store.get(self.task['task_id'])

    @staticmethod
    def _task():
        request = 'Bounded non-sensitive hosted receipt task.'
        task_id, run_id = new_id(), new_id()
        request_sha = hashlib.sha256(request.encode()).hexdigest()
        task = _record(
            task_id, run_id, 'pm-' + new_id(), 'pm-profile', request,
            state='PM_RESPONDED', provider_type='openai-compatible', model='pm-model',
            backend='NO_NATIVE_SESSION_ID_EXPOSED', request_sha=request_sha)
        task.update(
            pm_route_binding={'type': 'openai-compatible', 'model': 'pm-model',
                              'base_url': None},
            response='PM returned a bounded task.', error_code=None,
            pm_assignment={'schema_version': 1, 'route': 'codex', 'risk': 'LOW',
                           'goal': 'Produce a proposal diff.',
                           'acceptance': ['the proposal is recorded']},
        )
        return task

    def _record_with(self, *receipts):
        value = dict(self.saved, remote_test_receipts=[self.legacy, *receipts])
        value['record_sha256'] = task_store_module._digest(value)
        return value

    def _assert_refused(self, bad, marker=None):
        """``bad`` is refused by append and by record validation, without echo."""
        before = self.store.get(self.task['task_id'])
        with self.assertRaises(LLMToolError) as caught:
            self.store.append_remote_test_receipt(self.task['task_id'], bad)
        self.assertEqual(caught.exception.task_store_error_code, INVALID)
        if marker is not None:
            self.assertNotIn(marker, str(caught.exception))
        self.assertEqual(self.store.get(self.task['task_id']), before)
        with self.assertRaises(LLMToolError) as caught:
            task_store_module._validate_remote_test_receipts(
                dict(before, remote_test_receipts=[bad]))
        self.assertEqual(caught.exception.task_store_error_code, INVALID)
        with self.assertRaises(LLMToolError) as caught:
            task_store_module._validate(self._record_with(bad))
        self.assertEqual(caught.exception.task_store_error_code, INVALID)
        if marker is not None:
            self.assertNotIn(marker, str(caught.exception))

    def test_constants_are_named_and_exact(self):
        self.assertEqual(task_store_module._REMOTE_TEST_ACTIONS_WORKFLOW_PATH,
                         '.github/workflows/llmtool-remote-test.yml')
        self.assertEqual(list(task_store_module._REMOTE_TEST_ACTIONS_LABELS),
                         ['ubuntu-24.04'])
        self.assertEqual(task_store_module._REMOTE_TEST_ACTIONS_RUNNER_GROUP,
                         'GitHub Actions')
        self.assertEqual(task_store_module._REMOTE_TEST_ACTIONS_FIELDS, frozenset((
            'run_id', 'run_attempt', 'job_id', 'check_run_id', 'workflow_path',
            'workflow_sha', 'labels', 'runner_group', 'nonce')))
        self.assertEqual(task_store_module._COMPLETION_SOURCES, frozenset((
            'DIRECT_D6_DEVELOPER', 'HOST_WORKER', 'REVIEWER', 'EC2_REMOTE_TEST',
            'GITHUB', 'HIGH_DEBUG', 'GITHUB_REMOTE_TEST')))

    def test_legacy_ec2_receipt_stays_valid(self):
        self.assertEqual(self.saved['remote_test_receipts'], [self.legacy])
        self.assertNotIn('actions', self.legacy)
        task_store_module._validate(self.saved)
        task_store_module._validate_remote_test_receipts(self.saved)
        self.assertEqual(self.store.append_remote_test_receipt(
            self.task['task_id'], self.legacy), self.legacy)
        self.assertEqual(self.store.get(self.task['task_id'])['remote_test_receipts'],
                         [self.legacy])

    def test_four_receipt_shapes_are_accepted_and_replay_idempotently(self):
        shapes = (
            ('base', receipt(self.task, failed=True)),
            ('base plus failure_excerpt',
             receipt(self.task, failed=True, excerpt='synthetic failure line')),
            ('base plus actions', receipt(self.task, actions=actions_block())),
            ('base plus both',
             receipt(self.task, failed=True, excerpt='synthetic failure line',
                     actions=actions_block(run_attempt=2))),
        )
        appended = [self.legacy]
        for label, item in shapes:
            with self.subTest(label):
                self.assertEqual(self.store.append_remote_test_receipt(
                    self.task['task_id'], item), item)
                self.assertEqual(self.store.append_remote_test_receipt(
                    self.task['task_id'], item), item)
                appended.append(item)
                saved = self.store.get(self.task['task_id'])
                self.assertEqual(saved['remote_test_receipts'], appended)
                task_store_module._validate(saved)
                task_store_module._validate_remote_test_receipts(saved)

    def test_actions_boundaries_are_accepted(self):
        top = 2 ** 53
        for label, block in (
                ('lowest ids and attempt',
                 actions_block(run_id=1, job_id=1, check_run_id=1, run_attempt=1)),
                ('highest ids and attempt',
                 actions_block(run_id=top, job_id=top, check_run_id=top, run_attempt=100)),
        ):
            with self.subTest(label):
                item = receipt(self.task, actions=block)
                task_store_module._validate(self._record_with(item))
                self.assertEqual(self.store.append_remote_test_receipt(
                    self.task['task_id'], item), item)

    def test_existing_receipt_checks_still_apply_with_actions(self):
        cases = (
            ('PASS with failure_excerpt',
             receipt(self.task, excerpt='synthetic failure line', actions=actions_block())),
            ('PASS with non-zero exit', receipt(self.task, exit_code=1,
                                                actions=actions_block())),
            ('bad module name', receipt(self.task, modules=['not-a-module'],
                                        actions=actions_block())),
            ('other task', receipt(self.task, task_id=new_id(), actions=actions_block())),
            ('other pm session', receipt(self.task, pm_session='pm-' + new_id(),
                                         actions=actions_block())),
        )
        for label, bad in cases:
            with self.subTest(label):
                before = self.store.get(self.task['task_id'])
                with self.assertRaises(LLMToolError) as caught:
                    self.store.append_remote_test_receipt(self.task['task_id'], bad)
                self.assertEqual(caught.exception.task_store_error_code, INVALID)
                self.assertEqual(self.store.get(self.task['task_id']), before)

    def test_invalid_actions_are_refused(self):
        missing = actions_block()
        missing.pop('nonce')
        cases = [
            ('missing field', missing),
            ('extra field', actions_block(extra='value')),
            ('wrong workflow path',
             actions_block(workflow_path='.github/workflows/zzmarker.yml')),
            ('workflow path with suffix',
             actions_block(workflow_path='.github/workflows/llmtool-remote-test.yml\n')),
            ('wrong label', actions_block(labels=['zzmarker-label'])),
            ('extra label', actions_block(labels=['ubuntu-24.04', 'self-hosted'])),
            ('no labels', actions_block(labels=[])),
            ('labels as string', actions_block(labels='ubuntu-24.04')),
            ('wrong runner group', actions_block(runner_group='zzmarker-group')),
            ('runner group case', actions_block(runner_group='github actions')),
            ('nonce uppercase', actions_block(nonce='F' * 32)),
            ('nonce short', actions_block(nonce='f' * 31)),
            ('nonce long', actions_block(nonce='f' * 33)),
            ('nonce not hex', actions_block(nonce='z' * 32)),
            ('nonce not a string', actions_block(nonce=None)),
            ('workflow sha uppercase', actions_block(workflow_sha='E' * 40)),
            ('workflow sha short', actions_block(workflow_sha='e' * 39)),
            ('attempt zero', actions_block(run_attempt=0)),
            ('attempt too high', actions_block(run_attempt=101)),
            ('attempt bool', actions_block(run_attempt=True)),
            ('attempt float', actions_block(run_attempt=1.0)),
        ]
        for field in ('run_id', 'job_id', 'check_run_id'):
            for label, value in (('zero', 0), ('negative', -1), ('too large', 2 ** 53 + 1),
                                 ('bool', True), ('float', 1.0), ('string', '1')):
                cases.append((f'{field} {label}', actions_block(**{field: value})))
        for label, block in cases:
            with self.subTest(label):
                self._assert_refused(receipt(self.task, actions=block), marker='zzmarker')

    def test_non_dict_actions_are_refused(self):
        # Set the key directly: the receipt() helper treats actions=None as absent.
        for label, value in (
                ('actions null', None),
                ('actions a string', 'zzmarker-actions'),
                ('actions an empty list', []),
                ('actions a list of the block', [actions_block()]),
                ('actions an int', 1001)):
            with self.subTest(label):
                bad = receipt(self.task)
                bad['actions'] = value
                bad['receipt_digest'] = receipt_digest(bad)
                self.assertIn('actions', bad)
                self._assert_refused(bad, marker='zzmarker')

    def test_actions_require_null_remote_log_path(self):
        self._assert_refused(receipt(self.task, actions=actions_block(),
                                     remote_log_path=LEGACY_LOG_PATH))

    def test_receipt_digest_covers_actions(self):
        hosted = receipt(self.task, actions=actions_block())
        plain = receipt(self.task)
        self.assertNotEqual(hosted['receipt_digest'], plain['receipt_digest'])
        for label, change in (
                ('changed run id', {'run_id': 1002}),
                ('changed nonce', {'nonce': '0' * 32}),
                ('changed attempt', {'run_attempt': 2})):
            with self.subTest(label):
                tampered = dict(hosted, actions=dict(hosted['actions'], **change))
                self._assert_refused(tampered)
        added = dict(plain, actions=actions_block())
        self._assert_refused(added)
        removed = dict(hosted)
        removed.pop('actions')
        self._assert_refused(removed)

    def test_completion_source_github_remote_test_is_accepted(self):
        args = dict(event_id=new_id(), run_id=new_id(), session_id='pm-session',
                    source_sha='a' * 40, sequence=1, payload_sha256='b' * 64)
        with self.assertRaises(LLMToolError) as caught:
            self.store.record_completion_event(new_id(), source='NOT_A_SOURCE', **args)
        self.assertEqual(caught.exception.task_store_error_code, 'COMPLETION_EVENT_INVALID')
        # GitHub metadata stays limited to the GITHUB source.
        with self.assertRaises(LLMToolError) as caught:
            self.store.record_completion_event(
                new_id(), source='GITHUB_REMOTE_TEST',
                github_metadata={'issue_number': 1, 'comment_id': 1,
                                 'marker_type': 'x', 'marker_status': 'x',
                                 'merge_sha': None}, **args)
        self.assertEqual(caught.exception.task_store_error_code, 'COMPLETION_EVENT_INVALID')
        # The source passes argument validation; the unknown Task fails later.
        with self.assertRaises(Exception) as caught:
            self.store.record_completion_event(new_id(), source='GITHUB_REMOTE_TEST',
                                               **args)
        self.assertNotEqual(getattr(caught.exception, 'task_store_error_code', None),
                            'COMPLETION_EVENT_INVALID')


if __name__ == '__main__':
    unittest.main()
