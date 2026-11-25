"""#2602: AGY CLI 1.5.17 usage normalisation and TaskStore round trip."""

import hashlib
import tempfile
import unittest
from pathlib import Path

from llmtool.task_store import TaskStore
from llmtool.task_submission_cli import _normalize_agy_usage, _record


AGY_1_5_17_USAGE = {
    'input_tokens': 11910,
    'output_tokens': 68,
    'thinking_tokens': 66,
    'cache_read_tokens': 0,
    'total_tokens': 11978,
}

MAPPED_1_5_17_USAGE = {
    'prompt_tokens': 11910,
    'completion_tokens': 68,
    'total_tokens': 11978,
    'reasoning_tokens': 66,
    'cached_prompt_tokens': 0,
}

_ARTIFACT = (
    'diff --git a/src.py b/src.py\n'
    '--- a/src.py\n'
    '+++ b/src.py\n'
    '@@ -1 +1 @@\n'
    '-old\n'
    '+new\n'
)


def _base_record():
    prompt = 'AGY usage round trip'
    record = _record(
        'a' * 32, 'b' * 32, 'pm-' + 'c' * 32, 'pm', prompt,
        state='PM_RESPONDED', provider_type='openai-compatible',
        backend='NO_NATIVE_SESSION_ID_EXPOSED',
        request_sha=hashlib.sha256(prompt.encode('utf-8')).hexdigest())
    record['response'] = 'PM response'
    record['pm_route_binding'] = {
        'type': 'openai-compatible', 'model': None, 'base_url': None}
    return record


class AgyUsageNormalizeTests(unittest.TestCase):
    def test_agy_1_5_17_sample_maps_exactly(self):
        self.assertEqual(
            _normalize_agy_usage(dict(AGY_1_5_17_USAGE)),
            MAPPED_1_5_17_USAGE)

    def test_agy_shape_without_optional_fields_maps_three_fields(self):
        self.assertEqual(
            _normalize_agy_usage(
                {'input_tokens': 1, 'output_tokens': 2, 'total_tokens': 3}),
            {'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 3})

    def test_old_accepted_shape_is_unchanged(self):
        usage = {
            'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5,
            'cached_prompt_tokens': 1, 'reasoning_tokens': 1, 'tool_calls': 1,
        }
        self.assertEqual(_normalize_agy_usage(dict(usage)), usage)
        self.assertEqual(
            _normalize_agy_usage(
                {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}),
            {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0})

    def test_unknown_extra_agy_integer_keys_are_ignored(self):
        usage = dict(AGY_1_5_17_USAGE, cache_write_tokens=3, tool_calls=7)
        self.assertEqual(_normalize_agy_usage(usage), MAPPED_1_5_17_USAGE)

    def test_invalid_usage_stays_unknown_never_zero(self):
        cases = {
            'not a dict': [],
            'missing': {},
            'partial agy missing total': {
                'input_tokens': 1, 'output_tokens': 2},
            'partial agy missing output': {
                'input_tokens': 1, 'total_tokens': 3},
            'partial legacy missing total': {
                'prompt_tokens': 1, 'completion_tokens': 2},
            'negative required': {
                'input_tokens': -1, 'output_tokens': 2, 'total_tokens': 1},
            'negative optional': {
                'input_tokens': 1, 'output_tokens': 2, 'total_tokens': 3,
                'cache_read_tokens': -1},
            'non-integer optional': {
                'input_tokens': 1, 'output_tokens': 2, 'total_tokens': 3,
                'thinking_tokens': '2'},
            'boolean count': {
                'input_tokens': True, 'output_tokens': 2, 'total_tokens': 3},
            'mixed agy and completion': {
                'input_tokens': 1, 'output_tokens': 2, 'total_tokens': 3,
                'prompt_tokens': 1},
            'mixed legacy and input': {
                'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 3,
                'input_tokens': 1},
            'legacy extra key': {
                'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 3,
                'other_total': 6},
            'agy non-integer extra': {
                'input_tokens': 1, 'output_tokens': 2, 'total_tokens': 3,
                'other_total': '6'},
        }
        for name, value in cases.items():
            with self.subTest(case=name):
                self.assertIsNone(_normalize_agy_usage(value))


class AgyUsageTaskStoreTests(unittest.TestCase):
    def test_top_level_mapped_usage_round_trips_through_public_store_api(self):
        usage = _normalize_agy_usage(dict(AGY_1_5_17_USAGE))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / 'tasks'
            store = TaskStore(root)
            record = _base_record()
            record['usage'] = usage
            store.create(record)
            self.assertEqual(store.get(record['task_id'])['usage'], usage)

    def test_developer_mapped_usage_and_cli_source_round_trip_through_task_store(self):
        usage = _normalize_agy_usage(dict(AGY_1_5_17_USAGE))
        artifact_sha256 = hashlib.sha256(_ARTIFACT.encode('utf-8')).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / 'tasks'
            store = TaskStore(root)
            record = _base_record()
            record['developer'] = {
                'assignment_id': 'd' * 32, 'selection_source': 'PM',
                'state': 'DEVELOPER_RESPONDED', 'provider_profile': 'agy',
                'provider_type': 'agy-cli', 'model': 'gemini-test',
                'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
                'session_id': 'dev-' + 'e' * 32, 'source_sha': 'f' * 40,
                'source_sha_binding': 'D6_REGISTRY_OBSERVED',
                'read_paths': ['src.py'], 'write_paths': ['src.py'],
                'acceptance': ['the result is recorded'],
                'context_sha256': '1' * 64,
                'egress_basis': 'Approved non-sensitive Task context.',
                'input_bytes': 100, 'max_tokens': None,
                'artifact_format': 'unified_diff', 'artifact': _ARTIFACT,
                'artifact_sha256': artifact_sha256, 'usage': usage,
                'error_code': None, 'created_at': record['created_at'],
                'owner_assignment_ref': None,
                'execution': {
                    'status': 'DONE',
                    'native_session_id': 'conversation_12345678',
                    'process_exit_code': 0, 'stdout_sha256': '2' * 64,
                    'elapsed_ms': 42, 'cleanup_status': 'CONFIRMED',
                    'usage_source': 'CLI_REPORTED', 'source_bytes': None,
                    'source_file_count': None, 'source_manifest_sha256': None,
                },
            }
            # TaskStore's write path runs the full record validator; reading it
            # back revalidates. This test covers the usage shape, not dispatch.
            store._write(record, create=True)
            saved = store.get(record['task_id'])
        self.assertEqual(saved['developer']['usage'], usage)
        self.assertEqual(saved['developer']['execution']['usage_source'],
                         'CLI_REPORTED')
