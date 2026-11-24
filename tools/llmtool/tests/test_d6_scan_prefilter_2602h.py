"""#2602h: D6 holder scan prefilters with lighter read before full validation."""

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import review_trust_fixture
import test_task_store
import worker_policy_fixture
from llmtool import task_store as task_store_module
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore, new_id


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


class D6ScanPrefilterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'tasks'
        self.store = TaskStore(self.root)
        self.policy_path = worker_policy_fixture.install(self)

    def _d6_task(self, executor_task_id=None, *, responded=False):
        task = test_task_store.TaskStoreTests._codex_dispatch_task(self)
        if executor_task_id is not None:
            task['completion'] = {
                'state': 'WAITING_EXECUTOR',
                'executor_task_id': executor_task_id,
                'reviewer_task_id': None,
                'evidence': None,
                'missing_gates': [],
                'pm_dispatch': 'NOT_STARTED',
                'pm_response': None,
                'pm_recommendation': None,
                'pm_usage': None,
                'pm_error_code': None,
            }
            if responded:
                task['developer']['state'] = 'DEVELOPER_RESPONDED'
                task['developer']['provider_type'] = 'codex-cli'
                task['developer']['artifact'] = '--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b\n'
                task['developer']['artifact_sha256'] = hashlib.sha256(
                    task['developer']['artifact'].encode('utf-8')
                ).hexdigest()
        return task

    def _write_raw_task(self, task_id, raw_bytes):
        self.store._ensure_root()
        path = self.root / f'{task_id}.json'
        path.write_bytes(raw_bytes)
        os.chmod(path, 0o600)

    def _write_invalid_validated_record(self, task):
        bad = dict(task)
        bad['record_sha256'] = '0' * 64
        self._write_raw_task(task['task_id'], task_store_module._json_bytes(bad))

    def test_conflicting_holder_is_still_refused(self):
        holder = self._d6_task('d6-scan-conflict')
        self.store.create(holder)
        claimant = self._d6_task('d6-scan-conflict')
        with self.assertRaises(LLMToolError) as caught:
            self.store.create(claimant)
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_D6_ALREADY_BOUND')
        self.assertNotIn(claimant['task_id'], self.store.task_ids())

    def test_unrelated_record_failing_full_validation_no_longer_blocks_claim(self):
        unrelated = self._d6_task('d6-other-unrelated')
        self._write_invalid_validated_record(unrelated)
        claimant = self._d6_task('d6-scan-allowed')
        saved = self.store.create(claimant)
        self.assertEqual(saved['task_id'], claimant['task_id'])

    def test_unrelated_unparseable_record_still_fails_closed(self):
        bad_id = new_id()
        self._write_raw_task(bad_id, b'{not valid json\n')
        claimant = self._d6_task('d6-scan-blocked')
        with self.assertRaises(LLMToolError) as caught:
            self.store.create(claimant)
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_D6_BINDING_SCAN_FAILED')
        self.assertNotIn(claimant['task_id'], self.store.task_ids())

    def test_candidate_holder_failing_full_validation_still_fails_closed(self):
        candidate = self._d6_task('d6-scan-candidate')
        self._write_invalid_validated_record(candidate)
        claimant = self._d6_task('d6-scan-candidate')
        with self.assertRaises(LLMToolError) as caught:
            self.store.create(claimant)
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_D6_BINDING_SCAN_FAILED')
        self.assertNotIn(claimant['task_id'], self.store.task_ids())

    def test_fully_validated_read_called_only_for_candidates(self):
        unrelated_ids = []
        for idx in range(15):
            unrelated = self._d6_task(f'd6-unrelated-{idx}')
            self.store.create(unrelated)
            unrelated_ids.append(unrelated['task_id'])
        candidate = self._d6_task('d6-scan-target', responded=True)
        self.store.create(candidate)

        claimant = self._d6_task('d6-scan-target')
        get_calls = []
        real_get = self.store.get

        def counting_get(task_id):
            get_calls.append(task_id)
            return real_get(task_id)

        with patch.object(self.store, 'get', side_effect=counting_get):
            saved = self.store.create(claimant)

        self.assertEqual(saved['task_id'], claimant['task_id'])
        self.assertEqual(get_calls, [candidate['task_id']])
        for uid in unrelated_ids:
            self.assertNotIn(uid, get_calls)

    def test_unicode_escaped_d6_holder_is_still_refused(self):
        d6_id = 'd6-scan-unicode'
        holder = self.store.create(self._d6_task(d6_id))
        holder_path = self.root / f"{holder['task_id']}.json"
        raw = holder_path.read_bytes()
        literal = f'"{d6_id}"'.encode('utf-8')
        escaped = ('"' + ''.join(f'\\u{ord(ch):04x}' for ch in d6_id) + '"').encode('utf-8')
        self.assertIn(literal, raw)
        escaped_raw = raw.replace(literal, escaped)
        self.assertNotIn(d6_id.encode('utf-8'), escaped_raw)
        self._write_raw_task(holder['task_id'], escaped_raw)

        claimant = self._d6_task(d6_id)
        with self.assertRaises(LLMToolError) as caught:
            self.store.create(claimant)
        self.assertEqual(caught.exception.task_store_error_code, 'TASK_D6_ALREADY_BOUND')
        self.assertNotIn(claimant['task_id'], self.store.task_ids())


if __name__ == '__main__':
    unittest.main()
