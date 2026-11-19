"""Owner #289 / #2302: OpenCode Developer route model admission and validation.

The OpenCode Developer route admits whatever model the Owner configures
(free or paid, any provider prefix OpenCode supports, with or without a
#variant), with no free-only check and no per-model allow-list refusal,
while preserving strict input validation against malformed models.

The full delegate path (claim, CLI launch, stored DEVELOPER_RESPONDED) with
a configured paid model is covered in test_task_submission_cli
(test_opencode_developer_profile_non_free_model_is_refused_before_claim),
which has the registered D6 fixture that path needs.
"""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llmtool.developer_routes import (
    RouteError,
    check_registry_role_model,
    check_route_model,
    load_route_config,
)
from llmtool.errors import LLMToolError
from llmtool.opencode_direct_developer import (
    DirectOpenCodeError,
    is_free_model,
    is_valid_model,
    validate_model,
)
from llmtool.task_store import TaskStore, new_id
import llmtool.task_submission_cli as submission_cli
from llmtool.task_submission_cli import _record

import review_trust_fixture
import worker_policy_fixture


def setUpModule():
    review_trust_fixture.start_module()


def tearDownModule():
    review_trust_fixture.stop_module()


ADMITTED_PAID_MODELS = (
    'opencode/paid-model',
    'opencode/paid-model#high',
    'opencode/deepseek-v4.1',
    'opencode/deepseek-v4.1#low',
    'opencode/claude-sonnet-4',
    'opencode/gpt-5-mini#medium',
)

ADMITTED_OTHER_PROVIDER_MODELS = (
    'opencode-go/deepseek-v4.1-flash',
    'opencode-go/deepseek-v4.1-flash#high',
    'other/name-free',
    'other/name-paid',
    'fireworks/deepseek-v3',
    'together/llama-3.3-70b#low',
    'custom-provider/special-model',
)

ADMITTED_FREE_MODELS = (
    'opencode/deepseek-v4.1-flash-free',
    'opencode/deepseek-v4.1-flash-free#high',
    'opencode/test-free',
)

ALL_ADMITTED_MODELS = (ADMITTED_PAID_MODELS + ADMITTED_OTHER_PROVIDER_MODELS +
                       ADMITTED_FREE_MODELS)

MALFORMED_MODELS = (
    '',
    '   ',
    '\t',
    'opencode/model\n',
    'opencode/model\r',
    'opencode/model\x01',
    'opencode/..',
    '../model',
    'opencode/../model',
    'https://example.invalid/m',
    'opencode://deepseek',
    'bare-model',
    'deepseek-v4.1-flash-free',
    '-leading/model',
    'opencode/-model',
    'opencode/a/b',
    'opencode/a/b-free',
    'opencode/' + 'a' * 125,
    'opencode/example-free#',
    'opencode/example-free#HIGH',
    'opencode/example-free#a b',
    'opencode/example-free#x#y',
    'opencode/example-free#toolongvariantname123',
)

# Malformed values written quoted into the user config, including ones the
# config resolver refuses before validate_model runs (#2302 F1).
CONFIG_REFUSED_MODELS = (
    '',
    '   ',
    '\t',
    'opencode/model\n',
    'opencode/model\r',
    'opencode/model\x01',
    'opencode/model\x00',
)


def opencode_dispatch_task():
    """A PM_RESPONDED Task routed to opencode (shape of test_task_store)."""
    request = 'Bounded non-sensitive OpenCode proposal task.'
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
        pm_assignment={'schema_version': 1, 'route': 'opencode', 'risk': 'LOW',
                       'goal': 'Produce a proposal diff.',
                       'acceptance': ['the proposal is recorded']},
    )
    return task


def opencode_pending_assignment():
    """A DEVELOPER_PENDING OpenCode assignment (shape of test_task_store)."""
    return {
        'assignment_id': new_id(), 'selection_source': 'PM',
        'state': 'DEVELOPER_PENDING', 'provider_profile': 'opencode',
        'provider_type': None, 'model': None,
        'backend_session_semantics': 'CLI_REPORTED_SESSION_ID',
        'session_id': 'dev-' + new_id(), 'source_sha': 'a' * 40,
        'source_sha_binding': 'D6_REGISTRY_OBSERVED',
        'read_paths': ['src.py'], 'write_paths': ['src.py'],
        'acceptance': ['the proposal is recorded'],
        'context_sha256': 'b' * 64,
        'egress_basis': 'Approved non-sensitive test context.', 'input_bytes': 10,
        'max_tokens': 128, 'artifact_format': 'unified_diff',
        'artifact': None, 'artifact_sha256': None, 'usage': None,
        'error_code': None, 'created_at': '2026-10-23T00:00:00Z',
        'execution': None,
    }


def opencode_execution(status, session):
    return {
        'status': status, 'native_session_id': session,
        'process_exit_code': 0 if status == 'SUCCEEDED' else 1,
        'stdout_sha256': 'c' * 64, 'elapsed_ms': 12, 'cleanup_status': 'CONFIRMED',
        'usage_source': 'UNKNOWN', 'source_bytes': 10, 'source_file_count': 1,
        'source_manifest_sha256': 'd' * 64,
    }


class OpenCodeModelValidatorTests(unittest.TestCase):
    """A4: paid, other-provider and free models admitted; malformed refused."""

    def setUp(self):
        self.config = load_route_config()

    def assert_admitted(self, models):
        for model in models:
            with self.subTest(model=model):
                self.assertTrue(is_valid_model(model))
                self.assertEqual(validate_model(model), model)
                self.assertEqual(check_route_model(self.config, 'opencode', model), model)

    def test_paid_models_are_admitted(self):
        self.assert_admitted(ADMITTED_PAID_MODELS)
        for model in ADMITTED_PAID_MODELS:
            with self.subTest(model=model):
                self.assertFalse(is_free_model(model))

    def test_other_provider_models_are_admitted(self):
        self.assert_admitted(ADMITTED_OTHER_PROVIDER_MODELS)

    def test_free_models_are_admitted(self):
        self.assert_admitted(ADMITTED_FREE_MODELS)
        for model in ADMITTED_FREE_MODELS:
            with self.subTest(model=model):
                self.assertTrue(is_free_model(model))

    def test_admission_does_not_consult_allowed_models(self):
        # A1: no per-model allow-list refusal for the OpenCode route, with or
        # without allowed_models in the route entry.
        entry = dict(self.config['routes']['opencode'])
        entry.pop('allowed_models', None)
        unlisted = {'routes': {'opencode': entry}}
        for model in ALL_ADMITTED_MODELS:
            with self.subTest(model=model):
                self.assertEqual(check_route_model(unlisted, 'opencode', model), model)

    def test_malformed_models_are_refused_with_stable_codes(self):
        for model in MALFORMED_MODELS:
            with self.subTest(model=model):
                self.assertFalse(is_valid_model(model))
                with self.assertRaises(RouteError) as route_caught:
                    check_route_model(self.config, 'opencode', model)
                self.assertEqual(route_caught.exception.code, 'ROLE_MODEL_NOT_ALLOWED')
                with self.assertRaises(DirectOpenCodeError) as caught:
                    validate_model(model)
                self.assertEqual(caught.exception.direct_opencode_code,
                                 'OPENCODE_MODEL_INVALID')

    def test_non_str_models_are_refused(self):
        for model in (None, 7, True, ['opencode/paid-model']):
            with self.subTest(model=model):
                self.assertFalse(is_valid_model(model))
                with self.assertRaises(RouteError):
                    check_route_model(self.config, 'opencode', model)

    def test_other_routes_model_checks_remain_unchanged(self):
        # A3: AGY, Claude, gw-agent and Codex roles keep their checks.
        self.assertEqual(check_route_model(self.config, 'agy', 'gemini-3.8-flash-medium'),
                         'gemini-3.8-flash-medium')
        for bad_model in ('claude-opus-5-5', 'gpt-6.1-sol', 'opencode/paid-model'):
            with self.subTest(route='agy', model=bad_model):
                with self.assertRaises(RouteError) as caught:
                    check_route_model(self.config, 'agy', bad_model)
                self.assertEqual(caught.exception.code, 'ROLE_MODEL_NOT_ALLOWED')
        self.assertEqual(check_route_model(self.config, 'claude', 'claude-opus-5-5'),
                         'claude-opus-5-5')
        for bad_model in ('gemini-3.8-flash-medium', 'gpt-6.1-sol', 'opencode/paid-model'):
            with self.subTest(route='claude', model=bad_model):
                with self.assertRaises(RouteError) as caught:
                    check_route_model(self.config, 'claude', bad_model)
                self.assertEqual(caught.exception.code, 'ROLE_MODEL_NOT_ALLOWED')
        self.assertEqual(check_route_model(self.config, 'gw-agent', 'gw'), 'gw')
        for bad_model in ('opencode/paid-model', 'other-unlisted-model'):
            with self.subTest(route='gw-agent', model=bad_model):
                with self.assertRaises(RouteError) as caught:
                    check_route_model(self.config, 'gw-agent', bad_model)
                self.assertEqual(caught.exception.code, 'ROLE_MODEL_NOT_ALLOWED')
        self.assertEqual(
            check_registry_role_model(self.config, 'codex-reviewer', 'gpt-6.1-sol'),
            'gpt-6.1-sol')
        for bad_model in ('claude-opus-5-5', 'gemini-3.8-flash-medium',
                          'opencode/paid-model'):
            with self.subTest(role='codex-reviewer', model=bad_model):
                with self.assertRaises(RouteError) as caught:
                    check_registry_role_model(self.config, 'codex-reviewer', bad_model)
                self.assertEqual(caught.exception.code, 'ROLE_MODEL_NOT_ALLOWED')


class OpenCodeModelPersistenceTests(unittest.TestCase):
    """A-store: admitted models persist in every state; malformed stay refused."""

    ARTIFACT = ('diff --git a/src.py b/src.py\n'
                '--- a/src.py\n+++ b/src.py\n@@ -1 +1 @@\n-x=1\n+x=2\n')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TaskStore(Path(self.temp.name).resolve() / 'tasks')
        # #1692: the shared all-ALLOWED test policy, never the live file.
        self.policy_path = worker_policy_fixture.install(self)

    def _dispatching_task(self, model):
        task = opencode_dispatch_task()
        self.store.create(task)
        pending = opencode_pending_assignment()
        self.store.compare_and_update(
            task['task_id'], expected={'developer': None}, developer=pending)
        dispatching = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                       'provider_type': 'opencode-cli', 'model': model}
        self.store.update(task['task_id'], developer=dispatching)
        return task['task_id'], pending, dispatching

    def assert_stored(self, task_id, model, state):
        developer = self.store.get(task_id)['developer']
        self.assertEqual(developer['model'], model)
        self.assertEqual(developer['state'], state)

    def test_admitted_models_persist_across_developer_states(self):
        for model in ALL_ADMITTED_MODELS:
            with self.subTest(model=model):
                task_id, _, dispatching = self._dispatching_task(model)
                self.assert_stored(task_id, model, 'DEVELOPER_DISPATCHING')
                responded = {
                    **dispatching, 'state': 'DEVELOPER_RESPONDED',
                    'artifact': self.ARTIFACT,
                    'artifact_sha256': hashlib.sha256(self.ARTIFACT.encode()).hexdigest(),
                    'execution': opencode_execution('SUCCEEDED', 'ses_abcdefgh12345678'),
                }
                self.store.update(task_id, developer=responded)
                self.assert_stored(task_id, model, 'DEVELOPER_RESPONDED')

                other_id, _, other_dispatching = self._dispatching_task(model)
                unknown = {
                    **other_dispatching, 'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                    'error_code': 'OPENCODE_EXECUTION_FAILED',
                    'execution': opencode_execution('UNKNOWN', 'ses_unknown12345678'),
                }
                self.store.update(other_id, developer=unknown)
                self.assert_stored(other_id, model, 'DEVELOPER_OUTCOME_UNKNOWN')

    def test_malformed_models_are_refused_by_store_validation(self):
        task = opencode_dispatch_task()
        self.store.create(task)
        pending = opencode_pending_assignment()
        self.store.compare_and_update(
            task['task_id'], expected={'developer': None}, developer=pending)
        for model in MALFORMED_MODELS:
            with self.subTest(model=model):
                invalid = {**pending, 'state': 'DEVELOPER_DISPATCHING',
                           'provider_type': 'opencode-cli', 'model': model}
                with self.assertRaises(LLMToolError) as caught:
                    self.store.update(task['task_id'], developer=invalid)
                self.assertEqual(caught.exception.task_store_error_code,
                                 'TASK_STORE_INVALID')
        stored = self.store.get(task['task_id'])['developer']
        self.assertEqual(stored['state'], 'DEVELOPER_PENDING')
        self.assertIsNone(stored['model'])

    def test_invalid_outcome_evidence_stays_refused_for_admitted_model(self):
        # Outcome persistence is not weakened: a paid model does not relax
        # the evidence checks on a responded or unknown outcome.
        model = 'opencode-go/deepseek-v4.1-flash#high'
        task_id, _, dispatching = self._dispatching_task(model)
        responded = {
            **dispatching, 'state': 'DEVELOPER_RESPONDED',
            'artifact': self.ARTIFACT,
            'artifact_sha256': hashlib.sha256(self.ARTIFACT.encode()).hexdigest(),
            'execution': opencode_execution('SUCCEEDED', 'ses_abcdefgh12345678'),
        }
        invalid_records = {
            'responded without execution': {**responded, 'execution': None},
            'responded with failed execution': {
                **responded, 'execution': opencode_execution('FAILED', 'ses_abcdefgh12345678')},
            'responded with wrong artifact digest': {
                **responded, 'artifact_sha256': 'e' * 64},
            'responded without artifact': {
                **responded, 'artifact': None, 'artifact_sha256': None},
            'unknown without error code': {
                **dispatching, 'state': 'DEVELOPER_OUTCOME_UNKNOWN',
                'execution': opencode_execution('UNKNOWN', 'ses_unknown12345678')},
            'bad native session id': {
                **responded, 'execution': opencode_execution('SUCCEEDED', 'not-a-session')},
        }
        for label, invalid in invalid_records.items():
            with self.subTest(case=label):
                with self.assertRaises(LLMToolError) as caught:
                    self.store.update(task_id, developer=invalid)
                self.assertEqual(caught.exception.task_store_error_code,
                                 'TASK_STORE_INVALID')
        self.assert_stored(task_id, model, 'DEVELOPER_DISPATCHING')


class OpenCodeDeveloperProfileModelTests(unittest.TestCase):
    """A1/A2: the configured user-config model passes the delegate profile check."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve() / 'home'
        self.home.mkdir(mode=0o700)
        self.policy_path = worker_policy_fixture.install(self)

    def _write_opencode_developer_model(self, model, *, steps=None):
        config = self.home / '.config/llmtool/config.yaml'
        config.parent.mkdir(parents=True, exist_ok=True)
        text = 'providers:\n  opencode-developer:\n    model: ' + model + '\n'
        if steps is not None:
            text += '    provider_options:\n      steps: ' + str(steps) + '\n'
        config.write_text(text)

    def test_configured_admitted_models_are_returned_unchanged(self):
        for model in ALL_ADMITTED_MODELS:
            with self.subTest(model=model):
                self._write_opencode_developer_model(model, steps=24)
                with patch.dict(os.environ, {'HOME': str(self.home)}):
                    self.assertEqual(submission_cli._opencode_developer_profile(),
                                     (model, 24))

    def test_configured_malformed_models_are_refused_with_stable_code(self):
        # YAML-safe malformed values; control-character values are covered by
        # the validator tests above.
        for model in ('bare-model', 'deepseek-v4.1-flash-free', 'opencode/a/b',
                      'opencode/a/b-free', 'opencode/-model', '-leading/model',
                      'opencode/..', '../model', 'https://example.invalid/m',
                      'opencode/example-free#HIGH', 'opencode/' + 'a' * 125):
            with self.subTest(model=model):
                self._write_opencode_developer_model(model)
                with patch.dict(os.environ, {'HOME': str(self.home)}):
                    with self.assertRaises(LLMToolError) as caught:
                        submission_cli._opencode_developer_profile()
                self.assertEqual(getattr(caught.exception, 'direct_opencode_code', None),
                                 'OPENCODE_MODEL_INVALID')

    def test_configured_empty_or_control_character_models_keep_stable_code(self):
        # #2302 F1: values the config resolver itself refuses (empty,
        # whitespace, control characters) keep OPENCODE_MODEL_INVALID. The
        # model is written as a JSON (YAML double-quoted) scalar so escapes
        # survive the file.
        for model in CONFIG_REFUSED_MODELS:
            with self.subTest(model=model):
                self._write_opencode_developer_model(json.dumps(model))
                with patch.dict(os.environ, {'HOME': str(self.home)}):
                    with self.assertRaises(LLMToolError) as caught:
                        submission_cli._opencode_developer_profile()
                self.assertEqual(getattr(caught.exception, 'direct_opencode_code', None),
                                 'OPENCODE_MODEL_INVALID')

    def test_other_invalid_profile_fields_are_not_labelled_model_invalid(self):
        # The model-invalid code is not applied to a refusal unrelated to the model.
        self._write_opencode_developer_model('opencode/paid-model', steps=2)
        with patch.dict(os.environ, {'HOME': str(self.home)}):
            with self.assertRaises(LLMToolError) as caught:
                submission_cli._opencode_developer_profile()
        self.assertIsNone(getattr(caught.exception, 'direct_opencode_code', None))

    def test_no_fallback_model_replaces_a_refused_model(self):
        # A2: a refused model is never swapped for the default or another model.
        self._write_opencode_developer_model('opencode/a/b')
        with patch.dict(os.environ, {'HOME': str(self.home)}), \
                patch.object(submission_cli.opencode_direct_developer, 'validate_model',
                             wraps=validate_model) as checked:
            with self.assertRaises(LLMToolError):
                submission_cli._opencode_developer_profile()
        self.assertTrue(checked.call_args_list)
        for call in checked.call_args_list:
            self.assertEqual(call.args, ('opencode/a/b',))


if __name__ == '__main__':
    unittest.main()
