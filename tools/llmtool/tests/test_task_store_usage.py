import hashlib
import tempfile
import unittest
from pathlib import Path

from llmtool.task_store import TaskStore, new_id
from llmtool.task_verification import AcceptanceCriterion, CriterionKind, TaskContract


def record(usage):
    task_id, run_id = new_id(), new_id()
    request = 'Record provider token usage.'
    request_sha = hashlib.sha256(request.encode()).hexdigest()
    contract = TaskContract(
        schema_version=1, task_id=task_id, contract_version=1,
        previous_digest=None, goal='PM intake request sha256:' + request_sha,
        authorized_scope=('pm-planning',), forbidden_scope=(),
        criteria=(AcceptanceCriterion(
            'pm_response', CriterionKind.ARTIFACT,
            'Record the PM reply or unavailable status.'),),
    )
    return {
        'schema_version': 1, 'task_id': task_id, 'run_id': run_id,
        'pm_session_id': 'pm-' + new_id(), 'pm_provider_profile': 'pm',
        'pm_provider_type': 'codex-cli', 'pm_model': 'gpt-6-luna',
        'backend_session_semantics': 'EPHEMERAL_SINGLE_CALL',
        'native_session_id': None, 'state': 'PM_RESPONDED',
        'created_at': '2026-10-27T00:00:00Z',
        'updated_at': '2026-10-27T00:00:00Z',
        'task_contract': contract.to_dict(),
        'task_binding': contract.binding(run_id).to_dict(),
        'request': request, 'request_sha256': request_sha,
        'response': 'Usage recorded.', 'usage': usage,
        'error_code': None, 'record_sha256': None,
    }


class TaskStoreUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TaskStore((Path(self.temp.name) / 'tasks').resolve())

    def test_legacy_usage_without_optional_fields_still_loads(self):
        value = record({
            'prompt_tokens': 10, 'completion_tokens': 4, 'total_tokens': 14})

        created = self.store.create(value)

        self.assertEqual(self.store.get(value['task_id'])['usage'], created['usage'])

    def test_optional_cached_and_reasoning_usage_fields_are_persisted(self):
        usage = {
            'prompt_tokens': 10, 'completion_tokens': 4, 'total_tokens': 14,
            'cached_prompt_tokens': 6, 'reasoning_tokens': 2}
        value = record(usage)

        created = self.store.create(value)

        self.assertEqual(created['usage'], usage)
        self.assertEqual(self.store.get(value['task_id'])['usage'], usage)

    def test_unknown_optional_usage_fields_are_rejected(self):
        value = record({
            'prompt_tokens': 10, 'completion_tokens': 4, 'total_tokens': 14,
            'estimated_tokens': 1})

        with self.assertRaises(Exception):
            self.store.create(value)

    def test_bool_and_negative_optional_usage_counts_are_rejected(self):
        invalid_values = (True, -1)
        for field in ('cached_prompt_tokens', 'reasoning_tokens'):
            for invalid in invalid_values:
                usage = {
                    'prompt_tokens': 10, 'completion_tokens': 4,
                    'total_tokens': 14, field: invalid}
                with self.subTest(field=field, invalid=invalid):
                    with self.assertRaises(Exception):
                        self.store.create(record(usage))


if __name__ == '__main__':
    unittest.main()
