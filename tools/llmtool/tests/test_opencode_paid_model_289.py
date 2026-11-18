"""Owner decision #289: OpenCode Developer route model admission.

The OpenCode route admits any well-formed model id configured for it
(free or paid, any provider prefix OpenCode supports, with or without a #variant),
with no free-only check and no per-model allow-list refusal.
Malformed model identifiers are refused with stable codes.
"""

from pathlib import Path
import unittest
from unittest.mock import patch

from llmtool import developer_routes
from llmtool.developer_routes import RouteError, check_route_model, load_route_config
from llmtool import opencode_direct_developer
from llmtool.opencode_direct_developer import DirectOpenCodeError, configuration, validate_model
from llmtool import task_submission_cli
from llmtool.errors import LLMToolError


PAID_MODELS = (
    'opencode-go/deepseek-v4.1-flash',
    'opencode-go/deepseek-v4.1-flash#high',
    'opencode/deepseek-chat',
    'opencode/paid-model',
    'opencode/example-paid#medium',
)

ANOTHER_PROVIDER_MODELS = (
    'anthropic/claude-3-5-sonnet',
    'anthropic/claude-3-5-sonnet#high',
    'openai/gpt-4o',
    'google/gemini-2.5-flash',
    'openrouter/deepseek-chat',
    'together/llama-3-70b#low',
)

FREE_MODELS = (
    'opencode/deepseek-v4.1-flash-free',
    'opencode/deepseek-v4.1-flash-free#high',
    'other/name-free',
    'other/name-free#low',
)

MALFORMED_MODELS = (
    '',
    '   ',
    'opencode/has space',
    'opencode/newline\n',
    'opencode/carriage\r',
    'opencode/a/b',
    'opencode/../escape',
    '../traversal',
    '/leading/slash',
    'opencode/trailing/slash/',
    'https://example.invalid/model',
    'http://opencode/model',
    '-leading-dash/model',
    'opencode/-leading-dash',
    'bare-model-no-provider',
    'opencode/empty-variant#',
    'opencode/upper-variant#HIGH',
    'opencode/space-variant#a b',
    'opencode/multi-variant#x#y',
    'opencode/long-variant#abcdefghijklmnopq',
    'opencode/' + 'a' * 125,
    None,
    12345,
    ['opencode/model'],
    {'model': 'opencode/model'},
)


def _make_mock_profile(model, steps=None):
    provider_entry = {
        'name': 'opencode-developer',
        'type': 'opencode-cli',
        'model': model,
    }
    if steps is not None:
        provider_entry['provider_options'] = {'steps': steps}
    return {
        'name': 'opencode-developer',
        'active_provider': 'opencode-cli',
        'providers': {
            'opencode-cli': provider_entry,
        },
    }


class OpenCodePaidModel289Tests(unittest.TestCase):
    def setUp(self):
        self.config = load_route_config()

    def test_paid_models_admitted_by_validate_model(self):
        for model in PAID_MODELS:
            with self.subTest(model=model):
                self.assertEqual(validate_model(model), model)

    def test_paid_models_admitted_by_check_route_model(self):
        for model in PAID_MODELS:
            with self.subTest(model=model):
                self.assertEqual(check_route_model(self.config, 'opencode', model), model)

    def test_another_provider_models_admitted_by_validate_model(self):
        for model in ANOTHER_PROVIDER_MODELS:
            with self.subTest(model=model):
                self.assertEqual(validate_model(model), model)

    def test_another_provider_models_admitted_by_check_route_model(self):
        for model in ANOTHER_PROVIDER_MODELS:
            with self.subTest(model=model):
                self.assertEqual(check_route_model(self.config, 'opencode', model), model)

    def test_free_models_admitted_by_validate_model_and_check_route_model(self):
        for model in FREE_MODELS:
            with self.subTest(model=model):
                self.assertEqual(validate_model(model), model)
                self.assertEqual(check_route_model(self.config, 'opencode', model), model)

    def test_is_free_model_preserves_free_identification(self):
        for model in ('opencode/deepseek-v4.1-flash-free', 'opencode/deepseek-v4.1-flash-free#high'):
            with self.subTest(model=model):
                self.assertTrue(opencode_direct_developer.is_free_model(model))
        for model in PAID_MODELS:
            with self.subTest(model=model):
                self.assertFalse(opencode_direct_developer.is_free_model(model))

    def test_malformed_models_refused_by_validate_model(self):
        for model in MALFORMED_MODELS:
            with self.subTest(model=model):
                with self.assertRaises(DirectOpenCodeError) as caught:
                    validate_model(model)
                self.assertEqual(caught.exception.direct_opencode_code, 'OPENCODE_MODEL_INVALID')

    def test_malformed_models_refused_by_check_route_model(self):
        for model in MALFORMED_MODELS:
            with self.subTest(model=model):
                with self.assertRaises(RouteError) as caught:
                    check_route_model(self.config, 'opencode', model)
                self.assertEqual(caught.exception.code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_configuration_builds_expected_providers_and_policies(self):
        cases = (
            ('opencode-go/deepseek-v4.1-flash#high', 'opencode-go', 'deepseek-v4.1-flash'),
            ('anthropic/claude-3-5-sonnet', 'anthropic', 'claude-3-5-sonnet'),
            ('opencode/deepseek-v4.1-flash-free#high', 'opencode', 'deepseek-v4.1-flash-free'),
        )
        for model, expected_provider, expected_bare_model in cases:
            with self.subTest(model=model):
                cfg = configuration(read_paths=['a.py'], write_paths=['a.py'], model=model)
                self.assertEqual(cfg['model'], model)
                self.assertIn(expected_provider, cfg['providers'])
                self.assertIn(expected_bare_model, cfg['providers'][expected_provider]['models'])
                policy_actions = [(p['action'], p['resource'], p['effect']) for p in cfg['policies']]
                self.assertIn(('provider.use', '*', 'deny'), policy_actions)
                self.assertIn(('provider.use', expected_provider, 'allow'), policy_actions)
                self.assertIn(('integration.use', '*', 'deny'), policy_actions)
                self.assertIn(('integration.use', f'{expected_provider}#*', 'allow'), policy_actions)

    def test_profile_resolution_admits_paid_model_end_to_end(self):
        mock_profile = _make_mock_profile('opencode-go/deepseek-v4.1-flash#high', steps=500)
        with patch('llmtool.task_submission_cli.resolve_profile_config', return_value=mock_profile), \
                patch('llmtool.role_config.resolve_role', return_value={}):
            model, steps = task_submission_cli._opencode_developer_profile()
            self.assertEqual(model, 'opencode-go/deepseek-v4.1-flash#high')
            self.assertEqual(steps, 500)

    def test_profile_resolution_admits_another_provider_model(self):
        mock_profile = _make_mock_profile('anthropic/claude-3-5-sonnet#medium', steps=200)
        with patch('llmtool.task_submission_cli.resolve_profile_config', return_value=mock_profile), \
                patch('llmtool.role_config.resolve_role', return_value={}):
            model, steps = task_submission_cli._opencode_developer_profile()
            self.assertEqual(model, 'anthropic/claude-3-5-sonnet#medium')
            self.assertEqual(steps, 200)

    def test_profile_resolution_admits_free_model(self):
        mock_profile = _make_mock_profile('opencode/deepseek-v4.1-flash-free#high')
        with patch('llmtool.task_submission_cli.resolve_profile_config', return_value=mock_profile), \
                patch('llmtool.role_config.resolve_role', return_value={}):
            model, steps = task_submission_cli._opencode_developer_profile()
            self.assertEqual(model, 'opencode/deepseek-v4.1-flash-free#high')
            self.assertEqual(steps, opencode_direct_developer.DEFAULT_AGENT_STEPS)

    def test_profile_resolution_when_profile_is_none(self):
        with patch('llmtool.task_submission_cli.resolve_profile_config', return_value=None), \
                patch('llmtool.role_config.resolve_role', return_value={}):
            model, steps = task_submission_cli._opencode_developer_profile()
            self.assertEqual(model, opencode_direct_developer.MODEL)
            self.assertEqual(steps, opencode_direct_developer.DEFAULT_AGENT_STEPS)

    def test_profile_resolution_when_profile_model_is_none(self):
        mock_profile = _make_mock_profile(None)
        with patch('llmtool.task_submission_cli.resolve_profile_config', return_value=mock_profile), \
                patch('llmtool.role_config.resolve_role', return_value={}):
            model, steps = task_submission_cli._opencode_developer_profile()
            self.assertEqual(model, opencode_direct_developer.MODEL)
            self.assertEqual(steps, opencode_direct_developer.DEFAULT_AGENT_STEPS)

    def test_profile_resolution_refuses_malformed_model_with_stable_code(self):
        for bad_model in ('opencode/a/b', 'bare-model', '', '   ', 'opencode/m#HIGH'):
            with self.subTest(model=bad_model):
                mock_profile = _make_mock_profile(bad_model)
                with patch('llmtool.task_submission_cli.resolve_profile_config', return_value=mock_profile), \
                        patch('llmtool.role_config.resolve_role', return_value={}):
                    with self.assertRaises(LLMToolError) as caught:
                        task_submission_cli._opencode_developer_profile()
                    self.assertEqual(getattr(caught.exception, 'direct_opencode_code', None),
                                     'OPENCODE_MODEL_INVALID')

    def test_profile_resolution_refuses_missing_active_provider(self):
        broken = {'name': 'opencode-developer', 'model': 'opencode/example'}
        with patch('llmtool.task_submission_cli.resolve_profile_config', return_value=broken), \
                patch('llmtool.role_config.resolve_role', return_value={}):
            with self.assertRaises(LLMToolError) as caught:
                task_submission_cli._opencode_developer_profile()
            self.assertIn('opencode-developer profile is invalid', str(caught.exception))

    def test_profile_model_not_overwritten_by_repo_default_role(self):
        mock_profile = _make_mock_profile('opencode/paid-model')
        repo_default_role = {
            'role': 'opencode-developer',
            'model': 'opencode/deepseek-v4.1-flash-free',
            'model_source': 'repo_default',
            'effort': 'high',
            'effort_source': 'repo_default',
        }
        with patch('llmtool.task_submission_cli.resolve_profile_config', return_value=mock_profile), \
                patch('llmtool.role_config.resolve_role', return_value=repo_default_role):
            model, steps = task_submission_cli._opencode_developer_profile()
            self.assertEqual(model, 'opencode/paid-model')
            self.assertEqual(steps, opencode_direct_developer.DEFAULT_AGENT_STEPS)

    def test_profile_model_with_variant_not_overwritten_by_repo_default_role(self):
        mock_profile = _make_mock_profile('opencode-go/deepseek-v4.1-flash#medium')
        repo_default_role = {
            'role': 'opencode-developer',
            'model': 'opencode/deepseek-v4.1-flash-free',
            'model_source': 'repo_default',
            'effort': 'high',
            'effort_source': 'repo_default',
        }
        with patch('llmtool.task_submission_cli.resolve_profile_config', return_value=mock_profile), \
                patch('llmtool.role_config.resolve_role', return_value=repo_default_role):
            model, steps = task_submission_cli._opencode_developer_profile()
            self.assertEqual(model, 'opencode-go/deepseek-v4.1-flash#medium')
            self.assertEqual(steps, opencode_direct_developer.DEFAULT_AGENT_STEPS)

    def test_role_model_explicit_overrides_profile_model(self):
        mock_profile = _make_mock_profile('opencode/profile-model')
        explicit_role = {
            'role': 'opencode-developer',
            'model': 'opencode/explicit-role-model',
            'model_source': 'user_config',
            'effort': 'low',
            'effort_source': 'user_config',
        }
        with patch('llmtool.task_submission_cli.resolve_profile_config', return_value=mock_profile), \
                patch('llmtool.role_config.resolve_role', return_value=explicit_role):
            model, steps = task_submission_cli._opencode_developer_profile()
            self.assertEqual(model, 'opencode/explicit-role-model#low')
            self.assertEqual(steps, opencode_direct_developer.DEFAULT_AGENT_STEPS)


if __name__ == '__main__':
    unittest.main()
