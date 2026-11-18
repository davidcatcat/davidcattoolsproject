"""#2027: tests for ROUTE_CONFIG_UNAVAILABLE enforcement.

When developer-routes.json is unreadable or fails schema validation,
check_registry_role_model raises RouteError('ROUTE_CONFIG_UNAVAILABLE'),
role_config.check_registry_model maps it to LLMToolError with role_config_code
ROUTE_CONFIG_UNAVAILABLE, and review_dispatch._admit_review_model refuses with
review_dispatch_code ROUTE_CONFIG_UNAVAILABLE. No Reviewer fallback is admitted.
"""

import unittest
from unittest.mock import patch

from llmtool import developer_routes, review_dispatch, role_config, task_advance
from llmtool.developer_routes import RouteError, check_registry_role_model
from llmtool.errors import LLMToolError


class RouteConfigUnavailableTests(unittest.TestCase):
    def test_check_registry_role_model_raises_route_config_unavailable_when_load_fails(self):
        with patch.object(developer_routes, 'load_route_config', side_effect=RouteError('ROUTE_CONFIG_INVALID')):
            with self.assertRaises(RouteError) as caught:
                check_registry_role_model(None, 'codex-reviewer', 'gpt-6.1-sol')
            self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_UNAVAILABLE')

    def test_role_config_check_registry_model_raises_llmtoolerror_with_route_config_unavailable(self):
        with patch.object(developer_routes, 'load_route_config', side_effect=RouteError('ROUTE_CONFIG_INVALID')):
            with self.assertRaises(LLMToolError) as caught:
                role_config.check_registry_model('codex-reviewer', 'gpt-6.1-sol')
            self.assertEqual(getattr(caught.exception, 'role_config_code', None), 'ROUTE_CONFIG_UNAVAILABLE')

    def test_review_dispatch_admit_review_model_under_patch_refuses_route_config_unavailable(self):
        with patch.object(developer_routes, 'load_route_config', side_effect=RouteError('ROUTE_CONFIG_INVALID')):
            with self.assertRaises(LLMToolError) as caught:
                review_dispatch._admit_review_model('claude-reviewer', 'claude-sonnet-5-5')
            self.assertEqual(getattr(caught.exception, 'review_dispatch_code', None), 'ROUTE_CONFIG_UNAVAILABLE')

    def test_review_dispatch_admit_review_model_without_patch_returns_model(self):
        self.assertEqual(
            review_dispatch._admit_review_model('claude-reviewer', 'claude-sonnet-5-5'),
            'claude-sonnet-5-5',
        )

    def test_review_dispatch_admit_review_model_same_family_model_returns_model(self):
        # Owner #2302 P2: a Claude-family model other than the default is admitted.
        self.assertEqual(
            review_dispatch._admit_review_model('claude-reviewer', 'claude-opus-5-5'),
            'claude-opus-5-5',
        )

    def test_review_dispatch_admit_review_model_disallowed_model_refuses_role_model_not_allowed(self):
        for model in ('gpt-6.1-sol', 'anthropic/claude-opus-5-5'):
            with self.subTest(model=model):
                with self.assertRaises(LLMToolError) as caught:
                    review_dispatch._admit_review_model('claude-reviewer', model)
                self.assertEqual(getattr(caught.exception, 'review_dispatch_code', None),
                                 'ROLE_MODEL_NOT_ALLOWED')

    def test_explicit_config_dict_with_no_roles_key_refuses_role_model_not_allowed(self):
        with self.assertRaises(RouteError) as caught:
            check_registry_role_model({}, 'claude-reviewer', 'claude-sonnet-5-5')
        self.assertEqual(caught.exception.code, 'ROLE_MODEL_NOT_ALLOWED')

        with self.assertRaises(LLMToolError) as caught_role:
            role_config.check_registry_model('claude-reviewer', 'claude-sonnet-5-5', role_registry={})
        self.assertEqual(getattr(caught_role.exception, 'role_config_code', None), 'ROLE_MODEL_NOT_ALLOWED')

    def test_route_config_unavailable_not_in_task_advance_reviewer_route_unavailable(self):
        self.assertNotIn('ROUTE_CONFIG_UNAVAILABLE', task_advance._REVIEWER_ROUTE_UNAVAILABLE)

    def test_role_config_resolve_role_raises_route_config_unavailable_when_load_fails(self):
        with patch.object(developer_routes, 'load_route_config', side_effect=RouteError('ROUTE_CONFIG_INVALID')):
            for role_name in ('reviewer', 'codex-reviewer'):
                with self.assertRaises(LLMToolError) as caught:
                    role_config.resolve_role(role_name)
                self.assertEqual(
                    getattr(caught.exception, 'role_config_code', None),
                    'ROUTE_CONFIG_UNAVAILABLE',
                )

    def test_review_dispatch_resolve_reviewer_role_under_patch_and_fallback(self):
        with patch.object(developer_routes, 'load_route_config', side_effect=RouteError('ROUTE_CONFIG_INVALID')):
            for reviewer_key in ('claude-reviewer', 'codex-reviewer'):
                with self.assertRaises(LLMToolError) as caught:
                    review_dispatch._resolve_reviewer_role(reviewer_key)
                self.assertEqual(
                    getattr(caught.exception, 'review_dispatch_code', None),
                    'ROUTE_CONFIG_UNAVAILABLE',
                )
                self.assertIsNone(task_advance._reviewer_fallback({}, caught.exception))

        claude_role = review_dispatch._resolve_reviewer_role('claude-reviewer')
        self.assertEqual(claude_role['model'], 'claude-sonnet-5-5')

    def test_role_config_registry_default_missing_roles_entry_refuses_role_model_not_allowed(self):
        for registry in ({'roles': {}}, {'roles': {'codex-reviewer': {}}},
                         {'roles': {'codex-reviewer': {'allowed_models': []}}},
                         {'roles': {'codex-reviewer': {'default_model': 7}}}, {}, []):
            with self.subTest(registry=registry):
                with self.assertRaises(LLMToolError) as caught:
                    role_config._registry_default('codex-reviewer', registry)
                self.assertEqual(
                    getattr(caught.exception, 'role_config_code', None),
                    'ROLE_MODEL_NOT_ALLOWED',
                )

    def test_role_config_registry_default_prefers_default_model(self):
        # Owner #2302 P2: roles.*.default_model names the default when present.
        registry = {'roles': {'codex-reviewer': {'default_model': 'gpt-example-b',
                                                 'allowed_models': ['gpt-6.1-sol']}}}
        self.assertEqual(role_config._registry_default('codex-reviewer', registry),
                         (registry, 'gpt-example-b'))
        registry = {'roles': {'codex-reviewer': {'allowed_models': ['gpt-6.1-sol']}}}
        self.assertEqual(role_config._registry_default('codex-reviewer', registry)[1],
                         'gpt-6.1-sol')


if __name__ == '__main__':
    unittest.main()
