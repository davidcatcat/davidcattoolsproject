"""#2602 item (r) r1: reuse validation of unchanged TaskStore reads.

The tests use a real temporary TaskStore (file checks, JSON parse and bytes) and
count calls to the record validator. The validator itself is replaced only to
count and to give one deterministic refusal code; the store's read path, the
repository-config read and the config parser are real. No test data contains a
credential.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from llmtool import config, lineage_counts, task_store
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore

TASK_ID = '0123456789abcdef0123456789abcdef'


@unittest.skipUnless(os.name == 'posix', 'POSIX ownership and O_NOFOLLOW required')
class StoreReadMemoTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / 'state' / 'tasks'
        self.root.mkdir(parents=True, mode=0o700)
        self.store = TaskStore(self.root)

    def write_record(self, **fields):
        record = {'task_id': TASK_ID, 'nested': {'value': 1}, **fields}
        path = self.root / (TASK_ID + '.json')
        raw = json.dumps(record, sort_keys=True).encode('utf-8')
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw)
        return record

    def counting_validator(self):
        calls = []

        def validate(record, *, strict=True, repository_pattern=None):
            calls.append(record.get('payload'))
            if record.get('invalid') is True:
                task_store._fail('TASK_STORE_INVALID')
            return record

        return calls, validate

    def test_r1_second_full_scan_validates_nothing(self):
        self.write_record(payload='one')
        calls, validate = self.counting_validator()
        with patch.object(task_store, '_validate', side_effect=validate):
            first = lineage_counts.store_records(self.store)
            after_first = len(calls)
            second = lineage_counts.store_records(self.store)
        self.assertEqual(after_first, 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(first, second)
        self.assertEqual(first[TASK_ID]['payload'], 'one')

    def test_r2_changed_record_is_validated_again(self):
        self.write_record(payload='one')
        calls, validate = self.counting_validator()
        with patch.object(task_store, '_validate', side_effect=validate):
            self.store.get(TASK_ID)
            self.write_record(payload='two')
            changed = self.store.get(TASK_ID)
            self.assertEqual(len(calls), 2)
            self.assertEqual(changed['payload'], 'two')
            self.write_record(payload='bad', invalid=True)
            with self.assertRaises(LLMToolError) as caught:
                self.store.get(TASK_ID)
            self.assertEqual(caught.exception.task_store_error_code,
                             'TASK_STORE_INVALID')
            self.assertEqual(len(calls), 3)
            with self.assertRaises(LLMToolError) as again:
                self.store.get(TASK_ID)
            self.assertEqual(again.exception.task_store_error_code,
                             'TASK_STORE_INVALID')
            self.assertEqual(len(calls), 4)

    def test_r3_returned_records_are_independent(self):
        self.write_record(payload='one')
        calls, validate = self.counting_validator()
        with patch.object(task_store, '_validate', side_effect=validate):
            first = self.store.get(TASK_ID)
            first['payload'] = 'mutated'
            first['nested']['value'] = 99
            second = self.store.get(TASK_ID)
        self.assertEqual(second['payload'], 'one')
        self.assertEqual(second['nested']['value'], 1)
        self.assertEqual(len(calls), 1)

    def test_r4_a_new_instance_validates_again(self):
        self.write_record(payload='one')
        calls, validate = self.counting_validator()
        with patch.object(task_store, '_validate', side_effect=validate):
            self.store.get(TASK_ID)
            other = TaskStore(self.root)
            other.get(TASK_ID)
        self.assertEqual(len(calls), 2)

    def test_r5_records_without_owner_ref_do_not_read_repository_config(self):
        self.write_record(payload='one')
        calls, validate = self.counting_validator()
        first = SimpleNamespace(pattern='pattern-one')
        second = SimpleNamespace(pattern='pattern-two')
        with patch.object(task_store, '_validate', side_effect=validate):
            with patch('llmtool.worker_dispatch.owner_comment_ref',
                       return_value=first):
                self.store.get(TASK_ID)
            with patch('llmtool.worker_dispatch.owner_comment_ref',
                       return_value=second):
                self.store.get(TASK_ID)
        self.assertEqual(len(calls), 1)

    def test_r5_optional_owner_ref_does_not_read_repository_config(self):
        self.write_record(payload='one',
                          developer={'provider_profile': 'claude',
                                     'owner_assignment_ref': None})
        calls, validate = self.counting_validator()
        with patch.object(task_store, '_validate', side_effect=validate):
            with patch('llmtool.worker_dispatch.owner_comment_ref',
                       side_effect=AssertionError('config read')):
                self.store.get(TASK_ID)
        self.assertEqual(len(calls), 1)

    def test_r6_repository_config_is_part_of_validation_reuse(self):
        self.write_record(payload='one',
                          developer={'provider_profile': 'claude',
                                     'owner_assignment_ref': 'ref-one'})
        calls, validate = self.counting_validator()
        first = SimpleNamespace(pattern='pattern-one')
        second = SimpleNamespace(pattern='pattern-two')
        with patch.object(task_store, '_validate', side_effect=validate):
            with patch('llmtool.worker_dispatch.owner_comment_ref',
                       return_value=first):
                self.store.get(TASK_ID)
                self.store.get(TASK_ID)
            self.assertEqual(len(calls), 1)
            with patch('llmtool.worker_dispatch.owner_comment_ref',
                       return_value=second):
                changed = self.store.get(TASK_ID)
        self.assertEqual(len(calls), 2)
        self.assertEqual(changed['payload'], 'one')


class ConfigReadCacheTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.path = self.base / 'config.yaml'

    def test_r6_unchanged_config_is_parsed_once_and_changed_config_again(self):
        self.path.write_text('value: one\n', encoding='utf-8')
        with patch.object(yaml, 'load', wraps=yaml.load) as load:
            first = config._read_yaml(self.path, 'test config')
            second = config._read_yaml(self.path, 'test config')
            self.assertEqual(load.call_count, 1)
            self.assertEqual(first, {'value': 'one'})
            self.assertEqual(second, {'value': 'one'})
            first['value'] = 'mutated'
            self.assertEqual(config._read_yaml(self.path, 'test config'),
                             {'value': 'one'})
            self.path.write_text('value: second-value\n', encoding='utf-8')
            self.assertEqual(config._read_yaml(self.path, 'test config'),
                             {'value': 'second-value'})
            self.assertEqual(load.call_count, 2)

    def test_r6_malformed_config_refuses_every_time(self):
        self.path.write_text('value: [unclosed\n', encoding='utf-8')
        for _ in range(2):
            with self.assertRaises(LLMToolError) as caught:
                config._read_yaml(self.path, 'test config')
            self.assertIn('malformed YAML', str(caught.exception))

    def test_r6_repository_config_rereads_user_config_and_trust(self):
        home = self.base / 'home'
        user = home / '.config' / 'llmtool' / 'config.yaml'
        user.parent.mkdir(parents=True)
        user.write_text('generation: [unclosed\n', encoding='utf-8')
        with self.assertRaises(config.RepositoryConfigError) as caught:
            config.repository_config(home)
        self.assertEqual(caught.exception.code, 'TRUST_REPOSITORY_CONFIG_INVALID')

        user.write_text('generation:\n  max_tokens: 100\n', encoding='utf-8')
        trusted_one = SimpleNamespace(repository={
            'slug': 'example-org/example-repo',
            'approval_refs': {'worker_dispatch_issue': 1,
                              'worker_dispatch_evidence_pr': 2}})
        trusted_two = SimpleNamespace(repository={
            'slug': 'example-org/example-repo',
            'approval_refs': {'worker_dispatch_issue': 3,
                              'worker_dispatch_evidence_pr': 4}})
        with patch('llmtool.trust.load_trust', return_value=trusted_one):
            first = config.repository_config(home)
        with patch('llmtool.trust.load_trust', return_value=trusted_two):
            second = config.repository_config(home)
        self.assertEqual(first['dispatch_issue'], 1)
        self.assertEqual(second['dispatch_issue'], 3)

        user.write_text('repository:\n  slug: example-org/example-repo\n'
                        '  dispatch_issue: 1\n  dispatch_evidence_pr: 2\n',
                        encoding='utf-8')
        with self.assertRaises(config.RepositoryConfigError) as caught:
            config.repository_config(home)
        self.assertEqual(caught.exception.code, 'TRUST_LEGACY_REPOSITORY_CONFIG')


if __name__ == '__main__':
    unittest.main()
