"""#1502 WP-E invariant R1: one shared registry check for review/debug models.

Every launch of codex-reviewer, claude-reviewer, codex-debug or claude-debug
passes the one shared registry check. Owner #2302 (decision 11-17): that check
admits any well-formed model id of any family, with any provider prefix or
case, from the CLI, environment or user config; it refuses only a malformed
or missing model id or an unavailable registry. The registry entry names the
role's default model and is not a membership list. Formal review independence
follows the recorded model family of each run (task_completion), never this
check. Consumer launch tests live next to each consumer; this module covers
the shared check and the resolver. Nothing here opens a network connection.
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
    registry_default_model,
)
from llmtool.errors import LLMToolError

CODEX_ROLES = ('codex-reviewer', 'codex-debug')
CLAUDE_ROLES = ('claude-reviewer', 'claude-debug')
# Not well-formed model ids. Each passes the resolver's plain-text check, so
# only the shared model check can refuse it.
MALFORMED_MODELS = ('example model', 'https://example.invalid/model', '-example-model',
                    'x' * 129)


def listed(config, role):
    """The registry default model of role (Owner #2302: default_model)."""
    return registry_default_model(config['roles'][role])


def cross_family(config, role):
    """The other family's registry default model: admitted too (Owner #2302)."""
    other = 'claude-reviewer' if role in CODEX_ROLES else 'codex-reviewer'
    return listed(config, other)


def same_family(role):
    """A same-family model that is not the role's registry default."""
    return 'gpt-example-b' if role in CODEX_ROLES else 'claude-fable-5-2'


def other_models(config, role):
    """Well-formed models of another family, prefix or case: all admitted."""
    model = listed(config, role)
    return (cross_family(config, role), 'gemini-4-hydrogen-medium',
            'opencode/example-model-b-free', 'example-model-b',
            'openai/' + model, 'anthropic/' + model, 'vendor/' + model,
            model.upper(), model.title())


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

    def test_shipped_registry_names_only_default_models(self):
        # Owner #2302: the shipped registry is not a per-model permit list.
        for role in sorted(REGISTRY_ROLES):
            with self.subTest(role=role):
                self.assertNotIn('allowed_models', self.config['roles'][role])
                self.assertIsInstance(listed(self.config, role), str)

    def test_each_default_model_is_admitted(self):
        for role in sorted(REGISTRY_ROLES):
            with self.subTest(role=role):
                model = listed(self.config, role)
                self.assertEqual(check_registry_role_model(self.config, role, model), model)
                # None loads the repository developer-routes.json.
                self.assertEqual(check_registry_role_model(None, role, model), model)

    def test_other_same_family_models_are_admitted(self):
        for role in sorted(REGISTRY_ROLES):
            family = 'gpt-' if role in CODEX_ROLES else 'claude-'
            for model in (family + 'unlisted-model', same_family(role),
                          listed(self.config, role) + '-mini'):
                with self.subTest(role=role, model=model):
                    self.assertEqual(check_registry_role_model(self.config, role, model),
                                     model)

    def test_other_family_prefixed_and_case_variant_models_are_admitted(self):
        # Owner #2302 (decision 11-17): no family, prefix or case gate.
        for role in sorted(REGISTRY_ROLES):
            for model in other_models(self.config, role):
                with self.subTest(role=role, model=model):
                    self.assertEqual(check_registry_role_model(self.config, role, model),
                                     model)
                    self.assertEqual(check_registry_role_model(None, role, model), model)

    def test_malformed_and_whitespace_models_are_refused(self):
        for role in sorted(REGISTRY_ROLES):
            model = listed(self.config, role)
            for refused in MALFORMED_MODELS + (' ' + model, model + '\n', ''):
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
        malformed_default = copy.deepcopy(self.config)
        malformed_default['roles']['claude-reviewer']['default_model'] = MALFORMED_MODELS[0]
        empty_list = copy.deepcopy(self.config)
        empty_list['roles']['claude-reviewer'] = {'allowed_models': []}
        no_model = copy.deepcopy(self.config)
        no_model['roles']['claude-reviewer'] = {'timeout': 600}
        unknown_key = copy.deepcopy(self.config)
        unknown_key['roles']['claude-reviewer']['family'] = 'claude'
        missing_role = copy.deepcopy(self.config)
        del missing_role['roles']['claude-debug']
        for config in (route_only, malformed_default, empty_list, no_model, unknown_key,
                       missing_role, {}, [], 'not-a-config'):
            with self.subTest(config=config):
                self.assert_refused(config, 'claude-reviewer', model)

    def test_registry_with_listed_models_or_another_family_default_is_valid(self):
        # Owner #2302: an older allowed_models list is accepted but is not a
        # membership list, and a default of any family is valid.
        listed_models = copy.deepcopy(self.config)
        listed_models['roles']['claude-reviewer'] = {
            'allowed_models': ['claude-extra-model', 'gpt-example-b']}
        other_default = copy.deepcopy(self.config)
        other_default['roles']['claude-reviewer'] = {
            'default_model': cross_family(self.config, 'claude-reviewer')}
        for config in (listed_models, other_default):
            for model in (listed(self.config, 'claude-reviewer'), 'claude-fable-5-2',
                          'gemini-4-hydrogen-medium'):
                with self.subTest(config=config['roles']['claude-reviewer'], model=model):
                    self.assertEqual(
                        check_registry_role_model(config, 'claude-reviewer', model), model)

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

    def layers(self, role, model):
        """(layer, resolve kwargs) for the user config, env and one-shot CLI."""
        env_name = 'LLMTOOL_ROLE_' + role.upper().replace('-', '_') + '_MODEL'
        for layer in ('roles', 'env', 'cli'):
            self.save({'roles': {role: {'model': model}}} if layer == 'roles' else {})
            yield layer, {'environ': {env_name: model} if layer == 'env' else {},
                          'cli': {'model': model} if layer == 'cli' else None}

    def test_unset_model_takes_the_registry_model(self):
        for role in sorted(REGISTRY_ROLES):
            with self.subTest(role=role):
                resolved = self.resolve(role)
                self.assertEqual(resolved['model'], listed(self.config, role))
                self.assertEqual(resolved['model_source'], 'repo_default')
                self.assertEqual(resolved['source'], 'builtin')

    def test_any_well_formed_model_is_admitted_at_every_layer(self):
        # Owner #2302: e.g. claude-fable-5-2 or a Codex-family model for a
        # Claude role via user config, env or one-shot CLI alone.
        for role in sorted(REGISTRY_ROLES):
            for model in (same_family(role), cross_family(self.config, role),
                          'gemini-4-hydrogen-medium', 'vendor/' + same_family(role)):
                for layer, kwargs in self.layers(role, model):
                    with self.subTest(role=role, model=model, layer=layer):
                        resolved = self.resolve(role, **kwargs)
                        self.assertEqual((resolved['model'], resolved['model_source']),
                                         (model, layer))

    def test_malformed_models_are_refused_at_every_layer(self):
        for role in sorted(REGISTRY_ROLES):
            for model in MALFORMED_MODELS:
                for layer, kwargs in self.layers(role, model):
                    with self.subTest(role=role, model=model, layer=layer):
                        self.assert_refused(role, **kwargs)

    def test_prefixed_and_case_variants_are_admitted(self):
        for role in sorted(REGISTRY_ROLES):
            model = listed(self.config, role)
            for variant in ('openai/' + model, 'anthropic/' + model, model.upper()):
                with self.subTest(role=role, model=variant):
                    resolved = self.resolve(role, cli={'model': variant})
                    self.assertEqual((resolved['model'], resolved['model_source']),
                                     (variant, 'cli'))

    def test_default_model_from_cli_is_admitted(self):
        for role in sorted(REGISTRY_ROLES):
            with self.subTest(role=role):
                model = listed(self.config, role)
                resolved = self.resolve(role, cli={'model': model})
                self.assertEqual((resolved['model'], resolved['model_source']), (model, 'cli'))

    def test_legacy_reviewer_alias_resolves_and_is_checked(self):
        model = listed(self.config, 'claude-reviewer')
        self.save({'roles': {'reviewer': {'model': model}}})
        self.assertEqual(self.resolve('reviewer')['model'], model)
        other = cross_family(self.config, 'claude-reviewer')
        self.save({'roles': {'reviewer': {'model': other}}})
        self.assertEqual(self.resolve('reviewer')['model'], other)
        self.assertEqual(self.resolve('claude-reviewer')['model'], other)
        self.save({'roles': {'reviewer': {'model': MALFORMED_MODELS[0]}}})
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
        self.assertEqual(role_config.check_registry_model('reviewer', 'anthropic/' + model),
                         'anthropic/' + model)
        with self.assertRaises(LLMToolError) as caught:
            role_config.check_registry_model('reviewer', MALFORMED_MODELS[0])
        self.assertEqual(caught.exception.role_config_code, 'ROLE_MODEL_NOT_ALLOWED')


def _bg_dispatch_module():
    """The claude_bg_dispatch script, loaded like its own test module does."""
    import importlib.util
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'claude_bg_dispatch.py'
    spec = importlib.util.spec_from_file_location('claude_bg_dispatch_registry_test', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ConsumerAdmissionTests(unittest.TestCase):
    """Every High Debug #1782 consumer admits through the one shared check.

    Each row is (consumer, role, admit(model), code(error)). Full launch tests
    (no claim, no D6, no spawn on refusal) live next to each consumer.
    """

    def setUp(self):
        from llmtool import high_debug, review_authority, review_dispatch
        self.config = load_route_config()
        bg = _bg_dispatch_module()

        def bg_code(error):
            return error.code

        self.rows = (
            ('review_dispatch', 'claude-reviewer',
             lambda model: review_dispatch._admit_review_model('claude-reviewer', model),
             lambda error: error.review_dispatch_code),
            ('review_dispatch', 'codex-reviewer',
             lambda model: review_dispatch._admit_review_model('codex-reviewer', model),
             lambda error: error.review_dispatch_code),
            ('review_authority', 'claude-reviewer', review_authority.admit_reviewer_model,
             lambda error: error.review_code),
            ('claude_bg_dispatch', 'claude-reviewer', bg._reviewer_model, bg_code),
            ('high_debug', 'claude-debug',
             lambda model: high_debug._admit_debug_model('claude-debug', model),
             lambda error: error.high_debug_code),
            ('high_debug', 'codex-debug',
             lambda model: high_debug._admit_debug_model('codex-debug', model),
             lambda error: error.high_debug_code),
        )
        self.bg_error = bg.DispatchError

    def test_each_consumer_admits_any_well_formed_model_and_refuses_malformed_ones(self):
        for consumer, role, admit, code in self.rows:
            model = listed(self.config, role)
            family = 'gpt-' if role in CODEX_ROLES else 'claude-'
            # Owner #2302: same-family, other-family, prefixed and case variants.
            for admitted in ((model, family + 'unlisted-model', same_family(role)) +
                             other_models(self.config, role)):
                with self.subTest(consumer=consumer, role=role, model=admitted):
                    self.assertEqual(admit(admitted), admitted)
            for refused in MALFORMED_MODELS + (None,):
                with self.subTest(consumer=consumer, role=role, model=refused):
                    with self.assertRaises((LLMToolError, self.bg_error)) as caught:
                        admit(refused)
                    self.assertEqual(code(caught.exception), 'ROLE_MODEL_NOT_ALLOWED')

    def test_consumer_builtin_fallback_models_are_the_default_models(self):
        # A fallback used when no model is resolved still passes the check; it
        # equals the registry default so the repository default is one value.
        from llmtool import high_debug, review_dispatch
        self.assertEqual(review_dispatch._CODEX_REVIEW_MODEL,
                         listed(self.config, 'codex-reviewer'))
        self.assertEqual(review_dispatch._CLAUDE_REVIEW_MODEL,
                         listed(self.config, 'claude-reviewer'))
        self.assertEqual(high_debug._CODEX_MODEL, listed(self.config, 'codex-debug'))
        self.assertEqual(high_debug._CLAUDE_MODEL, listed(self.config, 'claude-debug'))

    def test_model_refusal_is_never_a_debug_fallback_code(self):
        from llmtool import high_debug
        self.assertNotIn('ROLE_MODEL_NOT_ALLOWED', high_debug.DEBUG_FALLBACK_CODES)
        self.assertNotIn('ROLE_MODEL_NOT_ALLOWED', high_debug._CLAUDE_DEBUG_UNAVAILABLE)


if __name__ == '__main__':
    unittest.main()
