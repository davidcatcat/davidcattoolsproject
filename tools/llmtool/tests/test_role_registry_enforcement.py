"""#1502 WP-E invariant R1: one shared registry check for review/debug models.

No code path may launch codex-reviewer, claude-reviewer, codex-debug or
claude-debug with a model outside that role's allowed_models, whatever the
CLI, environment or user config says, with any provider prefix or case.
Consumer launch tests live next to each consumer; this module covers the
shared check and the resolver. Nothing here opens a network connection.
"""

import copy
import tempfile
import unittest
from pathlib import Path

import yaml

from llmtool import role_config
from llmtool.config import user_config_path
from llmtool.developer_routes import (
    REGISTRY_ROLES, RouteError, check_registry_role_model, load_route_config,
)
from llmtool.errors import LLMToolError

CODEX_ROLES = ('codex-reviewer', 'codex-debug')
CLAUDE_ROLES = ('claude-reviewer', 'claude-debug')


def listed(config, role):
    return config['roles'][role]['allowed_models'][0]


def cross_family(config, role):
    """The other family's listed model: always refused for role."""
    other = 'claude-reviewer' if role in CODEX_ROLES else 'codex-reviewer'
    return listed(config, other)


class SharedCheckTests(unittest.TestCase):
    def setUp(self):
        self.config = load_route_config()

    def assert_refused(self, config, role, model):
        with self.assertRaises(RouteError) as caught:
            check_registry_role_model(config, role, model)
        self.assertEqual(caught.exception.code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_registry_governs_exactly_the_four_review_and_debug_roles(self):
        self.assertEqual(REGISTRY_ROLES, frozenset(CODEX_ROLES + CLAUDE_ROLES))
        self.assertEqual(set(self.config['roles']), set(REGISTRY_ROLES))

    def test_each_listed_model_is_admitted(self):
        for role in sorted(REGISTRY_ROLES):
            with self.subTest(role=role):
                model = listed(self.config, role)
                self.assertEqual(check_registry_role_model(self.config, role, model), model)
                # None loads the repository developer-routes.json.
                self.assertEqual(check_registry_role_model(None, role, model), model)

    def test_unlisted_and_cross_family_models_are_refused(self):
        for role in sorted(REGISTRY_ROLES):
            family = 'gpt-' if role in CODEX_ROLES else 'claude-'
            for refused in (family + 'unlisted-model', cross_family(self.config, role)):
                with self.subTest(role=role, model=refused):
                    self.assert_refused(self.config, role, refused)

    def test_prefix_case_and_whitespace_variants_are_refused(self):
        for role in sorted(REGISTRY_ROLES):
            model = listed(self.config, role)
            for refused in ('openai/' + model, 'anthropic/' + model, 'vendor/' + model,
                            model.upper(), model.title(), ' ' + model, model + '\n',
                            model + '-mini'):
                with self.subTest(role=role, model=refused):
                    self.assert_refused(self.config, role, refused)

    def test_non_text_models_are_refused(self):
        for role in sorted(REGISTRY_ROLES):
            for refused in (None, 7, [listed(self.config, role)], b'x'):
                with self.subTest(role=role, model=refused):
                    self.assert_refused(self.config, role, refused)

    def test_only_canonical_role_ids_are_accepted(self):
        model = listed(self.config, 'claude-reviewer')
        for role in ('reviewer', 'Claude-Reviewer', 'claude-developer', 'pm', None, ''):
            with self.subTest(role=role):
                self.assert_refused(self.config, role, model)

    def test_absent_or_invalid_registry_refuses_every_model(self):
        model = listed(self.config, 'claude-reviewer')
        route_only = {key: value for key, value in self.config.items() if key != 'roles'}
        extra = copy.deepcopy(self.config)
        extra['roles']['claude-reviewer']['allowed_models'].append('claude-extra-model')
        wrong_family = copy.deepcopy(self.config)
        wrong_family['roles']['claude-reviewer']['allowed_models'] = [
            cross_family(self.config, 'claude-reviewer')]
        missing_role = copy.deepcopy(self.config)
        del missing_role['roles']['claude-debug']
        for config in (route_only, extra, wrong_family, missing_role, {}, [], 'not-a-config'):
            with self.subTest(config=config):
                self.assert_refused(config, 'claude-reviewer', model)

    def test_route_only_fixtures_still_load(self):
        # Compatibility: route-only fixtures load; only admission needs roles.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'routes.json'
            source = Path(__file__).resolve().parents[1] / 'developer-routes.json'
            raw = yaml.safe_load(source.read_text(encoding='utf-8'))
            raw.pop('roles')
            import json
            path.write_text(json.dumps(raw), encoding='utf-8')
            config = load_route_config(path)
        self.assertNotIn('roles', config)
        self.assert_refused(config, 'codex-reviewer', listed(self.config, 'codex-reviewer'))


class ResolverEnforcementTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name) / 'home'
        self.home.mkdir()
        self.user = user_config_path(self.home)
        self.user.parent.mkdir(parents=True)
        self.config = load_route_config()

    def save(self, value):
        self.user.write_text(yaml.safe_dump(value), encoding='utf-8')

    def resolve(self, role, **kwargs):
        kwargs.setdefault('environ', {})
        return role_config.resolve_role(role, home=self.home, **kwargs)

    def assert_refused(self, role, **kwargs):
        with self.assertRaises(LLMToolError) as caught:
            self.resolve(role, **kwargs)
        self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_unset_model_takes_the_registry_model(self):
        for role in sorted(REGISTRY_ROLES):
            with self.subTest(role=role):
                resolved = self.resolve(role)
                self.assertEqual(resolved['model'], listed(self.config, role))
                self.assertEqual(resolved['model_source'], 'repo_default')
                self.assertEqual(resolved['source'], 'builtin')

    def test_unlisted_and_cross_family_models_are_refused_at_every_layer(self):
        for role in sorted(REGISTRY_ROLES):
            env_name = 'LLMTOOL_ROLE_' + role.upper().replace('-', '_') + '_MODEL'
            for model in ('gpt-unlisted-model' if role in CODEX_ROLES else
                          'claude-unlisted-model', cross_family(self.config, role)):
                for layer in ('roles', 'env', 'cli'):
                    with self.subTest(role=role, model=model, layer=layer):
                        self.save({'roles': {role: {'model': model}}} if layer == 'roles'
                                  else {})
                        self.assert_refused(
                            role,
                            environ={env_name: model} if layer == 'env' else {},
                            cli={'model': model} if layer == 'cli' else None)

    def test_prefixed_and_case_variants_are_refused(self):
        for role in sorted(REGISTRY_ROLES):
            model = listed(self.config, role)
            for variant in ('openai/' + model, 'anthropic/' + model, model.upper()):
                with self.subTest(role=role, model=variant):
                    self.assert_refused(role, cli={'model': variant})

    def test_listed_model_from_cli_is_admitted(self):
        for role in sorted(REGISTRY_ROLES):
            with self.subTest(role=role):
                model = listed(self.config, role)
                resolved = self.resolve(role, cli={'model': model})
                self.assertEqual((resolved['model'], resolved['model_source']), (model, 'cli'))

    def test_legacy_reviewer_alias_resolves_and_is_checked(self):
        model = listed(self.config, 'claude-reviewer')
        self.save({'roles': {'reviewer': {'model': model}}})
        self.assertEqual(self.resolve('reviewer')['model'], model)
        self.save({'roles': {'reviewer': {'model': cross_family(self.config,
                                                                  'claude-reviewer')}}})
        self.assert_refused('reviewer')
        self.assert_refused('claude-reviewer')

    def test_owner_override_never_admits_a_review_or_debug_model(self):
        for role in sorted(REGISTRY_ROLES):
            with self.subTest(role=role):
                self.assert_refused(role, owner_override={
                    'model': listed(self.config, role),
                    'ref': 'https://github.com/example-owner/example-repo/issues/1'
                           '#issuecomment-2'})

    def test_absent_registry_refuses_review_and_debug_resolution(self):
        route_only = {key: value for key, value in self.config.items() if key != 'roles'}
        for role in sorted(REGISTRY_ROLES):
            with self.subTest(role=role):
                self.assert_refused(role, role_registry=route_only)

    def test_check_registry_model_maps_an_alias_and_refuses_with_the_role_code(self):
        model = listed(self.config, 'claude-reviewer')
        self.assertEqual(role_config.check_registry_model('reviewer', model), model)
        with self.assertRaises(LLMToolError) as caught:
            role_config.check_registry_model('reviewer', 'anthropic/' + model)
        self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')


if __name__ == '__main__':
    unittest.main()
