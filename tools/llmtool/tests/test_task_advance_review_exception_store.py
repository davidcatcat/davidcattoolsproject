"""Owner-exception merge against a real TaskStore (#1519).

The owner-exception merge path writes ``review_exception`` together with the
MERGED ``merge_attempt``. These tests use a real temporary TaskStore so a field
the store refuses shows up as MERGE_RESULT_RECORD_FAILED here.
"""

import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llmtool import owner_exception, task_advance
from llmtool import task_store as task_store_module
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore, new_id
from llmtool.task_verification import AcceptanceCriterion, CriterionKind, TaskContract

HEAD = 'a' * 40
BASE = 'c' * 40
MAIN = 'b' * 40
NUMBER = 42
URL = 'https://github.com/example/repo/issues/1253#issuecomment-6788268483'


def _record():
    request = 'Owner-exception merge record test'
    task_id, run_id = new_id(), new_id()
    request_sha = hashlib.sha256(request.encode()).hexdigest()
    contract = TaskContract(
        schema_version=1, task_id=task_id, contract_version=1,
        previous_digest=None, goal='PM intake request sha256:' + request_sha,
        authorized_scope=('pm-planning',), forbidden_scope=(),
        criteria=(AcceptanceCriterion('pm_response', CriterionKind.ARTIFACT,
                                      'Record the PM reply or unavailable status.'),),
    )
    return {
        'schema_version': 1, 'task_id': task_id, 'run_id': run_id,
        'pm_session_id': 'pm-' + new_id(), 'pm_provider_profile': 'pm',
        'pm_provider_type': 'codex-cli', 'pm_model': 'gpt-6-luna',
        'backend_session_semantics': 'EPHEMERAL_SINGLE_CALL', 'native_session_id': None,
        'state': 'PM_PENDING', 'created_at': '2026-10-21T00:00:00Z',
        'updated_at': '2026-10-21T00:00:00Z', 'task_contract': contract.to_dict(),
        'task_binding': contract.binding(run_id).to_dict(), 'request': request,
        'request_sha256': request_sha, 'response': None, 'usage': None,
        'error_code': None, 'record_sha256': None,
    }


def _evidence():
    # The same shape owner_exception.verify() returns on a valid pass.
    return {
        'type': owner_exception.EXCEPTION_TYPE,
        'slot': 'S2a',
        'url': URL,
        'pr': NUMBER,
        'base': BASE,
        'head': HEAD,
        'files': [['docs/a.md', 'd' * 64, 2], ['docs/b.md', 'e' * 64, 1]],
    }


def _review_exception():
    return {'type': owner_exception.EXCEPTION_TYPE, 'url': URL, 'evidence': _evidence()}


class _GhRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, root, args):
        self.calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout=b'', stderr=b'')


def _github_get(path):
    if path == f'pulls/{NUMBER}':
        return {'number': NUMBER, 'state': 'open', 'merged': False,
                'mergeable': True, 'head': {'sha': HEAD}}
    if path == f'commits/{HEAD}/check-runs?per_page=100':
        return {'total_count': 1, 'check_runs': [
            {'id': 1, 'name': 'check (docs)', 'head_sha': HEAD,
             'status': 'completed', 'conclusion': 'success'}]}
    if path == f'pulls/{NUMBER}/files?per_page=100':
        return [{'filename': 'docs/a.md'}, {'filename': 'docs/b.md'}]
    if path == 'branches/main':
        return {'commit': {'sha': MAIN}}
    if path == f'compare/{MAIN}...{HEAD}':
        return {'status': 'identical'}
    raise ValueError(f'Unexpected github_get path: {path}')


class OwnerExceptionMergeRealStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TaskStore(Path(self.temp.name).resolve() / 'tasks')
        self.task = _record()
        self.store.create(self.task)

    def test_constants_match_owner_exception_module(self):
        self.assertEqual(task_store_module._REVIEW_EXCEPTION_TYPE,
                         owner_exception.EXCEPTION_TYPE)
        self.assertEqual(task_store_module._REVIEW_EXCEPTION_SLOTS, owner_exception.SLOTS)

    def test_owner_exception_merge_records_merged_and_review_exception(self):
        gh_runner = _GhRunner()
        record = self.store.get(self.task['task_id'])
        with patch.object(owner_exception, 'verify', return_value=(None, _evidence())), \
             patch.object(task_advance.mac_full_suite, 'request_full_suite',
                          return_value=task_advance.mac_full_suite.MAC_FULL_SUITE_DISPATCHED):
            outcome = task_advance._merge_once(
                store=self.store, record=record, number=NUMBER, head=HEAD,
                pr=_github_get(f'pulls/{NUMBER}'), events=[], github_get=_github_get,
                gh_runner=gh_runner, root='/nonexistent-root', owner_exception=URL,
                sleep_fn=lambda _seconds: None)
        self.assertEqual(outcome['step'], 'MERGED')
        self.assertNotIn('MERGE_RESULT_RECORD_FAILED', outcome['codes'])
        self.assertEqual(outcome['codes'], [])
        self.assertEqual(outcome['result']['review_exception'], _review_exception())
        merges = [args for args in gh_runner.calls if args[:2] == ['pr', 'merge']]
        self.assertEqual(merges, [['pr', 'merge', str(NUMBER), '--squash',
                                   '--match-head-commit', HEAD]])
        saved = self.store.get(self.task['task_id'])
        self.assertEqual(saved['merge_attempt']['state'], 'MERGED')
        self.assertEqual(saved['merge_attempt']['pr'], NUMBER)
        self.assertEqual(saved['merge_attempt']['head_sha'], HEAD)
        self.assertEqual(saved['review_exception'], _review_exception())

    def test_valid_review_exception_round_trips(self):
        current = self.store.get(self.task['task_id'])
        saved = self.store.compare_and_update(
            self.task['task_id'], expected={'record_sha256': current['record_sha256']},
            review_exception=_review_exception())
        self.assertEqual(saved['review_exception'], _review_exception())
        self.assertEqual(self.store.get(self.task['task_id'])['review_exception'],
                         _review_exception())

    def test_invalid_review_exception_shapes_are_refused(self):
        def with_evidence(**changes):
            item = _review_exception()
            item['evidence'] = dict(item['evidence'], **changes)
            return item

        without_slot = _review_exception()
        del without_slot['evidence']['slot']
        cases = {
            'not a dict': 'OWNER_EXCEPTION_DETERMINISTIC_REDACTION',
            'extra key': dict(_review_exception(), note='x'),
            'missing evidence': {'type': owner_exception.EXCEPTION_TYPE, 'url': URL},
            'wrong type': dict(_review_exception(), type='OTHER_EXCEPTION'),
            'bad url': dict(_review_exception(), url='https://example.com/x'),
            'evidence url differs': with_evidence(
                url='https://github.com/example/repo/issues/1253#issuecomment-1'),
            'evidence type differs': with_evidence(type='OTHER_EXCEPTION'),
            'missing slot': without_slot,
            'unknown slot': with_evidence(slot='S3'),
            'pr not int': with_evidence(pr='42'),
            'pr bool': with_evidence(pr=True),
            'pr zero': with_evidence(pr=0),
            'short head': with_evidence(head='a' * 39),
            'upper base': with_evidence(base='C' * 40),
            'no files': with_evidence(files=[]),
            'file not triple': with_evidence(files=[['docs/a.md', 'd' * 64]]),
            'file bad sha': with_evidence(files=[['docs/a.md', 'x' * 64, 1]]),
            'file zero count': with_evidence(files=[['docs/a.md', 'd' * 64, 0]]),
            'file absolute path': with_evidence(files=[['/etc/a.md', 'd' * 64, 1]]),
            'duplicate file': with_evidence(files=[['docs/a.md', 'd' * 64, 1],
                                                   ['docs/a.md', 'e' * 64, 1]]),
        }
        for label, value in cases.items():
            with self.subTest(label=label):
                current = self.store.get(self.task['task_id'])
                with self.assertRaises(LLMToolError) as caught:
                    self.store.compare_and_update(
                        self.task['task_id'],
                        expected={'record_sha256': current['record_sha256']},
                        review_exception=value)
                self.assertEqual(caught.exception.task_store_error_code,
                                 'TASK_REVIEW_EXCEPTION_INVALID')
                self.assertNotIn('review_exception', self.store.get(self.task['task_id']))


if __name__ == '__main__':
    unittest.main()
