"""#1502 WP-E invariant R1: one shared registry check for review/debug models.

No code path may launch codex-reviewer, claude-reviewer, codex-debug or
claude-debug with a malformed model id or a model of another or an unknown
family, whatever the CLI, environment or user config says. Owner #2302 part 1
leaves review and AI Debug admission unchanged (security repair v91 High 1): a
role admits any well-formed model of its own review family with no repository
change, so a formal PASS is classified by the family of the model that ran;
the role's allowed_models is not a membership list. A model the provider
cannot serve fails from the provider path. Consumer launch tests live next to
each consumer; this module covers the shared check and the resolver. Nothing
here opens a network connection.
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
    """The other family's listed model: refused for role."""
    other = 'claude-reviewer' if role in CODEX_ROLES else 'codex-reviewer'
    return listed(config, other)


def not_own_family(config, role):
    """Well-formed ids that are not a model of role's own review family."""
    return (cross_family(config, role), 'gemini-4-hydrogen-medium',
            'opencode/example-model-b-free', 'example-model-b', 'qwen3-coder-next',
            'deepseek-v4.1-flash', 'claude-gpt-6', 'gpt-claude-opus-5-5')


def prefix_and_case_variants(model):
    """Forms of the role's own model that are not written as the family names it."""
    return ('openai/' + model, 'anthropic/' + model, 'vendor/' + model,
            model.upper(), model.title())


# Badly formed model ids, refused for every role on every path.
MALFORMED = ('has space', '-leading-dash', 'https://example.invalid/m', 'x' * 129, '')


def same_family(role):
    """A same-family model that is not the role's listed model (Owner #2302 P2)."""
    return 'gpt-example-b' if role in CODEX_ROLES else 'claude-fable-5-2'


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

    def test_unlisted_same_family_models_are_admitted(self):
        # Owner #2302 P2: family and format decide, not allowed_models.
        for role in sorted(REGISTRY_ROLES):
            family = 'gpt-' if role in CODEX_ROLES else 'claude-'
            for model in (family + 'unlisted-model', same_family(role),
                          listed(self.config, role) + '-mini'):
                with self.subTest(role=role, model=model):
                    self.assertEqual(check_registry_role_model(self.config, role, model),
                                     model)

    def test_a_model_of_another_or_unknown_family_is_refused(self):
        # Reviewer and Debug independence is unchanged by Owner #2302 part 1:
        # the other review family's model, other families, ids of no known
        # family and mixed-family ids are refused for every role, so no
        # formal PASS can be classified by a role whose model is not its family.
        for role in sorted(REGISTRY_ROLES):
            for refused in not_own_family(self.config, role):
                with self.subTest(role=role, model=refused):
                    self.assert_refused(self.config, role, refused)

    def test_prefix_case_and_whitespace_variants_are_refused(self):
        for role in sorted(REGISTRY_ROLES):
            model = listed(self.config, role)
            for refused in prefix_and_case_variants(model) + (
                    ' ' + model, model + '\n', model + ' x'):
                with self.subTest(role=role, model=refused):
                    self.assert_refused(self.config, role, refused)

    def test_malformed_models_are_refused(self):
        for role in sorted(REGISTRY_ROLES):
            for refused in MALFORMED:
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
        malformed_list = copy.deepcopy(self.config)
        malformed_list['roles']['claude-reviewer']['allowed_models'] = ['has space']
        malformed_default = copy.deepcopy(self.config)
        malformed_default['roles']['claude-reviewer']['default_model'] = 'has space'
        missing_role = copy.deepcopy(self.config)
        del missing_role['roles']['claude-debug']
        for config in (route_only, malformed_list, malformed_default, missing_role,
                       {}, [], 'not-a-config'):
            with self.subTest(config=config):
                self.assert_refused(config, 'claude-reviewer', model)

    def test_registry_with_more_family_models_or_a_default_model_is_valid(self):
        # Owner #2302 P2: these registries are valid and admit any family model.
        extra = copy.deepcopy(self.config)
        extra['roles']['claude-reviewer']['allowed_models'].append('claude-extra-model')
        default_only = copy.deepcopy(self.config)
        default_only['roles']['claude-reviewer'] = {'default_model': 'claude-extra-model'}
        for config in (extra, default_only):
            for model in (listed(self.config, 'claude-reviewer'), 'claude-fable-5-2'):
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

    def test_unset_model_takes_the_registry_model(self):
        for role in sorted(REGISTRY_ROLES):
            with self.subTest(role=role):
                resolved = self.resolve(role)
                self.assertEqual(resolved['model'], listed(self.config, role))
                self.assertEqual(resolved['model_source'], 'repo_default')
                self.assertEqual(resolved['source'], 'builtin')

    def test_an_own_family_model_is_admitted_at_every_layer(self):
        # Owner #2302 part 1: a model of the role's own review family, set at
        # any layer with no repository change, is admitted with its family
        # recorded from the id.
        for role in sorted(REGISTRY_ROLES):
            env_name = 'LLMTOOL_ROLE_' + role.upper().replace('-', '_') + '_MODEL'
            own = 'codex' if role in CODEX_ROLES else 'claude'
            for model in (same_family(role), listed(self.config, role)):
                for layer in ('roles', 'env', 'cli'):
                    with self.subTest(role=role, model=model, layer=layer):
                        self.save({'roles': {role: {'model': model}}} if layer == 'roles'
                                  else {})
                        resolved = self.resolve(
                            role,
                            environ={env_name: model} if layer == 'env' else {},
                            cli={'model': model} if layer == 'cli' else None)
                        self.assertEqual((resolved['model'], resolved['model_source'],
                                          resolved['requested_model'],
                                          resolved['model_family']),
                                         (model, layer, model, own))

    def test_another_or_unknown_family_model_is_refused_at_every_layer(self):
        # Independence is unchanged: the other review family, another family,
        # no known family and a mixed-family id are refused, so a formal PASS
        # is never classified by a role whose model is not of its family.
        for role in sorted(REGISTRY_ROLES):
            env_name = 'LLMTOOL_ROLE_' + role.upper().replace('-', '_') + '_MODEL'
            for model in not_own_family(self.config, role):
                for layer in ('roles', 'env', 'cli'):
                    with self.subTest(role=role, model=model, layer=layer):
                        self.save({'roles': {role: {'model': model}}} if layer == 'roles'
                                  else {})
                        self.assert_refused(
                            role,
                            environ={env_name: model} if layer == 'env' else {},
                            cli={'model': model} if layer == 'cli' else None)

    def test_malformed_models_are_refused_at_every_layer(self):
        for role in sorted(REGISTRY_ROLES):
            env_name = 'LLMTOOL_ROLE_' + role.upper().replace('-', '_') + '_MODEL'
            for model in ('has space', '-leading-dash', 'https://example.invalid/m'):
                for layer in ('roles', 'env', 'cli'):
                    with self.subTest(role=role, model=model, layer=layer):
                        self.save({'roles': {role: {'model': model}}} if layer == 'roles'
                                  else {})
                        self.assert_refused(
                            role,
                            environ={env_name: model} if layer == 'env' else {},
                            cli={'model': model} if layer == 'cli' else None)

    def test_registry_default_has_no_requested_model_and_its_own_family(self):
        for role in sorted(REGISTRY_ROLES):
            with self.subTest(role=role):
                resolved = self.resolve(role)
                self.assertIsNone(resolved['requested_model'])
                self.assertEqual(resolved['model_family'],
                                 'codex' if role in CODEX_ROLES else 'claude')

    def test_prefixed_and_case_variants_are_refused(self):
        for role in sorted(REGISTRY_ROLES):
            model = listed(self.config, role)
            for variant in prefix_and_case_variants(model):
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
        self.assertEqual(self.resolve('claude-reviewer')['model_family'], 'claude')
        # The legacy alias never admits the other review family's model.
        other = cross_family(self.config, 'claude-reviewer')
        self.save({'roles': {'reviewer': {'model': other}}})
        self.assert_refused('reviewer')
        self.assert_refused('claude-reviewer')
        self.save({'roles': {'reviewer': {'model': 'has space'}}})
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
        for refused in ('anthropic/' + model, 'has space',
                        cross_family(self.config, 'claude-reviewer')):
            with self.subTest(model=refused):
                with self.assertRaises(LLMToolError) as caught:
                    role_config.check_registry_model('reviewer', refused)
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

    def test_each_consumer_admits_an_own_family_model_and_refuses_any_other(self):
        for consumer, role, admit, code in self.rows:
            model = listed(self.config, role)
            family = 'gpt-' if role in CODEX_ROLES else 'claude-'
            # Owner #2302 part 1: unlisted models of the role's own family are
            # admitted; the other review family, other families, ids of no
            # known family, prefix and case variants and malformed ids are not.
            for admitted in (model, family + 'unlisted-model', same_family(role)):
                with self.subTest(consumer=consumer, role=role, model=admitted):
                    self.assertEqual(admit(admitted), admitted)
            for refused in (not_own_family(self.config, role) +
                            prefix_and_case_variants(model) +
                            ('has space', '-leading-dash', 'https://example.invalid/m',
                             model + '\n', '', None)):
                with self.subTest(consumer=consumer, role=role, model=refused):
                    with self.assertRaises((LLMToolError, self.bg_error)) as caught:
                        admit(refused)
                    self.assertEqual(code(caught.exception), 'ROLE_MODEL_NOT_ALLOWED')

    def test_consumer_builtin_fallback_models_are_the_listed_models(self):
        # A fallback used when no model is resolved still passes the check, so
        # it must equal the registry entry (otherwise every launch would fail).
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
