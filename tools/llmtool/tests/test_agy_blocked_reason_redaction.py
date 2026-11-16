"""Tests for AGY failed run blocked_reason redaction (CWE-532)."""

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from llmtool.developer_routes import AgyRoute, RouteError, load_route_config
from llmtool.developer_routes.base import capture_snapshot, run_route, _exit_diagnostic
from llmtool.errors import LLMToolError
from llmtool.task_store import TaskStore, _validate, _digest
from llmtool.task_verification import parse_task_contract
try:
    import worker_policy_fixture
except ImportError:
    from tests import worker_policy_fixture

MODEL = load_route_config()['routes']['agy']['default_model']


class Boundary:
    def __init__(self, verified=True):
        self.verified = verified

    def prepare(self, root, write_paths, state_dirs=()):
        return ['/verified-boundary'], 'a' * 64

    def verify(self, root, write_paths, state_dirs=(), *, runner):
        return {'verified': self.verified, 'policy_sha256': 'a' * 64}


def make_task_record(task_id, run_id, *, developer=None):
    contract_data = {
        'schema_version': 1,
        'task_id': task_id,
        'goal': 'test goal',
        'acceptance': ['test acceptance'],
        'read_paths': ['source.py'],
        'write_paths': ['source.py'],
    }
    contract = parse_task_contract(json.dumps(contract_data))
    binding = contract.binding(run_id)
    record = {
        'schema_version': 2,
        'task_id': task_id,
        'run_id': run_id,
        'pm_session_id': 'pm-test-session',
        'pm_provider_profile': 'active-pm',
        'pm_provider_type': 'openai-compatible',
        'pm_model': 'model-1',
        'backend_session_semantics': 'EPHEMERAL_SINGLE_CALL',
        'native_session_id': None,
        'state': 'PM_PENDING',
        'created_at': '2026-11-16T00:00:00Z',
        'updated_at': '2026-11-16T00:00:00Z',
        'task_contract': contract_data,
        'task_binding': {
            'contract_digest': binding.contract_digest,
            'task_id': binding.task_id,
            'run_id': binding.run_id,
        },
        'request': 'test request',
        'request_sha256': hashlib.sha256(b'test request').hexdigest(),
        'response': None,
        'usage': None,
        'error_code': None,
        'developer': developer,
        'pm_assignment': None,
    }
    record['record_sha256'] = _digest(record)
    return record


def make_agy_developer(assignment_id, *, state='BLOCKED', error_code='AGY_EXIT_NONZERO',
                       blocked_reason='exit_code=1'):
    return {
        'assignment_id': assignment_id,
        'selection_source': 'PM',
        'state': state,
        'provider_profile': 'agy',
        'provider_type': 'agy-cli',
        'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
        'session_id': 'dev-test-session',
        'source_sha': '0' * 40,
        'source_sha_binding': 'UNVERIFIED_CALLER_INPUT',
        'context_sha256': '0' * 64,
        'egress_basis': 'TASK_SPEC',
        'input_bytes': 100,
        'max_tokens': None,
        'artifact_format': 'unified_diff',
        'artifact': None,
        'artifact_sha256': None,
        'usage': None,
        'error_code': error_code,
        'created_at': '2026-11-16T00:00:00Z',
        'model': 'gemini-1.5-pro',
        'read_paths': ['source.py'],
        'write_paths': ['source.py'],
        'acceptance': ['test acceptance'],
        'owner_assignment_ref': None,
        'blocked_reason': blocked_reason,
    }


class AgyBlockedReasonRedactionTests(unittest.TestCase):
    def setUp(self):
        worker_policy_fixture.install(self)
        self.temp = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp.name)
        self.root = self.temp_root / 'repo'
        self.root.mkdir()
        subprocess.run(['/usr/bin/git', 'init', str(self.root)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['/usr/bin/git', '-C', str(self.root), 'config', 'user.name', 'Test'], check=True)
        subprocess.run(['/usr/bin/git', '-C', str(self.root), 'config', 'user.email',
                        'test@example.invalid'], check=True)
        (self.root / 'source.py').write_text('original\n')
        subprocess.run(['/usr/bin/git', '-C', str(self.root), 'add', 'source.py'], check=True)
        subprocess.run(['/usr/bin/git', '-C', str(self.root), 'commit', '-m', 'initial'], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.branch = subprocess.check_output(
            ['/usr/bin/git', '-C', str(self.root), 'branch', '--show-current'], text=True).strip()
        self.head = subprocess.check_output(
            ['/usr/bin/git', '-C', str(self.root), 'rev-parse', 'HEAD'], text=True).strip()
        self.log = self.temp_root / 'agy.log'
        self.log.write_text(f'model_resolver: Resolving model {MODEL}\n')
        self.route = AgyRoute({'executable': 'agy'})

    def tearDown(self):
        self.temp.cleanup()

    def request(self):
        return {'root': str(self.root), 'prompt': 'bounded prompt',
                'model': MODEL, 'effort': 'medium',
                'expected_branch': self.branch, 'expected_head': self.head,
                'write_paths': ['source.py'], 'executable': '/usr/bin/agy',
                'log_path': 'agy.log'}

    def test_exit_diagnostic_records_exit_code_without_stderr_content(self):
        sensitive_stderr = b'CRITICAL ERROR: customer record 98765 database lookup failed with private account details'
        reason = _exit_diagnostic(2, sensitive_stderr)
        self.assertEqual(reason, 'exit_code=2')
        self.assertNotIn('CRITICAL', reason)
        self.assertNotIn('customer', reason)
        self.assertNotIn('98765', reason)
        self.assertNotIn('account', reason)

        reason_other = _exit_diagnostic(127, b'CONFIDENTIAL internal system trace at line 42')
        self.assertEqual(reason_other, 'exit_code=127')
        self.assertNotIn('CONFIDENTIAL', reason_other)

    def test_run_route_failed_agy_run_stores_no_stderr_in_blocked_reason(self):
        sensitive_stderr = b'INTERNAL ERROR: proprietary transaction failure on account record 54321'

        def runner(argv, cwd, env, timeout):
            Path(argv[argv.index('--log-file') + 1]).write_text(self.log.read_text())
            return 3, b'', sensitive_stderr

        with mock.patch.object(self.route, 'verified_model', wraps=self.route.verified_model):
            with self.assertRaises(RouteError) as caught:
                run_route(self.route, self.request(), gate=lambda: True,
                          runner=runner, boundary=Boundary(True),
                          snapshot=capture_snapshot)

        self.assertEqual(caught.exception.code, 'AGY_EXIT_NONZERO')
        self.assertEqual(caught.exception.blocked_reason, 'exit_code=3')
        self.assertNotIn('INTERNAL', caught.exception.blocked_reason)
        self.assertNotIn('proprietary', caught.exception.blocked_reason)
        self.assertNotIn('54321', caught.exception.blocked_reason)

    def test_task_store_strict_validation_accepts_clean_exit_code_and_refuses_stderr_tail(self):
        task_id = '0' * 32
        run_id = '1' * 32
        dev = make_agy_developer('2' * 32, blocked_reason='exit_code=1')
        record = make_task_record(task_id, run_id, developer=dev)

        # Clean exit_code reason passes strict validation.
        validated = _validate(record, strict=True)
        self.assertEqual(validated['developer']['blocked_reason'], 'exit_code=1')

        # Clean exit_code with fixed category also passes strict validation.
        dev_with_cat = make_agy_developer('2' * 32, blocked_reason='exit_code=1 category=CLI_ERROR')
        record_with_cat = make_task_record(task_id, run_id, developer=dev_with_cat)
        validated_cat = _validate(record_with_cat, strict=True)
        self.assertEqual(validated_cat['developer']['blocked_reason'], 'exit_code=1 category=CLI_ERROR')

        # Reason containing free-form stderr_tail fails strict validation.
        dev_bad = make_agy_developer('2' * 32, blocked_reason='exit_code=1 stderr_tail=customer account error')
        record_bad = make_task_record(task_id, run_id, developer=dev_bad)
        with self.assertRaises(LLMToolError):
            _validate(record_bad, strict=True)

        # Reason containing old withheld stderr_tail also fails strict validation.
        dev_withheld = make_agy_developer('2' * 32, blocked_reason='exit_code=1 stderr_tail=[withheld]')
        record_withheld = make_task_record(task_id, run_id, developer=dev_withheld)
        with self.assertRaises(LLMToolError):
            _validate(record_withheld, strict=True)

    def test_task_store_non_strict_validation_keeps_old_records_loading(self):
        task_id = '0' * 32
        run_id = '1' * 32

        # Old records with stderr_tail load successfully when strict=False (read mode).
        dev_old = make_agy_developer('2' * 32, blocked_reason='exit_code=1 stderr_tail=customer record error')
        record_old = make_task_record(task_id, run_id, developer=dev_old)
        loaded = _validate(record_old, strict=False)
        self.assertEqual(loaded['developer']['blocked_reason'], 'exit_code=1 stderr_tail=customer record error')

        # Old records with withheld stderr_tail load successfully when strict=False.
        dev_old_withheld = make_agy_developer('2' * 32, blocked_reason='exit_code=1 stderr_tail=[withheld]')
        record_old_withheld = make_task_record(task_id, run_id, developer=dev_old_withheld)
        loaded_withheld = _validate(record_old_withheld, strict=False)
        self.assertEqual(loaded_withheld['developer']['blocked_reason'], 'exit_code=1 stderr_tail=[withheld]')

    def test_task_store_file_persistence_and_existing_records_readable(self):
        store_root = self.temp_root / 'store'
        store = TaskStore(root=store_root)

        # 1. Storing a new record with clean exit_code blocked_reason succeeds.
        task_id_new = 'a' * 32
        run_id_new = 'b' * 32
        dev_new = make_agy_developer('c' * 32, blocked_reason='exit_code=2')
        record_new = make_task_record(task_id_new, run_id_new, developer=dev_new)
        store.create(record_new)
        fetched_new = store.get(task_id_new)
        self.assertEqual(fetched_new['developer']['blocked_reason'], 'exit_code=2')

        # 2. Writing an old-format record directly to disk simulating historical storage.
        task_id_old = 'e' * 32
        run_id_old = 'f' * 32
        dev_old = make_agy_developer('1' * 32, blocked_reason='exit_code=1 stderr_tail=legacy execution diagnostic')
        record_old = make_task_record(task_id_old, run_id_old, developer=dev_old)
        record_old_file = store_root / f'{task_id_old}.json'
        record_old_file.write_text(json.dumps(record_old, ensure_ascii=False, indent=2), encoding='utf-8')

        # Reading the old record succeeds and preserves its stored reason.
        fetched_old = store.get(task_id_old)
        self.assertEqual(fetched_old['developer']['blocked_reason'],
                         'exit_code=1 stderr_tail=legacy execution diagnostic')

    def test_existing_readers_of_blocked_reason_still_work(self):
        reason = _exit_diagnostic(5, b'any ignored stderr')

        # Readers checking startswith('exit_code=').
        self.assertTrue(reason.startswith('exit_code='))

        # Readers parsing exit_code field.
        parts = dict(part.split('=', 1) for part in reason.split() if '=' in part)
        self.assertEqual(parts.get('exit_code'), '5')
        self.assertEqual(int(parts['exit_code']), 5)

    def test_no_other_routes_stored_reasons_change(self):
        task_id = '3' * 32
        run_id = '4' * 32

        # Claude isolation block.
        claude_dev = {
            'assignment_id': '5' * 32,
            'selection_source': 'PM',
            'state': 'BLOCKED',
            'provider_profile': 'claude',
            'provider_type': 'claude-cli',
            'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
            'session_id': 'dev-claude-test',
            'source_sha': '0' * 40,
            'source_sha_binding': 'UNVERIFIED_CALLER_INPUT',
            'context_sha256': '0' * 64,
            'egress_basis': 'TASK_SPEC',
            'input_bytes': 100,
            'max_tokens': None,
            'artifact_format': 'unified_diff',
            'artifact': None,
            'artifact_sha256': None,
            'usage': None,
            'error_code': 'BLOCKED_WRITE_ISOLATION',
            'created_at': '2026-11-16T00:00:00Z',
            'model': 'claude-3-5-sonnet',
            'read_paths': ['source.py'],
            'write_paths': ['source.py'],
            'acceptance': ['test acceptance'],
            'owner_assignment_ref': None,
            'executor_task_id': 'd6-task-1',
            'direct_result_callback': {'executor_task_id': 'd6-task-1'},
            'blocked_reason': 'BOUNDARY_UNVERIFIED',
        }
        claude_record = make_task_record(task_id, run_id, developer=claude_dev)
        self.assertEqual(_validate(claude_record, strict=True)['developer']['blocked_reason'],
                         'BOUNDARY_UNVERIFIED')

        # AGY isolation block.
        agy_iso_dev = make_agy_developer('6' * 32, error_code='BLOCKED_WRITE_ISOLATION',
                                         blocked_reason='BOUNDARY_PREPARATION_FAILED')
        agy_iso_record = make_task_record(task_id, run_id, developer=agy_iso_dev)
        self.assertEqual(_validate(agy_iso_record, strict=True)['developer']['blocked_reason'],
                         'BOUNDARY_PREPARATION_FAILED')


if __name__ == '__main__':
    unittest.main()
