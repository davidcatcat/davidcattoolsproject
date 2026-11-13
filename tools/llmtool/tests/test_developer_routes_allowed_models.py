"""#1502 WP-E: per-route allowed_models in developer-routes.json."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from llmtool import developer_routes
from llmtool.developer_routes import RouteError, check_route_model, load_route_config

ROUTES_FILE = Path(__file__).resolve().parents[1] / 'developer-routes.json'
ROUTE_NAMES = ('agy', 'claude', 'gw-agent', 'opencode')
# Valid but unlisted example model names, per route format.
OTHER_MODEL = {'agy': 'example-model-b', 'claude': 'claude-example-b',
               'gw-agent': 'example-model-b', 'opencode': 'opencode/example-model-b-free'}
# Badly formatted names for each route's format check.
BAD_FORMAT = {'agy': ('https://example.invalid/m', 'has space', '-leading-dash'),
              'claude': ('example-model-b', 'claude-UPPER', 'claude-'),
              'gw-agent': ('https://example.invalid/m', 'has space', '-leading-dash'),
              'opencode': ('example-model-b-free', 'opencode/example-paid',
                           'opencode/example-model-b-free#high')}
# #1502 WP-E: the review and AI Debug role registry in use.
SHIPPED_ROLES = {
    'codex-reviewer': {'allowed_models': ['gpt-6.1-sol']},
    'claude-reviewer': {'allowed_models': ['claude-opus-5-5']},
    'codex-debug': {'allowed_models': ['gpt-6.1-sol']},
    'claude-debug': {'allowed_models': ['claude-opus-5-5']},
}


class AllowedModelsConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'developer-routes.json'
        self.shipped = json.loads(ROUTES_FILE.read_text(encoding='utf-8'))

    def load_with(self, route, mutate):
        data = copy.deepcopy(self.shipped)
        mutate(data['routes'][route])
        self.path.write_text(json.dumps(data), encoding='utf-8')
        return load_route_config(self.path)

    def assert_invalid(self, route, mutate):
        with self.assertRaises(RouteError) as caught:
            self.load_with(route, mutate)
        self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')

    def test_shipped_file_lists_only_each_default_model(self):
        config = load_route_config()
        self.assertEqual(config['routes']['agy']['allowed_models'],
                         ['gemini-3.8-flash-medium'])
        self.assertEqual(config['routes']['claude']['allowed_models'], ['claude-opus-5-5'])
        # #1502 WP-E: gw-agent lists today's actual served default model.
        self.assertEqual(config['routes']['gw-agent']['allowed_models'], ['gw'])
        self.assertEqual(config['routes']['gw-agent']['default_model'], 'gw')
        self.assertEqual(config['routes']['gw-agent']['profile']['model'], 'gw')
        self.assertEqual(config['routes']['opencode']['allowed_models'],
                         ['opencode/deepseek-v4.1-flash-free'])
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                entry = config['routes'][route]
                self.assertIs(type(entry['allowed_models']), list)
                self.assertEqual(entry['allowed_models'], [entry['default_model']])
        self.assertEqual(self.shipped['schema_version'], 1)

    def test_only_default_model_is_accepted(self):
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                config = self.load_with(route, lambda entry: entry.update(
                    allowed_models=[entry['default_model']]))
                entry = config['routes'][route]
                self.assertEqual(entry['allowed_models'], [entry['default_model']])

    def test_extra_valid_model_is_invalid(self):
        # PR #1759 F1: a well-formed extra model next to the default is refused.
        for route in ROUTE_NAMES:
            for order in ('default_first', 'default_last'):
                with self.subTest(route=route, order=order):
                    def mutate(entry, route=route, order=order):
                        extra = [entry['default_model'], OTHER_MODEL[route]]
                        entry['allowed_models'] = (
                            extra if order == 'default_first' else extra[::-1])
                    self.assert_invalid(route, mutate)

    def test_sixteen_items_are_invalid(self):
        # The former 16-item maximum no longer admits extra models.
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                prefix = 'claude-example-' if route == 'claude' else 'example-model-'

                def mutate(entry, prefix=prefix):
                    entry['allowed_models'] = [entry['default_model']] + [
                        f'{prefix}{index}' for index in range(15)]
                self.assert_invalid(route, mutate)

    def test_different_single_model_is_invalid(self):
        # A one-item list that differs from the default, even only by case.
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                self.assert_invalid(route, lambda entry, route=route: entry.update(
                    allowed_models=[OTHER_MODEL[route]]))
        self.assert_invalid('agy', lambda entry: entry.update(
            allowed_models=[entry['default_model'].upper()]))
        self.assert_invalid('gw-agent', lambda entry: entry.update(
            allowed_models=[entry['default_model'] + '-b']))

    def test_default_model_changed_without_list_is_invalid(self):
        # Changing default_model alone leaves allowed_models stale: refused.
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                def mutate(entry, route=route):
                    entry['default_model'] = OTHER_MODEL[route]
                    if route == 'gw-agent':
                        entry['profile']['model'] = OTHER_MODEL[route]
                self.assert_invalid(route, mutate)

    def test_check_route_model_allows_only_default_after_load(self):
        config = load_route_config()
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                default = config['routes'][route]['default_model']
                self.assertEqual(check_route_model(config, route, default), default)
                with self.assertRaises(RouteError) as caught:
                    check_route_model(config, route, OTHER_MODEL[route])
                self.assertEqual(caught.exception.code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_missing_key_is_invalid(self):
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                self.assert_invalid(route, lambda entry: entry.pop('allowed_models'))

    def test_empty_list_is_invalid(self):
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                self.assert_invalid(route, lambda entry: entry.update(allowed_models=[]))

    def test_non_list_is_invalid(self):
        for route in ROUTE_NAMES:
            for value_kind in ('str', 'dict', 'null', 'int'):
                with self.subTest(route=route, value_kind=value_kind):
                    def mutate(entry, value_kind=value_kind):
                        model = entry['default_model']
                        entry['allowed_models'] = {
                            'str': model, 'dict': {model: True},
                            'null': None, 'int': 1}[value_kind]
                    self.assert_invalid(route, mutate)

    def test_duplicate_item_is_invalid(self):
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                self.assert_invalid(route, lambda entry: entry.update(
                    allowed_models=[entry['default_model'], entry['default_model']]))

    def test_non_str_item_is_invalid(self):
        for route in ROUTE_NAMES:
            for item in (7, None, True, ['nested'], {'k': 'v'}):
                with self.subTest(route=route, item=item):
                    self.assert_invalid(route, lambda entry, item=item: entry.update(
                        allowed_models=[entry['default_model'], item]))

    def test_badly_formatted_item_is_invalid(self):
        for route in ROUTE_NAMES:
            for item in BAD_FORMAT[route]:
                with self.subTest(route=route, item=item):
                    self.assert_invalid(route, lambda entry, item=item: entry.update(
                        allowed_models=[entry['default_model'], item]))

    def test_default_model_not_listed_is_invalid(self):
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                self.assert_invalid(route, lambda entry, route=route: entry.update(
                    allowed_models=[OTHER_MODEL[route]]))

    def test_gw_agent_profile_model_not_listed_is_invalid(self):
        def mutate(entry):
            entry['profile']['model'] = OTHER_MODEL['gw-agent']
            entry['allowed_models'] = [entry['default_model']]
        self.assert_invalid('gw-agent', mutate)

    def test_seventeen_items_are_invalid(self):
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                prefix = 'claude-example-' if route == 'claude' else 'example-model-'

                def mutate(entry, prefix=prefix):
                    entry['allowed_models'] = [entry['default_model']] + [
                        f'{prefix}{index}' for index in range(16)]
                self.assert_invalid(route, mutate)


class OpenCodeRouteTests(unittest.TestCase):
    """#1502 WP-E: the OpenCode route uses only the current free model."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'developer-routes.json'
        self.shipped = json.loads(ROUTES_FILE.read_text(encoding='utf-8'))

    def load(self, data):
        self.path.write_text(json.dumps(data), encoding='utf-8')
        return load_route_config(self.path)

    def assert_invalid(self, mutate):
        data = copy.deepcopy(self.shipped)
        mutate(data['routes']['opencode'])
        with self.assertRaises(RouteError) as caught:
            self.load(data)
        self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')

    def test_shipped_route_matches_adapter_model_and_variant(self):
        from llmtool import opencode_direct_developer

        entry = load_route_config()['routes']['opencode']
        self.assertEqual(entry['executable'], 'opencode')
        self.assertEqual(entry['default_model'], 'opencode/deepseek-v4.1-flash-free')
        self.assertEqual(entry['default_effort'], 'high')
        self.assertEqual(opencode_direct_developer.MODEL,
                         f"{entry['default_model']}#{entry['default_effort']}")
        self.assertTrue(opencode_direct_developer.is_free_model(entry['default_model']))

    def test_paid_or_variant_default_model_is_invalid(self):
        for model in ('opencode/example-paid', 'opencode/example-model-b-free#high',
                      'other/example-model-b-free', 'example-model-b-free'):
            with self.subTest(model=model):
                self.assert_invalid(lambda entry, model=model: entry.update(
                    default_model=model, allowed_models=[model]))

    def test_wrong_fields_are_invalid(self):
        mutations = {
            'executable': lambda entry: entry.update(executable='opencode-other'),
            'effort': lambda entry: entry.update(default_effort='max'),
            'effort type': lambda entry: entry.update(default_effort=3),
            'extra key': lambda entry: entry.update(denied_dirs=[]),
            'missing executable': lambda entry: entry.pop('executable'),
            'missing effort': lambda entry: entry.pop('default_effort'),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label):
                self.assert_invalid(mutate)

    def test_route_must_be_an_object(self):
        data = copy.deepcopy(self.shipped)
        data['routes']['opencode'] = 'opencode/deepseek-v4.1-flash-free'
        with self.assertRaises(RouteError) as caught:
            self.load(data)
        self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')

    def test_config_without_opencode_still_loads(self):
        data = copy.deepcopy(self.shipped)
        del data['routes']['opencode']
        self.assertNotIn('opencode', self.load(data)['routes'])


class RoleRegistryTests(unittest.TestCase):
    """#1502 WP-E: allowed models for codex-reviewer, claude-reviewer and AI Debug."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'developer-routes.json'
        self.shipped = json.loads(ROUTES_FILE.read_text(encoding='utf-8'))

    def load(self, data):
        self.path.write_text(json.dumps(data), encoding='utf-8')
        return load_route_config(self.path)

    def assert_invalid(self, mutate):
        data = copy.deepcopy(self.shipped)
        mutate(data)
        with self.assertRaises(RouteError) as caught:
            self.load(data)
        self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')

    def test_shipped_roles_hold_the_models_in_use(self):
        self.assertEqual(self.shipped['roles'], SHIPPED_ROLES)
        self.assertEqual(load_route_config()['roles'], SHIPPED_ROLES)

    def test_roles_are_cross_family_with_their_names(self):
        roles = load_route_config()['roles']
        for role, entry in roles.items():
            with self.subTest(role=role):
                is_claude_model = entry['allowed_models'][0].startswith('claude-')
                self.assertEqual(is_claude_model, role.startswith('claude-'))

    def test_config_without_roles_still_loads(self):
        data = copy.deepcopy(self.shipped)
        del data['roles']
        self.assertNotIn('roles', self.load(data))

    def test_loaded_roles_are_copies(self):
        config = load_route_config()
        config['roles']['codex-reviewer']['allowed_models'].append('example-model-b')
        self.assertEqual(load_route_config()['roles'], SHIPPED_ROLES)

    def test_invalid_role_registry_is_refused(self):
        def set_models(role, models):
            return lambda data: data['roles'][role].update(allowed_models=models)

        mutations = {
            'not an object': lambda data: data.update(roles=['codex-reviewer']),
            'empty': lambda data: data.update(roles={}),
            'missing role': lambda data: data['roles'].pop('claude-debug'),
            'unknown role': lambda data: data['roles'].update(
                {'gw-agent-reviewer': {'allowed_models': ['example-model-b']}}),
            'entry not an object': lambda data: data['roles'].update(
                {'codex-debug': ['gpt-6.1-sol']}),
            'extra entry key': lambda data: data['roles']['codex-debug'].update(
                default_model='gpt-6.1-sol'),
            'missing allowed_models': lambda data: data['roles']['codex-debug'].pop(
                'allowed_models'),
            'empty list': set_models('codex-reviewer', []),
            'non-list': set_models('codex-reviewer', 'gpt-6.1-sol'),
            'two models': set_models('codex-reviewer', ['gpt-6.1-sol', 'example-model-b']),
            'duplicate': set_models('codex-reviewer', ['gpt-6.1-sol', 'gpt-6.1-sol']),
            'non-str': set_models('codex-debug', [7]),
            'bad format': set_models('codex-debug', ['has space']),
            'url': set_models('codex-debug', ['https://example.invalid/m']),
            'codex role with claude model': set_models('codex-reviewer', ['claude-opus-5-5']),
            'codex role with claude model upper': set_models('codex-debug', ['Claude-opus-5-5']),
            'claude role with codex model': set_models('claude-reviewer', ['gpt-6.1-sol']),
            'claude role bad format': set_models('claude-debug', ['claude-UPPER']),
            'unknown top-level key': lambda data: data.update(models={}),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label):
                self.assert_invalid(mutate)


class RoleModelFamilyAllowlistTests(unittest.TestCase):
    """Security repair (PR #1782 CWE-693): role families are an allowlist.

    The check runs on the full identifier: provider prefixes are never
    stripped, so prefixed and mixed-case forms of another family (and of the
    role's own family) are refused.
    """

    CODEX_ROLES = ('codex-reviewer', 'codex-debug')
    CLAUDE_ROLES = ('claude-reviewer', 'claude-debug')
    # Claude-family identifiers in prefixed, dotted and mixed-case forms.
    CLAUDE_FORMS = (
        'claude-opus-5-5', 'claude-example-b', 'Claude-opus-5-5', 'CLAUDE-OPUS-5-5',
        'cLaUdE-opus-5-5', 'anthropic/claude-opus-5-5', 'Anthropic/Claude-Opus-5-5',
        'ANTHROPIC/CLAUDE-OPUS-5-5', 'bedrock/anthropic.claude-opus-5-5',
        'us.anthropic.claude-opus-5-5', 'vertex_ai/claude-opus-5-5',
        'openrouter:claude-opus-5-5', 'openai/claude-opus-5-5', 'x/y/claude-opus-5-5',
        'gpt-claude-opus-5-5', 'codex-claude-b', 'gpt-6.1-anthropic',
    )
    # Codex/GPT-family identifiers in prefixed, dotted and mixed-case forms.
    CODEX_FORMS = (
        'gpt-6.1-sol', 'codex-example-b', 'GPT-6.1-sol', 'Gpt-6.1-Sol', 'CODEX-example-b',
        'openai/gpt-6.1-sol', 'OpenAI/GPT-6.1-SOL', 'azure/gpt-6.1-sol',
        'openrouter:gpt-6.1-sol', 'anthropic/gpt-6.1-sol', 'claude-gpt-6',
        'claude-codex-b', 'claude-openai-b',
    )
    # Neither family: refused for every role.
    OTHER_FORMS = ('gemini-3.8-flash-medium', 'example-model-b',
                   'opencode/deepseek-v4.1-flash-free', 'gw-agent-default',
                   'gpt-', 'codex-', 'claude-', 'gpt', 'gpt_6', 'gpt-6 sol')
    # Prefixed or mixed-case forms of a role's own family are also refused
    # (the prefix is part of the full identifier).
    CODEX_OWN_FAMILY_REFUSED = ('GPT-6.1-sol', 'Gpt-6.1-Sol', 'CODEX-example-b',
                                'openai/gpt-6.1-sol', 'OpenAI/GPT-6.1-SOL',
                                'azure/gpt-6.1-sol', 'openrouter:gpt-6.1-sol')
    CLAUDE_OWN_FAMILY_REFUSED = ('Claude-opus-5-5', 'CLAUDE-OPUS-5-5', 'cLaUdE-opus-5-5',
                                 'anthropic/claude-opus-5-5',
                                 'Anthropic/Claude-Opus-5-5',
                                 'us.anthropic.claude-opus-5-5',
                                 'vertex_ai/claude-opus-5-5')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'developer-routes.json'
        self.shipped = json.loads(ROUTES_FILE.read_text(encoding='utf-8'))

    def load_role(self, role, model):
        data = copy.deepcopy(self.shipped)
        data['roles'][role] = {'allowed_models': [model]}
        self.path.write_text(json.dumps(data), encoding='utf-8')
        return load_route_config(self.path)

    def assert_refused(self, role, model):
        with self.assertRaises(RouteError) as caught:
            self.load_role(role, model)
        self.assertEqual(caught.exception.code, 'ROUTE_CONFIG_INVALID')

    def test_codex_roles_accept_only_lowercase_unprefixed_codex_family(self):
        for role in self.CODEX_ROLES:
            for model in ('gpt-6.1-sol', 'codex-example-b', 'gpt-example-b'):
                with self.subTest(role=role, model=model):
                    config = self.load_role(role, model)
                    self.assertEqual(config['roles'][role], {'allowed_models': [model]})

    def test_claude_roles_accept_only_lowercase_unprefixed_claude_family(self):
        for role in self.CLAUDE_ROLES:
            for model in ('claude-opus-5-5', 'claude-example-b'):
                with self.subTest(role=role, model=model):
                    config = self.load_role(role, model)
                    self.assertEqual(config['roles'][role], {'allowed_models': [model]})

    def test_claude_family_in_any_form_is_refused_for_codex_roles(self):
        for role in self.CODEX_ROLES:
            for model in self.CLAUDE_FORMS:
                with self.subTest(role=role, model=model):
                    self.assert_refused(role, model)

    def test_codex_family_in_any_form_is_refused_for_claude_roles(self):
        for role in self.CLAUDE_ROLES:
            for model in self.CODEX_FORMS:
                with self.subTest(role=role, model=model):
                    self.assert_refused(role, model)

    def test_other_families_are_refused_for_every_role(self):
        # The former denylist accepted any well-formed non-'claude' prefix.
        for role in self.CODEX_ROLES + self.CLAUDE_ROLES:
            for model in self.OTHER_FORMS:
                with self.subTest(role=role, model=model):
                    self.assert_refused(role, model)

    def test_prefixed_or_mixed_case_own_family_is_refused(self):
        for role in self.CODEX_ROLES:
            for model in self.CODEX_OWN_FAMILY_REFUSED:
                with self.subTest(role=role, model=model):
                    self.assert_refused(role, model)
        for role in self.CLAUDE_ROLES:
            for model in self.CLAUDE_OWN_FAMILY_REFUSED:
                with self.subTest(role=role, model=model):
                    self.assert_refused(role, model)

    def test_role_checks_are_family_allowlists(self):
        checks = developer_routes._ROLE_MODEL_CHECKS
        self.assertEqual(set(checks), set(self.CODEX_ROLES + self.CLAUDE_ROLES))
        for role in self.CODEX_ROLES:
            with self.subTest(role=role):
                self.assertTrue(checks[role]('gpt-6.1-sol'))
                for model in self.CLAUDE_FORMS + self.OTHER_FORMS:
                    self.assertFalse(checks[role](model), model)
        for role in self.CLAUDE_ROLES:
            with self.subTest(role=role):
                self.assertTrue(checks[role]('claude-opus-5-5'))
                for model in self.CODEX_FORMS + self.OTHER_FORMS:
                    self.assertFalse(checks[role](model), model)
        for value in (None, 7, True, ['gpt-6.1-sol'], {'claude-opus-5-5': 1}):
            for role, check in checks.items():
                with self.subTest(role=role, value=value):
                    self.assertFalse(check(value))


class CheckRouteModelTests(unittest.TestCase):
    def setUp(self):
        self.config = load_route_config()

    def assert_refused(self, config, route, model):
        with self.assertRaises(RouteError) as caught:
            check_route_model(config, route, model)
        self.assertEqual(caught.exception.code, 'ROLE_MODEL_NOT_ALLOWED')

    def test_exported(self):
        self.assertIn('check_route_model', developer_routes.__all__)

    def test_listed_model_is_returned(self):
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                model = self.config['routes'][route]['default_model']
                self.assertEqual(check_route_model(self.config, route, model), model)

    def test_unlisted_model_is_refused(self):
        for route in ROUTE_NAMES:
            with self.subTest(route=route):
                self.assert_refused(self.config, route, OTHER_MODEL[route])
        self.assert_refused(self.config, 'agy', 'claude-opus-5-5')
        self.assert_refused(self.config, 'claude', 'gemini-3.8-flash-medium')
        self.assert_refused(self.config, 'claude', 'CLAUDE-OPUS-5-5')
        self.assert_refused(self.config, 'claude', '')

    def test_non_str_model_is_refused(self):
        for model in (None, 7, True, ['claude-opus-5-5'], {'claude-opus-5-5': 1}):
            with self.subTest(model=model):
                self.assert_refused(self.config, 'claude', model)

    def test_absent_route_is_refused(self):
        for route in ('opencode', 'codex', '', None, 7):
            with self.subTest(route=route):
                self.assert_refused(self.config, route, 'claude-opus-5-5')
        without_claude = copy.deepcopy(self.config)
        del without_claude['routes']['claude']
        self.assert_refused(without_claude, 'claude', 'claude-opus-5-5')


if __name__ == '__main__':
    unittest.main()
