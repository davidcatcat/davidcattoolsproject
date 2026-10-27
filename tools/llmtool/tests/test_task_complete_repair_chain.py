import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from llmtool.errors import LLMToolError
from llmtool.task_submission_cli import _completion_repair_artifact_segments


SOURCE = '1' * 40
HEAD1 = '2' * 40
HEAD2 = '3' * 40
FINAL = '4' * 40


def _claim(attempt=1, state='DISPATCHED', repair_id='a' * 32, head=HEAD1):
    return {'attempt': attempt, 'state': state, 'repair_task_id': repair_id,
            'head_sha': head}


class RepairChainTests(unittest.TestCase):
    def setUp(self):
        self.registration = SimpleNamespace(root='/repo', branch='task',
            source_sha=SOURCE, result_sha=FINAL)
        original_artifact = 'diff --git a/allowed.py b/allowed.py\n'
        self.original = {'source_sha': SOURCE, 'artifact': original_artifact,
            'artifact_sha256': hashlib.sha256(original_artifact.encode()).hexdigest(),
            'write_paths': ['allowed.py']}
        repair_artifact = 'diff --git a/allowed.py b/allowed.py\n'
        self.repair = {'developer': {'state': 'DEVELOPER_RESPONDED',
            'source_sha': HEAD1, 'artifact': repair_artifact,
            'artifact_sha256': hashlib.sha256(repair_artifact.encode()).hexdigest()}}
        self.store = SimpleNamespace(get=lambda task_id: self.repair)
        self.reader = patch('llmtool.task_submission_cli.task_completion.read_d6_registration',
                            return_value=self.registration)
        self.reader.start()
        self.addCleanup(self.reader.stop)

    def _segments(self, claims=None, store=None):
        task = {'developer': self.original}
        if claims is not None:
            task['review_repair_claims'] = claims
        return _completion_repair_artifact_segments(task, store or self.store,
            executor_task='executor', common_dir='/repo/.git', root='/repo',
            branch='task', source_sha=SOURCE)

    def test_one_repair_builds_ordered_segments(self):
        result = self._segments([_claim()])
        self.assertEqual(result, [(self.original['artifact'],
            self.original['artifact_sha256'], SOURCE, HEAD1),
            (self.repair['developer']['artifact'],
             self.repair['developer']['artifact_sha256'], HEAD1, FINAL)])

    def test_unknown_claim_fails_closed(self):
        with self.assertRaises(LLMToolError) as caught:
            self._segments([_claim(state='UNKNOWN')])
        self.assertEqual(caught.exception.completion_code,
                         'DEVELOPER_ARTIFACT_CHAIN_INVALID')

    def test_missing_repair_task_fails_closed(self):
        missing = SimpleNamespace(get=lambda task_id: (_ for _ in ()).throw(KeyError(task_id)))
        with self.assertRaises(LLMToolError) as caught:
            self._segments([_claim()], store=missing)
        self.assertEqual(caught.exception.completion_code,
                         'DEVELOPER_ARTIFACT_CHAIN_INVALID')

    def test_repair_source_mismatch_fails_closed(self):
        self.repair['developer']['source_sha'] = HEAD2
        with self.assertRaises(LLMToolError) as caught:
            self._segments([_claim()])
        self.assertEqual(caught.exception.completion_code,
                         'DEVELOPER_ARTIFACT_CHAIN_INVALID')

    def test_tampered_repair_artifact_fails_closed(self):
        self.repair['developer']['artifact'] += 'tamper\n'
        with self.assertRaises(LLMToolError) as caught:
            self._segments([_claim()])
        self.assertEqual(caught.exception.completion_code,
                         'DEVELOPER_ARTIFACT_CHAIN_INVALID')

    def test_out_of_scope_repair_path_fails_closed(self):
        artifact = 'diff --git a/outside.py b/outside.py\n'
        self.repair['developer']['artifact'] = artifact
        self.repair['developer']['artifact_sha256'] = hashlib.sha256(
            artifact.encode()).hexdigest()
        with self.assertRaises(LLMToolError) as caught:
            self._segments([_claim()])
        self.assertEqual(caught.exception.completion_code,
                         'DEVELOPER_ARTIFACT_CHAIN_INVALID')

    def test_no_repair_keeps_legacy_path(self):
        self.assertIsNone(self._segments())


if __name__ == '__main__':
    unittest.main()
